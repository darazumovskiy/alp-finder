#!/usr/bin/env python3
"""Погода по высотам из Open-Meteo в накопительный файл data/model_daily.json.

Правила (после аудита 28.08.2026):
- одна и та же ячейка сетки для всех высот: cell_selection=nearest (иначе Open-Meteo подбирает
  ячейку по высоте и для 4000 м берёт долинную); elevation= меняет только температуру
  (фиксированный градиент 0,65 К/100 м), осадки — общие для ячейки;
- «прошедшие сутки» = склейка кратчайших прогнозов (past_days), не наблюдение;
- осадки в мм в.э.; snowfall_sum Open-Meteo не используем (фаза по температуре ЯЧЕЙКИ);
- фазу осадков считаем сами по почасовой температуре на высоте в часы осадков;
- вилка: GFS, ICON, UKMO, JMA, Météo-France — суточные осадки и Tmax на 5435 м;
- ансамбль ECMWF: вероятность ≥1 и ≥5 мм по дням прогноза.
Запуск: python3 analysis/osadki/fetch_model.py
"""
import datetime as dt
import json
import statistics as st
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "analysis/osadki/data"
LAT, LON = 39.478, 73.590
HEIGHTS = [4000, 4663, 5018, 5435, 6000]
DAILY = "precipitation_sum,temperature_2m_max,temperature_2m_min,wind_speed_10m_max,cloud_cover_mean,sunshine_duration"
TZ = "Asia%2FBishkek"
BASE = f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}&cell_selection=nearest&timezone={TZ}"
OTHER = {"gfs_seamless": "GFS (США)", "icon_seamless": "ICON (Германия)", "ukmo_seamless": "UKMO (Британия)",
         "jma_seamless": "JMA (Япония)", "meteofrance_seamless": "ARPEGE (Франция)"}


def get(url):
    with urllib.request.urlopen(url, timeout=120) as r:
        return json.load(r)


def phase_by_hours(hp, ht):
    """Доля осадков, выпавших при T ≤ 0.5 °C (снег), взвешенно по мм."""
    tot = sum(p or 0 for p in hp)
    if tot <= 0:
        return None
    snow = sum((p or 0) for p, t in zip(hp, ht) if t is not None and t <= 0.5)
    return round(snow / tot, 2)


def main():
    DATA.mkdir(exist_ok=True)
    path = DATA / "model_daily.json"
    store = json.load(open(path)) if path.exists() else {}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
    today = (dt.datetime.utcnow() + dt.timedelta(hours=6)).date().isoformat()  # дата по Бишкеку
    cell = None
    for h in HEIGHTS:
        j = get(f"{BASE}&elevation={h}&daily={DAILY}&hourly=precipitation,temperature_2m&models=ecmwf_ifs&past_days=31&forecast_days=7")
        cell = (j.get("latitude"), j.get("longitude"))
        d = j["daily"]; hr = j["hourly"]
        by_day = {}
        for t, p, tt in zip(hr["time"], hr["precipitation"], hr["temperature_2m"]):
            by_day.setdefault(t[:10], ([], []))
            by_day[t[:10]][0].append(p); by_day[t[:10]][1].append(tt)
        for i, day in enumerate(d["time"]):
            rec = store.setdefault(day, {})
            hp, ht = by_day.get(day, ([], []))
            hv = rec.setdefault(str(h), {})
            hv.update({
                "prec": d["precipitation_sum"][i], "tmax": d["temperature_2m_max"][i], "tmin": d["temperature_2m_min"][i],
                "wind": d["wind_speed_10m_max"][i], "cloud": d["cloud_cover_mean"][i],
                "sun_h": None if d["sunshine_duration"][i] is None else round(d["sunshine_duration"][i] / 3600, 1),
                "snow_frac": phase_by_hours(hp, ht),
                "prec_hours": [k for k, p in enumerate(hp) if (p or 0) >= 0.1],
            })
            rec["forecast"] = day > today
            rec["fetched"] = now
            rec["src"] = "ECMWF IFS 9 км (Open-Meteo, cell_selection=nearest)"
    # вилка других моделей: осадки и Tmax на 5435 м
    for m, label in OTHER.items():
        try:
            d = get(f"{BASE}&elevation=5435&daily=precipitation_sum,temperature_2m_max&models={m}&past_days=31&forecast_days=5")["daily"]
            for i, day in enumerate(d["time"]):
                store.setdefault(day, {}).setdefault("other", {})[m] = {"prec": d["precipitation_sum"][i], "tmax5435": d["temperature_2m_max"][i], "label": label}
        except Exception as e:  # noqa: BLE001
            print("вилка", m, "не получена:", e)
    # ансамбль ECMWF 0.25° (51 член)
    try:
        e = get(f"https://ensemble-api.open-meteo.com/v1/ensemble?latitude={LAT}&longitude={LON}&elevation=5435&daily=precipitation_sum&models=ecmwf_ifs025&forecast_days=7&timezone={TZ}")["daily"]
        members = [k for k in e if k.startswith("precipitation_sum_member")]
        for i, day in enumerate(e["time"]):
            vals = [e[k][i] for k in members if e[k][i] is not None]
            if vals:
                store.setdefault(day, {})["ens"] = {"n": len(vals), "p_ge1": round(sum(v >= 1 for v in vals) / len(vals), 2),
                                                    "p_ge5": round(sum(v >= 5 for v in vals) / len(vals), 2),
                                                    "median": round(st.median(vals), 1), "p90": round(sorted(vals)[max(0, int(len(vals) * 0.9) - 1)], 1)}
    except Exception as ex:  # noqa: BLE001
        print("ансамбль не получен:", ex)
    store["_meta"] = {"cell": cell, "cell_note": "ячейка ECMWF 9 км, средняя высота ~4775 м; температура пересчитана на высоты градиентом 0,65 К/100 м",
                      "heights": HEIGHTS, "updated": now, "today": today}
    json.dump(store, open(path, "w"), ensure_ascii=False, indent=1, sort_keys=True)
    print("model_daily.json:", len([k for k in store if not k.startswith("_")]), "дней; ячейка", cell, "; сегодня", today)


if __name__ == "__main__":
    main()
