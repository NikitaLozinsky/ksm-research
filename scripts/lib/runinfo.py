#!/usr/bin/env python3
"""
runinfo.py — печатает в stdout JSON с параметрами окружения и прогона.

Вызывается из common.sh (функция write_params). Аргументы — пары ключ=значение,
которые описывают конкретный прогон (режим, номер повтора, объём памяти...).
Значения, похожие на числа, записываются как числа.

Пример:
    python3 runinfo.py experiment=ksm-probe mode=prctl rep=3 mib=256
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def read(path: str) -> str:
    """Прочитать файл и вернуть его содержимое без перевода строки ('' если нет)."""
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def sh(*cmd: str) -> str:
    """Выполнить команду и вернуть её вывод ('' при ошибке)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout.strip()
    except OSError:
        return ""


def parse_value(v: str):
    """'10' -> 10, '0.5' -> 0.5, остальное остаётся строкой."""
    for conv in (int, float):
        try:
            return conv(v)
        except ValueError:
            pass
    return v


def main() -> None:
    repo = Path(__file__).resolve().parents[2]

    run_args = {}
    for arg in sys.argv[1:]:
        key, _, value = arg.partition("=")
        run_args[key] = parse_value(value)

    # Все файлы /sys/kernel/mm/ksm/ — и настройки, и счётчики на момент вызова.
    ksm = {p.name: parse_value(read(str(p))) for p in sorted(Path("/sys/kernel/mm/ksm").iterdir())}

    # Состояние git: коммит и есть ли незакоммиченные изменения (dirty).
    commit = sh("git", "-C", str(repo), "rev-parse", "--short", "HEAD") or "none"
    dirty = bool(sh("git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"))

    meminfo = {}
    for line in read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        meminfo[k] = v.strip()

    info = {
        "run": run_args,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "host": {
            "hostname": read("/proc/sys/kernel/hostname"),
            "virt": sh("systemd-detect-virt") or "none",
            "kernel": os.uname().release,
            "nproc": os.cpu_count(),
            "mem_total": meminfo.get("MemTotal", ""),
            "swap_total": meminfo.get("SwapTotal", ""),
            "swaps": read("/proc/swaps"),
            "clocksource": read("/sys/devices/system/clocksource/clocksource0/current_clocksource"),
            "thp_enabled": read("/sys/kernel/mm/transparent_hugepage/enabled"),
            "thp_defrag": read("/sys/kernel/mm/transparent_hugepage/defrag"),
            "loadavg": read("/proc/loadavg"),
        },
        "ksm": ksm,
        "git": {"commit": commit, "dirty": dirty},
    }
    json.dump(info, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
