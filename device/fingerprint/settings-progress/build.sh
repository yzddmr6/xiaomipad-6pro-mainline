#!/bin/sh
# Build the narrow Settings progress extension.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cc=${CC:-cc}
"$cc" -shared -fPIC -O2 -std=c11 -D_GNU_SOURCE -Wall -Wextra \
    $(pkg-config --cflags gio-2.0) \
    "$root/settings-progress.c" \
    -o "$root/settings-progress.so" \
    $(pkg-config --libs gio-2.0) -ldl
printf 'Built %s\n' "$root/settings-progress.so"
