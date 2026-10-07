#!/bin/sh
# SPDX-License-Identifier: MIT
# Explicit component build; no install, service start, TA call or sensor access.
set -eu
project=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
root=$project/device/fingerprint/oem
out=${OUT_DIR:-$project/out/fingerprint-oem}
case ${1:-} in
  clients)
    OUT_DIR="$out/clients" bash "$root/tools/build-userspace.sh" --with-rpmb-provider
    ;;
  native)
    OUT_DIR="$out" bash "$root/tools/build-tod.sh"
    OUT_DIR="$out" bash "$root/tools/build-pam-input.sh"
    OUT_DIR="$out" bash "$root/tools/build-fprintd-oem.sh"
    ;;
  *) printf 'usage: %s clients|native\n' "$0" >&2; exit 2 ;;
esac
