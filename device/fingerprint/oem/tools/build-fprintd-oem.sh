#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Build the private fprintd adaptation candidate on the target's current ABI.
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/.." && pwd)
out=${OUT_DIR:-"$root/build/fprintd-oem"}
vendor=${FPRINTD_SOURCE:?Set FPRINTD_SOURCE to the extracted pinned fprintd-v1.94.5 directory}
python3 - "$root/FPRINTD_SOURCE_HASHES.json" "$vendor" <<'PY'
import hashlib, json, pathlib, sys
for name, digest in json.load(open(sys.argv[1])).items():
    if hashlib.sha256((pathlib.Path(sys.argv[2]) / name).read_bytes()).hexdigest() != digest:
        raise SystemExit("Pinned fprintd source mismatch: " + name)
PY
mkdir -p "$out"
python3 "$root/src/fingerprint/fprintd-oem/prepare.py" \
    "$vendor" "$out/source" \
    --patch-output "$out/fprintd-oem-update.patch"
meson setup "$out/build" "$out/source" --prefix=/usr \
    -Dpam=false -Dman=false -Dgtk_doc=false -Dsystemd=false
meson compile -C "$out/build" fprintd
cp "$out/build/src/fprintd" "$out/fprintd"
tar -czf "$out/fprintd-vendor.tar.gz" \
    -C "$(dirname -- "$vendor")" "$(basename -- "$vendor")"
(cd "$out" && sha256sum fprintd fprintd-vendor.tar.gz > SHA256SUMS)
