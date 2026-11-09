#!/bin/sh
# Host-side execution tests for the device-side layout script.
#
# tests/installer-layout.py covers the host's layout arithmetic and
# tests/installer.py covers the argument refusals, but neither one ever runs a
# function of tools/lib/install-layout.sh.  This suite does: it rewrites the
# script's absolute device paths onto a synthetic sysfs/dev fixture, stubs
# busybox and sgdisk, and runs the real function bodies and the real verbs
# under BusyBox ash -- the shell and the `set -e` semantics of the RAM image.
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
layout_source=${LIUQIN_LAYOUT_SCRIPT:-$project_root/tools/lib/install-layout.sh}
[ -f "$layout_source" ] ||
	{ printf '%s\n' "test-liuqin-layout: no such script: $layout_source" >&2; exit 1; }

host_busybox=${HOST_BUSYBOX:-$(command -v busybox 2>/dev/null || true)}
if [ -n "$host_busybox" ] && [ -x "$host_busybox" ]; then
	shell=$host_busybox
	shell_argument=sh
else
	printf '%s\n' 'test-liuqin-layout: BusyBox is unavailable, falling back to sh' >&2
	host_busybox=
	shell=$(command -v sh)
	shell_argument=
fi

test_root=$(mktemp -d)
cleanup() { rm -rf "$test_root"; }
trap cleanup EXIT HUP INT TERM

fail() { printf 'test-liuqin-layout: %s\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------------------
# Static check.  The installer RAM image is built from a fixed directory list
# that contains no /tmp, so no script that runs inside it may name one: the
# first backup-gpt on the tablet failed because dd could not create its output
# there.  Every device-side script is checked, not just the one this suite
# exercises.
# --------------------------------------------------------------------------
# Whole-line comments are stripped first: they may name the directory to
# explain why it must not be used, and only code that runs on the tablet is
# under test here.
for device_script in tools/lib/install-layout.sh tools/lib/install-root.sh \
	tools/lib/install-switch-store.sh tools/provision-liuqin-from-persist.sh initramfs/init; do
	[ -f "$project_root/$device_script" ] ||
		fail "no such device-side script: $device_script"
	! sed 's/^[[:space:]]*#.*$//' "$project_root/$device_script" |
		grep -nE '/tmp([^a-zA-Z0-9_]|$)' ||
		fail "$device_script names /tmp, which the installer RAM image does not have"
done

# --------------------------------------------------------------------------
# Stub commands.  Only blockdev is simulated: it records the read-only flag of
# each node in a state directory, which is what `seal` must leave behind.
# --------------------------------------------------------------------------
bin=$test_root/bin
mkdir -p "$bin"
cat >"$bin/bb" <<'BB'
#!/bin/sh
set -u
bb_command=$1
shift
printf 'bb %s %s\n' "$bb_command" "$*" >>"$TEST_COMMAND_LOG"
if [ "$bb_command" = dd ] && [ "${TEST_WRITE_FAIL:-0}" = 1 ]; then exit 1; fi
if [ "$bb_command" = dd ] && [ "${TEST_WRITE_SIGNAL:-0}" = 1 ]; then kill -TERM "$PPID"; exit 1; fi
if [ "$bb_command" = blockdev ]; then
	bb_flag=$1
	bb_node=${2##*/}
	case $bb_flag in
	--setro) printf '1\n' >"$TEST_STATE/$bb_node" ;;
	--setrw)
		[ "$bb_node" != "${TEST_FAIL_SETRW:-none}" ] || exit 1
		printf '0\n' >"$TEST_STATE/$bb_node"
		;;
	--rereadpt) : ;;
	*) exit 2 ;;
	esac
	exit 0
fi
if [ -n "${TEST_BUSYBOX:-}" ]; then
	exec "$TEST_BUSYBOX" "$bb_command" "$@"
fi
exec "$bb_command" "$@"
BB
cat >"$bin/sgdisk" <<'SGDISK'
#!/bin/sh
printf 'sgdisk %s\n' "$*" >>"$TEST_COMMAND_LOG"
exit "${TEST_SGDISK_STATUS:-0}"
SGDISK
cat >"$bin/failing-blockdev" <<'FAILING'
#!/bin/sh
printf 'failing-blockdev %s\n' "$*" >>"$TEST_COMMAND_LOG"
exit 1
FAILING
cat >"$bin/sync" <<'SYNC'
#!/bin/sh
exit 0
SYNC
chmod 0755 "$bin"/*

# --------------------------------------------------------------------------
# Fixture.  /sys/class/block entries are symlinks into a devices tree, exactly
# as they are on the tablet: parent_disk resolves the parent through "..", so a
# flat directory would answer with the class directory instead of the disk.
# --------------------------------------------------------------------------
case_root=$test_root/case
dev=$case_root/dev
sys=$case_root/sys
devices=$case_root/devices
state=$case_root/state
mountinfo=$case_root/mountinfo
command_log=$case_root/commands
# The scratch directory the script must create for itself; its parent does not
# exist either, so `mkdir -p` is the only thing that can bring it into being.
work=$case_root/scratch/layout-work

add_part() { # NAME INDEX MAJOR:MINOR SECTORS
	mkdir -p "$devices/sda/sda$2"
	printf 'PARTNAME=%s\n' "$1" >"$devices/sda/sda$2/uevent"
	printf '%s\n' "$3" >"$devices/sda/sda$2/dev"
	printf '%s\n' "$4" >"$devices/sda/sda$2/size"
	printf '%s\n' "$2" >"$devices/sda/sda$2/partition"
	ln -s "../devices/sda/sda$2" "$sys/sda$2"
	: >"$dev/sda$2"
	printf '1\n' >"$state/sda$2"
}

reset_fixture() {
	rm -rf "$case_root"
	# Deliberately no "tmp" directory: the installer RAM image has none, and
	# the scratch directory below is left absent so that every run proves the
	# script creates it for itself.
	mkdir -p "$dev" "$sys" "$devices/sda/queue" "$case_root/etc" "$state"
	printf '4096\n' >"$devices/sda/queue/logical_block_size"
	printf '131072\n' >"$devices/sda/size"
	printf '8:0\n' >"$devices/sda/dev"
	printf 'DEVNAME=sda\n' >"$devices/sda/uevent"
	ln -s '../devices/sda' "$sys/sda"
	truncate -s 67108864 "$dev/sda"
	printf '1\n' >"$state/sda"
	printf 'liuqin\n' >"$case_root/etc/liuqin-installer"
	printf '%s\n' 'c0ffee00-0000-4000-8000-000000000001' >"$case_root/boot_id"
	: >"$mountinfo"
	: >"$command_log"
	# A disk that is not a partition of the target must never be swept in by
	# the "sd*" globs the script uses.
	mkdir -p "$devices/sdb"
	printf 'DEVNAME=sdb\n' >"$devices/sdb/uevent"
	printf '8:16\n' >"$devices/sdb/dev"
	printf '2048\n' >"$devices/sdb/size"
	ln -s '../devices/sdb' "$sys/sdb"
	: >"$dev/sdb"
	printf '1\n' >"$state/sdb"

	add_part boot_a 1 8:1 2048
	add_part metadata 3 8:3 8192
	add_part userdata 35 8:35 34816
	# 17 MiB of 0xff, so a 16 MiB wipe is visible and the byte after it proves
	# the wipe stopped where it was told to.
	dd if=/dev/zero bs=1048576 count=17 2>/dev/null | tr '\000' '\377' >"$dev/sda35"
	# 4 MiB of stable, non-uniform content for the digest verb.
	i=0
	while [ "$i" -lt 64 ]; do
		printf 'liuqin-metadata-block-%03d' "$i"
		i=$((i + 1))
	done | dd of="$dev/sda3" bs=4096 conv=notrunc 2>/dev/null
	truncate -s 4194304 "$dev/sda3"
	# Recognisable head and tail GPT regions inside the 64 MiB disk image.
	dd if=/dev/zero bs=4096 count=6 2>/dev/null | tr '\000' 'A' |
		dd of="$dev/sda" bs=4096 seek=0 conv=notrunc 2>/dev/null
	dd if=/dev/zero bs=4096 count=6 2>/dev/null | tr '\000' 'B' |
		dd of="$dev/sda" bs=4096 seek=16378 conv=notrunc 2>/dev/null
	build_script
}

# --------------------------------------------------------------------------
# Rewrite the script onto the fixture.  Only absolute device paths and the two
# helper binaries move; every guard, every status and every message is the
# shipped text.  `-b` becomes `-e` because a test cannot create block devices.
# --------------------------------------------------------------------------
build_script() {
	script=$case_root/install-layout.sh
	sed "
		s|/sys/class/block|$sys|g
		s|/proc/self/mountinfo|$mountinfo|g
		s|/proc/sys/kernel/random/boot_id|$case_root/boot_id|g
		s|/etc/liuqin-installer|$case_root/etc/liuqin-installer|g
		s|LIUQIN_LAYOUT_WORK:-/run/liuqin-layout|LIUQIN_LAYOUT_WORK:-$work|
		s|^BB=/bin/busybox\$|BB=$bin/bb|
		s|^SGDISK=/usr/sbin/sgdisk\$|SGDISK=$bin/sgdisk|
		s|/dev/\${|$dev/\${|g
		s|\[ -b |[ -e |g
	" "$layout_source" >"$script"
	for stale in '/sys/class/block' '/proc/self/mountinfo' '/bin/busybox' \
		'/usr/sbin/sgdisk' '[ -b ' '/run/liuqin-layout'; do
		! grep -qF "$stale" "$script" ||
			fail "the fixture rewrite missed $stale; the script's paths changed"
	done
	grep -qF "$work" "$script" || fail 'the scratch-directory rewrite matched nothing'
	grep -qF "if=/dev/zero" "$script" || fail 'the zero source was rewritten away'
	grep -qF "$dev/\${" "$script" || fail 'the /dev rewrite matched nothing'

	# The function bundle, in the style of tests/persistent-root.sh: die, the
	# resolved BB, and every function body, with no top-level guard or verb.
	functions=$case_root/functions.sh
	{
		printf 'set -eu\n'
		sed -n '/^die() /p' "$script"
		sed -n '/^BB=/p' "$script"
		sed -n '/^[a-z_]*() {$/,/^}$/p' "$script"
	} >"$functions"
	for name in find_part parent_disk sector_size sector_count byte_size \
		assert_idle seal block_digest; do
		grep -q "^$name() {\$" "$functions" ||
			fail "function $name was not extracted; the script's shape changed"
	done
}

# --------------------------------------------------------------------------
# Runners.  Both report the exit status in $status and the output in $output.
# --------------------------------------------------------------------------
run_env() {
	env PATH="$bin:/usr/bin:/bin" \
		TEST_BUSYBOX="$host_busybox" \
		TEST_STATE="$state" \
		TEST_COMMAND_LOG="$command_log" \
		TEST_FAIL_SETRW="${TEST_FAIL_SETRW:-none}" \
		TEST_SGDISK_STATUS="${TEST_SGDISK_STATUS:-0}" \
		TEST_WRITE_FAIL="${TEST_WRITE_FAIL:-0}" TEST_WRITE_SIGNAL="${TEST_WRITE_SIGNAL:-0}" \
		"$shell" $shell_argument "$@"
}

run_fn() { # the shell code to append to the function bundle, on stdin
	{ cat "$functions"; cat; } >"$case_root/run.sh"
	status=0
	output=$(run_env "$case_root/run.sh" 2>&1) || status=$?
}

run_verb() { # VERB ARGUMENT...
	status=0
	output=$(run_env "$script" "$(cat "$case_root/boot_id")" "$@" 2>&1) || status=$?
}

expect_ok() { # NAME
	[ "$status" = 0 ] ||
		fail "$1: expected success, got status $status: $output"
}

expect_die() { # NAME MESSAGE-FRAGMENT
	[ "$status" != 0 ] || fail "$1: expected a refusal, got success: $output"
	case $output in
	*"liuqin-layout: $2"*) ;;
	*) fail "$1: expected the message '$2', got: $output" ;;
	esac
}

expect_output() { # NAME FRAGMENT
	case $output in
	*"$2"*) ;;
	*) fail "$1: expected the output '$2', got: $output" ;;
	esac
}

# --------------------------------------------------------------------------
# assert_idle.  The regression this suite was written for: with no partition
# of the disk mounted, the awk probe exits non-zero, and that status must not
# become the status of the loop, of the function or of the script.
# --------------------------------------------------------------------------
reset_fixture
run_fn <<EOF
assert_idle "$dev/sda"
printf 'IDLE_OK\n'
EOF
expect_ok assert-idle-unmounted
expect_output assert-idle-unmounted IDLE_OK

reset_fixture
run_fn <<EOF
assert_idle "$dev/sda"
EOF
expect_ok assert-idle-is-the-last-statement

# The same call in the shape the wipe-head verb uses it: a plain command under
# `set -e`, where a stray non-zero status aborts the script with no message.
reset_fixture
run_fn <<EOF
trap 'printf "ABORTED %s\n" "\$?"' EXIT
assert_idle "$dev/sda"
printf 'REACHED_THE_WRITE\n'
EOF
expect_ok assert-idle-does-not-abort
expect_output assert-idle-does-not-abort REACHED_THE_WRITE

reset_fixture
printf '36 35 8:35 / /mnt rw,relatime - ext4 /dev/sda35 rw\n' >"$mountinfo"
run_fn <<EOF
assert_idle "$dev/sda"
printf 'IDLE_OK\n'
EOF
expect_die assert-idle-mounted 'a partition of the target disk is mounted'
case $output in *IDLE_OK*) fail 'assert-idle-mounted: continued past the refusal' ;; esac

# A mount of an unrelated disk is not a mount of the target.
reset_fixture
printf '36 35 8:16 / /mnt rw,relatime - ext4 /dev/sdb rw\n' >"$mountinfo"
run_fn <<EOF
assert_idle "$dev/sda"
printf 'IDLE_OK\n'
EOF
expect_ok assert-idle-foreign-mount
expect_output assert-idle-foreign-mount IDLE_OK

# --------------------------------------------------------------------------
# find_part and parent_disk.
# --------------------------------------------------------------------------
reset_fixture
run_fn <<EOF
part=\$(find_part userdata)
printf 'PART %s\n' "\$part"
printf 'DISK %s\n' "\$(parent_disk "\$part")"
EOF
expect_ok find-part-resolves
expect_output find-part-resolves "PART $dev/sda35"
expect_output find-part-resolves "DISK $dev/sda"

reset_fixture
run_fn <<EOF
find_part linux_root
printf 'RESOLVED\n'
EOF
expect_die find-part-missing 'partition is missing: linux_root'
case $output in *RESOLVED*) fail 'find-part-missing: continued past the refusal' ;; esac

reset_fixture
add_part userdata 36 8:36 34816
run_fn <<EOF
find_part userdata
printf 'RESOLVED\n'
EOF
expect_die find-part-duplicate 'duplicate partition name: userdata'
case $output in *RESOLVED*) fail 'find-part-duplicate: continued past the refusal' ;; esac

# A partition whose node has disappeared is missing, not resolved.
reset_fixture
rm -f "$dev/sda35"
run_fn <<EOF
find_part userdata
EOF
expect_die find-part-no-node 'partition is missing: userdata'

# --------------------------------------------------------------------------
# Geometry helpers.
# --------------------------------------------------------------------------
reset_fixture
run_fn <<EOF
printf 'SECTOR %s\n' "\$(sector_size "$dev/sda")"
printf 'SECTORS %s\n' "\$(sector_count "$dev/sda")"
printf 'BYTES %s\n' "\$(byte_size "$dev/sda35")"
EOF
expect_ok geometry-helpers
expect_output geometry-helpers 'SECTOR 4096'
expect_output geometry-helpers 'SECTORS 16384'
expect_output geometry-helpers 'BYTES 17825792'

reset_fixture
rm -f "$devices/sda/queue/logical_block_size"
run_fn <<EOF
sector_count "$dev/sda"
EOF
expect_die geometry-no-sector-size 'logical sector size is unavailable'

reset_fixture
printf 'not-a-number\n' >"$devices/sda/size"
run_fn <<EOF
sector_count "$dev/sda"
EOF
expect_die geometry-bad-size 'disk size is unavailable'

# --------------------------------------------------------------------------
# seal: the whole disk and every partition of it read-only again, and nothing
# else touched.
# --------------------------------------------------------------------------
reset_fixture
for node in sda sda1 sda3 sda35; do printf '0\n' >"$state/$node"; done
printf '0\n' >"$state/sdb"
run_fn <<EOF
seal "$dev/sda"
printf 'SEALED\n'
EOF
expect_ok seal-restores-read-only
expect_output seal-restores-read-only SEALED
for node in sda sda1 sda3 sda35; do
	[ "$(cat "$state/$node")" = 1 ] || fail "seal: $node was left writable"
done
[ "$(cat "$state/sdb")" = 0 ] || fail 'seal: touched a disk that is not the target'

reset_fixture
run_fn <<EOF
BB=$bin/failing-blockdev
seal "$dev/sda"
printf 'SEALED\n'
EOF
expect_die seal-refuses-a-failed-flag 'cannot restore the read-only flag'

# --------------------------------------------------------------------------
# wipe-head, end to end through the shipped verb dispatch.
# --------------------------------------------------------------------------
reset_fixture
run_verb wipe-head userdata 16777216
expect_ok wipe-head
expect_output wipe-head 'liuqin-layout: WIPED userdata'
cmp -s -n 16777216 "$dev/sda35" /dev/zero || fail 'wipe-head: the head was not zeroed'
[ "$(dd if="$dev/sda35" bs=1 skip=16777216 count=1 2>/dev/null | od -An -tx1 | tr -d ' ')" = ff ] ||
	fail 'wipe-head: the wipe ran past the requested length'
[ "$(cat "$state/sda")" = 1 ] || fail 'wipe-head: the disk was left writable'
[ "$(cat "$state/sda35")" = 1 ] || fail 'wipe-head: the partition was left writable'
grep -q -- '--setrw' "$command_log" || fail 'wipe-head: never opened the write window'

reset_fixture
printf '36 35 8:35 / /mnt rw,relatime - ext4 /dev/sda35 rw\n' >"$mountinfo"
run_verb wipe-head userdata 16777216
expect_die wipe-head-mounted 'a partition of the target disk is mounted'
! grep -q -- '--setrw' "$command_log" || fail 'wipe-head-mounted: opened the write window anyway'
[ "$(dd if="$dev/sda35" bs=1 count=1 2>/dev/null | od -An -tx1 | tr -d ' ')" = ff ] ||
	fail 'wipe-head-mounted: wrote to a mounted disk'

reset_fixture
run_verb wipe-head userdata 4096
expect_die wipe-head-partial-mebibyte 'wipe length must be a whole number of mebibytes'
! grep -q -- '--setrw' "$command_log" ||
	fail 'wipe-head-partial-mebibyte: opened the write window for a wipe of zero blocks'

reset_fixture
run_verb wipe-head userdata 67108864
expect_die wipe-head-too-large 'partition is smaller than the requested wipe'

reset_fixture
run_verb wipe-head userdata 0
expect_die wipe-head-zero 'wipe length is out of range'

reset_fixture
run_verb wipe-head userdata 16MiB
expect_die wipe-head-not-a-number 'invalid wipe length'

# --------------------------------------------------------------------------
# The read-only verbs.
# --------------------------------------------------------------------------
reset_fixture
run_verb geometry userdata
expect_ok geometry-verb
expect_output geometry-verb "disk $dev/sda"
expect_output geometry-verb 'sector 4096'
expect_output geometry-verb 'sectors 16384'

reset_fixture
[ ! -e "$work" ] || fail 'the fixture left a scratch directory behind'
run_verb backup-gpt userdata head
expect_ok backup-gpt-head
printf '%s\n' "$output" | base64 -d >"$case_root/head.bin"
[ "$(wc -c <"$case_root/head.bin")" = 24576 ] || fail 'backup-gpt head: wrong length'
dd if="$dev/sda" bs=4096 count=6 of="$case_root/head.expected" 2>/dev/null
cmp -s "$case_root/head.bin" "$case_root/head.expected" ||
	fail 'backup-gpt head: did not return the head of the disk'
# The directory the RAM image does not ship must be created by the script, and
# the copy it read through must not survive the verb.
[ -d "$work" ] || fail 'backup-gpt head: the scratch directory was not created'
[ -z "$(ls -A "$work")" ] || fail 'backup-gpt head: left a temporary file behind'
[ ! -e "$case_root/tmp" ] || fail 'backup-gpt head: wrote below /tmp'

reset_fixture
run_verb backup-gpt userdata tail
expect_ok backup-gpt-tail
printf '%s\n' "$output" | base64 -d >"$case_root/tail.bin"
dd if="$dev/sda" bs=4096 skip=16378 count=6 of="$case_root/tail.expected" 2>/dev/null
cmp -s "$case_root/tail.bin" "$case_root/tail.expected" ||
	fail 'backup-gpt tail: did not return the tail of the disk'
[ -z "$(ls -A "$work")" ] || fail 'backup-gpt tail: left a temporary file behind'

# --------------------------------------------------------------------------
# restore-gpt, staged the way the host stages it: two base64 halves in the
# same scratch directory, written into a corrupted disk image and compared
# byte for byte.
# --------------------------------------------------------------------------
stage_halves() { # the pristine GPT regions, as base64 files
	mkdir -p "$work"
	dd if="$dev/sda" bs=4096 count=6 of="$case_root/head.expected" 2>/dev/null
	dd if="$dev/sda" bs=4096 skip=16378 count=6 of="$case_root/tail.expected" 2>/dev/null
	base64 <"$case_root/head.expected" >"$work/liuqin-gpt-head.b64"
	base64 <"$case_root/tail.expected" >"$work/liuqin-gpt-tail.b64"
}

corrupt_gpt() {
	dd if=/dev/zero bs=4096 count=6 2>/dev/null | tr '\000' 'X' |
		dd of="$dev/sda" bs=4096 seek=0 conv=notrunc 2>/dev/null
	dd if=/dev/zero bs=4096 count=6 2>/dev/null | tr '\000' 'Y' |
		dd of="$dev/sda" bs=4096 seek=16378 conv=notrunc 2>/dev/null
}

reset_fixture
stage_halves
corrupt_gpt
run_verb restore-gpt userdata
expect_ok restore-gpt
expect_output restore-gpt 'liuqin-layout: GPT_RESTORED'
dd if="$dev/sda" bs=4096 count=6 of="$case_root/head.after" 2>/dev/null
dd if="$dev/sda" bs=4096 skip=16378 count=6 of="$case_root/tail.after" 2>/dev/null
cmp -s "$case_root/head.after" "$case_root/head.expected" ||
	fail 'restore-gpt: the primary GPT was not restored'
cmp -s "$case_root/tail.after" "$case_root/tail.expected" ||
	fail 'restore-gpt: the backup GPT was not restored'
[ "$(cat "$state/sda")" = 1 ] || fail 'restore-gpt: the disk was left writable'
[ -z "$(ls -A "$work")" ] || fail 'restore-gpt: left the staged copies behind'
[ ! -e "$case_root/tmp" ] || fail 'restore-gpt: wrote below /tmp'

# Nothing staged is a refusal, not a write of whatever happens to be there.
reset_fixture
corrupt_gpt
run_verb restore-gpt userdata
expect_die restore-gpt-unstaged 'staged GPT copy is missing: head'
! grep -q -- '--setrw' "$command_log" ||
	fail 'restore-gpt-unstaged: opened the write window anyway'

reset_fixture
stage_halves
: >"$work/liuqin-gpt-tail.b64"
run_verb restore-gpt userdata
expect_die restore-gpt-short 'staged GPT copy has the wrong size: tail'
! grep -q -- '--setrw' "$command_log" ||
	fail 'restore-gpt-short: opened the write window anyway'

reset_fixture
run_verb backup-gpt userdata middle
expect_die backup-gpt-half 'backup-gpt takes head or tail'

reset_fixture
run_verb digest metadata 4096
expect_ok digest-verb
expect_output digest-verb 'size 4194304'
expect_output digest-verb "content $(dd if="$dev/sda3" bs=4096 count=1 2>/dev/null |
	sha256sum | cut -d' ' -f1)"
expect_output digest-verb "padding $(dd if="$dev/sda3" bs=4096 skip=1 count=1023 2>/dev/null |
	sha256sum | cut -d' ' -f1)"

reset_fixture
run_verb digest metadata 1024
expect_die digest-block-size 'length must be a whole number of 4096-byte blocks'

# --------------------------------------------------------------------------
# The RAM-image guards, on the real dispatch.
# --------------------------------------------------------------------------
reset_fixture
status=0
output=$(run_env "$script" 'c0ffee00-0000-4000-8000-000000000002' geometry userdata 2>&1) ||
	status=$?
expect_die boot-id 'RAM boot identity changed'

reset_fixture
printf 'android\n' >"$case_root/etc/liuqin-installer"
run_verb geometry userdata
expect_die installer-marker 'not the installer RAM image'

reset_fixture
run_verb nonsense userdata
expect_die unknown-verb 'unknown verb: nonsense'

reset_fixture
status=0
output=$(run_env "$script" "$(cat "$case_root/boot_id")" 2>&1) || status=$?
expect_die usage 'usage: install-layout.sh'

printf '%s\n' 'liuqin install-layout execution tests: PASS'

# Failure and signals after the write window opens always attempt full resealing.
for failure in write signal partition-open; do
 reset_fixture
 case $failure in
 write) TEST_WRITE_FAIL=1; export TEST_WRITE_FAIL;;
 signal) TEST_WRITE_SIGNAL=1; export TEST_WRITE_SIGNAL;;
 partition-open) TEST_FAIL_SETRW=sda35; export TEST_FAIL_SETRW;;
 esac
 run_verb wipe-head userdata 16777216
 [ "$status" != 0 ] || fail "$failure: unexpectedly succeeded"
 for node in sda sda1 sda3 sda35; do
  [ "$(cat "$state/$node")" = 1 ] || fail "$failure: $node left writable"
 done
 unset TEST_WRITE_FAIL TEST_WRITE_SIGNAL TEST_FAIL_SETRW
done
printf '%s\n' 'PASS: write failure, signal and partial-open failure reseal storage'
