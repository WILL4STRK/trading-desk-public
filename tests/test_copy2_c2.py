"""C2 deep scan (docs/MEME.md section 20): snapshot schedule and ranking, exclusions, two-window trades after desk costs
(outside-set coins only), the 40 % rule, the chance test, bundlers and snipers, the credit hard stop, resume without
duplicates, streaming results, nothing at or after T20. Synthetic data in temp folders; no network."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from research.copy import common as C
from research.copy2 import scan as SC
from research.copy2 import snapshot as S
from research.copy2 import follow2 as F2

T = S.T20
W1 = "Wa11et1111111111111111111111111111111111111"
W2 = "Wa11et2222222222222222222222222222222222222"
W3 = "Wa11et3333333333333333333333333333333333333"


def mint(i):
    return ("M%d" % i).ljust(43, "1")


def sw(ts, m, side, tokens, sol, sig=None):
    return {"ts": ts, "mint": m, "side": side, "tokens": tokens, "sol": sol, "sig": sig or "%s%s%s" % (ts, m, side), "slot": int(ts)}


def swap_tx(sig, ts, signer, m, tok, lam, slot=1):
    pre, post = [], []
    if tok[0]:
        pre.append({"accountIndex": 1, "mint": m, "owner": signer, "uiTokenAmount": {"amount": str(int(tok[0] * 1e6)), "decimals": 6}})
    if tok[1]:
        post.append({"accountIndex": 1, "mint": m, "owner": signer, "uiTokenAmount": {"amount": str(int(tok[1] * 1e6)), "decimals": 6}})
    return {"slot": slot, "blockTime": ts,
            "meta": {"err": None, "preBalances": [int(lam[0]), 0], "postBalances": [int(lam[1]), 0], "preTokenBalances": pre,
                     "postTokenBalances": post, "innerInstructions": []},
            "transaction": {"signatures": [sig], "message": {"accountKeys": [{"pubkey": signer, "signer": True},
                                                                              {"pubkey": "TokAcct", "signer": False}],
                                                             "instructions": []}}}


class Temp(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="c2test-"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class Snapshots(Temp):
    def test_schedule_starts_first_0005_after_t20(self):
        self.assertEqual(S.schedule(), ["2026-11-25", "2026-11-26", "2026-11-27", "2026-11-28", "2026-11-29", "2026-11-30", "2026-12-01"])

    def desk(self, mints):
        p = self.dir / "disc.jsonl"
        p.write_text("\n".join(json.dumps({"chain": "solana", "key": "solana:" + m}) for m in mints) + "\n"
                     + json.dumps({"chain": "base", "key": "base:0xabc"}) + "\n", encoding="utf-8")
        return p

    def http(self, url):
        ms = url.rsplit("/", 1)[1].split(",")
        out = []
        for m in ms:
            out += [{"chainId": "solana", "baseToken": {"address": m, "symbol": "S" + m[1]}, "priceUsd": "2", "volume": {"h24": 100},
                     "liquidity": {"usd": 10}},
                    {"chainId": "solana", "baseToken": {"address": m, "symbol": "S" + m[1]}, "priceUsd": "3", "volume": {"h24": 50},
                     "liquidity": {"usd": 99}}]
        return out

    def test_take_rules_and_volume_sum(self):
        desk = self.desk([mint(1), mint(2)])
        day0 = 1795564800 + 400                       # 2026-11-25 00:06:40 UTC
        with self.assertRaises(SystemExit):           # ranking page must be read on the first day
            S.take(self.dir, now=day0, http=self.http, desk=desk)
        (self.dir / "input").mkdir()
        (self.dir / "input" / "ranking-page.json").write_text(json.dumps({"url": "https://dexscreener.com/solana", "read_at": "x",
                                                                          "mints": [mint(3)]}), encoding="utf-8")
        d = S.take(self.dir, now=day0, http=self.http, desk=desk)
        self.assertEqual((d["from_desk"], d["from_page"], d["got"]), (2, 1, 3))
        self.assertEqual(d["vol"][mint(1)]["vol24"], 150.0)
        self.assertEqual(d["vol"][mint(1)]["price"], 3.0)          # the most liquid pair's price
        with self.assertRaises(SystemExit):
            S.take(self.dir, now=day0 + 60, http=self.http, desk=desk)     # one a day
        with self.assertRaises(SystemExit):
            S.take(self.dir, now=day0 - 86400, http=self.http, desk=desk)  # not a snapshot day
        with self.assertRaises(SystemExit):
            S.take(self.dir, now=1795564800 + 100, http=self.http, desk=desk)   # before 00:05

    def test_rank_needs_seven_and_applies_exclusions(self):
        (self.dir / "snapshots").mkdir()
        with self.assertRaises(SystemExit):
            S.rank(self.dir)
        for i, d in enumerate(S.schedule()):
            vol = {mint(1): {"vol24": 10, "price": 0.5, "symbol": "AAA"},
                   mint(2): {"vol24": 30, "price": 1.0, "symbol": "STBL"},
                   "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": {"vol24": 999, "price": 1.0, "symbol": "USDC"},
                   mint(4): {"vol24": 5, "price": 9.0, "symbol": "jup"}}
            if i == 0:
                vol[mint(5)] = {"vol24": 20, "price": 1.0, "symbol": "NEW"}
            (self.dir / "snapshots" / (d + ".json")).write_text(json.dumps({"vol": vol}), encoding="utf-8")
        r = S.rank(self.dir)
        self.assertEqual([x["mint"] for x in r["ranked"]], [mint(1), mint(5)])
        self.assertEqual(r["excluded"], {"listed_mint": 1, "stable_price": 1, "listed_symbol": 1})


class Trades(unittest.TestCase):
    def test_costs_windows_basis_and_t20(self):
        a, b, out = SC.H0 + 10, SC.SPLIT + 10, set()
        s = [sw(SC.H0 - 100, mint(9), "buy", 10, 1.0),          # bought before the history: no basis
             sw(a, mint(9), "sell", 10, 5.0),
             sw(a, mint(1), "buy", 100, 1.0), sw(a + 5, mint(1), "sell", 50, 1.0),
             sw(b, mint(1), "sell", 50, 2.0),
             sw(T + 1, mint(2), "buy", 1, 1.0), sw(T + 2, mint(2), "sell", 1, 9.0)]
        tr = SC.trades(s, {mint(1)})
        self.assertEqual([(w, m) for w, m, _, _ in tr], [("A", mint(1)), ("B", mint(1))])
        self.assertAlmostEqual(tr[0][2], 1.0 * 0.9825 - 0.5 * 1.0175)
        self.assertAlmostEqual(tr[1][2], 2.0 * 0.9825 - 0.5 * 1.0175)
        self.assertTrue(all(ins for *_, ins in tr))

    def test_window_rules(self):
        five = [(mint(i), 1.0) for i in range(5)]
        self.assertTrue(SC.window_ok(five))
        self.assertFalse(SC.window_ok(five[:4]))                         # 4 coins
        self.assertFalse(SC.window_ok(five[:4] + [(mint(4), 3.0)]))      # one trade 3 of 7 = 43 %
        self.assertFalse(SC.window_ok([(m, -1.0) for m, _ in five]))
        a = [("A", mint(i), 1.0, False) for i in range(5)] + [("A", mint(9), 50.0, True)]
        b = [("B", mint(i), 1.0, False) for i in range(5)]
        oa, ob = SC.split_outside(a + b)
        self.assertEqual(len(oa), 5)                                    # the set coin's 50 SOL never counts
        self.assertTrue(SC.qualifies(oa, ob))

    def test_chance_and_clearly_above(self):
        good = ([(mint(i), 1.0) for i in range(5)], [(mint(i), 1.0) for i in range(5)])
        c1, mean, p95, p99 = SC.chance([good] * 3, perms=50)
        self.assertEqual((mean, p99), (3, 3))                          # identical trades: shuffling cannot change it
        self.assertFalse(SC.clearly_above(3, mean, p99))
        mixed = ([(mint(i), v) for i, v in enumerate([5, 5, 5, -1, -1])], [(mint(i), v) for i, v in enumerate([-5, -5, -5, 1, 1])])
        a = SC.chance([mixed] * 20, perms=200)
        b = SC.chance([mixed] * 20, perms=200)
        self.assertEqual(a, b)                                          # seed 20
        self.assertTrue(SC.clearly_above(20, 2.0, 6))
        self.assertFalse(SC.clearly_above(7, 4.0, 6))                  # above p99 but not twice the mean

    def test_bundlers(self):
        groups = {("m", 5): [W1, W2, W3], ("n", 6): [W1, W3]}
        fund = {W1: "F", W2: "F", W3: "G"}
        b, multi = SC.bundles(groups, fund, lambda f: False)
        self.assertEqual((b, multi), ({W1, W2}, set()))
        fund2 = {W1: "F", W2: "F", W3: "F"}
        b2, multi2 = SC.bundles(groups, fund2, lambda f: False)
        self.assertEqual((b2, multi2), ({W1, W2, W3}, {W1, W3}))
        self.assertEqual(SC.bundles(groups, fund2, lambda f: True), (set(), set()))   # an exchange funder is not a bundle


class FakeHelius:
    """Helius stand-in: pages of a mint's first-24 h transactions; counts credits like the real one."""

    def __init__(self, pages):
        self.pages, self.calls, self.credits = pages, [], 0

    def rpc(self, method, params):
        self.calls.append((method, params))
        cfg = params[1]
        self.credits += 10
        lt = cfg["filters"].get("blockTime", {}).get("lt")
        assert lt is None or lt <= T, "asked past T20"
        tok = cfg.get("paginationToken")
        i = 0 if tok is None else int(tok)
        if i >= len(self.pages):
            return {"data": []}
        return {"data": self.pages[i], "paginationToken": str(i + 1) if i + 1 < len(self.pages) else None}


class ScanDB(Temp):
    def coin(self, db, m, t0):
        db.c.execute("insert into coins(mint, rank, kind, t0, status) values(?,?,?,?,?)", (m, 0, "top", t0, "ok"))
        db.c.commit()

    def test_budget_hard_stop_and_resume_without_duplicates(self):
        t0 = T - 5 * 86400
        pages = [[swap_tx("s%d%d" % (p, i), t0 + 100 * p + i, (W1, W2, W3)[i], mint(1), (0, 10), (5e9, 4e9), slot=10 + p)
                  for i in range(3)] for p in range(4)]
        db = SC.DB(self.dir / "scan.db")
        self.coin(db, mint(1), t0)
        h = FakeHelius(pages)
        m = SC.Meter(h, db, budget=20)                  # two pages, then stop
        with self.assertRaises(SC.Budget):
            SC.fetch_buys(db, m, log=lambda *_: None)
        self.assertEqual(db.get("credits"), 20)
        self.assertEqual(h.calls[0][1][1]["maxSupportedTransactionVersion"], C.TX_VERSION)   # version-1 transactions load
        self.assertEqual(db.c.execute("select count(*) from buys").fetchone()[0], 6)
        db2 = SC.DB(self.dir / "scan.db")               # a new process, a bigger budget
        m2 = SC.Meter(FakeHelius(pages), db2, budget=1000)
        SC.fetch_buys(db2, m2, log=lambda *_: None)
        self.assertEqual(db2.c.execute("select count(*) from buys").fetchone()[0], 12)
        self.assertEqual(db2.c.execute("select count(distinct sig) from buys").fetchone()[0], 12)
        self.assertEqual(db2.get("credits"), 20 + 20)   # resumed at page 3: pages 3 and 4 only
        self.assertEqual(db2.c.execute("select first_slot, done from coins").fetchone(), (10, 1))

    def test_marks_scope_dev_and_snipers(self):
        db = SC.DB(self.dir / "scan.db")
        for i in range(3):
            self.coin(db, mint(i), T - 9 * 86400)
            db.c.execute("update coins set first_slot=100, payer=? where mint=?", (W3 if i == 0 else None, mint(i)))
            for w, slot in ((W1, 200), (W2, 101 if i == 2 else 200), (W3, 300)):
                db.c.execute("insert into buys values(?,?,?,?,?,?,?)", (mint(i), w, 0, slot, 1, 1, "%s%s" % (w, i)))
        db.c.execute("insert into buys values(?,?,?,?,?,?,?)", (mint(0), "Other", 0, 500, 1, 1, "o"))
        db.c.commit()
        SC.mark_wallets(db, log=lambda *_: None)
        got = dict(db.c.execute("select wallet, coalesce(reason, status) from wallets").fetchall())
        self.assertEqual(got, {W1: "candidate", W2: "sniper", W3: "creator_dev"})   # "Other" bought 1 coin: not fetched

    def test_scan_streams_results_and_select_gate(self):
        db = SC.DB(self.dir / "scan.db")
        good_a = [[mint(10 + i), 1.0] for i in range(5)]
        rows = {"q%02d" % i: {"sigs": 10, "a": good_a, "b": good_a, "swaps_per_day": 1.0 + i, "set_pnl": 0, "eligible": True}
                for i in range(4)}
        for w, r in rows.items():
            db.c.execute("insert into results values(?,?)", (w, json.dumps(r)))
        db.c.commit()
        rep = SC.select(db, out=self.dir / "W.json", log=lambda *_: None)
        self.assertEqual(rep["qualified"], 4)
        self.assertFalse(rep["clearly_above_chance"])               # every shuffle also passes: no list, no forward test
        self.assertFalse((self.dir / "W.json").exists())

    def test_select_writes_list_when_clearly_above(self):
        db = SC.DB(self.dir / "scan.db")
        for i in range(60):
            if i < 20:       # 6 wins + 2 losses in each window: passes; shuffled, only when the losses split 2 and 2 (43 %)
                a = [[mint(i * 20 + k), 1.0] for k in range(6)] + [[mint(i * 20 + 6 + k), -1.5] for k in range(2)]
                b = [[mint(i * 20 + 10 + k), 1.0] for k in range(6)] + [[mint(i * 20 + 16 + k), -1.5] for k in range(2)]
            else:            # one window up, one down: never passes, shuffled or not
                a = [[mint(i * 20 + k), 2.0] for k in range(6)]
                b = [[mint(i * 20 + 10 + k), -2.0] for k in range(6)]
            db.c.execute("insert into results values(?,?)", ("w%03d" % i, json.dumps(
                {"sigs": 1, "a": a, "b": b, "swaps_per_day": float(i % 7), "set_pnl": 0, "eligible": True})))
        db.c.commit()
        out = self.dir / "W.json"
        rep = SC.select(db, out=out, log=lambda *_: None)
        self.assertEqual(rep["qualified"], 20)
        self.assertTrue(rep["clearly_above_chance"], rep)
        self.assertLess(rep["null_mean"], 10)
        doc = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(len(doc["c2"]), 20)
        self.assertEqual(doc["c2"][0]["tag"], "C2-01")
        self.assertEqual(len(doc["random"]), 20)


class FollowFrozen(Temp):
    def test_roles_from_frozen_list(self):
        g = lambda *a: subprocess.run(["git", "-C", str(self.dir)] + list(a), check=True, capture_output=True)
        g("init", "-q"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
        p = self.dir / "WALLETS-C2.json"
        p.write_text(json.dumps({"c2": [{"address": W1, "tag": "C2-01", "b_net": 2.0}],
                                 "random": [{"address": W2, "tag": "C2R-01"}]}), encoding="utf-8")
        g("add", "WALLETS-C2.json"); g("commit", "-q", "-m", "freeze")
        roles, fz = F2.load_roles(p, check=lambda x: C.frozen_check(x, repo=self.dir))
        self.assertEqual((roles[W1][0]["arm"], roles[W2][0]["arm"]), ("C2", "C2-random"))
        self.assertEqual(roles[W1][0]["start"], fz["start"])
        p.write_text("{}", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            F2.load_roles(p, check=lambda x: C.frozen_check(x, repo=self.dir))


if __name__ == "__main__":
    unittest.main()
