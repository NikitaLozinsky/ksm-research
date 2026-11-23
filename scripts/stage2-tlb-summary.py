#!/usr/bin/env python3
"""
stage2-tlb-summary.py — сводка серии stage2-ksmd-pin-tlb: межпроцессорные
сбросы TLB и вызовы функций на других ЦП против хвоста работы тика.

По прогону (модель тика 60 Гц, KSM вкл., привязка unpinned / split / shared):
  tlb_total_per_s  прирост строки «TLB: TLB shootdowns» /proc/interrupts по всем
                   vCPU за прогон, в секунду;
  cal_total_per_s  то же для «CAL: Function call interrupts»;
  tlb_app_per_s    прирост TLB только на vCPU тика (app_cpus) — сколько раз ЦП
                   тика прерывали ради сброса TLB;
  work_p50/p99/p999_us  длительность работы тика (из CSV timer_jitter);
  cow_ksm          число COW за прогон.
По вариантам: медиана, Q1, Q3, n.
Результат: results/summary/stage2-ksmd-pin-tlb-irq.csv (+ -per-run.csv).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]


def cpus(spec: str, n: int) -> list[int]:
    out = []
    for part in str(spec).split(","):
        if "-" in part:
            a, b = part.split("-"); out += list(range(int(a), int(b) + 1))
        elif part.strip() != "":
            out.append(int(part))
    return [c for c in out if c < n]


def read_irq(f: Path) -> dict:
    d = {}
    for line in f.read_text().splitlines():
        when, rest = line.split(None, 1)
        name, vals = rest.split(":", 1)
        nums = [int(x) for x in vals.split() if x.isdigit()]
        d[(when, name.strip())] = np.array(nums)
    return d


def main():
    raw = REPO / "results/raw/stage2-ksmd-pin-tlb"
    series = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else sorted(raw.iterdir())[-1]
    rows = []
    for pj in sorted(series.glob("*-rep*.json")):
        prm = json.loads(pj.read_text())["run"]
        base = pj.with_suffix("")
        irq = read_irq(base.with_suffix(".irq"))
        d = pd.read_csv(base.with_suffix(".csv"))
        k = pd.read_csv(base.with_suffix(".ksm.csv"))
        dur = float(prm["duration_s"])
        tlb = irq[("after", "TLB")] - irq[("before", "TLB")]
        cal = irq[("after", "CAL")] - irq[("before", "CAL")]
        app = cpus(prm["app_cpus"], len(tlb))
        w = d["work_ns"].to_numpy() / 1e3
        rows.append({"config": prm["config"], "rep": prm["rep"],
                     "tlb_total_per_s": tlb.sum() / dur, "cal_total_per_s": cal.sum() / dur,
                     "tlb_app_per_s": tlb[app].sum() / dur,
                     "work_p50_us": np.percentile(w, 50), "work_p99_us": np.percentile(w, 99),
                     "work_p999_us": np.percentile(w, 99.9),
                     "cow_ksm": int(k["cow_ksm"].iloc[-1] - k["cow_ksm"].iloc[0]),
                     "file": str(base.relative_to(REPO))})
    R = pd.DataFrame(rows).sort_values(["config", "rep"])
    out = REPO / "results/summary"
    R.insert(0, "series", str(series.relative_to(REPO)))
    R.to_csv(out / "stage2-ksmd-pin-tlb-irq-per-run.csv", index=False)
    agg = []
    for c, g in R.groupby("config"):
        for m in ["tlb_total_per_s", "tlb_app_per_s", "cal_total_per_s", "work_p50_us", "work_p99_us",
                  "work_p999_us", "cow_ksm"]:
            v = g[m].astype(float)
            agg.append({"config": c, "metric": m, "n": len(v), "median": v.median(),
                        "q1": v.quantile(.25), "q3": v.quantile(.75), "min": v.min(), "max": v.max()})
    A = pd.DataFrame(agg)
    A.insert(0, "series", str(series.relative_to(REPO)))
    A.to_csv(out / "stage2-ksmd-pin-tlb-irq.csv", index=False)
    print(f"Серия: {series.relative_to(REPO)}")
    for m, g in A.groupby("metric", sort=False):
        print(f"{m:16s}", "  ".join(f"{r.config}: {r['median']:.4g} [{r.q1:.4g}–{r.q3:.4g}]" for _, r in g.iterrows()))


if __name__ == "__main__":
    main()
