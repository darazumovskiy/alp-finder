#!/usr/bin/env python3
"""План Б: модель сцены 16.08 из телеметрии + триангуляция + BA.

Инкрементальный mapper хрупок на телесъёмке; здесь позы камер берутся из
телеметрии (метры/градусы точности — этого достаточно как начальное
приближение), в них триангулируются готовые матчи базы, и bundle adjustment
уточняет всё разом. Прицельные JPG polanski пропускаются (нет телеметрии).

Мировая система: локальная ENU в метрах с центром в сцене — сразу пригодна
для привязки вьюера и сплатов (масштаб честный, метры).
"""
import math
import sys
from pathlib import Path

import numpy as np
import pycolmap

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "analysis"))
from geoproject import at, load_rows  # noqa: E402
from build_ortho import load_cov, focal_at  # noqa: E402

BASE = Path(__file__).resolve().parent / "data/koshki-1608"
OUT = BASE / "sparse-telem"
SCENE = 39.482535, 73.585930, 4596.0
W, H = 1920, 1080
M_LAT = 111132.0
M_LON = 111320.0 * math.cos(math.radians(SCENE[0]))


def enu(lat, lon, alt):
    return np.array([(lon - SCENE[1]) * M_LON, (lat - SCENE[0]) * M_LAT,
                     alt - SCENE[2]])


def cam_pose(lat, lon, alt, yaw, pitch):
    """cam_from_world (R, t) из позы подвеса: yaw 0=север cw, pitch минус вниз."""
    yr, pr = math.radians(yaw), math.radians(pitch)
    cp = math.cos(pr)
    fwd = np.array([math.sin(yr) * cp, math.cos(yr) * cp, math.sin(pr)])
    up = np.array([0.0, 0.0, 1.0])
    right = np.cross(fwd, up)
    right /= np.linalg.norm(right)
    down = np.cross(fwd, right)
    R = np.stack([right, down, fwd])          # строки: оси камеры в мире
    C = enu(lat, lon, alt)
    return R, -R @ C


def quat_wxyz(R):
    """Кватернион (w, x, y, z) из матрицы поворота."""
    tr = R[0, 0] + R[1, 1] + R[2, 2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        return [0.25 * s, (R[2, 1] - R[1, 2]) / s,
                (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s]
    i = int(np.argmax([R[0, 0], R[1, 1], R[2, 2]]))
    j, k = (i + 1) % 3, (i + 2) % 3
    s = math.sqrt(R[i, i] - R[j, j] - R[k, k] + 1.0) * 2
    q = [0.0, 0.0, 0.0, 0.0]
    q[0] = (R[k, j] - R[j, k]) / s
    q[i + 1] = 0.25 * s
    q[j + 1] = (R[j, i] + R[i, j]) / s
    q[k + 1] = (R[k, i] + R[i, k]) / s
    return q


def main():
    db_path = BASE / "database.db"
    db = pycolmap.Database.open(str(db_path))
    images = db.read_all_images()
    db.close()
    rows_cache, cov_cache = {}, {}
    init = BASE / "sparse-telem-init"
    init.mkdir(parents=True, exist_ok=True)
    cams_f, imgs_f = [], []
    n_ok = 0
    for im in images:
        name = im.name
        if "_t" not in name:
            continue                          # прицельные JPG — без телеметрии
        stem, t_s = name.rsplit("_t", 1)
        t = float(t_s.replace(".jpg", ""))
        if stem not in rows_cache:
            video = next(iter((ROOT / "data/drive").rglob(stem + ".MP4")), None)
            rows_cache[stem] = load_rows(video) if video else None
            cov_cache[stem] = load_cov(video) if video else []
        rows = rows_cache[stem]
        if rows is None:
            continue
        pose = [at(rows, t, k) for k in ("lat", "lon", "alt_m", "gb_yaw", "gb_pitch")]
        f = focal_at(cov_cache[stem], t)
        if not all(math.isfinite(v) for v in pose) or not f:
            continue
        n_ok += 1
        cam_id = im.camera_id          # те же id, что в базе: авто-риги совпадут
        cams_f.append(f"{cam_id} SIMPLE_RADIAL {W} {H} {f:.1f} {W/2} {H/2} 0")
        R, tvec = cam_pose(*pose)
        q = quat_wxyz(R)
        imgs_f.append(f"{im.image_id} {q[0]:.9f} {q[1]:.9f} {q[2]:.9f} {q[3]:.9f} "
                      f"{tvec[0]:.4f} {tvec[1]:.4f} {tvec[2]:.4f} {cam_id} {name}")
        imgs_f.append("")                     # пустая строка точек 2D
    (init / "cameras.txt").write_text("\n".join(cams_f) + "\n")
    (init / "images.txt").write_text("\n".join(imgs_f) + "\n")
    (init / "points3D.txt").write_text("")
    rec = pycolmap.Reconstruction(str(init))
    print(f"камер из телеметрии: {n_ok}, в модели {rec.num_images()}", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    opts = pycolmap.IncrementalPipelineOptions()
    opts.triangulation.ignore_two_view_tracks = False
    rec2 = pycolmap.triangulate_points(
        rec, str(BASE / "database-norig.db"), str(BASE / "images"), str(OUT),
        options=opts)
    print(f"после триангуляции: {rec2.num_points3D()} точек", flush=True)

    ba = pycolmap.BundleAdjustmentOptions()
    ba.refine_focal_length = True
    ba.refine_extra_params = True
    pycolmap.bundle_adjustment(rec2, ba)
    rec2.write(str(OUT))
    err = rec2.compute_mean_reprojection_error()
    print(f"после BA: {rec2.num_reg_images()} камер, {rec2.num_points3D()} точек, "
          f"репроекция {err:.2f} px", flush=True)


if __name__ == "__main__":
    main()
