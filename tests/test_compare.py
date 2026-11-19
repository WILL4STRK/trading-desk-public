"""The formula-against-Claude watchlist comparison and the calibration numbers: no network."""
from unittest import mock

from desk import outcome
from desk import review as R
from desk import settings as S
from desk.engine import COMPARE_RULES, Desk
from desk.journal import Journal
from desk.source import Demo
from tests.base import DeskCase

RULES = {k: S.DEFAULTS[k] for k in COMPARE_RULES}


def bar(minute, o, h, l, c, v=1000):
    return {"minute": minute, "start": "%02d:%02d" % (minute // 60, minute % 60), "date": "2026-11-18",
            "o": o, "h": h, "l": l, "c": c, "v": v}


def day(after):
    """A 15-minute range 10.00-10.40 (9:30-9:45), then the bars given from 9:45 on, 5 minutes apart."""
    rng = [bar(570, 10.2, 10.4, 10.0, 10.3), bar(575, 10.3, 10.4, 10.1, 10.2), bar(580, 10.2, 10.3, 10.0, 10.1)]
    return rng + [bar(585 + 5 * i, *b) for i, b in enumerate(after)]


class Replay(DeskCase):
    def test_a_breakout_that_runs_reaches_the_target(self):
        bars = day([(10.3, 10.6, 10.3, 10.5), (10.5, 10.5, 10.45, 10.48), (10.5, 11.2, 10.5, 11.1)])
        o = outcome.watchlist_replay(bars, RULES, "long")
        self.assertEqual((o["result"], o["trigger"], o["stop"]), ("target", "09:50", 10.2))
        self.assertGreater(o["r"], 0)

    def test_no_breakout_no_data_and_atr_stops_are_named(self):
        flat = day([(10.2, 10.3, 10.1, 10.2)] * 5)
        self.assertEqual(outcome.watchlist_replay(flat, RULES, "long")["result"], "no trigger")
        self.assertEqual(outcome.watchlist_replay([], RULES, "long")["result"], "no data")
        bars = day([(10.3, 10.6, 10.3, 10.5)])
        self.assertEqual(outcome.watchlist_replay(bars, dict(RULES, stop_rule="atr:1"), "long")["result"], "unsupported")

    def test_a_breakdown_is_replayed_short(self):
        bars = day([(10.1, 10.1, 9.8, 9.9), (9.9, 10.0, 9.88, 9.95), (9.9, 9.9, 9.0, 9.1)])
        o = outcome.watchlist_replay(bars, RULES, "short")
        self.assertEqual(o["result"], "target")


class Comparison(DeskCase):
    def test_r_per_name_day_counts_untriggered_picks_as_zero(self):
        hit = {"result": "target", "r": 2.0}
        none = {"result": "no trigger", "r": None}
        days = [{"date": "2026-11-18", "formula": [{"symbol": "A"}, {"symbol": "B"}], "claude": [{"symbol": "A"}],
                 "results": {"A": {"long": hit, "short": none}, "B": {"long": none, "short": none}}},
                {"date": "2026-11-17", "formula": [{"symbol": "A"}], "claude": None, "results": {"A": {"long": hit}}}]
        c = R.watchlist_comparison(days)
        self.assertEqual(c["days"], 1)                                   # the day without Claude's list is left out
        self.assertEqual((c["formula long"]["name_days"], c["formula long"]["r_per_name_day"]), (2, 1.0))
        self.assertEqual((c["claude long"]["r_per_fill"], c["formula long"]["targets"]), (2.0, 1))
        self.assertEqual(c["overlap"], 0.5)

    def test_the_morning_lists_are_kept_and_replayed_after_the_close(self):
        d = Desk(Journal(), Demo())
        d.src.demo = False
        fake = {"picks": [{"symbol": "B", "atr_pct": 5.0}], "formula_picks": [{"symbol": "A"}], "claude_picks": [{"symbol": "B"}],
                "core": ["X"], "watchlist": ["X", "B"], "errors": [], "by": "claude", "summary": ""}
        with mock.patch("desk.agent.research", return_value=fake), mock.patch.object(d, "spendable", return_value=500):
            d.research_watchlist()
        date = d.src.now()[:10]
        kept = d.j.get("watchlist_comparison:" + date)
        self.assertEqual((kept["formula"][0]["symbol"], kept["claude"][0]["symbol"]), ("A", "B"))
        self.assertEqual(set(kept["rules"]), set(COMPARE_RULES))
        bars = [dict(b, date=date) for b in day([(10.3, 10.6, 10.3, 10.5)] + [(10.5, 10.6, 10.4, 10.5)] * 74)]
        with mock.patch("desk.charts.fetch", return_value={"bars": bars}), mock.patch("desk.engine.COMPARE_PAUSE", 0):
            self.assertEqual(d.compare_watchlists(date), 2)
            self.assertEqual(d.compare_watchlists(date), 0)              # done: not read again
        self.assertIn("long", d.j.get("watchlist_results:" + date)["A"])
        self.assertIn("FORMULA AGAINST CLAUDE", d.review()["text"])


class Calibration(DeskCase):
    def test_brier_and_bins_from_decided_replays_only(self):
        sigs = [{"id": i, "payload": {"odds": {"p_target": p, "p_stop": 1 - p}}} for i, p in ((1, 0.3), (2, 0.3), (3, 0.7), (4, 0.5))]
        outs = [{"signal_id": 1, "result": "target"}, {"signal_id": 2, "result": "stop"},
                {"signal_id": 3, "result": "target"}, {"signal_id": 4, "result": "open"},
                {"signal_id": 9, "result": "no fill"}]
        oc = R.odds_check(sigs, outs)
        self.assertEqual((oc["scored"], oc["no_fill"], oc["enough"]), (3, 1, False))
        self.assertEqual(oc["brier"], round((0.49 + 0.09 + 0.09) / 3, 3))
        self.assertEqual([b["odds"] for b in oc["bins"]], ["20-40%", "60-80%"])
