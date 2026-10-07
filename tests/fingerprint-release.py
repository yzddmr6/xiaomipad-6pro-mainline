#!/usr/bin/env python3
"""Offline firmware transaction/package staging tests; no hardware or host services."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


firmware = load("firmware", ROOT / "device/fingerprint/oem/src/fingerprint/deployment/provision_firmware.py")
package = load("release_package", ROOT / "tools/build-liuqin-fingerprint-deb.py")


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.cache = self.base / "firmware"
        self.cache.mkdir(mode=0o700)
        self.data = {name: (name + " fixture").encode() for name in firmware.NAMES}
        self.hashes = {name: hashlib.sha256(data).hexdigest() for name, data in self.data.items()}
        (self.cache / "SHA256.json").write_text(json.dumps(self.hashes))
        self.mountpoint = self.base / "mountpoint"
        self.mountpoint.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def fake_command(self, argv, **kwargs):
        if argv[0] == "mount":
            self.assertEqual(argv[-2], "/dev/disk/by-partlabel/modem_a")
            self.assertEqual(argv[1:5], ["-t", "vfat", "-o",
                "ro,nodev,nosuid,noexec,codepage=437,iocharset=iso8859-1"])
            image = self.mountpoint / "image"
            image.mkdir()
            for name, value in self.data.items():
                (image / name).write_bytes(value)
        else:
            self.assertEqual(argv, ["umount", str(self.mountpoint)])
            for path in (self.mountpoint / "image").iterdir():
                path.unlink()
            (self.mountpoint / "image").rmdir()
        return SimpleNamespace(returncode=0)

    def provision(self):
        original = tempfile.mkdtemp

        def temporary(*args, **kwargs):
            if kwargs.get("dir") == "/run":
                return str(self.mountpoint)
            return original(*args, **kwargs)

        with patch.object(firmware.tempfile, "mkdtemp", side_effect=temporary), \
             patch.object(firmware.subprocess, "run", side_effect=self.fake_command) as command:
            firmware.provision(self.cache)
            return command.call_args_list

    def test_verified_cache_does_not_mount(self):
        for name, data in self.data.items():
            (self.cache / name).write_bytes(data)
        with patch.object(firmware.subprocess, "run") as command:
            firmware.provision(self.cache)
            command.assert_not_called()

    def test_first_boot_copies_fixed_members_read_only_and_unmounts(self):
        self.assertEqual(len(self.provision()), 2)
        self.assertFalse(self.mountpoint.exists())
        for name, value in self.data.items():
            self.assertEqual((self.cache / name).read_bytes(), value)
            self.assertEqual((self.cache / name).stat().st_mode & 0o777, 0o600)

    def test_bad_stock_leaves_existing_cache_unchanged_and_unmounts(self):
        prior = self.cache / firmware.NAMES[0]
        prior.write_bytes(b"existing-invalid-cache")
        self.data[firmware.NAMES[-1]] = b"wrong stock image"
        with self.assertRaisesRegex(RuntimeError, "stock FPC firmware hash mismatch"):
            self.provision()
        self.assertFalse(self.mountpoint.exists())
        self.assertEqual(prior.read_bytes(), b"existing-invalid-cache")
        self.assertEqual({path.name for path in self.cache.iterdir()}, {prior.name, "SHA256.json"})

    def test_manifest_cannot_expand_extraction(self):
        self.hashes["../secret"] = "0" * 64
        (self.cache / "SHA256.json").write_text(json.dumps(self.hashes))
        with patch.object(firmware.subprocess, "run") as command:
            with self.assertRaises(ValueError):
                firmware.provision(self.cache)
            command.assert_not_called()

    def test_root_staging_has_no_account_services_or_credentials(self):
        payload = self.base / "payload"
        (payload / "deployment").mkdir(parents=True)
        conf = ROOT / "device/fingerprint/oem/src/fingerprint/libfprint-tod/fprintd-fpc1264-oem.conf"
        (payload / "deployment/fprintd-fpc1264-oem.conf").write_text(conf.read_text())
        root = self.base / "root"
        with patch.object(package.subprocess, "run") as command:
            package.stage_root(payload, root, {"deployment_mode": "installed"})
            command.assert_not_called()
        self.assertFalse((root / "var").exists())
        self.assertFalse((root / "etc/passwd").exists())
        self.assertFalse((root / "usr/share/applications").exists())
        self.assertIn("UMask=0077", (root / "etc/systemd/system/fprintd.service.d/60-liuqin-fpc-oem.conf").read_text())
        unit = (root / "usr/lib/systemd/system/liuqin-fpc-stable-module.service").read_text()
        self.assertNotIn("ssh", unit)
        self.assertNotIn("NetworkManager", unit)
        self.assertTrue((root / "etc/systemd/system/multi-user.target.wants/liuqin-fpc-stable-module.service").is_symlink())


if __name__ == "__main__":
    if os.geteuid() != 0:
        raise SystemExit("Run as root in an isolated test container/user namespace")
    unittest.main()
