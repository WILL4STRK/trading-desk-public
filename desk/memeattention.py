"""Rule P11, attention, and its controls (docs/MEME.md section 22). desk/meme.py calls `decide` at each P0 decision.

Every line is P0's decision (hour volume >= $20k, every 15 minutes per coin) on a busy (DexScreener m5 buys >= 20)
Solana coin whose tracked pool is pump.fun or PumpSwap:
  attention        boosted (boosts.active > 0: PAID promotion, not views), buyers per covered minute >= N   (the rule)
  attention_count  boosted, DexScreener m5 buys >= N_B, no Helius read                       (control b, must beat)
  attention_crowd  not boosted, in the seed-22 15 % sample, buyers per covered minute >= N   (control c, reported)
  attention_all    P0 itself since the first shadow of the three lines above                  (control a, must beat)

Buyers come from one Helius getTransactionsForAddress read of the pool's last <= 300 successful transactions in the 15
minutes before the decision, on P11's own key and its own daily credit cap (a hard stop). Off unless the setting
meme_attention is on and the key file exists.

Changing a number or a definition here after a result is seen restarts the count (docs/MEME.md section 22).
"""
import json
import math
import os
import random
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

POLICY = "attention"
COUNT, CROWD = "attention_count", "attention_crowd"
CONTROLS = (COUNT, CROWD)
POLICIES = (POLICY,) + CONTROLS
EVERYTHING = "attention_all"       # control (a): P0 without the screen, entries since the first P11-family shadow
STATE_KEY = "attention_v1"
READS_LOG = "attention_reads.jsonl"
MIN_PER_SLICE = 10

MIN_M5_BUYS = 20                   # busy (s.22.A)
DEXES = ("pumpfun", "pumpswap")    # pool kinds (s.22.A: Meteora 5.9 % of the rerun, under 10 %)
N_BPM = 34.66                      # buyers per covered minute, p75 of the 300-transaction rerun (s.22.A)
N_BUYS = 370                       # DexScreener m5 buys, p75 at the same points (control b)
SAMPLE = 0.15                      # control (c) sample share of busy, not-boosted coins
SEED = 22
WINDOW_S = 900
LIMIT = 300
MIN_COVER_S = 60
BOT_SWAPS = 5                      # more than this many buys and sells of the coin in one read: bot-like

DAY_CAP = 30000                    # Helius credits a UTC day, hard stop (s.22.E)
RESERVE = 40                       # a read (<= 300 full transactions: 30) plus a creator lookup (10)
TIMEOUT_S = 15
MIN_GAP_S = 1.0                    # at most one request a second
HELIUS = "https://mainnet.helius-rpc.com/?api-key="
KEY_FILE = Path(os.environ.get("DESK_P11_KEY") or r"C:\Trading\secrets\helius-p11.key")
CREATORS_MAX = 2000                # cached creators kept in the record


class CapReached(RuntimeError):
    pass


# ---- the frozen definitions (pure) --------------------------------------------------------------------------------

def m5_buys(pair):
    return int(((((pair or {}).get("txns") or {}).get("m5") or {}).get("buys")) or 0)


def boosts(pair):
    return int((((pair or {}).get("boosts") or {}).get("active")) or 0)


def eligible(chain, pair):
    """Busy and on a pump.fun or PumpSwap pool (s.22.A)."""
    return chain == "solana" and (pair or {}).get("dexId") in DEXES and m5_buys(pair) >= MIN_M5_BUYS


def decision_round(ts):
    return int(ts // WINDOW_S)


def sampled(key, rnd):
    """Control (c)'s draw for this coin in this round: random.Random("22:<round>:<coin key>") under 0.15 (s.22.A)."""
    return random.Random("%d:%d:%s" % (SEED, rnd, key)).random() < SAMPLE


def credits_for(n_full):
    """Helius full-mode billing: 10 credits per 100 transactions returned, rounded up, minimum 10."""
    return max(10, 10 * math.ceil(n_full / 100))


def _keys(tx):
    msg = tx["transaction"]["message"]
    raw = msg.get("accountKeys") or []
    if raw and isinstance(raw[0], dict):
        keys = [k["pubkey"] for k in raw]
        signers = [bool(k.get("signer")) for k in raw]
    else:
        keys = list(raw)
        n = (msg.get("header") or {}).get("numRequiredSignatures", 1)
        signers = [i < n for i in range(len(keys))]
    return keys, signers


def _amount(tb):
    u = tb.get("uiTokenAmount") or {}
    try:
        return int(u.get("amount") or 0) / (10 ** int(u.get("decimals") or 0))
    except (TypeError, ValueError):
        return 0.0


def _owner_deltas(tx, mint):
    """{owner: [pre, post]} of `mint` from pre/postTokenBalances."""
    meta = tx.get("meta") or {}
    out = {}
    for side, name in ((0, "preTokenBalances"), (1, "postTokenBalances")):
        for tb in meta.get(name) or []:
            if tb.get("mint") == mint and tb.get("owner"):
                out.setdefault(tb["owner"], [0.0, 0.0])[side] += _amount(tb)
    return out


def swaps(tx, mint):
    """Buys and sells of `mint` in one transaction (s.22.A): a signer whose balance rises while the non-signers' summed
    balance falls is a buyer, the reverse a seller. Any quote token. A failed transaction has none."""
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return []
    keys, signers = _keys(tx)
    signed = {w for w, s in zip(keys, signers) if s}
    od = _owner_deltas(tx, mint)
    other = sum(b - a for o, (a, b) in od.items() if o not in signed)
    if other == 0:
        return []
    out = []
    for w in sorted(signed):
        a, b = od.get(w, (0.0, 0.0))
        if b > a and other < 0:
            out.append({"wallet": w, "side": "buy", "slot": tx.get("slot")})
        elif b < a and other > 0:
            out.append({"wallet": w, "side": "sell", "slot": tx.get("slot")})
    return out


def measure(txs, mint, pool, creator, now):
    """Buyers per covered minute from one read (s.22.A): distinct buyers without the pool, the creator and bot-like
    wallets (more than 5 buys and sells, or a buy and a sell in one slot), over the covered time."""
    all_swaps = [s for t in txs for s in swaps(t, mint)]
    per = {}
    for s in all_swaps:
        per.setdefault(s["wallet"], []).append(s)
    bots = set()
    for w, ss in per.items():
        sides = {}
        for s in ss:
            sides.setdefault(s["slot"], set()).add(s["side"])
        if len(ss) > BOT_SWAPS or any(len(v) == 2 for v in sides.values()):
            bots.add(w)
    raw = {s["wallet"] for s in all_swaps if s["side"] == "buy"}
    buyers = raw - bots - {pool, creator}
    if len(txs) < LIMIT:
        covered = WINDOW_S
    else:
        oldest = min((t.get("blockTime") or now) for t in txs)
        covered = max(MIN_COVER_S, int(now - oldest))
    return {"txs": len(txs), "buyers": len(buyers), "bots": len(bots & raw), "covered_s": covered,
            "bpm": round(len(buyers) * 60 / covered, 4)}


def lines_for(all_fires, chain, key, pair, now, can_open):
    """Which reads a decision needs, before any network: {"read": reason or None, "count": fires, "sampled": bool}.
    reason "attention" for a boosted coin, "crowd" for a sampled not-boosted one; None when the line it would serve has
    an open shadow or a cooldown on the coin (can_open(line) is False) or the coin is not eligible."""
    out = {"read": None, "count": False, "sampled": False}
    if not all_fires or not eligible(chain, pair):
        return out
    if boosts(pair) > 0:
        out["count"] = m5_buys(pair) >= N_BUYS
        if can_open(POLICY):
            out["read"] = POLICY
    else:
        out["sampled"] = sampled(key, decision_round(now))
        if out["sampled"] and can_open(CROWD):
            out["read"] = "crowd"
    return out


def decide_from(plan, reading):
    """{line: fires} from the plan and a read's measure (or None when no read was made or it failed)."""
    bpm = (reading or {}).get("bpm")
    hot = bpm is not None and bpm >= N_BPM
    return {POLICY: plan["read"] == POLICY and hot, COUNT: plan["count"], CROWD: plan["read"] == "crowd" and hot}


def since(rows, start_ts):
    return [x for x in rows if start_ts is not None and x.get("ts") is not None and x["ts"] >= start_ts]


# ---- Helius, P11's own key and cap --------------------------------------------------------------------------------

def _day(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def book(s, now):
    """P11's record in the model save: the day's credits and capped count, cached creators, the last error."""
    b = s.get(STATE_KEY)
    if not isinstance(b, dict):
        b = s[STATE_KEY] = {}
    if b.get("day") != _day(now):
        b.update(day=_day(now), credits=0, capped=0, reads=0)
    b.setdefault("creators", {})
    return b


def load_key(path=None):
    p = Path(path or KEY_FILE)
    try:
        k = p.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return k or None


def http_post(url, body, timeout=TIMEOUT_S):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json",
                                          "User-Agent": "trading-desk/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


POST = http_post                   # tests replace this; nothing else reaches the network


class Reader:
    """One-request-a-second Helius reads on P11's key under the day's cap. The key never appears in an error."""

    _last = 0.0

    def __init__(self, key, rec, clock=time.monotonic, sleep=time.sleep):
        self.key, self.rec, self.clock, self.sleep = key, rec, clock, sleep

    def _call(self, params, cost_max):
        if self.rec["credits"] + cost_max > DAY_CAP:
            raise CapReached("P11 Helius cap %d reached for %s" % (DAY_CAP, self.rec["day"]))
        wait = MIN_GAP_S - (self.clock() - Reader._last)
        if wait > 0:
            self.sleep(wait)
        Reader._last = self.clock()
        try:
            r = POST(HELIUS + self.key, {"jsonrpc": "2.0", "id": 1, "method": "getTransactionsForAddress",
                                         "params": params})
        except Exception as err:
            raise RuntimeError(("%s: %s" % (type(err).__name__, err)).replace(self.key, "***")) from None
        if not isinstance(r, dict) or "error" in r:
            e = (r or {}).get("error") if isinstance(r, dict) else r
            raise RuntimeError(("Helius error: %s" % (e,)).replace(self.key, "***")[:300])
        data = ((r.get("result") or {}).get("data")) or []
        self.rec["credits"] += credits_for(len(data))
        return data

    def creator(self, mint):
        cache = self.rec["creators"]
        if mint not in cache:
            data = self._call([mint, {"transactionDetails": "full", "sortOrder": "asc", "limit": 1, "encoding": "json",
                                      "maxSupportedTransactionVersion": 1}], 10)
            keys, signers = _keys(data[0]) if data else ([], [])
            cache[mint] = keys[0] if keys and signers and signers[0] else None
            while len(cache) > CREATORS_MAX:
                cache.pop(next(iter(cache)))
        return cache[mint]

    def read(self, mint, pool, now):
        if self.rec["credits"] + RESERVE > DAY_CAP:
            raise CapReached("P11 Helius cap %d reached for %s" % (DAY_CAP, self.rec["day"]))
        txs = self._call([pool, {"transactionDetails": "full", "sortOrder": "desc", "limit": LIMIT, "encoding": "json",
                                 "maxSupportedTransactionVersion": 1,
                                 "filters": {"blockTime": {"gte": int(now - WINDOW_S), "lt": int(now)},
                                             "status": "succeeded"}}], 30)
        creator = self.creator(mint)
        return measure(txs, mint, pool, creator, now)


def decide(enabled, all_fires, chain, key, mint, pair, now, can_open, rec, log, key_text=None):
    """{line: fires} and the record written on each P11-family shadow. Off (all False, nothing read) unless enabled
    and P11's key file holds a key. Every read, capped skip and failed read is logged with the day's credits."""
    off = (dict.fromkeys(POLICIES, False), None)
    if not enabled:
        return off
    plan = lines_for(all_fires, chain, key, pair, now, can_open)
    info = {"boosts": boosts(pair), "m5_buys": m5_buys(pair), "round": decision_round(now), "sampled": plan["sampled"]}
    if not any((plan["read"], plan["count"])):
        return off
    reading = None
    if plan["read"]:
        k = key_text or load_key()
        if not k:
            return off
        row = {"ts": now, "key": key, "round": info["round"], "reason": plan["read"]}
        before = rec["credits"]
        try:
            reading = Reader(k, rec).read(mint, pair.get("pairAddress"), now)
            row.update(reading)
        except CapReached:
            rec["capped"] = rec.get("capped", 0) + 1
            row["skipped"] = "cap"
        except Exception as err:
            row["error"] = str(err)[:300]
            rec["error"] = "%s: %s" % (datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), row["error"])
        row.update(credits=rec["credits"] - before, day_credits=rec["credits"])
        rec["reads"] = rec.get("reads", 0) + ("skipped" not in row)
        log(READS_LOG, row)
        if reading:
            info.update(reading)
    return decide_from(plan, reading), info
