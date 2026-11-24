"""Seasons for the memecoin practice accounts, and read-only play-money views of the research studies.

Every practice account (the main one and each book in desk/meme.py BOOKS) has a season, 1 until its first refill.
A refill closes the current season: open positions are sold at the last sampled price with the normal costs
(meme._sell) and logged with why "season end", then the whole account (balance, trades and equity curve, untouched)
is written once to data/accounts/meme/seasons/<account>/season-<N>.json, and season N+1 starts at the starting
balance (Settings, $100) with no open positions. Nothing is ever deleted or overwritten.

A refill changes balances only. It never touches shadows.jsonl, an open shadow, the tracked coins or their cooldowns,
P0, the safety screen, the exits or the costs (docs/MEME.md sections 2, 3 and 17); it holds meme._state_lock, so it
never runs inside a pass and never makes a pass skip.

The study views (L1, C1, C1-S, D1 V1, D1 V2) are never traded by the desk. They read the studies' own append-only
logs through a handle that shares read, write and delete, closed at once, and cope with a missing or torn file. Their
seasons are only boundaries (seasons/views.json): a trade belongs to the season its entry falls in.
"""
import datetime
import json
import math
import os
import time
from pathlib import Path

from . import meme
from . import memereentry as MR
from . import settings as S
from .paths import ROOT
from .skills import read_json

VIEW_FILE = "views.json"
D1_T0 = 1795404112          # author time of commit 92be491, which added docs/MEME.md section 17 (2026-11-23 03:21:52 UTC)
D1_DAYS = 7
L1_T0 = 1795411987          # T18, docs/MEME.md section 18 (commit 0f02330)
L1_OUT = Path(os.environ.get("DESK_L1_OUT", r"C:\Trading\research\learner\out"))
COPY_HOME = Path(os.environ.get("DESK_COPY_HOME", r"C:\Trading\research\copy"))
D1_LOCK = Path(os.environ.get("DESK_D1_LOCK", ROOT / "research" / "downside" / "out" / "forward.lock"))
D1_HIDDEN = "Results hidden until the D1 read (after 2026-11-30 07:24 UTC)"
COPY_SIZE = 20.0            # section 19.B: $20 a copied buy
CURVE_MAX = 400             # points sent to the page per season

BOOK_LABELS = {"main": "Main account", "all": "Buy everything", "careful": "Careful", "hype": "Hype",
               "breakout": "Breakout", "calm_aged": "Calm, 1-7 days", "dip_aged": "Dip, 1-7 days",
               "aged_liquid": "Aged and liquid", "news": "News or trend", "migration": "Migration (P7)",
               "moonshot": "Moonshot (P8)", "quick": "Quick (P9)"}
VIEWS = {
    "l1": {"label": "L1 learner", "study": "docs/MEME.md section 18",
           "starts": "Starts when its study starts: the learner's first scored take after its 300-close warm-up."},
    "c1": {"label": "C1 copy", "study": "docs/MEME.md section 19",
           "starts": "Starts when its study starts: the commit of WALLETS-C1.json and the follower's first copied buy."},
    "c1s": {"label": "C1-S copy", "study": "docs/MEME.md section 19",
            "starts": "Starts when its study starts: the commit of WALLETS-C1S.json and the follower's first copied buy."},
    "d1v1": {"label": "D1 V1 run-up veto", "study": "docs/MEME.md section 17",
             "starts": "Starts when its study starts: P0 entries after 2026-11-23 03:21:52 UTC, skipping r1h >= +5."},
    "d1v2": {"label": "D1 V2 sell-pressure veto", "study": "docs/MEME.md section 17",
             "starts": "Starts when its study starts: P0 entries after 2026-11-23 03:21:52 UTC, skipping buy_ratio_m5 < 0.50."},
}


class SeasonError(Exception):
    pass


def book_names():
    return ("main",) + tuple(n for n, _, _ in meme.BOOKS)


def folder():
    return meme.folder() / "seasons"


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _write_new(path, obj):
    """Write a file that does not exist yet, atomically (temp file, fsync, replace). Never overwrites."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise SeasonError("%s already exists" % path.name)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(obj, indent=1) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _write_replace(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(obj, indent=1) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _archive_path(name, season, now):
    base = folder() / name / ("season-%d.json" % season)
    # a crash after the archive but before model.json saved leaves season N in place: a later refill of the
    # same season writes a second, dated file, and the newest one is read (nothing is overwritten)
    return base if not base.exists() else base.with_name("season-%d.%d.json" % (season, int(now * 1000)))


def _get(s, name):
    return s.get("account") if name == "main" else (s.get("books") or {}).get(name)


def _put(s, name, acct):
    if name == "main":
        s["account"] = acct
    else:
        s.setdefault("books", {})[name] = acct


def _close_season(st, name, acct, tracked, now, reason):
    """Sell what is open at the last price with the normal costs, write the archive. Returns (archive, new account)."""
    season = int(acct.get("season") or 1)
    ended = []
    for pos in list(acct["positions"]):
        t = tracked.get(pos["key"]) or {}
        meme._sell(st, acct, pos, t.get("px"), t.get("liq"), now, "season end")
        row = acct["closed"][-1]
        if row["why"] == "rug":                        # no price, or a dead pool: valued at 0, still a season end
            row["why"] = "season end"
            row["season_end_note"] = "no price" if not t.get("px") else "pool under the dead-liquidity floor"
        ended.append(row)
    eq = round(acct["cash"], 4)
    curve = list(acct.get("curve") or [])
    curve.append({"ts": now, "at": _iso(now), "equity": eq})
    closed = acct["closed"]
    archive = {"account": name, "season": season, "reason": reason, "start": acct["start"],
               "started": acct.get("started"), "ended": _iso(now), "ended_ts": now, "cash": eq, "equity": eq,
               "trades": len(closed), "wins": sum(1 for c in closed if c["pnl"] > 0),
               "closed": closed, "curve": curve, "season_end_closes": len(ended),
               "extra": {k: v for k, v in acct.items() if k not in ("start", "cash", "positions", "closed", "curve",
                                                                     "started", "season", "_nc")}}
    _write_new(_archive_path(name, season, now), archive)
    fresh = dict(meme._new_account(st["meme_starting_balance"]), season=season + 1, started=_iso(now))
    return archive, fresh, ended


def _refill_locked(names, now, reason):
    st, s = S.load(), meme.load()
    tracked = s.get("tracked") or {}
    out = []
    for name in names:
        acct = _get(s, name)
        if not acct:                                   # never started: season 1 begins at the next pass
            out.append({"account": name, "season": None, "archived": False})
            continue
        archive, fresh, ended = _close_season(st, name, acct, tracked, now, reason)
        _put(s, name, fresh)
        out.append({"account": name, "season": archive["season"], "archived": True, "balance": archive["cash"],
                    "season_end_closes": len(ended), "next_season": fresh["season"]})
        if name == "careful":                          # the paired re-entry study (off by default) sees these closes
            study = s.get(MR.STATE_KEY)
            if isinstance(study, dict) and "start_ts" in study:
                try:
                    MR.observe(study, st, now, [], ended, {})
                except Exception as err:
                    study["error"] = "%s: %s: %s" % (_iso(now), type(err).__name__, err)
        if name == "main" and isinstance(s.get("practice_drawdown"), dict):
            # the main-account companion copies main's fills, so its open copies could never close: archive it too
            # (as its own account); the next pass starts it fresh when it is enabled
            pd = s.pop("practice_drawdown")
            pd.setdefault("closed", [])
            _close_season(st, "practice_drawdown", pd, tracked, now, reason)
    meme._save(s)
    return out


def refill(names=None, now=None, timeout=60):
    """Close the season of each named practice account (all of them by default) and start the next at the
    starting balance. Raises SeasonError for an unknown name, meme.MemeError if a pass holds the lock too long."""
    names = list(book_names()) if names is None else list(names)
    bad = [n for n in names if n not in book_names()]
    if bad:
        raise SeasonError("Not a practice account: %s" % ", ".join(bad))
    if not meme._state_lock.acquire(timeout=timeout):
        raise meme.MemeError("A memecoin pass is running, try again in a minute.")
    try:
        return _refill_locked(names, time.time() if now is None else now, "refill")
    finally:
        meme._state_lock.release()


def refill_view(names, now=None):
    """A study view's refill is a season boundary only (the desk never trades it or touches its logs)."""
    bad = [n for n in names if n not in VIEWS]
    if bad:
        raise SeasonError("Not a study view: %s" % ", ".join(bad))
    now = time.time() if now is None else now
    with meme._append_lock:
        b = _view_bounds()
        for n in names:
            b.setdefault(n, []).append(now)
        _write_replace(folder() / VIEW_FILE, b)
    return [{"account": n, "season": len(b[n]), "next_season": len(b[n]) + 1} for n in names]


def _view_bounds():
    b = read_json(folder() / VIEW_FILE, {}) or {}
    return {k: sorted(float(x) for x in v if isinstance(x, (int, float))) for k, v in b.items() if isinstance(v, list)}


def archives(name):
    """{season: archive} for one account; when a season has two files (see _archive_path) the newest wins."""
    out = {}
    d = folder() / name
    if not d.is_dir():
        return out
    for f in sorted(d.glob("season-*.json")):
        a = read_json(f, None)
        if isinstance(a, dict) and isinstance(a.get("season"), int):
            if a["season"] not in out or a.get("ended_ts", 0) >= out[a["season"]].get("ended_ts", 0):
                out[a["season"]] = a
    return out


# ---- summaries for the page ---------------------------------------------------------------------------------------

def _thin(curve, n=CURVE_MAX):
    if len(curve) <= n:
        return curve
    step = len(curve) / n
    return [curve[int(i * step)] for i in range(n - 1)] + [curve[-1]]


def _summary(n, start, equity, closed, open_n, curve, started, ended=None, **extra):
    wins = sum(1 for c in closed if c["pnl"] > 0)
    return dict({"n": n, "start": start, "equity": round(equity, 2), "pnl": round(equity - start, 2),
                 "pnl_pct": round((equity / start - 1) * 100, 2) if start else None, "trades": len(closed),
                 "wins": wins, "win_rate": round(wins / len(closed) * 100, 1) if closed else None, "open": open_n,
                 "started": started, "ended": ended,
                 "curve": [{"ts": c["ts"], "equity": round(c["equity"], 2)} for c in _thin(curve)]}, **extra)


def book_cards(st=None, s=None):
    st, s = st or S.load(), s or meme.load()
    tracked = s.get("tracked") or {}
    cards = []
    for name in book_names():
        seasons = [_summary(n, a["start"], a["equity"], a["closed"], 0, a.get("curve") or [], a.get("started"),
                            a.get("ended"), season_end_closes=a.get("season_end_closes", 0))
                   for n, a in sorted(archives(name).items())]
        acct = _get(s, name)
        if acct:
            cur = int(acct.get("season") or 1)
            seasons.append(_summary(cur, acct["start"], meme.equity(st, acct, tracked), acct["closed"],
                                    len(acct["positions"]), acct.get("curve") or [], acct.get("started")))
        cards.append({"name": name, "label": BOOK_LABELS.get(name, name), "kind": "book", "refillable": True,
                      "seasons": seasons, "note": None if acct else "No pass has started this account yet."})
    return cards


# ---- the study views ----------------------------------------------------------------------------------------------

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.HANDLE)
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.GetFileSizeEx.argtypes = (wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong))
    _k32.ReadFile.argtypes = (wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                              wintypes.LPVOID)
    _k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _INVALID = wintypes.HANDLE(-1).value


def read_shared(path):
    """The whole file through a read-only handle that shares read, write and delete (Python's open() does not share
    delete), closed at once, so the study's own append or atomic replace never fails because of the desk. b"" if the
    file is missing."""
    path = os.fspath(Path(path).absolute())
    if os.name != "nt":
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except FileNotFoundError:
            return b""
    h = _k32.CreateFileW(path, 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
    if h in (None, _INVALID):
        err = ctypes.get_last_error()
        if err in (2, 3):                               # file or folder not found
            return b""
        raise ctypes.WinError(err)
    try:
        size = ctypes.c_longlong()
        if not _k32.GetFileSizeEx(h, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        chunks, left, got = [], size.value, wintypes.DWORD()
        buf = ctypes.create_string_buffer(1 << 20)
        while left > 0:
            if not _k32.ReadFile(h, buf, min(left, 1 << 20), ctypes.byref(got), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if not got.value:
                break
            chunks.append(buf.raw[:got.value])
            left -= got.value
        return b"".join(chunks)
    finally:
        _k32.CloseHandle(h)


def read_jsonl(path):
    """Complete JSON lines of an append-only log: a torn last line (no newline yet) and bad lines are skipped."""
    try:
        raw = read_shared(path)
    except OSError:
        return []
    end = raw.rfind(b"\n")
    rows = []
    for ln in raw[:end + 1].split(b"\n") if end >= 0 else []:
        if not ln.strip():
            continue
        try:
            r = json.loads(ln.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, RecursionError):
            continue
        if isinstance(r, dict):
            rows.append(r)
    return rows


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def simulate(trades, start, st, pct=None, max_open=None, cut=None, upto=None):
    """Play money for a list of {key, ts, exit_ts|None, net_pct|None, liq|None}, sized like the books: the account's
    value (open trades at cost) x meme_position_pct, at most meme_max_open open, one per coin. net_pct is the study's
    own recorded result, already after the section 2 round-trip cost; price impact (size / half the entry pool, each
    side) is taken off when the entry liquidity is known. Only entries in [cut, upto) count; a trade still open at
    `upto` ends the season at cost ("season end": the study logs give no price)."""
    pct = st["meme_position_pct"] if pct is None else pct
    max_open = st["meme_max_open"] if max_open is None else max_open
    ev = []
    for i, t in enumerate(trades):
        if (cut is not None and t["ts"] < cut) or (upto is not None and t["ts"] >= upto):
            continue
        ev.append((t["ts"], 1, i))
        if t.get("exit_ts") is not None and t.get("net_pct") is not None:
            ev.append((t["exit_ts"], 0, i))           # exits before entries at the same time
    ev.sort()
    cash, held, closed, curve = start, {}, [], [{"ts": cut or (ev[0][0] if ev else 0), "equity": start}]
    for ts, kind, i in ev:
        t = trades[i]
        if upto is not None and ts >= upto:
            break
        if kind == 1:
            if len(held) >= max_open or any(trades[j]["key"] == t["key"] for j in held):
                continue
            size = min((cash + sum(held.values())) * pct / 100, cash)
            if size >= 1:
                cash -= size
                held[i] = size
        elif i in held:
            size = held.pop(i)
            liq = _num(t.get("liq"))
            imp = size / (liq / 2) if liq and liq > 0 else 0.0
            back = max(0.0, size * (1 + t["net_pct"] / 100) * (1 - imp) / (1 + imp))
            cash += back
            closed.append({"key": t["key"], "ts": t["ts"], "exit_ts": ts, "pnl": round(back - size, 4),
                           "ret_pct": round((back / size - 1) * 100, 2), "why": t.get("why")})
            curve.append({"ts": ts, "equity": round(cash + sum(held.values()), 4)})
    if upto is not None:
        for i, size in list(held.items()):             # season end: valued at cost
            cash += held.pop(i)
            closed.append({"key": trades[i]["key"], "ts": trades[i]["ts"], "exit_ts": upto, "pnl": 0.0,
                           "ret_pct": 0.0, "why": "season end", "season_end_note": "no price in the study log; at cost"})
        curve.append({"ts": upto, "equity": round(cash, 4)})
    return cash + sum(held.values()), closed, len(held), curve


def _fixed_copy(trades, start, cut=None, upto=None):
    """Section 19.B: every copied buy is $20, as the follower took it (at most 10 open a arm, enforced there)."""
    cash, closed, open_n, pts = start, [], 0, []
    for t in sorted(trades, key=lambda x: x["ts"]):
        if (cut is not None and t["ts"] < cut) or (upto is not None and t["ts"] >= upto):
            continue
        done = t.get("net_pct") is not None and (upto is None or t["exit_ts"] < upto)
        if done:
            pnl = COPY_SIZE * t["net_pct"] / 100
            cash += pnl
            closed.append({"key": t["key"], "ts": t["ts"], "exit_ts": t["exit_ts"], "pnl": round(pnl, 4),
                           "ret_pct": round(t["net_pct"], 2), "why": t.get("why")})
            pts.append((t["exit_ts"], pnl))
        elif upto is not None:
            closed.append({"key": t["key"], "ts": t["ts"], "exit_ts": upto, "pnl": 0.0, "ret_pct": 0.0,
                           "why": "season end", "season_end_note": "no price in the study log; at cost"})
        else:
            open_n += 1
    eq, curve = start, [{"ts": cut or 0, "equity": start}]
    for ts, pnl in sorted(pts):
        eq += pnl
        curve.append({"ts": ts, "equity": round(eq, 4)})
    return cash, closed, open_n, curve


def l1_trades(out_dir=None):
    """L1's scored takes (not warm-up) with their closes; a void close (predicted after the exit) is not a trade."""
    out_dir = Path(out_dir or L1_OUT)
    closes = {}
    for c in read_jsonl(out_dir / "closes.jsonl"):
        if _num(c.get("entry_ts")) is not None and "key" in c:
            closes.setdefault("%s|%r" % (c["key"], float(c["entry_ts"])), c)
    rows = []
    for p in read_jsonl(out_dir / "predictions.jsonl"):
        if not p.get("take") or p.get("warmup") or _num(p.get("entry_ts")) is None or "key" not in p:
            continue
        c = closes.get("%s|%r" % (p["key"], float(p["entry_ts"])))
        if c is not None and c.get("status") != "predicted":
            continue
        raw = (p.get("features") or {}).get("raw") or {}
        rows.append({"key": p["key"], "ts": float(p["entry_ts"]), "liq": raw.get("liq"),
                     "exit_ts": _num((c or {}).get("exit_ts")), "net_pct": _num((c or {}).get("net_pct"))})
    return rows


def copy_trades(arm, home=None):
    """One arm's copied buys from the follower's logs: fills.jsonl buy legs, closes.jsonl net per buy."""
    home = Path(home or COPY_HOME)
    closes = {c["id"]: c for c in read_jsonl(home / "closes.jsonl") if c.get("arm") == arm and "id" in c}
    rows = []
    for f in read_jsonl(home / "fills.jsonl"):
        if f.get("leg") != "buy" or f.get("arm") != arm or _num(f.get("t")) is None:
            continue
        c = closes.get(f.get("id")) or {}
        rows.append({"key": f.get("mint"), "ts": f["t"], "exit_ts": _num(c.get("exit_t")),
                     "net_pct": _num(c.get("net_pct")), "why": c.get("why")})
    return rows


def d1_trades(veto, rows=None, open_shadows=None):
    """P0's own entries in D1's population (policy all, screen pass, entry after D1_T0), minus the veto's skips.
    Duplicate closes keep the first (section 17.1). A missing feature is no veto. Open shadows are open trades."""
    rows = meme._jsonl("shadows.jsonl") if rows is None else rows
    open_shadows = (meme.load().get("shadows") or []) if open_shadows is None else open_shadows
    first = {}
    for r in rows:
        if r.get("policy") != "all" or r.get("screen") != "pass" or _num(r.get("ts")) is None or r["ts"] <= D1_T0:
            continue
        k = (r.get("key"), float(r["ts"]))
        if k not in first or (_num(r.get("exit_ts")) or 0) < (_num(first[k].get("exit_ts")) or 0):
            first[k] = r
    seen = set(first)
    for sh in open_shadows:
        if (sh.get("policy") == "all" and sh.get("screen") == "pass" and _num(sh.get("ts")) is not None
                and sh["ts"] > D1_T0 and (sh.get("key"), float(sh["ts"])) not in seen):
            first[(sh.get("key"), float(sh["ts"]))] = dict(sh, exit_ts=None, net_pct=None)
    out = []
    for r in first.values():
        f = r.get("f") or {}
        if veto == "v1" and _num(f.get("r1h")) is not None and f["r1h"] >= 5:
            continue
        if veto == "v2" and _num(f.get("buy_ratio_m5")) is not None and f["buy_ratio_m5"] < 0.5:
            continue
        out.append({"key": r["key"], "ts": float(r["ts"]), "liq": f.get("liq"), "exit_ts": _num(r.get("exit_ts")),
                    "net_pct": _num(r.get("net_pct")), "why": r.get("why")})
    return out


def d1_revealed():
    """D1's forward window is read once (docs/MEME.md section 17); its cards show numbers only after that read
    has written research/downside/out/forward.lock."""
    return D1_LOCK.exists()


def view_cards(st=None, s=None):
    st, s = st or S.load(), s or meme.load()
    start = st["meme_starting_balance"]
    bounds = _view_bounds()
    shadow_rows = None
    cards = []
    for name, meta in VIEWS.items():
        if name.startswith("d1") and not d1_revealed():
            # nothing is computed or read for a hidden card: no balance, trade count, curve or season list
            cards.append({"name": name, "label": meta["label"], "kind": "view", "refillable": False,
                          "study": meta["study"], "hidden": True, "seasons": [], "note": D1_HIDDEN})
            continue
        note = None
        try:
            if name == "l1":
                trades, fixed = l1_trades(), False
            elif name in ("c1", "c1s"):
                trades, fixed = copy_trades("C1" if name == "c1" else "C1S"), True
            else:
                if shadow_rows is None:
                    shadow_rows = meme._jsonl("shadows.jsonl")
                trades, fixed = d1_trades(name[2:], shadow_rows, s.get("shadows") or []), False
        except Exception as err:                        # a view never breaks the page
            trades, fixed, note = [], False, "Could not read the study's log: %s" % type(err).__name__
        cuts = [None] + bounds.get(name, [])
        seasons = []
        for i, cut in enumerate(cuts):
            upto = cuts[i + 1] if i + 1 < len(cuts) else None
            eq, closed, open_n, curve = (_fixed_copy(trades, start, cut, upto) if fixed
                                         else simulate(trades, start, st, cut=cut, upto=upto))
            seasons.append(_summary(i + 1, start, eq, closed, open_n, curve, _iso(cut) if cut else None,
                                    _iso(upto) if upto else None))
        if not trades and note is None:
            note = meta["starts"]
        cards.append({"name": name, "label": meta["label"], "kind": "view", "refillable": True, "study": meta["study"],
                      "seasons": seasons, "note": note, "starts": meta["starts"],
                      "sizing": ("$%.0f a copied buy, as section 19.B" % COPY_SIZE) if fixed else
                      "%g%% a position, at most %d open, the study's net after cost and price impact"
                      % (st["meme_position_pct"], st["meme_max_open"])})
    return cards


def view():
    """What /api/meme/accounts returns. Reads files only: no network, no write."""
    st, s = S.load(), meme.load()
    return {"start": st["meme_starting_balance"], "accounts": book_cards(st, s) + view_cards(st, s),
            "d1_window_ends": _iso(D1_T0 + D1_DAYS * 86400)}
