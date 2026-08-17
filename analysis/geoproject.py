#!/usr/bin/env python3
"""Геопроекция взгляда камеры: фокусное фотограмметрией, дистанция до склона, масштаб.

Три инструмента (слой «геоиндекс» конвейера, docs/video-analysis.md п. 1):

  focal ВИДЕО T0 T1   — фокусное в пикселях на интервале панорамирования [T0, T1]:
                        оптический сдвиг между соседними сэмплами (phaseCorrelate),
                        делённый на поворот подвеса из телеметрии за тот же интервал.
                        Дрон должен висеть (GPS не меняется), иначе сдвиг не чисто
                        вращательный. Печатает медиану и разброс по парам кадров.
  cast ВИДЕО T PX PY F — трассировка луча: из позиции дрона в момент T через пиксель
                        (PX, PY) при фокусном F (пикс.) до пересечения с рельефом
                        Copernicus GLO-30 (data/dem/N39E073.tif). Печатает координаты
                        точки, дистанцию и метры-на-пиксель (GSD). Есть рельеф
                        NASA HMA 8 м (см. HMA_PATH ниже) — им пользуется 3D-вьюер.
  elev LAT LON        — высота рельефа в точке (проверка DEM).

Семантика углов телеметрии (gb_yaw: 0=север, по часовой; gb_pitch: минус=вниз)
подтверждена валидацией на рюкзаке: cast по видео DJI_20260813184253 в момент,
когда рюкзак виден в кадре, попадает в его триангулированные штабом координаты
(см. docs/nakhodki/README.md). Высоты DEM — эллипсоидальные поправки не вносим:
против MSL телеметрии расхождение единицы метров, для масштаба несущественно.
"""

import argparse
import csv
import math
from pathlib import Path

import cv2
import numpy as np
import tifffile

ROOT = Path(__file__).resolve().parents[1]
GLO_PATH = ROOT / "data/dem/N39E073.tif"
HMA_PATH = ROOT / "data/dem/hma8m_kurumdy.tif"   # analysis/build_hma_dem.py
# Расчётный конвейер с 17.08 живёт на HMA 8 м (решение оператора после сверки
# по независимым эталонам — analysis/review/dem-benchmark.md: грубые промахи
# рельефа втрое-вчетверо реже, чем у GLO-30). Смещения точек при миграции —
# analysis/review/dem-migration.md. За рамкой HMA elev() падает на GLO-30.
DEM_PATH = HMA_PATH if HMA_PATH.exists() else GLO_PATH
PATCH_DIR = ROOT / "analysis/dem-patches"   # DSM-патчи фотограмметрии (см. README там)
PATCH_FEATHER_M = 15.0                      # затухание поправки к краю охвата патчей
FLOW_W = 960          # ширина центрального окна для phaseCorrelate
RAY_STEP_M = 5.0      # шаг марша луча
RAY_MAX_M = 6000.0
TELEM_GAP_MAX_S = 1.0   # провал телеметрии длиннее — интерполяции не доверяем


# --- DEM ---------------------------------------------------------------------


def _read_geotiff(path):
    """(массив float32, lon0, lat0, dlon, dlat) — привязка верхнего левого угла."""
    tif = tifffile.TiffFile(path)
    page = tif.pages[0]
    z = page.asarray().astype(np.float32)
    scale = page.tags["ModelPixelScaleTag"].value      # (sx, sy, sz)
    tie = page.tags["ModelTiepointTag"].value          # (i, j, k, x, y, z)
    return z, tie[3], tie[4], scale[0], scale[1]


def _bilinear(z, x, y):
    """Векторная билинейная выборка z[y, x]; за границей и на NaN-соседях → NaN."""
    x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
    ok = (x >= 0) & (x < z.shape[1] - 1) & (y >= 0) & (y < z.shape[0] - 1)
    xs, ys = np.where(ok, x, 0), np.where(ok, y, 0)
    x0, y0 = xs.astype(int), ys.astype(int)
    fx, fy = xs - x0, ys - y0
    v = (z[y0, x0] * (1 - fx) * (1 - fy) + z[y0, x0 + 1] * fx * (1 - fy)
         + z[y0 + 1, x0] * (1 - fx) * fy + z[y0 + 1, x0 + 1] * fx * fy)
    return np.where(ok, v, np.nan)


class Dem:
    """Рельеф: базовый DEM (HMA 8 м либо GLO-30) + поправки DSM-патчей.

    Патчи (analysis/dem-patches/*.tif) — плотная реконструкция по кадрам дрона
    в системе высот телеметрии; базовый DEM в пятне вещей врёт до ~48 м. Вместо
    прямой подмены высот накладывается сглаженная разница «DSM − GLO-30»:
    дыры реконструкции заполняются ближайшей измеренной поправкой, к краям
    охвата поправка затухает до нуля — рельеф остаётся непрерывным и марш
    луча (cast) не спотыкается об обрывы на границе патча.
    """

    def __init__(self, path=DEM_PATH, patch_dir=PATCH_DIR):
        self.z, self.lon0, self.lat0, self.dlon, self.dlat = _read_geotiff(path)
        self.h, self.w = self.z.shape
        self._patch = self._load_patches(patch_dir) if patch_dir else None
        # рамка HMA уже района GLO-30: за её пределами elev() берёт GLO-30
        self._outside = (Dem(GLO_PATH, patch_dir=None)
                         if Path(path) != GLO_PATH and GLO_PATH.exists() else None)

    def _load_patches(self, patch_dir):
        paths = sorted(Path(patch_dir).glob("*.tif"))
        if not paths:
            return None
        patches = [_read_geotiff(p) for p in paths]
        # общая сетка: объединённый bbox, шаг — как у патчей (~1 м)
        dlon = min(p[3] for p in patches)
        dlat = min(p[4] for p in patches)
        lon_w = min(p[1] for p in patches)
        lon_e = max(p[1] + p[0].shape[1] * p[3] for p in patches)
        lat_n = max(p[2] for p in patches)
        lat_s = min(p[2] - p[0].shape[0] * p[4] for p in patches)
        gw = int(round((lon_e - lon_w) / dlon)) + 1
        gh = int(round((lat_n - lat_s) / dlat)) + 1
        lons = lon_w + np.arange(gw) * dlon
        lats = lat_n - np.arange(gh) * dlat
        glon, glat = np.meshgrid(lons, lats)

        # среднее DSM по патчам (независимые реконструкции, расходятся ~1-2 м)
        acc = np.zeros((gh, gw), np.float64)
        cnt = np.zeros((gh, gw), np.int32)
        for z, plon0, plat0, pdlon, pdlat in patches:
            v = _bilinear(z, (glon - plon0) / pdlon, (plat0 - glat) / pdlat)
            m = np.isfinite(v)
            acc[m] += v[m]
            cnt[m] += 1
        dsm = np.where(cnt > 0, acc / np.maximum(cnt, 1), np.nan)

        base = _bilinear(self.z, (glon - self.lon0) / self.dlon,
                         (self.lat0 - glat) / self.dlat)
        off = (dsm - base).astype(np.float32)
        known = np.isfinite(off)
        if not known.any():
            return None

        # дыры реконструкции → поправка ближайшего измеренного узла + сглаживание
        ky, kx = np.nonzero(known)
        uy, ux = np.nonzero(~known)
        if len(uy):
            step = 2000  # порциями, чтобы не строить матрицу все×все
            for s in range(0, len(uy), step):
                sy, sx = uy[s:s + step], ux[s:s + step]
                d2 = (sy[:, None] - ky[None, :]) ** 2 + (sx[:, None] - kx[None, :]) ** 2
                nearest = np.argmin(d2, axis=1)
                off[sy, sx] = off[ky[nearest], kx[nearest]]
        off = cv2.GaussianBlur(off, (0, 0), sigmaX=2)

        # затухание к краям bbox: ноль на границе → непрерывный стык с GLO-30
        m_per_deg_lat = 111132.0
        m_per_deg_lon = 111320.0 * math.cos(math.radians((lat_n + lat_s) / 2))
        ex = np.minimum(np.arange(gw), np.arange(gw)[::-1]) * dlon * m_per_deg_lon
        ey = np.minimum(np.arange(gh), np.arange(gh)[::-1]) * dlat * m_per_deg_lat
        wgt = np.minimum(np.minimum.outer(ey, ex) / PATCH_FEATHER_M, 1.0)
        off *= wgt.astype(np.float32)
        return off, lon_w, lat_n, dlon, dlat

    def elev(self, lat, lon):
        """Билинейная высота рельефа, м (с поправкой патчей, где они есть)."""
        x = (lon - self.lon0) / self.dlon
        y = (self.lat0 - lat) / self.dlat
        if not (0 <= x < self.w - 1 and 0 <= y < self.h - 1):
            if self._outside is not None:
                return self._outside.elev(lat, lon)
            raise ValueError(f"точка вне тайла DEM: {lat}, {lon}")
        x0, y0 = int(x), int(y)
        fx, fy = x - x0, y - y0
        z = self.z
        base = float(z[y0, x0] * (1 - fx) * (1 - fy) + z[y0, x0 + 1] * fx * (1 - fy)
                     + z[y0 + 1, x0] * (1 - fx) * fy + z[y0 + 1, x0 + 1] * fx * fy)
        if self._patch is not None:
            off, plon0, plat0, pdlon, pdlat = self._patch
            v = _bilinear(off, np.float64((lon - plon0) / pdlon),
                          np.float64((plat0 - lat) / pdlat))
            if np.isfinite(v):
                base += float(v)
        return base


# --- телеметрия ---------------------------------------------------------------


def load_rows(video: Path):
    side = video.with_suffix(video.suffix + ".gps.tsv")
    with side.open(encoding="utf-8") as f:
        rows = []
        for r in csv.DictReader(f, delimiter="\t"):
            try:
                rows.append({k: float(v) for k, v in r.items() if v != ""})
            except ValueError:
                continue
    return rows


def unwrap_deg(values):
    """Развёртка углов (град.) через скачки ±360 по измеренным отсчётам.

    Пропуск в телеметрии для np.unwrap — не пропуск, а NaN, который съедает
    накопленную поправку и обнуляет все углы до конца ролика. Здесь пропуски
    остаются пропусками, а развёртка идёт по тому, что измерено.
    """
    v = np.asarray(values, dtype=float)
    out = np.full(v.shape, np.nan)
    ok = np.isfinite(v)
    if ok.any():
        out[ok] = np.degrees(np.unwrap(np.radians(v[ok])))
    return out


def interp_gap(t, ts, vs, gap_max_s=TELEM_GAP_MAX_S):
    """Интерполяция vs(ts) в момент t; внутри провала телеметрии — nan.

    Строки идут через 0.03 с, поэтому провал в секунду — это реальная потеря
    пакетов, а не разрежённая запись. Через такой провал подвес успевает
    повернуться на десятки градусов, и интерполяция рисует направление
    взгляда, которого не было; для карты покрытия «не знаем» безопаснее
    выдуманного «осмотрено».
    """
    ts = np.asarray(ts, dtype=float)
    vs = np.asarray(vs, dtype=float)
    ok = np.isfinite(ts) & np.isfinite(vs)
    if not ok.any():
        return math.nan
    tk, vk = ts[ok], vs[ok]
    i = int(np.searchsorted(tk, t))
    if 0 < i < len(tk) and tk[i] - tk[i - 1] > gap_max_s:
        return math.nan
    return float(np.interp(t, tk, vk))


def at(rows, t, key):
    """Линейная интерполяция поля key в момент t (yaw разворачивается от скачков ±360).

    Строки без этого поля (пакеты заголовка, потеря части полей) пропускаются;
    провал длиннее TELEM_GAP_MAX_S даёт nan, а не додуманное значение.
    """
    rows = [r for r in rows if key in r and "time_s" in r]
    ts = [r["time_s"] for r in rows]
    vs = [r[key] for r in rows]
    if key.endswith("yaw"):
        vs = unwrap_deg(vs)
    return interp_gap(t, ts, vs)


# --- фокусное фотограмметрией --------------------------------------------------


def grab(cap, t):
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, img = cap.read()
    if not ok:
        raise ValueError(f"кадр t={t:.2f} не читается")
    return img


def gray_center(img):
    h, w = img.shape[:2]
    scale = w / FLOW_W
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (FLOW_W, int(h / scale)))
    return g.astype(np.float32), scale


MIN_ROT_RAD = math.radians(0.8)   # поворот подвеса, ниже которого делить не на что


def pair_focal(sample, shift_px, a, b, min_rot_rad=0.0):
    """(фокусное в пикс., причина) по паре кадров [a, b]; при отказе фокусное None.

    sample(t, поле) — телеметрия в момент t; shift_px — сдвиг картинки (dx, dy)
    в пикселях полного кадра. Причины отказа: gap — провал телеметрии,
    no_rot — подвес почти не повернулся, делить не на что. Порог поворота
    задаёт вызывающий: у сплошного прохода по видео он свой, у пакетного
    расчёта пары уже отобраны по величине поворота.

    Метод считает весь сдвиг кадра следствием поворота подвеса. На зависании
    так и есть; в полёте к сдвигу добавляется параллакс, и оценка врёт —
    пределы метода и замер этого вклада в docs/focal-length-calibration.md.
    """
    yaw_a, yaw_b = sample(a, "gb_yaw"), sample(b, "gb_yaw")
    pitch_a, pitch_b = sample(a, "gb_pitch"), sample(b, "gb_pitch")
    if not all(math.isfinite(v) for v in (yaw_a, yaw_b, pitch_a, pitch_b)):
        return None, "gap"
    pitch = (pitch_a + pitch_b) / 2
    dyaw = math.radians(yaw_b - yaw_a)
    dpitch = math.radians(pitch_b - pitch_a)
    dx, dy = shift_px
    # рыскание двигает картинку по горизонтали (в проекции на горизонт кадра —
    # cos(pitch)), тангаж по вертикали; берём ось, где поворот заметнее
    if abs(dyaw) > min_rot_rad and abs(dyaw) > 2 * abs(dpitch):
        f = -dx / (dyaw * math.cos(math.radians(pitch)))
    elif abs(dpitch) > min_rot_rad and abs(dpitch) > 2 * abs(dyaw):
        f = dy / dpitch
    else:
        return None, "no_rot"
    return (f, "ok") if f > 0 else (None, "no_rot")


def focal_px(video: Path, t0: float, t1: float, dt: float = 0.25):
    """(оценки фокусного в пикс., счётчик причин отказа) на интервале [t0, t1]."""
    rows = load_rows(video)
    cap = cv2.VideoCapture(str(video))
    ests, why = [], {}
    t = t0
    while t + dt <= t1:
        a, b = t, t + dt
        try:
            ga, sa = gray_center(grab(cap, a))
            gb, _ = gray_center(grab(cap, b))
        except ValueError:
            break
        (dx, dy), _resp = cv2.phaseCorrelate(ga, gb)
        f, reason = pair_focal(lambda t, k: at(rows, t, k), (dx * sa, dy * sa), a, b,
                               min_rot_rad=MIN_ROT_RAD)
        if f is None:
            why[reason] = why.get(reason, 0) + 1
        else:
            ests.append(f)
        t += dt
    cap.release()
    return ests, why


# --- трассировка луча ----------------------------------------------------------


def ray_dir(yaw_deg, pitch_deg, px, py, w, h, f):
    """Единичный вектор ENU луча через пиксель (px, py); yaw 0=север по часовой,
    pitch минус=вниз; (0,0) — левый верхний угол кадра."""
    # углы отклонения от оптической оси
    ax = math.atan2(px - w / 2, f)          # вправо +
    ay = math.atan2(py - h / 2, f)          # вниз +
    yaw = math.radians(yaw_deg) + ax
    pitch = math.radians(pitch_deg) - ay
    ce = math.cos(pitch)
    return (math.sin(yaw) * ce, math.cos(yaw) * ce, math.sin(pitch))  # (E, N, Up)


def cast(dem: Dem, lat, lon, alt, direction):
    """Марш луча до рельефа → (lat, lon, alt_рельефа, дистанция) или None.

    Если стартовая точка «под» рельефом (дрон вплотную к крутому склону: 30-метровая
    сетка DEM сглаживает склон выше позиции дрона, плюс ошибка высоты GPS), начальный
    подземный участок пропускается, и ищется первое пересечение после выхода луча
    над поверхность.
    """
    de, dn, du = direction
    m_per_deg_lat = 111132.0
    m_per_deg_lon = 111320.0 * math.cos(math.radians(lat))
    # старт над поверхностью считается «уже над» — иначе пересечение ближе
    # первого шага марша (крутой луч у склона) пропускается как подземный участок
    try:
        above = alt > dem.elev(lat, lon)
    except ValueError:
        above = False
    prev = 0.0
    d = RAY_STEP_M
    while d <= RAY_MAX_M:
        la = lat + dn * d / m_per_deg_lat
        lo = lon + de * d / m_per_deg_lon
        al = alt + du * d
        try:
            ground = dem.elev(la, lo)
        except ValueError:
            return None
        if al > ground:
            above = True
        elif above:
            lo_d, hi_d = prev, d     # уточнение бисекцией между prev и d
            for _ in range(20):
                mid = (lo_d + hi_d) / 2
                la = lat + dn * mid / m_per_deg_lat
                lo = lon + de * mid / m_per_deg_lon
                al = alt + du * mid
                if al <= dem.elev(la, lo):
                    hi_d = mid
                else:
                    lo_d = mid
            la = lat + dn * hi_d / m_per_deg_lat
            lo = lon + de * hi_d / m_per_deg_lon
            return la, lo, dem.elev(la, lo), hi_d
        prev = d
        d += RAY_STEP_M
    return None


class _ShiftDem:
    """DEM со сдвигом высоты — для вилки чувствительности cast к ошибке DEM."""

    def __init__(self, base, dz):
        self.base, self.dz = base, dz

    def elev(self, lat, lon):
        return self.base.elev(lat, lon) + self.dz


def cmd_cast(args):
    dem = Dem()
    video = Path(args.video)
    rows = load_rows(video)
    lat, lon = at(rows, args.t, "lat"), at(rows, args.t, "lon")
    alt = at(rows, args.t, "alt_m")
    yaw, pitch = at(rows, args.t, "gb_yaw"), at(rows, args.t, "gb_pitch")
    cap = cv2.VideoCapture(str(video))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
    cap.release()
    print(f"дрон: {lat:.6f}, {lon:.6f}, {alt:.0f} м; подвес yaw {yaw:.1f} pitch {pitch:.1f}")
    direction = ray_dir(yaw, pitch, args.px, args.py, w, h, args.f)
    hit = cast(dem, lat, lon, alt, direction)
    if hit is None:
        print("луч не пересёк рельеф (смотрит выше горизонта или вне тайла)")
        return
    la, lo, ground, dist = hit
    print(f"точка: {la:.6f}, {lo:.6f}, рельеф {ground:.0f} м, дистанция {dist:.0f} м")
    print(f"GSD: {dist / args.f * 100:.1f} см/пикс (объект 30 пикс ≈ {dist / args.f * 30:.2f} м)")
    # Обязательная вилка: ошибка высоты DEM на этой стене достигает 58 м
    # (docs/nezavisimyy-analiz/06-…), у пологих к склону лучей она уводит точку
    # на десятки-сотни метров (analysis/review/geoprojection.md, корректировка 14.08).
    drift_max = 0.0
    for dz in (+30, -30):
        h2 = cast(_ShiftDem(dem, dz), lat, lon, alt, direction)
        if h2 is None:
            print(f"вилка DEM{dz:+d} м: нет пересечения")
            drift_max = float("inf")
            continue
        la2, lo2, _, dist2 = h2
        drift = math.hypot((la2 - la) * 111132.0,
                           (lo2 - lo) * 111320.0 * math.cos(math.radians(la)))
        drift_max = max(drift_max, drift)
        print(f"вилка DEM{dz:+d} м: точка {la2:.6f}, {lo2:.6f}, дистанция {dist2:.0f} м, "
              f"увод {drift:.0f} м")
    if drift_max > 30:
        print("ВНИМАНИЕ: координата ненадёжна (луч идёт полого к склону) — "
              "подтвердить параллаксом/подлётом или дальномером")


WHY_FOCAL = {"gap": "провал телеметрии", "no_rot": "подвес почти не повернулся"}


def cmd_focal(args):
    ests, why = focal_px(Path(args.video), args.t0, args.t1)
    if why:
        print("отброшено пар: "
              + ", ".join(f"{WHY_FOCAL[k]} {n}" for k, n in sorted(why.items())))
    if not ests:
        print("годных пар нет — выберите участок панорамирования на зависании")
        return
    med = float(np.median(ests))
    print(f"оценок: {len(ests)}, медиана f = {med:.0f} пикс, "
          f"квартили {np.percentile(ests, 25):.0f}..{np.percentile(ests, 75):.0f}")


def cmd_elev(args):
    print(f"{Dem().elev(args.lat, args.lon):.1f} м")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("focal")
    p.add_argument("video")
    p.add_argument("t0", type=float)
    p.add_argument("t1", type=float)
    p.set_defaults(fn=cmd_focal)
    p = sub.add_parser("cast")
    p.add_argument("video")
    p.add_argument("t", type=float)
    p.add_argument("px", type=float)
    p.add_argument("py", type=float)
    p.add_argument("f", type=float)
    p.set_defaults(fn=cmd_cast)
    p = sub.add_parser("elev")
    p.add_argument("lat", type=float)
    p.add_argument("lon", type=float)
    p.set_defaults(fn=cmd_elev)
    args = ap.parse_args()
    args.fn(args)
