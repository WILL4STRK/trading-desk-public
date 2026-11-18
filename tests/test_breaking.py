"""Breaking news (desk/breaking.py and Desk.breaking_news), with fake news and a fake Claude: no network."""
from pathlib import Path
from unittest import mock

from desk import breaking as BN
from desk import notify
from desk import settings as S
from desk.engine import Desk
from desk.journal import Journal
from desk.source import Demo
from tests.base import DeskCase


def item(ticker, title, tags=("deal",), touches=None, link="https://news.example/a", **kw):
    return dict({"ticker": ticker, "title": title, "source": "Yahoo Finance", "tags": list(tags),
                 "touches": touches or [ticker], "link": link, "time": "2026-11-18T15:00:00+00:00"}, **kw)


def call(sym, action, impact="high", why="big news"):
    return {"symbol": sym, "impact": impact, "action": action, "why": why}


class Breaking(DeskCase):
    def check(self, items, answer, held=None):
        seen = {}

        def ask(text):
            seen["text"] = text
            return answer
        r = BN.check(S.validate({}), ["TSLA"], held or {}, Path(self.tmp), ask=ask, get=lambda t, st, wd: items)
        return r, seen

    def test_light_news_never_asks_claude(self):
        r, seen = self.check([item("TSLA", "CEO appointed", tags=["people"])], None)
        self.assertEqual((r["calls"], r["heavy"]), ([], 0))
        self.assertNotIn("text", seen)

    def test_only_high_impact_calls_on_listed_tickers_count(self):
        items = [item("TSLA", "Tesla to acquire X"), item("SPY", "Fed cuts rates", tags=["macro"]),
                 item("NVDA", "FDA approves", tags=[], important=True)]
        r, seen = self.check(items, {"calls": [call("TSLA", "buy"), call("EVIL", "buy"), call("SPY", "short", "medium"),
                                               call("NVDA", "exit"), call("TSLA", "short"), call("nvda", "avoid")]})
        self.assertEqual([(c["symbol"], c["action"]) for c in r["calls"]], [("TSLA", "buy"), ("NVDA", "avoid")])
        self.assertEqual(r["calls"][0]["link"], "https://news.example/a")
        self.assertIn("Fed cuts rates", seen["text"])

    def test_exit_needs_a_held_stock_and_links_must_be_https(self):
        items = [item("TSLA", "Tesla recall", tags=["risk"], link="javascript:alert(1)")]
        r, _ = self.check(items, {"calls": [call("TSLA", "exit")]}, held={"TSLA": "long"})
        self.assertEqual((r["calls"][0]["action"], r["calls"][0]["link"]), ("exit", ""))
        r, _ = self.check(items, {"calls": [call("TSLA", "exit")]})
        self.assertEqual(r["calls"], [])

    def test_a_failing_claude_or_feed_acts_on_nothing(self):
        def boom(text):
            raise RuntimeError("not logged in")
        r = BN.check(S.validate({}), ["TSLA"], {}, Path(self.tmp), ask=boom,
                     get=lambda t, st, wd: [item("TSLA", "Tesla to acquire X")])
        self.assertEqual(r["calls"], [])
        self.assertIn("not logged in", r["errors"][0])

        def down(t, st, wd):
            raise OSError("offline")
        self.assertIn("offline", BN.check(S.validate({}), [], {}, Path(self.tmp), get=down)["errors"][0])

    def test_market_feeds_and_open_trades_are_always_read(self):
        got = {}
        BN.check(S.validate({}), ["TSLA"], {"AMD": "short"}, Path(self.tmp), get=lambda t, st, wd: got.setdefault("t", t) and [])
        self.assertEqual(got["t"], ["TSLA", "AMD", "SPY", "QQQ"])


class DeskActs(DeskCase):
    def desk(self, calls):
        d = Desk(Journal(), Demo())
        d.src.demo = False
        d._auto = mock.Mock(news_exit=mock.Mock(return_value=1))
        S.update({"watchlist": ["TSLA", "AMD"], "blocked": ["GME"]})
        full = [dict(c, title="headline", source="Yahoo", link="") for c in calls]
        p = mock.patch.object(BN, "check", return_value={"calls": full, "heavy": len(full), "errors": []})
        p.start()
        self.addCleanup(p.stop)
        sent = mock.patch.object(notify, "send_news")
        self.sent = sent.start()
        self.addCleanup(sent.stop)
        return d

    def test_buy_adds_to_todays_list_once_a_day(self):
        d = self.desk([call("NVDA", "buy"), call("GME", "buy")])
        r = d.breaking_news()
        self.assertEqual(r["added"], ["NVDA"])
        self.assertEqual(S.load()["watchlist"], ["TSLA", "AMD", "NVDA"])
        self.assertIn("blocked list", d.j.events()[0]["message"])
        self.assertEqual(self.sent.call_count, 2)
        S.update({"watchlist": ["TSLA", "AMD"]})
        self.assertEqual(d.breaking_news()["added"], [])            # the same call later today: nothing again
        self.assertEqual(self.sent.call_count, 2)

    def test_avoid_drops_it_and_exit_closes_auto_paper(self):
        d = self.desk([call("AMD", "avoid"), call("TSLA", "exit")])
        d.breaking_news()
        self.assertEqual(S.load()["watchlist"], ["TSLA"])
        d._auto.news_exit.assert_any_call("AMD", "breaking news: avoid", close_open=False)
        d._auto.news_exit.assert_any_call("TSLA", "breaking news")
        self.assertIn("Auto paper is closing", d.j.events()[0]["message"])

    def test_off_and_demo_do_nothing(self):
        self.assertIsNone(Desk(Journal(), Demo()).breaking_news())
        d = self.desk([call("NVDA", "buy")])
        S.update({"breaking_news": False})
        self.assertIsNone(d.breaking_news())

    def test_the_phone_alert_names_the_call_and_the_article(self):
        b = notify.news_message({"symbol": "TSLA", "action": "short", "why": "recall", "title": "Tesla recall",
                                 "source": "Yahoo", "link": "https://news.example/a"}, "Added.", "paper")
        self.assertEqual(b["title"], "[PAPER] NEWS: SHORT TSLA")
        self.assertEqual(b["click"], "https://news.example/a")
        self.assertIn("Tesla recall", b["message"])
