#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The boot image's pin list must pass its own ownership policy.

The policy runs when the boot image is assembled; checking it here catches a
new pin under a distribution-owned prefix before an image build does.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('contract', ROOT / 'tools/lib/build-root-contract.py')
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)

lists = {name: value for name, value in vars(contract).items()
         if name.endswith('_PINNED_PATHS') and isinstance(value, tuple)}
assert lists, 'no pin list found'
for name, paths in lists.items():
    contract.check_pin_list(paths)
    print(f'PASS: {name} ({len(paths)} paths) satisfies the ownership policy')
