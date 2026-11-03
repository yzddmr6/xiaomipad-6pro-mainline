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
import sys
import tempfile
from pathlib import Path

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


class Fixture:
    """A fake sysfs tree plus the charger behaviour for one attach."""

    def __init__(self, directory):
        self.root = Path(directory) / 'xiaomi'
        self.root.mkdir()
        self.port = Path(directory) / 'typec'
        self.port.mkdir()
        (self.port / 'data_role').write_text(DEVICE_ROLE + '\n')
        self.state = Path(directory) / 'run/role-first-attempt'
        self.state.parent.mkdir(parents=True)
        self.events = []
        self.online = True
        self.vdm_failure = None
        self.role_failure = False
        self.nodes = {
            'request_vdm_cmd': '0,',
            'real_type': 'PD_PPS',
            'current_state': 'SNK_Ready',
            'adapter_svid': '0x2717',
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
        auth.ROLE_FIRST_STATE_FILE = str(self.state)
        auth.ROLE_READY_TIMEOUT = 0.05
        auth.NOTIFICATION_STATE_FILE = str(self.state.parent / 'notification')
        auth.CHARGE_PROFILE_STATE_FILE = str(self.state.parent / 'profile')
        auth.USB_ONLINE_STATE_FILE = str(self.state.parent / 'usb-online')
        auth.time.sleep = lambda _seconds: None
        auth.secrets.token_hex = lambda _length: CHALLENGE
        auth.find_typec_port = lambda: str(self.port)
        auth.usb_is_online = lambda: self.online
        auth.current_attach_token = lambda: ATTACH
        auth.wait_usb_online = lambda _timeout: self.online
        auth.wait_real_type = lambda _root, _timeout: self.nodes['real_type']
        auth.detect_adapter_id = lambda _root, _timeout: ADAPTER_ID
        auth.notify_completed_charge = lambda *_args, **_kwargs: False

        def enable_battery_auth(_root):
            self.events.append(('fuel-gauge',))

        def send_vdm(_root, cmd, payload='null', **_kwargs):
            self.events.append(('vdm', cmd))
            if self.vdm_failure:
                raise auth.UnsupportedChargerError(self.vdm_failure)
            if cmd == 5:
                return cmd, self.expected_digest()[:32]
            return cmd, ''

        def request_host(_port, _timeout):
            self.events.append(('role', self.role()))
            if self.role_failure:
                return 1, 'role request refused'
            (self.port / 'data_role').write_text(HOST_ROLE + '\n')
            return 0, ''

        auth.enable_battery_auth = enable_battery_auth
        auth.send_vdm = send_vdm
        auth.request_host_data_role = request_host

    def expected_digest(self):
        message = bytes.fromhex(CHALLENGE + ADAPTER_ID)
        key = bytes.fromhex(auth.KEYS[SEED_INDEX])
        return hmac.new(key, message, hashlib.sha256).hexdigest()

    def run_main(self, *extra):
        auth.sys.argv = ['liuqin-mipps-auth', '--role-first', '--sysfs',
                         str(self.root), '--seed-index', str(SEED_INDEX),
                         '--timeout', '1', *extra]
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            auth.main()

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
    check('the attempt is remembered for the attach',
          fixture.state_value() == ATTACH)

with tempfile.TemporaryDirectory() as directory:
    fixture = Fixture(directory)
    fixture.set('pd_verifed', '1')
    (fixture.port / 'data_role').write_text(HOST_ROLE + '\n')
    fixture.run_main()
    check('a verified attach is not replayed', not fixture.events)

with tempfile.TemporaryDirectory() as directory:
    fixture = Fixture(directory)
    fixture.state.write_text(ATTACH + '\n')
    fixture.run_main()
    check('the same attach is attempted only once', not fixture.events)
    check('the remembered attempt keeps its token',
          fixture.state_value() == ATTACH)

with tempfile.TemporaryDirectory() as directory:
    fixture = Fixture(directory)
    fixture.role_failure = True
    fixture.run_main()
    check('a refused role request sends no adapter command',
          not fixture.vdm_calls() and [e[0] for e in fixture.events] == ['role'],
          fixture.events)
    check('a refused role request is remembered for the attach',
          fixture.state_value() == ATTACH)

with tempfile.TemporaryDirectory() as directory:
    fixture = Fixture(directory)
    fixture.set('current_state', 'SNK_Wait')
    fixture.run_main()
    check('an unsettled source sends no adapter command', not fixture.events)

with tempfile.TemporaryDirectory() as directory:
    fixture = Fixture(directory)
    fixture.set('adapter_svid', '0x1234')
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
    fixture.run_main()
    check('a silent charger leaves a closed gate',
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

print('Role-first ordering and fail-closed checks passed; not a protocol test.')
