"""Phone alerts through ntfy (https://ntfy.sh): one push per ready signal and per breaking-news call, and, when
meme_ntfy is on, one per memecoin practice-account buy and sell (never for shadows).

Off until you set an ntfy topic in Settings. The demo never pushes. A push
that fails is logged and dropped: it never breaks or slows a scan. Anyone
who knows the topic name on a public server can read the alerts.
"""
import collections
import json
import logging
import math
import threading
import time
import urllib.parse
import urllib.request

from .paths import account_name

DASHBOARD = "http://127.0.0.1:8790"     # the desk on this PC (tools/windows/setup.ps1). Never a phone link: a phone cannot open it
log = logging.getLogger(__name__)

SAMPLE = {"symbol": "TEST", "qty": 1, "limit": 10.00, "stop": 9.80, "target": 10.40, "valid_until": "10:25",
          "reward_risk": 2.0, "max_loss": 0.20, "odds": {"edge_points": 3.0},
          "warnings": ["This is a test alert from your Trading Desk. It is not a signal."],
          "news": [{"title": "Test headline", "source": "Trading Desk", "link": ""}]}


def message(sig, mode):
    """The ntfy JSON body for one signal (without the topic)."""
    news = [n for n in (sig.get("news") or []) if n.get("title")][:3]
    short = sig.get("side") == "short"
    lines = ["%s %d at %.2f or %s. Stop %.2f. Target %.2f. Good until %s."
             % ("Sell short" if short else "Buy", sig["qty"], sig["limit"], "higher" if short else "lower",
                sig["stop"], sig["target"], sig["valid_until"])]
    if short and sig.get("paper_only"):
        lines.append("Practice only: this account cannot really short.")
    extra = []
    if sig.get("reward_risk") is not None:
        extra.append("Reward:risk %s." % sig["reward_risk"])
    if sig.get("max_loss") is not None:
        extra.append("Max loss $%.2f." % sig["max_loss"])
    ep = (sig.get("odds") or {}).get("edge_points")
    if ep is not None:
        extra.append("Time Machine edge %+.1f points." % ep)
    if extra:
        lines.append(" ".join(extra))
    if sig.get("warnings"):
        lines.append("Note: %s" % sig["warnings"][0])
    for n in news:
        lines.append("• %s (%s)" % (n["title"], n.get("source") or "news"))
        if n.get("link"):
            lines.append(n["link"])
    links = [n["link"] for n in news if n.get("link")]
    actions = [{"action": "view", "label": "Article", "url": u} for u in links[:2]]
    actions.append({"action": "view", "label": "Chart", "url": "https://finance.yahoo.com/quote/%s" % sig["symbol"]})
    tag = ("LIVE" if mode == "live" else "PAPER") + (" " + account_name() if account_name() else "")
    return {"title": "[%s] %s %s %s %.2f" % (tag, "SHORT" if short else "BUY",
                                              sig["symbol"], "≥" if short else "≤", sig["limit"]),
            "message": "\n".join(lines), "actions": actions,
            "tags": ["chart_with_downwards_trend" if short else "chart_with_upwards_trend"], "priority": 4,
            **({"click": links[0]} if links else {})}


def news_message(call, did, mode):
    """The ntfy JSON body for one breaking-news call (desk/breaking.py)."""
    tag = ("LIVE" if mode == "live" else "PAPER") + (" " + account_name() if account_name() else "")
    act = call["action"].upper()
    lines = [call["why"], "", "%s (%s)" % (call["title"], call["source"] or "news")]
    if call.get("link"):
        lines.append(call["link"])
    lines += ["", did]
    actions = [{"action": "view", "label": "Article", "url": call["link"]}] if call.get("link") else []
    actions.append({"action": "view", "label": "Chart", "url": "https://finance.yahoo.com/quote/%s" % call["symbol"]})
    return {"title": "[%s] NEWS: %s %s" % (tag, act, call["symbol"]), "message": "\n".join(lines),
            "actions": actions, "tags": ["rotating_light"], "priority": 5,
            **({"click": call["link"]} if call.get("link") else {})}


def post(st, body):
    """Sends one push now. Raises on any failure."""
    data = json.dumps(dict(body, topic=st["ntfy_topic"])).encode()
    req = urllib.request.Request(st["ntfy_server"], data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


def _post_quietly(st, body):
    try:
        post(st, body)
    except Exception as e:                       # a lost push must never reach the scan
        log.warning("ntfy alert not sent: %s", e)


def send_signal(sig, st, mode, demo):
    """Pushes one ready signal in the background. Never raises. Returns the thread, or None if nothing is sent."""
    try:
        if demo or not st.get("ntfy_topic"):
            return None
        t = threading.Thread(target=_post_quietly, args=(dict(st), message(sig, mode)), daemon=True)
        t.start()
        return t
    except Exception as e:
        log.warning("ntfy alert not sent: %s", e)
        return None


def send_news(call, did, st, mode, demo):
    """Pushes one breaking-news call in the background. Never raises. Returns the thread, or None."""
    try:
        if demo or not st.get("ntfy_topic"):
            return None
        t = threading.Thread(target=_post_quietly, args=(dict(st), news_message(call, did, mode)), daemon=True)
        t.start()
        return t
    except Exception as e:
        log.warning("ntfy alert not sent: %s", e)
        return None


def send_test(st, demo):
    """The Settings button: one sample alert, sent now, so you can check your phone. Raises ValueError with a reason."""
    if demo:
        raise ValueError("The demo never sends phone alerts. Run the desk without --demo to test them.")
    if not st.get("ntfy_topic"):
        raise ValueError("Set an ntfy topic in Settings and save it first.")
    try:
        post(st, message(SAMPLE, st["mode"]))
    except Exception as e:
        raise ValueError("Could not reach %s: %s" % (st["ntfy_server"], e))


# ---- memecoin paper trades (desk/meme.py calls send_meme after a run's buys and sells are saved) -------------------

MEME_LAST_LINE = "Paper trade only. These accounts lose money on average. Not advice."
MEME_RULES = {"all": "Buy everything (P0)", "hype": "Hype (P1)", "breakout": "Breakout (P2)",
              "calm_aged": "Calm aged (P3)", "dip_aged": "Aged dip (P4)", "aged_liquid": "Aged and liquid (P5)",
              "news": "News (P6)", "migration": "Migration (P7)", "migration_base": "Migration control (P7)",
              "moonshot": "Moonshot (P8)", "moonshot_base": "Moonshot control (P8)", "quick": "Quick (P9)"}
MEME_ACCOUNTS = {"all": "buy everything", "calm_aged": "calm aged", "dip_aged": "aged dip",
                 "aged_liquid": "aged and liquid"}
MEME_EXITS = {"stop": "stop", "target": "target", "trail": "trailing stop", "time": "time limit",
              "rug": "pool pulled", "liquidity pulled": "liquidity pulled"}
EXPLORERS = {"solana": ("Solscan", "https://solscan.io/token/"), "base": ("BaseScan", "https://basescan.org/token/"),
             "bsc": ("BscScan", "https://bscscan.com/token/"), "ethereum": ("Etherscan", "https://etherscan.io/token/")}

MEME_SAMPLE = {"kind": "buy", "account": "main", "symbol": "TEST", "chain": "solana",
               "token": "So11111111111111111111111111111111111111112", "pool": None, "dex": "test",
               "rule": "all", "px": 0.001234, "fill": 0.001256, "size": 20.0, "stop": 0.0010489, "target": 0.0016042,
               "liq": 100000.0, "v1h": 50000.0, "age_h": 30.0, "r1h": 2.0, "screen": "pass", "strict": "pass",
               "warning": "This is a test alert from your Trading Desk. It is not a trade."}


def _px(x):
    """A coin price with about four significant digits, never in e-notation."""
    if not x:
        return "unknown"
    return "$%.*f" % (max(2, 3 - math.floor(math.log10(abs(x)))), x)


def _big(x):
    if x is None:
        return "unknown"
    return "$%.2fM" % (x / 1e6) if x >= 1e6 else "$%.1fk" % (x / 1e3) if x >= 1e3 else "$%.0f" % x


def _age(h):
    if h is None:
        return "unknown"
    return "%d min" % round(h * 60) if h < 1 else "%.1f h" % h if h < 48 else "%.1f days" % (h / 24)


def _pct(x):
    return "unknown" if x is None else "%+.1f%%" % x


def _level(x, base):
    return "%s (%+.0f%%)" % (_px(x), (x / base - 1) * 100) if x and base else "unknown"


def meme_links(chain, token, pool):
    """(click URL, ntfy actions): DexScreener's pair page, the chain's explorer and, on Solana, RugCheck. At most 3,
    all public sites: never a link to this PC."""
    q = lambda a: urllib.parse.quote(str(a), safe="")
    dex = "https://dexscreener.com/%s/%s" % (q(chain), q(pool or token))
    actions = [{"action": "view", "label": "DexScreener", "url": dex}]
    if token and chain in EXPLORERS:
        actions.append({"action": "view", "label": EXPLORERS[chain][0], "url": EXPLORERS[chain][1] + q(token)})
    if token and chain == "solana":
        actions.append({"action": "view", "label": "RugCheck", "url": "https://rugcheck.xyz/tokens/" + q(token)})
    return dex, actions[:3]


def meme_message(ev):
    """The ntfy JSON body for one practice-account buy or sell (ev from desk/meme.py's _trade_alerts)."""
    sym = ev.get("symbol") or "?"
    acct = MEME_ACCOUNTS.get(ev["account"], ev["account"])
    if ev["kind"] == "buy":
        target = _level(ev["target"], ev.get("px")) if ev.get("target") else "none, a trailing stop"
        lines = ["Rule: %s" % MEME_RULES.get(ev.get("rule"), ev.get("rule")),
                 "Price: %s (filled at %s after costs)" % (_px(ev.get("px")), _px(ev.get("fill"))),
                 "Size: $%.2f" % ev["size"],
                 "Stop: %s · Target: %s" % (_level(ev.get("stop"), ev.get("px")), target),
                 "Pool liquidity: %s" % _big(ev.get("liq")),
                 "1h volume: %s" % _big(ev.get("v1h")),
                 "Age: %s" % _age(ev.get("age_h")),
                 "1h change: %s" % _pct(ev.get("r1h")),
                 "Safety check: %s%s" % (ev.get("screen") or "unknown",
                                         " (strict: %s)" % ev["strict"] if ev.get("strict") else "")]
        if ev.get("warning"):
            lines.append("Screen warning: %s" % ev["warning"])
        lines.append("DEX: %s" % (ev.get("dex") or "unknown"))
        title = "[PAPER MEME] BUY %s · %s · %s" % (sym, ev["chain"], acct)
        tags = ["chart_with_upwards_trend"]
    else:
        lines = ["Account: %s" % acct,
                 "Entry %s to exit %s" % (_px(ev.get("entry_px")), _px(ev.get("exit_px"))),
                 "Held: %d min" % round(ev.get("held_min") or 0),
                 "Net: %s$%.2f (%s)" % ("-" if ev["pnl"] < 0 else "+", abs(ev["pnl"]), _pct(ev["ret_pct"])),
                 "Account value after: $%.2f" % ev["balance"]]
        title = "[PAPER MEME] SELL %s · %s · %s" % (sym, MEME_EXITS.get(ev.get("why"), ev.get("why")), _pct(ev["ret_pct"]))
        tags = ["chart_with_downwards_trend" if ev["pnl"] < 0 else "chart_with_upwards_trend"]
    lines.append(MEME_LAST_LINE)
    click, actions = meme_links(ev["chain"], ev.get("token"), ev.get("pool"))
    return {"title": title, "message": "\n".join(lines), "click": click, "actions": actions, "tags": tags, "priority": 3}


class _Cap:
    """At most `limit` memecoin pushes in any hour. Over it an alert is dropped and logged; the next push allowed
    is one "N alerts skipped" summary (it takes that slot). In memory only: a restart starts the hour over."""

    def __init__(self):
        self.lock = threading.Lock()
        self.sent = collections.deque()
        self.skipped = 0

    def admit(self, bodies, limit, now):
        out = []
        with self.lock:
            for b in bodies:
                while self.sent and now - self.sent[0] >= 3600:
                    self.sent.popleft()
                if self.skipped and len(self.sent) < limit:
                    out.append({"title": "[PAPER MEME] %d alerts skipped" % self.skipped,
                                "message": "Over the cap of %d alerts an hour, %d memecoin paper alerts were not sent. "
                                           "The Memecoins tab has every trade.\n%s" % (limit, self.skipped, MEME_LAST_LINE),
                                "tags": ["mute"], "priority": 2})
                    self.sent.append(now)
                    self.skipped = 0
                if len(self.sent) >= limit:
                    self.skipped += 1
                    log.warning("memecoin alert skipped (over %d an hour): %s", limit, b.get("title"))
                    continue
                out.append(b)
                self.sent.append(now)
        return out


_meme_cap = _Cap()


def meme_topic(st):
    """meme_ntfy_topic, or ntfy_topic when it is empty."""
    return st.get("meme_ntfy_topic") or st.get("ntfy_topic") or ""


def _post_all(st, bodies):
    for b in bodies:
        _post_quietly(st, b)


def send_meme(events, st, demo, now=None):
    """Pushes a run's memecoin practice buys and sells in one background thread. Never raises. Returns the thread,
    or None if nothing is sent (meme_ntfy off, the demo, no topic, only other accounts, or over the cap)."""
    try:
        mode, topic = st.get("meme_ntfy", "off"), meme_topic(st)
        if demo or mode not in ("main", "all") or not topic:
            return None
        bodies = []
        for ev in events:
            if mode == "main" and ev.get("account") != "main":
                continue
            try:
                bodies.append(meme_message(ev))
            except Exception as e:               # one bad event never stops the others
                log.warning("memecoin alert not built: %s", e)
        bodies = _meme_cap.admit(bodies, st.get("meme_ntfy_max_per_hour", 20), time.time() if now is None else now)
        if not bodies:
            return None
        t = threading.Thread(target=_post_all, args=(dict(st, ntfy_topic=topic), bodies), daemon=True)
        t.start()
        return t
    except Exception as e:
        log.warning("memecoin alert not sent: %s", e)
        return None


def send_meme_test(st, demo):
    """The Settings button: one sample memecoin alert, now, to the memecoin topic. Raises ValueError with a reason.
    Works while meme_ntfy is off, so you can check the topic first, and never counts against the hourly cap."""
    if demo:
        raise ValueError("The demo never sends phone alerts. Run the desk without --demo to test them.")
    topic = meme_topic(st)
    if not topic:
        raise ValueError("Set an ntfy topic (or a memecoin ntfy topic) in Settings and save it first.")
    body = meme_message(MEME_SAMPLE)
    body["title"] = "[TEST] " + body["title"]
    try:
        post(dict(st, ntfy_topic=topic), body)
    except Exception as e:
        raise ValueError("Could not reach %s: %s" % (st["ntfy_server"], e))
