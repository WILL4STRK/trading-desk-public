"""Risk stops that look back further than one day.

The idea comes from CloddsBot's circuit breaker and safety manager (docs/CLODDSBOT.md): besides a
daily loss limit, stop when the week has gone badly, when the account is too far under its best point,
or after a run of losing trades. Three settings, each a percentage of the account or a count, and 0
turns one off:

- max_weekly_loss_pct: realised loss since Monday. Clears by itself the next week.
- max_drawdown_pct: how far the record is under its best point since the last reset, as a share of
  that best point (best point = the account now plus the drop).
- max_losing_streak: closed losers in a row since the last reset. An even trade ends the run.

The last two stay on until you press Reset stops: a bad run is the moment to read the record ("check
the log and make changes"), so the desk waits for you instead of carrying on after a timer.

These only stop NEW entries (engine.halt_reason). They never close a trade, never send anything and
never change a trade's size. Plain functions over journal rows; nothing here writes.
"""
import datetime

from . import settings as S

RESET_KEY = "stops_reset"       # journal kv: when you last pressed Reset stops


def week_start(date):
    d = datetime.date.fromisoformat(date[:10])
    return (d - datetime.timedelta(days=d.weekday())).isoformat()


def _closed(trades, since=None):
    rows = [t for t in trades if t.get("status") == "closed" and t.get("pnl") is not None and t.get("exit_at")]
    if since:
        rows = [t for t in rows if t["exit_at"] > since]
    return sorted(rows, key=lambda t: (t["exit_at"], t.get("id") or 0))


def status(st, trades, equity, today, reset_at=None):
    """Where each stop stands. tripped is the first stop that is on and reached, or None."""
    st = S.risk(st)
    equity = max(0.0, float(equity))
    since = _closed(trades, reset_at)

    wk = week_start(today)
    week_pnl = round(sum(t["pnl"] for t in _closed(trades) if wk <= t["exit_at"][:10] <= today), 2)
    week_lost = max(0.0, -week_pnl)
    week_limit = round(equity * float(st.get("max_weekly_loss_pct") or 0) / 100, 2)

    run = peak = 0.0
    for t in since:
        run += t["pnl"]
        peak = max(peak, run)
    drop = round(peak - run, 2)
    best = equity + drop
    drop_pct = round(100.0 * drop / best, 2) if best > 0 else 0.0
    dd_pct = float(st.get("max_drawdown_pct") or 0)
    dd_limit = round(best * dd_pct / 100, 2)

    streak = 0
    for t in reversed(since):
        if t["pnl"] >= 0:
            break
        streak += 1
    max_streak = int(st.get("max_losing_streak") or 0)

    items = [
        {"key": "weekly", "label": "Lost this week", "on": week_limit > 0, "used": week_lost, "limit": week_limit},
        {"key": "drawdown", "label": "Down from your best", "on": dd_pct > 0, "used": drop, "used_pct": drop_pct,
         "limit": dd_limit, "limit_pct": dd_pct},
        {"key": "streak", "label": "Losing trades in a row", "on": max_streak > 0, "used": streak, "limit": max_streak},
    ]
    tripped = None
    if max_streak and streak >= max_streak:
        tripped = {"key": "streak", "reason": "%d losing trade%s in a row, your limit. No new entries until you read "
                                              "the review and press Reset stops." % (streak, "" if streak == 1 else "s")}
    elif dd_pct and drop > 0 and drop_pct + 1e-9 >= dd_pct:
        tripped = {"key": "drawdown", "reason": "Down $%.2f (%.1f%%) from your best point, past your %g%% limit. No new "
                                                "entries until you read the review and press Reset stops."
                                                % (drop, drop_pct, dd_pct)}
    elif week_limit and week_lost + 1e-9 >= week_limit:
        tripped = {"key": "weekly", "reason": "Down $%.2f this week, at your weekly loss limit of $%.2f. No new entries "
                                              "until Monday." % (week_lost, week_limit)}
    return {"items": items, "tripped": tripped, "needs_reset": bool(tripped and tripped["key"] != "weekly"),
            "reset_at": reset_at}
