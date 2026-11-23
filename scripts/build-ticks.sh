#!/usr/bin/env bash
# Собирает общий образ движков. Журнал загрузки и pacman остаётся в results/logs.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
source "$REPO/scripts/lib/tick-progress.sh"
progress_init ticks-build 2
trap 'rc=$?; if (( rc )); then progress -2 "Ошибка · results/logs/ticks-build.log"; fi' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
exec > "$REPO/results/logs/ticks-build.log" 2>&1
progress 0 "Образ Arch Linux"
podman pull docker.io/archlinux/archlinux:base
completed=1
progress 0 "Установка Luanti и Godot в образ"
podman build --pull=never -t localhost/ksm-ticks:latest "$REPO/workloads/ticksrv"
completed=2
progress -1 "Образ готов"
