#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Prepare the inputs build-liuqin-image.py consumes, reusing slow artifacts.

The image builder (tools/build-liuqin-image.py) takes a JSON object of
prepared-input paths.  Two of those inputs are produced by QEMU-chroot builds
that take tens of minutes yet change only when a small, hashable set of
sources changes:

  sensor-stack.tar        tools/build-liuqin-sensors-stack.sh
  gnome-control-center    tools/build-liuqin-settings.py (native power panel)

This tool fingerprints those source sets, keeps the produced artifacts under
tools/local/artifacts-cache/ keyed by fingerprint, and reuses an artifact
when its fingerprint still matches.  An artifact is admitted only after its
recorded sha256 re-verifies against the bytes on disk; anything else is
rebuilt.  Cheap inputs (audio topology, firmware tree, WLAN tuple) are
rebuilt fresh on every run so they can never go stale silently.

Usage:
  python3 tools/prepare-image-inputs.py check   # report reuse/rebuild, no work
  python3 tools/prepare-image-inputs.py run     # build what's stale, then
                                                # write out/image-inputs.local.json
Rebuilds invoke the official builders under sudo; run `sudo -v` first.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
CACHE_DIR = PROJECT / 'tools/local/artifacts-cache'
CACHE_JSON = CACHE_DIR / 'cache.json'
PREPARED = PROJECT / 'out/prepared-inputs'
OUTPUT_JSON = PROJECT / 'out/image-inputs.local.json'


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hash(spec: str) -> str:
    return subprocess.run(['git', '-C', str(PROJECT), 'rev-parse', f'HEAD:{spec}'],
                          check=True, capture_output=True, text=True).stdout.strip()


def dir_content_hash(path: Path) -> str:
    """Stable content hash of a directory tree (paths + file digests)."""
    h = hashlib.sha256()
    for f in sorted(p for p in path.rglob('*') if p.is_file() and not p.is_symlink()):
        h.update(str(f.relative_to(path)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()


# --- fingerprints -----------------------------------------------------------

def sensors_fingerprint() -> dict:
    rom = (PROJECT / 'tools/local/roms/liuqin/OS2.0.6.0.VMYCNXM/extracted/'
           'super-work/vendor-extract/etc/sensors')
    return {
        'device/sensors': tree_hash('device/sensors'),
        'device/sensors-overlay': tree_hash('device/sensors-overlay'),
        'builder': sha256_file(PROJECT / 'tools/build-liuqin-sensors-stack.sh'),
        'rom_sensors': dir_content_hash(rom),
    }


def settings_fingerprint() -> dict:
    return {
        'device/gnome-control-center': tree_hash('device/gnome-control-center'),
        'builder': sha256_file(PROJECT / 'tools/build-liuqin-settings.py'),
    }


def fingerprint_key(fp: dict) -> str:
    blob = json.dumps(fp, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


# --- cache ------------------------------------------------------------------

def load_cache() -> dict:
    if CACHE_JSON.is_file():
        return json.loads(CACHE_JSON.read_text())
    return {}


def save_cache(cache: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_JSON.write_text(json.dumps(cache, indent=2, sort_keys=True) + '\n')


def cache_lookup(cache: dict, name: str, fp: dict) -> Path | None:
    entry = cache.get(name)
    if not entry or entry.get('fingerprint') != fingerprint_key(fp):
        return None
    artifact = CACHE_DIR / entry['artifact']
    if not artifact.is_file():
        return None
    if sha256_file(artifact) != entry.get('artifact_sha256'):
        raise SystemExit(f'{name}: cached artifact failed its recorded sha256 — '
                         f'cache entry is not trustworthy, refusing to reuse')
    return artifact


def cache_store(cache: dict, name: str, fp: dict, artifact: Path) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    short = fingerprint_key(fp)[:16]
    dest = CACHE_DIR / f'{name}-{short}{artifact.suffix}'
    if not dest.exists() or sha256_file(dest) != sha256_file(artifact):
        subprocess.run(['cp', '-a', str(artifact), str(dest)], check=True)
    cache[name] = {
        'fingerprint': fingerprint_key(fp),
        'artifact': dest.name,
        'artifact_sha256': sha256_file(dest),
    }


# --- builders ----------------------------------------------------------------

def rebuild_sensors(out_dir: Path) -> Path:
    tar = out_dir / 'artifacts/sensor-stack.tar'
    env = dict(os.environ, UPDATE_APT='1', OUT_DIR=str(out_dir))
    subprocess.run(['sudo', '-n', 'sh', 'tools/build-liuqin-sensors-stack.sh'],
                   cwd=PROJECT, env=env, check=True)
    return tar


def rebuild_settings(out_dir: Path) -> Path:
    subprocess.run(['python3', 'tools/build-liuqin-settings.py', '--prepare-only'],
                   cwd=PROJECT, check=True)
    subprocess.run(['sudo', '-n', 'python3', 'tools/build-liuqin-settings.py',
                    '--jobs', str(os.cpu_count() or 8)], cwd=PROJECT, check=True)
    return out_dir / 'gnome-control-center'


def rebuild_topology(out_bin: Path) -> None:
    candidates = [PROJECT.parent / 'audioreach-topology',
                  PROJECT / 'tools/local/audioreach-topology']
    src = next((c for c in candidates if (c / 'audioreach/audioreach.m4').is_file()), None)
    if src is None:
        raise SystemExit('audioreach-topology checkout not found '
                         '(expected next to the project or under tools/local)')
    out_bin.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, AUDIOREACH_TOPOLOGY_DIR=str(src), OUTPUT=str(out_bin))
    subprocess.run(['sh', 'tools/build-liuqin-audio-topology.sh'],
                   cwd=PROJECT, env=env, check=True)


def rebuild_firmware(out_dir: Path, topology: Path, wlan_tuple: Path) -> None:
    env = dict(os.environ,
               TOPOLOGY_BIN=str(topology),
               HSP2_TUPLE_DIR=str(wlan_tuple),
               OUT_DIR=str(out_dir))
    subprocess.run(['sh', 'tools/build-liuqin-firmware-prep.sh'],
                   cwd=PROJECT, env=env, check=True)


def fingerprint_build_input() -> Path:
    """Use the public, reviewed binary/source input; it contains no OEM TA."""
    spec = importlib.util.spec_from_file_location(
        'fingerprint_deb', PROJECT / 'tools/build-liuqin-fingerprint-deb.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    candidate = Path(os.environ.get('FINGERPRINT_BUNDLE',
        PROJECT / 'tools/local/downloads/fingerprint/v0.6.0/fingerprint-build-inputs.tar.gz'))
    if not candidate.exists():
        if 'FINGERPRINT_BUNDLE' in os.environ:
            raise SystemExit(f'FINGERPRINT_BUNDLE is missing: {candidate}')
        candidate.parent.mkdir(parents=True, exist_ok=True)
        url = ('https://github.com/yzddmr6/xiaomipad-6pro-mainline/releases/download/'
               'v0.6.0/fingerprint-build-inputs.tar.gz')
        temporary = candidate.with_suffix('.download')
        urllib.request.urlretrieve(url, temporary)
        if sha256_file(temporary) != module.BUNDLE_SHA256:
            temporary.unlink()
            raise SystemExit('Fingerprint build-input download checksum mismatch')
        temporary.rename(candidate)
    if sha256_file(candidate) != module.BUNDLE_SHA256:
        raise SystemExit('Fingerprint build-input checksum mismatch')
    return candidate.resolve()


# --- main --------------------------------------------------------------------

def gather() -> dict:
    """Fingerprint inputs and decide reuse vs rebuild for the slow artifacts."""
    cache = load_cache()
    plan = {}
    fp = sensors_fingerprint()
    hit = cache_lookup(cache, 'sensor-stack', fp)
    plan['sensors'] = {'fp': fp, 'hit': hit}
    fp = settings_fingerprint()
    hit = cache_lookup(cache, 'gnome-control-center', fp)
    plan['settings'] = {'fp': fp, 'hit': hit}
    return cache, plan


def cmd_check() -> None:
    _, plan = gather()
    for name, item in plan.items():
        state = 'reuse (fingerprint unchanged, artifact verified)' if item['hit'] \
            else 'REBUILD (sources changed or no verified cache entry)'
        print(f'{name}: {state}')


def cmd_run() -> None:
    cache, plan = gather()

    sensors_work = PROJECT / 'out/prepared-inputs/sensors'
    if plan['sensors']['hit']:
        sensor_tar = plan['sensors']['hit']
    else:
        subprocess.run(['rm', '-rf', str(sensors_work)], check=True)
        sensor_tar = rebuild_sensors(sensors_work)
        cache_store(cache, 'sensor-stack', plan['sensors']['fp'], sensor_tar)

    settings_work = PROJECT / 'out/prepared-inputs/settings'
    if plan['settings']['hit']:
        settings_bin = plan['settings']['hit']
    else:
        subprocess.run(['rm', '-rf', str(settings_work)], check=True)
        settings_bin = rebuild_settings(settings_work)
        cache_store(cache, 'gnome-control-center', plan['settings']['fp'], settings_bin)
    save_cache(cache)

    topology_bin = PREPARED / 'topology/Xiaomi-Pad-6-Pro-tplg.bin'
    rebuild_topology(topology_bin)

    wlan = PROJECT / 'out/wlan-source-check'
    if not wlan.is_dir():
        raise SystemExit('WLAN tuple missing: out/wlan-source-check '
                         '(build it with tools/build-liuqin-wlan.py)')

    firmware_dir = PREPARED / 'firmware'
    subprocess.run(['rm', '-rf', str(firmware_dir)], check=True)
    rebuild_firmware(firmware_dir, topology_bin, wlan)
    firmware_manifest = firmware_dir / 'firmware.manifest'

    settings_manifest = PREPARED / 'settings-manifest.json'
    settings_manifest.parent.mkdir(parents=True, exist_ok=True)
    settings_manifest.write_text(json.dumps({
        'source': json.loads((PROJECT / 'device/gnome-control-center/source.json').read_text()),
        'binary_sha256': sha256_file(settings_bin),
    }, indent=2) + '\n')

    # Paths are absolute: build-liuqin-image.py resolves relative values
    # against the JSON file's own directory, not against the project.
    inputs = {
        'UBUNTU_DESKTOP_ROOT': str(PROJECT / 'tools/local/ubuntu-desktop-26.04-arm64/rootfs'),
        'DESKTOP_ROOTFS_MANIFEST': str(PROJECT / 'tools/local/ubuntu-desktop-26.04-arm64/rootfs.manifest'),
        'FIRMWARE_POOL': str(PROJECT / 'tools/local/firmware-liuqin'),
        'FIRMWARE_TREE': str(firmware_dir),
        'FIRMWARE_MANIFEST_SHA256': sha256_file(firmware_manifest),
        'AUDIO_TOPOLOGY': str(topology_bin),
        'WLAN_HSP2_TUPLE': str(PROJECT / 'out/wlan-source-check'),
        'STOCK_OVERLAY_DIR': str(PROJECT / 'tools/local/roms/liuqin/OS2.0.6.0.VMYCNXM/analysis/dtbo'),
        'STOCK_BASE_DIR': str(PROJECT / 'tools/local/roms/liuqin/OS2.0.6.0.VMYCNXM/analysis/vendor_boot/dtbs'),
        'SENSOR_STACK_TAR': str(sensor_tar),
        'SENSOR_STACK_SHA256': sha256_file(sensor_tar),
        'POWER_SETTINGS_BINARY': str(settings_bin),
        'POWER_SETTINGS_MANIFEST': str(settings_manifest),
        'BUSYBOX': str(PROJECT / 'tools/local/busybox-arm64/usr/bin/busybox'),
        'MKBOOTIMG_DIR': str(PROJECT / 'tools/local/aosp-mkbootimg'),
        'FINGERPRINT_BUNDLE': str(fingerprint_build_input()),
    }
    for key, value in inputs.items():
        if not key.endswith('_SHA256') and not Path(value).exists():
            raise SystemExit(f'prepared input missing on disk: {key} -> {value}')
    OUTPUT_JSON.write_text(json.dumps(inputs, indent=2) + '\n')
    reused = [n for n, i in plan.items() if i['hit']]
    print(f'wrote {OUTPUT_JSON} '
          f'(reused: {", ".join(reused) if reused else "none"})')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'run'])
    args = parser.parse_args()
    {'check': cmd_check, 'run': cmd_run}[args.action]()


if __name__ == '__main__':
    main()
