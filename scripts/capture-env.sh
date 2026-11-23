#!/usr/bin/env bash
# =============================================================================
# capture-env.sh — фиксирует окружение, в котором проводятся эксперименты.
#
# Зачем: чтобы любой результат можно было связать с точной версией ядра,
# пакетов, параметрами KSM, памяти, swap и виртуализации. Без этого
# сравнение «ВМ против нативной системы» на поздних этапах невозможно.
#
# Скрипт НЕ зависит от VirtualBox: тип виртуализации определяется
# автоматически (systemd-detect-virt). На нативной машине он запишет "none".
#
# Использование:
#   scripts/capture-env.sh            # каталог выбирается автоматически:
#                                     #   env/vm-<тип>/  или  env/native/
#   scripts/capture-env.sh env/mylabel   # явный каталог
#
# Требует sudo только для dmidecode и dmesg (если нет прав — пропускает).
# =============================================================================
set -u   # ошибка при обращении к неопределённой переменной

# Корень репозитория = каталог на уровень выше, чем этот скрипт.
REPO="$(cd "$(dirname "$0")/.." && pwd)"

# --- Выбор каталога для записи ----------------------------------------------
VIRT="$(systemd-detect-virt 2>/dev/null || true)"
VIRT="${VIRT:-none}"   # на железе systemd-detect-virt пишет "none" и выходит с кодом 1
if [[ $# -ge 1 ]]; then
    OUT="$1"
elif [[ "$VIRT" == "none" ]]; then
    OUT="$REPO/env/native"
else
    OUT="$REPO/env/vm-$VIRT"
fi
mkdir -p "$OUT"
echo "Записываю окружение в: $OUT"

# Вспомогательная функция: выполнить команду и сохранить её вывод в файл.
# Первая строка файла — сама команда (чтобы было видно, откуда данные).
# Ошибки не прерывают скрипт: отсутствующий инструмент просто отмечается.
save() {
    local file="$1"; shift
    { echo "# \$ $*"; "$@" 2>&1 || echo "# (команда завершилась с ошибкой или недоступна)"; } > "$OUT/$file"
}

# Аналог save для чтения набора файлов вида «имя=значение».
dump_dir() {
    local file="$1" dir="$2"
    {
        echo "# содержимое $dir (снято $(date -Is))"
        for f in "$dir"/*; do
            [[ -f "$f" ]] || continue
            printf '%s=%s\n' "$(basename "$f")" "$(cat "$f" 2>/dev/null | tr '\n' ' ' | sed 's/ $//')"
        done
    } > "$OUT/$file"
}

# --- Общие сведения ----------------------------------------------------------
{
    echo "date=$(date -Is)"
    echo "hostname=$(cat /proc/sys/kernel/hostname)"
    echo "virt=$VIRT"
    echo "kernel=$(uname -r)"
} > "$OUT/summary.txt"

save uname.txt          uname -a
save os-release.txt     cat /etc/os-release
save cmdline.txt        cat /proc/cmdline
save lscpu.txt          lscpu
save nproc.txt          nproc
save free.txt           free -h
save meminfo.txt        cat /proc/meminfo
save vmstat.txt         cat /proc/vmstat
save sysctl-vm.txt      sysctl vm
save sysctl-kernel-sched.txt sysctl kernel.sched_autogroup_enabled kernel.timer_migration kernel.numa_balancing

# --- Swap и zram -------------------------------------------------------------
# zram — это swap в сжатой оперативной памяти. Он влияет на учёт памяти,
# поэтому его параметры фиксируются отдельно.
save swap.txt           swapon --show --bytes
save zram.txt           zramctl --output-all
for z in /sys/block/zram*; do
    [[ -d "$z" ]] && dump_dir "zram-$(basename "$z")-sysfs.txt" "$z"
done

# --- KSM ---------------------------------------------------------------------
dump_dir ksm-sysfs.txt /sys/kernel/mm/ksm
grep -E 'ksm|thp_split|thp_fault' /proc/vmstat > "$OUT/vmstat-ksm-thp.txt"

# --- Transparent Huge Pages ---------------------------------------------------
# THP важны: KSM работает с 4-КиБ страницами, и huge-страницы ему приходится
# разбивать. Режим THP заметно влияет на поведение KSM.
dump_dir thp-sysfs.txt /sys/kernel/mm/transparent_hugepage

# --- Конфигурация ядра (только значимые для работы опции) ---------------------
if [[ -r /proc/config.gz ]]; then
    zgrep -E '^(# )?CONFIG_(KSM|HZ|HZ_[0-9]+|PREEMPT[A-Z_]*|NO_HZ[A-Z_]*|TRANSPARENT_HUGEPAGE[A-Z_]*|ZRAM|ZSWAP|NUMA|SCHED_[A-Z_]*|CGROUPS|MEMCG)[ =]' \
        /proc/config.gz > "$OUT/kernel-config.txt"
fi

# --- Таймеры и частота ЦП ----------------------------------------------------
dump_dir clocksource.txt /sys/devices/system/clocksource/clocksource0
if [[ -d /sys/devices/system/cpu/cpu0/cpufreq ]]; then
    dump_dir cpufreq-cpu0.txt /sys/devices/system/cpu/cpu0/cpufreq
else
    echo "cpufreq недоступен (типично для ВМ)" > "$OUT/cpufreq-cpu0.txt"
fi
if [[ -d /sys/devices/system/cpu/cpuidle ]]; then
    dump_dir cpuidle.txt /sys/devices/system/cpu/cpuidle
fi

# --- Железо (на ВМ этого нет или не видно) -------------------------------------
# Топология SMT: какие логические ЦП делят одно физическое ядро. Нужна для
# выбора ЦП в сериях с привязкой (ksmd-pin): «соседи по SMT» делят L1/L2.
{
    echo "# логический ЦП: братья по SMT (thread_siblings_list), ядро (core_id)"
    for c in /sys/devices/system/cpu/cpu[0-9]*; do
        [[ -r "$c/topology/thread_siblings_list" ]] || continue
        echo "${c##*/}: siblings=$(cat "$c/topology/thread_siblings_list") core_id=$(cat "$c/topology/core_id")"
    done
    echo "smt_active=$(cat /sys/devices/system/cpu/smt/active 2>/dev/null)"
} > "$OUT/cpu-topology.txt"
# Драйвер частоты Intel: турбобуст (no_turbo), пределы в процентах.
[[ -d /sys/devices/system/cpu/intel_pstate ]] && dump_dir intel-pstate.txt /sys/devices/system/cpu/intel_pstate
# Режимы питания: ASUS (0 — обычный, 1 — производительный, 2 — тихий),
# power-profiles-daemon, сеть питания. Скрипты серий их не меняют.
{
    echo "asus_throttle_thermal_policy=$(cat /sys/devices/platform/asus-nb-wmi/throttle_thermal_policy 2>/dev/null)"
    echo "power_profile=$(powerprofilesctl get 2>/dev/null)"
    for ps in /sys/class/power_supply/*; do
        [[ -r "$ps/online" ]] && echo "${ps##*/}_online=$(cat "$ps/online")"
        [[ -r "$ps/capacity" ]] && echo "${ps##*/}_capacity=$(cat "$ps/capacity")"
    done
    for c in /sys/devices/system/cpu/cpu[0-9]*/cpufreq; do
        echo "${c%/cpufreq}: governor=$(cat "$c/scaling_governor" 2>/dev/null) epp=$(cat "$c/energy_performance_preference" 2>/dev/null)" | sed 's|.*/||'
    done
} > "$OUT/power.txt"
# Фон: 15 самых активных процессов в момент снимка (шум для замеров задержки).
save top-processes.txt  ps -eo pid,pcpu,rss,comm --sort=-pcpu --no-headers
sed -i '17,$d' "$OUT/top-processes.txt"

# --- Виртуализация -------------------------------------------------------------
{
    echo "systemd-detect-virt=$VIRT"
    echo "product_name=$(cat /sys/class/dmi/id/product_name 2>/dev/null)"
    echo "sys_vendor=$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null)"
    echo "bios_version=$(cat /sys/class/dmi/id/bios_version 2>/dev/null)"
    echo "# строки dmesg про гипервизор/таймеры:"
    sudo -n dmesg 2>/dev/null | grep -iE 'hypervisor|kvm-clock|tsc|virtualbox|vbox|clocksource' | head -40
} > "$OUT/virt.txt"

# --- Контейнеры ----------------------------------------------------------------
save cgroup.txt         stat -fc %T /sys/fs/cgroup
# JSON пишется без строки-заголовка, чтобы файл оставался корректным JSON.
podman info --format json > "$OUT/podman-info.json" 2>/dev/null || echo '{}' > "$OUT/podman-info.json"

# --- Версии пакетов ------------------------------------------------------------
# Список того, что используется в экспериментах. Отсутствующий пакет
# будет помечен pacman как "not found".
PKGS=(linux linux-docs glibc gcc python python-numpy python-pandas python-matplotlib
      python-scipy podman crun conmon stress-ng sysstat perf asciinema tmux jq
      nodejs jdk-openjdk htop man-pages)
pacman -Q "${PKGS[@]}" > "$OUT/packages.txt" 2>&1
pacman -Q > "$OUT/packages-all.txt"

echo "Готово. Файлов записано: $(ls "$OUT" | wc -l)"
