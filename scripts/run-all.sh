#!/usr/bin/env bash
# =============================================================================
# run-all.sh — запуск набора серий «в один клик» (например, на нативной системе).
#
#   scripts/run-all.sh --list                 показать доступные серии
#   scripts/run-all.sh --dry-run [серии...]   preflight + план, НИЧЕГО не меняет
#   scripts/run-all.sh [серии...]             preflight, затем серии по очереди
#
# Без списка серий берётся набор по умолчанию (DEFAULT_SET ниже).
# Запускать лучше в tmux:  scripts/tmux-run.sh all scripts/run-all.sh
#
# Порядок работы:
#   1) capture-env.sh — снимок окружения (env/<тип системы>/);
#   2) preflight.sh — если есть FAIL, запуск прекращается;
#   3) серии по одной; каждая сама сбрасывает состояние и при любом выходе
#      возвращает исходные настройки KSM (safety_init в common.sh);
#   4) при ошибке серии run-all останавливается (следующие не запускаются).
# =============================================================================
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# Реестр: имя | ориентировочная длительность на ВМ этапа 0 | команда
declare -A CMD DUR DESC
reg() { CMD[$1]="$2"; DUR[$1]="$3"; DESC[$1]="$4"; }
reg inherit     "scripts/stage0-prctl-inherit.sh"   "~65 мин" "наследование prctl: fork/exec/podman (этап 0, шаг 6)"
reg jitter      "scripts/stage0-jitter.sh"          "~40 мин" "шум таймера в простое, базовая линия (этап 0, шаг 7)"
reg ksm-probe   "env DURATION=60 scripts/stage0-ksm-probe.sh" "~40 мин" "KSM на проверочной нагрузке (этап 0, шаг 5)"
reg trace-check "scripts/stage1-trace-check.sh"     "~1 мин"  "tracepoint-ы KSM и cow_ksm (этап 1, шаг 1)"
reg mps-check   "scripts/stage1-mps-check.sh"       "~3 мин"  "max_page_sharing: значения и влияние (этап 1, шаг 2)"
reg xcheck      "env SERIES=stage1-jitter-xcheck CONFIGS=own:1000,ownroot:1000,cyclictest:1000 scripts/stage0-jitter.sh" \
                "~40 мин" "расхождение timer_jitter и cyclictest (этап 1, шаг 4)"
reg fork-check  "scripts/stage1-fork-share-check.sh" "~3 мин"  "учёт KSM для страниц после fork (этап 1)"
reg cow-cost    "scripts/stage1-cow-cost.sh"        "~17 мин" "цена COW KSM-страницы (этап 1, 7.1)"
reg rehearsal   "scripts/stage1-latency-rehearsal.sh" "~100 мин" "тик 60 Гц: KSM и трасса вкл./выкл., атрибуция (этап 1, 7.4)"
reg workloads   "env SERIES=stage1-workloads NS=1,4,8 KSM_CFGS=default scripts/stage1-workloads.sh" \
                "~6-8 ч" "5 нагрузок × N, KSM по умолчанию (этап 1, 7.2; нужны образы: build-workloads.sh)"
reg factors     "env SERIES=stage1-workloads-factors WORKLOADS=python NS=8 KSM_CFGS=default,fast,adv200,adv30,mps16,mps1024 MAX_S=600 scripts/stage1-workloads.sh" \
                "~3-4 ч" "факторы KSM на Python N=8 (этап 1, 7.3)"
reg loadgen-check "scripts/stage2-loadgen-check.sh"  "~3 мин"  "проверка генератора нагрузки на эталоне (этап 2)"
reg load-pilot  "scripts/stage2-load-pilot.sh"        "~25 мин" "пилот частот запросов (этап 2); частоты для load — по его сводке"
reg load        "env RATE_NGINX=400 RATE_PYTHON=50 RATE_NODE=200 RATE_JVM=400 RATE_POSTGRES=200 SERIES=stage2-load MODES=on,off PINS=none scripts/stage2-load.sh" \
                "~7 ч"    "задержка под нагрузкой, KSM вкл./выкл., 5 нагрузок (этап 2); частоты ВМ — на железе уточнить пилотом"
reg ksmd-pin    "scripts/stage2-ksmd-pin.sh"          "~80 мин" "привязка ksmd: unpinned/split/shared (+sibling при SMT) на модели тика (этап 2)"
reg ksmd-tlb    "env SERIES=stage2-ksmd-pin-tlb DURATION=30 scripts/stage2-ksmd-pin.sh" \
                "~35 мин" "то же со счётчиками сбросов TLB (этап 2)"
ORDER=(trace-check mps-check fork-check inherit ksm-probe jitter xcheck cow-cost rehearsal workloads factors
       loadgen-check load-pilot load ksmd-pin ksmd-tlb)
DEFAULT_SET=(trace-check mps-check inherit jitter)

usage_list() {
    printf '%-12s %-9s %s\n' "серия" "время" "описание"
    for s in "${ORDER[@]}"; do printf '%-12s %-9s %s\n' "$s" "${DUR[$s]}" "${DESC[$s]}"; done
}

DRY=0
SETS=()
for a in "$@"; do
    case "$a" in
        --list) usage_list; exit 0 ;;
        --dry-run) DRY=1 ;;
        -*) echo "неизвестный ключ $a" >&2; exit 2 ;;
        *) [[ -n "${CMD[$a]:-}" ]] || { echo "неизвестная серия: $a (см. --list)" >&2; exit 2; }
           SETS+=("$a") ;;
    esac
done
(( ${#SETS[@]} )) || SETS=("${DEFAULT_SET[@]}")

echo "### Preflight"
if ! scripts/preflight.sh; then
    echo "Preflight нашёл FAIL — серии не запускаются." >&2
    exit 1
fi

echo
echo "### План (${#SETS[@]} серий):"
for s in "${SETS[@]}"; do printf '  %-12s %-9s %s\n' "$s" "${DUR[$s]}" "${CMD[$s]//,/ }"; done
if (( DRY )); then
    echo "Режим --dry-run: ничего не запускалось и не менялось."
    exit 0
fi

echo
echo "### Снимок окружения"
scripts/capture-env.sh

for s in "${SETS[@]}"; do
    echo
    echo "### $(date '+%F %T') серия $s: ${DESC[$s]}"
    # CONFIGS в реестре записан через запятые вместо пробелов (иначе он
    # разобьётся на части массива команды); здесь возвращаем пробелы.
    read -r -a cmd <<< "${CMD[$s]}"
    for i in "${!cmd[@]}"; do cmd[$i]="${cmd[$i]//,/ }"; done
    "${cmd[@]}"
    echo "### $(date '+%F %T') серия $s завершена"
done
echo
echo "Все серии завершены. Дальше: summary-скрипты и графики (см. docs/running-native.md)."
