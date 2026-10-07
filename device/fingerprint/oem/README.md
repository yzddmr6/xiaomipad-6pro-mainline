# FPC1264 OEM fingerprint support

User guide: [English](../../../docs/FINGERPRINT.md) · [简体中文](../../../docs/FINGERPRINT.zh-CN.md).

This optional liuqin component implements fingerprint enrollment through
**Ubuntu Settings → System → Users → Fingerprint Login**, using the normal
Polkit password dialog. The libfprint TOD driver reports capture progress and
cancellation to that dialog; fprintd stores the resulting fingerprint and
provides the standard GDM login and lock-screen verification interface. It does
not add a separate everyday unlock application or modify GNOME Settings.

The current candidate supports **one configured Linux account and one finger**.
Native enrollment and login/unlock have passed on the test unit as described
below. The default image/kernel lock is not changed by this optional component.

## Current validation boundary

As of 2026-10-07, native Settings enrollment completed on the b87 support kernel.
The saved template was retained through installation of userspace `4d6b22f` and
the combined DP/fingerprint kernel `6.17.0-rc1-gfe81794b5e1b`. Same-finger acceptance,
other-finger rejection and native lock-screen unlock passed on that combination.
After writing the matching boot images and modules, an ordinary reboot also
reached the desktop through `gdm-fingerprint`, without entering the login password
or manually warming Keymaster/TEE. DP output was subsequently confirmed on the
same persistent boot. This is one configured account/finger on the known 256 GB
test unit, not a release or a claim covering other devices and adapters.

The fprintd service uses `UMask=0077`: native enrollment must create root-owned
mode-0600 templates accepted by the adaptive-update writer. Earlier development
templates created as 0644 need their permissions tightened before matching;
their contents and credentials must be preserved. Do not relax the writer's
ownership or mode checks. Storage failures now log only a fixed phase and error
domain/code, instead of silently turning a sensor match into authentication failure.

## User workflow and authorization

After the matching bundle and kernel have been installed:

1. Open **Settings → System → Users → Fingerprint Login** for the configured
   account. If Settings requires unlocking, use its normal system dialog.
2. Select a finger and authorize the enrollment through Polkit. Follow the
   native scan, lift/reposition and progress messages until completion.
3. Use the regular Ubuntu login or lock screen for verification. Password login
   remains available; the dedicated fingerprint PAM helper is not inserted into
   the normal password-login stack.

The active local user must authenticate for each enroll/delete action
(`auth_self`, without retaining that authorization). fprintd checks the real
D-Bus sender and separately authorizes selection of another username. The native
provider additionally restricts operations to the installed account profile.
The public package supports only the first local account, UID 1000, resolving
its name after initial setup. Development bundles retain explicit username/UID
binding. Existing private credential records retain their username/UID binding;
changing profiles does not recreate, rename or migrate an identity. Managing
multiple accounts is outside this component's scope.

The provider uses a separate root-private random credential and Gatekeeper
handle under `/var/lib/liuqin-fingerprint/native-enrollment/`, with native UID
`0x60000000 | linux_uid`. This authorizes the OEM enrollment protocol; it is not a
copy of the Linux password. New native credentials therefore need no separate
synchronization when the Linux password changes. Enrollment uses real
Gatekeeper/RPMB operations and writes private state; a temporary RAM kernel does
not make enrollment a zero-write operation.

Existing password-derived identities in the `0x40000000`/`0x50000000` ranges are
not migrated or overwritten. Existing fprintd templates are also preserved:
another enrollment is refused while a finger is stored for the account. Do not
delete credential files or uncertainty markers to force a retry. Canceling
initial credential creation lets that short transaction finish safely before
stopping; it does not continue into capture. Canceling capture waits for the
existing TEE operation and listeners to clean up.

## Matching kernel and runtime

Use the exact kernel release, FPC DT, module and OEM firmware identified by the
candidate. A working `/dev/fpc1020` alone is insufficient. The late loader checks
the kernel release, module SHA256, TEE node, FPC DT wiring and the validated
2960000–3008000 microvolt supply range. It never rebinds GENI.

Native deployment uses `deployment_mode: "installed"`. Its module service waits
for GDM and rotation services, without depending on SSH, networking or a temporary
diagnostic service. GDM service readiness does not require a user to have logged
in. The default `development` mode retains diagnostic/network readiness checks.
Neither mode installs a boot image or changes the partition table.

A temporary `fastboot boot` only supplies that running kernel. Availability after
an ordinary reboot requires a persistently installed matching kernel/module
combination. Boot/root identity checks must remain intact; changing a release
string or bypassing module hashes is not a supported installation method.

Verification loads the persisted fprintd database through the OEM matcher. It
does not call the old PAM credential helper or consume the old boot-local
18-hour prepared-input key. Verification after an ordinary reboot has passed
without a manual setup step. The verify path calls Keymaster `0x205` directly; unlike the
Gatekeeper enrollment client, it does not send the `0x200`/`0x207` negotiation
sequence. The observed reboot test does not justify adding speculative
initialization or claiming every power-loss/recovery scenario has passed.

## Build and package

The release image uses the `liuqin-fingerprint` Debian package. Download
`fingerprint-build-inputs.tar.gz` from the **same v0.6.0 release** (also included
in its complete release bundle), then build without accessing a device or host
services. This public archive contains the verified programs, corresponding
source/dependency materials and fixed TA hashes, with no OEM TA bytes or user
state; its SHA256 is pinned by the builder.

~~~sh
python3 tools/build-liuqin-fingerprint-deb.py --bundle /path/to/fingerprint-build-inputs.tar.gz --out out/fingerprint-debs --version 0.6.0
~~~

`BUILD_INPUTS.json` records the validated userspace's `4d6b22f` revision and
original input identity. Maintainers can reproduce the public archive from the
audited internal r4 host bundle using the fixed-metadata transformation below;
consumers need only the downloadable public archive, not that private input.

~~~sh
python3 tools/make-fingerprint-build-inputs.py --bundle /path/to/audited-r4/fingerprint.tar.gz --output out/fingerprint-build-inputs.tar.gz
~~~

This preserves the verified native binaries and exact FE module, overlays the
current Python integration, and stages the standard fprintd service, enrollment
Polkit rule and `UMask=0077`. It needs no existing user during rootfs assembly,
creates no credential/template, and installs no separate enrollment app. The
package pins the verified libfprint/TOD package ABI and matching kernel package.
The default first local UID 1000 account uses Ubuntu Settings after first boot.
The first-user profile and local firmware extraction still require final image
verification; the preceding device results describe the bound development bundle.

The public Debian package **does not distribute OEM trusted applications**.
It includes [ten fixed hashes](firmware/SHA256.json) and their
[stock provenance](firmware/NOTICE). Before late module loading, it reuses a
verified local cache or mounts this tablet's `modem_a` FAT read-only and copies
only those ten files. This occurs on normal boot, after the installer repairs
the stock modem image. A missing or mismatched image fails fingerprint startup
without a repair/retry loop, raw writes, or blocking password login. No firmware
extraction or credential creation runs during Debian/rootfs installation.

### Internal source builds and bound development bundles

[SOURCE_LOCK.json](SOURCE_LOCK.json) records source revisions and pinned Ubuntu
archives. [FPRINTD_SOURCE_HASHES.json](FPRINTD_SOURCE_HASHES.json) verifies the
external fprintd source. Compile static ARM64 clients from the repository root:

~~~sh
sh tools/build-liuqin-fingerprint-oem.sh clients
~~~

Build the native components with the target libfprint/TOD 1.95.1+tod1, Gio,
PAM/OpenSSL, Polkit/systemd and Meson/Ninja development inputs:

~~~sh
FPRINTD_SOURCE=/path/to/fprintd-v1.94.5 sh tools/build-liuqin-fingerprint-oem.sh native
~~~

`OUT_DIR` selects an isolated output (default `out/fingerprint-oem`). For a host
cross-build, the TOD/PAM scripts accept `CC` and target pkg-config paths; the
fprintd script also accepts `MESON_CROSS_FILE`. These commands do not connect to a
device or create credentials. Link against the target libraries and matching
TOD headers; compilation is not hardware acceptance.

Copy `candidate-config.example.json` outside source, replace its historical
stable-#2 kernel/boot identifiers with the actual inputs, and set the real
username and UID. For the native Settings interface, explicitly add:

~~~json
{
  "enrollment_interface": "fprintd",
  "deployment_mode": "installed"
}
~~~

The historical example by itself selects the development helper interface.
Use the newly built native-capable TOD, `native_enrol.py`, the enrollment Polkit
rule, the exact matching FPC module and external `fpcliu.mdt`/segment files.
Credential handles, random secrets, templates and calibration are never generic
firmware or public package inputs.

~~~sh
python3 device/fingerprint/oem/tools/package_fingerprint.py --stage out/fingerprint-stage --build out/fingerprint-oem --kernel-module /path/to/matching/fpc1264_spi_diag.ko --candidate-config /path/to/public-config.json --firmware /path/to/matching-firmware --revision your-revision --output out/liuqin-fpc-oem.tar.gz
~~~

The stage must be empty or match its manifest. Use a new output archive path;
existing archives are not overwritten. Extract into a root-owned directory and
run `install.py` as root. Native installation checks the live matching kernel,
installs the standard fprintd/Polkit integration, removes unchanged legacy app
launchers and their password-free authorization rule, then starts canonical
fprintd. It preserves password login and existing credential/template files.
`--replace-owned-candidate` verifies and backs up an existing owned program
bundle before replacement; it does not migrate biometric state.

When staging a different support kernel, use `install.py --defer-start` to keep
fprintd masked. After booting the matching kernel, rerun the installer without
`--defer-start` to check it and activate fprintd. Starting fprintd exposes the interface; it is not proof that enrollment or
unlocking works.

## Internal diagnostics and remaining limits

`acceptance.py`, `user_credentials.py` and the terminal/PAM launcher are retained
for the legacy development-helper workflow. They are not the normal native
Settings entry, and native installation removes their application-menu entry.
The legacy `--sync-password` command applies only to the old password-derived
identity scheme. Do not invoke legacy create, enrollment or random probe-UID
operations as native preflight checks.

For native failures, inspect the bounded status/phase diagnostics in
`journalctl -u fprintd.service` and the module service status. Never collect or
publish credential input, handles, authentication tokens, random secrets,
templates, raw captures or private calibration. To disable fingerprint
verification while preserving its state and password login, an administrator
can mask and stop `fprintd.service`; no credential reset is needed.

Current-device completion, persistence, same/other-finger behavior and native
login/unlock passed within the scope above. Placement sensitivity, latency,
identify-status 4/12 retry frequency, suspend/resume, long-term reliability and dual-boot
secure-storage coexistence are not established for this candidate. The current
support DTB has booted and the FPC module loaded without observed GENI faults in
that window; this does not resolve the contributor's historical GENI corruption
or prove repeated cold-boot stability.

Offline API and packaging tests cover protocol/ABI behavior. They cannot prove
real matching, hardware authorization, user-visible unlock or a complete image.

## Licenses

Original scripts/build integration follow project MIT. File-level licenses take
precedence: OEM C/PAM BSD-2-Clause, TOD/FpPrint LGPL-2.1-or-later, fprintd adaptation
GPL-2.0-or-later, AOSP definitions Apache-2.0, and vendor BSD notices retained.
OEM firmware and private credentials/HAT/templates/raw images/calibration are
not public source inputs.
