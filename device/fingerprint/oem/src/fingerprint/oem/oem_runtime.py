#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run one explicit OEM operation with bundled firmware and a transient bridge."""
import hashlib
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


def main():
    arguments = sys.argv[1:]
    rpmb_mode = None
    if arguments and arguments[0] in ("--ufs-rpmb-read-only", "--ufs-rpmb-authenticated"):
        rpmb_mode = arguments.pop(0)
    commands = {
        "--keymaster-negotiate": ["qsee-gatekeeper", "--negotiate"],
        "--shared-hmac-probe": ["qsee-gatekeeper", "--shared-hmac-probe"],
        "--hw-auth-probe": ["fpc_build_info", "--hw-auth-probe"],
        "--native-auth-preflight": ["native_credential_preflight.py", "--preflight"],
        "--template-inventory": ["fpc_build_info", "--template-inventory"],
    }
    match = len(arguments) in (2, 3) and arguments[0] in ("--match-existing", "--match-single")
    create = len(arguments) == 4 and arguments[0] == "--new-native-credential"
    change = len(arguments) == 6 and arguments[0] == "--change-native-credential"
    enrol_existing = len(arguments) == 6 and arguments[0] == "--enrol-existing"
    enrol = enrol_existing or len(arguments) == 5 and arguments[0] == "--enrol"
    authorize = len(arguments) == 4 and arguments[0] in ("--auth-preflight", "--auth-preflight-renew")
    if os.geteuid() != 0 or not (match or create or change or enrol or authorize or len(arguments) == 1 and arguments[0] in commands):
        print("usage (root): oem_runtime.py [--ufs-rpmb-read-only | --ufs-rpmb-authenticated] --keymaster-negotiate | --shared-hmac-probe | --hw-auth-probe | --native-auth-preflight | --template-inventory | --match-existing DATABASE [UPDATED_DATABASE] | --match-single DATABASE [UPDATED_DATABASE] | --new-native-credential NATIVE_UID DERIVED_INPUT_FD HANDLE_OUTPUT_FD | --change-native-credential NATIVE_UID HANDLE_FD CURRENT_INPUT_FD DESIRED_INPUT_FD HANDLE_OUTPUT_FD | --auth-preflight NATIVE_UID HANDLE_FD DERIVED_INPUT_FD | --enrol DATABASE NATIVE_UID HANDLE_FD DERIVED_INPUT_FD | --enrol-existing INPUT_DATABASE OUTPUT_DATABASE NATIVE_UID HANDLE_FD DERIVED_INPUT_FD", file=sys.stderr)
        return 2
    credential_fds = ()
    if create or change or enrol or authorize:
        if rpmb_mode != "--ufs-rpmb-authenticated":
            print("native credential/authorization operations require the authenticated UFS listener", file=sys.stderr)
            return 2
        try:
            uid = int(arguments[3 if enrol_existing else 2 if enrol else 1], 10)
            credential_fds = tuple(int(value, 10) for value in arguments[-(4 if change else 2):])
            if not 0x40000000 <= uid <= 0x7fffffff or any(fd < 3 for fd in credential_fds) or len(set(credential_fds)) != len(credential_fds):
                raise ValueError("native UID or distinct binary FDs invalid")
            for index, fd in enumerate(credential_fds):
                os.fstat(fd)
                access = fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE
                writing = create and index == 1 or change and index == 3
                if (writing and access == os.O_RDONLY) or (not writing and access == os.O_WRONLY):
                    raise ValueError("credential FD access mode invalid")
        except (ValueError, OSError):
            print("supply a native UID and distinct open binary credential FDs with the required access modes", file=sys.stderr)
            return 2
    if not Path("/dev/fpc1020").exists():
        print("load the matching stable-base FPC module after desktop/SSH startup", file=sys.stderr)
        return 2
    here = Path(__file__).resolve().parent
    listener_command = [str(here / "qsee-supplicant")]
    expected_listeners = 2
    if rpmb_mode:
        provider = here / "ufs-rpmb-provider" / "liuqin-rpmb-supplicant"
        if not provider.is_file():
            print("the UFS RPMB provider is missing from this runtime bundle", file=sys.stderr)
            return 2
        listener_command = [str(provider), "--serve-authenticated" if
                            rpmb_mode == "--ufs-rpmb-authenticated" else "--serve-read-only"]
        expected_listeners = 3
    firmware = here / "firmware"
    expected = json.loads((firmware / "SHA256.json").read_text())
    for name, digest in expected.items():
        if hashlib.sha256((firmware / name).read_bytes()).hexdigest() != digest:
            print("bundled OEM firmware hash mismatch", file=sys.stderr)
            return 2
    state = Path("/run/liuqin-fpc-oem-runtime")
    state.mkdir(mode=0o700, exist_ok=True)
    os.chmod(state, 0o700)
    parameter = Path("/sys/module/firmware_class/parameters/path")
    if match:
        client_name = "fpc_build_info"
        client = [str(here / client_name), *arguments]
    elif create:
        client_name = "qsee-gatekeeper"
        client = [str(here / client_name), "--new-credential", *arguments[1:]]
    elif change:
        client_name = "qsee-gatekeeper"
        client = [str(here / client_name), "--change-credential", *arguments[1:]]
    elif enrol:
        client_name = "authorize_oem.py"
        client = [str(here / client_name), *arguments]
    elif authorize:
        client_name = "authorize_oem.py"
        client = [str(here / client_name), "--preflight-renew" if arguments[0] == "--auth-preflight-renew" else "--preflight", *arguments[1:]]
    else:
        client_name, client_arg = commands[arguments[0]]
        client = [str(here / client_name), client_arg]
    if client_name.endswith(".py"):
        client.insert(0, sys.executable)
    result = 1
    cancelled = False
    def cancel_operation(signum, frame):
        nonlocal cancelled
        cancelled = True
    signal.signal(signal.SIGTERM, cancel_operation)
    signal.signal(signal.SIGINT, cancel_operation)
    # All users share one SPI sensor, listener set and firmware path.
    with open(state / "operation.lock", "a+b") as operation, tempfile.TemporaryFile(mode="w+b", dir=state) as log:
        os.fchmod(operation.fileno(), 0o600)
        try:
            fcntl.flock(operation.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("oem_runtime=BUSY", file=sys.stderr)
            return 1
        # sysfs adds exactly one newline. Write NUL when restoring an empty value.
        old_value = parameter.read_bytes()[:-1]
        listener = subprocess.Popen([*listener_command, "--state-dir", str(state / "ta-files")],
                                    stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                if cancelled:
                    print("oem_runtime_cancelled=1", file=sys.stderr)
                    return 1
                if listener.poll() is not None:
                    print("OEM listener could not start", file=sys.stderr)
                    return 1
                log.seek(0)
                messages = log.read()
                if messages.count(b"event=listener_registered") >= expected_listeners and (
                        not rpmb_mode or b"event=liuqin_rpmb_listeners_ready" in messages):
                    break
                time.sleep(0.05)
            else:
                print("OEM listener readiness was not observed", file=sys.stderr)
                return 1
            parameter.write_bytes(os.fsencode(firmware) + b"\0")
            duration = 900 if enrol else 180 if match else 70
            # Only the explicit opaque credential FDs cross this boundary.
            # authorize_oem keeps FPC open while Gatekeeper signs its challenge.
            client_process = subprocess.Popen(client,
                                              pass_fds=credential_fds, start_new_session=True)
            client_deadline = time.monotonic() + duration
            while True:
                if cancelled or time.monotonic() >= client_deadline:
                    print("oem_runtime_cancelled=1" if cancelled else "oem_runtime_timed_out=1", file=sys.stderr)
                    try:
                        os.killpg(client_process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        client_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(client_process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        client_process.wait()
                    result = 1
                    break
                try:
                    result = client_process.wait(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            parameter.write_bytes(old_value + b"\0")
            if listener.poll() is None:
                listener.terminate()
                try:
                    listener.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    listener.kill()
                    listener.wait(timeout=3)
            log.seek(0)
            for line in log.read().splitlines():
                if line.startswith((b"event=listener_request ", b"event=rpmb_")):
                    print(line.decode("ascii"))
            restored = parameter.read_bytes() == old_value + b"\n"
            power_off = Path("/sys/bus/spi/devices/spi1.0/power_state").read_text().strip() == "off"
            print(f"oem_runtime_cleanup firmware_path_restored={int(restored)} "
                  f"sensor_power_off={int(power_off)} listener_stopped={int(listener.poll() is not None)}")
            if not restored or not power_off:
                result = 1
    return result


if __name__ == "__main__":
    sys.exit(main())
