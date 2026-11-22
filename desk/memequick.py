"""Rule P9, quick (docs/MEME.md section 15). Pure functions, no network: desk/meme.py fetches the data and calls these.

Changing a number here after a result is seen restarts the count (docs/MEME.md section 5).
"""
POLICY = "quick"
POLICIES = (POLICY,)
STATE_KEY = "quick_v1"
STOP_PCT = -5.0           # from the buy price before costs
TARGET_PCT = 10.0
HOLD_MIN = 30             # time limit
LIQ_PULL = 0.5            # practice account: sell if liquidity falls under half of its level at the buy
MIN_PER_SLICE = 10


def decide(all_fires, verdict):
    """P0's decision on a coin whose plain safety check passed. Unknown (or anything but 'pass') is no."""
    return bool(all_fires) and verdict == "pass"


def exit_sample(entry_px, entry_ts, px, liq, now, entry_liq=None, dead_liq=1000.0):
    """One forward price sample: (price, why) or None. The stop fills at the sampled price (a gap is real), the target
    at the target. A missing price or a dead pool is a total loss. Order: dead, stop, target, time, liquidity pull,
    so the stop wins when more than one applies."""
    if not px or (liq is not None and liq < dead_liq):
        return 0.0, "rug"
    if px <= entry_px * (1 + STOP_PCT / 100):
        return px, "stop"
    if px >= entry_px * (1 + TARGET_PCT / 100):
        return entry_px * (1 + TARGET_PCT / 100), "target"
    if now >= entry_ts + HOLD_MIN * 60:
        return px, "time"
    if entry_liq and liq is not None and liq < entry_liq * LIQ_PULL:
        return px, "liquidity pulled"
    return None


def breakeven_win_pct(cost_pct=3.5):
    """Share of trades that must win for the bracket to break even when each win nets the target less cost and each
    loss the stop plus cost (ignores gaps and time exits). About 57 at 3.5 % cost, about 75 at 6 %."""
    h = cost_pct / 200

    def net(move):
        return ((1 + move / 100) * (1 - h) / (1 + h) - 1) * 100
    win, loss = net(TARGET_PCT), -net(STOP_PCT)
    return round(100 * loss / (win + loss), 1)


def since(rows, start_ts):
    """Rows (closed shadows) entered on or after start_ts: the control window of section 15."""
    return [x for x in rows if start_ts is not None and x.get("ts") is not None and x["ts"] >= start_ts]
