#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Generate stage-1 root admission contracts from the assembled file manifest.

Only the selected project-owned files are pinned. Distribution-managed programs
and ordinary user settings remain updatable. Stage 1 separately checks the
required executable and service topology.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path


# The complete GNOME pin list. Nothing is added programmatically; if a path is
# not written here it is not an admission criterion.
PINNED_PATHS = (
    "/etc/liuqin-gnome-root",
    "/etc/dconf/db/local.d/locks/00-liuqin-power",
    "/etc/systemd/system/liuqin-gnome-storage-guard.service",
    "/etc/systemd/system/liuqin-gnome-usb-rescue.service",
    "/etc/systemd/system/liuqin-power-keyd.service",
    "/etc/systemd/system/liuqin-power-keyd.service.d/20-liuqin-session-runtime.conf",
    "/etc/systemd/system/liuqin-snap-root-admission.service",
    "/etc/systemd/system/bluetooth.service.d/20-liuqin-public-address.conf",
    "/etc/systemd/system/liuqin-bt-preconfigure.service",
    "/etc/systemd/system/liuqin-hexagonrpcd-sdsp.service",
    "/etc/systemd/system/liuqin-ssc-sample-gate.service",
    "/etc/systemd/system/liuqin-sensor-stack.target",
    "/etc/systemd/system/liuqin-wlan-mac.service",
    "/etc/systemd/system/NetworkManager.service.d/20-liuqin-wlan-mac.conf",
    "/etc/udev/rules.d/80-liuqin-fastrpc.rules",
    "/usr/lib/firmware/novatek/liuqin/novatek_nt36532_m81_fw_csot.bin",
    "/usr/lib/firmware/novatek/liuqin/novatek_nt36532_m81_fw_tm.bin",
    "/usr/lib/firmware/qcom/sm8450/Xiaomi-Pad-6-Pro-tplg.bin",
    "/usr/bin/hexagonrpcd",
    "/usr/bin/ssccli",
    "/usr/share/qcom/sm8450/Xiaomi/liuqin/sensors/sns_reg_version",
    "/usr/share/liuqin/kernel.release",
    "/usr/share/liuqin/kernel-modules.manifest",
    "/usr/local/bin/busybox",
    "/usr/local/bin/liuqin-shell",
    "/usr/local/libexec/liuqin-iio-sensor-proxy",
    "/usr/local/libexec/liuqin-power-keyd",
    "/usr/local/libexec/liuqin-power-key-action",
    "/usr/local/libexec/liuqin-power-menu",
    "/usr/share/liuqin/power/io.github.liuqin.power.gschema.xml",
    "/usr/share/liuqin/power/gschemas.compiled",
    "/usr/local/libexec/liuqin-uinput-automation",
    "/usr/local/libexec/liuqin-audio-hwparams-probe",
    "/usr/local/sbin/liuqin-gnome-storage-guard",
    "/usr/local/sbin/liuqin-gnome-usb-rescue",
    "/usr/local/sbin/liuqin-snap-root-admission",
    "/usr/local/sbin/liuqin-bt-public-addr",
    "/usr/local/sbin/liuqin-wlan-mac",
    "/usr/libexec/liuqin-ssc-sample-gate",
)

# TODO(next contract revision): add
#     /usr/lib/firmware/updates/qcom/a730_sqe.fw
#     /usr/lib/firmware/updates/qcom/gmu_gen70000.bin
# once every root that this image must admit has had the device layer that puts
# them there installed.
#
# They belong in the pin list on the merits -- the device layer moved them to
# the kernel's updates/ override tree precisely so that linux-firmware can no
# longer overwrite them, which makes them stable pins in a way the old
# /usr/lib/firmware/qcom/ path never was.  They are held back for one release
# for a sequencing reason, not a design one:
#
# stage 1 treats a *missing* pinned file exactly like a corrupt one.
# `initramfs/init:231` requires each contract path to be a regular file before
# it hashes anything, and rejects the whole root otherwise.  The roots this
# image has to boot today still carry the GPU firmware only at the old path.
# Pinning the new path now would therefore reject every existing root until the
# new layer is installed -- and the new layer is installed *from* a booted
# root.  That is the same old-image/new-root deadlock the dconf pins caused,
# and the reason not to fix it by relaxing stage 1 is that "verify it only if
# it happens to be there" is not a security property: it hands any attacker who
# can delete a file the ability to skip its check.  Stage 1 stays fail-closed.
#
# So: the layer ships them now, the contract pins them next, once the layer is
# everywhere.  The layer's own assertions already guarantee they are present
# and that the dpkg-owned path is empty.

# Native-install pin list. Same ownership discipline as the GNOME
# list above, adjusted for the deb layout: the SSC proxy gates at its diverted
# in-place path, the GPU blobs pin at their updates/ override home (the old
# sequencing deadlock is gone -- every native root ships them from day one),
# and development evidence tools (ssccli, uinput-automation, the audio probe)
# are intentionally absent: they must not decide whether the system boots.
NATIVE_PINNED_PATHS = (
    "/etc/liuqin-native-root",
    "/etc/systemd/system/liuqin-slpi.service",
    "/usr/local/sbin/liuqin-slpi",
    "/etc/dconf/db/local.d/locks/00-liuqin-power",
    "/etc/systemd/system/NetworkManager.service.d/20-liuqin-wlan-mac.conf",
    "/etc/systemd/system/bluetooth.service.d/20-liuqin-public-address.conf",
    "/etc/systemd/system/liuqin-backlight-default.service",
    "/etc/systemd/system/liuqin-bt-preconfigure.service",
    "/etc/systemd/system/liuqin-gnome-storage-guard.service",
    "/usr/lib/systemd/system/liuqin-devmode.service",
    "/usr/lib/systemd/system/liuqin-devmode-ssh.socket",
    "/usr/lib/systemd/system/liuqin-devmode-ssh@.service",
    "/usr/lib/systemd/network/50-liuqin-devmode.network",
    "/usr/lib/liuqin/devmode/sshd_config",
    "/etc/liuqin-network",
    "/etc/udev/rules.d/71-liuqin-devmode.rules",
    "/etc/systemd/system/liuqin-hexagonrpcd-sdsp.service",
    "/etc/systemd/system/liuqin-power-keyd.service",
    "/etc/systemd/system/liuqin-power-keyd.service.d/20-liuqin-session-runtime.conf",
    "/etc/systemd/system/liuqin-sensor-stack.target",
    "/etc/systemd/system/liuqin-snap-root-admission.service",
    "/etc/systemd/system/liuqin-ssc-sample-gate.service",
    "/etc/systemd/system/liuqin-wlan-mac.service",
    "/etc/udev/rules.d/80-liuqin-fastrpc.rules",
    "/usr/lib/firmware/novatek/liuqin/novatek_nt36532_m81_fw_csot.bin",
    "/usr/lib/firmware/novatek/liuqin/novatek_nt36532_m81_fw_tm.bin",
    "/usr/lib/firmware/qcom/sm8450/Xiaomi-Pad-6-Pro-tplg.bin",
    "/usr/lib/firmware/updates/qcom/a730_sqe.fw",
    "/usr/lib/firmware/updates/qcom/gmu_gen70000.bin",
    "/usr/bin/hexagonrpcd",
    "/usr/libexec/iio-sensor-proxy",
    "/usr/libexec/liuqin-ssc-sample-gate",
    "/usr/local/bin/busybox",
    "/usr/sbin/liuqin-devmode",
    "/usr/local/libexec/liuqin-power-key-action",
    "/usr/local/libexec/liuqin-power-keyd",
    "/usr/local/libexec/liuqin-power-menu",
    "/usr/local/sbin/liuqin-bt-public-addr",
    "/usr/local/sbin/liuqin-gnome-storage-guard",
    "/usr/libexec/liuqin/devmode-gadget",
    "/usr/local/sbin/liuqin-snap-root-admission",
    "/usr/local/sbin/liuqin-wlan-mac",
    "/usr/share/liuqin/kernel-modules.manifest",
    "/usr/share/liuqin/kernel.release",
    "/usr/share/liuqin/power/gschemas.compiled",
    "/usr/share/liuqin/power/io.github.liuqin.power.gschema.xml",
    "/usr/share/qcom/sm8450/Xiaomi/liuqin/sensors/sns_reg_version",
)

PROFILE_PINS = {
    "gnome": (PINNED_PATHS, "LIUQIN_GNOME_ROOT_CONTRACT_V1"),
    "native": (NATIVE_PINNED_PATHS, "LIUQIN_NATIVE_ROOT_CONTRACT_V1"),
}

# Paths a pin list must never contain.  Everything here is owned by dpkg, so
# pinning it converts `apt upgrade` into a delayed-action boot failure -- which
# is not hypothetical, it is what v1 did.  The check exists because the failure
# is invisible at the moment it is introduced: the generator succeeds, the QEMU
# gate passes (nothing has been upgraded yet), and the bill arrives on the
# user's machine weeks later.  A comment could not have stopped that; a die can.
DISTRO_OWNED_PREFIXES = (
    "/usr/lib/aarch64-linux-gnu/",  # the whole distribution library closure
    "/usr/lib/systemd/",  # PID 1 and every unit Canonical ships
    "/usr/bin/",
    "/usr/sbin/",
    "/bin/",
    "/sbin/",
    "/lib/",
    "/etc/dconf/",  # preference, apart from the one exact lock below
    "/etc/gdm3/",
)

# The mirror of the rule above: a pinned path must be one this project owns
# outright.  Stated as an allowlist so that a *new* dpkg-owned directory nobody
# thought to deny is refused by default rather than admitted by default.
PROJECT_OWNED_PREFIXES = (
    "/etc/liuqin-",
    "/etc/systemd/system/liuqin-",
    "/usr/local/bin/",
    "/usr/local/libexec/",
    "/usr/local/sbin/",
    "/usr/lib/firmware/novatek/liuqin/",  # carved from the stock ROM, not packaged
    "/usr/lib/firmware/updates/",  # the kernel's override tree; no package writes here
    "/usr/share/liuqin/",  # custom-kernel/module identity, not owned by a package
)

# These paths live beneath otherwise distribution-owned top-level directories,
# so a prefix allowlist would quietly authorize files we do not own.  Keep
# each exception exact and make any addition a conscious contract review.
PROJECT_OWNED_EXACT_PATHS = (
    "/etc/dconf/db/local.d/locks/00-liuqin-power",
    "/etc/systemd/system/bluetooth.service.d/20-liuqin-public-address.conf",
    "/etc/systemd/system/NetworkManager.service.d/20-liuqin-wlan-mac.conf",
    "/etc/udev/rules.d/80-liuqin-fastrpc.rules",
    "/usr/libexec/liuqin-ssc-sample-gate",
    # Diverted by liuqin-sensors: a distro iio-sensor-proxy upgrade lands on
    # the .liuqin-orig side path, so pinning the live path stays safe against
    # distribution updates (used by the native profile).
    "/usr/libexec/iio-sensor-proxy",
    # The sensor stack is a separately built, manifest-pinned device-layer
    # artifact.  These paths deliberately use distribution-looking prefixes,
    # so admit only the exact files the layer owns; never relax /usr/bin,
    # /usr/share or /usr/lib/firmware/qcom wholesale.
    "/usr/bin/hexagonrpcd",
    "/usr/bin/ssccli",
    "/usr/share/qcom/sm8450/Xiaomi/liuqin/sensors/sns_reg_version",
    "/usr/lib/firmware/qcom/sm8450/Xiaomi-Pad-6-Pro-tplg.bin",
    # Developer mode (liuqin-device-support). The units, network file and
    # command use the packaged-file locations systemd and sshd expect; no
    # distribution package ships these names. Pinned so that the key-only
    # maintenance login always matches the boot image that admits the root.
    "/usr/lib/systemd/system/liuqin-devmode.service",
    "/usr/lib/systemd/system/liuqin-devmode-ssh.socket",
    "/usr/lib/systemd/system/liuqin-devmode-ssh@.service",
    "/usr/lib/systemd/network/50-liuqin-devmode.network",
    "/usr/lib/liuqin/devmode/sshd_config",
    "/usr/libexec/liuqin/devmode-gadget",
    "/usr/sbin/liuqin-devmode",
    "/etc/udev/rules.d/71-liuqin-devmode.rules",
)


def check_pin_list(paths: tuple[str, ...]) -> None:
    """Refuse a pin list that would make `apt upgrade` a boot failure."""
    if len(set(paths)) != len(paths):
        raise SystemExit("the pin list repeats a path")
    for rel in paths:
        if not rel.startswith("/") or "/../" in rel or rel.endswith("/.."):
            raise SystemExit(f"pin list entry is not a plain absolute path: {rel}")
        project_owned_exact = rel in PROJECT_OWNED_EXACT_PATHS
        for prefix in DISTRO_OWNED_PREFIXES:
            if rel.startswith(prefix) and not project_owned_exact:
                raise SystemExit(
                    f"refusing to pin a distribution-owned path: {rel} (under {prefix}); "
                    "stage 1 is fail-closed, so pinning a file dpkg may rewrite turns "
                    "`apt upgrade` into a root that will not boot next time. Prove what "
                    "is needed about distribution files with the topology gates in "
                    "initramfs/init instead of a content hash."
                )
        if not project_owned_exact and not any(
            rel.startswith(prefix) for prefix in PROJECT_OWNED_PREFIXES
        ):
            raise SystemExit(
                f"refusing to pin a path this project does not own: {rel}; "
                "admission criteria must be in the project prefix allowlist or "
                f"one of the exact exceptions {PROJECT_OWNED_EXACT_PATHS}"
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        help="accepted and unused since contract v2. Every hash now comes from "
        "the device-layer manifest, so there is no second tree to walk and no "
        "way for the contract and the layer to disagree.",
    )
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILE_PINS),
        default="gnome",
        help="gnome: legacy device-layer chain (default). native: node-C "
        "install chain; hashes come from the assembled native root's "
        "native-root.hashes via --device-layer-manifest",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument(
        "--device-layer-manifest",
        type=Path,
        help="device-layer.manifest from build-liuqin-gnome-device-layer.sh; "
        "supplies every hash in the contract",
    )
    args = parser.parse_args()
    project = args.project_root.resolve()
    pinned_paths, header = PROFILE_PINS[args.profile]

    check_pin_list(pinned_paths)

    if args.profile == "native" and not args.device_layer_manifest:
        raise SystemExit(
            "the native profile requires --device-layer-manifest pointing at "
            "the assembled root's native-root.hashes"
        )
    manifest_path = args.device_layer_manifest or (
        project / "out/gnome-device-layer/device-layer.manifest"
    )
    if not manifest_path.is_file():
        raise SystemExit(
            f"hash manifest is unavailable: {manifest_path}; "
            "run the device-layer or native-root builder first"
        )
    layer_hashes: dict[str, str] = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        digest, _, rel = line.partition("  ")
        if rel:
            layer_hashes[rel] = digest

    for rel in pinned_paths:
        if rel not in layer_hashes:
            raise SystemExit(f"the input manifest does not carry {rel}")

    lines = [header]
    lines.extend(f"{layer_hashes[rel]}  {rel}" for rel in sorted(pinned_paths))
    data = "\n".join(lines) + "\n"
    # Stage 1's own bounds, restated so a bad contract dies here rather than on
    # the tablet: `initramfs/init` accepts 256..16384 bytes and 10..128 lines
    # including the header.  The upper bounds deliberately leave room for a
    # reviewed future project closure without accepting an unbounded initramfs
    # parser input; the pin list remains far below both.
    if len(lines) != len(pinned_paths) + 1:
        raise SystemExit("the contract does not have one line per pinned path")
    if not 10 <= len(lines) <= 128 or not 256 <= len(data) <= 16384:
        raise SystemExit(
            f"{args.profile} contract is outside stage-1 bounds: {len(lines)} lines, {len(data)} bytes"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(data, encoding="ascii")
    os.chmod(args.output, 0o644)
    print(f"{args.profile} contract: {len(lines)-1} files, {len(data)} bytes, sha256={hashlib.sha256(data.encode()).hexdigest()}")


if __name__ == "__main__":
    main()
