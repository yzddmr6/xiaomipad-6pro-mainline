#!/bin/sh
# SPDX-License-Identifier: MIT
#
# Host-side execution tests for tools/lib/install-switch-store.sh, the
# installer step that fills liuqin-switch's store on the new Ubuntu root.
#
# The script runs under BusyBox sh with only BusyBox applets on PATH -- the
# shell of the installer RAM image, which has no /tmp either -- against a
# synthetic root directory.  Its BusyBox is replaced by a wrapper whose `wget`
# copies from a local directory instead of the host's HTTP server; every
# other applet is the real BusyBox.  A store it writes is then handed to the
# real liuqin-switch, which must accept it.
set -eu

project_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
script_source=$project_root/tools/lib/install-switch-store.sh
switch=$project_root/device/gnome-overlay/usr/local/sbin/liuqin-switch

busybox=$(command -v busybox || true)
[ -n "$busybox" ] || { echo 'install-switch-store: BusyBox is required' >&2; exit 1; }

if [ "$(id -u)" != 0 ]; then
	# Only for liuqin-switch's own root check at the end.
	command -v unshare >/dev/null 2>&1 || { echo 'install-switch-store: unshare is required' >&2; exit 1; }
	exec unshare -r "$0" "$@"
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM

pass=0
fail=0
ok() { pass=$((pass + 1)); printf 'ok   %s\n' "$*"; }
no() { fail=$((fail + 1)); printf 'FAIL %s\n' "$*"; }

# BusyBox applets only, as in the RAM image.
bin=$work/bin
mkdir -p "$bin"
for applet in dirname mktemp sh cat chmod cp cut date dd head id ls mkdir mv od printf rm sed sha256sum stat sync tr; do
	ln -s "$busybox" "$bin/$applet"
done
srv=$work/srv
mkdir -p "$srv"
cat >"$work/bb" <<EOF
#!$busybox sh
# wget -O FILE URL: serve URL's basename from the fixture directory.
if [ "\$1" = wget ]; then
	[ "\$2" = -O ] || exit 1
	case \$4 in http://*) ;; *) exit 1 ;; esac
	exec $busybox cp "$srv/\${4##*/}" "\$3"
fi
exec $busybox "\$@"
EOF
chmod 0755 "$work/bb"
script=$work/install-switch-store.sh
sed "s|^BB=/bin/busybox\$|BB=$work/bb|" "$script_source" >"$script"
grep -q "^BB=$work/bb\$" "$script" || { echo 'install-switch-store: the BB rewrite matched nothing' >&2; exit 1; }
! sed 's/^[[:space:]]*#.*$//' "$script_source" | grep -nE '/tmp([^a-zA-Z0-9_]|$)' ||
	no 'the store step names /tmp, which the installer RAM image does not have'

run() {
	status=0
	out=$(env PATH="$bin" "$bin/sh" "$script" "$@" 2>&1) || status=$?
}

header() { # <file> <kind>: write a boot-image header page like the real one
	python3 - "$1" "$2" <<'PY'
import sys
path, kind = sys.argv[1], sys.argv[2]
page = bytearray(4096)
page[:8] = b'ANDROID!'
values = {8: 1000}
if kind == 'ubuntu':
    values.update({36: 4096, 40: 2, 1644: 1660, 1648: 5000})
else:
    values.update({8: 46000, 20: 1584, 40: 4})
for offset, value in values.items():
    page[offset:offset + 4] = value.to_bytes(4, 'little')
with open(path, 'wb') as stream:
    stream.write(bytes(page) + bytes(1024 * 1024 if kind == 'ubuntu' else 4 * 1024 * 1024 - 4096))
PY
}

header "$srv/boot.img" ubuntu
header "$srv/android-boot.img" android
u_sha=$(sha256sum "$srv/boot.img" | cut -d' ' -f1)
a_sha=$(sha256sum "$srv/android-boot.img" | cut -d' ' -f1)
u_size=$(stat -c %s "$srv/boot.img")
a_size=$(stat -c %s "$srv/android-boot.img")
u_url=http://192.168.7.1:8000/boot.img
a_url=http://192.168.7.1:8000/android-boot.img
good="ubuntu $u_url $u_sha $u_size android $a_url $a_sha $a_size"

target=$work/root
mkdir -p "$target/etc" "$target/var"

# --- --check validates and touches nothing ----------------------------------
# shellcheck disable=SC2086
run --check $good
if [ "$status" = 0 ] && [ ! -e "$target/var/lib" ]; then ok '--check accepts a valid set and writes nothing'; else no "--check: $status $out"; fi

refuse() { # <label> <fragment> ARGUMENTS...
	r_label=$1 r_fragment=$2
	shift 2
	run "$@"
	case $status:$out in
	0:*) no "$r_label: accepted" ;;
	*"$r_fragment"*) ok "$r_label: refused" ;;
	*) no "$r_label: refused for another reason: $out" ;;
	esac
}
refuse 'no arguments' usage
refuse 'too few arguments' usage --check ubuntu "$u_url" "$u_sha" "$u_size"
refuse 'sets swapped' 'expected the ubuntu image' --check android "$a_url" "$a_sha" "$a_size" ubuntu "$u_url" "$u_sha" "$u_size"
refuse 'short sha256' 'invalid ubuntu sha256 length' --check ubuntu "$u_url" abc "$u_size" android "$a_url" "$a_sha" "$a_size"
refuse 'non-http URL' 'not http' --check ubuntu "file:///boot.img" "$u_sha" "$u_size" android "$a_url" "$a_sha" "$a_size"
refuse 'oversize image' 'does not fit' --check ubuntu "$u_url" "$u_sha" 201326593 android "$a_url" "$a_sha" "$a_size"
refuse 'relative root' 'must be absolute' root ubuntu "$u_url" "$u_sha" "$u_size" android "$a_url" "$a_sha" "$a_size"
refuse 'not a root' 'not an installed root' "$work" ubuntu "$u_url" "$u_sha" "$u_size" android "$a_url" "$a_sha" "$a_size"
if [ ! -e "$target/var/lib" ]; then ok 'no refusal wrote to the root'; else no 'a refusal wrote to the root'; fi

# --- content checks happen after the download, before anything is final ----
refuse 'wrong ubuntu digest' 'checksum does not match' \
	"$target" ubuntu "$u_url" "$a_sha" "$u_size" android "$a_url" "$a_sha" "$a_size"
refuse 'wrong android size' 'size does not match' \
	"$target" ubuntu "$u_url" "$u_sha" "$u_size" android "$a_url" "$a_sha" "$u_size"
cp "$srv/boot.img" "$srv/ours.img"
refuse 'an Ubuntu image offered as Android' 'does not identify as a android boot image' \
	"$target" ubuntu "$u_url" "$u_sha" "$u_size" android http://h/ours.img "$u_sha" "$u_size"
if [ ! -e "$target/var/lib/liuqin/switch/android" ]; then
	ok 'a refused Android image never became store/android'
else
	no 'a refused Android image became store/android'
fi

# --- the real thing ---------------------------------------------------------
# shellcheck disable=SC2086
run "$target" $good
store=$target/var/lib/liuqin/switch
if [ "$status" = 0 ]; then ok 'a valid set is installed'; else no "install: $status $out"; fi
case $out in *SWITCH_STORE_READY*) ok 'the step reports SWITCH_STORE_READY' ;; *) no 'no SWITCH_STORE_READY' ;; esac
for set in ubuntu android; do
	if [ -f "$store/$set/boot.img" ] && [ -f "$store/$set/meta.json" ] &&
		(cd "$store/$set" && sha256sum -c SHA256SUMS >/dev/null 2>&1); then
		ok "store/$set holds boot.img, meta.json and a matching SHA256SUMS"
	else
		no "store/$set is incomplete"
	fi
	mode=$(stat -c %a "$store/$set/boot.img")
	if [ "$mode" = 644 ]; then ok "store/$set/boot.img is 0644"; else no "store/$set/boot.img mode $mode"; fi
	if [ ! -e "$store/.$set.new" ]; then ok "no staging directory left for $set"; else no "staging left for $set"; fi
done
if grep -q '"installed": "ubuntu"' "$store/state.json"; then
	ok 'state.json records ubuntu in boot_a'
else
	no 'state.json does not record ubuntu'
fi

# --- the switcher accepts what the installer wrote --------------------------
dev=$work/dev
mkdir -p "$dev"
mkdir -p "$dev/power_supply/battery"
printf 'Battery\n' > "$dev/power_supply/battery/type"
printf '80\n' > "$dev/power_supply/battery/capacity"
dd if=/dev/zero of="$dev/boot_a" bs=1048576 count=4 2>/dev/null
dd if="$store/ubuntu/boot.img" of="$dev/boot_a" conv=notrunc 2>/dev/null
echo 'androidboot.slot_suffix=_a' >"$dev/cmdline"
status=0
out=$(sh "$switch" verify --store "$store" --dev-dir "$dev" 2>&1) || status=$?
case $status:$out in
0:*'boot_a holds: the Ubuntu image from the store'*) ok 'liuqin-switch verify accepts the installed store' ;;
*) no "liuqin-switch verify: $status $out" ;;
esac
status=0
out=$(sh "$switch" to-android --no-reboot --store "$store" --dev-dir "$dev" 2>&1) || status=$?
if [ "$status" = 0 ] && cmp -s "$dev/boot_a" "$store/android/boot.img"; then
	ok 'liuqin-switch switches to the Android image the installer stored'
else
	no "liuqin-switch to-android: $status $out"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" = 0 ]
