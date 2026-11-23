#!/usr/bin/env python3
"""
vmstat_fast.py — частый опрос счётчиков KSM из /proc/vmstat с метками
CLOCK_MONOTONIC.

Зачем: для copy-on-write KSM-страниц в ядре 7.2.9 нет tracepoint-а, есть
только счётчик cow_ksm (mm/memory.c, do_wp_page). Чтобы привязать COW к
моментам всплесков задержки, счётчик опрашивается часто (по умолчанию раз в
10 мс), и в CSV пишутся только СТРОКИ С ИЗМЕНЕНИЯМИ (плюс первая и последняя),
чтобы файлы не росли впустую.

Столбцы: t_mono (CLOCK_MONOTONIC, с — те же часы, что у perf -k CLOCK_MONOTONIC
и у наших программ), cow_ksm, ksm_swpin_copy.
Разрешение по времени ≈ интервал опроса: событие COW известно с точностью
«между двумя соседними строками».
"""
import argparse
import signal
import time

KEYS = ("cow_ksm", "ksm_swpin_copy")
stop = False


def on_signal(signum, frame):
    global stop
    stop = True


def read_counters(f) -> tuple:
    f.seek(0)
    vals = dict.fromkeys(KEYS, 0)
    for line in f:
        k, _, v = line.partition(" ")
        if k in vals:
            vals[k] = int(v)
    return tuple(vals[k] for k in KEYS)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--interval", type=float, default=0.01)
    ap.add_argument("--duration", type=float, default=0)
    args = ap.parse_args()
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    t0 = time.clock_gettime(time.CLOCK_MONOTONIC)
    nxt = t0
    last = None
    with open("/proc/vmstat") as vm, open(args.out, "w", buffering=1) as out:
        out.write("t_mono," + ",".join(KEYS) + "\n")
        while not stop:
            t = time.clock_gettime(time.CLOCK_MONOTONIC)
            cur = read_counters(vm)
            done = args.duration and t - t0 >= args.duration
            if cur != last or done:
                out.write(f"{t:.6f}," + ",".join(map(str, cur)) + "\n")
                last = cur
            if done:
                break
            nxt += args.interval
            d = nxt - time.clock_gettime(time.CLOCK_MONOTONIC)
            if d > 0:
                time.sleep(d)
            else:
                nxt = time.clock_gettime(time.CLOCK_MONOTONIC)


if __name__ == "__main__":
    main()
