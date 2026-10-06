# FPC1264 OEM fingerprint components

This directory is the internal source layout of the optional
[OEM component](../../README.md). Build and package commands are run from the
component root device/fingerprint/oem, unless using the repository wrapper.
Device status, user steps and acceptance limits are documented in the
[user guide](../../../../../docs/FINGERPRINT.md). A fresh candidate's
actual_biometric_acceptance: pending does not inherit the earlier desktop-r3
physical result.

## Component map

| Path | Responsibility |
| --- | --- |
| External kernel series | Matching SPI module, IRQ and sensor power |
| `src/fingerprint/oem` | OEM FPC/Keymaster/Gatekeeper clients, Linux PAM credential input, authorised enrolment and private runtime |
| `src/third_party/qsee-supplicant` | Listener transport and authenticated UFS RPMB provider |
| `src/third_party/QCBOR` | Gatekeeper CBOR build dependency |
| `src/fingerprint/libfprint-tod` | Single-finger OEM TOD adapter and FpPrint serializer |
| `src/fingerprint/fprintd-oem` | Private fprintd source preparation and durable adaptive update |
| `src/fingerprint/deployment` | Install, late module load, system D-Bus verification, acceptance and recovery |
| `tools` | Component builds and public program packaging |

`/dev/fpc1020` and the DT compatible `fpc,fpc1020` are the existing device ABI.
They do not select the historical host software matcher. The daemon explicitly
uses `FP_DRIVERS_ALLOWLIST=fpc1264_oem`; historical host software-matching prototypes are not included in this component.

## Database boundary

The TA imports and exports an opaque raw OEM database. The file under
`/var/lib/fprint/<user>/fpc1264_oem/liuqin-fpc1264-oem/<finger-hex>` is a
**serialized FpPrint**. Its private `fpi-data` is a version-1 `(uay)` holding the
raw database. TOD deserializes the print through libfprint, extracts that byte
array, and invokes `oem_runtime.py --match-single` with a private staging file.
Do not feed the fprintd store file directly to the raw database client.
`fpc-oem-print --serialize` performs the reverse conversion after accepted enrolment.

A match requires the existing single template, a fresh operation ID set through
FPC 3/1, a real capture and identify result, the returned auth-result envelope,
and completed runtime cleanup. Raw command success or a zero status is not a
biometric match. A miss remains a miss. Cleaned, explicit identify statuses 4/12
request a framework retry; that branch does not authenticate the user.
Successful adaptive updates are committed by the private fprintd storage patch
before it reports success. Cancellation, misses and inconclusive scans do not
publish an updated template.

## Build and package

Run each completed component build once. The clients are cross-compiled in WSL:

```sh
OUT_DIR="$PWD/build/review/clients" bash tools/build-userspace.sh --with-rpmb-provider
```

The following builds run on the target's existing libfprint/TOD/PAM ABI (or an
identical development environment). They compile programs; they do not install,
start fprintd, call the TA or enrol a finger.

```sh
OUT_DIR="$PWD/build/review" bash tools/build-tod.sh
OUT_DIR="$PWD/build/review" bash tools/build-pam-input.sh
FPRINTD_SOURCE=/path/to/fprintd-v1.94.5 OUT_DIR="$PWD/build/review" bash tools/build-fprintd-oem.sh
```

For packaging, use a **copy** of the public firmware/program stage and gather
`clients/`, the two TOD outputs, `pam-input`, `fprintd`, and the original vendor
source archive under one `--build` directory. The packager verifies the input
stage's public file hashes, refreshes all program/source copies, and produces a
new manifest and local tar. It does not install the package or update the
current deployed pointer automatically.

```sh
python3 tools/package_fingerprint.py --stage work/review-stage \
  --build build/review --output work/review-candidate.tar.gz \
  --kernel-module /path/to/matching/fpc1264_spi_diag.ko \
  --candidate-config /path/to/public-config.json \
  --firmware /path/to/matching-firmware --revision review-20261002
```

`prepare.py` puts its generated fprintd patch beside the isolated build tree,
or at `--patch-output`. A build does not rewrite the canonical source patch.
`tools/build-userspace.sh` includes `acceptance_input.py`; packaging refreshes
both `pam-input` and `fpc-oem-print` from the build rather than silently reusing
old helpers. Both QSEE source copies are refreshed from the compilation input;
packaging does not preserve an older transport source beside a new binary.
Firmware, template state and per-device secrets are not PR inputs.

## Device acceptance and recovery

Existing accepted exports and published prints are reused. The full acceptance
entry verifies same finger, restarts the daemon and verifies the reloaded print,
then rejects another finger. Only that complete sequence writes `acceptance.json`
and enables the normal D-Bus daemon. A one-shot `--verify` returns one result and
does not write the full acceptance record; save its output when diagnosing it.
Normal D-Bus idle exit is not a daemon failure. The password login path stays
available. `--recover` disables fingerprint authentication while retaining data.

On the recorded desktop-r3 boot, physical same/reload/other-finger checks passed
and the user reported real GNOME unlock success. Contact-position sensitivity
and unlock stutter remain; no measured success-rate or latency claim is made.
The specific 4/12 retry path, suspend/resume, cold boot and long-duration operation
still need physical evidence. Program build/packaging checks do not establish
those behaviours. The support kernel remains temporary `fastboot boot`, with
sensor voltage 2960000–3008000 microvolts and no GENI unbind/bind cleanup.
