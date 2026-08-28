#!/usr/bin/env python3
"""Пер-ролик поправка привязки орто-мозаики (только для build_ortho).

Привязка роликов держится на GPS/компасе дрона; у каждого ролика своя ошибка
7-30 м, из-за чего один предмет ложится в мозаику несколько раз (дубли
спальника, лоскутные швы). Здесь ролики выравниваются ДРУГ К ДРУГУ:

  1. каждый ролик-кандидат рендерится в собственный орто-лоскут опорных зон
     (кластер вещей и рюкзак) на фиксированном зуме;
  2. лоскут сшивается с лоскутом ОПОРНОГО ролика по особым точкам
     (SIFT + робастная медиана сдвига: свет и снег между днями меняются,
     фазовая корреляция целых лоскутов на этом ломается — проверено);
  3. медиана сдвигов по зонам -> поправка (dn, de) в метрах на ролик.

Опорный ролик — дневной вылет 15.08: по замеру относительно штабной
триангуляции синего рюкзака он ложится в ~7 м (docs/video-analysis.md,
«Точность привязки»); его абсолютная ошибка остаётся общей для всех — обои
для ориентировки, координаты по-прежнему только по исходникам и реестру.

Выход: analysis/coverage/ortho-anchors.tsv (video, dn_m, de_m, зоны, отклик)
       + QC-пары лоскутов в analysis/pilot/anchor-qc/.
Потребитель: build_ortho.py (сдвигает позу камеры ролика при укладке).
Расчётный конвейер координат (geoproject, реестр, coverage, flight_cache)
эти поправки НЕ использует.

Запуск: analysis/.venv/bin/python analysis/align_ortho_anchors.py
"""
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_ortho as B  # noqa: E402
from geoproject import Dem  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT_TSV = ROOT / "analysis/coverage/ortho-anchors.tsv"
QC_DIR = ROOT / "analysis/pilot/anchor-qc"
FLIGHTS = ROOT / "analysis/viewer/flights"
CACHE = ROOT / "analysis/ortho-work/anchor-patches"   # рендер лоскута долгий

REF = "DJI_20260815120151_0001_Z"   # заменяется автоподбором, см. pick_ref()
GZ = 20                             # ~5.8 см/пикс: хватает для сдвигов в метры
STEP_S = 2.0                        # шаг кадров рендера лоскута
# опорные зоны: кластер вещей (спальник/палки/крышка) и синий рюкзак
ZONES = [("veshchi", 39.483125, 73.585391, 55.0),
         ("ryukzak", 39.482656, 73.586792, 55.0)]
MIN_OVERLAP = 8000        # px совместной маски, меньше — зона не считается
MIN_INLIERS = 12          # согласных SIFT-пар, меньше — сшивка не удалась
MAX_SHIFT_M = 45.0        # больший сдвиг — заведомо ложная сшивка


def zone_px(lat, lon, half_m):
    """Рамка зоны в глобальных мерк-пикселях сетки GZ: (x0, y0, w, h, res)."""
    res = B.merc_res(lat, GZ)
    half = int(half_m / res)
    cx, cy = B.merc_px(lat, lon, GZ)
    return int(cx) - half, int(cy) - half, 2 * half, 2 * half, res


def render_patch(dem, video, x0, y0, w, h):
    """Орто-лоскут одного ролика в рамке [x0..x0+w)x[y0..y0+h) сетки GZ.

    Повторяет укладку build_ortho.composite_frame, но зум фиксирован (GZ),
    холст один и лучший источник выбирается только среди кадров ЭТОГО ролика.
    """
    rows = B.load_rows(video)
    cov = B.load_cov(video)
    if not cov:
        return None
    bgr = np.zeros((h, w, 3), np.uint8)
    score = np.zeros((h, w), np.float32)
    cap = cv2.VideoCapture(str(video))
    dur = cap.get(cv2.CAP_PROP_FRAME_COUNT) / max(cap.get(cv2.CAP_PROP_FPS), 1)
    t = 0.0
    while t < dur:
        pose = [B.at(rows, t, k) for k in ("lat", "lon", "alt_m", "gb_yaw", "gb_pitch")]
        f_px = B.focal_at(cov, t)
        if not (all(math.isfinite(v) for v in pose) and f_px):
            t += STEP_S
            continue
        lat0, lon0, alt0, yaw, pitch = pose

        us = np.arange(0, B.W + 1, B.NODE_PX, dtype=np.float64)
        vs = np.arange(0, B.H + 1, B.NODE_PX, dtype=np.float64)
        uu, vv = np.meshgrid(us, vs)
        nv, nu = uu.shape
        ax = np.arctan2(uu.ravel() - B.W / 2, f_px)
        ay = np.arctan2(vv.ravel() - B.H / 2, f_px)
        yw = np.radians(yaw) + ax
        pt = np.radians(pitch) - ay
        ce = np.cos(pt)
        dirs = np.stack([np.sin(yw) * ce, np.cos(yw) * ce, np.sin(pt)], axis=1)
        lat, lon, dist = B.cast_vec(dem, lat0, lon0, alt0, dirs)
        gsd = dist / f_px
        ok = np.isfinite(dist) & (dist <= B.DIST_MAX) & (gsd <= B.GSD_MAX)
        if ok.sum() < 4:
            t += STEP_S
            continue
        mx, my = B.merc_px(lat, lon, GZ)
        # кадр не задевает рамку — дальше не считаем
        inz = ok & (mx >= x0 - 32) & (mx < x0 + w + 32) & (my >= y0 - 32) & (my < y0 + h + 32)
        if not inz.any():
            t += STEP_S
            continue
        gs = np.full(len(dirs), np.nan)
        gs[ok] = B.graze_sin(dem, lat[ok], lon[ok], dirs[ok])
        ok &= np.nan_to_num(gs) >= B.GRAZE_MIN
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        got, img = cap.read()
        if not got:
            t += STEP_S
            continue
        src = cv2.resize(img, (B.W, B.H)) if img.shape[1] != B.W else img
        pyr = [src]
        for _ in range(3):
            p = pyr[-1]
            pyr.append(cv2.resize(p, ((p.shape[1] + 1) // 2, (p.shape[0] + 1) // 2),
                                  interpolation=cv2.INTER_AREA))
        okg = ok.reshape(nv, nu)
        mxg, myg = mx.reshape(nv, nu), my.reshape(nv, nu)
        qsg = np.where(ok, 1.0 / np.maximum(gsd, 1e-6) ** 2 * np.nan_to_num(gs), 0.0
                       ).reshape(nv, nu)
        for j in range(nv - 1):
            for i in range(nu - 1):
                if not (okg[j, i] and okg[j, i + 1] and okg[j + 1, i + 1] and okg[j + 1, i]):
                    continue
                dst = np.array([[mxg[j, i], myg[j, i]], [mxg[j, i + 1], myg[j, i + 1]],
                                [mxg[j + 1, i + 1], myg[j + 1, i + 1]],
                                [mxg[j + 1, i], myg[j + 1, i]]], np.float64)
                if dst[:, 0].max() < x0 or dst[:, 0].min() >= x0 + w \
                        or dst[:, 1].max() < y0 or dst[:, 1].min() >= y0 + h:
                    continue
                e = np.linalg.norm(np.roll(dst, -1, 0) - dst, axis=1)
                if e.max() > B.EDGE_MAX_PX or e.max() < 1e-3:
                    continue
                short = min(e[0] + e[2], e[1] + e[3]) / 2
                if short < 1.2:
                    continue
                scale = short / B.NODE_PX
                lvl = (0 if scale >= 0.75
                       else min(3, max(0, int(round(-math.log2(max(scale, 1e-6)))))))
                sq = np.array([[uu[j, i], vv[j, i]], [uu[j, i + 1], vv[j, i]],
                               [uu[j, i + 1], vv[j + 1, i]], [uu[j, i], vv[j + 1, i]]],
                              np.float32)
                qs = float(qsg[j:j + 2, i:i + 2].mean())
                bx0 = max(int(np.floor(dst[:, 0].min())) - x0, 0)
                by0 = max(int(np.floor(dst[:, 1].min())) - y0, 0)
                bx1 = min(int(np.ceil(dst[:, 0].max())) - x0, w)
                by1 = min(int(np.ceil(dst[:, 1].max())) - y0, h)
                if bx0 >= bx1 or by0 >= by1:
                    continue
                local = dst - [x0 + bx0, y0 + by0]
                hm = cv2.getPerspectiveTransform(sq / (1 << lvl),
                                                 local.astype(np.float32))
                patch = cv2.warpPerspective(
                    pyr[lvl], hm, (bx1 - bx0, by1 - by0), flags=cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_REPLICATE)
                mask = np.zeros((by1 - by0, bx1 - bx0), np.uint8)
                cv2.fillPoly(mask, [np.round(local).astype(np.int32)], 255)
                sub_s = score[by0:by1, bx0:bx1]
                put = (mask > 0) & (qs > sub_s)
                if put.any():
                    bgr[by0:by1, bx0:bx1][put] = patch[put]
                    sub_s[put] = qs
        t += STEP_S
    cap.release()
    if (score > 0).sum() < MIN_OVERLAP:
        return None
    return bgr, score > 0


def cached_patch(dem, name, video, zname, x0, y0, w, h):
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"{name}_{zname}.npz"
    if p.exists():
        d = np.load(p)
        return (d["bgr"], d["mask"]) if d["ok"] else None
    got = render_patch(dem, video, x0, y0, w, h)
    if got:
        np.savez_compressed(p, ok=True, bgr=got[0], mask=got[1])
    else:
        np.savez_compressed(p, ok=False, bgr=np.zeros(1), mask=np.zeros(1))
    return got


def masked_shift(ref, mov):
    """Сдвиг (dx, dy) px контента mov относительно ref по особым точкам.

    SIFT в совместно отснятой области, ratio-тест, затем чистый перенос:
    медиана сдвигов пар + отсев несогласных (2×MAD). Возвращает
    (dx, dy, инлаеров); сдвиг положительный — контент mov смещён на
    восток/юг относительно ref (самотест в main).
    """
    if (ref[1] & mov[1]).sum() < MIN_OVERLAP:
        return None
    sift = cv2.SIFT_create(nfeatures=4000)
    ga = cv2.cvtColor(ref[0], cv2.COLOR_BGR2GRAY)
    gb = cv2.cvtColor(mov[0], cv2.COLOR_BGR2GRAY)
    ka, da = sift.detectAndCompute(ga, ref[1].astype(np.uint8) * 255)
    kb, db = sift.detectAndCompute(gb, mov[1].astype(np.uint8) * 255)
    if da is None or db is None or len(ka) < MIN_INLIERS or len(kb) < MIN_INLIERS:
        return None
    bf = cv2.BFMatcher(cv2.NORM_L2)
    pairs = bf.knnMatch(da, db, k=2)
    d = [(kb[m.trainIdx].pt[0] - ka[m.queryIdx].pt[0],
          kb[m.trainIdx].pt[1] - ka[m.queryIdx].pt[1])
         for m, n2 in pairs if m.distance < 0.75 * n2.distance]
    if len(d) < MIN_INLIERS:
        return None
    d = np.array(d)
    med = np.median(d, axis=0)
    mad = np.median(np.abs(d - med), axis=0) + 1.0
    inl = np.all(np.abs(d - med) <= 2.5 * mad, axis=1)
    if inl.sum() < MIN_INLIERS:
        return None
    dx, dy = np.median(d[inl], axis=0)
    return float(dx), float(dy), int(inl.sum())


def ncc(ref, mov, dx=0.0, dy=0.0):
    """Корреляция совместной области после сдвига mov на (-dx, -dy)."""
    h, w = ref[1].shape
    M = np.float32([[1, 0, -dx], [0, 1, -dy]])
    gb = cv2.warpAffine(cv2.cvtColor(mov[0], cv2.COLOR_BGR2GRAY), M, (w, h))
    mb = cv2.warpAffine(mov[1].astype(np.uint8), M, (w, h)) > 0
    m = ref[1] & mb
    if m.sum() < MIN_OVERLAP:
        return None
    ga = cv2.cvtColor(ref[0], cv2.COLOR_BGR2GRAY)
    a, b = ga[m].astype(np.float64), gb[m].astype(np.float64)
    a -= a.mean()
    b -= b.mean()
    den = math.sqrt(float((a * a).sum()) * float((b * b).sum()))
    return float((a * b).sum() / den) if den > 0 else None


def selftest():
    """Знак сдвига: контент, смещённый на +20 px по x, обязан дать dx=+20."""
    rng = np.random.default_rng(1)
    a = (rng.random((64, 64)) * 255).astype(np.uint8)
    a = cv2.resize(a, (512, 512), interpolation=cv2.INTER_CUBIC)
    b = np.roll(a, 20, axis=1)          # контент уехал на восток (+x)
    m = np.ones((512, 512), bool)
    got = masked_shift((cv2.cvtColor(a, cv2.COLOR_GRAY2BGR), m),
                       (cv2.cvtColor(b, cv2.COLOR_GRAY2BGR), m))
    assert got and abs(got[0] - 20) < 1 and abs(got[1]) < 1, f"самотест: {got}"


def candidates():
    """Ролики с близкой съёмкой опорных зон: [(имя, мин. дистанция центра)]."""
    out = {}
    for mp in sorted(FLIGHTS.glob("*/meta.json")):
        meta = json.loads(mp.read_text())
        best = None
        for s in meta["samples"]:
            c = s[8]
            if not c:
                continue
            for _, zla, zlo, half in ZONES:
                d = math.hypot((c[0] - zla) * 111132.0,
                               (c[1] - zlo) * 111320.0 * math.cos(math.radians(zla)))
                if d < half and c[2] < 220:
                    best = min(best, c[2]) if best is not None else c[2]
        if best is not None:
            out[mp.parent.name] = best
    return sorted(out.items(), key=lambda kv: kv[1])


def main():
    selftest()
    QC_DIR.mkdir(parents=True, exist_ok=True)
    dem = Dem()
    cand = candidates()
    names = [n for n, _ in cand]
    ref_name = REF if REF in names else next(
        (n for n, _ in cand if n.startswith("DJI_20260815")), names[0])
    print(f"кандидатов {len(cand)}, опорный: {ref_name}")

    zones_px = [zone_px(la, lo, half) for _, la, lo, half in ZONES]
    ref_video = next(iter((ROOT / "data/drive").rglob(ref_name + ".MP4")))
    ref_patches = []
    for (zname, *_), (x0, y0, w, h, res) in zip(ZONES, zones_px):
        p = cached_patch(dem, ref_name, ref_video, zname, x0, y0, w, h)
        ref_patches.append(p)
        if p:
            cv2.imwrite(str(QC_DIR / f"ref_{zname}.jpg"), p[0])
        print(f"  опорный лоскут {zname}: {'ok' if p else 'пусто'}")

    rows = []
    for name, dmin in cand:
        if name == ref_name:
            rows.append((name, 0.0, 0.0, len([p for p in ref_patches if p]), 1.0))
            continue
        video = next(iter((ROOT / "data/drive").rglob(name + ".MP4")), None)
        if video is None:
            continue
        shifts = []
        for (zname, zla, *_), (x0, y0, w, h, res), rp in zip(ZONES, zones_px, ref_patches):
            if rp is None:
                continue
            mp = cached_patch(dem, name, video, zname, x0, y0, w, h)
            if mp is None:
                continue
            got = masked_shift(rp, mp)
            if not got:
                print(f"  {name} {zname}: мало согласных пар")
                continue
            dx, dy, nin = got
            de, dn = dx * res, -dy * res      # мерк-y растёт на юг
            if math.hypot(de, dn) > MAX_SHIFT_M:
                print(f"  {name} {zname}: отклонено (|d| {math.hypot(de, dn):.1f} м)")
                continue
            # гейт честности: сдвиг обязан заметно улучшить корреляцию
            # совместной области, иначе сшивка случайная
            c0, c1 = ncc(rp, mp), ncc(rp, mp, dx, dy)
            if c0 is None or c1 is None or c1 < c0 + 0.03 or c1 < 0.15:
                print(f"  {name} {zname}: отклонено гейтом "
                      f"(ncc {c0 if c0 is not None else -9:.2f} -> "
                      f"{c1 if c1 is not None else -9:.2f})")
                continue
            shifts.append((dn, de, nin))
            side = np.hstack([rp[0], mp[0]])
            cv2.imwrite(str(QC_DIR / f"{name}_{zname}.jpg"), side,
                        [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not shifts:
            print(f"  {name}: сшивка не удалась (дист {dmin} м)")
            continue
        dn = float(np.median([s[0] for s in shifts]))
        de = float(np.median([s[1] for s in shifts]))
        nin = int(sum(s[2] for s in shifts))
        rows.append((name, dn, de, len(shifts), nin))
        print(f"  {name}: dn {dn:+.1f} м, de {de:+.1f} м "
              f"(зон {len(shifts)}, пар {nin})")

    # поправка двигает КОНТЕНТ ролика к опорному: контент уехал на de/dn ->
    # камеру сдвигаем на -de/-dn
    with OUT_TSV.open("w") as f:
        f.write("video\tdn_cam_m\tde_cam_m\tzones\tinliers\n")
        for name, dn, de, nz, nin in rows:
            f.write(f"{name}\t{-dn:.2f}\t{-de:.2f}\t{nz}\t{nin}\n")
    print(f"{OUT_TSV.name}: поправок {len(rows)}")


if __name__ == "__main__":
    main()
