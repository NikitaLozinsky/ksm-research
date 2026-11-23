#!/usr/bin/env python3
"""
Проверка scripts/lib/attribution.py на синтетических данных с известным ответом.

Сценарий А («связь есть»): ksmd работает короткими отрезками; все «плохие»
пробуждения (большое опоздание) специально помещены внутрь отрезков ksmd.
Ожидание: у плохих окон доля ksmd_any = 1, обогащение к обычным и к
случайным окнам той же длины заметно > 1.

Сценарий Б («связи нет»): плохие пробуждения в случайных местах.
Ожидание: обогащение к случайным окнам той же длины ≈ 1.

Плюс проверка overlap_seconds на ручном примере.
Запуск:  python3 -I scripts/tests/test_attribution.py   (код выхода 0 = всё прошло)
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("attr", REPO / "scripts/lib/attribution.py")
attr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(attr)

ok = True


def check(cond, msg):
    global ok
    print(("  OK   " if cond else "  FAIL ") + msg)
    ok &= bool(cond)


# --- overlap_seconds: ручной пример ------------------------------------------
iv = np.array([[1.0, 2.0], [3.0, 3.5], [10.0, 11.0]])
got = attr.overlap_seconds(np.array([0.0, 1.5, 3.2, 5.0, 0.0]),
                           np.array([0.5, 3.2, 3.4, 6.0, 20.0]), iv)
check(np.allclose(got, [0.0, 0.5 + 0.2, 0.2, 0.0, 2.5]), f"overlap_seconds = {got}")

# --- синтетический прогон ------------------------------------------------------
rng = np.random.default_rng(0)
T, period = 60.0, 0.001
t_target = np.arange(0.0, T, period) + 1000.0          # «моменты тиков», CLOCK_MONOTONIC
n = len(t_target)
# ksmd: отрезки по 2 мс каждые 100 мс (занят 2 % времени)
ks = np.arange(1000.0, 1000.0 + T, 0.1) + 0.03
ksmd_iv = np.column_stack([ks, ks + 0.002])


def make_events(iv):
    rows = []
    for s, e in iv:
        rows.append({"t": s, "ev": "sched:sched_switch", "comm": "swapper", "pid": 0, "cpu": 0,
                     "prev_pid": 0, "next_pid": 57})
        rows.append({"t": e, "ev": "sched:sched_switch", "comm": "ksmd", "pid": 57, "cpu": 0,
                     "prev_pid": 57, "next_pid": 0})
        rows.append({"t": s + 0.001, "ev": "ksm:ksm_merge_with_ksm_page", "comm": "ksmd",
                     "pid": 57, "cpu": 0, "prev_pid": -1, "next_pid": -1})
    return pd.DataFrame(rows).sort_values("t", ignore_index=True)


ev = make_events(ksmd_iv)
base_lat = rng.uniform(20e-6, 80e-6, n)                 # обычные опоздания 20–80 мкс

# Сценарий А: 1 % тиков с опозданием 1.5 мс, начинающихся внутри отрезка ksmd
lat_a = base_lat.copy()
t_end_a = t_target + lat_a
# Ровно 1 % тиков (600) — по одному на каждый отрезок ksmd, без повторов.
# (Если всплесков меньше 1 %, порог p99 попадёт в обычные тики, и «плохие»
# окажутся смесью — тест проверял бы не то.)
assert len(ksmd_iv) == n // 100
bad_idx = np.arange(len(ksmd_iv))
for j, k in enumerate(bad_idx):
    i = int((ksmd_iv[k, 0] - 1000.0) / period)          # тик, совпадающий с началом ksmd
    lat_a[i] = 1.5e-3
    t_end_a[i] = t_target[i] + lat_a[i]
res_a = attr.analyze(t_end_a, lat_a, ev, None, q=0.99, shuffles=20)
print("Сценарий А (связь есть):")
print(res_a.to_string(index=False, float_format=lambda x: f"{x:.3g}"))
row = res_a.set_index("признак").loc["доля окон: ksmd_any"]
check(row["плохие"] > 0.95, f"доля плохих окон с ksmd = {row['плохие']:.3f} (ожидание ~1)")
check(row["обогащение_к_случайным"] > 3, f"обогащение к случайным = {row['обогащение_к_случайным']:.2f} (ожидание > 3)")

# Сценарий Б: плохие тики в случайных местах
lat_b = base_lat.copy()
idx = rng.choice(n, size=n // 100, replace=False)
lat_b[idx] = 1.5e-3
res_b = attr.analyze(t_target + lat_b, lat_b, ev, None, q=0.99, shuffles=50)
print("Сценарий Б (связи нет):")
print(res_b.to_string(index=False, float_format=lambda x: f"{x:.3g}"))
row = res_b.set_index("признак").loc["доля окон: ksmd_any"]
check(0.5 < row["обогащение_к_случайным"] < 2.0,
      f"обогащение к случайным = {row['обогащение_к_случайным']:.2f} (ожидание ~1)")

print("ИТОГ:", "все проверки пройдены" if ok else "ЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
