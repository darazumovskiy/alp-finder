#!/usr/bin/env python3
"""Экспорт COLMAP-модели в датасет Brush: легаси-текст + PINHOLE.

pycolmap 3.13 пишет новый бинарный формат (позы во frames.bin) — Brush 0.3
его не читает; SIMPLE_RADIAL тоже вне его понимания. Здесь модель уходит
в старый текстовый формат с PINHOLE-камерами (fx=fy=f, дисторсия
отбрасывается — у телевика k ничтожен), кадры копируются рядом.

Использование: export_brush.py SPARSE_DIR IMAGES_DIR OUT_ROOT
"""
import shutil
import sys
from pathlib import Path

import pycolmap


def main():
    sparse, images_dir, root = (Path(a) for a in sys.argv[1:4])
    rec = pycolmap.Reconstruction(str(sparse))
    sp = root / "sparse/0"
    sp.mkdir(parents=True, exist_ok=True)
    (root / "images").mkdir(exist_ok=True)

    cams, imgs = [], []
    for img in sorted(rec.images.values(), key=lambda i: i.image_id):
        cam = rec.cameras[img.camera_id]
        f, cx, cy = cam.params[0], cam.params[1], cam.params[2]
        cams.append(f"{cam.camera_id} PINHOLE {cam.width} {cam.height} "
                    f"{f:.4f} {f:.4f} {cx:.2f} {cy:.2f}")
        cfw = img.cam_from_world()
        q = cfw.rotation.quat            # pycolmap: (x, y, z, w)
        t = cfw.translation
        imgs.append(f"{img.image_id} {q[3]:.9f} {q[0]:.9f} {q[1]:.9f} {q[2]:.9f} "
                    f"{t[0]:.6f} {t[1]:.6f} {t[2]:.6f} {img.camera_id} {img.name}")
        imgs.append("")
        dst = root / "images" / img.name
        if not dst.exists():
            shutil.copy2(images_dir / img.name, dst)
    (sp / "cameras.txt").write_text("\n".join(cams) + "\n")
    (sp / "images.txt").write_text("\n".join(imgs) + "\n")
    pts = []
    for pid, p in rec.points3D.items():
        c = p.color
        pts.append(f"{pid} {p.xyz[0]:.6f} {p.xyz[1]:.6f} {p.xyz[2]:.6f} "
                   f"{int(c[0])} {int(c[1])} {int(c[2])} {p.error:.3f}")
    (sp / "points3D.txt").write_text("\n".join(pts) + "\n")
    print(f"{root}: {rec.num_reg_images()} камер, {len(pts)} точек")


if __name__ == "__main__":
    main()
