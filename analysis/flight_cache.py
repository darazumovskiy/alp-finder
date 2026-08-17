#!/usr/bin/env python3
"""Кеш «полёта» для плеера на карте (analysis/viewer/build_map.py, панель «Полёт»).

Для каждого ролика с телеметрией пишет в analysis/viewer/flights/<имя>/:
  f0000.jpg, f0001.jpg…  — кадры раз в FRAME_STEP с, ширина FRAME_W
                           (кадр i соответствует моменту i*FRAME_STEP);
  meta.json — сэмплы раз в META_STEP с; формат сэмпла (массив):
      [t, lat, lon, alt, agl, yaw, pitch, o8, ctr, poly]
      alt — абсолютная высота дрона, agl — над рельефом под дроном;
      yaw — компасный азимут камеры, pitch — наклон (минус = вниз);
      o8 — различимый предмет, см (порог 8 px из coverage tsv) или null;
      ctr — [lat, lon, dist_m] проекции центра кадра в рельеф или null
            (выше горизонта / вне тайла DEM);
      poly — полигон охвата кадра [[lat, lon]…] (лучи периметра в рельеф,
             нужно фокусное из analysis/coverage/) или null.

Идемпотентный: ролики с готовым meta.json пропускает (пересчёт — --force).
Телеметрию из идущей сессии обработки не ждёт — берёт только готовые
сайдкары *.MP4.gps.tsv.

Запуск (python из analysis/.venv, PYTHONPATH=analysis):
    flight_cache.py ВИДЕО...          # пути или имена файлов из data/drive
    flight_cache.py --all             # все ролики с телеметрией
"""
import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np

from geoproject import Dem, at, cast, load_rows, ray_dir

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/drive"
OUT_DIR = ROOT / "analysis/viewer/flights"
COV_DIR = ROOT / "analysis/coverage"

META_STEP = 2.0     # шаг сэмплов меты, с (совпадает с сеткой coverage tsv)
FRAME_STEP = 5.0    # шаг кадров, с
FRAME_W = 1024      # ширина кадра, px (кадр открывается лайтбоксом на весь экран)
W, H = 1920, 1080   # система координат фокусного (coverage tsv)

# периметр кадра для полигона охвата — как в view_footprint.py
EDGE_PX = ([(x, 0) for x in np.linspace(0, W - 1, 7)] +
           [(W - 1, y) for y in np.linspace(0, H - 1, 4)][1:] +
           [(x, H - 1) for x in np.linspace(W - 1, 0, 7)][1:] +
           [(0, y) for y in np.linspace(H - 1, 0, 4)][1:-1])


def load_coverage(video: Path):
    """[(t, f_px|None, obj8px_cm|None)] из coverage tsv; нет файла — []."""
    cov = COV_DIR / (video.name + ".coverage.tsv")
    if not cov.exists():
        return []
    rows = []
    for line in cov.read_text().splitlines()[1:]:
        p = line.split("\t")
        if len(p) < 6:
            continue
        ok = p[5] == "ok"
        rows.append((float(p[0]),
                     float(p[1]) if ok and p[1] else None,
                     float(p[4]) if ok and p[4] else None))
    return rows


def nearest_cov(cov, t, max_dt):
    best = None
    for row in cov:
        dt = abs(row[0] - t)
        if dt <= max_dt and (best is None or dt < best[0]):
            best = (dt, row)
    return best[1] if best else None


def sample(dem, rows, cov, t):
    lat, lon = at(rows, t, "lat"), at(rows, t, "lon")
    alt = at(rows, t, "alt_m")
    yaw, pitch = at(rows, t, "gb_yaw"), at(rows, t, "gb_pitch")
    # провал телеметрии: интерполировать нечего, момент выпадает из кеша
    # (NaN в JSON плеер не прочитает вовсе)
    if not all(math.isfinite(v) for v in (lat, lon, alt, yaw, pitch)):
        return None
    try:
        agl = alt - dem.elev(lat, lon)
    except ValueError:
        agl = None

    # центральный луч от фокусного не зависит
    hit = cast(dem, lat, lon, alt, ray_dir(yaw, pitch, W / 2, H / 2, W, H, 2500))
    ctr = [round(hit[0], 6), round(hit[1], 6), round(hit[3])] if hit else None

    row = nearest_cov(cov, t, max_dt=META_STEP * 3)
    f_px = row[1] if row else None
    o8 = row[2] if row and abs(row[0] - t) <= META_STEP else None

    poly = None
    if f_px:
        hits = [cast(dem, lat, lon, alt, ray_dir(yaw, pitch, px, py, W, H, f_px))
                for px, py in EDGE_PX]
        pts = [[round(h[0], 5), round(h[1], 5)] for h in hits if h]
        if len(pts) >= 3:
            poly = pts

    return [round(t, 1), round(lat, 6), round(lon, 6), round(alt, 1),
            round(agl) if agl is not None else None,
            round(yaw % 360, 1), round(pitch, 1),
            round(o8) if o8 is not None else None, ctr, poly]


def process(dem, video: Path, force: bool):
    out = OUT_DIR / video.stem
    meta_path = out / "meta.json"
    if meta_path.exists() and not force:
        print(f"{video.stem}: уже есть, пропуск")
        return
    rows = load_rows(video)
    missing = [k for k in ("lat", "lon", "alt_m", "gb_yaw", "gb_pitch")
               if not any(k in r for r in rows)]
    if missing:
        print(f"{video.stem}: в телеметрии нет {','.join(missing)} — пропуск",
              file=sys.stderr)
        return
    cov = load_coverage(video)
    dur = max(r["time_s"] for r in rows if "time_s" in r)

    out.mkdir(parents=True, exist_ok=True)
    # кадры не зависят от меты: при пересчёте меты (например, новое фокусное)
    # готовую нарезку не перегоняем. Но прерванный ffmpeg оставляет усечённый
    # набор (ревью 17.08) — считаем нарезку готовой только если кадров не
    # меньше ожидаемого по длительности (минус 1 на округление хвоста)
    n_expected = int(dur / FRAME_STEP)
    if len(list(out.glob("f*.jpg"))) < n_expected:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(video),
             "-vf", f"fps=1/{FRAME_STEP:g},scale={FRAME_W}:-2", "-q:v", "4",
             "-start_number", "0", str(out / "f%04d.jpg"), "-y"], check=True)
    n_frames = len(list(out.glob("f*.jpg")))

    samples = [s for s in (sample(dem, rows, cov, t)
                           for t in np.arange(0, dur + 1e-6, META_STEP)) if s]
    n_poly = sum(1 for s in samples if s[9])
    meta = dict(video=video.name,
                date=f"{video.stem[4:8]}-{video.stem[8:10]}-{video.stem[10:12]}",
                dur=round(dur, 1), frame_step=FRAME_STEP, n_frames=n_frames,
                meta_step=META_STEP, samples=samples)
    meta_path.write_text(json.dumps(meta, separators=(",", ":")))
    kb = sum(f.stat().st_size for f in out.iterdir()) // 1024
    print(f"{video.stem}: {n_frames} кадров, {len(samples)} сэмплов "
          f"(с полигоном {n_poly}), {kb} КБ")


def resolve(token: str):
    p = Path(token)
    if p.exists():
        return p
    hits = sorted(DATA.rglob(token if token.endswith(".MP4") else token + ".MP4"))
    if not hits:
        raise SystemExit(f"видео не найдено: {token}")
    return hits[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("videos", nargs="*", help="пути или имена роликов")
    ap.add_argument("--all", action="store_true",
                    help="все ролики data/drive с телеметрией")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if args.all:
        videos = sorted(p.with_suffix("").with_suffix("")
                        for p in DATA.rglob("*.MP4.gps.tsv"))
        videos = [v for v in videos if v.exists()]
    else:
        videos = [resolve(t) for t in args.videos]
    if not videos:
        raise SystemExit("нечего обрабатывать (укажите ролики или --all)")

    dem = Dem()
    for v in videos:
        if not v.with_suffix(v.suffix + ".gps.tsv").exists():
            print(f"{v.stem}: нет телеметрии, пропуск", file=sys.stderr)
            continue
        try:
            process(dem, v, args.force)
        except Exception as e:  # один битый ролик не валит весь прогон
            print(f"{v.stem}: ОШИБКА {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
