#!/usr/bin/env python3
"""Сборка модели сцены 16.08 по готовой базе матчей — пороги под телесъёмку.

Дефолтный init_min_tri_angle=16° рассчитан на широкоугольные кадры; у нашего
зума (FOV единицы градусов, дистанции 30-100 м) углы триангуляции меньше на
порядок — инициализация не находилась, mapper час толок воду в ступе
(подмодели по 10-12 кадров). Здесь пороги ослаблены; матчинг не повторяется.
"""
import sys
from pathlib import Path

import pycolmap

BASE = Path(__file__).resolve().parent / "data/koshki-1608"


def main():
    out = BASE / "sparse"
    out.mkdir(parents=True, exist_ok=True)
    o = pycolmap.IncrementalPipelineOptions()
    o.mapper.init_min_tri_angle = 4.0     # телеобъектив: углы мал.
    o.mapper.init_max_reg_trials = 4
    o.mapper.abs_pose_min_inlier_ratio = 0.15
    o.min_model_size = 8
    recs = pycolmap.incremental_mapping(str(BASE / "database.db"),
                                        str(BASE / "images"), str(out),
                                        options=o)
    best = None
    for i, rec in recs.items():
        print(f"модель {i}: {rec.num_reg_images()} камер, "
              f"{rec.num_points3D()} точек", flush=True)
        if best is None or rec.num_reg_images() > recs[best].num_reg_images():
            best = i
    if best is None:
        print("не собралось", file=sys.stderr)
        return 1
    print(f"лучшая модель: {best} → {out}/{best}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
