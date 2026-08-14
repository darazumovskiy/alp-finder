"""Одноразовая проверка 152839: проекция планового трека (гребня), зоны интереса
и Camp2 прямо на кадры ролика, с проверкой видимости за перегибом рельефа."""
import math
import re
import subprocess
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from geoproject import Dem, ray_dir, cast, load_rows, at

VIDEO = Path("data/drive/2026-08-13/dron-part2/DJI_20260813152839_0005_Z.MP4")
OUT = Path("analysis/fullframe/coverage-check-152839")
MOMENTS = [(0, 2917), (40, 5901), (84, 3134), (100, 5618)]
W, H = 1920, 1080
M_LAT = 111132.0

ZONE = [[39.4768, 73.5920], [39.4777, 73.5923]]
CAMP2 = (39.476132, 73.592438)

dem = Dem()
rows = load_rows(VIDEO)

pts = [(float(m.group(1)), float(m.group(2))) for m in
       re.finditer(r'<trkpt lat="([\d.]+)" lon="([\d.]+)"',
                   open("docs/marshrut/plan-track.gpx").read())]
# нитка маршрута в исходном порядке (без вейпоинтов); разрывы > 300 м не соединять
seg = [p for p in pts if 39.470 < p[0] < 39.483 and p[1] < 73.60]
dense = []
for a, b in zip(seg, seg[1:]):
    gap = math.hypot((b[0]-a[0])*M_LAT,
                     (b[1]-a[1])*111320*math.cos(math.radians(a[0])))
    if gap > 300:
        dense.append(None)
        continue
    n = max(2, int(gap / 8))
    for k in range(n):
        dense.append((a[0] + (b[0]-a[0])*k/n, a[1] + (b[1]-a[1])*k/n))
dense.append(seg[-1])


def project(lat0, lon0, alt0, yaw0, pitch0, f, tlat, tlon, talt):
    """(px, py, dist, hidden) или None, если сильно вне оси/за спиной."""
    m_lon = 111320 * math.cos(math.radians(lat0))
    de, dn, du = (tlon-lon0)*m_lon, (tlat-lat0)*M_LAT, talt-alt0
    horiz = math.hypot(de, dn)
    az = math.degrees(math.atan2(de, dn))
    el = math.degrees(math.atan2(du, horiz))
    dyaw = (az - yaw0 + 180) % 360 - 180
    if abs(dyaw) >= 60:
        return None
    px = W/2 + f*math.tan(math.radians(dyaw))
    py = H/2 + f*math.tan(math.radians(pitch0 - el))
    dist = math.hypot(horiz, du)
    hit = cast(dem, lat0, lon0, alt0,
               (de/dist, dn/dist, du/dist))
    hidden = hit is not None and hit[3] < dist - 12
    return px, py, dist, hidden


for t, f in MOMENTS:
    frame = OUT / f"t{t}.jpg"
    if not frame.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(VIDEO),
                        "-frames:v", "1", "-q:v", "2", str(frame), "-y"], check=True)
    lat0, lon0 = at(rows, t, "lat"), at(rows, t, "lon")
    alt0 = at(rows, t, "alt_m")
    yaw0 = at(rows, t, "gb_yaw") % 360
    pitch0 = at(rows, t, "gb_pitch")

    img = plt.imread(frame)
    fig, ax = plt.subplots(figsize=(16, 9))
    ax.imshow(img)
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")

    # маршрут: видимые куски сплошным, скрытые за рельефом — пунктиром
    proj = [None if p is None else
            project(lat0, lon0, alt0, yaw0, pitch0, f, p[0], p[1], dem.elev(*p))
            for p in dense]
    for a, b in zip(proj, proj[1:]):
        if a is None or b is None:
            continue
        style = dict(color="#ff1744", lw=3) if not (a[3] or b[3]) else \
                dict(color="#ff1744", lw=1.5, ls=":", alpha=0.7)
        ax.plot([a[0], b[0]], [a[1], b[1]], **style)

    zr = [(ZONE[0][0], ZONE[0][1]), (ZONE[0][0], ZONE[1][1]),
          (ZONE[1][0], ZONE[1][1]), (ZONE[1][0], ZONE[0][1]), (ZONE[0][0], ZONE[0][1])]
    zproj = [project(lat0, lon0, alt0, yaw0, pitch0, f, la, lo, dem.elev(la, lo))
             for la, lo in zr]
    for a, b in zip(zproj, zproj[1:]):
        if a and b:
            ax.plot([a[0], b[0]], [a[1], b[1]], color="#ffea00", lw=2, ls="--", alpha=0.9)

    c2 = project(lat0, lon0, alt0, yaw0, pitch0, f, CAMP2[0], CAMP2[1],
                 dem.elev(*CAMP2))
    if c2 and 0 <= c2[0] < W and 0 <= c2[1] < H:
        ax.plot(c2[0], c2[1], "^", color="#00e5ff", ms=16)
        ax.annotate(f"Camp2 ({c2[2]:.0f} м)", (c2[0], c2[1]), textcoords="offset points",
                    xytext=(12, -8), fontsize=14, color="#00e5ff",
                    bbox=dict(fc="black", alpha=0.5, ec="none"))

    # гребень (водораздел DEM): максимум высоты на широтных разрезах
    crest = []
    for la in np.arange(39.4762, 39.4790, 0.00025):
        pl = np.interp(la, [p[0] for p in sorted(seg)], [p[1] for p in sorted(seg)])
        m_lon_ = 111320 * math.cos(math.radians(la))
        lons = np.arange(pl - 150/m_lon_, pl + 150/m_lon_, 3/m_lon_)
        k = int(np.argmax([dem.elev(la, lo) for lo in lons]))
        crest.append((la, float(lons[k])))
    cproj = [project(lat0, lon0, alt0, yaw0, pitch0, f, la, lo, dem.elev(la, lo))
             for la, lo in crest]
    drawn = False
    for a, b in zip(cproj, cproj[1:]):
        if a and b and (0 <= a[0] <= W or 0 <= b[0] <= W) and (0 <= a[1] <= H or 0 <= b[1] <= H):
            ax.plot([a[0], b[0]], [a[1], b[1]], color="#00e676", lw=3)
            drawn = True
    if not drawn:
        # гребень вне кадра: стрелка к ближайшему по углу видимому его участку
        cand = [(math.hypot((p[0]-W/2)/W, (p[1]-H/2)/H), p) for p in cproj if p]
        if cand:
            _, p = min(cand)
            vx, vy = p[0]-W/2, p[1]-H/2
            k = min((W/2-80)/abs(vx) if vx else 9e9, (H/2-80)/abs(vy) if vy else 9e9, 1.0)
            ex, ey = W/2+vx*k, H/2+vy*k
            ax.annotate("", (ex, ey), (W/2+vx*k*0.8, H/2+vy*k*0.8),
                        arrowprops=dict(arrowstyle="->", color="#00e676", lw=4))
            ax.text(30, 170, f"гребень (водораздел DEM) ВНЕ кадра — по зелёной стрелке, "
                             f"~{p[2]:.0f} м", fontsize=14, color="#00e676",
                    bbox=dict(fc="black", alpha=0.6, ec="none"))

    hit = cast(dem, lat0, lon0, alt0, ray_dir(yaw0, pitch0, W/2, H/2, W, H, f))
    side = ""
    if hit:
        m_lon = 111320 * math.cos(math.radians(hit[0]))
        best = (9e9, 0)
        for a, b in zip(seg, seg[1:]):
            ax_, ay_ = (a[1]-hit[1])*m_lon, (a[0]-hit[0])*M_LAT
            bx_, by_ = (b[1]-hit[1])*m_lon, (b[0]-hit[0])*M_LAT
            dx, dy = bx_-ax_, by_-ay_
            L2 = dx*dx + dy*dy
            if L2 == 0:
                continue
            k = max(0.0, min(1.0, (-(ax_*dx + ay_*dy)) / L2))
            d = math.hypot(ax_ + k*dx, ay_ + k*dy)
            if d < best[0]:
                cross = dx*(-ay_) - dy*(-ax_)
                best = (d, cross)
        # нитка идёт с севера на юг: положительный cross = восточнее линии
        side = (f"{'ВОСТОЧНЕЕ' if best[1] > 0 else 'ЗАПАДНЕЕ'} линии маршрута "
                f"на {best[0]:.0f} м")
    ax.text(30, 60, f"t={t} с  ось: компас {yaw0:.0f}°, наклон {pitch0:.0f}°  "
                    f"центр кадра: {side}",
            fontsize=17, color="white", bbox=dict(fc="black", alpha=0.6, ec="none"))
    ax.text(30, 115, "красная линия — плановый маршрут по гребню "
                     "(пунктир — скрыт рельефом), жёлтая — зона интереса",
            fontsize=13, color="white", bbox=dict(fc="black", alpha=0.6, ec="none"))
    fig.tight_layout(pad=0)
    out = OUT / f"t{t}_track_overlay.jpg"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(out, "| ось", f"{yaw0:.0f}/{pitch0:.0f}", "| центр:", side)
