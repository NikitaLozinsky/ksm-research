#!/usr/bin/env bash
# =============================================================================
# stage1-latency-rehearsal.sh — этап 1, шаг 7.4: РЕПЕТИЦИЯ замера задержек с
# атрибуцией к событиям KSM. Цель — отладить пайплайн (тик-программа →
# трасса perf → cow_ksm → attribution.py) на ВМ. Числа задержек на ВМ —
# только для сравнения внутри серии (docs/limitations.md, Л4).
#
# Нагрузка: timer_jitter как модель тик-сервера 60 Гц с «состоянием»
# STATE_MIB МиБ (повторяющиеся страницы); на каждом тике обновляет TOUCH
# страниц по кругу тем же байтом (устойчивый цикл «KSM слил → запись → COW»).
# Запуск через ksm_launch: KSM_LAUNCH_MERGE=1 (KSM вкл.) или 0 (выкл.) —
# дерево процессов одинаковое, отличается только флаг (Р11).
# ksmd в обоих случаях запущен (run=1, pages_to_scan=1000): в «выкл.» ему
# просто нечего сканировать.
#
# Конфигурации: ksm{on,off} × trace{on,off}. Трассировка (perf ksm:* +
# sched_switch ksmd, cow_ksm раз в 10 мс) — фактор, чтобы проверить, не
# вносит ли она задержку сама.
# Параметры: REPS (10), DURATION (90), PERIOD_US (16667), STATE_MIB (256),
# TOUCH (256).
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"
source "$(dirname "$0")/lib/ksm_trace.sh"
safety_init

REPS="${REPS:-10}"; DURATION="${DURATION:-90}"; PERIOD_US="${PERIOD_US:-16667}"
STATE_MIB="${STATE_MIB:-256}"; TOUCH="${TOUCH:-256}"
read -r -a CONFIGS <<< "${CONFIGS:-ksmon-traceon ksmon-traceoff ksmoff-traceon ksmoff-traceoff}"
J="$REPO/workloads/jitter"; mkdir -p "$J/bin"
gcc -O2 -Wall -Wextra -o "$J/bin/timer_jitter" "$J/timer_jitter.c"
BIN="$REPO/workloads/ksm-probe/bin"
"$REPO/workloads/ksm-probe/build.sh" > /dev/null
OUT="$(new_series_dir stage1-latency-rehearsal)"
echo "Каталог серии: $OUT"

for rep in $(seq -w 1 "$REPS"); do
    k=$(( (10#$rep - 1) % ${#CONFIGS[@]} ))
    order=("${CONFIGS[@]:k}" "${CONFIGS[@]:0:k}")
    for cfg in "${order[@]}"; do
        base="$OUT/$cfg-rep$rep"
        reset_state > /dev/null
        ksm_set pages_to_scan 1000
        merge=0; [[ "$cfg" == ksmon-* ]] && merge=1
        write_params "$base.json" experiment=stage1-latency-rehearsal config="$cfg" rep="$rep" \
            duration_s="$DURATION" period_us="$PERIOD_US" state_mib="$STATE_MIB" \
            touch_per_tick="$TOUCH" ksm_launch_merge="$merge" order="${order[*]}"
        if [[ "$cfg" == *-traceon ]]; then
            trace_start "$base.perf.data"
            python3 -I "$REPO/scripts/lib/vmstat_fast.py" --out "$base.vmstat.csv" \
                --interval 0.01 --duration "$((DURATION + 5))" &
            vm=$!
        fi
        python3 -I "$REPO/scripts/lib/ksm_sampler.py" --out "$base.ksm.csv" --interval 1 &
        smp=$!
        sysw "$KSM/run" 1
        KSM_LAUNCH_QUIET=1 KSM_LAUNCH_MERGE="$merge" "$BIN/ksm_launch" "$J/bin/timer_jitter" \
            --period-us "$PERIOD_US" --duration "$DURATION" --out "$base.csv" \
            --state-mib "$STATE_MIB" --touch-per-tick "$TOUCH"
        sysw "$KSM/run" 2
        kill -TERM "$smp"; wait "$smp" || true
        if [[ "$cfg" == *-traceon ]]; then
            kill -TERM "$vm" 2>/dev/null || true; wait "$vm" 2>/dev/null || true
            trace_stop
            trace_export "$base.perf.data" "$base.perf-script.txt.gz"
        fi
        echo "rep$rep $cfg готово"
    done
done
echo "Серия завершена: $OUT"
