#!/usr/bin/env python3
"""Скрининг: какие точки карты можно поднять со ступени «GPS дрона» на триангуляцию.

У точки с привязкой «GPS дрона» координаты объекта нет вовсе — на карте стоит
место съёмки, объект где-то вдоль уса направления камеры (σ до 145 м). Поднять
её может только второй ракурс: два луча с разных позиций дают точку без модели
поверхности. Вопрос, на который отвечает этот отчёт, — для каких точек второй
ракурс в архиве вообще существует, чтобы не тратить ручной разбор впустую.

Как считается. Цели у такой точки нет, поэтому берётся опорная догадка: луч
камеры (азимут и наклон из карточки, позиция — GPS дрона) трассируется в
рельеф. Догадка координатой не является и никуда не выводится — она нужна
только чтобы спросить архив «кто ещё смотрел примерно сюда». Дальше по всем
JPG и роликам ищутся кадры, в чьё поле зрения догадка попадает, и меряется
база — расстояние между позициями съёмки. Триангуляция состоятельна при базе
не меньше десятой доли дистанции.

Отчёт печатается в stdout; при указании --tsv пишется таблица для планирования.

    cd analysis && .venv/bin/python triangulate_screen.py [--tsv out.tsv]
"""

import argparse
import ast
import glob
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from geoproject import Dem, cast, ray_dir  # noqa: E402
from triangulate import (  # noqa: E402
    M_LAT, PHOTO_HFOV, PHOTO_VFOV, _f_at, _video_tables, ang_to, m_lon,
    photo_focal, photo_kind, xmp_fields,
)

BASE_RATIO = 0.1     # база, ниже которой пересечение лучей вырождается
SEED_MAX_DIST_M = 1500.0


def load_points():
    """POINTS из build_map.py без запуска сборки карты (она тянет DEM и слои)."""
    src = (HERE / "viewer" / "build_map.py").read_text()
    names = {}
    for name in ("GPS", "LRF", "PROJ", "CRAY"):
        m = re.search(rf'^{name} = (\(.*?\)|".*?")\n', src, re.S | re.M)
        names[name] = ast.literal_eval(m.group(1))
    points = eval(src[src.index("POINTS = [") + 9:src.index("\nKINDS = [")],  # noqa: S307
                  dict(names), {})
    coord_fix = {names["GPS"]: "gps", names["LRF"]: "lrf",
                 names["CRAY"]: "cast", names["PROJ"]: "cast"}
    out = []
    for p in points:
        fix = p.get("fix") or next(
            (v for k, v in coord_fix.items() if p["coord"].startswith(k)), None)
        out.append((p, fix))
    return out


def seed_point(dem, p):
    """Опорная догадка о месте объекта: луч камеры в рельеф. Не координата."""
    view = p.get("view")
    if not view:
        return None
    direction = ray_dir(view[0], view[1], 960, 540, 1920, 1080, 7000)
    try:
        hit = cast(dem, p["lat"], p["lon"], p["alt"], direction)
    except ValueError:
        return None
    return hit


def photo_index():
    idx = []
    for path in glob.glob(str(ROOT / "data/drive/**/*.JPG"), recursive=True):
        meta = xmp_fields(path)
        if meta and meta["yaw"] is not None and meta["la"] is not None:
            idx.append((path, meta, photo_kind(Path(path).name)))
    return idx


def video_index():
    idx = []
    for cov in glob.glob(str(ROOT / "analysis/coverage/*.coverage.tsv")):
        vn = Path(cov).name.replace(".MP4.coverage.tsv", "")
        vids = glob.glob(str(ROOT / f"data/drive/**/{vn}.MP4"), recursive=True)
        if not vids or not Path(vids[0] + ".gps.tsv").exists():
            continue
        rows, focals = _video_tables(vids[0], cov)
        if rows and focals:
            idx.append((vids[0], rows[:: max(1, len(rows) // 600)], focals))
    return idx


def views_of(photos, videos, lat, lon, alt):
    """[(источник, дистанция, позиция)] — кадры, в поле зрения которых точка."""
    seen = []
    for path, meta, kind in photos:
        daz, dele, dist = ang_to(meta, lat, lon, alt)
        if (abs(daz) < PHOTO_HFOV[kind] and abs(dele) < PHOTO_VFOV[kind]
                and dist < SEED_MAX_DIST_M):
            seen.append((Path(path).name, dist, meta))
    for path, rows, focals in videos:
        best = None
        for t, la, lo, al, yaw, pit in rows:
            meta = dict(yaw=yaw, pit=pit, la=la, lo=lo, al=al)
            daz, dele, dist = ang_to(meta, lat, lon, alt)
            if abs(daz) > 45 or abs(dele) > 45 or dist > SEED_MAX_DIST_M:
                continue
            f = _f_at(focals, t)
            px = 960 + f * math.tan(math.radians(daz))
            py = 540 - f * math.tan(math.radians(dele))
            if 0 <= px < 1920 and 0 <= py < 1080:
                if best is None or dist < best[1]:
                    best = (f"{Path(path).stem} @{t:.0f}s", dist, meta)
        if best:
            seen.append(best)
    return seen


def best_base(seen):
    """(максимальная база, дистанция ближнего ракурса) по парам позиций съёмки."""
    if len(seen) < 2:
        return 0.0, seen[0][1] if seen else 0.0
    ref = min(seen, key=lambda s: s[1])
    base = max(math.hypot((s[2]["la"] - ref[2]["la"]) * M_LAT,
                          (s[2]["lo"] - ref[2]["lo"]) * m_lon(ref[2]["la"]))
               for s in seen)
    return base, ref[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tsv", help="куда записать таблицу")
    args = ap.parse_args()

    dem = Dem()
    points = [(p, fix) for p, fix in load_points() if fix == "gps"]
    print(f"точек на GPS дрона: {len(points)}; индексирую архив…")
    photos, videos = photo_index(), video_index()
    print(f"кадров-фото {len(photos)}, роликов с телеметрией {len(videos)}\n")

    rows = []
    for p, _fix in points:
        hit = seed_point(dem, p)
        if hit is None:
            rows.append((p["name"], "нет опоры", 0, 0, 0, 0,
                         "луч не встречает рельеф (взгляд выше горизонта) — "
                         "нужен ручной подбор кадров"))
            continue
        la, lo, al, dist = hit
        seen = views_of(photos, videos, la, lo, al)
        base, near = best_base(seen)
        if len(seen) < 2:
            verdict = "второго ракурса нет"
        elif base >= near * BASE_RATIO:
            verdict = "можно триангулировать"
        else:
            verdict = "база мала (все ракурсы с одной точки)"
        rows.append((p["name"], f"{la:.5f}, {lo:.5f}, {al:.0f}", dist,
                     len(seen), base, near, verdict))

    order = {"можно триангулировать": 0, "база мала (все ракурсы с одной точки)": 1,
             "второго ракурса нет": 2}
    rows.sort(key=lambda r: (order.get(r[6], 3), -r[3]))
    print(f"{'точка':52} {'ракурсов':>8} {'база,м':>7} {'дист,м':>7}  вердикт")
    for name, seed, _dist, n, base, near, verdict in rows:
        print(f"{name[:52]:52} {n:8d} {base:7.0f} {near:7.0f}  {verdict}")

    counts = {}
    for r in rows:
        counts[r[6]] = counts.get(r[6], 0) + 1
    print("\nитого: " + ", ".join(f"{k} — {v}" for k, v in sorted(counts.items())))

    if args.tsv:
        out = Path(args.tsv)
        with out.open("w", encoding="utf-8") as f:
            f.write("точка\tопорная догадка\tдистанция луча\tракурсов\tбаза\t"
                    "дистанция ближнего\tвердикт\n")
            for name, seed, dist, n, base, near, verdict in rows:
                f.write(f"{name}\t{seed}\t{dist:.0f}\t{n}\t{base:.0f}\t"
                        f"{near:.0f}\t{verdict}\n")
        print(f"таблица: {out}")


if __name__ == "__main__":
    main()
