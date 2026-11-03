#!/bin/sh
# Remove the Settings progress extension and restore the distribution binary.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
binary=/usr/bin/gnome-control-center
lib=/usr/local/lib/liuqin-fingerprint-ui

if [ "$(dpkg-divert --local --truename "$binary")" != "$binary" ]; then
    rm -f "$binary"
    dpkg-divert --local --rename --remove "$binary"
fi
rm -rf "$lib"
printf 'Removed Settings progress extension.\n'
