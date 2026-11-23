#!/usr/bin/env bash
# =============================================================================
# stage0-jitter.sh — этап 0, шаг 7: шум системы (базовая линия для замеров
# задержки). Холостой прогон: KSM выключен (run=2 после сброса), нагрузки нет,
# работает только timer_jitter.
#
# Конфигурации (CONFIGS, вида инструмент:период_мкс):
#   own:1000         — наша программа, 1 мс (стандартный период cyclictest,
#                      чувствителен к мелкому шуму);
#   own:16667        — наша программа, 60 Гц (типичный тик игрового сервера);
#   ownroot:1000     — наша программа, запущенная через sudo (от root); нужна
#                      для мини-серии этапа 1 по расхождению с cyclictest;
#   cyclictest:1000  — cyclictest из rt-tests в тех же условиях: перекрёстная
#                      проверка нашей программы общепринятым инструментом.
#                      --laptop: НЕ трогать /dev/cpu_dma_latency (иначе
#                      cyclictest запретил бы глубокие C-state и условия
#                      отличались бы от нашей программы); гистограмма с шагом
#                      1 мкс до 100 мс. Запуск через sudo: cyclictest 2.11
#                      без root не стартует даже с SCHED_OTHER. Опцию -p
#                      передавать НЕЛЬЗЯ: любое -p (даже -p 0) включает
#                      SCHED_FIFO (cyclictest.c v2.11, обработка OPT_PRIORITY).
#                      Политика SCHED_OTHER с nice 0 одинакова для root и
#                      обычного пользователя.
# Длительность прогона DURATION (60 с), повторов REPS (10). Политика
# планировщика SCHED_OTHER (как у обычных серверов, без привилегий).
#
# Результат: results/raw/stage0-jitter/<время>/<инструмент>-p<период>-rep<NN>.csv
# (для cyclictest — .cyclictest.txt с гистограммой) + .json + .load
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"
safety_init   # исходные настройки KSM вернутся при любом выходе (см. common.sh)

REPS="${REPS:-10}"
DURATION="${DURATION:-60}"
read -r -a CONFIGS <<< "${CONFIGS:-own:1000 own:16667 cyclictest:1000}"
SERIES="${SERIES:-stage0-jitter}"

DIR="$REPO/workloads/jitter"
mkdir -p "$DIR/bin"
gcc -O2 -Wall -Wextra -o "$DIR/bin/timer_jitter" "$DIR/timer_jitter.c"
OUT="$(new_series_dir "$SERIES")"
echo "Каталог серии: $OUT"

for rep in $(seq -w 1 "$REPS"); do
    k=$(( (10#$rep - 1) % ${#CONFIGS[@]} ))
    order=("${CONFIGS[@]:k}" "${CONFIGS[@]:0:k}")
    for cfg in "${order[@]}"; do
        tool="${cfg%%:*}"; p="${cfg##*:}"
        reset_state > /dev/null
        base="$OUT/$tool-p$p-rep$rep"
        write_params "$base.json" experiment="$SERIES" tool="$tool" period_us="$p" rep="$rep" \
            duration_s="$DURATION" policy=SCHED_OTHER ksm_run=2 order="${order[*]}" \
            cyclictest_version="$(cyclictest --help 2>&1 | head -1 | tr -d '"')"
        # Загрузка системы до и после прогона: видно, не было ли фоновой активности.
        # Строка cpu из /proc/stat (8-е число = steal: время, когда гипервизор
        # не давал vCPU работать; на нативной системе ~0).
        { echo "loadavg_before=$(cat /proc/loadavg)"; echo "stat_before=$(grep '^cpu ' /proc/stat)"; } > "$base.load"
        if [[ "$tool" == own ]]; then
            "$DIR/bin/timer_jitter" --period-us "$p" --duration "$DURATION" --out "$base.csv"
        elif [[ "$tool" == ownroot ]]; then
            sudo "$DIR/bin/timer_jitter" --period-us "$p" --duration "$DURATION" --out "$base.csv"
        else
            sudo cyclictest --laptop -q -t1 --policy=other -i "$p" -D "$DURATION" \
                -h 100000 > "$base.cyclictest.txt" 2> "$base.cyclictest.err"
        fi
        { echo "loadavg_after=$(cat /proc/loadavg)"; echo "stat_after=$(grep '^cpu ' /proc/stat)"; } >> "$base.load"
        echo "rep$rep $tool p=${p}us готово"
    done
done
echo "Серия завершена: $OUT"
