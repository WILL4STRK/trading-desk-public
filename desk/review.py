"""Reading the record: results, plan against what happened, and the day summary.

Plain functions over rows from the journal. Nothing here changes a rule, and
nothing here says what to do next: it lays the record out so you (or anyone helping you)
can read it before deciding whether to change a rule.

The video's list of what to look at besides profit is the list here: number
of trades, share of winners, average win and loss, worst drawdown, and how far
real fills were from the plan.
"""
from . import __version__
from . import stamp

MIN_TRADES = 20      # under this, every number below can flip with one trade
SIGNAL_ORDER = ("taken", "skipped", "expired", "ready", "weak", "blocked", "missed")


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _r2(x):
    return None if x is None else round(x, 2)


def closed(trades):
    rows = [t for t in trades if t.get("status") == "closed" and t.get("pnl") is not None]
    return sorted(rows, key=lambda t: (t.get("exit_at") or "", t.get("id") or 0))


def results(trades):
    """The numbers for a list of trades. Every figure is from closed trades only."""
    rows = closed(trades)
    wins = [t["pnl"] for t in rows if t["pnl"] > 0]
    losses = [t["pnl"] for t in rows if t["pnl"] < 0]
    total = round(sum(t["pnl"] for t in rows), 2)
    peak = run = drawdown = 0.0
    streak = worst_streak = 0
    for t in rows:
        run += t["pnl"]
        peak = max(peak, run)
        drawdown = min(drawdown, run - peak)
        streak = streak + 1 if t["pnl"] < 0 else 0
        worst_streak = max(worst_streak, streak)
    avg_win, avg_loss = _mean(wins), _mean(losses)
    return {
        "closed": len(rows), "wins": len(wins), "losses": len(losses), "even": len(rows) - len(wins) - len(losses),
        "win_rate": round(100.0 * len(wins) / len(rows), 1) if rows else None,
        "total": total, "per_trade": _r2(total / len(rows)) if rows else None,
        "avg_r": _r2(_mean([t.get("r_multiple") for t in rows])),
        "avg_win": _r2(avg_win), "avg_loss": _r2(avg_loss),
        "payoff": _r2(avg_win / -avg_loss) if avg_win is not None and avg_loss else None,
        "max_drawdown": round(drawdown, 2), "losing_run": worst_streak,
    }


def standing(res, min_trades=MIN_TRADES):
    """Where the record stands against break-even, in words. A description, not advice."""
    n = res["closed"]
    if n < min_trades:
        if n == 0:
            return {"enough": False, "label": "No closed trades yet", "note": "Nothing to read until trades are recorded."}
        return {"enough": False, "label": "Too few to judge",
                "note": "Only %d closed trade%s. Under %d, one trade can flip every number here."
                        % (n, "" if n == 1 else "s", min_trades)}
    r = res["avg_r"] or 0.0
    label = "Ahead of break-even" if r > 0.1 else "Behind break-even" if r < -0.1 else "About break-even"
    return {"enough": True, "label": label,
            "note": "%d closed trades, average %+.2fR. One bad week is not a reason to change a rule." % (n, r)}


def by_exit(trades):
    out = {}
    for t in closed(trades):
        k = t.get("exit_reason") or "manual"
        o = out.setdefault(k, {"n": 0, "total": 0.0})
        o["n"] += 1
        o["total"] = round(o["total"] + t["pnl"], 2)
    return out


def by_rules(trades, sig_by_id, rulesets=None):
    groups = {}
    for t in closed(trades):
        p = (sig_by_id.get(t.get("signal_id")) or {}).get("payload") or {}
        groups.setdefault((p.get("stamp") or {}).get("rules") or "unstamped", []).append(t)
    out = []
    for h, rows in groups.items():
        r = results(rows)
        out.append({"rules": h, "first_seen": ((rulesets or {}).get(h) or {}).get("first_seen"),
                    "closed": r["closed"], "total": r["total"], "avg_r": r["avg_r"], "win_rate": r["win_rate"]})
    return sorted(out, key=lambda g: g["first_seen"] or "")


def plan_vs_actual(trades, sig_by_id):
    """What the slip said against what was recorded. Positive slip = you paid more than planned."""
    fill_slip, over, fewer, stop_gap, target_gap = [], 0, 0, [], []
    planned = 0
    for t in trades:
        p = (sig_by_id.get(t.get("signal_id")) or {}).get("payload") or {}
        if p.get("limit") is not None:
            planned += 1
            fill_slip.append(t["entry_price"] - p["limit"])
            over += 1 if t["entry_price"] > p["limit"] + 0.005 else 0
            fewer += 1 if p.get("qty") and t["qty"] < p["qty"] else 0
        if t.get("status") == "closed" and t.get("exit_price") is not None:
            if t.get("exit_reason") == "stop":
                stop_gap.append(t["exit_price"] - t["stop"])
            elif t.get("exit_reason") == "target":
                target_gap.append(t["exit_price"] - t["target"])
    done = closed(trades)
    return {
        "from_signals": planned, "avg_fill_vs_limit": _r2(_mean(fill_slip)), "fills_over_limit": over, "took_fewer_shares": fewer,
        "stop_exits": len(stop_gap), "avg_stop_vs_plan": _r2(_mean(stop_gap)),
        "target_exits": len(target_gap), "avg_target_vs_plan": _r2(_mean(target_gap)),
        "closed_early": sum(1 for t in done if t.get("exit_reason") == "manual"),
        "closed_end_of_day": sum(1 for t in done if t.get("exit_reason") == "flat"),
    }


def by_source(trades):
    """The desk's own paper trades (auto paper, note "auto") against the ones you recorded."""
    groups = {}
    for t in closed(trades):
        groups.setdefault("auto" if t.get("note") == "auto" else "you", []).append(t)
    return {k: {"closed": r["closed"], "total": r["total"], "avg_r": r["avg_r"], "win_rate": r["win_rate"]}
            for k, r in ((k, results(v)) for k, v in sorted(groups.items()))}


def outcome_table(outcomes):
    """Per signal status (taken, skipped, expired, weak, blocked...): how the replays came out."""
    out = {}
    for o in outcomes:
        g = out.setdefault(o["status"], {"n": 0, "results": {}, "rs": []})
        g["n"] += 1
        g["results"][o["result"]] = g["results"].get(o["result"], 0) + 1
        if o.get("r") is not None:
            g["rs"].append(o["r"])
    return {k: {"n": g["n"], "results": g["results"], "avg_r": _r2(_mean(g["rs"])), "with_r": len(g["rs"])}
            for k, g in out.items()}


def odds_check(signals, outcomes):
    """Did the Time Machine's odds hold up? Its share of target-first against the replays' share."""
    by = {o["signal_id"]: o for o in outcomes}
    rows = [(s["payload"]["odds"]["p_target"], s["payload"]["odds"]["p_stop"], by[s["id"]]["result"])
            for s in signals if (s.get("payload") or {}).get("odds") and s["id"] in by
            and by[s["id"]]["result"] in ("target", "stop", "flat", "open")]
    if not rows:
        return None
    return {"n": len(rows), "predicted_target": _r2(100 * _mean([r[0] for r in rows])),
            "predicted_stop": _r2(100 * _mean([r[1] for r in rows])),
            "actual_target": _r2(100.0 * sum(1 for r in rows if r[2] == "target") / len(rows)),
            "actual_stop": _r2(100.0 * sum(1 for r in rows if r[2] == "stop") / len(rows))}


def scan_stats(scans):
    ran = [s["summary"] for s in scans if not s["summary"].get("halt")]
    if not ran:
        return None
    failed = sum(1 for s in ran if s.get("error"))
    return {"scans": len(ran), "days": len({s["at"][:10] for s in ran}), "failed": failed,
            "avg_charts": _r2(_mean([s.get("scanned") for s in ran])),
            "avg_gaps": _r2(_mean([s.get("unusable") for s in ran if "unusable" in s])),
            "breakouts": sum(s.get("hits") or 0 for s in ran), "new_signals": sum(s.get("new_signals") or 0 for s in ran)}


def signal_counts(signals):
    out = {}
    for s in signals:
        out[s["status"]] = out.get(s["status"], 0) + 1
    return out


def summary(trades, signals, events=(), rulesets=None, min_trades=MIN_TRADES, outcomes=(), scans=()):
    sig_by_id = {s["id"]: s for s in signals}
    res = results(trades)
    return {
        "results": res, "standing": standing(res, min_trades), "min_trades": min_trades,
        "by_exit": by_exit(trades), "by_rules": by_rules(trades, sig_by_id, rulesets),
        "plan": plan_vs_actual(trades, sig_by_id),
        "signals": signal_counts(signals), "signals_total": len(signals),
        "open": sum(1 for t in trades if t.get("status") == "open"),
        "settings_changes": [{"ts": e["ts"], "message": e["message"]} for e in events if e.get("kind") == "settings"][:50],
        "by_source": by_source(trades), "outcomes": outcome_table(outcomes), "odds_check": odds_check(signals, outcomes),
        "scans": scan_stats(scans), "rulesets": rulesets or {},
        "practice_fills": sum(1 for e in events if e.get("kind") == "paper"),
    }


# ---- the day ---------------------------------------------------------
def _usd(x, sign=True):
    return "%s$%.2f" % (("+" if x > 0 else "-" if x < 0 else "") if sign else ("-" if x < 0 else ""), abs(x))


def day_summary(date, trades, signals):
    """One paragraph on a day, or None when nothing happened."""
    sigs = [s for s in signals if s.get("date") == date]
    rows = [t for t in trades if t.get("date") == date]
    if not sigs and not rows:
        return None
    c = signal_counts(sigs)
    parts = ", ".join("%d %s" % (c[k], k) for k in SIGNAL_ORDER if c.get(k))
    text = "Day summary %s: %d signal%s%s." % (date, len(sigs), "" if len(sigs) == 1 else "s", " (%s)" % parts if parts else "")
    done = closed(rows)
    if rows:
        text += " You took %d trade%s." % (len(rows), "" if len(rows) == 1 else "s")
    if done:
        r = _mean([t.get("r_multiple") for t in done])
        text += " Closed %d: %s%s." % (len(done), _usd(sum(t["pnl"] for t in done)), " (%+.2fR a trade)" % r if r is not None else "")
    still = [t["symbol"] for t in rows if t.get("status") == "open"]
    if still:
        text += " Still marked open: %s. Day trades are flat by the close: record the exit." % ", ".join(still)
    if not rows:
        text += " No trades taken. That is a fine day."
    return text


# ---- the plain-text review report ------------------------------------
def report(mode, s, now, rules_hash=None):
    r, st, p = s["results"], s["standing"], s["plan"]
    f = lambda x, fmt="%+.2f": "n/a" if x is None else fmt % x
    L = ["Trading Desk review: %s record, as of %s" % (mode.upper(), now),
         "Desk %s, skills %s%s" % (__version__, stamp.skills_commit(), ", current rule set %s" % rules_hash if rules_hash else ""),
         "",
         "RESULTS (%d closed, %d open)" % (r["closed"], s["open"]),
         "  %s. %s" % (st["label"], st["note"])]
    if r["closed"]:
        L += ["  Winners %d, losers %d, even %d. Win rate %s%%." % (r["wins"], r["losses"], r["even"], f(r["win_rate"], "%.1f")),
              "  Total %s. Per trade %s. Average R %s." % (_usd(r["total"]), _usd(r["per_trade"]), f(r["avg_r"])),
              "  Average win %s, average loss %s, payoff %s." % ("n/a" if r["avg_win"] is None else _usd(r["avg_win"]),
                                                               "n/a" if r["avg_loss"] is None else _usd(r["avg_loss"]), f(r["payoff"], "%.2f")),
              "  Worst drawdown %s. Longest losing run %d." % (_usd(r["max_drawdown"]), r["losing_run"]),
              "  By exit: " + (", ".join("%s %d (%s)" % (k, v["n"], _usd(v["total"])) for k, v in sorted(s["by_exit"].items())) or "none")]
    L += ["", "PLAN AGAINST WHAT HAPPENED",
          "  Fills from a signal: %d. Average fill against the slip's limit: %s a share (plus = paid more)."
          % (p["from_signals"], f(p["avg_fill_vs_limit"])),
          "  Fills above the limit (the slip says skip those): %d. Took fewer shares than sized: %d." % (p["fills_over_limit"], p["took_fewer_shares"]),
          "  Stop exits: %d, average %s a share against the planned stop (minus = worse)." % (p["stop_exits"], f(p["avg_stop_vs_plan"])),
          "  Target exits: %d, average %s a share against the planned target." % (p["target_exits"], f(p["avg_target_vs_plan"])),
          "  Closed early by hand: %d. Closed at the end of the day: %d." % (p["closed_early"], p["closed_end_of_day"]),
          "", "SIGNALS (%d)" % s["signals_total"],
          "  " + (", ".join("%s %d" % (k, s["signals"][k]) for k in SIGNAL_ORDER if s["signals"].get(k)) or "none"),
          "", "WHO TOOK THEM (auto = auto paper, you = recorded by you)"]
    L += ["  %s: %d closed, %s, win rate %s%%, average R %s" % (k, v["closed"], _usd(v["total"]), f(v["win_rate"], "%.1f"),
                                                               f(v["avg_r"])) for k, v in s.get("by_source", {}).items()] or ["  none"]
    L += ["  Practice-account fills logged: %d." % s.get("practice_fills", 0),
          "", "HOW EVERY SIGNAL WOULD HAVE DONE (replayed on the day's candles with the slip's own numbers)"]
    L += ["  %s %d: %s; average %sR over %d that filled" % (k, v["n"], ", ".join("%s %d" % kv for kv in sorted(v["results"].items())),
                                                          f(v["avg_r"]), v["with_r"])
          for k, v in sorted(s.get("outcomes", {}).items())] or ["  none yet (replays run after 4:05 PM New York)"]
    oc = s.get("odds_check")
    L += ["", "DID THE ODDS HOLD UP",
          "  %d replays with odds: Time Machine said target first %s%%, stop first %s%%; the day gave %s%% and %s%%."
          % (oc["n"], f(oc["predicted_target"], "%.1f"), f(oc["predicted_stop"], "%.1f"), f(oc["actual_target"], "%.1f"),
             f(oc["actual_stop"], "%.1f")) if oc else "  none yet"]
    sc = s.get("scans")
    L += ["", "SCANS",
          "  %d scans over %d days, %d failed. Average %s charts read, %s left out for gaps. %d breakouts, %d new signals."
          % (sc["scans"], sc["days"], sc["failed"], f(sc["avg_charts"], "%.1f"), f(sc["avg_gaps"], "%.1f"), sc["breakouts"],
             sc["new_signals"]) if sc else "  none logged yet"]
    L += ["", "BY RULE SET"]
    L += ["  %s%s: %d closed, %s, average R %s" % (g["rules"], " (since %s)" % g["first_seen"] if g["first_seen"] else "",
                                                  g["closed"], _usd(g["total"]), f(g["avg_r"])) for g in s["by_rules"]] or ["  none"]
    for h, v in sorted((s.get("rulesets") or {}).items(), key=lambda kv: kv[1].get("first_seen") or ""):
        L.append("    %s = %s" % (h, ", ".join("%s %s" % kv for kv in sorted((v.get("rules") or {}).items()))))
    L += ["", "SETTINGS CHANGES (newest first)"]
    L += ["  %s  %s" % (e["ts"], e["message"]) for e in s["settings_changes"]] or ["  none logged"]
    L += ["", "Reading this: a number from fewer than %d trades is noise. " % s["min_trades"] +
          ("Paper fills are simulated, so good paper results show the routine works, not that live results will match."
           if mode == "paper" else "Real fills include slippage the slip cannot see; compare the fill and stop lines above."),
          "Rule changes are logged in docs/CHANGES.md. The desk shows numbers; it is not advice."]
    return "\n".join(L)
