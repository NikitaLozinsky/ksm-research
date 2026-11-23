# Общий статус для терминала: одна строка, обновляемая атомарно.
progress_init() {
    STATUS="$REPO/results/logs/$1.status"
    mkdir -p "$(dirname "$STATUS")"
    exec 8>"$STATUS.lock"
    flock -n 8 || { echo "Задача уже выполняется: $STATUS" >&2; exit 75; }
    total=$2
    started_at=$(date +%s)
    completed=0
    progress 0 "Подготовка"
}

progress() {
    local eta=$1 now
    shift
    now=$(date +%s)
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$$" "$started_at" "$now" "$completed" "$total" "$eta" "$*" > "$STATUS.tmp"
    mv -f "$STATUS.tmp" "$STATUS"
}
