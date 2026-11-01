#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build the device's GNOME Settings program from the Ubuntu source package."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def prepare(project, out, lock):
    for item in lock['patches']:
        if sha(project / item['path']) != item['sha256']:
            raise SystemExit('Settings patch differs from source.json: ' + item['path'])
    stamp = out / 'source-inputs.json'
    source = out / 'source'
    if stamp.exists():
        if json.loads(stamp.read_text()) != lock or not (source / 'debian/control').is_file():
            raise SystemExit('Settings inputs changed; choose a new --out directory')
        return source
    if source.exists():
        raise SystemExit('Incomplete source preparation; remove that source directory before retrying')
    downloads = project / 'tools/local/downloads'
    downloads.mkdir(parents=True, exist_ok=True)
    for name, expected in lock['archives'].items():
        archive = downloads / name
        if not archive.exists() or sha(archive) != expected:
            temporary = archive.with_suffix(archive.suffix + '.part')
            run('curl', '--fail', '--location', '--output', str(temporary), lock['base_url'] + name)
            if sha(temporary) != expected:
                raise SystemExit('Source archive checksum mismatch: ' + name)
            temporary.replace(archive)
    source.mkdir()
    orig, debian = list(lock['archives'])
    run('tar', '-xf', str(downloads / orig), '--strip-components=1', '-C', str(source))
    run('tar', '-xf', str(downloads / debian), '-C', str(source))
    patches = source / 'debian/patches'
    series = patches / 'series'
    names = []
    for item in lock['patches']:
        name = 'liuqin-' + Path(item['path']).name
        shutil.copyfile(project / item['path'], patches / name)
        names.append(name)
    series.write_text(series.read_text().rstrip() + '\n' + '\n'.join(names) + '\n')
    run('dpkg-source', '--before-build', str(source))
    stamp.write_text(json.dumps(lock, indent=2) + '\n')
    return source


def build(rootfs, source, out, lock, jobs):
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo to build in an isolated ARM64 root filesystem')
    os.unshare(os.CLONE_NEWNS)
    run('mount', '--make-rprivate', '/')
    if not (rootfs / 'usr/lib/systemd/systemd').is_file():
        raise SystemExit('Missing Ubuntu ARM64 root filesystem; use --rootfs')
    manifest = Path(str(rootfs) + '.manifest')
    if not manifest.is_file():
        raise SystemExit('Root filesystem manifest is missing')
    inputs = {'rootfs': str(rootfs), 'rootfs_manifest_sha256': sha(manifest),
              'source': lock, 'builder_sha256': sha(__file__)}
    stamp = out / 'build-inputs.json'
    if stamp.exists():
        previous = json.loads(stamp.read_text())
        # Compiler configuration comes from the locked Debian rules. Wrapper-only
        # changes can reuse Ninja objects; source or root changes cannot.
        compatible = lambda data: {k: v for k, v in data.items() if k != 'builder_sha256'}
        if compatible(previous) != compatible(inputs):
            raise SystemExit('Build inputs changed; choose a new --out directory')
    if not stamp.exists() and (out / 'work').exists():
        raise SystemExit('Build workspace has no input identity; choose a new --out directory')
    stamp.write_text(json.dumps(inputs, indent=2) + '\n')
    for path in (rootfs, out):
        if any(c in str(path) for c in ',:\n'):
            raise SystemExit('Overlay paths must not contain commas, colons or newlines')
    work = out / 'work'
    for name in ('upper', 'overlay', 'root'):
        (work / name).mkdir(parents=True, exist_ok=True)
    root = work / 'root'
    if os.path.ismount(root):
        raise SystemExit('Settings build root is already mounted')
    run('mount', '-t', 'overlay', 'overlay', '-o',
        f'lowerdir={rootfs},upperdir={work / "upper"},workdir={work / "overlay"}', str(root))
    mounted = []
    try:
        for filesystem in ('proc', 'sysfs'):
            target = root / ('proc' if filesystem == 'proc' else 'sys')
            target.mkdir(exist_ok=True)
            run('mount', '-t', filesystem, '-o', 'ro,nosuid,nodev,noexec', filesystem, str(target))
            mounted.append(target)
        guest_source = root / 'build/source'
        if not guest_source.exists():
            shutil.copytree(source, guest_source, symlinks=True)
        (root / 'etc/apt/sources.list.d/cdrom.sources').unlink(missing_ok=True)
        # APT hooks are lists; assigning a scalar does not remove inherited entries.
        (root / 'etc/apt/apt.conf.d/zz-liuqin-build').write_text(
            '#clear APT::Update::Post-Invoke;\n#clear APT::Update::Post-Invoke-Success;\n')
        initramfs_config = root / 'etc/initramfs-tools/update-initramfs.conf'
        initramfs_config.parent.mkdir(parents=True, exist_ok=True)
        initramfs_config.write_text('update_initramfs=no\n')
        resolver = root / 'run/systemd/resolve/stub-resolv.conf'
        resolver.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile('/etc/resolv.conf', resolver)
        policy = root / 'usr/sbin/policy-rc.d'
        policy.write_text('#!/bin/sh\nexit 101\n')
        policy.chmod(0o755)
        for name, major, minor in [('null', 1, 3), ('zero', 1, 5), ('random', 1, 8), ('urandom', 1, 9)]:
            device = root / 'dev' / name
            device.parent.mkdir(exist_ok=True)
            if not device.exists():
                os.mknod(device, stat.S_IFCHR | 0o666, os.makedev(major, minor))
            device.chmod(0o666)
        for name, target in [('fd', '/proc/self/fd'), ('stdin', '/proc/self/fd/0'),
                             ('stdout', '/proc/self/fd/1'), ('stderr', '/proc/self/fd/2')]:
            link = root / 'dev' / name
            if not link.is_symlink() and not link.exists():
                link.symlink_to(target)
        script = '''set -eu
export DEBIAN_FRONTEND=noninteractive
cd /build/source
test "$(dpkg --print-architecture)" = arm64
dpkg --configure -a
if ! dpkg-checkbuilddeps >/dev/null 2>&1; then
    apt-get update
    apt-get build-dep -y .
fi
export DEB_BUILD_OPTIONS=nocheck
dpkg-buildpackage --rules-target=override_dh_auto_configure --no-sign
ninja -C obj-aarch64-linux-gnu -j"$1" shell/gnome-control-center
strip --strip-unneeded -o /build/gnome-control-center obj-aarch64-linux-gnu/shell/gnome-control-center
dpkg-query -W -f='${binary:Package}\t${Version}\n' > /build/build-packages.tsv
'''
        run('chroot', str(root), '/bin/sh', '-s', '--', str(jobs), input=script, text=True)
        binary = out / 'gnome-control-center'
        shutil.copyfile(root / 'build/gnome-control-center', binary)
        binary.chmod(0o755)
        run('readelf', '-h', str(binary), stdout=subprocess.DEVNULL)
        shutil.copyfile(root / 'build/build-packages.tsv', out / 'build-packages.tsv')
        info = {'source': lock, 'binary_sha256': sha(binary),
                'build_packages_sha256': sha(out / 'build-packages.tsv')}
        (out / 'build-info.json').write_text(json.dumps(info, indent=2) + '\n')
        print('Built GNOME Settings:', binary, flush=True)
    finally:
        for target in reversed(mounted):
            run('umount', str(target))
        run('umount', str(root))


def main():
    os.umask(0o022)
    project = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--out', type=Path, default=project / 'out/gnome-control-center')
    parser.add_argument('--rootfs', type=Path, default=project / 'tools/local/ubuntu-desktop-26.04-arm64/rootfs')
    parser.add_argument('--jobs', type=int, default=min(os.cpu_count() or 1, 8))
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    out = args.out.resolve()
    if project / 'out' not in out.parents:
        parser.error('--out must be below this project\'s out directory')
    out.mkdir(parents=True, exist_ok=True)
    with (out / '.build.lock').open('a') as owner:
        try:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error('another writer owns this output directory')
        lock = json.loads((project / 'device/gnome-control-center/source.json').read_text())
        source = prepare(project, out, lock)
        if args.prepare_only:
            print('Prepared GNOME Settings source:', source)
        else:
            build(args.rootfs.resolve(), source, out, lock, args.jobs)


if __name__ == '__main__':
    main()
