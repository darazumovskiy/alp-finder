#!/usr/bin/env python3
"""Пары «полный кадр с обводкой» + «крупный кроп» для показа кандидата человеку.

Раздел 6 скилла priyom-novykh-video требует по каждому кандидату уверенности 3+
именно пару картинок: полный кадр, где тонкой красной линией обведено место, и
отдельный крупный кроп того же объекта. Скрипт делает обе из исходного видео по
таймкоду и bbox из tracks.tsv.

Использование:
  .venv/bin/python make_finding_images.py --video <путь.MP4> --t <секунды> \
      --bbox x,y,w,h --name <имя> --out review/findings-<дата>/

Выход: <out>/<имя>_full.jpg и <out>/<имя>_crop.jpg.
"""

import argparse
import subprocess
import tempfile
from pathlib import Path

import cv2

PAD = 2.5        # во сколько раз кроп шире bbox
MIN_CROP = 220   # минимальная сторона кропа в пикселях исходника
LINE = 2         # толщина обводки, px — «тонкая красная», как требует скилл
RED = (0, 0, 255)


def grab(video: Path, t: float):
    """Кадр видео на секунде t (ffmpeg -ss точнее, чем перемотка cv2 по зуму)."""
    with tempfile.TemporaryDirectory() as td:
        png = Path(td) / "f.png"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t}",
                        "-i", str(video), "-frames:v", "1", str(png)], check=True)
        img = cv2.imread(str(png))
    if img is None:
        raise SystemExit(f"кадр на {t} с не читается: {video}")
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--t", type=float, required=True)
    ap.add_argument("--bbox", required=True, help="x,y,w,h в пикселях кадра")
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    x, y, w, h = (int(v) for v in args.bbox.split(","))
    img = grab(Path(args.video), args.t)
    H, W = img.shape[:2]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # полный кадр: эллипс вокруг bbox с небольшим запасом
    full = img.copy()
    cx, cy = x + w // 2, y + h // 2
    ax, ay = max(int(w * 0.75), 18), max(int(h * 0.75), 18)
    cv2.ellipse(full, (cx, cy), (ax, ay), 0, 0, 360, RED, LINE, cv2.LINE_AA)
    p_full = out / f"{args.name}_full.jpg"
    cv2.imwrite(str(p_full), full, [cv2.IMWRITE_JPEG_QUALITY, 92])

    # кроп: bbox с полями, добитый до минимального размера, затем увеличение x2
    side_w = max(int(w * PAD), MIN_CROP)
    side_h = max(int(h * PAD), MIN_CROP)
    x0, y0 = max(0, cx - side_w // 2), max(0, cy - side_h // 2)
    x1, y1 = min(W, x0 + side_w), min(H, y0 + side_h)
    crop = img[y0:y1, x0:x1]
    crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    p_crop = out / f"{args.name}_crop.jpg"
    cv2.imwrite(str(p_crop), crop, [cv2.IMWRITE_JPEG_QUALITY, 95])

    print(f"{p_full}\n{p_crop}")


if __name__ == "__main__":
    main()
