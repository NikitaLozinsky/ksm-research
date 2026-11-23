#!/usr/bin/env bash
# =============================================================================
# watch-series.sh — следить за идущей серией: шкала прогресса, текущий прогон,
# оценка конца, питание и температура. Ничего не меняет, только читает.
#
#   watch-series.sh [серия] [всего прогонов]
#   по умолчанию — последняя серия в results/raw; число прогонов для известных
#   серий берётся само (stage4-eq — 120, stage4-eq-smoke — 4).
# Выход — Ctrl+C. Обновление раз в 5 с.
# =============================================================================
REPO="$HOME/ksm-research"
declare -A TOTAL=([stage4-eq]=120 [stage4-eq-smoke]=4 [stage3-load]=60 [stage3-load-np]=40 [stage3-load-high]=60)
while :; do
    ser="${1:-$(ls -td "$REPO"/results/raw/*/*/ 2>/dev/null | head -1 | xargs -r dirname | xargs -r basename)}"
    dir=$(ls -td "$REPO/results/raw/$ser"/*/ 2>/dev/null | head -1)
    total="${2:-${TOTAL[$ser]:-0}}"
    # продолжение серии с REP_START: прогонов меньше (пары REP_START..REPS)
    if [[ -z "${2:-}" && "$ser" == stage4-eq ]]; then
        rs=$(grep -o 'REP_START=[0-9]*' "$REPO/results/logs/block0.log" 2>/dev/null | tail -1 | cut -d= -f2)
        first=$(ls -d "$dir"*-rep*/ 2>/dev/null | sed 's/.*-rep//; s|/||' | sort -n | head -1)
        last=$(grep -o 'REPS=[0-9]*' "$REPO/results/logs/block0.log" 2>/dev/null | tail -1 | cut -d= -f2)
        [[ -n "$first" ]] && total=$(( (${last:-30} - 10#$first + 1) * 4 ))
    fi
    clear
    echo "Серия: $ser   ($dir)"
    if [[ -z "$dir" ]]; then echo "серий нет"; sleep 5; continue; fi
    runs=$(ls -d "$dir"*-rep*/ 2>/dev/null | wc -l)
    done_n=$(ls "$dir"*-rep*/ksm.csv 2>/dev/null | while read -r f; do [[ -n $(find "$(dirname "$f")" -name 'lat-c*.csv.gz' -o -name 'pgbench-c*.gz' | head -1) ]] && echo; done | wc -l)
    cur=$(ls -td "$dir"*-rep*/ 2>/dev/null | head -1 | xargs -r basename)
    # шкала
    if (( total > 0 )); then
        pct=$(( done_n * 100 / total )); w=40; f=$(( done_n * w / total ))
        bar=""; for ((i = 0; i < w; i++)); do (( i < f )) && bar+="#" || bar+="."; done
        printf '[%s] %d / %d прогонов (%d %%)\n' "$bar" "$done_n" "$total" "$pct"
        start=$(stat -c %W "$dir" 2>/dev/null); [[ "$start" == 0 || -z "$start" ]] && start=$(stat -c %Y "$(ls -d "$dir"*-rep*/ | tail -1)" 2>/dev/null)
        first=$(ls -trd "$dir"*-rep*/ 2>/dev/null | head -1); t0=$(stat -c %W "$first" 2>/dev/null)
        if (( done_n > 0 && t0 > 0 )); then
            el=$(( $(date +%s) - t0 )); per=$(( el / done_n )); left=$(( (total - done_n) * per ))
            printf 'прошло %d ч %02d мин, ~%d мин на прогон, осталось ~%d ч %02d мин (конец около %s)\n' \
                $((el/3600)) $((el%3600/60)) $((per/60)) $((left/3600)) $((left%3600/60)) "$(date -d "@$(( $(date +%s) + left ))" +%H:%M)"
        fi
    else
        echo "прогонов готово: $done_n (всего неизвестно — передайте вторым аргументом)"
    fi
    echo "сейчас: ${cur:-—}"
    echo
    echo "службы: $(systemctl --user list-units --no-legend 'ksm-*' 2>/dev/null | awk '{print $1" "$4}' | tr '\n' ' ')"
    for st in "$REPO"/results/logs/block0*.status; do [[ -f $st ]] && echo "$(basename "$st"): $(tail -1 "$st")"; done
    ac=$(cat /sys/class/power_supply/AC*/online 2>/dev/null | head -1); bat=$(cat /sys/class/power_supply/BAT*/capacity 2>/dev/null | head -1)
    t=$(( $(cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -n | tail -1) / 1000 ))
    echo "питание: $([[ $ac == 1 ]] && echo сеть || echo 'БАТАРЕЯ!') ${bat}%  профиль: $(cat /sys/firmware/acpi/platform_profile 2>/dev/null)  экономия: $(grep '^auto_battery' ~/.config/powersave/powersave.conf | cut -d= -f2) (off — защита серии включена)  t° ${t} °C"
    echo "контейнеров: $(podman ps -q --filter label=ksm-research=1 2>/dev/null | wc -l)   KSM run=$(cat /sys/kernel/mm/ksm/run)  слито страниц: $(cat /sys/kernel/mm/ksm/pages_sharing)"
    echo; echo "последние строки журнала серии:"
    tail -n 3 "$(ls -t "$REPO"/results/logs/block0*.log 2>/dev/null | head -1)" 2>/dev/null | cut -c1-150
    sleep 5
done
