"""Risk stops that look back further than one day (desk/breaker.py).

Taken from CloddsBot's circuit breaker and safety manager (docs/CLODDSBOT.md): a weekly loss limit, a
stop when the account is too far under its best point, and a stop after a run of losing trades. They
only stop NEW entries. They never close a trade, never size anything up, and the last two stay on until
you press Reset stops, so a bad run makes you read the record before the desk carries on."""
import json
import threading
import urllib.request

from desk import breaker
from desk import settings as S
from desk import stamp
from desk.engine import DeskError
from desk.server import serve
from tests.base import DeskCase
from tests.test_auto import AutoBase
from tests.test_desk import DemoCase


def trade(pnl, exit_at, tid=None, status="closed"):
    return {"id": tid or 0, "status": status, "pnl": pnl, "exit_at": exit_at}


def run(*pnls, day="2026-10-19"):
    """Closed trades, one a minute from 10:00 on `day`, oldest first."""
    return [trade(p, "%s 10:%02d" % (day, i), tid=i + 1) for i, p in enumerate(pnls)]


ON = {"max_weekly_loss_pct": 8.0, "max_drawdown_pct": 15.0, "max_losing_streak": 4}


class Pure(DeskCase):
    def status(self, trades, equity=400.0, today="2026-10-21", reset_at=None, **st):
        return breaker.status(dict(ON, **st), trades, equity, today, reset_at)

    def item(self, s, key):
        return next(i for i in s["items"] if i["key"] == key)

    def test_week_starts_on_monday(self):
        self.assertEqual(breaker.week_start("2026-10-21"), "2026-10-19")     # Wednesday
        self.assertEqual(breaker.week_start("2026-10-19"), "2026-10-19")     # Monday
        self.assertEqual(breaker.week_start("2026-10-25"), "2026-10-19")     # Sunday

    def test_nothing_recorded_trips_nothing(self):
        s = self.status([])
        self.assertIsNone(s["tripped"])
        self.assertFalse(s["needs_reset"])
        self.assertEqual([i["key"] for i in s["items"]], ["weekly", "drawdown", "streak"])

    def test_a_run_of_losers_trips_the_streak_stop_and_a_winner_ends_the_run(self):
        s = self.status(run(-5, -5, -5, -5), equity=380)
        self.assertEqual(s["tripped"]["key"], "streak")
        self.assertIn("4 losing trades in a row", s["tripped"]["reason"])
        self.assertTrue(s["needs_reset"])
        self.assertIsNone(self.status(run(-5, -5, -5, -5, 3), equity=383)["tripped"])
        self.assertEqual(self.item(self.status(run(-5, -5, -5), equity=385), "streak")["used"], 3)
        one = self.status(run(-5), equity=395, max_losing_streak=1)["tripped"]["reason"]
        self.assertTrue(one.startswith("1 losing trade in a row"), one)

    def test_an_even_trade_ends_a_losing_run(self):
        self.assertEqual(self.item(self.status(run(-5, -5, 0, -5)), "streak")["used"], 1)

    def test_open_trades_do_not_count(self):
        rows = run(-5, -5, -5) + [trade(None, None, tid=9, status="open")]
        self.assertEqual(self.item(self.status(rows), "streak")["used"], 3)

    def test_drawdown_is_measured_from_the_best_point_since_the_last_reset(self):
        # +20 then -80: best point $420, now $340, down $80 = 19 % of $420
        s = self.status(run(20, -40, -40), equity=340, max_losing_streak=0)
        d = self.item(s, "drawdown")
        self.assertEqual((d["used"], d["used_pct"]), (80.0, 19.05))
        self.assertEqual(d["limit"], 63.0)                                   # 15 % of the $420 peak
        self.assertEqual(s["tripped"]["key"], "drawdown")
        self.assertIn("from your best point", s["tripped"]["reason"])

    def test_just_under_the_drawdown_limit_is_fine(self):
        s = self.status(run(-30, -29), equity=341, max_losing_streak=0, max_weekly_loss_pct=0)    # $59 of a $400 peak = 14.75 %
        self.assertIsNone(s["tripped"])

    def test_reset_starts_the_counts_again(self):
        rows = run(-5, -5, -5, -5)
        s = self.status(rows, equity=380, reset_at="2026-10-19 10:03")
        self.assertIsNone(s["tripped"])
        self.assertEqual(self.item(s, "streak")["used"], 0)
        self.assertEqual(self.item(s, "drawdown")["used"], 0.0)
        s = self.status(rows + run(-5, day="2026-10-20"), equity=375, reset_at="2026-10-19 10:03")
        self.assertEqual(self.item(s, "streak")["used"], 1)

    def test_weekly_loss_counts_this_week_only_and_ignores_reset(self):
        last_week = run(-50, day="2026-10-16")                               # a Friday
        this_week = run(-10, day="2026-10-19") + run(-12, -11, day="2026-10-20")
        s = self.status(last_week + this_week, equity=317, max_losing_streak=0, max_drawdown_pct=0,
                        reset_at="2026-10-20 23:00")
        w = self.item(s, "weekly")
        self.assertEqual(w["used"], 33.0)
        self.assertEqual(w["limit"], 25.36)                                  # 8 % of $317
        self.assertEqual(s["tripped"]["key"], "weekly")
        self.assertIn("Monday", s["tripped"]["reason"])
        self.assertFalse(s["needs_reset"])                                   # it clears by itself next week

    def test_zero_turns_each_stop_off(self):
        s = self.status(run(-50, -50, -50, -50, -50), equity=150, max_weekly_loss_pct=0, max_drawdown_pct=0,
                        max_losing_streak=0, today="2026-10-19")
        self.assertIsNone(s["tripped"])
        self.assertTrue(all(not i["on"] for i in s["items"]))


class Settings(DeskCase):
    def test_defaults_and_ranges(self):
        s = S.validate({})
        self.assertEqual((s["max_weekly_loss_pct"], s["max_drawdown_pct"], s["max_losing_streak"]), (8.0, 15.0, 0))
        for k, bad in (("max_weekly_loss_pct", 60), ("max_drawdown_pct", -1), ("max_losing_streak", 21),
                       ("max_losing_streak", True)):
            with self.assertRaises(S.SettingsError, msg=k):
                S.validate({k: bad})

    def test_they_are_rules_so_changing_one_starts_a_new_rule_set(self):
        for k in ("max_weekly_loss_pct", "max_drawdown_pct", "max_losing_streak"):
            self.assertIn(k, stamp.RULE_KEYS)
        self.assertNotEqual(stamp.rules_hash(S.validate({})), stamp.rules_hash(S.validate({"max_losing_streak": 3})))

    def test_none_of_them_changes_how_big_a_trade_is(self):
        a, b = S.validate({}), S.validate({"max_weekly_loss_pct": 0, "max_drawdown_pct": 0, "max_losing_streak": 9})
        self.assertEqual(S.limits(a, 400), S.limits(b, 400))
        self.assertEqual(S.skills_config(a, 400), S.skills_config(b, 400))


class Engine(DemoCase):
    def losses(self, d, n, each_stop=11.2):
        for _ in range(n):
            tid = d.j.open_trade("F", 1, 12.0, each_stop, 13.6, d.src.now())
            d.exit(tid, each_stop, "stop")

    def test_a_losing_run_halts_new_entries_and_reset_clears_it(self):
        d = self.desk(max_trades_per_day=10, max_daily_loss_pct=30, max_losing_streak=3, max_drawdown_pct=0)
        self.losses(d, 3)
        halt = d.halt_reason(S.load(), d.src.now())
        self.assertIn("3 losing trades in a row", halt)
        self.assertTrue(d.state()["stops"]["needs_reset"])
        d.scan()
        sig = next((s for s in d.j.signals()), None)
        self.assertIsNone(sig)                                               # the scan was halted
        r = d.reset_stops()
        self.assertIsNone(r["tripped"])
        self.assertIsNone(d.halt_reason(S.load(), d.src.now()))
        kinds = [(e["kind"], e["message"]) for e in d.j.events(limit=50)]
        self.assertTrue(any(k == "stops" and "in a row" in m for k, m in kinds))     # the trip was logged
        self.assertTrue(any(k == "stops" and "reset" in m.lower() for k, m in kinds))  # and the reset

    def test_taking_a_signal_by_hand_is_refused_while_a_stop_is_on(self):
        d = self.desk(max_trades_per_day=10, max_daily_loss_pct=30, max_losing_streak=2, max_drawdown_pct=0,
                      min_edge_points=-50)
        d.scan()
        sig = next(s for s in d.j.signals(statuses=("ready",)))
        self.losses(d, 2)
        with self.assertRaises(DeskError):
            d.take(sig["id"], 1, sig["payload"]["limit"])

    def test_the_trip_is_logged_once(self):
        d = self.desk(max_trades_per_day=10, max_daily_loss_pct=30, max_losing_streak=2, max_drawdown_pct=0)
        self.losses(d, 2)
        for _ in range(4):
            d.halt_reason(S.load(), d.src.now())
            d.state()
        self.assertEqual(sum(1 for e in d.j.events(limit=50) if e["kind"] == "stops"), 1)

    def test_drawdown_stop_from_the_default_settings(self):
        d = self.desk(max_trades_per_day=20, max_daily_loss_pct=30)
        self.losses(d, 8)                                                    # 8 x $0.80 = $6.40: far from 15 %
        self.assertIsNone(d.halt_reason(S.load(), d.src.now()))
        tid = d.j.open_trade("F", 25, 12.0, 9.5, 13.6, d.src.now())
        d.exit(tid, 9.5, "stop")                                             # -$62.50: $68.90 of a $400 peak
        self.assertIn("from your best point", d.halt_reason(S.load(), d.src.now()))

    def test_the_route(self):
        d = self.desk(max_trades_per_day=10, max_daily_loss_pct=30, max_losing_streak=2, max_drawdown_pct=0)
        self.losses(d, 2)
        httpd = serve(d, None, 0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        req = urllib.request.Request("http://127.0.0.1:%d/api/stops/reset" % httpd.server_address[1], data=b"{}",
                                     headers={"Content-Type": "application/json", "X-Desk": "1"})
        with urllib.request.urlopen(req, timeout=30) as r:
            out = json.loads(r.read())
        self.assertIsNone(out["tripped"])
        self.assertIsNone(d.halt_reason(S.load(), d.src.now()))


class AutoPaperStops(AutoBase):
    def test_auto_paper_places_nothing_while_a_stop_is_on(self):
        S.update({"max_losing_streak": 2, "max_daily_loss_pct": 30, "max_trades_per_day": 10})
        for _ in range(2):
            tid = self.d.j.open_trade("F", 1, 12.0, 11.2, 13.6, self.d.src.now())
            self.d.j.close_trade(tid, 11.2, "stop", self.d.src.now(), "2026-10-20")
        self.assertEqual(self.tick()["placed"], 0)
        self.assertEqual(self.d.auto.book(), {})
        self.d.reset_stops()
        self.assertEqual(self.tick()["placed"], 1)
