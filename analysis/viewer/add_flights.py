#!/usr/bin/env python3
"""Добавляет слой облётов (трек + направление камеры) в уже собранный map.html,
не требуя пересборки всей карты (DEM не нужен).

Источник данных: сайдкары <видео>.MP4.gps.tsv (scripts/dji_meta_gps.py) —
колонки time_s, lat, lon, alt_m, alt_rel_m, ac_yaw, ac_pitch, ac_roll, gb_yaw, gb_pitch.

Трек прорежен до ~1 точки/0.5с, стрелки направления камеры — ~1/3с (используется
gb_yaw подвеса, а не ac_yaw борта — это фактическое направление съёмки).

Использование:
  python3 add_flights.py <map.html> <video1.gps.tsv> [video2.gps.tsv ...]
"""

import json
import math
import re
import sys
from pathlib import Path

TRACK_STEP_S = 0.5
ARROW_STEP_S = 3.0
ARROW_LEN_M = 120

COLORS = ["#ff4081", "#7c4dff", "#00e676", "#ffab00", "#18ffff", "#f4511e", "#c6ff00", "#ea80fc"]

M_PER_DEG_LAT = 111132.0


def m_per_deg_lon(lat):
    return 111320.0 * math.cos(math.radians(lat))


def load_tsv(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        header = f.readline().strip().split("\t")
        idx = {name: i for i, name in enumerate(header)}
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < len(header) or any(parts[i] == "" for i in idx.values()):
                continue
            try:
                rows.append({k: float(parts[i]) for k, i in idx.items()})
            except ValueError:
                continue
    return rows


def build_flight(tsv_path, color):
    rows = load_tsv(tsv_path)
    if not rows:
        return None
    name = Path(tsv_path).name.replace(".MP4.gps.tsv", "").replace(".gps.tsv", "")

    track = []
    last_t = -1e9
    for r in rows:
        if r["time_s"] - last_t >= TRACK_STEP_S:
            track.append([round(r["lat"], 6), round(r["lon"], 6)])
            last_t = r["time_s"]

    arrows = []
    last_t = -1e9
    for r in rows:
        if r["time_s"] - last_t >= ARROW_STEP_S:
            last_t = r["time_s"]
            lat, lon, yaw = r["lat"], r["lon"], r["gb_yaw"]
            dlat = (ARROW_LEN_M * math.cos(math.radians(yaw))) / M_PER_DEG_LAT
            dlon = (ARROW_LEN_M * math.sin(math.radians(yaw))) / m_per_deg_lon(lat)
            tip = [round(lat + dlat, 6), round(lon + dlon, 6)]
            mm, ss = divmod(int(r["time_s"]), 60)
            arrows.append({
                "p": [round(lat, 6), round(lon, 6)],
                "tip": tip,
                "t": f"{mm}:{ss:02d}",
                "alt": round(r["alt_rel_m"]),
            })

    return {"name": name, "color": color, "track": track, "arrows": arrows,
            "n": len(rows), "dur": round(rows[-1]["time_s"])}


JS_BLOCK = """
// --- облёты дрона (трек + направление камеры), добавлено add_flights.py ---
const flightsLayer = L.layerGroup();
for (const fl of (D.flights || [])) {
  const track = L.polyline(fl.track, {color: fl.color, weight: 2.5, opacity: 0.85});
  track.bindPopup(`<b>${fl.name}</b><br>${fl.n} кадров, ${Math.floor(fl.dur/60)}:${String(fl.dur%60).padStart(2,'0')} мин`);
  track.addTo(flightsLayer);
  for (const a of fl.arrows) {
    L.polyline([a.p, a.tip], {color: fl.color, weight: 1.3, opacity: 0.55})
      .bindPopup(`<b>${fl.name}</b> ${a.t}<br>направление камеры (gimbal yaw), высота ${a.alt} м отн.`)
      .addTo(flightsLayer);
  }
  if (fl.track.length) {
    L.circleMarker(fl.track[0], {radius: 4, color: fl.color, fillColor: fl.color, fillOpacity: 1, weight: 1})
      .bindPopup(`<b>${fl.name}</b> — старт трека`).addTo(flightsLayer);
  }
}
"""

OVERLAY_ENTRY = "  ['Облёты (трек + камера)', flightsLayer, false],\n"


def main():
    map_path = Path(sys.argv[1])
    tsv_paths = sys.argv[2:]
    html = map_path.read_text("utf-8")

    m = re.search(r"const D = (\{.*?\});", html, re.S)
    if not m:
        print("Не нашёл 'const D = {...};' в map.html", file=sys.stderr)
        sys.exit(1)
    data = json.loads(m.group(1))

    flights = []
    for i, tp in enumerate(tsv_paths):
        fl = build_flight(tp, COLORS[i % len(COLORS)])
        if fl:
            flights.append(fl)
            print(f"  + {fl['name']}: {len(fl['track'])} точек трека, {len(fl['arrows'])} стрелок камеры")
    data["flights"] = flights

    new_d = "const D = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";"
    html = html[:m.start()] + new_d + html[m.end():]

    if "add_flights.py" not in html:
        anchor = "// --- панель слоёв ---"
        assert anchor in html, "не нашёл якорь панели слоёв"
        html = html.replace(anchor, JS_BLOCK + "\n" + anchor)
        anchor2 = "const overlays = [\n"
        assert anchor2 in html, "не нашёл начало списка overlays"
        html = html.replace(anchor2, anchor2 + OVERLAY_ENTRY)

    map_path.write_text(html, "utf-8")
    kb = map_path.stat().st_size // 1024
    print(f"готово: {map_path} ({kb} КБ), облётов добавлено: {len(flights)}")


if __name__ == "__main__":
    main()
