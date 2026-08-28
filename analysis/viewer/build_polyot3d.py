#!/usr/bin/env python3
"""«Полёт 3D»: облёт района на движке kurumdy-3d с нашими данными.

Движок — открытая страница Николая (@nickoliuzzz, github.io/kurumdy-3d):
самописный WebGL2 без библиотек, полёт WASD, прицел, карточки объектов.
Исходно воксельный; рельеф заменён на гладкую треугольную сетку
(вершины в центрах ячеек BX, высоты с квантом BZ).
Здесь он используется как шаблон (polyot3d_template.html, вырезан payload),
а данные собираются из нашего конвейера:

  рельеф    — NASA HMA 8 м + DSM-патчи 2026 г. (Dem, как весь конвейер);
  слои      — тень, крутизна 45-55°/>55°, кулуары, горизонтали 100 м —
              предрасчёт здесь, в формате движка (байтовые сетки);
  покрытие  — 3 уровня по всем роликам (analysis/coverage/coverage-map-cells.json);
  объекты   — реестр точек build_map.POINTS с фото-кропами (этап 3);
  дрон      — треки и камеры из телеметрии (этап 4).

Контракт payload (сетка метрическая, шаг BX от СЗ-угла LAT1/LON0):
  lev  uint16[H*W]  (высота-zmin)/BZ;  shade, slope — байт на ячейку;
  meta биты: 0-2 класс ручной карты (не используем), 3 — горизонталь,
  4-5 — крутизна (1=35-45, 2=45-55, 3=>55), 6 — кулуар;  acov 0..3 —
  покрытие (3=детально… 1=обзорно);  lat = LAT1 - y*BX/MLA,
  lon = LON0 + x*BX/MLO;  линии — [[x, уровень, y], …].

Запуск: analysis/.venv/bin/python analysis/viewer/build_polyot3d.py
Выход:  analysis/viewer/polyot-3d.html
"""

import base64
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
sys.path.insert(0, str(HERE))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from geoproject import Dem, HMA_PATH, _bilinear  # noqa: E402
from build_map import CAMPS, HIDDEN_KINDS, KINDS, POINTS, parse_gpx  # noqa: E402

OUT = HERE / "polyot-3d.html"
TEMPLATE = HERE / "polyot3d_template.html"
COVER_JSON = ROOT / "analysis/coverage/coverage-map-cells.json"

STATUSES = [dict(id="confirmed", title="Подтверждено"),
            dict(id="open", title="Открыто"),
            dict(id="rejected", title="Отклонено"),
            dict(id="closed", title="Закрыто")]

# Рамка полёта: район операции с запасом (внутри рамки HMA-сетки)
LAT0, LAT1 = 39.450, 39.528
LON0, LON1 = 73.552, 73.648
BX = 8.0      # метров на ячейку сетки по горизонтали
BZ = 0.25     # метров на уровень по высоте (гладкая сетка: квант мельче ячейки,
              # диапазон высот района ~2.4 км = ~9.6 тыс. уровней, uint16 хватает)
MLA = 111320.0
MLO = 111320.0 * math.cos(math.radians((LAT0 + LAT1) / 2))


def b64(arr) -> str:
    return base64.b64encode(arr.tobytes()).decode()


def terrain(dem):
    """Высоты (с патчами) на метрической сетке движка: z[H, W], метры."""
    w = int(round((LON1 - LON0) * MLO / BX))
    h = int(round((LAT1 - LAT0) * MLA / BX))
    lons = LON0 + (np.arange(w) + 0.5) * BX / MLO
    lats = LAT1 - (np.arange(h) + 0.5) * BX / MLA
    glon, glat = np.meshgrid(lons, lats)
    z = _bilinear(dem.z, (glon - dem.lon0) / dem.dlon,
                  (dem.lat0 - glat) / dem.dlat)
    if dem._patch is not None:
        off, plon0, plat0, pdlon, pdlat = dem._patch
        v = _bilinear(off, (glon - plon0) / pdlon, (plat0 - glat) / pdlat)
        z = np.where(np.isfinite(v), z + v, z)
    assert np.isfinite(z).all(), "рамка полёта вышла за рамку DEM"
    return z.astype(np.float32), w, h


def layers(z):
    """(shade, slope_deg, meta) — байтовые сетки движка."""
    gy, gx = np.gradient(z.astype(np.float64), BX)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))

    # тень: классический hillshade, солнце с СЗ (аз. 315°), высота 45°
    az, alt = math.radians(315), math.radians(45)
    aspect = np.arctan2(-gx, gy)
    sl = np.arctan(np.hypot(gx, gy))
    shade = (np.sin(alt) * np.cos(sl)
             + np.cos(alt) * np.sin(sl) * np.cos(az - aspect))
    shade = np.clip(shade, 0.05, 1.0)

    meta = np.zeros(z.shape, np.uint8)
    # бит 3: горизонталь через 100 м (смена сотни против соседа слева/сверху)
    hund = np.floor(z / 100.0)
    contour = np.zeros(z.shape, bool)
    contour[:, 1:] |= hund[:, 1:] != hund[:, :-1]
    contour[1:, :] |= hund[1:, :] != hund[:-1, :]
    meta[contour] |= 8
    # биты 4-5: класс крутизны
    st = np.zeros(z.shape, np.uint8)
    st[slope >= 35] = 1
    st[slope >= 45] = 2
    st[slope >= 55] = 3
    meta |= st << 4
    # бит 6: кулуар — вогнутость >4 м в окне ~90 м при уклоне >28°
    win = int(round(90 / BX)) | 1
    mean = cv2.blur(z, (win, win))
    meta[(mean - z > 4.0) & (slope > 28)] |= 64

    return ((shade * 255).astype(np.uint8),
            np.clip(slope, 0, 90).astype(np.uint8), meta)


def to_xy(lat, lon):
    return (round((lon - LON0) * MLO / BX, 2), round((LAT1 - lat) * MLA / BX, 2))


def line3d(dem, pts, zmin, every=1):
    out = []
    for lat, lon in pts[::every]:
        try:
            z = dem.elev(lat, lon)
        except ValueError:
            continue
        x, y = to_xy(lat, lon)
        if 0 <= x <= (LON1 - LON0) * MLO / BX and 0 <= y <= (LAT1 - LAT0) * MLA / BX:
            out.append([x, round((z - zmin + 3.0) / BZ), y])   # 3 м над рельефом
    return out


def coverage_grid(w, h):
    """acov движка (0 или 1..3, 3 = детально) из сетки покрытия конвейера."""
    cov = json.loads(COVER_JSON.read_text())
    lat0, lon0 = cov["lat0"], cov["lon0"]
    dlat, dlon = cov["dlat"], cov["dlon"]
    ni = max(max(c[0] for c in v["cells"]) for v in cov["videos"]) + 1
    nj = max(max(c[1] for c in v["cells"]) for v in cov["videos"]) + 1
    tier = np.full((ni, nj), 9, np.uint8)       # ряды с юга на север
    for v in cov["videos"]:
        for i, j, t, *_ in v["cells"]:
            if t < tier[i, j]:
                tier[i, j] = t
    ys, xs = np.mgrid[0:h, 0:w]
    lat = LAT1 - (ys + 0.5) * BX / MLA
    lon = LON0 + (xs + 0.5) * BX / MLO
    ci = np.floor((lat - lat0) / dlat).astype(int)
    cj = np.floor((lon - lon0) / dlon).astype(int)
    inside = (ci >= 0) & (ci < ni) & (cj >= 0) & (cj < nj)
    t = np.where(inside, tier[np.clip(ci, 0, ni - 1), np.clip(cj, 0, nj - 1)], 9)
    return np.where(t <= 2, 3 - t, 0).astype(np.uint8)


def thumb(path, max_side=320):
    """Миниатюра-кадр как data URI, None если файла нет."""
    img = cv2.imread(str(ROOT / path))
    if img is None:
        return None
    k = max_side / max(img.shape[:2])
    if k < 1:
        img = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 72])
    return "data:image/jpeg;base64," + base64.b64encode(enc).decode() if ok else None


def registry_points(z, zmin, w, h):
    """apts движка из реестра карты: все точки со статусами и кадрами.

    Уровень маркера — рельеф в ячейке, не высота карточки: точка лежит на
    склоне, а расхождение высот (датумы, вилки) топило бы маркер в вокселях.
    """
    apts = []
    for p in POINTS:
        if p["kind"] in HIDDEN_KINDS:  # временно скрытые категории (см. build_map)
            continue
        x, y = to_xy(p["lat"], p["lon"])
        if not (0 <= x < w and 0 <= y < h):
            continue
        lv = round((float(z[min(int(y), h - 1), min(int(x), w - 1)]) - zmin) / BZ)
        bits = []
        if p.get("video"):
            bits.append(p["video"] + (f" {p['tc']}" if p.get("tc") else ""))
        if p.get("unc"):
            bits.append(f"±{p['unc']} м")
        if p.get("size"):
            bits.append(f"размер {p['size']}")
        d = p.get("desc", "")
        if bits:
            d = " · ".join(bits) + "\n" + d
        imgs = [i for i in p.get("imgs", []) if (ROOT / i).exists()]
        apts.append(dict(
            x=x, y=y, l=lv,
            n=p["name"], d=d, k=p["kind"], s=p["status"], c=str(p["conf"]),
            th=thumb(imgs[0]) if imgs else None, f=imgs))
    return apts


def drone_layer(dem, zmin, w, h):
    """(cams, fan) из кэша плеера: позиция старта каждого ролика + лучи взгляда.

    cams: [x, y, l, токен-время, высота, url плеера] — E открывает ролик
    в плеере «Полёт» карты (map.html#flight=…). fan: сегменты дрон→точка,
    куда смотрел центр кадра (сэмплы кэша flight_cache, прорежены).
    """
    cams, fan = [], []
    for mp in sorted((HERE / "flights").glob("*/meta.json")):
        meta = json.loads(mp.read_text())
        samples = meta.get("samples") or []
        if not samples:
            continue
        t0 = samples[0]
        x, y = to_xy(t0[1], t0[2])
        if not (0 <= x < w and 0 <= y < h):
            continue
        name = mp.parent.name                     # DJI_20260811153236_0001_Z
        token = name.split("_")[1] if "_" in name else name
        lv = round((t0[3] - zmin) / BZ)
        cams.append([x, y, lv, token, round(t0[3]),
                     f"map.html#flight={name}"])
        step = max(1, len(samples) // 16)
        for s in samples[::step]:
            tgt = s[8] if len(s) > 8 else None
            if not tgt:
                continue
            x1, y1 = to_xy(s[1], s[2])
            x2, y2 = to_xy(tgt[0], tgt[1])
            if not (0 <= x2 < w and 0 <= y2 < h and 0 <= x1 < w and 0 <= y1 < h):
                continue
            # tgt[2] — дистанция до склона, высоту конца луча берём из рельефа
            l1 = round((s[3] - zmin) / BZ, 1)
            l2 = round((dem.elev(tgt[0], tgt[1]) - zmin) / BZ, 1)
            fan.append([x1, l1, y1, x2, l2, y2])
    return cams, fan


def eye_layer(zt, zmin, w, h):
    """Индекс «глазами дрона»: поза камеры и фокусное каждого кадра кеша.

    На флайт: dict(v=имя, s=[[i, x, z, yb, yaw, pitch, f1024, fx, fz, fyb, fd]…])
    — кадр flights/<v>/f%04d.jpg номер i, позиция в сетке (x, z, yb — высота
    в блоках), компасный yaw/pitch подвеса, фокусное в пикселях кадра шириной
    1024; fx/fz/fyb — ТОЧКА СЪЁМКИ: куда упёрся луч оси камеры в рельеф
    (блоки), fd — длина луча в блоках (мера GSD: чем меньше, тем детальнее).
    Рантайм подбирает кадры по близости точки съёмки к прицелу пользователя —
    «покажи кадры, которые снимали то, куда я смотрю». Кадры, чей луч не
    упирается в рельеф ближе 2.5 км (горизонт/небо), выбрасываются.
    Поза интерполируется по сэмплам меты (шаг 2 с), фокусное — coverage tsv;
    кадры без фокусного пропускаются (проецировать нечем).
    """
    import bisect

    def footprint(x, zz, alt, yaw, pitch):
        yr, pr = math.radians(yaw), math.radians(pitch)
        cp = math.cos(pr)
        dx, dy, dz = math.sin(yr) * cp, math.sin(pr), -math.cos(yr) * cp
        px, py, pz = x * BX, alt, zz * BX
        for _ in range(int(2500 / 6)):
            px += dx * 6; py += dy * 6; pz += dz * 6
            gx, gz = px / BX, pz / BX
            if not (0 <= gx < w - 1 and 0 <= gz < h - 1):
                return None
            if py <= zt[int(gz), int(gx)]:
                d = math.hypot(px - x * BX, py - alt, pz - zz * BX)
                return (round(gx, 1), round(gz, 1),
                        round((py - zmin) / BX, 1), round(d / BX, 1))
        return None
    sys.path.insert(0, str(ROOT / "analysis"))
    from flight_cache import load_coverage, nearest_cov

    def lerp_yaw(a, b, t):
        d = (b - a + 180) % 360 - 180
        return (a + d * t) % 360

    eyes = []
    for mp in sorted((HERE / "flights").glob("*/meta.json")):
        meta = json.loads(mp.read_text())
        samples = meta.get("samples") or []
        if len(samples) < 2:
            continue
        stem = mp.parent.name
        video = next(iter((ROOT / "data/drive").rglob(stem + ".MP4")), None)
        cov = load_coverage(video) if video else []
        if not cov:
            continue
        ts = [s[0] for s in samples]
        rows = []
        step = meta.get("frame_step", 5.0)
        for i in range(meta.get("n_frames") or 0):
            t = i * step
            row = nearest_cov(cov, t, max_dt=6.0)
            if not row or not row[1]:
                continue
            j = min(max(bisect.bisect_left(ts, t), 1), len(samples) - 1)
            a, b = samples[j - 1], samples[j]
            u = 0.0 if b[0] == a[0] else max(0.0, min(1.0, (t - a[0]) / (b[0] - a[0])))
            lat = a[1] + (b[1] - a[1]) * u
            lon = a[2] + (b[2] - a[2]) * u
            alt = a[3] + (b[3] - a[3]) * u
            yaw = lerp_yaw(a[5], b[5], u)
            pitch = a[6] + (b[6] - a[6]) * u
            x, z = to_xy(lat, lon)
            if not (0 <= x < w and 0 <= z < h):
                continue
            fp = footprint(x, z, alt, yaw, pitch)
            if fp is None:          # луч в небо/за горизонт — кадр не привязать
                continue
            rows.append([i, x, z, round((alt - zmin) / BX, 2),
                         round(yaw, 1), round(pitch, 1),
                         round(row[1] * 1024 / 1920),
                         fp[0], fp[1], fp[2], fp[3]])
        if rows:
            eyes.append(dict(v=stem, s=rows))
    return eyes


def build():
    dem = Dem(HMA_PATH)
    z, w, h = terrain(dem)
    zmin = math.floor(z.min() / BZ) * BZ
    lev = np.round((z - zmin) / BZ).astype(np.uint16)
    shade, slope, meta = layers(z)
    acov = coverage_grid(w, h)                  # покрытие съёмкой, 3 уровня

    camps, wpts = [], []
    for name, lat, lon, _alt in CAMPS:
        x, y = to_xy(lat, lon)
        if not (0 <= x < w and 0 <= y < h):
            continue
        zc = dem.elev(lat, lon)
        lv = round((zc - zmin) / BZ)
        camps.append([x, y, lv, name, round(zc)])
        wpts.append([x, y, lv, name])

    track, _ = parse_gpx(ROOT / "docs/marshrut/plan-track.gpx")
    lines = dict(route=line3d(dem, track, zmin, every=2),
                 fall=[], prio=[], corridor=[])

    apts = registry_points(z, zmin, w, h)
    cams, fan = drone_layer(dem, zmin, w, h)
    eye = eye_layer(z, zmin, w, h)

    # детальные вставки сцен (bake_insert.py, scene3d_insert.py) — грузятся
    # страницей лениво. Зонные insert_z-* заменены плитками склона и в
    # список не идут (файлы остаются для истории)
    inserts = []
    for p in sorted((HERE / "ortho").glob("insert_*.json")):
        if p.name.startswith("insert_z-"):
            continue
        try:
            ins = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if ins.get("weak"):
            continue
        if ins.get("name") in HIDDEN_KINDS:  # вставка временно скрытой сцены
            continue
        inserts.append(dict(file=f"ortho/{p.name}",
                            title=ins.get("title", p.stem)))

    # плитки склона (slope_tiles.py): одна группа-галка, файлы лениво
    tiles = [dict(file=f"ortho/tiles3d/{p.name}")
             for p in sorted((HERE / "ortho/tiles3d").glob("t_*.json"))]

    # появление: над ледником севернее кластера вещей, взгляд на юг —
    # в кадре сразу склон с находками, стена LOOK и гребень
    sp_lat, sp_lon = 39.4915, 73.5850
    sx, sy = to_xy(sp_lat, sp_lon)
    spawn = dict(x=sx, z=sy, yaw=0.06, pitch=-0.10,
                 l=round((dem.elev(sp_lat, sp_lon) + 300 - zmin) / BZ))

    payload = dict(
        spawn=spawn,
        W=w, H=h, BX=BX, BZ=BZ, zmin=zmin,
        LAT0=LAT0, LAT1=LAT1, LON0=LON0, LON1=LON1, MLA=MLA, MLO=MLO,
        lev=b64(lev), shade=b64(shade), slope=b64(slope),
        meta=b64(meta), acov=b64(acov),
        tex_a="", tex_b="",
        apts=apts,
        kinds=[dict(id=k, title=t) for k, t in KINDS if k not in HIDDEN_KINDS],
        statuses=STATUSES, lines=lines,
        camps=camps, wpts=wpts,
        items=[], finds=[], shel=[], cams=cams, fan=fan, eye=eye,
        inserts=inserts, tiles=tiles)

    html = TEMPLATE.read_text("utf-8").replace(
        "__PAYLOAD__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    OUT.write_text(html, "utf-8")
    covered = float((acov > 0).mean())
    print(f"{OUT.name}: {OUT.stat().st_size / 2**20:.1f} МБ, сетка {w}x{h}, "
          f"высоты {z.min():.0f}-{z.max():.0f} м, точек {len(apts)} "
          f"(с кадрами {sum(1 for a in apts if a['th'])}), покрытие {covered:.0%} "
          f"рамки, полётов {len(cams)}, лучей {len(fan)}, лагерей {len(camps)}, "
          f"маршрут {len(lines['route'])} тчк")


if __name__ == "__main__":
    build()
