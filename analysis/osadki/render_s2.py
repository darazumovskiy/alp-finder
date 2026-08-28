#!/usr/bin/env python3
"""Sentinel-2 над Курумды: поиск сцен, кропы с подписями, индекс «доля камней» по зонам.

Для каждой сцены L2A (earth-search STAC, COG на AWS без ключей) рендерит PNG:
  scenes/s2/<date>_<sat>/zone_rgb.png, zone_swir.png, zone_mask.png   (3×3 км вокруг зоны)
  scenes/s2/<date>_<sat>/mtn_rgb.png,  mtn_swir.png,  mtn_mask.png    (8×8 км, вся гора)
и meta.json (облачность, доля камней по зонам). Итоговый индекс — data/s2_index.json.
Повторный запуск дорисовывает только новые сцены (идемпотентно).

Запуск: analysis/.venv/bin/python analysis/osadki/render_s2.py [--since 2026-08-01] [--force]
"""
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image, ImageDraw, ImageFont
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

os.environ["AWS_NO_SIGN_REQUEST"] = "YES"
os.environ["GDAL_DISABLE_READDIR_ON_OPEN"] = "EMPTY_DIR"

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "analysis/osadki/scenes/s2"
DATA = ROOT / "analysis/osadki/data"
STAC = "https://earth-search.aws.element84.com/v1/search"

# Опорные точки (lon, lat)
CAMP2 = (73.592438, 39.476132)
ZONES = {  # ключ: (lon, lat, полуширина по долготе, подпись)
    "ryukzak_4663": (73.5868, 39.4827, 0.003, "рюкзак ~4660 м"),
    "zona_5385-5485": (73.5924, 39.4780, 0.003, "зона 5385–5485 м"),
    "camp1_5018": (73.5949, 39.4846, 0.003, "гребень C1 ~5020 м"),
}
POINTS = {  # маркеры
    "контр. точка 5099": (73.592673, 39.481279),
    "C2": CAMP2,
    "C1": (73.594884, 39.484587),
    "ABC": (73.606729, 39.513125),
}
EXTENTS = {  # имя: (центр lon, lat, полуширина lon, полувысота lat)
    "zone": (73.590, 39.480, 0.020, 0.0155),   # ~3.4×3.4 км
    "mtn": (73.595, 39.490, 0.048, 0.037),      # ~8.2×8.2 км
}
SCL_BAD = [0, 1, 3, 8, 9, 10]  # nodata, saturated, cloud shadow, cloud med/high, cirrus
UNIT_COLORS = {"ryukzak_4600-4900": (217, 89, 38), "sklon_4900-5200": (200, 170, 60), "zona_5200-5500": (57, 135, 229), "ctrl_5099": (255, 70, 70)}
UNIT_LABELS = {"ryukzak_4600-4900": "пояс рюкзака 4600–4900", "sklon_4900-5200": "склон 4900–5200", "zona_5200-5500": "зона 5200–5500", "ctrl_5099": "контр. точка"}


def load_evaluable():
    """Сетка индекса (UTM, 20-м выравнивание) и карта оцениваемых пикселей из snow_index (по цветам _evaluable.png)."""
    meta_p = DATA / "snow_index.json"
    png = OUT / "_evaluable.png"
    if not meta_p.exists() or not png.exists():
        return None
    grid = json.load(open(meta_p))["meta"]["grid_utm"]
    im = np.array(Image.open(png).convert("RGB"))[::2, ::2]
    units = np.zeros(im.shape[:2], np.uint8)
    for i, (k, c) in enumerate(UNIT_COLORS.items(), 1):
        units[(im == np.array(c, np.uint8)).all(-1)] = i
    return grid, units
NDSI_ROCK = 0.4


def bbox_of(c):
    lon, lat, dx, dy = c
    return [lon - dx, lat - dy, lon + dx, lat + dy]


def stac_items(since, until):
    q = {"collections": ["sentinel-2-l2a"], "bbox": bbox_of(EXTENTS["mtn"]),
         "datetime": f"{since}T00:00:00Z/{until}T23:59:59Z", "limit": 200}
    req = urllib.request.Request(STAC, data=json.dumps(q).encode(),
                                 headers={"Content-Type": "application/json"})
    feats = json.load(urllib.request.urlopen(req, timeout=90))["features"]
    return sorted(feats, key=lambda f: f["properties"]["datetime"])


def read_window(href, bbox, upsample=1, grid=None):
    with rasterio.open(href) as ds:
        if grid is not None:  # выровненное окно индекса (x0,y0,x1,y1 в UTM, кратно 20 м)
            from rasterio.windows import Window
            res = ds.res[0]
            x0, y0, x1, y1 = grid
            w = Window(int(round((x0 - ds.transform.c) / res)), int(round((ds.transform.f - y1) / res)), int((x1 - x0) / res), int((y1 - y0) / res))
        else:
            b = transform_bounds("EPSG:4326", ds.crs, *bbox)
            w = from_bounds(*b, ds.transform)
        arr = ds.read(1, window=w).astype(np.float32)
        # реальные границы прочитанного окна (для привязки маркеров)
        win_t = ds.window_transform(w)
    if upsample > 1:
        arr = np.kron(arr, np.ones((upsample, upsample), dtype=np.float32))
    return arr, win_t, ds.crs


def lonlat_to_px(lon, lat, crs, win_t, scale=1.0):
    from rasterio.warp import transform
    xs, ys = transform("EPSG:4326", crs, [lon], [lat])
    col, row = ~win_t * (xs[0], ys[0])
    return col * scale, row * scale


def font(size):
    for p in ("/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def draw_overlays(img, crs, win_t, scale, extent, label, units=None):
    f = font(13)
    if units is not None and extent == "zone":
        # контуры площадок индекса (полупрозрачная заливка + подписи)
        ov = np.zeros((units.shape[0], units.shape[1], 4), np.uint8)
        for i, (k, c) in enumerate(UNIT_COLORS.items(), 1):
            ov[units == i] = (*c, 70)
        edge = np.zeros(units.shape, bool)
        edge[1:, :] |= units[1:, :] != units[:-1, :]; edge[:, 1:] |= units[:, 1:] != units[:, :-1]
        for i, (k, c) in enumerate(UNIT_COLORS.items(), 1):
            ov[edge & (units == i)] = (*c, 230)
        ovi = Image.fromarray(ov).resize(img.size, Image.NEAREST)
        img.paste(Image.alpha_composite(img.convert("RGBA"), ovi).convert("RGB"))
        d = ImageDraw.Draw(img)
        for i, (k, c) in enumerate(UNIT_COLORS.items(), 1):
            ys, xs = np.where(units == i)
            if len(xs):
                d.text((xs.min() * scale + 4, ys.min() * scale - 14), UNIT_LABELS[k], fill=c, font=f, stroke_width=2, stroke_fill=(0, 0, 0))
    else:
        d = ImageDraw.Draw(img)
        for key, (lon, lat, dx, lab) in ZONES.items():
            x0, y0 = lonlat_to_px(lon - dx, lat + dx * 0.77, crs, win_t, scale)
            x1, y1 = lonlat_to_px(lon + dx, lat - dx * 0.77, crs, win_t, scale)
            col = {"ryukzak_4663": (217, 89, 38), "zona_5385-5485": (57, 135, 229), "camp1_5018": (25, 158, 112)}[key]
            d.rectangle([x0, y0, x1, y1], outline=col, width=2)
    d = ImageDraw.Draw(img)
    for name, (lon, lat) in POINTS.items():
        if units is not None and name.startswith("контр"):
            continue  # площадка контрольной точки уже подписана
        x, y = lonlat_to_px(lon, lat, crs, win_t, scale)
        if not (0 <= x < img.width and 0 <= y < img.height):
            continue
        r = 5
        d.ellipse([x - r, y - r, x + r, y + r], outline=(255, 60, 60), width=2)
        if extent == "zone" or name in ("ABC", "C2"):
            d.text((x + 7, y - 7), name, fill=(255, 90, 90), font=f, stroke_width=2, stroke_fill=(0, 0, 0))
    d.text((6, img.height - 20), label, fill=(255, 255, 255), font=f, stroke_width=2, stroke_fill=(0, 0, 0))
    # масштабная линейка 1 км
    km_px = 1000 / 10 * scale
    d.line([(img.width - km_px - 10, img.height - 12), (img.width - 10, img.height - 12)], fill=(255, 255, 255), width=3)
    d.text((img.width - km_px - 10, img.height - 30), "1 км", fill=(255, 255, 255), font=f, stroke_width=2, stroke_fill=(0, 0, 0))


def render_scene(item, force=False):
    p = item["properties"]
    date = p["datetime"][:10]
    sat = item["id"].split("_")[0]
    d = OUT / f"{date}_{sat}"
    meta_path = d / "meta.json"
    if meta_path.exists() and not force:
        return json.load(open(meta_path))
    d.mkdir(parents=True, exist_ok=True)
    A = item["assets"]
    meta = {"date": date, "sat": sat, "id": item["id"], "time_utc": p["datetime"][11:16],
            "tile_cloud": round(p.get("eo:cloud_cover", -1)), "zones": {}, "panels": {}}
    ev = load_evaluable()
    for ext, c in EXTENTS.items():
        bbox = bbox_of(c)
        grid = ev[0] if (ev and ext == "zone") else None
        units = ev[1] if (ev and ext == "zone") else None
        r, wt, crs = read_window(A["red"]["href"], bbox, 1, grid)
        g, _, _ = read_window(A["green"]["href"], bbox, 1, grid)
        b, _, _ = read_window(A["blue"]["href"], bbox, 1, grid)
        sw, _, _ = read_window(A["swir16"]["href"], bbox, 2, grid)
        scl, _, _ = read_window(A["scl"]["href"], bbox, 2, grid)
        h, w = r.shape
        sw = sw[:h, :w]; scl = scl[:h, :w]
        if sw.shape != r.shape:  # окно 20 м могло выйти на пиксель короче
            pad = ((0, h - sw.shape[0]), (0, w - sw.shape[1]))
            sw = np.pad(sw, pad, mode="edge"); scl = np.pad(scl, pad, mode="edge")
        ndsi = (g - sw) / (g + sw + 1e-6)
        ok = ~np.isin(scl, SCL_BAD)
        scale = 2 if ext == "zone" else 1
        # RGB
        rgb = np.stack([r, g, b], -1) / 10000.0
        rgb = np.clip((rgb - 0.03) / 0.85, 0, 1) ** 0.75
        im_rgb = Image.fromarray((rgb * 255).astype(np.uint8)).resize((w * scale, h * scale), Image.BILINEAR)
        # SWIR
        swn = np.clip(sw / 10000.0 / 0.55, 0, 1)
        im_sw = Image.fromarray((np.stack([swn] * 3, -1) * 255).astype(np.uint8)).resize((w * scale, h * scale), Image.BILINEAR)
        # маска: снег — белый, камень — коричневый, облако/тень — серый
        mask = np.zeros((h, w, 3), np.uint8)
        mask[...] = (150, 150, 150)
        snow = ok & (ndsi >= NDSI_ROCK); rock = ok & (ndsi < NDSI_ROCK)
        mask[snow] = (235, 240, 250); mask[rock] = (150, 95, 50)
        im_mask = Image.fromarray(mask).resize((w * scale, h * scale), Image.NEAREST)
        panels = {"rgb": (im_rgb, "обычные цвета"), "swir": (im_sw, "инфракрасный SWIR: снег тёмный, камень светлый"),
                  "mask": (im_mask, "маска: белый — снег, коричневый — камень, серый — облако/тень")}
        for name, (im, lab) in panels.items():
            draw_overlays(im, crs, wt, scale, ext, f"{date} {sat} · {lab}", units if (units is not None and units.shape == r.shape) else None)
            fn = f"{ext}_{name}.png"
            im.save(d / fn, optimize=True)
            meta["panels"][f"{ext}_{name}"] = fn
        if ext == "zone":
            meta["zone_cloudfree"] = round(float(ok.mean() * 100))
            for key, (lon, lat, dx, lab) in ZONES.items():
                x0, y0 = lonlat_to_px(lon - dx, lat + dx * 0.77, crs, wt)
                x1, y1 = lonlat_to_px(lon + dx, lat - dx * 0.77, crs, wt)
                ys, ye = int(max(0, min(y0, y1))), int(min(h, max(y0, y1)))
                xs, xe = int(max(0, min(x0, x1))), int(min(w, max(x0, x1)))
                okz = ok[ys:ye, xs:xe]; nz = ndsi[ys:ye, xs:xe]
                cf = float(okz.mean() * 100) if okz.size else 0.0
                rk = float((nz[okz] < NDSI_ROCK).mean() * 100) if okz.sum() > 50 else None
                meta["zones"][key] = {"cloudfree": round(cf), "rock": None if rk is None else round(rk, 1),
                                      "n_ok": int(okz.sum()), "n_all": int(okz.size)}
        if ext == "mtn":
            meta["mtn_cloudfree"] = round(float(ok.mean() * 100))
    json.dump(meta, open(meta_path, "w"), ensure_ascii=False, indent=1)
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-08-01")
    ap.add_argument("--until", default="2026-12-31")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    items = stac_items(a.since, a.until)
    print(len(items), "сцен в каталоге", flush=True)
    index = []
    for it in items:
        try:
            m = render_scene(it, a.force)
            print(m["date"], m["sat"], "облачность тайла", m["tile_cloud"], "зона чисто", m.get("zone_cloudfree"),
                  {k: v["rock"] for k, v in m["zones"].items()}, flush=True)
            index.append(m)
        except Exception as e:  # noqa: BLE001
            print("ОШИБКА", it["id"], e, file=sys.stderr, flush=True)
    index.sort(key=lambda m: (m["date"], m["time_utc"]))
    DATA.mkdir(exist_ok=True)
    json.dump(index, open(DATA / "s2_index.json", "w"), ensure_ascii=False, indent=1)
    print("индекс:", DATA / "s2_index.json", len(index), "сцен")


if __name__ == "__main__":
    main()
