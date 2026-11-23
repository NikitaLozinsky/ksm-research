#!/usr/bin/env bash
# =============================================================================
# build-workloads.sh — скачать базовые образы и собрать образы нагрузок
# этапа 1 (нужна сеть). Список образов и их digest пишется в
# env/<тип системы>/images.txt, чтобы прогоны можно было повторить
# с теми же образами.
# =============================================================================
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
VIRT="$(systemd-detect-virt 2>/dev/null || true)"
VIRT="${VIRT:-none}"
[[ "$VIRT" == none ]] && ENVDIR="$REPO/env/native" || ENVDIR="$REPO/env/vm-$VIRT"
mkdir -p "$ENVDIR"

for img in docker.io/library/nginx:1.27-alpine docker.io/library/postgres:17-alpine \
           docker.io/library/alpine:3.22; do
    podman pull -q "$img" > /dev/null
done
for app in python node jvm; do
    podman build -q -t "localhost/ksm-$app:latest" "$REPO/workloads/apps/$app" > /dev/null
done
{
    echo "# образы нагрузок, снято $(date -Is)"
    for img in docker.io/library/nginx:1.27-alpine docker.io/library/postgres:17-alpine \
               docker.io/library/alpine:3.22 localhost/ksm-python:latest \
               localhost/ksm-node:latest localhost/ksm-jvm:latest; do
        echo "$img $(podman image inspect --format '{{.Digest}} {{.Size}}' "$img")"
    done
} > "$ENVDIR/images.txt"
cat "$ENVDIR/images.txt"
