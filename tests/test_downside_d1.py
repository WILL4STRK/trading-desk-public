"""D1 downside vetoes (docs/MEME.md section 17): random veto strata, vetoed = 0, missing feature, the forward lock. Synthetic data only."""
import json
import random
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


if __name__ == "__main__":
    unittest.main()
