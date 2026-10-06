# FPC1264 OEM TOD and final acceptance

The public [user guide](../../../../../../docs/FINGERPRINT.md) records support and
acceptance scope. The installed candidate is identified by its CANDIDATE.json
and SHA256.json, not by a host-local development path. The private installed
bundle is /usr/local/lib/liuqin-fpc-oem; /run staging and earlier milestones do
not identify a new candidate.

The adapter uses the tablet's existing libfprint 1.95.1+tod1 ABI and explicit
TOD discovery. Build with `tools/build-tod.sh` on that ABI. It does not replace
the system libfprint. A version-1 RAW `(uay)` FpPrint holds exactly one opaque
OEM database. The normal match path initializes hardware auth each session,
loads the database, requires one template, identifies and checks the returned
match-HAT envelope. It contains no host software biometric matcher.

The existing fprintd enrol API supplies no derived-input FD. Its enrol callback
therefore reports NOT_SUPPORTED. Authenticated enrolment uses the supplied
PAM/native credential provider, two fresh challenge/HAT rounds in one FPC
session, accepted END, one-template inventory and opaque database export.
`fpc-oem-print` serializes caller metadata and the accepted database, checking
deserialize/compatibility/equality in process. `enrol_publish.py` publishes
exclusively to the normal private fprintd file store. Accepted export survives
publication failure in the pending directory; uncertain export is preserved
without automatically repeating enrolment. Current scope is one finger/user.

The adapter now requests UPDATED_DATABASE on genuine matches. Completed,
cleaned success may replace the in-memory enrolled print and attach its original
clone as qdata. The private fprintd patch under `src/fingerprint/fprintd-oem`
commits adaptation in the framework file store before VerifyStatus success.
It compares the stored baseline under the common enrol/store lock, fsyncs the
new file, retains an old-file link and commits atomically. Storage errors reject
authentication success; miss, cancellation and inconclusive output never save.
Build that private daemon with `tools/build-fprintd-oem.sh`.

Current actual checks passed target ARM64 builds, genuine PAM account checks,
first native credential creation, same-input existing credential update with
internal SID comparison, two signatures accepted by FPC in one session, and
cross-process root kernel-keyring input consumption with another fresh signed
authorization. The real confined private daemon on the system D-Bus passed
GetDevices/Polkit Claim/Release. On the recorded desktop-r3 boot, one real OEM
print, same-finger matching, daemon-reload matching, other-finger rejection and
user-observed GNOME unlock passed. Contact-position sensitivity and unlock
stutter remain. See the public user guide for the recorded device scope; compilation does not
establish reliability or latency.

`fprintd-fpc1264-oem.conf` is installed: the private daemon ExecStart, explicit
TOD discovery, shared RuntimeDirectoryPreserve=yes, firmware-path exception and
observed FPC/TEE/UFS nodes retain the vendor confinement. It depends on the late
stable-module service after desktop/SSH/rotation startup. The system daemon
stays masked until the final real same/reloaded/other-finger acceptance passes.
Password PAM remains unchanged; existing GDM fingerprint integration is used.

The installed application “指纹最终验收” runs the narrowly scoped privileged
`acceptance.py`. It reuses an accepted print/export if present, otherwise does
one normal physical enrolment, same-finger verification, daemon-reload/same-
finger verification, then other-finger rejection before activating desktop
fprintd. The caller lifts its finger and confirms the stage before each Verify.
An 18-hour boot-local, one-use root kernel-keyring input can be prepared only
after real PAM and signed preflight; it is revoked on use and never written to
disk. No waiting collection process is needed. Expiry/reboot requires normal
PAM input again. `--recover` masks fingerprint authentication and retains data.

After a Linux password change, the explicit `user_credentials.py --sync-password`
entry authenticates the new/current Linux password and obtains the prior
fingerprint password through hidden tty prompts. Separate sealed FDs update the
existing Gatekeeper handle; failures preserve the prior file and uncertain
output. No fallback identity creation or template reset is performed.

The stable kernel remains a temporary `fastboot boot` image. Ordinary reboot
returns the installed kernel; temporarily boot the previously accepted support
image with its exact matching module. The late module service checks the
candidate identity after the desktop/network/rotation services. No partition
is flashed. Kernel identity and public component hashes are in the candidate; voltage stays 2960000–3008000 microvolts. The specific retry branch, next cold start, suspend/resume and sustained
reliability still need their own evidence. Unknown historical GENI memory corruption is not
declared fixed.
