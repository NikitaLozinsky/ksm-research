#!/usr/bin/env bash
# =============================================================================
# stage2-ksmd-pin.sh — этап 2, шаг 5: механизм хвоста задержки.
#
# Гипотеза этапа 1: самые долгие тики — это COW, совпавшие с работой ksmd.
# Вопрос: из-за чего — конкуренции за ЦП или за блокировки (mm, таблицы
# страниц)? Опыт: модель тик-сервера этапа 1 (timer_jitter 60 Гц, состояние
# STATE_MIB, TOUCH страниц за тик, KSM вкл., pages_to_scan=1000, трасса вкл.)
# при трёх вариантах привязки к ЦП (vCPU 0..NCPU-1):
#   unpinned  без привязок (как в репетиции этапа 1);
#   split     ksmd — на последнем vCPU, тик — на остальных: за ЦП они
#             не конкурируют;
#   shared    ksmd и тик — на одном последнем vCPU: конкуренция за ЦП
#             максимальна;
#   sibling   (только при SMT, этап 3) ksmd — на последнем логическом ЦП,
#             тик — на его брате по SMT: разные логические ЦП, но одно
#             физическое ядро (общие L1/L2 и блоки исполнения).
# При SMT (натив, этап 3) split исключает и братьев ksmd по SMT: тик идёт
# только на других физических ядрах. Без SMT (ВМ) всё как на этапе 2.
# Ожидание: если хвост — конкуренция за ЦП, в split он исчезает, в shared
# растёт; если хвост остаётся и в split — дело в блокировках.
# Привязка ksmd возвращается к исходной при любом выходе (safety_init:
# исходная маска ksmd сохраняется вместе с настройками KSM).
# Параметры: REPS (10), DURATION (90), STATE_MIB (256), TOUCH (256), SERIES.
# В каждом прогоне снимаются строки TLB (межпроцессорные сбросы TLB) и CAL
# (вызовы функций на других ЦП) из /proc/interrupts до и после — прямая
# проверка гипотезы «хвост — из-за межпроцессорных сбросов TLB при
# одновременной работе ksmd» (добавлено после основной серии этапа 2).
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"
source "$(dirname "$0")/lib/ksm_trace.sh"
safety_init
REPS="${REPS:-10}"; DURATION="${DURATION:-90}"; STATE_MIB="${STATE_MIB:-256}"; TOUCH="${TOUCH:-256}"
NCPU=$(nproc); LAST=$((NCPU - 1)); REST="0-$((NCPU - 2))"
# Братья последнего ЦП по SMT (кроме него самого), например "3" для CPU 7.
SIBS=$(python3 -I - "$LAST" <<'PY'
import sys
last = int(sys.argv[1])
txt = open(f"/sys/devices/system/cpu/cpu{last}/topology/thread_siblings_list").read().strip()
cpus = set()
for part in txt.split(","):
    a, _, b = part.partition("-")
    cpus.update(range(int(a), int(b or a) + 1))
cpus.discard(last)
print(",".join(map(str, sorted(cpus))))
PY
)
if [[ -n "$SIBS" ]]; then
    # split: все ЦП, кроме последнего и его братьев по SMT (другие физические ядра).
    REST=$(seq 0 "$LAST" | grep -vxF -e "$LAST" $(printf -- '-e %s ' ${SIBS//,/ }) | paste -sd,)
    DEFAULT_CONFIGS="unpinned split shared sibling"
else
    DEFAULT_CONFIGS="unpinned split shared"
fi
read -r -a CONFIGS <<< "${CONFIGS:-$DEFAULT_CONFIGS}"
echo "ЦП: $NCPU, ksmd при привязке — на $LAST, братья по SMT: ${SIBS:-нет}, split для тика: $REST"
J="$REPO/workloads/jitter"; mkdir -p "$J/bin"
gcc -O2 -Wall -Wextra -o "$J/bin/timer_jitter" "$J/timer_jitter.c"
BIN="$REPO/workloads/ksm-probe/bin"; "$REPO/workloads/ksm-probe/build.sh" > /dev/null
OUT="$(new_series_dir "${SERIES:-stage2-ksmd-pin}")"
echo "Каталог серии: $OUT"

for rep in $(seq -w 1 "$REPS"); do
    k=$(( (10#$rep - 1) % ${#CONFIGS[@]} ))
    order=("${CONFIGS[@]:k}" "${CONFIGS[@]:0:k}")
    for cfg in "${order[@]}"; do
        base="$OUT/$cfg-rep$rep"
        reset_state > /dev/null
        ksm_set pages_to_scan 1000
        case "$cfg" in
            unpinned) ksmd_set_affinity "0-$LAST"; app_cpus="0-$LAST" ;;
            split)    ksmd_set_affinity "$LAST";   app_cpus="$REST" ;;
            shared)   ksmd_set_affinity "$LAST";   app_cpus="$LAST" ;;
            sibling)  [[ -n "$SIBS" ]] || { echo "sibling без SMT невозможен"; exit 1; }
                      ksmd_set_affinity "$LAST";   app_cpus="$SIBS" ;;
        esac
        write_params "$base.json" experiment=stage2-ksmd-pin config="$cfg" rep="$rep" duration_s="$DURATION" \
            state_mib="$STATE_MIB" touch_per_tick="$TOUCH" ksmd_cpus="$(ksmd_get_affinity)" app_cpus="$app_cpus" \
            smt_siblings_of_ksmd_cpu="${SIBS:-none}" order="${order[*]}"
        trace_start "$base.perf.data"
        python3 -I "$REPO/scripts/lib/vmstat_fast.py" --out "$base.vmstat.csv" --interval 0.01 --duration "$((DURATION + 5))" &
        vm=$!
        python3 -I "$REPO/scripts/lib/ksm_sampler.py" --out "$base.ksm.csv" --interval 1 &
        smp=$!
        grep -E '^ *(TLB|CAL):' /proc/interrupts | sed 's/^/before /' > "$base.irq"
        sysw "$KSM/run" 1
        KSM_LAUNCH_QUIET=1 taskset -c "$app_cpus" "$BIN/ksm_launch" "$J/bin/timer_jitter" \
            --period-us 16667 --duration "$DURATION" --out "$base.csv" \
            --state-mib "$STATE_MIB" --touch-per-tick "$TOUCH"
        grep -E '^ *(TLB|CAL):' /proc/interrupts | sed 's/^/after /' >> "$base.irq"
        sysw "$KSM/run" 2
        kill -TERM "$smp"; wait "$smp" || true
        kill -TERM "$vm" 2>/dev/null || true; wait "$vm" 2>/dev/null || true
        trace_stop
        trace_export "$base.perf.data" "$base.perf-script.txt.gz"
        echo "rep$rep $cfg готово (ksmd: $(ksmd_get_affinity), тик: $app_cpus)"
    done
done
echo "Серия завершена: $OUT"
