#!/usr/bin/env bash
# Одна цепочка под series-guard и powerprofilesctl launch.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
child=
stop() {
    trap '' INT TERM
    if [[ -n "$child" ]]; then
        kill -TERM "$child" 2>/dev/null || true
        wait "$child" || true
    fi
    exit 143
}
trap stop INT TERM
step() {
    "$@" &
    child=$!
    local rc=0
    wait "$child" || rc=$?
    child=
    return "$rc"
}

# REP_START и REP_END относятся к оставшемуся диапазону Б.
step bash "$REPO/scripts/stage4-block-b.sh"
step bash "$REPO/scripts/stage4-block-v.sh"

source "$REPO/scripts/lib/tick-progress.sh"
progress_init ticks-block-v-report 1
trap 'rc=$?; if (( rc )); then progress -2 "Ошибка обработки · журнал службы ksm-s4-full"; fi' EXIT
progress 0 "Архивирование, анализ и графики"
step python3 "$REPO/scripts/stage4-finalize.py"
completed=1
progress -1 "Архивы, сводки и графики готовы"
