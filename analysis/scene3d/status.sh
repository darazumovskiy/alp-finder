#!/usr/bin/env bash
# Прогресс 3D-сцен, кратко. Запуск: bash analysis/scene3d/status.sh
Q="/private/tmp/claude-557897862/-Users-d-razumovskiy-work-alp-finder/d32ac67f-04ce-4772-8b00-c469f0f23f44/scratchpad/queue.log"
MAN="/Users/d.razumovskiy/work/alp-finder/analysis/viewer/scenes/manifest.json"

total=$(grep -m1 "зон в очереди" "$Q" 2>/dev/null | grep -o "[0-9]*")
cur=$(grep -E "^=== зона" "$Q" 2>/dev/null | tail -1 | sed -E 's|^=== зона ([0-9]+/[0-9]+).*|\1|')
done_n=$(grep -c "ГОТОВА и задеплоена" "$Q" 2>/dev/null)
skip_n=$(grep -cE "пропущена|— пропуск|ОШИБКА" "$Q" 2>/dev/null)

phase="между этапами"
pgrep -f extract_zone  >/dev/null && phase="выборка кадров"
pgrep -f sfm_zone      >/dev/null && phase="позы камер (SfM, самый долгий этап)"
pgrep -f brush_app     >/dev/null && phase="тренировка (GPU)"
pgrep -f deploy_private>/dev/null && phase="деплой на сайт"
pgrep -f queue_zones   >/dev/null || phase="ОЧЕРЕДЬ ЗАВЕРШЕНА ИЛИ УПАЛА"

echo "Зоны линии падения: ${cur:-—} — сейчас: $phase"
echo "  готово и на сайте: ${done_n:-0}  ·  пропущено/сбой: ${skip_n:-0}  ·  всего: ${total:-?}"
[ -f "$MAN" ] && python3 -c "
import json
m = json.load(open('$MAN'))
print('Сцены на сайте:', ', '.join(s['id'] for s in m['scenes']))"
