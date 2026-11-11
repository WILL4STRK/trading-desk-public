"""One candidate, start to finish, through the day-trader-skill tools:

    entry timer -> exit planner -> time machine -> budget -> risk -> ticket -> news

The result is a signal: the numbers, the time it is good until, and the
reasons if anything refused it. Nothing here places an order.
"""
import datetime
import math

from . import guards
from . import mode as M
from . import settings as S
from . import skills
from . import slipinfo


def workdir(date, symbol):
    return M.work_root(S.load()["mode"]) / date / symbol          # paper and live never share a folder


def _hhmm_add(hhmm, minutes):
    t = datetime.datetime.strptime(hhmm, "%H:%M") + datetime.timedelta(minutes=minutes)
    return t.strftime("%H:%M")


def _floor_cent(x):
    return math.floor(x * 100 + 1e-6) / 100


def write_account(wd, st, journal, now):
    """The account as the tools read it: config, portfolio and what was placed today."""
    today = now[:10]
    equity = journal.equity()
    cfg = S.skills_config(st, equity)
    skills.write_json(wd / "trader" / "config.json", cfg)
    positions = [{"ticker": t["symbol"], "shares": t["qty"],
                  "lots": [{"id": "%s-%d" % (t["symbol"], t["id"]), "date": t["date"], "shares": t["qty"],
                            "cost": round(t["qty"] * t["entry_price"], 2)}]}
                 for t in journal.open_trades()]
    skills.write_json(wd / "trader" / "portfolio.json",
                      {"cash": journal.settled_cash(today), "positions": positions})
    orders = []
    for t in journal.trades(today):
        orders.append({"at": t["entry_at"], "date": t["date"], "status": "placed", "kind": "entry",
                       "symbol": t["symbol"], "side": "buy", "qty": str(t["qty"]), "stop": t["stop"],
                       "max_loss": round((t["entry_price"] - t["stop"]) * t["qty"], 2)})
        if t["status"] == "closed":
            orders.append({"at": t["exit_at"], "date": t["exit_at"][:10], "status": "filled", "kind": "exit",
                           "symbol": t["symbol"], "side": "sell", "qty": str(t["qty"]), "price": t["exit_price"]})
    skills.write_json(wd / "trader" / "state" / "broker.json", {"orders": orders})
    return cfg


def edge_points(p_target, p_stop, target_r):
    """Time Machine edge: the target's share of decided replays minus the break-even share, in points."""
    decided = p_target + p_stop
    if decided <= 0:
        return None
    return round(100 * (p_target / decided - 1 / (1 + target_r)), 1)


def order_steps(sig):
    """How to enter this exact order at your broker, by hand. Most brokers call it a bracket or OCO order."""
    return [
        "At your broker, open an order ticket for %s." % sig["symbol"],
        "Choose a bracket order (a buy with a stop and a target attached, one cancels the other).",
        "The buy: quantity %d, order type LIMIT, price %.2f, good for the DAY." % (sig["qty"], sig["limit"]),
        "The stop: order type STOP, price %.2f. The target: LIMIT, price %.2f. Both good for the DAY."
        % (sig["stop"], sig["target"]),
        "Read the review screen: max loss about $%.2f. Then send it yourself." % sig["max_loss"],
        "Back here, press 'I placed it' and type your fill price.",
    ]


def evaluate(symbol, st, journal, source, hit=None):
    """Runs one symbol through the tools. Returns (status, payload) or (None, reason) when
    there is nothing to signal yet (the setup has not triggered)."""
    status, sig = _evaluate(symbol, st, journal, source, hit)
    if status is not None:
        slipinfo.finish(sig, st, journal, source.now())      # stamp, costs, rule card: display and record only
    return status, sig


def _evaluate(symbol, st, journal, source, hit=None):
    now = source.now()
    today = now[:10]
    wd = workdir(today, symbol)
    write_account(wd, st, journal, now)
    na = source.now_args()

    # 1. entry timer
    args = ["check", symbol, "--setup", "orb", "--or-minutes", st["or_minutes"], "--write", "--json"]
    if st["min_volume_x"] > 0:
        args += ["--min-volume-x", st["min_volume_x"]]
    r = skills.run("entry", args + na + source.chart_offline(symbol), wd)
    e = r.json
    if not isinstance(e, dict):
        return None, "entry timer: %s" % r.message
    if e.get("status") != "triggered":
        return None, "%s: %s" % (e.get("status"), e.get("reason") or "")
    trig = e["trigger"]
    age = e.get("minutes_since")
    sig = {
        "symbol": symbol, "setup": "orb", "date": today, "created": now,
        "trigger_time": trig["time"], "trigger_close_time": trig["close_time"],
        "trigger_price": trig["price"], "volume_x": trig.get("volume_x"),
        "opening_range": {"high": e["opening_range"]["high"], "low": e["opening_range"]["low"]},
        "prev_close": e.get("prev_close"), "reason": e.get("reason"),
        "valid_until": _hhmm_add(trig["close_time"], st["signal_valid_minutes"]),
        "problems": [], "warnings": [], "workdir": str(wd),
    }
    if age is not None and age > st["signal_valid_minutes"]:
        sig["problems"].append("It triggered at %s, %d minutes ago. Too late to chase." % (trig["time"], age))
        return "missed", sig

    # 2. exit planner
    r = skills.run("exit", ["plan", "--stop", st["stop_rule"], "--target", "%gR" % st["target_r"],
                            "--flat-by", st["flat_by"], "--write", "--json"] + na + source.chart_offline(symbol), wd)
    plan = r.json
    if r.rc != 0 or not isinstance(plan, dict) or "stop" not in plan:
        sig["problems"].append("Exit planner refused: %s" % r.message)
        return "blocked", sig
    risk_ps = plan["risk_per_share"]
    sig.update({"entry": plan["entry"], "stop": plan["stop"], "target": plan["target"],
                "stop_label": plan.get("stop_label"), "target_label": plan.get("target_label"),
                "risk_per_share": risk_ps, "flat_by": plan["flat_by"],
                "limit": _floor_cent(plan["entry"] + st["chase_r"] * risk_ps)})
    sig["warnings"] += plan.get("warnings") or []
    close = guards.stop_too_close(plan["entry"], risk_ps, st.get("min_stop_pct"))
    if close:
        sig["problems"].append(close)
        sig.update({"qty": 0, "cost": 0.0, "max_loss": 0.0, "reward": 0.0})
        return "blocked", sig

    # 3. time machine
    sig["odds"] = None
    if source.has_history(symbol):
        r = skills.run("timemachine", ["run", "--replays", st["replays"], "--until", plan["flat_by"],
                                       "--json", "--write"] + na + source.history_offline(symbol), wd, timeout=300)
        tm = r.json
        if isinstance(tm, dict) and "mc" in tm:
            mc, hist = tm["mc"], tm.get("history") or {}
            sig["odds"] = {
                "p_target": mc["p_target"], "p_stop": mc["p_stop"], "p_neither": mc["p_neither"],
                "p_green": mc["p_green"], "avg_r": mc["avg_R"], "replays": mc["replays"],
                "sessions": mc["pool_sessions"], "median_close": mc["median_close"],
                "p5_close": mc["p5_close"], "p95_close": mc["p95_close"], "until": mc["until"],
                "edge_points": edge_points(mc["p_target"], mc["p_stop"], plan["target_r"]),
                "verdict": tm.get("verdict"), "notes": tm.get("verdict_notes") or [],
                "history": {k: hist.get(k) for k in ("sessions", "triggered", "target", "stop", "close", "avg_R")},
            }
            sig["warnings"] += tm.get("warnings") or []
        else:
            sig["warnings"].append("Time Machine could not run: %s" % r.message)
    else:
        sig["warnings"].append("No 60-day history in the demo files for %s, so no odds." % symbol)

    # 4. budget: size at the limit ceiling, the worst price you would pay
    r = skills.run("budget", ["--limit", "%.2f" % sig["limit"], "--write", "--json"] + na, wd)
    b = r.json
    sizing = (b or {}).get("sizing") if isinstance(b, dict) else None
    if not sizing or sizing.get("refusal") or not (b.get("orders")):
        why = (sizing or {}).get("refusal") or r.message
        sig["problems"].append("Budget: %s" % why)
        sig.update({"qty": 0, "cost": 0.0, "max_loss": 0.0, "reward": 0.0})
        return "blocked", sig
    qty = int(sizing["qty"])
    sig.update({"qty": qty, "cost": round(qty * sig["limit"], 2), "max_loss": sizing["max_loss"],
                "reward": round(qty * (sig["target"] - sig["limit"]), 2),
                "reward_risk": sizing["reward_risk"], "bound_by": sizing.get("bound_by"),
                "sizing_limits": [{"name": l["name"], "shares": l["shares"], "how": l["how"], "binds": l.get("binds")}
                                  for l in sizing.get("limits", [])]})

    # 5. risk manager
    r = skills.run("risk", ["--json"] + na, wd)
    res = ((r.json or {}).get("results") or [None])[0] if isinstance(r.json, dict) else None
    if not res:
        sig["problems"].append("Risk check could not run: %s" % r.message)
        return "blocked", sig
    sig["risk"] = {"verdict": res["verdict"], "reasons": res.get("reasons") or [], "info": res.get("info") or []}
    sig["warnings"] += res.get("warnings") or []

    # 6. order ticket
    r = skills.run("broker", ["show", "--json"] + na, wd)
    t = r.json[0] if isinstance(r.json, list) and r.json else {}
    sig["ticket"] = {"plain": t.get("plain"), "ready": bool(t.get("ready")), "steps": order_steps(sig)}

    # 7. news
    sig["news"] = news(symbol, st, source, wd)
    if any("earnings" in (n.get("tags") or []) for n in sig["news"]):
        sig["warnings"].append("Earnings are in the headlines for %s. A day trade is flat before the close, "
                               "but the stock can move hard on any leak or guidance." % symbol)

    if res["verdict"] != "PASS":
        sig["problems"] += ["Risk: %s" % x for x in sig["risk"]["reasons"]]
        return "blocked", sig
    ep = (sig["odds"] or {}).get("edge_points")
    if sig["odds"] is None and not source.demo:
        sig["problems"].append("No Time Machine odds for this one, so the desk cannot grade it.")
        return "weak", sig
    if ep is not None and ep < st["min_edge_points"]:
        sig["problems"].append("Time Machine edge is %+.1f points, under your floor of %+.1f." % (ep, st["min_edge_points"]))
        return "weak", sig
    return "ready", sig


def news(symbol, st, source, wd, limit=5):
    args = ["--tickers", symbol, "--all", "--hours", st["news_hours"], "--no-moves", "--json"]
    if not st["sec_user_agent"]:
        args.append("--no-sec")
    r = skills.run("news", args + source.news_args(), wd, timeout=60)
    items = (r.json or {}).get("items") if isinstance(r.json, dict) else None
    out = []
    for it in (items or [])[:limit]:
        link = str(it.get("link") or "")
        out.append({"title": it.get("title"), "time": it.get("time"), "source": it.get("source"),
                    "kind": it.get("kind"), "tags": it.get("tags") or [],
                    "link": link if link.startswith("https://") else ""})
    return out
