# Метеоданные без снимков: реанализы, спутниковые осадки, NWP, горные прогнозы, API

Дата проверки: 2026‑08‑27. Точка Camp2: 39.476 N, 73.592 E, elevation=5529 м. Пометки: **[проверено]** — я реально дёрнул API/страницу сегодня; **[не проверено]** — из поисковой выдачи/по памяти, надо перепроверить.

## 0. Главное (TL;DR)

1. **Open‑Meteo — единственный источник, который закрывает и прогноз, и «что выпало», и ансамбль, одним бесплатным JSON‑API с поправкой на высоту (`elevation=5529`).** Все ключевые запросы для Camp2 проверены и работают (см. §7). Бесплатно до 10 000 вызовов/сут для некоммерческого использования — на бюллетень нужно ~5–20 вызовов/сут.
2. **Для «выпало за сутки» брать не ERA5, а архив оперативного прогноза ECMWF IFS 9 км** (`historical-forecast-api` / `archive` без `models` / `forecast?past_days=N` с `models=ecmwf_ifs`). Причина **[проверено]**: у Open‑Meteo ERA5 на 27.08 заканчивается **12.08** (лаг 15 дней, а не заявленные 5), ERA5‑Land вообще отдаёт `null` по осадкам (и для Оша тоже — т.е. осадков ERA5‑Land в Open‑Meteo сейчас нет). Дефолтный `archive` без `models` бесшовно подставляет IFS 9 км после конца ERA5 — ряд непрерывен до вчерашнего дня.
3. **Осадки в любой из этих моделей на высоте 5500 м — это оценка с ошибкой в разы** (ячейка 9–25 км, средняя высота ячейки ~4–4.5 км, а не 5.5). Сумма — ориентир, детекция событий (было/не было, «сильный/слабый») — надёжнее. Литература по Памиру/Тянь‑Шаню: ERA5 на 5000 м занижает снегопады примерно в 1.4–1.8 раза (медиана/среднее), MERRA‑2 — в 1.2–1.5, при этом ERA5 в сумме по бассейнам Высокой Азии часто **завышает** (мокрый биас в долинах). Это не противоречие — вертикальный градиент в ERA5 в 2.5 раза слабее реального (174 vs 451 мм/км).
4. **Проверка снегопада 14.08** **[проверено]**: архивные прогнозы на Camp2 за 14.08 дают всего ECMWF 0.6 мм / 0.4 см, GFS 2.3 мм / 1.2 см, ICON 5.2 мм / 0.4 см; ERA5 за 14.08 ещё недоступен. То есть модели видели лишь слабый снег — либо событие было локальным/конвективным (типично для летнего Памира, ячейка 9 км его «размазывает»), либо облёт сорвала облачность/видимость, а не количество. Вывод: бюллетень должен показывать **и осадки, и облачность/видимость**, и вести журнал «факт vs модель» для калибровки.
5. **Спутниковые оценки (IMERG/GSMaP/CHIRPS/MSWEP) для твёрдых осадков на 5000+ м бесполезны или почти бесполезны** — NASA прямо пишет в документации IMERG V07: «Snowfall amounts are deficient… IMERG is likely not a good source for estimating snowpack». Их место — только как независимая проверка «было ли событие вообще» по сектору.
6. **Готовые горные прогнозы**: mountain‑forecast.com уже имеет страницу **Gora Kurumdy** (39.45 N, 73.57 E, уровни 4000/5000/6000/6614 м, снег в см по периодам, freezing level) и Pik Lenin (3500/4500/5500/6500/7134). Бесплатно 6–7 дней, подписка £24.99/год **[не проверено, из обзоров]**. Это лучший «человеческий» интерфейс для штаба, но без API. meteoblue point+ €10/мес с «Meteogram Snow» по высотам.

---

## 1. Реанализы

### ERA5 (ECMWF, через CDS и через Open‑Meteo)
- **Что даёт**: total precipitation, snowfall (`sf`, в м в.э.), snow depth, 2m temp, ветер на уровнях давления (500 гПа ≈ 5.5 км), геопотенциал, freezing level рассчитывается сам.
- **Разрешение**: 0.25° (~28×21 км на 39° с.ш.) / часовое. Ячейка накрывает и Курумды 6.1 км, и долину ~3.5 км → средняя высота ячейки порядка 4–4.5 км **[не проверено]**. Для 5500 м это экстраполяция.
- **Латентность**: ERA5T — «about 5 days behind real time» на CDS ([ERA5 data documentation](https://confluence.ecmwf.int/display/CKB/ERA5%3A+data+documentation)). **[проверено]** В Open‑Meteo на 27.08 последний день с данными — 12.08 (15 дней), т.е. «сегодня» им не пользоваться.
- **Горизонт**: только прошлое.
- **Стоимость**: бесплатно. CDS требует аккаунт + принятие лицензии; Open‑Meteo — без ключа.
- **Как получить**:
  - CDS: dataset `reanalysis-era5-single-levels`, переменные `total_precipitation`, `snowfall`, `snow_depth`, `2m_temperature`; cdsapi‑скрипт (см. [How to download ERA5](https://confluence.ecmwf.int/display/CKB/How+to+download+ERA5)). Удобнее — новый time‑series датасет для точки: [reanalysis-era5-single-levels-timeseries](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels-timeseries?tab=overview).
  - Open‑Meteo `archive` с `models=era5` (URL в §7).
- **Точность в горах**: см. §6. Кратко: снегопады на 5000 м занижены ~×1.4–1.8 (Frontiers 2019), суточная детекция слабая (ERA5 snowfall лучше на месячном масштабе, [Wang 2025, IJC](https://rmets.onlinelibrary.wiley.com/doi/10.1002/joc.8926)); ERA5 snow depth выше ~1500 м «нереалистично велик» ([ERA5‑Land paper, ESSD 2021](https://essd.copernicus.org/articles/13/4349/2021/)).
- **Пригодность**: **2/5** для текущего мониторинга (лаг), **4/5** для ретроспективы/климатологии («какой сентябрь–октябрь обычно»).

### ERA5‑Land
- **Что даёт**: 9 км (0.1°), snow depth, SWE, snowfall, температура — но **без собственной ассимиляции**, это ERA5, прогнанный через land‑модель с лапс‑коррекцией.
- **Латентность**: ERA5‑Land‑T — те же 5 дней ([C3S launches ERA5‑Land‑T](https://climate.copernicus.eu/c3s-launches-new-era5-land-t-service)). **[проверено]** В Open‑Meteo `models=era5_land` отдаёт температуру, но `precipitation_sum`/`snowfall_sum` = null (и для Camp2, и для Оша, и за июль) — осадков ERA5‑Land там сейчас нет.
- **Ловушка snow depth** **[проверено + документация]**: для Camp2 ERA5‑Land вернул `snow_depth` = **20.42 м** константой. Это артефакт: ледниковым ячейкам (>50 % льда) присваивается фиксированный SWE 10 м ([ESSD 2021](https://essd.copernicus.org/articles/13/4349/2021/)). **Snow depth ERA5/ERA5‑Land над Курумды использовать нельзя.** Прирост снега считать только по snowfall.
- **Пригодность**: **1/5** для этой точки.

### MERRA‑2 (NASA GMAO)
- 0.5°×0.625° (~55 км), часовое, латентность **~3 недели после конца месяца** ([GES DISC M2T1NXFLX](https://www.earthdata.nasa.gov/data/catalog/ges-disc-m2t1nxflx-5.12.4)). Есть `PRECSNO`. По HMA занижает снегопад в среднем ~×1.5 (медиана ×1.2), вертикальный градиент 252 мм/км против 451 реального ([Frontiers 2019](https://www.frontiersin.org/journals/earth-science/articles/10.3389/feart.2019.00280/full)). **Пригодность 1/5** (лаг + грубая сетка).

### HAR v2 (High Asia Refined analysis, TU Berlin)
- WRF 10 км, форсинг ERA5, ежедневная реинициализация, есть `prcp`, snowfall (grid scale snow and ice), `snowh`. Покрытие 1979–2025, «будет обновляться», **near‑real‑time продукта нет** **[проверено по странице TU Berlin]**. Скачивание без регистрации по WebDAV: https://data.klima.tu-berlin.de/HAR/ ([HAR page](https://www.tu.berlin/en/klima/research/regional-climatology/high-asia/har)). **Пригодность 1/5** для мониторинга, **4/5** для климатологии сентября–октября на 10 км (лучше ERA5).

### HMASR (High Mountain Asia UCLA Daily Snow Reanalysis)
- ~500 м, суточный SWE/snow depth с ассимиляцией Landsat/MODIS fSCA, но только **WY 2000–2017** ([NSIDC HMA_SR_D](https://nsidc.org/data/hma_sr_d/versions/1)). Мониторингу не подходит (**0/5**), но это эталон, по которому получены биасы ERA5/MERRA‑2 выше.

### CARRA / CERRA
- CARRA — Арктика, не покрывает. CERRA — Европа 5 км, до 06.2021. Для Памира ничего нет. Из региональных: только HAR v2 (см. выше) и «ECMWF IFS 9 км архив» в Open‑Meteo (с 2017 г., это архив прогнозов, не реанализ, но фактически лучший непрерывный ряд на 9 км с латентностью 0 — см. §3).

---

## 2. Спутниковые оценки осадков

### GPM IMERG V07 (Early / Late / Final)
- 0.1° / 30 мин. Латентность: Early ~4 ч, Late ~12–14 ч, Final ~3.5 мес ([gpm.nasa.gov/data/imerg](https://gpm.nasa.gov/data/imerg)).
- **Твёрдые осадки**: официально: «Snowfall amounts are deficient in Version 07, precipitation in general is less certain in mountainous terrain… IMERG is likely not a good source for estimating snowpack». Пассивная микроволна над снежной/ледовой подстилающей поверхностью «не видит» снег (холодная поверхность ≈ сигнал снегопада), ИК видит только холодные вершины облаков. По обзорам IMERG «snowfall, shallow and light precipitation events cannot be well detected» ([Review of IMERG performance](https://www.sciencedirect.com/science/article/abs/pii/S0034425721004740)).
- **Как получить**: GeoTIFF Early: https://jsimpsonhttps.pps.eosdis.nasa.gov/imerg/gis/early/ (регистрация PPS), Giovanni для быстрых карт/рядов: https://giovanni.gsfc.nasa.gov/ (Earthdata login), GES DISC OPeNDAP. Бесплатно.
- **Пригодность**: **1/5** для количества, **2/5** как «был ли фронт над сектором» (Late run на утро следующего дня).

### GSMaP (JAXA)
- 0.1° / 1 ч, 60N–60S, NRT ~4 ч **[не проверено]**, регистрация на [JAXA Global Rainfall Watch](https://sharaku.eorc.jaxa.jp/GSMaP/registration.html). По выдаче: обновления **v7 NRT прекращены 29.06.2026** (переход на новую версию) **[не проверено]**. Снег — та же проблема, что у IMERG. **1/5**.

### CHIRPS v3
- 0.05°, пентады/сутки, prelim через 2 дня после пентады ([CHC](https://chc.ucsb.edu/data/chirps3)). Это **дождевой** ИК‑продукт с калибровкой по станциям (которых на 5000 м нет); снег не целевой. **0–1/5**.

### MSWEP V3 / MSWEP‑NRT (GloH2O)
- 0.1° / часовое, 1979–наст., NRT «<2 ч» (смесь GDAS, IMERG, GSMaP, PDIR‑Now) ([gloh2o.org/mswep](https://www.gloh2o.org/mswep/), [arXiv 2602.01436](https://arxiv.org/html/2602.01436v1)). Некоммерческий доступ CC BY‑NC по заявке, скачивание только rclone с Google Drive; API/FTP — коммерческим. По Тянь‑Шаню MSWEP оценивается лучше ERA5‑Land/CHIRPS/PERSIANN ([Tianshan 2026](https://www.sciencedirect.com/science/article/pii/S2214581826004519)). Но на 5500 м это всё равно смесь тех же ERA5/GDAS + спутников. **2/5**, и заявка/rclone — лишняя возня для «за 1 день».

### PERSIANN‑CCS/PDIR‑Now
- ИК‑only, 0.04°, латентность ~1 ч, точность худшая из всех; в горах зимой систематически плох. **0–1/5**.

---

## 3. Численные прогнозы (открытые)

### ECMWF IFS open data (напрямую)
- С 01.02.2024 — 0.25°, с 01.10.2025 весь Real‑time Catalogue открыт CC‑BY‑4.0; **«later in 2026» бесплатный сабсет расширят до 9 км с 2‑часовой латентностью** ([ECMWF news 2025](https://www.ecmwf.int/en/about/media-centre/news/2025/ecmwf-makes-its-entire-real-time-catalogue-open-all)). Сейчас 0.25°: 00/12z до 144 ч по 3 ч, далее до 360 ч по 6 ч; 06/18z до 144 ч. Параметры включают `tp`, **`sf`**, `2t`, `10u/10v`, уровни 1000…10 гПа; AIFS single и AIFS‑ENS тоже в open data ([datasets/open-data](https://www.ecmwf.int/en/forecasts/datasets/open-data)). Хранятся последние 12 прогонов.
- Доступ: https://data.ecmwf.int/forecasts, AWS/Azure зеркала, `pip install ecmwf-opendata` ([Confluence](https://confluence.ecmwf.int/pages/viewpage.action?pageId=272310539)). Латентность open‑data ~«2‑hour delay» по описанию Open‑Meteo.
- **Смысл для задачи**: прямой GRIB нужен, только если хочется своих карт/сечений. Для точки — Open‑Meteo то же самое без GRIB. **3/5**.

### Open‑Meteo Forecast API (агрегатор — основной кандидат)
- **Модели** **[проверено по докам]**: `ecmwf_ifs` (IFS HRES **9 км**, 1‑часовой, 15 дней, обновление 6 ч, без задержки с 01.10.2025), `ecmwf_ifs025` (0.25°, 3‑часовой), `ecmwf_aifs025_single` (AIFS, 6‑ч шаг), `gfs_seamless`/`gfs_global` (0.25°→0.11°, 16 дней, обновление каждый час), `icon_global` (0.1°/11 км, 7.5 дня), `meteofrance_arpege_world` (0.25°, 4 дня), `ukmo_global_deterministic_10km` (10 км, 7 дней), `gem_global`, `jma_gsm`, `bom_access_global`, `kma_gdps`, `cma_grapes_global`… ([docs](https://open-meteo.com/en/docs), [ECMWF API](https://open-meteo.com/en/docs/ecmwf-api)).
- **Переменные**: `temperature_2m`, `precipitation` (мм), `snowfall` (см), `snow_depth` (м), `freezing_level_height` (м), `wind_speed_10m`, `cloud_cover(_low/mid/high)`, `visibility` (не у всех), `temperature_500hPa`/`geopotential_height_500hPa`, `weather_code`. Daily: `snowfall_sum`, `precipitation_sum`, `temperature_2m_max/min`.
- **`elevation=5529`** **[проверено]**: параметр работает; ответ возвращает `elevation: 5529.0`. Без параметра Open‑Meteo берёт 90‑м DEM и даёт 5463 м. Коррекция — статистический даунскейлинг температуры по лапс‑рейту; осадки/снег **не** масштабируются по высоте (по докам «statistical downscaling», механизм для осадков не описан — **[не проверено]**, считать, что осадки = ячейка).
- **Важные наблюдения по Camp2** **[проверено 27.08]**:
  - Температура в один и тот же час на 5529 м: ECMWF −10.7…−11.3, AIFS −10.1, ARPEGE −12.1, UKMO −6.8, GFS −4.6, ICON −5.1 °C. Разброс 7 K — следствие разной высоты исходных ячеек и разной лапс‑коррекции. ECMWF 9 км ближе к реальности для 5500 м (стандартная атмосфера +ветер).
  - `freezing_level_height` = **null** для `ecmwf_ifs`, `ecmwf_ifs025`, AIFS, UKMO, ARPEGE; есть у **GFS и ICON** (4650–4720 м сегодня). Значит нулевую изотерму брать из GFS/ICON или считать из `temperature_2m` + 6.5 К/км, либо из `temperature_500hPa` (`ecmwf_ifs025`, `gfs_global`, `icon_global`).
  - Соотношение `snowfall`/`precipitation` у Open‑Meteo **7:1** (0.7 см на 1 мм в.э.; подтверждено issue [#900](https://github.com/open-meteo/open-meteo/issues/900)) — это заниженное «уплотнённое» отношение; для −10…−20 °C реальный свежий снег 10:1–20:1 (см. §5).
- **Латентность**: 0 (текущий прогон). Для «выпало вчера» — `past_days=N` (до 92) даёт склейку самых свежих прогонов (lead 0–6 ч) — фактически «анализ+кратчайший прогноз».
- **Лимиты** **[проверено по странице pricing]**: бесплатно некоммерчески 600/мин, 5000/ч, 10 000/сут, 300 000/мес; запрос с ≥10 переменными или >2 недель считается >1 вызова. Платно: Standard 1 M вызовов/мес, Professional 5 M (Historical/Ensemble/Climate только с Professional — **это касается коммерческого ключа**; на бесплатном тарифе Ensemble/Historical доступны). Цены по сторонним обзорам $29/$99 в мес **[не проверено]**. Поисково‑спасательная операция — некоммерческое использование.
- **Пригодность**: **5/5**.

### Open‑Meteo Historical Forecast API / Previous Runs / Archive‑fallback
- `historical-forecast-api.open-meteo.com/v1/forecast` — архив оперативных прогнозов: ECMWF IFS 9 км **с 2017**, IFS 0.25 с 02.2024, AIFS с 02.2025, GFS с 03.2021, ICON с 11.2022, UKMO с 03.2022, ARPEGE с 01.2024 ([docs](https://open-meteo.com/en/docs/historical-forecast-api)). Это ряд «что модель считала на момент события» — именно им закрывается лаг ERA5. **[проверено]** для Camp2 за 11–25.08 все три модели отдали полные суточные суммы; 21–23.08 — заметный снегопад (ECMWF 0.8+4.3+2.9 см при 1.1+6.1+4.0 мм; ICON 10.6+4.2+5.2 см при 20.9+14.3+10.1 мм).
- `previous-runs-api.open-meteo.com` — та же переменная с фиксированным упреждением (`_previous_day1…7`): удобно для оценки «насколько прогноз на 3 дня врёт по снегу» ([docs](https://open-meteo.com/en/docs/previous-runs-api)).
- **[проверено]** `archive-api` **без** `models` для 10–26.08 отдаёт непрерывный ряд: до 12.08 — ERA5, дальше — IFS 9 км (значения 13–26.08 совпали с `ecmwf_ifs`). Но обратите внимание на стык: ERA5 на 10–12.08 даёт 3.5/5.0/4.8 мм при snowfall ~0 (T_mean −2.5 °C → классифицировано как дождь на 5529 м!), IFS 9 км — 2.5/4.5/3.4 мм при 1.4/2.8/2.0 см снега и T_mean −5 °C. Т.е. в одном ряду будут два разных «климата» — для накопленной суммы использовать **только один источник** (IFS 9 км).

### Open‑Meteo Ensemble API
- `ensemble-api.open-meteo.com/v1/ensemble`: `ecmwf_ifs025` 51 член/15 дн, `ecmwf_aifs025` 51, `gfs025` 31/10 дн, `icon_seamless` 40/7.5 дн, `gem_global` 21/16 дн, `bom_access_global_ensemble` 18, **Google WeatherNext 2** 64/15 дн; переменные snowfall, precipitation, snow_depth, temperature_2m, freezing_level_height; `elevation` поддерживается ([docs](https://open-meteo.com/en/docs/ensemble-api)). **[проверено]** для Camp2 с `ecmwf_ifs025` вернулось 50 членов; на 02.09 медиана 0.56 см, максимум 3.6 см — т.е. «вероятность снегопада ≥2 см ≈ 15 %» считается прямо из членов. **5/5** для оценки неопределённости.

### GFS/GEFS, ICON, ARPEGE, UKMO напрямую
- NOMADS/AWS GRIB — только если нужны поля. Через Open‑Meteo всё то же по точке. ICON global 13 км (у Open‑Meteo 0.1°), ARPEGE world 0.25°, UKMO global 10 км. Отдельная польза ICON: в летнем Памире он давал самые большие суммы (21.08: 20.9 мм) — полезен как «верхняя оценка».

### yr.no / MET Norway Locationforecast 2.0
- Бесплатно, JSON, вне Скандинавии — **ECMWF HRES** с интерполяцией и адиабатической поправкой температуры по параметру `altitude` (только температура) ([FAQ](https://docs.api.met.no/doc/locationforecast/FAQ.html)). Осадки есть, снег отдельной переменной нет (только symbol). Требует `User-Agent` с контактом. Запрос: `https://api.met.no/weatherapi/locationforecast/2.0/complete?lat=39.476&lon=73.592&altitude=5529`. Дублирует Open‑Meteo `ecmwf_ifs`, но без snowfall. **3/5** (запасной источник ECMWF 9 км, если Open‑Meteo ляжет).

---

## 4. Готовые горные прогнозы

| Сервис | Что есть для нас | Цена | Оценка |
|---|---|---|---|
| **mountain‑forecast.com** — [Gora Kurumdy](https://www.mountain-forecast.com/peaks/Gora-Kurumdy/forecasts/6614) **[проверено]** | Уровни **4000 / 5000 / 6000 / 6614 м**; на 3 периода в сутки: снег (см), дождь (мм), freezing level, T max/min, ветер, wind chill, облачность; ~6–7 дней бесплатно. [Pik Lenin](https://www.mountain-forecast.com/peaks/Pik-Lenin/forecasts/5500): 3500/4500/5500/6500/7134. Модель не раскрывается (по опыту — собственный даунскейлинг GFS, **[не проверено]**). | Бесплатно; подписка £24.99/год или £3.99/мес — почасовые 6 дн + 3‑часовые ещё 10 дн **[не проверено, из обзора TGO]** | **4/5** как человеко‑читаемая страница для штаба; API нет, парсить HTML можно, но хрупко |
| **snow‑forecast.com** | Тот же движок, ориентирован на курорты; для Курумды ничего сверх mountain‑forecast | — | 2/5 |
| **meteoblue** — [Lenin Peak](https://www.meteoblue.com/en/weather/week/lenin-peak_kyrgyzstan_1161936); любая точка по координатам | point+: 14‑дневная метеограмма, **Meteogram Snow** (температура по высотам, снег, высота снега), multimodel; архив с 1985 ([pointplus](https://www.meteoblue.com/en/pointplus)) **[проверено]** | **€10 / мес, €20 / 3 мес, €30 / 6 мес, €50 / год**, 14 дней бесплатно | **4/5** для визуальной сверки; их API — отдельно, платный, по запросу |
| **Windy.com** | ECMWF 9 км (1‑час шаг в Premium), ICON 13 км, GFS 22 км, метеограмма по точке, слои «new snow», «freezing altitude»; **[не проверено]** высотной коррекции точки нет | Premium ~€22.99/год (автопродление) или €34.99 разово **[не проверено, из форума]** | 3/5 — карта для «откуда идёт фронт», не для цифр |
| **yr.no** | ECMWF HRES с поправкой на altitude, бесплатно, есть API (см. §3) | 0 | 3/5 |
| **Meteoexploration ModelPeak** ([Pik Lenina](https://meteoexploration.com/en/forecasts/Pik-Lenina/)) | Собственный WRF 1–5 дн + GFS 6–8 дн, Гималаи/Памир. Страница не открылась (ошибка сертификата) **[не проверено]** | неизвестно | 2/5 — есть Pik Lenina, Курумды нет |
| **Ventusky** | Карты ICON/GFS/ECMWF, Premium‑слои облачность/ветер по 16 высотам | ? | 2/5 |
| **OpenSnow** | Только США/Канада/Европа/Япония/Австралия‑НЗ | — | 0/5 |
| MWIS и аналоги | Только UK/Альпы | — | 0/5 |

---

## 5. Как собрать ежедневный бюллетень «выпало X мм / Y см на 5500 м, накоплено Z см с 15.08»

**Переменные (все из Open‑Meteo, `elevation=5529`):**
- `precipitation_sum` (мм в.э.) и `snowfall_sum` (см) — из `models=ecmwf_ifs` (9 км) как **основной**, `gfs_seamless` и `icon_global` — как вилка.
- `temperature_2m_min/max`, `wind_speed_10m_max`, `cloud_cover` (daily mean через hourly), `freezing_level_height` — из `icon_global`/`gfs_global` (у ECMWF null).
- «Прошлое» — `past_days=1..14` того же forecast‑эндпоинта (**самая свежая склейка прогонов**), или `historical-forecast-api` для длинных периодов (с 15.08).
- «Будущее» — `forecast_days=7` детерминистика + `ensemble` `ecmwf_ifs025` → P(snow ≥ 1 см), P(≥5 см), медиана/90‑й перцентиль.

**Масштабирование по высоте (честно: калибровки нет, это допущения):**
1. **Осадки ячейки → 5500 м.** Модель считает осадки для средней высоты ячейки (~4–4.5 км). Литература по HMA даёт для ERA5 на 5000 м занижение ×1.4–1.8 (Frontiers 2019: mean 1.78/median 1.42 для ERA5; ~×1.2–1.5 для MERRA‑2; «<2» для западного домена Тянь‑Шань/Памир). Для IFS 9 км биас меньше, но не измерен. Рекомендация: показывать **сырое значение** и «оценку ×1.5» как верхнюю границу, не более. Не пытаться применять фиксированный вертикальный градиент осадков: на Федченко и Абрамова аккумуляция зависит от положения относительно западного края бассейна сильнее, чем от высоты ([Fedchenko accumulation, J. Glaciol.](https://www.cambridge.org/core/journals/journal-of-glaciology/article/high-altitude-accumulation-and-preserved-climate-information-in-the-western-pamir-observations-from-the-fedchenko-glacier-accumulation-basin/961DB0039DA77185FF8C55960D1EC1A1)).
2. **Фаза.** На 5500 м в сентябре–октябре при T_max < 0 всё — снег; брать `precipitation_sum` как SWE целиком, а `snowfall_sum` Open‑Meteo (7:1) игнорировать либо пересчитать.
3. **SWE → толщина свежего снега.** Плотность свежего снега при −10…−20 °C ≈ 50–100 кг/м³ (отношение 10:1–20:1; «dendritic growth zone» −12…−18 °C даёт самый пушистый снег) ([UBC](https://www.eoas.ubc.ca/courses/atsc113/snow/met_concepts/07-met_concepts/07b-newly-fallen-snow-density/), [Judson & Doesken, BAMS](https://climate.colostate.edu/pdfs/Judson_Doesken_DensityFreshFallenSnow_BAMS.pdf)). При ветре >10 м/с плотность растёт до 150–200 кг/м³ и снег переносится. Практическая формула для бюллетеня: **H_свеж [см] ≈ SWE [мм] × (0.10 при T ≥ −5, 0.13 при −5…−12, 0.17 при < −12 °C)**, с оговоркой «ветровой перенос ±50 %».
4. **Накопление с 15.08.** Σ SWE (мм) по дням — это надёжнее, чем Σ см: свежий снег оседает за 1–3 дня до 150–250 кг/м³, сублимирует на солнце/ветре. Показывать «накоплено ~Z мм в.э. ≈ Z×0.4…0.6 см осевшего снега» **[допущение]**. Дополнительно — число дней с SWE ≥ 3 мм («заметный снегопад»).
5. **Не использовать `snow_depth`** ни из ERA5‑Land (20 м‑артефакт), ни из NWP (там снег ячейки, для ледника бессмысленно).

**Контроль:** вести таблицу «дата — факт от штаба/дрона (снег да/нет, видимость) — ECMWF/GFS/ICON» и через 2–3 недели выбрать модель и коэффициент. Проверка по 21–23.08: если штаб/пилоты подтвердят снегопад 21–23.08 (модели: 6–11 мм / ECMWF, 20–45 мм / ICON), это уже первая калибровочная точка.

---

## 6. Валидация: что литература говорит о Памиро‑Алае / Тянь‑Шане / Каракоруме

- **[Frontiers in Earth Science 2019, MERRA‑2 snowfall bias over HMA](https://www.frontiersin.org/journals/earth-science/articles/10.3389/feart.2019.00280/full)** (эталон — HMASR): MERRA‑2 занижает снегопад в среднем ×1.54 (медиана 1.19), **ERA5 ×1.78 (медиана 1.42)**, APHRODITE ×3.3. Западный домен (Тянь‑Шань, Памир, Каракорум) — «bias factors < 2». Вертикальный градиент снегопада: реанализ снега 451 мм/км, MERRA‑2 252, **ERA5 174**, APHRODITE 91. В тянь‑шаньском тайле на 5000 м: 1300 мм по HMASR против ~500 у MERRA‑2. ⇒ Именно вертикальный градиент, а не общая сумма, — главная ошибка на 5500 м.
- **[General overestimation of ERA5 precipitation in HMA basins, ERC 2021](https://iopscience.iop.org/article/10.1088/2515-7620/ac40f0)**: сток по ERA5 завышен на 33–70 % в западных (westerlies) бассейнах — ERA5 **мокрый** в сумме по бассейну. Вместе с предыдущим пунктом: ERA5 «размазывает» осадки по ячейке — лишнее внизу, недостача наверху.
- **[HESS 2024, downscaling ERA5 over HMA](https://hess.copernicus.org/articles/28/4903/2024/)**: только 3 из 46 станций выше 2000 м при медианной высоте водосборов ~4700 м; ERA5 «относительно точен по суммам/сезонности», но на высоте не верифицирован.
- **[Immerzeel et al. 2015, HESS, верховья Инда](https://hess.copernicus.org/articles/19/4673/2015/hess-19-4673-2015.pdf)**: высокогорные осадки по ледниковому балансу в 2–10 раз выше сеточных продуктов; корреляция с долинными станциями низкая.
- **[Evaluation of the ERA5 snowfall product in China, IJC 2025](https://rmets.onlinelibrary.wiley.com/doi/10.1002/joc.8926)** (2145 станций 1980–2023): хорош на месячном масштабе, **низкая точность суточной детекции снегопада**.
- **[Precipitation types in Chinese Tianshan, J. Mt. Sci. 2024](https://link.springer.com/article/10.1007/s11629-024-9258-8)**: холодный биас ERA5 в горах ведёт к ошибочной классификации снег/дождь; на Camp2 это видно и в нашем запросе (ERA5 на 10–12.08: 13 мм осадков, 0 см снега при T −2.5 °C — на 5529 м это неправдоподобно).
- **IMERG**: [GPM documentation](https://gpm.nasa.gov/data/imerg) — «snowfall amounts are deficient… not a good source for snowpack»; обзор [Pradhan et al. 2022](https://www.sciencedirect.com/science/article/abs/pii/S0034425721004740) — не детектирует снег и лёгкие осадки. Над снежной подстилающей поверхностью PMW‑алгоритмы вообще маскируются.
- **MSWEP** над Тянь‑Шанем лучше ERA5‑Land/CHIRPS/PERSIANN ([2026](https://www.sciencedirect.com/science/article/pii/S2214581826004519)) — но станции всё те же долинные.
- **Событие 14.08** **[проверено]**: архив прогнозов на Camp2 показал 0.6 мм (ECMWF), 2.3 мм (GFS), 5.2 мм (ICON). ERA5 на 14.08 в Open‑Meteo ещё нет (доступно до 12.08) — проверить через CDS через неделю. Предварительно: модели событие занизили или пропустили → штабу нужен порог тревоги не по «мм», а по «≥ 0.5 мм у любой из 3 моделей ИЛИ низкая облачность ≥ 70 %».

---

## 7. Конкретные URL для Camp2 (39.476, 73.592, elevation=5529) — все проверены 27.08

**Прогноз 7 дней, 3 модели, часовые + суточные:**
```
https://api.open-meteo.com/v1/forecast?latitude=39.476&longitude=73.592&elevation=5529&hourly=temperature_2m,precipitation,snowfall,snow_depth,freezing_level_height,wind_speed_10m,cloud_cover,cloud_cover_low&daily=snowfall_sum,precipitation_sum,temperature_2m_max,temperature_2m_min,wind_speed_10m_max&models=ecmwf_ifs,gfs_seamless,icon_global&timezone=Asia/Bishkek&forecast_days=7
```
(ответ: `elevation: 5529.0`; freezing_level_height у `ecmwf_ifs` = null, у GFS/ICON есть; T сегодня 00:00: ECMWF −10.7, GFS −4.6, ICON −5.1 °C.)

**«Что выпало» за последние 14 дней + сегодня (самые свежие прогоны ECMWF 9 км):**
```
https://api.open-meteo.com/v1/forecast?latitude=39.476&longitude=73.592&elevation=5529&daily=snowfall_sum,precipitation_sum,temperature_2m_min,temperature_2m_max&models=ecmwf_ifs&past_days=14&forecast_days=1&timezone=Asia/Bishkek
```
(результат 14–27.08: 8.7 см / ~13 мм по Open‑Meteo, пик 22–23.08.)

**Длинный ряд с 15.08 (архив прогнозов, 9 км, с 2017 г.):**
```
https://historical-forecast-api.open-meteo.com/v1/forecast?latitude=39.476&longitude=73.592&elevation=5529&start_date=2026-08-15&end_date=2026-08-26&daily=snowfall_sum,precipitation_sum,temperature_2m_mean&models=ecmwf_ifs,gfs_seamless,icon_global&timezone=Asia/Bishkek
```

**ERA5 (реанализ; сегодня доступен только до 12.08):**
```
https://archive-api.open-meteo.com/v1/archive?latitude=39.476&longitude=73.592&elevation=5529&start_date=2026-08-01&end_date=2026-08-26&daily=snowfall_sum,precipitation_sum,temperature_2m_mean&hourly=snow_depth&models=era5&timezone=Asia/Bishkek
```
(`models=era5_land` — осадки null; без `models` — ERA5 до 12.08, дальше IFS 9 км.)

**Ансамбль ECMWF 51 член, 7 дней:**
```
https://ensemble-api.open-meteo.com/v1/ensemble?latitude=39.476&longitude=73.592&elevation=5529&daily=snowfall_sum,precipitation_sum&hourly=temperature_2m&models=ecmwf_ifs025&timezone=Asia/Bishkek&forecast_days=7
```
(вернулось 50 членов `snowfall_sum_member01..50`; 02.09: медиана 0.56 см, max 3.6 см.)

**Климатология сентября–октября (ERA5, 1991–2025) для «нормы»:**
```
https://archive-api.open-meteo.com/v1/archive?latitude=39.476&longitude=73.592&elevation=5529&start_date=1991-09-01&end_date=2025-10-31&daily=precipitation_sum,snowfall_sum,temperature_2m_mean&models=era5&timezone=Asia/Bishkek
```
(>2 недель данных → считается как несколько вызовов; один раз, кешировать.)

**CDS (ERA5T, если нужен официальный реанализ через неделю):**
```python
import cdsapi
c = cdsapi.Client()
c.retrieve("reanalysis-era5-single-levels-timeseries",   # point time-series dataset
  {"variable":["total_precipitation","snowfall","2m_temperature"],
   "location":{"longitude":73.592,"latitude":39.476},
   "date":["2026-08-10/2026-08-26"],"data_format":"csv"}, "camp2_era5.csv")
```
(имена параметров time‑series датасета — **[не проверено]**, сверить с формой на [CDS](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels-timeseries?tab=overview).)

**ECMWF open data напрямую (если захочется поля `sf`):**
```python
from ecmwf.opendata import Client
Client(source="ecmwf").retrieve(type="fc", step=[24,48,72], param=["tp","sf","2t"], target="ifs.grib2")
```

---

## 8. Сводная таблица

| Источник | Даёт | Простр./врем. | Латентность | Горизонт | Цена | Снег на 5500 м | Пригодность |
|---|---|---|---|---|---|---|---|
| **Open‑Meteo Forecast (`ecmwf_ifs` 9 км)** | P, snowfall, T, ветер, облака; `elevation` | 9 км / 1 ч | 0 (+~2 ч от прогона) | 15 дн; `past_days` до 92 | 0 | ячейка ~4–4.5 км, поправка только T | **5** |
| Open‑Meteo Ensemble (`ecmwf_ifs025`, 51 чл.) | P(snow ≥ X) | 25 км / 3 ч | 0 | 15 дн | 0 | как выше | **5** |
| Open‑Meteo GFS / ICON / UKMO / ARPEGE / AIFS | вилка моделей; freezing level (GFS, ICON) | 10–25 км | 0 | 4–16 дн | 0 | ICON — верхняя оценка | 4 |
| Open‑Meteo Historical Forecast (IFS 9 км с 2017) | «что выпало» непрерывно до вчера | 9 км / 1 ч | ~1 сут | прошлое | 0 | как выше | **5** |
| ERA5 (CDS / Open‑Meteo) | P, sf, T, уровни | 25 км / 1 ч | 5 дн (CDS); **15 дн** (Open‑Meteo, факт) | прошлое | 0 | ×1.4–1.8 занижение на 5000 м, snow depth негоден | 2 (мониторинг) / 4 (климатология) |
| ERA5‑Land | T, SWE, snow depth | 9 км / 1 ч | 5 дн | прошлое | 0 | snow depth = 10 м‑артефакт на леднике; осадков в Open‑Meteo нет | 1 |
| HAR v2 | P, snowfall, snowh | 10 км / 1 ч | до 2025, NRT нет | прошлое | 0 | лучший региональный климат‑ряд | 1 / 4 (климатология) |
| HMASR | SWE, depth | 500 м / сут | WY2000–2017 | — | 0 | эталон | 0 |
| MERRA‑2 | PRECSNO | 55 км / 1 ч | ~3 нед | прошлое | 0 | ×1.2–1.5 занижение | 1 |
| IMERG Early/Late | P | 10 км / 30 мин | 4 / 14 ч | прошлое | 0 | «deficient», не для снега | 1–2 (детекция фронта) |
| GSMaP NRT | P | 10 км / 1 ч | ~4 ч | прошлое | 0 (регистрация) | то же; v7 NRT остановлен 06.2026 [?] | 1 |
| CHIRPS | дождь | 5 км / пентада | 2 дн после пентады | прошлое | 0 | не снег | 0–1 |
| MSWEP‑NRT | P (микс) | 10 км / 1 ч | <2 ч | прошлое | 0 по заявке, rclone | смесь тех же источников | 2 |
| ECMWF open data GRIB | tp, sf, уровни | 25 км / 3–6 ч (9 км «позже в 2026») | ~2 ч | 15 дн | 0 | как IFS | 3 |
| yr.no API | ECMWF HRES с altitude | 9 км / 1–6 ч | 0 | 10 дн | 0 | нет snowfall | 3 |
| mountain‑forecast.com Kurumdy | снег см, FL, T, ветер на 4000/5000/6000/6614 | ? | 0 | 6–7 дн (16 платно) | 0 / £25 в год | модель не раскрыта | 4 (для людей) |
| meteoblue point+ | Meteogram Snow по высотам, 14 дн | ~ | 0 | 14 дн | €10 / мес | multimodel | 4 (для людей) |
| Windy Premium | ECMWF 9 км 1‑ч, карты | 9–22 км | 0 | 10 дн | ~€23–35 / год | без поправки на высоту точки | 3 |

---

## 9. Ранжированная рекомендация: что поднять за 1 день

**Ранг 1 — автобюллетень в Telegram на Open‑Meteo (0 ₽, 1 день работы).**
Один Python‑скрипт по cron (или GitHub Actions, но при режиме приватности — локальный cron/сервер тим‑версии), 4 вызова в сутки:
1. `forecast … models=ecmwf_ifs&past_days=1&forecast_days=7` → «за вчера: X мм в.э. (≈Y см свежего при T), сегодня/завтра/3 дня: … , T min/max на 5529, ветер, облачность low».
2. `forecast … models=gfs_seamless,icon_global` → вилка + `freezing_level_height`.
3. `ensemble … models=ecmwf_ifs025` → P(≥1 см), P(≥5 см) на каждый из 7 дней, 90‑й перцентиль.
4. `historical-forecast-api … models=ecmwf_ifs&start_date=2026-08-15` (или локальный накопитель) → «накоплено с 15.08: Z мм в.э. ≈ … см осевшего снега, N дней со снегом ≥3 мм».
Формат сообщения: 6–8 строк, светофор (зелёный <1 мм, жёлтый 1–5, красный >5 мм/сут или ветер >15 м/с), ссылка на mountain‑forecast Kurumdy. Пороги первое время консервативные — задача «не пропустить», ложные тревоги дёшевы.
Дополнительно в тот же скрипт: журнал `date, ecmwf, gfs, icon, fact` в TSV для будущей калибровки.

**Ранг 2 — человеко‑читаемые страницы для штаба (0–€10).** Закрепить в чате ссылки: [Gora Kurumdy 5000 м](https://www.mountain-forecast.com/peaks/Gora-Kurumdy/forecasts/5000) и 6000 м, meteoblue Lenin Peak/точка Camp2 с point+ Meteogram Snow (14 дней бесплатно, потом €10/мес). Это второй, независимый взгляд.

**Ранг 3 — ретроспектива/норма (полдня, разово).** ERA5 через Open‑Meteo archive за 1991–2025 сентябрь–октябрь → «в среднем по ERA5 на этой ячейке N мм/мес в сентябре, M — в октябре, первый «большой» снегопад (>10 мм/сут) в среднем такого‑то числа» с оговоркой ×1.5 занижения. Плюс HAR v2 10 км, если нужно точнее (WebDAV, NetCDF).

**Ранг 4 — недельная проверка ERA5 по 14.08 и 21–23.08** через CDS/Open‑Meteo, когда данные дойдут (~к 01.09 по CDS, позже у Open‑Meteo). Не влияет на бюллетень, но даёт понимание, ловит ли реанализ те события, которые срывают вылеты.

**Не делать:** IMERG/GSMaP/CHIRPS как источник снега; snow depth из ERA5/ERA5‑Land; смешивать ERA5 и IFS в одной накопительной сумме; ждать 9‑км open data ECMWF напрямую — оно уже есть через Open‑Meteo.

**Оговорки к цифрам.** Все абсолютные мм/см с моделей на 5529 м — оценка с ошибкой в ×1.5–2, ансамбль это отражает лишь частично (он про атмосферу, не про рельеф). Пометки **[не проверено]** относятся к ценам подписок, латентности GSMaP, статусу GSMaP v7, модели mountain‑forecast и точным именам параметров CDS time‑series — перепроверить перед тем, как класть в документацию. Лимит веб‑поиска сессии исчерпан, поэтому пункты по meteoexploration (сертификат), ScienceDirect‑обзору сеточных продуктов по HMA (403) и yr.no rate‑limits остались без прямой проверки.

Источники: [Open‑Meteo docs](https://open-meteo.com/en/docs), [Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api), [Historical Forecast API](https://open-meteo.com/en/docs/historical-forecast-api), [Ensemble API](https://open-meteo.com/en/docs/ensemble-api), [ECMWF API](https://open-meteo.com/en/docs/ecmwf-api), [Pricing](https://open-meteo.com/en/pricing), [Previous Runs API](https://open-meteo.com/en/docs/previous-runs-api), [issue #900](https://github.com/open-meteo/open-meteo/issues/900), [ECMWF open data](https://www.ecmwf.int/en/forecasts/datasets/open-data), [ECMWF Confluence open data](https://confluence.ecmwf.int/pages/viewpage.action?pageId=272310539), [ECMWF news 10.2025](https://www.ecmwf.int/en/about/media-centre/news/2025/ecmwf-makes-its-entire-real-time-catalogue-open-all), [ERA5 documentation](https://confluence.ecmwf.int/display/CKB/ERA5%3A+data+documentation), [ERA5‑Land‑T](https://climate.copernicus.eu/c3s-launches-new-era5-land-t-service), [ERA5‑Land ESSD 2021](https://essd.copernicus.org/articles/13/4349/2021/), [HAR TU Berlin](https://www.tu.berlin/en/klima/research/regional-climatology/high-asia/har), [HMASR NSIDC](https://nsidc.org/data/hma_sr_d/versions/1), [MERRA‑2 M2T1NXFLX](https://www.earthdata.nasa.gov/data/catalog/ges-disc-m2t1nxflx-5.12.4), [GPM IMERG](https://gpm.nasa.gov/data/imerg), [IMERG review](https://www.sciencedirect.com/science/article/abs/pii/S0034425721004740), [JAXA GSMaP registration](https://sharaku.eorc.jaxa.jp/GSMaP/registration.html), [CHIRPS v3](https://chc.ucsb.edu/data/chirps3), [MSWEP](https://www.gloh2o.org/mswep/), [MSWEP V3 arXiv](https://arxiv.org/html/2602.01436v1), [MET Norway FAQ](https://docs.api.met.no/doc/locationforecast/FAQ.html), [mountain‑forecast Gora Kurumdy](https://www.mountain-forecast.com/peaks/Gora-Kurumdy/forecasts/6614), [mountain‑forecast Pik Lenin 5500](https://www.mountain-forecast.com/peaks/Pik-Lenin/forecasts/5500), [TGO review of Mountain‑Forecast app](https://www.thegreatoutdoorsmag.com/review/mountain-forecast-app/), [meteoblue point+](https://www.meteoblue.com/en/pointplus), [Windy price thread](https://community.windy.com/topic/42438/premium-subscription-price-increase), [Meteoexploration Pik Lenina](https://meteoexploration.com/en/forecasts/Pik-Lenina/), [Frontiers 2019 MERRA‑2 snowfall HMA](https://www.frontiersin.org/journals/earth-science/articles/10.3389/feart.2019.00280/full), [ERC 2021 ERA5 overestimation HMA](https://iopscience.iop.org/article/10.1088/2515-7620/ac40f0), [HESS 2024 ERA5 downscaling HMA](https://hess.copernicus.org/articles/28/4903/2024/), [Immerzeel 2015 HESS](https://hess.copernicus.org/articles/19/4673/2015/hess-19-4673-2015.pdf), [IJC 2025 ERA5 snowfall China](https://rmets.onlinelibrary.wiley.com/doi/10.1002/joc.8926), [J. Mt. Sci. 2024 Tianshan precipitation types](https://link.springer.com/article/10.1007/s11629-024-9258-8), [Tianshan datasets 2026](https://www.sciencedirect.com/science/article/pii/S2214581826004519), [Fedchenko accumulation](https://www.cambridge.org/core/journals/journal-of-glaciology/article/high-altitude-accumulation-and-preserved-climate-information-in-the-western-pamir-observations-from-the-fedchenko-glacier-accumulation-basin/961DB0039DA77185FF8C55960D1EC1A1), [UBC new‑snow density](https://www.eoas.ubc.ca/courses/atsc113/snow/met_concepts/07-met_concepts/07b-newly-fallen-snow-density/), [Judson & Doesken BAMS](https://climate.colostate.edu/pdfs/Judson_Doesken_DensityFreshFallenSnow_BAMS.pdf).
