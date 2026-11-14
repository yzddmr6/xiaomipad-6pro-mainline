#!/bin/sh
# SPDX-License-Identifier: MIT
#
# Self-test for liuqin-switch's decision and verification logic.
#
# What this covers: the parts that decide *whether* to write and *whether a
# write was good* -- store validation against SHA256SUMS and the header rule,
# refusing to archive our own image as Android, refusing a switch without a
# verified way back, the whole-partition write with zero fill and read-back,
# the rollback when a read-back does not match or a write fails, signals
# during the write, the read-only store of the Android side, the fallback-slot
# refusal, dry-run writing nothing, and the kernel read-only flag of boot_a
# and its disk being restored on every exit path.
#
# What this does NOT cover, and cannot: anything about the real tablet.  The
# partitions are ordinary files (--dev-dir), the read-only flag is a sidecar
# file, and a reboot is a marker file.  A pass says nothing about UFS, the
# bootloader, the page-cache flush, or whether either system boots afterwards.
#
# The suite runs twice: once under dash with the host's coreutils, once under
# BusyBox sh with nothing but BusyBox applets on PATH (the closest host-side
# stand-in for Android's mksh and toybox).  The switcher insists on uid 0, so
# the suite re-executes itself in a user namespace when it is not root.

set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
project=$(CDPATH='' cd -- "$here/.." && pwd)
SWITCH=$project/device/gnome-overlay/usr/local/sbin/liuqin-switch

if [ "$(id -u)" != 0 ]; then
	command -v unshare >/dev/null 2>&1 || { echo 'switch-selftest: unshare is required' >&2; exit 1; }
	exec unshare -r "$0" "$@"
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM

pass=0
fail=0
ok() { pass=$((pass + 1)); printf 'ok   %s\n' "$*"; }
no() { fail=$((fail + 1)); printf 'FAIL %s\n' "$*"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else no "$1 (got '$2', want '$3')"; fi; }

MiB=1048576
BOOT_SIZE=$((4 * MiB))

sum() { sha256sum "$1" | cut -d' ' -f1; }

# sha256 of <image> zero-filled to the partition size: what boot_a must read.
padded() {
	{ cat "$1"; head -c $((BOOT_SIZE - $(stat -c %s "$1"))) /dev/zero; } |
		sha256sum | cut -d' ' -f1
}

put_u32() { # <file> <offset> <value>
	pu_v=$3
	# shellcheck disable=SC2059
	printf "$(printf '\\%03o\\%03o\\%03o\\%03o' $((pu_v % 256)) $((pu_v / 256 % 256)) \
		$((pu_v / 65536 % 256)) $((pu_v / 16777216 % 256)))" |
		dd of="$1" bs=1 seek="$2" conv=notrunc 2>/dev/null
}

put_text() { # <file> <offset> <text>
	printf '%s' "$3" | dd of="$1" bs=1 seek="$2" conv=notrunc 2>/dev/null
}

blank() { # <file> <bytes>
	rm -f "$1"
	dd if=/dev/zero of="$1" bs=4096 count=$(($2 / 4096)) 2>/dev/null
}

# The project's v2 image: header v2, page 4096, header_size 1660, a DTB.
make_ubuntu() { # <file> <tag>
	blank "$1" $((MiB + 8192))
	put_text "$1" 0 'ANDROID!'
	put_u32 "$1" 8 1000
	put_u32 "$1" 36 4096
	put_u32 "$1" 40 2
	put_u32 "$1" 1644 1660
	put_u32 "$1" 1648 5000
	put_text "$1" 4096 "ubuntu-$2"
}

# A stock-shaped GKI image: header v4, header_size 1584, partition-sized.
make_android() { # <file> <tag> [bytes]
	blank "$1" "${3:-$BOOT_SIZE}"
	put_text "$1" 0 'ANDROID!'
	put_u32 "$1" 8 46000
	put_u32 "$1" 20 1584
	put_u32 "$1" 40 4
	put_text "$1" 4096 "android-$2"
}

make_set() { # <dir> <kind> <tag>
	rm -rf "$1"
	mkdir -p "$1"
	"make_$2" "$1/boot.img" "$3"
	(cd "$1" && sha256sum boot.img >SHA256SUMS)
}

run_suite() { # <label> <shell>...
	label=$1
	shift
	case_dir=$work/$label
	dev=$case_dir/dev
	store=$case_dir/store
	mkdir -p "$dev" "$store"
mkdir -p "$dev/power_supply/battery"
printf 'Battery\n' > "$dev/power_supply/battery/type"
printf '80\n' > "$dev/power_supply/battery/capacity"
	printf '\n== %s ==\n' "$label"

	sw() { # run the switcher, capture output and status
		status=0
		out=$(env PATH="$SW_PATH" "$@" 2>&1) || status=$?
	}
	run() { sw "$SHELL_CMD" "$SWITCH" --store "$store" --dev-dir "$dev" "$@"; }

	# The initial state of a dual install before the first switch: the live
	# Android image in boot_a, a disk node, both sealed read-only by the
	# storage guard, and the installer's Ubuntu set in the store.
	make_android "$dev/boot_a" live
	: >"$dev/sde"
	echo 1 >"$dev/boot_a.ro"
	echo 1 >"$dev/sde.ro"
	printf 'console=ttyMSM0 androidboot.slot_suffix=_a\n' >"$dev/cmdline"
	make_set "$store/ubuntu" ubuntu one
	android_live=$(sum "$dev/boot_a")
	ubuntu_one=$(padded "$store/ubuntu/boot.img")

	sealed() { # <what>: both flags back to 1 and no lock left behind
		if [ "$(cat "$dev/boot_a.ro")" = 1 ] && [ "$(cat "$dev/sde.ro")" = 1 ] && [ ! -e "$dev/.lock" ]; then
			ok "$1: boot_a and its disk are read-only again, no lock left"
		else
			no "$1: flags boot_a=$(cat "$dev/boot_a.ro") sde=$(cat "$dev/sde.ro") lock=$([ -e "$dev/.lock" ] && echo yes || echo no)"
		fi
	}
	untouched() { # <what>: the read-only flags were never changed
		if [ ! -s "$dev/.ro-log" ]; then
			ok "$1: the read-only flags were never touched"
		else
			no "$1: the read-only flags changed: $(tr '\n' ' ' <"$dev/.ro-log")"
		fi
		rm -f "$dev/.ro-log"
	}
	refused() { # <what>
		if [ "$status" != 0 ]; then ok "$1: refused"; else no "$1: accepted ($out)"; fi
	}
	succeeded() { # <what>
		if [ "$status" = 0 ]; then ok "$1: succeeded"; else no "$1: status $status ($out)"; fi
	}
	says() { # <what> <fragment>
		case $out in *"$2"*) ok "$1" ;; *) no "$1 (output: $out)" ;; esac
	}
	rebooted() { # <what> <yes|no>
		if [ -e "$dev/.rebooted" ]; then check "$1: rebooted = $2" yes "$2"; else check "$1: rebooted = $2" no "$2"; fi
		rm -f "$dev/.rebooted"
	}

	# --- a first look ------------------------------------------------------
	run status
	succeeded 'status on a fresh dual install'
	says 'status: the live Android image is recognised as Android' \
		'an Android image that is not the one in the store [android]'
	says 'status: store/android is absent' 'store/android   absent'
	untouched 'status'

	run verify
	refused 'verify without an Android archive'

	# --- refusals before any write ------------------------------------------
	run to-android --no-reboot
	refused 'to-android without store/android'
	check 'to-android refusal: boot_a unchanged' "$(sum "$dev/boot_a")" "$android_live"
	untouched 'to-android refusal'

	log_before=$(sum "$store/switch.log")
	run to-ubuntu --dry-run
	succeeded 'to-ubuntu --dry-run'
	check 'dry-run: boot_a unchanged' "$(sum "$dev/boot_a")" "$android_live"
	if [ -e "$store/android" ] || [ -e "$store/state.json" ] ||
		[ "$(sum "$store/switch.log")" != "$log_before" ]; then
		no 'dry-run: wrote to the store'
	else
		ok 'dry-run: wrote nothing to the store, not even the log'
	fi
	untouched 'dry-run'
	rebooted 'dry-run' no

	# The Android side: the store is read-only, and the live image is not in it.
	run to-ubuntu --read-only-store
	refused 'to-ubuntu from a read-only store with an unarchived Android image'
	says 'read-only refusal names the reason' 'the store is read-only here'
	check 'read-only refusal: boot_a unchanged' "$(sum "$dev/boot_a")" "$android_live"
	untouched 'read-only refusal'
	rebooted 'read-only refusal' no

	# A store that is not what SHA256SUMS says, or not of its own kind.
	printf 'tampered' | dd of="$store/ubuntu/boot.img" bs=1 seek=8192 conv=notrunc 2>/dev/null
	run to-ubuntu
	refused 'to-ubuntu with a boot.img that does not match SHA256SUMS'
	make_set "$store/ubuntu" ubuntu one
	sed -i "s/^[0-9a-f]\{64\}/$(printf '%064d' 0)/" "$store/ubuntu/SHA256SUMS"
	run to-ubuntu
	refused 'to-ubuntu with a wrong SHA256SUMS'
	rm "$store/ubuntu/SHA256SUMS"
	run to-ubuntu
	refused 'to-ubuntu without SHA256SUMS'
	make_set "$store/ubuntu" android wrongkind
	run to-ubuntu
	refused 'to-ubuntu with an Android image in store/ubuntu'
	says 'wrong-kind refusal names the reason' 'does not identify as a ubuntu boot image'
	make_set "$store/ubuntu" ubuntu one
	check 'every store refusal left boot_a alone' "$(sum "$dev/boot_a")" "$android_live"
	untouched 'store refusals'
	rebooted 'store refusals' no

	# --- archive, then switch to Ubuntu -------------------------------------
	run stash-android
	succeeded 'stash-android of the live Android image'
	check 'stash: boot.img archived byte for byte' "$(sum "$store/android/boot.img")" "$android_live"
	check 'stash: SHA256SUMS written' "$(sed -n 's/  boot.img$//p' "$store/android/SHA256SUMS")" "$android_live"
	if [ -f "$store/android/meta.json" ]; then ok 'stash: meta.json written'; else no 'stash: no meta.json'; fi
	untouched 'stash'

	run to-ubuntu
	succeeded 'to-ubuntu'
	check 'to-ubuntu: boot_a holds the Ubuntu image, zero-filled' "$(sum "$dev/boot_a")" "$ubuntu_one"
	check 'to-ubuntu: flags cleared disk-first and restored partition-first' \
		"$(tr '\n' ' ' <"$dev/.ro-log")" 'sde 0 boot_a 0 boot_a 1 sde 1 '
	rm -f "$dev/.ro-log"
	sealed 'to-ubuntu'
	rebooted 'to-ubuntu' yes
	check 'to-ubuntu: state.json records ubuntu' \
		"$(sed -n 's/.*"installed": *"\([^"]*\)".*/\1/p' "$store/state.json")" ubuntu
	run status
	says 'status: recognises the Ubuntu image from the store' 'the Ubuntu image from the store [ubuntu]'

	# --- the guard that protects the only way back --------------------------
	run stash-android
	refused 'stash-android while boot_a holds the Ubuntu image'
	check 'the Android archive survived the refusal' "$(sum "$store/android/boot.img")" "$android_live"

	make_ubuntu "$work/other-ubuntu.img" two
	dd if="$work/other-ubuntu.img" of="$dev/boot_a" conv=notrunc 2>/dev/null
	run stash-android
	refused 'stash-android while boot_a holds another Ubuntu build'
	run status
	says 'status: another Ubuntu build is recognised as Ubuntu' \
		'an Ubuntu image that is not the one in the store [ubuntu]'
	run to-ubuntu --no-reboot
	succeeded 'to-ubuntu over another Ubuntu build'
	says 'refresh leaves store/android alone' 'store/android unchanged'
	check 'refresh: boot_a holds the store Ubuntu image' "$(sum "$dev/boot_a")" "$ubuntu_one"
	check 'refresh: the Android archive is unchanged' "$(sum "$store/android/boot.img")" "$android_live"
	rm -f "$dev/.ro-log"
	sealed 'refresh'
	rebooted 'refresh with --no-reboot' no

	# --- and back ------------------------------------------------------------
	run to-android
	succeeded 'to-android'
	check 'to-android: boot_a holds Android again' "$(sum "$dev/boot_a")" "$android_live"
	rm -f "$dev/.ro-log"
	sealed 'to-android'
	rebooted 'to-android' yes
	run verify
	succeeded 'verify with both sets and Android installed'

	# The Android side again, now with its image archived: it may switch
	# without writing the store, and state.json stays as Ubuntu last wrote it.
	run to-ubuntu --read-only-store
	succeeded 'to-ubuntu from a read-only store with the Android image archived'
	check 'read-only switch: boot_a holds Ubuntu' "$(sum "$dev/boot_a")" "$ubuntu_one"
	check 'read-only switch: state.json not written' \
		"$(sed -n 's/.*"installed": *"\([^"]*\)".*/\1/p' "$store/state.json")" android
	rm -f "$dev/.ro-log"
	sealed 'read-only switch'
	rebooted 'read-only switch' yes
	run to-android --no-reboot
	succeeded 'back to Android'
	rm -f "$dev/.ro-log"

	# --- a write the medium did not keep ------------------------------------
	: >"$dev/.fault-corrupt"
	run to-ubuntu
	refused 'to-ubuntu with a read-back mismatch'
	says 'mismatch: says it did not reboot' 'NOT rebooted'
	check 'mismatch: boot_a holds the Android image again' "$(sum "$dev/boot_a")" "$android_live"
	rm -f "$dev/.ro-log"
	sealed 'read-back mismatch'
	rebooted 'read-back mismatch' no

	: >"$dev/.fault-write"
	run to-ubuntu
	refused 'to-ubuntu with a failed write'
	check 'failed write: boot_a holds the Android image again' "$(sum "$dev/boot_a")" "$android_live"
	rm -f "$dev/.ro-log"
	sealed 'failed write'
	rebooted 'failed write' no

	: >"$dev/.fault-setrw"
	run to-ubuntu
	refused 'to-ubuntu when boot_a cannot be made writable'
	check 'setrw failure: boot_a unchanged' "$(sum "$dev/boot_a")" "$android_live"
	check 'setrw failure: the disk flag was put back' \
		"$(tr '\n' ' ' <"$dev/.ro-log")" 'sde 0 boot_a 1 sde 1 '
	rm -f "$dev/.ro-log"
	sealed 'setrw failure'
	rebooted 'setrw failure' no

	# A terminate signal in the middle of the write is ignored: the write
	# completes and verifies instead of leaving boot_a half-written.
	: >"$dev/.fault-signal"
	run to-ubuntu --no-reboot
	succeeded 'to-ubuntu with a terminate signal during the write'
	check 'signal: boot_a holds the complete Ubuntu image' "$(sum "$dev/boot_a")" "$ubuntu_one"
	rm -f "$dev/.ro-log"
	sealed 'signal during the write'
	run to-android --no-reboot
	rm -f "$dev/.ro-log"

	# --- the Android side with a changed Android image ----------------------
	make_android "$dev/boot_a" repatched
	repatched=$(sum "$dev/boot_a")
	run to-android --no-reboot
	refused 'to-android over an Android image that is not in the store'
	run to-ubuntu --read-only-store
	refused 'to-ubuntu from a read-only store after Android changed its boot image'
	check 'changed Android image kept' "$(sum "$dev/boot_a")" "$repatched"
	untouched 'changed Android image'
	run to-ubuntu --no-reboot
	succeeded 'to-ubuntu from a writable store archives the new Android image first'
	check 'the new Android image is archived' "$(sum "$store/android/boot.img")" "$repatched"
	rm -f "$dev/.ro-log"
	sealed 'archive then switch'

	# --- import-android ------------------------------------------------------
	make_ubuntu "$work/not-android.img" three
	run import-android "$work/not-android.img"
	refused 'import-android of a v2 image'
	check 'refused import left store/android alone' "$(sum "$store/android/boot.img")" "$repatched"
	make_android "$work/rom-boot.img" rom $((2 * MiB))
	run import-android "$work/rom-boot.img"
	succeeded 'import-android of a v4 image shorter than the partition'
	check 'import: boot.img copied' "$(sum "$store/android/boot.img")" "$(sum "$work/rom-boot.img")"
	check 'import: SHA256SUMS written' "$(sed -n 's/  boot.img$//p' "$store/android/SHA256SUMS")" \
		"$(sum "$work/rom-boot.img")"
	run to-android --no-reboot
	succeeded 'to-android with a short imported image'
	check 'the short image is zero-filled to the partition' "$(sum "$dev/boot_a")" "$(padded "$work/rom-boot.img")"
	run verify
	succeeded 'verify recognises a zero-filled short image'
	rm -f "$dev/.ro-log"

	# --- no way back, no switch ----------------------------------------------
	run to-ubuntu --no-reboot
	rm -f "$dev/.ro-log"
	mv "$store/ubuntu" "$store/ubuntu.away"
	run to-android --no-reboot
	refused 'to-android while store/ubuntu is missing'
	says 'the refusal names the missing way back' 'no way back to Ubuntu'
	check 'no-way-back refusal: boot_a unchanged' "$(sum "$dev/boot_a")" "$ubuntu_one"
	untouched 'no-way-back refusal'
	mv "$store/ubuntu.away" "$store/ubuntu"

	# --- running on the fallback copy in slot B -----------------------------
	printf 'androidboot.slot_suffix=_b\n' >"$dev/cmdline"
	run to-android --no-reboot
	refused 'to-android while running from slot B'
	says 'the slot-B refusal explains the fastboot repair' 'fastboot --set-active=a'
	check 'slot-B refusal: boot_a unchanged' "$(sum "$dev/boot_a")" "$ubuntu_one"
	untouched 'slot-B refusal'
	printf 'androidboot.slot_suffix=_a\n' >"$dev/cmdline"

	# --- images that are neither --------------------------------------------
	blank "$dev/boot_a" "$BOOT_SIZE"
	run to-android --no-reboot
	refused 'to-android over an erased boot_a'
	run to-ubuntu --no-reboot
	refused 'to-ubuntu over an erased boot_a'
	run stash-android
	refused 'stash-android of an erased boot_a'
	make_ubuntu "$dev/boot_a" nodtb
	put_u32 "$dev/boot_a" 1648 0
	run status
	says 'a v2 image without a DTB is not ours' 'identifies as neither system [unknown]'
	untouched 'unknown images'

	# --- one switch at a time -------------------------------------------------
	mkdir -p "$dev/.lock"
	run status
	refused 'a second instance while the lock is held'
	rmdir "$dev/.lock"
}

# dash with coreutils
SW_PATH=$PATH
SHELL_CMD=$(command -v dash || command -v sh)
run_suite dash

# BusyBox sh with only BusyBox applets
if command -v busybox >/dev/null 2>&1; then
	bb=$(command -v busybox)
	bbbin=$work/bbbin
	mkdir -p "$bbbin"
	for applet in dirname mktemp sh cat cp cut date dd env head id kill ls mkdir mv od printf readlink \
		rm rmdir sed sha256sum sleep stat sync tr grep; do
		ln -s "$bb" "$bbbin/$applet"
	done
	SW_PATH=$bbbin
	SHELL_CMD="$bbbin/sh"
	run_suite busybox
else
	echo 'switch-selftest: BusyBox is unavailable; the BusyBox pass was skipped' >&2
	no 'BusyBox pass (busybox not installed)'
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" = 0 ]
