# Installation

[中文](FLASHING.zh-CN.md) | [Project overview](../README.md)

## Availability

Download all files from the same release and follow the [installation steps](INSTALL-TESTING.md).
The system archive may be split into several files; the guide includes the joining command.

## Supported Device

Xiaomi Pad 6 Pro, codename `liuqin`, SM8475. Other Xiaomi Pad models are not
compatible. Initial installation and first boot have been tested on a **256 GB
unit**. Android recovery has not yet been independently validated. The 128 GB
and 512 GB capacity variants are admitted by the rules below but have not been
tested on real hardware; modified layouts are not supported. Do not change
constants or bypass checks to force an installation.

Field notes from one 512 GB single-system install are in [Ubuntu single-system
on the 512 GB model](FLASHING-512GB.md); they record the AVB short-read trap and
the resulting layout and are not an endorsement of modified layouts.

The bootloader must be unlocked and slot A active. Device-side checks require
these Linux sysfs values (capacities in 512-byte sectors, not filesystem
blocks):

| Item | Admitted value |
|---|---|
| userdata partition name (PARTNAME) | `userdata`, unique, on the first UFS LUN (sda) |
| sda logical block size | 4096 |
| userdata sectors | ≥ 33554432 (16 GiB) |

The partition number and start offset vary with the capacity variant and are
not part of the identity; both the installer and the boot chain resolve the
target partition by PARTNAME. Unknown or mismatching device, slot or session
identity must stop installation. Checks reduce risk but do not guarantee
recovery or replace real installation testing.

## Data and Recovery

The Linux-only layout gives Ubuntu the space of the Android userdata partition.
The dual-boot layout shrinks that partition and keeps Android alongside Ubuntu.
Either initial installation is destructive, and unlocking the bootloader also
erases user data.

Before installation:

1. Back up personal files outside the tablet.
2. Obtain the stock firmware matching the device and retain its recovery instructions.
3. Back up the original boot partitions and factory `persist` partition before
   overwriting any boot partition. A RAM boot and a persistent flash are different
   operations; backing up after flashing does not preserve the original image.

The installer must read factory calibration and addresses from the same tablet.
Never use another tablet's `persist` image or calibration data.

Returning to Android requires restoring the appropriate stock firmware and
preparing userdata for Android. Replacing only the boot image does not undo an
Ubuntu installation. Keep the bootloader unlocked while non-stock boot images
remain installed.

## Dual Boot

In the dual-boot layout both systems boot from slot A. Switching writes the
other system's boot image into `boot_a`, verifies it by reading it back, and
restarts the tablet; the image that is not in use is kept on the Ubuntu root
filesystem. The active slot is never changed by a switch, and `boot_b` holds a
copy of the Ubuntu boot image that the bootloader uses on its own if `boot_a`
does not load. Ubuntu offers a **Reboot to Android** entry, and Android offers
a **Reboot to Ubuntu** button through a KernelSU module.

Switching in either direction has not yet been validated on hardware. See
[Switching Between the Systems](INSTALL-TESTING.md#switching-between-the-systems)
for the commands, the safety rules and the repair procedure.

## Release Bundle

A supported release will provide matching boot and root filesystem images,
installation tools, checksums, source revisions, supported storage layouts and
recovery requirements. The installer verifies images before accessing the device.
Kernel build artifacts alone are not installation images.
