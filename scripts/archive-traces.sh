#!/usr/bin/env bash
# =============================================================================
# archive-traces.sh — упаковать полные трассы perf (и журналы запросов) серии в архив вне git и
# записать в git манифест с SHA-256 (решение владельца, docs/decisions.md Р20).
#
#   scripts/archive-traces.sh results/raw/<серия>/<время>
#
# Результат:
#   archive/<серия>-<время>.tar.zst              — архив (каталог archive/ вне git)
#   results/trace-manifests/<серия>-<время>.sha256 — SHA-256 каждой трассы и архива
# Проверка целостности позже:
#   cd <корень репозитория> && sha256sum -c results/trace-manifests/<...>.sha256
# Исходные трассы в results/raw НЕ удаляются.
# =============================================================================
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
dir="${1%/}"
[[ -d "$dir" ]] || { echo "нет каталога $dir" >&2; exit 1; }
name="$(basename "$(dirname "$dir")")-$(basename "$dir")"
# Трассы perf и (с этапа 2) сжатые журналы каждого запроса генераторов,
# с этапа 3 — и журналы фазы прогрева JVM (warmup-lat-c*.csv.gz).
mapfile -t files < <(find "$dir" \( -name '*perf-script.txt.gz' -o -name 'lat-c*.csv.gz' \
                                    -o -name 'warmup-lat-c*.csv.gz' \
                                    -o -regex '.*/pgbench-c[0-9]+\.[0-9]+\.gz' \) | sort)
(( ${#files[@]} )) || { echo "в $dir нет трасс"; exit 0; }
mkdir -p archive results/trace-manifests
# Трассы уже в gzip: сильное сжатие почти ничего не даёт, а -T0 занял бы все ядра
# и мешал идущим сериям. Поэтому один поток, уровень 3, низкий приоритет.
# Готовый архив подменяется атомарно, даже если старый файл связан с локальным хранилищем.
tmp=$(mktemp "archive/.$name.XXXXXX")
trap 'if [[ -e "$tmp" ]]; then unlink "$tmp"; fi' EXIT
tar -cf - "${files[@]}" | nice -n 19 zstd -q -T1 -3 -o "$tmp" -f
mv -f -- "$tmp" "archive/$name.tar.zst"
{
    sha256sum "${files[@]}"
    sha256sum "archive/$name.tar.zst"
} > "results/trace-manifests/$name.sha256"
echo "Архив: archive/$name.tar.zst ($(du -h "archive/$name.tar.zst" | cut -f1)), трасс: ${#files[@]}"
echo "Манифест: results/trace-manifests/$name.sha256"
