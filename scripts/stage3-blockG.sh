#!/usr/bin/env bash
# =============================================================================
# stage3-blockG.sh — блок Г этапа 3: задержка под нагрузкой, KSM вкл./выкл.
# Три серии по порядку (docs/decisions.md, Р23):
#   1) stage3-load       nginx 400, Python 50, JVM 400 — частоты ВМ (прямое сравнение);
#   2) stage3-load-np    Node.js 200, PostgreSQL 200 — частоты ВМ (полный набор этапа 2);
#   3) stage3-load-high  nginx 1600, Python 200, JVM 1600 — та же относительная
#                        загрузка, что на ВМ.
# У JVM во всех сериях прогрев под нагрузкой 60 с (WARM_LOAD_S, Р22).
# Ошибка серии — стоп цепочки (следующие не запускаются).
#
# Параллельно раз в 10 с пишется results/logs/blockG-env.csv: крышка, сеть
# питания, заряд, профиль питания, режим «ИИ-агент». Если условия меняются
# посреди серии (владелец закрыл крышку, отключил зарядку), это видно по времени.
#
# Запуск отдельной службой:
#   systemd-run --user --unit=ksm-g --collect --setenv=PATH="$PATH" \
#       bash ~/ksm-research/scripts/stage3-blockG.sh
# Ход — results/logs/blockG.status, вывод — results/logs/blockG.log,
# код — results/logs/blockG.exit. Прервать: systemctl --user stop ksm-g.
# =============================================================================
set -uo pipefail
REPO="$HOME/ksm-research"; LOGS="$REPO/results/logs"
STATUS="$LOGS/blockG.status"; LOG="$LOGS/blockG.log"; ENVCSV="$LOGS/blockG-env.csv"
export MIN_AVAIL_MIB=2000
mkdir -p "$LOGS"; cd "$REPO" || exit 1
note() { echo "$(date '+%F %T') $*" | tee -a "$STATUS"; }

# --- запись условий ------------------------------------------------------------
env_logger() {
    echo "t_wall,t_mono_s,lid,ac_online,battery_pct,power_profile,agent_mode" > "$ENVCSV"
    while :; do
        local lid ac bat prof am
        lid=$(awk '{print $2}' /proc/acpi/button/lid/*/state 2>/dev/null | head -1)
        ac=$(cat /sys/class/power_supply/AC*/online 2>/dev/null | head -1)
        bat=$(cat /sys/class/power_supply/BAT*/capacity 2>/dev/null | head -1)
        prof=$(powerprofilesctl get 2>/dev/null)
        am=$("$HOME/bin/agent-mode" --status 2>/dev/null | head -1)
        echo "$(date -Is),$(awk '{print $1}' /proc/uptime),$lid,$ac,$bat,$prof,$am" >> "$ENVCSV"
        sleep 10
    done
}
env_logger & ENVPID=$!
trap 'kill "$ENVPID" 2>/dev/null' EXIT

# --- серии ---------------------------------------------------------------------
series() {
    local name="$1"; shift
    note "старт серии $name"
    echo "### $(date '+%F %T') серия $name: $*" >> "$LOG"
    env "$@" SERIES="$name" MODES="on off" PINS=none REPS=10 \
        WARM_LOAD_S=60 WARM_LOAD_WORKLOADS=jvm scripts/stage2-load.sh >> "$LOG" 2>&1
    local rc=$?
    note "конец серии $name, код $rc"
    if (( rc != 0 )); then echo "$rc" > "$LOGS/blockG.exit"; exit "$rc"; fi
    sleep 120   # пауза между сериями: остыть и успокоить систему
}

note "блок Г: старт"
scripts/preflight.sh >> "$LOG" 2>&1 || { note "СТОП: preflight с FAIL"; echo 1 > "$LOGS/blockG.exit"; exit 1; }
series stage3-load      WORKLOADS="nginx python jvm" RATE_NGINX=400 RATE_PYTHON=50 RATE_JVM=400
series stage3-load-np   WORKLOADS="node postgres"    RATE_NODE=200 RATE_POSTGRES=200
series stage3-load-high WORKLOADS="nginx python jvm" RATE_NGINX=1600 RATE_PYTHON=200 RATE_JVM=1600
echo 0 > "$LOGS/blockG.exit"
note "блок Г: все серии завершены"
