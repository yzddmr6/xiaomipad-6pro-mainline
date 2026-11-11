#!/bin/sh
# SPDX-License-Identifier: MIT
#
# Pack device/android/ksu-boot-ubuntu into a KernelSU-manager-installable zip.
# Deterministic: fixed member order, fixed timestamps, no host paths.
#
# The switcher is not kept twice in the tree: the Ubuntu side's
# device/gnome-overlay/usr/local/sbin/liuqin-switch is packed as the module's
# system/bin/liuqin-switch, so both systems always run the same script.
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
module_dir=${KSU_MODULE_DIR:-"$project_root/device/android/ksu-boot-ubuntu"}
switch_script=${LIUQIN_SWITCH:-"$project_root/device/gnome-overlay/usr/local/sbin/liuqin-switch"}
out_dir=${OUT_DIR:-"$project_root/out/ksu-module"}

die() { printf 'build-ksu-module: %s\n' "$*" >&2; exit 1; }

[ -d "$module_dir" ] || die "module directory is missing: $module_dir"
for required in module.prop webroot/index.html system/bin/boot-ubuntu service.sh README.md; do
	[ -f "$module_dir/$required" ] || die "module is missing $required"
done
[ -f "$switch_script" ] || die "liuqin-switch is missing: $switch_script"
[ ! -e "$module_dir/system/bin/liuqin-switch" ] ||
	die 'the module tree carries its own liuqin-switch; the overlay copy is the only source'
command -v python3 >/dev/null || die 'python3 is required'

module_id=$(sed -n 's/^id=//p' "$module_dir/module.prop" | head -n 1)
module_version=$(sed -n 's/^version=//p' "$module_dir/module.prop" | head -n 1)
case $module_id in ''|*[!a-zA-Z0-9_]*) die "module.prop has no usable id: $module_id" ;; esac
case $module_version in ''|*[!a-zA-Z0-9._-]*) die "module.prop has no usable version: $module_version" ;; esac

sh -n "$module_dir/system/bin/boot-ubuntu" || die 'boot-ubuntu is not valid sh'
sh -n "$module_dir/service.sh" || die 'service.sh is not valid sh'
sh -n "$switch_script" || die 'liuqin-switch is not valid sh'

mkdir -p "$out_dir"
zip_path=$out_dir/$module_id-$module_version.zip
rm -f "$zip_path"

MODULE_DIR=$module_dir ZIP_PATH=$zip_path SWITCH_SCRIPT=$switch_script python3 - <<'PY'
import os
import pathlib
import zipfile

module_dir = pathlib.Path(os.environ["MODULE_DIR"])
zip_path = pathlib.Path(os.environ["ZIP_PATH"])
# README.md documents the source tree, not the installed module.
skip = {"README.md"}
members = {
    str(p.relative_to(module_dir)): p for p in module_dir.rglob("*")
    if p.is_file() and str(p.relative_to(module_dir)) not in skip
    and "__pycache__" not in p.parts
}
if not members:
    raise SystemExit("build-ksu-module: nothing to pack")
members["system/bin/liuqin-switch"] = pathlib.Path(os.environ["SWITCH_SCRIPT"])
members["system/bin/switch-common.sh"] = pathlib.Path(os.environ["SWITCH_SCRIPT"]).with_name("switch-common.sh")
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
    for name, path in sorted(members.items()):
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = ((0o755 if os.access(path, os.X_OK) else 0o644) << 16)
        zf.writestr(info, path.read_bytes())
        print(f"  {info.external_attr >> 16 & 0o777:o}  {name}")
print(f"build-ksu-module: {zip_path}")
PY
