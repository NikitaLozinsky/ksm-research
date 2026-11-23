#!/usr/bin/env bash
# =============================================================================
# stop-series.sh — аккуратно остановить идущую серию (этап 4).
#
# Почему не `systemctl --user stop`: systemd шлёт SIGTERM ВСЕМ процессам службы
# сразу, и обработчик очистки серии (safety_cleanup в common.sh) погибает вместе
# с остальными: контейнеры остаются, KSM не возвращается к исходным настройкам,
# hw.csv не копируется в каталог серии (так было 2026-11-19 04:04).
# Здесь SIGTERM получает только скрипт серии. Его обработчик сам
# останавливает контейнеры и сэмплеры, возвращает KSM и копирует hw.csv, после
# чего series-guard возвращает powersave.conf.
#
#   scripts/stop-series.sh          остановить и дождаться очистки (до 6 мин)
# =============================================================================
pattern='^(/usr/bin/)?bash [^ ]*(stage2-load|stage4-ticks)\.sh$'
# Дочерний bash в подстановке команды может иметь ту же командную строку.
pipeline=$(pgrep -o -u "$(id -u)" -f '^(/usr/bin/)?bash [^ ]*stage4-ticks-pipeline\.sh$')
pid="$pipeline"
if [[ -z "$pid" ]]; then pid=$(pgrep -o -u "$(id -u)" -f "$pattern"); fi
if [[ -z "$pid" ]]; then echo "серия не идёт"; exit 0; fi
echo "останавливаю серию: $pid"
kill -TERM "$pid" || exit 1
for _ in $(seq 1 360); do   # bash выполнит обработчик только после текущего sleep (до 150 с)
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
done
if kill -0 "$pid" 2>/dev/null; then
    echo "ВНИМАНИЕ: не завершился за 6 мин — проверьте вручную"; exit 1
fi
if [[ -n "$pipeline" ]]; then
    runtime="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    if [[ ! -d "$runtime" ]]; then runtime=/tmp; fi
    # Блокировка освобождается после восстановления конфигурации питания.
    flock -w 30 "$runtime/ksm-series-guard.lock" true || exit 1
fi
echo "контейнеров эксперимента: $(podman ps -aq --filter label=ksm-research=1 | wc -l)"
echo "KSM: run=$(cat /sys/kernel/mm/ksm/run) pages_to_scan=$(cat /sys/kernel/mm/ksm/pages_to_scan)"
echo "auto_battery: $(grep '^auto_battery' ~/.config/powersave/powersave.conf | cut -d= -f2)"
