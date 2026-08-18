#!/usr/bin/env bash
# Статус конвейера сцен 3D: что крутится и где какой прогресс.
# Запуск:  bash analysis/scene3d/status.sh      (или watch -n 30 bash ...)
D="$(cd "$(dirname "$0")" && pwd)/data"
LOGS=(/private/tmp/claude-*/-Users-d-razumovskiy-work-alp-finder/*/scratchpad)

alive() { pgrep -f "$1" >/dev/null && echo "РАБОТАЕТ" || echo "—"; }

echo "== процессы =="
printf "  %-28s %s\n" "SfM зоны (sfm_zone)"        "$(alive sfm_zone)"
printf "  %-28s %s\n" "регистрация (register_)"    "$(alive register_koshki)"
printf "  %-28s %s\n" "тренировка (brush_app)"     "$(alive brush_app)"

echo "== зона stena-peak =="
if [ -f "$D/stena-peak/database.db" ]; then
  sz=$(du -h "$D/stena-peak/database.db" | cut -f1)
  echo "  кадров: $(ls "$D/stena-peak/images" 2>/dev/null | wc -l | tr -d ' '), база матчей: $sz (растёт = матчинг идёт)"
fi
for m in "$D"/stena-peak/sparse/*/; do
  [ -d "$m" ] && echo "  модель $(basename "$m"): готова"
done
last=$(grep -h "num_reg_frames" "${LOGS[@]}"/zone_sfm.log 2>/dev/null | tail -1 | grep -o "num_reg_frames=[0-9]*")
[ -n "$last" ] && echo "  сборка: $last камер в текущей модели"

echo "== сцена 16.08 =="
last=$(grep -h "num_reg_frames" "${LOGS[@]}"/reg.log 2>/dev/null | tail -1 | grep -o "num_reg_frames=[0-9]*")
[ -n "$last" ] && echo "  дорегистрация макро-кадров: $last камер (старт был 56)"
for v in "$D"/koshki-1608/splats-v*/; do
  ply=$(ls -t "$v"*.ply 2>/dev/null | head -1)
  ev=$(ls -dt "$v"eval_* 2>/dev/null | head -1)
  [ -n "$ply$ev" ] && echo "  $(basename "$v"): $(basename "$ply" 2>/dev/null) ${ev:+eval:$(basename "$ev")}"
done
