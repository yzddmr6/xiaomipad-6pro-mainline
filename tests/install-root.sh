#!/bin/sh
# Host-side execution tests for the /home handling of tools/lib/install-root.sh.
#
# tests/installer.py covers the option scan's refusals; this suite runs the
# home functions themselves -- the label check, e2fsck, the format path and
# the skeleton decision -- under BusyBox ash, the shell of the RAM image.  The
# script's absolute tool paths are rewritten onto logging wrappers around the
# host's e2fsprogs and BusyBox, and the "device" is an ext4 image file made by
# the host's mkfs.ext4: every e2fsprogs tool works on a regular file, so no
# mount, loop device or root is needed.
# The static checks match the script's literal text, $-expressions included.
# shellcheck disable=SC2016
set -eu

project_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
root_source=${LIUQIN_ROOT_SCRIPT:-$project_root/tools/lib/install-root.sh}
[ -f "$root_source" ] ||
	{ printf '%s\n' "test-liuqin-root: no such script: $root_source" >&2; exit 1; }

host_busybox=${HOST_BUSYBOX:-$(command -v busybox 2>/dev/null || true)}
if [ -n "$host_busybox" ] && [ -x "$host_busybox" ]; then
	shell=$host_busybox
	shell_argument='sh'
else
	printf '%s\n' 'test-liuqin-root: BusyBox is unavailable, falling back to sh' >&2
	host_busybox=
	shell=$(command -v sh)
	shell_argument=
fi

tool() { PATH="$PATH:/usr/sbin:/sbin" command -v "$1" 2>/dev/null || true; }
real_mkfs=$(tool mkfs.ext4)
real_e2fsck=$(tool e2fsck)
real_e2label=$(tool e2label)
real_debugfs=$(tool debugfs)

test_root=$(mktemp -d)
cleanup() { rm -rf "$test_root"; }
trap cleanup EXIT HUP INT TERM

fail() { printf 'test-liuqin-root: %s\n' "$*" >&2; exit 1; }
skipped=

# --------------------------------------------------------------------------
# Static checks on the shipped script: the order of the home steps is what
# keeps a home that cannot be kept from costing the installed system.
# --------------------------------------------------------------------------
line_of() { # FIXED-STRING: the line number of its only occurrence
	lo_lines=$(grep -nF -- "$1" "$root_source" | cut -d: -f1)
	[ -n "$lo_lines" ] || fail "the script no longer contains: $1"
	[ "$(printf '%s\n' "$lo_lines" | wc -l)" = 1 ] || fail "the script contains more than once: $1"
	printf '%s' "$lo_lines"
}
early_check=$(line_of '[ -z "$keep_home" ] || check_home "$home"')
download=$(line_of '/bin/busybox wget -O "$archive" "$2"')
open_home=$(line_of '/bin/busybox blockdev --setrw "$home"')
prepare=$(line_of 'prepare_home "$home" "$keep_home"')
format_root=$(line_of '/usr/sbin/mkfs.ext4 -F -L LIUQIN_ROOT -m 0 "$root"')
seed=$(line_of 'if home_needs_skeleton /mnt/install-home "$keep_home" && [ -d /mnt/install/native-root/home ]; then')
[ "$early_check" -lt "$download" ] || fail 'KEEP-HOME is checked only after the archive download'
[ "$open_home" -lt "$prepare" ] && [ "$prepare" -lt "$format_root" ] ||
	fail 'the home is not prepared inside the write window, before the system partition is formatted'
# Every other mkfs is the one in prepare_home, so KEEP-HOME decides it.
prepare_lines=$(sed -n '/^prepare_home() {/,/^}$/=' "$root_source")
formats=$(sed 's/^[[:space:]]*#.*$//' "$root_source" | grep -n 'mkfs' | cut -d: -f1)
[ "$(printf '%s\n' "$formats" | wc -l)" = 2 ] || fail 'the script no longer formats exactly two partitions'
for format_line in $formats; do
	[ "$format_line" = "$format_root" ] || printf '%s\n' "$prepare_lines" | grep -qx "$format_line" ||
		fail "line $format_line formats a partition outside prepare_home"
done
sed -n "$((seed + 1))p" "$root_source" | grep -qF '/usr/bin/tar -C /mnt/install/native-root/home' ||
	fail 'the skeleton copy is not the step home_needs_skeleton guards'

# --------------------------------------------------------------------------
# Wrappers.  Each logs its command line and runs the host's tool; e2fsck can
# be told to report a given status instead, for the exact threshold.
# --------------------------------------------------------------------------
bin=$test_root/bin
mkdir -p "$bin"
cat >"$bin/bb" <<'BB'
#!/bin/sh
printf 'bb %s\n' "$*" >>"$TEST_COMMAND_LOG"
if [ -n "${TEST_BUSYBOX:-}" ]; then
	exec "$TEST_BUSYBOX" "$@"
fi
exec "$@"
BB
for name in mkfs.ext4 e2fsck e2label; do
	cat >"$bin/$name" <<EOF
#!/bin/sh
printf '%s %s\n' '$name' "\$*" >>"\$TEST_COMMAND_LOG"
if [ '$name' = e2fsck ] && [ -n "\${TEST_E2FSCK_STATUS:-}" ]; then
	exit "\$TEST_E2FSCK_STATUS"
fi
exec "\$TEST_REAL_$(printf '%s' "$name" | tr 'a-z.2' 'A-Z_2')" "\$@"
EOF
done
chmod 0755 "$bin"/*

case_root=$test_root/case
command_log=$case_root/commands

# --------------------------------------------------------------------------
# Rewrite the script onto the wrappers.  Only absolute tool and RAM-image
# paths move; every guard and message is the shipped text.  LABEL_TOOL is
# where /usr/sbin/e2label goes: the wrapper, or a path that does not exist
# for the superblock fallback of an installer image built without e2label.
# --------------------------------------------------------------------------
build_script() { # LABEL_TOOL
	mkdir -p "$case_root/etc"
	script=$case_root/install-root.sh
	sed "
		s|/usr/sbin/mkfs.ext4|$bin/mkfs.ext4|g
		s|/usr/sbin/e2fsck|$bin/e2fsck|g
		s|/usr/sbin/e2label|$1|g
		s|/bin/busybox|$bin/bb|g
		s|/proc/sys/kernel/random/boot_id|$case_root/boot_id|g
		s|/etc/liuqin-installer|$case_root/etc/liuqin-installer|g
	" "$root_source" >"$script"
	for stale in /usr/sbin/mkfs.ext4 /usr/sbin/e2fsck /usr/sbin/e2label /bin/busybox \
		/proc/sys/kernel/random/boot_id ' /etc/liuqin-installer'; do
		! grep -qF -- "$stale" "$script" ||
			fail "the fixture rewrite missed $stale; the script's paths changed"
	done
	functions=$case_root/functions.sh
	{
		printf 'set -eu\n'
		sed -n '/^die() /p' "$script"
		for name in home_label check_home prepare_home home_needs_skeleton; do
			sed -n "/^$name() {/,/^}\$/p" "$script"
		done
	} >"$functions"
	for name in die home_label check_home prepare_home home_needs_skeleton; do
		grep -q "^$name() {" "$functions" ||
			fail "function $name was not extracted; the script's shape changed"
	done
}

reset_case() { # LABEL_TOOL
	rm -rf "$case_root"
	mkdir -p "$case_root"
	: >"$command_log"
	printf '%s\n' 'c0ffee00-0000-4000-8000-000000000001' >"$case_root/boot_id"
	build_script "$1"
	printf 'liuqin\n' >"$case_root/etc/liuqin-installer"
}
TEST_E2FSCK_STATUS=

run_env() {
	env PATH="$bin:/usr/bin:/bin" \
		TEST_BUSYBOX="$host_busybox" \
		TEST_COMMAND_LOG="$command_log" \
		TEST_E2FSCK_STATUS="${TEST_E2FSCK_STATUS:-}" \
		TEST_REAL_MKFS_EXT4="$real_mkfs" \
		TEST_REAL_E2FSCK="$real_e2fsck" \
		TEST_REAL_E2LABEL="$real_e2label" \
		"$shell" $shell_argument "$@"
}

run_fn() { # the shell code to append to the function bundle, on stdin
	{ cat "$functions"; cat; } >"$case_root/run.sh"
	status=0
	output=$(run_env "$case_root/run.sh" 2>&1) || status=$?
}

run_script() { # ARGUMENT...
	status=0
	output=$(run_env "$script" "$@" 2>&1) || status=$?
}

expect_ok() { # NAME
	[ "$status" = 0 ] || fail "$1: expected success, got status $status: $output"
}

expect_die() { # NAME MESSAGE-FRAGMENT
	[ "$status" != 0 ] || fail "$1: expected a refusal, got success: $output"
	case $output in
	*"liuqin-install: "*"$2"*) ;;
	*) fail "$1: expected the message '$2', got: $output" ;;
	esac
}

expect_output() { # NAME FRAGMENT
	case $output in
	*"$2"*) ;;
	*) fail "$1: expected the output '$2', got: $output" ;;
	esac
}

expect_no_output() { # NAME FRAGMENT
	case $output in
	*"$2"*) fail "$1: unexpected output '$2': $output" ;;
	esac
}

logged() { grep -q "^$1 " "$command_log"; }

# --------------------------------------------------------------------------
# The option scan under BusyBox ash.  Reaching the boot-identity check means
# the options were accepted; nothing before it touches a device.
# --------------------------------------------------------------------------
fixed='c0ffee00-0000-4000-8000-000000000002 u s 1 ERASE-LIUQIN-USERDATA linux_root linux_home'
reset_case "$bin/e2label"
for options in '' 'KEEP-HOME' 'ENABLE-USB-RESCUE KEEP-HOME' 'KEEP-HOME ENABLE-USB-RESCUE'; do
	# shellcheck disable=SC2086
	run_script $fixed $options
	expect_die "options '$options'" 'RAM boot identity changed'
done
for options in 'KEEP-HOME KEEP-HOME' 'ENABLE-USB-RESCUE KEEP-HOME ENABLE-USB-RESCUE' \
	'KEEP-HOME UNKNOWN' 'KEEP-HOME SWITCH-STORE'; do
	# shellcheck disable=SC2086
	run_script $fixed $options
	expect_die "options '$options'" 'usage: install-root.sh'
	expect_no_output "options '$options'" 'RAM boot identity changed'
done

# --------------------------------------------------------------------------
# The skeleton decision, on plain directories.
# --------------------------------------------------------------------------
skeleton_case() { # NAME MODE EXPECTED(SEED|KEEP) [MESSAGE]
	run_fn <<EOF
if home_needs_skeleton "$case_root/home" "$2"; then printf 'DECISION SEED\n'; else printf 'DECISION KEEP\n'; fi
EOF
	expect_ok "$1"
	expect_output "$1" "DECISION $3"
	if [ -n "${4:-}" ]; then
		expect_output "$1" "liuqin-install: $4"
	else
		expect_no_output "$1" 'liuqin-install:'
	fi
}

reset_case "$bin/e2label"
mkdir -p "$case_root/home/lost+found" "$case_root/home/alice"
skeleton_case fresh-home-always-seeded '' SEED

reset_case "$bin/e2label"
mkdir -p "$case_root/home/lost+found"
skeleton_case kept-home-only-lost-found KEEP-HOME SEED 'home partition is empty; seeding the skeleton'

reset_case "$bin/e2label"
mkdir -p "$case_root/home"
skeleton_case kept-home-empty KEEP-HOME SEED 'home partition is empty; seeding the skeleton'

reset_case "$bin/e2label"
mkdir -p "$case_root/home/lost+found" "$case_root/home/alice"
: >"$case_root/home/.hidden"
: >"$case_root/home/..odd"
ln -s nowhere "$case_root/home/dangling"
skeleton_case kept-home-with-users KEEP-HOME KEEP 'keeping existing /home (4 entries)'

reset_case "$bin/e2label"
mkdir -p "$case_root/home"
: >"$case_root/home/.profile"
skeleton_case kept-home-dotfile-only KEEP-HOME KEEP 'keeping existing /home (1 entries)'

# --------------------------------------------------------------------------
# prepare_home on ext4 image files.
# --------------------------------------------------------------------------
if [ -z "$real_mkfs" ] || [ -z "$real_e2fsck" ]; then
	printf '%s\n' 'SKIP: prepare_home on ext4 images (mkfs.ext4 or e2fsck is unavailable on this host)'
	skipped=yes
else
	seed_dir=$test_root/seed
	mkdir -p "$seed_dir/alice"
	printf 'keep me\n' >"$seed_dir/alice/notes.txt"
	printf 'export LIUQIN=1\n' >"$seed_dir/alice/.profile"

	make_image() { # PATH LABEL: a 32 MiB ext4 image holding the seed tree
		truncate -s 33554432 "$1"
		"$real_mkfs" -q -F -L "$2" -m 0 -d "$seed_dir" "$1"
	}
	notes() { # IMAGE: the seeded file, or nothing
		"$real_debugfs" -R 'cat /alice/notes.txt' "$1" 2>/dev/null || true
	}
	digest() { sha256sum "$1" | cut -d' ' -f1; }

	for label_tool in "$bin/e2label" "$bin/no-e2label-in-this-image"; do
		if [ "$label_tool" = "$bin/e2label" ] && [ -z "$real_e2label" ]; then
			printf '%s\n' 'SKIP: the e2label path (e2label is unavailable on this host)'
			skipped=yes
			continue
		fi
		kind=${label_tool##*/}

		# A LIUQIN_HOME filesystem is checked and kept, byte for byte when
		# it is clean.
		reset_case "$label_tool"
		image=$case_root/home.img
		make_image "$image" LIUQIN_HOME
		before=$(digest "$image")
		run_fn <<EOF
prepare_home "$image" KEEP-HOME
printf 'PREPARED\n'
EOF
		expect_ok "$kind: keep-liuqin-home"
		expect_output "$kind: keep-liuqin-home" PREPARED
		logged e2fsck || fail "$kind: keep-liuqin-home: e2fsck did not run"
		grep -qxF "e2fsck -p $image" "$command_log" ||
			fail "$kind: keep-liuqin-home: e2fsck was not run as e2fsck -p"
		! logged mkfs.ext4 || fail "$kind: keep-liuqin-home: the kept home was formatted"
		[ "$(digest "$image")" = "$before" ] || fail "$kind: keep-liuqin-home: the clean home was modified"
		[ -z "$real_debugfs" ] || [ "$(notes "$image")" = 'keep me' ] ||
			fail "$kind: keep-liuqin-home: the user's file is gone"
		if [ "$kind" = e2label ]; then
			logged e2label || fail 'keep-liuqin-home: e2label was not used'
		else
			! logged e2label || fail 'fallback: called e2label that the image does not have'
			grep -q '^bb dd ' "$command_log" || fail 'fallback: the superblock was not read'
		fi

		# Any other label, a near miss included, is refused before e2fsck
		# and without a write.
		for label in OTHER LIUQIN_HOME_OLD LIUQIN_ROOT; do
			reset_case "$label_tool"
			image=$case_root/home.img
			make_image "$image" "$label"
			before=$(digest "$image")
			run_fn <<EOF
prepare_home "$image" KEEP-HOME
printf 'PREPARED\n'
EOF
			expect_die "$kind: keep-label-$label" "does not hold the LIUQIN_HOME filesystem (label '$label')"
			expect_no_output "$kind: keep-label-$label" PREPARED
			! logged e2fsck || fail "$kind: keep-label-$label: e2fsck ran on a foreign filesystem"
			! logged mkfs.ext4 || fail "$kind: keep-label-$label: formatted"
			[ "$(digest "$image")" = "$before" ] || fail "$kind: keep-label-$label: the partition was modified"
		done

		# No filesystem at all.
		reset_case "$label_tool"
		image=$case_root/home.img
		truncate -s 33554432 "$image"
		run_fn <<EOF
check_home "$image"
printf 'CHECKED\n'
EOF
		expect_die "$kind: keep-unformatted" "does not hold the LIUQIN_HOME filesystem (label '')"
		expect_no_output "$kind: keep-unformatted" CHECKED
	done

	if [ -z "$real_debugfs" ]; then
		printf '%s\n' 'SKIP: e2fsck on damaged images (debugfs is unavailable on this host)'
		skipped=yes
	else
		# Errors e2fsck -p fixes by itself (status 1) are accepted.
		reset_case "$bin/e2label"
		image=$case_root/home.img
		make_image "$image" LIUQIN_HOME
		"$real_debugfs" -w -R 'ssv state 2' "$image" >/dev/null 2>&1
		run_fn <<EOF
prepare_home "$image" KEEP-HOME
printf 'PREPARED\n'
EOF
		expect_ok keep-fixable-errors
		expect_output keep-fixable-errors PREPARED
		[ "$(notes "$image")" = 'keep me' ] || fail "keep-fixable-errors: the user's file is gone"
		"$real_e2fsck" -n "$image" >/dev/null 2>&1 || fail 'keep-fixable-errors: the filesystem was left unclean'

		# Damage e2fsck -p will not repair (status 4) stops the installation.
		reset_case "$bin/e2label"
		image=$case_root/home.img
		make_image "$image" LIUQIN_HOME
		"$real_debugfs" -w -R 'clri <2>' "$image" >/dev/null 2>&1
		"$real_debugfs" -w -R 'ssv state 2' "$image" >/dev/null 2>&1
		run_fn <<EOF
prepare_home "$image" KEEP-HOME
printf 'PREPARED\n'
EOF
		expect_die keep-unrepairable 'e2fsck -p'
		expect_output keep-unrepairable 'exited with status 4'
		expect_no_output keep-unrepairable PREPARED
		! logged mkfs.ext4 || fail 'keep-unrepairable: formatted'
	fi

	# The exact threshold: 1 passes, 2 and above stop.
	for e2fsck_status in 1 2 8; do
		reset_case "$bin/e2label"
		image=$case_root/home.img
		make_image "$image" LIUQIN_HOME
		TEST_E2FSCK_STATUS=$e2fsck_status
		run_fn <<EOF
prepare_home "$image" KEEP-HOME
printf 'PREPARED\n'
EOF
		TEST_E2FSCK_STATUS=
		if [ "$e2fsck_status" = 1 ]; then
			expect_ok e2fsck-status-1
			expect_output e2fsck-status-1 PREPARED
		else
			expect_die "e2fsck-status-$e2fsck_status" "exited with status $e2fsck_status"
			expect_no_output "e2fsck-status-$e2fsck_status" PREPARED
		fi
	done

	# Without KEEP-HOME the partition is formatted, whatever it held.
	reset_case "$bin/e2label"
	image=$case_root/home.img
	make_image "$image" OTHER
	run_fn <<EOF
prepare_home "$image" ''
printf 'PREPARED\n'
EOF
	expect_ok format-home
	expect_output format-home PREPARED
	grep -qxF "mkfs.ext4 -F -L LIUQIN_HOME -m 0 $image" "$command_log" ||
		fail "format-home: not formatted as LIUQIN_HOME: $(cat "$command_log")"
	! logged e2fsck || fail 'format-home: checked a filesystem it formats'
	! logged e2label || fail 'format-home: read the label of a filesystem it formats'
	[ -z "$real_e2label" ] || [ "$("$real_e2label" "$image")" = LIUQIN_HOME ] ||
		fail 'format-home: the new filesystem is not labelled LIUQIN_HOME'
	[ -z "$real_debugfs" ] || [ -z "$(notes "$image")" ] || fail 'format-home: the old content survived'

	# A mode other than the two is refused, not treated as either.
	reset_case "$bin/e2label"
	image=$case_root/home.img
	make_image "$image" LIUQIN_HOME
	before=$(digest "$image")
	run_fn <<EOF
prepare_home "$image" KEEP
printf 'PREPARED\n'
EOF
	expect_die unknown-home-mode 'unsupported home mode'
	! logged mkfs.ext4 || fail 'unknown-home-mode: formatted'
	[ "$(digest "$image")" = "$before" ] || fail 'unknown-home-mode: the partition was modified'
fi

if [ -n "$skipped" ]; then
	printf '%s\n' 'liuqin install-root home tests: PASS (with the skips above)'
else
	printf '%s\n' 'liuqin install-root home tests: PASS'
fi

# Execute the shipped cleanup after an unmount failure. Every read-only flag
# must still be attempted, and the operation must remain a failure.
cat >"$bin/cleanup-bb" <<'BB'
#!/bin/sh
printf '%s\n' "$*" >>"$TEST_COMMAND_LOG"
exit 0
BB
chmod +x "$bin/cleanup-bb"
{
 printf 'home_mounted=true; mounted=true; opened=true; persist_mounted=true; download_mounted=true\n'
 printf 'home=/dev/fixture-home; root=/dev/fixture-root; parent=/dev/fixture-disk\n'
 printf 'sync() { :; }; umount() { echo "umount $*" >>"$TEST_COMMAND_LOG"; return 1; }\n'
 sed -n '/^cleanup() {/,/^}/p' "$root_source" | sed "s|/bin/busybox|$bin/cleanup-bb|g"
 printf 'cleanup\n'
} >"$case_root/cleanup.sh"
: >"$command_log"
if TEST_COMMAND_LOG="$command_log" "$shell" $shell_argument "$case_root/cleanup.sh"; then
 fail 'cleanup reports success after unmount failure'
fi
for node in home root disk; do
 grep -qx "blockdev --setro /dev/fixture-$node" "$command_log" || fail "cleanup omitted $node"
done
printf '%s\n' 'PASS: failed unmount still reseals every opened node and reports failure'
