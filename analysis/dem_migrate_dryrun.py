#!/usr/bin/env python3
"""Сухой прогон миграции конвейера GLO-30 → HMA 8 м: таблица смещений точек.

Каждый DEM-зависимый рецепт карточек POINTS исполняется на обоих рельефах;
выход — analysis/review/dem-migration.md: старая координата → новая, смещение
в метрах, изменение высоты и unc. Карточки НЕ правит (см. миграцию в
docs/koordinaty-status.md, «Незакрытые работы» п. 0).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
sys.path.insert(0, str(ROOT / "analysis/viewer"))

from geoproject import Dem, GLO_PATH, HMA_PATH  # noqa: E402
from point_recalc import compute_coord, dist_m  # noqa: E402
import build_map  # noqa: E402

OUT = ROOT / "analysis/review/dem-migration.md"
DEM_METHODS = {"cast", "c_center", "mean", "footprint"}


def run(dem, calc):
    try:
        res, _ = compute_coord(dem, calc)
        return res, None
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def main():
    old, new = Dem(GLO_PATH), Dem(HMA_PATH)
    lines = ["# Миграция конвейера на HMA 8 м: смещения точек",
             "",
             "Сухой прогон analysis/dem_migrate_dryrun.py: рецепт каждой карточки",
             "исполнен на GLO-30 (текущие числа) и на HMA 8 м (кандидат).",
             "",
             "| Точка | метод | было (карточка) | стало (HMA) | смещение, м | Δвысоты, м | unc: было → станет |",
             "|---|---|---|---|---|---|---|"]
    moved, broken, skipped = [], [], 0
    for p in build_map.POINTS:
        calc = p.get("calc")
        if not calc or calc.get("m") not in DEM_METHODS:
            skipped += 1
            continue
        res_new, err_new = run(new, calc)
        name = p["name"]
        card = f"{p['lat']:.6f}, {p['lon']:.6f}, {p['alt']} м"
        if err_new:
            lines.append(f"| {name} | {calc['m']} | {card} | **рецепт не исполнился**: {err_new} | — | — | — |")
            broken.append(name)
            continue
        d = dist_m(p["lat"], p["lon"], res_new["lat"], res_new["lon"])
        dalt = (res_new["alt"] - p["alt"]) if res_new.get("alt") is not None else None
        unc_s = (f"{p.get('unc', '—')} → {res_new['unc']:.0f}"
                 if res_new.get("unc") is not None else str(p.get("unc", "—")))
        lines.append(
            f"| {name} | {calc['m']} | {card} "
            f"| {res_new['lat']:.6f}, {res_new['lon']:.6f}, "
            f"{'' if res_new.get('alt') is None else format(res_new['alt'], '.0f')} м "
            f"| {d:.1f} | {'' if dalt is None else format(dalt, '+.0f')} | {unc_s} |")
        if d > 2.0:
            moved.append((d, name))
    moved.sort(reverse=True)
    lines += ["",
              f"Итог: DEM-зависимых точек {len(moved) + len(broken) + 0}+, "
              f"сдвинулись (>2 м): {len(moved)}, рецепт сломался: {len(broken)}, "
              f"DEM-независимых пропущено: {skipped}.",
              ""]
    if moved:
        lines.append("Крупнейшие смещения: " +
                     "; ".join(f"{n} — {d:.0f} м" for d, n in moved[:5]) + ".")
    if broken:
        lines.append("Требуют ручного пересмотра рецепта (луч не находит склон на HMA): " +
                     "; ".join(broken) + ".")
    OUT.write_text("\n".join(lines) + "\n", "utf-8")
    print("\n".join(lines[7:]))
    print(f"→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
