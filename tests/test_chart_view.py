"""The Desk tab's chart (/api/chart), its paper Buy button and the claude lookup. Demo data, no network."""
import os
import stat
import tempfile
from unittest import mock

from desk import agent, charts
from desk.engine import DeskError
from tests.test_desk import DemoCase


class Vwap(DemoCase):
    def test_restarts_each_day_and_skips_premarket(self):
        bars = [{"date": "2026-10-19", "minute": 9 * 60, "h": 99, "l": 99, "c": 99, "v": 1000},       # pre-market
                {"date": "2026-10-19", "minute": 570, "h": 11, "l": 9, "c": 10, "v": 100},
                {"date": "2026-10-19", "minute": 575, "h": 21, "l": 19, "c": 20, "v": 100},
                {"date": "2026-10-20", "minute": 570, "h": 31, "l": 29, "c": 30, "v": 50}]
        self.assertEqual([v for _, v in charts.vwap(bars)], [10.0, 15.0, 30.0])


class ChartView(DemoCase):
    def test_demo_chart_has_candles_vwap_and_levels(self):
        d = self.desk()
        out = d.chart("TSLA", "5m")
        self.assertEqual(out["symbol"], "TSLA")
        self.assertTrue(out["candles"] and out["vwap"])
        c = out["candles"][0]
        self.assertTrue(c["low"] <= min(c["open"], c["close"]) and c["high"] >= max(c["open"], c["close"]))
        self.assertIn("or", {lv["kind"] for lv in out["levels"]})

    def test_bad_interval_is_refused(self):
        with self.assertRaises(DeskError):
            self.desk().chart("TSLA", "7m")


class PaperBuy(DemoCase):
    def test_buys_at_last_price_inside_the_limits(self):
        d = self.desk()
        last = charts.session_bars(charts.intraday(d.src, "TSLA"), d.src.now())[-1]["c"]
        out = d.paper_buy("tsla")
        self.assertEqual((out["symbol"], out["price"]), ("TSLA", round(last, 2)))
        self.assertEqual(out["stop"], round(out["price"] * 0.98, 2))
        self.assertEqual(out["target"], round(out["price"] * 1.04, 2))
        t = d.j.trade(out["trade_id"])
        self.assertEqual((t["status"], t["note"], t["qty"]), ("open", "paper buy", out["qty"]))
        self.assertGreaterEqual(out["qty"], 1)

    def test_more_shares_than_the_limits_allow_is_refused(self):
        with self.assertRaisesRegex(DeskError, "allow 1 to"):
            self.desk().paper_buy("TSLA", 10 ** 6)

    def test_never_in_live_mode_and_never_a_bad_ticker(self):
        with self.assertRaises(DeskError):
            self.desk(mode="live").paper_buy("TSLA")
        with self.assertRaises(DeskError):
            self.desk().paper_buy("TS LA; rm")


class FindClaude(DemoCase):
    def test_finds_claude_in_local_bin_when_path_is_bare(self):
        home = tempfile.mkdtemp()
        os.makedirs(os.path.join(home, ".local", "bin"))
        exe = os.path.join(home, ".local", "bin", "claude")
        with open(exe, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(exe, os.stat(exe).st_mode | stat.S_IXUSR)
        env = {"HOME": home, "PATH": "/nonexistent"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(agent.find_claude(), exe)
