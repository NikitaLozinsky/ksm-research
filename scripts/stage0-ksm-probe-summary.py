#!/usr/bin/env python3
"""
stage0-ksm-probe-summary.py — агрегирование серии stage0-ksm-probe.

Для каждого повтора (CSV сэмплера + JSON параметров) считает:
  saved_end_MiB    экономия в конце наблюдения = pages_sharing * 4 КиБ
  expected_MiB     теоретическая экономия = (P - K) * 4 КиБ (см. ksm_probe.c)
  shared_end       pages_shared в конце (теория: K)
  t50_s, t90_s, t100_s  время от включения KSM до 50 / 90 / 100 % теоретической
                   экономии (NaN, если не достигнуто — например, режим none)
  ksmd_cpu_s_conv  время ЦП ksmd от включения до t100 (секунды ЦП)
  ksmd_pct_conv    средняя загрузка одного ЦП ksmd за это время, %
  ksmd_pct_steady  средняя загрузка ksmd после t100 до конца (холостое
                   сканирование уже слитой памяти), %
  full_scans_end   число полных проходов к концу
  pss_before_kB, pss_end_kB  Pss_Anon процесса до включения KSM и в конце
  anon_drop_MiB    насколько уменьшился системный AnonPages (/proc/meminfo)
                   от момента включения KSM до конца — независимая проверка
  thp_split        сколько huge-страниц разбито за прогон
  cow_ksm          сколько COW-копий KSM-страниц было за прогон (ожидаем 0:
                   нагрузка после заполнения память не пишет)

Результаты:
  results/summary/stage0-ksm-probe-per-run.csv   (по одной строке на повтор)
  results/summary/stage0-ksm-probe.csv           (медиана/Q1/Q3/min/max/n по режимам)
и короткая таблица в stdout.

Использование:
  scripts/stage0-ksm-probe-summary.py [каталог серии]   (по умолчанию — последняя)
"""
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
PAGE_KIB = 4
# Для режима none времена схождения — NaN; предупреждения numpy об этом не нужны.
warnings.filterwarnings("ignore", message="Mean of empty slice")


def latest_series(name: str) -> Path:
    runs = sorted((REPO / "results/raw" / name).iterdir())
    return runs[-1]


def first_time(t: pd.Series, cond: pd.Series) -> float:
    """Первое время, где условие истинно (NaN, если никогда)."""
    hit = t[cond]
    return float(hit.iloc[0]) if len(hit) else float("nan")


def per_run(csv_path: Path) -> dict:
    params = json.loads(csv_path.with_suffix(".json").read_text())["run"]
    d = pd.read_csv(csv_path)
    t_on = d.loc[d["run"] == 1, "t_s"].iloc[0]
    d["t"] = d["t_s"] - t_on
    before = d[d["t"] < 0]
    after = d[d["t"] >= 0]

    pages = params["mib"] * 1024 // PAGE_KIB
    k = params["distinct"]
    expected_sharing = pages - k
    sharing = after["pages_sharing"]

    t50 = first_time(after["t"], sharing >= 0.5 * expected_sharing)
    t90 = first_time(after["t"], sharing >= 0.9 * expected_sharing)
    t100 = first_time(after["t"], sharing >= expected_sharing)

    # ЦП ksmd: разность накопленного времени ЦП (нс) между двумя моментами.
    def cpu_between(t_a: float, t_b: float) -> float:
        seg = after[(after["t"] >= t_a) & (after["t"] <= t_b)]
        if len(seg) < 2:
            return float("nan")
        return (seg["ksmd_cpu_ns"].iloc[-1] - seg["ksmd_cpu_ns"].iloc[0]) / 1e9

    t_end = float(after["t"].iloc[-1])
    if np.isnan(t100):
        conv_cpu = conv_pct = steady_pct = float("nan")
        # для режима none: средняя загрузка за всё наблюдение
        steady_pct = cpu_between(0, t_end) / t_end * 100
    else:
        conv_cpu = cpu_between(0, t100)
        conv_pct = conv_cpu / t100 * 100 if t100 > 0 else float("nan")
        steady_pct = cpu_between(t100, t_end) / (t_end - t100) * 100

    last = d.iloc[-1]
    return {
        "mode": params["mode"],
        "rep": params["rep"],
        "file": str(csv_path.relative_to(REPO)),
        "observed_s": round(t_end, 2),
        "expected_MiB": expected_sharing * PAGE_KIB / 1024,
        "saved_end_MiB": last["pages_sharing"] * PAGE_KIB / 1024,
        "shared_end": int(last["pages_shared"]),
        "sharing_end": int(last["pages_sharing"]),
        "t50_s": t50,
        "t90_s": t90,
        "t100_s": t100,
        "ksmd_cpu_s_conv": conv_cpu,
        "ksmd_pct_conv": conv_pct,
        "ksmd_pct_steady": steady_pct,
        "full_scans_end": int(last["full_scans"]),
        "general_profit_end_MiB": last["general_profit"] / 2**20,
        "pss_before_kB": float(before["pid_Pss_Anon_kB"].iloc[-1]),
        "pss_end_kB": float(last["pid_Pss_Anon_kB"]),
        "anon_drop_MiB": (before["AnonPages_kB"].iloc[-1] - last["AnonPages_kB"]) / 1024,
        "thp_split": int(last["thp_split_page"] - d["thp_split_page"].iloc[0]),
        "cow_ksm": int(last["cow_ksm"] - d["cow_ksm"].iloc[0]),
    }


def main() -> None:
    series = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else latest_series("stage0-ksm-probe")
    rows = [per_run(p) for p in sorted(series.glob("*-rep*.csv"))]
    runs = pd.DataFrame(rows).sort_values(["mode", "rep"])

    out_dir = REPO / "results/summary"
    out_dir.mkdir(parents=True, exist_ok=True)
    runs.insert(0, "series", str(series.relative_to(REPO)))
    runs.to_csv(out_dir / "stage0-ksm-probe-per-run.csv", index=False)

    metrics = ["saved_end_MiB", "shared_end", "t50_s", "t90_s", "t100_s",
               "ksmd_cpu_s_conv", "ksmd_pct_conv", "ksmd_pct_steady",
               "full_scans_end", "pss_before_kB", "pss_end_kB", "anon_drop_MiB",
               "thp_split", "cow_ksm"]
    agg = []
    for mode, g in runs.groupby("mode"):
        for m in metrics:
            v = g[m].astype(float)
            agg.append({"mode": mode, "metric": m, "n": int(v.notna().sum()),
                        "median": v.median(), "q1": v.quantile(0.25),
                        "q3": v.quantile(0.75), "min": v.min(), "max": v.max()})
    agg = pd.DataFrame(agg)
    agg.insert(0, "series", str(series.relative_to(REPO)))
    agg.to_csv(out_dir / "stage0-ksm-probe.csv", index=False)

    print(f"Серия: {series.relative_to(REPO)}; повторов по режимам:",
          runs.groupby("mode").size().to_dict())
    print(f"Теоретическая экономия: {runs['expected_MiB'].iloc[0]:.1f} МиБ")
    pd.set_option("display.width", 160)
    print(agg.drop(columns="series").to_string(index=False, float_format=lambda x: f"{x:.3g}"))


if __name__ == "__main__":
    main()
