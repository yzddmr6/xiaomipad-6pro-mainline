#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check the MiPPS role-first ordering without a device or charger.

The test drives the daemon's main() against a temporary sysfs tree and a fake
charger, so it verifies ordering and fail-closed behaviour only.  It is not
protocol validation: real UVDM traffic and power measurements need hardware.
"""
import contextlib
import hashlib
import hmac
import importlib.machinery
import importlib.util
import io
import shlex
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

project = Path(__file__).resolve().parents[1]
daemon = project / 'device/gnome-overlay/usr/local/libexec/liuqin-mipps-auth'
loader = importlib.machinery.SourceFileLoader('liuqin_mipps_auth', str(daemon))
spec = importlib.util.spec_from_loader(loader.name, loader)
auth = importlib.util.module_from_spec(spec)
sys.modules[loader.name] = auth
loader.exec_module(auth)

CHALLENGE = 'ab' * 16
ADAPTER_ID = '0000a661'
SEED_INDEX = 3
ATTACH = 'port0-partner:1:2:3'
DEVICE_ROLE = 'host [device]'
HOST_ROLE = '[host] device'
original_write_node = auth.write_node
original_read_node = auth.read_node


class Fixture:
    """A fake sysfs tree plus the charger behaviour for one attach."""

    def __init__(self, directory):
        self.root = Path(directory) / 'xiaomi'
        self.root.mkdir()
        self.port = Path(directory) / 'typec'
        self.port.mkdir()
        (self.port / 'data_role').write_text(DEVICE_ROLE + '\n')
        self.state = Path(directory) / 'run/verified-attach'
        self.state.parent.mkdir(parents=True)
        self.events = []
        self.online = True
        self.attach = ATTACH
        self.disconnect_at = None
        self.clock = 0.0
        self.seed_index = None
        self.vdm_failure = None
        self.role_failure = False
        self.nodes = {
            'request_vdm_cmd': '0,Null',
            'real_type': 'PD_PPS',
            'current_state': 'SNK_Ready',
            'adapter_svid': '2717',
            'adapter_id': ADAPTER_ID,
            'pdo2': '0002d12c',
            'authentic': '1',
            'slave_authentic': '1',
            'pd_verifed': '0',
            'fastchg_mode': '0',
            'apdo_max': '30',
            'power_max': '0',
        }
        self.write_nodes()
        self.install_patches()

    def write_nodes(self):
        for name, value in self.nodes.items():
            (self.root / name).write_text(value + '\n')

    def set(self, name, value):
        self.nodes[name] = value
        (self.root / name).write_text(value + '\n')

    def role(self):
        return (self.port / 'data_role').read_text().strip()

    def vdm_calls(self):
        return [event[1] for event in self.events if event[0] == 'vdm']

    def install_patches(self):
        auth.VERIFIED_STATE_FILE = str(self.state)
        auth.NOTIFICATION_STATE_FILE = str(self.state.parent / 'notification')
        auth.CHARGE_PROFILE_STATE_FILE = str(self.state.parent / 'profile')
        auth.USB_ONLINE_STATE_FILE = str(self.state.parent / 'usb-online')
        def tick():
            self.clock += 0.1
            return self.clock
        auth.time.monotonic = tick
        auth.time.sleep = lambda seconds: setattr(self, 'clock', self.clock + seconds)
        auth.secrets.token_hex = lambda _length: CHALLENGE
        auth.find_typec_port = lambda: str(self.port)
        auth.usb_is_online = lambda: self.online
        auth.current_attach_token = lambda: self.attach
        auth.wait_usb_online = lambda _timeout: self.online
        auth.wait_real_type = lambda _root, _timeout: self.nodes['real_type']
        auth.notify_completed_charge = lambda *_args, **_kwargs: False

        def enable_battery_auth(_root):
            self.events.append(('fuel-gauge',))

        def write_node(root, name, value):
            if name != 'request_vdm_cmd':
                return original_write_node(root, name, value)
            cmd, payload = value.split(',', 1)
            cmd = int(cmd)
            self.events.append(('vdm', cmd))
            if self.disconnect_at == cmd:
                self.online = False
                self.attach = None
            if self.vdm_failure:
                if self.vdm_failure == 'once':
                    self.vdm_failure = None
                return
            if cmd == 4:
                self.seed_index = auth.SEEDS.index(payload)
            response = self.expected_digest()[:32] if cmd == 5 else ('0' if cmd <= 3 else 'Null')
            original_write_node(root, name, f'{cmd},{response}')

        def request_host(_port, _timeout):
            self.events.append(('role', self.role()))
            if self.role_failure:
                return 1, 'role request refused'
            (self.port / 'data_role').write_text(HOST_ROLE + '\n')
            return 0, ''

        auth.enable_battery_auth = enable_battery_auth
        auth.write_node = write_node
        auth.request_host_data_role = request_host

    def expected_digest(self):
        message = bytes.fromhex(CHALLENGE + ADAPTER_ID)
        key = bytes.fromhex(auth.KEYS[self.seed_index])
        return hmac.new(key, message, hashlib.sha256).hexdigest()

    def run_main(self, *extra):
        unit = (project / 'device/gnome-overlay/etc/systemd/system/liuqin-mipps-auth.service').read_text()
        command = unit.split('/usr/local/libexec/liuqin-mipps-auth ', 1)[1].split('\n', 1)[0]
        auth.sys.argv = ['liuqin-mipps-auth', *shlex.split(command),
                         '--sysfs', str(self.root), *extra]
        log = io.StringIO()
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            auth.main()
        return log.getvalue()

    def state_value(self):
        return self.state.read_text().strip() if self.state.exists() else None


def check(name, condition, detail=''):
    assert condition, f'{name}: {detail}'
    print(f'PASS {name}')


with tempfile.TemporaryDirectory() as directory:
    fixture = Fixture(directory)
    fixture.run_main()
    check('the host role is requested once before the handshake',
          [event for event in fixture.events if event[0] == 'role'],
          fixture.events)
    check('the host role is held before any adapter command',
          fixture.role() == HOST_ROLE and fixture.vdm_calls(), fixture.role())
    check('the completion follows Android 7 -> 6 without command 8',
          fixture.vdm_calls() == [1, 2, 3, 4, 5, 7, 6], fixture.vdm_calls())
    check('an accepted authentication asserts the gate',
          (fixture.root / 'pd_verifed').read_text().strip() == '1')
    check('the verified adapter is bound to this attachment',
          fixture.state_value() == 'mipps ' + ATTACH)

with tempfile.TemporaryDirectory() as directory:
    fixture = Fixture(directory)
    fixture.run_main()
    fixture.events.clear()
    (fixture.port / 'data_role').write_text(HOST_ROLE + '\n')
    fixture.run_main()
    check('a verified attach is not replayed', not fixture.events)

with tempfile.TemporaryDirectory() as directory:
    fixture = Fixture(directory)
    fixture.vdm_failure = 'once'
    log = fixture.run_main()
    check('a failed handshake retries and authenticates on the same attach',
          fixture.vdm_calls() == [1, 1, 2, 3, 4, 5, 7, 6] and
          fixture.state_value() == 'mipps ' + ATTACH and 'waiting for VDM state 1' in log)

with tempfile.TemporaryDirectory() as directory:
    fixture = Fixture(directory)
    fixture.role_failure = True
    fixture.run_main()
    check('a refused role request sends no adapter command',
          not fixture.vdm_calls() and [e[0] for e in fixture.events] == ['role'] * 3,
          fixture.events)
    check('a refused role request leaves the gate closed', not auth.pd_verified(fixture.root))

with tempfile.TemporaryDirectory() as directory:
    fixture = Fixture(directory)
    fixture.set('current_state', 'SNK_Startup')
    fixture.set('real_type', 'Unknown')
    def late_contract():
        if fixture.clock > 40:
            fixture.set('real_type', 'PD_PPS')
            fixture.set('current_state', 'SNK_Ready')
        return fixture.online
    auth.usb_is_online = late_contract
    fixture.run_main()
    check('PD and SNK_Ready arriving after 40 seconds still authenticate',
          fixture.clock > 40 and fixture.state_value() == 'mipps ' + ATTACH)

with tempfile.TemporaryDirectory() as directory:
    fixture = Fixture(directory)
    fixture.set('adapter_svid', '1234')
    fixture.run_main()
    check('a non-Xiaomi source keeps the negotiated data role',
          fixture.role() == DEVICE_ROLE and not [e for e in fixture.events
                                                 if e[0] == 'role'],
          fixture.role())
    check('a non-Xiaomi source is still probed best-effort',
          fixture.vdm_calls()[:5] == [1, 2, 3, 4, 5], fixture.vdm_calls())

with tempfile.TemporaryDirectory() as directory:
    fixture = Fixture(directory)
    fixture.vdm_failure = 'charger stopped answering'
    fixture.run_main('--no-allow-unverified-adapter')
    check('strict policy leaves a silent charger gate closed',
          (fixture.root / 'pd_verifed').read_text().strip() == '0')

with tempfile.TemporaryDirectory() as directory:
    fixture = Fixture(directory)
    fixture.set('authentic', '0')
    fixture.set('slave_authentic', '0')
    fixture.run_main()
    check('the fuel gauges are authenticated before the role request',
          [event[0] for event in fixture.events[:2]] == ['fuel-gauge', 'role'],
          fixture.events[:3])

with tempfile.TemporaryDirectory() as directory:
    fixture = Fixture(directory)
    fixture.online = False
    fixture.run_main()
    check('an offline port sends no adapter command', not fixture.events)

with tempfile.TemporaryDirectory() as directory:
    fixture = Fixture(directory)
    # PPS APDO, 3.3-11 V, max current 60 * 50 mA. SVID uses kernel %04x.
    fixture.set('pdo2', 'c0dc213c')
    fixture.set('adapter_svid', '0000')
    fixture.vdm_failure = 'silent'
    cable = fixture.port / 'port0-cable'
    (cable / 'identity').mkdir(parents=True)
    (cable / 'identity/product_type_vdo1').write_text('0x00000020\n')
    glob = auth.glob.glob
    def cable_glob(pattern):
        return [str(cable)] if pattern == '/sys/class/typec/port*-cable' else glob(pattern)
    with patch.object(auth.glob, 'glob', cable_glob):
        log = fixture.run_main()
    check('standard PPS accepts a 3 A advertisement after bounded retries',
          fixture.vdm_calls() == [1, 1, 1] and fixture.role() == DEVICE_ROLE and
          fixture.state_value() == 'pps ' + ATTACH and 'pd_auth=0' in log and
          auth.pd_verified(fixture.root))
    fixture.events.clear()
    fixture.run_main()
    check('duplicate event preserves the PPS verdict', not fixture.events)

for cmd in (1, 4, 5, 7, 6):
    with tempfile.TemporaryDirectory() as directory:
        fixture = Fixture(directory)
        fixture.disconnect_at = cmd
        fixture.set('pd_verifed', '1')
        try:
            fixture.run_main()
        except auth.UnsupportedChargerError:
            pass
        check(f'detach during command {cmd} clears the verdict',
              not auth.pd_verified(str(fixture.root)))
        check(f'detach during command {cmd} stops subsequent writes',
              fixture.vdm_calls()[-1] == cmd, fixture.vdm_calls())

for failure in ('offline', 'not-ready', 'role-refused', 'no-partner', 'fixed-pd', 'no-pdo'):
    with tempfile.TemporaryDirectory() as directory:
        fixture = Fixture(directory)
        fixture.set('pd_verifed', '1')
        fixture.set('request_vdm_cmd', '6,Null')
        if failure == 'offline': fixture.online = False
        if failure in ('not-ready', 'fixed-pd'):
            fixture.set('real_type', 'PD')
            fixture.set('current_state', 'SNK_Startup' if failure == 'not-ready' else 'SNK_Ready')
            fixture.vdm_failure = 'silent'
            auth.usb_is_online = lambda: fixture.clock < 80
        if failure == 'no-pdo':
            fixture.set('pdo2', '00000000')
            auth.usb_is_online = lambda: fixture.clock < 80
        if failure == 'role-refused': fixture.role_failure = True
        if failure == 'no-partner': fixture.attach = None
        try:
            fixture.run_main()
        except auth.UnsupportedChargerError:
            pass
        if failure in ('no-pdo', 'not-ready'):
            check(f'{failure} waits until unplug without commands', not fixture.events)
        if failure == 'fixed-pd':
            check('fixed PD never asserts the third-party gate', 'pps ' + ATTACH != fixture.state_value()
                  and fixture.vdm_calls() == [1, 1, 1])
        check(f'{failure} clears stale success', not auth.pd_verified(str(fixture.root)))

with tempfile.TemporaryDirectory() as directory:
    fixture = Fixture(directory)
    fixture.set('pd_verifed', '1')
    fixture.set('request_vdm_cmd', '6,Null')
    fixture.run_main()
    check('stale completed VDM without this attach proof is reauthenticated',
          fixture.vdm_calls() == [1, 2, 3, 4, 5, 7, 6])
    fixture.events.clear()
    fixture.attach = 'port0-partner:1:3:4'
    fixture.run_main()
    check('a new attach does not inherit success', bool(fixture.vdm_calls()))

with tempfile.TemporaryDirectory() as directory:
    fixture = Fixture(directory)
    fixture.set('adapter_svid', '1234')
    reads = []
    def read_node(root, name):
        reads.append(name)
        if name == 'current_state': fixture.set('adapter_svid', '2717')
        return original_read_node(root, name)
    auth.read_node = read_node
    fixture.run_main()
    auth.read_node = original_read_node
    check('SVID and data role are read after the negotiated contract',
          reads.index('current_state') < reads.index('adapter_svid') < reads.index('data_role'))

with tempfile.TemporaryDirectory() as directory:
    fixture = Fixture(directory)
    fixture.set('authentic', '0')
    fixture.set('slave_authentic', '0')
    def detach_in_battery(_root):
        fixture.online = False
        fixture.attach = 'different-partner'
    auth.enable_battery_auth = detach_in_battery
    try:
        fixture.run_main()
    except auth.UnsupportedChargerError:
        pass
    check('detach during fuel-gauge work cannot swap the new partner role',
          fixture.role() == DEVICE_ROLE and not fixture.events and not auth.pd_verified(str(fixture.root)))

with tempfile.TemporaryDirectory() as directory:
    fixture = Fixture(directory)
    fixture.set('real_type', 'PD')
    fixture.set('adapter_svid', '0000')
    fixture.vdm_failure = 'silent'
    def delayed_pps():
        if fixture.clock > 90:
            fixture.set('real_type', 'PD_PPS')
        return fixture.online
    auth.usb_is_online = delayed_pps
    fixture.run_main()
    check('fixed PD later upgraded to PPS retries without a new attach',
          fixture.clock > 90 and fixture.state_value() == 'pps ' + ATTACH)

with tempfile.TemporaryDirectory() as directory:
    fixture = Fixture(directory)
    fixture.set('adapter_svid', '0000')
    def fail_gauge(_root):
        raise RuntimeError('slave_authentic digest verification failed')
    auth.enable_battery_auth = fail_gauge
    log = fixture.run_main()
    check('battery authentication failure blocks standard PPS',
          not auth.pd_verified(fixture.root) and not fixture.vdm_calls() and
          'auth_attempt=3/3 failed: slave_authentic' in log)
