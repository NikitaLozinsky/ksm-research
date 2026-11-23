#!/usr/bin/env bash
# Одна дневная часть серии Б. Номера повторов относятся ко всей серии.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
start=${REP_START:-1}
end=${REP_END:-$start}
[[ "$start" =~ ^([1-9]|1[0-5])$ && "$end" =~ ^([1-9]|1[0-5])$ && "$start" -le "$end" ]] || {
    echo 'REP_START и REP_END должны задавать диапазон в пределах 1–15' >&2
    exit 2
}
# Проверяем завершённые пары. Прерванный повтор можно выполнить целиком заново.
shopt -s nullglob
for ((rep = start; rep <= end; rep++)); do
    for chunk in "$REPO"/results/raw/stage4-ticks-block-b/*; do
        markers=("$chunk"/*-rep"$rep"/complete)
        if (( ${#markers[@]} == 6 )); then
            echo "Повтор $rep уже завершён в $chunk" >&2
            exit 2
        fi
    done
done
export SERIES=stage4-ticks-block-b REPS=$end REP_START=$start SEED=20261123
export WORKLOADS='ticksrv luanti godot' TICK_CALIBRATE=0 TICK_REQUIRE_PERFORMANCE=1 N=8
export WARM_S=10 PRE_S=30 MERGE_S=60 STEADY_S=90 STABILIZE_SEC=25
export TICK_CPU_WORK=2400000 TICK_WRITES=128 TICK_LUANTI_EDITS=24576 TICK_LUANTI_ENTITIES=64
export TICK_GODOT_PASSES=512 TICK_GODOT_BODIES=128
exec bash "$REPO/scripts/stage4-ticks.sh"
