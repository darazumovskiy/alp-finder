#!/usr/bin/env python3
"""Детектор следов на снегу: вытянутые рельефные депрессии (борозды, следы качения).

Цветовой детектор (detect.py) борозды не ловит — они не отличаются цветом
(«Порог различимости», docs/video-analysis.md). Здесь признак рельефный:
борозда на снегу — вытянутая тёмная линия (тень собственной депрессии).
Метод: снежная маска (яркий низкохромный участок) → CLAHE → black-hat
линейными ядрами по вееру ориентаций (два масштаба) → связные компоненты →
фильтр вытянутости → score = длина × средняя глубина.

Шум по построению: слоистость льда, кромки снежников, састуги. Отсев — как
в основном конвейере: бюджет топ-N на кадр, трекинг повторов, ИИ/глаза
по монтажам. Калибровка: эталонные борозды обязаны попадать в топ кадра
(борозды U 163855 0:53, борозды Геннадия 163855 4:04, след срыва 135426 0:41).

Использование:
  .venv/bin/python furrow_scan.py ВИДЕО.MP4 --out scans-furrow/ИМЯ/ [--fps 1] [--top 12]
  .venv/bin/python furrow_scan.py ВИДЕО.MP4 --frame 53.5 --out debug/   # один кадр с оверлеем

Выход в --out: crops/tNNNN_MMmSSs.jpg (кроп с контуром следа), tracks.tsv
(таймкоды, bbox, длина в пикс., ориентация, score, GPS дрона из сайдкара),
montage_NN.jpg (сетки 4×4).
"""

import argparse
import math
from pathlib import Path

import cv2
import numpy as np

from video_scan import frame_shift, gps_at, load_gps, tc

# снежная маска
SNOW_L_MIN = 140         # L (Lab, 0..255): темнее — скалы/осыпь/глубокая тень
SNOW_CHROMA_MAX = 14     # |a-128|+|b-128|: выше — цветное (не снег)
FIELD_CLOSE = 15         # закрытие для сборки снежного поля
FIELD_MIN_AREA = 15000   # пикс.: поля меньше — пятна на осыпи, не снежник
SNOW_ERODE = 5           # пикс.: отступ от кромки снежника (кромка — сама линия)

# отклик борозды
ORIENTS = 12             # ориентаций веера ядер
LINE_LEN = (25, 51)      # длины линейных ядер black-hat (два масштаба)
RESP_PCTL = 99.0         # порог отклика: перцентиль по чистому снегу
RESP_MIN = 10.0          # и не ниже этого (уровней серого после CLAHE)

# фильтр компонент
MIN_AREA = 100           # пикс. маски
MIN_LEN = 45             # длина главной оси minAreaRect
MIN_ASPECT = 2.8         # вытянутость (длина/ширина)

CROP = 224               # полукроп вокруг центра следа
MATCH_DIST = 90          # трекинг: ближе — тот же след
TRACK_TTL = 3
TILE = 288
GRID = 4


def _line_kernels():
    """Линейные ядра всех ориентаций и масштабов (кэш на импорт)."""
    kernels = []
    for ln in LINE_LEN:
        for k in range(ORIENTS):
            th = math.pi * k / ORIENTS
            c = np.zeros((ln, ln), np.uint8)
            x0 = int(ln // 2 - (ln // 2 - 1) * math.cos(th))
            y0 = int(ln // 2 - (ln // 2 - 1) * math.sin(th))
            x1 = int(ln // 2 + (ln // 2 - 1) * math.cos(th))
            y1 = int(ln // 2 + (ln // 2 - 1) * math.sin(th))
            cv2.line(c, (x0, y0), (x1, y1), 1, 1)
            kernels.append(c)
    return kernels


KERNELS = _line_kernels()
CLAHE = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(16, 16))


def snow_mask(lab: np.ndarray) -> np.ndarray:
    """Чистый снег крупных снежных полей.

    Два условия сразу: пиксель сам снежно-яркий (тёмные камни-вкрапления
    исключаются — их black-hat-отклик иначе съедает перцентильный порог)
    и лежит в крупном снежном поле (мелкие яркие пятна на осыпи — не снежник,
    серая осыпь целиком в поле не собирается).
    """
    l_ch, a_ch, b_ch = lab[..., 0], lab[..., 1].astype(np.int16), lab[..., 2].astype(np.int16)
    chroma = np.abs(a_ch - 128) + np.abs(b_ch - 128)
    raw = ((l_ch >= SNOW_L_MIN) & (chroma <= SNOW_CHROMA_MAX)).astype(np.uint8)
    raw = cv2.morphologyEx(raw, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    field = cv2.morphologyEx(raw, cv2.MORPH_CLOSE,
                             np.ones((FIELD_CLOSE, FIELD_CLOSE), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(field, connectivity=8)
    big = np.zeros_like(field)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= FIELD_MIN_AREA:
            big[labels == i] = 1
    m = raw & big
    return cv2.erode(m, np.ones((SNOW_ERODE, SNOW_ERODE), np.uint8))


def furrow_components(img: np.ndarray):
    """[(x, y, w, h, length, angle, score, contour)] вытянутых следов на снегу.

    score — длина главной оси × средний отклик black-hat: длинная глубокая
    борозда набирает много, короткая вмятина или слабая тень — мало.
    """
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2Lab)
    snow = snow_mask(lab)
    if int(snow.sum()) < 4000:            # снега в кадре практически нет
        return []
    enh = CLAHE.apply(lab[..., 0])
    resp = np.zeros(enh.shape, np.uint8)
    for kern in KERNELS:
        np.maximum(resp, cv2.morphologyEx(enh, cv2.MORPH_BLACKHAT, kern), out=resp)
    resp = resp * snow

    vals = resp[snow > 0]
    thr = max(float(np.percentile(vals, RESP_PCTL)), RESP_MIN)
    m = (resp >= thr).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

    n, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < MIN_AREA:
            continue
        comp = (labels[y:y + h, x:x + w] == i).astype(np.uint8)
        cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cnt = max(cnts, key=cv2.contourArea)
        (_, _), (d0, d1), ang = cv2.minAreaRect(cnt)
        length, width = max(d0, d1), max(min(d0, d1), 1.0)
        if length < MIN_LEN or length / width < MIN_ASPECT:
            continue
        mean_resp = float(resp[y:y + h, x:x + w][comp > 0].mean())
        score = round(length * mean_resp, 1)
        out.append((int(x), int(y), int(w), int(h), round(float(length), 1),
                    round(ang, 1), score, cnt + [x, y]))
    out.sort(key=lambda b: -b[6])
    return out


def crop_with_outline(img, comp, half=CROP):
    x, y, w, h, *_rest, cnt = comp
    cx, cy = x + w // 2, y + h // 2
    x0, y0 = max(0, cx - half), max(0, cy - half)
    crop = img[y0:cy + half, x0:cx + half].copy()
    cv2.drawContours(crop, [cnt - [x0, y0]], -1, (0, 0, 255), 1)
    return crop


def debug_frame(video: Path, t: float, out: Path):
    """Один кадр: полнокадровый оверлей маски снега и найденных следов."""
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, img = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"кадр t={t} не читается")
    comps = furrow_components(img)
    vis = img.copy()
    snow = snow_mask(cv2.cvtColor(img, cv2.COLOR_BGR2Lab))
    vis[snow == 0] = (vis[snow == 0] * 0.55).astype(np.uint8)
    for j, comp in enumerate(comps):
        x, y, w, h, length, ang, score, cnt = comp
        cv2.drawContours(vis, [cnt], -1, (0, 0, 255), 2)
        cv2.putText(vis, f"{j} L{length:.0f} s{score:.0f}", (x, max(14, y - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{video.stem}_{tc(t)}"
    cv2.imwrite(str(out / f"{stem}.furrow.jpg"), vis, [cv2.IMWRITE_JPEG_QUALITY, 90])
    for j, comp in enumerate(comps[:12]):
        cv2.imwrite(str(out / f"{stem}_c{j:02d}.jpg"), crop_with_outline(img, comp),
                    [cv2.IMWRITE_JPEG_QUALITY, 92])
    print(f"{stem}: {len(comps)} следов, топ-5 score: "
          f"{[c[6] for c in comps[:5]]}")


def scan(video: Path, out: Path, fps: float, top: int):
    cap = cv2.VideoCapture(str(video))
    native = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(native / fps))
    gps = load_gps(video)

    tracks, done = [], []
    prev_small = None
    n_frame = n_sample = 0
    flow_w = 480
    while True:
        ok = cap.grab()
        if not ok:
            break
        if n_frame % step:
            n_frame += 1
            continue
        ok, img = cap.retrieve()
        n_frame += 1
        if not ok:
            break
        t = (n_frame - 1) / native
        n_sample += 1
        scale = img.shape[1] / flow_w
        small = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY),
                           (flow_w, int(img.shape[0] / scale))).astype(np.float32)
        if prev_small is not None and small.shape == prev_small.shape:
            dx, dy = frame_shift(prev_small, small, scale)
            for tr in tracks:
                tr["cx"] += dx
                tr["cy"] += dy
        prev_small = small

        comps = furrow_components(img)[:top]
        for tr in tracks:
            tr["misses"] += 1
        for comp in comps:
            x, y, w, h, length, ang, score, _cnt = comp
            cx, cy = x + w // 2, y + h // 2
            best = None
            for tr in tracks:
                d = math.hypot(cx - tr["cx"], cy - tr["cy"])
                if d < MATCH_DIST and (best is None or d < best[0]):
                    best = (d, tr)
            if best:
                tr = best[1]
                tr.update(cx=cx, cy=cy, misses=0, t1=t)
                if score > tr["score"]:
                    tr.update(score=score, bbox=(x, y, w, h), length=length,
                              ang=ang, t_best=t, crop=crop_with_outline(img, comp))
            else:
                tracks.append(dict(cx=cx, cy=cy, score=score, bbox=(x, y, w, h),
                                   length=length, ang=ang, t0=t, t1=t, t_best=t,
                                   misses=0, crop=crop_with_outline(img, comp)))
        still = []
        for tr in tracks:
            (done if tr["misses"] > TRACK_TTL else still).append(tr)
        tracks = still
    done += tracks
    cap.release()
    done.sort(key=lambda tr: -tr["score"])

    crops_dir = out / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)
    with (out / "tracks.tsv").open("w", encoding="utf-8") as f:
        f.write("track\tt_best\tt0\tt1\tbbox\tlength_px\tangle\tscore\tlat\tlon\talt\n")
        for i, tr in enumerate(done):
            lat, lon, alt = gps_at(gps, tr["t_best"])
            x, y, w, h = tr["bbox"]
            f.write(f"{i}\t{tr['t_best']:.1f}\t{tr['t0']:.1f}\t{tr['t1']:.1f}\t"
                    f"{x},{y},{w},{h}\t{tr['length']}\t{tr['ang']}\t{tr['score']}\t"
                    f"{lat}\t{lon}\t{alt}\n")
            cv2.imwrite(str(crops_dir / f"t{i:04d}_{tc(tr['t_best'])}.jpg"),
                        tr["crop"], [cv2.IMWRITE_JPEG_QUALITY, 92])

    per = GRID * GRID
    for m in range(0, len(done), per):
        sheet = np.zeros((TILE * GRID, TILE * GRID, 3), np.uint8)
        for j, tr in enumerate(done[m:m + per]):
            cell = cv2.resize(tr["crop"], (TILE, TILE))
            cv2.putText(cell, f"t{m + j} {tc(tr['t_best'])} L{tr['length']:.0f}",
                        (4, TILE - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
            r, c = divmod(j, GRID)
            sheet[r * TILE:(r + 1) * TILE, c * TILE:(c + 1) * TILE] = cell
        cv2.imwrite(str(out / f"montage_{m // per:02d}.jpg"), sheet,
                    [cv2.IMWRITE_JPEG_QUALITY, 90])

    print(f"{video.name}: сэмплов {n_sample}, следов-треков {len(done)}, "
          f"монтажей {math.ceil(len(done) / per) if done else 0}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--fps", type=float, default=1.0)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--frame", type=float, help="только один кадр t, с (отладка)")
    args = ap.parse_args()
    if args.frame is not None:
        debug_frame(args.video, args.frame, args.out)
    else:
        args.out.mkdir(parents=True, exist_ok=True)
        scan(args.video, args.out, args.fps, args.top)
