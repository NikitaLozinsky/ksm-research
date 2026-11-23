#!/usr/bin/env python3
"""
stage3-hw-summary.py — частота, температура и троттлинг по каждому прогону (этап 3).

Зачем: hw_sampler.py пишет hw.csv на всю серию (раз в 1 с). Чтобы понять, не
попал ли троттлинг в прогоны одного варианта (и не исказил ли сравнение), окно
каждого прогона берётся из его журнала тиков (<вариант>-repNN.csv, столбец
t_wake_mono_ns — CLOCK_MONOTONIC, как в hw.csv) и по этому окну считается:
  throttle_pkg     прирост счётчика троттлинга пакета за прогон
  throttle_core    прирост суммы счётчиков троттлинга ядер
  temp_max_c       максимальная температура пакета
  temp_median_c    медианная температура
  freq_app_mhz     медианная частота ЦП, на которых шёл тик (app_cpus из .json)
  freq_app_max_mhz медиана по секундам максимальной частоты среди этих ЦП (тик
                   идёт на одном из них; простаивающие соседи тянут медиану вниз)

Серии: stage2-ksmd-pin, stage1-latency-rehearsal (журналы тиков есть у обеих).
Результат: results/summary/stage3-hw-per-run.csv + stage3-hw.csv (медиана/IQR).

  scripts/stage3-hw-summary.py <каталог серии> [<каталог серии> ...]
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]


def cpus(spec, ncpu):
    """'0,1,2,4-6' → [0,1,2,4,5,6]; '0-7' и пусто — все ЦП."""
    if not spec:
        return list(range(ncpu))
    out = []
    for part in str(spec).split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def main():
    rows = []
    for d in map(Path, sys.argv[1:]):
        d = d.resolve()
        hw = pd.read_csv(d / "hw.csv")
        fcols = [c for c in hw.columns if c.startswith("freq_mhz_cpu")]
        for js in sorted(d.glob("*-rep[0-9][0-9].json")):
            p = json.loads(js.read_text())["run"]
            log = js.with_suffix(".csv")
            if not log.exists():
                continue
            t = pd.read_csv(log, usecols=["t_wake_mono_ns"])["t_wake_mono_ns"]
            w = hw[(hw.t_mono_ns >= t.min()) & (hw.t_mono_ns <= t.max())]
            if len(w) < 2:
                continue
            app = [f"freq_mhz_cpu{c}" for c in cpus(p.get("app_cpus"), len(fcols))]
            app = [c for c in app if c in w.columns]
            rows.append({
                "series": str(d.relative_to(REPO)), "experiment": p["experiment"],
                "config": p["config"], "rep": p["rep"], "seconds": len(w),
                "throttle_pkg": int(w.pkg_throttle_count.max() - w.pkg_throttle_count.min()),
                "throttle_core": int(w.core_throttle_count.max() - w.core_throttle_count.min()),
                "temp_max_c": w.pkg_temp_c.max(), "temp_median_c": w.pkg_temp_c.median(),
                "freq_app_mhz": float(np.median(w[app].to_numpy())),
                "freq_app_max_mhz": float(w[app].max(axis=1).median()),
            })
    R = pd.DataFrame(rows)
    out = REPO / "results/summary"
    R.to_csv(out / "stage3-hw-per-run.csv", index=False)
    m = ["throttle_pkg", "throttle_core", "temp_max_c", "temp_median_c", "freq_app_mhz", "freq_app_max_mhz"]
    A = (R.melt(id_vars=["experiment", "config"], value_vars=m, var_name="metric")
         .groupby(["experiment", "config", "metric"])["value"]
         .agg(n="count", median="median", q1=lambda v: v.quantile(.25),
              q3=lambda v: v.quantile(.75), min="min", max="max").reset_index())
    A.to_csv(out / "stage3-hw.csv", index=False)
    # прогонов с троттлингом из 10 — по вариантам
    R["throttled"] = R.throttle_pkg > 0
    print(R.groupby(["experiment", "config"]).agg(
        runs=("rep", "count"), throttled_runs=("throttled", "sum"),
        throttle_pkg_sum=("throttle_pkg", "sum"), temp_max=("temp_max_c", "max"),
        freq_app_median=("freq_app_mhz", "median"),
        freq_app_max=("freq_app_max_mhz", "median")).to_string())


if __name__ == "__main__":
    sys.exit(main())
