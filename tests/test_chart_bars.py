"""The Desk chart cleans every series before setData. Lightweight Charts 4.2.3 throws "Value is null" when two bars
share a time (the feed repeats its last print), so cleanBars must keep one row per time, in order, finite values only.
The function is read out of the real page and run under Node; the test skips when Node is not installed."""
import json
import re
import shutil
import subprocess
import unittest

from desk.paths import STATIC


def _clean_bars_source():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    start = page.index("function cleanBars(")
    end = page.index("\n", start)
    return page[start:end]


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class CleanBars(unittest.TestCase):
    def run_clean(self, rows, keys):
        js = _clean_bars_source() + f"\nprocess.stdout.write(JSON.stringify(cleanBars({json.dumps(rows)}, {json.dumps(keys)})));"
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30, check=True)
        return json.loads(out.stdout)

    def test_duplicate_last_bar_is_kept_once(self):
        rows = [{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2}, {"time": 2, "open": 2, "high": 2, "low": 2, "close": 2},
                {"time": 2, "open": 2, "high": 2, "low": 2, "close": 2}]
        got = self.run_clean(rows, ["open", "high", "low", "close"])
        self.assertEqual([r["time"] for r in got], [1, 2])

    def test_out_of_order_rows_are_sorted_and_later_duplicate_wins(self):
        rows = [{"time": 3, "value": 3}, {"time": 1, "value": 1}, {"time": 3, "value": 9}]
        self.assertEqual(self.run_clean(rows, ["value"]), [{"time": 1, "value": 1}, {"time": 3, "value": 9}])

    def test_rows_with_missing_or_non_numeric_values_are_dropped(self):
        rows = [{"time": 1, "value": None}, {"time": 2}, {"time": 3, "value": "x"}, {"time": None, "value": 1}, {"time": 4, "value": 4}]
        self.assertEqual(self.run_clean(rows, ["value"]), [{"time": 4, "value": 4}])

    def test_daily_chart_string_dates_are_kept_in_order(self):
        rows = [{"time": "2026-11-20", "value": 2}, {"time": "2026-11-19", "value": 1}, {"time": "2026-11-20", "value": 3}]
        self.assertEqual(self.run_clean(rows, ["value"]), [{"time": "2026-11-19", "value": 1}, {"time": "2026-11-20", "value": 3}])

    def test_empty_and_missing_input(self):
        self.assertEqual(self.run_clean([], ["value"]), [])
        self.assertEqual(self.run_clean(None, ["value"]), [])

    def test_load_chart_uses_the_cleaner_for_every_series(self):
        page = (STATIC / "index.html").read_text(encoding="utf-8")
        body = page[page.index("async function loadChart("):]
        body = body[:body.index("\n}\n")]
        self.assertIn('cleanBars(d.candles, ["open", "high", "low", "close"])', body)
        self.assertIn('cleanBars(d.vwap, ["value"])', body)
        self.assertNotIn("d.candles.map", body)


if __name__ == "__main__":
    unittest.main()
