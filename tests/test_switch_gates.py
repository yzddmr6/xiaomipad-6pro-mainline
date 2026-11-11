# SPDX-License-Identifier: MIT
"""Shared file-backed switch contract tests; never open real devices.

Canonical source: onepluspad-2pro-mainline/tests/test_switch_gates.py.
The same suite runs against liuqin's shell switcher, including BusyBox.
"""
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ERHAI = (ROOT / 'switch/erhai-switch').exists()
SWITCH = ROOT / ('switch/erhai-switch' if ERHAI else
                 'device/gnome-overlay/usr/local/sbin/liuqin-switch')
PARTS = ('boot', 'init_boot', 'vendor_boot') if ERHAI else ('boot',)
SIZE = 8192


class Gates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dev, self.store, self.bin = (self.root / n for n in ('dev', 'store', 'bin'))
        for directory in (self.dev, self.store, self.bin):
            directory.mkdir()
        self.battery = self.dev / 'power_supply/battery'
        self.battery.mkdir(parents=True)
        (self.battery / 'type').write_text('Battery\n')
        (self.battery / 'capacity').write_text('80\n')
        (self.dev / 'slot').write_text('_a\n')
        (self.dev / 'cmdline').write_text('androidboot.slot_suffix=_a\n')
        self.wrapper('id', '[ "$1" = -u ] && { echo 0; exit; }; exec /usr/bin/id "$@"')
        self.wrapper('sync', 'exit 0')
        self.wrapper('getent', 'echo "tester:x:1000:1000::/nonexistent:/bin/sh"')
        self.wrapper('busctl', 'printf "%s\\n" "$@" >> "$CASE_ROOT/notifications"')
        self.env = {**os.environ, 'PATH': str(self.bin) + ':' + os.environ['PATH'],
                    'CASE_ROOT': str(self.root), 'PKEXEC_UID': '1000'}
        for kind in ('android', 'ubuntu'):
            directory = self.store / kind
            directory.mkdir()
            for name in PARTS:
                data = bytearray((kind + name).encode().ljust(SIZE, b'\0'))
                data[:8] = b'ANDROID!'
                struct.pack_into('<I', data, 8, 1000)
                if kind == 'ubuntu':
                    struct.pack_into('<II', data, 36, 4096, 2)
                    struct.pack_into('<II', data, 1644, 1660, 5000)
                    data[44:44+len(b'erhai.boot=mainline')] = b'erhai.boot=mainline'
                else:
                    struct.pack_into('<I', data, 20, 1584)
                    struct.pack_into('<I', data, 40, 4)
                (directory / (name + '.img')).write_bytes(data)
            (directory / 'SHA256SUMS').write_text(''.join(
                f'{hashlib.sha256((directory / (n + ".img")).read_bytes()).hexdigest()}  {n}.img\n'
                for n in PARTS))
        self.fill('ubuntu')

    def wrapper(self, name, body):
        path = self.bin / name
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o755)

    def fill(self, kind):
        for name in PARTS:
            (self.dev / (name + '_a')).write_bytes((self.store / kind / (name + '.img')).read_bytes())
        (self.dev / '.rebooted').unlink(missing_ok=True)

    def partitions(self):
        return {n: (self.dev / (n + '_a')).read_bytes() for n in PARTS}

    def run_switch(self, *args, shell='sh'):
        command = [shell] if shell != 'busybox' else ['busybox', 'sh']
        if shell == 'busybox' and os.getuid() != 0:
            command = ['unshare', '-r', *command]
        return subprocess.run(command + [str(SWITCH), '--dev-dir', str(self.dev),
                                         '--store', str(self.store), *args],
                              env=self.env, text=True, capture_output=True, timeout=15)

    def assert_refused_unchanged(self, *args):
        before = self.partitions()
        result = self.run_switch(*args)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before, self.partitions())
        self.assertFalse((self.dev / '.rebooted').exists())
        return result

    def test_battery_gate(self):
        for value in ('0', '39', '', 'bad', '-1', '101', '40.0'):
            for target in ('android', 'ubuntu'):
                with self.subTest(value=value, target=target):
                    self.fill('ubuntu')
                    (self.battery / 'capacity').write_text(value)
                    result = self.assert_refused_unchanged('to-' + target)
                    self.assertIn('40%', result.stderr)
                    self.assertIn('40%', (self.root / 'notifications').read_text())
        (self.battery / 'capacity').unlink()
        self.assert_refused_unchanged('to-android')
        shutil.rmtree(self.dev / 'power_supply')
        self.assert_refused_unchanged('to-android')

    def test_boundary_and_repeat_reboot(self):
        (self.battery / 'capacity').write_text('40\n')
        for target in ('android', 'ubuntu'):
            with self.subTest(target=target):
                result = self.run_switch('to-' + target, '--no-reboot')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse((self.dev / '.rebooted').exists())
                before = self.partitions()
                # Any partition rewrite would fail this repeated-click case.
                self.fail_write(PARTS[0], persistent=True)
                result = self.run_switch('to-' + target)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(before, self.partitions())
                self.assertTrue((self.dev / '.rebooted').exists())
                (self.dev / '.rebooted').unlink()
                (self.bin / 'dd').unlink()

    def test_dry_run_and_no_reboot(self):
        for target in ('android', 'ubuntu'):
            before = self.partitions()
            result = self.run_switch('to-' + target, '--dry-run')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(before, self.partitions())
            self.assertFalse((self.dev / '.rebooted').exists())

    def test_status_and_verify_do_not_require_charge(self):
        shutil.rmtree(self.dev / 'power_supply')
        before = self.partitions()
        for command in ('status', 'verify'):
            result = self.run_switch(command)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before, self.partitions())

    def test_unknown_slot(self):
        # A saved state must never turn missing live slot evidence into A.
        (self.store / 'state.json').write_text('{"slot_suffix":"_a"}')
        for suffix in ('', 'garbage'):
            (self.dev / 'slot').write_text(suffix)
            (self.dev / 'cmdline').write_text('androidboot.slot_suffix=' + suffix)
            self.assert_refused_unchanged('to-android')
        if not ERHAI:
            for words in ('androidboot.slot_suffix=_b',
                          'androidboot.slot_suffix=_a slot_suffix=_b'):
                (self.dev / 'cmdline').write_text(words)
                self.assert_refused_unchanged('to-android')

    def fail_write(self, name, persistent=False, corrupt=False):
        (self.root / 'fault').touch()
        self.wrapper('dd', f'''
for arg in "$@"; do
    if [ "$arg" = "of=$CASE_ROOT/dev/{name}_a" ] && [ -e "$CASE_ROOT/fault" ]; then
        {'true' if persistent else 'rm "$CASE_ROOT/fault"'}
        printf 'broken' > "$CASE_ROOT/dev/{name}_a"
        echo 'INJECTED write diagnostic' >&2
        exit {0 if corrupt else 1}
    fi
done
exec /usr/bin/dd "$@"
''')

    def test_partial_write_rollback(self):
        for part in PARTS:
            for corrupt in (False, True):
                with self.subTest(part=part, corrupt=corrupt):
                    self.fill('ubuntu')
                    # Exact preimage: deliberately use an older Ubuntu build.
                    for name in PARTS:
                        path = self.dev / (name + '_a')
                        data = bytearray(path.read_bytes())
                        data[4096:4100] = b'old!'
                        path.write_bytes(data)
                    before = self.partitions()
                    self.fail_write(part, corrupt=corrupt)
                    result = self.run_switch('to-android')
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(before, self.partitions())
                    self.assertFalse((self.dev / '.rebooted').exists())
                    self.assertIn('rolled-back', result.stderr)
                    log = (self.store / 'switch.log').read_text()
                    self.assertIn('INJECTED write diagnostic', result.stderr)
                    self.assertIn('INJECTED write diagnostic', log)
                    self.assertIn('rolled-back', log)
                    self.assertIn('NOT rebooted' if not ERHAI else 'Not rebooting',
                                  (self.root / 'notifications').read_text())

    def test_failed_rollback_retains_recovery(self):
        self.fail_write(PARTS[-1], persistent=True)
        result = self.run_switch('to-android')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('rollback-failed', result.stdout + result.stderr)
        self.assertFalse((self.dev / '.rebooted').exists())
        self.assertTrue((self.store / ('.switch-rollback' if ERHAI else '.switch-rollback.img')).exists())
        if ERHAI:
            for name in PARTS[:-1]:
                self.assertEqual((self.dev / (name + '_a')).read_bytes(),
                                 (self.store / 'ubuntu' / (name + '.img')).read_bytes())

    def test_reboot_failure_reports_stderr(self):
        # Run the actual reboot function with fake system commands. Only this
        # function sees DEV_DIR empty; no device discovery/write code is run.
        function = re.search(r'^do_reboot\(\) \{.*?^\}', SWITCH.read_text(), re.M | re.S)[0]
        self.wrapper('systemctl', 'echo "INJECTED reboot inhibitor" >&2; exit 1')
        self.wrapper('reboot', 'echo UNSAFE-FORCE >> "$CASE_ROOT/forced"; exit 1')
        script = f'''. {shlex.quote(str(SWITCH.with_name('switch-common.sh')))}
PROG=test-switch
STORE={shlex.quote(str(self.store))}
DEV_DIR=
NO_REBOOT=0
DRY_RUN=0
log() {{ printf '%s\\n' "$*" >> "$STORE/switch.log"; }}
say() {{ log "$*"; }}
cleanup() {{ :; }}
is_android() {{ return 1; }}
die() {{ log "$*"; switch_notify_failure "$*"; exit 1; }}
{function}
do_reboot
'''
        result = subprocess.run(['sh', '-c', script], env=self.env, text=True,
                                capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('INJECTED reboot inhibitor', result.stderr)
        self.assertIn('INJECTED reboot inhibitor', (self.store / 'switch.log').read_text())
        self.assertIn('reboot failed', (self.root / 'notifications').read_text())
        self.assertFalse((self.root / 'forced').exists())

    def test_live_slot_sources_must_agree(self):
        def function(name):
            return re.search(r'^' + name + r'\(\) \{.*?^\}', SWITCH.read_text(), re.M | re.S)[0]
        cmdline, config, bootargs = (self.root / n for n in ('cmdline', 'bootconfig', 'bootargs'))
        bootargs.write_text('')
        body = (function('slot_from_bootargs') + '\n' + function('detect_slot') if ERHAI else
                function('slot_from_cmdline') + '\n' + function('detect_running_slot'))
        for real, fake in (('/proc/cmdline', cmdline), ('/proc/bootconfig', config),
                           ('/proc/device-tree/chosen/bootargs', bootargs)):
            body = body.replace(real, str(fake))
        prefix = '. ' + shlex.quote(str(SWITCH.with_name('switch-common.sh'))) + '\nDEV_DIR=\n'
        prefix += 'is_android() { return 1; }\nslot_from_gpt() { printf _a; }\n'
        tail = '\ndetect_slot' if ERHAI else '\ndetect_running_slot; printf "%s" "$RUNNING_SLOT"'
        cases = [
            ('androidboot.slot_suffix=_a', 'androidboot.slot_suffix = "_a"', '_a' if ERHAI else 'a'),
            ('androidboot.slot_suffix=_a', 'androidboot.slot_suffix = "_b"', ''),
            ('androidboot.slot_suffix=', 'androidboot.slot_suffix = "_a"', ''),
            ('androidboot.slot_suffix=_a', 'androidboot.slot_suffix = "bogus"', ''),
            ('androidboot.slot_suffix=_a androidboot.slot_suffix=_b', '', ''),
        ]
        for words, bootconfig, expected in cases:
            with self.subTest(words=words, bootconfig=bootconfig):
                cmdline.write_text(words + '\n')
                config.write_text(bootconfig + '\n')
                result = subprocess.run(['sh', '-c', prefix + body + tail], env=self.env,
                                        text=True, capture_output=True, timeout=5)
                self.assertEqual(result.stdout.strip(), expected, result.stderr)

    def test_busybox_shared_gate(self):
        self.assertIsNotNone(shutil.which('busybox'))
        for level, expected in (('39', 1), ('40', 0)):
            (self.battery / 'capacity').write_text(level)
            result = self.run_switch('to-android', '--no-reboot', shell='busybox')
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
