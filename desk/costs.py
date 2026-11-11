"""Two honesty checks on a signal's numbers. Display only: nothing here blocks.

1. Costs. The slip's reward:risk assumes you buy at the limit and sell exactly
   at the stop or the target. In real trading a stop fills a little worse and
   the spread takes a little each way. est_cost_per_share is that slice, per
   share, for the whole round trip. On a cheap stock with a tight stop it is a
   real part of the reward.
2. Thin evidence. The "same rule, last 60 days" line can rest on three or four
   triggers. A handful of trades proves nothing either way.
"""
MIN_TRIGGERS = 10


def after_cost(limit, stop, target, qty, est_cost_per_share):
    """Reward and risk at the limit price once the estimated cost is taken off both ends."""
    c = max(0.0, float(est_cost_per_share or 0))
    risk_ps = (limit - stop) + c
    reward_ps = (target - limit) - c
    if risk_ps <= 0:
        return None
    return {"per_share": round(c, 4),
            "total": round(c * qty, 2),
            "reward": round(reward_ps * qty, 2),
            "max_loss": round(risk_ps * qty, 2),
            "reward_risk": round(reward_ps / risk_ps, 2)}


def thin_note(odds, min_triggers=MIN_TRIGGERS):
    """A sentence when the back-check has too few trades to mean much, else None."""
    h = (odds or {}).get("history") or {}
    n, days = h.get("triggered"), h.get("sessions")
    if n is None or n >= min_triggers:
        return None
    if n == 0:
        return "Thin evidence: this rule did not trigger once on this stock in the last %s days." % (days or "60")
    return ("Thin evidence: this rule triggered only %d time%s on this stock in the last %s days. "
            "Too few to judge. Under %d is a guess, not a record." % (n, "" if n == 1 else "s", days or "60", min_triggers))
