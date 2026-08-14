"""Одноразовая схема облёта DJI_20260814114556_0002_Z: где висел дрон и куда смотрел."""
import math
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from geoproject import Dem

VIDEO_TSV = "data/drive/2026-08-14/drone/DJI_20260814114556_0002_Z.MP4.gps.tsv"
OUT = "analysis/fullframe/loc-114556/flight_map.png"

# центр области и метры-на-градус
LAT0, LON0 = 39.4830, 73.5851
M_LAT = 111320.0
M_LON = 111320.0 * math.cos(math.radians(LAT0))

def xy(lat, lon):
    return (lon - LON0) * M_LON, (lat - LAT0) * M_LAT

# трек дрона
rows = []
with open(VIDEO_TSV) as f:
    next(f)
    for line in f:
        p = line.rstrip("\n").split("\t")
        rows.append((float(p[0]), float(p[1]), float(p[2]), float(p[8]), float(p[9])))

# рельеф: сетка 300x300 м вокруг центра
dem = Dem()
half = 150
gx = np.linspace(-half, half, 121)
gy = np.linspace(-half, half, 121)
Z = np.zeros((len(gy), len(gx)))
for i, y in enumerate(gy):
    for j, x in enumerate(gx):
        Z[i, j] = dem.elev(LAT0 + y / M_LAT, LON0 + x / M_LON)

fig, ax = plt.subplots(figsize=(11, 11))
cs = ax.contour(gx, gy, Z, levels=np.arange(4400, 4900, 10), colors="#999", linewidths=0.5)
ax.clabel(cs, cs.levels[::2], fontsize=7, fmt="%d")

# трек (дрон почти висит — точки)
tx = [xy(r[1], r[2])[0] for r in rows]
ty = [xy(r[1], r[2])[1] for r in rows]
ax.plot(tx, ty, "-", color="#1565c0", lw=1.5, alpha=0.8)
ax.plot(tx[0], ty[0], "o", color="#1565c0", ms=8)
ax.annotate("дрон (висит весь ролик)\n4611 м, 2:49 полёта", (tx[0], ty[0]),
            textcoords="offset points", xytext=(12, 10), fontsize=10, color="#1565c0")

# лучи взгляда и проекции центров кадров
views = [
    (44, 39.482621, 73.584953, "t=0:44  gb_yaw −130°, pitch −50°\nточка 4576 м, дист. 46 м"),
    (151, 39.482699, 73.584844, "t=2:31  gb_yaw −132°, pitch −66°\nточка 4553 м, дист. 59 м"),
]
colors = {44: "#e65100", 151: "#8e24aa"}
for t, plat, plon, label in views:
    r = min(rows, key=lambda r: abs(r[0] - t))
    dx0, dy0 = xy(r[1], r[2])
    dx1, dy1 = xy(plat, plon)
    c = colors[t]
    ax.annotate("", (dx1, dy1), (dx0, dy0),
                arrowprops=dict(arrowstyle="->", color=c, lw=2))
    ax.plot(dx1, dy1, "*", color=c, ms=16)
    ax.annotate(label, (dx1, dy1), textcoords="offset points", xytext=(10, -26),
                fontsize=9, color=c)

# сектор обзора за весь ролик: gb_yaw −130…−162°
drone_x, drone_y = tx[0], ty[0]
for yaw in (-130, -162):
    az = math.radians(yaw)
    ax.plot([drone_x, drone_x + 90 * math.sin(az)],
            [drone_y, drone_y + 90 * math.cos(az)], "--", color="#1565c0", lw=1, alpha=0.5)
ax.annotate("сектор камеры за ролик\n(yaw −130…−162°, pitch −48…−66°)",
            (drone_x - 75, drone_y - 55), fontsize=9, color="#1565c0", alpha=0.8)

# известные объекты-ориентиры
refs = [
    (39.48327, 73.58513, "кластер вещей\n(стакан/пенка, 4559 м)"),
    (39.483176, 73.585463, "верёвка/вещи\n(триангуляция, 4529 м)"),
    (39.483135, 73.584891, "вещь 4557 м"),
]
for rlat, rlon, rlabel in refs:
    rx, ry = xy(rlat, rlon)
    ax.plot(rx, ry, "s", color="#c62828", ms=9)
    ax.annotate(rlabel, (rx, ry), textcoords="offset points", xytext=(8, 6),
                fontsize=9, color="#c62828")

ax.set_xlim(-half, half)
ax.set_ylim(-half, half)
ax.set_aspect("equal")
ax.set_xlabel("м к востоку от 73.5851°E")
ax.set_ylabel("м к северу от 39.4830°N")
ax.set_title("DJI_20260814114556_0002_Z — облёт 14.08: дрон висит и разглядывает\nстенку трещины в 50–60 м южнее кластера вещей")
ax.grid(alpha=0.2)
fig.tight_layout()
fig.savefig(OUT, dpi=130)
print(OUT)
