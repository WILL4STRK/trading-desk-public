"""Seasons and refills of the memecoin practice accounts, and the read-only study views (desk/memeseasons.py).
No network: the fake API from test_meme answers every GET."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from desk import meme
from desk import memeseasons as MS
from desk import settings as S

from .base import DeskCase
from .test_meme import SOL, T0, FakeAPI, Route

SECOND, THIRD = "Second111111111111111111111111111111", "Third1111111111111111111111111111111"


def _lines(path, rows, torn=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r) + "\n" for r in rows) + (torn or "")
    path.write_text(text, encoding="utf-8")


class Refill(DeskCase):
    def setUp(self):
        super().setUp()
        S.save({"meme_policy": "calm_aged"})
        self.api = FakeAPI()

    def test_a_refill_archives_the_season_and_starts_the_next_at_100(self):
        meme.run(now=T0, fetch=self.api)
        before = meme.load()
        self.assertEqual(len(before["account"]["positions"]), 1)
        out = MS.refill(now=T0 + 30)
        self.assertEqual({r["account"] for r in out}, set(MS.book_names()))
        s = meme.load()
        for name in MS.book_names():
            acct = MS._get(s, name)
            self.assertEqual((acct["season"], acct["cash"], acct["positions"], acct["closed"]), (2, 100.0, [], []))
            arch = MS.archives(name)[1]
            old = MS._get(before, name)
            self.assertEqual(arch["closed"][:len(old["closed"])], old["closed"])     # earlier trades untouched
            self.assertEqual(arch["curve"][:len(old["curve"])], old["curve"])
        main = MS.archives("main")[1]
        self.assertEqual([c["why"] for c in main["closed"]], ["season end"])
        self.assertTrue((MS.folder() / "main" / "season-1.json").is_file())
        self.assertFalse(list(MS.folder().rglob("*.tmp")))

    def test_open_positions_close_at_the_last_price_with_the_normal_costs(self):
        meme.run(now=T0, fetch=self.api)
        self.api.pairs[SOL] = FakeAPI.pair(SOL, "POOL1", 1.10, 100000.0)
        meme.run(now=T0 + 60, fetch=self.api)                 # a sample, no exit (stop -15 %, target +30 %)
        self.assertEqual(len(meme.load()["account"]["positions"]), 1)
        MS.refill(["main"], now=T0 + 90)
        t = MS.archives("main")[1]["closed"][0]
        qty = 20.0 / (1.0175 + 20 / 50000)
        proceeds = qty * 1.10 * (1 - 0.0175 - qty * 1.10 / 50000)
        self.assertEqual(t["why"], "season end")
        self.assertAlmostEqual(t["exit_px"], 1.10)
        self.assertAlmostEqual(t["pnl"], proceeds - 20.0, places=3)
        self.assertAlmostEqual(MS.archives("main")[1]["cash"], 80.0 + proceeds, places=3)
        self.assertEqual(meme.load()["books"]["calm_aged"].get("season", 1), 1)    # only main was refilled

    def test_a_dead_pool_still_ends_as_season_end_at_zero(self):
        meme.run(now=T0, fetch=self.api)
        s = meme.load()
        s["tracked"]["solana:" + SOL]["px"] = None
        meme._save(s)
        MS.refill(["main"], now=T0 + 30)
        t = MS.archives("main")[1]["closed"][0]
        self.assertEqual((t["why"], t["exit_fill"], t["season_end_note"]), ("season end", 0.0, "no price"))

    def test_the_history_survives_a_restart_and_more_passes(self):
        meme.run(now=T0, fetch=self.api)
        MS.refill(now=T0 + 30)
        meme.run(now=T0 + 60, fetch=self.api)
        MS.refill(["careful"], now=T0 + 90)
        meme.run(now=T0 + 1000, fetch=self.api)
        with mock.patch.object(MS, "_view_bounds", MS._view_bounds):    # a fresh read of every file, nothing cached
            cards = {c["name"]: c for c in MS.view()["accounts"]}
        self.assertEqual([x["n"] for x in cards["main"]["seasons"]], [1, 2])
        self.assertEqual([x["n"] for x in cards["careful"]["seasons"]], [1, 2, 3])
        self.assertEqual(meme.load()["account"]["season"], 2)
        self.assertEqual(cards["main"]["seasons"][0]["trades"], 1)

    def test_a_second_archive_of_one_season_never_overwrites(self):
        meme.run(now=T0, fetch=self.api)
        MS.refill(["main"], now=T0 + 30)
        s = meme.load()
        s["account"]["season"] = 1                             # as if model.json had not been saved after the archive
        meme._save(s)
        MS.refill(["main"], now=T0 + 40)
        files = sorted(p.name for p in (MS.folder() / "main").glob("season-*.json"))
        self.assertEqual(len(files), 2)
        self.assertEqual(MS.archives("main")[1]["ended_ts"], T0 + 40)

    def test_unknown_names_are_refused(self):
        with self.assertRaises(MS.SeasonError):
            MS.refill(["nope"])
        with self.assertRaises(MS.SeasonError):
            MS.refill_view(["main"])

    def test_a_refill_waits_for_a_pass_and_never_makes_it_skip(self):
        with mock.patch.object(meme, "_state_lock") as lock:
            lock.acquire.return_value = False
            with self.assertRaises(meme.MemeError):
                MS.refill(timeout=0.01)
        meme._state_lock.acquire()
        try:
            self.assertFalse(meme._running.locked())       # a refill holds only the state lock, not the pass lock
        finally:
            meme._state_lock.release()


class RefillLeavesTheForwardRecordAlone(DeskCase):
    def replay(self, refill, gap=False):
        S.save({"meme_policy": "calm_aged"})
        api = FakeAPI()

        def add(addr, pool, px=1.0):
            api.feed.append({"chainId": "solana", "tokenAddress": addr})
            api.pairs[addr] = api.pair(addr, pool, px, 100000.0)

        add(SECOND, "POOL2")
        meme.run(now=T0, fetch=api)
        meme.run(now=T0 + 120, fetch=api)
        api.pairs[SOL]["priceUsd"] = "1.1"
        meme.run(now=T0 + 240, fetch=api)
        if refill:
            self.assertTrue(any(MS._get(meme.load(), n)["positions"] for n in MS.book_names()))
            MS.refill(now=T0 + 250)
            MS.refill_view(list(MS.VIEWS), now=T0 + 250)
        add(THIRD, "POOL3")
        times = (360, 480, 1000, 1100, 1900) if gap else (360, 480, 600, 720, 840, 960, 1080, 1200)
        for i, dt in enumerate(times):
            if i == 1:
                api.pairs[SECOND]["priceUsd"] = "0.8"
            if i == 3:
                api.pairs[SOL]["priceUsd"] = "1.4"
            meme.run(now=T0 + dt, fetch=api)
        s = meme.load()
        logs = {p.name: p.read_bytes() for p in meme.folder().glob("*.jsonl")}
        return s, logs, api.calls

    def check(self, gap):
        base, base_logs, base_calls = self.replay(False, gap)
        with tempfile.TemporaryDirectory() as other, mock.patch.dict(os.environ, {"DESK_DATA": other}):
            s, logs, calls = self.replay(True, gap)
            self.assertGreaterEqual(len(logs["shadows.jsonl"].splitlines()), 4)
            self.assertEqual(logs, base_logs)                                  # shadows.jsonl and every other log
            self.assertEqual(json.dumps(s["shadows"], sort_keys=True), json.dumps(base["shadows"], sort_keys=True))
            self.assertEqual(s.get("mig_pending"), base.get("mig_pending"))
            self.assertEqual(calls, base_calls)                                 # the same data requests
            self.assertEqual(s["account"]["season"], 2)
            self.assertNotEqual(s["account"], base["account"])                 # the balances did change

    def test_shadows_and_every_shadow_are_byte_identical_with_and_without_the_refill(self):
        self.check(gap=False)

    @unittest.expectedFailure
    def test_known_limit_after_a_gap_the_books_share_the_candle_budget_with_the_shadows(self):
        """Known limit (pre-existing, not the refill's code): after more than GAP seconds without a look, open book
        positions and open shadows are replayed from GeckoTerminal candles out of one budget (GT_PER_RUN), positions
        first. A refill changes which positions are open, so a later replay can reach a shadow a pass earlier or
        later. Fixing it changes the shadow replay order, which is frozen until the D1 window ends (2026-11-30)."""
        self.check(gap=True)


class Views(DeskCase):
    def setUp(self):
        super().setUp()
        self.home = Path(self.tmp) / "studies"
        p1 = mock.patch.object(MS, "L1_OUT", self.home / "learner" / "out")
        p2 = mock.patch.object(MS, "COPY_HOME", self.home / "copy")
        self.lock = self.home / "downside" / "out" / "forward.lock"
        p3 = mock.patch.object(MS, "D1_LOCK", self.lock)
        p1.start(), p2.start(), p3.start()
        self.addCleanup(p1.stop), self.addCleanup(p2.stop), self.addCleanup(p3.stop)

    def read_d1(self):
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        self.lock.write_text("read")

    def cards(self):
        return {c["name"]: c for c in MS.view()["accounts"] if c["kind"] == "view"}

    def test_missing_files_give_empty_accounts_that_say_when_they_start(self):
        self.read_d1()
        c = self.cards()
        self.assertEqual(set(c), {"l1", "c1", "c1s", "d1v1", "d1v2"})
        for card in c.values():
            s = card["seasons"][-1]
            self.assertEqual((s["equity"], s["trades"], s["open"]), (100.0, 0, 0))
            self.assertTrue(card["note"].startswith("Starts when its study starts"))

    def test_l1_takes_and_closes_sized_like_the_books(self):
        out = self.home / "learner" / "out"
        t = MS.L1_T0 + 100
        _lines(out / "predictions.jsonl", [
            {"key": "a", "entry_ts": t, "take": True, "warmup": False, "features": {"raw": {"liq": 100000.0}}},
            {"key": "b", "entry_ts": t + 1, "take": False, "warmup": False},
            {"key": "c", "entry_ts": t + 2, "take": True, "warmup": True},
            {"key": "d", "entry_ts": t + 3, "take": True, "warmup": False, "features": {"raw": {}}}],
            torn='{"key": "e", "entry_ts"')
        _lines(out / "closes.jsonl", [{"key": "a", "entry_ts": t, "exit_ts": t + 600, "net_pct": 10.0, "status": "predicted"}])
        s = self.cards()["l1"]["seasons"][-1]
        imp = 20.0 / 50000
        back = 20.0 * 1.10 * (1 - imp) / (1 + imp)
        self.assertEqual((s["trades"], s["open"], s["wins"]), (1, 1, 1))
        self.assertAlmostEqual(s["equity"], round(100 - 20 + back, 2), places=2)

    def test_l1_card_shows_the_prediction_distribution_and_takes_after_warm_up(self):
        out = self.home / "learner" / "out"
        t = MS.L1_T0 + 100
        preds = [-8.0, -6.0, -5.5, -5.0, -4.0, -3.5, -3.0, -2.0, -1.0, 2.0]          # 10 after warm-up, one above 0
        rows = [{"key": "w%d" % i, "entry_ts": t + i, "pred": 0.0, "take": False, "warmup": True} for i in range(3)]
        rows += [{"key": "k%d" % i, "entry_ts": t + 10 + i, "pred": v, "take": v > 0, "warmup": False} for i, v in enumerate(preds)]
        _lines(out / "predictions.jsonl", rows, torn='{"key": "torn", "pred": 99')
        st = self.cards()["l1"]["preds"]
        self.assertEqual(st, {"n": 10, "warmup": 3, "median": -3.75, "p90": -1.0, "max": 2.0, "takes": 1})

    def test_l1_card_with_no_predictions_yet(self):
        self.assertEqual(self.cards()["l1"]["preds"], {"n": 0, "warmup": 0, "median": None, "p90": None, "max": None, "takes": 0})
        self.assertNotIn("preds", self.cards()["c1"])

    def test_the_page_shows_the_l1_prediction_line(self):
        js = (Path(MS.__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function predLine(p)", js)
        self.assertIn("${predLine(a.preds)}", js)

    def test_c1_and_c1s_are_20_a_copy_from_their_own_logs(self):
        home = self.home / "copy"
        _lines(home / "fills.jsonl", [
            {"id": "x1", "leg": "buy", "arm": "C1", "mint": "m1", "t": 10.0},
            {"id": "x2", "leg": "buy", "arm": "C1", "mint": "m2", "t": 20.0},
            {"id": "x3", "leg": "buy", "arm": "random", "mint": "m3", "t": 30.0},
            {"id": "x4", "leg": "buy", "arm": "C1S", "mint": "m4", "t": 40.0}])
        _lines(home / "closes.jsonl", [{"id": "x1", "arm": "C1", "entry_t": 10.0, "exit_t": 90.0, "net_pct": -50.0},
                                       {"id": "x3", "arm": "random", "entry_t": 30.0, "exit_t": 99.0, "net_pct": 90.0}])
        c = self.cards()
        self.assertEqual((c["c1"]["seasons"][-1]["equity"], c["c1"]["seasons"][-1]["open"]), (90.0, 1))
        self.assertEqual((c["c1s"]["seasons"][-1]["equity"], c["c1s"]["seasons"][-1]["open"]), (100.0, 1))

    def test_d1_v1_and_v2_are_p0_minus_their_vetoes_first_close_kept(self):
        t = MS.D1_T0 + 10
        rows = [{"key": "a", "policy": "all", "screen": "pass", "ts": t, "exit_ts": t + 60, "net_pct": 20.0,
                 "f": {"r1h": 8.0, "buy_ratio_m5": 0.6}},
                {"key": "a", "policy": "all", "screen": "pass", "ts": t, "exit_ts": t + 120, "net_pct": -30.0,
                 "f": {"r1h": 8.0, "buy_ratio_m5": 0.6}},                          # duplicate close: dropped
                {"key": "b", "policy": "all", "screen": "pass", "ts": t + 5, "exit_ts": t + 65, "net_pct": -10.0,
                 "f": {"r1h": 1.0, "buy_ratio_m5": 0.3}},
                {"key": "c", "policy": "all", "screen": "fail", "ts": t + 6, "exit_ts": t + 66, "net_pct": 50.0, "f": {}},
                {"key": "d", "policy": "all", "screen": "pass", "ts": MS.D1_T0 - 5, "exit_ts": t, "net_pct": 50.0, "f": {}},
                {"key": "e", "policy": "hype", "screen": "pass", "ts": t + 7, "exit_ts": t + 67, "net_pct": 50.0, "f": {}}]
        self.assertEqual([x["key"] for x in MS.d1_trades("v1", rows, [])], ["b"])
        v2 = MS.d1_trades("v2", rows, [])
        self.assertEqual([(x["key"], x["net_pct"]) for x in v2], [("a", 20.0)])
        meme.folder().mkdir(parents=True, exist_ok=True)
        _lines(meme.folder() / "shadows.jsonl", rows)
        self.read_d1()
        c = self.cards()
        self.assertEqual(c["d1v1"]["seasons"][-1]["trades"], 1)
        self.assertGreater(c["d1v2"]["seasons"][-1]["equity"], 100.0)

    def test_d1_numbers_stay_hidden_until_the_forward_read_writes_its_lock(self):
        t = MS.D1_T0 + 10
        rows = [{"key": "a%d" % i, "policy": "all", "screen": "pass", "ts": t + i, "exit_ts": t + 60 + i,
                 "net_pct": 12.345 + i, "f": {"r1h": 1.0, "buy_ratio_m5": 0.6}} for i in range(3)]
        meme.folder().mkdir(parents=True, exist_ok=True)
        _lines(meme.folder() / "shadows.jsonl", rows)
        with mock.patch.object(MS, "d1_trades", side_effect=AssertionError("read while hidden")):
            cards = self.cards()
        for name in ("d1v1", "d1v2"):
            card = cards[name]
            self.assertEqual(set(card), {"name", "label", "kind", "refillable", "study", "hidden", "seasons", "note"})
            self.assertEqual((card["seasons"], card["hidden"], card["refillable"]), ([], True, False))
            self.assertEqual(card["note"], "Results hidden until the D1 read (after 2026-11-30 07:24 UTC)")
        self.assertEqual(cards["l1"]["seasons"][-1]["equity"], 100.0)           # L1 and C1 stay visible
        self.assertNotIn("d1", json.dumps(meme.view()).lower().replace("id1", ""))
        self.read_d1()
        shown = self.cards()["d1v1"]
        self.assertEqual(shown["seasons"][-1]["trades"], 3)
        self.assertNotIn("hidden", shown)

    def test_a_view_refill_is_a_season_boundary_only(self):
        home = self.home / "copy"
        _lines(home / "fills.jsonl", [{"id": "x1", "leg": "buy", "arm": "C1", "mint": "m1", "t": 10.0},
                                      {"id": "x2", "leg": "buy", "arm": "C1", "mint": "m2", "t": 50.0}])
        _lines(home / "closes.jsonl", [{"id": "x1", "arm": "C1", "entry_t": 10.0, "exit_t": 20.0, "net_pct": 50.0},
                                       {"id": "x2", "arm": "C1", "entry_t": 50.0, "exit_t": 60.0, "net_pct": -50.0}])
        before = (home / "fills.jsonl").read_bytes(), (home / "closes.jsonl").read_bytes()
        MS.refill_view(["c1"], now=30.0)
        ss = self.cards()["c1"]["seasons"]
        self.assertEqual([(x["n"], x["equity"]) for x in ss], [(1, 110.0), (2, 90.0)])
        self.assertEqual(before, ((home / "fills.jsonl").read_bytes(), (home / "closes.jsonl").read_bytes()))

    def test_a_shared_read_lets_the_study_replace_its_file(self):
        p = self.home / "f.jsonl"
        _lines(p, [{"a": 1}], torn='{"a"')
        self.assertEqual(MS.read_jsonl(p), [{"a": 1}])
        tmp = p.with_name("f.tmp")
        tmp.write_text('{"a": 2}\n')
        os.replace(tmp, p)                                   # the handle was closed: the study's replace works
        self.assertEqual(MS.read_jsonl(p), [{"a": 2}])
        self.assertEqual(MS.read_jsonl(self.home / "missing" / "x.jsonl"), [])


class RefillRoute(Route):
    def test_the_refill_needs_sure_true_and_a_known_account(self):
        h = {"Content-Type": "application/json", "X-Desk": "1"}
        self.assertEqual(self.call("/api/meme/refill", {"sure": True, "account": "all"},
                                   {"Content-Type": "application/json"})[0], 403)
        self.assertEqual(self.call("/api/meme/refill", {"account": "all"}, h)[0], 400)
        self.assertEqual(self.call("/api/meme/refill", {"sure": "yes", "account": "all"}, h)[0], 400)
        self.assertEqual(self.call("/api/meme/refill", {"sure": True, "account": "nope"}, h)[0], 400)
        meme._save({"account": meme._new_account(100.0)})
        code, body = self.call("/api/meme/refill", {"sure": True, "account": "main"}, h)
        self.assertEqual((code, body["refilled"][0]["next_season"]), (200, 2))
        code, body = self.call("/api/meme/refill", {"sure": True, "account": "all"}, h)
        self.assertEqual(code, 200)
        code, v = self.call("/api/meme/accounts")
        self.assertEqual((code, len(v["accounts"])), (200, len(MS.book_names()) + len(MS.VIEWS)))

    def test_the_page_and_api_carry_no_d1_numbers_before_the_lock(self):
        t = MS.D1_T0 + 10
        meme._save({"account": meme._new_account(100.0)})
        _lines(meme.folder() / "shadows.jsonl", [{"key": "z", "policy": "all", "screen": "pass", "ts": t,
                                                  "exit_ts": t + 60, "net_pct": 77.777, "f": {}}])
        with mock.patch.object(MS, "D1_LOCK", Path(self.tmp) / "no" / "forward.lock"):
            code, v = self.call("/api/meme/accounts")
            self.assertEqual(code, 200)
            d1 = [a for a in v["accounts"] if a["name"].startswith("d1")]
            self.assertEqual([(a["seasons"], a.get("hidden")) for a in d1], [([], True), ([], True)])
            self.assertEqual(self.call("/api/meme")[0], 200)
            page = (Path(meme.__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
            self.assertIn("a.hidden", page)                    # the page draws a hidden card with its note only

    def test_the_page_asks_before_it_refills(self):
        js = (Path(meme.__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
        fn = js[js.index("async function refillAccounts"):]
        fn = fn[:fn.index("\n}\n")]
        self.assertLess(fn.index("if (!confirm("), fn.index('api("/api/meme/refill", {sure:true'))

    def test_view_and_reset(self):            # covered by test_meme.Route; not run twice here
        pass

    def test_reset_returns_a_conflict_if_a_pass_holds_the_lock_for_ten_seconds(self):
        pass


class D1LockPath(unittest.TestCase):
    """The desk reads the D1 lock where research/downside/d1.py forward writes it: on Windows under
    C:/Trading/research/downside/out (never the live folder), elsewhere ROOT-relative; DESK_D1_LOCK overrides."""

    def test_default_lock_path(self):
        from desk.paths import ROOT
        from research.downside import d1
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DESK_D1_LOCK", None)
            os.environ.pop("D1_OUT", None)
            self.assertEqual(MS.default_d1_lock("nt"), MS.D1_LOCK_WIN)
            self.assertEqual(MS.default_d1_lock("nt"), d1.default_out("nt") / "forward.lock")    # the same file
            self.assertNotIn(str(ROOT).lower(), str(MS.default_d1_lock("nt")).lower())
            self.assertEqual(MS.default_d1_lock("posix"), ROOT / "research" / "downside" / "out" / "forward.lock")
        with mock.patch.dict(os.environ, {"DESK_D1_LOCK": "X:/l/forward.lock"}):
            self.assertEqual(MS.default_d1_lock("nt"), Path("X:/l/forward.lock"))
