#!/usr/bin/env python3
"""Плотное облако зоны -> mesh-вставка «Полёт 3D».

Для каждой зоны с готовым fused.ply (PatchMatch+fusion, см. README):
  1. привязка scene->ENU из georef-dense.json (georef_scene.py по телеметрии);
  2. вертикальный якорь: медианное совмещение облака с рельефом движка
     (Dem + DSM-патчи) — маркеры реестра сидят на рельефе движка,
     вставка обязана сидеть там же;
  3. плоскость по облаку, сетка рельефа и ортофото из самих кадров зоны
     (dense/images + позы dense/sparse) — src/drape.py;
  4. упаковка в insert_z-<зона>.json/.jpg формата veshchi3d
     (analysis/scene3d_insert.py): вершины (e, n, alt_движка) float32 base64,
     support uint8 (255 = не измерено, вершина садится на рельеф движка);
  5. crop_overlaps: грубая вставка уступает точной — вырез в support под
     пятном более детальной показываемой вставки (после каждой пересборки).

Запуск: analysis/.venv/bin/python analysis/scene3d/zone_insert.py [зона ...]
        (без аргументов — все зоны; готовые вставки пропускаются, --force;
         --crop — только вырезы по готовым JSON; --regrade — только грейд)
"""
import base64
import json
import math
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import cv2
import numpy as np
import pycolmap

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE / "src"))
sys.path.insert(0, str(ROOT / "analysis"))

import drape  # noqa: E402
from geoproject import Dem, _bilinear  # noqa: E402

DATA = HERE / "data"
OUT_DIR = ROOT / "analysis/viewer/ortho"

ZONES = ["koshki-1608", "veshchi-15", "stena-peak", "gora-obzor",
         "linia-01", "linia-05", "linia-06", "linia-07", "linia-08",
         "linia-09", "linia-10", "linia-11", "linia-12", "linia-13"]
TITLES = {"koshki-1608": "кошки 16.08", "veshchi-15": "вещи",
          "stena-peak": "стена у пика", "gora-obzor": "гора обзорная"}

M_LAT = 111132.0
MAX_CLOUD = 1_500_000     # точек облака в подгонке/сетке — дальше только время
MAX_TEXELS = 6_000_000    # предел растра текстуры
MAX_FRAMES = 120          # кадров в запекании (равномерно по шкале GSD)
GRID_MAX = 480            # предел стороны сетки вершин
MIN_TEXEL_M = 0.01
MAX_TEXEL_M = 0.35        # крупнее — вставка не добавляет деталей к рельефу
MAX_EDGE_GAP_M = 15.0     # края висят выше — даже пологий фартук уже враньё
CROP_FACTOR = 2.0         # источник выреза детальнее цели минимум во столько
CROP_MAX_RMS_M = 15.0     # источник с привязкой грубее — резать другим нельзя


@dataclass(frozen=True)
class Node:
    position: np.ndarray
    right: np.ndarray
    up: np.ndarray
    forward: np.ndarray
    f_px: float
    width: int
    height: int


def read_fused(path: Path):
    """PLY фьюжна COLMAP: x y z nx ny nz float32 + rgb uchar, little endian."""
    raw = path.read_bytes()
    head_end = raw.index(b"end_header\n") + len(b"end_header\n")
    n = int(next(l for l in raw[:head_end].split(b"\n")
                 if l.startswith(b"element vertex")).split()[-1])
    dt = np.dtype([("xyz", "<f4", 3), ("nrm", "<f4", 3), ("rgb", "u1", 3)])
    v = np.frombuffer(raw, dt, count=n, offset=head_end)
    return v["xyz"].astype(np.float64), v["rgb"].copy()


def apply_sim3(T: np.ndarray, xyz: np.ndarray) -> np.ndarray:
    return xyz @ T[:3, :3].T + T[:3, 3]


def engine_ground(dem: Dem, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Высота рельефа движка: GLO-30 + поле поправок DSM-патчей."""
    z = _bilinear(dem.z, (lon - dem.lon0) / dem.dlon,
                  (dem.lat0 - lat) / dem.dlat)
    off, plon0, plat0, pdlon, pdlat = dem._patch
    p = _bilinear(off, (lon - plon0) / pdlon, (plat0 - lat) / pdlat)
    return np.where(np.isfinite(p), z + p, z)


def cameras_enu(rec: pycolmap.Reconstruction, T: np.ndarray, centroid):
    """Камеры зоны в ENU-осях привязки, грубые первыми (по GSD у центроида)."""
    s = float(np.cbrt(np.linalg.det(T[:3, :3])))
    R = T[:3, :3] / s
    nodes = []
    for img in rec.images.values():
        cam = rec.cameras[img.camera_id]
        fx = float(cam.params[0])
        R_cw = np.asarray(img.cam_from_world().rotation.matrix())
        pos = apply_sim3(T, np.asarray(img.projection_center())[None])[0]
        right, down, fwd = (R @ R_cw[0], R @ R_cw[1], R @ R_cw[2])
        node = Node(position=pos, right=right, up=-down, forward=fwd,
                    f_px=fx, width=int(cam.width), height=int(cam.height))
        gsd = float(np.linalg.norm(pos - centroid)) / fx
        nodes.append((gsd, img.image_id, img.name, node))
    nodes.sort(key=lambda t: -t[0])
    if len(nodes) > MAX_FRAMES:
        idx = np.linspace(0, len(nodes) - 1, MAX_FRAMES).round().astype(int)
        nodes = [nodes[i] for i in sorted(set(idx.tolist()))]
    return nodes


class LazyImages:
    """images.get(id) для drape.orthophoto — кадры читаются по одному."""

    def __init__(self, folder: Path, names: dict):
        self.folder, self.names = folder, names

    def get(self, image_id):
        p = self.folder / self.names[image_id]
        im = cv2.imread(str(p))
        return None if im is None else im[:, :, ::-1]


def drop_outliers(cloud: np.ndarray) -> np.ndarray:
    """Мусор PatchMatch (летающие башни, обрывки не на поверхности): точка
    живёт, только если её 8-й сосед ближе тройной медианы таких расстояний."""
    from scipy.spatial import cKDTree

    d, _ = cKDTree(cloud).query(cloud, k=9)
    d8 = d[:, 8]
    keep = d8 < 3.0 * float(np.median(d8))
    return cloud[keep]


def despike_surface(surface, frame) -> None:
    """Ячейки, чья высота прыгает от локальной медианы дальше 8 м, — не
    поверхность, а недобитый мусор: гасятся прямо в cell_drawn."""
    from scipy.ndimage import median_filter

    h = frame.to_plane(surface.vertices)[:, 2].reshape(surface.rows,
                                                       surface.cols)
    bad = np.abs(h - median_filter(h, size=7)) > 8.0
    q = surface.cell_drawn
    q &= ~(bad[:-1, :-1] | bad[:-1, 1:] | bad[1:, :-1] | bad[1:, 1:])


def keep_connected(surface) -> tuple[int, float, float]:
    """Обрывки поверхности в стороне от основного куска — не объект, а шум
    реконструкции: остаются только крупные связные компоненты. Возвращает
    (число компонент, доля крупнейшей, её площадь в м²)."""
    from scipy.ndimage import label

    q = surface.cell_drawn
    lab, n = label(q)
    if n == 0:
        return 0, 0.0, 0.0
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    big = int(sizes.max())
    keep = np.where(sizes >= max(30, big * 0.03))[0]
    q &= np.isin(lab, keep)
    cell_area = surface.posting_m ** 2
    return len(keep), big / max(int(sizes.sum()), 1), big * cell_area


def edge_gap_m(dem: Dem, meta: dict) -> float:
    """Насколько края вставки висят над рельефом движка ПОСЛЕ посадки
    (страница сглаживает разность окном 20% сетки — здесь то же поле):
    90-й перцентиль |остатка| по границе измеренной области."""
    from scipy.ndimage import binary_erosion, uniform_filter

    rows, cols = meta["rows"], meta["cols"]
    v = np.frombuffer(base64.b64decode(meta["verts_b64"]),
                      np.float32).reshape(rows, cols, 3)
    sup = np.frombuffer(base64.b64decode(meta["support_b64"]),
                        np.uint8).reshape(rows, cols)
    meas = sup < 255
    m_lon = 111320.0 * math.cos(math.radians(meta["origin_lat"]))
    lat = meta["origin_lat"] + v[:, :, 1] / M_LAT
    lon = meta["origin_lon"] + v[:, :, 0] / m_lon
    dh = np.where(meas, engine_ground(dem, lat.ravel(), lon.ravel())
                  .reshape(rows, cols) - v[:, :, 2], 0.0)
    size = max(17, 2 * int(max(rows, cols) * 0.2) + 1)
    num = uniform_filter(dh, size=size)
    den = uniform_filter(meas.astype(float), size=size)
    corr = np.where(den > 1e-6, num / np.maximum(den, 1e-6), 0.0)
    resid = np.abs(dh - corr)
    border = meas & ~binary_erosion(meas)
    if not border.any():
        return 0.0
    return float(np.percentile(resid[border], 90))


def grade(meta: dict, gap: float) -> dict:
    """Критерий показа: вставка обязана добавлять детали (тексель),
    быть цельной (главный кусок) и сшиваться с рельефом (края)."""
    meta["edge_gap_m"] = round(gap, 1)
    meta["weak"] = int(meta["texel_cm"] > MAX_TEXEL_M * 100
                       or meta["main_share"] < 0.6
                       or gap > MAX_EDGE_GAP_M)
    return meta


def fill_unpainted(rgb: np.ndarray, painted: np.ndarray) -> np.ndarray:
    """Неокрашенные текселы берут цвет ближайшего окрашенного: чёрных дыр
    в текстуре не остаётся (аналог inpaint в scene3d_insert.py)."""
    from scipy.ndimage import distance_transform_edt

    if painted.all() or not painted.any():
        return rgb
    _, (rr, cc) = distance_transform_edt(~painted, return_indices=True)
    return rgb[rr, cc]


def build_zone(dem: Dem, zone: str, force: bool) -> bool:
    out_json = OUT_DIR / f"insert_z-{zone}.json"
    if out_json.exists() and not force:
        print(f"{zone}: вставка уже есть, пропуск")
        return True
    fused = DATA / "_fused" / zone / "fused.ply"
    if not fused.exists():
        fused = DATA / zone / "dense-result/fused.ply"
    geo_p = DATA / zone / "georef-dense.json"
    if not fused.exists() or not geo_p.exists():
        print(f"{zone}: нет fused.ply или georef-dense.json — пропуск")
        return False

    geo = json.loads(geo_p.read_text())
    a_lat, a_lon, a_alt = geo["anchor"]
    T = np.asarray(geo["T"], float).reshape(4, 4)
    m_lon = 111320.0 * math.cos(math.radians(a_lat))

    xyz, _ = read_fused(fused)
    enu = apply_sim3(T, xyz)
    # обрезка выбросов PatchMatch: ядро облака по перцентилям с запасом
    lo, hi = np.percentile(enu, [1, 99], axis=0)
    pad = (hi - lo) * 0.1 + 2.0
    core = np.all((enu > lo - pad) & (enu < hi + pad), axis=1)
    enu = enu[core]
    step = max(1, len(enu) // MAX_CLOUD)
    cloud = drop_outliers(enu[::step])

    # вертикальный якорь к рельефу движка
    sub = cloud[::max(1, len(cloud) // 60_000)]
    lat = a_lat + sub[:, 1] / M_LAT
    lon = a_lon + sub[:, 0] / m_lon
    gz = engine_ground(dem, lat, lon)
    dz = float(np.median(gz - (a_alt + sub[:, 2])))
    alt0 = a_alt + dz

    frame = drape.fit_surface_frame(cloud)

    # черновой проход: где по сетке реально есть измеренная поверхность —
    # рамка обрезается до измеренного ядра, текстура не тратится на пустоту
    span_u = frame.u_max - frame.u_min
    span_v = frame.v_max - frame.v_min
    p0 = max(1.0, max(span_u, span_v) / 200)
    rect0 = drape.texture_rect(frame, 64)
    s0 = drape.heightfield(cloud, frame, rect0, posting_m=p0)
    despike_surface(s0, frame)
    rr, cc = np.nonzero(s0.cell_drawn)
    if len(rr) < 4:
        print(f"{zone}: измеренных ячеек нет — пропуск")
        return False
    u = np.linspace(rect0.u0, rect0.u1, s0.cols)
    v = np.linspace(rect0.v1, rect0.v0, s0.rows)
    frame = replace(frame,
                    u_min=float(u[cc.min()]) - p0, u_max=float(u[cc.max() + 1]) + p0,
                    v_min=float(v[rr.max() + 1]) - p0, v_max=float(v[rr.min()]) + p0)

    span_u = frame.u_max - frame.u_min
    span_v = frame.v_max - frame.v_min
    texel = max(MIN_TEXEL_M, math.sqrt(span_u * span_v / MAX_TEXELS))
    rect = drape.texture_rect(frame, int(span_u / texel))
    posting = max(0.25, max(span_u, span_v) / GRID_MAX)
    surface = drape.heightfield(cloud, frame, rect, posting_m=posting)
    despike_surface(surface, frame)
    n_parts, big_share, big_area = keep_connected(surface)
    if not surface.cell_drawn.any():
        print(f"{zone}: после чистки поверхности не осталось — пропуск")
        return False

    rec = pycolmap.Reconstruction(str(DATA / zone / "dense/sparse"))
    nodes = cameras_enu(rec, T, cloud.mean(axis=0))
    images = LazyImages(DATA / zone / "dense/images",
                        {iid: name for _, iid, name, _ in nodes})
    ortho = drape.orthophoto(surface, frame, cloud,
                             [(iid, node) for _, iid, _, node in nodes], images)
    painted = ortho.provenance > 0
    rgb = fill_unpainted(ortho.rgb, painted)

    # support: 255 = вершина вне нарисованных ячеек (страница сажает её на
    # рельеф движка), иначе расстояние до облака в дециметрах
    rows, cols = surface.rows, surface.cols
    vert_ok = np.zeros((rows, cols), bool)
    q = surface.cell_drawn
    vert_ok[:-1, :-1] |= q
    vert_ok[:-1, 1:] |= q
    vert_ok[1:, :-1] |= q
    vert_ok[1:, 1:] |= q
    sup = np.where(vert_ok.ravel(), np.clip(surface.support_m * 10, 0, 254),
                   255).astype(np.uint8)

    verts = surface.vertices.astype(np.float32)
    verts[:, 2] += alt0        # z сетки был относителен якоря высоты

    tex_name = f"insert_z-{zone}.jpg"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # строка 0 растра drape лежит у v1, страница ждёт строку 0 у v0
    cv2.imwrite(str(OUT_DIR / tex_name), np.flipud(rgb)[:, :, ::-1],
                [cv2.IMWRITE_JPEG_QUALITY, 85])
    meta = dict(
        name=f"z-{zone}",
        title=f"плотная 3D: {TITLES.get(zone, zone)}",
        mesh=1,
        trim=1,
        credit=f"COLMAP PatchMatch, зона {zone}, {len(xyz):,} точек, "
               f"геопривязка RMS {geo['rms_m']} м",
        origin_lat=a_lat, origin_lon=a_lon,
        dz_applied_m=round(dz, 2), rms_m=geo["rms_m"],
        parts=n_parts, main_share=round(big_share, 3),
        main_area_m2=round(big_area),
        texel_cm=round(rect.metres_per_texel * 100, 1),
        rows=rows, cols=cols, alt0=alt0,
        plane=dict(o=[round(float(v), 4) for v in frame.origin],
                   u=[round(float(v), 6) for v in frame.u],
                   v=[round(float(v), 6) for v in frame.v],
                   u0=round(rect.u0, 3), u1=round(rect.u1, 3),
                   v0=round(rect.v0, 3), v1=round(rect.v1, 3)),
        verts_b64=base64.b64encode(verts.tobytes()).decode(),
        support_b64=base64.b64encode(sup.tobytes()).decode(),
        tex=f"ortho/{tex_name}")
    meta = grade(meta, edge_gap_m(dem, meta))
    out_json.write_text(json.dumps(meta, separators=(",", ":")))
    print(f"{zone}: сетка {rows}x{cols} (шаг {posting:.2f} м), текстура "
          f"{rect.width}x{rect.height} ({rect.metres_per_texel * 100:.1f} см/"
          f"тексель), кадров {len(nodes)}, покрытие {ortho.covered_fraction:.0%}, "
          f"якорь {dz:+.1f} м, RMS {geo['rms_m']} м, "
          f"куски {n_parts} (главный {big_share:.0%}, {big_area:.0f} м²), "
          f"край {meta['edge_gap_m']} м, "
          f"{'НЕ ПОКАЗЫВАЕТСЯ (слабая)' if meta['weak'] else 'в показе'}, "
          f"json {out_json.stat().st_size // 1024} КБ + jpg "
          f"{(OUT_DIR / tex_name).stat().st_size // 1024} КБ", flush=True)
    return True


def _crop_sources() -> list[dict]:
    """Показываемые вставки как источники «здесь есть детальнее»: имя,
    тексель (м), шаг сетки (м) и lat/lon измеренных вершин."""
    out = []
    for p in sorted(OUT_DIR.glob("insert_*.json")):
        d = json.loads(p.read_text())
        if d.get("weak") or d.get("rms_m", 0) > CROP_MAX_RMS_M:
            continue
        tex = cv2.imread(str(OUT_DIR / Path(d["tex"]).name))
        if tex is None:
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
            m_lon = 111320.0 * math.cos(math.radians(d["origin_lat"]))
            lat = d["origin_lat"] + v[:, :, 1] / M_LAT
            lon = d["origin_lon"] + v[:, :, 0] / m_lon
            pl = d["plane"]
            texel = (pl["u1"] - pl["u0"]) / tex.shape[1]
            spacing = float(np.hypot(*(v[0, 1, :2] - v[0, 0, :2])))
            pts = np.stack([lat[meas], lon[meas]], axis=1)
        else:
            # heightfield (bake_insert.py): полная квадратная сетка n x n
            n = d["n"]
            lat = d["lat0"] + np.arange(n) * d["dlat"]
            lon = d["lon0"] + np.arange(n) * d["dlon"]
            texel = n * d["dlat"] * M_LAT / tex.shape[1]
            spacing = d["dlat"] * M_LAT
            gy, gx = np.meshgrid(lat, lon, indexing="ij")
            pts = np.stack([gy.ravel(), gx.ravel()], axis=1)
        out.append(dict(name=d["name"], texel_m=texel, spacing_m=spacing,
                        pts=pts))
    return out


def crop_overlaps(dem: Dem):
    """Грубая вставка уступает точной: в пятне более детальной (тексель
    мельче >= CROP_FACTOR раз) показываемой вставки её вершины гасятся в
    support=255 — иначе при совместном включении грубый мэш накрывает
    детальный. Перезапускать после каждого перепекания зон: build_zone
    пишет support заново, без вырезов."""
    from scipy.ndimage import label
    from scipy.spatial import cKDTree

    src = _crop_sources()
    for p in sorted(OUT_DIR.glob("insert_z-*.json")):
        d = json.loads(p.read_text())
        if d.get("weak"):
            continue
        rows, cols = d["rows"], d["cols"]
        v = np.frombuffer(base64.b64decode(d["verts_b64"]),
                          np.float32).reshape(rows, cols, 3)
        sup = np.frombuffer(base64.b64decode(d["support_b64"]),
                            np.uint8).reshape(rows, cols).copy()
        m_lon = 111320.0 * math.cos(math.radians(d["origin_lat"]))
        xy = np.stack([(d["origin_lat"] + v[:, :, 1] / M_LAT).ravel() * M_LAT,
                       (d["origin_lon"] + v[:, :, 0] / m_lon).ravel() * m_lon],
                      axis=1)
        my_texel = d["texel_cm"] / 100.0
        cut = np.zeros(rows * cols, bool)
        cut_by = []
        for s in src:
            if s["name"] == d["name"] or s["texel_m"] * CROP_FACTOR > my_texel:
                continue
            sxy = np.stack([s["pts"][:, 0] * M_LAT, s["pts"][:, 1] * m_lon],
                           axis=1)
            r = s["spacing_m"] * 1.2
            dist, _ = cKDTree(sxy).query(xy, distance_upper_bound=r)
            hit = np.isfinite(dist)
            if hit.any():
                cut |= hit
                cut_by.append(s["name"])
        cut &= (sup.ravel() < 255)
        if not cut.any():
            continue
        sup.ravel()[cut] = 255
        meas = sup < 255
        cells = (meas[:-1, :-1] & meas[:-1, 1:]
                 & meas[1:, :-1] & meas[1:, 1:])
        lab, _ = label(cells)
        sizes = np.bincount(lab.ravel())
        sizes[0] = 0
        big = int(sizes.max()) if sizes.size else 0
        posting = float(np.hypot(*(v[0, 1, :2] - v[0, 0, :2])))
        d["support_b64"] = base64.b64encode(sup.tobytes()).decode()
        d["crop_by"] = cut_by
        d["parts"] = int((sizes > 0).sum())
        d["main_share"] = round(big / max(int(sizes.sum()), 1), 3)
        d["main_area_m2"] = round(big * posting ** 2)
        d = grade(d, edge_gap_m(dem, d))
        p.write_text(json.dumps(d, separators=(",", ":")))
        print(f"{d['name']}: вырезано {int(cut.sum())} вершин под "
              f"{', '.join(cut_by)}; главный кусок {d['main_share']:.0%}, "
              f"край {d['edge_gap_m']} м, "
              f"{'НЕ ПОКАЗЫВАЕТСЯ (слабая)' if d['weak'] else 'в показе'}")


def regrade(dem: Dem):
    """Пересчёт критерия показа по готовым JSON, без перепекания."""
    for p in sorted(OUT_DIR.glob("insert_z-*.json")):
        meta = json.loads(p.read_text())
        meta = grade(meta, edge_gap_m(dem, meta))
        p.write_text(json.dumps(meta, separators=(",", ":")))
        print(f"{meta['name']}: край {meta['edge_gap_m']} м, тексель "
              f"{meta['texel_cm']} см, главный {meta['main_share']:.0%} — "
              f"{'НЕ ПОКАЗЫВАЕТСЯ (слабая)' if meta['weak'] else 'в показе'}")


def main():
    force = "--force" in sys.argv
    dem = Dem()
    if "--regrade" in sys.argv:
        regrade(dem)
        return
    if "--crop" in sys.argv:
        crop_overlaps(dem)
        return
    names = [a for a in sys.argv[1:] if not a.startswith("-")] or ZONES
    ok = 0
    for zone in names:
        try:
            ok += bool(build_zone(dem, zone, force))
        except Exception as e:  # одна кривая зона не роняет остальные
            print(f"{zone}: ОШИБКА {type(e).__name__}: {e}", flush=True)
    crop_overlaps(dem)
    print(f"готово: {ok} из {len(names)} зон")


if __name__ == "__main__":
    main()
