#!/usr/bin/env python3
"""Отбор кадров участка для отсмотра следов (борозды, качение, удары).

Цветовой детектор следы не ловит (нет цветового контраста), алгоритмический
рельефный слой на эталонных бороздах не отделяется от фактуры снега
(кочки, заструги) — рабочий метод по следам: визуальный отсмотр полных кадров.
Скрипт готовит посильный набор: по телеметрии выбирает кадры, где центр кадра
попадает в заданный участок, дедуплицирует по точке съёмки и взгляду
(зависания не размножаются), выбирает резкий кадр из соседних, усиливает
локальный контраст (CLAHE) — слабые борозды читаются лучше.

Использование:
  .venv/bin/python trace_frames.py ВИДЕО.MP4 [ещё видео...] --out trace-frames/УЧАСТОК/
      [--lat0 39.4818 --lat1 39.4845 --lon0 73.5836 --lon1 73.5878]
      [--step 1.0] [--move-m 8] [--turn-deg 12]

Видео без телеметрии (вертолёт) фильтру по участку не поддаются — берутся
целиком с дедупликацией по сдвигу сцены (--no-telemetry-shift).

Выход в --out: <видео>_tNNNN.jpg (полный кадр с CLAHE) и frames.tsv:
видео, таймкод, GPS дрона, точка попадания центра кадра (cast в DEM),
дистанция, углы подвеса, фокусное из coverage-таблицы (если есть).
"""

import argparse
import csv
import math
from pathlib import Path

import cv2
import numpy as np

from geoproject import Dem, at, cast, load_rows, ray_dir

COVERAGE_DIR = Path(__file__).parent / "coverage"
CLAHE = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))


def enhance(img):
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2Lab)
    lab[..., 0] = CLAHE.apply(lab[..., 0])
    return cv2.cvtColor(lab, cv2.COLOR_Lab2BGR)


def sharpness(img):
    g = cv2.cvtColor(cv2.resize(img, (480, 270)), cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(g, cv2.CV_32F).var())


def grab_sharp(cap, t, native):
    """Кадр в t или его сосед ±0.33 с — что резче (панорамы смазывают)."""
    best = None
    for tt in (t, t - 0.33, t + 0.33):
        if tt < 0:
            continue
        cap.set(cv2.CAP_PROP_POS_FRAMES, round(tt * native))
        ok, img = cap.read()
        if not ok:
            continue
        s = sharpness(img)
        if best is None or s > best[0]:
            best = (s, tt, img)
    return best


def load_focal(video: Path):
    """[(t, f_px)] из coverage-таблицы, если она есть."""
    p = COVERAGE_DIR / f"{video.name}.coverage.tsv"
    if not p.exists():
        return []
    out = []
    with p.open(encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            try:
                out.append((float(r["t"]), float(r["f_px"])))
            except (ValueError, KeyError):
                continue
    return out


def focal_at(focal, t, default=7000.0):
    """Фокусное ближайшего сэмпла coverage-таблицы (шаг там 2 с)."""
    if not focal:
        return default
    tt, f = min(focal, key=lambda p: abs(p[0] - t))
    return f if abs(tt - t) <= 2.5 and f > 0 else default


def scan_video(video, out, tsv, dem, args):
    cap = cv2.VideoCapture(str(video))
    native = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
    dur = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / native
    try:
        rows = load_rows(video)
    except FileNotFoundError:
        rows = []
    focal = load_focal(video)
    kept = 0

    if not rows:
        # без телеметрии: дедуп по сдвигу сцены
        prev = None
        t = 0.0
        while t < dur:
            cap.set(cv2.CAP_PROP_POS_FRAMES, round(t * native))
            ok, img = cap.read()
            if not ok:
                break
            small = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY),
                               (480, 270)).astype(np.float32)
            if prev is not None:
                (dx, dy), _ = cv2.phaseCorrelate(prev, small)
                if math.hypot(dx, dy) < args.shift_px:
                    t += args.step
                    continue
            prev = small
            name = f"{video.stem}_t{int(t):04d}.jpg"
            cv2.imwrite(str(out / name), enhance(img), [cv2.IMWRITE_JPEG_QUALITY, 90])
            tsv.write(f"{video.stem}\t{t:.1f}\t\t\t\t\t\t\t\t\t\t{name}\n")
            kept += 1
            t += args.step
        cap.release()
        print(f"{video.name}: без телеметрии, кадров {kept}")
        return

    last = None      # (lat, lon, yaw, pitch) последнего взятого
    t = 0.0
    while t < dur:
        lat, lon = at(rows, t, "lat"), at(rows, t, "lon")
        alt = at(rows, t, "alt_m")
        yaw, pitch = at(rows, t, "gb_yaw"), at(rows, t, "gb_pitch")
        hit = cast(dem, lat, lon, alt, ray_dir(yaw, pitch, w / 2, h / 2, w, h,
                                               focal_at(focal, t)))
        if hit is not None:
            inside = (args.lat0 <= hit[0] <= args.lat1
                      and args.lon0 <= hit[1] <= args.lon1)
        else:
            # зависание вплотную к склону: луч не встречает сглаженный DEM —
            # судим по позиции дрона (он в таких съёмках в метрах от объекта)
            inside = (args.lat0 <= lat <= args.lat1
                      and args.lon0 <= lon <= args.lon1)
        if not inside:
            t += args.step
            continue
        f_now = focal_at(focal, t)
        if last is not None:
            # порог смены вида — доля поля зрения (зум сужает FOV до ~16°,
            # фиксированный градусный порог там пропускает целые сцены)
            hfov = 2 * math.degrees(math.atan2(w / 2, f_now))
            thr = args.view_frac * hfov
            moved = math.hypot((lat - last[0]) * 110574,
                               (lon - last[1]) * 111320 * math.cos(math.radians(lat)))
            dist = hit[3] if hit is not None else 60.0
            moved_deg = math.degrees(math.atan2(moved, max(dist, 1.0)))
            turned = max(abs(yaw - last[2]), abs(pitch - last[3]))
            zoomed = abs(f_now / last[4] - 1) > 0.15
            if turned < thr and moved_deg < thr and not zoomed:
                t += args.step
                continue
        got = grab_sharp(cap, t, native)
        if got is None:
            break
        _, tt, img = got
        last = (lat, lon, yaw, pitch, f_now)
        name = f"{video.stem}_t{int(tt):04d}.jpg"
        cv2.imwrite(str(out / name), enhance(img), [cv2.IMWRITE_JPEG_QUALITY, 90])
        f_px = focal_at(focal, t, default=0) or ""
        hs = (f"{hit[0]:.6f}\t{hit[1]:.6f}\t{hit[2]:.0f}\t{hit[3]:.0f}"
              if hit is not None else "\t\t\t")
        tsv.write(f"{video.stem}\t{tt:.1f}\t{lat:.6f}\t{lon:.6f}\t{alt:.0f}\t"
                  f"{hs}\t{yaw:.1f}\t{pitch:.1f}\t{f_px}\t{name}\n")
        kept += 1
        t += args.step
    cap.release()
    print(f"{video.name}: кадров в участке {kept}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("videos", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--lat0", type=float, default=39.4818)
    ap.add_argument("--lat1", type=float, default=39.4845)
    ap.add_argument("--lon0", type=float, default=73.5836)
    ap.add_argument("--lon1", type=float, default=73.5878)
    ap.add_argument("--step", type=float, default=1.0)
    ap.add_argument("--view-frac", type=float, default=0.35,
                    help="дедуп: доля поля зрения, на которую должен смениться вид")
    ap.add_argument("--shift-px", type=float, default=90.0,
                    help="дедуп без телеметрии: минимальный сдвиг сцены (пикс. 480×270)")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    dem = Dem()
    with (args.out / "frames.tsv").open("w", encoding="utf-8") as tsv:
        tsv.write("video\tt\tdrone_lat\tdrone_lon\tdrone_alt\thit_lat\thit_lon\t"
                  "hit_alt\tdist_m\tgb_yaw\tgb_pitch\tf_px\tfile\n")
        for v in args.videos:
            scan_video(v, args.out, tsv, dem, args)
