"""Разбор ракурса дрона в момент t: куда смотрит камера, что покрывает кадр,
где относительно кадра известные находки.

Запуск (PYTHONPATH=analysis, python из analysis/.venv):
    view_footprint.py <видео.MP4> <t_сек> [--f F_PX] [--out-dir DIR]

Фокусное: без --f берётся из analysis/coverage/<имя>.coverage.tsv (ближайшая
строка со статусом ok в пределах ±6 с); для центрального луча фокусное не влияет.

Выход: печать сводки + два файла в out-dir:
    <имя>_t<T>_annotated.jpg — кадр со стрелками/маркерами на находки
    <имя>_t<T>_map.png      — схема сверху: дрон, полигон охвата, находки
"""
import argparse
import math
import subprocess
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from geoproject import Dem, ray_dir, cast, load_rows, at

# Опорные точки: синхронизировать с docs/nakhodki/README.md (источник правды).
TARGETS = [
    ("рюкзак 4663 м", 39.482656, 73.586792, 4663, "#e65100"),
    ("стакан/пенка 4559 м", 39.48327, 73.58513, 4559, "#c62828"),
    ("вещь 4557 м", 39.483135, 73.584891, 4557, "#ad1457"),
    ("верёвка/вещи 4529 м", 39.483176, 73.585463, 4529, "#6a1b9a"),
    ("зона интереса (центр)", 39.47725, 73.59215, 5443, "#2e7d32"),
    ("Camp2", 39.476132, 73.592438, 5529, "#00695c"),
]

M_LAT = 111132.0


def focal_from_coverage(video: Path, t: float):
    cov = Path("analysis/coverage") / (video.name + ".coverage.tsv")
    if not cov.exists():
        return None
    best = None
    for line in cov.read_text().splitlines()[1:]:
        p = line.split("\t")
        if len(p) >= 6 and p[5] == "ok" and p[1]:
            dt = abs(float(p[0]) - t)
            if dt <= 6 and (best is None or dt < best[0]):
                best = (dt, float(p[1]))
    return best[1] if best else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("t", type=float)
    ap.add_argument("--f", type=float, default=None, help="фокусное, px (ширина 1920)")
    ap.add_argument("--out-dir", type=Path, default=Path("analysis/fullframe/rakursy"))
    args = ap.parse_args()

    f_px = args.f or focal_from_coverage(args.video, args.t)
    if f_px is None:
        raise SystemExit("фокусное не найдено в coverage — передайте --f "
                         "(оценка: analysis/geoproject.py focal <видео> T0 T1)")

    dem = Dem()
    rows = load_rows(args.video)
    lat0, lon0 = at(rows, args.t, "lat"), at(rows, args.t, "lon")
    alt0 = at(rows, args.t, "alt_m")
    yaw0, pitch0 = at(rows, args.t, "gb_yaw"), at(rows, args.t, "gb_pitch")
    m_lon = 111320.0 * math.cos(math.radians(lat0))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{args.video.stem}_t{int(args.t)}"
    frame_path = args.out_dir / f"{tag}.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-ss", str(args.t), "-i", str(args.video),
                    "-frames:v", "1", "-q:v", "2", str(frame_path), "-y"], check=True)

    W, H = 1920, 1080
    print(f"дрон: {lat0:.6f}, {lon0:.6f}, {alt0:.0f} м; подвес yaw {yaw0:.1f} "
          f"(компас {yaw0 % 360:.0f}°) pitch {pitch0:.1f}; фокусное {f_px:.0f} px")

    hfov = math.degrees(2 * math.atan(W / 2 / f_px))
    center_hit = cast(dem, lat0, lon0, alt0, ray_dir(yaw0, pitch0, W / 2, H / 2, W, H, f_px))
    if center_hit:
        print(f"центр кадра: {center_hit[0]:.6f}, {center_hit[1]:.6f}, "
              f"рельеф {center_hit[2]:.0f} м, дистанция {center_hit[3]:.0f} м; HFOV {hfov:.0f}°")
    else:
        print(f"центр кадра выше горизонта (pitch {pitch0:.1f}); HFOV {hfov:.0f}°")

    edge_px = ([(x, 0) for x in np.linspace(0, W - 1, 7)] +
               [(W - 1, y) for y in np.linspace(0, H - 1, 4)][1:] +
               [(x, H - 1) for x in np.linspace(W - 1, 0, 7)][1:] +
               [(0, y) for y in np.linspace(H - 1, 0, 4)][1:-1])
    foot = [cast(dem, lat0, lon0, alt0, ray_dir(yaw0, pitch0, px, py, W, H, f_px))
            for px, py in edge_px]
    dists = [h[3] for h in foot if h]
    if dists:
        print(f"охват: {min(dists):.0f}–{max(dists):.0f} м от дрона, "
              f"лучей в рельеф {len(dists)}/{len(edge_px)} "
              f"(непопавшие — выше горизонта)")

    info = []
    for name, tlat, tlon, talt, color in TARGETS:
        de = (tlon - lon0) * m_lon
        dn = (tlat - lat0) * M_LAT
        du = talt - alt0
        horiz = math.hypot(de, dn)
        az = math.degrees(math.atan2(de, dn))
        el = math.degrees(math.atan2(du, horiz))
        dyaw = (az - yaw0 + 180) % 360 - 180
        dist = math.hypot(horiz, du)
        if abs(dyaw) >= 60:
            print(f"  {name}: далеко вне оси камеры (Δyaw {dyaw:+.0f}°), дист {dist:.0f} м")
            continue
        px = W / 2 + f_px * math.tan(math.radians(dyaw))
        py = H / 2 + f_px * math.tan(math.radians(pitch0 - el))
        inside = 0 <= px < W and 0 <= py < H
        info.append((name, px, py, dist, color, inside))
        print(f"  {name}: {'В КАДРЕ' if inside else 'вне кадра'} "
              f"пиксель ({px:.0f},{py:.0f}), дист {dist:.0f} м, "
              f"Δyaw {dyaw:+.1f}°, угол места цели {el:.1f}° (кадра {pitch0:.1f}°)")

    # аннотированный кадр (matplotlib — ради кириллицы)
    img = plt.imread(frame_path)
    figf, axf = plt.subplots(figsize=(16, 9))
    axf.imshow(img)
    axf.set_xlim(0, W)
    axf.set_ylim(H, 0)
    axf.axis("off")
    row = 0
    for name, px, py, dist, color, inside in info:
        if inside:
            axf.plot(px, py, "o", ms=18, mfc="none", mec=color, mew=3)
            axf.annotate(f"{name}: {dist:.0f} м", (px, py), textcoords="offset points",
                         xytext=(15, -15), fontsize=13, color=color,
                         bbox=dict(fc="white", alpha=0.7, ec="none"))
        else:
            vx, vy = px - W / 2, py - H / 2
            k = min((W / 2 - 60) / abs(vx) if vx else 9e9,
                    (H / 2 - 60) / abs(vy) if vy else 9e9)
            ex, ey = W / 2 + vx * k, H / 2 + vy * k
            axf.annotate("", (ex, ey), (W / 2 + vx * k * 0.8, H / 2 + vy * k * 0.8),
                         arrowprops=dict(arrowstyle="->", color=color, lw=3))
            axf.text(30, 60 + row * 48, f"{name}: {dist:.0f} м — вне кадра, по стрелке",
                     fontsize=14, color=color, bbox=dict(fc="white", alpha=0.7, ec="none"))
            row += 1
    figf.tight_layout(pad=0)
    out_frame = args.out_dir / f"{tag}_annotated.jpg"
    figf.savefig(out_frame, dpi=120)

    # схема сверху: экстент по дрону, охвату и целям
    pts = [(lat0, lon0)] + [(h[0], h[1]) for h in foot if h] + \
          [(t[1], t[2]) for t in TARGETS]
    latc = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
    lonc = (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2

    def xy(la, lo):
        return (lo - lonc) * m_lon, (la - latc) * M_LAT

    half = max(max(abs(xy(*p)[0]) for p in pts), max(abs(xy(*p)[1]) for p in pts)) + 80
    gx = np.linspace(-half, half, 141)
    gy = np.linspace(-half, half, 141)
    Z = np.array([[dem.elev(latc + y / M_LAT, lonc + x / m_lon) for x in gx] for y in gy])

    fig, ax = plt.subplots(figsize=(12, 12))
    cs = ax.contour(gx, gy, Z, levels=np.arange(4000, 6200, 25), colors="#999", linewidths=0.5)
    ax.clabel(cs, cs.levels[::4], fontsize=7, fmt="%d")

    dx, dy = xy(lat0, lon0)
    ax.plot(dx, dy, "o", color="#1565c0", ms=10)
    ax.annotate(f"дрон {alt0:.0f} м\nyaw {yaw0 % 360:.0f}° pitch {pitch0:.0f}°", (dx, dy),
                textcoords="offset points", xytext=(12, 8), fontsize=10, color="#1565c0")
    poly = [xy(h[0], h[1]) for h in foot if h]
    if poly:
        ax.fill([p[0] for p in poly], [p[1] for p in poly], color="#1565c0", alpha=0.25)
    if center_hit:
        chx, chy = xy(center_hit[0], center_hit[1])
        ax.plot(chx, chy, "+", color="#1565c0", ms=14, mew=3)
        ax.annotate(f"центр кадра: {center_hit[3]:.0f} м, выс. {center_hit[2]:.0f} м",
                    (chx, chy), textcoords="offset points", xytext=(10, -28),
                    fontsize=10, color="#1565c0")
    for name, tlat, tlon, talt, color in TARGETS:
        tx, ty = xy(tlat, tlon)
        ax.plot(tx, ty, "s", color=color, ms=8)
        ax.annotate(name, (tx, ty), textcoords="offset points", xytext=(8, 5),
                    fontsize=9, color=color)
        ax.plot([dx, tx], [dy, ty], ":", color=color, lw=1, alpha=0.5)
    ax.set_xlim(-half, half)
    ax.set_ylim(-half, half)
    ax.set_aspect("equal")
    ax.set_title(f"{args.video.stem} t={args.t:.0f} с — охват кадра (HFOV {hfov:.0f}°)")
    ax.grid(alpha=0.2)
    fig.tight_layout()
    out_map = args.out_dir / f"{tag}_map.png"
    fig.savefig(out_map, dpi=130)
    print(out_frame)
    print(out_map)


if __name__ == "__main__":
    main()
