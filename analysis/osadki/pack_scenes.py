#!/usr/bin/env python3
"""Склейка панелей сцены в один спрайт — чтобы не упереться в лимит Cloudflare Pages (20 000 файлов).

Для каждой папки scenes/{s2,s1,viirs}/<id>/ с meta.json делает sprite.jpg (панели в сетке по 3 в ряд)
и пишет в meta.json раздел "sprite": {"file", "w", "h", "panels": {name: [x, y, w, h]}}. Страницы
ссылаются только на спрайт (1 файл вместо 4–6), панель вырезается CSS-ом. Отдельные PNG остаются
локально (их читает ИИ-агент). Затем пересобирает data/{s2,s1,viirs}_index.json из meta.json.
Идемпотентно: спрайт не пересобирается, если новее всех панелей. Запуск: python3 analysis/osadki/pack_scenes.py
"""
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SCENES = ROOT / "analysis/osadki/scenes"
DATA = ROOT / "analysis/osadki/data"
ORDER = {"s2": ["zone_rgb", "zone_mask", "zone_swir", "mtn_rgb", "mtn_mask", "mtn_swir"],
         "s1": ["zone_vv", "zone_diff", "mtn_vv", "mtn_diff"],
         "viirs": ["near_fc", "near_tc", "wide_fc", "wide_tc"]}


def pack(d, kind, force=False):
    mp = d / "meta.json"
    if not mp.exists():
        return None
    meta = json.load(open(mp))
    names = [n for n in ORDER[kind] if n in meta.get("panels", {})]
    if not names:
        return meta
    files = [d / meta["panels"][n] for n in names]
    sp = d / "sprite.jpg"
    if sp.exists() and not force and all(sp.stat().st_mtime >= f.stat().st_mtime for f in files if f.exists()) and meta.get("sprite", {}).get("panels", {}).keys() == set(names):
        return meta
    ims = [(n, Image.open(f).convert("RGB")) for n, f in zip(names, files) if f.exists()]
    cols = 3
    rows = [ims[i:i + cols] for i in range(0, len(ims), cols)]
    W = max(sum(im.width for _, im in r) for r in rows)
    H = sum(max(im.height for _, im in r) for r in rows)
    sprite = Image.new("RGB", (W, H), (16, 18, 22))
    boxes = {}
    y = 0
    for r in rows:
        x = 0; rh = max(im.height for _, im in r)
        for n, im in r:
            sprite.paste(im, (x, y)); boxes[n] = [x, y, im.width, im.height]; x += im.width
        y += rh
    sprite.save(sp, quality=86, optimize=True, progressive=True)
    meta["sprite"] = {"file": "sprite.jpg", "w": W, "h": H, "panels": boxes}
    json.dump(meta, open(mp, "w"), ensure_ascii=False, indent=1)
    return meta


def main():
    for kind, index_name, sort_key in (("s2", "s2_index.json", lambda m: (m["date"], m["time_utc"])),
                                       ("s1", "s1_index.json", lambda m: (m["date"], m["time_utc"])),
                                       ("viirs", "viirs_index.json", lambda m: m["date"])):
        metas = []
        for d in sorted((SCENES / kind).glob("*/")):
            if d.name.startswith("_"):
                continue
            m = pack(d, kind)
            if m:
                metas.append(m)
        metas.sort(key=sort_key)
        json.dump(metas, open(DATA / index_name, "w"), ensure_ascii=False, indent=1)
        n_sp = sum(1 for m in metas if m.get("sprite"))
        print(kind, len(metas), "сцен,", n_sp, "спрайтов")


if __name__ == "__main__":
    main()
