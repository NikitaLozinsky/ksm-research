#!/usr/bin/env python3
"""Сводка тиков по прогонам и фазам с привязкой часов движков к CLOCK_MONOTONIC."""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from lib import attribution

REPO = Path(__file__).resolve().parents[1]
PERIOD_MS = {"ticksrv": 1000 / 60, "luanti": 50, "godot": 1000 / 60}


def read_ticks(path, workload, clocks):
    ticks = pd.read_csv(path)
    if len(ticks) < 2 or not np.array_equal(ticks.tick, np.arange(1, len(ticks) + 1)):
        raise ValueError(f"{path}: пропуск или повтор номера тика")
    start = ticks.start_ns.to_numpy()
    end = ticks.end_ns.to_numpy()
    if not (np.isfinite(start).all() and np.isfinite(end).all()
            and (end >= start).all() and (np.diff(start) > 0).all()):
        raise ValueError(f"{path}: некорректные метки времени")
    work_ms = (end - start) / 1e6
    interval_ms = np.r_[np.nan, np.diff(start) / 1e6]
    offset_span_us = np.nan
    if workload == "godot":
        # Linux Godot читает MONOTONIC_RAW относительно старта процесса.
        # Связываем часы движка с абсолютными RAW через пары с REALTIME.
        real = ticks.realtime_ns.to_numpy()
        anchor = (real > clocks.realtime_ns.iloc[0]) & (real < clocks.realtime_ns.iloc[-1])
        offsets = np.interp(real[anchor], clocks.realtime_ns, clocks.raw_ns) - start[anchor]
        if len(offsets) < 2 or np.ptp(offsets) > 1e6:
            raise ValueError(f"{path}: расходятся привязки часов Godot")
        offset_span_us = np.ptp(offsets) / 1000
        start = start + np.median(offsets)
        end = end + np.median(offsets)
    if workload != "ticksrv":
        # RAW и MONOTONIC могут идти с разной скоростью (коррекция частоты часов).
        start = np.interp(start, clocks.raw_ns, clocks.mono_ns)
        end = np.interp(end, clocks.raw_ns, clocks.mono_ns)
    start = start / 1e9
    return pd.DataFrame({
        "start": start, "end": end / 1e9, "previous_start": np.r_[np.nan, start[:-1]],
        "work_ms": work_ms, "interval_ms": interval_ms,
    }), offset_span_us


def metrics(ticks, period_ms):
    work = ticks.work_ms.to_numpy()
    intervals = ticks.interval_ms.dropna().to_numpy()
    if len(intervals) < 2:
        raise ValueError("В фазе недостаточно тиков")
    p50, p99, p999 = np.percentile(work, [50, 99, 99.9])
    slow_count = max(1, int(np.ceil(len(intervals) / 100)))
    slow = np.partition(intervals, len(intervals) - slow_count)[-slow_count:]
    return {
        "n_ticks": len(ticks), "n_intervals": len(intervals),
        "work_p50_ms": p50, "work_p99_ms": p99, "work_p999_ms": p999,
        "period_p50_ms": np.median(intervals), "low_1pct_tps": 1000 / np.mean(slow),
        "missed_frac": np.mean(intervals > 1.5 * period_ms),
        "jitter_std_ms": np.std(intervals, ddof=1),
        "jitter_iqr_ms": np.subtract(*np.percentile(intervals, [75, 25])),
    }


def summarize(base):
    params = json.loads((base / "params.json").read_text())["run"]
    workload = params["workload"]
    period_ms = PERIOD_MS[workload]
    clocks = pd.read_csv(base / "clocks.csv")
    if (clocks.phase.tolist() != ["launch", "pre", "merge", "steady", "end", "shutdown"]
            or any((clocks[column].diff().iloc[1:] <= 0).any()
                   for column in ("mono_ns", "raw_ns", "realtime_ns"))
            or clocks.span_ns.max() > 100_000):
        raise ValueError(f"{base}: неверные точки привязки часов")
    boundaries = clocks.set_index("phase").mono_ns / 1e9
    samples = pd.read_csv(base / "mem.csv")
    trace = params.get("trace", 1)
    if trace:
        vm = pd.read_csv(base / "vmstat.csv")
        ev = attribution.load_perf(str(base / "perf-script.txt.gz"))
    world_path = base / "world-ksm.csv"
    world = pd.read_csv(world_path) if world_path.exists() else None
    source = "world/ticks.csv" if workload == "luanti" else "ticks.csv"
    ticks_by_ctr, spans = {}, []
    phase_slack_s = 2 * period_ms / 1000
    for i in range(1, params["n"] + 1):
        ctr = f"c{i}"
        ticks, span = read_ticks(base / ctr / source, workload, clocks)
        if (ticks.start.iloc[0] > boundaries.pre - phase_slack_s
                or ticks.start.iloc[-1] < boundaries.end):
            raise ValueError(f"{base}/{ctr}: журнал не покрывает окно измерения")
        ticks_by_ctr[ctr] = ticks
        if np.isfinite(span):
            spans.append(span)

    summary, attributes = [], []
    meta = {"run": base.name, "workload": workload, "mode": params["mode"], "rep": params["rep"],
            "containers": params["n"], "godot_anchor_span_us": max(spans, default=np.nan)}
    measured = params.get("measured_ctr", "all")
    if measured != "all":
        meta.update(pin=params["pin"], measured_ctr=measured)
    knob = {"ticksrv": "tick_cpu_work", "luanti": "luanti_edits", "godot": "godot_passes"}[workload]
    if knob in params:
        meta["load_knob"] = knob
        meta["load_value"] = params[knob]
    for phase, stop in [("pre", "merge"), ("merge", "steady"), ("steady", "end")]:
        lo, hi = boundaries[phase], boundaries[stop]
        # Окно работы и окно межтикового интервала не должны пересекать границу фазы.
        parts = []
        for ticks in ticks_by_ctr.values():
            section = ticks.loc[(ticks.start >= lo) & (ticks.end < hi)].copy()
            section.loc[section.previous_start < lo, "interval_ms"] = np.nan
            parts.append(section)
        min_ticks_per_ctr = min(map(len, parts))
        phase_ticks = (pd.concat(parts, ignore_index=True) if measured == "all"
                       else parts[list(ticks_by_ctr).index(measured)])
        observed = samples.loc[samples.t_mono.between(lo, hi, inclusive="left") & (samples.n_procs > 0)]
        merging = observed.groupby("ctr").ksm_merging.max().reindex(ticks_by_ctr)
        if merging.isna().any() or min_ticks_per_ctr < 2:
            raise ValueError(f"{base}/{phase}: нет данных по всем контейнерам")
        world_max_kib = world_observed_ctrs = np.nan
        if world is not None and phase != "merge":
            rows = world.loc[world.phase == ("pre" if phase == "pre" else "end")]
            if set(rows.ctr) != set(ticks_by_ctr) or rows.ctr.duplicated().any():
                raise ValueError(f"{base}/{phase}: неполный замер отображения мира")
            world_max_kib = int(rows.world_ksm_kib.max())
            world_observed_ctrs = int((rows.world_ksm_kib > 0).sum())
        row = {**meta, "phase": phase, **metrics(phase_ticks, period_ms),
               "min_ticks_per_ctr": min_ticks_per_ctr, "ksm_min_pages": int(merging.min()),
               "ksm_median_pages": float(merging.median()), "ksm_max_pages": int(merging.max()),
               "world_ksm_max_kib": world_max_kib, "world_ksm_observed_ctrs": world_observed_ctrs}
        summary.append(row)
        if not trace:
            continue
        for metric, start_col, end_col in [("work_ms", "start", "end"),
                                          ("interval_ms", "previous_start", "start")]:
            usable = phase_ticks.dropna(subset=[metric])
            end = usable[end_col].to_numpy()
            result = attribution.analyze(end, end - usable[start_col].to_numpy(), ev, vm)
            attributes.append(result.assign(run=base.name, phase=phase, metric=metric))
    return summary, attributes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    args = parser.parse_args()
    rows, attrs = [], []
    bases = sorted(args.raw.glob("*-rep[0-9]*"))
    print(f"[сводка] 0/{len(bases)}", flush=True)
    for index, base in enumerate(bases, 1):
        summary, attributes = summarize(base)
        rows.extend(summary)
        attrs.extend(attributes)
        print(f"[сводка] {index}/{len(bases)} ({100 * index // len(bases)}%) · {base.name}", flush=True)
    if not rows:
        parser.error("нет прогонов с тиками")
    out = REPO / "results/summary" / f"{args.raw.parent.name}-{args.raw.name}"
    data = pd.DataFrame(rows)
    data.to_csv(f"{out}-per-run.csv", index=False)
    if attrs:
        pd.concat(attrs, ignore_index=True).to_csv(f"{out}-attribution.csv", index=False)
    steady = data[data.phase == "steady"]
    print(steady[["run", "n_ticks", "period_p50_ms", "ksm_min_pages", "world_ksm_observed_ctrs"]]
          .to_string(index=False))
    if args.raw.parent.name == "stage4-ticks-smoke":
        off = steady[steady["mode"] == "off"]
        on = steady[steady["mode"] == "on"]
        world_on = on.loc[on.workload == "ticksrv", "world_ksm_observed_ctrs"]
        world_off = off.loc[off.workload == "ticksrv", "world_ksm_observed_ctrs"]
        # Во время записей COW снимает слияние. Разовый smaps показывает
        # пару слитых отображений, а каждый контейнер проверяется по ряду ksm_stat.
        if (len(steady) != 6 or set(off.workload) != set(PERIOD_MS)
                or set(on.workload) != set(PERIOD_MS) or not (off.ksm_max_pages == 0).all()
                or not (on.ksm_min_pages > 0).all()
                or not (world_on >= 2).all() or not (world_off == 0).all()):
            parser.error("дымовая проверка KSM не пройдена для всех контейнеров")


if __name__ == "__main__":
    main()
