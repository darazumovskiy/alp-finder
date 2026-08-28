#!/usr/bin/env python3
"""Регистрация зум-фото дрона на геометрию плиток -> реестр фото-камер.

Зум-JPG — лучший материал проекта (0.2-0.35 см/пиксель против 2-50 см у
видео), и только у фото есть фокусное (EXIF), углы подвеса и лазерная точка
прицеливания (XMP LRFTarget*). Пятно зум-кадра на склоне — единицы метров,
поэтому прямое SIFT-сопоставление с плиткой не работает (разница масштабов
~25х). Регистрация идёт от лазерного якоря:

  1. начальная поза: позиция GPS, взгляд ТОЧНО в лазерную точку (она
     измерена дальномером), крен 0;
  2. фокусное: EXIF даёт вилку (цифровой зум), точное значение подбирается
     по максимуму корреляции фото, спроецированного на плоскость плитки,
     с её ортотекстурой;
  3. остаточный сдвиг/поворот добирается ECC-совмещением в плоскости
     плитки; поза пересчитывается PnP по точкам с настоящими высотами;
  4. гейт честности: итоговая корреляция >= MIN_NCC и позиция не дальше
     MAX_POS_JUMP_M от GPS — иначе фото отбрасывается (лучше без фото,
     чем приклеенное не туда).

Выход: analysis/scene3d/photo-cams.json — камеры в осях E,N,U (позиция
lat/lon/alt + орты right/up/forward + f_px), потребляет slope_tiles.py как
самый детальный источник текстуры.

Запуск: analysis/.venv/bin/python analysis/scene3d/photo_register.py
        (--limit N — первые N кандидатов; --qc — PNG-сверки в /tmp/photoreg)
"""
from __future__ import annotations

import base64
import json
import math
import re
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

TILES_DIR = ROOT / "analysis/viewer/ortho/tiles3d"
OUT_JSON = HERE / "photo-cams.json"

M_LAT = 111132.0
LAT_S, LAT_N = 39.460, 39.510
LON_W, LON_E = 73.572, 73.606
MIN_NCC = 0.22            # ниже — совмещение не доказано, фото не берём
MAX_POS_JUMP_M = 60.0
F_GRID = (0.75, 0.9, 1.0, 1.15, 1.35, 1.6, 1.9, 2.36, 2.8)


def m_lon(lat: float) -> float:
    return 111320.0 * math.cos(math.radians(lat))


def parse_photo(path: Path) -> dict | None:
    raw = path.read_bytes()[:300_000]
    a = raw.find(b"<x:xmpmeta")
    if a < 0:
        return None
    xmp = raw[a:raw.find(b"</x:xmpmeta>")].decode("utf-8", "ignore")

    def fx(key):
        m = re.search(r'drone-dji:' + key + r'="([^"]*)"', xmp)
        return float(m.group(1)) if m else None

    lat, lon, alt = fx("GpsLatitude"), fx("GpsLongitude"), fx("AbsoluteAltitude")
    t_lat, t_lon = fx("LRFTargetLat"), fx("LRFTargetLon")
    t_alt, t_d = fx("LRFTargetAbsAlt"), fx("LRFTargetDistance")
    if None in (lat, lon, alt, t_lat, t_lon, t_alt):
        return None
    from PIL import Image
    from PIL.ExifTags import TAGS
    im = Image.open(path)
    ex = {TAGS.get(k, k): v for k, v in (im._getexif() or {}).items()}
    f35 = float(ex.get("FocalLengthIn35mmFilm") or 0)
    if not f35:
        return None
    w, h = im.size
    return dict(path=str(path), w=w, h=h,
                lat=lat, lon=lon, alt=alt,
                t_lat=t_lat, t_lon=t_lon, t_alt=t_alt,
                t_dist=t_d or 100.0,
                f0=f35 / 36.0 * w)


def load_tile(p: Path) -> dict | None:
    d = json.loads(p.read_text())
    tex = cv2.imread(str(ROOT / "analysis/viewer" / d["tex"]),
                     cv2.IMREAD_GRAYSCALE)
    if tex is None:
        return None
    rows, cols = d["rows"], d["cols"]
    v = np.frombuffer(base64.b64decode(d["verts_b64"]),
                      np.float32).reshape(rows, cols, 3)
    sup = np.frombuffer(base64.b64decode(d["support_b64"]),
                        np.uint8).reshape(rows, cols)
    pl = d["plane"]
    o = np.array(pl["o"]); U = np.array(pl["u"]); V = np.array(pl["v"])
    n = np.cross(U, V); n /= np.linalg.norm(n)
    rel = v.reshape(-1, 3).astype(float)
    rel[:, 2] -= d["alt0"]
    rel -= o
    hgt = (rel @ n).reshape(rows, cols)
    return dict(name=d["name"], tex=tex, rows=rows, cols=cols,
                origin_lat=d["origin_lat"], origin_lon=d["origin_lon"],
                alt0=d["alt0"], o=o, U=U, V=V, n=n,
                u0=pl["u0"], u1=pl["u1"], v0=pl["v0"], v1=pl["v1"],
                hgt=hgt, meas=sup.reshape(rows, cols) < 255)


def lla_to_plane(tile: dict, lat: float, lon: float, alt: float):
    """(lat,lon,alt) -> (u, v, tex_px, tex_py) в плоскости плитки."""
    ml = m_lon(tile["origin_lat"])
    p = np.array([(lon - tile["origin_lon"]) * ml,
                  (lat - tile["origin_lat"]) * M_LAT,
                  alt - tile["alt0"]]) - tile["o"]
    u, v = float(p @ tile["U"]), float(p @ tile["V"])
    H, W = tile["tex"].shape
    tx = (u - tile["u0"]) / (tile["u1"] - tile["u0"]) * W
    ty = (tile["v1"] - v) / (tile["v1"] - tile["v0"]) * H
    return u, v, tx, ty


def plane_h_at(tile: dict, u: np.ndarray, v: np.ndarray):
    """Высота поверхности над плоскостью и измеренность в точках (u,v)."""
    rows, cols = tile["rows"], tile["cols"]
    gc = np.clip((u - tile["u0"]) / (tile["u1"] - tile["u0"]) * (cols - 1),
                 0, cols - 1)
    gr = np.clip((tile["v1"] - v) / (tile["v1"] - tile["v0"]) * (rows - 1),
                 0, rows - 1)
    gi, gj = np.round(gr).astype(int), np.round(gc).astype(int)
    return tile["hgt"][gi, gj], tile["meas"][gi, gj]


def cam_pose(photo: dict):
    """Мир = ENU-метры от лазерной точки. Взгляд точно в неё, крен 0."""
    ml = m_lon(photo["t_lat"])
    C = np.array([(photo["lon"] - photo["t_lon"]) * ml,
                  (photo["lat"] - photo["t_lat"]) * M_LAT,
                  photo["alt"] - photo["t_alt"]])
    fwd = -C / np.linalg.norm(C)
    right = np.cross(fwd, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, fwd)
    R = np.stack([right, -up, fwd])      # оси камеры: x вправо, y вниз, z вперёд
    return C, R


def tile_world(tile: dict, photo: dict, u, v, h):
    """Точка плоскости плитки (u,v,h) -> мир ENU лазерной точки фото."""
    ml = m_lon(tile["origin_lat"])
    p = (tile["o"][None] + np.asarray(u)[:, None] * tile["U"][None]
         + np.asarray(v)[:, None] * tile["V"][None]
         + np.asarray(h)[:, None] * tile["n"][None])
    lat = tile["origin_lat"] + p[:, 1] / M_LAT
    lon = tile["origin_lon"] + p[:, 0] / ml
    alt = tile["alt0"] + p[:, 2]
    ml2 = m_lon(photo["t_lat"])
    return np.stack([(lon - photo["t_lon"]) * ml2,
                     (lat - photo["t_lat"]) * M_LAT,
                     alt - photo["t_alt"]], axis=1)


def register(photo: dict, tiles: list[dict], qc_dir: Path | None) -> dict | None:
    # опора: поверхность (плитка или детальная сцена) с максимумом
    # измеренного в окне совмещения вокруг лазерной точки
    half = photo["t_dist"] * (photo["w"] / (photo["f0"] * F_GRID[0])) * 0.75
    half = float(np.clip(half, 4.0, 60.0))
    tile, t_txy, best_meas = None, None, 0.0
    for t in tiles:
        u, v, tx, ty = lla_to_plane(t, photo["t_lat"], photo["t_lon"],
                                    photo["t_alt"])
        H, W = t["tex"].shape
        if not (0 <= tx < W and 0 <= ty < H):
            continue
        texel_t = (t["u1"] - t["u0"]) / W
        r = max(4, int(half / texel_t))
        ga = np.linspace(u - half, u + half, 15)
        gb = np.linspace(v - half, v + half, 15)
        gaa, gbb = np.meshgrid(ga, gb)
        _, okw = plane_h_at(t, gaa.ravel(), gbb.ravel())
        frac = float(okw.mean())
        del r
        if frac > best_meas:
            tile, t_txy, best_meas = t, (u, v), frac
    if tile is None or best_meas < 0.3:
        return None

    img = cv2.imread(photo["path"], cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    C, R0 = cam_pose(photo)
    texel = (tile["u1"] - tile["u0"]) / tile["tex"].shape[1]

    u_c, v_c = t_txy
    _, _, tx0, ty0 = lla_to_plane(tile, photo["t_lat"], photo["t_lon"],
                                  photo["t_alt"])
    Wp = int(2 * half / texel)
    if Wp < 48:
        return None
    x0, y0 = int(tx0 - Wp // 2), int(ty0 - Wp // 2)
    Ht, Wt = tile["tex"].shape
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(Wt, x0 + Wp), min(Ht, y0 + Wp)
    patch = tile["tex"][y0:y1, x0:x1]
    if patch.size < 48 * 48:
        return None

    # тексель патча -> (u,v) плоскости; высота — срединная поверхность
    def patch_uv(px, py):
        u = tile["u0"] + (x0 + px + 0.5) / Wt * (tile["u1"] - tile["u0"])
        v = tile["v1"] - (y0 + py + 0.5) / Ht * (tile["v1"] - tile["v0"])
        return u, v

    uc0, vc0 = patch_uv(0, 0)
    uc1, vc1 = patch_uv(x1 - x0 - 1, y1 - y0 - 1)
    h_mid, _ = plane_h_at(tile, np.array([u_c]), np.array([v_c]))
    corners_uv = np.array([[uc0, vc0], [uc1, vc0], [uc1, vc1], [uc0, vc1]])

    def warp_for(f):
        """Фото -> пространство патча гомографией через позу и плоскость."""
        K = np.array([[f, 0, photo["w"] / 2], [0, f, photo["h"] / 2], [0, 0, 1]])
        world = tile_world(tile, photo, corners_uv[:, 0], corners_uv[:, 1],
                           np.full(4, h_mid[0]))
        camp = (R0 @ (world - C).T).T
        if (camp[:, 2] <= 1.0).any():
            return None
        px = (K @ camp.T).T
        px = px[:, :2] / px[:, 2:3]
        dst = np.array([[0, 0], [x1 - x0 - 1, 0],
                        [x1 - x0 - 1, y1 - y0 - 1], [0, y1 - y0 - 1]], float)
        Hm = cv2.getPerspectiveTransform(px.astype(np.float32),
                                         dst.astype(np.float32))
        return cv2.warpPerspective(img, Hm, (x1 - x0, y1 - y0)), Hm

    def ncc(a, b):
        m = (a > 0) & (b > 0)
        if m.sum() < 500:
            return -1.0
        av, bv = a[m].astype(float), b[m].astype(float)
        av -= av.mean(); bv -= bv.mean()
        d = math.sqrt((av * av).sum() * (bv * bv).sum())
        return float((av * bv).sum() / d) if d > 0 else -1.0

    best = None
    for fm in F_GRID:
        w = warp_for(photo["f0"] * fm)
        if w is None:
            continue
        s = ncc(w[0], patch)
        if best is None or s > best[0]:
            best = (s, photo["f0"] * fm, w[0], w[1])
    if best is None or best[0] < 0.05:
        return None
    score, f, warped, Hm = best

    # добор сдвига/поворота ECC в плоскости патча
    try:
        warp = np.eye(2, 3, dtype=np.float32)
        cc, warp = cv2.findTransformECC(
            patch, warped, warp, cv2.MOTION_EUCLIDEAN,
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 120, 1e-5),
            None, 5)
        fixed = cv2.warpAffine(warped, warp, (patch.shape[1], patch.shape[0]),
                               flags=cv2.WARP_INVERSE_MAP)
        s2 = ncc(fixed, patch)
        if s2 > score:
            score, warped = s2, fixed
            A = np.vstack([warp, [0, 0, 1]])
            Hm = np.linalg.inv(A) @ Hm
    except cv2.error:
        pass
    if score < MIN_NCC:
        return None

    # поза PnP: сетка точек патча (с настоящими высотами) -> пиксели фото
    gs = 14
    gx = np.linspace(4, x1 - x0 - 5, gs)
    gy = np.linspace(4, y1 - y0 - 5, gs)
    gxx, gyy = np.meshgrid(gx, gy)
    uu, vv = patch_uv(gxx.ravel(), gyy.ravel())
    hh, ok = plane_h_at(tile, uu, vv)
    if ok.sum() < 20:
        return None
    world = tile_world(tile, photo, uu[ok], vv[ok], hh[ok])
    pts_patch = np.stack([gxx.ravel()[ok], gyy.ravel()[ok]], axis=1)
    inv = np.linalg.inv(Hm)
    ph = (inv @ np.column_stack([pts_patch,
                                 np.ones(len(pts_patch))]).T).T
    pts_img = ph[:, :2] / ph[:, 2:3]
    inside = ((pts_img[:, 0] > 0) & (pts_img[:, 0] < photo["w"])
              & (pts_img[:, 1] > 0) & (pts_img[:, 1] < photo["h"]))
    if inside.sum() < 12:
        return None
    K = np.array([[f, 0, photo["w"] / 2], [0, f, photo["h"] / 2], [0, 0, 1]])
    okp, rvec, tvec = cv2.solvePnP(world[inside].astype(np.float64),
                                   pts_img[inside].astype(np.float64), K, None,
                                   flags=cv2.SOLVEPNP_SQPNP)
    if not okp:
        return None
    R, _ = cv2.Rodrigues(rvec)
    C2 = (-R.T @ tvec).ravel()
    jump = float(np.linalg.norm(C2 - C))
    if jump > MAX_POS_JUMP_M:
        return None

    if qc_dir:
        qc_dir.mkdir(parents=True, exist_ok=True)
        side = np.concatenate([patch, warped], axis=1)
        cv2.imwrite(str(qc_dir / (Path(photo["path"]).stem + ".png")), side)

    ml = m_lon(photo["t_lat"])
    return dict(
        file=photo["path"], w=photo["w"], h=photo["h"], f_px=round(f, 1),
        lat=photo["t_lat"] + C2[1] / M_LAT,
        lon=photo["t_lon"] + C2[0] / ml,
        alt=photo["t_alt"] + C2[2],
        right=[round(float(x), 6) for x in R[0]],
        up=[round(float(x), 6) for x in -R[1]],
        forward=[round(float(x), 6) for x in R[2]],
        ncc=round(score, 3), gps_jump_m=round(jump, 1),
        tile=tile["name"])


def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    qc_dir = Path("/tmp/photoreg") if "--qc" in sys.argv else None

    # опоры: плитки склона + детальные mesh-сцены (модель C C — тексель
    # 3.2 см, лазерные точки чаще всего именно в её пятне)
    paths = sorted(TILES_DIR.glob("t_*.json"))
    paths += [p for p in sorted((ROOT / "analysis/viewer/ortho")
                                .glob("insert_*.json"))
              if not p.name.startswith("insert_z-")
              and json.loads(p.read_text()).get("mesh")]
    tiles = [t for t in (load_tile(p) for p in paths) if t]
    print(f"опор (плитки + сцены): {len(tiles)}")

    photos = []
    for line in (ROOT / "scripts/manifest.tsv").read_text().splitlines():
        rel = line.split("\t")[0]
        if not rel.upper().endswith(".JPG") or "_Z" not in rel.upper():
            continue
        p = ROOT / rel
        if not p.exists():
            continue
        meta = parse_photo(p)
        if meta is None:
            continue
        if not (LAT_S < meta["t_lat"] < LAT_N and LON_W < meta["t_lon"] < LON_E):
            continue
        photos.append(meta)
    print(f"фото-кандидатов с лазерной точкой в рамке: {len(photos)}")
    if limit:
        photos = photos[:limit]

    out, rej = [], 0
    for ph in photos:
        try:
            r = register(ph, tiles, qc_dir)
        except Exception as e:
            print(f"  {Path(ph['path']).name}: ОШИБКА {type(e).__name__}: {e}",
                  flush=True)
            continue
        if r is None:
            rej += 1
            continue
        out.append(r)
        print(f"  {Path(ph['path']).name}: f={r['f_px']:.0f}px NCC {r['ncc']} "
              f"уход от GPS {r['gps_jump_m']} м (плитка {r['tile']})",
              flush=True)
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"зарегистрировано {len(out)}, отклонено {rej}; реестр: {OUT_JSON}")


if __name__ == "__main__":
    main()
