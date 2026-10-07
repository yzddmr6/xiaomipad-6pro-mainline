# OEM adaptive template persistence

The vendor fprintd 1.94.5 source snapshot is left unchanged. `prepare.py` copies
it to a private build tree and applies the generated three-file patch together
with `oem-update.inc`. Build only the daemon using `tools/build-fprintd-oem.sh`
against the tablet's installed libfprint ABI. The upstream PAM module and the
system libfprint remain installed; the candidate daemon is installed privately.

The TOD driver requests an updated opaque database from a genuine successful
match. It clones the original FpPrint and supplies that clone through private
qdata on the enrolled print. fprintd consumes this marker in `match_cb` before
emitting `VerifyStatus`: under the shared enrol/store lock it compares the
disk print with the original, writes a root-private temporary file, saves an
old-file hard link, and commits through rename and fsync. Failure restores the
in-memory original, refuses authentication success, and preserves the original
file or its recovery link. Cancelled, unmatched and inconclusive results never
publish adaptation. Discovery ignores the multi-character recovery filenames.

Native target compilation and real system-bus discovery/Claim/Release passed.
The recorded desktop-r3 boot has a real OEM print and passed same-finger,
reload and other-finger acceptance; the user also reported GNOME unlock.
Position sensitivity, stutter and unobserved retry/cold-start cases remain
documented in the [user guide](../../../../../../docs/FINGERPRINT.md).
`prepare.py --patch-output PATH` writes the generated patch into the build
output; running a build leaves the canonical source patch untouched.
