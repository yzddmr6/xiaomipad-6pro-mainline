# FPC1264 OEM fingerprint support

User guide: [English](../../../docs/FINGERPRINT.md) · [简体中文](../../../docs/FINGERPRINT.zh-CN.md).

This optional liuqin component implements fingerprint enrollment through
**Ubuntu Settings → System → Users → Fingerprint Login**, using the normal
Polkit password dialog. The libfprint TOD driver reports capture progress and
cancellation to that dialog; fprintd stores the resulting fingerprint and
provides the standard GDM login and lock-screen verification interface. It does
not add a separate everyday unlock application or modify GNOME Settings.

The current candidate supports **one configured Linux account and one finger**.
The native interface is implemented, but completed enrollment and unlocking on
the current device remain unverified. The default image/kernel lock is not
changed by this optional component.

## Current validation boundary

As of 2026-10-07, userspace candidate `70a4494` has been deployed with its matching
support kernel. The native GNOME Users page displays “Fingerprint Login”, and a
root-driven fprintd `EnrollStart` followed by cancellation completed with runtime
cleanup. The kernel/TEE/FPC device and required userspace services are available.

These observations do **not** establish normal-user Polkit authorization,
completed fingerprint enrollment, a persisted template, same-finger acceptance,
other-finger rejection, native lock-screen unlock or cold-boot GDM login. No
completed template was present at this checkpoint. Remaining device validation
must cover those operations, including cancellation through the Settings UI.

The contributor's earlier desktop-r3 enrollment/unlock results used another
component combination. They remain historical evidence and are not acceptance
of this native candidate. The former terminal helper also collected samples on
this device without completing a template; sampling alone is not enrollment.

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
provider additionally restricts operations to the username/UID in the installed
candidate. Managing multiple accounts is outside this candidate's scope.

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
18-hour prepared-input key. However, successful cold-boot verification has not
been demonstrated. The verify path calls Keymaster `0x205` directly; unlike the
Gatekeeper enrollment client, it does not send the `0x200`/`0x207` negotiation
sequence. Source inspection alone cannot determine whether resident TA state
requires that sequence after a cold boot. Test GDM verification after a fresh
boot **before** manually running any Keymaster negotiation or setup helper;
do not add speculative initialization or count a manually warmed test as a
cold-boot pass.

## Build and package

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
login/unlock remain pending as listed above. Placement sensitivity, latency,
identify-status 4/12 retries, suspend/resume, long-term reliability and dual-boot
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
