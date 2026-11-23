#!/usr/bin/env bash
# =============================================================================
# demo-ksm.sh — короткая наглядная демонстрация KSM для записи в asciinema.
# Не является замером (для чисел используется stage0-ksm-probe.sh).
#
# Запускает ksm_probe (256 МиБ, 1024 варианта страниц, madvise), включает KSM
# с параметрами по умолчанию и раз в 2 секунды печатает счётчики, пока
# экономия не достигнет теоретической (или не пройдёт 60 с).
#
# Запись:  asciinema rec -c scripts/demo-ksm.sh recordings/stage0-ksm-demo.cast
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"
BIN="$REPO/workloads/ksm-probe/bin"
"$REPO/workloads/ksm-probe/build.sh" > /dev/null

STABILIZE_SEC=2 reset_state > /dev/null
echo "Ядро: $(uname -r); KSM: pages_to_scan=$(cat $KSM/pages_to_scan), sleep_millisecs=$(cat $KSM/sleep_millisecs)"
echo "Запускаю ksm_probe: 256 МиБ, 1024 различных варианта страниц, madvise(MADV_MERGEABLE)"
ready="$(mktemp -u)"
"$BIN/ksm_probe" --mode madvise --mib 256 --distinct 1024 --ready-file "$ready" > /dev/null &
probe=$!
while [[ ! -f "$ready" ]]; do sleep 0.1; done; rm -f "$ready"
echo "Теория: pages_shared=1024, pages_sharing=64512 (252 МиБ)"
pss() { awk '/^Pss_Anon/ {print $2}' /proc/$probe/smaps_rollup; }
echo "Pss_Anon процесса до KSM: $(pss) КиБ"
echo "Включаю KSM (run=1)"
sysw "$KSM/run" 1
printf '%5s %12s %13s %10s %12s %14s\n' "t,с" pages_shared pages_sharing full_scans "экономия,МиБ" "Pss_Anon,КиБ"
for t in $(seq 0 2 60); do
    sh=$(cat $KSM/pages_shared); sg=$(cat $KSM/pages_sharing)
    printf '%5s %12s %13s %10s %12s %14s\n' "$t" "$sh" "$sg" "$(cat $KSM/full_scans)" "$((sg * 4 / 1024))" "$(pss)"
    [[ "$sg" -ge 64512 ]] && break
    sleep 2
done
kill "$probe"; wait "$probe" 2>/dev/null || true
sysw "$KSM/run" 2
echo "Готово: KSM остановлен (run=2)."
