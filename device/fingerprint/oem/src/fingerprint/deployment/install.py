#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Install the audited bundle and expose its supported enrollment interface."""
import hashlib
import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess

def install():
    parser = argparse.ArgumentParser()
    parser.add_argument("--replace-owned-candidate", action="store_true")
    parser.add_argument("--defer-start", action="store_true",
                        help="Keep fprintd masked while staging a different support kernel")
    options = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("root required")
    source = Path(__file__).resolve().parent
    info = json.loads((source / "CANDIDATE.json").read_text())
    manifest = json.loads((source / "SHA256.json").read_text())
    for name, digest in manifest.items():
        path = source / name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError("Candidate program hash mismatch: " + name)
    user = pwd.getpwnam(info["linux_username"])
    if user.pw_uid != info["linux_uid"]:
        raise RuntimeError("Candidate account mismatch")
    interface = info.get("enrollment_interface", "development-helper")
    if interface not in ("development-helper", "fprintd"):
        raise RuntimeError("Unsupported fingerprint enrollment interface")
    native = interface == "fprintd"
    if native:
        if info.get("deployment_mode") != "installed":
            raise RuntimeError("Native enrollment requires installed startup mode")
        for name in ("native_enrol.py", "deployment/60-liuqin-fingerprint-enroll.rules"):
            if name not in manifest:
                raise RuntimeError("Native enrollment input missing: " + name)
        if not options.defer_start and os.uname().release != info["kernel_release"]:
            raise RuntimeError("Start the matching support kernel or stage with --defer-start")
    units = Path("/etc/systemd/system")
    subprocess.run(["systemctl", "stop", "fprintd.service"], check=True)
    if (units / "liuqin-fpc-acceptance-fprintd.service").exists():
        subprocess.run(["systemctl", "stop", "liuqin-fpc-acceptance-fprintd.service"], check=True)
    destination = Path("/usr/local/lib/liuqin-fpc-oem")
    previous = None
    if destination.exists():
        installed = destination / "SHA256.json"
        if not installed.exists() or json.loads(installed.read_text()) != manifest:
            if not options.replace_owned_candidate or not installed.exists() or json.loads((destination/"CANDIDATE.json").read_text()).get("id") != info["id"]:
                raise RuntimeError("Another formal candidate is present; preserve it for an explicit upgrade")
            for name,digest in json.loads(installed.read_text()).items():
                if hashlib.sha256((destination/name).read_bytes()).hexdigest() != digest:
                    raise RuntimeError("Previous candidate has unrelated edits; preserve them")
            import time
            previous = destination.with_name("liuqin-fpc-oem-previous-" + str(time.time_ns()))
            os.rename(destination, previous)
            try:
                shutil.copytree(source, destination)
            except BaseException:
                if not destination.exists():
                    os.rename(previous,destination)
                raise
    else:
        shutil.copytree(source, destination)
    for path in (destination, *destination.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("Private candidate contains a symlink")
        path.chmod(0o700 if path.is_dir() or os.access(path, os.X_OK) or path.suffix == ".py" else 0o600)
        os.chown(path, 0, 0)
    # pkexec resolves the entry as the caller before authorizing it. Only this
    # public entry is readable; the rest of the program bundle stays private.
    destination.chmod(0o711)
    (destination / "acceptance.py").chmod(0o755)
    shutil.copyfile(destination / "pam.d/liuqin-fpc-enrol", Path("/etc/pam.d/liuqin-fpc-enrol"))
    vendor = Path("/usr/lib/systemd/system/fprintd.service").read_text()
    dropin = (destination / "deployment/fprintd-fpc1264-oem.conf").read_text()
    candidate_unit = vendor + "\n" + dropin
    candidate_unit += "\n[Service]\nExecStart=\nExecStart=/usr/local/lib/liuqin-fpc-oem/fprintd --no-timeout\n"
    (units / "liuqin-fpc-acceptance-fprintd.service").write_text(candidate_unit)
    module_unit = (destination / "deployment/liuqin-fpc-stable-module.service").read_text()
    from stable_module import render_module_unit
    (units / "liuqin-fpc-stable-module.service").write_text(render_module_unit(module_unit, info))
    # udev must not load the module before the guarded late-load service.
    modprobe = Path("/etc/modprobe.d")
    modprobe.mkdir(exist_ok=True)
    (modprobe / "liuqin-fpc-late-load.conf").write_text("blacklist fpc1264_spi_diag\n")
    dropins = units / "fprintd.service.d"
    dropins.mkdir(exist_ok=True)
    shutil.copyfile(destination / "deployment/fprintd-fpc1264-oem.conf", dropins / "60-liuqin-fpc-oem.conf")
    policies = Path("/usr/share/polkit-1/actions")
    # Scope the password-free local launcher to this acceptance executable,
    # this account, and an active local session. It cannot authorize a shell.
    rules = Path("/etc/polkit-1/rules.d")
    rules.mkdir(exist_ok=True)
    acceptance_rule = (
        'polkit.addRule(function(action, subject) {\n'
        '  if (action.id == "org.liuqin.fingerprint.accept" && subject.user == ' + json.dumps(user.pw_name) +
        ' && subject.local && subject.active) return polkit.Result.YES;\n});\n')
    terminal_entry = Path("/usr/local/bin/liuqin-fingerprint-accept-terminal")
    desktop_entry = Path("/usr/share/applications/liuqin-fingerprint-accept.desktop")
    launcher = Path("/usr/local/bin/liuqin-fingerprint-accept")
    if launcher.exists() or launcher.is_symlink():
        if not launcher.is_symlink() or launcher.resolve() != destination / "acceptance.py":
            raise RuntimeError("Preserve unrelated acceptance entry")
    elif not native:
        launcher.symlink_to(destination / "acceptance.py")
    if native:
        # Remove only our unchanged development launcher and its scoped policy.
        for path, name in (
                (terminal_entry, "deployment/liuqin-fingerprint-accept-terminal"),
                (desktop_entry, "deployment/liuqin-fingerprint-accept.desktop"),
                (policies / "org.liuqin.fingerprint.policy", "deployment/org.liuqin.fingerprint.policy")):
            if path.exists() or path.is_symlink():
                if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != manifest[name]:
                    raise RuntimeError("Preserve modified development entry: " + str(path))
                path.unlink()
        old_rule = rules / "60-liuqin-fingerprint-accept.rules"
        if old_rule.exists():
            if old_rule.read_text() != acceptance_rule:
                raise RuntimeError("Preserve modified acceptance authorization")
            old_rule.unlink()
        if launcher.is_symlink():
            launcher.unlink()
        enrollment_rule = rules / "60-liuqin-fingerprint-enroll.rules"
        shutil.copyfile(destination / "deployment/60-liuqin-fingerprint-enroll.rules", enrollment_rule)
        enrollment_rule.chmod(0o644)
        os.chown(enrollment_rule, 0, 0)
    else:
        shutil.copyfile(destination / "deployment/org.liuqin.fingerprint.policy", policies / "org.liuqin.fingerprint.policy")
        (rules / "60-liuqin-fingerprint-accept.rules").write_text(acceptance_rule)
        shutil.copyfile(destination / "deployment/liuqin-fingerprint-accept.desktop", desktop_entry)
        shutil.copyfile(destination / "deployment/liuqin-fingerprint-accept-terminal", terminal_entry)
        terminal_entry.chmod(0o755)
        os.chown(terminal_entry, 0, 0)
    for command in (["systemctl", "mask", "--now", "fprintd.service"],
                    ["systemctl", "daemon-reload"],
                    ["systemctl", "enable", "liuqin-fpc-stable-module.service"]):
        subprocess.run(command, check=True)
    authentication = "masked"
    if native and not options.defer_start:
        # Recheck the exact live kernel, module and wiring before D-Bus activation.
        from stable_module import load
        load()
        subprocess.run(["systemctl", "unmask", "fprintd.service"], check=True)
        subprocess.run(["systemctl", "start", "fprintd.service"], check=True)
        authentication = "native_fprintd"
    print("formal_candidate=INSTALLED public_programs=" + str(len(manifest)) +
          " fingerprint_authentication=" + authentication +
          " password_login_preserved=1 partition_writes=0")

if __name__ == "__main__":
    install()
