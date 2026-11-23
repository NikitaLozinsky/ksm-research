#!/usr/bin/env python3
"""График парных отношений задержки и 90 % ДИ для блока 0 этапа 4."""

from pathlib import Path

from lib.figure_paths import figure_path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator

REPO = Path(__file__).resolve().parents[1]


def main():
    data = pd.read_csv(REPO / "results/summary/stage4-eq-paired.csv")
    data = data[data.subset == "все пары"].set_index(["workload", "phase", "metric"])
    phases = {"pre": "Контроль", "merge": "Слияние", "steady": "Устойчивая"}
    metrics = {"p50_ms": "p50", "p99_ms": "p99", "p999_ms": "p99.9"}
    rows = [(phase, metric) for phase in phases for metric in metrics]
    positions = [4 * i + j for i in range(len(phases)) for j in range(len(metrics))]

    plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False,
                         "svg.hashsalt": "ksm-research"})
    fig, axes = plt.subplots(1, 2, sharex=True, sharey=True, figsize=(8, 5.2))
    fig.subplots_adjust(left=0.18, right=0.99, top=0.85, bottom=0.17, wspace=0.06)
    for ax, workload, title in zip(axes, ["python", "jvm"],
                                   ["Python · 50 запросов/с", "JVM · 400 запросов/с"]):
        selected = data.loc[workload].loc[rows]
        assert len(selected) == len(rows) and selected.n_pairs.nunique() == 1
        ratio, lo, hi = selected[["ratio", "ci90_lo", "ci90_hi"]].to_numpy().T
        assert ((0 < lo) & (lo <= ratio) & (ratio <= hi)).all()
        ax.set_title(f"{title} на контейнер\n{selected.n_pairs.iloc[0]} пар", fontsize=10)
        ax.set_xscale("log")
        ax.axvspan(1 / 1.2, 1.2, color="#e0edf4", label="Зона эквивалентности [1/1,2; 1,2]")
        ax.axvline(1, color="#505050", linestyle="--", linewidth=1)
        ax.errorbar(ratio, positions, xerr=[ratio - lo, hi - ratio], fmt="o",
                    color="#204864", markersize=4.5, capsize=3,
                    label="Парная оценка и 90 % бутстреп-ДИ")
        ax.grid(axis="x", alpha=0.25)

    axes[0].set_yticks(positions, [
        f"{phases[phase]} · {metrics[metric]}" if metric == "p50_ms" else metrics[metric]
        for phase, metric in rows
    ])
    axes[0].invert_yaxis()
    axes[0].set_xlim(0.75, 1.65)
    axes[0].xaxis.set_major_locator(FixedLocator([0.8, 1, 1.2, 1.4, 1.6]))
    axes[0].xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}".replace(".", ",")))
    axes[0].xaxis.set_minor_locator(NullLocator())
    fig.suptitle("Задержка HTTP-запросов при включении KSM", fontweight="bold")
    fig.supxlabel("Отношение задержек KSM вкл. / выкл. (лог. шкала)", y=0.075)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncols=2, frameon=False, fontsize=9)

    directory = REPO / "figures"
    fig.savefig(figure_path(directory, "stage4-eq-ratio", "png"), dpi=300)
    svg = figure_path(directory, "stage4-eq-ratio", "svg")
    fig.savefig(svg, metadata={"Date": None})
    svg.write_text("".join(f"{line.rstrip()}\n" for line in svg.read_text().splitlines()),
                   encoding="utf-8")
    plt.close(fig)


if __name__ == "__main__":
    main()
