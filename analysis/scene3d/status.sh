#!/usr/bin/env bash
# Статус конвейера сцен 3D с процентами. Запуск: bash analysis/scene3d/status.sh
D="$(cd "$(dirname "$0")" && pwd)/data"
SCRATCH=$(ls -d /private/tmp/claude-*/-Users-d-razumovskiy-work-alp-finder/*/scratchpad 2>/dev/null | head -1)

alive() { pgrep -f "$1" >/dev/null && echo "РАБОТАЕТ" || echo "—"; }

# прогресс exhaustive-матчинга из лога: "Processing block [i/A, j/B]"
match_pct() {
  local line
  line=$(grep -h "Processing block" "$1" 2>/dev/null | tail -1)
  [ -z "$line" ] && return
  echo "$line" | sed -E 's/.*\[([0-9]+)\/([0-9]+), ([0-9]+)\/([0-9]+)\].*/\1 \2 \3 \4/' | \
    awk '{printf "%d%% (блок %d/%d, %d/%d)", (($1-1)*$4+$3)*100/($2*$4), $1, $2, $3, $4}'
}
reg_last() { grep -h "num_reg_frames" "$1" 2>/dev/null | tail -1 | grep -o "num_reg_frames=[0-9]*" | cut -d= -f2; }

echo "== процессы =="
printf "  %-26s %s\n" "SfM зоны (sfm_zone)"     "$(alive sfm_zone)"
printf "  %-26s %s\n" "регистрация (register_)" "$(alive register_koshki)"
printf "  %-26s %s\n" "тренировка (brush_app)"  "$(alive brush_app)"

echo "== зона stena-peak (661 кадр) =="
if grep -q "== mapping ==" "$SCRATCH/zone_sfm.log" 2>/dev/null; then
  k=$(reg_last "$SCRATCH/zone_sfm.log")
  echo "  сборка модели: ${k:-0} камер из 661 ($(( ${k:-0} * 100 / 661 ))%)"
else
  mp=$(match_pct "$SCRATCH/zone_sfm.log")
  [ -n "$mp" ] && echo "  матчинг: $mp" || echo "  SIFT (быстрая фаза)"
fi
for m in "$D"/stena-peak/sparse/*/; do
  [ -d "$m" ] && echo "  модель $(basename "$m"): ГОТОВА"
done

echo "== сцена 16.08 =="
k=$(reg_last "$SCRATCH/reg.log")
if [ -n "$k" ]; then
  echo "  дорегистрация макро-кадров: $k камер (старт 56, потолок 431 —"
  echo "    сядут не все: зависание цепляется частично, любое число >56 — плюс)"
fi
for v in "$D"/koshki-1608/splats-v*/; do
  ply=$(ls -t "$v"*.ply 2>/dev/null | head -1 | xargs -n1 basename 2>/dev/null)
  ev=$(ls -dt "$v"eval_* 2>/dev/null | head -1 | xargs -n1 basename 2>/dev/null)
  step=$(echo "${ev:-$ply}" | grep -o "[0-9]*" | tail -1)
  [ -n "$ply$ev" ] && echo "  $(basename "$v"): шаг ${step:-?}/30000 ${ply:+ply:$ply}"
done
echo "(проценты матчинга и камеры сборки — из логов текущей сессии)"
