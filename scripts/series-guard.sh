#!/usr/bin/env bash
# =============================================================================
# series-guard.sh — запустить серию с закреплёнными условиями питания (этап 4).
#
# Зачем (Л12–Л13): в блоке Г этапа 3 демон power-auto включал режим экономии
# (профиль power-saver), когда разъём зарядки на секунды терял контакт. Здесь
# на время серии в ~/.config/powersave/powersave.conf ставится:
#   auto_battery=off           power-auto перечитывает конфиг на лету и больше
#                              не включает экономию;
#   lid_profile_*=performance  на случай перезапуска режима «ИИ-агент» (уже
#                              работающий режим читает конфиг только при старте,
#                              поэтому крышку во время серий не закрывать).
# При ЛЮБОМ выходе (конец, ошибка, Ctrl+C, systemctl stop) конфиг возвращается
# из бэкапа. Бэкап — ~/.config-backups/<дата>-series-guard/.
#
#   scripts/series-guard.sh <команда серии> [аргументы...]
# Пример:
#   systemd-run --user --unit=ksm-s4 --collect --setenv=PATH="$PATH" \
#       bash ~/ksm-research/scripts/series-guard.sh scripts/stage4-block0.sh
# Код выхода — код команды.
# =============================================================================
set -uo pipefail
CONF="$HOME/.config/powersave/powersave.conf"
BK="$HOME/.config-backups/$(date +%F)-series-guard"
LOCK="${XDG_RUNTIME_DIR:-/tmp}/ksm-series-guard.lock"

exec 7> "$LOCK"
flock -n 7 || { echo "series-guard: уже идёт другая серия ($LOCK)" >&2; exit 75; }

restore() {
    if [[ -f "$BK/powersave.conf" ]]; then
        cp -f "$BK/powersave.conf" "$CONF" && echo "series-guard: powersave.conf восстановлен"
    fi
}

if [[ -f "$CONF" ]]; then
    mkdir -p "$BK"; cp -a "$CONF" "$BK/powersave.conf"
    trap restore EXIT
    trap 'exit 130' INT TERM
    # правка через python: только нужные строки, остальное как было
    python3 -I - "$CONF" <<'PY'
import re, sys
p = sys.argv[1]; s = open(p).read()
want = {"auto_battery": "off", "lid_profile_build": "performance",
        "lid_profile_agent": "performance", "lid_profile_idle": "performance"}
for k, v in want.items():
    if re.search(rf"^{k}=", s, re.M):
        s = re.sub(rf"^{k}=.*$", f"{k}={v}", s, flags=re.M)
    else:
        s += f"{k}={v}\n"
open(p, "w").write(s)
PY
    echo "series-guard: auto_battery=off, lid_profile_*=performance (бэкап $BK)"
else
    echo "series-guard: нет $CONF — менять нечего"
fi

"$@"
