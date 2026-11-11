"""The seven parts of the rule behind a slip: market, setup, filter, entry,
exit, stop, size. If a rule cannot fill all seven it is not a rule yet.
Built from the signal's own numbers and the settings at the time, so the card
says exactly what the desk did. Words only; nothing here decides anything.
"""


def _t12(hhmm):
    try:
        h, m = (int(x) for x in str(hhmm).split(":"))
    except ValueError:
        return str(hhmm)
    return "%d:%02d %s" % ((h + 11) % 12 + 1, m, "AM" if h < 12 else "PM")


def build(sig, st):
    rng = sig.get("opening_range") or {}
    filters = ["new entries only from %s to %s New York time" % (_t12(st["no_entries_before"]), _t12(st["no_entries_after"])),
               "the Risk Manager has to pass it"]
    if st.get("min_volume_x"):
        filters.append("breakout candle volume at least %gx the opening candles" % st["min_volume_x"])
    if st.get("min_stop_pct"):
        filters.append("stop at least %g%% from the entry" % st["min_stop_pct"])
    filters.append("Time Machine edge of %+g points or better to show as ready" % st["min_edge_points"])
    have_plan = sig.get("stop") is not None and sig.get("limit") is not None
    card = [
        ("Market", "Stocks on your list priced at $%.2f or more, on 5-minute candles." % st["min_price"]),
        ("Setup", "Opening-range breakout: the first 5-minute candle to close above the high of the first %d minutes%s. "
                  "A wick above it does not count." % (st["or_minutes"], " (%.2f)" % rng["high"] if rng.get("high") else "")),
        ("Filter", "; ".join(filters)[:1].upper() + "; ".join(filters)[1:] + "."),
    ]
    if have_plan:
        card += [
            ("Entry", "Buy with a limit at %.2f or lower: the breakout close (%.2f) plus %g of the risk as room. Good until %s."
                      % (sig["limit"], sig["entry"], st["chase_r"], _t12(sig.get("valid_until")))),
            ("Exit", "Sell at the target, %.2f (%g times the risk above the entry), or by %s at the latest. Never held overnight."
                     % (sig["target"], st["target_r"], _t12(sig.get("flat_by")))),
            ("Stop", "Sell stop at %.2f (%s), which is %.2f a share under the breakout close."
                     % (sig["stop"], sig.get("stop_label") or st["stop_rule"], sig["risk_per_share"])),
        ]
        if sig.get("qty"):
            card.append(("Size", "%d whole share%s, set by your %s. The most this can lose at the stop is $%.2f; your limit is %g%% of the account a trade."
                         % (sig["qty"], "" if sig["qty"] == 1 else "s", sig.get("bound_by") or "limits", sig["max_loss"], st["risk_per_trade_pct"])))
        else:
            card.append(("Size", "No size: this one did not fit your limits. The reason is on the card."))
    else:
        card += [("Entry", "None: the signal stopped before a plan was made."),
                 ("Exit", "None."), ("Stop", "None."), ("Size", "None.")]
    return [{"part": a, "text": b} for a, b in card]
