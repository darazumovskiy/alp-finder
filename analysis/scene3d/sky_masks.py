#!/usr/bin/env python3
"""Маски неба для кадров сцены: луч выше горизонта рельефа → прозрачность.

Для каждого зарегистрированного кадра модели строится альфа-канал: пиксели,
чей луч (по позе и фокусному ИЗ МОДЕЛИ, не из телеметрии) не встречает DEM
в пределах 6 км — небо, альфа 0. Brush с прозрачными входами учит фон в ноль
(l1 по альфе), гигантские «облака» неба в сцене исчезают.

Сетка лучей — каждый 8-й пиксель, маска растягивается билинейно и режется
порогом; края чуть эродируются, чтобы не съесть гребень. Выход — PNG с
альфой в images/ нового датасета + модель с переименованными в .png кадрами.

Использование: sky_masks.py SPARSE_DIR OUT_DATASET_DIR
"""
import math
import sys
from pathlib import Path

import cv2
import numpy as np
import pycolmap

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "analysis"))
from geoproject import Dem  # noqa: E402
from build_ortho import cast_vec  # noqa: E402

BASE = Path(__file__).resolve().parent / "data/koshki-1608"
SCENE = 39.482535, 73.585930, 4596.0
M_LAT = 111132.0
M_LON = 111320.0 * math.cos(math.radians(SCENE[0]))
STEP = 8


def main():
    sparse, out_root = Path(sys.argv[1]), Path(sys.argv[2])
    rec = pycolmap.Reconstruction(str(sparse))
    dem = Dem()
    out_img = out_root / "images"
    out_img.mkdir(parents=True, exist_ok=True)

    for n, img in enumerate(sorted(rec.images.values(), key=lambda i: i.name)):
        cam = rec.cameras[img.camera_id]
        w, h = cam.width, cam.height
        f = cam.focal_length
        cfw = img.cam_from_world()
        R = np.asarray(cfw.rotation.matrix())          # мир→камера
        C = np.asarray(img.projection_center())        # ENU центра сцены
        lat0 = SCENE[0] + C[1] / M_LAT
        lon0 = SCENE[1] + C[0] / M_LON
        alt0 = SCENE[2] + C[2]

        us = np.arange(0, w, STEP, dtype=np.float64) + STEP / 2
        vs = np.arange(0, h, STEP, dtype=np.float64) + STEP / 2
        uu, vv = np.meshgrid(us, vs)
        # луч в камере: ((u-cx)/f, (v-cy)/f, 1) → мир: R^T @ d
        d_cam = np.stack([(uu.ravel() - w / 2) / f,
                          (vv.ravel() - h / 2) / f,
                          np.ones(uu.size)], axis=1)
        d_w = d_cam @ R                                # (N,3) ENU
        d_w /= np.linalg.norm(d_w, axis=1, keepdims=True)

        _, _, dist = cast_vec(dem, lat0, lon0, alt0, d_w)
        ground = np.isfinite(dist).reshape(uu.shape).astype(np.float32)
        mask = cv2.resize(ground, (w, h), interpolation=cv2.INTER_LINEAR)
        mask = (mask > 0.55).astype(np.uint8)
        # лёгкая дилатация земли: не откусывать гребень
        mask = cv2.dilate(mask, np.ones((9, 9), np.uint8))

        src = cv2.imread(str(BASE / "images" / img.name))
        rgba = np.dstack([src, mask * 255])
        new_name = img.name.rsplit(".", 1)[0] + ".png"
        cv2.imwrite(str(out_img / new_name), rgba)
        img.name = new_name
        if n % 20 == 0:
            print(f"{n}: {new_name}, неба {100 * (1 - mask.mean()):.0f}%", flush=True)

    sp = out_root / "sparse/0"
    sp.mkdir(parents=True, exist_ok=True)
    rec.write(str(sp))
    print(f"датасет с масками: {out_root} ({rec.num_reg_images()} кадров)")


if __name__ == "__main__":
    main()
