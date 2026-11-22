"""N1 paired shadow (docs/MEME.md §12). Observe the Careful book's booked fills and closes."""

STATE_KEY = "reentry_shadow_v1"
DAY = 24 * 60 * 60
WINDOW = 120 * DAY
SETTLE_GRACE = 4 * 60 * 60 + 150
FROZEN_SETTINGS = ("meme_starting_balance", "meme_position_pct", "meme_max_open", "meme_cost_pct",
                   "meme_chains", "meme_search_new")


def _settings(st):
    return {key: st[key] for key in FROZEN_SETTINGS}


def new(st, now):
    start = (int(now) // DAY + 1) * DAY
    return {"start_ts": start, "end_ts": start + WINDOW, "settle_by_ts": start + WINDOW + SETTLE_GRACE,
            "settings": _settings(st), "incomplete": None, "last_stop": {}, "locks": {}, "pending": {}, "rows": []}


def observe(study, st, now, entries, exits, snapshots):
    """Resolve only enrolled fills, then pair new entries using the latest booked stops."""
    if now > study["settle_by_ts"]:
        return
    enrolling = now < study["end_ts"]
    pending = study["pending"]
    rows = study["rows"]
    if study["incomplete"] is None:
        if enrolling and not st["meme_reentry_shadow"]:
            study["incomplete"] = {"ts": now, "reason": "observer disabled"}
        elif (enrolling or pending) and _settings(st) != study["settings"]:
            study["incomplete"] = {"ts": now, "reason": "reference settings changed"}

    for closed in exits:
        key = closed["key"]
        index = pending.get(key)
        if index is None or rows[index]["entry_ts"] != closed["ts"]:
            continue
        row = rows[index]
        blocked = row["lock"] is not None
        row["outcome"] = {"exit_ts": closed["exit_ts"], "observed_ts": now, "exit_fill": closed["exit_fill"],
                          "why": closed["why"], "control_pnl": closed["pnl"], "control_ret_pct": closed["ret_pct"],
                          "shadow_pnl": 0.0 if blocked else closed["pnl"]}
        del pending[key]
        if not blocked and closed["why"] == "stop":
            exit_ts = closed["exit_ts"]
            prior = study["last_stop"].get(key)
            study["last_stop"][key] = exit_ts
            if prior is not None and 0 <= exit_ts - prior < DAY:
                study["locks"][key] = {"first_stop_ts": prior, "second_stop_ts": exit_ts, "until_ts": exit_ts + DAY}

    if not enrolling or now < study["start_ts"] or study["incomplete"]:
        return
    for entry in entries:
        key = entry["key"]
        if entry["ts"] != now or entry["chain"] != "solana" or key in pending or entry["liq"] < 25000:
            continue
        pair = snapshots.get(key) or {}
        volume = (pair.get("volume") or {}).get("h1") or 0
        if not pair.get("pairAddress") or volume < 20000:
            continue
        lock = study["locks"].get(key)
        pending[key] = len(rows)
        rows.append({"key": key, "pool": pair["pairAddress"], "policy": entry["policy"], "entry_ts": now,
                     "entry_fill": entry["fill"], "size": entry["cost"], "liquidity": entry["liq"], "volume_1h": volume,
                     "lock": lock if lock and now < lock["until_ts"] else None, "outcome": None})
