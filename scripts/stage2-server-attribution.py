#!/usr/bin/env python3
"""
stage2-server-attribution.py — атрибуция по окнам ВРЕМЕНИ СЕРВЕРА.

В основной сводке окно запроса — [t_planned, t_done]: оно включает опоздание
самого генератора и остановки ВМ, когда стоит всё (в том числе ksmd). Здесь
окно — [t_sent, t_done] (только путь «запрос ушёл → ответ пришёл»), что ближе
к тому, что делает сервер. Для прогонов KSM вкл. HTTP-нагрузок, фазы merge и
steady: доля времени окна с ksmd на ЦП у всплесков (>= p99 времени сервера),
у обычных запросов и у случайных окон той же длины (20 перевыборок).

Использование: stage2-server-attribution.py <каталог серии> [префикс]
Результат: results/summary/<префикс>-server-attr.csv (по прогону и фазе).
"""
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


latlog = _load("latlog", "scripts/lib/latlog.py")
attr = _load("attr", "scripts/lib/attribution.py")


def main():
    series = Path(sys.argv[1]).resolve()
    name = sys.argv[2] if len(sys.argv) > 2 else series.parent.name
    rows = []
    for run in sorted(p for p in series.iterdir() if p.is_dir()):
        prm = json.loads((run / "params.json").read_text())["run"]
        if prm["mode"] != "on" or prm["workload"] == "postgres" or not (run / "perf-script.txt.gz").exists():
            continue
        k = pd.read_csv(run / "ksm.csv"); t_on = k.loc[k["run"] == 1, "t_mono"].iloc[0]
        L = latlog.load_latency(run).dropna(subset=["server"])
        ev = attr.load_perf(str(run / "perf-script.txt.gz")); iv = attr.ksmd_intervals(ev)
        mrg = float(prm["merge_s"])
        for ph, (a, z) in {"merge": (t_on, t_on + mrg), "steady": (t_on + mrg, np.inf)}.items():
            sel = L[(L.t_planned >= a) & (L.t_planned < z)]
            t_end, s = sel.t_done.to_numpy(), sel.server.to_numpy()
            frac = attr.overlap_seconds(t_end - s, t_end, iv) / np.maximum(s, 1e-9)
            bad = s >= np.quantile(s, 0.99)
            rng = np.random.default_rng(1); lens = s[bad]
            rf = [attr.overlap_seconds(st, st + lens, iv) / np.maximum(lens, 1e-9)
                  for st in (rng.uniform(t_end.min(), t_end.max() - lens.max(), len(lens)) for _ in range(20))]
            rows.append({"workload": prm["workload"], "pin": prm.get("pin", "none"), "rep": prm["rep"], "phase": ph,
                         "server_p99_ms": np.quantile(s, 0.99) * 1e3,
                         "onfrac_bad": frac[bad].mean(), "onfrac_norm": frac[~bad].mean(),
                         "onfrac_rand": np.concatenate(rf).mean(), "run": str(run.relative_to(REPO))})
    R = pd.DataFrame(rows)
    R.to_csv(REPO / f"results/summary/{name}-server-attr.csv", index=False)
    q = lambda v: f"{v.median():.3f} [{v.quantile(.25):.3f}–{v.quantile(.75):.3f}]"
    for key, g in R.groupby(["workload", "pin", "phase"]):
        print(*key, f"n={len(g)} | доля времени окна с ksmd: всплески {q(g.onfrac_bad)}, обычные {q(g.onfrac_norm)}, "
              f"случайные {q(g.onfrac_rand)} | p99 сервера {g.server_p99_ms.median():.1f} мс")


if __name__ == "__main__":
    main()
