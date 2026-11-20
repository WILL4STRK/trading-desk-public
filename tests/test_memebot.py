"""The Memebot tab only reads the memebot's files, and copes when they are missing."""
import json
import os
import tempfile
import shutil
import time
import unittest
from unittest import mock

from desk import memebot

LOG = """# Paper research loop

Updated: 2026-11-20T17:02:37+00:00

Status: in progress; no success established

| Cycle | Hypothesis | Train | Holdout | Forward | Distinct tokens train/holdout | Pass/fail |
|---|---|---|---|---|---|---|
| 1 | age band, entry direction | not run | not run | not run | 0/0 | collecting |

## Verdict

Research is in progress. No out-of-sample forward edge has been demonstrated.
"""


class Memebot(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="memebot-test-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        p = mock.patch.dict(os.environ, {"MEMEBOT_DIR": self.dir})
        p.start()
        self.addCleanup(p.stop)

    def write(self, rel, text):
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_missing_folder_says_where_it_looked(self):
        with mock.patch.dict(os.environ, {"MEMEBOT_DIR": os.path.join(self.dir, "nope")}):
            s = memebot.status()
        self.assertFalse(s["found"])
        self.assertIn("MEMEBOT_DIR", s["hint"])

    def test_reads_log_trades_discoveries_and_params(self):
        self.write("outputs/LOOP_LOG.md", LOG)
        self.write("discoveries.jsonl", '{"a":1}\n{"a":2}\n{"a":3}\n')
        self.write("trades_live.jsonl", "\n".join([
            json.dumps({"BUY": "X", "usd": 20}),                       # buys have no pnl: skipped
            json.dumps({"sym": "LOOSY", "pnl": -1.7, "ret": -0.085, "why": "trail", "hold_min": 12, "t_out": 100}),
            json.dumps({"sym": "ANT", "pnl": 0.2, "ret": 0.011, "why": "trail", "hold_min": 8.7, "t_out": 200}),
            "not json",
        ]) + "\n")
        self.write("cycles/cycle_01/params_candidate.json", '{"tp": 0.3}')
        s = memebot.status()
        self.assertTrue(s["found"])
        self.assertTrue(s["running"])
        self.assertEqual(s["log"]["status"], "in progress; no success established")
        self.assertEqual(s["log"]["cycles"]["rows"][0][0], "1")
        self.assertEqual(s["log"]["cycles"]["rows"][0][-1], "collecting")
        self.assertIn("No out-of-sample", s["log"]["verdict"])
        self.assertEqual(s["discoveries"], 3)
        self.assertEqual(s["frozen_params"], {"tp": 0.3})
        self.assertEqual([t["symbol"] for t in s["trades"]], ["ANT", "LOOSY"])     # newest first
        self.assertEqual(s["trade_summary"], {"count": 2, "wins": 1, "pnl": -1.5})

    def test_old_files_mean_not_running(self):
        path = self.write("LOOP_LOG.md", LOG)
        old = time.time() - 3600
        os.utime(path, (old, old))
        self.assertFalse(memebot.status()["running"])

    def test_archive_and_copies_are_not_counted_twice(self):
        row = json.dumps({"sym": "LOOSY", "pnl": -1.7, "t_out": 100}) + "\n"
        self.write("trades_live.jsonl", row)
        self.write("backup/trades_copy.jsonl", row)
        self.write("claude-session/trades_live.jsonl", json.dumps({"sym": "OLD", "pnl": 9, "t_out": 5}) + "\n")
        s = memebot.status()
        self.assertEqual([t["symbol"] for t in s["trades"]], ["LOOSY"])

    def test_skips_git_folder(self):
        self.write(".git/trades_x.jsonl", json.dumps({"sym": "HIDDEN", "pnl": 5}) + "\n")
        self.assertEqual(memebot.status()["trades"], [])


if __name__ == "__main__":
    unittest.main()
