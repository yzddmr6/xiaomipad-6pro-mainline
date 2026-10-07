#!/usr/bin/env python3
"""Real private-file/subprocess tests with a fake TA runtime; no device access.

Run: unshare -Ur python3 tests/fingerprint-native-provider.py (or as root).
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
OEM = PROJECT / "device/fingerprint/oem/src/fingerprint/oem"
spec = importlib.util.spec_from_file_location("native_enrol", OEM / "native_enrol.py")
provider = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provider)

FAKE = r'''
import hashlib,json,os,pathlib,signal,sys,time
base=pathlib.Path(__file__).parent
state=pathlib.Path(sys.argv[2]); operation=sys.argv[3]; uid=sys.argv[-2]
secret=os.read(int(sys.argv[-1]),33)
assert len(secret)==32
with (base/'calls').open('a') as f:
 f.write(json.dumps({'operation':operation,'uid':uid,'input_sha':hashlib.sha256(secret).hexdigest()})+'\n'); f.flush()
mode=(base/'mode').read_text()
cleanup='oem_runtime_cleanup firmware_path_restored=1 sensor_power_off=1 listener_stopped=1'
if operation=='--create':
 if mode=='cancel-create':
  (base/'create-ready').write_text('yes');time.sleep(.25)
 if mode=='create-fail':
  print(cleanup,flush=True);sys.exit(1)
 p=state/uid/'gatekeeper.handle';fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 os.write(fd,b'opaque-test-handle');os.close(fd);print(cleanup,flush=True);sys.exit(0)
assert operation=='--enrol'
print('READY enrol attempt=1: press same finger and hold',flush=True)
print('enrol_progress_status=1 remaining=8',flush=True)
if mode=='cancel':
 def cancel(sig,frame):
  time.sleep(0.1);print(cleanup,flush=True);(base/'child-cleaned').write_text('yes');sys.exit(1)
 signal.signal(signal.SIGTERM,cancel)
 (base/'ready').write_text('yes')
 while True:time.sleep(.05)
if mode=='enrol-fail':
 print(cleanup,flush=True);sys.exit(1)
fd=os.open(sys.argv[4],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
os.write(fd,b'opaque-single-finger-test-database');os.close(fd)
if mode=='output-race':pathlib.Path((base/'race-target').read_text()).write_text('keep existing output')
if mode=='unsafe-output':os.chmod(sys.argv[4],0o644)
print('do-not-forward-raw-runtime-output',flush=True)
if mode!='missing-cleanup':print(cleanup,flush=True)
'''


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.bundle = self.base / "bundle"
        self.bundle.mkdir(mode=0o700)
        self.output_dir = self.base / "output"
        self.output_dir.mkdir(mode=0o700)
        (self.bundle / "native_credentials.py").write_text(FAKE)
        (self.bundle / "native_credentials.py").chmod(0o600)
        (self.bundle / "mode").write_text("ok")
        self.user = SimpleNamespace(pw_name="alice", pw_uid=1000)
        self.configure_user()
        self.state = self.base / "native-enrollment"
        self.patches = [patch.object(provider, "__file__", str(self.bundle / "native_enrol.py")),
                        patch.object(provider, "STATE_ROOT", self.state),
                        patch.object(provider, "FPRINT_ROOT", self.base / "fprint"),
                        patch.object(provider.pwd, "getpwnam", side_effect=lambda name: self.user)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def configure_user(self):
        (self.bundle / "CANDIDATE.json").write_text(json.dumps({
            "linux_username": self.user.pw_name, "linux_uid": self.user.pw_uid}))
        (self.bundle / "CANDIDATE.json").chmod(0o600)

    def calls(self):
        path = self.bundle / "calls"
        return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []

    def invoke(self, filename="new-print", username=None):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status = provider.main(["--enrol", username or self.user.pw_name, str(self.output_dir / filename)])
        return status, output.getvalue()

    def test_creates_once_reuses_and_preserves_legacy_identity(self):
        legacy = self.base / "native" / str(0x50000000 | 1000)
        legacy.mkdir(parents=True)
        (legacy / "gatekeeper.handle").write_bytes(b'untouched old password credential')
        self.assertEqual(self.calls(), [])
        rc, log = self.invoke()
        self.assertEqual(rc, 0, log)
        self.assertIn("enrol_progress_status=1 remaining=8", log)
        self.assertTrue(log.endswith("native_enrol_cleanup=OK\nnative_enrol=OK single_finger_database=1\n"))
        self.assertNotIn("do-not-forward", log)
        account = self.state / str(0x60000000 | 1000)
        record = json.loads((account / "credential.json").read_text())
        self.assertEqual(len(bytes.fromhex(record["secret_hex"])), 32)
        self.assertNotIn(record["secret_hex"], log)
        self.assertFalse((account / "create.requested").exists())
        self.assertEqual((account.stat().st_mode & 0o777), 0o700)
        for path in (account / "credential.json", account / "gatekeeper.handle", self.output_dir / "new-print"):
            self.assertEqual((path.stat().st_uid, path.stat().st_gid, path.stat().st_mode & 0o777), (0, 0, 0o600))
        self.assertEqual(self.invoke("second-print")[0], 0)
        calls = self.calls()
        self.assertEqual([x["operation"] for x in calls], ["--create", "--enrol", "--enrol"])
        self.assertEqual(len({x["input_sha"] for x in calls}), 1)
        self.assertEqual((legacy / "gatekeeper.handle").read_bytes(), b'untouched old password credential')

    def test_target_is_claimed_account_not_root_or_agent(self):
        self.assertNotEqual(self.invoke(username="root")[0], 0)
        self.user = SimpleNamespace(pw_name="root", pw_uid=0)
        self.configure_user()
        self.assertNotEqual(self.invoke()[0], 0)
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.state.exists())

    def test_nonroot_refused_before_state_or_runtime(self):
        with patch.object(provider.os, "geteuid", return_value=1000):
            self.assertNotEqual(self.invoke()[0], 0)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.calls(), [])

    def test_partial_create_is_never_retried(self):
        (self.bundle / "mode").write_text("create-fail")
        self.assertNotEqual(self.invoke()[0], 0)
        account = self.state / str(0x60000000 | 1000)
        self.assertTrue((account / "create.requested").exists())
        before = (account / "credential.json").read_bytes()
        (self.bundle / "mode").write_text("ok")
        self.assertNotEqual(self.invoke()[0], 0)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual((account / "credential.json").read_bytes(), before)

    def test_legacy_template_blocks_before_new_identity_or_ta(self):
        template = self.base / "fprint/alice/fpc1264_oem/liuqin-fpc1264-oem/a"
        template.parent.mkdir(parents=True)
        template.write_bytes(b"legacy fingerprint is retained")
        rc, log = self.invoke()
        self.assertNotEqual(rc, 0)
        self.assertIn("native_enrol=REFUSED existing_template=1", log)
        self.assertEqual(template.read_bytes(), b"legacy fingerprint is retained")
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.state.exists())

    def test_cancel_during_create_finishes_commit_without_enrolling(self):
        (self.bundle / "mode").write_text("cancel-create")
        def cancel_when_creating():
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if (self.bundle / "create-ready").exists():
                    os.kill(os.getpid(), signal.SIGTERM)
                    return
                time.sleep(.01)
        sender = threading.Thread(target=cancel_when_creating)
        sender.start()
        rc, log = self.invoke()
        sender.join()
        self.assertNotEqual(rc, 0)
        self.assertIn("native_enrol=CANCELLED", log)
        self.assertIn("native_enrol_cleanup=OK", log)
        self.assertEqual([c["operation"] for c in self.calls()], ["--create"])
        account = self.state / str(0x60000000 | 1000)
        self.assertTrue((account / "gatekeeper.handle").exists())
        self.assertFalse((account / "create.requested").exists())
        self.assertFalse((self.output_dir / "new-print").exists())
        (self.bundle / "mode").write_text("ok")
        self.assertEqual(self.invoke()[0], 0)
        self.assertEqual([c["operation"] for c in self.calls()], ["--create", "--enrol"])

    def test_missing_final_cleanup_cannot_reuse_create_cleanup(self):
        (self.bundle / "mode").write_text("missing-cleanup")
        rc, log = self.invoke()
        self.assertNotEqual(rc, 0)
        self.assertIn(provider.CLEANUP, log)  # Credential create did clean.
        self.assertNotIn("native_enrol_cleanup=OK", log)
        self.assertNotIn("native_enrol=OK", log)
        self.assertFalse((self.output_dir / "new-print").exists())

    def test_existing_output_race_never_overwrites(self):
        path = self.output_dir / "new-print"
        (self.bundle / "race-target").write_text(str(path))
        (self.bundle / "mode").write_text("output-race")
        rc, log = self.invoke()
        self.assertNotEqual(rc, 0)
        self.assertEqual(path.read_text(), "keep existing output")
        self.assertNotIn("native_enrol=OK", log)

    def test_unsafe_template_and_credential_are_rejected(self):
        (self.bundle / "mode").write_text("unsafe-output")
        self.assertNotEqual(self.invoke()[0], 0)
        self.assertFalse((self.output_dir / "new-print").exists())
        record = self.state / str(0x60000000 | 1000) / "credential.json"
        record.chmod(0o644)
        previous = len(self.calls())
        self.assertNotEqual(self.invoke()[0], 0)
        self.assertEqual(len(self.calls()), previous)

    def test_cancel_waits_for_cleanup_and_next_enrol_reuses_identity(self):
        (self.bundle / "mode").write_text("cancel")
        def cancel_when_ready():
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if (self.bundle / "ready").exists():
                    os.kill(os.getpid(), signal.SIGTERM)
                    return
                time.sleep(.01)
        sender = threading.Thread(target=cancel_when_ready)
        sender.start()
        rc, log = self.invoke()
        sender.join()
        self.assertNotEqual(rc, 0)
        self.assertTrue((self.bundle / "child-cleaned").exists(), log)
        self.assertIn("native_enrol_cleanup=OK", log)
        self.assertIn("native_enrol=CANCELLED", log)
        self.assertNotIn("native_enrol=OK", log)
        self.assertFalse((self.output_dir / "new-print").exists())
        (self.bundle / "mode").write_text("ok")
        self.assertEqual(self.invoke()[0], 0)
        self.assertEqual(sum(c["operation"] == "--create" for c in self.calls()), 1)

    def test_distinct_user_credentials_do_not_share_secret_or_handle(self):
        self.assertEqual(self.invoke()[0], 0)
        self.user = SimpleNamespace(pw_name="bob", pw_uid=1001)
        self.configure_user()
        self.assertEqual(self.invoke("bob-print")[0], 0)
        creates = [c for c in self.calls() if c["operation"] == "--create"]
        self.assertEqual(len(creates), 2)
        self.assertNotEqual(creates[0]["uid"], creates[1]["uid"])
        self.assertNotEqual(creates[0]["input_sha"], creates[1]["input_sha"])


class PolicyTests(unittest.TestCase):
    def test_only_active_local_enrollment_requires_sender_authentication(self):
        rule = PROJECT / "device/fingerprint/oem/src/fingerprint/deployment/60-liuqin-fingerprint-enroll.rules"
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');let rule;
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'), {polkit:{Result:{AUTH_SELF:'self'},addRule:r=>rule=r}});
const enroll='net.reactivated.fprint.device.enroll';
assert.strictEqual(rule({id:enroll},{local:true,active:true,user:'alice'}),'self');
for (const id of ['net.reactivated.fprint.device.verify','net.reactivated.fprint.device.setusername','org.liuqin.fingerprint.accept'])
 assert.strictEqual(rule({id},{local:true,active:true}),undefined);
assert.strictEqual(rule({id:enroll},{local:false,active:true}),undefined);
assert.strictEqual(rule({id:enroll},{local:true,active:false}),undefined);
'''
        subprocess.run(["node", "-e", script, str(rule)], check=True)


if __name__ == "__main__":
    if os.geteuid() != 0:
        raise SystemExit("Use: unshare -Ur python3 tests/fingerprint-native-provider.py (no device access)")
    unittest.main()
