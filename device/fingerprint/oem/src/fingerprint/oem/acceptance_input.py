#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""One-use, boot-local authenticated input in the root kernel keyring.

Only the normal PAM frontend may prepare it. No password, derived input or
HAT is written to disk, returned as text or held by a waiting userspace daemon.
The key expires in 18 hours, is revoked on consumption, and disappears on boot.
"""
import ctypes
import fcntl
import json
import os
from pathlib import Path
import resource
import time

ROOT = Path("/run/liuqin-fpc-oem-runtime")
LIFETIME = 18 * 3600

def context(username):
    if os.geteuid() != 0 or not username or "/" in username or username in (".", ".."):
        raise ValueError("root account scope required")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    libc = ctypes.CDLL(None, use_errno=True)
    keys = ctypes.CDLL("libkeyutils.so.1", use_errno=True)
    keys.keyctl_get_keyring_ID.argtypes = (ctypes.c_int, ctypes.c_int)
    keys.keyctl_get_keyring_ID.restype = ctypes.c_int
    keys.keyctl_link.argtypes = (ctypes.c_int, ctypes.c_int)
    keys.add_key.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int)
    keys.add_key.restype = ctypes.c_int
    keys.keyctl_setperm.argtypes = (ctypes.c_int, ctypes.c_uint)
    keys.keyctl_set_timeout.argtypes = (ctypes.c_int, ctypes.c_uint)
    keys.keyctl_revoke.argtypes = (ctypes.c_int,)
    keys.keyctl_unlink.argtypes = (ctypes.c_int, ctypes.c_int)
    keys.keyctl_search.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int)
    keys.keyctl_search.restype = ctypes.c_int
    keys.keyctl_read.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t)
    keys.keyctl_read.restype = ctypes.c_long
    libc.read.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t)
    libc.read.restype = ctypes.c_ssize_t
    libc.write.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t)
    libc.write.restype = ctypes.c_ssize_t
    libc.mlock.argtypes = (ctypes.c_void_p, ctypes.c_size_t)
    libc.munlock.argtypes = (ctypes.c_void_p, ctypes.c_size_t)
    libc.memcmp.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t)
    libc.memcmp.restype = ctypes.c_int
    # The stable kernel supports ordinary UID keyrings. Persistent-keyring
    # support is optional and returned EOPNOTSUPP on the actual tablet.
    ring = keys.keyctl_get_keyring_ID(-4, 1)  # Root UID keyring, boot-local.
    if ring < 0:
        raise OSError("root kernel keyring unavailable")
    process_ring = keys.keyctl_get_keyring_ID(-2, 1)
    if process_ring < 0 or keys.keyctl_link(ring, process_ring):
        raise OSError("root kernel keyring possession unavailable")
    ROOT.mkdir(mode=0o700, exist_ok=True)
    info = ROOT.lstat()
    if info.st_uid != 0 or info.st_mode & 0o077 or not ROOT.is_dir() or ROOT.is_symlink():
        raise ValueError("private runtime directory required")
    return libc, keys, ring, ROOT / ("acceptance-" + username + ".json")

def prepare(username, native_uid, descriptor):
    libc, keys, ring, metadata = context(username)
    if metadata.exists():
        raise ValueError("a prepared acceptance is already present")
    buffer = (ctypes.c_ubyte * 32)()
    check = (ctypes.c_ubyte * 32)()
    if libc.mlock(buffer, 32) or libc.mlock(check, 32):
        raise OSError("locked input memory unavailable")
    serial = -1
    phase = "input_fd"
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        if libc.read(descriptor, buffer, 32) != 32:
            raise ValueError("incomplete authenticated input")
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        description = ("liuqin-fpc-acceptance:" + boot + ":" + username).encode("ascii")
        stale = keys.keyctl_search(ring, b"user", description, 0)
        if stale >= 0:
            # An interrupted preparation can leave a key without metadata.
            # The new input was freshly authenticated and verified above.
            if keys.keyctl_revoke(stale) or keys.keyctl_unlink(stale, ring):
                raise OSError("previous unfinished preparation could not be revoked")
        phase = "key_add"
        serial = keys.add_key(b"user", description, buffer, 32, ring)
        if serial < 0:
            raise OSError("private key creation failed")
        phase = "key_permission"
        if keys.keyctl_setperm(serial, 0x3F3F0000):
            raise OSError("private key permissions failed")
        phase = "key_timeout"
        if keys.keyctl_set_timeout(serial, LIFETIME):
            raise OSError("private expiring key creation failed")
        phase = "key_roundtrip"
        if keys.keyctl_read(serial, check, 32) != 32 or libc.memcmp(buffer, check, 32):
            raise OSError("private keyring input roundtrip failed")
        public = {"schema": 1, "username": username, "native_uid": native_uid,
                  "boot_id": boot, "key_serial": serial, "expires": int(time.time()) + LIFETIME}
        phase = "public_metadata"
        fd = os.open(metadata, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, (json.dumps(public) + "\n").encode("ascii"))
        finally:
            os.close(fd)
    except BaseException:
        import sys
        print("acceptance_input=FAILED phase=" + phase + " errno=" + str(ctypes.get_errno()), file=sys.stderr)
        if serial >= 0:
            keys.keyctl_revoke(serial)
            keys.keyctl_unlink(serial, ring)
        raise
    finally:
        ctypes.memset(buffer, 0, 32)
        ctypes.memset(check, 0, 32)
        libc.munlock(buffer, 32)
        libc.munlock(check, 32)

def consume(username, native_uid):
    libc, keys, ring, metadata = context(username)
    from user_credentials import private_file
    fd = private_file(metadata)
    try:
        if os.fstat(fd).st_size > 4096:
            raise ValueError("invalid preparation metadata")
        with os.fdopen(fd, "r", closefd=False) as source:
            public = json.load(source)
    finally:
        os.close(fd)
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if (public.get("schema"), public.get("username"), public.get("native_uid"), public.get("boot_id")) != (1, username, native_uid, boot) or public.get("expires", 0) <= time.time():
        raise ValueError("prepared input expired or belongs to another boot or account")
    buffer = (ctypes.c_ubyte * 32)()
    if libc.mlock(buffer, 32):
        raise OSError("locked input memory unavailable")
    descriptor = -1
    try:
        serial = int(public["key_serial"])
        if keys.keyctl_read(serial, buffer, 32) != 32:
            raise ValueError("prepared input unavailable")
        descriptor = os.memfd_create("liuqin-fpc-prepared-input", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        os.fchmod(descriptor, 0o600)
        if libc.write(descriptor, buffer, 32) != 32:
            raise OSError("incomplete private FD")
        os.lseek(descriptor, 0, os.SEEK_SET)
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS,
                    fcntl.F_SEAL_WRITE | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SEAL)
        if keys.keyctl_revoke(serial):
            raise OSError("one-use key revocation failed")
        keys.keyctl_unlink(serial, ring)
        metadata.unlink()
        return descriptor
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    finally:
        ctypes.memset(buffer, 0, 32)
        libc.munlock(buffer, 32)

def discard(username):
    if not (ROOT / ("acceptance-" + username + ".json")).exists():
        return
    _, keys, ring, metadata = context(username)
    if not metadata.exists():
        return
    from user_credentials import private_file
    fd = private_file(metadata)
    try:
        with os.fdopen(fd, "r", closefd=False) as source:
            public = json.load(source)
        keys.keyctl_revoke(int(public["key_serial"]))
        keys.keyctl_unlink(int(public["key_serial"]), ring)
        metadata.unlink()
    finally:
        os.close(fd)
