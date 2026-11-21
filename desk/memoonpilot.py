"""Best-effort P8 observations from existing screens and one indexed pool tape."""
import datetime
import math
from urllib.parse import parse_qs, urlsplit

UNKNOWN = "unknown"
CACHE_SECONDS = 120
FIELDS = ("withdrawable_lp_pct", "withdrawable_liquidity_usd", "lp_unlock_ts",
          "creator_net_sell_usd_1h", "top10_net_sell_usd_1h",
          "distinct_buyers_15m", "largest_buyer_volume_share_15m")


def number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def timestamp(value):
    ts = number(value)
    if ts is not None:
        return ts if 0 < ts < 1e11 else None  # seconds, not a slot or millisecond epoch
    if isinstance(value, str):
        try:
            dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo:
                return dt.timestamp()
        except (ValueError, OverflowError, OSError):
            pass
    return None


def record(value):
    return value if isinstance(value, dict) else {}


def rows(value):
    return value if isinstance(value, list) else []


def address(chain, value):
    return (value if chain == "solana" else value.lower()) if isinstance(value, str) and value else None


def screen_inputs(chain, report, pool, burn):
    """Extract pool-scoped observations without retaining the provider's full report."""
    report = record(report)
    pool = address(chain, pool)
    if not pool:
        return {}
    unlocks = []
    unlocked = None
    excluded = set(burn) | {pool, None}
    if chain == "solana":
        markets = [record(m) for m in rows(report.get("markets"))]
        excluded.update(record(report.get("knownAccounts")))
        for market in markets:
            excluded.update(market.get(k) for k in ("pubkey", "liquidityA", "liquidityB")
                            if isinstance(market.get(k), str))
        market = next((m for m in markets if m.get("pubkey") == pool), {})
        lp = record(market.get("lp"))
        lp_mint = lp.get("lpMint") or market.get("mintLP")
        locked = number(lp.get("lpLockedPct"))
        # The % is meaningful for a fungible LP market with recorded supply.
        supply = number(lp.get("lpTotalSupply"))
        if lp_mint and lp_mint != "11111111111111111111111111111111" and supply and supply > 0:
            if locked is not None and 0 <= locked <= 100:
                unlocked = 100 - locked
        unlocks.append(timestamp(lp.get("unlockDate")))
        for raw in record(report.get("lockers")).values():
            locker = record(raw)
            hints = parse_qs(urlsplit(str(locker.get("uri") or "")).query).get("pool_id", [])
            if (locker.get("pool") == pool or pool in hints or
                    (lp_mint and locker.get("lpMint") == lp_mint)):
                unlocks.append(timestamp(locker.get("unlockDate")))
        holders = rows(report.get("topHolders"))
        creator = address(chain, report.get("creator"))
        owner_key, share_key = "owner", "pct"
    else:
        markets = [record(m) for m in rows(report.get("dex"))]
        excluded.update(address(chain, m.get("pair")) for m in markets)
        # Token-level LP holders have no per-row pool ID. Use them only for an unambiguous V2 report.
        if len(markets) == 1 and address(chain, markets[0].get("pair")) == pool and markets[0].get("liquidity_type") == "UniV2":
            lp = {}
            for raw in rows(report.get("lp_holders")):
                holder = record(raw)
                owner = address(chain, holder.get("address"))
                share = number(holder.get("percent"))
                locked = str(holder.get("is_locked"))
                if not owner or owner in lp or share is None or not 0 <= share <= 1 or (owner not in burn and locked not in ("0", "1")):
                    lp = {}
                    break
                lp[owner] = (share, locked)
                if owner not in burn and locked == "1":
                    unlocks.extend(timestamp(record(item).get("end_time")) for item in rows(holder.get("locked_detail")))
            if lp and math.isclose(sum(share for share, _ in lp.values()), 1, abs_tol=0.0001):
                unlocked = 100 * sum(share for owner, (share, locked) in lp.items()
                                     if locked == "0" and owner not in burn)
        holders = rows(report.get("holders"))
        creator = address(chain, report.get("creator_address"))
        owner_key, share_key = "address", "percent"

    shares = {}
    for raw in holders:
        holder = record(raw)
        owner = address(chain, holder.get(owner_key))
        if owner in excluded or str(holder.get("is_contract")) == "1":
            continue
        share = number(holder.get(share_key))
        if share is None or not 0 <= share <= (100 if chain == "solana" else 1):
            shares = {}
            break
        shares[owner] = shares.get(owner, 0) + share
    top10 = sorted(shares, key=lambda owner: (-shares[owner], owner))[:10]
    return {"pool": pool, "unlocked": unlocked, "unlocks": sorted({u for u in unlocks if u}),
            "creator": creator if creator not in excluded else None, "top10": top10 if len(top10) == 10 else []}


def trade_page(chain, mint, data, as_of):
    """Return a time-fenced provider page with verified token-side direction."""
    mint = address(chain, mint)
    trades = {}
    for item in rows(record(data).get("data")):
        item = record(item)
        attrs = record(item.get("attributes"))
        ts = timestamp(attrs.get("block_timestamp"))
        maker = address(chain, attrs.get("tx_from_address"))
        this_id = item.get("id")
        source = address(chain, attrs.get("from_token_address"))
        target = address(chain, attrs.get("to_token_address"))
        volume = number(attrs.get("volume_in_usd"))
        if not ts or not maker or not source or not target or not isinstance(this_id, str) or not this_id:
            return None
        if (source == mint) == (target == mint) or volume is None or volume < 0:
            return None
        if ts > as_of:
            continue
        trade = (ts, maker, target == mint, volume)
        if this_id in trades and trades[this_id] != trade:
            return None
        trades[this_id] = trade
    if not trades:
        return None
    values = sorted(trades.values())
    return (as_of, values) if as_of - values[-1][0] <= CACHE_SECONDS else None


def diagnostics(chain, pool, liq, screen, page, now):
    out = dict.fromkeys(FIELDS, UNKNOWN)
    source = record(record(screen.get("report")).get("pilot"))
    if source.get("pool") == address(chain, pool):
        percent = source.get("unlocked")
        if percent is not None:
            out["withdrawable_lp_pct"] = round(percent, 4)
            if liq is not None:
                out["withdrawable_liquidity_usd"] = round(liq * percent / 100, 2)
        unlocks = source.get("unlocks") or []
        if unlocks:
            out["lp_unlock_ts"] = next((u for u in unlocks if u >= now), unlocks[-1])
        out["screen_ts"] = screen.get("ts", UNKNOWN)
    else:
        source = {}
        out["screen_ts"] = UNKNOWN
    out["trade_as_of"] = UNKNOWN
    if page is None or not 0 <= now - page[0] <= CACHE_SECONDS:
        return out
    as_of, trades = page
    out["trade_as_of"] = as_of
    out["oldest_trade_ts"] = trades[0][0]
    if trades[0][0] <= as_of - 900:
        buys = {}
        for ts, maker, buy, volume in trades:
            if ts > as_of - 900 and buy:
                buys[maker] = buys.get(maker, 0) + volume
        out["distinct_buyers_15m"] = len(buys)
        if sum(buys.values()) > 0:
            out["largest_buyer_volume_share_15m"] = round(max(buys.values()) / sum(buys.values()), 6)
    if trades[0][0] <= as_of - 3600:
        for name, owners in (("creator", [source.get("creator")]), ("top10", source.get("top10") or [])):
            owners = set(owners) - {None}
            if owners:
                out[name + "_net_sell_usd_1h"] = round(sum(-volume if buy else volume
                    for ts, maker, buy, volume in trades if ts > as_of - 3600 and maker in owners), 2)
    return out
