#!/usr/bin/env python3
"""Кадры для реконструкции сцены 16.08 «кошки»: выборка по телеметрии.

Берёт кадры, где камера смотрит на сцену (угол оси до направления на точку
< ANG_MAX) с дистанции < DIST_MAX, шаг DT с; смазанные (лапласиан ниже порога)
пропускает. Полные 1920-кадры + прицельные JPG polanski →
analysis/scene3d/data/koshki-1608/images/.
"""
import math
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "analysis"))
from geoproject import at, load_rows  # noqa: E402

SCENE = 39.482535, 73.585930, 4596.0        # центр сцены (среднее групп 1-2)
OUT = ROOT / "analysis/scene3d/data/koshki-1608/images"
DT = 1.0
ANG_MAX = 18.0      # угол между осью камеры и направлением на сцену, град
DIST_MAX = 300.0
LAP_MIN = 25.0      # порог резкости (var лапласиана на даунскейле 640)

VIDEOS = [
    ("DJI_20260816114520_0001_Z", 0, None),
    ("DJI_20260816141704_0001_Z", 780, 995),
    ("DJI_20260816143344_0005_Z", 0, None),
]


def ang_to_scene(lat, lon, alt, yaw, pitch):
    m_lat, m_lon = 111132.0, 111320.0 * math.cos(math.radians(lat))
    e = (SCENE[1] - lon) * m_lon
    n = (SCENE[0] - lat) * m_lat
    u = SCENE[2] - alt
    d = math.sqrt(e * e + n * n + u * u)
    ce = math.cos(math.radians(pitch))
    cam = (math.sin(math.radians(yaw)) * ce, math.cos(math.radians(yaw)) * ce,
           math.sin(math.radians(pitch)))
    dot = (cam[0] * e + cam[1] * n + cam[2] * u) / max(d, 1e-6)
    return math.degrees(math.acos(max(-1, min(1, dot)))), d


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    kept = 0
    for stem, t0, t1 in VIDEOS:
        video = next(iter((ROOT / "data/drive").rglob(stem + ".MP4")), None)
        if video is None:
            print(f"{stem}: нет файла", file=sys.stderr)
            continue
        rows = load_rows(video)
        cap = cv2.VideoCapture(str(video))
        dur = cap.get(cv2.CAP_PROP_FRAME_COUNT) / max(cap.get(cv2.CAP_PROP_FPS), 1)
        n_v = 0
        t = float(t0)
        end = min(t1 or dur, dur)
        while t < end:
            pose = [at(rows, t, k) for k in ("lat", "lon", "alt_m", "gb_yaw", "gb_pitch")]
            if all(math.isfinite(v) for v in pose):
                ang, dist = ang_to_scene(*pose)
                if ang <= ANG_MAX and dist <= DIST_MAX:
                    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
                    ok, img = cap.read()
                    if ok:
                        g = cv2.cvtColor(cv2.resize(img, (640, 360)), cv2.COLOR_BGR2GRAY)
                        if cv2.Laplacian(g, cv2.CV_32F).var() >= LAP_MIN:
                            cv2.imwrite(str(OUT / f"{stem}_t{t:07.1f}.jpg"), img,
                                        [cv2.IMWRITE_JPEG_QUALITY, 95])
                            n_v += 1
            t += DT
        cap.release()
        kept += n_v
        print(f"{stem}: {n_v} кадров")
    stills = sorted((ROOT / "data/drive/2026-08-16/polanski").glob("*.JPG"))
    for p in stills:
        shutil.copy2(p, OUT / p.name)
    print(f"итого: {kept} видеокадров + {len(stills)} прицельных фото → {OUT}")


if __name__ == "__main__":
    main()
