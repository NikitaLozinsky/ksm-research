#!/usr/bin/env bash
# =============================================================================
# stage1-cow-cost.sh — этап 1, шаг 7.1: цена copy-on-write KSM-страницы.
#
# Вопрос: сколько стоит первая запись в слитую KSM страницу по сравнению с
# записью в обычную (неслитую) страницу.
#
# Нагрузка: ksm_probe 64 МиБ (16384 страницы, 256 вариантов). Один прогон:
#   1) reset_state; ускоренное сканирование (pages_to_scan=5000), чтобы
#      слияние заняло секунды;
#   2) ksm_probe заполняет память; run=1; для слитой конфигурации — ждать,
#      пока pages_sharing достигнет теории (16384-256), не дольше 60 с;
#   3) run=0: ksmd остановлен, слияние СОХРАНЯЕТСЯ (иначе ksmd во время
#      записи сливал бы страницы обратно и мешал замеру);
#   4) SIGUSR1 → ksm_probe один раз пишет по байту в каждую страницу и
#      печатает длительность прохода (CLOCK_MONOTONIC);
#   5) cow_ksm до/после — проверка, что COW было ровно столько, сколько
#      слитых страниц (и 0 в контроле).
# Конфигурации:
#   merged-thp      madvise, THP как в системе (always)
#   merged-nothp    madvise + MADV_NOHUGEPAGE
#   plain-thp       без KSM (mode none), THP как в системе — контроль
#   plain-nothp     без KSM, без THP — контроль
# Контроль «plain»: те же страницы, тот же проход записи, но без COW.
# Параметры: REPS (10), MIB (64), DISTINCT (256).
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"
safety_init

REPS="${REPS:-10}"; MIB="${MIB:-64}"; DISTINCT="${DISTINCT:-256}"
read -r -a CONFIGS <<< "${CONFIGS:-merged-thp merged-nothp plain-thp plain-nothp}"
BIN="$REPO/workloads/ksm-probe/bin"
"$REPO/workloads/ksm-probe/build.sh" > /dev/null
OUT="$(new_series_dir stage1-cow-cost)"
echo "Каталог серии: $OUT"
PAGES=$((MIB * 256)); EXPECT=$((PAGES - DISTINCT))

for rep in $(seq -w 1 "$REPS"); do
    k=$(( (10#$rep - 1) % ${#CONFIGS[@]} ))
    order=("${CONFIGS[@]:k}" "${CONFIGS[@]:0:k}")
    for cfg in "${order[@]}"; do
        base="$OUT/$cfg-rep$rep"
        reset_state > /dev/null
        ksm_set pages_to_scan 5000
        mode=none; [[ "$cfg" == merged-* ]] && mode=madvise
        thp=(); [[ "$cfg" == *-nothp ]] && thp=(--no-thp)
        ready="$(mktemp -u)"
        "$BIN/ksm_probe" --mode "$mode" --mib "$MIB" --distinct "$DISTINCT" "${thp[@]}" \
            --ready-file "$ready" > "$base.probe.jsonl" &
        probe=$!
        for _ in $(seq 1 600); do [[ -f "$ready" ]] && break; sleep 0.1; done
        rm -f "$ready"
        sysw "$KSM/run" 1
        if [[ "$mode" == madvise ]]; then
            for _ in $(seq 1 600); do (( $(cat $KSM/pages_sharing) >= EXPECT )) && break; sleep 0.1; done
        else
            sleep 5   # то же время работы ksmd, что примерно нужно слитой конфигурации
        fi
        sysw "$KSM/run" 0
        sharing_before=$(cat $KSM/pages_sharing)
        cow0=$(awk '/^cow_ksm /{print $2}' /proc/vmstat)
        write_params "$base.json" experiment=stage1-cow-cost config="$cfg" rep="$rep" mib="$MIB" \
            distinct="$DISTINCT" mode="$mode" thp_flag="${thp[*]:-default}" \
            pages_sharing_before_touch="$sharing_before" order="${order[*]}"
        sleep 1
        kill -USR1 "$probe"
        for _ in $(seq 1 300); do grep -q '"event":"touch"' "$base.probe.jsonl" && break; sleep 0.05; done
        cow1=$(awk '/^cow_ksm /{print $2}' /proc/vmstat)
        echo "{\"cow_ksm_delta\":$((cow1 - cow0)),\"pages_sharing_before_touch\":$sharing_before}" > "$base.cow.json"
        kill -TERM "$probe"; wait "$probe" || true
        sysw "$KSM/run" 2
        echo "rep$rep $cfg: sharing=$sharing_before cow=$((cow1 - cow0)) $(grep -o '"duration_s":[0-9.]*' "$base.probe.jsonl")"
    done
done
echo "Серия завершена: $OUT"
