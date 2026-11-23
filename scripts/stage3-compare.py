#!/usr/bin/env python3
"""
stage3-compare.py — количественное сравнение «ВМ против натива» (этап 3).

Для каждой ключевой метрики берутся значения по повторам (n=10 на платформу) из
сводок этапов 0–2 (ВМ) и этапа 3 (натив) и считаются:
  vm_median, native_median, ratio = vm_median / native_median
  ratio_ci_lo/hi  95 % бутстреп-интервал отношения медиан (10 000 перевыборок
                  повторов внутри каждой платформы, процентильный)
  mw_p            двусторонний тест Манна–Уитни (scipy), без поправок
Если одна из медиан равна 0, отношение не считается (NaN), остаётся тест.

Второй блок — сравнения ВНУТРИ натива, которые нужны для выводов этапа 3
(разность медиан с 95 % бутстреп-ДИ и тест Манна–Уитни): KSM вкл. против выкл.
в репетиции, варианты привязки ksmd против unpinned.

Результат: results/summary/stage3-vm-vs-native.csv и stage3-native-contrasts.csv.
"""
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu

REPO = Path(__file__).resolve().parents[1]
S = REPO / "results/summary"
RNG = np.random.default_rng(20261118)
B = 10000


def rd(name):
    return pd.read_csv(S / f"{name}-per-run.csv")


def boot(a, b, stat):
    """Бутстреп статистики stat(медиана a, медиана b) по повторам."""
    ia = RNG.integers(0, len(a), (B, len(a))); ib = RNG.integers(0, len(b), (B, len(b)))
    ma = np.median(a[ia], axis=1); mb = np.median(b[ib], axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        v = stat(ma, mb)
    v = v[np.isfinite(v)]
    return (np.percentile(v, 2.5), np.percentile(v, 97.5)) if len(v) > B * 0.9 else (np.nan, np.nan)


def pval(a, b):
    if np.all(a == a[0]) and np.all(b == b[0]) and a[0] == b[0]:
        return 1.0
    return mannwhitneyu(a, b, alternative="two-sided").pvalue


# (вывод, метрика, сводка ВМ, фильтр ВМ, сводка натива, фильтр натива)
VM_NAT = [
    ("таймер 1 кГц, cyclictest", "p99_us", "stage1-jitter-xcheck", "tool=='cyclictest'", "stage3-jitter-xcheck", "tool=='cyclictest'"),
    ("таймер 1 кГц, свой", "p99_us", "stage1-jitter-xcheck", "tool=='own'", "stage3-jitter-xcheck", "tool=='own'"),
    ("таймер 1 кГц, свой, p90", "p90_us", "stage1-jitter-xcheck", "tool=='own'", "stage3-jitter-xcheck", "tool=='own'"),
    ("cyclictest, p90", "p90_us", "stage1-jitter-xcheck", "tool=='cyclictest'", "stage3-jitter-xcheck", "tool=='cyclictest'"),
    ("COW слитой страницы, без THP", "us_per_page", "stage1-cow-cost", "config=='merged-nothp'", "stage3-cow-cost", "config=='merged-nothp'"),
    ("COW слитой страницы, THP", "us_per_page", "stage1-cow-cost", "config=='merged-thp'", "stage3-cow-cost", "config=='merged-thp'"),
    ("репетиция KSM вкл., пропуски", "missed_frac", "stage1-latency-rehearsal", "config=='ksmon-traceoff'", "stage3-rehearsal", "config=='ksmon-traceoff'"),
    ("репетиция KSM вкл., работа p99", "work_p99_us", "stage1-latency-rehearsal", "config=='ksmon-traceoff'", "stage3-rehearsal", "config=='ksmon-traceoff'"),
    ("репетиция KSM выкл., пробуждение p99", "lat_p99_us", "stage1-latency-rehearsal", "config=='ksmoff-traceoff'", "stage3-rehearsal", "config=='ksmoff-traceoff'"),
    ("ksmd-pin shared, пробуждение p99", "lat_p99_us", "stage2-ksmd-pin", "config=='shared'", "stage3-ksmd-pin", "config=='shared'"),
    ("ksmd-pin split, пробуждение p99", "lat_p99_us", "stage2-ksmd-pin", "config=='split'", "stage3-ksmd-pin", "config=='split'"),
    ("ksmd-pin unpinned, пробуждение p99", "lat_p99_us", "stage2-ksmd-pin", "config=='unpinned'", "stage3-ksmd-pin", "config=='unpinned'"),
    ("ksmd-tlb split, сбросы TLB/с", "tlb_app_per_s", "stage2-ksmd-pin-tlb-irq", "config=='split'", "stage3-ksmd-tlb-irq", "config=='split'"),
    ("ksmd-tlb split, работа p99", "work_p99_us", "stage2-ksmd-pin-tlb-irq", "config=='split'", "stage3-ksmd-tlb-irq", "config=='split'"),
    ("ksmd-tlb shared, работа p99", "work_p99_us", "stage2-ksmd-pin-tlb-irq", "config=='shared'", "stage3-ksmd-tlb-irq", "config=='shared'"),
    ("ksm-probe prctl, ЦП ksmd при слиянии, %", "ksmd_pct_conv", "stage0-ksm-probe", "mode=='prctl'", "stage3-ksm-probe", "mode=='prctl'"),
    ("ksm-probe prctl, экономия, МиБ", "saved_end_MiB", "stage0-ksm-probe", "mode=='prctl'", "stage3-ksm-probe", "mode=='prctl'"),
]

# (вывод, метрика, сводка, фильтр A, фильтр B) — разность медиан A − B на нативе
CONTRASTS = [
    ("репетиция: KSM вкл. − выкл., работа p50 (трасса выкл.)", "work_p50_us", "stage3-rehearsal", "config=='ksmon-traceoff'", "config=='ksmoff-traceoff'"),
    ("репетиция: KSM вкл. − выкл., работа p99 (трасса выкл.)", "work_p99_us", "stage3-rehearsal", "config=='ksmon-traceoff'", "config=='ksmoff-traceoff'"),
    ("репетиция: KSM вкл. − выкл., пробуждение p99 (трасса выкл.)", "lat_p99_us", "stage3-rehearsal", "config=='ksmon-traceoff'", "config=='ksmoff-traceoff'"),
    ("репетиция: трасса вкл. − выкл., пробуждение p50 (KSM выкл.)", "lat_p50_us", "stage3-rehearsal", "config=='ksmoff-traceon'", "config=='ksmoff-traceoff'"),
    ("ksmd-pin: shared − unpinned, пробуждение p99", "lat_p99_us", "stage3-ksmd-pin", "config=='shared'", "config=='unpinned'"),
    ("ksmd-pin: sibling − unpinned, пробуждение p99", "lat_p99_us", "stage3-ksmd-pin", "config=='sibling'", "config=='unpinned'"),
    ("ksmd-pin: split − unpinned, пробуждение p99", "lat_p99_us", "stage3-ksmd-pin", "config=='split'", "config=='unpinned'"),
    ("ksmd-tlb: split − shared, работа p99", "work_p99_us", "stage3-ksmd-tlb-irq", "config=='split'", "config=='shared'"),
    ("ksmd-tlb: sibling − shared, работа p99", "work_p99_us", "stage3-ksmd-tlb-irq", "config=='sibling'", "config=='shared'"),
    ("ksmd-tlb: sibling − split, работа p99", "work_p99_us", "stage3-ksmd-tlb-irq", "config=='sibling'", "config=='split'"),
    ("ksmd-tlb: split − shared, сбросы TLB/с", "tlb_app_per_s", "stage3-ksmd-tlb-irq", "config=='split'", "config=='shared'"),
]


def main():
    rows = []
    for name, m, fa, qa, fb, qb in VM_NAT:
        a = rd(fa).query(qa)[m].dropna().to_numpy(float); b = rd(fb).query(qb)[m].dropna().to_numpy(float)
        ma, mb = np.median(a), np.median(b)
        lo, hi = boot(a, b, lambda x, y: x / y) if mb > 0 and ma > 0 else (np.nan, np.nan)
        rows.append({"comparison": name, "metric": m, "n_vm": len(a), "n_native": len(b),
                     "vm_median": ma, "native_median": mb,
                     "ratio": ma / mb if mb > 0 and ma > 0 else np.nan,
                     "ratio_ci_lo": lo, "ratio_ci_hi": hi, "mw_p": pval(a, b),
                     "vm_source": fa, "native_source": fb})
    R = pd.DataFrame(rows); R.to_csv(S / "stage3-vm-vs-native.csv", index=False)
    rows = []
    for name, m, f, qa, qb in CONTRASTS:
        d = rd(f); a = d.query(qa)[m].dropna().to_numpy(float); b = d.query(qb)[m].dropna().to_numpy(float)
        lo, hi = boot(a, b, lambda x, y: x - y)
        rows.append({"contrast": name, "metric": m, "n_a": len(a), "n_b": len(b),
                     "median_a": np.median(a), "median_b": np.median(b),
                     "diff_median": np.median(a) - np.median(b), "diff_ci_lo": lo, "diff_ci_hi": hi,
                     "significant": bool(lo > 0 or hi < 0), "mw_p": pval(a, b), "source": f})
    C = pd.DataFrame(rows); C.to_csv(S / "stage3-native-contrasts.csv", index=False)
    pd.set_option("display.width", 220)
    print(R[["comparison", "vm_median", "native_median", "ratio", "ratio_ci_lo", "ratio_ci_hi", "mw_p"]].to_string(index=False, float_format="%.4g"))
    print()
    print(C[["contrast", "median_a", "median_b", "diff_median", "diff_ci_lo", "diff_ci_hi", "mw_p"]].to_string(index=False, float_format="%.4g"))


if __name__ == "__main__":
    main()
