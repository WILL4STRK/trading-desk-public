"""Sending today's stocks to TradingView and thinkorswim: files and links only, never orders."""
import json
import threading
import urllib.error
import urllib.request

from desk import exports
from desk import settings as S
from desk.engine import Desk
from desk.journal import Journal
from desk.server import serve
from desk.source import Demo
from tests.base import DeskCase


def fake_state(**over):
    st = {
        "now": "2026-10-19 10:15:00",
        "signals": [{"payload": {"symbol": "TSLA"}, "status": "ready"},
                    {"payload": {"symbol": "ZZZQ"}, "status": "missed"},
                    {"payload": {"symbol": "TSLA"}, "status": "skipped"}],
        "open_trades": [{"symbol": "F"}],
        "settings": {"watchlist": ["AAPL", "F", "KO"]},
        "premarket": {"hits": [{"symbol": "NVDA"}]},
    }
    st.update(over)
    return st


class Symbols(DeskCase):
    def test_signals_then_open_trades_then_watchlist_no_repeats(self):
        self.assertEqual(exports.todays_symbols(fake_state()), ["TSLA", "ZZZQ", "F", "AAPL", "KO"])

    def test_empty_day_is_just_the_watchlist(self):
        st = fake_state(signals=[], open_trades=[])
        self.assertEqual(exports.todays_symbols(st), ["AAPL", "F", "KO"])

    def test_every_default_watchlist_stock_has_an_exchange(self):
        missing = [t for t in S.DEFAULTS["watchlist"] if t not in exports.EXCHANGES]
        self.assertEqual(missing, [])
        self.assertTrue(set(exports.EXCHANGES.values()) <= {"NASDAQ", "NYSE", "AMEX"})

    def test_exchanges_match_tradingview_symbol_search_2026_11_10(self):
        # Checked one by one against TradingView's own symbol search on 2026-11-10.
        # WMT moved from NYSE to NASDAQ; WBD has no US listing there any more.
        seen = {
            "NASDAQ": "AAL AAPL AMD AMZN CSCO DKNG HOOD INTC MARA MU NVDA PLTR PYPL RIVN SMCI "
                      "SOFI TSLA MSFT META GOOGL GOOG NFLX COIN LCID WMT QQQ",
            "NYSE": "BAC CCL DAL F GM KO NIO PFE SNAP T UBER VZ CPNG DIS JPM XOM",
            "AMEX": "SPY",
        }
        expected = {s: ex for ex, names in seen.items() for s in names.split()}
        self.assertEqual(exports.EXCHANGES, expected)
        self.assertNotIn("WBD", S.DEFAULTS["watchlist"])


class Files(DeskCase):
    def test_tradingview_file_is_one_comma_separated_line_with_exchange_prefixes(self):
        text, unknown = exports.tradingview_text(["TSLA", "F", "ZZZQ"])
        self.assertEqual(text, "NASDAQ:TSLA,NYSE:F,ZZZQ\n")
        self.assertEqual(unknown, ["ZZZQ"])

    def test_thinkorswim_text_is_one_ticker_a_line(self):
        self.assertEqual(exports.thinkorswim_text(["TSLA", "F"]), "TSLA\nF\n")

    def test_chart_link_goes_to_tradingview_only(self):
        self.assertEqual(exports.chart_url("TSLA"), "https://www.tradingview.com/chart/?symbol=NASDAQ%3ATSLA")
        self.assertEqual(exports.chart_url("ZZZQ"), "https://www.tradingview.com/chart/?symbol=ZZZQ")
        self.assertEqual(exports.chart_url("BRK.B"), "https://www.tradingview.com/chart/?symbol=BRK.B")

    def test_bundle(self):
        b = exports.bundle(fake_state())
        self.assertEqual(b["date"], "2026-10-19")
        self.assertEqual(b["filename"], "trading-desk-2026-10-19.txt")
        self.assertEqual(b["count"], 5)
        self.assertEqual(b["unknown_exchange"], ["ZZZQ"])
        self.assertEqual(b["thinkorswim"].splitlines()[0], "TSLA")
        self.assertTrue(b["tradingview"].startswith("NASDAQ:TSLA,"))

    def test_movers_use_the_exchange_the_research_saw(self):
        st = fake_state(watchlist_research={"picks": [{"symbol": "ZZZQ", "exchange": "NASDAQ"},
                                                      {"symbol": "TSLA", "exchange": "NYSE"}]})
        b = exports.bundle(st)
        self.assertIn("NASDAQ:ZZZQ", b["tradingview"])
        self.assertIn("NASDAQ:TSLA", b["tradingview"])        # the checked table wins
        self.assertEqual(b["unknown_exchange"], [])
        self.assertEqual(exports.chart_urls(st)["ZZZQ"], "https://www.tradingview.com/chart/?symbol=NASDAQ%3AZZZQ")

    def test_chart_urls_cover_signals_trades_watchlist_and_premarket(self):
        urls = exports.chart_urls(fake_state())
        self.assertEqual(set(urls), {"TSLA", "ZZZQ", "F", "AAPL", "KO", "NVDA"})
        self.assertTrue(all(u.startswith("https://www.tradingview.com/chart/?symbol=") for u in urls.values()))


class OverHttp(DeskCase):
    def setUp(self):
        super().setUp()
        S.save({})
        j = Journal()
        self.addCleanup(j.close)
        self.d = Desk(j, Demo("2026-10-19 10:15"))
        self.httpd = serve(self.d, None, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def get(self, path, host=None):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), headers={"Host": host} if host else {})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_export_json(self):
        code, _, body = self.get("/api/export")
        self.assertEqual(code, 200)
        b = json.loads(body)
        self.assertEqual(b["symbols"][:3], S.DEFAULTS["watchlist"][:3])     # no signals yet: the watchlist
        self.assertEqual(b["unknown_exchange"], [])

    def test_tradingview_download(self):
        code, headers, body = self.get("/api/export/tradingview.txt")
        self.assertEqual(code, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/plain"))
        self.assertEqual(headers["Content-Disposition"], 'attachment; filename="trading-desk-2026-10-19.txt"')
        self.assertTrue(body.decode().startswith("NYSE:F,NASDAQ:SOFI,"))

    def test_state_carries_chart_links(self):
        code, _, body = self.get("/api/state")
        self.assertEqual(json.loads(body)["chart_urls"]["AAPL"], "https://www.tradingview.com/chart/?symbol=NASDAQ%3AAAPL")

    def test_export_is_local_only(self):
        self.assertEqual(self.get("/api/export", host="evil.example")[0], 403)
        self.assertEqual(self.get("/api/export/tradingview.txt", host="evil.example")[0], 403)
