#!/usr/bin/env python3
"""Страница-сверка «Август 2026: спутник и модель против очевидца».

Вход: data/s2_index.json (render_s2.py), data/viirs_index.json (render_viirs.py),
data/s1_index.json (render_s1.py), seed/alt_*.json (сырые ответы Open-Meteo от 27.08 — снимок для сверки,
скрипта-генератора нет, поэтому лежат вне игнорируемого data/), наблюдения очевидца (ниже).
Выход: analysis/viewer/sverka-avgust-2026.html. Картинки — из analysis/osadki/scenes/ (build_dist копирует).
"""
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "analysis/osadki/data"
SEED = ROOT / "analysis/osadki/seed"
OUT = ROOT / "analysis/viewer/sverka-avgust-2026.html"
SC = "/analysis/osadki/scenes"

C_RYU, C_ZONA, C_C1 = "#d95926", "#3987e5", "#199e70"


def load(name, default=None):
    p = DATA / name
    return json.load(open(p)) if p.exists() else default


s2 = load("s2_index.json", [])
_si = load("snow_index.json", {"scenes": []})
IDX = {(m["date"], m["sat"]): m["units"] for m in _si["scenes"]}
UN = [("ryukzak_4600-4900", "рюкзак 4600–4900 м", C_RYU), ("zona_5200-5500", "зона 5200–5500 м", C_ZONA), ("ctrl_5099", "контрольная точка 5099 м", C_C1)]


def cov_cell(u):
    if not u or (u.get("coverage") or 0) < 0.95 or u.get("all", {}).get("covered_ref") is None:
        return "<span class='sub'>— (облака, покрытие %d %%)</span>" % round((u or {}).get("coverage", 0) * 100)
    a = u["all"]
    return f"<b>{a['covered_ref']:.0f} %</b> <span class='sub'>камней при порогах 0,3–0,5: {a.get('rock30', 0):.0f}–{a.get('rock50', 0):.0f} %</span>"
viirs = {m["date"]: m for m in load("viirs_index.json", [])}
s1 = load("s1_index.json", [])
alt = {e: json.load(open(SEED / f"alt_{e}.json"))["daily"] for e in (4663, 5435)}
days = alt[4663]["time"]
idx = {d: i for i, d in enumerate(days)}

# ---- наблюдения очевидца (AE, оператор дрона, был на горе 11–16.08; TG 28.08.2026) ----
AE = {
    "2026-08-11": ("Солнца не было весь день. Осадки шли примерно 4 часа. На 4000 м это был в основном дождь, на 4500 м — чаще снег.", "пасмурно", "осадки ~4 ч"),
    "2026-08-12": ("То же: без солнца, осадки около 4 часов, внизу дождь, выше снег.", "пасмурно", "осадки ~4 ч"),
    "2026-08-13": ("Солнце с рассвета до 12:00, потом закрыло. Осадки короче — около 2 часов.", "солнце до 12:00", "осадки ~2 ч"),
    "2026-08-14": ("Солнца не было. Осадки около 4 часов. Штаб: после двух вылетов облёт отменили из-за снегопада.", "пасмурно", "осадки ~4 ч, снегопад"),
    "2026-08-15": ("Единственный полностью солнечный день. Осадков не было. «Снег уходил на глазах, всё становилось более открытым».", "солнце весь день", "сухо"),
    "2026-08-16": ("Солнце до 12:00, осадки около 2 часов. Утром снег продолжал сходить.", "солнце до 12:00", "осадки ~2 ч"),
    "2026-08-17": ("AE уже уехал; наблюдений с горы нет.", "—", "—"),
}
CONTROL = "Контрольная точка 5099 м (39.481279, 73.592673, три камня на склоне): на кадрах дрона 15.08 снега больше, чем днём 14.08."

# ---- вердикты по дням (написаны человеком по данным ниже; см. раздел «Выводы») ----
VERDICT = {
    "2026-08-11": ("ok", "Сходится", "Модель даёт заметные осадки (4,5 мм) и +2 °C днём на высоте рюкзака — это и есть «внизу дождь, выше снег». Снимка нет: пролёта в этот день не было."),
    "2026-08-12": ("ok", "Сходится", "Модель: 3,4 мм. Спутник в 11:50 увидел только облака (98 % тайла закрыто) — подтверждает «без солнца». Про снег на склоне снимок ничего не говорит."),
    "2026-08-13": ("warn", "Модель занижает", "Очевидец видел ~2 часа осадков. ECMWF дала всего 0,4 мм, но показала их именно после полудня (15–17 ч), как и очевидец; другие модели дали 3,5 мм (GFS) и 6,6 мм (UKMO). Суточный ход модели видят верно, количество ECMWF занижает. Пролёта Sentinel-2 не было; VIIRS (13:30) показывает, было ли закрыто после полудня."),
    "2026-08-14": ("bad", "Модель занижает сильно", "Очевидец: ~4 часа осадков, снегопад сорвал облёт. ECMWF: 0,6 мм — осадки с 13 до 19 ч, но по 0,1 мм в час; ICON 5,2 мм (17–23 ч), UKMO 5,9 мм, GFS 2,3 мм. Модели видят, что вечером шёл снег, но ECMWF занижает количество в разы. Пролёта Sentinel-2 не было — сам снегопад из космоса не виден, только его результат утром 15-го."),
    "2026-08-15": ("ok", "Сходится с очевидцем и с контрольной точкой", "Модель: 0,0 мм — верно, у всех моделей 15-е сухое. Снимок в 11:50 ясный, но склоны выше 5000 м припорошены: в зоне интереса закрыто снегом 87 % камней-эталона, в контрольной точке — 10 %, в поясе рюкзака — 6 % (0 % — как на самой бесснежной дате 20.08). Это снег вечера и ночи 14→15, который модели показали слабым, — ровно то, что зафиксировано в контрольной точке. На высоте рюкзака днём +2,6 °C, свежий снег там не удержался. Таяние «на глазах» в течение дня спутник увидеть не мог: он снимает один раз, в полдень."),
    "2026-08-16": ("warn", "Модель занизила", "Очевидец: ~2 часа осадков после солнечного утра; ECMWF 0,0 мм, GFS 0,9 мм, UKMO 1,8 мм (17–22 ч). Небольшое событие: ECMWF его не видит, часть моделей видит. Пролёта не было."),
    "2026-08-17": ("ok", "Спутник подтверждает «вытаяло 15–16»", "Снимок частично облачный, но площадки чистые на ≥95 %: закрыто снегом в зоне 23 % (было 87 % утром 15-го), в контрольной точке 0 % (было 10 %), в поясе рюкзака 0 % (было 6 %). Снег ночи 14→15 за два солнечных дня в основном сошёл — как и описывал очевидец. Это единственная пара ясных снимков за неделю, поэтому «сошло за два дня» — вывод по одной паре."),
}


def dlabel(d):
    x = dt.date.fromisoformat(d)
    wd = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"][x.weekday()]
    return f"{x.day} августа, {wd}"


def model_row(d):
    i = idx.get(d)
    if i is None:
        return None
    p = alt[4663]["precipitation_sum"][i] or 0
    t46 = alt[4663]["temperature_2m_max"][i]
    t54 = alt[5435]["temperature_2m_max"][i]
    return p, t46, t54


def fmt_t(v):
    if v is None:
        return "—"
    v = 0.0 if abs(v) < 0.05 else v
    return f"{v:+.1f}".replace("+0.0", "0,0").replace(".", ",") + " °C"


def s2_for(d):
    return [m for m in s2 if m["date"] == d]


def s1_for(d):
    return [m for m in s1 if m["date"] == d]


def rock_cell(z):
    if not z or z.get("rock") is None:
        return "<span class='sub'>не видно (облака)</span>"
    r = z["rock"]; cf = z["cloudfree"]
    tag = "" if cf >= 60 else " <span class='sub' title='площадка частично под облаками'>(облачно, ненадёжно)</span>"
    return f"<b>{r:.0f} %</b>{tag}"


def img(src, cap, w="1 1 320px"):
    return (f"<figure style='flex:{w}'><a href=\"{src}\" target='_blank'><img loading='lazy' src=\"{src}\" alt='{cap}'></a>"
            f"<figcaption>{cap}</figcaption></figure>")


def spr(meta, kind, folder, panel, cap, w="1 1 320px"):
    """Панель из спрайта сцены (один файл на сцену — лимит файлов Cloudflare Pages)."""
    sp = meta.get("sprite")
    if not sp or panel not in sp["panels"]:
        return ""
    x, y, pw, ph = sp["panels"][panel]; W, H = sp["w"], sp["h"]
    src = f"{SC}/{kind}/{folder}/{sp['file']}"
    bs = W / pw * 100; px = x / (W - pw) * 100 if W > pw else 0; py = y / (H - ph) * 100 if H > ph else 0
    return (f"<figure style='flex:{w}'><a href=\"{src}\" target='_blank' title='открыть все панели'><div class='spv' style=\"aspect-ratio:{pw}/{ph};background-image:url('{src}');background-size:{bs:.3f}% auto;background-position:{px:.3f}% {py:.3f}%\"></div></a>"
            f"<figcaption>{cap}</figcaption></figure>")


def day_card(d):
    ae_text, sun, prec = AE[d]
    kind, title, comment = VERDICT[d]
    mr = model_row(d)
    scenes = s2_for(d)
    v = viirs.get(d)
    passes = s1_for(d)
    parts = [f"<section class='day {kind}' id='d{d[8:10]}'>",
             f"<h3>{dlabel(d)} <span class='badge {kind}'>{title}</span></h3>",
             "<div class='cols'>"]
    # левая колонка: очевидец + модель
    parts.append("<div class='col'>")
    parts.append(f"<div class='blk'><div class='lbl'>👁 Очевидец (AE, на горе)</div><p>{ae_text}</p>"
                 f"<p class='sub'>Небо: <b>{sun}</b> · Осадки: <b>{prec}</b></p></div>")
    if mr:
        p, t46, t54 = mr
        phase = "внизу дождь / мокрый снег, выше снег" if (t46 is not None and t46 > 0.5) else "снег на всех высотах"
        parts.append(f"<div class='blk'><div class='lbl'>🖥 Погодная модель (ECMWF, 9 км)</div>"
                     f"<p>Осадки за сутки: <b>{p:.1f} мм</b>{' — практически сухо' if p < 1 else ''}<br>"
                     f"Днём на 4660 м (рюкзак): <b>{fmt_t(t46)}</b> · на 5435 м (зона): <b>{fmt_t(t54)}</b><br>"
                     f"<span class='sub'>Значит: {phase}. Осадки модели — ±100 %, температура — ±2 °C.</span></p></div>")
    if d == "2026-08-15":
        parts.append(f"<div class='blk ctrl'><div class='lbl'>📍 Контрольная точка</div><p>{CONTROL}</p></div>")
    parts.append("</div>")  # col
    # правая колонка: снимки
    parts.append("<div class='col'>")
    if scenes:
        for m in scenes:
            base = f"{SC}/s2/{m['date']}_{m['sat']}"
            fold = f"{m['date']}_{m['sat']}"
            z = IDX.get((m["date"], m["sat"]), {})
            parts.append(f"<div class='blk'><div class='lbl'>🛰 Sentinel-2, {m['time_utc']} UTC (≈{int(m['time_utc'][:2])+6}:{m['time_utc'][3:]} местного) · облачность тайла {m['tile_cloud']} %, площадка чистая на {m.get('zone_cloudfree', '?')} %</div>"
                         f"<div class='imgrow'>{spr(m, 's2', fold, 'zone_rgb', 'Обычные цвета, 3×3 км. Цветом — площадки индекса: оранжевая — пояс рюкзака 4600–4900, жёлтая — склон 4900–5200, синяя — зона 5200–5500, красная — контрольная точка')}"
                         f"{spr(m, 's2', fold, 'zone_mask', 'Маска: белое — снег, коричневое — камень, серое — облако/тень. По ней и считается индекс')}</div>"
                         f"<table class='meta'><tr><th>Закрыто снегом (% камней-эталона 20.08)</th>" + "".join(f"<th style='color:{c}'>{l}</th>" for _, l, c in UN) + "</tr>"
                         f"<tr><td>на этом снимке</td>" + "".join(f"<td>{cov_cell(z.get(k))}</td>" for k, _, _ in UN) + "</tr></table>"
                         f"<p class='sub'>0 % — так же голо, как 20.08; 100 % — все эталонные камни под снегом. Число только при ≥95 % площадки без облаков. Погрешность одного значения ±3–5 п.п.; надёжны изменения больше ~8 п.п. на двух площадках (см. «Выводы»).</p></div>")
    else:
        parts.append("<div class='blk nos2'><div class='lbl'>🛰 Sentinel-2</div><p>Пролёта в этот день не было (спутник проходит над горой раз в 2–3 дня). Ниже — грубый ежедневный снимок VIIRS вместо него.</p></div>")
    if v and v.get("sprite"):
        parts.append(f"<div class='blk'><div class='lbl'>🌐 VIIRS (NOAA-20), ежедневно ≈13:30 местного, 375 м на пиксель — только «облачно или нет»</div>"
                     f"<div class='imgrow'>{spr(v, 'viirs', d, 'near_fc', 'Ложные цвета: голубое — снег и лёд, белое/розовое — облака, тёмное — камень. Красный кружок — зона интереса', '1 1 260px')}"
                     f"{spr(v, 'viirs', d, 'wide_tc', 'Обычные цвета, 55×45 км: вся Алайская долина и хребет', '1 1 260px')}</div></div>")
    for m in passes:
        if "zone_diff" in m["panels"]:
            prev_lab = (m.get("prev_date") or "")[5:]
            fold1 = f"{m['date']}_{m['orbit']}"
            parts.append(f"<div class='blk'><div class='lbl'>📡 Радар Sentinel-1 ({m['time_utc']} UTC, трек {m['orbit']}) — видит сквозь облака</div>"
                         f"<div class='imgrow'>{spr(m, 's1', fold1, 'zone_diff', f'Изменение сигнала по сравнению с прошлым пролётом того же трека ({prev_lab}): синее — сигнал упал (снег намок / потеплело), красное — вырос (подсохло / свежий сухой снег). Серое — без изменений', '1 1 300px')}</div>"
                         f"<p class='sub'>Радар не показывает толщину снега; на крутых склонах часть площадки искажена. Здесь он — только второе мнение о «мокро/сухо».</p></div>")
    parts.append("</div></div>")  # col, cols
    parts.append(f"<div class='verdict {kind}'><b>Итог дня.</b> {comment}</div>")
    parts.append("</section>")
    return "\n".join(parts)


def strip():
    """Лента всех августовских снимков — чтобы промотать глазами."""
    cells = []
    for m in s2:
        base = f"{SC}/s2/{m['date']}_{m['sat']}"
        z = IDX.get((m["date"], m["sat"]), {}).get("zona_5200-5500") or {}
        r = z.get("all", {}).get("covered_ref"); cf = z.get("coverage") or 0
        lab = "облака" if (r is None or cf < 0.95) else f"зона: закрыто снегом {r:.0f} %"
        sp = m.get("sprite") or {"file": "sprite.jpg", "w": 1, "h": 1, "panels": {"zone_rgb": [0, 0, 1, 1]}}
        x, y, pw, ph = sp["panels"].get("zone_rgb", [0, 0, 1, 1]); W, H = sp["w"], sp["h"]
        st = f"aspect-ratio:1/1;background-image:url('{base}/{sp['file']}');background-size:{W/pw*100:.2f}% auto;background-position:{(x/(W-pw)*100 if W>pw else 0):.2f}% {(y/(H-ph)*100 if H>ph else 0):.2f}%"
        cells.append(f"<a class='cell' href=\"{base}/{sp['file']}\" target='_blank'><div class='spv' style=\"{st}\"></div>"
                     f"<div class='cl'>{m['date'][8:10]}.{m['date'][5:7]} · {m['sat']}<br><span class='sub'>{lab}</span></div></a>")
    return "<div class='strip'>" + "".join(cells) + "</div>"


CSS = """
  :root { --bg:#101216; --panel:#181b21; --panel2:#1e222a; --text:#d7dbe0; --muted:#8b93a0; --accent:#7ab8ff; --line:#2a2f38;
    --ok:#5fd38a; --warn:#ffd200; --bad:#ff6b6b; }
  * { box-sizing:border-box; }
  html,body { margin:0; background:var(--bg); color:var(--text); font:15.5px/1.6 -apple-system,"Segoe UI",Roboto,sans-serif; }
  header { position:sticky; top:0; z-index:5; background:#101216ee; border-bottom:1px solid var(--line); padding:10px 16px; backdrop-filter:blur(6px); }
  header nav { display:flex; gap:16px; flex-wrap:wrap; align-items:baseline; max-width:1100px; margin:0 auto; }
  header .title { font-weight:700; } header a { color:var(--accent); text-decoration:none; }
  main { max-width:1100px; margin:0 auto; padding:20px 16px 80px; }
  h1 { font-size:27px; line-height:1.25; margin:18px 0 6px; }
  h2 { font-size:21px; margin:44px 0 10px; padding-top:14px; border-top:2px solid var(--line); }
  h3 { font-size:19px; margin:0 0 10px; }
  p { margin:8px 0; } .sub { color:var(--muted); } .lead { font-size:17px; }
  .badge { display:inline-block; font-size:13px; padding:2px 10px; border-radius:12px; margin-left:10px; vertical-align:middle; font-weight:600; }
  .badge.ok { background:#1d3b2a; color:var(--ok); } .badge.warn { background:#3b3512; color:var(--warn); } .badge.bad { background:#3b1d1d; color:var(--bad); }
  section.day { background:var(--panel); border:1px solid var(--line); border-left-width:4px; border-radius:12px; padding:16px 18px; margin:18px 0; }
  section.day.ok { border-left-color:var(--ok); } section.day.warn { border-left-color:var(--warn); } section.day.bad { border-left-color:var(--bad); }
  .cols { display:flex; gap:16px; flex-wrap:wrap; } .col { flex:1 1 380px; min-width:300px; }
  .blk { background:var(--panel2); border:1px solid var(--line); border-radius:10px; padding:10px 14px; margin:8px 0; }
  .blk .lbl { font-size:12.5px; color:var(--muted); font-weight:600; margin-bottom:4px; }
  .blk.ctrl { border-color:#5a3b1d; } .blk.nos2 { border-style:dashed; }
  .imgrow { display:flex; gap:10px; flex-wrap:wrap; margin:8px 0; } .imgrow figure { margin:0; min-width:220px; }
  .imgrow img { width:100%; border-radius:8px; border:1px solid var(--line); display:block; background:#000; }
  figcaption { font-size:12.5px; color:var(--muted); padding:4px 2px 0; }
  table.meta { width:100%; border-collapse:collapse; font-size:14px; margin:8px 0 2px; }
  table.meta td, table.meta th { padding:5px 8px; border-top:1px solid var(--line); text-align:left; }
  table.meta th { color:var(--muted); font-weight:600; font-size:12.5px; }
  .verdict { margin-top:12px; padding:10px 14px; border-radius:8px; background:#0d0f13; border:1px solid var(--line); }
  .verdict.ok { border-color:#2a5a3b; } .verdict.warn { border-color:#5a5320; } .verdict.bad { border-color:#5a2a2a; }
  .toc { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px 18px; margin:18px 0; }
  .toc a { color:var(--accent); text-decoration:none; display:inline-block; padding:2px 10px 2px 0; }
  .strip { display:flex; gap:8px; overflow-x:auto; padding:6px 2px 10px; }
  .strip .cell { flex:0 0 150px; text-decoration:none; color:var(--text); }
  .strip img { width:150px; height:150px; object-fit:cover; border-radius:8px; border:1px solid var(--line); display:block; }
  .spv { width:100%; border-radius:8px; border:1px solid var(--line); background-repeat:no-repeat; background-color:#000; }
  .strip .spv { width:150px; }
  .strip .cl { font-size:12px; line-height:1.3; padding:4px 2px; }
  .note { border-left:3px solid var(--accent); background:var(--panel2); padding:10px 14px; border-radius:0 8px 8px 0; margin:14px 0; }
  .okbox { border-left:3px solid var(--ok); background:var(--panel2); padding:10px 14px; border-radius:0 8px 8px 0; margin:14px 0; }
  .warnbox { border-left:3px solid var(--bad); background:var(--panel2); padding:10px 14px; border-radius:0 8px 8px 0; margin:14px 0; }
  ul { margin:6px 0 6px 4px; padding-left:22px; } li { margin:5px 0; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:16px 18px; margin:18px 0; }
"""

order = ["2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14", "2026-08-15", "2026-08-16", "2026-08-17"]
n_s2_aug = len([m for m in s2 if m["date"].startswith("2026-08")])
n_clear = len([m for m in s2 if m["date"].startswith("2026-08") and (m.get("zone_cloudfree") or 0) >= 60])

HTML = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Курумды — сверка: спутник и модель против очевидца, 11–17 августа</title>
<style>{CSS}</style>
</head>
<body>
<header><nav>
<span class="title">Курумды</span>
<a href="index.html">Кандидаты</a>
<a href="map.html">Карта</a>
<a href="osadki-monitoring.html">Снег и погода</a>
<a href="panoramy.html">Панорамы</a>
<a href="model-3d.html">3D-модель</a>
</nav></header>
<main>

<h1>Сверка: что видел человек на горе, что показала модель и что снял спутник</h1>
<p class="sub">11–17 августа 2026. Собрано 28.08.2026 по сообщению AE (оператор дрона, был на горе 11–16.08) и по контрольной точке штаба; обновлено после двух независимых аудитов 28.08 (метео и спутник). Часть страницы «Снег и погода».</p>

<p class="lead">Зачем это нужно. Мы хотим следить за снегом на склоне по спутникам и погодной модели, не выходя из дома.
Прежде чем этому доверять, надо проверить на неделе, про которую точно известно, что было на самом деле.
AE помнит по дням, когда было солнце и когда шли осадки; штаб точно знает, что 14-го снегопад сорвал облёт и что
в контрольной точке 15-го снега было больше, чем днём 14-го. Ниже — день за днём: слева человек и модель, справа снимки.</p>

<div class="toc">Перейти к дню: {" ".join(f"<a href='#d{d[8:10]}'>{int(d[8:10])} авг</a>" for d in order)} · <a href="#vyvody">Выводы</a> · <a href="#lenta">Все снимки августа</a></div>

<div class="okbox"><b>Коротко.</b> Спутниковый снимок совпал с рассказом очевидца и с контрольной точкой во всех трёх днях, когда он был
(12, 15, 17 августа). Погодная модель правильно показала два дождливо-снежных дня (11–12) и сухой день (15), но
<b>три коротких осадка (13, 14, 16 августа) она занизила или не увидела вовсе</b> — включая снегопад 14-го, который сорвал облёт.
Значит: на факт «снег был / снега не было» опираемся на спутник и на людей, модель — только на прогноз и температуру.</div>

<h2>День за днём</h2>
<p class="sub">Цвет полосы слева: зелёный — данные сходятся, жёлтый — модель занижает, красный — модель пропустила событие. На снимках: оранжевая рамка — место рюкзака (~4660 м), синяя — зона интереса под лагерем 2 (5385–5485 м), зелёная — гребень у лагеря 1 (~5020 м, контроль), красный кружок — контрольная точка 5099 м.</p>

{"".join(day_card(d) for d in order)}

<h2 id="vyvody">Выводы</h2>

<div class="card">
<h3>1. Что подтвердилось</h3>
<ul>
<li><b>Спутник видит снегопад по «исчезновению камней»</b> и видит таяние по их «возвращению». Утро 15-го: в зоне закрыто снегом 87 % эталонных камней, в контрольной точке 10 % (припорошено ночью) → 17-го: 23 % и 0 % (сошло за два солнечных дня). Это совпадает и с AE («15-го и утром 16-го снег уходил на глазах»), и с контрольной точкой.</li>
<li><b>Модель надёжна по температуре и по крупным осадкам.</b> «Внизу дождь, на 4500 снег» у AE = у модели +2 °C днём на 4660 м и −3 °C на 5435 м. Дождливые 11–12 августа и сухое 15-е модель показала верно.</li>
<li><b>Ежедневный VIIRS годится как «был ли день облачным»</b> — грубо, но каждый день, в отличие от Sentinel-2 (раз в 2–3 дня).</li>
</ul>
<h3>2. Что не подтвердилось</h3>
<ul>
<li><b>ECMWF занижает короткие осадки в разы.</b> 13, 14 и 16 августа очевидец видел 2–4 часа осадков; ECMWF дала 0,0–0,6 мм, хотя часы осадков указала верно (после полудня). Другие модели (GFS, ICON, UKMO) дали 1–7 мм. Снегопад 14-го (сорвал облёт) у ECMWF — 0,6 мм, у ICON — 5,2 мм. Вывод для мониторинга: <b>«ECMWF показывает 0 мм» не означает «осадков не было»</b>; смотрим вилку всех моделей.</li>
<li><b>Спутник не видит сам снегопад, только его последствия</b>, и только если пролёт попал на ясное утро. 14-го пролёта не было — прямого спутникового подтверждения снегопада 14-го нет, есть косвенное (утро 15-го).</li>
<li><b>Первая версия индекса была с ошибками.</b> Независимый аудит 28.08 показал: квадратные площадки не соответствовали высотам, маска облаков выбрасывала на разных датах разные куски скальной стены, а осенью тени имитировали бы «снег сходит». Индекс переделан: пояса высот по рельефу, фиксированный набор освещённых точек, число только при ≥95 % без облаков, метрика «закрыто снегом от эталона 20.08». Числа на этой странице — уже по новой версии.</li>
</ul>
<h3>3. Погрешности и уровень доверия</h3>
<table class="meta">
<tr><th>Что</th><th>Погрешность / доверие</th><th>Откуда оценка</th></tr>
<tr><td>«Закрыто снегом», одно значение</td><td>±3–5 п.п. на ясном снимке; при облачности площадки более 5 % — не публикуется</td><td>две орбиты в один день (02.08) расходятся на 1–6 п.п.; вилка порогов 0,3/0,5; аудит методики 28.08</td></tr>
<tr><td>Вывод «снега стало меньше / больше» между двумя ясными снимками</td><td>надёжно, если разница больше 8 п.п. и знак одинаков на двух площадках; иначе — «без явных изменений»</td><td>правило автокомментария; на 15→17.08 разница 64 п.п. в зоне и 10 п.п. в контрольной точке — вывод «сошло» надёжен</td></tr>
<tr><td>Осадки модели за сутки</td><td>±100 % по величине; факт «были/не были» — пропускает события менее ~1–2 мм</td><td>три пропуска из шести дней наблюдений AE; литература по Памиру даёт занижение в 1,5–2 раза</td></tr>
<tr><td>Температура модели по высотам</td><td>±2 °C</td><td>стандартная точность ECMWF с поправкой на высоту; на этой горе прямых измерений нет</td></tr>
<tr><td>Наблюдения AE</td><td>по памяти через 2 недели, точность «часы осадков» — ориентировочная; даты и «солнце/пасмурно» — надёжны (от них зависели вылеты)</td><td>слова самого AE</td></tr>
<tr><td>Контрольная точка 14 vs 15.08</td><td>факт надёжен (кадры дрона); время суток кадров 15-го уточнить</td><td>штаб</td></tr>
</table>
<h3>4. Что меняем в мониторинге</h3>
<ul>
<li>Индикатор «снега больше/меньше» считаем только по ясным снимкам и по двум площадкам сразу; сомнительные снимки помечаем и не используем в выводах.</li>
<li>Осадки модели в сводке подписываем «по модели, короткие осадки может не показать»; факт снегопада берём из спутника и от людей.</li>
<li>Ведём журнал «модель против факта» — эти семь дней стали первыми записями. Просим штаб присылать любые «сегодня шёл снег / было ясно» с датой.</li>
<li>Формулировка «15.08 — после снега 10–12 и события 14-го» заменена на «свежий снег вечера и ночи 14→15».</li>
<li>Порог «заметный снегопад» считаем не по одной модели, а по вилке: любая модель ≥2 мм или часы с осадками ≥3.</li>
</ul>
</div>

<h2 id="lenta">Все снимки Sentinel-2 за август</h2>
<p class="sub">Промотайте вправо: {n_s2_aug} пролётов, из них площадка видна на {n_clear}. Клик — полный размер. Число — доля камней в зоне интереса на этом снимке.</p>
{strip()}

<p class="sub" style="margin-top:40px">Источники: Copernicus Sentinel-2 L2A (AWS earth-search), NASA GIBS VIIRS NOAA-20, Sentinel-1 RTC (Microsoft Planetary Computer), Open-Meteo / ECMWF IFS 9 км. Скрипты: analysis/osadki/render_s2.py, render_viirs.py, render_s1.py, build_sverka.py.</p>
</main>
</body>
</html>
"""

OUT.write_text(HTML, "utf-8")
print("написано", OUT, len(HTML), "байт;", len(s2), "сцен S2,", len(viirs), "дней VIIRS,", len(s1), "пролётов S1")
