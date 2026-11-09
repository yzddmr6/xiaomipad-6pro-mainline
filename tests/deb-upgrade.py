#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
from pathlib import Path
import hashlib,json,os,shutil,stat,subprocess
import argparse
parser=argparse.ArgumentParser(description='Test a device-support deb upgrade in an ephemeral ARM64 overlay; no root image is assembled.')
parser.add_argument('--root', type=Path, required=True, help='Immutable Ubuntu ARM64 desktop tree')
parser.add_argument('--old-deb', type=Path, required=True)
parser.add_argument('--new-deb', type=Path, required=True)
parser.add_argument('--out', type=Path, required=True, help='New scratch directory')
args=parser.parse_args()
base,old,new,work=(p.resolve() for p in (args.root,args.old_deb,args.new_deb,args.out))
assert new.is_file()
work.mkdir()
for name in ('upper','work','root'): (work/name).mkdir()
os.unshare(os.CLONE_NEWNS)
def run(*args,**kwargs):return subprocess.run(args,check=True,**kwargs)
run('mount','--make-rprivate','/')
root=work/'root'
run('mount','-t','overlay','overlay','-o',f'lowerdir={base},upperdir={work/"upper"},workdir={work/"work"}',str(root))
mounted=[]
try:
 for name in ('proc',):
  run('mount','-t',name,'-o','ro,nosuid,nodev,noexec',name,str(root/name));mounted.append(root/name)
 for name,major,minor in [('null',1,3),('zero',1,5),('random',1,8),('urandom',1,9)]:
  path=root/'dev'/name
  if not path.exists():os.mknod(path,stat.S_IFCHR|0o666,os.makedev(major,minor))
  path.chmod(0o666)
 config=root/'etc/initramfs-tools/update-initramfs.conf';config.parent.mkdir(parents=True,exist_ok=True);config.write_text('update_initramfs=no\n')
 policy=root/'usr/sbin/policy-rc.d';policy.write_text('#!/bin/sh\nexit 101\n');policy.chmod(0o755)
 (root/'run/deb-test').mkdir(parents=True)
 shutil.copyfile(old,root/'run/deb-test/old.deb');shutil.copyfile(new,root/'run/deb-test/new.deb')
 def guest(script):
  return run('chroot',str(root),'/bin/sh','-eux',input=script,text=True,env=dict(os.environ,QEMU_CPU='max,pauth-impdef=on'))
 guest('dpkg -i /run/deb-test/old.deb\n')
 # A real package upgrade, including the obsolete conffile and broken keyboard.
 drop=root/'etc/systemd/system/liuqin-mipps-auth.service.d/10-allow-unverified-adapter.conf'
 assert drop.is_file()
 (root/'etc/default/keyboard').write_text('XKBMODEL="pc105"\nXKBLAYOUT="default,us"\nXKBVARIANT=","\nXKBOPTIONS=""\n')
 guest('dpkg -i /run/deb-test/new.deb\ndpkg --audit\n')
 assert not drop.exists()
 assert not (root/'usr/local/sbin/liuqin-rescue').exists()
 assert not (root/'usr/local/sbin/liuqin-gnome-usb-rescue').exists()
 assert not (root/'usr/local/bin/liuqin-shell').exists()
 assert (root/'etc/systemd/system/multi-user.target.wants/liuqin-mipps-battery-auth.service').is_symlink()
 assert (root/'etc/systemd/system/sysinit.target.wants/liuqin-physical-platform.service').is_symlink()
 assert not (root/'etc/systemd/system/usb-gadget.target.wants/liuqin-devmode.service').exists()
 assert not (root/'etc/systemd/system/multi-user.target.wants/liuqin-gnome-usb-rescue.service').exists()
 assert 'XKBLAYOUT="us"' in (root/'etc/default/keyboard').read_text()
 assert 'CHASSIS=tablet' in (root/'etc/machine-info').read_text()
 guest('dpkg-query -W -f=\'${Package} ${Version} ${Status}\\n\' liuqin-device-support\n'
       '/usr/libexec/gnome-initial-setup-set-debian-keyboard default,fr ,oss\n'
       '/usr/libexec/gnome-control-center-set-debian-keyboard default,us ,dvorak\n'
       'test -x /usr/libexec/gnome-initial-setup-set-debian-keyboard.liuqin-orig\n'
       'test -x /usr/libexec/gnome-control-center-set-debian-keyboard.liuqin-orig\n')
 assert 'XKBLAYOUT="us"' in (root/'etc/default/keyboard').read_text()
 assert 'XKBVARIANT="dvorak"' in (root/'etc/default/keyboard').read_text()
 extension=root/'usr/share/gnome-shell/extensions/liuqin-osk-guard@io.github.liuqin'
 assert json.loads((extension/'metadata.json').read_text())['shell-version'] == ['50']
 # dpkg's real glib schema trigger must make the shipped default effective.
 guest("GSETTINGS_BACKEND=memory gsettings get org.gnome.shell enabled-extensions | grep -F liuqin-osk-guard@io.github.liuqin\n")
 guest('/usr/bin/gnome-control-center --version\n')
 guest('systemd-analyze verify liuqin-physical-platform.service liuqin-mipps-auth.service liuqin-mipps-battery-auth.service\n')
 print('PASS: actual ARM64 dpkg upgrade, default MiPPS policy, units, keyboard migration and diverted writers',flush=True)
finally:
 for path in reversed(mounted):run('umount',str(path))
 run('umount',str(root))
