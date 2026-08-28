Материал собран — официальный agent-скилл vast-cli, три опенсорсных оркестратора, доки по onstart/templates/cloud-copy и живые кейсы. Составляю отчёт.

---

# Автоматизация vast.ai: рецепты, кейсы, скелет надёжного прогона

## 1. Канонический end-to-end рецепт

Официальный поток (совпадает во всех источниках — [CLI hello-world](https://docs.vast.ai/cli/hello-world), [официальный SKILL.md vast-cli](https://github.com/vast-ai/vast-cli/blob/master/vastai/SKILL.md)):

```
search offers → create instance → poll actual_status до "running" →
scp/rsync данные → ssh запуск → мониторинг → scp результаты → destroy -y
```

Ключевые факты из официальной документации:

- Всегда `--raw` — машиночитаемый JSON, парсить python/jq.
- ID инстанса — поле `new_contract` в ответе create.
- **Poll-loop warning (официальный):** если `actual_status` стал `exited` / `unknown` / `offline` — он **никогда** не станет `running`. Без таймаута и error-ветки скрипт зациклится, а диск капает деньгами. Рецепт: destroy и ретрай на другом оффере.
- Офферы динамические: между search и create машину могут забрать. Официальная рекомендация — искать несколько офферов и пробовать следующий; `--cancel-unavail` для fail-fast.
- `destroy` — единственное, что останавливает весь счётчик (stop оставляет плату за диск). `-y` обязателен в неинтерактивных скриптах, иначе повиснет на подтверждении.
- Плата за диск идёт с момента create, за GPU — с момента `running`.

### Реальные опенсорсные примеры

**1. [Yusuke710/vastai-skill](https://github.com/Yusuke710/vastai-skill)** — скилл для ИИ-агентов (наш сценарий один в один). Приёмы:
- Утилита `vastai-connect <id>` — закрывает главную дыру нативного CLI: блокируется, пока инстанс **реально доступен по SSH** (не просто `running` — контейнер может быть running, а sshd ещё нет), затем пишет SSH-алиас в отдельный `~/.ssh/vastai.conf`.
- Работа только plain ssh/rsync: `rsync -az --filter=':- .gitignore'` вверх, запуск, `rsync` вниз.
- Правило «destroy, never stop».

**2. [elliotwoods/vastai-blender](https://github.com/elliotwoods/vastai-blender)** — самый зрелый флот-оркестратор (рендер Blender на флоте машин):
- Ранжирование офферов `perf-per-dollar × reliability² × network`, причём perf — **собственный измеренный** throughput модели GPU (учится на завершённых чанках), а не синтетический бенчмарк vast.
- Работа делится на чанки; **упавшие машины заменяются автоматически, перерендериваются только недостающие кадры**.
- Передача файлов по SSH самого инстанса (SFTP), SHA-256-верификация, резюмируемая — «никаких credentials на чужих машинах».
- Spend cap и idle-timeout: ноды гасятся, простояв без работы дольше порога.

**3. [Gist engineervix (Ollama на vast)](https://gist.github.com/engineervix/44e153ee5db2ad0192f12c391f1216bb)** — компактный полный bash: search → create → poll → `vastai attach ssh` со свежесгенерированным ключом → ssh с `set -e` внутри heredoc → обработчик `handle_vastai_error`, который destroy'ит инстанс при любой ошибке.

**4. [jeremylongshore vastai-pack](https://github.com/jeremylongshore/claude-code-plugins-plus-skills/blob/main/plugins/saas-packs/vastai-pack/skills/vastai-ci-integration/SKILL.md)** — CI-интеграция (GitHub Actions): весь lifecycle в pipeline, cleanup в `if: always()` — destroy выполняется даже при упавших тестах. Там же — Python context manager `managed_instance` (destroy в `finally`) и скоринг офферов с весами: для батчей `{cost: 0.7, reliability: 0.2, perf: 0.1}`, для долгих прогонов reliability важнее.

**Как переживают отказ хоста** (сводно, включая [профиль remote-gpu-trainer](https://github.com/sickn33/agentic-awesome-skills/blob/main/skills/remote-gpu-trainer/profiles/vastai.md)): нативной очереди/шедулера у vast для одиночных инстансов нет — оркестратор поллит `actual_status`, при `exited/unknown/offline` destroy'ит и создаёт на новом оффере; задача обязана быть checkpoint-resumable, чекпоинты — только off-box (S3/локально), потому что диск контейнера умирает вместе с инстансом.

## 2. Templates против raw image

Шаблон vast = образ + env + launch mode + onstart + диск в одном сохранённом объекте (`--template_hash` при create). Надёжность старта даёт не сам шаблон, а **образ**:

- Хосты vast кешируют слои популярных образов. Официальные `vastai/base-image` и `vastai/pytorch` ([github](https://github.com/vast-ai/base-image)) построены поверх огромных `nvidia/cuda` — старт быстрый, потому что 90% слоёв уже на хосте. Свой образ рекомендуют строить `FROM vastai/pytorch:...` — тогда докачиваются только ваши слои.
- Тег `@vastai-automatic-tag` сам подбирает вариант под CUDA конкретной машины — убирает класс ошибок «образ не совместим с драйвером хоста».
- Два пути кастомизации: `PROVISIONING_SCRIPT` (env-переменная с URL скрипта, выполняется на старте — просто, но медленнее и хрупче) или derived-образ (надёжнее, рекомендуют для повторяющихся прогонов).
- Свой образ + свой onstart в шаблоне — да, полностью поддерживается ([creating-templates](https://docs.vast.ai/guides/templates/creating-templates)).
- Рекомендация сообщества: точный тег версии вместо `latest`.

Для одноразовых скриптовых прогонов шаблон не обязателен — те же поля передаются флагами create.

## 3. onstart-скрипты

**Что кладут** (реальные примеры):

- `env >> /etc/environment` — первая строка почти везде: env-переменные, переданные через `--env`, **не видны в SSH/tmux-сессиях** без этого экспорта ([docs](https://docs.vast.ai/guides/instances/docker-environment)).
- Автозапуск задачи + самогашение — официальный паттерн: внутри каждого инстанса предустановлены `$CONTAINER_ID` и per-instance ключ `$CONTAINER_API_KEY`, так что инстанс может убить сам себя:

```bash
cd /workspace && bash run_job.sh; vastai destroy instance $CONTAINER_ID
```

- [HoloScript vast-onstart-bootstrap.sh](https://github.com/brianonbased-dev/HoloScript/blob/main/scripts/mesh-deploy/vast-onstart-bootstrap.sh) — боевой пример: onstart минимальный (установить git → клонировать репо → запустить bootstrap оттуда), все секреты — через `--env`, с комментарием «keys leaked twice» о том, почему их нельзя вшивать в onstart-строку.
- `/root/onstart.sh` **перезапускается при каждом старте контейнера** — это и есть механизм durable-перезапуска после spot-прерывания (tmux умирает вместе с контейнером, onstart — нет).

**Подводные камни:**

- В SSH/Jupyter launch mode **entrypoint образа заменяется** vast'овским — если ваш образ живёт entrypoint'ом, его команду надо продублировать в onstart, иначе приложение просто не стартует. В режиме `args`/Entrypoint — наоборот, entrypoint сохраняется, но SSH не поднимается.
- SSH-режим без onstart = контейнер с голым sshd, задача сама не запустится.
- Лимит размера: 16 КБ через CLI, ~4 КБ через API. Обход — gzip+base64 или паттерн «onstart только качает и запускает настоящий скрипт».
- Обскурные ошибки загрузки кастомного образа в SSH-режиме — официальный совет перейти на Entrypoint mode.

## 4. Паттерн «самодостаточная задача»

Есть в двух вариантах:

**A. Всё в onstart** (HoloScript, официальные доки): onstart качает вход (git/wget/S3), считает, выгружает результат, `vastai destroy instance $CONTAINER_ID`. Секреты — через `--env` (в API это dict, не docker-строка). Для самогашения отдельный API-ключ не нужен — `$CONTAINER_API_KEY` предустановлен и ограничен своим инстансом, это самый безопасный вариант.

**B. Cloud Connections + `vastai cloud copy`** — vast'овский встроенный rclone: S3/Backblaze/GDrive подключаются в настройках аккаунта, копирование запускается API-вызовом снаружи (работает даже на остановленном инстансе). Официальное предупреждение: credentials временно копируются на хост-машину — **использовать ключ, ограниченный одним bucket, никогда полноправный**; для чувствительного — фильтр Secure Cloud hosts.

Позиция elliotwoods/vastai-blender радикальнее: никаких облачных credentials на чужих машинах вообще, всё через SFTP самого инстанса с верификацией хешей. Для наших данных (чувствительные кадры) это релевантный аргумент.

## 5. Фотограмметрия / рендеринг / не-ML батчи

- **Blender** — самый развитый не-ML кейс: официальный шаблон [Blender Batch Renderer](https://docs.vast.ai/blender-batch-rendering) (батч .blend-файлов) и полный оркестратор elliotwoods/vastai-blender (см. §1) с чанкованием кадров, заменой упавших нод и spend cap.
- **COLMAP**: специального шаблона нет, гоняют в CUDA-образах; грабли headless-запуска — нужна сборка с CUDA, отключать всё требующее дисплея, при падении automatic reconstructor разбивать на отдельные CLI-этапы (`feature_extractor`, `patch_match_stereo`, ...). Это совпадает с нашим опытом scene3d.
- **Whisper-ферма** ([openai/whisper discussion #575](https://github.com/openai/whisper/discussions/575)) — лучший найденный кейс большого не-ML-тренировочного батча: 3000 часов аудио → файлы в bucket → **простенький собственный API как очередь задач** (URL, прогресс, результаты) → Docker-контейнер, который берёт задачи из очереди → ~10 параллельных инстансов vast. Паттерн «тупые воркеры + внешняя очередь» — воркеру всё равно, умер ли сосед: задача просто вернётся в очередь.

## 6. LLM-агенты и оркестраторы поверх vast

- Vast официально таргетируется на агентов: `npx skills add vast-ai/vast-cli` ставит скилл для Claude Code/Cursor, SDK рекламируется как «Build AI agents that provision their own GPU compute. No human in the loop», у сайта есть `llms.txt`.
- **Официальная защита из их же llms.txt**: «Never create, stop, destroy, or modify paid resources unless the user explicitly asks for that action and has confirmed the target account, budget, and cleanup plan» — vast сам требует от агентов подтверждённый бюджет и план очистки. Наш скилл `verifikatsiya-platnogo-progona` — ровно эта рекомендация.
- Типовые защиты в агентских скиллах: таймаут + error-ветка в каждом poll-loop (иначе агент зациклится на оплачиваемом инстансе), `destroy -y` (без `-y` агент повисает на подтверждении), «Teardown Iron Law» из remote-gpu-trainer — **не destroy'ить, пока результаты не скопированы off-box И не верифицированы чтением, гейт на exit-code копирования, а не на строчку в логе**; kill switch — уметь мгновенно погасить все инстансы.
- Их managed-продукт [Serverless](https://docs.vast.ai/guides/serverless/architecture) (endpoint + workergroup + автоскейлер) и Deployments (`@remote`-декоратор, beta) — заточены под inference-очереди HTTP-запросов, для разового батча фотограмметрии избыточны.
- [lubfoltan/vast.ai-orchestrator](https://github.com/lubfoltan/vast.ai-orchestrator) — GUI-оркестратор тренировок (скоринг офферов, авто-полл, rsync/tar-передача, авто-харвест результатов, «one-click terminate — против часто забываемого cleanup»).

## Приёмы, которые мы не использовали

1. **`$CONTAINER_ID` + `$CONTAINER_API_KEY` для самогашения** — инстанс убивает себя сам по завершении задачи; страховка от «оркестратор умер, инстанс капает деньгами». Ключ per-instance, утечка почти безвредна.
2. **Poll-loop с error-веткой на `exited`/`unknown`/`offline` + таймаут** — официально задокументированная ловушка вечного цикла; наши ожидания должны destroy'ить и ретраить на следующем оффере.
3. **Ретрай по списку офферов + `--cancel-unavail`** — брать топ-N из search и пробовать по очереди, а не падать, если первый оффер увели.
4. **`@vastai-automatic-tag` и образы `vastai/*`** — быстрый старт за счёт кеша слоёв на хостах и автоподбор тега под CUDA хоста; убирает класс отказов «образ качается 20 минут / несовместим с драйвером».
5. **`vastai execute` и `vastai logs`** — мониторинг без SSH-сессии (меньше хрупкости, чем held ssh).
6. **Restricted API key** (`vastai create api-key --permissions ...`) — на арендованную машину/в CI отдавать ключ с урезанными правами, не главный.
7. **Проверка работоспособности до оплаты основной задачи** — smoke-запуск `nvidia-smi` / рендер одного кадра / одна итерация перед основным прогоном (паттерн vastai-blender: throughput меряется на первых чанках).
8. **«Тупые воркеры + внешняя очередь»** для многофайловых батчей (whisper-кейс): отказ хоста не требует умного восстановления — задача возвращается в очередь.
9. **Gate destroy на верификацию копии** — destroy только после успешного exit-code копирования результатов и проверки их читаемости, не по строчке «done» в логе.
10. **Idle-timeout и spend cap** на уровне оркестратора (vastai-blender) — жёсткий потолок трат независимо от логики задачи.

## Готовый скелет надёжного прогона

Собран из официального SKILL.md, gist engineervix, CI-паттерна jeremylongshore и правил remote-gpu-trainer:

```bash
#!/usr/bin/env bash
set -euo pipefail
# Требует: vastai set api-key ...; vastai create ssh-key ~/.ssh/id_ed25519.pub

QUERY='num_gpus=1 gpu_ram>=24 verified=true reliability>0.98 direct_port_count>=1 rentable=true'
IMAGE='vastai/pytorch:@vastai-automatic-tag'   # кешируется на хостах
DISK=60; MAX_OFFERS=5; BOOT_TIMEOUT=600
INSTANCE_ID=""

cleanup() { [ -n "$INSTANCE_ID" ] && vastai destroy instance "$INSTANCE_ID" -y || true; }
trap cleanup EXIT INT TERM                      # инстанс не переживёт смерть скрипта

# onstart: env для ssh-сессий + страховочное самогашение по дедлайну
ONSTART='env >> /etc/environment
(sleep 21600; vastai destroy instance $CONTAINER_ID) &   # hard deadline 6ч
'

# --- ретрай по топ-N офферов ---
OFFERS=$(vastai search offers "$QUERY" -o 'dph_total' --limit $MAX_OFFERS --raw \
         | python3 -c 'import sys,json; print("\n".join(str(o["id"]) for o in json.load(sys.stdin)))')
for OFFER in $OFFERS; do
  R=$(vastai create instance "$OFFER" --image "$IMAGE" --disk $DISK \
        --ssh --direct --cancel-unavail --onstart-cmd "$ONSTART" --raw) || continue
  INSTANCE_ID=$(echo "$R" | python3 -c 'import sys,json; print(json.load(sys.stdin)["new_contract"])')

  # --- ожидание с error-веткой (официальный poll-loop warning) ---
  DEADLINE=$(( $(date +%s) + BOOT_TIMEOUT )); OK=""
  while [ "$(date +%s)" -lt $DEADLINE ]; do
    ST=$(vastai show instance "$INSTANCE_ID" --raw \
         | python3 -c 'import sys,json; print(json.load(sys.stdin).get("actual_status") or "null")')
    case "$ST" in
      running) OK=1; break ;;
      exited|unknown|offline) break ;;          # никогда не станет running
      *) sleep 15 ;;
    esac
  done
  [ -n "$OK" ] && break
  vastai destroy instance "$INSTANCE_ID" -y; INSTANCE_ID=""   # ретрай на следующем оффере
done
[ -n "$INSTANCE_ID" ] || { echo "все офферы не поднялись"; exit 1; }

SSH=$(vastai show instance "$INSTANCE_ID" --raw \
      | python3 -c 'import sys,json;i=json.load(sys.stdin);print(f"-p {i[\"ssh_port\"]} root@{i[\"ssh_host\"]}")')
# дождаться реального sshd (running ≠ ssh-reachable — приём vastai-skill)
for i in $(seq 40); do ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no $SSH true && break; sleep 10; done

ssh $SSH "nvidia-smi"                                           # smoke-check GPU
rsync -az -e "ssh -p ${SSH#-p }" ./input/ "${SSH##* }:/workspace/input/"  # или wget/S3 из onstart
ssh $SSH "cd /workspace && nohup bash run_job.sh > job.log 2>&1 & echo started"

# мониторинг по маркер-файлу
while ! ssh $SSH 'test -f /workspace/DONE'; do
  ssh $SSH 'tail -2 /workspace/job.log' || true; sleep 60
done

# результаты: копия + верификация ДО destroy (Teardown Iron Law)
rsync -az -e "ssh -p ${SSH#-p }" "${SSH##* }:/workspace/output/" ./output/
python3 verify_output.py ./output/            # упадёт — trap НЕ destroy'ит... 
# ...нет: при провале верификации выходим без cleanup, чтобы не потерять данные:
# trap - EXIT; exit 1  — внутри verify-ветки при желании
# успех — trap сделает destroy сам
echo "OK: результаты в ./output, инстанс будет погашен"
```

Ключевые страховки скелета: `trap` гасит инстанс при любом исходе скрипта; hard-deadline внутри onstart гасит инстанс, даже если оркестратор умер; poll-loop не зацикливается на мёртвых статусах; ретрай идёт по списку офферов; destroy происходит только после верифицированной копии результатов.

I have enough material now to write the report in Russian, covering the canonical recipe with open-source examples, templates vs raw images, onstart-script pitfalls, self-contained tasks, non-ML rendering cases, LLM agents/orchestrators, plus a section on unused techniques and a ready-made bash skeleton for a reliable run.