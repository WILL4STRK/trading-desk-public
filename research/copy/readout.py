"""C1 / C1-S readout (docs/MEME.md section 19.C and 19.D).

    python research/copy/readout.py live              descriptive only, never a verdict
    python research/copy/readout.py look C1 100       a formal look (100, 200, 400 or 800); writes look-C1-100.json once
    python research/copy/readout.py look C1S 100

Reads only C1's own logs in C:\\Trading\\research\\copy\\ and, for P0, a copy of the desk's shadows.jsonl in input\\.
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.copy import common as C

LOOKS = (100, 200, 400, 800)
ALPHA_LOOK = 0.0125
BOOT = 10000
SEED = 19
CONTROLS = {"C1": ("random",), "C1S": ("random", "main")}


def _rows(path):
    p = Path(path)
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def slices_up(rows, k=4):
    """Number of k equal entry-time slices whose mean net is positive (an empty slice is not positive)."""
    if not rows:
        return 0
    a = min(r["entry_t"] for r in rows)
    b = max(r["entry_t"] for r in rows)
    w = (b - a) / k or 1.0
    groups = defaultdict(list)
    for r in rows:
        groups[min(k - 1, int((r["entry_t"] - a) / w))].append(r["net_pct"])
    return sum(1 for i in range(k) if groups[i] and C.mean(groups[i]) > 0)


def top_wallet_share(rows):
    """The largest single wallet's share of the arm's net dollar profit (None when the arm made no profit)."""
    by = defaultdict(float)
    for r in rows:
        by[r["wallet"]] += r["net_pct"] / 100 * C.SIZE_USD
    tot = sum(by.values())
    if tot <= 0:
        return None
    return max(by.values()) / tot


def bootstrap_lower(arm, ctl, boot=BOOT, seed=SEED, alpha=ALPHA_LOOK):
    """Lower bound (alpha percentile) of mean(arm) - mean(control), resampling whole coins (both arms' copies of a mint
    move together). Resamples in which either side is empty are dropped and counted."""
    by = defaultdict(lambda: ([], []))
    for r in arm:
        by[r["mint"]][0].append(r["net_pct"])
    for r in ctl:
        by[r["mint"]][1].append(r["net_pct"])
    mints = sorted(by)
    if not mints or not arm or not ctl:
        return None, 0
    rnd = C.rng(seed)
    diffs, dropped = [], 0
    for _ in range(boot):
        a, c = [], []
        for _ in mints:
            x = by[mints[rnd.randrange(len(mints))]]
            a += x[0]
            c += x[1]
        if not a or not c:
            dropped += 1
            continue
        diffs.append(C.mean(a) - C.mean(c))
    if not diffs:
        return None, dropped
    diffs.sort()
    return diffs[max(0, int(alpha * len(diffs)) - 1) if alpha * len(diffs) >= 1 else 0], dropped


def evaluate(arm_rows, controls, boot=BOOT):
    """The six checks of 19.D. controls: {name: rows}."""
    nets = [r["net_pct"] for r in arm_rows]
    coins = len({r["mint"] for r in arm_rows})
    best3 = sorted(nets)[:-3] if len(nets) > 3 else []
    share = top_wallet_share(arm_rows)
    out = {"n": len(nets), "coins": coins, "mean": C.mean(nets), "median": C.median(nets),
           "mean_without_best3": C.mean(best3), "slices_up": slices_up(arm_rows), "top_wallet_share": share,
           "gap_mean": C.mean([r["gap_pct"] for r in arm_rows if r.get("gap_pct") is not None]),
           "lag_median": C.median([r["lag"] for r in arm_rows if r.get("lag") is not None]), "controls": {}}
    gains_ok = True
    for name, rows in controls.items():
        lo, dropped = bootstrap_lower(arm_rows, rows, boot)
        out["controls"][name] = {"n": len(rows), "mean": C.mean([r["net_pct"] for r in rows]), "gain_lower": lo,
                                 "resamples_dropped": dropped}
        gains_ok = gains_ok and lo is not None and lo > 0
    checks = {"size": len(nets) >= 100 and coins >= 50,
              "mean_median": bool(nets) and out["mean"] > 0 and out["median"] > 0,
              "without_best3": out["mean_without_best3"] is not None and out["mean_without_best3"] > 0,
              "slices": out["slices_up"] >= 3,
              "beats_controls": gains_ok,
              "wallet_share": share is not None and share <= 0.25}
    out["checks"] = checks
    out["pass"] = all(checks.values())
    return out


def look_rows(home, arm, n):
    """The first n copied buys of the arm by entry time, if all have closed; their controls closed up to the n-th entry."""
    fills = sorted((r for r in _rows(Path(home) / "fills.jsonl") if r["leg"] == "buy" and r["arm"] == arm),
                   key=lambda r: (r["t"], r["id"]))
    if len(fills) < n:
        return None, "only %d copied buys" % len(fills)
    ids = {r["id"] for r in fills[:n]}
    t_end = fills[n - 1]["t"]
    closes = {r["id"]: r for r in _rows(Path(home) / "closes.jsonl")}
    if not ids <= set(closes):
        return None, "%d of the first %d still open" % (len(ids - set(closes)), n)
    arm_rows = [closes[i] for i in sorted(ids)]
    ctl = {c: [r for r in closes.values() if r["arm"] == c and r["entry_t"] <= t_end] for c in CONTROLS[arm]}
    return (arm_rows, ctl), None


def look(arm, n, home=C.HOME, boot=BOOT):
    if n not in LOOKS:
        raise SystemExit("formal looks are only at %s" % (LOOKS,))
    out = Path(home) / ("look-%s-%d.json" % (arm, n))
    if out.exists():
        raise SystemExit("%s exists: each formal look runs once" % out.name)
    got, why = look_rows(home, arm, n)
    if got is None:
        raise SystemExit("look %s %d not ready: %s" % (arm, n, why))
    res = evaluate(got[0], got[1], boot)
    res.update({"arm": arm, "look": n, "alpha": ALPHA_LOOK, "boot": boot, "seed": SEED})
    C.atomic_write(out, json.dumps(res, indent=1, sort_keys=True))
    return res


# ---- C1-S measures ------------------------------------------------------------------------------------------------------------

def side_measures(events):
    """For every side-wallet swap: lead over the cluster's main wallet first buy of the same mint (within 24 h either way)
    and the dump pattern (a side sell while the main still held the mint, or within 1 h after a main buy)."""
    side, main = [], defaultdict(list)
    for e in events:
        for arm, cl in zip(e.get("roles") or [], e.get("clusters") or []):
            if arm == "C1S":
                side.append((cl, e))
            elif arm == "main":
                main[(cl, e["mint"])].append(e)
    leads, dumps, sells = [], 0, 0
    for cl, e in side:
        ms = sorted(main.get((cl, e["mint"]), []), key=lambda x: x["ts"])
        if e["side"] == "buy":
            mb = [m["ts"] for m in ms if m["side"] == "buy"]
            if mb and abs(mb[0] - e["ts"]) <= 86400:
                leads.append(mb[0] - e["ts"])
        else:
            sells += 1
            held = 0.0
            for m in ms:
                if m["ts"] > e["ts"]:
                    break
                held += m["tokens"] if m["side"] == "buy" else -m["tokens"]
            recent = any(m["side"] == "buy" and 0 <= e["ts"] - m["ts"] <= 3600 for m in ms)
            if held > 0 or recent:
                dumps += 1
    return {"side_buys_with_main": len(leads), "lead_median_s": C.median(leads),
            "lead_share_before_main": (sum(1 for x in leads if x > 0) / len(leads)) if leads else None,
            "side_sells": sells, "dumps": dumps, "dump_share": dumps / sells if sells else None}


def p0(shadows_path, start, end):
    rows = [r for r in _rows(shadows_path) if r.get("policy") == "all" and r.get("chain") == "solana"
            and start <= r.get("ts", 0) < end and r.get("net_pct") is not None]
    return {"n": len(rows), "mean": C.mean(r["net_pct"] for r in rows), "label": "descriptive"}


def live(home=C.HOME):
    closes = _rows(Path(home) / "closes.jsonl")
    out = {"label": "LIVE READOUT, DESCRIPTIVE ONLY: not a verdict (section 19.D)"}
    for arm in ("C1", "random", "C1S", "main"):
        rs = [r for r in closes if r["arm"] == arm]
        out[arm] = {"closed": len(rs), "coins": len({r["mint"] for r in rs}), "mean": C.mean(r["net_pct"] for r in rs),
                    "median": C.median([r["net_pct"] for r in rs]),
                    "gap_mean": C.mean([r["gap_pct"] for r in rs if r.get("gap_pct") is not None])}
    out["c1s"] = side_measures(_rows(Path(home) / "events.jsonl"))
    st = Path(home) / "state.json"
    fz = (json.loads(st.read_text(encoding="utf-8")).get("frozen") or {}) if st.exists() else {}
    sh = Path(home) / "input" / "shadows.jsonl"
    if "C1" in fz and sh.exists():
        out["P0"] = p0(sh, fz["C1"]["start"], 1e12)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["live", "look"])
    ap.add_argument("arm", nargs="?", choices=["C1", "C1S"])
    ap.add_argument("n", nargs="?", type=int)
    a = ap.parse_args(argv)
    res = live() if a.what == "live" else look(a.arm, a.n)
    print(json.dumps(res, indent=1, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
