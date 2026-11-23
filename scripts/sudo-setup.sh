#!/usr/bin/env bash
# =============================================================================
# sudo-setup.sh — временно разрешить sudo без пароля на время серий этапа 3.
#
# Зачем: серии пишут в /sys/kernel/mm/ksm и /proc/sys/vm через `sudo -n` из
# tmux. Если sudo спросит пароль, серия зависнет. Скрипты вызывают
# `sudo bash` и `sudo python`, поэтому сузить правило до отдельных команд
# бессмысленно: это всё равно root. Поэтому правило простое, но ВРЕМЕННОЕ.
#
#   sudo scripts/sudo-setup.sh install   # создать /etc/sudoers.d/ksm-research
#   sudo scripts/sudo-setup.sh remove    # удалить (сделать после этапа!)
#        scripts/sudo-setup.sh status    # есть ли правило (без sudo)
#
# Безопасность: файл сначала пишется во временный, проверяется `visudo -cf`
# и только потом ставится с правами 0440 root:root. Ошибка в синтаксисе
# sudoers ломает sudo целиком, поэтому без проверки файл не ставится.
# =============================================================================
set -euo pipefail

FILE=/etc/sudoers.d/ksm-research
# Пользователь, для которого правило: тот, кто вызвал sudo.
USER_NAME="${SUDO_USER:-$(id -un)}"

case "${1:-status}" in
install)
    [[ $EUID -eq 0 ]] || { echo "Нужно: sudo $0 install"; exit 1; }
    [[ "$USER_NAME" != root ]] || { echo "Запускайте через sudo от своего пользователя"; exit 1; }
    tmp=$(mktemp)
    trap 'rm -f "$tmp"' EXIT
    cat > "$tmp" <<EOF
# Временно для серий ksm-research (этап 3). Удалить: sudo $(realpath "$0") remove
$USER_NAME ALL=(root) NOPASSWD: ALL
EOF
    visudo -cf "$tmp" > /dev/null || { echo "FAIL: visudo отверг файл, ничего не изменено"; exit 1; }
    install -m 0440 -o root -g root "$tmp" "$FILE"
    visudo -c > /dev/null && echo "OK: $FILE установлен, sudo без пароля для $USER_NAME"
    ;;
remove)
    [[ $EUID -eq 0 ]] || { echo "Нужно: sudo $0 remove"; exit 1; }
    rm -f "$FILE" && echo "OK: $FILE удалён, sudo снова спрашивает пароль"
    ;;
status)
    # /etc/sudoers.d без root не читается — проверяем через sudo -n.
    if sudo -n test -e "$FILE" 2>/dev/null; then echo "правило есть: $FILE"; else echo "правила нет (или sudo спрашивает пароль)"; fi
    sudo -n true 2>/dev/null && echo "sudo -n: работает без пароля" || echo "sudo -n: требует пароль"
    ;;
*)
    echo "Использование: $0 install|remove|status"; exit 1 ;;
esac
