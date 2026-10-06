#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Collect the current built candidate and exact public source provenance."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import shutil
import tarfile

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--kernel-module", type=Path, required=True)
    parser.add_argument("--candidate-config", type=Path, required=True, help="Public account and support-kernel metadata")
    parser.add_argument("--firmware", type=Path, help="External OEM firmware directory for a new stage")
    parser.add_argument("--revision", help="Public program revision recorded in CANDIDATE.json")
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    stage = options.stage
    existing = json.loads((stage / "SHA256.json").read_text()) if (stage / "SHA256.json").exists() else {}
    if not existing and stage.exists() and any(stage.iterdir()):
        raise RuntimeError("Use an empty or previously manifested public stage")
    if not existing and options.firmware is None:
        raise RuntimeError("A new public stage requires --firmware")
    for name, digest in existing.items():
        if hashlib.sha256((stage/name).read_bytes()).hexdigest() != digest:
            raise RuntimeError("Previous staging drift: " + name)
    updates = {"README.md": root / "README.md"}
    firmware_sources = {}
    if options.firmware is not None:
        firmware_sources = {p.name: p for p in sorted(options.firmware.iterdir())
                            if p.is_file() and re.fullmatch(r"fpcliu[.](mdt|b[0-9]{2})", p.name)}
        if "fpcliu.mdt" not in firmware_sources:
            raise RuntimeError("External OEM firmware is missing fpcliu.mdt")
        updates.update({"firmware/" + name: path for name, path in firmware_sources.items()})
    for name in ("fpc_build_info", "qsee-gatekeeper", "qsee-supplicant", "qsee-app-loader"):
        updates[name] = options.build / "clients" / name
    updates["ufs-rpmb-provider/liuqin-rpmb-supplicant"] = options.build / "clients/ufs-rpmb-provider/liuqin-rpmb-supplicant"
    updates["tod/libfprint-tod-fpc1264-oem.so"] = options.build / "libfprint-tod-fpc1264-oem.so"
    updates["fprintd"] = options.build / "fprintd"
    updates["pam-input"] = options.build / "pam-input"
    updates["fpc-oem-print"] = options.build / "fpc-oem-print"
    for path in (root / "src/fingerprint/oem").glob("*"):
        if path.suffix == ".py":
            updates[path.name] = path
        if path.suffix in (".py", ".c", ".sh"):
            updates["source/" + path.name] = path
    for name in ("fpc1264-oem.c", "fpc-oem-print.c"):
        updates["source/"+name] = root / "src/fingerprint/libfprint-tod" / name
    for path in (root / "src/fingerprint/deployment").glob("*"):
        if path.is_file():
            updates["deployment/"+path.name] = path
            if path.suffix == ".py":
                updates[path.name] = path
    updates["deployment/fprintd-fpc1264-oem.conf"] = root / "src/fingerprint/libfprint-tod/fprintd-fpc1264-oem.conf"
    updates["fprintd-fpc1264-oem.conf"] = updates["deployment/fprintd-fpc1264-oem.conf"]
    for path in (root / "src/fingerprint/fprintd-oem").glob("*"):
        if path.is_file():
            updates["source/fprintd-oem/"+path.name] = path
    updates["kernel/fpc1264_spi_diag.ko"] = options.kernel_module
    updates["pam.d/liuqin-fpc-enrol"] = root / "src/fingerprint/oem/pam.d/liuqin-fpc-enrol"
    vendor = root / "src/third_party/qsee-supplicant"
    for directory in ("src", "include", "LICENSES"):
        for path in (vendor / directory).rglob("*"):
            if path.is_file():
                updates["source/qsee-supplicant/" + path.relative_to(vendor).as_posix()] = path
    for name in ("LICENSE", "Makefile"):
        updates["source/qsee-supplicant/" + name] = vendor / name
    for name in ("liuqin_rpmb_main.c", "rpmb.c", "rpmb_ufs.c", "transport_qseecom.c",
                 "path.c", "services.c", "handle_db.c", "fs.c", "gpfs.c"):
        updates["ufs-rpmb-provider/src/" + name] = vendor / "src" / name
    for path in (vendor / "include").glob("*.h"):
        updates["ufs-rpmb-provider/include/" + path.name] = path
    updates["source/fprintd-vendor.tar.gz"] = options.build / "fprintd-vendor.tar.gz"
    updates["source/SOURCE_LOCK.json"] = root / "SOURCE_LOCK.json"
    updates["source/FPRINTD_SOURCE_HASHES.json"] = root / "FPRINTD_SOURCE_HASHES.json"
    updates["source/protocol/hw_auth_token.h"] = root / "src/fingerprint/protocol/hw_auth_token.h"
    qcbor = root / "src/third_party/QCBOR"
    for directory in ("src", "inc"):
        for path in (qcbor / directory).rglob("*"):
            if path.is_file():
                updates["source/QCBOR/" + path.relative_to(qcbor).as_posix()] = path
    updates["source/QCBOR/LICENSE"] = qcbor / "LICENSE"
    updates["source/package_fingerprint.py"] = Path(__file__).resolve()
    for name in ("build-userspace.sh", "build-tod.sh", "build-pam-input.sh", "build-fprintd-oem.sh"):
        updates["source/build-tools/" + name] = root / "tools" / name
    missing = [str(path) for path in updates.values() if not path.is_file()]
    if missing:
        raise RuntimeError("Incomplete build inputs: " + ", ".join(missing))
    candidate = json.loads(options.candidate_config.read_text())
    for key in ("linux_username", "linux_uid", "kernel_release", "base_kernel", "boot_image_sha256"):
        if key not in candidate:
            raise RuntimeError("Missing public candidate metadata: " + key)
    if not 0 < candidate["linux_uid"] < 0x10000000:
        raise RuntimeError("Unsupported Linux uid")
    candidate.update(schema=1, id="liuqin-fpc-oem-candidate-20261002-v1", native_uid=0x50000000 | candidate["linux_uid"],
                     program_path="/usr/local/lib/liuqin-fpc-oem", supports_one_finger_per_user=True,
                     actual_biometric_acceptance="pending", partition_writes=0,
                     kernel_module_sha256=hashlib.sha256(options.kernel_module.read_bytes()).hexdigest())
    stage.mkdir(parents=True, exist_ok=True)
    stage.chmod(0o700)
    for name, path in updates.items():
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    if options.revision:
        candidate["revision"] = options.revision
    if firmware_sources:
        (stage / "firmware/SHA256.json").write_text(json.dumps(
            {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in firmware_sources.items()},
            indent=2) + "\n")
    (stage / "CANDIDATE.json").write_text(json.dumps(candidate, indent=2)+"\n")
    hashes = {p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(stage.rglob("*")) if p.is_file() and p not in (stage/"SHA256.json", stage/"SHA256SUMS") and "__pycache__" not in p.parts}
    (stage / "SHA256.json").write_text(json.dumps(hashes, indent=2)+"\n")
    (stage / "SHA256SUMS").write_text("".join(d+"  "+n+"\n" for n,d in hashes.items()))
    binary_names = {"fprintd", "fpc_build_info", "qsee-gatekeeper", "qsee-supplicant", "qsee-app-loader", "pam-input", "fpc-oem-print",
                    "ufs-rpmb-provider/liuqin-rpmb-supplicant"}
    for path in stage.rglob("*"):
        if path.is_dir():
            path.chmod(0o700)
        else:
            name = path.relative_to(stage).as_posix()
            path.chmod(0o700 if name in binary_names or path.suffix == ".py" else 0o600)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(options.output,"w:gz") as archive:
        for name in sorted([*hashes, "SHA256.json", "SHA256SUMS"]):
            entry = archive.gettarinfo(str(stage/name), arcname=name)
            entry.uid = entry.gid = 0
            entry.uname = entry.gname = "root"
            with (stage/name).open("rb") as content:
                archive.addfile(entry, content)
    report = {"candidate": candidate, "public_programs":len(hashes), "archive": str(options.output),
              "archive_sha256":hashlib.sha256(options.output.read_bytes()).hexdigest(),"hashes":hashes}
    options.output.with_suffix(".json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({k:v for k,v in report.items() if k != "hashes"}))

if __name__ == "__main__":
    main()
