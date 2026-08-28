#!/usr/bin/env bash
# Деплой закрытой тим-версии просмотрщика: Cloudflare Worker «alp-finder-team» со статическими
# ассетами из analysis/viewer/dist под Basic Auth (_worker.js — в .gitignore, пароль и URL —
# docs/zakrytaya-zona.local.md). С 28.08.2026 вместо Pages: у Workers Paid лимит 100 000 файлов
# против 20 000 у Pages. Старый вариант — scripts/deploy_private_pages.sh.
# В режиме приватности (см. CLAUDE.local.md) деплоим ТОЛЬКО этим скриптом.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/analysis/viewer/dist"
WORKER="$ROOT/analysis/viewer/_worker.js"

[ -f "$DIST/index.html" ] || { echo "нет $DIST/index.html — сначала python analysis/viewer/build_dist.py" >&2; exit 1; }
[ -f "$WORKER" ] || { echo "нет $WORKER — код воркера в docs/zakrytaya-zona.local.md" >&2; exit 1; }
# воркер не должен лежать среди ассетов (он в main = _worker.js)
rm -f "$DIST/_worker.js"
N=$(find "$DIST" -type f | wc -l | tr -d ' ')
[ "$N" -lt 100000 ] || { echo "в dist $N файлов — больше лимита Workers Paid (100 000)" >&2; exit 1; }
echo "dist: $N файлов"

cd "$ROOT/analysis/viewer"
npx wrangler deploy -c wrangler.toml
echo "готово: см. URL воркера в docs/zakrytaya-zona.local.md"
