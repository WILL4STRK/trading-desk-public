"""The two chart studies (thinkorswim and TradingView) must match the desk.

They are not run here (neither platform can run outside its own app). These
tests read the source text and check: the defaults equal desk/settings.py, and
neither file can place, change or cancel an order.
"""
import re
import unittest
from pathlib import Path

from desk.settings import DEFAULTS

ROOT = Path(__file__).resolve().parent.parent
TOS = ROOT / "thinkorswim" / "TradingDesk_ORB.ts"
PINE = ROOT / "tradingview" / "TradingDesk_ORB.pine"

ORDER_CALLS = ("strategy.entry", "strategy.order", "strategy.exit", "strategy.close",
               "strategy.cancel", "AddOrder")


def _code(text, comment):
    """The text with comments removed, so a comment that names an order call is fine."""
    return "\n".join(line.split(comment, 1)[0] for line in text.splitlines())


def _hhmm_int(s):
    h, m = s.split(":")
    return int(h) * 100 + int(m)


def tos_inputs():
    code = _code(TOS.read_text(), "#")
    return {k: v.strip() for k, v in re.findall(r"^\s*input\s+(\w+)\s*=\s*([^;]+);", code, re.M)}


def pine_inputs():
    code = _code(PINE.read_text(), "//")
    return {k: v.strip() for k, v in
            re.findall(r"^\s*(\w+)\s*=\s*input\.\w+\(\s*([^,)]+)", code, re.M)}


def expected():
    return {
        "orMinutes": DEFAULTS["or_minutes"],
        "targetR": DEFAULTS["target_r"],
        "chaseR": DEFAULTS["chase_r"],
        "lastEntryTime": _hhmm_int(DEFAULTS["no_entries_after"]),
        "stopAtRangeLow": DEFAULTS["stop_rule"] == "or-low",
    }


def as_value(raw):
    if raw in ("yes", "true"):
        return True
    if raw in ("no", "false"):
        return False
    return float(raw)


class StudyDefaults(unittest.TestCase):
    def test_desk_stop_rule_is_one_the_studies_can_draw(self):
        self.assertIn(DEFAULTS["stop_rule"], ("or-mid", "or-low"))

    def test_thinkorswim_defaults_match_settings(self):
        got = tos_inputs()
        for name, want in expected().items():
            self.assertIn(name, got, "thinkScript has no input %s" % name)
            self.assertEqual(as_value(got[name]), want, "thinkScript %s" % name)

    def test_tradingview_defaults_match_settings(self):
        got = pine_inputs()
        for name, want in expected().items():
            self.assertIn(name, got, "Pine has no input %s" % name)
            self.assertEqual(as_value(got[name]), want, "Pine %s" % name)


class ShortSide(unittest.TestCase):
    """The short side is drawn by the same two studies, off by default, mirrored from the long rule."""

    def test_short_side_is_off_by_default_in_both(self):
        self.assertEqual(DEFAULTS["directions"], "long")
        self.assertIs(as_value(tos_inputs()["showShorts"]), False)
        self.assertIs(as_value(pine_inputs()["showShorts"]), False)

    def test_both_trigger_on_a_close_below_the_range_low(self):
        for path, comment in ((TOS, "#"), (PINE, "//")):
            code = _code(path.read_text(), comment)
            self.assertIn("close < orLow", code, path.name)
            self.assertIn("close > orHigh", code, path.name)        # the long rule is still there

    def test_short_stop_rounds_up_and_sits_at_the_mirrored_level(self):
        tos, pine = _code(TOS.read_text(), "#"), _code(PINE.read_text(), "//")
        self.assertIn("if stopAtRangeLow then orHigh else Ceil(orMid * 100 - 0.000001) / 100", tos)
        self.assertIn("stopAtRangeLow ? orHigh : math.ceil(orMid * 100 - 0.000001) / 100", pine)
        self.assertIn("sEntry - targetR * sRisk", tos)
        self.assertIn("sEntry - targetR * sRisk", pine)

    def test_pine_has_the_breakdown_alert_and_waits_for_a_closed_candle(self):
        code = _code(PINE.read_text(), "//")
        self.assertRegex(code, r'alertcondition\(isBreakdown,\s*title\s*=\s*"Trading Desk breakdown"')
        self.assertIn("barstate.isconfirmed and close < orLow", code)
        self.assertIn("isBreakdown := showShorts", code)

    def test_nothing_short_is_drawn_unless_switched_on(self):
        tos, pine = _code(TOS.read_text(), "#"), _code(PINE.read_text(), "//")
        self.assertIn("def shortOn = showShorts and", tos)
        self.assertIn("def isBreakdown = showShorts and", tos)
        self.assertIn("shortOn = showShorts and", pine)


class StudiesDrawOnly(unittest.TestCase):
    def test_no_order_calls(self):
        for path, comment in ((TOS, "#"), (PINE, "//")):
            code = _code(path.read_text(), comment)
            for call in ORDER_CALLS:
                self.assertNotIn(call, code, "%s contains %s" % (path.name, call))

    def test_pine_is_a_v6_indicator_not_a_strategy(self):
        code = _code(PINE.read_text(), "//")
        self.assertIn("//@version=6", PINE.read_text().splitlines()[0])
        self.assertRegex(code, r"^\s*indicator\(", )
        self.assertNotRegex(code, r"^\s*strategy\(")
        self.assertNotIn("strategy.", code)

    def test_pine_has_the_named_alert(self):
        code = _code(PINE.read_text(), "//")
        self.assertRegex(code, r'alertcondition\([^)]*title\s*=\s*"Trading Desk breakout"')

    def test_pine_reads_new_york_time_and_waits_for_a_closed_candle(self):
        code = _code(PINE.read_text(), "//")
        self.assertIn('"America/New_York"', code)
        self.assertIn("barstate.isconfirmed", code)
        self.assertIn("close > orHigh", code)


if __name__ == "__main__":
    unittest.main()
