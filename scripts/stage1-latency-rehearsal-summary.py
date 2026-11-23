#!/usr/bin/env python3
"""
stage1-latency-rehearsal-summary.py — сводка репетиции задержек (этап 1, 7.4).

По каждому прогону (тик 60 Гц с обновлением «состояния»):
  - опоздание пробуждения (lateness): p50, p99, p99.9, max (мкс);
  - длительность работы тика (work): p50, p99, p99.9, max (мкс);
  - доля пропущенных тиков;
  - число COW за прогон (cow_ksm из ksm_sampler) и pages_sharing в конце.
Для прогонов с трассировкой — атрибуция (scripts/lib/attribution.py):
  всплески длительности работы тика (>= p99 прогона) против обычных тиков
  и против случайных окон той же длины: доля окон с работой ksmd, среднее
  число COW в окне.
Сравнение по конфигурациям: медиана, Q1, Q3 по повторам.

Результаты: results/summary/<префикс>-per-run.csv, <префикс>.csv
            (префикс — второй аргумент, по умолчанию stage1-latency-rehearsal).
"""
import importlib.util
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("attr", REPO / "scripts/lib/attribution.py")
attr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(attr)
warnings.filterwarnings("ignore", message="Mean of empty slice")


def q(x, p):
    return float(np.percentile(x, p)) if len(x) else float("nan")


def main() -> None:
    raw = REPO / "results/raw/stage1-latency-rehearsal"
    series = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else sorted(raw.iterdir())[-1]
    # Второй аргумент — префикс файлов сводки (для серий того же формата,
    # например stage2-ksmd-pin); по умолчанию — имя серии этапа 1.
    name = sys.argv[2] if len(sys.argv) > 2 else "stage1-latency-rehearsal"
    rows = []
    for pj in sorted(series.glob("*-rep*.json")):
        prm = json.loads(pj.read_text())["run"]
        base = pj.with_suffix("")
        d = pd.read_csv(base.with_suffix(".csv"))
        lat = d["lateness_ns"].to_numpy() / 1e3
        work = d["work_ns"].to_numpy() / 1e3
        k = pd.read_csv(base.with_suffix(".ksm.csv"))
        row = {"config": prm["config"], "rep": prm["rep"],
               "lat_p50_us": q(lat, 50), "lat_p99_us": q(lat, 99), "lat_p999_us": q(lat, 99.9),
               "lat_max_us": lat.max(),
               "work_p50_us": q(work, 50), "work_p99_us": q(work, 99), "work_p999_us": q(work, 99.9),
               "work_max_us": work.max(),
               "missed_frac": d["missed"].sum() / (len(d) + d["missed"].sum()),
               "cow_ksm": int(k["cow_ksm"].iloc[-1] - k["cow_ksm"].iloc[0]),
               "pages_sharing_end": int(k["pages_sharing"].iloc[-1])}
        perf = base.with_suffix(".perf-script.txt.gz")
        if perf.exists():
            ev = attr.load_perf(str(perf))
            vm = pd.read_csv(base.with_suffix(".vmstat.csv"))
            t_end = (d["t_wake_mono_ns"] + d["work_ns"]).to_numpy() / 1e9
            res = attr.analyze(t_end, d["work_ns"].to_numpy() / 1e9, ev, vm, q=0.99).set_index("признак")
            row.update({
                "attr_ksmd_bad": res.loc["доля окон: ksmd_any", "плохие"],
                "attr_ksmd_norm": res.loc["доля окон: ksmd_any", "обычные"],
                "attr_ksmd_rand": res.loc["доля окон: ksmd_any", "случайные_той_же_длины"],
                "attr_cow_bad": res.loc["среднее: cow", "плохие"],
                "attr_cow_norm": res.loc["среднее: cow", "обычные"],
                "attr_cow_rand": res.loc["среднее: cow", "случайные_той_же_длины"],
                "ksmd_busy_frac": res.attrs["ksmd_busy_frac"],
            })
        rows.append(row)
    runs = pd.DataFrame(rows).sort_values(["config", "rep"])
    out = REPO / "results/summary"
    runs.insert(0, "series", str(series.relative_to(REPO)))
    runs.to_csv(out / f"{name}-per-run.csv", index=False)
    metrics = [c for c in runs.columns if c not in ("series", "config", "rep")]
    agg = []
    for cfg, g in runs.groupby("config"):
        for m in metrics:
            v = g[m].astype(float).dropna()
            if len(v) == 0:
                continue
            agg.append({"config": cfg, "metric": m, "n": len(v), "median": v.median(),
                        "q1": v.quantile(.25), "q3": v.quantile(.75), "min": v.min(), "max": v.max()})
    agg = pd.DataFrame(agg)
    agg.insert(0, "series", str(series.relative_to(REPO)))
    agg.to_csv(out / f"{name}.csv", index=False)
    print(f"Серия: {series.relative_to(REPO)}")
    pd.set_option("display.width", 170)
    pv = agg.pivot_table(index="metric", columns="config", values="median")
    print(pv.to_string(float_format=lambda x: f"{x:.4g}"))


if __name__ == "__main__":
    main()
