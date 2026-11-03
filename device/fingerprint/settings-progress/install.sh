#!/bin/sh
# Install the Settings progress extension over the distribution Settings binary.
# This changes /usr/bin/gnome-control-center with a local dpkg diversion.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
lib=/usr/local/lib/liuqin-fingerprint-ui
binary=/usr/bin/gnome-control-center

[ -f "$root/settings-progress.so" ] || "$root/build.sh"
[ -x "$lib/gnome-control-center" ] || {
    install -d -m 0755 "$lib"
    cp -a "$binary" "$lib/gnome-control-center"
}
install -m 0755 "$root/settings-progress.so" "$lib/settings-progress.so"
install -m 0755 "$root/settings-launcher.sh" "$lib/settings-launcher"
if [ "$(dpkg-divert --local --truename "$binary")" = "$binary" ]; then
    dpkg-divert --local --rename --add "$binary"
fi
install -m 0755 "$lib/settings-launcher" "$binary"
printf 'Installed Settings progress extension; restart Settings.\n'
