"""Пути графиков в публикационном дереве."""
COMPARISONS = {
    "stage3-cow-cost", "stage3-load-attribution", "stage3-load-cost",
    "stage3-load-ksm-effect", "stage3-load-p99", "stage3-missed-ticks",
    "stage3-pin-wake", "stage3-timer-jitter", "stage3-tlb",
}


def category(stem):
    if stem in COMPARISONS:
        return "comparison"
    if stem.startswith(("stage0-", "stage1-", "stage2-")):
        return "vm"
    return "native"


def figure_path(directory, stem, extension):
    path = directory / extension / category(stem) / f"{stem}.{extension}"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
