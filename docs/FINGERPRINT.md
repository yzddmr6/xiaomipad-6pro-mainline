# Xiaomi Pad 6 Pro fingerprint support

[简体中文](FINGERPRINT.zh-CN.md)

## Current status

The native Ubuntu Settings enrollment interface is implemented for the FPC1264
sensor. It uses the normal system authorization dialog and fprintd/libfprint
progress, cancellation and storage. Normal use does not require a terminal or a
separate fingerprint application.

As of 2026-10-07, native Settings enrollment has completed on the known 256 GB
test unit. Its saved template was retained through installation of the combined
DP/fingerprint kernel `6.17.0-rc1-gfe81794b5e1b` and userspace `4d6b22f`.
**Same-finger acceptance, other-finger rejection, native lock-screen unlock and
fingerprint login after an ordinary boot from tablet storage have passed.**
System authentication/session signals and the user's confirmation agree.

This candidate is persistently installed on the test unit but has not been
released. These results do not imply that existing release images include it.

## Requirements

Use a bundle prepared for the actual Linux username/UID, together with its exact
support kernel, FPC module, device tree and OEM firmware. The current scope is
**one configured Linux account and one finger**. Kernel/module hashes and wiring
checks must not be bypassed.

A temporary RAM boot does not install a kernel for the next power-on. Fingerprint
availability after reboot requires the matching kernel and modules to be
persistently installed. That ordinary boot path has passed on this test unit.
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

## Validation scope and remaining work

Native enrollment, template reload, same-finger acceptance, other-finger rejection,
native lock-screen unlock and GDM login after an ordinary reboot have passed.
Password login remains available without a separate unlock application.

Verification does not consume the old boot-local 18-hour enrollment authorization
or ask for the Linux password through the legacy helper. The reboot test started
at the regular login screen without any manual Keymaster negotiation or setup
helper. Fingerprint login does not decrypt the password-encrypted GNOME login
keyring; applications accessing its secrets may still request the keyring password.

Placement sensitivity, latency, suspend/resume, long-term reliability and
multiple fingers/accounts and fingerprint secure-storage coexistence after an
Android round trip are not established for this candidate.

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
