"""Rule F1 data (docs/MEME.md section 16 and 16.1). Reads only the COPY of desk data in INPUT (never the live folders).

Token selection (docs/MEME.md 16.2) uses desk data only, no price and NO liquidity filter: every token whose first sighting
has age >= 24 h and a pool address. Candles: GeckoTerminal 5-minute bars for that pool, 2 pages of 1000 at most (as research/meme/data.py),
one call per GAP seconds (15 s = 4 a minute), one process. Cached under INPUT/bars (git-ignored, never committed).

Run: python research/forecast/data.py
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

INPUT = Path(r"C:\Trading\research\forecast\input")
GT = "https://api.geckoterminal.com/api/v2/networks/%s/pools/%s/ohlcv/minute"
NETWORK = {"solana": "solana", "base": "base", "bsc": "bsc", "ethereum": "eth"}
GAP = 15.0                 # seconds between GeckoTerminal calls: 4 a minute, under the 5 a minute cap
MIN_AGE_H = 24.0
DEAD_LIQ = 1000.0
DAYS = 7
_last = [0.0]


def select_tokens(path=None):
    """Tokens to fetch, from the copied discoveries.jsonl only. Sorted by key so the order is unrelated to any outcome."""
    path = Path(path) if path else INPUT / "discoveries.jsonl"
    by = {}
    for line in path.read_text(encoding="utf8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        k = r.get("key")
        if not k or r.get("chain") not in NETWORK:
            continue
        d = by.setdefault(k, {"first": r, "last": r})
        d["last"] = r
    out = []
    for k, d in sorted(by.items()):
        f, l = d["first"], d["last"]
        age = (f.get("features") or {}).get("age_h")
        if age is None or age < MIN_AGE_H or not f.get("pool"):
            continue
        out.append({"key": k, "chain": f["chain"], "token": k.split(":", 1)[1], "pool": f["pool"], "symbol": f.get("symbol"),
                    "created_ts": f["ts"] - age * 3600, "last_liq": l.get("liq"), "dead": (l.get("liq") or 0.0) < DEAD_LIQ})
    return out


def _get(url):
    for wait in (0, 20, 30, 60, 120):
        time.sleep(max(wait, _last[0] + GAP - time.time()))
        _last[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None
            print("  %s HTTP %s, waiting" % (time.strftime("%H:%M:%S"), e.code), file=sys.stderr, flush=True)
        except Exception as e:  # noqa: BLE001 - network blip: wait and retry
            print("  %s %s, waiting" % (time.strftime("%H:%M:%S"), e), file=sys.stderr, flush=True)
    raise RuntimeError("no answer from " + url)


def bars_file(tok):
    return INPUT / "bars" / ("%s_%s.json" % (tok["chain"], tok["pool"]))


def load_bars(tok):
    f = bars_file(tok)
    return json.loads(f.read_text()) if f.exists() else None


def fetch_bars(tok):
    f = bars_file(tok)
    if f.exists():
        return json.loads(f.read_text())
    fetched, rows, before = int(time.time()), {}, None
    for _ in range(2):
        url = (GT % (NETWORK[tok["chain"]], tok["pool"])) + "?aggregate=5&limit=1000&currency=usd&token=%s" % tok["token"]
        if before:
            url += "&before_timestamp=%d" % before
        d = _get(url)
        if d is None and before:                  # page 1 was full but page 2 gave 400/404: do not cache half a history
            raise RuntimeError("page 2 missing for " + tok["pool"])
        page = ((d or {}).get("data") or {}).get("attributes", {}).get("ohlcv_list") or []
        for r in page:
            rows[int(r[0])] = [int(r[0])] + [float(x) for x in r[1:6]]
        if len(page) < 1000 or min(r[0] for r in page) < fetched - DAYS * 86400:
            break
        before = int(min(r[0] for r in page))
    doc = {"fetched": fetched, "bars": [rows[k] for k in sorted(rows)]}
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")           # atomic: a crash mid-write leaves no half file under the real name
    tmp.write_text(json.dumps(doc))
    os.replace(tmp, f)
    return doc


if __name__ == "__main__":
    toks = select_tokens()
    print("tokens selected: %d" % len(toks), flush=True)
    for n, t in enumerate(toks, 1):
        try:
            c = fetch_bars(t)
        except RuntimeError as e:
            print("skipped for now: %s" % e, flush=True)
            continue
        if n % 10 == 0 or n == len(toks):
            print("%d/%d (last %s %s, %d bars)" % (n, len(toks), t["chain"], t["symbol"], len(c["bars"])), flush=True)
