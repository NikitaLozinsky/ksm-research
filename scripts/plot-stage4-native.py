#!/usr/bin/env python3
"""Графики нативной серии Б из парных оценок и телеметрии."""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lib.figure_paths import figure_path

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "results/summary"
FIGURES = ROOT / "figures"
WORKLOADS = ["ticksrv", "luanti", "godot"]
LABELS = ["C-сервер", "Luanti", "Godot"]
COLORS = ["#215a88", "#955924", "#247b68"]


def save(fig, stem):
    fig.savefig(figure_path(FIGURES, stem, "png"), dpi=300)
    svg = figure_path(FIGURES, stem, "svg")
    fig.savefig(svg, metadata={"Date": None})
    svg.write_text("".join(f"{line.rstrip()}\n" for line in svg.read_text().splitlines()),
                   encoding="utf-8")
    plt.close(fig)


def plot_paired(data, specs, title, stem):
    fig, axes = plt.subplots(1, 2, figsize=(8.4, 3.1), sharey=True, layout="constrained")
    for ax, (metric, label, scale) in zip(axes, specs):
        rows = data.xs(metric, level="metric").loc[WORKLOADS]
        assert rows["n"].eq(15).all()
        median, lo, hi = rows[["median", "ci95_lo", "ci95_hi"]].to_numpy().T * scale
        for y, (center, left, right, color) in enumerate(zip(median, lo, hi, COLORS)):
            # Процентильный бутстреп-ДИ может не накрывать точечную оценку.
            ax.hlines(y, left, right, color=color, lw=2)
            ax.plot(center, y, "o", color=color, markersize=6)
        ax.axvline(0, color="#687385", ls="--", lw=1)
        ax.set_xlabel(label)
        ax.grid(axis="x", alpha=.22)
    axes[0].set_yticks(range(len(LABELS)), LABELS)
    axes[0].invert_yaxis()
    fig.suptitle(title, fontweight="bold")
    fig.supxlabel("KSM вкл. − выкл. · по 15 пар · медиана и 95 % бутстреп-ДИ", fontsize=9)
    save(fig, stem)


def plot_thermal(runs):
    fig, ax = plt.subplots(figsize=(8.2, 3.6), layout="constrained")
    for i, mode in enumerate(["off", "on"]):
        peaks = [runs.loc[runs.workload.eq(workload) & runs["mode"].eq(mode), "temp_max_c"]
                 for workload in WORKLOADS]
        assert all(len(values) == 15 for values in peaks)
        positions = np.arange(len(WORKLOADS)) + (-.17 if i == 0 else .17)
        color = ["#6982a2", "#aa552e"][i]
        boxes = ax.boxplot(peaks, positions=positions, widths=.28, patch_artist=True,
                           medianprops={"color": "#253249", "linewidth": 1.5},
                           boxprops={"facecolor": color, "alpha": .58})
        boxes["boxes"][0].set_label(["KSM выкл.", "KSM вкл."][i])
    ax.set_xticks(range(len(LABELS)), LABELS)
    ax.set_ylabel("Максимум температуры пакета за прогон, °C")
    ax.set_title("Нагрев в серии тик-серверов · 15 прогонов на режим", fontweight="bold")
    ax.grid(axis="y", alpha=.22)
    ax.legend(loc="lower right")
    save(fig, "stage4-block-b-thermal")


def main():
    plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False,
                         "svg.hashsalt": "ksm-research"})
    paired = pd.read_csv(SUMMARY / "stage4-ticks-block-b-paired.csv")
    paired = paired.query("phase == 'steady' and subset == 'all'").set_index(["workload", "metric"])
    plot_paired(paired, [("work_p50_ms", "Δ p50 работы тика, мс", 1),
                         ("work_p99_ms", "Δ p99 работы тика, мс", 1)],
                "Типичная работа тика и верхний перцентиль", "stage4-block-b-work")
    plot_paired(paired, [("low_1pct_tps", "Δ 1 % low, тиков/с", 1),
                         ("missed_frac", "Δ доли длинных интервалов, п. п.", 100)],
                "Темп медленных тиков и длинные интервалы", "stage4-block-b-cadence")
    runs = pd.read_csv(SUMMARY / "stage4-ticks-final-per-run.csv")
    plot_thermal(runs.query("block == 'b' and phase == 'steady'"))


if __name__ == "__main__":
    main()
