#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Exercise real boot gates with fsck exit codes and disposable ext4 images."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RootFsck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.commands = self.dir / 'commands'
        self.env = dict(os.environ, PATH=str(self.dir) + ':' + os.environ['PATH'],
                        COMMANDS=str(self.commands))
        self.helper = (ROOT / 'initramfs/root-fsck').read_text()
        self.assertIn('/usr/sbin/e2fsck -p "$root_device"', self.helper)
        self.checker = self.dir / 'checker'
        self.checker.write_text('#!/bin/sh\necho "FSCK $*" >> "$COMMANDS"\necho checker-output\nexit "$FSCK_STATUS"\n')
        self.checker.chmod(0o755)
        self.install_helper(self.checker)

    def install_helper(self, checker):
        self.script = self.dir / 'root-fsck'
        self.script.write_text(self.helper.replace('/usr/sbin/e2fsck', str(checker)))
        self.script.chmod(0o755)

    def run_check(self, status):
        return subprocess.run(['sh', str(self.script), '/dev/test-root', str(self.dir / 'fsck.log')],
                              env=dict(self.env, FSCK_STATUS=str(status)), capture_output=True, text=True)

    def test_exit_status_policy_and_log(self):
        for status in [0, 1, 2, 3, 4, 8, 16, 32, 128]:
            with self.subTest(status=status):
                result = self.run_check(status)
                self.assertEqual(result.returncode, 0 if status in (0, 1) else status)
                text = (self.dir / 'fsck.log').read_text()
                self.assertIn('checker-output', text)
                self.assertIn('root-fsck status=' + str(status), text)
                if status not in (0, 1):
                    self.assertIn('Read-write boot is stopped', result.stderr)
        self.assertTrue(all(line == 'FSCK -p /dev/test-root' for line in self.commands.read_text().splitlines()))

    def test_missing_checker_blocks_boot(self):
        self.checker.unlink()
        self.assertEqual(self.run_check(0).returncode, 127)
        self.assertIn('Read-write boot is stopped', (self.dir / 'fsck.log').read_text())

    def test_persistent_mount_gate(self):
        oneplus = ROOT / 'initramfs/init-ncm'
        if oneplus.exists():
            source = oneplus.read_text()
            body = source.split('\tif [ -n "$ROOT_SPEC" ]; then\n', 1)[1].split('\n\tif [ -n "$ROOT_SPEC" ] &&', 1)[0]
            body = 'if [ -n "$ROOT_SPEC" ]; then\n' + body
            body = body.replace('/run/evidence', str(self.dir)).replace('/dev/console', str(self.dir / 'console'))
            prefix = '''ROOT_SPEC=PARTLABEL=linux_rootfs
WATCHDOG_PID=999999
find_partlabel() { echo /dev/test-root; }
kill() { echo STOP_TIMER >> "$COMMANDS"; }
wait() { return 0; }
mkdir() { return 0; }
mount() { echo "MOUNT $*" >> "$COMMANDS"; }
grep() { return 0; }
'''
            variants = ['']
        else:
            source = (ROOT / 'initramfs/init').read_text()
            body = 'storage_prepare_persistent_root() {\n' + source.split('storage_prepare_persistent_root() {\n', 1)[1].split('\n}\n', 1)[0] + '\n}\nstorage_prepare_persistent_root\n'
            body = body.replace('/run/root-fsck.log', str(self.dir / 'root-fsck.log'))
            prefix = '''storage_target=/dev/test-root
storage_parent=/dev/test-parent
storage_probe_root=/test-probe
storage_newroot=/test-root
storage_dev_root=/no-such-test-dev
storage_home=
storage_root_subdir=$SUBDIR
BB=fake_bb
fake_bb() { echo "BB $*" >> "$COMMANDS"; }
log() { echo "$*"; }
storage_identity_ok() { return 0; }
storage_root_ok() { return 0; }
storage_snapshot_sd_nodes() { return 0; }
storage_snapshot_persistent_ok() { return 0; }
storage_path_is_block() { return 1; }
storage_abort() { echo ABORT >> "$COMMANDS"; }
'''
            variants = ['', 'native-root']
        for subdir in variants:
            for status in [0, 1, 2, 3, 4, 8, 16, 32, 128]:
                with self.subTest(subdir=subdir, status=status):
                    self.commands.unlink(missing_ok=True)
                    subprocess.run(['sh', '-c', prefix + body],
                                   env=dict(self.env, FSCK_STATUS=str(status), SUBDIR=subdir),
                                   capture_output=True, text=True, check=False)
                    commands = self.commands.read_text().splitlines()
                    checked = next(i for i, line in enumerate(commands) if line == 'FSCK -p /dev/test-root')
                    writes = [i for i, line in enumerate(commands) if ' -o rw' in line]
                    if status in (0, 1):
                        self.assertEqual(len(writes), 1)
                        self.assertLess(checked, writes[0])
                    else:
                        self.assertEqual(writes, [])
                        if not oneplus.exists():
                            self.assertEqual(commands[-1], 'ABORT')
                    if oneplus.exists():
                        self.assertLess(commands.index('STOP_TIMER'), checked)
                    else:
                        self.assertTrue(any(line.startswith('BB umount ') for line in commands[:checked]))

    def test_real_ext4_preen_and_corrupt_superblock(self):
        mkfs, fsck, debugfs = map(shutil.which, ['mke2fs', 'e2fsck', 'debugfs'])
        self.assertTrue(mkfs and fsck and debugfs, 'e2fsprogs required for boot gate tests')
        disk = self.dir / 'root.ext4'
        with disk.open('wb') as stream:
            stream.truncate(32 << 20)
        subprocess.run([mkfs, '-q', '-F', '-t', 'ext4', str(disk)], check=True, capture_output=True)
        self.install_helper(fsck)
        def run():
            return subprocess.run(['sh', str(self.script), str(disk), str(self.dir / 'real.log')], capture_output=True)
        self.assertEqual(run().returncode, 0)
        subprocess.run([debugfs, '-w', '-R', 'ssv state 2', str(disk)], check=True, capture_output=True)
        self.assertEqual(run().returncode, 0)
        subprocess.run([fsck, '-fn', str(disk)], check=True, capture_output=True)
        with disk.open('r+b') as stream:
            stream.seek(1024 + 56)
            stream.write(b'\0\0')
        self.assertNotEqual(run().returncode, 0)
        self.assertIn('Read-write boot is stopped', (self.dir / 'real.log').read_text())


if __name__ == '__main__':
    unittest.main()
