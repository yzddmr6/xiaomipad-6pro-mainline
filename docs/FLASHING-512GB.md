# Ubuntu single-system on the 512 GB model

> **Field notes, not an officially validated layout.** The steps below were used
> on one 12 GB/512 GB unit (hardware version 16.9.0) in October 2026. The
> installer and release notes remain authoritative; this page records the
> 512 GB-specific traps that were hit and the state the unit ended in. The
> installation is destructive.

The 512 GB variant has the same partition names as the 256 GB unit but different
sizes. The stock Android layout leaves 64 GiB for the Ubuntu root filesystem and
271 GiB for `/home`; the Android `super` and `userdata` partitions sit between
them. A single-system install can either keep the Android space unused or
reclaim it as a `/data` filesystem.

## Confirmed layout

`/dev/sda` is the UFS LUN that holds both Android and Linux partitions;
`/dev/sde` holds the boot partitions and must not be repartitioned.

| Partition | Size on this unit | Role |
| --- | --- | --- |
| `sda28` | 136.7 GiB | `/data`, label `LIUQIN_DATA` (reclaimed Android space) |
| `sda36` | 64 GiB | `/`, label `LIUQIN_ROOT`, ext4 |
| `sda37` | 271.3 GiB | `/home`, label `LIUQIN_HOME`, ext4 |
| `sde14` | 192 MiB | `boot_a`, Ubuntu boot image with embedded DTB |
| `sde17` | 8 KiB | `vbmeta_a`, AVB metadata |
| `sde18` | 24 MiB | `dtbo_a` |
| `sde25` | 96 MiB | `vendor_boot_a`, also loaded by the bootloader for Ubuntu |

The root filesystem lives inside `/dev/sda36` under `/native-root`; the
initramfs moves it into place. Do not format `sda36` directly.

## Before installing

1. Unlock the bootloader (`fastboot flashing unlock` and `critical unlock`).
   Unlocking erases userdata.
2. Use the stock ROM version pinned by the release you install. A different
   firmware build fails the bundle's hash checks.
3. Back up the original `boot_a`, `vbmeta_a` and both GPTs before writing
   anything. A RAM boot and a persistent flash are different operations.
4. Keep the stock firmware and its flashing instructions; returning to Android
   needs the full package, not just a boot image.

## Installing

Boot the installer in RAM and use the Linux-only layout:

```sh
fastboot boot installer.img
```

Follow [Installation testing](INSTALL-TESTING.md) for the network/telnet steps
and the layout choice. Linux-only gives Ubuntu the Android userdata space; the
installer writes the boot image, root filesystem and firmware. If the release
you are using refuses the 512 GB capacity, stop and use a release that admits
it; do not hand-edit the installer's size tables.

The unit described here was installed before the release rules admitted the
512 GB capacity; the layout below is the result, not a required part of a
current install.

## The first-boot trap: AVB reads fewer bytes than requested

The most common failure is: the first boot reaches the Xiaomi logo, then either
loops or returns to fastboot. `fastboot getvar current-slot` may still say `a`,
but `fastboot getvar slot-unbootable:a` reports `yes`. The bootloader log in the
`logfs` partition (`/dev/sda13`, FAT) contains:

```text
Booting from slot (_a)
Load Image boot_a total time: 102 ms
avb_slot_verify.c:210: ERROR: boot_a: Read fewer than requested bytes.
No bootable slots found enter fastboot mode
VB2: boot state: red(3)
```

With the stock image the same log shows `Boot state is: orange` and the boot
succeeds, which is the clue: the stock image carries an AVB footer and the
bootloader takes a different read path.

On an unlocked device libavb verifies the whole partition with
`get_size_of_partition()`. This bootloader returns fewer bytes than requested on
that path, the hash check fails, and the slot is marked unbootable. The Ubuntu
boot image has no AVB footer, so it hits the broken path every time.

Disable verity and verification in `vbmeta` on both slots:

```sh
fastboot --disable-verity --disable-verification flash vbmeta_a rom/images/vbmeta.img
fastboot --disable-verity --disable-verification flash vbmeta_b rom/images/vbmeta.img
fastboot --set-active=a
fastboot reboot
```

This changes only the four flag bytes in `vbmeta` (`HASHTREE_DISABLED |
VERIFICATION_DISABLED = 0x3`); the boot image is untouched and the change is
reversible by flashing the stock `vbmeta` again. After the first successful
boot the initramfs marks the slot successful, and later reboots enter Ubuntu
directly.

Verify from the booted system:

```sh
python3 - <<'PY'
import struct
d = open('/dev/sde17', 'rb').read(4096)
print(hex(struct.unpack_from('>I', d, 120)[0]))   # expect 0x3
PY
```

## Do not edit the initramfs root contract

The initramfs carries a hash contract over a set of files: systemd units for
SLPI, hexagonrpcd, the sensor gate and the sensor stack, several
`/usr/libexec` helpers, `/usr/local/sbin/liuqin-slpi`, `/usr/local/bin/liuqin-shell`,
touch firmware, audio topology and others. If any file is moved or modified,
the initramfs rejects the persistent root and starts an emergency shell:

```text
liuqin-initramfs: native root rejected: not a regular file: ...
liuqin-initramfs: persistent root marker or root contract is invalid
liuqin-initramfs: starting local emergency shell
```

Disable a service with a systemd drop-in or a `ConditionPathExists` guard; never
edit the contracted file itself.

The emergency shell is reachable over USB networking at `192.168.7.2:2323`.
From there the root filesystem can be mounted at `/mnt/r/native-root`, the
backup restored, and the tablet rebooted.

## Post-install essentials

Add the desktop user to the device groups; without this PipeWire exposes only
`Dummy Output` and `/dev/video*` is inaccessible:

```sh
usermod -aG audio,video,render,input <user>
```

Other fixes that were needed on this unit:

```sh
# console-setup fails with XKBLAYOUT="default,us" on current xkb-data
sed -i 's/^XKBLAYOUT=.*/XKBLAYOUT="us"/' /etc/default/keyboard

# no zram module in this kernel: use a swapfile
fallocate -l 16G /home/swapfile && chmod 600 /home/swapfile
mkswap /home/swapfile && swapon /home/swapfile
echo '/home/swapfile none swap sw,nofail,pri=10 0 0' >> /etc/fstab

# unattended-upgrades can hold a block inhibitor and refuse systemctl reboot
systemctl mask apt-daily.timer apt-daily-upgrade.timer
```

## Reclaiming the Android space

This is optional and irreversible without the stock ROM. It deletes the Android
`super` and `userdata` partitions and creates a new `linux_data` partition in
their place. Back up the GPT first and adapt the partition numbers after
`sgdisk -p /dev/sda`; the numbers below are specific to this unit.

```sh
sgdisk --backup=/root/gpt-backup.bin /dev/sda
sgdisk --delete=28 --delete=29 --delete=30 --delete=31 \
       --delete=32 --delete=33 --delete=34 --delete=35 /dev/sda
sgdisk --new=28:480768:36313087 --typecode=28:8300 --change-name=28:linux_data /dev/sda
partprobe /dev/sda
mkfs.ext4 -L LIUQIN_DATA /dev/sda28
mkdir -p /data
echo 'LABEL=LIUQIN_DATA /data ext4 defaults,nofail,x-systemd.device-timeout=30s 0 2' >> /etc/fstab
mount /data
```

The result on this unit was `/` 64 GiB, `/home` 271 GiB and `/data` 136.7 GiB.
Android can no longer boot after this; restoring it requires the full stock
firmware.

## Backups and rollback

Take these immediately after the first successful boot:

| Item | Command |
| --- | --- |
| Ubuntu boot image | `dd if=/dev/sde14 of=/root/boot-ubuntu.img bs=1M` |
| `vbmeta_a` as currently flashed | `dd if=/dev/sde17 of=/root/vbmeta-current.img` (back up the stock one before disabling verification) |
| GPTs | `sgdisk --backup=/root/gpt-sda.bin /dev/sda` and the same for `/dev/sde` |
| `misc` first 4 KiB | `dd if=/dev/sda11 of=/root/misc-first4k.bin bs=4k count=1` |

The minimal recovery from a non-booting slot is:

```sh
fastboot --disable-verity --disable-verification flash vbmeta_a vbmeta-stock.img
fastboot flash boot_a boot-ubuntu.img
fastboot --set-active=a
fastboot reboot
```

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Reboot returns to fastboot, `avb_slot_verify.c:210` | AVB short read | flash `vbmeta` with disable flags on both slots |
| Both slots unbootable, `fastboot boot` reports `Load Error` | slot state was marked dead | `fastboot --set-active=a` resets the slot state |
| Xiaomi logo loop, USB never appears | incompatible image (for example an Android header-v4 boot image) | hold power 12 s, then power+volume-down; flash the Ubuntu image back |
| Initramfs emergency shell, `native root rejected` | a contracted file was changed | restore it from the emergency shell |
| No sound after install | user not in `audio` | add the groups above |
| `systemctl reboot` refused | `unattended-upgrades` block inhibitor | mask the daily timers above |
| `/` or `/home` fails to mount or fsck | filesystem corruption | `e2fsck -y /dev/sda37` (or `sda36`) |

## Single-system versus dual-boot

Ubuntu needs the disabled-verification `vbmeta` described above. Whether Android
can boot with the same `vbmeta` was not established on this unit: the stock
boot, a KernelSU boot and a clean userdata all hung before USB initialised. If
Android requires the stock `vbmeta`, the two systems cannot share one
configuration, and switching would need a `vbmeta` write over fastboot each
time.

For that reason this unit ended up as Ubuntu-only with the Android space
reclaimed as `/data`. A dual-boot install should first prove that the Android
image actually boots under the `vbmeta` Ubuntu needs; do not assume it does.

## Provenance

The AVB short-read diagnosis comes from the bootloader log in `logfs` and from
comparing the stock and Ubuntu boot paths on this unit. Partition numbers and
sizes are from `sgdisk -p` and `lsblk` on the same tablet.
