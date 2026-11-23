#!/usr/bin/env bash
# Наблюдение из fish: bash ~/ksm-research/scripts/watch-ticks.sh build|check|smoke|pilot
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
case "${1:-}" in build|check|smoke|pilot) ;; *) echo 'Укажите build, check, smoke или pilot' >&2; exit 2 ;; esac
status="$REPO/results/logs/ticks-$1.status"
duration() { printf '%02d:%02d:%02d' "$(($1/3600))" "$(($1/60%60))" "$(($1%60))"; }
trap 'printf "\n"; exit 130' INT
while :; do
    [[ -s "$status" ]] || { echo "Ожидаю запуск ($1)…"; sleep 2; continue; }
    IFS=$'\t' read -r pid start changed done total eta label < "$status"
    running=0
    if (( eta >= 0 )) && kill -0 "$pid" 2>/dev/null; then running=1; fi
    now=$(date +%s)
    (( running )) || now=$changed
    left='—'
    if (( running && eta > 0 )); then
        remain=$((eta - now + changed))
        if (( remain > 0 )); then left="~$(duration "$remain")"; fi
    fi
    if (( ! running && eta >= 0 )); then label="Прервано · $label"; fi
    printf '\r\033[K%d/%d · %d%% · прошло %s · осталось %s · %s' \
        "$done" "$total" "$((done * 100 / total))" "$(duration "$((now-start))")" "$left" "$label"
    if (( ! running )); then
        printf '\n'
        (( eta == -1 ))
        exit
    fi
    sleep 2
done
