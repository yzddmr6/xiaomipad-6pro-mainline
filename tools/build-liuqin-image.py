#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Assemble matching device packages, rootfs and boot from prepared inputs."""
import argparse
import fcntl
import fnmatch
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import zipfile


# Files that decide what the installer writes to the tablet's storage: the
# partition layout engine, the host driver and the two device-side scripts.
# A change to any of them invalidates every earlier device test, because the
# next bundle will lay the disk out differently from the one that was tested.
INSTALLER_PATHS = ('tools/install-liuqin.py', 'tools/lib/liuqin_layout.py')
INSTALLER_GLOB = 'tools/lib/install-*.sh'


def installation_path_changes(project, env):
    """List the installation-path files that changed since the last release tag.

    Approach 4 of the dual-boot plan: a layout change must go through a full
    attended installation, never an incremental one.  The rule is a build-time
    assertion rather than a note in a document, because a note cannot fail a
    release.
    """
    described = subprocess.run(['git', '-C', str(project), 'describe', '--tags', '--abbrev=0'],
                               env=env, capture_output=True, text=True)
    changed = set()
    if described.returncode != 0:
        # No release tag yet: nothing has been published, so nothing can be an
        # increment on top of a tested bundle.  Require the mark explicitly.
        changed.update(INSTALLER_PATHS)
    else:
        tag = described.stdout.strip()
        listed = subprocess.check_output(
            ['git', '-C', str(project), 'diff', '--name-only', tag + '..HEAD'], env=env, text=True)
        dirty = subprocess.check_output(
            ['git', '-C', str(project), 'status', '--porcelain', '--'], env=env, text=True)
        listed += ''.join(line[3:] + '\n' for line in dirty.splitlines())
        for path in listed.split():
            if path in INSTALLER_PATHS or fnmatch.fnmatch(path, INSTALLER_GLOB):
                changed.add(path)
    return sorted(changed)


INPUTS = {
    'UBUNTU_DESKTOP_ROOT', 'DESKTOP_ROOTFS_MANIFEST', 'FIRMWARE_POOL',
    'FIRMWARE_TREE', 'FIRMWARE_MANIFEST_SHA256', 'AUDIO_TOPOLOGY',
    'WLAN_HSP2_TUPLE', 'STOCK_OVERLAY_DIR', 'STOCK_BASE_DIR',
    'SENSOR_STACK_TAR', 'SENSOR_STACK_SHA256', 'POWER_SETTINGS_BINARY',
    'POWER_SETTINGS_MANIFEST', 'BUSYBOX', 'MKBOOTIMG_DIR',
    'FINGERPRINT_BUNDLE',
}


def main():
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True,
                        help='Local JSON object of prepared-input environment variables')
    parser.add_argument('--kernel-out', type=Path, required=True)
    parser.add_argument('--kernel-source', type=Path,
                        default=project.parent / 'linux-sm8450-liuqin')
    parser.add_argument('--out', type=Path, default=project / 'out/image')
    parser.add_argument('--stage', choices=['all', 'modules', 'debs', 'copy', 'install',
                                          'assemble', 'manifest', 'boot', 'runtime', 'installer',
                                          'pack', 'bundle', 'release-assets'], default='all')
    parser.add_argument('--device-tested', action='store_true',
                        help='Mark release assets after completing device installation tests')
    args = parser.parse_args()
    if args.device_tested and args.stage != 'release-assets':
        parser.error('--device-tested is only valid with --stage release-assets')
    supplied = json.loads(args.inputs.read_text())
    if set(supplied) != INPUTS or not all(isinstance(v, str) and v for v in supplied.values()):
        parser.error('Input keys must match: ' + ', '.join(sorted(INPUTS)))
    for key, value in supplied.items():
        if not key.endswith('_SHA256'):
            supplied[key] = str((args.inputs.resolve().parent / value).resolve())
            value = supplied[key]
        if not key.endswith('_SHA256') and not Path(value).exists():
            parser.error('Prepared input missing: ' + key)
    out, kernel = args.out.resolve(), args.kernel_out.resolve()
    kernel_source = args.kernel_source.resolve()
    if project / 'out' not in out.parents:
        parser.error('--out must be inside the project out directory')
    lock = json.loads((project / 'kernel/source.json').read_text())
    subprocess.run(['sha256sum', '-c', '--quiet', 'SHA256SUMS'], cwd=kernel, check=True)
    info = json.loads((kernel / 'build-info.json').read_text())
    if info['commit'] != lock['commit']:
        parser.error('Kernel build must match the product lock, not a development override')
    if info['config_sha256'] != lock['config_sha256']:
        parser.error('Kernel configuration does not match the product lock')
    if hashlib.sha256((kernel / '.config').read_bytes()).hexdigest() != lock['config_sha256']:
        parser.error('Kernel configuration bytes differ from the product lock')
    imported = info.get('build_kind') == 'imported-clean-repro'
    if info.get('build_kind') == 'development' or imported:
        # A development build is a `build-liuqin-kernel.py --revision` build.
        # It was made from the same clean source, fragments and builder as a
        # product build; only its label records the lock of that day.  Once
        # the lock has moved to that very revision, the output is the product
        # input by content.  Accept it only when the fragments it was built
        # from are byte-identical to the ones the lock names today.
        fragments = {path: hashlib.sha256((project / path).read_bytes()).hexdigest()
                     for path in lock['config_fragments']}
        if info.get('fragments') != fragments:
            parser.error('Development kernel build used other config fragments than the product lock')
        print('Kernel: ' + info['build_kind'] + ' of locked commit ' + lock['commit'][:12] +
              '; commit, configuration and fragments match the lock', flush=True)
    elif info.get('build_kind') != 'product-input':
        parser.error('Kernel build must match the product lock, not a development override')
    env = os.environ.copy()
    # Privileged assembly reads exactly these user-owned repositories.
    env.update(GIT_CONFIG_COUNT='2', GIT_CONFIG_KEY_0='safe.directory',
               GIT_CONFIG_VALUE_0=str(project), GIT_CONFIG_KEY_1='safe.directory',
               GIT_CONFIG_VALUE_1=str(kernel_source))
    env.update(supplied, KERNEL_SOURCE=str(kernel_source),
               KERNEL_DIR=str(kernel_source), KERNEL_COMMIT=lock['commit'],
               KERNEL_OUT=str(kernel), KERNEL_IMAGE=str(kernel / 'arch/arm64/boot/Image'),
               KERNEL_DTB=str(kernel / 'arch/arm64/boot/dts' / lock['dtb']),
               KERNEL_MODULES_DIR=str(out / 'modules'), DEBS_DIR=str(out / 'debs'),
               NATIVE_ROOT_HASHES=str(out / 'root/native-root.hashes'))
    stages = {
        'modules': ('build-liuqin-kernel-modules.sh', [], out / 'modules'),
        'debs': ('build-liuqin-debs.sh', ['all'], out / 'debs'),
        'copy': ('build-liuqin-native-root.sh', ['copy'], out / 'root'),
        'install': ('build-liuqin-native-root.sh', ['debs'], out / 'root'),
        'assemble': ('build-liuqin-native-root.sh', ['assemble'], out / 'root'),
        'manifest': ('build-liuqin-native-root.sh', ['manifest'], out / 'root'),
        'boot': ('build-liuqin-native-boot.sh', [], out / 'boot'),
        'runtime': ('lib/build-installer-runtime.py', ['--root', supplied['UBUNTU_DESKTOP_ROOT'],
                                                     '--out', str(out / 'installer-runtime')], out / 'installer-runtime'),
        'installer': ('build-liuqin-native-boot.sh', [], out / 'installer'),
        'pack': ('build-liuqin-native-root.sh', ['pack'], out / 'root'),
    }
    selected = [*stages, 'bundle'] if args.stage == 'all' else [args.stage]
    if args.stage == 'all' and out.exists():
        parser.error('all requires a fresh output; resume with --stage instead')
    out.mkdir(parents=True, exist_ok=True)
    owner = (out / '.image.lock').open('a')
    try:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error('another assembly owns this output')
    for stage in selected:
        if stage == 'modules' and imported:
            # Reuse the product module tree already normalized and compared by
            # the clean A/B build. Never run Kbuild inside a frozen proof OUT.
            prepared = info.get('prepared_modules', {})
            names = {'modules.tar', 'modules.manifest', 'kernel.release', 'kernel.commit'}
            if (prepared.get('directory') != 'prepared-modules' or
                    set(prepared.get('files', {})) != names):
                parser.error('Imported kernel lacks its exact prepared module set')
            source = kernel / prepared['directory']
            if ((source / 'kernel.commit').read_text().strip() != lock['commit'] or
                    (source / 'kernel.release').read_text() !=
                    (kernel / 'include/config/kernel.release').read_text()):
                parser.error('Imported modules do not belong to the locked kernel')
            for name, expected in prepared['files'].items():
                with (source / name).open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                        parser.error('Imported module artifact mismatch: ' + name)
            shutil.copytree(source, out / 'modules')
            print('Stage: modules (verified clean/repro product artifacts)', flush=True)
            continue
        if stage == 'release-assets':
            changed = installation_path_changes(project, env)
            if changed and not args.device_tested:
                parser.error(
                    'the installation path changed since the last release tag, so this bundle '
                    'must be installed on a tablet and marked with --device-tested before it '
                    'becomes a release asset:\n  ' + '\n  '.join(changed))
            destination = out / 'release-assets'
            shutil.copytree(out / 'bundle', destination,
                            ignore=shutil.ignore_patterns('rootfs.tar.gz'))
            metadata = json.loads((destination / 'bundle.json').read_text())
            for name in ('INSTALL-TESTING.md', 'INSTALL-TESTING.zh-CN.md',
                         'FINGERPRINT.md', 'FINGERPRINT.zh-CN.md'):
                shutil.copyfile(project / 'docs' / name, destination / name)
                metadata['files'][name] = hashlib.sha256((destination / name).read_bytes()).hexdigest()
            if args.device_tested:
                metadata['status'] = 'DEVICE_TESTED'
                metadata['tested_storage_layout'] = 'known 256 GB layout'
            # Each GitHub Release asset must remain below its 2 GiB limit.
            subprocess.run(['split', '-b', '1900M', '-d', '-a', '2',
                            str(out / 'bundle/rootfs.tar.gz'),
                            str(destination / 'rootfs.tar.gz.part-')], check=True)
            (destination / 'bundle.json').write_text(json.dumps(metadata, indent=2) + '\n')
            hashes = dict(metadata['files'])
            for part in sorted(destination.glob('rootfs.tar.gz.part-*')):
                with part.open('rb') as stream:
                    hashes[part.name] = hashlib.file_digest(stream, 'sha256').hexdigest()
            hashes['bundle.json'] = hashlib.sha256((destination / 'bundle.json').read_bytes()).hexdigest()
            (destination / 'SHA256SUMS').write_text(''.join(f'{h}  {n}\n' for n, h in hashes.items()))
            continue
        if stage == 'bundle':
            destination = out / 'bundle'
            destination.mkdir()
            # Ship the project-owned Android return module and its host tools
            # with the same image; upstream KernelSU binaries remain fetched
            # from their pinned release rather than copied from a test tablet.
            ksu_out = out / 'ksu-module'
            subprocess.run(['sh', str(project / 'tools/build-ksu-module.sh')],
                           env=dict(env, OUT_DIR=str(ksu_out)), check=True)
            ksu_modules = list(ksu_out.glob('liuqin_boot_ubuntu-*.zip'))
            if len(ksu_modules) != 1:
                parser.error('Expected exactly one KernelSU return module')
            dualboot_tools = out / 'dualboot-tools.zip'
            with zipfile.ZipFile(dualboot_tools, 'w', zipfile.ZIP_DEFLATED) as archive:
                for name in ('tools/fetch-kernelsu-assets.sh',
                             'tools/patch-android-boot-ksu.py',
                             'tools/lib/kernelsu-assets.json',
                             'device/android/ksu-boot-ubuntu/README.md', 'LICENSE', 'NOTICE'):
                    entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    entry.external_attr = 0o644 << 16
                    archive.writestr(entry, (project / name).read_bytes())
            files = {'boot.img': out / 'boot/boot-liuqin-native.img',
                     'installer.img': out / 'installer/boot-liuqin-native.img',
                     'rootfs.tar.gz': out / 'root/rootfs.tar.gz',
                     'install.py': project / 'tools/install-liuqin.py',
                     'liuqin_layout.py': project / 'tools/lib/liuqin_layout.py',
                     'liuqin-rom-images.json': project / 'tools/lib/liuqin-rom-images.json',
                     'INSTALL-TESTING.md': project / 'docs/INSTALL-TESTING.md',
                     'INSTALL-TESTING.zh-CN.md': project / 'docs/INSTALL-TESTING.zh-CN.md',
                     'NOTICE': project / 'NOTICE', 'LICENSE': project / 'LICENSE',
                     ksu_modules[0].name: ksu_modules[0], 'dualboot-tools.zip': dualboot_tools,
                     'FINGERPRINT.md': project / 'docs/FINGERPRINT.md',
                     'FINGERPRINT.zh-CN.md': project / 'docs/FINGERPRINT.zh-CN.md',
                     'fingerprint-build-inputs.tar.gz': Path(supplied['FINGERPRINT_BUNDLE'])}
            if imported:
                proof = out / 'kernel-build-proof.zip'
                proof_files = [kernel / 'build-info.json', kernel / 'SHA256SUMS',
                               kernel / '.config', *sorted((kernel / 'provenance').iterdir())]
                with zipfile.ZipFile(proof, 'w', zipfile.ZIP_DEFLATED) as archive:
                    for source in proof_files:
                        entry = zipfile.ZipInfo(source.relative_to(kernel).as_posix(),
                                                date_time=(1980, 1, 1, 0, 0, 0))
                        entry.compress_type = zipfile.ZIP_DEFLATED
                        entry.external_attr = 0o644 << 16
                        archive.writestr(entry, source.read_bytes())
                files['kernel-build-proof.zip'] = proof
            hashes = {}
            for name, source in files.items():
                if name in ('boot.img', 'installer.img', 'rootfs.tar.gz'):
                    os.link(source, destination / name)
                else:
                    shutil.copyfile(source, destination / name)
                with source.open('rb') as stream:
                    hashes[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
            metadata = {'device': 'liuqin', 'status': 'OFFLINE_ASSEMBLED',
                        'kernel_commit': lock['commit'],
                        'project_commit': subprocess.check_output(['git', '-C', str(project), 'rev-parse', 'HEAD'], env=env, text=True).strip(),
                        'project_dirty': bool(subprocess.check_output(['git', '-C', str(project), 'status', '--porcelain'], env=env)),
                        'kernel_release': (kernel / 'include/config/kernel.release').read_text().strip(),
                        'features': {'displayport': True,
                                     'native_fingerprint': bool(supplied.get('FINGERPRINT_BUNDLE')),
                                     'kernelsu_return_module': ksu_modules[0].name},
                        'files': hashes}
            (destination / 'bundle.json').write_text(json.dumps(metadata, indent=2) + '\n')
            hashes['bundle.json'] = hashlib.sha256((destination / 'bundle.json').read_bytes()).hexdigest()
            (destination / 'SHA256SUMS').write_text(''.join(f'{h}  {n}\n' for n, h in hashes.items()))
            continue
        script, arguments, destination = stages[stage]
        print('Stage: ' + stage, flush=True)
        stage_env = dict(env, OUT_DIR=str(destination))
        if stage == 'installer':
            stage_env['INSTALLER_RUNTIME'] = str(out / 'installer-runtime')
        subprocess.run(['python3' if script.endswith('.py') else 'sh',
                        str(project / 'tools' / script), *arguments], env=stage_env, check=True)
    print('Selected assembly stages completed; device validation is separate')


if __name__ == '__main__':
    main()
