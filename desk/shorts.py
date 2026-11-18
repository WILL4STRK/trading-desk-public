"""The short side's maths: opening-range breakdowns.

The vendored Chart Scanner, Entry Timer, Exit Planner and Budget Manager are
long-only and are never edited, so everything they would have done for a
short is worked out here, as plain functions with no network and no files.
It is the long rule mirrored: the first 5-minute candle to CLOSE below the
opening range's low, a stop above the entry, a target below it.

Nothing here places an order. See docs/SHORTING.md for the rules and sources.
"""
import math

from .charts import OPEN_MIN

SETUP = "orb_short"
MIN_EQUITY = 2000.0        # brokers lend shares only above this (FINRA Rule 4210)
SSR_DROP_PCT = 10.0        # SEC Rule 201: down this much on the day switches the restriction on
TREND_DAYS = 200
MIN_RISK_PCT = 0.05        # a stop closer than this is inside the spread's noise (the Exit Planner's floor)

FACTS = [
    "Shorting needs a margin account that the broker has approved for it. A cash account cannot short at all, "
    "and a custodial account for someone under the age of majority generally cannot be a margin account.",
    "Brokers want at least $2,000 of equity in the account before they lend shares.",
    "The loss on a short is not capped. The price can rise without limit, and a gap can jump straight over your stop.",
    "Shares may not be available to borrow, borrowing can cost a fee, and the broker can close your short at any time.",
    "After a stock falls 10% in a day the short-sale restriction applies for the rest of that day and the next: "
    "a short can then only fill above the best bid. The desk blocks a short when it sees today's drop, "
    "but it cannot see a restriction left over from yesterday.",
]


class ShortError(ValueError):
    pass


def ceil_cent(x):
    return math.ceil(x * 100 - 1e-6) / 100


def floor_cent(x):
    return math.floor(x * 100 + 1e-6) / 100


def hhmm(minute):
    return "%02d:%02d" % divmod(int(minute), 60)


def clock12(minute):
    """600 -> '10:00 AM', the way the skills print a time."""
    h, m = divmod(int(minute), 60)
    return "%d:%02d %s" % ((h - 1) % 12 + 1, m, "AM" if h < 12 else "PM")


def _minutes(text):
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def status(st, equity):
    """Is the short side on, and is it practice only (paper_only)? Practice only unless every reason is
    cleared. This is about the account's facts, not the desk's Paper / Live mode."""
    why = []
    if st["account_type"] != "margin":
        why.append("Account type in Settings is cash. A cash account cannot short.")
    if equity < MIN_EQUITY:
        why.append("The account is $%s. Brokers need at least $%s of equity before they lend shares."
                   % (format(equity, ",.2f"), format(MIN_EQUITY, ",.0f")))
    if not st["short_approved"]:
        why.append("\"My broker approved this account for short selling\" is off in Settings.")
    return {"on": st["directions"] in ("short", "both"), "long_on": st["directions"] in ("long", "both"),
            "paper_only": bool(why), "why": why, "facts": FACTS, "min_equity": MIN_EQUITY}


def opening_range(bars, or_minutes, bar_minutes=5):
    """The high and low of the first or_minutes of the session, or None while any of those bars is missing."""
    rng = [b for b in bars if OPEN_MIN <= b["minute"] < OPEN_MIN + or_minutes]
    if len(rng) < or_minutes // bar_minutes:
        return None
    return {"high": max(b["h"] for b in rng), "low": min(b["l"] for b in rng), "bars": len(rng),
            "minutes": or_minutes, "avg_volume": sum(b["v"] for b in rng) / len(rng)}


def breakdown(bars, or_minutes, no_entries_after="15:30", min_volume_x=0.0, bar_minutes=5):
    """(range, trigger, reason). The trigger is the FIRST completed bar after the range that closes
    below the range low. A wick below is not a trigger. Only that first close counts: if it came too
    late in the day or on too little volume there is no trigger, the desk does not wait for a second one."""
    rng = opening_range(bars, or_minutes, bar_minutes)
    if not rng:
        return None, None, "waiting: the %d-minute opening range is not complete" % or_minutes
    for i, b in enumerate(bars):
        if b["minute"] < OPEN_MIN + or_minutes or not b["c"] < rng["low"] - 1e-9:
            continue
        closes = b["minute"] + bar_minutes
        if closes > _minutes(no_entries_after):
            return rng, None, "the first close below the range low came at %s, after the last entry time" % clock12(closes)
        vol_x = round(b["v"] / rng["avg_volume"], 2) if rng["avg_volume"] else None
        if min_volume_x > 0 and (vol_x is None or vol_x < min_volume_x):
            return rng, None, ("the %s bar closed below the range low on %sx the range's average volume, "
                               "under your %gx floor" % (clock12(b["minute"]), vol_x, min_volume_x))
        return rng, {"time": clock12(closes), "close_time": hhmm(closes), "bar_start": hhmm(b["minute"]),
                     "bar_index": i, "price": round(b["c"], 2), "volume": b["v"], "volume_x": vol_x,
                     "pct_under": round((1 - b["c"] / rng["low"]) * 100, 2)}, \
            "the %s bar closed below the range low" % clock12(b["minute"])
    return rng, None, "waiting: no candle has closed below the range low %.2f" % rng["low"]


def plan(entry, rng, stop_rule, target_r, chase_r):
    """Stop above the entry, target below it, and the lowest price worth selling at.
    Stops round UP to the cent (away from the entry), the mirror of the long side rounding down."""
    entry = round(float(entry), 2)
    head, _, tail = str(stop_rule).partition(":")
    if stop_rule == "or-mid":
        stop, label = ceil_cent((rng["high"] + rng["low"]) / 2), "the middle of the opening range"
    elif stop_rule == "or-low":           # the long rule's "far side of the range": for a short that is the high
        stop, label = ceil_cent(rng["high"]), "the opening range high"
    elif head == "pct":
        stop, label = ceil_cent(entry * (1 + float(tail) / 100)), "%g%% above the entry" % float(tail)
    else:
        raise ShortError("the stop rule %s is not built for shorts. Use or-mid, or-low or pct:X." % stop_rule)
    risk = round(stop - entry, 2)
    if risk <= 0:
        raise ShortError("the stop %.2f is not above the entry %.2f, so there is no short to plan" % (stop, entry))
    if risk < entry * MIN_RISK_PCT / 100:
        raise ShortError("the stop %.2f is only %.2f above the entry: too close to mean anything" % (stop, risk))
    target = round(entry - target_r * risk, 2)
    if target <= 0:
        raise ShortError("a %gR target would be at or under zero" % target_r)
    return {"entry": entry, "stop": stop, "target": target, "risk_per_share": risk, "target_r": target_r,
            "limit": ceil_cent(entry - chase_r * risk), "stop_label": label,
            "target_label": "%gR below the entry" % target_r}


def size(pl, lim, settled_cash):
    """Whole shares from the same percentage limits as a long. Risk is measured at the limit (the worst
    price you would sell at). Cash and the position cap are measured at the stop, the most the short can
    be worth before you are out: the desk wants that much cash on hand, so a short is never leveraged."""
    risk = round(pl["stop"] - pl["limit"], 2)
    cash = max(0.0, settled_cash - lim["cash_buffer"])
    limits = [
        {"name": "risk per trade", "shares": int(lim["max_loss_per_trade"] / risk + 1e-9),
         "how": "$%.2f max loss / $%.2f risk a share" % (lim["max_loss_per_trade"], risk)},
        {"name": "position cap", "shares": int(lim["max_position"] / pl["stop"] + 1e-9),
         "how": "$%.2f cap / $%.2f a share at the stop" % (lim["max_position"], pl["stop"])},
        {"name": "settled cash", "shares": int(cash / pl["stop"] + 1e-9),
         "how": "$%.2f after the buffer / $%.2f a share at the stop" % (cash, pl["stop"])},
    ]
    bound = min(limits, key=lambda l: l["shares"])
    qty = max(0, bound["shares"])
    for l in limits:
        l["binds"] = l is bound
    refusal = None
    if qty < 1:
        refusal = {"risk per trade": "one share risks $%.2f to the stop, over your $%.2f max loss per trade. No order."
                                     % (risk, lim["max_loss_per_trade"]),
                   "position cap": "one share at the stop is $%.2f, over your $%.2f position cap. No order."
                                   % (pl["stop"], lim["max_position"]),
                   "settled cash": "one share at the stop is $%.2f and you have $%.2f of settled cash after the buffer. "
                                   "No order." % (pl["stop"], cash)}[bound["name"]]
    return {"qty": qty, "bound_by": bound["name"], "limits": limits, "refusal": refusal,
            "risk_per_share": risk, "max_loss": round(qty * risk, 2),
            "reward": round(qty * (pl["limit"] - pl["target"]), 2),
            "reward_risk": round((pl["limit"] - pl["target"]) / risk, 2),
            "cost": round(qty * pl["stop"], 2)}


def restricted(day_low, prev_close):
    """True when today's low is 10% or more under the previous close (SEC Rule 201's trigger)."""
    if not prev_close or not day_low:
        return False
    return day_low <= prev_close * (1 - SSR_DROP_PCT / 100) + 1e-9


def trend(closes, price):
    """'down' when the price is under its 200-day average, 'up' when over, None when there is not enough history."""
    if not closes or len(closes) < TREND_DAYS:
        return None, None
    avg = sum(closes[-TREND_DAYS:]) / float(TREND_DAYS)
    return ("down" if price < avg else "up"), round(avg, 2)


def order(sig):
    """The short as the Risk Manager reads an order: a sell with a stop above and a target below."""
    return {"symbol": sig["symbol"], "side": "sell", "qty": str(int(sig["qty"])), "type": "limit",
            "limit_price": "%.2f" % sig["limit"], "time_in_force": "day", "order_class": "bracket",
            "take_profit_limit_price": "%.2f" % sig["target"], "stop_loss_stop_price": "%.2f" % sig["stop"]}


def alert_rules(sig):
    """Arguments for the Price Alerts skill: ring when the price RISES to the stop or FALLS to the
    target, and at flat-by time. The ids end the way the desk's monitor expects."""
    sym, day = sig["symbol"], sig["date"]
    low = sym.lower()
    return [
        ["add", "--symbol", sym, "--above", "%.2f" % sig["stop"], "--id", "%s-short-stop" % low, "--date", day,
         "--note", "short stop"],
        ["add", "--symbol", sym, "--below", "%.2f" % sig["target"], "--id", "%s-short-target" % low, "--date", day,
         "--note", "short target"],
        ["add", "--symbol", sym, "--at", sig["flat_by"], "--label", "flat by close", "--id", "%s-short-flat-by" % low,
         "--date", day],
    ]


def tos_steps(sig):
    """How to enter this exact short at your broker, by hand (any broker with bracket orders)."""
    steps = []
    if sig.get("paper_only"):
        steps.append("PRACTICE ONLY. This account cannot short for real. Practise it in your broker's paper "
                     "(practice) account, never in your real account.")
    steps += [
        "At your broker, open an order ticket for %s and choose a bracket order (one entry, then a stop and a "
        "target, one cancelling the other)." % sig["symbol"],
        "Entry (the short sale): SELL SHORT, quantity %d, order type LIMIT, price %.2f, good for the DAY. It "
        "opens a short, so it may read SELL ... TO OPEN." % (sig["qty"], sig["limit"]),
        "Stop line (a BUY, above your entry): order type STOP, price %.2f. Limit line (a BUY, the target, below): "
        "LIMIT, price %.2f. Both good for the DAY." % (sig["stop"], sig["target"]),
        "Review and send. If your broker says the shares are hard to borrow or not available, or that a "
        "short-sale restriction is on, stop and skip this one. Read the confirmation: the loss is about $%.2f "
        "if the stop fills at its price, and more if the price gaps over it." % sig["max_loss"],
        "Back here, type your fill price and press 'I sold short and it filled'.",
    ]
    return steps
