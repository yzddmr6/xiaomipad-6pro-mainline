# One database per finger, with enrollment progress

> **Status: tested and accepted on the 12 GB/512 GB unit.** Two fingers are
> enrolled as two independent single-template databases, both unlock the tablet,
> and Settings shows both entries. The Settings dialog also shows a live
> `已录入 X 次 / 共 Y 次` line. The earlier one-database-multiple-fingers
> experiment is superseded by this design.

The FPC1264 TA stores one opaque database per template. The previous attempt put
several fingers into one database and rewrote the fingerprint ID so fprintd kept
a single print. That worked, but the Settings UI could only show one row, a
single finger could not be deleted, and every finger shared one five-slot
database.

This design instead gives every finger its own database and template file. The
missing piece was fprintd's verification path: with more than one stored print
and no device-side `IDENTIFY`, fprintd verified `gallery[0]` only. The private
fprintd build now walks the sorted gallery one print at a time.

## How it works

```text
Settings enroll for finger F
  -> TOD driver passes FP_ENROL_FINGER=F to the provider
  -> provider creates a NEW single-template TA database
  -> fprintd stores it as its own FP3 file

Verify (any finger)
  -> fprintd sorts all stored prints by finger
  -> print 1: one OEM capture, match or miss
  -> on a miss: non-terminal verify-retry-scan, finger hint, next print
  -> the first match ends the action; no later print is captured
```

Each candidate database needs its own capture. A single physical press is not
reused across databases: the tested client could not re-identify a second
database in the same TEE session (`-249577372`), so the daemon asks for another
press when it moves to the next stored finger. With two enrolled fingers the
worst case is two presses; the first press usually matches.

## Components

| Part | Change |
| --- | --- |
| `libfprint-tod/fpc1264-oem.c` | passes the requested finger to the provider, reports 26 enrollment stages, keeps the per-finger database paths |
| `oem/native_enrol.py` | requires `FP_ENROL_FINGER`, refuses an existing finger file, always creates a fresh database with `--enrol` |
| `oem/fpc_build_info.c` | after arming the lift interrupt, polls the TA state every 250 ms; an IRQ alone is not accepted as a lift |
| `fprintd-oem/fprintd-multidb.patch` | sorts the full gallery, queues the remaining prints, emits `verify-retry-scan` plus `verify-finger-selected`, and stops on cancel |
| `settings-progress/` | optional `LD_PRELOAD` GTK extension that adds the counter to the native fingerprint dialog |

The fprintd patch keeps the existing adaptive-template update: a successful
match is written back to the file that actually matched, and a storage failure
does not report success.

## Tested result

| Test | Result |
| --- | --- |
| Two independent template files | `2` and `7`, each 772,578 B |
| Settings rows | one row per finger |
| Left finger unlock | match |
| Right finger unlock | match |
| Wrong finger | no match, then the daemon asks for the next print |
| Delete one finger | that file is removed; the other finger still matches |
| Enrollment progress | `已录入 4 次 / 共 26 次 · 请完全抬起手指` in the real dialog |
| Enrollment duration | right index, about 78 s for 26 stages |

The private harness also covered cancellation, daemon exit, an empty gallery,
storage failure and deletion of one of two prints. The user then accepted the
installed service on the real lock screen and Settings.

## Enrolling more fingers

1. Close any open Settings window, then reopen **Settings → System → Users →
   Fingerprint Login**.
2. Authorize with the normal password dialog.
3. Scan the finger, lift it fully, and reposition between samples. The counter
   only increases for accepted stages; retries do not increase it.
4. Repeat for another finger. Each finger creates a new row and a new file.
5. Lock the screen and unlock with either finger.

Do not enroll the same finger twice: it creates a second database that competes
for the same finger and slows verification.

## Limits

- **One press per candidate database.** The TA does not expose a way to reuse
  one capture across databases in the tested client. With two fingers, a miss on
  the first asks for a second press.
- **PAM timeout.** `common-auth` still uses `timeout=15`. Two databases are
  within the measured range, but this has not been re-measured for slow captures
  or many fingers.
- **Order is by finger, not by recent success.** The gallery is sorted
  left-thumb to right-little; the daemon does not remember the last successful
  finger yet.
- **Settings claims the reader.** While the fingerprint dialog is open,
  `gnome-control-center` holds the device; `sudo` and the lock screen may fall
  back to the password until it is closed. The progress extension closes the
  dialog on session lock, but does not otherwise release it.
- **UI extension is optional and fragile.** It depends on the
  `CcFingerprintDialog` type and the `enroll_message`/`enrollment_view` widget
  names. A Settings update can make it silently stop working; the enrollment
  itself is unaffected.
- **No deduplication.** Enrolling an already-stored finger creates another
  database instead of replacing or rejecting it.

## Building and installing

The driver, provider, client and fprintd daemon are built by the component
scripts:

```sh
tools/build-tod.sh
tools/build-userspace.sh --with-rpmb-provider
FPRINTD_SOURCE=/path/to/fprintd-v1.94.5 tools/build-fprintd-oem.sh
```

`build-fprintd-oem.sh` applies `fprintd-multidb.patch` after the existing
adaptive-update preparation. The progress extension is separate:

```sh
device/fingerprint/settings-progress/build.sh
sudo device/fingerprint/settings-progress/install.sh
```

The installer keeps the distribution Settings binary, adds a local
`dpkg-divert` and installs a launcher that preloads the extension. Re-run it
after a Settings package update.

## Rollback

Stop fprintd and restore the component and template backup taken before the
switch. The deployment backup used on the test unit was
`/root/fpc-deploy-single-db-20261030-123745/`; its `rollback.sh` restores the
previous round2 suite and the merged template without deleting newly enrolled
files. The progress extension is removed with:

```sh
sudo device/fingerprint/settings-progress/uninstall.sh
```

## Provenance

The per-finger split, the serial verify daemon patch and the progress extension
were developed for this tablet. The fprintd changes are a local patch against
1.94.5, not an upstream submission. The TA protocol work builds on the OEM
component in this repository; no vendor binaries are distributed here.
