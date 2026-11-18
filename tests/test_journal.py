from desk.journal import Journal, JournalError
from tests.base import DeskCase

T = "2026-10-19 10:15"


class Cash(DeskCase):
    def setUp(self):
        super().setUp()
        self.j = Journal()
        self.addCleanup(self.j.close)
        self.j.seed(400, T)

    def test_seed_once(self):
        self.assertFalse(self.j.seed(400, T))
        self.assertEqual(self.j.cash(), 400)
        self.assertEqual(self.j.equity(), 400)

    def test_buy_moves_cash_into_the_position(self):
        self.j.open_trade("W", 3, 110.70, 108.91, 113.56, T)
        self.assertEqual(self.j.cash(), 67.90)
        self.assertEqual(self.j.positions_cost(), 332.10)
        self.assertEqual(self.j.equity(), 400)

    def test_sale_money_is_unsettled_until_the_settle_date(self):
        tid = self.j.open_trade("W", 3, 110.70, 108.91, 113.56, T)
        pnl = self.j.close_trade(tid, 113.56, "target", "2026-10-19 11:00", "2026-10-20")
        self.assertEqual(pnl, 8.58)
        self.assertEqual(self.j.equity(), 408.58)
        self.assertEqual(self.j.settled_cash("2026-10-19"), 67.90)
        self.assertEqual(self.j.unsettled("2026-10-19"), [{"settles_on": "2026-10-20", "amount": 340.68}])
        self.assertEqual(self.j.settled_cash("2026-10-20"), 408.58)

    def test_cannot_buy_with_unsettled_cash(self):
        tid = self.j.open_trade("W", 3, 110.70, 108.91, 113.56, T)
        self.j.close_trade(tid, 113.56, "target", T, "2026-10-20")
        with self.assertRaises(JournalError):
            self.j.open_trade("F", 10, 12.00, 11.80, 12.40, T)
        self.j.open_trade("F", 10, 12.00, 11.80, 12.40, "2026-10-20 10:00")

    def test_paper_buys_with_4x_the_account(self):
        from unittest import mock
        from desk import settings as S
        with mock.patch.object(S, "PAPER_LEVERAGE", 4.0):
            self.assertEqual(self.j.buying_power(T[:10]), 1600.0)
            self.j.open_trade("W", 10, 110.00, 108.00, 114.00, T)          # $1,100 on a $400 account
            self.assertEqual(self.j.buying_power(T[:10]), 500.0)
            with self.assertRaises(JournalError):
                self.j.open_trade("F", 50, 12.00, 11.80, 12.40, T)       # $600 is over what is left

    def test_loss_and_r_multiple(self):
        tid = self.j.open_trade("W", 3, 110.70, 108.91, 113.56, T)
        self.assertEqual(self.j.close_trade(tid, 108.91, "stop", T, "2026-10-20"), -5.37)
        self.assertEqual(self.j.trade(tid)["r_multiple"], -1.0)
        self.assertEqual(self.j.day("2026-10-19"), {"trades": 1, "realised": -5.37, "open_risk": 0, "open": 0})

    def test_refusals(self):
        for args in [("W", 0, 110, 108, 113), ("W", 3, 110, 111, 113), ("W", 4, 110.70, 108.91, 113.56)]:
            with self.assertRaises(JournalError):
                self.j.open_trade(*args, T)
        tid = self.j.open_trade("W", 1, 110, 108, 113, T)
        with self.assertRaises(JournalError):
            self.j.close_trade(tid, 112, "because", T, "2026-10-20")
        self.j.close_trade(tid, 112, "manual", T, "2026-10-20")
        with self.assertRaises(JournalError):
            self.j.close_trade(tid, 112, "manual", T, "2026-10-20")

    def test_deposit_withdraw_set_balance(self):
        self.j.deposit(100, T)
        self.j.withdraw(50, T)
        self.assertEqual(self.j.cash(), 450)
        with self.assertRaises(JournalError):
            self.j.withdraw(1000, T)
        self.assertEqual(self.j.set_balance(449.37, T), -0.63)
        self.assertEqual(self.j.cash(), 449.37)

    def test_stats(self):
        a = self.j.open_trade("W", 1, 100, 98, 104, T)
        self.j.close_trade(a, 104, "target", T, T[:10])
        b = self.j.open_trade("W", 1, 100, 98, 104, T)
        self.j.close_trade(b, 98, "stop", T, T[:10])
        s = self.j.stats()
        self.assertEqual((s["closed"], s["wins"], s["losses"], s["win_rate"], s["total_pnl"], s["avg_r"]),
                         (2, 1, 1, 50.0, 2.0, 0.5))


class Signals(DeskCase):
    def setUp(self):
        super().setUp()
        self.j = Journal()
        self.addCleanup(self.j.close)
        self.j.seed(400, T)

    def test_one_signal_per_symbol_setup_and_day(self):
        sid = self.j.add_signal("2026-10-19", "TSLA", "orb", "ready", {"qty": 1}, T)
        self.assertIsNotNone(sid)
        self.assertIsNone(self.j.add_signal("2026-10-19", "TSLA", "orb", "ready", {}, T))
        self.assertIsNotNone(self.j.add_signal("2026-10-20", "TSLA", "orb", "ready", {}, T))
        self.assertEqual(self.j.signal(sid)["payload"], {"qty": 1})

    def test_status_changes_only_from_the_expected_state(self):
        sid = self.j.add_signal("2026-10-19", "TSLA", "orb", "ready", {}, T)
        self.assertTrue(self.j.set_signal_status(sid, "expired", expect=("ready",)))
        self.assertFalse(self.j.set_signal_status(sid, "skipped", expect=("ready",)))
        with self.assertRaises(JournalError):
            self.j.set_signal_status(sid, "great")

    def test_taking_a_signal_marks_it_taken(self):
        sid = self.j.add_signal("2026-10-19", "W", "orb", "ready", {}, T)
        self.j.open_trade("W", 1, 110, 108, 113, T, signal_id=sid)
        self.assertEqual(self.j.signal(sid)["status"], "taken")

    def test_events_with_a_key_ring_once(self):
        self.assertIsNotNone(self.j.add_event("stop", "hit", T, "W", key="trade-1-stop"))
        self.assertIsNone(self.j.add_event("stop", "hit", T, "W", key="trade-1-stop"))
        self.assertEqual(len(self.j.events()), 1)

    def test_survives_reopen(self):
        self.j.add_signal("2026-10-19", "TSLA", "orb", "ready", {}, T)
        self.j.close()
        j2 = Journal()
        self.assertEqual(len(j2.signals()), 1)
        self.assertEqual(j2.cash(), 400)
        self.j = j2


class OutcomeGaps(DeskCase):
    def test_replays_without_candles_are_found_for_a_retry(self):
        j = Journal()
        self.addCleanup(j.close)
        today = __import__("datetime").date.today().isoformat()
        a = j.add_signal(today, "W", "orb", "weak", {"symbol": "W"}, today + " 10:00")
        b = j.add_signal(today, "F", "orb", "weak", {"symbol": "F"}, today + " 10:00")
        nodata = {"result": "no data", "entry": None, "exit": None, "exit_time": None, "r": None, "note": "offline"}
        j.set_outcome(a, today, "W", "weak", nodata, today + " 16:05")
        j.set_outcome(b, today, "F", "weak", dict(nodata, result="flat", r=0.1), today + " 16:05")
        self.assertEqual(j.outcome_gaps(), [today])
        self.assertEqual(j.outcome_gaps(today), {a})
