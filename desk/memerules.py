"""Model 4's fixed rules (docs/MEME.md sections 2 and 3): features, the five policies, the safety screen verdict,
costs and the bracket exit replayed on bars. Pure functions, no network, shared by desk/meme.py and
research/meme/backtest.py so the forward record and the backtest can never use different rules.

Changing a number here after a result is seen restarts the count (docs/MEME.md section 5).
"""

COST_PCT = 3.5          # round trip, %, half on each side (meme_cost_pct overrides it on the desk)
STOP_PCT = -15.0        # from the buy price before costs
TARGET_PCT = 30.0
HOLD_MIN = 240          # time stop
COOLDOWN_MIN = 60       # after an exit, before the same policy may buy the same token again
DECIDE_EVERY_MIN = 15   # one decision point per token every 15 minutes
DEAD_LIQ = 1000.0       # a pool under this much liquidity is a total loss
MIN_V1H = 20000.0
HORIZONS = (15, 60, 240)
POLICIES = ("all", "hype", "breakout", "calm_aged", "dip_aged", "aged_liquid", "news")
CHAIN_COST = {"ethereum": 6.0}   # docs/MEME.md section 6: gas makes a small Ethereum round trip about 5 to 7 %
PROMO_FEEDS = ("boosts-latest", "boosts-top", "ads-latest")
RUGCHECK_MAX = 30       # score_normalised: 0 safe .. 100 risky
MAX_TAX = 0.10          # GoPlus buy/sell tax, as a fraction


def _between(x, lo, hi):
    return x is not None and lo <= x <= hi


def decide(f):
    """{policy: True/False} for one decision point. f holds age_h, r5, r1h, r6h, r24h, v1h, vacc, rvol and,
    forward only, buy_ratio_m5 and tx_m5 (None in the backtest: candles have no buy/sell split).
    A feature that is None makes any rule that needs it say no."""
    age, v1h = f.get("age_h"), f.get("v1h") or 0.0
    liquid = v1h >= MIN_V1H
    out = {"all": liquid}
    hype = liquid and age is not None and age >= 0.5 and _between(f.get("r5"), 0.5, 25) \
        and _between(f.get("r1h"), 0, 150) and (f.get("vacc") or 0) >= 1.1
    if f.get("tx_m5") is not None:      # forward: the old memebot's buy pressure test as well
        hype = hype and f["tx_m5"] >= 20 and (f.get("buy_ratio_m5") or 0) >= 0.55
    out["hype"] = bool(hype)
    out["breakout"] = bool(liquid and age is not None and age >= 6 and (f.get("rvol") or 0) >= 1.5
                           and f.get("above_6h_high") is True and f.get("r1h") is not None and f["r1h"] <= 100)
    aged = liquid and _between(age, 24, 168)
    out["calm_aged"] = bool(aged and _between(f.get("r1h"), -10, 10) and f.get("r6h") is not None
                            and f["r6h"] > -30 and f.get("r24h") is not None and f["r24h"] < 100)
    out["dip_aged"] = bool(aged and f.get("r1h") is not None and f["r1h"] <= -10
                           and f.get("r6h") is not None and f["r6h"] > -50)
    liq, mcap = f.get("liq"), f.get("mcap")
    out["aged_liquid"] = bool(_between(age, 72, 336) and liq is not None and liq >= 50000 and mcap
                              and liq / mcap >= 0.10 and (f.get("v24") or 0) >= 50000 and _between(f.get("r24h"), -15, 25)
                              and _between(f.get("r1h"), -5, 5) and f.get("boosts_active") == 0)
    out["news"] = bool(f.get("news") and liquid and age is not None and age >= 1 and _between(f.get("r1h"), -10, 50))
    return out


def vetoes(f, promo_recent=False):
    """Research filters R1 to R3 (docs/MEME.md section 6): the reasons a careful trader would skip this buy."""
    out = []
    if f.get("age_h") is None or f["age_h"] < 24:
        out.append("R1 under 24 h old")
    if (f.get("boosts_active") or 0) > 0 or promo_recent:
        out.append("R2 paid promotion")
    if (f.get("rvol") or 0) >= 5 and f.get("r1h") is not None and abs(f["r1h"]) < 5:
        out.append("R3 flat volume burst")
    return out


DEAD = ("0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead")
OWNER_POWERS = ("is_mintable", "hidden_owner", "can_take_back_ownership", "owner_change_balance", "transfer_pausable",
                "slippage_modifiable", "personal_slippage_modifiable", "is_blacklisted", "trading_cooldown", "is_proxy",
                "external_call")


def strict_verdict(chain, report, pool=None):
    """The strict safety check, research S1 to S3 (docs/MEME.md section 6). report: RugCheck's FULL report (solana) or
    GoPlus's token result (EVM). 'pass', 'fail' or 'unknown' with reasons; unknown is never a pass."""
    if not report:
        return "unknown", ["no full safety report"]
    why = []
    if chain == "solana":
        try:
            pools = {m.get("pubkey") for m in report.get("markets") or []}
            known = set((report.get("knownAccounts") or {}).keys())
            holders = [h for h in report.get("topHolders") or []
                       if h.get("owner") not in pools and h.get("address") not in pools
                       and h.get("owner") not in known and h.get("address") not in known]
            if not report.get("topHolders"):
                return "unknown", ["no holder list"]
            top10 = sum(float(h.get("pct") or 0) for h in holders[:10])
            supply = float((report.get("token") or {}).get("supply") or 0)
            creator = float(report.get("creatorBalance") or 0) / supply * 100 if supply else None
            mkt = [m for m in report.get("markets") or [] if m.get("pubkey") == pool]
            lock = (mkt[0].get("lp") or {}).get("lpLockedPct") if mkt else None
        except (TypeError, ValueError, AttributeError):
            return "unknown", ["holder data unreadable"]
        if top10 > 30:
            why.append("top 10 holders %.0f%%" % top10)
        if creator is None:
            return "unknown", ["no supply figure"]
        if creator > 5:
            why.append("creator holds %.0f%%" % creator)
        if lock is None:
            return ("fail" if why else "unknown"), why or ["traded pool not in the report"]
        if float(lock) < 90:
            why.append("pool %.0f%% locked" % float(lock))
        return ("fail" if why else "pass"), why
    try:
        cp = float(report.get("creator_percent") or 0) + float(report.get("owner_percent") or 0)
        holders = report.get("holders")
        if not holders:
            return "unknown", ["no holder list"]
        top10 = sum(sorted((float(h.get("percent") or 0) for h in holders if str(h.get("is_contract")) != "1"
                            and str(h.get("is_locked")) != "1" and str(h.get("address", "")).lower() not in DEAD),
                           reverse=True)[:10])
    except (TypeError, ValueError, AttributeError):
        return "unknown", ["GoPlus holder data unreadable"]
    if report.get("creator_percent") in (None, "") and report.get("owner_percent") in (None, ""):
        return "unknown", ["GoPlus has no creator or owner share"]
    if cp > 0.05:
        why.append("creator and owner hold %.0f%%" % (cp * 100))
    if top10 > 0.30:
        why.append("top 10 holders %.0f%%" % (top10 * 100))
    renounced = str(report.get("owner_address") or "").lower() in DEAD + ("",)
    if not renounced:
        why += ["owner power: %s" % k.replace("_", " ") for k in OWNER_POWERS if str(report.get(k)) == "1"]
    if str(report.get("honeypot_with_same_creator")) == "1":
        why.append("creator made a honeypot before")
    v2 = [d for d in report.get("dex") or [] if d.get("liquidity_type") == "UniV2"]
    if v2:
        locked = sum(float(h.get("percent") or 0) for h in report.get("lp_holders") or []
                     if str(h.get("is_locked")) == "1" or str(h.get("address", "")).lower() in DEAD)
        if locked < 0.90:
            why.append("LP %.0f%% locked" % (locked * 100))
    return ("fail" if why else "pass"), why


def features_from_bars(bars, i, created_ts=None):
    """Features at the close of bars[i] from 5-minute candles [ts, open, high, low, close, usd_volume], oldest
    first. GeckoTerminal leaves out a 5-minute slot with no trades, so windows are measured by time, not count."""
    t, close = bars[i][0], bars[i][4]

    def price_at(secs):            # last close at or before t - secs
        target, p = t - secs, None
        for b in reversed(bars[:i + 1]):
            if b[0] <= target:
                return b[4]
        return p

    def vol(secs):
        return sum(b[5] for b in bars[:i + 1] if b[0] > t - secs)

    def chg(secs):
        p = price_at(secs)
        return None if not p else (close / p - 1) * 100

    v1h, v24 = vol(3600), vol(86400)
    first = bars[0][0]
    hist_h = (t - first) / 3600
    prior = [b[2] for b in bars[:i + 1] if t - 7 * 3600 < b[0] <= t - 3600]
    return {"age_h": (t - created_ts) / 3600 if created_ts else None,
            "r5": chg(300), "r1h": chg(3600) if hist_h >= 1 else None, "r6h": chg(6 * 3600) if hist_h >= 6 else None,
            "r24h": chg(86400) if hist_h >= 24 else None, "v1h": v1h,
            "vacc": (bars[i][5] * 12 / v1h) if v1h else None,
            "rvol": (v1h / (v24 / 24)) if v24 and hist_h >= 24 else None,
            "above_6h_high": (close > max(prior)) if prior and hist_h >= 7 else None,
            "tx_m5": None, "buy_ratio_m5": None}


def features_from_pair(p, now_ms):
    """Features from a DexScreener /tokens/v1 pair (forward). above_6h_high cannot be known from a snapshot, so the
    desk passes its own sampled 6-hour high in p['_hi6'] when it has one."""
    pc, vol, tx = p.get("priceChange") or {}, p.get("volume") or {}, (p.get("txns") or {}).get("m5") or {}
    v1h, v24 = vol.get("h1") or 0.0, vol.get("h24") or 0.0
    created = p.get("pairCreatedAt")
    n = (tx.get("buys") or 0) + (tx.get("sells") or 0)
    price = float(p.get("priceUsd") or 0) or None
    hi6 = p.get("_hi6")
    liq = (p.get("liquidity") or {}).get("usd")
    return {"age_h": (now_ms - created) / 3.6e6 if created else None,
            "r5": pc.get("m5"), "r1h": pc.get("h1"), "r6h": pc.get("h6"), "r24h": pc.get("h24"), "v1h": v1h,
            "vacc": ((vol.get("m5") or 0) * 12 / v1h) if v1h else None,
            "rvol": (v1h / (v24 / 24)) if v24 else None,
            "above_6h_high": (price > hi6) if price and hi6 else None,
            "tx_m5": n, "buy_ratio_m5": (tx.get("buys") or 0) / n if n else None,
            "liq": float(liq) if liq is not None else None, "mcap": p.get("marketCap") or p.get("fdv"),
            "v24": v24, "boosts_active": (p.get("boosts") or {}).get("active", 0), "news": p.get("_news")}


def screen_verdict(chain, report):
    """'pass', 'fail' or 'unknown' with reasons. report is RugCheck's summary (solana) or GoPlus's token result
    (EVM), or None. Unknown is never a pass (docs/MEME.md section 3)."""
    if not report:
        return "unknown", ["no safety report"]
    why = []
    if chain == "solana":
        s = report.get("score_normalised")
        if s is None:
            return "unknown", ["RugCheck gave no score"]
        if s > RUGCHECK_MAX:
            why.append("RugCheck risk %s/100" % s)
        why += ["danger: %s" % r.get("name") for r in report.get("risks") or [] if r.get("level") == "danger"]
        return ("fail" if why else "pass"), why
    need = ("is_honeypot", "buy_tax", "sell_tax", "cannot_sell_all", "is_open_source")
    missing = [k for k in need if report.get(k) in (None, "")]
    if missing:
        return "unknown", ["GoPlus has no %s" % ", ".join(missing)]
    if str(report["is_honeypot"]) == "1":
        why.append("honeypot")
    for k in ("buy_tax", "sell_tax"):
        try:
            if float(report[k]) > MAX_TAX:
                why.append("%s %.0f%%" % (k.replace("_", " "), float(report[k]) * 100))
        except ValueError:
            return "unknown", ["GoPlus %s unreadable" % k]
    if str(report["cannot_sell_all"]) == "1":
        why.append("cannot sell all")
    if str(report["is_open_source"]) != "1":
        why.append("contract not open source")
    return ("fail" if why else "pass"), why


def net_pct(buy, sell, cost_pct=COST_PCT):
    """Net return, %, after the round-trip cost charged half on each side. sell 0 = total loss."""
    if not sell:
        return -100.0
    h = cost_pct / 200
    return (sell * (1 - h) / (buy * (1 + h)) - 1) * 100


def replay(entry_ts, entry_px, bars, dead=False, now=None, stop=STOP_PCT, target=TARGET_PCT, hold_min=HOLD_MIN):
    """Walk bars ([ts, o, h, l, c, v], each covering ts .. ts+300) after the entry and return the exit:
    {'ts', 'price', 'why'} or None while it is still open. The stop is assumed first when one bar touches both; a bar
    opening below the stop fills at its open. dead=True (the pool is gone) closes at 0 once the bars run out. With now past
    the time stop and no bar after it (no trades), it closes at the last price."""
    sp, tp, end = entry_px * (1 + stop / 100), entry_px * (1 + target / 100), entry_ts + hold_min * 60
    last = None
    for b in bars:
        if b[0] < entry_ts:
            continue
        if b[0] >= end:
            return {"ts": end, "price": last if last is not None else b[1], "why": "time"}
        o, h, l, c = b[1], b[2], b[3], b[4]
        if l <= sp:
            return {"ts": b[0], "price": min(sp, o), "why": "stop"}
        if h >= tp:
            return {"ts": b[0], "price": max(tp, o), "why": "target"}
        last = c
    if dead:
        return {"ts": max(entry_ts, bars[-1][0] + 300) if bars else entry_ts, "price": 0.0, "why": "rug"}
    if now is not None and now >= end:
        return {"ts": end, "price": last if last is not None else entry_px, "why": "time"}
    return None


def horizon_exit(entry_ts, bars, minutes):
    """Close at the last price at or before entry + minutes (None if the bars do not reach that far)."""
    t, px = entry_ts + minutes * 60, None
    if not bars or bars[-1][0] + 300 < t:
        return None
    for b in bars:
        if b[0] + 300 > t:
            break
        px = b[4]
    return px
