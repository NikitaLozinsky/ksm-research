#!/usr/bin/env python3
"""
plot-stage0-ksm.py — графики этапа 0 по серии stage0-ksm-probe.

  figures/png/vm/stage0-ksm-savings.png
      Сэкономленная память (pages_sharing × 4 КиБ) во времени после включения
      KSM, по режимам none / madvise / prctl: медиана по повторам и IQR.
      Пунктир — теоретический максимум для нагрузки.
  figures/png/vm/stage0-ksmd-cpu.png
      Загрузка ЦП потоком ksmd во времени (% одного ЦП, сглаживание окном 2 с),
      медиана и IQR по повторам.

Использование:  scripts/plot-stage0-ksm.py [каталог серии]
"""
import json
import sys
from pathlib import Path

from lib.figure_paths import figure_path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
FIG = REPO / "figures"
MODES = [("none", "без отметки (контроль)", "-."),
         ("madvise", "madvise(MADV_MERGEABLE)", "-"),
         ("prctl", "prctl(PR_SET_MEMORY_MERGE)", "--")]

plt.rcParams.update({"font.size": 10, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.3,
                     "svg.hashsalt": "ksm-research"})  # одинаковые id в SVG при пересборке


def save(fig, name: str) -> None:
    fig.tight_layout()
    fig.savefig(figure_path(FIG, name, "png"), dpi=300)
    fig.savefig(figure_path(FIG, name, "svg"), metadata={"Date": None})  # без даты: SVG воспроизводим
    plt.close(fig)


def load(series: Path):
    """dict режим -> список DataFrame (t от включения KSM), и параметры."""
    data, params = {}, None
    for p in sorted(series.glob("*-rep*.csv")):
        params = json.loads(p.with_suffix(".json").read_text())["run"]
        d = pd.read_csv(p)
        d["t"] = d["t_s"] - d.loc[d["run"] == 1, "t_s"].iloc[0]
        data.setdefault(params["mode"], []).append(d)
    return data, params


def band(runs, col_fn, grid):
    """Медиана и IQR величины по повторам на общей сетке времени."""
    Y = np.array([np.interp(grid, d["t"], col_fn(d)) for d in runs])
    return np.median(Y, axis=0), np.percentile(Y, 25, axis=0), np.percentile(Y, 75, axis=0), len(Y)


def main() -> None:
    raw = REPO / "results/raw/stage0-ksm-probe"
    series = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else sorted(raw.iterdir())[-1]
    data, params = load(series)
    t_max = min(d["t"].iloc[-1] for runs in data.values() for d in runs)
    grid = np.arange(-params["pre_s"], t_max, 0.5)
    expected = (params["mib"] * 256 - params["distinct"]) * 4 / 1024

    # --- экономия памяти ---
    fig, ax = plt.subplots(figsize=(6.0, 3.6))
    for i, (mode, label, ls) in enumerate(MODES):
        if mode not in data:
            continue
        med, q1, q3, n = band(data[mode], lambda d: d["pages_sharing"] * 4 / 1024, grid)
        ax.plot(grid, med, ls, color=f"C{i}", label=f"{label}, n={n}")
        ax.fill_between(grid, q1, q3, color=f"C{i}", alpha=0.25, linewidth=0)
    ax.axhline(expected, color="0.4", ls=":", lw=1)
    ax.text(grid[-1], expected, f"теоретический максимум {expected:.0f} МиБ",
            ha="right", va="bottom", fontsize=8, color="0.3")
    ax.axvline(0, color="0.5", ls=":", lw=1)
    ax.text(0.5, expected * 0.04, "KSM включён", fontsize=8, color="0.3")
    ax.set_xlabel("Время после включения KSM, с")
    ax.set_ylabel("Сэкономлено памяти, МиБ")
    ax.set_ylim(-5, expected * 1.12)
    ax.legend(loc="center right", fontsize=8)
    save(fig, "stage0-ksm-savings")

    # --- ЦП ksmd ---
    fig, ax = plt.subplots(figsize=(6.0, 3.6))
    win = 4  # 4 отсчёта по 0.5 с = окно 2 с

    def ksmd_pct(d):
        cpu = d["ksmd_cpu_ns"].diff(win) / (d["t_s"].diff(win) * 1e9) * 100
        return cpu.bfill().fillna(0)

    for i, (mode, label, ls) in enumerate(MODES):
        if mode not in data:
            continue
        med, q1, q3, n = band(data[mode], ksmd_pct, grid)
        ax.plot(grid, med, ls, color=f"C{i}", label=f"{label}, n={n}")
        ax.fill_between(grid, q1, q3, color=f"C{i}", alpha=0.25, linewidth=0)
    ax.axvline(0, color="0.5", ls=":", lw=1)
    ax.set_xlabel("Время после включения KSM, с")
    ax.set_ylabel("Загрузка ЦП потоком ksmd, % одного ЦП")
    ax.legend(loc="upper right", fontsize=8)
    save(fig, "stage0-ksmd-cpu")
    print("Сохранено: figures/png/vm/stage0-ksm-savings.png, figures/png/vm/stage0-ksmd-cpu.png")


if __name__ == "__main__":
    main()
