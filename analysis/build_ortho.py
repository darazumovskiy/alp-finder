#!/usr/bin/env python3
"""Ортомозаика по кадрам дрона: пирамида веб-меркаторных тайлов из всех роликов.

Каждый кадр (шаг FRAME_STEP с) орторектифицируется в рельеф: сетка узлов по
кадру, из позы камеры (телеметрия *.MP4.gps.tsv + фокусное coverage tsv)
векторный марш лучей до DEM (HMA 8 м + DSM-патчи, как весь конвейер),
кусочно-проективная укладка в тайлы 512 px схемы XYZ (веб-меркатор).
Разрешение адаптивное: уровень зума кадра выбирается по его GSD, поэтому
там, где дрон снимал близко/зумом (GSD 1-3 см/пикс), мозаика хранит сантиметровую
деталь, а дальние обзорные кадры ложатся только в грубые уровни.

На пиксель тайла остаётся лучший источник: score = резкость / GSD² × синус
угла луча к склону; пологие скользящие лучи (грубая ошибка привязки) и
слишком дальние кадры отбрасываются целиком.

Выход:
  analysis/viewer/ortho/{gz}/{sx}_{sy}.webp  — «суперблоки» BLOCK×BLOCK тайлов
      (4096 px, BGRA, A=0 — не снято): у Cloudflare Pages лимит 20 тыс. файлов
      на проект, поштучные 512-тайлы (десятки тысяч) в него не влезают, а в
      блоках 8×8 их в ~64 раза меньше. Сетка gz: тайл 512 px покрывает квадрат
      стандартного 256-тайла зума gz (эффективное разрешение — зум gz+1);
      блок (sx, sy) накрывает тайлы tx в [sx*8, sx*8+8), ty аналогично.
  analysis/viewer/ortho/base.webp            — базовый ковёр всего района;
  analysis/viewer/ortho/meta.json            — границы, зумы, статистика;
  analysis/ortho-work/tiles/                 — рабочие 512-тайлы (источник
      блоков), score-сайдкары укладки и done.json (идемпотентность).

Запуск (python из analysis/.venv, PYTHONPATH=analysis):
    build_ortho.py ВИДЕО...        # пути или имена из data/drive
    build_ortho.py --all           # все ролики с телеметрией
    build_ortho.py --finalize      # только пересборка пирамиды и меты
Пирамида (уровни ниже максимального и заполнение дыр грубыми уровнями)
пересобирается в конце каждого прогона.
"""
import argparse
import json
import math
import sys
from collections import OrderedDict
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geoproject import Dem, _bilinear, at, load_rows  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/drive"
COV_DIR = ROOT / "analysis/coverage"
OUT_DIR = ROOT / "analysis/viewer/ortho"
WORK_DIR = ROOT / "analysis/ortho-work"
TILES_DIR = WORK_DIR / "tiles"
BLOCK = 8             # тайлов в стороне суперблока (8×512 = 4096 px)
# верхние зумы — 3/4 всех блоков: там блок крупнее, чтобы влезать в лимит
# 20 тыс. файлов Cloudflare Pages вместе с кадрами плеера
BLOCK_BY_GZ = {21: 16, 22: 16, 23: 16}

W, H = 1920, 1080     # система координат кадра и фокусного (coverage tsv)
FRAME_STEP = 5.0      # шаг кадров, с (как в кеше «Полёта»)
NODE_PX = 16          # шаг сетки узлов по кадру, px
TILE = 512            # размер тайла, px
GZ_MIN, GZ_MAX = 13, 23   # сетка тайлов: эффективные зумы 14..24 (~1.5 м..0.7 см/пикс)
RAY_STEP_M = 5.0
RAY_MAX_M = 6000.0
GSD_MAX = 2.5         # м/пикс исходного кадра; хуже — кадр не кладём вовсе
DIST_MAX = 5000.0     # м; дальше привязка бессмысленна
GRAZE_MIN = 0.15      # синус угла луча к склону; ниже — скользящий луч, увод десятки м
EDGE_MAX_PX = 900.0   # квад растянуло в тайловых пикселях сильнее — шов заслона, пропуск
CACHE_TILES = 1200    # тайлов в памяти до сброса на диск
# рамка интереса (кратно шире района операции; за ней тайлы не строим)
LAT_S, LAT_N = 39.42, 39.56
LON_W, LON_E = 73.50, 73.70

MERC_R = 6378137.0


# --- веб-меркатор --------------------------------------------------------------


def merc_px(lat, lon, gz):
    """Глобальные пиксельные координаты (x, y) в сетке gz (тайл TILE px)."""
    s = TILE * (1 << gz)
    x = (lon + 180.0) / 360.0 * s
    y = (1.0 - np.arcsinh(np.tan(np.radians(lat))) / math.pi) / 2.0 * s
    return x, y


def merc_res(lat, gz):
    """Метров на пиксель мозаики в сетке gz на широте lat."""
    return 2 * math.pi * MERC_R * math.cos(math.radians(lat)) / (TILE * (1 << gz))


# --- векторный рельеф и марш лучей ---------------------------------------------


def elev_vec(dem: Dem, lat, lon):
    """Высота рельефа для массивов координат; вне тайла DEM — NaN."""
    v = _bilinear(dem.z, (lon - dem.lon0) / dem.dlon, (dem.lat0 - lat) / dem.dlat)
    if dem._patch is not None:
        off, plon0, plat0, pdlon, pdlat = dem._patch
        pv = _bilinear(off, (lon - plon0) / pdlon, (plat0 - lat) / pdlat)
        v = v + np.where(np.isfinite(pv), pv, 0.0)
    return v


def cast_vec(dem: Dem, lat0, lon0, alt0, dirs):
    """Марш пучка лучей до рельефа.

    dirs (N,3) ENU → (lat, lon, dist) массивы, NaN — луч не встретил рельеф
    (небо, за рамкой DEM, дальше RAY_MAX_M). Логика повторяет geoproject.cast:
    подземный старт у крутого склона пропускается до выхода луча над поверхность.
    """
    n = len(dirs)
    m_lat = 111132.0
    m_lon = 111320.0 * math.cos(math.radians(lat0))
    de, dn, du = dirs[:, 0], dirs[:, 1], dirs[:, 2]
    zmax = float(np.nanmax(dem.z)) + 100.0

    g0 = elev_vec(dem, np.array([lat0]), np.array([lon0]))[0]
    above = np.full(n, bool(np.isfinite(g0) and alt0 > g0))
    active = np.ones(n, bool)
    lo_d = np.zeros(n)          # последняя дистанция «над рельефом»
    hi_d = np.full(n, np.nan)   # первая дистанция «под рельефом»

    d = RAY_STEP_M
    while d <= RAY_MAX_M and active.any():
        ai = np.nonzero(active)[0]
        la = lat0 + dn[ai] * d / m_lat
        lo = lon0 + de[ai] * d / m_lon
        al = alt0 + du[ai] * d
        g = elev_vec(dem, la, lo)
        oob = ~np.isfinite(g)
        under = ~oob & (al <= g)
        over = ~oob & ~under
        hit = under & above[ai]
        hi_d[ai[hit]] = d
        above[ai[over]] = True
        lo_d[ai[over]] = d
        # луч ушёл в небо над самой высокой точкой рельефа — уже не пересечёт
        sky = over & (al > zmax) & (du[ai] >= 0)
        active[ai[oob | hit | sky]] = False
        d += RAY_STEP_M

    ok = np.isfinite(hi_d)
    lat_h = np.full(n, np.nan)
    lon_h = np.full(n, np.nan)
    dist = np.full(n, np.nan)
    if ok.any():
        oi = np.nonzero(ok)[0]
        a, b = lo_d[oi].copy(), hi_d[oi].copy()
        for _ in range(22):     # бисекция до ~1 мкм·2²² ≈ сантиметры
            mid = (a + b) / 2
            la = lat0 + dn[oi] * mid / m_lat
            lo = lon0 + de[oi] * mid / m_lon
            al = alt0 + du[oi] * mid
            g = elev_vec(dem, la, lo)
            under = np.isfinite(g) & (al <= g)
            b = np.where(under, mid, b)
            a = np.where(under, a, mid)
        dist[oi] = b
        lat_h[oi] = lat0 + dn[oi] * b / m_lat
        lon_h[oi] = lon0 + de[oi] * b / m_lon
    return lat_h, lon_h, dist


def graze_sin(dem: Dem, lat, lon, dirs):
    """Синус угла между лучом и плоскостью склона в точках падения."""
    eps = 8.0
    m_lat = 111132.0
    m_lon = 111320.0 * np.cos(np.radians(lat))
    dzdx = (elev_vec(dem, lat, lon + eps / m_lon) -
            elev_vec(dem, lat, lon - eps / m_lon)) / (2 * eps)
    dzdy = (elev_vec(dem, lat + eps / m_lat, lon) -
            elev_vec(dem, lat - eps / m_lat, lon)) / (2 * eps)
    nx, ny, nz = -dzdx, -dzdy, np.ones_like(dzdx)
    nn = np.sqrt(nx * nx + ny * ny + nz * nz)
    dot = (dirs[:, 0] * nx + dirs[:, 1] * ny + dirs[:, 2] * nz) / nn
    return -dot  # луч идёт в склон → dot отрицательный


# --- телеметрия и фокусное ------------------------------------------------------


def load_cov(video: Path):
    """[(t, f_px)] из coverage tsv (фокусное в системе 1920)."""
    cov = COV_DIR / (video.name + ".coverage.tsv")
    if not cov.exists():
        return []
    rows = []
    for line in cov.read_text().splitlines()[1:]:
        p = line.split("\t")
        if len(p) >= 6 and p[5] == "ok" and p[1]:
            try:
                rows.append((float(p[0]), float(p[1])))
            except ValueError:
                continue
    return rows


def focal_at(cov, t, max_dt=6.0):
    best = None
    for ct, f in cov:
        dt = abs(ct - t)
        if dt <= max_dt and (best is None or dt < best[0]):
            best = (dt, f)
    return best[1] if best else None


# --- хранилище тайлов -----------------------------------------------------------


class TileStore:
    """Тайлы BGRA + score с LRU-кешем и докладкой поверх готовых на диске."""

    def __init__(self):
        self.cache = OrderedDict()   # (gz,tx,ty) -> [bgr u8, score f32, dirty]
        self.touched = set()

    def _paths(self, key):
        gz, tx, ty = key
        return (TILES_DIR / str(gz) / str(tx) / f"{ty}.webp",
                WORK_DIR / str(gz) / f"{tx}_{ty}.npy")

    def get(self, key):
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        tp, sp = self._paths(key)
        if tp.exists() and sp.exists():
            img = cv2.imread(str(tp), cv2.IMREAD_UNCHANGED)
            score = np.load(sp).astype(np.float32) ** 2
            bgr = img[:, :, :3].copy() if img is not None else np.zeros((TILE, TILE, 3), np.uint8)
            if img is None or score.shape != (TILE, TILE):
                bgr, score = np.zeros((TILE, TILE, 3), np.uint8), np.zeros((TILE, TILE), np.float32)
        else:
            bgr = np.zeros((TILE, TILE, 3), np.uint8)
            score = np.zeros((TILE, TILE), np.float32)
        ent = [bgr, score, False]
        self.cache[key] = ent
        self.touched.add(key)
        if len(self.cache) > CACHE_TILES:
            old, ent_old = self.cache.popitem(last=False)
            self._write(old, ent_old)
        return ent

    def _write(self, key, ent):
        bgr, score, dirty = ent
        if not dirty:
            return
        tp, sp = self._paths(key)
        tp.parent.mkdir(parents=True, exist_ok=True)
        sp.parent.mkdir(parents=True, exist_ok=True)
        a = ((score > 0) * 255).astype(np.uint8)
        cv2.imwrite(str(tp), np.dstack([bgr, a]), [cv2.IMWRITE_WEBP_QUALITY, 82])
        # диапазон score ~1e-5..1e6 не влезает в float16 — храним корень
        np.save(sp, np.sqrt(score).astype(np.float16))

    def flush(self):
        for key, ent in self.cache.items():
            self._write(key, ent)
            ent[2] = False


# --- укладка кадра --------------------------------------------------------------


def frame_sharpness(img):
    g = cv2.cvtColor(cv2.resize(img, (640, 360)), cv2.COLOR_BGR2GRAY)
    v = cv2.Laplacian(g, cv2.CV_32F).var()
    return float(np.clip(v / 200.0, 0.25, 2.0))


def composite_frame(dem, store, img, lat0, lon0, alt0, yaw, pitch, f_px):
    """Кадр → тайлы. Возвращает (уложено квадов, gz) либо (0, None)."""
    us = np.arange(0, W + 1, NODE_PX, dtype=np.float64)
    vs = np.arange(0, H + 1, NODE_PX, dtype=np.float64)
    uu, vv = np.meshgrid(us, vs)                      # (nv, nu)
    nv, nu = uu.shape

    ax = np.arctan2(uu.ravel() - W / 2, f_px)
    ay = np.arctan2(vv.ravel() - H / 2, f_px)
    yw = np.radians(yaw) + ax
    pt = np.radians(pitch) - ay
    ce = np.cos(pt)
    dirs = np.stack([np.sin(yw) * ce, np.cos(yw) * ce, np.sin(pt)], axis=1)

    lat, lon, dist = cast_vec(dem, lat0, lon0, alt0, dirs)
    gsd = dist / f_px                                  # м на пиксель исходника
    ok = (np.isfinite(dist) & (dist <= DIST_MAX) & (gsd <= GSD_MAX)
          & (lat >= LAT_S) & (lat <= LAT_N) & (lon >= LON_W) & (lon <= LON_E))
    if ok.sum() < 4:
        return 0, None
    gs = np.full(len(dirs), np.nan)
    gs[ok] = graze_sin(dem, lat[ok], lon[ok], dirs[ok])
    ok &= np.nan_to_num(gs) >= GRAZE_MIN
    if ok.sum() < 4:
        return 0, None

    # зум кадра — по медианному GSD пригодных узлов
    gsd_med = float(np.median(gsd[ok]))
    lat_med = float(np.median(lat[ok]))
    gz = int(round(math.log2(2 * math.pi * MERC_R * math.cos(math.radians(lat_med))
                             / (TILE * gsd_med))))
    gz = max(GZ_MIN, min(GZ_MAX, gz))

    sharp = frame_sharpness(img)
    score = np.where(ok, sharp / np.maximum(gsd, 1e-6) ** 2 * gs, 0.0)

    mx, my = merc_px(lat, lon, gz)
    okg = ok.reshape(nv, nu)
    mxg, myg = mx.reshape(nv, nu), my.reshape(nv, nu)
    scg = score.reshape(nv, nu)

    src = cv2.resize(img, (W, H)) if img.shape[1] != W else img
    laid = 0
    for j in range(nv - 1):
        for i in range(nu - 1):
            if not (okg[j, i] and okg[j, i + 1] and okg[j + 1, i + 1] and okg[j + 1, i]):
                continue
            # float64 обязательно: глобальные пиксели меркатора ~3e9, float32
            # квантует их с шагом сотни пикселей; в float32 — только локальные
            dst = np.array([[mxg[j, i], myg[j, i]], [mxg[j, i + 1], myg[j, i + 1]],
                            [mxg[j + 1, i + 1], myg[j + 1, i + 1]],
                            [mxg[j + 1, i], myg[j + 1, i]]], np.float64)
            e = np.linalg.norm(np.roll(dst, -1, 0) - dst, axis=1)
            if e.max() > EDGE_MAX_PX or e.max() < 1e-3:
                continue   # растянуло через заслон либо вырождение
            qs = float(scg[j:j + 2, i:i + 2].mean())
            sq = np.array([[uu[j, i], vv[j, i]], [uu[j, i + 1], vv[j, i]],
                           [uu[j, i + 1], vv[j + 1, i]], [uu[j, i], vv[j + 1, i]]],
                          np.float32)
            x0, y0 = int(np.floor(dst[:, 0].min())), int(np.floor(dst[:, 1].min()))
            x1, y1 = int(np.ceil(dst[:, 0].max())), int(np.ceil(dst[:, 1].max()))
            for tx in range(x0 // TILE, x1 // TILE + 1):
                for ty in range(y0 // TILE, y1 // TILE + 1):
                    ox, oy = tx * TILE, ty * TILE
                    bx0, by0 = max(x0 - ox, 0), max(y0 - oy, 0)
                    bx1, by1 = min(x1 - ox, TILE), min(y1 - oy, TILE)
                    if bx0 >= bx1 or by0 >= by1:
                        continue
                    local = dst - [ox + bx0, oy + by0]
                    hm = cv2.getPerspectiveTransform(sq, local.astype(np.float32))
                    patch = cv2.warpPerspective(
                        src, hm, (bx1 - bx0, by1 - by0), flags=cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_REPLICATE)
                    mask = np.zeros((by1 - by0, bx1 - bx0), np.uint8)
                    cv2.fillPoly(mask, [np.round(local).astype(np.int32)], 255)
                    ent = store.get((gz, tx, ty))
                    sub_s = ent[1][by0:by1, bx0:bx1]
                    put = (mask > 0) & (qs > sub_s)
                    if put.any():
                        ent[0][by0:by1, bx0:bx1][put] = patch[put]
                        sub_s[put] = qs
                        ent[2] = True
            laid += 1
    return laid, gz


# --- обработка ролика -----------------------------------------------------------


def process(dem, store, video: Path, done: dict, force: bool):
    if video.name in done and not force:
        print(f"{video.stem}: уже уложен, пропуск")
        return
    rows = load_rows(video)
    cov = load_cov(video)
    if not cov:
        print(f"{video.stem}: нет фокусного (coverage tsv) — пропуск", file=sys.stderr)
        return
    cap = cv2.VideoCapture(str(video))
    dur = cap.get(cv2.CAP_PROP_FRAME_COUNT) / max(cap.get(cv2.CAP_PROP_FPS), 1)
    n_frames = n_laid = 0
    zooms = {}
    t = 0.0
    while t < dur:
        pose = [at(rows, t, k) for k in ("lat", "lon", "alt_m", "gb_yaw", "gb_pitch")]
        f_px = focal_at(cov, t)
        if all(math.isfinite(v) for v in pose) and f_px:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            got, img = cap.read()
            if got:
                laid, gz = composite_frame(dem, store, img, *pose, f_px)
                n_frames += 1
                if laid:
                    n_laid += 1
                    zooms[gz] = zooms.get(gz, 0) + 1
        t += FRAME_STEP
    cap.release()
    done[video.name] = dict(frames=n_frames, laid=n_laid, zooms=zooms)
    (WORK_DIR / "done.json").write_text(json.dumps(done, indent=1))
    zs = " ".join(f"z{z}:{n}" for z, n in sorted(zooms.items()))
    print(f"{video.stem}: кадров {n_frames}, уложено {n_laid} ({zs})")


# --- пирамида и мета ------------------------------------------------------------


def _read_bgra(path):
    """Тайл как BGRA: webp без прозрачных пикселей открывается 3-канальным."""
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None or img.ndim != 3:
        return None
    if img.shape[2] == 3:
        img = np.dstack([img, np.full(img.shape[:2], 255, np.uint8)])
    return img


def list_tiles(gz):
    d = TILES_DIR / str(gz)
    if not d.is_dir():
        return []
    return [(gz, int(p.parent.name), int(p.stem))
            for p in d.glob("*/*.webp")]


def build_pyramid():
    """Уровни ниже: грубый тайл = его натив + даунсэмпл детей поверх."""
    top = max((gz for gz in range(GZ_MIN, GZ_MAX + 1) if list_tiles(gz)), default=None)
    if top is None:
        return
    for gz in range(top - 1, GZ_MIN - 2, -1):
        children = list_tiles(gz + 1)
        parents = {}
        for _, tx, ty in children:
            parents.setdefault((tx // 2, ty // 2), []).append((tx, ty))
        for (px, py), chs in sorted(parents.items()):
            tp = TILES_DIR / str(gz) / str(px) / f"{py}.webp"
            img = _read_bgra(tp) if tp.exists() else None
            if img is None:
                img = np.zeros((TILE, TILE, 4), np.uint8)
            for tx, ty in chs:
                ch = _read_bgra(TILES_DIR / str(gz + 1) / str(tx) / f"{ty}.webp")
                if ch is None:
                    continue
                small = cv2.resize(ch, (TILE // 2, TILE // 2), interpolation=cv2.INTER_AREA)
                qx, qy = (tx % 2) * (TILE // 2), (ty % 2) * (TILE // 2)
                sub = img[qy:qy + TILE // 2, qx:qx + TILE // 2]
                m = small[:, :, 3] > 64
                sub[m] = small[m]
            tp.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(tp), img, [cv2.IMWRITE_WEBP_QUALITY, 82])


def _tile_ll(gz, tx, ty):
    """(lat, lon) северо-западного угла тайла."""
    s = TILE * (1 << gz)
    lon = tx * TILE / s * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty * TILE / s))))
    return lat, lon


def build_base():
    """Базовый ковёр: все тайлы сетки BASE_GZ одним webp (дальний вид в 3D)."""
    gz = 15                                   # ~1.8 м/пикс
    tiles = list_tiles(gz)
    if not tiles:
        return None
    xs = [t[1] for t in tiles]
    ys = [t[2] for t in tiles]
    x0, x1 = min(xs), max(xs) + 1
    y0, y1 = min(ys), max(ys) + 1
    wpx, hpx = (x1 - x0) * TILE, (y1 - y0) * TILE
    img = np.zeros((hpx, wpx, 4), np.uint8)
    for _, tx, ty in tiles:
        t = _read_bgra(TILES_DIR / str(gz) / str(tx) / f"{ty}.webp")
        if t is None:
            continue
        img[(ty - y0) * TILE:(ty - y0 + 1) * TILE,
            (tx - x0) * TILE:(tx - x0 + 1) * TILE] = t
    while max(img.shape[:2]) > 6144:          # потолок GPU-текстуры дальнего вида
        img = cv2.resize(img, (img.shape[1] // 2, img.shape[0] // 2),
                         interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(OUT_DIR / "base.webp"), img, [cv2.IMWRITE_WEBP_QUALITY, 80])
    lat_n, lon_w = _tile_ll(gz, x0, y0)
    lat_s, lon_e = _tile_ll(gz, x1, y1)
    return dict(gz=gz, w=img.shape[1], h=img.shape[0],
                lat_n=round(lat_n, 6), lon_w=round(lon_w, 6),
                lat_s=round(lat_s, 6), lon_e=round(lon_e, 6))


def pack_blocks():
    """Рабочие 512-тайлы → публикуемые суперблоки BLOCK×BLOCK (лимит файлов Pages)."""
    import shutil
    n_blocks = 0
    for gz in range(GZ_MIN - 1, GZ_MAX + 1):
        blk = BLOCK_BY_GZ.get(gz, BLOCK)
        out_z = OUT_DIR / str(gz)
        if out_z.exists():
            shutil.rmtree(out_z)
        tiles = list_tiles(gz)
        if not tiles:
            continue
        blocks = {}
        for _, tx, ty in tiles:
            blocks.setdefault((tx // blk, ty // blk), []).append((tx, ty))
        for (sx, sy), members in sorted(blocks.items()):
            img = np.zeros((TILE * blk, TILE * blk, 4), np.uint8)
            for tx, ty in members:
                t = _read_bgra(TILES_DIR / str(gz) / str(tx) / f"{ty}.webp")
                if t is None:
                    continue
                ox, oy = (tx - sx * blk) * TILE, (ty - sy * blk) * TILE
                img[oy:oy + TILE, ox:ox + TILE] = t
            if not (img[:, :, 3] > 0).any():
                continue
            out_z.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(out_z / f"{sx}_{sy}.webp"), img,
                        [cv2.IMWRITE_WEBP_QUALITY, 82])
            n_blocks += 1
    return n_blocks


def write_meta():
    zs = [gz for gz in range(GZ_MIN - 1, GZ_MAX + 1) if list_tiles(gz)]
    if not zs:
        return
    n_tiles = 0
    b = None
    for gz in zs:
        tiles = list_tiles(gz)
        n_tiles += len(tiles)
        if gz == zs[0]:
            s = TILE * (1 << gz)
            xs = [t[1] for t in tiles]
            ys = [t[2] for t in tiles]
            lon_w = min(xs) * TILE / s * 360 - 180
            lon_e = (max(xs) + 1) * TILE / s * 360 - 180
            lat_n = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * min(ys) * TILE / s))))
            lat_s = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (max(ys) + 1) * TILE / s))))
            b = [round(lat_s, 5), round(lon_w, 5), round(lat_n, 5), round(lon_e, 5)]
    n_blocks = pack_blocks()
    meta = dict(tile=TILE, block=BLOCK,
                blockz={str(gz): n for gz, n in BLOCK_BY_GZ.items()},
                minz=zs[0], maxz=zs[-1], bounds=b,
                tiles=n_tiles, blocks=n_blocks, base=build_base())
    (OUT_DIR / "meta.json").write_text(json.dumps(meta))
    print(f"пирамида: зумы {zs[0]}..{zs[-1]}, тайлов {n_tiles} "
          f"в {n_blocks} блоках, рамка {b}")


def resolve(token: str):
    p = Path(token)
    if p.exists():
        return p
    hits = sorted(DATA.rglob(token if token.endswith(".MP4") else token + ".MP4"))
    if not hits:
        raise SystemExit(f"видео не найдено: {token}")
    return hits[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("videos", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--finalize", action="store_true",
                    help="только пирамида и мета, без укладки")
    ap.add_argument("--pack", action="store_true",
                    help="только блоки и мета (пирамида уже собрана)")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    done_path = WORK_DIR / "done.json"
    done = json.loads(done_path.read_text()) if done_path.exists() else {}

    if not args.finalize and not args.pack:
        if args.all:
            videos = sorted(p.with_suffix("").with_suffix("")
                            for p in DATA.rglob("*.MP4.gps.tsv"))
            videos = [v for v in videos if v.exists()]
        else:
            videos = [resolve(t) for t in args.videos]
        if not videos:
            raise SystemExit("нечего обрабатывать (ролики или --all)")
        dem = Dem()
        store = TileStore()
        for v in videos:
            try:
                process(dem, store, v, done, args.force)
            except Exception as e:  # один битый ролик не валит прогон
                print(f"{v.stem}: ОШИБКА {e}", file=sys.stderr)
        store.flush()

    if not args.pack:
        build_pyramid()
    write_meta()


if __name__ == "__main__":
    main()
