#!/usr/bin/env bash
# =============================================================================
# common.sh — общие функции для всех скриптов экспериментов.
#
# Подключается командой:   source "$(dirname "$0")/lib/common.sh"
#
# Здесь собрано то, что должно выполняться ОДИНАКОВО во всех экспериментах:
#   - сброс состояния системы перед прогоном (правило из CLAUDE.md);
#   - установка параметров KSM;
#   - запись файла параметров прогона (.json рядом с CSV).
#
# Ничего не зависит от VirtualBox: те же функции работают на нативной машине.
# =============================================================================

# Корень репозитория (каталог над scripts/).
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
KSM=/sys/kernel/mm/ksm
# Метка, которой помечаются ВСЕ контейнеры экспериментов (podman run --label).
# Останавливаются и удаляются только контейнеры с этой меткой.
CTR_LABEL="ksm-research=1"

# Записать значение в sysfs/procfs от root. Обычный `sudo echo > файл` не
# работает (перенаправление делает ещё непривилегированный shell), поэтому
# используется `sudo tee`.
#   sysw <файл> <значение>
sysw() {
    echo "$2" | sudo tee "$1" > /dev/null
}

# Установить параметр KSM и проверить, что он действительно применился.
#   ksm_set pages_to_scan 100
ksm_set() {
    local name="$1" value="$2"
    sysw "$KSM/$name" "$value"
    local got
    got="$(cat "$KSM/$name")"
    # Для advisor_mode значение выглядит как "[none] scan-time", поэтому
    # проверяем вхождение, а не точное равенство.
    if [[ "$got" != "$value" && "$got" != *"[$value]"* ]]; then
        echo "ОШИБКА: $name=$got, ожидалось $value" >&2
        return 1
    fi
}

# Привести все настраиваемые параметры KSM к значениям по умолчанию ядра.
# Вызывается при сбросе, чтобы прогон не зависел от предыдущего.
# advisor_mode сначала выключается: при scan-time нельзя менять pages_to_scan.
ksm_defaults() {
    ksm_set advisor_mode none
    ksm_set pages_to_scan 100
    ksm_set sleep_millisecs 20
    ksm_set smart_scan 1
    ksm_set use_zero_pages 0
    ksm_set max_page_sharing 256
}

# Сброс состояния перед каждым прогоном (порядок фиксирован, см. CLAUDE.md):
#   1) остановить контейнеры ЭКСПЕРИМЕНТА (только с меткой $CTR_LABEL —
#      чужие контейнеры владельца на нативной системе не трогаются);
#   2) run=2: остановить ksmd и разъединить все слитые страницы;
#      дождаться, пока pages_shared и pages_sharing станут 0;
#   3) вернуть параметры KSM по умолчанию;
#   4) sync + drop_caches=3 (сбросить page cache, dentries и inodes);
#   5) пауза для стабилизации (по умолчанию 10 с, можно задать STABILIZE_SEC).
reset_state() {
    local pause="${STABILIZE_SEC:-10}"
    echo "[reset] остановка контейнеров эксперимента (метка $CTR_LABEL)"
    stop_our_containers

    echo "[reset] KSM run=2 (разъединение страниц)"
    sysw "$KSM/run" 2
    local i
    for i in $(seq 1 300); do
        if [[ "$(cat $KSM/pages_shared)" == 0 && "$(cat $KSM/pages_sharing)" == 0 ]]; then
            break
        fi
        sleep 0.1
    done
    if [[ "$(cat $KSM/pages_sharing)" != 0 ]]; then
        echo "ПРЕДУПРЕЖДЕНИЕ: после run=2 pages_sharing=$(cat $KSM/pages_sharing)" >&2
    fi
    ksm_defaults

    echo "[reset] sync + drop_caches"
    sync
    sysw /proc/sys/vm/drop_caches 3

    echo "[reset] пауза ${pause} с"
    sleep "$pause"
}

# Записать файл параметров прогона в JSON.
#   write_params <путь.json> ключ=значение ...
# В файл попадают: дата, ядро, тип виртуализации, git-коммит, все параметры
# KSM и THP на момент вызова, а также переданные пары ключ=значение.
write_params() {
    local out="$1"; shift
    python3 -I "$REPO/scripts/lib/runinfo.py" "$@" > "$out"
}

# Найти PID потока ядра ksmd (нужен для замера его загрузки ЦП).
ksmd_pid() {
    pgrep -x ksmd | head -1
}

# Каталог для сырых результатов серии: results/raw/<имя>/<дата-время>/
#   new_series_dir stage0-ksm-probe   → печатает путь
new_series_dir() {
    local d="$REPO/results/raw/$1/$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$d"
    echo "$d"
}

# Остановить и удалить только наши контейнеры (с меткой $CTR_LABEL).
stop_our_containers() {
    local ids
    ids="$(podman ps -aq --filter "label=$CTR_LABEL" 2>/dev/null || true)"
    if [[ -n "$ids" ]]; then
        podman stop --time 5 $ids > /dev/null 2>&1 || true
        # -v: удалить и АНОНИМНЫЕ тома этих контейнеров (образ postgres объявляет
        # VOLUME для данных; без -v тома копились — 8.6 ГБ к этапу 2). Именованные и
        # чужие тома не затрагиваются.
        podman rm -f -v $ids > /dev/null 2>&1 || true
    fi
}

# =============================================================================
# Безопасность (важно для нативной системы)
#
#   safety_init      вызвать в начале каждого скрипта серии, сразу после source.
#                    1) сохраняет ИСХОДНЫЕ настройки KSM (не «по умолчанию», а
#                       те, что были у владельца до запуска);
#                    2) ставит ловушку: при ЛЮБОМ выходе (нормальный конец,
#                       ошибка set -e, Ctrl+C, SIGTERM) выполняется
#                       safety_cleanup;
#                    3) если задан MIN_AVAIL_MIB, запускает сторож памяти:
#                       когда MemAvailable падает ниже порога, серия
#                       прерывается (SIGTERM), до OOM дело не доходит.
#   safety_cleanup   остановить трассировку perf, фоновые процессы скрипта,
#                    наши контейнеры; run=2 (разъединить страницы); вернуть
#                    исходные настройки KSM. Повторный вызов ничего не делает.
# =============================================================================
KSM_TUNABLES=(pages_to_scan sleep_millisecs merge_across_nodes use_zero_pages
              max_page_sharing stable_node_chains_prune_millisecs smart_scan
              advisor_max_cpu advisor_min_pages_to_scan advisor_max_pages_to_scan
              advisor_target_scan_time)
SAFETY_DIR=""
SAFETY_DONE=0

# Значение advisor_mode без скобок: "[none] scan-time" → none
ksm_advisor_mode() {
    sed -E 's/.*\[([^]]+)\].*/\1/' "$KSM/advisor_mode"
}

# Привязка потока ksmd к ЦП (этап 2, опыт с механизмом хвоста).
# Маска ksmd — часть исходного состояния: сохраняется в снимке и
# восстанавливается safety_cleanup так же, как настройки KSM.
ksmd_get_affinity() { taskset -pc "$(ksmd_pid)" | sed 's/.*: *//'; }
ksmd_set_affinity() { sudo taskset -pc "$1" "$(ksmd_pid)" > /dev/null; }

ksm_snapshot_save() {
    local f="$1" t
    {
        echo "ksmd_affinity=$(ksmd_get_affinity)"
        echo "run=$(cat "$KSM/run")"
        echo "advisor_mode=$(ksm_advisor_mode)"
        for t in "${KSM_TUNABLES[@]}"; do echo "$t=$(cat "$KSM/$t")"; done
    } > "$f"
}

# Восстановление: сначала run=2 (иначе max_page_sharing и merge_across_nodes
# не меняются — EBUSY, см. decisions Р13), advisor выключить (иначе нельзя
# писать pages_to_scan, Р14), затем параметры, затем исходный advisor_mode
# (он сбрасывает pages_to_scan, поэтому pages_to_scan пишется повторно для
# режима none), и в самом конце исходный run.
ksm_snapshot_restore() {
    local f="$1" k v run_orig adv_orig
    [[ -f "$f" ]] || return 0
    run_orig="$(sed -n 's/^run=//p' "$f")"
    local aff_orig; aff_orig="$(sed -n 's/^ksmd_affinity=//p' "$f")"
    if [[ -n "$aff_orig" && "$(ksmd_get_affinity)" != "$aff_orig" ]]; then
        ksmd_set_affinity "$aff_orig" || echo "ПРЕДУПРЕЖДЕНИЕ: не удалось вернуть привязку ksmd $aff_orig" >&2
    fi
    adv_orig="$(sed -n 's/^advisor_mode=//p' "$f")"
    sysw "$KSM/run" 2
    sysw "$KSM/advisor_mode" none
    while IFS='=' read -r k v; do
        [[ "$k" == run || "$k" == advisor_mode || "$k" == ksmd_affinity ]] && continue
        [[ "$(cat "$KSM/$k")" == "$v" ]] || sysw "$KSM/$k" "$v" 2>/dev/null \
            || echo "ПРЕДУПРЕЖДЕНИЕ: не удалось восстановить $k=$v" >&2
    done < "$f"
    if [[ "$adv_orig" != none ]]; then sysw "$KSM/advisor_mode" "$adv_orig"; fi
    [[ "$run_orig" == 2 ]] || sysw "$KSM/run" "$run_orig"
}

safety_cleanup() {
    [[ "$SAFETY_DONE" == 1 ]] && return 0
    SAFETY_DONE=1
    echo "[safety] очистка ($1)" >&2
    if declare -F trace_stop > /dev/null; then trace_stop 2>/dev/null || true; fi
    local j
    # Фоновые процессы скрипта. Запущенные через sudo принадлежат root —
    # обычный kill их не достанет, поэтому запасной вариант через sudo.
    for j in $(jobs -p); do kill -TERM "$j" 2>/dev/null || sudo -n kill -TERM "$j" 2>/dev/null || true; done
    wait 2>/dev/null || true
    stop_our_containers
    # Частота/температура за всю серию (hw_sampler, этап 3) — в каталог серии.
    # Сэмплер уже остановлен выше вместе с остальными фоновыми процессами.
    if [[ -n "$SAFETY_DIR" && -s "$SAFETY_DIR/hw.csv" && -n "${OUT:-}" && -d "${OUT:-}" ]]; then
        cp "$SAFETY_DIR/hw.csv" "$OUT/hw.csv" || true
    fi
    if [[ -n "$SAFETY_DIR" ]]; then
        ksm_snapshot_restore "$SAFETY_DIR/ksm.orig" || true
        echo "[safety] KSM: страницы разъединены, исходные настройки восстановлены" >&2
        rm -rf "$SAFETY_DIR"
    fi
}

mem_watchdog_start() {
    local main_pid=$$ limit="$MIN_AVAIL_MIB"
    (
        while sleep 1; do
            avail=$(awk '/^MemAvailable:/{print int($2/1024)}' /proc/meminfo)
            if (( avail < limit )); then
                echo "[safety] MemAvailable=${avail} МиБ < порога ${limit} МиБ — прерываю серию" >&2
                kill -TERM "$main_pid"
                exit 0
            fi
        done
    ) &
}

safety_init() {
    SAFETY_DIR="$(mktemp -d)"
    ksm_snapshot_save "$SAFETY_DIR/ksm.orig"
    trap 'safety_cleanup EXIT' EXIT
    trap 'safety_cleanup INT; exit 130' INT
    trap 'safety_cleanup TERM; exit 143' TERM
    if [[ -n "${MIN_AVAIL_MIB:-}" ]]; then mem_watchdog_start; fi
    # Частота ЦП, температура и троттлинг раз в 1 с на всю серию (только чтение
    # /sys). HW_SAMPLER=0 — выключить. Файл копирует safety_cleanup в $OUT.
    if [[ "${HW_SAMPLER:-1}" == 1 ]]; then
        python3 -I "$REPO/scripts/lib/hw_sampler.py" --out "$SAFETY_DIR/hw.csv" --interval 1 &
    fi
}
