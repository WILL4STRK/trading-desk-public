"""How a signal would have done, taken or not.

After the close the desk replays every signal of the day against that day's 5-minute candles, with the
slip's own numbers: buy at the limit or lower while the signal was good, then the stop, the target or
the flat-by time, whichever comes first. A short slip is the mirror: sell short at the limit or higher,
then the buy stop above, the target below or the flat-by time. The answer is stored beside the signal so a later review can
ask "what did I skip, and was skipping right?". It is an estimate from candles, not a fill: inside one
candle the order of the high and the low is unknown, so when a candle reaches both the stop and the
target the stop is counted (the careful answer). Nothing here changes a signal or a trade.
"""

import math
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from . import shorts
from .charts import OPEN_MIN

NO_PLAN, NO_FILL, TARGET, STOP, FLAT, OPEN = "no plan", "no fill", "target", "stop", "flat", "open"
NO_TRIGGER, NO_DATA, UNSUPPORTED = "no trigger", "no data", "unsupported"


def _minute(hhmm):
    h, m = str(hhmm).split(":")
    return int(h) * 60 + int(m)


def replay(p, bars):
    """p: a signal's payload. bars: that day's regular-session 5-minute bars (desk/charts.py), oldest first.
    Returns {result, entry, exit, exit_time, r, note}."""
    try:
        limit, stop, target = float(p["limit"]), float(p["stop"]), float(p["target"])
        start = _minute(p.get("trigger_close_time") or p["valid_until"])
        until = _minute(p["valid_until"])
        flat = _minute(p.get("flat_by") or "15:55")
    except (KeyError, TypeError, ValueError):
        return {"result": NO_PLAN, "entry": None, "exit": None, "exit_time": None, "r": None,
                "note": "The signal stopped before a plan was made."}
    if p.get("side") == "short":
        return _replay_short(limit, stop, target, start, until, flat, bars)
    if not stop < limit < target:
        return {"result": NO_PLAN, "entry": None, "exit": None, "exit_time": None, "r": None,
                "note": "The plan's numbers are not usable."}
    after = [b for b in bars if b["minute"] >= start]
    entry, i = None, None
    for k, b in enumerate(after):
        if b["minute"] >= until:
            break
        if b["l"] <= limit + 1e-9:
            entry, i = round(min(limit, b["o"]), 2), k     # a gap down fills at the open, under the limit
            break
    if entry is None:
        return {"result": NO_FILL, "entry": None, "exit": None, "exit_time": None, "r": None,
                "note": "The price never came back to the limit while the signal was good."}
    risk = entry - stop

    def done(result, price, b, note):
        price = round(price, 2)
        return {"result": result, "entry": entry, "exit": price, "exit_time": b["start"],
                "r": round((price - entry) / risk, 2) if risk > 0 else None, "note": note}

    for k, b in enumerate(after[i:]):
        if b["minute"] >= flat:
            return done(FLAT, b["o"], b, "Still open at the flat-by time.")
        if b["l"] <= stop + 1e-9:
            gap = k > 0 and b["o"] < stop                   # opened under the stop: a stop fills at the open
            return done(STOP, b["o"] if gap else stop, b, "Hit the stop." + (" Gapped through it." if gap else ""))
        if k > 0 and b["h"] >= target - 1e-9:               # not in the fill candle: its order is unknown
            return done(TARGET, max(target, b["o"]) if b["o"] > target else target, b, "Reached the target.")
    last = after[-1]
    return done(OPEN, last["c"], last, "The candles end before the trade did.")


def _replay_short(limit, stop, target, start, until, flat, bars):
    """The short mirror of replay(): fills at the limit or higher, stop above, target below."""
    if not target < limit < stop:
        return {"result": NO_PLAN, "entry": None, "exit": None, "exit_time": None, "r": None,
                "note": "The plan's numbers are not usable."}
    after = [b for b in bars if b["minute"] >= start]
    entry, i = None, None
    for k, b in enumerate(after):
        if b["minute"] >= until:
            break
        if b["h"] >= limit - 1e-9:
            entry, i = round(max(limit, b["o"]), 2), k     # a gap up fills at the open, over the limit
            break
    if entry is None:
        return {"result": NO_FILL, "entry": None, "exit": None, "exit_time": None, "r": None,
                "note": "The price never came back up to the limit while the signal was good."}
    risk = stop - entry

    def done(result, price, b, note):
        price = round(price, 2)
        return {"result": result, "entry": entry, "exit": price, "exit_time": b["start"],
                "r": round((entry - price) / risk, 2) if risk > 0 else None, "note": note}

    for k, b in enumerate(after[i:]):
        if b["minute"] >= flat:
            return done(FLAT, b["o"], b, "Still open at the flat-by time.")
        if b["h"] >= stop - 1e-9:
            gap = k > 0 and b["o"] > stop                   # opened over the stop: a buy stop fills at the open
            return done(STOP, b["o"] if gap else stop, b, "Hit the buy stop." + (" Gapped through it." if gap else ""))
        if k > 0 and b["l"] <= target + 1e-9:
            return done(TARGET, min(target, b["o"]), b, "Reached the target.")
    last = after[-1]
    return done(OPEN, last["c"], last, "The candles end before the trade did.")


# ---- the watchlist comparison: formula's picks against Claude's, same rules -----------------
def _hhmm(minutes):
    return "%02d:%02d" % (minutes // 60, minutes % 60)


def _breakout(bars, or_minutes, no_entries_after, min_volume_x, bar_minutes=5, no_entries_before="09:45"):
    """The first completed bar after the range that closes above its high on enough volume. Unlike
    shorts.breakdown, a close on thin volume is passed over: the entry timer waits for a later bar."""
    rng = shorts.opening_range(bars, or_minutes, bar_minutes)
    if not rng:
        return None, None, "the opening range is not complete"
    thin = False
    for b in bars:
        if b["minute"] < OPEN_MIN + or_minutes or not b["c"] > rng["high"] + 1e-9:
            continue
        closes = b["minute"] + bar_minutes
        if closes < _minute(no_entries_before):
            continue
        if closes > _minute(no_entries_after):
            return rng, None, "the first close above the range on enough volume came after the last entry time"
        if min_volume_x > 0 and (not rng["avg_volume"] or b["v"] / rng["avg_volume"] < min_volume_x):
            thin = True
            continue
        return rng, {"close_time": _hhmm(closes), "price": round(b["c"], 2)}, ""
    return rng, None, ("every close above the range high was under the volume floor" if thin
                       else "no close above the range high")


def _floor_cent(x):
    return math.floor(x * 100 + 1e-6) / 100          # as desk/pipeline.py rounds the long plan


def _cents(x, how=ROUND_FLOOR):
    """x to the cent in decimal arithmetic, without binary-float errors at a cent boundary."""
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=how))


def _long_plan(entry, rng, stop_rule, target_r, chase_r):
    head, _, tail = str(stop_rule).partition(":")
    hi, lo = round(rng["high"], 2), round(rng["low"], 2)       # the range as quoted, to the cent
    if stop_rule == "or-mid":
        stop = _cents((Decimal(str(hi)) + Decimal(str(lo))) / 2)
    elif stop_rule == "or-low":
        stop = lo
    elif head == "pct":
        stop = _cents(Decimal(str(entry)) * (1 - Decimal(tail) / 100))
    else:
        raise ValueError("the stop rule %s needs the exit planner; the comparison skips it" % stop_rule)
    risk = round(entry - stop, 2)
    if risk <= 0:
        raise ValueError("no room for a stop under the entry")
    return {"entry": entry, "stop": stop, "risk_per_share": risk,
            "target": _cents(Decimal(str(entry)) + Decimal(str(target_r)) * Decimal(str(risk)), ROUND_HALF_UP),
            "limit": _floor_cent(entry + chase_r * risk)}


def watchlist_replay(bars, rules, side):
    """How one watchlist pick would have done today with the morning's rules, signalled or not: the first
    opening-range breakout (long) or breakdown (short), its plan, then replay(). A shadow benchmark that
    puts the formula's and Claude's lists on the same footing. It never makes a signal, a fill or an order.
    shortcut: no atr stops (they need the exit planner); fine for comparing two lists, not a backtest of
    every setting."""
    blank = {"entry": None, "exit": None, "exit_time": None, "r": None, "trigger": None,
             "limit": None, "stop": None, "target": None}
    want = set(range(OPEN_MIN, OPEN_MIN + int(rules["or_minutes"]), 5))
    if not bars or not want <= {b["minute"] for b in bars}:           # every slot of the range, not just enough bars
        return dict(blank, result=NO_DATA, note="The day's opening range candles are missing.")
    short = side == "short"
    args = (bars, rules["or_minutes"], rules["no_entries_after"], rules["min_volume_x"])
    rng, trig, why = shorts.breakdown(*args) if short else _breakout(
        *args, no_entries_before=rules.get("no_entries_before") or "09:45")
    if not trig:
        return dict(blank, result=NO_TRIGGER, note=why)
    try:
        pl = (shorts.plan if short else _long_plan)(trig["price"], rng, rules["stop_rule"], rules["target_r"], rules["chase_r"])
    except (shorts.ShortError, ValueError) as e:
        return dict(blank, result=UNSUPPORTED, note=str(e)[:200])
    floor = rules.get("min_stop_pct")
    if floor and 100.0 * pl["risk_per_share"] / pl["entry"] + 1e-9 < floor:          # as guards.stop_too_close
        return dict(blank, result=NO_PLAN, trigger=trig["close_time"],
                    note="The stop is closer than your %g%% floor, so no signal would have been made." % floor)
    valid = _minute(trig["close_time"]) + int(rules["signal_valid_minutes"])
    start = max(_minute(trig["close_time"]), _minute(rules.get("no_entries_before") or "00:00"))
    if start >= valid:                                  # it went stale before entries were allowed
        return dict(blank, result=NO_TRIGGER, trigger=trig["close_time"],
                    note="The breakout came and went before the first entry time.")
    p = dict(pl, side=side, trigger_close_time=_hhmm(start), flat_by=rules["flat_by"], valid_until=_hhmm(valid))
    return dict(replay(p, bars), trigger=trig["close_time"], limit=pl["limit"], stop=pl["stop"], target=pl["target"])
