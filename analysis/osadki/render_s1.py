#!/usr/bin/env python3
"""Sentinel-1 радар (RTC, Microsoft Planetary Computer, без регистрации) над Курумды.

Для каждого пролёта: кропы VV в дБ (зона 3×3 км и гора 8×8 км) и «разница с предыдущим пролётом
того же трека» (Δ дБ: синий — сигнал упал, т.е. снег намок/потеплело; красный — вырос).
Выход: analysis/osadki/scenes/s1/<date>_<orbit>/… + meta.json; индекс data/s1_index.json.
Запуск: analysis/.venv/bin/python analysis/osadki/render_s1.py [--since 2026-07-15]
"""
import argparse
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from scipy.ndimage import uniform_filter
from PIL import Image, ImageDraw, ImageFont
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "analysis/osadki/scenes/s1"
DATA = ROOT / "analysis/osadki/data"
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SIGN = "https://planetarycomputer.microsoft.com/api/sas/v1/sign?href="
EXTENTS = {"zone": (73.590, 39.480, 0.020, 0.0155), "mtn": (73.595, 39.490, 0.048, 0.037)}
ZONES = {"ryukzak_4663": (73.5868, 39.4827, 0.003), "zona_5385-5485": (73.5924, 39.4780, 0.003), "camp1_5018": (73.5949, 39.4846, 0.003)}
CTRL = (73.592673, 39.481279)
os.environ["GDAL_DISABLE_READDIR_ON_OPEN"] = "EMPTY_DIR"


def font(size):
    for p in ("/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def bbox_of(c):
    lon, lat, dx, dy = c
    return [lon - dx, lat - dy, lon + dx, lat + dy]


def sign(href):
    return json.load(urllib.request.urlopen(SIGN + urllib.parse.quote(href, safe=""), timeout=60))["href"]


def stac_items(since, until):
    q = {"collections": ["sentinel-1-rtc"], "bbox": bbox_of(EXTENTS["mtn"]),
         "datetime": f"{since}T00:00:00Z/{until}T23:59:59Z", "limit": 200}
    req = urllib.request.Request(STAC, data=json.dumps(q).encode(), headers={"Content-Type": "application/json"})
    feats = json.load(urllib.request.urlopen(req, timeout=90))["features"]
    return sorted(feats, key=lambda f: f["properties"]["datetime"])


def read_window(href, bbox):
    with rasterio.open(href) as ds:
        b = transform_bounds("EPSG:4326", ds.crs, *bbox)
        w = from_bounds(*b, ds.transform)
        arr = ds.read(1, window=w).astype(np.float32)
        return arr, ds.window_transform(w), ds.crs


def to_px(lon, lat, crs, wt, scale):
    from rasterio.warp import transform
    xs, ys = transform("EPSG:4326", crs, [lon], [lat])
    c, r = ~wt * (xs[0], ys[0])
    return c * scale, r * scale


def overlays(im, crs, wt, scale, label):
    d = ImageDraw.Draw(im); f = font(13)
    for key, (lon, lat, dx) in ZONES.items():
        x0, y0 = to_px(lon - dx, lat + dx * 0.77, crs, wt, scale); x1, y1 = to_px(lon + dx, lat - dx * 0.77, crs, wt, scale)
        col = {"ryukzak_4663": (217, 89, 38), "zona_5385-5485": (57, 135, 229), "camp1_5018": (25, 158, 112)}[key]
        d.rectangle([x0, y0, x1, y1], outline=col, width=2)
    x, y = to_px(*CTRL, crs, wt, scale)
    d.ellipse([x - 5, y - 5, x + 5, y + 5], outline=(255, 60, 60), width=2)
    d.text((6, im.height - 20), label, fill=(255, 255, 255), font=f, stroke_width=2, stroke_fill=(0, 0, 0))


def db(a):
    """дБ после сглаживания 7×7 (≈70 м): без него разность двух пролётов — сплошной спекл-шум."""
    return 10 * np.log10(np.maximum(uniform_filter(a, 7), 1e-5))


def render(item, prev_by_orbit, force):
    p = item["properties"]
    date = p["datetime"][:10]
    orbit = f"{p['sat:orbit_state'][:3]}{p.get('sat:relative_orbit', '')}"
    d = OUT / f"{date}_{orbit}"
    mp = d / "meta.json"
    if mp.exists() and not force:
        m = json.load(open(mp)); prev_by_orbit[orbit] = m; return m
    d.mkdir(parents=True, exist_ok=True)
    meta = {"date": date, "orbit": orbit, "time_utc": p["datetime"][11:16], "id": item["id"], "panels": {}, "zones": {}}
    vv_href = sign(item["assets"]["vv"]["href"]); vh_href = sign(item["assets"]["vh"]["href"])
    prev = prev_by_orbit.get(orbit)
    for ext, c in EXTENTS.items():
        bbox = bbox_of(c)
        vv, wt, crs = read_window(vv_href, bbox)
        vh, _, _ = read_window(vh_href, bbox)
        scale = 2 if ext == "zone" else 1
        h, w = vv.shape
        vvdb = db(vv)
        g = np.clip((vvdb + 22) / 24, 0, 1)  # −22…+2 дБ
        im = Image.fromarray((np.stack([g] * 3, -1) * 255).astype(np.uint8)).resize((w * scale, h * scale), Image.BILINEAR)
        overlays(im, crs, wt, scale, f"{date} радар S1 {orbit} · яркость VV (тёмное — мокрый снег/гладкая поверхность)")
        im.save(d / f"{ext}_vv.png", optimize=True); meta["panels"][f"{ext}_vv"] = f"{ext}_vv.png"
        np.save(d / f"{ext}_vvdb.npy", vvdb.astype(np.float16))
        if prev is not None and (Path(OUT / f"{prev['date']}_{orbit}" / f"{ext}_vvdb.npy")).exists():
            pv = np.load(OUT / f"{prev['date']}_{orbit}" / f"{ext}_vvdb.npy").astype(np.float32)
            if pv.shape == vvdb.shape:
                dif = vvdb - pv
                # синий — падение (мокро/теплее), красный — рост; |Δ|<1.5 дБ — серое (в пределах шума), насыщение при 5 дБ
                mag = np.clip((np.abs(dif) - 1.5) / 3.5, 0, 1)
                base = np.clip((vvdb + 22) / 24, 0, 1) * 0.55 + 0.15  # подложка — сам радарный снимок
                rgb = np.stack([base] * 3, -1).astype(np.float32)
                pos = dif > 0
                rgb[..., 0] = np.where(pos, base + (1 - base) * mag, base * (1 - mag))
                rgb[..., 2] = np.where(~pos, base + (1 - base) * mag, base * (1 - mag))
                rgb[..., 1] = base * (1 - mag * 0.8)
                imd = Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8)).resize((w * scale, h * scale), Image.BILINEAR)
                overlays(imd, crs, wt, scale, f"{date} минус {prev['date'][5:]} (тот же трек) · синий: сигнал упал (намокло/потеплело), красный: вырос")
                imd.save(d / f"{ext}_diff.png", optimize=True); meta["panels"][f"{ext}_diff"] = f"{ext}_diff.png"
                meta["prev_date"] = prev["date"]
                if ext == "zone":
                    for key, (lon, lat, dx) in ZONES.items():
                        x0, y0 = to_px(lon - dx, lat + dx * 0.77, crs, wt, 1); x1, y1 = to_px(lon + dx, lat - dx * 0.77, crs, wt, 1)
                        ys, ye = int(max(0, min(y0, y1))), int(min(h, max(y0, y1))); xs, xe = int(max(0, min(x0, x1))), int(min(w, max(x0, x1)))
                        sub = dif[ys:ye, xs:xe]; valid = np.isfinite(sub) & (np.abs(sub) < 30)
                        meta["zones"][key] = {"dvv_db": round(float(np.median(sub[valid])), 2) if valid.sum() > 20 else None}
        if ext == "zone":
            for key, (lon, lat, dx) in ZONES.items():
                x0, y0 = to_px(lon - dx, lat + dx * 0.77, crs, wt, 1); x1, y1 = to_px(lon + dx, lat - dx * 0.77, crs, wt, 1)
                ys, ye = int(max(0, min(y0, y1))), int(min(h, max(y0, y1))); xs, xe = int(max(0, min(x0, x1))), int(min(w, max(x0, x1)))
                sub = vvdb[ys:ye, xs:xe]; valid = np.isfinite(sub) & (sub > -40)
                meta["zones"].setdefault(key, {})["vv_db"] = round(float(np.median(sub[valid])), 2) if valid.sum() > 20 else None
    json.dump(meta, open(mp, "w"), ensure_ascii=False, indent=1)
    prev_by_orbit[orbit] = meta
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-07-15")
    ap.add_argument("--until", default="2026-12-31")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    items = stac_items(a.since, a.until)
    print(len(items), "пролётов", flush=True)
    prev = {}; index = []
    for it in items:
        try:
            m = render(it, prev, a.force)
            print(m["date"], m["orbit"], {k: v for k, v in m["zones"].items()}, flush=True)
            index.append(m)
        except Exception as e:  # noqa: BLE001
            print("ОШИБКА", it["id"], e, flush=True)
    DATA.mkdir(exist_ok=True)
    json.dump(index, open(DATA / "s1_index.json", "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
