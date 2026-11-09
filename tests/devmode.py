#!/usr/bin/env python3
"""Run device developer-mode scripts under BusyBox in a root without /tmp."""
from pathlib import Path
import importlib.util, json, os, shutil, subprocess, tempfile
project = Path(__file__).resolve().parents[1]
overlay = project / 'device/gnome-overlay'
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    for d in ('bin','etc','dev','proc','run','usr/sbin','usr/libexec/liuqin','sys/class/udc/controller','sys/kernel/config/usb_gadget'):
        (root/d).mkdir(parents=True,exist_ok=True)
    (root/'dev/null').touch()
    shutil.copyfile(shutil.which('busybox'),root/'bin/busybox'); (root/'bin/busybox').chmod(0o755)
    for name in ('sh','cat','sed','tr','ln','cp','rm','chmod','mkdir','rmdir'):
        (root/'bin'/name).symlink_to('busybox')
    for rel in ('usr/sbin/liuqin-devmode','usr/libexec/liuqin/devmode-gadget','etc/liuqin-network'):
        shutil.copyfile(overlay/rel,root/rel); (root/rel).chmod(0o755)
    (root/'proc/cmdline').write_text('androidboot.serialno=fixture\n')
    def script(rel, body):
        p=root/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('#!/bin/sh\n'+body+'\n');p.chmod(0o755)
    script('bin/systemctl','echo "$*" >>/systemctl; exit 0')
    script('bin/ssh-keygen','echo "$*" >>/keygen; exit 0')
    script('usr/sbin/sshd','echo "$*" >>/sshd; [ ! -e /bad-sshd ]')
    # Model configfs directory creation/removal, not the policy being tested.
    script('bin/config-mkdir','/bin/busybox mkdir -p "$@"\n[ ! -d /sys/kernel/config/usb_gadget/liuqin-devmode ] || : >/sys/kernel/config/usb_gadget/liuqin-devmode/UDC')
    script('bin/config-rmdir','/bin/busybox rm -rf "$@"')
    p=root/'usr/libexec/liuqin/devmode-gadget';p.write_text(p.read_text().replace('mkdir "$g"','/bin/config-mkdir "$g"').replace('rmdir "$d"','/bin/config-rmdir "$d"'))
    def run(path, *args, ok=True):
        r=subprocess.run(['sudo','-n','chroot',str(root),'/bin/sh',path,*args],capture_output=True,text=True)
        assert (r.returncode==0)==ok, (path,args,r.stdout,r.stderr)
        assert not (root/'tmp').exists()
        return r.stdout
    run('/usr/sbin/liuqin-devmode','check',ok=False)
    keydir=root/'etc/liuqin/devmode';keydir.mkdir(parents=True)
    (keydir/'authorized_keys').write_text('ssh-ed25519 fixture\n')
    run('/usr/sbin/liuqin-devmode','check')
    (root/'bad-sshd').touch();run('/usr/sbin/liuqin-devmode','on',ok=False)
    assert not (root/'systemctl').exists()
    (root/'bad-sshd').unlink();run('/usr/sbin/liuqin-devmode','on')
    assert 'enable --now liuqin-devmode.service' in (root/'systemctl').read_text()
    run('/usr/sbin/liuqin-devmode','off')
    run('/usr/libexec/liuqin/devmode-gadget','start')
    gadget=root/'sys/kernel/config/usb_gadget/liuqin-devmode'
    assert (gadget/'UDC').read_text().strip()=='controller'
    assert (gadget/'idProduct').read_text().strip()=='0x1041'
    assert (gadget/'functions/ncm.usb0/dev_addr').read_text().strip()=='02:00:00:66:00:02'
    run('/usr/libexec/liuqin/devmode-gadget','start')
    run('/usr/libexec/liuqin/devmode-gadget','stop')
    assert not gadget.exists()
    other=root/'sys/kernel/config/usb_gadget/other';other.mkdir();(other/'UDC').write_text('controller\n')
    run('/usr/libexec/liuqin/devmode-gadget','start',ok=False)
    other.joinpath('UDC').write_text('')
    (root/'sys/class/udc/second').mkdir();run('/usr/libexec/liuqin/devmode-gadget','start',ok=False)
    # Execute installer migration and provisioning, substituting block I/O only.
    p=root/'install-devmode';p.write_text((project/'tools/lib/install-devmode.sh').read_text().replace('BB=/bin/busybox','BB=/bin/install-bb'))
    script('bin/install-bb','case "$1" in blockdev) echo 1;; mount) echo "$*" >>/mounts;; umount) echo "$*" >>/mounts;; *) exec /bin/busybox "$@";; esac')
    old=root/'run/liuqin-devmode/old/native-root/etc';old.mkdir(parents=True)
    (old/'liuqin-rescue-enabled').touch()
    assert run('/install-devmode','inspect','/dev/sda36').strip()=='enabled'
    assert 'ro,noload' in (root/'mounts').read_text() and 'umount' in (root/'mounts').read_text()
    new=root/'new';(new/'etc').mkdir(parents=True);(new/'etc/liuqin-native-root').touch()
    (new/'usr/lib/systemd/system').mkdir(parents=True);(new/'usr/lib/systemd/system/liuqin-devmode.service').touch()
    run('/install-devmode','install','/new',ok=False)
    (root/'run/liuqin-devmode/authorized_keys').write_text('ssh-ed25519 fixture\n')
    run('/install-devmode','install','/new')
    assert (new/'etc/liuqin/devmode/authorized_keys').stat().st_mode & 0o777 == 0o600
    assert (new/'etc/systemd/system/usb-gadget.target.wants/liuqin-devmode.service').is_symlink()
    # Execute the actual guarded stage-1 USB block and final failure path.
    init=(project/'initramfs/init').read_text()
    start=init.index('if [ "$storage_mode" = readonly ] && [ -f "$storage_installer_marker" ]; then\ngadget=')
    end=init.index('fi # dedicated RAM installer USB channel',start)+len('fi # dedicated RAM installer USB channel')
    (root/'etc/liuqin-installer').write_text('liuqin\n')
    for mode,marker in [('persistent',True),('readonly',False)]:
        if not marker: (root/'etc/liuqin-installer').unlink()
        header=f'storage_mode={mode}\nstorage_installer_marker=/etc/liuqin-installer\nufs_safe=true\n'
        (root/'channel').write_text(header+init[start:end])
        run('/channel')
        assert not (root/'sys/kernel/config/usb_gadget/liuqin').exists()
        tail=init[init.index('log "pstore is available'):]
        recovery=init[init.index('boot_failure_to_fastboot() {'):init.index('# The mutation test enters')]
        script('bin/recovery-bb','echo "$*" >>/recovery-log')
        script('bin/liuqin-reboot','echo "reboot $*" >>/recovery-log; exit 1')
        (root/'failure').write_text(header+'BB=/bin/recovery-bb\nlog() { echo "$*" >>/recovery-log; }\nsleep() { exit 99; }\nstorage_abort() { echo sealed >>/recovery-log; }\nsetsid() { echo opened >/shell-opened; exit 98; }\n'+recovery+tail)
        run('/failure',ok=False)
        events=(root/'recovery-log').read_text()
        assert events.index('sealed') < events.index('sleep 30') < events.index('reboot bootloader')
        assert 'reboot normal' not in events
        assert not (root/'shell-opened').exists()
    # Actual OpenSSH parses the shipped policy: no password or keyboard login.
    config=overlay/'usr/lib/liuqin/devmode/sshd_config'
    result=subprocess.check_output(['sudo','-n','/usr/sbin/sshd','-T','-f',str(config)],text=True)
    for line in ('passwordauthentication no','kbdinteractiveauthentication no','authenticationmethods publickey','allowusers root'):
        assert line in result
    # Files created by chroot are owned by root; relinquish them for tempfile cleanup.
    subprocess.run(['sudo','-n','chown','-R',f'{os.getuid()}:{os.getgid()}',str(root)],check=True)
print('PASS: explicit enable, key prerequisite, sshd policy, NCM lifecycle and old-marker migration')

# Host selector and SSH argv are exercised without accessing host interfaces.
spec=importlib.util.spec_from_file_location('devmode_host',project/'tools/liuqin-devmode-ssh.py')
host=importlib.util.module_from_spec(spec);spec.loader.exec_module(host)
network=host.load_network()
assert network['device_ip']=='192.168.66.2' and host.USB_ID==('1d6b','1041')
with tempfile.TemporaryDirectory() as directory:
    root=Path(directory); usb=root/'usb';usb.mkdir()
    for name,value in zip(('idVendor','idProduct','serial'),('1d6b','1041','liuqin-test')):
        (usb/name).write_text(value)
    interface=usb/'interface';interface.mkdir()
    net=root/'net';net.mkdir();device=net/'enxfixture';device.mkdir()
    (device/'address').write_text(network['host_mac'])
    (device/'device').symlink_to(interface)
    assert host.find_interfaces(network, 'test',net)==[('enxfixture','liuqin-test')]
    assert host.find_interfaces(network, 'other',net)==[]
    called=[]
    def route(*args,**kwargs):
        return subprocess.CompletedProcess(args,0,json.dumps([{'dev':'enxfixture','prefsrc':network['host_ip']}]))
    host.main(['--wait','0','root','true'],sys_net=net,run=route,execvp=lambda *a:called.append(a))
    assert called[0][0]=='ssh' and called[0][1][-2:]==['root@192.168.66.2','true']
print('PASS: host identity, serial selection, route and SSH command')
