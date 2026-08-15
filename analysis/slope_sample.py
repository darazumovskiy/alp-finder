#!/usr/bin/env python3
"""Сэмплы «точки срыва» для плеера карты (analysis/viewer/build_map.py).

Задача сэмпла: дать отсмотрщику подряд моменты съёмки, где склон возможного
срыва виден фронтально — как наблюдателю, стоящему со стороны упавших вещей
и смотрящему на хребет с маршрутом, — со слабым или средним зумом.
Позиция дрона не важна, важно направление взгляда камеры.

Зона склона: коридор вдоль гребня с маршрутом — прямая «Верёвки (из СМС) →
Camp2» (лагеря из build_map.CAMPS) с отступами TRIM_TOP/TRIM_BOT по концам,
шириной WEST_M на сторону падения вещей и EAST_M на другую (прямоугольник
штаба со скрина 15.08). Ось s — вдоль гребня, от «Верёвок» к Camp2.

Отбор моментов — по телеметрии из готовых кешей, ничего не пересчитывает:
  analysis/viewer/flights/<ролик>/meta.json — сэмплы раз в 2 с (позиция,
      углы камеры, различимость o8);
  analysis/coverage/<ролик>.MP4.coverage.tsv — фокусное f_px (есть не везде).
Критерий «камера наведена на зону» без DEM-луча (гео-проекция центра кадра
теряет фронтальные виды — центр часто в небе, а у стены DEM врёт и дистанция
центра ненадёжна):
  - азимут камеры указывает на какую-то точку оси зоны (±AIM_TOL);
  - наклон камеры в окне углов места «западная кромка коридора … гребень»
    ±ELEV_TOL (подъёмные виды из-под стены и дальние с запада целятся
    в лицо стены ниже гребня — окно покрывает всю видимую полосу склона);
    это же окно режет взгляды сверху на осыпь с вещами: от низкого дрона
    у осыпи гребень на десятки градусов вверх, а камера смотрит вниз;
  - при близком наведении (< FLOOR_DIST) дополнительно наклон не круче
    PITCH_FRONT_MIN: вблизи окно углов широкое и не отличает фронтальный
    вид от крупного плана сверху (по данным: верные ближние кадры −10°
    и положе, крупные планы −37° и круче);
  - взгляд в полуплоскости ±AZ_TOL от перпендикуляра к гребню со стороны
    падения вещей (включая виды вдоль гребня с торцов), но не с той стороны;
  - точка наведения в DIST_MIN..DIST_MAX;
  - если фокусное измерено — ширина кадра на дистанции наведения
    >= WIDTH_MIN м (слабый/средний зум); не измерено — момент берём,
    отсев на глаз дешевле пропуска.
Подряд идущие моменты склеиваются в клипы.

Выход — flights/samples/<id>.json, формат:
  id, title, dur, meta_step, band ([[lat,lon]…] зона для карты),
  clips [[видео, t0, t1]…], offs (старт клипа в сэмпл-времени),
  tl (o8 см или null на каждые meta_step с — окраска таймлайна без мет).
Сэмплов два: sryv-luchshie (жадное покрытие зоны минимумом клипов)
и sryv-polnyy (все подходящие клипы, упорядочены вдоль оси зоны).

Запуск (python из analysis/.venv, PYTHONPATH=analysis):
    slope_sample.py
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLIGHTS = ROOT / "analysis/viewer/flights"
COV_DIR = ROOT / "analysis/coverage"
OUT_DIR = FLIGHTS / "samples"

sys.path.insert(0, str(ROOT / "analysis/viewer"))
import build_map as bm                          # noqa: E402
from geoproject import Dem                      # noqa: E402

TRIM_TOP = 100.0     # начало коридора: отступ вдоль гребня от «Верёвок», м
TRIM_BOT = 30.0      # конец коридора: отступ вдоль гребня от Camp2, м
WEST_M = 130.0       # ширина коридора в сторону падения вещей, м
EAST_M = 100.0       # ширина коридора за гребень, м
AXIS_STEP = 25.0     # шаг оси зоны для привязки покрытия, м
AZ_TOL = 100.0       # взгляд в сторону гребня с запада (вкл. вдоль гребня), ±°
AIM_TOL = 15.0       # наведение: азимут камеры на точку оси зоны, ±°
ELEV_TOL = 25.0      # запас окна углов места видимой полосы склона, ±°
PITCH_FRONT_MIN = -10.0  # для ближних наведений: круче вниз — крупный план
FLOOR_DIST = 400.0   # ближе этого действует пол наклона PITCH_FRONT_MIN, м
DIST_MIN = 150.0     # ближе к точке наведения — не обзорный вид, м
DIST_MAX = 2500.0    # дальше склон почти не разглядеть, м
WIDTH_MIN = 50.0     # мин. ширина кадра на рельефе (если фокусное есть), м
GAP_S = 4.0          # склейка моментов в клип при дырке до, с
CLIP_MIN_S = 4.0     # клипы короче — шум
COVER_HALF_CAP = 150.0  # кадр «покрывает» вдоль оси не шире этого, м
HALF_DEFAULT = 50.0  # охват вдоль оси без фокусного, м
BIN = 25.0           # ячейка учёта покрытия оси, м

M_LAT = 111132.0


def m_lon(lat):
    return 111320.0 * math.cos(math.radians(lat))


def camp(prefix):
    return next((la, lo) for n, la, lo, _ in bm.CAMPS if n.startswith(prefix))


def build_band():
    """(зона [[lat,lon]…], ось [(lat,lon,s)…], зап. кромка, азимут взгляда °).

    Зона — прямоугольник вдоль гребня: ось s — прямая «Верёвки -> Camp2»
    с отступами TRIM_TOP/TRIM_BOT, поперёк WEST_M в сторону падения вещей
    и EAST_M за гребень. Западная кромка — точки оси, смещённые на WEST_M
    к вещам (низ видимой полосы склона для окна углов места).
    Азимут взгляда — перпендикуляр к гребню со стороны падения.
    """
    (ala, alo), (bla, blo) = camp("Верёвки"), camp("Camp2")
    kx = m_lon((ala + bla) / 2)
    ax, ay = alo * kx, ala * M_LAT
    bx, by = blo * kx, bla * M_LAT
    n = math.hypot(bx - ax, by - ay)
    ux, uy = (bx - ax) / n, (by - ay) / n            # вдоль гребня к Camp2
    ax, ay = ax + ux * TRIM_TOP, ay + uy * TRIM_TOP
    bx, by = bx - ux * TRIM_BOT, by - uy * TRIM_BOT
    length = math.hypot(bx - ax, by - ay)

    # перпендикуляр в сторону падения вещей (западный борт, к линии падения)
    px, py = -uy, ux
    if px > 0:   # сторона вещей — где долгота меньше (запад)
        px, py = -px, -py

    def geo(x, y):
        return [round(y / M_LAT, 6), round(x / kx, 6)]

    band = [geo(ax + px * WEST_M, ay + py * WEST_M),
            geo(bx + px * WEST_M, by + py * WEST_M),
            geo(bx - px * EAST_M, by - py * EAST_M),
            geo(ax - px * EAST_M, ay - py * EAST_M)]
    n_steps = int(length / AXIS_STEP)
    axis, west = [], []
    for i in range(n_steps + 1):
        s = i * length / n_steps
        x, y = ax + ux * s, ay + uy * s
        axis.append((round(y / M_LAT, 6), round(x / kx, 6), s))
        west.append(geo(x + px * WEST_M, y + py * WEST_M))
    view_az = math.degrees(math.atan2(-px, -py)) % 360   # с запада на гребень
    return band, axis, west, view_az


def load_focal(video_name):
    """{t: f_px} из coverage tsv."""
    cov = COV_DIR / (video_name + ".MP4.coverage.tsv")
    if not cov.exists():
        return {}
    out = {}
    for line in cov.read_text().splitlines()[1:]:
        p = line.split("\t")
        if len(p) >= 6 and p[5] == "ok" and p[1]:
            out[float(p[0])] = float(p[1])
    return out


def nearest_focal(focal, t, max_dt=6.0):
    if not focal:
        return None
    tb = min(focal, key=lambda x: abs(x - t))
    return focal[tb] if abs(tb - t) <= max_dt else None


def angdiff(a, b):
    return abs((a - b + 180) % 360 - 180)


def pick_moments(dem, axis, west, view_az):
    """[{v, t, s, half, w, o8}] — моменты «камера наведена на зону».

    Наведение по телеметрии: точка оси зоны с минимальным расхождением
    азимутов камеры и «дрон -> точка» (порог AIM_TOL), затем наклон камеры
    в окне углов места видимой полосы склона, полуплоскость взгляда
    и дистанция.
    """
    axis_el = [dem.elev(la, lo) for la, lo, _ in axis]
    west_el = [dem.elev(la, lo) for la, lo in west]
    out = []
    for mp in sorted(FLIGHTS.glob("*/meta.json")):
        m = json.loads(mp.read_text())
        focal = load_focal(m["video"].removesuffix(".MP4"))
        for smp in m["samples"]:
            t, la, lo, alt = smp[0], smp[1], smp[2], smp[3]
            yaw, pitch, o8 = smp[5], smp[6], smp[7]
            if angdiff(yaw, view_az) > AZ_TOL:
                continue
            best = None   # (расхождение азимута, точка оси i, дистанция)
            for i, (ala, alo, _) in enumerate(axis):
                dn = (ala - la) * M_LAT
                de = (alo - lo) * m_lon(la)
                brg = math.degrees(math.atan2(de, dn)) % 360
                d = angdiff(yaw, brg)
                if best is None or d < best[0]:
                    best = (d, i, math.hypot(dn, de))
            d_az, i, dist = best
            if d_az > AIM_TOL or not DIST_MIN <= dist <= DIST_MAX:
                continue
            if dist < FLOOR_DIST and pitch < PITCH_FRONT_MIN:
                continue
            wla, wlo = west[i]
            dist_w = math.hypot((wla - la) * M_LAT, (wlo - lo) * m_lon(la))
            ang_hi = math.degrees(math.atan2(axis_el[i] - alt, dist))
            ang_lo = math.degrees(math.atan2(west_el[i] - alt, max(dist_w, 1.0)))
            if not (min(ang_lo, ang_hi) - ELEV_TOL <= pitch
                    <= max(ang_lo, ang_hi) + ELEV_TOL):
                continue
            f_px = nearest_focal(focal, t)
            w = dist * 1920 / f_px if f_px else None
            if w is not None and w < WIDTH_MIN:
                continue
            out.append(dict(v=mp.parent.name, t=t, w=w, o8=o8, s=axis[i][2],
                            half=min(w / 2, COVER_HALF_CAP) if w else HALF_DEFAULT))
    return out


def group_clips(moments, meta_step):
    """Моменты одного ролика подряд -> клипы [{v, t0, t1, dur, s0, s1, sc, o8}]."""
    clips = []
    by_v = {}
    for mo in moments:
        by_v.setdefault(mo["v"], []).append(mo)
    for v, ms in by_v.items():
        ms.sort(key=lambda m: m["t"])
        run = [ms[0]]
        for mo in ms[1:]:
            if mo["t"] - run[-1]["t"] <= GAP_S:
                run.append(mo)
            else:
                clips.append(run)
                run = [mo]
        clips.append(run)
    out = []
    for run in clips:
        t0, t1 = run[0]["t"], run[-1]["t"] + meta_step
        if t1 - t0 < CLIP_MIN_S:
            continue
        o8s = [m["o8"] for m in run if m["o8"] is not None]
        out.append(dict(v=run[0]["v"], t0=t0, t1=t1, dur=t1 - t0,
                        s0=min(m["s"] - m["half"] for m in run),
                        s1=max(m["s"] + m["half"] for m in run),
                        sc=sum(m["s"] for m in run) / len(run),
                        o8=sorted(o8s)[len(o8s) // 2] if o8s else None))
    return out


def greedy_cover(clips, s_max):
    """Мин. набор клипов, жадно закрывающий ось зоны ячейками BIN."""
    n_bins = int(s_max / BIN) + 1
    left = set(range(n_bins))
    chosen = []
    pool = list(clips)
    while left and pool:
        def gain(c):
            return len(left & set(range(max(0, int(c["s0"] / BIN)),
                                        min(n_bins, int(c["s1"] / BIN) + 1))))
        best = max(pool, key=lambda c: (gain(c), -(c["o8"] or 9999), -c["dur"]))
        if gain(best) < 1:
            break
        left -= set(range(max(0, int(best["s0"] / BIN)),
                          min(n_bins, int(best["s1"] / BIN) + 1)))
        chosen.append(best)
        pool.remove(best)
    return chosen, left, n_bins


def make_sample(sid, title, clips, band, meta_step):
    clips = sorted(clips, key=lambda c: (c["sc"], c["v"], c["t0"]))
    offs, tl, off = [], [], 0.0
    metas = {}
    for c in clips:
        offs.append(round(off, 1))
        if c["v"] not in metas:
            metas[c["v"]] = json.loads(
                (FLIGHTS / c["v"] / "meta.json").read_text())["samples"]
        smps = metas[c["v"]]
        t = c["t0"]
        while t < c["t1"] - 1e-6:
            i = min(int(round(t / meta_step)), len(smps) - 1)
            tl.append(smps[i][7])
            t += meta_step
        off += c["t1"] - c["t0"]
    data = dict(id=sid, title=title, dur=round(off, 1), meta_step=meta_step,
                band=band, clips=[[c["v"], c["t0"], c["t1"]] for c in clips],
                offs=offs, tl=tl)
    path = OUT_DIR / f"{sid}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    print(f"{sid}: клипов {len(clips)}, длительность {off/60:.1f} мин "
          f"-> {path.relative_to(ROOT)}")
    return data


def main():
    dem = Dem()
    band, axis, west, view_az = build_band()
    s_max = axis[-1][2]
    print(f"зона: ось {s_max:.0f} м (Верёвки -> Camp2), "
          f"взгляд на склон — азимут {view_az:.0f}°±{AZ_TOL:.0f}°")

    meta_step = 2.0
    moments = pick_moments(dem, axis, west, view_az)
    if not moments:
        raise SystemExit("ни одного подходящего момента — проверь пороги")
    ws = sorted(m["w"] for m in moments if m["w"] is not None)
    n_nof = sum(1 for m in moments if m["w"] is None)
    print(f"моментов {len(moments)} (без фокусного {n_nof}); "
          f"ширина кадра, м: мин {ws[0]:.0f} / медиана {ws[len(ws)//2]:.0f} / "
          f"макс {ws[-1]:.0f}" if ws else f"моментов {len(moments)}, все без фокусного")

    clips = group_clips(moments, meta_step)
    print(f"клипов {len(clips)} из {len({c['v'] for c in clips})} роликов")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    best, left, n_bins = greedy_cover(clips, s_max)
    make_sample("sryv-luchshie", "Срыв: лучшие проходы", best, band, meta_step)
    make_sample("sryv-polnyy", "Срыв: все проходы", clips, band, meta_step)

    covered = n_bins - len(left)
    print(f"покрытие оси зоны лучшими проходами: {covered}/{n_bins} ячеек по {BIN:.0f} м "
          f"({covered / n_bins:.0%})")
    if left:
        runs, cur = [], []
        for b in sorted(left):
            if cur and b == cur[-1] + 1:
                cur.append(b)
            else:
                if cur:
                    runs.append(cur)
                cur = [b]
        runs.append(cur)
        print("дыры (вдоль гребня, от Верёвок к Camp2):")
        for r in runs:
            i0, i1 = int(r[0] * BIN / AXIS_STEP), int(min(r[-1] * BIN / AXIS_STEP,
                                                          len(axis) - 1))
            print(f"  {r[0]*BIN:.0f}–{(r[-1]+1)*BIN:.0f} м · "
                  f"{axis[i0][0]:.5f},{axis[i0][1]:.5f} — "
                  f"{axis[i1][0]:.5f},{axis[i1][1]:.5f}")


if __name__ == "__main__":
    main()
