#!/usr/bin/env python3
"""
stage3-load-analysis.py — анализ блока Г этапа 3 (задержка под нагрузкой на нативе).

Опирается на сводки stage2-load-summary.py (по прогонам и фазам) и метки
питания stage3-power-flags.py. Три таблицы:

1) results/summary/stage3-load-vm-vs-native.csv
   ВМ (stage2-load, частоты 400/50/400/200/200) против натива на ТЕХ ЖЕ
   частотах (stage3-load + stage3-load-np): медианы по повторам, отношение
   медиан ВМ/натив с 95 % бутстреп-ДИ, тест Манна–Уитни. Метрики: p50/p99/p99.9
   задержки (KSM вкл. и выкл. отдельно), загрузка ksmd (KSM вкл.), экономия
   памяти — разность saved_share_MiB вкл. − выкл. (как в этапе 2, см.
   docstring stage2-load-summary.py), время слияния t99.

2) results/summary/stage3-load-sensitivity.csv
   Разность медиан KSM вкл. − выкл. (p99, p99.9) по всем сериям натива: на
   всех прогонах и без прогонов, задетых перебоями питания (power_hit, Л13).
   Вывод «KSM не меняет хвост» устойчив, если оба интервала содержат 0.

3) results/summary/stage3-load-attribution.csv
   Атрибуция всплесков (запросы >= p99 фазы) при KSM вкл.: средняя доля
   времени окна, когда ksmd был на ЦП (attr_onfrac_*), и COW на окно
   (attr_cow_*) для всплесков, обычных запросов и случайных окон; медиана по
   повторам; парный тест Уилкоксона bad против rand по повторам и отношение
   onfrac bad/rand. Если bad ≈ rand — всплески не связаны с ksmd.

Бутстреп: 10 000 перевыборок повторов внутри группы, процентильный 95 % ДИ,
seed 20261119.
"""
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu, wilcoxon

REPO = Path(__file__).resolve().parents[1]
S = REPO / "results/summary"
RNG = np.random.default_rng(20261119)
B = 10000


def boot(a, b, stat):
    if len(a) < 2 or len(b) < 2:
        return np.nan, np.nan
    ia = RNG.integers(0, len(a), (B, len(a))); ib = RNG.integers(0, len(b), (B, len(b)))
    with np.errstate(divide="ignore", invalid="ignore"):
        v = stat(np.median(a[ia], axis=1), np.median(b[ib], axis=1))
    v = v[np.isfinite(v)]
    return (np.percentile(v, 2.5), np.percentile(v, 97.5)) if len(v) > B * 0.9 else (np.nan, np.nan)


def pval(a, b):
    if len(a) < 2 or len(b) < 2:
        return np.nan
    if np.all(a == a[0]) and np.all(b == b[0]):
        return 1.0 if a[0] == b[0] else 0.0
    return mannwhitneyu(a, b, alternative="two-sided").pvalue


def per_rep_saving(d):
    """Экономия KSM по повторам: saved_share_MiB(on, rep) − медиана(off) той же нагрузки.
    (Повторы on и off не парные, поэтому вычитается медиана off.)"""
    x = d[d.phase == "steady"]
    off = x[x["mode"] == "off"].groupby("workload").saved_share_MiB.median()
    on = x[x["mode"] == "on"].copy()
    on["saving"] = on.saved_share_MiB - on.workload.map(off)
    return on


def main():
    vm = pd.read_csv(S / "stage2-load-per-run.csv")
    nat = pd.concat([pd.read_csv(S / f"{s}-per-run.csv") for s in ["stage3-load", "stage3-load-np"]])
    rows = []

    def add(wl, label, a, b):
        a = np.asarray(a, float); a = a[~np.isnan(a)]; b = np.asarray(b, float); b = b[~np.isnan(b)]
        if not len(a) or not len(b):
            return
        ma, mb = np.median(a), np.median(b)
        ok = ma > 0 and mb > 0
        lo, hi = boot(a, b, lambda x, y: x / y) if ok else (np.nan, np.nan)
        rows.append({"workload": wl, "metric": label, "n_vm": len(a), "n_native": len(b),
                     "vm_median": ma, "native_median": mb, "ratio": ma / mb if ok else np.nan,
                     "ratio_ci_lo": lo, "ratio_ci_hi": hi, "mw_p": pval(a, b)})

    for wl in sorted(vm.workload.unique()):
        v, n = vm[vm.workload == wl], nat[nat.workload == wl]
        for ph in ["merge", "steady"]:
            for mode in ["on", "off"]:
                for m in ["p50_ms", "p99_ms", "p999_ms"]:
                    sel = lambda d: d[(d.phase == ph) & (d["mode"] == mode)][m]
                    add(wl, f"{ph} KSM {mode}: {m}", sel(v), sel(n))
            sel = lambda d: d[(d.phase == ph) & (d["mode"] == "on")].ksmd_pct
            add(wl, f"{ph} KSM on: ksmd_pct", sel(v), sel(n))
        add(wl, "KSM on: merge_t99_s", v[(v.phase == "steady") & (v["mode"] == "on")].merge_t99_s,
            n[(n.phase == "steady") & (n["mode"] == "on")].merge_t99_s)
        sv, sn = per_rep_saving(v), per_rep_saving(n)
        add(wl, "экономия вкл. − выкл., МиБ", sv[sv.workload == wl].saving, sn[sn.workload == wl].saving)
    R = pd.DataFrame(rows); R.to_csv(S / "stage3-load-vm-vs-native.csv", index=False)

    # --- 2) устойчивость к перебоям питания
    flags = pd.read_csv(S / "stage3-load-power-flags.csv")[["series", "workload", "mode", "rep", "power_hit"]]
    rows = []
    for s in ["stage3-load", "stage3-load-np", "stage3-load-high"]:
        d = pd.read_csv(S / f"{s}-per-run.csv").merge(flags, on=["series", "workload", "mode", "rep"], how="left")
        d["power_hit"] = d.power_hit.astype("boolean").fillna(False).astype(bool)
        for wl in sorted(d.workload.unique()):
            for ph in ["pre", "merge", "steady"]:
                for m in ["p99_ms", "p999_ms"]:
                    for subset, dd in [("все", d), ("без перебоев", d[~d.power_hit])]:
                        x = dd[(dd.workload == wl) & (dd.phase == ph)]
                        a = x[x["mode"] == "on"][m].to_numpy(float); b = x[x["mode"] == "off"][m].to_numpy(float)
                        lo, hi = boot(a, b, lambda p, q: p - q)
                        rows.append({"series": s, "workload": wl, "phase": ph, "metric": m, "subset": subset,
                                     "n_on": len(a), "n_off": len(b),
                                     "on_median": np.median(a) if len(a) else np.nan,
                                     "off_median": np.median(b) if len(b) else np.nan,
                                     "diff_median": (np.median(a) - np.median(b)) if len(a) and len(b) else np.nan,
                                     "diff_ci_lo": lo, "diff_ci_hi": hi,
                                     "significant": bool(lo > 0 or hi < 0), "mw_p": pval(a, b)})
    T = pd.DataFrame(rows); T.to_csv(S / "stage3-load-sensitivity.csv", index=False)

    # --- 3) атрибуция всплесков при KSM вкл.
    cols = ["attr_onfrac_bad", "attr_onfrac_norm", "attr_onfrac_rand", "attr_cow_bad", "attr_cow_norm", "attr_cow_rand"]
    parts = []
    for s in ["stage2-load", "stage3-load", "stage3-load-np", "stage3-load-high"]:
        d = pd.read_csv(S / f"{s}-per-run.csv")
        d = d[(d["mode"] == "on") & d.phase.isin(["merge", "steady"])]
        g = d.groupby(["workload", "phase"])[cols].median().reset_index()
        # парный тест Уилкоксона по повторам: всплески против случайных окон
        pv = []
        for (wl, ph), x in d.groupby(["workload", "phase"]):
            r = {"workload": wl, "phase": ph}
            for k in ["onfrac", "cow"]:
                xx = x[[f"attr_{k}_bad", f"attr_{k}_rand"]].dropna()
                diff = xx.iloc[:, 0] - xx.iloc[:, 1]
                r[f"n_{k}"] = len(xx)
                r[f"wilcoxon_p_{k}"] = wilcoxon(diff).pvalue if len(xx) >= 5 and (diff != 0).any() else np.nan
            pv.append(r)
        g = g.merge(pd.DataFrame(pv), on=["workload", "phase"])
        g["onfrac_bad_to_rand"] = g.attr_onfrac_bad / g.attr_onfrac_rand
        g.insert(0, "series", s); parts.append(g)
    A = pd.concat(parts); A.to_csv(S / "stage3-load-attribution.csv", index=False)

    pd.set_option("display.width", 230); pd.set_option("display.max_rows", 300)
    print(R.to_string(index=False, float_format="%.4g")); print()
    sig = T[T.significant]
    print("значимых разностей вкл. − выкл.:", len(sig), "из", len(T)); print(sig.to_string(index=False, float_format="%.4g")); print()
    print(A.to_string(index=False, float_format="%.4g"))


if __name__ == "__main__":
    main()
