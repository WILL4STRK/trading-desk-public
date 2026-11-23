"""The Terminal tab: a read-only list of watched Solana coins that did NOT fully pass the safety check, and a log
of the swaps you made yourself in Jupiter's widget.

The desk never holds a key and never sends a transaction. Swaps are built by Jupiter's Plugin in your browser and
signed in your own wallet (Phantom, Solflare). This module only reads model.json, screens.json and shadows.jsonl,
and appends what the widget reports after a swap to manual.jsonl. Nothing here changes a rule, a shadow, an exit
or a cost.
"""
import json
import os
import re
import threading
import time

from . import meme
from . import settings as S

SOL_MINT = "So11111111111111111111111111111111111111112"
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_MINT = re.compile(r"[%s]{32,44}" % B58)
_TXID = re.compile(r"[%s]{64,90}" % B58)       # a Solana signature: 64 bytes, 86 to 88 base58 characters
MAX_SELL_TAX = 10.0                             # % ; above this the swap panel stays shut
# Token-2022 extensions that can stop a sale or take the tokens back, matched in the screen's risk names and reasons
TOKEN_2022 = ((("permanent delegate",), "permanent delegate: another account can move or burn your tokens at any time"),
              (("transfer hook",), "transfer hook: the token's own program runs on every transfer and can refuse a sale"),
              (("non-transferable", "non transferable"), "non-transferable: the token cannot be sent, so it cannot be sold"))
MANUAL = "manual.jsonl"
TICKER_MAX = 60
_lock = threading.Lock()


def valid_mint(s):
    """True for a base58 string of 32 to 44 characters (a Solana address). Checked before any link or prefill."""
    return isinstance(s, str) and bool(_MINT.fullmatch(s))


def valid_txid(s):
    return isinstance(s, str) and bool(_TXID.fullmatch(s))


def links(mint, pool):
    """DexScreener, Solscan and RugCheck links, or None for any address that is not valid base58."""
    ok = valid_mint(mint)
    return {"dexscreener": "https://dexscreener.com/solana/%s" % pool if valid_mint(pool) else None,
            "solscan": "https://solscan.io/token/%s" % mint if ok else None,
            "rugcheck": "https://rugcheck.xyz/tokens/%s" % mint if ok else None}


def fully_passed(sc):
    return bool(sc) and sc.get("verdict") == "pass" and sc.get("strict") == "pass"


def failed_checks(sc):
    """Every failed check with its plain reason, from the screen's why lists. A coin never screened is one failed
    check ("not screened yet"): unknown is never a pass."""
    if not sc:
        return [{"check": "safety", "verdict": "unknown", "reason": "not screened yet"}]
    out = []
    if sc.get("verdict") != "pass":
        out += [{"check": "safety", "verdict": sc.get("verdict") or "unknown", "reason": str(w)}
                for w in (sc.get("why") or ["no reason given"])]
    if sc.get("strict") != "pass":
        out += [{"check": "strict", "verdict": sc.get("strict") or "unknown", "reason": str(w)}
                for w in (sc.get("strict_why") or ["no reason given"])]
    return out


_TAX = re.compile(r"(sell tax|transfer fee|transfer tax|\btax\b)", re.I)
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def cant_sell(sc):
    """Reasons you may not be able to sell, from what the screen saved: freeze authority enabled, a honeypot, a
    Token-2022 permanent delegate, transfer hook or non-transferable mint, or a sell tax (or transfer fee) above 10%.
    A tax risk with no readable size counts as above 10%."""
    sc = sc or {}
    rep = sc.get("report") or {}
    texts = [str(r.get("name") or "") for r in rep.get("risks") or [] if isinstance(r, dict)]
    texts += [str(w) for w in (sc.get("why") or []) + (sc.get("strict_why") or [])]
    why = []
    if any("freeze authority" in t.lower() for t in texts):
        why.append("freeze authority is enabled: the creator can freeze your tokens")
    if any("honeypot" in t.lower() for t in texts) or str(rep.get("is_honeypot")) == "1":
        why.append("reported as a honeypot: buys work, sells may not")
    for words, reason in TOKEN_2022:
        if any(w in t.lower() for t in texts for w in words):
            why.append(reason)
    taxes = []
    for t in texts:
        if _TAX.search(t):
            m = _PCT.search(t)
            taxes.append(float(m.group(1)) if m else None)
    try:
        if rep.get("sell_tax") not in (None, ""):
            taxes.append(float(rep["sell_tax"]) * 100)
    except (TypeError, ValueError):
        taxes.append(None)
    high = [x for x in taxes if x is None or x > MAX_SELL_TAX]
    if high:
        known = [x for x in high if x is not None]
        why.append("sell tax %s: above %.0f%%" % ("%.0f%%" % max(known) if known else "of unknown size", MAX_SELL_TAX))
    return why


def _record(key, shadows, books):
    """The bots' record on one coin: closed shadows and closed practice trades, with their net %."""
    sh = [r["net_pct"] for r in shadows if r.get("key") == key and r.get("net_pct") is not None]
    pr = [t["ret_pct"] for b in books for t in b.get("closed") or [] if t.get("key") == key and t.get("ret_pct") is not None]
    avg = lambda xs: round(sum(xs) / len(xs), 2) if xs else None
    return {"shadows": len(sh), "shadows_net_pct": avg(sh), "practice": len(pr), "practice_net_pct": avg(pr)}


def _age_h(t, now):
    c = t.get("created")
    try:
        return round((now - float(c) / 1000) / 3600, 2) if c else None
    except (TypeError, ValueError):
        return None


def view(now=None):
    """What /api/meme/terminal shows. Reads files only: no network, no writes."""
    now = time.time() if now is None else now
    st, s = S.load(), meme.load()
    tracked = s.get("tracked") or {}
    screens = meme.read_json(meme.folder() / "screens.json", {}) or {}
    shadows = meme._jsonl("shadows.jsonl")
    books = [s.get("account") or {}] + list((s.get("books") or {}).values())
    allow = bool(st.get("meme_terminal_allow_unsellable"))
    coins, ticker = [], []
    for k, t in tracked.items():
        if t.get("chain") != "solana" or not t.get("pool"):
            continue
        sc = screens.get(k)
        f = t.get("f") or {}
        mint, pool = t.get("addr"), t.get("pool")
        ok_mint = valid_mint(mint)
        ticker.append({"key": k, "symbol": t.get("symbol"), "px": t.get("px"), "r1h": f.get("r1h"),
                       "last_strong_ts": t.get("last_strong_ts")})
        if fully_passed(sc):
            continue
        block = cant_sell(sc)
        coins.append({"key": k, "symbol": t.get("symbol"), "name": t.get("name"), "mint": mint if ok_mint else None,
                      "px": t.get("px"), "r1h": f.get("r1h"), "r6h": f.get("r6h"), "r24h": f.get("r24h"),
                      "liq": t.get("liq"), "v1h": f.get("v1h"), "age_h": _age_h(t, now), "dex": t.get("dex"),
                      "screen": (sc or {}).get("verdict", "unknown"), "strict": (sc or {}).get("strict", "unknown"),
                      "failed": failed_checks(sc), "cant_sell": block,
                      "swap": "invalid_mint" if not ok_mint else "blocked" if block and not allow else "confirm",
                      "last_strong_ts": t.get("last_strong_ts"), "seen": t.get("seen"),
                      "record": _record(k, shadows, books), "links": links(mint, pool)})
    coins.sort(key=lambda c: -(c["seen"] or 0))
    ticker.sort(key=lambda x: -(x["last_strong_ts"] or 0))
    return {"coins": coins, "ticker": ticker[:TICKER_MAX], "manual": manual_log()[::-1][:200],
            "allow_unsellable": allow, "sol_mint": SOL_MINT, "strong": {"r1h": meme.STRONG_R1H, "buy_ratio_m5": meme.STRONG_BUY},
            "now": now}


def manual_log():
    return meme._jsonl(MANUAL)


def _num(x, name):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or x != x or x < 0 or x > 1e30:
        raise ValueError("%s must be a number of 0 or more" % name)
    return x


def record_manual(body, now=None):
    """Append one swap the widget reported (txid, mint, side, amounts, time) to manual.jsonl, with that coin's failed
    checks and the desk's price at this moment. The same txid twice is written once. Raises ValueError on bad input."""
    now = time.time() if now is None else now
    txid, mint, side = body.get("txid"), body.get("mint"), body.get("side")
    if not valid_txid(txid):
        raise ValueError("txid must be a Solana signature (base58)")
    if not valid_mint(mint) or mint == SOL_MINT:
        raise ValueError("mint must be a coin's Solana address (base58, 32 to 44 characters)")
    if side not in ("buy", "sell"):
        raise ValueError("side must be buy or sell")
    in_mint = body.get("in_mint") or (SOL_MINT if side == "buy" else mint)
    out_mint = body.get("out_mint") or (mint if side == "buy" else SOL_MINT)
    if not valid_mint(in_mint) or not valid_mint(out_mint) or (out_mint if side == "buy" else in_mint) != mint:
        raise ValueError("in_mint and out_mint must be Solana addresses, with the coin on the %s side"
                         % ("out" if side == "buy" else "in"))
    amt_in, amt_out = _num(body.get("in_amount"), "in_amount"), _num(body.get("out_amount"), "out_amount")
    try:
        at = float(body.get("time") or now)
    except (TypeError, ValueError):
        raise ValueError("time must be seconds since 1970")
    k = "solana:" + mint
    t = (meme.load().get("tracked") or {}).get(k) or {}
    sc = (meme.read_json(meme.folder() / "screens.json", {}) or {}).get(k)
    row = {"txid": txid, "mint": mint, "key": k, "symbol": t.get("symbol"), "side": side,
           "in_mint": in_mint, "out_mint": out_mint,
           "in_amount": amt_in, "out_amount": amt_out, "time": at, "at": meme._iso(at), "logged": now,
           "px": t.get("px"), "screen": (sc or {}).get("verdict", "unknown"), "strict": (sc or {}).get("strict", "unknown"),
           "failed": [] if fully_passed(sc) else failed_checks(sc), "cant_sell": cant_sell(sc)}
    return append_atomic(row)


def append_atomic(row):
    """Append-only and atomic: the new file is the old bytes, unchanged, plus one line, written to a temp file,
    flushed to disk and swapped in with os.replace. A reader sees the whole old file or the whole new one, never half
    a line; a crash before the swap leaves the old file as it was. Returns False if the txid is already logged."""
    d = meme.folder()
    d.mkdir(parents=True, exist_ok=True)
    path, tmp = d / MANUAL, d / (MANUAL + ".tmp")
    with _lock:
        try:
            old = path.read_bytes()
        except FileNotFoundError:
            old = b""
        for line in old.splitlines():
            try:
                if json.loads(line).get("txid") == row["txid"]:
                    return False
            except (ValueError, AttributeError):
                continue
        sep = b"\n" if old and not old.endswith(b"\n") else b""
        data = old + sep + json.dumps(row, separators=(",", ":")).encode("utf-8") + b"\n"
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    return True


# ---- live prices: the selected coin's on-chain accounts, read by the desk for the Terminal page
# Solana's public RPC refuses requests from a browser page (HTTP 403 for an Origin header; the websocket closes with
# 1006), so the page asks the desk. The page names a tracked coin, never an address: the desk works out the accounts
# itself (the coin's pump.fun curve or PumpSwap pool, its mint, and the pool's two vaults) and returns their raw bytes
# from one getMultipleAccounts. The page checks owners, layouts and links and decodes them (desk/static/terminal.js).
RPC = "https://api.mainnet-beta.solana.com"
PUMP_AMM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
POOL_DISC = bytes([241, 154, 109, 4, 17, 177, 109, 188])     # PumpSwap Pool (pump-fun/pump-public-docs idl/pump_amm.json)
CHAIN_DEX = ("pumpfun", "pumpswap")
CHAIN_EVERY = 0.5          # seconds between RPC calls from the desk, at most
CHAIN_CACHE = 0.8          # seconds an answer for one coin is reused (two open tabs make one call)
_chain_lock = threading.Lock()
_chain_last = [0.0]
_chain_cache = {}
_chain_vaults = {}
# key -> (dex, mint, pool) for tracked pump.fun / PumpSwap coins, so a poll never opens model.json: on Windows an open
# model.json can make the scanner's save fail (WinError 5 on replace). Refreshed from model.json at most once a minute;
# a key not in the map reloads it at most once every 5 s.
COINS_EVERY, COINS_MISS_EVERY = 60.0, 5.0
_coins = {"at": -1e18, "miss_at": -1e18, "map": {}}


class ChainError(Exception):
    pass


def _b58(b):
    n, s = int.from_bytes(b, "big"), ""
    while n:
        n, r = divmod(n, 58)
        s = B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s


def _rpc_accounts(addrs, post=None):
    """[{owner, data: [base64, "base64"]} | None] for addrs, from the public RPC (read-only)."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getMultipleAccounts",
                       "params": [addrs, {"encoding": "base64", "commitment": "confirmed"}]}).encode()
    if post is None:
        import urllib.request
        req = urllib.request.Request(RPC, body, {"Content-Type": "application/json", "User-Agent": meme.UA})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read().decode("utf-8"))
        except (OSError, ValueError) as e:
            raise ChainError("Solana RPC did not answer (%s)" % type(e).__name__)
    else:
        d = post(body)
    v = ((d or {}).get("result") or {}).get("value")
    if not isinstance(v, list) or len(v) != len(addrs):
        raise ChainError("Solana RPC gave no accounts")
    out = []
    for a in v:
        ok = isinstance(a, dict) and isinstance(a.get("owner"), str) and isinstance(a.get("data"), list) and len(a["data"]) == 2 \
            and isinstance(a["data"][0], str) and a["data"][1] == "base64" and len(a["data"][0]) < 20000
        out.append({"owner": a["owner"], "data": a["data"]} if ok else None)
    return out


def _coin(key, now):
    """(dex, mint, pool) for a tracked pump.fun or PumpSwap coin, or None; reads model.json only as described above."""
    c = _coins
    if now - c["at"] >= COINS_EVERY or (key not in c["map"] and now - c["miss_at"] >= COINS_MISS_EVERY):
        if key not in c["map"]:
            c["miss_at"] = now
        tracked = meme.load().get("tracked") or {}
        c["map"] = {k: (t["dex"], t["addr"], t["pool"]) for k, t in tracked.items()
                    if isinstance(t, dict) and t.get("chain") == "solana" and t.get("dex") in CHAIN_DEX
                    and valid_mint(t.get("addr")) and valid_mint(t.get("pool"))}
        c["at"] = now
    return c["map"].get(key)


def chain(key, post=None, now=None):
    """The raw accounts for one tracked pump.fun or PumpSwap coin: {kind, addrs: {role: address}, accounts: {address:
    {owner, data} | None}}. Raises ChainError for anything else. At most one RPC call every 0.5 s, answers reused 0.8 s.
    No file is read for a cached answer, and model.json at most once a minute (see _coin)."""
    import base64
    now = time.time() if now is None else now
    if not isinstance(key, str):
        raise ChainError("on-chain prices are for tracked pump.fun and PumpSwap coins only")
    hit = _chain_cache.get(key)
    if hit and now - hit[0] < CHAIN_CACHE:
        return hit[1]
    coin = _coin(key, now)
    if not coin:
        raise ChainError("on-chain prices are for tracked pump.fun and PumpSwap coins only")
    t = {"dex": coin[0], "addr": coin[1], "pool": coin[2]}
    with _chain_lock:
        wait = _chain_last[0] + CHAIN_EVERY - time.time()
        if wait > 0:
            time.sleep(wait)
        addrs = {"pool": t["pool"], "mint": t["addr"]}
        vaults = _chain_vaults.get((t["pool"], t["addr"]))       # a pool's vaults never change: read them once
        if vaults:
            addrs.update(vaults)
        accts = _rpc_accounts(list(addrs.values()), post)
        if t["dex"] == "pumpswap" and not vaults:
            pool = accts[0]
            raw = base64.b64decode(pool["data"][0]) if pool and pool["owner"] == PUMP_AMM else b""
            if raw[:8] == POOL_DISC and len(raw) >= 203:
                vaults = {"base_vault": _b58(raw[139:171]), "quote_vault": _b58(raw[171:203])}
                _chain_vaults[(t["pool"], t["addr"])] = vaults
                addrs.update(vaults)
                accts = _rpc_accounts(list(addrs.values()), post)
        _chain_last[0] = time.time()
    out = {"kind": "curve" if t["dex"] == "pumpfun" else "pool", "addrs": addrs, "at": now,
           "accounts": {a: x for a, x in zip(list(addrs.values()), accts)}}
    _chain_cache[key] = (now, out)
    return out
