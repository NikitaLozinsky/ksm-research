#!/usr/bin/env bash
# Проверка запуска и журнала по одному контейнеру без записи в настройки KSM.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
source "$REPO/scripts/lib/tick-progress.sh"
source "$REPO/scripts/lib/tick-workloads.sh"
progress_init ticks-check 3
scratch=$(mktemp -d)
id=
cleanup() {
    rc=$?
    if [[ -n "$id" ]]; then podman rm --force "$id" > /dev/null 2>&1 || true; fi
    python3 -c 'import shutil, sys; shutil.rmtree(sys.argv[1])' "$scratch"
    if (( rc )); then progress -2 "Ошибка · results/logs/ticks-check.log"; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
exec > "$REPO/results/logs/ticks-check.log" 2>&1

cd "$REPO"
git diff --check
bash -n workloads/ticksrv/entrypoint.sh
tick_build_binaries
for workload in ticksrv luanti godot; do
    progress "$(( (total - completed) * 6 ))" "$workload · запуск и проверка журнала"
    out="$scratch/$workload"
    id=$(tick_start "$workload" 0 "$out")
    tick_wait_ready "$workload" "$id" "$out"
    sleep 3
    podman stop --time 3 "$id" > /dev/null
    podman logs "$id"
    code=$(podman inspect --format '{{.State.ExitCode}}' "$id")
    [[ "$code" == 0 || "$code" == 143 ]] || { echo "$workload: код выхода $code" >&2; exit 1; }
    python3 - "$(tick_log_path "$workload" "$out")" "$workload" <<'PY'
import csv
import sys
from statistics import median

with open(sys.argv[1]) as f:
    rows = list(csv.DictReader(f))
assert len(rows) >= 30, len(rows)
starts = [int(row["start_ns"]) for row in rows]
assert all(int(row["tick"]) == index and int(row["end_ns"]) >= start
           for index, (row, start) in enumerate(zip(rows, starts), 1))
period = median(b-a for a, b in zip(starts, starts[1:]))
target = 1e9 / (20 if sys.argv[2] == "luanti" else 60)
assert target * 0.7 <= period <= target * 1.4, period
print(f"{sys.argv[2]}: {len(rows)} тиков, медиана интервала {period / 1e6:.3f} мс")
PY
    podman rm "$id" > /dev/null
    id=
    completed=$((completed + 1))
done
progress -1 "Все три проверки прошли"
