"""Draw model 4's two charts as plain SVG for docs/meme/ (read-only: it only reads data/accounts/meme).

  python3 tools/meme_graph.py            writes docs/meme/account.svg and docs/meme/rules.svg

account.svg  the practice account's value less its starting cash, over time (one line)
rules.svg    each rule's average trade after costs on the forward record, with its 95 % range (whole coins
             resampled), one row per rule and coin group that has closed trades
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from desk import meme  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "docs" / "meme"
BG, INK, INK2, GRID, BLUE = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df", "#2a78d6"
FONT = 'font-family="-apple-system, Segoe UI, Helvetica, Arial, sans-serif"'


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def account_svg(v):
    a, pts = v["account"], [(c["ts"], c["equity"] - v["account"]["start"]) for c in v["curve"]]
    W, H, L, R, T, B = 720, 300, 64, 24, 48, 40
    if len(pts) < 2:
        pts = pts * 2 if pts else [(0, 0.0), (1, 0.0)]
    x0, x1 = pts[0][0], pts[-1][0] if pts[-1][0] > pts[0][0] else pts[0][0] + 1
    ys = [p[1] for p in pts] + [0.0]
    y0, y1 = min(ys), max(ys)
    pad = max(1.0, (y1 - y0) * 0.1)
    y0, y1 = y0 - pad, y1 + pad
    X = lambda t: L + (t - x0) / (x1 - x0) * (W - L - R)
    Y = lambda y: T + (y1 - y) / (y1 - y0) * (H - T - B)
    ticks = [y0 + pad, 0.0, y1 - pad]
    grid = "".join('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" stroke="%s"%s/>'
                   '<text x="%d" y="%.1f" text-anchor="end" font-size="12" fill="%s">%s$%.2f</text>'
                   % (L, W - R, Y(t), Y(t), GRID, ' stroke-dasharray="3 4"' if t else "", L - 8, Y(t) + 4, INK2,
                      "+" if t > 0 else "−" if t < 0 else "", abs(t)) for t in sorted(set(round(t, 2) for t in ticks)))
    line = " ".join("%.1f,%.1f" % (X(t), Y(y)) for t, y in pts)
    last = pts[-1]
    title = "Memecoin practice account: made or lost since %s ($%.0f start)" % ((a.get("started") or "")[:10], a["start"])
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" %s role="img" aria-label="%s">'
            '<rect width="100%%" height="100%%" fill="%s"/>'
            '<text x="%d" y="26" font-size="15" font-weight="600" fill="%s">%s</text>%s'
            '<polyline points="%s" fill="none" stroke="%s" stroke-width="2" stroke-linejoin="round"/>'
            '<circle cx="%.1f" cy="%.1f" r="4" fill="%s" stroke="%s" stroke-width="2"/>'
            '<text x="%.1f" y="%.1f" text-anchor="end" font-size="12" fill="%s">$%.2f now</text>'
            '<text x="%d" y="%d" font-size="12" fill="%s">UTC, %s to %s</text></svg>'
            % (W, H, FONT, esc(title), BG, L, INK, esc(title), grid, line, BLUE, X(last[0]), Y(last[1]), BLUE, BG,
               X(last[0]) - 10, Y(last[1]) - 16, INK, a["equity"], L, H - 12, INK2,
               v["curve"][0]["at"][5:16] if v["curve"] else "", v["curve"][-1]["at"][5:16] if v["curve"] else ""))


GROUPS = (("", "any coin"), ("+screen", "passed the check"), ("+strict", "passed the strict check"),
          ("+evidence", "check + research filters"))
NAMES = {"all": "Buy everything", "hype": "Hype", "breakout": "Breakout", "calm_aged": "Calm, 1-7 days",
         "dip_aged": "Dip, 1-7 days", "aged_liquid": "Aged and liquid", "news": "News / narrative"}


def rules_svg(v):
    rows = [(NAMES[p], g, v["forward"][p + k]) for p in NAMES for k, g in GROUPS if v["forward"][p + k]["n"]]
    W, L, R, T, RH = 760, 330, 120, 56, 26
    H = T + RH * max(1, len(rows)) + 40
    vals = [0.0] + [x for _, _, s in rows for x in (s["mean"], s.get("mean_lo"), s.get("mean_hi")) if x is not None]
    lo, hi = min(vals), max(vals)
    pad = max(2.0, (hi - lo) * 0.08)
    lo, hi = lo - pad, hi + pad
    X = lambda x: L + (x - lo) / (hi - lo) * (W - L - R)
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" %s role="img" aria-label="Average trade by rule">'
           % (W, H, FONT), '<rect width="100%%" height="100%%" fill="%s"/>' % BG,
           '<text x="16" y="26" font-size="15" font-weight="600" fill="%s">Average trade after costs, by rule (forward test)</text>' % INK,
           '<text x="16" y="44" font-size="12" fill="%s">Dot = average; bar = 95%% range (whole coins resampled; shown once a line has 5+ coins). Right: trades / coins.</text>' % INK2,
           '<line x1="%.1f" x2="%.1f" y1="%d" y2="%d" stroke="%s" stroke-width="1.5"/>' % (X(0), X(0), T - 4, H - 34, INK2),
           '<text x="%.1f" y="%d" text-anchor="middle" font-size="12" fill="%s">0%% (break even)</text>' % (X(0), H - 16, INK2)]
    if not rows:
        out.append('<text x="%d" y="%d" font-size="13" fill="%s">No closed trades yet.</text>' % (L, T + 18, INK2))
    for i, (name, group, s) in enumerate(rows):
        y = T + RH * i + RH / 2
        out.append('<text x="16" y="%.1f" font-size="12.5" fill="%s">%s</text>' % (y + 4, INK, esc(name) if group == "any coin" else ""))
        out.append('<text x="150" y="%.1f" font-size="12.5" fill="%s">%s</text>' % (y + 4, INK2, esc(group)))
        if s.get("mean_lo") is not None:
            out.append('<line x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f" stroke="%s" stroke-width="2" stroke-linecap="round"/>'
                       % (X(s["mean_lo"]), X(s["mean_hi"]), y, y, BLUE))
        out.append('<circle cx="%.1f" cy="%.1f" r="5" fill="%s" stroke="%s" stroke-width="2"><title>%s, %s: %+.2f%% on %d trades</title></circle>'
                   % (X(s["mean"]), y, BLUE, BG, esc(name), esc(group), s["mean"], s["n"]))
        out.append('<text x="%d" y="%.1f" text-anchor="end" font-size="12" fill="%s">%+.1f%% · %d / %d</text>'
                   % (W - 12, y + 4, INK, s["mean"], s["n"], s["tokens"]))
    out.append("</svg>")
    return "".join(out)


def backtest_svg():
    """Held-out average per rule from research/meme/out/results.json (the backtest, run once)."""
    import json
    f = Path(__file__).resolve().parent.parent / "research" / "meme" / "out" / "results.json"
    if not f.exists():
        return None
    d = json.loads(f.read_text())
    rows = [(NAMES[p], v["holdout"]) for p, v in d["policies"].items() if v["holdout"].get("trades")]
    W, L, R, T, RH = 760, 200, 150, 56, 30
    H = T + RH * len(rows) + 40
    vals = [0.0] + [h["mean"] for _, h in rows]
    lo, hi = min(vals) - 2, max(vals) + 2
    X = lambda x: L + (x - lo) / (hi - lo) * (W - L - R)
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" %s role="img" aria-label="Backtest held-out average by rule">'
           % (W, H, FONT), '<rect width="100%%" height="100%%" fill="%s"/>' % BG,
           '<text x="16" y="26" font-size="15" font-weight="600" fill="%s">Backtest, held-out half: average trade after costs</text>' % INK,
           '<text x="16" y="44" font-size="12" fill="%s">%s to %s, 3.5%% round trip. No rule passed. Right: trades / coins.</text>'
           % (INK2, esc(d["window"]["cut"][:10]), esc(d["window"]["end"][:10])),
           '<line x1="%.1f" x2="%.1f" y1="%d" y2="%d" stroke="%s" stroke-width="1.5"/>' % (X(0), X(0), T - 4, H - 34, INK2),
           '<text x="%.1f" y="%d" text-anchor="middle" font-size="12" fill="%s">0%% (break even)</text>' % (X(0), H - 16, INK2)]
    for i, (name, h) in enumerate(rows):
        y = T + RH * i + RH / 2
        x0, x1 = sorted((X(0), X(h["mean"])))
        out.append('<text x="16" y="%.1f" font-size="12.5" fill="%s">%s</text>' % (y + 4, INK, esc(name)))
        out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="14" rx="4" fill="%s"/>' % (x0, y - 7, max(1.0, x1 - x0), BLUE))
        out.append('<text x="%d" y="%.1f" text-anchor="end" font-size="12" fill="%s">%+.1f%% · %d / %d</text>'
                   % (W - 12, y + 4, INK, h["mean"], h["trades"], h["tokens"]))
    out.append("</svg>")
    return "".join(out)


def main():
    v = meme.view()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "account.svg").write_text(account_svg(v))
    (OUT / "rules.svg").write_text(rules_svg(v))
    bt = backtest_svg()
    if bt:
        (OUT / "backtest.svg").write_text(bt)
    print("wrote", OUT / "account.svg", "and", OUT / "rules.svg")


if __name__ == "__main__":
    main()
