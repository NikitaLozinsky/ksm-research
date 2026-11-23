#!/usr/bin/env bash
# =============================================================================
# stage1-long-series.sh — длинные серии этапа 1 на реальных нагрузках в объёме,
# согласованном с владельцем 2026-10-17 («средний», ~10–11 ч):
#   7.2  все 5 нагрузок × N = 1, 4, 8, KSM по умолчанию, 10 повторов;
#   7.3  факторы KSM на python при N = 8, 10 повторов, предел наблюдения 600 с.
# Перед стартом ждёт окончания цепочки коротких серий (results/logs/stage1-short.exit)
# и запускается, только если она завершилась с кодом 0.
# =============================================================================
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
while [[ ! -f results/logs/stage1-short.exit ]]; do sleep 30; done
rc=$(cat results/logs/stage1-short.exit)
[[ "$rc" == 0 ]] || { echo "Короткие серии завершились с кодом $rc — длинные не запускаю" >&2; exit 1; }

echo "### $(date '+%F %T') 7.2: нагрузки × N"
SERIES=stage1-workloads NS="1 4 8" KSM_CFGS=default REPS=10 scripts/stage1-workloads.sh

echo "### $(date '+%F %T') 7.3: факторы KSM на python, N=8"
SERIES=stage1-workloads-factors WORKLOADS=python NS=8 \
    KSM_CFGS="default fast adv200 adv30 mps16 mps1024" REPS=10 MAX_S=600 \
    scripts/stage1-workloads.sh
echo "### $(date '+%F %T') длинные серии завершены"
