"""Model 4's sealed backtest (docs/MEME.md section 5) on the cached universe and candles from research/meme/data.py.
Every rule and number comes from desk/memerules.py; this file is only the glue: decision points, positions,
cooldowns, the split and the statistics.

  python3 -m research.meme.backtest dev     # dev split only, as often as needed (the holdout stays sealed)
  python3 -m research.meme.backtest final   # the one holdout look: writes out/results.json and out/holdout.lock
"""
import hashlib
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

from desk import memerules as R
from research.meme import data

OUT = Path(__file__).resolve().parent / "out"
LOCK = OUT / "holdout.lock"
STEP = R.DECIDE_EVERY_MIN * 60


def _feat(bars, i, k, created_ts):
    """features_from_bars(bars, i) on a short slice that gives exactly the same answer: the first bar (for the
    history length) plus bars from k, the last bar at or before t - 24 h (the deepest any feature looks)."""
    return R.features_from_bars(bars[:i + 1] if k == 0 else [bars[0]] + bars[k:i + 1], i if k == 0 else i - k + 1,
                                created_ts=created_ts)


def simulate(tok, bars, now, start=0):
    """Trades for one token: dicts with policy, token, entry_ts, entry_px, exit_ts, why, net, h15/h60/h240.
    Trades still open at the end (pool alive) come back with why='open' and net None. start: first decision time."""
    created = tok["created_ms"] / 1000 if tok.get("created_ms") else None
    free_at = {p: 0 for p in R.POLICIES}       # a policy may buy this token again from this time on
    trades, k = [], 0
    for i in range(len(bars) - 1):             # the last bar has no next bar to fill at
        t, close_t = bars[i][0], bars[i][0] + 300
        if close_t % STEP or t < start:
            continue
        while k + 1 <= i and bars[k + 1][0] <= t - 86400:
            k += 1
        want = [p for p, yes in R.decide(_feat(bars, i, k, created)).items() if yes and close_t >= free_at[p]]
        if not want:
            continue
        nxt = bars[i + 1]
        later = bars[i + 1:]
        for p in want:
            tr = {"policy": p, "chain": tok["chain"], "token": tok["token"], "decide_ts": close_t,
                  "entry_ts": nxt[0], "entry_px": nxt[1]}
            if not nxt[1] or nxt[1] <= 0:
                tr.update(why="bad_open", net=None)
                trades.append(tr)
                continue
            ex = R.replay(nxt[0], nxt[1], later, dead=tok["dead"], now=now)
            if ex is None:
                tr.update(why="open", net=None, exit_ts=None)
                free_at[p] = math.inf          # one open position per policy per token
            else:
                tr.update(why=ex["why"], exit_ts=ex["ts"], exit_px=ex["price"], net=R.net_pct(nxt[1], ex["price"]),
                          by_now=ex["why"] == "time" and later[-1][0] < ex["ts"])   # closed by `now`, no later bar
                free_at[p] = ex["ts"] + R.COOLDOWN_MIN * 60
            for m in R.HORIZONS:
                px = R.horizon_exit(nxt[0], later, m)
                tr["h%d" % m] = R.net_pct(nxt[1], px) if px is not None else (-100.0 if tok["dead"] else None)
            trades.append(tr)
    return trades


def split(trades):
    """Window = earliest to latest entry over every trade; dev = first 60 %, holdout = last 40 %, 4 equal folds."""
    lo, hi = min(t["entry_ts"] for t in trades), max(t["entry_ts"] for t in trades)
    cut = lo + 0.6 * (hi - lo)
    for t in trades:
        t["part"] = "dev" if t["entry_ts"] < cut else "holdout"
        t["fold"] = min(3, int(4 * (t["entry_ts"] - lo) / (hi - lo))) if hi > lo else 0
    return {"start": lo, "end": hi, "cut": cut}


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def stats(trades):
    """Statistics on closed trades (net known)."""
    tr = [t for t in trades if t["net"] is not None]
    nets = sorted(t["net"] for t in tr)
    by_tok = defaultdict(list)
    for t in tr:
        by_tok[(t["chain"], t["token"])].append(t["net"])
    top = max(by_tok.items(), key=lambda kv: len(kv[1])) if by_tok else None
    out = {"trades": len(nets), "tokens": len(by_tok), "mean": _mean(nets),
           "median": statistics.median(nets) if nets else None,
           "pct_positive": 100 * sum(x > 0 for x in nets) / len(nets) if nets else None,
           "mean_without_best3": _mean(nets[:-3]) if len(nets) > 3 else None,
           "worst5_mean": _mean(nets[:max(1, math.ceil(0.05 * len(nets)))]) if nets else None,
           "mean_per_token": _mean([_mean(v) for v in by_tok.values()]),
           "exits": dict(Counter(t["why"] for t in tr)),
           "open_at_end": sum(t["why"] == "open" for t in trades),
           "best": nets[-1] if nets else None, "worst": nets[0] if nets else None,
           "top_token_share": len(top[1]) / len(nets) if top else None,
           "top_token": "%s:%s" % top[0] if top else None}
    for m in R.HORIZONS:
        hs = [t["h%d" % m] for t in trades if t.get("h%d" % m) is not None]
        out["h%d_mean" % m], out["h%d_trades" % m] = _mean(hs), len(hs)
    return out


def fold_means(trades):
    return [_mean([t["net"] for t in trades if t["fold"] == f and t["net"] is not None]) for f in range(4)]


def run(dead_missing=True):
    """Simulate every token. dead_missing: a pool with no liquidity figure counts as dead (as data.py stores it)."""
    u = data.universe(fetch=False)
    start = u["fetched"] - data.DAYS * 86400
    trades, info = [], Counter()
    for tok in u["tokens"]:
        if not tok.get("pool"):
            info["no_pool"] += 1
            continue
        try:
            c = data.candles(tok, fetch=False)
        except FileNotFoundError:          # the download was stopped before this pool (order unrelated to outcome)
            info["not_fetched"] += 1
            continue
        bars = c["bars"]
        if not bars:
            info["no_candles"] += 1
            continue
        if not dead_missing and tok.get("liq_usd") is None:
            tok = dict(tok, dead=False)
        info["pools_with_bars"] += 1
        info["bars"] += sum(b[0] >= start for b in bars)
        info["dead_pools"] += bool(tok["dead"])
        trades += simulate(tok, bars, c["fetched"], start)
    return u, trades, info


def report(trades, parts):
    res = {}
    for p in R.POLICIES:
        mine = [t for t in trades if t["policy"] == p]
        res[p] = {part: stats([t for t in mine if t["part"] == part]) for part in parts}
        res[p]["fold_means"] = fold_means(mine)
    return res


def choose(res):
    """Best of P1 to P4 on dev mean net per trade (primary exits)."""
    ok = [p for p in ("hype", "breakout", "calm_aged", "dip_aged") if res[p]["dev"]["mean"] is not None]
    return max(ok, key=lambda p: res[p]["dev"]["mean"]) if ok else None


def verdict(res, p):
    h, a = res[p]["holdout"], res["all"]["holdout"]
    pos = lambda x: x is not None and x > 0  # noqa: E731
    folds = sum(pos(x) for x in res[p]["fold_means"])
    c = {"mean_and_median_above_0": pos(h["mean"]) and pos(h["median"]),
         "mean_above_0_without_best3": pos(h["mean_without_best3"]),
         "at_least_100_trades_on_50_tokens": h["trades"] >= 100 and h["tokens"] >= 50,
         "mean_positive_in_3_of_4_folds": folds >= 3,
         "beats_all_on_holdout": h["mean"] is not None and a["mean"] is not None and h["mean"] > a["mean"]}
    return c, all(c.values())


def _fmt(x, nd=2):
    return "-" if x is None else ("%.*f" % (nd, x))


def main(mode):
    u, trades, info = run()
    info["bad_open_skipped"] = sum(t["why"] == "bad_open" for t in trades)
    trades = [t for t in trades if t["why"] != "bad_open"]
    w = split(trades)
    print("tokens %d, pools with bars %d, no candles %d, not fetched %d, dead %d, bars in window %d, bad opens skipped %d"
          % (len(u["tokens"]), info["pools_with_bars"], info["no_candles"], info["not_fetched"], info["dead_pools"],
             info["bars"], info["bad_open_skipped"]))
    print("entry window %s .. %s, cut %s" % tuple(_ts(x) for x in (w["start"], w["end"], w["cut"])))
    if mode == "dev":
        res = report(trades, ["dev"])
        for p in R.POLICIES:
            s = res[p]["dev"]
            print("%-10s dev n=%-5d tok=%-4d mean=%7s med=%7s pos=%5s ex3=%7s tail5=%8s perTok=%7s open=%d top=%s %s"
                  % (p, s["trades"], s["tokens"], _fmt(s["mean"]), _fmt(s["median"]), _fmt(s["pct_positive"], 1),
                     _fmt(s["mean_without_best3"]), _fmt(s["worst5_mean"]), _fmt(s["mean_per_token"]),
                     s["open_at_end"], _fmt(s["top_token_share"]), s["exits"]))
        print("dev choice:", choose(res))
        return
    if LOCK.exists():
        sys.exit("the holdout was already looked at (%s); delete the lock only to restart the count" % LOCK)
    res = report(trades, ["dev", "holdout"])
    chosen = choose(res)
    cond, passed = verdict(res, chosen)
    # sensitivity, not used to choose: pools with no liquidity figure treated as alive
    _, t2, _ = run(dead_missing=False)
    t2 = [t for t in t2 if t["why"] != "bad_open"]
    split(t2)
    alt = report(t2, ["dev", "holdout"])
    sha = hashlib.sha256((Path(R.__file__)).read_bytes()).hexdigest()
    doc = {"chosen": chosen, "passed": passed, "conditions": cond, "policies": res,
           "window": {k: _ts(v) for k, v in w.items()}, "universe_fetched": _ts(u["fetched"]),
           "counts": dict(info, tokens=len(u["tokens"]), trades=len(trades),
                          closed_by_now=dict(Counter(t["policy"] for t in trades if t.get("by_now")))),
           "sensitivity_missing_liq_alive": {p: alt[p]["holdout"] for p in R.POLICIES},
           "memerules_sha256": sha}
    OUT.mkdir(exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(doc, indent=1, default=str))
    LOCK.write_text("holdout looked at %s\n" % _ts(__import__("time").time()))
    print(json.dumps({"chosen": chosen, "passed": passed, "conditions": cond}, indent=1))


def _ts(x):
    import datetime
    return datetime.datetime.utcfromtimestamp(x).strftime("%Y-%m-%d %H:%M UTC")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "dev")
