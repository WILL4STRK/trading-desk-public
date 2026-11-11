from desk import costs, pipeline
from desk import settings as S
from tests.test_desk import DemoCase


class Costs(DemoCase):
    def test_arithmetic(self):
        c = costs.after_cost(10.00, 9.80, 10.40, 10, 0.01)
        self.assertEqual((c["reward"], c["max_loss"], c["reward_risk"], c["total"]), (3.9, 2.1, 1.86, 0.1))
        self.assertEqual(costs.after_cost(10.00, 9.80, 10.40, 10, 0)["reward_risk"], 2.0)
        self.assertIsNone(costs.after_cost(10, 10, 11, 1, 0))

    def test_thin_note(self):
        self.assertIsNone(costs.thin_note({"history": {"triggered": 10, "sessions": 59}}))
        self.assertIn("only 3 times", costs.thin_note({"history": {"triggered": 3, "sessions": 59}}))
        self.assertIn("only 1 time ", costs.thin_note({"history": {"triggered": 1, "sessions": 59}}))
        self.assertIn("did not trigger once", costs.thin_note({"history": {"triggered": 0, "sessions": 59}}))
        self.assertIsNone(costs.thin_note(None))

    def test_tsla_slip_carries_both_and_status_is_unchanged(self):
        d = self.desk(target_r=1, chase_r=0, min_edge_points=-50)
        status, sig = pipeline.evaluate("TSLA", S.load(), d.j, d.src)
        self.assertEqual(status, "ready")                       # display only: same status as before
        ac = sig["after_cost"]                                  # 364.18 in, 358.32 stop, 370.04 target, 1 share
        self.assertEqual((ac["reward"], ac["max_loss"], ac["reward_risk"]), (5.85, 5.87, 1.0))
        self.assertIn("thin", sig["odds"])
        d2 = self.desk(target_r=1, chase_r=0, min_edge_points=-50, est_cost_per_share=0.5)
        _, sig2 = pipeline.evaluate("TSLA", S.load(), d2.j, d2.src)
        self.assertEqual(sig2["after_cost"]["reward_risk"], 0.84)
