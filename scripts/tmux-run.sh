#!/usr/bin/env bash
# =============================================================================
# tmux-run.sh — запускает длинную серию экспериментов в отдельной сессии tmux.
#
# Зачем: серия может идти десятки минут. В tmux она переживёт закрытие
# терминала, а за ходом можно наблюдать командой `tmux attach -t <имя>`.
#
# Использование:
#   scripts/tmux-run.sh <имя-сессии> <команда> [аргументы...]
#
# Что делает:
#   - весь вывод команды пишется в results/logs/<имя>.log;
#   - по завершении пишет код выхода в results/logs/<имя>.exit
#     (по этому файлу видно, что серия закончилась и чем);
#   - скрипт scripts/tmux-wait.sh <имя> ждёт появления этого файла.
# =============================================================================
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"

name="$1"; shift
logdir="$REPO/results/logs"
mkdir -p "$logdir"
log="$logdir/$name.log"
exitf="$logdir/$name.exit"
rm -f "$exitf"

if tmux has-session -t "$name" 2>/dev/null; then
    echo "Сессия tmux '$name' уже существует" >&2
    exit 1
fi

# printf %q экранирует аргументы, чтобы команда дошла до tmux без искажений.
cmd="$(printf '%q ' "$@")"
tmux new-session -d -s "$name" -c "$REPO" \
    "bash -c '$cmd 2>&1 | tee $(printf %q "$log"); echo \${PIPESTATUS[0]} > $(printf %q "$exitf")'"
echo "Запущено в tmux-сессии '$name'. Лог: $log"
