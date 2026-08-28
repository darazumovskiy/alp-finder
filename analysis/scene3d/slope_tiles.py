#!/usr/bin/env python3
"""Плитки склона: готовые плотные облака -> покрытие горы для «Полёт 3D».

Замена гигазон вставками-плитками (ход 2 доводки, 21.08):
  - плитка TILE_M x TILE_M метров, на плитку ровно ОДИН источник — зона с
    максимумом точек в ней (смесь зон даёт двойные поверхности из-за
    разницы привязок);
  - своя плоскость PCA и свой шаг сетки/текселя по фактической плотности
    точек — бюджет не фиксирован, разрешение не теряется;
  - чистка локальная (kNN-фильтр внутри плитки, а не глобальная медиана
    зоны — та ампутировала настоящую разреженную периферию);
  - вертикальная посадка медианой на рельеф движка per-плитка;
  - неокрашенные кадрами текселы — чисто чёрные: шейдер вставок их
    отбрасывает, дыра честная (никакой заливки ближайшим цветом);
  - зоны со сломанной геометрией/привязкой (linia-05/11/12) и koshki-1608
    (стоит в ~50 м от сцены) в источники не входят.

Выход: analysis/viewer/ortho/tiles3d/t_<ix>_<iy>.json/.jpg (формат
mesh-вставки + tile=1: вьюер рисует без фартуков и продавливания).

Запуск: analysis/.venv/bin/python analysis/scene3d/slope_tiles.py
        (пересборка всех плиток; --only ix,iy — одна плитка для отладки)
"""
from __future__ import annotations

import base64
import json
import math
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
import pycolmap

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE / "src"))
sys.path.insert(0, str(ROOT / "analysis"))

import drape  # noqa: E402
from geoproject import Dem  # noqa: E402
from zone_insert import (LazyImages, Node, apply_sim3, despike_surface,  # noqa: E402
                         engine_ground, read_fused)

DATA = HERE / "data"
OUT_DIR = ROOT / "analysis/viewer/ortho/tiles3d"

SOURCES = ["veshchi-15", "linia-01", "linia-06", "linia-07", "linia-08",
           "linia-09", "linia-10", "linia-13", "gora-obzor", "stena-peak"]

M_LAT = 111132.0
TILE_M = 120.0            # сторона плитки
MARGIN_M = 6.0            # запас точек вокруг плитки для опоры сетки у края
LAT_S, LAT_N = 39.460, 39.510
LON_W, LON_E = 73.572, 73.606
MIN_PTS = 1500            # меньше — не поверхность, плитка не строится
MAX_TILE_PTS = 400_000    # сабсемпл ради времени, на тексель не влияет
MAX_TEX_W = 2048
MAX_FRAMES_TILE = 36


def m_lon(lat: float) -> float:
    return 111320.0 * math.cos(math.radians(lat))


def load_zone_cloud(zone: str):
    """Облако зоны в её локальном ENU + геоданные. None, если файлов нет."""
    fused = DATA / "_fused" / zone / "fused.ply"
    if not fused.exists():
        fused = DATA / zone / "dense-result/fused.ply"
    geo_p = DATA / zone / "georef-dense.json"
    if not fused.exists() or not geo_p.exists():
        return None
    geo = json.loads(geo_p.read_text())
    T = np.asarray(geo["T"], float).reshape(4, 4)
    xyz, rgb = read_fused(fused)
    enu = apply_sim3(T, xyz)
    lo, hi = np.percentile(enu, [1, 99], axis=0)
    pad = (hi - lo) * 0.1 + 2.0
    core = np.all((enu > lo - pad) & (enu < hi + pad), axis=1)
    return dict(zone=zone, enu=enu[core], rgb=rgb[core], anchor=geo["anchor"],
                rms=geo.get("rms_m"))


def tile_index(lat: np.ndarray, lon: np.ndarray):
    ix = np.floor((lon - LON_W) * m_lon((LAT_S + LAT_N) / 2) / TILE_M).astype(int)
    iy = np.floor((lat - LAT_S) * M_LAT / TILE_M).astype(int)
    return ix, iy


def drop_outliers_local(pts: np.ndarray) -> tuple[np.ndarray, float]:
    """kNN-маска с порогом от МЕСТНОЙ медианы (не ампутирует разреженное)
    + фактический шаг точек: медиана d8 у равномерного поля ~= 1.6*шаг —
    честнее средней плотности по рамке, где точки лежат полосой."""
    from scipy.spatial import cKDTree
    if len(pts) < 32:
        return np.ones(len(pts), bool), 1.0
    d, _ = cKDTree(pts).query(pts, k=9)
    d8 = d[:, 8]
    return d8 < 3.0 * float(np.median(d8)), float(np.median(d8)) / 1.6


def scene_footprints() -> list[dict]:
    """Пятна детальных сцен-вставок (сцена кошек, модель C C, будущие
    сцены): плитка под ними гасится — грубая уступает точной, слои не
    накладываются."""
    out = []
    for p in sorted((ROOT / "analysis/viewer/ortho").glob("insert_*.json")):
        if p.name.startswith("insert_z-"):
            continue
        d = json.loads(p.read_text())
        if d.get("weak"):
            continue
        if d.get("mesh"):
            rows, cols = d["rows"], d["cols"]
            v = np.frombuffer(base64.b64decode(d["verts_b64"]),
                              np.float32).reshape(rows, cols, 3)
            sup = np.frombuffer(base64.b64decode(d["support_b64"]),
                                np.uint8).reshape(rows, cols)
            meas = sup < 255
            if not meas.any():
                continue
            ml = m_lon(d["origin_lat"])
            lat = d["origin_lat"] + v[:, :, 1][meas] / M_LAT
            lon = d["origin_lon"] + v[:, :, 0][meas] / ml
            sp = float(np.hypot(*(v[0, 1, :2] - v[0, 0, :2])))
        else:
            n = d["n"]
            lat = (d["lat0"] + np.arange(n) * d["dlat"])
            lon = (d["lon0"] + np.arange(n) * d["dlon"])
            gy, gx = np.meshgrid(lat, lon, indexing="ij")
            lat, lon = gy.ravel(), gx.ravel()
            sp = d["dlat"] * M_LAT
        out.append(dict(lat=lat, lon=lon, spacing=max(sp, 0.3)))
    return out


def cut_under_scenes(surface, scenes: list, a_lat: float, a_lon: float) -> None:
    """Гасит ячейки плитки в пятне детальной сцены-вставки. Радиус выреза
    не уже шага самой плитки (+ запас на кромку): иначе при ячейке плитки
    крупнее шага сцены вырез получается шахматкой, и грубая плитка
    нависает НАД детальной сценой."""
    from scipy.spatial import cKDTree
    q = surface.cell_drawn
    if not q.any() or not scenes:
        return
    rows, cols = surface.rows, surface.cols
    ve = surface.vertices[:, 0].reshape(rows, cols)
    vn = surface.vertices[:, 1].reshape(rows, cols)
    ce = 0.25 * (ve[:-1, :-1] + ve[:-1, 1:] + ve[1:, :-1] + ve[1:, 1:])
    cn = 0.25 * (vn[:-1, :-1] + vn[:-1, 1:] + vn[1:, :-1] + vn[1:, 1:])
    mla = m_lon(a_lat)
    cx = ((a_lon + ce / mla) * mla).ravel()
    cy = ((a_lat + cn / M_LAT) * M_LAT).ravel()
    hit = np.zeros(cx.shape, bool)
    for s in scenes:
        sxy = np.stack([s["lat"] * M_LAT, s["lon"] * mla], axis=1)
        r = max(s["spacing"] * 1.5, surface.posting_m * 1.6)
        dist, _ = cKDTree(sxy).query(np.stack([cy, cx], axis=1),
                                     distance_upper_bound=r)
        hit |= np.isfinite(dist)
    q &= ~hit.reshape(q.shape)


def keep_big_parts(surface) -> None:
    from scipy.ndimage import label
    q = surface.cell_drawn
    lab, n = label(q)
    if n == 0:
        return
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    keep = np.where(sizes >= max(20, int(sizes.max() * 0.02)))[0]
    q &= np.isin(lab, keep)


def tile_cameras(nodes, centre_enu: np.ndarray):
    """Камеры зоны, реально видящие центр плитки; грубые первыми, cap."""
    picked = []
    for gsd, iid, name, node in nodes:
        px, _, front = drape.project_points(node, centre_enu[None])
        if not front[0]:
            continue
        x, y = px[0]
        if not (-0.2 * node.width < x < 1.2 * node.width
                and -0.2 * node.height < y < 1.2 * node.height):
            continue
        d = float(np.linalg.norm(node.position - centre_enu))
        picked.append((d / node.f_px, iid, name, node))
    picked.sort(key=lambda t: -t[0])
    if len(picked) > MAX_FRAMES_TILE:
        idx = np.linspace(0, len(picked) - 1, MAX_FRAMES_TILE).round().astype(int)
        picked = [picked[i] for i in sorted(set(idx.tolist()))]
    return picked


def zone_nodes(zone: str):
    """Все камеры зоны в её ENU (как zone_insert.cameras_enu, но без cap)."""
    geo = json.loads((DATA / zone / "georef-dense.json").read_text())
    T = np.asarray(geo["T"], float).reshape(4, 4)
    s = float(np.cbrt(np.linalg.det(T[:3, :3])))
    R = T[:3, :3] / s
    rec = pycolmap.Reconstruction(str(DATA / zone / "dense/sparse"))
    nodes = []
    for img in rec.images.values():
        cam = rec.cameras[img.camera_id]
        R_cw = np.asarray(img.cam_from_world().rotation.matrix())
        pos = apply_sim3(T, np.asarray(img.projection_center())[None])[0]
        node = Node(position=pos, right=R @ R_cw[0], up=-(R @ R_cw[1]),
                    forward=R @ R_cw[2], f_px=float(cam.params[0]),
                    width=int(cam.width), height=int(cam.height))
        nodes.append((0.0, img.image_id, img.name, node))
    return nodes


class CachedImages:
    """images.get(id) с LRU-кешем: одна зона красит десятки плиток."""

    def __init__(self, folder: Path, names: dict, cap: int = 90):
        self.lazy = LazyImages(folder, names)
        self.cache: dict = {}
        self.order: list = []
        self.cap = cap

    def get(self, image_id):
        if image_id in self.cache:
            return self.cache[image_id]
        im = self.lazy.get(image_id)
        self.cache[image_id] = im
        self.order.append(image_id)
        if len(self.order) > self.cap:
            self.cache.pop(self.order.pop(0), None)
        return im


def build_tile(dem: Dem, ix: int, iy: int, zc: dict, nodes, images,
               scenes: list) -> dict | None:
    a_lat, a_lon, a_alt = zc["anchor"]
    mlon_a = m_lon(a_lat)
    mlon_g = m_lon((LAT_S + LAT_N) / 2)
    # границы плитки -> зона-локальный ENU (метры от якоря зоны)
    lat0 = LAT_S + iy * TILE_M / M_LAT
    lon0 = LON_W + ix * TILE_M / mlon_g
    lat1 = LAT_S + (iy + 1) * TILE_M / M_LAT
    lon1 = LON_W + (ix + 1) * TILE_M / mlon_g
    e0, e1 = (lon0 - a_lon) * mlon_a, (lon1 - a_lon) * mlon_a
    n0, n1 = (lat0 - a_lat) * M_LAT, (lat1 - a_lat) * M_LAT

    enu = zc["enu"]
    box = ((enu[:, 0] > e0 - MARGIN_M) & (enu[:, 0] < e1 + MARGIN_M)
           & (enu[:, 1] > n0 - MARGIN_M) & (enu[:, 1] < n1 + MARGIN_M))
    pts, rgbp = enu[box], zc["rgb"][box]
    if len(pts) < MIN_PTS:
        return None
    if len(pts) > MAX_TILE_PTS:
        step = len(pts) // MAX_TILE_PTS
        pts, rgbp = pts[::step], rgbp[::step]
    keep, spacing = drop_outliers_local(pts)
    pts, rgbp = pts[keep], rgbp[keep]
    if len(pts) < MIN_PTS:
        return None
    spacing = float(np.clip(spacing, 0.05, 3.0))

    # вертикальная посадка: медиана к рельефу движка по точкам плитки
    sub = pts[:: max(1, len(pts) // 8000)]
    lat = a_lat + sub[:, 1] / M_LAT
    lon = a_lon + sub[:, 0] / mlon_a
    dz = float(np.median(engine_ground(dem, lat, lon) - (a_alt + sub[:, 2])))

    frame = drape.fit_surface_frame(pts, margin_m=1.0)
    # низ 0.5 м: держит сетку плитки <=~240 в стороне (вес JSON), мельче
    # рельеф всё равно несёт текстура
    posting = float(np.clip(spacing * 2.0, 0.5, 3.0))
    texel = float(np.clip(spacing * 0.8, 0.02, 0.5))
    span_u = frame.u_max - frame.u_min
    width = min(MAX_TEX_W, max(64, int(span_u / texel)))
    rect = drape.texture_rect(frame, width)
    max_support = float(np.clip(spacing * 4.0, 1.5, 8.0))
    surface = drape.heightfield(pts, frame, rect, posting_m=posting,
                                max_support_m=max_support)
    despike_surface(surface, frame)
    keep_big_parts(surface)

    # рисуем только ячейки внутри квадрата плитки (запас был для опоры)
    rows, cols = surface.rows, surface.cols
    ve = surface.vertices[:, 0].reshape(rows, cols)
    vn = surface.vertices[:, 1].reshape(rows, cols)
    ce = 0.25 * (ve[:-1, :-1] + ve[:-1, 1:] + ve[1:, :-1] + ve[1:, 1:])
    cn = 0.25 * (vn[:-1, :-1] + vn[:-1, 1:] + vn[1:, :-1] + vn[1:, 1:])
    surface.cell_drawn &= (ce >= e0) & (ce < e1) & (cn >= n0) & (cn < n1)
    cut_under_scenes(surface, scenes, a_lat, a_lon)
    if surface.cell_drawn.sum() < 12:
        return None

    centre = pts.mean(axis=0)
    cams = tile_cameras(nodes, centre)
    ortho = drape.orthophoto(surface, frame, pts,
                             [(iid, node) for _, iid, _, node in cams], images)
    rgb = ortho.rgb.copy()
    painted = ortho.provenance > 0
    rgb[painted] = np.maximum(rgb[painted], 3)
    # тексель без кадра берёт цвет ближайшей ТОЧКИ ОБЛАКА (измеренный цвет,
    # не выдумка); дальше 2 шагов точек — честная дыра: чисто чёрное,
    # шейдер вставок его отбрасывает
    unp = ~painted
    if unp.any():
        from scipy.spatial import cKDTree
        uu, vv = rect.texel_centres()
        plane = frame.to_plane(pts)
        qi = np.nonzero(unp.ravel())[0]
        dist, idx = cKDTree(plane[:, :2]).query(
            np.column_stack([uu.ravel()[qi], vv.ravel()[qi]]),
            distance_upper_bound=max(2.0 * spacing, 2.0 * texel))
        hit = np.isfinite(dist)
        flat = rgb.reshape(-1, 3)
        flat[qi[hit]] = np.maximum(rgbp[idx[hit]], 3)
        flat[qi[~hit]] = 0
        rgb = flat.reshape(rgb.shape)

    q = surface.cell_drawn
    vert_ok = np.zeros((rows, cols), bool)
    vert_ok[:-1, :-1] |= q
    vert_ok[:-1, 1:] |= q
    vert_ok[1:, :-1] |= q
    vert_ok[1:, 1:] |= q
    sup = np.where(vert_ok.ravel(), np.clip(surface.support_m * 10, 0, 254),
                   255).astype(np.uint8)
    verts = surface.vertices.astype(np.float32)
    verts[:, 2] += a_alt + dz

    name = f"t_{ix}_{iy}"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(OUT_DIR / f"{name}.jpg"), np.flipud(rgb)[:, :, ::-1],
                [cv2.IMWRITE_JPEG_QUALITY, 85])
    meta = dict(
        name=name, mesh=1, trim=1, tile=1,
        src=zc["zone"], rms_m=zc["rms"],
        origin_lat=a_lat, origin_lon=a_lon,
        dz_applied_m=round(dz, 2),
        texel_cm=round(rect.metres_per_texel * 100, 1),
        posting_m=round(posting, 2),
        covered=round(ortho.covered_fraction, 3),
        rows=rows, cols=cols, alt0=a_alt + dz,
        plane=dict(o=[round(float(v), 4) for v in frame.origin],
                   u=[round(float(v), 6) for v in frame.u],
                   v=[round(float(v), 6) for v in frame.v],
                   u0=round(rect.u0, 3), u1=round(rect.u1, 3),
                   v0=round(rect.v0, 3), v1=round(rect.v1, 3)),
        verts_b64=base64.b64encode(verts.tobytes()).decode(),
        support_b64=base64.b64encode(sup.tobytes()).decode(),
        tex=f"ortho/tiles3d/{name}.jpg")
    (OUT_DIR / f"{name}.json").write_text(json.dumps(meta, separators=(",", ":")))
    return dict(cells=int(q.sum()), texel=rect.metres_per_texel,
                covered=ortho.covered_fraction, dz=dz)


def main():
    only = None
    if "--only" in sys.argv:
        only = tuple(int(v) for v in
                     sys.argv[sys.argv.index("--only") + 1].split(","))
    dem = Dem()

    # проход 1: счёт точек по (плитка, зона) — источник выбирается по максимуму
    counts: dict = {}
    zones = []
    for zone in SOURCES:
        zc = load_zone_cloud(zone)
        if zc is None:
            print(f"{zone}: нет облака/привязки — пропуск источника")
            continue
        a_lat, a_lon, _ = zc["anchor"]
        lat = a_lat + zc["enu"][:, 1] / M_LAT
        lon = a_lon + zc["enu"][:, 0] / m_lon(a_lat)
        inside = (lat > LAT_S) & (lat < LAT_N) & (lon > LON_W) & (lon < LON_E)
        ix, iy = tile_index(lat[inside], lon[inside])
        for (x, y), n in zip(*np.unique(np.stack([ix, iy], 1), axis=0,
                                        return_counts=True)):
            counts.setdefault((int(x), int(y)), {})[zone] = int(n)
        zones.append(zc)
        print(f"{zone}: {len(zc['enu']):,} точек, RMS {zc['rms']} м")
    by_zone: dict = {}
    for key, per in counts.items():
        best = max(per, key=per.get)
        if per[best] >= MIN_PTS:
            by_zone.setdefault(best, []).append(key)

    if only:
        by_zone = {z: [k for k in ks if k == only] for z, ks in by_zone.items()}

    if OUT_DIR.exists() and not only:
        shutil.rmtree(OUT_DIR)

    total = 0
    scenes = scene_footprints()
    zmap = {zc["zone"]: zc for zc in zones}
    for zone, keys in sorted(by_zone.items()):
        if not keys:
            continue
        zc = zmap[zone]
        nodes = zone_nodes(zone)
        images = CachedImages(DATA / zone / "dense/images",
                              {iid: nm for _, iid, nm, _ in nodes})
        done = 0
        for ix, iy in sorted(keys):
            try:
                r = build_tile(dem, ix, iy, zc, nodes, images, scenes)
            except Exception as e:  # одна плитка не роняет прогон
                print(f"  {zone} t_{ix}_{iy}: ОШИБКА {type(e).__name__}: {e}",
                      flush=True)
                continue
            if r:
                done += 1
                print(f"  t_{ix}_{iy} <- {zone}: ячеек {r['cells']}, "
                      f"тексель {r['texel'] * 100:.0f} см, окрашено "
                      f"{r['covered']:.0%}, якорь {r['dz']:+.1f} м", flush=True)
        total += done
        print(f"{zone}: {done} плиток из {len(keys)} кандидатов", flush=True)
    print(f"итого плиток: {total}")


if __name__ == "__main__":
    main()
