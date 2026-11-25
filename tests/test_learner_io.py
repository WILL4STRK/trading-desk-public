"""L1 infrastructure: learner.atomic_write retries a refused replace like the desk's write_json (PR #19); the watchdog reads
share-safely, so it never blocks a replace; its ALERT compares predictions with policy-all, screen-pass OPENS in model.json,
never with shadows.jsonl growth. Temp folders only; the PowerShell parts run on Windows only."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from research.learner import learner as L

PS1 = Path(L.__file__).with_name("run_learner.ps1")
WINDOWS = os.name == "nt" and shutil.which("powershell")


class AtomicWrite(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="l1-io-"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_a_replace_refused_twice_then_allowed_writes_the_file(self):
        real, calls, sleeps = os.replace, [], []

        def flaky(src, dst):
            calls.append(dst)
            if len(calls) <= 2:
                raise PermissionError(5, "Access is denied")
            real(src, dst)
        with mock.patch.object(L.os, "replace", flaky), mock.patch.object(L.time, "sleep", sleeps.append):
            L.atomic_write(self.dir / "state.json", '{"n": 1}')
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [0.05, 0.1])
        self.assertEqual((self.dir / "state.json").read_text(), '{"n": 1}')
        self.assertFalse((self.dir / "state.json.tmp").exists())

    def test_ten_refusals_raise_as_before_with_the_capped_backoff(self):
        sleeps = []
        with mock.patch.object(L.os, "replace", side_effect=PermissionError(5, "Access is denied")), \
                mock.patch.object(L.time, "sleep", sleeps.append), self.assertRaises(PermissionError):
            L.atomic_write(self.dir / "state.json", "{}")
        self.assertEqual(sleeps, [0.05, 0.1, 0.2, 0.4, 0.5, 0.5, 0.5, 0.5, 0.5])

    def test_other_errors_are_not_retried(self):
        with mock.patch.object(L.os, "replace", side_effect=FileNotFoundError("x")) as rep, self.assertRaises(FileNotFoundError):
            L.atomic_write(self.dir / "state.json", "{}")
        self.assertEqual(rep.call_count, 1)


@unittest.skipUnless(WINDOWS, "Windows and PowerShell only")
class SharedRead(unittest.TestCase):
    """What the watchdog's read does to a replace. Measured on this Windows: any open handle on the file makes os.replace onto
    it fail with PermissionError, even the watchdog's [IO.File]::Open(path, 'Open', 'Read', 'ReadWrite,Delete'). So the
    read is kept to one short open, and the learner's atomic_write retries the replace and gets through."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="l1-share-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.f = self.dir / "state.json"
        self.f.write_text("{}")

    def hold(self, ms, share="ReadWrite,Delete"):
        ps = "$fs = [IO.File]::Open('%s', 'Open', 'Read', '%s'); Write-Output held; [Console]::Out.Flush(); Start-Sleep -Milliseconds %d; $fs.Dispose()" % (self.f, share, ms)
        p = subprocess.Popen(["powershell", "-NoProfile", "-Command", ps], stdout=subprocess.PIPE, text=True)
        self.assertEqual(p.stdout.readline().strip(), "held")
        return p

    def test_a_held_watchdog_read_still_refuses_a_plain_replace(self):
        p = self.hold(2000)
        try:
            tmp = self.dir / "state.json.tmp"
            tmp.write_text('{"n": 2}')
            with self.assertRaises(PermissionError):
                os.replace(tmp, self.f)
        finally:
            p.wait(10); p.stdout.close()

    def test_atomic_write_rides_out_a_watchdog_read(self):
        p = self.hold(400)                                        # far longer than the watchdog's one read
        try:
            t0 = time.time()
            L.atomic_write(self.f, '{"n": 3}')                     # retries until the read is over
            took = time.time() - t0
        finally:
            p.wait(10); p.stdout.close()
        self.assertEqual(self.f.read_text(), '{"n": 3}')
        self.assertLess(took, 3.5)

    def test_the_watchdog_uses_only_the_shared_read(self):
        src = PS1.read_text(encoding="utf-8")
        code = chr(10).join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
        self.assertNotIn("Get-Content", code)
        self.assertIn("[IO.File]::Open($p, 'Open', 'Read', 'ReadWrite,Delete')", code)


@unittest.skipUnless(WINDOWS, "Windows and PowerShell only")
class Watchdog(unittest.TestCase):
    """Runs the real watchdog (run_learner.ps1 -Watch) against temp folders."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="l1-watch-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home, self.desk = self.root / "home", self.root / "desk"
        for d in (self.home / "out", self.home / "state", self.desk):
            d.mkdir(parents=True)
        self.log = self.root / "watchdog.log"
        self.now = time.time()
        (self.home / "state" / "state.json").write_text(json.dumps({"counts": {"predicted": 1, "closes": 5, "missed": 0, "void": 0, "scored": 0},
                                                                     "heartbeat": self.now, "error": None}))

    def shadow(self, key, ts, policy="all", screen="pass"):
        return {"key": key, "policy": policy, "screen": screen, "ts": ts, "chain": "solana"}

    def write(self, shadows, preds, shadows_jsonl_bytes=0):
        (self.desk / "model.json").write_text(json.dumps({"shadows": shadows, "tracked": {}}))
        (self.desk / "shadows.jsonl").write_bytes(b"x" * shadows_jsonl_bytes)
        lines = [json.dumps({"entry_ts": ts, "key": k, "pred": 0.1}, sort_keys=True, separators=(",", ":")) for k, ts in preds]   # as the learner writes
        (self.home / "out" / "predictions.jsonl").write_text("".join(l + "\n" for l in lines))

    def watch(self):
        env = dict(os.environ, L1_HOME=str(self.home), L1_DESK_MEME=str(self.desk), L1_ALERT_LOG=str(self.log))
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PS1), "-Watch"],
                           env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        return self.log.read_text(encoding="utf-8-sig").strip().splitlines()[-1]

    def test_no_alert_when_shadows_jsonl_grows_from_other_policies_closes(self):
        old = self.now - 3 * 3600
        a = self.shadow("solana:A", old)
        self.write([a, self.shadow("solana:H", old, policy="hype"), self.shadow("solana:F", old, screen="fail")], [("solana:A", old)])
        # the old rule's setup: predictions.jsonl idle for 2 hours while shadows.jsonl keeps growing
        (self.home / "state" / "watch.json").write_text(json.dumps({"pr_len": (self.home / "out" / "predictions.jsonl").stat().st_size,
                                                                    "pr_grew": "2026-01-01T00:00:00Z", "missed": 0}))
        self.watch()
        for n in (1000, 5000, 9000):                               # closes of every policy land in shadows.jsonl
            (self.desk / "shadows.jsonl").write_bytes(b"x" * n)
            line = self.watch()
            self.assertNotIn("without a prediction", line)
            self.assertNotIn("shadows.jsonl", line)
            self.assertIn("opens_10min=1 opens_without_prediction=0", line)

    def test_an_open_left_without_a_prediction_is_an_alert_after_10_minutes(self):
        self.write([self.shadow("solana:A", self.now - 20 * 60), self.shadow("solana:B", self.now - 3 * 60),
                    self.shadow("solana:C", L.T18 - 100), self.shadow("solana:H", self.now - 20 * 60, policy="hype")], [])
        line = self.watch()
        self.assertIn("ALERT", line)
        self.assertIn("1 policy-all screen-pass open(s) in model.json for 10+ min without a prediction", line)
        self.assertIn("opens_10min=1 opens_without_prediction=1", line)       # B is too young, C before T18, H another policy

    def test_a_float_entry_ts_matches_its_prediction(self):
        ts = 1795484498.327618                                      # a real entry ts: the float must match exactly
        self.write([self.shadow("solana:B34n", ts)], [("solana:B34n", ts)])
        line = self.watch()
        self.assertIn("opens_without_prediction=0", line)


if __name__ == "__main__":
    unittest.main()
