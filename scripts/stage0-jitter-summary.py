#!/usr/bin/env python3
"""
stage0-jitter-summary.py — сводка и график серии stage0-jitter (шум ВМ).

Для cyclictest (перекрёстная проверка) квантили считаются по его гистограмме
с шагом 1 мкс (значения cyclictest целые, в мкс), поэтому сравнивать с нашей
программой можно с точностью ~1 мкс. Значения больше 100 мс в гистограмму не
попадают («Histogram Overflows»); они учитываются как 100 мс, т.е. квантили
выше этого уровня для cyclictest — нижняя оценка, а max берётся из строки
«# Max Latencies».

Обе программы ПРОПУСКАЮТ прошедшие периоды (не догоняют). Число пропущенных
тиков известно только для нашей программы (столбец missed).

По каждому прогону (один файл = одна конфигурация, один повтор) считаются квантили
опоздания пробуждения в микросекундах: p50, p90, p99, p99.9, max; доля тиков
с опозданием больше 100 мкс и больше 1 мс; число «пропущенных» тиков
(опоздание >= периода, т.е. пробуждение позже следующего запланированного
тика); steal-время за прогон (из /proc/stat, в % от времени всех ЦП).

Затем по каждому периоду: медиана, Q1, Q3, min, max каждой величины по повторам.

Результаты:
  results/summary/stage0-jitter-per-run.csv
  results/summary/stage0-jitter.csv
  figures/png/vm/stage0-timer-jitter.png — CCDF опоздания (доля пробуждений,
      опоздавших больше чем на x), лог-лог; тонкие линии — отдельные повторы,
      толстая — все повторы вместе.
"""
import json
import sys
import warnings
from pathlib import Path

from lib.figure_paths import figure_path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
# missed для cyclictest — NaN; предупреждения numpy об этом не нужны.
warnings.filterwarnings("ignore", message="Mean of empty slice")
plt.rcParams.update({"font.size": 10, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.3,
                     "svg.hashsalt": "ksm-research"})  # одинаковые id в SVG при пересборке


def steal_pct(load_file: Path) -> float:
    """Доля steal-времени за прогон по строкам cpu из /proc/stat."""
    vals = {}
    for line in load_file.read_text().splitlines():
        k, _, v = line.partition("=")
        if k.startswith("stat_"):
            vals[k] = np.array(v.split()[1:], dtype=float)
    if len(vals) != 2:
        return float("nan")
    d = vals["stat_after"] - vals["stat_before"]
    return d[7] / d.sum() * 100 if d.sum() > 0 else float("nan")


def cyclictest_hist(path: Path):
    """Гистограмма cyclictest: строки «значение_мкс счётчик» (без '#')."""
    vals, cnts = [], []
    max_us = overflows = 0
    for line in path.read_text().splitlines():
        if line.startswith("# Max Latencies:"):
            max_us = int(line.split(":")[1].split()[0])
        if line.startswith("# Histogram Overflows:"):
            overflows = int(line.split(":")[1].split()[0])
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit():
            c = int(parts[1])
            if c:
                vals.append(int(parts[0])); cnts.append(c)
    if overflows:
        vals.append(100000); cnts.append(overflows)   # нижняя оценка, см. docstring
    return np.array(vals, dtype=float), np.array(cnts, dtype=np.int64), max_us


def weighted_quantiles(vals, cnts, qs):
    """Квантили по гистограмме (значение, частота)."""
    cum = np.cumsum(cnts) / cnts.sum()
    return [vals[np.searchsorted(cum, q / 100.0)] for q in qs]


def main() -> None:
    raw = REPO / "results/raw/stage0-jitter"
    series = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else sorted(raw.iterdir())[-1]
    # Второй аргумент — префикс имён файлов сводки (по умолчанию stage0-jitter).
    # График CCDF этапа 0 строится только для серии этапа 0.
    name = sys.argv[2] if len(sys.argv) > 2 else "stage0-jitter"
    rows, samples = [], {}
    for pj in sorted(series.glob("*-rep*.json")):
        params = json.loads(pj.read_text())["run"]
        tool, period = params["tool"], params["period_us"]
        base = pj.with_suffix("")
        if tool in ("own", "ownroot"):
            f = base.with_suffix(".csv")
            lat_us = pd.read_csv(f)["lateness_ns"].to_numpy() / 1000.0
            if tool == "own":
                samples.setdefault(period, []).append(lat_us)
            vals, cnts = np.sort(lat_us), np.ones(len(lat_us), dtype=np.int64)
            missed = int(pd.read_csv(f)["missed"].sum())
            max_us = vals.max()
        else:
            f = base.with_suffix(".cyclictest.txt")
            vals, cnts, max_us = cyclictest_hist(f)
            missed = float("nan")
        q = weighted_quantiles(vals, cnts, [50, 90, 99, 99.9])
        n = int(cnts.sum())
        rows.append({
            "tool": tool, "period_us": period, "rep": params["rep"], "n_ticks": n,
            "p50_us": q[0], "p90_us": q[1], "p99_us": q[2], "p999_us": q[3],
            "max_us": max_us, "min_us": vals.min(),
            "frac_gt_100us": float(cnts[vals > 100].sum() / n),
            "frac_gt_1ms": float(cnts[vals > 1000].sum() / n),
            "missed_ticks": missed,
            "missed_frac": missed / (n + missed) if missed == missed else float("nan"),
            "steal_pct": steal_pct(base.with_suffix(".load")),
            "file": str(f.relative_to(REPO)),
        })
    runs = pd.DataFrame(rows).sort_values(["tool", "period_us", "rep"])
    out = REPO / "results/summary"
    runs.insert(0, "series", str(series.relative_to(REPO)))
    runs.to_csv(out / f"{name}-per-run.csv", index=False)

    metrics = ["p50_us", "p90_us", "p99_us", "p999_us", "max_us", "min_us",
               "frac_gt_100us", "frac_gt_1ms", "missed_ticks", "missed_frac", "steal_pct"]
    agg = []
    for (tool, period), g in runs.groupby(["tool", "period_us"]):
        for m in metrics:
            v = g[m].astype(float)
            agg.append({"tool": tool, "period_us": period, "metric": m, "n": len(v),
                        "median": v.median(), "q1": v.quantile(0.25),
                        "q3": v.quantile(0.75), "min": v.min(), "max": v.max()})
    agg = pd.DataFrame(agg)
    agg.insert(0, "series", str(series.relative_to(REPO)))
    agg.to_csv(out / f"{name}.csv", index=False)

    # --- график: CCDF (только для серии этапа 0) ---
    if name != "stage0-jitter":
        print_table(series, agg)
        return
    fig, ax = plt.subplots(figsize=(6.0, 3.6))
    for i, (period, reps) in enumerate(sorted(samples.items())):
        label = f"период {period / 1000:g} мс" if period < 10000 else f"период {period / 1000:.2f} мс (60 Гц)"
        for r in reps:
            x = np.sort(r)
            y = 1.0 - np.arange(len(x)) / len(x)
            ax.step(x, y, where="post", color=f"C{i}", alpha=0.25, lw=0.6)
        allx = np.sort(np.concatenate(reps))
        ally = 1.0 - np.arange(len(allx)) / len(allx)
        ax.step(allx, ally, where="post", color=f"C{i}", lw=1.6, ls="-" if i == 0 else "--",
                label=f"{label}, n={len(reps)} повторов")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Опоздание пробуждения, мкс (лог. шкала)")
    ax.set_ylabel("Доля пробуждений\nс опозданием > x (лог. шкала)")
    ax.set_xlim(left=1)
    ax.legend(fontsize=8, loc="lower left")
    fig.tight_layout()
    fig.savefig(figure_path(REPO / "figures", "stage0-timer-jitter", "png"), dpi=300)
    fig.savefig(figure_path(REPO / "figures", "stage0-timer-jitter", "svg"), metadata={"Date": None})

    print_table(series, agg)


def print_table(series: Path, agg: pd.DataFrame) -> None:
    print(f"Серия: {series.relative_to(REPO)}")
    pd.set_option("display.width", 160)
    print(agg.drop(columns="series").to_string(index=False, float_format=lambda x: f"{x:.4g}"))


if __name__ == "__main__":
    main()
