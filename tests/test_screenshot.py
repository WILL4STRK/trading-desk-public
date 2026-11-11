"""Reading thinkorswim screenshots: the parser on fake OCR output, the image checks, and
(when the on-device reader is built) a real round trip on sample images."""
import base64
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from desk import screenshot as SS
from desk import settings as S
from desk.engine import Desk, DeskError
from desk.journal import Journal
from desk.source import Demo
from tests.base import DeskCase

FIX = Path(__file__).resolve().parent / "fixtures"
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def line(text, x, y, w=0.2, h=0.03):
    return {"text": text, "x": x, "y": y, "w": w, "h": h}


BALANCE = [                                   # label on the left, value on the right of the same row
    line("Account Info", 0.05, 0.05),
    line("Net Liquidating Value", 0.05, 0.12), line("$1,412.35", 0.62, 0.121, 0.12),
    line("Stock Buying Power", 0.05, 0.17), line("$1,400.05", 0.62, 0.171, 0.12),
    line("Option Buying Power", 0.05, 0.22), line("$1,400.05", 0.62, 0.22, 0.12),
    line("Cash & Sweep Vehicle", 0.05, 0.27), line("$1,412.35", 0.62, 0.27, 0.12),
    line("Day Trades Left", 0.05, 0.32), line("3", 0.62, 0.32, 0.02),
]
STACKED = [                                   # label above its value (narrow panel)
    line("Net Liq", 0.1, 0.10), line("412.35", 0.1, 0.14, 0.1),
    line("Cash", 0.5, 0.10, 0.08), line("$ 400.00", 0.5, 0.14, 0.1),
]
FILLS = [                                     # filled orders, one row split into columns by OCR
    line("Filled Orders", 0.02, 0.05),
    line("01/05/26 10:16:02", 0.02, 0.10, 0.15), line("BOT", 0.20, 0.10, 0.04), line("+6", 0.26, 0.10, 0.04),
    line("KO", 0.32, 0.10, 0.05), line("@61.25", 0.40, 0.10, 0.06), line("LMT", 0.50, 0.10, 0.04),
    line("01/05/26 11:02:40 SOLD -6 KO @61.80 FILLED", 0.02, 0.15, 0.6),
    line("01/05/26 11:30:00 SOLD -1,000 BRK.B @412.10", 0.02, 0.20, 0.6),
]


class ParseTests(unittest.TestCase):
    def test_balance_on_one_row(self):
        got = {c["field"]: c["value"] for c in SS.read_balance(BALANCE)}
        self.assertEqual(got, {"net_liq": 1412.35, "buying_power": 1400.05, "cash": 1412.35})
        cash = next(c for c in SS.read_balance(BALANCE) if c["field"] == "cash")
        self.assertEqual(cash["label"], "Cash & Sweep Vehicle")

    def test_balance_with_the_value_under_the_label(self):
        got = {c["field"]: c["value"] for c in SS.read_balance(STACKED)}
        self.assertEqual(got, {"net_liq": 412.35, "cash": 400.0})

    def test_balance_ignores_other_numbers(self):
        self.assertEqual(SS.read_balance([line("Day Trades Left", 0.1, 0.1), line("3", 0.6, 0.1)]), [])
        self.assertEqual(SS.read_balance([line("Cash & Sweep Vehicle", 0.1, 0.1)]), [])   # no value found

    def test_fills_from_split_and_whole_rows(self):
        self.assertEqual(SS.read_fills(FILLS), [
            {"side": "buy", "qty": 6, "symbol": "KO", "price": 61.25},
            {"side": "sell", "qty": 6, "symbol": "KO", "price": 61.8},
            {"side": "sell", "qty": 1000, "symbol": "BRK.B", "price": 412.10},
        ])

    def test_fill_words_vary(self):
        rows = [line("Bought 3 W @ 110.75", 0.1, 0.1, 0.5), line("Sell 3 W at $108.91", 0.1, 0.2, 0.5),
                line("BUY +2 F @ 12", 0.1, 0.3, 0.5)]
        self.assertEqual(SS.read_fills(rows), [
            {"side": "buy", "qty": 3, "symbol": "W", "price": 110.75},
            {"side": "sell", "qty": 3, "symbol": "W", "price": 108.91},
            {"side": "buy", "qty": 2, "symbol": "F", "price": 12.0},
        ])

    def test_no_fills_in_a_balance_screenshot(self):
        self.assertEqual(SS.read_fills(BALANCE), [])


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_accepts_png_as_data_url_or_plain_base64(self):
        for raw in ("data:image/png;base64," + base64.b64encode(PNG_1PX).decode(), base64.b64encode(PNG_1PX).decode()):
            p = SS.save_image(raw, "balance", self.tmp, "2026-11-10 10:15")
            self.assertTrue(p.is_file())
            self.assertEqual(p.read_bytes(), PNG_1PX)
            self.assertEqual(p.suffix, ".png")
            self.assertTrue(str(p).startswith(str(self.tmp)))

    def test_refuses_what_is_not_an_image(self):
        for raw in ("", "not base64 !!!", base64.b64encode(b"%PDF-1.4 hello").decode(),
                    base64.b64encode(b"#!/bin/sh\nrm -rf /").decode(), None, 12):
            with self.assertRaises(SS.ScreenshotError):
                SS.save_image(raw, "balance", self.tmp, "2026-11-10 10:15")
        with self.assertRaises(SS.ScreenshotError):
            SS.save_image(base64.b64encode(PNG_1PX).decode(), "../../etc", self.tmp, "2026-11-10 10:15")

    def test_refuses_huge_images(self):
        big = base64.b64encode(PNG_1PX + b"\0" * (SS.MAX_BYTES + 1)).decode()
        with self.assertRaises(SS.ScreenshotError):
            SS.save_image(big, "trade", self.tmp, "2026-11-10 10:15")


class DeskScreenshots(DeskCase):
    def desk(self):
        S.save({})
        j = Journal()
        self.addCleanup(j.close)
        return Desk(j, Demo("2026-10-19 10:15"))

    def fake_ocr(self, lines):
        real = SS.ocr
        SS.ocr = lambda path: lines
        self.addCleanup(setattr, SS, "ocr", real)

    def test_balance_screenshot_suggests_but_does_not_save(self):
        d = self.desk()
        self.fake_ocr(BALANCE)
        out = d.read_screenshot("balance", base64.b64encode(PNG_1PX).decode())
        self.assertEqual(out["suggest"], 1412.35)                  # cash is the one to use
        self.assertEqual(d.state()["account"]["cash"], 400.0)       # nothing changed yet
        self.assertTrue(out["image"].startswith("screenshots/"))
        self.assertIn("Cash & Sweep Vehicle", out["text"])

    def test_trade_screenshot_matches_the_open_trade(self):
        d = self.desk()
        tid = d.j.open_trade("KO", 6, 61.25, 61.10, 61.80, "2026-10-19 10:15")
        self.fake_ocr(FILLS)
        out = d.read_screenshot("trade", base64.b64encode(PNG_1PX).decode(), trade_id=tid)
        self.assertEqual(out["suggest"], {"price": 61.8, "reason": "target"})
        self.assertEqual(len(out["fills"]), 3)

    def test_trade_screenshot_with_a_stop_fill_and_nothing_found(self):
        d = self.desk()
        tid = d.j.open_trade("KO", 6, 61.25, 61.10, 61.80, "2026-10-19 10:15")
        self.fake_ocr([line("SOLD -6 KO @61.05", 0.1, 0.1, 0.6)])
        self.assertEqual(d.read_screenshot("trade", base64.b64encode(PNG_1PX).decode(), trade_id=tid)["suggest"],
                         {"price": 61.05, "reason": "stop"})
        self.fake_ocr([line("hello", 0.1, 0.1)])
        out = d.read_screenshot("trade", base64.b64encode(PNG_1PX).decode(), trade_id=tid)
        self.assertIsNone(out["suggest"])
        self.assertIn("type", out["message"].lower())

    def test_bad_requests(self):
        d = self.desk()
        self.fake_ocr(BALANCE)
        with self.assertRaises(DeskError):
            d.read_screenshot("selfie", base64.b64encode(PNG_1PX).decode())
        with self.assertRaises(DeskError):
            d.read_screenshot("balance", "nope")
        with self.assertRaises(DeskError):
            d.read_screenshot("trade", base64.b64encode(PNG_1PX).decode(), trade_id=999)



@unittest.skipUnless(SS.reader_available(), "the on-device text reader is not built (build/ocr)")
class RealReader(unittest.TestCase):
    """The real Vision reader on two sample screenshots in tests/fixtures."""

    def test_balance_sample(self):
        got = {c["field"]: c["value"] for c in SS.read_balance(SS.ocr(FIX / "sample-balance.jpg"))}
        self.assertEqual(got.get("cash"), 1203.75)
        self.assertEqual(got.get("net_liq"), 1250.40)

    def test_fills_sample(self):
        fills = SS.read_fills(SS.ocr(FIX / "sample-filled.jpg"))
        self.assertIn({"side": "buy", "qty": 6, "symbol": "PFE", "price": 25.10}, fills)
        self.assertIn({"side": "sell", "qty": 6, "symbol": "PFE", "price": 25.40}, fills)


if __name__ == "__main__":
    unittest.main()
