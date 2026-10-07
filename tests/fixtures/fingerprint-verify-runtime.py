#!/usr/bin/python3
"""Synthetic verify results; never opens a sensor or a real template."""
import os
import sys

mode = os.environ["FPC_VERIFY_TEST_CASE"]
status = 1
if mode == "matched":
    print("match_result=matched desktop_authentication=disabled")
    status = 0
elif mode == "not-matched":
    print("match_result=not_matched desktop_authentication=disabled")
    status = 3
elif mode == "identify-retry":
    print("match_result=inconclusive identify_app_status=4")
else:
    app_status = {"transport-error": -1, "unknown-status": 99}.get(mode, 1)
    print(f"match_incomplete=capture_rejected app_status={app_status}", file=sys.stderr)
if mode in ("timed-out", "cancelled"):
    print(f"oem_runtime_{'timed_out' if mode == 'timed-out' else 'cancelled'}=1", file=sys.stderr)
if mode == "tee-error-after-capture":
    print("tee_invoke_ret=1 origin=4", file=sys.stderr)
if mode != "missing-cleanup":
    print("oem_runtime_cleanup firmware_path_restored=1 sensor_power_off=1 listener_stopped=1")
sys.exit(status)
