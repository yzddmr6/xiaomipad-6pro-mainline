#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Log in to a liuqin running developer mode over its USB NCM link.

The host interface is found by the fixed host MAC of device/devmode-network.json
and the USB device above it (Linux development IDs 1d6b:1041, serial
liuqin-*), as for the debug channel.  The device hands the host its address by
DHCP; if the host has not taken it, the ip commands that set it are printed.
Then `ssh USER@DEVICE_IP [COMMAND...]` replaces this process.  Nothing else
is sent to the device.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

NETWORK = Path(__file__).resolve().parents[1] / 'device/devmode-network.json'
SYS_NET = Path('/sys/class/net')
USB_ID = ('1d6b', '1041')


def load_network(path=NETWORK):
    network = json.loads(Path(path).read_text())
    for key in ('device_ip', 'host_ip', 'device_mac', 'host_mac'):
        if not isinstance(network.get(key), str):
            raise ValueError(f'{path}: missing {key}')
    return network


def usb_identity(netdev):
    """(idVendor, idProduct, serial) of the USB device above a network interface."""
    for parent in (netdev / 'device').resolve().parents:
        if (parent / 'idVendor').exists():
            return tuple((parent / name).read_text().strip() for name in ('idVendor', 'idProduct', 'serial'))
    return None


def find_interfaces(network, serial=None, sys_net=SYS_NET):
    """Host interfaces of liuqin NCM links; serial is the androidboot serial number."""
    found = []
    for netdev in sorted(Path(sys_net).iterdir()):
        try:
            if (netdev / 'address').read_text().strip() != network['host_mac']:
                continue
            identity = usb_identity(netdev)
        except (FileNotFoundError, NotADirectoryError):
            continue  # An unplug can remove attributes between the reads.
        if not identity or identity[:2] != USB_ID:
            continue
        if identity[2] == f'liuqin-{serial}' if serial else identity[2].startswith('liuqin-'):
            found.append((netdev.name, identity[2]))
    return found


def route_ready(interface, network, run=subprocess.run):
    """True when the host reaches device_ip through interface from host_ip."""
    result = run(['ip', '-j', 'route', 'get', network['device_ip']], capture_output=True, text=True, timeout=5)
    if result.returncode != 0:
        return False
    return any(r.get('dev') == interface and r.get('prefsrc') == network['host_ip']
               for r in json.loads(result.stdout))


def host_commands(interface, network):
    return [f'sudo ip link set dev {interface} up',
            f'sudo ip address replace {network["host_ip"]}/24 dev {interface}']


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('-i', '--identity', type=Path, help='SSH private key matching the provisioned public key')
    ap.add_argument('--serial', help='androidboot serial number, when more than one liuqin is attached')
    ap.add_argument('--wait', type=float, default=20, metavar='SECONDS',
                    help='how long to wait for the link and the DHCP address (default 20, at most 300)')
    ap.add_argument('--check', action='store_true', help='only report whether the link is ready; no ssh')
    ap.add_argument('-t', '--tty', action='store_true', help='pass -t to ssh, e.g. for sudo with a password prompt')
    ap.add_argument('--network', type=Path, default=NETWORK, help=argparse.SUPPRESS)
    ap.add_argument('user', nargs='?', help='local account on the tablet')
    ap.add_argument('command', nargs=argparse.REMAINDER, help='remote command (default: login shell)')
    args = ap.parse_args(argv)
    if args.command[:1] == ['--']:  # kept by some Python versions, dropped by others
        args.command = args.command[1:]
    if not 0 <= args.wait <= 300:
        ap.error('--wait must be between 0 and 300 seconds')
    if args.check:
        if args.user or args.command:
            ap.error('--check takes no user or command')
    elif not args.user:
        ap.error('a user on the tablet is required')
    elif args.user.startswith('-') or '@' in args.user or not args.user.strip():
        ap.error(f'not a user name: {args.user!r}')
    return args


def main(argv=None, sys_net=SYS_NET, run=subprocess.run, execvp=os.execvp, clock=time.monotonic,
         sleep=time.sleep, euid=os.geteuid):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    network = load_network(args.network)
    deadline = clock() + args.wait
    while True:
        found = find_interfaces(network, args.serial, sys_net)
        if len(found) > 1:
            print('liuqin-devmode-ssh: more than one liuqin NCM link: ' +
                  ', '.join(f'{name} ({serial})' for name, serial in found) + '; choose one with --serial',
                  file=sys.stderr)
            return 1
        ready = bool(found) and route_ready(found[0][0], network, run)
        if ready or clock() >= deadline:
            break
        sleep(0.5)
    if not found:
        print(f'liuqin-devmode-ssh: no liuqin NCM link (host MAC {network["host_mac"]}, USB {":".join(USB_ID)}); '
              'is the cable connected and developer mode on (liuqin-devmode status on the tablet)?', file=sys.stderr)
        return 1
    interface, serial = found[0]
    if not ready:
        commands = host_commands(interface, network)
        if euid() == 0:
            for command in commands:
                run(command.split()[1:], check=True)
            ready = route_ready(interface, network, run)
        if not ready:
            print(f'liuqin-devmode-ssh: {interface} ({serial}) has no address for {network["device_ip"]}; '
                  'the host did not take the DHCP lease. Set it with:', file=sys.stderr)
            for command in commands:
                print('  ' + command, file=sys.stderr)
            return 2
    if args.check:
        print(f'{interface} {serial} {network["host_ip"]} -> {network["device_ip"]} ready')
        return 0
    identity = args.identity
    if identity is None:
        default = Path.home() / '.ssh/id_ed25519'
        identity = default if default.exists() else Path.home() / '.ssh/liuqin-devmode'
    ssh = ['ssh'] + (['-i', str(identity)] if args.identity is not None or identity.exists() else []) + (['-t'] if args.tty else []) + [f'{args.user}@{network["device_ip"]}'] + args.command
    execvp('ssh', ssh)
    return 1


if __name__ == '__main__':
    sys.exit(main())
