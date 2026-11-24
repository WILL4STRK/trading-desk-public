"""D1 downside vetoes (docs/MEME.md section 17): random veto strata, vetoed = 0, missing feature, the forward lock. Synthetic data only."""
import json
import os
import random
import subprocess
import tempfile
import unittest
from pathlib import Path

from research.downside import d1


def sig(key="solana:A", ts=1000.0, net=1.0, r1h=0.0, br=0.6, liq=50000.0, chain="solana", why="time", exit_ts=None):
    return {"key": key, "chain": chain, "ts": ts, "liq": liq, "r1h": r1h, "buy_ratio_m5": br, "why": why,
            "exit_ts": ts + 14400 if exit_ts is None else exit_ts, "net": net}


class RandomVeto(unittest.TestCase):
    def test_skip_count_kept_within_each_stratum(self):
        rng = random.Random(3)
        sigs = [sig(key="k%d" % i, ts=(i % 3) * d1.BLOCK_S + 1, liq=[10000, 50000, 900000, None][i % 4], chain=["solana", "bsc"][i % 2],
                    r1h=[10.0, 0.0][i % 5 == 0]) for i in range(240)]
        cuts = d1.liq_cuts(sigs)
        flags = [d1.vetoed(s, "V1") for s in sigs]
        g = d1.groups(sigs, cuts)
        k = {st: sum(flags[i] for i in ix) for st, ix in g.items()}
        self.assertGreater(len(g), 6)
        for _ in range(200):
            skipped = d1.draw_skips(g, k, rng)
            got = {}
            for i in skipped:
                st = d1.stratum(sigs[i], cuts)
                got[st] = got.get(st, 0) + 1
            self.assertEqual({st: n for st, n in got.items()}, {st: n for st, n in k.items() if n})
            self.assertEqual(len(set(skipped)), len(skipped))

    def test_missing_liquidity_has_its_own_stratum(self):
        cuts = [10.0, 20.0]
        self.assertEqual(d1.stratum(sig(liq=None), cuts)[1], "u")
        self.assertEqual(d1.stratum(sig(liq=0), cuts)[1], "u")
        self.assertEqual(d1.stratum(sig(liq=15.0), cuts)[1], 1)


class Outcomes(unittest.TestCase):
    def test_vetoed_signal_counts_as_zero(self):
        sigs = [sig(key="a", net=-50.0, r1h=12.0), sig(key="b", net=10.0, r1h=1.0)]
        e = d1.evaluate(sigs, "V1", draws=10, boot=10)
        self.assertEqual(e["skipped"], 1)
        self.assertAlmostEqual(e["mean_with_veto"], (10.0 + 0.0) / 2)
        self.assertAlmostEqual(e["kept_mean"], 10.0)
        self.assertAlmostEqual(e["skipped_mean"], -50.0)

    def test_missing_feature_is_not_a_veto(self):
        self.assertFalse(d1.vetoed(sig(r1h=None), "V1"))
        self.assertFalse(d1.vetoed(sig(br=None), "V2"))
        self.assertTrue(d1.vetoed(sig(r1h=5.0), "V1"))        # the boundary: >= +5 is vetoed
        self.assertFalse(d1.vetoed(sig(br=0.50), "V2"))       # < 0.50 only
        self.assertTrue(d1.vetoed(sig(br=0.4999), "V2"))

    def test_severe_loss_definition(self):
        self.assertTrue(d1.severe(sig(why="stop", exit_ts=1000 + 3600, net=-12.0)))
        self.assertFalse(d1.severe(sig(why="stop", exit_ts=1000 + 3601, net=-12.0)))
        self.assertTrue(d1.severe(sig(why="time", net=-15.0)))


class ForwardLock(unittest.TestCase):
    def test_lock_blocks_a_second_forward_read(self):
        since = 1795404112
        rows = [{"policy": "all", "screen": "pass", "key": "solana:%d" % i, "chain": "solana", "ts": since + 100 + i * 60,
                 "f": {"liq": 30000.0 + i, "r1h": float(i % 9), "buy_ratio_m5": 0.3 + (i % 5) / 10}, "exit_ts": since + 2000 + i * 60,
                 "why": "time", "net_pct": float(i % 7 - 3)} for i in range(40)]
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "shadows.jsonl"
            f.write_text(chr(10).join(json.dumps(r) for r in rows), encoding="utf8")
            out = Path(d) / "out"
            with self.assertRaises(SystemExit):                       # before the window and its grace end: refused
                d1.forward(f, since, now=since + d1.WINDOW_S, out=out)
            self.assertFalse((out / "forward.lock").exists())
            r = d1.forward(f, since, now=since + d1.WINDOW_S + d1.GRACE_S + 1, out=out)
            self.assertEqual(r["vetoes"]["V1"]["verdict"], "inconclusive")   # 40 signals: under the 300-signal floor
            self.assertTrue((out / "forward.lock").exists())
            with self.assertRaises(SystemExit):
                d1.forward(f, since, now=since + d1.WINDOW_S + d1.GRACE_S + 2, out=out)


class SinceFromGit(unittest.TestCase):
    """Section 17 was added by MODIFYING docs/MEME.md, so the lookup must not filter on added files."""

    def test_this_repo_returns_the_section_17_commit_time(self):
        root = Path(d1.__file__).resolve().parents[2]
        known = subprocess.run(["git", "cat-file", "-e", "92be491^{commit}"], cwd=root, capture_output=True)
        if known.returncode:
            self.skipTest("92be491 is not in this checkout's history")
        self.assertEqual(d1.since_from_git(root), 1795404112)

    def test_fixture_repo_where_the_heading_is_added_to_an_existing_file(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

            def commit(text, when):
                (Path(d) / "docs").mkdir(exist_ok=True)
                (Path(d) / "docs" / "MEME.md").write_text(text, encoding="utf8")
                e = dict(env, GIT_AUTHOR_DATE="%d +0000" % when, GIT_COMMITTER_DATE="%d +0000" % when)
                subprocess.run(["git", "add", "-A"], cwd=d, check=True, env=e, capture_output=True)
                subprocess.run(["git", "commit", "-q", "-m", "c"], cwd=d, check=True, env=e, capture_output=True)

            subprocess.run(["git", "init", "-q"], cwd=d, check=True, capture_output=True)
            nl = chr(10)
            commit("# doc" + nl, 1700000000)
            commit("# doc" + nl * 2 + "## 17. D1: downside vetoes" + nl, 1700000500)    # the heading arrives in a modification
            commit("# doc" + nl * 2 + "## 17. D1: downside vetoes" + nl * 2 + "more" + nl, 1700000900)
            self.assertEqual(d1.since_from_git(d), 1700000500)


if __name__ == "__main__":
    unittest.main()


class Duplicates(unittest.TestCase):
    """Section 17.1: a shadow closed twice (a failed desk save) counts once, at its first close."""
    KEY = "solana:8WW5B189mPntQQ85HTPvQNsYxXhQbAhdjXvZ6qaapump"
    # the real case, 2026-11-23: entry 21:14:58 UTC, closed at 21:20:00 and again at 21:21:01 (save failed at 21:20:11)
    FIRST = {"key": KEY, "chain": "solana", "policy": "all", "screen": "pass", "ts": 1795468498.5843933, "at": "2026-11-23 21:14:58",
             "f": {"liq": 30000.0, "r1h": 3.0, "buy_ratio_m5": 0.6}, "exit_ts": 1795468800.3069353, "exit_at": "2026-11-23 21:20:00",
             "why": "stop", "net_pct": -17.985}
    AGAIN = dict(FIRST, exit_ts=1795468861.0062323, exit_at="2026-11-23 21:21:01", net_pct=-26.216)

    def write(self, rows):
        d = tempfile.mkdtemp(prefix="d1-dup-")
        p = Path(d) / "shadows.jsonl"
        p.write_text("".join(json.dumps(r) + chr(10) for r in rows), encoding="utf8")
        return p

    def test_the_real_case_keeps_one_line_the_21_20_00_close(self):
        other = dict(self.FIRST, key="solana:Other", ts=1795468000.0, exit_ts=1795468300.0, net_pct=5.0)
        sigs, removed = d1.read(self.write([self.FIRST, other, self.AGAIN]))
        mine = [s for s in sigs if s["key"] == self.KEY]
        self.assertEqual(removed, 1)
        self.assertEqual(len(mine), 1)
        self.assertEqual((mine[0]["exit_ts"], mine[0]["net"]), (1795468800.3069353, -17.985))
        self.assertEqual(len(sigs), 2)

    def test_the_first_close_is_kept_whatever_the_line_order(self):
        sigs, removed = d1.read(self.write([self.AGAIN, self.FIRST]))
        self.assertEqual((removed, [s["net"] for s in sigs]), (1, [-17.985]))

    def test_other_policies_and_distinct_entries_are_not_duplicates(self):
        rows = [self.FIRST, dict(self.FIRST, policy="hype"), dict(self.FIRST, ts=self.FIRST["ts"] + 900)]
        sigs, removed = d1.read(self.write(rows))
        self.assertEqual((removed, len(sigs)), (0, 2))              # the hype line is not a D1 signal

    def test_load_prints_the_count_and_forward_records_it(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            sigs = d1.load(self.write([self.FIRST, self.AGAIN]))
        self.assertIn("removed 1 duplicate close (same key, policy and entry ts; kept the earliest exit)", buf.getvalue())
        self.assertEqual(len(sigs), 1)
        out = tempfile.mkdtemp(prefix="d1-out-")
        with redirect_stdout(io.StringIO()):
            res = d1.forward(self.write([self.FIRST, self.AGAIN]), since=1795400000, now=1795400000 + d1.WINDOW_S + d1.GRACE_S + 1, out=out)
        self.assertEqual((res["duplicates_removed"], res["signals_in_window"]), (1, 1))


class LockPath(unittest.TestCase):
    """The forward read writes its lock under C:/Trading/research/downside/out on Windows, never in a repo or the live folder."""

    def test_default_out_windows_and_elsewhere(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("D1_OUT", None)
            self.assertEqual(d1.default_out("nt"), d1.WIN_OUT)
            self.assertEqual(str(d1.WIN_OUT), "C:" + chr(92) + "Trading" + chr(92) + "research" + chr(92) + "downside" + chr(92) + "out")
            self.assertEqual(d1.default_out("posix"), d1.HERE / "out")
        with mock.patch.dict(os.environ, {"D1_OUT": "X:/elsewhere"}):
            self.assertEqual(d1.default_out("nt"), Path("X:/elsewhere"))

    def test_forward_writes_its_lock_in_the_given_out(self):
        since = 1795404112
        rows = [{"policy": "all", "screen": "pass", "key": "solana:%d" % i, "chain": "solana", "ts": since + 100 + i,
                 "f": {"liq": 1.0, "r1h": 1.0, "buy_ratio_m5": 0.6}, "exit_ts": since + 900, "why": "time", "net_pct": 1.0}
                for i in range(5)]
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "shadows.jsonl"
            f.write_text(chr(10).join(json.dumps(r) for r in rows), encoding="utf8")
            out = Path(d) / "Trading" / "research" / "downside" / "out"          # does not exist yet: created
            d1.forward(f, since, now=since + d1.WINDOW_S + d1.GRACE_S + 1, out=out)
            self.assertTrue((out / "forward.lock").exists())
            self.assertFalse((d1.HERE / "out" / "forward.lock").exists() and out != d1.HERE / "out")
