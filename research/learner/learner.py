"""L1: a continuously learning model on P0's entries (docs/MEME.md section 18). Paper only. Stdlib only.

L1 has no order path. It reads the desk's folder read-only (model.json for open shadows, new complete lines of shadows.jsonl for
closes) and never writes it. Everything it writes goes under HOME (C:\\Trading\\research\\learner): out/ (logs, weight snapshots,
looks) and state/ (its own state, written atomically).

  python research/learner/learner.py run              # poll every 30 s, forever (run_learner.ps1 starts this)
  python research/learner/learner.py once             # one poll
  python research/learner/learner.py verify           # check the hash chains of out/predictions.jsonl and out/closes.jsonl
  python research/learner/learner.py status           # live readout: DESCRIPTIVE ONLY, never a verdict
  python research/learner/learner.py look 150         # a formal look (150, 300, 600 or 1200 scored takes); runs once

Fields read (as desk/meme.py writes them): model.json "shadows" (key, chain, policy, screen, ts, f) and "tracked"[key]["dex"];
shadows.jsonl (key, chain, policy, screen, ts, f, exit_ts, net_pct). Exit fields are read only from shadows.jsonl lines, at a close.
"""
import argparse
import hashlib
import json
import math
import os
import random
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(r"C:\Users\Willr\trading-desk\data\accounts\meme")      # the live desk's folder: read only
HOME = Path(r"C:\Trading\research\learner")

# ---- frozen by docs/MEME.md section 18 (changing any of these makes a new study, L2)
T18 = 1795411987                       # author time of commit 0f02330, which added section 18 (2026-11-23 05:33:07 UTC)
POLICY, SCREEN = "all", "pass"
POLL_S = 30
NUMERIC = ("r5", "r1h", "r6h", "r24h", "v1h", "vacc", "rvol", "tx_m5", "buy_ratio_m5", "liq", "age_h")
LOGGED = ("v1h", "liq", "age_h")
CHAINS = ("solana", "ethereum", "base", "bsc")                           # + other
DEXES = ("pumpswap", "raydium", "uniswap", "pumpfun", "pancakeswap", "meteora")   # + other, + missing
LR, L2, CLIP = 0.01, 1e-4, 30.0
ZCAP = 5.0                             # 18.1: standardised features capped to [-ZCAP, +ZCAP]; the step is normalised by 1 + ||x||^2
WARMUP = 300
LOOKS = (150, 300, 600, 1200)
ALPHA_LOOK = 0.05 / 4
BLOCK_S = 6 * 3600
SEED, DRAWS, BOOT = 18, 1000, 10000
FLOOR_TAKES, FLOOR_COINS = 100, 50
EXIT_FIELDS = ("exit_at", "exit_ts", "exit_px", "why", "net_pct", "peak", "checked")
DIM = len(NUMERIC) * 2 + len(CHAINS) + 1 + len(DEXES) + 2
ZERO = "0" * 64


class LearnerError(Exception):
    pass


# ---------------------------------------------------------------- features

def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def raw_features(shadow, dex):
    """Entry-time inputs only: the 11 numeric fields of f (transformed), chain, dex. Nothing else of the shadow is read."""
    f = shadow.get("f") or {}
    nums = {}
    for k in NUMERIC:
        v = _num(f.get(k))
        if v is not None and k in LOGGED:
            v = math.log1p(max(v, 0.0))
        nums[k] = v
    return {"num": nums, "chain": shadow.get("chain"), "dex": dex if isinstance(dex, str) and dex else None}


def new_model():
    return {"w": [0.0] * DIM, "b": 0.0, "n": 0, "stats": {k: [0, 0.0, 0.0] for k in NUMERIC}}


def vector(raw, stats):
    """Standardised vector: 11 z-scores capped to [-5, +5] (missing = 0), 11 missing flags, 5 chain one-hot, 8 dex one-hot."""
    z, miss = [], []
    for k in NUMERIC:
        v = raw["num"].get(k)
        n, mean, m2 = stats[k]
        sd = math.sqrt(m2 / (n - 1)) if n >= 2 else 0.0
        zv = (v - mean) / sd if v is not None and sd > 0 else 0.0
        z.append(max(-ZCAP, min(ZCAP, zv)))                         # 18.1: capped to [-5, +5]
        miss.append(1.0 if v is None else 0.0)
    ch = [1.0 if raw["chain"] == c else 0.0 for c in CHAINS] + [1.0 if raw["chain"] not in CHAINS else 0.0]
    d = raw["dex"]
    dx = [1.0 if d == c else 0.0 for c in DEXES] + [1.0 if d is not None and d not in DEXES else 0.0, 1.0 if d is None else 0.0]
    return z + miss + ch + dx


def predict(model, x):
    return sum(a * b for a, b in zip(model["w"], x)) + model["b"]


def train(model, raw, net_pct):
    """One normalised SGD step (18.1): the close's values enter the running statistics, then x is built, then
    w <- w + lr * (err * x / (1 + ||x||^2) - l2 * w), b <- b + lr * err / (1 + ||x||^2), with err = y - (w.x + b)."""
    for k in NUMERIC:
        v = raw["num"].get(k)
        if v is None:
            continue
        st = model["stats"][k]
        st[0] += 1
        d = v - st[1]
        st[1] += d / st[0]
        st[2] += d * (v - st[1])
    x = vector(raw, model["stats"])
    y = max(-CLIP, min(CLIP, float(net_pct)))
    err = y - predict(model, x)
    g = err / (1.0 + sum(xi * xi for xi in x))
    model["w"] = [w + LR * (g * xi - L2 * w) for w, xi in zip(model["w"], x)]
    model["b"] += LR * g
    model["n"] += 1
    if not all(math.isfinite(w) for w in model["w"] + [model["b"]]):
        raise LearnerError("weights are no longer finite after step %d" % model["n"])


def weight_hash(model):
    blob = json.dumps({"w": model["w"], "b": model["b"], "stats": model["stats"]}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ---------------------------------------------------------------- files

def sha(b):
    return hashlib.sha256(b).hexdigest()


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def last_line(path):
    """Bytes of the last complete line (without newline); drops a partial last line left by a crash (we own this file)."""
    path = Path(path)
    if not path.exists():
        return None
    with open(path, "r+b") as fh:
        size = fh.seek(0, os.SEEK_END)
        if not size:
            return None
        back = min(size, 1 << 20)
        fh.seek(size - back)
        tail = fh.read(back)
        if not tail.endswith(b"\n"):
            cut = tail.rfind(b"\n")
            fh.truncate(size - back + cut + 1 if cut >= 0 else size - back)
            tail = tail[:cut + 1] if cut >= 0 else b""
        lines = tail.rstrip(b"\n").split(b"\n")
        return lines[-1] if lines and lines[-1] else None


def population(r):
    return r.get("policy") == POLICY and r.get("screen") == SCREEN and _num(r.get("ts")) is not None and r["ts"] > T18


def sid(r):
    return "%s|%r" % (r["key"], float(r["ts"]))


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.HANDLE)
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.GetFileSizeEx.argtypes = (wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong))
    _k32.SetFilePointerEx.argtypes = (wintypes.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), wintypes.DWORD)
    _k32.ReadFile.argtypes = (wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID)
    _k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _GENERIC_READ, _SHARE_ALL, _OPEN_EXISTING, _NORMAL = 0x80000000, 0x1 | 0x2 | 0x4, 3, 0x80
    _INVALID = wintypes.HANDLE(-1).value


class _SharedHandle:
    """A read-only handle. On Windows it is opened with FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE (Python's
    open() does not share delete, so a replace of the file by another process would fail with PermissionError)."""

    def __init__(self, path):
        self.path = os.fspath(Path(path).absolute())
        if os.name == "nt":
            h = _k32.CreateFileW(self.path, _GENERIC_READ, _SHARE_ALL, None, _OPEN_EXISTING, _NORMAL, None)
            if h in (None, _INVALID):
                raise ctypes.WinError(ctypes.get_last_error())
            self.h = h
        else:
            self.fh = open(self.path, "rb")

    def size(self):
        if os.name != "nt":
            return os.fstat(self.fh.fileno()).st_size
        n = ctypes.c_longlong()
        if not _k32.GetFileSizeEx(self.h, ctypes.byref(n)):
            raise ctypes.WinError(ctypes.get_last_error())
        return n.value

    def read(self, offset, n):
        if os.name != "nt":
            self.fh.seek(offset)
            return self.fh.read(n)
        if not _k32.SetFilePointerEx(self.h, offset, None, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        chunks, left = [], n
        buf = ctypes.create_string_buffer(1 << 20)
        got = wintypes.DWORD()
        while left > 0:
            if not _k32.ReadFile(self.h, buf, min(left, 1 << 20), ctypes.byref(got), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if not got.value:
                break
            chunks.append(buf.raw[:got.value])
            left -= got.value
        return b"".join(chunks)

    def close(self):
        if os.name == "nt":
            if self.h is not None:
                _k32.CloseHandle(self.h)
                self.h = None
        else:
            self.fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def read_shared(path, offset=0):
    """(size, bytes from offset to the end), read through a shared handle that is closed at once."""
    with _SharedHandle(path) as h:
        size = h.size()
        return size, (h.read(offset, size - offset) if size > offset else b"")


# ---------------------------------------------------------------- the learner

class Learner:
    def __init__(self, data=DATA, home=HOME, clock=time.time):
        self.data, self.home, self.clock = Path(data), Path(home), clock
        self.out, self.sdir = self.home / "out", self.home / "state"
        self.preds, self.closes = self.out / "predictions.jsonl", self.out / "closes.jsonl"
        self.state_f = self.sdir / "state.json"
        self.state = self._load()
        self.flush()

    def _load(self):
        if self.state_f.exists():
            s = json.loads(self.state_f.read_text(encoding="utf8"))
            if s.get("t18") != T18:
                raise LearnerError("state belongs to another study (t18=%s)" % s.get("t18"))
            return s
        return {"t18": T18, "offset": 0, "pending": {}, "done": {}, "model": new_model(),
                "counts": {"predicted": 0, "void": 0, "missed": 0, "closes": 0, "scored": 0, "scored_takes": 0},
                "tips": {"predictions": ZERO, "closes": ZERO}, "outbox": [], "snapshot_day": None, "error": None}

    def save(self):
        atomic_write(self.state_f, json.dumps(self.state, separators=(",", ":")))

    # -- the outbox: lines are put in the state (saved atomically) before they are appended, so a crash can
    #    neither lose nor repeat a line: on restart, lines already in the log (found by its last line's hash) are skipped.
    def _emit(self, log, row):
        row = dict(row, prev=self.state["tips"][log])
        line = json.dumps(row, separators=(",", ":"), sort_keys=True)
        self.state["tips"][log] = sha(line.encode())
        self.state["outbox"].append([log, line])

    def flush(self):
        box = self.state["outbox"]
        if not box:
            return
        for log in ("predictions", "closes"):
            items = [line for lg, line in box if lg == log]
            if not items:
                continue
            path = self.preds if log == "predictions" else self.closes
            path.parent.mkdir(parents=True, exist_ok=True)
            tip = last_line(path)
            tip_h = sha(tip) if tip is not None else ZERO
            hashes = [sha(x.encode()) for x in items]
            if tip_h in hashes:
                items = items[hashes.index(tip_h) + 1:]
            elif json.loads(items[0])["prev"] != tip_h:
                raise LearnerError("%s: its last line is not the one the next line chains to; check `verify`" % path.name)
            if items:
                with open(path, "ab") as fh:
                    fh.write(b"".join(x.encode() + b"\n" for x in items))
                    fh.flush()
                    os.fsync(fh.fileno())
        self.state["outbox"] = []
        self.save()

    def _read_inputs(self):
        """(model.json dict, new complete shadows.jsonl lines, new offset). Both files are read through a handle that shares
        read, write and delete, and closed at once, so the desk's atomic replace of model.json never fails because of us."""
        mj = json.loads(read_shared(self.data / "model.json")[1].decode("utf8"))
        off = self.state["offset"]
        try:
            size, buf = read_shared(self.data / "shadows.jsonl", off)
        except FileNotFoundError:
            return mj, [], off
        if size < off:
            raise LearnerError("shadows.jsonl shrank (%d < offset %d)" % (size, off))
        end = buf.rfind(b"\n")
        if end < 0:
            return mj, [], off
        out = []
        for ln in buf[:end].split(b"\n"):
            try:
                out.append(json.loads(ln.decode("utf8")))
            except ValueError:
                continue
        return mj, out, off + end + 1

    def poll(self):
        s, m = self.state, self.state["model"]
        try:
            mj, lines, new_off = self._read_inputs()
        except (OSError, ValueError) as e:   # any read error: skip the whole cycle (offset unchanged) and log it
            s["read_skips"] = s.get("read_skips", 0) + 1
            print("%s read skipped (%s): %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), type(e).__name__, e), flush=True)
            return s["counts"]
        if not isinstance(mj, dict):
            return s["counts"]
        tracked = mj.get("tracked") or {}
        # 1. opens: a population shadow seen open for the first time is predicted at once
        for sh in mj.get("shadows") or []:
            if not isinstance(sh, dict) or not population(sh):
                continue
            i = sid(sh)
            if i in s["pending"] or i in s["done"]:
                continue
            raw = raw_features(sh, (tracked.get(sh["key"]) or {}).get("dex"))
            x = vector(raw, m["stats"])
            pred = predict(m, x)
            warm = s["counts"]["closes"] < WARMUP
            now = self.clock()
            row = {"key": sh["key"], "entry_ts": sh["ts"], "pred_ts": now, "features": {"raw": raw, "x": x},
                   "pred": pred, "take": pred > 0, "warmup": warm, "model_version": m["n"], "weight_hash": weight_hash(m)}
            self._emit("predictions", row)
            s["pending"][i] = {"pred_ts": now, "raw": raw, "take": pred > 0, "warmup": warm}
            s["counts"]["predicted"] += 1
        # 2. closes: new complete lines of shadows.jsonl
        s["offset"] = new_off
        for r in lines:
            if not isinstance(r, dict) or not population(r) or "key" not in r:
                continue
            i = sid(r)
            if i in s["done"]:
                continue
            p = s["pending"].pop(i, None)
            if p is None:
                status, raw = "missed", raw_features(r, (tracked.get(r["key"]) or {}).get("dex"))
                s["counts"]["missed"] += 1
            else:
                raw = p["raw"]
                status = "void" if p["pred_ts"] >= r["exit_ts"] else "predicted"
                s["counts"]["void"] += status == "void"
                if status == "predicted" and not p["warmup"]:
                    s["counts"]["scored"] += 1
                    s["counts"]["scored_takes"] += bool(p["take"])
            train(m, raw, r["net_pct"])
            s["counts"]["closes"] += 1
            s["done"][i] = status
            self._emit("closes", {"key": r["key"], "entry_ts": r["ts"], "exit_ts": r["exit_ts"], "net_pct": r["net_pct"],
                                  "status": status, "model_version": m["n"], "weight_hash": weight_hash(m)})
        # 3. one weight snapshot per UTC day
        day = time.strftime("%Y-%m-%d", time.gmtime(self.clock()))
        if s["snapshot_day"] != day:
            atomic_write(self.out / "weights" / (day + ".json"), json.dumps(dict(m, hash=weight_hash(m), day=day), indent=1))
            s["snapshot_day"] = day
        s["heartbeat"] = self.clock()
        self.save()
        self.flush()
        return s["counts"]


# ---------------------------------------------------------------- checks and readouts

def verify(path):
    """(lines, None) when the chain holds, else (line number, reason) of the first break."""
    prev, n = ZERO, 0
    if not Path(path).exists():
        return 0, None
    with open(path, "rb") as fh:
        for n, ln in enumerate(fh, 1):
            ln = ln.rstrip(b"\n")
            try:
                row = json.loads(ln)
            except ValueError:
                return n, "not JSON"
            if row.get("prev") != prev:
                return n, "prev does not match the line before"
            prev = sha(ln)
    return n, None


def _rows(path):
    if not Path(path).exists():
        return []
    return [json.loads(x) for x in Path(path).read_text(encoding="utf8").splitlines() if x.strip()]


def scored_signals(home=HOME):
    """Scored signals in prediction order: not warm-up, closed, not void. Returns (signals, open scored predictions)."""
    out = Path(home) / "out"
    closes = {"%s|%r" % (c["key"], float(c["entry_ts"])): c for c in _rows(out / "closes.jsonl")}
    sigs, waiting = [], []
    for p in _rows(out / "predictions.jsonl"):
        if p["warmup"]:
            continue
        c = closes.get("%s|%r" % (p["key"], float(p["entry_ts"])))
        if c is None:
            waiting.append(p)
            sigs.append({"key": p["key"], "ts": p["entry_ts"], "take": p["take"], "net": None})
        elif c["status"] == "predicted":
            sigs.append({"key": p["key"], "ts": p["entry_ts"], "take": p["take"], "net": c["net_pct"]})
    return sigs, waiting


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def expected_random(sigs):
    """The random take's expected mean: in each 6-hour block, its mean net times L1's take count there, over all takes."""
    blocks = {}
    for s in sigs:
        blocks.setdefault(int(s["ts"] // BLOCK_S), []).append(s)
    k = sum(s["take"] for s in sigs)
    if not k:
        return None
    return sum(sum(x["take"] for x in b) * _mean([x["net"] for x in b]) for b in blocks.values()) / k


def random_draws(sigs, rng, draws=DRAWS):
    blocks = {}
    for s in sigs:
        blocks.setdefault(int(s["ts"] // BLOCK_S), []).append(s)
    out = []
    for _ in range(draws):
        picked = []
        for b in blocks.values():
            picked += [x["net"] for x in rng.sample(b, sum(x["take"] for x in b))]
        out.append(_mean(picked))
    return out


def gain(sigs):
    t = [s["net"] for s in sigs if s["take"]]
    r = expected_random(sigs)
    return None if not t or r is None else _mean(t) - r


def bootstrap_lower(sigs, rng, boot=BOOT, alpha=ALPHA_LOOK):
    """Whole-coin bootstrap of the gain; one-sided lower bound at alpha. A resample with no take counts as -inf (conservative)."""
    coins = {}
    for s in sigs:
        coins.setdefault(s["key"], []).append(s)
    keys = sorted(coins)
    gs = []
    for _ in range(boot):
        res = [s for k in (rng.choice(keys) for _ in keys) for s in coins[k]]
        g = gain(res)
        gs.append(-math.inf if g is None else g)
    gs.sort()
    return gs[int(alpha * boot)]


def evaluate(sigs, seed=SEED, draws=DRAWS, boot=BOOT):
    takes = [s for s in sigs if s["take"]]
    nets = [s["net"] for s in takes]
    lo, hi = min(s["ts"] for s in sigs), max(s["ts"] for s in sigs)
    width = (hi - lo) / 4 or 1.0
    slices = [_mean([s["net"] for s in takes if min(int((s["ts"] - lo) / width), 3) == q]) for q in range(4)]
    best3 = sorted(nets)[:-3] if len(nets) > 3 else []
    p0 = _mean([s["net"] for s in sigs])
    rng = random.Random(seed)
    rd = random_draws(sigs, rng, draws)
    g = gain(sigs)
    lb = bootstrap_lower(sigs, random.Random(seed), boot)
    coins = len({s["key"] for s in takes})
    cond = {"floor_100_takes_50_coins": len(takes) >= FLOOR_TAKES and coins >= FLOOR_COINS,
            "mean_and_median_above_0": bool(nets) and _mean(nets) > 0 and statistics.median(nets) > 0,
            "mean_without_best_3_above_0": bool(best3) and _mean(best3) > 0,
            "positive_in_3_of_4_slices": sum((x or 0) > 0 for x in slices) >= 3,
            "beats_p0": bool(nets) and p0 is not None and _mean(nets) > p0,
            "gain_lower_bound_above_0": lb > 0}
    rdv = sorted(x for x in rd if x is not None)
    return {"signals": len(sigs), "takes": len(takes), "take_coins": coins, "take_mean": _mean(nets),
            "take_median": statistics.median(nets) if nets else None, "mean_without_best_3": _mean(best3),
            "slice_means": slices, "p0_mean": p0, "random_expected_mean": expected_random(sigs),
            "random_draws_mean": _mean(rdv), "random_draws_95": [rdv[int(0.025 * len(rdv))], rdv[int(0.975 * len(rdv)) - 1]] if rdv else None,
            "gain": g, "gain_lower_bound": lb, "alpha": ALPHA_LOOK, "conditions": cond}


def look(n, home=HOME, now=None):
    if n not in LOOKS:
        sys.exit("formal looks are only at %s scored takes" % (LOOKS,))
    out = Path(home) / "out"
    f = out / ("look-%d.json" % n)
    if f.exists():
        sys.exit("look %d was already run (%s)" % (n, f))
    for m in LOOKS:
        g = out / ("look-%d.json" % m)
        if g.exists() and json.loads(g.read_text(encoding="utf8")).get("verdict") == "pass":
            sys.exit("L1 already passed at look %d; it has stopped" % m)
    for log in ("predictions.jsonl", "closes.jsonl"):
        k, why = verify(out / log)
        if why:
            sys.exit("%s line %d: %s" % (log, k, why))
    sigs, _ = scored_signals(home)
    takes = [i for i, s in enumerate(sigs) if s["take"]]
    if len(takes) < n:
        sys.exit("look %d needs %d scored takes; there are %d" % (n, n, len(takes)))
    sigs = sigs[:takes[n - 1] + 1]
    if any(s["net"] is None for s in sigs):
        sys.exit("look %d: %d of its signals are still open; run it after they close" % (n, sum(s["net"] is None for s in sigs)))
    res = evaluate(sigs)
    res.update(look=n, at=time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(time.time() if now is None else now)),
               verdict="pass" if all(res["conditions"].values()) else ("fail" if n == LOOKS[-1] else "not yet"))
    atomic_write(f, json.dumps(res, indent=1, default=str))
    return res


def status(home=HOME):
    st = json.loads((Path(home) / "state" / "state.json").read_text(encoding="utf8"))
    sigs, waiting = scored_signals(home)
    closed = [s for s in sigs if s["net"] is not None]
    takes = [s["net"] for s in closed if s["take"]]
    return {"READOUT": "descriptive only, never a verdict (docs/MEME.md section 18)", "counts": st["counts"],
            "model_version": st["model"]["n"], "weight_hash": weight_hash(st["model"]), "open_predictions": len(st["pending"]),
            "scored_closed": len(closed), "scored_open": len(waiting), "takes_closed": len(takes), "take_mean": _mean(takes),
            "p0_mean": _mean([s["net"] for s in closed]), "heartbeat": st.get("heartbeat"), "error": st.get("error")}


# ---------------------------------------------------------------- run

def take_lock(sdir):
    """One learner at a time: an OS lock on state/learner.lock (released when the process dies); our PID goes in state/learner.pid."""
    sdir.mkdir(parents=True, exist_ok=True)
    fh = open(sdir / "learner.lock", "a+")
    try:
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except ImportError:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("another learner holds %s; exiting" % (sdir / "learner.lock"))
    atomic_write(sdir / "learner.pid", "%d\n" % os.getpid())    # for run_learner.ps1's stale-PID check (the lock file is locked)
    return fh


def refuse_live_folder():
    live = DATA.parents[2].resolve()
    if HERE == live or live in HERE.parents:
        sys.exit("refusing to run from the live folder %s; run from C:\\Trading\\learner\\repo" % live)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("run", "once", "verify", "status", "look"))
    ap.add_argument("n", nargs="?", type=int)
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--home", default=str(HOME))
    a = ap.parse_args(argv)
    home = Path(a.home)
    if a.cmd == "verify":
        bad = False
        for log in ("predictions.jsonl", "closes.jsonl"):
            k, why = verify(home / "out" / log)
            print("%s: %s" % (log, "%d lines, chain OK" % k if not why else "BROKEN at line %d: %s" % (k, why)))
            bad |= bool(why)
        return 1 if bad else 0
    if a.cmd == "status":
        print(json.dumps(status(home), indent=1))
        return 0
    if a.cmd == "look":
        print(json.dumps(look(a.n, home), indent=1, default=str))
        return 0
    refuse_live_folder()
    lock = take_lock(home / "state")  # noqa: F841 (held for the life of the process)
    lr = Learner(a.data, home)
    if a.cmd == "once":
        print(json.dumps(lr.poll()))
        return 0
    print("learner start pid=%d t18=%d" % (os.getpid(), T18), flush=True)
    while True:
        try:
            lr.poll()
        except LearnerError as e:
            lr.state["error"] = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), e)
            lr.save()
            print("learner stopped: %s" % e, flush=True)
            return 2
        time.sleep(POLL_S)


if __name__ == "__main__":
    sys.exit(main())
