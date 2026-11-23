#!/usr/bin/env python3
"""
stage2-load-pilot-summary.py — сводка пилота частот (этап 2, шаг 3).

Для каждой нагрузки и ступени частоты (запросов/с на контейнер, 8 контейнеров):
задержка p50/p99/p99.9 (от расписания), время сервера p50 (HTTP), доля
выполненных запросов от запланированных, ошибки.
«Насыщение» — первая ступень, где выполнено < 98 % запланированного ИЛИ
p50 задержки выросла более чем в 3 раза относительно самой низкой ступени.
Рекомендуемая частота — наибольшая ступень не выше половины частоты
насыщения (если насыщения нет — половина самой высокой ступени); это
ориентир «~30–50 % от насыщения» из плана этапа.

Результат: results/summary/stage2-load-pilot.csv и таблица в stdout.
"""
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("latlog", REPO / "scripts/lib/latlog.py")
latlog = importlib.util.module_from_spec(spec); spec.loader.exec_module(latlog)


def main():
    raw = REPO / "results/raw/stage2-load-pilot"
    series = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else sorted(raw.iterdir())[-1]
    rows = []
    for d in sorted(p for p in series.iterdir() if p.is_dir()):
        prm = json.loads((d / "params.json").read_text())["run"]
        L = latlog.load_latency(d)
        planned = prm["rate_per_ctr"] * prm["n"] * prm["step_s"]
        lat = L["latency"].dropna() * 1e3
        rows.append({"workload": prm["workload"], "rate_per_ctr": prm["rate_per_ctr"],
                     "planned": int(planned), "done": int(L["ok"].sum()),
                     "done_frac": L["ok"].sum() / planned,
                     "p50_ms": lat.median(), "p99_ms": lat.quantile(.99), "p999_ms": lat.quantile(.999),
                     "server_p50_ms": (L["server"].dropna() * 1e3).median()})
    t = pd.DataFrame(rows).sort_values(["workload", "rate_per_ctr"])
    t.insert(0, "series", str(series.relative_to(REPO)))
    t.to_csv(REPO / "results/summary/stage2-load-pilot.csv", index=False)
    pd.set_option("display.width", 160)
    print(t.drop(columns="series").to_string(index=False, float_format=lambda x: f"{x:.3g}"))
    print("\nРекомендация (ориентир, решение — с учётом таблицы):")
    for w, g in t.groupby("workload"):
        g = g.sort_values("rate_per_ctr"); base = g.p50_ms.iloc[0]
        sat = g[(g.done_frac < 0.98) | (g.p50_ms > 3 * base)]
        sat_rate = sat.rate_per_ctr.iloc[0] if len(sat) else None
        lim = (sat_rate / 2) if sat_rate else g.rate_per_ctr.max() / 2
        cand = g[g.rate_per_ctr <= lim].rate_per_ctr
        rec = cand.max() if len(cand) else g.rate_per_ctr.min()
        print(f"  {w}: насыщение {'не достигнуто' if sat_rate is None else f'с {sat_rate}/с'}; рекомендуемая частота {rec}/с на контейнер")


if __name__ == "__main__":
    main()
