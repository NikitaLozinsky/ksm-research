#!/usr/bin/env bash
# =============================================================================
# stage1-trace-check.sh — этап 1, шаг 1: проверка, что источники событий KSM
# на этом ядре реально работают и дают метки времени, совместимые с нашими
# замерами задержки.
#
# Что проверяется:
#   1) tracepoint-ы ksm:* (список берётся из `perf list 'ksm:*'`), включая
#      ksm_advisor при advisor_mode=scan-time;
#   2) работа потока ksmd во времени: sched:sched_switch с фильтром по ksmd
#      (когда ksmd получает ЦП и когда отдаёт);
#   3) счётчики /proc/vmstat из ksm.rst: cow_ksm, ksm_swpin_copy.
#      Для COW у ядра 7.2.9 tracepoint-а нет (см. docs/decisions.md, Р12),
#      поэтому cow_ksm снимается частым опросом (scripts/lib/vmstat_fast.py).
#
# Сценарий: ksm_probe 64 МиБ (256 вариантов) + madvise; KSM с ускоренным
# сканированием и advisor_mode=scan-time; через TOUCH с ksm_probe пишет по
# байту в каждую страницу → ожидаем cow_ksm ≈ числу слитых страниц.
#
# perf пишет время по CLOCK_MONOTONIC (-k CLOCK_MONOTONIC) — те же часы, что
# clock_gettime(CLOCK_MONOTONIC) в timer_jitter и ksm_probe, поэтому события
# можно напрямую сопоставлять с тиками.
#
# Результат: results/raw/stage1-trace-check/<время>/ (perf script в .txt.gz,
# сэмплы vmstat в CSV, JSON ksm_probe, параметры в .json). perf.data удаляется:
# он двоичный и привязан к версии perf, а текст perf script самодостаточен.
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"
source "$(dirname "$0")/lib/ksm_trace.sh"
safety_init   # исходные настройки KSM вернутся при любом выходе (см. common.sh)

HOLD="${HOLD:-40}"
TOUCH="${TOUCH:-25}"
BIN="$REPO/workloads/ksm-probe/bin"
"$REPO/workloads/ksm-probe/build.sh" > /dev/null
OUT="$(new_series_dir stage1-trace-check)"
echo "Каталог: $OUT"

STABILIZE_SEC=3 reset_state > /dev/null
ksm_set advisor_mode scan-time
ksm_set advisor_target_scan_time 2      # цель: полный проход за 2 с
write_params "$OUT/params.json" experiment=stage1-trace-check hold_s="$HOLD" \
    touch_after_s="$TOUCH" mib=64 distinct=256 advisor_mode=scan-time advisor_target_scan_time=2
cow0=$(awk '/^cow_ksm /{print $2}' /proc/vmstat)

# Запись трассы (ksm:* и переключения ksmd); возврат только после ack от perf.
trace_start "$OUT/perf.data"
python3 -I "$REPO/scripts/lib/vmstat_fast.py" --out "$OUT/vmstat.csv" --interval 0.01 \
    --duration "$((HOLD + 4))" &
vm_pid=$!
sysw "$KSM/run" 1
"$BIN/ksm_probe" --mode madvise --mib 64 --distinct 256 --hold "$HOLD" \
    --touch-after "$TOUCH" > "$OUT/probe.jsonl"
sysw "$KSM/run" 2
wait "$vm_pid"
trace_stop
cow1=$(awk '/^cow_ksm /{print $2}' /proc/vmstat)
trace_export "$OUT/perf.data" "$OUT/perf-script.txt.gz"
echo "cow_ksm_delta=$((cow1 - cow0))" > "$OUT/vmstat-delta.txt"

echo "--- события по типам:"
zcat "$OUT/perf-script.txt.gz" | awk '{for(i=1;i<=NF;i++) if($i ~ /^(ksm|sched):/){sub(":$","",$i); print $i}}' \
    | sort | uniq -c
echo "--- cow_ksm за прогон: $((cow1 - cow0))"
cat "$OUT/probe.jsonl"
