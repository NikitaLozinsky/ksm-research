#!/usr/bin/env python3
"""Проверить полноту этапа 4, собрать сводки и графики, архивировать трассы."""
import json
import runpy
import subprocess
from datetime import datetime
from pathlib import Path

from lib.figure_paths import figure_path
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "results/raw"
SUMMARY = ROOT / "results/summary"
FIGURES = ROOT / "figures"
MODES = {"on", "off"}
WORKLOADS = {"ticksrv", "luanti", "godot"}
PINS = {"shared", "sibling", "split", "unpinned"}
METRICS = ["work_p50_ms", "work_p99_ms", "work_p999_ms", "period_p50_ms",
           "low_1pct_tps", "missed_frac", "jitter_std_ms", "jitter_iqr_ms"]
THERMAL = ["temp_launch_c", "temp_max_c", "freq_median_max_mhz", "throttle_pkg_delta"]


def select_runs(block, repetitions):
    """Полный повтор приходит из одного каталога, включая обе стороны пары."""
    expected = (WORKLOADS if block == "b" else {"ticksrv"})
    variants = MODES if block == "b" else PINS
    chosen = {}
    for chunk in sorted((RAW / f"stage4-ticks-block-{block}").glob("*")):
        runs = {}
        for marker in chunk.glob("*/complete"):
            base = marker.parent
            p = json.loads((base / "params.json").read_text())["run"]
            key = (p["workload"], p["mode"] if block == "b" else p["pin"])
            rep = p["rep"]
            if key in runs.setdefault(rep, {}):
                raise ValueError(f"{chunk}: дублируется {rep}/{key}")
            runs[rep][key] = base
        for rep, group in runs.items():
            if set(group) != {(w, mode) for w in expected for mode in variants}:
                continue
            if rep in chosen:
                raise ValueError(f"Блок {block}: два полных повтора с номером {rep}")
            chosen[rep] = group.values()
    if set(chosen) != set(range(1, repetitions + 1)):
        raise ValueError(f"Блок {block}: полные повторы {sorted(chosen)}, ожидалось {repetitions}")
    return [base for rep in sorted(chosen) for base in sorted(chosen[rep])]


def telemetry(base, hw):
    boundary = pd.read_csv(base / "clocks.csv").set_index("phase").mono_ns
    window = hw[hw.t_mono_ns.between(boundary.pre, boundary.end)]
    launch = hw[hw.t_mono_ns.between(boundary.launch, boundary.pre)]
    # hw_sampler опрашивает датчики с интервалом 1 с. Проверяем покрытие окна.
    if len(window) < 2 or len(launch) < 1:
        raise ValueError(f"{base}: отсутствует телеметрия")
    max_gap = np.diff(np.r_[boundary.pre, window.t_mono_ns, boundary.end]).max() / 1e9
    stable = (max_gap <= 3 and window.ac_online.eq(1).all()
              and window.platform_profile.eq("performance").all())
    freq = window.filter(regex=r"^freq_mhz_cpu[0-9]+$")
    return {
        "temp_launch_c": launch.pkg_temp_c.iloc[0], "temp_max_c": window.pkg_temp_c.max(),
        "freq_median_max_mhz": freq.max(axis=1).median(),
        "throttle_pkg_delta": window.pkg_throttle_count.max() - window.pkg_throttle_count.min(),
        "ac_off_samples": int(window.ac_online.eq(0).sum()),
        "hw_max_gap_s": max_gap, "power_ok": stable,
    }


def collect(bases, block):
    output, attributes = [], []
    for chunk in sorted({base.parent for base in bases}):
        local = [base for base in bases if base.parent == chunk]
        prefix = SUMMARY / f"{chunk.parent.name}-{chunk.name}"
        run_file, attr_file = Path(f"{prefix}-per-run.csv"), Path(f"{prefix}-attribution.csv")
        if run_file.exists() and attr_file.exists():
            rows = pd.read_csv(run_file)
            attr = pd.read_csv(attr_file)
        else:
            summarize = runpy.run_path(str(ROOT / "scripts/stage4-ticks-summary.py"))["summarize"]
            runs, attrib = zip(*(summarize(base) for base in local))
            rows = pd.DataFrame([row for run in runs for row in run])
            attr = pd.concat([row for run in attrib for row in run], ignore_index=True)
        hw = pd.read_csv(chunk / "hw.csv")
        for base in local:
            selected = rows[rows.run.eq(base.name)]
            events = attr[attr.run.eq(base.name)]
            if set(selected.phase) != {"pre", "merge", "steady"} or len(selected) != 3 or len(events) != 30:
                raise ValueError(f"{base}: неполная сводка")
            steady = selected[selected.phase.eq("steady")].iloc[0]
            if not (steady.ksm_min_pages > 0 if steady["mode"] == "on" else steady.ksm_max_pages == 0):
                raise ValueError(f"{base}: счётчики KSM не подтверждают режим")
            output.append(selected.assign(block=block, chunk=chunk.name, **telemetry(base, hw)))
            attributes.append(events.assign(block=block, chunk=chunk.name))
    return pd.concat(output, ignore_index=True), pd.concat(attributes, ignore_index=True)


def paired(data):
    rows = []
    rng = np.random.default_rng(20261123)
    for (workload, phase), group in data.groupby(["workload", "phase"]):
        on = group[group["mode"].eq("on")].set_index("rep").sort_index()
        off = group[group["mode"].eq("off")].set_index("rep").sort_index()
        clean = (on.power_ok & off.power_ok).to_numpy()
        # Одинаковые подвыборки должны давать одинаковые ДИ.
        draws = {n: rng.integers(0, n, size=(10000, n))
                 for n in sorted({len(on), int(clean.sum())} - {0})}
        for subset, mask in [("all", np.ones(len(on), dtype=bool)), ("power_ok", clean)]:
            for metric in METRICS + THERMAL:
                delta = (on[metric] - off[metric]).to_numpy()[mask]
                if not len(delta):
                    continue
                q1, median, q3 = np.percentile(delta, [25, 50, 75])
                indexes = draws[len(delta)]
                lo, hi = np.percentile(np.median(delta[indexes], axis=1), [2.5, 97.5])
                rows.append(dict(workload=workload, phase=phase, subset=subset, metric=metric,
                                 n=len(delta), median=median, q1=q1, q3=q3, ci95_lo=lo, ci95_hi=hi))
    return pd.DataFrame(rows)


def pin_summary(data):
    long = data.melt(id_vars=["pin", "phase"], value_vars=METRICS, var_name="metric")
    return (long.groupby(["pin", "phase", "metric"]).value
            .agg(n="count", median="median", q1=lambda x: x.quantile(.25), q3=lambda x: x.quantile(.75))
            .reset_index())


def graph(table, names, group, xlabel, filename):
    table = table.set_index(group).loc[names]
    fig, ax = plt.subplots(figsize=(7.2, 3.4), layout="constrained")
    q1, median, q3 = table[["q1", "median", "q3"]].to_numpy().T
    ax.errorbar(median, range(len(table)), xerr=[median - q1, q3 - median],
                fmt="o", capsize=4, color="#204864")
    ax.set(yticks=range(len(table)), yticklabels=names, xlabel=xlabel)
    if group == "workload":
        ax.axvline(0, color="#444444", linestyle="--", linewidth=1)
    ax.invert_yaxis()
    ax.grid(axis="x", alpha=.25)
    fig.savefig(figure_path(FIGURES, filename, "png"), dpi=300)
    fig.savefig(figure_path(FIGURES, filename, "svg"), metadata={"Date": None})
    plt.close(fig)


def archive(chunk):
    relative = chunk.relative_to(ROOT)
    name = f"{chunk.parent.name}-{chunk.name}"
    manifest = f"results/trace-manifests/{name}.sha256"
    archive_path = f"archive/{name}.tar.zst"
    if not (ROOT / manifest).exists() or not (ROOT / archive_path).exists():
        subprocess.run(["bash", "scripts/archive-traces.sh", str(relative)], cwd=ROOT, check=True)
    subprocess.run(["sha256sum", "--quiet", "-c", manifest], cwd=ROOT, check=True)
    subprocess.run(["tar", "--zstd", "--compare", "-f", archive_path], cwd=ROOT, check=True)
    return manifest


def main():
    b_runs = select_runs("b", 15)
    v_runs = select_runs("v", 10)
    b, attr_b = collect(b_runs, "b")
    v, attr_v = collect(v_runs, "v")
    assert len(b) == 270 and len(v) == 120
    b_stats, v_stats = paired(b), pin_summary(v)
    template = "phase == 'steady' and metric == 'work_p999_ms'"
    b_plot = b_stats.query(template + " and subset == 'all'")
    v_plot = v_stats.query(template)
    graph(b_plot, ["ticksrv", "luanti", "godot"], "workload",
          "KSM вкл. − выкл., p99.9 работы тика, мс (медиана и IQR)", "stage4-block-b-p999")
    graph(v_plot, ["shared", "sibling", "split", "unpinned"], "pin",
          "p99.9 работы измеряемого тика, мс (медиана и IQR)", "stage4-block-v-p999")
    frames = {
        "stage4-ticks-final-per-run.csv": pd.concat([b, v], ignore_index=True),
        "stage4-ticks-final-attribution.csv": pd.concat([attr_b, attr_v], ignore_index=True),
        "stage4-ticks-block-b-paired.csv": b_stats,
        "stage4-ticks-block-v-summary.csv": v_stats,
    }
    paths = [f"results/summary/{name}" for name in frames]
    for name, frame in frames.items():
        frame.to_csv(SUMMARY / name, index=False)
    text = [
        "# Этап 4 — итоги серий Б и В", "",
        "Завершены 45 пар серии Б и 40 прогонов блока В. "
        "Сводки блока 0 и пилота приведены в [отчёте этапа](stage-4.md).", "",
        "## Блок Б. Работа тика в устойчивой фазе", "",
        "Разность p99.9 KSM вкл. − выкл. рассчитана внутри каждой пары. "
        "Отрицательная разность означает сокращение задержки.", "",
        "| Нагрузка | Медиана, мс | IQR, мс | 95 % бутстреп-ДИ, мс | Чистых пар по питанию |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in b_plot.itertuples():
        clean = b_stats.query(template + " and subset == 'power_ok'")
        clean = clean.loc[clean.workload.eq(row.workload), "n"]
        text.append(f"| {row.workload} | {row.median:.3f} | [{row.q1:.3f}; {row.q3:.3f}] | "
                    f"[{row.ci95_lo:.3f}; {row.ci95_hi:.3f}] | {int(clean.iloc[0]) if len(clean) else 0} |")
    text += ["", "Проверка остальных метрик устойчивой фазы. В каждой ячейке медиана парной "
             "разности вкл. − выкл. и 95 % бутстреп-ДИ.", "",
             "| Нагрузка | p50 работы, мс | p99 работы, мс | 1 % low, тиков/с | Доля длинных интервалов, п. п. |",
             "|---|---:|---:|---:|---:|"]
    steady = b_stats.query("phase == 'steady' and subset == 'all'").set_index(["workload", "metric"])
    for workload in sorted(WORKLOADS):
        values = []
        for metric in ("work_p50_ms", "work_p99_ms", "low_1pct_tps", "missed_frac"):
            row = steady.loc[workload, metric]
            scale = 100 if metric == "missed_frac" else 1
            median, lo, hi = row[["median", "ci95_lo", "ci95_hi"]] * scale
            values.append(f"{median:.3f} [{lo:.3f}; {hi:.3f}]")
        text.append(f"| {workload} | " + " | ".join(values) + " |")
    text += ["", "## Блок В. Привязка ksmd", "",
             "Устойчивая фаза измеряемого C-сервера, p99.9 работы тика.", "",
             "| Привязка | Повторы | Медиана, мс | IQR, мс |", "|---|---:|---:|---:|"]
    for row in v_plot.itertuples():
        text.append(f"| {row.pin} | {row.n} | {row.median:.3f} | [{row.q1:.3f}; {row.q3:.3f}] |")
    text += ["", "## Тепловые условия", "",
             "| Блок | Нагрузка | Вариант | Прогоны с троттлингом | Макс. температура, °C |",
             "|---|---|---|---:|---:|"]
    for block, frame, variant in [("Б", b, "mode"), ("В", v, "pin")]:
        for (workload, mode), group in frame[frame.phase.eq("steady")].groupby(["workload", variant]):
            n = int(group.throttle_pkg_delta.gt(0).sum())
            text.append(f"| {block} | {workload} | {mode} | {n}/{len(group)} | {group.temp_max_c.max():.0f} |")
    text += ["", "Парные разности температуры на старте, максимальной температуры, частоты и "
             "счётчика троттлинга находятся в сводке серии Б. Тепловые ограничения описаны в Л15 и Р26. "
             "Атрибуция к активности ksmd и COW описывает совпадение по времени.", "",
             "[График Б](../figures/png/native/stage4-block-b-p999.png) · [График В](../figures/png/native/stage4-block-v-p999.png)", "",
             *[f"- [{Path(path).name}](../{path})" for path in paths], ""]
    report = "docs/stage-4-results.md"
    (ROOT / report).write_text("\n".join(text), encoding="utf-8")
    paths.append(report)
    paths += [str(figure_path(FIGURES, f"stage4-block-{block}-p999", ext).relative_to(ROOT))
              for block in ("b", "v") for ext in ("png", "svg")]
    for chunk in sorted({base.parent for base in b_runs + v_runs}):
        paths.append(archive(chunk))
        name = f"{chunk.parent.name}-{chunk.name}"
        for suffix in ("per-run", "attribution"):
            summary_path = SUMMARY / f"{name}-{suffix}.csv"
            if summary_path.exists():
                paths.append(str(summary_path.relative_to(ROOT)))
    journal = "docs/journal.md"
    diff = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", journal], cwd=ROOT)
    if diff.returncode == 0:
        path = ROOT / journal
        title = "Серии Б и В завершены, результаты этапа 4"
        if title not in path.read_text(encoding="utf-8"):
            timestamp = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%Y-%m-%d %H:%M")
            with path.open("a", encoding="utf-8") as file:
                file.write(f"\n### {timestamp} — {title}\n\n"
                           "- Проверены 45 пар Б и 40 прогонов В, манифесты и содержимое архивов. "
                           "[Итоговый отчёт](stage-4-results.md).\n"
                           "- График Б показывает парную разность p99.9 работы тика и IQR по нагрузкам.\n"
                           "- График В показывает p99.9 работы измеряемого C-сервера и IQR по привязкам.\n")
        paths.append(journal)
    paths += ["results/raw/stage4-ticks-block-b", "results/raw/stage4-ticks-block-v"]
    subprocess.run(["git", "add", "--", *paths], cwd=ROOT, check=True)
    subprocess.run(["git", "commit", "--only", "-m", "Этап 4: итоговые результаты серий Б и В",
                    "--", *paths], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
