#!/usr/bin/env python3
"""
stage3-power-flags.py — метки «питание и частота» по каждому прогону блока Г (этап 3).

Зачем: с 22:17 2026-11-18 разъём зарядки работал с перебоями (docs/limitations.md,
Л13). Ноутбук на секунды уходил на батарею, power-auto четыре раза включал
режим экономии (профиль power-saver). Здесь для каждого прогона считается,
задело ли его это, и какая была частота ЦП. Тогда в сравнении KSM вкл./выкл.
можно проверить устойчивость вывода: без задетых прогонов.

Окно прогона — от первого до последнего запланированного запроса фазы
измерения (t_planned_ns в lat-c*.csv.gz, CLOCK_MONOTONIC). Прогрев JVM
(warmup-lat-*) в окно не входит. У PostgreSQL журналов lat-c* нет (pgbench) —
окно всего прогона берётся по ksm.csv.

Источники питания:
  results/logs/blockG-ac.csv        смены AC0/online раз в 1 с (с 22:29)
  results/logs/blockG-env.csv       AC0/online раз в 10 с (весь блок)
  results/logs/blockG-powersave.log включения/выключения режима экономии
                                    (время по стене, дата 2026-11-18, MSK)
Столбцы на прогон:
  battery_s     секунд без сети в окне (по 1-секундному журналу, а где его нет —
                10 с на каждую запись env с ac=0)
  powersaver_s  секунд режима экономии в окне
  power_hit     battery_s > 0 или powersaver_s > 0
  throttle_pkg, freq_max_mhz  прирост троттлинга пакета и медиана по секундам
                максимума частоты среди всех ЦП (hw.csv серии)

Результат: results/summary/stage3-load-power-flags.csv
  scripts/stage3-power-flags.py <каталог серии> [...]
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
LOGS = REPO / "results/logs"


def mono_now_offset():
    """Связь стены и CLOCK_MONOTONIC берётся из env-журнала (t_wall, t_mono_s)."""
    e = pd.read_csv(LOGS / "blockG-env.csv")
    wall = pd.to_datetime(e.t_wall).map(lambda t: t.timestamp())
    return float(np.median(wall - e.t_mono_s)), e


def intervals_ac(off, env):
    """Интервалы [начало, конец) без сети, в секундах CLOCK_MONOTONIC."""
    out = []
    ac = pd.read_csv(LOGS / "blockG-ac.csv")
    t1 = ac.t_mono_s.iloc[0]                       # с этого момента есть 1-с журнал
    rows = ac.to_dict("records")
    for a, b in zip(rows, rows[1:] + [None]):
        if a["ac_online"] == 0:
            out.append((a["t_mono_s"], b["t_mono_s"] if b else a["t_mono_s"] + 1))
    for r in env[(env.t_mono_s < t1) & (env.ac_online == 0)].to_dict("records"):
        out.append((r["t_mono_s"], r["t_mono_s"] + 10))
    return out


def intervals_ps(off):
    """Интервалы режима экономии по журналу powersave (дата блока — 2026-11-18)."""
    out, start = [], None
    for line in (LOGS / "blockG-powersave.log").read_text().splitlines():
        hhmmss = line.split()[0]
        t = datetime.fromisoformat(f"2026-11-18T{hhmmss}+03:00").timestamp() - off
        if "включён" in line:
            start = t
        elif "выключен" in line and start is not None:
            out.append((start, t)); start = None
    return out


def overlap(iv, a, b):
    return sum(max(0.0, min(e, b) - max(s, a)) for s, e in iv)


def main():
    off, env = mono_now_offset()
    ac_iv, ps_iv = intervals_ac(off, env), intervals_ps(off)
    rows = []
    for d in map(Path, sys.argv[1:]):
        d = d.resolve()
        hw = pd.read_csv(d / "hw.csv")
        fcols = [c for c in hw.columns if c.startswith("freq_mhz_cpu")]
        for p in sorted(d.glob("*-rep[0-9][0-9]/params.json")):
            r = json.loads(p.read_text())["run"]
            ts = [pd.read_csv(f, usecols=["t_planned_ns"]).t_planned_ns
                  for f in sorted(p.parent.glob("lat-c*.csv.gz"))]
            if ts:
                t = pd.concat(ts)
            else:
                # PostgreSQL (pgbench) — окно всего прогона по ksm.csv (t_mono, с)
                t = pd.read_csv(p.parent / "ksm.csv", usecols=["t_mono"]).t_mono * 1e9
            a, b = t.min() / 1e9, t.max() / 1e9
            w = hw[(hw.t_mono_ns >= t.min()) & (hw.t_mono_ns <= t.max())]
            bat, ps = overlap(ac_iv, a, b), overlap(ps_iv, a, b)
            rows.append({
                "series": r["experiment"], "workload": r["workload"], "mode": r["mode"],
                "rep": r["rep"], "t_start_mono_s": round(a, 1), "window_s": round(b - a, 1),
                "battery_s": round(bat, 1), "powersaver_s": round(ps, 1),
                "power_hit": bool(bat > 0 or ps > 0),
                "throttle_pkg": int(w.pkg_throttle_count.max() - w.pkg_throttle_count.min()) if len(w) else np.nan,
                "freq_max_mhz": float(w[fcols].max(axis=1).median()) if len(w) else np.nan,
                "temp_max_c": w.pkg_temp_c.max() if len(w) else np.nan,
            })
    R = pd.DataFrame(rows)
    R.to_csv(REPO / "results/summary/stage3-load-power-flags.csv", index=False)
    print(R[R.power_hit].to_string(index=False))
    print()
    print(R.groupby(["series", "power_hit"]).agg(runs=("rep", "count"),
          freq_max=("freq_max_mhz", "median"), throttled=("throttle_pkg", lambda v: int((v > 0).sum()))).to_string())


if __name__ == "__main__":
    sys.exit(main())
