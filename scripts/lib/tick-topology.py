"""Подобрать логические ЦП для измеряемого тика, ksmd и фоновых реплик."""
import os
from pathlib import Path


def siblings(cpu):
    path = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list")
    result = set()
    for field in path.read_text().strip().split(","):
        first, _, last = field.partition("-")
        result.update(range(int(first), int(last or first) + 1))
    return result


def main():
    allowed = os.sched_getaffinity(0)
    ksmd = max(allowed)
    same_core = siblings(ksmd) & allowed
    other_cores = allowed - same_core
    if len(same_core) < 2 or not other_cores:
        raise SystemExit("Блок В требует SMT и отдельного физического ядра")
    split = min(other_cores)
    peers = other_cores - siblings(split)
    if not peers:
        raise SystemExit("Блок В требует ещё одного ядра для фоновых реплик")
    print(ksmd, min(same_core - {ksmd}), split, ",".join(map(str, sorted(peers))))


if __name__ == "__main__":
    main()
