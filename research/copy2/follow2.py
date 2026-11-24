"""C2 follower (docs/MEME.md section 20.E): research/copy/follow.py's engine, stream and back-fill, unchanged, on the frozen
WALLETS-C2.json, with its own logs in C:\\Trading\\research\\copy2\\follow\\. Paper only. Arms: C2 and C2-random. No alerts
(section 20 does not ask for them). Runs only after WALLETS-C2.json is committed (section 20.D says no forward test otherwise).

    python research/copy2/follow2.py run
"""
import json
import queue
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.copy import common as C
from research.copy import follow as F
from research.copy2 import scan as SC

HOME = Path(r"C:\Trading\research\copy2\follow")


def load_roles(path=SC.LIST, check=C.frozen_check):
    """{address: [role]} for C2 and C2-random, starting at the freeze commit of WALLETS-C2.json."""
    fz = check(path)
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    roles = {}
    for arm, key in (("C2", "c2"), ("C2-random", "random")):
        for w in doc.get(key) or []:
            stats = {k: w.get(k) for k in ("a_net", "b_net", "a_coins", "b_coins", "b_score") if w.get(k) is not None}
            roles.setdefault(w["address"], []).append({"arm": arm, "tag": w["tag"], "cluster": None, "stats": stats,
                                                       "start": fz["start"]})
    return roles, fz


def run(home=HOME):
    F.refuse_live_folder()
    lock = F.take_lock(home)
    logf = Path(home) / "follow.log"

    def log(msg):
        with open(logf, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), C.mask(msg)))
    roles, fz = load_roles()
    h = C.Helius(C.load_key(), rps=8)
    eng = F.Engine(home, roles, F.dex_prices, F.make_check(h), alerter=None)
    old = eng.state.setdefault("frozen", {}).get("C2")
    if old and old["sha256"] != fz["sha256"]:
        raise SystemExit("C2 list hash changed since the first start: a new list is a new study")
    eng.state["frozen"]["C2"] = fz
    q = queue.Queue()
    st = F.Stream(h.wss, roles, q, log)
    st.start()
    try:
        while True:
            try:
                kind, v = q.get(timeout=1.0)
            except queue.Empty:
                kind = None
            if kind == "backfill":
                F.backfill(h, eng, roles, log)
            elif kind == "sig":
                F.process(h, eng, v[0], log)
                eng.state["last_sig"][v[1]] = v[0]
            eng.tick()
            eng.state["credits"] = h.credits
            eng.save()
    finally:
        st.stop.set()
        lock.close()


if __name__ == "__main__":
    if sys.argv[1:] == ["run"]:
        run()
    else:
        raise SystemExit("usage: follow2.py run")
