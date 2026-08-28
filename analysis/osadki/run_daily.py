#!/usr/bin/env python3
"""Ежедневный прогон мониторинга снега: данные → снимки → индекс → ИИ-комментарий → страницы → деплой.

Каждый шаг изолирован: ошибка одного не останавливает остальные (кроме сборки страниц),
результат каждого шага и время пишутся в data/runs.json — страница показывает их как
«последнее обновление». Запуск по расписанию — launchd (scripts/osadki_daily.sh), руками:
  analysis/.venv/bin/python analysis/osadki/run_daily.py [--no-deploy] [--no-agent] [--only step,step]
"""
import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / "analysis/.venv/bin/python"
OS = ROOT / "analysis/osadki"
DATA = OS / "data"
STEPS = [
    ("fetch_model", [sys.executable, OS / "fetch_model.py"], "погода по высотам (Open-Meteo / ECMWF)"),
    ("fetch_stations", [sys.executable, OS / "fetch_stations.py", "--days", "7"], "метеостанции Каракуль и Сары-Таш (OGIMET)"),
    ("render_s2", [PY, OS / "render_s2.py"], "новые снимки Sentinel-2"),
    ("render_viirs", [sys.executable, OS / "render_viirs.py"], "ежедневные снимки VIIRS"),
    ("render_s1", [PY, OS / "render_s1.py"], "радар Sentinel-1"),
    ("snow_index", [PY, OS / "snow_index.py"], "индекс снега по Sentinel-2"),
    ("pack_scenes", [sys.executable, OS / "pack_scenes.py"], "упаковка панелей в спрайты (лимит файлов Pages)"),
    ("timeline", [sys.executable, OS / "build_report.py", "--timeline-only"], "лента данных"),
    ("agent", [PY, OS / "agent_daily.py", "--auto"], "оценка ИИ-агента (Claude Fable 5)"),
    ("build_report", [sys.executable, OS / "build_report.py"], "страница «Снег и погода»"),
    ("build_sverka", [sys.executable, OS / "build_sverka.py"], "страница сверки"),
    ("build_dist", [sys.executable, ROOT / "analysis/viewer/build_dist.py"], "сборка сайта"),
    ("deploy", ["bash", ROOT / "scripts/deploy_private.sh"], "публикация на сайте"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-deploy", action="store_true")
    ap.add_argument("--no-agent", action="store_true")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else None
    runs_path = DATA / "runs.json"
    runs = json.load(open(runs_path)) if runs_path.exists() else []
    run = {"started": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "steps": {}, "ok": True}
    log = open(DATA / "runs.log", "a")
    for name, cmd, label in STEPS:
        if only and name not in only:
            continue
        if (name == "deploy" and a.no_deploy) or (name == "agent" and a.no_agent):
            run["steps"][name] = {"skipped": True, "label": label}; continue
        t0 = time.time()
        try:
            r = subprocess.run([str(c) for c in cmd], cwd=ROOT, capture_output=True, text=True, timeout=3600)
            ok = r.returncode == 0
            tail = (r.stdout.strip().splitlines() or [""])[-1][:200]
            err = (r.stderr.strip().splitlines() or [""])[-1][:300] if not ok else ""
        except subprocess.TimeoutExpired:
            ok, tail, err = False, "", "таймаут 60 мин"
        secs = round(time.time() - t0)
        run["steps"][name] = {"ok": ok, "secs": secs, "label": label, "tail": tail, "err": err}
        log.write(f"{run['started']} {name} {'ok' if ok else 'FAIL'} {secs}s {tail} {err}\n"); log.flush()
        print(f"{name:14s} {'ok ' if ok else 'FAIL'} {secs:4d}s  {tail}  {err}", flush=True)
        if not ok and name in ("fetch_model", "snow_index", "timeline", "build_report", "build_dist"):
            run["ok"] = False
        if not ok and name in ("build_dist",):
            break
        # runs.json обновляем после каждого шага, чтобы страница увидела состояние даже при падении
        run["finished"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        json.dump((runs + [run])[-60:], open(runs_path, "w"), ensure_ascii=False, indent=1)
    run["finished"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    json.dump((runs + [run])[-60:], open(runs_path, "w"), ensure_ascii=False, indent=1)
    print("готово:", "ok" if run["ok"] else "с ошибками")


if __name__ == "__main__":
    main()
