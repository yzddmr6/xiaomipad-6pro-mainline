#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Execute the shipped migration and preformat integration in BusyBox, no /tmp."""
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import tempfile

project = Path(__file__).resolve().parents[1]
assert os.geteuid() == 0, 'run with sudo (chroot and numeric ownership assertions)'

def put(root, name, text, mode=0o644, gid=0):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    p.chmod(mode)
    os.chown(p, 0, gid)
    return p

def databases(root, base, users=()):
    values = dict(passwd='root:x:0:0:root:/root:/bin/sh\n', shadow='root:!:20000:0:99999:7:::\n',
                  group='root:x:0:\nsudo:x:27:root\nshadow:x:42:\n',
                  gshadow='root:*::\nsudo:*::root\nshadow:*::\n')
    for i, user in enumerate(users, 1000):
        values['passwd'] += f'{user}:x:{i}:{i}:{user}:/home/{user}:/bin/sh\n'
        values['shadow'] += f'{user}:$6$fixture$hash:20000:0:99999:7:::\n'
        values['group'] += f'{user}:x:{i}:root\n'
        values['gshadow'] += f'{user}:!:root:root\n'
    if users:
        values['group'] = values['group'].replace('sudo:x:27:root', 'sudo:x:27:root,' + ','.join(users))
        values['gshadow'] = values['gshadow'].replace('sudo:*::root', 'sudo:*::root,' + ','.join(users))
    for name, text in values.items():
        private = name in ('shadow', 'gshadow')
        put(root, base + '/etc/' + name, text, 0o640 if private else 0o644, 42 if private else 0)

def fixture(root, users=('alice',)):
    for d in ('bin', 'dev', 'run', 'usr/lib/liuqin', 'usr/bin', 'old', 'new'):
        (root / d).mkdir(parents=True, exist_ok=True)
    shutil.copy2(shutil.which('busybox'), root / 'bin/busybox')
    for applet in subprocess.check_output(['busybox', '--list'], text=True).split():
        if not (root / 'bin' / applet).exists(): (root / 'bin' / applet).symlink_to('busybox')
    (root / 'usr/bin/tar').symlink_to('/bin/busybox')
    # BusyBox chooses its applet using argv[0].
    (root / 'dev/null').touch()
    for name in ('install-keep-home.sh', 'install-keep-home.awk'):
        shutil.copy2(project / 'tools/lib' / name, root / 'usr/lib/liuqin' / name)
    databases(root, 'old', users)
    databases(root, 'new')
    put(root, 'old/etc/NetworkManager/system-connections/wifi.nmconnection', '[connection]\nid=wifi\n')
    put(root, 'old/var/lib/bluetooth/AA:BB/CC:DD/info', '[General]\nName=keyboard\n', 0o600)
    (root / 'old/var/lib/bluetooth').chmod(0o700)
    put(root, 'old/var/lib/fprint/private', 'MUST NOT MIGRATE')
    for user in users:
        put(root, f'old/var/lib/AccountsService/users/{user}', '[User]\nSession=ubuntu\n', 0o600)
        put(root, f'old/var/lib/AccountsService/icons/{user}', 'icon')
    if users:
        # The distribution's modes: private profiles, icons readable by GDM.
        for directory, mode in (('', 0o775), ('users', 0o700), ('icons', 0o775)):
            (root / 'old/var/lib/AccountsService' / directory).chmod(mode)

def run(root, *args, ok=True):
    r = subprocess.run(['chroot', str(root), '/bin/sh', *args], capture_output=True, text=True)
    assert (r.returncode == 0) == ok, (args, r.returncode, r.stdout, r.stderr)
    assert not (root / 'tmp').exists()
    return r.stdout

def helper(root, mode, target, ok=True):
    return run(root, '/usr/lib/liuqin/install-keep-home.sh', mode, '/run/state', target, ok=ok)

for case in ('one', 'multiple', 'none', 'missing-optional', 'uid-conflict', 'name-conflict',
             'gid-conflict', 'uid-leadingzero-conflict', 'bad-shadow', 'missing-root', 'symlink'):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        users = () if case == 'none' else ('alice', 'bob') if case == 'multiple' else ('alice',)
        fixture(root, users)
        if case == 'missing-optional':
            shutil.rmtree(root / 'old/etc/NetworkManager')
            shutil.rmtree(root / 'old/var/lib/bluetooth')
        if case == 'bad-shadow': put(root, 'old/etc/shadow', 'alice:broken\n')
        if case == 'missing-root': shutil.rmtree(root / 'old')
        if case == 'symlink':
            (root / 'old/etc/shadow').unlink()
            (root / 'old/etc/shadow').symlink_to('/new/etc/shadow')
        invalid = case in ('bad-shadow', 'missing-root', 'symlink')
        output = helper(root, 'snapshot', '/old', ok=not invalid)
        if case == 'bad-shadow': assert 'malformed shadow' in output
        if invalid:
            assert not (root / 'run/state/ready').exists()
            print('PASS:', case)
            continue
        if case == 'uid-conflict':
            databases(root, 'new', ('charlie',))
        if case == 'uid-leadingzero-conflict':
            databases(root, 'new', ('charlie',))
            p = root / 'new/etc/passwd'; p.write_text(p.read_text().replace(':1000:1000:', ':01000:2000:'))
            p = root / 'new/etc/group'; p.write_text(p.read_text().replace(':1000:', ':2000:'))
        if case == 'name-conflict':
            p = root / 'new/etc/passwd'; p.write_text(p.read_text() + 'alice:x:990:0:service:/var/lib/service:/bin/false\n')
        if case == 'gid-conflict':
            p = root / 'new/etc/group'; p.write_text(p.read_text() + 'service:x:1000:\n')
        before = {p.name: p.read_bytes() for p in (root / 'new/etc').iterdir()}
        conflict = case.endswith('conflict')
        output = helper(root, 'merge', '/new', ok=not conflict)
        if case in ('uid-conflict', 'uid-leadingzero-conflict', 'name-conflict'):
            assert 'account name/UID conflict:' in output
        if case == 'gid-conflict': assert 'group name/GID conflict:' in output
        assert before == {p.name: p.read_bytes() for p in (root / 'new/etc').iterdir()}
        if conflict:
            assert not (root / 'run/state/merged-ready').exists()
            print('PASS:', case)
            continue
        helper(root, 'restore', '/new')
        for name in ('passwd', 'shadow', 'group', 'gshadow'):
            p = root / 'new/etc' / name
            private = name in ('shadow', 'gshadow')
            assert stat.S_IMODE(p.stat().st_mode) == (0o640 if private else 0o644)
            assert (p.stat().st_uid, p.stat().st_gid) == (0, 42 if private else 0)
            for user in users: assert user + ':' in p.read_text()
            if name == 'group':
                for i, user in enumerate(users, 1000): assert f'{user}:x:{i}:root\n' in p.read_text()
            if name == 'gshadow':
                for user in users: assert f'{user}:!:root:root\n' in p.read_text()
        group = (root / 'new/etc/group').read_text()
        assert 'sudo:x:27:root' + (',' + ','.join(users) if users else '') + '\n' in group
        assert not (root / 'new/var/lib/fprint').exists()
        if case != 'missing-optional':
            p = root / 'new/etc/NetworkManager/system-connections/wifi.nmconnection'
            assert stat.S_IMODE(p.stat().st_mode) == 0o600
            assert (p.stat().st_uid, p.stat().st_gid) == (0, 0)
            bluetooth = root / 'new/var/lib/bluetooth/AA:BB/CC:DD/info'
            assert bluetooth.read_text().endswith('Name=keyboard\n')
            assert stat.S_IMODE(bluetooth.stat().st_mode) == 0o600
            assert (bluetooth.stat().st_uid, bluetooth.stat().st_gid) == (0, 0)
            assert stat.S_IMODE((root / 'new/var/lib/bluetooth').stat().st_mode) == 0o700
        for user in users:
            assert (root / f'new/var/lib/AccountsService/users/{user}').exists()
            icon = root / f'new/var/lib/AccountsService/icons/{user}'
            profile = root / f'new/var/lib/AccountsService/users/{user}'
            assert stat.S_IMODE(icon.stat().st_mode) == 0o644
            assert stat.S_IMODE(profile.stat().st_mode) == 0o600
            assert (icon.stat().st_uid, icon.stat().st_gid) == (0, 0)
            assert (profile.stat().st_uid, profile.stat().st_gid) == (0, 0)
            assert stat.S_IMODE(icon.parent.stat().st_mode) == 0o775
            assert stat.S_IMODE(profile.parent.stat().st_mode) == 0o700
            assert stat.S_IMODE(icon.parent.parent.stat().st_mode) == 0o775
        assert stat.S_IMODE((root / 'run/state').stat().st_mode) == 0o700
    print('PASS:', case)

# Execute the real preformat function, faking only mount syscalls. The account
# archive is real; the snapshot and merge are the shipped target programs.
source = (project / 'tools/lib/install-root.sh').read_text()
function = source[source.index('prepare_kept_state() {'):source.index('\nroot=$(find_part')]
for case in ('success', 'bad-shadow', 'unreadable', 'conflict', 'fresh'):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); fixture(root)
        (root / 'run/liuqin-old-root').mkdir()
        shutil.copytree(root / 'old', root / 'run/liuqin-old-root/native-root')
        if case == 'bad-shadow': put(root, 'run/liuqin-old-root/native-root/etc/shadow', 'broken\n')
        if case == 'conflict': databases(root, 'new', ('other',))
        with tarfile.open(root / 'archive.tar.gz', 'w:gz') as tar:
            for name in ('passwd', 'shadow', 'group', 'gshadow'):
                tar.add(root / 'new/etc' / name, './etc/' + name)
        put(root, 'test.sh', '''set -eu
root=/dev/fixture; archive=/archive.tar.gz
keep_home=KEEP-HOME; kept_state=false; old_mounted=false
die() { echo "$*"; exit 1; }
mount() { echo "mount $*" >>/operations; ''' + ('return 1' if case == 'unreadable' else ':') + '''; }
umount() { echo "umount $*" >>/operations; }
''' + function + '\n' + ('keep_home=\n' if case == 'fresh' else '') + '''prepare_kept_state
[ "$old_mounted" = false ]
echo "FORMATTING kept_state=$kept_state"
''')
        output = run(root, '/test.sh', ok=case != 'conflict')
        if case == 'conflict': assert 'FORMATTING' not in output
        elif case == 'success': assert 'FORMATTING kept_state=true' in output
        else: assert 'FORMATTING kept_state=false' in output
        if case in ('bad-shadow', 'unreadable'): assert 'first setup required' in output
        if case == 'fresh': assert not (root / 'operations').exists()
        else: assert 'ro,noload' in (root / 'operations').read_text()
    print('PASS: preformat', case)
print('PASS: actual BusyBox migration, preformat refusal/fallback, permissions and ownership; no /tmp')
