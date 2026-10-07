# Xiaomi Pad 6 Pro fingerprint support

[简体中文](FINGERPRINT.zh-CN.md)

## Current status

The native Ubuntu Settings enrollment interface is implemented for the FPC1264
sensor. It uses the normal system authorization dialog and fprintd/libfprint
progress, cancellation and storage. Normal use does not require a terminal or a
separate fingerprint application.

As of 2026-10-07, the matching native candidate (`70a4494`) is running on the test
device. “Fingerprint Login” appears on the native Users page, and a root-driven
`EnrollStart` → cancel check completed with cleanup. **No completed fingerprint
template is present yet. Enrollment, same/other-finger matching, persistence,
lock-screen unlock and cold-boot login remain unverified.** Root API cancellation
is not proof of the normal user's authorization or full Settings workflow.

Earlier contributor results used a different component combination and do not
establish acceptance of this candidate. Fingerprint support is still an optional
candidate, not a fully validated capability of every released image.

## Requirements

Use a bundle prepared for the actual Linux username/UID, together with its exact
support kernel, FPC module, device tree and OEM firmware. The current scope is
**one configured Linux account and one finger**. Kernel/module hashes and wiring
checks must not be bypassed.

A temporary RAM boot does not install a kernel for the next power-on. Fingerprint
availability after reboot requires the matching kernel and modules to be
persistently installed. That cold-boot path is still awaiting validation.
Build and installation details are in the [component reference](../device/fingerprint/oem/README.md).

## Enroll through Ubuntu Settings

1. Open **Settings → System → Users → Fingerprint Login** for the configured
   account. Unlock Settings if the normal system dialog requests it.
2. Choose a finger and authorize enrollment in the system password dialog.
3. Follow the native scan, lift/reposition and progress instructions until the
   dialog reports completion. Cancellation waits for the ongoing operation to
   clean up before releasing the reader.

After successful enrollment, verification uses the regular Ubuntu login and
lock screen. The normal password-login stack is preserved, so password login
remains the fallback. The presence of the Settings row or a running service does
not demonstrate successful enrollment or unlock.

Another enrollment is refused while a finger is already stored for this
account. If intentionally replacing it, use the Settings deletion control first;
do not remove private files to force re-enrollment.

## Passwords and existing identities

The password dialog is the desktop's Polkit authorization for enrolling or
deleting a fingerprint. The provider does **not** store the Linux password. It
creates a separate, root-only random machine credential and Gatekeeper handle
for the configured account; those private files stay on this tablet. A Linux
password change therefore requires no separate fingerprint-password sync for
this native route.

Existing password-derived identities and templates are not automatically
migrated, recreated or deleted. Preserve any uncertain-creation markers rather
than attempting a credential reset. Initial credential creation writes real
Gatekeeper/RPMB state; cancellation during that short transaction finishes its
safe commit before stopping and does not proceed into fingerprint capture.
A temporary kernel does not make these operations read-only.

## Validation still required

The current candidate needs a completed native enrollment, same-finger acceptance,
other-finger rejection, reload of the saved template, native lock-screen unlock
and fresh-boot GDM login before it can be considered working end to end.

Verification does not consume the old boot-local 18-hour enrollment authorization
or ask for the Linux password through the legacy helper. Whether resident
Keymaster state needs additional initialization after a cold boot remains
unproven. Cold-boot testing must start at the regular login screen before running
any manual Keymaster negotiation or setup helper.

Placement sensitivity, latency, suspend/resume, long-term reliability and
Android dual-boot coexistence are not established for this candidate.

## Appendix: recovery and legacy diagnostics

If fingerprint verification must be disabled while preserving private state and
password login, an administrator can stop and mask the normal daemon:

~~~sh
sudo systemctl mask --now fprintd.service
~~~

Diagnostic details are available from `fprintd.service` and
`liuqin-fpc-stable-module.service`. Share only status/phase information, never
passwords, random credentials, handles, tokens, templates or calibration.

`acceptance.py`, the old terminal launcher and `user_credentials.py` remain
internal tools for the legacy development-helper interface. They are not the
normal native entry; native installation removes their application-menu launchers.
The old `--sync-password` command applies only to password-derived legacy
identities. Do not run legacy create or random probe-UID operations as native
preflight checks. See the [component reference](../device/fingerprint/oem/README.md)
for that distinction and the matching-package requirements.
