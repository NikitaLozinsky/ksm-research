#!/usr/bin/env python3
"""
plot-stage3.py — графики этапа 3: ВМ (этапы 0–2) против нативного Arch.
По одному выводу на график; точки — отдельные повторы, черта — медиана, ус — IQR.

  stage3-timer-jitter.png  p99 опоздания таймера 1 кГц (cyclictest и свои таймеры):
                           на железе шум в сотни раз меньше, и инструменты совпадают;
  stage3-missed-ticks.png  доля пропущенных тиков 60 Гц в репетиции и в серии ksmd-pin:
                           на ВМ 0.2–17 %, на железе 0;
  stage3-cow-cost.png      время записи в страницу: слитая (COW) и обычная, ВМ и натив;
  stage3-tlb.png           сбросы TLB на ЦП тика и p99 работы тика по вариантам привязки
                           ksmd: механизм есть и на железе, но цена мала;
  stage3-pin-wake.png      p99 опоздания пробуждения тика по вариантам привязки ksmd:
                           на железе худший вариант — shared (на ВМ он был лучшим);
  stage3-hw.png            частота ЦП тика и троттлинг по прогонам серии ksmd-pin
                           (проверка, что разница вариантов не от нагрева).
Блок Г (задержка под нагрузкой, scripts/stage3-load-analysis.py):
  stage3-load-p99.png      p99 задержки запросов в устойчивой фазе, ВМ против натива
                           на тех же частотах: на железе хвосты в 17–3900 раз короче;
  stage3-load-ksm-effect.png  разность медиан p99 KSM вкл. − выкл. с 95 % ДИ по
                           нагрузкам и сериям: ни один интервал на нативе не
                           исключает 0 (в т.ч. без прогонов с перебоями питания);
  stage3-load-attribution.png  доля времени ksmd на ЦП во всплесках (>= p99) к
                           случайным окнам, по прогонам: на нативе ≈2×, на ВМ ≤1;
  stage3-load-cost.png     цена и польза KSM под нагрузкой: загрузка ksmd и экономия
                           памяти, ВМ против натива.
Источники: results/summary/stage3-*-per-run.csv (натив) и одноимённые сводки этапов 0–2 (ВМ).
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
VM, NAT = "#7f7f7f", "#d62728"           # ВМ — серый, натив — красный
PIN = ["shared", "sibling", "split", "unpinned"]
PIN_RU = {"shared": "shared\n(тот же ЦП)", "sibling": "sibling\n(брат SMT)",
          "split": "split\n(другое ядро)", "unpinned": "unpinned\n(без привязки)"}


def save(fig, name, tight=True):
    if tight:
        fig.tight_layout()
    fig.savefig(figure_path(FIG, name, "png"), dpi=300)
    fig.savefig(figure_path(FIG, name, "svg"), metadata={"Date": None}); plt.close(fig)


def rd(name):
    return pd.read_csv(S / f"{name}-per-run.csv")


def strip(ax, x, v, color, rng, label=None, w=.14):
    """Точки повторов + медиана (черта) + IQR (ус). Пустая группа — пропуск."""
    v = np.asarray(v, float); v = v[~np.isnan(v)]
    if not len(v):
        return
    ax.scatter(x + rng.uniform(-w / 2, w / 2, len(v)), v, s=12, color=color, alpha=.65, zorder=3, label=label)
    q1, m, q3 = np.percentile(v, [25, 50, 75])
    ax.plot([x - w, x + w], [m, m], color="k", lw=1.8, zorder=4); ax.plot([x, x], [q1, q3], color="k", lw=1, zorder=4)


def pairs(ax, groups, rng, dx=.2):
    """groups: [(подпись, значения ВМ или None, значения натива)] — пары точек ВМ/натив."""
    for i, (_, a, b) in enumerate(groups):
        if a is not None:
            strip(ax, i - dx, a, VM, rng, "ВМ (VirtualBox, 4 vCPU)" if i == 0 else None)
        strip(ax, i + dx, b, NAT, rng, "натив (i5-11300H)" if i == 0 else None)
    ax.set_xticks(range(len(groups)), [g[0] for g in groups])


def main():
    rng = np.random.default_rng(0)

    # 1) шум таймера: серия xcheck (три инструмента на одном таймере 1 кГц)
    a, b = rd("stage1-jitter-xcheck"), rd("stage3-jitter-xcheck")
    tools = [("cyclictest", "cyclictest"), ("own", "свой таймер"), ("ownroot", "свой таймер\n(root, FIFO)")]
    fig, ax = plt.subplots(figsize=(6.2, 4.2))
    pairs(ax, [(lab, a[a.tool == t].p99_us / 1000, b[b.tool == t].p99_us / 1000) for t, lab in tools], rng)
    ax.set_yscale("log"); ax.set_ylabel("p99 опоздания таймера, мс (лог. шкала)")
    ax.set_title("Таймер 1 кГц без нагрузки, KSM выкл., n=10 на точку", fontsize=9)
    ax.legend(fontsize=8, loc="center right")
    save(fig, "stage3-timer-jitter")

    # 2) пропуски тиков 60 Гц
    ra, rb = rd("stage1-latency-rehearsal"), rd("stage3-rehearsal")
    pa, pb = rd("stage2-ksmd-pin"), rd("stage3-ksmd-pin")
    g = [(f"репетиция\n{c.replace('ksm', 'KSM ').replace('-trace', '\nтрасса ')}",
          ra[ra.config == c].missed_frac * 100, rb[rb.config == c].missed_frac * 100)
         for c in ["ksmoff-traceoff", "ksmoff-traceon", "ksmon-traceoff", "ksmon-traceon"]]
    g += [(f"ksmd-pin\n{c}", pa[pa.config == c].missed_frac * 100 if c in set(pa.config) else None,
           pb[pb.config == c].missed_frac * 100) for c in PIN]
    fig, ax = plt.subplots(figsize=(8.4, 4.2))
    pairs(ax, g, rng)
    ax.set_xticks(range(len(g)), [x[0] for x in g], fontsize=7)
    ax.set_ylabel("Пропущенные тики, % от всех")
    ax.set_title("Тик 60 Гц, 90 с на прогон, n=10 на точку; sibling на ВМ нет (нет SMT)", fontsize=9)
    ax.legend(fontsize=8)
    save(fig, "stage3-missed-ticks")

    # 3) цена COW
    ca, cb = rd("stage1-cow-cost"), rd("stage3-cow-cost")
    cfg = [("merged-nothp", "слитая\nбез THP"), ("merged-thp", "слитая\nTHP"),
           ("plain-nothp", "обычная\nбез THP"), ("plain-thp", "обычная\nTHP")]
    fig, ax = plt.subplots(figsize=(6.2, 4.2))
    pairs(ax, [(lab, ca[ca.config == c].us_per_page, cb[cb.config == c].us_per_page) for c, lab in cfg], rng)
    ax.set_yscale("log"); ax.set_ylabel("Время первой записи на страницу, мкс (лог. шкала)")
    ax.set_title("16384 страницы 4 КиБ за прогон, n=10 на точку", fontsize=9)
    ax.legend(fontsize=8)
    save(fig, "stage3-cow-cost")

    # 4) сбросы TLB и работа тика
    ta, tb = rd("stage2-ksmd-pin-tlb-irq"), rd("stage3-ksmd-tlb-irq")
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.4, 4.0))
    for ax, col, k, yl in [(ax1, "tlb_app_per_s", 1, "Сбросы TLB на ЦП тика, 1/с"),
                           (ax2, "work_p99_us", 1, "p99 работы тика, мкс (лог. шкала)")]:
        pairs(ax, [(PIN_RU[c], ta[ta.config == c][col] * k if c in set(ta.config) else None,
                    tb[tb.config == c][col] * k) for c in PIN], rng)
        ax.set_ylabel(yl); ax.tick_params(axis="x", labelsize=8)
    ax1.set_yscale("log"); ax1.set_ylabel("Сбросы TLB на ЦП тика, 1/с (лог. шкала)")
    ax2.set_yscale("log")
    ax1.legend(fontsize=8, loc="lower right")
    fig.suptitle("ksmd-tlb: тик 60 Гц, 256 COW на тик, n=10 на точку", fontsize=9)
    save(fig, "stage3-tlb")

    # 5) пробуждение тика по вариантам привязки
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    pairs(ax, [(PIN_RU[c], pa[pa.config == c].lat_p99_us / 1000 if c in set(pa.config) else None,
                pb[pb.config == c].lat_p99_us / 1000) for c in PIN], rng)
    ax.set_yscale("log"); ax.set_ylabel("p99 опоздания пробуждения тика, мс (лог. шкала)")
    ax.set_title("ksmd-pin: тик 60 Гц, KSM вкл., n=10 на точку", fontsize=9)
    ax.tick_params(axis="x", labelsize=8); ax.legend(fontsize=8)
    save(fig, "stage3-pin-wake")

    # 6) частота и троттлинг по прогонам ksmd-pin (только натив)
    h = rd("stage3-hw"); h = h[h.experiment == "stage2-ksmd-pin"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.0, 3.8))
    for i, c in enumerate(PIN):
        strip(ax1, i, h[h.config == c].freq_app_max_mhz / 1000, NAT, rng)
        strip(ax2, i, h[h.config == c].throttle_pkg, NAT, rng)
    for ax in (ax1, ax2):
        ax.set_xticks(range(len(PIN)), [PIN_RU[c] for c in PIN], fontsize=8)
    ax1.set_ylabel("Частота ЦП тика, ГГц\n(медиана по секундам макс. среди ЦП тика)")
    ax2.set_ylabel("События троттлинга пакета за прогон")
    fig.suptitle("Натив, серия ksmd-pin: нагрев и частота в окне каждого прогона, n=10", fontsize=9)
    save(fig, "stage3-hw")

    # ---------------- блок Г ----------------
    WL = ["nginx", "python", "jvm", "node", "postgres"]
    WL_RU = {"nginx": "nginx", "python": "Python", "jvm": "JVM", "node": "Node.js", "postgres": "PostgreSQL"}
    va = rd("stage2-load")
    na = pd.concat([rd("stage3-load"), rd("stage3-load-np")])

    # 7) p99 в устойчивой фазе: ВМ против натива, KSM вкл. и выкл.
    fig, ax = plt.subplots(figsize=(8.4, 4.4))
    for i, wl in enumerate(WL):
        for j, mode in enumerate(["off", "on"]):
            sel = lambda d: d[(d.workload == wl) & (d.phase == "steady") & (d["mode"] == mode)].p99_ms
            x0 = i + (j - .5) * .42
            strip(ax, x0 - .1, sel(va), VM, rng, ("ВМ" if mode == "off" else None) if i == 0 else None, w=.08)
            strip(ax, x0 + .1, sel(na), NAT, rng, ("натив" if mode == "off" else None) if i == 0 else None, w=.08)
            ax.text(x0, 2e4, "KSM\n" + ("выкл." if mode == "off" else "вкл."), ha="center", va="top", fontsize=6.5)
    ax.set_xticks(range(len(WL)), [WL_RU[w] for w in WL])
    ax.set_yscale("log"); ax.set_ylim(.02, 3e4)
    ax.set_ylabel("p99 задержки запроса, мс (лог. шкала)")
    ax.set_title("Устойчивая фаза, одинаковые частоты (400/50/400/200/200 запр./с на контейнер), n=10", fontsize=9)
    ax.legend(fontsize=8, loc="lower center", ncol=2)
    save(fig, "stage3-load-p99")

    # 8) эффект KSM на p99: разность медиан вкл. − выкл. с 95 % ДИ
    vc = pd.read_csv(S / "stage2-load-compare.csv")
    vc = vc[(vc.phase == "steady") & (vc.metric == "p99_ms")]
    sn = pd.read_csv(S / "stage3-load-sensitivity.csv")
    sn = sn[(sn.phase == "steady") & (sn.metric == "p99_ms")]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.6, 4.6), gridspec_kw={"width_ratios": [1, 1.4]})
    for i, wl in enumerate(WL):
        r = vc[vc.workload == wl]
        if len(r):
            r = r.iloc[0]
            ax1.errorbar(r.diff_median, i, xerr=[[r.diff_median - r.diff_ci_lo], [r.diff_ci_hi - r.diff_median]],
                         fmt="o", color=VM, capsize=3)
    ax1.axvline(0, color="k", lw=.8); ax1.set_yticks(range(len(WL)), [WL_RU[w] for w in WL]); ax1.invert_yaxis()
    ax1.set_xlabel("p99 вкл. − выкл., мс"); ax1.set_title("ВМ (stage2-load)", fontsize=9)
    styles = [("stage3-load", "все", NAT, "o", "натив, частоты ВМ"),
              ("stage3-load-np", "все", NAT, "o", None),
              ("stage3-load-high", "все", "#8c1515", "s", "натив, ×4 частота"),
              ("stage3-load-high", "без перебоев", "#e377c2", "D", "×4, без прогонов с перебоями питания")]
    for k, (ser, sub, col, mk, lab) in enumerate(styles):
        for i, wl in enumerate(WL):
            r = sn[(sn.series == ser) & (sn.subset == sub) & (sn.workload == wl)]
            if not len(r):
                continue
            r = r.iloc[0]; y = i + {0: -.15, 1: -.15, 2: .08, 3: .25}[k]
            ax2.errorbar(r.diff_median, y, xerr=[[r.diff_median - r.diff_ci_lo], [r.diff_ci_hi - r.diff_median]],
                         fmt=mk, color=col, capsize=3, ms=5, label=lab if i == {0: 0, 1: 3, 2: 0, 3: 0}[k] else None)
    ax2.axvline(0, color="k", lw=.8); ax2.set_yticks(range(len(WL)), [WL_RU[w] for w in WL]); ax2.invert_yaxis()
    ax2.set_xlabel("p99 вкл. − выкл., мс"); ax2.set_title("натив (stage3-load*)", fontsize=9)
    fig.legend(fontsize=7.5, loc="lower center", ncol=3, bbox_to_anchor=(.5, 0))
    fig.suptitle("Эффект KSM на p99 в устойчивой фазе: медиана разности и 95 % бутстреп-ДИ, n=10+10", fontsize=9)
    fig.tight_layout(rect=(0, .07, 1, 1)); save(fig, "stage3-load-ksm-effect", tight=False)

    # 9) атрибуция: onfrac всплесков / onfrac случайных окон, по прогонам
    fig, ax = plt.subplots(figsize=(8.0, 4.2))
    sers = [("stage2-load", VM, "ВМ"), ("stage3-load", NAT, "натив"), ("stage3-load-np", NAT, None),
            ("stage3-load-high", "#8c1515", "натив, ×4 частота")]
    seen = set()
    for ser, col, lab in sers:
        d = rd(ser); d = d[(d["mode"] == "on") & (d.phase == "steady")]
        for i, wl in enumerate(WL):
            x = d[d.workload == wl]
            if not len(x):
                continue
            v = (x.attr_onfrac_bad / x.attr_onfrac_rand).to_numpy()
            dx = {"stage2-load": -.25, "stage3-load": 0, "stage3-load-np": 0, "stage3-load-high": .25}[ser]
            strip(ax, i + dx, v, col, rng, lab if lab and lab not in seen else None, w=.12); seen.add(lab)
    ax.axhline(1, color="k", lw=.8, ls="--")
    ax.set_xticks(range(len(WL)), [WL_RU[w] for w in WL])
    ax.set_ylabel("доля времени ksmd на ЦП:\nвсплески (≥ p99) / случайные окна")
    ax.set_title("KSM вкл., устойчивая фаза; >1 — ksmd чаще рядом со всплесками, n=10 на точку", fontsize=9)
    ax.legend(fontsize=8)
    save(fig, "stage3-load-attribution")

    # 10) цена и польза: загрузка ksmd и экономия памяти
    lv = pd.read_csv(S / "stage3-load-vm-vs-native.csv")
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.4, 4.0))
    pairs(ax1, [(WL_RU[w], va[(va.workload == w) & (va.phase == "steady") & (va["mode"] == "on")].ksmd_pct,
                 na[(na.workload == w) & (na.phase == "steady") & (na["mode"] == "on")].ksmd_pct) for w in WL], rng)
    ax1.set_ylabel("загрузка ksmd, % одного ЦП"); ax1.set_ylim(0, 31); ax1.legend(fontsize=8, ncol=2, loc="upper center"); ax1.tick_params(axis="x", labelsize=8)
    for i, w in enumerate(WL):
        r = lv[(lv.workload == w) & (lv.metric == "экономия вкл. − выкл., МиБ")].iloc[0]
        for dx, col, m in [(-.2, VM, r.vm_median), (.2, NAT, r.native_median)]:
            ax2.bar(i + dx, m, width=.36, color=col, alpha=.8)
    ax2.set_xticks(range(len(WL)), [WL_RU[w] for w in WL], fontsize=8)
    ax2.set_ylabel("экономия памяти, МиБ\n(медиана Δ(Rss−Pss) вкл. − медиана выкл.)")
    fig.suptitle("Цена и польза KSM под нагрузкой (устойчивая фаза), 8 контейнеров, n=10", fontsize=9)
    save(fig, "stage3-load-cost")


if __name__ == "__main__":
    main()
