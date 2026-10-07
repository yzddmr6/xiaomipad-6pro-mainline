#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Prepare the isolated fprintd build without modifying the vendor snapshot."""
import argparse
import difflib
from pathlib import Path
import shutil

def prepare(vendor: Path, destination: Path, patch_output: Path = None):
    if destination.exists():
        raise SystemExit("Refuse to replace an existing build source directory")
    shutil.copytree(vendor, destination)
    patch = []
    for name in ("file_storage.c", "file_storage.h", "device.c"):
        path = destination / "src" / name
        original = path.read_text()
        revised = original
        if name == "file_storage.c":
            revised += '\n#include "oem-update.inc"\n'
        elif name == "file_storage.h":
            revised += "\ngboolean file_storage_print_data_update (FpPrint *, FpPrint *, GCancellable *, GError **);\n"
        else:
            revised = revised.replace('#include "storage.h"', '#include "storage.h"\n#include "file_storage.h"', 1)
            start = revised.index("static void\nmatch_cb (")
            end = revised.index("\nstatic void\nverify_cb (", start)
            body = revised[start:end]
            body = body.replace("  gboolean cancelled;", "  g_autoptr(GError) storage_error = NULL;\n  FpPrint *original = NULL;\n  gboolean cancelled;", 1)
            body = body.replace("  report_verify_status (rdev, matched, error);", '''  if (match && g_strcmp0 (fp_device_get_driver (device), "fpc1264_oem") == 0)
    original = g_object_get_data (G_OBJECT (match), "liuqin-fpc-oem-original");
  if (original)
    {
      if (matched && !file_storage_print_data_update (original, match, priv->current_cancellable, &storage_error))
        matched = FALSE;
      if (!matched)
        {
          g_autoptr(GVariant) data = NULL;
          g_object_get (original, "fpi-data", &data, NULL);
          g_object_set (match, "fpi-data", data, NULL);
        }
      g_object_set_data (G_OBJECT (match), "liuqin-fpc-oem-original", NULL);
    }
  report_verify_status (rdev, matched, storage_error ? storage_error : error);''', 1)
            revised = revised[:start] + body + revised[end:]
        if revised == original:
            raise SystemExit("Upstream patch anchor missing: " + name)
        path.write_text(revised)
        patch.extend(difflib.unified_diff(original.splitlines(True), revised.splitlines(True),
                                        fromfile="a/src/" + name, tofile="b/src/" + name))
    shutil.copyfile(Path(__file__).with_name("oem-update.inc"), destination / "src/oem-update.inc")
    (patch_output or destination.parent / "fprintd-oem-update.patch").write_text("".join(patch))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("vendor", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--patch-output", type=Path)
    options = parser.parse_args()
    prepare(options.vendor, options.destination, options.patch_output)
