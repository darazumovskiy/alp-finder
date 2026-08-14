#!/usr/bin/env python3
"""Декодер треков nakarte.me из nktl-ссылок в GPX.

Ссылка вида https://nakarte.me/#...&nktl=<id> указывает на данные на сервере
https://tracks.nakarte.me/track/<id>. Формат: треки разделены '/', каждый —
URL-safe Base64 от (байт версии + protobuf TrackView, схема nktk.proto из
репозитория wladich/nakarte). Координаты — дельта-кодированные sint32 с шагом
360/(2^24-1) градуса. Таймстемпов формат не содержит — только геометрия.

Использование:
    python analysis/nakarte_nktl.py <nktl-id или полная ссылка> <выходная-директория>
"""

import base64
import pathlib
import re
import sys
import urllib.request
from xml.sax.saxutils import escape

ARC_UNIT = ((1 << 24) - 1) / 360


def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def zigzag(n: int) -> int:
    return (n >> 1) ^ -(n & 1)


def parse_message(buf: bytes) -> dict[int, list]:
    """Разбирает protobuf-сообщение в словарь {номер поля: [значения]}."""
    fields: dict[int, list] = {}
    pos = 0
    while pos < len(buf):
        key, pos = read_varint(buf, pos)
        field_no, wire_type = key >> 3, key & 7
        if wire_type == 0:
            value, pos = read_varint(buf, pos)
        elif wire_type == 2:
            length, pos = read_varint(buf, pos)
            value = buf[pos:pos + length]
            pos += length
        else:
            raise ValueError(f"неожиданный wire type {wire_type} (поле {field_no})")
        fields.setdefault(field_no, []).append(value)
    return fields


def parse_packed_sint32(chunks: list[bytes]) -> list[int]:
    values = []
    for chunk in chunks:
        pos = 0
        while pos < len(chunk):
            raw, pos = read_varint(chunk, pos)
            values.append(zigzag(raw))
    return values


def delta_decode(deltas: list[int]) -> list[float]:
    acc = 0
    out = []
    for d in deltas:
        acc += d
        out.append(acc / ARC_UNIT)
    return out


def parse_fragment(fragment: str) -> dict:
    pad = "=" * (-len(fragment) % 4)
    raw = base64.urlsafe_b64decode(fragment + pad)
    version = raw[0] - 64
    if version != 4:
        raise ValueError(f"версия {version} не поддерживается (только 4, protobuf)")
    track_view = parse_message(raw[1:])
    track = parse_message(track_view[2][0])
    name = track[1][0].decode("utf-8") if 1 in track else "Track"
    segments = []
    for seg_buf in track.get(2, []):
        seg = parse_message(seg_buf)
        lats = delta_decode(parse_packed_sint32(seg.get(1, [])))
        lons = delta_decode(parse_packed_sint32(seg.get(2, [])))
        segments.append(list(zip(lats, lons)))
    waypoints = []
    if 3 in track:
        wpts_msg = parse_message(track[3][0])
        mid_lat = zigzag(wpts_msg[1][0]) if 1 in wpts_msg else 0
        mid_lon = zigzag(wpts_msg[2][0]) if 2 in wpts_msg else 0
        for wp_buf in wpts_msg.get(3, []):
            wp = parse_message(wp_buf)
            waypoints.append({
                "lat": (zigzag(wp[1][0]) + mid_lat) / ARC_UNIT if 1 in wp else mid_lat / ARC_UNIT,
                "lon": (zigzag(wp[2][0]) + mid_lon) / ARC_UNIT if 2 in wp else mid_lon / ARC_UNIT,
                "name": wp[3][0].decode("utf-8") if 3 in wp else "",
            })
    return {"name": name, "segments": segments, "waypoints": waypoints}


def to_gpx(track: dict) -> str:
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<gpx version="1.1" creator="nakarte_nktl" xmlns="http://www.topografix.com/GPX/1/1">']
    for wp in track["waypoints"]:
        parts.append(f'  <wpt lat="{wp["lat"]:.6f}" lon="{wp["lon"]:.6f}">'
                     f'<name>{escape(wp["name"])}</name></wpt>')
    if track["segments"]:
        parts.append(f'  <trk><name>{escape(track["name"])}</name>')
        for seg in track["segments"]:
            parts.append("    <trkseg>")
            parts.extend(f'      <trkpt lat="{lat:.6f}" lon="{lon:.6f}"/>' for lat, lon in seg)
            parts.append("    </trkseg>")
        parts.append("  </trk>")
    parts.append("</gpx>")
    return "\n".join(parts)


def slugify(name: str) -> str:
    translit = str.maketrans(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя ",
        "abvgdeejziyklmnoprstufhccss_y_eua-")
    slug = name.lower().translate(translit)
    slug = re.sub(r"[^a-z0-9_.-]+", "-", slug).strip("-")
    return slug or "track"


def main() -> None:
    src, out_dir = sys.argv[1], pathlib.Path(sys.argv[2])
    m = re.search(r"nktl=([\w-]+)", src)
    nktl_id = m.group(1) if m else src
    with urllib.request.urlopen(f"https://tracks.nakarte.me/track/{nktl_id}") as resp:
        data = resp.read().decode("ascii")
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, fragment in enumerate(data.split("/")):
        track = parse_fragment(fragment)
        n_points = sum(len(s) for s in track["segments"])
        fname = f"{i:02d}-{slugify(track['name'])}.gpx"
        (out_dir / fname).write_text(to_gpx(track), encoding="utf-8")
        kind = f"{len(track['segments'])} сегм., {n_points} точек" if track["segments"] else \
               f"{len(track['waypoints'])} путевых точек"
        print(f"{fname}\t{track['name']}\t{kind}")


if __name__ == "__main__":
    main()
