#!/usr/bin/env python3
"""Сверка двух моделей рельефа с независимыми эталонами: GLO-30 против HMA 8 м.

Вопрос, на который отвечает скрипт: какая модель ближе к реальной горе —
текущая расчётная (Copernicus GLO-30) или кандидат на миграцию (NASA HMA 8 м,
data/dem/hma8m_kurumdy.tif). Эталоны не зависят ни от одной из моделей:

  A. Лазерные цели дальномера (LRFTarget* из XMP обычных снимков M30T;
     SUPR-тайлы исключаются — их лазер бьёт по сетке панорамы, медиана −58 м).
     Лазер меряет реальную поверхность; ошибка самой цели — единицы метров.
  B. Фотограмметрические DSM-патчи 2026 г. (analysis/dem-patches/*.tif) —
     плотный ground truth в пятне вещей; сравниваются ГОЛЫЕ базы (патчи
     в Dem не подключаются, иначе сравнение с самим собой).
  C. Триангулированные точки (метод triang в POINTS build_map.py) — высота
     из пересечения лучей двух ракурсов, рельеф в ней не участвует.

Обе модели сравниваются с патчами наложенными (как в бою — конвейер работает
с патчами) и голыми (вклад самой базы). Выход: сводка в stdout + таблица
analysis/review/dem-benchmark.md. Решение о миграции конвейера — за
оператором по этим числам (docs/koordinaty-status.md, «Незакрытые работы» п. 0).
"""

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "analysis/viewer"))

from extract_lrf_anchors import xmp  # noqa: E402
from geoproject import Dem, GLO_PATH, HMA_PATH, _bilinear, _read_geotiff  # noqa: E402

OUT = ROOT / "analysis/review/dem-benchmark.md"


def stats(err):
    err = np.asarray(err, float)
    return (f"n={len(err)}, медиана {np.median(err):+.1f} м, "
            f"p10 {np.percentile(err, 10):+.1f}, p90 {np.percentile(err, 90):+.1f}, "
            f"|err| медиана {np.median(abs(err)):.1f} м, "
            f"промахов >20 м {np.mean(abs(err) > 20):.0%}, >40 м {np.mean(abs(err) > 40):.0%}")


def lrf_targets():
    """[(lat, lon, alt, фото)] с обычных снимков; SUPR-каталоги мимо.

    Одна цель попадает в W/Z/T-варианты одного кадра — дубли схлопываются.
    """
    rows, seen = [], set()
    for jp in sorted((ROOT / "data/drive").rglob("*.JPG")):
        if "_SUPR" in jp.parent.name:
            continue
        v = xmp(jp)
        if all(k in v for k in ("LRFTargetLat", "LRFTargetLon", "LRFTargetAbsAlt")):
            key = (round(v["LRFTargetLat"], 5), round(v["LRFTargetLon"], 5),
                   round(v["LRFTargetAbsAlt"]))
            if key in seen:
                continue
            seen.add(key)
            rows.append((v["LRFTargetLat"], v["LRFTargetLon"],
                         v["LRFTargetAbsAlt"], jp.relative_to(ROOT).as_posix()))
    return rows


def elev_or_none(dem, lat, lon):
    try:
        return dem.elev(lat, lon)
    except ValueError:
        return None


def bench_lrf(dems, lines):
    rows = lrf_targets()
    lines.append(f"\n## A. Лазерные цели (обычные снимки, без SUPR): {len(rows)} шт.\n")
    for name, dem in dems.items():
        errs, used = [], 0
        for lat, lon, alt, _ in rows:
            z = elev_or_none(dem, lat, lon)
            if z is not None:
                errs.append(z - alt)
                used += 1
        lines.append(f"- **{name}**: {stats(errs)}")
    return rows


def bench_patches(lines):
    """Голые базы против среднего DSM патчей на их собственной сетке."""
    patches = [_read_geotiff(p) for p in sorted((ROOT / "analysis/dem-patches").glob("*.tif"))]
    bases = {"GLO-30 голый": Dem(GLO_PATH, patch_dir=None),
             "HMA 8 м голый": Dem(HMA_PATH, patch_dir=None)}
    lines.append(f"\n## B. Фотограмметрические патчи 2026 г. как эталон: {len(patches)} шт.\n")
    for name, dem in bases.items():
        errs = []
        for z, plon0, plat0, pdlon, pdlat in patches:
            h, w = z.shape
            lons = plon0 + (np.arange(0, w, 4) + .5) * pdlon
            lats = plat0 - (np.arange(0, h, 4) + .5) * pdlat
            glon, glat = np.meshgrid(lons, lats)
            base = _bilinear(dem.z, (glon - dem.lon0) / dem.dlon,
                             (dem.lat0 - glat) / dem.dlat)
            truth = z[::4, ::4]
            m = np.isfinite(truth) & np.isfinite(base)
            errs.extend((base[m] - truth[m]).tolist())
        lines.append(f"- **{name}**: {stats(errs)}")


def bench_triang(dems, lines):
    from build_map import POINTS
    pts = [p for p in POINTS
           if (p.get("calc") or {}).get("m") == "triang" or p.get("fix") == "triang"]
    lines.append(f"\n## C. Триангулированные точки: {len(pts)} шт.\n")
    for p in pts:
        row = [f"- {p['name'][:60]} ({p['lat']:.6f}, {p['lon']:.6f}, {p['alt']} м):"]
        for name, dem in dems.items():
            z = elev_or_none(dem, p["lat"], p["lon"])
            row.append(f"{name} {z - p['alt']:+.1f} м" if z is not None else f"{name} вне рамки")
        lines.append(" ".join(row))


def main():
    dems = {"GLO-30 + патчи": Dem(GLO_PATH), "HMA 8 м + патчи": Dem(HMA_PATH)}
    lines = ["# Сверка рельефов с независимыми эталонами",
             "", "Сборка: analysis/dem_benchmark.py. Ошибка = модель − эталон, м."]
    bench_lrf(dems, lines)
    bench_patches(lines)
    bench_triang(dems, lines)
    text = "\n".join(lines) + "\n"
    OUT.write_text(text, "utf-8")
    print(text)
    print(f"→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
