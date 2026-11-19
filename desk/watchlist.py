"""The daily watchlist: your core tickers plus today's high-fluctuation movers.

Every morning before the open (9:00 AM New York, desk/scheduler.py) the desk
reads Yahoo's free stock screeners (most active, biggest gainers and losers),
keeps liquid stocks you can afford, ranks them by how much they move in a day
(average true range over the last 14 sessions, as % of the price) and puts the
top ones next to your core list. GET only, no key, nothing is ever sent.
"""
import datetime
import json
import re
import time
import urllib.parse
import urllib.request

from . import charts

SCREENS = ("most_actives", "day_gainers", "day_losers")
HOSTS = charts.HOSTS
MIN_AVG_VOLUME = 1_000_000      # shares a day: thin stocks have wide spreads and gappy candles
ATR_DAYS = 14
MAX_CANDIDATES = 40             # daily charts read per morning: be polite to a free endpoint
PAUSE = 0.3                     # seconds between daily-chart requests
TICKER = re.compile(r"[A-Z]{1,5}")
# Yahoo exchange codes -> TradingView prefixes, so the export can place a mover (desk/exports.py)
TV_EXCHANGE = {"NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NAS": "NASDAQ",
               "NYQ": "NYSE", "ASE": "AMEX", "PCX": "AMEX", "BTS": "CBOE"}


def screen(name, count=25, timeout=15):
    """One Yahoo predefined screener -> its quotes. Raises charts.ChartError when no host answers."""
    path = "/v1/finance/screener/predefined/saved?" + urllib.parse.urlencode({"scrIds": name, "count": count})
    last = "no answer"
    for host in HOSTS:
        req = urllib.request.Request("https://%s%s" % (host, path), method="GET",
                                     headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
            return ((d.get("finance") or {}).get("result") or [{}])[0].get("quotes") or []
        except (OSError, ValueError, AttributeError, IndexError) as e:
            last = str(e)
    raise charts.ChartError("Yahoo screener %s did not answer (%s)" % (name, last))


def atr_pct(bars, n=ATR_DAYS):
    """Average true range of the last n daily bars, as % of the last close. None with too few bars."""
    if len(bars) < n + 1:
        return None
    trs = [max(b["h"] - b["l"], abs(b["h"] - p["c"]), abs(b["l"] - p["c"])) for p, b in zip(bars[-n - 1:-1], bars[-n:])]
    return round(sum(trs) / n / bars[-1]["c"] * 100, 2)


def candidates(quotes_by_screen, core, blocked, min_price, max_price):
    """Screener quotes -> {symbol: {price, avg_volume, from}} worth a daily chart."""
    out = {}
    for name, quotes in quotes_by_screen.items():
        for q in quotes:
            sym = str(q.get("symbol") or "").upper()
            price, vol = q.get("regularMarketPrice"), q.get("averageDailyVolume3Month") or 0
            if (q.get("quoteType") != "EQUITY" or not TICKER.fullmatch(sym) or sym in core or sym in blocked
                    or not price or not (min_price <= price <= max_price) or vol < MIN_AVG_VOLUME):
                continue
            c = out.setdefault(sym, {"price": round(float(price), 2), "avg_volume": int(vol), "from": [],
                                     "exchange": TV_EXCHANGE.get(q.get("exchange"))})
            c["from"].append(name)
    return out


def research(st, max_price, fetch_screen=screen, fetch_daily=lambda s: charts.fetch(s, "1mo", "1d")["bars"], today=None):
    """Today's watchlist. Returns {watchlist, core, picks, errors}; picks sorted most volatile first.
    The fetchers are arguments so the tests run without a network. ATR uses finished sessions only:
    today's daily bar is still forming when the desk starts after the open."""
    today = today or datetime.datetime.now(charts.NY).strftime("%Y-%m-%d")
    core, n = list(st["core_watchlist"]), st["auto_watchlist_movers"]
    quotes, errors = {}, []
    for name in SCREENS:
        try:
            quotes[name] = fetch_screen(name)
        except Exception as e:                      # one screener down still leaves the others
            errors.append(str(e)[:160])
    cands = candidates(quotes, set(core), set(st["blocked"]), st["min_price"], max_price)
    # the busiest first, so the chart budget goes to the stocks with the most trading
    ranked = sorted(cands.items(), key=lambda kv: -kv[1]["avg_volume"])[:MAX_CANDIDATES]
    picks = []
    for i, (sym, c) in enumerate(ranked if n else []):
        if i:
            time.sleep(PAUSE)
        try:
            a = atr_pct([b for b in fetch_daily(sym) if b.get("date", "") < today])
        except Exception as e:
            errors.append("%s: %s" % (sym, str(e)[:120]))
            continue
        if a is not None:
            picks.append(dict(c, symbol=sym, atr_pct=a))
    picks.sort(key=lambda p: -p["atr_pct"])
    picks = picks[:n]
    return {"watchlist": core + [p["symbol"] for p in picks], "core": core, "picks": picks, "errors": errors}
