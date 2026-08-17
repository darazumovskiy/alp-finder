#!/usr/bin/env python3
"""LRF-анкеры из DJI JPG: лазерные точки рельефа для линковки гео-привязки.

Каждый JPG M30T с включённым дальномером несёт в XMP координаты ЦЕЛИ лазера
(LRFTargetLat/Lon/AbsAlt/Distance) и позицию/углы дрона. Цель лазера — прямое
измерение поверхности (точнее DEM); набор целей = сетка контроля рельефа и
гео-привязки. У SUPR-тайлов лазер бьёт по сетке панорамы (не по объекту
интереса) — для контроля рельефа это не мешает.

Использование:
  python3 scripts/extract_lrf_anchors.py data/drive/2026-08-16 [ещё каталоги…]
Выход: analysis/coverage/lrf-anchors-<дата-каталога>.tsv (+ stdout сводка
расхождения с DEM, если доступен analysis/geoproject).
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEYS = ("LRFTargetLat", "LRFTargetLon", "LRFTargetAbsAlt", "LRFTargetDistance",
        "GpsLatitude", "GpsLongitude", "AbsoluteAltitude",
        "GimbalYawDegree", "GimbalPitchDegree")


def xmp(path: Path):
    data = path.read_bytes()
    out = {}
    for k in KEYS:
        m = re.search(("drone-dji:%s=\"([^\"]+)\"" % k).encode(), data)
        if m:
            try:
                out[k] = float(m.group(1).decode().replace("+", ""))
            except ValueError:
                pass
    return out


def main():
    dirs = [Path(a).resolve() for a in sys.argv[1:]] or [ROOT / "data/drive/2026-08-16"]
    rows = []
    for d in dirs:
        for jp in sorted(d.rglob("*.JPG")):
            v = xmp(jp)
            if "LRFTargetLat" not in v or "LRFTargetLon" not in v:
                continue
            rows.append((jp.relative_to(ROOT).as_posix(), v))
    if not rows:
        print("LRF-полей не найдено")
        return

    # DEM-сверка, если окружение позволяет
    dem = None
    try:
        sys.path.insert(0, str(ROOT / "analysis"))
        from geoproject import Dem
        dem = Dem()
    except Exception as e:  # noqa: BLE001
        print(f"DEM недоступен ({e}) — колонка dem_minus_lrf будет пустой")

    date = rows[0][0].split("/")[2] if len(rows[0][0].split("/")) > 2 else "unknown"
    out = ROOT / "analysis" / "coverage" / f"lrf-anchors-{date}.tsv"
    deltas = []
    with out.open("w", encoding="utf-8") as f:
        f.write("photo\tlat\tlon\talt\tdist_m\tdrone_lat\tdrone_lon\tdrone_alt\t"
                "gb_yaw\tgb_pitch\tdem_alt\tdem_minus_lrf\n")
        for rel, v in rows:
            la, lo, al = v["LRFTargetLat"], v["LRFTargetLon"], v.get("LRFTargetAbsAlt")
            dem_alt = dd = ""
            if dem is not None and al is not None:
                try:
                    z = dem.elev(la, lo)
                    dem_alt, dd = f"{z:.1f}", f"{z - al:+.1f}"
                    deltas.append(z - al)
                except ValueError:
                    pass
            f.write(f"{rel}\t{la:.7f}\t{lo:.7f}\t{'' if al is None else al}\t"
                    f"{v.get('LRFTargetDistance','')}\t{v.get('GpsLatitude','')}\t"
                    f"{v.get('GpsLongitude','')}\t{v.get('AbsoluteAltitude','')}\t"
                    f"{v.get('GimbalYawDegree','')}\t{v.get('GimbalPitchDegree','')}\t"
                    f"{dem_alt}\t{dd}\n")
    print(f"{out}: {len(rows)} анкеров")
    if deltas:
        import statistics as st
        print(f"DEM − LRF: медиана {st.median(deltas):+.1f} м, "
              f"разброс {min(deltas):+.1f}…{max(deltas):+.1f} м, n={len(deltas)}")


if __name__ == "__main__":
    main()
