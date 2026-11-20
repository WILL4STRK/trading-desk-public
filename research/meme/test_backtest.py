"""Checks for the backtest glue (research/meme/backtest.py) on synthetic bars. The rules themselves live in
desk/memerules.py. Run: python3 -m unittest research.meme.test_backtest"""
import random
import unittest

from desk import memerules as R
from research.meme import backtest as B

T0 = 900 * 2_000_000          # on the 15-minute grid


def flat(n, px=1.0, vol=2000.0, gap_every=0):
    """n five-minute bars at a flat price; vol 2000 a bar makes v1h $24k once an hour has passed."""
    out, ts = [], T0
    for k in range(n):
        out.append([ts, px, px, px, px, vol])
        ts += 600 if gap_every and k % gap_every == gap_every - 1 else 300
    return out


TOK = {"chain": "solana", "token": "X", "created_ms": None, "dead": False}


class Glue(unittest.TestCase):
    def test_entry_at_next_open_on_grid(self):
        bars = flat(80)
        bars[12][1] = 1.01            # an open that differs, to see which bar fills
        trades = [t for t in B.simulate(TOK, bars, now=bars[-1][0] + 10 ** 6) if t["policy"] == "all"]
        first = trades[0]
        i = next(k for k, b in enumerate(bars) if b[0] + 300 == first["decide_ts"])
        self.assertEqual(first["decide_ts"] % 900, 0)
        self.assertEqual(first["entry_ts"], bars[i + 1][0])
        self.assertEqual(first["entry_px"], bars[i + 1][1])
        self.assertEqual(i, 11)       # first grid bar with 10+ bars of history (v1h >= $20k)
        self.assertAlmostEqual(first["net"], R.net_pct(1.01, 1.0))

    def test_one_position_then_cooldown(self):
        bars = flat(200)
        trades = [t for t in B.simulate(TOK, bars, now=bars[-1][0] + 10 ** 6) if t["policy"] == "all"]
        self.assertGreaterEqual(len(trades), 2)
        for a, b in zip(trades, trades[1:]):
            self.assertEqual(a["why"], "time")
            self.assertGreaterEqual(b["decide_ts"], a["exit_ts"] + R.COOLDOWN_MIN * 60)
            self.assertLess(b["decide_ts"] - B.STEP, a["exit_ts"] + R.COOLDOWN_MIN * 60)   # the first allowed point

    def test_dead_pool_is_a_total_loss_not_dropped(self):
        bars = flat(30)
        alive = [t for t in B.simulate(TOK, bars, now=bars[-1][0]) if t["policy"] == "all"]
        dead = [t for t in B.simulate(dict(TOK, dead=True), bars, now=bars[-1][0]) if t["policy"] == "all"]
        self.assertEqual([t["why"] for t in alive], ["open"])
        self.assertIsNone(alive[0]["net"])
        self.assertEqual(B.stats(alive)["open_at_end"], 1)
        self.assertEqual([t["why"] for t in dead], ["rug"])
        self.assertEqual(dead[0]["net"], -100.0)
        self.assertEqual(dead[0]["h240"], -100.0)
        self.assertEqual(B.stats(dead)["trades"], 1)

    def test_now_past_time_stop_closes_quiet_trade(self):
        bars = flat(30)
        tr = [t for t in B.simulate(TOK, bars, now=bars[-1][0] + 5 * 3600) if t["policy"] == "all"]
        self.assertEqual(tr[0]["why"], "time")
        self.assertTrue(tr[0]["by_now"])

    def test_split_and_folds(self):
        trades = [{"entry_ts": x} for x in (0, 10, 24, 25, 59, 60, 75, 100)]
        w = B.split(trades)
        self.assertEqual(w["cut"], 60)
        self.assertEqual([t["part"] for t in trades], ["dev"] * 5 + ["holdout"] * 3)
        self.assertEqual([t["fold"] for t in trades], [0, 0, 0, 1, 2, 2, 3, 3])

    def test_sliced_features_match_full(self):
        rnd = random.Random(7)
        bars, ts, px = [], T0, 1.0
        for _ in range(700):
            px *= 1 + rnd.uniform(-0.03, 0.03)
            bars.append([ts, px, px * 1.02, px * 0.98, px * (1 + rnd.uniform(-0.01, 0.01)), rnd.uniform(0, 5000)])
            ts += 300 * rnd.choice((1, 1, 1, 2, 5))            # gaps, as GeckoTerminal leaves out quiet slots
        k = 0
        for i in range(len(bars)):
            while k + 1 <= i and bars[k + 1][0] <= bars[i][0] - 86400:
                k += 1
            if i % 7 == 0 or i < 30:
                self.assertEqual(B._feat(bars, i, k, T0 - 3600), R.features_from_bars(bars, i, created_ts=T0 - 3600))
        self.assertGreater(k, 0)   # the slice was really used


if __name__ == "__main__":
    unittest.main()
