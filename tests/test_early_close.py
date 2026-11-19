"""Early-close days (the day after Thanksgiving, Christmas Eve): out 5 minutes before the 1:00 PM bell."""
from unittest import mock

from desk import pipeline
from desk import settings as S
from tests.test_desk import DemoCase

EARLY = {"open": True, "closes": "13:00", "early_close": "day after Thanksgiving", "date": "2026-10-19"}


class EarlyClose(DemoCase):
    def test_for_day_moves_flat_by_and_the_entry_cutoff(self):
        st = S.validate({})
        day = S.for_day(st, "13:00")
        self.assertEqual((day["flat_by"], day["no_entries_after"]), ("12:55", "12:30"))
        self.assertEqual(S.for_day(st, "16:00"), st)                 # a normal day is unchanged
        self.assertEqual(S.for_day(st, None), st)                    # no clock: unchanged
        self.assertEqual(S.for_day(dict(st, flat_by="12:00", no_entries_after="11:00"), "13:00")["flat_by"], "12:00")

    def test_no_new_entries_in_the_last_half_hour_of_an_early_day(self):
        d = self.desk()
        with mock.patch.object(d, "clock", return_value=EARLY):
            self.assertIsNone(d.halt_reason(S.load(), "2026-11-27 12:25"))
            self.assertIn("this late", d.halt_reason(S.load(), "2026-11-27 12:30"))

    def test_a_scan_on_an_early_day_plans_to_be_out_by_12_55(self):
        d = self.desk()
        seen = []

        def evaluate(symbol, st, journal, source, hit=None):
            seen.append(st["flat_by"])
            return None, "test"
        with mock.patch.object(d, "clock", return_value=EARLY), mock.patch.object(pipeline, "evaluate", evaluate):
            d.scan()
        self.assertTrue(seen)
        self.assertEqual(set(seen), {"12:55"})
