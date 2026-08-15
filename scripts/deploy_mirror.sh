#!/usr/bin/env bash
# Публикация analysis/viewer/dist в ветку gh-pages — зеркало просмотрщика
# на GitHub Pages: https://darazumovskiy.github.io/alp-finder/
# Нужно потому, что *.pages.dev заблокирован провайдерами в РФ и РБ.
# Перед запуском собрать dist: python analysis/viewer/build_dist.py
#
# Пуш инкрементальный: клонируем gh-pages без содержимого файлов
# (--filter=blob:none), накладываем dist поверх пустого рабочего дерева и
# пушим только изменённые файлы — полный dist (~0.5 ГБ) одним паком
# соединение с GitHub не переживает.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/analysis/viewer/dist"
[ -f "$DIST/index.html" ] || {
  echo "нет $DIST/index.html — сначала python analysis/viewer/build_dist.py" >&2
  exit 1
}

URL="$(git -C "$ROOT" remote get-url origin)"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

git clone -q --filter=blob:none --no-checkout --depth 1 --branch gh-pages "$URL" "$TMP"
git -C "$TMP" read-tree HEAD
cp -R "$DIST/." "$TMP/"

GITC() { git -C "$TMP" \
  -c user.name="$(git -C "$ROOT" config user.name)" \
  -c user.email="$(git -C "$ROOT" config user.email)" "$@"; }

# рабочее дерево = ровно dist, поэтому add -A даёт точное зеркало:
# файлы, исчезнувшие из dist, станут удалениями. Пушим порциями по ~200 МБ —
# пак больше ~0.5 ГБ соединение с GitHub не переживает.
BATCH_BYTES=$((200 * 1024 * 1024))
batch=() ; size=0 ; n=0
flush() {
  [ ${#batch[@]} -eq 0 ] && return 0
  GITC add -- "${batch[@]}"
  if ! GITC diff --cached --quiet; then
    n=$((n + 1))
    GITC commit -q -m "Зеркало просмотрщика: порция $n"
    GITC push -q "$URL" HEAD:gh-pages
    echo "  порция $n отправлена (${#batch[@]} файлов)"
  fi
  batch=() ; size=0
}
while IFS= read -r -d '' f; do
  batch+=("$f")
  # удалённые файлы попадают в ls-files -m, но на диске их нет — размер 0
  size=$((size + $(stat -f%z "$TMP/$f" 2>/dev/null || stat -c%s "$TMP/$f" 2>/dev/null || echo 0)))
  if [ "$size" -ge "$BATCH_BYTES" ]; then flush; fi
done < <(git -C "$TMP" ls-files -o -m --exclude-standard -z)
flush

# финальный проход: удаления и всё, что не попало в порции
GITC add -A
if ! GITC diff --cached --quiet; then
  GITC commit -q -m "Зеркало просмотрщика (GitHub Pages)"
  GITC push -q "$URL" HEAD:gh-pages
elif [ "$n" -eq 0 ]; then
  echo "gh-pages уже актуальна, пуш не нужен"
  exit 0
fi
echo "готово: https://darazumovskiy.github.io/alp-finder/"
