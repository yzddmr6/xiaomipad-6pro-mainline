#!/usr/bin/env python3
"""Check startup modes and the module loading gates; no hardware calls."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT = ROOT / "device/fingerprint/oem/src/fingerprint/deployment"
spec = importlib.util.spec_from_file_location("stable_module", DEPLOYMENT / "stable_module.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class StartupModeTests(unittest.TestCase):
    def test_default_development_keeps_control_and_network(self):
        self.assertEqual(module.readiness_services({}),
                         ("ssh", "NetworkManager", "gdm", "iio-sensor-proxy"))
        self.assertEqual(module.readiness_services({"control_service": "liuqin-codex-wifi.service"})[0],
                         "liuqin-codex-wifi.service")

    def test_installed_loader_and_unit_share_dependencies(self):
        info = {"deployment_mode": "installed", "control_service": "unavailable-debug.service"}
        self.assertEqual(module.readiness_services(info), ("gdm", "iio-sensor-proxy"))
        unit = module.render_module_unit((DEPLOYMENT / "liuqin-fpc-stable-module.service").read_text(), info)
        self.assertIn("After=gdm.service iio-sensor-proxy.service\n", unit)
        self.assertIn("Wants=gdm.service iio-sensor-proxy.service\n", unit)
        self.assertNotIn("NetworkManager", unit)
        self.assertNotIn("unavailable-debug", unit)
        self.assertIn("ConditionPathExists=/dev/tee0\n", unit)
        self.assertIn("ConditionPathExists=/sys/bus/spi/devices/spi1.0/of_node\n", unit)

    def test_unknown_mode_does_not_silently_relax_guards(self):
        with self.assertRaises(ValueError):
            module.readiness_services({"deployment_mode": "typo"})

    def test_missing_tee_never_invokes_a_command_in_either_mode(self):
        for mode in ("development", "installed"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                (base / "CANDIDATE.json").write_text(json.dumps({"deployment_mode": mode}))

                def paths(value):
                    return Mock(exists=lambda: False) if str(value) == "/dev/tee0" else Path(value)

                with patch.object(module, "__file__", str(base / "stable_module.py")), \
                     patch.object(module, "Path", side_effect=paths), \
                     patch.object(module.os, "geteuid", return_value=0), \
                     patch.object(module.subprocess, "run") as command:
                    with self.assertRaisesRegex(RuntimeError, "TEE"):
                        module.load()
                    command.assert_not_called()

    def test_installed_mode_loads_the_running_kernels_module_after_the_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)

            def paths(value):
                path = Path(value)
                if path.is_absolute() and not path.is_relative_to(base):
                    return base / "hardware" / path.relative_to("/")
                return path

            node = paths("/sys/bus/spi/devices/spi1.0/of_node")
            regulator = paths("/sys/firmware/devicetree/base/soc@0/rsc@17a00000/regulators-1/ldo9")
            device = paths("/dev/fpc1020")
            entries = {paths("/dev/tee0"): b"", node / "compatible": b"fpc,fpc1020\0",
                       node / "vdd-supply": (1).to_bytes(4, "big"),
                       regulator / "phandle": (1).to_bytes(4, "big"),
                       regulator / "regulator-min-microvolt": (2960000).to_bytes(4, "big"),
                       regulator / "regulator-max-microvolt": (3008000).to_bytes(4, "big")}
            for path, data in entries.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            # A candidate built for another kernel must not matter any more.
            (base / "CANDIDATE.json").write_text(json.dumps({
                "validated_kernel_release": "other-kernel", "deployment_mode": "installed"}))
            modprobe_status = 0

            def run(argv, **_):
                if argv[0] == "modprobe" and modprobe_status == 0:
                    device.write_bytes(b"")
                return SimpleNamespace(returncode=modprobe_status if argv[0] == "modprobe" else 0)

            with patch.object(module, "__file__", str(base / "stable_module.py")), \
                 patch.object(module, "Path", side_effect=paths), \
                 patch.object(module.os, "geteuid", return_value=0), \
                 patch.object(module.subprocess, "run", side_effect=run) as command:
                module.load()
                programs = [call.args[0] for call in command.call_args_list]
                self.assertIn(["modprobe", "fpc1264_spi_diag"], programs)
                self.assertEqual(programs[-1], ["udevadm", "settle", "--timeout=5"])
                self.assertFalse(any(argv[0] == "insmod" for argv in programs))

                device.unlink()
                modprobe_status = 1
                command.reset_mock()
                with self.assertRaisesRegex(RuntimeError, "does not provide fpc1264_spi_diag"):
                    module.load()
                self.assertNotIn("udevadm", [call.args[0][0] for call in command.call_args_list])

                command.reset_mock()
                (regulator / "regulator-min-microvolt").write_bytes((1000000).to_bytes(4, "big"))
                with self.assertRaisesRegex(RuntimeError, "voltage range"):
                    module.load()
                command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
