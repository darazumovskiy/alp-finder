#!/usr/bin/env python3
"""Очередь зон вдоль линии падения: вершина → вещи → долина.

На зону: выборка кадров (extract_zone) → SfM (sfm_zone) → лучшая модель →
экспорт → тренировка Brush (GPU) → конвертация с фильтром игл → части →
геопривязка → манифест → деплой. Идемпотентно: зона с готовыми частями в
scenes/ пропускается; упавшая зона не валит очередь. ~1-1.5 ч на зону.

Запуск: nohup .../python queue_zones.py > лог 2>&1 &
"""
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path("/Users/d.razumovskiy/work/alp-finder")
SC = ROOT / "analysis/scene3d"
DATA = SC / "data"
SCENES = ROOT / "analysis/viewer/scenes"
PY = str(ROOT / "analysis/.venv/bin/python")
BRUSH = str(Path.home() / "tools/brush/brush-app-aarch64-apple-darwin/brush_app")
M_LAT = 111132.0
M_LON = 111320.0 * math.cos(math.radians(39.48))
HALF_LAT = 200.0 / M_LAT          # полузона 200 м
HALF_LON = 200.0 / M_LON
PEAK = (39.46882, 73.58942)       # вершина/гребень (высота 6096 в реестре)


def sh(args, log_tag, timeout=None):
    print(f"[{log_tag}] $ {' '.join(str(a) for a in args)}", flush=True)
    r = subprocess.run([str(a) for a in args], capture_output=True, text=True,
                       timeout=timeout)
    tail = (r.stdout + r.stderr).strip().splitlines()[-4:]
    for line in tail:
        print(f"[{log_tag}] {line}", flush=True)
    return r.returncode == 0


def zone_centers():
    kml = (ROOT / "docs/nakhodki/search_vectors.kml").read_text()
    m = re.search(r"<name>Fall line[^<]*</name>.*?<coordinates>([^<]+)</coordinates>",
                  kml, re.S)
    line = []
    for c in m.group(1).split():
        p = c.split(",")
        line.append((float(p[1]), float(p[0])))
    # верхний сегмент: от вершины вниз к началу линии (вещам)
    top = []
    a, b = PEAK, line[0]
    dist = math.hypot((b[0] - a[0]) * M_LAT, (b[1] - a[1]) * M_LON)
    n = int(dist // 300)
    for i in range(n + 1):
        t = i / max(n, 1)
        top.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    # вдоль линии падения через ~300 м
    down, acc = [], 0.0
    for p, q in zip(line, line[1:]):
        acc += math.hypot((q[0] - p[0]) * M_LAT, (q[1] - p[1]) * M_LON)
        if acc >= 300:
            down.append(q)
            acc = 0.0
    return top + down


def do_zone(i, lat, lon):
    name = f"linia-{i:02d}"
    part0 = SCENES / f"{name}_aa"
    if part0.exists():
        print(f"[{name}] уже готова, пропуск", flush=True)
        return True
    base = DATA / name
    if not sh([PY, SC / "extract_zone.py", name,
               lat - HALF_LAT, lat + HALF_LAT, lon - HALF_LON, lon + HALF_LON,
               2000, 45, 420], name):
        return False
    n_img = len(list((base / "images").glob("*.jpg")))
    if n_img < 60:
        print(f"[{name}] кадров мало ({n_img}) — зона пропущена", flush=True)
        return False
    if not sh([PY, SC / "sfm_zone.py", name, 2], name, timeout=4 * 3600):
        return False
    best, best_n = None, 0
    for d in (base / "sparse").iterdir():
        try:
            import pycolmap
            r = pycolmap.Reconstruction(str(d))
            if r.num_reg_images() > best_n:
                best, best_n = d, r.num_reg_images()
        except Exception:
            continue
    if best is None or best_n < 25:
        print(f"[{name}] модель слабая ({best_n} камер) — пропуск", flush=True)
        return False
    print(f"[{name}] модель {best.name}: {best_n} камер", flush=True)
    if not sh([PY, SC / "export_brush.py", best, base / "images", base / "brush"],
              name):
        return False
    splats = base / "splats"
    splats.mkdir(exist_ok=True)
    if not sh([BRUSH, base / "brush", "--total-steps", 20000,
               "--max-splats", 3000000, "--export-every", 20000,
               "--export-path", splats, "--export-name", "z_{iter}.ply"],
              name, timeout=3 * 3600):
        return False
    ply = splats / "z_20000.ply"
    if not ply.exists():
        return False
    if not sh([PY, SC / "ply2splat.py", ply, splats / "zone.splat", 1500000], name):
        return False
    if not sh([PY, SC / "georef_scene.py", best, SCENES / f"{name}.geo.json"], name):
        return False
    # части под лимит хостинга
    buf = (splats / "zone.splat").read_bytes()
    parts = []
    for k in range(0, len(buf), 24 * 2 ** 20):
        pn = f"{name}_{chr(97 + len(parts))}{chr(97)}"
        (SCENES / pn).write_bytes(buf[k:k + 24 * 2 ** 20])
        parts.append("scenes/" + pn)
    man_p = SCENES / "manifest.json"
    man = json.loads(man_p.read_text())
    man["scenes"] = [s for s in man["scenes"] if s["id"] != name]
    man["scenes"].append(dict(
        id=name, title=f"Линия падения {i:02d}", parts=parts,
        geo=json.loads((SCENES / f"{name}.geo.json").read_text())))
    man_p.write_text(json.dumps(man))
    sh([PY, ROOT / "analysis/viewer/build_dist.py"], name)
    sh(["bash", ROOT / "scripts/deploy_private.sh"], name, timeout=3600)
    print(f"[{name}] ГОТОВА и задеплоена ({best_n} камер)", flush=True)
    return True


def main():
    centers = zone_centers()
    print(f"зон в очереди: {len(centers)}", flush=True)
    ok = 0
    for i, (lat, lon) in enumerate(centers, 1):
        print(f"=== зона {i}/{len(centers)}: {lat:.5f} {lon:.5f} ===", flush=True)
        try:
            ok += bool(do_zone(i, lat, lon))
        except Exception as e:
            print(f"[linia-{i:02d}] ОШИБКА: {e}", flush=True)
    print(f"итого готово зон: {ok}/{len(centers)}", flush=True)


if __name__ == "__main__":
    main()
