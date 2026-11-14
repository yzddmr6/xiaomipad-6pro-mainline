#!/usr/bin/env python3
"""Package the ARM64 interpreter, standard library and actual PMIC ADC modules."""
import argparse
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess


def package(root, kernel, out):
    helper = Path(__file__).with_name('build-root-fsck.py')
    spec = importlib.util.spec_from_file_location('elf_inputs', helper)
    fsck = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fsck)
    root = root.resolve(strict=True)
    interpreter = (root/'usr/bin/python3').resolve(strict=True)
    version = interpreter.name.removeprefix('python')
    if not re.fullmatch(r'3\.\d+', version) or not interpreter.is_relative_to(root):
        raise ValueError('invalid interpreter in the pinned target root')
    std = root/'usr/lib'/interpreter.name
    # Only Python files from the interpreter's stdlib, never site packages or
    # host installations. Embedded pure modules include lazy error/codec paths.
    for f in sorted(std.rglob('*.py')):
        if f.name == 'sitecustomize.py':
            continue
        if any(part in ('site-packages', 'dist-packages', '__pycache__', 'test', 'tests') for part in f.relative_to(std).parts):
            continue
        if not f.resolve(strict=True).is_relative_to(root):
            raise ValueError('stdlib input outside pinned root')
        dest = out/f.relative_to(root)
        dest.parent.mkdir(exist_ok=True, parents=True)
        shutil.copyfile(f, dest)
    entries = [(str(interpreter.relative_to(root)), interpreter)]
    for name in ('_hashlib', '_hmac', '_sha2', '_random', '_posixsubprocess', '_opcode'):
        for f in (std/'lib-dynload').glob(name+'.*.so'):
            entries.append((str(f.relative_to(root)),f))
    for rel, source in fsck.elf_closure(root, entries).items():
        target = out/rel
        target.parent.mkdir(exist_ok=True, parents=True)
        if target.exists() and target.read_bytes() != source.read_bytes():
            raise ValueError('inconsistent ELF input '+rel)
        if not target.exists():
            shutil.copyfile(source, target)
            target.chmod(0o755)
    (out/'usr/bin/python3').symlink_to(interpreter.name)
    release = (kernel/'include/config/kernel.release').read_text().strip()
    for name in ('qcom-vadc-common', 'qcom-spmi-adc5'):
        config = {'qcom-vadc-common':'QCOM_VADC_COMMON','qcom-spmi-adc5':'QCOM_SPMI_ADC5'}[name]
        text = (kernel/'.config').read_text().splitlines()
        if 'CONFIG_'+config+'=y' in text:
            continue
        if 'CONFIG_'+config+'=m' not in text:
            raise ValueError('missing PMIC ADC configuration '+config)
        mod = kernel/'drivers/iio/adc'/(name+'.ko')
        vermagic = subprocess.check_output(['modinfo','-F','vermagic',mod], text=True).split()[0]
        if vermagic != release:
            raise ValueError('ADC module release mismatch')
        dest=out/'lib/modules'/release/(name+'.ko')
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(mod, dest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'kernel', 'out'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    package(args.root, args.kernel, args.out)
