#!/usr/bin/env python3
"""
stage1-workloads-summary.py — сводка серий stage1-workloads (реальные нагрузки).

По каждому прогону (нагрузка W, N контейнеров, настройка KSM C, повтор):
  pss_before_MiB      сумма Pss_Anon всех процессов всех контейнеров перед
                      включением KSM (последний отсчёт до run=1);
  pss_end_MiB         то же в конце наблюдения;
  saved_pss_MiB/_pct  падение Pss (до − после). ВНИМАНИЕ: смешивает эффект KSM с
                      собственной динамикой памяти приложения (GC, рост кучи);
  saved_share_MiB     ГЛАВНАЯ метрика экономии: прирост (RssAnon − PssAnon) по всем
                      процессам. Страница, общая для k отображений, даёт в Rss
                      k копий, а в Pss одну, т.е. Rss − Pss = число «лишних»
                      копий. Рост/сжатие собственной памяти приложения меняет Rss
                      и Pss одинаково и в разности сокращается;
  saved_share_pct     saved_share_MiB в % от RssAnon до включения KSM (сколько
                      анонимной памяти контейнеров удалось не хранить);
  saved_ksm_MiB       pages_sharing × 4 КиБ в конце (оценка самого KSM);
  general_profit_MiB  оценка выгоды KSM с учётом затрат на rmap_item (ksm.rst);
  rss_anon_before_MiB RssAnon до (не уменьшается от KSM — контроль);
  cg_anon_before/end  memory.stat:anon cgroup контейнеров до/после;
  per_ctr_pss_MiB     Pss на контейнер до KSM;
  t50_s, t90_s        время от включения KSM до 50 % / 90 % конечного pages_sharing;
  ksmd_cpu_s, ksmd_pct  время ЦП ksmd за наблюдение и средняя загрузка (% одного ЦП);
                      ЗАВИСЯТ от длины наблюдения, разной у конфигураций;
  ksmd_cpu_t90_s, ksmd_cpu_t99_s  время ЦП ksmd от включения KSM до 90 % / 99 %
                      конечного pages_sharing — «цена» достижения экономии, не
                      зависящая от того, сколько ещё наблюдали после (этап 2);
  full_scans          число полных проходов за наблюдение;
  converged, elapsed_s  критерий окончания (stable / max, см. decisions Р17).
  conv_ok             ПРОВЕРКА схождения задним числом (добавлена после серий этапа 1,
                      Р17): за наблюдение было >= 3 полных прохода И за последние 30 с
                      pages_sharing вырос не больше чем на max(1 %, 256 страниц).
                      conv_ok = 0 — KSM, вероятно, ещё не закончил слияние, экономия
                      этого прогона — НИЖНЯЯ граница.
По конфигурациям: медиана, Q1, Q3, min, max, n.

Использование: stage1-workloads-summary.py <каталог серии> [префикс=stage1-workloads]
Результаты: results/summary/<префикс>-per-run.csv, <префикс>.csv
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]


def first_t(t, cond):
    hit = t[cond]
    return float(hit.iloc[0]) if len(hit) else float("nan")


def per_run(pj: Path) -> dict:
    prm = json.loads(pj.read_text())["run"]
    base = pj.with_suffix("")
    k = pd.read_csv(base.with_suffix(".ksm.csv"))
    m = pd.read_csv(base.with_suffix(".mem.csv"))
    end = json.loads(base.with_suffix(".end.json").read_text())
    t_on = k.loc[k["run"] == 1, "t_mono"].iloc[0]
    tot = m.groupby("t_mono")[["pss_anon_kB", "rss_anon_kB", "cg_anon_kB", "n_procs"]].sum().reset_index()
    before = tot[tot["t_mono"] < t_on]
    b = before.iloc[-1] if len(before) else tot.iloc[0]
    e = tot.iloc[-1]
    after = k[k["t_mono"] >= t_on]
    final = after["pages_sharing"].iloc[-1]
    tt = after["t_mono"] - t_on
    cpu = (after["ksmd_cpu_ns"].iloc[-1] - after["ksmd_cpu_ns"].iloc[0]) / 1e9

    def cpu_until(frac):
        if final <= 0:
            return float("nan")
        hit = after[after["pages_sharing"] >= frac * final]
        return (hit["ksmd_cpu_ns"].iloc[0] - after["ksmd_cpu_ns"].iloc[0]) / 1e9
    span = tt.iloc[-1]
    n = int(prm["n"])
    t_end = tt.iloc[-1]
    ps30 = after.loc[tt <= t_end - 30, "pages_sharing"]
    g30 = final - (ps30.iloc[-1] if len(ps30) else after["pages_sharing"].iloc[0])
    fscans = int(after["full_scans"].iloc[-1] - after["full_scans"].iloc[0])
    conv_ok = int(fscans >= 3 and g30 <= max(0.01 * final, 256))
    return {
        "workload": prm["workload"], "n": n, "ksm_cfg": prm["ksm_cfg"], "rep": prm["rep"],
        "pss_before_MiB": b["pss_anon_kB"] / 1024, "pss_end_MiB": e["pss_anon_kB"] / 1024,
        "saved_pss_MiB": (b["pss_anon_kB"] - e["pss_anon_kB"]) / 1024,
        "saved_pss_pct": (b["pss_anon_kB"] - e["pss_anon_kB"]) / b["pss_anon_kB"] * 100
        if b["pss_anon_kB"] else float("nan"),
        "saved_share_MiB": ((e["rss_anon_kB"] - e["pss_anon_kB"]) - (b["rss_anon_kB"] - b["pss_anon_kB"])) / 1024,
        "saved_share_pct": ((e["rss_anon_kB"] - e["pss_anon_kB"]) - (b["rss_anon_kB"] - b["pss_anon_kB"]))
        / b["rss_anon_kB"] * 100 if b["rss_anon_kB"] else float("nan"),
        "saved_ksm_MiB": final * 4 / 1024,
        "general_profit_MiB": after["general_profit"].iloc[-1] / 2**20,
        "rss_anon_before_MiB": b["rss_anon_kB"] / 1024, "rss_anon_end_MiB": e["rss_anon_kB"] / 1024,
        "cg_anon_before_MiB": b["cg_anon_kB"] / 1024, "cg_anon_end_MiB": e["cg_anon_kB"] / 1024,
        "per_ctr_pss_MiB": b["pss_anon_kB"] / 1024 / n, "procs": int(b["n_procs"]),
        "t50_s": first_t(tt, after["pages_sharing"] >= 0.5 * final) if final > 0 else float("nan"),
        "t90_s": first_t(tt, after["pages_sharing"] >= 0.9 * final) if final > 0 else float("nan"),
        "ksmd_cpu_s": cpu, "ksmd_pct": cpu / span * 100 if span > 0 else float("nan"),
        "ksmd_cpu_t90_s": cpu_until(0.9), "ksmd_cpu_t99_s": cpu_until(0.99),
        "t99_s": first_t(tt, after["pages_sharing"] >= 0.99 * final) if final > 0 else float("nan"),
        "full_scans": int(after["full_scans"].iloc[-1] - after["full_scans"].iloc[0]),
        "converged": end["converged"], "elapsed_s": end["elapsed_s"],
        "conv_ok": conv_ok, "growth_last30_pages": int(g30),
        "file": str(base.relative_to(REPO)),
    }


def main() -> None:
    series = Path(sys.argv[1]).resolve()
    name = sys.argv[2] if len(sys.argv) > 2 else "stage1-workloads"
    runs = pd.DataFrame([per_run(p) for p in sorted(series.glob("*-rep*.json"))
                         if not p.name.endswith(".end.json")])
    runs = runs.sort_values(["workload", "n", "ksm_cfg", "rep"])
    out = REPO / "results/summary"
    runs.insert(0, "series", str(series.relative_to(REPO)))
    runs.to_csv(out / f"{name}-per-run.csv", index=False)
    metrics = ["pss_before_MiB", "pss_end_MiB", "saved_pss_MiB", "saved_pss_pct", "saved_share_MiB",
               "saved_share_pct", "saved_ksm_MiB",
               "general_profit_MiB", "rss_anon_before_MiB", "cg_anon_before_MiB", "cg_anon_end_MiB",
               "per_ctr_pss_MiB", "t50_s", "t90_s", "t99_s", "ksmd_cpu_s", "ksmd_pct",
               "ksmd_cpu_t90_s", "ksmd_cpu_t99_s", "full_scans", "elapsed_s"]
    agg = []
    for (w, n, c), g in runs.groupby(["workload", "n", "ksm_cfg"]):
        for mtr in metrics:
            v = g[mtr].astype(float).dropna()
            if not len(v):
                continue
            agg.append({"workload": w, "n": n, "ksm_cfg": c, "metric": mtr, "runs": len(v),
                        "median": v.median(), "q1": v.quantile(.25), "q3": v.quantile(.75),
                        "min": v.min(), "max": v.max(),
                        "converged_max": int((g["converged"] == "max").sum()),
                        "conv_ok_runs": int(g["conv_ok"].sum())})
    agg = pd.DataFrame(agg)
    agg.insert(0, "series", str(series.relative_to(REPO)))
    agg.to_csv(out / f"{name}.csv", index=False)
    print(f"Серия: {series.relative_to(REPO)}")
    pd.set_option("display.width", 200)
    show = ["rss_anon_before_MiB", "saved_share_MiB", "saved_share_pct", "saved_ksm_MiB", "saved_pss_MiB",
            "general_profit_MiB",
            "per_ctr_pss_MiB", "t90_s", "ksmd_pct", "elapsed_s"]
    pv = agg[agg["metric"].isin(show)].pivot_table(index=["workload", "n", "ksm_cfg"],
                                                   columns="metric", values="median")
    print(pv[show].to_string(float_format=lambda x: f"{x:.3g}"))
    print("Прогонов, упёршихся в MAX_S:", int((runs["converged"] == "max").sum()), "из", len(runs))
    bad = runs[runs["conv_ok"] == 0].groupby(["workload", "n", "ksm_cfg"]).size()
    print("Прогоны без подтверждённого схождения (conv_ok=0; экономия — нижняя граница):")
    print(bad.to_string() if len(bad) else "  нет")


if __name__ == "__main__":
    main()
