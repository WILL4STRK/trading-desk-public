"""D1 downside vetoes on P0's own entries (docs/MEME.md section 17). Stdlib only. Reads a COPY of shadows.jsonl, never the live file.

  python research/downside/d1.py explore --until <unix>     # shadows opened up to the section 17 commit: description only
  python research/downside/d1.py forward [--since <unix>]   # the one forward-window read; writes forward.lock in default_out()

Fields read (as the desk writes them, desk/meme.py): policy, screen, key, chain, ts, f.liq, f.r1h, f.buy_ratio_m5, exit_ts, why, net_pct.
"""
import argparse
import json
import os
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
INPUT = Path(r"C:\Trading\research\downside\input\shadows.jsonl")
WIN_OUT = Path(r"C:\Trading\research\downside\out")


def default_out(name=None):
    """Where the forward read writes forward.json and forward.lock: D1_OUT if set, else WIN_OUT
    (C:/Trading/research/downside/out) on Windows, never inside a repo or the live desk folder; else research/downside/out."""
    env = os.environ.get("D1_OUT")
    if env:
        return Path(env)
    return WIN_OUT if (name or os.name) == "nt" else HERE / "out"


OUT = default_out()
SEED, DRAWS, BOOT = 17, 1000, 1000
WINDOW_S, GRACE_S = 7 * 86400, 4 * 3600 + 150
BLOCK_S = 6 * 3600
SEVERE_STOP_S, SEVERE_NET = 3600, -15.0
FLOOR_SIGNALS, FLOOR_COINS = 300, 100
VETOES = {"V1": ("r1h", lambda v: v >= 5.0), "V2": ("buy_ratio_m5", lambda v: v < 0.50)}


def dedupe(rows):
    """Section 17.1: one line per shadow (the same key, policy and entry ts). A failed desk save can make the next run
    close a shadow again; keep the FIRST close (the earliest exit_ts, the one the paper logic took). Returns (rows, removed);
    order is kept, and of equal exit times the earlier line wins."""
    best = {}
    for i, r in enumerate(rows):
        k = (r.get("key"), r.get("policy"), r.get("ts"))
        x = r.get("exit_ts")
        rank = (x is None, x if x is not None else 0.0, i)        # a close beats an open line, then the earliest exit
        if k not in best or rank < best[k][0]:
            best[k] = (rank, i)
    keep = {i for _, i in best.values()}
    return [r for i, r in enumerate(rows) if i in keep], len(rows) - len(keep)


def read(path=INPUT):
    """(signals, duplicate closes removed). Signals: policy 'all' and screen 'pass', de-duplicated first (section 17.1).
    Unresolved ones (no exit) keep net None."""
    rows = []
    for line in Path(path).read_text(encoding="utf8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict):
            rows.append(r)
    rows, removed = dedupe(rows)
    out = []
    for r in rows:
        if r.get("policy") != "all" or r.get("screen") != "pass":
            continue
        f = r.get("f") or {}
        out.append({"key": r["key"], "chain": r.get("chain"), "ts": r["ts"], "liq": f.get("liq"), "r1h": f.get("r1h"),
                    "buy_ratio_m5": f.get("buy_ratio_m5"), "exit_ts": r.get("exit_ts"), "why": r.get("why"),
                    "net": r.get("net_pct") if r.get("exit_ts") is not None else None})
    return out, removed


def load(path=INPUT, quiet=False):
    """The signals (see read); prints how many duplicate closes section 17.1 removed."""
    sigs, removed = read(path)
    if not quiet:
        print("section 17.1: removed %d duplicate close%s (same key, policy and entry ts; kept the earliest exit)"
              % (removed, "" if removed == 1 else "s"))
    return sigs


def vetoed(sig, name):
    """A missing feature means no veto."""
    field, test = VETOES[name]
    v = sig.get(field)
    return v is not None and bool(test(v))


def severe(sig):
    return (sig["why"] == "stop" and sig["exit_ts"] - sig["ts"] <= SEVERE_STOP_S) or sig["net"] <= SEVERE_NET


def liq_cuts(sigs):
    liqs = sorted(s["liq"] for s in sigs if s["liq"])
    return statistics.quantiles(liqs, n=3) if len(liqs) >= 3 else []


def stratum(sig, cuts):
    if not sig["liq"]:
        t = "u"
    else:
        t = sum(sig["liq"] > c for c in cuts)
    return (sig["chain"], t, int(sig["ts"] // BLOCK_S))


def groups(sigs, cuts):
    g = {}
    for i, s in enumerate(sigs):
        g.setdefault(stratum(s, cuts), []).append(i)
    return g


def draw_skips(g, k, rng):
    """One random-veto draw: exactly k[stratum] signals skipped in every stratum."""
    skipped = []
    for st, ix in g.items():
        if k[st]:
            skipped += rng.sample(ix, k[st])
    return skipped


def random_veto_counts(sigs, flags, cuts, rng, draws=DRAWS):
    """Skip exactly as many signals in every stratum as the real veto skipped there, at random. Returns (kept sums, kept severe counts)
    per draw, plus the per-stratum skipped counts (the share check)."""
    g = groups(sigs, cuts)
    sums, sev = [], []
    tot = sum(s["net"] for s in sigs)
    totsev = sum(severe(s) for s in sigs)
    k = {st: sum(flags[i] for i in ix) for st, ix in g.items()}
    for _ in range(draws):
        skipped = draw_skips(g, k, rng)
        sums.append(tot - sum(sigs[i]["net"] for i in skipped))
        sev.append(totsev - sum(severe(sigs[i]) for i in skipped))
    return sums, sev, k


def expected_random_sum(sigs, flags, cuts):
    """Exact expectation of the random veto's kept return sum: per stratum, (n - k) / n times the stratum's total return."""
    tot = 0.0
    for st, ix in groups(sigs, cuts).items():
        k = sum(flags[i] for i in ix)
        tot += (len(ix) - k) / len(ix) * sum(sigs[i]["net"] for i in ix)
    return tot


def gain(sigs, flags, cuts):
    """Per original signal: mean return with the real veto (vetoed counts 0) minus the random veto's expected mean return."""
    n = len(sigs)
    kept = sum(s["net"] for s, f in zip(sigs, flags) if not f)
    return (kept - expected_random_sum(sigs, flags, cuts)) / n


def bootstrap(sigs, name, cuts, rng, boot=BOOT):
    """Percentile 95 % interval of the gain, resampling whole coins."""
    by = {}
    for s in sigs:
        by.setdefault(s["key"], []).append(s)
    keys = list(by)
    gains = []
    for _ in range(boot):
        res = [s for k in (rng.choice(keys) for _ in keys) for s in by[k]]
        gains.append(gain(res, [vetoed(s, name) for s in res], cuts))
    gains.sort()
    return gains[int(0.025 * boot)], gains[int(0.975 * boot) - 1]


def evaluate(sigs, name, seed=SEED, draws=DRAWS, boot=BOOT):
    sigs = [s for s in sigs if s["net"] is not None]
    n = len(sigs)
    cuts = liq_cuts(sigs)
    flags = [vetoed(s, name) for s in sigs]
    rng = random.Random(seed)
    sums, sev, k = random_veto_counts(sigs, flags, cuts, rng, draws)
    skipped = [s for s, f in zip(sigs, flags) if f]
    keptl = [s for s, f in zip(sigs, flags) if not f]
    m = lambda xs: sum(xs) / len(xs) if xs else None  # noqa: E731
    lo, hi = bootstrap(sigs, name, cuts, random.Random(seed + 1), boot)
    ts = sorted(s["ts"] for s in sigs)
    t0, t1 = ts[0], ts[-1]
    slices = []
    for q in range(4):
        part = [(s, f) for s, f in zip(sigs, flags) if min(3, int(4 * (s["ts"] - t0) / max(t1 - t0, 1))) == q]
        slices.append(gain([s for s, _ in part], [f for _, f in part], cuts) if part else None)
    return {"veto": name, "signals": n, "coins": len({s["key"] for s in sigs}), "skip_share": len(skipped) / n if n else None,
            "skipped": len(skipped), "kept_mean": m([s["net"] for s in keptl]), "skipped_mean": m([s["net"] for s in skipped]),
            "mean_with_veto": sum(s["net"] for s in keptl) / n, "mean_random_veto": m(sums) / n,
            "gain": gain(sigs, flags, cuts), "gain_95": [lo, hi], "slice_gains": slices,
            "severe_rate_veto": sum(severe(s) for s in keptl) / n, "severe_rate_random": m(sev) / n,
            "severe_rate_all": sum(severe(s) for s in sigs) / n, "strata_skips": {str(a): b for a, b in k.items() if b}}


def since_from_git(repo):
    """Author time of the commit that added the section 17 heading to docs/MEME.md."""
    out = subprocess.run(["git", "log", "-S## 17. D1", "--format=%at", "--", "docs/MEME.md"], cwd=repo,
                         capture_output=True, text=True, check=True).stdout.split()
    if not out:
        sys.exit("cannot find the commit that added section 17; pass --since")
    return int(out[-1])


def forward(path=INPUT, since=None, now=None, out=OUT):
    out = Path(out)
    lock, result = out / "forward.lock", out / "forward.json"
    if lock.exists() or result.exists():
        sys.exit("the forward window was already read (%s); delete the lock only to restart the count" % lock)
    now = time.time() if now is None else now
    end = since + WINDOW_S
    if now < end + GRACE_S:
        sys.exit("the window ends %s; read after %s" % (time.strftime("%Y-%m-%d %H:%M", time.gmtime(end)),
                                                        time.strftime("%Y-%m-%d %H:%M", time.gmtime(end + GRACE_S))))
    sigs, removed = read(path)
    print("section 17.1: removed %d duplicate close%s (same key, policy and entry ts; kept the earliest exit)"
          % (removed, "" if removed == 1 else "s"))
    allsigs = [s for s in sigs if since < s["ts"] <= end]
    unresolved = sum(s["net"] is None for s in allsigs)
    res = {"since": since, "end": end, "signals_in_window": len(allsigs), "unresolved": unresolved,
           "duplicates_removed": removed, "vetoes": {}}
    for name in VETOES:
        e = evaluate(allsigs, name)
        floor = e["signals"] >= FLOOR_SIGNALS and e["coins"] >= FLOOR_COINS and unresolved <= 0.02 * max(len(allsigs), 1)
        cond = {"floor_300_signals_100_coins": floor, "gain_lower_bound_above_0": e["gain_95"][0] > 0,
                "positive_in_3_of_4_slices": sum((g or 0) > 0 for g in e["slice_gains"]) >= 3,
                "severe_rate_below_random": e["severe_rate_veto"] < e["severe_rate_random"]}
        res["vetoes"][name] = dict(e, conditions=cond, verdict="inconclusive" if not floor else
                                   ("pass" if all(cond.values()) else "fail"))
    out.mkdir(parents=True, exist_ok=True)
    result.write_text(json.dumps(res, indent=1, default=str))
    lock.write_text("forward window read %s\n" % time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(now)))
    return res


def explore(path=INPUT, until=None):
    sigs = [s for s in load(path) if until is None or s["ts"] <= until]
    return {name: evaluate(sigs, name) for name in VETOES}


def _fmt(x, nd=2, pct=False):
    return "-" if x is None else ("%.*f%%" % (nd, 100 * x) if pct else "%.*f" % (nd, x))


def print_table(res, title):
    print(title)
    for name, e in res.items():
        print("%s signals=%d coins=%d skip=%d (%s) | kept mean %s vs skipped mean %s | gain vs random %s [%s, %s] | severe: veto %s random %s none %s" % (
            name, e["signals"], e["coins"], e["skipped"], _fmt(e["skip_share"], 1, True), _fmt(e["kept_mean"]), _fmt(e["skipped_mean"]),
            _fmt(e["gain"]), _fmt(e["gain_95"][0]), _fmt(e["gain_95"][1]), _fmt(e["severe_rate_veto"], 1, True),
            _fmt(e["severe_rate_random"], 1, True), _fmt(e["severe_rate_all"], 1, True)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("explore", "forward"))
    ap.add_argument("--since", type=int)
    ap.add_argument("--until", type=int)
    ap.add_argument("--input", default=str(INPUT))
    a = ap.parse_args()
    if a.mode == "explore":
        r = explore(a.input, a.until)
        print_table(r, "EXPLORATORY (shadows before the section 17 commit; description only, never used to change a number)")
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "explore.json").write_text(json.dumps(r, indent=1, default=str))
    else:
        since = a.since if a.since else since_from_git(HERE.parents[1])
        r = forward(a.input, since)
        print_table({k: v for k, v in r["vetoes"].items()}, "FORWARD WINDOW (read once)")
        print(json.dumps({k: {"verdict": v["verdict"], "conditions": v["conditions"]} for k, v in r["vetoes"].items()}, indent=1))
