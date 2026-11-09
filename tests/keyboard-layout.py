#!/usr/bin/env python3
"""Execute both writer wrappers and their converter; optionally use the distro writer."""
from pathlib import Path
import os,shutil,subprocess,sys,tempfile
project=Path(__file__).resolve().parents[1]
overlay=project/'device/gnome-overlay'
with tempfile.TemporaryDirectory() as directory:
 root=Path(directory)
 for d in ('bin','etc/default','usr/libexec','usr/local/libexec','usr/share/X11/xkb/symbols'):
  (root/d).mkdir(parents=True,exist_ok=True)
 symbols=root/'usr/share/X11/xkb/symbols'
 for name in ('us','fr','de'): (symbols/name).touch()
 keyboard=root/'etc/default/keyboard'
 converter=root/'usr/local/libexec/liuqin-keyboard-layout'
 source=(overlay/'usr/local/libexec/liuqin-keyboard-layout').read_text()
 for prefix in ('/etc/default/keyboard','/usr/share/X11/xkb/symbols','/usr/libexec/'):
  source=source.replace(prefix,str(root)+prefix)
 converter.write_text(source);converter.chmod(0o755)
 (root/'bin/dpkg-reconfigure').write_text('#!/bin/sh\necho called >>"$CALLS"\n')
 (root/'bin/dpkg-reconfigure').chmod(0o755)
 (root/'bin/debconf-communicate').write_text('#!/bin/sh\ncat >"$DEBCONF_INPUT"\nprintf "0 value set\\n0 value set\\n"\n')
 (root/'bin/setupcon').write_text('#!/bin/sh\necho "$*" >"$SETUPCON_ARGS"\n')
 for name in ('debconf-communicate','setupcon'): (root/'bin'/name).chmod(0o755)

 if len(sys.argv)>1:
  writer=Path(sys.argv[1]).read_text().replace('KBD_FILE="/etc/default/keyboard"',f'KBD_FILE="{keyboard}"')
 else:
  writer='''#!/bin/sh
set -eu
printf 'XKBLAYOUT="%s"\\nXKBVARIANT="%s"\\nXKBOPTIONS="%s"\\n' "$1" "$2" "${3:-}" >"$KEYBOARD"
dpkg-reconfigure keyboard-configuration
'''
 writer=writer.replace('#!/bin/sh','#!'+shutil.which('busybox')+' sh',1)
 env=dict(os.environ,PATH=str(root/'bin')+':'+os.environ['PATH'],CALLS=str(root/'calls'),KEYBOARD=str(keyboard),DEBCONF_INPUT=str(root/'debconf'),SETUPCON_ARGS=str(root/'setupcon'))
 for name in ('gnome-initial-setup','gnome-control-center'):
  path=root/f'usr/libexec/{name}-set-debian-keyboard'
  path.write_text((overlay/f'usr/libexec/{name}-set-debian-keyboard').read_text().replace('/usr/local/libexec/liuqin-keyboard-layout',str(converter)))
  original=path.with_name(path.name+'.liuqin-orig');original.write_text(writer);original.chmod(0o755)
 def run(name,*args,ok=True):
  cmd=[str(converter),'--migrate'] if name=='migrate' else ['busybox','sh',str(root/f'usr/libexec/{name}-set-debian-keyboard'),*args]
  result=subprocess.run(cmd,env=env,capture_output=True,text=True)
  assert (result.returncode==0)==ok,result.stdout+result.stderr
 for name in ('gnome-initial-setup','gnome-control-center'):
  keyboard.write_text('XKBLAYOUT="fr"\nXKBVARIANT="oss"\nXKBOPTIONS=""\n')
  run(name,'default,us',',dvorak')
  assert 'XKBLAYOUT="us"' in keyboard.read_text() and 'XKBVARIANT="dvorak"' in keyboard.read_text()
  keyboard.write_text('XKBLAYOUT="fr"\nXKBVARIANT="oss"\nXKBOPTIONS=""\n')
  run(name,'default','')
  assert 'XKBLAYOUT="fr"' in keyboard.read_text() and 'XKBVARIANT="oss"' in keyboard.read_text()
  before=keyboard.read_bytes();run(name,'nonexistent','',ok=False);assert keyboard.read_bytes()==before
 keyboard.write_text('XKBLAYOUT="default,fr,us"\nXKBVARIANT=",oss,dvorak"\nXKBOPTIONS="grp:alt_shift_toggle"\n')
 calls_before=(root/'calls').read_text()
 run('migrate');assert 'XKBLAYOUT="fr,us"' in keyboard.read_text() and 'XKBVARIANT="oss,dvorak"' in keyboard.read_text()
 assert (root/'calls').read_text()==calls_before
 assert 'layoutcode fr,us' in (root/'debconf').read_text() and (root/'setupcon').read_text().strip()=='--save-only'
 saved=keyboard.stat().st_mtime_ns;run('migrate');assert keyboard.stat().st_mtime_ns==saved
 assert not (root/'tmp').exists()
 # Exercise systemd's actual condition evaluator for both capability states.
 config=(overlay/'etc/systemd/user/snap.prompting-client.daemon.service.d/90-liuqin-kernel.conf').read_text()
 conditions=[line.replace('/sys/',str(root)+'/sys/') for line in config.splitlines() if line.startswith('Condition')]
 result=subprocess.run(['systemd-analyze','condition',*conditions],capture_output=True)
 assert result.returncode!=0
 for condition in conditions:
  path=Path(condition.split('=',1)[1]);path.parent.mkdir(parents=True,exist_ok=True);path.touch()
 assert subprocess.run(['systemd-analyze','condition',*conditions],capture_output=True).returncode==0
print('PASS: both writer conversions, matching variants, current-layout fallback, one-time migration, capability conditions')
