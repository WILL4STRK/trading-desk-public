"""C1 / C1-S copy study (docs/MEME.md section 19): shared pieces. Paper only. Nothing here can sign or send a transaction:
the only Solana calls are reads (getTransaction, getSignaturesForAddress, getTransactionsForAddress, getAccountInfo) and a
read-only log subscription.

Secrets: the Helius key is read from C:\\Trading\\secrets\\helius.key at run time and never logged; mask() removes it from any
text before it reaches a log or an exception message. The caller-name map (C:\\Trading\\secrets\\kol-map.json) is read only by
discover.py and only its cluster IDs and addresses leave it.
"""
import base64
import hashlib
import json
import os
import random
import re
import socket
import ssl
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
HOME = Path(os.environ.get("COPY_HOME", r"C:\Trading\research\copy"))
SECRETS = Path(os.environ.get("COPY_SECRETS", r"C:\Trading\secrets"))
KEY_FILE = SECRETS / "helius.key"
KOL_MAP = SECRETS / "kol-map.json"
DESK_SETTINGS = Path(os.environ.get("COPY_DESK_SETTINGS", r"C:\Users\Willr\trading-desk\data\settings.json"))

T19 = 1795467909                      # author time of 1955697 (section 19 heading), 2026-11-23 21:05:09 UTC
T19_COMMIT = "1955697c55dabaf64df38fe409177d42aed9d56f"
SOL_MINT = "So11111111111111111111111111111111111111112"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1tGtX9Rk5bW7zm7v"
B58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

COST_SIDE = 0.0175                    # section 2: 3.5 % round trip, half each side
SIZE_USD = 20.0
MAX_OPEN = 10
DELAY_S = 3.0
FILL_WINDOW_S = 120.0
MISSED_AFTER_S = 120.0
DEAD_LIQ = 1000.0
TIME_EXIT_S = 24 * 3600
ALERT_CAP = 20
ALERT_DISCLAIMER = "Paper. Callers often sell into followers."
LAMPORTS = 1e9
TX_VERSION = 1                        # highest transaction version asked for (version 1 exists since 2026; 0 refuses it)


# ---- secrets -----------------------------------------------------------------------------------------------------------

_KEYS = set()


def load_key(path=None):
    """The Helius key from its file (whitespace stripped). Remembered only so mask() can remove it from text."""
    p = Path(path or KEY_FILE)
    k = p.read_text(encoding="utf-8").strip()
    if not k:
        raise RuntimeError("empty key file %s" % p)
    _KEYS.add(k)
    return k


_KEYQ = re.compile(r"(api[-_]?key=)[^&\s'\"]+", re.I)


def mask(text):
    """Text with every api-key query value and every loaded key replaced by ***."""
    s = _KEYQ.sub(r"\1***", str(text))
    for k in _KEYS:
        if k:
            s = s.replace(k, "***")
    return s


# ---- files ---------------------------------------------------------------------------------------------------------------

def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_write(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    for i in range(10):                         # a reader with an open handle can make the replace fail on Windows
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            time.sleep(0.05 * (2 ** i) if i < 4 else 0.5)
    os.replace(tmp, p)


class Log:
    """Append-only JSONL with a unique "id" per line. A line whose id is already in the file is not written again, so a
    restart that redoes work never duplicates a line. Each line is flushed and fsynced; text passes through mask()."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ids = set()
        if self.path.exists():
            raw = self.path.read_bytes()
            if raw and not raw.endswith(b"\n"):           # a torn last line from a crash: drop it
                raw = raw[:raw.rfind(b"\n") + 1]
                atomic_write(self.path, raw.decode("utf-8"))      # never a half-rewritten log
            for ln in raw.splitlines():
                try:
                    self.ids.add(json.loads(ln)["id"])
                except (ValueError, KeyError, TypeError):
                    pass

    def __contains__(self, i):
        return i in self.ids

    def rows(self):
        if not self.path.exists():
            return []
        return [json.loads(ln) for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def add(self, row):
        if row["id"] in self.ids:
            return False
        line = mask(json.dumps(row, sort_keys=True, separators=(",", ":"))) + "\n"
        with open(self.path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        self.ids.add(row["id"])
        return True


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.HANDLE)
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.GetFileSizeEx.argtypes = (wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong))
    _k32.ReadFile.argtypes = (wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID)
    _k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _INVALID = wintypes.HANDLE(-1).value


def read_shared(path):
    """The whole file, read through a handle opened with FILE_SHARE_READ | WRITE | DELETE and closed at once, so the desk
    can replace the file while we read (Python's open() does not share delete)."""
    path = os.fspath(Path(path).absolute())
    if os.name != "nt":
        with open(path, "rb") as f:
            return f.read()
    h = _k32.CreateFileW(path, 0x80000000, 0x7, None, 3, 0x80, None)
    if h in (None, _INVALID):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        n = ctypes.c_longlong()
        if not _k32.GetFileSizeEx(h, ctypes.byref(n)):
            raise ctypes.WinError(ctypes.get_last_error())
        buf = ctypes.create_string_buffer(max(n.value, 1))
        got = wintypes.DWORD()
        out = b""
        while len(out) < n.value:
            if not _k32.ReadFile(h, buf, n.value - len(out), ctypes.byref(got), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if not got.value:
                break
            out += buf.raw[:got.value]
        return out
    finally:
        _k32.CloseHandle(h)


def ntfy_settings(path=None):
    """(server, topic) from the desk's settings.json: meme_ntfy_topic, else ntfy_topic (the desk's own fallback)."""
    st = json.loads(read_shared(path or DESK_SETTINGS).decode("utf-8"))
    server = st.get("ntfy_server") or "https://ntfy.sh"
    return server, (st.get("meme_ntfy_topic") or st.get("ntfy_topic") or "")


# ---- frozen lists ----------------------------------------------------------------------------------------------------------

def git(*args, repo=REPO):
    return subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True, text=True, check=True).stdout.strip()


def frozen_check(path, repo=REPO):
    """A frozen list is valid only if exactly one commit ever touched it and the file on disk is that commit's blob.
    Returns {sha256, commit, start} (start = the commit's author time = the forward window's start); raises otherwise."""
    rel = Path(path).resolve().relative_to(Path(repo).resolve()).as_posix()
    commits = [c for c in git("log", "--format=%H %at", "--follow", "--", rel, repo=repo).splitlines() if c.strip()]
    if len(commits) != 1:
        raise RuntimeError("%s: %d commits touch it; a frozen list has exactly one" % (rel, len(commits)))
    h, at = commits[0].split()
    blob = git("rev-parse", "%s:%s" % (h, rel), repo=repo)
    disk = git("hash-object", str(Path(path).resolve()), repo=repo)
    if blob != disk:
        raise RuntimeError("%s differs from its freeze commit %s" % (rel, h[:7]))
    return {"sha256": sha256_file(path), "commit": h, "start": int(at)}


# ---- HTTP and Helius -------------------------------------------------------------------------------------------------------

UA = "c1-copy-study/1.0"


def http_json(url, body=None, timeout=20):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json", "User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(mask("HTTP %s from %s" % (e.code, url))) from None
    except Exception as e:
        raise RuntimeError(mask("%s: %s" % (type(e).__name__, e))) from None


class Pacer:
    """At most `per` calls in any `window` seconds (thread-safe)."""

    def __init__(self, per, window=1.0, clock=time.monotonic, sleep=time.sleep):
        self.per, self.window, self.clock, self.sleep = per, window, clock, sleep
        self.t = []
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            while True:
                now = self.clock()
                self.t = [x for x in self.t if now - x < self.window]
                if len(self.t) < self.per:
                    self.t.append(now)
                    return
                self.sleep(self.window - (now - self.t[0]) + 0.01)


# credits per call, from https://www.helius.dev/docs/billing/credits (read 2026-11-23)
CREDITS = {"getTransactionsForAddress": None, "default": 1}


class Helius:
    """JSON-RPC reads with a rate limit, retries and a credit counter. The URL with the key is never logged or raised."""

    def __init__(self, key, rps=8, cap=None, post=http_json):
        self.url = "https://mainnet.helius-rpc.com/?api-key=" + key
        self.wss = "wss://mainnet.helius-rpc.com/?api-key=" + key
        _KEYS.add(key)
        self.pace = Pacer(rps)
        self.credits = 0
        self.cap = cap
        self.post = post

    def _charge(self, method, params, result):
        if method == "getTransactionsForAddress":
            cfg = params[1] if len(params) > 1 else {}
            if cfg.get("transactionDetails") == "full":
                n = len((result or {}).get("data") or [])
                return max(10, 10 * -(-n // 100))
            return 10
        return 1

    def rpc(self, method, params, tries=4):
        if self.cap is not None and self.credits >= self.cap:
            raise CreditCap("Helius credit cap %d reached" % self.cap)
        err = None
        for i in range(tries):
            self.pace.wait()
            try:
                r = self.post(self.url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            except RuntimeError as e:
                err = e
                time.sleep(min(30, 2 ** i))
                continue
            if "error" in r:
                code = (r["error"] or {}).get("code")
                err = RuntimeError(mask("%s error %s: %s" % (method, code, (r["error"] or {}).get("message"))))
                if code in (-32429, 429, -32005, -32603):
                    time.sleep(min(30, 2 ** i))
                    continue
                raise err
            res = r.get("result")
            self.credits += self._charge(method, params, res)
            return res
        raise err


class CreditCap(RuntimeError):
    pass


# ---- transaction parsing ---------------------------------------------------------------------------------------------------

def _keys(tx):
    """(account keys in order, signer flags) for json or jsonParsed encoding, v0 loaded addresses appended."""
    msg = tx["transaction"]["message"]
    raw = msg["accountKeys"]
    if raw and isinstance(raw[0], dict):
        keys = [k["pubkey"] for k in raw]
        signers = [bool(k.get("signer")) for k in raw]
    else:
        keys = list(raw)
        n = msg.get("header", {}).get("numRequiredSignatures", 1)
        signers = [i < n for i in range(len(keys))]
    la = (tx.get("meta") or {}).get("loadedAddresses") or {}
    extra = list(la.get("writable") or []) + list(la.get("readonly") or [])
    if extra and len(keys) < len((tx.get("meta") or {}).get("preBalances") or []):
        keys += extra
        signers += [False] * len(extra)
    return keys, signers


def _amt(tb):
    u = tb.get("uiTokenAmount") or {}
    try:
        return int(u.get("amount") or 0) / (10 ** int(u.get("decimals") or 0))
    except (TypeError, ValueError):
        return 0.0


def owner_deltas(tx):
    """{owner: {mint: (pre, post)}} from pre/postTokenBalances."""
    meta = tx.get("meta") or {}
    out = {}
    for side, key in ((0, "preTokenBalances"), (1, "postTokenBalances")):
        for tb in meta.get(key) or []:
            o, m = tb.get("owner"), tb.get("mint")
            if not o or not m:
                continue
            cur = out.setdefault(o, {}).setdefault(m, [0.0, 0.0])
            cur[side] += _amt(tb)
    return out


def parse_swaps(tx, wallets=None):
    """Section 19 swaps in one transaction: for each signer W (or each W in `wallets` that signed), if exactly one non-SOL mint
    changed in W's own token balance and W's SOL + wrapped SOL changed the other way, one swap:
    {wallet, mint, side: buy|sell, tokens (abs change), pre_tokens, sol (abs change, fees included), sig, slot, ts}.
    Failed transactions and transfers give nothing."""
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return []
    keys, signers = _keys(tx)
    pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
    od = owner_deltas(tx)
    sig = (tx["transaction"].get("signatures") or [None])[0]
    out = []
    for i, w in enumerate(keys):
        if not signers[i] or (wallets is not None and w not in wallets) or i >= len(pre) or i >= len(post):
            continue
        toks = od.get(w, {})
        sol = (post[i] - pre[i]) / LAMPORTS
        if SOL_MINT in toks:
            sol += toks[SOL_MINT][1] - toks[SOL_MINT][0]
        moved = [(m, a, b) for m, (a, b) in toks.items() if m != SOL_MINT and abs(b - a) > 0]
        if len(moved) != 1:
            continue
        m, a, b = moved[0]
        d = b - a
        if d > 0 and sol < 0:
            side = "buy"
        elif d < 0 and sol > 0:
            side = "sell"
        else:
            continue
        out.append({"wallet": w, "mint": m, "side": side, "tokens": abs(d), "pre_tokens": a, "sol": abs(sol),
                    "sig": sig, "slot": tx.get("slot"), "ts": tx.get("blockTime")})
    return out


def _instructions(tx):
    msg = tx["transaction"]["message"]
    ins = list(msg.get("instructions") or [])
    for grp in (tx.get("meta") or {}).get("innerInstructions") or []:
        ins += grp.get("instructions") or []
    return ins


def parse_transfers(tx):
    """SOL and token transfers in a jsonParsed transaction that has no swap: [{src, dst, asset ('SOL' or mint), amount, sig,
    ts}]. SOL from system transfer instructions (inner ones too); tokens from owner balance changes (one owner down, another
    up, same mint)."""
    meta = tx.get("meta") or {}
    if meta.get("err") is not None or parse_swaps(tx):
        return []
    sig = (tx["transaction"].get("signatures") or [None])[0]
    ts = tx.get("blockTime")
    out = []
    for ins in _instructions(tx):
        p = ins.get("parsed") if isinstance(ins, dict) else None
        if ins.get("program") == "system" and isinstance(p, dict) and p.get("type") in ("transfer", "transferWithSeed"):
            inf = p.get("info") or {}
            if inf.get("source") and inf.get("destination"):
                out.append({"src": inf["source"], "dst": inf["destination"], "asset": "SOL",
                            "amount": (inf.get("lamports") or 0) / LAMPORTS, "sig": sig, "ts": ts})
    od = owner_deltas(tx)
    mints = {m for d in od.values() for m in d if m != SOL_MINT}
    for m in sorted(mints):
        down = [(o, d[m][0] - d[m][1]) for o, d in od.items() if m in d and d[m][1] < d[m][0]]
        up = [(o, d[m][1] - d[m][0]) for o, d in od.items() if m in d and d[m][1] > d[m][0]]
        if len(down) == 1 and len(up) == 1 and down[0][0] != up[0][0]:
            out.append({"src": down[0][0], "dst": up[0][0], "asset": m, "amount": up[0][1], "sig": sig, "ts": ts})
    return out


def fee_payer(tx):
    keys, _ = _keys(tx)
    return keys[0] if keys else None


# ---- costs and fills (section 2 cost, section 19.B) ------------------------------------------------------------------------

def buy_fill(px, liq, size=SIZE_USD):
    """Paper buy price: price x (1 + 1.75 % + size / (liquidity / 2)). None when there is no usable liquidity figure."""
    if not px or px <= 0 or not liq or liq <= 0:
        return None
    return px * (1 + COST_SIDE + size / (liq / 2))


def sell_fill(px, liq, value):
    """Paper sell price for `value` dollars at the sampled price; floored at 0."""
    if not px or px <= 0 or not liq or liq <= 0:
        return 0.0
    return max(0.0, px * (1 - COST_SIDE - value / (liq / 2)))


def mirror_fraction(sold, pre_balance):
    """The fraction of its holding the followed wallet sold (1 when its balance before is unknown or smaller)."""
    if not pre_balance or pre_balance <= 0 or sold >= pre_balance:
        return 1.0
    return max(0.0, sold / pre_balance)


# ---- can't-sell check -------------------------------------------------------------------------------------------------------

TRAP_EXT = {"permanentDelegate": "Token-2022 permanent delegate", "transferHook": "Token-2022 transfer hook",
            "nonTransferable": "Token-2022 non-transferable"}


def mint_blocks(acct):
    """Reasons from the mint's jsonParsed account (getAccountInfo value). None (unknown) when it cannot be read."""
    try:
        data = acct["data"]["parsed"]["info"]
    except (TypeError, KeyError):
        return None
    why = []
    if data.get("freezeAuthority"):
        why.append("freeze authority is set")
    if acct.get("owner") == TOKEN_2022:
        for e in data.get("extensions") or []:
            name = e.get("extension")
            st = e.get("state") or {}
            if name in TRAP_EXT:
                if name != "permanentDelegate" or st.get("delegate"):
                    why.append(TRAP_EXT[name])
            elif name == "defaultAccountState" and str(st.get("accountState", "")).lower() == "frozen":
                why.append("Token-2022 default account state frozen")
            elif name == "transferFeeConfig":
                bps = max(int((st.get("newerTransferFee") or {}).get("transferFeeBasisPoints") or 0),
                          int((st.get("olderTransferFee") or {}).get("transferFeeBasisPoints") or 0))
                if bps > 1000:
                    why.append("Token-2022 transfer fee %.1f%% over 10%%" % (bps / 100))
    return why


_TAX = re.compile(r"(sell tax|transfer fee|transfer tax|\btax\b)", re.I)
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def rugcheck_blocks(summary):
    """The desk's memeterminal.cant_sell reasons from a RugCheck summary (danger or warn risks naming a honeypot, freeze
    authority, a Token-2022 trap, or a tax over 10 %; a tax with no readable size counts as over). None when unknown."""
    if not isinstance(summary, dict) or "risks" not in summary:
        return None
    texts = [("%s %s" % (r.get("name") or "", r.get("description") or "")).strip()
             for r in summary.get("risks") or [] if isinstance(r, dict)]
    why = []
    low = [t.lower() for t in texts]
    if any("freeze authority" in t for t in low):
        why.append("freeze authority (RugCheck)")
    if any("honeypot" in t for t in low):
        why.append("honeypot (RugCheck)")
    for w, r in (("permanent delegate", "permanent delegate"), ("transfer hook", "transfer hook"),
                 ("non-transferable", "non-transferable"), ("non transferable", "non-transferable")):
        if any(w in t for t in low) and r + " (RugCheck)" not in why:
            why.append(r + " (RugCheck)")
    for t in texts:
        if _TAX.search(t):
            m = _PCT.search(t)
            if m is None or float(m.group(1)) > 10:
                why.append("tax over 10%% (RugCheck: %s)" % t[:60])
    return why


# ---- tiny WebSocket client (RFC 6455, client side, text frames) -------------------------------------------------------------

class WebSocket:
    def __init__(self, url, timeout=60):
        u = urllib.parse.urlsplit(url)
        host, port = u.hostname, u.port or (443 if u.scheme == "wss" else 80)
        raw = socket.create_connection((host, port), timeout=timeout)
        self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host) if u.scheme == "wss" else raw
        key = base64.b64encode(os.urandom(16)).decode()
        path = (u.path or "/") + ("?" + u.query if u.query else "")
        req = ("GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
               "Sec-WebSocket-Version: 13\r\nUser-Agent: %s\r\n\r\n" % (path, host, key, UA))
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            c = self.sock.recv(1)
            if not c:
                raise ConnectionError("websocket handshake closed")
            head += c
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError("websocket handshake refused: %s" % head.split(b"\r\n", 1)[0].decode(errors="replace"))
        self.lock = threading.Lock()

    def _send(self, op, payload):
        hdr = bytes([0x80 | op])
        n = len(payload)
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        m = os.urandom(4)
        body = bytes(b ^ m[i % 4] for i, b in enumerate(payload))
        with self.lock:
            self.sock.sendall(hdr + m + body)

    def send(self, text):
        self._send(0x1, text.encode("utf-8"))

    def ping(self):
        self._send(0x9, b"")

    def _exact(self, n):
        b = b""
        while len(b) < n:
            c = self.sock.recv(n - len(b))
            if not c:
                raise ConnectionError("websocket closed")
            b += c
        return b

    def recv(self):
        """The next text message (str). Answers pings; raises ConnectionError on close."""
        buf = b""
        while True:
            h = self._exact(2)
            fin, op, n = h[0] & 0x80, h[0] & 0x0F, h[1] & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._exact(8))[0]
            mk = self._exact(4) if h[1] & 0x80 else None
            p = self._exact(n)
            if mk:
                p = bytes(b ^ mk[i % 4] for i, b in enumerate(p))
            if op == 0x8:
                raise ConnectionError("websocket closed by server")
            if op == 0x9:
                self._send(0xA, p)
                continue
            if op == 0xA:
                continue
            buf += p
            if fin:
                return buf.decode("utf-8")

    def close(self):
        try:
            self._send(0x8, b"")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


# ---- stats used by the readout ---------------------------------------------------------------------------------------------

def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def rng(seed=19):
    return random.Random(seed)
