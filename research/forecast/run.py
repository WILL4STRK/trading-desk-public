"""Rule F1 backtest runner (docs/MEME.md section 16 and 16.1).

  python research/forecast/run.py dev     # dev points only; as often as you like
  python research/forecast/run.py final   # the one holdout look: writes RESULTS-HOLDOUT.json and out/holdout.lock

Run it with the forecast venv (C:\\Trading\\forecast-venv). Refuses to start unless TIMING-NOTE.md fixes the decision step.
Window = first to last ELIGIBLE decision point of all tokens (the same for every arm); first 60 % dev, last 40 % holdout, 4 equal folds.
A trade belongs to dev or holdout by its entry time.
"""
import json
import re
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from research.forecast import core, data as D  # noqa: E402
from research.meme import backtest as B  # noqa: E402

OUT = Path(r"C:\Trading\research\forecast\out")
LOCK = OUT / "holdout.lock"
RESULT = HERE / "RESULTS-HOLDOUT.json"
FCACHE = OUT / "forecasts.jsonl"


def step_minutes():
    m = re.search(r"^decision_step_minutes:\s*(\d+)\s*$", (HERE / "TIMING-NOTE.md").read_text(encoding="utf8"), re.M)
    if not m:
        sys.exit("TIMING-NOTE.md has no decision_step_minutes line: do the timing run first (16.1 item 6)")
    return int(m.group(1))


class Forecaster:
    """Chronos-2 zero-shot, batch of one, cached by (token, decision time). Returns (q20, q50) of the close at +4 h."""

    def __init__(self):
        self.cache, self.pipe = {}, None
        if FCACHE.exists():
            for line in FCACHE.read_text(encoding="utf8").splitlines():
                r = json.loads(line)
                self.cache[r["k"]] = (r["q20"], r["q50"])

    def __call__(self, key, t, closes):
        k = "%s|%d" % (key, t)
        if k not in self.cache:
            if self.pipe is None:
                import torch  # noqa: F401
                from chronos import Chronos2Pipeline
                name, rev = (HERE / "MODEL_REVISION").read_text().split()
                self.pipe = Chronos2Pipeline.from_pretrained(name, revision=rev, device_map="cpu")
            import torch
            q, _ = self.pipe.predict_quantiles([torch.tensor(closes, dtype=torch.float32)], prediction_length=core.HORIZON,
                                               quantile_levels=[0.2, 0.5])
            v = q[0][0, core.HORIZON - 1, :].tolist()
            self.cache[k] = (v[0], v[1])
            OUT.mkdir(parents=True, exist_ok=True)
            with FCACHE.open("a", encoding="utf8") as f:
                f.write(json.dumps({"k": k, "q20": v[0], "q50": v[1]}) + "\n")
        return self.cache[k]


def build(step):
    pts, info = [], {"tokens_selected": 0, "tokens_with_bars": 0, "no_candles": 0, "not_fetched": 0, "gaps": 0, "bad_open": 0,
                     "dead_tokens": 0}
    for tok in D.select_tokens():
        info["tokens_selected"] += 1
        c = D.load_bars(tok)
        if c is None:
            info["not_fetched"] += 1
            continue
        if not c["bars"]:
            info["no_candles"] += 1
            continue
        info["tokens_with_bars"] += 1
        info["dead_tokens"] += bool(tok["dead"])
        p, sk = core.points(tok, c["bars"], c["fetched"], step)
        pts += p
        info["gaps"] += sk["gaps"]
        info["bad_open"] += sk["bad_open"]
    return pts, info


def stat_line(s):
    f = lambda x, n=2: "-" if x is None else ("%.*f" % (n, x))  # noqa: E731
    return "n=%-4d tok=%-3d mean=%7s med=%7s pos=%5s ex3=%7s perTok=%7s open=%d" % (
        s["trades"], s["tokens"], f(s["mean"]), f(s["median"]), f(s["pct_positive"], 1), f(s["mean_without_best3"]),
        f(s["mean_per_token"]), s["open_at_end"])


def point_stats(pts):
    nets = sorted(p["net"] for p in pts if p["net"] is not None)
    return {"points": len(pts), "closed": len(nets), "mean": sum(nets) / len(nets) if nets else None,
            "median": statistics.median(nets) if nets else None}


def run(mode):
    step = step_minutes()
    pts, info = build(step)
    if not pts:
        sys.exit("no eligible decision points: fetch the candles first (python research/forecast/data.py)")
    lo, hi = min(p["decide_ts"] for p in pts), max(p["decide_ts"] for p in pts)
    cut = lo + 0.6 * (hi - lo)
    if mode == "final":
        if LOCK.exists() or RESULT.exists():
            sys.exit("the holdout was already looked at (%s / %s); delete the lock only to restart the count" % (LOCK, RESULT))
        use = pts
    else:
        use = [p for p in pts if p["decide_ts"] < cut]
    fc = Forecaster()
    t0 = time.time()
    for n, p in enumerate(use, 1):
        q20, q50 = fc(p["key"], p["decide_ts"], p["closes"])
        p["q20"], p["q50"], p["f1"] = q20, q50, core.f1_signal(p["closes"], q20, q50)
        if n % 500 == 0:
            print("  forecast %d/%d (%.0f s)" % (n, len(use), time.time() - t0), flush=True)
    res = {"info": dict(info, eligible_points=len(pts), points_used=len(use), step_minutes=step,
                        window=[B._ts(lo), B._ts(hi), B._ts(cut)])}
    arms = {}
    for arm in core.ARMS:
        tr = core.arm_trades(use, arm)
        for t in tr:
            t["part"] = "dev" if t["entry_ts"] < cut else "holdout"
            t["fold"] = min(3, int(4 * (t["entry_ts"] - lo) / (hi - lo))) if hi > lo else 0
        arms[arm] = tr
    parts = ["dev"] + (["holdout"] if mode == "final" else [])
    res["arms"] = {a: {pt: B.stats([t for t in arms[a] if t["part"] == pt]) for pt in parts} for a in core.ARMS}
    res["folds"] = {a: B.fold_means(arms[a]) for a in core.ARMS} if mode == "final" else None
    for pt in parts:   # F1's selection share and its result on the points it rejected (point level, not blocked by positions)
        sel = [p for p in use if (p["entry_ts"] < cut) == (pt == "dev")]
        res.setdefault("selection", {})[pt] = {"eligible": len(sel), "f1_selected": sum(p["f1"] for p in sel),
                                               "selected": point_stats([p for p in sel if p["f1"]]),
                                               "rejected": point_stats([p for p in sel if not p["f1"]])}
    print("tokens %(tokens_selected)d selected, %(tokens_with_bars)d with bars, %(not_fetched)d not fetched, gaps skipped %(gaps)d, "
          "eligible points %(eligible_points)d, step %(step_minutes)d min" % res["info"])
    print("window %s .. %s, cut %s" % tuple(res["info"]["window"]))
    for pt in parts:
        for a in core.ARMS:
            print("%-8s %-7s %s" % (a, pt, stat_line(res["arms"][a][pt])))
        s = res["selection"][pt]
        print("   F1 selects %d of %d eligible points (%.1f %%); selected mean %s, rejected mean %s (point level)"
              % (s["f1_selected"], s["eligible"], 100 * s["f1_selected"] / max(1, s["eligible"]),
                 s["selected"]["mean"], s["rejected"]["mean"]))
    OUT.mkdir(parents=True, exist_ok=True)
    if mode == "dev":
        (OUT / "dev_latest.json").write_text(json.dumps(res, indent=1, default=str))
        return res
    h = {a: res["arms"][a]["holdout"] for a in core.ARMS}
    f = h["f1"]
    cond = {"mean_positive": (f["mean"] or 0) > 0, "median_positive": (f["median"] or 0) > 0,
            "positive_without_best3": (f["mean_without_best3"] or 0) > 0,
            "enough_trades": f["trades"] >= 100 and f["tokens"] >= 50,
            "folds_positive_3_of_4": sum((m or 0) > 0 for m in res["folds"]["f1"]) >= 3,
            "beats_p0": f["mean"] is not None and h["p0"]["mean"] is not None and f["mean"] > h["p0"]["mean"],
            "beats_persist": f["mean"] is not None and h["persist"]["mean"] is not None and f["mean"] > h["persist"]["mean"]}
    verdict = "inconclusive" if not cond["enough_trades"] else ("pass" if all(cond.values()) else "fail")
    res.update(conditions=cond, verdict=verdict)
    RESULT.write_text(json.dumps(res, indent=1, default=str))
    LOCK.write_text("holdout looked at %s\n" % B._ts(time.time()))
    print(json.dumps({"verdict": verdict, "conditions": cond}, indent=1))
    return res


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "dev")
