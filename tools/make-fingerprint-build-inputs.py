#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Release-maintainer step: remove OEM TA bytes/account binding from audited r4.

Consumers download fingerprint-build-inputs.tar.gz from the same release and
pass it directly to build-liuqin-fingerprint-deb.py. They do not need r4 or OEM
firmware. This script records how that public binary/source input was produced.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

R4_SHA256 = "bff5753541ff2c86fee3abcac7b8b2374a30d67d55749c7a4728b55fdefd506d"
EPOCH = 1791331200
OEM = Path(__file__).resolve().parents[1] / "device/fingerprint/oem"


def encode(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def build(source, output):
    content = source.read_bytes()
    if hashlib.sha256(content).hexdigest() != R4_SHA256:
        raise ValueError("expected audited r4 input")
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
        members = archive.getmembers()
        if any(not item.isfile() for item in members) or len({item.name for item in members}) != len(members):
            raise ValueError("unexpected r4 member type/duplicate")
        files = {item.name: archive.extractfile(item).read() for item in members}
        modes = {item.name: item.mode for item in members}
    manifest = json.loads(files["SHA256.json"])
    if set(files) != set(manifest) | {"SHA256.json", "SHA256SUMS"} or any(
            hashlib.sha256(files[name]).hexdigest() != digest for name, digest in manifest.items()):
        raise ValueError("r4 manifest mismatch")
    expected = json.loads((OEM / "firmware/SHA256.json").read_text())
    if json.loads(files["firmware/SHA256.json"]) != expected:
        raise ValueError("firmware manifest differs from reviewed stock")
    names = {"fpcliu.mdt", *(f"fpcliu.b{i:02d}" for i in range(9))}
    if set(expected) != names:
        raise ValueError("expected exactly ten TA hashes")
    for name in names:
        value = files.pop("firmware/" + name)
        if hashlib.sha256(value).hexdigest() != expected[name]:
            raise ValueError("r4 TA hash mismatch")
    info = json.loads(files["CANDIDATE.json"])
    for key in ("linux_username", "boot_image_sha256", "recovery_image_sha256", "actual_biometric_acceptance"):
        info.pop(key, None)
    info.update(account_profile="primary-uid-1000", firmware_source="modem_a-read-only")
    files["CANDIDATE.json"] = encode(info)
    files["firmware/NOTICE"] = (OEM / "firmware/NOTICE").read_bytes()
    files["BUILD_INPUTS.json"] = encode({
        "schema": 1, "upstream_host_bundle_sha256": R4_SHA256,
        "binary_revision": info["revision"],
        "description": "Public FPC programs, corresponding source and fixed TA hashes; no OEM TA bytes or user state",
        "source_material": "source/ includes modified client/TOD/fprintd sources, upstream fprintd archive, locks and licenses",
        "firmware": "Only hashes/provenance; the runtime provisions from this tablet's modem_a",
        "archive_epoch": EPOCH,
    })
    files["source/make-fingerprint-build-inputs.py"] = Path(__file__).read_bytes()
    manifest = {name: hashlib.sha256(value).hexdigest() for name, value in sorted(files.items())
                if name not in ("SHA256.json", "SHA256SUMS")}
    files["SHA256.json"] = encode(manifest)
    files["SHA256SUMS"] = "".join(f"{digest}  {name}\n" for name, digest in manifest.items()).encode()
    # Constant gzip header, tar order/modes/ownership/timestamps, no host paths.
    result = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=result, compresslevel=9, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name, value in sorted(files.items()):
                entry = tarfile.TarInfo(name)
                entry.size, entry.mtime = len(value), EPOCH
                entry.uid = entry.gid = 0
                entry.uname = entry.gname = "root"
                entry.mode = modes.get(name, 0o600)
                archive.addfile(entry, io.BytesIO(value))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(result.getvalue())
    print(json.dumps({"archive": str(output), "sha256": hashlib.sha256(result.getvalue()).hexdigest(),
                      "public_programs": len(manifest), "oem_firmware_distributed": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path, help="Audited private r4 host archive (maintainers only)")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.bundle, args.output)
