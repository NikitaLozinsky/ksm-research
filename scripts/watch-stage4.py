#!/usr/bin/env python3
"""Однострочный монитор оставшихся серий этапа 4. Запуск из fish: python3 scripts/watch-stage4.py."""
import argparse
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Moscow")
STAGES = {
    "b": ("Б", 90, 235),
    "v": ("В", 40, 180),
}
ANALYSIS = (b"/stage4-ticks-summary.py", b"/stage4-finalize.py")


def analysis_status(parent, status, elapsed):
    parts = [f"расчёт {duration(elapsed)}"]
    log = status.with_suffix(".log")
    try:
        with log.open("rb") as file:
            size = file.seek(0, os.SEEK_END)
            file.seek(max(0, size - 8192))
            lines = file.read().decode("utf-8", errors="replace").splitlines()
            if update := next((line for line in reversed(lines) if line.startswith("[сводка] ")), ""):
                parts.append(update.removeprefix("[сводка] "))
    except OSError:
        pass
    try:
        pids = (Path(f"/proc/{parent}/task/{parent}") / "children").read_text().split()
    except OSError:
        pids = []
    for pid in pids:
        proc = Path("/proc") / pid
        try:
            args = (proc / "cmdline").read_bytes().split(b"\0")
            if not any(arg.endswith(ANALYSIS) for arg in args):
                continue
            fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
            cpu = (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
            state = {"R": "на ЦП", "S": "ожидание", "D": "ожидание ввода-вывода"}
            parts.append(f"CPU {duration(cpu)} · {state.get(fields[0], fields[0])}")
            break
        except (OSError, ValueError, IndexError):
            continue
    return " · ".join(parts)


def duration(seconds):
    hours, rest = divmod(max(0, int(seconds)), 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def read_status(path, now):
    pid, start, changed, done, total, eta, label = path.read_text().strip().split("\t", 6)
    pid, start, changed, done, total, eta = map(int, (pid, start, changed, done, total, eta))
    cmdline = Path(f"/proc/{pid}/cmdline")
    # PID мог быть занят другим процессом после перезапуска компьютера.
    alive = eta >= 0 and cmdline.exists() and b"/stage4-ticks" in cmdline.read_bytes()
    age = max(0, (now if alive else changed) - start)
    left = max(0, eta - now + changed) if alive and eta > 0 else None
    if alive and ("сводки" in label or "анализ" in label):
        label = f"{label} · {analysis_status(pid, path, now - changed)}"
    return alive, age, changed, done, total, eta, left, label


def snapshot(repo, now):
    elapsed = 0
    upcoming = 0
    counts = []
    active, latest = [], []
    for tag, (stage, total, nominal) in STAGES.items():
        raw = repo / "results/raw" / f"stage4-ticks-block-{tag}"
        keys = {marker.parent.name for marker in raw.glob("*/*/complete")}
        done = min(len(keys), total)
        counts.append((stage, done, total))
        upcoming += (total - done) * nominal
        for path in (repo / "results/logs").glob(f"ticks-block-{tag}-*.status"):
            try:
                alive, age, changed, current, chunk, eta, left, label = read_status(path, now)
            except (OSError, ValueError):
                continue
            elapsed += age
            latest.append((changed, stage, eta, label))
            if alive:
                active.append((stage, current, chunk, left, label))
                if left is not None:
                    upcoming += left - (chunk - current) * nominal
    total = sum(item[2] for item in counts)
    done = sum(item[1] for item in counts)
    stages = " · ".join(f"{stage} {n}/{limit}" for stage, n, limit in counts)
    if active:
        stage, current, chunk, left, label = active[0]
        state = f"{stage}, часть {current}/{chunk} · {label}"
        if left:
            finish = datetime.fromtimestamp(now + left, TZ).strftime("%H:%M")
            state += f" · конец части ~{finish}"
    elif done == total:
        state = "Все прогоны завершены"
    elif latest:
        _, stage, eta, label = max(latest)
        state = f"{stage} · {label}" if eta == -2 else f"Пауза · последняя часть {stage}"
        if eta >= 0:
            state = f"{stage} · прервано во время стадии «{label}»"
    else:
        state = "Ожидаю первый запуск"
    return (f"{done}/{total} ({100 * done // total}%) · {stages} · "
            f"прошло {duration(elapsed)} · осталось ~{duration(upcoming)} машинного времени · {state}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="показать один снимок состояния")
    args = parser.parse_args()
    try:
        while True:
            line = snapshot(REPO, int(time.time()))
            if args.once:
                print(line)
                return
            print(f"\r\033[K{line}", end="", flush=True)
            time.sleep(3)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
