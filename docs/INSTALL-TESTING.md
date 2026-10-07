# Installation Steps

Initial installation and first boot have been tested on a 256 GB unit. The
128 GB and 512 GB variants are admitted by the rules in the
[installation guide](FLASHING.md) but have not been tested on real hardware.
This remains an experimental device port. Keep the tablet attended and prepare a recovery plan.

An earlier revision of the dual-boot layout has been installed on a 256 GB
unit: the partition table was split, Ubuntu was installed into `linux_root`
with `/home` on `linux_home`, and Ubuntu booted from slot B. The current
revision boots both systems from slot A and switches between them by
exchanging the boot image in `boot_a` (see
[Switching Between the Systems](#switching-between-the-systems)). The current revision has been installed on the same unit with `--layout dual --keep-home` (v0.5.0): the reinstall kept `/home`, Ubuntu boots from slot A, and `liuqin-switch verify` confirms the switch store. The switch in either direction, Android's first boot and KernelSU have not yet been tested on hardware. The Linux-only layout uses the same
layout engine but has not been installed on hardware. Treat both as attended
experiments.

## Installation Layouts

The installer offers two layouts, both laid out by the same engine. In the
`dual` layout both systems boot from slot A. The `linux-only` layout places
Ubuntu in slot B and leaves slot A untouched, so that an emergency Android kept
with `--android-size` remains bootable.

| Layout | Android | Ubuntu system | Ubuntu home |
| --- | --- | --- | --- |
| `linux-only` | none by default | `linux_root`, 32 GiB | `linux_home`, the remainder |
| `dual` | `userdata`, 96 GiB | `linux_root`, 32 GiB | `linux_home`, the remainder |

Select one with `--layout linux-only` or `--layout dual`. Sizes are given as
`NNG` or as a percentage of the available tail of the disk:

- `--android-size` sets the size of the Android data partition. The default is
  `96G` for `dual` and `0` for `linux-only`. `0` deletes `userdata`, which
  gives the whole tail to Ubuntu. A non-zero size must be at least 16 GiB;
  `--layout linux-only --android-size 32G` keeps a small emergency Android.
- `--root-size` sets the size of `linux_root`. The default is `32G` and the
  minimum is 16 GiB.
- `linux_home` receives everything that is left and must be at least 8 GiB.

Every partition is aligned to 4 MiB. `userdata` is only ever shrunk in place:
it keeps its original type GUID, unique GUID and attribute bits, and no
existing partition is moved. A size outside the feasible range is refused
before any device access, with the feasible range printed.

The installer prints the complete layout plan — partition, start sector, size
and action — and requires the same interactive confirmation as before.

### What each layout writes

`linux-only` writes the partition table, `linux_root`, `linux_home` and
`boot_b`. It does not touch `super`, `metadata` or any slot A partition.

`dual` additionally clears the first 16 MiB of `userdata` and of `metadata`, so
that Android reformats both on first boot instead of finding stale file-based
encryption keys, and restores the stock Android partitions of slot A from a ROM
directory you supply. Only the images whose bytes differ from what the tablet
already holds are written, and only to `_a` partition names. `super` is an
Android sparse image and cannot be compared against the partition, so it is
always written when the dual layout is selected.

The ROM's `boot.img` is the exception: it is not flashed. `boot_a` receives
the project boot image, `boot_b` receives the same image as the bootloader's
fallback, and Android's boot image is stored on `linux_root` for the switcher
described below. The installer never runs the stock `flash_all` script and
never writes a `_b` partition other than `boot_b`.

### Dual-boot prerequisites

`--layout dual` requires `--rom-dir`, pointing at an extracted stock Xiaomi
Fastboot ROM. The original ROM is an upstream input and is not redistributed
here. The installer verifies `boot.img`, `vendor_boot.img`, `dtbo.img`,
`vbmeta.img`, `vbmeta_system.img`, `super.img`, `NON-HLOS.bin`, `BTFM.bin` and
`dspso.bin` against the checksums pinned
in `liuqin-rom-images.json`, and refuses any other release. Those checksums
identify the exact ROM this port was validated against; a different ROM
requires repeating that validation.

`NON-HLOS.bin` restores `modem_a`, which contains the WLAN and DSP firmware;
`BTFM.bin` restores `bluetooth_a`, and `dspso.bin` restores `dsp_a`. A missing or damaged firmware filesystem can
crash Android before ADB starts even when its boot and system images match.
Matching partitions are left alone. Before replacing one of these firmware partitions,
the installer saves its entire original contents as `modem_a.img`,
`bluetooth_a.img` or `dsp_a.img` in the private backup directory and verifies its checksum.
The replacement is zero-filled to the actual partition size and checked again
before flashing, so damaged bytes beyond the original ROM file cannot survive.
To undo a replacement, verify that directory's `SHA256SUMS`, then use
`fastboot -s SERIAL flash modem_a /path/to/backup/modem_a.img` (or the corresponding
`bluetooth_a` or `dsp_a` command). This restores the saved bytes, including any pre-existing
damage; it does not alter the partition table or Android data.

Observe the ROM's own anti-rollback rule: its version must be at least the
version already fused into the tablet.

### Replacing the Android boot image

`--layout dual` accepts `--android-boot IMG`, which makes `IMG` Android's boot
image instead of the ROM's own `boot.img`: it is stored on `linux_root` and
written to `boot_a` whenever the tablet is switched to Android. Every other
stock image is still verified against the pinned checksums, and the override
itself is admitted only when it is exactly as long as the stock `boot.img` and
its header identifies it as an Android boot image (header version 3 or 4). Its
sha256 is printed in the plan summary and again in the erasure confirmation.
The option is rejected with any layout other than `dual`.

This is how a root-enabled Android is installed alongside Ubuntu.
`tools/patch-android-boot-ksu.py` produces such an image entirely on the host:
it unpacks the stock `boot.img`, renames `init` to `init.real` in the ramdisk,
installs KernelSU's `ksuinit` as `init` and the matching loadable module as
`/kernelsu.ko`, and repacks the image with the kernel, the command line and
every other header field unchanged. The KernelSU release it draws on is pinned
in `tools/lib/kernelsu-assets.json` and fetched by
`tools/fetch-kernelsu-assets.sh`.

```sh
sh tools/fetch-kernelsu-assets.sh
python3 tools/patch-android-boot-ksu.py \
  --boot /path/to/extracted-stock-rom/images/boot.img \
  --ksuinit tools/local/downloads/kernelsu/v3.3.0/ksuinit-aarch64 \
  --lkm tools/local/downloads/kernelsu/v3.3.0/lkm-aarch64-android12-5.10_kernelsu.ko \
  --out out/android-ksu/boot-ksu.img --report out/android-ksu/boot-ksu.report.txt
```

The tool refuses to write anything unless an unpatched unpack-and-repack of the
same image reproduces the stock bytes exactly, so the only difference between
its output and the stock image is the ramdisk it was asked to change.

Any change to the ramdisk invalidates the AVB boot signature and the vbmeta
hash descriptor for that partition, so the resulting image boots only on an
unlocked bootloader. Root also makes Android able to modify slot B: the
`liuqin_boot_ubuntu` KernelSU module disables the system updater on every boot
for that reason, because an Android over-the-air update rewrites the inactive
slot, which holds the fallback copy of the Ubuntu boot image, and then makes
it active.

## Requirements

- Xiaomi Pad 6 Pro (liuqin) with the factory partition table, 4096-byte
  logical sectors and `userdata` as the last partition. Modified partition
  layouts are rejected.
- Unlocked bootloader and the device in Fastboot mode.
- Battery at least 30 percent charged.
- Linux host with Python 3.11 or newer, Android platform-tools and USB networking.
- Personal files backed up outside the tablet. Installation erases all userdata.
- A matching original Xiaomi Fastboot ROM and an Android recovery plan prepared
  before installation. The installer does not back up personal userdata.

Download all files from the same release. `install.py`, `liuqin_layout.py` and
`liuqin-rom-images.json` belong together; the installer refuses to run without
them. If the system archive is split, join it in the bundle directory:

```sh
if [ ! -f rootfs.tar.gz ]; then
  cat rootfs.tar.gz.part-* > rootfs.tar.gz
fi
```

Images are verified automatically before any device access. Before the
installation starts, confirm the erasure interactively by typing `YES`
(scripts and non-interactive shells pass `--yes` explicitly):

```sh
python3 install.py --bundle . --serial DEVICE_SERIAL \
  --backup /path/to/new-private-backup --erase-userdata --layout linux-only
```

```sh
python3 install.py --bundle . --serial DEVICE_SERIAL \
  --backup /path/to/new-private-backup --erase-userdata --layout dual \
  --rom-dir /path/to/extracted-stock-rom --android-size 96G --root-size 32G
```

```sh
python3 install.py --bundle . --serial DEVICE_SERIAL \
  --backup /path/to/new-private-backup --erase-userdata --layout dual \
  --rom-dir /path/to/extracted-stock-rom --android-boot /path/to/boot-ksu.img \
  --android-size 96G --root-size 32G
```

Use `python3 install.py --bundle . --check` for an optional local-only check;
adding `--layout` prints the planned layout without accessing a device.
Locally built or CI-generated bundles that have not passed device testing require
`--allow-unverified` for an explicitly attended test.

The installer boots `installer.img` in RAM, waits for its USB network, backs up
boot_a, boot_b, persist and both copies of the partition table, verifies those
backups, then edits the partition table, installs the rootfs and provisions this
tablet's calibration and addresses. It formats `linux_root` and `linux_home` as
ext4 and writes an `/etc/fstab` entry mounting `LABEL=LIUQIN_HOME` at `/home`.
In the dual layout it also places the Ubuntu and Android boot images, each
checked against its checksum and header, in the switch store on `linux_root`.
Only after the root installation succeeds does it write the project boot image
— to `boot_a` and `boot_b` in the dual layout, to `boot_b` in the Linux-only
layout — select the booting slot through Fastboot (slot A for dual, slot B for
Linux-only) and reboot. It does not relock the bootloader and does not write
persist.
Keep the backup directory private. The USB rescue shell has no authentication:
use a direct, trusted USB connection, not a shared network.

After a partition-table edit, `sgdisk` verifies the new table and the installer
re-reads it and compares it with the plan. If anything does not match, the
installation stops and the saved table can be written back.

USB networking normally obtains an address through DHCP. `--host-address` selects
the host's USB address when automatic route selection is unsuitable. If the
installer cannot establish its control channel it stops; do not blindly retry
after a partial installation. Preserve the error output and backup first.

## Reinstalling and Changing the Split

On a tablet that already carries this split layout, the installer refuses to
resize it. Partition sizes cannot be adjusted in place, because `linux_root`
and `linux_home` would have to move and their contents cannot be preserved
through the move.

To reinstall the system on an existing split and keep `/home`, pass
`--keep-home`. It keeps the existing sizes, leaves `linux_home` untouched and
reinstalls `linux_root` only, so it may not be combined with `--android-size`
or `--root-size`. Before anything is formatted, the installer checks that
`linux_home` holds the existing `LIUQIN_HOME` filesystem (its label, then
`e2fsck -p`) and stops if it does not; the filesystem is then kept as it is,
and only an empty one receives the default `/home` contents.

To change the split, restore the stock partition table and install again:

```sh
python3 install.py --bundle . --serial DEVICE_SERIAL \
  --restore-partition-table /path/to/private-backup
```

The restore refuses a backup taken from another tablet, and refuses a saved
table whose checksums do not match the manifest recorded beside it. A restored
stock table leaves the tablet without a usable system; complete it with a full
installation or with the original ROM's clean-flash procedure.

## Switching Between the Systems

In the dual layout both systems boot from slot A. Switching writes the other
system's boot image into `boot_a`, reads it back, and restarts the tablet. The
switch never changes the active slot: this bootloader changes slots only
through its own `fastboot --set-active`, which also swaps the type GUIDs of
every `_a`/`_b` partition pair and the UFS boot LUN, and a slot change made in
any other way leaves every image failing to load. Only the boot image rotates.
Android's `vendor_boot_a`, `dtbo_a`, `vbmeta_a`, `super`, `metadata` and
`userdata` stay as installed; the Ubuntu boot image is self-contained and
carries its own kernel, device tree and initramfs.

The image that is not installed is kept on the Ubuntu root filesystem:

```
/var/lib/liuqin/switch/
  ubuntu/    boot.img  SHA256SUMS  meta.json
  android/   boot.img  SHA256SUMS  meta.json
  state.json switch.log
```

The installer fills both sets. The directory is not part of the file list the
boot image verifies before starting Ubuntu, so changing it never prevents
Ubuntu from booting. `boot_b` holds a copy of the Ubuntu boot image that the
bootloader falls back to on its own when `boot_a` does not load; the switcher
never writes it.

**From Ubuntu**, open **Reboot to Android** from the application list, which
asks for an administrator password, or run:

```sh
sudo liuqin-switch status          # what boot_a holds and what the store holds
sudo liuqin-switch verify          # re-check both images; non-zero if anything is off
sudo liuqin-switch to-android      # write the Android image into boot_a and reboot
```

**From Android**, use the **Reboot to Ubuntu** button in the WebUI of the
`liuqin_boot_ubuntu` KernelSU module, or run
`su -c 'sh /data/adb/modules/liuqin_boot_ubuntu/system/bin/boot-ubuntu'`.
Android mounts the Ubuntu root read-only for the duration of the switch; its
kernel cannot mount that file system read-write.

`--no-reboot` stops before the restart, and `--dry-run` reports what would be
written without writing anything.

The switcher enforces these rules, in this order:

- A stored image that is missing, does not match its `SHA256SUMS`, is larger
  than `boot_a` or is not of the kind its directory names is refused before
  anything is written.
- No switch starts without a verified way back. Switching to Android requires
  a usable Ubuntu image in the store. Switching to Ubuntu requires the Android
  image currently in `boot_a` to be the one in the store; from Ubuntu the
  switcher archives it first, and from Android, where the store is read-only,
  it refuses instead.
- An image that identifies as the project's own is never archived as Android.
- `boot_a` is written as a whole partition — the image followed by zeros —
  synced, read back and compared. If the read-back differs, or the write fails,
  the previous system's image is written back and the switcher exits with an
  error without restarting the tablet.
- The kernel read-only flag of `boot_a` and of its disk, which the storage
  guard sets on every boot, is cleared only for the write and restored on every
  exit path. Interrupt, hangup and terminate signals are ignored while `boot_a`
  is being written.

The two kinds of image are told apart by their boot-image header. Header
version 2 with a 4096-byte page, a 1660-byte header and an embedded device tree
is the project's Ubuntu image. Header version 3 or 4, with a header of 1580 or
1584 bytes respectively, is an Android image. Anything else is refused.

**When Android's boot image changes.** Replacing Android's boot image on the
tablet itself — for example with the KernelSU manager's direct-install action —
makes it differ from the stored copy, and the switch to Ubuntu is then refused
from Android. Prepare such images on the host instead, and import them from
Ubuntu before switching:

```sh
sudo liuqin-switch import-android /path/to/boot-ksu.img
```

If the switch is already blocked, copy `boot_a` off the tablet from Android,
restore Ubuntu's boot image from a host (see the Fastboot commands below), and
import the copy from Ubuntu.

**When the tablet starts from slot B.** If `boot_a` does not load, the
bootloader switches to slot B by itself and starts Ubuntu from the fallback
copy. The switcher detects this and refuses to write, because writing `boot_a`
cannot change what boots next. Repair it from a host in Fastboot mode:

```sh
cp /path/to/boot.img boot-padded.img
truncate -s 201326592 boot-padded.img
fastboot flash boot_a boot-padded.img
fastboot --set-active=a
```

Fastboot writes only the bytes it is sent, and `liuqin-switch` identifies the
content of `boot_a` by the whole partition, so the image is flashed zero-filled
to the 192 MiB partition size, as the installer does. The bare image boots as
well, but leaves the previous tail of the partition behind it, and
`liuqin-switch verify` then reports an Ubuntu image that is not the one in the
store until `liuqin-switch to-ubuntu` rewrites the partition.

An interruption during a write that the switcher cannot catch, such as a power
loss, has the same outcome and the same repair.

**Validation status.** The switcher's decision and verification logic is
covered by host-side tests that run it against ordinary files. Neither
direction of the switch, the read-only mount on Android, nor the cold boot of
either system from slot A after a switch has been performed on the tablet.
Ubuntu has booted from slot B on the earlier revision; booting it from slot A,
where the bootloader pairs it with slot A's stock `dtbo_a` and `vbmeta_a`, has
not been tested.

**Upgrading an earlier dual installation.** Installations made by the earlier
revision boot Ubuntu from slot B and have no switch store. Reinstall the system
with the current release, keeping `/home`, to move to the new arrangement:

```sh
python3 install.py --bundle . --serial DEVICE_SERIAL \
  --backup /path/to/new-private-backup --erase-userdata --layout dual \
  --rom-dir /path/to/extracted-stock-rom --android-boot /path/to/boot-ksu.img \
  --keep-home
```

`--android-boot` is optional; without it the ROM's `boot.img` is stored.

## Android in the Dual Layout

The first Android boot after a dual installation reformats `userdata` and
`metadata` and takes several minutes.

Root through KernelSU is deliberately not performed by the installer. Produce
the patched image on the host as described above and either pass it to the
installer with `--android-boot` or import it from Ubuntu with
`liuqin-switch import-android`. Do not flash it to `boot_a` directly.

**Never use "install to inactive slot" in the KernelSU manager.** The inactive
slot is slot B, which holds the fallback copy of the Ubuntu boot image.

**Freeze system updates.** A MIUI or HyperOS over-the-air update writes to the
inactive slot, slot B, and then makes it active. Android cannot start from
slot B, and the update destroys the fallback copy of the Ubuntu boot image.

**Never run the stock `flash_all` script again.** It writes most images with the
`_ab` suffix, that is, into both slots at once, and it ends with
`fastboot set_active a`. Running it after a dual installation overwrites the
Ubuntu boot chain and returns the tablet to Android only. The same applies to
`flash_all_lock.sh` and `flash_all_except_storage.sh`.

## Desktop Diagnostics

For an attended installation test, add `--enable-rescue` to the installation
command to make the rescue shell available from the first boot. This is an
explicit opt-in to unauthenticated root access, not the default installation.

On the tablet, enable the rescue shell with:

```sh
sudo liuqin-rescue on
```

Check it with `liuqin-rescue status`. This grants unauthenticated root access
at `192.168.7.2:2323` and remains enabled across boots. Use only a trusted
connection; do not expose or forward this port to other networks. After
diagnostics, run `sudo liuqin-rescue off` on the tablet to disable it and
close existing rescue connections. Release images leave it disabled by default.

## Known Issues

On some units, starting the sensor processor (SLPI) resets the tablet. On such
a unit, create the empty marker file `/etc/liuqin-slpi-disabled` (for example
with `sudo touch /etc/liuqin-slpi-disabled`) and reboot: `liuqin-slpi.service`
is then skipped and SLPI is not started. Automatic screen rotation is
unavailable while the marker exists; delete it and reboot to start SLPI again.
Never edit `/etc/systemd/system/liuqin-slpi.service` itself: the boot image
verifies that file and refuses to start a root on which it has been modified.

## Recovery

Returning to Android alone erases the Ubuntu installation and requires a
compatible original Fastboot ROM, including its userdata initialization.
Restoring a boot partition alone is not a complete Android recovery, and it does
not restore the partition table: use `--restore-partition-table` first if the
tablet was installed with a split layout.

Use the original ROM's full clean-flash procedure, not its keep-data or relock
variant. Preserve the anti-rollback checks. Never restore another tablet's
persist or calibration. Keep the bootloader unlocked while non-stock images
remain. The original ROM is an upstream input, not duplicated in this repository.

Android recovery still requires independent device testing. Successful Ubuntu
installation does not establish that Android recovery has been validated.
