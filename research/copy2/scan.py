"""C2 deep scan (docs/MEME.md section 20.A-20.D). Paper research only: Helius reads, no order path.

    python research/copy2/scan.py run      every phase in order; resumes where it stopped (state in C:\\Trading\\research\\copy2\\scan.db)
    python research/copy2/scan.py status

Memory-safe: buys, wallets, funders and per-wallet results live in sqlite on disk; one wallet's history is in memory at a time.
Helius at most 8 calls/s; the credit meter is stored in the database after every call and the scan stops hard at the budget.
"""
import argparse
import hashlib
import json
import math
import os
import sqlite3
import statistics
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.copy import common as C
from research.copy import discover as D
from research.copy2 import snapshot as S

T20 = S.T20
HIST_S = 180 * 86400
SPLIT = T20 - 90 * 86400
H0 = T20 - HIST_S
TOP = 300
MIN_SET_COINS = 3
COIN_TX_CAP = 1_000_000
EXCHANGE_SIGS = 50_000
TOO_ACTIVE = 20_000
MIN_COINS = 5
MAX_SHARE = 0.40
BOOT = 1000
PERM = 1000
SEED = 20
FREEZE = 50
BUDGET = 20_000_000
RPS = 8
COST = C.COST_SIDE
LIST = Path(__file__).resolve().parent / "WALLETS-C2.json"


class Budget(RuntimeError):
    pass


# ---- pure rules --------------------------------------------------------------------------------------------------------------

def trades(swaps, set_mints, h0=H0, split=SPLIT, t20=T20):
    """Section 20.D trades: one per sell in [h0, t20) with a known basis. Average-cost basis per mint in SOL, buys at sol x 1.0175,
    sells at sol x 0.9825. Returns [(window 'A'|'B', mint, pnl_sol, in_set)] in time order."""
    pos, out = {}, []
    for s in sorted(swaps, key=lambda s: (s["ts"], s.get("sig") or "")):
        if s["ts"] is None or s["ts"] >= t20:
            continue
        p = pos.setdefault(s["mint"], [0.0, 0.0])
        if s["side"] == "buy":
            if s["ts"] >= h0:
                p[0] += s["tokens"]
                p[1] += s["sol"] * (1 + COST)
            continue
        if s["ts"] < h0 or p[0] <= 0:
            continue
        q = min(s["tokens"], p[0])
        basis = p[1] * q / p[0]
        pnl = s["sol"] * (1 - COST) * (q / s["tokens"]) - basis
        p[0] -= q
        p[1] -= basis
        out.append(("A" if s["ts"] < split else "B", s["mint"], pnl, s["mint"] in set_mints))
    return out


def window_ok(tr):
    """[(mint, pnl)] of one window: net > 0, >= 5 distinct coins, largest single profit <= 40 % of net."""
    if not tr:
        return False
    net = sum(p for _, p in tr)
    if net <= 0 or len({m for m, _ in tr}) < MIN_COINS:
        return False
    return max(p for _, p in tr) <= MAX_SHARE * net


def split_outside(tr):
    a = [(m, p) for w, m, p, ins in tr if w == "A" and not ins]
    b = [(m, p) for w, m, p, ins in tr if w == "B" and not ins]
    return a, b


def eligible(a, b):
    return len({m for m, _ in a}) >= MIN_COINS and len({m for m, _ in b}) >= MIN_COINS


def qualifies(a, b):
    return window_ok(a) and window_ok(b)


def boot_se(pnls, rnd, n=BOOT):
    if len(pnls) < 2:
        return None
    sums = [sum(pnls[rnd.randrange(len(pnls))] for _ in pnls) for _ in range(n)]
    return statistics.pstdev(sums)


def chance(elig, perms=PERM, seed=SEED):
    """Null distribution of the qualifier count: each eligible wallet's outside-set trade P&Ls permuted across its own slots
    (each slot keeps its coin and window). elig: [(a, b)]. Returns (counts, mean, p95, p99)."""
    rnd = C.rng(seed)
    counts = []
    for _ in range(perms):
        n = 0
        for a, b in elig:
            slots = [("A", m) for m, _ in a] + [("B", m) for m, _ in b]
            pn = [p for _, p in a] + [p for _, p in b]
            rnd.shuffle(pn)
            na = [(m, pn[i]) for i, (w, m) in enumerate(slots) if w == "A"]
            nb = [(m, pn[i]) for i, (w, m) in enumerate(slots) if w == "B"]
            n += qualifies(na, nb)
        counts.append(n)
    cs = sorted(counts)
    pct = lambda q: cs[min(len(cs) - 1, int(math.ceil(q * len(cs))) - 1)]
    return counts, sum(counts) / len(counts), pct(0.95), pct(0.99)


def clearly_above(actual, mean, p99):
    return actual > p99 and actual >= 2 * mean


def bundles(groups, funder, is_exchange):
    """groups: {(mint, slot): [wallets]}; funder: {wallet: source or None}. Wallets in a same-slot group sharing a non-exchange
    funder. Returns (bundled wallets, multi-group wallets)."""
    seen = {}
    for g, ws in groups.items():
        by = {}
        for w in set(ws):
            f = funder.get(w)
            if f and not is_exchange(f):
                by.setdefault(f, set()).add(w)
        for f, members in by.items():
            if len(members) >= 2:
                for w in members:
                    seen.setdefault(w, set()).add((g, f))
    return set(seen), {w for w, gs in seen.items() if len(gs) > 1}


def order_key(w):
    return hashlib.sha256(w.encode()).hexdigest()


# ---- the database ------------------------------------------------------------------------------------------------------------

SCHEMA = """
create table if not exists meta(k text primary key, v text);
create table if not exists coins(mint text primary key, rank int, kind text, t0 real, payer text, creator text, first_slot int,
  cursor text, done int default 0, txs int default 0, truncated int default 0, status text);
create table if not exists buys(mint text, wallet text, ts real, slot int, sol real, tokens real, sig text,
  primary key(sig, wallet, mint));
create index if not exists buys_w on buys(wallet);
create index if not exists buys_ms on buys(mint, slot);
create table if not exists wallets(wallet text primary key, ncoins int, status text, reason text, okey text);
create table if not exists funders(wallet text primary key, src text);
create table if not exists sigs(address text primary key, n int);
create table if not exists results(wallet text primary key, data text);
"""


class DB:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.c = sqlite3.connect(str(path))
        self.c.executescript(SCHEMA)
        self.c.commit()

    def get(self, k, d=None):
        r = self.c.execute("select v from meta where k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else d

    def put(self, k, v):
        self.c.execute("insert or replace into meta values(?,?)", (k, json.dumps(v)))
        self.c.commit()


class Meter:
    """Wraps C.Helius: the running total is saved after every call and the scan stops before passing the budget."""

    def __init__(self, h, db, budget=BUDGET):
        self.h, self.db, self.budget = h, db, budget
        self.base = db.get("credits", 0)
        h.credits = 0

    @property
    def used(self):
        return self.base + self.h.credits

    def rpc(self, method, params):
        if self.used >= self.budget:
            raise Budget("credit budget %d reached" % self.budget)
        try:
            return self.h.rpc(method, params)
        finally:
            self.db.put("credits", self.used)


def gtfa(m, address, gte=None, lt=None, limit=1000, details="full", token=None, asc=True):
    bt = {}
    if gte is not None:
        bt["gte"] = int(math.floor(gte))
    if lt is not None:
        bt["lt"] = int(math.ceil(lt))
    cfg = {"transactionDetails": details, "sortOrder": "asc" if asc else "desc", "limit": limit,
           "maxSupportedTransactionVersion": C.TX_VERSION, "filters": {"status": "succeeded"}}
    if details == "full":
        cfg["encoding"] = "jsonParsed"
    if bt:
        cfg["filters"]["blockTime"] = bt
    if token:
        cfg["paginationToken"] = token
    return m.rpc("getTransactionsForAddress", [address, cfg]) or {}


# ---- phases ------------------------------------------------------------------------------------------------------------------

def load_set(db, ranked, runners, m, http=C.http_json, log=print):
    """Walk the ranked list until 300 coins pass the age rule (first transaction before T20 - 24 h); add C1's runners."""
    if db.get("set_done"):
        return
    n = db.c.execute("select count(*) from coins where kind='top' and status='ok'").fetchone()[0]
    for i, r in enumerate(ranked):
        if n >= TOP:
            break
        mint = r["mint"]
        if db.c.execute("select 1 from coins where mint=?", (mint,)).fetchone():
            continue
        first = (gtfa(m, mint, limit=1).get("data") or [None])[0]
        if not first or first.get("blockTime") is None:
            db.c.execute("insert into coins(mint, rank, kind, status) values(?,?,?,?)", (mint, i, "top", "no_tx"))
        elif first["blockTime"] >= T20 - 86400:
            db.c.execute("insert into coins(mint, rank, kind, status) values(?,?,?,?)", (mint, i, "top", "too_young"))
        else:
            db.c.execute("insert into coins(mint, rank, kind, t0, payer, status) values(?,?,?,?,?,?)",
                         (mint, i, "top", first["blockTime"], C.fee_payer(first), "ok"))
            n += 1
        db.c.commit()
    for mint in sorted(runners):
        if db.c.execute("select 1 from coins where mint=?", (mint,)).fetchone():
            continue
        first = (gtfa(m, mint, limit=1).get("data") or [None])[0]
        ok = first and first.get("blockTime") is not None and first["blockTime"] < T20 - 86400
        db.c.execute("insert into coins(mint, rank, kind, t0, payer, status) values(?,?,?,?,?,?)",
                     (mint, None, "runner", first["blockTime"] if ok else None, C.fee_payer(first) if ok else None,
                      "ok" if ok else "too_young"))
        db.c.commit()
    db.put("set_done", True)
    log("set: %d coins" % db.c.execute("select count(*) from coins where status='ok'").fetchone()[0])


def fetch_buys(db, m, creator=None, log=print):
    """Every first-24 h buy of every set coin, page by page; the cursor is saved with each page, so a restart resumes mid-coin."""
    rows = db.c.execute("select mint, t0, cursor, txs from coins where status='ok' and done=0 order by mint").fetchall()
    for mint, t0, cursor, txs in rows:
        if creator:
            db.c.execute("update coins set creator=? where mint=?", (creator(mint), mint))
        end = min(t0 + 86400, T20)
        tok = json.loads(cursor) if cursor else None
        while True:
            r = gtfa(m, mint, gte=t0, lt=end, token=tok)
            data = r.get("data") or []
            for t in data:
                for s in C.parse_swaps(t):
                    if s["mint"] != mint:
                        continue
                    if s["side"] == "buy" and s["ts"] is not None and t0 <= s["ts"] < end:
                        db.c.execute("insert or ignore into buys values(?,?,?,?,?,?,?)",
                                     (mint, s["wallet"], s["ts"], s["slot"], s["sol"], s["tokens"], s["sig"]))
                    fs = db.c.execute("select first_slot from coins where mint=?", (mint,)).fetchone()[0]
                    if s["slot"] is not None and (fs is None or s["slot"] < fs):
                        db.c.execute("update coins set first_slot=? where mint=?", (s["slot"], mint))
            txs += len(data)
            tok = r.get("paginationToken")
            trunc = txs >= COIN_TX_CAP
            db.c.execute("update coins set cursor=?, txs=?, truncated=?, done=? where mint=?",
                         (json.dumps(tok), txs, int(trunc), int(not tok or not data or trunc), mint))
            db.c.commit()
            if not tok or not data or trunc:
                break
    log("buys: %d rows" % db.c.execute("select count(*) from buys").fetchone()[0])


def mark_wallets(db, log=print):
    """Scope filter (>= 3 set coins), creators and dev payers, snipers."""
    if db.get("wallets_done"):
        return
    db.c.execute("""insert or ignore into wallets(wallet, ncoins, status)
                    select wallet, count(distinct mint), 'candidate' from buys group by wallet having count(distinct mint) >= ?""",
                 (MIN_SET_COINS,))
    dev = {r[0] for r in db.c.execute("select payer from coins where payer is not null union select creator from coins where creator is not null")}
    for w in dev:
        db.c.execute("update wallets set status='excluded', reason='creator_dev' where wallet=? and status='candidate'", (w,))
    db.c.execute("""update wallets set status='excluded', reason='sniper' where status='candidate' and wallet in (
                    select b.wallet from buys b join coins c on b.mint=c.mint where b.slot <= c.first_slot + 1)""")
    for (w,) in db.c.execute("select wallet from wallets").fetchall():
        db.c.execute("update wallets set okey=? where wallet=?", (order_key(w), w))
    db.c.commit()
    db.put("wallets_done", True)
    log("wallets: %s" % dict(db.c.execute("select coalesce(reason, status), count(*) from wallets group by 1").fetchall()))


def first_funder(m, db, w):
    r = db.c.execute("select src from funders where wallet=?", (w,)).fetchone()
    if r:
        return r[0]
    txs = gtfa(m, w, limit=20).get("data") or []
    fin = D.first_sol_in([x for t in txs for x in C.parse_transfers(t)], w)
    src = fin["src"] if fin else ""
    db.c.execute("insert or replace into funders values(?,?)", (w, src))
    db.c.commit()
    return src


def sig_count(m, db, addr, gte=H0):
    r = db.c.execute("select n from sigs where address=?", (addr,)).fetchone()
    if r:
        return r[0]
    n, tok = 0, None
    while n < EXCHANGE_SIGS:
        r = gtfa(m, addr, gte=gte, lt=T20, details="signatures", token=tok)
        n += len(r.get("data") or [])
        tok = r.get("paginationToken")
        if not tok or not r.get("data"):
            break
    n = min(n, EXCHANGE_SIGS)
    db.c.execute("insert or replace into sigs values(?,?)", (addr, n))
    db.c.commit()
    return n


def mark_bundlers(db, m, log=print):
    if db.get("bundlers_done"):
        return
    groups = {}
    for mint, slot in db.c.execute("""select b.mint, b.slot from buys b join wallets w on b.wallet=w.wallet
                                      where w.status='candidate' group by b.mint, b.slot""").fetchall():
        ws = [r[0] for r in db.c.execute("select distinct wallet from buys where mint=? and slot=?", (mint, slot))]
        if len(ws) >= 2:
            groups[(mint, slot)] = ws
    funder = {}
    for ws in groups.values():
        for w in ws:
            if w not in funder:
                funder[w] = first_funder(m, db, w) or None
    bund, multi = bundles(groups, funder, lambda f: sig_count(m, db, f) >= EXCHANGE_SIGS)
    for w in bund:
        db.c.execute("update wallets set status='excluded', reason=? where wallet=? and status='candidate'",
                     ("multi_cluster" if w in multi else "bundler", w))
    db.c.commit()
    db.put("bundlers_done", True)
    log("bundlers: %d (multi %d)" % (len(bund), len(multi)))


def scan_wallets(db, m, set_mints, log=print):
    """One wallet at a time, in SHA-256(address) order: signature count, history, exclusions 4-5, trades. Results stream to disk."""
    todo = db.c.execute("""select wallet from wallets where status='candidate'
                           and wallet not in (select wallet from results) order by okey""").fetchall()
    for i, (w,) in enumerate(todo):
        n = sig_count(m, db, w)
        res = {"sigs": n}
        if n >= EXCHANGE_SIGS:
            res["reason"] = "exchange"
        else:
            txs, tok = [], None
            while len(txs) <= TOO_ACTIVE:
                r = gtfa(m, w, gte=H0, lt=T20, token=tok)
                txs += r.get("data") or []
                tok = r.get("paginationToken")
                if not tok or not r.get("data"):
                    break
            if len(txs) > TOO_ACTIVE:
                res["reason"] = "too_active"
            else:
                sw = [s for t in txs for s in C.parse_swaps(t, {w})]
                st = D.wallet_stats(sw, H0, T20)
                why = D.bot_reason(st, None)
                if why:
                    res["reason"] = why
                else:
                    tr = trades(sw, set_mints)
                    a, b = split_outside(tr)
                    res.update({"a": a, "b": b, "swaps_per_day": st["swaps_per_day"],
                                "set_pnl": sum(p for _, _, p, ins in tr if ins), "eligible": eligible(a, b)})
            del txs
        db.c.execute("insert or replace into results values(?,?)", (w, json.dumps(res)))
        db.c.commit()
        if i % 200 == 0:
            log("wallets %d of %d, credits %d" % (i, len(todo), m.used))


def select(db, out=LIST, log=print):
    """Qualify, rank, the chance test, the random control. Writes WALLETS-C2.json only if the count is clearly above chance."""
    elig, quals, pool, reasons = [], [], [], {}
    for w, data in db.c.execute("select wallet, data from results order by wallet"):
        r = json.loads(data)
        if r.get("reason"):
            reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
            continue
        if not r.get("eligible"):
            reasons["not_eligible"] = reasons.get("not_eligible", 0) + 1
            continue
        a, b = [tuple(x) for x in r["a"]], [tuple(x) for x in r["b"]]
        elig.append((a, b))
        row = {"address": w, "swaps_per_day": r["swaps_per_day"], "a_net": sum(p for _, p in a), "b_net": sum(p for _, p in b),
               "a_coins": len({x for x, _ in a}), "b_coins": len({x for x, _ in b}), "set_pnl": r.get("set_pnl")}
        (quals if qualifies(a, b) else pool).append((row, [p for _, p in b]))
    _, mean, p95, p99 = chance(elig)
    rnd = C.rng(SEED)
    ranked = []
    for row, bp in sorted(quals, key=lambda q: q[0]["address"]):
        se = boot_se(bp, rnd)
        row["b_score"] = row["b_net"] / se if se else None
        ranked.append(row)
    ranked.sort(key=lambda r: (-(r["b_score"] or -1e18), r["address"]))
    top = ranked[:FREEZE]
    for i, r in enumerate(top, 1):
        r["tag"] = "C2-%02d" % i
    rnd_rows = D.draw_random(top, [p for p, _ in pool], seed=SEED)
    for i, r in enumerate(rnd_rows, 1):
        r["tag"] = "C2R-%02d" % i
    rep = {"eligible": len(elig), "qualified": len(quals), "null_mean": mean, "null_p95": p95, "null_p99": p99,
           "clearly_above_chance": clearly_above(len(quals), mean, p99), "reasons": reasons, "credits": db.get("credits", 0),
           "frozen": len(top), "random": len(rnd_rows)}
    if rep["clearly_above_chance"]:
        D.write_list(out, {"study": "C2", "section": "20", "t20": T20, "t20_commit": S.T20_COMMIT, "c2": top, "random": rnd_rows,
                           "report": rep})
    db.put("report", rep)
    log(json.dumps(rep, sort_keys=True))
    return rep


def run(home=S.HOME, budget=BUDGET, log=print):
    db = DB(Path(home) / "scan.db")
    ranked = json.loads((Path(home) / "ranked.json").read_text(encoding="utf-8"))["ranked"]
    runners = set(json.loads((Path(home) / "runners-c1.json").read_text(encoding="utf-8"))["mints"])
    m = Meter(C.Helius(C.load_key(), rps=RPS), db, budget)
    fx = D.Fetch(None, Path(home) / "cache")
    try:
        load_set(db, ranked, runners, m, log=log)
        fetch_buys(db, m, creator=fx.creator, log=log)
        mark_wallets(db, log=log)
        mark_bundlers(db, m, log=log)
        set_mints = {r[0] for r in db.c.execute("select mint from coins where status='ok'")}
        scan_wallets(db, m, set_mints, log=log)
    except Budget as e:
        db.put("stopped", str(e))
        log("STOP: %s (credits %d)" % (e, m.used))
        return None
    return select(db, log=log)


def runners_from_c1(c1_input, c1_cache, out, t19=C.T19):
    """C1's runners (section 19.A) recomputed from C1's cached candles only: no network call. Coins without a cached candle file
    are counted, never fetched."""
    first, _ = D.load_desk(c1_input, t19)
    uni = D.universe(first, t19)

    class NoNet:
        def __call__(self, url):
            raise RuntimeError("no network in runners_from_c1")
    fx = D.Fetch(None, c1_cache, t19=t19, http=NoNet(), sleep=lambda s: None)
    fx._http_retry = lambda url, pacer, host: (None, "not cached")
    mints, missing = [], 0
    for k in D.mint_order(uni):
        d = uni[k]
        c = fx.candles(d["pool"], d["ts"])
        if "error" in c:
            missing += 1
            continue
        if c.get("bars") and D.runner(c["bars"], d["ts"], float(d["px"]), t19)[0]:
            mints.append(k.split(":", 1)[1])
    doc = {"source": "C1 cached candles", "universe": len(uni), "not_cached": missing, "mints": sorted(mints)}
    C.atomic_write(out, json.dumps(doc, indent=1))
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["run", "status", "runners"])
    a = ap.parse_args(argv)
    if a.what == "runners":
        d = runners_from_c1(C.HOME / "input" / "discoveries.jsonl", C.HOME / "cache", S.HOME / "runners-c1.json")
        print(json.dumps({k: d[k] for k in ("universe", "not_cached")} | {"runners": len(d["mints"])}))
    elif a.what == "run":
        print(json.dumps(run(), default=str))
    else:
        db = DB(S.HOME / "scan.db")
        print(json.dumps({"credits": db.get("credits", 0), "stopped": db.get("stopped"),
                          "coins": dict(db.c.execute("select status, count(*) from coins group by 1").fetchall()),
                          "wallets": dict(db.c.execute("select coalesce(reason, status), count(*) from wallets group by 1").fetchall()),
                          "results": db.c.execute("select count(*) from results").fetchone()[0]}, indent=1))


if __name__ == "__main__":
    main()
