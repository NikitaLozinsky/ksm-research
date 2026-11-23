#!/usr/bin/env bash
# =============================================================================
# stage0-prctl-inherit.sh — этап 0, шаг 6: наследуется ли prctl(PR_SET_MEMORY_MERGE)
# через fork и exec, и как включить KSM для процесса в контейнере podman.
#
# Зачем: включать KSM для чужих программ (nginx, python, java, игровые
# серверы) без правки их кода можно только снаружи: поставить флаг и
# запустить программу. Это работает, только если флаг переживает fork/exec.
# В документации ядра (ksm.rst) и man-pages это не описано, а в истории ядра
# были ошибки именно здесь (см. docs/references.md), поэтому проверяем сами.
#
# Целевой процесс во всех случаях — ksm_probe --mode none: сам он KSM НЕ
# включает. Значит, если его память слилась, флаг пришёл снаружи.
# ksm_probe печатает JSON: pr_get_at_start (флаг при старте процесса),
# ksm_merge_any, ksm_merging_pages (сколько страниц реально слито).
#
# Конфигурации:
#   host-control     ksm_probe --mode none                     (флага нет нигде)
#   host-fork        ksm_probe --mode prctl --fork             (prctl → fork → ребёнок выделяет память)
#   host-exec        ksm_launch ksm_probe                      (prctl → exec)
#   host-fork-exec   ksm_launch bash -c 'ksm_probe; true'      (prctl → exec bash → fork → exec)
#   ctr-control      podman run ... ksm_probe                  (контроль в контейнере)
#   ctr-outer        ksm_launch podman run ... ksm_probe       (флаг ставится до podman)
#   ctr-inner        podman run ... ksm_launch ksm_probe       (лаунчер внутри контейнера)
#   ctr-systemd      systemd-run --user -p MemoryKSM=yes podman run ... ksm_probe
#
# Каждая конфигурация проверяется при двух настройках ksmd (SCANS):
#   default     pages_to_scan=100,   sleep_millisecs=20  (по умолчанию ядра)
#   aggressive  pages_to_scan=20000, sleep_millisecs=0   (условия, в которых
#               по сообщению в LKML (2025) флаг мог теряться при exec)
#
# Это функциональная проверка (да/нет), а не замер производительности,
# поэтому пауза стабилизации сокращена до 3 с (STABILIZE_SEC).
# Параметры: REPS (10), MIB (64), DISTINCT (256), HOLD (15), IMAGE (alpine:3.22)
# =============================================================================
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"
safety_init   # исходные настройки KSM вернутся при любом выходе (см. common.sh)
export STABILIZE_SEC="${STABILIZE_SEC:-3}"

REPS="${REPS:-10}"
MIB="${MIB:-64}"
DISTINCT="${DISTINCT:-256}"
HOLD="${HOLD:-15}"
IMAGE="${IMAGE:-docker.io/library/alpine:3.22}"
read -r -a CONFIGS <<< "${CONFIGS:-host-control host-fork host-exec host-fork-exec ctr-control ctr-outer ctr-inner ctr-systemd}"
read -r -a SCANS <<< "${SCANS:-default aggressive}"

BIN="$REPO/workloads/ksm-probe/bin"
"$REPO/workloads/ksm-probe/build.sh" > /dev/null
OUT="$(new_series_dir stage0-prctl-inherit)"
echo "Каталог серии: $OUT"

PROBE_ARGS=(--mode none --mib "$MIB" --distinct "$DISTINCT" --hold "$HOLD")
# Общие аргументы podman: без сети, каталог с бинарниками только для чтения.
PODMAN_RUN=(podman run --rm --network=none --label "$CTR_LABEL" -v "$BIN:/probe:ro" "$IMAGE")
export KSM_LAUNCH_QUIET=1

# Запуск одной конфигурации; stdout — JSON-строки от ksm_probe.
run_config() {
    case "$1" in
        host-control)   "$BIN/ksm_probe" "${PROBE_ARGS[@]}" ;;
        host-fork)      "$BIN/ksm_probe" --mode prctl --fork --mib "$MIB" --distinct "$DISTINCT" --hold "$HOLD" ;;
        host-exec)      "$BIN/ksm_launch" "$BIN/ksm_probe" "${PROBE_ARGS[@]}" ;;
        host-fork-exec) "$BIN/ksm_launch" bash -c '"$0" "$@"; true' "$BIN/ksm_probe" "${PROBE_ARGS[@]}" ;;
        ctr-control)    "${PODMAN_RUN[@]}" /probe/ksm_probe "${PROBE_ARGS[@]}" ;;
        ctr-outer)      "$BIN/ksm_launch" "${PODMAN_RUN[@]}" /probe/ksm_probe "${PROBE_ARGS[@]}" ;;
        ctr-inner)      "${PODMAN_RUN[@]}" /probe/ksm_launch /probe/ksm_probe "${PROBE_ARGS[@]}" ;;
        ctr-systemd)    systemd-run --user --quiet --wait --pipe -p MemoryKSM=yes \
                            "${PODMAN_RUN[@]}" /probe/ksm_probe "${PROBE_ARGS[@]}" ;;
        *) echo "неизвестная конфигурация $1" >&2; return 2 ;;
    esac
}

for rep in $(seq -w 1 "$REPS"); do
    for scan in "${SCANS[@]}"; do
        k=$(( (10#$rep - 1) % ${#CONFIGS[@]} ))
        order=("${CONFIGS[@]:k}" "${CONFIGS[@]:0:k}")
        for cfg in "${order[@]}"; do
            reset_state > /dev/null
            if [[ "$scan" == aggressive ]]; then
                ksm_set pages_to_scan 20000
                ksm_set sleep_millisecs 0
            fi
            base="$OUT/$cfg-$scan-rep$rep"
            write_params "$base.json" experiment=stage0-prctl-inherit config="$cfg" \
                scan="$scan" rep="$rep" mib="$MIB" distinct="$DISTINCT" hold_s="$HOLD" \
                image="$IMAGE" order="${order[*]}"
            sysw "$KSM/run" 1
            rc=0
            run_config "$cfg" > "$base.jsonl" 2> "$base.stderr" || rc=$?
            sysw "$KSM/run" 2
            echo "$rc" > "$base.rc"
            echo "rep$rep $scan $cfg rc=$rc $(tail -n1 "$base.jsonl" | grep -o '"pr_get_at_start":[0-9-]*,.*merging_pages":[0-9-]*' || echo НЕТ-JSON)"
        done
    done
done
echo "Серия завершена: $OUT"
