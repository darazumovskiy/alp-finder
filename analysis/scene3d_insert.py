#!/usr/bin/env python3
"""Модель участка вещей (плотная фотограмметрия C C) -> вставка «Полёт 3D».

Источник — автономный вьюер автора `external/kurumdy-image-lab/out/scene3d/
kurumdy-3d.html`: в нём запечены сетка поверхности 0.25 м (СЦЕНА-JSON,
вершины ENU-метры от лазерной палки, эллипсоидальные высоты) и ортотекстура
3.2 см/тексель. Здесь они переупаковываются в mesh-вставку нашего формата:

  - вершины (e, n, alt_движка) float32 base64; вертикальный якорь — медианное
    совмещение с рельефом движка в зоне DSM-патчей (маркеры реестра сидят на
    рельефе движка, вставка обязана сидеть там же; сдвиг пишется в JSON);
  - support uint8 base64 (255 = ячейка не измерена, треугольники не рисуются);
  - плоскость текстуры (оси/границы) — uv считает страница проекцией вершин.

Координатный конвейер проекта не трогается: Dem, патчи и реестр без изменений.

Выход: analysis/viewer/ortho/insert_veshchi3d.json + insert_veshchi3d.jpg.
Запуск: analysis/.venv/bin/python analysis/scene3d_insert.py
"""
import base64
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geoproject import Dem, _bilinear  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "external/kurumdy-image-lab/out/scene3d"
OUT_DIR = ROOT / "analysis/viewer/ortho"

M_LAT = 111132.0


def load_scene():
    s = (SRC / "kurumdy-3d.html").read_text(encoding="utf-8", errors="ignore")
    a = s.find(">", s.find('<script type="application/json" id="scene"')) + 1
    return json.loads(s[a:s.find("</script>", a)])


def main():
    sc = load_scene()
    fr, su = sc["frame"], sc["surface"]
    v = np.frombuffer(base64.b64decode(su["vertices_float32_base64"]),
                      np.float32).reshape(-1, 3).copy()
    sup = np.frombuffer(base64.b64decode(su["support_uint8_base64"]), np.uint8)
    rows, cols = su["rows"], su["cols"]
    assert v.shape[0] == rows * cols == sup.shape[0]

    # вертикальный якорь: медиана (рельеф движка - модель) по измеренным
    # вершинам внутри DSM-патчей
    dem = Dem()
    m_lon = 111320.0 * np.cos(np.radians(fr["origin_lat"]))
    meas = sup <= 127
    vv = v[meas][::10]
    lat = fr["origin_lat"] + vv[:, 1] / M_LAT
    lon = fr["origin_lon"] + vv[:, 0] / m_lon
    alt_e = fr["origin_alt_ellipsoidal_m"] + vv[:, 2]
    z = _bilinear(dem.z, (lon - dem.lon0) / dem.dlon, (dem.lat0 - lat) / dem.dlat)
    off, plon0, plat0, pdlon, pdlat = dem._patch
    p = _bilinear(off, (lon - plon0) / pdlon, (plat0 - lat) / pdlat)
    inpatch = np.isfinite(p)
    dz_all = np.where(inpatch, z + p, z) - alt_e
    dz = float(np.median(dz_all[inpatch & np.isfinite(dz_all)]))
    print(f"вертикальный якорь: +{dz:.2f} м (медиана по {int(inpatch.sum())} "
          f"вершинам в патчах)")

    # вершины -> (e, n, alt_движка)
    verts = np.empty_like(v)
    verts[:, 0] = v[:, 0]
    verts[:, 1] = v[:, 1]
    verts[:, 2] = fr["origin_alt_ellipsoidal_m"] + v[:, 2] + dz

    sf = su["frame"]
    rect = su["rect"]
    meta = dict(
        name="veshchi3d",
        title="модель вещей — плотная 3D (C C)",
        mesh=1,
        # правила плиток: только измеренные ячейки, мягкая посадка на рельеф,
        # без юбок (иначе края, висящие над ошибкой GLO-30, дают чёрные шторы)
        trim=1, tile=1,
        credit="external/kurumdy-image-lab, PR #12: COLMAP+PatchMatch, "
               "клип DJI_20260813163855, 1.54 млн точек",
        origin_lat=fr["origin_lat"], origin_lon=fr["origin_lon"],
        dz_applied_m=round(dz, 2),
        rows=rows, cols=cols,
        # локальная высота для проекции uv = alt_движка - alt0
        alt0=fr["origin_alt_ellipsoidal_m"] + dz,
        plane=dict(o=sf["origin_m"], u=sf["u"], v=sf["v"],
                   u0=rect["u_range_m"][0], u1=rect["u_range_m"][1],
                   v0=rect["v_range_m"][0], v1=rect["v_range_m"][1]),
        verts_b64=base64.b64encode(verts.astype(np.float32).tobytes()).decode(),
        support_b64=base64.b64encode(sup.tobytes()).decode(),
        tex="ortho/insert_veshchi3d.jpg")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # чёрные не отснятые куски ортотекстуры заливаются соседними тонами:
    # чёрное страница отбрасывает в прозрачность, и в мэше это дыры в небо
    tex = cv2.imread(str(SRC / "full/ortho.jpg"))
    hole = (tex.sum(axis=2) < 12).astype(np.uint8)
    tex = cv2.inpaint(tex, hole, 3, cv2.INPAINT_TELEA)
    cv2.imwrite(str(OUT_DIR / "insert_veshchi3d.jpg"), tex,
                [cv2.IMWRITE_JPEG_QUALITY, 88])
    out = OUT_DIR / "insert_veshchi3d.json"
    out.write_text(json.dumps(meta, separators=(",", ":")))
    print(f"{out.name}: {out.stat().st_size // 1024} КБ, "
          f"сетка {rows}x{cols}, текстура "
          f"{(OUT_DIR / 'insert_veshchi3d.jpg').stat().st_size // 1024} КБ")


if __name__ == "__main__":
    main()
