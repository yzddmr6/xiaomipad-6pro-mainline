#!/bin/sh
# Execute the real initramfs wait under BusyBox; synthetic sysfs has no /tmp.
set -eu
project=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin" "$work/dev" "$work/sys" "$work/state"
bb=$(command -v busybox)
cat >"$work/bin/bb" <<'EOF'
#!/bin/sh
if [ "$1" = blockdev ]; then
 echo "$*" >>"$WORK/calls"
 [ "${FAIL_LOCK:-0}" = 0 ]
elif [ "$1" = mdev ] || [ "$1" = sleep ]; then
 name=$1; shift; exec "$WORK/bin/$name" "$@"
else
 exec "$HOST_BB" "$@"
fi
EOF
cat >"$work/bin/mdev" <<'EOF'
#!/bin/sh
n=$(cat "$WORK/scans"); n=$((n+1)); echo "$n" >"$WORK/scans"
if [ "$n" = "${ARRIVE:-0}" ]; then
 : >"$WORK/dev/sdf7"
fi
EOF
cat >"$work/bin/sleep" <<'EOF'
#!/bin/sh
echo "$1" >>"$WORK/sleeps"
EOF
chmod +x "$work/bin/"*
for node in sda sdb sdc sdd sde sdf sda34 sda35 sdb6 sdc6 sdd6 sde77 sdf7; do
 : >"$work/dev/$node"
 mkdir -p "$work/sys/$node"
 echo '8:1' >"$work/sys/$node/dev"
 case $node in *[0-9]) echo 1 >"$work/sys/$node/partition";; esac
done
echo PARTNAME=userdata >"$work/sys/sda35/uevent"
run() {
 echo 0 >"$work/scans"; : >"$work/calls"; : >"$work/sleeps"
 WORK=$work HOST_BB=$bb LIUQIN_INIT_STORAGE_TEST_ONLY=1 \
 LIUQIN_INIT_STORAGE_TEST_PHASE=wait-ufs LIUQIN_INIT_TEST_PATH="$work/bin:/usr/bin:/bin" \
 LIUQIN_INIT_TEST_BUSYBOX="$work/bin/bb" LIUQIN_INIT_TEST_LOG="$work/log" \
 LIUQIN_STORAGE_DEV_ROOT="$work/dev" LIUQIN_STORAGE_SYS_BLOCK="$work/sys" \
 LIUQIN_STORAGE_TEST_REGULAR_FILES=1 "$bb" sh "$project/initramfs/init"
}
run
[ ! -s "$work/sleeps" ]
rm "$work/dev/sdf7"
ARRIVE=4; export ARRIVE
run
[ "$(wc -l <"$work/sleeps")" = 3 ]
rm "$work/dev/sdf7"
ARRIVE=0
if run; then echo 'missing partition accepted' >&2; exit 1; fi
[ "$(wc -l <"$work/sleeps")" = 100 ]
: >"$work/dev/sdf7"
FAIL_LOCK=1; export FAIL_LOCK
if run; then echo 'failed read-only guard accepted' >&2; exit 1; fi
unset FAIL_LOCK
rm "$work/dev/sda35"
for num in 36 37; do
 : >"$work/dev/sda$num"; mkdir -p "$work/sys/sda$num"
done
rm -r "$work/sys/sda35"
echo PARTNAME=linux_root >"$work/sys/sda36/uevent"
echo PARTNAME=linux_home >"$work/sys/sda37/uevent"
run
[ ! -s "$work/sleeps" ]
echo 'PASS: immediate readiness, delayed partition, timeout, lock failure and split layout'
