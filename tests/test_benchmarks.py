"""The Vs market tab (desk/benchmarks.py): the bot's % return next to the index funds."""
from desk import benchmarks as BM
from desk.engine import Desk
from desk.journal import Journal
from desk.source import Demo
from tests.base import DeskCase
from tests.test_graph import close


class Benchmarks(DeskCase):
    def test_returns_start_at_the_close_before_the_first_trade(self):
        bars = {"SPY": [{"date": "2026-11-16", "c": 100.0}, {"date": "2026-11-17", "c": 101.0}, {"date": "2026-11-18", "c": 102.0}],
                "QQQ": "Yahoo did not answer"}
        days = [{"date": "2026-11-17", "pnl": 4.0}, {"date": "2026-11-18", "pnl": 6.0}]
        m = BM.compare(days, bars, 400.0, "2026-11-17")
        spy = next(g for g in m["groups"] if g["symbol"] == "SPY")
        self.assertEqual([p["pct"] for p in spy["points"]], [1.0, 2.0])
        self.assertEqual([p["pct"] for p in m["bot"]["points"]], [1.0, 2.5])
        self.assertEqual((m["bot"]["pct"], m["bot"]["pnl"]), (2.5, 10.0))
        qqq = next(g for g in m["groups"] if g["symbol"] == "QQQ")
        self.assertEqual((qqq["pct"], qqq["error"]), (None, "Yahoo did not answer"))

    def test_desk_counts_only_the_bots_trades_and_stays_offline_in_the_demo(self):
        j = Journal()
        close(j, "A", 8, "2026-11-18 10:00", "auto")
        close(j, "B", -50, "2026-11-18 11:00")         # yours, not the bot's
        m = Desk(j, Demo()).benchmarks()
        self.assertEqual((m["start"], m["bot"]["pnl"]), ("2026-11-18", 8.0))
        self.assertTrue(all(g["error"] for g in m["groups"]))
