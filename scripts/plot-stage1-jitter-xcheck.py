#!/usr/bin/env python3
"""
plot-stage1-jitter-xcheck.py — график мини-серии stage1-jitter-xcheck:
CCDF опоздания пробуждения (период 1 мс) для трёх вариантов запуска:
  own      — наша timer_jitter от обычного пользователя;
  ownroot  — та же программа через sudo (от root);
  cyclictest — cyclictest 2.11 (через sudo, SCHED_OTHER).
Вопрос графика: объясняется ли расхождение этапа 0 (наша программа против
cyclictest) запуском от root.

Тонкие линии — отдельные повторы, толстые — все повторы вместе.
Для cyclictest значения > 100 мс (переполнение гистограммы) учтены как 100 мс.

Использование:  scripts/plot-stage1-jitter-xcheck.py [каталог серии]
Результат:      figures/png/vm/stage1-jitter-xcheck.png
"""
import importlib.util
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
# Переиспользуем разбор гистограммы cyclictest из сводки этапа 0.
spec = importlib.util.spec_from_file_location("j0", REPO / "scripts/stage0-jitter-summary.py")
j0 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(j0)

plt.rcParams.update({"font.size": 10, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.3,
                     "svg.hashsalt": "ksm-research"})
TOOLS = [("own", "наша программа, пользователь", "-"),
         ("ownroot", "наша программа, root (sudo)", "--"),
         ("cyclictest", "cyclictest 2.11 (sudo)", ":")]


def ccdf_from_hist(vals, cnts):
    """CCDF по гистограмме: x — значения, y — доля значений >= x."""
    order = np.argsort(vals)
    v, c = vals[order], cnts[order]
    y = 1.0 - (np.cumsum(c) - c) / c.sum()
    return v, y


def main() -> None:
    raw = REPO / "results/raw/stage1-jitter-xcheck"
    series = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else sorted(raw.iterdir())[-1]
    data = {}
    for pj in sorted(series.glob("*-rep*.json")):
        prm = json.loads(pj.read_text())["run"]
        base = pj.with_suffix("")
        if prm["tool"] in ("own", "ownroot"):
            x = pd.read_csv(base.with_suffix(".csv"))["lateness_ns"].to_numpy() / 1000.0
            vals, cnts = x, np.ones(len(x), dtype=np.int64)
        else:
            vals, cnts, _ = j0.cyclictest_hist(base.with_suffix(".cyclictest.txt"))
        data.setdefault(prm["tool"], []).append((vals, cnts))

    fig, ax = plt.subplots(figsize=(6.0, 3.6))
    for i, (tool, label, ls) in enumerate(TOOLS):
        if tool not in data:
            continue
        for vals, cnts in data[tool]:
            x, y = ccdf_from_hist(vals, cnts)
            ax.step(x, y, where="post", color=f"C{i}", alpha=0.2, lw=0.6)
        allv = np.concatenate([v for v, _ in data[tool]])
        allc = np.concatenate([c for _, c in data[tool]])
        x, y = ccdf_from_hist(allv, allc)
        ax.step(x, y, where="post", color=f"C{i}", lw=1.6, ls=ls,
                label=f"{label}, n={len(data[tool])}")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlim(left=1)
    ax.set_xlabel("Опоздание пробуждения, мкс (лог. шкала), период 1 мс")
    ax.set_ylabel("Доля пробуждений\nс опозданием ≥ x (лог. шкала)")
    ax.legend(fontsize=8, loc="lower left")
    fig.tight_layout()
    fig.savefig(figure_path(REPO / "figures", "stage1-jitter-xcheck", "png"), dpi=300)
    fig.savefig(figure_path(REPO / "figures", "stage1-jitter-xcheck", "svg"), metadata={"Date": None})
    print("Сохранено: figures/png/vm/stage1-jitter-xcheck.png")


if __name__ == "__main__":
    main()
