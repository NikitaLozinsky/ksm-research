#!/usr/bin/env python3
"""Обновить навигацию по ВМ, нативным данным и сопоставлению сред."""
from collections import defaultdict
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
TITLES = {
    "vm": "Результаты VirtualBox",
    "native": "Результаты нативного Arch",
    "comparison": "Сравнение VirtualBox и нативного Arch",
}
REPORTS = {
    "vm": "[Этап 0](../../docs/stage-0.md) · [Этап 1](../../docs/stage-1.md) · [Этап 2](../../docs/stage-2.md)",
    "native": "[Этап 3](../../docs/stage-3.md) · [Этап 4](../../docs/stage-4.md)",
    "comparison": "[Сравнительный отчёт](../../docs/stage-3.md#итоги-2026-11-19)",
}


def table_category(name):
    if "vm-vs-native" in name:
        return "comparison"
    if name.startswith(("stage0-", "stage1-", "stage2-")):
        return "vm"
    return "native"


def raw_category(chunk):
    # Первый нативный прогон снят в ноябре. Имена серий 0–2 переиспользовались.
    return "vm" if chunk.name < "20261101" else "native"


def link(target, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() and path.resolve() == target.resolve():
        return
    path.symlink_to(os.path.relpath(target, path.parent), target_is_directory=target.is_dir())


def main():
    summaries = defaultdict(list)
    for csv in sorted((RESULTS / "summary").glob("*.csv")):
        key = table_category(csv.name)
        directory = RESULTS / key / "summary"
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(csv, directory / csv.name)
        summaries[key].append(csv.name)

    series = defaultdict(lambda: defaultdict(list))
    for chunk in sorted((RESULTS / "raw").glob("*/*")):
        if chunk.is_dir() and (chunk.name[0:8].isdigit()) and "-" in chunk.name:
            key = raw_category(chunk)
            series[key][chunk.parent.name].append(chunk.name)
            link(chunk, RESULTS / key / "raw" / chunk.parent.name / chunk.name)

    for archive in sorted((ROOT / "archive").glob("*.tar.zst")):
        stamp = archive.name.removesuffix(".tar.zst").rsplit("-", 2)[-2]
        if len(stamp) == 8 and stamp.isdigit():
            key = "vm" if stamp < "20261101" else "native"
            link(archive, RESULTS / key / "trace-archives" / archive.name)

    for key, title in TITLES.items():
        text = [
            f"# {title}", "",
            f'{REPORTS[key]} · [Графики PNG](../../figures/png/{key}/) · [Графики SVG](../../figures/svg/{key}/)',
            "",
            f"## Сводки CSV ({len(summaries[key])})", "",
        ]
        text.extend(f"- [{name}](summary/{name})" for name in summaries[key])
        if series[key]:
            text += ["", "## Сырые серии", "",
                     "Локальные ссылки на эти же прогоны лежат в `raw/` рядом со сводками. "
                     "Полные архивы, если они доступны локально, открываются через `trace-archives/`.", "",
                     "| Серия | Каталоги прогонов |", "|---|---|"]
            for name, chunks in series[key].items():
                links = " · ".join(f"[{chunk}](../raw/{name}/{chunk}/)" for chunk in chunks)
                text.append(f"| {name} | {links} |")
        else:
            text += ["", "Исходные прогоны находятся в каталогах [ВМ](../vm/) и [нативной системы](../native/)."]
        (RESULTS / key / "README.md").write_text("\n".join(text) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
