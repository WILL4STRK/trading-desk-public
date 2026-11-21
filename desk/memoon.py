"""Rule P8, moonshot, and its control P8b (docs/MEME.md section 10). Pure functions, no network: desk/meme.py
fetches the data and calls these.

Changing a number here after a result is seen restarts the count (docs/MEME.md section 5).
"""
POLICIES = ("moonshot", "moonshot_base")
DECIDE_EVERY_MIN = 5
AGE_H = (1.0, 48.0)
R1H = (20.0, 150.0)
MIN_VACC = 1.5
MIN_TX5 = 50
MIN_BUY_RATIO = 0.55
MIN_V1H = 50000.0
MIN_LIQ = 25000.0
STOP_PCT = -25.0
TRAIL = ((150.0, 30.0), (40.0, 25.0))     # (peak gain %, fall from peak %), the highest step that applies
HOLD_MIN = 24 * 60
SIZE_PCT = 5.0
MAX_OPEN = 10
COOLDOWN_MIN = 60
MIN_COST_PCT = 4.0


def _between(x, lo, hi):
    return x is not None and lo <= x <= hi


def decide(f, liq):
    """{'moonshot': bool, 'moonshot_base': bool} from memerules.features_from_pair output and pool liquidity."""
    base = bool(_between(f.get("age_h"), *AGE_H) and (f.get("v1h") or 0) >= MIN_V1H
                and liq is not None and liq >= MIN_LIQ)
    moon = bool(base and _between(f.get("r1h"), *R1H) and (f.get("r5") or 0) > 0 and (f.get("vacc") or 0) >= MIN_VACC
                and (f.get("tx_m5") or 0) >= MIN_TX5 and (f.get("buy_ratio_m5") or 0) >= MIN_BUY_RATIO)
    return {"moonshot": moon, "moonshot_base": base}


def stop_level(entry_px, peak_px):
    """The sell level for a position bought at entry_px whose highest price so far is peak_px. It never falls as the
    peak rises: each step of the ladder keeps at least the level the step below reached at its top (docs/MEME.md
    section 10, correction of 2026-11-21)."""
    gain = (peak_px / entry_px - 1) * 100
    level = entry_px * (1 + STOP_PCT / 100)
    steps = sorted(TRAIL)                                   # lowest step first
    for i, (at, fall) in enumerate(steps):
        if gain < at:
            break
        top = steps[i + 1][0] if i + 1 < len(steps) else None
        reach = peak_px if top is None or gain < top else entry_px * (1 + top / 100)
        level = max(level, reach * (1 - fall / 100))
    return level


def exit_sample(entry_px, entry_ts, peak_px, px, liq, now, entry_liq=None, dead_liq=1000.0):
    """One forward price sample: (price, why) or None. peak_px is the peak before this sample (the caller then raises
    it). A missing price or a dead pool is a total loss."""
    if not px or (liq is not None and liq < dead_liq):
        return 0.0, "rug"
    if px <= stop_level(entry_px, peak_px):
        return px, "trail" if peak_px >= entry_px * 1.4 else "stop"
    if now >= entry_ts + HOLD_MIN * 60:
        return px, "time"
    if entry_liq and liq is not None and liq < entry_liq * 0.5:
        return px, "liquidity pulled"
    return None


def replay(entry_ts, entry_px, peak_px, bars, dead=False, now=None):
    """Walk 5-minute bars [ts, o, h, l, c, v] after the entry. Returns ({'ts', 'price', 'why'} or None, new peak).
    Each bar: the stop from the peak before it is checked against the low first (a bar opening below it fills at the
    open), then the peak rises to the bar's high."""
    end, last, peak = entry_ts + HOLD_MIN * 60, None, peak_px
    for b in sorted(bars or []):
        if b[0] < entry_ts:
            continue
        if b[0] >= end:
            return {"ts": end, "price": last if last is not None else b[1], "why": "time"}, peak
        lvl = stop_level(entry_px, peak)
        if b[3] <= lvl:
            return {"ts": b[0], "price": min(lvl, b[1]),
                    "why": "trail" if peak >= entry_px * 1.4 else "stop"}, peak
        peak, last = max(peak, b[2]), b[4]
    if dead:
        return {"ts": max(entry_ts, bars[-1][0] + 300) if bars else entry_ts, "price": 0.0, "why": "rug"}, peak
    if now is not None and now >= end:
        return {"ts": end, "price": last if last is not None else entry_px, "why": "time"}, peak
    return None, peak
