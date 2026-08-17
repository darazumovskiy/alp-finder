#!/usr/bin/env python3
"""Покрытие произвольного полигона склона кадрами всех роликов с телеметрией.

Метод — проекция рамки кадра (video_footprint_hits): для каждого сэмпла
телеметрии кадр печатается в DEM сеткой лучей по всему полю зрения с реальным
фокусным момента из analysis/coverage/*.coverage.tsv; масштаб (различим ли
предмет: порог 8 px из стенда врезок) пересчитан на дистанцию каждого луча.
Моменты без измеренного фокусного дают только центральный луч с допуском 75 м
и считаются «смотрели, масштаб неизвестен». Ячейка полигона — «смотрели»,
если хотя бы одно попадание легло ближе своего радиуса закраски.

Лучи маршируются в DEM независимо: мёртвые зоны за перегибами рельефа
внутри рамки кадра не закрашиваются. Прежний метод «только центр кадра
с допуском 75 м» (как в docs/nezavisimyy-analiz/01-probel-pokrytiya.md
внешней группы) доступен функцией video_hits.

Использование:
  analysis/.venv/bin/python analysis/coverage_polygon.py ПОЛИГОН.json
  где ПОЛИГОН.json = [[lat, lon], ...]
Выход: таблица по ячейкам (--out-cells), сводка в stdout.
"""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from geoproject import Dem, cast, ray_dir, load_rows, interp_gap, unwrap_deg  # noqa: E402

DATA = HERE.parent / "data" / "drive"
COV = HERE / "coverage"

M_PER_DEG_LAT = 111132.0
THRESH_OBJ_CM = 100    # предмет 1 м (рюкзак/человек) при пороге 8 px


def m_per_deg_lon(lat):
    return 111320.0 * math.cos(math.radians(lat))


def point_in_poly(lat, lon, poly):
    inside = False
    n = len(poly)
    for i in range(n):
        la1, lo1 = poly[i]
        la2, lo2 = poly[(i + 1) % n]
        if (lo1 > lon) != (lo2 > lon):
            t = (lon - lo1) / (lo2 - lo1)
            if lat < la1 + t * (la2 - la1):
                inside = not inside
    return inside


def gsd_table(video_name):
    """t -> obj8px_cm по готовому расчёту покрытия; None если расчёта нет."""
    path = COV / (video_name + ".coverage.tsv")
    if not path.exists():
        return None
    table = {}
    for line in path.read_text().splitlines()[1:]:
        t, _f, _d, _g, o8, status = line.split("\t")
        table[round(float(t), 1)] = float(o8) if status == "ok" and o8 else None
    return table


class MomentTable:
    """ok-строки *.coverage.tsv: (obj8px_cm, f_px, dist_m) ближайшего момента ±0.6 с.

    Точный lookup по ключу ломается молча, если tsv пересчитан с другим
    шагом/стартом сетки — тогда весь ролик уходил бы в «центр+75 м без
    масштаба» (ревью 14.08, п. 7). Поэтому — ближайшая строка с допуском.
    """

    TOL_S = 0.6

    def __init__(self, rows):
        self.ts = sorted(rows)
        self.rows = rows

    def get(self, t):
        exact = self.rows.get(round(t, 1))
        if exact is not None:
            return exact
        import bisect
        k = bisect.bisect_left(self.ts, t)
        best = None
        for i in (k - 1, k):
            if 0 <= i < len(self.ts) and abs(self.ts[i] - t) <= self.TOL_S:
                if best is None or abs(self.ts[i] - t) < abs(best - t):
                    best = self.ts[i]
        return self.rows[best] if best is not None else None


def moment_table(video_name):
    """MomentTable по готовому расчёту покрытия; None если расчёта нет."""
    path = COV / (video_name + ".coverage.tsv")
    if not path.exists():
        return None
    rows = {}
    for line in path.read_text().splitlines()[1:]:
        t, f, d, _g, o8, status = line.split("\t")
        if status == "ok" and f and d and o8:
            rows[round(float(t), 1)] = (float(o8), float(f), float(d))
    return MomentTable(rows)


def video_hits(video, dem, step, bbox):
    """[(lat, lon, obj8px_cm | nan, t_s)] — попадания центра кадра в рельеф внутри bbox."""
    rows = load_rows(video)
    if not rows or "gb_yaw" not in rows[0]:
        return []
    t_arr = np.array([r["time_s"] for r in rows])
    fields = {k: np.array([r.get(k, math.nan) for r in rows])
              for k in ("lat", "lon", "alt_m", "gb_pitch")}
    yaw = unwrap_deg([r.get("gb_yaw", math.nan) for r in rows])
    gsd = gsd_table(video.name)

    hits = []
    t = float(t_arr[0])
    while t <= float(t_arr[-1]):
        la = interp_gap(t, t_arr, fields["lat"])
        lo = interp_gap(t, t_arr, fields["lon"])
        al = interp_gap(t, t_arr, fields["alt_m"])
        yw = interp_gap(t, t_arr, yaw)
        pt = interp_gap(t, t_arr, fields["gb_pitch"])
        if not all(math.isfinite(v) for v in (la, lo, al, yw, pt)):
            t += step
            continue
        # центр кадра: фокусное на направление луча не влияет
        hit = cast(dem, la, lo, al, ray_dir(yw % 360, pt, 960, 540, 1920, 1080, 1000))
        if hit is not None:
            hla, hlo = hit[0], hit[1]
            if bbox[0] <= hla <= bbox[1] and bbox[2] <= hlo <= bbox[3]:
                o8 = math.nan
                if gsd is not None:
                    v = gsd.get(round(t, 1))
                    if v is not None:
                        o8 = v
                hits.append((hla, hlo, o8, t))
        t += step
    return hits


FP_GRID_X = 13       # лучей по ширине кадра
FP_GRID_Y = 8        # лучей по высоте кадра
FP_R_MIN_M = 21.0    # закраска попадания не уже полудиагонали ячейки 30 м
FP_R_MAX_M = 40.0    # и не шире: не наводить мосты через мёртвые зоны
FP_CENTER_R_M = 75.0 # без фокусного рамка кадра неизвестна — прежний допуск центра
FP_PROFILE = 1 / 3   # профиль лежащего 3D-предмета относительно длины (рюкзак, человек)


def _slope_stretch(dem, lat, lon, direction):
    """Во сколько раз крупнее должен быть лежащий предмет при скользящем луче.

    Видимый размер предмета длиной L с профилем L·FP_PROFILE под углом
    скольжения γ к плоскости склона: L·(sin γ + FP_PROFILE·cos γ) — при
    фронтальном взгляде растяжения нет, при скользящем видно профиль
    (потолок 1/FP_PROFILE = 3×). Полный 1/sin γ был бы честен только для
    плоских целей (борозды, расстеленная ткань) и хоронит объёмные.
    Нормаль склона — конечными разностями DEM ±15 м."""
    dlat = 15.0 / M_PER_DEG_LAT
    dlon = 15.0 / m_per_deg_lon(lat)
    try:
        dzdx = (dem.elev(lat, lon + dlon) - dem.elev(lat, lon - dlon)) / 30.0
        dzdy = (dem.elev(lat + dlat, lon) - dem.elev(lat - dlat, lon)) / 30.0
    except ValueError:
        return 1.0
    nx, ny, nz = -dzdx, -dzdy, 1.0
    nn = math.sqrt(nx * nx + ny * ny + nz * nz)
    de, dn, du = direction
    sin_g = min(abs(de * nx + dn * ny + du * nz) / nn, 1.0)
    cos_g = math.sqrt(1.0 - sin_g * sin_g)
    return 1.0 / (sin_g + FP_PROFILE * cos_g)


def _hull(points):
    """Выпуклая оболочка 2D-точек (monotone chain), точки — (x, y) в метрах."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts
    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2 and ((out[-1][0] - out[-2][0]) * (p[1] - out[-2][1])
                                     - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0])) <= 0:
                out.pop()
            out.append(p)
        return out[:-1]
    return half(pts) + half(pts[::-1])


def _dist_to_hull_edge(p, hull):
    """Расстояние от внутренней точки до границы оболочки (0 — на границе/вне)."""
    if len(hull) < 3:
        return 0.0
    best = math.inf
    inside = True
    for a, b in zip(hull, hull[1:] + hull[:1]):
        ax, ay = a; bx, by = b
        dx, dy = bx - ax, by - ay
        cross = dx * (p[1] - ay) - dy * (p[0] - ax)
        if cross < 0:
            inside = False
        L2 = dx * dx + dy * dy
        t = 0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
        best = min(best, math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy)))
    return best if inside else 0.0


def video_footprint_hits(video, dem, step, bbox):
    """[(lat, lon, obj8px_cm | nan, t_s, paint_r_m)] — проекция рамки кадра в рельеф.

    Моменты с измеренным фокусным печатаются сеткой лучей FP_GRID_X × FP_GRID_Y
    по всему кадру: каждый луч маршируется в DEM независимо, поэтому мёртвые зоны
    за перегибами внутри рамки шире 2×FP_R_MAX_M не закрашиваются. Лучи стоят
    в центрах ячеек сетки (полшага от кромки кадра). Радиус закраски —
    0.6 фактического шага соседних попаданий на местности, в пределах
    [FP_R_MIN_M, FP_R_MAX_M], и дополнительно не дальше границы выпуклой
    оболочки попаданий кадра + FP_R_MIN_M — закраска не выходит за реальную
    рамку (аудит и независимое ревью 14.08: краевые лучи с изотропным радиусом
    красили до 20% «средних» ячеек за рамкой).

    Масштаб попадания — obj8px центра × (дистанция луча / дистанция центра из
    tsv) × растяжение наклона (луч, скользящий по склону, видит лежащий предмет
    в ракурсе — ревью 14.08 п. 3: медианное растяжение 2×). Сэмплы, где дрон
    «под» сглаженным DEM, дают ложно близкие дистанции — их масштабу не
    доверяем (NaN → «обзорно», ревью п. 5). Моменты без фокусного — как в
    video_hits: центральный луч с допуском FP_CENTER_R_M и без масштаба.
    """
    rows = load_rows(video)
    if not rows or "gb_yaw" not in rows[0]:
        return []
    t_arr = np.array([r["time_s"] for r in rows])
    fields = {k: np.array([r.get(k, math.nan) for r in rows])
              for k in ("lat", "lon", "alt_m", "gb_pitch")}
    yaw = unwrap_deg([r.get("gb_yaw", math.nan) for r in rows])
    moments = moment_table(video.name)

    pxs = (np.arange(FP_GRID_X) + 0.5) * 1920 / FP_GRID_X
    pys = (np.arange(FP_GRID_Y) + 0.5) * 1080 / FP_GRID_Y

    hits = []
    t = float(t_arr[0])
    while t <= float(t_arr[-1]):
        la = interp_gap(t, t_arr, fields["lat"])
        lo = interp_gap(t, t_arr, fields["lon"])
        al = interp_gap(t, t_arr, fields["alt_m"])
        yw = interp_gap(t, t_arr, yaw)
        pt = interp_gap(t, t_arr, fields["gb_pitch"])
        if not all(math.isfinite(v) for v in (la, lo, al, yw, pt)):
            t += step
            continue
        yw %= 360
        m = moments.get(round(t, 1)) if moments else None

        if m is None:
            hit = cast(dem, la, lo, al, ray_dir(yw, pt, 960, 540, 1920, 1080, 1000))
            if hit is not None and _in_bbox(hit, bbox):
                hits.append((hit[0], hit[1], math.nan, t, FP_CENTER_R_M))
            t += step
            continue

        o8, f, dist0 = m
        try:
            under_dem = dem.elev(la, lo) > al
        except ValueError:
            under_dem = True
        grid = {}
        dirs = {}
        for gi, px in enumerate(pxs):
            for gj, py in enumerate(pys):
                d = ray_dir(yw, pt, px, py, 1920, 1080, f)
                h = cast(dem, la, lo, al, d)
                if h is not None:
                    grid[(gi, gj)] = h
                    dirs[(gi, gj)] = d
        # локальные метры относительно дрона — для оболочки рамки
        mlon = m_per_deg_lon(la)
        xy = {k: ((h[1] - lo) * mlon, (h[0] - la) * M_PER_DEG_LAT)
              for k, h in grid.items()}
        hull = _hull(list(xy.values()))
        for (gi, gj), h in grid.items():
            gaps = []
            for ni, nj in ((gi + 1, gj), (gi - 1, gj), (gi, gj + 1), (gi, gj - 1)):
                nb = grid.get((ni, nj))
                if nb is not None:
                    gaps.append(math.hypot(
                        (nb[0] - h[0]) * M_PER_DEG_LAT,
                        (nb[1] - h[1]) * m_per_deg_lon(h[0])))
            r = min(max(0.6 * max(gaps) if gaps else FP_R_MIN_M, FP_R_MIN_M), FP_R_MAX_M)
            r = min(r, _dist_to_hull_edge(xy[(gi, gj)], hull) + FP_R_MIN_M)
            if _in_bbox(h, bbox):
                if under_dem:
                    o8_hit = math.nan
                else:
                    o8_hit = o8 * h[3] / dist0 * _slope_stretch(dem, h[0], h[1], dirs[(gi, gj)])
                hits.append((h[0], h[1], o8_hit, t, r))
        t += step
    return hits


def _in_bbox(hit, bbox):
    return bbox[0] <= hit[0] <= bbox[1] and bbox[2] <= hit[1] <= bbox[3]


# --- строгая печать кадра (пересчёт 17.08 после кейса сцены 16.08) -------------
#
# Прежний метод закрашивал ячейку, если её центр в 21–40 м от попадания луча:
# детальность кадра могла «дотянуться» до места в 40 м от реально снятого
# склона (ячейка сцены 16.08 стала «детальной 5 см» по кадрам осыпи в 42 м
# ниже сцены — analysis/viewer/otchet-2026-08-17-koshki-pokrytie.html).
# Строгий метод: там, где кадр претендует на масштаб (детально/средне),
# сетка лучей адаптивно сгущается квадродеревом до шага ≤ FP_STRICT_GAP_M
# на местности, и радиус закраски попадания ограничен FP_STRICT_R_MAX_M —
# закраска не выходит за фактически измеренные лучи дальше, чем на шаг
# интерполяции между соседними лучами. Грубые попадания (различим только
# предмет > FP_STRICT_OBJ_CM) и моменты без фокусного остаются на прежних
# правилах — их уровень и так лишь «обзорно».

FP_STRICT_GAP_M = 8.0      # целевой шаг соседних лучей на местности
FP_STRICT_R_MIN_M = 6.0    # радиус закраски строгого попадания
FP_STRICT_R_MAX_M = 10.0   # (меньше полудиагонали ячейки 15 м — не «мостит»)
FP_STRICT_OBJ_CM = 150     # порог «претендует на масштаб» → строгий путь
FP_STRICT_DEPTH = 7        # глубина квадродерева
FP_STRICT_RAY_BUDGET = 3000  # потолок лучей на сэмпл (защита от разноса)


def video_footprint_hits_strict(video, dem, step, bbox):
    """[(lat, lon, obj8px_cm | nan, t_s, paint_r_m)] — строгая проекция рамки.

    Отличия от video_footprint_hits: попадания с масштабом детально/средне
    (obj8px ≤ FP_STRICT_OBJ_CM) закрашивают не дальше FP_STRICT_R_MAX_M от
    реального луча, а сетка лучей в таких зонах сгущается квадродеревом до
    шага FP_STRICT_GAP_M — детальность приписывается только реально снятой
    земле. Мёртвые зоны за перегибами не закрашиваются (лучи независимы).
    """
    rows = load_rows(video)
    if not rows or "gb_yaw" not in rows[0]:
        return []
    t_arr = np.array([r["time_s"] for r in rows])
    fields = {k: np.array([r.get(k, math.nan) for r in rows])
              for k in ("lat", "lon", "alt_m", "gb_pitch")}
    yaw = unwrap_deg([r.get("gb_yaw", math.nan) for r in rows])
    moments = moment_table(video.name)

    pxs = (np.arange(FP_GRID_X) + 0.5) * 1920 / FP_GRID_X
    pys = (np.arange(FP_GRID_Y) + 0.5) * 1080 / FP_GRID_Y

    stretch_cache = {}

    def stretch_at(lat, lon, dirv):
        key = (round(lat * 1e4), round(lon * 1e4))
        v = stretch_cache.get(key)
        if v is None:
            v = _slope_stretch(dem, lat, lon, dirv)
            stretch_cache[key] = v
        return v

    hits = []
    t = float(t_arr[0])
    while t <= float(t_arr[-1]):
        la = interp_gap(t, t_arr, fields["lat"])
        lo = interp_gap(t, t_arr, fields["lon"])
        al = interp_gap(t, t_arr, fields["alt_m"])
        yw = interp_gap(t, t_arr, yaw)
        pt = interp_gap(t, t_arr, fields["gb_pitch"])
        if not all(math.isfinite(v) for v in (la, lo, al, yw, pt)):
            t += step
            continue
        yw %= 360
        m = moments.get(round(t, 1)) if moments else None

        if m is None:
            hit = cast(dem, la, lo, al, ray_dir(yw, pt, 960, 540, 1920, 1080, 1000))
            if hit is not None and _in_bbox(hit, bbox):
                hits.append((hit[0], hit[1], math.nan, t, FP_CENTER_R_M))
            t += step
            continue

        o8, f, dist0 = m
        try:
            under_dem = dem.elev(la, lo) > al
        except ValueError:
            under_dem = True

        n_rays = 0
        cache = {}     # (px, py) округлённые -> hit | None

        def shoot(px, py):
            nonlocal n_rays
            key = (round(px, 1), round(py, 1))
            if key in cache:
                return cache[key]
            n_rays += 1
            d = ray_dir(yw, pt, px, py, 1920, 1080, f)
            h = cast(dem, la, lo, al, d)
            if h is not None and under_dem:
                h = (h[0], h[1], h[2], math.nan)   # дистанции не доверяем
            cache[key] = (h, d)
            return cache[key]

        def o8_at(h, d):
            if h is None or not math.isfinite(h[3]):
                return math.nan
            return o8 * h[3] / dist0 * stretch_at(h[0], h[1], d)

        def gdist(h1, h2):
            return math.hypot((h1[0] - h2[0]) * M_PER_DEG_LAT,
                              (h1[1] - h2[1]) * m_per_deg_lon(h1[0]))

        emitted = set()

        def emit(px, py, h, d, spacing):
            key = (round(px, 1), round(py, 1))
            if key in emitted or h is None or not _in_bbox(h, bbox):
                return
            emitted.add(key)
            o8h = o8_at(h, d)
            if math.isfinite(o8h) and o8h <= FP_STRICT_OBJ_CM:
                r = min(max(0.75 * spacing, FP_STRICT_R_MIN_M), FP_STRICT_R_MAX_M)
            else:
                r = min(max(0.6 * spacing, FP_R_MIN_M), FP_R_MAX_M)
            hits.append((h[0], h[1], o8h, t, r))

        def quad(p00, p11, depth):
            """Квадрат кадра в пикселях: (x0,y0), (x1,y1) — рекурсивное сгущение."""
            (x0, y0), (x1, y1) = p00, p11
            corners = [shoot(x0, y0), shoot(x1, y0), shoot(x0, y1), shoot(x1, y1)]
            hs = [c for c in corners if c[0] is not None]
            if not hs:
                return
            ext = max((gdist(a[0], b[0]) for a in hs for b in hs), default=0.0)
            best_o8 = min((o8_at(h, d) for h, d in hs
                           if math.isfinite(o8_at(h, d))), default=math.inf)
            partial = len(hs) < 4
            need = (ext > FP_STRICT_GAP_M and best_o8 <= FP_STRICT_OBJ_CM
                    and depth < FP_STRICT_DEPTH and n_rays < FP_STRICT_RAY_BUDGET
                    and min(x1 - x0, y1 - y0) > 2.0)
            if partial:
                need = need and depth < 2   # кромку рамки уточняем неглубоко
            if need:
                xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
                quad((x0, y0), (xm, ym), depth + 1)
                quad((xm, y0), (x1, ym), depth + 1)
                quad((x0, ym), (xm, y1), depth + 1)
                quad((xm, ym), (x1, y1), depth + 1)
            else:
                spacing = ext if ext > 0 else FP_STRICT_GAP_M
                for (h, d), (px, py) in zip(
                        corners, [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]):
                    if h is not None:
                        emit(px, py, h, d, spacing)

        for gi in range(FP_GRID_X - 1):
            for gj in range(FP_GRID_Y - 1):
                quad((pxs[gi], pys[gj]), (pxs[gi + 1], pys[gj + 1]), 0)
        t += step
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("poly", help="JSON-файл [[lat, lon], ...]")
    ap.add_argument("--cell", type=float, default=30.0, help="шаг сетки, м")
    ap.add_argument("--step", type=float, default=2.0, help="шаг сэмплов телеметрии, с")
    ap.add_argument("--out-cells", default=None, help="TSV по ячейкам")
    args = ap.parse_args()

    poly = json.load(open(args.poly))
    lats = [p[0] for p in poly]
    lons = [p[1] for p in poly]
    lat_c = sum(lats) / len(lats)
    # запас bbox для попаданий: максимальный радиус закраски
    pad = max(FP_R_MAX_M, FP_CENTER_R_M) + args.cell
    pad_la = pad / M_PER_DEG_LAT
    pad_lo = pad / m_per_deg_lon(lat_c)
    bbox = (min(lats) - pad_la, max(lats) + pad_la,
            min(lons) - pad_lo, max(lons) + pad_lo)

    dem = Dem()
    videos = sorted(p.with_suffix("").with_suffix("")  # <имя>.MP4.gps.tsv -> <имя>.MP4
                    for p in DATA.rglob("*.MP4.gps.tsv"))
    all_hits = []
    per_video = []
    for v in videos:
        hits = video_footprint_hits(v, dem, args.step, bbox)
        if hits:
            per_video.append((v.name, len(hits),
                              sum(1 for h in hits if not math.isnan(h[2]))))
        all_hits.extend(hits)

    # сетка ячеек внутри полигона
    d_la = args.cell / M_PER_DEG_LAT
    d_lo = args.cell / m_per_deg_lon(lat_c)
    cells = []
    la = min(lats) + d_la / 2
    while la < max(lats):
        lo = min(lons) + d_lo / 2
        while lo < max(lons):
            if point_in_poly(la, lo, poly):
                cells.append((la, lo))
            lo += d_lo
        la += d_la

    h = np.array([(la, lo) for la, lo, _, _, _ in all_hits]) if all_hits else np.zeros((0, 2))
    o8 = np.array([x[2] for x in all_hits]) if all_hits else np.zeros(0)
    paint_r = np.array([x[4] for x in all_hits]) if all_hits else np.zeros(0)
    results = []
    for la, lo in cells:
        if len(h):
            dist = np.hypot((h[:, 0] - la) * M_PER_DEG_LAT,
                            (h[:, 1] - lo) * m_per_deg_lon(la))
            near = dist <= paint_r
            looked = bool(near.any())
            seen = bool((near & (o8 <= THRESH_OBJ_CM)).any())
            best_o8 = float(np.nanmin(o8[near])) if looked and not np.all(
                np.isnan(o8[near])) else math.nan
            min_d = float(dist.min())
        else:
            looked = seen = False
            best_o8 = math.nan
            min_d = math.inf
        alt = dem.elev(la, lo)
        results.append(dict(lat=la, lon=lo, alt=alt, looked=looked, seen=seen,
                            best_o8=best_o8, min_d=min_d))

    n = len(results)
    n_seen = sum(r["seen"] for r in results)
    n_lookonly = sum(r["looked"] and not r["seen"] for r in results)
    n_blind = n - n_seen - n_lookonly
    alt_blind = [r["alt"] for r in results if not r["looked"]]
    print(f"ячеек {args.cell:.0f} м внутри полигона: {n}")
    print(f"  осмотрено с масштабом (предмет ≤{THRESH_OBJ_CM} см различим): "
          f"{n_seen} ({n_seen / n:.0%})")
    print(f"  центр кадра ложился, но масштаб неизвестен/хуже: "
          f"{n_lookonly} ({n_lookonly / n:.0%})")
    print(f"  ни одно попадание кадра не легло в ячейку: "
          f"{n_blind} ({n_blind / n:.0%})")
    if alt_blind:
        print(f"  неосмотренные ячейки по высоте: {min(alt_blind):.0f}–{max(alt_blind):.0f} м")
    print("\nролики с попаданиями в полигон (всего / с измеренным масштабом):")
    for name, k, k_gsd in sorted(per_video, key=lambda x: -x[1]):
        print(f"  {name}: {k} / {k_gsd}")

    if args.out_cells:
        with open(args.out_cells, "w", encoding="utf-8") as f:
            f.write("lat\tlon\talt_m\tlooked\tseen\tbest_obj8_cm\tmin_dist_m\n")
            for r in results:
                o8_txt = "" if math.isnan(r["best_o8"]) else f"{r['best_o8']:.0f}"
                f.write(f"{r['lat']:.6f}\t{r['lon']:.6f}\t{r['alt']:.0f}\t"
                        f"{int(r['looked'])}\t{int(r['seen'])}\t{o8_txt}\t"
                        f"{r['min_d']:.0f}\n")
        print(f"\nячейки: {args.out_cells}")


if __name__ == "__main__":
    main()
