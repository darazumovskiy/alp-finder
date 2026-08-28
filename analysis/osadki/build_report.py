#!/usr/bin/env python3
"""Страница «Снег и погода» v2 + лента данных data/timeline.json.

Собирает по дням: погоду по высотам (ECMWF 9 км + вилка моделей + ансамбль), индекс снега по
Sentinel-2 v2 (snow_index.json), снимки S2/S1/VIIRS, метеостанции, автокомментарий по правилам
и ИИ-комментарий (agent/notes). Все числа — с погрешностью; ИИ-текст помечен отдельно.
Запуск: python3 analysis/osadki/build_report.py [--timeline-only]
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
DATA = ROOT / "analysis/osadki/data"
NOTES = ROOT / "analysis/osadki/agent/notes"
OUT = ROOT / "analysis/viewer/osadki-monitoring.html"
SC = "/analysis/osadki/scenes"
C1, C2, C3, C4 = "#3987e5", "#d95926", "#199e70", "#c98500"
MUTED, TEXT, LINE = "#8b93a0", "#d7dbe0", "#2a2f38"
UNITS = [("ryukzak_4600-4900", "Пояс рюкзака 4600–4900 м", C2), ("sklon_4900-5200", "Склон 4900–5200 м", C4),
         ("zona_5200-5500", "Зона интереса 5200–5500 м", C1), ("ctrl_5099", "Контрольная точка 5099 м", C3)]
H_ORDER = ["4000", "4663", "5018", "5435", "6000"]


def load(name, default):
    p = DATA / name
    return json.load(open(p)) if p.exists() else default


def fmt(v, d=1, suf=""):
    if v is None:
        return "—"
    return f"{v:.{d}f}".replace(".", ",") + suf


def tfmt(v):
    if v is None:
        return "—"
    v = 0.0 if abs(v) < 0.05 else v
    return (f"{v:+.1f}".replace("+0.0", "0,0").replace(".", ",")) + " °C"


def dl(d):
    return f"{int(d[8:10])}.{d[5:7]}"


# ---------------- лента ----------------
def sprite_ref(kind, folder, m):
    """Ссылка на спрайт сцены и карта панелей (x, y, w, h) — вместо отдельных файлов."""
    sp = m.get("sprite")
    if not sp:
        return None
    return {"src": f"{SC}/{kind}/{folder}/{sp['file']}", "w": sp["w"], "h": sp["h"], "p": sp["panels"]}


def build_timeline():
    model = load("model_daily.json", {})
    meta_model = model.pop("_meta", {})
    idx = load("snow_index.json", {"meta": {}, "scenes": []})
    s2r = {(m["date"], m["sat"]): m for m in load("s2_index.json", [])}
    s1 = load("s1_index.json", [])
    viirs = {m["date"]: m for m in load("viirs_index.json", [])}
    stations = load("stations.json", {})
    st_meta = stations.pop("_meta", {})
    today = meta_model.get("today") or (dt.datetime.utcnow() + dt.timedelta(hours=6)).date().isoformat()
    days = {}

    def blank(d):
        return {"model": None, "s2": [], "s1": [], "viirs": None, "stations": stations.get(d), "ai": None, "auto": None}

    for d, rec in model.items():
        if not d.startswith("20"):
            continue
        hs = {h: rec[h] for h in H_ORDER if h in rec}
        other = rec.get("other", {})
        days[d] = blank(d)
        days[d]["model"] = {"forecast": rec.get("forecast", d > today), "prec": hs.get("5435", {}).get("prec"),
                            "prec_other": {v["label"]: v["prec"] for v in other.values() if v.get("prec") is not None},
                            "tmax5435_other": {v["label"]: v["tmax5435"] for v in other.values() if v.get("tmax5435") is not None},
                            "t": {h: {"tmax": hv.get("tmax"), "tmin": hv.get("tmin")} for h, hv in hs.items()},
                            "snow_frac_4663": hs.get("4663", {}).get("snow_frac"), "sun_h": hs.get("5435", {}).get("sun_h"),
                            "cloud": hs.get("5435", {}).get("cloud"), "wind": hs.get("5435", {}).get("wind"),
                            "prec_hours": hs.get("5435", {}).get("prec_hours", []), "ens": rec.get("ens")}
    for sc in idx["scenes"]:
        d = sc["date"]
        days.setdefault(d, blank(d))
        r = s2r.get((d, sc["sat"]), {})
        u = {}
        for key, _, _ in UNITS:
            uu = sc["units"].get(key, {})
            a = uu.get("all", {})
            u[key] = {"coverage": uu.get("coverage"), "rock30": a.get("rock30"), "rock40": a.get("rock40"), "rock50": a.get("rock50"),
                      "covered": a.get("covered_ref"), "mid": a.get("ndsi_mid_share"),
                      "north_rock40": uu.get("north", {}).get("rock40"), "other_rock40": uu.get("other", {}).get("rock40")}
        days[d]["s2"].append({"sat": sc["sat"], "orbit": sc["orbit"], "time_utc": sc["time_utc"], "tile_cloud": sc["tile_cloud"], "sun_el": sc["sun_el"],
                              "coverage_zona": u["zona_5200-5500"]["coverage"], "idx": u,
                              "sprite": sprite_ref("s2", f"{d}_{sc['sat']}", r)})
    for m in s1:
        d = m["date"]
        days.setdefault(d, blank(d))
        days[d]["s1"].append({"orbit": m["orbit"], "time_utc": m["time_utc"], "prev_date": m.get("prev_date"),
                              "dvv": {k: v.get("dvv_db") for k, v in m.get("zones", {}).items()},
                              "sprite": sprite_ref("s1", f"{d}_{m['orbit']}", m)})
    for d, m in viirs.items():
        if d in days:
            days[d]["viirs"] = sprite_ref("viirs", d, m)
    for d in days:
        p = NOTES / f"{d}.json"
        if p.exists():
            days[d]["ai"] = json.load(open(p))
    for d in [k for k in days if k < "2026-08-01"]:  # июльские пролёты радара — вне окна мониторинга
        del days[d]
    order = sorted(days)
    last_clear = None
    for d in order:
        days[d]["auto"] = auto_comment(d, days[d], last_clear, days, order)
        for s in days[d]["s2"]:
            if (s["coverage_zona"] or 0) >= 0.95:
                last_clear = (d, s)
    return {"generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"), "today": today, "days": days,
            "units": {k: {"label": l, "color": c, "meta": idx["meta"].get("units", {}).get(k)} for k, l, c in UNITS},
            "index_meta": idx["meta"], "model_meta": meta_model, "stations_meta": st_meta}


def trend_from(s, prev):
    if not prev:
        return None, []
    deltas = []
    for key, lab, _ in UNITS:
        a = s["idx"][key]["covered"]; b = prev[1]["idx"][key]["covered"]
        if a is None or b is None:
            continue
        deltas.append((lab, b, a, a - b))
    if not deltas:
        return None, []
    ups = sum(1 for *_, dd in deltas if dd >= 8); downs = sum(1 for *_, dd in deltas if dd <= -8)
    if ups >= 2 and downs == 0:
        return "больше", deltas
    if downs >= 2 and ups == 0:
        return "меньше", deltas
    if len(deltas) == 1 and ups == 1:
        return "больше (по одной площадке)", deltas
    if len(deltas) == 1 and downs == 1:
        return "меньше (по одной площадке)", deltas
    return "без явных изменений", deltas


def auto_comment(d, day, last_clear, days, order):
    lines = []
    m = day["model"]
    trend = None
    if m and m["prec"] is not None:
        p = m["prec"]; oth = list(m["prec_other"].values())
        rng = f" (другие модели: {fmt(min(oth), 0)}–{fmt(max(oth), 0)} мм)" if oth else ""
        pref = "Прогноз: " if m["forecast"] else "По модели за сутки: "
        if p < 1:
            lines.append(pref + f"практически без осадков ({fmt(p)} мм){rng}. Короткие осадки модель может не показать.")
        else:
            snow = m.get("snow_frac_4663")
            ph = "" if snow is None else (" — на высоте рюкзака в основном снег" if snow >= 0.7 else (" — на высоте рюкзака в основном дождь/мокрый снег" if snow <= 0.3 else " — на высоте рюкзака смешанные"))
            lines.append(pref + f"осадки {fmt(p)} мм воды{rng}{ph}; выше 5000 м — снег.")
        t46 = m["t"].get("4663", {}).get("tmax"); t54 = m["t"].get("5435", {}).get("tmax")
        if t46 is not None and t54 is not None:
            oo = list(m["tmax5435_other"].values()) or [t54]
            lines.append(f"Днём на 4663 м {tfmt(t46)} — {'снег тает от воздуха' if t46 > 0 else 'мороз'}; на 5435 м {tfmt(t54)} (другие модели {fmt(min(oo), 0)}…{fmt(max(oo), 0)} °C — знак на этой высоте модели не согласуют).")
        if m.get("ens") and m["forecast"]:
            e = m["ens"]
            lines.append(f"Ансамбль ECMWF: вероятность осадков ≥1 мм — {int(e['p_ge1']*100)} %, ≥5 мм — {int(e['p_ge5']*100)} %.")
    clear = [s for s in day["s2"] if (s["coverage_zona"] or 0) >= 0.95]
    if day["s2"] and not clear:
        lines.append("Спутник Sentinel-2: пролёт был, но площадки под облаками — индекс не считается.")
    for s in clear:
        parts = []
        for key, lab, _ in UNITS:
            u = s["idx"][key]
            if u["covered"] is not None:
                parts.append(f"{lab.split(' ')[0].lower()} {lab.split(' ')[1] if lab.startswith('Пояс') else ''}: закрыто снегом {fmt(u['covered'], 0)} % камней-эталона".replace("  ", " "))
        if parts:
            lines.append(f"Sentinel-2 {s['sat']} {s['time_utc']} UTC, площадки чистые — " + "; ".join(parts) + ".")
        tr, deltas = trend_from(s, last_clear)
        if tr:
            trend = tr
            dtxt = ", ".join(f"{lab.split(' ')[0].lower()} {fmt(b, 0)}→{fmt(a, 0)} %" for lab, b, a, dd in deltas)
            lines.append(f"Против предыдущего ясного снимка ({dl(last_clear[0])}): снега стало <b>{tr}</b> ({dtxt}). Надёжно, если разница больше ~8 п.п. и совпадает на двух площадках.")
    for s in day["s1"]:
        dz = s["dvv"].get("zona_5385-5485")
        if dz is not None and abs(dz) >= 2:
            lines.append(f"Радар Sentinel-1 ({s['orbit']}): сигнал в зоне {'упал' if dz < 0 else 'вырос'} на {fmt(abs(dz))} дБ к пролёту {dl(s['prev_date']) if s.get('prev_date') else '—'} — {'вероятно, снег намок или потеплело' if dz < 0 else 'подсохло или свежий сухой снег'}. Толщину радар не измеряет.")
    stn = day.get("stations") or {}
    k = stn.get("karakul"); st_ = stn.get("sarytash")
    if k:
        lines.append(f"Станция Каракуль (3930 м, 52 км южнее): {tfmt(k.get('tmin'))}…{tfmt(k.get('tmax'))}, осадки {fmt(k.get('prec'), 1, ' мм') if k.get('prec') is not None else 'нет данных'}{', был снег' if k.get('snow') else ''}, облачность {fmt(k.get('cloud_okta'), 0, '/8')}.")
    if st_:
        lines.append(f"Сары-Таш (3150 м, 40 км): {tfmt(st_.get('tmin'))}…{tfmt(st_.get('tmax'))}{', был снег' if st_.get('snow') else ''}, облачность {fmt(st_.get('cloud_okta'), 0, '/8')}.")
    if m and not m["forecast"]:
        streak = 0
        for dd in reversed([x for x in order if x <= d]):
            mm = days[dd]["model"]
            if mm and mm["prec"] is not None and mm["prec"] < 1:
                streak += 1
            else:
                break
        acc = sum((days[x]["model"]["prec"] or 0) for x in order if "2026-08-15" <= x <= d and days[x]["model"] and not days[x]["model"]["forecast"])
        lines.append(f"Дней подряд без осадков ≥1 мм по модели: {streak}. Накоплено с 15.08 по модели: {fmt(acc, 0)} мм воды (±100 %).")
    return {"text": "<br>".join(lines), "trend": trend}


# ---------------- графики ----------------
def svg_open(w, h):
    return f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system,Segoe UI,Roboto,sans-serif" font-size="12">'


def xscale(order, L, R, W):
    d0 = dt.date.fromisoformat(order[0]); n = (dt.date.fromisoformat(order[-1]) - d0).days + 1
    return lambda d: L + (W - L - R) * ((dt.date.fromisoformat(d) - d0).days + 0.5) / n, n


def xaxis(s, order, sx, H, B, today):
    d = dt.date.fromisoformat(order[0]); d1 = dt.date.fromisoformat(order[-1])
    while d <= d1:
        if d.day in (1, 5, 10, 15, 20, 25):
            s.append(f'<text x="{sx(d.isoformat()):.1f}" y="{H-B+16}" text-anchor="middle" fill="{MUTED}">{d.day:02d}.{d.month:02d}</text>')
        d += dt.timedelta(days=1)
    if order[0] <= today <= order[-1]:
        xt = sx(today)
        s.append(f'<line x1="{xt:.1f}" y1="14" x2="{xt:.1f}" y2="{H-B}" stroke="{MUTED}" stroke-dasharray="4 4"/><text x="{xt+4:.1f}" y="24" fill="{MUTED}">сегодня</text>')


def chart_covered(tl):
    days = tl["days"]; order = sorted(days)
    W, H, L, R, T, B = 940, 270, 44, 14, 18, 40
    sx, _ = xscale(order, L, R, W)
    sy = lambda v: T + (H - T - B) * (1 - v / 100)
    s = [svg_open(W, H)]
    for g in range(0, 101, 20):
        s.append(f'<line x1="{L}" y1="{sy(g):.1f}" x2="{W-R}" y2="{sy(g):.1f}" stroke="{LINE}"/><text x="{L-6}" y="{sy(g)+4:.1f}" text-anchor="end" fill="{MUTED}">{g}%</text>')
    for d in order:
        m = days[d]["model"]
        if m and m["prec"] is not None and m["prec"] >= 3 and not m["forecast"]:
            s.append(f'<rect x="{sx(d)-5:.1f}" y="{T}" width="10" height="{H-T-B}" fill="{MUTED}" fill-opacity="0.18"><title>{dl(d)}: осадки по модели {fmt(m["prec"])} мм</title></rect>')
    for key, lab, col in UNITS:
        pts = [(d, sc["idx"][key]["covered"], sc) for d in order for sc in days[d]["s2"] if sc["idx"][key]["covered"] is not None]
        if len(pts) >= 2:
            s.append('<path d="M' + " L".join(f"{sx(d):.1f},{sy(v):.1f}" for d, v, _ in pts) + f'" fill="none" stroke="{col}" stroke-width="2"/>')
        for d, v, sc in pts:
            s.append(f'<circle class="pt" data-date="{d}" cx="{sx(d):.1f}" cy="{sy(v):.1f}" r="4.5" fill="{col}" stroke="#181b21" stroke-width="1.5"><title>{dl(d)} {sc["sat"]}: {lab} — закрыто снегом {fmt(v,0)} % камней-эталона (камней при порогах 0,3/0,5: {fmt(sc["idx"][key]["rock30"],0)}–{fmt(sc["idx"][key]["rock50"],0)} %)</title></circle>')
    xaxis(s, order, sx, H, B, tl["today"])
    s.append(f'<text x="{L}" y="{H-4}" fill="{MUTED}">доля камней-эталона (бесснежная дата 20.08), закрытых снегом. Выше — снега больше. Только ясные снимки. Серые полосы — снегопады ≥3 мм по модели</text></svg>')
    return "".join(s)


def chart_prec(tl):
    days = tl["days"]; order = sorted(days)
    W, H, L, R, T, B = 940, 250, 44, 14, 18, 40
    sx, n = xscale(order, L, R, W); pw = (W - L - R) / n
    vals = [days[d]["model"]["prec"] or 0 for d in order if days[d]["model"]]
    ymax = max(16, (max(vals) if vals else 0) * 1.15)
    sy = lambda v: T + (H - T - B) * (1 - min(v, ymax) / ymax)
    s = [svg_open(W, H)]
    for g in range(0, int(ymax) + 1, 5):
        s.append(f'<line x1="{L}" y1="{sy(g):.1f}" x2="{W-R}" y2="{sy(g):.1f}" stroke="{LINE}"/><text x="{L-6}" y="{sy(g)+4:.1f}" text-anchor="end" fill="{MUTED}">{g}</text>')
    for d in order:
        m = days[d]["model"]
        if not m or m["prec"] is None:
            continue
        x = sx(d) - pw / 2 + 1.5; w = pw - 3; p = m["prec"]
        fill = "#86b6ef" if m["forecast"] else C1
        op = ' fill-opacity="0.55"' if m["forecast"] else ""
        oth = list(m["prec_other"].values())
        rng = f"; другие модели {fmt(min(oth),0)}–{fmt(max(oth),0)} мм" if oth else ""
        s.append(f'<rect class="pt" data-date="{d}" x="{x:.1f}" y="{sy(p):.1f}" width="{w:.1f}" height="{H-B-sy(p):.1f}" fill="{fill}"{op} rx="2"><title>{dl(d)}: ECMWF {fmt(p)} мм{rng}{" (прогноз)" if m["forecast"] else ""}</title></rect>')
        if oth:
            s.append(f'<line x1="{sx(d):.1f}" y1="{sy(max(oth)):.1f}" x2="{sx(d):.1f}" y2="{sy(min(oth)):.1f}" stroke="{TEXT}" stroke-opacity="0.5" stroke-width="1.5"/>')
        if p >= 3:
            s.append(f'<text x="{sx(d):.1f}" y="{sy(p)-4:.1f}" text-anchor="middle" fill="{TEXT}">{p:.0f}</text>')
    xaxis(s, order, sx, H, B, tl["today"])
    s.append(f'<text x="{L}" y="{H-4}" fill="{MUTED}">мм воды за сутки, ECMWF 9 км (столбики); усы — разброс других моделей (GFS, ICON, UKMO, JMA, ARPEGE), выше шкалы обрезаны. На высоте зоны это снег</text></svg>')
    return "".join(s)


def chart_temp(tl):
    days = tl["days"]; order = sorted(days)
    W, H, L, R, T, B = 940, 230, 44, 70, 18, 40
    sx, _ = xscale(order, L, R, W)
    series = [("4663", "4663 м", C2), ("5435", "5435 м", C1)]
    vals = [days[d]["model"]["t"][h]["tmax"] for d in order if days[d]["model"] for h, _, _ in series if days[d]["model"]["t"].get(h, {}).get("tmax") is not None]
    ymin, ymax = min(-12, min(vals) - 1), max(6, max(vals) + 1)
    sy = lambda v: T + (H - T - B) * (1 - (v - ymin) / (ymax - ymin))
    s = [svg_open(W, H)]
    for g in range(int(ymin) - int(ymin) % 4, int(ymax) + 1, 4):
        s.append(f'<line x1="{L}" y1="{sy(g):.1f}" x2="{W-R}" y2="{sy(g):.1f}" stroke="{LINE}"/><text x="{L-6}" y="{sy(g)+4:.1f}" text-anchor="end" fill="{MUTED}">{g}</text>')
    s.append(f'<line x1="{L}" y1="{sy(0):.1f}" x2="{W-R}" y2="{sy(0):.1f}" stroke="{TEXT}" stroke-width="1.5"/><text x="{L+6}" y="{sy(0)-5:.1f}" fill="{TEXT}">0 °C — выше тает от воздуха</text>')
    band = []
    for d in order:
        m = days[d]["model"]
        if m and m["tmax5435_other"] and m["t"].get("5435", {}).get("tmax") is not None:
            v = list(m["tmax5435_other"].values()) + [m["t"]["5435"]["tmax"]]
            band.append((d, min(v), max(v)))
    if len(band) >= 2:
        path = "M" + " L".join(f"{sx(d):.1f},{sy(hi):.1f}" for d, lo, hi in band) + " L" + " L".join(f"{sx(d):.1f},{sy(lo):.1f}" for d, lo, hi in reversed(band)) + " Z"
        s.append(f'<path d="{path}" fill="{C1}" fill-opacity="0.12" stroke="none"><title>разброс моделей на 5435 м</title></path>')
    for h, lab, col in series:
        pts = [(d, days[d]["model"]["t"][h]["tmax"]) for d in order if days[d]["model"] and days[d]["model"]["t"].get(h, {}).get("tmax") is not None]
        if not pts:
            continue
        s.append('<path d="M' + " L".join(f"{sx(d):.1f},{sy(v):.1f}" for d, v in pts) + f'" fill="none" stroke="{col}" stroke-width="2"/>')
        for d, v in pts:
            s.append(f'<circle class="pt" data-date="{d}" cx="{sx(d):.1f}" cy="{sy(v):.1f}" r="3.5" fill="{col}" stroke="#181b21" stroke-width="1.5"><title>{dl(d)}, {lab}: макс. {tfmt(v)}</title></circle>')
        s.append(f'<text x="{sx(pts[-1][0])+8:.1f}" y="{sy(pts[-1][1])+4:.1f}" fill="{TEXT}">{lab}</text>')
    xaxis(s, order, sx, H, B, tl["today"])
    s.append(f'<text x="{L}" y="{H-4}" fill="{MUTED}">максимальная температура за день, ECMWF с пересчётом на высоту (ячейка ~4775 м, −0,65 °C на 100 м). Голубая лента — разброс других моделей на 5435 м</text></svg>')
    return "".join(s)


def chart_clim(clim):
    W, H, L, R, T, B = 940, 200, 44, 12, 18, 40
    names = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
    m = clim["ifs"]["monthly"]; e = clim["era5"]["monthly"]
    pw = (W - L - R) / 12
    ymax = max(50, max(m[str(k)]["prec_mean"] for k in range(1, 13)) * 1.25)
    sy = lambda v: T + (H - T - B) * (1 - v / ymax)
    s = [svg_open(W, H)]
    for g in range(0, int(ymax) + 1, 10):
        s.append(f'<line x1="{L}" y1="{sy(g):.1f}" x2="{W-R}" y2="{sy(g):.1f}" stroke="{LINE}"/><text x="{L-6}" y="{sy(g)+4:.1f}" text-anchor="end" fill="{MUTED}">{g}</text>')
    for i in range(12):
        k = str(i + 1); v = m[k]["prec_mean"]; x = L + i * pw + 6; w = pw - 12
        col = C1 if i in (7, 8) else "#6da7ec"
        s.append(f'<rect x="{x:.1f}" y="{sy(v):.1f}" width="{w:.1f}" height="{H-B-sy(v):.1f}" fill="{col}" rx="3"><title>{names[i]}: ECMWF 2017–2025 в среднем {fmt(v,0)} мм (от {fmt(m[k]["prec_min"],0)} до {fmt(m[k]["prec_max"],0)}); ERA5 1991–2025: {fmt(e[k]["prec_mean"],0)} мм. Дней с плюсом на 4663 м: {fmt(m[k]["pos_days_4663"],0)} (ECMWF) / {fmt(e[k]["pos_days_4663"],0)} (ERA5)</title></rect>')
        s.append(f'<text x="{x+w/2:.1f}" y="{sy(v)-4:.1f}" text-anchor="middle" fill="{TEXT}">{v:.0f}</text><text x="{x+w/2:.1f}" y="{H-B+16}" text-anchor="middle" fill="{MUTED}">{names[i]}</text>')
    s.append(f'<text x="{L}" y="{H-4}" fill="{MUTED}">средние осадки за месяц, мм, та же модель ECMWF 9 км (архив 2017–2025). ERA5 даёт вдвое больше в абсолюте, но то же соотношение сентябрь/август</text></svg>')
    return "".join(s)


CSS = Path(__file__).with_name("report.css").read_text() if Path(__file__).with_name("report.css").exists() else ""
JS = Path(__file__).with_name("report.js").read_text() if Path(__file__).with_name("report.js").exists() else ""


def status_bar(runs):
    if not runs:
        return "<div class='status'><span>Автообновление ещё не запускалось — данные собраны вручную 28.08.2026.</span></div>"
    r = runs[-1]
    ok = r.get("ok") and all(v.get("ok", True) or v.get("skipped") for v in r["steps"].values())
    fin = r.get("finished", r["started"])
    t = dt.datetime.fromisoformat(fin.replace("Z", "+00:00")) + dt.timedelta(hours=6)
    import run_daily  # актуальные подписи шагов (в runs.json могут быть старые)
    labels = {name: label for name, _, label in run_daily.STEPS}
    steps = "".join(f"<tr><td>{labels.get(k, v.get('label', k))}</td><td>{'пропущен' if v.get('skipped') else ('✓' if v.get('ok') else '✗ ' + (v.get('err') or ''))}</td><td>{v.get('secs','')}</td></tr>" for k, v in r["steps"].items())
    return (f"<div class='status'><span><span class='dot' style='background:{'var(--ok)' if ok else 'var(--warn)'}'></span>"
            f"Последнее обновление: <b>{t.strftime('%d.%m %H:%M')} по Бишкеку</b> {'— все шаги прошли' if ok else '— часть шагов с ошибками'}</span>"
            f"<span class='sub'>Следующие запуски: ежедневно в 08:00 и 18:00 по Бишкеку</span>"
            f"<details><summary>шаги последнего запуска</summary><table class='meta'><tr><th>шаг</th><th>итог</th><th>сек</th></tr>{steps}</table></details></div>")


def kpis(tl, clim):
    days = tl["days"]; order = sorted(days)
    past = [d for d in order if days[d]["model"] and not days[d]["model"]["forecast"]]
    last7 = past[-7:]
    p7 = sum(days[d]["model"]["prec"] or 0 for d in last7)
    labs = list(days[last7[-1]]["model"]["prec_other"].keys()) if last7 else []
    oth = [sum((days[d]["model"]["prec_other"].get(lab) or 0) for d in last7) for lab in labs]
    streak = 0
    for d in reversed(past):
        if (days[d]["model"]["prec"] or 0) < 1:
            streak += 1
        else:
            break
    lc = None
    for d in reversed(order):
        for s in days[d]["s2"]:
            if (s["coverage_zona"] or 0) >= 0.95:
                lc = (d, s); break
        if lc:
            break
    trend = days[lc[0]]["auto"]["trend"] if lc and days[lc[0]]["auto"] else None
    zc = lc[1]["idx"]["zona_5200-5500"]["covered"] if lc else None
    fut = [d for d in order if days[d]["model"] and days[d]["model"]["forecast"]][:3]
    pmax = max([days[d]["model"]["ens"]["p_ge1"] for d in fut if days[d]["model"].get("ens")] or [0])
    m9 = clim["ifs"]["monthly"]["9"]; m8 = clim["ifs"]["monthly"]["8"]
    return f"""<div class="kpis">
<div class="kpi"><div class="v">{('снега ' + trend) if trend else '—'}</div><div class="l">последний ясный снимок {dl(lc[0]) if lc else '—'}{(': зона закрыта снегом на ' + fmt(zc,0) + ' % от 20.08') if zc is not None else ''}</div><div class="e">надёжно при разнице &gt;8 п.п. на двух площадках; один снимок ±3–5 п.п.</div></div>
<div class="kpi"><div class="v">{fmt(p7,0)} мм</div><div class="l">осадков за 7 дней по модели ECMWF (до {dl(last7[-1]) if last7 else '—'})</div><div class="e">другие модели за те же дни: {fmt(min(oth),0) if oth else '—'}–{fmt(max(oth),0) if oth else '—'} мм; короткие осадки модель занижает</div></div>
<div class="kpi"><div class="v">{streak} дн.</div><div class="l">подряд без осадков ≥1 мм по модели</div><div class="e">по модели; очевидцы видели короткие осадки и в «сухие» дни</div></div>
<div class="kpi"><div class="v">{int(pmax*100)} %</div><div class="l">вероятность осадков ≥1 мм в ближайшие 3 дня (ансамбль ECMWF, 51 вариант)</div><div class="e">вероятность — по атмосфере, рельеф горы ансамбль не учитывает</div></div>
<div class="kpi"><div class="v">{fmt(m9['prec_mean'],0)} vs {fmt(m8['prec_mean'],0)}</div><div class="l">норма сентября и августа, мм (та же модель, 2017–2025)</div><div class="e">сентябрь суше августа в {clim['ifs']['sep_lt_aug_years']} лет; абсолют по ERA5 вдвое выше, соотношение то же</div></div>
</div>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeline-only", action="store_true")
    a = ap.parse_args()
    tl = build_timeline()
    json.dump(tl, open(DATA / "timeline.json", "w"), ensure_ascii=False, indent=1)
    print("timeline:", len(tl["days"]), "дней")
    if a.timeline_only:
        return
    clim = load("clim.json", None)
    runs = load("runs.json", [])
    um = tl["index_meta"].get("units", {})
    ev = f"{SC}/s2/_evaluable.png"
    HTML = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Курумды — снег и погода</title>
<style>{CSS}</style>
</head>
<body>
<header><nav>
<span class="title">Курумды</span>
<a href="index.html">Кандидаты</a>
<a href="map.html">Карта</a>
<a href="panoramy.html">Панорамы</a>
<a href="model-3d.html">3D-модель</a>
<a href="polyot-3d.html">Полёт 3D</a>
<a href="osadki-monitoring.html">Снег и погода</a>
<a href="otchety.html">Отчёты</a>
</nav></header>
<main>
<h1>Снег на склоне Курумды: следим по спутникам и погодной модели</h1>
<p class="sub">Задача штаба: отслеживать, снега на склоне стало больше или меньше, по высотам, и накопить историю к 15 сентября. Всё — «с дивана»: спутники, модели, метеостанции. Страница обновляется автоматически. Сверка метода с очевидцем — в <a href="sverka-avgust-2026.html">отдельном отчёте</a>; методика прошла независимый аудит 28.08, правки учтены (раздел «Погрешности»).</p>
{status_bar(runs)}

<h2 id="korotko"><span class="num">1.</span>Коротко сегодня</h2>
{kpis(tl, clim)}

<h2 id="brauzer"><span class="num">2.</span>День за днём</h2>
<p class="sub">Выберите день в ленте (или стрелки ← → на клавиатуре). Столбик в клетке — осадки по модели, число — дневная температура на высоте рюкзака, точки: зелёная — ясный снимок Sentinel-2, серая — снимок в облаках, фиолетовая — радар, жёлтая — есть оценка ИИ-агента. ▲/▼ — снега стало больше/меньше по сравнению с предыдущим ясным снимком. Пунктирные клетки — прогноз.</p>
<div class="strip" id="strip"></div>
<div class="nav2"><button id="prev">← раньше</button><span class="cur" id="curdate"></span><button id="next">позже →</button></div>
<div class="daybox"><div id="left"></div><div id="right"></div></div>

<h2 id="grafiki"><span class="num">3.</span>Графики</h2>
<h3>Сколько камней-эталона закрыто снегом (главный индикатор)</h3>
<div class="legend">{"".join(f'<span><i style="background:{c}"></i>{l}</span>' for _, l, c in UNITS)}</div>
<div class="chart">{chart_covered(tl)}</div>
<p class="cap">Клик по точке открывает день. Метод: на самой бесснежной ясной дате (20.08) отмечаем пиксели-камни на каждой площадке; дальше смотрим, какая их часть на новом снимке стала снегом. Считаются только пиксели, которые освещены солнцем и в октябре (иначе осенние тени имитировали бы «снег сходит»). Площадки — пояса высот по рельефу, а не квадраты.</p>
<h3>Осадки по модели, день за днём</h3>
<div class="chart">{chart_prec(tl)}</div>
<p class="cap">ECMWF — одна из самых «сухих» моделей для этой горы; усы показывают, что другие дают в 2–3 раза больше. В какую сторону врёт каждая на этой горе — узнаем по журналу «модель против факта».</p>
<h3>Дневная температура по высотам</h3>
<div class="legend"><span><i style="background:{C2}"></i>4663 м — высота рюкзака</span><span><i style="background:{C1}"></i>5435 м — зона интереса</span></div>
<div class="chart">{chart_temp(tl)}</div>
<p class="cap">На 4663 м все модели согласны: днём выше нуля почти всегда — снег между снегопадами тает. На 5435 м знак не согласован (лента), поэтому «тает ли от воздуха в зоне» по модели сказать нельзя; там работают солнце и ветер.</p>
<h3>Что обычно бывает в сентябре</h3>
<div class="chart">{chart_clim(clim) if clim else ''}</div>
<p class="cap">Сентябрь суше августа в {clim['ifs']['sep_lt_aug_years'] if clim else '—'} лет по ECMWF (медиана отношения {fmt(clim['ifs']['sep_to_aug_median'],2) if clim else '—'}) и в {clim['era5']['sep_lt_aug_years'] if clim else '—'} лет по ERA5 (1991–2025). Это устойчивый вывод; абсолютные миллиметры между моделями различаются вдвое. Снегопады в сентябре бывают почти каждый год (минимум по ECMWF — {fmt(clim['ifs']['monthly']['9']['prec_min'],0) if clim else '—'} мм за месяц).</p>

<h2 id="metod"><span class="num">4.</span>Как считается индекс и что он значит</h2>
<div class="imgrow">
<figure style="max-width:560px"><a href="{ev}" target="_blank"><img src="{ev}" alt="оцениваемые пиксели"></a><figcaption>Пиксели, по которым считается индекс: оранжевые — пояс рюкзака 4600–4900 м ({um.get('ryukzak_4600-4900',{}).get('n','—')} точек по 10 м), жёлтые — склон 4900–5200 ({um.get('sklon_4900-5200',{}).get('n','—')}), синие — зона 5200–5500 ({um.get('zona_5200-5500',{}).get('n','—')}), красные — контрольная точка ({um.get('ctrl_5099',{}).get('n','—')}). Тёмное — не считается (тень осенью, облака на эталоне, вне поясов).</figcaption></figure>
</div>
<ul>
<li><b>Что видит спутник.</b> Sentinel-2 снимает гору каждые 2–3 дня около полудня, точка = 10 м. Снег и камень различаются по инфракрасному каналу (снег его почти не отражает). Камень — точка со «снежным индексом» ниже 0,4; считаем и при порогах 0,3 и 0,5, чтобы показать вилку.</li>
<li><b>Что такое «закрыто снегом».</b> 20 августа — самый бесснежный ясный день. Камни, видимые в тот день, — эталон. На каждом следующем снимке смотрим, какая доля эталонных камней стала снегом. 0 % — так же голо, как 20.08; 60 % — большинство камней под снегом.</li>
<li><b>Почему не все пиксели.</b> Осенью солнце ниже, тени растут; в тени спутник видит «камень» там, где снег. Поэтому считаем только точки, освещённые и 5 октября (рассчитано по рельефу и положению Солнца), и не тёмные на эталоне. Так ряд не «плывёт» к 15 сентября.</li>
<li><b>Когда числа нет.</b> Если облака закрыли больше 5 % площадки — прочерк: частичная облачность выбрасывает разные куски склона и искажает долю (это показал аудит).</li>
<li><b>Что индекс НЕ говорит.</b> Толщину снега; что происходит на затенённых северных стенах; что под снегом. Северные склоны показаны отдельной колонкой — там точек мало и доверие ниже.</li>
</ul>

<h2 id="kak"><span class="num">5.</span>Как это работает</h2>
<div class="flow">
<div class="box"><b>Источники</b>Sentinel-2 (открытый архив AWS) · Sentinel-1 радар (Microsoft Planetary Computer) · VIIRS ежедневно (NASA GIBS) · ECMWF и ещё 5 моделей через Open-Meteo · станции Каракуль и Сары-Таш (OGIMET) — всё бесплатно, без ключей</div>
<div class="arrow">→</div>
<div class="box"><b>Робот на нашем Mac</b>launchd запускает конвейер в 08:00 и 18:00 по Бишкеку: скачать новое, вырезать гору, посчитать индекс, обновить ленту</div>
<div class="arrow">→</div>
<div class="box"><b>Оценка ИИ-агента</b>Claude Fable 5 получает ленту, журнал сверки и снимки и пишет оценку по строгой схеме — на день каждого снимка Sentinel-2 и на текущий день (за август история дописана задним числом, только по данным на ту дату, это отмечено в блоке). Это сгенерированный текст: числа не считает, только читает. На странице помечен фиолетовым, отдельно от алгоритмического расчёта</div>
<div class="arrow">→</div>
<div class="box"><b>Эта страница</b>пересобирается и публикуется на сайте; статус запуска — вверху</div>
</div>

<h2 id="pogreshnosti"><span class="num">6.</span>Погрешности и уровень доверия</h2>
<table class="meta">
<tr><th>Что</th><th>Погрешность / доверие</th><th>Откуда</th></tr>
<tr><td>«Закрыто снегом», одно значение</td><td>±3–5 п.п. на ясном снимке; при облачности &gt;5 % площадки — не публикуется</td><td>две орбиты в один день (02.08) расходятся на 1–6 п.п.; порог 0,3/0,5 — вилка ±5–10 п.п. (аудит 28.08)</td></tr>
<tr><td>Вывод «снега больше/меньше»</td><td>надёжно при разнице &gt;8 п.п. на двух площадках с одинаковым знаком; иначе «без явных изменений»</td><td>правило автокомментария; снегопады 3–4.08 и 22–23.08 видны при любом пороге</td></tr>
<tr><td>Северные склоны</td><td>низкое доверие: мало освещённых точек, тонкий снег на скалах даёт промежуточные значения</td><td>аудит: сигнал в основном с освещённого восточного гребня</td></tr>
<tr><td>Осадки модели за сутки</td><td>±100 %; короткие ливни/снегопады занижает в разы; ECMWF — одна из самых сухих моделей здесь</td><td>сверка с очевидцем 11–16.08; разброс моделей ×3–7</td></tr>
<tr><td>Температура модели</td><td>±2 °C на 4663 м (знак надёжен); на 5435 м модели расходятся на 4–5 °C — знак не гарантирован</td><td>аудит 28.08: ECMWF 0 плюсовых дней в августе, GFS/ICON — до 17</td></tr>
<tr><td>Норма сентября</td><td>отношение сентябрь/август устойчиво (0,5–0,6); абсолютные мм — ±50 %</td><td>две шкалы: ECMWF 2017–2025 и ERA5 1991–2025</td></tr>
<tr><td>Станции</td><td>измерения точные, но станции в 40–52 км и на 1,5–2 км ниже склона</td><td>OGIMET, SYNOP каждые 3 ч</td></tr>
<tr><td>Оценка ИИ-агента</td><td>сгенерированный моделью текст по тем же данным; не использовать как источник чисел</td><td>помечен отдельно от алгоритмического расчёта; при отказе модели — пусто</td></tr>
</table>
<div class="warnbox"><b>Чего система не сможет.</b> Измерить сантиметры снега (ни один спутник не умеет на таком склоне). Увидеть, что происходит под снегом и на затенённых стенах. Назвать день, когда что-то вытает. Работать в облачную неделю — тогда остаются радар (мокро/сухо), модель и станции.</div>

<h2 id="slovar"><span class="num">7.</span>Словарик</h2>
<dl class="gloss">
<dt>Sentinel-2</dt><dd>Европейские спутники, снимают бесплатно всю Землю, точка на снимке = 10 м. Над Курумды — каждые 2–3 дня около полудня; снимок доступен через несколько часов.</dd>
<dt>Sentinel-1 (радар)</dt><dd>Видит сквозь облака и ночью, но не различает толщину снега. Падение сигнала — снег намок или потеплело.</dd>
<dt>VIIRS</dt><dd>Американский спутник, каждый день, точка = 375 м. Годится только понять «облачно или ясно» и «снег в долине или нет».</dd>
<dt>Индекс «закрыто снегом»</dt><dd>Доля камней-эталона (открытых 20.08), которые на новом снимке под снегом. Растёт — снега больше, падает — меньше.</dd>
<dt>Погодная модель ECMWF</dt><dd>Европейский расчёт погоды по всей планете, ячейка 9 км. Для точки на горе температуру пересчитывают на высоту, осадки — общие на ячейку. Для проверки рядом показаны ещё пять моделей.</dd>
<dt>Ансамбль</dt><dd>51 вариант прогноза с немного разными стартовыми условиями; доля вариантов с осадками = вероятность.</dd>
<dt>Open-Meteo, OGIMET, GIBS</dt><dd>Бесплатные сайты-посредники, которые отдают данные моделей, метеостанций и спутников программе по запросу.</dd>
<dt>launchd / крон</dt><dd>Будильник на компьютере: «запускай программу каждый день в 08:00 и 18:00».</dd>
</dl>
<p class="sub" style="margin-top:40px">Данные: Copernicus Sentinel-1/2, NASA GIBS VIIRS, Open-Meteo (ECMWF, GFS, ICON, UKMO, JMA, ARPEGE), OGIMET. Код: analysis/osadki/. Научная база и аудиты: docs/osadki-monitoring.md.</p>
</main>
<script>window.__TL__ = {json.dumps(tl, ensure_ascii=False)};</script>
<script>{JS}</script>
</body>
</html>
"""
    OUT.write_text(HTML, "utf-8")
    print("написано", OUT, len(HTML) // 1024, "КБ")


if __name__ == "__main__":
    main()
