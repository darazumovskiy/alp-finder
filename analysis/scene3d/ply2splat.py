#!/usr/bin/env python3
"""Конвертер 3DGS ply (Brush) → компактный .splat (antimatter15).

Формат .splat: 32 байта на сплат — позиция 3×f32, масштаб 3×f32 (линейный),
цвет RGBA u8 (SH0 → цвет, opacity сигмоидой), поворот-кватернион 4×u8
(128 + 128·q). Сплаты сортируются по «весомости» (объём × непрозрачность),
слабый хвост отбрасывается лимитом. Использование:

    ply2splat.py вход.ply выход.splat [макс_сплатов]
"""
import struct
import sys

import numpy as np

SH0 = 0.28209479177387814


def read_ply(path):
    with open(path, "rb") as f:
        header = b""
        while not header.endswith(b"end_header\n"):
            header += f.readline()
        lines = header.decode().splitlines()
        n = next(int(l.split()[-1]) for l in lines if l.startswith("element vertex"))
        props = [l.split()[-1] for l in lines if l.startswith("property float")]
        data = np.frombuffer(f.read(n * len(props) * 4), dtype="<f4")
    return data.reshape(n, len(props)), {p: i for i, p in enumerate(props)}


def main():
    src, dst = sys.argv[1], sys.argv[2]
    cap = int(sys.argv[3]) if len(sys.argv) > 3 else 1_500_000
    v, idx = read_ply(src)
    pos = v[:, [idx["x"], idx["y"], idx["z"]]]
    scale = np.exp(v[:, [idx["scale_0"], idx["scale_1"], idx["scale_2"]]])
    rot = v[:, [idx["rot_0"], idx["rot_1"], idx["rot_2"], idx["rot_3"]]]
    rot = rot / np.linalg.norm(rot, axis=1, keepdims=True)
    col = 0.5 + SH0 * v[:, [idx["f_dc_0"], idx["f_dc_1"], idx["f_dc_2"]]]
    op = 1.0 / (1.0 + np.exp(-v[:, idx["opacity"]]))

    # фильтр «игл»: игла = одно большое измерение и два малых (луч),
    # а «блин» (два больших, одно тонкое) — нормальный поверхностный мазок,
    # его НЕ трогаем (урок 18.08: фильтр по макс/мин вырезал поверхность)
    ss = np.sort(scale, axis=1)                  # s0 <= s1 <= s2
    needle = ss[:, 2] / np.maximum(ss[:, 1], 1e-9)
    good = (needle < 8.0) & (ss[:, 2] < 3.0)
    print(f"иглы (s2/s1>=8): {(needle >= 8.0).sum()}, "
          f"гиганты >3м: {(ss[:, 2] >= 3.0).sum()} — выброшены")
    pos, scale, rot, col, op = (a[good] for a in (pos, scale, rot, col, op))

    weight = scale.prod(axis=1) * op
    order = np.argsort(-weight)[:cap]
    # обратно отсортируем по весомости для прогрессивной загрузки
    pos, scale, rot, col, op = (a[order] for a in (pos, scale, rot, col, op))

    n = len(pos)
    buf = np.zeros(n, dtype=[("pos", "<f4", 3), ("scale", "<f4", 3),
                             ("rgba", "u1", 4), ("quat", "u1", 4)])
    buf["pos"] = pos
    buf["scale"] = scale
    buf["rgba"][:, :3] = np.clip(col * 255, 0, 255).astype(np.uint8)
    buf["rgba"][:, 3] = np.clip(op * 255, 0, 255).astype(np.uint8)
    buf["quat"] = np.clip(rot * 128 + 128, 0, 255).astype(np.uint8)
    buf.tofile(dst)
    print(f"{src}: {len(v)} → {n} сплатов, {n*32/2**20:.1f} МБ")


if __name__ == "__main__":
    main()
