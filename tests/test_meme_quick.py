"""Rule P9, quick (docs/MEME.md section 15; desk/memequick.py and its hooks in desk/meme.py). No network."""
from unittest import mock

from desk import meme
from desk import memequick as QK
from desk import memerules as R
from desk import settings as S

from .base import DeskCase
from .test_meme import FakeAPI, SOL, T0


class Pure(DeskCase):
    def test_entry_is_p0s_decision_on_a_passed_screen_and_unknown_is_no(self):
        self.assertTrue(QK.decide(True, "pass"))
        self.assertFalse(QK.decide(True, "unknown"))
        self.assertFalse(QK.decide(True, "fail"))
        self.assertFalse(QK.decide(False, "pass"))

    def test_stop_fills_at_the_sampled_price_a_gap_is_real(self):
        self.assertEqual(QK.exit_sample(1.0, 0, 0.95, 5e4, 60), (0.95, "stop"))
        self.assertEqual(QK.exit_sample(1.0, 0, 0.80, 5e4, 60), (0.80, "stop"))      # gapped far under -5 %
        self.assertIsNone(QK.exit_sample(1.0, 0, 0.9501, 5e4, 60))

    def test_target_fills_at_the_target(self):
        self.assertEqual(QK.exit_sample(1.0, 0, 1.30, 5e4, 60), (1.10, "target"))
        self.assertIsNone(QK.exit_sample(1.0, 0, 1.0999, 5e4, 60))

    def test_stop_is_first_when_both_trigger_in_a_sample_or_a_candle(self):
        self.assertEqual(QK.exit_sample(1.0, 0, 0.9, 5e4, 30 * 60 + 5)[1], "stop")     # stop and time together
        # a candle touching both +10 % and -5 %: replay with P9's numbers says stop
        r = R.replay(0, 1.0, [[0, 1.0, 1.2, 0.9, 1.0, 1]], stop=QK.STOP_PCT, target=QK.TARGET_PCT, hold_min=QK.HOLD_MIN)
        self.assertEqual((r["why"], r["price"]), ("stop", 0.95))
        gap = R.replay(0, 1.0, [[0, 0.9, 0.95, 0.88, 0.9, 1]], stop=QK.STOP_PCT, target=QK.TARGET_PCT, hold_min=QK.HOLD_MIN)
        self.assertEqual(gap["price"], 0.9)                                              # opened under the stop

    def test_time_limit_is_30_minutes(self):
        self.assertIsNone(QK.exit_sample(1.0, 0, 1.02, 5e4, 30 * 60 - 1))
        self.assertEqual(QK.exit_sample(1.0, 0, 1.02, 5e4, 30 * 60), (1.02, "time"))
        r = R.replay(0, 1.0, [[0, 1.0, 1.04, 0.98, 1.02, 1]], now=30 * 60 + 1, hold_min=QK.HOLD_MIN,
                     stop=QK.STOP_PCT, target=QK.TARGET_PCT)
        self.assertEqual((r["why"], r["price"], r["ts"]), ("time", 1.02, 30 * 60))

    def test_dead_pool_and_liquidity_pull(self):
        self.assertEqual(QK.exit_sample(1.0, 0, 1.0, 500.0, 60), (0.0, "rug"))
        self.assertEqual(QK.exit_sample(1.0, 0, None, 5e4, 60), (0.0, "rug"))
        self.assertEqual(QK.exit_sample(1.0, 0, 1.0, 4e4, 60, entry_liq=1e5), (1.0, "liquidity pulled"))
        self.assertIsNone(QK.exit_sample(1.0, 0, 1.0, 4e4, 60))              # a shadow has no entry liquidity

    def test_cost_math_and_the_breakeven_win_rate(self):
        win, loss = R.net_pct(1.0, 1.10), R.net_pct(1.0, 0.95)
        self.assertAlmostEqual(win, 6.2, delta=0.05)
        self.assertAlmostEqual(loss, -8.27, delta=0.02)
        self.assertEqual(QK.breakeven_win_pct(3.5), 57.1)
        self.assertEqual(QK.breakeven_win_pct(6.0), 74.6)
        self.assertAlmostEqual(R.net_pct(1.0, 1.10, 6.0), 3.59, delta=0.02)      # Ethereum
        st = S.load()
        self.assertEqual(meme.cost_pct(st, "solana", "quick"), 3.5)
        self.assertEqual(meme.cost_pct(st, "ethereum", "quick"), 6.0)

    def test_control_window_starts_at_the_first_quick_shadow(self):
        rows = [{"key": "a", "policy": "all", "screen": "pass", "ts": 10, "net_pct": -9.0},
                {"key": "b", "policy": "all", "screen": "pass", "ts": 110, "net_pct": -5.0},
                {"key": "c", "policy": "all", "screen": "unknown", "ts": 120, "net_pct": 1.0},
                {"key": "d", "policy": "quick", "screen": "pass", "ts": 100, "net_pct": 6.0}]
        fw = meme.forward(rows)
        self.assertEqual((fw["quick_control"]["n"], fw["quick_control"]["mean"]), (1, -5.0))
        self.assertEqual(fw["quick_all"]["n"], 2)
        self.assertEqual(fw["quick"]["n"], 1)
        self.assertFalse(meme._bar_for("quick_control", fw["quick_control"], fw))      # a comparator, never a candidate


class Forward(DeskCase):
    def setUp(self):
        super().setUp()
        S.save({"meme_policy": "calm_aged"})
        self.api = FakeAPI()

    def run_at(self, t):
        return meme.run(now=t, fetch=self.api)

    def quick(self):
        return [x for x in meme.load()["shadows"] if x["policy"] == "quick"]

    def test_entry_logs_dex_and_opens_a_shadow_and_its_own_account(self):
        self.run_at(T0)
        sh = self.quick()
        self.assertEqual((len(sh), sh[0]["dex"], sh[0]["screen"], sh[0]["px"]), (1, "raydium", "pass", 1.0))
        s = meme.load()
        pos = s["books"]["quick"]["positions"][0]
        self.assertEqual((pos["policy"], pos["dex"]), ("quick", "raydium"))
        self.assertAlmostEqual(pos["cost"], 20.0)                                     # 20 % of $100
        self.assertAlmostEqual(pos["fill"], 1.0 * (1 + 0.0175 + 20 / 50000))          # desk cost plus price impact
        # P0 is untouched: its shadow keeps the section 3 bracket and has no dex field
        self.assertNotIn("dex", [x for x in s["shadows"] if x["policy"] == "all"][0])

    def test_an_unscreened_coin_gets_no_quick_entry(self):
        self.api.rug = None                                                           # safety check unknown
        self.run_at(T0)
        s = meme.load()
        self.assertEqual(self.quick(), [])
        self.assertEqual(s["books"]["quick"]["positions"], [])
        self.assertTrue([x for x in s["shadows"] if x["policy"] == "all"])            # P0 still logs it

    def test_target_closes_at_plus_10_while_p0_stays_open(self):
        self.run_at(T0)
        self.api.pairs[SOL] = FakeAPI.pair(SOL, "POOL1", 1.12, 100000.0)
        out = self.run_at(T0 + 60)
        s = meme.load()
        done = [x for x in meme._jsonl("shadows.jsonl") if x["policy"] == "quick"][0]
        self.assertEqual((done["why"], done["exit_px"]), ("target", 1.10))
        self.assertAlmostEqual(done["net_pct"], R.net_pct(1.0, 1.10), places=2)
        self.assertEqual([x["policy"] for x in s["shadows"] if x["policy"] != "moonshot_base"],
                         ["all", "calm_aged"])                                         # +12 % is no exit for them
        self.assertEqual(s["books"]["quick"]["closed"][0]["why"], "target")
        self.assertEqual(out["shadows_closed"], 1)

    def test_a_stop_fills_at_the_gapped_sample(self):
        self.run_at(T0)
        self.api.pairs[SOL] = FakeAPI.pair(SOL, "POOL1", 0.90, 100000.0)
        self.run_at(T0 + 60)
        done = [x for x in meme._jsonl("shadows.jsonl") if x["policy"] == "quick"][0]
        self.assertEqual((done["why"], done["exit_px"]), ("stop", 0.90))
        self.assertEqual(meme.load()["books"]["quick"]["closed"][0]["exit_px"], 0.90)

    def test_30_minute_time_exit(self):
        self.run_at(T0)
        for t in range(int(T0) + 60, int(T0) + 1800, 60):                                   # a sample a minute
            self.api.pairs[SOL] = FakeAPI.pair(SOL, "POOL1", 1.02, 100000.0, now=t)
            self.run_at(t)
        self.assertEqual(len(self.quick()), 1)                                         # 29 minutes: still open
        self.api.pairs[SOL] = FakeAPI.pair(SOL, "POOL1", 1.02, 100000.0, now=T0 + 1800)
        self.run_at(T0 + 1800)
        done = [x for x in meme._jsonl("shadows.jsonl") if x["policy"] == "quick"][0]
        self.assertEqual((done["why"], done["exit_px"]), ("time", 1.02))

    def test_candle_replay_uses_the_quick_bracket_after_a_gap_in_the_desk_run(self):
        self.run_at(T0)
        later = T0 + 3600
        self.addCleanup(setattr, meme, "GT_PER_RUN", meme.GT_PER_RUN)
        meme.GT_PER_RUN = 30                                                           # one candle call per open item
        self.api.pairs[SOL] = FakeAPI.pair(SOL, "POOL1", 1.0, 100000.0, now=later)
        self.api.bars = [[T0 + 300, 1.0, 1.03, 0.99, 1.0, 10], [T0 + 600, 0.93, 0.95, 0.91, 0.92, 10]]
        self.run_at(later)
        done = [x for x in meme._jsonl("shadows.jsonl") if x["policy"] == "quick"][0]
        self.assertEqual((done["why"], done["exit_px"], done["exit_ts"]), ("stop", 0.93, T0 + 600))

    def test_a_p9_failure_is_kept_on_its_record_and_the_core_scan_goes_on(self):
        with mock.patch.object(QK, "decide", side_effect=RuntimeError("boom")):
            out = self.run_at(T0)
        s = meme.load()
        self.assertIn("RuntimeError: boom", s["quick_v1"]["error"])
        self.assertEqual((out["new"], out["bought"]), (1, 1))                          # calm_aged still bought
        self.assertEqual(self.quick(), [])
        self.assertIn("RuntimeError: boom", meme.view()["quick"]["error"])

    def test_view_card_numbers(self):
        self.run_at(T0)
        self.api.pairs[SOL] = FakeAPI.pair(SOL, "POOL1", 1.12, 100000.0)
        self.run_at(T0 + 60)
        q = meme.view()["quick"]
        self.assertEqual((q["stats"]["n"], q["ends"], q["breakeven_pct"], q["dex"]), (1, {"target": 1}, 57.1, {"raydium": 1}))
        self.assertEqual(q["target_pct"], 100.0)
        self.assertIn("quick+screen", meme.view()["forward"])
