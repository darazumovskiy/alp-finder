#!/usr/bin/env python3
"""Индекс снега по Sentinel-2 v2 — после аудита 28.08.2026.

Что изменилось против индекса v1 (rock_zones.py, удалён 28.08; результаты — docs/osadki-monitoring.md §7.2):
1. Сетка выровнена по пикселям 10/20 м (без ресемплинга и сдвига), окно общее для всех сцен.
2. Оцениваемое множество пикселей E фиксировано для всех дат: высотный пояс по DEM (HMA 8 м) +
   расстояние до опорной точки + освещённость cos(i) > 0.3 и отсутствие отбрасываемой тени на
   20 сентября (чтобы ряд не «плыл» осенью) + пиксель не тёмный на эталонной чистой сцене.
3. Маска облаков: классы SCL 8/9/10 (облака) везде; класс 3 «тень облака» — только если пиксель
   действительно потемнел относительно эталона (B03 < 50 % эталонного); обрезанные B03 ≤ 50 → брак.
4. Число публикуется только при покрытии валидными пикселями ≥ 95 % от E; иначе null.
5. Вилка порогов NDSI 0.3 / 0.4 / 0.5 и метрика «закрыто снегом, % от камней-эталона» —
   доля пикселей, которые на эталонную дату (самую бесснежную чистую) были камнем, а сейчас снег.
6. Обе сцены одного дня (две орбиты) сохраняются отдельно — их разница = эмпирическая погрешность.
7. Разбивка по экспозиции: северные склоны (аспект 292–68°) отдельно от остальных.
Выход: data/snow_index.json, scenes/s2/_evaluable.png (какие пиксели считаются).
Запуск: analysis/.venv/bin/python analysis/osadki/snow_index.py [--ref S2C_43SCD_20260820_0_L2A]
"""
import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject, transform, transform_bounds
from rasterio.windows import Window

os.environ["AWS_NO_SIGN_REQUEST"] = "YES"
os.environ["GDAL_DISABLE_READDIR_ON_OPEN"] = "EMPTY_DIR"
ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "analysis/osadki/data"
SCENES = ROOT / "analysis/osadki/scenes/s2"
DEM = ROOT / "data/dem/hma8m_kurumdy.tif"
CRS = "EPSG:32643"
EXT = (73.590, 39.480, 0.020, 0.0155)  # как zone в render_s2.py
PAD = 3000  # м, запас DEM для расчёта отбрасываемой тени
SEP_CHECK = dt.datetime(2026, 10, 5, 5, 58)  # освещённость на 5 октября: E должно оставаться видимым до середины октября
POINTS = {"ryukzak": (73.586792, 39.482656), "zona": (73.5924, 39.4780), "ctrl": (73.592673, 39.481279)}
UNITS = {  # имя: (точка, радиус м, высота от, высота до, подпись)
    "ryukzak_4600-4900": ("ryukzak", 500, 4600, 4900, "Пояс рюкзака, 4600–4900 м (радиус 500 м от рюкзака)"),
    "sklon_4900-5200": ("zona", 800, 4900, 5200, "Склон 4900–5200 м (радиус 800 м от центра зоны)"),
    "zona_5200-5500": ("zona", 800, 5200, 5500, "Зона интереса, 5200–5500 м (радиус 800 м)"),
    "ctrl_5099": ("ctrl", 150, 0, 9000, "Контрольная точка 5099 м (радиус 150 м)"),
}
THR = (0.3, 0.4, 0.5)


def solar(lat, lon, t):
    """Высота и азимут Солнца (NOAA-приближение), t — UTC."""
    doy = t.timetuple().tm_yday; hrs = t.hour + t.minute / 60
    g = 2 * math.pi / 365 * (doy - 1 + (hrs - 12) / 24)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    ha = math.radians((hrs * 60 + eqt + 4 * lon) / 4 - 180); la = math.radians(lat)
    cz = math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha); zen = math.acos(cz)
    az = math.degrees(math.acos((math.sin(la) * math.cos(zen) - math.sin(decl)) / (math.cos(la) * math.sin(zen))))
    az = (az + 180) % 360 if ha > 0 else (540 - az) % 360  # NOAA: утром Солнце на ЮВ (проверено по STAC: 20.08 05:58 UTC — 148.7°)
    return 90 - math.degrees(zen), az


def grid():
    lon, lat, dx, dy = EXT
    b = transform_bounds("EPSG:4326", CRS, lon - dx, lat - dy, lon + dx, lat + dy)
    x0 = math.floor(b[0] / 20) * 20; x1 = math.ceil(b[2] / 20) * 20
    y0 = math.floor(b[1] / 20) * 20; y1 = math.ceil(b[3] / 20) * 20
    return x0, y0, x1, y1


def load_dem(x0, y0, x1, y1, pad):
    """DEM HMA → сетка 10 м UTM с запасом pad метров."""
    X0, Y1 = x0 - pad, y1 + pad
    w = int((x1 - x0 + 2 * pad) / 10); h = int((y1 - y0 + 2 * pad) / 10)
    dst = np.full((h, w), np.nan, np.float32)
    tr = from_origin(X0, Y1, 10, 10)
    with rasterio.open(DEM) as src:
        reproject(rasterio.band(src, 1), dst, dst_transform=tr, dst_crs=CRS, resampling=Resampling.bilinear, dst_nodata=np.nan)
    p = pad // 10
    return dst, dst[p:-p, p:-p], tr


def terrain(dem):
    gy, gx = np.gradient(dem, 10.0)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.degrees(np.arctan2(-gx, gy)) % 360  # 0 = север, по часовой
    return slope, aspect


def cosi(slope, aspect, sun_az, sun_el):
    zen = math.radians(90 - sun_el); az = math.radians(sun_az)
    return np.cos(slope) * math.cos(zen) + np.sin(slope) * math.sin(zen) * np.cos(az - np.radians(aspect))


def cast_shadow(big, pad, sun_az, sun_el):
    H, W = big.shape; az = math.radians(sun_az); tanel = math.tan(math.radians(sun_el))
    dx = math.sin(az); dy = -math.cos(az)
    shadow = np.zeros((H, W), bool); rr, cc = np.mgrid[0:H, 0:W]
    bigf = np.nan_to_num(big, nan=-1e9)
    for k in range(1, pad // 10):
        r = np.round(rr + dy * k).astype(int); c = np.round(cc + dx * k).astype(int)
        ok = (r >= 0) & (r < H) & (c >= 0) & (c < W)
        z = np.full((H, W), -1e9, np.float32); z[ok] = bigf[r[ok], c[ok]]
        shadow |= z > bigf + k * 10 * tanel
    p = pad // 10
    return shadow[p:-p, p:-p]


def read_band(href, x0, y0, x1, y1, res):
    with rasterio.open(href) as ds:
        assert ds.crs.to_string() == CRS, ds.crs
        c0 = int(round((x0 - ds.transform.c) / res)); r0 = int(round((ds.transform.f - y1) / res))
        w = int((x1 - x0) / res); h = int((y1 - y0) / res)
        return ds.read(1, window=Window(c0, r0, w, h)).astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="S2C_43SCD_20260820_0_L2A", help="эталонная чистая и самая бесснежная сцена")
    a = ap.parse_args()
    items = {i["id"]: i for i in json.load(open(DATA / "s2_items.json"))}
    # свежий список сцен из каталога (s2_index.json пишет render_s2.py)
    for m in json.load(open(DATA / "s2_index.json")):
        items.setdefault(m["id"], None)
    import urllib.request
    q = {"collections": ["sentinel-2-l2a"], "bbox": [EXT[0] - EXT[2], EXT[1] - EXT[3], EXT[0] + EXT[2], EXT[1] + EXT[3]],
         "datetime": "2026-08-01T00:00:00Z/2026-12-31T23:59:59Z", "limit": 200}
    req = urllib.request.Request("https://earth-search.aws.element84.com/v1/search", data=json.dumps(q).encode(), headers={"Content-Type": "application/json"})
    for f in json.load(urllib.request.urlopen(req, timeout=90))["features"]:
        items[f["id"]] = f
    ids = sorted([k for k, v in items.items() if v], key=lambda k: items[k]["properties"]["datetime"])

    x0, y0, x1, y1 = grid()
    big, dem, _ = load_dem(x0, y0, x1, y1, PAD)
    slope, aspect = terrain(dem)
    H, W = dem.shape
    # координаты пикселей → расстояния до опорных точек
    xs = x0 + 10 * (np.arange(W) + 0.5); ys = y1 - 10 * (np.arange(H) + 0.5)
    XX, YY = np.meshgrid(xs, ys)
    dist = {}
    for k, (lon, lat) in POINTS.items():
        px, py = transform("EPSG:4326", CRS, [lon], [lat])
        dist[k] = np.hypot(XX - px[0], YY - py[0])
    north = (aspect >= 292) | (aspect < 68)
    # освещённость на контрольную сентябрьскую дату
    el_s, az_s = solar(EXT[1], EXT[0], SEP_CHECK)
    ci_sep = cosi(slope, aspect, az_s, el_s); sh_sep = cast_shadow(big, PAD, az_s, el_s)
    lit_sep = (ci_sep > 0.3) & ~sh_sep

    cache = {}

    def bands(wid):
        if wid not in cache:
            A = items[wid]["assets"]
            g = read_band(A["green"]["href"], x0, y0, x1, y1, 10)
            sw = np.kron(read_band(A["swir16"]["href"], x0, y0, x1, y1, 20), np.ones((2, 2), np.float32))
            scl = np.kron(read_band(A["scl"]["href"], x0, y0, x1, y1, 20), np.ones((2, 2), np.float32))
            cache[wid] = (g, sw, scl, (g - sw) / (g + sw + 1e-6))
        return cache[wid]

    # эталон
    g_ref, sw_ref, scl_ref, ndsi_ref = bands(a.ref)
    ref_ok = ~np.isin(scl_ref, [0, 1, 3, 8, 9, 10]) & (g_ref > 800)
    E_all = lit_sep & ref_ok & np.isfinite(dem)
    units = {}
    for name, (pt, rad, z0, z1, label) in UNITS.items():
        E = E_all & (dist[pt] <= rad) & (dem >= z0) & (dem < z1)
        units[name] = {"E": E, "label": label, "n": int(E.sum()), "n_north": int((E & north).sum()),
                       "elev": [float(np.nanmin(dem[E])) if E.any() else None, float(np.nanmedian(dem[E])) if E.any() else None, float(np.nanmax(dem[E])) if E.any() else None],
                       "ref_rock": E & (ndsi_ref < 0.4)}
    # картинка оцениваемого множества
    im = np.zeros((H, W, 3), np.uint8) + 40
    cols = {"ryukzak_4600-4900": (217, 89, 38), "sklon_4900-5200": (200, 170, 60), "zona_5200-5500": (57, 135, 229), "ctrl_5099": (255, 70, 70)}
    for name, u in units.items():
        im[u["E"]] = cols[name]
    img = Image.fromarray(im).resize((W * 2, H * 2), Image.NEAREST)
    from PIL import ImageFont
    try:
        fnt = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 13)
    except Exception:
        fnt = ImageFont.load_default()
    ImageDraw.Draw(img).text((6, H * 2 - 20), "Пиксели, по которым считается индекс: оранжевый 4600–4900, жёлтый 4900–5200, синий 5200–5500, красный — контрольная точка (освещены 5.10; не тёмные на эталоне 20.08)", fill=(255, 255, 255), font=fnt, stroke_width=2, stroke_fill=(0, 0, 0))
    SCENES.mkdir(parents=True, exist_ok=True)
    img.save(SCENES / "_evaluable.png", optimize=True)

    out = {"meta": {"ref": a.ref, "grid_utm": [x0, y0, x1, y1], "sep_check": SEP_CHECK.isoformat(), "sun_sep": [round(el_s, 1), round(az_s, 1)],
                    "units": {n: {"label": u["label"], "n": u["n"], "n_north": u["n_north"], "elev": u["elev"], "n_ref_rock": int(u["ref_rock"].sum())} for n, u in units.items()},
                    "thresholds": THR, "coverage_min": 0.95, "rules": __doc__.strip().split("\n")[2:16]}, "scenes": []}
    for wid in ids:
        p = items[wid]["properties"]
        try:
            g, sw, scl, ndsi = bands(wid)
        except Exception as e:  # noqa: BLE001
            print("ошибка", wid, e); continue
        sun_el, sun_az = p.get("view:sun_elevation"), p.get("view:sun_azimuth")
        ci = cosi(slope, aspect, sun_az, sun_el)
        cloud = np.isin(scl, [8, 9, 10]) | np.isin(scl, [0, 1])
        shadow_cloud = (scl == 3) & (g < 0.5 * g_ref)
        valid_all = ~cloud & ~shadow_cloud & (g > 50) & (ci > 0.2)
        rec = {"id": wid, "date": p["datetime"][:10], "time_utc": p["datetime"][11:16], "sat": wid[:3],
               "orbit": "R048" if p.get("view:azimuth", 0) < 200 else "R091", "sun_el": round(sun_el, 1), "sun_az": round(sun_az, 1),
               "tile_cloud": round(p.get("eo:cloud_cover", -1)), "units": {}}
        for name, u in units.items():
            E = u["E"]; v = valid_all & E
            cov = float(v.sum() / max(1, E.sum()))
            r = {"coverage": round(cov, 3), "n_valid": int(v.sum())}
            ok = cov >= 0.95 and v.sum() >= 30
            for sub, mask in (("all", E), ("north", E & north), ("other", E & ~north)):
                vv = v & mask
                d = {"n": int(vv.sum())}
                if ok and vv.sum() >= 20 and (vv.sum() / max(1, mask.sum())) >= 0.95:
                    for t in THR:
                        d[f"rock{int(t*100)}"] = round(float((ndsi[vv] < t).mean() * 100), 1)
                    rr = u["ref_rock"] & vv
                    d["covered_ref"] = round(float((ndsi[rr] >= 0.4).mean() * 100), 1) if rr.sum() >= 15 else None
                    d["n_ref_rock"] = int(rr.sum())
                    d["ndsi_mid_share"] = round(float(((ndsi[vv] >= 0.3) & (ndsi[vv] < 0.5)).mean() * 100), 1)
                else:
                    for t in THR:
                        d[f"rock{int(t*100)}"] = None
                    d["covered_ref"] = None
                r[sub] = d
            rec["units"][name] = r
        out["scenes"].append(rec)
        z = rec["units"]["zona_5200-5500"]["all"]; c = rec["units"]["ctrl_5099"]["all"]; rk = rec["units"]["ryukzak_4600-4900"]["all"]
        print(rec["date"], rec["sat"], rec["orbit"], f"cov зона {rec['units']['zona_5200-5500']['coverage']:.2f}",
              "зона rock40", z.get("rock40"), "закрыто", z.get("covered_ref"), "| ctrl", c.get("rock40"), c.get("covered_ref"), "| рюкзак", rk.get("rock40"), rk.get("covered_ref"), flush=True)
    json.dump(out, open(DATA / "snow_index.json", "w"), ensure_ascii=False, indent=1)
    print("units:", {n: (u["n"], u["elev"]) for n, u in units.items()})


if __name__ == "__main__":
    main()
