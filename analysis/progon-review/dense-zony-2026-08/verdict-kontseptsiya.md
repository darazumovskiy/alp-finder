# Вердикт: концепция решения

## 1. Продуктовая задача
- Вердикт: ПРОВЕРЕНО
- Уверенность: высокая — каждое утверждение раздела нашлось в первоисточниках, кроме одной цитаты оператора.
- Как проверял: сверил с `AGENTS.md`, `docs/video-analysis.md` (дефекты мозаики 2 и 3а — смаз стен вертикальной проекцией и дубли объектов из-за ошибок привязки лоскутов 7–30 м, автоякорение честно провалено тремя способами 19.08), `docs/kontseptsiya-3d-vizualizatsii.md` (иерархия честности, потребитель «Полёт 3D»), `docs/nezavisimyy-analiz/model-3d/README.md` (референсная модель вещей: 1,54 млн точек, невязки на лазере +3,3/+2,5 м).
- Потребность реальна: мозаика документированно врёт на стенах и дублирует предметы, спутниковый рельеф на этом склоне ошибается до ±40 м, а плотная модель — единственное представление в проекте, где рельеф построен самими кадрами. Единственное, чего нет в доках, — дословный вердикт оператора «лучшее, что было» (видимо, из чата), но на вывод это не влияет: превосходство модели над мозаикой задокументировано объективно.

## 2. Концепция решает задачу
- Вердикт: ПРОВЕРЕНО
- Уверенность: высокая — метод уже дал заявленный результат в этом же проекте на этих же параметрах, вход проверен независимым пересчётом.
- Как проверял: `analysis/scene3d/README.md` (референсный конвейер: те же команды PatchMatch/fusion, те же параметры geom_consistency + max_image_size 1600), `docs/scene3d-metodologiya.md` (почему сплаты забракованы — аудит 18.08 нашёл отброшенную дисторсию и 40% брака зон), сам пересчитал таблицу качества входа pycolmap'ом по трём зонам (linia-05: 27 камер, 0.67 пкс, трек 7.3; koshki-1608: 134, 0.86, 6.3; gora-obzor: 217, 0.96, 9.4) — совпало с заявкой до сотых. Проверил на диске: 14 рабочих пространств `dense/` существуют, суммарный объём 1 770 248 КиБ — ровно как в заявке.
- Отказ от альтернатив подтверждён фактами доков, а не вкусом: сплаты (аудит), натяжка на DEM (±40 м на стенах), автоякорение (трёхкратный провал). Важная честная деталь концепции подтверждается референсом: репроекции зон (0.54–1.03 пкс) хуже референсных 0.41 пкс, и часть зон может дать шумные облака — критерий «≥12 из 14» это закладывает, а геометрическая консистентность PatchMatch отбрасывает несогласованные пиксели, а не выдумывает их.

## 3. Допущения
- Вердикт: ПРОВЕРЕНО
- Уверенность: высокая — все существенные допущения либо проверены до трат, либо накрыты тест-прогоном, либо их провал не сжигает оплаченное.
- Как проверял: выписал допущения из заявки и сверил каждое с доками и данными.
- Список: (а) позы sparse-моделей верны — низкая репроекция не гарантирует этого полностью (урок аудита сплатов: красивая метрика у мусорной модели), но провал даёт шумное облако конкретной зоны, что закрыто критерием ≥12/14 и не заражает остальные; (б) скорость 0.413 мин/кадр переносится с 4070 Ti на 4090 — консервативно и калибруется тест-прогоном за $0.35–0.46; (в) время fusion 3–5 мин/зона — заявка сама помечает как допущение без первоисточника, тест его замеряет; (г) порода стабильна между днями — держится на факте сшивки мультиклиповых sparse-моделей, а провал в снежных пятнах даст честные дыры, не ложь; (д) геопривязка подобием по телеметрии даст пригодное размещение вставок — для 5 linia-зон заведомо грубая (RMS 23–104 м), заявка это честно признаёт; критично: провал привязки не обесценивает оплаченное облако — оно в системе sparse-модели и перепривязывается бесплатно позже; (е) адаптер drape→вставка — новая домашняя бесплатная работа, деньги от неё не зависят.

## 4. Грабли проекта
- Вердикт: ПРОВЕРЕНО
- Уверенность: высокая — прошёл каталог `docs/koordinaty-status.md` пункт за пунктом.
- Как проверял: `docs/koordinaty-status.md`, `docs/dem-patches.md`, `docs/focal-length-calibration.md` (по ссылкам), README модели вещей.
- Ни одна известная грабля не повторяется: фокусные из EXIF/SRT не используются (SfM самокалибруется, SIMPLE_RADIAL на кадр); дисторсия не отбрасывается (андисторт кадров сделан — это исправление главной ошибки сплатов); от DEM метод уходит принципиально (в этом его смысл); датумная разница эллипсоид/геоид 33,2 м знается и снимается медианным вертикальным совмещением на вставке — тем же приёмом, что обкатан 19.08 на модели вещей; ошибка «датумного сдвига −29,8 м» не повторяется; вырожденные зависания (нет базиса → масштаб не определён) — отбраковка задокументирована и заложена в критерий успеха. Порог 20 камер реально работает: проверил, что linia-02 исключена именно поэтому (её лучшая модель — 9 камер).

## 5. Альтернативы
- Вердикт: ПРОВЕРЕНО
- Уверенность: высокая — бесплатный путь существует, назван в заявке и отложен решением владельца денег, а не исполнителя.
- Как проверял: `docs/zadanie-cc-dense-zony-2026-08-19.md` (задание волонтёру с CUDA оформлено, канал передачи в закрытом контуре не согласован), `docs/kontseptsiya-3d-vizualizatsii.md` (выбор «C C или аренда — решение оператора» зафиксирован ещё 18.08).
- Существенно более дешёвого пути нет: локально CUDA отсутствует (PatchMatch — единственный CUDA-этап), CPU-альтернатива — дни счёта, урезание объёма экономит доли от $5–13. Единственное наблюдение не в ущерб вердикту: зона veshchi-15 частично перекрывает уже готовую модель вещей (клип 163855, 13.08) — но она строится по другому, лучшему материалу 15.08, и её доля в смете ~4%.

## 6. Встраивание результата
- Вердикт: ПРОВЕРЕНО
- Уверенность: средняя — потребитель и механика реальны и обкатаны, но один стык (адаптер drape→вставка) ещё не написан.
- Как проверял: `docs/video-analysis.md` «Детальные вставки сцен» (mesh-вставка модели вещей в «Полёт 3D» работает с 19.08: продавливание рельефа движка, вертикальный якорь, ленивые галки), `analysis/scene3d/README.md` (drape.py самодостаточен), шапки `prep_dense.py` и `run_dense_all.sh` (домашняя цепочка после возврата облаков прописана в самих скриптах).
- Выход платной части — `fused.ply` в системе sparse-модели зоны — ровно тот вход, который ест домашняя цепочка привязка→поверхность→вставка, уже пройденная на модели вещей. Мёртвым артефактом облака не станут: даже при затыке адаптера они переиспользуемы без пересчёта. Признанная дыра: текущий `scene3d_insert.py` читает вход из HTML модели вещей, адаптер под выход drape.py — новая работа; она бесплатная и в объём заявки включена.

## Итог
- **ГОДЕН С ОГОВОРКАМИ**
- Общая уверенность: высокая
- Оговорки:
  1. **Грубая привязка 5 зон линии падения** (linia-05/09/10/11/12, RMS 23–104 м): их вставки могут стоять на десятки метров не там — для планирования облёта эти зоны использовать только с пометкой «грубо» в интерфейсе/отчёте. Снимается перепривязкой по большему числу кадров (бесплатно, облака пересчёта не требуют).
  2. **Дыра линии падения не закроется**: linia-02/03/04 в прогон не входят (sparse-моделей нет или 9 камер) — в отчёте прогона явно указать разрыв покрытия, чтобы дыру не приняли за осмотренное. Снимается строкой в отчёте.
  3. **Приватность**: заливка 1,7 ГиБ кадров района на арендованную машину допустима только с явным аппрувом оператора, который заявка и требует. Снимается самим аппрувом (он же закрывает непроверяемые по докам ссылки на устные решения оператора 19.08).
  4. Рекомендация без блокировки: после тест-прогона прогнать домашнюю цепочку (привязка + drape + адаптер) на `fused.ply` linia-05 параллельно полной очереди — дешёвая ранняя проверка единственного необкатанного стыка.

The operator quote isn't verifiable from docs but seems low-stakes given the documented product need. I'm also noting that veshchi-15 might overlap with the existing model's coverage from the 13.08 clip, since both cover the same "вещи" area — this could mean it's a duplicate rather than new coverage, though the 15.08 footage was apparently better quality.

Now I'm listing out the key assumptions for section 3: sparse-model poses being correct (backed by geometric consistency and the ≥12/14 success criterion, though only partially tested via the linia-05 test run), the 0.413 min/frame speed transferring to the 4090 and other zones, the 3-5 min/zone fusion estimate being an acknowledged unverified assumption that the test will measure, terrain/snow stability within zones (if wrong, this just creates holes rather than false data), and the weakest point — geopривязка by similarity via telemetry for the 5 linia zones, which has known poor RMS (23-104m) and isn't tested by the paid run itself, meaning if it fails the insert would be placed incorrectly.

I'm also noting that the cloud computation cost isn't wasted even if geopривязка fails, since the cloud can be reused later with better alignment. The drape→insert adapter is new unpaid work, so if it proves difficult, clouds simply wait with no money lost. I'm checking that drape.py's standalone home steps are self-sufficient per the README, while build_scene3d.py isn't portable — but since the заявка uses drape.py directly and v3 already excluded fuse_dense.py, this should hold, though there's residual risk drape.py needs more inputs than just fused.ply, like camera data for texture projection, which requires math review. I'm also confirming that having just the fused.ply point cloud (without depth maps) should be sufficient, since the chosen colmap stereo_fusion path produces everything drape needs — point cloud, poses, and images.

Going through the known pitfalls checklist: datum issues (ellipsoid/geoid offset) are handled via median alignment, EXIF/SRT focal lengths aren't needed since SfM self-calibrates, and the method avoids DEM-related errors entirely. The previous datum-shift error won't recur, shallow-ray issues don't apply here, and GPS drift between clips is acknowledged as a risk for multi-clip georeferencing zones, flagged via RMS checks. I'm also confirming degenerate models with undefined scale get filtered out through minimum threshold checks.

My overall verdict: PASS WITH CAVEATS. The caveats are: rough georeferencing on 5 line-zones (RMS 23-104m) means those insertions could be off by tens of meters, so they need flagging for flight planning and re-anchoring with more frames before real use; privacy concerns around uploading area footage to external machines require explicit operator approval already built into the request, with approval as the resolution; and an unclosed gap in the fall-line coverage since linia-02/03/04 won't have usable sparse reconstructions — this needs to be honestly reported as a coverage gap so command doesn't mistake the hole for inspected terrain.

Additional notes: the "best available" and operator's 19.08 choice aren't verifiable from documentation but the approval gate in the plan covers this; and I'm considering recommending a quick test run of the home pipeline (georef+drape+adapter) on a test fused.ply from linia-05 before or alongside the full queue — cheap validation of format compatibility, though it's more of a soft suggestion than a hard condition since the full queue only costs $5 extra and running the home chain first just adds maybe an hour delay.

Now I'll write the final verdict in the required format across the six checklist sections, concluding PASS WITH CAVEATS at high/medium confidence.
