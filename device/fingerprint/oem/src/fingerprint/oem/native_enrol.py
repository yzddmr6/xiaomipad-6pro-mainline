#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Root-only enrol provider for an already-authorized fprintd EnrollStart.

fprintd owns sender/claimed-user/Polkit authorization. This provider never
authenticates as the daemon or asks for a password. A separate native identity
uses a root-private random credential; its HAT proves possession of that machine
credential, not that Gatekeeper checked a human's Linux password. Existing
password-derived identities and Android accounts are never opened or reset.
"""
import fcntl
import json
import os
from pathlib import Path
import pwd
import resource
import shutil
import signal
import stat
import subprocess
import sys
import tempfile

STATE_ROOT = Path("/var/lib/liuqin-fingerprint/native-enrollment")
FPRINT_ROOT = Path("/var/lib/fprint")
CLEANUP = "oem_runtime_cleanup firmware_path_restored=1 sensor_power_off=1 listener_stopped=1"
FORWARD = ("READY ", "enrol_progress_status=", "capture_rejected=", "oem_runtime_cleanup ",
           "native_credential_provider=FAILED", "native_credential_provider=BUSY",
           "pipeline_incomplete=", "end_enrol_not_submitted=", "enrol_authorization=",
           "oem_runtime_timed_out=", "oem_runtime_cancelled=")


def private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700):
        raise ValueError("unsafe private directory")


def private_read(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or
                (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o600) or
                not 0 < info.st_size <= limit):
            raise ValueError("unsafe private file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            return stream.read(limit + 1)
    finally:
        os.close(fd)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_exclusive(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
        os.fsync(fd)
    finally:
        os.close(fd)
    sync_directory(path.parent)


class Runtime:
    def __init__(self):
        self.child = None
        self.cancelled = False
        self.creating = False
        self.cleaned = True  # No hardware child has been started yet.

    def cancel(self, signum, frame):
        self.cancelled = True
        if not self.creating and self.child is not None and self.child.poll() is None:
            try:
                self.child.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass

    def run(self, command, input_fd, finish_creation=False):
        if self.cancelled and not finish_creation:
            return 1
        cleanup = False
        previous_cleaned = self.cleaned
        self.cleaned = False
        self.creating = finish_creation
        try:
            self.child = subprocess.Popen(command, pass_fds=(input_fd,), stdin=subprocess.DEVNULL,
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          text=True, errors="replace", bufsize=1,
                                          env=dict(os.environ, PYTHONUNBUFFERED="1"))
        except OSError:
            self.cleaned = previous_cleaned
            self.creating = False
            raise
        if self.cancelled:
            self.cancel(signal.SIGTERM, None)
        try:
            for line in self.child.stdout:
                line = line.rstrip("\r\n")
                if line.startswith("oem_runtime_cleanup "):
                    cleanup = line == CLEANUP
                if line.startswith(FORWARD):
                    print(line, flush=True)
            result = self.child.wait()
        finally:
            # A canceled operation must finish its existing runtime cleanup.
            # Never terminate early and pretend that the sensor/listener is idle.
            if self.child.poll() is None:
                self.cancel(signal.SIGTERM, None)
                for line in self.child.stdout:
                    if line.rstrip("\r\n").startswith("oem_runtime_cleanup "):
                        cleanup = line.rstrip("\r\n") == CLEANUP
                self.child.wait()
            self.child.stdout.close()
            self.child = None
            self.creating = False
            self.cleaned = previous_cleaned and cleanup
        # Creating a Gatekeeper identity is a short persistent transaction.
        # Finish a started create so the caller can validate/publish its handle;
        # remember cancellation and never proceed to finger acquisition.
        return result if finish_creation or not self.cancelled else 1


def enrol(username, output, runtime):
    user = pwd.getpwnam(username)
    if user.pw_name != username or not 0 < user.pw_uid < 0x10000000:
        raise ValueError("unsupported target account")
    here = Path(__file__).resolve().parent
    for path in (here, here / "native_credentials.py", here / "CANDIDATE.json"):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or info.st_mode & 0o022:
            raise ValueError("untrusted program bundle")
    candidate = json.loads((here / "CANDIDATE.json").read_text())
    profile = candidate.get("account_profile", "bound-account")
    if profile == "primary-uid-1000":
        # Resolve only on authorized enrollment, after initial setup created the
        # local account. Persistent credential binding below remains unchanged.
        primary = pwd.getpwuid(1000)
        if (candidate.get("linux_uid") != 1000 or user.pw_uid != 1000 or
                primary.pw_name != username):
            raise ValueError("only the primary local account is supported")
    elif profile == "bound-account":
        if (candidate.get("linux_username"), candidate.get("linux_uid")) != (username, user.pw_uid):
            raise ValueError("target differs from the installed account")
    else:
        raise ValueError("unsupported account profile")
    prints = FPRINT_ROOT / username / "fpc1264_oem" / "liuqin-fpc1264-oem"
    if any(os.path.lexists(prints / finger) for finger in "123456789a"):
        print("native_enrol=REFUSED existing_template=1", flush=True)
        return 2
    if not output.is_absolute() or output.parent.resolve() != output.parent or os.path.lexists(output):
        raise ValueError("output must be a new private absolute path")
    parent = output.parent.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or
            (parent.st_uid, parent.st_gid, stat.S_IMODE(parent.st_mode)) != (0, 0, 0o700)):
        raise ValueError("untrusted output directory")
    uid = 0x60000000 | user.pw_uid
    private_directory(STATE_ROOT)
    account = STATE_ROOT / str(uid)
    private_directory(account)
    lock = os.open(account / "user-operation.lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    secret_fd = -1
    secret = bytearray()
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        record_path = account / "credential.json"
        handle = account / "gatekeeper.handle"
        requested = account / "create.requested"
        if os.path.lexists(requested):
            raise ValueError("unfinished credential creation; preserve state")
        exists = (os.path.lexists(record_path), os.path.lexists(handle))
        if exists not in ((False, False), (True, True)):
            raise ValueError("incomplete credential state; preserve state")
        binding = {"schema": 1, "linux_username": username, "linux_uid": user.pw_uid,
                   "native_uid": uid, "credential_kind": "random-polkit"}
        if not exists[0]:
            if runtime.cancelled:
                return 1
            secret = bytearray(os.urandom(32))
            write_exclusive(record_path, (json.dumps(dict(binding, secret_hex=secret.hex()),
                                                     sort_keys=True) + "\n").encode())
            # Durable before any Gatekeeper call, including an interrupted create.
            write_exclusive(requested, b"creation requested\n")
        else:
            record = json.loads(private_read(record_path, 4096))
            encoded = record.pop("secret_hex")
            if record != binding:
                raise ValueError("credential belongs to another account")
            secret = bytearray.fromhex(encoded)
            private_read(handle, 1024)
        if len(secret) != 32:
            raise ValueError("invalid machine credential")
        secret_fd = os.memfd_create("liuqin-native-enrol-input", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        os.fchmod(secret_fd, 0o600)
        if os.write(secret_fd, secret) != 32:
            raise OSError("incomplete credential input")
        secret[:] = bytes(len(secret))
        fcntl.fcntl(secret_fd, fcntl.F_ADD_SEALS,
                    fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        command = [sys.executable, "-u", str(here / "native_credentials.py"), "--state-dir", str(STATE_ROOT)]
        if not exists[0]:
            os.lseek(secret_fd, 0, os.SEEK_SET)
            status = runtime.run([*command, "--create", str(uid), str(secret_fd)], secret_fd,
                                 finish_creation=True)
            if status or not runtime.cleaned:
                print(f"native_enrol_runtime=FAILED operation=create status={status} "
                      f"cleanup_complete={int(runtime.cleaned)}", file=sys.stderr, flush=True)
                return 1
            private_read(handle, 1024)
            requested.unlink()
            sync_directory(account)
        if runtime.cancelled:
            return 1
        # The existing FPC exporter uses O_EXCL and checks exactly one template.
        # Keep its output private until the final enrol runtime has fully cleaned.
        with tempfile.TemporaryDirectory(prefix=".native-enrol-", dir=output.parent) as temporary:
            database = Path(temporary) / "database"
            os.lseek(secret_fd, 0, os.SEEK_SET)
            status = runtime.run([*command, "--enrol", str(database), str(uid), str(secret_fd)], secret_fd)
            if status or not runtime.cleaned or runtime.cancelled:
                print(f"native_enrol_runtime=FAILED operation=enrol status={status} "
                      f"cleanup_complete={int(runtime.cleaned)}", file=sys.stderr, flush=True)
                return 1
            source_fd = os.open(database, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                info = os.fstat(source_fd)
                if (not stat.S_ISREG(info.st_mode) or
                        (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o600) or
                        not 0 < info.st_size <= 16 * 1024 * 1024):
                    raise ValueError("incomplete private database")
                target_fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
                try:
                    with os.fdopen(source_fd, "rb", closefd=False) as source, os.fdopen(target_fd, "wb", closefd=False) as target:
                        shutil.copyfileobj(source, target)
                        target.flush()
                    os.fsync(target_fd)
                    if runtime.cancelled:
                        raise InterruptedError("enrolment canceled")
                except BaseException:
                    output.unlink()
                    raise
                finally:
                    os.close(target_fd)
            finally:
                os.close(source_fd)
            sync_directory(output.parent)
        return 0
    finally:
        secret[:] = bytes(len(secret))
        if secret_fd >= 0:
            os.close(secret_fd)
        os.close(lock)


def main(arguments=None):
    arguments = sys.argv[1:] if arguments is None else arguments
    runtime = Runtime()
    result = 2
    handlers = {}
    try:
        if os.geteuid() != 0 or os.getegid() != 0 or len(arguments) != 3 or arguments[0] != "--enrol":
            print("usage (root): native_enrol.py --enrol USERNAME OUTPUT_DATABASE", file=sys.stderr)
            return 2
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        os.umask(0o077)
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            handlers[sig] = signal.signal(sig, runtime.cancel)
        result = enrol(arguments[1], Path(arguments[2]), runtime)
    except (OSError, ValueError, KeyError, TypeError) as error:
        code = error.errno if isinstance(error, OSError) and isinstance(error.errno, int) else "none"
        print("native_enrol=FAILED phase=private_state_or_runtime existing_identities_preserved=1 "
              f"exception={type(error).__name__} errno={code}", file=sys.stderr)
        result = 1
    finally:
        if runtime.cleaned and runtime.child is None:
            print("native_enrol_cleanup=OK", flush=True)
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    if runtime.cancelled:
        print("native_enrol=CANCELLED", flush=True)
        return 1
    if result == 0 and runtime.cleaned:
        print("native_enrol=OK single_finger_database=1", flush=True)
    return result


if __name__ == "__main__":
    sys.exit(main())
