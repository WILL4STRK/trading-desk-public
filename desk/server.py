"""The dashboard's web server. Standard library only, this computer only.

It listens on 127.0.0.1. Requests must come with a localhost Host header (or a
tunnel name from DESK_HOSTS, which must then log in: desk/remote.py), and
anything that changes data must carry the X-Desk header, which a page on
another website cannot send without a preflight this server never grants.
"""
import json
import os
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import remote
from . import settings as S
from .engine import DeskError
from .paths import STATIC
from .remote import COOKIE

# Names a tunnel on this computer may use: exact ("mypc.example.com") or a suffix
# (".trycloudflare.com"). Visits under these names must log in (desk/remote.py).
# The server still listens on 127.0.0.1 only.
EXTRA_HOSTS = tuple(h.strip().lower() for h in os.environ.get("DESK_HOSTS", "").split(",") if h.strip())
LOCAL_HOSTS = ("127.0.0.1", "localhost")

LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading Desk sign-in</title><style>
:root{color-scheme:dark}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#000;color:#fff;
font:16px -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}
form{background:#1C1C1E;border-radius:20px;padding:24px;width:min(340px,calc(100vw - 32px));display:grid;gap:12px}
h1{margin:0;font-size:22px}p{margin:0;color:rgba(235,235,245,.68);font-size:14px}
input{font:inherit;padding:12px;border-radius:12px;border:1px solid #3A3A3C;background:#000;color:#fff}
button{font:inherit;font-weight:600;padding:12px;border:0;border-radius:12px;background:#0071E3;color:#fff;cursor:pointer}
.err{color:#FF6961}</style></head><body><form method="post" action="/login">
<h1>Trading Desk</h1><p>Sign in to reach your desk from another computer.</p>%s
<label for="pw" class="vh" style="position:absolute;left:-9999px">Password</label>
<input id="pw" name="password" type="password" autocomplete="current-password" placeholder="Password" required autofocus>
<button type="submit">Sign in</button></form></body></html>"""


def _host_ok(host):
    return host in LOCAL_HOSTS or any(host == h or (h.startswith(".") and host.endswith(h)) for h in EXTRA_HOSTS)

TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}


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
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                             "connect-src 'self'; frame-ancestors 'none'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _remote(self):
            """Through a tunnel: a tunnel's Host name, or any Cloudflare header (cloudflared adds them)."""
            host = (self.headers.get("Host") or "").split(":")[0].lower()
            return host not in LOCAL_HOSTS or bool(self.headers.get("Cf-Connecting-Ip"))

        def _signed_in(self):
            c = SimpleCookie()
            try:
                c.load(self.headers.get("Cookie") or "")
            except CookieError:
                return False
            return COOKIE in c and remote.valid(c[COOKIE].value)

        def _local(self):
            """Lets a request through, or answers it and returns False."""
            host = (self.headers.get("Host") or "").split(":")[0].lower()
            if not _host_ok(host):
                self._send(403, {"error": "local only"})
                return False
            if not self._remote() or self._signed_in():
                return True
            if urlparse(self.path).path.startswith("/api/"):
                self._send(401, {"error": "Sign in again: open the desk's address and log in."})
            else:
                self._send(303, b"", "text/plain", {"Location": "/login"})
            return False

        def _login(self):
            addr = self.headers.get("Cf-Connecting-Ip") or self.client_address[0]
            if self.command == "GET":
                return self._send(200, (LOGIN_PAGE % "").encode(), TYPES[".html"])
            if remote.load() is None:
                msg = "No password is set. On the desk's computer run: python -m desk password"
            elif remote.locked(addr):
                msg = "Too many wrong tries. Wait ten minutes."
            else:
                n = min(int(self.headers.get("Content-Length") or 0), 4096)
                pw = (parse_qs(self.rfile.read(n).decode("utf-8", "replace")).get("password") or [""])[0]
                if remote.check(pw):
                    return self._send(303, b"", "text/plain", {
                        "Location": "/", "Set-Cookie": "%s=%s; Max-Age=%d; Path=/; HttpOnly; Secure; SameSite=Strict"
                        % (COOKIE, remote.token(), remote.SESSION_DAYS * 86400)})
                remote.failed(addr)
                msg = "Wrong password."
            self._send(401, (LOGIN_PAGE % ('<p class="err">%s</p>' % msg)).encode(), TYPES[".html"])

        def do_GET(self):
            if urlparse(self.path).path == "/login" and _host_ok((self.headers.get("Host") or "").split(":")[0].lower()):
                return self._login()
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
                if u.path == "/api/graph":                  # read-only: made or lost over time, every paper account
                    return self._send(200, desk.graph())
                if u.path == "/api/review":                 # read-only: results and the review report
                    return self._send(200, desk.review())
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
            if urlparse(self.path).path == "/login" and _host_ok((self.headers.get("Host") or "").split(":")[0].lower()):
                return self._login()
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
