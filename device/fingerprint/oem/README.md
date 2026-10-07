# FPC1264 OEM fingerprint support

User guide: [English](../../../docs/FINGERPRINT.md) · [简体中文](../../../docs/FINGERPRINT.zh-CN.md).

Optional Xiaomi Pad 6 Pro (liuqin, SM8475) support using OEM FPC/Keymaster
trusted applications and real Gatekeeper/RPMB authorization. Includes clients,
PAM credential input, single-finger enrolment/persistence, libfprint TOD,
the fprintd adaptation patch, deployment and physical acceptance.

This component is separate from the kernel series and the default image build.
The product kernel lock stays unchanged. Matching OEM firmware, FPC module,
boot binaries and private device data are external inputs.

## Build

The concentrated source layout is retained under this component. SOURCE_LOCK.json
records QSEE supplicant/QCBOR base revisions and pinned Ubuntu source archives.
FPRINTD_SOURCE_HASHES.json verifies the supplied external fprintd source.
Vendor directories are source subsets; use these component scripts.

Compile static ARM64 clients from the repository root:

~~~sh
sh tools/build-liuqin-fingerprint-oem.sh clients
~~~

Build native components on ARM64 with libfprint/TOD 1.95.1+tod1, Gio, PAM/OpenSSL,
Polkit/systemd, Meson/Ninja development packages. Extract the hash-verified
fprintd original source from SOURCE_LOCK.json:

~~~sh
FPRINTD_SOURCE=/path/to/fprintd-v1.94.5 sh tools/build-liuqin-fingerprint-oem.sh native
~~~

These commands compile only. Output defaults to out/fingerprint-oem; OUT_DIR
selects an isolated output. No TA, credential or sensor operation occurs.

For a host cross-build, the TOD/PAM scripts accept a target compiler through
`CC` and target pkg-config paths. The fprintd script additionally accepts
`MESON_CROSS_FILE`. Link against the installed target libraries and matching
TOD headers; a successful build does not establish hardware acceptance.

## Explicit candidate and installation

Copy candidate-config.example.json outside source and set the actual username,
UID, exact support-kernel release, source identity and public boot hashes.
An explicitly prepared development candidate may set `control_service` to
the verified diagnostic systemd service instead of the default `ssh` service.
The default `deployment_mode` is `development` and retains the diagnostic,
network, GDM and rotation readiness checks. A persistently installed matching
kernel may use `deployment_mode: "installed"`: it waits for GDM and rotation,
but does not depend on networking or a development control service. GDM service
readiness does not require a user to log in. Both modes retain the exact kernel
release, module hash, TEE, device-tree and supply checks. This mode alone does
not install a kernel or establish cold-boot fingerprint acceptance.
The example describes the accepted stable #2 temporary support boot; it is not
a product image. Use the module built for that kernel and matching external
fpcliu.mdt / segment files. No credential or biometric file belongs in firmware.

~~~sh
python3 device/fingerprint/oem/tools/package_fingerprint.py --stage out/fingerprint-stage --build out/fingerprint-oem --kernel-module /path/to/matching/fpc1264_spi_diag.ko --candidate-config /path/to/public-config.json --firmware /path/to/matching-firmware --revision your-revision --output out/liuqin-fpc-oem.tar.gz
~~~

The stage must be empty or match its public manifest. Complete build inputs,
source provenance and the exact module hash are required. Fresh physical
acceptance remains pending.

Extract into a root-owned directory and run install.py as root. It installs
/usr/local/lib/liuqin-fpc-oem, preserves password login and masks normal fprintd
until acceptance. It never writes boot partitions. Use temporary fastboot boot
of the previously accepted support image, then the installed “指纹最终验收”
launcher or sudo /usr/local/lib/liuqin-fpc-oem/acceptance.py.

Existing accepted templates are reused. Acceptance requires same-finger match,
daemon restart/persisted-template reload match, and unenrolled-finger rejection
before enabling the desktop daemon. The late module loader checks exact release,
module hash, FPC DT, 2960000-3008000 microvolt supply and ready desktop/network/
rotation services. GENI is never rebound. The confined daemon explicitly selects
the OEM TOD adapter and excludes the unrelated software matcher.

Linux password changes use the separate normal PAM-authorized
user_credentials.py --sync-password USER entry. Cancellation/failure preserves
the old credential and templates. --recover disables fingerprint authentication
without deleting data or replacing password login. RPMB uses real authenticated
UFS transactions, not emulation.

## Validation and limits

Recorded desktop-r3 device checks passed authorized enrolment, persistent match,
reload match, unenrolled-finger rejection and user-confirmed GNOME unlock/rotation.
Later packaging changes compiled/packaged locally but were not deployed.
The current repository-layout and metadata adaptation is a new candidate and
does not inherit physical acceptance for every build.

Placement sensitivity and unlock stutter remain; success rate and latency are
unmeasured. Standard GNOME Enroll cannot supply authorization and is unsupported.
Scope is one fingerprint per user. Specific identify 4/12 retry, cold boot,
suspend/resume and long-term reliability are unaccepted. The support kernel
remains temporary, complete submitted kernel DTB is unaccepted, and historical
GENI memory corruption is undiagnosed.

CI compiles clients/native components and checks public source syntax. It cannot
validate firmware, hardware authorization, fingerprints or a complete image.

## Licenses

Original scripts/build integration follow project MIT. File-level licenses take
precedence: OEM C/PAM BSD-2-Clause, TOD/FpPrint LGPL-2.1-or-later, fprintd adaptation
GPL-2.0-or-later, AOSP definitions Apache-2.0, and vendor BSD notices retained.
OEM firmware and private credentials/HAT/templates/raw images/calibration are
not public source inputs.
