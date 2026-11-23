#!/usr/bin/env python3
"""
stage1-cow-cost-summary.py — сводка серии stage1-cow-cost (цена COW).

По каждому прогону: длительность прохода записи (по байту в каждую страницу),
на страницу (мкс), число COW (прирост cow_ksm за проход), pages_sharing перед
проходом. По конфигурациям: медиана, Q1, Q3, min, max, n.
Плюс разность и отношение медиан «слито − не слито» на страницу для каждого
режима THP (это и есть оценка добавочной цены COW на страницу).

Проверки корректности (печатаются): в слитых конфигурациях cow_ksm должен
быть ≈ pages_sharing+pages_shared перед проходом, в контрольных — 0.

Результаты: results/summary/stage1-cow-cost-per-run.csv, stage1-cow-cost.csv.
"""
import json
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    raw = REPO / "results/raw/stage1-cow-cost"
    series = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else sorted(raw.iterdir())[-1]
    rows = []
    for pj in sorted(series.glob("*-rep*.json")):
        if pj.name.endswith(".cow.json"):
            continue
        prm = json.loads(pj.read_text())["run"]
        base = pj.with_suffix("")
        touch = next(json.loads(l) for l in base.with_suffix(".probe.jsonl").read_text().splitlines()
                     if '"event":"touch"' in l)
        cow = json.loads(base.with_suffix(".cow.json").read_text())
        rows.append({"config": prm["config"], "rep": prm["rep"],
                     "pages": touch["pages"], "duration_s": touch["duration_s"],
                     "us_per_page": touch["duration_s"] / touch["pages"] * 1e6,
                     "cow_ksm_delta": cow["cow_ksm_delta"],
                     "pages_sharing_before": cow["pages_sharing_before_touch"],
                     "file": str(base.relative_to(REPO))})
    runs = pd.DataFrame(rows).sort_values(["config", "rep"])
    out = REPO / "results/summary"
    runs.insert(0, "series", str(series.relative_to(REPO)))
    runs.to_csv(out / "stage1-cow-cost-per-run.csv", index=False)

    agg = []
    for cfg, g in runs.groupby("config"):
        for m in ["us_per_page", "duration_s", "cow_ksm_delta", "pages_sharing_before"]:
            v = g[m].astype(float)
            agg.append({"config": cfg, "metric": m, "n": len(v), "median": v.median(),
                        "q1": v.quantile(.25), "q3": v.quantile(.75), "min": v.min(), "max": v.max()})
    agg = pd.DataFrame(agg)
    agg.insert(0, "series", str(series.relative_to(REPO)))
    agg.to_csv(out / "stage1-cow-cost.csv", index=False)

    print(f"Серия: {series.relative_to(REPO)}")
    pd.set_option("display.width", 160)
    print(agg.drop(columns="series").to_string(index=False, float_format=lambda x: f"{x:.4g}"))
    med = runs.groupby("config")["us_per_page"].median()
    for thp in ("thp", "nothp"):
        m, p = med.get(f"merged-{thp}"), med.get(f"plain-{thp}")
        if m is not None and p is not None:
            print(f"{thp}: медиана на страницу слито {m:.3f} мкс, не слито {p:.3f} мкс, "
                  f"разность {m - p:.3f} мкс, отношение {m / p:.1f}×")


if __name__ == "__main__":
    main()
