#!/bin/sh
# SPDX-License-Identifier: MIT
# Run before the first persistent write, including any GPT or header wipe.
set -eu
die() { echo "liuqin-preflight: $*" >&2; exit 1; }
[ "$#" = 2 ] || die 'usage: install-preflight.sh BOOT_ID ARCHIVE_BYTES'
[ "$(cat /proc/sys/kernel/random/boot_id)" = "$1" ] || die 'RAM boot identity changed'
[ "$(cat /etc/liuqin-installer)" = liuqin ] || die 'not the RAM installer'
case $2 in ''|*[!0-9]*) die 'invalid archive size';; esac
battery=
for supply in /sys/class/power_supply/*; do
 [ "$(cat "$supply/type" 2>/dev/null)" = Battery ] || continue
 battery=$(cat "$supply/capacity"); break
done
case $battery in ''|*[!0-9]*) die 'battery level is unavailable';; esac
[ "$battery" -ge 30 ] || die 'charge to at least 30 percent before installation'
available=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
case $available in ''|*[!0-9]*) die 'available memory is unknown';; esac
[ "$2" -gt 0 ] && [ "$(( $2 + 536870912 ))" -lt "$((available * 1024))" ] || die 'insufficient RAM'
work=/run/liuqin-preflight
mkdir -p "$work/persist" "$work/root/etc"
mounted=false
cleanup() {
 if [ "$mounted" = true ]; then /bin/busybox umount "$work/persist" || return 1; fi
 rm -rf "$work/root"
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM
persist=/dev/disk/by-partlabel/persist
[ -b "$persist" ] || die 'persist partition is missing'
[ "$(/bin/busybox blockdev --getro "$persist")" = 1 ] || die 'persist is not read-only'
/bin/busybox mount -t ext4 -o ro,noload "$persist" "$work/persist"
mounted=true
# Exercise the real provisioning parser against RAM output. This validates
# addresses, calibration lengths and the complete sensor registry before GPT
# writes, rather than discovering malformed factory data after formatting.
echo liuqin-native-root-v1 >"$work/root/etc/liuqin-native-root"
echo 'fastrpc:x:100:100::/:/bin/false' >"$work/root/etc/passwd"
echo 'fastrpc:x:100:' >"$work/root/etc/group"
PERSIST_SRC=$work/persist sh /usr/lib/liuqin/provision.sh "$work/root" >/dev/null
cleanup
mounted=false
trap - EXIT HUP INT TERM
echo 'liuqin-preflight: battery, memory and factory data PASS'
