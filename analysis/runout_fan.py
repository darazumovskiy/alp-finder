#!/usr/bin/env python3
"""Веер выносов: устойчивость коридора и участок, где материал останавливается.

Дополняет прокладку линии падения (`search_vectors.kml`, D8 по GLO-30) двумя
вещами, которых одиночная линия дать не может.

**1. Устойчивость.** Одна линия D8 проведена из одной точки по модели с шагом
30 м. По измерениям самого проекта модель на этой стене ошибается на десятки
метров (палка и крышка: модель выше дальномера на 57-58 м,
`docs/nezavisimyy-analiz/06-obnovlenie-14-08-0246.md`), а кулуар уже ячейки.
Значит вопрос не в том, куда идёт линия из одной точки, а в том, меняется ли
ответ при сдвиге старта на величину ошибки. Здесь старт размазывается по
кольцам, из каждой точки идёт свой спуск, и считается, сколько путей прошло
через каждую ячейку. Сошлись - коридор устойчив, и ошибка привязки на вывод не
влияет. Разошлись - полосу поиска надо расширять, и видно, насколько.

**2. Где материал встаёт.** Приоритет "всё ниже отметки палки" - это 2313 м из
2454 м проложенного пути, 985 ячеек, 72.5 га. Облетать столько с разрешением,
на котором различим предмет, - это не один вылет. Но материал не размазан по
пути равномерно: он останавливается там, где уклон падает ниже угла покоя.
Выше этого уклона склон работает на транспорт, ниже - на накопление. Поэтому
ячейки коридора ранжируются по уклону, и первым вылетом закрывается участок
накопления, а не весь путь.

Точка остановки по обрыву трассировки для этого не годится: D8 обрывается во
впадине модели, а впадина на сглаженном рельефе бывает и артефактом.

Запуск:
    python3 runout_fan.py 39.482656 73.586792
    python3 runout_fan.py 39.482656 73.586792 --repose 25 --band 300
"""

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np

from geoproject import Dem, DEM_PATH

ROOT = Path(__file__).resolve().parent


def rc(dem, lat, lon):
    """Ячейка модели по координатам."""
    x = (lon - dem.lon0) / dem.dlon
    y = (dem.lat0 - lat) / dem.dlat
    return int(round(y)), int(round(x))


def latlon(dem, r, c):
    return dem.lat0 - r * dem.dlat, dem.lon0 + c * dem.dlon


def descend(z, r0, c0, maxsteps=400):
    """Путь наискорейшего спуска D8 по ячейкам модели."""
    h, w = z.shape
    r, c = r0, c0
    path = [(r, c)]
    seen = {(r, c)}
    for _ in range(maxsteps):
        best, drop = None, 0.0
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == dc == 0:
                    continue
                nr, nc = r + dr, c + dc
                if not (0 <= nr < h and 0 <= nc < w) or (nr, nc) in seen:
                    continue
                d = z[r, c] - z[nr, nc]
                if d > drop:
                    best, drop = (nr, nc), d
        if best is None:
            break
        r, c = best
        seen.add(best)
        path.append(best)
    return path


def metres(dem, a, b):
    """Расстояние между ячейками, м."""
    la1, lo1 = latlon(dem, *a)
    la2, lo2 = latlon(dem, *b)
    dy = (la2 - la1) * 111320
    dx = (lo2 - lo1) * 111320 * math.cos(math.radians(la1))
    return math.hypot(dx, dy)


def load_coverage(path):
    """Слои покрытия проекта: по ячейке - лучший различимый предмет, см.

    Читается `analysis/coverage/coverage-map-cells.json` как есть. В нём сетка
    задана lat0/lon0/dlat/dlon, а по каждому ролику лежат ячейки
    `[i, j, tier, o8, t, n]`, где o8 - размер наименьшего предмета, занимающего
    8 пикселей (порог различимости стенда врезок). По ячейке берётся лучший o8
    среди всех роликов.
    """
    p = Path(path)
    if not p.exists():
        print(f"  покрытие не найдено: {p}")
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    best = {}
    for v in d["videos"]:
        for c in v["cells"]:
            i, j, _tier, o8 = c[0], c[1], c[2], c[3]
            if o8 is None:
                continue
            key = (i, j)
            if key not in best or o8 < best[key][0]:
                best[key] = (o8, v["name"], c[4])
    return {"lat0": d["lat0"], "lon0": d["lon0"], "dlat": d["dlat"],
            "dlon": d["dlon"], "detail_cm": d["detail_cm"],
            "mid_cm": d["mid_cm"], "best": best}


def cover_at(cov, lat, lon):
    """(различимый предмет в см, ролик, таймкод) для ячейки покрытия или None."""
    i = int((lat - cov["lat0"]) / cov["dlat"])
    j = int((lon - cov["lon0"]) / cov["dlon"])
    return cov["best"].get((i, j))


def main():
    ap = argparse.ArgumentParser(description="Веер выносов и участок накопления")
    ap.add_argument("lat", type=float)
    ap.add_argument("lon", type=float)
    ap.add_argument("--dem", default=str(DEM_PATH))
    ap.add_argument("--radii", type=float, nargs="+", default=[0, 30, 60, 90, 120],
                    help="кольца разброса старта, м: берутся по ошибке привязки")
    ap.add_argument("--az", type=int, default=16, help="азимутов на кольцо")
    ap.add_argument("--repose", type=float, default=30.0,
                    help="угол покоя, град: ниже него склон копит, а не несёт")
    ap.add_argument("--hold", type=int, default=3,
                    help="сколько ячеек подряд ниже угла покоя считать началом накопления")
    ap.add_argument("--band", type=float, default=200.0,
                    help="ширина полосы поиска вокруг коридора, м")
    ap.add_argument("--gsd", type=float, default=2.0, help="целевой размер пикселя, см")
    ap.add_argument("--swath", type=float, default=60.0, help="захват на проход, м")
    ap.add_argument("--speed", type=float, default=8.0, help="скорость съёмки, м/с")
    ap.add_argument("--obj", type=float, default=30.0,
                    help="предмет какого размера, см, считаем различимым при сверке")
    ap.add_argument("--coverage", default=str(ROOT / "coverage" /
                                             "coverage-map-cells.json"),
                    help="слои покрытия проекта для сверки, что уже попадало в кадр")
    ap.add_argument("--out", default=str(ROOT / "runout"))
    cfg = ap.parse_args()

    dem = Dem(Path(cfg.dem))
    z = np.asarray(dem.z, dtype=np.float64)
    r0, c0 = rc(dem, cfg.lat, cfg.lon)
    print(f"старт: {cfg.lat:.6f}, {cfg.lon:.6f}; по модели {z[r0, c0]:.0f} м")
    print(f"шаг модели: {dem.dlat * 111320:.1f} м по широте")

    # --- веер: старт размазан по кольцам ошибки привязки ---------------------
    starts = [(cfg.lat, cfg.lon)]
    for rad in cfg.radii:
        if rad == 0:
            continue
        for k in range(cfg.az):
            a = 2 * math.pi * k / cfg.az
            starts.append((cfg.lat + rad * math.cos(a) / 111320.0,
                           cfg.lon + rad * math.sin(a)
                           / (111320.0 * math.cos(math.radians(cfg.lat)))))
    hits = {}
    ends = []
    for la, lo in starts:
        r, c = rc(dem, la, lo)
        if not (0 <= r < z.shape[0] and 0 <= c < z.shape[1]):
            continue
        path = descend(z, r, c)
        for cell in path:
            hits[cell] = hits.get(cell, 0) + 1
        ends.append(path[-1])
    n = len(starts)
    print(f"путей: {n} (кольца {cfg.radii} м), задето ячеек: {len(hits)}")

    # сходимость: насколько разошлись концы
    eh = [z[e] for e in ends]
    spread = 0.0
    for e in ends:
        spread = max(spread, metres(dem, ends[0], e))
    uniq = len(set(ends))
    print(f"концы путей: {len(set(ends))} различных, разброс {spread:.0f} м, "
          f"высоты {min(eh):.0f}-{max(eh):.0f} м")
    if uniq == 1:
        print("  сошлись в одну ячейку: ошибка привязки на вывод не влияет")
    elif spread <= cfg.band / 2:
        print(f"  разброс меньше полуширины полосы: коридор устойчив")
    else:
        print(f"  ВНИМАНИЕ: разброс больше полуширины полосы {cfg.band/2:.0f} м, "
              f"полосу поиска надо расширять до {spread:.0f} м")

    # --- где на пути от старта кончается транспорт ---------------------------
    # Ядро начинается там, где пути уже сошлись, то есть обычно уже в пологой
    # части. Выше этого склон несёт, и смене нужна отметка, ниже которой
    # искать: считается по осевому пути из самой точки старта.
    axis = descend(z, r0, c0)
    run = 0.0
    brk = None
    for i in range(len(axis) - 1):
        d = metres(dem, axis[i], axis[i + 1])
        dz = z[axis[i]] - z[axis[i + 1]]
        slope = math.degrees(math.atan2(dz, d)) if d else 0.0
        run += d
        if slope < cfg.repose:
            if brk is None:
                brk = (i, run, float(z[axis[i + 1]]))
        else:
            brk = None
    if brk is not None:
        la, lo = latlon(dem, *axis[brk[0]])
        print(f"\nосевой путь: {len(axis)} ячеек, с {z[axis[0]]:.0f} до "
              f"{z[axis[-1]]:.0f} м")
        print(f"  транспорт кончается на {brk[2]:.0f} м, это {brk[1]:.0f} м пути "
              f"и {z[axis[0]] - brk[2]:.0f} м перепада от старта")
        print(f"  отметка перелома: {la:.6f}, {lo:.6f} — ниже неё искать")

    # --- ядро коридора: через ячейку прошла хотя бы половина путей -----------
    core = [cell for cell, v in hits.items() if v >= n * 0.5]
    core.sort(key=lambda cell: -z[cell])
    print(f"\nядро коридора: {len(core)} ячеек, "
          f"с {z[core[0]]:.0f} до {z[core[-1]]:.0f} м")

    # --- уклон по участкам и начало накопления -------------------------------
    rows = []
    below = 0
    accum_from = None
    for i, cell in enumerate(core):
        la, lo = latlon(dem, *cell)
        if i + 1 < len(core):
            d = metres(dem, cell, core[i + 1])
            dz = z[cell] - z[core[i + 1]]
            slope = math.degrees(math.atan2(dz, d)) if d else 0.0
        else:
            slope = 0.0
        below = below + 1 if slope < cfg.repose else 0
        if accum_from is None and below >= cfg.hold:
            accum_from = i - cfg.hold + 1
        rows.append({"широта": round(la, 6), "долгота": round(lo, 6),
                     "высота_м": round(float(z[cell])),
                     "уклон_град": round(slope, 1),
                     "доля_путей": round(hits[cell] / n, 2),
                     "режим": "несёт" if slope >= cfg.repose else "копит"})
    if accum_from is None:
        accum_from = len(core)
        print(f"участок накопления не найден: уклон нигде не падает ниже "
              f"{cfg.repose:.0f}° на {cfg.hold} ячеек подряд")
    else:
        acc = rows[accum_from:]
        L = sum(metres(dem, core[i], core[i + 1])
                for i in range(accum_from, len(core) - 1))
        print(f"\nучасток накопления: с {acc[0]['высота_м']} м "
              f"({acc[0]['широта']}, {acc[0]['долгота']}) вниз, "
              f"{len(acc)} ячеек, {L:.0f} м пути")
        sl = [r["уклон_град"] for r in acc if r["уклон_град"] > 0]
        if sl:
            print(f"  уклон: медиана {np.median(sl):.0f}°, максимум {max(sl):.0f}°")
        print(f"  выше него склон работает на транспорт: "
              f"{accum_from} ячеек, уклон до "
              f"{max((r['уклон_град'] for r in rows[:accum_from]), default=0):.0f}°")

        # --- цена облёта участка накопления ---------------------------------
        area = L * cfg.band / 1e6
        lines = L * cfg.band / cfg.swath / 1000.0
        mins = lines * 1000.0 / cfg.speed / 60.0
        print(f"\nцена закрытия участка накопления:")
        print(f"  полоса {cfg.band:.0f} м на {L:.0f} м = {area:.2f} км²")
        print(f"  при {cfg.gsd:.0f} см/пиксель и захвате {cfg.swath:.0f} м: "
              f"{lines:.1f} км линий")
        print(f"  на {cfg.speed:.0f} м/с это {mins:.0f} минут чистого пролёта")

    # --- сверка со слоями покрытия -------------------------------------------
    cov = load_coverage(cfg.coverage)
    if cov is not None:
        seen_n = det_n = 0
        for r in rows:
            hit = cover_at(cov, r["широта"], r["долгота"])
            if hit is None:
                r["в_кадре"] = "нет"
                r["различимый_предмет_см"] = ""
                r["ролик"] = ""
                continue
            o8, name, t = hit
            r["в_кадре"] = "да"
            r["различимый_предмет_см"] = o8
            r["ролик"] = f"{name} {int(t)//60:02d}:{int(t)%60:02d}"
            seen_n += 1
            det_n += o8 <= cfg.obj
        print(f"\nсверка со слоями покрытия ({len(cov['best'])} ячеек в кадре):")
        print(f"  ячеек ядра попадало в кадр: {seen_n} из {len(rows)}")
        print(f"  из них с масштабом, на котором различим предмет "
              f"{cfg.obj:.0f} см: {det_n}")
        acc = rows[accum_from:]
        acc_det = sum(1 for r in acc
                      if r.get("различимый_предмет_см") not in ("", None)
                      and r["различимый_предмет_см"] <= cfg.obj)
        print(f"  на участке накопления различим предмет "
              f"{cfg.obj:.0f} см в {acc_det} из {len(acc)} ячеек")
        vals = [r["различимый_предмет_см"] for r in acc
                if r.get("различимый_предмет_см") not in ("", None)]
        if vals:
            print(f"  лучший масштаб на участке накопления: "
                  f"предмет {min(vals)} см (порог «детально» у проекта "
                  f"{cov['detail_cm']} см)")

    # --- вывод --------------------------------------------------------------
    out = Path(cfg.out)
    out.mkdir(parents=True, exist_ok=True)
    cols = list(rows[0].keys())
    with (out / "runout.tsv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, delimiter="\t")
        w.writeheader()
        w.writerows(rows)

    kml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
           '<name>Веер выносов: коридор и участок накопления</name>']
    half_la = dem.dlat / 2
    half_lo = dem.dlon / 2
    for i, r in enumerate(rows):
        mode = r["режим"]
        # копит - красным, несёт - серым; осмотренное полупрозрачным
        col = "8c1414c8" if mode == "копит" else "5a808080"
        o8 = r.get("различимый_предмет_см")
        if isinstance(o8, (int, float)) and o8 <= 30:
            col = "5a14a014"
        la, lo = r["широта"], r["долгота"]
        ring = [(lo - half_lo, la - half_la), (lo + half_lo, la - half_la),
                (lo + half_lo, la + half_la), (lo - half_lo, la + half_la),
                (lo - half_lo, la - half_la)]
        kml.append(
            f"<Placemark><name>{r['высота_м']} м, {mode}</name><description>"
            f"уклон {r['уклон_град']}°, путей через ячейку "
            f"{r['доля_путей']:.0%}"
            + (f", в кадре {r.get('в_кадре', '?')}"
               f", различим предмет {r.get('различимый_предмет_см', '?')} см"
               if "в_кадре" in r else "")
            + "</description>"
            f"<Style><LineStyle><color>00000000</color></LineStyle>"
            f"<PolyStyle><color>{col}</color></PolyStyle></Style>"
            f"<Polygon><outerBoundaryIs><LinearRing><coordinates>"
            + " ".join(f"{a},{b},0" for a, b in ring)
            + "</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>")
    kml.append("</Document></kml>")
    (out / "runout.kml").write_text("\n".join(kml), encoding="utf-8")

    meta = {"старт": [cfg.lat, cfg.lon], "путей": n,
            "концов_различных": uniq, "разброс_концов_м": round(spread),
            "ячеек_ядра": len(rows), "накопление_с_ячейки": accum_from,
            "угол_покоя_град": cfg.repose}
    (out / "runout.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nготово: {out/'runout.tsv'}, {out/'runout.kml'}, {out/'runout.json'}")


if __name__ == "__main__":
    main()
