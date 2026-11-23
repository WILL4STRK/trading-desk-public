"""Model 4: memecoins, paper only (docs/MEME.md). Its own practice account in data/accounts/meme/.

Every run (about once a minute while the desk is up; crypto never closes, so NYSE hours do not apply):
  1. every 5 minutes, read DexScreener's discovery feeds; a token seen for the first time gets a line in
     discoveries.jsonl (time, chain, feeds, snapshot), whether or not anything ever buys it;
  2. snapshot every tracked token (DexScreener /tokens/v1, 30 a request) and close what the rules say to close;
  3. if the desk was off, replay what was open from GeckoTerminal 5-minute candles (memerules.replay);
  4. safety-screen tokens (RugCheck for Solana, GoPlus for BSC, Base and Ethereum), cached; unknown is never a pass;
  5. every 15 minutes per token, run all five policies (memerules.decide) and open a *shadow* for each that buys,
     screened or not, traded or not; closed shadows go to shadows.jsonl, which is the forward test;
  6. the paper account buys only the traded policy's picks that passed the screen.

Files, in data/accounts/meme/: model.json (accounts, tracked tokens, open shadows), discoveries.jsonl,
shadows.jsonl and screens.json. Nothing here can sign, swap or send anything: it has no wallet, no key and only
makes read-only GETs to public APIs.
"""
import datetime
import json
import logging
from os import SEEK_END
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import memerules as R
from . import memigrate as M
from . import memoon as MN
from . import memoonpilot as MP
from . import memereentry as MR
from . import memequick as QK
from . import notify
from . import settings as S
from .paths import ROOT, data_dir
from .skills import read_json, write_json

NAME = "meme"
DS = "https://api.dexscreener.com"
GT = "https://api.geckoterminal.com/api/v2"
FEEDS = ("/token-boosts/latest/v1", "/token-boosts/top/v1", "/token-profiles/latest/v1",
         "/token-profiles/recent-updates/v1", "/community-takeovers/latest/v1", "/ads/latest/v1")
GT_NET = {"solana": "solana", "base": "base", "bsc": "bsc", "ethereum": "eth"}
EVM_ID = {"bsc": 56, "base": 8453, "ethereum": 1}
FEEDS_EVERY = 300          # seconds between reads of the discovery feeds (60 a minute allowed)
SEARCH_TERMS = ("pump", "sol", "bnb", "base", "meme", "ai", "cat", "dog", "inu", "pepe", "trump", "frog")
SEARCH_PER_PASS = 3        # search terms per feed pass, rotating (the feeds alone found about 15 new coins an hour)
SEARCH_MAX_AGE_H = 24      # the search source keeps only pools created this recently
METAS_MAX = 6              # trending narratives read per feed pass
TRACK_MAX = 300
TRACK_DAYS = 7
SCREEN_TTL = 6 * 3600      # a safety report is read again after this
SCREENS_PER_RUN = 12
GT_PER_RUN = 8             # GeckoTerminal calls a run may spend (30 a minute allowed; the run is about once a minute)
MIG_RESERVE = 2            # GeckoTerminal calls a run keeps back for rule P7's one-time decisions (docs/MEME.md s.9)
GAP = 180                  # seconds without a look before an open item is replayed from candles
LIQ_PULL = 0.5             # paper: sell if the pool's liquidity falls under half of what it was at the buy
UA = "trading-desk/1 (paper research; read-only)"
SPACING = {"api.dexscreener.com": 0.25, "api.geckoterminal.com": 2.4, "api.rugcheck.xyz": 1.1,
           "api.gopluslabs.io": 2.1}

_lock, _last, _cool = threading.Lock(), {}, {}
_running = threading.Lock()
_append_lock = threading.Lock()
log = logging.getLogger(__name__)


class MemeError(Exception):
    pass


def get(url, timeout=15, wait_for_slot=True):
    """One read-only GET, spaced per host, retried with backoff on HTTP 429. None for 404."""
    host = urllib.parse.urlsplit(url).netloc
    if time.time() < _cool.get(host, 0):
        raise MemeError("%s is busy; skipped for now" % host)
    for attempt in range(4):
        with _lock:
            wait = _last.get(host, 0) + SPACING.get(host, 0.5) - time.time()
            if not wait_for_slot and wait > 0:
                raise MemeError("%s is busy; skipped for now" % host)
            _last[host] = time.time() + max(0.0, wait)
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429 and host == "api.geckoterminal.com":    # 30 a minute per address: skip, never stall a run
                _cool[host] = time.time() + 20
                _log_429(host, url)
                raise MemeError("GeckoTerminal is busy (too many requests); tried again later")
            if e.code == 429 and attempt < 3 and wait_for_slot:
                time.sleep(5 * 2 ** attempt)
                continue
            raise MemeError("%s said HTTP %s" % (host, e.code))
        except (OSError, ValueError) as e:
            raise MemeError("%s did not answer (%s)" % (host, e))
    raise MemeError("%s kept saying slow down" % host)


def _log_429(host, url):
    """One line per HTTP 429 in api_errors.jsonl, so a shared-address rate limit shows up in the record."""
    try:
        _append("api_errors.jsonl", {"at": _iso(time.time()), "ts": time.time(), "host": host, "code": 429,
                                     "path": urllib.parse.urlsplit(url).path[:120]})
    except OSError:
        pass


_real_get = get           # the tests patch get; this keeps the real one reachable for its own test


# ---- files -------------------------------------------------------------------------------------------------------

def folder():
    return data_dir() / "accounts" / NAME


def load():
    return read_json(folder() / "model.json", None) or {}


def _save(state):
    write_json(folder() / "model.json", state)


def _append(name, row):
    directory = folder()
    directory.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, separators=(",", ":")).encode("utf-8") + b"\n"
    with _append_lock, (directory / name).open("a+b") as fh:
        if fh.tell():
            fh.seek(-1, SEEK_END)
            if fh.read(1) != b"\n":
                line = b"\n" + line
        fh.write(line)


def _jsonl(name):
    rows, skipped = [], 0
    try:
        with (folder() / name).open("rb") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line.decode("utf-8")))
                except (ValueError, RecursionError):
                    skipped += 1
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("Could not read %s: %s", name, exc)
    if skipped:
        log.warning("Skipped %d malformed JSONL line(s) in %s", skipped, name)
    return rows


def _count(name):
    try:
        with open(folder() / name, "rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def research_pick():
    """(policy, passed) from the backtest's results (research/meme/REPORT.md); calm_aged if it has not run."""
    r = read_json(ROOT / "research" / "meme" / "out" / "results.json", None) or {}
    p = r.get("chosen")
    return (p, bool(r.get("passed"))) if p in R.POLICIES[1:] else ("calm_aged", None)


LEARN = ("hype", "breakout", "calm_aged", "dip_aged", "aged_liquid", "news")
# Extra practice accounts: one per rule on coins
# that passed the safety check, and "careful": any rule, strict check passed and no research filter (docs/MEME.md s.7).
BOOKS = tuple((r, (r,), "screen") for r in R.POLICIES) + (("careful", LEARN, "evidence"),
                                                           ("migration", ("migration",), "strict"),
                                                           ("moonshot", ("moonshot",), "screen"),
                                                           ("quick", ("quick",), "screen"))
DELAYED = {"migration": ("migration", "migrations.jsonl"), "migration_base": (None, "migrations.jsonl"),
           "moonshot": ("moonshot", "moonshots.jsonl"), "moonshot_base": (None, "moonshots.jsonl")}  # policy: (book, log)
PRACTICE_DRAWDOWN_PCT = 15


def _book_ok(how, sc, veto):
    if how == "screen":
        return sc.get("verdict") == "pass"
    return sc.get("verdict") == "pass" and sc.get("strict") == "pass" and veto == []


def policy(st):
    if st["meme_policy"] == "careful":
        return "careful", ("careful mode: any rule, only coins that pass the strict check and none of the research "
                           "filters; the least-bad setup in the tests (no rule made money)")
    if st["meme_policy"] == "learn":
        return "learn", "learning mode: any of the six rules, so the account trades often"
    if st["meme_policy"] != "auto":
        return st["meme_policy"], "set in Settings"
    p, ok = research_pick()
    if ok is None:
        return p, "no backtest yet; the default rule (docs/MEME.md)"
    return p, "backtest pick, %s its held-out test" % ("passed" if ok else "did not pass")


def _new_account(cash):
    return {"start": cash, "cash": cash, "positions": [], "closed": [], "curve": [], "started": None}


# ---- discovery, snapshots and screens -----------------------------------------------------------------------------

def discover(st, fetch=get, now=None, turn=0):
    """{(chain, address): [feed tags]} from the discovery feeds, only on the chains in Settings. With meme_search_new,
    also DexScreener's search for a few rotating words, keeping pools under a day old (tag "search-new"): a wider net
    for new coins, the same rules and screens. Discovery sources never change what a rule buys."""
    found = {}

    def add(chain, addr, tag):
        if chain in st["meme_chains"] and addr:
            found.setdefault((chain, addr), [])
            if tag not in found[(chain, addr)]:
                found[(chain, addr)].append(tag)

    for path in FEEDS:
        try:
            rows = fetch(DS + path) or []
        except MemeError:
            continue
        rows = rows if isinstance(rows, list) else [rows]
        tag = path.strip("/").split("/")[0].replace("token-", "") + ("-" + path.split("/")[2] if "/" in path[1:] else "")
        for r in rows:
            if isinstance(r, dict):
                add(r.get("chainId"), r.get("tokenAddress"), tag)
    try:
        metas = fetch(DS + "/metas/trending/v1") or []
    except MemeError:
        metas = []
    for m in (metas if isinstance(metas, list) else [])[:METAS_MAX]:
        slug = m.get("slug") if isinstance(m, dict) else None
        if not slug:
            continue
        try:
            d = fetch(DS + "/metas/meta/v1/" + urllib.parse.quote(slug)) or {}
        except MemeError:
            continue
        for p in (d.get("pairs") or []) if isinstance(d, dict) else []:
            add(p.get("chainId"), (p.get("baseToken") or {}).get("address"), "meta:" + slug)
    if st.get("meme_search_new"):
        now = time.time() if now is None else now
        for i in range(SEARCH_PER_PASS):
            term = SEARCH_TERMS[(turn * SEARCH_PER_PASS + i) % len(SEARCH_TERMS)]
            try:
                d = fetch(DS + "/latest/dex/search?q=" + urllib.parse.quote(term)) or {}
            except MemeError:
                continue
            for p in (d.get("pairs") or []) if isinstance(d, dict) else []:
                made = p.get("pairCreatedAt") if isinstance(p, dict) else None
                if made and now - made / 1000 <= SEARCH_MAX_AGE_H * 3600:
                    add(p.get("chainId"), (p.get("baseToken") or {}).get("address"), "search-new")
    return found


def snapshot(tracked, fetch=get):
    """{key: pair} for tracked tokens, keeping each token's own pool (the most liquid one at first sighting)."""
    out, by_chain = {}, {}
    for k, t in tracked.items():
        by_chain.setdefault(t["chain"], []).append(k)
    for chain, keys in by_chain.items():
        for i in range(0, len(keys), 30):
            part = keys[i:i + 30]
            try:
                pairs = fetch("%s/tokens/v1/%s/%s" % (DS, chain, ",".join(tracked[k]["addr"] for k in part))) or []
            except MemeError:
                continue
            for k in part:
                t = tracked[k]
                mine = [p for p in pairs if isinstance(p, dict)
                        and (p.get("baseToken") or {}).get("address", "").lower() == t["addr"].lower()]
                own = [p for p in mine if p.get("pairAddress") == t.get("pool")]
                if own and own[0].get("dexId") == "pumpfun":       # graduated: follow the token to its real pool
                    own = [p for p in mine if p.get("dexId") != "pumpfun" and _liq(p)] or own
                pick = own or sorted(mine, key=lambda p: -(_liq(p) or 0))
                if pick:
                    out[k] = pick[0]
    return out


def _liq(p):
    """Pool liquidity in USD, or None when DexScreener gives none (a pump.fun bonding curve): unknown, not zero."""
    v = (p.get("liquidity") or {}).get("usd")
    return float(v) if v is not None else None


def screen(chain, addr, fetch=get, pool=None):
    """(verdict, reasons, slim report, strict verdict, strict reasons) from RugCheck's full report (solana) or GoPlus
    (EVM). A failed read is 'unknown' for both. The strict check is research S1 to S3 (docs/MEME.md section 6)."""
    try:
        if chain == "solana":
            rep = fetch("https://api.rugcheck.xyz/v1/tokens/%s/report" % urllib.parse.quote(addr))
            slim = rep and {k: rep.get(k) for k in ("score_normalised", "lpLockedPct", "creator")} | {
                "risks": [{"name": r.get("name"), "level": r.get("level")} for r in rep.get("risks") or []]}
        else:
            d = fetch("https://api.gopluslabs.io/api/v1/token_security/%d?contract_addresses=%s"
                      % (EVM_ID[chain], urllib.parse.quote(addr))) or {}
            res = d.get("result") or {}
            rep = res.get(addr.lower()) or (next(iter(res.values())) if res else None)
            slim = rep and {k: rep.get(k) for k in ("is_honeypot", "buy_tax", "sell_tax", "cannot_sell_all",
                                                    "is_open_source", "holder_count", "owner_percent", "creator_percent")}
    except MemeError as e:
        return "unknown", [str(e)], None, "unknown", [str(e)]
    v, why = R.screen_verdict(chain, rep)
    sv, swhy = R.strict_verdict(chain, rep, pool)
    if slim:
        try:
            slim["pilot"] = MP.screen_inputs(chain, rep, pool, R.DEAD)
        except (TypeError, ValueError, OverflowError):
            pass  # optional diagnostics never change the screen result
    return v, why, slim, sv, swhy


STOP_WORDS = set("""THE AND FOR WITH FROM THIS THAT ARE WAS HAS HAVE WILL ITS INTO OVER AFTER BEFORE NEW TODAY
YAHOO DAY GAINERS LOSERS ACTIVES MOST MOVED USUAL VOLUME SHARES STOCK STOCKS MARKET MARKETS EARNINGS REPORT REPORTS
QUARTER SALES PRICE TARGET SAYS SAID INC CORP CO LTD PLC ETF FUND COIN TOKEN CRYPTO LAUNCH LAUNCHES IPO WEEK YEAR
UP DOWN HIGH LOW ITS NOT BUT YOU HOW WHY WHAT ONE TWO TOP""".split())


def news_words(st):
    """Words from the news you are watching: the meme_news_words setting (for example "GTA 6 launch"). Upper case,
    3+ letters, common words left out. Empty if none. Coins from a trending DexScreener narrative count as news
    anyway (news_match)."""
    import re
    texts = [str(x) for x in (st.get("meme_news_words") or [])]
    return {w for t in texts for w in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", t.upper()) if w not in STOP_WORDS}


def news_match(t, words):
    """True when a coin is tied to today's news: its symbol or a word of its name is a news word, or it came from a
    trending DexScreener narrative."""
    import re
    if any(s.startswith("meta:") for s in t.get("sources") or []):
        return True
    mine = {str(t.get("symbol") or "").upper()} | set(re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", str(t.get("name") or "").upper()))
    return bool(mine & words - STOP_WORDS)


_pilot_busy = threading.Lock()
_pilot_pages = {}


def _pilot_key(root, token, pair):
    return root, token["chain"], pair["pairAddress"], token["addr"]


def _prefetch_pilot(candidates, budget, now, fetch):
    """One spare GeckoTerminal request after core work, with no waiting in the desk run."""
    if not candidates or budget[0] <= MIG_RESERVE or not _pilot_busy.acquire(blocking=False):
        return
    key = min(candidates)[1]
    _, chain, pool, mint = key
    url = "%s/networks/%s/pools/%s/trades" % (GT, GT_NET[chain], urllib.parse.quote(pool, safe=""))

    def collect():
        try:
            data = _real_get(url, timeout=2, wait_for_slot=False) if fetch is _real_get else fetch(url)
            page = MP.trade_page(chain, mint, data, now)
            if page is not None:
                _pilot_pages[key] = page
        except (MemeError, OSError, ValueError):
            pass
        finally:
            _pilot_busy.release()

    budget[0] -= 1
    try:
        threading.Thread(target=collect, name="meme-pilot", daemon=True).start()
    except RuntimeError:
        _pilot_busy.release()


def gt_bars(chain, pool, end_ts, limit=60, fetch=get):
    """5-minute candles [ts, o, h, l, c, v], oldest first, ending at end_ts (read-only GeckoTerminal)."""
    d = fetch("%s/networks/%s/pools/%s/ohlcv/minute?aggregate=5&limit=%d&currency=usd&before_timestamp=%d"
              % (GT, GT_NET[chain], pool, limit, int(end_ts))) or {}
    rows = ((d.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    return sorted([float(x) for x in r[:6]] for r in rows)


_chart_cache = {}
CHART_IVL = {"5m": ("minute", 5), "15m": ("minute", 15), "1h": ("hour", 1)}


def chart(key, ivl="5m", fetch=None):
    """Candles and levels for the Memecoins tab's chart: GeckoTerminal OHLCV for the coin's tracked pool (read-only),
    cached 60 seconds, plus the buy, stop and target of a position the practice account holds."""
    if ivl not in CHART_IVL:
        raise MemeError("interval must be 5m, 15m or 1h")
    s = load()
    t = (s.get("tracked") or {}).get(key)
    if not t or not t.get("pool"):
        raise MemeError("That coin is not on the tracked list.")
    hit = _chart_cache.get((key, ivl))
    if hit and time.time() - hit[0] < 60:
        bars = hit[1]
    else:
        unit, agg = CHART_IVL[ivl]
        d = (fetch or get)("%s/networks/%s/pools/%s/ohlcv/%s?aggregate=%d&limit=300&currency=usd"
                           % (GT, GT_NET[t["chain"]], urllib.parse.quote(t["pool"]), unit, agg)) or {}
        rows = ((d.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        bars = [{"time": int(r[0]), "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]}
                for r in sorted(rows)]
        _chart_cache[(key, ivl)] = (time.time(), bars)
    levels = []
    for p in (s.get("account") or {}).get("positions") or []:
        if p["key"] == key:
            levels = [{"kind": "entry", "title": "Bought", "price": p["px"]},
                      {"kind": "stop", "title": "Stop", "price": p["px"] * (1 + R.STOP_PCT / 100)},
                      {"kind": "target", "title": "Target", "price": p["px"] * (1 + R.TARGET_PCT / 100)}]
    trades = [{"time": int(x["ts"]), "side": "buy", "policy": x["policy"]} for x in (s.get("account") or {}).get("closed", [])
              + (s.get("account") or {}).get("positions", []) if x["key"] == key]
    trades += [{"time": int(x["exit_ts"]), "side": "sell", "why": x["why"]} for x in (s.get("account") or {}).get("closed", [])
               if x["key"] == key]
    return {"key": key, "symbol": t.get("symbol"), "chain": t["chain"], "ivl": ivl, "bars": bars, "levels": levels,
            "trades": sorted(trades, key=lambda x: x["time"]),
            "url": "https://dexscreener.com/%s/%s" % (t["chain"], t["pool"])}


# ---- the paper account -------------------------------------------------------------------------------------------

def cost_pct(st, chain, policy=None):
    """Round-trip cost for a chain: Settings' cost, or more where gas makes it dearer (memerules.CHAIN_COST). Rule P7
    and its control pay at least 4 % (docs/MEME.md section 9)."""
    c = max(st["meme_cost_pct"], R.CHAIN_COST.get(chain, 0.0))
    return max(c, M.MIN_COST_PCT) if policy in M.POLICIES + MN.POLICIES else c


def _side_cost(st, value, liq, chain=None, policy=None):
    """Fraction lost on one side: half the round-trip cost plus price impact (size / half the pool)."""
    return cost_pct(st, chain, policy) / 200 + (value / (liq / 2) if liq and liq > 0 else 1.0)


def _dead(liq):
    """Liquidity reported and under DEAD_LIQ. Unknown liquidity (None) is not a rug."""
    return liq is not None and liq < R.DEAD_LIQ


def _sell(st, acct, pos, px, exit_liq, ts, why):
    liq = pos["liq"] if exit_liq is None else exit_liq
    if px and why != "rug" and not _dead(liq):
        fill = max(0.0, px * (1 - _side_cost(st, pos["qty"] * px, liq, pos["chain"], pos.get("policy"))))
    else:
        fill, why = 0.0, "rug"
    proceeds = pos["qty"] * fill
    acct["cash"] = round(acct["cash"] + proceeds, 6)
    acct["positions"].remove(pos)
    acct["closed"].append(dict(pos, exit_at=_iso(ts), exit_ts=ts, exit_px=px or 0.0, exit_liq=exit_liq,
                               exit_fill=fill, why=why,
                               pnl=round(proceeds - pos["cost"], 4),
                               ret_pct=round((proceeds / pos["cost"] - 1) * 100, 2)))


def equity(st, acct, tracked):
    v = acct["cash"]
    for p in acct["positions"]:
        t = tracked.get(p["key"]) or {}
        px, liq = t.get("px") or 0, t.get("liq")
        liq = p["liq"] if liq is None else liq
        v += p["qty"] * px * (1 - _side_cost(st, p["qty"] * px, liq, p["chain"], p.get("policy"))) if px and not _dead(liq) else 0.0
    return v


def _new_drawdown_book(start, now):
    return dict(_new_account(start), started=_iso(now), realized_peak=start, halted=None)


def _practice_drawdown(st, s, entries, exits, tracked, now):
    """Size a companion account from the main book's fills, without feeding back into the scan."""
    enabled = st["meme_practice_drawdown"]
    start = st["meme_starting_balance"]
    book = s.get("practice_drawdown")
    if book and book["start"] != start:
        s.pop("practice_drawdown")
        book = None
    if book is None:
        if not enabled:
            return
        book = s["practice_drawdown"] = _new_drawdown_book(start, now)

    positions = {(p["key"], p["ts"]): p for p in book["positions"]}
    for closed in exits:
        pos = positions.get((closed["key"], closed["ts"]))
        if pos is None:
            continue
        _sell(st, book, pos, closed["exit_px"], closed.get("exit_liq"), closed["exit_ts"], closed["why"])
        realized = round(book["cash"] + sum(p["cost"] for p in book["positions"]), 6)
        book["realized_peak"] = max(book["realized_peak"], realized)
        limit = round(book["realized_peak"] * (1 - PRACTICE_DRAWDOWN_PCT / 100), 6)
        if book["halted"] is None and realized <= limit:
            book["halted"] = {"at": _iso(now), "equity": realized, "peak": book["realized_peak"]}

    if not enabled or book["halted"]:
        return
    for source in entries:
        size = min(source["cost"], equity(st, book, tracked) * st["meme_position_pct"] / 100, book["cash"])
        if size >= 1:
            fill = source["px"] * (1 + _side_cost(st, size, source["liq"], source["chain"]))
            book["cash"] = round(book["cash"] - size, 6)
            book["positions"].append(dict(source, cost=size, fill=fill, qty=size / fill))


def _quick_error(s, now, err):
    """Rule P9's guard, like N1's: a failure is kept on its own record and never stops the core scan."""
    book = s.get(QK.STATE_KEY)
    if not isinstance(book, dict):
        book = s[QK.STATE_KEY] = {}
    book["error"] = f"{_iso(now)}: {type(err).__name__}: {err}"


def _exit_rule(entry_px, entry_ts, px, liq, now, entry_liq=None):
    """The forward exit from one price sample: (price to use, why) or None. The stop fills at the sampled price
    (a gap is real); the target fills at the target (as a limit would); a dead pool is a total loss."""
    if _dead(liq) or not px:
        return 0.0, "rug"
    if px <= entry_px * (1 + R.STOP_PCT / 100):
        return px, "stop"
    if px >= entry_px * (1 + R.TARGET_PCT / 100):
        return entry_px * (1 + R.TARGET_PCT / 100), "target"
    if now >= entry_ts + R.HOLD_MIN * 60:
        return px, "time"
    if entry_liq and liq is not None and liq < entry_liq * LIQ_PULL:
        return px, "liquidity pulled"
    return None


# ---- one run -----------------------------------------------------------------------------------------------------

def run(now=None, fetch=None, demo=False):
    """One pass. Returns a short summary dict. Never raises for a network problem; the error is kept on the card.
    demo: no phone alerts (the scheduler never runs this model in the demo anyway)."""
    if not _running.acquire(blocking=False):
        return {"skipped": "already running"}
    try:
        return _run(time.time() if now is None else now, fetch or get, demo)
    finally:
        _running.release()


def _trade_alerts(st, accts, before, tracked, screens):
    """Phone-alert events (desk/notify.py) for this run's practice-account buys and sells. Reads only: it never
    changes an account, a shadow, an exit or a cost. Shadows never alert."""
    out = []
    for bn, ba in accts:
        held, n_closed = before[bn]
        for c in ba["closed"][n_closed:]:
            t = tracked.get(c["key"]) or {}
            out.append({"kind": "sell", "account": bn, "symbol": c.get("symbol"), "chain": c["chain"],
                        "token": c["key"].split(":", 1)[1], "pool": t.get("pool"), "why": c.get("why"),
                        "entry_px": c["px"], "exit_px": c.get("exit_px"), "held_min": (c["exit_ts"] - c["ts"]) / 60,
                        "pnl": c["pnl"], "ret_pct": c["ret_pct"], "balance": round(equity(st, ba, tracked), 2)})
        for p in ba["positions"]:
            if (p["key"], p["ts"]) in held:
                continue
            t, sc, pol = tracked.get(p["key"]) or {}, screens.get(p["key"]) or {}, p.get("policy")
            f = t.get("f") or {}
            if pol in MN.POLICIES:
                stop, target = MN.stop_level(p["px"], p["px"]), None
            elif pol in QK.POLICIES:
                stop, target = p["px"] * (1 + QK.STOP_PCT / 100), p["px"] * (1 + QK.TARGET_PCT / 100)
            else:
                stop, target = p["px"] * (1 + R.STOP_PCT / 100), p["px"] * (1 + R.TARGET_PCT / 100)
            out.append({"kind": "buy", "account": bn, "symbol": p.get("symbol"), "chain": p["chain"],
                        "token": p["key"].split(":", 1)[1], "pool": t.get("pool"), "dex": p.get("dex") or t.get("dex"),
                        "rule": pol, "px": p["px"], "fill": p.get("fill"), "size": p["cost"], "stop": stop,
                        "target": target, "liq": p.get("liq"), "v1h": f.get("v1h"), "age_h": f.get("age_h"),
                        "r1h": f.get("r1h"), "screen": sc.get("verdict"), "strict": sc.get("strict"),
                        "warning": (sc.get("why") or [None])[0]})
    return out


def _run(now, fetch, demo=False):
    st = S.load()
    s = load()
    pol, why_pol = policy(st)
    acct = s.get("account")
    if not acct or acct.get("start") != st["meme_starting_balance"]:
        acct = _new_account(st["meme_starting_balance"])          # a new starting balance starts the account over
    acct["started"] = acct.get("started") or _iso(now)
    first_main_close = len(acct["closed"])
    books = s.get("books") or {}
    for name, _, _ in BOOKS:
        if not books.get(name) or books[name].get("start") != st["meme_starting_balance"]:
            books[name] = _new_account(st["meme_starting_balance"])
        books[name]["started"] = books[name].get("started") or _iso(now)
    accts = [("main", acct)] + [(n, books[n]) for n, _, _ in BOOKS]
    before = {bn: ({(p["key"], p["ts"]) for p in ba["positions"]}, len(ba["closed"])) for bn, ba in accts}
    careful = books["careful"]
    first_careful_close = len(careful["closed"])
    tracked, shadows = s.get("tracked") or {}, s.get("shadows") or []
    screens = read_json(folder() / "screens.json", {}) or {}
    out = {"new": 0, "shadows_opened": 0, "shadows_closed": 0, "bought": 0, "sold": 0}

    # 1. discovery feeds
    if now - (s.get("last_feeds") or 0) >= FEEDS_EVERY:
        s["last_feeds"] = now
        s["feed_turn"] = (s.get("feed_turn") or 0) + 1
        for (chain, addr), tags in discover(st, fetch, now, s["feed_turn"]).items():
            k = chain + ":" + addr
            if k in tracked:
                t = tracked[k]
                t["sources"] = sorted(set(t["sources"]) | set(tags))
                if set(tags) & set(R.PROMO_FEEDS):
                    t["last_promo"] = now
                t["last_feed"] = now
            else:
                tracked[k] = {"chain": chain, "addr": addr, "first_seen": now, "last_feed": now, "sources": sorted(tags),
                              "last_promo": now if set(tags) & set(R.PROMO_FEEDS) else None,
                              "cool": {}, "last_decide": 0}
    # 2. snapshots; a new token's discovery line is written with its first snapshot
    snap = snapshot(tracked, fetch) if tracked else {}
    for k, p in snap.items():
        t = tracked[k]
        t["px"] = float(p.get("priceUsd") or 0) or None
        t["liq"] = _liq(p)
        t["f"] = {x: (round(y, 4) if isinstance(y, float) else y) for x, y in R.features_from_pair(p, now * 1000).items()}
        t["fdv"], t["mcap"] = p.get("fdv"), p.get("marketCap")
        t["v24"] = (p.get("volume") or {}).get("h24")
        if t.get("pool") and p.get("pairAddress") != t["pool"]:
            t["pool"], t["dex"] = p.get("pairAddress"), p.get("dexId")
        t["seen"] = now
        t.setdefault("symbol", (p.get("baseToken") or {}).get("symbol") or "?")
        t["name"] = (p.get("baseToken") or {}).get("name") or t.get("name")
        if not t.get("pool"):
            t.update(pool=p.get("pairAddress"), created=p.get("pairCreatedAt"), dex=p.get("dexId"))
            _append("discoveries.jsonl", {"at": _iso(now), "ts": now, "key": k, "chain": t["chain"], "symbol": t["symbol"],
                                          "pool": t["pool"], "sources": t["sources"],
                                          "features": R.features_from_pair(p, now * 1000),
                                          "liq": t["liq"], "fdv": p.get("fdv"), "px": t["px"]})
            out["new"] += 1
    gt_budget = [GT_PER_RUN]
    pilot_pairs = []
    pilot_root = folder()
    pilot_now = {}
    for key, page in tuple(_pilot_pages.items()):
        if key[0] == pilot_root and now - page[0] > MP.CACHE_SECONDS:
            _pilot_pages.pop(key, None)

    def close_shadow(sh, px, ts, why):
        sh.update(exit_at=_iso(ts), exit_ts=ts, exit_px=px, why=why,
                  net_pct=round(R.net_pct(sh["px"], px, cost_pct(st, sh["chain"], sh["policy"])), 3))
        shadows.remove(sh)
        _append("shadows.jsonl", sh)
        tracked.get(sh["key"], {}).get("cool", {})[sh["policy"]] = ts + R.COOLDOWN_MIN * 60
        out["shadows_closed"] += 1

    # 3. exits: from this run's sample, or from candles when the desk was away
    items = [(("pos", bn, ba), p) for bn, ba in accts for p in list(ba["positions"])] + [(("sh", None, None), sh)
                                                                                         for sh in list(shadows)]
    for (kind, bn, ba), it in items:
        t = tracked.get(it["key"]) or {}
        entry_px, entry_ts = it["px"], it["ts"]
        hit = None
        moon = it.get("policy") in MN.POLICIES
        quick = it.get("policy") in QK.POLICIES
        if now - it.get("checked", entry_ts) > GAP and t.get("pool") and gt_budget[0] > MIG_RESERVE:
            gt_budget[0] -= 1
            try:
                hold = MN.HOLD_MIN if moon else QK.HOLD_MIN if quick else R.HOLD_MIN
                bars = gt_bars(t["chain"], t["pool"], min(now, entry_ts + hold * 60 + 300), limit=300 if moon else 60,
                               fetch=fetch)
                dead = it["key"] in snap and _dead(t.get("liq"))
                if moon:
                    r, it["peak"] = MN.replay(entry_ts, entry_px, it.get("peak") or entry_px, bars, dead=dead, now=now)
                elif quick:
                    r = R.replay(entry_ts, entry_px, bars, dead=dead, now=now, stop=QK.STOP_PCT, target=QK.TARGET_PCT,
                                 hold_min=QK.HOLD_MIN)
                else:
                    r = R.replay(entry_ts, entry_px, bars, dead=dead, now=now)
                hit = r and (r["price"], r["why"], r["ts"])
            except MemeError:
                continue                     # tried again next run
        elif now - it.get("checked", entry_ts) > GAP:
            continue                         # no candle budget left this run: wait, never guess
        if not hit and it["key"] in snap:
            if moon:
                e = MN.exit_sample(entry_px, entry_ts, it.get("peak") or entry_px, t.get("px"), t.get("liq"), now,
                                   it.get("liq") if kind == "pos" else None, R.DEAD_LIQ)
                if not e and t.get("px"):
                    it["peak"] = max(it.get("peak") or entry_px, t["px"])
            elif quick:
                try:
                    e = QK.exit_sample(entry_px, entry_ts, t.get("px"), t.get("liq"), now,
                                       it.get("liq") if kind == "pos" else None, R.DEAD_LIQ)
                except Exception as err:
                    _quick_error(s, now, err)
                    continue
            else:
                e = _exit_rule(entry_px, entry_ts, t.get("px"), t.get("liq"), now, it.get("liq") if kind == "pos" else None)
            hit = e and (e[0], e[1], now)
        if hit:
            if kind == "pos":
                _sell(st, ba, it, hit[0], t.get("liq"), hit[2], hit[1])
                tracked.get(it["key"], {}).get("cool", {})["paper" if bn == "main" else "paper:" + bn] = \
                    hit[2] + R.COOLDOWN_MIN * 60
                out["sold"] += bn == "main"
            else:
                close_shadow(it, hit[0], hit[2], hit[1])
        elif it["key"] in snap:
            it["checked"] = now

    first_main_entry = len(acct["positions"])
    first_careful_entry = len(careful["positions"])
    # 4. safety screens: newest unscreened (or stale) first
    todo = sorted((k for k, t in tracked.items() if t.get("pool") and now - (screens.get(k) or {}).get("ts", 0) > SCREEN_TTL),
                  key=lambda k: -tracked[k]["first_seen"])[:SCREENS_PER_RUN]
    for k in todo:
        v, why, rep, sv, swhy = screen(tracked[k]["chain"], tracked[k]["addr"], fetch, tracked[k].get("pool"))
        screens[k] = {"ts": now, "at": _iso(now), "verdict": v, "why": why, "report": rep, "strict": sv, "strict_why": swhy}
    write_json(folder() / "screens.json", screens)

    # 4b. delayed-fill rules (P7 docs/MEME.md section 9, P8 section 10): fills due from earlier decisions first
    pending = s.get("mig_pending") or []
    for pe in list(pending):
        if now - pe["decided"] < M.FILL_AFTER_S:
            continue
        pending.remove(pe)
        bname, logf = DELAYED[pe["policy"]]
        t = tracked.get(pe["key"]) or {}
        ok = now - pe["decided"] <= M.FILL_BY_S and pe["key"] in snap and t.get("px") and not _dead(t.get("liq"))
        _append(logf, {"at": _iso(now), "ts": now, "key": pe["key"], "event": "fill" if ok else "cancel",
                       "policy": pe["policy"], "px": t.get("px"), "why": None if ok else "no price in the 60-150 s fill window"})
        if not ok:
            continue
        shadows.append({"key": pe["key"], "symbol": t.get("symbol"), "chain": t["chain"], "policy": pe["policy"],
                        "ts": now, "at": _iso(now), "px": t["px"], "peak": t["px"], "screen": pe["screen"],
                        "strict": pe["strict"], "checked": now, "news": False, "veto": pe["veto"], "f": pe["f"],
                        "decided": pe["decided"],
                        **({"pilot": pe["pilot"]} if "pilot" in pe else {})})
        out["shadows_opened"] += 1
        if not bname:
            continue
        ba = books[bname]
        moon = bname == "moonshot"
        cap = MN.MAX_OPEN if moon else st["meme_max_open"]
        if (not st["meme_paused"] and len(ba["positions"]) < cap and t.get("liq")
                and pe["key"] not in {p["key"] for p in ba["positions"]} and now >= t["cool"].get("paper:" + bname, 0)):
            size = min(equity(st, ba, tracked) * (MN.SIZE_PCT if moon else st["meme_position_pct"]) / 100, ba["cash"])
            if size >= 1:
                fill = t["px"] * (1 + _side_cost(st, size, t["liq"], t["chain"], pe["policy"]))
                ba["cash"] = round(ba["cash"] - size, 6)
                ba["positions"].append({"key": pe["key"], "symbol": t.get("symbol"), "chain": t["chain"],
                                        "policy": pe["policy"], "ts": now, "at": _iso(now), "px": t["px"], "peak": t["px"],
                                        "fill": fill, "qty": size / fill, "cost": size, "liq": t["liq"], "checked": now})
    for k, p in snap.items():
        t = tracked[k]
        if t.get("mig_done"):
            continue
        anc = M.anchor(t["chain"], t["addr"], p)
        if anc is None:
            continue
        when = M.in_window(now, anc)
        if when == "early":
            continue
        row = {"at": _iso(now), "ts": now, "key": k, "symbol": t.get("symbol"), "event": "decide", "anchor": anc,
               "anchor_at": _iso(anc), "minutes": round((now - anc) / 60, 1), "pool": p.get("pairAddress"),
               "dex": p.get("dexId"), "first_seen_min": round((t["first_seen"] - anc) / 60, 1)}
        if when == "late":
            t["mig_done"] = True
            if t["first_seen"] - anc <= M.RECENT_S:        # a fresh graduate the desk saw too late: attrition
                why = ("GeckoTerminal data unavailable in the window after %d tries (%s)"
                       % (t["mig_tries"], t.get("mig_last_error") or "no budget") if t.get("mig_tries")
                       else "first decision run came after minute 17")
                _append("migrations.jsonl", dict(row, event="missed", why=why))
            continue                                       # graduated long before the desk saw it: not in the study
        f = R.features_from_pair(p, now * 1000)
        sc = screens.get(k) or {}
        veto = [v for v in R.vetoes(f, now - (t.get("last_promo") or -1e12) < 86400) if not v.startswith("R1")]
        buyers = first = None
        if gt_budget[0] > 0:
            gt_budget[0] -= 1
            try:
                d = fetch("%s/networks/solana/pools/%s/trades" % (GT, urllib.parse.quote(p.get("pairAddress") or ""))) or {}
                trades = [x.get("attributes") or {} for x in d.get("data") or []]
                if p.get("pairAddress"):
                    pilot_now[_pilot_key(pilot_root, t, p)] = MP.trade_page(t["chain"], t["addr"], d, now)
                buyers, first, _ = M.flow(trades, anc, ((sc.get("report") or {}).get("creator")))
                if first is None and gt_budget[0] > 0:
                    gt_budget[0] -= 1
                    m = fetch("%s/networks/solana/pools/%s/ohlcv/minute?aggregate=1&limit=30&currency=usd"
                              "&before_timestamp=%d" % (GT, urllib.parse.quote(p.get("pairAddress") or ""),
                                                        int(anc + 20 * 60))) or {}
                    first = M.first_from_bars(((m.get("data") or {}).get("attributes") or {}).get("ohlcv_list"), anc)
            except MemeError as e:
                row["data_error"] = str(e)
        else:
            row["data_error"] = "no GeckoTerminal budget left this run"
        if row.get("data_error"):
            # the trades fetch failed (a shared-address 429 cool-down, or no budget): try again next run while the
            # minute-15-to-17 window is still open; never decide on missing data (docs/MEME.md section 9 note)
            t["mig_tries"] = t.get("mig_tries", 0) + 1
            t["mig_last_error"] = row["data_error"]
            if now + 60 < anc + M.DECIDE_TO_MIN * 60:
                _append("migrations.jsonl", dict(row, event="retry", why=row["data_error"]))
                continue
            t["mig_done"] = True
            _append("migrations.jsonl", dict(row, event="missed", why="GeckoTerminal data unavailable in the window "
                                             "after %d tries (%s)" % (t["mig_tries"], row["data_error"])))
            continue
        t["mig_done"] = True
        if t.get("mig_tries"):
            row["tries"] = t["mig_tries"] + 1
        dec, why = M.decide(t.get("liq"), f.get("v1h"), buyers, first, t.get("px"))
        safe = sc.get("strict") == "pass" and not veto
        if not safe:
            why = why + ["strict check %s" % sc.get("strict", "unknown")] * (sc.get("strict") != "pass") + veto
        row.update(liq=t.get("liq"), v1h=f.get("v1h"), buyers=buyers, first_px=first, px=t.get("px"),
                   strict=sc.get("strict", "unknown"), veto=veto, why=why,
                   yes=[n for n, y in dec.items() if y and safe])
        _append("migrations.jsonl", row)
        for name in row["yes"]:
            pending.append({"key": k, "policy": name, "decided": now, "screen": sc.get("verdict", "unknown"),
                            "strict": sc.get("strict", "unknown"), "veto": veto,
                            "f": {x: (round(y, 3) if isinstance(y, float) else y) for x, y in f.items()}})
    # 4c. rule P8 and its control (docs/MEME.md section 10): every 5 minutes per coin, filled 60 s later
    waiting = {(pe["key"], pe["policy"]) for pe in pending}
    open_now = {(sh["key"], sh["policy"]) for sh in shadows}
    for k, p in snap.items():
        t = tracked[k]
        remain = t.get("last_moon", 0) + MN.DECIDE_EVERY_MIN * 60 - now
        if remain > 0:
            if remain <= MP.CACHE_SECONDS and p.get("pairAddress") and not _dead(t.get("liq")):
                key = _pilot_key(pilot_root, t, p)
                page = _pilot_pages.get(key)
                if (not page or now + remain - page[0] > 90) and MN.decide(
                        R.features_from_pair(p, now * 1000), t.get("liq"))["moonshot_base"]:
                    pilot_pairs.append((remain, key))
            continue
        t["last_moon"] = now
        f = R.features_from_pair(p, now * 1000)
        dec = MN.decide(f, t.get("liq"))
        if not any(dec.values()) or _dead(t.get("liq")):
            continue
        sc = screens.get(k) or {}
        veto = R.vetoes(f, now - (t.get("last_promo") or -1e12) < 86400)
        block = ([] if sc.get("verdict") == "pass" else ["safety check %s" % sc.get("verdict", "unknown")]) + \
            [v for v in veto if v.startswith("R3")]
        pool = p.get("pairAddress")
        key = _pilot_key(pilot_root, t, p) if pool else None
        page = pilot_now.get(key) or _pilot_pages.get(key)
        pilot = MP.diagnostics(t["chain"], pool, t.get("liq"), sc, page, now)
        _append("moonshots.jsonl", {"at": _iso(now), "ts": now, "key": k, "symbol": t.get("symbol"), "event": "decide",
                                    "chain": t["chain"], "momentum": dec["moonshot"],
                                    "yes": [n for n, y in dec.items() if y and not block],
                                    "why": block, "veto": veto, "liq": t.get("liq"), "px": t.get("px"), "pilot": pilot,
                                    "f": {x: (round(y, 3) if isinstance(y, float) else y) for x, y in f.items()}})
        if block:
            continue
        for name, yes in dec.items():
            if yes and (k, name) not in waiting and (k, name) not in open_now and now >= t["cool"].get(name, 0):
                pending.append({"key": k, "policy": name, "decided": now, "screen": sc.get("verdict", "unknown"),
                                "strict": sc.get("strict", "unknown"), "veto": veto, "pilot": pilot,
                                "f": {x: (round(y, 3) if isinstance(y, float) else y) for x, y in f.items()}})
    s["mig_pending"] = pending

    # 5 and 6. decisions every 15 minutes per token; today's news words for P6 (docs/MEME.md section 6)
    words = news_words(st)
    open_sh = {(sh["key"], sh["policy"]) for sh in shadows}
    held = {bn: {p["key"] for p in ba["positions"]} for bn, ba in accts}
    for k, p in snap.items():
        t = tracked[k]
        if now - t.get("last_decide", 0) < R.DECIDE_EVERY_MIN * 60:
            continue
        t["last_decide"] = now
        p = dict(p, _news=news_match(t, words))
        f = R.features_from_pair(p, now * 1000)
        pre = R.decide(dict(f, above_6h_high=True))
        if pre["breakout"] and t.get("pool") and gt_budget[0] > 0:      # the 6-hour high needs candles
            gt_budget[0] -= 1
            try:
                bars = gt_bars(t["chain"], t["pool"], now, limit=100, fetch=fetch)
                if bars:
                    f["above_6h_high"] = R.features_from_bars(bars, len(bars) - 1)["above_6h_high"]
            except MemeError:
                pass
        dec = R.decide(f)
        sc = screens.get(k) or {"verdict": "unknown", "why": ["not screened yet"]}
        try:
            dec[QK.POLICY] = QK.decide(dec.get("all"), sc.get("verdict"))      # P9 (docs/MEME.md s.15): P0's entry, screen pass
        except Exception as err:
            dec[QK.POLICY] = False
            _quick_error(s, now, err)
        for name, yes in dec.items():
            if not yes or (k, name) in open_sh or now < t["cool"].get(name, 0) or not t.get("px") or _dead(t.get("liq")):
                continue
            shadows.append({"key": k, "symbol": t.get("symbol"), "chain": t["chain"], "policy": name, "ts": now,
                            "at": _iso(now), "px": t["px"], "screen": sc["verdict"], "checked": now,
                            "strict": sc.get("strict", "unknown"), "news": p["_news"],
                            "veto": R.vetoes(f, now - (t.get("last_promo") or -1e12) < 86400),
                            "f": {x: (round(y, 3) if isinstance(y, float) else y) for x, y in f.items()},
                            **({"dex": p.get("dexId")} if name in QK.POLICIES else {})})
            out["shadows_opened"] += 1
        veto = R.vetoes(f, now - (t.get("last_promo") or -1e12) < 86400)
        plan = [("main", acct, LEARN if pol in ("learn", "careful") else (pol,), "evidence" if pol == "careful" else "screen")] + [(n, books[n], rules, how)
                                                                                    for n, rules, how in BOOKS]
        for bn, ba, rules, how in plan:
            fired = [r for r in rules if dec.get(r)]
            cool = "paper" if bn == "main" else "paper:" + bn
            if (fired and _book_ok(how, sc, veto) and not st["meme_paused"] and k not in held[bn]
                    and len(ba["positions"]) < st["meme_max_open"] and now >= t["cool"].get(cool, 0) and t.get("px")
                    and t.get("liq") and not _dead(t["liq"])):
                size = min(equity(st, ba, tracked) * st["meme_position_pct"] / 100, ba["cash"])
                if size >= 1:
                    fill = t["px"] * (1 + _side_cost(st, size, t["liq"], t["chain"]))
                    ba["cash"] = round(ba["cash"] - size, 6)
                    ba["positions"].append({"key": k, "symbol": t.get("symbol"), "chain": t["chain"], "policy": fired[0],
                                            "ts": now, "at": _iso(now), "px": t["px"], "fill": fill, "qty": size / fill,
                                            "cost": size, "liq": t["liq"], "checked": now,
                                            **({"dex": p.get("dexId")} if fired[0] in QK.POLICIES else {})})
                    held[bn].add(k)
                    out["bought"] += bn == "main"

    # Optional observers share the model save with the core scan. Keep their failures on their own records.
    try:
        _practice_drawdown(st, s, acct["positions"][first_main_entry:], acct["closed"][first_main_close:], tracked, now)
    except Exception as err:
        book = s.get("practice_drawdown")
        if not isinstance(book, dict):
            book = s["practice_drawdown"] = _new_drawdown_book(st["meme_starting_balance"], now)
        book["error"] = f"{_iso(now)}: {type(err).__name__}: {err}"

    study = s.get(MR.STATE_KEY)
    try:
        if study is None and st["meme_reentry_shadow"]:
            study = s[MR.STATE_KEY] = MR.new(st, now)
        if study is not None and "start_ts" in study:
            MR.observe(study, st, now, careful["positions"][first_careful_entry:],
                       careful["closed"][first_careful_close:], snap)
    except Exception as err:
        if not isinstance(study, dict):
            study = s[MR.STATE_KEY] = {}
        study["error"] = f"{_iso(now)}: {type(err).__name__}: {err}"
        if study.get("incomplete") is None:
            study["incomplete"] = {"ts": now, "reason": "observer error"}

    # phone alerts for this run's practice buys and sells, built before the prune (it may drop a sold coin's pool)
    try:
        alerts = _trade_alerts(st, accts, before, tracked, screens)
    except Exception as err:                      # like N1 and P9: a push never decides anything in the scan
        alerts = []
        log.warning("memecoin phone alerts not built: %s", err)

    # prune: old or dead tokens with nothing open
    busy = set().union(*held.values()) | {sh["key"] for sh in shadows} | {pe["key"] for pe in pending}
    for k in [k for k, t in tracked.items() if k not in busy and (now - t["first_seen"] > TRACK_DAYS * 86400
              or (t.get("seen") and _dead(t.get("liq")))
              or (not t.get("pool") and now - t["first_seen"] > 3600))]:
        del tracked[k]
    if len(tracked) > TRACK_MAX:
        for k in sorted((k for k in tracked if k not in busy), key=lambda k: tracked[k].get("last_feed", 0))[:len(tracked) - TRACK_MAX]:
            del tracked[k]

    for bn, ba in accts:
        e = equity(st, ba, tracked)
        if not ba["curve"] or now - ba["curve"][-1]["ts"] >= 3600 or len(ba["closed"]) != ba.get("_nc"):
            ba["curve"].append({"ts": now, "at": _iso(now), "equity": round(e, 4)})
            ba["curve"] = ba["curve"][-2000:]
            ba["_nc"] = len(ba["closed"])
    eq = equity(st, acct, tracked)
    s.update(account=acct, books=books, tracked=tracked, shadows=shadows, last_run=now, checked=_iso(now), error=None,
             policy=pol, policy_why=why_pol)
    _save(s)
    try:                                          # after the buys and sells are recorded; never raises into the scan
        notify.send_meme(alerts, st, demo)
    except Exception as err:
        log.warning("memecoin phone alerts not sent: %s", err)
    out.update(tracked=len(tracked), open_shadows=len(shadows), equity=round(eq, 2))
    _prefetch_pilot(pilot_pairs, gt_budget, now, fetch)
    return out


def money(today):
    """Model 4's line for the Desk's money table, like modelacct.money(): balance (open coins at their last price,
    after costs), made or lost since the last point before today, and since the start. Reads the files only. The
    curve is kept in UTC, so "today" starts at 00:00 UTC, which is 7 PM New York the evening before."""
    st, s = S.load(), load()
    a = s.get("account")
    if not a or not a.get("curve"):
        return {"balance": None, "today": 0.0, "total": 0.0, "start": None}
    eq = equity(st, a, s.get("tracked") or {})
    before = [c["equity"] for c in a["curve"] if c["at"][:10] < today]
    return {"balance": round(eq, 2), "today": round(eq - (before[-1] if before else a["start"]), 2),
            "total": round(eq - a["start"], 2), "start": a["start"]}


def note_error(now, err):
    s = load()
    s["error"] = "%s: %s" % (_iso(now if isinstance(now, (int, float)) else time.time()), err)
    _save(s)


def reset():
    """Start the paper account over. The forward record (discoveries, shadows, screens) is kept."""
    if not _running.acquire(timeout=10):
        raise MemeError("A memecoin pass is running, try again in a minute.")
    try:
        s = load()
        s["account"] = _new_account(S.load()["meme_starting_balance"])
        s.pop("practice_drawdown", None)
        _save(s)
    finally:
        _running.release()


# ---- the forward test (the review and the tab) --------------------------------------------------------------------

def wilson(k, n, z=1.96):
    """Wilson 95 % range for a win rate, in % (research/stats.py's method)."""
    if not n:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return round(100 * (c - m) / d, 1), round(100 * (c + m) / d, 1)


def coin_bootstrap(by_tok, reps=1000, seed=7):
    """95 % range for the average trade, resampling whole coins (a coin's trades move together, like the stocks desk's
    day blocks). None while there are fewer than 5 coins."""
    import random
    groups = list(by_tok.values())
    if len(groups) < 5:
        return None, None
    rnd, means = random.Random(seed), []
    for _ in range(reps):
        pick = [x for _ in groups for x in rnd.choice(groups)]
        means.append(statistics.fmean(pick))
    means.sort()
    return round(means[int(reps * .025)], 2), round(means[int(reps * .975)], 2)


def folds(rows, k=4):
    """Mean net_pct in k equal slices of time (by entry ts), oldest first; None for an empty slice. docs/MEME.md
    section 5 asks for a positive mean in at least 3 of 4. Rows without a ts give []."""
    timed = [x for x in rows if x.get("ts") is not None]
    if not timed:
        return []
    t0, t1 = min(x["ts"] for x in timed), max(x["ts"] for x in timed)
    span = (t1 - t0) or 1.0
    parts = [[] for _ in range(k)]
    for x in timed:
        parts[min(k - 1, int((x["ts"] - t0) / span * k))].append(x["net_pct"])
    return [round(statistics.fmean(p), 2) if p else None for p in parts]


def fold_counts(rows, k=4):
    """Trades in each of the k equal time slices used by folds()."""
    timed = [x for x in rows if x.get("ts") is not None]
    if not timed:
        return []
    t0, t1 = min(x["ts"] for x in timed), max(x["ts"] for x in timed)
    span, n = (t1 - t0) or 1.0, [0] * k
    for x in timed:
        n[min(k - 1, int((x["ts"] - t0) / span * k))] += 1
    return n


def stats(rows):
    """The pass-bar numbers (docs/MEME.md section 5) for closed shadows or trades with net_pct."""
    r = sorted(x["net_pct"] for x in rows)
    if not r:
        return {"n": 0, "tokens": 0}
    keys = {x["key"] for x in rows}
    top3 = r[:-3] if len(r) > 3 else []
    tail = r[:max(1, len(r) // 20)]
    by_tok = {}
    for x in rows:
        by_tok.setdefault(x["key"], []).append(x["net_pct"])
    lo, hi = coin_bootstrap(by_tok)
    w_lo, w_hi = wilson(sum(v > 0 for v in r), len(r))
    fl = folds(rows)
    return {"folds": fl, "folds_pos": sum(1 for m in fl if m is not None and m > 0), "folds_n": fold_counts(rows),"n": len(r), "tokens": len(keys), "mean": round(statistics.fmean(r), 2), "median": round(statistics.median(r), 2),
            "win_pct": round(100 * sum(v > 0 for v in r) / len(r), 1), "mean_lo": lo, "mean_hi": hi,
            "win_lo": w_lo, "win_hi": w_hi,
            "mean_no_top3": round(statistics.fmean(top3), 2) if top3 else None,
            "tail5": round(statistics.fmean(tail), 2), "best": round(r[-1], 2),
            "share_100": round(100 * sum(v >= 100 for v in r) / len(r), 1),
            "mean_no_top10": round(statistics.fmean(r[:-10]), 2) if len(r) > 10 else None,
            "per_token_mean": round(statistics.fmean(statistics.fmean(v) for v in by_tok.values()), 2)}


_fw_cache = {}


def forward(rows=None):
    """{policy: stats, policy+screen: stats, ...} over closed shadows. Reading the file is cached until it changes."""
    if rows is None:
        f = folder() / "shadows.jsonl"
        try:
            sig = (str(f), f.stat().st_mtime_ns, f.stat().st_size)
        except OSError:
            sig = None
        if sig and _fw_cache.get("sig") == sig:
            return _fw_cache["out"]
        out = forward(_jsonl("shadows.jsonl"))
        _fw_cache.update(sig=sig, out=out)
        return out
    out = {}
    for p in R.POLICIES + M.POLICIES + MN.POLICIES + QK.POLICIES:
        mine = [x for x in rows if x["policy"] == p]
        out[p] = stats(mine)
        out[p + "+screen"] = stats([x for x in mine if x.get("screen") == "pass"])
        out[p + "+strict"] = stats([x for x in mine if x.get("strict") == "pass"])
        out[p + "+evidence"] = stats([x for x in mine if x.get("screen") == "pass" and x.get("veto") == []])
    # P9's comparators (docs/MEME.md s.15): P0 with and without the screen, entries since the first P9 shadow
    start = min((x["ts"] for x in rows if x["policy"] in QK.POLICIES and x.get("ts") is not None), default=None)
    p0 = [x for x in rows if x["policy"] == "all"]
    out["quick_control"] = stats(QK.since([x for x in p0 if x.get("screen") == "pass"], start))
    out["quick_all"] = stats(QK.since(p0, start))
    return out


def bar(x, base=None, control=None, min_slice=0):
    """docs/MEME.md section 5's pass bar on one forward line, time folds included. Rule P7 (section 9) also needs
    min_slice trades in every slice and must beat its own control."""
    return bool(x.get("n", 0) >= 100 and x.get("tokens", 0) >= 50 and (x.get("mean") or -1) > 0
                and (x.get("median") or -1) > 0 and (x.get("mean_no_top3") or -1) > 0
                and x.get("folds_pos", 0) >= 3
                and (not min_slice or min(x.get("folds_n") or [0]) >= min_slice)
                and (not base or not base.get("n") or x["mean"] > base["mean"])
                and (not control or (control.get("n") and x["mean"] > control["mean"])))


def _bar_for(k, v, fw):
    if k.startswith(("migration_base", "moonshot_base")):
        return False                      # a control is a comparator, never a candidate
    if k.startswith("quick_"):
        return False                      # a comparator, never a candidate
    if k.startswith("quick"):
        return bar(v, fw.get("quick_all"), fw.get("quick_control"), QK.MIN_PER_SLICE)
    if k.startswith("moonshot"):
        return bar(v, fw.get("all"), fw.get("moonshot_base"), M.MIN_PER_SLICE)
    if k.startswith("migration"):
        return bar(v, fw.get("all"), fw.get("migration_base"), M.MIN_PER_SLICE)
    return bar(v, fw.get("all"))


def conclusion(fw=None):
    """Where the forward test stands: ('pass', [lines]) when a line clears the bar (a candidate for its own fresh test),
    ('no_edge', [lines]) when 4+ rules have a full sample and every full line's 95 % range is below 0, else
    ('collecting', [lines with a full sample])."""
    fw = forward() if fw is None else fw
    full = {k: v for k, v in fw.items() if v.get("n", 0) >= 100 and v.get("tokens", 0) >= 50}
    passed = sorted(k for k, v in full.items() if not k.startswith("all") and _bar_for(k, v, fw))
    if passed:
        return "pass", passed
    rules = {k.split("+")[0] for k in full}
    if len(rules) >= 4 and all(v.get("mean_hi") is not None and v["mean_hi"] < 0 for v in full.values()):
        return "no_edge", sorted(full)
    return "collecting", sorted(full)


def verdict(fw, pol, account_rows=()):
    """Has the traded policy's screened forward record met the pass bar? In learning and careful mode the account trades
    several rules, so the bar is read on the account's own closed trades."""
    a = stats(list(account_rows)) if pol in ("learn", "careful") else fw.get(pol + "+screen") or {}
    base = fw.get("all") or {}
    checks = [("100 or more trades on 50 or more tokens", a.get("n", 0) >= 100 and a.get("tokens", 0) >= 50),
              ("mean above 0", (a.get("mean") or -1) > 0), ("median above 0", (a.get("median") or -1) > 0),
              ("still above 0 without the 3 best", (a.get("mean_no_top3") or -1) > 0),
              ("positive in 3 of 4 time slices", a.get("folds_pos", 0) >= 3),
              ("beats buying everything", a.get("n") and base.get("n") and a["mean"] > base["mean"])]
    return [{"check": c, "ok": bool(ok)} for c, ok in checks]


def view():
    """What /api/meme and the Memecoins tab show. Reads files only: no network."""
    st, s = S.load(), load()
    acct = s.get("account") or _new_account(st["meme_starting_balance"])
    tracked = s.get("tracked") or {}
    screens = read_json(folder() / "screens.json", {}) or {}
    rows = _jsonl("shadows.jsonl")
    fw = forward(rows)
    pol, why = policy(st)
    eq = equity(st, acct, tracked)
    closed = acct["closed"]
    pos = []
    for p in acct["positions"]:
        t = tracked.get(p["key"]) or {}
        px = t.get("px") or 0
        liq = p["liq"] if t.get("liq") is None else t["liq"]
        val = p["qty"] * px * (1 - _side_cost(st, p["qty"] * px, liq, p["chain"])) if px and not _dead(liq) else 0.0
        pos.append(dict({k: p[k] for k in ("key", "symbol", "chain", "policy", "at", "px", "cost")},
                        now_px=px, value=round(val, 2), pnl=round(val - p["cost"], 2),
                        move_pct=round((px / p["px"] - 1) * 100, 2) if px else None,
                        **({"stop": MN.stop_level(p["px"], p.get("peak") or p["px"]), "target": None,
                            "ends": _iso(p["ts"] + MN.HOLD_MIN * 60), "peak": p.get("peak")}
                           if p.get("policy") in MN.POLICIES else
                           {"stop": p["px"] * (1 + R.STOP_PCT / 100), "target": p["px"] * (1 + R.TARGET_PCT / 100),
                            "ends": _iso(p["ts"] + R.HOLD_MIN * 60)})))
    held = {p["key"] for p in acct["positions"]}
    cand = []
    for k, t in sorted(tracked.items(), key=lambda kv: (kv[0] not in held, -kv[1]["first_seen"])):
        if not t.get("pool"):
            continue
        sc = screens.get(k) or {}
        f = t.get("f") or {}
        cand.append({"key": k, "symbol": t.get("symbol"), "chain": t["chain"], "first_seen": _iso(t["first_seen"]),
                     "sources": t["sources"][:4], "px": t.get("px"), "liq": round(t["liq"]) if t.get("liq") is not None else None,
                     "fdv": t.get("fdv"), "v24": t.get("v24"), "f": f, "held": k in held,
                     "rules": [r for r, yes in R.decide(f).items() if yes] if f else [],
                     "screen": sc.get("verdict", "unknown"),
                     "strict": sc.get("strict", "unknown"), "strict_why": sc.get("strict_why") or [],
                     "screen_at": sc.get("at"), "snapshot_at": _iso(t["seen"]) if t.get("seen") else None,
                     "vetoes": R.vetoes(f, (t.get("seen") or 0) - (t.get("last_promo") or -1e12) < 86400) if f else None,
                     "why": (sc.get("why") or ["no red flags found"]) if sc else ["not screened yet"],
                     "url": "https://dexscreener.com/%s/%s" % (t["chain"], t.get("pool") or t["addr"])})
    sv = [x.get("verdict") for x in screens.values()]
    practice = books_view(st, s, tracked)
    # Present existing saved marks and closed rows without changing trading state.
    for book in practice:
        saved = acct if book["name"] == "main" else s["books"][book["name"]]
        book["curve"] = (saved.get("curve") or [])[-72:]
    careful = next((book["rows"] for book in practice if book["name"] == "careful"), [])
    values = {}
    for row in rows:
        values.setdefault(row["policy"], []).append(row["net_pct"])
    values["careful"] = [row["net_pct"] for row in careful]
    boundaries = (-50, -20, 0, 20, 50, 100)
    distributions = {}
    for name, returns in values.items():
        bins = [0] * (len(boundaries) + 1)
        for result in returns:
            bins[sum(result >= edge for edge in boundaries)] += 1
        distributions[name] = bins
    model_results = {
        name: {"passed": _bar_for(name, fw.get(name) or {}, fw)}
        for name in R.POLICIES + M.POLICIES + MN.POLICIES + QK.POLICIES
        if name not in ("all", "migration_base", "moonshot_base")
    }
    model_results["careful"] = {"stats": stats(careful),
                                "passed": all(item["ok"] for item in verdict(fw, "careful", careful))}
    companion = s.get("practice_drawdown")
    risk = {"enabled": st["meme_practice_drawdown"], "limit_pct": PRACTICE_DRAWDOWN_PCT, "book": None}
    if companion:
        risk["book"] = {
            "started": companion["started"], "start": companion["start"], "cash": round(companion["cash"], 2),
            "equity": round(equity(st, companion, tracked), 2), "realized_peak": companion["realized_peak"],
            "realized_equity": round(companion["cash"] + sum(p["cost"] for p in companion["positions"]), 2),
            "trades": len(companion["closed"]), "open": len(companion["positions"]), "halted": companion["halted"],
        }
    return {"enabled": st["meme_enabled"], "paused": st["meme_paused"], "policy": pol, "policy_why": why,
            "research": dict(zip(("pick", "passed"), research_pick())),
            "account": {"start": acct["start"], "cash": round(acct["cash"], 2), "equity": round(eq, 2),
                        "return_pct": round((eq / acct["start"] - 1) * 100, 2), "started": acct.get("started"),
                        "trades": len(closed), "wins": sum(1 for t in closed if t["pnl"] > 0),
                        "realized": round(sum(t["pnl"] for t in closed), 2)},
            "positions": pos, "closed": closed[-20:][::-1], "curve": acct["curve"][-500:],
            "candidates": cand, "tracked": len(tracked), "open_shadows": len(s.get("shadows") or []),
            "screens": {v: sv.count(v) for v in ("pass", "fail", "unknown")},
            "discoveries": _count("discoveries.jsonl"),
            "books": practice,
            "model_results": model_results,
            "model_min_per_slice": M.MIN_PER_SLICE,
            "distributions": {"boundaries": boundaries, "counts": distributions},
            "practice_drawdown": risk,
            "migrations": migration_funnel(),
            "moonshots": moonshot_funnel(),
            "quick": quick_view(rows, fw, s),
            "forward": fw, "verdict": verdict(fw, pol, [{"key": t["key"], "net_pct": t["ret_pct"], "ts": t["ts"]} for t in closed]), "cost_pct": st["meme_cost_pct"],
            "rules": {"stop": R.STOP_PCT, "target": R.TARGET_PCT, "hold_min": R.HOLD_MIN},
            "checked": s.get("checked"), "error": s.get("error"), "paper_only": True}


def migration_funnel():
    """Rule P7's attrition (docs/MEME.md section 9): graduates seen, decided in the window, missed, how many each
    line said yes to, fills and cancels, and the commonest reasons the full rule said no."""
    rows = _jsonl("migrations.jsonl")
    ev = lambda e: [r for r in rows if r.get("event") == e]
    dec = ev("decide")
    why = {}
    for r in dec:
        for w in r.get("why") or []:
            w = w.split(" (")[0] if "buyers" not in w else "too few buyers since migration"
            why[w] = why.get(w, 0) + 1
    return {"seen": len(dec) + len(ev("missed")), "decided": len(dec), "missed": len(ev("missed")),
            "retries": len(ev("retry")),
            "yes": len([r for r in dec if "migration" in (r.get("yes") or [])]),
            "yes_base": len([r for r in dec if "migration_base" in (r.get("yes") or [])]),
            "filled": len(ev("fill")), "cancelled": len(ev("cancel")),
            "top_no": sorted(why.items(), key=lambda kv: -kv[1])[:5]}


def moonshot_funnel():
    """Rule P8's decisions (docs/MEME.md section 10): coins whose momentum test fired, how many were blocked by the
    safety check or the R3 veto, fills, cancels, and the skew numbers of its closed shadows."""
    rows = _jsonl("moonshots.jsonl")
    dec = [r for r in rows if r.get("event") == "decide" and r.get("momentum", True)]
    blocked = {}
    for r in dec:
        for w in r.get("why") or []:
            blocked[w] = blocked.get(w, 0) + 1
    fw = forward().get("moonshot") or {}
    return {"fired": len(dec), "coins": len({r["key"] for r in dec}),
            "passed": len([r for r in dec if "moonshot" in (r.get("yes") or [])]),
            "filled": len([r for r in rows if r.get("event") == "fill" and r.get("policy") == "moonshot"]),
            "cancelled": len([r for r in rows if r.get("event") == "cancel" and r.get("policy") == "moonshot"]),
            "blocked": sorted(blocked.items(), key=lambda kv: -kv[1])[:5],
            "best": fw.get("best"), "share_100": fw.get("share_100"), "mean_no_top10": fw.get("mean_no_top10")}


def quick_view(rows, fw, s):
    """Rule P9's card (docs/MEME.md section 15): its closed shadows against the control, the win rate against the
    break-even rate its costs imply, and how its trades ended."""
    mine = [x for x in rows if x["policy"] in QK.POLICIES]
    ends = {}
    for x in mine:
        ends[x.get("why")] = ends.get(x.get("why"), 0) + 1
    n = len(mine)
    return {"stats": fw.get("quick") or {}, "control": fw.get("quick_control") or {}, "everything": fw.get("quick_all") or {},
            "ends": ends, "target_pct": round(100 * ends.get("target", 0) / n, 1) if n else None,
            "breakeven_pct": QK.breakeven_win_pct(R.COST_PCT), "breakeven_eth_pct": QK.breakeven_win_pct(R.CHAIN_COST["ethereum"]),
            "open": len([x for x in s.get("shadows") or [] if x.get("policy") in QK.POLICIES]),
            "dex": {d: sum(1 for x in mine if x.get("dex") == d) for d in sorted({x.get("dex") or "?" for x in mine})},
            "error": (s.get(QK.STATE_KEY) or {}).get("error"),
            "rules": {"stop": QK.STOP_PCT, "target": QK.TARGET_PCT, "hold_min": QK.HOLD_MIN}}


BOOK_NAMES = {"careful": "Careful: any rule, strict check, no research filter"}


def books_view(st, s, tracked):
    """One line per practice account: the main learning account first, then one per rule and the careful one."""
    out = []
    for name, acct in [("main", s.get("account"))] + [(n, (s.get("books") or {}).get(n)) for n, _, _ in BOOKS]:
        if not acct:
            continue
        eq = equity(st, acct, tracked)
        c = acct["closed"]
        out.append({"name": name, "equity": round(eq, 2), "start": acct["start"],
                    "return_pct": round((eq / acct["start"] - 1) * 100, 2), "trades": len(c),
                    "wins": sum(1 for t in c if t["pnl"] > 0), "open": len(acct["positions"]),
                    "rows": [{"key": t["key"], "net_pct": t["ret_pct"], "ts": t["ts"]} for t in c]})
    return out


def all_positive(v=None):
    """Is every practice account above its starting cash? (A snapshot, not proof: see conclusion().)"""
    b = (v or view())["books"]
    return bool(b) and all(x["return_pct"] > 0 for x in b)


def lines(v=None):
    """The review's MODEL 4 section."""
    v = v or view()
    a = v["account"]
    L = ["  Account: $%.2f (%+.2f%%) from $%.2f since %s. %d closed trades, %d won, realized $%+.2f. Trades: %s (%s)."
         % (a["equity"], a["return_pct"], a["start"], a["started"] or "not started", a["trades"], a["wins"], a["realized"],
            v["policy"], v["policy_why"]),
         "  Tracking %d tokens (%d discoveries logged); safety screens: %d pass, %d fail, %d unknown. Cost %.1f%% round trip."
         % (v["tracked"], v["discoveries"], v["screens"]["pass"], v["screens"]["fail"], v["screens"]["unknown"], v["cost_pct"]),
         "  Forward record (closed shadows, net of cost, stop %d%% / target +%d%% / %d min):"
         % (v["rules"]["stop"], v["rules"]["target"], v["rules"]["hold_min"])]
    for p in R.POLICIES + M.POLICIES + MN.POLICIES + QK.POLICIES:
        for name in (p, p + "+screen", p + "+strict", p + "+evidence"):
            x = v["forward"][name]
            if not x["n"] and name != p:
                continue
            L.append("    %-22s %s" % (name, "none closed yet" if not x["n"] else
                                         "%d trades, %d tokens, mean %+.2f%% (95%% range %s), median %+.2f%%, %.0f%% won, without top 3 %s, worst 5%% %+.2f%%"
                                         % (x["n"], x["tokens"], x["mean"],
                                            "%s to %s" % (x["mean_lo"], x["mean_hi"]) if x["mean_lo"] is not None else "n/a",
                                            x["median"], x["win_pct"],
                                            "%+.2f%%" % x["mean_no_top3"] if x["mean_no_top3"] is not None else "n/a", x["tail5"])))
    L.append("  Practice accounts (each $%.0f, 20%% a coin, at most %d):" % (v["account"]["start"], 4))
    for b in v.get("books") or []:
        L.append("    %-10s $%.2f (%+.2f%%), %d closed, %d won, %d open" % (b["name"], b["equity"], b["return_pct"],
                                                                        b["trades"], b["wins"], b["open"]))
    risk = v.get("practice_drawdown") or {}
    practice = risk.get("book")
    if practice:
        status = "halted" if practice["halted"] else ("on" if risk["enabled"] else "off")
        L.append("    Main drawdown experiment (%d%% halt): %s, realized equity $%.2f, peak $%.2f, %d closed, %d open."
                 % (risk["limit_pct"], status, practice["realized_equity"], practice["realized_peak"],
                    practice["trades"], practice["open"]))
    L.append("  Pass bar for %s+screen: %s" % (v["policy"], "; ".join("%s %s" % (c["check"], "yes" if c["ok"] else "NO")
                                                                     for c in v["verdict"])))
    mo = v.get("moonshots") or {}
    if mo:
        L.append("  Rule P8 (moonshot, docs/MEME.md s.10): momentum fired %d times on %d coins, %d passed the checks, "
                 "%d filled, %d cancelled; best closed %s%%, %s%% of closed above +100%%." % (
                     mo["fired"], mo["coins"], mo["passed"], mo["filled"], mo["cancelled"],
                     mo["best"] if mo["best"] is not None else "n/a", mo["share_100"] if mo["share_100"] is not None else "n/a"))
    g = v.get("migrations") or {}
    if g:
        L.append("  Rule P7 (after migration, docs/MEME.md s.9): %d graduates seen, %d decided, %d too late; P7 said buy %d, "
                 "control %d; %d filled, %d cancelled." % (g["seen"], g["decided"], g["missed"], g["yes"], g["yes_base"],
                                                         g["filled"], g["cancelled"]))
    if v.get("error"):
        L.append("  Last error: %s" % v["error"])
    return L
