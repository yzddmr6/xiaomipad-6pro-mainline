#!/bin/sh
# SPDX-License-Identifier: MIT
# Read old maintenance policy without opening storage; provision only public keys.
set -eu
BB=/bin/busybox
work=/run/liuqin-devmode
mkdir -p "$work"
case ${1:-} in
inspect)
 [ "$#" = 2 ] || exit 2
 case $2 in /dev/sda[0-9]*) ;; *) exit 2;; esac
 [ "$("$BB" blockdev --getro "$2")" = 1 ] || exit 1
 mkdir -p "$work/old"
 "$BB" mount -t ext4 -o ro,noload "$2" "$work/old"
 trap '"$BB" umount "$work/old"' EXIT
 trap 'exit 130' HUP INT TERM
 old=$work/old/native-root
 if [ -f "$old/etc/liuqin-rescue-enabled" ] ||
    [ -L "$old/etc/systemd/system/usb-gadget.target.wants/liuqin-devmode.service" ]; then
  if [ -f "$old/etc/liuqin/devmode/authorized_keys" ] &&
     [ ! -L "$old/etc/liuqin/devmode/authorized_keys" ]; then
   "$BB" cp "$old/etc/liuqin/devmode/authorized_keys" "$work/authorized_keys"
   "$BB" chmod 600 "$work/authorized_keys"
  fi
  echo enabled
 else
  echo disabled
 fi
 ;;
install)
 [ "$#" = 2 ] || exit 2
 [ -s "$work/authorized_keys" ] && [ ! -L "$work/authorized_keys" ] || {
  echo 'developer mode requires a staged host public key' >&2; exit 1;
 }
 [ -f "$2/etc/liuqin-native-root" ] || exit 1
 [ -f "$2/usr/lib/systemd/system/liuqin-devmode.service" ] || exit 1
 "$BB" mkdir -p "$2/etc/liuqin/devmode" "$2/etc/systemd/system/usb-gadget.target.wants"
 "$BB" cp "$work/authorized_keys" "$2/etc/liuqin/devmode/authorized_keys"
 "$BB" chmod 700 "$2/etc/liuqin/devmode"
 "$BB" chmod 600 "$2/etc/liuqin/devmode/authorized_keys"
 "$BB" ln -sfn /usr/lib/systemd/system/liuqin-devmode.service \
  "$2/etc/systemd/system/usb-gadget.target.wants/liuqin-devmode.service"
 "$BB" rm -f "$2/etc/liuqin-rescue-enabled"
 ;;
*) echo 'usage: install-devmode.sh inspect DEVICE | install ROOT' >&2; exit 2;;
esac
