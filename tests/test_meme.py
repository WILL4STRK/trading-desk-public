"""Model 4, memecoins (desk/meme.py, desk/memerules.py, docs/MEME.md): rules, costs, screen, the paper account,
catch-up from candles, the route and the scheduler. No network: a fake API answers every GET."""
import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
from unittest import mock

from desk import meme
from desk import memerules as R
from desk import memigrate as M
from desk import memoon as MN
from desk import memoonpilot as MP
from desk import settings as S
from desk.scheduler import Scheduler
from desk.server import serve

from .base import DeskCase
from .test_desk import DemoCase

T0 = 1795000000.0
SOL = "So1aNaMeme1111111111111111111111111111pump"
BSC = "0xabc0000000000000000000000000000000000001"


class FakeAPI:
    """Answers the desk's read-only GETs from a few dicts the test changes between runs."""

    def __init__(self):
        self.feed = [{"chainId": "solana", "tokenAddress": SOL}]
        self.pairs = {SOL: self.pair(SOL, "POOL1", 1.0, 100000.0)}
        self.rug = {"score_normalised": 5, "risks": [], "lpLockedPct": 90}
        self.goplus = {}
        self.bars = []
        self.trades = []
        self.calls = []

    @staticmethod
    def pair(addr, pool, price, liq, age_h=48.0, now=T0, dex="raydium", **pc):
        change = {"m5": 0.2, "h1": 2.0, "h6": 0.0, "h24": 5.0}
        change.update(pc)
        return {"chainId": "solana", "dexId": dex, "pairAddress": pool, "baseToken": {"address": addr, "symbol": "MEME"},
                "priceUsd": str(price), "liquidity": None if liq is None else {"usd": liq}, "fdv": 1e6,
                "pairCreatedAt": (now - age_h * 3600) * 1000, "priceChange": change,
                "volume": {"m5": 3000.0, "h1": 50000.0, "h6": 300000.0, "h24": 1000000.0},
                "txns": {"m5": {"buys": 30, "sells": 20}}}

    def __call__(self, url):
        self.calls.append(url)
        if "/tokens/v1/" in url:
            return [p for p in self.pairs.values() if p["baseToken"]["address"] in url]
        if "dexscreener.com/token-boosts/latest" in url:
            return self.feed
        if "dexscreener.com" in url:
            return []
        if "rugcheck" in url:
            return self.rug
        if "gopluslabs" in url:
            return {"code": 1, "result": self.goplus}
        if "geckoterminal" in url and "/trades" in url:
            return {"data": [{"id": str(i), "attributes": a} for i, a in enumerate(self.trades)]}
        if "geckoterminal" in url:
            return {"data": {"attributes": {"ohlcv_list": list(reversed(self.bars))}}}
        raise AssertionError(url)


class Rules(DeskCase):
    def test_cost_is_half_on_each_side_and_break_even_needs_about_3_56_pct(self):
        self.assertAlmostEqual(R.net_pct(1.0, 1.0), (0.9825 / 1.0175 - 1) * 100)
        self.assertAlmostEqual(R.net_pct(1.0, 1.0356), 0.0, delta=0.01)
        self.assertEqual(R.net_pct(1.0, 0.0), -100.0)

    def test_unknown_is_never_a_pass(self):
        self.assertEqual(R.screen_verdict("solana", None)[0], "unknown")
        self.assertEqual(R.screen_verdict("solana", {"risks": []})[0], "unknown")
        self.assertEqual(R.screen_verdict("bsc", {"is_honeypot": "0"})[0], "unknown")
        ok = {"is_honeypot": "0", "buy_tax": "0", "sell_tax": "0.02", "cannot_sell_all": "0", "is_open_source": "1"}
        self.assertEqual(R.screen_verdict("bsc", ok), ("pass", []))
        self.assertEqual(R.screen_verdict("bsc", dict(ok, is_honeypot="1"))[0], "fail")
        self.assertEqual(R.screen_verdict("bsc", dict(ok, sell_tax="0.25"))[0], "fail")
        self.assertEqual(R.screen_verdict("solana", {"score_normalised": 5, "risks": [{"name": "x", "level": "danger"}]})[0], "fail")
        self.assertEqual(R.screen_verdict("solana", {"score_normalised": 60, "risks": []})[0], "fail")

    def test_stop_gaps_fill_at_the_open_and_a_bar_touching_both_is_a_stop(self):
        self.assertEqual(R.replay(0, 1.0, [[0, 0.7, 0.8, 0.6, 0.7, 1]])["price"], 0.7)        # opened under the stop
        both = R.replay(0, 1.0, [[0, 1.0, 1.5, 0.8, 1.2, 1]])
        self.assertEqual((both["why"], both["price"]), ("stop", 0.85))
        self.assertEqual(R.replay(0, 1.0, [[0, 1.0, 1.31, 0.95, 1.2, 1]])["why"], "target")
        self.assertIsNone(R.replay(0, 1.0, [[0, 1.0, 1.1, 0.95, 1.0, 1]]))

    def test_a_dead_pool_is_a_total_loss_never_dropped_and_never_before_the_buy(self):
        r = R.replay(1000, 1.0, [[700, 1, 1, 1, 1, 1]], dead=True)
        self.assertEqual((r["price"], r["why"]), (0.0, "rug"))
        self.assertGreaterEqual(r["ts"], 1000)

    def test_time_stop_closes_a_quiet_pool_at_its_last_price(self):
        r = R.replay(0, 1.0, [[0, 1.0, 1.05, 0.95, 1.02, 1]], now=R.HOLD_MIN * 60 + 1)
        self.assertEqual((r["why"], r["price"]), ("time", 1.02))

    def test_policies(self):
        f = R.features_from_pair(FakeAPI.pair(SOL, "P", 1.0, 1e5), T0 * 1000)
        d = R.decide(f)
        self.assertTrue(d["all"] and d["calm_aged"])
        self.assertFalse(d["dip_aged"] or d["hype"] or d["breakout"])
        young = R.decide(R.features_from_pair(FakeAPI.pair(SOL, "P", 1.0, 1e5, age_h=2), T0 * 1000))
        self.assertFalse(young["calm_aged"])
        self.assertTrue(R.decide(dict(f, r1h=-15.0))["dip_aged"])

    def test_settings_are_validated(self):
        for bad in ({"meme_policy": "moon"}, {"meme_chains": ["doge"]}, {"meme_cost_pct": -1}, {"meme_enabled": "yes"}):
            with self.assertRaises(S.SettingsError):
                S.validate(bad)
        self.assertEqual(S.validate({"meme_chains": "bsc, solana"})["meme_chains"], ["solana", "bsc"])
        self.assertEqual(S.validate({"meme_news_words": "GTA 6 launch\n, trump "})["meme_news_words"], ["GTA 6 launch", "trump"])


class Research(DeskCase):
    def test_aged_liquid_needs_a_deep_pool_and_no_boost(self):
        p = FakeAPI.pair(SOL, "P", 1.0, 100000.0, age_h=100, h24=5.0, h1=1.0)
        p["marketCap"] = 500000
        f = R.features_from_pair(p, T0 * 1000)
        self.assertTrue(R.decide(f)["aged_liquid"])
        self.assertFalse(R.decide(dict(f, boosts_active=3))["aged_liquid"])
        self.assertFalse(R.decide(dict(f, liq=20000.0))["aged_liquid"])

    def test_vetoes(self):
        self.assertEqual(R.vetoes({"age_h": 2}), ["R1 under 24 h old"])
        self.assertEqual(R.vetoes({"age_h": 48, "boosts_active": 1}), ["R2 paid promotion"])
        self.assertEqual(R.vetoes({"age_h": 48, "rvol": 6, "r1h": 1}), ["R3 flat volume burst"])
        self.assertEqual(R.vetoes({"age_h": 48, "rvol": 6, "r1h": 12}), [])

    def test_strict_check_solana(self):
        rep = {"topHolders": [{"owner": "POOL", "pct": 50}, {"owner": "w1", "pct": 10}, {"owner": "w2", "pct": 5}],
               "markets": [{"pubkey": "POOL", "lp": {"lpLockedPct": 100}}], "knownAccounts": {},
               "token": {"supply": 1000}, "creatorBalance": 0}
        self.assertEqual(R.strict_verdict("solana", rep, "POOL"), ("pass", []))   # the pool's own share is left out
        rep["topHolders"].append({"owner": "w3", "pct": 20})
        self.assertEqual(R.strict_verdict("solana", rep, "POOL")[0], "fail")
        self.assertEqual(R.strict_verdict("solana", dict(rep, topHolders=[]), "POOL")[0], "unknown")
        self.assertEqual(R.strict_verdict("solana", None)[0], "unknown")

    def test_strict_check_evm_owner_powers_unless_renounced(self):
        ok = {"creator_percent": "0.01", "owner_percent": "0", "holders": [{"address": "0x1", "percent": "0.1"}],
              "owner_address": "0xabc", "honeypot_with_same_creator": "0"}
        self.assertEqual(R.strict_verdict("bsc", ok)[0], "pass")
        self.assertEqual(R.strict_verdict("bsc", dict(ok, hidden_owner="1"))[0], "fail")
        self.assertEqual(R.strict_verdict("bsc", dict(ok, hidden_owner="1", owner_address=R.DEAD[1]))[0], "pass")
        self.assertEqual(R.strict_verdict("bsc", dict(ok, creator_percent="0.6"))[0], "fail")

    def test_news_words_come_from_watch_lines_and_match_a_coin(self):
        S.save({"meme_news_words": ["TTWO GTA 6 launches 2026-11-19"]})
        words = meme.news_words(S.load())
        self.assertIn("GTA", words)
        self.assertNotIn("LAUNCHES", words)
        self.assertTrue(meme.news_match({"symbol": "GTA6", "name": "GTA Six Coin"}, words))
        self.assertFalse(meme.news_match({"symbol": "BONK", "name": "Bonk"}, words))
        self.assertTrue(meme.news_match({"symbol": "X", "sources": ["meta:ai"]}, set()))

    def test_ethereum_pays_more(self):
        self.assertEqual(meme.cost_pct(S.load(), "ethereum"), 6.0)
        self.assertEqual(meme.cost_pct(S.load(), "solana"), 3.5)

    def test_shadows_carry_the_strict_check_and_the_vetoes(self):
        api = FakeAPI()
        api.rug = {"score_normalised": 5, "risks": [], "topHolders": [{"owner": "w", "pct": 5}],
                   "markets": [{"pubkey": "POOL1", "lp": {"lpLockedPct": 100}}], "knownAccounts": {},
                   "token": {"supply": 100}, "creatorBalance": 0}
        meme.run(now=T0, fetch=api)
        sh = meme.load()["shadows"][0]
        self.assertEqual((sh["strict"], sh["veto"]), ("pass", ["R2 paid promotion"]))   # it came from the boost feed
        fw = meme.forward([dict(sh, net_pct=1.0)])
        self.assertEqual((fw["all+strict"]["n"], fw["all+evidence"]["n"]), (1, 0))


class Conclusion(DeskCase):
    def rows(self, policy, vals, coins=60):
        return [{"key": "c%d" % (i % coins), "policy": policy, "screen": "pass", "strict": "pass", "veto": [],
                 "net_pct": v, "ts": T0 + i * 600} for i, v in enumerate(vals)]

    def test_the_pass_bar_needs_3_of_4_time_slices(self):
        # every trade wins in the first quarter and loses after: a good average from one lucky stretch
        vals = [40.0] * 30 + [-2.0] * 90
        x = meme.stats(self.rows("aged_liquid", vals))
        self.assertGreater(x["mean"], 0)
        self.assertEqual(x["folds_pos"], 1)
        self.assertFalse(meme.bar(x))
        self.assertIn({"check": "positive in 3 of 4 time slices", "ok": False},
                      meme.verdict({"all": {}}, "learn", self.rows("aged_liquid", vals)))

    def test_rows_without_times_have_no_folds(self):
        self.assertEqual(meme.folds([{"net_pct": 1.0}]), [])

    def test_collecting_then_no_edge_then_pass(self):
        self.assertEqual(meme.conclusion(meme.forward([]))[0], "collecting")
        losing = [r for p in ("all", "hype", "calm_aged", "dip_aged") for r in self.rows(p, [-8.0, -3.0, 1.0] * 40)]
        self.assertEqual(meme.conclusion(meme.forward(losing))[0], "no_edge")
        winning = losing + self.rows("aged_liquid", [6.0, 2.0, -1.0] * 40)
        state, lines = meme.conclusion(meme.forward(winning))
        self.assertEqual(state, "pass")
        self.assertIn("aged_liquid", lines)


class Search(DeskCase):
    def test_search_new_adds_only_young_pools_on_the_chosen_chains(self):
        young = {"chainId": "solana", "baseToken": {"address": "NEW1"}, "pairCreatedAt": (T0 - 3600) * 1000}
        old = {"chainId": "solana", "baseToken": {"address": "OLD1"}, "pairCreatedAt": (T0 - 5 * 86400) * 1000}
        other = {"chainId": "tron", "baseToken": {"address": "TRX1"}, "pairCreatedAt": (T0 - 60) * 1000}
        seen = []

        def fetch(url):
            seen.append(url)
            return {"pairs": [young, old, other]} if "/latest/dex/search" in url else []
        found = meme.discover(S.load(), fetch, now=T0, turn=1)
        self.assertEqual(found, {("solana", "NEW1"): ["search-new"]})
        terms = [u.split("q=")[1] for u in seen if "q=" in u]
        self.assertEqual(len(terms), meme.SEARCH_PER_PASS)
        S.update({"meme_search_new": False})
        self.assertEqual(meme.discover(S.load(), fetch, now=T0), {})

    def test_the_turn_rotates_the_words(self):
        def words(turn):
            got = []
            meme.discover(S.load(), lambda u: got.append(u) or {}, now=T0, turn=turn)
            return [u.split("q=")[1] for u in got if "q=" in u]
        self.assertNotEqual(words(0), words(1))


class Throttle(DeskCase):
    def test_a_busy_geckoterminal_is_skipped_at_once_not_waited_on(self):
        import io
        err = urllib.error.HTTPError("u", 429, "slow down", {}, io.BytesIO(b""))
        with mock.patch.object(meme, "get", meme.__dict__["_real_get"]), \
                mock.patch("urllib.request.urlopen", side_effect=err) as op:
            meme._cool.clear()
            with self.assertRaises(meme.MemeError):
                meme.get("https://api.geckoterminal.com/api/v2/x")
            t = time.time()
            with self.assertRaises(meme.MemeError):
                meme.get("https://api.geckoterminal.com/api/v2/y")
            self.assertLess(time.time() - t, 0.5)
            self.assertEqual(op.call_count, 1)                  # the second call never left the Mac
            meme._cool.clear()


class Learning(DeskCase):
    def test_careful_is_the_default_and_skips_a_promoted_coin(self):
        self.assertEqual(S.load()["meme_policy"], "careful")
        out = meme.run(now=T0, fetch=FakeAPI())                  # from the boost feed: research filter R2
        self.assertEqual(out["bought"], 0)
        self.assertEqual(meme.view()["policy"], "careful")

    def test_learning_mode_buys_on_any_of_the_rules(self):
        S.save({"meme_policy": "learn"})
        api = FakeAPI()
        api.pairs[SOL] = FakeAPI.pair(SOL, "POOL1", 1.0, 100000.0, h1=-15.0)     # a dip: calm_aged says no
        out = meme.run(now=T0, fetch=api)
        self.assertEqual(out["bought"], 1)
        self.assertEqual(meme.load()["account"]["positions"][0]["policy"], "dip_aged")
        self.assertEqual(meme.view()["policy"], "learn")


class Book(DeskCase):
    def setUp(self):
        super().setUp()
        S.save({"meme_policy": "calm_aged"})
        self.api = FakeAPI()

    def run_at(self, t):
        return meme.run(now=t, fetch=self.api)

    def test_first_sighting_is_logged_screened_shadowed_and_bought(self):
        out = self.run_at(T0)
        self.assertEqual((out["new"], out["bought"]), (1, 1))
        disc = meme._jsonl("discoveries.jsonl")
        self.assertEqual(disc[0]["sources"], ["boosts-latest"])
        s = meme.load()
        self.assertEqual(sorted(sh["policy"] for sh in s["shadows"]), ["all", "calm_aged"])
        self.assertTrue(all(sh["screen"] == "pass" for sh in s["shadows"]))
        pos = s["account"]["positions"][0]
        self.assertAlmostEqual(pos["cost"], 20.0)                           # 20% of $100
        self.assertAlmostEqual(pos["fill"], 1.0 * (1 + 0.0175 + 20 / 50000))  # half the cost plus price impact
        self.assertAlmostEqual(s["account"]["cash"], 80.0)

    def test_a_stop_sells_at_the_sampled_price_after_costs_and_closes_the_shadows(self):
        self.run_at(T0)
        self.api.pairs[SOL] = FakeAPI.pair(SOL, "POOL1", 0.80, 100000.0)
        out = self.run_at(T0 + 60)
        self.assertEqual((out["sold"], out["shadows_closed"]), (1, 2))
        t = meme.load()["account"]["closed"][0]
        self.assertEqual(t["why"], "stop")
        qty = 20.0 / (1.0175 + 20 / 50000)
        proceeds = qty * 0.80 * (1 - 0.0175 - qty * 0.80 / 50000)
        self.assertAlmostEqual(t["pnl"], proceeds - 20.0, places=3)
        sh = meme._jsonl("shadows.jsonl")
        self.assertAlmostEqual(sh[0]["net_pct"], R.net_pct(1.0, 0.80), places=2)
        self.assertEqual(meme.view()["account"]["trades"], 1)

    def test_an_unknown_screen_records_shadows_but_never_buys(self):
        self.api.rug = None
        out = self.run_at(T0)
        self.assertEqual(out["bought"], 0)
        self.assertEqual({sh["screen"] for sh in meme.load()["shadows"]}, {"unknown"})

    def test_a_bonding_curve_with_no_liquidity_field_is_not_a_rug_and_is_not_bought(self):
        self.api.pairs[SOL] = FakeAPI.pair(SOL, "CURVE", 1.0, None, dex="pumpfun")
        out = self.run_at(T0)
        self.assertEqual(out["bought"], 0)
        self.assertEqual(len(meme.load()["shadows"]), 2)
        self.api.pairs[SOL] = FakeAPI.pair(SOL, "CURVE", 1.1, None, dex="pumpfun")
        out = self.run_at(T0 + 60)
        self.assertEqual(out["shadows_closed"], 0)

    def test_a_pulled_pool_is_a_total_loss(self):
        self.run_at(T0)
        self.api.pairs[SOL] = FakeAPI.pair(SOL, "POOL1", 0.99, 500.0)
        self.run_at(T0 + 60)
        t = meme.load()["account"]["closed"][0]
        self.assertEqual((t["why"], t["pnl"]), ("rug", -20.0))
        self.assertTrue(all(x["net_pct"] == -100.0 for x in meme._jsonl("shadows.jsonl")))

    def test_while_the_desk_was_off_open_trades_are_replayed_from_candles(self):
        self.run_at(T0)
        later = T0 + 3600
        self.api.pairs[SOL] = FakeAPI.pair(SOL, "POOL1", 0.95, 100000.0, now=later)
        self.api.bars = [[T0 + 300, 1.0, 1.02, 0.97, 1.0, 10], [T0 + 600, 0.84, 0.86, 0.80, 0.82, 10]]
        self.run_at(later)
        t = meme.load()["account"]["closed"][0]
        self.assertEqual((t["why"], t["exit_px"], t["exit_ts"]), ("stop", 0.84, T0 + 600))   # gapped: filled at the open
        self.assertTrue(any("geckoterminal" in u for u in self.api.calls))

    def test_cooldown_and_one_position_per_coin(self):
        self.run_at(T0)
        self.run_at(T0 + 16 * 60)
        s = meme.load()
        self.assertEqual(len(s["account"]["positions"]), 1)
        self.assertEqual(len(s["shadows"]), 2)

    def test_a_new_starting_balance_starts_the_account_over_and_reset_keeps_the_record(self):
        self.run_at(T0)
        S.update({"meme_starting_balance": 250})
        self.run_at(T0 + 60)
        self.assertEqual(meme.load()["account"]["start"], 250)
        meme.reset()
        self.assertEqual(meme.load()["account"]["positions"], [])
        self.assertEqual(len(meme._jsonl("discoveries.jsonl")), 1)

    def test_forward_stats_and_the_pass_bar(self):
        rows = [{"key": "k%d" % i, "policy": "calm_aged", "screen": "pass", "net_pct": v}
                for i, v in enumerate([10.0, -5.0, 2.0, 50.0])]
        fw = meme.forward(rows)
        self.assertEqual((fw["calm_aged+screen"]["n"], fw["calm_aged+screen"]["median"]), (4, 6.0))
        self.assertEqual(fw["calm_aged+screen"]["mean_no_top3"], -5.0)
        v = meme.verdict(fw, "calm_aged")
        self.assertFalse(v[0]["ok"])                    # 4 trades is far from 100

    def test_each_rule_has_its_own_account_and_careful_skips_promoted_coins(self):
        self.run_at(T0)
        books = {b["name"]: b for b in meme.view()["books"]}
        self.assertEqual(books["all"]["open"], 1)
        self.assertEqual(books["calm_aged"]["open"], 1)
        self.assertEqual(books["hype"]["open"], 0)
        self.assertEqual(books["careful"]["open"], 0)            # came from the boost feed: research filter R2
        self.api.pairs[SOL] = FakeAPI.pair(SOL, "POOL1", 1.40, 100000.0)
        self.run_at(T0 + 60)
        books = {b["name"]: b for b in meme.view()["books"]}
        self.assertEqual((books["calm_aged"]["trades"], books["calm_aged"]["wins"]), (1, 1))
        self.assertTrue(books["calm_aged"]["return_pct"] > 0)
        self.assertFalse(meme.all_positive())                   # hype never traded: still at its start, not above

    def test_money_table_line(self):
        self.assertIsNone(meme.money("2026-11-20")["balance"])
        self.run_at(T0)
        m = meme.money("2099-01-01")
        self.assertEqual(m["start"], 100.0)
        self.assertAlmostEqual(m["balance"], m["total"] + 100.0, places=2)

    def test_review_has_a_model_4_section(self):
        self.run_at(T0)
        self.assertTrue(any("Forward record" in l for l in meme.lines()))


class PracticeDrawdown(DeskCase):
    def replay(self, enabled):
        S.save({"meme_policy": "calm_aged", "meme_practice_drawdown": enabled})
        api = FakeAPI()

        def add(addr, pool):
            api.feed.append({"chainId": "solana", "tokenAddress": addr})
            api.pairs[addr] = api.pair(addr, pool, 1.0, 100000.0)

        second, third, fourth = ("Second111111111111111111111111111111",
                                 "Third1111111111111111111111111111111",
                                 "Fourth111111111111111111111111111111")
        add(second, "POOL2")
        meme.run(now=T0, fetch=api)
        meme.run(now=T0 + 150, fetch=api)
        api.pairs[SOL]["liquidity"]["usd"] = 900.0
        add(third, "POOL3")
        meme.run(now=T0 + 300, fetch=api)
        at_halt = meme.load()
        api.pairs[second]["priceUsd"] = "1.31"
        meme.run(now=T0 + 360, fetch=api)
        meme.run(now=T0 + 480, fetch=api)
        add(fourth, "POOL4")
        meme.run(now=T0 + 600, fetch=api)
        final = meme.load()
        logs = {path.name: path.read_bytes() for path in meme.folder().glob("*.jsonl")}
        return at_halt, final, logs, api.calls

    def test_halt_leaves_main_results_shadow_logs_and_data_requests_identical(self):
        before, baseline, baseline_logs, baseline_calls = self.replay(False)
        self.assertNotIn("practice_drawdown", before)
        with tempfile.TemporaryDirectory() as other, mock.patch.dict(os.environ, {"DESK_DATA": other}):
            at_halt, final, logs, calls = self.replay(True)
            risk = final.pop("practice_drawdown")
            status = meme.view()["practice_drawdown"]
            self.assertEqual(status["book"]["halted"], risk["halted"])
            self.assertIn("Main drawdown experiment (15% halt): halted", "\n".join(meme.lines()))
            self.assertEqual(at_halt["practice_drawdown"]["halted"]["equity"], 80.0)
            self.assertEqual(len(at_halt["practice_drawdown"]["positions"]), 1)
            self.assertEqual([p["why"] for p in risk["closed"]], ["rug", "target"])
            self.assertEqual(risk["positions"], [])
            self.assertGreater(risk["cash"], 85.0)
            self.assertEqual(risk["halted"], at_halt["practice_drawdown"]["halted"])
            self.assertEqual(final, baseline)
            self.assertEqual(logs, baseline_logs)
            self.assertEqual(calls, baseline_calls)
            meme.reset()
            self.assertIsNone(meme.view()["practice_drawdown"]["book"])
            self.assertEqual(logs, {path.name: path.read_bytes() for path in meme.folder().glob("*.jsonl")})

    def test_enabling_with_an_existing_main_position_only_copies_new_fills(self):
        S.save({"meme_policy": "calm_aged"})
        api = FakeAPI()
        meme.run(now=T0, fetch=api)
        S.update({"meme_practice_drawdown": True})
        meme.run(now=T0 + 60, fetch=api)
        self.assertEqual(len(meme.load()["account"]["positions"]), 1)
        self.assertEqual(meme.load()["practice_drawdown"]["positions"], [])

    def test_companion_exit_uses_recorded_liquidity_at_its_own_size(self):
        st = S.validate({"meme_practice_drawdown": True})
        entry = {"key": SOL, "chain": "solana", "ts": T0, "px": 1.0, "cost": 30, "liq": 100000}
        for exit_liq in (20000, None, 0):
            with self.subTest(exit_liq=exit_liq):
                s = {}
                meme._practice_drawdown(st, s, [entry], [], {}, T0)
                book = s["practice_drawdown"]
                main = meme._new_account(100)
                main_pos = dict(entry, qty=30)
                main["positions"].append(main_pos)
                meme._sell(st, main, main_pos, 1.3, exit_liq, T0 + 60, "target")
                self.assertEqual(main["closed"][0]["exit_liq"], exit_liq)

                meme._practice_drawdown(st, s, [], main["closed"], {SOL: {"liq": 900}}, T0 + 120)
                close = book["closed"][0]
                expected = 0.0
                if exit_liq != 0:
                    liq = entry["liq"] if exit_liq is None else exit_liq
                    expected = 1.3 * (1 - 0.0175 - close["qty"] * 1.3 / (liq / 2))
                self.assertEqual(close["cost"], 20)
                self.assertEqual(close["exit_liq"], exit_liq)
                self.assertAlmostEqual(close["exit_fill"], expected)

    def test_closed_profit_raises_the_peak_before_the_exact_drawdown_limit(self):
        st = S.validate({"meme_practice_drawdown": True})
        s = {}
        first = {"key": SOL, "chain": "solana", "ts": T0, "px": 1.0, "cost": 20, "liq": 100000}
        meme._practice_drawdown(st, s, [first], [], {}, T0)
        book = s["practice_drawdown"]
        pos = book["positions"][0]
        px = 1.3
        won = dict(first, exit_px=px, exit_ts=T0 + 60, why="target")
        second = dict(first, key="second", ts=T0 + 60)
        meme._practice_drawdown(st, s, [second], [won], {}, T0 + 60)
        self.assertGreater(book["realized_peak"], 100)
        self.assertAlmostEqual(book["closed"][0]["pnl"],
                               pos["qty"] * px * (1 - meme._side_cost(st, pos["qty"] * px, pos["liq"], "solana")) - 20,
                               places=4)

        # A rug at the exact realized limit still halts. Keep the open basis equal to that loss.
        exact_loss = book["realized_peak"] * 0.15
        open_pos = book["positions"][0]
        book["cash"] = book["realized_peak"] - exact_loss
        open_pos["cost"] = exact_loss
        lost = dict(second, exit_px=0, exit_ts=T0 + 120, why="rug")
        meme._practice_drawdown(st, s, [], [lost], {}, T0 + 120)
        self.assertAlmostEqual(book["halted"]["equity"], book["realized_peak"] * 0.85, places=6)


class Route(DemoCase):
    def setUp(self):
        super().setUp()
        self.d = self.desk()
        self.httpd = serve(self.d, None, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def call(self, path, body=None, headers=None):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     data=None if body is None else json.dumps(body).encode(), headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_view_and_reset(self):
        code, v = self.call("/api/meme")
        self.assertEqual((code, v["paper_only"]), (200, True))
        h = {"Content-Type": "application/json", "X-Desk": "1"}
        self.assertEqual(self.call("/api/meme/reset", {"sure": True}, {"Content-Type": "application/json"})[0], 403)
        self.assertEqual(self.call("/api/meme/reset", {}, h)[0], 400)
        self.assertEqual(self.call("/api/meme/reset", {"sure": True}, h)[0], 200)

    def test_reset_returns_a_conflict_if_a_pass_holds_the_lock_for_ten_seconds(self):
        state = {"account": meme._new_account(80), "practice_drawdown": meme._new_account(100)}
        meme._save(state)
        headers = {"Content-Type": "application/json", "X-Desk": "1"}
        with mock.patch.object(meme, "_running") as lock:
            lock.acquire.return_value = False
            code, body = self.call("/api/meme/reset", {"sure": True}, headers)
        lock.acquire.assert_called_once_with(timeout=10)
        lock.release.assert_not_called()
        self.assertEqual(code, 409)
        self.assertEqual(body["error"], "A memecoin pass is running, try again in a minute.")
        self.assertEqual(meme.load(), state)


class Schedule(DemoCase):
    def test_memecoins_run_on_a_weekend_and_never_in_demo(self):
        d = self.desk()
        ran = []
        with mock.patch.object(Scheduler, "_meme", lambda self: ran.append(1)), \
                mock.patch.object(d, "scan", return_value=None), mock.patch.object(d, "premarket"), \
                mock.patch.object(d, "congress"), mock.patch.object(d, "monitor"), \
                mock.patch.object(d.auto, "tick", return_value=None):
            sch = Scheduler(d, log=lambda *a: None)
            sch.tick(wall=1000.0)
            time.sleep(0.05)
            self.assertEqual(ran, [])                                   # the demo never runs it
            d.src.demo = False
            with mock.patch.object(d.src, "now", return_value="2026-11-21 13:00"), \
                    mock.patch.object(d.paper, "settle", return_value=None):  # a Saturday: NYSE shut
                sch.tick(wall=2000.0)
                sch.tick(wall=2030.0)                                   # under a minute later: not again
                time.sleep(0.05)
                self.assertEqual(ran, [1])
                S.update({"meme_enabled": False})
                sch.tick(wall=3000.0)
                time.sleep(0.05)
                self.assertEqual(ran, [1])


PUMP = "Gr4duAteMeme11111111111111111111111111pump"


def _iso_utc(ts):
    import datetime
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _trades(anc, n_buyers, first_px=1.0, creator_buys=0):
    rows = [{"block_timestamp": _iso_utc(anc + 10), "kind": "buy", "tx_from_address": "w0", "price_to_in_usd": str(first_px)}]
    rows += [{"block_timestamp": _iso_utc(anc + 30 + i), "kind": "buy", "tx_from_address": "w%d" % (i + 1),
              "price_to_in_usd": "1.0"} for i in range(n_buyers - 1)]
    rows += [{"block_timestamp": _iso_utc(anc + 40), "kind": "buy", "tx_from_address": "CREATOR",
              "price_to_in_usd": "1.0"}] * creator_buys
    return rows


class MigrationRules(DeskCase):
    def test_anchor_needs_a_graduated_launchpad_mint_on_solana(self):
        pool = {"dexId": "pumpswap", "pairCreatedAt": 1000 * 1000}
        self.assertEqual(M.anchor("solana", PUMP, pool), 1000.0)
        self.assertIsNone(M.anchor("solana", PUMP, dict(pool, dexId="pumpfun")))       # still on the curve
        self.assertIsNone(M.anchor("solana", "So1aNaMeme", pool))                       # not a launchpad mint
        self.assertIsNone(M.anchor("bsc", "0xabcpump", pool))
        self.assertEqual(M.anchor("solana", "XyzBonkMintbonk", dict(pool, dexId="raydium")), 1000.0)

    def test_the_one_decision_window(self):
        self.assertEqual([M.in_window(a, 0) for a in (14 * 60, 15 * 60, 16.9 * 60, 17 * 60)], ["early", "now", "now", "late"])

    def test_flow_counts_distinct_buyers_since_the_anchor_without_the_creator(self):
        rows = _trades(1000, 30, first_px=0.5, creator_buys=3) + [
            {"block_timestamp": _iso_utc(900), "kind": "buy", "tx_from_address": "before", "price_to_in_usd": "9"}]
        n, first, _ = M.flow(rows, 1000, creator="CREATOR")
        self.assertEqual((n, first), (30, 0.5))
        late = [r for r in rows if r["tx_from_address"] != "w0"]           # the list starts 30 s after the anchor
        self.assertEqual(M.flow(late, 1000)[1], 1.0)
        self.assertIsNone(M.flow([dict(r, block_timestamp=_iso_utc(1200)) for r in rows], 1000)[1])  # too far back

    def test_decide(self):
        ok, why = M.decide(60000, 25000, 30, 1.0, 1.1)
        self.assertEqual((ok, why), ({"migration": True, "migration_base": True}, []))
        ok, why = M.decide(60000, 25000, 10, 1.0, 1.5)
        self.assertEqual(ok, {"migration": False, "migration_base": True})
        self.assertEqual(len(why), 2)
        self.assertEqual(M.decide(None, 25000, 30, 1.0, 1.0)[0], {"migration": False, "migration_base": False})
        self.assertFalse(M.decide(60000, 25000, 30, None, 1.0)[0]["migration"])          # missing data is a no

    def test_the_bar_needs_ten_a_slice_and_must_beat_its_control(self):
        rows = [{"key": "c%d" % (i % 60), "policy": "migration", "net_pct": v, "ts": T0 + i * 600}
                for i, v in enumerate([6.0, 2.0, -1.0] * 40)]
        x = meme.stats(rows)
        self.assertEqual(sum(x["folds_n"]), 120)
        self.assertTrue(meme.bar(x, None, {"n": 50, "mean": 0.5}, M.MIN_PER_SLICE))
        self.assertFalse(meme.bar(x, None, {"n": 50, "mean": 9.0}, M.MIN_PER_SLICE))    # the control did better
        self.assertFalse(meme.bar(x, None, None, 40))                                     # 30 a slice, not 40
        fw = {"all": {}, "migration_base": {"n": 50, "mean": 9.0}}
        self.assertFalse(meme._bar_for("migration", x, fw))
        self.assertFalse(meme._bar_for("migration_base", x, {"all": {}}))                # never a candidate


class MigrationRun(DeskCase):
    def setUp(self):
        super().setUp()
        self.api = FakeAPI()
        self.anc = T0 - 16 * 60
        self.api.feed = [{"chainId": "solana", "tokenAddress": PUMP}]
        p = FakeAPI.pair(PUMP, "PSWAP", 1.0, 80000.0, age_h=16 / 60, dex="pumpswap")
        p["pairCreatedAt"] = self.anc * 1000
        self.api.pairs = {PUMP: p}
        self.api.trades = _trades(self.anc, 30, first_px=0.95, creator_buys=2)
        self.api.rug = {"score_normalised": 5, "risks": [], "creator": "CREATOR", "topHolders": [{"owner": "w", "pct": 5}],
                        "markets": [{"pubkey": "PSWAP", "lp": {"lpLockedPct": 100}}], "knownAccounts": {},
                        "token": {"supply": 100}, "creatorBalance": 0}
        self.api.feed = [{"chainId": "solana", "tokenAddress": PUMP}]

    def log(self):
        return meme._jsonl("migrations.jsonl")

    def test_decide_then_fill_after_60s_into_shadows_and_its_own_account(self):
        with mock.patch.object(R, "vetoes", return_value=[]):             # the fake feed is a boost: no R2 here
            meme.run(now=T0, fetch=self.api)
            d = [r for r in self.log() if r["event"] == "decide"][0]
            self.assertEqual((d["buyers"], d["first_px"], sorted(d["yes"])), (30, 0.95, ["migration", "migration_base"]))
            self.assertEqual(len(meme.load()["mig_pending"]), 2)
            self.assertFalse([sh for sh in meme.load()["shadows"] if sh["policy"].startswith("migration")])
            meme.run(now=T0 + 70, fetch=self.api)
        s = meme.load()
        self.assertEqual(sorted(sh["policy"] for sh in s["shadows"] if sh["policy"].startswith("migration")),
                         ["migration", "migration_base"])
        self.assertEqual(s["mig_pending"], [])
        self.assertEqual(len(s["books"]["migration"]["positions"]), 1)
        self.assertEqual(s["books"]["careful"]["positions"], [])
        self.assertEqual([r["event"] for r in self.log()].count("fill"), 2)

    def test_p7_pays_at_least_4_pct(self):
        st = S.load()
        self.assertEqual(meme.cost_pct(st, "solana", "migration"), 4.0)
        self.assertEqual(meme.cost_pct(st, "solana", "calm_aged"), 3.5)
        self.assertEqual(meme.cost_pct(st, "ethereum", "migration"), 6.0)

    def test_a_late_fill_is_cancelled(self):
        with mock.patch.object(R, "vetoes", return_value=[]):
            meme.run(now=T0, fetch=self.api)
            meme.run(now=T0 + 200, fetch=self.api)
        self.assertEqual([r["event"] for r in self.log()].count("cancel"), 2)
        self.assertFalse([sh for sh in meme.load()["shadows"] if sh["policy"].startswith("migration")])

    def test_a_coin_first_seen_after_minute_17_is_logged_as_missed(self):
        self.api.pairs[PUMP]["pairCreatedAt"] = (T0 - 30 * 60) * 1000
        meme.run(now=T0, fetch=self.api)
        self.assertEqual([r["event"] for r in self.log()], ["missed"])

    def test_a_coin_that_graduated_days_ago_is_not_counted(self):
        self.api.pairs[PUMP]["pairCreatedAt"] = (T0 - 3 * 86400) * 1000
        meme.run(now=T0, fetch=self.api)
        self.assertEqual(self.log(), [])
        self.assertTrue(meme.load()["tracked"]["solana:" + PUMP]["mig_done"])

    def test_one_decision_per_mint_and_unknown_safety_never_buys(self):
        self.api.rug = None
        with mock.patch.object(R, "vetoes", return_value=[]):
            meme.run(now=T0, fetch=self.api)
            meme.run(now=T0 + 60, fetch=self.api)
        d = [r for r in self.log() if r["event"] == "decide"]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["yes"], [])
        self.assertIn("strict check unknown", d[0]["why"])

    def test_a_busy_geckoterminal_is_retried_inside_the_window_not_decided_on_missing_data(self):
        real = self.api.__call__
        calls = {"n": 0}

        def flaky(url):
            if "geckoterminal" in url and "/trades" in url and calls["n"] == 0:
                calls["n"] += 1
                raise meme.MemeError("api.geckoterminal.com is busy; skipped for now")
            return real(url)
        anc = T0 - 15 * 60 - 10                                              # minute 15.2 at T0
        self.api.pairs[PUMP]["pairCreatedAt"] = anc * 1000
        self.api.trades = _trades(anc, 30, first_px=0.95, creator_buys=2)
        with mock.patch.object(R, "vetoes", return_value=[]):
            meme.run(now=T0, fetch=flaky)                                    # minute 15.2: busy, retry
            self.assertEqual([r["event"] for r in self.log()], ["retry"])
            self.assertFalse(meme.load()["tracked"]["solana:" + PUMP].get("mig_done"))
            meme.run(now=T0 + 30, fetch=flaky)                               # minute 15.7: decided with data
        d = [r for r in self.log() if r["event"] == "decide"]
        self.assertEqual((len(d), d[0]["buyers"], d[0]["tries"]), (1, 30, 2))
        self.assertEqual(meme.migration_funnel()["retries"], 1)

    def test_still_no_data_at_minute_17_is_missed_with_the_reason(self):
        def busy(url):
            if "geckoterminal" in url:
                raise meme.MemeError("api.geckoterminal.com is busy; skipped for now")
            return self.api(url)
        self.api.pairs[PUMP]["pairCreatedAt"] = (T0 - 15 * 60 - 10) * 1000   # minute 15.2 at T0
        meme.run(now=T0, fetch=busy)                                         # minute 15.2: retry
        meme.run(now=T0 + 60, fetch=busy)                                    # minute 16.2: no time left, missed
        ev = [(r["event"], r.get("why", "")) for r in self.log()]
        self.assertEqual(ev[0][0], "retry")
        self.assertEqual(ev[-1][0], "missed")
        self.assertIn("unavailable in the window after 2 tries", ev[-1][1])
        self.assertEqual([e for e, _ in ev].count("decide"), 0)              # never decided on missing data

    def test_a_429_is_logged(self):
        import io
        err = urllib.error.HTTPError("u", 429, "slow down", {}, io.BytesIO(b""))
        with mock.patch.object(meme, "get", meme.__dict__["_real_get"]), \
                mock.patch("urllib.request.urlopen", side_effect=err):
            meme._cool.clear()
            with self.assertRaises(meme.MemeError):
                meme.get("https://api.geckoterminal.com/api/v2/networks/solana/pools/X/trades")
            meme._cool.clear()
        rows = meme._jsonl("api_errors.jsonl")
        self.assertEqual((rows[0]["code"], rows[0]["path"]), (429, "/api/v2/networks/solana/pools/X/trades"))

    def test_the_paid_promotion_veto_still_applies(self):
        meme.run(now=T0, fetch=self.api)                                   # found on the boost feed: R2
        d = [r for r in self.log() if r["event"] == "decide"][0]
        self.assertEqual(d["yes"], [])
        self.assertIn("R2 paid promotion", d["why"])


MOON = "So1aNaMoon4444444444444444444444444444pump"


def _moon_pair(price, liq=60000.0, now=T0, **pc):
    p = FakeAPI.pair(MOON, "MPOOL", price, liq, age_h=6.0, now=now, dex="raydium", **pc)
    p["priceChange"].update({"m5": 4.0, "h1": 60.0})
    p["priceChange"].update(pc)
    p["volume"].update({"m5": 15000.0, "h1": 80000.0})
    p["txns"]["m5"] = {"buys": 70, "sells": 30}
    return p


class MoonRules(DeskCase):
    def test_decide(self):
        f = R.features_from_pair(_moon_pair(1.0), T0 * 1000)
        self.assertEqual(MN.decide(f, 60000.0), {"moonshot": True, "moonshot_base": True})
        self.assertEqual(MN.decide(dict(f, r1h=5.0), 60000.0), {"moonshot": False, "moonshot_base": True})
        self.assertFalse(MN.decide(dict(f, r1h=400.0), 60000.0)["moonshot"])        # already flown
        self.assertFalse(MN.decide(dict(f, age_h=0.5), 60000.0)["moonshot_base"])   # under an hour
        self.assertFalse(MN.decide(f, None)["moonshot_base"])                        # no pool figure: no

    def test_trailing_ladder(self):
        self.assertAlmostEqual(MN.stop_level(1.0, 1.2), 0.75)                 # under +40%: the -25% stop
        self.assertAlmostEqual(MN.stop_level(1.0, 1.6), 1.2)                  # +60% peak: 25% below it
        self.assertAlmostEqual(MN.stop_level(1.0, 4.0), 2.8)                  # +300% peak: 30% below it
        self.assertAlmostEqual(MN.stop_level(1.0, 2.5), 1.875)               # at +150% it keeps the 25% step's level
        self.assertAlmostEqual(MN.stop_level(1.0, 2.6), 1.875)               # 30% under 2.6 is 1.82: never lower
        levels = [MN.stop_level(1.0, 1.0 + i / 100) for i in range(0, 600)]
        self.assertTrue(all(b >= a - 1e-12 for a, b in zip(levels, levels[1:])))   # never falls as the peak rises
        self.assertEqual(MN.exit_sample(1.0, 0, 4.0, 2.7, 1e5, 60)[1], "trail")
        self.assertEqual(MN.exit_sample(1.0, 0, 1.1, 0.74, 1e5, 60)[1], "stop")
        self.assertIsNone(MN.exit_sample(1.0, 0, 4.0, 3.0, 1e5, 60))
        self.assertEqual(MN.exit_sample(1.0, 0, 1.0, 1.1, 1e5, MN.HOLD_MIN * 60)[1], "time")
        self.assertEqual(MN.exit_sample(1.0, 0, 1.0, 1.1, 500, 60), (0.0, "rug"))

    def test_replay_lets_a_winner_run_and_checks_the_stop_before_the_new_high(self):
        bars = [[300, 1.0, 2.0, 0.95, 1.9, 1], [600, 1.9, 5.0, 1.8, 4.8, 1], [900, 4.8, 4.9, 3.0, 3.2, 1]]
        r, peak = MN.replay(0, 1.0, 1.0, bars)
        self.assertEqual((r["why"], r["price"], peak), ("trail", 3.5, 5.0))   # 30% under the 5.0 peak, a +250% exit
        both = [[300, 1.0, 3.0, 0.70, 2.0, 1]]                                # the low breaks -25% in the same bar
        self.assertEqual(MN.replay(0, 1.0, 1.0, both)[0]["why"], "stop")


class MoonRun(DeskCase):
    def setUp(self):
        super().setUp()
        self.api = FakeAPI()
        self.api.feed = [{"chainId": "solana", "tokenAddress": MOON}]
        self.api.pairs = {MOON: _moon_pair(1.0)}

    def go(self, *times):
        with mock.patch.object(R, "vetoes", return_value=[]):
            for t in times:
                meme.run(now=t, fetch=self.api)

    def test_fires_fills_60s_later_and_rides_past_30_pct(self):
        self.go(T0)
        self.assertEqual(sorted(pe["policy"] for pe in meme.load()["mig_pending"]), ["moonshot", "moonshot_base"])
        self.go(T0 + 70)
        s = meme.load()
        pos = s["books"]["moonshot"]["positions"]
        self.assertEqual(len(pos), 1)
        self.assertAlmostEqual(pos[0]["cost"], 5.0)                            # 5% of $100
        for i, px in enumerate([1.5, 3.0, 5.0], start=2):                       # +400%: no target sells it
            self.api.pairs = {MOON: _moon_pair(px, now=T0 + i * 60)}
            self.go(T0 + i * 60)
        self.assertEqual(len(meme.load()["books"]["moonshot"]["positions"]), 1)
        self.api.pairs = {MOON: _moon_pair(3.4, now=T0 + 300)}                 # 32% under the 5.0 peak
        self.go(T0 + 300)
        c = meme.load()["books"]["moonshot"]["closed"][0]
        self.assertEqual(c["why"], "trail")
        self.assertGreater(c["ret_pct"], 200)
        sh = [x for x in meme._jsonl("shadows.jsonl") if x["policy"] == "moonshot"][0]
        self.assertAlmostEqual(sh["net_pct"], R.net_pct(1.0, 3.4, 4.0), places=2)   # 4% floor
        mo = meme.view()["moonshots"]
        self.assertEqual((mo["fired"], mo["coins"]), (2, 1))                  # T0 and T0+300: two 5-minute decisions

    def test_p8b_only_decision_logs_its_pilot_without_changing_p8_funnel(self):
        self.api.pairs = {MOON: _moon_pair(1.0, h1=10.0)}
        self.go(T0, T0 + 70)
        line = next(r for r in meme._jsonl("moonshots.jsonl") if r["event"] == "decide")
        self.assertEqual(line["yes"], ["moonshot_base"])
        self.assertFalse(line["momentum"])
        self.assertEqual(line["pilot"]["distinct_buyers_15m"], "unknown")
        shadows = [s for s in meme.load()["shadows"] if s["policy"] in MN.POLICIES]
        self.assertEqual([(s["policy"], s["pilot"]) for s in shadows], [("moonshot_base", line["pilot"])])
        self.assertEqual(meme.moonshot_funnel()["fired"], 0)

    def test_spare_prefetch_uses_provider_inputs_and_copies_them_to_both_shadows(self):
        self.api.rug.update(creator="creator", markets=[{"pubkey": "MPOOL",
                            "lp": {"lpMint": "lpA", "lpTotalSupply": 1000, "lpLockedPct": 90}}],
                            lockers={"locker": {"uri": "https://raydium.io/?pool_id=MPOOL", "unlockDate": T0 + 1000}},
                            topHolders=[{"owner": "large" if i == 0 else "owner%d" % i, "pct": 2}
                                        for i in range(10)])
        self.api.pairs = {MOON: _moon_pair(1.0)}
        self.go(T0)
        self.go(T0 + 151)  # cancel the first delayed entries after the existing fill window
        as_of = T0 + 240

        def trade(ts, maker, buy, dollars):
            return {"block_timestamp": meme._iso(ts).replace(" ", "T") + "Z", "tx_from_address": maker,
                    "from_token_address": "QUOTE" if buy else MOON, "to_token_address": MOON if buy else "QUOTE",
                    "volume_in_usd": str(dollars)}

        self.api.trades = [trade(as_of - 3650, "other", True, 5), trade(as_of - 1100, "creator", False, 500),
                           trade(as_of - 100, "large", False, 200), trade(as_of - 95, "creator", True, 100),
                           trade(as_of - 20, "buyer", True, 300)]
        with mock.patch.object(meme.threading, "Thread") as thread:
            thread.side_effect = lambda target, **_: mock.Mock(start=target)
            self.go(as_of)
        self.assertEqual(len([u for u in self.api.calls if "/trades" in u]), 1)
        self.go(T0 + 300, T0 + 370)
        line = [r for r in meme._jsonl("moonshots.jsonl") if r["event"] == "decide"][-1]
        pilot = line["pilot"]
        self.assertEqual(pilot["withdrawable_liquidity_usd"], 6000)
        self.assertEqual(pilot["lp_unlock_ts"], T0 + 1000)
        self.assertEqual(pilot["creator_net_sell_usd_1h"], 400)
        self.assertEqual(pilot["top10_net_sell_usd_1h"], 200)
        self.assertEqual((pilot["distinct_buyers_15m"], pilot["largest_buyer_volume_share_15m"]), (2, .75))
        self.assertEqual(pilot["trade_as_of"], as_of)
        self.assertEqual(line["yes"], ["moonshot", "moonshot_base"])
        self.assertEqual([s["pilot"] for s in meme.load()["shadows"] if s["policy"] in MN.POLICIES], [pilot, pilot])
        self.assertAlmostEqual(meme.load()["books"]["moonshot"]["positions"][0]["cost"], 5)

    def test_pilot_skips_reserve_and_reports_incomplete_windows_unknown(self):
        with mock.patch.object(meme.threading, "Thread") as thread:
            meme._prefetch_pilot([(1, ("root", "solana", "MPOOL", MOON))], [meme.MIG_RESERVE], T0, self.api)
            thread.assert_not_called()
        self.api.trades = [{"block_timestamp": T0 - 20, "tx_from_address": "buyer",
                            "from_token_address": "QUOTE", "to_token_address": MOON, "volume_in_usd": "100"}]
        page = MP.trade_page("solana", MOON, self.api("https://api.geckoterminal.com/pools/MPOOL/trades"), T0)
        self.assertIsNotNone(page)
        self.assertEqual(MP.diagnostics("solana", "MPOOL", 100000, {}, page, T0)["distinct_buyers_15m"], "unknown")
        self.assertEqual(MP.diagnostics("solana", "MPOOL", 100000, {}, page, T0 + 121)["trade_as_of"], "unknown")

    def test_goplus_matched_v2_lp_and_unlock(self):
        pool, owner = "0xpool", "0xowner"
        holders = [{"address": owner, "percent": "0.2", "is_locked": "0"},
                   {"address": "0xlock", "percent": "0.8", "is_locked": "1",
                    "locked_detail": [{"end_time": str(int(T0 + 3600))}]}]
        self.api.goplus = {BSC: {"is_honeypot": "0", "buy_tax": "0", "sell_tax": "0", "cannot_sell_all": "0",
                                "is_open_source": "1", "dex": [{"pair": pool, "liquidity_type": "UniV2"}],
                                "lp_holders": holders}}
        report = meme.screen("bsc", BSC, self.api, pool)[2]
        pilot = MP.diagnostics("bsc", pool, 50000, {"report": report, "ts": T0}, None, T0)
        self.assertEqual((pilot["withdrawable_liquidity_usd"], pilot["lp_unlock_ts"]), (10000, T0 + 3600))
        self.assertEqual(MP.diagnostics("bsc", "0xother", 50000, {"report": report}, None, T0)["lp_unlock_ts"], "unknown")

    def test_unknown_safety_blocks_and_is_logged(self):
        self.api.rug = None
        self.go(T0, T0 + 70)
        self.assertEqual(meme.load()["books"]["moonshot"]["positions"], [])
        d = meme._jsonl("moonshots.jsonl")[0]
        self.assertEqual((d["yes"], d["why"]), ([], ["safety check unknown"]))

    def test_decides_every_5_minutes_not_every_run(self):
        self.go(T0, T0 + 60, T0 + 120)
        self.assertEqual(len([r for r in meme._jsonl("moonshots.jsonl") if r["event"] == "decide"]), 1)
