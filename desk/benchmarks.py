"""The Vs market tab: the bot's return next to the S&P 500 and other popular groupings.

Each grouping is its index ETF (price only, no dividends). Returns are measured
from the close before the bot's first closed trade, so every line starts at 0%.
The bot's return is its made-or-lost over the starting balance in Settings.
Live: daily closes from the same free Yahoo address the desk already reads (GET only).
"""
import datetime
import time

from . import charts
from . import settings as S

GROUPS = [("SPY", "S&P 500"), ("QQQ", "Nasdaq 100"), ("DIA", "Dow 30"), ("IWM", "Russell 2000")]
_CACHE = {}                 # range -> (fetched at, {symbol: bars or error})
TTL = 15 * 60


def _range(start):
    """The smallest Yahoo range that reaches back to `start` (YYYY-MM-DD), with a few days spare."""
    days = (datetime.date.today() - datetime.date.fromisoformat(start)).days + 7
    for rng, n in (("1mo", 30), ("3mo", 91), ("6mo", 182), ("1y", 365), ("2y", 730), ("5y", 1826)):
        if days <= n:
            return rng
    return "max"


def _bars(rng):
    hit = _CACHE.get(rng)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    got = {}
    for sym, _ in GROUPS:
        try:
            got[sym] = charts.fetch(sym, rng, "1d")["bars"]
        except charts.ChartError as e:
            got[sym] = str(e)
    if not all(isinstance(v, str) for v in got.values()):    # cache only when Yahoo answered at least once
        _CACHE[rng] = (time.time(), got)
    return got


def compare(days, bars, balance, start):
    """Daily % returns from `start` for the bot and each grouping.
    days: [{date, pnl}] of the bot's closed trades; bars: {symbol: [{date, c}] or an error string}."""
    out = []
    for sym, name in GROUPS:
        b = bars.get(sym)
        if isinstance(b, str) or not b:
            out.append({"symbol": sym, "name": name, "error": b or "no data", "points": [], "pct": None})
            continue
        before = [x for x in b if x["date"] < start]
        base = (before[-1] if before else b[0])["c"]
        pts = [{"date": x["date"], "pct": round((x["c"] / base - 1) * 100, 2)} for x in b if x["date"] >= start]
        out.append({"symbol": sym, "name": name, "points": pts, "pct": pts[-1]["pct"] if pts else 0.0})
    # the bot: running made-or-lost on every market day and trade day, over the starting balance
    dates = sorted({p["date"] for g in out for p in g["points"]} | {d["date"] for d in days})
    by_day = {}
    for d in days:
        by_day[d["date"]] = by_day.get(d["date"], 0.0) + float(d["pnl"])
    run, pts = 0.0, []
    for d in dates:
        run += by_day.get(d, 0.0)
        pts.append({"date": d, "pct": round(run / balance * 100, 2)})
    bot = {"symbol": "BOT", "name": "Bot (auto paper)", "points": pts, "pct": pts[-1]["pct"] if pts else 0.0,
           "pnl": round(run, 2)}
    return {"start": start, "balance": balance, "bot": bot, "groups": out}


def summary(bot_days, demo=False):
    """bot_days: the bot's own closed-trade days [{date, pnl}], oldest first. Changes nothing."""
    balance = float(S.load()["starting_balance"])
    start = bot_days[0]["date"] if bot_days else (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    bars = {s: "the demo has no market data" for s, _ in GROUPS} if demo else _bars(_range(start))
    return compare(bot_days, bars, balance, start)
