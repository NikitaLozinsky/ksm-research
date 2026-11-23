#!/usr/bin/env python3
"""
latlog.py — чтение журналов задержки генераторов в единый вид.

load_latency(run_dir) -> DataFrame со столбцами:
  ctr        номер контейнера (генератора)
  t_planned  запланированный момент запроса, CLOCK_MONOTONIC, с
  t_done     момент получения ответа, CLOCK_MONOTONIC, с (NaN — ответа нет)
  latency    задержка от запланированного момента, с (Р21; NaN — ответа нет)
  server     время сервера t_done − t_sent, с (только loadgen; у pgbench NaN)
  ok         1 — успешный ответ

Источники:
  lat-c<i>.csv               loadgen: t_planned_ns, t_sent_ns, t_done_ns, status, latency_ns;
  pgbench-c<i>.<pid>         pgbench -l: client_id transaction_no time script_no
                             time_epoch time_us [schedule_lag]. По исходнику
                             REL_17 `time` = now − txn_scheduled (уже от
                             расписания, Р21), мкс. Момент окончания —
                             time_epoch + time_us·1e-6 по «настенной» шкале;
                             переводится на CLOCK_MONOTONIC вычитанием
                             realtime_minus_mono из params.json прогона.
"""
import glob
import gzip
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd


def _params(run_dir: Path) -> dict:
    return json.loads((run_dir / "params.json").read_text())["run"]


def load_latency(run_dir) -> pd.DataFrame:
    run_dir = Path(run_dir)
    frames = []
    for f in sorted(run_dir.glob("lat-c*.csv*")):          # .csv или .csv.gz
        i = int(re.search(r"lat-c(\d+)", f.name).group(1))
        d = pd.read_csv(f)
        ok = (d.latency_ns >= 0) & (d.status >= 200) & (d.status < 400)
        frames.append(pd.DataFrame({
            "ctr": i,
            "t_planned": d.t_planned_ns / 1e9,
            "t_done": np.where(d.t_done_ns > 0, d.t_done_ns / 1e9, np.nan),
            "latency": np.where(ok, d.latency_ns / 1e9, np.nan),
            "server": np.where(ok, (d.t_done_ns - d.t_sent_ns) / 1e9, np.nan),
            "ok": ok.astype(int)}))
    pg = sorted(f for f in glob.glob(str(run_dir / "pgbench-c*.*")) if re.search(r"pgbench-c\d+\.\d+(\.gz)?$", f))
    if pg:
        off = float(_params(run_dir)["realtime_minus_mono"])
        for f in pg:
            i = int(re.search(r"pgbench-c(\d+)\.", f).group(1))
            rows = []
            opener = gzip.open if f.endswith(".gz") else open
            for line in opener(f, "rt"):
                p = line.split()
                if len(p) < 6 or p[2] == "skipped":
                    continue
                lat = int(p[2]) / 1e6
                t_done = int(p[4]) + int(p[5]) / 1e6 - off
                rows.append((t_done - lat, t_done, lat))
            a = np.array(rows) if rows else np.zeros((0, 3))
            frames.append(pd.DataFrame({"ctr": i, "t_planned": a[:, 0], "t_done": a[:, 1],
                                        "latency": a[:, 2], "server": np.nan, "ok": 1}))
    if not frames:
        return pd.DataFrame(columns=["ctr", "t_planned", "t_done", "latency", "server", "ok"])
    return pd.concat(frames, ignore_index=True).sort_values("t_planned", ignore_index=True)
