#!/usr/bin/env bash
# =============================================================================
# stage2-rerun.sh — этап 2, шаг 1: повтор конфигураций этапа 1, у которых
# критерий окончания срабатывал раньше схождения (decisions Р17): Python N=4 и
# N=8 и для контроля JVM N=8, KSM по умолчанию, 10 повторов, с исправленным
# критерием (>= 3 полных прохода + рост <= max(1 %, 256 страниц) за 60 с).
# MAX_S=900: при скорости по умолчанию полный проход 1.2 ГБ Python занимает
# ~100 с, трёх проходов и минуты стабильности должно хватить с запасом.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
SERIES=stage2-rerun WORKLOADS=python NS="4 8" KSM_CFGS=default REPS=10 MAX_S=900 \
    scripts/stage1-workloads.sh
SERIES=stage2-rerun-jvm WORKLOADS=jvm NS=8 KSM_CFGS=default REPS=10 MAX_S=900 \
    scripts/stage1-workloads.sh
