#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Real offline APT full-set upgrade/rollback in a disposable ARM64 overlay."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile


def run(*args, **kwargs):
    return subprocess.run(list(map(str,args)),check=True,**kwargs)


def verify_payloads(root, directory, rows):
    checked=0
    for row in rows:
        proc=subprocess.Popen(['dpkg-deb','--fsys-tarfile',str(directory/row['file'])],stdout=subprocess.PIPE)
        with tarfile.open(fileobj=proc.stdout,mode='r|') as archive:
            for item in archive:
                relative=item.name.removeprefix('./')
                # Configuration files can intentionally retain local changes.
                if relative.startswith('etc/') or not item.isfile():continue
                if relative.startswith('/') or '..' in Path(relative).parts:raise ValueError('unsafe package path')
                with archive.extractfile(item) as data: expected=hashlib.file_digest(data,'sha256').hexdigest()
                with (root/relative).open('rb') as data: actual=hashlib.file_digest(data,'sha256').hexdigest()
                assert actual==expected,(row['Package'],relative)
                checked+=1
        proc.stdout.close()
        if proc.wait()!=0:raise RuntimeError('dpkg payload read failed')
    return checked


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for option in ('root','old-debs','new-debs','out'):p.add_argument('--'+option,type=Path,required=True)
    p.add_argument('--old-contract',type=Path)
    p.add_argument('--new-contract',type=Path)
    a=p.parse_args()
    if os.geteuid()!=0:p.error('run under sudo in heavy.slice')
    base,old,new,work=[x.resolve() for x in (a.root,a.old_debs,a.new_debs,a.out)]
    if base==Path('/') or work.exists():p.error('use an immutable ARM64 root and a fresh output')
    if not (base/'var/lib/dpkg/status').is_file():p.error('base package status missing')
    sets={}
    for name,directory in [('old',old),('new',new)]:
        rows=[]
        for deb in sorted(directory.glob('liuqin-*.deb')):
            data=subprocess.check_output(['dpkg-deb','-f',str(deb),'Package','Version','Architecture'],text=True)
            fields=dict(l.split(': ',1) for l in data.strip().splitlines())
            if fields['Architecture'] not in ('arm64','all'):p.error('expected ARM64 project package')
            with deb.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
            rows.append(dict(fields,file=deb.name,sha256=digest))
        sets[name]=rows
    names={r['Package'] for r in sets['old']}
    if not names or names!={r['Package'] for r in sets['new']}:
        p.error('whole-set lifecycle needs identical old/new package names')
    work.mkdir(mode=0o700)
    for name in ('upper','work','root'):(work/name).mkdir()
    (work/'inputs.json').write_text(json.dumps(sets,indent=2)+'\n')
    # No network and no host block devices are available to maintainer scripts.
    os.unshare(os.CLONE_NEWNS|os.CLONE_NEWNET)
    run('mount','--make-rprivate','/')
    root=work/'root'; mounts=[]
    def mount(*args,target):
        run('mount',*args,target);mounts.append(target)
    mount('-t','overlay','overlay','-o',f'lowerdir={base},upperdir={work/"upper"},workdir={work/"work"}',target=root)
    try:
        for d in ('proc','dev','run'):(root/d).mkdir(exist_ok=True)
        mount('-t','proc','-o','ro,nosuid,nodev,noexec','proc',target=root/'proc')
        mount('-t','tmpfs','-o','nosuid,mode=755','tmpfs',target=root/'dev')
        mount('-t','tmpfs','-o','nosuid,nodev,mode=755','tmpfs',target=root/'run')
        for name,major,minor in [('null',1,3),('zero',1,5),('random',1,8),('urandom',1,9)]:
            os.mknod(root/'dev'/name,stat.S_IFCHR|0o666,os.makedev(major,minor));(root/'dev'/name).chmod(0o666)
        (root/'dev/fd').symlink_to('/proc/self/fd')
        for name,directory in [('old',old),('new',new)]:
            dest=root/'run'/name;dest.mkdir()
            mount('--bind',directory,target=dest)
            run('mount','-o','remount,bind,ro',dest)
        policy=root/'usr/sbin/policy-rc.d';policy.write_text('#!/bin/sh\nexit 101\n');policy.chmod(0o755)
        config=root/'etc/initramfs-tools/update-initramfs.conf'
        config.parent.mkdir(parents=True,exist_ok=True);config.write_text('update_initramfs=no\n')
        sentinel=root/'home/.liuqin-apt-fixture';sentinel.write_text('preserve-data\n');sentinel.chmod(0o600)
        env=dict(os.environ,QEMU_CPU='max,pauth-impdef=on',DEBIAN_FRONTEND='noninteractive',LC_ALL='C')
        def guest(*args):
            return subprocess.check_output(['chroot',str(root),*args],text=True,env=env,stderr=subprocess.STDOUT,timeout=1800)
        def packages():
            lines=guest('dpkg-query','-W','-f=${Package}\t${Version}\t${Status}\n').splitlines()
            return {l.split('\t')[0]:l.split('\t')[1:] for l in lines}
        before=packages()
        for row in sets['old']:
            assert before.get(row['Package'])==[row['Version'],'install ok installed'],(row,before.get(row['Package']))
        diverts=guest('dpkg-divert','--list')
        (root/'run/empty-sources.list').write_text('')
        (root/'run/empty-sources.d').mkdir()
        report={'base':str(base),'network':'isolated empty namespace','block_devices':False,'phases':[]}
        for phase,kind,downgrade in [('upgrade','new',False),('rollback','old',True)]:
            argv=['apt-get','-o','Dir::Etc::sourcelist=/run/empty-sources.list','-o','Dir::Etc::sourceparts=/run/empty-sources.d',
                  '-o','Dpkg::Options::=--force-confold','-o','Dpkg::Use-Pty=0','--no-remove','--assume-yes']
            if downgrade:argv.append('--allow-downgrades')
            # Local .deb acquisition still needs APT's file-copy method. Network
            # isolation and empty source lists enforce offline operation.
            argv+=['install']+['/run/'+kind+'/'+r['file'] for r in sets[kind]]
            try:
                transcript=guest(*argv)
            except subprocess.CalledProcessError as error:
                (work/(phase+'.log')).write_text(error.output)
                raise
            (work/(phase+'.log')).write_text(transcript)
            expected=dict(before)
            for row in sets[kind]:expected[row['Package']]=[row['Version'],'install ok installed']
            after=packages();assert after==expected,{'unexpected_packages':{k:after.get(k) for k in after.keys()|expected.keys() if after.get(k)!=expected.get(k)}}
            audit=guest('dpkg','--audit');assert not audit.strip(),audit
            assert guest('dpkg-divert','--list')==diverts,'diversion set changed'
            assert sentinel.read_text()=='preserve-data\n' and stat.S_IMODE(sentinel.stat().st_mode)==0o600
            # Check project-owned executable payloads using dpkg's package hashes.
            verification=guest('dpkg','--verify',*sorted(names))
            (work/(phase+'-verify.log')).write_text(verification)
            differences=[l for l in verification.splitlines() if ' c ' not in l]
            assert not differences,differences
            payload_files=verify_payloads(root, new if kind=='new' else old, sets[kind])
            notifier=root/'usr/lib/systemd/user/mainline-release-notifier.timer'
            enabled=root/'etc/systemd/user/timers.target.wants/mainline-release-notifier.timer'
            if notifier.exists():
                assert enabled.is_symlink(),'shipped notifier must be enabled'
                assert guest('test','/etc/systemd/user/timers.target.wants/mainline-release-notifier.timer','-ef','/usr/lib/systemd/user/mainline-release-notifier.timer')==''
                release=root/'usr/share/mainline-release/version'
                assert release.read_text().strip()==next(r['Version'] for r in sets[kind] if r['Package']=='liuqin-device-support')
            else:
                assert not enabled.is_symlink(),'rollback left a dangling notifier timer'
            for path,mode in [('etc',0o755),('usr',0o755),('var',0o755),('tmp',0o1777)]:
                state=(root/path).stat()
                assert (state.st_uid,state.st_gid,stat.S_IMODE(state.st_mode))==(0,0,mode),(phase,path,state)

            contract=a.new_contract if kind=='new' else a.old_contract
            pins=0
            if contract:
                lines=contract.read_text().splitlines()
                assert lines.pop(0)=='LIUQIN_NATIVE_ROOT_CONTRACT_V1'
                for line in lines:
                    digest,name=line.split('  ',1)
                    assert name.startswith('/') and '..' not in Path(name).parts
                    target=root/name[1:]
                    assert target.is_file() and not target.is_symlink() and target.stat().st_uid==target.stat().st_gid==0,name
                    with target.open('rb') as data:assert hashlib.file_digest(data,'sha256').hexdigest()==digest,(phase,name)
                    pins+=1
            report['phases'].append({'phase':phase,'versions':{r['Package']:r['Version'] for r in sets[kind]},
                'boot_contract_pins_verified':pins,'dpkg_audit_empty':True,'unrelated_packages_unchanged':True,'data_preserved':True,
                'non_conffile_verification_clean':True,'payload_files_sha256_checked':payload_files})
        assert packages()==before
        report['status']='OFFLINE_APT_PASS'
        (work/'result.json').write_text(json.dumps(report,indent=2)+'\n')
        print('OFFLINE_APT_PASS: full candidate upgrade and whole-set rollback; no boot or device proof')
    finally:
        for target in reversed(mounts):run('umount',target)

if __name__=='__main__':main()
