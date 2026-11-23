#!/usr/bin/env bash
# =============================================================================
# stage1-fork-share-check.sh — этап 1: как KSM учитывает страницы, которые уже
# физически общие после fork (copy-on-write), — важно для prefork-серверов
# (gunicorn, nginx, postgres), где pages_sharing может завышать экономию.
#
# Нагрузка: ksm_probe 64 МиБ (P = 16384 страниц), K = 256 вариантов, madvise.
#   single  — один процесс (как этап 0): теория pages_shared = K,
#             pages_sharing = P − K, реальная экономия = P − K страниц.
#   forked  — процесс заполняет память и делает fork; потомок ничего не пишет.
#             До KSM физических страниц P (общие для двух процессов).
#             Если KSM сливает и уже общие страницы, ожидаем rmap_item 2P,
#             pages_shared = K, pages_sharing = 2P − K, а РЕАЛЬНАЯ экономия
#             (по AnonPages и сумме Pss) — только P − K: pages_sharing завысит
#             её на P страниц. Если KSM такие страницы не трогает — увидим.
# Для каждого прогона пишутся счётчики KSM, сумма Anonymous (анонимный Rss,
# поле smaps_rollup) и Pss_Anon обоих
# процессов и системный AnonPages до включения KSM и после схождения.
# Параметры: REPS (5 — функциональная проверка с известным ответом, Р4).
# Результат: results/raw/stage1-fork-share-check/<время>/runs.csv
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"
safety_init
REPS="${REPS:-5}"
BIN="$REPO/workloads/ksm-probe/bin"
"$REPO/workloads/ksm-probe/build.sh" > /dev/null
OUT="$(new_series_dir stage1-fork-share-check)"
write_params "$OUT/params.json" experiment=stage1-fork-share-check reps="$REPS" mib=64 distinct=256
echo "config,rep,pages_shared,pages_sharing,rmap_items_sum,rss_before_kB,pss_before_kB,rss_after_kB,pss_after_kB,anon_before_kB,anon_after_kB" > "$OUT/runs.csv"

# Сумма поля smaps_rollup по процессу и его прямым потомкам.
sum_field() {
    local pid="$1" key="$2" total=0 p v
    for p in "$pid" $(cat /proc/"$pid"/task/*/children 2>/dev/null); do
        v=$(awk -v k="$key:" '$1==k{print $2}' /proc/"$p"/smaps_rollup 2>/dev/null || echo 0)
        total=$((total + ${v:-0}))
    done
    echo "$total"
}
sum_rmap() {
    local pid="$1" total=0 p v
    for p in "$pid" $(cat /proc/"$pid"/task/*/children 2>/dev/null); do
        v=$(awk '$1=="ksm_rmap_items"{print $2}' /proc/"$p"/ksm_stat 2>/dev/null || echo 0)
        total=$((total + ${v:-0}))
    done
    echo "$total"
}
anon() { awk '/^AnonPages:/{print $2}' /proc/meminfo; }

for rep in $(seq 1 "$REPS"); do
    for cfg in single forked; do
        STABILIZE_SEC=2 reset_state > /dev/null
        ksm_set pages_to_scan 5000
        extra=(); [[ "$cfg" == forked ]] && extra=(--fork-after-fill)
        ready="$(mktemp -u)"
        "$BIN/ksm_probe" --mode madvise --mib 64 --distinct 256 "${extra[@]}" \
            --ready-file "$ready" > "$OUT/$cfg-rep$rep.probe.jsonl" &
        probe=$!
        for _ in $(seq 1 600); do [[ -f "$ready" ]] && break; sleep 0.1; done
        rm -f "$ready"; sleep 1
        rb=$(sum_field "$probe" Anonymous); pb=$(sum_field "$probe" Pss_Anon); ab=$(anon)
        sysw "$KSM/run" 1
        prev=-1; same=0
        for _ in $(seq 1 240); do      # до 60 с; схождение = 2 с без изменений
            cur=$(cat $KSM/pages_sharing)
            if [[ "$cur" == "$prev" && "$cur" != 0 ]]; then same=$((same+1)); else same=0; fi
            (( same >= 8 )) && break
            prev=$cur; sleep 0.25
        done
        ra=$(sum_field "$probe" Anonymous); pa=$(sum_field "$probe" Pss_Anon); aa=$(anon)
        echo "$cfg,$rep,$(cat $KSM/pages_shared),$(cat $KSM/pages_sharing),$(sum_rmap "$probe"),$rb,$pb,$ra,$pa,$ab,$aa" >> "$OUT/runs.csv"
        kill -TERM "$probe"; wait "$probe" || true
        sysw "$KSM/run" 2
    done
done
column -s, -t "$OUT/runs.csv"
