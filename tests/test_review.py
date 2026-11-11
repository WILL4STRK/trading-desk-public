import contextlib
import io
import os

from desk import __main__ as main
from desk import review as R
from desk import settings as S
from tests.test_desk import DemoCase


def trade(i, pnl, reason="target", r=None, entry=10.0, stop=9.5, target=11.0, exit_price=None, sid=None, qty=10, status="closed", date="2026-10-19"):
    return {"id": i, "signal_id": sid, "date": date, "symbol": "T%d" % i, "qty": qty, "entry_price": entry, "stop": stop,
            "target": target, "status": status, "exit_price": exit_price, "exit_at": "%s 11:%02d" % (date, i),
            "exit_reason": reason if status == "closed" else None, "pnl": pnl if status == "closed" else None,
            "r_multiple": r}


class Numbers(DemoCase):
    def test_results(self):
        rows = [trade(1, 10, r=2), trade(2, -5, "stop", r=-1), trade(3, -5, "stop", r=-1), trade(4, 10, r=2), trade(5, 0, "flat", r=0),
                trade(6, None, status="open")]
        r = R.results(rows)
        self.assertEqual((r["closed"], r["wins"], r["losses"], r["even"]), (5, 2, 2, 1))
        self.assertEqual((r["win_rate"], r["total"], r["per_trade"], r["avg_r"]), (40.0, 10.0, 2.0, 0.4))
        self.assertEqual((r["avg_win"], r["avg_loss"], r["payoff"]), (10.0, -5.0, 2.0))
        self.assertEqual((r["max_drawdown"], r["losing_run"]), (-10.0, 2))      # +10, then two -5 in a row
        self.assertEqual(R.by_exit(rows), {"target": {"n": 2, "total": 20.0}, "stop": {"n": 2, "total": -10.0}, "flat": {"n": 1, "total": 0.0}})

    def test_nothing_recorded(self):
        r = R.results([])
        self.assertEqual((r["closed"], r["win_rate"], r["total"], r["max_drawdown"], r["payoff"]), (0, None, 0, 0.0, None))
        self.assertEqual(R.standing(r)["label"], "No closed trades yet")

    def test_too_few_trades_gets_no_verdict(self):
        few = R.standing(R.results([trade(i, 10, r=2) for i in range(1, 20)]))
        self.assertEqual((few["enough"], few["label"]), (False, "Too few to judge"))
        self.assertIn("Only 19 closed trades", few["note"])
        self.assertEqual(R.standing(R.results([trade(i, 10, r=2) for i in range(1, 21)]))["label"], "Ahead of break-even")
        self.assertEqual(R.standing(R.results([trade(i, -5, "stop", r=-1) for i in range(1, 21)]))["label"], "Behind break-even")
        self.assertEqual(R.standing(R.results([trade(i, 0, "flat", r=0) for i in range(1, 21)]))["label"], "About break-even")

    def test_plan_against_what_happened(self):
        sigs = {1: {"id": 1, "status": "taken", "payload": {"limit": 10.00, "qty": 10, "stamp": {"rules": "aaaa1111"}}},
                2: {"id": 2, "status": "taken", "payload": {"limit": 10.00, "qty": 10, "stamp": {"rules": "bbbb2222"}}}}
        rows = [trade(1, -5.5, "stop", entry=10.02, exit_price=9.47, sid=1),      # paid 2c over, stop slipped 3c
                trade(2, 10, "target", entry=9.98, exit_price=11.0, sid=2, qty=5),
                trade(3, 1, "manual", exit_price=10.1)]
        p = R.plan_vs_actual(rows, sigs)
        self.assertEqual((p["from_signals"], p["avg_fill_vs_limit"], p["fills_over_limit"], p["took_fewer_shares"]), (2, 0.0, 1, 1))
        self.assertEqual((p["stop_exits"], p["avg_stop_vs_plan"], p["target_exits"], p["avg_target_vs_plan"], p["closed_early"]),
                         (1, -0.03, 1, 0.0, 1))
        groups = {g["rules"]: g["closed"] for g in R.by_rules(rows, sigs)}
        self.assertEqual(groups, {"aaaa1111": 1, "bbbb2222": 1, "unstamped": 1})

    def test_day_summary(self):
        self.assertIsNone(R.day_summary("2026-10-19", [], []))
        sigs = [{"date": "2026-10-19", "status": s} for s in ("taken", "blocked", "blocked", "expired")] + [{"date": "2026-10-16", "status": "ready"}]
        text = R.day_summary("2026-10-19", [trade(1, 4.68, r=0.6)], sigs)
        self.assertEqual(text, "Day summary 2026-10-19: 4 signals (1 taken, 1 expired, 2 blocked). You took 1 trade. Closed 1: +$4.68 (+0.60R a trade).")
        self.assertIn("No trades taken. That is a fine day.", R.day_summary("2026-10-19", [], sigs))
        self.assertIn("Still marked open: T2", R.day_summary("2026-10-19", [trade(2, None, status="open")], sigs))


class OnTheDesk(DemoCase):
    def taken(self, d):
        d.scan()
        sig = next(s for s in d.j.signals(statuses=("ready",)) if s["symbol"] == "W")
        p = sig["payload"]
        return d.take(sig["id"], p["qty"], p["limit"]), p

    def test_review_reads_the_record_and_changes_nothing(self):
        d = self.desk()
        tid, p = self.taken(d)
        d.exit(tid, p["target"], "target")
        d.change_settings({"target_r": 3})
        before = (d.j.cash(), len(d.j.trades()), len(d.j.events(limit=1000)))
        rv = d.review()
        self.assertEqual((d.j.cash(), len(d.j.trades()), len(d.j.events(limit=1000))), before)
        s = rv["summary"]
        self.assertEqual((rv["mode"], s["results"]["closed"], s["results"]["wins"], s["standing"]["label"]), ("paper", 1, 1, "Too few to judge"))
        self.assertEqual((s["plan"]["from_signals"], s["plan"]["avg_fill_vs_limit"], s["plan"]["target_exits"]), (1, 0.0, 1))
        self.assertEqual(s["signals"]["taken"], 1)
        self.assertEqual(len(s["by_rules"]), 1)
        self.assertEqual(s["by_rules"][0]["first_seen"], "2026-10-19 10:15")
        self.assertEqual(s["settings_changes"][0]["message"], "Setting changed: target_r from 2 to 3.")
        for line in ("Trading Desk review: PAPER record", "RESULTS (1 closed, 0 open)", "Too few to judge", "PLAN AGAINST WHAT HAPPENED",
                     "BY RULE SET", "Setting changed: target_r from 2 to 3.", "Paper fills are simulated"):
            self.assertIn(line, rv["text"])

    def test_the_day_summary_is_logged_once_after_the_close(self):
        d = self.desk()
        tid, p = self.taken(d)
        d.exit(tid, p["stop"], "stop")
        d.monitor()
        self.assertEqual([e for e in d.j.events() if e["kind"] == "summary"], [])      # 10:15: the day is not over
        d.src.set_now("2026-10-19 16:01")
        d.monitor()
        d.monitor()
        got = [e["message"] for e in d.j.events(limit=200) if e["kind"] == "summary"]
        self.assertEqual(len(got), 1)
        self.assertIn("You took 1 trade. Closed 1: -$", got[0])

    def test_the_command_prints_each_record_that_exists(self):
        d = self.desk()
        tid, p = self.taken(d)
        d.exit(tid, p["target"], "target")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main.main(["review", "--demo"]), 0)
        text = out.getvalue()
        self.assertIn("Trading Desk review: PAPER record", text)
        self.assertNotIn("LIVE record", text)                       # no live journal yet, and the command must not create one
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "journal-live.db")))
        d.set_mode("live", "LIVE")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main.main(["review", "--demo"])
        self.assertIn("Trading Desk review: LIVE record", out.getvalue())
        self.assertIn("RESULTS (0 closed, 0 open)", out.getvalue())
