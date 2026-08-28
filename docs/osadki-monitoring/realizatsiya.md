# Мониторинг снега на Курумды — как это реализовано (справочник для новых сессий)

Состояние на вечер 28.08.2026. Этот файл — техническое описание того, что построено и как оно работает;
научная база, аудиты и история решений — в [../osadki-monitoring.md](../osadki-monitoring.md) (§7–8),
шесть исходных рисерч-отчётов — в [otchety/](otchety/), аудиты методики — в [audit/](audit/).
Человеческий вариант (для штаба) — страница «Снег и погода» на сайте (`analysis/viewer/osadki-monitoring.html`).

## 1. Задача и рамки

Штаб (руководитель Alex, 27.08): третий альпинист остаётся на склоне выше рюкзака (~4663 м) или в трещине;
нужно **следить** (не предсказывать), снега на склонах стало больше или меньше, по высотам, и накопить историю
к 15.09.2026. Ограничения оператора (Dmitriy): только «с дивана» — спутники, метеомодели, станции; дроны, рейки,
датчики, люди на горе исключены. Решения оператора, которые нельзя нарушать:

- ИИ-агент — **только `claude-fable-5`**, без дешёвых и запасных моделей («туда пойдут люди»); при отказе модели
  заметка просто не создаётся, и это видно на странице.
- Каждый запуск конвейера явно виден на странице (статус-бар). В Telegram пока ничего не шлём.
- Все числа — с погрешностью; эвристические выводы — с уровнем доверия.
- На странице **явно различаются** два вида текста: «⚙️ Алгоритмический расчёт по данным» (шаблон с подстановкой
  чисел, функция `auto_comment()`, без модели) и «✨ Оценка ИИ-агента · сгенерированный текст» (Fable 5).
- Оценка ИИ-агента должна лежать на карточке **дня снимка Sentinel-2**, а не на дне запуска.
- Проверка методики — независимыми сабагентами на чистом контексте с установкой искать ошибки; результаты — в `audit/`.
- В интерфейсе сайта не должно быть упоминаний контура публикации, паролей и т.п. (перед деплоем — grep, см. §9).

## 2. Где что лежит

| Путь | Что это |
|---|---|
| `analysis/osadki/run_daily.py` | Оркестратор: 13 шагов, журнал `data/runs.json`; ручной запуск и флаги — §7 |
| `analysis/osadki/fetch_model.py` | Open-Meteo: ECMWF IFS 9 км по высотам 4000/4663/5018/5435/6000 м + 5 моделей-вилка + ансамбль → `data/model_daily.json` |
| `analysis/osadki/fetch_stations.py` | OGIMET, декодированные SYNOP станций 38875 Каракуль (3930 м) и 38871 Сары-Таш (3150 м) → `data/stations.json` |
| `analysis/osadki/fetch_clim.py` | Климатология «как обычно»: IFS 2017–2025 и ERA5 1991–2025, помесячно → `data/clim.json` (запускается вручную, не в конвейере) |
| `analysis/osadki/render_s2.py` | Sentinel-2 L2A через AWS earth-search STAC (тайл T43SCD, без ключей): кропы 3×3 км и 8×8 км, RGB/SWIR/маска → `scenes/s2/<дата>_<спутник>/` |
| `analysis/osadki/render_viirs.py` | VIIRS NOAA-20 375 м через NASA GIBS WMS, обычные и ложные цвета, два охвата → `scenes/viirs/<дата>/` |
| `analysis/osadki/render_s1.py` | Sentinel-1 RTC (Microsoft Planetary Computer, SAS-подпись без регистрации): VV/VH дБ и разница к предыдущему пролёту того же трека → `scenes/s1/<дата>_<трек>/` |
| `analysis/osadki/snow_index.py` | **Индекс снега v2** по Sentinel-2 на поясах высот по DEM HMA 8 м → `data/snow_index.json`, `scenes/s2/_evaluable.png` |
| `analysis/osadki/pack_scenes.py` | Склейка панелей каждой сцены в один `sprite.jpg` (лимит файлов хостинга) + индексы `data/{s2,s1,viirs}_index.json` |
| `analysis/osadki/build_report.py` | Лента `data/timeline.json` (`--timeline-only`) и страница `analysis/viewer/osadki-monitoring.html` |
| `analysis/osadki/report.css`, `report.js` | Стили и интерактив страницы (лента дней, спрайт-просмотрщик, глубокие ссылки `#date=YYYY-MM-DD`) |
| `analysis/osadki/agent_daily.py` | ИИ-агент (Anthropic Python SDK, `claude-fable-5`) → `agent/notes/<дата>.json` |
| `analysis/osadki/agent/notes/` | Заметки агента по датам (в git — они малы и не воспроизводятся бесплатно) |
| `analysis/osadki/build_sverka.py` | Страница-сверка «Август 2026: спутник и модель против очевидца» → `analysis/viewer/sverka-avgust-2026.html` |
| `analysis/osadki/fakt-vs-model.tsv` | **Рукописный** журнал «факт против модели» (очевидец AE 11–16.08, контрольная точка штаба). Лежит вне `data/`, потому что `data/` игнорируется git |
| `analysis/osadki/seed/alt_*.json` | Сырые ответы Open-Meteo от 27.08 для сверки (скрипта-генератора нет, поэтому вне `data/`) |
| `analysis/osadki/data/` | Все регенерируемые данные (под `.gitignore` правилом `data/`): `model_daily.json`, `stations.json`, `clim.json`, `snow_index.json`, `timeline.json`, `runs.json`, `runs.log`, `launchd.log`, индексы сцен |
| `analysis/osadki/scenes/` | Кропы снимков и спрайты (~90 МБ, регенерируются; в `.gitignore`) |
| `analysis/viewer/otchety.html` | Список отчётов на сайте (пункт меню «Отчёты») |
| `scripts/osadki_daily.sh` | Обёртка для launchd (PATH, локаль, лог `data/launchd.log`) |
| `scripts/com.alp-finder.osadki.plist` | LaunchAgent; установлен в `~/Library/LaunchAgents/`, запуски 05:00 и 15:00 по часам Mac (= 08:00/18:00 Бишкека при UTC+3) |
| `scripts/deploy_private.sh` | Публикация `analysis/viewer/dist` (адрес, пароль, конфиг воркера — в локальных файлах, не в git) |
| `docs/osadki-monitoring.md` | База знаний: рисерч 27.08 (§1–6), уточнённый интент (§7), аудиты/индекс v2/конвейер/агент (§8) |

Точки и пояса (координаты в коде `snow_index.py`/`render_s2.py`): рюкзак 39.482656, 73.586792 (4663 м);
центр зоны интереса 39.4780, 73.5924; контрольная точка штаба 39.481279, 73.592673 (5099 м). Единицы индекса:
`ryukzak_4600-4900` (R 500 м от рюкзака), `sklon_4900-5200` и `zona_5200-5500` (R 800 м от центра зоны),
`ctrl_5099` (R 150 м). Цвета единиц на всех страницах одинаковые: #d95926 / #c98500 / #3987e5 / #199e70; цвет
ИИ-блока #b388ff.

## 3. Поток данных

```
Open-Meteo ──fetch_model──▶ model_daily.json ─┐
OGIMET ─────fetch_stations▶ stations.json ────┤
earth-search STAC (S2) ──render_s2──▶ scenes/s2/…/{zone,mtn}_{rgb,swir,mask}.png + meta.json
                                        │        └─snow_index──▶ snow_index.json, _evaluable.png
GIBS WMS (VIIRS) ──render_viirs──▶ scenes/viirs/…            │
Planetary Computer (S1) ──render_s1──▶ scenes/s1/…           │
                                        └──pack_scenes──▶ sprite.jpg на сцену + {s2,s1,viirs}_index.json
                                                                                  │
build_report --timeline-only ──▶ timeline.json  ◀──────────────────────────────────┘
        │                              │
        │                     agent_daily --auto ──▶ agent/notes/<дата>.json   (Fable 5)
        ▼                              ▼
build_report ──▶ osadki-monitoring.html      build_sverka ──▶ sverka-avgust-2026.html
        └────────────▶ analysis/viewer/build_dist.py ──▶ dist/ ──▶ scripts/deploy_private.sh
```

Лента `timeline.json` — единственный вход для страницы и для агента: всё, что видит человек, видит и модель.

## 4. Шаги конвейера (`run_daily.py`)

Порядок и подписи шагов — список `STEPS` в `run_daily.py`; эти же подписи показываются на странице в статус-баре
(берутся из кода, не из журнала, чтобы старые формулировки не всплывали).

| Шаг | Что делает | Особенности / грабли |
|---|---|---|
| `fetch_model` | `ecmwf_ifs` с `cell_selection=nearest` (одна ячейка 39.47276, 73.56847, ~4775 м для всех высот), `elevation=` меняет только температуру (0,65 К/100 м), осадки общие; почасовая фаза осадков по температуре на высоте; вилка GFS/ICON/UKMO/JMA/ARPEGE; ансамбль `ecmwf_ifs025` (P≥1 мм, P≥5 мм) | «прошедшие сутки» = склейка кратчайших прогнозов, не наблюдение; `snowfall_sum` не используем |
| `fetch_stations` | OGIMET `gsynres` по 2 станциям за 7 дней: tmax/tmin, осадки, снег по ww, облачность | `hora` не может быть в будущем — иначе HTTP 400 (берём текущий час UTC, округлённый до 3 ч); пауза 15 с между запросами; теги HTML в верхнем регистре — парсить с `re.I` |
| `render_s2` | Поиск сцен STAC (тайл T43SCD, орбиты R048/R091, повтор 2–3 дня, задержка 3–8 ч), чтение COG-окон (`AWS_NO_SIGN_REQUEST`), NDSI=(B03−B11)/(B03+B11), маска SCL, подписи поясов | сетка зоны выровнена по сетке индекса; повторный запуск пропускает уже готовые сцены |
| `render_viirs` | GIBS WMS `VIIRS_NOAA20_CorrectedReflectance_{TrueColor,BandsM11-I2-I1}`, охваты wide/near | снимок дня появляется через несколько часов после пролёта (~08:00 UTC) |
| `render_s1` | RTC VV/VH → дБ, сглаживание 7×7, разница к предыдущему пролёту того же трека, серым при |Δ|<1,5 дБ | без сглаживания разница — спекл-шум |
| `snow_index` | см. §5 | требует `data/dem/hma8m_kurumdy.tif` (NASA HMA 8 м) и venv с rasterio |
| `pack_scenes` | по 3 панели в ряд в один JPEG; в `meta.json` пишет `sprite: {file, w, h, panels: {name: [x,y,w,h]}}` | страница режет спрайт через CSS `background-position` |
| `timeline` | `build_report.py --timeline-only` → `timeline.json` | дни раньше 2026-08-01 отбрасываются |
| `agent` | `agent_daily.py --auto` | см. §6; шаг платный; `--no-agent` пропускает |
| `build_report` | страница «Снег и погода» | inline SVG-графики, CSS/JS из `report.css/js` |
| `build_sverka` | страница-сверка | очевидец и вердикты зашиты в словарях `AE`/`VERDICT` в коде |
| `build_dist` | `analysis/viewer/build_dist.py` — копирует страницы (список `PAGES`) и картинки в `dist/`, пути делает относительными | ~30 с; ~19,9 тыс. файлов, +2/день |
| `deploy` | `scripts/deploy_private.sh` | ~40 с инкрементально; `--no-deploy` пропускает |

Ошибка шага не останавливает остальные, кроме `build_dist`; «ok» прогона снимается при падении
`fetch_model`, `snow_index`, `timeline`, `build_report`, `build_dist`. Журнал: `data/runs.json` (последние 60
прогонов; каждый шаг — `ok/secs/label/tail/err`), `data/runs.log`, `data/launchd.log`.

## 5. Индекс снега v2 (кратко; методика и аудит — osadki-monitoring.md §8.1–8.2)

Главная величина на странице — **«закрыто снегом, % от камней-эталона»**: какая доля пикселей, бывших камнями на
самой бесснежной ясной дате (эталон `S2C_43SCD_20260820_0_L2A`), сейчас под снегом. 0 % — как 20.08, 100 % —
все закрыты. Считается на сетке 20 м (UTM 43N) на четырёх единицах (пояса по DEM HMA 8 м) только по
«оцениваемому множеству» E: освещено солнцем и не в отбрасываемой тени на контрольную дату 5.10.2026 05:58 UTC
(солнце по формулам NOAA; азимут сверен со STAC `view:sun_azimuth`), не тёмное на эталоне. Облака: SCL 8/9/10,
класс 3 — только при реальном потемнении. Число публикуется только при покрытии единицы ≥95 %. Дополнительно:
камни при порогах NDSI 0,3/0,4/0,5, доля промежуточных NDSI, разбивка север/прочие склоны. Оцениваемых
точек: рюкзак 310, склон 3107, зона 2016, контроль 298. Погрешность одного значения ±3–5 п.п.; надёжным
считается изменение ≥8 п.п., совпавшее на двух единицах (`trend_from()` в `build_report.py`).

Формат `snow_index.json`: `meta` (ref, grid_utm, sep_check, sun_sep, units{n, n_north, elev, n_ref_rock}),
`scenes[]` по сценам: `units[unit].{coverage, all/north/other:{rock30,rock40,rock50,covered_ref,ndsi_mid_share}}`.

## 6. ИИ-агент (`agent_daily.py`)

- SDK `anthropic` (venv), `client.messages.stream(model="claude-fable-5", output_config={"effort": "high"},
  max_tokens=6000)`, системный промпт `SYSTEM` с шестью жёсткими правилами (числа только из данных; снимки
  описывать отдельно от расчётов; нет данных — так и писать; без советов по безопасности; по-русски без жаргона;
  всегда уровень доверия) и строгой JSON-схемой: `summary, trend, trend_basis, image_observations,
  model_vs_fact, confidence, confidence_why, flags[], watch_next`.
- Вход: `timeline.json` за 10 дней до даты (+5 дней прогноза, только для «сегодня»), журнал
  `fakt-vs-model.tsv`, до 6 картинок (два последних ясных S2: RGB и маска зоны; последняя разница радара; VIIRS
  за дату; карта оцениваемых пикселей). Из ленты убираются чужие заметки агента (`ai`) — модель оценивает данные,
  а не свои прошлые тексты.
- Ключ: переменные `ANTHROPIC_API_KEY_2` → `ANTHROPIC_API_KEY` из окружения, иначе из
  `~/.hw-workspace/secrets.env` (как в hw-all `dd-workflow/scripts/driver-claude.ts`). **Ключ не печатать в логи и
  транскрипты.**
- Даты: `--auto` (шаг конвейера) = заметка на сегодня по Бишкеку (пересоздаётся каждым прогоном — данные
  меняются) + на дату последнего снимка S2, если у неё заметки ещё нет. `--date YYYY-MM-DD` для прошлой даты =
  заметка «задним числом»: лента обрезана по дате, журнал обрезан по дате, дни после не показываются, в JSON
  `backfill: true`, на странице пометка. `--dry-run` печатает состав входа без вызова API.
- Выход `agent/notes/<дата>.json`: `ok`, `model`, `usage{in,out}`, `stop_reason`, `images[]` (подписи),
  `raw` (ответ целиком), поля схемы; при отказе/не-JSON — `ok:false` и `error`, страница показывает причину.
- Расход: 9–27 тыс. входных / 1,3–2,6 тыс. выходных токенов на заметку. История августа (12 дат снимков)
  дописана 28.08.
- Отказы и ошибки API не маскируются другой моделью — это осознанное решение оператора.

## 7. Запуск руками, расписание, отладка

```bash
# полный прогон без публикации и без платного агента
analysis/.venv/bin/python analysis/osadki/run_daily.py --no-deploy --no-agent
# отдельные шаги (имена — из STEPS)
analysis/.venv/bin/python analysis/osadki/run_daily.py --only timeline,build_report,build_dist
# агент: как в конвейере / за конкретную дату / без вызова API
analysis/.venv/bin/python analysis/osadki/agent_daily.py --auto
analysis/.venv/bin/python analysis/osadki/agent_daily.py --date 2026-08-27
analysis/.venv/bin/python analysis/osadki/agent_daily.py --date 2026-08-27 --dry-run
# климатология (редко, вручную)
python3 analysis/osadki/fetch_clim.py
# локальный просмотр
python3 -m http.server 8077 -d . ; open http://localhost:8077/analysis/viewer/osadki-monitoring.html#date=2026-08-27
```

launchd: `launchctl list | grep osadki` (должно быть `com.alp-finder.osadki`); переустановка —
`launchctl unload ~/Library/LaunchAgents/com.alp-finder.osadki.plist; cp scripts/com.alp-finder.osadki.plist
~/Library/LaunchAgents/; launchctl load ~/Library/LaunchAgents/com.alp-finder.osadki.plist`; разовый запуск
по расписанию — `launchctl start com.alp-finder.osadki`. Mac должен быть включён и не спать в 05:00/15:00 по
своим часам; если часовой пояс Mac изменится, часы в plist пересчитать (цель — 08:00/18:00 Бишкека, UTC+6).

Долгие шаги из ассистента запускать отсоединёнными (`os.fork()+os.setsid()` или фоновая задача с логом) —
таймаут инструмента 10 мин убивает цепочку. Визуальная проверка страниц — headless Chrome:
`"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --window-size=1200,4600 --screenshot=out.png "http://localhost:8079/analysis/viewer/osadki-monitoring.html#date=2026-08-27"`.

Типовые сбои: OGIMET 400 (`hora` в будущем); STAC/COG таймауты (повторить шаг); Planetary Computer требует
SAS-подпись каждого href; Python 3.9 системный не умеет вложенные кавычки в f-строках (страницы собираются
системным `python3`, растровые шаги — venv); `cd` в составных командах сбивает cwd — использовать абсолютные пути.

## 8. Страницы и UI

`osadki-monitoring.html` (v2): статус последнего запуска (из `runs.json`, подписи шагов из `STEPS`) → 1. Коротко
сегодня (KPI с погрешностями) → 2. День за днём (лента дней: столбик осадков, Tmax на 4663 м, точки: зелёная —
ясный S2, серая — S2 в облаках, фиолетовая — радар, жёлтая — есть оценка ИИ; карточка дня: модель по высотам,
станции, индекс, ⚙️ расчёт, ✨ оценка агента или заглушка «оценка не создавалась»; просмотрщик снимков S2 3×3 /
вся гора / радар / VIIRS из спрайтов) → 3. Графики (закрыто снегом; осадки с вилкой моделей; температура по
высотам с лентой разброса; климатология IFS) → 4. Как считается индекс → 5. Как это работает → 6. Погрешности
и уровень доверия → 7. Словарик. Глубокие ссылки `#date=YYYY-MM-DD`, стрелки ← → на клавиатуре.

Пункты меню «Снег и погода» и «Отчёты» добавлены во все страницы и во все генераторы (`build_viewer.py`,
`build_map.py`, `build_coverage3d.py`, `build_ortho3d.py`, статические `montages.html`, `ortho3d.html`,
`coverage-3d.html`, `index.html`, `map.html`); `build_dist.py` `PAGES` включает `osadki-monitoring.html`,
`sverka-avgust-2026.html`, `otchety.html`. Палитра графиков проверена валидатором dataviz.

## 9. Публикация и проверки перед ней

`python3 analysis/viewer/build_dist.py` → `bash scripts/deploy_private.sh` (это и делает шаг `deploy`).
Хостинг с 28.08 — Cloudflare Worker со статическими ассетами (лимит 100 000 файлов на Workers Paid; Cloudflare
Pages упёрся в 20 000 — из-за этого сцены упакованы в спрайты). Адрес, пароль, конфиг воркера и код
`_worker.js` — только в локальных файлах (`docs/zakrytaya-zona.local.md`, `CLAUDE.local.md`, `.gitignore`).
Публичные адреса (`alp-finder.pages.dev`, GitHub Pages) **не обновлять** без явной команды оператора.

Перед деплоем: `grep -il "тим-верси\|закрытый контур\|закрытая верси\|basic auth\|парол" analysis/viewer/dist/*.html
analysis/osadki/report.js` должен быть пустым; помнить, что `runs.json` (подписи шагов) тоже попадает на страницу.

## 10. Что в git, что нет

- В git: весь код `analysis/osadki/*.py`, `report.css/js`, `agent/notes/*.json`, `fakt-vs-model.tsv`,
  `seed/alt_*.json`, скрипты в `scripts/`, страницы `analysis/viewer/*.html`, документация.
- Не в git (регенерируется): `analysis/osadki/data/` (правило `data/`), `analysis/osadki/scenes/`,
  `analysis/viewer/dist/`, `*.log`. После свежего клона: venv + DEM (README «Развёртывание»,
  `analysis/build_hma_dem.py` для HMA 8 м) → `run_daily.py --no-deploy --no-agent` восстановит всё за ~10–20 мин
  (снимки августа скачаются заново).
- Режим работы с ветками и push — по `CLAUDE.local.md` (локальный файл; до его снятия — только локальные коммиты
  в `team-only`).

## 11. Известные ограничения и что дальше

- Модель занижает/пропускает короткие осадки (журнал 13, 14, 16.08); нулевые дни трактовать осторожно.
- На 5435 м знак дневной температуры модели не согласуют — «тает ли от воздуха в зоне» по модели не решается.
- Индекс работает только на ясных снимках с покрытием ≥95 %; между ними — радар (качественно) и VIIRS (грубо).
- К октябрю растёт доля тени — множество E выбрано так, чтобы оставаться освещённым до 5.10; после — пересмотреть.
- Удалены 28.08 как устаревшие: `build_report_v1.py` (страница v1) и `rock_zones.py` (индекс v1 в квадратных окнах,
  снят аудитом; его результаты остались в osadki-monitoring.md §7.2 как история).
- Не сделано (обсуждалось, не заказано): рассылка в Telegram; вынос панорам/полётов в R2; отключение старого
  Pages-проекта; сводный итог к 15.09 (по накопленной истории).

## 12. Хронология

- 27.08 — рисерч всех источников (6 отчётов), первая эмпирика (S2 «доля камней», модели за август,
  климатология), страница v1, уточнение интента штаба.
- 28.08 день — два независимых аудита (метео, спутник) → индекс v2, переписан `fetch_model`, страница v2,
  конвейер `run_daily.py`, launchd, агент (вариант B), сверка с очевидцем, меню «Отчёты».
- 28.08 вечер — переезд хостинга на Worker (лимит файлов), спрайты сцен, чистка упоминаний контура в UI,
  переименование подписей блоков, привязка заметок агента к дате снимка и дозаполнение истории августа,
  журнал и seed вынесены из игнорируемого `data/`, этот справочник.
