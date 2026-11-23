#!/usr/bin/env bash
# Парная серия тиков. По умолчанию дымовая: три нагрузки × KSM вкл./выкл.
# Изменяемые параметры: REPS, REP_START, SEED, N, WARM_S, PRE_S, MERGE_S, STEADY_S.
# Запускать через series-guard.sh. Для остановки использовать stop-series.sh.
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"
source "$REPO/scripts/lib/ksm_trace.sh"
source "$REPO/scripts/lib/tick-progress.sh"
source "$REPO/scripts/lib/tick-workloads.sh"

SERIES="${SERIES:-stage4-ticks-smoke}"
CALIBRATE="${TICK_CALIBRATE:-0}"
read -r -a targets <<< "${WORKLOADS:-ticksrv luanti godot}"
[[ "$CALIBRATE" == 0 || "$CALIBRATE" == 1 ]] || exit 2
declare -A seen=()
for target in "${targets[@]}"; do
    case "$target" in ticksrv|luanti|godot) ;; *) echo "Неизвестная нагрузка: $target" >&2; exit 2 ;; esac
    [[ ! ${seen[$target]+yes} ]] || exit 2
    seen[$target]=1
done
(( ${#targets[@]} > 0 )) || exit 2
REPS="${REPS:-1}"; REP_START="${REP_START:-1}"; SEED="${SEED:-20261123}"
N="${N:-8}"; WARM_S="${WARM_S:-5}"; PRE_S="${PRE_S:-10}"
MERGE_S="${MERGE_S:-30}"; STEADY_S="${STEADY_S:-12}"
for val in "$REPS" "$REP_START" "$SEED" "$N" "$WARM_S" "$PRE_S" "$MERGE_S" "$STEADY_S"; do
    [[ "$val" =~ ^[1-9][0-9]*$ ]] || { echo "Ожидаются положительные целые числа" >&2; exit 2; }
done
[[ "$SERIES" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ && "$REP_START" -le "$REPS" ]] || exit 2
if (( CALIBRATE )); then
    read -r -a cpu_steps <<< "${TICK_CPU_STEPS:-600000 1200000 2400000 4800000}"
    read -r -a edit_steps <<< "${TICK_EDIT_STEPS:-512 1024 2048 4096}"
    read -r -a pass_steps <<< "${TICK_PASS_STEPS:-32 64 128 256}"
    for target in "${targets[@]}"; do
        case "$target" in
            ticksrv) length=${#cpu_steps[@]} ;;
            luanti) length=${#edit_steps[@]} ;;
            godot) length=${#pass_steps[@]} ;;
        esac
        (( length >= REPS )) || { echo "Мало точек калибровки для $target" >&2; exit 2; }
    done
fi
tag=ticks-smoke
PINNING=0
if [[ "$SERIES" == stage4-ticks-block-b ]]; then
    tag="ticks-block-b-$REP_START"
elif [[ "$SERIES" == stage4-ticks-block-v ]]; then
    tag="ticks-block-v-$REP_START"
    PINNING=1
else
    case "$SERIES" in stage4-ticks-calibration*|stage4-ticks-pilot*) tag=ticks-pilot ;; esac
fi
progress_init "$tag" "$(( (REPS - REP_START + 1) * ${#targets[@]} * (2 - CALIBRATE + 2 * PINNING) ))"
log="$REPO/results/logs/$tag.log"
ids=()
finish() {
    local rc=$?
    if (( rc )); then
        for i in "${!ids[@]}"; do
            podman logs "${ids[i]}" > "$base/c$((i+1))/engine.log" 2>&1 || true
        done
    fi
    if [[ -n "$SAFETY_DIR" ]]; then safety_cleanup EXIT; fi
    if (( rc )); then progress -2 "Ошибка (код $rc) · results/logs/$tag.log"; fi
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
exec > "$log" 2>&1

cd "$REPO"
if (( PINNING )); then
    topology=$(python3 "$REPO/scripts/lib/tick-topology.py")
    read -r pin_cpu sibling_cpu split_cpu peer_cpus <<< "$topology"
fi
export MIN_AVAIL_MIB="${MIN_AVAIL_MIB:-2000}"
"$REPO/scripts/preflight.sh"
if [[ ${TICK_REQUIRE_PERFORMANCE:-0} == 1 && $(cat /sys/firmware/acpi/platform_profile) != performance ]]; then
    echo "Профиль performance не удерживается" >&2
    exit 1
fi
tick_build_binaries
TICK_IMAGE=$(podman image inspect --format '{{.Id}}' "$TICK_IMAGE")
versions=$(podman run --rm --network=none --entrypoint pacman "$TICK_IMAGE" -Q luanti-server godot)
versions=${versions//$'\n'/, }
[[ "$versions" == 'luanti-server 5.17.'*', godot 4.7.'* ]] || { echo "Версии вне плана: $versions" >&2; exit 1; }
OUT=$(new_series_dir "$SERIES")
echo "Каталог серии: $OUT"
safety_init
# safety_init ставит свои ловушки. Сохраняем код выхода и вывод контейнера поверх общей очистки.
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
cpus=$(awk '/Cpus_allowed_list:/{print $2}' /proc/self/status)
RANDOM=$SEED
estimate=$(( WARM_S + PRE_S + MERGE_S + STEADY_S + ${STABILIZE_SEC:-10} + 20 ))

stamp() {
    python3 -I -c '
import sys, time
before = time.monotonic_ns()
raw = time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
real = time.time_ns()
after = time.monotonic_ns()
print(sys.argv[1], (before + after) // 2, raw, real, after - before, sep=",")
' "$1" >> "$base/clocks.csv"
}

running() {
    local states
    states=$(podman inspect --format '{{.State.Running}}' "${ids[@]}")
    if [[ $(sort -u <<< "$states") != true ]]; then
        printf 'Контейнер завершился во время измерения: %s\n' "${ids[*]}" >&2
        return 1
    fi
}

pause() {
    # wait прерывается сигналом сразу, в отличие от синхронного sleep в bash.
    sleep "$1" &
    wait "$!"
}

world_ksm() {
    local phase=$1 i pid
    for i in "${!pids[@]}"; do
        pid=${pids[i]}
        sudo awk -v phase="$phase" -v container="c$((i+1))" '
            /^[[:xdigit:]]+-[[:xdigit:]]+ / { world = /\[anon:ticksrv-world\]$/ }
            world && /^KSM:/ { print phase "," container "," $2; found=1 }
            END { if (!found) exit 1 }
        ' "/proc/$pid/smaps" >> "$base/world-ksm.csv"
    done
}

for ((rep = REP_START; rep <= REPS; rep++)); do
    round_seed=$SEED
    if [[ "$SERIES" == stage4-ticks-block-b || "$PINNING" == 1 ]]; then
        # Порядок данного повтора одинаков при любом разбиении на дневные части.
        round_seed=$((SEED + rep - 1))
        RANDOM=$round_seed
    fi
    workloads=("${targets[@]}")
    for ((i = ${#workloads[@]} - 1; i > 0; i--)); do
        j=$((RANDOM % (i + 1)))
        w=${workloads[i]}; workloads[i]=${workloads[j]}; workloads[j]=$w
    done
    for w in "${workloads[@]}"; do
        if (( PINNING )); then
            modes=(shared sibling split unpinned)
            for ((i = 3; i > 0; i--)); do
                j=$((RANDOM % (i + 1)))
                variant=${modes[i]}; modes[i]=${modes[j]}; modes[j]=$variant
            done
        elif (( CALIBRATE )); then
            modes=(off)
            case "$w" in
                ticksrv) TICK_CPU_WORK=${cpu_steps[rep-1]}; level="итераций $TICK_CPU_WORK" ;;
                luanti) TICK_LUANTI_EDITS=${edit_steps[rep-1]}; level="правок $TICK_LUANTI_EDITS" ;;
                godot) TICK_GODOT_PASSES=${pass_steps[rep-1]}; level="итераций на тело $TICK_GODOT_PASSES" ;;
            esac
        elif (( RANDOM % 2 )); then modes=(off on)
        else modes=(on off)
        fi
        pair_pos=0
        for variant in "${modes[@]}"; do
            mode=$variant
            pin=none
            ksmd_cpus=$cpus
            app_cpus=$cpus
            if (( PINNING )); then
                pin=$variant
                mode=on
                case "$pin" in
                    shared) ksmd_cpus=$pin_cpu; app_cpus=$pin_cpu ;;
                    sibling) ksmd_cpus=$pin_cpu; app_cpus=$sibling_cpu ;;
                    split) ksmd_cpus=$pin_cpu; app_cpus=$split_cpu ;;
                esac
            fi
            pair_pos=$((pair_pos + 1))
            if (( CALIBRATE )); then
                label="$w · калибровка $rep · $level"
            elif (( PINNING )); then
                label="$w · $pin · повтор $rep"
            else
                label="$w · KSM $mode · повтор $rep"
            fi
            current=$((total - completed))
            progress "$((current * estimate))" "$label · сброс"
            base="$OUT/$w-$variant-rep$rep"
            mkdir "$base"
            reset_state
            ksm_set pages_to_scan 1000
            ksmd_set_affinity "$ksmd_cpus"
            merge=0
            if [[ "$mode" == on ]]; then merge=1; fi

            progress "$((current * estimate - ${STABILIZE_SEC:-10}))" "$label · запуск"
            echo 'phase,mono_ns,raw_ns,realtime_ns,span_ns' > "$base/clocks.csv"
            stamp launch
            ids=(); pids=(); pairs=()
            for ((i = 1; i <= N; i++)); do
                if (( PINNING && i == 1 )); then
                    id=$(TICK_APP_CPUS="$app_cpus" tick_start "$w" "$merge" "$base/c$i" --label "$CTR_LABEL")
                elif (( PINNING )); then
                    id=$(TICK_APP_CPUS="$peer_cpus" TICK_CPU_WORK=0 tick_start "$w" "$merge" "$base/c$i" --label "$CTR_LABEL")
                else
                    id=$(tick_start "$w" "$merge" "$base/c$i" --label "$CTR_LABEL")
                fi
                ids+=("$id")
                pid=$(podman inspect --format '{{.State.Pid}}' "$id")
                pids+=("$pid"); pairs+=("c$i=$pid")
            done
            for i in "${!ids[@]}"; do tick_wait_ready "$w" "${ids[i]}" "$base/c$((i+1))"; done
            if (( PINNING )); then
                # Ядро показывает эффективные маски после exec конечного процесса.
                for i in "${!pids[@]}"; do
                    actual=$(awk '/Cpus_allowed_list:/{print $2}' "/proc/${pids[i]}/status")
                    echo "c$((i+1)) CPU: $actual"
                    expected=$peer_cpus
                    if (( i == 0 )); then expected=$app_cpus; fi
                    python3 - "$actual" "$expected" <<'CPUS'
import sys
def expand(s):
    return {i for field in s.split(",") for first, _, last in [field.partition("-")]
            for i in range(int(first), int(last or first) + 1)}
if expand(sys.argv[1]) != expand(sys.argv[2]):
    raise SystemExit("Маска контейнера не совпала с заданной")
CPUS
                done
            fi
            pause "$WARM_S"
            running
            write_params "$base/params.json" experiment="$SERIES" workload="$w" mode="$mode" \
                ksm_launch_merge="$merge" n="$N" rep="$rep" paired="$((1 - CALIBRATE - PINNING))" \
                trace="$((1 - CALIBRATE))" pair_pos="$pair_pos" seed="$SEED" \
                round_seed="$round_seed" stabilize_s="${STABILIZE_SEC:-10}" \
                pre_s="$PRE_S" merge_s="$MERGE_S" steady_s="$STEADY_S" warm_s="$WARM_S" \
                image_id="$TICK_IMAGE" packages="$versions" containers="${pairs[*]}" ksmd_cpus="$ksmd_cpus" pin="$pin" app_cpus="$app_cpus" \
                peer_cpus="${peer_cpus:-none}" measured_ctr="$([[ "$PINNING" == 1 ]] && echo c1 || echo all)" \
                tick_writes="${TICK_WRITES:-128}" tick_cpu_work="${TICK_CPU_WORK:-0}" \
                luanti_entities="${TICK_LUANTI_ENTITIES:-64}" luanti_edits="${TICK_LUANTI_EDITS:-32}" \
                godot_bodies="${TICK_GODOT_BODIES:-128}" godot_passes="${TICK_GODOT_PASSES:-1}"
            if [[ "$w" == ticksrv ]]; then
                echo 'phase,ctr,world_ksm_kib' > "$base/world-ksm.csv"
                world_ksm pre
            fi
            if ((! CALIBRATE)); then
                trace_start "$base/perf.data"
                python3 -I "$REPO/scripts/lib/vmstat_fast.py" --out "$base/vmstat.csv" --interval 0.01 \
                    --duration "$(( PRE_S + MERGE_S + STEADY_S + 60 ))" &
                s_vm=$!
            fi
            python3 -I "$REPO/scripts/lib/ksm_sampler.py" --out "$base/ksm.csv" --interval 1 &
            s_ksm=$!
            sudo python3 -I "$REPO/scripts/lib/ctr_mem_sampler.py" --out "$base/mem.csv" --interval 2 "${pairs[@]}" &
            s_mem=$!

            rest=$(( (current - 1) * estimate + 8 ))
            progress "$((rest + PRE_S + MERGE_S + STEADY_S))" "$label · контроль"
            stamp pre
            pause "$PRE_S"
            running
            ksm_set run 1
            stamp merge
            progress "$((rest + MERGE_S + STEADY_S))" "$label · слияние"
            pause "$MERGE_S"
            running
            stamp steady
            progress "$((rest + STEADY_S))" "$label · устойчивая фаза"
            pause "$STEADY_S"
            running
            stamp end
            if [[ "$w" == ticksrv ]]; then world_ksm end; fi
            # Запись движка сбрасывается пачками. Даём записать хвост измеренного окна.
            pause 2
            stamp shutdown
            kill -TERM "$s_ksm"
            sudo kill -TERM "$s_mem"
            wait "$s_ksm" "$s_mem" || true
            if ((! CALIBRATE)); then
                kill -TERM "$s_vm"
                wait "$s_vm" || true
                trace_stop
                trace_export "$base/perf.data" "$base/perf-script.txt.gz"
            fi
            progress "$rest" "$label · сохранение"
            running
            podman stop --time 8 "${ids[@]}" > /dev/null
            for i in "${!ids[@]}"; do
                p="$base/c$((i+1))"
                podman logs "${ids[i]}" > "$p/engine.log" 2>&1
                code=$(podman inspect --format '{{.State.ExitCode}}' "${ids[i]}")
                if [[ "$code" != 0 && "$code" != 143 ]]; then
                    echo "$label: контейнер c$((i+1)) завершился с кодом $code" >&2
                    exit 1
                fi
            done
            podman rm "${ids[@]}" > /dev/null
            ids=()
            ksm_set run 2
            if [[ "$SERIES" == stage4-ticks-block-b || "$PINNING" == 1 ]]; then
                # Маркер пишется после остановки контейнеров, экспорта трассы и разъединения страниц.
                touch "$base/complete"
            fi
            completed=$((completed + 1))
        done
    done
done
progress 0 "Проверка журналов и расчёт сводки"
python3 "$REPO/scripts/stage4-ticks-summary.py" "$OUT" &
wait "$!"
safety_cleanup done
progress -1 "Серия завершена"
