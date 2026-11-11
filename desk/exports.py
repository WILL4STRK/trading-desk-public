"""Hand today's stocks to TradingView and thinkorswim as plain text you import yourself.

Nothing here talks to TradingView, thinkorswim or any broker. It builds:
  - a TradingView watchlist file: one line, EXCHANGE:SYMBOL separated by commas
    (the format TradingView's "Upload list" reads),
  - thinkorswim paste text: one ticker a line (thinkorswim's watchlist Import
    can take symbols from the clipboard),
  - TradingView chart links, opened only when you click one.
"""
from urllib.parse import quote

# Where each default watchlist stock is listed, for TradingView's EXCHANGE:SYMBOL form.
# Checked against TradingView's symbol search on 2026-11-10 (WMT is now NASDAQ; WBD
# has no US listing there). A stock that moves exchange shows up as "not found" in
# TradingView after import, so fix it here and in tests/test_exports.py. Stocks not in this table are exported
# without a prefix and listed in "unknown_exchange" so the dashboard can say so.
EXCHANGES = {
    "AAL": "NASDAQ", "AAPL": "NASDAQ", "AMD": "NASDAQ", "AMZN": "NASDAQ", "BAC": "NYSE",
    "CCL": "NYSE", "CSCO": "NASDAQ", "DAL": "NYSE", "DKNG": "NASDAQ", "F": "NYSE",
    "GM": "NYSE", "HOOD": "NASDAQ", "INTC": "NASDAQ", "KO": "NYSE", "MARA": "NASDAQ",
    "MU": "NASDAQ", "NIO": "NYSE", "NVDA": "NASDAQ", "PFE": "NYSE", "PLTR": "NASDAQ",
    "PYPL": "NASDAQ", "RIVN": "NASDAQ", "SMCI": "NASDAQ", "SNAP": "NYSE", "SOFI": "NASDAQ",
    "T": "NYSE", "TSLA": "NASDAQ", "UBER": "NYSE", "VZ": "NYSE",
    # common extras
    "MSFT": "NASDAQ", "META": "NASDAQ", "GOOGL": "NASDAQ", "GOOG": "NASDAQ", "NFLX": "NASDAQ",
    "COIN": "NASDAQ", "LCID": "NASDAQ", "CPNG": "NYSE", "DIS": "NYSE", "JPM": "NYSE",
    "WMT": "NASDAQ", "XOM": "NYSE", "SPY": "AMEX", "QQQ": "NASDAQ",
}

CHART = "https://www.tradingview.com/chart/?symbol="


def _add(out, sym):
    sym = str(sym or "").strip().upper()
    if sym and sym not in out:
        out.append(sym)


def todays_symbols(state):
    """Today's signals first (in the order they came), then open trades, then the watchlist."""
    out = []
    for sg in state.get("signals") or []:
        _add(out, (sg.get("payload") or {}).get("symbol") or sg.get("symbol"))
    for t in state.get("open_trades") or []:
        _add(out, t.get("symbol"))
    for sym in (state.get("settings") or {}).get("watchlist") or []:
        _add(out, sym)
    return out


def tv_symbol(sym):
    ex = EXCHANGES.get(sym)
    return "%s:%s" % (ex, sym) if ex else sym


def tradingview_text(symbols):
    """(file text, symbols with no known exchange)."""
    return ",".join(tv_symbol(s) for s in symbols) + "\n", [s for s in symbols if s not in EXCHANGES]


def thinkorswim_text(symbols):
    return "".join(s + "\n" for s in symbols)


def chart_url(sym):
    return CHART + quote(tv_symbol(sym), safe=".")


def chart_urls(state):
    syms = todays_symbols(state)
    for h in (state.get("premarket") or {}).get("hits") or []:
        _add(syms, h.get("symbol"))
    return {s: chart_url(s) for s in syms}


def bundle(state):
    syms = todays_symbols(state)
    tv, unknown = tradingview_text(syms)
    date = str(state.get("now") or "")[:10]
    return {"date": date, "count": len(syms), "symbols": syms,
            "tradingview": tv, "thinkorswim": thinkorswim_text(syms),
            "unknown_exchange": unknown, "filename": "trading-desk-%s.txt" % date}
