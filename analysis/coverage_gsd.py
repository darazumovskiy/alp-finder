#!/usr/bin/env python3
"""Реальное покрытие дальних облётов 11–12.08: GSD по центру кадра против порога 8 px.

Стенд врезок (docs/video-analysis.md, «Порог различимости») дал правило: цветной
предмет уверенно доходит до отсмотра от 8 px кадра 1080p. Этот скрипт переводит
порог в сантиметры для каждого момента каждого ролика 11–12.08: фокусное —
самокалибровкой по панорамированию (docs/focal-length-calibration.md), дальность —
трассировкой центрального луча в рельеф (analysis/geoproject.py). Итог по ролику —
какую долю времени детектор реально «видел» предмет размера рюкзака / куртки /
крышки, и какую долю времени фокусное неизвестно (зависаний без панорам нет).

Считается только центр кадра — как в расчёте покрытия внешней группы
(docs/nezavisimyy-analiz/01-probel-pokrytiya.md): оценка занижает покрытие краёв
кадра, но для вердикта «осмотрено/не осмотрено» это безопасная сторона.

Использование:  cd analysis && .venv/bin/python coverage_gsd.py [--step 2]
Выход: coverage/<видео>.tsv (по строке на сэмпл), coverage/summary.tsv, таблица в stdout.
"""

import argparse
import math
from pathlib import Path

import cv2
import numpy as np

from geoproject import (Dem, cast, ray_dir, load_rows, grab, gray_center,
                        interp_gap, pair_focal, unwrap_deg)

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data" / "drive"
OUT = HERE / "coverage"

PAIR_DT = 0.5        # база пары кадров для фокусного, с
PAIR_EVERY = 2.0     # минимальный шаг между парами, с
ROT_MIN, ROT_MAX = 0.4, 8.0   # суммарный поворот подвеса в паре, градусы
F_BAND = (1000.0, 20000.0)    # оценки фокусного вне этой полосы - брак
F_WINDOW = 20.0      # окно поиска оценок фокусного вокруг сэмпла, с
F_MIN_ESTS = 3       # минимум оценок в окне, иначе фокусное «неизвестно»
THRESH_PX = 8        # порог различимости из стенда врезок
OBJECTS = (("ryukzak", 1.00), ("kurtka", 0.60), ("kryshka", 0.17))


def tracks(rows):
    """Массивы времени и полей телеметрии с развёрнутым yaw (пропуски — nan)."""
    t = np.array([r["time_s"] for r in rows])
    out = {"t": t}
    for k in ("lat", "lon", "alt_m", "gb_pitch"):
        out[k] = np.array([r.get(k, math.nan) for r in rows])
    out["gb_yaw"] = unwrap_deg([r.get("gb_yaw", math.nan) for r in rows])
    return out


def interp(tr, t, key):
    return interp_gap(t, tr["t"], tr[key])


def pan_pairs(tr):
    """[(t0, t1)] — пары кадров, где подвес повернулся на ROT_MIN..ROT_MAX градусов."""
    pairs = []
    t = float(tr["t"][0])
    end = float(tr["t"][-1])
    while t + PAIR_DT <= end:
        dyaw = interp(tr, t + PAIR_DT, "gb_yaw") - interp(tr, t, "gb_yaw")
        dpitch = interp(tr, t + PAIR_DT, "gb_pitch") - interp(tr, t, "gb_pitch")
        rot = math.hypot(dyaw, dpitch)
        dominant = abs(dyaw) > 2 * abs(dpitch) or abs(dpitch) > 2 * abs(dyaw)
        if ROT_MIN <= rot <= ROT_MAX and dominant:
            pairs.append((t, t + PAIR_DT))
            t += PAIR_EVERY
        else:
            t += PAIR_DT
    return pairs


def focal_series(video: Path, tr, dem: Dem):
    """([(t_середины_пары, f_px)], счётчик отказов) по панорамированиям ролика."""
    cap = cv2.VideoCapture(str(video))
    sample = lambda t, key: interp(tr, t, key)      # noqa: E731
    ests, why = [], {}
    for a, b in pan_pairs(tr):
        try:
            ga, sa = gray_center(grab(cap, a))
            gb, _ = gray_center(grab(cap, b))
        except ValueError:
            continue
        (dx, dy), _resp = cv2.phaseCorrelate(ga, gb)
        f, reason = pair_focal(sample, dem, (dx * sa, dy * sa), a, b)
        if f is None:
            why[reason] = why.get(reason, 0) + 1
        elif F_BAND[0] <= f <= F_BAND[1]:
            ests.append(((a + b) / 2, f))
        else:
            why["out_of_band"] = why.get("out_of_band", 0) + 1
    cap.release()
    return ests, why


def focal_at(ests, t):
    """Медиана оценок фокусного в окне ±F_WINDOW вокруг t или None."""
    near = [f for tf, f in ests if abs(tf - t) <= F_WINDOW]
    if len(near) < F_MIN_ESTS:
        return None
    return float(np.median(near))


def scan_video(video: Path, dem: Dem, step: float):
    rows = load_rows(video)
    if not rows or "gb_yaw" not in rows[0]:
        return None
    tr = tracks(rows)
    ests, why = focal_series(video, tr, dem)
    cap = cv2.VideoCapture(str(video))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
    cap.release()

    samples = []
    t = float(tr["t"][0])
    while t <= float(tr["t"][-1]):
        f = focal_at(ests, t)
        row = dict(t=t, f=f, dist=None, gsd=None, sky=False, gap=False)
        pose = [interp(tr, t, k) for k in ("lat", "lon", "alt_m", "gb_yaw", "gb_pitch")]
        if not all(math.isfinite(v) for v in pose):
            row["gap"] = True
        elif f is not None:
            lat, lon, alt, yaw, pitch = pose
            hit = cast(dem, lat, lon, alt, ray_dir(yaw % 360, pitch, w / 2, h / 2, w, h, f))
            if hit is None:
                row["sky"] = True
            else:
                row["dist"] = hit[3]
                row["gsd"] = hit[3] / f          # м/пикс в центре кадра
        samples.append(row)
        t += step
    return dict(video=video, n_ests=len(ests), why=why, samples=samples, width=w)


def write_video_tsv(res):
    out = OUT / (res["video"].name + ".coverage.tsv")
    with out.open("w", encoding="utf-8") as f:
        f.write("t\tf_px\tdist_m\tgsd_cm\tobj8px_cm\tstatus\n")
        for s in res["samples"]:
            if s["gap"]:
                st, fpx, d, g = "no_telemetry", "", "", ""
            elif s["f"] is None:
                st, fpx, d, g = "no_focal", "", "", ""
            elif s["sky"]:
                st, fpx, d, g = "above_horizon", f"{s['f']:.0f}", "", ""
            else:
                st = "ok"
                fpx, d = f"{s['f']:.0f}", f"{s['dist']:.0f}"
                g = f"{s['gsd'] * 100:.1f}"
            o8 = f"{s['gsd'] * 100 * THRESH_PX:.0f}" if s["gsd"] else ""
            f.write(f"{s['t']:.1f}\t{fpx}\t{d}\t{g}\t{o8}\t{st}\n")


def summarize(res):
    ss = res["samples"]
    n = len(ss)
    known = [s for s in ss if s["gsd"] is not None]
    nofocal = sum(1 for s in ss if s["f"] is None and not s["gap"])
    sky = sum(1 for s in ss if s["sky"])
    gap = sum(1 for s in ss if s["gap"])
    row = dict(video=res["video"].name, n=n, n_ests=res["n_ests"],
               no_focal=nofocal / n, sky=sky / n, gap=gap / n,
               f_drone_move=res["why"].get("drone_move", 0), width=res["width"])
    if known:
        gsds = np.array([s["gsd"] for s in known])
        row["gsd_med_cm"] = float(np.median(gsds)) * 100
        row["gsd_p90_cm"] = float(np.percentile(gsds, 90)) * 100
        row["obj8_med_cm"] = row["gsd_med_cm"] * THRESH_PX
        for name, size_m in OBJECTS:
            # покрыто = предмет size_m занимает >= THRESH_PX пикселей
            row[name] = float((gsds <= size_m / THRESH_PX).mean()) * len(known) / n
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=float, default=2.0, help="шаг сэмплов, с")
    ap.add_argument("--dates", default="20260811,20260812")
    ap.add_argument("--only", default="", help="подстрока имени: пересчитать только эти ролики")
    args = ap.parse_args()
    dates = tuple(args.dates.split(","))
    videos = sorted({p.name: p for p in DATA.rglob("DJI_*.MP4")
                     if p.name[4:12] in dates and args.only in p.name}.values(),
                    key=lambda p: p.name)
    OUT.mkdir(exist_ok=True)
    dem = Dem()
    summary = []
    for v in videos:
        res = scan_video(v, dem, args.step)
        if res is None:
            print(f"{v.name}: нет углов в сайдкаре, пропуск")
            continue
        write_video_tsv(res)
        row = summarize(res)
        summary.append(row)
        cov = " ".join(f"{name} {row.get(name, 0):.0%}" for name, _ in OBJECTS)
        med = f"{row['obj8_med_cm']:.0f} см" if "obj8_med_cm" in row else "—"
        print(f"{row['video']}: оценок f {row['n_ests']} (отброшено за перелёт дрона "
              f"{row['f_drone_move']}), без фокусного {row['no_focal']:.0%}, "
              f"провал телеметрии {row['gap']:.0%}, выше горизонта {row['sky']:.0%}, "
              f"мин. предмет (8 px, медиана) {med}; покрытие: {cov}")

    keys = ["video", "width", "n", "n_ests", "f_drone_move", "no_focal", "sky", "gap",
            "gsd_med_cm", "gsd_p90_cm", "obj8_med_cm"] + [n for n, _ in OBJECTS]
    # сводка накопительная: строки других дат сохраняются, свои — заменяются
    path = OUT / "summary.tsv"
    old = {}
    if path.exists():
        lines = path.read_text().splitlines()
        # набор колонок менялся — старые строки без пересчёта смешивать нельзя
        if lines and lines[0].split("\t") == keys:
            for line in lines[1:]:
                old[line.split("\t", 1)[0]] = line
    for r in summary:
        old[r["video"]] = "\t".join("" if r.get(k) is None else
                                    (f"{r[k]:.3f}" if isinstance(r.get(k), float) else str(r[k]))
                                    for k in keys)
    with path.open("w", encoding="utf-8") as f:
        f.write("\t".join(keys) + "\n")
        for name in sorted(old):
            f.write(old[name] + "\n")
    print(f"\nсводка: {path}")


if __name__ == "__main__":
    main()
