#!/usr/bin/env python3
"""Пилот «честной 3D»: гладкий мэш рельефа + ортомозаика из кадров съёмки.

Отличие от «Полёт 3D» (воксели 8 м, форма-кубики): рельеф здесь — треугольная
сетка HMA 8 м + DSM-патчи, а картинка — готовые тайлы analysis/build_ortho.py,
натянутые текстурой. Разрешение картинки не зависит от шага сетки: на грубом
треугольнике лежит сантиметровая текстура.

Зоны (уровни детальности, вложенные): весь район → панорама коридора спуска →
пятно вещей → хот-спот рюкзак/вещи. Каждая зона — слой с чекбоксом; текстуры
грузятся fetch'ем, где орто нет — серая подложка-отмывка рельефа.

Вход: analysis/ortho-work/tiles/, DEM, POINTS/CAMPS из build_map.
Выход: analysis/viewer/ortho3d.html + analysis/viewer/ortho3d/*.jpg.

Запуск: analysis/.venv/bin/python analysis/viewer/build_ortho3d.py
Просмотр: python3 -m http.server 8077 -d . → /analysis/viewer/ortho3d.html
"""

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
from build_map import CAMPS, POINTS  # noqa: E402

TILES_DIR = ROOT / "analysis/ortho-work/tiles"
OUT_HTML = HERE / "ortho3d.html"
OUT_TEX = HERE / "ortho3d"

TILE = 512
MERC_R = 6378137.0
MAX_TEX = 3900          # потолок стороны текстуры-куска (лимит GPU 4096)
M_LAT = 111132.0

# имя, lat0(Ю), lat1(С), lon0(З), lon1(В), gz тайлов, шаг мэша (м), уровень
ZONES = [
    ("Весь район",        39.4350, 39.5250, 73.5500, 73.6500, 15, 20.0, 0),
    ("Панорама спуска",   39.4730, 39.4870, 73.5790, 73.5990, 18, 4.0, 1),
    ("Пятно вещей",       39.4805, 39.4855, 73.5825, 73.5900, 19, 2.0, 2),
    ("Рюкзак и вещи",     39.4822, 39.4836, 73.5842, 73.5874, 21, 1.0, 3),
]

VIEWS = [
    ("Общий вид", 39.4855, 73.5920, 4500),
    ("Панорама спуска", 39.4796, 73.5895, 1300),
    ("Пятно вещей", 39.4831, 73.5856, 500),
    ("Рюкзак", 39.482656, 73.586792, 120),
]


def merc_px(lat, lon, gz):
    s = TILE * (1 << gz)
    x = (np.asarray(lon) + 180.0) / 360.0 * s
    y = (1.0 - np.arcsinh(np.tan(np.radians(np.asarray(lat)))) / math.pi) / 2.0 * s
    return x, y


def elev_grid(dem, lats, lons):
    """Высоты (с DSM-патчами) на сетке lats×lons; NaN → ближайшая конечная."""
    glon, glat = np.meshgrid(lons, lats)
    h = _bilinear(dem.z, (glon - dem.lon0) / dem.dlon, (dem.lat0 - glat) / dem.dlat)
    if dem._patch is not None:
        off, plon0, plat0, pdlon, pdlat = dem._patch
        v = _bilinear(off, (glon - plon0) / pdlon, (plat0 - glat) / pdlat)
        h = np.where(np.isfinite(v), h + v, h)
    if np.isnan(h).any():
        h = np.where(np.isfinite(h), h, np.nanmin(h))
    return h


def hillshade(h, step_m):
    """Отмывка рельефа [0..1] — подложка там, где орто-съёмки нет."""
    dzdy, dzdx = np.gradient(h, step_m)          # ось 0 — юг→север
    nx, ny, nz = -dzdx, -dzdy, np.ones_like(h)
    nn = np.sqrt(nx * nx + ny * ny + nz * nz)
    sun = np.array([-0.35, 0.25, 0.9])
    sun /= np.linalg.norm(sun)
    s = (nx * sun[0] + ny * sun[1] + nz * sun[2]) / nn
    return np.clip(s, 0.15, 1.0)


def merc_region(gz, x0, y0, x1, y1):
    """Регион мозаики [x0..x1)×[y0..y1) в глобальных пикселях сетки gz → BGRA."""
    img = np.zeros((y1 - y0, x1 - x0, 4), np.uint8)
    for tx in range(x0 // TILE, (x1 - 1) // TILE + 1):
        for ty in range(y0 // TILE, (y1 - 1) // TILE + 1):
            p = TILES_DIR / str(gz) / str(tx) / f"{ty}.webp"
            if not p.exists():
                continue
            t = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
            if t is None:
                continue
            if t.shape[2] == 3:
                t = np.dstack([t, np.full(t.shape[:2], 255, np.uint8)])
            ox, oy = tx * TILE - x0, ty * TILE - y0
            sx0, sy0 = max(0, -ox), max(0, -oy)
            sx1 = min(TILE, img.shape[1] - ox)
            sy1 = min(TILE, img.shape[0] - oy)
            if sx0 >= sx1 or sy0 >= sy1:
                continue
            img[oy + sy0:oy + sy1, ox + sx0:ox + sx1] = t[sy0:sy1, sx0:sx1]
    return img


def build_zone(dem, name, lat0, lat1, lon0, lon1, gz, step_m, level, slug):
    m_lon = 111320.0 * math.cos(math.radians((lat0 + lat1) / 2))
    ni = int(round((lat1 - lat0) * M_LAT / step_m)) + 1
    nj = int(round((lon1 - lon0) * m_lon / step_m)) + 1
    lats = np.linspace(lat0, lat1, ni)           # ряды с юга на север
    lons = np.linspace(lon0, lon1, nj)
    h = elev_grid(dem, lats, lons)
    zmin = float(np.floor(h.min()))
    shade = hillshade(h, step_m)

    # куски: сетка узлов делится так, чтобы текстура куска влезла в MAX_TEX
    xw, yn = merc_px(lat1, lon0, gz)             # северо-запад
    xe, ys = merc_px(lat0, lon1, gz)
    w_px, h_px = float(xe - xw), float(ys - yn)
    ncx = max(1, math.ceil(w_px / MAX_TEX))
    ncy = max(1, math.ceil(h_px / MAX_TEX))
    jb = np.round(np.linspace(0, nj - 1, ncx + 1)).astype(int)
    ib = np.round(np.linspace(0, ni - 1, ncy + 1)).astype(int)

    chunks = []
    filled_w = 0.0
    for cy in range(ncy):
        for cx in range(ncx):
            j0, j1 = int(jb[cx]), int(jb[cx + 1])
            i0, i1 = int(ib[cy]), int(ib[cy + 1])
            # текстурная рамка куска — мерк-координаты его узловых границ;
            # ряд i растёт на север, пиксель мозаики — на юг
            x0, y0 = merc_px(lats[i1], lons[j0], gz)
            x1, y1 = merc_px(lats[i0], lons[j1], gz)
            x0, y0, x1, y1 = int(x0), int(y0), int(math.ceil(x1)), int(math.ceil(y1))
            img = merc_region(gz, x0, y0, x1, y1)
            alpha = img[:, :, 3] > 0
            filled_w += alpha.mean() * (i1 - i0) * (j1 - j0)

            sub = shade[i0:i1 + 1, j0:j1 + 1]
            base = cv2.resize((sub * 255).astype(np.uint8),
                              (img.shape[1], img.shape[0]),
                              interpolation=cv2.INTER_LINEAR)
            # серо-бежевая отмывка: заметно отличается от живых кадров
            fall = np.dstack([(base * 0.62).astype(np.uint8),
                              (base * 0.66).astype(np.uint8),
                              (base * 0.70).astype(np.uint8)])
            bgr = np.where(alpha[..., None], img[:, :, :3], fall)
            fn = f"{slug}_{cy}_{cx}.jpg"
            cv2.imwrite(str(OUT_TEX / fn), bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
            chunks.append(dict(i0=i0, i1=i1, j0=j0, j1=j1, img=f"ortho3d/{fn}"))

    fill = filled_w / ((ni - 1) * (nj - 1))
    res_cm = 2 * math.pi * MERC_R * math.cos(math.radians((lat0 + lat1) / 2)) \
        / (TILE * (1 << gz)) * 100
    print(f"{name}: мэш {ni}x{nj} (шаг {step_m:g} м), текстура {res_cm:.0f} см/пикс, "
          f"кусков {len(chunks)}, орто заполнено {fill:.0%}")
    return dict(name=name, lat0=lat0, lon0=lon0,
                dlat=(lat1 - lat0) / (ni - 1), dlon=(lon1 - lon0) / (nj - 1),
                ni=ni, nj=nj, zmin=zmin,
                h=np.rint((h - zmin) * 10).astype(int).ravel().tolist(),
                chunks=chunks, level=level, res_cm=round(res_cm, 1))


def build():
    if not HMA_PATH.exists():
        sys.exit(f"нет {HMA_PATH.name} — соберите: analysis/build_hma_dem.py")
    if not TILES_DIR.is_dir():
        sys.exit(f"нет {TILES_DIR} — сначала analysis/build_ortho.py")
    OUT_TEX.mkdir(exist_ok=True)
    dem = Dem(HMA_PATH)

    slugs = ["rayon", "panorama", "veshchi", "hotspot"]
    zones = [build_zone(dem, *z, slug=s) for z, s in zip(ZONES, slugs)]

    marks = []
    for name, lat, lon, _alt in CAMPS:
        try:
            marks.append(dict(kind="camp", name=name, lat=lat, lon=lon,
                              alt=round(dem.elev(lat, lon), 1)))
        except ValueError:
            continue
    for p in POINTS:
        if p.get("status") != "confirmed":
            continue
        marks.append(dict(kind="find", name=p["name"], lat=p["lat"], lon=p["lon"],
                          alt=round(dem.elev(p["lat"], p["lon"]), 1)))

    lat_c = (ZONES[0][1] + ZONES[0][2]) / 2
    data = dict(
        latC=lat_c, lonC=(ZONES[0][3] + ZONES[0][4]) / 2,
        elevC=min(z["zmin"] for z in zones),
        mlat=M_LAT, mlon=111320.0 * math.cos(math.radians(lat_c)),
        zones=zones, marks=marks,
        views=[dict(name=n, lat=la, lon=lo, dist=d) for n, la, lo, d in VIEWS],
    )

    three = (HERE / "vendor/three.min.js").read_text()
    orbit = (HERE / "vendor/OrbitControls.js").read_text()
    html = TEMPLATE.replace("__THREE__", three + "\n" + orbit)
    html = html.replace("__DATA__", json.dumps(data, separators=(",", ":")))
    OUT_HTML.write_text(html, "utf-8")
    tex_mb = sum(f.stat().st_size for f in OUT_TEX.iterdir()) / 2**20
    print(f"{OUT_HTML.name}: {OUT_HTML.stat().st_size / 2**20:.1f} МБ, "
          f"текстур {tex_mb:.0f} МБ, зон {len(zones)}, отметок {len(marks)}")


TEMPLATE = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>Курумды — рельеф с орто-текстурой (пилот)</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { --bg:#0f1113; --card:#191c20cc; --line:#2a2e34; --fg:#d7dbe0; --dim:#8b929b; }
  html, body { margin:0; background:var(--bg); color:var(--fg);
               font:14px/1.45 system-ui, sans-serif; height:100%; }
  header { position:relative; z-index:3; background:#14161acc;
           border-bottom:1px solid var(--line); padding:8px 16px; }
  header nav { display:flex; gap:16px; flex-wrap:wrap; align-items:baseline; }
  .title { font-weight:700; }
  a { color:#7ab8ff; text-decoration:none; }
  #scene { position:fixed; inset:0; }
  #panel { position:fixed; top:56px; left:12px; z-index:2; width:280px;
           background:var(--card); border:1px solid var(--line); border-radius:10px;
           padding:12px 14px; backdrop-filter: blur(4px); }
  #panel h1 { font-size:15px; margin:0 0 8px; }
  #panel label { display:flex; gap:7px; align-items:center; margin:4px 0; cursor:pointer; }
  #panel .res { color:var(--dim); font-size:12px; margin-left:auto; }
  #panel .views { display:flex; gap:6px; flex-wrap:wrap; margin-top:8px; }
  #panel .views button { background:#22262c; color:var(--fg); border:1px solid var(--line);
                         border-radius:6px; padding:4px 8px; cursor:pointer; font-size:12px; }
  #panel .views button:hover { background:#2c313a; }
  #panel .note { color:var(--dim); font-size:12px; margin-top:8px;
                 border-top:1px solid var(--line); padding-top:8px; }
  #hint { position:fixed; right:12px; bottom:10px; z-index:2; color:var(--dim); font-size:12px; }
</style></head><body>
<header><nav><span class="title">Курумды — видеоанализ</span>
<a href="coverage-3d.html">3D-покрытие</a>
<a href="polyot-3d.html">Полёт 3D</a>
<a href="osadki-monitoring.html">Снег и погода</a>
<a href="otchety.html">Отчёты</a>
<a href="ortho3d.html"><b>Рельеф+орто (пилот)</b></a>
</nav></header>
<div id="scene"></div>
<div id="panel">
  <h1>Рельеф с орто-текстурой</h1>
  <label><input type="checkbox" id="ortho" checked> орто-текстура (кадры съёмки)</label>
  <div id="zonelist"></div>
  <label><input type="checkbox" id="labels" checked> подписи (лагеря, вещи)</label>
  <div class="views" id="views"></div>
  <div class="note">Пилот: треугольная сетка рельефа (HMA 8 м + DSM-патчи) с
  текстурой из ортомозаики build_ortho.py. Серая отмывка — там съёмки нет.
  Детальные зоны лежат поверх обзорных. Открывать через http
  (python3 -m http.server 8077 из корня) — с file:// браузер не отдаёт
  текстуры в WebGL и рельеф остаётся серым.</div>
</div>
<div id="hint">вращение — мышь · зум — колесо · сдвиг — правая кнопка</div>
<script>__THREE__</script>
<script>
const D = __DATA__;

function toXYZ(lat, lon, alt) {
  return [(lon - D.lonC) * D.mlon, alt - D.elevC, -(lat - D.latC) * D.mlat];
}

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0f1113);
scene.fog = new THREE.Fog(0x0f1113, 12000, 26000);
const camera = new THREE.PerspectiveCamera(55, innerWidth/innerHeight, 2, 40000);
const renderer = new THREE.WebGLRenderer({antialias:true});
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
document.getElementById('scene').appendChild(renderer.domElement);
const controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.enableDamping = true; controls.maxPolarAngle = Math.PI * 0.495;

scene.add(new THREE.HemisphereLight(0xdfe6f0, 0x4a453e, .95));
const sun = new THREE.DirectionalLight(0xffffff, .55);
sun.position.set(-2000, 3000, -1500);
scene.add(sun);

// --- зоны: мэш кусками, у каждого своя текстура --------------------------------
const loader = new THREE.TextureLoader();
const plainMat = new THREE.MeshLambertMaterial({color: 0x8a8378});
const zoneGroups = [];
for (const Z of D.zones) {
  const g = new THREE.Group();
  const zPos = (i, j) => {
    const lat = Z.lat0 + i * Z.dlat, lon = Z.lon0 + j * Z.dlon;
    return toXYZ(lat, lon, Z.zmin + Z.h[i * Z.nj + j] / 10);
  };
  for (const c of Z.chunks) {
    const ni = c.i1 - c.i0 + 1, nj = c.j1 - c.j0 + 1;
    const pos = new Float32Array(ni * nj * 3);
    const uv = new Float32Array(ni * nj * 2);
    for (let i = 0; i < ni; i++)
      for (let j = 0; j < nj; j++) {
        const k = i * nj + j;
        const [x, y, z] = zPos(c.i0 + i, c.j0 + j);
        pos[k*3] = x; pos[k*3+1] = y; pos[k*3+2] = z;
        uv[k*2] = j / (nj - 1);
        uv[k*2+1] = i / (ni - 1);   // ряд 0 — юг → низ текстуры
      }
    const idx = [];
    for (let i = 0; i < ni - 1; i++)
      for (let j = 0; j < nj - 1; j++) {
        const a = i*nj+j, b = a+1, d = a+nj, e = d+1;
        idx.push(a, b, d, b, e, d);
      }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geo.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
    geo.setIndex(idx);
    geo.computeVertexNormals();
    // до загрузки текстуры (и при её отказе, например с file://) — серый
    // рельеф, а не чёрный: карта подключается только по факту загрузки
    const mat = new THREE.MeshLambertMaterial({color: 0x8a8378});
    const tex = loader.load(c.img, () => {
      mat.map = tex; mat.color.set(0xffffff); mat.needsUpdate = true;
    });
    tex.flipY = true;
    tex.anisotropy = renderer.capabilities.getMaxAnisotropy();
    // детальная зона поверх обзорной: сдвиг глубины против z-борьбы
    mat.polygonOffset = true;
    mat.polygonOffsetFactor = -2 * Z.level;
    mat.polygonOffsetUnits = -2 * Z.level;
    plainMat.polygonOffset = true;
    const mesh = new THREE.Mesh(geo, mat);
    mesh.renderOrder = Z.level;
    mesh.userData.texMat = mat;
    g.add(mesh);
  }
  scene.add(g);
  zoneGroups.push(g);
}

// --- отметки --------------------------------------------------------------------
const labGroup = new THREE.Group();
scene.add(labGroup);
function label(text, xyz, color, size) {
  const c = document.createElement('canvas');
  const ctx = c.getContext('2d');
  ctx.font = '26px system-ui';
  c.width = Math.ceil(ctx.measureText(text).width) + 16; c.height = 38;
  const ctx2 = c.getContext('2d');
  ctx2.font = '26px system-ui';
  ctx2.fillStyle = '#0f1113cc'; ctx2.fillRect(0, 0, c.width, c.height);
  ctx2.fillStyle = color; ctx2.fillText(text, 8, 28);
  const sp = new THREE.Sprite(new THREE.SpriteMaterial(
    {map: new THREE.CanvasTexture(c), depthTest: false, sizeAttenuation: false}));
  sp.position.set(xyz[0], xyz[1] + 12, xyz[2]);
  sp.scale.set(c.width / 38 * size, size, 1);
  sp.center.set(0.5, 0);
  labGroup.add(sp);
}
for (const m of D.marks) {
  const xyz = toXYZ(m.lat, m.lon, m.alt + 1.5);
  const isCamp = m.kind === 'camp';
  // находка — компактный маркер: не должен закрывать место при подлёте
  const dot = new THREE.Mesh(
    new THREE.SphereGeometry(isCamp ? 7 : 0.6, 12, 12),
    new THREE.MeshBasicMaterial({color: isCamp ? 0xc084fc : 0xff5f5f}));
  dot.position.set(...xyz);
  labGroup.add(dot);
  const nm = m.name.length > 28 ? m.name.slice(0, 27) + '…' : m.name;
  label(nm, xyz, isCamp ? '#d9c4ff' : '#ffb3b3', isCamp ? .05 : .038);
}

// --- панель ---------------------------------------------------------------------
const zl = document.getElementById('zonelist');
D.zones.forEach((Z, k) => {
  const lab = document.createElement('label');
  lab.innerHTML = `<input type="checkbox" checked> ${Z.name}` +
    `<span class="res">${Z.res_cm < 100 ? Z.res_cm + ' см/пикс'
                                        : (Z.res_cm/100).toFixed(1) + ' м/пикс'}</span>`;
  lab.querySelector('input').onchange = e => zoneGroups[k].visible = e.target.checked;
  zl.appendChild(lab);
});
document.getElementById('ortho').onchange = e => {
  for (const g of zoneGroups)
    g.traverse(o => { if (o.isMesh) o.material = e.target.checked ? o.userData.texMat : plainMat; });
};
document.getElementById('labels').onchange = e => labGroup.visible = e.target.checked;

const Z0 = D.zones[0];
function groundAt(lat, lon) {
  const i = Math.min(Z0.ni - 1, Math.max(0, Math.round((lat - Z0.lat0) / Z0.dlat)));
  const j = Math.min(Z0.nj - 1, Math.max(0, Math.round((lon - Z0.lon0) / Z0.dlon)));
  return Z0.zmin + Z0.h[i * Z0.nj + j] / 10;
}
function flyTo(v) {
  const t = new THREE.Vector3(...toXYZ(v.lat, v.lon, groundAt(v.lat, v.lon)));
  controls.target.copy(t);
  camera.position.set(t.x + v.dist * .35, t.y + v.dist * .8, t.z + v.dist * .6);
}
const viewsDiv = document.getElementById('views');
for (const v of D.views) {
  const b = document.createElement('button');
  b.textContent = v.name;
  b.onclick = () => flyTo(v);
  viewsDiv.appendChild(b);
}

flyTo(D.views[0]);
addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});
(function loop(){ requestAnimationFrame(loop); controls.update();
                  renderer.render(scene, camera); })();
</script></body></html>
"""


if __name__ == "__main__":
    build()
