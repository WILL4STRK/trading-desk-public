from desk import pipeline, rulecard
from desk import settings as S
from tests.test_desk import DemoCase


class RuleCard(DemoCase):
    def test_seven_parts_in_order_with_the_slips_own_numbers(self):
        d = self.desk(target_r=1, chase_r=0, min_edge_points=-50)
        _, sig = pipeline.evaluate("TSLA", S.load(), d.j, d.src)
        card = sig["rule"]
        self.assertEqual([c["part"] for c in card], ["Market", "Setup", "Filter", "Entry", "Exit", "Stop", "Size"])
        text = {c["part"]: c["text"] for c in card}
        self.assertIn("364.18", text["Entry"])
        self.assertIn("10:25 AM", text["Entry"])
        self.assertIn("370.04", text["Exit"])
        self.assertIn("3:55 PM", text["Exit"])
        self.assertIn("358.32", text["Stop"])
        self.assertIn("1 whole share,", text["Size"])
        self.assertIn("first 15 minutes", text["Setup"])
        self.assertTrue(text["Filter"].startswith("New entries only from 9:45 AM to 3:30 PM New York time; the Risk Manager"))

    def test_a_blocked_signal_still_gets_seven_parts(self):
        d = self.desk(risk_per_trade_pct=1, max_daily_loss_pct=2)
        status, sig = pipeline.evaluate("TSLA", S.load(), d.j, d.src)
        self.assertEqual(status, "blocked")
        self.assertEqual(len(sig["rule"]), 7)
        self.assertIn("did not fit", sig["rule"][6]["text"])
        missed = rulecard.build({"opening_range": {"high": 1.0}}, S.load())
        self.assertEqual(len(missed), 7)
