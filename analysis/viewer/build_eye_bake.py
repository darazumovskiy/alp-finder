#!/usr/bin/env python3
"""Фото-подложка «Полёт 3D»: запекание кадров съёмки в одну текстуру рельефа.

Зачем. Режим «глазами дрона» проецирует кадры в реальном времени, но живых
проекторов всего 12 — гору ими не обклеить (зум-кадр покрывает пятно 15-100 м,
получается конфетти). Здесь та же задача решается ЗАРАНЕЕ и ВСЕМИ кадрами:
для каждого пикселя подложки (SCALE м) из всех ~2100 кадров выбирается тот,
что снял это место детальнее всех, и берётся его цвет. Выход — одна сплошная
картинка на весь район: в браузере грузится как обычная текстура, без
тормозов, покрытие везде, где реально летал дрон.

Как. По каждому кадру пускается сетка лучей RAYS_X×RAYS_Y через его кадр
(поза и углы подвеса из телеметрии, фокусное из coverage). Луч маршируется
до рельефа (сетка высот движка) — точка попадания и есть видимая поверхность,
заслонённые гребнем места луч не достанет (окклюзия бесплатно). В точке
запоминается цвет пикселя кадра и «цена» = размер пикселя кадра на земле
(дистанция/фокусное): меньше — детальнее. Лучший по цене кадр побеждает.

Честные ограничения: телеметрия врёт (кадры могут лежать со сдвигом метры —
десятки метров), экспозиция роликов не выравнивается (лоскутность), рельеф —
сетка 8 м. Это осознанная «эвристическая дорисовка» по запросу оператора,
не фотограмметрия; точную сцену даст реконструкция (сплаты).

Запуск: analysis/.venv/bin/python analysis/viewer/build_eye_bake.py
Выход:  analysis/viewer/eyebake/base.webp (+ meta.json)
"""

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from build_polyot3d import BX, Dem, HMA_PATH, eye_layer, terrain  # noqa: E402

SCALE = 3.0        # метров на пиксель подложки
RAYS_X, RAYS_Y = 160, 90  # сетка лучей по кадру (шаг ~6 px кадра 1024x576):
                          # у широкого кадра шаг по земле ~2.5 м ≈ пиксель
                          # подложки — честное покрытие плотное, а одиночные
                          # брызги можно смело чистить
STEP = 4.0         # шаг марша луча, м
T_MIN, T_MAX = 20.0, 1500.0   # ближе — мусор от ошибок высоты, дальше — мыло
OUT_DIR = HERE / "eyebake"


def bake():
    dem = Dem(HMA_PATH)
    zt, w, h = terrain(dem)          # высоты, метры; сетка блоков BX
    zmin = math.floor(zt.min() / 2.5) * 2.5
    eyes = eye_layer(zt, zmin, w, h)
    frames = [(f["v"], s) for f in eyes for s in f["s"]]
    print(f"кадров к запеканию: {len(frames)}")

    W2, H2 = int(w * BX / SCALE), int(h * BX / SCALE)
    prio = np.full((H2, W2), np.inf, np.float32)
    rgb = np.zeros((H2, W2, 3), np.uint8)

    # сетка лучей в NDC кадра: u вправо, v вверх
    uu, vv = np.meshgrid(np.linspace(-1, 1, RAYS_X), np.linspace(-1, 1, RAYS_Y))
    uu, vv = uu.ravel(), vv.ravel()
    # соответствующие пиксели кадра 1024x576
    px = np.clip(((uu + 1) / 2 * 1023).astype(int), 0, 1023)
    py = np.clip(((1 - vv) / 2 * 575).astype(int), 0, 575)

    done = 0
    for v, s in frames:
        i, x, z, yb, yaw, pitch, f1024 = s[0], s[1], s[2], s[3], s[4], s[5], s[6]
        img = cv2.imread(str(HERE / "flights" / v / f"f{i:04d}.jpg"))
        if img is None:
            continue
        yr, pr = math.radians(yaw), math.radians(pitch)
        cp = math.cos(pr)
        fwd = np.array([math.sin(yr) * cp, math.sin(pr), -math.cos(yr) * cp])
        right = np.array([-fwd[2], 0.0, fwd[0]])
        right /= max(np.linalg.norm(right), 1e-6)
        upi = np.cross(right, fwd)
        tx = 512.0 / f1024                      # tan половинного поля зрения
        ty = 288.0 / f1024
        dirs = (fwd[None, :]
                + uu[:, None] * tx * right[None, :]
                + vv[:, None] * ty * upi[None, :])
        dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)

        p0 = np.array([x * BX, zmin + yb * BX, z * BX])
        n = len(dirs)
        t = np.full(n, T_MIN)
        alive = np.ones(n, bool)
        hit_t = np.full(n, np.nan)
        while alive.any():
            pts = p0[None, :] + dirs * t[:, None]
            gx = (pts[:, 0] / BX).astype(int)
            gz = (pts[:, 2] / BX).astype(int)
            inside = (gx >= 0) & (gx < w) & (gz >= 0) & (gz < h)
            dead = alive & ~inside
            alive[dead] = False
            gi = np.where(alive)[0]
            if not len(gi):
                break
            below = pts[gi, 1] <= zt[gz[gi], gx[gi]]
            hits = gi[below]
            hit_t[hits] = t[hits]
            alive[hits] = False
            t[alive] += STEP
            over = alive & (t > T_MAX)
            alive[over] = False

        ok = np.isfinite(hit_t)
        if not ok.any():
            continue
        tt = hit_t[ok]
        pts = p0[None, :] + dirs[ok] * tt[:, None]
        bx2 = (pts[:, 0] / SCALE).astype(int)
        bz2 = (pts[:, 2] / SCALE).astype(int)
        m = (bx2 >= 0) & (bx2 < W2) & (bz2 >= 0) & (bz2 < H2)
        bx2, bz2, tt = bx2[m], bz2[m], tt[m]
        cols = img[py[ok][m], px[ok][m]]        # BGR
        pv = (tt / f1024).astype(np.float32)    # м/пиксель кадра — «цена»
        # в пределах кадра дубликаты по ячейке: оставляем лучший
        order = np.argsort(pv)
        idx = bz2[order] * W2 + bx2[order]
        first = np.unique(idx, return_index=True)[1]
        sel = order[first]
        fz, fx, fp, fc = bz2[sel], bx2[sel], pv[sel], cols[sel]
        better = fp < prio[fz, fx]
        prio[fz[better], fx[better]] = fp[better]
        rgb[fz[better], fx[better]] = fc[better]
        done += 1
        if done % 200 == 0:
            print(f"  {done}/{len(frames)}…")

    filled = np.isfinite(prio)
    print(f"запечено кадров: {done}, покрыто {filled.mean()*100:.1f}% подложки")

    # одиночные брызги (точка почти без запечённых соседей) — случайные дальние
    # лучи, дают «соль-перец»: убираем до заливки
    known = filled.astype(np.uint8)
    nb = cv2.filter2D(known, -1, np.ones((3, 3), np.uint8),
                      borderType=cv2.BORDER_CONSTANT) - known
    solo = (known == 1) & (nb <= 1)
    known[solo] = 0
    print(f"убрано брызг: {solo.sum()}")

    # затянуть дырки сеточного семплинга: 3 прохода заливки от соседей
    for _ in range(3):
        k = np.ones((3, 3), np.uint8)
        cnt = cv2.filter2D(known, -1, k, borderType=cv2.BORDER_CONSTANT)
        acc = cv2.filter2D(rgb.astype(np.float32), -1, k.astype(np.float32),
                           borderType=cv2.BORDER_CONSTANT)
        grow = (known == 0) & (cnt >= 3)
        rgb[grow] = (acc[grow] / cnt[grow, None]).astype(np.uint8)
        known[grow] = 1
    alpha = (known * 255).astype(np.uint8)
    print(f"после заливки дырок: {known.mean()*100:.1f}%")

    OUT_DIR.mkdir(exist_ok=True)
    bgra = np.dstack([rgb, alpha])
    cv2.imwrite(str(OUT_DIR / "base.webp"), bgra,
                [cv2.IMWRITE_WEBP_QUALITY, 82])
    (OUT_DIR / "meta.json").write_text(json.dumps(
        dict(w=W2, h=H2, scale=SCALE, frames=done)))
    size = (OUT_DIR / "base.webp").stat().st_size / 1e6
    print(f"eyebake/base.webp: {W2}x{H2} @{SCALE} м/пикс, {size:.1f} МБ")


if __name__ == "__main__":
    bake()
