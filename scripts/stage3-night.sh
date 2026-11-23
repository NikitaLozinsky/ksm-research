#!/usr/bin/env bash
# =============================================================================
# stage3-night.sh — ночная очередь этапа 3: дождаться блока А, затем блоки Б+В,
# затем вернуть профили питания режима «ИИ-агент».
#
# Запуск (отдельной службой systemd, а не из tmux агента: tmux живёт в cgroup
# Claude Desktop и умрёт, если закрыть приложение):
#   systemd-run --user --unit=ksm-night --collect \
#       --setenv=PATH="$PATH" bash ~/ksm-research/scripts/stage3-night.sh
#   journalctl --user -u ksm-night        — что происходило
#   systemctl --user stop ksm-night       — прервать (серия восстановит KSM сама)
#
# Шаги:
#   1) ждать results/logs/blockA.exit (блок А в tmux-сессии blockA).
#      Если сессия blockA пропала без .exit — блок А убит, дальше не идём;
#   2) блок А с ошибкой (код ≠ 0) — Б+В не запускаются;
#   3) блоки Б+В одной командой run-all.sh, лог results/logs/blockBV.log,
#      код выхода — results/logs/blockBV.exit;
#   4) в любом случае вернуть ~/.config/powersave/powersave.conf из бэкапа
#      (профили lid_profile_* ставились в performance только на эту ночь).
# Итог по шагам — results/logs/night.status (одна строка на событие).
# =============================================================================
set -uo pipefail
REPO="$HOME/ksm-research"
LOGS="$REPO/results/logs"
STATUS="$LOGS/night.status"
CONF="$HOME/.config/powersave/powersave.conf"
CONF_BACKUP="$HOME/.config-backups/2026-11-18-ksm-night/.config/powersave/powersave.conf"
BLOCK_BV=(jitter xcheck rehearsal cow-cost ksmd-tlb ksmd-pin)

note() { echo "$(date '+%F %T') $*" | tee -a "$STATUS"; }

# Профили вернуть при любом выходе (конец, ошибка, systemctl stop).
restore_conf() {
    if [[ -f "$CONF_BACKUP" ]] && cp "$CONF_BACKUP" "$CONF"; then
        note "powersave.conf восстановлен из бэкапа (работающий режим ИИ-агента держит performance до своего выключения)"
    else
        note "ПРЕДУПРЕЖДЕНИЕ: powersave.conf не восстановлен, бэкап: $CONF_BACKUP"
    fi
}
trap restore_conf EXIT

mkdir -p "$LOGS"
note "очередь запущена, жду блок А"

# 1) ждать блок А
while [[ ! -f "$LOGS/blockA.exit" ]]; do
    if ! tmux has-session -t blockA 2>/dev/null; then
        sleep 5   # сессия могла закрыться ровно в момент записи .exit
        [[ -f "$LOGS/blockA.exit" ]] && break
        note "СТОП: сессия blockA пропала без blockA.exit (убита?) — Б+В не запускаю"
        exit 1
    fi
    sleep 30
done
codeA=$(cat "$LOGS/blockA.exit")
note "блок А завершён, код $codeA"

# 2) блок А с ошибкой — дальше нельзя
if [[ "$codeA" != 0 ]]; then
    note "СТОП: блок А с ошибкой — Б+В не запускаю, см. $LOGS/blockA.log"
    exit 1
fi

# Пауза, чтобы система успокоилась после блока А (сброс кэшей, контейнеры).
sleep 120

# 3) блоки Б+В
note "старт блоков Б+В: ${BLOCK_BV[*]}"
cd "$REPO" || exit 1
MIN_AVAIL_MIB=2000 scripts/run-all.sh "${BLOCK_BV[@]}" > "$LOGS/blockBV.log" 2>&1
codeBV=$?
echo "$codeBV" > "$LOGS/blockBV.exit"
note "блоки Б+В завершены, код $codeBV"
exit "$codeBV"
