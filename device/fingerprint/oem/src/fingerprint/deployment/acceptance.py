#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""The final physical acceptance entry, retaining normal password login."""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import signal
import subprocess
import sys

FINGERS = ("left-thumb", "left-index-finger", "left-middle-finger", "left-ring-finger",
           "left-little-finger", "right-thumb", "right-index-finger", "right-middle-finger",
           "right-ring-finger", "right-little-finger")

cancelled = False
child = None

def cancel(signum, frame):
    global cancelled
    cancelled = True
    if child is not None and child.poll() is None:
        child.send_signal(signal.SIGTERM)

def run(*command):
    global child
    cleanup = command[:2] in (("systemctl", "stop"), ("systemctl", "mask"))
    if cancelled and not cleanup:
        raise RuntimeError("Fingerprint acceptance cancelled")
    enrolling = len(command) > 1 and Path(command[1]).name == "enrol_publish.py"
    child = subprocess.Popen(list(command),
        stdout=subprocess.PIPE if enrolling else None,
        stderr=subprocess.STDOUT if enrolling else None,
        text=True, bufsize=1, env={**os.environ, "PYTHONUNBUFFERED": "1"})
    try:
        if enrolling:
            fd = os.open("/var/lib/liuqin-fingerprint/last-acceptance.log",
                         os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w", buffering=1) as diagnostic:
                diagnostic.write("\nacceptance_started_utc=" + datetime.datetime.now(datetime.timezone.utc).isoformat() + "\n")
                for line in child.stdout:
                    diagnostic.write(line)
                    attempt = re.match(r"READY enrol attempt=(\d+):", line)
                    progress = re.match(r"enrol_progress_status=(-?\d+) remaining=(\d+)", line)
                    if attempt:
                        print("第 " + attempt[1] + " 次：请按住同一根手指。", flush=True)
                    elif line.startswith("READY lift finger"):
                        print("请抬起手指，调整接触位置后再按。", flush=True)
                    elif progress:
                        print("录入剩余：" + progress[2], flush=True)
                    elif line.startswith("capture_rejected="):
                        print("本次接触未接受，请抬起后调整位置。", flush=True)
                    elif line.startswith("enrol_authorization_phase=end_enrol"):
                        print("采样完成，正在授权并保存模板。", flush=True)
                    elif line.startswith(("pipeline_incomplete=", "end_enrol_not_submitted=")):
                        print(line.strip(), flush=True)
        while True:
            try:
                result = child.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                if cancelled and not cleanup:
                    child.send_signal(signal.SIGTERM)
        if result:
            if enrolling:
                print("录入未完成，诊断信息已保留。", file=sys.stderr, flush=True)
            raise subprocess.CalledProcessError(result, list(command))
        if cancelled and not cleanup:
            raise RuntimeError("Fingerprint acceptance cancelled after child cleanup")
    finally:
        if child.poll() is None:
            child.send_signal(signal.SIGTERM)
            child.wait()
        if child.stdout is not None:
            child.stdout.close()
        child = None

def idle():
    power = Path("/sys/bus/spi/devices/spi1.0/power_state").read_text().strip()
    firmware = Path("/sys/module/firmware_class/parameters/path").read_bytes() == b"\n"
    lock = os.open("/run/liuqin-fpc-oem-runtime/operation.lock", os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(lock)
    if power != "off" or not firmware:
        raise RuntimeError("Fingerprint cleanup has not completed")
    return {"sensor_power": power, "firmware_path_empty": firmware, "operation_lock_free": True}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--finger", choices=FINGERS, default="right-index-finger")
    options = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Use the installed acceptance launcher or sudo")
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):
        signal.signal(signum, cancel)
    here = Path(__file__).resolve().parent
    candidate = json.loads((here / "CANDIDATE.json").read_text())
    user = candidate["linux_username"]
    account = pwd.getpwnam(user)
    caller = os.environ.get("PKEXEC_UID", os.environ.get("SUDO_UID"))
    if caller is not None and int(caller) not in (0, account.pw_uid):
        raise RuntimeError("This candidate is scoped to its verified local Linux account")
    manifest = json.loads((here / "SHA256.json").read_text())
    if any(hashlib.sha256((here/name).read_bytes()).hexdigest() != digest for name,digest in manifest.items()):
        raise RuntimeError("Installed public program bundle has drifted")
    if options.recover:
        run("systemctl", "stop", "liuqin-fpc-acceptance-fprintd.service")
        run("systemctl", "mask", "--now", "fprintd.service")
        from acceptance_input import discard
        discard(user)
        print("恢复完成：指纹登录关闭，密码登录保留；稳定模块无需重绑。")
        return 0
    from stable_module import load
    load()
    store = Path("/var/lib/fprint") / user / "fpc1264_oem/liuqin-fpc1264-oem"
    prints = [p for p in store.iterdir() if p.name in tuple(format(n,"x") for n in range(1,11))] if store.exists() else []
    if len(prints) > 1:
        raise RuntimeError("The current candidate supports one fingerprint per Linux user")
    if options.status:
        print(json.dumps({"candidate": candidate["id"], "user": user, "published_oem_prints": len(prints),
                          "physical_acceptance_pending": not (Path("/var/lib/liuqin-fingerprint/acceptance.json").exists()),
                          "idle": idle()}, ensure_ascii=False))
        return 0
    if not prints:
        if options.verify:
            raise RuntimeError("No accepted OEM template is available; run the normal acceptance entry")
        print("请正常按放同一根手指，按提示调整接触位置，直到录入完成。", flush=True)
        prepared = Path("/run/liuqin-fpc-oem-runtime") / ("acceptance-" + user + ".json")
        if prepared.exists():
            import time
            metadata = json.loads(prepared.read_text())
            if metadata.get("expires",0) <= time.time():
                from acceptance_input import discard
                discard(user)
        mode = "--enrol-prepared" if prepared.exists() else "--enrol"
        run(sys.executable, str(here / "enrol_publish.py"), mode, user, options.finger)
        prints = [store / format(FINGERS.index(options.finger) + 1, "x")]
    finger = FINGERS[int(prints[0].name,16) - 1]
    from verify_once import verify
    daemon = "liuqin-fpc-acceptance-fprintd.service"
    results = []
    try:
        run("systemctl", "start", daemon)
        if options.verify:
            print("请触摸传感器进行一次真实验证。", flush=True)
            print("验证结果：" + verify(user, finger))
            return 0
        input("请先抬起手指。按回车开始同指验证，再触摸刚才录入的手指。")
        results.append(verify(user, finger, "verify-match"))
        idle()
        # Recreate fprintd to prove the persisted database is loaded afresh.
        run("systemctl", "restart", daemon)
        input("模板已从磁盘重新加载。抬起手指并按回车，再用同一根手指触摸。")
        results.append(verify(user, finger, "verify-match"))
        idle()
        input("请抬起并换一根没有录入的手指。按回车开始异指验证，然后触摸。")
        results.append(verify(user, finger, "verify-no-match"))
        idle()
    finally:
        run("systemctl", "stop", daemon)
    root = Path("/var/lib/liuqin-fingerprint")
    record = {"schema": 1, "candidate": candidate["id"], "linux_username": user,
              "time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
              "same_finger": results[0], "reloaded_same_finger": results[1], "other_finger": results[2],
              "password_login_preserved": True, "real_desktop_unlock": "pending", "idle": idle()}
    temporary = root / ".acceptance.json.new"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, (json.dumps(record,indent=2)+"\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, root / "acceptance.json")
    run("systemctl", "unmask", "fprintd.service")
    run("systemctl", "daemon-reload")
    run("systemctl", "start", "fprintd.service")
    print("真实录入、保存重载、同指接受和异指拒绝均通过；桌面指纹服务已启用。")
    print("密码登录保留。最后请在桌面锁屏后验证一次实际解锁，确认屏幕与旋转正常。")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.CalledProcessError) as error:
        print("指纹验收停止：" + str(error), file=sys.stderr)
        raise SystemExit(1)
