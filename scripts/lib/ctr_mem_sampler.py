#!/usr/bin/env python3
"""
ctr_mem_sampler.py — периодический замер памяти контейнеров эксперимента.

Для каждого контейнера (аргументы вида ИМЯ=PID, где PID — init-процесс
контейнера из `podman inspect --format {{.State.Pid}}`; PID узнаёт вызывающий
скрипт от имени пользователя, а сэмплер можно запускать через sudo — так
smaps_rollup процессов rootless-контейнера читается без проблем с правами):
  - cgroup находится через /proc/<PID>/cgroup;
  - процессы контейнера — из cgroup.procs этой cgroup;
  - pss_anon_kB    — сумма Pss_Anon (smaps_rollup) по всем процессам. Pss делит
                     общую страницу между всеми, кто её использует, поэтому
                     показывает реальную долю памяти после слияния KSM;
  - rss_anon_kB    — сумма RssAnon (status): НЕ уменьшается от KSM (общая
                     страница считается каждому), нужен для сравнения;
  - ksm_merging    — сумма ksm_merging_pages по процессам;
  - cg_anon_kB, cg_current_kB — memory.stat:anon и memory.current cgroup
                     (как их учитывает memcg при KSM — проверяется по данным).
Пишется одна строка на контейнер на отсчёт: t_mono, ctr, n_procs, ...

Чтение smaps_rollup обходит таблицы страниц (нагрузка), поэтому интервал по
умолчанию 5 с.

Использование:
  sudo python3 -I ctr_mem_sampler.py --out mem.csv --interval 5 c1=12345 c2=12399 ...
Останавливается по SIGTERM/SIGINT или когда все контейнеры исчезли.
"""
import argparse
import csv
import signal
import time
from pathlib import Path

stop = False


def on_signal(s, f):
    global stop
    stop = True


def cgroup_dir(pid: int) -> Path | None:
    try:
        for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines():
            if line.startswith("0::"):
                return Path("/sys/fs/cgroup") / line[3:].lstrip("/")
    except OSError:
        return None
    return None


def read_kv(path: Path, key: str) -> int:
    try:
        for line in path.read_text().splitlines():
            k, _, v = line.replace(":", " ").partition(" ")
            if k == key:
                return int(v.split()[0])
    except (OSError, ValueError, IndexError):
        pass
    return 0


def sample_ctr(cg: Path) -> dict:
    procs = []
    try:
        procs = [int(x) for x in (cg / "cgroup.procs").read_text().split()]
    except OSError:
        pass
    pss = rss = merging = 0
    for p in procs:
        pss += read_kv(Path(f"/proc/{p}/smaps_rollup"), "Pss_Anon")
        rss += read_kv(Path(f"/proc/{p}/status"), "RssAnon")
        merging += read_kv(Path(f"/proc/{p}/ksm_stat"), "ksm_merging_pages")
    return {"n_procs": len(procs), "pss_anon_kB": pss, "rss_anon_kB": rss,
            "ksm_merging": merging,
            "cg_anon_kB": read_kv(cg / "memory.stat", "anon") // 1024,
            "cg_current_kB": int((cg / "memory.current").read_text()) // 1024
            if (cg / "memory.current").exists() else 0}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("ctrs", nargs="+", help="ИМЯ=PID")
    a = ap.parse_args()
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    cgs = {}
    for item in a.ctrs:
        name, _, pid = item.partition("=")
        cg = cgroup_dir(int(pid)) if pid.isdigit() else None
        if cg is not None:
            cgs[name] = cg

    fields = ["t_mono", "ctr", "n_procs", "pss_anon_kB", "rss_anon_kB", "ksm_merging",
              "cg_anon_kB", "cg_current_kB"]
    nxt = time.clock_gettime(time.CLOCK_MONOTONIC)
    with open(a.out, "w", newline="", buffering=1) as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        while not stop:
            t = time.clock_gettime(time.CLOCK_MONOTONIC)
            alive = 0
            for cid, cg in cgs.items():
                if not cg.exists():
                    continue
                alive += 1
                w.writerow({"t_mono": f"{t:.3f}", "ctr": cid, **sample_ctr(cg)})
            if alive == 0:
                break
            nxt += a.interval
            d = nxt - time.clock_gettime(time.CLOCK_MONOTONIC)
            if d > 0:
                time.sleep(d)
            else:
                nxt = time.clock_gettime(time.CLOCK_MONOTONIC)


if __name__ == "__main__":
    main()
