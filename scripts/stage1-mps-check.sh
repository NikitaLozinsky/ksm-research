#!/usr/bin/env bash
# =============================================================================
# stage1-mps-check.sh — этап 1, шаг 2: как max_page_sharing меняется на лету,
# какие значения допустимы и как он влияет на экономию.
#
# По коду ядра 7.2.9 (mm/ksm.c, max_page_sharing_store):
#   - значение разбирается kstrtoint, меньше 2 → EINVAL;
#   - смена значения → EBUSY, если есть слитые страницы (ksm_pages_shared != 0)
#     или не удалось удалить все стабильные узлы. Т.е. менять можно только
#     после run=2 (разъединить всё).
# Скрипт проверяет это записью значений и фиксирует код ошибки каждой попытки.
#
# Затем влияние на экономию: ksm_probe 16 МиБ (4096 страниц), DISTINCT=16
# вариантов → по C=256 копий каждого. Теория при лимите M:
#   KSM-страниц на вариант = ceil(C / M),  pages_shared = 16 * ceil(C/M),
#   pages_sharing = 4096 - pages_shared.
# Проверяется для M из MPS_LIST, REPS повторов (функциональная проверка,
# результат детерминированный, поэтому 3 повтора по умолчанию — см. Р4).
# Результат: results/raw/stage1-mps-check/<время>/{attempts.csv,effect.csv,params.json}
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"
safety_init   # исходные настройки KSM вернутся при любом выходе (см. common.sh)

REPS="${REPS:-3}"
read -r -a MPS_LIST <<< "${MPS_LIST:-2 8 64 256 1024}"
BIN="$REPO/workloads/ksm-probe/bin"
"$REPO/workloads/ksm-probe/build.sh" > /dev/null
OUT="$(new_series_dir stage1-mps-check)"
echo "Каталог: $OUT"
write_params "$OUT/params.json" experiment=stage1-mps-check reps="$REPS" mps_list="${MPS_LIST[*]}" \
    mib=16 distinct=16

# Попытка записи с фиксацией результата (OK или текст ошибки).
#   try_set <сценарий> <значение>
try_set() {
    local scen="$1" val="$2" res
    if res=$(echo "$val" | sudo tee "$KSM/max_page_sharing" 2>&1 >/dev/null); then res=OK; fi
    res="${res##*: }"   # оставить только текст ошибки
    echo "$scen,$val,$res,$(cat $KSM/max_page_sharing),$(cat $KSM/pages_shared)" >> "$OUT/attempts.csv"
}
echo "scenario,value,result,value_after,pages_shared" > "$OUT/attempts.csv"

# --- 1. Допустимые значения при пустом KSM -----------------------------------
STABILIZE_SEC=2 reset_state > /dev/null
for v in 1 0 -1 abc 2 2147483647 2147483648 256; do try_set empty "$v"; done

# --- 2. Смена при наличии слитых страниц -------------------------------------
ksm_set pages_to_scan 5000
sysw "$KSM/run" 1
ready="$(mktemp -u)"
"$BIN/ksm_probe" --mode madvise --mib 16 --distinct 16 --ready-file "$ready" > /dev/null &
probe=$!
for _ in $(seq 1 300); do [[ "$(cat $KSM/pages_sharing)" -ge 4000 ]] && break; sleep 0.1; done
try_set run1_merged 1024
sysw "$KSM/run" 0                       # ksmd остановлен, слияние сохранено
try_set run0_merged 1024
sysw "$KSM/run" 2                       # разъединить всё
for _ in $(seq 1 100); do [[ "$(cat $KSM/pages_shared)" == 0 ]] && break; sleep 0.1; done
try_set run2_unmerged 1024
kill "$probe"; wait "$probe" 2>/dev/null || true
rm -f "$ready"

# --- 3. Влияние на экономию --------------------------------------------------
echo "mps,rep,pages_shared,pages_sharing,stable_node_chains,stable_node_dups,expected_shared,expected_sharing" > "$OUT/effect.csv"
for rep in $(seq 1 "$REPS"); do
    for m in "${MPS_LIST[@]}"; do
        STABILIZE_SEC=2 reset_state > /dev/null
        ksm_set max_page_sharing "$m"
        ksm_set pages_to_scan 5000
        sysw "$KSM/run" 1
        "$BIN/ksm_probe" --mode madvise --mib 16 --distinct 16 > /dev/null &
        probe=$!
        # ждать, пока pages_sharing перестанет меняться 3 с подряд (до 60 с)
        prev=-1; same=0
        for _ in $(seq 1 120); do
            cur="$(cat $KSM/pages_sharing)"
            if [[ "$cur" == "$prev" && "$cur" != 0 ]]; then same=$((same+1)); else same=0; fi
            [[ $same -ge 6 ]] && break
            prev="$cur"; sleep 0.5
        done
        c=256; per=$(( (c + m - 1) / m )); es=$((16 * per)); eg=$((4096 - es))
        echo "$m,$rep,$(cat $KSM/pages_shared),$(cat $KSM/pages_sharing),$(cat $KSM/stable_node_chains),$(cat $KSM/stable_node_dups),$es,$eg" >> "$OUT/effect.csv"
        kill "$probe"; wait "$probe" 2>/dev/null || true
        sysw "$KSM/run" 2
    done
done
STABILIZE_SEC=1 reset_state > /dev/null
column -s, -t "$OUT/attempts.csv"
echo
column -s, -t "$OUT/effect.csv"
