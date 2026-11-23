#!/usr/bin/env bash
# =============================================================================
# stage0-ksm-probe.sh — этап 0, шаг 5: работает ли KSM в этой системе и как
# быстро он экономит память при параметрах ядра по умолчанию.
#
# Нагрузка: workloads/ksm-probe (N МиБ, K различных вариантов страниц).
# Конфигурации (MODES):
#   none    — контроль: KSM включён глобально (run=1), но процесс не просил
#             слияния → экономии быть не должно;
#   madvise — область отмечена madvise(MADV_MERGEABLE);
#   prctl   — весь процесс отмечен prctl(PR_SET_MEMORY_MERGE).
#
# Один повтор:
#   1) reset_state (сброс KSM, кэшей, пауза);
#   2) запуск ksm_probe, ожидание заполнения памяти;
#   3) запуск сэмплера (каждые INTERVAL с), PRE секунд базовой линии;
#   4) KSM run=1 с параметрами по умолчанию (pages_to_scan=100,
#      sleep_millisecs=20), наблюдение DURATION секунд;
#   5) остановка сэмплера и нагрузки, run=2.
# Момент включения KSM определяется по столбцу run в CSV (первая строка с 1).
#
# Параметры через переменные окружения (значения по умолчанию в скобках):
#   REPS (10), MODES ("none madvise prctl"), MIB (256), DISTINCT (1024),
#   DURATION (90), PRE (5), INTERVAL (0.5), THP_FLAG ("" или "--no-thp")
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"
safety_init   # исходные настройки KSM вернутся при любом выходе (см. common.sh)

REPS="${REPS:-10}"
read -r -a MODES <<< "${MODES:-none madvise prctl}"
MIB="${MIB:-256}"
DISTINCT="${DISTINCT:-1024}"
DURATION="${DURATION:-90}"
PRE="${PRE:-5}"
INTERVAL="${INTERVAL:-0.5}"
THP_FLAG="${THP_FLAG:-}"
SERIES="${SERIES:-stage0-ksm-probe}"

BIN="$REPO/workloads/ksm-probe/bin"
"$REPO/workloads/ksm-probe/build.sh" > /dev/null
OUT="$(new_series_dir "$SERIES")"
echo "Каталог серии: $OUT"

for rep in $(seq -w 1 "$REPS"); do
    # Циклический сдвиг порядка конфигураций (см. скилл run-experiment).
    k=$(( (10#$rep - 1) % ${#MODES[@]} ))
    order=("${MODES[@]:k}" "${MODES[@]:0:k}")
    echo "=== повтор $rep, порядок: ${order[*]}"
    for mode in "${order[@]}"; do
        reset_state
        base="$OUT/$mode-rep$rep"
        ready="$(mktemp -u)"

        # 2) нагрузка
        "$BIN/ksm_probe" --mode "$mode" --mib "$MIB" --distinct "$DISTINCT" \
            $THP_FLAG --ready-file "$ready" > "$base.probe.jsonl" &
        probe=$!
        for _ in $(seq 1 600); do [[ -f "$ready" ]] && break; sleep 0.1; done
        [[ -f "$ready" ]] || { echo "ksm_probe не заполнил память за 60 с" >&2; exit 1; }
        rm -f "$ready"

        # Параметры записываются до включения KSM: в json попадает
        # конфигурация, с которой стартует прогон.
        write_params "$base.json" experiment="$SERIES" mode="$mode" rep="$rep" \
            mib="$MIB" distinct="$DISTINCT" duration_s="$DURATION" pre_s="$PRE" \
            interval_s="$INTERVAL" thp_flag="${THP_FLAG:-default}" \
            order="${order[*]}" probe_pid="$probe"

        # 3) сэмплер + базовая линия
        python3 -I "$REPO/scripts/lib/ksm_sampler.py" --out "$base.csv" \
            --interval "$INTERVAL" --pid "$probe" --pss &
        sampler=$!
        sleep "$PRE"

        # 4) включить KSM и наблюдать
        sysw "$KSM/run" 1
        sleep "$DURATION"

        # 5) остановка
        kill -TERM "$sampler"; wait "$sampler" || true
        kill -TERM "$probe";   wait "$probe"   || true
        sysw "$KSM/run" 2
        echo "  $mode rep$rep: $(tail -c 200 "$base.probe.jsonl" | tr -d '\n')"
    done
done
echo "Серия завершена: $OUT"
