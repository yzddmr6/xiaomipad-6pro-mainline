#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run the packaged charge display, sampler and idle helper against a disposable liuqin sysfs."""
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LIBEXEC = ROOT / 'device/charge-policy/usr/libexec'


class LiuqinCharge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.display = runpy.run_path(str(LIBEXEC / 'tablet-charge-display'))
        cls.sampler = runpy.run_path(str(LIBEXEC / 'tablet-charge-sample'))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sys = Path(self.tmp.name) / 'sys'
        self.run_dir = Path(self.tmp.name) / 'run'
        self.usb = self.sys / 'class/power_supply/qcom-battmgr-usb'
        self.bat = self.sys / 'class/power_supply/qcom-battmgr-bat'
        self.xm = self.sys / 'devices/platform/pmic-glink/pmic_glink.power-supply.0/xiaomi'
        self.partner = self.sys / 'class/typec/port0-partner'
        self.partner.mkdir(parents=True)
        self.put(self.usb, online=1, usb_type='Unknown SDP DCP CDP ACA C PD PD_DRP [PD_PPS] BrickID',
                 voltage_now=9100000, current_now=200000)
        # 8600 mAh pack at 50%: 4.0 V x 8.6 Ah x 0.5 = 17.2 Wh to go.
        self.put(self.bat, voltage_now=4000000, current_now=11250000, temp=290, capacity=50,
                 charge_full=8600000, status='Charging')
        self.put(self.xm, real_type='PD_PPS', pd_verifed=1, fastchg_mode=1, authentic=1,
                 slave_authentic=1, current_state='SNK_Ready', apdo_max=45, power_max=67,
                 bq2597x_bus_voltage=9100, bq2597x_bus_current=3000,
                 bq2597x_slave_bus_current=2800)
        self.put(self.xm.parent / 'sm8475_raw', raw_xm_connector_temp=2**32 - 25)
        self.wake('qcom-battmgr-charge', 4500)
        self.verdict('mipps')

    def put(self, directory, **values):
        directory.mkdir(parents=True, exist_ok=True)
        for name, value in values.items():
            path = directory / name
            if value is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(f'{value}\n')

    def wake(self, name, active):
        node = self.sys / 'class/wakeup/wakeup17'
        self.put(node, name=name, active_time_ms=active)
        self.put(self.sys / 'class/wakeup/wakeup2', name='unrelated', active_time_ms=120)

    def verdict(self, kind, token=None):
        st = self.partner.stat()
        token = token or f'port0-partner:{st.st_dev}:{st.st_ino}:{st.st_ctime_ns}'
        self.put(self.run_dir / 'liuqin-mipps-auth', **{'verified-attach': f'{kind} {token}'})

    def snapshot(self, on_battery=False):
        return self.display['snapshot'](self.sys, 'liuqin', on_battery, self.run_dir)

    def test_mipps_adds_both_pumps_and_hides_unexported_rows(self):
        row = self.snapshot()
        self.assertEqual(row['path'], 'MiPPS')
        self.assertAlmostEqual(row['volts'], 9.1)
        # 200 mA USB input plus 3.0 A and 2.8 A on the two charge pumps.
        self.assertAlmostEqual(row['amps'], 6.0)
        self.assertAlmostEqual(row['watts'], 45.0)
        self.assertEqual(row['minutes'], 23)
        self.assertIs(row['awake'], True)
        for hidden in ('exits', 'reason', 'secondary', 'request_v', 'request_a'):
            self.assertNotIn(hidden, row)

    def test_standard_pps_and_verdicts_for_another_partner(self):
        self.verdict('pps')
        self.assertEqual(self.snapshot()['path'], 'PPS')
        # A verdict recorded for the previous partner is not this charge.
        self.verdict('mipps', token='port0-partner:1:2:3')
        self.assertEqual(self.snapshot()['path'], 'PPS')
        # The same partner node with a re-read inode time is still this charge.
        st = self.partner.stat()
        self.verdict('mipps', token=f'port0-partner:{st.st_dev}:{st.st_ino}:1')
        self.assertEqual(self.snapshot()['path'], 'MiPPS')
        self.verdict('mipps', token=f'port0-partner:{st.st_dev}:{st.st_ino + 1}:{st.st_ctime_ns}')
        self.assertEqual(self.snapshot()['path'], 'PPS')
        (self.run_dir / 'liuqin-mipps-auth/verified-attach').write_text('none\n')
        self.assertEqual(self.snapshot()['path'], 'PPS')
        # Before the gate opens the firmware charges at the fixed PD level.
        self.put(self.xm, pd_verifed=0, fastchg_mode=0)
        self.assertEqual(self.snapshot()['path'], 'PD')

    def test_fixed_pd_counts_only_the_buck(self):
        self.put(self.usb, usb_type='Unknown SDP DCP CDP ACA C [PD] PD_DRP PD_PPS BrickID',
                 voltage_now=9000000, current_now=1400000)
        self.put(self.xm, real_type='PD', pd_verifed=0, fastchg_mode=0,
                 bq2597x_bus_current=39, bq2597x_slave_bus_current=39)
        row = self.snapshot()
        self.assertEqual(row['path'], 'PD')
        self.assertAlmostEqual(row['volts'], 9.0)
        self.assertAlmostEqual(row['amps'], 1.4)
        # The firmware's fast mode on a PD contract is the PPS path the authenticator reports.
        self.put(self.xm, pd_verifed=1, fastchg_mode=1)
        self.verdict('mipps')
        self.assertEqual(self.snapshot()['path'], 'MiPPS')

    def test_computer_port_and_quick_charge(self):
        # rc3 periodic samples on a host port: idle pumps read 39 mA each.
        self.put(self.usb, usb_type='Unknown [SDP] DCP CDP ACA C PD PD_DRP PD_PPS BrickID',
                 voltage_now=5070000, current_now=400000)
        self.put(self.bat, current_now=-107000)
        self.put(self.xm, real_type='SDP', pd_verifed=0, fastchg_mode=0, current_state='SNK_Startup',
                 bq2597x_bus_voltage=5024, bq2597x_bus_current=39, bq2597x_slave_bus_current=39)
        self.wake('qcom-battmgr-charge', 0)
        row = self.snapshot()
        self.assertEqual(row['path'], 'USB')
        self.assertAlmostEqual(row['volts'], 5.07)
        self.assertAlmostEqual(row['amps'], 0.4)
        self.assertLess(row['watts'], 0)
        self.assertIsNone(row['minutes'])
        self.assertIs(row['awake'], False)
        self.put(self.xm, real_type='HVDCP_3')
        self.assertEqual(self.snapshot()['path'], 'QC')

    def test_missing_firmware_nodes_and_wakeup_source(self):
        shutil.rmtree(self.xm.parent)
        shutil.rmtree(self.sys / 'class/wakeup/wakeup17')
        row = self.snapshot()
        # The kernel's USB type is the fallback; the row shows only what exists.
        self.assertEqual(row['path'], 'PD')
        self.assertAlmostEqual(row['amps'], 0.2)
        self.assertNotIn('awake', row)
        self.assertNotIn('exits', row)

    def test_offline_and_battery_display(self):
        self.put(self.usb, online=0)
        self.put(self.bat, current_now=-900000, status='Discharging')
        self.assertEqual(self.snapshot(), {'device': 'liuqin', 'online': 0,
                                           'partner': self.partner.stat().st_ino})
        row = self.snapshot(on_battery=True)
        self.assertIsNone(row['path'])
        self.assertAlmostEqual(row['watts'], -3.6)
        self.assertNotIn('exits', row)

    def test_sampler_records_the_authenticator_and_signed_connector_temperature(self):
        row = self.sampler['sample'](self.sys, 'liuqin', 'periodic')
        pump = row['pump']
        self.assertEqual(pump['auth'], None)  # the sampler reads the live /run
        self.assertEqual(pump['connector_temp_decic'], -25)
        self.assertEqual((pump['real_type'], pump['pd_verifed'], pump['power_max']), ('PD_PPS', '1', '67'))
        state = self.sampler['liuqin_charger'](self.sys, self.run_dir)
        self.assertEqual(state['auth'], 'mipps')

    def sandbox(self, script, *binds, args=(), env=()):
        """Run a packaged entry point with the board file it reads on the tablet."""
        bwrap = shutil.which('bwrap')
        if not bwrap:
            self.skipTest('bubblewrap is not installed')
        board = Path(self.tmp.name) / 'device'
        board.write_text('liuqin\n')
        cmd = [bwrap, '--unshare-all', '--die-with-parent', '--clearenv',
               '--ro-bind', '/usr/bin', '/usr/bin', '--ro-bind', '/usr/lib', '/usr/lib',
               '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib', '--dev', '/dev']
        if Path('/usr/lib64').is_dir():
            cmd += ['--ro-bind', '/usr/lib64', '/usr/lib64', '--symlink', 'usr/lib64', '/lib64']
        cmd += ['--ro-bind', str(LIBEXEC), '/libexec',
                '--ro-bind', str(board), '/usr/share/tablet-charge/device', *binds]
        for key, value in env:
            cmd += ['--setenv', key, value]
        return board, cmd + ['/usr/bin/python3', '-IB', '/libexec/' + script, *args]

    def test_packaged_display_entry_point(self):
        board, cmd = self.sandbox('tablet-charge-display',
                                  '--ro-bind', str(self.sys), '/fixture/sys',
                                  '--ro-bind', str(self.run_dir), '/fixture/run',
                                  env=(('TABLET_CHARGE_SYSFS', '/fixture/sys'),
                                       ('TABLET_CHARGE_RUN', '/fixture/run')))
        # The verdict names the partner as the tablet sees it, inside the sandbox too.
        out = json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout)
        self.assertEqual(out['path'], 'MiPPS')
        self.assertNotIn('exits', out)
        board.write_text('unknown\n')
        out = json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout)
        self.assertEqual(out, {})

    def test_idle_helper_follows_the_charging_wakeup_source(self):
        _, cmd = self.sandbox('tablet-charge-idle',
                              '--ro-bind', str(self.sys / 'class/wakeup'), '/sys/class/wakeup',
                              args=('--status',))
        for active, expected in (('4500', 'inhibit'), ('0', 'allow'), (None, 'allow')):
            with self.subTest(active_time_ms=active):
                self.put(self.sys / 'class/wakeup/wakeup17', active_time_ms=active)
                out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
                self.assertEqual(out.strip(), expected)
        # Only the wired-charging source counts; another active wakeup source does not.
        self.put(self.sys / 'class/wakeup/wakeup17', name='qcom-battmgr-bat', active_time_ms=4500)
        out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
        self.assertEqual(out.strip(), 'allow')


if __name__ == '__main__':
    unittest.main()
