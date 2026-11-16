# `liuqin_boot_ubuntu` — KernelSU module: Android → Ubuntu

Android-side half of the liuqin dual-boot switch. The Ubuntu-side half is the
same program, `/usr/local/sbin/liuqin-switch`, shipped in the
`liuqin-device-support` package together with a **Reboot to Android** desktop
entry.

## How switching works

**Both systems boot from slot A.** Switching writes the other system's boot
image into `boot_a`, reads it back and restarts the tablet. The active slot is
never changed.

Slot switching is not used because this bootloader does not switch slots by
attribute bits alone: it couples them to a type-GUID swap on every `_a`/`_b`
partition pair and to the UFS boot LUN, and a switch that changes only part of
that state leaves every image failing to load. Android also cannot run from
slot B at all, since `super` has room for one dynamic-partition set only.

The image that is not installed is kept on the Ubuntu root filesystem
(`linux_root`, inside its `native-root` directory):

```
/var/lib/liuqin/switch/
  ubuntu/    boot.img  SHA256SUMS  meta.json
  android/   boot.img  SHA256SUMS  meta.json
  state.json switch.log
```

`boot_b` holds a copy of the Ubuntu boot image. It is there only for the
bootloader's own fallback when `boot_a` does not load; nothing in this module
or in the switcher writes it.

## Contents

| Path | What it is |
|---|---|
| `module.prop` | KernelSU module metadata |
| `system/bin/liuqin-switch` | the switcher; packed from `device/gnome-overlay/usr/local/sbin/liuqin-switch` when the zip is built, so both systems run the same script |
| `system/bin/boot-ubuntu` | terminal command: confirmation, then `liuqin-switch to-ubuntu` |
| `service.sh` | boot-time guard that disables the Android system updater |
| `webroot/index.html` | the KernelSU WebUI page: the switcher's status plus one button |
| `META-INF/com/google/android/*` | stub that refuses a recovery sideload |

## What "Reboot to Ubuntu" does on Android

1. Mounts `/dev/block/by-name/linux_root` **read-only** (`ro,noload`) on
   `/mnt/liuqin-switch`. The Android kernel (5.10) cannot mount this ext4
   read-write, and the switch must not replay its journal either, so the
   Android side never writes the store.
2. Checks `ubuntu/boot.img` against its `SHA256SUMS` and against the header
   rule below, and that it fits `boot_a`.
3. Checks that `boot_a` holds exactly the image in `android/` — the way back.
   If Android's boot image has changed since it was archived (a KernelSU
   re-patch, a manually flashed image), the switch is refused and nothing is
   written, because overwriting `boot_a` would destroy the only copy. The new
   image then has to reach the store from the Ubuntu side with
   `liuqin-switch import-android <image>`.
4. Clears the kernel read-only flag of `boot_a` and its disk if set, writes the
   whole partition (image plus zero fill), syncs, restores the flag, reads the
   partition back and compares it. A mismatch writes the Android image back
   and stops without rebooting.
5. Unmounts the store and reboots with `svc power reboot`.

Images are told apart by their boot-image header: header version 2 with a
4096-byte page, a 1660-byte header and a DTB is the project's Ubuntu image;
header version 3 or 4 (header size 1580 or 1584) is Android; anything else is
refused.

From a root shell, the switcher can always be called by its module path,
whether or not KernelSU mounts the module's `system/` over `/system`:

```sh
su -c 'sh /data/adb/modules/liuqin_boot_ubuntu/system/bin/liuqin-switch status'
su -c 'sh /data/adb/modules/liuqin_boot_ubuntu/system/bin/boot-ubuntu'
```

`boot-ubuntu -n` reports what would be written without writing anything. The
switcher's own log goes to `/data/adb/ksu/log/liuqin-switch.log`, because the
store is read-only here; the WebUI calls the script by the same module path.

## OTA freeze

An Android over-the-air update is applied by the A/B update engine, which
writes the **inactive** slot — slot B — and then makes it active. That would
overwrite the fallback copy of the Ubuntu boot image and hand the next boot to
a slot from which Android cannot start.

`service.sh` therefore runs once per boot — KernelSU starts it in late start —
waits for `sys.boot_completed`, and disables the system updater for the primary
user:

```
pm disable-user --user 0 com.android.updater
```

It logs every decision to `/data/adb/ksu/log/liuqin-ota-freeze.log`, falling
back to the module directory when that directory does not exist, and keeps the
log bounded. The package list can be overridden with `LIUQIN_OTA_PACKAGES`.

Nothing about this is irreversible: `pm enable com.android.updater` brings the
application back, and uninstalling the module removes the script. Updating
Android on this tablet is a deliberate operation — restore the stock partition
table first, update, then install again.

## Install

Build the zip and install it from the KernelSU manager:

```
sh tools/build-ksu-module.sh          # -> out/ksu-module/liuqin_boot_ubuntu-v0.4.zip
```

Recovery sideload is deliberately rejected — it would unpack the files outside
KernelSU's module directory.

## Untested on hardware (待真机核实)

None of the following has run on the tablet yet. The switcher's decision and
verification logic is covered by `tests/switch-selftest.sh`, which runs it
against ordinary files and says nothing about the device.

- **The switch itself.** Writing `boot_a` from Android, the read-back after the
  page-cache flush, and the cold boot into Ubuntu from slot A have not been
  performed.
- **Read-only mount.** `mount -t ext4 -o ro,noload` from the KernelSU root
  shell, under Android's SELinux policy and mount namespaces, is untested.
- **Block-device access.** That toybox `blockdev` and `dd` can write `boot_a`
  from the KernelSU `su` domain is untested.
- **WebUI API shape.** The page calls `ksu.exec(command, optionsJson,
  callbackName)` and expects `window[callbackName](errno, stdout, stderr)`.
  This is the widely documented KernelSU WebUI bridge, but no local file in
  this repository documents it. If the manager's bridge differs, only `exec()`
  in `webroot/index.html` needs to change. Whether the callback arrives before
  the reboot is also untested; the terminal command is the fallback.
- **KernelSU flavour.** The stock kernel is GKI `5.10.209-android12-9`, KMI
  `android12-5.10`, and upstream KernelSU ships a matching loadable module for
  it. `tools/patch-android-boot-ksu.py` builds the patched Android boot image;
  the assets are pinned in `tools/lib/kernelsu-assets.json`. Whether the tablet
  boots that image, and whether the manager then reports root, is untested.
- **Slot suffix on Android.** The switcher reads `ro.boot.slot_suffix` to
  refuse a switch while running from slot B; the property has not been read on
  this tablet.
- **Updater package name.** `com.android.updater` is the system updater on this
  ROM generation, but it has not been read off the tablet. `service.sh` logs
  `is not installed for user 0` when the name is wrong, so the log answers the
  question on the first boot.
