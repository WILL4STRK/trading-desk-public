"""One answer per stock per scan: long, short, both or nothing, and why.

The desk looks for a long (opening-range breakout, found by the Chart Scanner
skill) and, when shorts are switched on, a short (opening-range breakdown, found
by desk/shorts.py from the stock's own candles). This file turns what each side
saw into one row per stock, so the record can answer "what did it pass on, and
why?" for both directions (docs/SHORTING.md).

Words only. Nothing here decides whether to trade; it records what the scan did.
"""

OFF = "switched off in Settings"


def note(signal=None, status=None, reason=None):
    """What one side of the scan saw for one stock: a signal (with its status) or a reason for none."""
    return {"signal": signal, "status": status, "reason": reason}


def row(symbol, long_note, short_note):
    """Combines the two sides into the logged decision. A side that is off reads 'switched off'.
    A signal of any status (ready, weak, blocked, missed) counts as that side's answer: the
    status says whether it was tradable, and the review reads it from there."""
    ln = long_note or note(reason=OFF)
    sn = short_note or note(reason=OFF)
    went_long, went_short = ln["signal"] is not None, sn["signal"] is not None
    decision = "both" if went_long and went_short else "long" if went_long else "short" if went_short else "nothing"
    return {"symbol": symbol, "decision": decision,
            "long": ln["status"] or ln["reason"], "short": sn["status"] or sn["reason"],
            "long_signal": ln["signal"], "short_signal": sn["signal"]}


def counts(rows):
    """{decision: n} and the commonest reasons for 'nothing' on each side, for the review."""
    by = {}
    why = {"long": {}, "short": {}}
    for r in rows:
        by[r["decision"]] = by.get(r["decision"], 0) + 1
        for side in ("long", "short"):
            if r[side + "_signal"] is None and r[side] and r[side] != OFF:
                why[side][r[side]] = why[side].get(r[side], 0) + 1
    top = {side: sorted(v.items(), key=lambda kv: (-kv[1], kv[0]))[:5] for side, v in why.items()}
    return {"by_decision": by, "top_reasons": top, "rows": len(rows)}
