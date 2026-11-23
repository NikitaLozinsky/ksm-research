#!/usr/bin/env python3
"""
attribution.py — привязка всплесков задержки к событиям KSM.

Идея. Для каждого «события задержки» (пробуждение тика, запрос) известны
момент окончания t_end и задержка lat (всё по CLOCK_MONOTONIC). Окно
«ожидания» — [t_end - lat - pre, t_end]: в это время событие должно было уже
произойти, но не произошло. Для каждого окна считаются признаки:
  ksmd_on_s     сколько секунд ksmd был на ЦП внутри окна (из sched_switch);
  ksmd_any      был ли ksmd на ЦП хотя бы раз в окне (0/1);
  merges        число событий ksm_merge_with_ksm_page в окне;
  scan_bound    была ли граница полного прохода (ksm_start/stop_scan) в окне;
  cow           прирост cow_ksm за интервалы опроса vmstat, пересекающие окно
                (точность — интервал опроса, ~10 мс).
Затем «плохие» события (lat >= квантиль q, по умолчанию p99) сравниваются с
остальными: доля событий с признаком и ОБОГАЩЕНИЕ = P(признак | плохое) /
P(признак | обычное). Обогащение ~1 значит, что признак встречается у плохих
событий не чаще, чем у обычных, т.е. связь не видна. Сравнение с обычными
событиями обязательно: если ksmd работает 30 % времени, он «пересечётся» с
30 % любых окон случайно.

Плохие окна в среднем ДЛИННЕЕ обычных (задержка больше), поэтому чаще
пересекаются с любыми событиями. Чтобы это учесть, считается ещё один
контроль: те же признаки для «сдвинутых» окон той же длины, взятых в
случайные моменты прогона (shuffle, по умолчанию 20 раз), — это ожидаемая
доля пересечений при отсутствии связи для окон такой длины.

Использование (CLI):
  attribution.py --lat lat.csv --t-end-col t_wake_mono_ns --lat-col lateness_ns \\
      --unit ns --perf perf-script.txt.gz --vmstat vmstat.csv [--q 0.99] [--pre-ms 0]
Печатает таблицу и (с --out) пишет её в CSV.
"""
import argparse
import gzip
import re
import sys

import numpy as np
import pandas as pd

LINE_RE = re.compile(r"^\s*(?P<comm>.+?)\s+(?P<pid>\d+)\s+\[(?P<cpu>\d+)\]\s+"
                     r"(?P<t>\d+\.\d+):\s+(?P<ev>[\w:]+):\s?(?P<rest>.*)$")


def load_perf(path: str) -> pd.DataFrame:
    """Разбор текста `perf script -F comm,pid,cpu,time,event,trace`."""
    rows = []
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", errors="replace") as f:
        for line in f:
            m = LINE_RE.match(line)
            if not m:
                continue
            rest = m["rest"]
            row = {"t": float(m["t"]), "ev": m["ev"], "comm": m["comm"].strip(),
                   "pid": int(m["pid"]), "cpu": int(m["cpu"]),
                   "prev_pid": -1, "next_pid": -1}
            if m["ev"] == "sched:sched_switch":
                pp = re.search(r"prev_pid=(\d+)", rest)
                nn = re.search(r"next_pid=(\d+)", rest)
                row["prev_pid"] = int(pp.group(1)) if pp else -1
                row["next_pid"] = int(nn.group(1)) if nn else -1
            rows.append(row)
    return pd.DataFrame(rows).sort_values("t", ignore_index=True)


def ksmd_intervals(ev: pd.DataFrame) -> np.ndarray:
    """Интервалы [начало, конец], когда ksmd был на ЦП (по sched_switch)."""
    ksmd_pids = set(ev.loc[ev["comm"] == "ksmd", "pid"])
    sw = ev[ev["ev"] == "sched:sched_switch"]
    out, start = [], None
    for t, prev_pid, next_pid in sw[["t", "prev_pid", "next_pid"]].itertuples(index=False):
        if next_pid in ksmd_pids:
            start = t
        elif prev_pid in ksmd_pids and start is not None:
            out.append((start, t))
            start = None
    return np.array(out) if out else np.zeros((0, 2))


def overlap_seconds(a0: np.ndarray, a1: np.ndarray, iv: np.ndarray) -> np.ndarray:
    """Суммарное пересечение каждого окна [a0_i, a1_i] с набором интервалов iv."""
    if len(iv) == 0:
        return np.zeros(len(a0))
    starts, ends = iv[:, 0], iv[:, 1]
    cum = np.concatenate([[0.0], np.cumsum(ends - starts)])
    res = np.zeros(len(a0))
    for i, (x0, x1) in enumerate(zip(a0, a1)):
        lo = np.searchsorted(ends, x0, side="right")      # первый интервал, кончающийся после x0
        hi = np.searchsorted(starts, x1, side="left")     # первый интервал, начинающийся после x1
        if hi <= lo:
            continue
        full = cum[hi] - cum[lo]
        # подрезать крайние интервалы
        full -= max(0.0, x0 - starts[lo])
        full -= max(0.0, ends[hi - 1] - x1)
        res[i] = max(0.0, full)
    return res


def count_in(a0: np.ndarray, a1: np.ndarray, times: np.ndarray) -> np.ndarray:
    times = np.sort(times)
    return np.searchsorted(times, a1, side="right") - np.searchsorted(times, a0, side="left")


def cow_in(a0, a1, vm: pd.DataFrame) -> np.ndarray:
    """Прирост cow_ksm за интервалы опроса, пересекающие окно."""
    if vm is None or len(vm) < 2:
        return np.full(len(a0), np.nan)
    t = vm["t_mono"].to_numpy()
    c = vm["cow_ksm"].to_numpy()
    # прирост на интервале (t[k-1], t[k]] приписывается всему интервалу
    lo = np.searchsorted(t, a0, side="right")          # первый отсчёт после начала окна
    hi = np.searchsorted(t, a1, side="left")           # первый отсчёт не раньше конца окна
    hi = np.minimum(hi, len(t) - 1)
    lo = np.clip(lo, 1, len(t) - 1)
    return np.where(hi >= lo, c[hi] - c[lo - 1], 0)


def features(a0, a1, ev, iv, vm) -> pd.DataFrame:
    merges = ev.loc[ev["ev"] == "ksm:ksm_merge_with_ksm_page", "t"].to_numpy()
    bounds = ev.loc[ev["ev"].isin(["ksm:ksm_start_scan", "ksm:ksm_stop_scan"]), "t"].to_numpy()
    on = overlap_seconds(a0, a1, iv)
    return pd.DataFrame({
        "ksmd_on_s": on,
        "ksmd_any": (on > 0).astype(int),
        "merges": count_in(a0, a1, merges),
        "scan_bound": (count_in(a0, a1, bounds) > 0).astype(int),
        "cow": cow_in(a0, a1, vm),
    })


def analyze(t_end, lat, ev, vm, q=0.99, pre=0.0, shuffles=20, seed=1) -> pd.DataFrame:
    """Таблица: признак × (плохие, обычные, случайные окна той же длины)."""
    a1 = np.asarray(t_end, float)
    a0 = a1 - np.asarray(lat, float) - pre
    iv = ksmd_intervals(ev)
    f = features(a0, a1, ev, iv, vm)
    thr = np.quantile(lat, q)
    bad = np.asarray(lat) >= thr

    # Контроль: окна той же длины, что у плохих, в случайных местах прогона.
    rng = np.random.default_rng(seed)
    lo, hi = a1.min(), a1.max()
    lens = (a1 - a0)[bad]
    ctrl = []
    for _ in range(shuffles):
        s = rng.uniform(lo, hi - lens.max() if hi - lens.max() > lo else hi, size=len(lens))
        ctrl.append(features(s, s + lens, ev, iv, vm))
    ctrl = pd.concat(ctrl, ignore_index=True) if ctrl else f.iloc[:0]

    rows = []
    for col in ["ksmd_any", "scan_bound"]:
        pb, pn, pc = f.loc[bad, col].mean(), f.loc[~bad, col].mean(), ctrl[col].mean()
        rows.append({"признак": f"доля окон: {col}", "плохие": pb, "обычные": pn,
                     "случайные_той_же_длины": pc,
                     "обогащение_к_обычным": pb / pn if pn > 0 else np.nan,
                     "обогащение_к_случайным": pb / pc if pc > 0 else np.nan})
    for col in ["ksmd_on_s", "merges", "cow"]:
        mb, mn, mc = f.loc[bad, col].mean(), f.loc[~bad, col].mean(), ctrl[col].mean()
        rows.append({"признак": f"среднее: {col}", "плохие": mb, "обычные": mn,
                     "случайные_той_же_длины": mc,
                     "обогащение_к_обычным": mb / mn if mn > 0 else np.nan,
                     "обогащение_к_случайным": mb / mc if mc > 0 else np.nan})
    out = pd.DataFrame(rows)
    out.attrs.update({"threshold": thr, "n_bad": int(bad.sum()), "n_all": len(bad),
                      "ksmd_busy_frac": iv[:, 1].sum() - iv[:, 0].sum() if len(iv) else 0.0})
    span = a1.max() - a1.min()
    out.attrs["ksmd_busy_frac"] = (out.attrs["ksmd_busy_frac"] / span) if span > 0 else np.nan
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--lat", required=True)
    ap.add_argument("--t-end-col", default="t_wake_mono_ns")
    ap.add_argument("--lat-col", default="lateness_ns")
    ap.add_argument("--unit", choices=["ns", "us", "s"], default="ns")
    ap.add_argument("--perf", required=True)
    ap.add_argument("--vmstat")
    ap.add_argument("--q", type=float, default=0.99)
    ap.add_argument("--pre-ms", type=float, default=0.0)
    ap.add_argument("--out")
    a = ap.parse_args()
    k = {"ns": 1e-9, "us": 1e-6, "s": 1.0}[a.unit]
    lat = pd.read_csv(a.lat)
    ev = load_perf(a.perf)
    vm = pd.read_csv(a.vmstat) if a.vmstat else None
    res = analyze(lat[a.t_end_col].to_numpy() * k, lat[a.lat_col].to_numpy() * k, ev, vm,
                  q=a.q, pre=a.pre_ms / 1000)
    print(f"порог (квантиль {a.q}) = {res.attrs['threshold'] * 1e3:.3f} мс; плохих "
          f"{res.attrs['n_bad']} из {res.attrs['n_all']}; ksmd на ЦП "
          f"{res.attrs['ksmd_busy_frac'] * 100:.2f} % времени прогона")
    pd.set_option("display.width", 160)
    print(res.to_string(index=False, float_format=lambda x: f"{x:.4g}"))
    if a.out:
        res.to_csv(a.out, index=False)


if __name__ == "__main__":
    sys.exit(main())
