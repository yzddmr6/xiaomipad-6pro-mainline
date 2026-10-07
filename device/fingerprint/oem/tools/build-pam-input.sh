#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Native target build: PAM and OpenSSL are the target's existing libraries.
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/.." && pwd)
out=${OUT_DIR:-"$root/build/pam-input"}
cc=${CC:-cc}
mkdir -p "$out/pam.d"
"$cc" -O2 -std=c11 -D_GNU_SOURCE -Wall -Wextra -Werror \
    "$root/src/fingerprint/oem/pam-input.c" -o "$out/pam-input" -lpam -lcrypto
cp "$root/src/fingerprint/oem/pam.d/liuqin-fpc-enrol" "$out/pam.d/"
(cd "$out" && sha256sum pam-input pam.d/liuqin-fpc-enrol > SHA256SUMS)
