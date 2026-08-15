#!/usr/bin/env python3
"""Зонд аномалий на SUPR-тайлах: два канала с физическими приорами.

Канал A «тёмное на снегу»: изолированный тёмный блоб площадью 0.12–2.0 м²
(масштаб через GSD тайла). «Тёмное» — относительно яркости снега самого
тайла; блоб окружён преимущественно снегом, примыкающая скала — мелкая.

Канал B «цветовой выброс»: кластеры 3 см–0.5 м, чья цветность (a*, b*)
— робастный выброс против распределения самого тайла (Махаланобис по
медиане/ковариации с отсечкой хвостов). Никаких зашитых цветов — палитра
«скала + снег + тени» оценивается по кадру.

GSD: f = 15700 px (docs/focal-length-calibration.md), дистанция — LRF тайла;
при TooFar — медиана LRF тайлов панорамы.

Запуск: analysis/.venv/bin/python analysis/snow_anomaly_probe.py <тайлы...>
        [--out DIR] [--top N] [--maha K]
Выход: TSV в stdout (файл, x, y, канал, площадь_м², скор, gsd_см) и кропы.
"""

import argparse
import glob
import math
import os
import re
import sys

import cv2
import numpy as np

F_PX = 15700.0


def xmp(path):
    raw = open(path, "rb").read(400000)
    m = re.search(rb"<x:xmpmeta.*?</x:xmpmeta>", raw, re.S)
    if not m:
        return {}
    x = m.group(0).decode("utf-8", "replace")
    out = {}
    for k in ("LRFStatus", "LRFTargetDistance"):
        mm = re.search(rf'drone-dji:{k}="([^"]*)"', x)
        out[k] = mm.group(1) if mm else None
    return out


def pano_median_dist(folder):
    ds = []
    for p in glob.glob(os.path.join(folder, "*_SUPR.JPG")):
        meta = xmp(p)
        try:
            d = float(meta.get("LRFTargetDistance") or 0)
        except ValueError:
            d = 0
        if meta.get("LRFStatus") == "Normal" and d > 1:
            ds.append(d)
    return float(np.median(ds)) if ds else 210.0


def tile_gsd(path, pano_median):
    meta = xmp(path)
    try:
        d = float(meta.get("LRFTargetDistance") or 0)
    except ValueError:
        d = 0
    if meta.get("LRFStatus") != "Normal" or d <= 1:
        d = pano_median
    return d / F_PX


def channel_dark_on_snow(bgr, gsd):
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    snow = ((g > 165) & (hsv[:, :, 1] < 60))
    if snow.sum() < 1000:
        return []
    snow_med = float(np.median(g[snow]))
    dark = (g < 0.62 * snow_med).astype(np.uint8)
    snow = snow.astype(np.uint8)

    px_m2 = 1.0 / (gsd * gsd)
    a_min, a_max = 0.12 * px_m2, 2.0 * px_m2
    big_rock = 4.0 * px_m2

    n, lab, stats, cent = cv2.connectedComponentsWithStats(dark, 8)
    areas = stats[:, 4]
    ring_r = max(3, int(0.6 / gsd))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * ring_r + 1,) * 2)
    hits = []
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if not (a_min <= a <= a_max):
            continue
        if max(w, h) / max(1, min(w, h)) > 5:
            continue
        pad = ring_r + 2
        y0, y1 = max(0, y - pad), min(bgr.shape[0], y + h + pad)
        x0, x1 = max(0, x - pad), min(bgr.shape[1], x + w + pad)
        sub = lab[y0:y1, x0:x1]
        comp = (sub == i).astype(np.uint8)
        ring = (cv2.dilate(comp, k) > 0) & (comp == 0)
        if ring.sum() == 0:
            continue
        snow_fr = snow[y0:y1, x0:x1][ring].mean()
        if snow_fr < 0.45:
            continue
        # непосредственная граница блоба: должен стоять в снегу,
        # а не быть выступом скального массива
        b3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        edge = (cv2.dilate(comp, b3) > 0) & (comp == 0)
        edge_snow = snow[y0:y1, x0:x1][edge].mean()
        edge_dark = (sub[edge] > 0).mean()
        if edge_snow < 0.45 or edge_dark > 0.35:
            continue
        cx, cy = int(cent[i][0]), int(cent[i][1])
        a_m2 = a * gsd * gsd
        plateau = 1.0 if 0.15 <= a_m2 <= 1.5 else 0.6
        score = (0.5 * float(snow_fr) + 0.5 * float(edge_snow)) * plateau
        hits.append((cx, cy, "dark_on_snow", a_m2, score))
    return hits


def channel_color_outlier(bgr, gsd, maha_k):
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    edges = (cv2.magnitude(gx, gy) > 220).astype(np.uint8)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    A = lab[:, :, 1] - 128.0
    B = lab[:, :, 2] - 128.0
    # робастная статистика палитры тайла по подвыборке
    step = max(1, bgr.shape[0] * bgr.shape[1] // 400000)
    sa = A.reshape(-1)[::step]
    sb = B.reshape(-1)[::step]
    med = np.array([np.median(sa), np.median(sb)])
    # отсечка хвостов, чтобы сами аномалии не раздували ковариацию
    da, db = sa - med[0], sb - med[1]
    r = np.hypot(da, db)
    keep = r < np.percentile(r, 98)
    cov = np.cov(np.vstack([da[keep], db[keep]]))
    cov += np.eye(2) * 0.5
    icov = np.linalg.inv(cov)

    dA, dB = A - med[0], B - med[1]
    m2 = icov[0, 0] * dA * dA + 2 * icov[0, 1] * dA * dB + icov[1, 1] * dB * dB
    mask = (m2 > maha_k * maha_k).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    a_min = max(4, int((0.03 / gsd) ** 2))   # ≥ ~3×3 см
    a_max = int((0.5 / gsd) ** 2)            # ≤ ~0.5×0.5 м
    n, labc, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
    hits = []
    md = np.sqrt(np.maximum(m2, 0))
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if not (a_min <= a <= a_max):
            continue
        sel = labc[y:y + h, x:x + w] == i
        # каёмка хроматической аберрации: тонкая, вытянутая, целиком на
        # ярком градиенте; компактное пятно, пересекающее границу, — нет
        edge_fr = float(edges[y:y + h, x:x + w][sel].mean())
        elong = max(w, h) / max(1, min(w, h))
        if edge_fr > 0.65 and elong > 2.5:
            continue
        if edge_fr > 0.9:
            continue
        mm = float(md[y:y + h, x:x + w][sel].mean())
        sc = (mm / maha_k) * min(1.0, math.sqrt(a / 12.0))
        cx, cy = int(cent[i][0]), int(cent[i][1])
        hits.append((cx, cy, "color", a * gsd * gsd, sc))
    return hits


def dedup(hits, r=40):
    hits = sorted(hits, key=lambda h: -h[4])
    out = []
    for h in hits:
        if all(math.hypot(h[0] - o[0], h[1] - o[1]) > r for o in out):
            out.append(h)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tiles", nargs="+")
    ap.add_argument("--out", default=None)
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--maha", type=float, default=6.0)
    args = ap.parse_args()

    med_cache = {}
    for path in args.tiles:
        folder = os.path.dirname(path)
        if folder not in med_cache:
            med_cache[folder] = pano_median_dist(folder)
        gsd = tile_gsd(path, med_cache[folder])
        bgr = cv2.imread(path)
        if bgr is None:
            continue
        hd = dedup(channel_dark_on_snow(bgr, gsd))
        hc = dedup(channel_color_outlier(bgr, gsd, args.maha))
        fuse_r = 0.5 / gsd
        fused = []
        for cx, cy, ch, a, sc in hd:
            near = [c for c in hc
                    if math.hypot(c[0] - cx, c[1] - cy) < fuse_r]
            if near:
                sc = sc + 1.0 + max(c[4] for c in near)
                ch = "dark+color"
            fused.append((cx, cy, ch, a, sc))
        hits = sorted(fused, key=lambda h: -h[4])[: args.top] + \
            hc[: args.top]
        name = os.path.basename(path)
        for cx, cy, ch, area, score in hits:
            print(f"{name}\t{cx}\t{cy}\t{ch}\t{area:.3f}\t{score:.2f}\t{gsd*100:.1f}")
            if args.out:
                os.makedirs(args.out, exist_ok=True)
                c = bgr[max(0, cy - 60):cy + 60, max(0, cx - 60):cx + 60]
                cv2.imwrite(os.path.join(
                    args.out, f"{name[:-4]}_{cx}_{cy}_{ch}.jpg"),
                    cv2.resize(c, None, fx=4, fy=4,
                               interpolation=cv2.INTER_LANCZOS4))
        sys.stdout.flush()


if __name__ == "__main__":
    main()
