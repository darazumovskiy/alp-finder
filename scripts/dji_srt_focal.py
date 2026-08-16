#!/usr/bin/env python3
"""Извлечение заявленного фокусного расстояния и зума из DJI-SRT.

Штатный сайдкар `<видео>.MP4.gps.tsv` (scripts/dji_meta_gps.py) фокусного не
содержит — во встроенном data-потоке M30T такого поля нет, и конвейер
восстанавливает масштаб самокалибровкой по панорамированию
(docs/focal-length-calibration.md). Но SRT, который камера пишет рядом с
роликом, несёт `focal_len` и `dzoom_ratio` покадрово.

Это НЕ замена самокалибровке. `focal_len` из SRT дан в миллиметрах, и перевод
в пиксели требует размера матрицы, который у M30T в зум-режиме не паспортизован;
брать его на веру — ровно та ошибка, от которой предостерегает
docs/focal-length-calibration.md. Сайдкар отсюда нужен для другого: это
независимый ряд, против которого самокалибровку можно ПРОВЕРИТЬ, и признак
момента смены зума — там оценка по 20-кадровой базе заведомо испорчена.

Использование:
  python3 scripts/dji_srt_focal.py <видео.MP4> [ещё видео...]

Рядом с видео появится <имя>.MP4.focal.tsv: time_s, focal_mm, dzoom, focal_eff
(focal_eff = focal_mm * dzoom — заявленный итоговый масштаб) и zoom_change —
метка кадра, где заявленный масштаб отличается от предыдущего.
"""

import re
import sys
from pathlib import Path

# [focal_len: 480.00] [dzoom_ratio: 1.18]
FOCAL_RE = re.compile(r"\[focal_len:\s*([0-9.]+)\]\s*\[dzoom_ratio:\s*([0-9.]+)\]")
# 00:00:12,345 --> 00:00:12,378  — берём начало реплики
TIME_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->")


def parse(srt: Path):
    """[(time_s, focal_mm, dzoom)] по всем репликам SRT."""
    out = []
    t = None
    for line in srt.open(encoding="utf-8", errors="replace"):
        m = TIME_RE.search(line)
        if m:
            h, mi, s, ms = (int(x) for x in m.groups())
            t = h * 3600 + mi * 60 + s + ms / 1000
            continue
        m = FOCAL_RE.search(line)
        if m and t is not None:
            out.append((t, float(m.group(1)), float(m.group(2))))
            t = None
    return out


def write(video: Path):
    srt = video.with_suffix(".SRT")
    if not srt.exists():
        srt = video.with_suffix(".srt")
    if not srt.exists():
        print(f"{video.name}: SRT рядом нет, пропуск")
        return None

    rows = parse(srt)
    if not rows:
        print(f"{video.name}: в SRT нет полей focal_len/dzoom_ratio, пропуск")
        return None

    out = video.with_suffix(video.suffix + ".focal.tsv")
    prev = None
    changes = 0
    with out.open("w", encoding="utf-8") as f:
        f.write("time_s\tfocal_mm\tdzoom\tfocal_eff\tzoom_change\n")
        for t, mm, dz in rows:
            eff = mm * dz
            changed = prev is not None and abs(eff - prev) > 1e-6
            changes += changed
            f.write(f"{t:.3f}\t{mm:.2f}\t{dz:.2f}\t{eff:.2f}\t{int(changed)}\n")
            prev = eff

    effs = sorted({r[1] * r[2] for r in rows})
    print(f"{video.name}: реплик {len(rows)}, {rows[-1][0]:.0f} с -> {out.name}")
    print(f"  заявленный масштаб focal_eff: {effs[0]:.0f} .. {effs[-1]:.0f} "
          f"({len(effs)} различных значений), смен зума {changes}")
    return out


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    for arg in sys.argv[1:]:
        write(Path(arg))


if __name__ == "__main__":
    main()
