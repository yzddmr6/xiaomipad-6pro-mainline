#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Persist an opaque native Gatekeeper handle; caller owns the derived-input FD.

No password, derived input or HAT is stored here. Creation is explicit and will
not replace an existing handle. Authorization reuses the credential's identity.
The caller must authenticate/authorize the Linux user before requesting enrol.
"""
import fcntl
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile


def private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("credential directory must be a root-owned private directory")


def main():
    arguments = sys.argv[1:]
    state = Path("/var/lib/liuqin-fingerprint/native")
    if len(arguments) >= 2 and arguments[0] == "--state-dir":
        state = Path(arguments[1])
        arguments = arguments[2:]
    create = len(arguments) == 3 and arguments[0] == "--create"
    change = len(arguments) == 4 and arguments[0] == "--change-input"
    authorize = len(arguments) == 3 and arguments[0] in ("--auth-preflight", "--auth-preflight-renew")
    enrol_existing = len(arguments) == 5 and arguments[0] == "--enrol-existing"
    enrol = enrol_existing or len(arguments) == 4 and arguments[0] == "--enrol"
    if os.geteuid() != 0 or not (create or change or authorize or enrol) or not state.is_absolute():
        print("usage (root): native_credentials.py [--state-dir ABSOLUTE_PRIVATE_DIRECTORY] "
              "--create NATIVE_UID DERIVED_INPUT_FD | --auth-preflight NATIVE_UID DERIVED_INPUT_FD | "
              "--change-input NATIVE_UID CURRENT_INPUT_FD DESIRED_INPUT_FD | "
              "--enrol DATABASE NATIVE_UID DERIVED_INPUT_FD | "
              "--enrol-existing INPUT_DATABASE OUTPUT_DATABASE NATIVE_UID DERIVED_INPUT_FD", file=sys.stderr)
        return 2
    try:
        uid = int(arguments[3 if enrol_existing else 2 if enrol else 1], 10)
        input_fd = int(arguments[-1], 10)
        if not 0x40000000 <= uid <= 0x7fffffff or input_fd < 3 or os.isatty(input_fd):
            raise ValueError("native UID or binary input FD invalid")
        if fcntl.fcntl(input_fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_WRONLY:
            raise ValueError("derived input FD is not readable")
        os.fstat(input_fd)
        current_fd = -1
        if change:
            current_fd = int(arguments[2], 10)
            if current_fd < 3 or current_fd == input_fd or os.isatty(current_fd) or fcntl.fcntl(current_fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_WRONLY:
                raise ValueError("current derived input FD invalid")
            os.fstat(current_fd)
        runtime = Path(__file__).resolve().parent / "oem_runtime.py"
        if not runtime.is_file():
            raise ValueError("the colocated OEM runtime is missing")
        os.umask(0o077)
        private_directory(state)
        account = state / str(uid)
        private_directory(account)
    except (ValueError, OSError):
        print("native_credential_provider=FAILED phase=arguments_or_private_directory", file=sys.stderr)
        return 2

    handle = account / "gatekeeper.handle"
    lock_fd = -1
    handle_fd = -1
    temporary = None
    retain_temporary = False
    published = False
    cancelled = False
    child = None

    def cancel_operation(signum, frame):
        nonlocal cancelled
        cancelled = True
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGTERM)

    def run_runtime(operation, inherited):
        nonlocal child
        if cancelled:
            return 1
        child = subprocess.Popen(operation, pass_fds=inherited)
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
        lock_fd = os.open(account / "operation.lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        command = [sys.executable, str(runtime), "--ufs-rpmb-authenticated"]
        if create:
            if os.path.lexists(handle):
                print("native_credential_provider=FAILED existing_handle_preserved=1 "
                      "new_credential_not_requested=1", file=sys.stderr)
                return 2
            handle_fd, temporary = tempfile.mkstemp(prefix=".gatekeeper-handle-", dir=account)
            os.fchmod(handle_fd, 0o600)
            result = run_runtime([*command, "--new-native-credential", str(uid),
                                  str(input_fd), str(handle_fd)], (input_fd, handle_fd))
            retain_temporary = 0 < os.fstat(handle_fd).st_size <= 1024
            if result:
                return result
            info = os.fstat(handle_fd)
            if not 0 < info.st_size <= 1024:
                print("native_credential_provider=FAILED phase=opaque_handle_output", file=sys.stderr)
                return 1
            os.fsync(handle_fd)
            os.close(handle_fd)
            handle_fd = -1
            # Same-directory hard link publishes without replacing any file.
            os.link(temporary, handle, follow_symlinks=False)
            published = True
            os.unlink(temporary)
            temporary = None
            directory_fd = os.open(account, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            print("native_credential_provider=CREATED opaque_handle_persisted=1 "
                  "derived_input_persisted=0 user_identity_validation=caller_owned")
            return 0

        handle_fd = os.open(handle, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        info = os.fstat(handle_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= 1024:
            print("native_credential_provider=FAILED phase=private_opaque_handle", file=sys.stderr)
            return 2
        if change:
            output_fd, temporary = tempfile.mkstemp(prefix=".gatekeeper-update-", dir=account)
            try:
                os.fchmod(output_fd, 0o600)
                result = run_runtime([*command, "--change-native-credential", str(uid), str(handle_fd),
                                      str(current_fd), str(input_fd), str(output_fd)],
                                     (handle_fd, current_fd, input_fd, output_fd))
                retain_temporary = 0 < os.fstat(output_fd).st_size <= 1024
                if result or cancelled:
                    print("native_credential_update=FAILED existing_handle_file_preserved=1 "
                          "new_credential_not_requested=1", file=sys.stderr)
                    return result or 1
                if not 0 < os.fstat(output_fd).st_size <= 1024:
                    return 1
                os.fsync(output_fd)
            finally:
                os.close(output_fd)
            directory_fd = os.open(account, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            backup_fd, previous = tempfile.mkstemp(prefix=".gatekeeper-previous-", dir=account)
            os.close(backup_fd)
            os.unlink(previous)
            try:
                os.link(handle, previous, follow_symlinks=False)
                os.fsync(directory_fd)
                if cancelled:
                    return 1
                os.replace(temporary, handle)
                temporary = None
                os.fsync(directory_fd)
                os.unlink(previous)
                previous = None
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
                # On an uncertain commit retain the original opaque handle
                # beside the installed one. No automatic recovery or retry.
                if previous is not None:
                    print("native_credential_update_previous_handle_retained=1 "
                          "automatic_retry=0 secure_state_after_failure=unverified", file=sys.stderr)
            print("native_credential_update=OK opaque_handle_replaced=1 "
                  "new_credential_not_requested=1 sid_validation=TA_owned_and_client_checked")
            return 0
        if enrol_existing:
            operation = ["--enrol-existing", *arguments[1:3], str(uid), str(handle_fd), str(input_fd)]
        elif enrol:
            operation = ["--enrol", arguments[1], str(uid), str(handle_fd), str(input_fd)]
        else:
            operation = [arguments[0], str(uid), str(handle_fd), str(input_fd)]
        return run_runtime([*command, *operation], (handle_fd, input_fd))
    except BlockingIOError:
        print("native_credential_provider=BUSY", file=sys.stderr)
        return 1
    except OSError:
        print(f"native_credential_provider=FAILED phase=handle_storage "
              f"existing_handle_preserved={int(published or os.path.lexists(handle))}", file=sys.stderr)
        return 1
    finally:
        if handle_fd >= 0:
            os.close(handle_fd)
        if temporary is not None:
            if retain_temporary:
                print("opaque_handle_candidate_retained=1 automatic_retry=0 "
                      "secure_state_after_failure=unverified", file=sys.stderr)
            else:
                os.unlink(temporary)
        if lock_fd >= 0:
            os.close(lock_fd)


if __name__ == "__main__":
    sys.exit(main())
