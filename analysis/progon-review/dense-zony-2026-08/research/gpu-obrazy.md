# Отчёт: нестандартные CUDA-образы на vast.ai (кейс colmap/colmap + RTX 4090)

## 1. Фильтрация офферов по CUDA/драйверу

- Поле `cuda_max_good` в JSON оффера = максимальная CUDA-версия, которую поддерживает хост-драйвер. В CLI-запросе оно называется `cuda_vers` («machine max supported cuda version (based on driver version)») — [docs.vast.ai/cli/reference/search-offers](https://docs.vast.ai/cli/reference/search-offers). Helpdesk-статья (обновлена 27.01.2026) подтверждает: в веб-портале этого фильтра нет, только CLI — [vast-ai.crisp.help](https://vast-ai.crisp.help/en/article/how-to-filter-max-cuda-version-hgkvlo/).
- Рабочий синтаксис (пример прямо из доков):

```bash
vastai search offers 'gpu_name=RTX_4090 num_gpus=1 cuda_vers>=12.9 driver_version>=575.51.03 reliability>0.98 verified=true' -o 'dph'
```

- `driver_version` — строка «3 digit» (`535.86.05`), сравнение поддерживается (`driver_version >= 535.86.05` в официальном примере). Надёжность: значение выводится из драйвера, который репортит демон хоста — для отсечки годится, но финальная истина — `nvidia-smi` уже на инстансе (см. смоук). Для вашего кейса `cuda_vers>=12.9` эквивалентен «нативный драйвер ≥575.51», что снимает ошибку 804 в корне.

## 2. Каталог отказов GPU-стека → причина → диагностика за минуту → решение

| Отказ | Причина | Диагностика (≤1 мин) | Решение |
|---|---|---|---|
| **CUDA error 804** «forward compatibility … non supported HW» | Образ несёт `cuda-compat-12-9` (ставится в base-слой всех nvidia/cuda-образов — [Dockerfile NVIDIA](https://gitlab.com/nvidia/container-images/cuda/blob/master/dist/13.3.0/ubuntu2404/base/Dockerfile)); при хост-драйвере < требуемого compat-libcuda подхватывается, а GeForce forward compat не поддерживает — только Data Center GPU ([NVIDIA docs](https://docs.nvidia.com/deploy/cuda-compatibility/forward-compatibility.html); тот же кейс именно с COLMAP — [форум NVIDIA, 02.2024](https://forums.developer.nvidia.com/t/cuda-fails-with-the-forward-compatibility-error/281091)) | `nvidia-smi` (драйвер <575?) + `ls /usr/local/cuda/compat/` + `ldconfig -p \| grep libcuda` — какой libcuda резолвится | Либо хост с `cuda_vers>=12.9`, либо нейтрализовать compat (см. ниже) — тогда работает и на 560 |
| **«Failed to initialize NVML: Driver/library version mismatch»** | Хост обновил драйвер без ребута: kernel-модуль ≠ userspace-библиотеки. Изнутри контейнера НЕ чинится ([Exxact](https://support.exxactcorp.com/hc/en-us/articles/32810166604183), [vast-хостерская wiki](https://www.cryptolabs.co.za/vast-ai-gpu-hosting-wiki/?wiki_page=nvidia-driver-guide)) | `nvidia-smi` падает мгновенно с этим текстом; сверка `cat /proc/driver/nvidia/version` vs версия NVML в ошибке | Менять хост. Кнопка «гаечный ключ» → report machine, destroy, арендовать другой |
| **«No devices were found» / «couldn't communicate with the NVIDIA driver»** | Драйвер хоста не загружен/сломан | `nvidia-smi` | Официальный совет vast: report machine и арендовать другую ([docs.vast.ai/cuda](https://docs.vast.ai/cuda)) |
| **nvidia-smi работает, «CUDA Version: N/A», libcuda не находится** | `NVIDIA_DRIVER_CAPABILITIES` без `compute` — тогда toolkit не монтирует CUDA-библиотеки ([NVIDIA CTK docs](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/1.17.3/docker-specialized.html)) | `echo $NVIDIA_DRIVER_CAPABILITIES`; в выводе nvidia-smi поле CUDA Version | В colmap/colmap не грозит: базовый nvidia/cuda задаёт `compute,utility` в ENV. Для самосборных образов — прописать этот ENV в Dockerfile или `-e` при создании |
| **CUDA error 803** «unsupported display driver / cuda driver combination» | Зеркальная ловушка compat: хост-драйвер НОВЕЕ compat-либы из образа, а та стоит первой в путях загрузчика ([vLLM #35593](https://github.com/vllm-project/vllm/issues/35593), [whisper.cpp #3814](https://github.com/ggml-org/whisper.cpp/issues/3814)) | `ldconfig -p \| grep libcuda`: резолвится `/usr/local/cuda/compat/...`? | Тот же фикс, что для 804 — приоритет хостовому libcuda |
| **MIG включён** | На датацентровых картах: контейнер видит не то устройство/0 устройств. На RTX 4090 MIG отсутствует — не ваш случай | `nvidia-smi` — колонка «MIG M.» | Менять хост (арендатор MIG не выключит) |
| **ECC-ошибки / залоченные клоки** | Деградировавшая память; хост залочил частоты | `nvidia-smi -q -d ECC,CLOCK`; растущие Uncorr. ECC при нагрузке | Менять хост; медленные клоки — терпимо, ECC-ошибки — нет |
| **cudaErrorCallRequiresNewerDriver** | Приложение использует фичу, требующую драйвер новее, несмотря на minor-version compatibility ([NVIDIA](https://docs.nvidia.com/deploy/cuda-compatibility/latest/595/minor-version-compatibility.html)) | Текст ошибки уникален | Хост с драйвером под мажор+минор тулкита |

**Нейтрализация compat-ловушки (804/803) без смены хоста** — в onstart, до первого запуска colmap:

```bash
export LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH
# или радикально: rm -f /usr/local/cuda/compat/libcuda* && ldconfig
```

Это заставляет загрузчик брать хостовый libcuda, вмонтированный container toolkit'ом (проверенный workaround из vLLM #35593; AWS делает то же самое условной логикой в entrypoint — [SageMaker docs](https://docs.aws.amazon.com/sagemaker/latest/dg/container-nvidia-compliance.html)). Важно: после этого CUDA 12.9-приложение на драйвере 560 работает через **minor version compatibility** (любой драйвер ≥525 покрывает весь мажор 12.x), а образ colmap собран с реальным SASS под `all-major` (включая sm_89 для 4090) — JIT не нужен.

## 3. ENTRYPOINT-образы на vast

Три режима запуска ([docs.vast.ai/guides/instances/connect/overview](https://docs.vast.ai/guides/instances/connect/overview), [docker-environment](https://docs.vast.ai/guides/instances/docker-environment)):

- **SSH / Jupyter**: entrypoint образа **заменяется** скриптом vast (он и поднимает sshd). Для colmap это не потеря — `ENTRYPOINT colmap` в интерактивной работе не нужен, вы просто получаете shell и зовёте `colmap ...` руками. Если образ хитрит с sshd (не root) — известный баг с обходом ([vast-ai/base-image #141](https://github.com/vast-ai/base-image/issues/141)), к colmap не относится.
- **Entrypoint-режим**: образ запускается как есть, ssh не даётся; аргументы entrypoint'у — через `--args`.
- **`args` runtype (API)**: ENTRYPOINT сохраняется, `args_str` подставляется как его аргументы ([creating-instances-with-api](https://docs.vast.ai/api-reference/creating-instances-with-api)).

Практика для официальных образов (pytorch/pytorch, colmap/colmap): арендовать в SSH-режиме, работу запускать руками или из onstart. Совет из доков: если SSH-режим на кастомном образе даёт «obscure loading errors» — откатиться на Entrypoint-режим.

```bash
vastai create instance <OFFER_ID> --image colmap/colmap:latest --disk 60 --ssh --direct \
  --onstart-cmd 'export LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH; env >> /etc/environment'
```

(`env >> /etc/environment` — иначе переменные не видны в ssh-сессиях, это документированная особенность.)

## 4. COLMAP: образы и специфика

- **Все датированные теги 2026 года собраны на CUDA 12.9.1** — `ARG NVIDIA_CUDA_VERSION=12.9.1` в [Dockerfile](https://github.com/colmap/colmap/blob/d43f452d/docker/Dockerfile); до середины 2025 был 12.2.2 (тег-«эра» до релиза 3.12, т.е. старый colmap). Несколько тегов на одну дату (`20260626.7140…7144`) — это номера CI-прогонов, не варианты CUDA. **Датированный тег проблему 804 не решает.**
- **Формат sparse-моделей 3.11↔3.12 — не проблема**: `rigs.bin`/`frames.bin` опциональны, чтение полностью совместимо в обе стороны, при их отсутствии инициализируются тривиальные rig/frame ([release notes 3.12.0](https://github.com/colmap/colmap/releases/tag/3.12.0), [format docs](https://colmap.github.io/format.html)).
- **Мульти-GPU**: `--PatchMatchStereo.gpu_index=0,1,...` — patch_match параллелится по картам; VRAM регулируется `--PatchMatchStereo.max_image_size` (главный потребитель — разрешение кадров).
- **Ловушка Blackwell**: на sm_100+ (RTX 5090 и т.п.) nvcc мискомпилирует ядро SweepFromTopToBottom; свежие сборки обходят это через sm_90 PTX ([комментарий в исходнике](https://github.com/colmap/colmap/blob/main/src/colmap/mvs/patch_match_cuda.cu), issue #3514). На 4090 (sm_89) не касается — ещё один аргумент фильтровать именно 4090.
- **«Свой образ» из apt — не вариант**: пакеты colmap в дистрибутивах собраны **без CUDA** ([официальная документация](https://colmap.github.io/install.html)), dense-этап не заработает. Реалистичный свой образ — пересборка официального Dockerfile с `--build-arg NVIDIA_CUDA_VERSION=12.6.3 --build-arg CUDA_ARCHITECTURES=89` (Dockerfile это поддерживает). Но: сборка из исходников ~30–60 мин на x86, на вашем Mac (arm64) — через эмуляцию в разы дольше; хостинг публичного образа на Docker Hub бесплатен, однако публикация артефактов наружу сейчас под запретом режима приватности — пришлось бы собирать прямо на арендованном инстансе (это ещё и платное время).

### Рекомендация по образу

| Вариант | Плюсы | Минусы |
|---|---|---|
| `colmap/colmap:latest` + фильтр `cuda_vers>=12.9` | Ноль работы, свежий colmap 3.13-dev, SASS под 4090 уже в бинаре | Меньше офферов (нужен драйвер ≥575) |
| `latest` + нейтрализация compat в onstart | Работает и на драйверах 525–575, максимум офферов | Одна лишняя строка в onstart; полагаемся на minor-version compat |
| Датированный тег | — | Ничего не решает: та же CUDA 12.9.1 |
| Свой образ на CUDA 12.6 | Нет зависимости от compat | Часы сборки, платное время либо конфликт с режимом приватности, поддержка на себе |

**Вердикт**: `colmap/colmap:latest`, фильтр `cuda_vers>=12.9` как основной отсекатель, плюс compat-нейтрализатор в onstart как страховка (он безвреден на новых драйверах). Свой образ не собирать.

## 5. Однострочный смоук GPU (первая минута после ssh)

`nvidia-smi` проверяет только NVML — CUDA-ядро он не исполняет. Реальный смоук — `feature_extractor` с GPU: он вызывает `cudaGetDeviceCount` (точка падения 804) и гоняет SIFT-ядра за секунды. Заранее положить 2 маленьких кадра (или сгенерировать):

```bash
nvidia-smi --query-gpu=name,driver_version,compute_cap,memory.total --format=csv \
&& ldconfig -p | grep libcuda \
&& mkdir -p /tmp/smk/img && cp /workspace/frames/*.jpg /tmp/smk/img/ \
&& colmap feature_extractor --database_path /tmp/smk/db.db --image_path /tmp/smk/img \
     --SiftExtraction.use_gpu 1 --SiftExtraction.gpu_index 0 \
&& echo '=== GPU_OK ==='
```

Чек-лист чтения вывода: драйвер ≥575 (или compat нейтрализован), `compute_cap 8.9`, `libcuda` резолвится **не** из `/usr/local/cuda/compat/`, `GPU_OK` напечатан. Любое падение до `GPU_OK` — диагностировать по каталогу из раздела 2; если причина на стороне хоста (NVML mismatch, no devices) — не чинить, а report machine + destroy + другой оффер (минуты аренды дешевле часа отладки чужого хоста). Для полной уверенности перед большим прогоном — тот же мини-датасет прогнать через `patch_match_stereo` на 2 кадрах (ещё ~1–2 минуты), это исполняет ровно то ядро, которое будет молотить часами.