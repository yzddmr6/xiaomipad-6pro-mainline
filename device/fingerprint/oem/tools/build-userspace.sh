#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Compile the collected clients. No device connection or credential operation.
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/.." && pwd)
out=${OUT_DIR:-"$root/build/userspace"}
cc=${CC:-aarch64-linux-gnu-gcc}
client="$root/src/fingerprint/oem"
vendor="$root/src/third_party/qsee-supplicant"
qcbor="$root/src/third_party/QCBOR"
mkdir -p "$out"
flags=(-static -O2 -std=c11 -D_GNU_SOURCE -Wall -Wextra -Werror)

"$cc" "${flags[@]}" -o "$out/fpc_build_info" "$client/fpc_build_info.c"
"$cc" "${flags[@]}" -I"$qcbor/inc" -o "$out/qsee-gatekeeper" \
    "$client/qsee_gatekeeper.c" "$qcbor/src/qcbor_encode.c" \
    "$qcbor/src/qcbor_decode.c" "$qcbor/src/UsefulBuf.c" "$qcbor/src/ieee754.c" -lm
common=("$vendor/src/path.c" "$vendor/src/services.c" "$vendor/src/handle_db.c" \
        "$vendor/src/fs.c" "$vendor/src/gpfs.c")
"$cc" "${flags[@]}" -I"$vendor/include" -pthread -o "$out/qsee-supplicant" \
    "$vendor/src/main.c" "$vendor/src/transport_qseecom.c" "${common[@]}" "$vendor/src/notify.c"
"$cc" "${flags[@]}" -I"$vendor/include" -o "$out/qsee-app-loader" \
    "$vendor/src/app_loader.c" "$vendor/src/app_acquire.c" "$vendor/src/notify.c"
if [[ ${1:-} == --with-rpmb-provider ]]; then
    mkdir -p "$out/ufs-rpmb-provider"
    "$cc" "${flags[@]}" -I"$vendor/include" -pthread \
        -o "$out/ufs-rpmb-provider/liuqin-rpmb-supplicant" \
        "$vendor/src/liuqin_rpmb_main.c" "$vendor/src/rpmb.c" "$vendor/src/rpmb_ufs.c" \
        "$vendor/src/transport_qseecom.c" "${common[@]}"
elif [[ $# -gt 0 ]]; then
    printf 'usage: %s [--with-rpmb-provider]\n' "$0" >&2
    exit 2
fi
cp "$client/acceptance_input.py" "$client/authorize_oem.py" "$client/native_credential_preflight.py" \
   "$client/native_credentials.py" "$client/user_credentials.py" "$client/enrol_publish.py" "$client/oem_runtime.py" "$out/"
hash_files=(fpc_build_info qsee-gatekeeper qsee-supplicant qsee-app-loader
            acceptance_input.py authorize_oem.py native_credential_preflight.py native_credentials.py user_credentials.py enrol_publish.py oem_runtime.py)
if [[ ${1:-} == --with-rpmb-provider ]]; then
    hash_files+=(ufs-rpmb-provider/liuqin-rpmb-supplicant)
fi
(cd "$out" && sha256sum "${hash_files[@]}" > SHA256SUMS)
printf 'Userspace binaries: %s\n' "$out"
