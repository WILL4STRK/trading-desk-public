"""Where the clock and the market data come from.

Live: the real New York clock, and the tools fetch Yahoo's free data.
Demo: the clock is pinned to the morning the skills' fixtures were cut
(Monday 2026-10-19) and every tool reads those saved files, so the whole desk
runs with no network. Good for a first look and for the tests.
"""
import datetime
import gzip
import json
from zoneinfo import ZoneInfo

from .paths import FIXTURES, data_dir

NY = ZoneInfo("America/New_York")


class Live:
    demo = False

    def now(self):
        return datetime.datetime.now(NY).strftime("%Y-%m-%d %H:%M")

    def now_args(self):
        return []

    def news_args(self):
        return []

    def scan_offline(self):
        return []

    def chart_offline(self, symbol):
        return []

    def history_offline(self, symbol):
        return []

    def quotes_offline(self):
        return []

    def has_history(self, symbol):
        return True


class Demo:
    demo = True
    BUNDLE = FIXTURES / "trader-scanner" / "wall5m-2026-10-19.json.gz"

    def __init__(self, now="2026-10-19 10:15"):
        self._now = now
        self._charts = data_dir() / "demo-charts"

    def now(self):
        return self._now

    def set_now(self, now):
        self._now = now

    def now_args(self):
        return ["--now", self._now]

    def news_args(self):
        t = datetime.datetime.strptime(self._now, "%Y-%m-%d %H:%M").replace(tzinfo=NY)
        return ["--now", t.astimezone(datetime.timezone.utc).isoformat(), "--offline", FIXTURES / "trader-news", "--dry-run"]

    def scan_offline(self):
        return ["--offline", self.BUNDLE]

    def _unpack(self):
        """The scanner's bundle is {symbol: chart}; the entry timer wants one file a symbol."""
        if (self._charts / ".done").exists():
            return
        self._charts.mkdir(parents=True, exist_ok=True)
        with gzip.open(self.BUNDLE, "rt") as f:
            bundle = json.load(f)
        for sym, chart in bundle.items():
            (self._charts / ("%s.json" % sym)).write_text(json.dumps(chart))
        tsla = FIXTURES / "trader-entry" / "TSLA-2026-10-19-5m.json"
        (self._charts / "TSLA.json").write_text(tsla.read_text())
        (self._charts / ".done").write_text("ok")

    def chart_offline(self, symbol):
        self._unpack()
        return ["--offline", self._charts]

    def has_history(self, symbol):
        return symbol == "TSLA"   # the only 60-day file in the fixtures

    def history_offline(self, symbol):
        return ["--offline", FIXTURES / "trader-timemachine" / "TSLA-60d5m.json.gz"]

    def quotes_offline(self):
        return ["--offline", FIXTURES / "trader-alerts"]
