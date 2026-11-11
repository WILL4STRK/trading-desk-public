"""Order shapes for the desk's pretend practice account (desk/paper.py, desk/auto.py).

A signal's bracket order is the same one the desk tells you to type in at your
broker by hand (pipeline.order_steps): BUY LIMIT, DAY; once it fills, a STOP
and a LIMIT to sell, DAY, one cancelling the other. Orders are kept as plain
dicts in a common nested layout (an order, its legs, its child orders). This
file only builds, checks and reads them; it sends nothing anywhere.
"""
import math
import re

TICKER = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


def _price(v, name):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
        raise ValueError("%s must be a price above 0" % name)
    return "%.2f" % round(float(v), 2)


def check(symbol, qty, limit, stop, target):
    if not isinstance(symbol, str) or not TICKER.match(symbol):
        raise ValueError("not a stock ticker: %r" % (symbol,))
    if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
        raise ValueError("shares must be a whole number of 1 or more")
    lim, stp, tgt = _price(limit, "limit"), _price(stop, "stop"), _price(target, "target")
    if not float(stp) < float(lim) < float(tgt):
        raise ValueError("the stop must be under the buy price and the target over it")
    return lim, stp, tgt


def _leg(instruction, symbol, qty):
    return [{"instruction": instruction, "quantity": qty, "instrument": {"symbol": symbol, "assetType": "EQUITY"}}]


def bracket(symbol, qty, limit, stop, target):
    """BUY qty at limit (DAY); when filled, SELL at target or at stop (DAY), one cancels the other."""
    lim, stp, tgt = check(symbol, qty, limit, stop, target)
    take_profit = {"orderType": "LIMIT", "session": "NORMAL", "price": tgt, "duration": "DAY",
                   "orderStrategyType": "SINGLE", "orderLegCollection": _leg("SELL", symbol, qty)}
    stop_loss = {"orderType": "STOP", "session": "NORMAL", "stopPrice": stp, "duration": "DAY",
                 "orderStrategyType": "SINGLE", "orderLegCollection": _leg("SELL", symbol, qty)}
    return {
        "orderType": "LIMIT", "session": "NORMAL", "price": lim, "duration": "DAY",
        "orderStrategyType": "TRIGGER", "orderLegCollection": _leg("BUY", symbol, qty),
        "childOrderStrategies": [{"orderStrategyType": "OCO", "childOrderStrategies": [take_profit, stop_loss]}],
    }


def plain(symbol, qty, limit, stop, target):
    """The same order in plain words, for the confirm box."""
    lim, stp, tgt = check(symbol, qty, limit, stop, target)
    return [
        "BUY %d %s, LIMIT %s, good for the day" % (qty, symbol, lim),
        "When it fills: SELL %d at STOP %s or at LIMIT %s, whichever comes first (the other is cancelled)"
        % (qty, stp, tgt),
    ]


# ---- one plain order (auto paper's flat-by-close sell) -------------------------
def _qty(qty):
    if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
        raise ValueError("shares must be a whole number of 1 or more")
    return qty


def _ticker(symbol):
    if not isinstance(symbol, str) or not TICKER.match(symbol):
        raise ValueError("not a stock ticker: %r" % (symbol,))
    return symbol


def single(symbol, side, qty, kind, price=None, stop=None):
    """One plain order, DAY. Sells can be MARKET, LIMIT or STOP. A buy must be a LIMIT."""
    _ticker(symbol)
    _qty(qty)
    if side not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")
    if kind not in ("MARKET", "LIMIT", "STOP") or (side == "BUY" and kind != "LIMIT"):
        raise ValueError("that order type is not supported")
    o = {"orderType": kind, "session": "NORMAL", "duration": "DAY", "orderStrategyType": "SINGLE",
         "orderLegCollection": _leg(side, symbol, qty)}
    if kind == "LIMIT":
        o["price"] = _price(price, "limit price")
    if kind == "STOP":
        o["stopPrice"] = _price(stop, "stop price")
    return o


# ---- reading an account's order list ---------------------------------------------
# Statuses where the order is still alive (it can fill, or is waiting to go live).
OPEN = {"WORKING", "PENDING_ACTIVATION", "QUEUED", "ACCEPTED", "AWAITING_PARENT_ORDER", "AWAITING_CONDITION",
        "AWAITING_STOP_CONDITION", "AWAITING_MANUAL_REVIEW", "AWAITING_UR_OUT", "AWAITING_RELEASE_TIME",
        "PENDING_ACKNOWLEDGEMENT", "NEW"}


def _f(v):
    try:
        f = float(v)
        return round(f, 4) if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _whole(v):
    f = _f(v)
    return int(f) if f is not None and float(f).is_integer() else (f or 0)


def rows(raw):
    """Nested orders as flat rows a person can read. Anything odd is skipped, never raised."""
    out, wrappers = [], [0]

    def walk(o, parent, group):
        if not isinstance(o, dict):
            return
        oid = str(o.get("orderId") or "")
        legs = o.get("orderLegCollection")
        first = legs[0] if isinstance(legs, list) and legs and isinstance(legs[0], dict) else None
        kids = o.get("childOrderStrategies") if isinstance(o.get("childOrderStrategies"), list) else []
        if first is None:                               # an OCO wrapper: no order of its own, just two children
            wrappers[0] += 1                            # each pair is its own group, even if it has no id
            for k in kids:
                walk(k, parent, oid or "pair-%d" % wrappers[0])
            return
        sym = str((first.get("instrument") or {}).get("symbol") or "").upper()
        side = str(first.get("instruction") or "").upper()
        if not oid or not sym or side not in ("BUY", "SELL"):
            return
        qty, filled = _whole(o.get("quantity")), _whole(o.get("filledQuantity"))
        fills = []
        for act in o.get("orderActivityCollection") or []:
            for ex in (act.get("executionLegs") or []) if isinstance(act, dict) else []:
                q, p = _f((ex or {}).get("quantity")), _f((ex or {}).get("price"))
                if q and p:
                    fills.append((q, p))
        done = sum(q for q, _ in fills)
        status = str(o.get("status") or "").upper()
        out.append({
            "id": oid, "parent": parent, "oco": group, "symbol": sym, "side": side, "qty": qty, "filled": filled,
            "remaining": _whole(o.get("remainingQuantity")) if o.get("remainingQuantity") is not None else max(0, qty - filled),
            "type": str(o.get("orderType") or "").upper(), "price": _f(o.get("price")), "stop": _f(o.get("stopPrice")),
            "status": status, "open": status in OPEN, "entered": str(o.get("enteredTime") or ""),
            "avg_fill": round(sum(q * p for q, p in fills) / done, 4) if done else None,
        })
        for k in kids:
            walk(k, oid, None)

    for o in raw if isinstance(raw, list) else []:
        walk(o, None, None)
    return out
