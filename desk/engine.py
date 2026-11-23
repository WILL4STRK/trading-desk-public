"""The desk: scans, turns fresh setups into signals, watches open trades and
records what YOU did. It never places a real order: you place each one at your
broker by hand. In paper mode, auto paper may trade the desk's own pretend
practice account (desk/paper.py, desk/auto.py), which never touches a broker.
"""
import datetime
import math
import re
import threading
import time

from . import decide
from . import agent as AG
from . import auto as A
from . import breaking as BN
from . import benchmarks as BM
from . import breaker
from . import changes
from . import charts
from . import exports
from . import graph as G
from . import health
from . import mode as M
from . import notify
from . import outcome
from . import pipeline
from . import review as R
from . import screenshot
from . import settings as S
from . import shorts
from . import skills
from . import stamp
from . import watchlist as W
from .journal import Journal, JournalError
from .paths import FIXTURES, account_name, data_dir

MAX_EVALS_PER_SCAN = 8
COMPARE_RULES = ("or_minutes", "stop_rule", "target_r", "chase_r", "min_volume_x", "min_stop_pct",
                 "signal_valid_minutes", "no_entries_before", "no_entries_after", "flat_by")
COMPARE_PAUSE = 0.4
COMPARE_TRIES = 12       # passes (about one an hour) before a pick with no usable candles is given up
COMPARE_DAYS = 28        # older comparisons cannot be recovered from the requested one-month chart


class DeskError(ValueError):
    pass


def universe_args(out):
    """The Chart Scanner command that writes the list of every US stock. It takes no --quiet."""
    return ["universe", "--out", out]


def _minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _at_minutes(at):
    """'9:50 AM' -> minutes after midnight."""
    return _minutes(datetime.datetime.strptime(at.strip(), "%I:%M %p").strftime("%H:%M"))


class Desk:
    def __init__(self, journal, source):
        self.j, self.src = journal, source
        self.lock = threading.RLock()
        self._clock = (None, None)
        st = S.load()
        self._journals = {st["mode"]: journal}  # one journal per mode, opened on first use and kept open
        self.j.seed(M.seed_amount(st["mode"], st["starting_balance"]), self.src.now())

    # ---- clock -------------------------------------------------------
    def scan_dir(self, now):
        return data_dir() / "work" / now[:10] / "_scan"

    def clock(self, at=None):
        now = at or self.src.now()
        if self._clock[0] == now:
            return self._clock[1]
        r = skills.run("alerts", ["clock", "--json", "--now", now], self.scan_dir(now), timeout=30)
        c = r.json if isinstance(r.json, dict) else {"open": False, "reason": r.message, "date": now[:10]}
        self._clock = (now, c)
        return c

    def settle_date(self, now):
        """When money from a sale made now can be used again. Paper mode: at once, so auto paper can keep learning."""
        st = S.load()
        if st["account_type"] == "margin" or st["mode"] == "paper":
            return now[:10]
        c = self.clock(now[:10] + " 23:59")
        nxt = str(c.get("next_open") or "")[:10]
        if len(nxt) == 10 and nxt > now[:10]:
            return nxt
        d = datetime.date.fromisoformat(now[:10]) + datetime.timedelta(days=1)
        while d.weekday() >= 5:
            d += datetime.timedelta(days=1)
        return d.isoformat()

    # ---- can the desk look for entries right now? --------------------
    def spendable(self, st, now):
        lim = S.limits(st, self.j.equity())
        return round(max(0.0, min(self.j.buying_power(now[:10]) - lim["cash_buffer"], lim["max_position"])), 2)

    def halt_reason(self, st, now):
        if st.get("paused"):
            return "Paused by you. No new signals until you resume. Open trades keep their alerts."
        c = self.clock(now)
        if not c.get("open"):
            return "The market is closed (%s)." % (c.get("reason") or "closed")
        t = _minutes(now[11:16])
        st = S.for_day(st, c.get("closes"))
        if t < _minutes(st["no_entries_before"]):
            return "No entries before %s. The opening range is still forming." % st["no_entries_before"]
        if t >= _minutes(st["no_entries_after"]):
            return "No new entries this late in the day."
        day = self.j.day(now[:10])
        lim = S.limits(st, self.j.equity())
        if day["trades"] >= S.trades_cap(st):
            return "You have taken %d of %d trades today. Done for the day." % (day["trades"], S.trades_cap(st))
        if day["realised"] <= -lim["max_daily_loss"] < 0:
            return "Down $%.2f today, at your daily loss limit of $%.2f. Done for the day." % (-day["realised"], lim["max_daily_loss"])
        stop = self.stops(st, now)["tripped"]
        if stop:
            self._note_stop(stop, now)
            return stop["reason"]
        if st["mode"] == "live" and self.j.cash() <= 0:
            return "Live mode has no cash yet. Enter your real cash in the Account tab first."
        if self.spendable(st, now) < max(st["min_price"], 1):
            return "No settled cash to trade with until your last sale settles."
        return None

    # ---- risk stops over more than one day (desk/breaker.py) --------
    def stops(self, st=None, now=None):
        st, now = st or S.load(), now or self.src.now()
        return breaker.status(st, self.j.trades(limit=100000), self.j.equity(), now[:10], self.j.get(breaker.RESET_KEY))

    def _note_stop(self, stop, now):
        """Logs a trip once: per week for the weekly stop, per reset for the others."""
        when = breaker.week_start(now) if stop["key"] == "weekly" else (self.j.get(breaker.RESET_KEY) or "start")
        self.j.add_event("stops", stop["reason"], now, key="stop-%s-%s" % (stop["key"], when))

    def reset_stops(self):
        """You read the record and want the desk to carry on: the losing run and the drawdown count from now.
        The weekly stop is a calendar limit and is not reset."""
        with self.lock:
            now = self.src.now()
            self.j.put(breaker.RESET_KEY, now)
            self.j.add_event("stops", "Risk stops reset by you. The losing run and the drop from the best point "
                                      "count again from here.", now)
            return self.stops(S.load(), now)

    # ---- scanning ----------------------------------------------------
    def premarket(self):
        """The Chart Scanner's daily setups: the charts worth watching today."""
        st, now = S.load(), self.src.now()
        wd = self.scan_dir(now)
        args = ["daily", "--json", "--no-write", "--quiet", "--now", now]
        if self.src.demo:
            args += ["--offline", FIXTURES / "trader-scanner" / "daily-spark-2026-10-19.json"]
        else:
            args += ["--symbols", ",".join(st["watchlist"])]
        r = skills.run("scanner", args, wd, timeout=600)
        d = r.json if isinstance(r.json, dict) else {}
        out = {"date": now[:10], "at": now, "as_of": d.get("as_of"), "scanned": d.get("scanned"),
               "hits": [{"symbol": h["symbol"], "setups": h.get("setups"), "price": h.get("price"), "rsi": h.get("rsi")}
                        for h in d.get("hits", [])][:40],
               "error": None if d else r.message}
        self.j.put("premarket", out)
        return out

    def research_watchlist(self, force=False, apply=True):
        """9:00 AM New York: today's watchlist = your core list + the day's movers. With the watchlist
        agent on, Claude picks them from the formula's candidates and their headlines (desk/agent.py);
        otherwise the formula picks (desk/watchlist.py). Live data only. If every screener failed,
        yesterday's list stays. force: run even with the daily research off (python3 -m desk agent)."""
        st, now = S.load(), self.src.now()
        if self.src.demo or not (st["auto_watchlist"] or force):
            return None
        saved = self.j.get("watchlist_research") or {}
        if not force and saved.get("date") == now[:10] and saved.get("applied"):
            return saved                         # a restart must not choose or pay for the same morning again
        spend, before = self.spendable(st, now), list(st["watchlist"])
        if st["watchlist_agent"]:
            r = AG.research(st, spend, data_dir() / "work" / now[:10] / "_agent")
        else:
            r = dict(W.research(st, max_price=spend), by="formula", summary="")
        kept = not r["picks"] and bool(r["errors"])
        out = dict(r, date=now[:10], at=now, kept_old=kept, applied=apply and not kept)
        if not apply:
            return out
        key = "watchlist_comparison:" + now[:10]       # the morning's two lists, replayed after the close
        if self.j.get(key) is None:
            self.j.put(key, {"date": now[:10], "at": now, "by": r["by"], "errors": r["errors"],
                             "formula": r.get("formula_picks", r["picks"]), "claude": r.get("claude_picks"),
                             "rules": {k: st[k] for k in COMPARE_RULES}})
        if not kept:
            with self.lock:                 # additions, removals and news avoids made during research stay
                avoid = set(self.news_avoid(now))
                current = S.update(lambda cur: {"watchlist": [x for x in dict.fromkeys(
                    r["watchlist"] + [x for x in cur["watchlist"] if x not in before])
                    if x not in avoid and not (x in before and x not in cur["watchlist"])]})
                out["watchlist"] = current["watchlist"]
        self.j.put("watchlist_research", out)
        who = "Claude picked" if r["by"] == "claude" else "The formula picked"
        msg = ("Watchlist research failed, so yesterday's list stays: %s" % "; ".join(r["errors"][:2]) if kept else
               "Watchlist research: %d core tickers and %d proposed movers. %s %s." % (
                   len(r["core"]), len(r["picks"]), who,
                   ", ".join("%s (moves %.1f%% a day)" % (p["symbol"], p["atr_pct"]) for p in r["picks"]) or "none today"))
        self.j.add_event("watchlist", msg, now, key="watchlist-%s" % now[:10])
        return out

    def news_avoid(self, now=None):
        """Today's tickers breaking news said to avoid: no new entry in them today (scan and auto paper)."""
        return self.j.get("news_avoid:" + (now or self.src.now())[:10]) or []

    def breaking_news(self):
        """Market hours, every few minutes: acts on news that can move a stock or the market hard today
        (desk/breaking.py). Buy or short adds the stock to today's watchlist, so the next scan grades it with
        your usual rules; exit closes auto paper's trade and alerts you for yours; avoid drops it from today's
        list and blocks new entries in it today. Never trades live money. Live data only.
        Returns {calls, heavy, errors, added} or None when off."""
        st, now = S.load(), self.src.now()
        if self.src.demo or not st["breaking_news"]:
            return None
        held = {t["symbol"]: t.get("side") or "long" for t in self.j.open_trades()}
        wd = data_dir() / "news-watch"
        r = BN.check(st, st["watchlist"], held, wd)            # slow (feeds, Claude): outside the lock
        added = []
        with self.lock:
            st2, now2 = S.load(), self.src.now()
            stale = ("breaking news was turned off" if not st2["breaking_news"] else
                     "the mode changed" if st2["mode"] != st["mode"] else
                     "the day changed" if now2[:10] != now[:10] else
                     "the market closed" if r["calls"] and not self.clock(now2).get("open") else None)
            if stale:                                       # nothing acted on; the stories come back next time
                return dict(r, calls=[], added=[], errors=r["errors"] + ["%s while Claude was reading" % stale])
            for c in r["calls"]:
                added += self._news_call(c, now2)
            BN.acknowledge(wd, r.get("ack_ids") or [])
        if r["errors"]:                                     # kept for the review, once an hour at most
            self.j.add_event("news", "Breaking news check had problems: %s" % "; ".join(r["errors"])[:500],
                             now, key="news-errors-%s" % now[:13])
        return dict(r, added=added)

    def _news_call(self, c, now):
        """Applies one breaking-news call (under self.lock). Returns the tickers it added to the watchlist."""
        sym, act = c["symbol"], c["action"]
        key = "news-%s-%s-%s-%s" % (now[:10], sym, act, c.get("item_id") or "")
        if self.j.has_event(key):                           # once per story, stock and action
            return []
        head = "Breaking news on %s: %s (%s). Claude says %s: %s" % (sym, c["title"], c["source"], act, c["why"])
        st, added = S.load(), []
        if act in ("buy", "short") and (sym in st["blocked"] or sym in self.news_avoid(now)):
            did = "It is on your blocked list or today's avoid list, so nothing was done."
        elif act in ("buy", "short"):
            if sym not in st["watchlist"]:
                S.update(lambda cur: {"watchlist": list(dict.fromkeys(cur["watchlist"] + [sym]))})
                added.append(sym)
            did = ("Added to today's watchlist: the next scan grades it with your usual rules."
                   if added else "Already on your watchlist: the next scan grades it.")
            if act == "short" and st["directions"] == "long":
                did += " Shorts are off (Settings > Directions), so only a breakout buy can come of it."
        elif act == "exit":
            n = self.auto.news_exit(sym, "breaking news") if st["mode"] == "paper" else 0
            mine = any(t["symbol"] == sym and t.get("note") != A.AUTO for t in self.j.open_trades())
            did = " ".join(x for x in ("Auto paper is closing its trade." if n else "",
                                       "Close your trade at your broker, then record the exit here." if mine else "") if x) \
                or "Nothing is open in it any more."
        else:                                               # avoid
            day = "news_avoid:" + now[:10]
            self.j.put(day, sorted(set(self.j.get(day) or []) | {sym}))
            S.update(lambda cur: {"watchlist": [x for x in cur["watchlist"] if x != sym]})
            if st["mode"] == "paper":
                self.auto.news_exit(sym, "breaking news: avoid", close_open=False)
            did = ("Dropped from today's watchlist and no new entry in it today "
                   "(it comes back with tomorrow's research if it is a core ticker).")
        if self.j.add_event("news", "%s %s" % (head, did), now, sym, key=key):
            notify.send_news(c, did, st, st["mode"], self.src.demo)
        return added

    def _universe_due(self, st, now):
        if self.src.demo or not st["scan_universe"]:
            return False
        last = self.j.get("universe_scan_at")
        if not last or last[:10] != now[:10]:
            return True
        return _minutes(now[11:16]) - _minutes(last[11:16]) >= st["universe_scan_every_min"]

    def scan(self):
        """One pass, then a note of whether it worked (desk/health.py counts failures in a row)."""
        summary = self._scan()
        health.note_scan(self.j, summary)
        self._log_scan(summary)
        return summary

    def _log_scan(self, summary):
        """Every scan that ran is kept for the review; a halt is kept once, when it starts."""
        halt = summary.get("halt")
        if halt and halt == getattr(self, "_logged_halt", None):
            return
        self._logged_halt = halt
        self.j.add_scan(summary)

    def health(self, st=None, now=None, halt=False):
        st, now = st or S.load(), now or self.src.now()
        return health.check(now, self.clock(now), st, self.j.get("last_scan"), self.j.get("scan_fail_streak", 0),
                            self.halt_reason(st, now) if halt is False else halt, self.src.demo, self.j.path,
                            skills.TOOLS)

    def _scan(self):
        """One pass. Longs: opening-range breakouts from the Chart Scanner skill. Shorts (when switched on):
        opening-range breakdowns from each stock's own candles. Then one row per stock saying long, short,
        both or nothing, and why (desk/decide.py)."""
        with self.lock:
            st, now = S.load(), self.src.now()
            st = dict(st, blocked=st["blocked"] + self.news_avoid(now))   # today's breaking-news avoids count as blocked
            st = S.for_day(st, self.clock(now).get("closes"))            # early close: out by 12:55, not 15:55
            summary = {"at": now, "scanned": 0, "hits": 0, "fresh": 0, "evaluated": 0, "too_expensive": 0,
                       "stale": 0, "already": 0, "new_signals": 0, "halt": self.halt_reason(st, now), "error": None,
                       "directions": st["directions"], "short_hits": 0, "short_new": 0}
            if summary["halt"]:
                self.j.put("last_scan", summary)
                return summary
            # Practice-only shorts are looked for in paper mode only; in live mode they would sit next to real slips.
            practice = shorts.status(st, self.j.equity())["paper_only"]
            shorts_on = st["directions"] != "long" and not (practice and st["mode"] == "live")
            summary["shorts_skipped"] = None if shorts_on or st["directions"] == "long" else \
                "Short signals are practice only on this account, so the desk looks for them in paper mode only."
            long_notes = self._scan_longs(st, now, summary) if st["directions"] != "short" else {}
            short_notes = self._scan_shorts(st, now, summary) if shorts_on else {}
            if long_notes is not None:
                symbols = list(dict.fromkeys(list(long_notes) + list(short_notes)))
                off = None if shorts_on else decide.note(reason=decide.OFF if st["directions"] == "long"
                                                         else "practice only: looked for in paper mode only")
                self.j.add_decisions([decide.row(sym, long_notes.get(sym) if st["directions"] != "short" else None,
                                                 short_notes.get(sym) if shorts_on else off)
                                      for sym in symbols], now)
            self.j.put("last_scan", summary)
            return summary

    def _scan_longs(self, st, now, summary):
        """Opening-range breakouts so far, then the full pipeline on the fresh ones. Returns {symbol: note},
        or None when the scanner itself failed (summary["error"] says why)."""
        wd = self.scan_dir(now)
        args = ["orb", "--or-minutes", st["or_minutes"], "--min-price", st["min_price"],
                "--json", "--no-write", "--quiet"] + self.src.now_args()
        if self.src.demo:
            args += self.src.scan_offline()
        elif self._universe_due(st, now):
            uni = wd.parent.parent / "universe.txt"
            if not uni.exists() or (datetime.date.today().toordinal() - datetime.date.fromtimestamp(uni.stat().st_mtime).toordinal()) >= 7:
                skills.run("scanner", universe_args(uni), wd, timeout=120)
            args += ["--universe", uni]
            self.j.put("universe_scan_at", now)
        else:
            args += ["--symbols", ",".join(st["watchlist"])]
        r = skills.run("scanner", args, wd, timeout=900)
        d = r.json if isinstance(r.json, dict) else None
        if d is None:
            summary["error"] = "Scanner: %s" % r.message
            return None
        hits = d.get("hits", [])
        counts = d.get("counts") if isinstance(d.get("counts"), dict) else {}
        # unusable: charts the scanner left out because a 5-minute candle is missing from the free data
        summary.update({"scanned": d.get("scanned", 0), "hits": len(hits), "failed": d.get("failed", 0),
                        "unusable": int(counts.get("unusable") or 0)})
        fate = {}       # what happened to each breakout, for the scan log (summary["detail"])
        notes = {sym: decide.note(reason="no close above the range high, or the scanner left the chart out")
                 for sym in st["watchlist"]}
        now_min, spend = _minutes(now[11:16]), self.spendable(st, now)
        fresh = []
        for h in hits:
            sym = h["symbol"]
            if sym in st["blocked"]:
                notes[sym] = decide.note(reason="on your blocked list")
                fate[sym] = "blocked list"
            elif self.j.has_signal(now[:10], sym, "orb"):
                summary["already"] += 1
                notes[sym] = decide.note(reason="already signalled today")
                fate[sym] = "signal already made"
            elif now_min - _at_minutes(h["at"]) > st["signal_valid_minutes"]:
                summary["stale"] += 1
                notes[sym] = decide.note(reason="breakout too old to chase")
                fate[sym] = "too old"
            elif h["price"] > spend:
                summary["too_expensive"] += 1
                notes[sym] = decide.note(reason="one share costs more than you can spend")
                fate[sym] = "too expensive"
            else:
                fresh.append(h)
                fate[sym] = "waiting: only %d graded a scan" % MAX_EVALS_PER_SCAN
        summary["fresh"] = len(fresh)
        fresh.sort(key=lambda h: (-_at_minutes(h["at"]), -h.get("pct_over", 0)))
        for h in fresh[MAX_EVALS_PER_SCAN:]:
            notes[h["symbol"]] = decide.note(reason="not graded: more than %d fresh breakouts this scan" % MAX_EVALS_PER_SCAN)
        for h in fresh[:MAX_EVALS_PER_SCAN]:
            status, sig = pipeline.evaluate(h["symbol"], st, self.j, self.src, hit=h)
            summary["evaluated"] += 1
            fate[h["symbol"]] = status or "no trigger: %s" % str(sig)[:120]
            if status is None:
                notes[h["symbol"]] = decide.note(reason=str(sig)[:200])
                continue
            sid = self.j.add_signal(now[:10], h["symbol"], "orb", status, sig, now)
            notes[h["symbol"]] = decide.note(signal=sid, status=status)
            if sid and status == "ready":
                summary["new_signals"] += 1
                if self.j.add_event("signal", "%s: buy %d at %.2f or lower, stop %.2f, target %.2f. Good until %s."
                                    % (h["symbol"], sig["qty"], sig["limit"], sig["stop"], sig["target"], sig["valid_until"]),
                                    now, h["symbol"], key="signal-%d" % sid):
                    notify.send_signal(sig, st, st["mode"], self.src.demo)   # once per signal, in the background
        summary["detail"] = [{"symbol": h["symbol"], "at": h["at"], "price": h["price"], "fate": fate.get(h["symbol"])}
                             for h in hits]
        return notes

    def _scan_shorts(self, st, now, summary):
        """Opening-range breakdowns (docs/SHORTING.md). The Chart Scanner skill only finds breakouts,
        so the desk reads each stock's 5-minute candles itself. Read-only, like every scan.
        Returns {symbol: note} for every stock it looked at."""
        now_min, spend = _minutes(now[11:16]), self.spendable(st, now)
        if self.src.demo:
            symbols = sorted(p.stem for p in self.src.chart_offline("")[1].glob("*.json"))
        else:
            symbols = st["watchlist"]
        notes, fresh = {}, []
        for sym in symbols:
            if sym in st["blocked"]:
                notes[sym] = decide.note(reason="on your blocked list")
                continue
            if self.j.has_signal(now[:10], sym, shorts.SETUP):
                notes[sym] = decide.note(reason="already signalled today")
                continue
            try:
                chart = charts.intraday(self.src, sym)
            except charts.ChartError as e:
                notes[sym] = decide.note(reason="no candles: %s" % str(e)[:120])
                continue
            finally:
                if not self.src.demo:
                    time.sleep(0.2)
            _, trig, why = shorts.breakdown(charts.session_bars(chart, now), st["or_minutes"],
                                            st["no_entries_after"], st["min_volume_x"])
            if not trig:
                notes[sym] = decide.note(reason=why if not why.startswith("waiting") else "no close below the range low")
                continue
            summary["short_hits"] += 1
            if now_min - _minutes(trig["close_time"]) > st["signal_valid_minutes"]:
                notes[sym] = decide.note(reason="breakdown too old to chase")
                continue
            if trig["price"] < st["min_price"]:
                notes[sym] = decide.note(reason="price under your lowest share price")
                continue
            if trig["price"] > spend:
                notes[sym] = decide.note(reason="one share costs more than you can set aside")
                continue
            fresh.append((sym, chart, trig))
        fresh.sort(key=lambda f: (-_minutes(f[2]["close_time"]), -f[2]["pct_under"]))
        for sym, _, _ in fresh[MAX_EVALS_PER_SCAN:]:
            notes[sym] = decide.note(reason="not graded: more than %d fresh breakdowns this scan" % MAX_EVALS_PER_SCAN)
        for sym, chart, _ in fresh[:MAX_EVALS_PER_SCAN]:
            status, sig = pipeline.evaluate_short(sym, st, self.j, self.src, chart=chart)
            if status is None:
                notes[sym] = decide.note(reason=str(sig)[:200])
                continue
            sid = self.j.add_signal(now[:10], sym, shorts.SETUP, status, sig, now)
            notes[sym] = decide.note(signal=sid, status=status)
            if sid and status == "ready":
                summary["short_new"] += 1
                summary["new_signals"] += 1
                if self.j.add_event("signal", "SHORT%s %s: sell short %d at %.2f or higher, stop %.2f, target %.2f. Good until %s."
                                    % (" (practice only)" if sig["paper_only"] else "", sym, sig["qty"], sig["limit"],
                                       sig["stop"], sig["target"], sig["valid_until"]), now, sym, key="signal-%d" % sid):
                    notify.send_signal(sig, st, st["mode"], self.src.demo)
        return notes

    # ---- watching ----------------------------------------------------
    def monitor(self):
        """Expires old signals and rings the stop, the target and flat-by-close on open trades."""
        with self.lock:
            now = self.src.now()
            now_min = _minutes(now[11:16])
            for s in self.j.signals(statuses=("ready",)):
                vu = s["payload"].get("valid_until")
                if s["date"] < now[:10] or (vu and now_min > _minutes(vu)):
                    self.j.set_signal_status(s["id"], "expired", expect=("ready",))
            quotes = self.j.get("quotes", {})
            for t in self.j.open_trades():
                short = t.get("side") == "short"
                wd = pipeline.workdir(t["date"], t["symbol"], t.get("side") or "long")
                r = skills.run("alerts", ["check", "--json", "--now", now] + self.src.quotes_offline(), wd, timeout=60)
                d = r.json if isinstance(r.json, dict) else {}
                for sym, px in (d.get("quotes") or {}).items():
                    quotes[sym] = {"price": round(float(px), 2), "at": now}
                for ring in d.get("rings", []):
                    rid = str(ring.get("id", ""))
                    kind = "stop" if rid.endswith("stop") else "target" if rid.endswith("target") else "flat"
                    msg = {"stop": "%s touched your stop (%.2f). Check your broker: your stop order should have sold. Then record the exit here.",
                           "target": "%s reached your target (%.2f). Check your broker: your limit order should have sold. Then record the exit here.",
                           "flat": "%s is still open and it is time to be flat. Close it at your broker now, then record the exit here."}[kind]
                    if short:
                        msg = {"stop": "Short %s rose to your stop (%.2f). Check your broker: your buy stop should have covered it. Then record the exit here.",
                               "target": "Short %s fell to your target (%.2f). Check your broker: your buy limit should have covered it. Then record the exit here.",
                               "flat": "Short %s is still open and it is time to be flat. Buy to cover at your broker now, then record the exit here."}[kind]
                    msg = msg % ((t["symbol"], t["stop"]) if kind == "stop" else (t["symbol"], t["target"]) if kind == "target" else (t["symbol"],))
                    if t.get("note") != A.AUTO:             # auto paper sells its own trades: nothing for you to do
                        self.j.add_event(kind, msg, now, t["symbol"], key="trade-%d-%s" % (t["id"], kind))
                if t["date"] < now[:10]:
                    self.j.add_event("flat", "%s%s was opened on %s and is still marked open. Day trades are flat by the close: "
                                     "close it or record the exit." % ("Short " if short else "", t["symbol"], t["date"]), now, t["symbol"],
                                     key="trade-%d-overnight-%s" % (t["id"], now[:10]))
            self.j.put("quotes", quotes)
            if now[11:16] >= "16:00":           # after the close, once: what the day was
                text = R.day_summary(now[:10], self.j.trades(now[:10]), self.j.signals(date=now[:10]))
                if text:
                    self.j.add_event("summary", text, now, key="summary-%s" % now[:10])
        if now[11:16] >= "16:05" and self.j.get("outcomes_done") != now[:10]:
            self.record_outcomes(now[:10])           # outside the lock: it reads one chart a signal
        elif not self.src.demo and self.j.get("outcomes_retry") != now[:13]:
            self.j.put("outcomes_retry", now[:13])   # at most once an hour: replays that got no candles (offline, asleep)
            for d in self.j.outcome_gaps():
                self.record_outcomes(d, only_gaps=True)
            self.compare_pending()                   # charts that did not answer after the close, any day

    def record_outcomes(self, date, only_gaps=False):
        """After the close: how every signal of the day would have done with its own numbers (desk/outcome.py).
        only_gaps: replay again just the signals that got "no data" before."""
        now, done, got = self.src.now(), 0, {}
        gaps = self.j.outcome_gaps(date) if only_gaps else None
        for s in self.j.signals(date=date, limit=1000):
            if gaps is not None and s["id"] not in gaps:
                continue
            p = s["payload"]
            try:
                if s["symbol"] not in got:           # a month of 5-minute bars, so a missed day can be replayed later
                    got[s["symbol"]] = charts.intraday(self.src, s["symbol"]) if self.src.demo \
                        else charts.fetch(s["symbol"], "1mo", "5m")
                bars = charts.session_bars(got[s["symbol"]], date + " 16:00")
            except charts.ChartError as e:
                o = {"result": "no data", "entry": None, "exit": None, "exit_time": None, "r": None, "note": str(e)[:200]}
            else:
                o = outcome.replay(p, bars)
            self.j.set_outcome(s["id"], date, s["symbol"], s["status"], o, now)
            done += 1
        self.compare_watchlists(date, got)
        self.j.put("outcomes_done", date)
        if done:
            self.j.add_event("summary", "Replayed %d of today's signals against the day's candles for the review." % done,
                             now, key="outcomes-%s" % date)
        return done

    def compare_pending(self):
        """Every kept morning comparison still missing results, from days whose close has been replayed."""
        today, done = self.src.now()[:10], self.j.get("outcomes_done")
        for k in self.j.get_prefix("watchlist_comparison:"):
            date = k.split(":", 1)[1]
            if date < today or date == done:
                self.compare_watchlists(date)

    def compare_watchlists(self, date, got=None):
        """After the close: the morning's formula picks and Claude picks, each replayed long and short with the
        same saved rules (outcome.watchlist_replay), stored as watchlist_results:<date>. Shadow only: no signal,
        fill or order. A symbol whose candles are missing or stop before the flat-by time is tried again on a
        later pass, COMPARE_TRIES passes at most (watchlist_retry:<date>), then kept as given up ("final").
        Returns how many it tried."""
        cmp_ = self.j.get("watchlist_comparison:" + date)
        if not cmp_ or self.src.demo:
            return 0
        key, got = "watchlist_results:" + date, got if got is not None else {}
        res = dict(self.j.get(key) or {})

        def finished(v):
            return isinstance(v, dict) and (v.get("final") or ("error" not in v and all(
                isinstance(v.get(side), dict) and v[side].get("result") not in (None, outcome.NO_DATA, outcome.OPEN)
                for side in ("long", "short"))))
        todo = sorted(s for s in {p["symbol"] for g in ("formula", "claude") for p in (cmp_.get(g) or [])}
                      if not finished(res.get(s)))
        if not todo:
            return 0
        tries = int(self.j.get("watchlist_retry:" + date) or 0) + 1
        age = (datetime.date.fromisoformat(self.src.now()[:10]) - datetime.date.fromisoformat(date)).days
        if age > COMPARE_DAYS:
            res.update({sym: {"error": "The comparison is outside the chart history window.", "final": True}
                        for sym in todo})
            self.j.put(key, res)
            return 0
        last = tries >= COMPARE_TRIES
        rules = S.for_day(cmp_["rules"], self.clock(date + " 10:00").get("closes"))
        flat = min(_minutes(rules["flat_by"]), charts.CLOSE_MIN - 5)
        for i, sym in enumerate(todo):
            try:
                if sym not in got:
                    if i:
                        time.sleep(COMPARE_PAUSE)
                    got[sym] = charts.fetch(sym, "1mo", "5m")
                bars = charts.session_bars(got[sym], date + " 16:00")
                if not bars or bars[-1]["minute"] < flat:
                    raise charts.ChartError("the day's candles stop before %s" % rules["flat_by"])
                required = set(range(charts.OPEN_MIN, flat + 1, 5))
                if not required <= {b["minute"] for b in bars}:
                    raise charts.ChartError("the day's candles have missing five-minute slots")
                res[sym] = {side: outcome.watchlist_replay(bars, rules, side) for side in ("long", "short")}
            except charts.ChartError as e:
                res[sym] = {"error": str(e)[:200], "final": last}
        self.j.put(key, res)
        self.j.put("watchlist_retry:" + date, tries)
        return len(todo)

    # ---- what you did ------------------------------------------------
    @staticmethod
    def money(typed):
        """A number a person typed, as a broker shows it: "$1,234.56", " 400 ", 14.4.
        Raises ValueError for anything else (empty, words, nan, inf)."""
        if isinstance(typed, bool) or typed is None:
            raise ValueError("not a number")
        if isinstance(typed, (int, float)):
            value = float(typed)
        else:
            text = str(typed).strip().replace("$", "", 1).replace(",", "").strip()
            if not re.fullmatch(r"\d+(\.\d+)?|\.\d+", text):
                raise ValueError("not a number")
            value = float(text)
        if not math.isfinite(value):
            raise ValueError("not a number")
        return value

    def take(self, signal_id, qty, price):
        """You placed the order at your broker and it filled: record it."""
        with self.lock:
            st, now = S.load(), self.src.now()
            s = self.j.signal(signal_id)
            if not s:
                raise DeskError("No such signal.")
            if self.auto.claimed(s["id"]):
                raise DeskError("Auto paper is trading this one in the practice account. It records the fill itself.")
            if s["status"] != "ready":
                raise DeskError("That signal is %s, not ready. Do not chase it." % s["status"])
            p = s["payload"]
            halt = self.halt_reason(st, now)
            if halt:
                raise DeskError("%s This signal is off." % halt)
            try:
                qty, price = int(qty), round(self.money(price), 2)
            except (TypeError, ValueError):
                raise DeskError("Shares and price must be numbers.")
            if qty < 1 or qty > p["qty"]:
                raise DeskError("The desk sized this at %d shares. You can take fewer, never more." % p["qty"])
            if p.get("side") == "short":
                return self._take_short(st, now, signal_id, p, qty, price)
            if price <= p["stop"]:
                raise DeskError("A fill at %.2f is at or under the stop (%.2f). Check the number." % (price, p["stop"]))
            if price >= p["target"]:
                raise DeskError("A fill at %.2f is at or over the target (%.2f). Check the number." % (price, p["target"]))
            lim = S.limits(st, self.j.equity())
            loss = round(qty * (price - p["stop"]), 2)
            if loss > lim["max_loss_per_trade"] + 0.005:
                raise DeskError("At %.2f, %d shares risk $%.2f to the stop. Your limit is $%.2f. If it really filled there, "
                                "sell down to fit, then record what you kept." % (price, qty, loss, lim["max_loss_per_trade"]))
            try:
                tid = self.j.open_trade(p["symbol"], qty, price, p["stop"], p["target"], now, signal_id, p.get("flat_by"))
            except JournalError as e:
                raise DeskError(str(e))
            skills.run("alerts", ["add", "--from-plan"], p["workdir"], timeout=30)
            self.j.add_event("fill", "Recorded: bought %d %s at %.2f. Stop %.2f, target %.2f, flat by %s."
                             % (qty, p["symbol"], price, p["stop"], p["target"], p.get("flat_by")), now, p["symbol"])
            return tid

    def _take_short(self, st, now, signal_id, p, qty, price):
        """You sold short by hand and it filled: record it. The mirror of a long fill."""
        if st["directions"] == "long":
            raise DeskError("Short signals are switched off in Settings. This signal is off.")
        if price >= p["stop"]:
            raise DeskError("A short fill at %.2f is at or over the stop (%.2f). Check the number." % (price, p["stop"]))
        if price <= p["target"]:
            raise DeskError("A short fill at %.2f is at or under the target (%.2f). Check the number." % (price, p["target"]))
        lim = S.limits(st, self.j.equity())
        loss = round(qty * (p["stop"] - price), 2)
        if loss > lim["max_loss_per_trade"] + 0.005:
            raise DeskError("At %.2f, %d shares risk $%.2f to the stop. Your limit is $%.2f. If it really filled there, "
                            "buy back down to fit, then record what you kept." % (price, qty, loss, lim["max_loss_per_trade"]))
        practice = shorts.status(st, self.j.equity())["paper_only"]
        if practice and st["mode"] == "live":
            # A practice-only short can be shown in live mode but never recorded as real money.
            raise DeskError("This account cannot short for real (%s) The live record only holds real trades. "
                            "Switch to paper mode to practise shorts." % " ".join(shorts.status(st, self.j.equity())["why"]))
        try:
            tid = self.j.open_trade(p["symbol"], qty, price, p["stop"], p["target"], now, signal_id, p.get("flat_by"),
                                    note="practice short" if practice else "", side="short")
        except JournalError as e:
            raise DeskError(str(e))
        for args in shorts.alert_rules(p):
            skills.run("alerts", args, p["workdir"], timeout=30)
        self.j.add_event("fill", "Recorded%s: sold short %d %s at %.2f. Buy stop %.2f, target %.2f, flat by %s."
                         % (" (practice)" if practice else "", qty, p["symbol"], price, p["stop"], p["target"], p.get("flat_by")),
                         now, p["symbol"])
        return tid

    PAPER_STOP, PAPER_TARGET = 0.02, 0.04     # a paper buy's bracket: 2% under, 4% over (2:1)

    def paper_buy(self, symbol, qty=None):
        """The chart's Buy button: a pretend buy of any ticker at its last price, in paper mode only.
        It is recorded like a real fill (cash, open trades, P&L), sized to the same limits. No order is sent."""
        sym = str(symbol or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{1,5}", sym):
            raise DeskError("Type a ticker like AAPL.")
        try:
            ch = charts.intraday(self.src, sym)
            bars = charts.session_bars(ch, self.src.now()) or ch["bars"]    # finished bars: the demo's clock is frozen
        except charts.ChartError as e:
            raise DeskError(str(e))
        if not bars:
            raise DeskError("No price for %s right now." % sym)
        price = round(float(bars[-1]["c"]), 2)
        with self.lock:
            st, now = S.load(), self.src.now()
            if st["mode"] != "paper":
                raise DeskError("Buy here is practice only. Switch to paper mode: the live record only holds real trades.")
            halt = self.halt_reason(st, now)
            if halt:
                raise DeskError(halt)
            stop, target = round(price * (1 - self.PAPER_STOP), 2), round(price * (1 + self.PAPER_TARGET), 2)
            lim = S.limits(st, self.j.equity())
            most = min(int(lim["max_loss_per_trade"] // max(price - stop, 0.01)), int(self.spendable(st, now) // price))
            if most < 1:
                raise DeskError("One share of %s at %.2f does not fit your limits (max loss $%.2f, can spend $%.2f)."
                                % (sym, price, lim["max_loss_per_trade"], self.spendable(st, now)))
            try:
                qty = most if qty in (None, "") else int(qty)
            except (TypeError, ValueError):
                raise DeskError("Shares must be a whole number.")
            if not 1 <= qty <= most:
                raise DeskError("Your limits allow 1 to %d shares of %s at %.2f." % (most, sym, price))
            try:
                tid = self.j.open_trade(sym, qty, price, stop, target, now, note="paper buy")
            except JournalError as e:
                raise DeskError(str(e))
            self.j.add_event("fill", "Paper buy: %d %s at %.2f. Stop %.2f, target %.2f. Practice only, no order was sent."
                             % (qty, sym, price, stop, target), now, sym)
            return {"trade_id": tid, "symbol": sym, "qty": qty, "price": price, "stop": stop, "target": target}

    def skip(self, signal_id):
        with self.lock:
            self.auto.release(signal_id, "you skipped it")
            if not self.j.set_signal_status(signal_id, "skipped", expect=("ready", "weak", "blocked")):
                raise DeskError("That signal cannot be skipped any more.")

    def exit(self, trade_id, price, reason):
        """You are out (the stop, the target, or you closed it): record it."""
        with self.lock:
            now = self.src.now()
            try:
                price = round(self.money(price), 2)
                pnl = self.j.close_trade(int(trade_id), price, reason, now, self.settle_date(now))
            except (TypeError, ValueError) as e:
                raise DeskError(str(e) if isinstance(e, JournalError) else "The exit price must be a number.")
            t = self.j.trade(int(trade_id))
            self.j.add_event("exit", "Recorded: %s %d %s at %.2f. %s $%.2f." %
                             ("bought to cover" if t.get("side") == "short" else "sold", t["qty"], t["symbol"], price,
                              "Made" if pnl >= 0 else "Lost", abs(pnl)), now, t["symbol"])
            return pnl

    def change_settings(self, wanted):
        """Saves settings and writes each change to the event log, old value and new."""
        with self.lock:
            if not isinstance(wanted, dict):
                raise DeskError("Settings must be a set of names and values.")
            for k in changes.RESERVED:
                if k in wanted:
                    raise DeskError("%s has its own switch. It cannot be changed from the settings form." % k)
            old = S.load()
            new = S.update(wanted)
            now = self.src.now()
            for key, sentence in changes.diff(old, new):
                self.j.add_event("settings", sentence, now)
            return new

    def test_alert(self):
        """Settings > Send test alert: one sample push to your phone, now."""
        try:
            notify.send_test(S.load(), self.src.demo)
        except ValueError as e:
            raise DeskError(str(e))

    def test_meme_alert(self):
        """Settings > Send test meme alert: one sample memecoin push to the memecoin topic, now."""
        try:
            notify.send_meme_test(S.load(), self.src.demo)
        except ValueError as e:
            raise DeskError(str(e))

    def set_mode(self, new, typed=None):
        """Paper or live. Each has its own journal, so this swaps the whole record."""
        with self.lock:
            if new not in M.MODES:
                raise DeskError("Mode must be paper or live.")
            if new == "live" and account_name():
                raise DeskError("Extra paper accounts are practice only. Switch to live on the main desk.")
            st, now = S.load(), self.src.now()
            if new == st["mode"]:
                return new
            if not M.confirmed(new, typed):
                raise DeskError("To switch to live, type %s in the box. Live means the trades you record are real money." % M.CONFIRM_WORD)
            if self.j.open_trades():
                raise DeskError("You have an open %s trade. Record its exit before you switch." % st["mode"])
            if st["mode"] == "paper":
                self.auto.cancel_working("switching to live")   # a waiting practice buy must not fill unrecorded
            other = self._journals.get(new)
            if other is None:
                other = self._journals[new] = Journal(M.journal_path(new))
            other.seed(M.seed_amount(new, st["starting_balance"]), now)
            for key in M.CARRY:
                if other.get(key) is None and self.j.get(key) is not None:
                    other.put(key, self.j.get(key))
            self.j.add_event("mode", "Switched from %s to %s. This %s record is kept as it is." % (st["mode"], new, st["mode"]), now)
            S.update({"mode": new})
            self.j = other
            self.j.add_event("mode", "Now in %s mode." % new.upper() if new == "live" else "Now in paper mode.", now)
            return new

    def cash_move(self, kind, amount):
        now = self.src.now()
        try:
            amount = self.money(amount)
            if kind == "deposit":
                self.j.deposit(amount, now, "deposit")
            elif kind == "withdraw":
                self.j.withdraw(amount, now, "withdrawal")
            elif kind == "set":
                self.j.set_balance(amount, now)
            else:
                raise DeskError("Unknown cash action.")
        except (TypeError, ValueError) as e:
            raise DeskError(str(e) if isinstance(e, (JournalError, DeskError)) else "The amount must be a number.")

    # ---- screenshots of your broker's screen (docs/ACCOUNT-TAB.md) ----
    def read_screenshot(self, kind, image, trade_id=None):
        """Saves and reads a broker screenshot. Only suggests numbers; nothing is recorded."""
        if kind not in screenshot.KINDS:
            raise DeskError("Unknown screenshot type.")
        trade = None
        if kind == "trade":
            try:
                trade = self.j.trade(int(trade_id))
            except (TypeError, ValueError):
                trade = None
            if not trade or trade.get("status") != "open":
                raise DeskError("Pick an open trade first.")
        now = self.src.now()
        try:
            path = screenshot.save_image(image, kind, data_dir(), now)
            lines = screenshot.ocr(path)
        except screenshot.ScreenshotError as e:
            raise DeskError(str(e))
        out = {"kind": kind, "image": path.relative_to(data_dir()).as_posix(), "text": "\n".join(l["text"] for l in lines[:80]),
               "suggest": None}
        if kind == "balance":
            found = screenshot.read_balance(lines)
            out["found"] = found
            cash = next((f for f in found if f["field"] == "cash"), None)
            if cash:
                out["suggest"] = cash["value"]
                out["message"] = "Found %s: $%s. Check it, then press Save." % (cash["label"], format(cash["value"], ",.2f"))
            elif found:
                out["message"] = "No cash line found, only %s. Type your cash in." % ", ".join(f["label"] for f in found)
            else:
                out["message"] = "No balance found in that screenshot. Type your cash in instead."
        else:
            fills = screenshot.read_fills(lines)
            out["fills"] = fills
            short = trade.get("side") == "short"          # a short is closed by a buy
            sells = [f for f in fills if f["side"] == ("buy" if short else "sell") and f["symbol"] == trade["symbol"]]
            pick = next((f for f in reversed(sells) if f["qty"] == trade["qty"]), sells[-1] if sells else None)
            if pick:
                p = pick["price"]
                if short:
                    reason = "target" if p <= trade["target"] + 0.005 else "stop" if p >= trade["stop"] - 0.005 else "manual"
                else:
                    reason = "target" if p >= trade["target"] - 0.005 else "stop" if p <= trade["stop"] + 0.005 else "manual"
                out["suggest"] = {"price": p, "reason": reason}
                out["message"] = "Found: %s %d %s at %.2f. Check it, then press Save." % (
                    "bought to cover" if short else "sold", pick["qty"], pick["symbol"], p)
            else:
                out["message"] = "No %s of %s found in that screenshot. Type the exit price instead." % (
                    "buy to cover" if short else "sale", trade["symbol"])
        self.j.add_event("screenshot", "Saved a %s screenshot (%s)." % (kind, out["image"]), now,
                         trade["symbol"] if trade else None)
        return out

    @property
    def auto(self):
        """Auto paper (desk/auto.py): paper mode only, practice account only."""
        if getattr(self, "_auto", None) is None:
            self._auto = A.AutoPaper(self)
        return self._auto

    @property
    def paper(self):
        """The pretend practice account auto paper trades in paper mode (desk/paper.py)."""
        if getattr(self, "_paper", None) is None:
            from . import paper
            self._paper = paper.PaperAccount(self)
        return self._paper

    def earnings(self, symbol):
        st, now = S.load(), self.src.now()
        symbol = symbol.strip().upper()
        if not symbol.replace(".", "").isalpha() or len(symbol) > 7:
            raise DeskError("That is not a ticker.")
        args = [symbol, "--json", "--quarters", 4]
        if self.src.demo:
            args += ["--offline", FIXTURES / "trader-analyst"]
        elif not st["sec_user_agent"]:
            raise DeskError("The SEC asks who is reading. Put your name and email in Settings (SEC contact) to turn this on.")
        r = skills.run("analyst", args, self.scan_dir(now), timeout=90)
        row = r.json[0] if isinstance(r.json, list) and r.json else None
        if not row or not row.get("result"):
            raise DeskError("No earnings data for %s: %s" % (symbol, (row or {}).get("error") or r.message))
        res = row["result"]
        keys = ("fiscal", "end", "revenue", "net_income", "eps_diluted", "gross_margin", "net_margin",
                "revenue_yoy", "net_income_yoy", "eps_yoy", "filed", "form")
        return {"symbol": symbol, "entity": res.get("entity"),
                "rows": [{k: q.get(k) for k in keys} for q in res.get("rows", [])]}

    def congress(self, refresh=False):
        st, now = S.load(), self.src.now()
        cached = self.j.get("congress")
        if cached and not refresh and cached.get("date") == now[:10]:
            return cached
        args = ["index", "--json", "--limit", 15]
        for m in st["congress_members"]:
            args += ["--member", m]
        if self.src.demo:
            args += ["--offline", FIXTURES / "trader-pelosi" / "2026FD.zip"]
        r = skills.run("pelosi", args, self.scan_dir(now), timeout=90)
        rows = r.json if isinstance(r.json, list) else []
        out = {"date": now[:10], "at": now, "error": None if isinstance(r.json, list) else r.message,
               "filings": [{"name": f.get("name"), "filed": f.get("filed"), "type": f.get("type_name"),
                            "url": f.get("url") if str(f.get("url", "")).startswith("https://") else ""} for f in rows]}
        self.j.put("congress", out)
        return out

    # ---- everything the dashboard shows ------------------------------
    def state(self):
        st, now = S.load(), self.src.now()
        today = now[:10]
        equity = self.j.equity()
        lim = S.limits(st, equity)
        quotes = self.j.get("quotes", {})
        open_trades = []
        for t in self.j.open_trades():
            q = (quotes.get(t["symbol"]) or {})
            t["last"], t["last_at"] = q.get("price"), q.get("at")
            sign = -1 if t.get("side") == "short" else 1
            t["unrealised"] = round(sign * (q["price"] - t["entry_price"]) * t["qty"], 2) if q.get("price") else None
            t["max_loss"] = round(sign * (t["entry_price"] - t["stop"]) * t["qty"], 2)
            t["reward"] = round(sign * (t["target"] - t["entry_price"]) * t["qty"], 2)
            open_trades.append(t)
        halt = self.halt_reason(st, now)
        signals = self.j.signals(date=today)
        settled = self.j.settled_cash(today)
        for sg in signals:
            if sg["status"] == "ready":
                p = sg["payload"]
                if p.get("side") == "short" and st["directions"] == "long":
                    sg["off"] = "Short signals are switched off in Settings."
                    continue
                sg["off"] = halt or (("You have $%.2f of settled cash and this needs about $%.2f." % (settled, p["cost"]))
                                     if p.get("cost", 0) > settled else None)
        out = {
            "now": now, "demo": self.src.demo, "mode": st["mode"], "clock": self.clock(now),
            "halt": halt,
            "account": {"equity": equity, "cash": self.j.cash(), "settled_cash": self.j.settled_cash(today),
                        "unsettled": self.j.unsettled(today), "spendable": self.spendable(st, now),
                        "positions_cost": self.j.positions_cost(), "type": st["account_type"]},
            "limits": lim, "day": self.j.day(today), "stats": self.j.stats(),
            "signals": signals, "open_trades": open_trades,
            "trades": self.j.trades(limit=50), "events": self.j.events(limit=40),
            "last_scan": self.j.get("last_scan"), "premarket": self.j.get("premarket"),
            "watchlist_research": self.j.get("watchlist_research"),
            "account_name": account_name(),
            "settings": st,
            "shorts": shorts.status(st, equity),
            "auto": self.auto.summary(st),
            "stops": self.stops(st, now),
            "chart_urls": {},
        }
        out["health"] = self.health(st, now, halt)
        out["chart_urls"] = exports.chart_urls(out)
        out["export_unknown"] = exports.tradingview_text(exports.todays_symbols(out), exports.known(out))[1]
        return out

    def review(self, journal=None, mode=None):
        """The record laid out for reading: numbers and the plain-text report. Changes nothing."""
        st, now = S.load(), self.src.now()
        j, mode = journal or self.j, mode or st["mode"]
        summ = R.summary(j.trades(limit=100000), j.signals(limit=100000), j.events(limit=100000), j.get("rulesets", {}),
                         outcomes=j.outcomes(), scans=j.scans(limit=100000))
        summ["decisions"] = decide.counts(j.decisions(limit=100000))
        res = j.get_prefix("watchlist_results:")
        summ["watchlists"] = R.watchlist_comparison(
            [dict(c, results=res.get("watchlist_results:" + c["date"]) or {})
             for c in j.get_prefix("watchlist_comparison:").values()])
        try:
            from . import meme
            summ["meme"] = meme.view()
        except Exception:               # a damaged memecoin file must not break the stocks review
            summ["meme"] = None
        return {"mode": mode, "now": now, "summary": summ, "text": R.report(mode, summ, now, stamp.rules_hash(st))}

    def chart(self, symbol, interval="5m"):
        """The Desk tab's chart (desk/charts.py view), plus today's slip for that ticker as price lines."""
        st, now = S.load(), self.src.now()
        try:
            out = charts.view(self.src, symbol, interval, st["or_minutes"])
        except charts.ChartError as e:
            raise DeskError(str(e))
        sig = next((s for s in self.j.signals(date=now[:10]) if s["symbol"] == out["symbol"]), None)
        if sig:
            p = sig["payload"]
            out["levels"] += [{"name": n, "price": p[k], "kind": "slip"} for n, k in
                              (("Entry", "limit"), ("Stop", "stop"), ("Target", "target")) if p.get(k)]
        return out

    def graph(self):
        """The Graph tab: made or lost over time, for this record and every paper account (desk/graph.py)."""
        return G.summary(self.j.path)

    def benchmarks(self):
        """The Vs market tab: the bot's return next to the S&P 500 and other index ETFs (desk/benchmarks.py)."""
        days = [d for d in G.curve([t for t in G._closed(self.j.path) if t.get("note") == A.AUTO])["days"] if d["date"]]
        return BM.summary(days, demo=self.src.demo)

    def exports(self):
        """Today's stocks as text for TradingView and thinkorswim. Read-only: nothing is sent anywhere."""
        return exports.bundle(self.state())
