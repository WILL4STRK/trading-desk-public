"""C1 / C1-S follower (docs/MEME.md section 19.B). Paper only: no wallet, no signing key, no order path. It watches the frozen
wallets over a read-only Helius log subscription, parses their swaps, and books paper copies from DexScreener prices.

    python research/copy/follow.py run       the long-running process (one at a time: an OS lock on state\\follow.lock)
    python research/copy/follow.py status    counts from state.json

Logs (append-only JSONL, one unique id per line, so a restart never duplicates a line) in C:\\Trading\\research\\copy\\:
events.jsonl (every followed-wallet swap, its detection lag, missed or not), fills.jsonl (paper legs), skips.jsonl,
closes.jsonl (closed copies: net, the wallet's own result, copy gap), alerts.jsonl. state.json is replaced atomically.
"""
import argparse
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.copy import common as C

DEX = "https://api.dexscreener.com/tokens/v1/solana/%s"
RUGCHECK_SUM = "https://api.rugcheck.xyz/v1/tokens/%s/report/summary"
SAMPLE_EVERY = 5.0
POSITION_EVERY = 30.0
SETTINGS_EVERY = 3600.0
PING_EVERY = 30.0
SEEN_KEEP = 20000
ALERT_ARMS = ("C1", "C1S")


# ---- the frozen lists -> who we follow ---------------------------------------------------------------------------------------

def load_roles(c1_path=None, c1s_path=None, check=C.frozen_check):
    """{address: [role]} with role = {arm, tag, cluster, stats, start}. Each list must pass the frozen check; its forward
    window starts at its freeze commit. C1 and its random control are followed only if discovery said C1 started."""
    roles, frozen = {}, {}
    c1_path = Path(c1_path or C.HERE / "WALLETS-C1.json")
    c1s_path = Path(c1s_path or C.HERE / "WALLETS-C1S.json")

    def add(w, arm, start, cluster=None):
        stats = {k: w.get(k) for k in ("runners", "early_buys", "swaps_per_day", "realized_sol", "win_share", "rules",
                                       "lead_mints", "lead_median_s") if w.get(k) is not None}
        roles.setdefault(w["address"], []).append({"arm": arm, "tag": w["tag"], "cluster": cluster, "stats": stats,
                                                   "start": start})
    if c1_path.exists():
        fz = check(c1_path)
        frozen["C1"] = fz
        doc = json.loads(c1_path.read_text(encoding="utf-8"))
        if doc.get("started"):
            for w in doc.get("c1") or []:
                add(w, "C1", fz["start"])
            for w in doc.get("random") or []:
                add(w, "random", fz["start"])
    if c1s_path.exists():
        fz = check(c1s_path)
        frozen["C1S"] = fz
        doc = json.loads(c1s_path.read_text(encoding="utf-8"))
        for w in doc.get("sides") or []:
            add(w, "C1S", fz["start"], w.get("cluster"))
        for w in doc.get("mains") or []:
            add(w, "main", fz["start"], w.get("cluster"))
    return roles, frozen


# ---- prices -----------------------------------------------------------------------------------------------------------------

def dex_prices(mints, http=C.http_json, clock=time.time):
    """{mint: {px, liq, native (SOL per token), pool, symbol, t}} from DexScreener, the pair with the most liquidity.
    `t` is the local time the answer arrived (the sample time). SOL's own USD price converts non-SOL quotes."""
    out = {}
    ms = [m for m in dict.fromkeys(mints) if C.B58.match(m)]
    for i in range(0, len(ms), 30):
        chunk = ms[i:i + 30]
        try:
            pairs = http(DEX % ",".join(chunk))
        except RuntimeError:
            continue
        t = clock()
        if not isinstance(pairs, list):
            continue
        sol_usd = None
        best = {}
        for p in pairs:
            base = (p.get("baseToken") or {}).get("address")
            liq = (p.get("liquidity") or {}).get("usd")
            if base in chunk and (base not in best or (liq or 0) > ((best[base].get("liquidity") or {}).get("usd") or 0)):
                best[base] = p
        for m, p in best.items():
            try:
                px = float(p.get("priceUsd") or 0) or None
            except ValueError:
                px = None
            q = (p.get("quoteToken") or {}).get("address")
            native = None
            try:
                if q == C.SOL_MINT:
                    native = float(p.get("priceNative") or 0) or None
                elif px:
                    sol_usd = sol_usd or sol_price(http, clock)
                    native = px / sol_usd if sol_usd else None
            except ValueError:
                pass
            out[m] = {"px": px, "liq": (p.get("liquidity") or {}).get("usd"), "native": native, "pool": p.get("pairAddress"),
                      "symbol": (p.get("baseToken") or {}).get("symbol") or m[:6], "t": t}
    return out


_SOL = {"t": -1e18, "v": None}


def sol_price(http=C.http_json, clock=time.time):
    """SOL in USD from DexScreener (the most liquid SOL/USDC or SOL/USDT pair), cached for 60 s."""
    if clock() - _SOL["t"] < 60 and _SOL["v"]:
        return _SOL["v"]
    try:
        pairs = http(DEX % C.SOL_MINT)
    except RuntimeError:
        return _SOL["v"]
    best = None
    for p in pairs if isinstance(pairs, list) else []:
        if ((p.get("baseToken") or {}).get("address") == C.SOL_MINT and (p.get("quoteToken") or {}).get("symbol") in ("USDC", "USDT")
                and p.get("priceUsd") and (best is None or ((p.get("liquidity") or {}).get("usd") or 0) > ((best.get("liquidity") or {}).get("usd") or 0))):
            best = p
    if best:
        _SOL.update(t=clock(), v=float(best["priceUsd"]))
    return _SOL["v"]


# ---- alerts -----------------------------------------------------------------------------------------------------------------

def alert_text(role, side, symbol, mint, pool):
    """(title, message). C1-S tags carry cluster IDs only; nothing here can hold a caller's name."""
    title = "[PAPER COPY] %s %s %s" % (role["tag"], side.upper(), symbol)
    st = role.get("stats") or {}
    bits = ", ".join("%s %s" % (k, st[k]) for k in sorted(st))
    links = ["https://dexscreener.com/solana/%s" % (pool or mint), "https://solscan.io/token/%s" % mint,
             "https://rugcheck.xyz/tokens/%s" % mint]
    return title, "%s\nDev stats: %s\n%s\n%s" % (title, bits or "none", "\n".join(links), C.ALERT_DISCLAIMER)


class Alerter:
    """ntfy pushes, at most 20 in any hour (the sent times live in state, so a restart keeps the cap)."""

    def __init__(self, state, log, settings=C.ntfy_settings, post=None, clock=time.time):
        self.state, self.log, self.settings, self.clock = state, log, settings, clock
        self.post = post or self._post
        self.server, self.topic, self.read_at = None, "", -1e18

    def _post(self, server, body):
        C.http_json(server, body)

    def refresh(self):
        now = self.clock()
        if now - self.read_at >= SETTINGS_EVERY:
            self.read_at = now
            try:
                self.server, self.topic = self.settings()
            except Exception as e:
                self.state.setdefault("errors", []).append("settings: %s" % C.mask(e))

    def send(self, aid, title, message):
        if aid in self.log:
            return "dup"
        self.refresh()
        now = self.clock()
        sent = [t for t in self.state.get("alert_times", []) if now - t < 3600]
        self.state["alert_times"] = sent
        if not self.topic:
            status = "no_topic"
        elif len(sent) >= C.ALERT_CAP:
            status = "capped"
        else:
            try:
                self.post(self.server, {"topic": self.topic, "title": title, "message": message, "tags": ["memo"]})
                sent.append(now)
                status = "sent"
            except Exception as e:
                status = "error: %s" % C.mask(e)
        self.log.add({"id": aid, "t": now, "title": title, "status": status})
        return status


# ---- the paper engine -------------------------------------------------------------------------------------------------------

class Engine:
    """All copy rules of 19.B. Pure apart from the injected price, check and alert functions and the clock, so tests drive it
    with fixtures. Every id is deterministic (signature, wallet, arm, leg), and a leg already in fills.jsonl is replayed from
    the log instead of being filled again, so a restart cannot duplicate or re-price anything."""

    def __init__(self, home, roles, prices, check, alerter=None, clock=time.time):
        self.home = Path(home)
        self.roles, self.prices, self.check, self.clock = roles, prices, check, clock
        self.events = C.Log(self.home / "events.jsonl")
        self.fills = C.Log(self.home / "fills.jsonl")
        self.skips = C.Log(self.home / "skips.jsonl")
        self.closes = C.Log(self.home / "closes.jsonl")
        self.alerts_log = C.Log(self.home / "alerts.jsonl")
        self.state_path = self.home / "state.json"
        self.state = self._load()
        self.alerter = alerter(self.state, self.alerts_log) if alerter else None
        self._fill_rows = {r["id"]: r for r in self.fills.rows()}
        self.last_pos_check = 0.0

    def _load(self):
        if self.state_path.exists():
            s = json.loads(self.state_path.read_text(encoding="utf-8"))
        else:
            s = {}
        for k, v in (("positions", {}), ("pending", []), ("seen", []), ("last_sig", {}), ("mint_checks", {}),
                     ("alert_times", []), ("counts", {}), ("errors", [])):
            s.setdefault(k, v)
        return s

    def save(self):
        self.state["seen"] = self.state["seen"][-SEEN_KEEP:]
        self.state["errors"] = self.state["errors"][-50:]
        self.state["heartbeat"] = self.clock()
        C.atomic_write(self.state_path, json.dumps(self.state, sort_keys=True))

    def count(self, k):
        self.state["counts"][k] = self.state["counts"].get(k, 0) + 1

    def skip(self, sid, arm, mint, why, **extra):
        if self.skips.add(dict({"id": sid, "t": self.clock(), "arm": arm, "mint": mint, "why": why}, **extra)):
            self.count("skip_" + why.split(":")[0])

    # -- detection
    def on_tx(self, tx, t_det):
        """Every swap of a followed wallet in this transaction."""
        sig = (tx["transaction"].get("signatures") or [None])[0]
        for s in C.parse_swaps(tx, set(self.roles)):
            self.on_swap(s, t_det)
        if sig and sig not in self.state["seen"]:
            self.state["seen"].append(sig)

    def on_swap(self, s, t_det):
        lag = t_det - s["ts"] if s["ts"] is not None else None
        missed = lag is None or lag > C.MISSED_AFTER_S
        eid = "%s:%s" % (s["sig"], s["wallet"])
        roles = [r for r in self.roles.get(s["wallet"], []) if s["ts"] is not None and s["ts"] >= r["start"]]
        if not roles:
            return
        self.events.add(dict(s, id=eid, t_det=t_det, lag=lag, missed=missed, roles=[r["arm"] for r in roles],
                             tags=[r["tag"] for r in roles], clusters=[r["cluster"] for r in roles]))
        if missed:
            self.count("missed")
            return
        for r in roles:
            if self.alerter and r["arm"] in ALERT_ARMS:
                info = self.prices([s["mint"]]).get(s["mint"]) or {}
                title, msg = alert_text(r, s["side"], info.get("symbol") or s["mint"][:6], s["mint"], info.get("pool"))
                self.alerter.send("%s:%s:alert" % (eid, r["arm"]), title, msg)
            if s["side"] == "buy":
                self.plan_buy(s, r, t_det)
            else:
                self.plan_sell(s, r, t_det)

    def _open(self, arm):
        return [p for p in self.state["positions"].values() if p["arm"] == arm and not p.get("closed")]

    def _pending_buys(self, arm):
        return [l for l in self.state["pending"] if l["arm"] == arm and l["leg"] == "buy"]

    def plan_buy(self, s, r, t_det):
        arm, mint = r["arm"], s["mint"]
        lid = "%s:%s:%s:buy" % (s["sig"], s["wallet"], arm)
        if lid in self.fills or any(l["id"] == lid for l in self.state["pending"]):
            return
        held = {p["mint"] for p in self._open(arm)} | {l["mint"] for l in self._pending_buys(arm)}
        if mint in held:
            return self.skip(lid, arm, mint, "one_per_coin")
        if len(self._open(arm)) + len(self._pending_buys(arm)) >= C.MAX_OPEN:
            return self.skip(lid, arm, mint, "max_open")
        why = self.mint_check(mint)
        if why is None:
            return self.skip(lid, arm, mint, "cant_sell_unknown")
        if why:
            return self.skip(lid, arm, mint, "cant_sell", reasons=why)
        self.state["pending"].append({"id": lid, "leg": "buy", "arm": arm, "mint": mint, "wallet": s["wallet"],
                                      "tag": r["tag"], "t_det": t_det, "due": t_det + C.DELAY_S,
                                      "deadline": t_det + C.FILL_WINDOW_S, "lag": t_det - s["ts"],
                                      "own_sol": s["sol"], "own_tokens": s["tokens"], "wallet_ts": s["ts"]})

    def plan_sell(self, s, r, t_det):
        arm, mint = r["arm"], s["mint"]
        pos = next((p for p in self._open(arm) if p["mint"] == mint and p["wallet"] == s["wallet"]), None)
        if pos is None:
            return
        f = C.mirror_fraction(s["tokens"], s["pre_tokens"])
        lid = "%s:%s:%s:sell" % (s["sig"], s["wallet"], arm)
        if lid in self.fills or any(l["id"] == lid for l in self.state["pending"]):
            return
        own = pos["own"]
        if own["left"] > 0:                       # the wallet's own lot sells the same fraction at its own price
            q = own["left"] * f
            own["received"] += q * (s["sol"] / s["tokens"])
            own["left"] -= q
        self.state["pending"].append({"id": lid, "leg": "sell", "arm": arm, "mint": mint, "wallet": s["wallet"],
                                      "pid": pos["pid"], "fraction": f, "t_det": t_det, "due": t_det + C.DELAY_S,
                                      "deadline": t_det + C.FILL_WINDOW_S, "why": "mirror"})

    def mint_check(self, mint):
        """Cached can't-sell reasons ([] = clear, None = unknown)."""
        c = self.state["mint_checks"].get(mint)
        if c is None or (c.get("why") is None and self.clock() - c.get("t", 0) > 600):
            why = self.check(mint)
            c = {"why": why, "t": self.clock()}
            self.state["mint_checks"][mint] = c
        return c["why"]

    # -- fills
    def tick(self):
        now = self.clock()
        due = [l for l in self.state["pending"] if l["due"] <= now]
        if due:
            replay = [l for l in due if l["id"] in self._fill_rows]
            for l in replay:
                self._apply(l, self._fill_rows[l["id"]])
            need = [l for l in due if l not in replay]
            px = self.prices([l["mint"] for l in need]) if need else {}
            for l in need:
                q = px.get(l["mint"])
                if q and q.get("px") and q["t"] >= l["due"]:
                    self.fill(l, q)
                elif now > l["deadline"]:
                    self.state["pending"].remove(l)
                    self.skip(l["id"], l["arm"], l["mint"], "no_price_in_window")
        if now - self.last_pos_check >= POSITION_EVERY:
            self.last_pos_check = now
            self.check_positions(now)

    def fill(self, l, q):
        if l["leg"] == "buy":
            fp = C.buy_fill(q["px"], q.get("liq"))
            if fp is None:
                self.state["pending"].remove(l)
                return self.skip(l["id"], l["arm"], l["mint"], "no_liquidity")
            row = {"id": l["id"], "leg": "buy", "arm": l["arm"], "mint": l["mint"], "t": q["t"], "px": q["px"],
                   "liq": q.get("liq"), "fill": fp, "native": q.get("native"), "symbol": q.get("symbol"),
                   "pool": q.get("pool"), "t_det": l["t_det"], "lag": l["lag"], "delay": q["t"] - l["t_det"]}
        else:
            pos = self.state["positions"].get(l["pid"])
            if not pos or pos.get("closed"):
                self.state["pending"].remove(l)
                return
            tokens = pos["left"] * l["fraction"]
            fp = C.sell_fill(q["px"], q.get("liq"), tokens * q["px"])
            row = {"id": l["id"], "leg": "sell", "arm": l["arm"], "mint": l["mint"], "pid": l["pid"], "t": q["t"],
                   "px": q["px"], "liq": q.get("liq"), "fill": fp, "tokens": tokens, "native": q.get("native"),
                   "fraction": l["fraction"], "why": l.get("why"), "t_det": l["t_det"], "delay": q["t"] - l["t_det"]}
        self.fills.add(row)
        self._fill_rows[row["id"]] = row
        self._apply(l, row)

    def _apply(self, l, row):
        if l in self.state["pending"]:
            self.state["pending"].remove(l)
        if row["leg"] == "buy":
            pid = row["id"]
            if pid in self.state["positions"]:
                return
            tokens = C.SIZE_USD / row["fill"]
            self.state["positions"][pid] = {
                "pid": pid, "arm": row["arm"], "mint": row["mint"], "wallet": l["wallet"], "tag": l.get("tag"),
                "symbol": row.get("symbol"), "pool": row.get("pool"), "t": row["t"], "wallet_ts": l.get("wallet_ts"),
                "fill": row["fill"], "tokens": tokens, "left": tokens, "proceeds": 0.0, "lag": l.get("lag"),
                "own": {"paid": l["own_sol"], "lot": l["own_tokens"], "left": l["own_tokens"], "received": 0.0}}
            self.count("buys")
            return
        pos = self.state["positions"].get(row["pid"])
        if not pos or pos.get("closed") or row["id"] in pos.setdefault("legs", []):
            return
        pos["legs"].append(row["id"])
        pos["proceeds"] += row["tokens"] * row["fill"]
        pos["left"] -= row["tokens"]
        if pos["left"] <= pos["tokens"] * 1e-9:
            self.close(pos, row["why"] or "mirror", row["t"], row.get("native"))

    def check_positions(self, now):
        opn = [p for p in self.state["positions"].values() if not p.get("closed")]
        if not opn:
            return
        px = self.prices([p["mint"] for p in opn])
        for p in opn:
            q = px.get(p["mint"])
            if q is None:
                continue
            if q.get("liq") is not None and q["liq"] < C.DEAD_LIQ:
                self.close(p, "dead_pool", q["t"], q.get("native"), dead=True)
            elif q["t"] - p["t"] >= C.TIME_EXIT_S and q.get("px"):
                lid = "%s:time" % p["pid"]
                l = {"id": lid, "leg": "sell", "arm": p["arm"], "mint": p["mint"], "wallet": p["wallet"], "pid": p["pid"],
                     "fraction": 1.0, "t_det": q["t"], "due": q["t"], "deadline": q["t"], "why": "time"}
                if lid in self._fill_rows:
                    self._apply(l, self._fill_rows[lid])
                else:
                    self.fill(l, q)

    def close(self, p, why, t, native, dead=False):
        """Net per copied buy, the wallet's own result on the same lot, and the copy gap."""
        if dead:
            p["left"] = 0.0
        p["closed"] = True
        net = copy_net(p["proceeds"])
        own = own_result(p["own"], native)
        row = {"id": p["pid"], "arm": p["arm"], "tag": p["tag"], "wallet": p["wallet"], "mint": p["mint"],
               "symbol": p.get("symbol"), "entry_t": p["t"], "exit_t": t, "why": why, "net_pct": net,
               "own_pct": own, "gap_pct": None if own is None else round(own - net, 4), "lag": p.get("lag")}
        self.closes.add(row)
        self.count("closes")
        return row


def copy_net(proceeds, size=C.SIZE_USD):
    """Net % of one copied buy: dollars back over all its sells / $20 - 1."""
    return round((proceeds / size - 1) * 100, 4)


def own_result(own, native_at_exit):
    """The copied wallet's own % on the copied lot: SOL received for the fractions it sold, plus what is left valued at our
    exit sample's SOL price, over the SOL it paid. None if unknown."""
    if not own or not own.get("paid"):
        return None
    left_val = own["left"] * native_at_exit if own.get("left") and native_at_exit else 0.0
    if own.get("left") and native_at_exit is None:
        return None
    return round(((own["received"] + left_val) / own["paid"] - 1) * 100, 4)


# ---- can't-sell check (live) ------------------------------------------------------------------------------------------------

def make_check(helius, http=C.http_json):
    pace = C.Pacer(1, 1.1)

    def check(mint):
        try:
            acct = (helius.rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}]) or {}).get("value")
        except Exception:
            return None
        a = C.mint_blocks(acct)
        if a is None:
            return None
        pace.wait()
        try:
            b = C.rugcheck_blocks(http(RUGCHECK_SUM % mint))
        except RuntimeError:
            return None
        if b is None:
            return None
        return a + b
    return check


# ---- the live loop ----------------------------------------------------------------------------------------------------------

class Stream(threading.Thread):
    """logsSubscribe (mentions) for every followed wallet on one Helius WebSocket; pushes (signature, wallet) to a queue.
    Reconnects with backoff and asks the main loop to back-fill after each reconnect."""

    def __init__(self, wss, wallets, q, log):
        super().__init__(daemon=True)
        self.wss, self.wallets, self.q, self.log = wss, sorted(wallets), q, log
        self.stop = threading.Event()

    def run(self):
        back = 1
        while not self.stop.is_set():
            ws = None
            try:
                ws = C.WebSocket(self.wss, timeout=PING_EVERY + 15)
                subs, pend = {}, {}
                for i, w in enumerate(self.wallets, 1):
                    pend[i] = w
                    ws.send(json.dumps({"jsonrpc": "2.0", "id": i, "method": "logsSubscribe",
                                        "params": [{"mentions": [w]}, {"commitment": "confirmed"}]}))
                self.q.put(("backfill", None))
                back, last_ping = 1, time.time()
                while not self.stop.is_set():
                    if time.time() - last_ping >= PING_EVERY:
                        ws.ping()
                        last_ping = time.time()
                    try:
                        msg = json.loads(ws.recv())
                    except TimeoutError:
                        continue
                    if "id" in msg and msg.get("id") in pend:
                        if "result" in msg:
                            subs[msg["result"]] = pend.pop(msg["id"])
                        else:
                            self.log("subscribe failed for %s: %s" % (pend.pop(msg["id"])[:6], C.mask(msg.get("error"))))
                        continue
                    p = msg.get("params") or {}
                    v = ((p.get("result") or {}).get("value") or {})
                    w = subs.get(p.get("subscription"))
                    if w and v.get("signature") and v.get("err") is None:
                        self.q.put(("sig", (v["signature"], w)))
            except Exception as e:
                self.log("stream: %s; reconnect in %d s" % (C.mask(e), back))
            finally:
                if ws:
                    ws.close()
            self.stop.wait(back)
            back = min(back * 2, 60)


def backfill(h, eng, wallets, log):
    """Signatures since the last processed one per wallet (oldest first). A wallet seen for the first time starts from now."""
    for w in sorted(wallets):
        last = eng.state["last_sig"].get(w)
        try:
            sigs = h.rpc("getSignaturesForAddress", [w, dict({"limit": 100, "commitment": "confirmed"},
                                                             **({"until": last} if last else {}))]) or []
        except Exception as e:
            log("backfill %s: %s" % (w[:6], C.mask(e)))
            continue
        if sigs:
            eng.state["last_sig"][w] = sigs[0]["signature"]
        if not last:
            continue
        for s in reversed(sigs):
            if s.get("err") is None and s["signature"] not in eng.state["seen"]:
                process(h, eng, s["signature"], log)


def process(h, eng, sig, log):
    if sig in eng.state["seen"]:
        return
    try:
        tx = h.rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0,
                                            "commitment": "confirmed"}])
    except Exception as e:
        log("getTransaction %s: %s" % (sig[:8], C.mask(e)))
        return
    if tx:
        eng.on_tx(tx, time.time())


def take_lock(home):
    import msvcrt
    p = Path(home) / "follow.lock"
    p.parent.mkdir(parents=True, exist_ok=True)
    f = open(p, "a+")
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        raise SystemExit("another follow.py holds %s" % p)
    C.atomic_write(Path(home) / "follow.pid", str(os.getpid()))
    return f


def refuse_live_folder():
    live = (Path.home() / "trading-desk").resolve()
    if C.HERE == live or live in C.HERE.parents:
        raise SystemExit("follow.py never runs from the live desk folder")


def run(home=C.HOME):
    refuse_live_folder()
    lock = take_lock(home)
    logf = Path(home) / "follow.log"

    def log(msg):
        with open(logf, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), C.mask(msg)))
    roles, frozen = load_roles()
    if not roles:
        raise SystemExit("no frozen list to follow")
    key = C.load_key()
    h = C.Helius(key, rps=8)
    eng = Engine(home, roles, dex_prices, make_check(h), alerter=Alerter)
    for k, fz in frozen.items():
        old = eng.state.setdefault("frozen", {}).get(k)
        if old and old["sha256"] != fz["sha256"]:
            raise SystemExit("%s list hash changed since the first start: a new list is a new study" % k)
        eng.state["frozen"][k] = fz
    log("start: %d wallets, lists %s" % (len(roles), {k: v["sha256"][:12] for k, v in frozen.items()}))
    q = queue.Queue()
    st = Stream(h.wss, roles, q, log)
    st.start()
    try:
        while True:
            try:
                kind, v = q.get(timeout=1.0)
            except queue.Empty:
                kind = None
            if kind == "backfill":
                backfill(h, eng, roles, log)
            elif kind == "sig":
                sig, w = v
                process(h, eng, sig, log)
                eng.state["last_sig"][w] = sig
            eng.tick()
            eng.state["credits"] = h.credits
            eng.save()
    finally:
        st.stop.set()
        lock.close()


def status(home=C.HOME):
    p = Path(home) / "state.json"
    s = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    opn = [x for x in (s.get("positions") or {}).values() if not x.get("closed")]
    print(json.dumps({"heartbeat": s.get("heartbeat"), "counts": s.get("counts"), "open": len(opn),
                      "pending": len(s.get("pending") or []), "credits": s.get("credits")}, indent=1))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["run", "status"])
    a = ap.parse_args(argv)
    run() if a.what == "run" else status()


if __name__ == "__main__":
    main()
