"""C1 / C1-S copy study (docs/MEME.md section 19): no post-T19 data in discovery, frozen lists (hash and commit check), fills at
least 3 s after detection, fraction-mirrored sells, copy-gap math, the key and caller names never in logs, restart without
duplicates, blocked coins skipped, each C1-S linking rule on hand-made fixtures. Synthetic data in temp folders only; no
network."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from research.copy import common as C
from research.copy import discover as D
from research.copy import follow as F
from research.copy import readout as R

T = C.T19
W1 = "Wa11et1111111111111111111111111111111111111"
W2 = "Wa11et2222222222222222222222222222222222222"
MINT = "M1nt111111111111111111111111111111111111111"
MINT2 = "M1nt222222222222222222222222222222222222222"
POOL = "Poo1111111111111111111111111111111111111111"
KEY = "test-helius-key-0123456789abcdef"


def tx(sig, ts, signer, mint=MINT, tok=(0.0, 1000.0), lam=(5e9, 4e9), slot=1, err=None, extra_owner=None):
    """A minimal jsonParsed transaction: signer's token balance of mint goes tok[0] -> tok[1], its lamports lam[0] -> lam[1]."""
    pre, post = [], []
    if tok[0]:
        pre.append({"accountIndex": 1, "mint": mint, "owner": signer, "uiTokenAmount": {"amount": str(int(tok[0] * 1e6)), "decimals": 6}})
    if tok[1]:
        post.append({"accountIndex": 1, "mint": mint, "owner": signer, "uiTokenAmount": {"amount": str(int(tok[1] * 1e6)), "decimals": 6}})
    return {"slot": slot, "blockTime": ts,
            "meta": {"err": err, "preBalances": [int(lam[0]), 0], "postBalances": [int(lam[1]), 0],
                     "preTokenBalances": pre, "postTokenBalances": post, "innerInstructions": []},
            "transaction": {"signatures": [sig],
                            "message": {"accountKeys": [{"pubkey": signer, "signer": True}, {"pubkey": "TokAcct", "signer": False}],
                                        "instructions": []}}}


def sol_transfer(sig, ts, src, dst, lamports=1e9):
    return {"slot": 1, "blockTime": ts,
            "meta": {"err": None, "preBalances": [10e9, 0], "postBalances": [9e9, 1e9], "preTokenBalances": [],
                     "postTokenBalances": [], "innerInstructions": []},
            "transaction": {"signatures": [sig],
                            "message": {"accountKeys": [{"pubkey": src, "signer": True}, {"pubkey": dst, "signer": False}],
                                        "instructions": [{"program": "system", "parsed": {"type": "transfer", "info": {
                                            "source": src, "destination": dst, "lamports": lamports}}}]}}}


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class TempHome(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="c1test-"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


# ---- discovery uses no data at or after T19 ----------------------------------------------------------------------------------

class NoPostT19(TempHome):
    def test_desk_lines_after_t19_are_ignored(self):
        p = self.dir / "d.jsonl"
        rows = [{"ts": T - 3 * 86400, "key": "solana:A", "chain": "solana", "pool": POOL, "px": 1.0, "features": {"age_h": 1}},
                {"ts": T - 3600, "key": "solana:B", "chain": "solana", "pool": POOL, "px": 1.0},
                {"ts": T + 1, "key": "solana:C", "chain": "solana", "pool": POOL, "px": 1.0},
                {"ts": T - 4 * 86400, "key": "base:D", "chain": "base", "pool": POOL, "px": 1.0}]
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        first, d0 = D.load_desk(p)
        self.assertNotIn("solana:C", first)
        self.assertEqual(d0, T - 4 * 86400)
        uni = D.universe(first)
        self.assertEqual(set(uni), {"solana:A"})        # B's 24 h window does not close before T19

    def test_bars_after_window_or_t19_never_count(self):
        ts0 = T - 86400 - 600
        bars = [[ts0, 1, 1, 1, 1.0, 0], [ts0 + 86400 - 300, 1, 9, 1, 9.0, 0], [ts0 + 86400, 1, 9, 1, 9.0, 0]]
        self.assertEqual(D.runner(bars, ts0, 1.0)[0], True)       # last bar inside the window closes 9x
        late = T - 1000
        bars2 = [[late - 600, 1, 1, 1, 1.0, 0], [T - 200, 1, 5, 1, 5.0, 0]]      # closes after T19
        self.assertEqual(D.runner(bars2, late - 600, 1.0)[0], False)
        self.assertEqual(D.runner([[ts0, 1, 5, 1, 1.9, 0]], ts0, 1.0)[0], False)  # a high wick is not a close

    def test_run_start_and_early_windows(self):
        ts0 = T - 2 * 86400
        bars = [[ts0, 0, 0, 0, 1.1, 0], [ts0 + 300, 0, 0, 0, 1.3, 0], [ts0 + 600, 0, 0, 0, 2.5, 0]]
        hit, t_run = D.runner(bars, ts0, 1.0)
        self.assertTrue(hit)
        self.assertEqual(t_run, ts0 + 300)
        ws = D.early_windows(ts0, 2.0, t_run)
        self.assertEqual(ws[0], (ts0 - 7200, ts0 - 7200 + 1800))
        self.assertEqual(ws[1], (ts0 - 7200, t_run))      # max(t_c, t_run - 6 h) = t_c
        ws2 = D.early_windows(T - 600, 0.0, T + 3600)
        self.assertTrue(all(b <= T for _, b in ws2))

    def test_fetch_never_asks_past_t19_and_drops_late_rows(self):
        sent = []

        class H:
            credits = 0

            def rpc(self, m, params):
                sent.append(params)
                return {"data": [tx("s1", T - 10, W1), tx("s2", T, W1), tx("s3", T + 50, W1)]}
        fx = D.Fetch(H(), self.dir / "cache")
        got, _ = fx.txs(W1, T - 1000, T + 10000)
        self.assertEqual([t["transaction"]["signatures"][0] for t in got], ["s1"])
        self.assertLessEqual(sent[0][1]["filters"]["blockTime"]["lt"], T)

    def test_wallet_stats_only_inside_window(self):
        sw = [{"ts": T - 100, "sig": "a", "slot": 1, "mint": MINT, "side": "buy", "tokens": 10, "sol": 1.0},
              {"ts": T - 50, "sig": "b", "slot": 2, "mint": MINT, "side": "sell", "tokens": 10, "sol": 2.0},
              {"ts": T + 5, "sig": "c", "slot": 3, "mint": MINT2, "side": "buy", "tokens": 1, "sol": 50.0}]
        st = D.wallet_stats(sw, T - 86400)
        self.assertEqual(st["swaps"], 2)
        self.assertAlmostEqual(st["realized_sol"], 1.0)


class Exclusions(unittest.TestCase):
    def test_bot_heavy_sandwich_exchange(self):
        base = {"ts": T - 5000, "sig": "x", "slot": 1, "mint": MINT, "side": "buy", "tokens": 1, "sol": 0.1}
        burst = [dict(base, ts=T - 5000 + i, sig="b%d" % i, slot=i) for i in range(21)]
        self.assertEqual(D.bot_reason(D.wallet_stats(burst, T - 86400), 10), "bot_mev")
        sand = []
        for s in (5, 9):
            sand += [dict(base, ts=T - 900 + s, sig="p%d" % s, slot=s), dict(base, ts=T - 900 + s, sig="q%d" % s, slot=s, side="sell")]
        self.assertEqual(D.bot_reason(D.wallet_stats(sand, T - 86400), 10), "bot_mev")
        heavy = [dict(base, ts=T - 80000 + 70 * i, sig="h%d" % i, slot=100 + i) for i in range(51)]
        self.assertEqual(D.bot_reason(D.wallet_stats(heavy, T - 86400), 10), "heavy")
        self.assertEqual(D.bot_reason(D.wallet_stats([base], T - 86400), 10000), "exchange")
        self.assertIsNone(D.bot_reason(D.wallet_stats([base], T - 86400), 10))

    def test_top_volume_and_rank_and_random_draw(self):
        sol = {"w%03d" % i: float(i) for i in range(150)}
        top = D.top_volume(sol)
        self.assertEqual(len(top), 100)
        self.assertNotIn("w000", top)
        q = [{"address": "b", "runners": 5, "realized_sol": 1.0}, {"address": "a", "runners": 6, "realized_sol": 0.1}]
        self.assertEqual([x["address"] for x in D.rank(q)], ["a", "b"])
        pool = [{"address": "p%02d" % i, "swaps_per_day": float(i)} for i in range(50)]
        c1 = [{"swaps_per_day": 3.0}, {"swaps_per_day": 45.0}]
        a, b = D.draw_random(c1, list(pool)), D.draw_random(c1, list(pool))
        self.assertEqual(a, b)                         # seed 19: same draw every time
        self.assertLess(a[0]["swaps_per_day"], 10)
        self.assertGreaterEqual(a[1]["swaps_per_day"], 40)


# ---- frozen lists ----------------------------------------------------------------------------------------------------------

class Frozen(TempHome):
    def git(self, *a):
        subprocess.run(["git", "-C", str(self.dir)] + list(a), check=True, capture_output=True)

    def setUp(self):
        super().setUp()
        self.git("init", "-q")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")

    def freeze(self, name, doc):
        p = self.dir / name
        p.write_text(json.dumps(doc), encoding="utf-8")
        self.git("add", name)
        self.git("commit", "-q", "-m", "freeze")
        return p

    def test_both_lists_pass_then_fail_on_edit_or_recommit(self):
        for name in ("WALLETS-C1.json", "WALLETS-C1S.json"):
            p = self.freeze(name, {"c1": [], "sides": []})
            fz = C.frozen_check(p, repo=self.dir)
            self.assertEqual(fz["sha256"], C.sha256_file(p))
            p.write_text(json.dumps({"c1": [{"address": W1}]}), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                C.frozen_check(p, repo=self.dir)            # edited after the freeze
            self.git("add", name)
            self.git("commit", "-q", "-m", "change")
            with self.assertRaises(RuntimeError):
                C.frozen_check(p, repo=self.dir)            # a second commit touched it

    def test_load_roles_uses_freeze_commit_time(self):
        p = self.freeze("WALLETS-C1S.json", {"sides": [{"address": W1, "tag": "K03-side-1", "cluster": "K03"}],
                                             "mains": [{"address": W2, "tag": "K03-main", "cluster": "K03"}]})
        roles, fz = F.load_roles(self.dir / "none.json", p, check=lambda x: C.frozen_check(x, repo=self.dir))
        self.assertEqual(roles[W1][0]["arm"], "C1S")
        self.assertEqual(roles[W2][0]["arm"], "main")
        self.assertEqual(roles[W1][0]["start"], fz["C1S"]["start"])


# ---- the paper engine --------------------------------------------------------------------------------------------------------

class Prices:
    def __init__(self, clock, px=1.0, liq=100000.0, native=0.01):
        self.clock, self.px, self.liq, self.native, self.calls = clock, px, liq, native, []

    def __call__(self, mints):
        self.calls.append(self.clock())
        return {m: {"px": self.px, "liq": self.liq, "native": self.native, "pool": POOL, "symbol": "TST", "t": self.clock()}
                for m in mints}


def roles(arm="C1", tag="C1-01", start=T):
    return {W1: [{"arm": arm, "tag": tag, "cluster": "K03" if arm == "C1S" else None, "stats": {"runners": 6}, "start": start}]}


class Engine(TempHome):
    def make(self, clock, prices=None, check=lambda m: [], arm="C1", alerter=None):
        return F.Engine(self.dir, roles(arm), prices or Prices(clock), check, alerter=alerter, clock=clock)

    def test_fill_at_least_3s_after_detection(self):
        clk = Clock(T + 1000)
        e = self.make(clk)
        e.on_tx(tx("buy1", T + 998, W1), t_det=T + 1000)
        self.assertEqual(len(e.state["pending"]), 1)
        clk.t = T + 1001
        e.tick()                                     # not due: no sample taken for it
        self.assertEqual(len(e.fills.rows()), 0)
        clk.t = T + 1003.2
        e.tick()
        rows = e.fills.rows()
        self.assertEqual(len(rows), 1)
        self.assertGreaterEqual(rows[0]["t"] - rows[0]["t_det"], 3.0)
        self.assertAlmostEqual(rows[0]["lag"], 2.0)
        self.assertAlmostEqual(rows[0]["fill"], 1.0 * (1 + 0.0175 + 20 / 50000))

    def test_late_detection_is_missed_not_copied(self):
        clk = Clock(T + 1000)
        e = self.make(clk)
        e.on_tx(tx("old", T + 800, W1), t_det=T + 1000)
        self.assertEqual(e.state["pending"], [])
        self.assertTrue(e.events.rows()[0]["missed"])

    def test_swap_before_forward_start_is_ignored(self):
        clk = Clock(T + 10)
        e = self.make(clk)
        e.roles[W1][0]["start"] = T + 5
        e.on_tx(tx("pre", T + 4, W1), t_det=T + 10)
        self.assertEqual(e.events.rows(), [])

    def test_fraction_mirrored_sells_and_copy_gap(self):
        clk = Clock(T + 100)
        pr = Prices(clk)
        e = self.make(clk, pr)
        e.on_tx(tx("b", T + 99, W1, tok=(0, 1000), lam=(5e9, 4e9)), t_det=T + 100)     # it pays 1 SOL for 1000
        clk.t = T + 104
        e.tick()
        pos = next(iter(e.state["positions"].values()))
        full = pos["tokens"]
        clk.t = T + 200
        e.on_tx(tx("s1", T + 199, W1, tok=(1000, 500), lam=(4e9, 4.6e9)), t_det=T + 200)  # sells 50 % for 0.6 SOL
        clk.t = T + 204
        pr.px = 1.2
        e.tick()
        self.assertAlmostEqual(pos["left"], full / 2)
        clk.t = T + 300
        e.on_tx(tx("s2", T + 299, W1, tok=(500, 0), lam=(4.6e9, 5.3e9)), t_det=T + 300)   # sells the rest for 0.7 SOL
        clk.t = T + 304
        e.tick()
        self.assertTrue(pos["closed"])
        c = e.closes.rows()[0]
        f_buy = 1.0 * (1 + 0.0175 + 20 / 50000)
        proceeds = (full / 2) * 1.2 * (1 - 0.0175 - (full / 2 * 1.2) / 50000) + (full / 2) * 1.2 * (1 - 0.0175 - (full / 2 * 1.2) / 50000)
        self.assertAlmostEqual(c["net_pct"], round((proceeds / 20 - 1) * 100, 4), places=3)
        self.assertAlmostEqual(c["own_pct"], 30.0, places=3)                             # 1.3 SOL back on 1 SOL
        self.assertAlmostEqual(c["gap_pct"], round(c["own_pct"] - c["net_pct"], 4), places=3)
        self.assertAlmostEqual(full, 20 / f_buy)

    def test_copy_gap_math(self):
        self.assertEqual(F.copy_net(25.0), 25.0)
        self.assertEqual(F.copy_net(0.0), -100.0)
        own = {"paid": 2.0, "lot": 100, "left": 50, "received": 1.5}
        self.assertEqual(F.own_result(own, 0.02), 25.0)          # (1.5 + 50 x 0.02) / 2 - 1
        self.assertIsNone(F.own_result(own, None))
        self.assertEqual(C.mirror_fraction(50, 100), 0.5)
        self.assertEqual(C.mirror_fraction(150, 100), 1.0)
        self.assertEqual(C.mirror_fraction(5, 0), 1.0)

    def test_dead_pool_and_time_exit(self):
        clk = Clock(T + 100)
        pr = Prices(clk)
        e = self.make(clk, pr)
        e.on_tx(tx("b", T + 99, W1), t_det=T + 100)
        clk.t = T + 104
        e.tick()
        pr.liq = 500.0
        clk.t = T + 200
        e.tick()
        self.assertEqual(e.closes.rows()[0]["net_pct"], -100.0)
        self.assertEqual(e.closes.rows()[0]["why"], "dead_pool")
        e2 = F.Engine(self.dir / "b", roles(), Prices(clk), lambda m: [], clock=clk)
        e2.on_tx(tx("b2", clk.t - 1, W1, mint=MINT2), t_det=clk.t)
        clk.t += 4
        e2.tick()
        clk.t += 86400
        e2.tick()
        self.assertEqual(e2.closes.rows()[0]["why"], "time")

    def test_limits_one_per_coin_and_ten_open(self):
        clk = Clock(T + 100)
        e = self.make(clk)
        e.on_tx(tx("b1", T + 99, W1), t_det=T + 100)
        e.on_tx(tx("b2", T + 99, W1, slot=2), t_det=T + 100)
        self.assertEqual(e.skips.rows()[0]["why"], "one_per_coin")
        for i in range(12):
            e.on_tx(tx("m%d" % i, T + 99, W1, mint="Mint%039d" % i), t_det=T + 100)
        self.assertEqual(len(e.state["pending"]), 10)
        self.assertIn("max_open", [r["why"] for r in e.skips.rows()])

    def test_blocked_and_unknown_coins_are_skipped(self):
        clk = Clock(T + 100)
        e = self.make(clk, check=lambda m: ["freeze authority is set"])
        e.on_tx(tx("b", T + 99, W1), t_det=T + 100)
        self.assertEqual(e.state["pending"], [])
        s = e.skips.rows()[0]
        self.assertEqual((s["why"], s["reasons"]), ("cant_sell", ["freeze authority is set"]))
        e2 = F.Engine(self.dir / "u", roles(), Prices(clk), lambda m: None, clock=clk)
        e2.on_tx(tx("b", T + 99, W1), t_det=T + 100)
        self.assertEqual(e2.skips.rows()[0]["why"], "cant_sell_unknown")

    def test_mint_checks(self):
        frz = {"owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
               "data": {"parsed": {"info": {"freezeAuthority": "X", "extensions": []}}}}
        self.assertEqual(C.mint_blocks(frz), ["freeze authority is set"])
        t22 = {"owner": C.TOKEN_2022, "data": {"parsed": {"info": {"freezeAuthority": None, "extensions": [
            {"extension": "transferHook", "state": {}}, {"extension": "permanentDelegate", "state": {"delegate": "D"}},
            {"extension": "transferFeeConfig", "state": {"newerTransferFee": {"transferFeeBasisPoints": 1500}}}]}}}}
        self.assertEqual(len(C.mint_blocks(t22)), 3)
        self.assertIsNone(C.mint_blocks(None))
        self.assertEqual(C.rugcheck_blocks({"risks": [{"name": "Honeypot"}]}), ["honeypot (RugCheck)"])
        self.assertEqual(C.rugcheck_blocks({"risks": [{"name": "High sell tax", "description": "25%"}]})[0][:11], "tax over 10")
        self.assertEqual(C.rugcheck_blocks({"risks": []}), [])
        self.assertIsNone(C.rugcheck_blocks({}))

    def test_restart_resumes_without_duplicates(self):
        clk = Clock(T + 100)
        e = self.make(clk)
        e.on_tx(tx("b", T + 99, W1), t_det=T + 100)
        e.save()
        clk.t = T + 104
        e.tick()                                     # fill logged ...
        # ... crash before state.json was saved: the new process sees the pending leg and the swap again
        e2 = self.make(clk)
        e2.on_tx(tx("b", T + 99, W1), t_det=T + 100.5)
        e2.tick()
        e2.save()
        e3 = self.make(clk)
        e3.on_tx(tx("b", T + 99, W1), t_det=T + 101)
        e3.tick()
        for name in ("events.jsonl", "fills.jsonl"):
            ids = [json.loads(l)["id"] for l in (self.dir / name).read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(ids), len(set(ids)), name)
            self.assertEqual(len(ids), 1, name)
        self.assertEqual(len(e3.state["positions"]), 1)
        self.assertEqual(e3.state["pending"], [])
        p = next(iter(e3.state["positions"].values()))
        self.assertAlmostEqual(p["fill"], e.fills.rows()[0]["fill"])     # replayed from the log, not re-priced

    def test_torn_last_line_is_dropped(self):
        p = self.dir / "x.jsonl"
        p.write_text('{"id":"a"}\n{"id":"b"', encoding="utf-8")
        lg = C.Log(p)
        self.assertEqual(lg.ids, {"a"})
        lg.add({"id": "b"})
        self.assertEqual(p.read_text(encoding="utf-8"), '{"id":"a"}\n{"id":"b"}\n')


# ---- secrets ---------------------------------------------------------------------------------------------------------------

class Secrets(TempHome):
    def test_key_never_in_logs_or_errors(self):
        kf = self.dir / "helius.key"
        kf.write_text(KEY + "\n", encoding="utf-8")
        self.assertEqual(C.load_key(kf), KEY)

        def boom(url, body):
            raise RuntimeError(C.mask("HTTP 500 from %s" % url))
        h = C.Helius(KEY, post=boom)
        with self.assertRaises(RuntimeError) as cm:
            h.rpc("getTransaction", ["x"], tries=1)
        self.assertNotIn(KEY, str(cm.exception))
        self.assertIn("api-key=***", C.mask(h.url))
        self.assertNotIn(KEY, C.mask("wss://x/?api-key=%s and %s" % (KEY, KEY)))
        lg = C.Log(self.dir / "l.jsonl")
        lg.add({"id": "1", "msg": "url %s" % h.url})
        self.assertNotIn(KEY, (self.dir / "l.jsonl").read_text(encoding="utf-8"))

    def test_engine_logs_never_hold_the_key(self):
        C.load_key(self.write_key())
        clk = Clock(T + 100)
        e = F.Engine(self.dir, roles(), Prices(clk), lambda m: None, clock=clk)
        e.state["errors"].append("boom https://mainnet.helius-rpc.com/?api-key=" + KEY)
        e.on_tx(tx("b", T + 99, W1), t_det=T + 100)
        e.save()
        for p in self.dir.iterdir():
            if p.is_file() and p.suffix in (".jsonl", ".json"):
                txt = p.read_text(encoding="utf-8")
                if p.name != "state.json":
                    self.assertNotIn(KEY, txt, p.name)

    def write_key(self):
        kf = self.dir / "k.key"
        kf.write_text(KEY, encoding="utf-8")
        return kf

    def test_secrets_are_not_tracked(self):
        files = subprocess.run(["git", "-C", str(C.REPO), "ls-files"], capture_output=True, text=True, check=True).stdout
        self.assertNotIn("kol-map.json", files)
        self.assertNotIn("helius.key", files)
        gi = (C.REPO / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("kol-map.json", gi)
        self.assertIn("*.key", gi)


# ---- C1-S --------------------------------------------------------------------------------------------------------------------

M = "Ma1n111111111111111111111111111111111111111"
M2 = "Ma1n222222222222222222222222222222222222222"
I = "Int3rmed1ary1111111111111111111111111111111"
FUNDER = "Fund3r11111111111111111111111111111111111111"


class Linking(unittest.TestCase):
    def test_rule_a_funded(self):
        direct = C.parse_transfers(sol_transfer("f1", T - 5 * 86400, M, W1))[0]
        self.assertTrue(D.rule_funded(direct, T - 4 * 86400, M, set()))
        self.assertFalse(D.rule_funded(direct, T - 4 * 86400 + 7 * 86400, M, set()))     # first swap > 7 days later
        via = C.parse_transfers(sol_transfer("f2", T - 5 * 86400, I, W1))[0]
        main_out = C.parse_transfers(sol_transfer("f0", T - 6 * 86400, M, I))
        fresh = D.fresh_intermediaries(M, main_out, {I: T - 6.5 * 86400})
        self.assertEqual(fresh, {I})
        self.assertTrue(D.rule_funded(via, T - 5 * 86400 + 60, M, fresh))
        stale = D.fresh_intermediaries(M, main_out, {I: T - 30 * 86400})
        self.assertFalse(D.rule_funded(via, T - 5 * 86400 + 60, M, stale))
        other = C.parse_transfers(sol_transfer("f3", T - 5 * 86400, M2, W1))[0]
        self.assertFalse(D.rule_funded(other, T - 4 * 86400, M, fresh))
        self.assertEqual(D.first_sol_in(C.parse_transfers(sol_transfer("x", 5, M2, W1)) + [direct], W1)["src"], M2)

    def test_rule_b_lead(self):
        main_first = {"A": 1000, "B": 2000, "C": 3000, "D": 4000}
        buys = [{"mint": "A", "ts": 1000 - 60}, {"mint": "B", "ts": 2000 - 1799}, {"mint": "C", "ts": 3000 - 11}]
        n, leads = D.rule_lead(buys, main_first)
        self.assertEqual(n, 3)
        self.assertEqual(leads, [11, 60, 1799])
        n2, _ = D.rule_lead(buys[:2] + [{"mint": "C", "ts": 3000 - 5}, {"mint": "D", "ts": 4000 - 1900}], main_first)
        self.assertEqual(n2, 2)                         # 5 s and 31.7 min are outside 10 s to 30 min

    def test_rule_c_sweep(self):
        to_main = C.parse_transfers(sol_transfer("s1", T - 100, W1, M))
        to_funder = C.parse_transfers(sol_transfer("s2", T - 100, W1, FUNDER))
        self.assertTrue(D.rule_sweep(to_main, W1, M, FUNDER, False))
        self.assertTrue(D.rule_sweep(to_funder, W1, M, FUNDER, False))
        self.assertFalse(D.rule_sweep(to_funder, W1, M, FUNDER, True))          # the funder is an exchange
        self.assertFalse(D.rule_sweep(C.parse_transfers(sol_transfer("s3", 1, M, W1)), W1, M, FUNDER, False))
        tok = tx("t1", T - 50, W1, tok=(100, 0), lam=(5e9, 5e9))
        tok["meta"]["postTokenBalances"].append({"accountIndex": 2, "mint": MINT, "owner": M,
                                                 "uiTokenAmount": {"amount": str(int(100e6)), "decimals": 6}})
        self.assertTrue(D.rule_sweep(C.parse_transfers(tok), W1, M, None, False))   # tokens back to the main wallet

    def test_two_clusters_excluded(self):
        kept, multi = D.link_all({W1: {"K01": ["a"], "K02": ["b"]}, W2: {"K03": ["b", "c"]}})
        self.assertEqual(multi, [W1])
        self.assertEqual(kept, {W2: ("K03", ["b", "c"])})


class Names(TempHome):
    NAMES = ("SecretCallerAlpha", "SecretCallerBeta")

    def test_names_never_leave_the_map(self):
        mp = self.dir / "kol-map.json"
        mp.write_text(json.dumps({"K01": {"name": self.NAMES[0], "wallet": M, "source_url": "https://kolscan.io/x",
                                          "read_at": "2026-11-24T10:00:00Z"},
                                  "K02": {"name": self.NAMES[1], "wallet": M2, "source_url": "https://kolscan.io/y",
                                          "read_at": "2026-11-24T10:00:00Z"}}), encoding="utf-8")
        kol = D.load_kol_map(mp)
        self.assertNotIn("SecretCaller", json.dumps(kol))
        self.assertEqual(set(kol), {"K01", "K02"})
        doc, _ = D.run_kol(FakeFetch(), T - 3 * 86400, kol, out_dir=self.dir, log=lambda *_: None)
        out = (self.dir / "WALLETS-C1S.json").read_text(encoding="utf-8") + (self.dir / "DISCOVERY-C1S.md").read_text(encoding="utf-8")
        for n in self.NAMES:
            self.assertNotIn(n, out)
        self.assertEqual([s["tag"] for s in doc["sides"]], ["K01-side-1"])
        self.assertEqual(doc["sides"][0]["rules"], ["a"])
        sent = []
        clk = Clock(T + 100)
        e = F.Engine(self.dir / "home", {W1: [{"arm": "C1S", "tag": "K01-side-1", "cluster": "K01", "stats": {}, "start": T}]},
                     Prices(clk), lambda m: [], clock=clk,
                     alerter=lambda st, lg: F.Alerter(st, lg, settings=lambda: ("https://ntfy.test", "topic"),
                                                      post=lambda s, b: sent.append(b), clock=clk))
        e.on_tx(tx("b", T + 99, W1), t_det=T + 100)
        self.assertEqual(sent[0]["title"], "[PAPER COPY] K01-side-1 BUY TST")
        self.assertIn(C.ALERT_DISCLAIMER, sent[0]["message"])
        for p in (self.dir / "home").iterdir():
            for n in self.NAMES:
                self.assertNotIn(n, p.read_text(encoding="utf-8"))
        tracked = subprocess.run(["git", "-C", str(C.REPO), "grep", "-l", "SecretCallerAlpha", "--", ":!tests/test_copy_c1.py"],
                                 capture_output=True, text=True).stdout
        self.assertEqual(tracked, "")

    def test_alert_cap_20_an_hour(self):
        clk = Clock(T)
        st, sent = {}, []
        a = F.Alerter(st, C.Log(self.dir / "a.jsonl"), settings=lambda: ("s", "t"), post=lambda s, b: sent.append(b), clock=clk)
        for i in range(25):
            a.send("id%d" % i, "t", "m")
        self.assertEqual(len(sent), 20)
        clk.t = T + 3601
        a.send("late", "t", "m")
        self.assertEqual(len(sent), 21)
        self.assertEqual(a.send("late", "t", "m"), "dup")


class FakeFetch:
    """A tiny chain: M funds W1 directly; W2 trades but has no link; M2 has no candidates."""

    class H:
        credits = 0

    h = H()
    t19 = T

    def sig_count(self, a, gte, stop=D.EXCHANGE_SIGS):
        return 10

    def txs(self, a, gte=None, lt=None, cap=D.HIST_CAP, asc=True):
        if a == M:
            return [sol_transfer("f", T - 2 * 86400, M, W1)], False
        if a == W1:
            return [tx("w1b", T - 86400, W1)], False
        return [], False

    def first_txs(self, a, n=20):
        if a == W1:
            return [sol_transfer("f", T - 2 * 86400, M, W1), tx("w1b", T - 86400, W1)]
        return []


class WebSocketFrames(unittest.TestCase):
    def test_ping_fragment_text_and_masked_send(self):
        import socket
        import struct
        a, b = socket.socketpair()
        ws = C.WebSocket.__new__(C.WebSocket)
        ws.sock, ws.lock = a, __import__("threading").Lock()
        big = ("x" * 300).encode()
        b.sendall(bytes([0x89, 0]) + bytes([0x01, 3]) + b'{"a' + bytes([0x80, 126]) + struct.pack(">H", len(big) + 2) + b'":' + big)
        self.assertEqual(ws.recv(), '{"a":' + "x" * 300)
        pong = b.recv(2)
        self.assertEqual(pong[0], 0x8A)                  # answered the ping
        b.recv(4)                                        # its mask key (empty payload)
        ws.send("hi")
        h = b.recv(2)
        self.assertEqual((h[0], h[1]), (0x81, 0x80 | 2))
        mk = b.recv(4)
        self.assertEqual(bytes(x ^ mk[i % 4] for i, x in enumerate(b.recv(2))), b"hi")
        b.sendall(bytes([0x88, 0]))
        with self.assertRaises(ConnectionError):
            ws.recv()
        a.close()
        b.close()


# ---- readout ------------------------------------------------------------------------------------------------------------------

class Readout(unittest.TestCase):
    def rows(self, arm, nets, wallets=None, start=0):
        return [{"arm": arm, "mint": "m%d" % (i % 60), "net_pct": n, "entry_t": start + i, "wallet": (wallets or ["w%d" % (i % 8)])[0]
                 if wallets else "w%d" % (i % 8), "gap_pct": 1.0, "lag": 2.0} for i, n in enumerate(nets)]

    def test_pass_bar_and_controls(self):
        arm = self.rows("C1", [5.0 + (i % 3) for i in range(120)])
        ctl = self.rows("random", [-3.0 for _ in range(120)])
        res = R.evaluate(arm, {"random": ctl}, boot=500)
        self.assertTrue(res["pass"], res["checks"])
        one = self.rows("C1", [5.0 + (i % 3) for i in range(120)], wallets=["w0"])
        self.assertFalse(R.evaluate(one, {"random": ctl}, boot=200)["checks"]["wallet_share"])
        self.assertFalse(R.evaluate(arm[:80], {"random": ctl}, boot=200)["checks"]["size"])

    def test_side_measures(self):
        ev = [{"roles": ["main"], "clusters": ["K01"], "mint": "A", "side": "buy", "ts": 1000, "tokens": 10},
              {"roles": ["C1S"], "clusters": ["K01"], "mint": "A", "side": "buy", "ts": 900, "tokens": 5},
              {"roles": ["C1S"], "clusters": ["K01"], "mint": "A", "side": "sell", "ts": 1500, "tokens": 5},
              {"roles": ["C1S"], "clusters": ["K01"], "mint": "B", "side": "sell", "ts": 1500, "tokens": 5}]
        m = R.side_measures(ev)
        self.assertEqual(m["lead_median_s"], 100)
        self.assertEqual((m["side_sells"], m["dumps"]), (2, 1))


if __name__ == "__main__":
    unittest.main()
