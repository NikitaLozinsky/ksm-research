#!/usr/bin/env python3
"""
ksm_sampler.py — периодически снимает счётчики KSM и пишет их в CSV.

Что записывается в каждой строке (одна строка = один момент времени):
  t_s                 время от старта сэмплера, секунды (монотонные часы)
  t_mono              абсолютное CLOCK_MONOTONIC, с (общая ось с perf, vmstat_fast,
                      ctr_mem_sampler и нашими программами)
  <счётчики KSM>      все числовые файлы /sys/kernel/mm/ksm/ (pages_shared,
                      pages_sharing, full_scans, general_profit, ...)
  ksmd_cpu_ns         суммарное время ЦП потока ksmd в наносекундах
                      (первое поле /proc/<pid ksmd>/schedstat). Загрузка ksmd
                      за интервал = разность / длительность интервала.
  cow_ksm, ksm_swpin_copy, thp_split_page   счётчики из /proc/vmstat
  MemAvailable_kB, AnonPages_kB             из /proc/meminfo
  pid_*               (если задан --pid) данные отслеживаемого процесса:
                      ksm_merging_pages, ksm_rmap_items, ksm_merge_any и,
                      с флагом --pss, Pss_Anon_kB из smaps_rollup.

Pss (proportional set size) делит каждую общую страницу между всеми, кто её
использует. Поэтому падение Pss — независимая от счётчиков KSM проверка того,
что память действительно сэкономлена. Чтение smaps_rollup обходит таблицы
страниц процесса, это небольшая дополнительная нагрузка. Поэтому флаг
опциональный.

Остановка: по SIGTERM/SIGINT, по истечении --duration или (с --stop-with-pid)
когда отслеживаемый процесс завершился.

Пример:
    python3 ksm_sampler.py --out run.csv --interval 0.5 --pid 1234 --pss
"""
import argparse
import csv
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

KSM_DIR = Path("/sys/kernel/mm/ksm")
VMSTAT_KEYS = ("cow_ksm", "ksm_swpin_copy", "thp_split_page")
MEMINFO_KEYS = ("MemAvailable", "AnonPages")

stop = False


def on_signal(signum, frame):
    global stop
    stop = True


def ksm_counters() -> dict:
    """Все файлы KSM, значение которых — одно целое число."""
    out = {}
    for p in sorted(KSM_DIR.iterdir()):
        try:
            v = p.read_text().strip()
        except OSError:
            continue
        if v.lstrip("-").isdigit():
            out[p.name] = int(v)
    return out


def vmstat() -> dict:
    out = {k: 0 for k in VMSTAT_KEYS}
    with open("/proc/vmstat") as f:
        for line in f:
            k, v = line.split()
            if k in out:
                out[k] = int(v)
    return out


def meminfo() -> dict:
    out = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, rest = line.split(":", 1)
            if k in MEMINFO_KEYS:
                out[f"{k}_kB"] = int(rest.split()[0])
    return out


def ksmd_cpu_ns(pid: int) -> int:
    with open(f"/proc/{pid}/schedstat") as f:
        return int(f.read().split()[0])


def pid_stats(pid: int, pss: bool) -> dict:
    """Счётчики KSM конкретного процесса. Пустые значения, если процесс исчез."""
    out = {"pid_alive": 0, "pid_ksm_merging_pages": "", "pid_ksm_rmap_items": "",
           "pid_ksm_merge_any": ""}
    if pss:
        out["pid_Pss_Anon_kB"] = ""
    try:
        with open(f"/proc/{pid}/ksm_stat") as f:
            for line in f:
                key, _, val = line.replace(":", " ").partition(" ")
                val = val.strip()
                if key == "ksm_merging_pages":
                    out["pid_ksm_merging_pages"] = int(val)
                elif key == "ksm_rmap_items":
                    out["pid_ksm_rmap_items"] = int(val)
                elif key == "ksm_merge_any":
                    out["pid_ksm_merge_any"] = 1 if val == "yes" else 0
        if pss:
            with open(f"/proc/{pid}/smaps_rollup") as f:
                for line in f:
                    if line.startswith("Pss_Anon:"):
                        out["pid_Pss_Anon_kB"] = int(line.split()[1])
        out["pid_alive"] = 1
    except (OSError, ValueError):
        pass
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Сэмплер счётчиков KSM")
    ap.add_argument("--out", required=True, help="путь к CSV")
    ap.add_argument("--interval", type=float, default=0.5, help="период опроса, с")
    ap.add_argument("--duration", type=float, default=0, help="длительность, с (0 = до сигнала)")
    ap.add_argument("--pid", type=int, default=0, help="PID отслеживаемого процесса")
    ap.add_argument("--pss", action="store_true", help="читать Pss_Anon процесса")
    ap.add_argument("--stop-with-pid", action="store_true",
                    help="остановиться, когда отслеживаемый процесс завершится")
    args = ap.parse_args()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    ksmd = int(subprocess.run(["pgrep", "-x", "ksmd"], capture_output=True,
                              text=True).stdout.split()[0])

    t0 = time.monotonic()
    next_t = t0
    writer = None
    with open(args.out, "w", newline="", buffering=1) as f:
        while not stop:
            now = time.monotonic()
            row = {"t_s": round(now - t0, 4), "t_mono": round(time.clock_gettime(time.CLOCK_MONOTONIC), 4)}
            row.update(ksm_counters())
            row["ksmd_cpu_ns"] = ksmd_cpu_ns(ksmd)
            row.update(vmstat())
            row.update(meminfo())
            if args.pid:
                row.update(pid_stats(args.pid, args.pss))
            if writer is None:
                writer = csv.DictWriter(f, fieldnames=list(row.keys()))
                writer.writeheader()
            writer.writerow(row)

            if args.duration and now - t0 >= args.duration:
                break
            if args.pid and args.stop_with_pid and not row.get("pid_alive"):
                break
            # Расписание по абсолютному времени: ошибки не накапливаются.
            next_t += args.interval
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.monotonic()  # отстали — продолжаем без догонялок


if __name__ == "__main__":
    sys.exit(main())
