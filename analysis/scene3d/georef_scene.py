#!/usr/bin/env python3
"""Геопривязка сплат-сцены: подобие scene→ENU по телеметрии камер.

Центры камер модели (произвольная система SfM) сопоставляются с телеметрией
тех же кадров (ENU-метры от якоря) — подобие (масштаб+поворот+сдвиг) по
Umeyama, робастно: две итерации с отбросом хвоста невязок. Кадры без
телеметрии (JPG) в подгонке не участвуют.

    georef_scene.py SPARSE_DIR OUT_JSON [ANCHOR_LAT ANCHOR_LON ANCHOR_ALT]

OUT_JSON: {"anchor": [lat, lon, alt], "T": 4x4 scene→ENU, "rms_m": ...}
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import pycolmap

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "analysis"))
from geoproject import at, load_rows  # noqa: E402

M_LAT = 111132.0


def umeyama(src, dst):
    """Подобие dst ≈ s·R·src + t; возвращает (s, R, t)."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    sc, dc = src - mu_s, dst - mu_d
    cov = dc.T @ sc / len(src)
    U, D, Vt = np.linalg.svd(cov)
    S = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[2, 2] = -1
    R = U @ S @ Vt
    var_s = (sc ** 2).sum() / len(src)
    s = np.trace(np.diag(D) @ S) / var_s
    t = mu_d - s * R @ mu_s
    return s, R, t


def main():
    sparse, out_json = Path(sys.argv[1]), Path(sys.argv[2])
    stem_filter = None
    for a in sys.argv[3:]:
        if a.startswith("--only="):
            stem_filter = a.split("=", 1)[1]
    rec = pycolmap.Reconstruction(str(sparse))

    rows_cache = {}
    pairs = []
    for img in rec.images.values():
        if "_t" not in img.name:
            continue
        if stem_filter and stem_filter not in img.name:
            continue
        stem, t_s = img.name.rsplit("_t", 1)
        t = float(t_s.rsplit(".", 1)[0])
        if stem not in rows_cache:
            video = next(iter((ROOT / "data/drive").rglob(stem + ".MP4")), None)
            rows_cache[stem] = load_rows(video) if video else None
        rows = rows_cache[stem]
        if rows is None:
            continue
        lat = at(rows, t, "lat")
        lon = at(rows, t, "lon")
        alt = at(rows, t, "alt_m")
        yaw = at(rows, t, "gb_yaw")
        pitch = at(rows, t, "gb_pitch")
        if not all(math.isfinite(v) for v in (lat, lon, alt, yaw, pitch)):
            continue
        pairs.append((np.asarray(img.projection_center()), lat, lon, alt,
                      img, yaw, pitch))
    if len(pairs) < 8:
        raise SystemExit(f"мало кадров с телеметрией: {len(pairs)}")

    if len(sys.argv) > 5:
        a_lat, a_lon, a_alt = (float(v) for v in sys.argv[3:6])
    else:
        a_lat = float(np.mean([p[1] for p in pairs]))
        a_lon = float(np.mean([p[2] for p in pairs]))
        a_alt = float(np.mean([p[3] for p in pairs]))
    m_lon = 111320.0 * math.cos(math.radians(a_lat))

    src_c = np.array([p[0] for p in pairs])
    dst_c = np.array([[(p[2] - a_lon) * m_lon, (p[1] - a_lat) * M_LAT,
                       p[3] - a_alt] for p in pairs])
    # почти коллинеарные центры (прямой подлёт) вырождают подобие — добавляем
    # псевдоточки вдоль осей взгляда: центр + forward·L в обеих системах
    L_ENU = 60.0
    scale0, _, _ = umeyama(src_c, dst_c)
    src_f, dst_f = [], []
    for (c, lat, lon, alt, img, yaw, pitch) in pairs:
        cfw = img.cam_from_world()
        R_cw = np.asarray(cfw.rotation.matrix())
        fwd_scene = R_cw.T @ np.array([0.0, 0.0, 1.0])
        yr, pr = math.radians(yaw), math.radians(pitch)
        cp = math.cos(pr)
        fwd_enu = np.array([math.sin(yr) * cp, math.cos(yr) * cp, math.sin(pr)])
        src_f.append(c + fwd_scene * (L_ENU / scale0))
        dst_f.append(0)  # заполняется ниже
    src = np.vstack([src_c, np.array(src_f)])
    dst = np.vstack([dst_c, dst_c + np.array(
        [[math.sin(math.radians(p[5])) * math.cos(math.radians(p[6])) * L_ENU,
          math.cos(math.radians(p[5])) * math.cos(math.radians(p[6])) * L_ENU,
          math.sin(math.radians(p[6])) * L_ENU] for p in pairs])])

    keep = np.ones(len(src), bool)
    for _ in range(2):
        s, R, t = umeyama(src[keep], dst[keep])
        res = np.linalg.norm((s * (R @ src.T).T + t) - dst, axis=1)
        thr = max(np.percentile(res[keep], 80) * 2.0, 3.0)
        keep = res < thr
    rms = float(np.sqrt((res[keep] ** 2).mean()))

    T = np.eye(4)
    T[:3, :3] = s * R
    T[:3, 3] = t
    out_json.write_text(json.dumps(dict(
        anchor=[round(a_lat, 7), round(a_lon, 7), round(a_alt, 2)],
        T=[round(v, 6) for v in T.flatten().tolist()],
        rms_m=round(rms, 2), used=int(keep.sum()), total=len(src))))
    print(f"{out_json.name}: масштаб {s:.4f}, камер {keep.sum()}/{len(src)}, "
          f"RMS {rms:.1f} м")


if __name__ == "__main__":
    main()
