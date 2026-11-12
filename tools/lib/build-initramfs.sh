#!/bin/sh

set -eu
umask 022

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
busybox=${BUSYBOX:-"$project_root/tools/local/busybox-arm64/usr/bin/busybox"}
# Explicitly select a stage-2 image, or ROOTFS=none for a native persistent root.
if [ -z "${ROOTFS+isset}" ]; then
	echo "error: ROOTFS is not set, and it has no default." >&2
	echo "       ROOTFS=out/rootfs/rootfs.squashfs" >&2
	echo "           the two-stage image: initramfs + Ubuntu Base in RAM" >&2
	echo "       ROOTFS=none" >&2
	echo "           no embedded stage-2 image" >&2
	exit 1
fi
rootfs=$ROOTFS
if [ "$rootfs" = none ]; then
	rootfs=
elif [ -z "$rootfs" ]; then
	echo "error: ROOTFS is set but empty, which is the trap this check exists" >&2
	echo "       for. Write ROOTFS=none to ask for the stage-1-only image." >&2
	exit 1
fi
# The touchscreen firmware has to be here and not only in the stage-2 rootfs.
# nvt_ts_probe() queues Boot_Update_Firmware with zero delay, so request_firmware()
# runs while the initramfs is still the root filesystem -- long before the
# squashfs is even attached to a loop device. The retry that would eventually
# find it in the rootfs is the ESD watchdog, and that only fires once the driver
# has decided the controller is alive, which it cannot do if the first download
# never happened. Half a megabyte against a 32 MB initramfs buys certainty.
touch_firmware_dir=${TOUCH_FIRMWARE_DIR:-"$project_root/tools/local/firmware-liuqin/touch-nt36532/vendor/firmware"}
touch_firmware_sha256="\
2582cf81d6eeb69b57cb8b18b2afe683e9cd7e20ed0be390e14ed75302b0c584  novatek_nt36532_m81_fw_csot.bin
3d9737da5e0fc3ea64e340c495067d4d6dcbd5ca2a2351fe955ec88fab35da1e  novatek_nt36532_m81_fw_tm.bin"
# The Bluetooth firmware is here for the same reason, arrived at the same way.
# hci_register_dev() queues hdev->power_on, and hci_power_on() calls
# hci_dev_open() straight away, which runs hdev->setup -- qca_setup() -- which
# is where the two request_firmware() calls live. Nothing waits for a user: by
# the time /init has finished counting block devices the requests have already
# been made and answered, or already failed. Ubuntu Base ships no BlueZ, so
# there is no second chance from userspace either: no hciconfig, no btmgmt,
# nothing that could re-open hci0 once the rootfs is up. One megabyte, and it
# is the difference between the driver having a chance and not.
#
# Selection rule and pinning are explained in tools/build-liuqin-ubuntu-rootfs.sh;
# the two scripts ship the identical set, from the identical directory, and both
# check it against the same digest.
bt_firmware_dir=${BT_FIRMWARE_DIR:-"$project_root/tools/local/firmware-liuqin/bt-qca6490/vendor/bt_firmware/image"}
bt_firmware_pattern='^(hpbtfw2[01]\.tlv|hpnv2[01]g?\.(bin|b[0-9a-f]+))$'
bt_firmware_set_sha256=d31712321a1c15591148a2e6a7ce94a12445558d2de82b48e5815abc581203a0
wlan_board_file=${WLAN_BOARD_FILE:-"$project_root/tools/local/firmware-liuqin/wlan-qca6490/vendor/firmware_mnt/image/qca6490/bd_m81.elf"}
wlan_board_sha256=aa8ae92d781e09db8cffa960b00c9606e4bb65d9c7a89f9b32df1bd24aee8573
wlan_firmware_dir=${WLAN_FIRMWARE_DIR:-"$project_root/tools/local/firmware-liuqin/wlan-qca6490/upstream/WCN6855/hw2.0"}
wlan_firmware_sha256="\
d94af8648a0347903b68809b4100ea816e76afb6ead967c8b353c6521500f285  amss.bin.zst
ba583c3550d15871dbbbe1349dab64bac5badced5b6bf122aa7263f8d7df76d4  board-2.bin.zst
7769d18f26c025008221702e6884c9225f375db46d3e584e20223e33728376ec  m3.bin.zst
c4b298869269bc55ca73d71beeb756b72a9b19e710cea2af0401accbafa2d3c7  regdb.bin.zst"
# The GNOME product image uses the complete HSP2-amss20 tuple that passed the
# device validation. ath11k probes before
# switch_root, so carrying this only in the stage-2 device layer would leave a
# cold boot on the old HSP1.1 tuple.  Keep all four protocol inputs atomic and
# put raw copies in firmware/updates for both hardware revision request paths.
wlan_hsp2_tuple=${WLAN_HSP2_TUPLE:-"$project_root/out/wlan"}
wlan_hsp2_amss_sha256=cc3e477fa698a28bdb8c8115a071893f9b2f5230de190ad525e74fd69bbb6092
wlan_hsp2_m3_sha256=6938b4bba268a02659ee5e16992971aa0e2fab103a4f60cbccf65e4bd8ac9836
wlan_hsp2_board2_sha256=15811f0b799fc26a881bce02e282834cd41b77c2812443ffd931012552a7c1f9
wlan_hsp2_regdb_sha256=06810e85c94c8f412c4e72cb38546cac90db7fdbadd45aae00f977f862d38a7b
audio_topology=${AUDIO_TOPOLOGY:-"$project_root/out/audio-topology/Xiaomi-Pad-6-Pro-tplg.bin"}
audio_topology_sha256=9c9f8bfcffde1f52c143d58cba821c5f6b3899553370ab92a88b90d81fef9ee9
regulatory_firmware_dir=${REGULATORY_FIRMWARE_DIR:-"$project_root/tools/local/firmware-liuqin/regulatory/upstream"}
regulatory_firmware_sha256="\
92eec693a0a9ec460be6bb3ff9e22b259b2f906eddd2984dd90654b5ecfa0f18  regulatory.db
4242d15defb0608b917a2c3cd0dbea21c026a4acc905093e8947dd494c2e26b1  regulatory.db.p7s"
gpu_firmware_dir=${GPU_FIRMWARE_DIR:-"$project_root/tools/local/firmware-liuqin/gpu-adreno730/vendor"}
gpu_firmware_sha256="\
e67d1829f57fc8326d806234b932dc3b78e28a9940df34c70752f17d5f38b7aa  a730_sqe.fw
36296b019753fbb2a4733450a2d0a94e307238227297a747d0ff2811364f5d5f  a730_zap.mbn
b2d3f0e2a98fd6259ab9ffef16df63265542e6cc9a970d53d476e823d432042a  gmu_gen70000.bin"
dsp_firmware_dir=${DSP_FIRMWARE_DIR:-"$project_root/tools/local/firmware-liuqin/dsp-sm8475/vendor/qcom/sm8475/liuqin"}
dsp_firmware_count=68
dsp_firmware_set_sha256=7f9b43d3815b6592f541e0b870357010488980bc040b925e93bbf12d01282908
slot_success_source="$project_root/device/boot/liuqin-mark-slot-successful.c"
slot_success_gcc=$(command -v "${SLOT_SUCCESS_GCC:-aarch64-linux-gnu-gcc}" || true)
charger_mode_source="$project_root/device/charger-mode/liuqin-charger-mode"
charger_key_source="$project_root/device/charger-mode/liuqin-charger-mode-power-key.c"
charger_exit_source="$project_root/device/charger-mode/liuqin-charger-mode-exit.c"
source_dir="$project_root/initramfs"
out_dir=${OUT_DIR:-"$project_root/out/initramfs-liuqin"}
source_epoch=${SOURCE_DATE_EPOCH:-0}
storage_mode=${LIUQIN_STORAGE_MODE:-readonly}
embed_rootfs=${LIUQIN_EMBED_ROOTFS:-1}
root_profile=${LIUQIN_ROOT_PROFILE:-legacy}
gnome_root_contract=${GNOME_ROOT_CONTRACT:-"$project_root/device/rootfs-artifacts/gnome-root.contract"}
native_root_contract=${NATIVE_ROOT_CONTRACT:-}
native_root_manifest=${NATIVE_ROOT_MANIFEST:-"$project_root/out/native-root/native-root.hashes"}
case $storage_mode in
readonly|persistent) ;;
*)
	echo "error: LIUQIN_STORAGE_MODE must be readonly or persistent, not: $storage_mode" >&2
	exit 1
	;;
esac
case $embed_rootfs in
0|1) ;;
*)
	echo "error: LIUQIN_EMBED_ROOTFS must be 0 or 1, not: $embed_rootfs" >&2
	exit 1
	;;
esac
if [ "$embed_rootfs" = 0 ] && [ "$storage_mode" != persistent ]; then
	echo "error: a rootfs-free initramfs is only valid in persistent mode" >&2
	exit 1
fi
case $root_profile in
legacy|gnome|native) ;;
*) echo "error: LIUQIN_ROOT_PROFILE must be legacy, gnome or native" >&2; exit 1 ;;
esac
if [ "$root_profile" != legacy ] && [ "$storage_mode" != persistent ]; then
	echo "error: the $root_profile root profile requires persistent mode" >&2
	exit 1
fi
charger_mode=${LIUQIN_CHARGER_MODE:-auto}
case $charger_mode in
auto)
	if [ "$storage_mode" = persistent ] && [ "$root_profile" != legacy ]; then
		charger_mode=1
	else
		charger_mode=0
	fi
	;;
0|1) ;;
*) echo "error: LIUQIN_CHARGER_MODE must be auto, 0 or 1" >&2; exit 1 ;;
esac
# The explicit installer branch switches the final image to read-only RAM mode.
if [ "$root_profile" != legacy ] && [ "$charger_mode" != 1 ] && [ -z "${INSTALLER_RUNTIME:-}" ]; then
	echo "error: a persistent GNOME/native image must include charger-mode hold" >&2
	exit 1
fi
if [ "$root_profile" != legacy ]; then
	[ -d "$wlan_hsp2_tuple/hw2.0" ] && [ -d "$wlan_hsp2_tuple/hw2.1" ] || {
		echo "error: the GNOME image requires the reviewed HSP2 WLAN tuple: $wlan_hsp2_tuple" >&2
		exit 1
	}
	for wlan_item in \
		"amss.bin:$wlan_hsp2_amss_sha256" \
		"m3.bin:$wlan_hsp2_m3_sha256" \
		"board-2.bin:$wlan_hsp2_board2_sha256" \
		"regdb.bin:$wlan_hsp2_regdb_sha256"; do
		wlan_name=${wlan_item%%:*}
		wlan_hash=${wlan_item#*:}
		for wlan_revision in hw2.0 hw2.1; do
			[ -f "$wlan_hsp2_tuple/$wlan_revision/$wlan_name" ] || {
				echo "error: HSP2 WLAN tuple lacks $wlan_revision/$wlan_name" >&2
				exit 1
			}
			[ "$(sha256sum "$wlan_hsp2_tuple/$wlan_revision/$wlan_name" | cut -d' ' -f1)" = "$wlan_hash" ] || {
				echo "error: HSP2 WLAN tuple hash mismatch: $wlan_revision/$wlan_name" >&2
				exit 1
			}
		done
	done
	[ -f "$audio_topology" ] && [ ! -L "$audio_topology" ] && \
		[ "$(sha256sum "$audio_topology" | cut -d' ' -f1)" = "$audio_topology_sha256" ] || {
		echo "error: AudioReach topology identity mismatch: $audio_topology" >&2
		exit 1
	}
fi
if [ ! -x "$busybox" ]; then
	echo "error: static ARM64 BusyBox is unavailable: $busybox" >&2
	exit 1
fi

if ! readelf -h "$busybox" | grep -q 'Machine:.*AArch64'; then
	echo "error: BusyBox is not an AArch64 executable: $busybox" >&2
	exit 1
fi

if readelf -l "$busybox" | grep -q 'INTERP'; then
	echo "error: BusyBox is dynamically linked: $busybox" >&2
	exit 1
fi

if ! (cd "$touch_firmware_dir" && printf '%s\n' "$touch_firmware_sha256" | sha256sum -c --quiet -); then
	echo "error: touchscreen firmware does not match the pinned hashes: $touch_firmware_dir" >&2
	echo "       see tools/local/firmware-liuqin/MANIFEST.sha256" >&2
	exit 1
fi

if [ ! -d "$bt_firmware_dir" ]; then
	echo "error: Bluetooth firmware directory is unavailable: $bt_firmware_dir" >&2
	echo "       provide the firmware files matching the expected hashes" >&2
	exit 1
fi
bt_firmware_names=$(
	cd "$bt_firmware_dir" && ls -1 | LC_ALL=C sort | grep -E "$bt_firmware_pattern"
)
bt_firmware_seen=$(
	cd "$bt_firmware_dir" && printf '%s\n' "$bt_firmware_names" | \
		xargs sha256sum | sha256sum | cut -d' ' -f1
)
if [ "$bt_firmware_seen" != "$bt_firmware_set_sha256" ]; then
	echo "error: the Bluetooth firmware set does not match its pinned digest" >&2
	echo "       directory: $bt_firmware_dir" >&2
	echo "       expected:  $bt_firmware_set_sha256" >&2
	echo "       found:     $bt_firmware_seen ($(printf '%s\n' "$bt_firmware_names" | wc -l) files)" >&2
	echo "       see tools/local/firmware-liuqin/MANIFEST.sha256" >&2
	exit 1
fi

if ! printf '%s  %s\n' "$wlan_board_sha256" "$wlan_board_file" | sha256sum -c --quiet -; then
	echo "error: WLAN board data does not match the pinned hash: $wlan_board_file" >&2
	exit 1
fi
if ! (cd "$wlan_firmware_dir" && printf '%s\n' "$wlan_firmware_sha256" | sha256sum -c --quiet -); then
	echo "error: upstream WCN6855 firmware does not match the pinned hashes: $wlan_firmware_dir" >&2
	exit 1
fi
if ! (cd "$regulatory_firmware_dir" && printf '%s\n' "$regulatory_firmware_sha256" | sha256sum -c --quiet -); then
	echo "error: wireless regulatory database does not match the pinned hashes: $regulatory_firmware_dir" >&2
	exit 1
fi
if ! (cd "$gpu_firmware_dir" && printf '%s\n' "$gpu_firmware_sha256" | sha256sum -c --quiet -); then
	echo "error: Adreno 730 firmware does not match the pinned hashes: $gpu_firmware_dir" >&2
	exit 1
fi
dsp_firmware_seen=$(
	cd "$dsp_firmware_dir" && find . -maxdepth 1 -type f -printf '%P\n' | \
		LC_ALL=C sort | xargs sha256sum | sha256sum | cut -d' ' -f1
)
if [ "$(find "$dsp_firmware_dir" -maxdepth 1 -type f | wc -l)" -ne "$dsp_firmware_count" ] || \
	[ "$dsp_firmware_seen" != "$dsp_firmware_set_sha256" ]; then
	echo "error: SM8475 DSP firmware set does not match its pinned membership and digest" >&2
	exit 1
fi

if [ -n "$rootfs" ] && [ ! -r "$rootfs" ]; then
	echo "error: stage-2 root filesystem is unavailable: $rootfs" >&2
	echo "       run tools/build-liuqin-ubuntu-rootfs.sh, or set ROOTFS=none to" >&2
	echo "       build the stage-1-only diagnostic initramfs." >&2
	exit 1
fi

# A persistent initramfs must prove the exact stage-2 storage guard it will
# hand execution to.  The rootfs builder emits this sidecar with the squashfs;
# it binds the supplied squashfs and the complete PID 1 chain.  Stage 1 keeps
# the whole contract outside LIUQIN_ROOT, so no mutable root bytes can alter a
# hash or link target before validation.
storage_root_contract=
if [ "$storage_mode" = persistent ]; then
	if [ "$root_profile" != native ]; then
		if [ -z "$rootfs" ]; then
			echo "error: persistent mode requires a stage-2 root filesystem, not ROOTFS=none" >&2
			exit 1
		fi
		storage_rcs_contract="$rootfs.persistent-rcS.contract"
		if [ ! -f "$storage_rcs_contract" ] || [ -L "$storage_rcs_contract" ] ||
			[ "$(stat -c '%a' "$storage_rcs_contract" 2>/dev/null || true)" != 644 ]; then
			echo "error: persistent rootfs contract is missing or has an unsafe mode: $storage_rcs_contract" >&2
			exit 1
		fi
		storage_rootfs_sha256=$(sha256sum "$rootfs" | cut -d' ' -f1)
		if ! awk -v rootfs_sha256="$storage_rootfs_sha256" '
			NR == 1 { valid = ($0 == "LIUQIN_PERSISTENT_ROOT_CONTRACT_V2") }
			NR == 2 { valid = valid && ($0 == "rootfs_sha256=" rootfs_sha256) }
			NR == 3 { valid = valid && ($0 ~ /^rcs_sha256=[0-9a-f]{64}$/) }
			NR == 4 { valid = valid && ($0 ~ /^inittab_sha256=[0-9a-f]{64}$/) }
			NR == 5 { valid = valid && ($0 ~ /^busybox_sha256=[0-9a-f]{64}$/) }
			NR == 6 { valid = valid && ($0 == "sbin_link=usr/sbin") }
			NR == 7 { valid = valid && ($0 == "usr_sbin_init_link=/usr/local/bin/busybox") }
			NR == 8 { valid = valid && ($0 == "rcs_interpreter=/usr/local/bin/busybox sh") }
			END { exit !(NR == 8 && valid) }
		' "$storage_rcs_contract"; then
			echo "error: persistent rootfs contract does not match the selected rootfs: $storage_rcs_contract" >&2
			exit 1
		fi
		storage_root_contract=$storage_rcs_contract
	fi
	if [ "$root_profile" = native ]; then
		[ -n "$native_root_contract" ] || {
			echo "error: the native profile requires NATIVE_ROOT_CONTRACT (tools/lib/build-root-contract.py --profile native)" >&2
			exit 1
		}
		if [ ! -f "$native_root_contract" ] || [ -L "$native_root_contract" ] ||
			[ "$(stat -c '%a' "$native_root_contract" 2>/dev/null || true)" != 644 ] ||
			[ "$(sed -n '1p' "$native_root_contract")" != LIUQIN_NATIVE_ROOT_CONTRACT_V1 ]; then
			echo "error: native root contract is unavailable or invalid: $native_root_contract" >&2
			exit 1
		fi
		if [ ! -f "$native_root_manifest" ] || [ -L "$native_root_manifest" ]; then
			echo "error: native root hash manifest is unavailable: $native_root_manifest (run build-liuqin-native-root.sh)" >&2
			exit 1
		fi
		# Same relation as the GNOME check below, against the assembled native
		# root's hash list: every contract pin must be in the tree at the same
		# hash, so the image and the root can never describe different trees.
		tail -n +2 "$native_root_contract" | while IFS= read -r contract_line; do
			[ -n "$contract_line" ] || continue
			contract_hash=${contract_line%%  *}
			contract_path=${contract_line#*  }
			# Exact field comparison: hash is 64 chars, two spaces, then the
			# path.  An index()-based suffix match false-matches any line one
			# character shorter than the needle (index 0 equals length diff 0),
			# which the 234k-entry native manifest actually contains.
			tree_hash=$(awk -v p="$contract_path" \
				'{ h=substr($0,1,64); if (substr($0,67) == p) { print h; exit } }' \
				"$native_root_manifest")
			if [ -z "$tree_hash" ]; then
				echo "error: the native contract pins a path the assembled root does not carry: $contract_path" >&2
				exit 1
			fi
			if [ "$tree_hash" != "$contract_hash" ]; then
				echo "error: the native contract disagrees with the assembled root on $contract_path" >&2
				exit 1
			fi
		done
		native_contract_pins=$(tail -n +2 "$native_root_contract" | grep -c . || :)
		if [ "$native_contract_pins" -lt 9 ]; then
			echo "error: native root contract pins only $native_contract_pins files;" >&2
			echo "       stage 1 rejects a contract shorter than 10 lines" >&2
			exit 1
		fi
	fi
	if [ "$root_profile" = gnome ]; then
		if [ ! -f "$gnome_root_contract" ] || [ -L "$gnome_root_contract" ] ||
			[ "$(stat -c '%a' "$gnome_root_contract" 2>/dev/null || true)" != 644 ] ||
			[ "$(sed -n '1p' "$gnome_root_contract")" != LIUQIN_GNOME_ROOT_CONTRACT_V1 ]; then
			echo "error: GNOME root contract is unavailable or invalid: $gnome_root_contract" >&2
			exit 1
		fi
		# Only once the contract is known to exist and be well formed. The
		# contract is generated from the device layer's manifest, so a layer
		# rebuilt afterwards leaves the two describing different trees; stage 1
		# then rejects a root that is perfectly fine and falls back, which on a
		# console reads like a boot failure. It cost a QEMU round here after a
		# dconf change was followed by regenerating the contract and nothing
		# else. Ordering is load-bearing: layer, contract, initramfs, boot.
		gnome_layer_manifest=${GNOME_LAYER_MANIFEST:-"$project_root/out/gnome-device-layer/device-layer.manifest"}
		if [ ! -f "$gnome_layer_manifest" ] || [ -L "$gnome_layer_manifest" ]; then
			echo "error: GNOME device-layer manifest is unavailable: $gnome_layer_manifest" >&2
			exit 1
		fi
		# Every path the contract pins must be in the layer at the same hash.
		#
		# Driven from the contract itself. This used to be a second, hand-kept
		# copy of the pin list, and a second copy is a second place to forget:
		# contract v2 stopped pinning the dconf pair, gdm3/custom.conf and the
		# two GPU blobs at their old dpkg-owned path, and the hardcoded list
		# went on demanding all five -- so the build failed on the very change
		# that fixed the boot bug, complaining about paths that were removed on
		# purpose. Iterating over the contract states the relation that is
		# actually wanted (contract is a subset of the layer, hashes agree)
		# without naming a single file, so the next scope change needs no edit
		# here at all.
		#
		# The `exit 1` below is inside a pipeline, so it leaves a subshell
		# rather than the script; `set -eu` at the top of this file is what
		# turns the pipeline's non-zero status into the build stopping. That is
		# the pre-existing shape of this check and it does work -- noted
		# because it does not look like it does.
		tail -n +2 "$gnome_root_contract" | while IFS= read -r contract_line; do
			[ -n "$contract_line" ] || continue
			contract_hash=${contract_line%%  *}
			contract_path=${contract_line#*  }
			# Exact field comparison (hash is 64 chars, two spaces, then the
			# path).  The previous index()-based suffix match false-matched a
			# line one character shorter than the needle: index() then yields 0
			# and the length difference is also 0.
			layer_hash=$(awk -v p="$contract_path" \
				'{ h=substr($0,1,64); if (substr($0,67) == p) { print h; exit } }' \
				"$gnome_layer_manifest")
			if [ -z "$layer_hash" ]; then
				echo "error: the GNOME contract pins a path the device layer does not carry: $contract_path" >&2
				exit 1
			fi
			if [ "$layer_hash" != "$contract_hash" ]; then
				echo "error: the GNOME contract disagrees with the device layer on $contract_path" >&2
				echo "       contract $contract_hash" >&2
				echo "       layer    $layer_hash" >&2
				echo "       regenerate the contract from the current layer manifest" >&2
				exit 1
			fi
		done
		# A contract that pins nothing satisfies every comparison above. Stage 1
		# refuses a contract shorter than ten lines including the header
		# (initramfs/init:238), so hold the same floor here rather than ship an
		# image whose own contract the tablet will reject. `grep -c` exits 1 on
		# a count of zero, which under set -e would abort with no message.
		gnome_contract_pins=$(tail -n +2 "$gnome_root_contract" | grep -c . || :)
		if [ "$gnome_contract_pins" -lt 9 ]; then
			echo "error: GNOME root contract pins only $gnome_contract_pins files;" >&2
			echo "       stage 1 rejects a contract shorter than 10 lines" >&2
			exit 1
		fi
	fi
	if [ ! -r "$slot_success_source" ] || [ ! -x "$slot_success_gcc" ]; then
		echo "error: persistent slot-success source/toolchain is unavailable" >&2
		exit 1
	fi
fi
# Naming the file explicitly stops the build from picking a stale rootfs on its
# own; it does not stop a stale path from being copied out of an old shell
# history. So say when a newer one exists. A warning and not an error, because
# rebuilding an older image on purpose is a legitimate thing to want -- the
# failure being guarded against is the one where nobody was thinking about it.
if [ -n "$rootfs" ]; then
	find "$project_root/out" -maxdepth 2 -name rootfs.squashfs -newer "$rootfs" \
		2>/dev/null | LC_ALL=C sort | while read -r newer_rootfs; do
		echo "warning: a newer stage-2 root filesystem exists: $newer_rootfs" >&2
	done
	echo "stage 2: $rootfs" >&2
fi

mkdir -p "$out_dir"
# The cpio below runs inside `cd "$staging"`, so every path it is handed has to
# be absolute. The built-in default already is; a relative OUT_DIR= on the
# command line was not, and failed there with a message about a file it had
# created moments earlier.
out_dir=$(CDPATH= cd -- "$out_dir" && pwd)
archive="$out_dir/liuqin-firstboot.cpio.gz"
raw_archive="$out_dir/.liuqin-firstboot.cpio.$$"
staging=$(mktemp -d "$out_dir/.staging.XXXXXX")
cleanup() {
	rm -f "$raw_archive" "$raw_archive.gz"
	rm -rf "$staging"
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$staging/bin" "$staging/sbin" "$staging/etc" "$staging/proc" \
	"$staging/sys" "$staging/dev" "$staging/run" "$staging/usr/bin" \
	"$staging/usr/sbin"
cp "$busybox" "$staging/bin/busybox"
cp "$source_dir/init" "$staging/init"
# Persistent roots need the distro checker before their first writable mount.
# UBUNTU_DESKTOP_ROOT is already a fixed input of build-liuqin-image.py.
if [ "$storage_mode" = persistent ]; then
	fsck_root=${ROOT_FSCK_ROOT:-${UBUNTU_DESKTOP_ROOT:-}}
	[ -n "$fsck_root" ] || { echo 'set ROOT_FSCK_ROOT to the verified Ubuntu ARM64 root' >&2; exit 1; }
	set --
	[ "$charger_mode" != 1 ] || set -- --library libdrm.so.2
	python3 "$project_root/tools/lib/build-root-fsck.py" --root "$fsck_root" --out "$staging/fsck-runtime" "$@"
	cp "$staging/fsck-runtime/root-fsck-files.json" "$out_dir/root-fsck-files.json"
	cp -a "$staging/fsck-runtime"/. "$staging"/
	rm -rf "$staging/fsck-runtime" "$staging/root-fsck-files.json"
	install -m 0755 "$source_dir/root-fsck" "$staging/bin/root-fsck"
fi
install -m 0755 "$project_root/device/platform/liuqin-physical-platform" "$staging/bin/liuqin-physical-platform"
cp "$source_dir/etc/issue" "$staging/etc/issue"
cp "$source_dir/etc/mdev.conf" "$staging/etc/mdev.conf"
chmod 0644 "$staging/etc/mdev.conf"
chmod 0755 "$staging/init" "$staging/bin/busybox"
printf '%s\n' "$storage_mode" >"$staging/etc/liuqin-storage-mode"
chmod 0644 "$staging/etc/liuqin-storage-mode"
if [ "$root_profile" = gnome ]; then
	printf '%s\n' gnome >"$staging/etc/liuqin-root-profile"
	chmod 0644 "$staging/etc/liuqin-root-profile"
fi
if [ "$root_profile" = native ]; then
	printf '%s\n' native >"$staging/etc/liuqin-root-profile"
	chmod 0644 "$staging/etc/liuqin-root-profile"
fi
if [ "$storage_mode" = persistent ]; then
	if [ -n "$storage_root_contract" ]; then
		cp "$storage_root_contract" "$staging/etc/liuqin-persistent-root.contract"
		chmod 0644 "$staging/etc/liuqin-persistent-root.contract"
	fi
	if [ "$root_profile" = gnome ]; then
		cp "$gnome_root_contract" "$staging/etc/liuqin-gnome-root.contract"
		chmod 0644 "$staging/etc/liuqin-gnome-root.contract"
	fi
	if [ "$root_profile" = native ]; then
		cp "$native_root_contract" "$staging/etc/liuqin-native-root.contract"
		chmod 0644 "$staging/etc/liuqin-native-root.contract"
	fi
	"$slot_success_gcc" -static -Os -s "$slot_success_source" \
		-o "$staging/bin/liuqin-mark-slot-successful"
	chmod 0755 "$staging/bin/liuqin-mark-slot-successful"
	if ! readelf -h "$staging/bin/liuqin-mark-slot-successful" | grep -q 'Machine:.*AArch64' ||
		readelf -l "$staging/bin/liuqin-mark-slot-successful" | grep -q INTERP; then
		echo "error: slot-success helper is not a static AArch64 ELF" >&2
		exit 1
	fi
fi
if [ "$charger_mode" = 1 ]; then
	# No module loader or persistent root is available in charger mode. The
	# enabled DP component also gates the shared DSI DRM master through FSA4480.
	charger_config=${KERNEL_OUT:?set KERNEL_OUT for the charging runtime}/.config
	for symbol in DRM_MSM DRM_MSM_DSI DRM_PANEL_NOVATEK_NT36532 \
		SM_DISPCC_8450 TYPEC_MUX_FSA4480 QCOM_PMIC_GLINK DRM_AUX_HPD_BRIDGE \
		BACKLIGHT_KTZ8866 USB_CONFIGFS USB_CONFIGFS_NCM; do
		grep -qx "CONFIG_$symbol=y" "$charger_config" || {
			echo "error: charger root dependency CONFIG_$symbol must be built in" >&2; exit 1;
		}
	done
	printf '%s\n' hold-v1 >"$staging/etc/liuqin-charger-mode"
	chmod 0644 "$staging/etc/liuqin-charger-mode"
	cp "$charger_mode_source" "$staging/bin/liuqin-charger-mode"
	chmod 0755 "$staging/bin/liuqin-charger-mode"
	cp "$project_root/device/charger-mode/liuqin-charger-usb" "$staging/bin/liuqin-charger-usb"
	chmod 0755 "$staging/bin/liuqin-charger-usb"
	for charger_helper in key exit; do
		case $charger_helper in
		key) charger_source=$charger_key_source; charger_output=liuqin-charger-mode-power-key ;;
		exit) charger_source=$charger_exit_source; charger_output=liuqin-charger-mode-exit ;;
		esac
		"$slot_success_gcc" -static -Os -s -Wall -Wextra -Werror \
			"$charger_source" -o "$staging/bin/$charger_output"
		chmod 0755 "$staging/bin/$charger_output"
		if ! readelf -h "$staging/bin/$charger_output" | grep -q 'Machine:.*AArch64' ||
			readelf -l "$staging/bin/$charger_output" | grep -q INTERP; then
			echo "error: $charger_output is not a static AArch64 ELF" >&2
			exit 1
		fi
	done
	# The distro DRM ABI and runtime are bundled from the same pinned ARM64
	# root as e2fsck. The status renderer owns a standard dumb-buffer scanout.
	[ -n "${fsck_root:-}" ] || { echo 'charger display requires the fixed Ubuntu root' >&2; exit 1; }
	"$slot_success_gcc" -Os -s -Wall -Wextra -Werror -I/usr/include/libdrm \
		"$project_root/device/charger-mode/liuqin-charger-screen.c" \
		"$fsck_root/usr/lib/aarch64-linux-gnu/libdrm.so.2" \
		-Wl,-rpath-link,"$fsck_root/usr/lib/aarch64-linux-gnu" \
		-o "$staging/bin/liuqin-charger-screen"
	readelf -h "$staging/bin/liuqin-charger-screen" | grep -q 'Machine:.*AArch64' || exit 1
fi

# Use the installer's existing RESTART2 helper for product recovery too.
if [ -x "$staging/bin/liuqin-charger-mode-exit" ]; then
	ln -s liuqin-charger-mode-exit "$staging/bin/liuqin-reboot"
else
	"$slot_success_gcc" -static -Os -s -Wall -Wextra -Werror \
		"$charger_exit_source" -o "$staging/bin/liuqin-reboot"
fi

mkdir -p "$staging/lib/firmware/novatek/liuqin"
printf '%s\n' "$touch_firmware_sha256" | while read -r _ firmware_name; do
	cp "$touch_firmware_dir/$firmware_name" \
		"$staging/lib/firmware/novatek/liuqin/$firmware_name"
	chmod 0644 "$staging/lib/firmware/novatek/liuqin/$firmware_name"
done

# btqca.c hardcodes the qca/ prefix into every name it builds, so this directory
# is not a choice.
mkdir -p "$staging/lib/firmware/qca"
printf '%s\n' "$bt_firmware_names" | while read -r firmware_name; do
	cp "$bt_firmware_dir/$firmware_name" "$staging/lib/firmware/qca/$firmware_name"
	chmod 0644 "$staging/lib/firmware/qca/$firmware_name"
done

# ath11k probes and boots MHI before /init can switch_root, so WLAN firmware is
# just as stage-1-critical as the BT blobs above. Keep one physical compressed
# copy under hw2.0 and symlink hw2.1 to it, matching upstream linux-firmware.
for hw_revision in hw2.0 hw2.1; do
	mkdir -p "$staging/lib/firmware/ath11k/WCN6855/$hw_revision"
	cp "$wlan_board_file" \
		"$staging/lib/firmware/ath11k/WCN6855/$hw_revision/board.bin"
	chmod 0644 "$staging/lib/firmware/ath11k/WCN6855/$hw_revision/board.bin"
done
printf '%s\n' "$wlan_firmware_sha256" | while read -r _ firmware_name; do
	cp "$wlan_firmware_dir/$firmware_name" \
		"$staging/lib/firmware/ath11k/WCN6855/hw2.0/$firmware_name"
	chmod 0644 "$staging/lib/firmware/ath11k/WCN6855/hw2.0/$firmware_name"
	ln -s "../hw2.0/$firmware_name" \
		"$staging/lib/firmware/ath11k/WCN6855/hw2.1/$firmware_name"
done

if [ "$root_profile" = gnome ] || [ "$root_profile" = native ]; then
	for wlan_revision in hw2.0 hw2.1; do
		for wlan_name in amss.bin m3.bin board-2.bin regdb.bin; do
			install -D -m 0644 "$wlan_hsp2_tuple/$wlan_revision/$wlan_name" \
				"$staging/lib/firmware/updates/ath11k/WCN6855/$wlan_revision/$wlan_name"
		done
	done
	install -D -m 0644 "$audio_topology" \
		"$staging/lib/firmware/qcom/sm8450/Xiaomi-Pad-6-Pro-tplg.bin"
fi

# cfg80211 is built in and requests its signed regulatory database while still
# in stage 1. The kernel configuration requires the matching PKCS#7 signature,
# so keep the pair together and pin both bytes.
printf '%s\n' "$regulatory_firmware_sha256" | while read -r _ firmware_name; do
	cp "$regulatory_firmware_dir/$firmware_name" "$staging/lib/firmware/$firmware_name"
	chmod 0644 "$staging/lib/firmware/$firmware_name"
done

# DRM/GPU and all three PAS remoteprocs probe before /init can switch_root.
# Mirror the exact stage-2 layout here so neither side depends on probe timing.
mkdir -p "$staging/lib/firmware/qcom/sm8475/liuqin"
cp "$gpu_firmware_dir/a730_zap.mbn" \
	"$staging/lib/firmware/qcom/sm8475/liuqin/a730_zap.mbn"
cp "$gpu_firmware_dir/a730_sqe.fw" "$gpu_firmware_dir/gmu_gen70000.bin" \
	"$staging/lib/firmware/qcom/"
cp "$dsp_firmware_dir"/* "$staging/lib/firmware/qcom/sm8475/liuqin/"

if [ -n "$rootfs" ] && [ "$embed_rootfs" = 1 ]; then
	mkdir -p "$staging/mnt/lower" "$staging/mnt/rw" "$staging/newroot"
	cp "$rootfs" "$staging/rootfs.squashfs"
	chmod 0644 "$staging/rootfs.squashfs"
fi

# `ln` and `udhcpd` were used by the init script long before they were linked
# here: the Ubuntu BusyBox is built with the standalone shell, so its own `sh`
# dispatches applets it cannot find in PATH. That is a property of one vendor's
# build, not of BusyBox, and nothing outside `sh` gets it -- so link every applet
# the init script names.
for applet in awk grep od tr basename blockdev cat chmod chroot cp cttyhack cut dmesg findfs \
	hostname ip kill killall ln losetup ls mkdir mount poweroff readlink reboot \
	setsid sh sha256sum sleep stat switch_root sync telnetd udhcpd umount; do
	ln -s busybox "$staging/bin/$applet"
done
ln -s ../bin/busybox "$staging/sbin/mdev"

if [ -n "${INSTALLER_RUNTIME:-}" ]; then
	[ -x "$INSTALLER_RUNTIME/usr/bin/tar" ] && [ -x "$INSTALLER_RUNTIME/usr/sbin/mkfs.ext4" ] &&
		[ -x "$INSTALLER_RUNTIME/usr/sbin/e2fsck" ] && [ -x "$INSTALLER_RUNTIME/usr/sbin/sgdisk" ] ||
		{ echo 'installer runtime is incomplete' >&2; exit 1; }
	cp -a "$INSTALLER_RUNTIME"/. "$staging"/
	mkdir -p "$staging/usr/lib/liuqin"
	cp "$project_root/tools/lib/install-preflight.sh" "$staging/usr/lib/liuqin/install-preflight.sh"
	cp "$project_root/tools/lib/install-devmode.sh" "$staging/usr/lib/liuqin/install-devmode.sh"
	cp "$project_root/tools/lib/install-keep-home.sh" "$project_root/tools/lib/install-keep-home.awk" "$staging/usr/lib/liuqin/"
	cp "$project_root/tools/lib/install-root.sh" "$staging/usr/lib/liuqin/install-root.sh"
	cp "$project_root/tools/lib/install-layout.sh" "$staging/usr/lib/liuqin/install-layout.sh"
	cp "$project_root/tools/lib/install-switch-store.sh" "$staging/usr/lib/liuqin/install-switch-store.sh"
	cp "$project_root/tools/provision-liuqin-from-persist.sh" "$staging/usr/lib/liuqin/provision.sh"
	printf 'readonly\n' >"$staging/etc/liuqin-storage-mode"
	printf 'liuqin\n' >"$staging/etc/liuqin-installer"
	chmod 0644 "$staging/etc/liuqin-storage-mode" "$staging/etc/liuqin-installer"
	# GNU tar invokes gzip externally; do not depend on ash's applet dispatch.
	ln -s busybox "$staging/bin/gzip"
fi

find "$staging" -exec touch -h -d "@$source_epoch" {} +

(
	cd "$staging"
	find . -print0 | LC_ALL=C sort -z | \
		cpio --null --create --format=newc --owner=0:0 --reproducible \
			--file="$raw_archive"
)
gzip -n -9 "$raw_archive"
mv "$raw_archive.gz" "$archive"

if [ -n "$rootfs" ]; then
	sha256sum "$archive" "$busybox" "$rootfs"
else
	sha256sum "$archive" "$busybox"
fi
printf 'bluetooth firmware: %s files, set sha256 %s\n' \
	"$(printf '%s\n' "$bt_firmware_names" | wc -l)" "$bt_firmware_set_sha256"
printf 'initramfs: %s bytes\n' "$(stat -c %s "$archive")"
printf 'initramfs: %s\n' "$archive"
printf 'storage mode: %s\n' "$storage_mode"
printf 'embedded rootfs: %s\n' "$embed_rootfs"
printf 'root profile: %s\n' "$root_profile"
printf 'charger mode: %s\n' "$charger_mode"
