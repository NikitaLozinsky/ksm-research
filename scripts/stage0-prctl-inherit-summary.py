#!/usr/bin/env python3
"""
stage0-prctl-inherit-summary.py — сводка серии stage0-prctl-inherit.

Для каждого прогона берётся последняя JSON-строка ksm_probe (целевой процесс:
role=main или role=child) и определяется:
  inherited   флаг PR_SET_MEMORY_MERGE был у процесса при старте
              (pr_get_at_start == 1), т.е. пришёл от предков;
  merged_frac доля страниц процесса, реально участвующих в слиянии
              (ksm_merging_pages / pages) к концу удержания памяти;
  merged      merged_frac >= 0.99.
Сводка по (конфигурация, режим ksmd): n, сколько раз флаг унаследован,
сколько раз память слилась, медиана merged_frac, ошибки запуска (rc != 0).

Результаты: results/summary/stage0-prctl-inherit-per-run.csv,
            results/summary/stage0-prctl-inherit.csv, таблица в stdout.
"""
import json
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
ORDER = ["host-control", "host-fork", "host-exec", "host-fork-exec",
         "ctr-control", "ctr-outer", "ctr-inner", "ctr-systemd"]


def main() -> None:
    raw = REPO / "results/raw/stage0-prctl-inherit"
    series = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else sorted(raw.iterdir())[-1]
    rows = []
    for pj in sorted(series.glob("*-rep*.json")):
        params = json.loads(pj.read_text())["run"]
        base = pj.with_suffix("")
        rc = int(base.with_suffix(".rc").read_text()) if base.with_suffix(".rc").exists() else -1
        lines = [l for l in base.with_suffix(".jsonl").read_text().splitlines() if l.startswith("{")]
        target = json.loads(lines[-1]) if lines else {}
        pages = target.get("pages", 0) or 0
        frac = target.get("ksm_merging_pages", 0) / pages if pages else float("nan")
        rows.append({
            "config": params["config"], "scan": params["scan"], "rep": params["rep"],
            "rc": rc, "has_json": bool(lines),
            "role": target.get("role"), "pid_in_ns": target.get("pid"),
            "pr_get_at_start": target.get("pr_get_at_start"),
            "ksm_merge_any": target.get("ksm_merge_any"),
            "ksm_merging_pages": target.get("ksm_merging_pages"),
            "pages": pages, "merged_frac": frac,
            "inherited": target.get("pr_get_at_start") == 1,
            "merged": frac >= 0.99,
            "file": str(base.relative_to(REPO)),
        })
    runs = pd.DataFrame(rows)
    out = REPO / "results/summary"
    runs.insert(0, "series", str(series.relative_to(REPO)))
    runs.to_csv(out / "stage0-prctl-inherit-per-run.csv", index=False)

    agg = (runs.groupby(["config", "scan"])
           .agg(n=("rep", "size"), rc_errors=("rc", lambda s: int((s != 0).sum())),
                inherited=("inherited", "sum"), merged=("merged", "sum"),
                merged_frac_median=("merged_frac", "median"),
                merged_frac_min=("merged_frac", "min"))
           .reset_index())
    agg["config"] = pd.Categorical(agg["config"], ORDER, ordered=True)
    agg = agg.sort_values(["config", "scan"])
    agg.insert(0, "series", str(series.relative_to(REPO)))
    agg.to_csv(out / "stage0-prctl-inherit.csv", index=False)
    print(f"Серия: {series.relative_to(REPO)}")
    print(agg.drop(columns="series").to_string(index=False, float_format=lambda x: f"{x:.3f}"))


if __name__ == "__main__":
    main()
