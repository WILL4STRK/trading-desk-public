"""Read-only public downloads; every response is retained in this directory's cache."""
import concurrent.futures
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
HOME = Path(__file__).resolve().parent
CACHE = HOME / 'cache'
sys.path.insert(0, str(ROOT))
from desk import charts, settings


def vendor(folder, filename):
    spec = importlib.util.spec_from_file_location('bt_' + folder.replace('-', '_'), ROOT / 'vendor/day-trader-skill' / folder / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


scan = vendor('trader-scanner', 'scan.py')
_lock = threading.Lock()
_last = 0.0


def download(name, url):
    global _last
    path = CACHE / name
    if path.exists():
        return path.read_bytes()
    CACHE.mkdir(parents=True, exist_ok=True)
    for attempt in range(4):
        with _lock:
            time.sleep(max(0, .31 - (time.monotonic() - _last)))
            _last = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json,text/plain'})
            with urllib.request.urlopen(req, timeout=25) as response:
                raw = response.read()
            tmp = path.with_suffix(path.suffix + '.tmp')
            tmp.write_bytes(raw)
            tmp.replace(path)
            return raw
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise RuntimeError('Access requires permission/key or is refused; no signup attempted: ' + url) from e
            if e.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
            time.sleep(5 * 2 ** attempt)
        except OSError:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def chart(symbol, interval, fetch=False):
    path = CACHE / (symbol + '-' + interval + '.json')
    if fetch:
        rng = {'5m': '60d', '1d': '1y', '60m': '730d'}[interval]
        url = 'https://query2.finance.yahoo.com/v8/finance/chart/' + urllib.parse.quote(symbol.replace('.', '-')) + '?' + urllib.parse.urlencode({'range': rng, 'interval': interval})
        raw = download(path.name, url)
    else:
        raw = path.read_bytes()
    doc = json.loads(raw)
    result = doc['chart']['result']
    if not result:
        raise ValueError(str(doc['chart'].get('error')))
    # Ignore Yahoo's present-day metadata prices; strategy prices come only from dated bars.
    bars = charts.parse(doc)['bars']
    for b in bars:
        if not all(math.isfinite(b[k]) for k in ('o','h','l','c','v')):
            raise ValueError('non-finite candle: ' + symbol)
        if b['v'] < 0 or b['l'] > min(b['o'],b['c']) + 1e-6 or b['h'] < max(b['o'],b['c']) - 1e-6:
            raise ValueError('invalid OHLCV candle: ' + symbol)
    return bars


def prepare_daily():
    texts = {}
    for f in ('nasdaqlisted.txt', 'otherlisted.txt'):
        texts[f] = download(f, 'https://www.nasdaqtrader.com/dynamic/SymDir/' + f).decode()
    universe, counts, created = scan.build_universe(texts)
    symbols = sorted(set(s for s, _ in universe) | set(settings.DEFAULTS['core_watchlist']) | {'SPY'})
    manifest = {'directory_created': created, 'universe_counts': counts, 'symbols': symbols,
                'source': 'Nasdaq Trader current listings; Yahoo unadjusted-close chart OHLCV', 'errors': {}}
    def one(s):
        try:
            bars = chart(s, '1d', True)
            if not bars:
                raise ValueError('empty daily history')
            return s, None
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as e:
            return s, str(e)[:180]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for i, (s, error) in enumerate(pool.map(one, symbols), 1):
            if error:
                manifest['errors'][s] = error
            if i % 100 == 0 or i == len(symbols):
                print('Daily history: %d/%d, failures %d' % (i, len(symbols), len(manifest['errors'])), flush=True)
                (CACHE / 'universe.json').write_text(json.dumps(manifest, indent=2))
    (CACHE / 'universe.json').write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == '__main__':
    prepare_daily()
