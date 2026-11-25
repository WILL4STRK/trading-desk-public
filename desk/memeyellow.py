"""Rule P10, yellow, and its three controls (docs/MEME.md section 21). Pure functions, no network: desk/meme.py
reads the screen and the Terminal's can't-sell block and calls these.

Every line is P0's decision (hour volume >= $20k, every 15 minutes per coin) split by the coin's screen:
  yellow         0 danger-level flags, 1 or 2 warn-level flags (the rule)
  yellow_clean   0 flags of any kind                           (control a)
  yellow_heavy   0 danger-level flags, 3 or more warn-level    (control b)
  yellow_danger  1 or more danger-level flags                  (control c)
An unknown screen, or a coin the can't-sell block stops, is in none of them. Exits and cost are section 3 and 2.

Changing a number or a definition here after a result is seen restarts the count (docs/MEME.md section 21).
"""
from . import memerules as R

POLICY = "yellow"
CLEAN, HEAVY, DANGER = "yellow_clean", "yellow_heavy", "yellow_danger"
CONTROLS = (CLEAN, HEAVY, DANGER)
POLICIES = (POLICY,) + CONTROLS
EVERYTHING = "yellow_all"          # comparator: P0 without the screen, entries since the first P10-family shadow
STATE_KEY = "yellow_v1"
WARN_MAX = 2                       # 1 or 2 warn-level flags
MIN_PER_SLICE = 10
KNOWN = ("pass", "fail")


def flags(chain, sc):
    """(danger flags, warn flags) from one saved screen, or None when the screen is unknown (section 21)."""
    sc = sc or {}
    if sc.get("verdict") not in KNOWN or sc.get("strict") not in KNOWN:
        return None
    rep = sc.get("report") or {}
    danger, warn = [], []
    if chain == "solana":
        for r in rep.get("risks") or []:
            level = r.get("level") if isinstance(r, dict) else None
            if level == "danger":
                danger.append("RugCheck danger: %s" % r.get("name"))
            elif level == "warn":
                warn.append("RugCheck warn: %s" % r.get("name"))
            else:
                return None                                # a level the definition does not know: unknown
        score = rep.get("score_normalised")
        if isinstance(score, (int, float)) and score > R.RUGCHECK_MAX:
            danger.append("RugCheck risk %s/100" % score)
    else:
        danger += ["GoPlus: %s" % w for w in sc.get("why") or []]
    warn += ["strict: %s" % w for w in sc.get("strict_why") or []]
    return danger, warn


def group(chain, sc, blocked):
    """Which of the four lines this coin's screen puts a P0 decision in, or None (unknown screen or can't-sell)."""
    if blocked:
        return None
    f = flags(chain, sc)
    if f is None:
        return None
    danger, warn = f
    if danger:
        return DANGER
    if not warn:
        return CLEAN
    return POLICY if len(warn) <= WARN_MAX else HEAVY


def decide(all_fires, chain, sc, blocked):
    """{line: fires} for the four lines: P0's decision, in the coin's one group. Unknown is no for all four."""
    g = group(chain, sc, blocked) if all_fires else None
    return {p: p == g for p in POLICIES}


def since(rows, start_ts):
    """Rows entered on or after start_ts: the comparison period of section 21."""
    return [x for x in rows if start_ts is not None and x.get("ts") is not None and x["ts"] >= start_ts]
