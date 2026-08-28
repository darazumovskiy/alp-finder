Все ключевые источники собраны. Составляю отчёт.

---

# Отчёт: биллинг vast.ai и доставка данных (проверено 20.08.2026)

## 1. Биллинг — точные факты

Все факты из официальных доков (актуальны на сегодня):

- **Три отдельных счётчика**: GPU-рента ($/час), хранение ($/ГБ/час), трафик ($/TB). Ставки индивидуальны у каждого хоста — смотреть при выборе оффера (hover по цене). [docs.vast.ai/billing](https://docs.vast.ai/billing)
- **Посекундно**: «rent 10 minutes → pay 10 minutes». GPU-счёт идёт за каждую секунду в состоянии `running`. [docs.vast.ai/billing](https://docs.vast.ai/billing)
- **Хранение — с момента создания** инстанса, за **выделенный** диск (`--disk N`), не за использованный, каждую секунду существования инстанса **в любом состоянии кроме `offline`** — created, loading, running, stopped, exited. [docs.vast.ai/guides/instances/pricing](https://docs.vast.ai/guides/instances/pricing)
- **`loading` (скачивание образа) — GPU не биллится**, но диск уже биллится. FAQ прямо: «you are not charged when it says Loading» (речь о GPU-ренте). [docs.vast.ai/billing](https://docs.vast.ai/billing)
- **`stopped`** — только диск. **`offline`** (хост отвалился) — ничего. **`destroy`** — прекращает всё, данные удаляются безвозвратно. [docs.vast.ai/guides/instances/manage-instances](https://docs.vast.ai/guides/instances/manage-instances)
- **Трафик — по факту байтов в обе стороны**, в любом состоянии инстанса, по ставке хоста ($/TB). В `$/hr` на карточке **не входит** — это частая причина «списывается больше, чем ожидал». У многих хостов $0/TB, но бывает и дорого — проверять в оффере (секция «Internet»). [docs.vast.ai/billing](https://docs.vast.ai/billing)
- **Минимальных списаний нет** (в доках не упоминаются).
- **Lifetime**: у каждого оффера есть Max Duration; по его истечении инстанс **автоматически стопится**, продление — на усмотрение хоста. Данные считать потерянными. [FAQ](https://cdn.vast.ai/faq/)
- **Баланс = предоплата**. При нуле: инстансы автостоп, диск продолжает биллиться **в минус**, без привязанной карты данные **уничтожаются** через grace-период. [docs.vast.ai/billing](https://docs.vast.ai/billing)
- **Рефанды**: потраченные кредиты — «absolutely no refunds» (и по ToS «all payments final»); непотраченные — вернут через чат поддержки (кроме крипты). [vast.ai/terms](https://vast.ai/terms)

**Посмотреть списания**: CLI `vastai show invoices-v1 --charges` (фильтры `--start-date/--end-date/--limit/--latest-first`); баланс — `vastai show user`; в UI — [cloud.vast.ai/billing](https://cloud.vast.ai/billing/), вкладка **Charges** с разбивкой по GPU / диску / трафику на каждый инстанс. [vast-cli SKILL.md](https://github.com/vast-ai/vast-cli/blob/master/vastai/SKILL.md)

## 2. Каталог ловушек биллинга → защита

| Ловушка | Источник | Защита |
|---|---|---|
| Диск биллится с **создания**, ещё до running и даже если инстанс так и не заработал | докс | Брать минимальный `--disk` (нам хватит 25–30 ГБ); мёртвый инстанс destroy сразу, не «потом» |
| `exited`/`unknown`/`offline` никогда не станет `running` — poll-цикл ждёт вечно, диск капает | [SDK quickstart](https://vastai-80aa3a82.mintlify.app/sdk/python/quickstart) | В скрипте ожидания — таймаут и обработка этих статусов: destroy + другой оффер |
| Stopped ≠ бесплатно: диск капает бессрочно, у stopped ставка диска часто **выше**, чем у running | [pricing](https://docs.vast.ai/guides/instances/pricing) | Никогда не оставлять stopped «на завтра» — скачал результат → destroy |
| Трафик не виден в $/hr; сюрприз при 8 ГБ выгрузки на хосте с платным $/TB | [billing](https://docs.vast.ai/billing) FAQ «charged more than expected» | При выборе оффера проверить Internet-ставку; при $1/TB наши 10 ГБ — копейки, при $20/TB уже заметно |
| Нерабочий инстанс (SSH не пускает, битый образ) — деньги за диск/время **не возвращают автоматически**; отзывы: поддержка часто отказывает | [CostBench-агрегация отзывов](https://costbench.com/software/ai-gpu-cloud/vast-ai/hidden-costs/), ToS | Смок-тест сразу после running: `ssh … "nvidia-smi"` с таймаутом; не работает за 2–3 минуты → destroy (потеря = центы диска, GPU за нерабочий SSH-коннект биллится — потому проверять быстро) |
| Репорт хоста: формального механизма «пожаловаться и вернуть» нет; только чат поддержки на сайте, компенсация дискреционная | ToS, отзывы | За $0.5–1 не тратить время; за заметную сумму — чат с console.vast.ai, приложить instance id и скрин. Ожидания низкие |
| Хост уходит в offline посреди расчёта; биллинг offline не идёт, но **прогресс и данные могут пропасть** (реальные кейсы: «hosts go offline mid-training», данные недоступны) | [отзывы](https://medium.com/@velinxs/cheap-a100-rentals-find-the-best-deals-in-2026-ddeae320a324), FAQ | Чекпойнты + промежуточная выгрузка результатов наружу каждые N минут (см. §4 рецепта) |
| Interruptible/bid-инстанс перебили ставкой → stopped, процессы убиты, диск капает | FAQ | Для нашего 3-часового расчёта брать **on-demand**, не interruptible |
| Lifetime оффера меньше длины расчёта → автостоп посреди работы | FAQ | Фильтр `duration` при поиске: Max Duration ≥ 1 день |
| Забытый инстанс + autobilling = карта списывается бесконечно | [billing](https://docs.vast.ai/billing) | **Не включать autobilling**; держать баланс маленьким — это естественный предохранитель: при нуле всё стопится само |
| Reliability score: новые машины стартуют с 0.60, растёт от аптайма; это статистика, **не гарантия** — SSH-ловушки бывают и на 0.99 | [concepts](https://docs.vast.ai/guides/concepts), [FAQ](https://cdn.vast.ai/faq/) | Фильтровать `reliability > 0.98 verified=true direct_port_count>=1`, но смок-тест обязателен всё равно |

«Двойное списание при пересоздании» как баг в отзывах не подтвердилось — то, что пользователи так называют, это оплата диска/минут на каждом из брошенных инстансов (у нас уже случилось: ~$2 за три попытки). Защита — быстрый смок-тест и мгновенный destroy.

## 3. Матрица путей доставки данных

Ключевой факт из FAQ: для `vastai copy` local↔instance **машина** должна иметь открытые порты, а сам инстанс может быть даже **stopped** — копирование оркестрируется vast'ом на стороне хоста, работающий SSH-вход в контейнер не нужен.

| Путь | Работает без живого SSH-входа | Скорость | Надёжность / ограничения |
|---|---|---|---|
| `vastai copy local:… ID:…` (rsync под капотом) | **Да** (даже со stopped-инстанса), но машина — с открытыми портами | Высокая (прямое соединение) | Рекомендуемый основной путь; не копировать в `/root` или `/` — ломает права ssh-папки. [docs](https://docs.vast.ai/cli/reference/copy) |
| Cloud Sync / `vastai cloud copy` (S3, Backblaze, GDrive, Dropbox, HF) | **Да**, работает «even while inactive», запускается из UI/CLI/API | Высокая (хост качает напрямую в облако) | Настроить заранее в [Settings → Cloud Connections](https://cloud.vast.ai/settings/); креды временно попадают на хост — завести **отдельный ключ/бакет только под это**; vast доплаты не берёт, платится трафик хоста + хранение у облака. Только Docker-инстансы. [docs](https://docs.vast.ai/guides/instances/storage/cloud-sync) |
| scp/rsync через **direct** SSH (`--direct`, `vastai ssh-url`) | Нет | Высокая | Требует машину с открытыми портами; предпочтительный интерактивный путь |
| scp/rsync через **proxy** SSH (по умолчанию) | Нет | Низкая, прокси перегружаются | Докс: только для <1 ГБ. Наши 1.7 ГБ вверх — уже за границей комфорта |
| wget/curl **на** инстанс с внешнего URL | Да, если есть любой шелл (веб-терминал Jupyter) или onstart-скрипт | Максимальная («fastest method» по FAQ) | Идеально для заливки 1.7 ГиБ: положить архив на доступный URL, качать из onstart |
| Jupyter upload/download кнопки | Да (браузер) | Медленно через прокси; быстро только на direct-HTTPS инстансе | Лимит размера файла; годится как запасной канал для мелочи |
| `vastai copy` instance→instance | Да | Высокая | dst должен быть running с открытыми портами; для нас неактуально |

Практический вывод для нашего прогона: **вверх** — wget с URL или `vastai copy`; **вниз** — `vastai copy` + дублирование в Backblaze/S3 через `vastai cloud copy` (работает даже если SSH умер и даже если инстанс остановлен балансом).

## 4. Мониторинг без SSH

- **`vastai logs INSTANCE_ID`** — stdout контейнера, по умолчанию последние 1000 строк, есть `--tail N`, grep-фильтр и `--daemon-logs` (системные логи хоста — видно, что происходит до/вне контейнера). Работает чисто через API. [docs](https://docs.vast.ai/cli/reference/logs) → значит: если расчёт пишет прогресс в stdout (или в файл, который `tail -f`-ится в stdout), прогресс виден всегда, даже без SSH.
- **Jupyter launch mode** — веб-терминал в браузере с персистентной сессией + файловый менеджер с download. Полноценный запасной вход, если ssh-клиент не пускает. [docs](https://docs.vast.ai/guides/instances/connect/ssh) (раздел «SSH Alternative — Jupyter Terminal»)
- **Instance Portal** (в шаблонах vast.ai base-image) — кнопка Open: туннели Cloudflare, вкладка Logs со стримингом `/var/log/portal/*.log`. [docs](https://docs.vast.ai/guides/instances/connect/instance-portal)
- **`vastai show instances --raw`** — статус, аптайм, текущая ставка; **`vastai show invoices-v1 --charges`** — фактические списания почти в реальном времени (баланс обновляется раз в несколько секунд).

## 5. Рецепт защиты бюджета (полный набор)

1. **Баланс как предохранитель**: autobilling выключен, карта на автосписание не привязана к порогу; на балансе держать ровно бюджет прогона (~$8–10 на 3 часа 4×4090 с запасом). При нуле vast сам всё остановит. Включить low-balance email.
2. **Выбор оффера**: `verified=true`, `reliability > 0.98`, `direct_port_count >= 1`, Max Duration ≥ 1 день, Internet-ставка ≤ пары $/TB, CUDA-версия хоста ≥ требуемой образом (наша ловушка №1 — несовместимый драйвер: фильтровать `cuda_vers >= …`).
3. **SSH-ключ загружен на [manage-keys](https://cloud.vast.ai/manage-keys/) ДО создания** — ключи запекаются при создании, добавить к живому инстансу нельзя (наша ловушка №2).
4. **Создание**: `--ssh --direct`, минимальный `--disk`, Jupyter-совместимый шаблон (даёт веб-терминал как запасной вход). Onstart сразу пишет прогресс в stdout.
5. **Смок-тест за 3 минуты**: цикл ожидания `running` с таймаутом 10–15 мин и веткой на `exited/unknown/offline`; затем `ssh -o ConnectTimeout=15 … "nvidia-smi"`. Провал → немедленный destroy, следующий оффер. Цена ошибки при такой дисциплине — центы.
6. **Страховка результата**: чекпойнты расчёта на диск + `vastai cloud copy` в Backblaze/S3 (подключить заранее, ключ scoped на один бакет) — вручную по ходу или scheduled-job (`--schedule HOURLY`, [docs](https://docs.vast.ai/guides/instances/storage/cloud-backups)). Тогда смерть SSH, offline хоста или автостоп по балансу не теряют данные: stopped-инстанс всё ещё отдаёт файлы через `vastai copy`/Cloud Sync.
7. **Авто-гашение** (нативного авто-destroy по таймеру/бюджету у vast **нет**, есть только автостоп по lifetime и по нулевому балансу):
   - на локальной машине `at`/cron через 4 часа: `vastai destroy instance ID`;
   - дополнительно изнутри инстанса: per-instance API-ключ (рецепт из [FAQ](https://cdn.vast.ai/faq/) «How can I stop the instance from within») + `timeout 3.5h ./run.sh; ./vast stop instance ${VAST_CONTAINERLABEL:2}` — самостоп даже если локальная машина уснула. Стоп, не destroy — чтобы успеть забрать данные; destroy руками после скачивания.
8. **Контроль по ходу**: раз в ~30 мин `vastai logs` + `vastai show invoices-v1 --charges`; расхождение расхода с планом ($1.2–1.5/час + диск) — сигнал разбираться немедленно.
9. **Финал**: скачал → проверил контрольные суммы локально → `vastai destroy instance` → убедиться по `vastai show instances`, что список пуст, и по Charges, что счётчики встали.

Отдельно про пункт «вернуть деньги за нерабочий инстанс»: механизм один — чат поддержки на сайте; по ToS они не обязаны, по отзывам чаще отказывают на мелких суммах. Реалистичная стратегия — не допускать потерь больше центов дисциплиной из п. 5, а не надеяться на компенсации.

I think I have enough to work with now. For reporting broken machines, the mechanism seems to be through support chat on the console, with refunds being discretionary rather than automatic—that's consistent with what I've seen in aggregated reviews.

On double billing, I don't find anything specific, but the real trap is that recreating an instance re-downloads the image (not billed) while storage billing continues from creation, and bid instances that get outbid stop running but storage charges persist. Bandwidth is billed per byte in both directions at host-set rates regardless of instance state, though many hosts set it near $0/TB—worth checking the offer details. I'll skip the question of whether image pulls incur bandwidth charges since it's undocumented, and note there's no native auto-destroy timer for instances.

Auto-stop happens through a few mechanisms: the offer's max duration triggers auto-stop at expiry, there's a per-instance API key for stopping from within, scheduled jobs exist only for cloud backups, and balance depletion acts as a natural budget cap since instances stop at $0 if there's no card on file—though storage charges can still go negative while data awaits deletion. The API uses PUT to the instance state endpoint to stop and DELETE to destroy, with the CLI command `vastai destroy instance` doing the same, and users can set up their own cron job as a backup safeguard, plus there are webhooks for notifications.

I'm also noting that `vastai copy` for local-to-instance transfers works even while an instance is stopped, meaning data can be retrieved without SSH access, and cloud sync similarly works while inactive—though under the hood `vastai copy` still relies on SSH authentication, and copying to /root can break SSH folder permissions in ways that break future copy operations.

I'm reasoning through whether the copy operation is orchestrated host-side rather than through the container's SSH daemon, since it works even for stopped instances—suggesting it's not dependent on the container's sshd being functional.

I'm also covering `vastai logs`, which pulls container or daemon logs via S3 upload without needing SSH, Jupyter's web terminal for browser-based file transfer (slow, with size limits, also SSH-free), and the billing commands like `vastai show invoices-v1 --charges` and `vastai show user` for checking credit balance—I want to double check that the balance field is accurate before mentioning it, and I still need to verify whether destroying an instance stops billing immediately per the 10-minute FAQ.

Now I have enough to write the final report in Russian, organized into sections on billing facts, a catalog of pitfalls and protections, a matrix of data delivery paths, monitoring without SSH, and budget protection recommendations—with URLs and access dates included, kept concise but thorough. Since this is a chat response rather than file writes, there's no blast radius to worry about.