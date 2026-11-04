# Building

[中文](BUILD.md) | [Project overview](../README.en.md)

## Kernel

Use Linux with the project and kernel checkouts beside each other:

```text
workspace/
  xiaomipad-6pro-mainline/
  linux-sm8450-liuqin/
```

The kernel checkout must match the commit in `kernel/source.json`. The repository
is based on `sm8450-mainline/linux`; the device branch is `liuqin-6.17`.
The build script verifies the commit and generated configuration before compiling.

On Ubuntu 24.04, install the kernel build dependencies:

```sh
sudo apt-get update
sudo apt-get install build-essential bc bison flex libssl-dev libelf-dev \
  gcc-aarch64-linux-gnu libc6-dev-arm64-cross binutils-aarch64-linux-gnu python3 git ccache kmod rsync
```

From the project checkout:

```sh
python3 tools/build-liuqin-kernel.py --configure-only
python3 tools/build-liuqin-kernel.py --jobs 12
```

The second command builds the Image, device tree and modules under `out/kernel`.
It does not access the tablet. Available outputs include:

| Path under out/kernel | Content |
|---|---|
| arch/arm64/boot/Image | ARM64 kernel |
| arch/arm64/boot/dts/qcom/sm8475-xiaomi-liuqin.dtb | Device tree |
| modules/lib/modules/ | Installed kernel modules |
| .config | Effective kernel configuration |
| vmlinux, System.map | Debugging and symbol information |
| build-info.json, SHA256SUMS | Source/toolchain identity and core output checksums |

Use `--source PATH` for a different kernel checkout and `--out PATH` for a
different output directory. Repeated builds reuse compatible outputs and ccache.
Changed build inputs require a new output directory. The default compiler is
`aarch64-linux-gnu-gcc`; `CROSS_COMPILE` selects another toolchain prefix.

A successful kernel build does not establish hardware functionality or produce
a complete installation image. Byte-for-byte comparisons require the same
complete toolchain and build inputs.

## Userspace and Images

### Input Sources

| Component | Delivery |
|---|---|
| Original Ubuntu desktop base | Download the pinned Canonical ISO and extract it; this repository does not mirror the ISO |
| Unmodified Ubuntu packages and BusyBox | Download from Ubuntu repositories and cache locally; no duplicate package mirror |
| Upstream tools and userspace source | Pin upstream versions; maintain integration code and necessary patches here |
| Device kernel and project integration | Maintain source in the two project repositories; provide matching binaries with installation releases |
| Board firmware set | Manage required components as release inputs, not scattered experiment outputs; do not mirror the entire stock ROM |
| Factory calibration and device addresses | Read from the user's own tablet during installation; never include in generic packages |
| Adapted Ubuntu system | A project installation artifact, distinct from the unmodified upstream rootfs |

Builders download upstream inputs and assemble the system. Installation users use
matching finished artifacts without compiling components or finding dependencies
individually. Download installation bundles from [GitHub Releases](https://github.com/yzddmr6/xiaomipad-6pro-mainline/releases)
and observe each release's tested scope and limitations.

### Ubuntu Base

Install curl, util-linux (flock), 7z and squashfs-tools, then run:

```sh
sh tools/build-liuqin-ubuntu-desktop-rootfs.sh download
sh tools/build-liuqin-ubuntu-desktop-rootfs.sh casper
sudo sh tools/build-liuqin-ubuntu-desktop-rootfs.sh extract
```

Verified cached downloads are reused and interrupted transfers can resume.
`UBUNTU_DESKTOP_URL` may select a mirror supplying identical pinned bytes, not a
different release. Set `UBUNTU_DESKTOP_INPUT` consistently across stages to change
the input directory. Extraction prepares the desktop base only; project device
components must still be installed before it can boot on the tablet.

### Device Components

Third-party components do not all need to be rebuilt from source. Pinned upstream
binaries or project-prepared components may be used where redistribution permits,
with provenance, checksums and required license/source materials. Project changes
remain available as source. No prepared firmware download is currently published.

`tools/build-liuqin-wlan.py` prepares the WLAN set from a stock QCA6490 directory
(amss20.bin, m3.bin, regdb.bin and bd_m81gf.elf) and the pinned upstream
board-2.bin.zst. Pass `--vendor`, `--base-board`, `--out` and `--bdencoder`.
The encoder is `tools/scripts/ath11k/ath11k-bdencoder` from Qualcomm's
qca-swiss-army-knife commit `6df4dae3e2f5e4c2903f3cafd40996fc1b3639ce`.
Python 3 and zstd are required. The output is the firmware preparer's
`HSP2_TUPLE_DIR`. Stock input acquisition instructions and distribution terms
remain incomplete; this tool does not download or distribute firmware.

The device integration sources include system services, audio configuration,
power-key support, sensor patches and the GNOME Settings patch. Sensor source
versions are recorded in `device/sensors/sources.manifest`.

GNOME Settings uses the Ubuntu source package and the patch recorded in
`device/gnome-control-center/source.json`. It requires Python 3.12, curl, patch,
dpkg-dev and an AArch64 binfmt interpreter on the host, plus the prepared Ubuntu
ARM64 root filesystem. Prepare its source with:

```sh
python3 tools/build-liuqin-settings.py --prepare-only
```

Build with `sudo python3 tools/build-liuqin-settings.py --jobs 8`. The build runs
inside an isolated mount namespace and does not modify the input root filesystem.
The device package builder reads its binary and source identity from
`out/gnome-control-center/`; there is no dependency on a historical Settings binary.

### Prepared inputs and the artifact cache

`python3 tools/prepare-image-inputs.py run` prepares every input that
`build-liuqin-image.py` consumes and writes `out/image-inputs.local.json`.
Cheap inputs (audio topology, firmware tree) are rebuilt fresh on every run;
the two slow QEMU builds (the sensor stack and the GNOME Settings power panel)
are cached under `tools/local/artifacts-cache/` keyed by a fingerprint of
their sources: an unchanged fingerprint reuses the artifact after a sha256
re-verification, and any change rebuilds it.  `check` only reports reuse vs
rebuild without doing the work.  Rebuilds invoke the official builders via
sudo (run `sudo -v` first).

The speaker topology is built from the AudioReach source revision
`2af1f1ebb8d4fd03b5f53891467ddde2e208a8a0` in
[linux-msm/audioreach-topology](https://github.com/linux-msm/audioreach-topology).
With that checkout available, run:

```sh
AUDIOREACH_TOPOLOGY_DIR="$PWD/../audioreach-topology" \
  OUTPUT="$PWD/out/audio-topology/Xiaomi-Pad-6-Pro-tplg.bin" \
  sh tools/build-liuqin-audio-topology.sh
```

The native boot builder consumes the selected kernel's Image and DTB, an
assembled root file manifest, the topology and the prepared firmware inputs.
It does not require an older boot image or a recovery root filesystem.

| Input | Variable |
|---|---|
| Kernel source checkout and build output | KERNEL_SOURCE, KERNEL_OUT |
| Assembled root's native-root.hashes | NATIVE_ROOT_HASHES |
| Compiled speaker topology | AUDIO_TOPOLOGY |
| Prepared device firmware directories | FIRMWARE_POOL |
| HSP2 WLAN tuple | WLAN_HSP2_TUPLE |
| Extracted stock DTBO entries and base DTBs | STOCK_OVERLAY_DIR, STOCK_BASE_DIR |

After supplying those inputs, `sh tools/build-liuqin-native-boot.sh` writes
`out/native-boot/boot-liuqin-native.img`. The accompanying
`native-boot.identity` records input and output hashes without local paths.
An offline assembly result still requires device validation before distribution.

The complete image build is still being prepared for a clean checkout.
Versioned firmware inputs and userspace build dependencies must be available
before that workflow is supported. Do not substitute an older
boot image or a kernel from a different build.

## Root Filesystem Archives

After assembly and manifest generation, run
`sudo -E sh tools/build-liuqin-native-root.sh pack` with the same `OUT_DIR`.
This produces `rootfs.tar.gz` and its checksum without rebuilding components or
accessing the tablet. Keep the input tree unchanged throughout packaging; do not
package a booted tree containing accounts or provisioned device data.
Existing archives are not overwritten.

The archive preserves numeric ownership, modes, links, ACLs and extended
attributes, including `security.capability`. To extract a trusted, verified
archive locally, use `sudo sh tools/lib/rootfs-archive.sh extract NEW_DIR rootfs.tar.gz`.
GNU tar is required; BusyBox tar is not a substitute. The installation environment
must provide equivalent extraction support. This does not validate installation
or Android recovery.

## Continuous Integration

The workflow builds the pinned kernel using the same Python entry point as the
local build. Its artifacts are kernel build outputs, not installable Ubuntu
releases. Kernel-repository development builds share the same action.
`tools/build-liuqin-image.py` assembles matching system artifacts with resumable
stages. The image workflow requires a configured dedicated runner; see
[CI setup](CI.md). Follow the [installation steps](INSTALL-TESTING.en.md) for device
testing. A CI build alone does not validate a new installation bundle.

## Release Assets

After completing device installation tests, export the existing bundle without
rebuilding its images:

```sh
sudo python3 tools/build-liuqin-image.py --inputs inputs.local.json \
  --kernel-out out/kernel --out out/image --stage release-assets --device-tested
```

Upload the files in `out/image/release-assets/` to the main project's GitHub
Release. The root filesystem is split into files below GitHub's per-asset limit;
the installation guide explains how to join them. Omit `--device-tested` when
exporting a bundle that has not completed device testing.
