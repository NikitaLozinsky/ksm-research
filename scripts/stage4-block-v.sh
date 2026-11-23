#!/usr/bin/env bash
# Блок В: один измеряемый сервер и семь однотипных процессов для слияния страниц.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
export SERIES=stage4-ticks-block-v WORKLOADS=ticksrv REPS=10 REP_START=${REP_V_START:-1}
export TICK_CALIBRATE=0 TICK_REQUIRE_PERFORMANCE=1 N=8 SEED=20261123
export WARM_S=10 PRE_S=15 MERGE_S=45 STEADY_S=65 STABILIZE_SEC=25
export TICK_CPU_WORK=2400000 TICK_WRITES=128
exec bash "$REPO/scripts/stage4-ticks.sh"
