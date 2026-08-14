#!/usr/bin/env python3
"""Детектор следов на снегу: гребневой фильтр с прайором «вниз по склону».

Цветовой детектор (detect.py) борозды не ловит — они не отличаются цветом.
Простые рельефные признаки (black-hat, анизотропия по всем ориентациям) на
эталонных бороздах не отделяются от фактуры снега: кочки, заструги и потёки
дают тот же отклик. Работает физический прайор: борозда падения направлена
вниз по склону. Направление «вниз» в пикселях кадра считается из телеметрии
(углы подвеса) и градиента DEM в точке попадания центрального луча; гребневой
отклик (анизотропное сглаживание вдоль линии + вторая производная поперёк)
берётся только вдоль этого направления, а отклик поперёк вычитается.
Слоистость льда (поперёк склона) и ненаправленный рельеф давятся.

Порог не отделяет борозды от потёков (те тоже вниз по склону) — слой работает
как подсказчик: топ-N вытянутых компонент кадра уходит в кропы и монтажи на
VLM/человеческий триаж. Калибровка на эталонах (обе известные борозды —
ранг 0 своего кадра при thr=8): борозды Геннадия — 163855 t=244 (зона кадра
x 400–860), борозды U — 163855 t=55 (x 718–1345, кадр скрина TG #1752).

Использование:
  .venv/bin/python furrow_scan.py ВИДЕО.MP4 --out scans-furrow/ИМЯ/ [--fps 1] [--top 10]
  .venv/bin/python furrow_scan.py ВИДЕО.MP4 --frame 244 --out debug/   # один кадр с оверлеем

Нужны сайдкар .gps.tsv (прайор не считается без телеметрии) и DEM-тайл.
Выход в --out: crops/tNNNN_MMmSSs.jpg (кроп с рамкой), tracks.tsv (таймкоды,
bbox, длина, score, GPS дрона), montage_NN.jpg (сетки 4×4).
"""

import argparse
import math
from pathlib import Path

import cv2
import numpy as np

from geoproject import Dem, at, cast, load_rows, ray_dir
from trace_frames import focal_at, load_focal
from video_scan import frame_shift, gps_at, load_gps, tc

# снежная маска (см. snow_mask): яркие пиксели крупных снежных полей
SNOW_L_MIN = 140
SNOW_CHROMA_MAX = 14
FIELD_CLOSE = 15
FIELD_MIN_AREA = 15000
SNOW_ERODE = 5

# гребневой отклик (на половинном разрешении)
SIGMA_ALONG = 12.0       # сглаживание вдоль линии
SIGMA_ACROSS = 2.5       # поперёк (≈ полуширина борозды)
BG_SIGMA = 25.0          # фон нормализованной свёрткой
RESP_THR = 8.0           # порог отклика (калибровка на эталонах)
MIN_AREA = 40            # пикс. половинного разрешения
MIN_LEN = 35             # длина главной оси (половинное разрешение)
MIN_ASPECT = 2.5

CROP = 224               # полукроп (native) вокруг центра следа
MATCH_DIST = 90
TRACK_TTL = 3
TILE = 288
GRID = 4


def snow_mask(lab: np.ndarray) -> np.ndarray:
    """Чистый снег крупных снежных полей (вкрапления камней исключены)."""
    l_ch = lab[..., 0]
    a_ch, b_ch = lab[..., 1].astype(np.int16), lab[..., 2].astype(np.int16)
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
    return cv2.erode(raw & big, np.ones((SNOW_ERODE, SNOW_ERODE), np.uint8))


def downslope_theta(dem, rows, t, f_px, w, h):
    """Угол направления «вниз по склону» в пикселях кадра (deg, y вниз) или None."""
    lat, lon = at(rows, t, "lat"), at(rows, t, "lon")
    alt = at(rows, t, "alt_m")
    yaw, pitch = at(rows, t, "gb_yaw"), at(rows, t, "gb_pitch")
    hit = cast(dem, lat, lon, alt, ray_dir(yaw, pitch, w / 2, h / 2, w, h, f_px))
    if hit is None:
        return None
    hl, ho = hit[0], hit[1]
    step = 15.0
    m_lon = 111320.0 * math.cos(math.radians(hl))
    try:
        dz_dn = (dem.elev(hl + step / 110574, ho)
                 - dem.elev(hl - step / 110574, ho)) / (2 * step)
        dz_de = (dem.elev(hl, ho + step / m_lon)
                 - dem.elev(hl, ho - step / m_lon)) / (2 * step)
    except ValueError:
        return None
    g = math.hypot(dz_dn, dz_de)
    if g < 1e-4:
        return None
    v = np.array([-dz_de / g, -dz_dn / g, -g])
    v /= np.linalg.norm(v)
    ry, rp = math.radians(yaw), math.radians(pitch)
    fwd = np.array([math.sin(ry) * math.cos(rp), math.cos(ry) * math.cos(rp),
                    math.sin(rp)])
    right = np.array([math.cos(ry), -math.sin(ry), 0.0])
    up = np.cross(right, fwd)
    dx, dy = float(v @ right), float(-(v @ up))
    n = math.hypot(dx, dy)
    if n < 1e-6:
        return None
    return math.degrees(math.atan2(dy / n, dx / n))


def ridge_response(img, theta_deg):
    """Отклик борозды вдоль направления theta минус отклик поперёк (полуразмер)."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2Lab)
    snow = snow_mask(lab)
    l = lab[..., 0].astype(np.float32)
    half = cv2.resize(l, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    m = cv2.resize(snow, None, fx=0.5, fy=0.5,
                   interpolation=cv2.INTER_NEAREST).astype(np.float32)
    if m.sum() < 2000:
        return None, None
    bg = cv2.GaussianBlur(half * m, (0, 0), BG_SIGMA) / (
        cv2.GaussianBlur(m, (0, 0), BG_SIGMA) + 1e-6)
    r = (half - bg) * m
    h, w = r.shape
    outs = []
    for rot in (-theta_deg, -theta_deg + 90):
        M = cv2.getRotationMatrix2D((w / 2, h / 2), rot, 1.0)
        Mi = cv2.getRotationMatrix2D((w / 2, h / 2), -rot, 1.0)
        rr = cv2.warpAffine(r, M, (w, h), borderMode=cv2.BORDER_CONSTANT)
        b = cv2.GaussianBlur(rr, (0, 0), sigmaX=SIGMA_ALONG, sigmaY=SIGMA_ACROSS)
        d = cv2.Sobel(b, cv2.CV_32F, 0, 2, ksize=5)
        outs.append(cv2.warpAffine(d, Mi, (w, h), borderMode=cv2.BORDER_CONSTANT))
    resp = np.clip(np.clip(outs[0], 0, None) - np.clip(outs[1], 0, None), 0, None)
    return resp * (m > 0), m


def furrow_components(img, theta_deg, top=None):
    """[(x, y, w, h, length, score, contour)] в НАТИВНЫХ пикселях кадра."""
    resp, m = ridge_response(img, theta_deg)
    if resp is None:
        return []
    mm = (resp > RESP_THR).astype(np.uint8)
    mm = cv2.morphologyEx(mm, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mm, connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < MIN_AREA:
            continue
        comp = (labels[y:y + h, x:x + w] == i).astype(np.uint8)
        cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cnt = max(cnts, key=cv2.contourArea)
        (_, _), (d0, d1), _ang = cv2.minAreaRect(cnt)
        length, width = max(d0, d1), max(min(d0, d1), 1.0)
        if length < MIN_LEN or length / width < MIN_ASPECT:
            continue
        mean_r = float(resp[y:y + h, x:x + w][comp > 0].mean())
        score = round(length * mean_r, 1)
        out.append((x * 2, y * 2, w * 2, h * 2, round(length * 2, 1), score,
                    (cnt + [x, y]) * 2))
    out.sort(key=lambda c: -c[5])
    return out[:top] if top else out


def crop_with_outline(img, comp, half=CROP):
    x, y, w, h, *_rest, cnt = comp
    cx, cy = x + w // 2, y + h // 2
    x0, y0 = max(0, cx - half), max(0, cy - half)
    crop = img[y0:cy + half, x0:cx + half].copy()
    cv2.drawContours(crop, [cnt - [x0, y0]], -1, (0, 0, 255), 1)
    return crop


def debug_frame(video: Path, t: float, out: Path):
    dem = Dem()
    rows = load_rows(video)
    focal = load_focal(video)
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
    cap.set(cv2.CAP_PROP_POS_FRAMES, round(t * fps))
    ok, img = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"кадр t={t} не читается")
    theta = downslope_theta(dem, rows, t, focal_at(focal, t), w, h)
    if theta is None:
        raise SystemExit("прайор не считается: луч мимо рельефа / нет телеметрии")
    comps = furrow_components(img, theta)
    vis = img.copy()
    for j, comp in enumerate(comps[:15]):
        x, y, wc, hc, length, score, cnt = comp
        cv2.drawContours(vis, [cnt], -1, (0, 0, 255), 2)
        cv2.putText(vis, f"{j} L{length:.0f} s{score:.0f}", (x, max(14, y - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{video.stem}_{tc(t)}"
    cv2.imwrite(str(out / f"{stem}.furrow.jpg"), vis, [cv2.IMWRITE_JPEG_QUALITY, 90])
    print(f"{stem}: θ={theta:.0f}°, {len(comps)} следов, топ-5: "
          f"{[c[5] for c in comps[:5]]}")


def scan(video: Path, out: Path, fps: float, top: int):
    dem = Dem()
    rows = load_rows(video)
    focal = load_focal(video)
    cap = cv2.VideoCapture(str(video))
    native = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
    step = max(1, round(native / fps))
    gps = load_gps(video)

    tracks, done = [], []
    prev_small = None
    theta_last = None
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

        theta = downslope_theta(dem, rows, t, focal_at(focal, t), w, h)
        if theta is None:
            theta = theta_last    # зависание вплотную: направление меняется медленно
        else:
            theta_last = theta
        comps = furrow_components(img, theta, top=top) if theta is not None else []

        for tr in tracks:
            tr["misses"] += 1
        for comp in comps:
            x, y, wc, hc, length, score, _cnt = comp
            cx, cy = x + wc // 2, y + hc // 2
            best = None
            for tr in tracks:
                d = math.hypot(cx - tr["cx"], cy - tr["cy"])
                if d < MATCH_DIST and (best is None or d < best[0]):
                    best = (d, tr)
            if best:
                tr = best[1]
                tr.update(cx=cx, cy=cy, misses=0, t1=t)
                if score > tr["score"]:
                    tr.update(score=score, bbox=(x, y, wc, hc), length=length,
                              t_best=t, crop=crop_with_outline(img, comp))
            else:
                tracks.append(dict(cx=cx, cy=cy, score=score, bbox=(x, y, wc, hc),
                                   length=length, t0=t, t1=t, t_best=t, misses=0,
                                   crop=crop_with_outline(img, comp)))
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
        f.write("track\tt_best\tt0\tt1\tbbox\tlength_px\tscore\tlat\tlon\talt\n")
        for i, tr in enumerate(done):
            lat, lon, alt = gps_at(gps, tr["t_best"])
            x, y, wc, hc = tr["bbox"]
            f.write(f"{i}\t{tr['t_best']:.1f}\t{tr['t0']:.1f}\t{tr['t1']:.1f}\t"
                    f"{x},{y},{wc},{hc}\t{tr['length']}\t{tr['score']}\t"
                    f"{lat}\t{lon}\t{alt}\n")
            cv2.imwrite(str(crops_dir / f"t{i:04d}_{tc(tr['t_best'])}.jpg"),
                        tr["crop"], [cv2.IMWRITE_JPEG_QUALITY, 92])

    per = GRID * GRID
    for mstart in range(0, len(done), per):
        sheet = np.zeros((TILE * GRID, TILE * GRID, 3), np.uint8)
        for j, tr in enumerate(done[mstart:mstart + per]):
            cell = cv2.resize(tr["crop"], (TILE, TILE))
            cv2.putText(cell, f"t{mstart + j} {tc(tr['t_best'])} L{tr['length']:.0f}",
                        (4, TILE - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (0, 255, 255), 1)
            r, c = divmod(j, GRID)
            sheet[r * TILE:(r + 1) * TILE, c * TILE:(c + 1) * TILE] = cell
        cv2.imwrite(str(out / f"montage_{mstart // per:02d}.jpg"), sheet,
                    [cv2.IMWRITE_JPEG_QUALITY, 90])

    print(f"{video.name}: сэмплов {n_sample}, следов-треков {len(done)}, "
          f"монтажей {math.ceil(len(done) / per) if done else 0}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--fps", type=float, default=1.0)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--frame", type=float, help="только один кадр t, с (отладка)")
    args = ap.parse_args()
    if args.frame is not None:
        debug_frame(args.video, args.frame, args.out)
    else:
        args.out.mkdir(parents=True, exist_ok=True)
        scan(args.video, args.out, args.fps, args.top)
