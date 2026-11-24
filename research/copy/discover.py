"""C1 / C1-S discovery (docs/MEME.md section 19.A, 19.S and amendment 19.1). Runs once per list. Uses only data stamped before
T19: desk lines with ts < T19, candles that close at or before min(ts0 + 24 h, T19), transactions with blockTime < T19.

    python research/copy/discover.py c1  [--input C:\\Trading\\research\\copy\\input\\discoveries.jsonl]
    python research/copy/discover.py kol            (C1-S; reads C:\\Trading\\secrets\\kol-map.json)

Writes research/copy/WALLETS-C1.json + DISCOVERY-C1.md, or WALLETS-C1S.json + DISCOVERY-C1S.md. Refuses to overwrite a list
that is already committed. Caches raw fetches under C:\\Trading\\research\\copy\\cache\\ so an interrupted run resumes.
"""
import argparse
import hashlib
import json
import math
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.copy import common as C

DAY = 86400
EARLY_S = 1800
PRE_RUN_S = 6 * 3600
RUNNER_X = 2.0
RUN_START_X = 1.25
MIN_RUNNERS = 5
TOP_N = 30
MIN_LIST = 10
TOP_VOLUME = 100
BOT_PER_MIN = 20
SANDWICH_SLOTS = 2
HEAVY_PER_DAY = 50
EXCHANGE_SIGS = 10000
HIST_CAP = 3000            # 19.1: more transactions than this in the history window is "too active to score"
WINDOW_TX_CAP = 10000      # 19.1: per runner, per address and window, the earliest 10,000 transactions
CREDIT_CAP_C1 = 600000


def gt_per_min():
    """GeckoTerminal calls a minute: COPY_GT_PER_MIN (1 to 5), default 5. Section 19.A allows at most 5."""
    try:
        return max(1, min(5, int(os.environ.get("COPY_GT_PER_MIN", "5"))))
    except ValueError:
        return 5
CREDIT_CAP_KOL = 300000    # 19.1
RANDOM_POOL = 300          # 19.1
FALLBACK_PAGES = 3000      # 19.2: getSignaturesForAddress pages (1,000 signatures each) per address and window
SEED = 19
GT = "https://api.geckoterminal.com/api/v2/networks/solana/pools/%s/ohlcv/minute?aggregate=5&before_timestamp=%d&limit=300&currency=usd"
RUGCHECK = "https://api.rugcheck.xyz/v1/tokens/%s/report"
LIST_C1 = C.HERE / "WALLETS-C1.json"
LIST_C1S = C.HERE / "WALLETS-C1S.json"


# ---- pure rules (section 19.A) ---------------------------------------------------------------------------------------------

def load_desk(path, t19=C.T19):
    """(first-sighting line per Solana key with ts < T19, D0 = ts of the earliest line). Later lines are ignored."""
    first, d0 = {}, None
    with open(path, encoding="utf-8") as f:
        for ln in f:
            try:
                d = json.loads(ln)
                ts = float(d["ts"])
            except (ValueError, KeyError, TypeError):
                continue
            if ts >= t19:
                continue
            d0 = ts if d0 is None else min(d0, ts)
            if d.get("chain") == "solana" and (d["key"] not in first or ts < first[d["key"]]["ts"]):
                first[d["key"]] = d
    return first, d0


def universe(first, t19=C.T19):
    """Coins whose 24 h runner window closes by T19."""
    return {k: d for k, d in first.items() if d["ts"] + DAY <= t19 and d.get("pool") and d.get("px")}


def usable_bars(bars, ts0, t19=C.T19):
    """[(open, close)] for 5-minute bars with open >= ts0 and close time (open + 300) <= min(ts0 + 24 h, T19), by time."""
    end = min(ts0 + DAY, t19)
    return sorted((int(b[0]), float(b[4])) for b in bars if int(b[0]) >= ts0 and int(b[0]) + 300 <= end)


def runner(bars, ts0, p0, t19=C.T19):
    """(is_runner, t_run). A runner has a close >= 2 x p0 in its window; t_run is the open of the first close >= 1.25 x p0."""
    ub = usable_bars(bars, ts0, t19)
    hit = any(c >= RUNNER_X * p0 for _, c in ub)
    t_run = next((o for o, c in ub if c >= RUN_START_X * p0), None)
    return hit, (t_run if hit else None)


def early_windows(ts0, age_h, t_run, t19=C.T19):
    """The two early windows [a, b) clipped to T19; empty ones dropped."""
    t_c = ts0 - 3600.0 * float(age_h or 0)
    ws = [(t_c, t_c + EARLY_S), (max(t_c, t_run - PRE_RUN_S), t_run)]
    out = []
    for a, b in ws:
        b = min(b, t19)
        if b > a and (a, b) not in out:
            out.append((a, b))
    return out


def in_windows(ts, ws):
    return ts is not None and any(a <= ts < b for a, b in ws)


def wallet_stats(swaps, d0, t19=C.T19):
    """Dev stats from a wallet's swaps (section 19 swaps, ts < T19): counts, bot/MEV and heavy-trader flags, realized P&L
    with average-cost basis in SOL (fees are inside the SOL amounts), share of closed mints in profit."""
    sw = sorted((s for s in swaps if s.get("ts") is not None and d0 <= s["ts"] < t19), key=lambda s: (s["ts"], s["sig"] or ""))
    days = max((t19 - d0) / DAY, 1e-9)
    ts = [s["ts"] for s in sw]
    max_min, j = 0, 0
    for i in range(len(ts)):
        while ts[i] - ts[j] >= 60:
            j += 1
        max_min = max(max_min, i - j + 1)
    by_slot = defaultdict(set)
    for s in sw:
        by_slot[(s["slot"], s["mint"])].add(s["side"])
    sandwich = sum(1 for v in by_slot.values() if v == {"buy", "sell"})
    per_day = defaultdict(int)
    for s in sw:
        per_day[(s["mint"], int(s["ts"] // DAY))] += 1
    pos = {}
    realized, closed = 0.0, {}
    for s in sw:
        p = pos.setdefault(s["mint"], [0.0, 0.0])      # tokens, cost basis SOL
        if s["side"] == "buy":
            p[0] += s["tokens"]
            p[1] += s["sol"]
        elif p[0] > 0:
            q = min(s["tokens"], p[0])
            basis = p[1] * q / p[0]
            r = s["sol"] * (q / s["tokens"]) - basis
            realized += r
            closed[s["mint"]] = closed.get(s["mint"], 0.0) + r
            p[0] -= q
            p[1] -= basis
    return {"swaps": len(sw), "swaps_per_day": round(len(sw) / days, 3), "max_swaps_60s": max_min,
            "sandwich_slots": sandwich, "max_mint_day": max(per_day.values()) if per_day else 0,
            "realized_sol": round(realized, 6), "mints_closed": len(closed),
            "win_share": round(sum(1 for v in closed.values() if v > 0) / len(closed), 3) if closed else None}


def bot_reason(st, n_sigs):
    """Exclusions 2 to 4 (and 19.1's too-active rule) for one wallet, or None."""
    if n_sigs is not None and n_sigs >= EXCHANGE_SIGS:
        return "exchange"
    if st is None:
        return "too_active"
    if st["max_swaps_60s"] > BOT_PER_MIN or st["sandwich_slots"] >= SANDWICH_SLOTS:
        return "bot_mev"
    if st["max_mint_day"] > HEAVY_PER_DAY:
        return "heavy"
    return None


def top_volume(early_sol, n=TOP_VOLUME):
    """Exclusion 5: the n early buyers with the most SOL spent on early buys (ties by address)."""
    return {w for w, _ in sorted(early_sol.items(), key=lambda kv: (-kv[1], kv[0]))[:n]}


def rank(qualified):
    """C1 order: distinct runners desc, realized SOL desc, address."""
    return sorted(qualified, key=lambda q: (-q["runners"], -q["realized_sol"], q["address"]))


def quintile_bins(values):
    """Quintile edges (20/40/60/80th percentiles) of a list."""
    xs = sorted(values)
    if not xs:
        return []
    return [xs[min(len(xs) - 1, int(len(xs) * p / 5))] for p in (1, 2, 3, 4)]


def bin_of(x, edges):
    return sum(1 for e in edges if x >= e)


def draw_random(c1, pool, seed=SEED):
    """One control wallet per C1 wallet (rank order) from the same swaps-a-day quintile of the pool, without replacement;
    candidates sorted by address; random.Random(19). A quintile that runs dry takes from the nearest quintile (lower first)."""
    r = C.rng(seed)
    edges = quintile_bins([p["swaps_per_day"] for p in pool])
    bins = defaultdict(list)
    for p in sorted(pool, key=lambda p: p["address"]):
        bins[bin_of(p["swaps_per_day"], edges)].append(p)
    out = []
    for w in c1:
        b = bin_of(w["swaps_per_day"], edges)
        for d in sorted(range(5), key=lambda k: (abs(k - b), k)):
            if bins[d]:
                out.append(bins[d].pop(r.randrange(len(bins[d]))))
                break
    return out


# ---- pure rules (section 19.S) ---------------------------------------------------------------------------------------------

def first_sol_in(transfers, wallet):
    """The earliest incoming SOL transfer to wallet (dict), or None."""
    ins = [t for t in transfers if t["dst"] == wallet and t["asset"] == "SOL" and t["src"] != wallet and t.get("ts") is not None]
    return min(ins, key=lambda t: (t["ts"], t["sig"] or "")) if ins else None


def rule_funded(w_first_in, w_first_swap_ts, main, fresh_intermediaries):
    """(a): W's first SOL came from M, or from a fresh intermediary of M, within 7 days before W's first swap."""
    if not w_first_in or w_first_swap_ts is None:
        return False
    src_ok = w_first_in["src"] == main or w_first_in["src"] in fresh_intermediaries
    return src_ok and 0 <= w_first_swap_ts - w_first_in["ts"] <= 7 * DAY


def fresh_intermediaries(main, main_out, first_tx_ts):
    """Wallets I that got SOL from M and whose first transaction was at most 7 days before that funding."""
    out = set()
    for t in main_out:
        if t["src"] == main and t["asset"] == "SOL":
            f = first_tx_ts.get(t["dst"])
            if f is not None and t["ts"] - f <= 7 * DAY:
                out.add(t["dst"])
    return out


def rule_lead(w_buys, main_first_buy):
    """(b): (number of distinct mints W bought 10 s to 30 min before M's first buy, [leads in s])."""
    leads = {}
    for b in w_buys:
        tm = main_first_buy.get(b["mint"])
        if tm is None:
            continue
        d = tm - b["ts"]
        if 10 <= d <= EARLY_S and (b["mint"] not in leads or d > leads[b["mint"]]):
            leads[b["mint"]] = d
    return len(leads), sorted(leads.values())


def rule_sweep(w_transfers_out, wallet, main, main_funder, funder_is_exchange):
    """(c): W sent SOL or tokens to M, or to M's funder (unless that funder is an exchange)."""
    targets = {main} | ({main_funder} if main_funder and not funder_is_exchange else set())
    return any(t["src"] == wallet and t["dst"] in targets for t in w_transfers_out)


def link_all(links):
    """links: {wallet: {cluster: [rules]}} -> (kept {wallet: (cluster, rules)}, multi-cluster wallets)."""
    kept, multi = {}, []
    for w, cl in sorted(links.items()):
        cl = {k: v for k, v in cl.items() if v}
        if len(cl) > 1:
            multi.append(w)
        elif len(cl) == 1:
            k, v = next(iter(cl.items()))
            kept[w] = (k, sorted(set(v)))
    return kept, multi


# ---- fetching --------------------------------------------------------------------------------------------------------------

def unavailable(err):
    """True only when the error says the method itself is not available (plan or unknown method): the section 19.A
    fallback is for that case, not for any other error (an unsupported transaction version, a timeout, a bad request)."""
    t = str(err).lower()
    return "-32601" in t or "method not found" in t or "not available" in t or "upgrade" in t or "plan" in t


class Fetch:
    """Cached reads. Helius transactions always carry blockTime < T19; GeckoTerminal at most 5 calls a minute."""

    def __init__(self, helius, cache, t19=C.T19, http=C.http_json, sleep=time.sleep):
        self.h, self.cache, self.t19, self.http = helius, Path(cache), t19, http
        self.sleep = sleep
        self.gt = C.Pacer(gt_per_min(), 60.0, sleep=sleep)
        self.rc = C.Pacer(1, 1.1, sleep=sleep)
        self.gtfa_ok = None

    def _cached(self, kind, name, fn):
        p = self.cache / kind / (hashlib.sha256(name.encode()).hexdigest()[:40] + ".json")
        if p.exists():
            v = json.loads(p.read_text(encoding="utf-8"))
            if "error" not in v:
                return v
        v = fn()
        if "error" not in v:                    # a failed fetch is never cached: a rerun asks again
            C.atomic_write(p, json.dumps(v))
        return v

    def _http_retry(self, url, pacer, host):
        """GET with the pacer; a 429 or other failure waits 60 s, 120 s, ... (at most 5 tries). Every failure is logged to
        fetch_errors.jsonl next to the cache folder. Returns (json, None) or (None, last error)."""
        err = None
        for i in range(5):
            pacer.wait()
            try:
                return self.http(url), None
            except RuntimeError as e:
                err = str(e)
                try:
                    with open(self.cache.parent / "fetch_errors.jsonl", "a", encoding="utf-8") as f:
                        f.write(json.dumps({"ts": time.time(), "host": host, "try": i + 1, "error": C.mask(err)}) + "\n")
                except OSError:
                    pass
                self.sleep(60 * (i + 1))
        return None, err

    def candles(self, pool, ts0):
        before = int(min(ts0 + DAY, self.t19))

        def go():
            r, err = self._http_retry(GT % (pool, before), self.gt, "api.geckoterminal.com")
            if r is None:
                return {"error": err}
            return {"bars": ((r.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []}
        return self._cached("candles", "%s|%d" % (pool, before), go)

    def creator(self, mint):
        def go():
            r, err = self._http_retry(RUGCHECK % mint, self.rc, "api.rugcheck.xyz")
            if r is None:
                return {"error": err}
            return {"creator": (r or {}).get("creator")}
        return self._cached("rugcheck", mint, go).get("creator")

    def _gtfa(self, address, cfg):
        """getTransactionsForAddress, or None once the plan has refused it (then the fallback is used for the whole run)."""
        if self.gtfa_ok is False:
            return None
        try:
            r = self.h.rpc("getTransactionsForAddress", [address, cfg]) or {}
            self.gtfa_ok = True
            return r
        except C.CreditCap:
            raise
        except RuntimeError as e:
            if self.gtfa_ok or not unavailable(e):
                raise
            self.gtfa_ok = False
            return None

    def _sigs(self, address, gte, lt, stop=None, max_pages=FALLBACK_PAGES):
        """Fallback: successful signatures with gte <= blockTime < lt, oldest first, by paging getSignaturesForAddress
        backwards from the newest (1 credit a page). Returns (sigs, truncated)."""
        out, before, pages = [], None, 0
        while pages < max_pages:
            cfg = {"limit": 1000}
            if before:
                cfg["before"] = before
            page = self.h.rpc("getSignaturesForAddress", [address, cfg]) or []
            pages += 1
            if not page:
                return sorted(out, key=lambda s: (s["blockTime"], s["signature"])), False
            for s in page:
                bt = s.get("blockTime")
                if bt is None or s.get("err") is not None:
                    continue
                if bt < lt and (gte is None or bt >= gte):
                    out.append(s)
            before = page[-1]["signature"]
            last = page[-1].get("blockTime")
            if gte is not None and last is not None and last < gte:
                return sorted(out, key=lambda s: (s["blockTime"], s["signature"])), False
        return sorted(out, key=lambda s: (s["blockTime"], s["signature"])), True

    def _fallback_txs(self, address, gte, lt, cap):
        sigs, trunc = self._sigs(address, gte, lt)
        out = []
        for s in sigs[:cap]:
            t = self.h.rpc("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": C.TX_VERSION}])
            if t:
                out.append(t)
        return out, trunc or len(sigs) > cap

    def txs(self, address, gte=None, lt=None, cap=HIST_CAP, asc=True):
        """Full jsonParsed transactions of address with gte <= blockTime < min(lt, T19), ascending, at most cap.
        Returns (txs, truncated)."""
        lt = self.t19 if lt is None else min(lt, self.t19)
        name = "%s|%s|%s|%d" % (address, gte, lt, cap)

        def go():
            if self.gtfa_ok is False:
                got, trunc = self._fallback_txs(address, gte, lt, cap)
                return {"txs": [t for t in got if t.get("blockTime") is not None and t["blockTime"] < self.t19],
                        "truncated": trunc}
            out, tok = [], None
            while len(out) < cap:
                bt = {"lt": int(math.ceil(lt))}
                if gte is not None:
                    bt["gte"] = int(math.floor(gte))
                cfg = {"transactionDetails": "full", "sortOrder": "asc" if asc else "desc", "limit": 100,
                       "encoding": "jsonParsed", "maxSupportedTransactionVersion": C.TX_VERSION,
                       "filters": {"blockTime": bt, "status": "succeeded"}}
                if tok:
                    cfg["paginationToken"] = tok
                r = self._gtfa(address, cfg)
                if r is None:
                    return go()
                out += r.get("data") or []
                tok = r.get("paginationToken")
                if not tok or not r.get("data"):
                    break
            trunc = len(out) >= cap and bool(tok)
            return {"txs": [t for t in out[:cap] if t.get("blockTime") is not None and t["blockTime"] < self.t19
                            and (gte is None or t["blockTime"] >= math.floor(gte))], "truncated": trunc}
        v = self._cached("txs", name, go)
        return v["txs"], v["truncated"]

    def sig_count(self, address, gte, stop=EXCHANGE_SIGS):
        """Signatures of address in [gte, T19), counted up to stop (signatures-only mode)."""
        def go():
            n, tok = 0, None
            while n < stop:
                cfg = {"transactionDetails": "signatures", "sortOrder": "asc", "limit": 1000,
                       "filters": {"blockTime": {"gte": int(gte), "lt": int(self.t19)}}}
                if tok:
                    cfg["paginationToken"] = tok
                r = self._gtfa(address, cfg)
                if r is None:
                    return {"n": min(len(self._sigs(address, gte, self.t19)[0]), stop)}
                n += len(r.get("data") or [])
                tok = r.get("paginationToken")
                if not tok or not r.get("data"):
                    break
            return {"n": min(n, stop)}
        return self._cached("sigcount", "%s|%d|%d" % (address, gte, stop), go)["n"]

    def first_txs(self, address, n=20):
        """The address's first n transactions ever (before T19)."""
        return self.txs(address, None, self.t19, cap=n)[0]


# ---- C1 --------------------------------------------------------------------------------------------------------------------

def mint_order(keys):
    return sorted(keys, key=lambda k: hashlib.sha256(k.split(":", 1)[1].encode()).hexdigest())


def history(fx, w, d0):
    """(stats or None if too active, signature count, swaps, transfers) for wallet w over [D0, T19)."""
    n = fx.sig_count(w, d0)
    if n >= EXCHANGE_SIGS:
        return None, n, [], []
    txs, trunc = fx.txs(w, d0, None, HIST_CAP)
    if trunc:
        return None, n, [], []
    sw = [s for t in txs for s in C.parse_swaps(t, {w})]
    tr = [x for t in txs for x in C.parse_transfers(t)]
    return wallet_stats(sw, d0, fx.t19), n, sw, tr


def run_c1(fx, desk_path, out_dir=C.HERE, log=print):
    first, d0 = load_desk(desk_path, fx.t19)
    uni = universe(first, fx.t19)
    rep = {"t19": fx.t19, "d0": d0, "input_sha256": C.sha256_file(desk_path), "solana_coins": len(first),
           "universe": len(uni), "no_candles": 0, "runners": 0, "runners_dropped_cap": 0, "truncated_windows": 0}
    runners = []
    for n, k in enumerate(mint_order(uni)):
        if n % 50 == 0:
            log("candles %d of %d" % (n, len(uni)))
        d = uni[k]
        c = fx.candles(d["pool"], d["ts"])
        if "error" in c:
            rep["candle_fetch_failed"] = rep.get("candle_fetch_failed", 0) + 1
            continue
        if not c.get("bars"):
            rep["no_candles"] += 1
            continue
        hit, t_run = runner(c["bars"], d["ts"], float(d["px"]), fx.t19)
        if hit:
            runners.append((k, d, t_run))
    rep["runners"] = len(runners)
    log("runners %d of %d" % (len(runners), len(uni)))
    early = defaultdict(lambda: {"runners": set(), "sol": 0.0, "buys": 0})
    excluded_dev = set()
    for i, (k, d, t_run) in enumerate(runners):
        mint = k.split(":", 1)[1]
        ws = early_windows(d["ts"], (d.get("features") or {}).get("age_h"), t_run, fx.t19)
        try:
            seen = {}
            for addr in (mint, d["pool"]):
                for a, b in ws:
                    txs, trunc = fx.txs(addr, a, b, WINDOW_TX_CAP)
                    rep["truncated_windows"] += bool(trunc)
                    for t in txs:
                        seen[(t["transaction"].get("signatures") or [""])[0]] = t
            dev = {fx.creator(mint)}
            ft = fx.first_txs(mint, 1)
            if ft:
                dev.add(C.fee_payer(ft[0]))
        except C.CreditCap:
            rep["runners_dropped_cap"] = len(runners) - i
            break
        excluded_dev |= {x for x in dev if x}
        for t in seen.values():
            for s in C.parse_swaps(t):
                if s["mint"] == mint and s["side"] == "buy" and in_windows(s["ts"], ws) and s["wallet"] not in dev:
                    e = early[s["wallet"]]
                    e["runners"].add(mint)
                    e["sol"] += s["sol"]
                    e["buys"] += 1
    for w in excluded_dev:
        early.pop(w, None)
    rep["early_buyers"] = len(early)
    rep["excluded_dev_wallets"] = len(excluded_dev)
    top = top_volume({w: e["sol"] for w, e in early.items()})
    rep["excluded_top_volume"] = len(top)
    pool = {w: e for w, e in early.items() if w not in top}
    cands = sorted(w for w, e in pool.items() if len(e["runners"]) >= MIN_RUNNERS)
    rep["candidates_5_runners"] = len(cands)
    rnd_pool = sorted(w for w in pool if w not in set(cands))
    sample = C.rng(SEED).sample(rnd_pool, min(RANDOM_POOL, len(rnd_pool)))
    reasons = defaultdict(int)
    qualified, others = [], []
    for group, ws_ in (("cand", cands), ("pool", sample)):
        for w in ws_:
            try:
                st, n, _, _ = history(fx, w, d0)
            except C.CreditCap:
                reasons["credit_cap_" + group] += 1
                continue
            why = bot_reason(st, n)
            if why:
                reasons["%s_%s" % (group, why)] += 1
                continue
            row = dict(st, address=w, runners=len(pool[w]["runners"]), early_buys=pool[w]["buys"],
                       early_sol=round(pool[w]["sol"], 6))
            if group == "cand" and st["realized_sol"] > 0:
                qualified.append(row)
            else:
                if group == "cand":
                    reasons["cand_not_profitable"] += 1
                others.append(row)
    rep["exclusions"] = dict(sorted(reasons.items()))
    rep["qualified"] = len(qualified)
    ranked = rank(qualified)[:TOP_N]
    for i, q in enumerate(ranked, 1):
        q["tag"] = "C1-%02d" % i
    rnd = draw_random(ranked, others)
    for i, q in enumerate(rnd, 1):
        q["tag"] = "R-%02d" % i
    rep["helius_credits"] = fx.h.credits
    rep["started"] = len(ranked) >= MIN_LIST
    doc = {"study": "C1", "section": "19", "t19": fx.t19, "t19_commit": C.T19_COMMIT, "d0": d0,
           "input_sha256": rep["input_sha256"], "helius_credits": fx.h.credits, "started": rep["started"],
           "c1": ranked, "random": rnd}
    write_list(out_dir / "WALLETS-C1.json", doc)
    (out_dir / "DISCOVERY-C1.md").write_text(report_md("C1", rep), encoding="utf-8", newline="\n")
    return doc, rep


def write_list(path, doc):
    path = Path(path)
    if path.exists():
        try:
            C.git("ls-files", "--error-unmatch", str(path))
            raise SystemExit("%s is committed (frozen); a new list is a new study" % path.name)
        except Exception as e:
            if isinstance(e, SystemExit):
                raise
    C.atomic_write(path, json.dumps(doc, indent=1, sort_keys=True) + "\n")


def report_md(name, rep):
    lines = ["# %s discovery report (docs/MEME.md section 19)" % name, "",
             "Generated %s UTC. Dev window: data before T19 = %s." % (time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
                                                                    time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(rep.get("t19") or C.T19))), ""]
    for k, v in rep.items():
        lines.append("- `%s`: %s" % (k, json.dumps(v, sort_keys=True)))
    return "\n".join(lines) + "\n"


# ---- C1-S ------------------------------------------------------------------------------------------------------------------

def load_kol_map(path=C.KOL_MAP):
    """{cluster: wallet} from the secret map; names and URLs stay inside this function's caller only for the source date."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for k, v in sorted(raw.items()):
        if not (len(k) == 3 and k[0] == "K" and k[1:].isdigit()):
            raise SystemExit("kol-map keys must be K01, K02, ...")
        w = v.get("wallet") if isinstance(v, dict) else None
        if not w or not C.B58.match(w):
            raise SystemExit("kol-map %s: bad wallet" % k)
        out[k] = {"wallet": w, "read_at": (v.get("read_at") or "")[:10]}
    return out


def run_kol(fx, d0, kol, out_dir=C.HERE, log=print):
    """C1-S linking. kol: {cluster: {wallet, read_at}} (no names). d0: the C1 history start."""
    rep = {"t19": fx.t19, "d0": d0, "clusters": len(kol), "mains_dropped_exchange": 0,
           "links_before": {"a": 0, "b": 0, "c": 0}, "links_after": {"a": 0, "b": 0, "c": 0},
           "excluded": defaultdict(int), "lead_mints_dropped_cap": 0}
    mains, mains_info = {}, {}
    for k, v in kol.items():
        m = v["wallet"]
        st, n, sw, tr = history(fx, m, d0)
        if n >= EXCHANGE_SIGS:
            rep["mains_dropped_exchange"] += 1
            continue
        firsts = fx.first_txs(m, 20)
        fin = first_sol_in([x for t in firsts for x in C.parse_transfers(t)], m)
        funder = fin["src"] if fin else None
        funder_ex = bool(funder) and fx.sig_count(funder, d0) >= EXCHANGE_SIGS
        buys = {}
        for s in sorted(sw, key=lambda s: s["ts"]):
            if s["side"] == "buy" and s["mint"] not in buys:
                buys[s["mint"]] = s["ts"]
        buys = dict(list(buys.items())[:200])
        mains[k] = {"wallet": m, "funder": funder, "funder_ex": funder_ex, "buys": buys, "out": tr}
        mains_info[k] = dict(st or {}, address=m, tag="%s-main" % k, cluster=k, source_read=v["read_at"])
    main_set = {v["wallet"] for v in mains.values()}
    links = defaultdict(lambda: defaultdict(list))
    leads = defaultdict(list)
    cands = defaultdict(set)
    for k, M in mains.items():
        recips = {t["dst"] for t in M["out"] if t["src"] == M["wallet"] and t["asset"] == "SOL"}
        first_ts = {}
        for i in sorted(recips):
            ft = fx.first_txs(i, 1)
            if ft:
                first_ts[i] = ft[0]["blockTime"]
        fresh = fresh_intermediaries(M["wallet"], M["out"], first_ts)
        M["fresh"] = fresh
        cands[k] |= recips
        for i in sorted(fresh):
            _, _, _, itr = history(fx, i, d0)
            cands[k] |= {t["dst"] for t in itr if t["src"] == i and t["asset"] == "SOL"}
    # rule (b) candidates: buyers 10 s to 30 min before M's first buy, round-robin over clusters by mint index
    order = []
    for k, M in mains.items():
        for j, (mint, tm) in enumerate(M["buys"].items()):
            order.append((j, k, mint, tm))
    for idx, (j, k, mint, tm) in enumerate(sorted(order)):
        try:
            txs, _ = fx.txs(mint, tm - EARLY_S, tm - 10 + 1e-9, 2000)
        except C.CreditCap:
            rep["lead_mints_dropped_cap"] = len(order) - idx
            break
        for t in txs:
            for s in C.parse_swaps(t):
                if s["mint"] == mint and s["side"] == "buy":
                    cands[k].add(s["wallet"])
    for k, M in mains.items():
        for w in sorted(cands[k] - main_set):
            try:
                st, n, sw, tr = history(fx, w, d0)
                firsts = fx.first_txs(w, 20)
            except C.CreditCap:
                rep["excluded"]["credit_cap"] += 1
                continue
            fin = first_sol_in([x for t in firsts for x in C.parse_transfers(t)], w)
            fsw = [s for t in firsts for s in C.parse_swaps(t, {w})]
            first_swap = min((s["ts"] for s in fsw), default=None)
            if first_swap is None:
                first_swap = min((s["ts"] for s in sw), default=None)
            rules = []
            if rule_funded(fin, first_swap, M["wallet"], M["fresh"]):
                rules.append("a")
            nl, ld = rule_lead([s for s in sw if s["side"] == "buy"], M["buys"])
            if nl >= 3:
                rules.append("b")
                leads[(w, k)] = ld
            if rule_sweep(tr, w, M["wallet"], M["funder"], M["funder_ex"]):
                rules.append("c")
            for r in rules:
                rep["links_before"][r] += 1
            why = bot_reason(st, n)
            if rules and why:
                rep["excluded"][why] += 1
                continue
            if rules:
                links[w][k] = rules
                links[w]["_stats"] = st
    stats = {w: links[w].pop("_stats") for w in list(links)}
    kept, multi = link_all(links)
    rep["excluded"]["multi_cluster"] = len(multi)
    sides, per = [], defaultdict(int)
    for w, (k, rules) in sorted(kept.items(), key=lambda kv: (kv[1][0], kv[0])):
        per[k] += 1
        for r in rules:
            rep["links_after"][r] += 1
        ld = leads.get((w, k), [])
        sides.append(dict(stats[w], address=w, cluster=k, tag="%s-side-%d" % (k, per[k]), rules=rules,
                          lead_mints=len(ld), lead_median_s=C.median(ld)))
    rep["excluded"] = dict(rep["excluded"])
    rep["sides"] = len(sides)
    rep["helius_credits"] = fx.h.credits
    doc = {"study": "C1-S", "section": "19", "t19": fx.t19, "t19_commit": C.T19_COMMIT, "d0": d0,
           "helius_credits": fx.h.credits, "sides": sides, "mains": [mains_info[k] for k in sorted(mains_info)]}
    write_list(out_dir / "WALLETS-C1S.json", doc)
    (out_dir / "DISCOVERY-C1S.md").write_text(report_md("C1-S", rep), encoding="utf-8", newline="\n")
    return doc, rep


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["c1", "kol"])
    ap.add_argument("--input", default=str(C.HOME / "input" / "discoveries.jsonl"))
    a = ap.parse_args(argv)
    key = C.load_key()
    h = C.Helius(key, rps=8, cap=CREDIT_CAP_C1 if a.what == "c1" else CREDIT_CAP_KOL)
    fx = Fetch(h, C.HOME / "cache")
    if a.what == "c1":
        _, rep = run_c1(fx, Path(a.input))
    else:
        d0 = json.loads(LIST_C1.read_text(encoding="utf-8"))["d0"] if LIST_C1.exists() else load_desk(a.input)[1]
        _, rep = run_kol(fx, d0, load_kol_map())
    print(C.mask(json.dumps(rep, indent=1, sort_keys=True, default=str)))


if __name__ == "__main__":
    main()
