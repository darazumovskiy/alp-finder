#!/usr/bin/env python3
"""3D-вид покрытия: рельеф района, раскрашенный сеткой осмотра дрона.

Ответ на вопрос «какие склоны реально осмотрены»: та же сетка покрытия, что
на 2D-карте (analysis/coverage_map.py, 3 уровня по порогу 8 px), но натянутая
на рельеф — в 3D видны мёртвые зоны за перегибами, которые плоская карта
скрывает. Рельеф — NASA HMA 8 м (analysis/build_hma_dem.py, стереопары
WorldView ~2017) с фотограмметрическими патчами 2026 г. поверх
(docs/dem-patches.md); расчётный конвейер координат остаётся на GLO-30.

Вход: analysis/coverage/coverage-map-cells.json, DEM, маршрут (GPX), коридор
(KML), подтверждённые вещи из POINTS build_map.py.
Выход: analysis/viewer/coverage-3d.html — самодостаточный (three.js встроен).

Запуск: analysis/.venv/bin/python analysis/viewer/build_coverage3d.py
"""

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402

from geoproject import Dem, HMA_PATH, _bilinear  # noqa: E402
from build_map import (  # noqa: E402
    CAMPS, LAT0, LAT1, LON0, LON1, M_PER_DEG_LAT, POINTS, m_per_deg_lon,
    parse_gpx, parse_kml_lines,
)

COVER_JSON = ROOT / "analysis/coverage/coverage-map-cells.json"
OUT = HERE / "coverage-3d.html"

# Пресеты камеры: имя, широта/долгота цели, дистанция камеры (м)
VIEWS = [
    ("Общий вид", 39.4855, 73.5920, 4200),
    ("Пятно вещей", 39.4831, 73.5856, 700),
    ("Зона интереса", 39.4772, 73.5921, 900),
    ("Слепая полоса", 39.4805, 73.5900, 1400),
]


def terrain_grid(dem, cover):
    """Высоты рельефа в центрах ячеек сетки покрытия: H[ni, nj], float."""
    lat0, lon0 = cover["lat0"], cover["lon0"]
    dlat, dlon = cover["dlat"], cover["dlon"]
    ni = int(round((LAT1 - lat0) / dlat))
    nj = int(round((LON1 - lon0) / dlon))
    lats = lat0 + (np.arange(ni) + 0.5) * dlat
    lons = lon0 + (np.arange(nj) + 0.5) * dlon
    glon, glat = np.meshgrid(lons, lats)
    h = _bilinear(dem.z, (glon - dem.lon0) / dem.dlon,
                  (dem.lat0 - glat) / dem.dlat)
    if dem._patch is not None:
        off, plon0, plat0, pdlon, pdlat = dem._patch
        v = _bilinear(off, (glon - plon0) / pdlon, (plat0 - glat) / pdlat)
        h = np.where(np.isfinite(v), h + v, h)
    return h, ni, nj


def line3d(dem, pts, lift=4.0, every=1):
    """[(lat, lon, высота+lift), ...] — полилиния, посаженная на рельеф."""
    out = []
    for lat, lon in pts[::every]:
        try:
            z = dem.elev(lat, lon) + lift
        except ValueError:
            continue
        out.append([round(lat, 6), round(lon, 6), round(z, 1)])
    return out


def build():
    if not HMA_PATH.exists():
        sys.exit(f"нет {HMA_PATH.name} — соберите: analysis/build_hma_dem.py")
    dem = Dem(HMA_PATH)
    cover = json.loads(COVER_JSON.read_text())
    h, ni, nj = terrain_grid(dem, cover)
    hmin = float(np.nanmin(h))
    hdm = np.rint((h - hmin) * 10).astype(int)   # дециметры от минимума

    track, _ = parse_gpx(ROOT / "docs/marshrut/plan-track.gpx")
    kml = parse_kml_lines(ROOT / "docs/nakhodki/search_vectors.kml")
    lines = [dict(name="Маршрут группы (план)", color="#c084fc",
                  pts=line3d(dem, track, every=2))]
    for name, pts in kml.items():
        lines.append(dict(name=name, color="#ff5f5f", pts=line3d(dem, pts)))

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

    data = dict(
        lat0=cover["lat0"], lon0=cover["lon0"],
        dlat=cover["dlat"], dlon=cover["dlon"],
        ni=ni, nj=nj, hmin=round(hmin, 1),
        h=hdm.ravel().tolist(),                 # ряды с юга на север
        mlat=M_PER_DEG_LAT,
        mlon=m_per_deg_lon((LAT0 + LAT1) / 2),
        # ячейки обрезаются до [i, j, уровень]: расширенная мета
        # (таймкод, масштаб, проходы) нужна только попапу «чем снята точка» на 2D-карте
        videos=[dict(name=v["name"], date=v["date"],
                     cells=[c[:3] for c in v["cells"]])
                for v in cover["videos"]],
        detail_cm=cover["detail_cm"], mid_cm=cover["mid_cm"],
        lines=lines, marks=marks,
        views=[dict(name=n, lat=la, lon=lo, dist=d) for n, la, lo, d in VIEWS],
    )

    three = (HERE / "vendor/three.min.js").read_text()
    orbit = (HERE / "vendor/OrbitControls.js").read_text()
    html = TEMPLATE.replace("__THREE__", three + "\n" + orbit)
    html = html.replace("__DATA__", json.dumps(data, separators=(",", ":")))
    OUT.write_text(html, "utf-8")
    print(f"{OUT.name}: {OUT.stat().st_size / 2**20:.1f} МБ, "
          f"сетка {ni}x{nj}, роликов {len(cover['videos'])}, "
          f"линий {len(lines)}, отметок {len(marks)}")


TEMPLATE = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>Курумды — покрытие в 3D</title>
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
  #panel { position:fixed; top:56px; left:12px; z-index:2; width:270px;
           background:var(--card); border:1px solid var(--line); border-radius:10px;
           padding:12px 14px; backdrop-filter: blur(4px); }
  #panel h1 { font-size:15px; margin:0 0 8px; }
  #panel label { display:flex; gap:7px; align-items:center; margin:4px 0; cursor:pointer; }
  #panel .sw { flex:0 0 14px; height:14px; border-radius:3px; }
  #panel select { width:100%; margin:4px 0 8px; background:#22262c; color:var(--fg);
                  border:1px solid var(--line); border-radius:6px; padding:4px; }
  #panel .views { display:flex; gap:6px; flex-wrap:wrap; margin-top:8px; }
  #panel .views button { background:#22262c; color:var(--fg); border:1px solid var(--line);
                         border-radius:6px; padding:4px 8px; cursor:pointer; font-size:12px; }
  #panel .views button:hover { background:#2c313a; }
  #panel .note { color:var(--dim); font-size:12px; margin-top:8px;
                 border-top:1px solid var(--line); padding-top:8px; }
  #hint { position:fixed; right:12px; bottom:10px; z-index:2; color:var(--dim); font-size:12px; }
  #fold { display:none; }
  @media (max-width:700px) {
    #panel { width:220px; }
  }
</style></head><body>
<header><nav><span class="title">Курумды — видеоанализ</span>
<a href="index.html">Кандидаты</a>
<a href="montages.html">Все монтажные листы</a>
<a href="map.html">Карта</a>
<a href="panoramy.html">Панорамы</a>
<a href="model-3d.html">3D-модель</a>
<a href="coverage-3d.html"><b>3D-покрытие</b></a>
<a href="polyot-3d.html">Полёт 3D</a>
</nav></header>
<div id="scene"></div>
<div id="panel">
  <h1>Осмотр склонов дроном</h1>
  <select id="day"></select>
  <label><span class="sw" style="background:#35d07f"></span>
    <input type="checkbox" id="t0" checked> детально (предмет ≤ 20 см)</label>
  <label><span class="sw" style="background:#ffd166"></span>
    <input type="checkbox" id="t1" checked> средне (предмет ≤ 1 м)</label>
  <label><span class="sw" style="background:#7fa8c9"></span>
    <input type="checkbox" id="t2" checked> обзорно / масштаб неизвестен</label>
  <label><span class="sw" style="background:#6b655c"></span> не осмотрено</label>
  <label><input type="checkbox" id="labels" checked> подписи (лагеря, вещи)</label>
  <label><input type="checkbox" id="lin" checked> маршрут и коридор</label>
  <div class="views" id="views"></div>
  <div class="note">Та же сетка, что на 2D-карте: проекция рамки кадра в рельеф
  с реальным фокусным, масштаб пересчитан на дистанцию каждого участка кадра,
  порог различимости 8 px (docs/coverage-gsd.md). Рельеф: NASA HMA 8 м
  (WorldView ~2017) + фотограмметрические патчи 2026 г. в пятне вещей
  (docs/dem-patches.md).</div>
</div>
<div id="hint">вращение — мышь · зум — колесо · сдвиг — правая кнопка</div>
<script>__THREE__</script>
<script>
const D = __DATA__;

// --- геометрия: ENU-метры от центра рамки -----------------------------------
const latC = D.lat0 + (D.ni / 2) * D.dlat, lonC = D.lon0 + (D.nj / 2) * D.dlon;
const elevC = D.hmin;
function toXYZ(lat, lon, alt) {
  return [(lon - lonC) * D.mlon, alt - elevC, -(lat - latC) * D.mlat];
}

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0f1113);
scene.fog = new THREE.Fog(0x0f1113, 9000, 22000);
const camera = new THREE.PerspectiveCamera(55, innerWidth/innerHeight, 5, 30000);
const renderer = new THREE.WebGLRenderer({antialias:true});
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
document.getElementById('scene').appendChild(renderer.domElement);
const controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.enableDamping = true; controls.maxPolarAngle = Math.PI * 0.495;

scene.add(new THREE.HemisphereLight(0xcfd8e6, 0x3a352e, .8));
const sun = new THREE.DirectionalLight(0xffffff, .8);
sun.position.set(-2000, 3000, -1500);
scene.add(sun);

// --- рельеф -------------------------------------------------------------------
const ni = D.ni, nj = D.nj;
const pos = new Float32Array(ni * nj * 3);
const col = new Float32Array(ni * nj * 3);
for (let i = 0; i < ni; i++)
  for (let j = 0; j < nj; j++) {
    const k = i * nj + j;
    const lat = D.lat0 + (i + .5) * D.dlat, lon = D.lon0 + (j + .5) * D.dlon;
    const alt = D.hmin + D.h[k] / 10;
    const [x, y, z] = toXYZ(lat, lon, alt);
    pos[k*3] = x; pos[k*3+1] = y; pos[k*3+2] = z;
  }
const idx = [];
for (let i = 0; i < ni - 1; i++)
  for (let j = 0; j < nj - 1; j++) {
    const a = i*nj+j, b = a+1, c = a+nj, d = c+1;
    idx.push(a, b, c, b, d, c);
  }
const geo = new THREE.BufferGeometry();
geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
geo.setIndex(idx);
geo.computeVertexNormals();
scene.add(new THREE.Mesh(geo, new THREE.MeshLambertMaterial({vertexColors: true})));

// --- покрытие: раскраска вершин -----------------------------------------------
const TIER_RGB = [[0x35/255,0xd0/255,0x7f/255], [1,0xd1/255,0x66/255],
                  [0x7f/255,0xa8/255,0xc9/255]];
const BARE = [0x8a/255, 0x83/255, 0x78/255];
function repaint() {
  const day = document.getElementById('day').value;
  const show = [0,1,2].map(t => document.getElementById('t'+t).checked);
  const tier = new Int8Array(ni * nj).fill(-1);
  for (const v of D.videos) {
    if (day !== 'all' && v.date !== day) continue;
    for (const [i, j, t] of v.cells) {
      const k = i * nj + j;
      if (tier[k] < 0 || t < tier[k]) tier[k] = t;
    }
  }
  for (let k = 0; k < ni * nj; k++) {
    const t = tier[k];
    const c = (t >= 0 && show[t]) ? TIER_RGB[t] : BARE;
    col[k*3] = c[0]; col[k*3+1] = c[1]; col[k*3+2] = c[2];
  }
  geo.attributes.color.needsUpdate = true;
}

// --- линии и отметки ------------------------------------------------------------
const linGroup = new THREE.Group(), labGroup = new THREE.Group();
scene.add(linGroup); scene.add(labGroup);
for (const L of D.lines) {
  const pts = L.pts.map(p => new THREE.Vector3(...toXYZ(p[0], p[1], p[2])));
  const g = new THREE.BufferGeometry().setFromPoints(pts);
  linGroup.add(new THREE.Line(g, new THREE.LineBasicMaterial({color: L.color})));
}
function label(text, xyz, color, size) {
  const c = document.createElement('canvas');
  const ctx = c.getContext('2d');
  ctx.font = '26px system-ui';
  c.width = Math.ceil(ctx.measureText(text).width) + 16; c.height = 38;
  const ctx2 = c.getContext('2d');
  ctx2.font = '26px system-ui';
  ctx2.fillStyle = '#0f1113cc'; ctx2.fillRect(0, 0, c.width, c.height);
  ctx2.fillStyle = color; ctx2.fillText(text, 8, 28);
  const tex = new THREE.CanvasTexture(c);
  // sizeAttenuation:false — подпись постоянного экранного размера
  const sp = new THREE.Sprite(new THREE.SpriteMaterial(
    {map: tex, depthTest: false, sizeAttenuation: false}));
  sp.position.set(xyz[0], xyz[1] + 25, xyz[2]);
  sp.scale.set(c.width / 38 * size, size, 1);
  sp.center.set(0.5, 0);
  labGroup.add(sp);
}
for (const m of D.marks) {
  const xyz = toXYZ(m.lat, m.lon, m.alt + 3);
  const isCamp = m.kind === 'camp';
  const dot = new THREE.Mesh(
    new THREE.SphereGeometry(isCamp ? 9 : 6, 12, 12),
    new THREE.MeshBasicMaterial({color: isCamp ? 0xc084fc : 0xff5f5f}));
  dot.position.set(...xyz);
  labGroup.add(dot);
  const nm = m.name.length > 28 ? m.name.slice(0, 27) + '…' : m.name;
  label(nm, xyz, isCamp ? '#d9c4ff' : '#ffb3b3', isCamp ? .055 : .042);
}

// --- панель -------------------------------------------------------------------
const daySel = document.getElementById('day');
daySel.innerHTML = '<option value="all">Все дни</option>' +
  [...new Set(D.videos.map(v => v.date))].sort()
    .map(d => `<option>${d}</option>`).join('');
daySel.onchange = repaint;
for (const t of [0,1,2]) document.getElementById('t'+t).onchange = repaint;
document.getElementById('labels').onchange =
  e => labGroup.visible = e.target.checked;
document.getElementById('lin').onchange =
  e => linGroup.visible = e.target.checked;
const viewsDiv = document.getElementById('views');
function flyTo(v) {
  const alt = sampleAlt(v.lat, v.lon);
  const t = new THREE.Vector3(...toXYZ(v.lat, v.lon, alt));
  controls.target.copy(t);
  camera.position.set(t.x + v.dist * .35, t.y + v.dist * .95, t.z + v.dist * .6);
}
function sampleAlt(lat, lon) {
  const i = Math.min(ni - 1, Math.max(0, Math.round((lat - D.lat0) / D.dlat - .5)));
  const j = Math.min(nj - 1, Math.max(0, Math.round((lon - D.lon0) / D.dlon - .5)));
  return D.hmin + D.h[i * nj + j] / 10;
}
for (const v of D.views) {
  const b = document.createElement('button');
  b.textContent = v.name;
  b.onclick = () => flyTo(v);
  viewsDiv.appendChild(b);
}

repaint();
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
