#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Build against the exact target's installed TOD development headers.
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/.." && pwd)
out=${OUT_DIR:-"$root/build/tod"}
cc=${CC:-cc}
mkdir -p "$out"
"$cc" -shared -fPIC -O2 -std=c11 -D_GNU_SOURCE -Wall -Wextra -Werror \
    $(pkg-config --cflags libfprint-2-tod-1 gio-2.0) \
    "$root/src/fingerprint/libfprint-tod/fpc1264-oem.c" \
    -o "$out/libfprint-tod-fpc1264-oem.so" \
    $(pkg-config --libs libfprint-2-tod-1 gio-2.0)
"$cc" -O2 -std=c11 -D_GNU_SOURCE -Wall -Wextra -Werror \
    $(pkg-config --cflags libfprint-2 libfprint-2-tod-1) \
    "$root/src/fingerprint/libfprint-tod/fpc-oem-print.c" \
    -o "$out/fpc-oem-print" \
    $(pkg-config --libs libfprint-2 libfprint-2-tod-1)
(cd "$out" && sha256sum libfprint-tod-fpc1264-oem.so fpc-oem-print > SHA256SUMS)
