#!/usr/bin/env python3
"""Выборка кадров зоны для реконструкции: по центрам кадров кеша полётов.

Быстрее и универсальнее extract_koshki: не трассирует лучи заново, а берёт
готовые сэмплы flights/*/meta.json (каждые 2 с: поза + проекция центра кадра
в рельеф). Кадр попадает в набор, если центр кадра лежит в рамке зоны и
дистанция до склона в разумных пределах. Прореживание на видео и глобально.

    extract_zone.py ИМЯ LAT_S LAT_N LON_W LON_E [DIST_MAX] [PER_VIDEO] [TOTAL]

Выход: data/<ИМЯ>/images/*.jpg (1920, резкие).
"""
import json
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[2]
FLIGHTS = ROOT / "analysis/viewer/flights"
LAP_MIN = 25.0


def main():
    name = sys.argv[1]
    lat_s, lat_n, lon_w, lon_e = (float(a) for a in sys.argv[2:6])
    dist_max = float(sys.argv[6]) if len(sys.argv) > 6 else 2500.0
    per_video = int(sys.argv[7]) if len(sys.argv) > 7 else 60
    total_cap = int(sys.argv[8]) if len(sys.argv) > 8 else 700
    out = Path(__file__).resolve().parent / "data" / name / "images"
    out.mkdir(parents=True, exist_ok=True)

    picked = []
    for mp in sorted(FLIGHTS.glob("*/meta.json")):
        meta = json.loads(mp.read_text())
        stem = mp.parent.name
        times = []
        for s in meta.get("samples", []):
            ctr = s[8] if len(s) > 8 else None
            if not ctr:
                continue
            if lat_s <= ctr[0] <= lat_n and lon_w <= ctr[1] <= lon_e \
                    and ctr[2] <= dist_max:
                times.append(s[0])
        if not times:
            continue
        step = max(1, len(times) // per_video)
        picked.extend((stem, t) for t in times[::step])
    if len(picked) > total_cap:
        step = len(picked) / total_cap
        picked = [picked[int(i * step)] for i in range(total_cap)]
    print(f"кандидатов: {len(picked)} из {len(set(p[0] for p in picked))} роликов",
          flush=True)

    n = 0
    by_video = {}
    for stem, t in picked:
        by_video.setdefault(stem, []).append(t)
    for stem, ts in sorted(by_video.items()):
        video = next(iter((ROOT / "data/drive").rglob(stem + ".MP4")), None)
        if video is None:
            continue
        cap = cv2.VideoCapture(str(video))
        for t in ts:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, img = cap.read()
            if not ok:
                continue
            g = cv2.cvtColor(cv2.resize(img, (640, 360)), cv2.COLOR_BGR2GRAY)
            if cv2.Laplacian(g, cv2.CV_32F).var() < LAP_MIN:
                continue
            cv2.imwrite(str(out / f"{stem}_t{t:07.1f}.jpg"), img,
                        [cv2.IMWRITE_JPEG_QUALITY, 95])
            n += 1
        cap.release()
        print(f"{stem}: +{len(ts)}", flush=True)
    print(f"итого кадров: {n} → {out}", flush=True)


if __name__ == "__main__":
    main()
