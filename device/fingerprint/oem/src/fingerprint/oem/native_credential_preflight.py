#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Validate native Gatekeeper -> signed HAT -> FPC without acquiring a finger.

The credential belongs to the isolated native Linux probe UID, not an Android
account. A random machine input proves protocol plumbing, not user identity.
It is generated inside this process and discarded after this single preflight.
The caller provides the running FPC module, OEM firmware and listener bridge.
"""
import os
from pathlib import Path
import subprocess
import sys

NATIVE_UID = 0x400003E8


def credential_pipe(secret):
    reading, writing = os.pipe()
    try:
        os.write(writing, secret)
    finally:
        os.close(writing)
    return reading


def main():
    if sys.argv[1:] != ["--preflight"] or os.geteuid() != 0:
        print("usage (root): native_credential_preflight.py --preflight", file=sys.stderr)
        return 2
    here = Path(__file__).resolve().parent
    gatekeeper = here / "qsee-gatekeeper"
    authorization = here / "authorize_oem.py"
    if not os.access(gatekeeper, os.X_OK) or not authorization.is_file():
        print("native credential and authorization clients must be colocated", file=sys.stderr)
        return 2

    secret = bytearray(os.urandom(32))
    handle_fd = os.memfd_create("native-gatekeeper-handle", os.MFD_CLOEXEC)
    input_fd = -1
    try:
        input_fd = credential_pipe(secret)
        creation = subprocess.run(
            ["timeout", "--kill-after=5s", "20s", str(gatekeeper), "--new-credential",
             str(NATIVE_UID), str(input_fd), str(handle_fd)],
            pass_fds=(input_fd, handle_fd))
        os.close(input_fd)
        input_fd = -1
        if creation.returncode:
            print("native_credential_preflight=FAILED phase=credential_creation", file=sys.stderr)
            return 1
        os.lseek(handle_fd, 0, os.SEEK_SET)
        input_fd = credential_pipe(secret)
        authorization_result = subprocess.run(
            ["timeout", "--kill-after=5s", "40s", sys.executable, str(authorization),
             "--preflight", str(NATIVE_UID), str(handle_fd), str(input_fd)],
            pass_fds=(handle_fd, input_fd))
        if authorization_result.returncode:
            print("native_credential_preflight=FAILED phase=authorization", file=sys.stderr)
            return 1
        print("native_credential_preflight=OK machine_input_only=1 user_identity_not_verified=1 "
              "touch_not_collected=1 template_not_created=1")
        return 0
    finally:
        if input_fd >= 0:
            os.close(input_fd)
        secret[:] = b"\x00" * len(secret)
        size = os.fstat(handle_fd).st_size
        os.lseek(handle_fd, 0, os.SEEK_SET)
        if size:
            os.write(handle_fd, b"\x00" * size)
        os.close(handle_fd)


if __name__ == "__main__":
    sys.exit(main())
