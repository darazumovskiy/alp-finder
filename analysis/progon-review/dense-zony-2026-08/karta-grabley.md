# Карта граблей vast.ai и надёжный рецепт прогона

Синтез шести рисерч-отчётов (research/*.md, 20.08.2026). Правило чтения:
каждая строка = отказ, который СЛУЧИТСЯ, и готовая защита.

## Наши три фейла — точные причины (пост-фактум)

1. **CUDA 804 на хосте №1**: образ несёт CUDA 12.9 + compat-библиотеки;
   на драйвере 560 контейнер включает forward compatibility, которую
   GeForce не поддерживает. Отсекается ФИЛЬТРОМ ПОИСКА `cuda_vers>=12.9`
   (эквивалент «нативный драйвер ≥575»); страховка для любых драйверов —
   нейтрализация compat в onstart: `export LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH`.
2. **SSH denied на хостах №2–3**: задокументированный паттерн маркетплейса —
   агент хоста молча не доносит ключ в контейнер. «5 попыток до рабочего
   хоста» — норма сервиса. Обход, не зависящий от агента: класть ключ
   СВОИМ кодом через `--onstart-cmd` (+ маркер ONSTART_OK, проверяемый
   через `vastai logs` БЕЗ ssh). Битый хост не чинить — destroy сразу.
3. **~$2 потерь** — «налог на перебор хостов», статистическая норма
   платформы; рефандов по ToS нет, но чат поддержки нередко возвращает
   кредиты добровольно — попросить, приложив instance id 48162847/48164027.

## Отказы по стадиям → защита

| Стадия | Отказ | Защита |
|---|---|---|
| Поиск | ТТХ вранья: сеть/CPU/диск хуже заявленных | verified + reliability>0.98; `cpu_cores_effective` (не cpu_cores!) ≥ 32 — fusion CPU-этап; смоук диска/CPU после входа |
| Поиск | несовместимый драйвер | `cuda_vers>=12.9` прямо в запросе |
| Поиск | дорогой диск/трафик у дешёвого оффера | эффективная цена = dph + storage×120ГБ + traffic×15ГБ |
| Поиск | max duration меньше прогона | duration ≥ 1 день |
| Поиск | приватность: владелец community-хоста имеет root и видит данные | РЕШЕНИЕ ОПЕРАТОРА: принять риск или фильтр datacenter (дороже) |
| Создание | оффер увели (гонка) | брать топ-3 офферов, ретрай по списку |
| Создание | spend_rate_limit аккаунта | у нас уже прогрет ($2 потрачено, 4×4090 создавались) — снят |
| Загрузка | pull >20 мин на медленном хосте | GPU-время loading бесплатно, но таймаут 15 мин → destroy → следующий |
| Загрузка | статус exited/unknown/offline навсегда | poll с таймаутом и веткой ошибок, НЕ вечный цикл |
| Доступ | ключ не доставлен агентом | ключ кладёт наш onstart; проверка ONSTART_OK через `vastai logs` без ssh; denied >5 мин при ONSTART_OK → destroy |
| Доступ | «Connection refused» первые 30–60 с | ретраи 3 мин — это норма инициализации |
| Доступ | порт из show instances неверный | только `vastai ssh-url` |
| GPU | карта не считает при верных версиях (NVML mismatch, no devices) | смоук РЕАЛЬНЫМ CUDA-ядром: `colmap feature_extractor` на 2 кадрах → GPU_OK; хостовая проблема → report + destroy, не чинить |
| Данные | proxy-ssh душится >1 ГБ | rsync только по direct-порту (`--direct`, direct_port_count>=1) |
| Данные | `vastai copy` local↔instance сломан (issue #326) | не полагаться; основной канал rsync/direct, запасной — Jupyter/cloud sync |
| Счёт | контейнер «running», а задача не идёт | ENTRYPOINT в ssh-режиме подменяется — запуск только руками/onstart; через 2–3 мин проверить рост лога и nvidia-smi ≥80% |
| Счёт | хост ушёл в offline посреди счёта, без рефанда | промежуточный rsync готовых fused.ply каждые 20–30 мин |
| Счёт | диск кончился — не расширяется никогда | 120 ГБ ≈ ×2.4 запас + чистка карт после fusion (уже в скрипте) |
| Гашение | stop ≠ выключение: диск капает вечно, GPU уводят | НИКОГДА не stop; только `destroy -y` (без -y CLI виснет) |
| Гашение | забытый инстанс | локальный таймер destroy + самостоп изнутри (`$CONTAINER_API_KEY`, `timeout 4h`) |
| Финал | результаты погибли с destroy | destroy только после exit-code rsync + локального чтения файлов |
| Деньги | перерасход незаметен | баланс = ровно бюджет (естественный предохранитель); `vastai show invoices-v1 --charges` при каждом контроле |

## Надёжный рецепт (собран из официального SKILL vast-cli + 4 опенсорсных оркестраторов)

```bash
# 1. Поиск: полный фильтр
vastai search offers 'num_gpus=4 gpu_name=RTX_4090 verified=true
  reliability>0.98 cuda_vers>=12.9 disk_space>=120 direct_port_count>=1
  inet_down>=200 inet_up>=200 duration>=1 rentable=true' -o 'dph+' --raw
# выбрать по эффективной цене; взять ТОП-3 как очередь ретраев

# 2. Создание: ключ кладём сами, compat нейтрализуем, env экспортируем
vastai create instance <OFFER> --image colmap/colmap:latest --disk 120 --ssh --direct \
  --onstart-cmd 'env >> /etc/environment; mkdir -p /root/.ssh && chmod 700 /root/.ssh && echo "<PUB>" >> /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys; echo "export LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH" >> /etc/profile.d/cudafix.sh; echo ONSTART_OK'

# 3. Poll: таймаут 15 мин; exited/unknown/offline → destroy -y → следующий оффер
# 4. vastai logs <ID> → ждать ONSTART_OK (проверка ключа БЕЗ ssh)
# 5. ssh по `vastai ssh-url`, ретраи 3 мин; denied >5 мин → destroy -y → следующий
# 6. Смоук (≤3 мин): nvidia-smi; nproc; ldconfig -p | grep libcuda (не compat!);
#    colmap feature_extractor на 2 кадрах → GPU_OK; dd диск
# 7. rsync данных по direct; тест-зона; рендер облака → ПОКАЗ ОПЕРАТОРУ
# 8. полная очередь (nohup >> лог); каждые 20–30 мин: логи + rsync готовых + charges
# 9. локальный таймер destroy через 5 ч + самостоп изнутри инстанса
# 10. финал: rsync всех → локальная проверка → destroy -y → show instances пуст
```

## Открытые решения оператора

1. **Приватность**: community-хост = владелец физически может видеть кадры
   (root над докер-хостом; признано самим vast). Либо принять, либо фильтр
   datacenter/Secure Cloud (дороже, офферов меньше).
2. **Компенсация**: попросить в чате поддержки кредиты за два хоста
   с битым SSH (instance 48162847, 48164027) — по отзывам возвращают.
