#!/usr/bin/env python3
"""Гигапанорама: тайловый рендер сшивки в DZI-пирамиду + зум-страница.

Холст в десятки тысяч пикселей не помещается в память и не открывается как
JPEG, поэтому продукт — пирамида тайлов Deep Zoom (как у карт) и HTML-страница
с OpenSeadragon: колесо мыши приближает от обзора участка до сантиметровых
деталей крупных планов.

Вход — папка сшивки merge_mosaics.py (запущенной с --register-only и --scale,
задающим детализацию врезок): mosaics.tsv с гомографиями полотен на общий холст.
Рендер идёт горизонтальными полосами в memmap-файл на диске, затем пирамида
уровней и тайлы. Отметки из findings-marks.tsv с пиксельными якорями на
пришитых полотнах выводятся поверх панорамы маркерами.

Использование:
  .venv/bin/python tile_pano.py --merged stitch/pano-veshchi --out stitch/pano-veshchi/pano

Выход в --out: pano.dzi, pano_files/<уровень>/<x>_<y>.jpg, index.html
(OpenSeadragon с CDN — для просмотра нужен интернет), canvas.dat удаляется.
"""

import argparse
import csv
import json
import math
from pathlib import Path

import cv2
import numpy as np

from stitch import feather

TILE = 1024           # крупнее тайл — меньше файлов (лимиты статик-хостингов)
STRIP = 2048          # высота полосы рендера, чётная (для точной пирамиды)
BLACK = 4             # яркость ниже — «пусто»

HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>{title}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  html, body {{ margin: 0; height: 100%; background: #111; }}
  #pano {{ width: 100%; height: 100%; }}
  .mark {{ border: 2px solid #ffd200; border-radius: 50%;
           width: 26px; height: 26px; margin: -13px 0 0 -13px; }}
  .mark span {{ position: absolute; left: 28px; top: -2px; color: #ffd200;
                font: bold 13px sans-serif; white-space: nowrap;
                text-shadow: 0 0 4px #000, 0 0 4px #000; }}
</style></head><body>
<div id="pano"></div>
<script src="https://cdn.jsdelivr.net/npm/openseadragon@4.1/build/openseadragon/openseadragon.min.js"></script>
<script>
const marks = {marks_json};
const viewer = OpenSeadragon({{
  id: "pano", tileSources: "pano.dzi", prefixUrl:
  "https://cdn.jsdelivr.net/npm/openseadragon@4.1/build/openseadragon/images/",
  maxZoomPixelRatio: 3, showNavigator: true,
}});
viewer.addHandler("open", () => {{
  const w = viewer.world.getItemAt(0).getContentSize().x;
  for (const m of marks) {{
    const el = document.createElement("div");
    el.className = "mark";
    el.innerHTML = "<span>" + m.label + "</span>";
    viewer.addOverlay(el, new OpenSeadragon.Point(m.x / w, m.y / w));
  }}
}});
</script></body></html>
"""


def read_items(merged: Path):
    with (merged / "mosaics.tsv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    return [(Path(r["mosaic"]),
             np.float64([float(r[f"h{i}{j}"]) for i in range(3)
                         for j in range(3)]).reshape(3, 3)) for r in rows]


def canvas_size(items):
    pts = []
    for p, M in items:
        img = cv2.imread(str(p), cv2.IMREAD_REDUCED_GRAYSCALE_8)
        h, w = img.shape[0] * 8, img.shape[1] * 8
        corners = np.float64([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
        pts.append(cv2.perspectiveTransform(corners, M))
    pts = np.concatenate(pts).reshape(-1, 2)
    return math.ceil(pts[:, 0].max()), math.ceil(pts[:, 1].max())


def render_canvas(items, cw, ch, canvas_path: Path):
    canvas = np.memmap(canvas_path, dtype=np.uint8, mode="w+",
                       shape=(ch, cw, 3))
    for p, M in items:
        img = cv2.imread(str(p))
        h, w = img.shape[:2]
        alpha_src = (feather(w, h)
                     * (cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) > BLACK))
        corners = cv2.perspectiveTransform(
            np.float64([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2), M
        ).reshape(-1, 2)
        bx0 = max(0, math.floor(corners[:, 0].min()))
        bx1 = min(cw, math.ceil(corners[:, 0].max()))
        by0 = max(0, math.floor(corners[:, 1].min()))
        by1 = min(ch, math.ceil(corners[:, 1].max()))
        if bx1 <= bx0 or by1 <= by0:
            continue
        bw = bx1 - bx0
        for y in range(by0, by1, STRIP):
            sh = min(STRIP, by1 - y)
            S = np.array([[1, 0, -bx0], [0, 1, -y], [0, 0, 1.0]]) @ M
            warped = cv2.warpPerspective(img, S, (bw, sh))
            a = cv2.warpPerspective(alpha_src, S, (bw, sh))
            hit = a > 1e-3
            if not hit.any():
                continue
            dst = canvas[y:y + sh, bx0:bx1]
            empty = cv2.cvtColor(dst, cv2.COLOR_BGR2GRAY) <= BLACK
            a = np.where(hit & empty, 1.0, a)[..., None].astype(np.float32)
            dst[hit] = (dst[hit] * (1 - a[hit])
                        + warped[hit] * a[hit]).astype(np.uint8)
        print(f"  уложено {p.parent.name}/{p.name}")
    canvas.flush()
    return canvas


def write_tiles(level_dir: Path, arr, w, h, quality):
    level_dir.mkdir(parents=True, exist_ok=True)
    for ty in range(math.ceil(h / TILE)):
        band = np.asarray(arr[ty * TILE:min((ty + 1) * TILE, h)])
        for tx in range(math.ceil(w / TILE)):
            tile = band[:, tx * TILE:min((tx + 1) * TILE, w)]
            if tile.max() <= BLACK:      # пустой тайл не пишем, фон и так тёмный
                continue
            cv2.imwrite(str(level_dir / f"{tx}_{ty}.jpg"), tile,
                        [cv2.IMWRITE_JPEG_QUALITY, quality])


def halve(arr, w, h, out_path: Path):
    """Уменьшение вдвое полосами (memmap → memmap); ceil — по спецификации DZI."""
    nw, nh = max(1, math.ceil(w / 2)), max(1, math.ceil(h / 2))
    small = np.memmap(out_path, dtype=np.uint8, mode="w+", shape=(nh, nw, 3))
    for y in range(0, nh, STRIP):
        sh = min(STRIP, nh - y)
        src = np.asarray(arr[y * 2:(y + sh) * 2])
        small[y:y + sh] = cv2.resize(src, (nw, sh),
                                     interpolation=cv2.INTER_AREA)
    small.flush()
    return small, nw, nh


def marks_on_canvas(items, marks_path: Path):
    by_name = {str(p): M for p, M in items}
    out = []
    with marks_path.open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(
            (ln for ln in f if not ln.startswith("#")), delimiter="\t")]
    for r in rows:
        M = by_name.get(r.get("mosaic") or "")
        if M is None:
            continue
        p = cv2.perspectiveTransform(
            np.float64([[float(r["mx"]), float(r["my"])]]).reshape(-1, 1, 2),
            M).ravel()
        label = ("≈ " if r.get("approx") == "1" else "") + r["label"]
        out.append(dict(x=float(p[0]), y=float(p[1]), label=label))
    return out


def read_extra_marks(path: Path):
    """Готовые канвас-координаты гео-отметок (label, x, y) — из pano_compose.py."""
    if path is None or not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [dict(x=float(r["x"]), y=float(r["y"]), label=r["label"])
                for r in csv.DictReader(f, delimiter="\t")]


def build(merged: Path, out: Path, marks_path: Path, quality: int, title: str,
          extra_marks: Path = None):
    items = read_items(merged)
    cw, ch = canvas_size(items)
    levels = math.ceil(math.log2(max(cw, ch)))
    print(f"холст {cw}x{ch}, уровней {levels + 1}, полотен {len(items)}")
    out.mkdir(parents=True, exist_ok=True)
    files = out / "pano_files"

    canvas_path = out / "canvas.dat"
    arr = render_canvas(items, cw, ch, canvas_path)
    w, h = cw, ch
    tmp_paths = [canvas_path]
    for level in range(levels, -1, -1):
        write_tiles(files / str(level), arr, w, h, quality)
        print(f"  уровень {level}: {w}x{h}")
        if level == 0:
            break
        tmp = out / f"canvas_{level - 1}.dat"
        arr, w, h = halve(arr, w, h, tmp)
        tmp_paths.append(tmp)

    (out / "pano.dzi").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Image xmlns="http://schemas.microsoft.com/deepzoom/2008" '
        f'Format="jpg" Overlap="0" TileSize="{TILE}">'
        f'<Size Width="{cw}" Height="{ch}"/></Image>\n', encoding="utf-8")
    marks = marks_on_canvas(items, marks_path) + read_extra_marks(extra_marks)
    (out / "index.html").write_text(
        HTML.format(title=title, marks_json=json.dumps(marks,
                                                       ensure_ascii=False)),
        encoding="utf-8")
    del arr
    for t in tmp_paths:
        t.unlink(missing_ok=True)
    n_tiles = sum(1 for _ in files.rglob("*.jpg"))
    print(f"готово: {out / 'index.html'} (тайлов {n_tiles}, отметок {len(marks)})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--merged", type=Path, required=True,
                    help="папка merge_mosaics.py с mosaics.tsv")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--marks", type=Path,
                    default=Path(__file__).parent / "findings-marks.tsv")
    ap.add_argument("--quality", type=int, default=85)
    ap.add_argument("--title", default="Гигапанорама")
    ap.add_argument("--extra-marks", type=Path,
                    help="tsv label/x/y — гео-отметки от pano_compose.py")
    args = ap.parse_args()
    build(args.merged, args.out, args.marks, args.quality, args.title,
          args.extra_marks)
