#!/bin/sh
# SPDX-License-Identifier: MIT
#
# Assemble the native Ubuntu root filesystem:
#
#   pinned Ubuntu 26.04 desktop arm64 rootfs
#   + the five liuqin debs installed in an ARM64 chroot
#   + first-boot assembly (no ubuntu account, no autologin, marker, unit links)
#
# The output tree is generic: per-device data (cirrus calibration, BT address,
# WLAN MAC, sensor registry) is provisioned at install time and is asserted
# ABSENT here.  The tree is verified against the same topology gates stage 1
# will enforce (native profile in initramfs/init), so a root that would be
# rejected on the tablet fails here first.
#
# Stages are individually rerunnable so a failure near the end does not cost
# the full copy+chroot again:
#
#   sudo tools/build-liuqin-native-root.sh [all|copy|debs|assemble|manifest]
#
# copy      fresh cp -a of the pinned rootfs + trivial static edits
# debs      qemu chroot apt/dpkg install of the five debs
# assemble  BlueZ policy, unit links, marker, boundary asserts, pre-flight
# manifest  tree manifest + hash list + identity
# pack      archive the assembled tree with ownership, ACLs and xattrs
# all       the four in order (default)
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
out_dir=${OUT_DIR:-"$project_root/out/native-root"}
desktop_root=${UBUNTU_DESKTOP_ROOT:-"$project_root/tools/local/ubuntu-desktop-26.04-arm64/rootfs"}
desktop_manifest=${DESKTOP_ROOTFS_MANIFEST:-"$desktop_root.manifest"}
desktop_manifest_sha256=175ca2299263545973831606e397cce3fbc79298d97eb0bd7e216ac34944eed7
debs_dir=${DEBS_DIR:-"$project_root/out/liuqin-debs"}
# Shared with test-liuqin-debs.sh: the archive indexes are downloaded once per
# host, not once per runner, and the shipped tree keeps the pinned (empty)
# lists state instead of carrying stale host-fetched indexes.
apt_cache=${APT_CACHE_DIR:-"$project_root/tools/local/apt-cache-26.04-arm64"}
marker_sha256=4fdae4f7a27af8b0d4a2bbc168c7f01c3c5c6b5e245fcc521389d662f8212c5b

die() { printf 'build-liuqin-native-root: %s\n' "$*" >&2; exit 1; }
say() { printf 'build-liuqin-native-root: %s\n' "$*"; }

[ "$(id -u)" = 0 ] || die 'run as root (tree copy, chroot and ownership preservation need it)'
[ -x "$desktop_root/usr/lib/systemd/systemd" ] || die "not an Ubuntu root: $desktop_root"
[ -f "$desktop_manifest" ] || die "desktop tree manifest is unavailable: $desktop_manifest"
[ "$(sha256sum "$desktop_manifest" | cut -d' ' -f1)" = "$desktop_manifest_sha256" ] ||
	die 'desktop tree manifest identity mismatch'
case $out_dir in
"$project_root"/out/*) ;;
*) die "refusing an output directory outside out/: $out_dir" ;;
esac
command -v qemu-aarch64 >/dev/null || grep -q P /proc/sys/fs/binfmt_misc/qemu-aarch64 2>/dev/null ||
	die 'qemu-aarch64 binfmt with the P flag is required'
root=$out_dir/rootfs

stage_copy() {
	[ ! -e "$out_dir" ] || die "copy stage refuses an existing output: $out_dir (rm it or run a later stage)"
	mkdir -p "$out_dir"
	say "copying the pinned desktop rootfs to $root"
	cp -a "$desktop_root" "$root"
	# The casper media source has no meaning on the installed system and breaks
	# apt-get update with a file:/cdrom entry that has no Release file.
	rm -f "$root/etc/apt/sources.list.d/cdrom.sources"
	printf 'liuqin\n' >"$root/etc/hostname"
	chmod 0644 "$root/etc/hostname"
	# First-boot semantics: systemd generates the machine id; assert the pinned
	# tree's empty 0444 placeholder survived the copy.
	[ "$(stat -c '%a %u %g %s' "$root/etc/machine-id")" = '444 0 0 0' ] ||
		die 'machine-id placeholder did not survive the copy'
	# snap-confine carries a security.capability xattr; a copy that silently
	# drops it breaks snap confinement on the installed system.  getcap prints
	# the path before the caps, so compare field 2 onward -- the two trees'
	# paths differ.
	src_caps=$(getcap "$desktop_root/usr/lib/snapd/snap-confine" 2>/dev/null | awk '{print $2}')
	dst_caps=$(getcap "$root/usr/lib/snapd/snap-confine" 2>/dev/null | awk '{print $2}')
	[ -n "$src_caps" ] || die 'pinned desktop rootfs lost the snap-confine capability'
	[ "$src_caps" = "$dst_caps" ] || die 'snap-confine capability xattr did not survive the copy'
	say 'copy PASS'
}

stage_debs() {
	if [ "${LIUQIN_ROOT_MOUNT_NS:-}" != 1 ]; then
		LIUQIN_ROOT_MOUNT_NS=1 unshare --mount --propagation private sh "$0" debs
		return
	fi
	[ -x "$root/usr/lib/systemd/systemd" ] || die 'run the copy stage first'
	for deb in firmware:all device-support:arm64 sensors:arm64 kernel:arm64; do
		name=liuqin-${deb%:*}; arch=${deb#*:}
		ls "$debs_dir"/${name}_*_${arch}.deb >/dev/null 2>&1 || die "missing deb: $name ($arch)"
	done
	ls "$debs_dir"/liuqin-device_*_arm64.deb >/dev/null 2>&1 || die 'missing deb: liuqin-device'
	say 'installing the liuqin deb set in a qemu-aarch64 chroot'
	mkdir -p "$root/tmp/liuqin-debs"
	cp "$debs_dir"/*.deb "$root/tmp/liuqin-debs/"
	cp -L /etc/resolv.conf "$root/etc/resolv.conf.test"
	cat >"$root/root/native-assemble.sh" <<'EOF'
#!/bin/sh
set -eux
cp -L /etc/resolv.conf.test /etc/resolv.conf
# APT hooks are lists; scalar command-line overrides do not clear them.
cat >/tmp/liuqin-apt.conf <<'APT'
#clear APT::Update::Post-Invoke-Success;
#clear APT::Update::Post-Invoke;
#clear DPkg::Post-Invoke;
APT
apt-get -c /tmp/liuqin-apt.conf update >/dev/null
# The camera application and its codec plugins are image content, not
# dependencies of liuqin-device-support: removing Snapshot or a codec must
# never take the device units with it.  ugly/libav carry the H.264 encoder
# and decoder that Snapshot recording and in-app playback use; libcamera-tools
# (cam) and v4l-utils (media-ctl, v4l2-ctl) are the checks docs/CAMERA.md
# tells users to run.
apt-get -c /tmp/liuqin-apt.conf install -y --no-install-recommends libqrtr1 libprotobuf-c1 \
	gnome-snapshot libcamera-ipa gstreamer1.0-libcamera gstreamer1.0-libav gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly \
	libcamera-tools v4l-utils >/dev/null
dpkg -i /tmp/liuqin-debs/liuqin-firmware_*_all.deb \
	/tmp/liuqin-debs/liuqin-device-support_*_arm64.deb \
	/tmp/liuqin-debs/liuqin-sensors_*_arm64.deb \
	/tmp/liuqin-debs/liuqin-kernel_*_arm64.deb \
	$(find /tmp/liuqin-debs -maxdepth 1 -name 'liuqin-fingerprint_*_arm64.deb' -print) \
	/tmp/liuqin-debs/liuqin-device_*_arm64.deb
dpkg --audit
for pkg in liuqin-firmware liuqin-device-support liuqin-sensors liuqin-kernel liuqin-device; do
	dpkg-query -W -f='${Status}\n' "$pkg" | grep -qx 'install ok installed'
done
if ls /tmp/liuqin-debs/liuqin-fingerprint_*_arm64.deb >/dev/null 2>&1; then
	dpkg-query -W -f='${Status}\n' liuqin-fingerprint | grep -qx 'install ok installed'
fi
EOF
	chmod 0755 "$root/root/native-assemble.sh"
	mkdir -p "$apt_cache/lists" "$apt_cache/archives" \
		"$root/var/lib/apt/lists" "$root/var/cache/apt/archives"
	mount --bind "$apt_cache/lists" "$root/var/lib/apt/lists"
	mount --bind "$apt_cache/archives" "$root/var/cache/apt/archives"
	mount -t proc -o ro proc "$root/proc"
	[ ! -e "$root/usr/sbin/policy-rc.d" ] || die 'unexpected existing service-start policy'
	printf '#!/bin/sh\nexit 101\n' >"$root/usr/sbin/policy-rc.d"
	chmod 0755 "$root/usr/sbin/policy-rc.d"
	trap 'rm -f "$root/usr/sbin/policy-rc.d"; umount "$root/proc" "$root/var/cache/apt/archives" "$root/var/lib/apt/lists" 2>/dev/null || :' EXIT
	chroot "$root" /bin/sh /root/native-assemble.sh
	umount "$root/proc" "$root/var/cache/apt/archives" "$root/var/lib/apt/lists"
	trap - EXIT
	rm -f "$root/root/native-assemble.sh" "$root/etc/resolv.conf.test" \
		"$root/tmp/liuqin-apt.conf" "$root/usr/sbin/policy-rc.d"
	rm -rf "$root/tmp/liuqin-debs"
	# cp -L inside the chroot wrote through the distro resolver symlink into the
	# run/ stub; restore the pinned empty placeholder and assert the symlink
	# itself was never replaced.
	: >"$root/run/systemd/resolve/stub-resolv.conf"
	[ -L "$root/etc/resolv.conf" ] &&
		[ "$(readlink "$root/etc/resolv.conf")" = ../run/systemd/resolve/stub-resolv.conf ] ||
		die 'etc/resolv.conf is not the distro resolver symlink after the chroot'
	say 'debs PASS'
}

stage_assemble() {
	[ -x "$root/usr/lib/systemd/systemd" ] || die 'run the copy stage first'
	[ -f "$root/usr/share/liuqin/kernel.release" ] || die 'run the debs stage first'
	# --- BlueZ AutoEnable (intentional local configuration of the distro conf) --
	bluez_conf=$root/etc/bluetooth/main.conf
	[ -f "$bluez_conf" ] && [ ! -L "$bluez_conf" ] || die 'BlueZ main.conf is missing or unsafe'
	bluez_tmp=$bluez_conf.liuqin.$$
	awk '
BEGIN { in_policy=0; saw_policy=0; emitted=0 }
/^\[Policy\][[:space:]]*$/ {
	if (in_policy && !emitted) print "AutoEnable=true"
	in_policy=1; saw_policy=1; emitted=0; print; next
}
/^\[[^]]+\][[:space:]]*$/ {
	if (in_policy && !emitted) print "AutoEnable=true"
	in_policy=0; print; next
}
in_policy && /^[#;]?[[:space:]]*AutoEnable[[:space:]]*=/ {
	if (!emitted) print "AutoEnable=true"
	emitted=1; next
}
{ print }
END {
	if (in_policy && !emitted) print "AutoEnable=true"
	if (!saw_policy) exit 42
}
' "$bluez_conf" >"$bluez_tmp" || { rm -f "$bluez_tmp"; die 'BlueZ main.conf has no unambiguous Policy section'; }
	chown 0:0 "$bluez_tmp"
	chmod 0644 "$bluez_tmp"
	mv "$bluez_tmp" "$bluez_conf"
	grep -qx 'AutoEnable=true' "$bluez_conf" || die 'BlueZ AutoEnable edit did not land'

	# --- unit enablement (native set; snap admission deliberately not required) --
	# basic.target.requires carries the storage guard only: the snap admission
	# text check must not gate basic.target (exit-list item).  snap-admission's
	# unit still ships with liuqin-device-support for on-demand --verify.
	link_unit() { # link_unit <wants/requires dir> <unit>
		mkdir -p "$root/etc/systemd/system/$1"
		ln -sfn "../$2" "$root/etc/systemd/system/$1/$2"
		[ "$(readlink "$root/etc/systemd/system/$1/$2")" = "../$2" ] || die "unit link failed: $1/$2"
	}
	link_unit basic.target.requires liuqin-gnome-storage-guard.service
	link_unit multi-user.target.wants liuqin-gnome-usb-rescue.service
	link_unit multi-user.target.wants liuqin-slpi.service
	link_unit multi-user.target.wants liuqin-power-keyd.service
	link_unit graphical.target.wants liuqin-backlight-default.service
	# liuqin-hide-gunyah-node.service ships in the deb but stays unwired:
	# the detect-virt containment is deferred to a later iteration (S2-16,
	# user decision 2026-09-12).  Wire it with
	#   link_unit sysinit.target.wants liuqin-hide-gunyah-node.service
	# when that iteration lands.
	# The distro default.target already resolves to graphical.target through
	# /usr/lib; state it in /etc so a distro default change can never silently
	# drop the installed system to multi-user.
	ln -sfn /usr/lib/systemd/system/graphical.target "$root/etc/systemd/system/default.target"
	[ ! -e "$root/etc/systemd/system/basic.target.requires/liuqin-snap-root-admission.service" ] ||
		die 'snap admission must not be required by basic.target in the native root'

	# --- marker ---------------------------------------------------------------
	printf 'liuqin-native-root-v1\n' >"$root/etc/liuqin-native-root"
	chown 0:0 "$root/etc/liuqin-native-root"
	chmod 0644 "$root/etc/liuqin-native-root"
	[ "$(sha256sum "$root/etc/liuqin-native-root" | cut -d' ' -f1)" = "$marker_sha256" ] ||
		die 'native root marker content mismatch'

	# --- per-device and first-boot boundaries ----------------------------------
	[ ! -e "$root/var/lib/liuqin-private" ] ||
		die 'per-device private data must not be in the generic tree'
	for private in var/lib/fprint var/lib/liuqin-fingerprint; do
		if [ -d "$root/$private" ] && find "$root/$private" -type f | grep -q .; then
			die "fingerprint private state must not be in the generic tree: $private"
		fi
	done
	if find "$root/usr/lib/firmware/cirrus" -name '*-calr.bin' | grep -q .; then
		die 'per-device cirrus calibration must not be in the generic tree'
	fi
	# A human account is uid 1000..59999; nobody (65534) is not one.
	if awk -F: '$3 >= 1000 && $3 < 60000 { found=1 } END { exit !found }' "$root/etc/passwd"; then
		die 'the generic tree carries a human account'
	fi
	! grep -Eq '^[[:space:]]*AutomaticLogin' "$root/etc/gdm3/custom.conf" ||
		die 'gdm autologin survived; first boot must run gnome-initial-setup'
	! grep -Eq '^[[:space:]]*InitialSetupEnable[[:space:]]*=[[:space:]]*false' "$root/etc/gdm3/custom.conf" ||
		die 'gdm initial setup is actively disabled'
	[ -x "$root/usr/libexec/gnome-initial-setup" ] ||
		die 'gnome-initial-setup is not installed in the tree'

	# --- stage-1 topology pre-flight (mirror of the native profile in init) ----
	say 'running the stage-1 topology pre-flight against the tree'
	preflight_fail() { die "stage-1 pre-flight: $1"; }
	for exe in \
		/usr/lib/systemd/systemd /usr/sbin/gdm3 /usr/bin/gnome-shell \
		/usr/bin/hexagonrpcd \
		/usr/local/sbin/liuqin-slpi \
		/usr/local/bin/busybox /usr/local/bin/liuqin-shell \
		/usr/libexec/iio-sensor-proxy \
		/usr/local/sbin/liuqin-gnome-storage-guard \
		/usr/local/sbin/liuqin-gnome-usb-rescue \
		/usr/local/libexec/liuqin-power-keyd \
		/usr/local/libexec/liuqin-power-key-action \
		/usr/local/sbin/liuqin-bt-public-addr \
		/usr/local/sbin/liuqin-wlan-mac \
		/usr/libexec/liuqin-ssc-sample-gate; do
		[ "$(stat -c '%a' "$root$exe" 2>/dev/null || true)" = 755 ] ||
			preflight_fail "not executable: $exe"
	done
	for regular in \
		/etc/liuqin-native-root \
		/etc/dconf/db/local.d/locks/00-liuqin-power \
		/etc/systemd/system/liuqin-gnome-storage-guard.service \
		/etc/systemd/system/liuqin-gnome-usb-rescue.service \
		/etc/systemd/system/liuqin-power-keyd.service \
		/etc/systemd/system/bluetooth.service.d/20-liuqin-public-address.conf \
		/etc/systemd/system/liuqin-bt-preconfigure.service \
		/etc/systemd/system/liuqin-hexagonrpcd-sdsp.service \
		/etc/systemd/system/liuqin-slpi.service \
		/etc/systemd/system/liuqin-ssc-sample-gate.service \
		/etc/systemd/system/liuqin-sensor-stack.target \
		/etc/systemd/system/liuqin-wlan-mac.service \
		/etc/systemd/system/NetworkManager.service.d/20-liuqin-wlan-mac.conf \
		/etc/udev/rules.d/80-liuqin-fastrpc.rules \
		/usr/lib/firmware/novatek/liuqin/novatek_nt36532_m81_fw_csot.bin \
		/usr/lib/firmware/novatek/liuqin/novatek_nt36532_m81_fw_tm.bin \
		/usr/lib/firmware/qcom/sm8450/Xiaomi-Pad-6-Pro-tplg.bin \
		/usr/lib/firmware/updates/qcom/a730_sqe.fw \
		/usr/lib/firmware/updates/qcom/gmu_gen70000.bin \
		/usr/share/qcom/sm8450/Xiaomi/liuqin/sensors/sns_reg_version; do
		[ "$(stat -c '%a' "$root$regular" 2>/dev/null || true)" = 644 ] ||
			preflight_fail "regular file mode is not 644: $regular"
	done
	[ -L "$root/usr/sbin/init" ] && [ "$(readlink "$root/usr/sbin/init")" = ../lib/systemd/systemd ] ||
		preflight_fail '/usr/sbin/init is not the systemd symlink'
	[ -L "$root/etc/systemd/system/default.target" ] &&
		[ "$(readlink "$root/etc/systemd/system/default.target")" = /usr/lib/systemd/system/graphical.target ] ||
		preflight_fail 'default.target is not graphical.target'
	[ -L "$root/etc/systemd/system/display-manager.service" ] &&
		[ "$(readlink "$root/etc/systemd/system/display-manager.service")" = /lib/systemd/system/gdm3.service ] ||
		preflight_fail 'display-manager.service is not gdm3'
	grep -qx 'Requires=liuqin-bt-preconfigure.service' \
		"$root/etc/systemd/system/bluetooth.service.d/20-liuqin-public-address.conf" ||
		preflight_fail 'Bluetooth preconfiguration is not required by BlueZ'
	grep -qx 'Before=bluetooth.service' "$root/etc/systemd/system/liuqin-bt-preconfigure.service" ||
		preflight_fail 'bt-preconfigure lacks Before=bluetooth.service'
	chroot "$root" /usr/lib/systemd/systemd --version >/dev/null 2>&1 ||
		preflight_fail 'systemd will not execute under chroot'
	say 'assemble PASS'
}

stage_manifest() {
	[ -f "$root/etc/liuqin-native-root" ] || die 'run the assemble stage first'
	say 'writing the tree manifest and hash list'
	manifest=$out_dir/native-root.manifest
	hashes=$out_dir/native-root.hashes
	# Batch the hashing (one sha256sum per file would spawn ~240k processes).
	# sha256sum -z: NUL-terminated output with no filename escaping -- the tree
	# carries systemd-escaped unit names containing literal backslashes, which
	# the escaped default format would mangle.
	( cd "$root" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum -z ) >"$out_dir/.filehashes" ||
		{ rm -f "$out_dir/.filehashes"; die 'batch hashing failed'; }
	( cd "$root" && find . -printf '%y %m %u:%g %p\n' | LC_ALL=C sort -k4 ) >"$out_dir/.treemeta" ||
		{ rm -f "$out_dir/.filehashes" "$out_dir/.treemeta"; die 'find over the tree failed'; }
	python3 - "$out_dir/.treemeta" "$out_dir/.filehashes" "$manifest" "$hashes" <<'PYEOF' ||
import sys
by_path = {}
for chunk in open(sys.argv[2], "rb").read().split(b"\0"):
	if not chunk:
		continue
	digest, _, path = chunk.partition(b"  ")
	by_path[path.decode()] = digest.decode()
out_manifest = []
out_hashes = []
count = 0
for line in open(sys.argv[1], encoding="utf-8").read().splitlines():
	typ, mode, owner, path = line.split(" ", 3)
	digest = by_path.get(path, "-") if typ == "f" else "-"
	if typ == "f" and digest == "-":
		raise SystemExit(f"missing hash for {path}")
	out_manifest.append(f"{digest}  {typ} {mode} {owner} {path}")
	if typ == "f":
		out_hashes.append(f"{digest}  /{path[2:]}")
	count += 1
open(sys.argv[3], "w").write("\n".join(out_manifest) + "\n")
open(sys.argv[4], "w").write("\n".join(out_hashes) + "\n")
print(count)
PYEOF
	{ rm -f "$manifest" "$hashes" "$out_dir/.filehashes" "$out_dir/.treemeta"; die 'manifest generation failed'; }
	entries=$(wc -l <"$manifest" | tr -d ' ')
	rm -f "$out_dir/.filehashes" "$out_dir/.treemeta"
	[ "$entries" -ge 100000 ] || { rm -f "$manifest" "$hashes"; die "manifest is implausibly small: $entries"; }
	{
		printf 'native_root_version=v1\n'
		printf 'desktop_rootfs_manifest_sha256=%s\n' "$desktop_manifest_sha256"
		sha256sum "$debs_dir"/*.deb | sed "s|$debs_dir/||" | LC_ALL=C sort
		printf 'entries=%s\n' "$entries"
		printf 'manifest_sha256=%s\n' "$(sha256sum "$manifest" | cut -d' ' -f1)"
		printf 'hashes_sha256=%s\n' "$(sha256sum "$hashes" | cut -d' ' -f1)"
	} >"$out_dir/native-root.identity"
	chmod 0644 "$manifest" "$hashes" "$out_dir/native-root.identity"
	say "manifest PASS: $root ($entries entries)"
	cat "$out_dir/native-root.identity"
}

stage_pack() {
	[ -f "$out_dir/native-root.identity" ] || die 'run the manifest stage first'
	[ -n "$(getcap "$root/usr/lib/snapd/snap-confine" 2>/dev/null)" ] ||
		die 'root tree has lost the snap-confine capability'
	sh "$project_root/tools/lib/rootfs-archive.sh" pack "$root" "$out_dir/rootfs.tar.gz"
	(cd "$out_dir" && sha256sum rootfs.tar.gz >rootfs.tar.gz.sha256)
	say 'archive prepared; this is not an installation or release verdict'
}

case ${1:-all} in
copy) stage_copy ;;
debs) stage_debs ;;
assemble) stage_assemble ;;
manifest) stage_manifest ;;
pack) stage_pack ;;
all) stage_copy; stage_debs; stage_assemble; stage_manifest ;;
*) die 'usage: build-liuqin-native-root.sh [all|copy|debs|assemble|manifest|pack]' ;;
esac
