"""Section 19.3: real version-1 transactions, recorded from Helius getTransactionsForAddress (tests/fixtures/helius_v1),
read by the C1 and C2 parsers: the signer, the mint, the token and SOL amounts, the block time and the slot match Helius's
own enhanced parse of the same signature. Venues: a PumpSwap buy, a pump.fun bonding-curve buy, a Raydium LaunchLab buy.
No network."""
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from research.copy import common as C
from research.copy import discover as D
from research.copy2 import scan as SC

FIX = json.loads((Path(__file__).parent / "fixtures" / "helius_v1" / "swaps.json").read_text(encoding="utf-8"))["txs"]
VENUES = ("pumpswap", "pumpfun_curve", "raydium_launchlab")


def cases():
    for v in VENUES:
        yield v, FIX[v]["tx"], FIX[v]["expected"], FIX[v]["program"]


class Recorded(unittest.TestCase):
    def test_fixtures_are_real_version_1_direct_buys(self):
        self.assertEqual(set(FIX), set(VENUES))
        for v, tx, e, prog in cases():
            with self.subTest(v):
                self.assertEqual(tx["version"], 1)
                self.assertIsNone(tx["meta"]["err"])
                self.assertEqual(tx["transaction"]["signatures"][0], e["signature"])
                progs = {i.get("programId") for i in C._instructions(tx)}
                self.assertIn(prog, progs)                                   # the venue's program ran
                self.assertNotIn("JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4", progs)   # direct, not routed
                if v == "pumpfun_curve":
                    self.assertNotIn(FIX["pumpswap"]["program"], progs)


class C1Parser(unittest.TestCase):
    def test_parse_swaps_reads_each_venue(self):
        for v, tx, e, _ in cases():
            with self.subTest(v):
                sw = C.parse_swaps(tx)
                self.assertEqual(len(sw), 1)
                s = sw[0]
                self.assertEqual(s["side"], "buy")
                self.assertEqual(s["wallet"], e["signer"])
                self.assertEqual(s["mint"], e["mint"])
                self.assertAlmostEqual(s["tokens"], e["token_raw"] / 10 ** e["decimals"], places=6)
                self.assertAlmostEqual(s["sol"], e["lamports"] / 1e9, places=9)
                self.assertEqual(s["ts"], e["block_time"])
                self.assertEqual(s["slot"], e["slot"])
                self.assertEqual(s["sig"], e["signature"])

    def test_keys_line_up_with_balances(self):
        for v, tx, e, _ in cases():
            with self.subTest(v):
                keys, signers = C._keys(tx)
                self.assertEqual(len(keys), len(tx["meta"]["preBalances"]))
                self.assertEqual([k for k, s in zip(keys, signers) if s], [e["signer"]])
                self.assertEqual(C.fee_payer(tx), e["signer"])               # C1 dev exclusion, C2 payer

    def test_follower_filter_by_wallet(self):
        for v, tx, e, _ in cases():
            with self.subTest(v):
                self.assertEqual([s["wallet"] for s in C.parse_swaps(tx, {e["signer"]})], [e["signer"]])
                self.assertEqual(C.parse_swaps(tx, {"SomeoneElse1111111111111111111111111111111"}), [])
                self.assertEqual(C.parse_transfers(tx), [])                  # a swap is never read as a transfer

    def test_discovery_fetch_keeps_them(self):
        """Through discover.Fetch.txs (the getTransactionsForAddress path the early-buyer loop reads)."""
        d = Path(tempfile.mkdtemp(prefix="copy-v1-"))
        self.addCleanup(shutil.rmtree, d, True)
        for v, tx, e, _ in cases():
            with self.subTest(v):
                class H:
                    credits = 0

                    def rpc(self, m, params):
                        assert params[1]["maxSupportedTransactionVersion"] == C.TX_VERSION
                        return {"data": [copy.deepcopy(tx)], "paginationToken": None}
                fx = D.Fetch(H(), d / v, t19=e["block_time"] + 60)
                got, trunc = fx.txs(e["mint"], e["block_time"] - 60, None)
                self.assertFalse(trunc)
                sw = [s for t in got for s in C.parse_swaps(t)]
                self.assertEqual([(s["wallet"], s["mint"], s["side"], s["ts"]) for s in sw],
                                 [(e["signer"], e["mint"], "buy", e["block_time"])])


class C2Parser(unittest.TestCase):
    def test_fetch_buys_records_each_venue(self):
        d = Path(tempfile.mkdtemp(prefix="copy2-v1-"))
        self.addCleanup(shutil.rmtree, d, True)
        db = SC.DB(d / "scan.db")
        for v, tx, e, _ in cases():
            db.c.execute("insert into coins(mint, rank, kind, t0, status) values(?,?,?,?,?)",
                         (e["mint"], 0, "top", e["block_time"] - 60, "ok"))
        db.c.commit()

        class H:
            def rpc(self, m, params):
                assert params[1]["maxSupportedTransactionVersion"] == C.TX_VERSION
                return {"data": [copy.deepcopy(x["tx"]) for x in FIX.values() if x["expected"]["mint"] == params[0]]}
        t20 = SC.T20
        try:
            SC.T20 = max(x["expected"]["block_time"] for x in FIX.values()) + 3600
            SC.fetch_buys(db, SC.Meter(H(), db, budget=10 ** 6), log=lambda *_: None)
        finally:
            SC.T20 = t20
        rows = {r[0]: r for r in db.c.execute("select mint, wallet, ts, slot, sol, tokens, sig from buys")}
        self.assertEqual(len(rows), 3)
        for v, tx, e, _ in cases():
            with self.subTest(v):
                mint, wallet, ts, slot, sol, tokens, sig = rows[e["mint"]]
                self.assertEqual((wallet, ts, slot, sig), (e["signer"], e["block_time"], e["slot"], e["signature"]))
                self.assertAlmostEqual(sol, e["lamports"] / 1e9, places=9)
                self.assertAlmostEqual(tokens, e["token_raw"] / 10 ** e["decimals"], places=6)
                self.assertEqual(db.c.execute("select first_slot from coins where mint=?", (mint,)).fetchone()[0], e["slot"])


if __name__ == "__main__":
    unittest.main()
