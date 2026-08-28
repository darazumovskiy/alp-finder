#!/usr/bin/env python3
"""Пер-ролик поправка привязки: сшивка лоскутов В ПЛОСКОСТИ СТЕНЫ.

Вертикальные орто-лоскуты стены (align_ortho_anchors.py) с разных ракурсов
искажены каждый по-своему — SIFT-сшивка не проходит гейт корреляции
(проверено 19.08: ncc ~0 до и после сдвига). Здесь лоскут каждого ролика
рендерится в проекции вдоль стены (машинерия bake_insert.py) — геометрия
искажения общая, различия только в свете/снеге, порода стабильна.

Сдвиг ищется SIFT-ом в плоскости (eu, ev), гейт — рост корреляции совместной
области; сдвиг переводится в метры С/В и пишется в тот же
analysis/coverage/ortho-anchors.tsv для build_ortho.py.

Запуск: analysis/.venv/bin/python analysis/align_wall_anchors.py
"""
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geoproject import Dem  # noqa: E402
from bake_insert import M_LAT, ray_px, scene_grid, wall_axes  # noqa: E402
from build_ortho import at, focal_at, load_cov, load_rows  # noqa: E402
from align_ortho_anchors import (  # noqa: E402
    MIN_OVERLAP, OUT_TSV, QC_DIR, candidates, masked_shift, ncc)

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "analysis/ortho-work/anchor-wall-patches"

# плоскость сшивки: кластер вещей (спальник/палки/крышка) на стене
SCENE = dict(name="anchor-veshchi", lat=39.483125, lon=73.585391,
             half_m=32.0, step_m=0.8, texel_m=0.05)
STEP_S = 1.5
DIST_MAX_M = 320.0
MAX_SHIFT_M = 45.0
MIN_FILL = 0.10


def render_wall_patch(dem, g, nrm, eu, ev, frame, video):
    """Лоскут ролика в плоскости стены: (bgr, маска) или None."""
    u0, u1, v0, v1, tw, th, te, tn, tu3, res = frame
    rows = load_rows(video)
    cov = load_cov(video)
    if not cov:
        return None
    zmean = float(g["z"].mean())
    tex = np.zeros((th, tw, 3), np.uint8)
    score = np.zeros((th, tw), np.float32)
    cap = cv2.VideoCapture(str(video))
    dur = cap.get(cv2.CAP_PROP_FRAME_COUNT) / max(cap.get(cv2.CAP_PROP_FPS), 1)
    t = 0.0
    while t < dur:
        pose = [at(rows, t, k) for k in ("lat", "lon", "alt_m", "gb_yaw", "gb_pitch")]
        f = focal_at(cov, t)
        if not (all(math.isfinite(p) for p in pose) and f):
            t += STEP_S
            continue
        lat0, lon0, alt0, yaw, pitch = pose
        cam_e = (lon0 - SCENE["lon"]) * g["m_lon"]
        cam_n = (lat0 - SCENE["lat"]) * M_LAT
        cam_u = alt0 - zmean
        if math.hypot(cam_e, cam_n, cam_u) > DIST_MAX_M:
            t += STEP_S
            continue
        px, py, dist, behind = ray_px(cam_e, cam_n, cam_u, yaw, pitch, f,
                                      te, tn, tu3)
        inside = (~behind & (px > 8) & (px < 1920 - 8) & (py > 8) & (py < 1080 - 8))
        if inside.mean() < 0.005:
            t += STEP_S
            continue
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        got, img = cap.read()
        if not got:
            t += STEP_S
            continue
        gray = cv2.cvtColor(cv2.resize(img, (640, 360)), cv2.COLOR_BGR2GRAY)
        if cv2.Laplacian(gray, cv2.CV_32F).var() < 25:
            t += STEP_S
            continue
        rays = np.stack([(te - cam_e), (tn - cam_n), (tu3 - cam_u)], axis=-1)
        rn = -(rays @ nrm) / np.maximum(dist, 1e-9)
        gsd = dist / f
        sc_map = np.where(inside & (rn > 0.15),
                          1.0 / np.maximum(gsd, 1e-6) ** 2 * rn, 0.0
                          ).astype(np.float32)
        win = sc_map > score
        if win.any():
            med_gsd = float(np.median(gsd[inside])) if inside.any() else 1.0
            scale = med_gsd / SCENE["texel_m"]
            lvl = (0 if scale >= 0.75
                   else min(3, max(0, int(round(-math.log2(max(scale, 1e-6)))))))
            src = img
            for _ in range(lvl):
                src = cv2.resize(src, ((src.shape[1] + 1) // 2,
                                       (src.shape[0] + 1) // 2),
                                 interpolation=cv2.INTER_AREA)
            colors = cv2.remap(src, (px / (1 << lvl)).astype(np.float32),
                               (py / (1 << lvl)).astype(np.float32),
                               cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
            tex[win] = colors[win]
            score[win] = sc_map[win]
        t += STEP_S
    cap.release()
    mask = score > 0
    if mask.mean() < MIN_FILL or mask.sum() < MIN_OVERLAP:
        return None
    return tex, mask


def cached(dem, g, nrm, eu, ev, frame, name, video):
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"{name}.npz"
    if p.exists():
        d = np.load(p)
        return (d["bgr"], d["mask"]) if d["ok"] else None
    got = render_wall_patch(dem, g, nrm, eu, ev, frame, video)
    if got:
        np.savez_compressed(p, ok=True, bgr=got[0], mask=got[1])
    else:
        np.savez_compressed(p, ok=False, bgr=np.zeros(1), mask=np.zeros(1))
    return got


def main():
    QC_DIR.mkdir(parents=True, exist_ok=True)
    dem = Dem()
    g = scene_grid(dem, SCENE)
    nrm, eu, ev = wall_axes(g)

    pts = np.stack([g["e"].ravel(), g["n_m"].ravel(), g["up"].ravel()], axis=1)
    uq, vq = pts @ eu, pts @ ev
    u0, u1 = float(uq.min()), float(uq.max())
    v0, v1 = float(vq.min()), float(vq.max())
    tw = int((u1 - u0) / SCENE["texel_m"])
    th = int((v1 - v0) / SCENE["texel_m"])
    A = np.stack([uq, vq, np.ones_like(uq)], axis=1)
    cl, *_ = np.linalg.lstsq(A, np.stack([g["glat"].ravel(), g["glon"].ravel()],
                                         axis=1), rcond=None)
    tu = u0 + (np.arange(tw) + 0.5) * (u1 - u0) / tw
    tv = v0 + (np.arange(th) + 0.5) * (v1 - v0) / th
    tuu, tvv = np.meshgrid(tu, tv)
    tlat = cl[0, 0] * tuu + cl[1, 0] * tvv + cl[2, 0]
    tlon = cl[0, 1] * tuu + cl[1, 1] * tvv + cl[2, 1]
    from geoproject import _bilinear
    tz = _bilinear(dem.z, (tlon - dem.lon0) / dem.dlon, (dem.lat0 - tlat) / dem.dlat)
    if dem._patch is not None:
        off, plon0, plat0, pdlon, pdlat = dem._patch
        v = _bilinear(off, (tlon - plon0) / pdlon, (plat0 - tlat) / pdlat)
        tz = np.where(np.isfinite(v), tz + v, tz)
    te = (tlon - SCENE["lon"]) * g["m_lon"]
    tn = (tlat - SCENE["lat"]) * M_LAT
    tu3 = tz - float(g["z"].mean())
    frame = (u0, u1, v0, v1, tw, th, te, tn, tu3, SCENE["texel_m"])
    res = max((u1 - u0) / tw, (v1 - v0) / th)

    cand = candidates()
    names = [n for n, _ in cand]
    ref_name = next((n for n, _ in cand if n.startswith("DJI_20260815")), names[0])
    print(f"кандидатов {len(cand)}, опорный: {ref_name}, "
          f"лоскут {tw}x{th} @ {res * 100:.0f} см/пикс")

    ref_video = next(iter((ROOT / "data/drive").rglob(ref_name + ".MP4")))
    rp = cached(dem, g, nrm, eu, ev, frame, ref_name, ref_video)
    if rp is None:
        sys.exit("опорный лоскут пуст")
    cv2.imwrite(str(QC_DIR / "wall_ref.jpg"), rp[0])

    rows = []
    for name, dmin in cand:
        if name == ref_name:
            rows.append((name, 0.0, 0.0, 1, 999))
            continue
        video = next(iter((ROOT / "data/drive").rglob(name + ".MP4")), None)
        if video is None:
            continue
        mp = cached(dem, g, nrm, eu, ev, frame, name, video)
        if mp is None:
            print(f"  {name}: лоскут пуст (дист {dmin} м)")
            continue
        got = masked_shift(rp, mp)
        if not got:
            print(f"  {name}: мало согласных пар")
            continue
        dx, dy, nin = got
        # сдвиг в плоскости стены -> метры С/В (вклад вдоль нормали нулевой)
        d3 = dx * res * eu + dy * res * ev
        de, dn = float(d3[0]), float(d3[1])
        if math.hypot(de, dn) > MAX_SHIFT_M:
            print(f"  {name}: отклонено (|d| {math.hypot(de, dn):.1f} м)")
            continue
        c0, c1 = ncc(rp, mp), ncc(rp, mp, dx, dy)
        if c0 is None or c1 is None or c1 < c0 + 0.03 or c1 < 0.15:
            print(f"  {name}: отклонено гейтом "
                  f"(ncc {c0 if c0 is not None else -9:.2f} -> "
                  f"{c1 if c1 is not None else -9:.2f})")
            continue
        rows.append((name, dn, de, 1, nin))
        cv2.imwrite(str(QC_DIR / f"wall_{name}.jpg"), np.hstack([rp[0], mp[0]]),
                    [cv2.IMWRITE_JPEG_QUALITY, 80])
        print(f"  {name}: dn {dn:+.1f} м, de {de:+.1f} м (пар {nin}, "
              f"ncc {c0:.2f} -> {c1:.2f})")

    with OUT_TSV.open("w") as f:
        f.write("video\tdn_cam_m\tde_cam_m\tzones\tinliers\n")
        for name, dn, de, nz, nin in rows:
            f.write(f"{name}\t{-dn:.2f}\t{-de:.2f}\t{nz}\t{nin}\n")
    print(f"{OUT_TSV.name}: поправок {len(rows)}")


if __name__ == "__main__":
    main()
