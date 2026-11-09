#!/bin/sh
# SPDX-License-Identifier: MIT
# No /tmp, Python or target-root programs: runs in the BusyBox RAM installer.
set -eu
umask 077
bb=/bin/busybox
rules=${0%/*}/install-keep-home.awk
mode=$1
state=$2
root=$3
say() { printf 'liuqin-install: KEEP-HOME: %s\n' "$*"; }
die() { say "$*" >&2; exit 1; }
# Reject symlinks/special files (including parent components), never follow an
# old-root link into the live installer. Missing optional directories are OK.
safe_path() {
 p=$1
 while [ "$p" != / ]; do [ ! -L "$p" ] || return 1; p=${p%/*}; [ -n "$p" ] || p=/; done
 [ ! -e "$1" ] || [ -z "$("$bb" find "$1" ! -type f ! -type d -print)" ]
}
state_kib=0
budget() {
 [ -e "$1" ] || return 0
 amount=$("$bb" du -sk "$1" | "$bb" cut -f1)
 state_kib=$((state_kib + amount))
 [ "$state_kib" -le 65536 ] || die 'old state exceeds 64 MiB; first setup required'
}
accounts() {
 for f in passwd shadow group gshadow; do
  safe_path "$1/etc/$f" && [ -s "$1/etc/$f" ] || return 1
 done
}
run_awk() {
 "$bb" awk -v mode="$1" -v old="$2" -v out="$3" -f "$rules" \
  "$2/passwd" "$2/shadow" "$2/group" "$2/gshadow" ${4:+"$4/passwd"} ${4:+"$4/shadow"} ${4:+"$4/group"} ${4:+"$4/gshadow"}
}
case $mode in
snapshot)
 "$bb" mkdir -p "$state"
 "$bb" chmod 0700 "$state"
 accounts "$root" || die 'old account databases unavailable; first setup required'
 for f in passwd shadow group gshadow; do budget "$root/etc/$f"; done
 "$bb" mkdir "$state/accounts" "$state/data"
 for f in passwd shadow group gshadow users; do : >"$state/accounts/$f"; done
 run_awk snapshot "$root/etc" "$state/accounts" '' || die 'old account databases invalid; first setup required'
 # No ordinary account means GNOME first setup is still needed.
 if [ ! -s "$state/accounts/users" ]; then
  say 'no ordinary users; first setup required'
 fi
 for path in etc/NetworkManager/system-connections var/lib/bluetooth; do
  safe_path "$root/$path" || die "unsafe old state: $path; first setup required"
  if [ -d "$root/$path" ]; then
   budget "$root/$path"
   "$bb" mkdir -p "$state/data/${path%/*}"
   "$bb" cp -a "$root/$path" "$state/data/$path"
  fi
 done
 while IFS= read -r user; do
  for kind in users icons; do
   path=var/lib/AccountsService/$kind/$user
   safe_path "$root/$path" || die "unsafe AccountsService state; first setup required"
   if [ -f "$root/$path" ]; then
    budget "$root/$path"
    "$bb" mkdir -p "$state/data/${path%/*}"
    # Keep the old root's directory modes: users/ stays private and GDM can
    # still traverse icons/. The restore copies these modes onto the new root.
    for dir in var/lib/AccountsService "${path%/*}"; do
     "$bb" chmod "$("$bb" stat -c %a "$root/$dir")" "$state/data/$dir"
    done
    "$bb" cp -a "$root/$path" "$state/data/$path"
   fi
  done
 done <"$state/accounts/users"
 [ "$("$bb" du -sk "$state" | "$bb" cut -f1)" -le 65536 ] || die 'state exceeds 64 MiB; first setup required'
 : >"$state/ready"
 say 'old state captured in RAM (no fingerprint credentials)'
 ;;
merge)
 [ -f "$state/ready" ] || die 'snapshot not ready'
 accounts "$root" || die 'new account databases invalid'
 "$bb" mkdir -p "$state/merged"
 for f in passwd shadow group gshadow; do : >"$state/merged/$f"; done
 run_awk merge "$state/accounts" "$state/merged" "$root/etc" || die 'account merge refused; nothing formatted'
 : >"$state/merged-ready"
 ;;
restore)
 [ -f "$state/merged-ready" ] || die 'merge was not validated before formatting'
 # Copy into the archive files, retaining its root:shadow group and modes.
 for f in passwd shadow group gshadow; do
  safe_path "$root/etc/$f" && [ -f "$root/etc/$f" ] || die 'unsafe destination account database'
  "$bb" cat "$state/merged/$f" >"$root/etc/$f"
  "$bb" chown 0 "$root/etc/$f"
 done
 "$bb" chmod 0644 "$root/etc/passwd" "$root/etc/group"
 "$bb" chmod 0640 "$root/etc/shadow" "$root/etc/gshadow"
 for path in etc/NetworkManager/system-connections var/lib/bluetooth var/lib/AccountsService; do
  [ -d "$state/data/$path" ] || continue
  safe_path "$root/$path" || die "unsafe destination: $path"
  "$bb" mkdir -p "$root/$path"
  "$bb" cp -a "$state/data/$path/." "$root/$path/"
  "$bb" chown -R 0:0 "$root/$path"
 done
 if [ -d "$root/etc/NetworkManager/system-connections" ]; then
  "$bb" find "$root/etc/NetworkManager/system-connections" -type f -exec /bin/busybox chmod 0600 '{}' ';'
 fi
 say 'accounts, network connections and Bluetooth pairing restored; fingerprints must be enrolled again'
 ;;
*) die 'unknown operation' ;;
esac
