#!/usr/bin/env python3
"""Ежедневный ИИ-комментарий (Claude Fable 5) к данным мониторинга снега → agent/notes/<date>.json.

Правила:
- модель ТОЛЬКО claude-fable-5 (решение оператора: люди могут пойти по этим данным, экономить нельзя);
  запасных моделей нет — при отказе модели заметка за день просто не создаётся и это видно на странице;
- агент НЕ считает числа: получает готовую ленту (timeline.json), журнал «факт против модели» и
  картинки; любое число в ответе должно быть из входных данных;
- ответ — строгий JSON по схеме ниже; ИИ-текст на странице помечается отдельно от расчётов;
- ключ Anthropic — из окружения или ~/.hw-workspace/secrets.env (ANTHROPIC_API_KEY_2, затем
  ANTHROPIC_API_KEY), как в hw-all/dd-workflow/driver-claude.ts; в логи не попадает.
Даты (решение оператора 28.08: оценка должна лежать на той же карточке, что и снимок):
- --auto (конвейер): заметка за сегодня (пересоздаётся каждым прогоном — данные меняются) и, если
  у последнего снимка Sentinel-2 заметки ещё нет, — за его дату;
- --date YYYY-MM-DD: одна дата; для прошлых дат заметка создаётся «задним числом» только по данным,
  доступным на ту дату (лента и журнал обрезаются, дни после даты не показываются) и помечается backfill.
Запуск: analysis/.venv/bin/python analysis/osadki/agent_daily.py [--auto | --date YYYY-MM-DD] [--dry-run]
"""
import argparse
import base64
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OS = ROOT / "analysis/osadki"
DATA = ROOT / "analysis/osadki/data"
NOTES = ROOT / "analysis/osadki/agent/notes"
SCENES = ROOT / "analysis/osadki/scenes"
MODEL = "claude-fable-5"

SYSTEM = """Ты — аналитик данных при штабе поисково-спасательной операции на пике Курумды (Кыргызстан).
На горе остаётся тело пропавшего альпиниста; штаб ждёт, когда снег на склоне сойдёт, чтобы найти его с дрона.
Тебе дают: ленту данных по дням (погодная модель по высотам, индекс снега по спутнику Sentinel-2, радар,
метеостанции, ежедневный VIIRS), журнал сверки с очевидцами и несколько снимков. Твоя задача — написать
короткий понятный комментарий для людей без метео-подготовки: что изменилось за последние дни, снега на
склоне стало больше или меньше, насколько этому можно верить, за чем следить дальше.

Жёсткие правила:
1. Числа берёшь только из переданных данных, ничего не пересчитываешь и не оцениваешь «на глаз» в сантиметрах.
2. Отдельно описываешь, что ВИДНО на снимках (это твоё наблюдение), и отдельно — что говорят расчёты.
3. Если данных нет или снимок облачный — так и пишешь. Не заполняешь пробелы догадками.
4. Не даёшь советов о безопасности и не принимаешь решений за штаб; только описываешь данные.
5. Пишешь по-русски, коротко, без жаргона; термины объясняешь одной фразой.
6. Всегда указываешь уровень доверия и почему.
Отвечай ТОЛЬКО JSON-объектом (без markdown) со схемой:
{"date": "YYYY-MM-DD",
 "summary": "2–4 предложения для штаба",
 "trend": "меньше" | "больше" | "без явных изменений" | "не видно (облака/нет данных)",
 "trend_basis": "на чём основан вывод о тренде (даты, площадки, числа из данных)",
 "image_observations": "что видно на приложенных снимках своими словами (или «снимков нет»)",
 "model_vs_fact": "что модель показала за последние дни и есть ли расхождения с фактами/спутником",
 "confidence": "низкая" | "средняя" | "высокая",
 "confidence_why": "почему",
 "flags": ["короткие предупреждения, например: снегопад по модели 3 сентября ≥5 мм с вероятностью 40 %"],
 "watch_next": "за чем следить в ближайшие дни (даты пролётов, прогноз)"}"""


def load_key():
    for name in ("ANTHROPIC_API_KEY_2", "ANTHROPIC_API_KEY"):
        if os.environ.get(name):
            return os.environ[name], f"{name} (env)"
    p = Path.home() / ".hw-workspace" / "secrets.env"
    store = {}
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                store[k.strip().removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    for name in ("ANTHROPIC_API_KEY_2", "ANTHROPIC_API_KEY"):
        if store.get(name):
            return store[name], f"{name} (secrets.env)"
    return None, None


def b64(path):
    return base64.standard_b64encode(Path(path).read_bytes()).decode()


def pick_images(timeline, date):
    """Последние два ясных снимка S2 (зона: RGB и маска), последний радар с разницей, VIIRS за дату, оцениваемое множество."""
    imgs = []
    days = sorted(d for d in timeline if d <= date)
    clear = []
    for d in reversed(days):
        for s in timeline[d].get("s2", []):
            if (s.get("coverage_zona") or 0) >= 0.95 and (SCENES / "s2" / f"{d}_{s['sat']}" / "zone_rgb.png").exists():
                clear.append((d, s))
        if len(clear) >= 2:
            break
    for i, (d, s) in enumerate(clear[:2]):
        tag = "последний ясный" if i == 0 else "предыдущий ясный"
        for panel, lab in (("zone_rgb", "обычные цвета"), ("zone_mask", "маска снег/камень/облако")):
            fp = SCENES / "s2" / f"{d}_{s['sat']}" / f"{panel}.png"  # локальные PNG (на сайте — спрайты)
            if fp.exists():
                imgs.append((f"Sentinel-2 {d} ({tag}), {lab}, 3×3 км; рамки: оранжевая — рюкзак, синяя — зона интереса, зелёная — гребень C1, кружок — контрольная точка", fp))
    for d in reversed(days):
        for s in timeline[d].get("s1", []):
            fp = SCENES / "s1" / f"{d}_{s['orbit']}" / "zone_diff.png"
            if fp.exists():
                imgs.append((f"Радар Sentinel-1 {d}: изменение сигнала к предыдущему пролёту того же трека (синее — упал: намокло/потеплело; красное — вырос)", fp))
                break
        else:
            continue
        break
    v = SCENES / "viirs" / date / "near_fc.png"
    if v.exists():
        imgs.append((f"VIIRS {date}, 375 м, ложные цвета: голубое — снег/лёд, белое — облака", v))
    ev = SCENES / "s2" / "_evaluable.png"
    if ev.exists():
        imgs.append(("Какие пиксели входят в расчёт индекса (цветом — пояса высот)", ev))
    return imgs[:6]


def today_bishkek():
    return (dt.datetime.utcnow() + dt.timedelta(hours=6)).date().isoformat()


def cut_journal(text, date):
    """Строки журнала только по дату включительно (для заметок задним числом — без подглядывания вперёд)."""
    out = []
    for ln in text.splitlines():
        if ln.startswith("#") or ln.startswith("date\t") or not ln.strip():
            out.append(ln); continue
        if ln.split("\t")[0] <= date:
            out.append(ln)
    return "\n".join(out)


def make_note(timeline, date, ndays, dry_run):
    today = today_bishkek()
    backfill = date < today
    days = sorted(d for d in timeline)
    window = [d for d in days if d <= date][-ndays:] + ([] if backfill else [d for d in days if d > date][:5])
    # заметки агента за другие дни модели не показываем: она должна оценивать данные, а не свои прошлые тексты
    compact = {d: {k: v for k, v in timeline[d].items() if k not in ("panels", "ai")} for d in window}
    journal = (OS / "fakt-vs-model.tsv").read_text() if (OS / "fakt-vs-model.tsv").exists() else ""
    if backfill:
        journal = cut_journal(journal, date)
    imgs = pick_images(timeline, date)
    intro = (f"Сегодня {date}. Лента данных по дням (последние {ndays} дней + прогноз), JSON:" if not backfill else
             f"Оцениваемая дата — {date}; заметка создаётся задним числом ({today}), но только по данным, известным на {date}: "
             f"дни после этой даты не показаны, их нет и в журнале. Пиши так, как если бы сегодня было {date}. "
             f"Лента данных по дням (последние {ndays} дней), JSON:")
    content = [{"type": "text", "text": f"{intro}\n{json.dumps(compact, ensure_ascii=False)}\n\n"
                                        f"Журнал «факт против модели» (TSV):\n{journal}\n\nК сообщению приложены снимки, подписи по порядку:\n" + "\n".join(f"{i+1}. {lab}" for i, (lab, _) in enumerate(imgs))}]
    for lab, fp in imgs:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64(fp)}})
    content.append({"type": "text", "text": f"Напиши комментарий за {date} по схеме. Только JSON."})
    if dry_run:
        print(json.dumps({"date": date, "backfill": backfill, "days": window, "images": [str(f) for _, f in imgs], "text_chars": len(content[0]["text"])}, ensure_ascii=False, indent=1)); return
    key, src = load_key()
    if not key:
        print("ключ Anthropic не найден (окружение / ~/.hw-workspace/secrets.env)"); sys.exit(3)
    import anthropic
    client = anthropic.Anthropic(api_key=key, max_retries=3, timeout=600)
    NOTES.mkdir(parents=True, exist_ok=True)
    try:
        with client.messages.stream(model=MODEL, max_tokens=6000, system=SYSTEM, output_config={"effort": "high"},
                                    messages=[{"role": "user", "content": content}]) as stream:
            resp = stream.get_final_message()
    except anthropic.APIStatusError as e:
        print("ошибка API:", e.status_code, e.message); sys.exit(4)
    note = {"date": date, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"), "model": resp.model,
            "backfill": backfill, "key_source": src, "stop_reason": resp.stop_reason, "usage": {"in": resp.usage.input_tokens, "out": resp.usage.output_tokens},
            "images": [lab for lab, _ in imgs], "ok": False}
    if resp.stop_reason == "refusal":
        note["error"] = f"модель отказалась отвечать ({getattr(resp.stop_details, 'category', None)})"
    else:
        text = "".join(b.text for b in resp.content if b.type == "text")
        note["raw"] = text
        m = re.search(r"\{.*\}", text, re.S)
        try:
            js = json.loads(m.group(0)) if m else None
            if js and isinstance(js, dict) and "summary" in js:
                note.update({k: js.get(k) for k in ("summary", "trend", "trend_basis", "image_observations", "model_vs_fact", "confidence", "confidence_why", "flags", "watch_next")})
                note["ok"] = True
            else:
                note["error"] = "ответ не по схеме"
        except json.JSONDecodeError:
            note["error"] = "ответ не JSON"
    json.dump(note, open(NOTES / f"{date}.json", "w"), ensure_ascii=False, indent=1)
    print("заметка", date, "ok" if note["ok"] else note.get("error"), "| токены", note["usage"], "| модель", note["model"], "| задним числом" if backfill else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="одна дата (по умолчанию — сегодня по Бишкеку)")
    ap.add_argument("--auto", action="store_true", help="режим конвейера: сегодня + последний снимок S2 без заметки")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--days", type=int, default=10)
    a = ap.parse_args()
    tl_path = DATA / "timeline.json"
    if not tl_path.exists():
        print("нет data/timeline.json — сначала build_report.py"); sys.exit(2)
    timeline = json.load(open(tl_path))["days"]
    today = today_bishkek()
    if a.auto:
        targets = []
        s2_dates = [d for d in sorted(timeline) if timeline[d].get("s2") and d <= today]
        if s2_dates and s2_dates[-1] != today and not (NOTES / f"{s2_dates[-1]}.json").exists():
            targets.append(s2_dates[-1])
        targets.append(today)
    else:
        targets = [a.date or today]
    for d in targets:
        if d not in timeline:
            print("даты нет в ленте:", d); continue
        make_note(timeline, d, a.days, a.dry_run)


if __name__ == "__main__":
    main()
