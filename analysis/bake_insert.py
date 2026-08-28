#!/usr/bin/env python3
"""Детальная вставка сцены для «Полёт 3D»: текстура в проекции вдоль стены.

Обзорная мозаика (build_ortho.py) проецирует кадры вертикально вниз — стена
в плане сжата, фронтальная съёмка сцены теряется. Вставка печёт текстуру
в СОБСТВЕННОЙ плоскости сцены (оси вдоль стены, по PCA рельефа) из ближних
кадров, без потолка зума мозаики, и кладётся в polyot-3d отдельным
мэш-патчем поверх рельефа (слой «вставка: сцена…»).

Геометрия патча — рельеф конвейера (HMA 8 м + DSM-патчи где есть), сеткой
0.5 м: камни выглядят резкой картинкой на грани, объёмными не становятся —
для объёма нужна локальная плотная реконструкция (следующий этап).

Выход: analysis/viewer/ortho/insert_<имя>.jpg (текстура)
       analysis/viewer/ortho/insert_<имя>.json (мэш + uv + рамка)
       analysis/pilot/insert-qc/<имя>_qc.jpg (текстура с LRF-крестами)

Запуск: analysis/.venv/bin/python analysis/bake_insert.py
"""
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geoproject import Dem, _bilinear  # noqa: E402
from build_ortho import at, focal_at, load_cov, load_rows  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "analysis/viewer/ortho"
QC_DIR = ROOT / "analysis/pilot/insert-qc"

W, H = 1920, 1080
M_LAT = 111132.0

SCENES = [dict(
    name="koshki",
    title="сцена кошек 16.08",
    lat=39.482548, lon=73.585926, half_m=25.0,
    step_m=0.5,            # шаг сетки мэша
    texel_m=0.012,         # пиксель текстуры в плоскости стены
    # ближние ролики сцены (карточка реестра «Сцена 16.08»)
    videos=[("DJI_20260816114520_0001_Z", None, None),
            ("DJI_20260816141704_0001_Z", 780.0, 995.0),
            ("DJI_20260816143344_0005_Z", None, None)],
    frame_step=1.0,
    # LRF-точки групп сцены — кресты на QC-текстуре
    marks=[(39.482548, 73.585926, "gr1"),
           (39.482528, 73.585931, "gr2"),
           (39.482522, 73.585943, "gr3")],
)]


def scene_grid(dem, sc):
    """Сетка мэша: lat/lon/alt + локальные ENU-метры от центра сцены."""
    m_lon = 111320.0 * math.cos(math.radians(sc["lat"]))
    n = int(round(2 * sc["half_m"] / sc["step_m"])) + 1
    dlat = sc["step_m"] / M_LAT
    dlon = sc["step_m"] / m_lon
    lat0 = sc["lat"] - sc["half_m"] / M_LAT          # ряды с юга на север
    lon0 = sc["lon"] - sc["half_m"] / m_lon
    lats = lat0 + np.arange(n) * dlat
    lons = lon0 + np.arange(n) * dlon
    glon, glat = np.meshgrid(lons, lats)
    z = _bilinear(dem.z, (glon - dem.lon0) / dem.dlon, (dem.lat0 - glat) / dem.dlat)
    if dem._patch is not None:
        off, plon0, plat0, pdlon, pdlat = dem._patch
        v = _bilinear(off, (glon - plon0) / pdlon, (plat0 - glat) / pdlat)
        z = np.where(np.isfinite(v), z + v, z)
    e = (glon - sc["lon"]) * m_lon
    nn = (glat - sc["lat"]) * M_LAT
    u = z - float(z.mean())
    return dict(lat0=lat0, lon0=lon0, dlat=dlat, dlon=dlon, n=n,
                glat=glat, glon=glon, z=z, e=e, n_m=nn, up=u, m_lon=m_lon)


def wall_axes(g):
    """Оси текстуры по PCA рельефа сцены: (нормаль, вдоль стены, вниз по склону)."""
    pts = np.stack([g["e"].ravel(), g["n_m"].ravel(), g["up"].ravel()], axis=1)
    pts = pts - pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts, full_matrices=False)
    nrm = vt[2]
    if nrm[2] < 0:
        nrm = -nrm
    down = np.array([0.0, 0.0, -1.0])
    ev = down - nrm * float(down @ nrm)
    ev /= np.linalg.norm(ev)
    eu = np.cross(ev, nrm)
    eu /= np.linalg.norm(eu)
    return nrm, eu, ev


def ray_px(cam_e, cam_n, cam_u, yaw, pitch, f, pe, pn, pu):
    """Пиксели (px, py) проекции точек в кадр — инверсия geoproject.ray_dir."""
    de, dn, du = pe - cam_e, pn - cam_n, pu - cam_u
    dist = np.sqrt(de * de + dn * dn + du * du)
    pitch_t = np.arcsin(np.clip(du / np.maximum(dist, 1e-9), -1, 1))
    yaw_t = np.arctan2(de, dn)
    ax = yaw_t - math.radians(yaw)
    ax = (ax + np.pi) % (2 * np.pi) - np.pi
    ay = math.radians(pitch) - pitch_t
    px = W / 2 + f * np.tan(ax)
    py = H / 2 + f * np.tan(ay)
    behind = np.abs(ax) > math.pi / 2
    return px, py, dist, behind


def bake(dem, sc):
    g = scene_grid(dem, sc)
    nrm, eu, ev = wall_axes(g)
    slope = math.degrees(math.acos(max(min(nrm[2], 1), -1)))
    print(f"{sc['name']}: сетка {g['n']}x{g['n']}, склон {slope:.0f}°")

    pts = np.stack([g["e"].ravel(), g["n_m"].ravel(), g["up"].ravel()], axis=1)
    uq = pts @ eu
    vq = pts @ ev
    u0, u1 = float(uq.min()), float(uq.max())
    v0, v1 = float(vq.min()), float(vq.max())
    tw = int((u1 - u0) / sc["texel_m"])
    th = int((v1 - v0) / sc["texel_m"])
    k = max(tw, th) / 4096.0
    if k > 1:
        tw, th = int(tw / k), int(th / k)
    res_u = (u1 - u0) / tw
    res_v = (v1 - v0) / th
    print(f"  текстура {tw}x{th} ({res_u * 100:.1f} см/пикс в плоскости стены)")

    # (u, v) -> (lat, lon) аффинно по узлам мэша (стена почти плоская,
    # остаточная кривизна даёт сдвиг текстуры на сантиметры)
    A = np.stack([uq, vq, np.ones_like(uq)], axis=1)
    cl, *_ = np.linalg.lstsq(A, np.stack([g["glat"].ravel(), g["glon"].ravel()],
                                         axis=1), rcond=None)
    tu = u0 + (np.arange(tw) + 0.5) * res_u
    tv = v0 + (np.arange(th) + 0.5) * res_v
    tuu, tvv = np.meshgrid(tu, tv)
    tlat = cl[0, 0] * tuu + cl[1, 0] * tvv + cl[2, 0]
    tlon = cl[0, 1] * tuu + cl[1, 1] * tvv + cl[2, 1]
    tz = _bilinear(dem.z, (tlon - dem.lon0) / dem.dlon, (dem.lat0 - tlat) / dem.dlat)
    if dem._patch is not None:
        off, plon0, plat0, pdlon, pdlat = dem._patch
        v = _bilinear(off, (tlon - plon0) / pdlon, (plat0 - tlat) / pdlat)
        tz = np.where(np.isfinite(v), tz + v, tz)
    te = (tlon - sc["lon"]) * g["m_lon"]
    tn = (tlat - sc["lat"]) * M_LAT
    tu3 = tz - float(g["z"].mean())

    tex = np.zeros((th, tw, 3), np.uint8)
    score = np.zeros((th, tw), np.float32)
    zmean = float(g["z"].mean())

    for name, t0, t1 in sc["videos"]:
        video = next(iter((ROOT / "data/drive").rglob(name + ".MP4")), None)
        if video is None:
            print(f"  {name}: видео не найдено, пропуск", file=sys.stderr)
            continue
        rows = load_rows(video)
        cov = load_cov(video)
        cap = cv2.VideoCapture(str(video))
        dur = cap.get(cv2.CAP_PROP_FRAME_COUNT) / max(cap.get(cv2.CAP_PROP_FPS), 1)
        ta = t0 if t0 is not None else 0.0
        tb = min(t1 if t1 is not None else dur, dur)
        used = 0
        t = ta
        while t < tb:
            pose = [at(rows, t, k) for k in ("lat", "lon", "alt_m", "gb_yaw", "gb_pitch")]
            f = focal_at(cov, t)
            if not (all(math.isfinite(p) for p in pose) and f):
                t += sc["frame_step"]
                continue
            lat0, lon0, alt0, yaw, pitch = pose
            cam_e = (lon0 - sc["lon"]) * g["m_lon"]
            cam_n = (lat0 - sc["lat"]) * M_LAT
            cam_u = alt0 - zmean
            if math.hypot(cam_e, cam_n, cam_u) > 400:
                t += sc["frame_step"]
                continue
            px, py, dist, behind = ray_px(cam_e, cam_n, cam_u, yaw, pitch, f,
                                          te, tn, tu3)
            inside = (~behind & (px > 8) & (px < W - 8) & (py > 8) & (py < H - 8))
            if inside.mean() < 0.002:
                t += sc["frame_step"]
                continue
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            got, img = cap.read()
            if not got:
                t += sc["frame_step"]
                continue
            gray = cv2.cvtColor(cv2.resize(img, (640, 360)), cv2.COLOR_BGR2GRAY)
            if cv2.Laplacian(gray, cv2.CV_32F).var() < 25:   # смаз панорамирования
                t += sc["frame_step"]
                continue
            # наклон луча к нормали стены: скользящие ракурсы штрафуются
            rays = np.stack([(te - cam_e), (tn - cam_n), (tu3 - cam_u)], axis=-1)
            rn = -(rays @ nrm) / np.maximum(dist, 1e-9)
            gsd = dist / f
            sc_map = np.where(inside & (rn > 0.15),
                              1.0 / np.maximum(gsd, 1e-6) ** 2 * rn, 0.0
                              ).astype(np.float32)
            win = sc_map > score
            if not win.any():
                t += sc["frame_step"]
                continue
            # префильтр: кадр детальнее текстуры — уменьшенная копия
            med_gsd = float(np.median(gsd[inside])) if inside.any() else 1.0
            scale = med_gsd / max(res_u, res_v)
            lvl = (0 if scale >= 0.75
                   else min(3, max(0, int(round(-math.log2(max(scale, 1e-6)))))))
            src = img
            for _ in range(lvl):
                src = cv2.resize(src, ((src.shape[1] + 1) // 2,
                                       (src.shape[0] + 1) // 2),
                                 interpolation=cv2.INTER_AREA)
            mapx = (px / (1 << lvl)).astype(np.float32)
            mapy = (py / (1 << lvl)).astype(np.float32)
            colors = cv2.remap(src, mapx, mapy, cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_CONSTANT)
            tex[win] = colors[win]
            score[win] = sc_map[win]
            used += 1
            t += sc["frame_step"]
        cap.release()
        print(f"  {name}: кадров уложено {used}")

    fill = float((score > 0).mean())
    print(f"  заполнено {fill:.0%}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(OUT_DIR / f"insert_{sc['name']}.jpg"), tex,
                [cv2.IMWRITE_JPEG_QUALITY, 90])

    QC_DIR.mkdir(parents=True, exist_ok=True)
    qc = tex.copy()
    for mla, mlo, mname in sc["marks"]:
        me = (mlo - sc["lon"]) * g["m_lon"]
        mn = (mla - sc["lat"]) * M_LAT
        try:
            mz = dem.elev(mla, mlo) - zmean
        except ValueError:
            continue
        p = np.array([me, mn, mz])
        mu, mv = float(p @ eu), float(p @ ev)
        qx = int((mu - u0) / res_u)
        qy = int((mv - v0) / res_v)
        if 0 <= qx < tw and 0 <= qy < th:
            cv2.drawMarker(qc, (qx, qy), (0, 120, 255), cv2.MARKER_CROSS, 60, 2)
            cv2.putText(qc, mname, (qx + 24, qy - 10),
                        cv2.FONT_HERSHEY_COMPLEX, 0.9, (0, 120, 255), 2)
    cv2.imwrite(str(QC_DIR / f"{sc['name']}_qc.jpg"), qc,
                [cv2.IMWRITE_JPEG_QUALITY, 88])

    # мэш: узлы сетки c uv в текстуре
    node_u = (pts @ eu).reshape(g["n"], g["n"])
    node_v = (pts @ ev).reshape(g["n"], g["n"])
    zmin = float(np.floor(g["z"].min()))
    meta = dict(
        name=sc["name"], title=sc["title"],
        lat0=g["lat0"], lon0=g["lon0"], dlat=g["dlat"], dlon=g["dlon"],
        n=g["n"], zmin=zmin,
        h=np.rint((g["z"] - zmin) * 10).astype(int).ravel().tolist(),
        u=np.round((node_u - u0) / (u1 - u0), 4).ravel().tolist(),
        v=np.round((node_v - v0) / (v1 - v0), 4).ravel().tolist(),
        tex=f"ortho/insert_{sc['name']}.jpg", fill=round(fill, 2))
    (OUT_DIR / f"insert_{sc['name']}.json").write_text(
        json.dumps(meta, separators=(",", ":")))
    print(f"  insert_{sc['name']}.json + текстура записаны")


def main():
    dem = Dem()
    for sc in SCENES:
        bake(dem, sc)


if __name__ == "__main__":
    main()
