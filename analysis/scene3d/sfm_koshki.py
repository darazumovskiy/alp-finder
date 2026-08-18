#!/usr/bin/env python3
"""Sparse-реконструкция сцены 16.08: pycolmap на CPU.

SIFT по всем кадрам → exhaustive matching (три ролика должны сшиться в одну
модель, поэтому не sequential) → incremental mapping. Камера на кадр своя
(SIMPLE_RADIAL): в роликах зум плавает. Выход — data/koshki-1608/sparse/<i>,
печатает сводку по подмоделям.
"""
import sys
from pathlib import Path

import pycolmap

BASE = Path(__file__).resolve().parent / "data/koshki-1608"
IMAGES = BASE / "images"
DB = BASE / "database.db"
SPARSE = BASE / "sparse"


def main():
    SPARSE.mkdir(parents=True, exist_ok=True)
    if not DB.exists():
        print("== SIFT ==", flush=True)
        ext = pycolmap.FeatureExtractionOptions()
        ext.max_image_size = 1920
        ext.num_threads = -1
        pycolmap.extract_features(str(DB), str(IMAGES),
                                  camera_model="SIMPLE_RADIAL",
                                  extraction_options=ext)
        print("== matching (exhaustive) ==", flush=True)
        pycolmap.match_exhaustive(str(DB))
    print("== mapping ==", flush=True)
    recs = pycolmap.incremental_mapping(str(DB), str(IMAGES), str(SPARSE))
    for i, rec in recs.items():
        print(f"модель {i}: {rec.num_reg_images()} камер, "
              f"{rec.num_points3D()} точек", flush=True)
    if not recs:
        print("реконструкция не собралась", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
