Материала достаточно — все гипотезы проверены по документации, GitHub-issues и пользовательским отзывам. Собираю отчёт.

---

# Карта жизненного цикла vast.ai → возможные отказы

Формат каждой строки: **отказ** · частота по источникам · защита · цена проверки.

## 1. Выбор оффера

- **Заявленные ТТХ ≠ реальные.** Сеть «1500 Mbps» даёт <100 Mbps; хост тихо срезал GPU-производительность на 22% без уведомления и скидки ([CostBench, 2026](https://costbench.com/software/ai-gpu-cloud/vast-ai/hidden-costs/), [hostlist.io отзывы](https://hostlist.io/host/vast-ai)). Частота: систематические жалобы на дешёвых/неверифицированных хостах. Защита: `verified=true`, `reliability>0.96`, история >100 ч, свежие отзывы ([DeployBase](https://deploybase.ai/articles/a100-vastai)). Цена проверки: 0 (фильтры поиска).
- **Вам достаётся ДОЛЯ машины.** Поле `cpu_cores_effective` — «virtual cpus you get», отдельное от `cpu_cores` всей машины; аналогично RAM и `gpu_frac` ([docs: search-offers](https://docs.vast.ai/cli/reference/search-offers)). Оффер «4×4090» может быть четвертью 16-GPU-машины: CPU, диск и сеть делите с соседями. Защита: фильтровать по `cpu_cores_effective`, а не `cpu_cores`. Цена: 0.
- **P2P между GPU глобально отключён на мультитенантных хостах** (безопасность): NCCL-обмен идёт через RAM, 0.15–0.5× от PCIe ([постмортем cortwave, 4×4090](https://cortwave.github.io/posts/multi-gpu/)). Для COLMAP это не смертельно (задачи на GPU независимы), но `pcie_bw` на карту проверять стоит. Цена: 1 мин (`nvidia-smi topo -m`).
- **Bandwidth платный per-byte в обе стороны, цена хостовая** — встречалось $2.5/100 GB ([CostBench](https://costbench.com/software/ai-gpu-cloud/vast-ai/hidden-costs/)). Для ваших 1.7 ГиБ + результаты — центы, но rate смотреть до аренды (hover на цене оффера). Цена: 0.
- **У on-demand контракта есть max duration**: по истечении инстанс автостопится, через 48 ч может быть удалён ([docs: instance-types](https://docs.vast.ai/guides/instances/choosing/instance-types)). Проверять поле duration на карточке — чтобы «3 часа» влезли с запасом. Цена: 0.

## 2. Создание

- **Офферы-призраки — ПОДТВЕРЖДЕНО.** «Offer is no longer available» — гонка при создании, официально задокументирована ([docs: find-and-rent](https://docs.vast.ai/guides/instances/choosing/find-and-rent)); также «allocation failed: insufficient capacity» на хостах, которые числятся свободными. Защита: ловить ошибку, сразу брать следующий оффер из списка. Цена: retry-логика в скрипте.
- **`spend_rate_limit` нового аккаунта.** Лимит трат «почти ноль» до верификации email, растёт автоматически «за несколько часов» использования; повышение — через чат поддержки, крипто-плательщикам дают охотнее ([docs: troubleshooting](https://docs.vast.ai/guides/reference/troubleshooting), [account-settings](https://docs.vast.ai/guides/reference/account-settings)). 4×4090 — относительно дорогой инстанс, создание может упасть по лимиту аккаунта, а не по вине хоста. Цена: дешёвый тест-инстанс заранее / подождать пару часов после первых трат.
- **Статус `error` при провижининге** → хост битый, только destroy + другой оффер ([каталог ошибок vastai](https://eliteai.tools/agent-skills/vastai-common-errors)).

## 3. Загрузка образа

- **Гипотеза «медленный pull за наш счёт» — ЧАСТИЧНО ОПРОВЕРГНУТА по деньгам, ПОДТВЕРЖДЕНА по времени.** GPU-время в статусе `Loading` официально НЕ тарифицируется ([docs: billing FAQ](https://docs.vast.ai/guides/reference/billing)), но storage-плата идёт с момента создания, и pull может длиться «час и более» на медленном линке ([docs: manage-instances](https://docs.vast.ai/guides/instances/manage-instances)). Порог из документации: норма <5 мин, >20 мин — смотреть `vastai show logs`, бросать и пересоздавать. Цена: таймаут в скрипте + slim-образ (4 GB pytorch-runtime вместо 30 GB «всё включено»).
- **Poll-ловушка:** `exited` / `unknown` / `offline` НИКОГДА не перейдут в `running` — скрипт без ветки ошибок крутится вечно, диск капает ([официальный SDK quickstart](https://vastai-80aa3a82.mintlify.app/sdk/python/quickstart) прямо предупреждает). Защита: таймаут + обработка всех статусов.

## 4. Доступ

- **В режимах SSH/Jupyter ваш Docker ENTRYPOINT НЕ выполняется** — его подменяет установочный скрипт vast; запускать работу надо из on-start ([docs: template-settings](https://docs.vast.ai/guides/templates/template-settings)). Классический сценарий «running, а батч не стартовал». `--onstart-cmd` ≤16 КБ, перезапускается при каждом старте контейнера.
- **Образы с non-root sshd ломаются на vast** (vast сам пытается поднять sshd от root): [base-image #141](https://github.com/vast-ai/base-image/issues/141), март 2026. Защита: официальные образы vastai/* или проверенный pytorch.
- **«Connection refused» первые 30–60 с после `running`** — sshd ещё поднимается; это не ошибка ([vastai-common-errors](https://github.com/jeremylongshore/claude-code-plugins-plus-skills/blob/main/plugins/saas-packs/vastai-pack/skills/vastai-common-errors/SKILL.md)).
- Порты — случайные внешние на общем IP, до 64 штук; узнавать только из `vastai show instance`, не хардкодить.

## 5. Доставка данных

- **Proxy-SSH throttled: официальная рекомендация scp через прокси только до ~1 ГБ**, дальше — direct-TCP ([docs data-movement, цит. по профилю vastai](https://github.com/sickn33/agentic-awesome-skills/blob/main/skills/remote-gpu-trainer/profiles/vastai.md)). Ваши 1.7 ГиБ — уже за порогом: создавать с `--direct` и фильтром `direct_port_count>=1`. Цена: 0.
- **`vast copy` сломан** — открытый баг [vast-cli #326](https://github.com/vast-ai/vast-cli/issues/326) (февр. 2026). Защита: rsync/scp по direct-порту, не полагаться на `vastai copy`.

## 6. Длительный счёт (ваши ~3 часа)

- **«Running», но контейнер падает циклически — ПОДТВЕРЖДЕНО как статус `exited`** (bad entrypoint/зависимости); лечится только чтением `vastai logs` ([vastai-common-errors](https://eliteai.tools/agent-skills/vastai-common-errors)). Защита: через 2–3 мин после старта проверить, что процесс COLMAP реально жив и пишет вывод.
- **Выдёргивание on-demand хостом — УТОЧНЕНО:** цену живого контракта хост поднять не может, но (а) контракт истекает по max duration и хост вправе не продлить ([Rental-Types](https://vast.ai/article/Rental-Types)); (б) хост может просто уйти в `Offline` (питание/интернет/желание) — жалобы регулярные, деньги за даунтайм не возвращают ([CostBench](https://costbench.com/software/ai-gpu-cloud/vast-ai/hidden-costs/)); (в) подтверждён кейс тихой деградации производительности под живым контрактом. Никакого SIGTERM/уведомления при пропаже нет. Защита: промежуточные результаты сбрасывать наружу поэтапно (после SfM, после stereo, после fusion), не одним куском в конце.
- **Slow CPU — ПОДТВЕРЖДЕНО механизмом**: `cpu_cores_effective` может быть малой долей заявленных ядер; «128 ядер» в карточке — это вся машина. Ваш fusion, CPU-этап, растянется кратно. Защита: `nproc` и короткий CPU-бенч сразу после SSH, до запуска большого счёта. Цена: 1 мин.
- **Диск переполняется и НЕ расширяется**: размер фиксируется при создании навсегда, min 10 GB по умолчанию ([docs: find-and-rent](https://docs.vast.ai/guides/instances/choosing/find-and-rent)); «No space left on device» посреди прогона = пересоздание с нуля. Гипотезу «диск занят чужими данными» источники не подтверждают (квота ваша личная), а вот «медленный диск на дешёвых хостах» — да ([rfp.wiki, сводка отзывов 2026](https://www.rfp.wiki/artificial-intelligence/ai-infrastructure-platforms/vast-ai)). Защита: `--disk` с 3–5× запасом (COLMAP dense: depth+normal maps легко ×20 к входу), `dd`-тест диска после входа.
- **Баланс → $0 = автостоп инстансов**, без сохранённой карты — уничтожение данных ([docs: billing](https://docs.vast.ai/guides/reference/billing)). С бюджетом $23 и 4×4090 по ~$1.5–2/час запас есть, но bandwidth-расход в $/hr не показывается — следить за Charges.

## 7. Выгрузка

- **Результаты гибнут при destroy безвозвратно**, платформенного FS нет. Правило: destroy только после проверенного exit-кода копирования И контрольного открытия файлов локально. Большая выгрузка через proxy виснет (см. стадию 5).

## 8. Гашение

- **`stop` ≠ выключение счётчика: диск биллится вечно**, даже в минус — «сюрприз-счёт №1» на платформе ([docs: billing](https://docs.vast.ai/guides/reference/billing)). Единственный стоп-кран — `destroy`.
- **CLI `destroy` без `-y` виснет на интерактивном подтверждении** — ловушка для автоматизации ([официальный SKILL.md vast-cli](https://github.com/vast-ai/vast-cli/blob/master/vastai/SKILL.md)).
- Volume (если создадите) биллится отдельно и ПОСЛЕ destroy инстанса, пока сам volume не удалён.

## 9. Счета

- **Таймзоны — гипотеза НЕ ПОДТВЕРДИЛАСЬ как риск**: биллинг посекундный с предоплаченного кошелька, привязки к суткам нет; UTC всплывает только в экспорте инвойсов. Реальный риск другой: **bandwidth-строка не входит в $/hr** и всплывает отдельно ([docs: billing](https://docs.vast.ai/guides/reference/billing)).
- **Деньги за нерабочие инстансы не возвращают**: множественные жалобы «charged for non-functional service», поддержка отказывает ([CostBench](https://costbench.com/software/ai-gpu-cloud/vast-ai/hidden-costs/)). Ваши $2 на несовместимый драйвер — норма платформы, а не невезение; закладывайте «налог на перебор» ~10–15% бюджета.
- **Rate limits API: HTTP 429 без `Retry-After`**, per-endpoint + per-identity; CLI сам ретраит (3 попытки, backoff от 0.15 с) — для батч-скриптов рекомендован `--retry 6` ([docs: rate-limits](https://docs.vast.ai/cli/rate-limits)). Не поллить статус чаще раза в несколько секунд.
- **API-ключ попадает в URL запросов** — открытый security-issue [vast-cli #470](https://github.com/vast-ai/vast-cli/issues/470) (июль 2026): ключ светится в любых прокси-логах. Не гонять CLI через логирующие прокси; секреты не вшивать в образ и onstart (они хранятся платформой).

---

# ТОП-5 самых опасных неожиданностей для вашего сценария

1. **Нерасширяемый диск против аппетитов COLMAP dense.** Вход 1.7 ГиБ обманчив: depth/normal maps на PatchMatch-этапе раздуваются на порядки, а `--disk` фиксируется при создании навсегда. Переполнение на 2.5-м часе = весь прогон в мусор. Защита стоит центы: 80–100 GB диска (~$0.01–0.02/час).
2. **`cpu_cores_effective`: fusion упрётся в четверть машины.** Fusion — CPU-этап, а 4×4090-оффер часто есть доля большого хоста: «128 ядер» в шапке, 16–32 реально ваших. Прогон растянется в разы — при почасовой оплате это прямой удар по $23. Проверка бесплатна: поле оффера до аренды + `nproc` до запуска.
3. **Автоматизация без веток ошибок жжёт деньги молча.** Три задокументированные ловушки разом: poll-цикл по статусам, которые никогда не станут `running`; `stop` вместо `destroy` (диск биллится вечно); `destroy` без `-y`, висящий на подтверждении. Для команды, впервые автоматизирующей аренду, это самый вероятный способ потерять больше $2.
4. **Подмена ENTRYPOINT в SSH-режиме.** Батч, упакованный в ENTRYPOINT образа, не запустится вообще — инстанс будет честно «running» и тикать деньгами, пока вы разбираетесь. Запуск — только через on-start скрипт, и через 2–3 минуты проверять, что COLMAP реально пишет вывод.
5. **`spend_rate_limit` нового аккаунта.** Лимит трат растёт с возрастом аккаунта; относительно дорогой 4×4090 может не создаться в нужный момент по лимиту самого аккаунта — сюрприз в ночь прогона, когда всё остальное готово. Дёшево снимается заранее: верифицированный email, маленький тестовый прогон за часы до боевого, при отказе — чат поддержки.

Вне топа, но держать в голове: хост может тихо деградировать или уйти в Offline без сигнала и без рефанда — при 3-часовом прогоне риск умеренный, закрывается поэтапной выгрузкой промежуточных результатов (после SfM, после stereo, после fusion), а не одним архивом в конце.

I have enough material now to compile the report in Russian, structuring it as a lifecycle map covering stage, failure mode, frequency by source, mitigation, and verification cost, plus a top-5 list tailored to their specific scenario with 4×4090 GPUs, ~3 hour runtime, COLMAP batch processing, 1.7 GiB of data, and a $23 budget. I'm starting with the offer-selection stage, noting how advertised specs often diverge from reality—like network speeds dropping from a claimed 1500 Mbps to under 100 Mbps in practice.

For the creation stage, I'm noting the race condition where offers become unavailable right as you try to claim them, plus how new accounts face near-zero spend rate limits until email verification, which could block instance creation entirely.

Moving to provisioning errors, I need to destroy and retry on another host if it fails. For image loading, I'm tracking that it's not GPU-billed but bandwidth charges can still accrue, and slow links can stretch this stage for hours — anything past 20 minutes warrants checking logs since normal loads finish under 5.

For access, I'm flagging that SSH mode overrides the entrypoint entirely, so onstart scripts are the right approach for batch jobs, plus watching for sshd conflicts on non-root images, random external ports, and the 16KB onstart-cmd limit that reruns on every start. For data delivery, I'm weighing proxy scp for small transfers under 1GB against direct access requirements for larger ones.

For the long multi-hour run, I'm cataloging failure modes: hosts going offline, contracts expiring, balance hitting zero triggering autostop, silent performance degradation, and fixed non-resizable disks that can kill the run if COLMAP's dense reconstruction generates far more data than the input size suggests — depth maps and fused outputs can balloon to tens of GB. I'm also noting CPU-bound fusion steps will drag if the effective core allocation is low, and that there's no SIGTERM warning when things go wrong.

For upload and teardown, I'm tracking that exit status should be verified before destroying the instance, large transfers over proxy can stall, stopping an instance isn't the same as destroying it (disk keeps billing), destroy is irreversible, volumes bill separately afterward, and CLI destroy can hang without the -y flag.

For billing, I'm noting bandwidth isn't included in the hourly rate, charges apply even on failed starts with no refunds, offline periods still get billed, billing is per-second from a prepaid wallet so timezones don't matter though invoices use UTC, and there's a known issue where API keys leak into request URLs.

Now I'm checking rate limiting behavior — 429 responses with no Retry-After header, and the CLI's default retry count of 3.

I'm going through each hypothesis to confirm or refute it: ghost offers are confirmed (insufficient capacity errors, allocation failures), with a ~20 minute loading threshold per docs, and exited/unknown/offline states never resolve. The broken docker pull hypothesis is partially true — GPU time isn't billed during loading, but bandwidth charges and lost time are still possible, with a 10-20 minute threshold. For the running-but-restart-loop case, I'm confirming exited is a real status, connection refused shortly after running just means sshd hasn't started yet (30-60s), and containers can crash-loop on bad entrypoints, which needs status checks to verify.

On pricing and on-demand preemption: contract price is fixed for its duration, so hosts can't raise prices mid-contract, but contracts have a max duration and hosts may not renew, or may go offline — silent performance degradation is confirmed by reviews. For disk issues, overflow is a quota limit and non-resizable, and advertised vs actual disk bandwidth shows poor performance on cheap hosts per review sites. Network degradation is confirmed (1500→100), slow CPU is confirmed via the cpu_cores_effective mechanism, and I'm now looking into timezone handling with per-second prepaid billing.

Rate limit risk seems near zero — 429s come without Retry-After but the CLI auto-retries. New accounts face a confirmed spend_rate_limit that's nearly zero until verification and grows over hours; geographically, payments go through Stripe cards or crypto (BitPay/Crypto.com), and Russian cards aren't accepted by Stripe, though I'll stick to what's documented rather than assert beyond it.

For their specific scenario — 4×4090, 3 hours, COLMAP, 1.7GiB, $23 — I'm building a top-5 risk list, starting with disk: it's fixed at creation and non-resizable, and COLMAP's dense reconstruction (depth/normal maps) can easily consume tens of GB, risking overflow.

Second risk is cpu_cores_effective — with fusion/CPU-stage work, a 4×4090 offer might only allocate a quarter of the machine's cores, so I need to check the offer field and run nproc before the main job starts. Third, stop isn't the same as destroy, and an automation poll-loop without a timeout that gets stuck on exited/unknown states will keep burning disk and bandwidth — destroy after verified export is the only real safeguard. Fourth, entrypoint gets overridden in SSH mode, so batch jobs need to run via onstart rather than ENTRYPOINT to avoid sshd conflicts.

Fifth, a new account's spend_rate_limit could cap out around $1.2-2/hr for 4×4090, meaning creation could fail due to account limits rather than host issues — plus there's a moderate risk of silent host degradation, though a 3-hour window limits exposure. Now I'm structuring the report in Russian markdown, organizing it as compact per-stage lists covering stage → failure → frequency by source → defense/workaround → verification cost, since a full table would be too wide.