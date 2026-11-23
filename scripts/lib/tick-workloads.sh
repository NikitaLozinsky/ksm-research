# Общие команды штатной серии и проверки движков без изменения KSM.
TICK_SOURCE="$REPO/workloads/ticksrv"
TICK_BIN="$TICK_SOURCE/bin"
TICK_IMAGE="${TICK_IMAGE:-localhost/ksm-ticks:latest}"

tick_build_binaries() {
    mkdir -p "$TICK_BIN"
    gcc -std=c11 -Wall -Wextra -Werror -O2 "$TICK_SOURCE/ticksrv.c" -o "$TICK_BIN/ticksrv"
    "$REPO/workloads/ksm-probe/build.sh" > /dev/null
}

tick_start() {
    local workload=$1 mode=$2 out=$3
    shift 3
    # Godot без init работает как PID 1 и не завершается по SIGTERM.
    if [[ "$workload" == godot ]]; then set -- --init "$@"; fi
    local cmd=(bash /bench/entrypoint.sh "$workload")
    if [[ -n ${TICK_APP_CPUS:-} ]]; then cmd=(taskset -c "$TICK_APP_CPUS" "${cmd[@]}"); fi
    mkdir -p "$out"
    podman run -d "$@" --network=none \
        -v "$TICK_SOURCE:/bench:ro" -v "$TICK_BIN:/bin-host:ro" \
        -v "$REPO/workloads/ksm-probe/bin:/ksm:ro" -v "$out:/out" \
        -e KSM_LAUNCH_QUIET=1 -e KSM_LAUNCH_MERGE="$mode" \
        -e TICK_WRITES="${TICK_WRITES:-128}" -e TICK_CPU_WORK="${TICK_CPU_WORK:-0}" \
        -e TICK_LUANTI_ENTITIES="${TICK_LUANTI_ENTITIES:-64}" \
        -e TICK_LUANTI_EDITS="${TICK_LUANTI_EDITS:-32}" -e TICK_GODOT_BODIES="${TICK_GODOT_BODIES:-128}" \
        -e TICK_GODOT_PASSES="${TICK_GODOT_PASSES:-1}" \
        --entrypoint /ksm/ksm_launch "$TICK_IMAGE" "${cmd[@]}"
}

tick_log_path() {
    local workload=$1 out=$2
    if [[ "$workload" == luanti ]]; then echo "$out/world/ticks.csv"
    else echo "$out/ticks.csv"; fi
}

tick_wait_ready() {
    local workload=$1 id=$2 out=$3 file
    file=$(tick_log_path "$workload" "$out")
    for _ in {1..30}; do
        if [[ -s "$file" ]] && [[ $(wc -l < "$file") -gt 1 ]]; then return; fi
        [[ $(podman inspect --format '{{.State.Running}}' "$id") == true ]] || break
        sleep 1
    done
    podman logs "$id" >&2
    echo "$workload: контейнер $id не начал писать тики" >&2
    return 1
}
