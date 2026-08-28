#!/usr/bin/env bash
# Обёртка для launchd: ежедневный прогон мониторинга снега (analysis/osadki/run_daily.py).
# Расписание — ~/Library/LaunchAgents/com.alp-finder.osadki.plist (08:00 и 18:00 по Бишкеку,
# т.е. 05:00 и 15:00 по часам этого Mac при часовом поясе UTC+3). Логи — analysis/osadki/data/launchd.log.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export LANG=ru_RU.UTF-8 LC_ALL=ru_RU.UTF-8
cd "$ROOT"
echo "=== $(date -u +%FT%TZ) старт ($*)" >> analysis/osadki/data/launchd.log
"$ROOT/analysis/.venv/bin/python" analysis/osadki/run_daily.py "$@" >> analysis/osadki/data/launchd.log 2>&1
echo "=== $(date -u +%FT%TZ) конец, код $?" >> analysis/osadki/data/launchd.log
