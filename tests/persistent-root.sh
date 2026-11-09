#!/bin/sh
# Host-side fail-closed tests for initramfs persistent-root selection.

set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
init=$project_root/initramfs/init
host_busybox=${HOST_BUSYBOX:-$(command -v busybox)}
[ -x "$host_busybox" ] || { printf '%s\n' 'test-liuqin-persistent-root: host BusyBox is required' >&2; exit 1; }
test_root=$(mktemp -d)
cleanup() { rm -rf "$test_root"; }
trap cleanup EXIT HUP INT TERM

fail() { printf 'test-liuqin-persistent-root: %s\n' "$*" >&2; exit 1; }

make_fake_commands() {
	mkdir -p "$test_root/bin"
	cat >"$test_root/bin/liuqin-physical-platform" <<'EOF'
#!/bin/sh
printf 'physical-platform %s\n' "$*" >>"$TEST_COMMAND_LOG"
exit "${TEST_PLATFORM_FAIL:-0}"
EOF
	cat >"$test_root/bin/findfs" <<'EOF'
#!/bin/sh
case $1 in
LABEL=LIUQIN_ROOT)
	[ "${TEST_FINDFS_FAIL:-0}" != 1 ] || exit 1
	printf '%s\n' "$TEST_LABEL_PATH" ;;
LABEL=LIUQIN_HOME)
	[ -n "${TEST_HOME_LABEL_PATH:-}" ] || exit 1
	printf '%s\n' "$TEST_HOME_LABEL_PATH" ;;
*) exit 2 ;;
esac
EOF
	cat >"$test_root/bin/blockdev" <<'EOF'
#!/bin/sh
set -u
op=$1; node=$2; key=$(basename "$node")
printf '%s %s\n' "$op" "$node" >>"$TEST_COMMAND_LOG"
case "$op:$key" in
	--setrw:$TEST_FAIL_SETRW) exit 1 ;;
	--setro:$TEST_FAIL_SETRO) exit 1 ;;
	--getro:$TEST_FAIL_GETRO) exit 1 ;;
esac
case $op in
	--setro) printf '1\n' >"$TEST_STATE/$key" ;;
	--setrw)
		printf '0\n' >"$TEST_STATE/$key"
		if [ "${TEST_MUTATE_AFTER_SETRW:-none}" != none ] &&
			[ ! -e "$TEST_MUTATED" ]; then
			: >"$TEST_MUTATED"
			case $TEST_MUTATE_AFTER_SETRW in
			add) : >"$TEST_DEV/sdc"; printf '1\n' >"$TEST_STATE/sdc" ;;
			remove) rm -f "$TEST_DEV/sdb" "$TEST_STATE/sdb" ;;
			remove-target) rm -f "$TEST_DEV/sda35" "$TEST_STATE/sda35" ;;
			alias) mv "$TEST_DEV/sdb" "$TEST_DEV/real-sdb"; ln -s real-sdb "$TEST_DEV/sdb" ;;
			esac
		fi
		;;
	--getro) cat "$TEST_STATE/$key" ;;
	*) exit 2 ;;
esac
EOF
	cat >"$test_root/bin/mount" <<'EOF'
#!/bin/sh
set -u
count=0
[ ! -r "$TEST_MOUNT_COUNT" ] || count=$(cat "$TEST_MOUNT_COUNT")
count=$((count + 1)); printf '%s\n' "$count" >"$TEST_MOUNT_COUNT"
printf 'mount%d %s\n' "$count" "$*" >>"$TEST_COMMAND_LOG"
if [ "$count" = "${TEST_MUTATE_AFTER_MOUNT_NUMBER:-0}" ]; then
	case ${TEST_MUTATE_AFTER_MOUNT:-none} in
	add) : >"$TEST_DEV/sdc"; printf '1\n' >"$TEST_STATE/sdc" ;;
	remove) rm -f "$TEST_DEV/sdb" "$TEST_STATE/sdb" ;;
	alias) mv "$TEST_DEV/sdb" "$TEST_DEV/real-sdb"; ln -s real-sdb "$TEST_DEV/sdb" ;;
	esac
fi
[ "$count" != "${TEST_FAIL_MOUNT_NUMBER:-0}" ]
EOF
	cat >"$test_root/bin/umount" <<'EOF'
#!/bin/sh
count=0
[ ! -r "$TEST_UMOUNT_COUNT" ] || count=$(cat "$TEST_UMOUNT_COUNT")
count=$((count + 1)); printf '%s\n' "$count" >"$TEST_UMOUNT_COUNT"
printf 'umount %s\n' "$*" >>"$TEST_COMMAND_LOG"
[ "${TEST_FAIL_UMOUNT:-0}" != 1 ] || exit 1
for fail_count in ${TEST_FAIL_UMOUNT_NUMBER:-0}; do
	[ "$count" != "$fail_count" ] || exit 1
done
exit 0
EOF
	cat >"$test_root/bin/chroot" <<'EOF'
#!/bin/sh
count=0
[ ! -r "$TEST_CHROOT_COUNT" ] || count=$(cat "$TEST_CHROOT_COUNT")
count=$((count + 1)); printf '%s\n' "$count" >"$TEST_CHROOT_COUNT"
printf 'chroot %s\n' "$*" >>"$TEST_COMMAND_LOG"
	if [ "$count" = "${TEST_MUTATE_RCS_ON_CHROOT:-0}" ]; then
		case ${TEST_MUTATE_RCS:-none} in
		missing) rm -f "$TEST_NEWROOT/etc/init.d/rcS" ;;
		mode) chmod 0644 "$TEST_NEWROOT/etc/init.d/rcS"; printf '644 0 0\n' >"$TEST_RCS_STAT_FILE" ;;
		stale) printf '#!/bin/sh\necho stale\n' >"$TEST_NEWROOT/etc/init.d/rcS" ;;
		alias) mv "$TEST_NEWROOT/etc/init.d/rcS" "$TEST_NEWROOT/etc/init.d/real-rcS"; ln -s real-rcS "$TEST_NEWROOT/etc/init.d/rcS" ;;
		esac
	fi
	if [ "$count" = "${TEST_MUTATE_PID1_ON_CHROOT:-0}" ]; then
		case ${TEST_MUTATE_PID1:-none} in
		inittab-missing) rm -f "$TEST_NEWROOT/etc/inittab" ;;
		inittab-stale) printf '# changed\n' >"$TEST_NEWROOT/etc/inittab" ;;
		inittab-alias) mv "$TEST_NEWROOT/etc/inittab" "$TEST_NEWROOT/etc/real-inittab"; ln -s real-inittab "$TEST_NEWROOT/etc/inittab" ;;
		busybox-missing) rm -f "$TEST_NEWROOT/usr/local/bin/busybox" ;;
		busybox-stale) printf '# changed\n' >"$TEST_NEWROOT/usr/local/bin/busybox" ;;
		busybox-alias) mv "$TEST_NEWROOT/usr/local/bin/busybox" "$TEST_NEWROOT/usr/local/bin/real-busybox"; ln -s real-busybox "$TEST_NEWROOT/usr/local/bin/busybox" ;;
		sbin-link) rm "$TEST_NEWROOT/sbin"; ln -s usr/local "$TEST_NEWROOT/sbin" ;;
		init-link) rm "$TEST_NEWROOT/usr/sbin/init"; ln -s /bin/sh "$TEST_NEWROOT/usr/sbin/init" ;;
		bash-stale) printf '# changed\n' >"$TEST_NEWROOT/bin/bash" ;;
		loader-stale) printf '# changed\n' >"$TEST_NEWROOT/lib/ld-linux-aarch64.so.1" ;;
		tool-stale) printf '# changed\n' >"$TEST_NEWROOT/usr/bin/stat" ;;
		esac
	fi
	if [ "$count" = "${TEST_MUTATE_GNOME_ON_CHROOT:-0}" ]; then
		case ${TEST_MUTATE_GNOME:-none} in
		marker-missing) rm -f "$TEST_NEWROOT/etc/liuqin-gnome-root" ;;
		file-stale) printf 'changed\n' >"$TEST_NEWROOT/usr/bin/gnome-shell" ;;
		file-symlink) mv "$TEST_NEWROOT/usr/bin/gnome-shell" "$TEST_NEWROOT/usr/bin/real-gnome-shell"; ln -s real-gnome-shell "$TEST_NEWROOT/usr/bin/gnome-shell" ;;
		init-link) rm -f "$TEST_NEWROOT/usr/sbin/init"; ln -s /bin/sh "$TEST_NEWROOT/usr/sbin/init" ;;
		esac
	fi
	if [ "$count" = "${TEST_MUTATE_NATIVE_ON_CHROOT:-0}" ]; then
		case ${TEST_MUTATE_NATIVE:-none} in
		marker-missing) rm -f "$TEST_NEWROOT/etc/liuqin-native-root" ;;
		file-stale) printf 'changed\n' >"$TEST_NEWROOT/usr/libexec/iio-sensor-proxy" ;;
		esac
	fi
[ "${TEST_FAIL_CHROOT:-0}" != 1 ] &&
	[ "$count" != "${TEST_FAIL_CHROOT_NUMBER:-0}" ]
EOF
	cat >"$test_root/bin/switch_root" <<'EOF'
#!/bin/sh
printf 'switch_root %s\n' "$*" >>"$TEST_COMMAND_LOG"
exit "${TEST_SWITCH_ROOT_STATUS:-1}"
EOF
	cat >"$test_root/bin/stat" <<'EOF'
#!/bin/sh
# Only the GNOME per-file authority check uses the bare owner format; host
# fixtures cannot chown to root, so answer it from the fixture instead.
if [ "${2:-}" = '%u %g' ]; then
	printf '%s\n' "${TEST_GNOME_OWNER:-0 0}"
	exit 0
fi
if [ "${2:-}" = '%a' ]; then
	exec /usr/bin/stat "$@"
fi
case ${3:-} in
	*/etc/liuqin-native-root) printf '%s\n' "${TEST_NATIVE_MARKER_STAT:-644 0 0 22}" ;;
	*/etc/liuqin-gnome-root) printf '%s\n' "${TEST_GNOME_MARKER_STAT:-644 0 0 21}" ;;
	*/liuqin-root-profile) printf '%s\n' "${TEST_PROFILE_STAT:-644 0 0}" ;;
	*/etc/liuqin-persistent-root) printf '%s\n' "$TEST_MARKER_STAT" ;;
	*/etc/init.d/rcS) cat "$TEST_RCS_STAT_FILE" ;;
	*/liuqin-persistent-root.contract) printf '%s\n' "$TEST_ROOT_CONTRACT_STAT" ;;
	*/etc/inittab) printf '%s\n' "$TEST_INITTAB_STAT" ;;
	*/usr/local/bin/busybox) printf '%s\n' "$TEST_BUSYBOX_STAT" ;;
	*/usr/sbin/init) printf '%s\n' "$TEST_INIT_STAT" ;;
	*/sbin) printf '%s\n' "$TEST_SBIN_STAT" ;;
	*) exec /usr/bin/stat "$@" ;;
esac
EOF
	cat >"$test_root/bin/busybox" <<'EOF'
#!/bin/sh
command=$1
shift
printf 'busybox %s\n' "$command $*" >>"$TEST_COMMAND_LOG"
exec "$TEST_FAKE_BIN/$command" "$@"
EOF
	for applet in cut grep head readlink sha256sum; do
		cat >"$test_root/bin/$applet" <<EOF
#!/bin/sh
exec /usr/bin/$applet "\$@"
EOF
	done
	chmod 0755 "$test_root/bin"/*
}

reset_fixture() {
	rm -rf "$test_root/case"
	case_root=$test_root/case
	dev=$case_root/dev
	sys=$case_root/sys/class/block
	probe=$case_root/probe
	newroot=$case_root/newroot
	state=$case_root/state
	mkdir -p "$dev" "$sys/sda/queue" "$sys/sda35" "$probe/etc" \
		"$probe/usr/local/bin" "$probe/usr/sbin" "$probe/bin" "$probe/lib" \
		"$probe/usr/bin" "$newroot/etc" "$newroot/usr/local/bin" \
		"$newroot/usr/sbin" "$newroot/bin" "$newroot/lib" "$newroot/usr/bin" \
		"$newroot/dev" "$state"
	for node in sda sda1 sda35 sdb; do
		: >"$dev/$node"
		: >"$newroot/dev/$node"
		printf '1\n' >"$state/$node"
	done
	printf '493854720\n' >"$sys/sda/size"
	printf '4096\n' >"$sys/sda/queue/logical_block_size"
	printf '35\n' >"$sys/sda35/partition"
	printf '22065152\n' >"$sys/sda35/start"
	printf '471789528\n' >"$sys/sda35/size"
	printf 'PARTNAME=userdata\n' >"$sys/sda35/uevent"
	: >"$case_root/mounts"
	for root in "$probe" "$newroot"; do
		printf 'LIUQIN_PERSISTENT_ROOT_V1\n' >"$root/etc/liuqin-persistent-root"
		printf '# inittab\n' >"$root/etc/inittab"
		mkdir -p "$root/etc/init.d"
		printf '#!/usr/local/bin/busybox sh\nexit 0\n' >"$root/etc/init.d/rcS"
		chmod 0755 "$root/etc/init.d/rcS"
		printf '#!/bin/sh\nexit 0\n' >"$root/usr/local/bin/busybox"
		chmod 0755 "$root/usr/local/bin/busybox"
		printf '#!/bin/sh\nexit 0\n' >"$root/bin/bash"
		printf 'loader\n' >"$root/lib/ld-linux-aarch64.so.1"
		printf 'tool\n' >"$root/usr/bin/stat"
		ln -s usr/sbin "$root/sbin"
		ln -s /usr/local/bin/busybox "$root/usr/sbin/init"
	done
	command_log=$case_root/commands
	mount_count=$case_root/mount-count
	umount_count=$case_root/umount-count
	chroot_count=$case_root/chroot-count
	root_contract=$case_root/liuqin-persistent-root.contract
	{
		printf '%s\n' 'LIUQIN_PERSISTENT_ROOT_CONTRACT_V2'
		printf 'rootfs_sha256=%064d\n' 0
		printf 'rcs_sha256=%s\n' "$(sha256sum "$probe/etc/init.d/rcS" | cut -d' ' -f1)"
		printf 'inittab_sha256=%s\n' "$(sha256sum "$probe/etc/inittab" | cut -d' ' -f1)"
		printf 'busybox_sha256=%s\n' "$(sha256sum "$probe/usr/local/bin/busybox" | cut -d' ' -f1)"
		printf '%s\n' 'sbin_link=usr/sbin'
		printf '%s\n' 'usr_sbin_init_link=/usr/local/bin/busybox'
		printf '%s\n' 'rcs_interpreter=/usr/local/bin/busybox sh'
	} >"$root_contract"
	rcs_stat=$case_root/rcs-stat
	printf '755 0 0\n' >"$rcs_stat"
	mutated=$case_root/mutated
	init_log=$case_root/init-log
	: >"$init_log"
	: >"$command_log"
	# The nodes every abort path must have put back into the sealed baseline.
	# A split-layout fixture adds its linux_home node to this list.
	ro_nodes='sda sda1 sda35 sdb'
	TEST_HOME_LABEL_PATH=
	TEST_LABEL_PATH=$dev/sda35
	TEST_MARKER_STAT='644 0 0 26'
	TEST_FINDFS_FAIL=0 TEST_FAIL_SETRW=none TEST_FAIL_SETRO=none \
		TEST_FAIL_GETRO=none TEST_FAIL_MOUNT_NUMBER=0 TEST_FAIL_UMOUNT=0 \
		TEST_FAIL_UMOUNT_NUMBER=0 TEST_FAIL_CHROOT=0 TEST_FAIL_CHROOT_NUMBER=0 \
		TEST_MUTATE_AFTER_SETRW=none TEST_MUTATE_AFTER_MOUNT_NUMBER=0 \
		TEST_MUTATE_AFTER_MOUNT=none TEST_MUTATE_RCS_ON_CHROOT=0 TEST_MUTATE_RCS=none \
		TEST_MUTATE_PID1_ON_CHROOT=0 TEST_MUTATE_PID1=none \
		TEST_STORAGE_PHASE=prepare TEST_SWITCH_ROOT_STATUS=1
	TEST_ROOT_CONTRACT_STAT='644 0 0 453'
	TEST_INITTAB_STAT='644 0 0' TEST_BUSYBOX_STAT='755 0 0'
	TEST_SBIN_STAT='777 0 0' TEST_INIT_STAT='777 0 0'
	TEST_PROFILE_STAT='644 0 0' TEST_GNOME_MARKER_STAT='644 0 0 21'
	TEST_GNOME_OWNER='0 0' TEST_MUTATE_GNOME_ON_CHROOT=0 TEST_MUTATE_GNOME=none
	TEST_NATIVE_MARKER_STAT='644 0 0 22'
	TEST_MUTATE_NATIVE_ON_CHROOT=0 TEST_MUTATE_NATIVE=none
	LIUQIN_STORAGE_PROFILE_FILE= LIUQIN_STORAGE_GNOME_CONTRACT= LIUQIN_STORAGE_NATIVE_CONTRACT=
	export case_root dev sys probe newroot state command_log mount_count umount_count
	export init_log ro_nodes TEST_HOME_LABEL_PATH
	export chroot_count
	export root_contract rcs_stat mutated
	export TEST_LABEL_PATH TEST_MARKER_STAT TEST_FINDFS_FAIL TEST_FAIL_SETRW
	export TEST_FAIL_SETRO TEST_FAIL_GETRO TEST_FAIL_MOUNT_NUMBER
	export TEST_FAIL_UMOUNT TEST_FAIL_UMOUNT_NUMBER TEST_FAIL_CHROOT
	export TEST_FAIL_CHROOT_NUMBER
	export TEST_MUTATE_AFTER_SETRW TEST_MUTATE_AFTER_MOUNT_NUMBER TEST_MUTATE_AFTER_MOUNT
	export TEST_MUTATE_RCS_ON_CHROOT TEST_MUTATE_RCS TEST_MUTATE_PID1_ON_CHROOT TEST_MUTATE_PID1
	export TEST_STORAGE_PHASE TEST_SWITCH_ROOT_STATUS
	export TEST_ROOT_CONTRACT_STAT TEST_INITTAB_STAT TEST_BUSYBOX_STAT TEST_SBIN_STAT TEST_INIT_STAT
	export TEST_PROFILE_STAT TEST_GNOME_MARKER_STAT TEST_GNOME_OWNER
	export TEST_MUTATE_GNOME_ON_CHROOT TEST_MUTATE_GNOME
	export LIUQIN_STORAGE_PROFILE_FILE LIUQIN_STORAGE_GNOME_CONTRACT
	export TEST_NATIVE_MARKER_STAT TEST_MUTATE_NATIVE_ON_CHROOT TEST_MUTATE_NATIVE
	export LIUQIN_STORAGE_NATIVE_CONTRACT
}

run_init() {
	env \
		LIUQIN_INIT_STORAGE_TEST_ONLY=1 \
		LIUQIN_INIT_STORAGE_TEST_PHASE="$TEST_STORAGE_PHASE" \
		LIUQIN_INIT_TEST_PATH="$test_root/bin:/usr/bin:/bin" \
		LIUQIN_INIT_TEST_BUSYBOX="$test_root/bin/busybox" \
		LIUQIN_INIT_TEST_LOG="$init_log" \
		TEST_HOME_LABEL_PATH="${TEST_HOME_LABEL_PATH:-}" \
		LIUQIN_STORAGE_DEV_ROOT="$dev" \
		LIUQIN_STORAGE_SYS_BLOCK="$sys" \
		LIUQIN_STORAGE_MOUNTS="$case_root/mounts" \
		LIUQIN_STORAGE_PROBE_ROOT="$probe" \
		LIUQIN_STORAGE_NEWROOT="$newroot" \
		LIUQIN_STORAGE_ROOT_CONTRACT="$root_contract" \
		LIUQIN_STORAGE_PROFILE_FILE="${LIUQIN_STORAGE_PROFILE_FILE:-}" \
		LIUQIN_STORAGE_GNOME_CONTRACT="${LIUQIN_STORAGE_GNOME_CONTRACT:-}" \
		LIUQIN_STORAGE_NATIVE_CONTRACT="${LIUQIN_STORAGE_NATIVE_CONTRACT:-}" \
		TEST_NATIVE_MARKER_STAT="$TEST_NATIVE_MARKER_STAT" \
		TEST_MUTATE_NATIVE_ON_CHROOT="$TEST_MUTATE_NATIVE_ON_CHROOT" \
		TEST_MUTATE_NATIVE="$TEST_MUTATE_NATIVE" \
		TEST_PROFILE_STAT="$TEST_PROFILE_STAT" \
		TEST_GNOME_MARKER_STAT="$TEST_GNOME_MARKER_STAT" \
		TEST_GNOME_OWNER="$TEST_GNOME_OWNER" \
		TEST_MUTATE_GNOME_ON_CHROOT="$TEST_MUTATE_GNOME_ON_CHROOT" \
		TEST_MUTATE_GNOME="$TEST_MUTATE_GNOME" \
		TEST_LABEL_PATH="$TEST_LABEL_PATH" TEST_MARKER_STAT="$TEST_MARKER_STAT" \
		TEST_FINDFS_FAIL="$TEST_FINDFS_FAIL" TEST_FAIL_SETRW="$TEST_FAIL_SETRW" \
		TEST_FAIL_SETRO="$TEST_FAIL_SETRO" TEST_FAIL_GETRO="$TEST_FAIL_GETRO" \
		TEST_FAIL_MOUNT_NUMBER="$TEST_FAIL_MOUNT_NUMBER" \
		TEST_FAIL_UMOUNT="$TEST_FAIL_UMOUNT" \
		TEST_FAIL_UMOUNT_NUMBER="$TEST_FAIL_UMOUNT_NUMBER" \
		TEST_FAIL_CHROOT="$TEST_FAIL_CHROOT" \
		TEST_FAIL_CHROOT_NUMBER="$TEST_FAIL_CHROOT_NUMBER" \
		TEST_STATE="$state" TEST_COMMAND_LOG="$command_log" \
		TEST_MOUNT_COUNT="$mount_count" TEST_UMOUNT_COUNT="$umount_count" \
		TEST_CHROOT_COUNT="$chroot_count" \
		TEST_SWITCH_ROOT_STATUS="$TEST_SWITCH_ROOT_STATUS" \
		TEST_DEV="$dev" TEST_MUTATED="$mutated" TEST_NEWROOT="$newroot" \
		TEST_FAKE_BIN="$test_root/bin" \
		TEST_RCS_STAT_FILE="$rcs_stat" \
		sh "$init" >/dev/null 2>&1
}

assert_all_ro() {
	for node in ${ro_nodes:-sda sda1 sda35 sdb}; do
		[ "$(cat "$state/$node")" = 1 ] || fail "$1 left $node writable"
	done
}

expect_success() {
	name=$1
	if ! run_init; then fail "$name: valid persistent root was rejected"; fi
	[ "$(cat "$state/sda")" = 0 ] || fail "$name: parent was not opened"
	[ "$(cat "$state/sda35")" = 0 ] || fail "$name: target was not opened"
	[ "$(cat "$state/sda1")" = 1 ] || fail "$name: sibling became writable"
	[ "$(cat "$state/sdb")" = 1 ] || fail "$name: another LUN became writable"
	grep -q "mount1 -t ext4 -o ro,noload $dev/sda35 $probe" "$command_log" ||
		fail "$name: missing read-only validation mount"
	grep -q "mount2 -t ext4 -o rw,noatime $dev/sda35 $newroot" "$command_log" ||
		fail "$name: missing final read-write mount"
}

expect_rejected() {
	name=$1
	if run_init; then fail "$name: mutation was accepted"; fi
	assert_all_ro "$name"
}

expect_rejected_before_open() {
	name=$1
	expect_rejected "$name"
	if grep -q -- '--setrw' "$command_log"; then
		fail "$name: target identity/root failure opened a block device"
	fi
}

expect_critical() {
	name=$1
	if run_init; then
		fail "$name: critical recovery failure was accepted"
	else
		rc=$?
	fi
	[ "$rc" -eq 125 ] || fail "$name: expected unsafe-fallback status 125, got $rc"
	assert_all_ro "$name"
}

expect_critical_node_set_changed() {
	name=$1
	if run_init; then
		fail "$name: node-set mutation was accepted"
	else
		rc=$?
	fi
	[ "$rc" -eq 125 ] || fail "$name: expected unsafe-fallback status 125, got $rc"
	for node in sda sda1 sda35; do
		[ -e "$dev/$node" ] || continue
		[ "$(cat "$state/$node")" = 1 ] || fail "$name left $node writable"
	done
}

expect_empty_sd_namespace_critical() {
	name=$1
	if run_init; then
		fail "$name: empty /dev/sd* namespace was accepted as a safe fallback"
	else
		rc=$?
	fi
	[ "$rc" -eq 125 ] || fail "$name: expected unsafe-fallback status 125, got $rc"
	if grep -q -- '--setrw' "$command_log"; then
		fail "$name: empty namespace opened a block device"
	fi
}

expect_switch_root_returned_fail_closed() {
	name=$1
	if run_init; then
		fail "$name: returned switch_root was accepted"
	else
		rc=$?
	fi
	# Production uses exec so switch_root itself is PID 1.  A returning helper
	# therefore replaces init and exits non-zero, which is a kernel panic on the
	# real device rather than a route back to RAM rescue services.
	[ "$rc" -eq 1 ] || fail "$name: expected fail-closed switch_root status 1, got $rc"
	grep -q "^mount3 -o move /proc $newroot/proc$" "$command_log" ||
		fail "$name: /proc was not moved before injected switch_root return"
	grep -q "^mount4 -o move /sys $newroot/sys$" "$command_log" ||
		fail "$name: /sys was not moved before injected switch_root return"
	grep -q "^mount5 -o move /dev $newroot/dev$" "$command_log" ||
		fail "$name: /dev was not moved before injected switch_root return"
	grep -q "^switch_root $newroot /sbin/init$" "$command_log" ||
		fail "$name: switch_root fault injection did not run"
	grep -q "^busybox switch_root $newroot /sbin/init$" "$command_log" ||
		fail "$name: pinned BusyBox did not receive the exact switch_root argv"
	[ "$(cat "$state/sda")" = 0 ] || fail "$name: parent was not still in the RW handoff state"
	[ "$(cat "$state/sda35")" = 0 ] || fail "$name: target was not still in the RW handoff state"
}

# --- GNOME profile fixtures ------------------------------------------------
# The GNOME root lives in a gnome-root/ subdirectory of the persistent volume
# and hands PID 1 to systemd.  Its skeleton mirrors the pinned contract shape:
# a marker, systemd, GDM, the liuqin overlay executables, the BlueZ pre-start
# gate and the remaining symlink gates.  The same tree is written below the
# probe mount (RO validation view) and at newroot (what the faked bind mount
# would expose).
gnome_contract_paths='/etc/liuqin-gnome-root /etc/gdm3/custom.conf
/usr/lib/systemd/systemd /usr/lib/systemd/system/gdm.service
/usr/bin/gnome-shell /usr/sbin/gdm3 /usr/local/bin/busybox
/usr/local/bin/liuqin-shell /usr/local/sbin/liuqin-gnome-storage-guard
/usr/local/sbin/liuqin-gnome-usb-rescue
/etc/dconf/db/local.d/locks/00-liuqin-power
/etc/systemd/system/liuqin-gnome-storage-guard.service
/etc/systemd/system/liuqin-gnome-usb-rescue.service
/etc/systemd/system/liuqin-power-keyd.service
/etc/systemd/system/liuqin-snap-root-admission.service
/etc/systemd/system/bluetooth.service.d/20-liuqin-public-address.conf
/etc/systemd/system/liuqin-bt-preconfigure.service
/etc/systemd/system/liuqin-hexagonrpcd-sdsp.service
/etc/systemd/system/liuqin-ssc-sample-gate.service
/etc/systemd/system/liuqin-sensor-stack.target
/etc/systemd/system/liuqin-wlan-mac.service
/etc/systemd/system/NetworkManager.service.d/20-liuqin-wlan-mac.conf
/etc/udev/rules.d/80-liuqin-fastrpc.rules
/usr/bin/hexagonrpcd /usr/bin/ssccli
/usr/local/libexec/liuqin-iio-sensor-proxy
/usr/lib/firmware/qcom/sm8450/Xiaomi-Pad-6-Pro-tplg.bin
/usr/share/qcom/sm8450/Xiaomi/liuqin/sensors/sns_reg_version
/usr/share/liuqin/kernel.release
/usr/share/liuqin/kernel-modules.manifest
/usr/local/libexec/liuqin-power-keyd
/usr/local/libexec/liuqin-power-key-action
/usr/local/libexec/liuqin-uinput-automation
/usr/local/libexec/liuqin-audio-hwparams-probe
/usr/local/sbin/liuqin-snap-root-admission
/usr/local/sbin/liuqin-bt-public-addr
/usr/local/sbin/liuqin-wlan-mac
/usr/libexec/liuqin-ssc-sample-gate'

make_gnome_tree() {
	tree=$1
	mkdir -p "$tree/etc/gdm3" "$tree/etc/dconf/db/local.d/locks" \
		"$tree/etc/udev/rules.d" \
		"$tree/etc/systemd/system/basic.target.requires" \
		"$tree/etc/systemd/system/multi-user.target.wants" \
		"$tree/etc/systemd/system/bluetooth.service.d" \
		"$tree/etc/systemd/system/NetworkManager.service.d" \
		"$tree/usr/bin" "$tree/usr/sbin" "$tree/usr/local/bin" \
		"$tree/usr/local/libexec" "$tree/usr/local/sbin" \
		"$tree/usr/libexec" "$tree/usr/lib/systemd/system" \
		"$tree/usr/lib/firmware/qcom/sm8450" \
		"$tree/usr/share/qcom/sm8450/Xiaomi/liuqin/sensors" +		"$tree/usr/share/liuqin"
	printf 'LIUQIN_GNOME_ROOT_V1\n' >"$tree/etc/liuqin-gnome-root"
	printf '[daemon]\nAutomaticLogin=ubuntu\n' >"$tree/etc/gdm3/custom.conf"
	printf '#!/bin/sh\nexit 0\n' >"$tree/usr/lib/systemd/systemd"
	chmod 0755 "$tree/usr/lib/systemd/systemd"
	printf '# gdm unit\n' >"$tree/usr/lib/systemd/system/gdm.service"
	printf 'gnome-shell\n' >"$tree/usr/bin/gnome-shell"
	printf 'hexagonrpcd\n' >"$tree/usr/bin/hexagonrpcd"
	printf 'ssccli\n' >"$tree/usr/bin/ssccli"
	printf 'gdm3\n' >"$tree/usr/sbin/gdm3"
	printf 'busybox\n' >"$tree/usr/local/bin/busybox"
	printf 'liuqin-shell\n' >"$tree/usr/local/bin/liuqin-shell"
	printf 'storage-guard\n' >"$tree/usr/local/sbin/liuqin-gnome-storage-guard"
	printf 'usb-rescue\n' >"$tree/usr/local/sbin/liuqin-gnome-usb-rescue"
	printf 'power-keyd\n' >"$tree/usr/local/libexec/liuqin-power-keyd"
	printf 'power-key-action\n' >"$tree/usr/local/libexec/liuqin-power-key-action"
	printf 'uinput-automation\n' >"$tree/usr/local/libexec/liuqin-uinput-automation"
	printf 'audio-hwparams-probe\n' >"$tree/usr/local/libexec/liuqin-audio-hwparams-probe"
	printf 'iio-sensor-proxy\n' >"$tree/usr/local/libexec/liuqin-iio-sensor-proxy"
	printf 'snap-root-admission\n' >"$tree/usr/local/sbin/liuqin-snap-root-admission"
	printf 'bt-public-addr\n' >"$tree/usr/local/sbin/liuqin-bt-public-addr"
	printf 'wlan-mac\n' >"$tree/usr/local/sbin/liuqin-wlan-mac"
	printf 'ssc-sample-gate\n' >"$tree/usr/libexec/liuqin-ssc-sample-gate"
	printf 'r03-audio-topology\n' >"$tree/usr/lib/firmware/qcom/sm8450/Xiaomi-Pad-6-Pro-tplg.bin"
	printf 'version=12\n' >"$tree/usr/share/qcom/sm8450/Xiaomi/liuqin/sensors/sns_reg_version"
	printf '6.17.0-rc1-gfixture\n' >"$tree/usr/share/liuqin/kernel.release"
	printf 'fixture  usr/lib/modules/6.17.0-rc1-gfixture/kernel/fixture.ko\n' \
		>"$tree/usr/share/liuqin/kernel-modules.manifest"
	printf '/org/gnome/settings-daemon/plugins/power/power-button-action\n' \
		>"$tree/etc/dconf/db/local.d/locks/00-liuqin-power"
	printf '[Service]\nExecStart=/usr/local/sbin/liuqin-gnome-storage-guard\n' \
		>"$tree/etc/systemd/system/liuqin-gnome-storage-guard.service"
	printf '[Service]\nExecStart=/usr/local/sbin/liuqin-gnome-usb-rescue\n' \
		>"$tree/etc/systemd/system/liuqin-gnome-usb-rescue.service"
	printf '[Unit]\nRequires=liuqin-bt-preconfigure.service\nAfter=liuqin-bt-preconfigure.service\nStartLimitIntervalSec=300\nStartLimitBurst=3\n' \
		>"$tree/etc/systemd/system/bluetooth.service.d/20-liuqin-public-address.conf"
	printf '[Unit]\nBefore=bluetooth.service\nPartOf=bluetooth.service\n[Service]\nType=oneshot\nExecStart=/usr/local/sbin/liuqin-bt-public-addr\nRemainAfterExit=yes\n' \
		>"$tree/etc/systemd/system/liuqin-bt-preconfigure.service"
	for gnome_unit in \
		liuqin-power-keyd.service liuqin-snap-root-admission.service \
		liuqin-hexagonrpcd-sdsp.service \
		liuqin-ssc-sample-gate.service liuqin-sensor-stack.target \
		liuqin-wlan-mac.service; do
		printf '[Unit]\nDescription=%s\n' "$gnome_unit" \
			>"$tree/etc/systemd/system/$gnome_unit"
	done
	printf '[Unit]\nRequires=liuqin-wlan-mac.service\n' \
		>"$tree/etc/systemd/system/NetworkManager.service.d/20-liuqin-wlan-mac.conf"
	printf 'SUBSYSTEM=="misc", KERNEL=="fastrpc-sdsp"\n' \
		>"$tree/etc/udev/rules.d/80-liuqin-fastrpc.rules"
	chmod 0755 "$tree/usr/lib/systemd/systemd" "$tree/usr/bin/gnome-shell" \
		"$tree/usr/bin/hexagonrpcd" "$tree/usr/bin/ssccli" \
		"$tree/usr/sbin/gdm3" "$tree/usr/local/bin/busybox" \
		"$tree/usr/local/bin/liuqin-shell" \
		"$tree/usr/local/sbin/liuqin-gnome-storage-guard" \
		"$tree/usr/local/sbin/liuqin-gnome-usb-rescue" \
		"$tree/usr/local/libexec/liuqin-power-keyd" \
		"$tree/usr/local/libexec/liuqin-power-key-action" \
		"$tree/usr/local/libexec/liuqin-uinput-automation" \
		"$tree/usr/local/libexec/liuqin-audio-hwparams-probe" \
		"$tree/usr/local/libexec/liuqin-iio-sensor-proxy" \
		"$tree/usr/local/sbin/liuqin-snap-root-admission" \
		"$tree/usr/local/sbin/liuqin-bt-public-addr" \
		"$tree/usr/local/sbin/liuqin-wlan-mac" \
		"$tree/usr/libexec/liuqin-ssc-sample-gate"
	chmod 0644 "$tree/etc/dconf/db/local.d/locks/00-liuqin-power" \
		"$tree/etc/systemd/system/liuqin-gnome-storage-guard.service" \
		"$tree/etc/systemd/system/liuqin-gnome-usb-rescue.service" \
		"$tree/etc/systemd/system/liuqin-power-keyd.service" \
		"$tree/etc/systemd/system/liuqin-snap-root-admission.service" \
		"$tree/etc/systemd/system/bluetooth.service.d/20-liuqin-public-address.conf" \
		"$tree/etc/systemd/system/liuqin-bt-preconfigure.service" \
		"$tree/etc/systemd/system/liuqin-hexagonrpcd-sdsp.service" \
		"$tree/etc/systemd/system/liuqin-ssc-sample-gate.service" \
		"$tree/etc/systemd/system/liuqin-sensor-stack.target" \
		"$tree/etc/systemd/system/liuqin-wlan-mac.service" \
		"$tree/etc/systemd/system/NetworkManager.service.d/20-liuqin-wlan-mac.conf" \
		"$tree/etc/udev/rules.d/80-liuqin-fastrpc.rules" \
		"$tree/usr/lib/firmware/qcom/sm8450/Xiaomi-Pad-6-Pro-tplg.bin" \
		"$tree/usr/share/qcom/sm8450/Xiaomi/liuqin/sensors/sns_reg_version" \
		"$tree/usr/share/liuqin/kernel.release" \
		"$tree/usr/share/liuqin/kernel-modules.manifest"
	ln -s ../liuqin-gnome-storage-guard.service \
		"$tree/etc/systemd/system/basic.target.requires/liuqin-gnome-storage-guard.service"
	ln -s ../liuqin-snap-root-admission.service \
		"$tree/etc/systemd/system/basic.target.requires/liuqin-snap-root-admission.service"
	ln -s ../liuqin-gnome-usb-rescue.service \
		"$tree/etc/systemd/system/multi-user.target.wants/liuqin-gnome-usb-rescue.service"
	ln -s ../liuqin-power-keyd.service \
		"$tree/etc/systemd/system/multi-user.target.wants/liuqin-power-keyd.service"
	rm -f "$tree/usr/sbin/init"
	ln -s ../lib/systemd/systemd "$tree/usr/sbin/init"
	rm -f "$tree/etc/systemd/system/default.target" \
		"$tree/etc/systemd/system/display-manager.service"
	ln -s /usr/lib/systemd/system/graphical.target \
		"$tree/etc/systemd/system/default.target"
	ln -s /lib/systemd/system/gdm3.service \
		"$tree/etc/systemd/system/display-manager.service"
}

gnome_reset_fixture() {
	reset_fixture
	make_gnome_tree "$probe/gnome-root"
	make_gnome_tree "$newroot"
	profile_file=$case_root/liuqin-root-profile
	printf 'gnome\n' >"$profile_file"
	gnome_contract=$case_root/gnome-root.contract
	{
		printf 'LIUQIN_GNOME_ROOT_CONTRACT_V1\n'
		for gnome_path in $gnome_contract_paths; do
			printf '%s  %s\n' \
				"$(sha256sum "$probe/gnome-root$gnome_path" | cut -d' ' -f1)" \
				"$gnome_path"
		done
	} >"$gnome_contract"
	LIUQIN_STORAGE_PROFILE_FILE=$profile_file
	LIUQIN_STORAGE_GNOME_CONTRACT=$gnome_contract
	export LIUQIN_STORAGE_PROFILE_FILE LIUQIN_STORAGE_GNOME_CONTRACT
	export profile_file gnome_contract
}

gnome_expect_success() {
	name=$1
	if ! run_init; then fail "$name: valid GNOME root was rejected"; fi
	[ "$(cat "$state/sda")" = 0 ] || fail "$name: parent was not opened"
	[ "$(cat "$state/sda35")" = 0 ] || fail "$name: target was not opened"
	[ "$(cat "$state/sda1")" = 1 ] || fail "$name: sibling became writable"
	[ "$(cat "$state/sdb")" = 1 ] || fail "$name: another LUN became writable"
	grep -q "mount1 -t ext4 -o ro,noload $dev/sda35 $probe" "$command_log" ||
		fail "$name: missing read-only validation mount"
	grep -q "mount2 -t ext4 -o rw,noatime $dev/sda35 $probe" "$command_log" ||
		fail "$name: missing read-write volume mount"
	grep -q "mount3 --bind $probe/gnome-root $newroot" "$command_log" ||
		fail "$name: missing GNOME subroot bind"
	[ "$(grep -c "^umount $probe\$" "$command_log")" = 2 ] ||
		fail "$name: the backing volume was not detached after the bind"
	if grep -q "rw,noatime $dev/sda35 $newroot" "$command_log"; then
		fail "$name: GNOME profile used the legacy whole-volume root mount"
	fi
}

gnome_expect_rejected_safe() {
	name=$1
	if run_init; then fail "$name: mutation was accepted"; else rc=$?; fi
	[ "$rc" -eq 1 ] || fail "$name: expected safe-fallback status 1, got $rc"
	assert_all_ro "$name"
}

gnome_expect_switch_root_returned_fail_closed() {
	name=$1
	if run_init; then
		fail "$name: returned switch_root was accepted"
	else
		rc=$?
	fi
	[ "$rc" -eq 1 ] || fail "$name: expected fail-closed switch_root status 1, got $rc"
	grep -q "^mount4 -o move /proc $newroot/proc$" "$command_log" ||
		fail "$name: /proc was not moved before injected switch_root return"
	grep -q "^mount5 -o move /sys $newroot/sys$" "$command_log" ||
		fail "$name: /sys was not moved before injected switch_root return"
	grep -q "^mount6 -o move /dev $newroot/dev$" "$command_log" ||
		fail "$name: /dev was not moved before injected switch_root return"
	grep -q "^switch_root $newroot /usr/lib/systemd/systemd$" "$command_log" ||
		fail "$name: GNOME handoff argv is not systemd"
	grep -q "^busybox switch_root $newroot /usr/lib/systemd/systemd$" "$command_log" ||
		fail "$name: pinned BusyBox did not receive the exact GNOME switch_root argv"
	if grep -q "^switch_root $newroot /sbin/init$" "$command_log"; then
		fail "$name: GNOME profile fell back to the legacy /sbin/init handoff"
	fi
	[ "$(cat "$state/sda")" = 0 ] || fail "$name: parent was not still in the RW handoff state"
	[ "$(cat "$state/sda35")" = 0 ] || fail "$name: target was not still in the RW handoff state"
}

# Keep the R0.3 project-owned closure independently runnable.  The complete
# persistent-root suite remains the broad regression; this focused host mode
# is the fast, falsifiable admission proof for the larger next-contract shape
# and never writes an initramfs, contract or block device.
gnome_r03_contract_only() {
	gnome_reset_fixture; gnome_expect_success gnome-r03-valid
	gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/usr/local/libexec/liuqin-power-keyd"; expect_rejected_before_open gnome-power-keyd-stale
	gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/usr/local/libexec/liuqin-power-key-action"; expect_rejected_before_open gnome-power-key-action-stale
	gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/etc/dconf/db/local.d/locks/00-liuqin-power"; expect_rejected_before_open gnome-power-key-lock-stale
	gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/usr/local/sbin/liuqin-snap-root-admission"; expect_rejected_before_open gnome-snap-admission-stale
	gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/usr/local/sbin/liuqin-bt-public-addr"; expect_rejected_before_open gnome-bt-public-address-stale
	gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/etc/systemd/system/liuqin-hexagonrpcd-sdsp.service"; expect_rejected_before_open gnome-hexagon-unit-stale
	gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/usr/libexec/liuqin-ssc-sample-gate"; expect_rejected_before_open gnome-ssc-sample-gate-stale
	gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/usr/local/libexec/liuqin-audio-hwparams-probe"; expect_rejected_before_open gnome-r03-audio-probe-stale
	gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/usr/bin/hexagonrpcd"; expect_rejected_before_open gnome-hexagon-binary-stale
	gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/usr/local/libexec/liuqin-iio-sensor-proxy"; expect_rejected_before_open gnome-sensor-proxy-stale
	gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/usr/lib/firmware/qcom/sm8450/Xiaomi-Pad-6-Pro-tplg.bin"; expect_rejected_before_open gnome-r03-topology-stale
	gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/usr/share/qcom/sm8450/Xiaomi/liuqin/sensors/sns_reg_version"; expect_rejected_before_open gnome-sensor-registry-version-stale
	gnome_reset_fixture; chmod 0644 "$probe/gnome-root/usr/local/libexec/liuqin-power-keyd"; expect_rejected_before_open gnome-power-keyd-mode
	gnome_reset_fixture; chmod 0644 "$probe/gnome-root/usr/libexec/liuqin-ssc-sample-gate"; expect_rejected_before_open gnome-ssc-sample-gate-mode
	gnome_reset_fixture; chmod 0600 "$probe/gnome-root/etc/dconf/db/local.d/locks/00-liuqin-power"; expect_rejected_before_open gnome-power-key-lock-mode
	gnome_reset_fixture; rm "$probe/gnome-root/etc/systemd/system/basic.target.requires/liuqin-snap-root-admission.service"; expect_rejected_before_open gnome-snap-enable-missing
	gnome_reset_fixture; rm "$probe/gnome-root/etc/systemd/system/basic.target.requires/liuqin-snap-root-admission.service"; ln -s ../liuqin-gnome-usb-rescue.service "$probe/gnome-root/etc/systemd/system/basic.target.requires/liuqin-snap-root-admission.service"; expect_rejected_before_open gnome-snap-enable-target
	gnome_reset_fixture; rm "$probe/gnome-root/etc/systemd/system/multi-user.target.wants/liuqin-power-keyd.service"; expect_rejected_before_open gnome-power-key-enable-missing
	gnome_reset_fixture; rm "$probe/gnome-root/etc/systemd/system/multi-user.target.wants/liuqin-power-keyd.service"; ln -s ../liuqin-gnome-usb-rescue.service "$probe/gnome-root/etc/systemd/system/multi-user.target.wants/liuqin-power-keyd.service"; expect_rejected_before_open gnome-power-key-enable-target
	gnome_reset_fixture; printf 'Requires=wrong.service\n' >"$probe/gnome-root/etc/systemd/system/bluetooth.service.d/20-liuqin-public-address.conf"; expect_rejected_before_open gnome-bt-order-stale
	gnome_reset_fixture; printf 'Before=wrong.service\n' >"$probe/gnome-root/etc/systemd/system/liuqin-bt-preconfigure.service"; expect_rejected_before_open gnome-bt-preconfigure-stale
	gnome_reset_fixture; mkdir -p "$probe/gnome-root/etc/systemd/system/bluetooth.service.wants"; ln -s ../liuqin-bt-public-addr.service "$probe/gnome-root/etc/systemd/system/bluetooth.service.wants/liuqin-bt-public-addr.service"; expect_rejected_before_open gnome-bt-obsolete-wants
	gnome_reset_fixture; mkdir -p "$probe/gnome-root/etc/systemd/system/bluetooth.service.requires"; ln -s ../liuqin-bt-public-addr.service "$probe/gnome-root/etc/systemd/system/bluetooth.service.requires/liuqin-bt-public-addr.service"; expect_rejected_before_open gnome-bt-obsolete-requires
}

# The native-install root (node C) lives in a native-root/ subdirectory of the
# same volume, pins project-owned paths only, puts the SSC proxy at its
# diverted in-place path, and deliberately keeps snap admission out of
# basic.target.  The fixture mirrors the pin list and the topology gates.
native_contract_paths='/etc/liuqin-native-root /etc/dconf/db/local.d/locks/00-liuqin-power
/etc/systemd/system/liuqin-gnome-storage-guard.service
/usr/lib/systemd/system/liuqin-devmode.service
/etc/systemd/system/liuqin-power-keyd.service
/etc/systemd/system/liuqin-power-keyd.service.d/20-liuqin-session-runtime.conf
/etc/systemd/system/liuqin-backlight-default.service
/etc/systemd/system/liuqin-snap-root-admission.service
/etc/systemd/system/bluetooth.service.d/20-liuqin-public-address.conf
/etc/systemd/system/liuqin-bt-preconfigure.service
/etc/systemd/system/liuqin-hexagonrpcd-sdsp.service
/etc/systemd/system/liuqin-ssc-sample-gate.service
/etc/systemd/system/liuqin-sensor-stack.target
/etc/systemd/system/liuqin-wlan-mac.service
/etc/systemd/system/NetworkManager.service.d/20-liuqin-wlan-mac.conf
/etc/udev/rules.d/80-liuqin-fastrpc.rules
/usr/lib/firmware/novatek/liuqin/novatek_nt36532_m81_fw_csot.bin
/usr/lib/firmware/novatek/liuqin/novatek_nt36532_m81_fw_tm.bin
/usr/lib/firmware/qcom/sm8450/Xiaomi-Pad-6-Pro-tplg.bin
/usr/lib/firmware/updates/qcom/a730_sqe.fw
/usr/lib/firmware/updates/qcom/gmu_gen70000.bin
/usr/bin/hexagonrpcd
/usr/libexec/iio-sensor-proxy /usr/libexec/liuqin-ssc-sample-gate
/usr/local/bin/busybox /usr/sbin/liuqin-devmode
/usr/local/libexec/liuqin-power-keyd /usr/local/libexec/liuqin-power-key-action
/usr/local/libexec/liuqin-power-menu
/usr/local/sbin/liuqin-gnome-storage-guard
/usr/libexec/liuqin/devmode-gadget
/usr/local/sbin/liuqin-snap-root-admission
/usr/local/sbin/liuqin-bt-public-addr /usr/local/sbin/liuqin-wlan-mac
/usr/share/liuqin/kernel.release /usr/share/liuqin/kernel-modules.manifest
/usr/share/liuqin/power/io.github.liuqin.power.gschema.xml
/usr/share/liuqin/power/gschemas.compiled
/usr/share/qcom/sm8450/Xiaomi/liuqin/sensors/sns_reg_version'

make_native_tree() {
	tree=$1
	mkdir -p "$tree/usr/libexec/liuqin" "$tree/usr/lib/systemd/system"
	mkdir -p "$tree/etc/dconf/db/local.d/locks" "$tree/etc/udev/rules.d" \
		"$tree/etc/systemd/system/basic.target.requires" \
		"$tree/etc/systemd/system/multi-user.target.wants" \
		"$tree/etc/systemd/system/graphical.target.wants" \
		"$tree/etc/systemd/system/bluetooth.service.d" \
		"$tree/etc/systemd/system/NetworkManager.service.d" \
		"$tree/etc/systemd/system/liuqin-power-keyd.service.d" \
		"$tree/usr/bin" "$tree/usr/sbin" "$tree/usr/local/bin" \
		"$tree/usr/local/libexec" "$tree/usr/local/sbin" \
		"$tree/usr/libexec" "$tree/usr/lib/systemd/system" \
		"$tree/usr/lib/firmware/novatek/liuqin" \
		"$tree/usr/lib/firmware/qcom/sm8450" \
		"$tree/usr/lib/firmware/updates/qcom" \
		"$tree/usr/share/qcom/sm8450/Xiaomi/liuqin/sensors" \
		"$tree/usr/share/liuqin/power"
	printf 'liuqin-native-root-v1\n' >"$tree/etc/liuqin-native-root"
	printf '#!/bin/sh\nexit 0\n' >"$tree/usr/lib/systemd/systemd"
	printf 'gnome-shell\n' >"$tree/usr/bin/gnome-shell"
	printf 'hexagonrpcd\n' >"$tree/usr/bin/hexagonrpcd"
	printf 'gdm3\n' >"$tree/usr/sbin/gdm3"
	printf 'busybox\n' >"$tree/usr/local/bin/busybox"
	printf 'liuqin-shell\n' >"$tree/usr/sbin/liuqin-devmode"
	printf 'storage-guard\n' >"$tree/usr/local/sbin/liuqin-gnome-storage-guard"
	printf 'usb-rescue\n' >"$tree/usr/libexec/liuqin/devmode-gadget"
	printf 'power-keyd\n' >"$tree/usr/local/libexec/liuqin-power-keyd"
	printf 'power-key-action\n' >"$tree/usr/local/libexec/liuqin-power-key-action"
	printf 'power-menu\n' >"$tree/usr/local/libexec/liuqin-power-menu"
	printf 'iio-sensor-proxy\n' >"$tree/usr/libexec/iio-sensor-proxy"
	printf 'snap-root-admission\n' >"$tree/usr/local/sbin/liuqin-snap-root-admission"
	printf 'bt-public-addr\n' >"$tree/usr/local/sbin/liuqin-bt-public-addr"
	printf 'wlan-mac\n' >"$tree/usr/local/sbin/liuqin-wlan-mac"
	printf 'ssc-sample-gate\n' >"$tree/usr/libexec/liuqin-ssc-sample-gate"
	printf 'touch-csot\n' >"$tree/usr/lib/firmware/novatek/liuqin/novatek_nt36532_m81_fw_csot.bin"
	printf 'touch-tm\n' >"$tree/usr/lib/firmware/novatek/liuqin/novatek_nt36532_m81_fw_tm.bin"
	printf 'r04-audio-topology\n' >"$tree/usr/lib/firmware/qcom/sm8450/Xiaomi-Pad-6-Pro-tplg.bin"
	printf 'sqe\n' >"$tree/usr/lib/firmware/updates/qcom/a730_sqe.fw"
	printf 'gmu\n' >"$tree/usr/lib/firmware/updates/qcom/gmu_gen70000.bin"
	printf 'version=12\n' >"$tree/usr/share/qcom/sm8450/Xiaomi/liuqin/sensors/sns_reg_version"
	printf '6.17.0-rc1-gfixture\n' >"$tree/usr/share/liuqin/kernel.release"
	printf 'fixture  usr/lib/modules/6.17.0-rc1-gfixture/kernel/fixture.ko\n' \
		>"$tree/usr/share/liuqin/kernel-modules.manifest"
	printf 'gschema\n' >"$tree/usr/share/liuqin/power/io.github.liuqin.power.gschema.xml"
	printf 'compiled\n' >"$tree/usr/share/liuqin/power/gschemas.compiled"
	printf '/org/gnome/settings-daemon/plugins/power/power-button-action\n' \
		>"$tree/etc/dconf/db/local.d/locks/00-liuqin-power"
	printf '[Service]\nExecStart=/usr/local/sbin/liuqin-gnome-storage-guard\n' \
		>"$tree/etc/systemd/system/liuqin-gnome-storage-guard.service"
	printf '[Service]\nExecStart=/usr/libexec/liuqin/devmode-gadget\n' \
		>"$tree/usr/lib/systemd/system/liuqin-devmode.service"
	printf '[Service]\nExecStart=/usr/local/libexec/liuqin-backlight-default\n' \
		>"$tree/etc/systemd/system/liuqin-backlight-default.service"
	printf '[Unit]\nRequires=liuqin-bt-preconfigure.service\nAfter=liuqin-bt-preconfigure.service\n' \
		>"$tree/etc/systemd/system/bluetooth.service.d/20-liuqin-public-address.conf"
	printf '[Unit]\nBefore=bluetooth.service\nPartOf=bluetooth.service\n[Service]\nType=oneshot\nExecStart=/usr/local/sbin/liuqin-bt-public-addr\nRemainAfterExit=yes\n' \
		>"$tree/etc/systemd/system/liuqin-bt-preconfigure.service"
	printf '[Service]\nRuntimeDirectory=liuqin-power-keyd\n' \
		>"$tree/etc/systemd/system/liuqin-power-keyd.service.d/20-liuqin-session-runtime.conf"
	for native_unit in \
		liuqin-power-keyd.service liuqin-snap-root-admission.service \
		liuqin-hexagonrpcd-sdsp.service \
		liuqin-ssc-sample-gate.service liuqin-sensor-stack.target \
		liuqin-wlan-mac.service; do
		printf '[Unit]\nDescription=%s\n' "$native_unit" \
			>"$tree/etc/systemd/system/$native_unit"
	done
	printf '[Unit]\nRequires=liuqin-wlan-mac.service\n' \
		>"$tree/etc/systemd/system/NetworkManager.service.d/20-liuqin-wlan-mac.conf"
	printf 'SUBSYSTEM=="misc", KERNEL=="fastrpc-sdsp"\n' \
		>"$tree/etc/udev/rules.d/80-liuqin-fastrpc.rules"
	chmod 0755 "$tree/usr/lib/systemd/systemd" "$tree/usr/bin/gnome-shell" \
		"$tree/usr/bin/hexagonrpcd" \
		"$tree/usr/sbin/gdm3" "$tree/usr/local/bin/busybox" \
		"$tree/usr/sbin/liuqin-devmode" \
		"$tree/usr/local/sbin/liuqin-gnome-storage-guard" \
		"$tree/usr/libexec/liuqin/devmode-gadget" \
		"$tree/usr/local/libexec/liuqin-power-keyd" \
		"$tree/usr/local/libexec/liuqin-power-key-action" \
		"$tree/usr/local/libexec/liuqin-power-menu" \
		"$tree/usr/libexec/iio-sensor-proxy" \
		"$tree/usr/local/sbin/liuqin-snap-root-admission" \
		"$tree/usr/local/sbin/liuqin-bt-public-addr" \
		"$tree/usr/local/sbin/liuqin-wlan-mac" \
		"$tree/usr/libexec/liuqin-ssc-sample-gate"
	chmod 0644 "$tree/etc/liuqin-native-root" \
		"$tree/etc/dconf/db/local.d/locks/00-liuqin-power" \
		"$tree/etc/systemd/system/liuqin-gnome-storage-guard.service" \
		"$tree/usr/lib/systemd/system/liuqin-devmode.service" \
		"$tree/etc/systemd/system/liuqin-backlight-default.service" \
		"$tree/etc/systemd/system/liuqin-power-keyd.service" \
		"$tree/etc/systemd/system/liuqin-power-keyd.service.d/20-liuqin-session-runtime.conf" \
		"$tree/etc/systemd/system/liuqin-snap-root-admission.service" \
		"$tree/etc/systemd/system/bluetooth.service.d/20-liuqin-public-address.conf" \
		"$tree/etc/systemd/system/liuqin-bt-preconfigure.service" \
		"$tree/etc/systemd/system/liuqin-hexagonrpcd-sdsp.service" \
		"$tree/etc/systemd/system/liuqin-ssc-sample-gate.service" \
		"$tree/etc/systemd/system/liuqin-sensor-stack.target" \
		"$tree/etc/systemd/system/liuqin-wlan-mac.service" \
		"$tree/etc/systemd/system/NetworkManager.service.d/20-liuqin-wlan-mac.conf" \
		"$tree/etc/udev/rules.d/80-liuqin-fastrpc.rules" \
		"$tree/usr/lib/firmware/novatek/liuqin/novatek_nt36532_m81_fw_csot.bin" \
		"$tree/usr/lib/firmware/novatek/liuqin/novatek_nt36532_m81_fw_tm.bin" \
		"$tree/usr/lib/firmware/qcom/sm8450/Xiaomi-Pad-6-Pro-tplg.bin" \
		"$tree/usr/lib/firmware/updates/qcom/a730_sqe.fw" \
		"$tree/usr/lib/firmware/updates/qcom/gmu_gen70000.bin" \
		"$tree/usr/share/qcom/sm8450/Xiaomi/liuqin/sensors/sns_reg_version" \
		"$tree/usr/share/liuqin/kernel.release" \
		"$tree/usr/share/liuqin/kernel-modules.manifest" \
		"$tree/usr/share/liuqin/power/io.github.liuqin.power.gschema.xml" \
		"$tree/usr/share/liuqin/power/gschemas.compiled"
	ln -s ../liuqin-gnome-storage-guard.service \
		"$tree/etc/systemd/system/basic.target.requires/liuqin-gnome-storage-guard.service"
	ln -s ../liuqin-power-keyd.service \
		"$tree/etc/systemd/system/multi-user.target.wants/liuqin-power-keyd.service"
	ln -s ../liuqin-backlight-default.service \
		"$tree/etc/systemd/system/graphical.target.wants/liuqin-backlight-default.service"
	rm -f "$tree/usr/sbin/init"
	ln -s ../lib/systemd/systemd "$tree/usr/sbin/init"
	rm -f "$tree/etc/systemd/system/default.target" \
		"$tree/etc/systemd/system/display-manager.service"
	ln -s /usr/lib/systemd/system/graphical.target \
		"$tree/etc/systemd/system/default.target"
	ln -s /lib/systemd/system/gdm3.service \
		"$tree/etc/systemd/system/display-manager.service"
}

native_reset_fixture() {
	reset_fixture
	make_native_tree "$probe/native-root"
	make_native_tree "$newroot"
	profile_file=$case_root/liuqin-root-profile
	printf 'native\n' >"$profile_file"
	native_contract=$case_root/native-root.contract
	{
		printf 'LIUQIN_NATIVE_ROOT_CONTRACT_V1\n'
		for native_path in $native_contract_paths; do
			printf '%s  %s\n' \
				"$(sha256sum "$probe/native-root$native_path" | cut -d' ' -f1)" \
				"$native_path"
		done
	} >"$native_contract"
	LIUQIN_STORAGE_PROFILE_FILE=$profile_file
	LIUQIN_STORAGE_NATIVE_CONTRACT=$native_contract
	export LIUQIN_STORAGE_PROFILE_FILE LIUQIN_STORAGE_NATIVE_CONTRACT
	export profile_file native_contract
}

native_expect_success() {
	name=$1
	if ! run_init; then fail "$name: valid native root was rejected"; fi
	[ "$(cat "$state/sda")" = 0 ] || fail "$name: parent was not opened"
	[ "$(cat "$state/sda35")" = 0 ] || fail "$name: target was not opened"
	[ "$(cat "$state/sda1")" = 1 ] || fail "$name: sibling became writable"
	grep -q "mount1 -t ext4 -o ro,noload $dev/sda35 $probe" "$command_log" ||
		fail "$name: missing read-only validation mount"
	grep -q "mount2 -t ext4 -o rw,noatime $dev/sda35 $probe" "$command_log" ||
		fail "$name: missing read-write volume mount"
	grep -q "mount3 --bind $probe/native-root $newroot" "$command_log" ||
		fail "$name: missing native subroot bind"
	[ "$(grep -c "^umount $probe\$" "$command_log")" = 2 ] ||
		fail "$name: the backing volume was not detached after the bind"
	if grep -q "rw,noatime $dev/sda35 $newroot" "$command_log"; then
		fail "$name: native profile used the legacy whole-volume root mount"
	fi
}

make_fake_commands

if [ "${LIUQIN_PERSISTENT_ROOT_GNOME_R03_ONLY:-0}" = 1 ]; then
	gnome_r03_contract_only
	printf '%s\n' 'test-liuqin-persistent-root: PASS (R0.3 GNOME contract fixture only)'
	exit 0
fi

reset_fixture
expect_success valid

# An empty /dev/sd* glob is unknown storage state, never an all-read-only
# proof. The persistent abort must therefore take the dedicated unsafe path.
reset_fixture; rm -f "$dev"/sd* "$newroot/dev"/sd* "$state"/sd*; expect_empty_sd_namespace_critical empty-sd-namespace

# Capacity variants move userdata inside the GPT: total disk size, partition
# number and start offset are no longer part of the identity.  PARTNAME, the
# 4K logical block size and a 16 GiB floor are what admission checks.
reset_fixture; printf '250069680\n' >"$sys/sda/size"; printf '20973568\n' >"$sys/sda35/start"; printf '229067376\n' >"$sys/sda35/size"; expect_success capacity-variant-128g
reset_fixture; printf '34\n' >"$sys/sda35/partition"; expect_success partition-number-ignored
reset_fixture; printf '512\n' >"$sys/sda/queue/logical_block_size"; expect_rejected_before_open sector-size
reset_fixture; printf '33554431\n' >"$sys/sda35/size"; expect_rejected_before_open partition-below-floor
reset_fixture; printf 'PARTNAME=super\n' >"$sys/sda35/uevent"; expect_rejected_before_open partition-name
reset_fixture; mkdir -p "$sys/sda7"; printf 'PARTNAME=userdata\n' >"$sys/sda7/uevent"; expect_rejected_before_open duplicate-userdata
reset_fixture; printf 'PARTNAME=super\n' >"$sys/sda35/uevent"; mkdir -p "$sys/sdb1"; printf 'PARTNAME=userdata\n' >"$sys/sdb1/uevent"; expect_rejected_before_open userdata-on-wrong-lun
reset_fixture; TEST_LABEL_PATH=$dev/sda1; export TEST_LABEL_PATH; expect_rejected_before_open label-target
reset_fixture; TEST_FINDFS_FAIL=1; export TEST_FINDFS_FAIL; expect_rejected_before_open label-missing
reset_fixture; printf '%s /mnt ext4 rw 0 0\n' "$dev/sda35" >"$case_root/mounts"; expect_rejected_before_open mounted-target
reset_fixture; rm "$probe/etc/liuqin-persistent-root"; expect_rejected_before_open marker-missing
reset_fixture; printf 'WRONG\n' >"$probe/etc/liuqin-persistent-root"; expect_rejected_before_open marker-content
reset_fixture; TEST_MARKER_STAT='600 0 0 26'; export TEST_MARKER_STAT; expect_rejected_before_open marker-mode
reset_fixture; TEST_MARKER_STAT='644 1000 0 26'; export TEST_MARKER_STAT; expect_rejected_before_open marker-owner
reset_fixture; rm "$probe/etc/liuqin-persistent-root"; ln -s inittab "$probe/etc/liuqin-persistent-root"; expect_rejected_before_open marker-symlink
reset_fixture; rm "$probe/usr/local/bin/busybox"; expect_rejected_before_open busybox-missing
reset_fixture; rm "$probe/etc/inittab"; expect_rejected_before_open inittab-missing
reset_fixture; printf '# changed\n' >"$probe/etc/inittab"; expect_rejected_before_open inittab-stale
reset_fixture; mv "$probe/etc/inittab" "$probe/etc/real-inittab"; ln -s real-inittab "$probe/etc/inittab"; expect_rejected_before_open inittab-symlink
reset_fixture; TEST_INITTAB_STAT='600 0 0'; export TEST_INITTAB_STAT; expect_rejected_before_open inittab-mode
reset_fixture; printf '# changed\n' >"$probe/usr/local/bin/busybox"; expect_rejected_before_open busybox-stale
reset_fixture; mv "$probe/usr/local/bin/busybox" "$probe/usr/local/bin/real-busybox"; ln -s real-busybox "$probe/usr/local/bin/busybox"; expect_rejected_before_open busybox-symlink
reset_fixture; TEST_BUSYBOX_STAT='644 0 0'; export TEST_BUSYBOX_STAT; expect_rejected_before_open busybox-mode
reset_fixture; rm "$probe/sbin"; ln -s usr/local "$probe/sbin"; expect_rejected_before_open sbin-link
reset_fixture; rm "$probe/usr/sbin/init"; ln -s /bin/sh "$probe/usr/sbin/init"; expect_rejected_before_open init-link
reset_fixture; rm "$probe/etc/init.d/rcS"; expect_rejected_before_open rcs-missing
reset_fixture; printf '644 0 0\n' >"$rcs_stat"; expect_rejected_before_open rcs-mode
reset_fixture; printf '#!/usr/local/bin/busybox sh\n# changed\n' >"$probe/etc/init.d/rcS"; expect_rejected_before_open rcs-stale
reset_fixture; printf '#!/bin/sh\necho stale\n' >"$probe/etc/init.d/rcS"; expect_rejected_before_open rcs-interpreter
reset_fixture; mv "$probe/etc/init.d/rcS" "$probe/etc/init.d/real-rcS"; ln -s real-rcS "$probe/etc/init.d/rcS"; expect_rejected_before_open rcs-symlink
reset_fixture; printf 'invalid\n' >"$root_contract"; expect_rejected_before_open root-contract-stale
reset_fixture; TEST_FAIL_CHROOT=1; export TEST_FAIL_CHROOT; expect_rejected_before_open chroot-failure
reset_fixture; TEST_FAIL_MOUNT_NUMBER=1; export TEST_FAIL_MOUNT_NUMBER; expect_rejected_before_open readonly-mount
reset_fixture; TEST_FAIL_UMOUNT=1; export TEST_FAIL_UMOUNT; expect_critical readonly-unmount
reset_fixture; TEST_FAIL_SETRW=sda; export TEST_FAIL_SETRW; expect_rejected parent-setrw
reset_fixture; TEST_FAIL_SETRW=sda35; export TEST_FAIL_SETRW; expect_rejected target-setrw
reset_fixture; TEST_FAIL_SETRO=sda1; export TEST_FAIL_SETRO; expect_critical sibling-setro
reset_fixture; TEST_FAIL_GETRO=sda; export TEST_FAIL_GETRO; expect_rejected parent-getro
reset_fixture; TEST_FAIL_GETRO=sda35; export TEST_FAIL_GETRO; expect_rejected target-getro
reset_fixture; TEST_FAIL_GETRO=sdb; export TEST_FAIL_GETRO; expect_critical sibling-getro
reset_fixture; TEST_FAIL_MOUNT_NUMBER=2; export TEST_FAIL_MOUNT_NUMBER; expect_rejected writable-mount

# The final root is checked again after the RW mount. A stage-2 command that
# was valid during the readonly probe must not be accepted if it changes during
# that write window.
reset_fixture; TEST_MUTATE_RCS_ON_CHROOT=2 TEST_MUTATE_RCS=missing; export TEST_MUTATE_RCS_ON_CHROOT TEST_MUTATE_RCS; expect_rejected rw-rcs-missing
reset_fixture; TEST_MUTATE_RCS_ON_CHROOT=2 TEST_MUTATE_RCS=mode; export TEST_MUTATE_RCS_ON_CHROOT TEST_MUTATE_RCS; expect_rejected rw-rcs-mode
reset_fixture; TEST_MUTATE_RCS_ON_CHROOT=2 TEST_MUTATE_RCS=stale; export TEST_MUTATE_RCS_ON_CHROOT TEST_MUTATE_RCS; expect_rejected rw-rcs-stale
reset_fixture; TEST_MUTATE_RCS_ON_CHROOT=2 TEST_MUTATE_RCS=alias; export TEST_MUTATE_RCS_ON_CHROOT TEST_MUTATE_RCS; expect_rejected rw-rcs-symlink
reset_fixture; TEST_MUTATE_PID1_ON_CHROOT=2 TEST_MUTATE_PID1=inittab-missing; export TEST_MUTATE_PID1_ON_CHROOT TEST_MUTATE_PID1; expect_rejected rw-inittab-missing
reset_fixture; TEST_MUTATE_PID1_ON_CHROOT=2 TEST_MUTATE_PID1=inittab-stale; export TEST_MUTATE_PID1_ON_CHROOT TEST_MUTATE_PID1; expect_rejected rw-inittab-stale
reset_fixture; TEST_MUTATE_PID1_ON_CHROOT=2 TEST_MUTATE_PID1=inittab-alias; export TEST_MUTATE_PID1_ON_CHROOT TEST_MUTATE_PID1; expect_rejected rw-inittab-symlink
reset_fixture; TEST_MUTATE_PID1_ON_CHROOT=2 TEST_MUTATE_PID1=busybox-missing; export TEST_MUTATE_PID1_ON_CHROOT TEST_MUTATE_PID1; expect_rejected rw-busybox-missing
reset_fixture; TEST_MUTATE_PID1_ON_CHROOT=2 TEST_MUTATE_PID1=busybox-stale; export TEST_MUTATE_PID1_ON_CHROOT TEST_MUTATE_PID1; expect_rejected rw-busybox-stale
reset_fixture; TEST_MUTATE_PID1_ON_CHROOT=2 TEST_MUTATE_PID1=busybox-alias; export TEST_MUTATE_PID1_ON_CHROOT TEST_MUTATE_PID1; expect_rejected rw-busybox-symlink
reset_fixture; TEST_MUTATE_PID1_ON_CHROOT=2 TEST_MUTATE_PID1=sbin-link; export TEST_MUTATE_PID1_ON_CHROOT TEST_MUTATE_PID1; expect_rejected rw-sbin-link
reset_fixture; TEST_MUTATE_PID1_ON_CHROOT=2 TEST_MUTATE_PID1=init-link; export TEST_MUTATE_PID1_ON_CHROOT TEST_MUTATE_PID1; expect_rejected rw-init-link
# These paths are intentionally *not* part of the PID 1 interpreter chain.
# A mutable root's bash, dynamic loader, or PATH tool must therefore neither
# become an authority nor make a valid static-BusyBox handoff fail.
reset_fixture; TEST_MUTATE_PID1_ON_CHROOT=2 TEST_MUTATE_PID1=bash-stale; export TEST_MUTATE_PID1_ON_CHROOT TEST_MUTATE_PID1; expect_success rw-bash-stale
reset_fixture; TEST_MUTATE_PID1_ON_CHROOT=2 TEST_MUTATE_PID1=loader-stale; export TEST_MUTATE_PID1_ON_CHROOT TEST_MUTATE_PID1; expect_success rw-loader-stale
reset_fixture; TEST_MUTATE_PID1_ON_CHROOT=2 TEST_MUTATE_PID1=tool-stale; export TEST_MUTATE_PID1_ON_CHROOT TEST_MUTATE_PID1; expect_success rw-tool-stale

# The node set is pinned immediately before opening the parent/target pair.
# Any disappearance, addition, or alias during that window is unsafe even if
# all nodes that remain can subsequently be marked read-only.
reset_fixture; TEST_MUTATE_AFTER_SETRW=add; export TEST_MUTATE_AFTER_SETRW; expect_critical_node_set_changed rw-node-added
reset_fixture; TEST_MUTATE_AFTER_SETRW=remove; export TEST_MUTATE_AFTER_SETRW; expect_critical_node_set_changed rw-node-removed
reset_fixture; TEST_MUTATE_AFTER_SETRW=remove-target; export TEST_MUTATE_AFTER_SETRW; expect_critical_node_set_changed rw-target-removed
reset_fixture; TEST_MUTATE_AFTER_SETRW=alias; export TEST_MUTATE_AFTER_SETRW; expect_critical_node_set_changed rw-node-alias

# A mount that itself completes is not an authorization to continue. Mutations
# after mount2 are caught by the post-mount snapshot and are never switch_root'ed.
reset_fixture; TEST_MUTATE_AFTER_MOUNT_NUMBER=2 TEST_MUTATE_AFTER_MOUNT=add; export TEST_MUTATE_AFTER_MOUNT_NUMBER TEST_MUTATE_AFTER_MOUNT; expect_critical_node_set_changed mount2-node-added; grep -q "^mount2 " "$command_log" || fail "mount2-node-added: rw mount did not complete"
reset_fixture; TEST_MUTATE_AFTER_MOUNT_NUMBER=2 TEST_MUTATE_AFTER_MOUNT=remove; export TEST_MUTATE_AFTER_MOUNT_NUMBER TEST_MUTATE_AFTER_MOUNT; expect_critical_node_set_changed mount2-node-removed; grep -q "^mount2 " "$command_log" || fail "mount2-node-removed: rw mount did not complete"
reset_fixture; TEST_MUTATE_AFTER_MOUNT_NUMBER=2 TEST_MUTATE_AFTER_MOUNT=alias; export TEST_MUTATE_AFTER_MOUNT_NUMBER TEST_MUTATE_AFTER_MOUNT; expect_critical_node_set_changed mount2-node-alias; grep -q "^mount2 " "$command_log" || fail "mount2-node-alias: rw mount did not complete"

# A root that became busy after the RW mount must never be reclassified as a
# safe RAM fallback merely because every BLKROSET subsequently returned zero.
reset_fixture
TEST_FAIL_CHROOT_NUMBER=4; TEST_FAIL_UMOUNT_NUMBER=2
export TEST_FAIL_CHROOT_NUMBER TEST_FAIL_UMOUNT_NUMBER
expect_critical writable-root-unmount

# switch_root normally never returns. If it does after proc/sys/dev were moved
# and userdata is RW, exec has replaced PID 1: its non-zero status is therefore
# fatal, rather than resuming the RAM service path.
reset_fixture; TEST_STORAGE_PHASE=switch; export TEST_STORAGE_PHASE; expect_switch_root_returned_fail_closed switch-root-returned

# A path alias is not accepted even when it resolves to a file with the right
# apparent name and every other attribute matches.
reset_fixture
mv "$dev/sda35" "$dev/real-userdata"
ln -s real-userdata "$dev/sda35"
TEST_LABEL_PATH=$dev/sda35; export TEST_LABEL_PATH
expect_rejected_before_open target-symlink
# --- Split dual-boot layout: the linux_home partition ----------------------
# P1b gave /home its own linux_home partition and left mounting it to the real
# root's fstab (LABEL=LIUQIN_HOME, nofail).  The initramfs still sealed every
# UFS node with BLKROSET, and that kernel-level flag survives switch_root, so
# the first device boot after the dual install logged
#   mount: /home: WARNING: source write-protected, mounted read-only
# and gnome-initial-setup could not create the user's home directory.  The
# fixture below is the split layout: sda35 is linux_root, sda36 is linux_home.
split_reset_fixture() {
	reset_fixture
	printf 'PARTNAME=linux_root\n' >"$sys/sda35/uevent"
	mkdir -p "$sys/sda36"
	printf '36\n' >"$sys/sda36/partition"
	printf '201326592\n' >"$sys/sda36/size"
	printf 'PARTNAME=linux_home\n' >"$sys/sda36/uevent"
	: >"$dev/sda36"
	: >"$newroot/dev/sda36"
	printf '1\n' >"$state/sda36"
	ro_nodes='sda sda1 sda35 sda36 sdb'
	TEST_HOME_LABEL_PATH=$dev/sda36
	export ro_nodes TEST_HOME_LABEL_PATH
}

split_assert_home_opened() {
	name=$1
	[ "$(cat "$state/sda36")" = 0 ] ||
		fail "$name: linux_home is still write-protected at the handoff"
	grep -q -- "^--setrw $dev/sda36\$" "$command_log" ||
		fail "$name: BLKROSET was never cleared on linux_home"
	grep -q "persistent home resolved via PARTNAME=linux_home ($dev/sda36), LABEL=LIUQIN_HOME confirmed" \
		"$init_log" || fail "$name: home resolution was not logged"
	grep -q 'persistent home opened read-write' "$init_log" ||
		fail "$name: opening linux_home was not logged"
}

# "Unresolved" is a boot that continues: no BLKROSET clear, no abort, and a log
# line saying why /home stays where it is.
split_assert_home_unresolved() {
	name=$1
	! grep -q -- "^--setrw $dev/sda36\$" "$command_log" ||
		fail "$name: BLKROSET was cleared on an unusable linux_home"
	grep -q 'persistent home not resolved' "$init_log" ||
		fail "$name: the unresolved home partition was not logged"
}

split_assert_home_sealed() {
	split_assert_home_unresolved "$1"
	[ "$(cat "$state/sda36")" = 1 ] ||
		fail "$1: an unusable linux_home was opened read-write"
}

# The fix itself: linux_home found on the same LUN, LABEL=LIUQIN_HOME agrees,
# and its read-only flag is cleared so systemd can mount /home read-write.
split_reset_fixture; expect_success split-home-valid; split_assert_home_opened split-home-valid

# The label is the authority on "this is our /home", exactly as for the root.
split_reset_fixture; TEST_HOME_LABEL_PATH=$dev/sda1; export TEST_HOME_LABEL_PATH
expect_success split-home-label-disagrees; split_assert_home_sealed split-home-label-disagrees
split_reset_fixture; TEST_HOME_LABEL_PATH=; export TEST_HOME_LABEL_PATH
expect_success split-home-label-missing; split_assert_home_sealed split-home-label-missing

# Ambiguity fails closed on the partition, never on the boot.
split_reset_fixture
mkdir -p "$sys/sda37"; printf 'PARTNAME=linux_home\n' >"$sys/sda37/uevent"
: >"$dev/sda37"; : >"$newroot/dev/sda37"; printf '1\n' >"$state/sda37"
ro_nodes='sda sda1 sda35 sda36 sda37 sdb'; export ro_nodes
expect_success split-home-duplicate; split_assert_home_sealed split-home-duplicate
[ "$(cat "$state/sda37")" = 1 ] || fail "split-home-duplicate: second linux_home became writable"

split_reset_fixture
rm -rf "$sys/sda36"; mkdir -p "$sys/sdb1"; printf 'PARTNAME=linux_home\n' >"$sys/sdb1/uevent"
expect_success split-home-wrong-lun; split_assert_home_sealed split-home-wrong-lun

# No linux_home at all (an install that never split /home out) is a normal
# boot: the fstab entry is nofail and /home stays on the root filesystem.
split_reset_fixture
rm -rf "$sys/sda36" "$dev/sda36" "$newroot/dev/sda36" "$state/sda36"
ro_nodes='sda sda1 sda35 sdb'; TEST_HOME_LABEL_PATH=; export ro_nodes TEST_HOME_LABEL_PATH
expect_success split-home-absent; split_assert_home_unresolved split-home-absent

# A refused BLKROSET leaves the partition in the sealed baseline and the boot
# continues; it must not be left in the writable set afterwards.
split_reset_fixture; TEST_FAIL_SETRW=sda36; export TEST_FAIL_SETRW
expect_success split-home-setrw-refused
[ "$(cat "$state/sda36")" = 1 ] || fail "split-home-setrw-refused: linux_home is not sealed"
grep -q 'persistent home could not be opened' "$init_log" ||
	fail "split-home-setrw-refused: the refused open was not logged"

# Every abort after the pair was opened re-seals the home partition with the
# rest of the LUN.  The setrw assertion proves the re-seal is doing work.
split_reset_fixture; TEST_FAIL_MOUNT_NUMBER=2; export TEST_FAIL_MOUNT_NUMBER
expect_rejected split-home-abort-reseals
grep -q -- "^--setrw $dev/sda36\$" "$command_log" ||
	fail "split-home-abort-reseals: linux_home was never opened, so the re-seal proves nothing"
split_reset_fixture; TEST_MUTATE_RCS_ON_CHROOT=2 TEST_MUTATE_RCS=stale
export TEST_MUTATE_RCS_ON_CHROOT TEST_MUTATE_RCS
expect_rejected split-home-rw-reject-reseals

# The handoff re-checks the same UFS state under the moved /dev, so linux_home
# has to remain in the writable set there too -- otherwise the last snapshot
# before switch_root would reject exactly the boot this change enables.
split_reset_fixture; TEST_STORAGE_PHASE=switch; export TEST_STORAGE_PHASE
expect_switch_root_returned_fail_closed split-home-switch-root
split_assert_home_opened split-home-switch-root

# The legacy layout (root on userdata) is untouched: no resolution, no clear,
# not even a log line, whatever else the GPT happens to contain.
reset_fixture
mkdir -p "$sys/sda36"; printf 'PARTNAME=linux_home\n' >"$sys/sda36/uevent"
: >"$dev/sda36"; : >"$newroot/dev/sda36"; printf '1\n' >"$state/sda36"
ro_nodes='sda sda1 sda35 sda36 sdb'; TEST_HOME_LABEL_PATH=$dev/sda36
export ro_nodes TEST_HOME_LABEL_PATH
expect_success legacy-home-untouched
[ "$(cat "$state/sda36")" = 1 ] ||
	fail "legacy-home-untouched: the legacy layout opened linux_home"
! grep -q -- "^--setrw $dev/sda36\$" "$command_log" ||
	fail "legacy-home-untouched: the legacy layout cleared BLKROSET on linux_home"
! grep -q 'persistent home' "$init_log" ||
	fail "legacy-home-untouched: the legacy layout ran home resolution"

# --- GNOME root profile ----------------------------------------------------
# A valid gnome profile mounts the volume, binds gnome-root/ to newroot,
# detaches the volume, and never uses the legacy whole-volume root mount.
gnome_reset_fixture; gnome_expect_success gnome-valid

# Profile selection is fail-closed to legacy: an absent, malformed, mis-owned,
# symlinked or content-mutated profile file must all boot the legacy root.
# expect_success proves the legacy path was taken because it requires the
# legacy whole-volume rw mount of newroot.
legacy_profile_case() {
	reset_fixture
	profile_file=$case_root/liuqin-root-profile
	printf '%s\n' "$1" >"$profile_file"
	LIUQIN_STORAGE_PROFILE_FILE=$profile_file
	export LIUQIN_STORAGE_PROFILE_FILE profile_file
}
reset_fixture; LIUQIN_STORAGE_PROFILE_FILE=$case_root/absent-profile; export LIUQIN_STORAGE_PROFILE_FILE; expect_success profile-absent-legacy
legacy_profile_case weston; expect_success profile-content-legacy
legacy_profile_case GNOME; expect_success profile-case-legacy
legacy_profile_case gnome; TEST_PROFILE_STAT='600 0 0'; export TEST_PROFILE_STAT; expect_success profile-mode-legacy
legacy_profile_case gnome; TEST_PROFILE_STAT='644 1000 0'; export TEST_PROFILE_STAT; expect_success profile-owner-legacy
legacy_profile_case gnome; rm "$profile_file"; ln -s mounts "$profile_file"; expect_success profile-symlink-legacy

# The GNOME marker is validated first, on the RO probe, before any block
# device is opened.
gnome_reset_fixture; rm "$probe/gnome-root/etc/liuqin-gnome-root"; expect_rejected_before_open gnome-marker-missing
gnome_reset_fixture; printf 'LIUQIN_GNOME_ROOT_V2\n' >"$probe/gnome-root/etc/liuqin-gnome-root"; expect_rejected_before_open gnome-marker-content
gnome_reset_fixture; TEST_GNOME_MARKER_STAT='664 0 0 21'; export TEST_GNOME_MARKER_STAT; expect_rejected_before_open gnome-marker-mode
gnome_reset_fixture; TEST_GNOME_MARKER_STAT='644 1000 0 21'; export TEST_GNOME_MARKER_STAT; expect_rejected_before_open gnome-marker-owner
gnome_reset_fixture; TEST_GNOME_MARKER_STAT='644 0 0 22'; export TEST_GNOME_MARKER_STAT; expect_rejected_before_open gnome-marker-size
gnome_reset_fixture; rm "$probe/gnome-root/etc/liuqin-gnome-root"; ln -s ../gdm3/custom.conf "$probe/gnome-root/etc/liuqin-gnome-root"; expect_rejected_before_open gnome-marker-symlink

# The pinned initramfs contract is the only authority over the subroot.
gnome_reset_fixture; LIUQIN_STORAGE_GNOME_CONTRACT=$case_root/absent.contract; export LIUQIN_STORAGE_GNOME_CONTRACT; expect_rejected_before_open gnome-contract-missing
gnome_reset_fixture; mv "$gnome_contract" "$case_root/real.contract"; ln -s real.contract "$gnome_contract"; expect_rejected_before_open gnome-contract-symlink
gnome_reset_fixture; sed -i '1s/.*/LIUQIN_GNOME_ROOT_CONTRACT_V2/' "$gnome_contract"; expect_rejected_before_open gnome-contract-header
gnome_reset_fixture; head -n 3 "$gnome_contract" >"$gnome_contract.tmp"; mv "$gnome_contract.tmp" "$gnome_contract"; expect_rejected_before_open gnome-contract-small
gnome_reset_fixture; head -n 9 "$gnome_contract" >"$gnome_contract.tmp"; mv "$gnome_contract.tmp" "$gnome_contract"; expect_rejected_before_open gnome-contract-short
gnome_reset_fixture; { cat "$gnome_contract"; head -c 17000 /dev/zero | tr '\0' 'x'; } >"$gnome_contract.tmp"; mv "$gnome_contract.tmp" "$gnome_contract"; expect_rejected_before_open gnome-contract-large
gnome_reset_fixture; sed -i '2s/^./x/' "$gnome_contract"; expect_rejected_before_open gnome-contract-hash-format
gnome_reset_fixture; sed -i '2s/  / /' "$gnome_contract"; expect_rejected_before_open gnome-contract-separator
gnome_reset_fixture; sed -i '2s|  /|  |' "$gnome_contract"; expect_rejected_before_open gnome-contract-relative
gnome_reset_fixture; sed -i '2s|  /etc/|  /etc/../etc/|' "$gnome_contract"; expect_rejected_before_open gnome-contract-escape

# Contract-pinned files: missing, stale, symlinked or mis-owned bytes reject
# before any device opens.  A missing subroot entirely (the fresh-device
# state) is the same rejection.
gnome_reset_fixture; rm "$probe/gnome-root/usr/bin/gnome-shell"; expect_rejected_before_open gnome-file-missing
gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/usr/bin/gnome-shell"; expect_rejected_before_open gnome-file-stale
gnome_reset_fixture; mv "$probe/gnome-root/usr/bin/gnome-shell" "$probe/gnome-root/usr/bin/real-gnome-shell"; ln -s real-gnome-shell "$probe/gnome-root/usr/bin/gnome-shell"; expect_rejected_before_open gnome-file-symlink
gnome_reset_fixture; TEST_GNOME_OWNER='1000 1000'; export TEST_GNOME_OWNER; expect_rejected_before_open gnome-file-owner
gnome_reset_fixture; rm -rf "$probe/gnome-root"; expect_rejected_before_open gnome-subroot-missing

# The three symlink gates and the dynamic chroot probe.
gnome_reset_fixture; rm "$probe/gnome-root/usr/sbin/init"; ln -s /bin/sh "$probe/gnome-root/usr/sbin/init"; expect_rejected_before_open gnome-init-link
gnome_reset_fixture; rm "$probe/gnome-root/usr/sbin/init"; expect_rejected_before_open gnome-init-link-missing
gnome_reset_fixture; rm "$probe/gnome-root/etc/systemd/system/default.target"; ln -s /usr/lib/systemd/system/multi-user.target "$probe/gnome-root/etc/systemd/system/default.target"; expect_rejected_before_open gnome-default-target
gnome_reset_fixture; rm "$probe/gnome-root/etc/systemd/system/display-manager.service"; ln -s /lib/systemd/system/lightdm.service "$probe/gnome-root/etc/systemd/system/display-manager.service"; expect_rejected_before_open gnome-display-manager
gnome_reset_fixture; chmod 0644 "$probe/gnome-root/usr/local/sbin/liuqin-gnome-storage-guard"; expect_rejected_before_open gnome-guard-mode
gnome_reset_fixture; chmod 0644 "$probe/gnome-root/usr/bin/gnome-shell"; expect_rejected_before_open gnome-shell-mode
gnome_reset_fixture; rm "$probe/gnome-root/etc/systemd/system/basic.target.requires/liuqin-gnome-storage-guard.service"; expect_rejected_before_open gnome-guard-enable-missing
gnome_reset_fixture; rm "$probe/gnome-root/etc/systemd/system/basic.target.requires/liuqin-gnome-storage-guard.service"; ln -s ../liuqin-gnome-usb-rescue.service "$probe/gnome-root/etc/systemd/system/basic.target.requires/liuqin-gnome-storage-guard.service"; expect_rejected_before_open gnome-guard-enable-target
gnome_reset_fixture; rm "$probe/gnome-root/etc/systemd/system/multi-user.target.wants/liuqin-gnome-usb-rescue.service"; expect_rejected_before_open gnome-rescue-enable-missing
gnome_reset_fixture; TEST_FAIL_CHROOT=1; export TEST_FAIL_CHROOT; expect_rejected_before_open gnome-chroot-failure

# R0.3 project-owned safety closure: the content contract detects stale bytes,
# while the topology gate catches a valid-looking file that is not executable
# or is not connected to the target that owns its activation.
gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/usr/local/libexec/liuqin-power-keyd"; expect_rejected_before_open gnome-power-keyd-stale
gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/usr/local/libexec/liuqin-power-key-action"; expect_rejected_before_open gnome-power-key-action-stale
gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/usr/local/libexec/liuqin-uinput-automation"; expect_rejected_before_open gnome-uinput-automation-stale
gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/etc/dconf/db/local.d/locks/00-liuqin-power"; expect_rejected_before_open gnome-power-key-lock-stale
gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/usr/local/sbin/liuqin-snap-root-admission"; expect_rejected_before_open gnome-snap-admission-stale
gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/usr/local/sbin/liuqin-bt-public-addr"; expect_rejected_before_open gnome-bt-public-address-stale
gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/etc/systemd/system/liuqin-hexagonrpcd-sdsp.service"; expect_rejected_before_open gnome-hexagon-unit-stale
gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/usr/libexec/liuqin-ssc-sample-gate"; expect_rejected_before_open gnome-ssc-sample-gate-stale
gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/usr/local/libexec/liuqin-audio-hwparams-probe"; expect_rejected_before_open gnome-r03-audio-probe-stale
gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/usr/bin/hexagonrpcd"; expect_rejected_before_open gnome-hexagon-binary-stale
gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/usr/local/libexec/liuqin-iio-sensor-proxy"; expect_rejected_before_open gnome-sensor-proxy-stale
gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/usr/lib/firmware/qcom/sm8450/Xiaomi-Pad-6-Pro-tplg.bin"; expect_rejected_before_open gnome-r03-topology-stale
gnome_reset_fixture; printf 'changed\n' >"$probe/gnome-root/usr/share/qcom/sm8450/Xiaomi/liuqin/sensors/sns_reg_version"; expect_rejected_before_open gnome-sensor-registry-version-stale
gnome_reset_fixture; chmod 0644 "$probe/gnome-root/usr/local/libexec/liuqin-power-keyd"; expect_rejected_before_open gnome-power-keyd-mode
gnome_reset_fixture; chmod 0644 "$probe/gnome-root/usr/local/libexec/liuqin-uinput-automation"; expect_rejected_before_open gnome-uinput-automation-mode
gnome_reset_fixture; chmod 0644 "$probe/gnome-root/usr/libexec/liuqin-ssc-sample-gate"; expect_rejected_before_open gnome-ssc-sample-gate-mode
gnome_reset_fixture; chmod 0600 "$probe/gnome-root/etc/dconf/db/local.d/locks/00-liuqin-power"; expect_rejected_before_open gnome-power-key-lock-mode
gnome_reset_fixture; rm "$probe/gnome-root/etc/systemd/system/basic.target.requires/liuqin-snap-root-admission.service"; expect_rejected_before_open gnome-snap-enable-missing
gnome_reset_fixture; rm "$probe/gnome-root/etc/systemd/system/basic.target.requires/liuqin-snap-root-admission.service"; ln -s ../liuqin-gnome-usb-rescue.service "$probe/gnome-root/etc/systemd/system/basic.target.requires/liuqin-snap-root-admission.service"; expect_rejected_before_open gnome-snap-enable-target
gnome_reset_fixture; rm "$probe/gnome-root/etc/systemd/system/multi-user.target.wants/liuqin-power-keyd.service"; expect_rejected_before_open gnome-power-key-enable-missing
gnome_reset_fixture; rm "$probe/gnome-root/etc/systemd/system/multi-user.target.wants/liuqin-power-keyd.service"; ln -s ../liuqin-gnome-usb-rescue.service "$probe/gnome-root/etc/systemd/system/multi-user.target.wants/liuqin-power-keyd.service"; expect_rejected_before_open gnome-power-key-enable-target
gnome_reset_fixture; printf 'Requires=wrong.service\n' >"$probe/gnome-root/etc/systemd/system/bluetooth.service.d/20-liuqin-public-address.conf"; expect_rejected_before_open gnome-bt-preconfigure-dropin-stale
gnome_reset_fixture; printf 'Before=wrong.service\n' >"$probe/gnome-root/etc/systemd/system/liuqin-bt-preconfigure.service"; expect_rejected_before_open gnome-bt-preconfigure-unit-stale
gnome_reset_fixture; mkdir -p "$probe/gnome-root/etc/systemd/system/bluetooth.service.wants"; ln -s ../liuqin-bt-public-addr.service "$probe/gnome-root/etc/systemd/system/bluetooth.service.wants/liuqin-bt-public-addr.service"; expect_rejected_before_open gnome-bt-obsolete-wants
gnome_reset_fixture; mkdir -p "$probe/gnome-root/etc/systemd/system/bluetooth.service.requires"; ln -s ../liuqin-bt-public-addr.service "$probe/gnome-root/etc/systemd/system/bluetooth.service.requires/liuqin-bt-public-addr.service"; expect_rejected_before_open gnome-bt-obsolete-requires

# RO probe, RW volume mount, subroot bind and volume detach each fail closed.
gnome_reset_fixture; TEST_FAIL_MOUNT_NUMBER=1; export TEST_FAIL_MOUNT_NUMBER; expect_rejected_before_open gnome-readonly-mount
gnome_reset_fixture; TEST_FAIL_MOUNT_NUMBER=2; export TEST_FAIL_MOUNT_NUMBER; gnome_expect_rejected_safe gnome-volume-mount
gnome_reset_fixture; TEST_FAIL_MOUNT_NUMBER=3; export TEST_FAIL_MOUNT_NUMBER; gnome_expect_rejected_safe gnome-bind-failed
gnome_reset_fixture; TEST_FAIL_UMOUNT_NUMBER=2; export TEST_FAIL_UMOUNT_NUMBER; gnome_expect_rejected_safe gnome-detach-failed
gnome_reset_fixture; TEST_FAIL_UMOUNT_NUMBER='2 3'; export TEST_FAIL_UMOUNT_NUMBER; expect_critical gnome-detach-critical

# The subroot is re-validated after the bind: a mutation during the write
# window is rejected and every device is locked again.
gnome_reset_fixture; TEST_MUTATE_GNOME_ON_CHROOT=1 TEST_MUTATE_GNOME=marker-missing; export TEST_MUTATE_GNOME_ON_CHROOT TEST_MUTATE_GNOME; gnome_expect_rejected_safe gnome-rw-marker-missing
gnome_reset_fixture; TEST_MUTATE_GNOME_ON_CHROOT=1 TEST_MUTATE_GNOME=file-stale; export TEST_MUTATE_GNOME_ON_CHROOT TEST_MUTATE_GNOME; gnome_expect_rejected_safe gnome-rw-file-stale
gnome_reset_fixture; TEST_MUTATE_GNOME_ON_CHROOT=1 TEST_MUTATE_GNOME=file-symlink; export TEST_MUTATE_GNOME_ON_CHROOT TEST_MUTATE_GNOME; gnome_expect_rejected_safe gnome-rw-file-symlink
gnome_reset_fixture; TEST_MUTATE_GNOME_ON_CHROOT=1 TEST_MUTATE_GNOME=init-link; export TEST_MUTATE_GNOME_ON_CHROOT TEST_MUTATE_GNOME; gnome_expect_rejected_safe gnome-rw-init-link

# Device-namespace mutations in the GNOME write window are as unsafe as in
# the legacy path: after the open, after the volume mount, after the bind.
gnome_reset_fixture; TEST_MUTATE_AFTER_SETRW=add; export TEST_MUTATE_AFTER_SETRW; expect_critical_node_set_changed gnome-node-added
gnome_reset_fixture; TEST_MUTATE_AFTER_MOUNT_NUMBER=2 TEST_MUTATE_AFTER_MOUNT=add; export TEST_MUTATE_AFTER_MOUNT_NUMBER TEST_MUTATE_AFTER_MOUNT; expect_critical_node_set_changed gnome-volume-node-added; grep -q "^mount2 " "$command_log" || fail "gnome-volume-node-added: volume mount did not complete"
gnome_reset_fixture; TEST_MUTATE_AFTER_MOUNT_NUMBER=3 TEST_MUTATE_AFTER_MOUNT=remove; export TEST_MUTATE_AFTER_MOUNT_NUMBER TEST_MUTATE_AFTER_MOUNT; expect_critical_node_set_changed gnome-bind-node-removed; grep -q "^mount3 " "$command_log" || fail "gnome-bind-node-removed: subroot bind did not complete"

# The GNOME handoff execs systemd -- and the legacy handoff, exercised by the
# earlier switch-root-returned case, still execs /sbin/init.
gnome_reset_fixture; TEST_STORAGE_PHASE=switch; export TEST_STORAGE_PHASE; gnome_expect_switch_root_returned_fail_closed gnome-switch-root-returned

# --- Native install root profile (node C) -----------------------------------
# A valid native profile mounts the volume, binds native-root/ to newroot,
# detaches the volume, and never uses the legacy whole-volume root mount.
native_reset_fixture; rm "$root_contract"; native_expect_success native-without-legacy-contract

# The marker gates first, on the RO probe, before any block device opens.
native_reset_fixture; rm "$probe/native-root/etc/liuqin-native-root"; expect_rejected_before_open native-marker-missing
native_reset_fixture; printf 'liuqin-native-root-v2\n' >"$probe/native-root/etc/liuqin-native-root"; expect_rejected_before_open native-marker-content
native_reset_fixture; TEST_NATIVE_MARKER_STAT='664 0 0 22'; export TEST_NATIVE_MARKER_STAT; expect_rejected_before_open native-marker-mode
native_reset_fixture; TEST_NATIVE_MARKER_STAT='644 0 0 21'; export TEST_NATIVE_MARKER_STAT; expect_rejected_before_open native-marker-size
native_reset_fixture; rm "$probe/native-root/etc/liuqin-native-root"; ln -s ../hostname "$probe/native-root/etc/liuqin-native-root"; expect_rejected_before_open native-marker-symlink

# The pinned initramfs contract is the only authority over the subroot.
native_reset_fixture; LIUQIN_STORAGE_NATIVE_CONTRACT=$case_root/absent.contract; export LIUQIN_STORAGE_NATIVE_CONTRACT; expect_rejected_before_open native-contract-missing
native_reset_fixture; mv "$native_contract" "$case_root/real.contract"; ln -s real.contract "$native_contract"; expect_rejected_before_open native-contract-symlink
native_reset_fixture; sed -i '1s/.*/LIUQIN_GNOME_ROOT_CONTRACT_V1/' "$native_contract"; expect_rejected_before_open native-contract-header
native_reset_fixture; head -n 9 "$native_contract" >"$native_contract.tmp"; mv "$native_contract.tmp" "$native_contract"; expect_rejected_before_open native-contract-short
native_reset_fixture; sed -i '2s/^./x/' "$native_contract"; expect_rejected_before_open native-contract-hash-format
native_reset_fixture; sed -i '2s|  /etc/|  /etc/../etc/|' "$native_contract"; expect_rejected_before_open native-contract-escape

# Contract-pinned content: the diverted in-place proxy path and the updates/
# GPU blobs the legacy contract could not yet pin are gated here.
native_reset_fixture; printf 'changed\n' >"$probe/native-root/usr/libexec/iio-sensor-proxy"; expect_rejected_before_open native-sensor-proxy-stale
native_reset_fixture; printf 'changed\n' >"$probe/native-root/usr/lib/firmware/updates/qcom/a730_sqe.fw"; expect_rejected_before_open native-gpu-sqe-stale
native_reset_fixture; printf 'changed\n' >"$probe/native-root/usr/lib/firmware/updates/qcom/gmu_gen70000.bin"; expect_rejected_before_open native-gpu-gmu-stale
native_reset_fixture; printf 'changed\n' >"$probe/native-root/usr/lib/firmware/qcom/sm8450/Xiaomi-Pad-6-Pro-tplg.bin"; expect_rejected_before_open native-topology-stale
native_reset_fixture; printf 'changed\n' >"$probe/native-root/usr/bin/hexagonrpcd"; expect_rejected_before_open native-hexagon-binary-stale
native_reset_fixture; chmod 0644 "$probe/native-root/usr/local/libexec/liuqin-power-keyd"; expect_rejected_before_open native-power-keyd-mode
native_reset_fixture; rm "$probe/native-root/usr/share/liuqin/kernel.release"; expect_rejected_before_open native-kernel-release-missing

# Topology gates: enablement links, and the fail-closed snap direction -- a
# root that still lets snap admission gate basic.target is rejected.
native_reset_fixture; rm "$probe/native-root/etc/systemd/system/basic.target.requires/liuqin-gnome-storage-guard.service"; expect_rejected_before_open native-guard-enable-missing
native_reset_fixture; ln -s ../liuqin-snap-root-admission.service "$probe/native-root/etc/systemd/system/basic.target.requires/liuqin-snap-root-admission.service"; expect_rejected_before_open native-snap-must-not-gate
native_reset_fixture; rm "$probe/native-root/etc/systemd/system/graphical.target.wants/liuqin-backlight-default.service"; expect_rejected_before_open native-backlight-enable-missing
native_reset_fixture; native_expect_success native-devmode-off-by-default
native_reset_fixture; rm "$probe/native-root/etc/systemd/system/multi-user.target.wants/liuqin-power-keyd.service"; expect_rejected_before_open native-power-key-enable-missing
native_reset_fixture; rm "$probe/native-root/usr/sbin/init"; ln -s /bin/sh "$probe/native-root/usr/sbin/init"; expect_rejected_before_open native-init-link
native_reset_fixture; rm "$probe/native-root/etc/systemd/system/default.target"; ln -s /usr/lib/systemd/system/multi-user.target "$probe/native-root/etc/systemd/system/default.target"; expect_rejected_before_open native-default-target
native_reset_fixture; rm "$probe/native-root/etc/systemd/system/display-manager.service"; ln -s /lib/systemd/system/lightdm.service "$probe/native-root/etc/systemd/system/display-manager.service"; expect_rejected_before_open native-display-manager
native_reset_fixture; TEST_FAIL_CHROOT=1; export TEST_FAIL_CHROOT; expect_rejected_before_open native-chroot-failure
native_reset_fixture; rm -rf "$probe/native-root"; expect_rejected_before_open native-subroot-missing

# The subroot is re-validated after the bind, under the native checker.
native_reset_fixture; TEST_MUTATE_NATIVE_ON_CHROOT=1 TEST_MUTATE_NATIVE=marker-missing; export TEST_MUTATE_NATIVE_ON_CHROOT TEST_MUTATE_NATIVE; gnome_expect_rejected_safe native-rw-marker-missing
native_reset_fixture; TEST_MUTATE_NATIVE_ON_CHROOT=1 TEST_MUTATE_NATIVE=file-stale; export TEST_MUTATE_NATIVE_ON_CHROOT TEST_MUTATE_NATIVE; gnome_expect_rejected_safe native-rw-file-stale

# The native handoff execs systemd, profile-selected like GNOME.
native_reset_fixture; TEST_STORAGE_PHASE=switch; export TEST_STORAGE_PHASE; gnome_expect_switch_root_returned_fail_closed native-switch-root-returned
python3 - "$command_log" <<'PYTEST'
import sys
s = open(sys.argv[1]).read()
assert s.index('physical-platform /sys') < s.index('mount4 -o move /proc')
PYTEST
native_reset_fixture; TEST_STORAGE_PHASE=switch TEST_PLATFORM_FAIL=1
export TEST_STORAGE_PHASE TEST_PLATFORM_FAIL
gnome_expect_rejected_safe physical-platform-refused
unset TEST_PLATFORM_FAIL

grep -q '^storage_marker=etc/liuqin-persistent-root$' "$init" || fail "marker path changed"
grep -q '^storage_marker_sha256=0d354b25a425dcc17da2c7a8bbdaae09274cecdb1f137c70630ebe5634fa9a48$' \
	"$init" || fail "marker content contract changed"
# The handoff target is profile-selected: /sbin/init for legacy (proved
# dynamically by switch-root-returned) and systemd for gnome (proved by
# gnome-switch-root-returned).  Pin both resolutions and the exec form.
grep -F -q 'exec "$BB" switch_root "$storage_newroot" "$storage_init"' "$init" ||
	fail "persistent handoff does not execute switch_root as PID 1"
grep -q '^storage_init=/sbin/init$' "$init" ||
	fail "legacy handoff target is no longer /sbin/init"
grep -q '^	storage_init=/usr/lib/systemd/systemd$' "$init" ||
	fail "gnome handoff target is no longer systemd"
grep -q '^	storage_marker_sha256=bd86a359f5b6bf05f09abf544967489e924c251ab7c07684df62b0dbda4c3fca$' \
	"$init" || fail "gnome marker content contract changed"
grep -q '497a8b108a3eefc839c3738a5c452ee3d6a437eb1ebaf0ea43bd635a8a227978' "$init" ||
	fail "gnome profile-file hash pin changed"
# The slot marked successful is the slot this boot came from, never a constant:
# the dual layout boots Ubuntu from slot A (slot B only on the bootloader's
# fallback copy), the Linux-only layout from slot B.
slot_check_lines=$(grep -n '/bin/liuqin-mark-slot-successful --check "\$slot_suffix"' "$init" | cut -d: -f1)
slot_check_first=$(printf '%s\n' "$slot_check_lines" | sed -n '1p')
slot_check_second=$(printf '%s\n' "$slot_check_lines" | sed -n '2p')
slot_mark_line=$(grep -n '/bin/liuqin-mark-slot-successful --mark "\$slot_suffix"' "$init" | cut -d: -f1)
slot_b_refuse_line=$(grep -n 'refusing a persistent root handoff without a usable androidboot.slot_suffix' "$init" | cut -d: -f1)
! grep -q 'liuqin-mark-slot-successful --mark-a\|liuqin-mark-slot-successful --check-a' "$init" ||
	fail "init still pins slot A in the slot-success gate"
grep -q '^		if slot_from_cmdline "\$(cat /proc/cmdline)"; then$' "$init" ||
	fail "slot-success gate no longer derives the slot from the cmdline"
switch_line=$(grep -n 'elif ! storage_switch_persistent_root; then' "$init" | cut -d: -f1)
case $slot_check_first:$slot_mark_line:$slot_check_second:$slot_b_refuse_line:$switch_line in
	*[!0-9:]*|*::*|:*) fail "persistent slot-success gate is missing" ;;
esac
[ "$(printf '%s\n' "$slot_check_lines" | wc -l | tr -d ' ')" = 2 ] ||
	fail "persistent slot-success gate does not have exactly two checks"
[ "$slot_check_first" -lt "$slot_mark_line" ] &&
	[ "$slot_mark_line" -lt "$slot_check_second" ] &&
	[ "$slot_check_second" -lt "$switch_line" ] &&
	[ "$slot_b_refuse_line" -lt "$switch_line" ] ||
	fail "persistent root can switch before the slot-success gate"

# The split dual-boot layout puts the root in linux_root; a single-system
# install still hands over userdata.  linux_root has to win when both exist.
grep -q '^	for storage_partname in linux_root userdata; do$' "$init" ||
	fail "root resolution no longer prefers PARTNAME=linux_root over userdata"
grep -q '^	storage_partlabel_link=\$storage_dev_root/disk/by-partlabel/linux_root$' "$init" ||
	fail "root resolution no longer cross-checks by-partlabel"
grep -q 'findfs LABEL=LIUQIN_ROOT' "$init" ||
	fail "root resolution no longer confirms LABEL=LIUQIN_ROOT"

# The split layout also has to clear the read-only flag on linux_home, and it
# has to do so in exactly one window: after the root has proven its own
# identity (a partition named by a root we have not admitted is not authority
# enough to unseal anything) and before the handoff (after switch_root the
# initramfs is gone and the flag is whatever it was left as).
home_resolve_def_line=$(grep -cn '^storage_resolve_home() {$' "$init")
[ "$home_resolve_def_line" = 1 ] ||
	fail "init does not define exactly one storage_resolve_home"
grep -q '^	\[ "\$storage_resolved_partname" = linux_root \] || return 1$' "$init" ||
	fail "home resolution is not restricted to the split linux_root layout"
grep -q '^		sda\[0-9\]\*) ;;$' "$init" ||
	fail "home resolution no longer pins the first UFS LUN"
grep -q 'findfs LABEL=LIUQIN_HOME' "$init" ||
	fail "home resolution no longer cross-checks LABEL=LIUQIN_HOME"
root_label_line=$(grep -n 'findfs LABEL=LIUQIN_ROOT' "$init" | cut -d: -f1)
home_call_line=$(grep -n '^		if storage_resolve_home; then$' "$init" | cut -d: -f1)
identity_gate_line=$(grep -n '^	storage_identity_ok || {$' "$init" | cut -d: -f1)
root_setrw_line=$(grep -n 'blockdev --setrw "\$storage_target"' "$init" | cut -d: -f1)
home_setrw_line=$(grep -n 'blockdev --setrw "\$storage_home"' "$init" | cut -d: -f1)
switch_exec_line=$(grep -n '^	exec "\$BB" switch_root ' "$init" | cut -d: -f1)
case $root_label_line:$home_call_line:$identity_gate_line:$root_setrw_line:$home_setrw_line:$switch_exec_line in
	*[!0-9:]*|*::*|:*|*:) fail "the linux_home read-only clear is not where it must be" ;;
esac
[ "$root_label_line" -lt "$home_call_line" ] ||
	fail "home resolution runs before the root confirms LABEL=LIUQIN_ROOT"
[ "$home_call_line" -lt "$identity_gate_line" ] ||
	fail "home resolution is not part of the root identity check"
[ "$identity_gate_line" -lt "$root_setrw_line" ] ||
	fail "the root identity gate no longer precedes the BLKROSET clears"
[ "$root_setrw_line" -lt "$home_setrw_line" ] ||
	fail "linux_home is unsealed before the root partition is"
[ "$home_setrw_line" -lt "$switch_exec_line" ] ||
	fail "linux_home is unsealed after switch_root, where it can no longer help"

# Whatever rejects the boot must put linux_home back with the rest of the LUN.
sed -n '/^storage_abort() {$/,/^}$/p' "$init" >"$test_root/abort-body"
[ -s "$test_root/abort-body" ] || fail "could not extract storage_abort from init"
grep -q '^	storage_home_name=$' "$test_root/abort-body" ||
	fail "storage_abort no longer drops linux_home from the writable set"
grep -q '^	storage_lock_all || storage_lock_ok=false$' "$test_root/abort-body" ||
	fail "storage_abort no longer re-seals every UFS node"

# --------------------------------------------------------------------------
# Installer-mode /dev/disk/by-partlabel: boot_a/boot_b/persist are required,
# userdata/linux_root/linux_home are optional.  A Linux-only install deletes
# userdata and a fresh device has no linux_root, and either case must still get
# a telnet channel -- that is the channel --restore-partition-table runs from.
# --------------------------------------------------------------------------
installer_root=$test_root/installer
sed -n '/^storage_path_is_block() {$/,/^}$/p' "$init" >"$installer_root.fn1"
sed -n '/^storage_installer_required=/,/^}$/p' "$init" >"$installer_root.fn2"
[ -s "$installer_root.fn1" ] && [ -s "$installer_root.fn2" ] ||
	fail "could not extract the installer by-partlabel helpers from init"
grep -q '^storage_installer_required=.boot_a boot_b persist.$' "$init" ||
	fail "installer required partition set changed"
grep -q '^storage_installer_optional=.userdata linux_root linux_home.$' "$init" ||
	fail "installer optional partition set changed"

# $1 case name, $2 writable node suffix or empty, rest: PARTNAME values
installer_run() {
	installer_name=$1; installer_writable=$2; shift 2
	installer_case=$installer_root/$installer_name
	rm -rf "$installer_case"
	mkdir -p "$installer_case/dev" "$installer_case/sys" "$installer_case/etc"
	: >"$installer_case/etc/liuqin-installer"
	installer_index=10
	for installer_label in "$@"; do
		installer_index=$((installer_index + 1))
		mkdir -p "$installer_case/sys/sda$installer_index"
		printf 'PARTNAME=%s\n' "$installer_label" \
			>"$installer_case/sys/sda$installer_index/uevent"
		: >"$installer_case/dev/sda$installer_index"
		[ "$installer_label" != "$installer_writable" ] ||
			printf '%s\n' "sda$installer_index" >"$installer_case/writable"
	done
	cat >"$installer_case/bb" <<'INSTALLER_BB'
#!/bin/sh
installer_bb_cmd=$1; shift
case $installer_bb_cmd in
grep) exec grep "$@" ;;
blockdev)
	installer_bb_node=${2##*/}
	if [ -r "$INSTALLER_WRITABLE_FILE" ] &&
		[ "$(cat "$INSTALLER_WRITABLE_FILE")" = "$installer_bb_node" ]; then
		printf '0\n'
	else
		printf '1\n'
	fi
	;;
*) exit 2 ;;
esac
INSTALLER_BB
	chmod 0755 "$installer_case/bb"
	{
		printf 'BB=%s\n' "$installer_case/bb"
		printf 'ufs_safe=true\nstorage_mode=${TEST_CHANNEL_MODE:-readonly}\n'
		printf 'storage_dev_root=%s\n' "$installer_case/dev"
		printf 'storage_sys_block=%s\n' "$installer_case/sys"
		printf 'storage_installer_marker=%s\n' "$installer_case/etc/liuqin-installer"
		printf 'log() { printf "log: %%s\\n" "$*"; }\n'
		printf 'telnetd() { printf "telnetd\\n"; }\n'
		cat "$installer_root.fn1"
		cat "$installer_root.fn2"
		printf 'start_stage1_telnet\n'
	} >"$installer_case/run.sh"
	LIUQIN_INIT_STORAGE_TEST_ONLY=1 \
		INSTALLER_WRITABLE_FILE="$installer_case/writable" \
		"$host_busybox" sh "$installer_case/run.sh" >"$installer_case/out" 2>&1
}

installer_expect_ok() {
	installer_expect_name=$1; shift
	installer_run "$installer_expect_name" '' "$@" ||
		fail "installer/$installer_expect_name: refused a usable layout"
	grep -q '^telnetd$' "$installer_root/$installer_expect_name/out" ||
		fail "installer/$installer_expect_name: telnet channel was not opened"
}

installer_expect_refused() {
	installer_expect_name=$1; installer_expect_writable=$2; shift 2
	if installer_run "$installer_expect_name" "$installer_expect_writable" "$@"; then
		fail "installer/$installer_expect_name: accepted a layout it must refuse"
	fi
	! grep -q '^telnetd$' "$installer_root/$installer_expect_name/out" ||
		fail "installer/$installer_expect_name: opened telnet after a refusal"
}

installer_linked() {
	[ -L "$installer_root/$1/dev/disk/by-partlabel/$2" ]
}

# Every optional partition present: all six get a symlink.
installer_expect_ok full boot_a boot_b persist userdata linux_root linux_home
for installer_label in boot_a boot_b persist userdata linux_root linux_home; do
	installer_linked full "$installer_label" ||
		fail "installer/full: $installer_label was not linked"
done
grep -q '^log: installer data partitions linked: userdata linux_root linux_home$' \
	"$installer_root/full/out" || fail "installer/full: optional link log is wrong"

# Bare device, nothing but the required three: still a telnet channel.
installer_expect_ok bare boot_a boot_b persist
for installer_label in boot_a boot_b persist; do
	installer_linked bare "$installer_label" ||
		fail "installer/bare: $installer_label was not linked"
done
for installer_label in userdata linux_root linux_home; do
	! installer_linked bare "$installer_label" ||
		fail "installer/bare: $installer_label was linked but does not exist"
done
grep -q '^log: installer data partitions linked: none$' "$installer_root/bare/out" ||
	fail "installer/bare: optional link log is wrong"

# A Linux-only install (--android-size 0) has no userdata at all.
installer_expect_ok linux-only boot_a boot_b persist linux_root linux_home
! installer_linked linux-only userdata ||
	fail "installer/linux-only: userdata was linked but does not exist"
installer_linked linux-only linux_root ||
	fail "installer/linux-only: linux_root was not linked"
grep -q '^log: installer data partitions linked: linux_root linux_home$' \
	"$installer_root/linux-only/out" || fail "installer/linux-only: optional link log is wrong"

# The linux_root symlink has to agree with the uevent resolution, because
# storage_resolve_target cross-checks exactly that node.
installer_linked linux-only linux_root &&
	[ "$(readlink "$installer_root/linux-only/dev/disk/by-partlabel/linux_root")" \
		= "$installer_root/linux-only/dev/sda14" ] ||
	fail "installer/linux-only: linux_root points at the wrong node"

# Each required partition is fatal when missing; no optional one ever is.
installer_expect_refused no-boot-a '' boot_b persist userdata
installer_expect_refused no-boot-b '' boot_a persist userdata
installer_expect_refused no-persist '' boot_a boot_b userdata
installer_expect_ok no-userdata boot_a boot_b persist linux_root
installer_expect_ok no-linux-root boot_a boot_b persist userdata
installer_expect_ok no-linux-home boot_a boot_b persist userdata linux_root

# Duplicates and writable nodes are refused whether the label is required or not.
installer_expect_refused dup-required '' boot_a boot_a boot_b persist
installer_expect_refused dup-optional '' boot_a boot_b persist linux_root linux_root
installer_expect_refused rw-required persist boot_a boot_b persist
installer_expect_refused rw-optional linux_root boot_a boot_b persist linux_root

printf '%s\n' 'liuqin persistent-root fail-closed tests: PASS'

TEST_CHANNEL_MODE=persistent; export TEST_CHANNEL_MODE
if installer_run persistent-no-shell '' boot_a boot_b persist; then
 fail 'persistent boot exposed installer shell'
fi
! grep -q '^telnetd$' "$installer_root/persistent-no-shell/out" || fail 'persistent telnet opened'
unset TEST_CHANNEL_MODE
