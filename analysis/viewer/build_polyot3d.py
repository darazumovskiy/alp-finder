#!/usr/bin/env python3
"""«Полёт 3D»: воксельный облёт района на движке kurumdy-3d с нашими данными.

Движок — открытая страница Николая (@nickoliuzzz, github.io/kurumdy-3d):
самописный WebGL2 без библиотек, полёт WASD, прицел, карточки объектов.
Здесь он используется как шаблон (polyot3d_template.html, вырезан payload),
а данные собираются из нашего конвейера:

  рельеф    — NASA HMA 8 м + DSM-патчи 2026 г. (Dem, как весь конвейер);
  слои      — тень, крутизна 45-55°/>55°, кулуары, горизонтали 100 м —
              предрасчёт здесь, в формате движка (байтовые сетки);
  покрытие  — 3 уровня по всем роликам (analysis/coverage/coverage-map-cells.json);
  объекты   — реестр точек build_map.POINTS с фото-кропами (этап 3);
  дрон      — треки и камеры из телеметрии (этап 4).

Контракт payload (сетка метрическая, шаг BX от СЗ-угла LAT1/LON0):
  lev  uint16[H*W]  (высота-zmin)/BZ;  shade, slope — байт на ячейку;
  meta биты: 0-2 класс ручной карты (не используем), 3 — горизонталь,
  4-5 — крутизна (1=35-45, 2=45-55, 3=>55), 6 — кулуар;  acov 0..3 —
  покрытие (3=детально… 1=обзорно);  lat = LAT1 - y*BX/MLA,
  lon = LON0 + x*BX/MLO;  линии — [[x, уровень, y], …].

Запуск: analysis/.venv/bin/python analysis/viewer/build_polyot3d.py
Выход:  analysis/viewer/polyot-3d.html
"""

import base64
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
sys.path.insert(0, str(HERE))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from geoproject import Dem, HMA_PATH, _bilinear  # noqa: E402
from build_map import CAMPS, parse_gpx  # noqa: E402

OUT = HERE / "polyot-3d.html"
TEMPLATE = HERE / "polyot3d_template.html"

# Рамка полёта: район операции с запасом (внутри рамки HMA-сетки)
LAT0, LAT1 = 39.450, 39.528
LON0, LON1 = 73.552, 73.648
BX = 8.0      # метров на воксель по горизонтали
BZ = 2.5      # метров на уровень по высоте
MLA = 111320.0
MLO = 111320.0 * math.cos(math.radians((LAT0 + LAT1) / 2))


def b64(arr) -> str:
    return base64.b64encode(arr.tobytes()).decode()


def terrain(dem):
    """Высоты (с патчами) на метрической сетке движка: z[H, W], метры."""
    w = int(round((LON1 - LON0) * MLO / BX))
    h = int(round((LAT1 - LAT0) * MLA / BX))
    lons = LON0 + (np.arange(w) + 0.5) * BX / MLO
    lats = LAT1 - (np.arange(h) + 0.5) * BX / MLA
    glon, glat = np.meshgrid(lons, lats)
    z = _bilinear(dem.z, (glon - dem.lon0) / dem.dlon,
                  (dem.lat0 - glat) / dem.dlat)
    if dem._patch is not None:
        off, plon0, plat0, pdlon, pdlat = dem._patch
        v = _bilinear(off, (glon - plon0) / pdlon, (plat0 - glat) / pdlat)
        z = np.where(np.isfinite(v), z + v, z)
    assert np.isfinite(z).all(), "рамка полёта вышла за рамку DEM"
    return z.astype(np.float32), w, h


def layers(z):
    """(shade, slope_deg, meta) — байтовые сетки движка."""
    gy, gx = np.gradient(z.astype(np.float64), BX)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))

    # тень: классический hillshade, солнце с СЗ (аз. 315°), высота 45°
    az, alt = math.radians(315), math.radians(45)
    aspect = np.arctan2(-gx, gy)
    sl = np.arctan(np.hypot(gx, gy))
    shade = (np.sin(alt) * np.cos(sl)
             + np.cos(alt) * np.sin(sl) * np.cos(az - aspect))
    shade = np.clip(shade, 0.05, 1.0)

    meta = np.zeros(z.shape, np.uint8)
    # бит 3: горизонталь через 100 м (смена сотни против соседа слева/сверху)
    hund = np.floor(z / 100.0)
    contour = np.zeros(z.shape, bool)
    contour[:, 1:] |= hund[:, 1:] != hund[:, :-1]
    contour[1:, :] |= hund[1:, :] != hund[:-1, :]
    meta[contour] |= 8
    # биты 4-5: класс крутизны
    st = np.zeros(z.shape, np.uint8)
    st[slope >= 35] = 1
    st[slope >= 45] = 2
    st[slope >= 55] = 3
    meta |= st << 4
    # бит 6: кулуар — вогнутость >4 м в окне ~90 м при уклоне >28°
    win = int(round(90 / BX)) | 1
    mean = cv2.blur(z, (win, win))
    meta[(mean - z > 4.0) & (slope > 28)] |= 64

    return ((shade * 255).astype(np.uint8),
            np.clip(slope, 0, 90).astype(np.uint8), meta)


def to_xy(lat, lon):
    return (round((lon - LON0) * MLO / BX, 2), round((LAT1 - lat) * MLA / BX, 2))


def line3d(dem, pts, zmin, every=1):
    out = []
    for lat, lon in pts[::every]:
        try:
            z = dem.elev(lat, lon)
        except ValueError:
            continue
        x, y = to_xy(lat, lon)
        if 0 <= x <= (LON1 - LON0) * MLO / BX and 0 <= y <= (LAT1 - LAT0) * MLA / BX:
            out.append([x, round((z - zmin) / BZ + 2), y])
    return out


def build():
    dem = Dem(HMA_PATH)
    z, w, h = terrain(dem)
    zmin = math.floor(z.min() / BZ) * BZ
    lev = np.round((z - zmin) / BZ).astype(np.uint16)
    shade, slope, meta = layers(z)
    acov = np.zeros(z.shape, np.uint8)          # этап 2: покрытие съёмкой

    camps, wpts = [], []
    for name, lat, lon, _alt in CAMPS:
        x, y = to_xy(lat, lon)
        if not (0 <= x < w and 0 <= y < h):
            continue
        zc = dem.elev(lat, lon)
        lv = round((zc - zmin) / BZ)
        camps.append([x, y, lv, name, round(zc)])
        wpts.append([x, y, lv, name])

    track, _ = parse_gpx(ROOT / "docs/marshrut/plan-track.gpx")
    lines = dict(route=line3d(dem, track, zmin, every=2),
                 fall=[], prio=[], corridor=[])

    payload = dict(
        W=w, H=h, BX=BX, BZ=BZ, zmin=zmin,
        LAT0=LAT0, LAT1=LAT1, LON0=LON0, LON1=LON1, MLA=MLA, MLO=MLO,
        lev=b64(lev), shade=b64(shade), slope=b64(slope),
        meta=b64(meta), acov=b64(acov),
        tex_a="", tex_b="",
        apts=[], kinds=[], statuses=[], lines=lines,
        camps=camps, wpts=wpts,
        items=[], finds=[], shel=[], cams=[], fan=[])

    html = TEMPLATE.read_text("utf-8").replace(
        "__PAYLOAD__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    OUT.write_text(html, "utf-8")
    print(f"{OUT.name}: {OUT.stat().st_size / 2**20:.1f} МБ, сетка {w}x{h}, "
          f"высоты {z.min():.0f}-{z.max():.0f} м, лагерей {len(camps)}, "
          f"маршрут {len(lines['route'])} тчк")


if __name__ == "__main__":
    build()
