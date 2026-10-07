#!/usr/bin/python3
"""Synthetic TOD process-protocol fixture. No sensor, TEE or credential access."""
import os
from pathlib import Path
import signal
import sys
import time

mode = os.environ["FPC_TEST_CASE"]
assert sys.argv[1:3] == ["--enrol", "fixture-user"]
target = Path(sys.argv[3])
stopping = False


def cancel(signum, frame):
    global stopping
    stopping = True


signal.signal(signal.SIGTERM, cancel)
if mode == "stderr":
    # Exceed a pipe buffer without spending the emulated API test on thousands
    # of otherwise identical callback dispatches.
    for _ in range(1000):
        print("synthetic ignored diagnostic " + "x" * 128, file=sys.stderr, flush=True)
print("READY enrol attempt=1: press same finger and hold", flush=True)
print("READY finger_irq_armed=down press finger now", flush=True)
remaining = 0 if mode == "zero" else 25 if mode == "twenty-six" else 3
print(f"enrol_progress_status=1 remaining={remaining}", flush=True)
if mode == "diagnostics":
    print("unlisted-payload-must-not-enter-journal", flush=True)
    for line in (
        "native_enrol_runtime=FAILED operation=enrol status=1",
        "native_enrol=FAILED phase=fixture errno=5",
        "pipeline_incomplete=finger_down_wait",
        "end_enrol_not_submitted=1 fresh_authorization_failed=1",
        "enrol_authorization=TA_rejected",
    ):
        print(line, flush=True)
    print("native_enrol_cleanup=OK", flush=True)
    raise SystemExit(1)
if mode.startswith("cancel"):
    while not stopping:
        time.sleep(0.005)
    # Intermediate runtime cleanup is not final provider-wide cleanup.
    print("oem_runtime_cleanup firmware_path_restored=1 sensor_power_off=1 listener_stopped=1", flush=True)
    time.sleep(0.2)
    if mode != "cancel-no-cleanup":
        print("native_enrol_cleanup=OK", flush=True)
    raise SystemExit(1)
if remaining:
    print("READY lift finger and reposition for next sample", flush=True)
    print("READY enrol attempt=2: press same finger and hold", flush=True)
    print("READY finger_irq_armed=down press finger now", flush=True)
    print("enrol_progress_status=1 remaining=2", flush=True)
    if mode != "normal-lift":
        print("enrol_progress_status=7 remaining=2", flush=True)
        print("capture_rejected=4 enrol_not_sent=1", flush=True)
    print("READY lift finger and reposition for next sample", flush=True)
    print("enrol_progress_status=1 remaining=1", flush=True)
if mode == "existing":
    print("native_enrol_cleanup=OK", flush=True)
    print("native_enrol=REFUSED existing_template=1", flush=True)
    raise SystemExit(2)
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
os.write(fd, b"synthetic opaque database")
os.close(fd)
if mode == "permissions":
    target.chmod(0o644)
if mode == "early-cleanup":
    print("oem_runtime_cleanup firmware_path_restored=1 sensor_power_off=1 listener_stopped=1", flush=True)
else:
    print("native_enrol_cleanup=OK", flush=True)
print("native_enrol=OK single_finger_database=1", flush=True)
if mode == "wait-exit":
    os.close(1)
    os.close(2)
    time.sleep(0.2)
