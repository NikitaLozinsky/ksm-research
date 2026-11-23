#!/usr/bin/env python3
"""
plot-stage2.py — графики этапа 2 (по одному выводу на график).

  stage2-p99-diff.png        разность медиан p99 задержки (KSM вкл. − выкл.) с 95 %
                             бутстреп-интервалом по нагрузкам и фазам (pre — контроль);
  stage2-server-attr.png     доля времени окна, когда ksmd был на ЦП: всплески времени
                             сервера (>= p99) против случайных окон той же длины;
  stage2-pin-tick.png        p99 работы тика при привязке ksmd unpinned / split / shared;
  stage2-tlb.png             межпроцессорные сбросы TLB на vCPU тика в секунду по вариантам;
  stage2-memory-load.png     экономия памяти под нагрузкой (вкл. − выкл., Δ(Rss−Pss)) против
                             простоя (этап 1/2, N=8).
Источники: results/summary/stage2-*.csv, stage1-workloads*.csv, stage2-rerun*.csv.
"""
from pathlib import Path

from lib.figure_paths import figure_path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
S = REPO / "results/summary"; FIG = REPO / "figures"
plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.alpha": 0.3, "svg.hashsalt": "ksm-research"})
WL = ["nginx", "python", "node", "jvm", "postgres"]
NAMES = {"nginx": "nginx", "python": "Python", "node": "Node.js", "jvm": "JVM", "postgres": "PostgreSQL"}


def save(fig, name):
    fig.tight_layout(); fig.savefig(figure_path(FIG, name, "png"), dpi=300)
    fig.savefig(figure_path(FIG, name, "svg"), metadata={"Date": None}); plt.close(fig)


def strip(ax, groups, labels, colors, rng):
    for i, (v, c) in enumerate(zip(groups, colors)):
        v = np.asarray(v, float)
        ax.scatter(i + rng.uniform(-.12, .12, len(v)), v, s=14, color=c, alpha=.7, zorder=3)
        q1, m, q3 = np.percentile(v, [25, 50, 75])
        ax.plot([i - .25, i + .25], [m, m], color="k", lw=2, zorder=4); ax.plot([i, i], [q1, q3], color="k", lw=1, zorder=4)
    ax.set_xticks(range(len(labels)), labels)


def main():
    rng = np.random.default_rng(0)
    # 1) разность p99
    C = pd.read_csv(S / "stage2-load-compare.csv")
    C = C[(C["compare"] == "KSM вкл. − KSM выкл.") & (C.metric == "p99_ms")]
    fig, ax = plt.subplots(figsize=(6.2, 4.6))
    y, ticks, labels = 0, [], []
    for w in WL:
        for ph, mk, col in [("pre", "o", "0.6"), ("merge", "s", "C3"), ("steady", "D", "C0")]:
            r = C[(C.workload == w) & (C.phase == ph)]
            if not len(r):
                continue
            r = r.iloc[0]
            ax.errorbar(r.diff_median, y, xerr=[[r.diff_median - r.diff_ci_lo], [r.diff_ci_hi - r.diff_median]],
                        fmt=mk, color=col, mfc=col if r.significant else "white", capsize=2, ms=5)
            ticks.append(y); labels.append(f"{NAMES[w]} · {ph}"); y += 1
        y += 0.6
    ax.axvline(0, color="k", lw=0.8)
    ax.set_xscale("symlog", linthresh=10)
    ax.set_yticks(ticks, labels, fontsize=7); ax.invert_yaxis()
    ax.set_xlabel("Разность медиан p99 задержки (KSM вкл. − выкл.), мс\n(симметричная лог. шкала; усы — 95 % бутстреп-ДИ)")
    ax.set_title("N=8, fast, n=10+10; закрашено — ДИ не содержит 0; pre — контроль", fontsize=9)
    save(fig, "stage2-p99-diff")

    # 2) атрибуция по времени сервера
    A = pd.read_csv(S / "stage2-load-server-attr.csv")
    fig, ax = plt.subplots(figsize=(6.2, 3.8))
    x = 0; xt, xl = [], []
    for w in ["nginx", "python", "node", "jvm"]:
        for ph in ["merge", "steady"]:
            g = A[(A.workload == w) & (A.phase == ph)]
            for dx, col, c, lab in [(-0.15, "C3", "onfrac_bad", "всплески (≥ p99)"), (0.15, "0.5", "onfrac_rand", "случайные окна")]:
                q1, m, q3 = np.percentile(g[c], [25, 50, 75])
                ax.errorbar(x + dx, m, yerr=[[m - q1], [q3 - m]], fmt="o", color=col, capsize=2,
                            label=lab if x == 0 else None)
            xt.append(x); xl.append(f"{NAMES[w]}\n{ph}"); x += 1
    ax.set_xticks(xt, xl, fontsize=7)
    ax.set_ylabel("Доля времени окна сервера,\nкогда ksmd был на ЦП")
    ax.set_title("KSM вкл., окна [отправка; ответ], медиана и IQR по 10 повторам", fontsize=9)
    ax.legend(fontsize=8); ax.set_ylim(0, None)
    save(fig, "stage2-server-attr")

    # 3) привязка ksmd: хвост работы тика
    P = pd.read_csv(S / "stage2-ksmd-pin-per-run.csv")
    order = [("unpinned", "без привязки"), ("split", "ksmd и тик\nна разных vCPU"), ("shared", "ksmd и тик\nна одном vCPU")]
    fig, ax = plt.subplots(figsize=(6.0, 3.6))
    strip(ax, [P.loc[P.config == k, "work_p99_us"] / 1e3 for k, _ in order], [l for _, l in order], ["C0", "C3", "C2"], rng)
    ax.set_ylabel("p99 длительности работы тика, мс"); ax.set_ylim(0, None)
    ax.set_title("Модель тика 60 Гц, KSM вкл., n=10 на вариант", fontsize=9)
    save(fig, "stage2-pin-tick")

    # 4) сбросы TLB
    f = S / "stage2-ksmd-pin-tlb-irq-per-run.csv"
    if f.exists():
        T = pd.read_csv(f)
        fig, ax = plt.subplots(figsize=(6.0, 3.6))
        strip(ax, [T.loc[T.config == k, "tlb_app_per_s"] for k, _ in order], [l for _, l in order], ["C0", "C3", "C2"], rng)
        ax.set_ylabel("Сбросов TLB на vCPU тика, 1/с"); ax.set_ylim(0, None)
        ax.set_title("/proc/interrupts, строка TLB; прогоны по 30 с, n=10 на вариант", fontsize=9)
        save(fig, "stage2-tlb")

    # 5) память под нагрузкой против простоя
    R = pd.read_csv(S / "stage2-load-per-run.csv")
    R = R[R.phase == "steady"]
    idle = {}
    for fn in ["stage1-workloads-per-run.csv", "stage2-rerun-per-run.csv", "stage2-rerun-jvm-per-run.csv"]:
        d = pd.read_csv(S / fn); d = d[(d.n == 8)]
        for w, g in d.groupby("workload"):
            idle[w] = g.saved_share_MiB.to_numpy()          # последние файлы перекрывают первые
    fac = pd.read_csv(S / "stage1-workloads-factors-per-run.csv")
    fig, ax = plt.subplots(figsize=(6.2, 3.8))
    for i, w in enumerate(WL):
        on = R[(R.workload == w) & (R["mode"] == "on")].saved_share_MiB.to_numpy()
        off = R[(R.workload == w) & (R["mode"] == "off")].saved_share_MiB.to_numpy()
        load = on - np.median(off)                           # вклад KSM под нагрузкой
        idl = fac[fac.ksm_cfg == "fast"].saved_share_MiB.to_numpy() if w == "python" else idle.get(w, np.array([np.nan]))
        for dx, v, col, lab in [(-0.15, idl, "0.5", "простой (без запросов)"), (0.15, load, "C3", "под нагрузкой (вкл. − выкл.)")]:
            q1, m, q3 = np.nanpercentile(v, [25, 50, 75])
            ax.errorbar(i + dx, m, yerr=[[m - q1], [q3 - m]], fmt="o", color=col, capsize=3, label=lab if i == 0 else None)
    ax.set_xticks(range(len(WL)), [NAMES[w] for w in WL])
    ax.set_yscale("log"); ax.set_ylabel("Сэкономлено, МиБ (лог. шкала)\nΔ(RssAnon − PssAnon), 8 контейнеров")
    ax.set_title("Медиана и IQR по 10 повторам; Python в простое — настройка fast", fontsize=9)
    ax.legend(fontsize=8)
    save(fig, "stage2-memory-load")
    print("Сохранено: stage2-p99-diff, stage2-server-attr, stage2-pin-tick, stage2-tlb, stage2-memory-load")


if __name__ == "__main__":
    main()
