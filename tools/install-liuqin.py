#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Install a verified liuqin bundle from a Linux host over USB networking."""
import argparse
import base64
import functools
import hashlib
import http.server
import importlib.util
import json
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid

# Slot assignment.  In the dual layout both systems boot from slot A: the
# project boot image goes to boot_a, Android's boot image is kept in the switch
# store on the Ubuntu root, and liuqin-switch rotates the two through boot_a.
# The stock bootloader switches slots only through its own fastboot
# --set-active, which swaps the type GUIDs of every _a/_b pair and the UFS boot
# LUN together; nothing on the tablet can do that, so switching never changes
# the slot.  boot_b receives the same project image as the bootloader's own
# fallback when boot_a does not load.
#
# The Linux-only layout keeps its original assignment, Ubuntu in slot B, so
# that an emergency Android kept with --android-size stays bootable from the
# untouched slot A.  It has no switch store.
DUAL_UBUNTU_SLOT = 'a'
DUAL_FALLBACK_SLOT = 'b'
LINUX_ONLY_UBUNTU_SLOT = 'b'
# The stock (or --android-boot) boot image is not flashed in the dual layout:
# it is stored for liuqin-switch and served to the RAM installer under this name.
ANDROID_BOOT_IMAGE = 'boot.img'
ANDROID_BOOT_URL = 'android-boot.img'
# These stock firmware filesystems are required by Android before ADB starts.
# Back up a mismatching partition before restoring it from the pinned ROM.
ANDROID_FIRMWARE_PARTITIONS = ('modem_a', 'bluetooth_a', 'dsp_a')
# Every layout operation resolves the disk through a partition that exists in
# every state of this installation: persist is never created, moved or removed.
ANCHOR = 'persist'
ZERO_DIGEST = hashlib.sha256(b'').hexdigest()


def load_layout():
    """Load the layout engine from the repository or from the bundle beside us."""
    here = Path(__file__).resolve().parent
    for candidate in (here / 'lib/liuqin_layout.py', here / 'liuqin_layout.py'):
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location('liuqin_layout', candidate)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise RuntimeError('liuqin_layout.py is missing next to this program'
                       ' —— 安装包不完整，请下载同一版本的全部文件')


layout = load_layout()


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def update_zeros(digest, length):
    """Feed `length` zero bytes into `digest`."""
    while length > 0:
        block = min(length, 1 << 20)
        digest.update(bytes(block))
        length -= block
    return digest


def zero_digest(length):
    """sha256 of `length` zero bytes, for the padding check on stock images."""
    return update_zeros(hashlib.sha256(), length).hexdigest()


def boot_partition_bytes(table):
    """The size of boot_a and boot_b.

    The stock boot.img of the pinned ROM fills its partition exactly (the
    --android-boot override is held to the same length), so the pinned table
    is the one source of this number; the partitions the bootloader reports
    are compared with it before anything is written.
    """
    return table['images'][ANDROID_BOOT_IMAGE]['bytes']


def pad_boot_image(source, expected_sha256, size, directory):
    """Write `source` followed by zeros up to `size` bytes into `directory`.

    The bootloader writes only the bytes it is sent, so flashing the bare
    project image would leave the old tail of the partition (the stock
    image's AVB footer, for one) behind it.  liuqin-switch identifies boot_a
    by the hash of the whole partition, the image zero-filled to the
    partition size, so that is exactly what is flashed.  The zeros are a
    sparse tail (truncate), so the file costs the image's own size on disk.

    Firmware restores also use this helper with their own scratch directory
    and actual partition size, so an old nonzero tail cannot survive a repair.

    Returns the padded file and its sha256.  The source is checked against
    `expected_sha256` while it is copied, so the padded file carries no bytes
    other than those of the verified bundle image.
    """
    target = Path(directory) / 'boot-padded.img'
    image = hashlib.sha256()
    padded = hashlib.sha256()
    with source.open('rb') as stream, target.open('xb') as out:
        while chunk := stream.read(1 << 20):
            image.update(chunk)
            padded.update(chunk)
            out.write(chunk)
        length = out.tell()
        if length > size:
            raise RuntimeError(f'the image is {length} bytes, larger than the {size}-byte partition')
        out.truncate(size)
    if image.hexdigest() != expected_sha256:
        raise RuntimeError('the image changed while it was being padded; nothing was written')
    update_zeros(padded, size - length)
    if target.stat().st_size != size:
        raise RuntimeError(f'the padded image is not {size} bytes')
    return target, padded.hexdigest()


# The RAM shell is BusyBox ash behind telnetd, and every remote command reaches
# it as one input line.  ash's line-editing buffer refuses a line of 1024 bytes
# or more: measured on the RAM image, 1023 bytes including the newline are
# accepted and 1024 are not.  The PTY is not the limit; Linux's canonical
# input limit is 4095 bytes.
RAM_SHELL_LINE_BYTES = 1023  # longest accepted line, newline included


def frame_command(text, start, end):
    """Return the input line that runs `text` between the two marker lines.

    A line the RAM shell would refuse raises before anything is sent.
    """
    line = ("stty -echo; printf '\\n%s\\n' " + shlex.quote(start) +
            '; sh -c ' + shlex.quote(text) +
            "; result=$?; printf '\\n%s %s\\n' " + shlex.quote(end) +
            ' "$result"\n').encode()
    if len(line) > RAM_SHELL_LINE_BYTES:
        raise RuntimeError(f'remote command line is {len(line)} bytes; the RAM shell accepts at most'
                           f' {RAM_SHELL_LINE_BYTES} including the newline, so it was not sent: '
                           + repr(text[:80]))
    return line


def command(address, text, timeout=60):
    """Use exact line markers, not command echo, to delimit one shell result."""
    token = 'LIUQIN_' + uuid.uuid4().hex
    start, end = token + '_START', token + '_END'
    line = frame_command(text, start, end)
    with socket.create_connection((address, 2323), timeout=10) as connection:
        connection.settimeout(1)
        # BusyBox telnetd announces WILL ECHO / WILL SGA / DO NAWS.
        connection.sendall(b'\xff\xfd\x01\xff\xfd\x03\xff\xfc\x1f')
        connection.sendall(line)
        buffer = bytearray()
        deadline = time.monotonic() + timeout
        pattern = re.compile(rb'(?:^|\n)' + end.encode() + rb' ([0-9]+)\r?\n')
        while time.monotonic() < deadline:
            try:
                chunk = connection.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                break
            buffer.extend(chunk)
            # Backup output can be large; the terminator is always at the tail.
            match = pattern.search(buffer, max(0, len(buffer) - 512))
            if match:
                first = re.search(rb'(?:^|\n)' + start.encode() + rb'\r?\n', buffer)
                if not first or int(match[1]) != 0:
                    raise RuntimeError(bytes(buffer[-4096:]).decode(errors='replace'))
                return bytes(buffer[first.end():match.start()]).replace(b'\r\n', b'\n').removesuffix(b'\r')
        raise RuntimeError('RAM command timed out or connection closed; installation stopped')


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--check', action='store_true', help='Verify local files without accessing any device')
    parser.add_argument('--serial')
    parser.add_argument('--host-address', help='Host IPv4 address on the tablet USB network')
    parser.add_argument('--device-address', default='192.168.7.2')
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--no-backup', action='store_true',
                        help='Explicitly skip partition backups for an existing --keep-home reinstall')
    parser.add_argument('--erase-userdata', action='store_true')
    parser.add_argument('--layout', choices=list(layout.MODES),
                        help='linux-only: Ubuntu is the only system. '
                             'dual: keep a stock Android alongside it; both boot from slot A')
    parser.add_argument('--android-size', help='Android userdata size as NNG or NN%% '
                                               '(0 deletes the partition; default 96G dual, 0 linux-only)')
    parser.add_argument('--root-size', help='linux_root size as NNG or NN%% (default 32G); '
                                            'linux_home takes the remainder')
    parser.add_argument('--keep-home', action='store_true',
                        help='Reinstall the system on an existing split layout and keep linux_home')
    parser.add_argument('--rom-dir', type=Path,
                        help='Extracted stock Fastboot ROM directory; required by --layout dual')
    parser.add_argument('--android-boot', type=Path, metavar='IMG',
                        help='Keep this boot image as Android\'s boot image instead of the stock '
                             'boot.img; only applies to --layout dual. It is stored on the Ubuntu '
                             'root and written to boot_a by liuqin-switch. Every other stock '
                             'image is still verified against the pinned checksums.')
    parser.add_argument('--restore-partition-table', type=Path, metavar='BACKUP_DIR',
                        help='Restore the partition table saved in an earlier backup directory and stop')
    parser.add_argument('--yes', action='store_true', help='Skip the interactive data-erasure confirmation')
    parser.add_argument('--allow-unverified', action='store_true', help='Explicitly test an offline-only bundle')
    parser.add_argument('--enable-rescue', action='store_true',
                        help='Enable unauthenticated root rescue access after installation (trusted USB only)')
    return parser, parser.parse_args(argv)


def reference_plan(parser, args):
    """The plan as it applies to an untouched factory table, for the prompt.

    The device's own table is read and re-planned before anything is written;
    this preview exists so that the confirmation names real numbers.
    """
    disk = layout.reference_stock_disk()
    region = disk.usable_end - disk.by_name(layout.USERDATA_NAME).start
    try:
        android, root = layout.resolve_sizes(args.layout, args.android_size, args.root_size, region)
        return layout.plan_layout(disk, args.layout, android, root)
    except layout.LayoutError as error:
        parser.error(str(error))


def validate_arguments(parser, args, preview=False):
    """Check the argument combinations.  ``preview`` covers the offline --check
    path, where no device and no stock ROM are involved."""
    if args.no_backup and (args.backup or not args.keep_home or args.restore_partition_table):
        parser.error('--no-backup requires --keep-home and cannot be combined with --backup or restore')
    if args.keep_home and (args.android_size or args.root_size):
        parser.error('--keep-home keeps the existing sizes; do not also request new ones'
                     ' —— 现有分区尺寸不可调整，请勿同时指定尺寸参数')
    if args.restore_partition_table is not None:
        for name, value in (('--layout', args.layout), ('--rom-dir', args.rom_dir),
                            ('--android-boot', args.android_boot),
                            ('--android-size', args.android_size), ('--root-size', args.root_size)):
            if value:
                parser.error('--restore-partition-table is a separate action; ' + name + ' does not apply')
        if not preview and not args.serial:
            parser.error('--restore-partition-table requires --serial')
        return
    if args.android_boot is not None:
        # The override decides what Android boots from, so it is checked on the
        # offline path as well: nothing about it needs a device.
        if args.layout != 'dual':
            parser.error('--android-boot only applies to --layout dual'
                         ' —— 仅双系统模式可替换 Android 侧 boot 镜像')
        check_android_boot(parser, args.android_boot)
    if preview:
        return
    if not args.layout:
        parser.error('--layout linux-only|dual is required')
    if args.layout == 'dual' and not args.rom_dir:
        parser.error('--layout dual needs --rom-dir pointing at an extracted stock Fastboot ROM'
                     ' —— 双系统模式必须提供原厂 ROM 目录，本项目不分发原厂镜像')
    if args.layout != 'dual' and args.rom_dir:
        parser.error('--rom-dir only applies to --layout dual')


def boot_image_kind(header):
    """The header rule liuqin-switch and the store step apply, in Python.

    'ubuntu': header v2, 4096-byte page, 1660-byte header, a DTB.
    'android': header v3 (1580-byte header) or v4 (1584-byte header).
    Both need the ANDROID! magic and a kernel; anything else is 'unknown'.
    """
    if len(header) < 1652 or header[:8] != b'ANDROID!':
        return 'unknown'

    def u32(offset):
        return int.from_bytes(header[offset:offset + 4], 'little')

    version, kernel = u32(40), u32(8)
    if kernel == 0:
        return 'unknown'
    if version == 2 and u32(36) == 4096 and u32(1644) == 1660 and u32(1648) > 0:
        return 'ubuntu'
    if (version, u32(20)) in ((3, 1580), (4, 1584)):
        return 'android'
    return 'unknown'


def check_android_boot(parser, path):
    """Accept an Android boot override only if it can occupy the stock partition.

    The image replaces a verified stock image, so it is held to the properties
    that can be checked without a device: it is exactly as long as the stock
    boot.img the pinned table describes, and its header identifies it as an
    Android (v3/v4) boot image, which is what liuqin-switch will require before
    it writes the image to boot_a.  Its content is the operator's
    responsibility and its checksum is printed wherever the plan is shown.
    """
    table = load_rom_table(parser)
    expected = table['images']['boot.img']['bytes']
    if not path.is_file():
        parser.error('--android-boot is not a file: ' + str(path))
    if path.stat().st_size != expected:
        parser.error(f'--android-boot must be exactly {expected} bytes, the size of the stock '
                     f'boot.img in {table["rom"]}; {path} is {path.stat().st_size} bytes'
                     ' —— 替换镜像必须与原厂 boot 分区等长')
    with path.open('rb') as stream:
        header = stream.read(4096)
    if header[:8] != b'ANDROID!':
        parser.error('--android-boot does not start with the Android boot magic: ' + str(path) +
                     ' —— 该文件不是 Android boot 镜像')
    if boot_image_kind(header) != 'android':
        parser.error('--android-boot is not an Android boot image with header version 3 or 4: ' +
                     str(path) + ' —— 该文件不是 v3/v4 头的 Android boot 镜像')
    return sha(path)


def load_rom_table(parser):
    """The pinned stock-ROM identities, from the repository or from the bundle."""
    here = Path(__file__).resolve().parent
    pinned = next((item for item in (here / 'lib/liuqin-rom-images.json',
                                     here / 'liuqin-rom-images.json') if item.is_file()), None)
    if pinned is None:
        parser.error('liuqin-rom-images.json is missing next to this program'
                     ' —— 安装包不完整，请下载同一版本的全部文件')
    return json.loads(pinned.read_text())


def verify_rom(parser, rom_dir, android_boot=None):
    """Check every stock image this installation writes against the pinned table."""
    table = load_rom_table(parser)
    images = rom_dir.resolve()
    if (images / 'images').is_dir():
        images = images / 'images'
    selected = {}
    for name, entry in sorted(table['images'].items()):
        path = images / name
        if not path.is_file():
            parser.error('the ROM directory does not contain ' + name +
                         ' —— 请指向解包后的原厂 Fastboot ROM 目录（含 images/）')
        if path.stat().st_size != entry['bytes'] or sha(path) != entry['sha256']:
            parser.error('ROM image does not match the pinned ' + table['rom'] + ' release: ' + name +
                         ' —— 原厂镜像与本项目验证过的版本不一致，请使用 ' + table['rom'])
        selected[name] = dict(entry, path=path)
    if android_boot is not None:
        # Every stock image was verified above; only now is the Android boot
        # image swapped, so the override can never skip a checksum.
        override = android_boot.resolve()
        selected['boot.img'] = dict(selected['boot.img'], path=override, sha256=sha(override),
                                    override=True)
    return table, selected


def main(argv=None):
    parser, args = parse_arguments(argv)
    bundle = args.bundle.resolve()
    manifest = json.loads((bundle / 'bundle.json').read_text())
    if manifest['device'] != 'liuqin':
        parser.error('wrong device bundle')
    for name in ('boot.img', 'installer.img', 'rootfs.tar.gz'):
        if sha(bundle / name) != manifest['files'][name]:
            parser.error('bundle checksum mismatch: ' + name + ' —— 包文件损坏或不完整，请重新下载')
    if args.check:
        validate_arguments(parser, args, preview=True)
        print('Local bundle checksums verified; no device access')
        if args.layout:
            print('Planned layout for an untouched factory partition table:')
            print(reference_plan(parser, args).table())
        return
    validate_arguments(parser, args)
    if manifest['status'] != 'DEVICE_TESTED' and not args.allow_unverified:
        parser.error('bundle has not passed device testing; use --allow-unverified only for attended tests'
                     ' —— 该包未通过真机验证，请勿用于正式安装')
    restore = args.restore_partition_table
    if restore is None and not all((args.serial, args.backup or args.no_backup, args.erase_userdata)):
        parser.error('--serial, --backup (or --no-backup), --erase-userdata and --layout are required')
    if restore is None:
        if not args.no_backup:
            if args.backup.exists():
                parser.error('--backup must be a new directory')
            args.backup = args.backup.resolve()
            if args.backup == bundle or bundle in args.backup.parents:
                parser.error('private backups must be outside the served bundle directory')
        planned = None if args.keep_home else reference_plan(parser, args)
        rom_table, rom_images = ({}, {})
        if args.layout == 'dual':
            rom_table, rom_images = verify_rom(parser, args.rom_dir, args.android_boot)
            print('Stock ROM verified against the pinned ' + rom_table['rom'] + ' checksums')
            if args.android_boot is not None:
                print('Android boot image override: ' + str(args.android_boot.resolve()))
                print('  sha256 ' + rom_images['boot.img']['sha256'])
    else:
        restore = restore.resolve()
        if not (restore / 'gpt/manifest.json').is_file():
            parser.error('the backup directory has no saved partition table: ' + str(restore))
    if args.host_address:
        socket.inet_pton(socket.AF_INET, args.host_address)
    socket.inet_pton(socket.AF_INET, args.device_address)

    def fastboot(*arguments):
        result = subprocess.run(['fastboot', '-s', args.serial, *arguments], check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=1800)
        return result.stdout

    for name, value in (('product', 'liuqin'), ('unlocked', 'yes')):
        if not re.search(r'\b' + name + r':\s*' + value + r'\b', fastboot('getvar', name)):
            parser.error('Fastboot device check failed: ' + name +
                         ' —— 请确认设备是小米平板 6 Pro（liuqin）且已解锁 Bootloader')

    def partition_size(name, required=True):
        match = re.search(r'partition-size:' + re.escape(name) + r':\s*(0x[0-9a-fA-F]+)',
                          fastboot('getvar', 'partition-size:' + name))
        if not match:
            if required:
                raise RuntimeError('Cannot determine partition size: ' + name)
            return None
        return int(match[1], 16)

    if restore is None:
        # The partition table is the authority once the RAM installer is up;
        # this is the cheapest identity check that runs before booting it.
        userdata_size = partition_size('userdata', required=False)
        if userdata_size is not None and userdata_size < 16 * 1024 ** 3:
            parser.error('userdata is smaller than 16 GiB; only Xiaomi Pad 6 Pro (liuqin) is supported'
                         ' —— 请确认设备为小米平板 6 Pro（liuqin），不要用于其他机型')
        # The project boot image is flashed zero-filled to the whole boot
        # partition, so every partition it goes to must be the pinned size,
        # the image must fit it, and the padded image must go over USB in one
        # raw download rather than be re-sparsed by fastboot.
        boot_bytes = boot_partition_bytes(load_rom_table(parser))
        for slot in boot_slots(args.layout):
            reported = partition_size('boot_' + slot)
            if reported != boot_bytes:
                parser.error(f'boot_{slot} is {reported} bytes, not the {boot_bytes} bytes of the pinned'
                             ' layout —— boot 分区大小与本项目验证过的设备不一致')
        largest = max((bundle / name).stat().st_size for name in ('boot.img', 'installer.img'))
        if largest > boot_bytes:
            parser.error('boot image exceeds the reported boot partition size'
                         ' —— boot 镜像大于 boot 分区，包与设备不匹配')
        match = re.search(r'max-download-size:\s*(0x[0-9a-fA-F]+|[0-9]+)',
                          fastboot('getvar', 'max-download-size'))
        if not match or int(match[1], 0) < boot_bytes:
            parser.error('the bootloader cannot receive a whole boot partition in one download'
                         ' (max-download-size ' + (match[1] if match else 'unknown') + ')'
                         ' —— Bootloader 单次下载上限小于 boot 分区')
        print('Layout: ' + args.layout)
        if args.keep_home:
            print('Reinstalling on the existing split layout: the partition sizes are kept as'
                  ' they are and linux_home is left untouched.')
        else:
            print(planned.table())
        if not args.yes:
            if not sys.stdin.isatty():
                parser.error('data erasure needs an interactive confirmation; pass --yes to skip it'
                             ' —— 非交互环境请显式加 --yes 确认清空 userdata')
            print(f'About to REPARTITION tablet {args.serial} and erase every partition shown above.')
            print(f'即将修改平板 {args.serial} 的分区表并清空上表所列分区的全部数据。')
            if args.layout == 'dual':
                print('Both systems boot from slot A. Ubuntu\'s boot image is written to boot_a,'
                      ' with a fallback copy in boot_b; Android keeps its own userdata, and its'
                      ' boot image is stored on the Ubuntu root for liuqin-switch.')
                print('两个系统都从 A 槽启动：Ubuntu 的 boot 镜像写入 boot_a，boot_b 保存一份回退副本；'
                      'Android 保留独立的 userdata，其 boot 镜像存放在 Ubuntu 根分区，由 liuqin-switch 切换。')
                if args.android_boot is not None:
                    print('Android will boot the supplied image instead of the stock boot.img:')
                    print('  ' + str(args.android_boot.resolve()))
                    print('  sha256 ' + rom_images['boot.img']['sha256'])
                    print('Android 侧将启动上述替换镜像，而非原厂 boot.img。')
            if input('Type YES to continue / 输入 YES 继续: ') != 'YES':
                parser.error('data erasure was not confirmed —— 未确认，已取消')
    server = None
    scratch = None
    try:
        if restore is None:
            # Built before anything on the tablet changes, so that a host
            # without room for it stops the installation while it is harmless.
            # Host-side scratch, private to this run; the bundle directory is
            # served to the tablet and may be read-only.
            scratch = tempfile.TemporaryDirectory(prefix='liuqin-install-')
            padded_boot = pad_boot_image(bundle / 'boot.img', manifest['files']['boot.img'],
                                         boot_bytes, scratch.name)
            print(f'Project boot image zero-filled to the {boot_bytes}-byte boot partition:'
                  f' sha256 {padded_boot[1]}', flush=True)
        print('Booting the RAM installer...', flush=True)
        fastboot('boot', str(bundle / 'installer.img'))
        deadline = time.monotonic() + 120
        while True:
            try:
                boot_id = command(args.device_address,
                                  'test "$(cat /etc/liuqin-installer)" = liuqin && cat /proc/sys/kernel/random/boot_id').decode().strip()
                uuid.UUID(boot_id)
                break
            except (OSError, RuntimeError, ValueError):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Installer USB channel did not become ready; no formatting performed')
                time.sleep(2)

        def remote(text, timeout=60):
            guard = 'test "$(cat /proc/sys/kernel/random/boot_id)" = ' + shlex.quote(boot_id)
            return command(args.device_address, guard + ' && ' + text, timeout)

        def device_layout(*arguments, timeout=180):
            return remote(shlex.join(['sh', '/usr/lib/liuqin/install-layout.sh', boot_id, *arguments]),
                          timeout).decode()

        cmdline = shlex.split(remote('cat /proc/cmdline').decode())
        serial = next((item.split('=', 1)[1] for item in cmdline if item.startswith('androidboot.serialno=')), '')
        if serial != args.serial:
            raise RuntimeError('RAM device serial does not match the selected Fastboot device'
                               ' —— 序列号不一致，请检查 --serial 参数')
        release = remote('uname -r').decode().strip()
        if release != manifest['kernel_release']:
            raise RuntimeError('Installer kernel does not match this bundle'
                               ' —— 安装器内核与包不匹配，请使用同一发布包内的全部文件')
        if restore is not None:
            restore_partition_table(remote, device_layout, restore, args.serial)
            remote("(sleep 2; /usr/sbin/liuqin-reboot bootloader) >/dev/null 2>&1 &")
            print('Stock partition table restored. The tablet is returning to Fastboot.')
            return
        if not args.host_address:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
                route.connect((args.device_address, 2323))
                args.host_address = route.getsockname()[0]
        extra = {}
        if args.layout == 'dual':
            extra['/' + ANDROID_BOOT_URL] = rom_images[ANDROID_BOOT_IMAGE]['path']
        server = http.server.ThreadingHTTPServer((args.host_address, 0), bundle_handler(bundle, extra))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        backups = {}
        saved = None
        if args.no_backup:
            print('Partition backups skipped by explicit --no-backup request.', flush=True)
        else:
            args.backup.mkdir(mode=0o700, parents=True)
            for name in ('boot_a', 'boot_b', 'persist'):
                backups[name + '.img'] = backup_partition(remote, args.backup, name)
            (args.backup / 'SHA256SUMS').write_text(''.join(f'{h}  {n}\n' for n, h in backups.items()))
            saved = backup_partition_table(device_layout, args.backup, args.serial)
        disk = layout.parse_print(device_layout('report', ANCHOR))
        state = layout.classify(disk)
        print('Partition table state: ' + state)
        if state == 'unknown':
            raise RuntimeError(
                'this partition table is neither the factory layout nor one this installer created;'
                ' refusing to change it —— 分区表无法识别，拒绝修改。'
                ' 可用 --restore-partition-table 恢复出厂分区表')
        if state == 'stock' and args.keep_home:
            raise RuntimeError(
                'the tablet still carries the factory partition table, so there is no linux_home'
                ' to keep; install without --keep-home —— 设备仍是出厂分区表，没有可保留的 linux_home')
        if state == 'split':
            if not args.keep_home:
                raise RuntimeError(
                    'the tablet already carries a split layout; pass --keep-home to reinstall the'
                    ' system and keep linux_home. Changing the split requires restoring the stock'
                    ' partition table and installing again —— 已有分区布局不支持原地调整尺寸')
            print('Reinstalling on the existing split layout; linux_home is left untouched.')
        else:
            layout.check_stock_device(disk)
            region = disk.usable_end - disk.by_name(layout.USERDATA_NAME).start
            android, root = layout.resolve_sizes(args.layout, args.android_size, args.root_size, region)
            plan = layout.plan_layout(disk, args.layout, android, root)
            print(plan.table(), flush=True)
            confirm_plan(planned, plan)
            apply_layout(device_layout, disk, plan, saved, args.layout)
        flashes = []
        if args.layout == 'dual':
            flashes = stock_images_to_flash(device_layout, {
                name: entry for name, entry in rom_images.items() if name != ANDROID_BOOT_IMAGE})
            for index, (image_name, entry) in enumerate(flashes):
                name = entry['partition']
                if name in ANDROID_FIRMWARE_PARTITIONS:
                    if args.no_backup:
                        firmware_bytes = int(remote('/bin/busybox blockdev --getsize64 ' +
                                                    shlex.quote('/dev/disk/by-partlabel/' + name)).decode().strip())
                    else:
                        backups[name + '.img'] = backup_partition(remote, args.backup, name)
                        firmware_bytes = (args.backup / (name + '.img')).stat().st_size
                    firmware_scratch = Path(scratch.name) / name
                    firmware_scratch.mkdir()
                    image, digest = pad_boot_image(entry['path'], entry['sha256'],
                                                   firmware_bytes, firmware_scratch)
                    flashes[index] = (image_name, dict(entry, path=image,
                                                      flash_bytes=firmware_bytes,
                                                      flash_sha256=digest))
            if not args.no_backup:
                (args.backup / 'SHA256SUMS').write_text(''.join(f'{h}  {n}\n' for n, h in backups.items()))
        base = f'http://{args.host_address}:{server.server_port}'
        install = install_root_command(boot_id, base, bundle, manifest, args, rom_images)
        print('Installing Ubuntu into ' + layout.ROOT_NAME + '.', flush=True)
        result = remote(shlex.join(install), 3600)
        if b'liuqin-install: ROOT_INSTALLED' not in result:
            raise RuntimeError('Device did not confirm root installation')
        remote("(sleep 2; /usr/sbin/liuqin-reboot bootloader) >/dev/null 2>&1 &")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            devices = subprocess.check_output(['fastboot', 'devices'], text=True, timeout=10)
            if any(line.split()[0] == args.serial for line in devices.splitlines() if line.split()):
                break
            time.sleep(2)
        else:
            raise RuntimeError('Return to Fastboot not observed; boot partition was not flashed')
        finish_in_fastboot(fastboot, args.layout, flashes, *padded_boot, boot_bytes)
        print('Installation commands completed. First-boot verification is still required.')
    finally:
        if server:
            server.shutdown()
            server.server_close()
        if scratch:
            scratch.cleanup()


def boot_slots(mode):
    """The slots whose boot partition receives the project boot image, the
    booting slot first."""
    if mode == 'dual':
        return (DUAL_UBUNTU_SLOT, DUAL_FALLBACK_SLOT)
    return (LINUX_ONLY_UBUNTU_SLOT,)


def backup_partition(remote, directory, name):
    """Save and verify the entire original partition before a possible write."""
    print('Backing up and verifying ' + name + '...', flush=True)
    device = '/dev/disk/by-partlabel/' + name
    content = remote('test -b ' + device + ' && /bin/busybox base64 ' + device, 600)
    target = directory / (name + '.img')
    target.write_bytes(base64.b64decode(content, validate=False))
    target.chmod(0o600)
    expected = remote('/bin/busybox sha256sum ' + device, 120).decode().split()[0]
    if sha(target) != expected:
        raise RuntimeError('Backup verification failed: ' + name)
    return expected


def finish_in_fastboot(fastboot, mode, flashes, padded_image, padded_sha256, partition_bytes):
    """The fastboot tail of an installation, after the root is in place.

    Stock images go to their _a partitions first, then the project boot image
    -- zero-filled to the whole partition by pad_boot_image(), and checked
    again here -- to every slot boot_slots() names, and only then is the
    booting slot selected -- through the bootloader's own --set-active, the
    one operation that keeps its slot attributes, type GUIDs and boot LUN
    consistent.
    """
    if padded_image.stat().st_size != partition_bytes or sha(padded_image) != padded_sha256:
        raise RuntimeError('the zero-filled boot image changed before it was flashed; boot_a was not written')
    for name, entry in flashes:
        if entry['partition'] in ANDROID_FIRMWARE_PARTITIONS:
            path = Path(entry['path'])
            if path.stat().st_size != entry['flash_bytes'] or sha(path) != entry['flash_sha256']:
                raise RuntimeError('the zero-filled firmware image changed before flashing: ' + name)
    for name, entry in flashes:
        source = 'supplied' if entry.get('override') else 'stock'
        print(f'Writing the {source} {name} to {entry["partition"]}...', flush=True)
        fastboot('flash', entry['partition'], str(entry['path']))
    slots = boot_slots(mode)
    for slot in slots:
        role = 'fallback copy of the ' if slot != slots[0] else ''
        print(f'Writing the {role}project boot image to boot_{slot} (whole partition)...', flush=True)
        fastboot('flash', 'boot_' + slot, str(padded_image))
    fastboot('--set-active=' + slots[0])
    fastboot('reboot')


def install_root_command(boot_id, base_url, bundle, manifest, args, rom_images):
    """The install-root.sh command line.

    The seven fixed arguments come first, then the options in this order:
    ENABLE-USB-RESCUE (--enable-rescue), KEEP-HOME (--keep-home: check and keep
    the existing LIUQIN_HOME filesystem instead of formatting it), and in the
    dual layout SWITCH-STORE with its eight fields, which is always last.
    """
    install = ['sh', '/usr/lib/liuqin/install-root.sh', boot_id, base_url + '/rootfs.tar.gz',
               manifest['files']['rootfs.tar.gz'], str((bundle / 'rootfs.tar.gz').stat().st_size),
               'ERASE-LIUQIN-USERDATA', layout.ROOT_NAME, layout.HOME_NAME]
    if args.enable_rescue:
        install.append('ENABLE-USB-RESCUE')
    if args.keep_home:
        install.append('KEEP-HOME')
    if args.layout == 'dual':
        install += switch_store_arguments(base_url, manifest['files']['boot.img'], bundle / 'boot.img',
                                          rom_images[ANDROID_BOOT_IMAGE])
    return install


def switch_store_arguments(base_url, ubuntu_sha256, ubuntu_image, android_entry):
    """install-root.sh arguments that fill liuqin-switch's store in the dual layout."""
    return ['SWITCH-STORE',
            'ubuntu', base_url + '/boot.img', ubuntu_sha256, str(ubuntu_image.stat().st_size),
            'android', base_url + '/' + ANDROID_BOOT_URL, android_entry['sha256'],
            str(Path(android_entry['path']).stat().st_size)]


def bundle_handler(bundle, extra):
    """Serve the bundle directory, plus the exact extra paths given (URL path to
    file), which the dual layout uses for the Android boot image kept outside
    the bundle.  Nothing else outside the bundle is reachable."""
    class Handler(http.server.SimpleHTTPRequestHandler):
        def translate_path(self, path):
            clean = path.split('?', 1)[0].split('#', 1)[0]
            if clean in extra:
                return str(extra[clean])
            return super().translate_path(path)

    return functools.partial(Handler, directory=str(bundle))


def confirm_plan(expected, actual):
    """What was confirmed and what will be written must be the same decision.

    Only the sizes the operator chose are compared.  linux_home is the
    remainder by definition, so its size legitimately follows the capacity of
    the tablet in front of us rather than the preview's reference geometry.
    """
    if [item.name for item in expected.entries] != [item.name for item in actual.entries]:
        raise RuntimeError('the computed layout has a different shape from the confirmed one')
    for one, two in zip(expected.entries, actual.entries):
        if one.action != two.action or (one.name != layout.HOME_NAME and one.size != two.size):
            raise RuntimeError(
                'the tablet\'s partition table does not produce the layout that was confirmed '
                f'({one.name}: {one.action} {one.size} confirmed, {two.action} {two.size} '
                'computed); nothing was written')


def backup_partition_table(device_layout, directory, serial):
    """Save both GPT copies of the disk before any edit, with a hash manifest."""
    print('Backing up the partition table...', flush=True)
    target = directory / 'gpt'
    target.mkdir(mode=0o700)
    report = device_layout('report', ANCHOR)
    (target / 'sgdisk-p.txt').write_text(report)
    geometry = dict(line.split(None, 1) for line in device_layout('geometry', ANCHOR).splitlines() if line)
    copies = {}
    for half in ('head', 'tail'):
        data = base64.b64decode(device_layout('backup-gpt', ANCHOR, half), validate=False)
        expected = 6 * int(geometry['sector'])
        if len(data) != expected:
            raise RuntimeError(f'the {half} GPT copy is {len(data)} bytes, not {expected}')
        path = target / f'sda-{half}.bin'
        path.write_bytes(data)
        path.chmod(0o600)
        copies[path.name] = hashlib.sha256(data).hexdigest()
    saved = {'serial': serial, 'disk': geometry['disk'], 'sector_size': int(geometry['sector']),
             'sectors': int(geometry['sectors']), 'files': copies}
    (target / 'manifest.json').write_text(json.dumps(saved, indent=2) + '\n')
    print('Partition table saved to ' + str(target))
    return target


# The installer RAM image is built from a fixed directory list that has no
# /tmp, so anything the host writes on the device goes to the same scratch
# directory tools/lib/install-layout.sh reads from, and the host creates it
# before writing into it rather than assuming it is already there.
LAYOUT_WORK = '/run/liuqin-layout'


def stage_blob(remote, name, data):
    """Push a small verified blob to the RAM image in base64 chunks."""
    remote('mkdir -p ' + shlex.quote(LAYOUT_WORK))
    path = LAYOUT_WORK + '/liuqin-gpt-' + name + '.b64'
    text = base64.b64encode(data).decode()
    remote(':>' + path)
    # Each chunk is one RAM shell line, which command() bounds by ash's
    # line-editing buffer (RAM_SHELL_LINE_BYTES).  500 base64 characters plus
    # the path, the boot_id guard and the markers stay well inside it.
    for index in range(0, len(text), 500):
        remote('printf %s ' + shlex.quote(text[index:index + 500]) + ' >>' + path)
    remote('printf "\\n" >>' + path)


def restore_partition_table(remote, device_layout, directory, serial):
    """Restore the saved stock table, refusing any dump that is not the saved one."""
    saved = json.loads((directory / 'gpt/manifest.json').read_text())
    if saved.get('serial') != serial:
        raise RuntimeError('this backup was taken from serial ' + str(saved.get('serial')) +
                           ', not ' + serial + ' —— 拒绝把其他设备的分区表写入本机')
    geometry = dict(line.split(None, 1) for line in device_layout('geometry', ANCHOR).splitlines() if line)
    if int(geometry['sectors']) != saved['sectors'] or int(geometry['sector']) != saved['sector_size']:
        raise RuntimeError('the tablet geometry differs from the saved partition table')
    for name, digest in saved['files'].items():
        path = directory / 'gpt' / name
        if not path.is_file() or sha(path) != digest:
            raise RuntimeError('the saved partition table does not match its manifest: ' + name +
                               ' —— 备份已损坏或被改动，拒绝写入')
        stage_blob(remote, name.split('-')[1].split('.')[0], path.read_bytes())
    print('Restoring the saved partition table...', flush=True)
    if 'GPT_RESTORED' not in device_layout('restore-gpt', ANCHOR):
        raise RuntimeError('the device did not confirm the partition-table restore')


def apply_layout(device_layout, before, plan, saved, mode):
    """Wipe stale metadata, write the table once, verify it, restore on failure."""
    userdata = before.by_name(layout.USERDATA_NAME)
    info = layout.parse_info(device_layout('info', layout.USERDATA_NAME))
    if info['first_sector'] != userdata.first_sector or info['name'] != layout.USERDATA_NAME:
        raise RuntimeError('the userdata partition reports inconsistent geometry')
    # Android reformats userdata and metadata on first boot, but only if the
    # stale file-based-encryption keys are gone.  Zeroing the head of userdata
    # also removes the LIUQIN_ROOT superblock of the old whole-disk root, so no
    # second filesystem can answer to that label afterwards.
    print('Wiping stale filesystem and encryption metadata...', flush=True)
    device_layout('wipe-head', layout.USERDATA_NAME, str(layout.WIPE_BYTES), timeout=300)
    if mode == 'dual':
        device_layout('wipe-head', layout.METADATA_NAME, str(layout.WIPE_BYTES), timeout=300)
    disk_path = json.loads((saved / 'manifest.json').read_text())['disk']
    arguments = layout.sgdisk_arguments(plan, disk_path, info)
    print('Writing the new partition table...', flush=True)
    try:
        if 'LAYOUT_APPLIED' not in device_layout('apply', ANCHOR, *arguments, timeout=300):
            raise RuntimeError('the device did not confirm the partition-table edit')
        after = layout.parse_print(device_layout('report', ANCHOR))
        layout.verify_applied(after, plan)
        if plan.entry(layout.USERDATA_NAME).action != 'delete':
            layout.verify_preserved(info, layout.parse_info(device_layout('info', layout.USERDATA_NAME)))
    except (RuntimeError, layout.LayoutError) as error:
        print('Partition-table edit failed; restoring the saved table.', file=sys.stderr, flush=True)
        raise RuntimeError(str(error) + ' —— 分区表写入失败，请用 --restore-partition-table '
                           + str(saved.parent) + ' 恢复出厂分区表后重试')
    print('New partition table verified.')


def stock_images_to_flash(device_layout, images):
    """Flash only the stock images whose bytes are not already on the tablet.

    A stock image is usually shorter than its partition and the remainder is
    zero, so the comparison is the image digest over the first N bytes plus a
    zero check over everything after them.
    """
    selected = []
    for name, entry in sorted(images.items()):
        if entry.get('override'):
            # An override is what the operator asked for; it is written whether
            # or not the partition already carries those bytes.
            print(f'{name}: supplied override, will be written to {entry["partition"]}')
            selected.append((name, entry))
            continue
        if entry.get('sparse'):
            # An Android sparse image cannot be compared against the raw
            # partition, so it is always written when the dual layout asks for it.
            print(f'{name}: sparse image, will be written to {entry["partition"]}')
            selected.append((name, entry))
            continue
        report = dict(line.split(None, 1) for line in
                      device_layout('digest', entry['partition'], str(entry['bytes']),
                                    timeout=900).splitlines() if line)
        padding = int(report['size']) - entry['bytes']
        if report['content'] != entry['sha256']:
            reason = 'content differs'
        elif report['padding'] != zero_digest(padding):
            reason = 'the bytes after the image are not zero'
        else:
            print(f'{name}: already present on {entry["partition"]}, not written')
            continue
        print(f'{name}: {reason}, will be written to {entry["partition"]}')
        selected.append((name, entry))
    return selected


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError, layout.LayoutError) as error:
        sys.exit('Installation stopped: ' + str(error))
