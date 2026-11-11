#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bundle distro e2fsck and its complete ARM64 ELF closure from a fixed Ubuntu root.

Use the verified Ubuntu root used for the product build, before unmounting it.
No host libraries or executables are copied. Every input byte is recorded.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


def build(root, out):
    root = root.resolve()
    if out.exists():
        raise ValueError('output must be a new directory')
    libraries = root / 'usr/lib/aarch64-linux-gnu'
    pending = [('usr/sbin/e2fsck', root / 'usr/sbin/e2fsck')]
    files = {}
    while pending:
        relative, source = pending.pop()
        if relative in files:
            continue
        # Resolve symlinks inside the distribution, never against the host root.
        for _ in range(32):
            if not source.is_symlink():
                break
            target = source.readlink()
            source = root / str(target).lstrip('/') if target.is_absolute() else source.parent / target
        source = source.resolve()
        if not source.is_relative_to(root):
            raise ValueError('input escapes Ubuntu root: ' + relative)
        header = subprocess.check_output(['readelf', '-h', str(source)], text=True)
        if not re.search(r'Machine:\s+AArch64', header):
            raise ValueError('expected ARM64 ELF: ' + relative)
        info = subprocess.check_output(['readelf', '-l', '-d', str(source)], text=True)
        for name in re.findall(r'\(NEEDED\).*?\[([^]\n]+)\]', info):
            pending.append(('lib/aarch64-linux-gnu/' + name, libraries / name))
        for interpreter in re.findall(r'Requesting program interpreter: ([^]\n]+)', info):
            if interpreter != '/lib/ld-linux-aarch64.so.1':
                raise ValueError('unexpected ELF interpreter: ' + interpreter)
            pending.append((interpreter.lstrip('/'), libraries / Path(interpreter).name))
        files[relative] = source
    # e2fsck.conf is optional; preserve any distro policy when present.
    config = root / 'etc/e2fsck.conf'
    if config.exists():
        files['etc/e2fsck.conf'] = config
    out.mkdir(parents=True)
    manifest = {}
    for relative, source in sorted(files.items()):
        destination = out / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(0o644 if relative.startswith('etc/') else 0o755)
        manifest[relative] = hashlib.sha256(destination.read_bytes()).hexdigest()
    (out / 'root-fsck-files.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    build(args.root, args.out)


if __name__ == '__main__':
    main()
