#!/usr/bin/env python3
"""
plot-stage1-workloads.py — графики этапа 1 по реальным нагрузкам.

  figures/png/vm/stage1-savings-vs-n.png   доля анонимной памяти контейнеров, которую
      KSM позволил не хранить (Δ(RssAnon − PssAnon) / RssAnon до, %), в
      зависимости от числа одинаковых контейнеров N, KSM по умолчанию.
      Точка — медиана, усы — IQR по 10 повторам. Полый маркер — конфигурация,
      где схождение подтверждено не во всех повторах (conv_ok < 10): значение
      там — нижняя граница.
  figures/png/vm/stage1-factors-tradeoff.png  python, N=8: время до 90 % экономии
      против средней загрузки ЦП потоком ksmd для 6 настроек KSM
      (медиана и IQR по обеим осям).
Источники: results/summary/stage1-workloads-per-run.csv,
           results/summary/stage1-workloads-factors-per-run.csv
"""
from pathlib import Path

from lib.figure_paths import figure_path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
S = REPO / "results/summary"
FIG = REPO / "figures"
plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.alpha": 0.3, "svg.hashsalt": "ksm-research"})


def save(fig, name):
    fig.tight_layout()
    fig.savefig(figure_path(FIG, name, "png"), dpi=300)
    fig.savefig(figure_path(FIG, name, "svg"), metadata={"Date": None})
    plt.close(fig)


def q(v):
    v = np.asarray(v, float)
    return np.percentile(v, [25, 50, 75])


def main():
    r = pd.read_csv(S / "stage1-workloads-per-run.csv")
    names = {"nginx": "nginx", "python": "Python (gunicorn)", "postgres": "PostgreSQL",
             "node": "Node.js", "jvm": "JVM"}
    markers = {"nginx": "o", "python": "s", "postgres": "^", "node": "D", "jvm": "v"}
    fig, ax = plt.subplots(figsize=(6.0, 4.3))
    for i, w in enumerate(["nginx", "python", "postgres", "node", "jvm"]):
        g = r[r.workload == w]
        ns = sorted(g.n.unique())
        stats = [q(g[g.n == n].saved_share_pct) for n in ns]
        ok = [int(g[g.n == n].conv_ok.sum()) for n in ns]
        x = np.array(ns) * (1 + 0.03 * (i - 2))         # небольшой сдвиг, чтобы усы не слипались
        med = [s[1] for s in stats]
        ax.errorbar(x, med, yerr=[[s[1] - s[0] for s in stats], [s[2] - s[1] for s in stats]],
                    color=f"C{i}", lw=1, capsize=3, ls="-", label=names[w])
        for xi, m, k in zip(x, med, ok):
            ax.plot(xi, m, markers[w], color=f"C{i}", ms=6,
                    mfc=f"C{i}" if k == 10 else "white", zorder=3)
    ax.set_xscale("log", base=2)
    ax.set_xticks([1, 4, 8], ["1", "4", "8"])
    ax.set_xlabel("Число одинаковых контейнеров N")
    ax.set_ylabel("Сэкономлено анонимной\nпамяти, % (Δ(Rss−Pss)/Rss)")
    ax.set_ylim(0, None)
    ax.set_title("KSM по умолчанию, n=10 повторов; полый маркер — нижняя граница", fontsize=9)
    ax.legend(fontsize=8, ncol=5, loc="upper center", bbox_to_anchor=(0.5, -0.2),
              frameon=False, handlelength=1.5, columnspacing=1.0)
    save(fig, "stage1-savings-vs-n")

    f = pd.read_csv(S / "stage1-workloads-factors-per-run.csv")
    labels = {"default": "default (100 стр./20 мс)", "fast": "fast (1000 стр./20 мс)",
              "adv200": "advisor, цель 200 с", "adv30": "advisor, цель 30 с",
              "mps16": "fast + max_page_sharing=16", "mps1024": "fast + max_page_sharing=1024"}
    fig, ax = plt.subplots(figsize=(6.0, 3.8))
    for i, c in enumerate(["default", "fast", "adv200", "adv30", "mps16", "mps1024"]):
        g = f[f.ksm_cfg == c]
        xq, yq = q(g.ksmd_pct), q(g.t90_s)
        full = int(g.conv_ok.sum()) == len(g)
        ax.errorbar(xq[1], yq[1], xerr=[[xq[1] - xq[0]], [xq[2] - xq[1]]],
                    yerr=[[yq[1] - yq[0]], [yq[2] - yq[1]]], fmt="o", color=f"C{i}",
                    mfc=f"C{i}" if full else "white", capsize=3,
                    label=labels[c] + ("" if full else " *"))
    ax.set_yscale("log")
    ax.set_xlabel("Средняя загрузка ЦП потоком ksmd, % одного ЦП")
    ax.set_ylabel("Время до 90 % экономии, с\n(лог. шкала)")
    ax.set_title("Python, N=8, n=10 повторов; * и полый маркер — схождение\nподтверждено не во всех повторах", fontsize=9)
    ax.legend(fontsize=7, loc="upper right")
    save(fig, "stage1-factors-tradeoff")
    print("Сохранено: figures/png/vm/ и figures/svg/vm/")


if __name__ == "__main__":
    main()
