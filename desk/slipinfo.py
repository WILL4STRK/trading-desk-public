"""Extras added to every signal after the skills have run: the stamp, the
numbers after an estimated cost, the thin-evidence note and the rule card.
Kept out of pipeline.py so that file stays the skills' pipeline and nothing else.
Display and record only: none of this changes a signal's status.
"""
from . import costs
from . import rulecard
from . import stamp


def finish(sig, st, journal=None, now=None):
    sig["stamp"] = stamp.make(st, journal, now)
    if sig.get("qty") and sig.get("limit") is not None:
        sig["after_cost"] = costs.after_cost(sig["limit"], sig["stop"], sig["target"], sig["qty"],
                                             st.get("est_cost_per_share", 0), sig.get("side") or "long")
    if sig.get("odds"):
        sig["odds"]["thin"] = costs.thin_note(sig["odds"])
    sig["rule"] = rulecard.build(sig, st)
    return sig
