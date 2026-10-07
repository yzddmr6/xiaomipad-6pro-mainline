# Fingerprint on Xiaomi Pad 6 Pro

[简体中文](FINGERPRINT.zh-CN.md)

## Status

FPC1264 fingerprint support is optional and currently supports one fingerprint
per Linux user. On the recorded desktop-r3 device setup, authorised enrolment,
persisted same-finger matching, matching after a daemon restart, rejection of an
unenrolled finger and GNOME lock-screen unlock passed. Automatic rotation also
passed on that setup. Placement sensitivity and unlock stutter remain; there
is no measured success rate or touch-to-unlock latency.

The default project image does not install this component or enable fingerprint
login. The current source-layout and packaging adaptation is a new candidate:
its CI build and packaging checks passed, but it has not been installed and
accepted on the tablet. The earlier device result applies to its recorded
component combination, not to every image built from this branch.

## Requirements and installation

Use the previously accepted support boot with its exact matching FPC module,
OEM FPC/Keymaster trusted applications, and a real Gatekeeper/UFS RPMB provider.
The tested desktop ABI is libfprint/TOD 1.95.1+tod1 with the project fprintd
adaptation. Firmware, boot binaries and private device state are separate
inputs. Build and explicit candidate preparation are described in the
[component instructions](../device/fingerprint/oem/README.md).

Install only a candidate prepared for the actual Linux account and support
kernel. Extract its archive into a root-owned directory and run its install.py
as root. Installation preserves password login and keeps the normal fprintd
service masked until physical acceptance. It adds an isolated PAM input stack;
it does not replace the system password-login stack.

The support kernel currently uses temporary fastboot boot. Ordinary reboot
returns to the installed kernel and may remove fingerprint support. This source
series does not install a kernel to a real boot partition. The complete DTB from
the separate kernel submission has not passed device boot acceptance.

## First use and verification

After starting the accepted support boot, open “指纹最终验收” (Fingerprint final
acceptance) in the application menu. The installed launcher obtains its narrow
local authorisation. A terminal can also use:

~~~sh
sudo /usr/local/lib/liuqin-fpc-oem/acceptance.py
~~~

A new Linux account without an existing Gatekeeper handle needs explicit
credential setup once, after booting the matching support kernel:

~~~sh
sudo /usr/local/lib/liuqin-fpc-oem/user_credentials.py --create "$USER"
~~~

This authenticates the Linux password and creates that account's credential
through the normal Gatekeeper chain. Existing handles are preserved: do not
repeat creation to repair a failed match or delete an uncertainty marker to
force it. Accounts with existing credentials skip this step.

Use the normal Linux password prompt when credential input is required. If a
published template already exists, the entry reuses it. It does not ask for
another enrolment to investigate a matching or packaging problem.

The full acceptance flow asks for the enrolled finger, restarts the daemon and
asks for that finger again, then asks for an unenrolled finger. Only successful
same-finger/reload matches and explicit other-finger rejection enable the desktop
daemon. Finally, lock GNOME, unlock with the enrolled finger, and check display
and automatic rotation. A successful TA call or build alone does not establish
that desktop result.

For one match attempt with an existing template:

~~~sh
sudo /usr/local/lib/liuqin-fpc-oem/acceptance.py --verify
~~~

This reports one match result. It does not write the full acceptance record or
complete the desktop acceptance flow. Normal GNOME Settings enrolment is
unsupported because its Enroll API does not supply the required authorisation.

## Password changes and recovery

After changing the Linux password, use the separate synchronisation entry:

~~~sh
sudo /usr/local/lib/liuqin-fpc-oem/user_credentials.py --sync-password "$USER"
~~~

It authenticates the current Linux password through PAM and obtains the prior
fingerprint credential password through hidden terminal prompts. Failure or
cancellation preserves the existing handle and templates. Desktop password
changes do not yet invoke this entry automatically.

To disable fingerprint login while retaining credentials and templates:

~~~sh
sudo /usr/local/lib/liuqin-fpc-oem/acceptance.py --recover
~~~

Password login stays available. The recovery entry does not rebind GENI or reset
secure storage.

## Troubleshooting and remaining limits

- If the application entry is missing, the optional candidate has not necessarily
  been installed. Follow its build/install instructions; the default image does
  not promise fingerprint support.
- If the entry reports a kernel/module mismatch or an unavailable FPC/TEE node,
  verify the candidate's exact support boot and module identity. Retain the
  2960000–3008000 microvolt sensor supply.
- If matching is inconsistent, lift the finger fully and try a different contact
  position. Placement sensitivity is a known limitation; repeated enrolment is
  not the default recovery step.
- If GNOME fails to unlock or remains slow, retain password login and inspect
  only public integer/status diagnostics. Do not publish passwords, derived
  inputs, handles, HATs, templates, raw captures or private calibration.
- The specific identify 4/12 retry path, cold boot, suspend/resume and long-term
  reliability are unaccepted. Historical GENI memory corruption remains
  undiagnosed. This work does not claim to fix it.

## Components and sources

The kernel series supplies QSEE transport and SPI sensor control. OEM trusted
applications perform authorisation and biometric matching. The userspace series
supplies the authenticated provider, PAM input, enrolment/persistence, TOD adapter
and fprintd adaptation. It does not use a host software fingerprint matcher.

Dependencies and original source hashes are recorded in
[SOURCE_LOCK.json](../device/fingerprint/oem/SOURCE_LOCK.json). File-level
licenses and [NOTICE](../NOTICE) retain the original authorship and terms.
The legacy /dev/fpc1020 and fpc,fpc1020 names are retained device ABI names;
validated hardware scope is this tablet's FPC1264.
