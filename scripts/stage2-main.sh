#!/usr/bin/env bash
# =============================================================================
# stage2-main.sh — длинные серии этапа 2 одной цепочкой (план подтверждён 2026-10-18):
#   4. основная серия: 5 нагрузок × KSM вкл./выкл., N=8, настройка fast, 10 повторов;
#   5a. механизм хвоста на модели тика: ksmd unpinned / split / shared, 10 повторов;
#   5b. механизм хвоста на Python под нагрузкой: KSM вкл., привязка none / split, 10 повторов.
# Частоты на контейнер выбраны пилотом (docs/journal.md, 2026-10-18 10:52 и 11:00):
#   nginx 400, Python 50, Node.js 200, JVM 400, PostgreSQL 200 запросов/с.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
export RATE_NGINX=400 RATE_PYTHON=50 RATE_NODE=200 RATE_JVM=400 RATE_POSTGRES=200
scripts/preflight.sh 2000 | tail -1
echo "### $(date '+%F %T') 4: основная серия"
SERIES=stage2-load MODES="on off" PINS=none REPS=10 scripts/stage2-load.sh
echo "### $(date '+%F %T') 5a: привязка ksmd, модель тика"
REPS=10 scripts/stage2-ksmd-pin.sh
echo "### $(date '+%F %T') 5b: привязка ksmd, Python под нагрузкой"
SERIES=stage2-load-pin WORKLOADS=python MODES=on PINS="none split" REPS=10 scripts/stage2-load.sh
echo "### $(date '+%F %T') все серии этапа 2 завершены"
