#!/bin/sh
# SPDX-License-Identifier: MIT
#
# Reproducibly build the liuqin SSC userspace as an arm64 extension layer.
# The stock Ubuntu 26.04 root is mounted as a read-only overlay lowerdir; apt
# and compilation can never mutate the release root.  Exact ROM configuration
# is copied into the output, while per-device registry/calibration is excluded.
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
rootfs=${ROOTFS:-$project_root/tools/local/ubuntu-desktop-26.04-arm64/rootfs}
source_manifest=${SOURCE_MANIFEST:-$project_root/device/sensors/sources.manifest}
source_cache=${SOURCE_CACHE:-$project_root/tools/local/sensor-stack-src}
rom_sensors=${ROM_SENSORS:-$project_root/tools/local/roms/liuqin/OS2.0.6.0.VMYCNXM/extracted/super-work/vendor-extract/etc/sensors}
static_overlay=${SENSORS_OVERLAY:-$project_root/device/sensors-overlay}
ssc_accel_test_runner=$project_root/tests/ssc-accel-integration.py
hexagonrpc_patch=${HEXAGONRPC_PATCH:-$project_root/device/sensors/patches/0001-hexagonrpcd-expose-the-SSC-registry-version-sibling.patch}
hexagonrpc_sns_patch=${HEXAGONRPC_SNS_PATCH:-$project_root/device/sensors/patches/0002-hexagonrpcd-add-a-narrow-sns_registry-property-shim.patch}
iio_claim_patch=${IIO_CLAIM_PATCH:-$project_root/device/sensors/patches/0003-iio-sensor-proxy-start-preclaimed-coldplug-sensor.patch}
iio_polling_patch=${IIO_POLLING_PATCH:-$project_root/device/sensors/patches/0004-iio-sensor-proxy-serialize-ssc-accel-polling.patch}
iio_release_patch=${IIO_RELEASE_PATCH:-$project_root/device/sensors/patches/0005-iio-sensor-proxy-cancel-released-pending-claims.patch}
iio_availability_patch=${IIO_AVAILABILITY_PATCH:-$project_root/device/sensors/patches/0006-iio-sensor-proxy-broadcast-sensor-availability.patch}
out_dir=${OUT_DIR:-$project_root/out/liuqin-sensors-stack}
jobs=${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf 12)}
update_apt=${UPDATE_APT:-0}
resume=${RESUME:-0}
assemble_only=${ASSEMBLE_ONLY:-0}
caller_uid=${SUDO_UID:-$(id -u)}
caller_gid=${SUDO_GID:-$(id -g)}

expected_config_count=56
expected_config_manifest_sha256=1cbf79b3ba5bfdd3efa152bcb5900ff6addd703f61b19def59cea62ae3c56867
expected_sns_reg_sha256=d89a5dfd69b9681f6864bca8d24c17fb922cb19a9ed3a0f7f5c00e4b5dc1089b
expected_hexagonrpc_patch_sha256=856b046e572209b5f13c54ef100e685463aba23eefb850d0f3389c4c7ac6fa90
expected_hexagonrpc_sns_patch_sha256=7826943d36a93bbddf1746e96b831bbc2efd5e7c7ff1f0d8460fe2f2acf7a1a5
expected_iio_claim_patch_sha256=84bd7c4986d5e1ad68a6edfe241147e41262db3c95f69b054e464a95846ac808
expected_iio_polling_patch_sha256=978b9d168584890e576ac6cee4b27fb75260d0117da4b0d68813a25fe15fa738
expected_iio_release_patch_sha256=c46633a0ff858cd4d28fcdc14226f473a874c7fc6073970a215bc77b1be11aaf
expected_iio_availability_patch_sha256=c260ff38fc9f08e0ac9e84e0c661a3c692166f8969e2402f9ad50108cd6dc35c
expected_sns_reg_version_sha256=dbe3f332ebc155730bd5291a550e3b89307c948a87111daef7cad7415770f729
device_prefix=usr/share/qcom/sm8450/Xiaomi/liuqin

die() { printf 'build-liuqin-sensors-stack: %s\n' "$*" >&2; exit 1; }
say() { printf 'build-liuqin-sensors-stack: %s\n' "$*"; }

[ "$(id -u)" = 0 ] || die 'run as root (an overlay mount and arm64 chroot are required)'
# The arm64 chroot runs under qemu-user. The default pointer-authentication
# emulation makes it several times slower; the implementation-defined
# algorithm keeps the CPU features the guest sees and only speeds it up.
export QEMU_CPU=max,pauth-impdef=on
[ -x "$rootfs/usr/lib/systemd/systemd" ] || die "not an Ubuntu root: $rootfs"
[ -r "$source_manifest" ] || die "missing source manifest: $source_manifest"
[ -r "$ssc_accel_test_runner" ] || die "missing SSC accelerometer test runner: $ssc_accel_test_runner"
[ -r "$hexagonrpc_patch" ] || die "missing hexagonrpc patch: $hexagonrpc_patch"
[ -r "$hexagonrpc_sns_patch" ] || die "missing hexagonrpc patch: $hexagonrpc_sns_patch"
[ -r "$iio_claim_patch" ] || die "missing iio-sensor-proxy patch: $iio_claim_patch"
[ -r "$iio_polling_patch" ] || die "missing iio-sensor-proxy patch: $iio_polling_patch"
[ -r "$iio_release_patch" ] || die "missing iio-sensor-proxy patch: $iio_release_patch"
[ -r "$iio_availability_patch" ] || die "missing iio-sensor-proxy patch: $iio_availability_patch"
[ -d "$static_overlay" ] || die "missing static sensor overlay: $static_overlay"
[ -d "$rom_sensors/config" ] || die "missing exact-ROM sensor config: $rom_sensors/config"
[ -r "$rom_sensors/sns_reg_config" ] || die 'missing exact-ROM sns_reg_config'
command -v git >/dev/null || die 'git is required'
command -v mount >/dev/null || die 'mount is required'
command -v tar >/dev/null || die 'tar is required'

case $source_cache in "$project_root"/tools/local/* | /tmp/*) ;; *) die 'SOURCE_CACHE must be below tools/local or /tmp' ;; esac
case $out_dir in "$project_root"/out/* | /tmp/*) ;; *) die 'OUT_DIR must be below out or /tmp' ;; esac
case $source_cache:$out_dir in *'/../'*|*'/..:'*|*':..'*) die 'path traversal is not allowed' ;; esac

# The hash is over a sorted, basename-only SHA256SUMS file, so it is stable
# across checkout locations.  This pins the exact OS2.0.6.0.VMYCNXM input.
registry_manifest=$(mktemp)
trap 'rm -f "$registry_manifest"' EXIT
(cd "$rom_sensors/config" &&
	find . -maxdepth 1 -type f -printf '%P\0' | LC_ALL=C sort -z | xargs -0 sha256sum) >"$registry_manifest"
config_count=$(wc -l <"$registry_manifest" | tr -d ' ')
[ "$config_count" = "$expected_config_count" ] || die "expected $expected_config_count ROM configs, got $config_count"
[ "$(sha256sum "$registry_manifest" | cut -d' ' -f1)" = "$expected_config_manifest_sha256" ] ||
	die 'exact-ROM sensor config manifest mismatch'
[ "$(sha256sum "$rom_sensors/sns_reg_config" | cut -d' ' -f1)" = "$expected_sns_reg_sha256" ] ||
	die 'exact-ROM sns_reg_config mismatch'
[ "$(grep -c '^property=' "$rom_sensors/sns_reg_config")" = 2 ] ||
	die 'exact-ROM sns_reg_config property count mismatch'
grep -Fqx 'property=persist.vendor.sensors.enable.property=/mnt/vendor/persist/sensors/registry/file1' \
	"$rom_sensors/sns_reg_config" || die 'exact-ROM sensor property 0 mismatch'
grep -Fqx 'property=persist.vendor.sensors.enable.property1=/mnt/vendor/persist/sensors/registry/file2' \
	"$rom_sensors/sns_reg_config" || die 'exact-ROM sensor property 1 mismatch'
[ "$(sha256sum "$hexagonrpc_patch" | cut -d' ' -f1)" = "$expected_hexagonrpc_patch_sha256" ] ||
	die 'hexagonrpc registry-version patch mismatch'
[ "$(sha256sum "$hexagonrpc_sns_patch" | cut -d' ' -f1)" = "$expected_hexagonrpc_sns_patch_sha256" ] ||
	die 'hexagonrpc sns_registry patch mismatch'
[ "$(sha256sum "$iio_claim_patch" | cut -d' ' -f1)" = "$expected_iio_claim_patch_sha256" ] ||
	die 'iio-sensor-proxy preclaimed coldplug patch mismatch'
[ "$(sha256sum "$iio_polling_patch" | cut -d' ' -f1)" = "$expected_iio_polling_patch_sha256" ] ||
	die 'iio-sensor-proxy polling patch mismatch'
[ "$(sha256sum "$iio_release_patch" | cut -d' ' -f1)" = "$expected_iio_release_patch_sha256" ] ||
	die 'iio-sensor-proxy release patch mismatch'
[ "$(sha256sum "$iio_availability_patch" | cut -d' ' -f1)" = "$expected_iio_availability_patch_sha256" ] ||
	die 'iio-sensor-proxy availability patch mismatch'

mkdir -p "$source_cache"
while IFS="$(printf '\t')" read -r component version repository commit tree; do
	case $component in ''|'#'*) continue ;; esac
	dir=$source_cache/$component
	if [ ! -d "$dir/.git" ]; then
		say "fetching $component $version"
		git clone --filter=blob:none --no-checkout "$repository" "$dir"
	fi
	[ "$(git -C "$dir" remote get-url origin)" = "$repository" ] ||
		die "$component origin differs from the pinned repository"
	if ! git -C "$dir" cat-file -e "$commit^{commit}" 2>/dev/null; then
		git -C "$dir" fetch --quiet origin "$commit"
	fi
	git -C "$dir" checkout --quiet --detach "$commit"
	[ "$(git -C "$dir" rev-parse HEAD)" = "$commit" ] || die "$component commit mismatch"
	[ "$(git -C "$dir" rev-parse 'HEAD^{tree}')" = "$tree" ] || die "$component tree mismatch"
	git -C "$dir" diff --quiet && git -C "$dir" diff --cached --quiet || die "$component source tree is dirty"
done <"$source_manifest"

# Avoid a broad deletion target derived from the environment.  RESUME=1 keeps
# a failed isolated upperdir (never the stock root) so a dependency correction
# need not download and configure the whole toolchain again.
case $resume in
0)
	rm -rf "$out_dir"
	mkdir -p "$out_dir/work/upper" "$out_dir/work/overlay" "$out_dir/work/root" \
		"$out_dir/work/dest" "$out_dir/work/source" "$out_dir/artifacts"
	;;
1)
	[ -d "$out_dir/work/upper" ] && [ -d "$out_dir/work/overlay" ] ||
		die 'RESUME=1 requested but no isolated work directory exists'
	mkdir -p "$out_dir/work/root" "$out_dir/work/dest" "$out_dir/artifacts"
	;;
*) die 'RESUME must be 0 or 1' ;;
esac
work=$out_dir/work
merged=$work/root
dest=$work/dest

# Keep the pinned mirrors immutable. Build from an isolated byte copy and
# apply the reviewed local deltas there; a future source-cache refresh can
# never silently absorb or drop a sensor contract.
rm -rf "$work/source"
mkdir -p "$work/source"
cp -a "$source_cache/." "$work/source/"
install -m 0755 "$ssc_accel_test_runner" "$work/source/liuqin-ssc-accel-integration.py"
git -C "$work/source/hexagonrpc" apply --check "$hexagonrpc_patch" ||
	die 'hexagonrpc registry-version patch no longer applies to the pinned source'
git -C "$work/source/hexagonrpc" apply "$hexagonrpc_patch"
git -C "$work/source/hexagonrpc" apply --check "$hexagonrpc_sns_patch" ||
	die 'hexagonrpc sns_registry patch no longer applies after the registry-version patch'
git -C "$work/source/hexagonrpc" apply "$hexagonrpc_sns_patch"
git -C "$work/source/hexagonrpc" diff --check || die 'patched hexagonrpc tree has whitespace errors'
# Reuse the reviewed common-library fixes against the exact source pins.
(cd "$project_root/device/sensors" && sha256sum -c preventive-patches.sha256) ||
 die 'preventive sensor patch identity mismatch'
while read -r digest relative; do
 component=${relative%%/*}
 patch=$project_root/device/sensors/$relative
 git -C "$work/source/$component" apply --check "$patch" || die "sensor patch does not apply: $relative"
 git -C "$work/source/$component" apply "$patch"
done <"$project_root/device/sensors/preventive-patches.sha256"
git -C "$work/source/iio-sensor-proxy" apply --check "$iio_claim_patch" ||
	die 'iio-sensor-proxy preclaimed coldplug patch no longer applies to the pinned source'
git -C "$work/source/iio-sensor-proxy" apply "$iio_claim_patch"
for patch in "$iio_polling_patch" "$iio_release_patch" "$iio_availability_patch"; do
	git -C "$work/source/iio-sensor-proxy" apply --check "$patch" || die "sensor patch does not apply: $patch"
	git -C "$work/source/iio-sensor-proxy" apply "$patch"
done
git -C "$work/source/iio-sensor-proxy" diff --check ||
	die 'patched iio-sensor-proxy tree has whitespace errors'

mounted_overlay=0
mounted_source=0
mounted_proc=0
mounted_resolv=0
mounted_devpts=0
cleanup() {
	set +e
	if [ "$mounted_resolv" != 0 ] && mountpoint -q "$merged/run/systemd/resolve/stub-resolv.conf"; then
		umount "$merged/run/systemd/resolve/stub-resolv.conf" || umount -l "$merged/run/systemd/resolve/stub-resolv.conf"
	fi
	if [ "$mounted_proc" != 0 ] && mountpoint -q "$merged/proc"; then
		umount "$merged/proc" || umount -l "$merged/proc"
	fi
	if [ "$mounted_devpts" != 0 ] && mountpoint -q "$merged/dev/pts"; then
		umount "$merged/dev/pts" || umount -l "$merged/dev/pts"
	fi
	if [ "$mounted_source" != 0 ] && mountpoint -q "$merged/build/source"; then
		umount "$merged/build/source" || umount -l "$merged/build/source"
	fi
	if [ "$mounted_overlay" != 0 ] && mountpoint -q "$merged"; then
		umount "$merged" || umount -l "$merged"
	fi
	# sudo must not leave the reusable source mirror or deliverables readable
	# only by root.  Do not chown the overlay upper: it models a root filesystem.
	chown -R "$caller_uid:$caller_gid" "$source_cache" 2>/dev/null || true
	chown -R "$caller_uid:$caller_gid" "$out_dir/artifacts" 2>/dev/null || true
	rm -f "$registry_manifest"
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

mount -t overlay overlay -o "lowerdir=$rootfs,upperdir=$work/upper,workdir=$work/overlay" "$merged"
mounted_overlay=1
mkdir -p "$merged/build/source" "$merged/build/work" "$merged/build/dest" \
	"$merged/run/systemd/resolve" "$merged/dev/pts"
mount --bind "$work/source" "$merged/build/source"
mounted_source=1
mount -o remount,bind,ro "$merged/build/source"
mount -t proc proc "$merged/proc"
mounted_proc=1
mount -t devpts devpts "$merged/dev/pts" -o newinstance,ptmxmode=0666,mode=0620
mounted_devpts=1
: >"$merged/run/systemd/resolve/stub-resolv.conf"
mount --bind /etc/resolv.conf "$merged/run/systemd/resolve/stub-resolv.conf"
mounted_resolv=1

case $assemble_only in
0)
say 'installing isolated Ubuntu 26.04 arm64 build dependencies'
# appstreamcli's catalogue refresh is a desktop convenience and takes several
# minutes under QEMU.  Removing its apt hook only in this disposable upperdir
# makes no package-selection difference and leaves the stock root untouched.
rm -f "$merged/etc/apt/apt.conf.d/50appstream" \
	"$merged/etc/apt/apt.conf.d/50command-not-found" \
	"$merged/etc/apt/apt.conf.d/99update-notifier" \
	"$merged/etc/apt/sources.list.d/cdrom.sources"
# Development-library upgrades can trigger dracut to rebuild the base root's
# unrelated generic-kernel initramfs.  That output is never copied into the
# sensor layer and takes many minutes under QEMU, so replace only the
# disposable upperdir's update-initramfs entry point with a successful no-op.
# The immutable release root below the overlay remains byte-for-byte intact.
cat >"$merged/usr/sbin/update-initramfs" <<'EOF'
#!/bin/sh
printf '%s\n' 'liuqin sensor build: skipped unrelated initramfs regeneration'
exit 0
EOF
chmod 0755 "$merged/usr/sbin/update-initramfs"
# RESUME may follow an interrupted package trigger.  Complete only the
# disposable upperdir's pending dpkg configuration before asking apt to solve
# the declared dependency set again.
chroot "$merged" /usr/bin/env DEBIAN_FRONTEND=noninteractive \
	dpkg --configure --pending
if [ "$update_apt" = 1 ]; then
	chroot "$merged" /usr/bin/env DEBIAN_FRONTEND=noninteractive \
		apt-get -o Acquire::Retries=3 update
elif [ "$update_apt" != 0 ]; then
	die 'UPDATE_APT must be 0 or 1'
else
	say 'using the Ubuntu rootfs pinned package indexes (UPDATE_APT=0)'
fi
chroot "$merged" /usr/bin/env DEBIAN_FRONTEND=noninteractive \
	apt-get -o Acquire::Retries=3 -o DPkg::Options::=--no-triggers \
	--no-install-recommends install -y \
	build-essential meson ninja-build pkg-config \
	python3-dev python3-gi python3-protobuf python3-dbusmock libqrtr1 \
	gir1.2-umockdev-1.0 umockdev locales-all \
	libglib2.0-dev libqmi-glib-dev libprotobuf-c-dev protobuf-c-compiler protobuf-compiler \
	libgudev-1.0-dev libpolkit-gobject-1-dev libsystemd-dev libjson-c-dev

cat >"$merged/build/build.sh" <<'EOF'
#!/bin/sh
set -eu
jobs=${JOBS:-12}
# Keep deterministic messages by default, but do not set LC_ALL: two upstream
# tests intentionally override LC_NUMERIC=fr_FR.UTF-8 and LC_ALL would win.
unset LC_ALL
export LANG=C.UTF-8
export SOURCE_DATE_EPOCH=0
export CFLAGS='-O2 -g0 -ffile-prefix-map=/build=. -fdebug-prefix-map=/build=.'
export LDFLAGS='-Wl,--build-id=sha1'

build_one() {
	name=$1
	shift
	reconfigure=
	[ ! -f "/build/work/$name/meson-private/coredata.dat" ] || reconfigure=--reconfigure
	meson setup $reconfigure "/build/work/$name" "/build/source/$name" \
		--prefix=/usr --libdir=lib/aarch64-linux-gnu --buildtype=release --wrap-mode=nodownload "$@"
	meson compile -C "/build/work/$name" -j "$jobs"
}

testlog_passed() {
	log=$1
	expected=$2
	[ -f "$log" ] || return 1
	[ "$(grep -c '^result: *exit status 0$' "$log" || true)" = "$expected" ] || return 1
	[ "$(grep -c '^result: *exit status [^0]' "$log" || true)" = 0 ] || return 1
}

build_one hexagonrpc -Dhexagonrpcd_verbose=false
meson test -C /build/work/hexagonrpc --print-errorlogs --timeout-multiplier=4
DESTDIR=/build/dest meson install -C /build/work/hexagonrpc

build_one libssc
meson test -C /build/work/libssc --print-errorlogs --timeout-multiplier=4
# Exercise the upstream mock runner itself: a failed child must not turn green.
child_status=0
/build/work/libssc/mocking/ssc_server/ssc-server "sh -c 'exit 7' # no-service" || child_status=$?
[ "$child_status" = 7 ] || { echo "ssc-server lost child exit status: $child_status" >&2; exit 1; }
DESTDIR=/build/dest meson install -C /build/work/libssc
cp -a /build/dest/usr/. /usr/
ldconfig

build_one iio-sensor-proxy -Dssc-support=enabled -Dtests=true -Dgtk-tests=false -Dgtk_doc=false \
	-Dudevrulesdir=/usr/lib/udev/rules.d -Dsystemdsystemunitdir=/usr/lib/systemd/system
# All four SSC cases register QRTR service 400.  Run the other 21 tests in
# parallel, then the SSC cases serially: full coverage without the false race
# where one mock removes another's service.
test_list=$(meson test -C /build/work/iio-sensor-proxy --list)
non_ssc_tests=$(printf '%s\n' "$test_list" | grep -v 'Tests.test_ssc_')
ssc_tests=$(printf '%s\n' "$test_list" | grep 'Tests.test_ssc_')
[ "$(printf '%s\n' "$non_ssc_tests" | grep -c .)" = 21 ] || {
	printf '%s\n' 'unexpected non-SSC iio test count' >&2
	exit 1
}
[ "$(printf '%s\n' "$ssc_tests" | grep -c .)" = 4 ] || {
	printf '%s\n' 'unexpected SSC iio test count' >&2
	exit 1
}
ssc_accel_test=$(printf '%s\n' "$ssc_tests" | grep 'Tests.test_ssc_accel$')
[ "$(printf '%s\n' "$ssc_accel_test" | grep -c .)" = 1 ] || {
	printf '%s\n' 'SSC accelerometer test is absent or ambiguous' >&2
	exit 1
}
# Test names contain no whitespace in upstream 3.9, so deliberate word
# splitting passes one name per argument.
non_ssc_log=/build/work/iio-sensor-proxy/meson-logs/testlog-non-ssc.txt
if testlog_passed "$non_ssc_log" 21; then
	printf '%s\n' 'iio-sensor-proxy: retaining current 21/21 non-SSC PASS testlog'
else
	# shellcheck disable=SC2086
	meson test -C /build/work/iio-sensor-proxy --num-processes "$jobs" \
		--print-errorlogs --timeout-multiplier=4 $non_ssc_tests
	cp /build/work/iio-sensor-proxy/meson-logs/testlog.txt "$non_ssc_log"
fi
# Preserve the already-observed upstream light publication race before the
# required accelerometer run writes its own test log.
current_log=/build/work/iio-sensor-proxy/meson-logs/testlog.txt
light_fail_log=/build/work/iio-sensor-proxy/meson-logs/testlog-ssc-light-known-fail.txt
if grep -q 'Tests.test_ssc_light' "$current_log" 2>/dev/null &&
	grep -q '^result: *exit status [^0]' "$current_log" 2>/dev/null; then
	cp "$current_log" "$light_fail_log"
fi
ssc_log=/build/work/iio-sensor-proxy/meson-logs/testlog-ssc.txt
: >"$ssc_log"
# iio-sensor-proxy's own ssc-test.py does not create a mock service.  Upstream
# CI starts libssc's server once before invoking Meson; mirror that exact
# topology instead of relying on a service left behind by another test.
/build/work/libssc/mocking/ssc_server/ssc-server \
	>/build/work/iio-sensor-proxy/meson-logs/ssc-server.log 2>&1 &
ssc_server_pid=$!
cleanup_ssc_server() {
	kill "$ssc_server_pid" 2>/dev/null || true
	wait "$ssc_server_pid" 2>/dev/null || true
}
trap cleanup_ssc_server EXIT HUP INT TERM
sleep 2
kill -0 "$ssc_server_pid" 2>/dev/null || {
	cat /build/work/iio-sensor-proxy/meson-logs/ssc-server.log >&2
	exit 1
}
# Upstream keeps this runtime driver but marks its generic udev admission and
# corresponding test skipped because some unrelated devices can hang during
# setup.  liuqin's sample gate has already bounded and proven that exact path.
# Reuse upstream's test fixture and mock server, but wait for the asynchronous
# D-Bus publications instead of reading immediately after driver discovery.
# This requires HasAccelerometer=true, a normal post-discovery claim, and a
# claim which re-enters synchronous coldplug discovery before the driver is
# published.  Both paths must receive an orientation measurement, so a Meson
# "SKIP" or the real Claim-before-device race cannot masquerade as coverage.
if ! top_builddir=/build/work/iio-sensor-proxy \
	top_srcdir=/build/source/iio-sensor-proxy \
	timeout --signal=TERM --kill-after=5s 120s \
	umockdev-wrapper python3 /build/source/liuqin-ssc-accel-integration.py \
	/build/source/iio-sensor-proxy/tests/ssc-test.py >"$ssc_log" 2>&1; then
	cat "$ssc_log" >&2
	exit 1
fi
grep -q '^OK$' "$ssc_log" || {
	cat "$ssc_log" >&2
	printf '%s\n' 'SSC accelerometer integration test did not complete with OK' >&2
	exit 1
}
if grep -Eq '^test_liuqin_ssc_(accel|claim_during_coldplug) .* skipped ' "$ssc_log"; then
	cat "$ssc_log" >&2
	printf '%s\n' 'SSC accelerometer integration test was skipped' >&2
	exit 1
fi
grep -Eq '^test_liuqin_ssc_accel .* ok$' "$ssc_log" || {
	cat "$ssc_log" >&2
	printf '%s\n' 'normal SSC accelerometer claim did not pass' >&2
	exit 1
}
grep -Eq '^test_liuqin_ssc_claim_during_coldplug .* ok$' "$ssc_log" || {
	cat "$ssc_log" >&2
	printf '%s\n' 'SSC Claim-before-coldplug regression did not pass' >&2
	exit 1
}
cleanup_ssc_server
trap - EXIT HUP INT TERM
DESTDIR=/build/dest meson install -C /build/work/iio-sensor-proxy

dpkg-query -W -f='${binary:Package}\t${Version}\t${Architecture}\n' \
	build-essential meson ninja-build pkg-config libglib2.0-dev libqmi-glib-dev \
	python3-dev python3-gi python3-protobuf python3-dbusmock libqrtr1 \
	gir1.2-umockdev-1.0 umockdev locales-all \
	libprotobuf-c-dev protobuf-c-compiler protobuf-compiler libgudev-1.0-dev \
	libpolkit-gobject-1-dev libsystemd-dev libjson-c-dev |
	LC_ALL=C sort >/build/dest/ubuntu-resolute-build-deps.tsv
EOF
chmod 0755 "$merged/build/build.sh"
JOBS=$jobs chroot "$merged" /build/build.sh
;;
1)
	[ -x "$merged/build/dest/usr/bin/ssccli" ] || die 'ASSEMBLE_ONLY has no completed ssccli'
	[ -x "$merged/build/dest/usr/bin/hexagonrpcd" ] || die 'ASSEMBLE_ONLY has no completed hexagonrpcd'
	[ -x "$merged/build/dest/usr/libexec/iio-sensor-proxy" ] || die 'ASSEMBLE_ONLY has no completed SensorProxy'
	say 'reusing the completed and tested arm64 build (ASSEMBLE_ONLY=1)'
	;;
*) die 'ASSEMBLE_ONLY must be 0 or 1' ;;
esac

# Overlay mounts cannot bind a directory that is also the host destination;
# the build wrote to merged/build/dest, which resolves to the overlay upper.
cp -a "$merged/build/dest/." "$dest/"
if [ -r "$merged/build/work/iio-sensor-proxy/meson-logs/testlog-ssc-light-known-fail.txt" ]; then
	cp "$merged/build/work/iio-sensor-proxy/meson-logs/testlog-ssc-light-known-fail.txt" \
		"$out_dir/artifacts/iio-ssc-light-known-fail.log"
fi
ssc_accel_log=$merged/build/work/iio-sensor-proxy/meson-logs/testlog-ssc.txt
[ -r "$ssc_accel_log" ] || die 'SSC accelerometer integration log is absent'
grep -q '^OK$' "$ssc_accel_log" || die 'SSC accelerometer integration log is not an unskipped PASS'
if grep -Eq '^test_liuqin_ssc_(accel|claim_during_coldplug) .* skipped ' "$ssc_accel_log"; then
	die 'SSC accelerometer integration log contains a skipped test'
fi
grep -Eq '^test_liuqin_ssc_accel .* ok$' "$ssc_accel_log" ||
	die 'normal SSC accelerometer claim is absent from the integration log'
grep -Eq '^test_liuqin_ssc_claim_during_coldplug .* ok$' "$ssc_accel_log" ||
	die 'Claim-before-coldplug regression is absent from the integration log'
cp "$ssc_accel_log" "$out_dir/artifacts/iio-ssc-accel-unskipped.log"

# libqrtr is a direct dependency of the upstream libssc test server and is not
# present in the base root. Carry the complete runtime pair, including
# the SONAME symlink, rather than letting success depend on the build upperdir.
mkdir -p "$dest/usr/lib/aarch64-linux-gnu"
for qrtr_lib in "$merged"/usr/lib/aarch64-linux-gnu/libqrtr.so.1*; do
	[ -e "$qrtr_lib" ] || die 'Ubuntu libqrtr1 installed no libqrtr.so.1 runtime'
	cp -a "$qrtr_lib" "$dest/usr/lib/aarch64-linux-gnu/"
done

say 'assembling static policy and exact-ROM registry'
# hexagonrpc derives its unit directory from libdir and consequently installs
# three upstream templates below the multiarch library directory.  They are
# neither a valid systemd search path nor the policy used on liuqin; retain the
# binaries/libraries but remove only that exact generated subtree.
rm -rf "$dest/usr/lib/aarch64-linux-gnu/systemd"
# The upstream install target also emits headers, pkg-config metadata and its
# Python mock server.  They are build/test inputs, not tablet runtime files.
rm -rf "$dest/usr/include/libssc" \
	"$dest/usr/lib/python3/dist-packages/ssc_server" \
	"$dest/usr/libexec/installed-tests"
rm -f "$dest/usr/lib/aarch64-linux-gnu/libhexagonrpc.so" \
	"$dest/usr/lib/aarch64-linux-gnu/libssc.so" \
	"$dest/usr/lib/aarch64-linux-gnu/pkgconfig/libssc.pc"
# Ubuntu owns the 3.8 SensorProxy binary/unit/data paths.  Keep those package
# files intact and install only our verified 3.9+SSC executable under
# /usr/local; the drop-in above replaces ExecStart without fighting dpkg.
install -D -m 0755 "$dest/usr/libexec/iio-sensor-proxy" \
	"$dest/usr/local/libexec/liuqin-iio-sensor-proxy"
rm -f "$dest/usr/libexec/iio-sensor-proxy" "$dest/usr/bin/monitor-sensor" \
	"$dest/usr/lib/systemd/system/iio-sensor-proxy.service" \
	"$dest/usr/lib/udev/rules.d/80-iio-sensor-proxy.rules" \
	"$dest/usr/share/dbus-1/system.d/net.hadess.SensorProxy.conf" \
	"$dest/usr/share/polkit-1/actions/net.hadess.SensorProxy.policy"
cp -a "$static_overlay/." "$dest/"
config_dst=$dest/$device_prefix/sensors/config
mkdir -p "$config_dst" "$dest/$device_prefix/sensors" "$dest/var/lib/liuqin-sensors"
cp -a "$rom_sensors/config/." "$config_dst/"
install -m 0644 "$rom_sensors/sns_reg_config" "$dest/$device_prefix/sensors/sns_reg.conf"
version=$(sed -n 's/^version=//p' "$rom_sensors/sns_reg_config")
[ "$version" = 12 ] || die "unexpected sns registry version: $version"
printf 'version=%s\0' "$version" >"$dest/$device_prefix/sensors/sns_reg_version"
[ "$(sha256sum "$dest/$device_prefix/sensors/sns_reg_version" | cut -d' ' -f1)" = \
	"$expected_sns_reg_version_sha256" ] || die 'generated sns_reg_version mismatch'
cp "$registry_manifest" "$dest/$device_prefix/sensors/config.SHA256SUMS"
ln -sfn /var/lib/liuqin-sensors/registry "$dest/$device_prefix/sensors/registry"
install -d "$dest/usr/share/doc/liuqin"
cp "$source_manifest" "$dest/usr/share/doc/liuqin/sensor-stack-sources.manifest"
cp "$project_root/device/sensors/preventive-patches.json" "$project_root/device/sensors/preventive-patches.sha256" "$dest/usr/share/doc/liuqin/"
{
	printf '%s  %s\n' "$expected_hexagonrpc_patch_sha256" "${hexagonrpc_patch##*/}"
	printf '%s  %s\n' "$expected_hexagonrpc_sns_patch_sha256" "${hexagonrpc_sns_patch##*/}"
	printf '%s  %s\n' "$expected_iio_claim_patch_sha256" "${iio_claim_patch##*/}"
	printf '%s  %s\n' "$expected_iio_polling_patch_sha256" "${iio_polling_patch##*/}"
	printf '%s  %s\n' "$expected_iio_release_patch_sha256" "${iio_release_patch##*/}"
	printf '%s  %s\n' "$expected_iio_availability_patch_sha256" "${iio_availability_patch##*/}"
} >"$dest/usr/share/doc/liuqin/sensor-stack-patches.manifest"
mv "$dest/ubuntu-resolute-build-deps.tsv" \
	"$dest/usr/share/doc/liuqin/ubuntu-resolute-sensor-build-deps.tsv"

# Do not enable the target at multi-user.target: it would run before the
# FastRPC character device exists, become active with a skipped daemon, and
# make the later udev SYSTEMD_WANTS event a no-op.  The device event is the
# sole activation edge.  Upstream daemon units are also deliberately not
# enabled, so only one process can own the FastRPC endpoint.

find "$dest" -type f -perm /022 -exec chmod go-w {} +
find "$dest/usr/libexec" "$dest/usr/local/sbin" -type f -exec chmod 0755 {} +

# Close runtime libraries against the unmodified Ubuntu root, not the apt-rich
# build upper.  Copy only a SONAME that is absent from both the base and the
# extension, plus its symlink target, then iterate because that object may add
# another dependency.  This is how libprotobuf-c.so.1 was caught offline.
closure_round=0
while :; do
	closure_round=$((closure_round + 1))
	[ "$closure_round" -le 12 ] || die 'runtime dependency closure did not converge'
	missing=$work/runtime-missing.tsv
	: >"$missing"
	find "$dest" -type f -print | LC_ALL=C sort | while IFS= read -r elf; do
		file "$elf" | grep -q 'ELF 64-bit LSB.*ARM aarch64' || continue
		readelf -d "$elf" 2>/dev/null | sed -n 's/.*Shared library: \[\([^]]*\)\].*/\1/p' |
		while IFS= read -r needed; do
			[ -n "$needed" ] || continue
			provider=$(find "$dest/lib" "$dest/usr/lib" "$rootfs/lib" "$rootfs/usr/lib" \
				-name "$needed" -print -quit 2>/dev/null || true)
			[ -n "$provider" ] || printf '%s\t%s\n' "${elf#$dest/}" "$needed" >>"$missing"
		done
	done
	sort -u -o "$missing" "$missing"
	[ -s "$missing" ] || break
	while IFS="$(printf '\t')" read -r consumer needed; do
		source=$(find "$merged/lib" "$merged/usr/lib" -name "$needed" -print -quit 2>/dev/null || true)
		[ -n "$source" ] || die "build root cannot provide $needed required by $consumer"
		rel=${source#$merged/}
		target_dir=$dest/${rel%/*}
		mkdir -p "$target_dir"
		cp -a "$source" "$target_dir/"
		if [ -L "$source" ]; then
			link_target=$(readlink "$source")
			case $link_target in /*) die "absolute runtime library symlink: $source" ;; esac
			[ -e "${source%/*}/$link_target" ] || die "broken runtime library symlink: $source"
			cp -a "${source%/*}/$link_target" "$target_dir/"
		fi
	done <"$missing"
done

say 'auditing architecture, links, and private-data boundary'
elf_count=0
find "$dest" -type f -print | LC_ALL=C sort | while IFS= read -r file; do
	case $(file -b "$file") in
	*ELF*)
		file "$file" | grep -q 'ARM aarch64' || die "non-arm64 ELF in layer: ${file#$dest}"
		;;
	esac
done
elf_count=$(find "$dest" -type f -exec file {} + | grep -c 'ELF 64-bit LSB.*ARM aarch64' || true)
[ "$elf_count" -ge 7 ] || die "too few arm64 ELF artifacts: $elf_count"
[ -x "$dest/usr/bin/ssccli" ] || die 'ssccli was not installed'
[ -x "$dest/usr/bin/hexagonrpcd" ] || die 'hexagonrpcd was not installed'
[ -x "$dest/usr/local/libexec/liuqin-iio-sensor-proxy" ] || die 'local iio-sensor-proxy was not installed'
[ -L "$dest/usr/lib/aarch64-linux-gnu/libqrtr.so.1" ] || die 'libqrtr SONAME symlink is absent'
qrtr_target=$(readlink "$dest/usr/lib/aarch64-linux-gnu/libqrtr.so.1")
[ -n "$qrtr_target" ] && [ -s "$dest/usr/lib/aarch64-linux-gnu/$qrtr_target" ] ||
	die 'libqrtr SONAME target is absent'
[ -L "$dest/$device_prefix/sensors/registry" ] || die 'private registry import link is absent'
[ "$(readlink "$dest/$device_prefix/sensors/registry")" = /var/lib/liuqin-sensors/registry ] ||
	die 'private registry link target is wrong'
[ -z "$(find "$dest/var/lib/liuqin-sensors" -type f -print -quit)" ] ||
	die 'per-device calibration leaked into the release layer'

# Resolve every DT_NEEDED against the stock release root plus this extension,
# not against the apt-populated build upperdir.  This catches the classic
# "works in the build chroot, missing on the tablet" packaging error.
needed_audit=$out_dir/artifacts/runtime-needed.tsv
: >"$needed_audit"
find "$dest" -type f -print | LC_ALL=C sort | while IFS= read -r elf; do
	file "$elf" | grep -q 'ELF 64-bit LSB.*ARM aarch64' || continue
	readelf -d "$elf" 2>/dev/null | sed -n 's/.*Shared library: \[\([^]]*\)\].*/\1/p' |
	while IFS= read -r needed; do
		[ -n "$needed" ] || continue
		provider=$(find -L "$dest/lib" "$dest/usr/lib" "$rootfs/lib" "$rootfs/usr/lib" \
			-type f -name "$needed" -print -quit 2>/dev/null || true)
		[ -n "$provider" ] || die "unclosed runtime dependency ${elf#$dest/}: $needed"
		case $provider in
		"$dest"/*) source=extension ;;
		*) source=ubuntu-root ;;
		esac
		printf '%s\t%s\t%s\n' "${elf#$dest/}" "$needed" "$source" >>"$needed_audit"
	done
done
[ -s "$needed_audit" ] || die 'runtime dependency audit produced no records'

(cd "$dest" && find . -type f ! -name layer.manifest -printf '%P\0' |
	LC_ALL=C sort -z | xargs -0 sha256sum) >"$out_dir/artifacts/layer.manifest"
(cd "$dest" && find . -type l -printf '%P -> %l\n' | LC_ALL=C sort) >"$out_dir/artifacts/links.manifest"
cp "$dest/usr/share/doc/liuqin/ubuntu-resolute-sensor-build-deps.tsv" \
	"$out_dir/artifacts/ubuntu-resolute-build-deps.tsv"

tar --sort=name --mtime='@0' --owner=0 --group=0 --numeric-owner \
	--pax-option=delete=atime,delete=ctime -C "$dest" -cf "$out_dir/artifacts/sensor-stack.tar" .
sha256sum "$out_dir/artifacts/sensor-stack.tar" >"$out_dir/artifacts/sensor-stack.tar.sha256"

say 'running QEMU arm64 static smoke checks'
qemu=${QEMU_AARCH64:-/usr/bin/qemu-aarch64-static}
[ -x "$qemu" ] || die 'qemu-aarch64-static is required for the host smoke gate'
# The smoke greps English output; a localized host locale (e.g. zh_CN) makes
# gettext binaries print translated usage text.  Pin the C locale.
ld_path=$dest/usr/lib/aarch64-linux-gnu:$rootfs/usr/lib/aarch64-linux-gnu:$rootfs/lib/aarch64-linux-gnu
version=$(
	LC_ALL=C LD_LIBRARY_PATH=$ld_path "$qemu" -L "$rootfs" "$dest/usr/bin/ssccli" --version
)
[ "$version" = 'libssc version 0.4.4' ] || die "unexpected ssccli version: $version"
LC_ALL=C LD_LIBRARY_PATH=$ld_path "$qemu" -L "$rootfs" "$dest/usr/bin/hexagonrpcd" 2>&1 |
	grep -q 'Usage:' || die 'hexagonrpcd QEMU smoke failed'
LC_ALL=C LD_LIBRARY_PATH=$ld_path "$qemu" -L "$rootfs" \
	"$dest/usr/local/libexec/liuqin-iio-sensor-proxy" --help 2>&1 |
	grep -q '^Usage:' || die 'iio-sensor-proxy QEMU execution smoke failed'

say "PASS: $out_dir/artifacts/sensor-stack.tar"
say "SHA256: $(cut -d' ' -f1 "$out_dir/artifacts/sensor-stack.tar.sha256")"
