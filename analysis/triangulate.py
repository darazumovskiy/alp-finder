#!/usr/bin/env python3
"""Триангуляция объекта по кадрам с разных позиций дрона + оценка точности.

Зачем: пиксель кадра даёт только направление (луч). Одиночный луч превращается
в координату только через модель поверхности — это ломается на пологих лучах
(скольжение) и у кромок (перелёт, ошибка ~100 м, случай 15.08). Два и более
лучей с разных позиций дают точку без модели поверхности вовсе.

Лазера в видеотелеметрии M30T НЕТ (проверено разбором всех полей protobuf-потока
dvtm и родных SRT, 15.08): дистанция пишется только в XMP фотографий
(LRFTarget*). Поэтому для находок из видео/панорам второй ракурс — единственный
способ получить дальность без предположений о рельефе.

Подкоманды:

  find <lat> <lon> <alt>
      Поиск по архиву кадров, видящих точку: все JPG (XMP: позиция+углы) и все
      ролики с .gps.tsv (посекундно). Печатает: файл/таймкод, пиксель, дистанцию,
      базу до опорной позиции (первой найденной) и пригодность для триангуляции
      (база ≥ 1/10 дистанции).

  solve <спека> [<спека> ...]
      Спека: photo:<путь.JPG>:<px>:<py> либо video:<путь.MP4>:<t>:<px>:<py>
      Печатает: координату (пересечение лучей наименьшими квадратами), остаток
      по каждому лучу, оценку σ (из геометрии лучей и углового шума) и вердикт.

Фокусные (эмпирика по поворотам подвеса, EXIF-пересчёт занижен на ~18%):
зум-фото SUPR 4000x3000 — 15700 px; W-фото 4000x3000 — 3151 px;
видео 1920x1080 — из analysis/coverage/<имя>.coverage.tsv (самокалибровка).

Пиксели объекта во втором кадре переносить SIFT-ом по окрестной скальной
текстуре (образец — analysis/fullframe/, сессия 15.08) или кликом глазами.
"""

import argparse
import bisect
import csv
import glob
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

M_LAT = 111132.0
F_ZOOM_PHOTO = 15700.0   # SUPR/зум-JPG 4000x3000
F_W_PHOTO = 3151.0       # широкая камера, JPG 4000x3000
SIGMA_ANG_DEG = 0.05     # угловой шум: ~5 px SIFT на f=15700… грубее для W — учтём per-ray

PHOTO_HFOV = {"zoom": 7.25, "w": 36.9, "t": 20.4}
PHOTO_VFOV = {"zoom": 5.46, "w": 28.7, "t": 15.5}


def m_lon(lat):
    return 111320.0 * math.cos(math.radians(lat))


def xmp_fields(path):
    raw = open(path, "rb").read()
    m = re.search(rb"<x:xmpmeta.*?</x:xmpmeta>", raw, re.S)
    if not m:
        return None
    x = m.group(0).decode("utf-8", "replace")

    def g(k):
        mm = re.search(rf'drone-dji:{k}="([^"]*)"', x)
        return float(mm.group(1)) if mm else None

    return dict(yaw=g("GimbalYawDegree"), pit=g("GimbalPitchDegree"),
                la=g("GpsLatitude"), lo=g("GpsLongitude"),
                al=g("AbsoluteAltitude"),
                lrf_d=g("LRFTargetDistance"))


def photo_kind(name):
    if "_W" in name:
        return "w"
    if "_T" in name:
        return "t"
    return "zoom"


def photo_focal(kind):
    return F_W_PHOTO if kind == "w" else (F_ZOOM_PHOTO if kind == "zoom" else 1422.0)


# --- find --------------------------------------------------------------------


def ang_to(meta, lat, lon, alt):
    n = (lat - meta["la"]) * M_LAT
    e = (lon - meta["lo"]) * m_lon(meta["la"])
    dh = math.hypot(n, e)
    du = alt - meta["al"]
    az = math.degrees(math.atan2(e, n)) % 360
    el = math.degrees(math.atan2(du, dh))
    daz = (az - meta["yaw"] + 180) % 360 - 180
    dele = el - meta["pit"]
    return daz, dele, math.hypot(dh, du)


def cmd_find(args):
    lat, lon, alt = args.lat, args.lon, args.alt
    hits = []
    for p in glob.glob(str(ROOT / "data/drive/**/*.JPG"), recursive=True):
        meta = xmp_fields(p)
        if not meta or meta["yaw"] is None or meta["la"] is None:
            continue
        kind = photo_kind(Path(p).name)
        daz, dele, dist = ang_to(meta, lat, lon, alt)
        if abs(daz) < PHOTO_HFOV[kind] and abs(dele) < PHOTO_VFOV[kind] and dist < 1500:
            f = photo_focal(kind)
            px = 2000 + f * math.tan(math.radians(daz))
            py = 1500 - f * math.tan(math.radians(dele))
            hits.append(dict(src=p, t=None, meta=meta, dist=dist,
                             px=px, py=py, kind=kind))
    for cov in glob.glob(str(ROOT / "analysis/coverage/*.coverage.tsv")):
        vn = Path(cov).name.replace(".MP4.coverage.tsv", "")
        vids = glob.glob(str(ROOT / f"data/drive/**/{vn}.MP4"), recursive=True)
        if not vids or not Path(vids[0] + ".gps.tsv").exists():
            continue
        rr, ff = _video_tables(vids[0], cov)
        if not rr or not ff:
            continue
        best = None
        for t, la, lo, al, yaw, pit in rr[:: max(1, len(rr) // 600)]:
            f = _f_at(ff, t)
            meta = dict(yaw=yaw, pit=pit, la=la, lo=lo, al=al)
            daz, dele, dist = ang_to(meta, lat, lon, alt)
            if abs(daz) > 45 or abs(dele) > 45:
                continue
            px = 960 + f * math.tan(math.radians(daz))
            py = 540 - f * math.tan(math.radians(dele))
            if 0 <= px < 1920 and 0 <= py < 1080:
                if best is None or dist / f < best["dist"] / best["f"]:
                    best = dict(src=vids[0], t=t, meta=meta, dist=dist,
                                px=px, py=py, kind="video", f=f)
        if best:
            hits.append(best)
    if not hits:
        print("покрытий не найдено")
        return
    hits.sort(key=lambda h: h["dist"])
    base = hits[0]["meta"]
    print(f"{'файл / таймкод':66} {'дист':>6} {'пиксель':>12} {'база,м':>7} {'триангул.'}")
    for h in hits:
        b = math.hypot((h["meta"]["la"] - base["la"]) * M_LAT,
                       (h["meta"]["lo"] - base["lo"]) * m_lon(base["la"]))
        ok = "ДА" if b >= h["dist"] / 10 else "мало базы"
        tag = str(Path(h["src"]).relative_to(ROOT))[-60:] + (f" @{h['t']:.0f}s" if h["t"] else "")
        print(f"{tag:66} {h['dist']:6.0f} ({h['px']:4.0f},{h['py']:4.0f}) {b:7.0f} {ok}")


def _video_tables(vid, cov):
    rr = []
    for r in csv.DictReader(open(vid + ".gps.tsv"), delimiter="\t"):
        try:
            rr.append((float(r["time_s"]), float(r["lat"]), float(r["lon"]),
                       float(r["alt_m"]), float(r["gb_yaw"]), float(r["gb_pitch"])))
        except (ValueError, KeyError):
            pass
    ff = []
    for r in csv.DictReader(open(cov), delimiter="\t"):
        try:
            ff.append((float(r["t"]), float(r["f_px"])))
        except (ValueError, KeyError):
            pass
    return rr, ff


def _f_at(ff, t):
    ts = [x[0] for x in ff]
    j = min(max(bisect.bisect_left(ts, t) - 1, 0), len(ff) - 1)
    if j + 1 < len(ff) and abs(ff[j + 1][0] - t) < abs(ff[j][0] - t):
        j += 1
    return ff[j][1]


# --- solve -------------------------------------------------------------------


def parse_spec(s):
    parts = s.split(":")
    if parts[0] == "photo":
        path, px, py = parts[1], float(parts[2]), float(parts[3])
        meta = xmp_fields(path)
        kind = photo_kind(Path(path).name)
        f = photo_focal(kind)
        w, h = 4000, 3000
        sigma_px = 5.0
        return meta, px, py, f, w, h, sigma_px, s
    if parts[0] == "video":
        path, t, px, py = parts[1], float(parts[2]), float(parts[3]), float(parts[4])
        cov = ROOT / f"analysis/coverage/{Path(path).name}.coverage.tsv"
        rr, ff = _video_tables(path, str(cov))
        ts = [r[0] for r in rr]
        i = min(max(bisect.bisect_left(ts, t), 0), len(rr) - 1)
        _, la, lo, al, yaw, pit = rr[i]
        meta = dict(yaw=yaw, pit=pit, la=la, lo=lo, al=al)
        return meta, px, py, _f_at(ff, t), 1920, 1080, 3.0, s
    raise SystemExit(f"не понял спеку: {s}")


def cmd_solve(args):
    import numpy as np
    rays = []
    lat0 = lon0 = alt0 = None
    for spec in args.specs:
        meta, px, py, f, w, h, sigma_px, tag = parse_spec(spec)
        if lat0 is None:
            lat0, lon0, alt0 = meta["la"], meta["lo"], meta["al"]
        daz = math.degrees(math.atan((px - w / 2) / f))
        dele = -math.degrees(math.atan((py - h / 2) / f))
        az = math.radians(meta["yaw"] + daz)
        el = math.radians(meta["pit"] + dele)
        d = np.array([math.cos(el) * math.sin(az),
                      math.cos(el) * math.cos(az), math.sin(el)])
        o = np.array([(meta["lo"] - lon0) * m_lon(lat0),
                      (meta["la"] - lat0) * M_LAT, meta["al"] - alt0])
        rays.append((o, d, math.degrees(sigma_px / f), tag))
    if len(rays) < 2:
        raise SystemExit("нужно ≥2 лучей с разных позиций")
    A = np.zeros((3, 3))
    b = np.zeros(3)
    for o, d, _, _ in rays:
        P = np.eye(3) - np.outer(d, d)
        A += P
        b += P @ o
    p = np.linalg.solve(A, b)
    la = lat0 + p[1] / M_LAT
    lo = lon0 + p[0] / m_lon(lat0)
    al = alt0 + p[2]
    print(f"координата: {la:.6f}, {lo:.6f}, {al:.0f} м")
    dists = []
    for o, d, sig_ang, tag in rays:
        P = np.eye(3) - np.outer(d, d)
        resid = float(np.linalg.norm(P @ (p - o)))
        dist = float(np.linalg.norm(p - o))
        dists.append(dist)
        print(f"  {tag[-70:]}: дистанция {dist:.0f} м, остаток {resid:.1f} м, "
              f"σ_угла {sig_ang:.3f}°")
    # формальная σ позиции: угловой шум × дистанция, через геометрию (A^-1)
    sig = np.mean([math.radians(s) * d for (_, _, s, _), d in zip(rays, dists)])
    cov = np.linalg.inv(A) * (sig ** 2) * len(rays)
    eig = np.sqrt(np.linalg.eigvalsh(cov))
    print(f"σ позиции (полуоси эллипсоида): {eig[0]:.1f} / {eig[1]:.1f} / {eig[2]:.1f} м")
    worst = max(float(np.linalg.norm((np.eye(3) - np.outer(d, d)) @ (p - o)))
                for o, d, _, _ in rays)
    print("вердикт:", "ok" if worst < 10 else
          f"ПЛОХО: остаток {worst:.0f} м — проверь пиксели/кадры (не тот объект?)")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("find")
    p.add_argument("lat", type=float)
    p.add_argument("lon", type=float)
    p.add_argument("alt", type=float)
    p.set_defaults(fn=cmd_find)
    p = sub.add_parser("solve")
    p.add_argument("specs", nargs="+")
    p.set_defaults(fn=cmd_solve)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
