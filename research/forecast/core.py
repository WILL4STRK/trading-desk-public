"""Rule F1 logic (docs/MEME.md section 16 and 16.1). Pure functions: no network, no model import.

Bars are GeckoTerminal rows [ts, open, high, low, close, usd_volume]; ts is the bar's OPEN time and a bar covers ts..ts+300.
A decision point is a bar that closes (ts + 300) on the decision grid. Only bars with ts + 300 <= t reach any feature or the
forecaster. The fill is the open of the first bar after the input window (the next bar in the list, as research/meme/backtest.py),
with the section 2 cost (R.net_pct). Exits are R.replay, costs R.net_pct: the same functions the section 5 backtest uses.
"""
import bisect

from desk import memerules as R

BAR = 300
CTX = 288                  # context length, 5-minute steps (24 h)
HORIZON = 48               # forecast steps (4 h)
MAX_FILL = 0.20            # a point is ineligible when more than 20 % of its 288 steps were forward-filled
MIN_AGE_H = 24.0
F1_MEDIAN_MIN = 8.0        # % : median forecast close at +4 h versus the last close
F1_Q20_MIN = -10.0         # % : 20th-percentile forecast close at +4 h versus the last close
PERSIST_R4H_MIN = 8.0      # % : C-persist, the token's own last-4-hour return
PERSIST_R1H_MIN = 0.0      # % : and its last-hour return
ARMS = ("f1", "p0", "persist")


def last_usable(bars, t):
    """Index of the last bar that has CLOSED at or before t (ts + 300 <= t), or -1."""
    return bisect.bisect_right([b[0] for b in bars], t - BAR) - 1


def grid(bars, i):
    """(closes, filled): the 288 closes on a regular 5-minute grid ending at bars[i]. A step with no bar takes the previous close
    (forward fill). Returns (None, filled) when the first step has no earlier bar to fill from."""
    end = bars[i][0]
    start = end - BAR * (CTX - 1)
    j = bisect.bisect_left([b[0] for b in bars], start)
    have = {}
    for b in bars[j:i + 1]:
        have[b[0]] = b[4]
    prev = bars[j - 1][4] if j > 0 else None
    out, filled = [], 0
    for k in range(CTX):
        ts = start + BAR * k
        if ts in have:
            prev = have[ts]
        else:
            filled += 1
        if prev is None:
            return None, filled
        out.append(prev)
    return out, filled


def points(tok, bars, fetched, step_min=15):
    """Every eligible decision point of one token, in time order. tok: key, chain, token, created_ts, dead.
    A point: {key, chain, token, decide_ts, entry_ts, entry_px, closes, p0, persist, net, exit_ts, why}.
    Eligibility is the same for all three arms (16.1 item 3): age >= 24 h, v1h >= $20k, filled share <= 20 %."""
    step = step_min * 60
    out, skipped = [], {"bad_open": 0, "gaps": 0}
    for i in range(len(bars) - 1):
        t = bars[i][0] + BAR
        if t % step:
            continue
        feats = R.features_from_bars(bars[:i + 1], i, created_ts=tok["created_ts"])
        if feats["age_h"] is None or feats["age_h"] < MIN_AGE_H or feats["v1h"] < R.MIN_V1H:
            continue
        closes, filled = grid(bars, i)
        if closes is None or filled > MAX_FILL * CTX:
            skipped["gaps"] += 1
            continue
        nxt = bars[i + 1]
        if not nxt[1] or nxt[1] <= 0:
            skipped["bad_open"] += 1
            continue
        r4h = (closes[-1] / closes[-1 - 48] - 1) * 100
        r1h = (closes[-1] / closes[-1 - 12] - 1) * 100
        ex = R.replay(nxt[0], nxt[1], bars[i + 1:], dead=tok["dead"], now=fetched)
        pt = {"key": tok["key"], "chain": tok["chain"], "token": tok["token"], "decide_ts": t, "entry_ts": nxt[0],
              "entry_px": nxt[1], "closes": closes, "p0": True,
              "persist": r4h >= PERSIST_R4H_MIN and r1h >= PERSIST_R1H_MIN}
        if ex is None:
            pt.update(why="open", net=None, exit_ts=None)
        else:
            pt.update(why=ex["why"], net=R.net_pct(nxt[1], ex["price"]), exit_ts=ex["ts"])
        out.append(pt)
    return out, skipped


def f1_signal(closes, q20, q50):
    """F1 entry: median and 20th-percentile forecast closes at +4 h versus the last close (section 16)."""
    last = closes[-1]
    return (q50 / last - 1) * 100 >= F1_MEDIAN_MIN and (q20 / last - 1) * 100 >= F1_Q20_MIN


def arm_trades(pts, arm):
    """One arm's trades from time-ordered eligible points: a point whose flag is set buys unless that arm still holds the token
    (an open position blocks it to the end of the data) or is inside the 60-minute cooldown after its exit. Arms never block each other."""
    free_at, trades = {}, []
    for p in sorted(pts, key=lambda p: p["decide_ts"]):
        if not p[arm] or p["decide_ts"] < free_at.get(p["key"], 0):
            continue
        trades.append(dict(p, policy=arm))
        free_at[p["key"]] = float("inf") if p["exit_ts"] is None else p["exit_ts"] + R.COOLDOWN_MIN * 60
    return trades
