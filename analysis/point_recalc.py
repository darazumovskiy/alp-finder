#!/usr/bin/env python3
"""Пересчёт чисел карточек карты из машинных рецептов calc= / size_calc=.

Каждая точка POINTS (analysis/viewer/build_map.py), чья координата или размер
считались нами, несёт рецепт — полный набор входов расчёта. Этот модуль
исполняет рецепт заново и сверяет результат с числами карточки; расхождение —
ошибка сборки, а не повод для чтения старых отчётов.

CLI:
  analysis/.venv/bin/python analysis/point_recalc.py            # все точки, сводка
  analysis/.venv/bin/python analysis/point_recalc.py спальник   # одна, подробно

Выход 1, если хоть одна точка разошлась с пересчётом.

Методы рецепта calc= (m=):
  cast      луч через пиксель кадра в рельеф: video, t, px, py, f
            (+ f_lo/f_hi — вилка фокусного). unc = увод вилки DEM ±30 м
            и вилки фокусного. f числом или "cov" — самокалибровка из
            analysis/coverage/<видео>.coverage.tsv. off_px — насколько объект
            может быть смещён от использованного пикселя: к unc добавляется
            dist·off_px/f. extra_unc — заявленная добавка (обосновать в coord).
  c_center  луч центра кадра: video, t; для off_px нужно f.
  mean      среднее нескольких наблюдений: obs=[рецепты cast/c_center],
            also=[[lat, lon, метка], …] — чужие оценки того же объекта,
            включаются в разброс. unc покрывает вилки наблюдений и разброс.
  triang    пересечение ≥2 лучей (analysis/triangulate.py solve): specs=[…].
            unc = max(σ-эллипсоид гориз., худший остаток) + extra_unc
            (записанные вилки входов, напр. фокусного).
  gps       точка = позиция дрона: video, t. Сверка карточки с телеметрией.
  lrf       позиция цели дальномера из XMP фото: photos=[имена .JPG].
            Несколько снимков — среднее, unc = разброс; один — типовая σ ступени.
  footprint точка = позиция дрона, unc = радиус пятна кадра под дроном:
            video, t, f (наименьшее фокусное = консервативный радиус).
  registry  координата процитирована из docs/nakhodki/README.md: match=«lat, lon»
            — строка обязана быть в реестре и равняться карточке.
  quoted    расчёт был, но его входы не сохранены: src=файл, quote=строка
            результата. Проверяется цитата и равенство карточке; пересчёту
            не подлежит — честная пометка, не ступень доверия.

Рецепт size_calc= (размер по кадру): px (или px_lo/px_hi), dist (или
dist_lo/dist_hi), f (или f_lo/f_hi), unit («м»/«см»), label — префикс подписи.
Интервал = [px·dist_lo/f_hi, px·dist_hi/f_lo]; ошибки дистанции и фокусного
перемножаются, одного числа у видео-размера не бывает. Строку размера карточки
генерирует build_map.py из этого интервала.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geoproject import Dem, _ShiftDem, at, cast, load_rows, ray_dir  # noqa: E402
from triangulate import solve_rays  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs/nakhodki/README.md"

VIDEO_W, VIDEO_H = 1920, 1080
TOL_M = 2.0        # допуск на координату карточки (округление 5-го знака ≈ 1.1 м)
TOL_ALT = 3.0
TOL_GPS_M = 5.0    # gps-точки: карточки копировались из разных выгрузок телеметрии

_rows_cache = {}


def dist_m(la1, lo1, la2, lo2):
    return math.hypot((la2 - la1) * 111132.0,
                      (lo2 - lo1) * 111320.0 * math.cos(math.radians(la1)))


def find_video(token):
    """Путь видео по токену (шестизначное время, полное имя, кусок имени)."""
    hits = sorted(ROOT.glob(f"data/drive/**/*{token}*.MP4"))
    hits = [h for h in hits if h.with_suffix(h.suffix + ".gps.tsv").exists()]
    if not hits:
        raise ValueError(f"видео по токену «{token}» с телеметрией не найдено")
    if len(hits) > 1:
        # дубликаты вида *_fixed.MP4: предпочесть файл, чьё имя кончается ровно токеном
        exact = [h for h in hits if h.stem.endswith(token)]
        if len(exact) == 1:
            return exact[0]
        raise ValueError(f"токен «{token}» неоднозначен: {[h.name for h in hits]}")
    return hits[0]


def rows_for(token):
    if token not in _rows_cache:
        _rows_cache[token] = load_rows(find_video(token))
    return _rows_cache[token]


def telem(token, t):
    rows = rows_for(token)
    vals = {k: at(rows, t, k) for k in ("lat", "lon", "alt_m", "gb_yaw", "gb_pitch")}
    if not all(math.isfinite(v) for v in vals.values()):
        raise ValueError(f"{token} t={t}: провал телеметрии")
    vals["gb_yaw"] = (vals["gb_yaw"] + 180) % 360 - 180
    return vals


def focal_of(c):
    """Фокусное рецепта: число либо "cov" — самокалибровка конвейера покрытия."""
    f = c.get("f")
    if f != "cov":
        return f
    video = find_video(c["video"])
    cov = ROOT / f"analysis/coverage/{video.name}.coverage.tsv"
    best = None
    for line in cov.read_text().splitlines()[1:]:
        parts = dict(zip(("t", "f_px"), line.split("\t")))
        try:
            t, fpx = float(parts["t"]), float(parts["f_px"])
        except (ValueError, KeyError):
            continue
        if best is None or abs(t - c["t"]) < abs(best[0] - c["t"]):
            best = (t, fpx)
    if best is None:
        raise ValueError(f"в {cov.name} нет фокусного")
    return best[1]


def unc_variants(computed):
    """Допустимые записи unc карточки для посчитанной величины: точное значение,
    потолок до целого или до кратного 5 — округление только вверх."""
    c = math.ceil(computed - 1e-9)
    return {computed, float(c), float(max(5 * math.ceil(c / 5), 5))}


def fmt_ru(v):
    """Число по-русски: 23; 8,5; 0,62."""
    if v >= 10:
        s = f"{v:.0f}"
    elif v >= 1:
        s = f"{v:.1f}".rstrip("0").rstrip(".")
    else:
        s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


# --- координатные методы --------------------------------------------------------


def _cast_once(dem, c, detail):
    """Общий низ cast/c_center: рецепт → (lat, lon, alt, dist, unc), detail←журнал."""
    tv = telem(c["video"], c["t"])
    center = c.get("m") == "c_center"
    px = VIDEO_W / 2 if center else c["px"]
    py = VIDEO_H / 2 if center else c["py"]
    f = focal_of(c)
    if f is None:
        if not center or "off_px" in c:
            raise ValueError("в рецепте нет фокусного f (число или \"cov\")")
        f = 1000.0   # чистому центральному лучу фокусное не нужно
    detail.append(f"дрон {tv['lat']:.6f}, {tv['lon']:.6f}, {tv['alt_m']:.0f} м; "
                  f"подвес yaw {tv['gb_yaw']:.1f} pitch {tv['gb_pitch']:.1f}; "
                  + ("центр кадра" if center else f"пиксель ({px:.0f},{py:.0f})")
                  + f", f={f:.0f}")
    direction = ray_dir(tv["gb_yaw"], tv["gb_pitch"], px, py, VIDEO_W, VIDEO_H, f)
    hit = cast(dem, tv["lat"], tv["lon"], tv["alt_m"], direction)
    if hit is None:
        raise ValueError(f"{c['video']} t={c['t']}: луч не пересёк рельеф")
    la, lo, ground, dist = hit
    unc = 0.0
    # величина вилки DEM: ±30 м, но не больше высоты дрона над рельефом —
    # летящий дрон сам ограничивает ошибку DEM в своей окрестности сверху
    agl = tv["alt_m"] - dem.elev(tv["lat"], tv["lon"])
    dz_max = min(30.0, max(agl - 2.0, 5.0))
    if dz_max < 30:
        detail.append(f"вилка DEM ограничена {dz_max:.0f} м: дрон в {agl:.0f} м "
                      "над рельефом — большая ошибка DEM противоречит его GPS")
    for dz in (+dz_max, -dz_max):
        h2 = cast(_ShiftDem(dem, dz), tv["lat"], tv["lon"], tv["alt_m"], direction)
        if h2 is None:
            raise ValueError(f"{c['video']} t={c['t']}: вилка DEM{dz:+.0f} без пересечения "
                             "— координатой не считать")
        drift = dist_m(la, lo, h2[0], h2[1])
        detail.append(f"вилка DEM{dz:+.0f} м: увод {drift:.0f} м")
        unc = max(unc, drift)
    for key in ("f_lo", "f_hi"):
        if key in c:
            d2 = ray_dir(tv["gb_yaw"], tv["gb_pitch"], px, py, VIDEO_W, VIDEO_H, c[key])
            h2 = cast(dem, tv["lat"], tv["lon"], tv["alt_m"], d2)
            if h2 is None:
                raise ValueError(f"{c['video']} t={c['t']}: вилка f={c[key]} без пересечения")
            drift = dist_m(la, lo, h2[0], h2[1])
            detail.append(f"вилка f={c[key]:.0f}: увод {drift:.0f} м")
            unc = max(unc, drift)
    if "off_px" in c:
        off_m = dist * c["off_px"] / f
        detail.append(f"объект до {c['off_px']:.0f} пикс от луча: +{off_m:.0f} м к unc")
        unc += off_m
    if "extra_unc" in c:
        detail.append(f"заявленная добавка (см. coord): +{c['extra_unc']} м")
        unc += c["extra_unc"]
    detail.append(f"точка {la:.6f}, {lo:.6f}, {ground:.0f} м; дистанция {dist:.0f} м; "
                  f"unc {unc:.0f} м")
    return la, lo, ground, dist, unc


def calc_cast(dem, c, detail):
    la, lo, ground, dist, unc = _cast_once(dem, c, detail)
    return dict(lat=la, lon=lo, alt=ground, unc=unc, dist=dist)


def calc_mean(dem, c, detail):
    hits = []
    unc = 0.0
    for i, o in enumerate(c["obs"], 1):
        detail.append(f"— наблюдение {i}: {o['video']} t={o['t']}")
        la, lo, ground, _, u = _cast_once(dem, o, detail)
        hits.append((la, lo, ground))
        unc = max(unc, u)
    cluster = [(sum(h[0] for h in hits) / len(hits),
                sum(h[1] for h in hits) / len(hits),
                sum(h[2] for h in hits) / len(hits))]
    spread = max((dist_m(*cluster[0][:2], h[0], h[1]) for h in hits), default=0.0)
    detail.append(f"среднее наших наблюдений: {cluster[0][0]:.6f}, {cluster[0][1]:.6f}, "
                  f"{cluster[0][2]:.0f} м; разброс {spread:.0f} м")
    for la, lo, *rest in c.get("also", []):
        label = rest[0] if rest else "чужая оценка"
        cluster.append((la, lo, None))
        detail.append(f"чужая оценка ({label}): {la:.6f}, {lo:.6f}")
    lat = sum(p[0] for p in cluster) / len(cluster)
    lon = sum(p[1] for p in cluster) / len(cluster)
    alt = cluster[0][2]
    spread = max(spread, max(dist_m(lat, lon, p[0], p[1]) for p in cluster))
    unc = max(unc, spread)
    alt_spread = max(abs(h[2] - alt) for h in hits)
    detail.append(f"итог: {lat:.6f}, {lon:.6f}, {alt:.0f} м; unc {unc:.0f} м "
                  f"(вилки наблюдений и разброс оценок)")
    return dict(lat=lat, lon=lon, alt=alt, unc=unc, alt_unc=max(alt_spread, 0.0) or None)


def calc_triang(dem, c, detail):
    specs = []
    for s in c["specs"]:
        kind, path, rest = s.split(":", 2)
        specs.append(f"{kind}:{ROOT / path}:{rest}")
    r = solve_rays(specs)
    for tag, dist, resid, sig in r["rays"]:
        detail.append(f"луч …{tag[-58:]}: дистанция {dist:.0f} м, остаток {resid:.1f} м")
    sig_h = max(r["sigma"][:2])
    detail.append(f"координата {r['lat']:.6f}, {r['lon']:.6f}, {r['alt']:.0f} м; "
                  f"σ гориз. {sig_h:.0f} м, худший остаток {r['worst']:.1f} м")
    unc = max(sig_h, r["worst"]) + c.get("extra_unc", 0)
    if c.get("extra_unc"):
        detail.append(f"+ записанные вилки входов {c['extra_unc']} м → unc {unc:.0f} м")
    return dict(lat=r["lat"], lon=r["lon"], alt=r["alt"], unc=unc,
                alt_unc=max(r["sigma"][2] if len(r["sigma"]) > 2 else 0, r["worst"])
                + c.get("extra_unc", 0))


def lrf_target(name):
    import re
    hits = sorted(ROOT.glob(f"data/drive/**/{name}"))
    if not hits:
        raise ValueError(f"фото {name} не найдено")
    raw = hits[0].read_bytes()
    m = re.search(rb"<x:xmpmeta.*?</x:xmpmeta>", raw, re.S)
    x = m.group(0).decode("utf-8", "replace")
    vals = dict(re.findall(r'drone-dji:(LRFTarget\w+)="([^"]*)"', x))
    if "LRFTargetLat" not in vals:
        raise ValueError(f"{name}: в XMP нет цели дальномера")
    return (float(vals["LRFTargetLat"]), float(vals["LRFTargetLon"]),
            float(vals["LRFTargetAbsAlt"]), float(vals["LRFTargetDistance"]))


def calc_lrf(dem, c, detail):
    pts = []
    for name in c["photos"]:
        la, lo, al, d = lrf_target(name)
        pts.append((la, lo, al))
        detail.append(f"{name}: цель дальномера {la:.6f}, {lo:.6f}, {al:.1f} м "
                      f"(дистанция {d:.0f} м)")
    lat = sum(p[0] for p in pts) / len(pts)
    lon = sum(p[1] for p in pts) / len(pts)
    alt = sum(p[2] for p in pts) / len(pts)
    unc = None
    if len(pts) > 1:
        unc = max(dist_m(lat, lon, p[0], p[1]) for p in pts) + c.get("extra_unc", 0)
        detail.append(f"среднее {lat:.6f}, {lon:.6f}, {alt:.0f} м; разброс → unc {unc:.0f} м")
    if any("_SUPR" in n for n in c["photos"]):
        # дальномер SUPR-панорам точечно непригоден: медиана −58 м по высоте,
        # промахи >40 м у 62% замеров (docs/koordinaty-status.md) — пол вилки 40 м
        unc = max(unc or 0, 40.0)
        detail.append("SUPR-панорама: пол вилки дальномера 40 м")
    return dict(lat=lat, lon=lon, alt=alt, unc=unc,
                alt_unc=40.0 if any("_SUPR" in n for n in c["photos"]) else None)


def calc_gps(dem, c, detail):
    tv = telem(c["video"], c["t"])
    detail.append(f"позиция дрона {c['video']} t={c['t']}: "
                  f"{tv['lat']:.6f}, {tv['lon']:.6f}, {tv['alt_m']:.0f} м")
    return dict(lat=tv["lat"], lon=tv["lon"], alt=tv["alt_m"], unc=None, tol=TOL_GPS_M)


def calc_footprint(dem, c, detail):
    tv = telem(c["video"], c["t"])
    f = focal_of(c)
    ground = dem.elev(tv["lat"], tv["lon"])
    h_agl = tv["alt_m"] - ground
    radius = h_agl * math.hypot(VIDEO_W, VIDEO_H) / 2 / f
    detail.append(f"позиция дрона {tv['lat']:.6f}, {tv['lon']:.6f}, {tv['alt_m']:.0f} м; "
                  f"рельеф {ground:.0f} м, высота над склоном {h_agl:.0f} м")
    detail.append(f"радиус пятна кадра при f={f:.0f}: {radius:.0f} м → unc")
    return dict(lat=tv["lat"], lon=tv["lon"], alt=tv["alt_m"], unc=radius, tol=TOL_GPS_M)


def calc_registry(dem, c, detail):
    text = REGISTRY.read_text(encoding="utf-8")
    if c["match"] not in text:
        raise ValueError(f"строки «{c['match']}» нет в {REGISTRY.relative_to(ROOT)} "
                         "— карта разошлась с реестром")
    la, lo = (float(x) for x in c["match"].split(","))
    detail.append(f"цитата «{c['match']}» найдена в реестре находок")
    return dict(lat=la, lon=lo, alt=None, unc=None)


def calc_quoted(dem, c, detail):
    src = ROOT / c["src"]
    if c["quote"] not in src.read_text(encoding="utf-8"):
        raise ValueError(f"цитаты «{c['quote']}» нет в {c['src']} — число повисло в воздухе")
    detail.append(f"входы расчёта не сохранены, пересчёт невозможен; "
                  f"цитата «{c['quote']}» сверена с {c['src']}")
    return dict(lat=None, lon=None, alt=None, unc=None, quoted=True)


METHODS = dict(cast=calc_cast, c_center=calc_cast, mean=calc_mean, triang=calc_triang,
               gps=calc_gps, lrf=calc_lrf, footprint=calc_footprint,
               registry=calc_registry, quoted=calc_quoted)


def compute_coord(dem, calc):
    """Исполнить рецепт → (dict(lat, lon, alt, unc, …), журнал строк)."""
    detail = []
    res = METHODS[calc["m"]](dem, calc, detail)
    return res, detail


# --- размер ---------------------------------------------------------------------


def compute_size(sc):
    """Рецепт размера → dict(lo, hi, unit, text) — интервал с перемноженными вилками."""
    px_lo, px_hi = sc.get("px_lo", sc.get("px")), sc.get("px_hi", sc.get("px"))
    d_lo, d_hi = sc.get("dist_lo", sc.get("dist")), sc.get("dist_hi", sc.get("dist"))
    f_lo, f_hi = sc.get("f_lo", sc.get("f")), sc.get("f_hi", sc.get("f"))
    if None in (px_lo, px_hi, d_lo, d_hi, f_lo, f_hi):
        raise ValueError("size_calc: нужны px, dist, f (или их вилки *_lo/*_hi)")
    k = 100.0 if sc.get("unit", "м") == "см" else 1.0
    lo = px_lo * d_lo / f_hi * k
    hi = px_hi * d_hi / f_lo * k
    unit = sc.get("unit", "м")
    text = f"{fmt_ru(lo)}–{fmt_ru(hi)} {unit}"
    if sc.get("label"):
        text = f"{sc['label']} {text}"
    return dict(lo=lo, hi=hi, unit=unit, text=text)


# --- сверка карточки с пересчётом -------------------------------------------------


def check_point(dem, p):
    """(проблемы: [str], журнал: [str]) — пусто в первом значении = карточка сходится."""
    problems, detail = [], []
    calc = p.get("calc")
    if calc:
        try:
            res, detail = compute_coord(dem, calc)
        except Exception as e:  # noqa: BLE001 — любую поломку рецепта показать по имени точки
            return [f"рецепт не исполнился: {e}"], detail
        tol = res.get("tol", TOL_M)
        if res.get("lat") is not None:
            d = dist_m(p["lat"], p["lon"], res["lat"], res["lon"])
            if d > tol:
                problems.append(f"координата разошлась на {d:.1f} м "
                                f"(карточка {p['lat']}, {p['lon']} ↔ "
                                f"расчёт {res['lat']:.6f}, {res['lon']:.6f})")
        if res.get("alt") is not None and abs(p["alt"] - res["alt"]) > max(TOL_ALT, tol):
            problems.append(f"высота разошлась: карточка {p['alt']} ↔ расчёт {res['alt']:.0f}")
        if res.get("unc") is not None:
            card_unc = p.get("unc")
            if card_unc is None:
                problems.append(f"unc не записан, расчёт даёт {res['unc']:.0f} м")
            elif float(card_unc) not in unc_variants(res["unc"]):
                problems.append(f"unc {card_unc} не из расчёта: посчитано {res['unc']:.1f} м "
                                f"(допустимо {sorted(unc_variants(res['unc']))})")
    if "size_calc" in p:
        try:
            size = compute_size(p["size_calc"])
            detail.append(f"размер: {size['text']}")
        except Exception as e:  # noqa: BLE001
            problems.append(f"size_calc не исполнился: {e}")
        if "size" in p:
            problems.append("size= задан вручную при наличии size_calc= — источник один")
    return problems, detail


def verify_points(dem, points, resolve_fix=None):
    """Все точки: рецепт calc= обязателен каждой и обязан сходиться с карточкой.

    Возвращает [(имя, [проблемы])] — пусто = карта честная. resolve_fix(p) —
    функция определения ступени (у build_map своя, с выводом из coord)."""
    bad = []
    for p in points:
        fix = resolve_fix(p) if resolve_fix else p.get("fix")
        problems = []
        if "calc" not in p:
            problems.append(f"нет рецепта calc= (ступень «{fix}») — число нельзя пересчитать")
        else:
            problems += check_point(dem, p)[0]
        if problems:
            bad.append((p["name"], problems))
    return bad


# --- CLI --------------------------------------------------------------------------


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parent / "viewer"))
    import build_map

    dem = Dem()
    query = " ".join(sys.argv[1:]).strip().lower()
    points = [p for p in build_map.POINTS if not query or query in p["name"].lower()]
    if not points:
        raise SystemExit(f"точек по запросу «{query}» нет")

    fail = False
    for p in points:
        fix = build_map.resolve_fix(p)
        if "calc" not in p:
            fail = True
            print(f"[!!] {p['name']}: нет рецепта calc= (ступень «{fix}»)")
            continue
        problems, detail = check_point(dem, p)
        mark = "ОШИБКА" if problems else "ok"
        fail = fail or bool(problems)
        print(f"[{mark}] {p['name']} (ступень {fix}, метод "
              f"{p.get('calc', {}).get('m', '—')})")
        if query:
            for line in detail:
                print(f"    {line}")
            print(f"    карточка: {p['lat']}, {p['lon']}, {p['alt']} м, "
                  f"unc={p.get('unc', '—')}")
        for pr in problems:
            print(f"    !! {pr}")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
