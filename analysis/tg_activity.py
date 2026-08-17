#!/usr/bin/env python3
"""Аналитика активности волонтёров в TG-группе «Анализ видео с дронам».

Источник: выгрузка docs/telegram/<тема>/messages.md (scripts/tg_export.py).
Что делает:
  1. Парсит все messages.md -> SQLite analysis/tg_activity.sqlite (таблица messages).
  2. Считает по авторам: сообщения, период и сессии активности (оценка времени),
     поданные фото-кандидаты, принятые фото (dHash-сверка с темой «Подтвержденные»).
  3. Пишет агрегаты в analysis/tg_activity.json и по-авторские дайджесты
     сообщений для LLM-оценки в scratch-директорию (--digests DIR).

Запуск: analysis/.venv/bin/python analysis/tg_activity.py [--digests DIR]
"""

import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "telegram"
DB_PATH = ROOT / "analysis" / "tg_activity.sqlite"
JSON_PATH = ROOT / "analysis" / "tg_activity.json"

# личные диалоги — не групповая активность, в статистику не входят
PRIVATE_SLUGS = {"lichka-gennadiy-bege"}
# темы, куда подают кандидатов-находок (фото «с файндингами»)
CANDIDATE_SLUGS = {
    "novye-skrinshoty", "veshchi", "pod-voprosom",
    "gipoteza-2-kurtka-ili-spalnik", "1-gipotezy", "dlya-pereproverki-dronom",
}
ACCEPTED_SLUG = "podtverzhdennye"

HEADER_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\] #(\d+) (.*?):(?: \[медиа: ([^\]]+)\])?$"
)

SESSION_GAP_MIN = 45   # разрыв, после которого начинается новая сессия
SESSION_BASE_MIN = 10  # минимальная стоимость сессии (зашёл-посмотрел-написал)
HASH_HAMMING_MAX = 8   # порог совпадения dHash (из 64 бит)


def parse_messages():
    msgs = []
    for md in sorted(DOCS.glob("*/messages.md")):
        slug = md.parent.name
        cur = None
        text_lines = []
        for line in md.read_text(encoding="utf-8").splitlines():
            m = HEADER_RE.match(line)
            if m:
                if cur:
                    cur["text"] = "\n".join(text_lines).strip()
                    msgs.append(cur)
                dt, msg_id, author, media = m.groups()
                cur = {
                    "topic": slug,
                    "msg_id": int(msg_id),
                    "dt": dt,
                    "author": author.strip(),
                    "media": media or "",
                }
                text_lines = []
            elif cur is not None and not line.startswith("# "):
                text_lines.append(line)
        if cur:
            cur["text"] = "\n".join(text_lines).strip()
            msgs.append(cur)
    return msgs


def fill_db(msgs):
    DB_PATH.unlink(missing_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE messages (
        topic TEXT, msg_id INTEGER, dt TEXT, author TEXT,
        media TEXT, text TEXT, is_private INTEGER)""")
    con.executemany(
        "INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
        [(m["topic"], m["msg_id"], m["dt"], m["author"], m["media"], m["text"],
          int(m["topic"] in PRIVATE_SLUGS)) for m in msgs])
    con.commit()
    return con


def dhash(path: Path):
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    small = cv2.resize(img, (9, 8), interpolation=cv2.INTER_AREA)
    diff = small[:, 1:] > small[:, :-1]
    return np.packbits(diff).tobytes()


def hamming(a: bytes, b: bytes) -> int:
    return int(np.unpackbits(np.frombuffer(
        bytes(x ^ y for x, y in zip(a, b)), dtype=np.uint8)).sum())


IMG_EXT = {".jpg", ".jpeg", ".png"}


def build_accepted_index(msgs):
    """dHash всех картинок в «Подтвержденные» -> список (hash, dt)."""
    idx = []
    for m in msgs:
        if m["topic"] != ACCEPTED_SLUG or not m["media"]:
            continue
        p = ROOT / m["media"]
        if p.suffix.lower() not in IMG_EXT or not p.exists():
            continue
        h = dhash(p)
        if h:
            idx.append((h, m["dt"], m["msg_id"]))
    return idx


def sessions_minutes(dts):
    """Оценка времени в чате: сессии с разрывом <= SESSION_GAP_MIN минут."""
    times = sorted(datetime.strptime(d, "%Y-%m-%d %H:%M") for d in dts)
    total = 0.0
    n_sessions = 0
    start = prev = times[0]
    for t in times[1:]:
        if (t - prev).total_seconds() / 60 > SESSION_GAP_MIN:
            total += (prev - start).total_seconds() / 60 + SESSION_BASE_MIN
            n_sessions += 1
            start = t
        prev = t
    total += (prev - start).total_seconds() / 60 + SESSION_BASE_MIN
    n_sessions += 1
    return total, n_sessions


def aggregate(msgs):
    group_msgs = [m for m in msgs if m["topic"] not in PRIVATE_SLUGS]

    authors = {}
    candidate_posts = []  # (hash, dt, author) — поданные кандидаты
    accepted_posts = []   # (hash, dt, poster) — картинки в «Подтвержденных»
    for m in group_msgs:
        a = authors.setdefault(m["author"], {
            "messages": 0, "text_messages": 0, "dts": [],
            "media_total": 0, "candidates": 0, "accepted": 0, "topics": {},
        })
        a["messages"] += 1
        a["dts"].append(m["dt"])
        a["topics"][m["topic"]] = a["topics"].get(m["topic"], 0) + 1
        if m["text"]:
            a["text_messages"] += 1
        if m["media"]:
            a["media_total"] += 1
            p = ROOT / m["media"]
            h = dhash(p) if p.suffix.lower() in IMG_EXT and p.exists() else None
            if m["topic"] in CANDIDATE_SLUGS:
                a["candidates"] += 1
                if h:
                    candidate_posts.append((h, m["dt"], m["author"]))
            if m["topic"] == ACCEPTED_SLUG and h:
                accepted_posts.append((h, m["dt"], m["author"]))

    # Кластеризуем принятые картинки (один кадр могли перепостить дважды),
    # каждый кластер атрибутируем самому раннему подателю кандидата;
    # если кандидата не было — тому, кто запостил в «Подтвержденные».
    accepted_posts.sort(key=lambda x: x[1])
    clusters = []  # (rep_hash, first_dt, poster)
    for h, dt, poster in accepted_posts:
        if any(hamming(h, rh) <= HASH_HAMMING_MAX for rh, _, _ in clusters):
            continue
        clusters.append((h, dt, poster))
    for rh, adt, poster in clusters:
        matches = [(dt, author) for h, dt, author in candidate_posts
                   if dt <= adt and hamming(h, rh) <= HASH_HAMMING_MAX]
        credit = min(matches)[1] if matches else poster
        authors[credit]["accepted"] += 1

    out = []
    for name, a in authors.items():
        minutes, n_sessions = sessions_minutes(a["dts"])
        days = sorted({d[:10] for d in a["dts"]})
        out.append({
            "author": name,
            "messages": a["messages"],
            "text_messages": a["text_messages"],
            "days_active": len(days),
            "first_day": days[0],
            "last_day": days[-1],
            "est_hours": round(minutes / 60, 1),
            "sessions": n_sessions,
            "media_total": a["media_total"],
            "candidates": a["candidates"],
            "accepted": a["accepted"],
            "topics": a["topics"],
        })
    out.sort(key=lambda x: -x["messages"])
    return out


def write_digests(msgs, agg, digest_dir: Path, min_messages: int):
    digest_dir.mkdir(parents=True, exist_ok=True)
    names = [a["author"] for a in agg if a["messages"] >= min_messages]
    by_author = {}
    for m in msgs:
        if m["topic"] in PRIVATE_SLUGS or m["author"] not in names:
            continue
        by_author.setdefault(m["author"], []).append(m)
    for i, name in enumerate(names):
        lines = [f"# Сообщения автора «{name}» по темам группы", ""]
        budget = 24000
        for m in sorted(by_author.get(name, []), key=lambda x: x["dt"]):
            text = " ".join(m["text"].split())
            if len(text) > 400:
                text = text[:400] + "…"
            tag = " [фото]" if m["media"] else ""
            row = f"[{m['dt']}] ({m['topic']}){tag} {text}"
            if budget - len(row) < 0:
                lines.append(f"… (обрезано, всего сообщений: {len(by_author[name])})")
                break
            budget -= len(row)
            lines.append(row)
        (digest_dir / f"{i:02d}.md").write_text("\n".join(lines), encoding="utf-8")
    (digest_dir / "index.json").write_text(
        json.dumps({f"{i:02d}.md": n for i, n in enumerate(names)},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    return names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--digests", help="директория для по-авторских дайджестов")
    ap.add_argument("--min-messages", type=int, default=15)
    args = ap.parse_args()

    msgs = parse_messages()
    con = fill_db(msgs)
    n_priv = sum(m["topic"] in PRIVATE_SLUGS for m in msgs)
    print(f"messages: {len(msgs)} (из них личка: {n_priv}) -> {DB_PATH}")

    agg = aggregate(msgs)
    JSON_PATH.write_text(json.dumps(agg, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    print(f"authors: {len(agg)} -> {JSON_PATH}")

    if args.digests:
        names = write_digests(msgs, agg, Path(args.digests), args.min_messages)
        print(f"digests: {len(names)} авторов (>= {args.min_messages} сообщ.) -> {args.digests}")
    con.close()


if __name__ == "__main__":
    main()
