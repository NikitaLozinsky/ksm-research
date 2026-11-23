#!/usr/bin/env python3
"""
hw_sampler.py — частота ЦП, температура и троттлинг во время серии (этап 3).

Зачем: на ноутбуке турбобуст и нагрев меняют скорость ЦП в ходе серии.
На ВМ этого не было видно (частотой управлял хост), на железе это главный
«шум хоста». Сэмплер ничего не меняет, только читает /sys.

Запускается из safety_init (scripts/lib/common.sh) на всю серию; при выходе
файл копируется в каталог серии как hw.csv. Время — CLOCK_MONOTONIC, как у
остальных сэмплеров и perf, поэтому строки можно сопоставить с прогонами.

Столбцы:
  t_mono_ns            момент замера (CLOCK_MONOTONIC, нс)
  freq_mhz_cpuN        текущая частота логического ЦП N (scaling_cur_freq)
  pkg_temp_c           температура пакета (thermal_zone типа x86_pkg_temp)
  pkg_throttle_count   накопленный счётчик троттлинга пакета (cpu0)
  core_throttle_count  сумма счётчиков троттлинга ядер по всем ЦП
С этапа 4 (Л13: перебои разъёма зарядки, профиль питания менялся посреди
серии) — условия питания раз в секунду:
  ac_online            1 — питание от сети, 0 — от батареи (источник типа Mains)
  lid                  open / closed (/proc/acpi/button/lid/*/state)
  platform_profile     профиль прошивки (/sys/firmware/acpi/platform_profile:
                       performance / balanced / low-power и т. п.)
  powersave_mode       режим ~/bin/powersave ($XDG_RUNTIME_DIR/powersave/mode;
                       off / standard / extreme, пусто — нет файла)
Нет датчика — пустое значение (например, в ВМ).

  hw_sampler.py --out hw.csv [--interval 1]
"""
import argparse
import glob
import os
import signal
import sys
import time

stop = False


def on_term(signum, frame):
    global stop
    stop = True


def read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def find_pkg_zone():
    for z in sorted(glob.glob("/sys/class/thermal/thermal_zone*")):
        if read(f"{z}/type") == "x86_pkg_temp":
            return f"{z}/temp"
    return None


def find_mains():
    for d in sorted(glob.glob("/sys/class/power_supply/*")):
        if read(f"{d}/type") == "Mains":
            return f"{d}/online"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--interval", type=float, default=1.0)
    a = ap.parse_args()
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    cpus = sorted(int(p.rsplit("cpu", 1)[1])
                  for p in glob.glob("/sys/devices/system/cpu/cpu[0-9]*"))
    zone = find_pkg_zone()
    freq_paths = [f"/sys/devices/system/cpu/cpu{c}/cpufreq/scaling_cur_freq" for c in cpus]
    core_thr = [f"/sys/devices/system/cpu/cpu{c}/thermal_throttle/core_throttle_count" for c in cpus]
    pkg_thr = "/sys/devices/system/cpu/cpu0/thermal_throttle/package_throttle_count"
    mains = find_mains()
    lids = sorted(glob.glob("/proc/acpi/button/lid/*/state"))
    prof = "/sys/firmware/acpi/platform_profile"
    ps_mode = os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "powersave/mode")

    with open(a.out, "w", buffering=1) as f:
        f.write(",".join(["t_mono_ns"] + [f"freq_mhz_cpu{c}" for c in cpus]
                         + ["pkg_temp_c", "pkg_throttle_count", "core_throttle_count",
                            "ac_online", "lid", "platform_profile", "powersave_mode"]) + "\n")
        nxt = time.monotonic()
        while not stop:
            t = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
            fr = []
            for p in freq_paths:
                v = read(p)
                fr.append(str(int(v) // 1000) if v else "")
            temp = read(zone) if zone else ""
            temp = f"{int(temp) / 1000:.1f}" if temp else ""
            ct = [read(p) for p in core_thr]
            ct_sum = str(sum(int(x) for x in ct if x)) if any(ct) else ""
            lid = read(lids[0]).split()[-1] if lids and read(lids[0]) else ""
            f.write(",".join([str(t)] + fr + [temp, read(pkg_thr), ct_sum,
                                              read(mains) if mains else "", lid,
                                              read(prof), read(ps_mode)]) + "\n")
            nxt += a.interval
            time.sleep(max(0.0, nxt - time.monotonic()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
