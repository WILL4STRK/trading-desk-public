"""Regression tests for the breaking-news lifecycle, failed exits, the comparison retries and the loop:
no network, no claude."""
import threading
from pathlib import Path
from unittest import mock

from desk import breaking as BN
from desk import charts, notify, outcome
from desk import paper as P
from desk import settings as S
from desk.engine import COMPARE_TRIES, Desk
from desk.journal import Journal
from desk.scheduler import Scheduler
from desk.source import Demo
from tests.base import DeskCase
from tests.test_auto import AutoBase
from tests.test_breaking import call, item
from tests.test_compare import RULES, bar, day
from tests.test_paper import q


class NewsState(DeskCase):
    def check(self, items, ask):
        return BN.check(S.validate({}), ["TSLA"], {}, Path(self.tmp), ask=ask,
                        get=lambda t, st, wd: {"items": items, "notes": ["yahoo: HTTP 429"]})

    def test_a_story_without_a_ticker_does_not_break_the_prompt(self):
        text = BN.prompt([item(None, "Fed cuts rates", tags=["macro"], touches=["SPY"])], {})
        self.assertIn('"tickers": ["SPY"]', text)

    def test_stories_come_back_until_acknowledged_and_feed_notes_are_errors(self):
        news = [item("TSLA", "Tesla to acquire X", id="rss:1")]

        def boom(text):
            raise RuntimeError("not logged in")
        r = self.check(news, boom)
        self.assertEqual(r["ack_ids"], [])                         # Claude failed: shown again next time
        self.assertIn("news: yahoo: HTTP 429", r["errors"])
        asked = []
        r = self.check(news, lambda t: asked.append(t) or {"calls": [call("TSLA", "buy")]})
        self.assertEqual(len(asked), 1)
        self.assertEqual(r["calls"][0]["item_id"], BN.item_id(news[0]))
        BN.acknowledge(Path(self.tmp), r["ack_ids"])
        self.check(news, lambda t: asked.append(t) or {"calls": []})
        self.assertEqual(len(asked), 1)                            # handled: not shown to Claude again

    def test_the_skill_is_read_without_its_own_seen_state(self):
        got = {}

        def fake(tool, args, wd, timeout):
            got["args"] = args
            return mock.Mock(json={"items": [], "notes": []})
        with mock.patch("desk.skills.run", fake):
            BN.fetch(["TSLA"], S.validate({}), Path(self.tmp))
        self.assertIn("--all", got["args"])
        self.assertIn("--dry-run", got["args"])


class NewsApply(DeskCase):
    def desk(self, calls, before=None):
        d = Desk(Journal(), Demo())
        d.src.demo = False
        d._auto = mock.Mock(news_exit=mock.Mock(return_value=1))
        d.clock = mock.Mock(return_value={"open": True})
        S.update({"watchlist": ["TSLA", "AMD"]})
        full = [dict(c, title="headline", source="Yahoo", link="", item_id="s1") for c in calls]

        def check(*a, **kw):
            if before:
                before()
            return {"calls": full, "heavy": len(full), "errors": [], "ack_ids": ["s1"]}
        for p in (mock.patch.object(BN, "check", side_effect=check), mock.patch.object(notify, "send_news")):
            p.start()
            self.addCleanup(p.stop)
        return d

    def test_turned_off_while_claude_reads_acts_on_nothing_and_acknowledges_nothing(self):
        d = self.desk([call("NVDA", "buy")], before=lambda: S.update({"breaking_news": False}))
        with mock.patch.object(BN, "acknowledge") as ack:
            r = d.breaking_news()
        self.assertEqual((r["calls"], r["added"]), ([], []))
        self.assertIn("turned off", r["errors"][-1])
        ack.assert_not_called()
        self.assertNotIn("NVDA", S.load()["watchlist"])

    def test_avoid_blocks_new_entries_today(self):
        d = self.desk([call("AMD", "avoid")])
        d.breaking_news()
        self.assertEqual(d.news_avoid(), ["AMD"])
        self.assertNotIn("AMD", S.load()["watchlist"])

    def test_concurrent_watchlist_edits_are_not_lost(self):
        S.update({"watchlist": []})
        names = ["T" + a + b for a in "ABCD" for b in "VWXYZ"]           # 20 tickers, written at once

        def add(sym):
            S.update(lambda cur: {"watchlist": cur["watchlist"] + [sym]})
        threads = [threading.Thread(target=add, args=(n,)) for n in names]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(S.load()["watchlist"]), sorted(names))

    def test_research_keeps_what_news_added_while_it_ran(self):
        d = Desk(Journal(), Demo())
        d.src.demo = False

        def research(*a, **kw):
            S.update(lambda cur: {"watchlist": cur["watchlist"] + ["NEWS"]})     # breaking news, mid-research
            return {"picks": [{"symbol": "B", "atr_pct": 5.0}], "core": ["X"], "watchlist": ["X", "B"],
                    "errors": [], "by": "formula", "summary": ""}
        S.update({"watchlist_agent": False, "auto_watchlist": True})
        with mock.patch("desk.watchlist.research", research), mock.patch.object(d, "spendable", return_value=500):
            d.research_watchlist()
        self.assertEqual(S.load()["watchlist"], ["X", "B", "NEWS"])


class NewsExits(AutoBase):
    def test_a_buy_filled_before_the_cancel_is_closed_as_a_trade(self):
        self.tick()                                       # the buy is working
        self.px["KO"] = q(60.10)                          # and the price came to it
        self.assertEqual(self.d.auto.news_exit("KO", "breaking news"), 1)
        e = self.entry()
        self.assertEqual((e["state"], e["reason"]), ("closed", "flat"))
        self.assertEqual(self.d.j.open_trades(), [])
        self.assertEqual(self.d.paper.snapshot()["positions"], [])

    def test_a_close_that_cannot_be_placed_is_retried_until_out(self):
        t = self.filled()
        with mock.patch.object(self.d.paper, "place_order", side_effect=P.PaperError("no price")):
            self.assertEqual(self.d.auto.news_exit("KO", "breaking news"), 1)
        self.assertEqual(self.d.j.open_trades()[0]["id"], t["id"])
        self.assertEqual(self.entry()["news_exit"], "breaking news")
        with mock.patch.object(self.d, "clock", return_value={"open": False}):
            self.tick()                                   # market shut: still waiting
        self.assertEqual(self.entry()["state"], "open")
        self.tick()
        c = self.d.j.trade(t["id"])
        self.assertEqual((c["status"], c["exit_reason"]), ("closed", "flat"))

    def test_avoid_declines_todays_signals(self):
        self.d.j.put("news_avoid:" + self.d.src.now()[:10], ["KO"])
        self.tick()
        self.assertEqual(self.entry()["state"], "declined")


class Replays(DeskCase):
    def test_a_thin_first_breakout_waits_for_a_bar_with_volume(self):
        bars = day([(10.3, 10.6, 10.3, 10.5)]) + [bar(590, 10.5, 10.7, 10.5, 10.6, v=5000)]
        bars[3]["v"] = 100
        o = outcome.watchlist_replay(bars, dict(RULES, min_volume_x=1.5), "long")
        self.assertEqual(o["trigger"], "09:55")

    def test_a_missing_range_slot_is_no_data(self):
        bars = day([(10.3, 10.6, 10.3, 10.5)])
        del bars[1]
        bars.insert(1, bar(600, 10.3, 10.4, 10.1, 10.2))    # still enough bars, but 9:35 is missing
        self.assertEqual(outcome.watchlist_replay(bars, RULES, "long")["result"], outcome.NO_DATA)

    def test_a_stop_under_the_floor_is_no_plan_and_cents_are_exact(self):
        bars = day([(10.3, 10.6, 10.3, 10.5)])
        self.assertEqual(outcome.watchlist_replay(bars, dict(RULES, min_stop_pct=50), "long")["result"], outcome.NO_PLAN)
        pl = outcome._long_plan(10.5, {"high": 10.4, "low": 9.61}, "or-mid", 2, 0)
        self.assertEqual(pl["stop"], 10.0)                # (10.40 + 9.61) / 2 = 10.005, floored in cents
        self.assertEqual(pl["target"], 11.5)

    def test_a_breakout_that_goes_stale_before_the_first_entry_time_is_no_trigger(self):
        bars = day([(10.3, 10.6, 10.3, 10.5)])
        o = outcome.watchlist_replay(bars, dict(RULES, no_entries_before="10:30", signal_valid_minutes=10), "long")
        self.assertEqual(o["result"], outcome.NO_TRIGGER)


class CompareRetries(DeskCase):
    def test_missing_candles_are_retried_then_given_up(self):
        d = Desk(Journal(), Demo())
        d.src.demo = False
        date = d.src.now()[:10]
        d.j.put("watchlist_comparison:" + date, {"formula": [{"symbol": "A"}], "claude": None, "rules": RULES})
        with mock.patch("desk.charts.fetch", side_effect=charts.ChartError("offline")):
            for _ in range(COMPARE_TRIES - 1):
                self.assertEqual(d.compare_watchlists(date), 1)
            self.assertFalse(d.j.get("watchlist_results:" + date)["A"]["final"])
            self.assertEqual(d.compare_watchlists(date), 1)
            self.assertTrue(d.j.get("watchlist_results:" + date)["A"]["final"])
            self.assertEqual(d.compare_watchlists(date), 0)        # given up: no more reads

    def test_a_day_whose_candles_stop_early_is_tried_again(self):
        d = Desk(Journal(), Demo())
        d.src.demo = False
        date = d.src.now()[:10]
        d.j.put("watchlist_comparison:" + date, {"formula": [{"symbol": "A"}], "claude": None, "rules": RULES})
        short = [dict(b, date=date) for b in day([(10.3, 10.6, 10.3, 10.5)])]
        with mock.patch("desk.charts.fetch", return_value={"bars": short}):
            d.compare_watchlists(date)
        self.assertIn("stop before", d.j.get("watchlist_results:" + date)["A"]["error"])
        d.j.put("outcomes_done", date)
        with mock.patch.object(d, "compare_watchlists") as again:
            d.compare_pending()
        again.assert_called_once_with(date)


class Loop(DeskCase):
    def test_a_failing_pass_does_not_end_the_scheduler(self):
        s = Scheduler(mock.Mock(), log=lambda m: None)
        s.wake = mock.Mock()
        s.tick = mock.Mock(side_effect=[RuntimeError("bad settings file"), None, KeyboardInterrupt])
        with self.assertRaises(KeyboardInterrupt):
            s.run()
        self.assertEqual(s.tick.call_count, 3)                     # it kept going after the error


class ReviewRegressions(AutoBase):
    def test_a_fill_during_cancel_is_reconciled_without_a_second_sale(self):
        self.tick()
        cancel = self.d.paper.cancel_order
        raced = []

        def fill_then_cancel(account, order_id):
            if not raced:
                raced.append(True)
                self.px["KO"] = q(60.10)
                self.d.paper.settle()
            return cancel(account, order_id)
        with mock.patch.object(self.d.paper, "cancel_order", side_effect=fill_then_cancel):
            self.assertEqual(self.d.auto.news_exit("KO", "breaking news"), 1)
        self.assertEqual(self.entry()["state"], "closed")
        self.assertEqual(self.d.paper.snapshot()["positions"], [])

    def test_a_target_filling_during_child_cancel_never_gets_an_extra_market_sale(self):
        trade = self.filled()
        cancel = self.d.paper.cancel_order
        raced = []

        def target_then_cancel(account, order_id):
            if not raced:
                raced.append(True)
                self.px["KO"] = q(61.10)
                self.d.paper.settle()
            return cancel(account, order_id)
        with mock.patch.object(self.d.paper, "cancel_order", side_effect=target_then_cancel), \
                mock.patch.object(self.d.paper, "place_order", wraps=self.d.paper.place_order) as place:
            self.d.auto.news_exit("KO", "breaking news")
            place.assert_not_called()
        self.tick()
        self.assertEqual(self.d.j.trade(trade["id"])["exit_reason"], "target")
        self.assertEqual(self.d.paper.snapshot()["positions"], [])

    def test_settlement_waits_for_the_desk_transaction(self):
        self.tick()
        started, finished = threading.Event(), threading.Event()

        def settle():
            started.set()
            self.d.paper.settle()
            finished.set()
        thread = threading.Thread(target=settle)
        with self.d.lock:
            thread.start()
            self.assertTrue(started.wait(2))
            self.assertFalse(finished.wait(0.05))
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(finished.is_set())


class ReviewApply(DeskCase):
    desk = NewsApply.desk

    def test_market_closed_after_assessment_does_not_apply_or_acknowledge(self):
        d = self.desk([call("NVDA", "buy")])
        d.clock.return_value = {"open": False}
        with mock.patch.object(BN, "acknowledge") as ack:
            result = d.breaking_news()
        self.assertEqual(result["calls"], [])
        self.assertIn("market closed", result["errors"][-1])
        ack.assert_not_called()
        self.assertNotIn("NVDA", S.load()["watchlist"])

    def test_success_is_acknowledged_after_the_watchlist_changes(self):
        d = self.desk([call("NVDA", "buy")])

        def acknowledge(wd, ids):
            self.assertIn("NVDA", S.load()["watchlist"])
            self.assertTrue(d.j.has_event("news-2026-10-19-NVDA-buy-s1"))
        with mock.patch.object(BN, "acknowledge", side_effect=acknowledge) as ack:
            d.breaking_news()
        ack.assert_called_once()

    def test_research_preserves_a_removal_and_an_avoid_added_during_research(self):
        d = Desk(Journal(), Demo())
        d.src.demo = False
        S.update({"watchlist_agent": False, "watchlist": ["AAPL", "AMD"]})

        def research(*args, **kwargs):
            with d.lock:
                S.update({"watchlist": ["AMD", "NEWS"]})
                d.j.put("news_avoid:" + d.src.now()[:10], ["AMD"])
            return {"core": ["AAPL", "AMD"], "picks": [], "watchlist": ["AAPL", "AMD"],
                    "errors": [], "by": "formula", "summary": ""}
        with mock.patch("desk.watchlist.research", side_effect=research), mock.patch.object(d, "spendable", return_value=500):
            result = d.research_watchlist()
        self.assertEqual(S.load()["watchlist"], ["NEWS"])
        self.assertEqual(result["watchlist"], ["NEWS"])

    def test_restart_reuses_successful_morning_research(self):
        d = Desk(Journal(), Demo())
        d.src.demo = False
        saved = {"date": d.src.now()[:10], "applied": True, "watchlist": ["AAPL"]}
        d.j.put("watchlist_research", saved)
        with mock.patch("desk.agent.research") as research:
            self.assertEqual(d.research_watchlist(), saved)
        research.assert_not_called()


class ReviewLoop(DeskCase):
    def test_a_live_news_worker_prevents_a_second_one(self):
        entered, release = threading.Event(), threading.Event()
        desk = mock.Mock()
        desk.src.demo = False
        desk.src.now.return_value = "2026-11-18 10:00"
        desk.clock.return_value = {"open": True}

        def news():
            entered.set()
            release.wait(5)
            return {"calls": [], "errors": [], "added": []}
        desk.breaking_news.side_effect = news
        scheduler = Scheduler(desk, log=lambda m: None)
        scheduler.premarket_day = "2026-11-18"
        with mock.patch("desk.scheduler.S.load", return_value=dict(S.DEFAULTS, mode="live")):
            try:
                scheduler.tick(wall=10000)
                self.assertTrue(entered.wait(2))
                scheduler.tick(wall=20000)
                self.assertEqual(desk.breaking_news.call_count, 1)
            finally:
                release.set()
                if scheduler.news_worker:
                    scheduler.news_worker.join(5)


class ReviewReplay(DeskCase):
    def test_long_waits_for_a_new_trigger_after_the_first_entry_time(self):
        bars = day([(10.3, 10.6, 10.3, 10.5)]) + [bar(595, 10.5, 10.8, 10.5, 10.7)]
        result = outcome.watchlist_replay(bars, dict(RULES, no_entries_before="10:00"), "long")
        self.assertEqual(result["trigger"], "10:00")
        self.assertEqual(result["stop"], 10.2)
        self.assertEqual(result["target"], 11.7)

    def test_percentage_stop_uses_decimal_flooring(self):
        plan = outcome._long_plan(100, {"high": 100, "low": 99}, "pct:1.000000001", 2, 0.2)
        self.assertEqual(plan["stop"], 98.99)
        self.assertEqual(plan["target"], 102.02)


class ReviewCompare(DeskCase):
    def desk(self):
        d = Desk(Journal(), Demo())
        d.src.demo = False
        date = d.src.now()[:10]
        d.j.put("watchlist_comparison:" + date, {"formula": [{"symbol": "A"}], "claude": None, "rules": RULES})
        return d, date

    def test_old_comparisons_are_final_without_requesting_a_chart(self):
        d, date = self.desk()
        d.src.set_now("2026-11-18 16:10")
        with mock.patch("desk.charts.fetch") as fetch:
            self.assertEqual(d.compare_watchlists(date), 0)
        fetch.assert_not_called()
        self.assertTrue(d.j.get("watchlist_results:" + date)["A"]["final"])

    def test_a_missing_middle_slot_is_retryable_even_with_a_closing_bar(self):
        d, date = self.desk()
        bars = [dict(bar(m, 10.2, 10.4, 10, 10.2), date=date) for m in range(570, 960, 5) if m != 575]
        with mock.patch("desk.charts.fetch", return_value={"bars": bars}):
            d.compare_watchlists(date)
        result = d.j.get("watchlist_results:" + date)["A"]
        self.assertIn("missing", result["error"])
        self.assertFalse(result["final"])

    def test_legacy_nested_no_data_results_are_not_final(self):
        d, date = self.desk()
        d.j.put("watchlist_results:" + date, {"A": {"long": {"result": "no data"}, "short": {"result": "no data"}}})
        with mock.patch("desk.charts.fetch", side_effect=charts.ChartError("offline")) as fetch:
            self.assertEqual(d.compare_watchlists(date), 1)
        fetch.assert_called_once()


class VendorNewsReview(DeskCase):
    def setUp(self):
        super().setUp()
        import importlib.util
        path = Path(__file__).resolve().parents[1] / "vendor/day-trader-skill/trader-news/news.py"
        spec = importlib.util.spec_from_file_location("review_vendor_news", path)
        self.news = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.news)

    def test_missing_rss_guids_and_links_have_stable_distinct_ids(self):
        xml = b"<rss><channel><item><title>First catalyst</title></item><item><title>Second catalyst</title></item></channel></rss>"
        first = self.news.parse_rss(xml, "AAPL")
        again = self.news.parse_rss(xml, "AAPL")
        self.assertNotEqual(first[0]["id"], first[1]["id"])
        self.assertEqual([i["id"] for i in first], [i["id"] for i in again])

    def test_retry_after_is_not_shortened_to_thirty_seconds(self):
        error = self.news.urllib.error.HTTPError("https://example.test", 429, "slow down", {"Retry-After": "60"}, None)
        response = mock.MagicMock()
        response.__enter__.return_value.headers = {}
        response.__enter__.return_value.read.return_value = b"<rss/>"
        with mock.patch.object(self.news.urllib.request, "urlopen", side_effect=[error, response]), \
                mock.patch.object(self.news.time, "sleep") as sleep:
            self.assertEqual(self.news.Fetcher(pause=0).get("https://example.test", "feed"), b"<rss/>")
        sleep.assert_called_once_with(60.0)
