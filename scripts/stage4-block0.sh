#!/usr/bin/env bash
# =============================================================================
# stage4-block0.sh — блок 0 этапа 4: эквивалентность под нагрузкой (Р24).
# Python 50 и JVM 400 запр./с на контейнер, 8 контейнеров, 30 пар (вкл./выкл.
# подряд, случайный порядок), прогрев JVM под нагрузкой 60 с.
#   SMOKE=1 — дымовой прогон: 1 пара, короткие фазы, серия stage4-eq-smoke.
#   REP_START=N — продолжение после остановки: пары N..30 в новом каталоге
#   серии stage4-eq, порядок с другим seed (20261119 + N). Первый кусок
#   (2026-11-19 02:55–04:04) остановлен владельцем из-за шума вентилятора на
#   паре 4: готовы python 1–3, jvm 1–4. Продолжать с REP_START=4 (у JVM будет
#   31 пара — лишняя пара анализу не мешает).
#   REP_END=M — закончить кусок на паре M (по умолчанию 30): дробить серию
#   на дневные куски, ночью вентилятор мешает владельцу.
# Запуск (условия питания закрепляет series-guard.sh):
#   systemd-run --user --unit=ksm-s4b0 --collect --setenv=PATH="$PATH" \
#       bash ~/ksm-research/scripts/series-guard.sh ~/ksm-research/scripts/stage4-block0.sh
# Ход — results/logs/block0.status, вывод — block0.log, код — block0.exit.
# =============================================================================
set -uo pipefail
REPO="$HOME/ksm-research"; LOGS="$REPO/results/logs"
tag=block0; [[ "${SMOKE:-0}" == 1 ]] && tag=block0-smoke
STATUS="$LOGS/$tag.status"; LOG="$LOGS/$tag.log"
export MIN_AVAIL_MIB=2000
mkdir -p "$LOGS"; cd "$REPO" || exit 1
rm -f "$LOGS/$tag.exit"
note() { echo "$(date '+%F %T') $*" | tee -a "$STATUS"; }

note "старт ($tag)"
scripts/preflight.sh >> "$LOG" 2>&1 || { note "СТОП: preflight с FAIL"; echo 1 > "$LOGS/$tag.exit"; exit 1; }
if [[ "${SMOKE:-0}" == 1 ]]; then
    set -- SERIES=stage4-eq-smoke REPS=1 PRE_S=10 MERGE_S=20 STEADY_S=20 WARM_LOAD_S=10
else
    set -- SERIES=stage4-eq REPS="${REP_END:-30}" WARM_LOAD_S=60 REP_START="${REP_START:-1}"
fi
seed=$(( 20261119 + ${REP_START:-1} - 1 ))
echo "### $(date '+%F %T') параметры: $* SEED=$seed" >> "$LOG"
note "параметры: $* SEED=$seed"
env "$@" PAIRED=1 SEED="$seed" WORKLOADS="python jvm" MODES="on off" PINS=none \
    RATE_PYTHON=50 RATE_JVM=400 WARM_LOAD_WORKLOADS=jvm scripts/stage2-load.sh >> "$LOG" 2>&1
rc=$?
echo "$rc" > "$LOGS/$tag.exit"
note "конец ($tag), код $rc"
exit "$rc"
