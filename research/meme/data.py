"""Model 4's backtest data (docs/MEME.md sections 1 and 5): the universe is every token on the DexScreener discovery
feeds when this runs, and each token's highest-liquidity pool gets about 7 days of GeckoTerminal 5-minute candles.
Read-only GETs, no keys. Everything is cached under research/meme/cache/ (git-ignored) and never refreshed on purpose,
so the backtest always reruns on the same snapshot.

Run: python3 research/meme/data.py   (universe once, then candles for every pool; about 9 GeckoTerminal calls a minute, 1 to 2 hours)
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from desk import memerules  # noqa: E402

CACHE = Path(__file__).resolve().parent / "cache"
DS = "https://api.dexscreener.com"
GT = "https://api.geckoterminal.com/api/v2/networks/%s/pools/%s/ohlcv/minute"
CHAINS = {"solana": "solana", "base": "base", "bsc": "bsc", "ethereum": "eth"}   # DexScreener id -> GeckoTerminal id
FEEDS = ["token-boosts/latest/v1", "token-boosts/top/v1", "token-profiles/latest/v1",
         "token-profiles/recent-updates/v1", "community-takeovers/latest/v1", "ads/latest/v1"]
DAYS = 7
_last = {}


def _get(url, gap):
    """GET JSON, at most one call per `gap` seconds per host, backing off on 429 and 5xx. 404 -> None."""
    host = url.split("/")[2]
    for wait in (0, 20, 20, 30, 30, 60, 60, 120, 120):
        time.sleep(max(wait, _last.get(host, 0) + gap - time.time()))
        _last[host] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None
            print("  %s %s, waiting" % (time.strftime("%H:%M:%S"), e.code), file=sys.stderr, flush=True)
            last = e
        except Exception as e:  # noqa: BLE001 - network blip: wait and try again
            last = e
    raise RuntimeError("no answer from %s: %s" % (url, last))


def _ds(path):
    return _get(DS + "/" + path, 1.2)          # feeds allow 60/min


def universe(fetch=True):
    """{'fetched': unix s, 'tokens': [{chain, token, tags, pool, pool_side, created_ms, liq_usd, dead}]}, cached."""
    f = CACHE / "universe.json"
    if f.exists():
        return json.loads(f.read_text())
    if not fetch:
        raise FileNotFoundError(f)
    fetched = int(time.time())
    seen = {}                                  # (chain, lower addr) -> {chain, token, tags}

    def add(chain, addr, tag):
        if chain in CHAINS and addr:
            k = (chain, addr.lower())
            seen.setdefault(k, {"chain": chain, "token": addr, "tags": []})
            if tag not in seen[k]["tags"]:
                seen[k]["tags"].append(tag)

    for p in FEEDS:
        for x in _ds(p) or []:
            add(x.get("chainId"), x.get("tokenAddress"), p.split("/")[0] + "/" + p.split("/")[1])
    for m in _ds("metas/trending/v1") or []:
        for pr in (_ds("metas/meta/v1/" + m["slug"]) or {}).get("pairs") or []:
            add(pr.get("chainId"), (pr.get("baseToken") or {}).get("address"), "meta:" + m["slug"])
    by_chain = {}
    for v in seen.values():
        by_chain.setdefault(v["chain"], []).append(v)
    for chain, toks in by_chain.items():
        for k in range(0, len(toks), 30):
            batch = toks[k:k + 30]
            pairs = _get(DS + "/tokens/v1/%s/%s" % (chain, ",".join(t["token"] for t in batch)), 0.25) or []
            for t in batch:
                a, best = t["token"].lower(), None
                for p in pairs:
                    side = "base" if p["baseToken"]["address"].lower() == a else \
                        "quote" if p["quoteToken"]["address"].lower() == a else None
                    liq = (p.get("liquidity") or {}).get("usd")
                    if side and (best is None or (liq or 0) > (best[1] or 0)):
                        best = (p, liq, side)
                if best:
                    p, liq, side = best
                    t.update(pool=p["pairAddress"], pool_side=side, created_ms=p.get("pairCreatedAt"), liq_usd=liq,
                             dex=p.get("dexId"), symbol=p["baseToken" if side == "base" else "quoteToken"]["symbol"],
                             dead=(liq or 0) < memerules.DEAD_LIQ)
    doc = {"fetched": fetched, "tokens": sorted(seen.values(), key=lambda t: (t["chain"], t["token"].lower()))}
    CACHE.mkdir(exist_ok=True)
    f.write_text(json.dumps(doc, indent=1))
    return doc


def candles(t, fetch=True):
    """{'fetched': unix s, 'bars': [[ts, o, h, l, c, usd_vol], ...] oldest first}, about DAYS back, cached.
    A pool GeckoTerminal does not know is cached with no bars."""
    f = CACHE / "bars" / ("%s_%s.json" % (t["chain"], t["pool"]))
    if f.exists():
        return json.loads(f.read_text())
    if not fetch:
        raise FileNotFoundError(f)
    fetched, rows, before = int(time.time()), {}, None
    for _ in range(2):                          # 2 x 1000 five-minute bars, about 7 days on a busy pool
        url = (GT % (CHAINS[t["chain"]], t["pool"])) + "?aggregate=5&limit=1000&currency=usd&token=%s" % t["pool_side"]
        if before:
            url += "&before_timestamp=%d" % before
        d = _get(url, 6.5)                     # GeckoTerminal answers 429 above about 10/min (seen 2026-11-20)
        page = ((d or {}).get("data") or {}).get("attributes", {}).get("ohlcv_list") or []
        for r in page:
            rows[int(r[0])] = [int(r[0])] + [float(x) for x in r[1:6]]
        if len(page) < 1000 or min(r[0] for r in page) < fetched - DAYS * 86400:
            break
        before = int(min(r[0] for r in page))
    doc = {"fetched": fetched, "bars": [rows[k] for k in sorted(rows)]}
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(doc))
    return doc


if __name__ == "__main__":
    u = universe()
    toks = [t for t in u["tokens"] if t.get("pool")]
    print("universe: %d tokens, %d with a pool, %d dead (liq < $%.0f), %d dead because liquidity is missing"
          % (len(u["tokens"]), len(toks), sum(t["dead"] for t in toks), memerules.DEAD_LIQ,
             sum(t["liq_usd"] is None for t in toks)), flush=True)
    for n, t in enumerate(toks, 1):
        try:
            c = candles(t)
        except RuntimeError as e:              # not cached: a rerun tries it again
            print("skipped for now: %s" % e, flush=True)
            continue
        if n % 20 == 0 or n == len(toks):
            print("%d/%d pools (last: %s %s, %d bars)" % (n, len(toks), t["chain"], t["symbol"], len(c["bars"])),
                  flush=True)
