#!/usr/bin/env bash
# Плотный этап всех зон на CUDA-машине (образ colmap/colmap:latest).
#
# Использование: bash run_dense_all.sh /workspace/zones [число_GPU]
#   zones/<зона>/dense — рабочие пространства из prep_dense.py.
# Живая очередь: освободившаяся карта забирает следующую зону списка
# (клейм — атомарный mkdir), зона целиком на одной карте. Идемпотентно:
# зона с готовым fused.ply пропускается, ошибка зоны не валит очередь,
# несвежие клеймы снимаются на старте. Карты глубины зоны удаляются после
# успешного слияния — без этого диск съедают ~60 ГБ промежуточных карт.
# tmux в образе нет — запускать через:
#   nohup bash run_dense_all.sh /workspace/zones 4 > /workspace/job.log 2>&1 &
set -u
BASE="${1:?путь к каталогу зон}"
NGPU="${2:-1}"

ORDER=(koshki-1608 veshchi-15 stena-peak gora-obzor
       linia-01 linia-05 linia-06 linia-07 linia-08 linia-09
       linia-10 linia-11 linia-12 linia-13)

rm -rf "$BASE"/.claim-*

run_zone() {
  local z="$1" gpu="$2" d="$BASE/$1/dense"
  echo "[$z@gpu$gpu] patch_match_stereo… ($(date +%H:%M))"
  colmap patch_match_stereo --workspace_path "$d" --workspace_format COLMAP \
    --PatchMatchStereo.geom_consistency true \
    --PatchMatchStereo.max_image_size 1600 \
    --PatchMatchStereo.gpu_index "$gpu" \
    || { echo "[$z@gpu$gpu] ОШИБКА patch_match"; return; }
  echo "[$z@gpu$gpu] stereo_fusion…"
  colmap stereo_fusion --workspace_path "$d" --workspace_format COLMAP \
    --input_type geometric --output_path "$d/fused.ply" \
    || { echo "[$z@gpu$gpu] ОШИБКА fusion"; return; }
  rm -rf "$d/stereo/depth_maps" "$d/stereo/normal_maps"
  echo "[$z@gpu$gpu] ГОТОВО: $(du -h "$d/fused.ply" | cut -f1)"
}

worker() {
  local gpu="$1" z d
  for z in "${ORDER[@]}"; do
    d="$BASE/$z/dense"
    [ -d "$d" ] || continue
    [ -f "$d/fused.ply" ] && continue
    mkdir "$BASE/.claim-$z" 2>/dev/null || continue   # зону забрала другая карта
    run_zone "$z" "$gpu"
  done
}

for ((g=0; g<NGPU; g++)); do
  worker "$g" &
done
wait
echo "ОЧЕРЕДЬ ЗАВЕРШЕНА"
