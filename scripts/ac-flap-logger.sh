#!/usr/bin/env bash
# ac-flap-logger.sh — журнал датчика сети питания раз в 1 с (пишет только смены).
# Зачем: разъём зарядки работает с перебоями (docs/limitations.md), а журнал условий
# блока Г пишет раз в 10 с и короткие пропадания не видит. По этому файлу при
# анализе помечаются прогоны, в окно которых попало питание от батареи.
# Столбцы: t_wall, t_mono_s (как /proc/uptime), ac_online. Выходит сам, когда служба
# ksm-g (блок Г) закончилась.
OUT="$HOME/ksm-research/results/logs/blockG-ac.csv"
[ -s "$OUT" ] || echo "t_wall,t_mono_s,ac_online" > "$OUT"
prev=
while systemctl --user is-active -q ksm-g; do
    ac=$(cat /sys/class/power_supply/AC0/online 2>/dev/null)
    if [ "$ac" != "$prev" ]; then
        echo "$(date -Is),$(awk '{print $1}' /proc/uptime),$ac" >> "$OUT"; prev=$ac
    fi
    sleep 1
done
