#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run the real charger policy against disposable sysfs and real key classifier."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class Charger(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.key = Path(cls.build.name) / 'power-key'
        subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-O2',
                        str(ROOT/'device/charger-mode/liuqin-charger-mode-power-key.c'),
                        '-o', str(cls.key)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = Path(self.tmp.name)
        for d in ('dev','bat','usb','backlight','status'): (self.p/d).mkdir()
        for name,value in {'cmdline':'androidboot.mode=charger\n', 'uptime':'0.00 0.00\n',
                           'dev/sda':'', 'bat/capacity':'45\n','bat/voltage_now':'3800000\n',
                           'bat/charge_counter':'1000000\n','bat/current_now':'200000\n',
                           'bat/status':'Charging\n','usb/online':'1\n',
                           'backlight/max_brightness':'2047\n','backlight/brightness':'0\n',
                           'backlight/bl_power':'4\n'}.items(): (self.p/name).write_text(value)
        self.env = dict(os.environ, FIXTURE=str(self.p), KEY_ACTION='2')
        for key,path in {'CMDLINE_FILE':'cmdline','DEV_ROOT':'dev','BAT_ROOT':'bat',
                         'USB_ROOT':'usb','BACKLIGHT':'backlight','STATUS_DIR':'status',
                         'UPTIME_FILE':'uptime','LOG_FILE':'log','PMSG_FILE':'no-pmsg',
                         'SCREEN_TOOL':'no-screen','USB_TOOL':'usb-start',
                         'KEY_TOOL':'key','EXIT_TOOL':'exit','BLOCKDEV':'blockdev'}.items():
            self.env['LIUQIN_CHARGER_'+key] = str(self.p/path)
        self.env.update(LIUQIN_CHARGER_TEST_ALLOW_REGULAR='1',
                        LIUQIN_CHARGER_MAX_TICKS='1', LIUQIN_CHARGER_INTERVAL='1')
        self.script('blockdev', 'case "$1" in --setro) exit 0;; --getro) echo "${UFS_RO:-1}";; esac')
        self.script('usb-start', 'echo usb >> "$FIXTURE/actions"')
        self.script('exit', 'echo "exit $1" >> "$FIXTURE/actions"')
        self.script('key', 'exit "$KEY_ACTION"')

    def script(self,name,body):
        p=self.p/name; p.write_text('#!/bin/sh\n'+body+'\n'); p.chmod(0o755)

    def run_charger(self):
        subprocess.run(['sh',str(ROOT/'device/charger-mode/liuqin-charger-mode')],
                       env=self.env, check=True, capture_output=True, timeout=5)
        return json.loads((self.p/'status/status.json').read_text())

    def actions(self):
        p=self.p/'actions'; return p.read_text().splitlines() if p.exists() else []

    def test_short_press_fresh_charge_gate(self):
        self.env['KEY_ACTION']='0'
        for cap,volts,allowed in [('45','3800000',True),('39','3800000',False),
                                  ('45','3699999',False),('NA','3800000',False),
                                  ('45','NA',False),('101','3800000',False)]:
            with self.subTest(capacity=cap,voltage=volts):
                (self.p/'actions').unlink(missing_ok=True)
                (self.p/'bat/capacity').write_text(cap+'\n')
                (self.p/'bat/voltage_now').write_text(volts+'\n')
                result=self.run_charger()
                self.assertEqual('exit normal' in self.actions(),allowed)
                self.assertEqual(result['can_boot'], 'yes' if allowed else 'no')
        self.assertEqual((self.p/'backlight/brightness').read_text().strip(),'102')
        self.assertEqual((self.p/'backlight/bl_power').read_text().strip(),'0')

    def test_charge_is_reread_after_key(self):
        self.script('key','echo 2 > "$FIXTURE/bat/capacity"; exit 0')
        self.run_charger()
        self.assertNotIn('exit normal',self.actions())

    def test_storage_failure_blocks_normal_and_usb(self):
        self.env.update(KEY_ACTION='0',UFS_RO='0')
        result=self.run_charger()
        self.assertEqual(result['ufs'],'failed')
        self.assertEqual(self.actions(),[])

    def test_low_or_unknown_charge_still_allows_fastboot(self):
        self.env.update(KEY_ACTION='10',UFS_RO='0')
        (self.p/'bat/capacity').unlink()
        self.run_charger()
        self.assertEqual(self.actions(),['exit bootloader'])

    def test_net_gain_requires_counter_increase_and_external_power(self):
        self.env['LIUQIN_CHARGER_MAX_TICKS']='2'
        for counter,online,trend in [('1002000','1','gaining'),('999000','1','not-gaining'),
                                     ('1000000','1','not-gaining'),('1002000','0','not-gaining'),
                                     ('NA','1','unknown')]:
            with self.subTest(counter=counter,online=online):
                (self.p/'uptime').write_text('0.0 0.0\n')
                (self.p/'bat/charge_counter').write_text('1000000\n')
                (self.p/'usb/online').write_text('1\n')
                self.script('key', f'echo 61.0 > "$FIXTURE/uptime"\necho {counter} > "$FIXTURE/bat/charge_counter"\necho {online} > "$FIXTURE/usb/online"\nexit 2')
                self.assertEqual(self.run_charger()['trend'],trend)

    def test_real_key_short_hold_and_no_release(self):
        for script,timeout,expected in [('press 3500\nrelease 3600\n',30000,0),
                                        ('press 3500\nwait 5500\n',30000,10),
                                        ('press 3500\nrelease 12000\n',30000,10),
                                        ('press 3500\nwait 5500\n',4000,10),
                                        ('press 0\nrelease 100\n',30000,2),
                                        ('press 3500\nrelease 3510\n',30000,2)]:
            with self.subTest(script=script):
                p=self.p/'events';p.write_text(script)
                r=subprocess.run([str(self.key),'--test-script',str(p),
                                  '--timeout-ms',str(timeout)],capture_output=True)
                self.assertEqual(r.returncode,expected,r.stderr)

if __name__=='__main__': unittest.main()
