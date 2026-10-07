#!/bin/sh
# SPDX-License-Identifier: MIT
#
# Build the liuqin device .deb set from explicit firmware, userspace and
# kernel inputs. Each package owns its installed content.
#
#   firmware        /usr/lib/firmware closure from build-liuqin-firmware-prep.sh
#   device-support  gnome-overlay + compiled helpers + rescue shell + power panel
#   sensors         SSC userspace from build-liuqin-sensors-stack.sh
#   kernel          modules tree + /boot payload (input-gated; boot write is C)
#   meta            liuqin-device metapackage depending on all of the above
#   all             everything whose inputs are present (kernel may be gated)
#
# Distro-file ownership conflicts use dpkg-divert in preinst/postrm, never a
# bare overwrite.  The set was measured against the pinned 26.04 desktop root:
# 4 firmware files (linux-firmware-qualcomm-wireless, different bytes) plus the
# two reviewed component replacements (gnome-control-center power panel, SSC
# iio-sensor-proxy).  The native install keeps the distro gdm3 custom.conf
# untouched: with no autologin override and no human user, GDM runs
# gnome-initial-setup on first boot, so the overlay's autologin
# variant is deliberately not packaged.
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
out_dir=${OUT_DIR:-"$project_root/out/liuqin-debs"}
version=${LIUQIN_DEB_VERSION:-0.1}
maintainer='yzddmr6 <46088090+yzddmr6@users.noreply.github.com>'

firmware_tree=${FIRMWARE_TREE:-"$project_root/device/firmware"}
firmware_manifest_sha256=${FIRMWARE_MANIFEST_SHA256:-012c413e0a5d5c3c14631fbdfe50da5c51e95ffc1ae431b8d93f776538b65ba2}
overlay=${GNOME_OVERLAY:-"$project_root/device/gnome-overlay"}
ubuntu_desktop_root=${UBUNTU_DESKTOP_ROOT:-"$project_root/tools/local/ubuntu-desktop-26.04-arm64/rootfs"}
audio_probe_libasound=$ubuntu_desktop_root/usr/lib/aarch64-linux-gnu/libasound.so.2.0.0
busybox=${BUSYBOX:-"$project_root/tools/local/busybox-arm64/usr/bin/busybox"}
busybox_sha256=${BUSYBOX_SHA256:-52151e7f322f926b64049cdaa1410dc3ea6485525e0624b05813791c219ae933}
power_key_cc=${POWER_KEY_CC:-$(command -v aarch64-linux-gnu-gcc || true)}
power_keyd_source=${POWER_KEYD_SOURCE:-"$project_root/device/power-key/liuqin-power-keyd.c"}
uinput_automation_source=${UINPUT_AUTOMATION_SOURCE:-"$project_root/device/input/liuqin-uinput-automation.c"}
audio_probe_source=${AUDIO_PROBE_SOURCE:-"$project_root/device/audio-topology/liuqin-audio-hwparams-probe.c"}
power_settings_binary=${POWER_SETTINGS_BINARY:-"$project_root/out/gnome-control-center/gnome-control-center"}
power_settings_sha256=${POWER_SETTINGS_SHA256:-}
power_settings_manifest=${POWER_SETTINGS_MANIFEST:-"$project_root/out/gnome-control-center/build-info.json"}
# SENSOR_STACK_SHA256 selects the sensor build to include in this package set.
sensor_stack=${SENSOR_STACK_TAR:-"$project_root/out/liuqin-sensors-stack/artifacts/sensor-stack.tar"}
sensor_stack_sha256=${SENSOR_STACK_SHA256:-9dcb2b8cb3a6539ccd6d2b410476f3e6ebc725072a3b63089a570ebdaeec5701}
webkit_env=etc/environment.d/50-liuqin-dmabuf.conf
webkit_env_sha256=1db6b589d305556a976c72b20500d9e932650eb090ba845052a0b8f659bdcca5

kernel_modules_dir=${KERNEL_MODULES_DIR:-}
kernel_image=${KERNEL_IMAGE:-}
kernel_dtb=${KERNEL_DTB:-}
kernel_initramfs=${KERNEL_INITRAMFS:-}
fingerprint_bundle=${FINGERPRINT_BUNDLE:-}

die() { printf 'build-liuqin-debs: %s\n' "$*" >&2; exit 1; }

command -v dpkg-deb >/dev/null || die 'dpkg-deb is required'
case $out_dir in
"$project_root"/out/* | /tmp/*) ;;
*) die "refusing to wipe an output directory outside out/ or /tmp: $out_dir" ;;
esac

mkdir -p "$out_dir"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM

sha_ok() { [ "$(sha256sum "$1" | cut -d' ' -f1)" = "$2" ] || die "$3"; }

# divert_add <pkg> <path>... : preinst body fragment file
write_divert_preinst() {
	pkg=$1; shift
	{
		printf '#!/bin/sh\nset -e\n'
		printf 'case $1 in install|upgrade)\n'
		for p in "$@"; do
			printf '\tdpkg-divert --package %s --add --rename --divert %s.liuqin-orig %s\n' \
				"$pkg" "$p" "$p"
		done
		printf '\t;;\nesac\n'
	} >"$work/$pkg.preinst"
}

write_divert_postrm() {
	pkg=$1; shift
	{
		printf '#!/bin/sh\nset -e\n'
		printf 'if [ "$1" = remove ]; then\n'
		for p in "$@"; do
			printf '\tdpkg-divert --package %s --rename --remove %s\n' "$pkg" "$p"
		done
		printf 'fi\n'
	} >"$work/$pkg.postrm"
}

pack() { # pack <pkg> <arch> <depends> <description> [extra DEBIAN files...]
	pkg=$1; arch=$2; depends=$3; desc=$4
	root=$work/$pkg/root
	[ -d "$root/DEBIAN" ] || mkdir -p "$root/DEBIAN"
	{
		printf 'Package: %s\nVersion: %s\nArchitecture: %s\n' "$pkg" "$version" "$arch"
		printf 'Maintainer: %s\n' "$maintainer"
		[ -z "$depends" ] || printf 'Depends: %s\n' "$depends"
		printf 'Section: misc\nPriority: optional\n'
		printf 'Description: %s\n' "$desc"
	} >"$root/DEBIAN/control"
	if [ -d "$root/etc" ]; then
		(cd "$root" && find etc -type f -printf '/etc/%P\n' | LC_ALL=C sort) >"$root/DEBIAN/conffiles"
		# A diverted path ships as a plain file: declaring it a conffile would
		# make dpkg prompt about the diverted-away distro conffile on install.
		if [ -f "$work/$pkg.noconffiles" ]; then
			grep -vxF -f "$work/$pkg.noconffiles" "$root/DEBIAN/conffiles" >"$work/conffiles.filtered" ||
				[ ! -s "$work/conffiles.filtered" ]
			mv "$work/conffiles.filtered" "$root/DEBIAN/conffiles"
		fi
		[ -s "$root/DEBIAN/conffiles" ] || rm -f "$root/DEBIAN/conffiles"
	fi
	for script in preinst postinst prerm postrm; do
		[ ! -f "$root/DEBIAN/$script" ] || chmod 0755 "$root/DEBIAN/$script"
	done
	[ ! -f "$root/DEBIAN/conffiles" ] || chmod 0644 "$root/DEBIAN/conffiles"
	dpkg-deb --root-owner-group --build "$root" "$out_dir/${pkg}_${version}_${arch}.deb" >/dev/null
	printf 'built: %s\n' "$out_dir/${pkg}_${version}_${arch}.deb"
}

build_firmware() {
	pkg=liuqin-firmware
	[ -d "$firmware_tree/usr/lib/firmware" ] ||
		die "firmware-prep tree is unavailable: $firmware_tree (run tools/build-liuqin-firmware-prep.sh)"
	[ -f "$firmware_tree/firmware.manifest" ] && [ ! -L "$firmware_tree/firmware.manifest" ] ||
		die 'firmware-prep manifest is unavailable'
	sha_ok "$firmware_tree/firmware.manifest" "$firmware_manifest_sha256" \
		'firmware-prep manifest identity mismatch'
	root=$work/$pkg/root
	mkdir -p "$root"
	cp -a "$firmware_tree/usr" "$root/"
	# Re-hash every copied file against the pinned manifest; a wrong or
	# partial tree fails here, not on the panel.
	sed 's|  /|  |' "$firmware_tree/firmware.manifest" |
		(cd "$root" && sha256sum -c --quiet - >/dev/null) ||
		die 'firmware content does not match the pinned firmware-prep manifest'
	count=$(find "$root/usr/lib/firmware" -type f | wc -l | tr -d ' ')
	[ "$count" = 197 ] || die "firmware closure is not the pinned 197 files: $count"
	# The main-tree ath11k blobs (hw2.0 files and the hw2.1 symlinks pointing
	# at them) collide with linux-firmware-qualcomm-wireless at different
	# bytes; divert the distro copies so the pinned fallbacks survive a
	# distro firmware upgrade.
	divert_paths="
/usr/lib/firmware/ath11k/WCN6855/hw2.0/amss.bin.zst
/usr/lib/firmware/ath11k/WCN6855/hw2.0/board-2.bin.zst
/usr/lib/firmware/ath11k/WCN6855/hw2.0/m3.bin.zst
/usr/lib/firmware/ath11k/WCN6855/hw2.0/regdb.bin.zst
/usr/lib/firmware/ath11k/WCN6855/hw2.1/amss.bin.zst
/usr/lib/firmware/ath11k/WCN6855/hw2.1/board-2.bin.zst
/usr/lib/firmware/ath11k/WCN6855/hw2.1/m3.bin.zst
/usr/lib/firmware/ath11k/WCN6855/hw2.1/regdb.bin.zst"
	mkdir -p "$root/DEBIAN"
	# shellcheck disable=SC2086
	write_divert_preinst "$pkg" $divert_paths
	# shellcheck disable=SC2086
	write_divert_postrm "$pkg" $divert_paths
	cp "$work/$pkg.preinst" "$root/DEBIAN/preinst"
	cp "$work/$pkg.postrm" "$root/DEBIAN/postrm"
	pack "$pkg" all '' 'Xiaomi Pad 6 Pro (liuqin) firmware closure, pinned vendor set'
}

build_device_support() {
	pkg=liuqin-device-support
	[ -d "$overlay" ] || die "GNOME overlay is unavailable: $overlay"
	[ -x "$busybox" ] || die "pinned BusyBox is unavailable: $busybox"
	sha_ok "$busybox" "$busybox_sha256" 'pinned BusyBox hash mismatch'
	for src in "$power_keyd_source" "$uinput_automation_source" "$audio_probe_source"; do
		[ -f "$src" ] && [ ! -L "$src" ] || die "reviewed source is unavailable: $src"
	done
	[ -x "$power_key_cc" ] || die "aarch64 compiler is unavailable: $power_key_cc"
	[ -f "$audio_probe_libasound" ] && [ ! -L "$audio_probe_libasound" ] ||
		die "Canonical arm64 libasound is unavailable: $audio_probe_libasound"
	[ -r "$overlay/$webkit_env" ] ||
		die 'the overlay carries no reviewed WebKit fallback environment file'
	sha_ok "$overlay/$webkit_env" "$webkit_env_sha256" \
		'the overlay WebKit fallback is not the reviewed legacy-compositor revision'
	if [ -f "$overlay/usr/share/liuqin/power/io.github.liuqin.power.gschema.xml" ]; then
		[ -f "$power_settings_binary" ] && [ ! -L "$power_settings_binary" ] ||
			die 'build the reviewed native power settings component or set POWER_SETTINGS_BINARY'
		if [ -z "$power_settings_sha256" ]; then
			power_settings_sha256=$(python3 - "$power_settings_manifest" \
				"$project_root/device/gnome-control-center/source.json" <<'PY'
import json, sys
from pathlib import Path
info = json.loads(Path(sys.argv[1]).read_text())
expected = json.loads(Path(sys.argv[2]).read_text())
if info['source'] != expected:
    raise SystemExit('Settings build uses different source inputs')
print(info['binary_sha256'])
PY
			) || die 'build GNOME Settings with tools/build-liuqin-settings.py first'
		fi
		sha_ok "$power_settings_binary" "$power_settings_sha256" \
			'native power settings binary identity mismatch'
	fi
	root=$work/$pkg/root
	mkdir -p "$root"
	# Overlay content, excluding documentation, Python bytecode caches, the
	# firmware path owned by liuqin-firmware, and the autologin gdm3 override
	# (native install keeps the distro custom.conf so first boot runs
	# gnome-initial-setup; see the header comment).
	(cd "$overlay" && find . -mindepth 1 -type f \
		! -name README.md ! -path '*/__pycache__/*' ! -path './usr/lib/firmware/*' \
		! -path './etc/gdm3/custom.conf' \
		-printf '%P\n' | LC_ALL=C sort) >"$work/$pkg.files"
	while IFS= read -r rel; do
		[ -n "$rel" ] || continue
		mode=$(stat -c '%a' "$overlay/$rel")
		case $mode in 6??) mode=644 ;; 7??) mode=755 ;; esac
		install -D -m "$mode" "$overlay/$rel" "$root/$rel"
	done <"$work/$pkg.files"
	[ "$(wc -l <"$work/$pkg.files" | tr -d ' ')" -ge 30 ] ||
		die 'device-support overlay copy is implausibly small'
	# Adapt the shared storage guard to the native root marker.
	guard=$root/usr/local/sbin/liuqin-gnome-storage-guard
	grep -qx 'marker=${LIUQIN_GNOME_GUARD_TEST_MARKER:-/etc/liuqin-gnome-root}' "$guard" ||
		die 'storage guard drifted from the reviewed legacy marker'
	grep -qx 'marker_sha=bd86a359f5b6bf05f09abf544967489e924c251ab7c07684df62b0dbda4c3fca' "$guard" ||
		die 'storage guard drifted from the reviewed legacy marker hash'
	sed -i \
		-e 's|^marker=${LIUQIN_GNOME_GUARD_TEST_MARKER:-/etc/liuqin-gnome-root}$|marker=/etc/liuqin-native-root|' \
		-e 's|^marker_sha=bd86a359f5b6bf05f09abf544967489e924c251ab7c07684df62b0dbda4c3fca$|marker_sha=4fdae4f7a27af8b0d4a2bbc168c7f01c3c5c6b5e245fcc521389d662f8212c5b|' \
		"$guard"
	grep -qx 'marker=/etc/liuqin-native-root' "$guard" ||
		die 'storage guard native marker rewrite did not land'
	# Compiled helpers, same deterministic flags as the device-layer builder.
	mkdir -p "$root/usr/local/libexec"
	LC_ALL=C SOURCE_DATE_EPOCH=0 "$power_key_cc" \
		-std=c11 -O2 -pipe \
		-Wall -Wextra -Werror -Wformat=2 -Wshadow -Wstrict-prototypes \
		-Wmissing-prototypes -fno-common -fstack-protector-strong \
		-D_FORTIFY_SOURCE=3 \
		-ffile-prefix-map="$project_root"=. \
		-ffile-prefix-map="$root"=/build/liuqin-device-support \
		-Wl,-z,relro,-z,now -Wl,--build-id=sha1 \
		"$power_keyd_source" -o "$root/usr/local/libexec/liuqin-power-keyd"
	chmod 0755 "$root/usr/local/libexec/liuqin-power-keyd"
	LC_ALL=C SOURCE_DATE_EPOCH=0 "$power_key_cc" \
		-std=c11 -O2 -pipe -static -Wall -Wextra -Werror -Wformat=2 \
		-fstack-protector-strong -D_FORTIFY_SOURCE=3 \
		-ffile-prefix-map="$project_root"=. -ffile-prefix-map="$root"=/build/liuqin-device-support \
		-Wl,-z,relro,-z,now -Wl,--build-id=sha1 \
		"$uinput_automation_source" -o "$root/usr/local/libexec/liuqin-uinput-automation"
	chmod 0755 "$root/usr/local/libexec/liuqin-uinput-automation"
	LC_ALL=C SOURCE_DATE_EPOCH=0 "$power_key_cc" \
		-std=c11 -O2 -pipe -Wall -Wextra -Werror -fstack-protector-strong \
		-D_FORTIFY_SOURCE=3 -ffile-prefix-map="$project_root"=. \
		-Wl,-z,relro,-z,now -Wl,--build-id=sha1 \
		"$audio_probe_source" "$audio_probe_libasound" \
		-o "$root/usr/local/libexec/liuqin-audio-hwparams-probe"
	chmod 0755 "$root/usr/local/libexec/liuqin-audio-hwparams-probe"
	# The dual-boot switcher runs as root through pkexec.  The desktop entry,
	# the polkit action and the script have to name the same program and
	# argument, or the entry silently asks for the generic pkexec action.
	# The A/B slot helper is deliberately not shipped here any more: only the
	# initramfs marks the running slot successful, and nothing in the running
	# system may change which slot boots.
	switcher=$root/usr/local/sbin/liuqin-switch
	if [ ! -f "$switcher" ] || [ ! -x "$switcher" ]; then
		die 'the overlay carries no liuqin-switch'
	fi
	sh -n "$switcher" || die 'liuqin-switch is not valid sh'
	grep -qx 'Exec=pkexec /usr/local/sbin/liuqin-switch to-android' \
		"$root/usr/local/share/applications/liuqin-reboot-to-android.desktop" ||
		die 'the Reboot to Android entry no longer runs liuqin-switch to-android'
	policy=$root/usr/share/polkit-1/actions/io.github.liuqin.switch.policy
	for annotation in \
		'<annotate key="org.freedesktop.policykit.exec.path">/usr/local/sbin/liuqin-switch</annotate>' \
		'<annotate key="org.freedesktop.policykit.exec.argv1">to-android</annotate>'; do
		grep -qF "$annotation" "$policy" ||
			die 'the polkit action no longer matches pkexec liuqin-switch to-android'
	done
	for retired in usr/local/bin/liuqin-boot-android usr/local/libexec/liuqin-mark-slot-successful \
		usr/local/share/applications/liuqin-boot-android.desktop; do
		[ ! -e "$root/$retired" ] || die "the retired slot-switching path is back: /$retired"
	done
	command -v readelf >/dev/null || die 'readelf is required to verify generated executables'
	[ "$(LC_ALL=C readelf -h "$root/usr/local/libexec/liuqin-power-keyd" |
		sed -n 's/^[[:space:]]*Machine:[[:space:]]*//p')" = AArch64 ] ||
		die 'power-key daemon is not an AArch64 executable'
	install -D -m 0755 "$busybox" "$root/usr/local/bin/busybox"
	if [ -f "$overlay/usr/share/liuqin/power/io.github.liuqin.power.gschema.xml" ]; then
		install -D -m 0755 "$power_settings_binary" "$root/usr/bin/gnome-control-center"
		command -v glib-compile-schemas >/dev/null || die 'glib-compile-schemas is required'
		glib-compile-schemas --strict "$root/usr/share/liuqin/power"
		chmod 0644 "$root/usr/share/liuqin/power/gschemas.compiled"
	fi
	mkdir -p "$root/DEBIAN"
	write_divert_preinst "$pkg" /usr/bin/gnome-control-center
	write_divert_postrm "$pkg" /usr/bin/gnome-control-center
	{
		printf '#!/bin/sh\nset -e\nif [ "$1" = configure ]; then\n'
		printf '\tif [ -d /run/systemd/system ]; then systemctl daemon-reload || :; fi\n'
		printf '\tif command -v dconf >/dev/null; then dconf update || :; fi\n'
		printf 'fi\n'
	} >"$root/DEBIAN/postinst"
	cp "$work/$pkg.preinst" "$root/DEBIAN/preinst"
	cp "$work/$pkg.postrm" "$root/DEBIAN/postrm"
	pack "$pkg" arm64 'libasound2t64 (>= 1.2), gnome-control-center (>= 1:50.0)' \
		'Xiaomi Pad 6 Pro (liuqin) device support: units, helpers, rescue shell, power panel'
}

build_sensors() {
	pkg=liuqin-sensors
	[ -f "$sensor_stack" ] && [ ! -L "$sensor_stack" ] ||
		die "reviewed sensor stack is unavailable: $sensor_stack (run tools/build-liuqin-sensors-stack.sh)"
	sha_ok "$sensor_stack" "$sensor_stack_sha256" 'sensor stack artifact identity mismatch'
	root=$work/$pkg/root
	mkdir -p "$root"
	tar --extract --file "$sensor_stack" --directory "$root" --no-same-owner
	for required in \
		usr/bin/hexagonrpcd \
		usr/bin/ssccli \
		usr/local/libexec/liuqin-iio-sensor-proxy \
		usr/local/sbin/liuqin-sensor-registry-import \
		etc/udev/rules.d/80-liuqin-fastrpc.rules \
		etc/systemd/system/liuqin-ssc-sample-gate.service; do
		[ -e "$root/$required" ] || die "sensor stack merge missed $required"
	done
	# Runtime libraries come from the distribution via Depends, not from a
	# private copy inside the package.
	rm -f "$root"/usr/lib/aarch64-linux-gnu/libqrtr.so.1* \
		"$root"/usr/lib/aarch64-linux-gnu/libprotobuf-c.so.1*
	# End the mixed-ownership arrangement: instead of a /usr/local binary plus
	# an ExecStart override, the SSC-enabled proxy replaces the distro binary
	# in place through a diversion.  The drop-in keeps its real payload -- the
	# SSC sample gate and the AF_QIPCRTR sandbox admission -- and loses only
	# the two ExecStart lines that pointed at the /usr/local path.
	mkdir -p "$root/usr/libexec"
	mv "$root/usr/local/libexec/liuqin-iio-sensor-proxy" "$root/usr/libexec/iio-sensor-proxy"
	chmod 0755 "$root/usr/libexec/iio-sensor-proxy"
	dropin=$root/etc/systemd/system/iio-sensor-proxy.service.d/90-liuqin-ssc.conf
	[ -f "$dropin" ] || die 'sensor stack carries no SSC drop-in'
	grep -qx 'ExecStart=/usr/local/libexec/liuqin-iio-sensor-proxy' "$dropin" ||
		die 'SSC drop-in drifted from the reviewed ExecStart override'
	grep -vx -e 'ExecStart=' -e 'ExecStart=/usr/local/libexec/liuqin-iio-sensor-proxy' \
		"$dropin" >"$work/dropin.tmp"
	mv "$work/dropin.tmp" "$dropin"
	chmod 0644 "$dropin"
	mkdir -p "$root/DEBIAN"
	write_divert_preinst "$pkg" /usr/libexec/iio-sensor-proxy
	write_divert_postrm "$pkg" /usr/libexec/iio-sensor-proxy
	{
		printf '#!/bin/sh\nset -e\nif [ "$1" = configure ]; then\n'
		printf '\tif [ -d /run/systemd/system ]; then systemctl daemon-reload || :; fi\n'
		printf '\tif command -v systemd-sysusers >/dev/null; then systemd-sysusers || :; fi\n'
		printf '\tif command -v systemd-tmpfiles >/dev/null; then systemd-tmpfiles --create || :; fi\n'
		printf 'fi\n'
	} >"$root/DEBIAN/postinst"
	cp "$work/$pkg.preinst" "$root/DEBIAN/preinst"
	cp "$work/$pkg.postrm" "$root/DEBIAN/postrm"
	pack "$pkg" arm64 'libqrtr1, libprotobuf-c1, iio-sensor-proxy' \
		'Xiaomi Pad 6 Pro (liuqin) SSC sensor stack (hexagonrpcd, libssc, SSC iio-sensor-proxy)'
}

build_kernel() {
	pkg=liuqin-kernel
	[ -n "$kernel_modules_dir" ] ||
		die 'liuqin-kernel requires KERNEL_MODULES_DIR (build-liuqin-kernel-modules.sh output)'
	[ -n "$kernel_image" ] && [ -f "$kernel_image" ] ||
		die 'liuqin-kernel requires KERNEL_IMAGE (matching kernel OUT Image)'
	[ -n "$kernel_dtb" ] && [ -f "$kernel_dtb" ] ||
		die 'liuqin-kernel requires KERNEL_DTB (matching device.dtb)'
	# The boot builder produces its initramfs after root assembly.
	modules_tar=$kernel_modules_dir/modules.tar
	modules_manifest=$kernel_modules_dir/modules.manifest
	release_file=$kernel_modules_dir/kernel.release
	for f in "$modules_tar" "$modules_manifest" "$release_file"; do
		[ -f "$f" ] && [ ! -L "$f" ] || die "kernel-module input is unavailable: $f"
	done
	release=$(cat "$release_file")
	case $release in '' | *[!A-Za-z0-9._+-]*) die "unsafe kernel release: $release" ;; esac
	root=$work/$pkg/root
	mkdir -p "$root/boot"
	tar --extract --file "$modules_tar" --directory "$root" --no-same-owner
	[ -d "$root/usr/lib/modules/$release" ] ||
		die "module tree does not contain the pinned release $release"
	[ -f "$root/usr/lib/modules/$release/modules.dep" ] ||
		die 'module tree carries no dependency index'
	install -D -m 0644 "$kernel_image" "$root/boot/vmlinuz-$release"
	if [ -n "$kernel_initramfs" ]; then
		[ -f "$kernel_initramfs" ] || die "KERNEL_INITRAMFS is set but unavailable: $kernel_initramfs"
		install -D -m 0644 "$kernel_initramfs" "$root/boot/initrd.img-$release"
	fi
	install -D -m 0644 "$kernel_dtb" "$root/boot/dtb-$release"
	install -D -m 0644 "$modules_manifest" "$root/usr/share/liuqin/kernel-modules.manifest"
	printf '%s\n' "$release" >"$work/kernel.release"
	install -D -m 0644 "$work/kernel.release" "$root/usr/share/liuqin/kernel.release"
	mkdir -p "$root/DEBIAN"
	# The installer owns boot partition updates; this package only supplies files.
	pack "$pkg" arm64 '' \
		'Xiaomi Pad 6 Pro (liuqin) kernel modules and boot payload'
}

build_fingerprint() {
	[ -n "$fingerprint_bundle" ] && [ -f "$fingerprint_bundle" ] ||
		die 'liuqin-fingerprint requires a verified FINGERPRINT_BUNDLE input'
	python3 "$project_root/tools/build-liuqin-fingerprint-deb.py" \
		--bundle "$fingerprint_bundle" --out "$out_dir" --version "$version"
}

build_meta() {
	pkg=liuqin-device
	root=$work/$pkg/root
	mkdir -p "$root"
	depends="liuqin-firmware (= $version), liuqin-device-support (= $version), liuqin-sensors (= $version), liuqin-kernel (= $version)"
	[ -z "$fingerprint_bundle" ] || depends="$depends, liuqin-fingerprint (= $version)"
	pack "$pkg" arm64 \
		"$depends" \
		'Xiaomi Pad 6 Pro (liuqin) device support metapackage'
}

case ${1:-all} in
firmware) build_firmware ;;
device-support) build_device_support ;;
sensors) build_sensors ;;
kernel) build_kernel ;;
fingerprint) build_fingerprint ;;
meta) build_meta ;;
all)
	build_firmware
	build_device_support
	build_sensors
	if [ -n "$kernel_modules_dir" ]; then build_kernel; else
		printf 'build-liuqin-debs: skipping liuqin-kernel (no KERNEL_MODULES_DIR)\n' >&2
	fi
	[ -z "$fingerprint_bundle" ] || build_fingerprint
	build_meta
	;;
*) die 'usage: build-liuqin-debs.sh all|firmware|device-support|sensors|kernel|fingerprint|meta' ;;
esac
