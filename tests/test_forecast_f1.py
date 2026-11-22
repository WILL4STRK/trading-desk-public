"""Rule F1 (docs/MEME.md section 16.1): input cutoff, fill at the next bar's open, gaps, one position per token.
Synthetic bars only; no model, no network, no desk data."""
import copy
import unittest

from desk import memerules as R
from research.forecast import core

T0 = 1795000200               # divisible by 300 (a 5-minute bar open) and a bar that closes on the 15-minute grid at T0 + 600
TOK = {"key": "solana:TEST", "chain": "solana", "token": "TEST", "created_ts": T0 - 40 * 3600, "dead": False}
NOW = T0 + 10 ** 7


def make_bars(n=420):
    """Slowly drifting price; each bar opens 0.1 % above the previous close so the fill price differs from every close."""
    bars, prev = [], 1.0
    for k in range(n):
        o = prev * 1.001
        c = o * (1 + 0.0004 * ((k % 7) - 3))
        bars.append([T0 + 300 * k, o, max(o, c) * 1.002, min(o, c) * 0.998, c, 2000.0])
        prev = c
    return bars


class Cutoff(unittest.TestCase):
    def test_last_usable_is_close_time_at_or_before_t(self):
        bars = make_bars(10)
        t = bars[5][0] + 300                       # bar 5 closes exactly at t: usable
        self.assertEqual(core.last_usable(bars, t), 5)
        self.assertEqual(core.last_usable(bars, t - 1), 4)       # one second earlier: bar 5 has not closed
        self.assertEqual(core.last_usable(bars, bars[5][0]), 4)  # at bar 5's OPEN time it is not usable either

    def test_a_bar_closing_after_t_never_reaches_input_or_features(self):
        bars = make_bars()
        pts, _ = core.points(TOK, bars, NOW)
        self.assertTrue(pts)
        p = pts[3]
        i = next(k for k, b in enumerate(bars) if b[0] + 300 == p["decide_ts"])
        poisoned = copy.deepcopy(bars)
        for j in range(i + 1, len(poisoned)):      # every bar that opens at or after t: wild close, high, low and volume
            poisoned[j][2:] = [9e9, 1e-9, 9e9, 9e9]   # high, low, close, volume (the open stays: it is the fill price)
        pts2, _ = core.points(TOK, poisoned, NOW)
        q = next(x for x in pts2 if x["decide_ts"] == p["decide_ts"])
        self.assertEqual(p["closes"], q["closes"])                 # the forecaster input is identical
        self.assertLess(max(q["closes"]), 1e6)                     # and holds no poisoned close
        self.assertEqual((p["p0"], p["persist"]), (q["p0"], q["persist"]))   # eligibility and features did not move
        self.assertEqual(p["entry_px"], q["entry_px"])             # only the next open is used for the fill

    def test_input_window_ends_at_the_last_closed_bar(self):
        bars = make_bars()
        pts, _ = core.points(TOK, bars, NOW)
        p = pts[0]
        i = next(k for k, b in enumerate(bars) if b[0] + 300 == p["decide_ts"] - 300 + 300 - 0) if False else \
            next(k for k, b in enumerate(bars) if b[0] + 300 == p["decide_ts"])
        self.assertEqual(p["closes"][-1], bars[i][4])
        self.assertEqual(len(p["closes"]), core.CTX)


class Fill(unittest.TestCase):
    def test_fill_is_open_of_next_bar_with_section_2_cost_for_every_arm(self):
        bars = make_bars()
        pts, _ = core.points(TOK, bars, NOW)
        for p in pts:
            p["f1"] = p["persist"] = True           # force all three arms to trade every point they can
        by_ts = {b[0]: k for k, b in enumerate(bars)}
        for arm in core.ARMS:
            trades = core.arm_trades(pts, arm)
            self.assertTrue(trades, arm)
            for tr in trades:
                k = by_ts[tr["decide_ts"] - 300] + 1           # the first bar after the input window
                self.assertEqual(tr["entry_ts"], bars[k][0])
                self.assertEqual(tr["entry_px"], bars[k][1])    # its OPEN, not any close
                self.assertNotEqual(tr["entry_px"], bars[k - 1][4])
                ex = R.replay(bars[k][0], bars[k][1], bars[k:], dead=False, now=NOW)
                self.assertEqual(tr["net"], R.net_pct(bars[k][1], ex["price"]))   # the section 2 cost
                self.assertEqual(tr["exit_ts"], ex["ts"])


class Gaps(unittest.TestCase):
    def test_forward_fill_and_the_20_percent_rule(self):
        bars = make_bars()
        i = 400
        window = [b for b in bars if bars[i][0] - 300 * 287 <= b[0] <= bars[i][0]]
        some = [b[0] for b in window][5:5 + 50]           # 50 missing steps of 288: 17.4 %, still eligible
        thin = [b for b in bars if b[0] not in set(some)]
        k = next(x for x, b in enumerate(thin) if b[0] == bars[i][0])
        closes, filled = core.grid(thin, k)
        self.assertEqual(filled, 50)
        self.assertEqual(len(closes), 288)
        gap0 = (some[0] - (bars[i][0] - 300 * 287)) // 300
        self.assertEqual(closes[gap0], closes[gap0 - 1])  # a filled step repeats the previous close
        more = [b[0] for b in window][5:5 + 60]           # 60 missing steps: 20.8 %, ineligible
        thin2 = [b for b in bars if b[0] not in set(more)]
        upto = next(x for x, b in enumerate(thin2) if b[0] == bars[i][0]) + 2    # keep one bar after the decision for the fill
        pts, sk = core.points(TOK, thin2[:upto], NOW)
        self.assertNotIn(bars[i][0] + 300, [p["decide_ts"] for p in pts])
        self.assertGreater(sk["gaps"], 0)

    def test_first_step_without_an_earlier_bar_is_ineligible(self):
        bars = make_bars()
        thin = [b for b in bars if b[0] != bars[0][0]]            # drop the very first bar: step 0 has nothing to fill from
        closes, _ = core.grid(thin, 286)                           # window starts before the first remaining bar
        self.assertIsNone(closes)


class OnePosition(unittest.TestCase):
    def test_open_position_blocks_and_cooldown_applies(self):
        def pt(t, exit_ts, key="k"):
            return {"key": key, "decide_ts": t, "exit_ts": exit_ts, "p0": True, "persist": True, "f1": True}
        pts = [pt(1000, 1000 + 1800), pt(1900, 5000), pt(1000 + 1800 + 3599, 9999), pt(1000 + 1800 + 3600, None),
               pt(1000 + 1800 + 4000, 1), pt(1000, 2000, key="other")]
        got = [(t["key"], t["decide_ts"]) for t in core.arm_trades(pts, "p0")]
        # k buys at 1000, is held to 2800 (1900 blocked), cooldown to 6400 (6399 blocked), buys at 6400 with no exit: blocks the rest
        self.assertEqual(sorted(got), sorted([("k", 1000), ("other", 1000), ("k", 6400)]))
        for p in pts:
            p["f1"] = False                               # an arm whose flag is off never buys
        self.assertEqual(core.arm_trades(pts, "f1"), [])


class Signal(unittest.TestCase):
    def test_f1_thresholds(self):
        c = [1.0] * 288
        self.assertTrue(core.f1_signal(c, 0.95, 1.08))
        self.assertFalse(core.f1_signal(c, 0.95, 1.0799))     # median just under +8 %
        self.assertFalse(core.f1_signal(c, 0.8999, 1.2))      # 20th percentile just under -10 %
        self.assertTrue(core.f1_signal(c, 0.9, 1.2))


if __name__ == "__main__":
    unittest.main()
