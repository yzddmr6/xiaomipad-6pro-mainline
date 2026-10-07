#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Offline tests: no Fastboot, tablet connection or block-device writes."""
import hashlib
import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch

project = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('installer', project / 'tools/install-liuqin.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def boot_header(kind, fields=None):
    """A 4096-byte boot image header page shaped like the real ones.

    'ubuntu' is the project's v2 image (page 4096, header 1660, a DTB);
    'android' the stock GKI v4 image (header 1584).  ``fields`` overrides
    individual u32 fields by offset, e.g. ``{1648: 0}`` for "no DTB".
    """
    page = bytearray(4096)
    page[:8] = b'ANDROID!'
    values = {8: 1000}
    if kind == 'ubuntu':
        values.update({36: 4096, 40: 2, 1644: 1660, 1648: 5000})
    elif kind == 'android':
        values.update({8: 46000, 20: 1584, 40: 4})
    values.update(fields or {})
    for offset, value in values.items():
        page[offset:offset + 4] = value.to_bytes(4, 'little')
    return bytes(page)


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    files = {}
    for name in ('boot.img', 'installer.img', 'rootfs.tar.gz'):
        (root / name).write_bytes(name.encode())
        files[name] = hashlib.sha256(name.encode()).hexdigest()
    (root / 'bundle.json').write_text(json.dumps({'device': 'liuqin', 'files': files}))
    args = ['python3', str(project / 'tools/install-liuqin.py'), '--bundle', str(root), '--check']
    subprocess.run(args, check=True)
    subprocess.run(args + ['--enable-rescue'], check=True)
    (root / 'boot.img').write_bytes(b'corrupted')
    assert subprocess.run(args, capture_output=True).returncode != 0
    (root / 'boot.img').write_bytes(b'boot.img')
    (root / 'bundle.json').write_text(json.dumps({'device': 'liuqin', 'files': files,
                                               'status': 'OFFLINE_ASSEMBLED'}))
    class StopFlow(Exception):
        """Not an OSError/RuntimeError, so the RAM-channel retry loop lets it through."""

    pinned_boot_bytes = json.loads((project / 'tools/lib/liuqin-rom-images.json').read_text()
                                   )['images']['boot.img']['bytes']

    def run_install(argv_extra, reported, stdin_tty=False, answer=None, boot_size=hex(pinned_boot_bytes),
                    max_download='0x30000000'):
        calls = []

        def fastboot(command, **kwargs):
            calls.append(command)
            assert command[:3] == ['fastboot', '-s', 'TEST_SERIAL']
            if command[3] == 'boot':
                return SimpleNamespace(stdout='booting\n')
            name = command[-1]
            values = {'product': 'liuqin', 'unlocked': 'yes', 'current-slot': 'a',
                      'partition-size:userdata': reported, 'partition-size:boot_a': boot_size,
                      'partition-size:boot_b': boot_size, 'max-download-size': max_download}
            return SimpleNamespace(stdout=name + ': ' + values[name] + '\n')

        stdin = SimpleNamespace(isatty=lambda: stdin_tty)
        with patch.object(installer.sys, 'argv', ['install.py', '--bundle', str(root),
                          '--serial', 'TEST_SERIAL', '--backup', str(root.parent / 'unused-backup'),
                          '--erase-userdata', '--allow-unverified', '--layout', 'linux-only',
                          *argv_extra]), \
             patch.object(installer.subprocess, 'run', side_effect=fastboot), \
             patch.object(installer.sys, 'stdin', stdin), \
             patch('builtins.input', lambda *a: answer), \
             patch.object(installer, 'command', side_effect=StopFlow('stop after boot')):
            try:
                installer.main()
            except (SystemExit, RuntimeError, StopFlow):
                pass
            else:
                raise AssertionError('installation unexpectedly completed: ' + reported)
        return calls

    for reported in ('0x100000', hex(8 * 1024**3)):
        calls = run_install([], reported)
        assert calls[-1][-1] == 'partition-size:userdata', (reported, calls)
    print('PASS: undersized userdata layouts are rejected before RAM boot')

    # A tablet whose userdata has already been replaced by a Linux-only split
    # reports no userdata size at all.  That case is decided by the partition
    # table itself, read in the RAM installer, not by this pre-boot probe.
    calls = run_install(['--yes'], 'unknown')
    assert calls[-1][3] == 'boot', calls
    print('PASS: a tablet without userdata is referred to the on-device table check')

    for reported in (hex(16 * 1024**3), hex(471789528 * 512)):
        calls = run_install(['--yes'], reported)
        assert calls[-1][3] == 'boot', (reported, calls)
    print('PASS: userdata layouts of 16 GiB and above are admitted')

    calls = run_install([], hex(471789528 * 512), stdin_tty=True, answer='no')
    assert not any(call[3:4] == ['boot'] for call in calls)
    calls = run_install([], hex(471789528 * 512), stdin_tty=True, answer='YES')
    assert calls[-1][3] == 'boot'
    print('PASS: interactive erasure confirmation gates the RAM installer boot')

    # Both boot partitions are size-checked before the dual layout boots the
    # RAM installer, because both receive the project image.
    calls = run_install(['--yes'], hex(16 * 1024**3))
    assert ['partition-size:boot_b'] == [c[-1] for c in calls if c[-1].startswith('partition-size:boot')], calls
    print('PASS: the Linux-only layout checks boot_b only')

    # The project image is flashed zero-filled to the whole partition, so a
    # boot partition of another size, or a bootloader that cannot take the
    # whole partition in one raw download, stops before the RAM installer.
    for boot_size, max_download in ((hex(pinned_boot_bytes + 4096), '0x30000000'),
                                    (hex(pinned_boot_bytes - 4096), '0x30000000'),
                                    (hex(pinned_boot_bytes), hex(pinned_boot_bytes - 1))):
        calls = run_install(['--yes'], hex(16 * 1024**3), boot_size=boot_size, max_download=max_download)
        assert not any(call[3:4] == ['boot'] for call in calls), (boot_size, max_download, calls)
    calls = run_install(['--yes'], hex(16 * 1024**3), max_download=str(pinned_boot_bytes))
    assert calls[-1][3] == 'boot', calls
    print('PASS: boot partitions must be the pinned size and fit one fastboot download')

# A local fake shell supplies a CRLF transcript containing the echoed command.
# Only complete marker lines may finish the transaction, not the echo itself.
listener = socket.socket()
listener.bind(('127.0.0.1', 0))
listener.listen(1)
address = listener.getsockname()

def shell():
    with listener.accept()[0] as connection:
        received = b''
        while not received.endswith(b'\n'):
            received += connection.recv(4096)
        token = re.search(rb'LIUQIN_[a-f0-9]+', received)[0]
        connection.sendall(received.replace(b'\n', b'\r\n'))
        connection.sendall(b'\r\n' + token + b'_START\r\nvalue\r\n' + token + b'_END 0\r\n')

thread = threading.Thread(target=shell)
thread.start()
connect = socket.create_connection
with patch.object(installer.socket, 'create_connection', side_effect=lambda *a, **k: connect(address, **k)):
    assert installer.command('unused', 'printf value') == b'value'
thread.join()
listener.close()
assert subprocess.run(['sh', str(project / 'tools/lib/install-root.sh')], capture_output=True).returncode != 0
invalid = subprocess.run(['sh', str(project / 'tools/lib/install-root.sh'),
                          'unused', 'unused', 'unused', 'unused', 'ERASE-LIUQIN-USERDATA',
                          'linux_root', 'linux_home', 'INVALID'], capture_output=True)
assert invalid.returncode != 0 and b'unsupported option: INVALID' in invalid.stderr and \
    b'usage: install-root.sh' in invalid.stderr, invalid.stderr
unauthorized = subprocess.run(['sh', str(project / 'tools/lib/install-root.sh'),
                               'unused', 'unused', 'unused', 'unused', 'NO',
                               'linux_root', 'linux_home'], capture_output=True)
assert unauthorized.returncode != 0 and b'data-erasure acknowledgement' in unauthorized.stderr
for arguments in ([], ['id', 'report'], ['id', 'nonsense', 'x']):
    refused = subprocess.run(['sh', str(project / 'tools/lib/install-layout.sh'), *arguments],
                             capture_output=True)
    assert refused.returncode != 0, arguments
print('PASS: checksum rejection, local-only check, CRLF command framing and missing-authorization refusal')

# Argument combinations that must never reach a device.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    files = {}
    for name in ('boot.img', 'installer.img', 'rootfs.tar.gz'):
        (root / name).write_bytes(name.encode())
        files[name] = hashlib.sha256(name.encode()).hexdigest()
    (root / 'bundle.json').write_text(json.dumps({'device': 'liuqin', 'files': files,
                                                  'status': 'OFFLINE_ASSEMBLED'}))
    base = ['python3', str(project / 'tools/install-liuqin.py'), '--bundle', str(root),
            '--serial', 'TEST_SERIAL', '--backup', str(root.parent / 'unused-backup'),
            '--erase-userdata', '--allow-unverified', '--yes']
    for extra, fragment in (
            ([], b'--layout'),
            (['--layout', 'dual'], b'--rom-dir'),
            (['--layout', 'linux-only', '--rom-dir', str(root)], b'--rom-dir only applies'),
            (['--layout', 'linux-only', '--restore-partition-table', str(root)], b'separate action')):
        refused = subprocess.run(base + extra, capture_output=True)
        assert refused.returncode != 0 and fragment in refused.stderr, (extra, refused.stderr)
print('PASS: layout, ROM and restore argument combinations are checked before any device access')

# The Android boot override replaces a checksum-pinned stock image, so its own
# admission rules are checked offline, before any device is contacted.
pinned = json.loads((project / 'tools/lib/liuqin-rom-images.json').read_text())
stock_boot_bytes = pinned['images']['boot.img']['bytes']
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    files = {}
    for name in ('boot.img', 'installer.img', 'rootfs.tar.gz'):
        (root / name).write_bytes(name.encode())
        files[name] = hashlib.sha256(name.encode()).hexdigest()
    (root / 'bundle.json').write_text(json.dumps({'device': 'liuqin', 'files': files,
                                                  'status': 'OFFLINE_ASSEMBLED'}))
    good = root / 'android-boot-good.img'
    good.write_bytes(boot_header('android') + bytes(stock_boot_bytes - 4096))
    short = root / 'android-boot-short.img'
    short.write_bytes(boot_header('android') + bytes(stock_boot_bytes - 4097))
    wrong_magic = root / 'android-boot-magic.img'
    wrong_magic.write_bytes(b'NOTABOOT' + boot_header('android')[8:] + bytes(stock_boot_bytes - 4096))
    # A v2 image, ours, is exactly what the Android slot must never be handed.
    ours = root / 'android-boot-v2.img'
    ours.write_bytes(boot_header('ubuntu') + bytes(stock_boot_bytes - 4096))
    bare = root / 'android-boot-bare.img'
    bare.write_bytes(b'ANDROID!' + bytes(stock_boot_bytes - 8))
    check = ['python3', str(project / 'tools/install-liuqin.py'), '--bundle', str(root), '--check']
    for extra, fragment in (
            (['--layout', 'dual', '--android-boot', str(short)], b'exactly'),
            (['--layout', 'dual', '--android-boot', str(wrong_magic)], b'Android boot magic'),
            (['--layout', 'dual', '--android-boot', str(ours)], b'header version 3 or 4'),
            (['--layout', 'dual', '--android-boot', str(bare)], b'header version 3 or 4'),
            (['--layout', 'dual', '--android-boot', str(root / 'absent.img')], b'not a file'),
            (['--layout', 'linux-only', '--android-boot', str(good)], b'only applies to --layout dual'),
            (['--android-boot', str(good)], b'only applies to --layout dual')):
        refused = subprocess.run(check + extra, capture_output=True)
        assert refused.returncode != 0 and fragment in refused.stderr, (extra, refused.stderr)
    accepted = subprocess.run(check + ['--layout', 'dual', '--android-boot', str(good)],
                              capture_output=True)
    assert accepted.returncode == 0, accepted.stderr
    restore = subprocess.run(check + ['--restore-partition-table', str(root),
                                      '--android-boot', str(good)], capture_output=True)
    assert restore.returncode != 0 and b'separate action' in restore.stderr, restore.stderr
print('PASS: the Android boot override is refused unless it is a dual-layout, '
      'partition-sized v3/v4 Android boot image')

# The override must replace the stock boot.img only after every other stock
# image has been verified, and must always be written.
with tempfile.TemporaryDirectory() as directory:
    rom = Path(directory) / 'images'
    rom.mkdir(parents=True)
    for name, entry in pinned['images'].items():
        (rom / name).write_bytes(b'')
    parser, args = installer.parse_arguments(
        ['--bundle', str(rom), '--layout', 'dual', '--rom-dir', str(rom.parent)])
    refused = []
    with patch.object(parser, 'error', side_effect=lambda message: refused.append(message)
                      or (_ for _ in ()).throw(SystemExit(2))):
        try:
            installer.verify_rom(parser, rom.parent)
        except SystemExit:
            pass
    assert refused and 'does not match the pinned' in refused[0], refused
print('PASS: a ROM directory whose images do not match the pinned release is refused')

# Android loads WLAN/DSP and Bluetooth firmware before its debug channel is
# available. A valid boot/super pair alone cannot repair these filesystems.
firmware_images = {'NON-HLOS.bin': 'modem_a', 'BTFM.bin': 'bluetooth_a', 'dspso.bin': 'dsp_a'}
for name, partition in firmware_images.items():
    entry = pinned['images'][name]
    assert entry['partition'] == partition
    assert partition in installer.ANDROID_FIRMWARE_PARTITIONS
    calls = []

    def firmware_digest(*args, **kwargs):
        calls.append(args)
        assert args == ('digest', partition, str(entry['bytes']))
        return (f'size {entry["bytes"] + 4096}\n'
                f'content {content_sha}\npadding {padding_sha}\n')

    for content_sha, padding_sha, repair in (
            ('0' * 64, installer.zero_digest(4096), True),
            (entry['sha256'], installer.zero_digest(4096), False),
            (entry['sha256'], '1' * 64, True)):
        selected = installer.stock_images_to_flash(firmware_digest, {name: entry})
        assert selected == ([(name, entry)] if repair else []), (name, selected)
    assert len(calls) == 3

    with tempfile.TemporaryDirectory() as directory:
        saved = Path(directory)
        original = b'previous partition contents, including a damaged filesystem'
        expected_sha = hashlib.sha256(original).hexdigest()
        commands = []

        def read_partition(command, timeout=60):
            commands.append(command)
            assert '/dev/disk/by-partlabel/' + partition in command
            if 'base64' in command:
                return base64.b64encode(original) + b'\r\n'
            assert 'sha256sum' in command
            return (expected_sha + '  /dev/disk/by-partlabel/' + partition + '\n').encode()

        assert installer.backup_partition(read_partition, saved, partition) == expected_sha
        backup = saved / (partition + '.img')
        assert backup.read_bytes() == original
        assert backup.stat().st_mode & 0o777 == 0o600
        assert len(commands) == 2
        expected_sha = 'f' * 64
        try:
            installer.backup_partition(read_partition, saved, partition)
        except RuntimeError as error:
            assert 'Backup verification failed' in str(error)
        else:
            raise AssertionError('firmware backup hash mismatch was accepted')
print('PASS: required Android firmware is repaired only on mismatch, with verified private backups')

# The installer RAM image has no /tmp, so everything the host writes on the
# tablet goes to the scratch directory the device script reads from -- and the
# host creates that directory before writing into it, because the first
# command it sends may well be this one.
sent = []
installer.stage_blob(lambda text, timeout=60: sent.append(text), 'head', b'liuqin')
assert sent[0] == 'mkdir -p ' + installer.LAYOUT_WORK, sent
staged = installer.LAYOUT_WORK + '/liuqin-gpt-head.b64'
assert sent[1] == ':>' + staged, sent
assert all('/tmp' not in text for text in sent), sent
assert sent[2] == 'printf %s bGl1cWlu >>' + staged, sent
assert sent[3] == 'printf "\\n" >>' + staged, sent
assert installer.LAYOUT_WORK == '/run/liuqin-layout'
# The device script must read the halves back from that same directory.
layout_script = (project / 'tools/lib/install-layout.sh').read_text()
assert 'LIUQIN_LAYOUT_WORK:-' + installer.LAYOUT_WORK in layout_script
print('PASS: the staged partition-table halves land in the scratch directory the RAM image has')

# --- slot assignment and the fastboot tail ---------------------------------
# The dual layout boots both systems from slot A: the project image goes to
# boot_a and, as the bootloader's fallback, boot_b; slot A is selected through
# fastboot itself.  The stock boot.img is never flashed there -- it is stored.
assert installer.boot_slots('dual') == ('a', 'b')
assert installer.boot_slots('linux-only') == ('b',)
boot_bytes = installer.boot_partition_bytes(pinned)
assert boot_bytes == 201326592 == pinned['images']['boot.img']['bytes']
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    image = root / 'boot.img'
    image.write_bytes(boot_header('ubuntu') + b'\x5a' * 8192)
    padded, padded_sha = installer.pad_boot_image(image, installer.sha(image), boot_bytes, root)
    firmware_flashes = []
    for name, partition in firmware_images.items():
        scratch = root / partition
        scratch.mkdir()
        source = scratch / name
        source.write_bytes(b'known stock firmware')
        payload, digest = installer.pad_boot_image(source, installer.sha(source), 4096, scratch)
        assert payload.read_bytes() == b'known stock firmware' + bytes(4096 - len(b'known stock firmware'))
        firmware_flashes.append((name, {'partition': partition, 'path': payload,
                                       'flash_bytes': 4096, 'flash_sha256': digest}))
    firmware_flashes.sort()
    for mode, expected in (
            ('dual', [['flash', entry['partition'], str(entry['path'])] for _, entry in firmware_flashes] +
                     [['flash', 'vendor_boot_a', '/rom/vendor_boot.img'],
                      ['flash', 'boot_a', str(padded)],
                      ['flash', 'boot_b', str(padded)],
                      ['--set-active=a'], ['reboot']]),
            ('linux-only', [['flash', 'boot_b', str(padded)],
                            ['--set-active=b'], ['reboot']])):
        sent = []
        payloads = {}

        def record(*arguments):
            sent.append(list(arguments))
            if arguments[0] == 'flash' and arguments[1].startswith('boot_'):
                payload = Path(arguments[2])
                payloads[arguments[1]] = (payload.stat().st_size, installer.sha(payload))

        flashes = firmware_flashes + [
            ('vendor_boot.img', {'partition': 'vendor_boot_a', 'path': '/rom/vendor_boot.img'})] \
            if mode == 'dual' else []
        installer.finish_in_fastboot(record, mode, flashes, padded, padded_sha, boot_bytes)
        assert sent == expected, (mode, sent)
        assert not any(call[0].startswith('--set-active') and call != expected[-2] for call in sent), sent
        # Every boot write is the whole partition: the image, then zeros.
        assert set(payloads) == {'boot_' + slot for slot in installer.boot_slots(mode)}, payloads
        assert all(payload == (boot_bytes, padded_sha) for payload in payloads.values()), payloads
    # No partition is written if a prepared firmware payload has changed.
    damaged = firmware_flashes[0][1]['path']
    with damaged.open('r+b') as stream:
        stream.seek(4095)
        stream.write(b'\x01')
    sent = []
    try:
        installer.finish_in_fastboot(lambda *a: sent.append(a), 'dual', firmware_flashes,
                                     padded, padded_sha, boot_bytes)
    except RuntimeError as error:
        assert 'firmware image changed' in str(error)
    else:
        raise AssertionError('a changed firmware payload was flashed')
    assert sent == [], sent
    assert padded.read_bytes()[:image.stat().st_size] == image.read_bytes()
    assert padded.read_bytes()[image.stat().st_size:] == bytes(boot_bytes - image.stat().st_size)
    # A padded file that changed after it was built is never flashed.
    with padded.open('r+b') as stream:
        stream.seek(boot_bytes - 1)
        stream.write(b'\x01')
    sent = []
    try:
        installer.finish_in_fastboot(lambda *a: sent.append(a), 'dual', [], padded, padded_sha, boot_bytes)
    except RuntimeError:
        pass
    else:
        raise AssertionError('a changed padded image was flashed')
    assert sent == [], sent
    # The source is checked against the bundle digest while it is copied, and
    # an image larger than the partition is refused.
    for source, digest, fragment in ((image, 'f' * 64, 'changed while it was being padded'),
                                     (image, installer.sha(image), 'larger than')):
        with tempfile.TemporaryDirectory() as scratch:
            try:
                installer.pad_boot_image(source, digest, boot_bytes if fragment != 'larger than' else 4096,
                                         scratch)
            except RuntimeError as error:
                assert fragment in str(error), error
            else:
                raise AssertionError('pad_boot_image accepted ' + fragment)
print('PASS: dual writes the project image to boot_a and boot_b and selects slot A; '
      'linux-only keeps slot B; every boot write is the image zero-filled to the whole partition')

installer_source = (project / 'tools/install-liuqin.py').read_text()
assert "'--set-active=b'" not in installer_source and '--set-active=b' not in installer_source
assert 'stock_images_to_flash(device_layout, {' in installer_source
assert 'if name != ANDROID_BOOT_IMAGE})' in installer_source
print('PASS: the stock boot.img is stored for liuqin-switch, not flashed')

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    ubuntu = root / 'boot.img'
    ubuntu.write_bytes(boot_header('ubuntu') + bytes(4096))
    android = root / 'android.img'
    android.write_bytes(boot_header('android') + bytes(8192))
    arguments = installer.switch_store_arguments(
        'http://192.168.7.1:8000', 'a' * 64, ubuntu, {'sha256': 'b' * 64, 'path': android})
    assert arguments == ['SWITCH-STORE',
                         'ubuntu', 'http://192.168.7.1:8000/boot.img', 'a' * 64, '8192',
                         'android', 'http://192.168.7.1:8000/android-boot.img', 'b' * 64, '12288'], arguments
    # install-root.sh accepts exactly this shape, validates it before touching
    # anything, and refuses a malformed one.
    base = ['sh', str(project / 'tools/lib/install-root.sh'), 'not-this-boot', 'u', 's', '1',
            'ERASE-LIUQIN-USERDATA', 'linux_root', 'linux_home']
    # Reaching the boot-identity check means every option was accepted: it is
    # the first test after the option scan, and nothing has been touched yet.
    accepted = b'RAM boot identity changed'
    for extra, fragment in (
            ([], accepted),
            (arguments, accepted),
            (['ENABLE-USB-RESCUE'] + arguments, accepted),
            (['KEEP-HOME'], accepted),
            (['ENABLE-USB-RESCUE', 'KEEP-HOME'], accepted),
            (['KEEP-HOME', 'ENABLE-USB-RESCUE'], accepted),
            (['KEEP-HOME'] + arguments, accepted),
            (['ENABLE-USB-RESCUE', 'KEEP-HOME'] + arguments, accepted),
            (['KEEP-HOME', 'ENABLE-USB-RESCUE'] + arguments, accepted),
            (['KEEP-HOME', 'KEEP-HOME'], b'option given twice: KEEP-HOME'),
            (['ENABLE-USB-RESCUE', 'KEEP-HOME', 'ENABLE-USB-RESCUE'], b'option given twice: ENABLE-USB-RESCUE'),
            (['KEEP-HOME', 'KEEP-HOME'] + arguments, b'option given twice: KEEP-HOME'),
            (['KEEP-HOME', 'KEEP-THE-HOME'], b'unsupported option: KEEP-THE-HOME'),
            (['keep-home'], b'unsupported option: keep-home'),
            (['KEEP-HOME', ''], b'unsupported option: ;'),
            # SWITCH-STORE takes exactly its eight fields and comes last.
            (arguments + ['KEEP-HOME'], b'usage: install-root.sh'),
            (['NOT-A-STORE'] + arguments[1:], b'unsupported option'),
            (arguments[:3] + ['nothex'] + arguments[4:], b'invalid ubuntu sha256'),
            (arguments[:5] + ['ubuntu'] + arguments[6:], b'expected the android image'),
            (arguments[:8] + ['201326593'], b'does not fit'),
            (arguments[:2] + ['ftp://x/boot.img'] + arguments[3:], b'not http'),
            (arguments[:-1], b'usage')):
        result = subprocess.run(base + extra, capture_output=True)
        assert result.returncode != 0 and fragment in result.stderr, (extra, result.stderr)
        # A refused shape stops in the option scan, before the identity check.
        assert fragment == accepted or accepted not in result.stderr, (extra, result.stderr)
print('PASS: the switch-store arguments are built for and validated by install-root.sh; '
      'ENABLE-USB-RESCUE and KEEP-HOME are accepted once each in either order, before SWITCH-STORE')

# The host builds the install-root.sh command: KEEP-HOME only with --keep-home,
# in the documented order, and every shape it builds is one the device script
# accepts.
with tempfile.TemporaryDirectory() as directory:
    bundle = Path(directory)
    (bundle / 'rootfs.tar.gz').write_bytes(b'rootfs')
    (bundle / 'boot.img').write_bytes(boot_header('ubuntu') + bytes(4096))
    android = bundle / 'android.img'
    android.write_bytes(boot_header('android') + bytes(8192))
    manifest = {'files': {'rootfs.tar.gz': 'c' * 64, 'boot.img': 'a' * 64}}
    rom_images = {installer.ANDROID_BOOT_IMAGE: {'sha256': 'b' * 64, 'path': android}}
    fixed = ['sh', '/usr/lib/liuqin/install-root.sh', 'not-this-boot', 'http://192.168.7.1:8000/rootfs.tar.gz',
             'c' * 64, '6', 'ERASE-LIUQIN-USERDATA', installer.layout.ROOT_NAME, installer.layout.HOME_NAME]
    for layout_mode in ('linux-only', 'dual'):
        for enable_rescue in (False, True):
            for keep_home in (False, True):
                args = SimpleNamespace(layout=layout_mode, enable_rescue=enable_rescue, keep_home=keep_home)
                built = installer.install_root_command('not-this-boot', 'http://192.168.7.1:8000', bundle,
                                                       manifest, args, rom_images)
                options = built[len(fixed):]
                expected = (['ENABLE-USB-RESCUE'] if enable_rescue else []) + \
                    (['KEEP-HOME'] if keep_home else [])
                if layout_mode == 'dual':
                    expected += installer.switch_store_arguments(
                        'http://192.168.7.1:8000', 'a' * 64, bundle / 'boot.img',
                        rom_images[installer.ANDROID_BOOT_IMAGE])
                assert built[:len(fixed)] == fixed, built
                assert options == expected, (layout_mode, enable_rescue, keep_home, built)
                assert ('KEEP-HOME' in built) == keep_home, built
                result = subprocess.run(['sh', str(project / 'tools/lib/install-root.sh'), *built[2:]],
                                        capture_output=True)
                assert result.returncode != 0 and accepted in result.stderr, (built, result.stderr)
    # main() hands exactly this command to the tablet.
    assert 'install = install_root_command(boot_id, base, bundle, manifest, args, rom_images)' in \
        (project / 'tools/install-liuqin.py').read_text()
print('PASS: the host sends KEEP-HOME only with --keep-home, after ENABLE-USB-RESCUE and before '
      'SWITCH-STORE, and install-root.sh accepts every shape the host builds')

# What the tablet holds after the dual installation, as liuqin-switch sees it.
# boot_a starts out as the stock Android image, whose AVB footer sits at the
# end of the partition; fastboot writes exactly the bytes it is sent.  The
# store's SHA256SUMS names the unpadded image (the installer passes the bundle
# digest to install-switch-store.sh), while liuqin-switch expects boot_a to
# hash like that image zero-filled to the partition size -- which is what the
# installer now flashes.  The bare image, as flashed before, fails `verify`.
switch_script = project / 'device/gnome-overlay/usr/local/sbin/liuqin-switch'


def switch_function(name):
    lines = switch_script.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(name + '() {'))
    return '\n'.join(lines[start:lines.index('}', start) + 1])


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    ubuntu = root / 'boot.img'
    ubuntu.write_bytes(boot_header('ubuntu') + b'\x5a' * (3 * 1024 * 1024 + 123))
    android = root / 'android.img'
    stock = bytearray(boot_header('android') + b'\xa5' * 8192)
    android.write_bytes(bytes(stock))
    padded, padded_sha = installer.pad_boot_image(ubuntu, installer.sha(ubuntu), boot_bytes, root)
    unpadded_sha = installer.sha(ubuntu)
    arguments = installer.switch_store_arguments('http://192.168.7.1:8000', unpadded_sha, ubuntu,
                                                 {'sha256': installer.sha(android), 'path': android})
    assert arguments[3] == unpadded_sha != padded_sha
    # liuqin-switch's own padded_sha() of the store image is the flashed payload's digest.
    shell = '\n'.join((switch_function('zeros'), switch_function('padded_sha'), 'padded_sha "$1"'))
    switch_padded = subprocess.run(['sh', '-c', 'BOOT_BYTES=' + str(boot_bytes) + '\n' + shell, 'sh', str(ubuntu)],
                                   capture_output=True, text=True, check=True).stdout.strip()
    assert switch_padded == padded_sha == installer.sha(padded), (switch_padded, padded_sha)

    def fixture(flashed, name):
        base = root / name
        dev, store = base / 'dev', base / 'store'
        dev.mkdir(parents=True)
        for kind, source, digest in (('ubuntu', ubuntu, arguments[3]), ('android', android, arguments[7])):
            (store / kind).mkdir(parents=True)
            (store / kind / 'boot.img').write_bytes(source.read_bytes())
            # install-switch-store.sh writes exactly this line from its argument.
            (store / kind / 'SHA256SUMS').write_text(digest + '  boot.img\n')
        # The stock image fills boot_a and ends in its AVB footer.
        with (dev / 'boot_a').open('wb') as stream:
            stream.write(bytes(stock))
            stream.truncate(boot_bytes)
            stream.seek(boot_bytes - 64)
            stream.write(b'AVBf' + b'\x01' * 60)
        # fastboot flash boot_a: the payload's bytes, nothing more.
        with (dev / 'boot_a').open('r+b') as stream, flashed.open('rb') as payload:
            while chunk := payload.read(1 << 20):
                stream.write(chunk)
        with (dev / 'sde').open('wb') as stream:
            stream.truncate(2923429888)
        (dev / 'boot_a.ro').write_text('1\n')
        (dev / 'sde.ro').write_text('1\n')
        (dev / 'cmdline').write_text('console=ttyMSM0 androidboot.slot_suffix=_a\n')
        if as_root is None:
            return None
        return subprocess.run([*as_root, 'sh', str(switch_script), 'verify',
                               '--dev-dir', str(dev), '--store', str(store)],
                              capture_output=True, text=True)

    # liuqin-switch insists on uid 0 even against a file fixture.  CI runners
    # may forbid unprivileged user namespaces; the digest identity above is
    # the fix itself and is checked either way.
    if os.geteuid() == 0:
        as_root = []
    elif subprocess.run(['unshare', '-r', 'true'], capture_output=True).returncode == 0:
        as_root = ['unshare', '-r']
    else:
        as_root = None
    good = fixture(padded, 'padded')
    assert installer.sha(root / 'padded/dev/boot_a') == padded_sha
    if as_root is None:
        print('SKIP: liuqin-switch verify on the flashed fixture (no uid 0 or user namespace here)')
    else:
        assert good.returncode == 0 and 'boot_a holds: the Ubuntu image from the store' in \
            good.stdout + good.stderr, good.stdout + good.stderr
        stale = fixture(ubuntu, 'bare')
        assert stale.returncode != 0 and 'boot_a holds: an Ubuntu image that is not the one in the store' in \
            stale.stdout + stale.stderr, stale.stdout + stale.stderr
print('PASS: the flashed payload is the whole partition; SHA256SUMS keeps the unpadded digest, which '
      'liuqin-switch pads to the flashed digest' + ('' if as_root is None else
      '; liuqin-switch verify accepts boot_a after the flash (the bare image, as before, is refused)'))

# Serve the bundle plus exactly the one extra file; nothing else outside.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    bundle = root / 'bundle'
    bundle.mkdir()
    (bundle / 'boot.img').write_bytes(b'project')
    outside = root / 'rom-boot.img'
    outside.write_bytes(b'android')
    (root / 'secret').write_bytes(b'secret')
    server = installer.http.server.ThreadingHTTPServer(
        ('127.0.0.1', 0), installer.bundle_handler(bundle, {'/android-boot.img': outside}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    import urllib.request
    import urllib.error
    url = f'http://127.0.0.1:{server.server_port}'
    try:
        assert urllib.request.urlopen(url + '/boot.img').read() == b'project'
        assert urllib.request.urlopen(url + '/android-boot.img').read() == b'android'
        assert urllib.request.urlopen(url + '/android-boot.img?x=1').read() == b'android'
        for path in ('/../secret', '/%2e%2e/secret', '/rom-boot.img'):
            try:
                urllib.request.urlopen(url + path).read()
            except urllib.error.HTTPError as error:
                assert error.code == 404, (path, error.code)
            else:
                raise AssertionError('served a file outside the bundle: ' + path)
    finally:
        server.shutdown()
        server.server_close()
print('PASS: the installer serves the Android boot image and nothing else outside the bundle')

# --- one header rule, three implementations --------------------------------
# liuqin-switch and the store step share the shell text; the installer's
# Python copy must agree with it on every shape.
def shell_function(path, name):
    lines = Path(path).read_text().splitlines()
    start = lines.index(name + '() { # <image or device>' if name == 'classify' else name + '() { # <file> <offset>')
    end = lines.index('}', start)
    return '\n'.join(lines[start:end + 1])


store_script = project / 'tools/lib/install-switch-store.sh'
for name in ('u32_at', 'classify'):
    assert shell_function(switch_script, name) == shell_function(store_script, name), name
classifier = shell_function(switch_script, 'u32_at') + '\n' + shell_function(switch_script, 'classify') + \
    '\nclassify "$1"\n'
shapes = {
    'ubuntu': boot_header('ubuntu'),
    'android v4': boot_header('android'),
    'android v3': boot_header('android', {20: 1580, 40: 3}),
    'v3 with a v4 header size': boot_header('android', {40: 3}),
    'v2 without a DTB': boot_header('ubuntu', {1648: 0}),
    'v2 with a 2048-byte page': boot_header('ubuntu', {36: 2048}),
    'v2 with a v1 header size': boot_header('ubuntu', {1644: 1648}),
    'v4 without a kernel': boot_header('android', {8: 0}),
    'v1': boot_header('ubuntu', {40: 1}),
    'v5': boot_header('android', {40: 5}),
    'no magic': b'NOTABOOT' + boot_header('android')[8:],
    'erased': bytes(4096),
}
with tempfile.TemporaryDirectory() as directory:
    for label, header in shapes.items():
        image = Path(directory) / 'image'
        image.write_bytes(header + bytes(4096))
        want = installer.boot_image_kind(header)
        for shell in (['sh'], ['busybox', 'sh']):
            got = subprocess.run([*shell, '-c', classifier, 'sh', str(image)],
                                 capture_output=True, text=True).stdout.strip()
            assert got == want, (label, shell, got, want)
    assert installer.boot_image_kind(shapes['ubuntu']) == 'ubuntu'
    assert installer.boot_image_kind(shapes['android v3']) == 'android'
    assert installer.boot_image_kind(shapes['android v4']) == 'android'
print('PASS: the Python and shell header rules agree on ' + str(len(shapes)) + ' header shapes')
# Every remote command is one RAM shell line, and ash's line-editing buffer
# refuses a line of 1024 bytes or more (newline included; measured).  command()
# refuses such a line before it connects; a line of 1022 bytes plus the
# newline is still sent.
assert installer.RAM_SHELL_LINE_BYTES == 1023
token = 'LIUQIN_' + '0' * 32  # the shape command() draws from uuid4().hex
start, end = token + '_START', token + '_END'
# 'a' needs no shell quoting, so each one adds exactly one byte to the line.
framing = len(installer.frame_command('a', start, end)) - 1
fits = 'a' * (installer.RAM_SHELL_LINE_BYTES - framing)
line = installer.frame_command(fits, start, end)
assert len(line) == 1023 and line.endswith(b'\n') and b'\n' not in line[:-1], len(line)
connections = []

def refuse_connection(*arguments, **keywords):
    connections.append(arguments)
    raise ConnectionRefusedError('test: past the guard')

with patch.object(installer.socket, 'create_connection', side_effect=refuse_connection):
    try:
        installer.command('unused', fits + 'a')
    except RuntimeError as error:
        assert 'RAM shell accepts at most 1023' in str(error) and 'not sent' in str(error), error
    else:
        raise AssertionError('a 1024-byte RAM shell line was not refused')
    assert not connections, 'the over-long line was refused only after connecting'
    try:
        installer.command('unused', fits)
    except ConnectionRefusedError:
        pass
    assert len(connections) == 1, 'a 1023-byte RAM shell line did not reach the connection'
print('PASS: command() refuses a 1024-byte RAM shell line before connecting and sends a 1023-byte one'
      ' (newline included)')

# Real GPT partition dumps (e.g. 24 KiB sda-head.bin) are staged in chunks.
# Each chunk must stay short as text and, wrapped by remote()'s boot_id guard
# and framed by command(), fit one RAM shell line.
sent_large = []
installer.stage_blob(lambda text, timeout=60: sent_large.append(text), 'head', b'\x00' * (24 * 1024))
assert len(sent_large) > 10, "Large blob should be split into multiple chunks"
assert all(len(cmd) < 700 for cmd in sent_large), sent_large
guard_source = 'guard = \'test "$(cat /proc/sys/kernel/random/boot_id)" = \' + shlex.quote(boot_id)'
assert guard_source in installer_source and "guard + ' && ' + text" in installer_source
guard = 'test "$(cat /proc/sys/kernel/random/boot_id)" = ' + '00000000-0000-0000-0000-000000000000'
framed = [installer.frame_command(guard + ' && ' + text, start, end) for text in sent_large]
assert max(map(len, framed)) <= installer.RAM_SHELL_LINE_BYTES, max(map(len, framed))
print('PASS: large partition blobs are chunked into RAM shell lines of at most '
      + str(max(map(len, framed))) + ' bytes')

