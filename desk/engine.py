"""The desk: scans, turns fresh setups into signals, watches open trades and
records what YOU did. It never places a real order: you place each one at your
broker by hand. In paper mode, auto paper may trade the desk's own pretend
practice account (desk/paper.py, desk/auto.py), which never touches a broker.
"""
import datetime
import math
import re
import threading

from . import auto as A
from . import changes
from . import charts
from . import exports
from . import health
from . import mode as M
from . import outcome
from . import pipeline
from . import review as R
from . import screenshot
from . import settings as S
from . import skills
from . import stamp
from .journal import Journal, JournalError
from .paths import FIXTURES, data_dir

MAX_EVALS_PER_SCAN = 8


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
        """When money from a sale made now can be used again."""
        if S.load()["account_type"] == "margin":
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
        return round(max(0.0, min(self.j.settled_cash(now[:10]) - lim["cash_buffer"], lim["max_position"])), 2)

    def halt_reason(self, st, now):
        if st.get("paused"):
            return "Paused by you. No new signals until you resume. Open trades keep their alerts."
        c = self.clock(now)
        if not c.get("open"):
            return "The market is closed (%s)." % (c.get("reason") or "closed")
        t = _minutes(now[11:16])
        shift = 180 if c.get("early_close") else 0
        if t < _minutes(st["no_entries_before"]):
            return "No entries before %s. The opening range is still forming." % st["no_entries_before"]
        if t >= _minutes(st["no_entries_after"]) - shift:
            return "No new entries this late in the day."
        day = self.j.day(now[:10])
        lim = S.limits(st, self.j.equity())
        if day["trades"] >= st["max_trades_per_day"]:
            return "You have taken %d of %d trades today. Done for the day." % (day["trades"], st["max_trades_per_day"])
        if day["realised"] <= -lim["max_daily_loss"] < 0:
            return "Down $%.2f today, at your daily loss limit of $%.2f. Done for the day." % (-day["realised"], lim["max_daily_loss"])
        if st["mode"] == "live" and self.j.cash() <= 0:
            return "Live mode has no cash yet. Enter your real cash in the Account tab first."
        if self.spendable(st, now) < max(st["min_price"], 1):
            return "No settled cash to trade with until your last sale settles."
        return None

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
        """One pass: opening-range breakouts so far, then the full pipeline on the fresh ones."""
        with self.lock:
            st, now = S.load(), self.src.now()
            summary = {"at": now, "scanned": 0, "hits": 0, "fresh": 0, "evaluated": 0, "too_expensive": 0,
                       "stale": 0, "already": 0, "new_signals": 0, "halt": self.halt_reason(st, now), "error": None}
            if summary["halt"]:
                self.j.put("last_scan", summary)
                return summary
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
                self.j.put("last_scan", summary)
                return summary
            hits = d.get("hits", [])
            counts = d.get("counts") if isinstance(d.get("counts"), dict) else {}
            # unusable: charts the scanner left out because a 5-minute candle is missing from the free data
            summary.update({"scanned": d.get("scanned", 0), "hits": len(hits), "failed": d.get("failed", 0),
                            "unusable": int(counts.get("unusable") or 0)})
            now_min, spend = _minutes(now[11:16]), self.spendable(st, now)
            fresh, fate = [], {}
            for h in hits:
                sym = h["symbol"]
                if sym in st["blocked"]:
                    fate[sym] = "blocked list"
                    continue
                if self.j.has_signal(now[:10], sym, "orb"):
                    summary["already"] += 1
                    fate[sym] = "signal already made"
                elif now_min - _at_minutes(h["at"]) > st["signal_valid_minutes"]:
                    summary["stale"] += 1
                    fate[sym] = "too old"
                elif h["price"] > spend:
                    summary["too_expensive"] += 1
                    fate[sym] = "too expensive"
                else:
                    fresh.append(h)
                    fate[sym] = "waiting: only %d graded a scan" % MAX_EVALS_PER_SCAN
            summary["fresh"] = len(fresh)
            fresh.sort(key=lambda h: (-_at_minutes(h["at"]), -h.get("pct_over", 0)))
            for h in fresh[:MAX_EVALS_PER_SCAN]:
                status, sig = pipeline.evaluate(h["symbol"], st, self.j, self.src, hit=h)
                summary["evaluated"] += 1
                fate[h["symbol"]] = status or "no trigger: %s" % str(sig)[:120]
                if status is None:
                    continue
                sid = self.j.add_signal(now[:10], h["symbol"], "orb", status, sig, now)
                if sid and status == "ready":
                    summary["new_signals"] += 1
                    self.j.add_event("signal", "%s: buy %d at %.2f or lower, stop %.2f, target %.2f. Good until %s."
                                     % (h["symbol"], sig["qty"], sig["limit"], sig["stop"], sig["target"], sig["valid_until"]),
                                     now, h["symbol"], key="signal-%d" % sid)
            summary["detail"] = [{"symbol": h["symbol"], "at": h["at"], "price": h["price"], "fate": fate.get(h["symbol"])}
                                 for h in hits]
            self.j.put("last_scan", summary)
            return summary

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
                wd = pipeline.workdir(t["date"], t["symbol"])
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
                    msg = msg % ((t["symbol"], t["stop"]) if kind == "stop" else (t["symbol"], t["target"]) if kind == "target" else (t["symbol"],))
                    if t.get("note") != A.AUTO:             # auto paper sells its own trades: nothing for you to do
                        self.j.add_event(kind, msg, now, t["symbol"], key="trade-%d-%s" % (t["id"], kind))
                if t["date"] < now[:10]:
                    self.j.add_event("flat", "%s was opened on %s and is still marked open. Day trades are flat by the close: "
                                     "close it or record the exit." % (t["symbol"], t["date"]), now, t["symbol"],
                                     key="trade-%d-overnight-%s" % (t["id"], now[:10]))
            self.j.put("quotes", quotes)
            if now[11:16] >= "16:00":           # after the close, once: what the day was
                text = R.day_summary(now[:10], self.j.trades(now[:10]), self.j.signals(date=now[:10]))
                if text:
                    self.j.add_event("summary", text, now, key="summary-%s" % now[:10])
        if now[11:16] >= "16:05" and self.j.get("outcomes_done") != now[:10]:
            self.record_outcomes(now[:10])           # outside the lock: it reads one chart a signal

    def record_outcomes(self, date):
        """After the close: how every signal of the day would have done with its own numbers (desk/outcome.py)."""
        now, done = self.src.now(), 0
        for s in self.j.signals(date=date, limit=1000):
            p = s["payload"]
            try:
                bars = charts.session_bars(charts.intraday(self.src, s["symbol"]), date + " 16:00")
            except charts.ChartError as e:
                o = {"result": "no data", "entry": None, "exit": None, "exit_time": None, "r": None, "note": str(e)[:200]}
            else:
                o = outcome.replay(p, bars)
            self.j.set_outcome(s["id"], date, s["symbol"], s["status"], o, now)
            done += 1
        self.j.put("outcomes_done", date)
        if done:
            self.j.add_event("summary", "Replayed %d of today's signals against the day's candles for the review." % done,
                             now, key="outcomes-%s" % date)
        return done

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
            self.j.add_event("exit", "Recorded: sold %d %s at %.2f. %s $%.2f." %
                             (t["qty"], t["symbol"], price, "Made" if pnl >= 0 else "Lost", abs(pnl)), now, t["symbol"])
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

    def set_mode(self, new, typed=None):
        """Paper or live. Each has its own journal, so this swaps the whole record."""
        with self.lock:
            if new not in M.MODES:
                raise DeskError("Mode must be paper or live.")
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
        out = {"kind": kind, "image": str(path.relative_to(data_dir())), "text": "\n".join(l["text"] for l in lines[:80]),
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
            sells = [f for f in fills if f["side"] == "sell" and f["symbol"] == trade["symbol"]]
            pick = next((f for f in reversed(sells) if f["qty"] == trade["qty"]), sells[-1] if sells else None)
            if pick:
                p = pick["price"]
                reason = "target" if p >= trade["target"] - 0.005 else "stop" if p <= trade["stop"] + 0.005 else "manual"
                out["suggest"] = {"price": p, "reason": reason}
                out["message"] = "Found: sold %d %s at %.2f. Check it, then press Save." % (pick["qty"], pick["symbol"], p)
            else:
                out["message"] = "No sale of %s found in that screenshot. Type the exit price instead." % trade["symbol"]
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
            t["unrealised"] = round((q["price"] - t["entry_price"]) * t["qty"], 2) if q.get("price") else None
            t["max_loss"] = round((t["entry_price"] - t["stop"]) * t["qty"], 2)
            t["reward"] = round((t["target"] - t["entry_price"]) * t["qty"], 2)
            open_trades.append(t)
        halt = self.halt_reason(st, now)
        signals = self.j.signals(date=today)
        settled = self.j.settled_cash(today)
        for sg in signals:
            if sg["status"] == "ready":
                p = sg["payload"]
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
            "settings": st,
            "auto": self.auto.summary(st),
            "chart_urls": {},
        }
        out["health"] = self.health(st, now, halt)
        out["chart_urls"] = exports.chart_urls(out)
        out["export_unknown"] = exports.tradingview_text(exports.todays_symbols(out))[1]
        return out

    def review(self, journal=None, mode=None):
        """The record laid out for reading: numbers and the plain-text report. Changes nothing."""
        st, now = S.load(), self.src.now()
        j, mode = journal or self.j, mode or st["mode"]
        summ = R.summary(j.trades(limit=100000), j.signals(limit=100000), j.events(limit=100000), j.get("rulesets", {}),
                         outcomes=j.outcomes(), scans=j.scans(limit=100000))
        return {"mode": mode, "now": now, "summary": summ, "text": R.report(mode, summ, now, stamp.rules_hash(st))}

    def exports(self):
        """Today's stocks as text for TradingView and thinkorswim. Read-only: nothing is sent anywhere."""
        return exports.bundle(self.state())
