"""The 9:00 AM watchlist research (desk/watchlist.py), with fake screeners: no network."""
from desk import settings as S
from desk import watchlist as W
from desk.engine import Desk
from desk.journal import Journal
from desk.source import Demo
from tests.base import DeskCase


def bars(closes, swing):
    """Daily bars around each close, high/low swing apart."""
    return [{"o": c, "h": c + swing / 2, "l": c - swing / 2, "c": c} for c in closes]


def q(sym, price=20.0, vol=5_000_000, kind="EQUITY"):
    return {"symbol": sym, "regularMarketPrice": price, "averageDailyVolume3Month": vol, "quoteType": kind}


class Watchlist(DeskCase):
    def test_atr_pct_is_average_true_range_over_the_last_close(self):
        self.assertEqual(W.atr_pct(bars([10.0] * 15, 1.0)), 10.0)
        self.assertIsNone(W.atr_pct(bars([10.0] * 14, 1.0)))        # needs n + 1 bars

    def test_candidates_keep_liquid_affordable_new_stocks(self):
        got = W.candidates({"most_actives": [q("GOOD"), q("ETF", kind="ETF"), q("THIN", vol=10_000), q("CHEAP", price=2),
                                             q("PRICY", price=900), q("CORE"), q("BAD.X"), q("NOPE")],
                            "day_gainers": [q("GOOD")]},
                           core={"CORE"}, blocked={"NOPE"}, min_price=5, max_price=500)
        self.assertEqual(list(got), ["GOOD"])
        self.assertEqual(got["GOOD"]["from"], ["most_actives", "day_gainers"])

    def test_research_adds_the_most_volatile_movers_to_the_core(self):
        st = dict(S.validate({}), core_watchlist=["AAPL"], auto_watchlist_movers=2)
        swings = {"CALM": 0.2, "WILD": 3.0, "MID": 1.0}
        r = W.research(st, 500, fetch_screen=lambda name: [q(s) for s in swings],
                       fetch_daily=lambda sym: bars([20.0] * 20, swings[sym]))
        self.assertEqual(r["watchlist"], ["AAPL", "WILD", "MID"])
        self.assertEqual([p["symbol"] for p in r["picks"]], ["WILD", "MID"])
        self.assertEqual(r["errors"], [])

    def test_a_dead_screener_is_reported_not_raised(self):
        st = dict(S.validate({}), core_watchlist=["AAPL"])

        def boom(name):
            raise OSError("down")
        r = W.research(st, 500, fetch_screen=boom, fetch_daily=lambda s: [])
        self.assertEqual(r["watchlist"], ["AAPL"])
        self.assertEqual(len(r["errors"]), len(W.SCREENS))

    def test_the_demo_never_researches(self):
        self.assertIsNone(Desk(Journal(), Demo()).research_watchlist())

    def test_zero_trades_a_day_means_no_cap(self):
        st = S.validate({"mode": "live", "max_trades_per_day": 0})
        self.assertEqual(S.trades_cap(st), S.PAPER_TRADES_CAP)
        self.assertEqual(S.trades_cap(dict(st, max_trades_per_day=3)), 3)

    def test_todays_unfinished_daily_bar_is_left_out(self):
        st = dict(S.validate({}), core_watchlist=[], auto_watchlist_movers=1)
        days = [dict(b, date="2026-11-%02d" % (i + 1)) for i, b in enumerate(bars([20.0] * 15, 1.0))]
        today = dict(bars([20.0], 30.0)[0], date="2026-11-16")      # a huge half-day bar
        r = W.research(st, 500, fetch_screen=lambda name: [q("WILD")], fetch_daily=lambda s: days + [today],
                       today="2026-11-16")
        self.assertEqual(r["picks"][0]["atr_pct"], 5.0)
