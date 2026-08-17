#!/usr/bin/env python3
"""Сетка покрытия всего района съёмкой дрона: три уровня детальности для карты.

Метод — проекция рамки кадра (video_footprint_hits из coverage_polygon.py):
каждые step секунд кадр печатается в DEM сеткой лучей по всему полю зрения,
с реальным фокусным момента из analysis/coverage/<видео>.coverage.tsv.
Масштаб каждого попадания (минимальный различимый предмет, порог 8 px из
стенда врезок) — пересчитан на дистанцию конкретного луча, поэтому дальний
край кадра честно грубее ближнего. Моменты без измеренного фокусного
(рамка кадра неизвестна) — центральный луч с допуском 75 м без масштаба.

Уровни по лучшему (минимальному) различимому предмету среди всех попаданий
в ячейку:
  detail — предмет от 20 см (крышка, ботинок, каска);
  mid    — предмет от 1 м (рюкзак, лежащий человек), мелочь пропускается;
  over   — смотрели, но различимо только крупное (> 1 м) либо масштаб
           не измерен (зависание без панорам — фокусное неизвестно).

Лучи маршируются в DEM независимо, поэтому мёртвые зоны за перегибами
рельефа внутри рамки кадра не закрашиваются.

Использование:  analysis/.venv/bin/python analysis/coverage_map.py
Выход: analysis/coverage/coverage-map-cells.json — вклад каждого ролика
отдельно: ролик, дата вылета, ячейки [i, j, уровень 0/1/2, лучший
obj8px_cm | null, таймкод лучшего прохода (с), число проходов рядом].
Карта объединяет вклады на лету (фильтры по дате и ролику) и по тем же
данным отвечает на обратный вопрос: какие ролики видели данную точку
(ПКМ по карте). Сводка по всем дням — в stdout.
"""

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from coverage_polygon import (  # noqa: E402
    DATA, M_PER_DEG_LAT, m_per_deg_lon, video_footprint_hits_strict,
)
from geoproject import Dem  # noqa: E402

OUT = HERE / "coverage" / "coverage-map-cells.json"

# Рамка карты — та же, что в analysis/viewer/build_map.py
LAT0, LAT1 = 39.455, 39.525
LON0, LON1 = 73.565, 73.635

CELL_M = 15.0      # шаг сетки (до 17.08 было 30 м: на крутом склоне ячейка 30 м
                   # растягивается по вертикали на ~40 м и прячет неосмотренные
                   # углы — кейс сцены 16.08; см. otchet-2026-08-17)
STEP_S = 2.0       # шаг сэмплов телеметрии
TIER_DETAIL_CM = 20   # предмет ≤ 20 см различим
TIER_MID_CM = 100     # предмет ≤ 1 м различим


def main():
    lat_c = (LAT0 + LAT1) / 2
    dlat = CELL_M / M_PER_DEG_LAT
    dlon = CELL_M / m_per_deg_lon(lat_c)
    bbox = (LAT0, LAT1, LON0, LON1)

    ni = int((LAT1 - LAT0) / dlat)
    nj = int((LON1 - LON0) / dlon)
    dem = Dem()
    videos = sorted(p.with_suffix("").with_suffix("")
                    for p in DATA.rglob("*.MP4.gps.tsv"))
    def tier(o8):
        return 0 if o8 <= TIER_DETAIL_CM else (1 if o8 <= TIER_MID_CM else 2)

    best = {}       # (i, j) -> лучший obj8px_cm по всем роликам (для сводки)
    vid_out = []    # повидеовый вклад — фильтры по дате/ролику на карте
    for v in videos:
        hits = video_footprint_hits_strict(v, dem, STEP_S, bbox)
        n_gsd = 0
        vbest = {}     # (i, j) -> (лучший obj8px_cm, таймкод этого прохода)
        vcells_t = {}  # (i, j) -> набор таймкодов сэмплов, задевших ячейку
        for la, lo, o8, t, radius in hits:
            if math.isnan(o8):
                o8 = math.inf
            else:
                n_gsd += 1
            i0 = int((la - LAT0) / dlat)
            j0 = int((lo - LON0) / dlon)
            reach = int(radius // CELL_M) + 1
            # ячейка «смотрели», если её центр в радиусе закраски попадания
            # (точное расстояние, без квантования по сетке)
            for di in range(-reach, reach + 1):
                for dj in range(-reach, reach + 1):
                    i, j = i0 + di, j0 + dj
                    if not (0 <= i < ni and 0 <= j < nj):
                        continue
                    clat = LAT0 + (i + 0.5) * dlat
                    clon = LON0 + (j + 0.5) * dlon
                    if math.hypot((clat - la) * M_PER_DEG_LAT,
                                  (clon - lo) * m_per_deg_lon(clat)) > radius:
                        continue
                    vcells_t.setdefault((i, j), set()).add(round(t))
                    cur = vbest.get((i, j))
                    if cur is None or o8 < cur[0]:
                        vbest[(i, j)] = (o8, t)
        for ij, (o8, _t) in vbest.items():
            cur = best.get(ij)
            if cur is None or o8 < cur:
                best[ij] = o8
        if vbest:
            nm = v.name           # DJI_YYYYMMDD..._Z.MP4 — дата вылета из имени
            date = f"{nm[4:8]}-{nm[8:10]}-{nm[10:12]}"
            cells = [[i, j, tier(o8),
                      None if math.isinf(o8) else round(o8),
                      round(t), len(vcells_t[(i, j)])]
                     for (i, j), (o8, t) in vbest.items()]
            vid_out.append(dict(
                name=nm.removesuffix(".MP4"), date=date,
                cells=sorted(cells, key=lambda c: (c[0], c[1]))))
        print(f"{v.name}: попаданий {len(hits)}, с масштабом {n_gsd}",
              file=sys.stderr)

    data = dict(lat0=LAT0, lon0=LON0, dlat=round(dlat, 8), dlon=round(dlon, 8),
                cell_m=CELL_M,
                detail_cm=TIER_DETAIL_CM, mid_cm=TIER_MID_CM,
                method="strict-2026-08-17",
                videos=vid_out)
    OUT.write_text(json.dumps(data, separators=(",", ":")))

    tiers = {"detail": [], "mid": [], "over": []}
    for ij, o8 in best.items():
        tiers[("detail", "mid", "over")[tier(o8)]].append(ij)
    n = len(best)
    area = n * CELL_M * CELL_M / 1e6
    print(f"ячеек со взглядом: {n} (~{area:.1f} км²)")
    for tier, label in (("detail", f"детально (предмет ≤ {TIER_DETAIL_CM} см)"),
                        ("mid", f"средне (предмет ≤ {TIER_MID_CM} см)"),
                        ("over", "обзорно / масштаб неизвестен")):
        k = len(tiers[tier])
        print(f"  {label}: {k} ({k / n:.0%}, ~{k * CELL_M**2 / 1e6:.1f} км²)")
    print(f"сетка: {OUT}")


if __name__ == "__main__":
    main()
