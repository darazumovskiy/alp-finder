#!/usr/bin/env python3
"""Подготовка зон к плотному этапу (PatchMatch на CUDA у C C).

Для каждой зоны с готовой sparse-моделью: выбирается модель с максимумом
камер, кадры андисторятся по ней (CPU, pycolmap) в рабочее пространство
COLMAP `data/<зона>/dense/` — ровно то, что ест
`colmap patch_match_stereo` (см. analysis/scene3d/README.md, шаг 4).

После возврата карт глубины от C C: stereo_fusion (CPU) -> fuse_dense.py ->
поверхность+орто (src/drape.py) -> вставка «Полёт 3D» (scene3d_insert.py).

Запуск: analysis/.venv/bin/python analysis/scene3d/prep_dense.py [зона ...]
        (без аргументов — все зоны по списку приоритета)
"""
import shutil
import sys
from pathlib import Path

import pycolmap

DATA = Path(__file__).resolve().parent / "data"
# порядок = поисковая ценность: сцены находок, потом линия падения сверху вниз
ZONES = ["koshki-1608", "veshchi-15", "stena-peak", "gora-obzor"] + \
        [f"linia-{i:02d}" for i in range(1, 14)]
MAX_IMAGE = 1600      # предел PatchMatch у C C — крупнее андистортить незачем


def best_model(zone: Path):
    best, bn = None, 0
    for d in sorted(zone.glob("sparse*")):
        cands = [d] if (d / "images.bin").exists() else \
            sorted(p for p in d.iterdir() if p.is_dir()) if d.is_dir() else []
        for c in cands:
            if not (c / "images.bin").exists():
                continue
            try:
                r = pycolmap.Reconstruction(str(c))
            except Exception:
                continue
            if r.num_reg_images() > bn:
                best, bn = c, r.num_reg_images()
    return best, bn


def main():
    names = sys.argv[1:] or ZONES
    total = 0
    for name in names:
        zone = DATA / name
        out = zone / "dense"
        if (out / "images").exists():
            print(f"{name}: dense уже подготовлен, пропуск")
            continue
        model, n = best_model(zone)
        if model is None or n < 20:
            print(f"{name}: модели нет или слабая ({n} камер) — пропуск")
            continue
        print(f"{name}: модель {model.relative_to(zone)}, камер {n} — андисторт…",
              flush=True)
        if out.exists():
            shutil.rmtree(out)
        opts = pycolmap.UndistortCameraOptions()
        opts.max_image_size = MAX_IMAGE
        pycolmap.undistort_images(str(out), str(model), str(zone / "images"),
                                  undistort_options=opts)
        size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
        total += size
        print(f"{name}: dense/ готов, {size // 2**20} МБ", flush=True)
    print(f"итого подготовлено: {total // 2**20} МБ")


if __name__ == "__main__":
    main()
