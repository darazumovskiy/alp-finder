#!/usr/bin/env bash
# Публикация analysis/viewer/dist в ветку gh-pages — зеркало просмотрщика
# на GitHub Pages: https://darazumovskiy.github.io/alp-finder/
# Нужно потому, что *.pages.dev заблокирован провайдерами в РФ и РБ.
# Перед запуском собрать dist: python analysis/viewer/build_dist.py
#
# Механика: постоянный клон gh-pages в analysis/viewer/.mirror (в .gitignore).
# rsync накладывает dist на клон, git перехеширует только изменённые файлы,
# по сети уходит только дельта. Если изменений больше ~100 МБ — они уходят
# порциями (обрыв соединения стоит одной порции), каждый пуш повторяется
# до 3 раз. В конце история ветки схлопывается в один коммит без родителей
# (force-push): зеркалу история не нужна, а с ней репозиторий на GitHub
# растёт с каждым деплоем (лимит 5 ГБ).
# Если .mirror сломался — удалить каталог, скрипт пересоздаст его сам
# (первый прогон после пересоздания перехеширует весь dist — это разовая цена).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/analysis/viewer/dist"
MIRROR="$ROOT/analysis/viewer/.mirror"
[ -f "$DIST/index.html" ] || {
  echo "нет $DIST/index.html — сначала python analysis/viewer/build_dist.py" >&2
  exit 1
}

# трекер размера: у GitHub Pages лимит опубликованного сайта 1 ГБ
DIST_MB=$(du -sm "$DIST" | cut -f1)
echo "dist: ${DIST_MB} МБ (лимит публикации GitHub Pages — 1024 МБ)"
if [ "$DIST_MB" -gt 1024 ]; then
  echo "ВНИМАНИЕ: dist больше лимита GitHub Pages — проверяй после деплоя, что зеркало обновилось" >&2
fi

# MIRROR_URL — переопределение адреса для тестов и деплоя в форк
URL="${MIRROR_URL:-$(git -C "$ROOT" remote get-url origin)}"

GITC() { git -C "$MIRROR" \
  -c user.name="$(git -C "$ROOT" config user.name)" \
  -c user.email="$(git -C "$ROOT" config user.email)" "$@"; }

# обрыв соединения с GitHub — штатная ситуация, повторяем пуш
push_retry() {
  local attempt
  for attempt in 1 2 3; do
    if GITC push -q -f "$URL" "$@"; then return 0; fi
    echo "  пуш не прошёл (попытка $attempt из 3), пауза 15 с" >&2
    sleep 15
  done
  echo "пуш не прошёл после 3 попыток" >&2
  return 1
}

if [ ! -d "$MIRROR/.git" ]; then
  echo "первичная инициализация $MIRROR (клон gh-pages без содержимого файлов)"
  git clone -q --filter=blob:none --no-checkout --depth 1 --branch gh-pages \
    "$URL" "$MIRROR"
  git -C "$MIRROR" read-tree HEAD
  # без этого скан 13 тыс. файлов (ls-files/add) занимает секунды вместо мгновений
  git -C "$MIRROR" config core.untrackedCache true
fi

rsync -a --delete --exclude=/.git "$DIST/" "$MIRROR/"

# изменения крупнее порога коммитим и пушим порциями; остаток и удаления
# уходят финальным коммитом
BATCH_BYTES=$((100 * 1024 * 1024))
batch=() ; size=0 ; n=0
flush() {
  [ ${#batch[@]} -eq 0 ] && return 0
  GITC add -- "${batch[@]}"
  if ! GITC diff --cached --quiet; then
    n=$((n + 1))
    GITC commit -q -m "Зеркало просмотрщика: порция $n"
    push_retry HEAD:gh-pages
    echo "  порция $n отправлена (${#batch[@]} файлов)"
  fi
  batch=() ; size=0
}
while IFS= read -r -d '' f; do
  batch+=("$f")
  # удалённые файлы попадают в ls-files -m, но на диске их нет — размер 0
  size=$((size + $(stat -f%z "$MIRROR/$f" 2>/dev/null || stat -c%s "$MIRROR/$f" 2>/dev/null || echo 0)))
  if [ "$size" -ge "$BATCH_BYTES" ]; then flush; fi
done < <(GITC ls-files -o -m --exclude-standard -z)

GITC add -A
TREE=$(GITC write-tree)
# нечего делать: дерево совпадает с веткой и история уже схлопнута
# (в свежем shallow-клоне родитель tip'а не виден — тогда схлопывание
# произойдёт при первом деплое с реальными изменениями)
if [ "$n" -eq 0 ] && [ "$TREE" = "$(GITC rev-parse 'HEAD^{tree}')" ] \
   && ! GITC rev-parse -q --verify HEAD^ >/dev/null 2>&1; then
  echo "gh-pages уже актуальна, пуш не нужен"
  exit 0
fi
COMMIT=$(GITC commit-tree -m "Зеркало просмотрщика (GitHub Pages)" "$TREE")
GITC update-ref refs/heads/gh-pages "$COMMIT"
push_retry gh-pages:gh-pages

# подчистить локальный клон: порционные коммиты и старые блобы недостижимы
GITC reflog expire --expire=now --all 2>/dev/null || true
GITC gc -q --auto 2>/dev/null || true
echo "готово: https://darazumovskiy.github.io/alp-finder/"
