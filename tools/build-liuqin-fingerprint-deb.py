#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Stage the verified FPC userspace for an offline, first-account installation.

Usage: build-liuqin-fingerprint-deb.py --bundle fingerprint-build-inputs.tar.gz --out DIR --version 0.6.0
Never runs systemctl, mount, a TA, or target binaries on the build host. Existing
machine enrollment and template state are not inputs and are not packaged.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
OEM = ROOT / "device/fingerprint/oem"
DEPLOYMENT = OEM / "src/fingerprint/deployment"
# Public v0.6.0 release attachment, produced by make-fingerprint-build-inputs.py.
# The original private host archive is not a consumer/build dependency.
BUNDLE_SHA256 = "7af0e818fe27fcd569959d5f45568aa0e34f19bae78ad40d7a32170b8c8c8852"
VERIFIED_COMPONENTS = {
    "fprintd": "463c8b79c99758a610ed7a9b76f22cbaab808e48184f255ecd28bc30e04f25cd",
    "tod/libfprint-tod-fpc1264-oem.so": "e128383ae639890f6020ca41e3dbcf5ba23e781de432c22edf26f9e00805a0dd",
    "kernel/fpc1264_spi_diag.ko": "f2580d173c9b54dc8f55357b98fb5833d1be3ef4e8d9f9eda00c2176d2fd0db8",
}
KERNEL_RELEASE = "6.17.0-rc1-gfe81794b5e1b"
RUNTIME_PATH = "usr/local/lib/liuqin-fpc-oem"
FIRMWARE_NAMES = {"fpcliu.mdt", *(f"fpcliu.b{i:02d}" for i in range(9))}
BINARIES = {"fprintd", "fpc_build_info", "qsee-gatekeeper", "qsee-supplicant",
            "qsee-app-loader", "pam-input", "fpc-oem-print",
            "ufs-rpmb-provider/liuqin-rpmb-supplicant"}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_bundle(bundle, destination):
    if sha(bundle) != BUNDLE_SHA256:
        raise ValueError("input is not the public v0.6.0 fingerprint-build-inputs.tar.gz")
    with tarfile.open(bundle, "r:gz") as archive:
        seen = set()
        for item in archive:
            name = PurePosixPath(item.name)
            if (not item.isfile() or name.is_absolute() or ".." in name.parts or
                    name.as_posix() != item.name or item.name in seen):
                raise ValueError("unsafe or duplicate bundle member")
            seen.add(item.name)
            target = destination / item.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(item) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
    manifest = json.loads((destination / "SHA256.json").read_text())
    if seen != set(manifest) | {"SHA256.json", "SHA256SUMS"}:
        raise ValueError("bundle manifest closure mismatch")
    if any(sha(destination / name) != digest for name, digest in manifest.items()):
        raise ValueError("bundle manifest hash mismatch")


def prepare_payload(payload, version):
    info = json.loads((payload / "CANDIDATE.json").read_text())
    if (info["kernel_release"] != KERNEL_RELEASE or
            info["enrollment_interface"] != "fprintd" or info["deployment_mode"] != "installed"):
        raise ValueError("release requires the verified FE kernel/native userspace")
    if any(sha(payload / name) != digest for name, digest in VERIFIED_COMPONENTS.items()):
        raise ValueError("public input differs from the validated native components")
    module = payload / "kernel/fpc1264_spi_diag.ko"
    if sha(module) != info["kernel_module_sha256"]:
        raise ValueError("module hash mismatch")
    modinfo = shutil.which("modinfo") or "/usr/sbin/modinfo"
    vermagic = subprocess.check_output([modinfo, "-F", "vermagic", str(module)], text=True)
    if vermagic.split()[0] != KERNEL_RELEASE:
        raise ValueError("module ABI differs from release kernel")
    expected = json.loads((OEM / "firmware/SHA256.json").read_text())
    actual = json.loads((payload / "firmware/SHA256.json").read_text())
    if actual != expected or set(expected) != FIRMWARE_NAMES:
        raise ValueError("OEM TA hashes differ from the reviewed stock firmware")
    if any((payload / "firmware" / name).exists() for name in FIRMWARE_NAMES):
        raise ValueError("public build inputs must not distribute OEM TA bytes")
    shutil.copyfile(OEM / "firmware/NOTICE", payload / "firmware/NOTICE")
    # Overlay current Python integration only; validated C binaries and their
    # matching C/source archives remain byte-for-byte from the reviewed bundle.
    for path in (OEM / "src/fingerprint/oem").glob("*.py"):
        shutil.copyfile(path, payload / path.name)
        shutil.copyfile(path, payload / "source" / path.name)
    for path in DEPLOYMENT.iterdir():
        if path.is_file():
            shutil.copyfile(path, payload / "deployment" / path.name)
            if path.suffix == ".py":
                shutil.copyfile(path, payload / path.name)
    dropin = OEM / "src/fingerprint/libfprint-tod/fprintd-fpc1264-oem.conf"
    for name in ("fprintd-fpc1264-oem.conf", "deployment/fprintd-fpc1264-oem.conf"):
        shutil.copyfile(dropin, payload / name)
    shutil.copyfile(Path(__file__), payload / "source/build-liuqin-fingerprint-deb.py")
    shutil.copyfile(OEM / "README.md", payload / "README.md")
    # Whitelist release metadata: never carry the developer account or its
    # previous test boot/recovery identities into a fresh-user installation.
    candidate = {key: info[key] for key in (
        "schema", "id", "kernel_release", "base_kernel", "kernel_source_diff_sha256",
        "target_libfprint_package", "build_kind", "deployment_mode", "enrollment_interface",
        "native_user_setup", "credential_creation", "program_path",
        "supports_one_finger_per_user", "partition_writes", "kernel_module_sha256")}
    candidate.update(account_profile="primary-uid-1000", linux_uid=1000,
                     native_uid=0x60000000 | 1000, firmware_source="modem_a-read-only",
                     release_version=version, binary_bundle_sha256=BUNDLE_SHA256,
                     binary_revision=info["revision"])
    (payload / "CANDIDATE.json").write_text(json.dumps(candidate, indent=2) + "\n")
    manifest = {path.relative_to(payload).as_posix(): sha(path)
                for path in sorted(payload.rglob("*")) if path.is_file()
                and path.name not in ("SHA256SUMS",) and path != payload / "SHA256.json"}
    (payload / "SHA256.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (payload / "SHA256SUMS").write_text("".join(f"{value}  {name}\n" for name, value in manifest.items()))
    for path in (payload, *payload.rglob("*")):
        name = path.relative_to(payload).as_posix()
        path.chmod(0o700 if path.is_dir() or name in BINARIES or path.suffix == ".py" else 0o600)
    return candidate, manifest


def stage_root(payload, root, info):
    """Pure offline staging: does not resolve an account or contact systemd."""
    destination = root / RUNTIME_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(payload, destination)
    spec = importlib.util.spec_from_file_location("fingerprint_stable_module", DEPLOYMENT / "stable_module.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    unit = module.render_module_unit((DEPLOYMENT / "liuqin-fpc-stable-module.service").read_text(), info)
    files = {
        "usr/lib/systemd/system/liuqin-fpc-stable-module.service": unit,
        "etc/systemd/system/fprintd.service.d/60-liuqin-fpc-oem.conf":
            (payload / "deployment/fprintd-fpc1264-oem.conf").read_text(),
        "etc/modprobe.d/liuqin-fpc-late-load.conf": "blacklist fpc1264_spi_diag\n",
        "etc/polkit-1/rules.d/60-liuqin-fingerprint-enroll.rules":
            (DEPLOYMENT / "60-liuqin-fingerprint-enroll.rules").read_text(),
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o644)
    enable = root / "etc/systemd/system/multi-user.target.wants/liuqin-fpc-stable-module.service"
    enable.parent.mkdir(parents=True, exist_ok=True)
    enable.symlink_to("/usr/lib/systemd/system/liuqin-fpc-stable-module.service")


def build(bundle, out, version):
    if not re.fullmatch(r"[0-9][0-9A-Za-z.+~_-]*", version):
        raise ValueError("invalid package version")
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"liuqin-fingerprint_{version}_arm64.deb"
    report_path = target.with_suffix(".json")
    if target.exists() or report_path.exists():
        raise ValueError("preserve existing output; choose a fresh directory/version")
    with tempfile.TemporaryDirectory(prefix="liuqin-fingerprint-deb-") as temporary:
        work = Path(temporary)
        payload, root = work / "payload", work / "root"
        payload.mkdir()
        read_bundle(bundle, payload)
        info, manifest = prepare_payload(payload, version)
        stage_root(payload, root, info)
        control = root / "DEBIAN"
        control.mkdir()
        abi = info["target_libfprint_package"]
        dependencies = [f"liuqin-kernel (= {version})", "python3", "python3-gi",
            f"libfprint-2-2 (= {abi})", f"libfprint-2-tod1 (= {abi})",
            "fprintd (>= 1.94.5)", "libpam-fprintd (>= 1.94.5)", "libc6 (>= 2.34)",
            "libglib2.0-0t64", "libpolkit-gobject-1-0", "libpam0g", "libpam-modules",
            "libssl3t64", "libkeyutils1", "systemd", "udev", "kmod", "mount", "polkitd", "dbus"]
        (control / "control").write_text(
            f"Package: liuqin-fingerprint\nVersion: {version}\nArchitecture: arm64\n"
            "Maintainer: yzddmr6 <46088090+yzddmr6@users.noreply.github.com>\n"
            "Section: admin\nPriority: optional\nDepends: " + ", ".join(dependencies) + "\n"
            "Description: Xiaomi Pad 6 Pro native single-finger authentication\n"
            " GNOME Settings enrollment and GDM authentication for the primary local\n"
            " UID 1000 account, with the matching FE kernel. OEM trusted applications\n"
            " are SHA-verified from this tablet's modem_a at first boot, not bundled.\n")
        (control / "conffiles").write_text("".join("/" + p.relative_to(root).as_posix() + "\n"
            for p in sorted((root / "etc").rglob("*")) if p.is_file() and not p.is_symlink()))
        # Stable archive metadata; SOURCE_DATE_EPOCH is honored when supplied.
        epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "1791331200"))
        for path in (root, *root.rglob("*")):
            os.utime(path, (epoch, epoch), follow_symlinks=False)
        intermediate = work / target.name
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(root), str(intermediate)],
                       check=True, env=dict(os.environ, SOURCE_DATE_EPOCH=str(epoch)))
        # Publish only the complete frozen output, with no overwrite window.
        with target.open("xb") as output, intermediate.open("rb") as source:
            shutil.copyfileobj(source, output)
        report = {"package": target.name, "sha256": sha(target), "candidate": info,
                  "input_bundle_sha256": BUNDLE_SHA256, "public_programs": len(manifest),
                  "oem_firmware_distributed": False, "hashes": manifest}
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({key: report[key] for key in ("package", "sha256", "public_programs")}))
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    build(args.bundle, args.out, args.version)


if __name__ == "__main__":
    main()
