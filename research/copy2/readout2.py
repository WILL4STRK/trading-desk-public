"""C2 readout (docs/MEME.md section 20.E = 19.D): formal looks at 100/200/400/800 closed C2 copies against C2-random,
alpha 0.0125 each, written once. Uses research/copy/readout.py's checks unchanged.

    python research/copy2/readout2.py look 100
"""
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.copy import common as C
from research.copy import readout as R
from research.copy2 import follow2 as F2


def look_rows(home, n):
    fills = sorted((r for r in R._rows(Path(home) / "fills.jsonl") if r["leg"] == "buy" and r["arm"] == "C2"),
                   key=lambda r: (r["t"], r["id"]))
    if len(fills) < n:
        return None, "only %d copied buys" % len(fills)
    ids = {r["id"] for r in fills[:n]}
    t_end = fills[n - 1]["t"]
    closes = {r["id"]: r for r in R._rows(Path(home) / "closes.jsonl")}
    if not ids <= set(closes):
        return None, "%d of the first %d still open" % (len(ids - set(closes)), n)
    ctl = [r for r in closes.values() if r["arm"] == "C2-random" and r["entry_t"] <= t_end]
    return ([closes[i] for i in sorted(ids)], {"C2-random": ctl}), None


def look(n, home=F2.HOME, boot=R.BOOT):
    if n not in R.LOOKS:
        raise SystemExit("formal looks are only at %s" % (R.LOOKS,))
    out = Path(home) / ("look-C2-%d.json" % n)
    if out.exists():
        raise SystemExit("%s exists: each formal look runs once" % out.name)
    got, why = look_rows(home, n)
    if got is None:
        raise SystemExit("look C2 %d not ready: %s" % (n, why))
    res = R.evaluate(got[0], got[1], boot)
    res.update({"arm": "C2", "look": n, "alpha": R.ALPHA_LOOK, "boot": boot, "seed": R.SEED})
    C.atomic_write(out, json.dumps(res, indent=1, sort_keys=True))
    return res


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "look":
        print(json.dumps(look(int(sys.argv[2])), indent=1, sort_keys=True))
    else:
        raise SystemExit("usage: readout2.py look N")
