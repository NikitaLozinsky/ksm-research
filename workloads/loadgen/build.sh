#!/usr/bin/env bash
# Сборка генератора нагрузки (статически: запускается и на хосте, и в любом netns).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$DIR/bin"
gcc -O2 -Wall -Wextra -static -o "$DIR/bin/loadgen" "$DIR/loadgen.c"
gcc --version | head -1 > "$DIR/bin/BUILD_INFO"
