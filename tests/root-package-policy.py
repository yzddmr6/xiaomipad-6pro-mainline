#!/usr/bin/env python3
import importlib.util
from pathlib import Path
project=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('policy',project/'tools/lib/root-package-policy.py')
policy=importlib.util.module_from_spec(spec);spec.loader.exec_module(policy)
status={p:'install ok installed' for p in ('liuqin-kernel','ubuntu-desktop','unattended-upgrades','linux-generic-hwe-26.04','linux-image-7.0.0-14-generic','linux-headers-7.0.0-14','linux-main-modules-zfs-7.0.0-14-generic','zfs-dkms','dracut','flash-kernel','initramfs-tools-core','kdump-tools')}
status['linux-image-removed'] = 'deinstall ok not-installed'
selected=sorted(p for p in status if policy.obsolete(p) and not status[p].endswith('not-installed'))
plan='\n'.join('Purg '+p+' [version]' for p in selected)
assert policy.removal_plan(status,plan)==selected
for bad in (plan+'\nRemv ubuntu-desktop [version]', plan+'\nInst dracut [version]', plan.replace('Purg kdump-tools [version]','')):
 try: policy.removal_plan(status,bad)
 except RuntimeError: pass
 else: raise AssertionError('unsafe removal plan accepted')
try: policy.verify(status)
except RuntimeError: pass
else: raise AssertionError('extra kernel accepted')
for p in selected: del status[p]
del status['linux-image-removed']
policy.verify(status)
assert set(status)=={'liuqin-kernel','ubuntu-desktop','unattended-upgrades'}
print('PASS: scoped purge, dependent-package refusal, sole-kernel assertion, updates retained')
