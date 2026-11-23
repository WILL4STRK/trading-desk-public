"""The Memecoins candle chart is drawn by candleSvg() in the page. It is read out of the real page and run under Node
(skipped when Node is not installed). Checks: compact price tags on a phone-width chart, at least 3 price and 3 time
labels, a buy/sell legend only when marks are in view, a hover title on every mark, and a one-candle coin not crashing."""
import json
import re
import shutil
import subprocess
import unittest

from desk.paths import STATIC


def _source():
    page = (STATIC / "app.js").read_text(encoding="utf-8")
    start = page.index("function candleSvg(")
    end = page.index("\nlet MCHD", start)
    return page[start:end]


def _bars(n, t0=1_700_000_000, step=300):
    return [{"time": t0 + i * step, "open": 1 + i * .01, "high": 1.2 + i * .01, "low": .9 + i * .01, "close": 1.05 + i * .01, "volume": 5} for i in range(n)]


LEVELS = [{"kind": "entry", "title": "Bought", "price": 1.0}, {"kind": "stop", "title": "Stop", "price": .8}, {"kind": "target", "title": "Target", "price": 1.4}]


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class CandleSvg(unittest.TestCase):
    def draw(self, d, width):
        js = ("const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');"
              "const cpx = n => n == null ? '-' : n >= 1 ? Number(n).toFixed(4) : Number(n).toPrecision(4);\n" + _source()
              + f"\nprocess.stdout.write(candleSvg({json.dumps(d)}, {width}));")
        return subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=30, check=True).stdout

    @staticmethod
    def texts(svg):
        return [re.sub(r"<[^>]+>", "", t) for t in re.findall(r"<text[^>]*>.*?</text>", svg)]

    def test_phone_width_has_short_tags_and_enough_axis_labels(self):
        t0 = 1_700_000_000
        svg = self.draw({"bars": _bars(80), "levels": LEVELS, "trades": [{"time": t0 + 600, "side": "buy", "policy": "news"}, {"time": t0 + 3000, "side": "sell", "why": "stop"}]}, 390)
        txt = self.texts(svg)
        for tag in ("T 1.4000", "B 1.0000", "S 0.8000"):
            self.assertIn(tag, txt)
        self.assertGreaterEqual(len([t for t in txt if re.fullmatch(r"\d\.\d+", t)]), 3)
        self.assertGreaterEqual(len([t for t in txt if t.endswith(" UTC")]), 3)
        self.assertIn("▲ bought · ▼ sold", " ".join(txt))
        self.assertIn("<title>Bought · news", svg)
        self.assertIn("<title>Sold · stop", svg)

    def test_wide_chart_keeps_full_tag_names(self):
        self.assertIn("Target 1.4000", self.texts(self.draw({"bars": _bars(80), "levels": LEVELS, "trades": []}, 900)))

    def test_no_legend_without_marks(self):
        txt = " ".join(self.texts(self.draw({"bars": _bars(80), "levels": LEVELS, "trades": []}, 390)))
        self.assertNotIn("bought", txt)

    def test_single_candle_draws_one_time_label(self):
        txt = self.texts(self.draw({"bars": _bars(1), "levels": [], "trades": []}, 390))
        self.assertEqual(len([t for t in txt if t.endswith(" UTC")]), 1)


if __name__ == "__main__":
    unittest.main()
