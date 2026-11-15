#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Background-wake resuspend: the real key daemon's scheduler and the real action helper.

The daemon runs in its --test-script mode against a recording helper; the
helper runs against fake loginctl, gdbus, busctl and systemctl. Real suspend,
GNOME's blank timing and the wake sources need the tablet.
"""
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DAEMON = ROOT / 'device/power-key/liuqin-power-keyd.c'
HELPER = ROOT / 'device/gnome-overlay/usr/local/libexec/liuqin-power-key-action'


def script(path, body):
    path.write_text('#!/bin/sh\n' + body + '\n')
    path.chmod(0o755)


class Scheduler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.daemon = Path(cls.build.name) / 'keyd'
        subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-Wshadow',
                        '-Wstrict-prototypes', '-Wmissing-prototypes', str(DAEMON),
                        '-o', str(cls.daemon)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.helper = self.dir / 'helper'
        # Each resuspend call consumes the next exit status; short/long succeed.
        script(self.helper, 'echo "$1" >> "$LOG"\n'
               '[ "$1" = resuspend ] || exit 0\n'
               'rc=$(head -n1 "$STATUS"); tail -n +2 "$STATUS" > "$STATUS.n"; mv "$STATUS.n" "$STATUS"\n'
               'exit "${rc:-3}"')

    def run_script(self, events, statuses=()):
        (self.dir / 'status').write_text(''.join(f'{s}\n' for s in statuses))
        (self.dir / 'log').unlink(missing_ok=True)
        (self.dir / 'events').write_text(''.join(f'{t} {e}\n' for t, e in events))
        env = dict(os.environ, LOG=str(self.dir / 'log'), STATUS=str(self.dir / 'status'))
        subprocess.run([str(self.daemon), '--action-helper', str(self.helper),
                        '--test-script', str(self.dir / 'events')],
                       env=env, check=True, capture_output=True, timeout=30)
        log = self.dir / 'log'
        return log.read_text().split() if log.exists() else []

    def test_background_wake_waits_for_the_dark_session_then_suspends_once(self):
        calls = self.run_script([(0, 'resume'), (10000, 'tick'), (20000, 'tick'),
                                 (25000, 'tick'), (30000, 'tick'), (90000, 'tick')], (75, 0))
        self.assertEqual(calls, ['resuspend', 'resuspend'])

    def test_power_key_wake_never_resuspends(self):
        calls = self.run_script([(0, 'resume'), (300, 'press'), (400, 'release'),
                                 (20000, 'tick'), (60000, 'tick')], (0,))
        self.assertEqual(calls, ['short'])

    def test_ineligible_session_ends_the_window(self):
        calls = self.run_script([(0, 'resume'), (20000, 'tick'), (25000, 'tick')], (3, 0))
        self.assertEqual(calls, ['resuspend'])

    def test_window_is_bounded_and_rearmed_by_the_next_resume(self):
        ticks = [(t, 'tick') for t in range(20000, 700001, 5000)]
        calls = self.run_script([(0, 'resume'), *ticks], [75] * 500)
        # Every 5 s from 20 s to the 10 min end of the window.
        self.assertEqual(calls, ['resuspend'] * 117)
        calls = self.run_script([(0, 'resume'), (20000, 'tick'), (25000, 'tick'),
                                 (100000, 'resume'), (110000, 'tick'), (120000, 'tick')], (0, 0))
        self.assertEqual(calls, ['resuspend', 'resuspend'])

    def test_without_a_resume_nothing_is_scheduled(self):
        self.assertEqual(self.run_script([(20000, 'tick'), (25000, 'tick')], (0,)), [])


class Helper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.bin = self.dir / 'bin'
        self.bin.mkdir()
        self.state = self.dir / 'state'
        self.state.mkdir()
        self.wakeup = self.dir / 'wakeup'
        (self.wakeup / 'wakeup3').mkdir(parents=True)
        (self.wakeup / 'wakeup3/name').write_text('qcom-battmgr-charge\n')
        (self.wakeup / 'wakeup3/active_time_ms').write_text('0\n')
        (self.wakeup / 'wakeup4').mkdir()
        (self.wakeup / 'wakeup4/name').write_text('alarmtimer\n')
        (self.wakeup / 'wakeup4/active_time_ms').write_text('900\n')
        runtime = self.dir / 'run/user/1000'
        runtime.mkdir(parents=True)
        self.bus = socket.socket(socket.AF_UNIX)
        self.bus.bind(str(runtime / 'bus'))
        self.addCleanup(self.bus.close)
        self.answers = self.dir / 'answers'
        self.answer(GetActive='(true,)', IsInhibited='(false,)', Get='(<3>,)',
                    GetIdletime='(uint64 31000,)', BlockInhibited='s "handle-power-key:handle-lid-switch"',
                    Class='user')
        script(self.bin / 'loginctl', f'''. "{self.answers}"
case "$1 $3" in
"show-seat --property=ActiveSession") echo c2 ;;
"show-session --property=Active") echo yes ;;
"show-session --property=Class") echo "$Class" ;;
"show-session --property=User") echo 1000 ;;
"show-session --property=Name") echo tester ;;
esac''')
        script(self.bin / 'getent', 'echo "tester:x:1000:1000::/nonexistent:/bin/sh"')
        script(self.bin / 'setpriv', 'while [ "$1" != -- ]; do shift; done; shift; exec "$@"')
        script(self.bin / 'gdbus', f'''. "{self.answers}"
for word; do case $word in *.GetActive) echo "$GetActive" ;; *.IsInhibited) echo "$IsInhibited" ;;
*.Properties.Get) echo "$Get" ;; *.GetIdletime) echo "$GetIdletime" ;; esac; done''')
        script(self.bin / 'busctl', f'. "{self.answers}"; echo "$BlockInhibited"')
        script(self.bin / 'systemctl', f'echo "$*" >> "{self.dir}/systemctl"')

    def answer(self, **values):
        current = {}
        if self.answers.exists():
            for line in self.answers.read_text().splitlines():
                key, value = line.split('=', 1)
                current[key] = value
        current.update({k: "'" + v + "'" for k, v in values.items()})
        self.answers.write_text(''.join(f'{k}={v}\n' for k, v in current.items()))

    def run_helper(self):
        (self.dir / 'systemctl').unlink(missing_ok=True)
        env = {'PATH': '/usr/bin:/bin'}
        for name in ('loginctl', 'getent', 'setpriv', 'gdbus', 'busctl', 'systemctl'):
            env['LIUQIN_' + name.upper()] = str(self.bin / name)
        env.update(LIUQIN_RUNTIME_BASE=str(self.dir / 'run/user'), LIUQIN_STATE_DIR=str(self.state),
                   LIUQIN_WAKEUP_DIR=str(self.wakeup))
        result = subprocess.run(['sh', str(HELPER), 'resuspend'], env=env, capture_output=True,
                                text=True, timeout=30)
        suspended = (self.dir / 'systemctl').exists()
        return result.returncode, suspended

    def test_locked_dark_idle_session_suspends_again(self):
        self.assertEqual(self.run_helper(), (0, True))
        self.assertEqual((self.state / 'display-state').read_text(), 'suspend-requested\n')
        self.assertEqual((self.dir / 'systemctl').read_text(), 'suspend\n')

    def test_not_yet_dark_or_idle_waits(self):
        self.answer(Get='(<0>,)')
        self.assertEqual(self.run_helper(), (75, False))
        self.answer(Get='(<3>,)', GetIdletime='(uint64 5000,)')
        self.assertEqual(self.run_helper(), (75, False))
        self.assertFalse((self.state / 'display-state').exists())

    def test_reasons_to_stay_awake(self):
        for change, label in ((dict(GetActive='(false,)'), 'unlocked'),
                              (dict(IsInhibited='(true,)'), 'session inhibitor'),
                              (dict(BlockInhibited='s "sleep:handle-lid-switch"'), 'logind sleep block'),
                              (dict(Class='greeter'), 'login screen')):
            with self.subTest(label):
                saved = self.answers.read_text()
                self.answer(**change)
                self.assertEqual(self.run_helper(), (3, False))
                self.answers.write_text(saved)
        # A wired charger holds the kernel source: stay awake to keep fast charging.
        (self.wakeup / 'wakeup3/active_time_ms').write_text('4500\n')
        self.assertEqual(self.run_helper(), (3, False))
        (self.wakeup / 'wakeup3/active_time_ms').write_text('0\n')
        self.assertEqual(self.run_helper(), (0, True))


if __name__ == '__main__':
    unittest.main()
