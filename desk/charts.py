"""Candles for the desk's own maths (the short side, desk/shorts.py).

The vendored skills fetch and read their own charts, but the ones that find
and plan an entry are long-only, so the short side needs the candles itself.
Demo: the skills' saved 5-minute files. Live: the same free Yahoo chart
address the skills use. GET only, two fixed hosts, nothing is ever sent.
"""
import calendar
import datetime
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
HOSTS = ("query2.finance.yahoo.com", "query1.finance.yahoo.com")
OPEN_MIN, CLOSE_MIN = 9 * 60 + 30, 16 * 60


class ChartError(ValueError):
    pass


def parse(doc, symbol=""):
    """A Yahoo v8 chart document -> {symbol, prev_close, bars}. Bars with no price are dropped.
    A bar: {date, start "HH:MM", minute (after midnight, New York), o, h, l, c, v}."""
    try:
        res = doc["chart"]["result"][0]
        meta, ts = res.get("meta") or {}, res.get("timestamp") or []
        q = res["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError):
        raise ChartError("no chart data%s" % (" for %s" % symbol if symbol else ""))
    got = str(meta.get("symbol") or "").upper()
    if symbol and got and got != symbol.upper():
        raise ChartError("the chart holds %s, not %s" % (got, symbol.upper()))
    bars = []
    for i, t in enumerate(ts):
        try:
            o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
            v = (q.get("volume") or [None] * len(ts))[i]
        except (KeyError, IndexError):
            continue
        if None in (o, h, l, c) or min(o, h, l, c) <= 0:
            continue
        et = datetime.datetime.fromtimestamp(int(t), NY)
        bars.append({"date": et.strftime("%Y-%m-%d"), "start": et.strftime("%H:%M"),
                     "minute": et.hour * 60 + et.minute,
                     "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": float(v or 0)})
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    return {"symbol": got or symbol.upper(), "prev_close": float(prev) if prev else None, "bars": bars}


def fetch(symbol, rng, interval, timeout=15, prepost=False):
    """One read-only GET of Yahoo's chart for a ticker. Tries the second host when the first fails.
    prepost: include the pre-market and after-hours bars."""
    symbol = symbol.strip().upper()
    if not (0 < len(symbol) <= 7 and symbol.replace(".", "").isalpha()):
        raise ChartError("%r is not a ticker" % symbol)
    path = "/v8/finance/chart/%s?%s" % (urllib.parse.quote(symbol),
                                         urllib.parse.urlencode(dict({"range": rng, "interval": interval},
                                                                    **({"includePrePost": "true"} if prepost else {}))))
    last = "no answer"
    for host in HOSTS:
        req = urllib.request.Request("https://%s%s" % (host, path), method="GET",
                                     headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return parse(json.loads(r.read().decode("utf-8")), symbol)
        except (urllib.error.URLError, OSError, ValueError) as e:
            last = str(e)
    raise ChartError("Yahoo did not answer for %s (%s)" % (symbol, last))


def intraday(source, symbol):
    """Today's 5-minute chart. Demo reads the saved file; live asks Yahoo."""
    if source.demo:
        folder = source.chart_offline(symbol)[1]
        try:
            return parse(json.loads((folder / ("%s.json" % symbol.upper())).read_text()), symbol)
        except OSError:
            raise ChartError("no saved chart for %s in the demo files" % symbol.upper())
    return fetch(symbol, "1d", "5m")


def daily_closes(source, symbol):
    """About a year of daily closes, oldest first, or None when there are none (the demo has no daily file)."""
    if source.demo:
        return None
    return [b["c"] for b in fetch(symbol, "1y", "1d")["bars"]]


def session_bars(chart, now, bar_minutes=5):
    """The regular-session bars of the day in `now` ("YYYY-MM-DD HH:MM", New York) that have
    finished by then. A bar that is still forming is left out: it has not closed."""
    now_min = int(now[11:13]) * 60 + int(now[14:16])
    return [b for b in chart["bars"]
            if b["date"] == now[:10] and OPEN_MIN <= b["minute"] < CLOSE_MIN and b["minute"] + bar_minutes <= now_min]


def sma(values, n):
    """The average of the last n values, or None when there are fewer than n."""
    if not values or len(values) < n:
        return None
    return sum(values[-n:]) / float(n)


# ---- the Desk tab's chart (GET /api/chart) ------------------------------------
INTERVALS = {"1m": ("1d", "1m"), "5m": ("5d", "5m"), "1d": ("1y", "1d")}


def _t(b):
    """A bar's time for the chart: New York wall-clock time written as UTC seconds, so the axis reads New York."""
    return calendar.timegm(time.strptime(b["date"], "%Y-%m-%d")) + b["minute"] * 60


def vwap(bars):
    """Session VWAP per regular-session bar, restarting each day: [(bar, vwap)]."""
    out, day, pv, vol = [], None, 0.0, 0.0
    for b in bars:
        if not OPEN_MIN <= b["minute"] < CLOSE_MIN:
            continue
        if b["date"] != day:
            day, pv, vol = b["date"], 0.0, 0.0
        pv += (b["h"] + b["l"] + b["c"]) / 3 * b["v"]
        vol += b["v"]
        if vol:
            out.append((b, round(pv / vol, 4)))
    return out


def view(source, symbol, interval, or_minutes):
    """Candles, session VWAP and key levels for one ticker. Demo: the saved 5-minute file only."""
    if interval not in INTERVALS:
        raise ChartError("interval must be one of %s" % ", ".join(INTERVALS))
    if source.demo:
        ch, daily = intraday(source, symbol), []
    else:
        rng, iv = INTERVALS[interval]
        ch = fetch(symbol, rng, iv, prepost=interval != "1d")
        daily = ch["bars"] if interval == "1d" else fetch(symbol, "5d", "1d")["bars"]
    bars = ch["bars"]
    if not bars:
        raise ChartError("no candles for %s" % symbol.upper())
    day = bars[-1]["date"]
    levels = []
    pre = [b for b in bars if b["date"] == day and b["minute"] < OPEN_MIN]
    if pre and interval != "1d":
        levels += [{"name": "PM high", "price": max(b["h"] for b in pre), "kind": "pm"},
                   {"name": "PM low", "price": min(b["l"] for b in pre), "kind": "pm"}]
    prior = [b for b in daily if b["date"] < day]
    if prior:
        p = prior[-1]
        levels += [{"name": "PD high", "price": p["h"], "kind": "pd"}, {"name": "PD low", "price": p["l"], "kind": "pd"},
                   {"name": "PD close", "price": p["c"], "kind": "pd"}]
    elif ch["prev_close"] and interval != "1d":
        levels.append({"name": "PD close", "price": ch["prev_close"], "kind": "pd"})
    opening = [b for b in bars if b["date"] == day and OPEN_MIN <= b["minute"] < OPEN_MIN + or_minutes]
    if opening and interval != "1d":
        levels += [{"name": "OR high", "price": max(b["h"] for b in opening), "kind": "or"},
                   {"name": "OR low", "price": min(b["l"] for b in opening), "kind": "or"}]
    if interval == "1d":
        candles = [{"time": b["date"], "open": b["o"], "high": b["h"], "low": b["l"], "close": b["c"], "volume": b["v"]} for b in bars]
        vw = []
    else:
        candles = [{"time": _t(b), "open": b["o"], "high": b["h"], "low": b["l"], "close": b["c"], "volume": b["v"],
                    "session": OPEN_MIN <= b["minute"] < CLOSE_MIN} for b in bars]
        vw = [{"time": _t(b), "value": v} for b, v in vwap(bars)]
    return {"symbol": ch["symbol"], "interval": interval, "day": day, "candles": candles, "vwap": vw,
            "levels": [dict(l, price=round(l["price"], 4)) for l in levels]}
