#!/usr/bin/env bash
# =============================================================================
# preflight.sh — проверка системы перед запуском серий. ТОЛЬКО ЧТЕНИЕ:
# ничего не меняет (ни KSM, ни кэши, ни контейнеры).
#
# Зачем: на нативной системе ошибку лучше поймать до запуска, а не через
# час серии. Каждая строка — PASS / WARN / FAIL с пояснением.
#   FAIL — запускать нельзя (нет интерфейса ядра, нет инструмента, нет sudo);
#   WARN — запускать можно, но результат может исказиться (фоновая нагрузка,
#          чужие контейнеры, KSM уже кем-то используется) — решает владелец.
# Код выхода: 0 — нет FAIL, 1 — есть FAIL.
#
# Использование:  scripts/preflight.sh [мин_свободной_памяти_МиБ=3000]
# =============================================================================
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
KSM=/sys/kernel/mm/ksm
MIN_MIB="${1:-3000}"
fails=0; warns=0

pass() { printf '  PASS  %s\n' "$*"; }
warn() { printf '  WARN  %s\n' "$*"; warns=$((warns + 1)); }
fail() { printf '  FAIL  %s\n' "$*"; fails=$((fails + 1)); }

# Сравнение версий ядра: ver_ge 7.2.9 6.8 → истина
ver_ge() { [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" == "$2" ]]; }

echo "== Система"
virt="$(systemd-detect-virt 2>/dev/null || true)"
virt="${virt:-none}"
kver="$(uname -r | sed -E 's/^([0-9]+\.[0-9]+(\.[0-9]+)?).*/\1/')"
echo "  ядро $(uname -r), виртуализация: $virt, ЦП: $(nproc)"
if ver_ge "$kver" 6.8; then pass "ядро >= 6.8 (prctl и tracepoint-ы с 6.4, advisor с 6.8)"
elif ver_ge "$kver" 6.4; then warn "ядро $kver: prctl и tracepoint-ы есть, advisor_mode нет (нужно >= 6.8)"
else fail "ядро $kver < 6.4: нет PR_SET_MEMORY_MERGE и tracepoint-ов KSM"; fi

echo "== Права"
if sudo -n true 2>/dev/null; then pass "sudo без пароля работает"
else fail "sudo требует пароль: серии пишут в /sys и /proc от root (настройте sudo или запускайте вручную)"; fi

echo "== Интерфейсы KSM"
if [[ -d "$KSM" ]]; then
    for f in run pages_to_scan sleep_millisecs max_page_sharing smart_scan advisor_mode \
             advisor_target_scan_time general_profit ksm_zero_pages pages_shared pages_sharing full_scans; do
        [[ -e "$KSM/$f" ]] || { fail "нет $KSM/$f"; continue; }
    done
    pass "каталог $KSM и нужные файлы"
else
    fail "нет $KSM (ядро без CONFIG_KSM?)"
fi
[[ -e /proc/self/ksm_stat ]] && pass "/proc/<pid>/ksm_stat есть" || fail "нет /proc/<pid>/ksm_stat"
grep -q '^cow_ksm ' /proc/vmstat && pass "/proc/vmstat: cow_ksm" || fail "в /proc/vmstat нет cow_ksm"
n_tp=$(sudo -n bash -c 'ls -d /sys/kernel/tracing/events/ksm/ksm_* 2>/dev/null | wc -l' 2>/dev/null || echo 0)
if (( n_tp >= 8 )); then pass "tracepoint-ов ksm:* в tracefs: $n_tp"
else fail "tracepoint-ов ksm:* найдено $n_tp (нужно tracefs и ядро >= 6.4)"; fi

echo "== Текущее состояние KSM (наши серии его сбросят и потом восстановят)"
run=$(cat $KSM/run 2>/dev/null); shared=$(cat $KSM/pages_shared 2>/dev/null)
echo "  run=$run pages_shared=$shared pages_to_scan=$(cat $KSM/pages_to_scan 2>/dev/null) advisor_mode=$(cat $KSM/advisor_mode 2>/dev/null)"
if [[ "$run" == 1 || "${shared:-0}" -gt 0 ]]; then
    warn "KSM уже используется (run=$run, pages_shared=$shared): reset_state разъединит эти страницы на время серий"
else
    pass "KSM не используется"
fi

echo "== Инструменты"
for t in gcc python3 perf podman tmux jq cyclictest systemd-run; do
    command -v "$t" > /dev/null && pass "$t: $(command -v "$t")" || fail "нет $t"
done
python3 -I -c 'import numpy, pandas, matplotlib, scipy' 2>/dev/null \
    && pass "python: numpy, pandas, matplotlib, scipy" || fail "нет модулей python (numpy/pandas/matplotlib/scipy)"

echo "== Память и нагрузка"
avail=$(awk '/^MemAvailable:/{print int($2/1024)}' /proc/meminfo)
if (( avail >= MIN_MIB )); then pass "MemAvailable ${avail} МиБ >= ${MIN_MIB}"
else fail "MemAvailable ${avail} МиБ < ${MIN_MIB}: риск OOM"; fi
swapon --show --noheadings | sed 's/^/  swap: /'
echo "  THP: $(cat /sys/kernel/mm/transparent_hugepage/enabled)"
load1=$(cut -d' ' -f1 /proc/loadavg)
if awk -v l="$load1" -v n="$(nproc)" 'BEGIN{exit !(l < 0.25*n)}'; then pass "loadavg(1 мин) = $load1"
else warn "loadavg(1 мин) = $load1 (>= 25% ЦП): фоновая нагрузка исказит замеры"; fi
echo "  верхние потребители ЦП:"; ps -eo pcpu,comm --sort=-pcpu --no-headers | head -4 | sed 's/^/    /'

echo "== Контейнеры"
others=$(podman ps -q --filter "label!=ksm-research=1" 2>/dev/null | wc -l)
ours=$(podman ps -aq --filter "label=ksm-research=1" 2>/dev/null | wc -l)
(( others == 0 )) && pass "чужих работающих контейнеров нет" \
    || warn "работает чужих контейнеров: $others (наши серии их НЕ трогают, но они дают фоновую нагрузку)"
(( ours == 0 )) && pass "оставшихся контейнеров эксперимента нет" \
    || warn "найдено контейнеров эксперимента: $ours (остались от прерванной серии; будут удалены reset_state)"

echo "== Питание (этап 4, Л12–Л13)"
mains=""; for d in /sys/class/power_supply/*; do [[ "$(cat "$d/type" 2>/dev/null)" == Mains ]] && mains="$d/online"; done
if [[ -z "$mains" ]]; then pass "датчика сети нет (ВМ или настольный ПК)"
elif [[ "$(cat "$mains")" == 1 ]]; then pass "питание от сети"
else warn "питание от батареи: прошивка может ограничить мощность, профиль может смениться"; fi
lid=$(awk '{print $2}' /proc/acpi/button/lid/*/state 2>/dev/null | head -1)
[[ "$lid" == closed ]] && warn "крышка закрыта: режим «ИИ-агент» меняет профиль питания раз в 10 с (Л13)" \
    || pass "крышка: ${lid:-нет датчика}"
prof=$(cat /sys/firmware/acpi/platform_profile 2>/dev/null)
[[ -z "$prof" || "$prof" == performance ]] && pass "профиль прошивки: ${prof:-нет}" || warn "профиль прошивки: $prof (не performance)"
psconf="$HOME/.config/powersave/powersave.conf"
if pgrep -u "$(id -u)" -f '^/bin/bash .*/bin/power-auto$' > /dev/null && ! grep -qx 'auto_battery=off' "$psconf" 2>/dev/null; then
    warn "power-auto включит экономию при пропадании сети — запускать серии через scripts/series-guard.sh"
else pass "power-auto не включит экономию посреди серии"; fi

echo "== Диск"
free_gb=$(df -BG --output=avail "$REPO" | tail -1 | tr -dc '0-9')
(( free_gb >= 5 )) && pass "свободно на диске репозитория: ${free_gb} ГиБ" || warn "свободно только ${free_gb} ГиБ"

echo
echo "Итог: FAIL=$fails, WARN=$warns"
(( fails == 0 ))
