"""The paired N1 shadow, with synthetic closes and the desk's fake provider."""

import os
import tempfile
from unittest import mock

from desk import meme
from desk import memereentry as MR
from desk import settings as S

from .base import DeskCase
from .test_meme import FakeAPI, SOL, T0

KEY = "solana:" + SOL
START = (int(T0) // MR.DAY + 1) * MR.DAY


def entry(ts, key=KEY, policy="calm_aged"):
    return {"key": key, "chain": "solana", "policy": policy, "ts": ts, "liq": 25000, "fill": 1.02, "cost": 20}


def close(fill, ts, why="stop", pnl=-4):
    return dict(fill, exit_ts=ts, why=why, exit_fill=1.02 * (1 + pnl / 20), pnl=pnl, ret_pct=pnl * 5)


def snapshots(key=KEY, pool="A", volume=20000):
    return {key: {"pairAddress": pool, "volume": {"h1": volume}}}


class Lockout(DeskCase):
    def setUp(self):
        super().setUp()
        self.st = S.validate({"meme_reentry_shadow": True})
        self.study = MR.new(self.st, START - 60)

    def observe(self, when, entries=(), exits=(), market=None):
        MR.observe(self.study, self.st, when, entries, exits, snapshots() if market is None else market)

    def test_two_stops_lock_the_mint_across_rules_and_pools_and_keep_the_control(self):
        first = entry(START)
        self.observe(START, [first])
        self.observe(START + 60, exits=[close(first, START + 60)])
        second = entry(START + 3660, policy="dip_aged")
        self.observe(second["ts"], [second], market=snapshots(pool="B"))
        stop = START + 3720
        self.observe(stop, exits=[close(second, stop)])

        third = entry(START + 7320, policy="news")
        self.observe(third["ts"], [third], market=snapshots(pool="C"))
        row = self.study["rows"][-1]
        self.assertEqual(row["lock"], {"first_stop_ts": START + 60, "second_stop_ts": stop, "until_ts": stop + MR.DAY})
        self.assertEqual((row["pool"], row["policy"]), ("C", "news"))
        # A control stop for an already blocked fill never extends the lock.
        self.observe(third["ts"] + 60, exits=[close(third, third["ts"] + 60)])
        self.assertEqual((row["outcome"]["control_pnl"], row["outcome"]["shadow_pnl"]), (-4, 0))
        self.assertEqual(self.study["locks"][KEY]["until_ts"], stop + MR.DAY)

        other = entry(stop + MR.DAY - 1, key=KEY.lower())
        self.observe(other["ts"], [other], market=snapshots(other["key"]))
        self.assertIsNone(self.study["rows"][-1]["lock"])
        at_expiry = entry(stop + MR.DAY)
        self.observe(at_expiry["ts"], [at_expiry])
        self.assertIsNone(self.study["rows"][-1]["lock"])

    def test_late_stop_uses_exit_clock_and_is_known_only_when_booked(self):
        first = entry(START)
        self.observe(START, [first])
        self.observe(START + 60, exits=[close(first, START + 60)])
        second = entry(START + 3660)
        self.observe(second["ts"], [second])
        stop = second["ts"] + 60
        self.observe(stop + 2 * 3600)
        self.assertEqual(self.study["locks"], {})

        known = stop + 3 * 3600
        self.observe(known, [entry(known)], [close(second, stop)])
        self.assertEqual(self.study["rows"][1]["outcome"]["observed_ts"], known)
        self.assertEqual(self.study["rows"][1]["outcome"]["exit_ts"], stop)
        self.assertEqual(self.study["rows"][2]["lock"]["until_ts"], stop + MR.DAY)

    def test_expired_first_stop_and_nonstop_losses_do_not_trigger_a_lock(self):
        first = entry(START)
        self.observe(START, [first])
        self.observe(START + 60, exits=[close(first, START + 60)])
        second = entry(START + MR.DAY)
        self.observe(second["ts"], [second])
        self.observe(second["ts"] + 60, exits=[close(second, second["ts"] + 60)])
        third = entry(second["ts"] + 3660)
        self.observe(third["ts"], [third])
        self.observe(third["ts"] + 60, exits=[close(third, third["ts"] + 60, why="rug", pnl=-20)])
        self.assertEqual(self.study["locks"], {})
        self.assertEqual(self.study["last_stop"][KEY], second["ts"] + 60)

    def test_forward_window_filters_and_settings_changes(self):
        self.assertEqual(self.study["start_ts"], START)
        self.observe(START - 1, [entry(START - 1)])
        self.observe(START, [entry(START)], market=snapshots(volume=19999))
        self.observe(START + 1, [dict(entry(START + 1), liq=24999)])
        self.observe(START + 2, [dict(entry(START + 2), chain="base")])
        self.observe(START + 3, exits=[close(entry(START - 1), START + 3)])
        self.assertEqual(self.study["rows"], [])

        enrolled = entry(START + 4)
        self.observe(enrolled["ts"], [enrolled])
        self.st = dict(self.st, meme_cost_pct=4.5)
        self.observe(START + 5, [entry(START + 5)], exits=[close(enrolled, START + 5)])
        self.st = dict(self.st, meme_cost_pct=3.5)
        self.observe(START + 6, [entry(START + 6)])
        self.assertEqual(self.study["incomplete"]["reason"], "reference settings changed")
        self.assertEqual(len(self.study["rows"]), 1)
        self.assertEqual(self.study["rows"][0]["outcome"]["shadow_pnl"], -4)

    def test_enrollment_ends_and_late_unmatched_outcomes_stay_unresolved(self):
        end = self.study["end_ts"]
        enrolled = entry(end - 1)
        self.observe(end - 1, [enrolled])
        self.observe(end, [entry(end, key=KEY.lower())], market=snapshots(KEY.lower()))
        deadline = self.study["settle_by_ts"]
        self.observe(deadline + 1, exits=[close(enrolled, end)])
        self.assertEqual(len(self.study["rows"]), 1)
        self.assertIsNone(self.study["rows"][0]["outcome"])
        self.assertEqual(self.study["pending"], {KEY: 0})

    def test_disabling_stops_enrollment_and_retains_closes(self):
        first = entry(START)
        self.observe(START, [first])
        self.st = dict(self.st, meme_reentry_shadow=False)
        self.observe(START + 60, [entry(START + 60)], [close(first, START + 60)])
        self.st = dict(self.st, meme_reentry_shadow=True)
        self.observe(START + 120, [entry(START + 120)])
        self.assertEqual(self.study["incomplete"], {"ts": START + 60, "reason": "observer disabled"})
        self.assertEqual(len(self.study["rows"]), 1)
        self.assertEqual(self.study["rows"][0]["outcome"]["shadow_pnl"], -4)


class CarefulTape(DeskCase):
    def replay(self, enabled):
        class Profiles(FakeAPI):
            def __call__(self, url):
                if url.endswith("/token-profiles/latest/v1"):
                    self.calls.append(url)
                    return self.feed
                if url.endswith("/token-boosts/latest/v1"):
                    self.calls.append(url)
                    return []
                return super().__call__(url)

        S.save({"meme_reentry_shadow": enabled})
        api = Profiles()
        api.rug = {"score_normalised": 5, "risks": [], "topHolders": [{"owner": "w", "pct": 5}],
                   "markets": [{"pubkey": "POOL1", "lp": {"lpLockedPct": 100}}], "knownAccounts": {},
                   "token": {"supply": 100}, "creatorBalance": 0}
        api.feed = []
        meme.run(now=START - 600, fetch=api)
        api.feed = [{"chainId": "solana", "tokenAddress": SOL}]
        for offset, price in [(0, 1.0), (60, 0.8), (3660, 1.0), (3720, 0.8), (7320, 1.0), (7380, 1.4)]:
            api.pairs[SOL] = api.pair(SOL, "POOL1", price, 100000, age_h=72, now=START)
            meme.run(now=START + offset, fetch=api)
        logs = {path.name: path.read_bytes() for path in meme.folder().glob("*.jsonl")}
        return meme.load(), logs, api.calls

    def test_shadow_records_skipped_winner_without_changing_reference_or_requests(self):
        baseline, baseline_logs, baseline_calls = self.replay(False)
        self.assertNotIn(MR.STATE_KEY, baseline)
        with tempfile.TemporaryDirectory() as other, mock.patch.dict(os.environ, {"DESK_DATA": other}):
            state, logs, calls = self.replay(True)
        rows = state.pop(MR.STATE_KEY)["rows"]
        self.assertEqual([row["outcome"]["why"] for row in rows], ["stop", "stop", "target"])
        self.assertTrue(rows[2]["lock"])
        self.assertGreater(rows[2]["outcome"]["control_pnl"], 0)
        self.assertEqual(rows[2]["outcome"]["shadow_pnl"], 0)
        self.assertEqual(state, baseline)
        self.assertEqual(logs, baseline_logs)
        self.assertEqual(calls, baseline_calls)
