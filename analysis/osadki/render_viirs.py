#!/usr/bin/env python3
"""Ежедневные снимки VIIRS (NOAA-20, 375 м) через NASA GIBS WMS — без регистрации.

Для каждого дня: район 55×45 км (вся долина Алая + хребет) и ближний 18×15 км, два слоя:
обычные цвета и «ложные цвета» M11-I2-I1 (снег/лёд — голубой, облака — белые/розовые).
Выход: analysis/osadki/scenes/viirs/<date>/{wide,near}_{tc,fc}.png + meta.json.
Запуск: python3 analysis/osadki/render_viirs.py [--since 2026-08-01] [--until YYYY-MM-DD]
"""
import argparse
import datetime as dt
import json
import os
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "analysis/osadki/scenes/viirs"
WMS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
LAYERS = {"tc": "VIIRS_NOAA20_CorrectedReflectance_TrueColor",
          "fc": "VIIRS_NOAA20_CorrectedReflectance_BandsM11-I2-I1"}
# bbox = (lat_min, lon_min, lat_max, lon_max) — WMS 1.3.0 с EPSG:4326 ждёт lat,lon
BOXES = {"wide": (39.25, 73.25, 39.70, 73.95, 900, 580), "near": (39.41, 73.49, 39.55, 73.69, 800, 560)}
MARK = (73.5924, 39.4780)  # зона интереса
LABEL = {"tc": "VIIRS 375 м · обычные цвета", "fc": "VIIRS 375 м · ложные цвета: снег/лёд голубой, облака белые"}


def font(size):
    for p in ("/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def fetch(layer, box, date, path):
    lat0, lon0, lat1, lon1, w, h = box
    url = (f"{WMS}?SERVICE=WMS&REQUEST=GetMap&VERSION=1.3.0&LAYERS={layer}&CRS=EPSG:4326"
           f"&BBOX={lat0},{lon0},{lat1},{lon1}&WIDTH={w}&HEIGHT={h}&FORMAT=image/png&TIME={date}")
    urllib.request.urlretrieve(url, path)
    im = Image.open(path).convert("RGB")
    d = ImageDraw.Draw(im)
    x = (MARK[0] - lon0) / (lon1 - lon0) * w
    y = (lat1 - MARK[1]) / (lat1 - lat0) * h
    d.ellipse([x - 7, y - 7, x + 7, y + 7], outline=(255, 60, 60), width=3)
    d.text((x + 10, y - 8), "Курумды, зона", fill=(255, 90, 90), font=font(14), stroke_width=2, stroke_fill=(0, 0, 0))
    return im


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-08-01")
    ap.add_argument("--until", default=dt.date.today().isoformat())
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    d0 = dt.date.fromisoformat(a.since); d1 = dt.date.fromisoformat(a.until)
    index = []
    day = d0
    while day <= d1:
        date = day.isoformat()
        dd = OUT / date
        meta = dd / "meta.json"
        if meta.exists() and not a.force:
            index.append(json.load(open(meta)))
        else:
            dd.mkdir(parents=True, exist_ok=True)
            m = {"date": date, "panels": {}}
            ok = True
            for bname, box in BOXES.items():
                for lk, layer in LAYERS.items():
                    fn = f"{bname}_{lk}.png"
                    try:
                        im = fetch(layer, box, date, dd / fn)
                        ImageDraw.Draw(im).text((6, im.height - 20), f"{date} · {LABEL[lk]}", fill=(255, 255, 255),
                                                font=font(13), stroke_width=2, stroke_fill=(0, 0, 0))
                        im.save(dd / fn, optimize=True)
                        m["panels"][f"{bname}_{lk}"] = fn
                    except Exception as e:  # noqa: BLE001
                        ok = False
                        print(date, fn, "ошибка", e, flush=True)
            m["ok"] = ok
            json.dump(m, open(meta, "w"), ensure_ascii=False)
            index.append(m)
            print(date, "ok" if ok else "частично", flush=True)
        day += dt.timedelta(days=1)
    (ROOT / "analysis/osadki/data").mkdir(exist_ok=True)
    json.dump(index, open(ROOT / "analysis/osadki/data/viirs_index.json", "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
