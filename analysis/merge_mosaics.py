#!/usr/bin/env python3
"""Сшивка полотен разных роликов (выход stitch.py) в общее полотно участка.

Первое полотно списка — якорь: его масштаб и ориентация задают холст. Остальные
пришиваются по общим точкам (SIFT на уменьшенных копиях, гомография RANSAC):
каждое — либо напрямую к якорю, либо к уже пришитому полотну (перебор в порядке
списка, берётся связка с максимумом инлайеров). Не нашедшие связки полотна
пропускаются с пометкой в выводе.

Использование:
  .venv/bin/python merge_mosaics.py --out merged/ ЯКОРЬ.jpg ПОЛОТНО2.jpg ...

Выход в --out: merged.jpg (+ merged_preview.jpg), mosaics.tsv — гомография
каждого полотна на общий холст (для переноса отметок, см. annotate_mosaic.py).
С --register-only рендера нет, только mosaics.tsv: гигапиксельные холсты
(--scale N растит холст против масштаба якоря) рендерит tile_pano.py.
"""

import argparse
import math
from pathlib import Path

import cv2
import numpy as np

from stitch import PREVIEW_MAX, feather

WORK_MAX = 3000        # сторона уменьшенной копии для поиска общих точек
MIN_INLIERS = 60       # полотна большие, случайных совпадений много — порог выше


def load_small(path: Path):
    img = cv2.imread(str(path))
    k = min(1.0, WORK_MAX / max(img.shape[:2]))
    small = cv2.resize(img, None, fx=k, fy=k) if k < 1.0 else img
    return img, small, k


def pair_homography(sift, matcher, feat_a, feat_b):
    """Гомография b→a на уменьшенных копиях, (H, inliers)."""
    kp_a, des_a = feat_a
    kp_b, des_b = feat_b
    if des_a is None or des_b is None:
        return None, 0
    pairs = matcher.knnMatch(des_b, des_a, k=2)
    good = [m for m, nn in (p for p in pairs if len(p) == 2)
            if m.distance < 0.75 * nn.distance]
    if len(good) < MIN_INLIERS:
        return None, len(good)
    src = np.float32([kp_b[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp_a[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    inl = int(mask.sum()) if mask is not None else 0
    return (H, inl) if H is not None and inl >= MIN_INLIERS else (None, inl)


def merge(paths, out: Path, max_canvas: int, scale_up: float = 1.0,
          register_only: bool = False):
    out.mkdir(parents=True, exist_ok=True)
    sift = cv2.SIFT_create(nfeatures=12000)
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    items = []
    for p in paths:
        img, small, k = load_small(p)
        # чёрный фон холстов даёт ложные точки на границах — маской его убираем
        mask = (cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) > 4).astype(np.uint8)
        mask = cv2.erode(mask, np.ones((9, 9), np.uint8))
        feat = sift.detectAndCompute(small, mask)
        items.append(dict(path=p, img=img, small_k=k, feat=feat, G=None))
        print(f"{p.name}: {img.shape[1]}x{img.shape[0]}, точек {len(feat[0])}")

    # G — гомография полного полотна на холст якоря; --scale растит холст
    # относительно масштаба якоря, чтобы не терять детализацию крупных планов
    items[0]["G"] = np.diag([scale_up, scale_up, 1.0])
    placed = [items[0]]
    changed = True
    while changed:
        changed = False
        for it in items:
            if it["G"] is not None:
                continue
            best = None
            for base in placed:
                H, inl = pair_homography(sift, matcher, base["feat"], it["feat"])
                if H is not None and (best is None or inl > best[2]):
                    best = (base, H, inl)
            if best is None:
                continue
            base, H, inl = best
            # H действует на уменьшенных копиях; переводим в полные пиксели
            Sb = np.diag([1 / base["small_k"], 1 / base["small_k"], 1.0])
            Si = np.diag([it["small_k"], it["small_k"], 1.0])
            it["G"] = base["G"] @ Sb @ H @ Si
            placed.append(it)
            changed = True
            print(f"  {it['path'].name} → {base['path'].name}: инлайеров {inl}")
    skipped = [it["path"].name for it in items if it["G"] is None]
    if skipped:
        print(f"не пришиты (нет общих точек): {', '.join(skipped)}")

    pts = []
    for it in placed:
        h, w = it["img"].shape[:2]
        corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
        pts.append(cv2.perspectiveTransform(corners, it["G"]))
    pts = np.concatenate(pts).reshape(-1, 2)
    x0, y0 = pts.min(0)
    x1, y1 = pts.max(0)
    scale = min(1.0, max_canvas / max(x1 - x0, y1 - y0))
    T = np.diag([scale, scale, 1.0]) @ np.array(
        [[1, 0, -x0], [0, 1, -y0], [0, 0, 1.0]])
    cw, ch = math.ceil((x1 - x0) * scale), math.ceil((y1 - y0) * scale)

    with (out / "mosaics.tsv").open("w", encoding="utf-8") as f:
        f.write("mosaic\t" + "\t".join(f"h{i}{j}" for i in range(3)
                                       for j in range(3)) + "\n")
        for it in placed:
            M = (T @ it["G"]).astype(np.float64)
            f.write(str(it["path"]) + "\t"
                    + "\t".join(f"{v:.8g}" for v in M.ravel()) + "\n")
    print(f"merged: {cw}x{ch}, полотен {len(placed)} из {len(items)}")
    if register_only:
        return

    canvas = np.zeros((ch, cw, 3), np.uint8)
    filled = np.zeros((ch, cw), bool)
    for it in placed:
        h, w = it["img"].shape[:2]
        M = (T @ it["G"]).astype(np.float64)
        warped = cv2.warpPerspective(it["img"], M, (cw, ch))
        # чёрные поля исходного полотна не должны затирать уже уложенное
        content = (cv2.cvtColor(it["img"], cv2.COLOR_BGR2GRAY) > 4).astype(np.float32)
        a = cv2.warpPerspective(feather(w, h) * content, M, (cw, ch))
        hit = a > 1e-3
        a = np.where(hit & ~filled, 1.0, a)[..., None].astype(np.float32)
        canvas[hit] = (canvas[hit] * (1 - a[hit])
                       + warped[hit] * a[hit]).astype(np.uint8)
        filled |= hit

    cv2.imwrite(str(out / "merged.jpg"), canvas, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if max(cw, ch) > PREVIEW_MAX:
        k = PREVIEW_MAX / max(cw, ch)
        cv2.imwrite(str(out / "merged_preview.jpg"),
                    cv2.resize(canvas, (round(cw * k), round(ch * k))),
                    [cv2.IMWRITE_JPEG_QUALITY, 90])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mosaics", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--max-canvas", type=int, default=16000)
    ap.add_argument("--scale", type=float, default=1.0,
                    help="во сколько раз растить холст против масштаба якоря")
    ap.add_argument("--register-only", action="store_true",
                    help="только регистрация: mosaics.tsv без рендера холста "
                         "(для гигапанорам — рендерит tile_pano.py)")
    args = ap.parse_args()
    merge(args.mosaics, args.out, args.max_canvas, args.scale,
          args.register_only)
