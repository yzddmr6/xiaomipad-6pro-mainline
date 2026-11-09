#!/usr/bin/env python3
"""Execute preflight and the real factory-data provisioner in a RAM-shaped root."""
from pathlib import Path
import os,shutil,subprocess,tempfile
project=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
 root=Path(directory)
 for d in ('bin','dev/disk/by-partlabel','etc','run','proc/sys/kernel/random','sys/class/power_supply/battery','usr/lib/liuqin'):
  (root/d).mkdir(parents=True,exist_ok=True)
 shutil.copyfile(shutil.which('busybox'),root/'bin/busybox');(root/'bin/busybox').chmod(0o755)
 for name in subprocess.check_output(['busybox','--list'],text=True).split():
  if not (root/'bin'/name).exists(): (root/'bin'/name).symlink_to('busybox')
 (root/'dev/null').touch();(root/'dev/disk/by-partlabel/persist').touch()
 (root/'etc/liuqin-installer').write_text('liuqin\n')
 (root/'proc/sys/kernel/random/boot_id').write_text('fixture\n')
 (root/'proc/meminfo').write_text('MemAvailable: 4000000 kB\n')
 battery=root/'sys/class/power_supply/battery';(battery/'type').write_text('Battery\n');(battery/'capacity').write_text('90\n')
 persist=root/'run/liuqin-preflight/persist'
 for d in ('wlan','bluetooth','audio','sensors/registry/registry'): (persist/d).mkdir(parents=True)
 (persist/'wlan/wlan_mac.bin').write_text('wlan0=020000000001\n')
 (persist/'bluetooth/.bt_nv.bin').write_bytes(bytes(range(6)))
 (persist/'audio/crus_calr.bin').write_bytes(bytes(range(16)))
 for n in range(101): (persist/'sensors/registry/registry'/str(n)).write_text('{}')
 (root/'usr/lib/liuqin/provision.sh').write_text((project/'tools/provision-liuqin-from-persist.sh').read_text())
 (root/'preflight').write_text((project/'tools/lib/install-preflight.sh').read_text().replace('[ -b "$persist" ]','[ -e "$persist" ]').replace('/bin/busybox','/bin/test-bb'))
 (root/'bin/test-bb').write_text('''#!/bin/sh
case $1 in
blockdev) echo 1;;
mount|umount) echo "$*" >>/operations;;
*) exec /bin/busybox "$@";;
esac
''');(root/'bin/test-bb').chmod(0o755)
 def run(ok):
  r=subprocess.run(['chroot',str(root),'/bin/sh','/preflight','fixture','1000000'],capture_output=True,text=True)
  assert (r.returncode==0)==ok,r.stdout+r.stderr
  assert not (root/'tmp').exists()
 run(True)
 assert 'ro,noload' in (root/'operations').read_text()
 for kind in ('battery','memory','factory'):
  (root/'operations').unlink()
  if kind=='battery': (battery/'capacity').write_text('29')
  if kind=='memory': (root/'proc/meminfo').write_text('MemAvailable: 100 kB\n')
  if kind=='factory': (persist/'audio/crus_calr.bin').write_bytes(b'bad')
  run(False)
  if kind!='factory': assert not (root/'operations').exists()
  else: assert 'umount' in (root/'operations').read_text()
  (battery/'capacity').write_text('90');(root/'proc/meminfo').write_text('MemAvailable: 4000000 kB\n')
  if not (root/'operations').exists(): (root/'operations').touch()
print('PASS: battery, memory and real factory-data parsing before persistent writes; read-only cleanup')
