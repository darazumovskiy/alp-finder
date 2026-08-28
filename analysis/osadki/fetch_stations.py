#!/usr/bin/env python3
"""Ближайшие реальные метеостанции через OGIMET (декодированные SYNOP каждые 3 ч) → data/stations.json.

Каракуль (Таджикистан, WMO 38875, 3930 м, 52 км южнее горы) — с осадками.
Сары-Таш (Кыргызстан, WMO 38871, 3150 м, 40 км северо-западнее) — температура, явления, облачность;
суммы осадков в международный обмен не передаёт.
Сводим 3-часовые сроки в сутки (по UTC, как в SYNOP): Tmax/Tmin, сумма осадков, был ли снег
(коды/слова в колонке WW), средняя облачность. OGIMET отвечает 400, если hora — в будущем,
поэтому час берём от текущего UTC. Запуск: python3 analysis/osadki/fetch_stations.py [--days 7]
"""
import argparse
import datetime as dt
import json
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "analysis/osadki/data"
STATIONS = {"karakul": ("38875", "Каракуль (Таджикистан), 3930 м, 52 км южнее"), "sarytash": ("38871", "Сары-Таш (Кыргызстан), 3150 м, 40 км северо-западнее")}
UA = {"User-Agent": "Mozilla/5.0 (alp-finder search monitoring)"}
SNOW_WW = re.compile(r"\b(2[2-3]|3[6-9]|7[0-9]|8[5-6])\b|snow|сне", re.I)


def fetch(ind, days):
    now = dt.datetime.utcnow()
    hora = (now.hour // 3) * 3
    url = (f"https://www.ogimet.com/cgi-bin/gsynres?ind={ind}&lang=en&decoded=yes&ndays={days}"
           f"&ano={now.year}&mes={now.month:02d}&day={now.day:02d}&hora={hora:02d}")
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=90).read().decode("latin-1", errors="ignore")


def strip(s):
    return re.sub(r"<[^>]+>", "", s).replace("&nbsp;", " ").strip()


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse(html):
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I)
    header = None
    obs = []
    for r in rows:
        cells = [strip(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S | re.I)]
        if not cells:
            continue
        if cells[0] == "Date" and header is None:
            header = cells[: cells.index("W2") + 1] if "W2" in cells else cells
            continue
        m = re.match(r"(\d{2})/(\d{2})/(\d{4})", cells[0])
        if not m or header is None:
            continue
        # первая колонка — дата, вторая — время; дальше по заголовку со сдвигом 1
        rec = {"date": f"{m.group(3)}-{m.group(1)}-{m.group(2)}", "time": cells[1]}
        for k, v in zip(header[1:], cells[2:]):
            rec[k] = v
        obs.append(rec)
    return obs


def daily(obs):
    out = {}
    for o in obs:
        d = out.setdefault(o["date"], {"t": [], "tmax": [], "tmin": [], "prec12": [], "prec6": [], "prec3": [], "snow": False, "cloud": [], "n": 0})
        d["n"] += 1
        for key, col in (("t", "T(C)"), ("tmax", "Tmax(C)"), ("tmin", "Tmin(C)")):
            v = num(o.get(col))
            if v is not None:
                d[key].append(v)
        p = o.get("Prec(mm)", "")
        mp = re.match(r"([0-9.]+)/(\d+)h", p)
        if mp:
            d[f"prec{mp.group(2)}"] = d.get(f"prec{mp.group(2)}", []) + [float(mp.group(1))]
        if SNOW_WW.search(o.get("WW", "") + " " + o.get("W1", "") + " " + o.get("W2", "")):
            d["snow"] = True
        n = num(o.get("Nt"))
        if n is not None:
            d["cloud"].append(n)
    res = {}
    for date, d in out.items():
        allt = d["t"] + d["tmax"] + d["tmin"]
        prec = sum(d["prec12"]) if d["prec12"] else (sum(d["prec6"]) if d["prec6"] else (sum(d["prec3"]) if d["prec3"] else None))
        res[date] = {"tmax": max(allt) if allt else None, "tmin": min(allt) if allt else None, "prec": prec,
                     "snow": d["snow"], "cloud_okta": round(sum(d["cloud"]) / len(d["cloud"]), 1) if d["cloud"] else None, "n_obs": d["n"]}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args()
    DATA.mkdir(exist_ok=True)
    path = DATA / "stations.json"
    store = json.load(open(path)) if path.exists() else {}
    for i, (key, (ind, label)) in enumerate(STATIONS.items()):
        if i:
            time.sleep(15)  # OGIMET не любит частые запросы
        try:
            html = fetch(ind, a.days)
            obs = parse(html)
            dd = daily(obs)
            for date, rec in dd.items():
                if rec["n_obs"] >= 4 or date not in store or key not in store[date]:
                    store.setdefault(date, {})[key] = rec
            print(key, label, ":", len(obs), "сроков,", len(dd), "дней")
        except Exception as e:  # noqa: BLE001
            print(key, "ошибка:", e)
    store["_meta"] = {k: v[1] for k, v in STATIONS.items()}
    store["_meta"]["updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
    json.dump(store, open(path, "w"), ensure_ascii=False, indent=1, sort_keys=True)


if __name__ == "__main__":
    main()
