#!/usr/bin/env python3
"""Дожим модели сцены 16.08: итерации триангуляция↔BA + дорегистрация кадров.

Телеметрийный скелет (91 камера, ~1 тыс. точек) недобирает точек: позы были
неточны, большинство матчей не прошло пороги. По кругу — триангуляция с
уточнёнными позами, BA, снова триангуляция — пока число точек не встанет.
Затем incremental_mapping с input_path регистрирует остальные кадры (141704
с гейтнутым фокусным, прицельные JPG) по 2D-3D соответствиям — телеметрия
им уже не нужна. Выход: sparse-final/0.
"""
import sys
from pathlib import Path

import pycolmap

BASE = Path(__file__).resolve().parent / "data/koshki-1608"
DB = BASE / "database-norig.db"
IMAGES = BASE / "images"


def stats(rec, tag):
    per = {}
    for img in rec.images.values():
        stem = img.name.split("_t")[0] if "_t" in img.name else "JPG"
        per[stem] = per.get(stem, 0) + 1
    err = rec.compute_mean_reprojection_error()
    print(f"{tag}: камер {rec.num_reg_images()}, точек {rec.num_points3D()}, "
          f"репроекция {err:.2f} px, по источникам {per}", flush=True)


def main():
    rec = pycolmap.Reconstruction(str(BASE / "sparse-telem"))
    stats(rec, "старт")
    opts = pycolmap.IncrementalPipelineOptions()
    ba = pycolmap.BundleAdjustmentOptions()
    ba.refine_focal_length = True
    ba.refine_extra_params = True
    prev = 0
    for it in range(1, 6):
        rec = pycolmap.triangulate_points(
            rec, str(DB), str(IMAGES), str(BASE / "sparse-telem"), options=opts)
        pycolmap.bundle_adjustment(rec, ba)
        stats(rec, f"итерация {it}")
        n = rec.num_points3D()
        if n < prev * 1.05:
            break
        prev = n
    rec.write(str(BASE / "sparse-telem"))

    out = BASE / "sparse-final"
    out.mkdir(exist_ok=True)
    mo = pycolmap.IncrementalPipelineOptions()
    mo.mapper.init_min_tri_angle = 4.0
    mo.mapper.abs_pose_min_inlier_ratio = 0.15
    recs = pycolmap.incremental_mapping(
        str(DB), str(IMAGES), str(out), options=mo,
        input_path=str(BASE / "sparse-telem"))
    for i, r in recs.items():
        stats(r, f"финал {i}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
