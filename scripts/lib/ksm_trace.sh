#!/usr/bin/env bash
# =============================================================================
# ksm_trace.sh — запись событий KSM через perf с точной синхронизацией старта.
#
# Подключается после common.sh:   source "$(dirname "$0")/lib/ksm_trace.sh"
#
# Что пишется (system-wide, все ЦП):
#   ksm:*                  все tracepoint-ы KSM ядра (см. docs/decisions.md, Р12);
#   sched:sched_switch     только переключения, где участвует ksmd, т.е.
#                          точные интервалы, когда ksmd занимает ЦП.
# Часы: CLOCK_MONOTONIC (-k CLOCK_MONOTONIC) — совпадают с clock_gettime в
# наших программах, поэтому события и тики сопоставляются напрямую.
#
# Почему синхронизация: perf record -a стартует больше секунды (на этапе 1
# первая проверка потеряла событие ksm_enter, т.к. процесс зарегистрировался
# раньше, чем perf начал запись). Поэтому perf запускается с ВЫКЛЮЧЕННЫМИ
# событиями (--delay=-1), затем получает команду "enable" через FIFO и
# отвечает "ack". Только после ack функция trace_start возвращает управление.
#
#   trace_start <путь/perf.data>      начать запись (ждёт подтверждения)
#   trace_stop                        остановить и дождаться завершения perf
#   trace_export <perf.data> <out.txt.gz>   текст perf script (+ удалить .data)
# =============================================================================

TRACE_DIR=""
TRACE_PID=""

trace_start() {
    local data="$1"
    TRACE_DIR="$(mktemp -d)"
    mkfifo "$TRACE_DIR/ctl" "$TRACE_DIR/ack"
    local ksmd
    ksmd="$(ksmd_pid)"
    sudo perf record -q -a -k CLOCK_MONOTONIC -o "$data" \
        --delay=-1 --control="fifo:$TRACE_DIR/ctl,$TRACE_DIR/ack" \
        -e 'ksm:*' \
        -e sched:sched_switch --filter "prev_pid == $ksmd || next_pid == $ksmd" \
        > "$TRACE_DIR/perf.log" 2>&1 &
    TRACE_PID=$!
    # Открыть FIFO: управляющий на запись, подтверждений на чтение.
    exec {TRACE_CTL}>"$TRACE_DIR/ctl"
    exec {TRACE_ACK}<"$TRACE_DIR/ack"
    echo enable >&"$TRACE_CTL"
    local reply=""
    if ! read -r -t 30 -u "$TRACE_ACK" reply || [[ "$reply" != ack* ]]; then
        echo "ОШИБКА: perf не подтвердил включение записи (ответ: '$reply')" >&2
        cat "$TRACE_DIR/perf.log" >&2
        return 1
    fi
}

trace_stop() {
    [[ -n "$TRACE_PID" ]] || return 0
    echo disable >&"$TRACE_CTL" || true
    read -r -t 10 -u "$TRACE_ACK" _ || true
    exec {TRACE_CTL}>&- {TRACE_ACK}<&-
    # sudo пересылает SIGINT процессу perf; perf корректно дописывает файл.
    sudo kill -INT "$TRACE_PID" 2>/dev/null || true
    wait "$TRACE_PID" 2>/dev/null || true
    rm -rf "$TRACE_DIR"
    TRACE_PID=""
}

trace_export() {
    local data="$1" out="$2"
    sudo perf script -i "$data" -F comm,pid,cpu,time,event,trace 2>/dev/null | gzip > "$out"
    sudo rm -f "$data" "$data.old"
}
