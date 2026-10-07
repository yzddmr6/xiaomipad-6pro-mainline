#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Authenticated enrolment and exclusive publication in fprintd's file store.

Uses the target libfprint serializer and a single-template OEM database. This
does not install a driver, start fprintd, change PAM or enable desktop unlock.
Private biometric bytes stay on the device, never in diagnostic output.
"""
import fcntl
import json
import os
from pathlib import Path
import pwd
import signal
import stat
import subprocess
import sys
import tempfile

from native_credentials import private_directory


FINGERS = ("left-thumb", "left-index-finger", "left-middle-finger", "left-ring-finger",
           "left-little-finger", "right-thumb", "right-index-finger", "right-middle-finger",
           "right-ring-finger", "right-little-finger")


def main():
    arguments = sys.argv[1:]
    store = Path("/var/lib/fprint")
    state = Path("/var/lib/liuqin-fingerprint/native")
    if len(arguments) >= 2 and arguments[0] == "--store-dir":
        store = Path(arguments[1])
        arguments = arguments[2:]
    if len(arguments) >= 2 and arguments[0] == "--state-dir":
        state = Path(arguments[1])
        arguments = arguments[2:]
    if len(arguments) != 3 or arguments[0] not in ("--enrol", "--enrol-prepared") or os.geteuid() != 0 or not store.is_absolute() or not state.is_absolute():
        print("usage (root, interactive terminal): enrol_publish.py "
              "[--store-dir ABSOLUTE_PRIVATE_DIRECTORY] [--state-dir ABSOLUTE_PRIVATE_DIRECTORY] "
              "--enrol[-prepared] LINUX_USERNAME FINGER_NAME", file=sys.stderr)
        return 2
    child = None
    cancelled = False
    lock = -1
    output_fd = -1
    temporary = None
    database = None
    ready = None

    def cancel_operation(signum, frame):
        nonlocal cancelled
        cancelled = True
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGTERM)

    def run(command, inherited=(), environment=None):
        nonlocal child
        if cancelled:
            return 1
        child = subprocess.Popen(command, pass_fds=inherited, env=environment)
        while True:
            try:
                result = child.wait(timeout=0.1)
                child = None
                return 1 if cancelled else result
            except subprocess.TimeoutExpired:
                if cancelled:
                    child.send_signal(signal.SIGTERM)

    signal.signal(signal.SIGTERM, cancel_operation)
    signal.signal(signal.SIGINT, cancel_operation)
    signal.signal(signal.SIGHUP, cancel_operation)
    try:
        user = pwd.getpwnam(arguments[1])
        if user.pw_name != arguments[1] or "/" in user.pw_name or user.pw_name in (".", "..") or not 0 < user.pw_uid < 0x40000000:
            raise ValueError("unsupported Linux account")
        finger = FINGERS.index(arguments[2]) + 1
        here = Path(__file__).resolve().parent
        serializer = here / "fpc-oem-print"
        module = here / "tod" / "libfprint-tod-fpc1264-oem.so"
        for path in (here, serializer, module, here / "user_credentials.py"):
            info = path.lstat()
            if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
                raise ValueError("the complete, trusted runtime bundle is required")
        os.umask(0o077)
        private_directory(store)
        user_store = store / user.pw_name
        private_directory(user_store)
        driver_store = user_store / "fpc1264_oem"
        private_directory(driver_store)
        device_store = driver_store / "liuqin-fpc1264-oem"
        private_directory(device_store)
        lock = os.open(device_store / ".operation.lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        target = device_store / format(finger, "x")
        # The current TOD Verify path selects one print; 1:N Identify is not
        # implemented. Keep the initial usable scope to one finger per user.
        if any(os.path.lexists(device_store / format(value, "x")) for value in range(1, 11)):
            print("oem_print_publication=REFUSED existing_print_preserved=1 "
                  "scope=one_finger_per_user touch_not_collected=1", file=sys.stderr)
            return 2
        pending = Path("/var/lib/liuqin-fingerprint/pending") / user.pw_name
        private_directory(pending)
        database = pending / "single-finger.db"
        ready = pending / "export-ready.json"
        if os.path.lexists(database):
            if not ready.exists():
                print("oem_print_publication=REFUSED uncertain_export_preserved=1 automatic_reenrol=0", file=sys.stderr)
                return 1
            from user_credentials import private_file
            metadata_fd = private_file(ready)
            try:
                with os.fdopen(metadata_fd, "r", closefd=False) as source:
                    metadata = json.load(source)
            finally:
                os.close(metadata_fd)
            if metadata != {"schema": 1, "user": user.pw_name, "finger": arguments[2], "end_enrol_accepted": True, "template_count": 1}:
                raise ValueError("pending export belongs to another accepted enrolment")
            print("oem_print_publication=REUSE accepted_pending_export=1 touch_not_collected=1")
        else:
            result = run([sys.executable, str(here / "user_credentials.py"), "--state-dir", str(state),
                          arguments[0], user.pw_name, str(database)])
            if result:
                return result
            from user_credentials import write_exclusive
            write_exclusive(ready, (json.dumps({"schema": 1, "user": user.pw_name,
                  "finger": arguments[2], "end_enrol_accepted": True, "template_count": 1}) + "\n").encode("ascii"))
        database_fd = os.open(database, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            output_fd, temporary = tempfile.mkstemp(prefix=".fpc-publish-", dir=device_store)
            os.fchmod(output_fd, 0o600)
            environment = os.environ.copy()
            environment["FP_LIUQIN_FPC1264_OEM_ENABLE"] = "1"
            environment["FP_TOD_DRIVERS_DIR"] = str(module.parent)
            result = run([str(serializer), "--serialize", user.pw_name, arguments[2],
                          str(database_fd), str(output_fd)], (database_fd, output_fd), environment)
            if result:
                return result
        finally:
            os.close(database_fd)
        if cancelled or os.fstat(output_fd).st_size <= 3:
            return 1
        os.fsync(output_fd)
        os.close(output_fd)
        output_fd = -1
        # Same-directory exclusive publication leaves an existing print
        # intact even if another writer appears after the initial check.
        os.link(temporary, target, follow_symlinks=False)
        os.unlink(temporary)
        temporary = None
        parent = os.open(device_store, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        database.unlink()
        ready.unlink()
        print("oem_print_publication=OK linux_identity_checked=1 "
              "store_format=fprintd_single_finger private_file_mode=0600 "
              "desktop_authentication=disabled")
        return 0
    except BlockingIOError:
        print("oem_print_publication=BUSY", file=sys.stderr)
        return 1
    except (KeyError, ValueError, OSError):
        print("oem_print_publication=FAILED phase=account_bundle_or_private_store", file=sys.stderr)
        return 2
    finally:
        if output_fd >= 0:
            os.close(output_fd)
        if temporary is not None:
            os.unlink(temporary)
        if lock >= 0:
            os.close(lock)


if __name__ == "__main__":
    sys.exit(main())
