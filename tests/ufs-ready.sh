#!/bin/sh
# Execute the real initramfs wait under BusyBox, with no /tmp in the fixture.
set -eu
project=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin" "$work/dev" "$work/sys"
bb=$(command -v busybox)
cat >"$work/bin/bb" <<'EOF'
#!/bin/sh
case $1 in
blockdev) echo "$*" >>"$WORK/calls"; [ "${FAIL_LOCK:-0}" = 0 ];;
mdev)
 n=$(cat "$WORK/scans"); n=$((n+1)); echo "$n" >"$WORK/scans"
 if [ "$n" = "${ARRIVE:-0}" ]; then : >"$WORK/dev/${ARRIVE_NODE:-sdf}"; fi
 ;;
sleep)
 n=$(cat "$WORK/sleeps"); n=$((n+1)); echo "$n" >"$WORK/sleeps"
 echo "$((n / 10)).$((n % 10)) 0" >"$WORK/uptime"
 ;;
*) exec "$HOST_BB" "$@";;
esac
EOF
chmod +x "$work/bin/bb"
node() { : >"$work/dev/$1"; mkdir -p "$work/sys/$1"; echo "PARTNAME=$2" >"$work/sys/$1/uevent"; }
reset() {
 rm -rf "$work/dev" "$work/sys"; mkdir "$work/dev" "$work/sys"
 for disk in sda sdb sdc sdd sde sdf; do : >"$work/dev/$disk"; done
 node sda21 persist; node sde14 boot_a; node sde43 boot_b
 ARRIVE=0; export ARRIVE
}
run() {
 echo 0 >"$work/scans"; echo 0 >"$work/sleeps"; echo '0.0 0' >"$work/uptime"; : >"$work/calls"
 WORK=$work HOST_BB=$bb LIUQIN_INIT_STORAGE_TEST_ONLY=1 \
 LIUQIN_INIT_STORAGE_TEST_PHASE=wait-ufs LIUQIN_INIT_TEST_PATH="$work/bin:/usr/bin:/bin" \
 LIUQIN_INIT_TEST_BUSYBOX="$work/bin/bb" LIUQIN_INIT_TEST_LOG="$work/log" \
 LIUQIN_INIT_TEST_UPTIME="$work/uptime" \
 LIUQIN_STORAGE_DEV_ROOT="$work/dev" LIUQIN_STORAGE_SYS_BLOCK="$work/sys" \
 "$bb" sh "$project/initramfs/init" >"$work/result"
}
reset; node sda35 userdata; run
[ "$(cat "$work/sleeps")" = 2 ]
reset; node sda35 userdata; node sda36 linux_root; node sda37 linux_home; run
[ "$(cat "$work/sleeps")" = 2 ]
# 512 GB fixture deliberately uses different numbers for every named partition.
reset; rm -rf "$work/dev" "$work/sys"; mkdir "$work/dev" "$work/sys"
for disk in sda sdb sdc sdd sde sdf; do : >"$work/dev/$disk"; done
node sda9 persist; node sde8 boot_a; node sde29 boot_b
node sda42 userdata; node sda45 linux_root; node sda46 linux_home; run
[ "$(cat "$work/sleeps")" = 2 ]
# Linux-only has no userdata at all.
reset; node sda36 linux_root; node sda37 linux_home; run
[ "$(cat "$work/sleeps")" = 2 ]
rm "$work/dev/sdf"; ARRIVE=4; export ARRIVE
run; [ "$(cat "$work/sleeps")" = 5 ]
# New partition invalidates stability too, even if required names already exist.
ARRIVE_NODE=sda99 ARRIVE=2; export ARRIVE_NODE ARRIVE
run; [ "$(cat "$work/sleeps")" = 3 ]
unset ARRIVE_NODE
ARRIVE=0; rm "$work/dev/sdf"
if run; then echo 'missing LUN accepted before timeout' >&2; exit 1; fi
[ "$(cat "$work/sleeps")" = 100 ]
[ "$(cat "$work/result")" = found_block=true ]
# Timeout without any block devices preserves the separate discovery fact.
rm -rf "$work/dev" "$work/sys"; mkdir "$work/dev" "$work/sys"
if run; then exit 1; fi
[ "$(cat "$work/result")" = found_block=false ]
echo 'PASS: stock, split, 512 GB numbering, Linux-only, late LUN, changing set, timeout discovery'
