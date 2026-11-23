#!/usr/bin/env bash
# Сборка тестовых программ KSM. Бинарники кладутся в workloads/ksm-probe/bin/
# (каталог не хранится в git: он воспроизводится этим скриптом).
# Статическая сборка (-static) нужна, чтобы бинарник можно было запустить
# внутри любого контейнера, не заботясь о библиотеках.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$DIR/bin"
for prog in ksm_probe ksm_launch; do
    gcc -O2 -Wall -Wextra -static -o "$DIR/bin/$prog" "$DIR/$prog.c"
done
gcc --version | head -1 > "$DIR/bin/BUILD_INFO"
echo "Собрано: $(ls "$DIR/bin")"
