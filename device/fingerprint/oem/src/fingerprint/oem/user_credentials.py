#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Authenticate a Linux user before creating or using its native credential.

The password is read by the colocated PAM helper from /dev/tty. Only its
32-byte derived result crosses anonymous FDs. Public KDF parameters and the
opaque Gatekeeper handle are private, root-owned state; no password or HAT is
persisted. No credential is silently re-created after a failed authorization.
"""
import fcntl
import json
import os
from pathlib import Path
import pwd
import resource
import signal
import stat
import subprocess
import sys

from native_credentials import private_directory


def private_file(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        os.close(descriptor)
        raise ValueError("invalid private state file")
    return descriptor


def write_exclusive(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            output.write(data)
            output.flush()
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def sealed_memory(data):
    descriptor = os.memfd_create("liuqin-fpc-public-parameters", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        os.fchmod(descriptor, 0o600)
        if os.write(descriptor, data) != len(data):
            raise OSError("incomplete parameter FD")
        os.lseek(descriptor, 0, os.SEEK_SET)
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS,
                    fcntl.F_SEAL_WRITE | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SEAL)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def native_account_uid(state, linux_uid):
    """Reuse any established legacy account, including uncertain creation."""
    legacy_uid = 0x40000000 | linux_uid
    if any(os.path.lexists(state / str(legacy_uid) / name) for name in
           ("gatekeeper.handle", "input-parameters.json", "create.requested")):
        return legacy_uid
    return 0x50000000 | linux_uid


def main():
    arguments = sys.argv[1:]
    state = Path("/var/lib/liuqin-fingerprint/native")
    if len(arguments) >= 2 and arguments[0] == "--state-dir":
        state = Path(arguments[1])
        arguments = arguments[2:]
    create_and_prepare = len(arguments) == 2 and arguments[0] == "--create-and-prepare-acceptance"
    create = create_and_prepare or (len(arguments) == 2 and arguments[0] == "--create")
    preflight = len(arguments) == 2 and arguments[0] in ("--auth-preflight", "--auth-preflight-renew")
    sync_password = len(arguments) == 2 and arguments[0] == "--sync-password"
    prepare_acceptance = create_and_prepare or (len(arguments) == 2 and arguments[0] == "--prepare-acceptance")
    prepared_enrol = len(arguments) == 3 and arguments[0] == "--enrol-prepared"
    enrol = len(arguments) == 3 and arguments[0] == "--enrol"
    enrol_existing = len(arguments) == 4 and arguments[0] == "--enrol-existing"
    if os.geteuid() != 0 or not state.is_absolute() or not (create or preflight or sync_password or enrol or enrol_existing or prepare_acceptance or prepared_enrol):
        print("usage (root, interactive terminal): user_credentials.py [--state-dir ABSOLUTE_PRIVATE_DIRECTORY] "
              "--create[-and-prepare-acceptance] LINUX_USERNAME | --auth-preflight[-renew] LINUX_USERNAME | --sync-password LINUX_USERNAME | "
              "--prepare-acceptance LINUX_USERNAME | --enrol-prepared LINUX_USERNAME OUTPUT_DATABASE | "
              "--enrol LINUX_USERNAME OUTPUT_DATABASE | "
              "--enrol-existing LINUX_USERNAME INPUT_DATABASE OUTPUT_DATABASE", file=sys.stderr)
        return 2
    descriptors = []
    child = None
    cancelled = False

    def cancel_operation(signum, frame):
        nonlocal cancelled
        cancelled = True
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGTERM)

    def run(command, inherited):
        nonlocal child
        if cancelled:
            return 1
        # Keep the controlling terminal for PAM. Lower layers own TA cleanup.
        child = subprocess.Popen(command, pass_fds=inherited)
        while True:
            try:
                result = child.wait(timeout=0.1)
                child = None
                return 1 if cancelled else result
            except subprocess.TimeoutExpired:
                if cancelled:
                    child.send_signal(signal.SIGTERM)

    signal.signal(signal.SIGINT, cancel_operation)
    signal.signal(signal.SIGTERM, cancel_operation)
    signal.signal(signal.SIGHUP, cancel_operation)
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        account_info = pwd.getpwnam(arguments[1])
        if account_info.pw_name != arguments[1] or not 0 < account_info.pw_uid < 0x10000000:
            raise ValueError("unsupported native account UID")
        # Diagnostic clients historically occupied 0x40000000 | POSIX UID.
        # First human setup uses its own range, without recreating that SID.
        # A previously persisted human identity in the old range is reused.
        uid = native_account_uid(state, account_info.pw_uid)
        here = Path(__file__).resolve().parent
        helper = here / "pam-input"
        for path in (here, helper, here / "native_credentials.py"):
            info = path.lstat()
            if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
                raise ValueError("runtime bundle must be root-owned and not writable by other users")
        if not helper.is_file():
            raise ValueError("the colocated PAM helper is missing")
        os.umask(0o077)
        private_directory(state)
        account = state / str(uid)
        private_directory(account)
        lock = os.open(account / "user-operation.lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        descriptors.append(lock)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle = account / "gatekeeper.handle"
        metadata = account / "input-parameters.json"
        requested = account / "create.requested"
        if create and (os.path.lexists(handle) or os.path.lexists(requested)):
            print("linux_user_credential=REFUSED existing_identity_preserved=1 "
                  "new_credential_not_requested=1", file=sys.stderr)
            return 2
        if not create and not os.path.lexists(handle):
            print("linux_user_credential=FAILED phase=missing_persistent_handle "
                  "new_credential_not_requested=1", file=sys.stderr)
            return 2
        if create and not os.path.lexists(metadata):
            parameters = {"schema": 1, "linux_username": account_info.pw_name,
                          "linux_uid": account_info.pw_uid, "native_uid": uid,
                          "kdf": "PBKDF2-HMAC-SHA256", "iterations": 600000,
                          "salt_hex": os.urandom(32).hex(), "input_bytes": 32}
            write_exclusive(metadata, (json.dumps(parameters, sort_keys=True) + "\n").encode("ascii"))
        parameter_fd = private_file(metadata)
        descriptors.append(parameter_fd)
        if os.fstat(parameter_fd).st_size > 4096:
            raise ValueError("invalid public parameter size")
        with os.fdopen(parameter_fd, "r", closefd=False) as source:
            parameters = json.load(source)
        if (parameters.get("schema"), parameters.get("linux_username"), parameters.get("linux_uid"),
                parameters.get("native_uid"), parameters.get("kdf"), parameters.get("iterations"),
                parameters.get("input_bytes")) != (1, account_info.pw_name, account_info.pw_uid, uid,
                                                    "PBKDF2-HMAC-SHA256", 600000, 32):
            raise ValueError("public parameters do not match this account or supported KDF")
        salt = bytes.fromhex(parameters["salt_hex"])
        if len(salt) != 32:
            raise ValueError("invalid public salt size")
        salt_fd = sealed_memory(salt)
        descriptors.append(salt_fd)
        input_fd = os.memfd_create("liuqin-fpc-derived-input", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        os.fchmod(input_fd, 0o600)
        descriptors.append(input_fd)
        current_fd = -1
        if sync_password:
            current_fd = os.memfd_create("liuqin-fpc-previous-input", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
            os.fchmod(current_fd, 0o600)
            descriptors.append(current_fd)
            helper_arguments = ["--sync-inputs", account_info.pw_name, str(salt_fd),
                                str(current_fd), str(input_fd)]
            inherited = (salt_fd, current_fd, input_fd)
        else:
            helper_arguments = ["--authenticate", account_info.pw_name, str(salt_fd), str(input_fd)]
            inherited = (salt_fd, input_fd)
        if prepared_enrol:
            from acceptance_input import consume
            prepared_fd = consume(account_info.pw_name, uid)
            os.close(input_fd)
            descriptors.remove(input_fd)
            input_fd = prepared_fd
            descriptors.append(input_fd)
            result = 0
        else:
            result = run([str(helper), *helper_arguments], inherited)
        if result:
            print("linux_user_credential=FAILED phase=linux_identity "
                  "gatekeeper_not_requested=1", file=sys.stderr)
            return result
        input_fds = (current_fd, input_fd) if sync_password else (input_fd,)
        for descriptor in input_fds:
            if os.fstat(descriptor).st_size != 32:
                raise ValueError("incomplete authenticated derived input")
            os.lseek(descriptor, 0, os.SEEK_SET)
            if not prepared_enrol:
                fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS,
                            fcntl.F_SEAL_WRITE | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SEAL)
        if create:
            # Persist an uncertainty marker before the TA can change identity.
            # A failed call must not silently retry a new SID for the same UID.
            write_exclusive(requested, b"")
            operation = ["--create", str(uid), str(input_fd)]
        elif sync_password:
            operation = ["--change-input", str(uid), str(current_fd), str(input_fd)]
        elif enrol_existing:
            operation = ["--enrol-existing", *arguments[2:4], str(uid), str(input_fd)]
        elif enrol or prepared_enrol:
            operation = ["--enrol", arguments[2], str(uid), str(input_fd)]
        elif prepare_acceptance:
            operation = ["--auth-preflight-renew", str(uid), str(input_fd)]
        else:
            operation = [arguments[0], str(uid), str(input_fd)]
        result = run([sys.executable, str(here / "native_credentials.py"),
                      "--state-dir", str(state), *operation], input_fds)
        if prepare_acceptance and result == 0 and not cancelled:
            from acceptance_input import prepare
            prepare(account_info.pw_name, uid, input_fd)
            print("acceptance_input=PREPARED kernel_memory_only=1 one_use=1 expires_hours=18 touch_not_collected=1")
        if sync_password and result == 0:
            from acceptance_input import discard
            discard(account_info.pw_name)
        print(f"linux_user_credential={'OK' if result == 0 else 'FAILED'} "
              "linux_identity_checked=1 anonymous_input_fd=1 password_or_derived_input_persisted=0 "
              "desktop_authentication=disabled")
        return result
    except BlockingIOError:
        print("linux_user_credential=BUSY", file=sys.stderr)
        return 1
    except (KeyError, ValueError, OSError):
        print("linux_user_credential=FAILED phase=account_parameters_or_private_storage", file=sys.stderr)
        return 2
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


if __name__ == "__main__":
    sys.exit(main())
