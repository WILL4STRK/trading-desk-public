"""Rule P7, buy after a launchpad migration, and its control P7b (docs/MEME.md section 9). Pure functions, no
network: desk/meme.py fetches the data and calls these.

Changing a number here after a result is seen restarts the count (docs/MEME.md section 5).
"""
import datetime

POLICIES = ("migration", "migration_base")
LAUNCH_SUFFIX = ("pump", "bonk")          # pump.fun and LetsBonk mints
CURVE_DEX = ("pumpfun", "launchlab")      # the launchpads' own bonding-curve pools
DECIDE_FROM_MIN = 15                      # the one decision: first run 15 to 17 minutes after the anchor
DECIDE_TO_MIN = 17
MIN_LIQ = 50000.0
MIN_V1H = 20000.0
MIN_BUYERS = 25
PRICE_BAND = (0.90, 1.20)                 # price now / first traded price after the anchor
FILL_AFTER_S = 60                         # fill at the first sample at least this long after the decision
FILL_BY_S = 150                           # ... and no later than this, or cancel
MIN_COST_PCT = 4.0                        # round trip, the report's floor
FIRST_MARK_SLACK_S = 60                   # the trade list must reach this close to the anchor for its first price
RECENT_S = 3600                           # a coin first seen this soon after its migration counts as a missed graduate
MIN_PER_SLICE = 10                        # the report's extra: at least this many trades in each of 4 time slices


def anchor(chain, addr, pair):
    """The migration time (unix seconds) for a graduated launchpad coin, or None: Solana, a pump/bonk mint, and its
    pool is no longer the launchpad's curve. The anchor is that pool's creation time."""
    if chain != "solana" or not str(addr).endswith(LAUNCH_SUFFIX) or not isinstance(pair, dict):
        return None
    if pair.get("dexId") in CURVE_DEX or not pair.get("pairCreatedAt"):
        return None
    return pair["pairCreatedAt"] / 1000.0


def in_window(now, anc):
    """'early', 'now' or 'late' for the one decision."""
    age = now - anc
    if age < DECIDE_FROM_MIN * 60:
        return "early"
    return "now" if age < DECIDE_TO_MIN * 60 else "late"


def _ts(iso):
    try:
        return datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def flow(trades, anc, creator=None):
    """From GeckoTerminal trade rows (attributes dicts, any order): (distinct buyers since the anchor, first traded
    price after the anchor or None when the list does not reach back far enough, earliest trade time seen)."""
    rows = []
    for a in trades or []:
        t = _ts(a.get("block_timestamp"))
        if t is not None and t >= anc:
            rows.append((t, a))
    rows.sort(key=lambda r: r[0])
    buyers = {a.get("tx_from_address") for _, a in rows
              if a.get("kind") == "buy" and a.get("tx_from_address") and a.get("tx_from_address") != creator}
    first = None
    if rows and rows[0][0] - anc <= FIRST_MARK_SLACK_S:
        t0, a0 = rows[0]
        p = a0.get("price_to_in_usd") if a0.get("kind") == "buy" else a0.get("price_from_in_usd")
        try:
            first = float(p) if p is not None else None
        except ValueError:
            first = None
    return len(buyers), first, (rows[0][0] if rows else None)


def first_from_bars(bars, anc):
    """Open of the first 1-minute candle at or after the anchor ([ts, o, h, l, c, v], any order), or None."""
    after = sorted(b for b in bars or [] if b[0] >= anc - 59)
    return float(after[0][1]) if after else None


def decide(liq, v1h, buyers, first_px, px):
    """({'migration': bool, 'migration_base': bool}, [reasons the full rule said no]). Missing data is a no."""
    why = []
    base = liq is not None and liq >= MIN_LIQ and (v1h or 0) >= MIN_V1H
    if liq is None or liq < MIN_LIQ:
        why.append("pool under $50k" if liq is not None else "no pool liquidity figure")
    if (v1h or 0) < MIN_V1H:
        why.append("hour volume under $20k")
    if buyers is None or buyers < MIN_BUYERS:
        why.append("%s buyers since migration (25 needed)" % ("unknown" if buyers is None else buyers))
    if not first_px or not px:
        why.append("no first traded price after migration")
    elif not (PRICE_BAND[0] <= px / first_px <= PRICE_BAND[1]):
        why.append("price %.0f%% of its first post-migration price (90-120%% needed)" % (px / first_px * 100))
    return {"migration": bool(base and not why), "migration_base": bool(base)}, why
