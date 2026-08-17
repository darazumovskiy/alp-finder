#!/usr/bin/env python3
"""Сборка рабочего DEM из тайла NASA HMA 8 м (стереопары WorldView, ~2017).

Вход:  data/dem/HMA_DEM8m_MOS_20170716_tile-203.tif — проекция Альберса, 8 м.
Выход: data/dem/hma8m_kurumdy.tif — сетка широта/долгота (EPSG:4326) с шагом
       ~8 м по нашему району, дыры и зоны вне тайла залиты GLO-30.

Система высот. HMA отсчитывает высоты от эллипсоида WGS84, GLO-30 — от геоида
(EGM2008); в нашем районе разница систематическая, ~30 м. Весь конвейер проекта
(телеметрия MSL, валидация cast по рюкзаку, DSM-патчи) живёт в системе GLO-30,
поэтому HMA приводится к ней: из него вычитается медианная разница
«HMA − GLO-30» по всей рамке (робастна: скалы доминируют над ледниками).

Формат выходного файла совместим с analysis/geoproject.py::_read_geotiff
(ModelPixelScaleTag + ModelTiepointTag, float32). Скачивание тайла — по токену
NASA Earthdata (nasa.token.txt в корне, в git не попадает):

    TOKEN=$(tr -d ' \n\r' < nasa.token.txt)
    curl -sL -H "Authorization: Bearer $TOKEN" -o data/dem/HMA_DEM8m_MOS_20170716_tile-203.tif \
      "https://data.nsidc.earthdatacloud.nasa.gov/nsidc-cumulus-prod-protected/HMA/HMA_DEM8m_MOS/1/2002/01/28/HMA_DEM8m_MOS_20170716_tile-203.tif"

Оговорка по свежести: съёмка ~2017 года — скальный рельеф верен, ледники и
снежники с тех пор просели (см. docs/dem-patches.md — патчи 2026 г. точнее
в зоне вещей и накладываются ПОВЕРХ этого DEM классом Dem).
"""

from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data/dem/HMA_DEM8m_MOS_20170716_tile-203.tif"
GLO = ROOT / "data/dem/N39E073.tif"
OUT = ROOT / "data/dem/hma8m_kurumdy.tif"

# Рамка с запасом вокруг района операции (маршрут, коридор, зона поиска).
LAT0, LAT1 = 39.42, 39.56
LON0, LON1 = 73.51, 73.67
DLAT = 7.2e-05   # ~8.0 м
DLON = 9.3e-05   # ~8.0 м на широте 39.5°


def main() -> None:
    h = int(round((LAT1 - LAT0) / DLAT))
    w = int(round((LON1 - LON0) / DLON))
    dst_transform = from_origin(LON0, LAT1, DLON, DLAT)
    hma = np.full((h, w), np.nan, np.float32)

    with rasterio.open(SRC) as src:
        reproject(
            rasterio.band(src, 1), hma,
            dst_transform=dst_transform, dst_crs="EPSG:4326",
            resampling=Resampling.bilinear,
            src_nodata=src.nodata, dst_nodata=np.nan)

    with rasterio.open(GLO) as glo:
        base = np.full((h, w), np.nan, np.float32)
        reproject(
            rasterio.band(glo, 1), base,
            dst_transform=dst_transform, dst_crs="EPSG:4326",
            resampling=Resampling.bilinear,
            src_nodata=glo.nodata, dst_nodata=np.nan)

    both = np.isfinite(hma) & np.isfinite(base)
    diff = hma[both] - base[both]
    offset = float(np.median(diff))
    holes = np.isfinite(base) & ~np.isfinite(hma)
    print(f"сетка {w}x{h}, HMA покрывает {both.mean():.1%}, дыр {holes.mean():.2%}")
    print(f"HMA − GLO-30: медиана {offset:+.1f} м, "
          f"p5 {np.percentile(diff, 5):+.1f}, p95 {np.percentile(diff, 95):+.1f}")

    out = np.where(np.isfinite(hma), hma - offset, base).astype(np.float32)
    if not np.isfinite(out).all():
        raise SystemExit("остались дыры без данных даже в GLO-30")

    with rasterio.open(
            OUT, "w", driver="GTiff", height=h, width=w, count=1,
            dtype="float32", crs="EPSG:4326", transform=dst_transform,
            compress="lzw") as dst:
        dst.write(out, 1)
    print(f"записан {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e6:.1f} МБ)")


if __name__ == "__main__":
    main()
