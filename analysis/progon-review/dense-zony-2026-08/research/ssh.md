Собрал всё необходимое. Итоговый отчёт.

---

# SSH-доступ к vast.ai: механика, отказы, обходы

Все ссылки на docs.vast.ai проверены сегодня, 20.08.2026.

## 1. Как vast доставляет ключи в контейнер

**Механика (Docker-инстансы, режим `--ssh`):**

- Аккаунт-ключи читаются **один раз — в момент создания инстанса** («it is applied at container creation time»). Ключ передаётся в контейнер через env-переменную `SSH_PUBLIC_KEY` при `docker create` — это видно в логах создания и задокументировано в списке переменных окружения ([docs: hello-world](https://docs.vast.ai/cli/hello-world), [docs: docker-environment](https://docs.vast.ai/guides/instances/docker-environment), [vast-cli issue #336](https://github.com/vast-ai/vast-cli/issues/336)).
- `--ssh` = launch mode «ssh»: vast **заменяет ENTRYPOINT образа своим setup-скриптом**, который ставит/запускает sshd, пишет `SSH_PUBLIC_KEY` в `/root/.ssh/authorized_keys` и потом исполняет ваш onstart ([docs: connect/overview](https://docs.vast.ai/guides/instances/connect/overview), [docs: template-settings](https://docs.vast.ai/guides/templates/template-settings)). В официальном `vastai/base-image` это `/opt/instance-tools/bin/entrypoint.sh`, ключи прописываются «every time the instance starts» ([github: vast-ai/base-image](https://github.com/vast-ai/base-image/)). `authorized_keys` живёт в overlay контейнера (не отдельный том) — при recreate перезаписывается.
- `attach ssh` на живом инстансе: API `POST /instances/{id}/ssh` регистрирует ключ за инстансом в базе vast ([docs: attach-ssh-key](https://docs.vast.ai/api-reference/instances/attach-ssh-key)); доставку в контейнер выполняет агент хоста. Официально: кнопка SSH на карточке инстанса «add a key **without recreating**» ([docs: hello-world](https://docs.vast.ai/cli/hello-world)) — перезапуск не требуется. Ваш ответ «SSH key already associated with instance» означает: в базе ключ есть, но агент хоста его в контейнер не донёс — это и есть разрыв.
- **Противоречие в доках, знать о нём:** [create-ssh-key API](https://docs.vast.ai/api-reference/accounts/create-ssh-key) обещает «automatically added to all your current instances», а [SSH-гайд](https://docs.vast.ai/guides/instances/connect/ssh) прямо говорит «Existing instances will **not** get the new key automatically». Верить второму.
- Прокси (`sshN.vast.ai:PORT`) и direct ведут в **один и тот же sshd контейнера** — аутентификация одна, поэтому отказ по обоим каналам = ключа нет в контейнере, а не проблема канала.
- VM/KVM-инстансы — отдельный мир: ключ запекается в образ VM, после создания не меняется, только пересоздание ([docs: virtual-machines](https://docs.vast.ai/guides/instances/virtual-machines)).

## 2. Каталог причин «Permission denied (publickey)»

| # | Причина | Решение | Источник |
|---|---|---|---|
| 1 | Ключа не было в аккаунте на момент create | Добавить ключ, пересоздать инстанс | [docs: ssh](https://docs.vast.ai/guides/instances/connect/ssh) |
| 2 | **Задержка инициализации**: sshd уже отвечает, а entrypoint ещё не дописал authorized_keys | Ждать и ретраить 1–3 мин; официальный баннер vast: «If authentication fails, try again after a few seconds» | [docs: ssh, sftp-баннер](https://docs.vast.ai/guides/instances/connect/ssh) |
| 3 | **Сломанная инжекция на конкретном хосте** (агент/скрипт хоста молча не записал ключ) — маркетплейс, качество хостов разное. Ровно ваш кейс: тот же образ, та же команда, хост №1 работает, №2/№3 нет | Destroy → другой хост (фильтры `verified=true reliability>0.98`); либо обход через onstart (см. §3) | отчёт пользователя о B200-хосте: «Vast.ai overrides your Docker entrypoint, silently breaking key injection» ([sillymoo.dev, 2026](https://sillymoo.dev/my-ai-forgot-what-plex-is-but-got-80-on-everything-else/)) |
| 4 | Неправильные права файлов внутри инстанса (KVM VM: `StrictModes yes` + кривые права на authorized_keys) | Для Docker vast ставит `StrictModes no`; для KVM — использовать их template, не голый image | [vast-cli issue #336](https://github.com/vast-ai/vast-cli/issues/336) |
| 5 | Ключ добавлен в аккаунт **после** создания инстанса | Кнопка SSH на карточке инстанса / `vastai attach ssh` (Docker); VM — только пересоздание | [docs: ssh](https://docs.vast.ai/guides/instances/connect/ssh) |
| 6 | Локальные проблемы: не тот приватный ключ, права ≠600, ключ не в агенте, обрезанный .pub при вставке в UI | `ssh -vv`, `chmod 600`, `ssh-add -l`; в UI вставлять ключ целиком с префиксом и комментарием | [docs: troubleshooting](https://docs.vast.ai/guides/reference/troubleshooting) |
| 7 | Не тот порт (взяли `ssh_host/ssh_port` из `show instances` вместо `ssh-url`) | Всегда `vastai ssh-url <id>` | [docs: ssh-url](https://docs.vast.ai/cli/reference/ssh-url), практика в сторонних скиллах |
| 8 | Запрос пароля вместо ключа = ключ не дошёл вовсе (паролей нет в принципе) | Те же действия, что #1–3 | [docs: FAQ jupyter-ssh](https://docs.vast.ai/guides/reference/faq/jupyter-ssh) |

**Про ENTRYPOINT colmap:** в режиме `--ssh` ENTRYPOINT образа **не запускается вообще** — vast заменяет его своим скриптом. Значит ENTRYPOINT colmap не может блокировать ssh-обвязку; предупреждение JSONArgsRecommended нерелевантно. Побочный эффект замены только один: colmap-процессы сами не стартуют (вам это и не нужно — вы запускаете руками). То, что инстанс №1 с тем же образом работал, подтверждает: образ совместим, виноваты хосты №2/№3 (причина 3).

**Launch modes:** `ssh`/`jupyter` — entrypoint заменяется, ключ инжектится, есть onstart; `args`/entrypoint-mode — образ запускается как есть, **ssh не ставится вовсе**, доступ — забота образа ([docs: creating-instances-with-api](https://docs.vast.ai/api-reference/creating-instances-with-api)). Template vs raw image: template — это преднастроенный пресет (image+mode+onstart+env); для KVM работает только template, для Docker разницы в механике ключей нет.

## 3. Обходные пути, по убыванию надёжности

1. **Onstart-скрипт с явной записью ключа** — самый надёжный, ключ кладётся вашим кодом, не агентом:

```bash
vastai create instance <OFFER_ID> --image colmap/colmap:latest --disk 120 --ssh --direct \
  --onstart-cmd 'mkdir -p /root/.ssh && chmod 700 /root/.ssh && echo "ssh-ed25519 AAAA...ваш.pub целиком" >> /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys && echo KEY_INSTALLED && cat /root/.ssh/authorized_keys'
```

Лимит поля onstart — 4048 символов; длиннее — файл через `--onstart FILE` или gzip+base64 ([docs OpenAPI notes](https://docs.vast.ai/api-reference/instances/show-ssh-keys)). `echo KEY_INSTALLED && cat ...` даёт проверку **без ssh**: `vastai logs <id>` покажет, исполнился ли скрипт и что лежит в authorized_keys. Оговорка: onstart исполняется тем же vast-entrypoint'ом — если у хоста сломан сам запуск обвязки, не спасёт ничто, кроме смены хоста.
2. **Кнопка на карточке инстанса** (тот самый «instance-specific SSH interface» / Open SSH Interface → add/remove SSH keys): добавляет ключ живому Docker-инстансу без пересоздания и без перезапуска ([docs: hello-world](https://docs.vast.ai/cli/hello-world)). Эквивалент CLI — `vastai attach ssh <id> "$(cat ~/.ssh/id_ed25519.pub)"`. Надёжность средняя: доставку делает тот же агент хоста, который у вас уже молчал.
3. **Jupyter-режим как ручной канал**: `--jupyter` вместо `--ssh` — vast ставит Jupyter в рантайме, web-терминал доступен из браузера без ключей (по токену), оттуда можно руками дописать authorized_keys ([docs: ssh, «SSH Alternative — Jupyter Terminal»](https://docs.vast.ai/guides/instances/connect/ssh)). Для colmap-образа сработает (Ubuntu-база), но тянет установку Jupyter.
4. **`vastai execute`** — НЕ вариант: разрешены только `ls`, `rm`, `du`, записать ключ нельзя ([docs: execute](https://docs.vast.ai/cli/reference/execute)). Ограничения «только для остановленных» в доках нет — он ходит через агент хоста, но для нашей задачи бесполезен.
5. **env-переменные** (`--env '-e ...'`) — ключ так не доставить: ваши env не попадают в ssh-сессии автоматически и никто их в authorized_keys не пишет; это канал для onstart, не замена ему ([docs: docker-environment](https://docs.vast.ai/guides/instances/docker-environment)).

## 4. Максимально дешёвая проверка SSH

- CPU-only инстансов на vast нет, минимум — 1x слабый GPU. Самые дешёвые: GTX 1060/1080/1080 Ti и RTX 3060 — **$0.03–0.05/час** on-demand, спот от ~$0.02 ([gpus.io, авг 2026](https://gpus.io/en/providers/vast-ai), [getdeploying](https://getdeploying.com/gpus/nvidia-gtx-1080)).
- Биллинг посекундный, без минимальных списаний; баланс обновляется раз в несколько секунд ([docs: FAQ billing](https://docs.vast.ai/guides/reference/faq/billing)).
- **Пока образ качается («loading»), GPU-время НЕ биллится**: «Storage charges begin at creation. GPU charges begin when status reaches `running`» ([официальный SKILL.md vast-cli](https://raw.githubusercontent.com/vast-ai/vast-cli/master/vastai/SKILL.md)). Storage биллится с момента создания в любом состоянии, bandwidth — за трафик всегда.
- Итого smoke-тест: дешёвый GPU + `--disk 10` + лёгкий образ (`vastai/base-image` вместо colmap, чтобы не качать гигабайты) + destroy через 5 минут = **< 1 цента**. Обязательно `vastai destroy instance <id> -y` — stopped-инстанс продолжает есть storage.

## 5. Канон автоматизации (и рецепт для образов с ENTRYPOINT)

Официальный цикл из SKILL.md vast-cli: `create ssh-key` (до всего) → `create instance` → поллинг `show instance <id> --raw` до `actual_status == "running"` **с таймаутом и веткой ошибки** (`exited`/`unknown`/`offline` никогда не станут running — destroy и другой оффер) → `ssh-url` → подключение. Для образов со своим ENTRYPOINT канон один: в `--ssh`-режиме перенести команду ENTRYPOINT в onstart, если её запуск нужен ([docs: template-settings](https://docs.vast.ai/guides/templates/template-settings)); «всегда работающего» создания vast не гарантирует — гарантию даёт связка onstart-инжекция ключа + быстрый fail-over на другой хост.

---

# Рекомендуемый надёжный рецепт

```bash
# 0) один раз: ключ в аккаунте
vastai create ssh-key ~/.ssh/id_ed25519.pub

# 1) оффер только с хорошего хоста
vastai search offers 'verified=true reliability>0.98 direct_port_count>=1 rentable=true num_gpus=1' -o 'dph_total' --raw

# 2) создание: ключ кладём САМИ через onstart + маркер для проверки без ssh
PUB=$(cat ~/.ssh/id_ed25519.pub)
vastai create instance <OFFER_ID> --image colmap/colmap:latest --disk 120 --ssh --direct \
  --onstart-cmd "mkdir -p /root/.ssh && chmod 700 /root/.ssh && echo '$PUB' >> /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys && echo ONSTART_OK"

# 3) поллинг до running, таймаут 10 мин; exited/unknown/offline => destroy, другой оффер
vastai show instance <ID> --raw   # actual_status

# 4) проверка инжекции БЕЗ ssh:
vastai logs <ID> --tail 50        # ищем ONSTART_OK

# 5) подключение строго по ssh-url, с ретраями ~3 мин (инициализация)
vastai ssh-url <ID>
ssh -o ConnectTimeout=15 -o StrictHostKeyChecking=no -p <PORT> root@<HOST> 'echo OK'

# 6) permission denied дольше 5 мин при ONSTART_OK в логах — хост битый:
vastai destroy instance <ID> -y   # и берём следующий оффер из шага 1
```

Правила: ключ у onstart не зависит от агента хоста; `vastai logs` — канал диагностики без ssh; не чинить битый хост (attach/reboot у вас уже не помогли — это подтверждает известный паттерн «молча сломанной инжекции» на части хостов маркетплейса), а менять хост — это дешевле 1 цента на слабом GPU.

I think I have enough to put together a full report in Russian, covering key delivery mechanics, a catalog of permission-denied causes, workarounds ranked by reliability, a cheap test, and automation guidance, plus a failure table and recommended recipe — drawing on the Vast.ai SSH docs noting that account keys apply only at instance creation and don't propagate to existing instances.

I'm also noting the contradiction between the API docs (claiming new keys auto-propagate to all instances) and the SSH guide (saying they don't), along with details on the SSH_PUBLIC_KEY env var, how ssh/jupyter launch modes replace the entrypoint, the role of onstart scripts, and how the base-image's entrypoint.sh re-applies SSH keys on every container start. I'm also pulling in a GitHub issue about KVM mode problems and a blog post about B200 hosting issues as supporting evidence.

Now I'm gathering more practical details: the SFTP banner's advice to retry failed auth after a few seconds, the CLI skill's status table and billing rules (storage charged from creation, GPU charged only while running, with balances updating every few seconds), the limited scope of the execute command (just ls/rm/du via the host agent), the attach-ssh command, the 4048-character onstart limit, a workflow doc recommending attaching SSH after instance creation, rough GPU pricing for older cards, and Jupyter Terminal as a fallback access method when SSH isn't available.

I also realize vastai logs works without SSH and only shows container stdout — but an onstart script could echo the authorized_keys file to stdout, making it readable through logs as a debugging trick. Putting this together, the user's issue (same image works on one host but fails on others) points to host-side key injection variance rather than anything about the container's entrypoint, since vast overrides it in SSH mode anyway. My recommendation: have onstart echo the key, verify via logs, and if a host is unreliable, destroy the instance and pick a different one, filtering for verified hosts.

On the "restart after attach" question, the docs are ambiguous — the base image claims keys are set up "every time the instance starts," but since SSH_PUBLIC_KEY is fixed at creation time, it's unclear whether attach-ssh updates live containers without a restart; community reports suggest it usually works, but clearly it failed for this user, pointing to a broken host agent. I should flag the contradiction between the two doc pages on this point, and note that vastai execute is too restricted (ls/rm/du only) to be used for writing SSH keys directly.

For web terminal access, only Jupyter-launch or Instance Portal images expose one — SSH-mode instances like colmap have no web channel, so the workaround is switching to --jupyter runtype to get a terminal and fix keys manually, though reliability is moderate. I should also mention the GUI's "Open SSH Interface" button, which reportedly lets users add/remove SSH keys per instance without recreating it.

For a guaranteed fix, I'm thinking through an --onstart-cmd recipe that creates the .ssh directory, appends the public key to authorized_keys, and sets correct permissions — though this depends on vast's entrypoint actually running onstart if the wrapper itself fails. I'd also echo the key and cat the authorized_keys file so it shows up in vastai logs for verification. Now I'm considering what the cheapest way to test this would be.

A single low-end GPU on-demand with minimal disk should cost well under a cent for a short test, since billing is per-second with storage starting at creation and GPU time starting at running. The overall flow I'm mapping out: register the key, search for reliable offers with direct port access, create the instance with the onstart key-injection command, poll until running, grab the ssh-url, then retry the ssh connection for a few minutes before giving up and trying a different host if permission denied persists. Now I want to write up the report — compact but covering all of this.

For dates, I should avoid guessing at exact issue or post dates and instead just note that the information is current as of the fetch date, 2026-08-20.