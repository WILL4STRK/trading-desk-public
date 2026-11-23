"""The dashboard's web server. Standard library only, this computer only.

It listens on 127.0.0.1. Requests must come with a localhost Host header, and
anything that changes data must carry the X-Desk header, which a page on
another website cannot send without a preflight this server never grants.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import meme
from . import memebot
from . import memeterminal
from . import settings as S
from .engine import DeskError
from .paths import STATIC

# Scripts only from this desk and Jupiter's Plugin host (the Terminal tab's swap widget). No inline script.
# Jupiter's widget talks to its own APIs (*.jup.ag) and a Solana RPC, fetches its CSS and Google Fonts' stylesheet,
# and shows token logos from many hosts.
CSP = ("default-src 'self'; script-src 'self' https://plugin.jup.ag; "
       "style-src 'self' 'unsafe-inline' https://plugin.jup.ag https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob: https:; "
       "connect-src 'self' https://*.jup.ag https://fonts.googleapis.com https://api.mainnet-beta.solana.com "
       "wss://api.mainnet-beta.solana.com; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; "
       # TradingView charts are cross-origin frames: they cannot read or call this desk
       "frame-src https://www.tradingview-widget.com https://s.tradingview.com")

TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".woff2": "font/woff2",
         ".txt": "text/plain; charset=utf-8"}


def make_handler(desk, scheduler=None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TradingDesk/1"

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", CSP)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _local(self):
            host = (self.headers.get("Host") or "").split(":")[0].lower()
            if host not in ("127.0.0.1", "localhost"):
                self._send(403, {"error": "local only"})
                return False
            return True

        def do_GET(self):
            if not self._local():
                return
            u = urlparse(self.path)
            try:
                if u.path == "/api/state":
                    return self._send(200, desk.state())
                if u.path == "/api/earnings":
                    return self._send(200, desk.earnings((parse_qs(u.query).get("symbol") or [""])[0]))
                if u.path == "/api/congress":
                    return self._send(200, desk.congress(refresh="refresh" in parse_qs(u.query)))
                if u.path == "/api/chart":                  # read-only: candles, VWAP and levels for the Desk tab
                    q = parse_qs(u.query)
                    return self._send(200, desk.chart((q.get("symbol") or [""])[0], (q.get("interval") or ["5m"])[0]))
                if u.path == "/api/graph":                  # read-only: made or lost over time, every paper account
                    return self._send(200, desk.graph())
                if u.path == "/api/benchmarks":             # read-only: the bot against the S&P 500 and friends
                    return self._send(200, desk.benchmarks())
                if u.path == "/api/review":                 # read-only: results and the review report
                    return self._send(200, desk.review())
                if u.path == "/api/meme":                   # read-only: model 4, the memecoin paper accounts
                    return self._send(200, meme.view())
                if u.path == "/api/meme/chart":             # read-only: one coin's candles (GeckoTerminal) and levels
                    q = parse_qs(u.query)
                    try:
                        return self._send(200, meme.chart((q.get("key") or [""])[0], (q.get("ivl") or ["5m"])[0]))
                    except meme.MemeError as e:
                        return self._send(400, {"error": str(e)})
                if u.path == "/api/meme/terminal":          # read-only: coins that failed a check, the ticker, your log
                    return self._send(200, memeterminal.view())
                if u.path == "/api/memebot":                # read-only: the separate memebot paper loop's files
                    return self._send(200, memebot.status())
                if u.path == "/api/export":                 # read-only: text for TradingView and thinkorswim
                    return self._send(200, desk.exports())
                if u.path == "/api/export/tradingview.txt":
                    b = desk.exports()
                    return self._send(200, b["tradingview"].encode(), "text/plain; charset=utf-8",
                                      {"Content-Disposition": 'attachment; filename="%s"' % b["filename"]})
            except (DeskError, S.SettingsError) as e:
                return self._send(400, {"error": str(e)})
            name = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
            f = (STATIC / name).resolve()
            if STATIC.resolve() not in f.parents or not f.is_file():
                return self._send(404, {"error": "not found"})
            self._send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))

        def do_POST(self):
            if not self._local():
                return
            if self.headers.get("X-Desk") != "1" or "application/json" not in (self.headers.get("Content-Type") or ""):
                return self._send(403, {"error": "missing desk header"})
            limit = 17 * 1024 * 1024 if urlparse(self.path).path == "/api/screenshot" else 100000
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > limit:
                    return self._send(413, {"error": "That is too large. A screenshot must be under 12 MB."})
                body = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._send(400, {"error": "bad JSON"})
            path = urlparse(self.path).path
            try:
                if path == "/api/paper/reset":            # the dashboard: start the practice account over
                    desk.paper.reset()
                    out = {}
                elif path == "/api/paper/buy":            # the chart's Buy button: a pretend fill, paper mode only
                    out = desk.paper_buy(body.get("symbol"), body.get("qty"))
                elif path == "/api/meme/reset":            # start the memecoin practice account over; the record stays
                    if body.get("sure") is not True:
                        raise DeskError("Starting the memecoin account over needs sure: true.")
                    try:
                        meme.reset()
                    except meme.MemeError as e:
                        return self._send(409, {"error": str(e)})
                    out = {}
                elif path == "/api/meme/manual":           # a swap you signed in your wallet; the desk only logs it
                    try:
                        out = {"logged": memeterminal.record_manual(body)}
                    except ValueError as e:
                        return self._send(400, {"error": str(e)})
                elif path == "/api/take":
                    out = {"trade_id": desk.take(body.get("signal_id"), body.get("qty"), body.get("price"))}
                elif path == "/api/skip":
                    desk.skip(body.get("signal_id"))
                    out = {}
                elif path == "/api/exit":
                    out = {"pnl": desk.exit(body.get("trade_id"), body.get("price"), body.get("reason"))}
                elif path == "/api/cash":
                    desk.cash_move(body.get("kind"), body.get("amount"))
                    out = {}
                elif path == "/api/settings":
                    out = {"settings": desk.change_settings(body.get("changes") or {})}
                elif path == "/api/stops/reset":          # you read the record; the losing run and drawdown restart
                    out = desk.reset_stops()
                elif path == "/api/notify/test":          # one sample phone alert, from the saved settings
                    desk.test_alert()
                    out = {}
                elif path == "/api/notify/test-meme":     # one sample memecoin paper alert, from the saved settings
                    desk.test_meme_alert()
                    out = {}
                elif path == "/api/mode":                 # paper or live; live needs the word typed
                    out = {"mode": desk.set_mode(body.get("mode"), body.get("confirm"))}
                elif path == "/api/scan":
                    if scheduler:
                        scheduler.scan_now()
                        out = {"queued": True}
                    else:
                        out = {"scan": desk.scan()}
                elif path == "/api/screenshot":           # read only; nothing is recorded until Save
                    out = desk.read_screenshot(body.get("kind"), body.get("image"), body.get("trade_id"))
                elif path == "/api/seen":
                    try:
                        upto = int(body.get("id") or 0)
                    except (TypeError, ValueError):
                        return self._send(400, {"error": "id must be a number"})
                    desk.j.mark_seen(upto)
                    out = {}
                else:
                    return self._send(404, {"error": "not found"})
            except (DeskError, S.SettingsError) as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:                          # never drop the connection without saying so
                return self._send(500, {"error": "The desk hit an error (%s). Nothing was changed at your broker: "
                                                 "the desk never places real orders." % type(e).__name__})
            out["ok"] = True
            self._send(200, out)

    return Handler


def serve(desk, scheduler=None, port=8787):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(desk, scheduler))
    httpd.daemon_threads = True
    return httpd
