"""Read-only view of the memebot paper-research loop for the Memebot tab.

The memebot is a separate program (github.com/WILL4STRK/meambot) that paper
trades memecoins found on DexScreener. It never places real orders, and this
module never changes its files: it only reads them.

Where it looks, in order:
  1. the MEMEBOT_DIR environment variable
  2. a meambot or memebot folder next to this Trading Desk folder
"""
import json
import os
import re
import time
from pathlib import Path

from .paths import ROOT

FRESH_SECONDS = 15 * 60          # a file written this recently means the loop is still running
MAX_LOG_BYTES = 300_000
MAX_TRADES = 200
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}
_count_cache = {}


def find_dir():
    env = os.environ.get("MEMEBOT_DIR")
    if env:
        return Path(env).expanduser()
    for name in ("meambot", "memebot"):
        p = ROOT.parent / name
        if p.is_dir():
            return p
    return None


def _walk(base, depth=3):
    """Files under base, at most `depth` folders down, skipping tool folders."""
    out = []
    stack = [(base, 0)]
    while stack:
        d, lvl = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            if e.is_dir(follow_symlinks=False):
                if lvl < depth and e.name not in SKIP_DIRS:
                    stack.append((e.path, lvl + 1))
            elif e.is_file(follow_symlinks=False):
                out.append(e)
    return out


def _newest(files, name):
    hits = [f for f in files if f.name == name]
    return max(hits, key=lambda f: f.stat().st_mtime, default=None)


def _count_lines(path):
    st = os.stat(path)
    key = (path, st.st_size, st.st_mtime)
    if key not in _count_cache:
        n = 0
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                n += chunk.count(b"\n")
        _count_cache.clear()
        _count_cache[key] = n
    return _count_cache[key]


def _tail_lines(path, limit):
    """The last `limit` lines of a text file, without reading all of it."""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        fh.seek(max(0, size - 400_000))
        lines = fh.read().decode("utf-8", "replace").splitlines()
    if size > 400_000:
        lines = lines[1:]                       # the first one may be cut
    return lines[-limit:]


def parse_loop_log(text):
    """Status line, update time, cycle table and verdict from LOOP_LOG.md."""
    out = {"status": None, "updated": None, "verdict": None, "cycles": {"head": [], "rows": []}}
    m = re.search(r"^Status:\s*(.+)$", text, re.M)
    if m:
        out["status"] = m.group(1).strip()
    m = re.search(r"^Updated:\s*(.+)$", text, re.M)
    if m:
        out["updated"] = m.group(1).strip()
    m = re.search(r"^##\s*Verdict\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if m:
        out["verdict"] = m.group(1).strip()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.lstrip().startswith("|") and "cycle" in line.lower():
            cells = lambda s: [c.strip() for c in s.strip().strip("|").split("|")]
            out["cycles"]["head"] = cells(line)
            for row in lines[i + 2:]:
                if not row.lstrip().startswith("|"):
                    break
                out["cycles"]["rows"].append(cells(row))
            break
    return out


def _trades(files):
    rows = []
    for f in files:
        if not (f.name.endswith(".jsonl") and "trade" in f.name.lower()):
            continue
        for line in _tail_lines(f.path, MAX_TRADES):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and "pnl" in r:
                rows.append({
                    "file": f.name,
                    "symbol": r.get("sym") or r.get("symbol") or r.get("token") or "?",
                    "pnl": r.get("pnl"),
                    "ret": r.get("ret"),
                    "why": r.get("why") or r.get("exit_reason") or "",
                    "hold_min": r.get("hold_min"),
                    "t_out": r.get("t_out") or r.get("ts"),
                })
    rows.sort(key=lambda r: r["t_out"] or 0, reverse=True)
    return rows[:MAX_TRADES]


def status(now=None):
    now = now or time.time()
    d = find_dir()
    if d is None or not d.is_dir():
        return {"found": False, "dir": str(d) if d else None,
                "hint": "Put the meambot folder next to this Trading Desk folder, or set MEMEBOT_DIR to where it is."}
    files = _walk(d)
    newest = max((f.stat().st_mtime for f in files), default=None)
    out = {"found": True, "dir": str(d), "last_write": newest,
           "running": bool(newest and now - newest < FRESH_SECONDS)}

    log = _newest(files, "LOOP_LOG.md")
    if log:
        with open(log.path, "rb") as fh:
            text = fh.read(MAX_LOG_BYTES).decode("utf-8", "replace")
        out["log"] = {"path": log.path, "mtime": log.stat().st_mtime, "text": text, **parse_loop_log(text)}

    disc = [f for f in files if f.name.startswith("discoveries") and f.name.endswith(".jsonl")]
    out["discoveries"] = sum(_count_lines(f.path) for f in disc) if disc else None

    params = _newest(files, "params_candidate.json")
    if params:
        try:
            with open(params.path, encoding="utf-8") as fh:
                out["frozen_params"] = json.load(fh)
        except (OSError, ValueError):
            out["frozen_params"] = {"error": "could not read params_candidate.json"}

    trades = _trades(files)
    out["trades"] = trades
    pnls = [t["pnl"] for t in trades if isinstance(t["pnl"], (int, float))]
    out["trade_summary"] = {"count": len(pnls), "wins": sum(p > 0 for p in pnls),
                            "pnl": round(sum(pnls), 2) if pnls else 0.0}
    return out
