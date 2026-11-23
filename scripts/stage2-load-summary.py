#!/usr/bin/env python3
"""
stage2-load-summary.py — сводка основной серии этапа 2 (задержка под нагрузкой).

Фазы прогона (относительно момента включения KSM t_on, одинаковые для on/off):
  pre     [t_on − PRE_S, t_on)          базовая линия, run=0
  merge   [t_on, t_on + MERGE_S)        окно слияния
  steady  [t_on + MERGE_S, конец)       устойчивая фаза
Запрос относится к фазе по запланированному моменту t_planned.

По прогону и фазе: n запросов, доля ошибок, задержка p50/p99/p99.9 (мс, от
расписания, Р21), время сервера p50/p99 (HTTP), COW за фазу, средняя загрузка
ksmd (% одного ЦП) за фазу; по прогону — t99 слияния (с от t_on) и экономия
памяти Δ(RssAnon − PssAnon) к концу (МиБ). ВНИМАНИЕ: под нагрузкой Δ(Rss − Pss)
меняется и без KSM (процессы после fork дописывают общие страницы — у
PostgreSQL прирост отрицательный), поэтому эффект KSM на память под нагрузкой —
это РАЗНОСТЬ этой метрики между вариантами on и off (строка saved_share_MiB в
сравнении).
Для прогонов с трассой — атрибуция всплесков (>= p99 фазы) против обычных
запросов и случайных окон той же длины (scripts/lib/attribution.py):
  attr_ksmd_*      доля окон, где ksmd хоть раз был на ЦП. Для длинных окон
                   (> 20 мс при sleep_millisecs=20) насыщается до 1 и почти
                   ничего не различает — показывается для полноты;
  attr_onfrac_*    средняя ДОЛЯ времени окна, когда ksmd был на ЦП (ksmd_on_s /
                   длина окна) — основной признак, не насыщается;
  attr_cow_*       среднее число COW (cow_ksm) на окно.

Сравнение on против off по каждой нагрузке и фазе (по повторам):
  медианы и IQR метрики у обоих вариантов; разность медиан (on − off) с 95 %
  бутстреп-интервалом (10000 перевыборок повторов внутри каждого варианта);
  тест Манна–Уитни (двусторонний). Вывод «KSM меняет метрику» — только если
  интервал не содержит 0.

Результаты: results/summary/<префикс>-per-run.csv, <префикс>-compare.csv.
Использование: stage2-load-summary.py <каталог серии> [префикс=stage2-load]
"""
import importlib.util
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu

REPO = Path(__file__).resolve().parents[1]
warnings.filterwarnings("ignore", message="Mean of empty slice")


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


latlog = _load("latlog", "scripts/lib/latlog.py")
attr = _load("attr", "scripts/lib/attribution.py")


def q(x, p):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    return float(np.percentile(x, p)) if len(x) else float("nan")


def per_run(run: Path) -> list[dict]:
    prm = json.loads((run / "params.json").read_text())["run"]
    k = pd.read_csv(run / "ksm.csv")
    t_on = k.loc[k["run"] == 1, "t_mono"].iloc[0]
    pre, mrg = float(prm["pre_s"]), float(prm["merge_s"])
    L = latlog.load_latency(run)
    after = k[k.t_mono >= t_on]
    final = after.pages_sharing.iloc[-1]
    t99 = (after.loc[after.pages_sharing >= 0.99 * final, "t_mono"].iloc[0] - t_on) if final > 0 else float("nan")
    m = pd.read_csv(run / "mem.csv")
    tot = m.groupby("t_mono")[["pss_anon_kB", "rss_anon_kB"]].sum().reset_index()
    b = tot[tot.t_mono < t_on].iloc[-1]; e = tot.iloc[-1]
    saved = ((e.rss_anon_kB - e.pss_anon_kB) - (b.rss_anon_kB - b.pss_anon_kB)) / 1024
    ev = vm = None
    if (run / "perf-script.txt.gz").exists() and prm["mode"] == "on":
        ev = attr.load_perf(str(run / "perf-script.txt.gz"))
        vm = pd.read_csv(run / "vmstat.csv")
    phases = {"pre": (t_on - pre, t_on), "merge": (t_on, t_on + mrg), "steady": (t_on + mrg, np.inf)}
    out = []
    for ph, (a, z) in phases.items():
        sel = L[(L.t_planned >= a) & (L.t_planned < z)]
        kk = k[(k.t_mono >= a) & (k.t_mono < min(z, k.t_mono.max() + 1))]
        span = kk.t_mono.iloc[-1] - kk.t_mono.iloc[0] if len(kk) > 1 else float("nan")
        lat = sel.latency.to_numpy() * 1e3
        row = {"workload": prm["workload"], "mode": prm["mode"], "pin": prm.get("pin", "none"),
               "rep": prm["rep"], "phase": ph,
               "n_req": len(sel), "err_frac": 1 - sel.ok.mean() if len(sel) else float("nan"),
               "p50_ms": q(lat, 50), "p99_ms": q(lat, 99), "p999_ms": q(lat, 99.9),
               "server_p50_ms": q(sel.server * 1e3, 50), "server_p99_ms": q(sel.server * 1e3, 99),
               "cow_ksm": int(kk.cow_ksm.iloc[-1] - kk.cow_ksm.iloc[0]) if len(kk) > 1 else 0,
               "ksmd_pct": (kk.ksmd_cpu_ns.iloc[-1] - kk.ksmd_cpu_ns.iloc[0]) / 1e9 / span * 100
               if len(kk) > 1 and span > 0 else float("nan"),
               "merge_t99_s": t99, "saved_share_MiB": saved, "run": str(run.relative_to(REPO))}
        if ev is not None and ph != "pre":
            ok = sel.dropna(subset=["latency"])
            if len(ok) > 200:
                t_end, lat_s = ok.t_done.to_numpy(), ok.latency.to_numpy()
                r = attr.analyze(t_end, lat_s, ev, vm, q=0.99).set_index("признак")
                # доля времени окна с ksmd на ЦП: плохие / обычные / случайные той же длины
                iv = attr.ksmd_intervals(ev)
                a0 = t_end - lat_s
                frac = attr.overlap_seconds(a0, t_end, iv) / np.maximum(lat_s, 1e-9)
                bad = lat_s >= np.quantile(lat_s, 0.99)
                rng = np.random.default_rng(1)
                lens = lat_s[bad]
                lo_t, hi_t = t_end.min(), t_end.max()
                rf = []
                for _ in range(20):
                    st = rng.uniform(lo_t, max(lo_t, hi_t - lens.max()), size=len(lens))
                    rf.append(attr.overlap_seconds(st, st + lens, iv) / np.maximum(lens, 1e-9))
                row.update({"attr_onfrac_bad": float(frac[bad].mean()), "attr_onfrac_norm": float(frac[~bad].mean()),
                            "attr_onfrac_rand": float(np.concatenate(rf).mean())})
                row.update({"attr_ksmd_bad": r.loc["доля окон: ksmd_any", "плохие"],
                            "attr_ksmd_norm": r.loc["доля окон: ksmd_any", "обычные"],
                            "attr_ksmd_rand": r.loc["доля окон: ksmd_any", "случайные_той_же_длины"],
                            "attr_cow_bad": r.loc["среднее: cow", "плохие"],
                            "attr_cow_norm": r.loc["среднее: cow", "обычные"],
                            "attr_cow_rand": r.loc["среднее: cow", "случайные_той_же_длины"]})
        out.append(row)
    return out


def boot_diff(a, b, n=10000, seed=0):
    rng = np.random.default_rng(seed)
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = [np.median(rng.choice(a, len(a))) - np.median(rng.choice(b, len(b))) for _ in range(n)]
    return np.percentile(d, [2.5, 97.5])


def main():
    series = Path(sys.argv[1]).resolve()
    name = sys.argv[2] if len(sys.argv) > 2 else "stage2-load"
    rows = []
    for run in sorted(p for p in series.iterdir() if p.is_dir()):
        rows += per_run(run)
    R = pd.DataFrame(rows)
    out = REPO / "results/summary"
    R.insert(0, "series", str(series.relative_to(REPO)))
    R.to_csv(out / f"{name}-per-run.csv", index=False)
    comp = []
    metrics = ["p50_ms", "p99_ms", "p999_ms", "server_p99_ms", "err_frac", "cow_ksm", "ksmd_pct", "saved_share_MiB"]

    def compare(key, A, B, label_a, label_b):
        for mtr in metrics:
            a, b = A[mtr].dropna(), B[mtr].dropna()
            if len(a) < 3 or len(b) < 3:
                continue
            lo, hi = boot_diff(a, b)
            p = mannwhitneyu(a, b, alternative="two-sided").pvalue if (a.nunique() > 1 or b.nunique() > 1) else float("nan")
            comp.append({**key, "compare": f"{label_a} − {label_b}", "metric": mtr, "n_a": len(a), "n_b": len(b),
                         "a_median": a.median(), "a_q1": a.quantile(.25), "a_q3": a.quantile(.75),
                         "b_median": b.median(), "b_q1": b.quantile(.25), "b_q3": b.quantile(.75),
                         "diff_median": a.median() - b.median(), "diff_ci_lo": lo, "diff_ci_hi": hi,
                         "mw_p": p, "significant": bool(lo > 0 or hi < 0)})

    # KSM вкл. против выкл. — внутри одной привязки к ЦП
    for (w, pn, ph), g in R.groupby(["workload", "pin", "phase"]):
        compare({"workload": w, "pin": pn, "phase": ph}, g[g["mode"] == "on"], g[g["mode"] == "off"],
                "KSM вкл.", "KSM выкл.")
    # split против none — внутри одного режима KSM (шаг 5)
    for (w, m, ph), g in R.groupby(["workload", "mode", "phase"]):
        if g["pin"].nunique() > 1:
            compare({"workload": w, "mode": m, "phase": ph}, g[g["pin"] == "split"], g[g["pin"] == "none"],
                    "split", "none")
    C = pd.DataFrame(comp)
    C.insert(0, "series", str(series.relative_to(REPO)))
    C.to_csv(out / f"{name}-compare.csv", index=False)
    pd.set_option("display.width", 200)
    if len(C):
        show = C[C.metric.isin(["p50_ms", "p99_ms", "p999_ms"])]
        print(show.drop(columns=["series", "a_q1", "a_q3", "b_q1", "b_q3"]).to_string(
            index=False, float_format=lambda x: f"{x:.3g}"))
    else:
        print("сравнений нет (меньше 3 повторов в группах)")


if __name__ == "__main__":
    main()
