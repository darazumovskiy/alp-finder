#!/usr/bin/env python3
"""Сборка публичной версии просмотрщика в analysis/viewer/dist/.

Копирует index.html, map.html, montages.html, panoramy.html и все картинки,
на которые они ссылаются (пути от корня репозитория: analysis/..., docs/...,
data/...), в dist/ с сохранением структуры путей; папки тайлов гигапанорам
(PANOS) копируются целиком в dist/pano/. Также кладёт в корень dist архивы
srt-*.zip из корня репозитория — они опубликованы как ссылки для волонтёров
(docs/drive-inventory.md), без этого пересборка молча снимала бы их с сайта. Абсолютные пути картинок (/analysis/...)
при копировании переписываются на относительные: страницы в dist лежат в корне
сайта, поэтому относительные пути работают и в корне домена (Cloudflare Pages),
и в подпапке (зеркало GitHub Pages).

Запуск: python analysis/viewer/build_dist.py
Деплой: npx wrangler pages deploy analysis/viewer/dist --project-name alp-finder
Зеркало для РФ/РБ: bash scripts/deploy_mirror.sh
"""

import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VIEWER_DIR = Path(__file__).resolve().parent
DIST = VIEWER_DIR / "dist"

PAGES = ["index.html", "map.html", "montages.html", "panoramy.html",
         "coverage-3d.html", "polyot-3d.html", "otchet-2026-08-16.html",
         "otchet-2026-08-16-part2.html",
         "otchet-2026-08-17-koshki-pokrytie.html",
         "otchet-tg-aktivnost.html"]

# Самодостаточные HTML вне viewer/ (ссылки из навигации) → имя в корне dist
EXTRA_FILES = {
    "docs/nezavisimyy-analiz/model-3d/model-3d.html": "model-3d.html",
    "docs/nezavisimyy-analiz/model-3d/model-3d-full.html": "model-3d-full.html",
}

# Гигапанорамы (tile_pano.py): папка тайлов → путь в dist.
# Ссылки на них — в panoramy.html; ASSET_RE их не ловит, копируем целиком.
PANOS = {
    "analysis/stitch/pano-veshchi-full/pano": "pano/veshchi",
    "analysis/stitch/merged-ryukzak-14/pano": "pano/ryukzak-14",
    "analysis/stitch/merged-zona/pano": "pano/zona",
    "analysis/stitch/pano-183932/pano": "pano/183932",
}

# Ловим и литеральные src="/analysis/...", и пути в JS-данных ("analysis/.../x.jpg")
ASSET_RE = re.compile(
    r'(?:analysis|docs|data)/[A-Za-z0-9_./\-]+\.(?:jpe?g|png|JPG|PNG)'
)


def main() -> int:
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    # без него GitHub Pages (Jekyll) выкидывает каталоги с подчёркиванием (_calib)
    (DIST / ".nojekyll").write_text("")

    assets: set[str] = set()
    for page in PAGES:
        src = VIEWER_DIR / page
        if not src.exists():
            print(f"нет страницы {src}, сначала запусти build_viewer.py/build_map.py",
                  file=sys.stderr)
            return 1
        text = src.read_text()
        # локальные страницы смотрят от корня репо (src="/analysis/...");
        # в dist страницы в корне сайта — те же пути делаем относительными
        (DIST / page).write_text(
            text.replace('src="/', 'src="').replace('data-full="/', 'data-full="'),
            "utf-8",
        )
        assets.update(ASSET_RE.findall(text))

    n_extra = 0
    for src_rel, dst_name in EXTRA_FILES.items():
        src = REPO_ROOT / src_rel
        if not src.is_file():
            print(f"нет файла {src_rel} — пропущен (навигация будет с битой "
                  f"ссылкой)", file=sys.stderr)
            continue
        shutil.copy2(src, DIST / dst_name)
        n_extra += 1

    n_zips = 0
    for z in sorted(REPO_ROOT.glob("srt-*.zip")):
        shutil.copy2(z, DIST / z.name)
        n_zips += 1

    # кеш плеера полётов (analysis/flight_cache.py): map.html грузит его fetch'ем
    # по относительному пути flights/..., поэтому в dist он лежит рядом с картой
    n_flights = 0
    flights = VIEWER_DIR / "flights"
    if flights.is_dir():
        shutil.copytree(flights, DIST / "flights")
        n_flights = len(list((DIST / "flights").iterdir()))

    # ортомозаика (analysis/build_ortho.py): polyot-3d.html и map.html грузят
    # тайлы fetch'ем по относительному пути ortho/..., кладём рядом
    n_ortho = 0
    ortho = VIEWER_DIR / "ortho"
    if ortho.is_dir():
        shutil.copytree(ortho, DIST / "ortho")
        n_ortho = len(list((DIST / "ortho").rglob("*.webp")))

    # запечённый фон из кадров (analysis/viewer/build_eye_bake.py)
    ebake = VIEWER_DIR / "eyebake"
    if ebake.is_dir():
        shutil.copytree(ebake, DIST / "eyebake")

    n_panos = 0
    for src_rel, dst_rel in PANOS.items():
        src = REPO_ROOT / src_rel
        if not src.is_dir():
            print(f"нет панорамы {src_rel} — пропущена (страница будет с битой "
                  f"ссылкой)", file=sys.stderr)
            continue
        shutil.copytree(src, DIST / dst_rel)
        n_panos += 1

    copied = 0
    missing: list[str] = []
    for rel in sorted(assets):
        src = REPO_ROOT / rel
        if not src.is_file():
            missing.append(rel)
            continue
        dst = DIST / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1

    total_mb = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file()) / 2**20
    print(f"dist/: {copied} картинок + {len(PAGES)} страниц + {n_extra} 3D-моделей "
          f"+ {n_zips} архивов + {n_panos} панорам + {n_flights} полётов "
          f"+ {n_ortho} орто-тайлов, {total_mb:.0f} МБ")
    if missing:
        print(f"не найдено {len(missing)} файлов (страницы будут с битыми превью):",
              file=sys.stderr)
        for rel in missing:
            print(f"  {rel}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
