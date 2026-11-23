#!/usr/bin/env python3
"""
stage4-paired-analysis.py — парный анализ и тест эквивалентности (этап 4, блок 0, Р24).

Вход: сводка по прогонам от stage2-load-summary.py (<префикс>-per-run.csv) и
каталог сырой серии (для окон прогонов и hw.csv). Серия снята с PAIRED=1:
номер повтора = номер пары, прогоны «вкл.» и «выкл.» пары идут подряд.

Для каждой нагрузки, фазы (pre / merge / steady) и метрики (p50, p99, p99.9):
  r_i = ln(метрика_вкл_i / метрика_выкл_i)      — по парам i
  ratio        exp(медиана r_i)                    оценка отношения вкл./выкл.
  ci90_lo/hi   90 % бутстреп-ДИ exp(медианы r) по парам (10 000 перевыборок,
               seed 20261119) — для TOST на уровне 5 %
  ci95_lo/hi   95 % ДИ — для обычного вывода «есть эффект»
  wilcoxon_p   знаковый ранговый тест Уилкоксона по r_i (H0: медиана 0)
  equivalent   90 % ДИ целиком внутри [1/1.2; 1.2] (порог ±20 %, задан до опыта)
  verdict      «эквивалентно» / «есть эффект» (95 % ДИ не содержит 1) /
               «не определено» (ни то, ни другое)
Фаза pre — контроль: KSM ещё не включён ни в одном прогоне пары, отношение
должно быть ≈1; её разброс показывает остаточный дрейф внутри пары.
Те же расчёты — без пар, где хоть один прогон задет питанием (power_hit:
ac_online=0 или powersave_mode не off в окне прогона, по hw.csv серии).

Атрибуция (повтор находки этапа 3): по прогонам «вкл.» отношение
attr_onfrac_bad / attr_onfrac_rand, медиана и знаковый тест Уилкоксона.

Результаты: results/summary/<префикс>-paired.csv, <префикс>-paired-attr.csv,
<префикс>-power.csv.
  stage4-paired-analysis.py <каталог серии> <префикс>
Серия из нескольких кусков (остановка и продолжение с REP_START): каждый кусок
сводится stage2-load-summary.py под своим префиксом, затем
  stage4-paired-analysis.py <итоговый префикс> <каталог1>=<префикс1> <каталог2>=<префикс2> ...
Пара = (кусок, номер повтора); неполные пары (прогон без журналов) отбрасываются.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import wilcoxon

REPO = Path(__file__).resolve().parents[1]
S = REPO / "results/summary"
MARGIN = 1.2
B = 10000


def power_flags(raw):
    """Метки питания по окну каждого прогона (окно — t_planned журналов запросов)."""
    hw = pd.read_csv(raw / "hw.csv")
    rows = []
    for p in sorted(raw.glob("*-rep[0-9]*/params.json")):
        r = json.loads(p.read_text())["run"]
        ts = [pd.read_csv(f, usecols=["t_planned_ns"]).t_planned_ns for f in sorted(p.parent.glob("lat-c*.csv.gz"))]
        if ts:
            t = pd.concat(ts); a, b = t.min(), t.max()
        else:
            k = pd.read_csv(p.parent / "ksm.csv", usecols=["t_mono"]).t_mono * 1e9; a, b = k.min(), k.max()
        w = hw[(hw.t_mono_ns >= a) & (hw.t_mono_ns <= b)]
        bat = int((w.get("ac_online", pd.Series(dtype=float)) == 0).sum())
        ps = int((w.get("powersave_mode", pd.Series(dtype=object)).fillna("off").astype(str) != "off").sum())
        fcols = [c for c in hw.columns if c.startswith("freq_mhz_cpu")]
        rows.append({"workload": r["workload"], "mode": r["mode"], "rep": int(r["rep"]),
                     "pair_pos": int(r.get("pair_pos", 0) or 0), "battery_s": bat, "powersave_s": ps,
                     "power_hit": bat > 0 or ps > 0,
                     "throttle_pkg": int(w.pkg_throttle_count.max() - w.pkg_throttle_count.min()) if len(w) else np.nan,
                     "freq_max_mhz": float(w[fcols].max(axis=1).median()) if len(w) else np.nan,
                     "temp_max_c": w.pkg_temp_c.max() if len(w) else np.nan})
    return pd.DataFrame(rows)


def boot_ci(r, rng, levels=(90, 95)):
    idx = rng.integers(0, len(r), (B, len(r)))
    m = np.median(r[idx], axis=1)
    out = {}
    for L in levels:
        lo, hi = np.percentile(m, [(100 - L) / 2, 100 - (100 - L) / 2])
        out[L] = (float(np.exp(lo)), float(np.exp(hi)))
    return out


def main():
    if len(sys.argv) == 3 and "=" not in sys.argv[2]:
        parts = [(Path(sys.argv[1]).resolve(), sys.argv[2])]; pref = sys.argv[2]
    else:
        pref = sys.argv[1]
        parts = [(Path(a.split("=", 1)[0]).resolve(), a.split("=", 1)[1]) for a in sys.argv[2:]]
    rng = np.random.default_rng(20261119)
    ds, pws = [], []
    for k, (raw, pp) in enumerate(parts, 1):
        x = pd.read_csv(S / f"{pp}-per-run.csv"); x["part"] = k
        x["rep"] = k * 1000 + x.rep.astype(int)          # пара = (кусок, повтор)
        ds.append(x)
        w = power_flags(raw); w["part"] = k; w["rep"] = k * 1000 + w.rep; pws.append(w)
    d = pd.concat(ds, ignore_index=True)
    d = d[d.n_req.fillna(0) > 0]                          # прогоны без журналов запросов
    pw = pd.concat(pws, ignore_index=True); pw.to_csv(S / f"{pref}-power.csv", index=False)
    hit = pw.groupby(["workload", "rep"]).power_hit.any().rename("pair_hit").reset_index()
    rows = []
    for subset in ["все пары", "без перебоев питания"]:
        for (wl, ph), x in d.groupby(["workload", "phase"]):
            on = x[x["mode"] == "on"].set_index("rep"); off = x[x["mode"] == "off"].set_index("rep")
            reps = on.index.intersection(off.index)
            if subset != "все пары":
                bad = set(hit[(hit.workload == wl) & hit.pair_hit].rep)
                reps = [r for r in reps if r not in bad]
            for m in ["p50_ms", "p99_ms", "p999_ms"]:
                a = on.loc[reps, m].to_numpy(float); b = off.loc[reps, m].to_numpy(float)
                ok = (a > 0) & (b > 0); r = np.log(a[ok] / b[ok])
                if len(r) < 5:
                    continue
                ci = boot_ci(r, rng)
                eq = ci[90][0] >= 1 / MARGIN and ci[90][1] <= MARGIN
                eff = ci[95][0] > 1 or ci[95][1] < 1
                rows.append({"subset": subset, "workload": wl, "phase": ph, "metric": m, "n_pairs": len(r),
                             "on_median": np.median(a), "off_median": np.median(b),
                             "ratio": float(np.exp(np.median(r))),
                             "ci90_lo": ci[90][0], "ci90_hi": ci[90][1], "ci95_lo": ci[95][0], "ci95_hi": ci[95][1],
                             "wilcoxon_p": wilcoxon(r).pvalue if np.any(r != 0) else 1.0,
                             "equivalent": eq,
                             "verdict": "эквивалентно" if eq else ("есть эффект" if eff else "не определено")})
    R = pd.DataFrame(rows); R.to_csv(S / f"{pref}-paired.csv", index=False)

    att = []
    for (wl, ph), x in d[(d["mode"] == "on") & d.phase.isin(["merge", "steady"])].groupby(["workload", "phase"]):
        v = (x.attr_onfrac_bad / x.attr_onfrac_rand).dropna().to_numpy()
        diff = (x.attr_onfrac_bad - x.attr_onfrac_rand).dropna().to_numpy()
        att.append({"workload": wl, "phase": ph, "n": len(v), "bad_to_rand_median": np.median(v),
                    "bad_to_rand_q1": np.percentile(v, 25), "bad_to_rand_q3": np.percentile(v, 75),
                    "runs_above_1": int((v > 1).sum()),
                    "wilcoxon_p": wilcoxon(diff).pvalue if len(diff) >= 5 and np.any(diff != 0) else np.nan})
    A = pd.DataFrame(att); A.to_csv(S / f"{pref}-paired-attr.csv", index=False)

    pd.set_option("display.width", 220); pd.set_option("display.max_rows", 200)
    print(R.to_string(index=False, float_format="%.4g")); print()
    print(A.to_string(index=False, float_format="%.4g")); print()
    print("прогонов, задетых питанием:", int(pw.power_hit.sum()), "из", len(pw),
          "; с троттлингом:", int((pw.throttle_pkg > 0).sum()))


if __name__ == "__main__":
    main()
