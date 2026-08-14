#!/usr/bin/env python3
"""Чистовое полотно: геопривязка мозаики и отрисовка отметок вещей и изолиний.

Геопривязка без знания фокусного: центральный луч каждого кадра (позиция дрона +
углы подвеса из сайдкара) трассируется в рельеф (geoproject.cast), это даёт пары
«точка мира ↔ точка холста» по всем кадрам склейки; по ним RANSAC'ом подгоняется
гомография мир→холст. Дальше отметки (findings-marks.tsv) и изолинии DEM
проецируются этой гомографией.

Точность: склон не плоскость, цепочка склейки дрейфует, у cast вилка DEM ±30 м —
подгонка приближённая. Скрипт печатает медианный остаток в метрах; отметки на
полотне — «где искать глазами», не координаты для штаба.

Использование (после merge_mosaics.py):
  .venv/bin/python annotate_mosaic.py --merged merged/ \
      --videos ../data/drive/2026-08-13/drone-part3 [--step 25] [--marks ...]

Либо для одиночной мозаики stitch.py: --mosaic stitch/<видео>/mosaic_NN.jpg
(эквивалент merged из одного полотна). Выход: annotated.jpg (+ _preview.jpg)
рядом с merged.jpg / мозаикой.
"""

import argparse
import csv
import math
from pathlib import Path

import cv2
import numpy as np
from matplotlib import pyplot as plt

from geoproject import Dem, cast, load_rows, at, ray_dir
from stitch import PREVIEW_MAX

M_PER_DEG_LAT = 111132.0


def read_tsv(path: Path):
    with path.open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(
            (ln for ln in f if not ln.startswith("#")), delimiter="\t")]
    return rows


def mosaic_list(args):
    """[(путь мозаики, H мозаика→холст)] и путь выходного файла."""
    if args.merged:
        rows = read_tsv(args.merged / "mosaics.tsv")
        items = [(Path(r["mosaic"]),
                  np.float64([float(r[f"h{i}{j}"]) for i in range(3)
                              for j in range(3)]).reshape(3, 3))
                 for r in rows]
        return items, args.merged / "merged.jpg", args.merged / "annotated.jpg"
    return ([(args.mosaic, np.eye(3))], args.mosaic,
            args.mosaic.with_name(args.mosaic.stem + "_annotated.jpg"))


def ground_points(mosaic: Path, H, videos_dir: Path, dem: Dem):
    """Пары (мир E/N относительно точки отсчёта пока в градусах) ↔ (холст x, y)."""
    frames = mosaic.parent / ("frames_" + mosaic.stem.split("_")[1] + ".tsv")
    video = videos_dir / (mosaic.parent.name + ".MP4")
    rows = load_rows(video)
    out = []
    for r in read_tsv(frames):
        t = float(r["t"])
        try:
            drone = (at(rows, t, "lat"), at(rows, t, "lon"), at(rows, t, "alt_m"))
            yaw, pitch = at(rows, t, "gb_yaw"), at(rows, t, "gb_pitch")
        except (KeyError, ValueError):
            continue
        if pitch > -3:          # луч у горизонта — пересечение ненадёжно
            continue
        hit = cast(dem, *drone, ray_dir(yaw, pitch, 0, 0, 0, 0, 1000))
        if hit is None:
            continue
        la, lo, ground, _ = hit
        p = cv2.perspectiveTransform(
            np.float64([[float(r["canvas_x"]), float(r["canvas_y"])]]
                       ).reshape(-1, 1, 2), H).ravel()
        out.append((la, lo, ground, p[0], p[1]))
    return out


def fit_world_to_canvas(pts, thr_px):
    """Гомография (E,N м)→(x,y холста) по парам центров кадров, RANSAC.

    None — привязка не сошлась (мало точек: дрон висел вплотную к склону и
    лучи не пересекают DEM); полотно тогда размечается только пиксельными
    якорями, без изолиний и гео-отметок.
    """
    if len(pts) < 8:
        return None
    lat0 = float(np.median([p[0] for p in pts]))
    lon0 = float(np.median([p[1] for p in pts]))
    m_lon = 111320.0 * math.cos(math.radians(lat0))

    def enu(la, lo):
        return (lo - lon0) * m_lon, (la - lat0) * M_PER_DEG_LAT

    world = np.float64([enu(p[0], p[1]) for p in pts]).reshape(-1, 1, 2)
    canvas = np.float64([(p[3], p[4]) for p in pts]).reshape(-1, 1, 2)
    H, mask = cv2.findHomography(world, canvas, cv2.RANSAC, thr_px)
    if H is None:
        return None
    inl = mask.ravel().astype(bool)
    if inl.sum() < 12:      # вырожденная подгонка: изолинии по ней — мусор
        return None
    proj = cv2.perspectiveTransform(world[inl], H)
    res_px = np.linalg.norm((proj - canvas[inl]).reshape(-1, 2), axis=1)
    # локальный масштаб (м/пикс) — по якорной точке облака
    c = world[inl].reshape(-1, 2).mean(0)
    step = np.float64([[c[0], c[1]], [c[0] + 1, c[1]]]).reshape(-1, 1, 2)
    a, b = cv2.perspectiveTransform(step, H).reshape(-1, 2)
    m_per_px = 1.0 / max(np.linalg.norm(b - a), 1e-9)
    print(f"геопривязка: точек {len(pts)}, инлайеров {int(inl.sum())}, "
          f"медианный остаток {np.median(res_px):.0f} px "
          f"≈ {np.median(res_px) * m_per_px:.0f} м, масштаб {m_per_px * 100:.0f} см/px")
    return H, enu, (lat0, lon0), world[inl].reshape(-1, 2), m_per_px


def sample_dem_grid(dem: Dem, lat0, lon0, world_x, world_y):
    """Высоты DEM на сетке локальных метров (векторно, билинейно)."""
    m_lon = 111320.0 * math.cos(math.radians(lat0))
    lon = lon0 + world_x / m_lon
    lat = lat0 + world_y / M_PER_DEG_LAT
    x = (lon - dem.lon0) / dem.dlon
    y = (dem.lat0 - lat) / dem.dlat
    x = np.clip(x, 0, dem.w - 2)
    y = np.clip(y, 0, dem.h - 2)
    x0 = x.astype(int)
    y0 = y.astype(int)
    fx, fy = x - x0, y - y0
    z = dem.z
    return (z[y0, x0] * (1 - fx) * (1 - fy) + z[y0, x0 + 1] * fx * (1 - fy)
            + z[y0 + 1, x0] * (1 - fx) * fy + z[y0 + 1, x0 + 1] * fx * fy)


def annotate(args):
    dem = Dem()
    items, base_img_path, out_path = mosaic_list(args)
    pts = []
    for mosaic, H in items:
        got = ground_points(mosaic, H, args.videos, dem)
        pts += got
        print(f"{mosaic.parent.name}/{mosaic.name}: центров кадров с рельефом {len(got)}")
    img = cv2.imread(str(base_img_path))
    ch, cw = img.shape[:2]
    thr = max(0.02 * max(cw, ch), 60)
    geo = fit_world_to_canvas(pts, thr)
    if geo is None:
        print("геопривязка не сошлась — только пиксельные отметки, "
              "без изолиний и масштаба")
    else:
        H, enu, (lat0, lon0), world_inl, m_per_px = geo
    filled = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) > 4

    fs = max(cw, ch) / 3000            # базовый масштаб шрифта/толщин
    lw = max(2, round(2 * fs))

    # --- изолинии ---
    contours = []
    if geo is not None:
        pad = 0.25
        x0, y0 = world_inl.min(0)
        x1, y1 = world_inl.max(0)
        x0, x1 = x0 - (x1 - x0) * pad, x1 + (x1 - x0) * pad
        y0, y1 = y0 - (y1 - y0) * pad, y1 + (y1 - y0) * pad
        n = 240
        gx, gy = np.meshgrid(np.linspace(x0, x1, n), np.linspace(y0, y1, n))
        gz = sample_dem_grid(dem, lat0, lon0, gx, gy)
        levels = np.arange(math.floor(gz.min() / args.step) * args.step,
                           gz.max() + args.step, args.step)
        cs = plt.contour(gx, gy, gz, levels=levels)
        contours = list(zip(cs.levels, cs.allsegs))
    for level, segs in contours:
        for seg in segs:
            if len(seg) < 2:
                continue
            p = cv2.perspectiveTransform(
                seg.reshape(-1, 1, 2).astype(np.float64), H).reshape(-1, 2)
            keep = ((p[:, 0] >= 0) & (p[:, 0] < cw)
                    & (p[:, 1] >= 0) & (p[:, 1] < ch))
            keep &= filled[np.clip(p[:, 1].astype(int), 0, ch - 1),
                           np.clip(p[:, 0].astype(int), 0, cw - 1)]
            # рисуем кусками, чтобы линия не тянулась через пустоты холста
            runs = np.split(np.where(keep)[0],
                            np.where(np.diff(np.where(keep)[0]) > 1)[0] + 1)
            longest = None
            for run in runs:
                if len(run) < 2:
                    continue
                poly = p[run].astype(np.int32).reshape(-1, 1, 2)
                cv2.polylines(img, [poly], False, (0, 0, 0), lw + 2,
                              cv2.LINE_AA)
                cv2.polylines(img, [poly], False, (210, 235, 255), lw,
                              cv2.LINE_AA)
                if longest is None or len(run) > len(longest):
                    longest = run
            if longest is not None and len(longest) > 30:
                mx, my = p[longest[len(longest) // 2]]
                txt = f"{level:.0f}"
                cv2.putText(img, txt, (int(mx), int(my) - round(4 * fs)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9 * fs, (0, 0, 0),
                            lw + 2, cv2.LINE_AA)
                cv2.putText(img, txt, (int(mx), int(my) - round(4 * fs)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9 * fs,
                            (210, 235, 255), lw, cv2.LINE_AA)
    plt.close("all")

    # --- отметки вещей ---
    by_name = {str(m): Hm for m, Hm in items}
    label_boxes = []      # занятые прямоугольники подписей — разводим коллизии
    for r in read_tsv(args.marks):
        anchor = by_name.get(r["mosaic"]) if r.get("mosaic") else None
        if r.get("mosaic") and anchor is None:
            print(f"отметка {r['label']}: полотно {r['mosaic']} не в сшивке, "
                  f"ставлю по геопривязке")
        if anchor is not None:
            p = cv2.perspectiveTransform(
                np.float64([[float(r["mx"]), float(r["my"])]]
                           ).reshape(-1, 1, 2), anchor).ravel()
        else:
            if geo is None:
                continue
            e, nn_ = enu(float(r["lat"]), float(r["lon"]))
            # вне облака привязки гомография экстраполирует непредсказуемо —
            # такие отметки не рисуем вовсе, чтобы не ставить ложных меток
            lo_w = world_inl.min(0)
            hi_w = world_inl.max(0)
            margin = 0.5 * (hi_w - lo_w) + 20
            if not ((lo_w - margin <= [e, nn_]) & ([e, nn_] <= hi_w + margin)).all():
                print(f"отметка вне участка привязки (пропущена): {r['label']}")
                continue
            p = cv2.perspectiveTransform(
                np.float64([[e, nn_]]).reshape(-1, 1, 2), H).ravel()
        x, y = int(p[0]), int(p[1])
        if not (0 <= x < cw and 0 <= y < ch) or not filled[y, x]:
            print(f"отметка вне полотна: {r['label']}")
            continue
        rad = round(14 * fs)
        col = (0, 220, 255)
        cv2.circle(img, (x, y), rad, (0, 0, 0), lw + 2, cv2.LINE_AA)
        cv2.circle(img, (x, y), rad, col, lw, cv2.LINE_AA)
        label = ("≈ " if r.get("approx") == "1" else "") + r["label"]
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_COMPLEX,
                                      1.1 * fs, lw)
        tx, ty = x + rad + round(6 * fs), y - rad
        while any(tx < bx1 and tx + tw > bx0 and ty - th < by1 and ty > by0
                  for bx0, by0, bx1, by1 in label_boxes):
            ty += round(th * 1.8)
        label_boxes.append((tx, ty - th, tx + tw, ty))
        cv2.line(img, (x + rad, y), (tx, ty - th // 2), (0, 0, 0), lw + 2,
                 cv2.LINE_AA)
        cv2.line(img, (x + rad, y), (tx, ty - th // 2), col, max(1, lw - 1),
                 cv2.LINE_AA)
        cv2.putText(img, label, (tx, ty), cv2.FONT_HERSHEY_COMPLEX,
                    1.1 * fs, (0, 0, 0), lw + 3, cv2.LINE_AA)
        cv2.putText(img, label, (tx, ty), cv2.FONT_HERSHEY_COMPLEX,
                    1.1 * fs, col, lw, cv2.LINE_AA)

    # --- масштабная линейка 100 м ---
    if geo is not None:
        bar = round(100 / m_per_px)
        bx, by = round(0.03 * cw), ch - round(0.03 * ch)
        cv2.line(img, (bx, by), (bx + bar, by), (0, 0, 0), lw + 3, cv2.LINE_AA)
        cv2.line(img, (bx, by), (bx + bar, by), (255, 255, 255), lw, cv2.LINE_AA)
        cv2.putText(img, "100 m (приблизительно)", (bx, by - round(8 * fs)),
                    cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), lw,
                    cv2.LINE_AA)

    cv2.imwrite(str(out_path), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if max(cw, ch) > PREVIEW_MAX:
        k = PREVIEW_MAX / max(cw, ch)
        cv2.imwrite(str(out_path.with_name(out_path.stem + "_preview.jpg")),
                    cv2.resize(img, (round(cw * k), round(ch * k))),
                    [cv2.IMWRITE_JPEG_QUALITY, 90])
    print(f"записано: {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--merged", type=Path, help="папка с merged.jpg и mosaics.tsv")
    g.add_argument("--mosaic", type=Path, help="одиночная мозаика stitch.py")
    ap.add_argument("--videos", type=Path, required=True,
                    help="папка с исходными видео и сайдкарами .gps.tsv")
    ap.add_argument("--marks", type=Path,
                    default=Path(__file__).parent / "findings-marks.tsv")
    ap.add_argument("--step", type=float, default=25.0,
                    help="шаг изолиний, м")
    args = ap.parse_args()
    annotate(args)
