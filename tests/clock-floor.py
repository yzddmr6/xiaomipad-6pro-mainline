#!/usr/bin/env python3
"""Execute the floor wrapper and unmodified distro fake-hwclock in BusyBox."""
from pathlib import Path
import os,shutil,subprocess,sys,tempfile
project=Path(__file__).resolve().parents[1]
distro=Path(sys.argv[1] if len(sys.argv)>1 else '/usr/sbin/fake-hwclock')
with tempfile.TemporaryDirectory() as directory:
 root=Path(directory)
 for d in ('bin','etc','usr/sbin'): (root/d).mkdir(parents=True)
 shutil.copyfile(shutil.which('busybox'),root/'bin/busybox');(root/'bin/busybox').chmod(0o755)
 for n in ('sh','cat'): (root/'bin'/n).symlink_to('busybox')
 (root/'usr/sbin/fake-hwclock').write_text(distro.read_text().replace('date -u', '/bin/date -u'));(root/'usr/sbin/fake-hwclock').chmod(0o755)
 (root/'floor').write_text((project/'device/gnome-overlay/usr/local/libexec/liuqin-clock-floor').read_text().replace('date -u', '/bin/date -u'))
 (root/'bin/date').write_text('''#!/bin/sh
case "$*" in
'-u +%s') cat /now;;
'-u -d 2026-11-09 00:00:00 +%s') echo 1794182400;;
'-u -s 2026-11-09 00:00:00') echo restored >>/calls;;
*) exit 1;;
esac
''');(root/'bin/date').chmod(0o755)
 (root/'etc/fake-hwclock.data').write_text('2026-11-09 00:00:00\n')
 for now,count in ((100,1),(1794182400,1),(1794182500,1)):
  (root/'now').write_text(str(now))
  subprocess.run(['sudo','-n','chroot',str(root),'/bin/sh','/floor'],check=True)
  assert len((root/'calls').read_text().splitlines())==count
 assert not (root/'tmp').exists()
print('PASS: distro fake-hwclock restores only a forward time floor')
