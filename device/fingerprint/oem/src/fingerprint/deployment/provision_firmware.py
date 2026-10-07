#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Cache ten SHA-pinned FPC TA files from this tablet's read-only modem FAT.

No firmware bytes are distributed by liuqin-fingerprint. This runs once before
late module load, after the installer has repaired modem_a. Failure disables
fingerprint startup; it never repairs a partition or touches biometric state.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

NAMES = ("fpcliu.mdt", *(f"fpcliu.b{i:02d}" for i in range(9)))
MODEM = "/dev/disk/by-partlabel/modem_a"


def checked_hashes(directory):
    hashes = json.loads((directory / "SHA256.json").read_text())
    if set(hashes) != set(NAMES) or any(
            not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value)
            for value in hashes.values()):
        raise ValueError("invalid fixed FPC firmware manifest")
    return hashes


def matches(path, digest):
    if path.is_symlink():
        raise ValueError("refuse symlink in FPC firmware cache")
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest


def provision(directory):
    if os.geteuid() != 0:
        raise RuntimeError("root required")
    directory = Path(directory)
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or
            info.st_mode & 0o022):
        raise ValueError("untrusted FPC firmware cache")
    hashes = checked_hashes(directory)
    if all(matches(directory / name, hashes[name]) for name in NAMES):
        print("fpc_firmware=READY source=verified_cache")
        return
    mountpoint = Path(tempfile.mkdtemp(prefix="liuqin-fpc-modem-", dir="/run"))
    mounted = False
    try:
        subprocess.run(["mount", "-t", "vfat", "-o",
                        "ro,nodev,nosuid,noexec,codepage=437,iocharset=iso8859-1",
                        MODEM, str(mountpoint)], check=True, timeout=15)
        mounted = True
        with tempfile.TemporaryDirectory(prefix=".provision-", dir=directory) as staging:
            stage = Path(staging)
            for name in NAMES:
                source = mountpoint / "image" / name
                if not matches(source, hashes[name]):
                    raise RuntimeError("stock FPC firmware hash mismatch: " + name)
                target = stage / name
                # Recheck the exact copied bytes before publishing any member.
                target.write_bytes(source.read_bytes())
                target.chmod(0o600)
                if not matches(target, hashes[name]):
                    raise RuntimeError("copied FPC firmware hash mismatch: " + name)
            for name in NAMES:
                if (directory / name).is_symlink():
                    raise ValueError("refuse symlink in FPC firmware cache")
            for name in NAMES:
                os.replace(stage / name, directory / name)
    finally:
        # Never recursively remove a mountpoint. A failed umount is an error,
        # leaving the read-only mount intact for diagnosis, with no retry loop.
        if mounted:
            subprocess.run(["umount", str(mountpoint)], check=True, timeout=15)
        mountpoint.rmdir()
    print("fpc_firmware=READY source=local_modem_read_only")


if __name__ == "__main__":
    provision(Path(__file__).resolve().parent / "firmware")
