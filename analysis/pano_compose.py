#!/usr/bin/env python3
"""Доклейка «острова» в гигапанораму по опорным точкам (когда SIFT бессилен).

Ролики, снятые вплотную или с другого ракурса, к обзорной панораме по общим
точкам не пришиваются. Если на острове есть объекты с известными координатами
(дальномер, триангуляция), он укладывается преобразованием подобия: пары
«пиксель острова ↔ мировая координата» переводятся в пары «пиксель острова ↔
пиксель панорамы» через геопривязку панорамы (трассировка центров кадров в
рельеф, как в annotate_mosaic), и по ним оценивается сдвиг+поворот+масштаб.

Точность укладки — метры (геопривязка панорамы + подобие вместо перспективы,
ракурсы разные): остров стоит на своём месте и в масштабе, но это коллажный
шов, не фотограмметрия.

Файл пар (tsv): mosaic  mx  my  lat  lon — пиксель полотна острова (пути как
в island/mosaics.tsv) и мировая координата этой точки. Минимум 2 пары.

Использование:
  .venv/bin/python pano_compose.py --base stitch/pano-veshchi \
      --island stitch/merged-ryukzak-14 --pairs ryukzak-gcp.tsv \
      --videos ../data/drive/2026-08-13/drone-part3 --out stitch/pano-veshchi-full

Выход в --out: mosaics.tsv (база + остров), extra-marks.tsv — гео-отметки
из findings-marks.tsv, попавшие в участок привязки (канвас-координаты,
рисует tile_pano.py --extra-marks).
"""

import argparse
import csv
from pathlib import Path

import cv2
import numpy as np

from annotate_mosaic import fit_world_to_canvas, ground_points, read_tsv


def read_items(tsv: Path):
    rows = read_tsv(tsv)
    return [(r["mosaic"],
             np.float64([float(r[f"h{i}{j}"]) for i in range(3)
                         for j in range(3)]).reshape(3, 3)) for r in rows]


def write_items(tsv: Path, items):
    with tsv.open("w", encoding="utf-8") as f:
        f.write("mosaic\t" + "\t".join(f"h{i}{j}" for i in range(3)
                                       for j in range(3)) + "\n")
        for p, M in items:
            f.write(str(p) + "\t" + "\t".join(f"{v:.8g}" for v in M.ravel())
                    + "\n")


def main(args):
    from geoproject import Dem
    dem = Dem()
    base = read_items(args.base / "mosaics.tsv")
    pts = []
    for p, M in base:
        pts += ground_points(Path(p), M, args.videos, dem)
    geo = fit_world_to_canvas(pts, args.thr)
    if geo is None:
        raise SystemExit("геопривязка базовой панорамы не сошлась")
    H, enu, _, world_inl, m_per_px = geo

    island = read_items(args.island / "mosaics.tsv")
    by_name = dict(island)
    src, dst = [], []
    for r in read_tsv(args.pairs):
        Mi = by_name.get(r["mosaic"])
        if Mi is None:
            raise SystemExit(f"пара ссылается на полотно вне острова: {r['mosaic']}")
        p_isl = cv2.perspectiveTransform(
            np.float64([[float(r["mx"]), float(r["my"])]]).reshape(-1, 1, 2),
            Mi).ravel()
        e, n = enu(float(r["lat"]), float(r["lon"]))
        p_base = cv2.perspectiveTransform(
            np.float64([[e, n]]).reshape(-1, 1, 2), H).ravel()
        src.append(p_isl)
        dst.append(p_base)
    S, _ = cv2.estimateAffinePartial2D(
        np.float64(src).reshape(-1, 1, 2), np.float64(dst).reshape(-1, 1, 2))
    if S is None:
        raise SystemExit("подобие по парам не оценилось (мало/вырожденные пары)")
    S3 = np.vstack([S, [0, 0, 1]])
    res = [float(np.linalg.norm(S3[:2] @ np.append(s, 1) - d))
           for s, d in zip(src, dst)]
    sc = float(np.hypot(S[0, 0], S[0, 1]))
    print(f"остров: пар {len(src)}, остатки {[f'{r:.0f}' for r in res]} px "
          f"(≈{max(res) * m_per_px:.0f} м), масштаб острова ×{sc:.2f}")

    args.out.mkdir(parents=True, exist_ok=True)
    write_items(args.out / "mosaics.tsv",
                base + [(p, S3 @ M) for p, M in island])

    # гео-отметки без пиксельного якоря — в канвас-координаты (внутри привязки)
    lo_w = world_inl.min(0)
    hi_w = world_inl.max(0)
    margin = 0.5 * (hi_w - lo_w) + 20
    with (args.out / "extra-marks.tsv").open("w", encoding="utf-8") as f:
        f.write("label\tx\ty\n")
        n_extra = 0
        for r in read_tsv(args.marks):
            if r.get("mosaic"):
                continue
            e, n = enu(float(r["lat"]), float(r["lon"]))
            if not ((lo_w - margin <= [e, n]) & ([e, n] <= hi_w + margin)).all():
                continue
            p = cv2.perspectiveTransform(
                np.float64([[e, n]]).reshape(-1, 1, 2), H).ravel()
            label = ("≈ " if r.get("approx") == "1" else "") + r["label"]
            f.write(f"{label}\t{p[0]:.0f}\t{p[1]:.0f}\n")
            n_extra += 1
    print(f"готово: {args.out / 'mosaics.tsv'} "
          f"({len(base)}+{len(island)} полотен, гео-отметок {n_extra})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", type=Path, required=True)
    ap.add_argument("--island", type=Path, required=True)
    ap.add_argument("--pairs", type=Path, required=True)
    ap.add_argument("--videos", type=Path, required=True,
                    help="папка видео+сайдкары роликов базовой панорамы")
    ap.add_argument("--marks", type=Path,
                    default=Path(__file__).parent / "findings-marks.tsv")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--thr", type=float, default=2500)
    args = ap.parse_args()
    main(args)
