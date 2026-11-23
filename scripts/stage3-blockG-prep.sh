#!/usr/bin/env bash
# =============================================================================
# stage3-blockG-prep.sh — подготовительная часть блока Г этапа 3 (без выводов):
#   1) preflight (FAIL → стоп);
#   2) loadgen-check — генератор нагрузки на эталонном сервере (PASS/FAIL);
#   3) дымовой прогон прогрева JVM под нагрузкой (WARM_LOAD_S, доработка шага 1):
#      jvm, KSM вкл., 1 повтор, короткие окна — проверка, что фаза прогрева
#      проходит и её журналы пишутся отдельно (warmup-lat-c*.csv.gz);
#   4) пилот частот для nginx, Python, JVM со ступенями ВЫШЕ, чем на ВМ
#      (железо быстрее: на ВМ насыщение было на 800/400/800 запросов/с).
# Частоты основной серии выбирает агент по сводке пилота (docs/decisions.md, Р23),
# поэтому основная серия здесь НЕ запускается.
#
# Запуск отдельной службой (не из tmux агента, см. журнал 2026-11-18 03:38):
#   systemd-run --user --unit=ksm-g-prep --collect --setenv=PATH="$PATH" \
#       bash ~/ksm-research/scripts/stage3-blockG-prep.sh
# Ход — results/logs/blockG-prep.status, вывод — results/logs/blockG-prep.log,
# код — results/logs/blockG-prep.exit.
# =============================================================================
set -uo pipefail
REPO="$HOME/ksm-research"; LOGS="$REPO/results/logs"
STATUS="$LOGS/blockG-prep.status"; LOG="$LOGS/blockG-prep.log"
export MIN_AVAIL_MIB=2000
mkdir -p "$LOGS"; cd "$REPO" || exit 1
note() { echo "$(date '+%F %T') $*" | tee -a "$STATUS"; }

# Шаг: имя, команда. Ошибка шага — стоп цепочки с кодом шага.
step() {
    local name="$1"; shift
    note "старт: $name"
    echo "### $(date '+%F %T') $name: $*" >> "$LOG"
    "$@" >> "$LOG" 2>&1; local rc=$?
    note "конец: $name, код $rc"
    if (( rc != 0 )); then echo "$rc" > "$LOGS/blockG-prep.exit"; exit "$rc"; fi
}

step capture-env scripts/capture-env.sh
step preflight   scripts/preflight.sh
step loadgen-check scripts/stage2-loadgen-check.sh
step smoke-jvm-warm env WORKLOADS=jvm MODES=on REPS=1 N=8 WARM_LOAD_S=30 \
    PRE_S=10 MERGE_S=20 STEADY_S=20 RATE_JVM=400 SERIES=stage2-load-smoke \
    scripts/stage2-load.sh
step load-pilot env WORKLOADS="nginx python jvm" \
    RATES_nginx="100 200 400 800 1600 3200" \
    RATES_python="25 50 100 200 400 800" \
    RATES_jvm="100 200 400 800 1600 3200" \
    scripts/stage2-load-pilot.sh
echo 0 > "$LOGS/blockG-prep.exit"
note "подготовка блока Г завершена"
