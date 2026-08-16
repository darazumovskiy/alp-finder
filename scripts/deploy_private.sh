#!/usr/bin/env bash
# Деплой закрытой тим-версии просмотрщика: Cloudflare Pages alp-finder-team
# под Basic Auth. Пароль, URL и код воркера — docs/zakrytaya-zona.local.md.
# В режиме приватности (см. CLAUDE.local.md) деплоим ТОЛЬКО этим скриптом.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/analysis/viewer/dist"
WORKER="$ROOT/analysis/viewer/_worker.js"   # код воркера (Basic Auth)

[ -f "$DIST/index.html" ] || {
  echo "нет $DIST/index.html — сначала python analysis/viewer/build_dist.py" >&2
  exit 1
}
[ -f "$WORKER" ] || {
  echo "нет $WORKER — код воркера в docs/zakrytaya-zona.local.md" >&2
  exit 1
}

# воркер кладётся в dist только на время деплоя, иначе паролем закроется
# и публичный проект, который собирается из того же dist
cp "$WORKER" "$DIST/_worker.js"
trap 'rm -f "$DIST/_worker.js"' EXIT

npx wrangler pages deploy "$DIST" --project-name alp-finder-team
echo "готово: https://alp-finder-team.pages.dev"
