#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Remove the unused distro boot stack, refusing unrelated dependency removals."""
import argparse
import re
import subprocess


def obsolete(name):
    return bool(re.match(r'linux-(?:generic(?:-|$)|image-|modules-|headers-|main-modules-zfs-)', name)
                or name in ('kdump-tools', 'zfs-dkms', 'flash-kernel', 'initramfs-tools-core')
                or name.startswith(('zfs-modules', 'dracut')))


def packages():
    text = subprocess.check_output(['dpkg-query', '-W', '-f=${Package}\t${Status}\n'], text=True)
    return dict(line.split('\t', 1) for line in text.splitlines())


def removal_plan(status, simulation):
    selected = sorted(p for p, state in status.items() if not state.endswith('not-installed') and obsolete(p))
    removals = re.findall(r'^(?:Remv|Purg) (\S+)', simulation, re.M)
    unexpected = [p for p in removals if not obsolete(p.split(':')[0])]
    if unexpected or re.search(r'^(?:Inst|Conf) ', simulation, re.M):
        raise RuntimeError('boot-stack purge changes unrelated packages: ' + ', '.join(unexpected))
    if set(p.split(':')[0] for p in removals) != set(selected):
        raise RuntimeError('APT removal plan differs from the selected boot stack')
    return selected


def verify(status):
    remaining = sorted(p for p, state in status.items()
                       if not state.endswith('not-installed') and obsolete(p))
    # This project names its sole kernel package liuqin-kernel, whose payload
    # owns /boot and /usr/lib/modules; it is not an Ubuntu linux-image-* alias.
    if remaining or status.get('liuqin-kernel') != 'install ok installed':
        raise RuntimeError('expected only liuqin-kernel, remaining distro boot packages: ' + ', '.join(remaining))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('purge', 'verify'))
    args = parser.parse_args()
    status = packages()
    if args.action == 'purge':
        selected = sorted(p for p, state in status.items()
                          if not state.endswith('not-installed') and obsolete(p))
        if selected:
            cmd = ['apt-get', '-y', 'purge', *selected]
            simulation = subprocess.check_output(cmd[:1] + ['--simulate'] + cmd[1:], text=True)
            print(simulation, flush=True)
            removal_plan(status, simulation)
            subprocess.run(cmd, check=True)
    else:
        verify(status)
        print('PASS: liuqin-kernel is the sole kernel; no distro initramfs, ZFS or kdump stack')


if __name__ == '__main__':
    main()
