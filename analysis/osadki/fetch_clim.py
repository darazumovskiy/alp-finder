#!/usr/bin/env python3
"""Климатология «как обычно бывает» — по ТОЙ ЖЕ модели, что и текущие данные (ECMWF IFS 9 км,
архив прогнозов Open-Meteo 2017–2025), чтобы не смешивать шкалы (аудит 28.08.2026: ERA5 и IFS
дают разные суммы и разную температуру на 5435 м). ERA5 1991–2025 — вторая шкала, для справки.
Выход: data/clim.json (перезаписывает старый, ERA5-только). Разово: python3 analysis/osadki/fetch_clim.py
"""
import json
import statistics as st
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "analysis/osadki/data"
LAT, LON = 39.478, 73.590


def get(url):
    with urllib.request.urlopen(url, timeout=300) as r:
        return json.load(r)


def monthly(daily, years):
    P = defaultdict(list); T0 = defaultdict(list); T0b = defaultdict(list)
    for t, p, a, b in zip(daily["time"], daily["precipitation_sum"], daily["tmax_a"], daily["tmax_b"]):
        y, m = int(t[:4]), int(t[5:7])
        P[(y, m)].append(p or 0); T0[(y, m)].append(1 if (a is not None and a > 0) else 0); T0b[(y, m)].append(1 if (b is not None and b > 0) else 0)
    out = {}
    for m in range(1, 13):
        sums = [sum(P[(y, m)]) for y in years if (y, m) in P]
        pos = [sum(T0[(y, m)]) for y in years if (y, m) in T0]
        posb = [sum(T0b[(y, m)]) for y in years if (y, m) in T0b]
        out[m] = {"prec_mean": round(st.mean(sums), 1), "prec_median": round(st.median(sums), 1), "prec_min": round(min(sums), 1), "prec_max": round(max(sums), 1),
                  "pos_days_5435": round(st.mean(pos), 1), "pos_days_4663": round(st.mean(posb), 1), "n_years": len(sums)}
    return out


def fetch_pair(base, models, start, end):
    a = get(f"{base}&elevation=5435&start_date={start}&end_date={end}&daily=precipitation_sum,temperature_2m_max&models={models}&timezone=Asia%2FBishkek")["daily"]
    b = get(f"{base}&elevation=4663&start_date={start}&end_date={end}&daily=temperature_2m_max&models={models}&timezone=Asia%2FBishkek")["daily"]
    return {"time": a["time"], "precipitation_sum": a["precipitation_sum"], "tmax_a": a["temperature_2m_max"], "tmax_b": b["temperature_2m_max"]}


def main():
    out = {}
    ifs = fetch_pair(f"https://historical-forecast-api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}&cell_selection=nearest", "ecmwf_ifs", "2017-01-01", "2025-12-31")
    out["ifs"] = {"label": "ECMWF IFS 9 км, архив прогнозов 2017–2025 (та же модель, что в текущих данных)", "years": "2017–2025", "monthly": monthly(ifs, range(2017, 2026))}
    era = fetch_pair(f"https://archive-api.open-meteo.com/v1/archive?latitude={LAT}&longitude={LON}&cell_selection=nearest", "era5", "1991-01-01", "2025-12-31")
    out["era5"] = {"label": "ERA5 реанализ 25 км, 1991–2025 (ячейка ниже и теплее — другая шкала)", "years": "1991–2025", "monthly": monthly(era, range(1991, 2026))}
    # соотношение сентябрь/август по годам — устойчивая величина
    for key, d, yrs in (("ifs", ifs, range(2017, 2026)), ("era5", era, range(1991, 2026))):
        P = defaultdict(float)
        for t, p in zip(d["time"], d["precipitation_sum"]):
            P[(int(t[:4]), int(t[5:7]))] += p or 0
        ratios = [P[(y, 9)] / P[(y, 8)] for y in yrs if P[(y, 8)] > 0]
        out[key]["sep_to_aug_median"] = round(st.median(ratios), 2)
        out[key]["sep_lt_aug_years"] = f"{sum(r < 1 for r in ratios)} из {len(ratios)}"
    json.dump(out, open(DATA / "clim.json", "w"), ensure_ascii=False, indent=1)
    for k in ("ifs", "era5"):
        m = out[k]["monthly"]
        print(k, "авг", m[8], "сен", m[9], "sep/aug", out[k]["sep_to_aug_median"], out[k]["sep_lt_aug_years"])


if __name__ == "__main__":
    main()
