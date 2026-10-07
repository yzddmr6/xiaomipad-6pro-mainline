#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Connect a fresh FPC challenge to OEM Gatekeeper over anonymous pipes.

Caller supplies open binary FDs for an existing handle and derived credential.
No credential file is opened here and no token is logged or stored. Enrolment
keeps the provider open for two challenges: before capture and before END_ENROL.
Deployment puts this script beside both clients. Use --preflight first.
"""
import os
import pathlib
import signal
import subprocess
import sys


def main():
    if len(sys.argv) == 5 and sys.argv[1] in ("--preflight", "--preflight-renew"):
        mode, uid, handle_fd, password_fd = sys.argv[1:]
        database = None
    elif len(sys.argv) == 6 and sys.argv[1] == "--enrol":
        mode, database, uid, handle_fd, password_fd = sys.argv[1:]
    elif len(sys.argv) == 7 and sys.argv[1] == "--enrol-existing":
        mode, existing_database, database, uid, handle_fd, password_fd = sys.argv[1:]
    else:
        print("usage: authorize_oem.py --preflight UID HANDLE_FD DERIVED_INPUT_FD | "
              "--preflight-renew UID HANDLE_FD DERIVED_INPUT_FD | "
              "--enrol DATABASE UID HANDLE_FD DERIVED_INPUT_FD | "
              "--enrol-existing INPUT_DATABASE OUTPUT_DATABASE UID HANDLE_FD DERIVED_INPUT_FD", file=sys.stderr)
        return 2

    try:
        uid = int(uid)
        handle_fd, password_fd = int(handle_fd), int(password_fd)
        if not 0 <= uid <= 0xFFFFFFFF or handle_fd < 0 or password_fd < 0:
            raise ValueError("invalid UID or FD")
        if handle_fd == password_fd:
            raise ValueError("credential FDs must be distinct")
        os.fstat(handle_fd)
        os.fstat(password_fd)
    except (ValueError, OSError):
        print("invalid UID or open credential FDs", file=sys.stderr)
        return 2

    here = pathlib.Path(__file__).resolve().parent
    fpc = here / "fpc_build_info"
    gatekeeper = here / "qsee-gatekeeper"
    # Validate both executables before either can open hardware.
    if not os.access(fpc, os.X_OK) or not os.access(gatekeeper, os.X_OK):
        print("place both clients beside authorize_oem.py before running", file=sys.stderr)
        return 2

    challenge_read, challenge_write = os.pipe()
    hat_read, hat_write = os.pipe()
    provider = None
    client = None
    cancelled = False
    def cancel_operation(signum, frame):
        nonlocal cancelled
        cancelled = True
        for process in (provider, client):
            if process is not None and process.poll() is None:
                process.send_signal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, cancel_operation)
    signal.signal(signal.SIGINT, cancel_operation)
    try:
        provider = subprocess.Popen(
            [str(gatekeeper), "--verify" if mode == "--preflight" else "--verify-enrol",
             str(uid), str(challenge_read),
             str(handle_fd), str(password_fd), str(hat_write)],
            pass_fds=(challenge_read, handle_fd, password_fd, hat_write))
        if mode in ("--preflight", "--preflight-renew"):
            fpc_args = ["--auth-preflight-renew" if mode == "--preflight-renew" else "--auth-preflight",
                        str(hat_read), str(challenge_write)]
        elif mode == "--enrol-existing":
            fpc_args = ["--bio-pipeline-existing", existing_database, database,
                        str(hat_read), str(challenge_write)]
        else:
            fpc_args = ["--bio-pipeline", database, str(hat_read), str(challenge_write)]
        client = subprocess.Popen([str(fpc), *fpc_args],
                                  pass_fds=(hat_read, challenge_write))
    except OSError as error:
        print(f"client start failed: {error}", file=sys.stderr)
    finally:
        for fd in (challenge_read, challenge_write, hat_read, hat_write):
            os.close(fd)

    # Closing the unused pipe ends makes failure become EOF at the peer.
    # FPC owns sensor cleanup; no restart or extra acquisition is performed.
    if cancelled:
        cancel_operation(signal.SIGTERM, None)
    client_status = client.wait() if client is not None else 1
    provider_status = provider.wait() if provider is not None else 1
    return 0 if not cancelled and client_status == 0 and provider_status == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
