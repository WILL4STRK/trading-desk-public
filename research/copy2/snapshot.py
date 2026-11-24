"""C2 coin snapshots (docs/MEME.md section 20.A). DexScreener only: no Helius, no GeckoTerminal.

    python research/copy2/snapshot.py take      one daily snapshot (00:05 UTC or later, 7 days in a row from the first 00:05 after T20)
    python research/copy2/snapshot.py rank      after 7 snapshots: the ranked candidate list -> C:\\Trading\\research\\copy2\\ranked.json
    python research/copy2/snapshot.py status

Candidates = every Solana token in the desk's discoveries.jsonl (read through a shared handle, read-only) at snapshot time, plus the
tokens in input\\ranking-page.json (the dexscreener.com ranking page read once, on the first snapshot day, with its URL and time).
"""
import argparse
import json
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.copy import common as C

T20 = 1795497832                    # author time of 2612d45 (section 20 heading), 2026-11-24 05:23:52 UTC
T20_COMMIT = "2612d4565cd02e94fa27be18935135e4ea49d17a"
HOME = Path(r"C:\Trading\research\copy2")
DESK_DISC = Path(r"C:\Users\Willr\trading-desk\data\accounts\meme\discoveries.jsonl")
DEX = "https://api.dexscreener.com/tokens/v1/solana/%s"
DAYS = 7
HERE = Path(__file__).resolve().parent
EXCL = HERE / "coin_exclusions.json"


def schedule(t20=T20):
    """The 7 snapshot days (YYYY-MM-DD, UTC): from the first 00:05 UTC after T20."""
    day0 = int(t20 // 86400) * 86400
    first = day0 + 300 if day0 + 300 > t20 else day0 + 86400 + 300
    return [time.strftime("%Y-%m-%d", time.gmtime(first + i * 86400)) for i in range(DAYS)]


def desk_candidates(raw):
    """Solana mints from discoveries.jsonl bytes."""
    out = set()
    for ln in raw.decode("utf-8", "replace").splitlines():
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if d.get("chain") == "solana" and str(d.get("key", "")).startswith("solana:"):
            out.add(d["key"].split(":", 1)[1])
    return out


def page_candidates(path):
    p = Path(path)
    if not p.exists():
        return set(), None
    d = json.loads(p.read_text(encoding="utf-8"))
    return {m for m in d.get("mints", []) if C.B58.match(m)}, {k: d.get(k) for k in ("url", "read_at", "pages")}


def volumes(mints, http=C.http_json, pace=None):
    """{mint: {vol24, price, symbol, pairs}} summed over every Solana pair whose base token is the mint."""
    pace = pace or C.Pacer(60, 60.0)
    out = {}
    ms = sorted(m for m in mints if C.B58.match(m))
    for i in range(0, len(ms), 30):
        chunk = ms[i:i + 30]
        pace.wait()
        try:
            pairs = http(DEX % ",".join(chunk))
        except RuntimeError:
            try:
                time.sleep(5)
                pairs = http(DEX % ",".join(chunk))
            except RuntimeError:
                continue
        for p in pairs if isinstance(pairs, list) else []:
            if p.get("chainId") not in (None, "solana"):
                continue
            base = (p.get("baseToken") or {}).get("address")
            if base not in chunk:
                continue
            o = out.setdefault(base, {"vol24": 0.0, "price": None, "symbol": (p.get("baseToken") or {}).get("symbol"), "pairs": 0,
                                      "_liq": -1})
            o["vol24"] += float((p.get("volume") or {}).get("h24") or 0)
            o["pairs"] += 1
            liq = float((p.get("liquidity") or {}).get("usd") or 0)
            if liq > o["_liq"] and p.get("priceUsd"):
                o["_liq"], o["price"] = liq, float(p["priceUsd"])
    for o in out.values():
        o.pop("_liq", None)
    return out


def take(home=HOME, now=None, http=C.http_json, desk=DESK_DISC, t20=T20):
    now = time.time() if now is None else now
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    sched = schedule(t20)
    if day not in sched:
        raise SystemExit("%s is not a snapshot day (%s .. %s)" % (day, sched[0], sched[-1]))
    if now % 86400 < 300:
        raise SystemExit("before 00:05 UTC")
    out = Path(home) / "snapshots" / ("%s.json" % day)
    if out.exists():
        raise SystemExit("%s exists: one snapshot a day" % out.name)
    cand = desk_candidates(C.read_shared(desk))
    pc, page = page_candidates(Path(home) / "input" / "ranking-page.json")
    if day == sched[0] and page is None:
        raise SystemExit("input\\ranking-page.json is missing: the ranking page is read on the first snapshot day, before this")
    allm = cand | pc
    vols = volumes(allm, http=http)
    doc = {"day": day, "t": now, "candidates": len(allm), "from_desk": len(cand), "from_page": len(pc), "page": page,
           "got": len(vols), "vol": vols}
    C.atomic_write(out, json.dumps(doc, sort_keys=True))
    return doc


def excluded(mint, symbol, prices, ex):
    """Section 20.A item 3 (the age rule is checked by the scan): exclusion list by mint or symbol, or stable-looking prices."""
    mints = {m for v in ex["mints"].values() for m in v}
    syms = {s.upper() for v in ex["symbols"].values() for s in v}
    if mint in mints:
        return "listed_mint"
    if (symbol or "").upper() in syms:
        return "listed_symbol"
    if len(prices) == DAYS and all(p is not None and 0.97 <= p <= 1.03 for p in prices):
        return "stable_price"
    return None


def rank(home=HOME, t20=T20):
    sched = schedule(t20)
    snaps = []
    for d in sched:
        p = Path(home) / "snapshots" / ("%s.json" % d)
        if not p.exists():
            raise SystemExit("snapshot %s missing; rank needs all 7" % d)
        snaps.append((d, C.sha256_file(p), json.loads(p.read_text(encoding="utf-8"))))
    ex = json.loads(EXCL.read_text(encoding="utf-8"))
    tot, sym, prices = {}, {}, {}
    for _, _, s in snaps:
        for m, v in s["vol"].items():
            tot[m] = tot.get(m, 0.0) + (v.get("vol24") or 0.0)
            sym.setdefault(m, v.get("symbol"))
            prices.setdefault(m, []).append(v.get("price"))
    keep, drops = [], {}
    for m in sorted(tot, key=lambda m: (-tot[m], m)):
        why = excluded(m, sym.get(m), prices.get(m, []), ex)
        if why:
            drops[why] = drops.get(why, 0) + 1
            continue
        keep.append({"mint": m, "symbol": sym.get(m), "vol7": round(tot[m], 2)})
    doc = {"t20": t20, "snapshots": [{"day": d, "sha256": h} for d, h, _ in snaps], "excluded": drops, "ranked": keep}
    C.atomic_write(Path(home) / "ranked.json", json.dumps(doc, indent=1))
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["take", "rank", "status"])
    a = ap.parse_args(argv)
    if a.what == "take":
        d = take()
        print(json.dumps({k: d[k] for k in ("day", "candidates", "from_desk", "from_page", "got")}))
    elif a.what == "rank":
        d = rank()
        print(json.dumps({"ranked": len(d["ranked"]), "excluded": d["excluded"]}))
    else:
        have = sorted(p.stem for p in (HOME / "snapshots").glob("*.json")) if (HOME / "snapshots").exists() else []
        print(json.dumps({"schedule": schedule(), "have": have}))


if __name__ == "__main__":
    main()
