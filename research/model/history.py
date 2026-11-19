#!/usr/bin/env python3
"""Free daily history for the model lab: Yahoo's chart endpoint, no key, cached forever.

Daily bars back to each fund's launch, with dividends and splits, so every
return here is a total return (SPY included). Run once; the lab reads the cache.
"""
import datetime as dt
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

HOME = Path(__file__).resolve().parent
CACHE = HOME / 'cache'
NY = ZoneInfo('America/New_York')
# ETFs only: they cannot drop out of the sample the way failed stocks do (little survivorship bias).
SYMBOLS = ['SPY', 'QQQ', 'IWM', 'DIA', 'MDY', 'XLK', 'XLF', 'XLE', 'XLV', 'XLY', 'XLP', 'XLI', 'XLB', 'XLU',
           'TLT', 'IEF', 'SHY', 'GLD', 'SH', 'PSQ', '^VIX', '^IRX']
HOSTS = ('query2.finance.yahoo.com', 'query1.finance.yahoo.com')
# Fresh test sets for round 3: never used in development (2000-2020 SPY ETFs).
# Price indices: no dividends, compared only with buy-and-hold of the same index.
EXTRA = ['^GSPC', '^IRX', '^GDAXI', '^N225', '^FTSE', '^HSI', '^GSPTSE', '^AXJO', '^FCHI', '^STOXX50E']
EXTRA_START = -1325635200     # 1928: the S&P 500 index and T-bill yields go back that far


def path(sym, long=False):
    return CACHE / (sym.replace('^', '_') + ('-long' if long else '-max') + '.json')


def download(sym, start=631152000):
    # range=max silently returns monthly bars; explicit dates keep them daily
    q = urllib.parse.urlencode({'period1': start, 'period2': int(time.time()), 'interval': '1d', 'events': 'div,split'})
    last = None
    for attempt in range(6):
        for host in HOSTS:
            req = urllib.request.Request('https://%s/v8/finance/chart/%s?%s' % (host, urllib.parse.quote(sym), q),
                                         headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read().decode())
            except Exception as e:          # 429 or a host down: back off, then the other host
                last = e
        time.sleep(2 ** attempt)
    raise RuntimeError('%s: %s' % (sym, last))


def bars(sym, long=False):
    """[{date, o, h, l, c, ac}] oldest first. ac is the dividend- and split-adjusted close;
    the open is adjusted by the same day's factor so open-to-close returns stay total returns."""
    res = json.loads(path(sym, long).read_text())['chart']['result'][0]
    q, ts = res['indicators']['quote'][0], res.get('timestamp') or []
    adj = ((res['indicators'].get('adjclose') or [{}])[0].get('adjclose')) or q['close']
    out = []
    for i, t in enumerate(ts):
        o, h, l, c, a = q['open'][i], q['high'][i], q['low'][i], q['close'][i], adj[i]
        if None in (o, h, l, c, a) or min(o, c) <= 0:
            continue
        f = a / c
        out.append({'date': dt.datetime.fromtimestamp(t, NY).strftime('%Y-%m-%d'),
                    'o': o * f, 'h': h * f, 'l': l * f, 'c': a, 'raw_c': c})
    return out


def main():
    CACHE.mkdir(exist_ok=True)
    for sym in SYMBOLS:
        if path(sym).exists():
            continue
        path(sym).write_text(json.dumps(download(sym)))
        b = bars(sym)
        print('%-5s %5d bars %s..%s' % (sym, len(b), b[0]['date'], b[-1]['date']), flush=True)
        time.sleep(0.4)
    for sym in EXTRA:
        if path(sym, True).exists():
            continue
        path(sym, True).write_text(json.dumps(download(sym, EXTRA_START)))
        b = bars(sym, True)
        print('%-9s %5d bars %s..%s' % (sym, len(b), b[0]['date'], b[-1]['date']), flush=True)
        time.sleep(0.4)


if __name__ == '__main__':
    main()
