"""Rule P11, attention, and its controls (docs/MEME.md section 22; desk/memeattention.py and its hooks in desk/meme.py).
No network: Helius is a fake POST."""
import json
from pathlib import Path
from unittest import mock

from desk import meme
from desk import memeattention as AT
from desk import settings as S

from .base import DeskCase
from .test_meme import FakeAPI, SOL, T0

POOL = "POOL1"
KEY = "solana:" + SOL
SECRET = "sekret-key-123"


def tx(signer, d_signer, d_pool, ts, slot=None, mint=SOL, pool=POOL, err=None, quote="QUOTEMINT"):
    """One transaction: `signer`'s balance of `mint` moves by d_signer and the pool's by d_pool (any quote token)."""
    pre = [{"owner": signer, "mint": mint, "uiTokenAmount": {"amount": "1000", "decimals": 0}},
           {"owner": pool, "mint": mint, "uiTokenAmount": {"amount": "1000000", "decimals": 0}},
           {"owner": pool, "mint": quote, "uiTokenAmount": {"amount": "500", "decimals": 0}}]
    post = [{"owner": signer, "mint": mint, "uiTokenAmount": {"amount": str(1000 + d_signer), "decimals": 0}},
            {"owner": pool, "mint": mint, "uiTokenAmount": {"amount": str(1000000 + d_pool), "decimals": 0}},
            {"owner": pool, "mint": quote, "uiTokenAmount": {"amount": "500", "decimals": 0}}]
    return {"slot": slot if slot is not None else int(ts), "blockTime": int(ts),
            "transaction": {"message": {"accountKeys": [signer, pool], "header": {"numRequiredSignatures": 1}}},
            "meta": {"err": err, "preTokenBalances": pre, "postTokenBalances": post}}


def buy(w, ts, slot=None):
    return tx(w, 10, -10, ts, slot)


def sell(w, ts, slot=None):
    return tx(w, -10, 10, ts, slot)


class FakeHelius:
    """getTransactionsForAddress answers: the pool read and the mint's first transaction (the creator's)."""

    def __init__(self, pool_txs=(), creator="CREATOR"):
        self.pool_txs, self.creator, self.calls = list(pool_txs), creator, []

    def __call__(self, url, body):
        self.calls.append((url, body))
        addr, cfg = body["params"]
        if cfg.get("sortOrder") == "asc":
            return {"result": {"data": [tx(self.creator, 10, -10, T0 - 86400)]}}
        return {"result": {"data": self.pool_txs[:cfg["limit"]]}}


def hot_txs(now, buyers=200, span=300):
    """300 successful transactions in the last `span` seconds: `buyers` distinct buyers, the rest sells."""
    out = []
    for i in range(AT.LIMIT):
        ts = now - 1 - span * i / AT.LIMIT
        out.append(buy("B%d" % i, ts) if i < buyers else sell("S%d" % i, ts))
    return out


class Definitions(DeskCase):
    def test_buys_and_sells_against_the_pool_in_any_quote(self):
        self.assertEqual(AT.swaps(buy("W", T0), SOL), [{"wallet": "W", "side": "buy", "slot": int(T0)}])
        self.assertEqual(AT.swaps(sell("W", T0), SOL)[0]["side"], "sell")
        self.assertEqual(AT.swaps(tx("W", 10, -10, T0, err={"x": 1}), SOL), [])         # failed: nothing
        self.assertEqual(AT.swaps(tx("W", 10, 0, T0), SOL), [])                          # no counterparty moved
        self.assertEqual(AT.swaps(buy("W", T0), "OTHERMINT"), [])

    def test_a_vault_owned_by_another_account_still_counts(self):
        t = tx("W", 10, -10, T0, pool="VAULT_AUTHORITY")                                  # Meteora-style vault owner
        self.assertEqual(AT.swaps(t, SOL)[0]["side"], "buy")

    def test_measure_excludes_creator_pool_and_bot_like_wallets(self):
        txs = [buy("A", T0 - 10), buy("B", T0 - 20), buy("CREATOR", T0 - 30)]
        txs += [buy("BOT", T0 - 40 - i) for i in range(6)]                                # 6 swaps: more than 5
        txs += [buy("SAND", T0 - 100, slot=7), sell("SAND", T0 - 100, slot=7)]           # buy and sell in one slot
        m = AT.measure(txs, SOL, POOL, "CREATOR", T0)
        self.assertEqual((m["buyers"], m["bots"], m["covered_s"]), (2, 2, 900))
        self.assertAlmostEqual(m["bpm"], 2 * 60 / 900, places=4)

    def test_five_swaps_is_not_bot_like(self):
        m = AT.measure([buy("W", T0 - i) for i in range(5)], SOL, POOL, None, T0)
        self.assertEqual((m["buyers"], m["bots"]), (1, 0))

    def test_covered_time_with_a_full_read(self):
        m = AT.measure(hot_txs(T0, buyers=200, span=300), SOL, POOL, None, T0)
        self.assertEqual((m["txs"], m["buyers"]), (300, 200))
        self.assertEqual(m["covered_s"], 300)
        self.assertAlmostEqual(m["bpm"], 40.0, places=4)
        short = AT.measure(hot_txs(T0, buyers=10, span=20), SOL, POOL, None, T0)
        self.assertEqual(short["covered_s"], 60)                                          # at least one minute

    def test_frozen_numbers(self):
        self.assertEqual((AT.MIN_M5_BUYS, AT.N_BPM, AT.N_BUYS, AT.SAMPLE, AT.SEED, AT.LIMIT, AT.DAY_CAP),
                         (20, 34.66, 370, 0.15, 22, 300, 30000))
        self.assertEqual(AT.DEXES, ("pumpfun", "pumpswap"))
        self.assertEqual([AT.credits_for(n) for n in (0, 1, 100, 101, 300)], [10, 10, 10, 20, 30])

    def test_the_sample_is_seeded_per_round_and_about_15_percent(self):
        import random
        self.assertEqual(AT.sampled("k", 5), random.Random("22:5:k").random() < 0.15)
        self.assertEqual(AT.sampled("k", 5), AT.sampled("k", 5))
        share = sum(AT.sampled("solana:c%d" % i, r) for i in range(200) for r in range(20)) / 4000
        self.assertTrue(0.12 < share < 0.18, share)
        self.assertEqual(AT.decision_round(900 * 7 + 899), 7)

    def pair(self, buys=30, boost=10, dex="pumpswap"):
        p = FakeAPI.pair(SOL, POOL, 1.0, 100000.0, dex=dex)
        p["txns"]["m5"]["buys"] = buys
        if boost is not None:
            p["boosts"] = {"active": boost}
        return p

    def test_which_read_a_decision_needs(self):
        yes = lambda line: True
        self.assertEqual(AT.lines_for(True, "solana", KEY, self.pair(), T0, yes)["read"], AT.POLICY)
        self.assertIsNone(AT.lines_for(False, "solana", KEY, self.pair(), T0, yes)["read"])     # P0 did not fire
        self.assertIsNone(AT.lines_for(True, "solana", KEY, self.pair(buys=19), T0, yes)["read"])
        self.assertIsNone(AT.lines_for(True, "solana", KEY, self.pair(dex="meteora"), T0, yes)["read"])
        self.assertIsNone(AT.lines_for(True, "bsc", KEY, self.pair(), T0, yes)["read"])
        self.assertIsNone(AT.lines_for(True, "solana", KEY, self.pair(), T0, lambda line: False)["read"])
        self.assertTrue(AT.lines_for(True, "solana", KEY, self.pair(buys=370), T0, yes)["count"])
        self.assertFalse(AT.lines_for(True, "solana", KEY, self.pair(buys=369), T0, yes)["count"])
        rnd = AT.decision_round(T0)
        plan = AT.lines_for(True, "solana", KEY, self.pair(boost=None), T0, yes)
        self.assertEqual(plan["read"], "crowd" if AT.sampled(KEY, rnd) else None)
        self.assertFalse(plan["count"])

    def test_threshold_is_inclusive(self):
        plan = {"read": AT.POLICY, "count": False, "sampled": False}
        self.assertTrue(AT.decide_from(plan, {"bpm": 34.66})[AT.POLICY])
        self.assertFalse(AT.decide_from(plan, {"bpm": 34.65})[AT.POLICY])
        self.assertFalse(any(AT.decide_from(plan, None).values()))
        crowd = AT.decide_from({"read": "crowd", "count": False, "sampled": True}, {"bpm": 50})
        self.assertEqual(crowd, {AT.POLICY: False, AT.COUNT: False, AT.CROWD: True})


class Budget(DeskCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(AT, "MIN_GAP_S", 0)
        p.start()
        self.addCleanup(p.stop)

    def test_the_day_cap_is_a_hard_stop_before_any_call(self):
        rec = AT.book({}, T0)
        rec["credits"] = AT.DAY_CAP - AT.RESERVE + 1
        fake = FakeHelius(hot_txs(T0))
        with mock.patch.object(AT, "POST", fake), self.assertRaises(AT.CapReached):
            AT.Reader(SECRET, rec).read(SOL, POOL, T0)
        self.assertEqual(fake.calls, [])

    def test_credits_are_counted_as_billed_and_reset_each_utc_day(self):
        s = {}
        rec = AT.book(s, T0)
        with mock.patch.object(AT, "POST", FakeHelius(hot_txs(T0))):
            AT.Reader(SECRET, rec).read(SOL, POOL, T0)
        self.assertEqual(rec["credits"], 40)                                             # 300 txs: 30, creator: 10
        with mock.patch.object(AT, "POST", FakeHelius(hot_txs(T0))):
            AT.Reader(SECRET, rec).read(SOL, POOL, T0 + 60)
        self.assertEqual(rec["credits"], 70)                                             # the creator is cached
        self.assertEqual(AT.book(s, T0 + 86400)["credits"], 0)

    def test_the_key_never_appears_in_an_error(self):
        def boom(url, body):
            raise OSError("cannot reach %s" % url)
        rec = AT.book({}, T0)
        with mock.patch.object(AT, "POST", boom), self.assertRaises(RuntimeError) as cm:
            AT.Reader(SECRET, rec).read(SOL, POOL, T0)
        self.assertNotIn(SECRET, str(cm.exception))
        self.assertIn("***", str(cm.exception))

    def test_the_read_asks_for_the_frozen_window(self):
        fake = FakeHelius(hot_txs(T0))
        with mock.patch.object(AT, "POST", fake):
            AT.Reader(SECRET, AT.book({}, T0)).read(SOL, POOL, T0)
        addr, cfg = fake.calls[0][1]["params"]
        self.assertEqual(addr, POOL)
        self.assertEqual((cfg["transactionDetails"], cfg["sortOrder"], cfg["limit"], cfg["maxSupportedTransactionVersion"]),
                         ("full", "desc", 300, 1))
        self.assertEqual(cfg["filters"], {"blockTime": {"gte": int(T0 - 900), "lt": int(T0)}, "status": "succeeded"})


class Forward(DeskCase):
    def setUp(self):
        super().setUp()
        S.save({"meme_policy": "calm_aged", "meme_attention": True})
        self.api = FakeAPI()
        p = FakeAPI.pair(SOL, POOL, 1.0, 100000.0, dex="pumpswap")
        p["boosts"] = {"active": 10}
        self.api.pairs[SOL] = p
        self.helius = FakeHelius(hot_txs(T0))
        keyfile = Path(self.tmp) / "helius-p11.key"
        keyfile.write_text(SECRET, encoding="utf-8")
        for name, value in (("POST", self.helius), ("MIN_GAP_S", 0), ("KEY_FILE", keyfile)):
            patch = mock.patch.object(AT, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def run_at(self, t):
        return meme.run(now=t, fetch=self.api)

    def lines(self):
        return sorted(x["policy"] for x in meme.load()["shadows"] if x["policy"].startswith("attention"))

    def reads(self):
        return meme._jsonl(AT.READS_LOG)

    def test_a_boosted_coin_with_many_buyers_opens_p11_and_its_account(self):
        self.run_at(T0)
        s = meme.load()
        self.assertEqual(self.lines(), ["attention"])
        sh = [x for x in s["shadows"] if x["policy"] == "attention"][0]
        self.assertEqual((sh["att"]["buyers"], sh["att"]["covered_s"], sh["att"]["boosts"], sh["dex"]),
                         (200, 300, 10, "pumpswap"))
        self.assertAlmostEqual(sh["att"]["bpm"], 40.0, places=3)
        self.assertEqual(len(s["books"]["attention"]["positions"]), 1)
        self.assertTrue([x for x in s["shadows"] if x["policy"] == "all"])               # P0 untouched
        self.assertNotIn("att", [x for x in s["shadows"] if x["policy"] == "all"][0])
        r = self.reads()
        self.assertEqual(len(r), 1)
        self.assertEqual((r[0]["reason"], r[0]["credits"], r[0]["day_credits"], r[0]["buyers"]), ("attention", 40, 40, 200))
        self.assertEqual(s[AT.STATE_KEY]["credits"], 40)

    def test_few_buyers_is_no_p11(self):
        self.helius.pool_txs = hot_txs(T0, buyers=100, span=300)                         # 20 a minute
        self.run_at(T0)
        self.assertEqual(self.lines(), [])
        self.assertEqual(len(self.reads()), 1)

    def test_control_b_uses_the_raw_count_and_reads_nothing(self):
        self.api.pairs[SOL]["txns"]["m5"]["buys"] = 400
        self.helius.pool_txs = hot_txs(T0, buyers=10, span=300)
        self.run_at(T0)
        self.assertEqual(self.lines(), ["attention_count"])

    def test_off_by_default_reads_nothing_and_buys_nothing(self):
        S.save({"meme_attention": False})
        self.run_at(T0)
        self.assertEqual((self.lines(), self.reads(), self.helius.calls), ([], [], []))
        self.assertTrue([x for x in meme.load()["shadows"] if x["policy"] == "all"])

    def test_no_key_file_reads_nothing(self):
        with mock.patch.object(AT, "KEY_FILE", Path(self.tmp) / "missing.key"):
            self.run_at(T0)
        self.assertEqual((self.lines(), self.helius.calls), ([], []))

    def test_a_meteora_pool_is_in_no_line(self):
        self.api.pairs[SOL]["dexId"] = "meteora"
        self.api.pairs[SOL]["txns"]["m5"]["buys"] = 400
        self.run_at(T0)
        self.assertEqual((self.lines(), self.helius.calls), ([], []))

    def test_at_the_cap_the_read_is_skipped_and_counted(self):
        s = meme.load() or {}
        AT.book(s, T0)["credits"] = AT.DAY_CAP - 10
        meme._save(s)
        self.run_at(T0)
        s = meme.load()
        self.assertEqual(self.lines(), [])
        self.assertEqual(self.helius.calls, [])
        self.assertEqual(s[AT.STATE_KEY]["capped"], 1)
        self.assertEqual(self.reads()[0]["skipped"], "cap")

    def test_a_failed_read_is_logged_and_the_scan_goes_on(self):
        def boom(url, body):
            raise OSError("down " + url)
        with mock.patch.object(AT, "POST", boom):
            out = self.run_at(T0)
        s = meme.load()
        self.assertEqual(out["new"], 1)
        self.assertEqual(self.lines(), [])
        self.assertIn("down", s[AT.STATE_KEY]["error"])
        self.assertNotIn(SECRET, json.dumps(s) + json.dumps(self.reads()))
        self.assertTrue([x for x in s["shadows"] if x["policy"] == "all"])

    def test_a_p11_failure_is_kept_on_its_record_and_the_core_scan_goes_on(self):
        with mock.patch.object(AT, "decide", side_effect=RuntimeError("boom")):
            out = self.run_at(T0)
        s = meme.load()
        self.assertIn("RuntimeError: boom", s[AT.STATE_KEY]["error"])
        self.assertEqual(out["new"], 1)
        self.assertEqual(self.lines(), [])
        self.assertTrue([x for x in s["shadows"] if x["policy"] == "all"])
        self.assertIn("RuntimeError: boom", meme.view()["attention"]["error"])

    def test_an_open_p11_shadow_stops_a_second_read(self):
        self.run_at(T0)
        self.run_at(T0 + 900)                                                            # next decision, still open
        self.assertEqual(len(self.reads()), 1)
        self.assertEqual(self.lines(), ["attention"])

    def test_view_card(self):
        self.run_at(T0)
        self.api.pairs[SOL] = dict(self.api.pairs[SOL], priceUsd="1.31")
        self.run_at(T0 + 60)
        v = meme.view()
        a = v["attention"]
        self.assertEqual((a["stats"]["n"], a["ends"], a["passed"], a["on"], a["key"]), (1, {"target": 1}, False, True, True))
        self.assertEqual(set(a["controls"]), {"attention_count", "attention_crowd"})
        self.assertEqual(a["everything"]["n"], 1)
        self.assertEqual((a["helius"]["credits"], a["helius"]["cap"]), (40, 30000))
        self.assertIn("attention", v["model_results"])
        self.assertIn("attention", [b["name"] for b in v["books"]])


class Bar(DeskCase):
    @staticmethod
    def rows(policy, n, coins, net, t0=1000.0):
        return [{"key": "c%d" % (i % coins), "policy": policy, "screen": "pass", "ts": t0 + i * 60,
                 "net_pct": net + (i % 3)} for i in range(n)]

    def test_only_p11_can_pass_and_it_must_beat_everything_and_the_raw_count(self):
        good = self.rows("attention", 120, 60, 2.0) + self.rows("attention_count", 50, 25, -1.0) + self.rows("all", 50, 25, -3.0)
        fw = meme.forward(good)
        self.assertTrue(meme._bar_for("attention", fw["attention"], fw))
        for k in ("attention_count", "attention_crowd", "attention_all", "attention+screen"):
            self.assertFalse(meme._bar_for(k, fw.get(k) or {}, fw))
        fw = meme.forward(self.rows("attention", 120, 60, 2.0) + self.rows("attention_count", 50, 25, 5.0)
                          + self.rows("all", 50, 25, -3.0))
        self.assertFalse(meme._bar_for("attention", fw["attention"], fw))                # (b) is better
        fw = meme.forward(self.rows("attention", 120, 60, 2.0) + self.rows("all", 50, 25, -3.0))
        self.assertFalse(meme._bar_for("attention", fw["attention"], fw))                # no (b) yet: no pass

    def test_comparison_period_starts_at_the_first_p11_family_shadow(self):
        rows = [{"key": "a", "policy": "all", "screen": "pass", "ts": 10, "net_pct": -9.0},
                {"key": "b", "policy": "all", "screen": "fail", "ts": 210, "net_pct": -5.0},
                {"key": "c", "policy": "attention_crowd", "screen": "pass", "ts": 200, "net_pct": 1.0}]
        fw = meme.forward(rows)
        self.assertEqual((fw["attention_all"]["n"], fw["attention_all"]["mean"]), (1, -5.0))
