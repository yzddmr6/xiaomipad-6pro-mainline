#!/usr/bin/env python3
"""Execute the shipped helper in a BusyBox-only root without /tmp."""
from pathlib import Path
import os, shutil, subprocess, tempfile
project = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as work:
    root = Path(work)
    for d in ('bin', 'etc', 'proc/self', 'sys/firmware/devicetree/base', 'sys/bus/platform/devices'):
        (root / d).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(shutil.which('busybox'), root / 'bin/busybox')
    (root / 'bin/busybox').chmod(0o755)
    for name in ('sh', 'tr', 'od', 'grep', 'sed', 'awk', 'readlink', 'cat'):
        (root / 'bin' / name).symlink_to('busybox')
    shutil.copyfile(project / 'device/platform/liuqin-physical-platform', root / 'policy')
    def script(name, text):
        p = root / 'bin' / name
        p.write_text('#!/bin/sh\n' + text + '\n'); p.chmod(0o755)
    script('mount', 'echo "$*" >>/calls\n[ ! -e /fail-mount ] || exit 1\necho "liuqin-physical-platform /sys/firmware/devicetree/base/hypervisor tmpfs ro 0 0" >>/proc/self/mounts')
    script('systemd-detect-virt', 'cat /detector; [ "$(cat /detector)" != none ]')
    script('systemctl', 'case "$1" in is-active) [ -e /booted ];; show) cat /manager;; esac')
    script('hostnamectl', 'cat /chassis')
    base = root / 'sys/firmware/devicetree/base'
    hyper = base / 'hypervisor'
    (hyper / 'qcom,gunyah-vm').mkdir(parents=True)
    (base / 'model').write_bytes(b'Liuqin based on Qualcomm Technologies, Inc SM8475\0')
    (base / 'compatible').write_bytes(b'qcom,cape-mtp\0qcom,cape\0qcom,mtp\0')
    (hyper / 'compatible').write_bytes(b'qcom,gunyah-hypervisor-1.0\0qcom,gunyah-hypervisor\0simple-bus\0')
    vmid = hyper / 'qcom,gunyah-vm/qcom,vmid'
    vmid.write_bytes(b'\0\0\0\3')
    (root / 'proc/self/mounts').write_text('')
    def run(ok, *args):
        result = subprocess.run(['sudo', '-n', 'chroot', str(root), '/bin/sh', '/policy', *args], capture_output=True, text=True)
        assert (result.returncode == 0) == ok, result.stdout + result.stderr
        assert not (root / 'tmp').exists()
    run(True)
    run(True)
    assert len((root / 'calls').read_text().splitlines()) == 1
    (root / 'proc/self/mounts').write_text('')
    vmid.write_bytes(b'\0\0\0\4'); run(False)
    vmid.write_bytes(b'\0\0\0\3')
    device = root / 'sys/bus/platform/devices/renamed-device'
    device.mkdir(); (device / 'driver').symlink_to('/bin')
    (device / 'of_node').symlink_to('/sys/firmware/devicetree/base/hypervisor/qcom,gunyah-vm')
    run(False); (device / 'driver').unlink()
    (root / 'fail-mount').touch(); run(False); (root / 'fail-mount').unlink()
    (base / 'model').write_bytes(b'Another tablet\0'); run(False)
    (root / 'etc/machine-info').write_text('CHASSIS=tablet\n')
    (root / 'detector').write_text('none\n'); (root / 'manager').write_text('')
    run(True, '--verify')
    (root / 'booted').touch(); (root / 'chassis').write_text('tablet\n'); run(True, '--verify')
    (root / 'chassis').write_text('vm\n'); run(False, '--verify')
    (root / 'chassis').write_text('tablet\n'); (root / 'manager').write_text('vm-other\n'); run(False, '--verify')
    (root / 'manager').write_text(''); (root / 'detector').write_text('vm-other\n'); run(False, '--verify')
print('PASS: live DT, VMID, bound driver, mount failure, idempotence and sysinit/full verification')
