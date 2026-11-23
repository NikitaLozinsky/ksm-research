#!/usr/bin/env bash
set -euo pipefail
case "$1" in
    ticksrv) exec /bin-host/ticksrv ;;
    luanti)
        # Игра лежит в пользовательском каталоге Luanti. У каждого контейнера свой мир.
        mkdir -p "$HOME/.minetest/games" /out/world
        ln -s /bench/luanti/ksmbench "$HOME/.minetest/games/ksmbench"
        cp /bench/luanti/luanti.conf /tmp/bench.conf
        printf '\ntickbench_entities = %s\ntickbench_edits = %s\n' \
            "${TICK_LUANTI_ENTITIES:-64}" "${TICK_LUANTI_EDITS:-32}" >> /tmp/bench.conf
        exec luantiserver --gameid ksmbench --world /out/world --config /tmp/bench.conf --logfile /dev/null
        ;;
    godot)
        # Движок может создавать служебные файлы. Исходный проект смонтирован только на чтение.
        cp -r /bench/godot /tmp/project
        exec env GODOT_SILENCE_ROOT_WARNING=1 godot --headless --path /tmp/project --max-fps 60
        ;;
    *) echo "Неизвестная нагрузка: $1" >&2; exit 2 ;;
esac
