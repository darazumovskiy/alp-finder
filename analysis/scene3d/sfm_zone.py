#!/usr/bin/env python3
"""SfM зоны: SIFT + exhaustive + инкрементальная сборка с порогами под телевик.

    sfm_zone.py ИМЯ [MAX_MODELS]

Данные: data/<ИМЯ>/images → data/<ИМЯ>/{database.db, sparse/}.
Пороги — уроки сцены 16.08 (см. docs/kontseptsiya-3d-vizualizatsii.md).
"""
import sys
from pathlib import Path

import pycolmap


def main():
    name = sys.argv[1]
    max_models = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    base = Path(__file__).resolve().parent / "data" / name
    db = base / "database.db"
    sparse = base / "sparse"
    sparse.mkdir(exist_ok=True)

    if not db.exists():
        print("== SIFT ==", flush=True)
        ext = pycolmap.FeatureExtractionOptions()
        ext.max_image_size = 1920
        ext.num_threads = -1
        pycolmap.extract_features(str(db), str(base / "images"),
                                  camera_model="SIMPLE_RADIAL",
                                  extraction_options=ext)
        print("== matching ==", flush=True)
        pycolmap.match_exhaustive(str(db))

    print("== mapping ==", flush=True)
    o = pycolmap.IncrementalPipelineOptions()
    o.mapper.init_min_tri_angle = 4.0
    o.mapper.init_max_reg_trials = 4
    o.mapper.abs_pose_min_inlier_ratio = 0.15
    o.min_model_size = 10
    o.max_num_models = max_models
    recs = pycolmap.incremental_mapping(str(db), str(base / "images"),
                                        str(sparse), options=o)
    best = None
    for i, rec in recs.items():
        print(f"модель {i}: {rec.num_reg_images()} камер, "
              f"{rec.num_points3D()} точек", flush=True)
        if best is None or rec.num_reg_images() > recs[best].num_reg_images():
            best = i
    print(f"лучшая: {best}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
