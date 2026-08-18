#!/usr/bin/env python3
"""Дорегистрация макро-кадров (зависание 114520, прицельные JPG) в модель.

Жёсткая модель орбиты (sparse/1) фиксируется; остальные кадры локализуются
против её 3D-точек по 2D-3D соответствиям с ослабленными порогами
(масштаб макро-кадров сильно другой, инлаеров мало, но позы известной
карте хватает). Выход: sparse-reg/0 — орбита + сколько село макро-кадров.
"""
import sys
from pathlib import Path

import pycolmap

BASE = Path(__file__).resolve().parent / "data/koshki-1608"


def stats(rec, tag):
    per = {}
    for img in rec.images.values():
        stem = img.name.split("_t")[0][-8:] if "_t" in img.name else "JPG"
        per[stem] = per.get(stem, 0) + 1
    print(f"{tag}: камер {rec.num_reg_images()}, точек {rec.num_points3D()}, "
          f"{per}", flush=True)


def main():
    out = BASE / "sparse-reg"
    out.mkdir(exist_ok=True)
    o = pycolmap.IncrementalPipelineOptions()
    o.fix_existing_frames = True
    o.mapper.abs_pose_min_num_inliers = 12
    o.mapper.abs_pose_min_inlier_ratio = 0.05
    o.mapper.abs_pose_max_error = 16.0
    o.mapper.max_reg_trials = 5
    o.min_model_size = 3
    o.max_num_models = 1
    recs = pycolmap.incremental_mapping(
        str(BASE / "database-norig.db"), str(BASE / "images"), str(out),
        options=o, input_path=str(BASE / "sparse/1"))
    for i, r in recs.items():
        stats(r, f"модель {i}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
