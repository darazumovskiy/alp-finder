# Открытые спутниковые данные и снежные продукты для мониторинга осадков/прироста снега на Курумды (39.48 N, 73.59 E, 4600–6100 м)

Дата обзора: 2026-08-27. Часть цифр (ревизит, латентность) **получена прямо из каталогов** (STAC CDSE, STAC USGS) по точке 73.59 E / 39.48 N за июнь–август 2026 — это не оценки, а факт. Местное время = UTC+6.

## 0. Главная оговорка по задаче

Ни один открытый спутниковый продукт **не измеряет толщину/прирост снега напрямую** на площадке 1–2 км в крутых горах. На 5000+ м NDSI насыщается: «снег есть» — всегда. Реально измеримые **прокси свежего снегопада**:

1. **Доля обнажённых скал/морены** в зоне интереса (S2 10 м, Landsat 30 м): после снегопада скалы и следы засыпаются → доля «не-снега» падает. Это самый прямой сигнал «засыпало ли участок между лагерями 2 и 3».
2. **Высота снеговой линии** на подходах/долинах (S2/Landsat + DEM): опустилась → был снегопад.
3. **Отражение в NIR/SWIR (B8A, B11, B12 у S2; I2/M11 у VIIRS)**: свежий мелкозернистый снег ярче в NIR/SWIR, при старении темнеет. Полуколичественный индикатор «свежести».
4. **Изменение обратного рассеяния Sentinel-1** по одному и тому же треку: мокрый снег даёт падение на 2–3 дБ, сухой свежий снег — небольшой рост VH (Lievens et al.).
5. **Геостационары** (Elektro-L на 76°E — почти надир над Памиром, Meteosat-9 IODC, FY-4B): **когда** была облачность/снегопад, с шагом 15–30 мин.

Толщина по спутнику (ICESat-2, C-SNOW, SWE-продукты) — либо архив, либо не работает на склонах >20°, см. ниже.

---

## 1. Sentinel-2 (A/B/C), L2A

**Состояние группировки 2026.** S2C заменил S2A в штатном строю 21.01.2025; S2A с 13.03.2025 летает в «кампании продления» на орбите со сдвигом 36° от S2B, **кампания продлена до 31.12.2026** — [CDSE news 2026-05-15](https://dataspace.copernicus.eu/news/2026-5-15-sentinel-2a-extension-campaign-prolonged-until-end-2026), [CDSE docs](https://documentation.dataspace.copernicus.eu/Data/SentinelMissions/Sentinel2.html).

**Ревизит по факту над точкой** (STAC CDSE, коллекция `sentinel-2-l2a`, тайл **T43SCD**, 01.06–27.08.2026): **45 сцен за 88 дней**, две относительные орбиты **R048** (~05:46 UTC) и **R091** (~05:56–06:03 UTC), то есть **~11:50 местного**. Паттерн: S2B/S2C дают пары через 2–3 дня (1, 3, 6, 8, 11, 13…), S2A добавляет сцену каждые 10 дней на каждой орбите. Средний интервал ≈ 2 дня. Облачность по тайлу: 16 из 45 сцен <30 %, ещё ~12 в диапазоне 30–50 %.

**Латентность по факту** (поле `published` в STAC за август 2026): S2B/S2C — **2.7–4.8 ч** после съёмки; S2A — **6–8 ч**. Формальная категория `PT24H`/NRT (по [CREODIAS](https://creodias.eu/cases/timeliness-and-frequency-of-sentinel-satellite-products-explained/) — «в течение 24 ч»). Т.е. сцена 11:50 местного обычно доступна к 15–17 часам того же дня.

| Поле | Значение |
|---|---|
| Что даёт | L2A: отражение на поверхности, 13 каналов; для снега: B03 (green), B08/B8A (NIR), B11/B12 (SWIR), маска SCL (снег/облака/тени) |
| Разрешение | 10 м (B02–B04, B08), 20 м (B8A, B11, B12, SCL) |
| Ревизит 39.5 N | ~2 дня (факт, см. выше); при 2 спутниках был бы 2–3 дня |
| Латентность | 3–8 ч (факт) |
| Стоимость | 0 |
| Как получить | CDSE: [STAC API](https://stac.dataspace.copernicus.eu/v1) (поиск без регистрации; скачивание — по бесплатной регистрации, S3-ключи или OData); [Copernicus Browser](https://browser.dataspace.copernicus.eu); [Sentinel Hub в CDSE](https://dataspace.copernicus.eu/ecosystem/services/sentinel-hub) — **10 000 PU/мес бесплатно**; [openEO](https://dataspace.copernicus.eu/ecosystem/services/openeo) — **10 000 кредитов/мес бесплатно** ([news 2026-03-02](https://dataspace.copernicus.eu/news/2026-3-2-platform-wide-updates-openeo-credit-billing)); GEE `COPERNICUS/S2_SR_HARMONIZED` (лаг ингеста — дни, неподтверждено точно) |
| Регистрация | CDSE — бесплатная; GEE — некоммерческий тир, с 27.04.2026 месячные квоты вычислений ([GEE noncommercial tiers](https://developers.google.com/earth-engine/guides/noncommercial_tiers)) |
| Ограничения | Облачность (≈ 1/3 сцен пригодны); съёмка в 11:50 — северные и северо-западные стены частично в тени (нужна маска освещённости из DEM); NDSI насыщен на снегу → работать с долей скал и NIR/SWIR-яркостью, а не с NDSI; SCL часто путает снег и облака над ледниками |
| **Пригодность: 5/5** | Единственный бесплатный источник с 10-м разрешением и суточной латентностью. Даёт «засыпало/не засыпало» на площадке 1–2 км и свежесть снега; толщину — нет. |

## 2. Landsat 8/9

**Ревизит по факту** (STAC USGS `landsat-c2l2-sr`): path 151, rows 032/033, L8 и L9 чередуются → **раз в 8 дней**, ~05:46 UTC (11:46 местного), 10 дат за июнь–август. Облачность: 6 из 10 дат <25 %.

| Поле | Значение |
|---|---|
| Что даёт | OLI 30 м (9 каналов, SWIR 1.6/2.2 мкм), TIRS 100 м; C2 L2 SR + QA-маска |
| Разрешение | 30 м (пан 15 м) |
| Ревизит | 8 дней (L8+L9) |
| Латентность | L1 — **4–6 ч** (RT tier), L2 SR — обычно в пределах суток-двух (неподтверждено точно) — [USGS L1](https://www.usgs.gov/landsat-missions/landsat-collection-2-level-1-data), [USGS L2](https://www.usgs.gov/landsat-missions/landsat-collection-2-level-2-science-products) |
| Стоимость | 0 |
| Как получить | [EarthExplorer](https://earthexplorer.usgs.gov), [STAC landsatlook](https://landsatlook.usgs.gov/stac-server) (поиск без регистрации), GEE `LANDSAT/LC09/C02/T1_L2`; с 01.2026 L1 также в CDSE ([news](https://dataspace.copernicus.eu/news/2026-1-16-landsat-8-and-landsat-9-collection-2-level-1-data-availability-copernicus-data-space)) |
| Регистрация | USGS EROS (бесплатно) для скачивания |
| Ограничения | Те же, что S2, плюс 30 м; 12-битная радиометрия — на ярком снегу насыщения меньше, чем у старых сенсоров |
| **Пригодность: 3/5** | Дополняет S2 (сдвигает сетку дат, +1 шанс на безоблачную сцену в неделю); тот же алгоритм. Отдельно — не нужен. Через [HLS v2.0](https://hls.gsfc.nasa.gov/data-access-and-tools/) (30 м, S2+Landsat гармонизированы, латентность 2–3 дня) можно взять обе миссии одним форматом, но у HLSS30 бывают пропуски. |

## 3. MODIS / VIIRS (ежедневные снежные продукты)

**Состояние 2026.** Terra и Aqua дрейфуют: Aqua — до конца миссии **август 2026** (может уже завершена — проверить), Terra — финансирование до 30.09.2026, EOL до 2027 (неподтверждено) — [NSIDC об орбитах](https://nsidc.org/data/user-resources/data-announcements/ongoing-changes-terra-and-aqua-orbits-impacting-modis-snow-and-sea-ice-products), [Terra status](https://terra.nasa.gov/about/instrumentupdatearchive). Дрейф Terra к более раннему времени → **длиннее тени на склонах**. NASA переводит всё на VIIRS ([MODIS→VIIRS](https://ladsweb.modaps.eosdis.nasa.gov/learn/modis-to-viirs-transition/)). Suomi-NPP имел аварию 10–21.07.2026, восстановлен.

| Продукт | Что даёт | Разр. | Ревизит | Латентность | Как получить |
|---|---|---|---|---|---|
| **MOD10A1 v61** (Terra) | NDSI_Snow_Cover 0–100, NDSI, Snow_Albedo, QA; «ongoing» — [NSIDC](https://nsidc.org/data/mod10a1/versions/61) | 500 м | ежедневно | «несколько дней» | earthaccess/Earthdata Search, GEE `MODIS/061/MOD10A1` |
| **MOD10_L2 NRT** (LANCE) | swath 5-мин, снег/NDSI | 500 м | ежедневно | **60–125 мин** — [Earthdata MODIS NRT](https://www.earthdata.nasa.gov/data/instruments/modis/near-real-time-data) | `nrt3.modaps.eosdis.nasa.gov/archive/allData/61/MOD10_L2/` (Earthdata Login) |
| **MOD10A1F / MYD10A1F** | cloud-gap-filled: последний безоблачный пиксель + возраст наблюдения — [GSFC](https://modis-snow-ice.gsfc.nasa.gov/?c=MOD10A1F) | 500 м | ежедневно | дни | NSIDC |
| **VNP10A1 v2** (S-NPP) | NDSI snow cover, «2012 — present» — [NSIDC](https://nsidc.org/data/vnp10a1/versions/2) | **375 м** | ежедневно | «несколько дней» | earthaccess, AWS Earthdata Cloud |
| **VJ110A1 / VJ210A1** (NOAA-20/21) | то же | 375 м | ежедневно (3 спутника VIIRS → 3 пролёта/сутки) | дни | NSIDC/LAADS |
| **VNP10_NRT / VJ110_NRT** (LANCE) | swath 6-мин, 375 м — [Earthdata VIIRS NRT](https://www.earthdata.nasa.gov/data/instruments/viirs/land-near-real-time-data) | 375 м | ежедневно | ~1–3 ч (в таблице NASA не указано; по общему правилу LANCE ≈3 ч — неподтверждено) | nrt3/nrt4 |
| **VNP10A1F v1** | CGF | 375 м | — | **снят в апреле 2025**, рекомендуется v2 (наличие v2 F — неподтверждено) — [NSIDC](https://nsidc.org/data/vnp10a1f/versions/1) | — |

Стоимость: 0; регистрация Earthdata Login (бесплатно).

**Ограничения:** 375–500 м на площадке 1–2 км — это 4–16 пикселей; в них смесь склонов разной экспозиции; NDSI на высокогорье насыщен; тени на склонах при низком солнце (Terra) помечаются как «неопределённость». Для оценки *прироста* полезны не NDSI_Snow_Cover, а **Snow_Albedo_Daily_Tile** (MOD10A1) и NIR/SWIR-каналы L1/L2 (VNP02/VNP09) — рост альбедо после снегопада.

**Пригодность: 3/5** для «был ли снегопад в районе» (ежедневно, латентность ~2 ч через LANCE), **1–2/5** для «сколько выпало на площадке».

## 4. Sentinel-1 SAR (C-band)

**Состояние группировки 2026.** S1A завершил работу **29.06.2026**; S1C (запуск 12.2024) + S1D (запуск 11.2025, данные открыты с **17.04.2026**) — штатная пара с 6-дневным номинальным ревизитом с конца июня 2026 — [ESA](https://www.esa.int/Applications/Observing_the_Earth/Copernicus/Sentinel-1/Time_to_say_goodbye_to_Sentinel-1A), [CDSE 2026-05-28](https://dataspace.copernicus.eu/news/2026-5-28-sentinel-1-orbital-reconfiguration-dates), [Sentinel Online](https://sentinels.copernicus.eu/-/sentinel-1d-user-data-opening-and-future-plans).

**Ревизит по факту над точкой** (STAC CDSE `sentinel-1-grd`, июнь–август 2026): **4 относительные орбиты**, каждая с 12-дневным циклом:
- **R005** desc ~01:13 UTC (07:13 местного), **R107** desc ~01:05 UTC — два соседних дня;
- **R027** asc ~12:58 UTC (18:58 местного), **R100** asc ~13:06 UTC.

Итого **4 пролёта за 12 дней** (пары в соседние дни: 5–6, 11–12, 17–18, 23–24 августа). С июля все сцены только **S1D** (принял сценарий S1A); **S1C над этим районом в каталоге за июнь–август нет** — то есть «6-дневный ревизит» здесь пока не реализован на одном треке, неподтверждено, изменится ли сценарий. Все продукты IW GRDH 1SDV (VV+VH), SLC тоже есть.

**Латентность по факту** (август 2026): **2.3–2.8 ч** после съёмки (категория Fast-24h), один выброс 20 ч.

| Поле | Значение |
|---|---|
| Что даёт | σ⁰ VV/VH, не зависит от облачности и освещённости |
| Разрешение | IW GRDH ~10×10 м пиксель (20×22 м реальное) |
| Ревизит | 4 пролёта/12 дней с 4 геометрий (факт) |
| Латентность | 2–3 ч (факт) |
| Стоимость | 0 |
| Как получить | CDSE STAC/OData/S3; Sentinel Hub в CDSE (`sentinel-1-grd`, есть ортокоррекция по Copernicus DEM и `GAMMA0_TERRAIN`) — [docs](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data/S1GRD.html); [ASF DAAC](https://www.earthdata.nasa.gov/data/alerts-outages/sentinel-1d-data-available-download-asf-daac) + бесплатный on-demand RTC-процессинг [HyP3](https://hyp3-docs.asf.alaska.edu/sentinel1/) (месячная квота кредитов — число неподтверждено); GEE `COPERNICUS/S1_GRD` (ингест ~2 дня) |
| Что вытащить про снег | (а) **мокрый снег**: падение на −2…−3 дБ относительно референса сухого снега (Nagler & Rott) — [обзор](https://www.mdpi.com/2072-4292/8/4/348); (б) **сухой свежий снег/глубина**: рост VH/VV (Lievens et al. 2019/2022), алгоритм эмпирический, 1 км, корреляция 0.65–0.77, MAE 0.18–0.31 м — [TC 2022](https://tc.copernicus.org/articles/16/159/2022/), [TC 2026](https://tc.copernicus.org/articles/20/227/2026/); (в) готовый продукт **C-SNOW** (KU Leuven) — только архив 2016–2020, NH, 1 км — [C-SNOW](https://ees.kuleuven.be/project/c-snow) |
| Ограничения | Layover/shadow на крутых склонах (нужен отбор пикселей по локальному углу падения 20–60°, DEM 8–30 м); ретривал глубины на 1 км и на склонах не валидирован; сравнивать только **один и тот же трек** между датами |
| **Пригодность: 3/5** | Всепогодный индикатор «изменилось состояние снега» каждые ~3 дня; количественная глубина — нет. Хороший второй канал, когда S2 закрыт облаками. |

## 5. Готовые снежные продукты

| Продукт | Что даёт | Разр. | Период / латентность | Доступ | Пригодность |
|---|---|---|---|---|---|
| **CLMS Snow Cover Extent Global 1 km v1** | доля снега 0–100 % (SCAmod), S3 SLSTR + NOAA-20 VIIRS, **с декабря 2025**, **латентность «within 1 day»** — [CLMS](https://land.copernicus.eu/en/products/snow/snow-cover-global-v1-0-1km), [ATBD](https://land.copernicus.eu/en/technical-library/algorithm-theoretical-basis-document-snow-cover-extent-global-version-1/@@download/file) | 1 км | ежедневно / ≤1 сут | CDSE S3 `/eodata/CLMS/bio-geophysical/snow_cover_extent/sce_global_1km_daily_v1`, OData | **2/5** — 1 км, FSC насыщен на высокогорье; годится как «облачно/безоблачно + сколько снега в долинах» |
| **CLMS SWE NH 5 km v2** | SWE, SSMIS+VIIRS, с 07.2024, «within 12 h» — [CLMS](https://land.copernicus.eu/en/products/snow/snow-water-equivalent-v2-0-5km) | 5 км | ежедневно / 12 ч | CDSE S3 `/eodata/CLMS/bio-geophysical/snow_water_equivalent/swe_northernhemisphere_5km_daily_v2` | **1/5** — пассивная микроволновка; в сложном рельефе, как правило, маска/недостоверно (для этого пикселя не проверено) |
| **ESA CCI Snow (SCFV, SWE) v4** | климатические ряды 1979–2023, SLSTR 2020–2022 — [CCI](https://climate.esa.int/en/projects/snow/Snow_data/) | 1–25 км | архив | CEDA | **0/5** — не NRT |
| **HMASR (UCLA)** | суточные SWE/SD/fSCA, 16″ (~500 м), **WY2000–2017** — [NSIDC](https://nsidc.org/data/hma_sr_d/versions/1) | 500 м | архив | NSIDC | **0/5** для мониторинга; **полезен для климатологии**: сколько обычно накапливается в сентябре–октябре на этих высотах |
| **HMA DEM 8 m** (mosaics, 2002–2016) — [NSIDC](https://nsidc.org/data/hma_dem8m_mos/versions/1) | опорный рельеф | 8 м | статика | NSIDC | справочный (у проекта уже используется) |
| **H SAF H35** (AVHRR/Metop, NH, fSCA 1 км, NRT ежедневно) — [H SAF](https://hsaf.meteoam.it/Products/ProductsList?type=snow) | fSCA | 1 км | ежедневно | регистрация на hsaf.meteoam.it, FTP | **2/5** |
| **H SAF H65** (SWE NH 25 км), **H13** (SWE только Европа 25W–45E), **H34** (SEVIRI 0° — слишком косой ракурс на 73°E) | SWE / маска | 25 км / 3 км | ежедневно | то же | **1/5** |
| **ICESat-2 ATL06/ATL08** | высота поверхности вдоль треков, 91-дневный цикл, но в средних широтах **тот же RGT повторяется раз в ~3 года** (спутник наклоняется для покрытия); на склонах >20° ошибка глубины **1.3–1.8 м** — [TC 2026](https://tc.copernicus.org/articles/20/3051/2026/), [ATL06 v7](https://nsidc.org/data/atl06/versions/7) | пятно ~17 м, шаг 20 м | нерегулярно | NSIDC, латентность недели (неподтверждено) | **1/5** — на конкретном склоне не измерить |

## 6. Planet

- **NICFI**: программа **закрыта** — новых базмапов нет с 12.2024, доступ снят к 04.2025, Норвегия отменила закупку следующей фазы (09.2025) — [Nimbo](https://nimbo.earth/stories/end-nicfi-satellite-tropical-forest-monitoring-alternative/), [Collect Earth](https://www.collect.earth/planet-imagery-via-nicfi-is-no-longer-available-on-ceo/). Памир в тропики и так не входил.
- **Education & Research Basic**: **3 000 км²/мес** PlanetScope, нужен **университетский e-mail**, НКО и госорганы не подходят — [Planet E&R](https://www.planet.com/industries/education-and-research/), [support](https://support.planet.com/hc/en-us/articles/360016570538-How-Can-I-Register-for-the-Education-and-Research-Basic-Program). Если у кого-то из волонтёров есть университетская аффилиация — 3 000 км²/мес хватает на ежедневные 3-м снимки площадки 5×5 км (≈750 км²/мес).
- **Пригодность: 4/5 при наличии аккаунта, иначе 0.** Программа disaster-data у Planet для этой операции — неподтверждено, можно запросить.

## 7. Umbra / Capella open data

- **Umbra Open Data** (CC-BY 4.0, [AWS](https://registry.opendata.aws/umbra-open-data/)): проверил листинг бакета `umbra-open-data-catalog` — **над Кыргызстаном/Таджикистаном сцен нет**; ближайшие: Ташкент, Кабул, Амударья, Lop Nur (Синьцзян). **0/5**.
- **Capella Open Data** ([STAC](https://stacindex.org/catalogs/capella-space-open-data), [support](https://support.capellaspace.com/what-is-the-capella-open-data-program)): каталог по типам/столицам/use-case; сцен над Памиром не известно (полный перебор STAC не делал — неподтверждено). **0–1/5**.

## 8. Прочее

| Источник | Что даёт | Оценка |
|---|---|---|
| **Copernicus DEM** | GLO-30 издание 2024_1 в GEE ([GEE](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_DEM_GLO30_2024_1)), DGED 2023_1 на OpenTopography; новых съёмок TanDEM-X нет — для снега бесполезен, только как опорный рельеф | справочно |
| **ASTER** | Terra жив, ASTER TIR восстановлен 15.04.2025; съёмка по заявкам, не систематическая; будущее после 30.09.2026 неясно — [Terra](https://terra.nasa.gov/areas/aster) | 1/5 |
| **PROBA-V** | миссия завершена в 2020–2021 (по памяти, неподтверждено) | 0/5 |
| **Elektro-L №3 и №5 на 76°E** (МСУ-ГС, 1 км VIS / 4 км IR в надире, **30 мин**) — **почти надир над Памиром** (Δλ ≈ 3°). №5 введён в эксплуатацию 06.08.2026, оба на 76°E — [ntsomz](https://electro.ntsomz.ru/en/), [news](https://www1.ru/en/news/2026/08/07/426899-rossiia-rassiriaet-gruppirovku-sputnikov-elektro-l.html). Бесплатно, без регистрации: полный диск JPG 11136×11136 (~1 км/пикс) и FTP `electro:electro@ntsomz.gptl.ru:2121`; архив на сайте — сутки; калиброванных L1 в открытом доступе — неподтверждено | **3/5** для «когда шёл снег / когда просветы» (RGB-JPG, без калибровки) |
| **Meteosat-9 IODC на 45.5°E** (SEVIRI, 12 каналов, **15 мин**, 3 км в надире, HRV 1 км; на 39.5N/73.6E угол ~50–55° → эффективно ~5 км; в строю до 2027) — [EUMETSAT IODC](https://www.eumetsat.int/indian-ocean-data-coverage-iodc), [Data Store HRSEVIRI-IODC](https://data.eumetsat.int/product/EO:EUM:DAT:MSG:HRSEVIRI-IODC). Бесплатно по регистрации, API `eumdac` | **3/5** — калиброванный 1.6 мкм (снег/облако) и IR каждые 15 мин |
| **FY-4B AGRI** (~105°E с 03.2024 — [AMT](https://amt.copernicus.org/articles/17/6659/2024/); полный диск 15 мин; VIS 0.5–1 км, 1.6 мкм 2 км) — [NSMC](http://data.nsmc.org.cn) с регистрацией | 2/5 — доступ через китайский портал, неудобно |
| **MTG-I1 (0°), Himawari-9 (140.7°E), GOES** | 73°E — на краю/за краем диска | 0/5 |
| **GPM IMERG Early** (0.1°, 30 мин, **латентность 4 ч**) — [GES DISC](https://www.earthdata.nasa.gov/data/catalog/ges-disc-gpm-3imergde-07) | сигнал «прошёл фронт с осадками»; твёрдые осадки в высокогорье занижает (общеизвестно, для точки не проверено) | 2/5 |
| **NASA Worldview / GIBS** — true/false color VIIRS NOAA-20/21 **250 м**, MODIS, слои snow cover; **~3 ч** после съёмки — [Worldview](https://worldview.earthdata.nasa.gov), [снапшоты](https://wvs.earthdata.nasa.gov) | ежедневный визуальный контроль без кода; false color M11-I2-I1 отделяет снег от облаков | **4/5** для быстрого глаза |

---

## Сводная таблица

| Источник | Разр. | Ревизит 39.5 N (факт) | Латентность | Регистрация | Прирост снега? | Оценка |
|---|---|---|---|---|---|---|
| Sentinel-2 L2A | 10–20 м | ~2 дня (45 сцен/88 дн) | 3–8 ч (факт) | CDSE, бесплатно | прокси: доля скал, снеговая линия, NIR/SWIR-свежесть | **5** |
| Sentinel-1 GRD | ~20 м | 4 пролёта/12 дн, 4 трека | 2–3 ч (факт) | CDSE/ASF | мокрый снег; сухой — полуколич. | **3** |
| Landsat 8/9 | 30 м | 8 дней | 4–6 ч (L1) | USGS | как S2 | **3** |
| VIIRS VNP10A1/VJ110A1, LANCE NRT | 375 м | ежедневно ×3 | 1–3 ч NRT / дни L3 | Earthdata | «был снегопад в районе» | **3** |
| MODIS MOD10A1 / MOD10_L2 NRT | 500 м | ежедневно | 1–2 ч NRT | Earthdata | то же; Terra/Aqua на исходе | **2–3** |
| Worldview/GIBS | 250 м | ежедневно | ~3 ч | нет | визуально | **4** |
| Elektro-L 76°E | ~1 км | 30 мин | ~минуты–часы | нет | время снегопадов/просветов | **3** |
| Meteosat-9 IODC | ~5 км эфф. | 15 мин | ~1 ч (неподтв.) | EUMETSAT | то же, калибровано | **3** |
| CLMS SCE 1 км global | 1 км | ежедневно | ≤1 сут | CDSE | FSC, насыщен | **2** |
| H SAF H35 | 1 км | ежедневно | ~сутки | H SAF | FSC | **2** |
| IMERG Early | ~10 км | 30 мин | 4 ч | Earthdata | осадки грубо | **2** |
| Planet E&R | 3 м | ежедневно | часы | университет | визуально, 3 м | **4 / 0** |
| CLMS SWE 5 км, H65 | 5–25 км | ежедневно | 12 ч | — | в горах не работает | **1** |
| ICESat-2 | 17 м вдоль трека | ~3 года на RGT | недели | Earthdata | нет | **1** |
| HMASR, CCI Snow, C-SNOW | 0.5–25 км | архив | — | — | климатология | **0** |
| Umbra/Capella | 0.25–1 м | нет сцен | — | — | — | **0** |

## Ранжированная рекомендация: что заработает за 1–3 дня силами одного Python-программиста

1. **Sentinel-2 «индекс засыпания» (день 1).** Cron раз в 6 ч: STAC CDSE → новые сцены тайла T43SCD → через Sentinel Hub Process API (бесплатные 10 000 PU) или S3 забрать B03/B04/B8A/B11/B12/SCL для полигона ~5×5 км → маски облаков/теней (SCL + маска освещённости из DEM по времени съёмки) → по высотным поясам (4600–5000, 5000–5500, 5500–6100) считать: долю пикселей NDSI<0.4 (скалы), медиану B11 на снегу (свежесть), высоту снеговой линии в долинах. Выход — CSV-ряд + PNG-квиклук с обводкой зоны C2–C3, автоматом в закрытую тим-версию. Уже к 15–17 ч местного есть ответ по сцене 11:50.
2. **Worldview/GIBS снапшоты (день 1, час работы).** Скрипт дергает `wvs.earthdata.nasa.gov` для VIIRS NOAA-21 false color (M11-I2-I1) и true color 250 м по bbox → ежедневная картинка «район целиком»: облачность, снегопад на подходах. Плюс IMERG Early как флаг «прошёл фронт».
3. **Sentinel-1 «изменение по треку» (день 2).** Для 4 треков (R005/R107/R027/R100) хранить референс сухого снега (последняя ясная холодная дата) и на каждый новый пролёт считать Δσ⁰ VV, VH, VH/VV на площадке по пикселям с локальным углом 20–60° (RTC через HyP3 или `GAMMA0_TERRAIN` в Sentinel Hub). Порог −2…−3 дБ → «мокрый снег/таяние», рост VH → «вероятно свежий сухой снег». Работает сквозь облака каждые ~3 дня, через 2–3 ч после пролёта.
4. **VIIRS/MODIS ежедневный ряд (день 2–3).** VNP10A1/VJ110A1 через `earthaccess` (или LANCE VJ110_NRT для 1–3 ч) → Snow albedo / NDSI-статистика по окну 3×3 км — грубый, но ежедневный фон для п. 1.
5. **Elektro-L 76°E (день 3, опционально).** Раз в 30 мин качать JPG полного диска, вырезать окно вокруг Памира → анимация суток: когда закрыто облаками, когда просветы (планирование вылетов дрона). Если нужны калиброванные каналы — Meteosat-9 IODC через `eumdac`.
6. Не тратить время: ICESat-2, SWE-продукты (CLMS/H SAF/CCI), HMASR (кроме одной выгрузки климатологии сентября–октября 2000–2017 для ожидаемых величин), Umbra/Capella, ASTER, Copernicus DEM. Planet E&R — только если найдётся университетский аккаунт (тогда это лучший визуальный канал, 3 м ежедневно).

Ключевые неподтверждённые пункты, которые стоит проверить перед стартом: (а) будет ли S1C снимать этот район (пока в каталоге только S1D); (б) статус Aqua после августа 2026; (в) наличие VNP10A1F v2; (г) квота HyP3; (д) есть ли у Elektro-L открытые калиброванные L1-данные, а не только JPG.

Sources:
- [CDSE: Sentinel-2A extension prolonged until end 2026](https://dataspace.copernicus.eu/news/2026-5-15-sentinel-2a-extension-campaign-prolonged-until-end-2026)
- [CDSE Sentinel-2 documentation](https://documentation.dataspace.copernicus.eu/Data/SentinelMissions/Sentinel2.html)
- [CDSE STAC API](https://stac.dataspace.copernicus.eu/v1)
- [Sentinel Hub в CDSE](https://dataspace.copernicus.eu/ecosystem/services/sentinel-hub), [openEO в CDSE](https://dataspace.copernicus.eu/ecosystem/services/openeo), [openEO billing 2026](https://dataspace.copernicus.eu/news/2026-3-2-platform-wide-updates-openeo-credit-billing)
- [GEE noncommercial tiers](https://developers.google.com/earth-engine/guides/noncommercial_tiers)
- [ESA: goodbye Sentinel-1A](https://www.esa.int/Applications/Observing_the_Earth/Copernicus/Sentinel-1/Time_to_say_goodbye_to_Sentinel-1A), [CDSE S1 reconfiguration](https://dataspace.copernicus.eu/news/2026-5-28-sentinel-1-orbital-reconfiguration-dates), [Sentinel-1D data opening](https://sentinels.copernicus.eu/-/sentinel-1d-user-data-opening-and-future-plans), [S1D at ASF](https://www.earthdata.nasa.gov/data/alerts-outages/sentinel-1d-data-available-download-asf-daac), [HyP3](https://hyp3-docs.asf.alaska.edu/sentinel1/), [S1 GRD в Sentinel Hub](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data/S1GRD.html)
- [Lievens S1 snow depth Alps (TC 2022)](https://tc.copernicus.org/articles/16/159/2022/), [Scale patterns S1 SD (TC 2026)](https://tc.copernicus.org/articles/20/227/2026/), [C-SNOW](https://ees.kuleuven.be/project/c-snow), [Nagler wet snow S1](https://www.mdpi.com/2072-4292/8/4/348)
- [USGS Landsat C2 L1](https://www.usgs.gov/landsat-missions/landsat-collection-2-level-1-data), [L2](https://www.usgs.gov/landsat-missions/landsat-collection-2-level-2-science-products), [Landsat в CDSE](https://dataspace.copernicus.eu/news/2026-1-16-landsat-8-and-landsat-9-collection-2-level-1-data-availability-copernicus-data-space), [HLS](https://hls.gsfc.nasa.gov/data-access-and-tools/)
- [NSIDC MOD10A1 v61](https://nsidc.org/data/mod10a1/versions/61), [MOD10A1F](https://modis-snow-ice.gsfc.nasa.gov/?c=MOD10A1F), [VNP10A1 v2](https://nsidc.org/data/vnp10a1/versions/2), [VNP10A1F v1 retired](https://nsidc.org/data/vnp10a1f/versions/1), [MODIS NRT](https://www.earthdata.nasa.gov/data/instruments/modis/near-real-time-data), [VIIRS land NRT](https://www.earthdata.nasa.gov/data/instruments/viirs/land-near-real-time-data), [Terra/Aqua drift (NSIDC)](https://nsidc.org/data/user-resources/data-announcements/ongoing-changes-terra-and-aqua-orbits-impacting-modis-snow-and-sea-ice-products), [MODIS→VIIRS](https://ladsweb.modaps.eosdis.nasa.gov/learn/modis-to-viirs-transition/), [Terra updates](https://terra.nasa.gov/about/instrumentupdatearchive), [ASTER](https://terra.nasa.gov/areas/aster)
- [CLMS SCE Global 1 km](https://land.copernicus.eu/en/products/snow/snow-cover-global-v1-0-1km), [ATBD](https://land.copernicus.eu/en/technical-library/algorithm-theoretical-basis-document-snow-cover-extent-global-version-1/@@download/file), [CLMS SWE v2](https://land.copernicus.eu/en/products/snow/snow-water-equivalent-v2-0-5km)
- [ESA CCI Snow data](https://climate.esa.int/en/projects/snow/Snow_data/), [HMASR](https://nsidc.org/data/hma_sr_d/versions/1), [HMA DEM 8 m](https://nsidc.org/data/hma_dem8m_mos/versions/1)
- [ICESat-2 snow depth workflow (TC 2026)](https://tc.copernicus.org/articles/20/3051/2026/), [ATL06 v7](https://nsidc.org/data/atl06/versions/7)
- [H SAF snow products](https://hsaf.meteoam.it/Products/ProductsList?type=snow), [H65](https://hsaf.meteoam.it/Products/Detail?prod=H65)
- [Planet E&R](https://www.planet.com/industries/education-and-research/), [E&R Basic registration](https://support.planet.com/hc/en-us/articles/360016570538-How-Can-I-Register-for-the-Education-and-Research-Basic-Program), [NICFI end](https://nimbo.earth/stories/end-nicfi-satellite-tropical-forest-monitoring-alternative/), [Collect Earth NICFI](https://www.collect.earth/planet-imagery-via-nicfi-is-no-longer-available-on-ceo/)
- [Umbra open data AWS](https://registry.opendata.aws/umbra-open-data/), [Umbra catalog](http://umbra-open-data-catalog.s3-website.us-west-2.amazonaws.com/), [Capella open data](https://support.capellaspace.com/what-is-the-capella-open-data-program), [Capella STAC](https://stacindex.org/catalogs/capella-space-open-data)
- [Copernicus DEM 2024_1 (GEE)](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_DEM_GLO30_2024_1), [OpenTopography](https://opentopography.org/news/updated-copernicus-30m-DEM-available)
- [Elektro-L site](https://electro.ntsomz.ru/en/), [Elektro-L №5 в эксплуатации](https://www1.ru/en/news/2026/08/07/426899-rossiia-rassiriaet-gruppirovku-sputnikov-elektro-l.html), [запуск №5](https://www.nasaspaceflight.com/2026/02/elektro-l-no5-launch/)
- [EUMETSAT IODC](https://www.eumetsat.int/indian-ocean-data-coverage-iodc), [HRSEVIRI-IODC в Data Store](https://data.eumetsat.int/product/EO:EUM:DAT:MSG:HRSEVIRI-IODC), [WMO OSCAR Meteosat-9 IODC](https://space.oscar.wmo.int/satellites/view/meteosat_9_iodc)
- [FY-4B drift to 105E (AMT)](https://amt.copernicus.org/articles/17/6659/2024/), [FY-4 eoPortal](https://www.eoportal.org/satellite-missions/fy-4), [NSMC data](http://data.nsmc.org.cn)
- [IMERG Early](https://www.earthdata.nasa.gov/data/catalog/ges-disc-gpm-3imergde-07)
- [Worldview new layers](https://www.earthdata.nasa.gov/news/worldview-adds-15-new-data-layers)
- [CREODIAS timeliness](https://creodias.eu/cases/timeliness-and-frequency-of-sentinel-satellite-products-explained/)
