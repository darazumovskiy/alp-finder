#!/usr/bin/env python3
"""Осмотрена ли именно ЭТА точка: обратная проекция точки во все ролики.

Ячеечный слой покрытия отвечает про 30-метровую ячейку (и min-агрегация
уровня может приписать точке детальность соседнего склона — кейс сцены
16.08, `analysis/viewer/otchet-2026-08-17-koshki-pokrytie.html`). Этот
инструмент отвечает про точку: в каких роликах она была в кадре, на каком
пикселе, с какой дистанции, с каким масштабом (obj8px в точке) и не закрыта
ли перегибом рельефа (окклюзия по DEM как есть и по DEM со сдвигом).

Использование:
  analysis/.venv/bin/python analysis/point_seen.py --lat 39.482522 \
      --lon 73.585943 --alt 4597.4 [--dem-shift -26] [--video <имя>...]

Без --video берутся все ролики с телеметрией подвеса в data/drive.
Вывод: по ролику — сэмплы «в кадре» (t, пиксель, дистанция, масштаб, запас
окклюзии в метрах) и ближайший промах; сводка в конце. Моменты без
измеренного фокусного в «в кадре» не участвуют (рамка кадра неизвестна) —
для них печатается только угловое отклонение от оси.
"""

import argparse
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
from geoproject import (  # noqa: E402
    Dem, _ShiftDem, cast, interp_gap, load_rows, ray_dir, unwrap_deg,
)
from coverage_polygon import (  # noqa: E402
    DATA, M_PER_DEG_LAT, _slope_stretch, m_per_deg_lon, moment_table,
)

W, H = 1920, 1080


def wrap180(a):
    return (a + 180.0) % 360.0 - 180.0


def audit(video, dem, dem_shifted, lat, lon, alt, step=0.5):
    rows = load_rows(video)
    if not rows or "gb_yaw" not in rows[0]:
        return None
    t_arr = np.array([r["time_s"] for r in rows])
    fields = {k: np.array([r.get(k, math.nan) for r in rows])
              for k in ("lat", "lon", "alt_m", "gb_pitch")}
    yaw_arr = unwrap_deg([r.get("gb_yaw", math.nan) for r in rows])
    moments = moment_table(video.name)

    seen, nearmiss, nofocal_close = [], None, 0
    t = float(t_arr[0])
    while t <= float(t_arr[-1]):
        la = interp_gap(t, t_arr, fields["lat"])
        lo = interp_gap(t, t_arr, fields["lon"])
        al = interp_gap(t, t_arr, fields["alt_m"])
        yw = interp_gap(t, t_arr, yaw_arr)
        pt = interp_gap(t, t_arr, fields["gb_pitch"])
        t0, t = t, t + step
        if not all(math.isfinite(v) for v in (la, lo, al, yw, pt)):
            continue
        de = (lon - lo) * m_per_deg_lon(la)
        dn = (lat - la) * M_PER_DEG_LAT
        du = alt - al
        dist = math.sqrt(de * de + dn * dn + du * du)
        dyaw = wrap180(math.degrees(math.atan2(de, dn)) - yw)
        dpitch = math.degrees(math.asin(du / dist)) - pt
        m = moments.get(round(t0, 1)) if moments else None
        if m is None:
            # фокусное неизвестно: считаем «возможно в кадре» по широкому полю
            if abs(dyaw) < 44 and abs(dpitch) < 28:
                nofocal_close += 1
            continue
        o8c, f, dist0 = m
        px = W / 2 + f * math.tan(math.radians(dyaw))
        py = H / 2 - f * math.tan(math.radians(dpitch))
        half_x = math.degrees(math.atan2(W / 2, f))
        half_y = math.degrees(math.atan2(H / 2, f))
        miss = max(abs(dyaw) - half_x, abs(dpitch) - half_y)
        if 0 <= px < W and 0 <= py < H and abs(dyaw) < 60:
            dirv = (de / dist, dn / dist, du / dist)
            occ = {}
            for name, d_ in (("dem", dem), ("dem_shift", dem_shifted)):
                h = cast(d_, la, lo, al, dirv)
                occ[name] = None if h is None else round(h[3] - dist, 1)
            stretch = _slope_stretch(dem, lat, lon, dirv)
            seen.append(dict(t=round(t0, 1), px=round(px), py=round(py),
                             dist=round(dist, 1),
                             o8=round(o8c * dist / dist0 * stretch, 1),
                             occ=occ))
        elif nearmiss is None or miss < nearmiss[0]:
            nearmiss = (miss, round(t0, 1), round(dist, 1))
    return dict(seen=seen, nearmiss=nearmiss, nofocal_close=nofocal_close)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--alt", type=float, required=True,
                    help="высота точки, м (LRF/лазер, не DEM)")
    ap.add_argument("--dem-shift", type=float, default=-26.0,
                    help="сдвиг DEM для второй оценки окклюзии (default −26)")
    ap.add_argument("--video", nargs="*", default=None,
                    help="имена роликов без расширения; default — все")
    ap.add_argument("--step", type=float, default=0.5)
    args = ap.parse_args()

    dem = Dem()
    dem_shifted = _ShiftDem(dem, args.dem_shift)
    if args.video:
        videos = []
        for name in args.video:
            hits = list(DATA.rglob(name + ".MP4"))
            if not hits:
                print(f"{name}: не найден в {DATA}", file=sys.stderr)
                continue
            videos.append(hits[0])
    else:
        videos = sorted(p.with_suffix("").with_suffix("")
                        for p in DATA.rglob("*.MP4.gps.tsv"))

    total_seen = 0
    for v in videos:
        res = audit(Path(str(v)), dem, dem_shifted,
                    args.lat, args.lon, args.alt, args.step)
        if res is None:
            continue
        name = Path(str(v)).name.removesuffix(".MP4")
        if res["seen"]:
            total_seen += 1
            best = min(res["seen"], key=lambda r: r["o8"])
            print(f"{name}: В КАДРЕ {len(res['seen'])} сэмплов; лучший "
                  f"t={best['t']}с px=({best['px']},{best['py']}) "
                  f"дист={best['dist']}м obj8px={best['o8']}см "
                  f"окклюзия(dem/сдвиг)={best['occ']['dem']}/"
                  f"{best['occ']['dem_shift']}м")
            for r in res["seen"]:
                print(f"    t={r['t']:8.1f} px=({r['px']:5d},{r['py']:5d}) "
                      f"дист={r['dist']:7.1f} obj8px={r['o8']:6.1f}см "
                      f"occ={r['occ']['dem']}/{r['occ']['dem_shift']}")
        else:
            nm = res["nearmiss"]
            nm_s = (f"ближайший промах {nm[0]:.1f}° (t={nm[1]}с, {nm[2]}м)"
                    if nm else "фокусное не измерено ни разу")
            extra = (f"; без фокусного близко к оси: {res['nofocal_close']} сэмплов"
                     if res["nofocal_close"] else "")
            print(f"{name}: не в кадре; {nm_s}{extra}")
    print(f"\nроликов с точкой в кадре (с измеренным фокусным): {total_seen}")


if __name__ == "__main__":
    main()
