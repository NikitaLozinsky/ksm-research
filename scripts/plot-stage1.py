#!/usr/bin/env python3
"""
plot-stage1.py — графики этапа 1 по сериям цены COW и репетиции задержек.

  figures/png/vm/stage1-cow-cost.png        цена записи в страницу: слитую KSM и обычную
                                     (мкс на страницу, лог. шкала), точки — повторы;
  figures/png/vm/stage1-tick-work.png       CCDF длительности работы тика 60 Гц с KSM и без
                                     (без трассировки), все повторы вместе + тонкие
                                     линии отдельных повторов;
  figures/png/vm/stage1-attribution.png     доля окон, в которых ksmd был на ЦП: всплески
                                     работы тика (>= p99), обычные тики, случайные
                                     окна той же длины; медиана и IQR по повторам;
  figures/png/vm/stage1-trace-observer.png  медианное опоздание пробуждения (p50 прогона)
                                     по 4 конфигурациям: эффект трассировки на ВМ.
Источники: results/summary/stage1-cow-cost-per-run.csv,
           results/summary/stage1-latency-rehearsal-per-run.csv,
           results/raw/stage1-latency-rehearsal/<серия>/*.csv.
"""
import json
from pathlib import Path

from lib.figure_paths import figure_path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
FIG = REPO / "figures"
S = REPO / "results/summary"
plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.alpha": 0.3, "svg.hashsalt": "ksm-research"})


def save(fig, name):
    fig.tight_layout()
    fig.savefig(figure_path(FIG, name, "png"), dpi=300)
    fig.savefig(figure_path(FIG, name, "svg"), metadata={"Date": None})
    plt.close(fig)


def strip(ax, groups, labels, colors, rng):
    """Точки повторов + медиана (чёрная черта) + IQR (вертикальная линия)."""
    for i, (v, c) in enumerate(zip(groups, colors)):
        v = np.asarray(v, float)
        ax.scatter(i + rng.uniform(-0.12, 0.12, len(v)), v, s=14, color=c, alpha=0.7, zorder=3)
        q1, med, q3 = np.percentile(v, [25, 50, 75])
        ax.plot([i - 0.25, i + 0.25], [med, med], color="k", lw=2, zorder=4)
        ax.plot([i, i], [q1, q3], color="k", lw=1, zorder=4)
    ax.set_xticks(range(len(labels)), labels)


def main():
    rng = np.random.default_rng(0)

    # 1) цена COW
    c = pd.read_csv(S / "stage1-cow-cost-per-run.csv")
    order = [("plain-thp", "обычная\nTHP по умолч."), ("merged-thp", "слитая KSM\nTHP по умолч."),
             ("plain-nothp", "обычная\nбез THP"), ("merged-nothp", "слитая KSM\nбез THP")]
    fig, ax = plt.subplots(figsize=(6.0, 3.6))
    strip(ax, [c.loc[c.config == k, "us_per_page"] for k, _ in order], [l for _, l in order],
          ["C0", "C3", "C0", "C3"], rng)
    ax.set_yscale("log")
    ax.set_ylabel("Время первой записи\nв страницу, мкс (лог. шкала)")
    n = c.groupby("config").size().min()
    ax.set_title(f"Запись по байту в 16384 страницы, n={n} повторов на столбец", fontsize=9)
    save(fig, "stage1-cow-cost")

    # 2) CCDF работы тика
    r = pd.read_csv(S / "stage1-latency-rehearsal-per-run.csv")
    series = REPO / r["series"].iloc[0]
    fig, ax = plt.subplots(figsize=(6.0, 3.6))
    for i, (cfg, label, ls) in enumerate([("ksmoff-traceoff", "KSM выкл.", "-"),
                                          ("ksmon-traceoff", "KSM вкл.", "--")]):
        allv = []
        files = sorted(series.glob(f"{cfg}-rep*.csv"))
        files = [f for f in files if not f.name.endswith(".ksm.csv") and not f.name.endswith(".vmstat.csv")]
        for f in files:
            w = np.sort(pd.read_csv(f)["work_ns"].to_numpy() / 1e3)
            allv.append(w)
            ax.step(w, 1 - np.arange(len(w)) / len(w), where="post", color=f"C{i}", alpha=0.2, lw=0.6)
        a = np.sort(np.concatenate(allv))
        ax.step(a, 1 - np.arange(len(a)) / len(a), where="post", color=f"C{i}", lw=1.6, ls=ls,
                label=f"{label}, n={len(files)} повторов")
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("Длительность работы тика, мкс (лог. шкала)")
    ax.set_ylabel("Доля тиков с работой > x\n(лог. шкала)")
    ax.set_title("Тик 60 Гц, обновление 256 страниц состояния за тик", fontsize=9)
    ax.legend(fontsize=8, loc="lower left")
    save(fig, "stage1-tick-work")

    # 3) атрибуция
    t = r[r.config == "ksmon-traceon"]
    fig, ax = plt.subplots(figsize=(6.0, 3.6))
    strip(ax, [t["attr_ksmd_bad"], t["attr_ksmd_norm"], t["attr_ksmd_rand"]],
          ["всплески работы\nтика (≥ p99)", "обычные тики", "случайные окна\nтой же длины"],
          ["C3", "C0", "C7"], rng)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Доля окон, в которых\nksmd был на ЦП")
    ax.set_title(f"KSM вкл., трассировка вкл., n={len(t)} повторов", fontsize=9)
    save(fig, "stage1-attribution")

    # 4) эффект наблюдателя (трассировка) на опоздание пробуждения
    order = [("ksmoff-traceoff", "KSM выкл.\nтрасса выкл."), ("ksmoff-traceon", "KSM выкл.\nтрасса вкл."),
             ("ksmon-traceoff", "KSM вкл.\nтрасса выкл."), ("ksmon-traceon", "KSM вкл.\nтрасса вкл.")]
    fig, ax = plt.subplots(figsize=(6.0, 3.6))
    strip(ax, [r.loc[r.config == k, "lat_p50_us"] / 1e3 for k, _ in order], [l for _, l in order],
          ["C0", "C2", "C3", "C1"], rng)
    ax.set_ylabel("Медианное опоздание\nпробуждения тика, мс")
    ax.set_title("Тик 60 Гц на ВМ; точка — один прогон 90 с", fontsize=9)
    save(fig, "stage1-trace-observer")
    print("Сохранено: stage1-cow-cost, stage1-tick-work, stage1-attribution, stage1-trace-observer")


if __name__ == "__main__":
    main()
