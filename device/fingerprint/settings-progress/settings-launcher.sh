#!/bin/sh
export LD_PRELOAD=/usr/local/lib/liuqin-fingerprint-ui/settings-progress.so
exec /usr/local/lib/liuqin-fingerprint-ui/gnome-control-center "$@"
