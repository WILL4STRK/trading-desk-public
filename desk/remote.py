"""Remote access: one password for visits that come through a tunnel.

On this computer (Host localhost or 127.0.0.1, no Cloudflare headers) nothing
changes: no password. A visit through the Cloudflare tunnel must log in once;
it then carries a signed cookie for 30 days. Set or change the password with
`python3 -m desk password`; changing it signs every browser out.

The password is kept only as a salted PBKDF2-SHA256 hash in data/remote.json.
Five wrong tries from one address lock that address out for ten minutes.
"""
import hashlib
import hmac
import json
import os
import secrets
import time

from .paths import data_dir

ITERATIONS = 200_000
SESSION_DAYS = 30
MIN_LENGTH = 10
MAX_TRIES, LOCK_SECONDS = 5, 600
COOKIE = "desk_session"
_fails = {}            # address -> [times of wrong tries]


def path():
    return data_dir() / "remote.json"


def load():
    try:
        return json.loads(path().read_text())
    except (OSError, ValueError):
        return None


def _hash(password, salt, iterations):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations).hex()


def set_password(password):
    if len(password) < MIN_LENGTH:
        raise ValueError("Use at least %d characters." % MIN_LENGTH)
    salt = secrets.token_hex(16)
    d = {"salt": salt, "iterations": ITERATIONS, "hash": _hash(password, salt, ITERATIONS),
         "secret": secrets.token_hex(32)}           # a new secret signs every old session out
    tmp = path().with_suffix(".tmp")
    tmp.write_text(json.dumps(d))
    os.chmod(tmp, 0o600)
    tmp.replace(path())


def check(password):
    d = load()
    return bool(d) and hmac.compare_digest(_hash(password, d["salt"], d["iterations"]), d["hash"])


def _sign(secret, text):
    return hmac.new(bytes.fromhex(secret), text.encode(), hashlib.sha256).hexdigest()


def token(now=None):
    d = load()
    exp = str(int((now or time.time()) + SESSION_DAYS * 86400))
    return "%s.%s" % (exp, _sign(d["secret"], exp))


def valid(tok, now=None):
    d = load()
    exp, _, sig = str(tok or "").partition(".")
    if not d or not exp.isdigit() or int(exp) < (now or time.time()):
        return False
    return hmac.compare_digest(sig, _sign(d["secret"], exp))


def locked(addr, now=None):
    now = now or time.time()
    tries = [t for t in _fails.get(addr, []) if now - t < LOCK_SECONDS]
    _fails[addr] = tries
    return len(tries) >= MAX_TRIES


def failed(addr, now=None):
    _fails.setdefault(addr, []).append(now or time.time())
