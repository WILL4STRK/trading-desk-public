"""The Terminal tab (desk/memeterminal.py, desk/static/terminal.js): only coins that did not fully pass, the can't-sell
block, escaping of attacker-chosen symbols, mint validation, the append-only atomic manual log, the CSP, and the
D1 check that the display-only last_strong_ts leaves every saved account and shadow byte-for-byte the same."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from desk import meme
from desk import memeterminal as MT
from desk import settings as S
from desk.paths import STATIC
from desk.server import CSP, serve
from desk.skills import write_json

from .base import DeskCase
from .test_desk import DemoCase
from .test_meme import SOL, T0, FakeAPI

EVIL = "<img src=x onerror=alert(1)>"
GOOD = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"      # 44 base58 characters
POOL = "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2"
TX = "5" * 87


def coin(addr, **kw):
    t = {"chain": "solana", "addr": addr, "pool": POOL, "symbol": "C", "name": "Coin", "first_seen": T0, "seen": T0,
         "px": 0.01, "liq": 50000.0, "dex": "raydium", "created": (T0 - 7200) * 1000, "sources": [],
         "f": {"r1h": 25.0, "r6h": 1.0, "r24h": 2.0, "v1h": 9000.0, "buy_ratio_m5": 0.6}}
    t.update(kw)
    return t


def scr(verdict="fail", strict="fail", why=("RugCheck risk 80/100",), strict_why=("top 10 holders 50%",), risks=()):
    return {"ts": T0, "verdict": verdict, "why": list(why), "strict": strict, "strict_why": list(strict_why),
            "report": {"score_normalised": 80, "risks": [{"name": n, "level": "danger"} for n in risks]}}


def mint(i):
    return (GOOD[:-2] + "%02d" % i).replace("0", "a")


class Lists(DeskCase):
    def save(self, tracked, screens):
        write_json(meme.folder() / "model.json", {"tracked": tracked})
        write_json(meme.folder() / "screens.json", screens)

    def test_lists_only_solana_coins_that_did_not_fully_pass(self):
        keys = {n: "solana:" + mint(i) for i, n in enumerate(("pass", "fail", "strict", "unknown", "never"))}
        tracked = {k: coin(k.split(":")[1], symbol=n) for n, k in keys.items()}
        tracked["base:0xabc"] = dict(coin("0xabc"), chain="base")
        self.save(tracked, {keys["pass"]: scr("pass", "pass", [], []), keys["fail"]: scr("fail", "pass"),
                            keys["strict"]: scr("pass", "fail"), keys["unknown"]: scr("unknown", "unknown", ["no safety report"]),
                            "base:0xabc": scr()})
        v = MT.view(now=T0)
        self.assertEqual({c["symbol"] for c in v["coins"]}, {"fail", "strict", "unknown", "never"})
        by = {c["symbol"]: c for c in v["coins"]}
        self.assertEqual([f["reason"] for f in by["fail"]["failed"]], ["RugCheck risk 80/100"])
        self.assertEqual([f["check"] for f in by["strict"]["failed"]], ["strict"])
        self.assertEqual(by["never"]["failed"][0]["reason"], "not screened yet")
        self.assertEqual(by["fail"]["links"]["solscan"], "https://solscan.io/token/" + keys["fail"].split(":")[1])
        self.assertEqual(len(v["ticker"]), 5)                                # the ticker shows every watched Solana coin

    def test_the_bots_record_on_the_coin(self):
        k = "solana:" + GOOD
        write_json(meme.folder() / "model.json", {"tracked": {k: coin(GOOD)},
                                                  "account": {"closed": [{"key": k, "ret_pct": -10.0}]},
                                                  "books": {"quick": {"closed": [{"key": k, "ret_pct": 20.0}]}}})
        for n in (-50.0, 10.0):
            meme._append("shadows.jsonl", {"key": k, "net_pct": n})
        rec = MT.view(now=T0)["coins"][0]["record"]
        self.assertEqual(rec, {"shadows": 2, "shadows_net_pct": -20.0, "practice": 2, "practice_net_pct": 5.0})


class CantSell(DeskCase):
    def test_each_of_the_three_reasons_blocks_the_swap(self):
        cases = {"freeze": scr(risks=["Freeze Authority still enabled"]), "honeypot": scr(why=["honeypot"]),
                 "tax": scr(risks=["High transfer fee 25%"])}
        for name, sc in cases.items():
            with self.subTest(name):
                why = MT.cant_sell(sc)
                self.assertEqual(len(why), 1, why)
                self.assertIn({"freeze": "freeze authority", "honeypot": "honeypot", "tax": "sell tax 25%"}[name], why[0])
        self.assertIn("honeypot", MT.cant_sell({"verdict": "fail", "report": {"is_honeypot": "1"}})[0])
        self.assertIn("sell tax 15%", MT.cant_sell({"verdict": "fail", "report": {"sell_tax": "0.15"}})[0])

    def test_permanent_delegate_blocks(self):
        self.assertEqual(MT.cant_sell(scr(risks=["Permanent Delegate"])),
                         ["permanent delegate: another account can move or burn your tokens at any time"])

    def test_transfer_hook_blocks(self):
        self.assertEqual(MT.cant_sell(scr(why=["danger: Transfer Hook enabled"])),
                         ["transfer hook: the token's own program runs on every transfer and can refuse a sale"])

    def test_non_transferable_blocks(self):
        self.assertEqual(MT.cant_sell(scr(strict_why=["Non-Transferable token"])),
                         ["non-transferable: the token cannot be sent, so it cannot be sold"])

    def test_a_sell_tax_of_10_pct_or_less_and_ordinary_risks_do_not_block(self):
        self.assertEqual(MT.cant_sell(scr(risks=["Transfer fee 10%", "Low Liquidity", "Mint Authority still enabled"])), [])
        self.assertEqual(MT.cant_sell(None), [])

    def test_a_tax_of_unknown_size_blocks(self):
        self.assertEqual(MT.cant_sell(scr(risks=["Transfer fee"])), ["sell tax of unknown size: above 10%"])

    def test_the_view_blocks_by_default_and_the_setting_opens_it(self):
        k = "solana:" + GOOD
        write_json(meme.folder() / "model.json", {"tracked": {k: coin(GOOD)}})
        write_json(meme.folder() / "screens.json", {k: scr(risks=["Freeze Authority still enabled"])})
        self.assertFalse(S.load()["meme_terminal_allow_unsellable"])
        c = MT.view(now=T0)["coins"][0]
        self.assertEqual(c["swap"], "blocked")
        S.save({"meme_terminal_allow_unsellable": True})
        self.assertEqual(MT.view(now=T0)["coins"][0]["swap"], "confirm")
        with self.assertRaises(S.SettingsError):
            S.save({"meme_terminal_allow_unsellable": "yes"})

    def test_an_invalid_mint_never_gets_a_swap_or_a_link(self):
        k = "solana:" + EVIL
        write_json(meme.folder() / "model.json", {"tracked": {k: coin(EVIL)}})
        c = MT.view(now=T0)["coins"][0]
        self.assertEqual((c["swap"], c["mint"], c["links"]["solscan"], c["links"]["rugcheck"]), ("invalid_mint", None, None, None))


class Mints(unittest.TestCase):
    CASES = {GOOD: True, "1" * 32: True, "1" * 44: True, "1" * 31: False, "1" * 45: False,
             GOOD[:-1] + "0": False, GOOD[:-1] + "O": False, GOOD[:-1] + "I": False, GOOD[:-1] + "l": False,
             GOOD + "\n": False, " " + GOOD[1:]: False, EVIL: False, "": False}

    def test_python(self):
        for s, ok in self.CASES.items():
            self.assertIs(MT.valid_mint(s), ok, repr(s))
        for bad in (None, 123, [GOOD]):
            self.assertFalse(MT.valid_mint(bad))
        self.assertEqual(MT.links(EVIL, EVIL), {"dexscreener": None, "solscan": None, "rugcheck": None})

    @unittest.skipUnless(shutil.which("node"), "Node is not installed")
    def test_javascript(self):
        out = node("process.stdout.write(JSON.stringify(%s.map(T.validMint)))" % json.dumps(list(self.CASES)))
        self.assertEqual(json.loads(out), list(self.CASES.values()))


def node(code):
    js = "const T = require(%s);\n%s" % (json.dumps(str(STATIC / "terminal.js")), code)
    return subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=30, check=True).stdout


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class Escaping(unittest.TestCase):
    def test_a_malicious_symbol_and_name_are_shown_as_text(self):
        c = {"key": "solana:" + EVIL, "symbol": EVIL, "name": EVIL + '"\'`', "dex": EVIL, "mint": GOOD, "swap": "confirm",
             "failed": [{"check": "safety", "reason": EVIL}], "cant_sell": [EVIL], "record": {}, "links": {}}
        out = json.loads(node("""const c = %s;
            process.stdout.write(JSON.stringify([T.rowHtml(c, "wl-1"), T.gate(c, false), T.gate(Object.assign({}, c, {swap: "blocked"}), false),
              T.tokenPanel(c), T.botRow({time: 1, side: c.symbol, policy: c.symbol, why: c.symbol}),
              T.manualRow({symbol: c.symbol, side: c.symbol, txid: c.symbol, at: c.symbol, in_amount: c.symbol, out_amount: 1})]))""" % json.dumps(c)))
        for html in out:
            self.assertNotIn("<img", html)
            self.assertNotIn("onerror=alert", html.replace("&lt;img src=x onerror=alert(1)&gt;", ""))
            self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)
        self.assertNotIn('data-tkey="solana:<', out[0])

    def test_the_success_callback_needs_a_real_txid_and_the_coin_on_one_side(self):
        sol = MT.SOL_MINT
        out = json.loads(node("""const sol = %s, m = %s, tx = %s;
            process.stdout.write(JSON.stringify([
              T.tradeFrom({txid: tx, swapResult: {inputAddress: sol, outputAddress: m, inputAmount: 5, outputAmount: 7}}, m, sol, 1000),
              T.tradeFrom({txid: tx, swapResult: {inputAddress: m, outputAddress: sol, inputAmount: 7, outputAmount: 5}}, m, sol, 1000),
              T.tradeFrom({txid: "<x>", swapResult: {inputAddress: sol, outputAddress: m}}, m, sol, 1000),
              T.tradeFrom({txid: tx, swapResult: {inputAddress: sol, outputAddress: sol}}, m, sol, 1000)]))""" % (
            json.dumps(sol), json.dumps(GOOD), json.dumps(TX))))
        self.assertEqual(out[0], {"txid": TX, "mint": GOOD, "side": "buy", "in_mint": sol, "out_mint": GOOD,
                                  "in_amount": 5, "out_amount": 7, "time": 1})
        self.assertEqual(out[1]["side"], "sell")
        self.assertEqual(out[2:], [None, None])


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class Watchlist(unittest.TestCase):
    """The watchlist's pure parts: sort, search, which coins are new or gone, and the sparkline."""
    COINS = [{"key": "a", "symbol": "AAA", "mint": GOOD, "age_h": 5, "r1h": 3, "v1h": 10, "liq": 900, "last_strong_ts": None, "px": 2.0, "r6h": 0, "r24h": 100},
             {"key": "b", "symbol": "bbb", "mint": "1" * 40, "age_h": 1, "r1h": -9, "v1h": 30, "liq": 100, "last_strong_ts": 50, "px": 1.0},
             {"key": "c", "symbol": "CCC", "mint": "2" * 40, "age_h": None, "r1h": 40, "v1h": None, "liq": 500, "last_strong_ts": 90, "px": None}]

    def run_js(self, code):
        return json.loads(node("const C = %s; process.stdout.write(JSON.stringify(%s))" % (json.dumps(self.COINS), code)))

    def test_sorts(self):
        got = self.run_js("['newest','r1h','volume','liquidity','strong','nonsense'].map(b => T.sortCoins(C, b).map(c => c.key))")
        self.assertEqual(got, [["b", "a", "c"], ["c", "a", "b"], ["b", "a", "c"], ["a", "c", "b"], ["c", "b", "a"], ["b", "a", "c"]])

    def test_search_by_symbol_or_mint(self):
        got = self.run_js("['bb', 'BB', '1111', GOOD_PREFIX, '', 'zzz'].map(q => C.filter(c => T.matches(c, q)).map(c => c.key))"
                          .replace("GOOD_PREFIX", json.dumps(GOOD[:6].lower())))
        self.assertEqual(got, [["b"], ["b"], ["b"], ["a"], ["a", "b", "c"], []])

    def test_new_and_gone(self):
        got = self.run_js("T.diff(['a', 'x'], C, 'newest')")
        self.assertEqual(([c["key"] for c in got["added"]], got["gone"]), (["b", "c"], ["x"]))
        self.assertEqual(self.run_js("T.diff(['a', 'b', 'c'], C, 'r1h')"), {"added": [], "gone": []})

    def test_sparkline_from_the_coins_own_changes(self):
        pts = self.run_js("T.sparkPoints(C[0])")                      # 24 h ago, 6 h ago, 1 h ago, now
        self.assertEqual(len(pts), 4)
        for got, want in zip(pts, [1.0, 2.0, 2.0 / 1.03, 2.0]):
            self.assertAlmostEqual(got, want)
        self.assertEqual(self.run_js("[T.sparkPath(C[2]), T.sparkPoints({px: 1, r1h: -100})]"), ["", [1]])

    def test_prices_never_use_e_notation(self):
        self.assertEqual(self.run_js("[1.148e-8, 0.000009219, 0.0011, 42, 0, null].map(T.price)"),
                         ["0.00000001148", "0.000009219", "0.001100", "42.0000", "0", "\u2013"])

    def test_price_precision_for_tiny_prices(self):
        self.assertEqual(self.run_js("[T.precision(0.000002111), T.precision(0.5), T.precision(42), T.precision(null)]"), [9, 4, 2, 4])


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class LivePrices(unittest.TestCase):
    """Live 5s/15s candles from DexScreener's pair endpoint: price only, every response checked."""
    MINT, POOL = GOOD, POOL

    def js(self, code):
        return json.loads(node("const M = %s, P = %s; process.stdout.write(JSON.stringify(%s))" % (json.dumps(self.MINT), json.dumps(self.POOL), code)))

    def test_candles_from_polled_prices_at_5s_and_15s_boundaries(self):
        samples = "[{t: 100, p: 1}, {t: 101.9, p: 3}, {t: 104.999, p: 0.5}, {t: 105, p: 2}, {t: 114.9, p: 4}, {t: 115, p: 1.5}, {t: NaN, p: 9}, {t: 116, p: Infinity}]"
        five, fifteen = self.js("[T.priceCandles(%s, 5), T.priceCandles(%s, 15)]" % (samples, samples))
        self.assertEqual(five, [{"time": 100, "open": 1, "high": 3, "low": 0.5, "close": 0.5},
                                {"time": 105, "open": 2, "high": 2, "low": 2, "close": 2},
                                {"time": 110, "open": 4, "high": 4, "low": 4, "close": 4},
                                {"time": 115, "open": 1.5, "high": 1.5, "low": 1.5, "close": 1.5}])
        self.assertEqual(fifteen, [{"time": 90, "open": 1, "high": 3, "low": 0.5, "close": 0.5},      # 90..104.999
                                   {"time": 105, "open": 2, "high": 4, "low": 1.5, "close": 1.5}])   # 105..119.999
        self.assertTrue(all("volume" not in c for c in five + fifteen))          # price only

    def test_a_valid_response_gives_the_price(self):
        ok = "{pairs: [{chainId: 'solana', pairAddress: P, baseToken: {address: M}, priceUsd: '0.0000123'}]}"
        self.assertEqual(self.js("[T.livePrice(%s, M, P), T.livePrice({pair: {pairAddress: P, baseToken: {address: M}, priceUsd: 2}}, M, P)]" % ok), [0.0000123, 2])

    def test_malformed_or_foreign_responses_are_dropped(self):
        bad = ["null", "[]", "'x'", "{pairs: 'x'}", "{pairs: [null]}",
               "{pairs: [{pairAddress: P, baseToken: {address: 'Other111111111111111111111111111111'}, priceUsd: '1'}]}",
               "{pairs: [{pairAddress: 'OtherPool1111111111111111111111111', baseToken: {address: M}, priceUsd: '1'}]}",
               "{pairs: [{chainId: 'bsc', pairAddress: P, baseToken: {address: M}, priceUsd: '1'}]}",
               "{pairs: [{pairAddress: P, baseToken: {address: M}, priceUsd: 'NaN'}]}",
               "{pairs: [{pairAddress: P, baseToken: {address: M}, priceUsd: '-1'}]}",
               "{pairs: [{pairAddress: P, baseToken: {address: M}, priceUsd: '0'}]}",
               "{pairs: [{pairAddress: P, baseToken: {address: M}, priceUsd: 'Infinity'}]}",
               "{pairs: [{pairAddress: P, baseToken: {address: M}, priceUsd: {}}]}",
               "{pairs: [{pairAddress: P, baseToken: {address: M}}]}",
               "{pairs: [{pairAddress: P, baseToken: M, priceUsd: '1'}]}"]
        self.assertEqual(self.js("[%s].map(d => T.livePrice(d, M, P))" % ", ".join(bad)), [None] * len(bad))


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class FailedChecksLine(unittest.TestCase):
    """No checkbox: one compact red line above the swap, expandable; danger items bold; blocked and invalid unchanged."""
    C = {"key": "solana:" + GOOD, "symbol": "C", "mint": GOOD, "swap": "confirm", "cant_sell": [],
         "failed": [{"check": "safety", "reason": "RugCheck risk 54/100"}, {"check": "safety", "reason": "danger: Creator history of rugged tokens"},
                    {"check": "safety", "reason": "danger: Single holder ownership"}, {"check": "strict", "reason": "top 10 holders 50%"}]}

    def gate(self, **kw):
        return json.loads(node("process.stdout.write(JSON.stringify(T.gate(%s)))" % json.dumps(dict(self.C, **kw))))

    def test_a_warned_coin_gets_the_line_and_no_checkbox(self):
        html = self.gate()
        self.assertNotIn("checkbox", html)
        self.assertNotIn("I understand", html)
        self.assertIn("<summary", html)
        summary = html[html.index("<summary"):html.index("</summary>")]
        self.assertIn("4 failed checks", summary)
        for short in ("RugCheck 54/100", "creator rugged before", "single holder", "top 10 holders 50%"):
            self.assertIn(short, summary)
        self.assertIn('<b class="t-danger">creator rugged before</b>', summary)
        self.assertIn('<b class="t-danger">single holder</b>', summary)
        self.assertIn("<span>RugCheck 54/100</span>", summary)
        self.assertLess(summary.index("creator rugged before"), summary.index("RugCheck 54/100"))      # danger first

    def test_expanding_lists_every_failed_check_in_full(self):
        html = self.gate()
        full = html[html.index("<ul>"):]
        for line in ("Safety: RugCheck risk 54/100", "Safety: danger: Creator history of rugged tokens",
                     "Safety: danger: Single holder ownership", "Strict: top 10 holders 50%"):
            self.assertIn(line, full)
        self.assertEqual(full.count("<li>"), 4)

    def test_blocked_and_invalid_are_unchanged(self):
        blocked = self.gate(swap="blocked", cant_sell=["reported as a honeypot: buys work, sells may not"])
        self.assertIn("Blocked: you may not be able to sell this token", blocked)
        self.assertNotIn("<summary", blocked)
        self.assertIn("Swap off", self.gate(swap="invalid_mint", mint=None))


class ManualLog(DeskCase):
    def body(self, txid=TX, **kw):
        b = {"txid": txid, "mint": GOOD, "side": "buy", "in_amount": 1000000, "out_amount": 5, "time": T0}
        b.update(kw)
        return b

    def setUp(self):
        super().setUp()
        k = "solana:" + GOOD
        write_json(meme.folder() / "model.json", {"tracked": {k: coin(GOOD, px=0.02)}})
        write_json(meme.folder() / "screens.json", {k: scr()})
        self.path = meme.folder() / MT.MANUAL

    def test_a_row_carries_the_failed_checks_and_the_price_then(self):
        self.assertTrue(MT.record_manual(self.body(), now=T0 + 5))
        row = MT.manual_log()[0]
        self.assertEqual((row["px"], row["side"], row["in_mint"], row["out_mint"]), (0.02, "buy", MT.SOL_MINT, GOOD))
        self.assertEqual([f["reason"] for f in row["failed"]], ["RugCheck risk 80/100", "top 10 holders 50%"])

    def test_append_only_and_the_same_txid_once(self):
        MT.record_manual(self.body())
        first = self.path.read_bytes()
        self.assertFalse(MT.record_manual(self.body(side="sell", in_mint=GOOD, out_mint=MT.SOL_MINT)))
        self.assertEqual(self.path.read_bytes(), first)
        self.assertTrue(MT.record_manual(self.body("4" * 88, side="sell")))
        after = self.path.read_bytes()
        self.assertTrue(after.startswith(first))
        self.assertEqual(after.count(b"\n"), 2)
        self.assertEqual([r["side"] for r in MT.manual_log()], ["buy", "sell"])

    def test_a_partial_last_line_is_kept_and_the_new_row_starts_on_its_own_line(self):
        self.path.write_bytes(b'{"txid":"old"}\n{"txid":"half')
        MT.record_manual(self.body())
        data = self.path.read_bytes()
        self.assertTrue(data.startswith(b'{"txid":"old"}\n{"txid":"half\n'))
        self.assertEqual(MT.manual_log()[-1]["txid"], TX)

    def test_a_crash_before_the_swap_leaves_the_old_file_whole(self):
        MT.record_manual(self.body())
        first = self.path.read_bytes()
        with mock.patch.object(MT.os, "replace", side_effect=OSError("disk gone")):
            with self.assertRaises(OSError):
                MT.record_manual(self.body("4" * 88))
        self.assertEqual(self.path.read_bytes(), first)
        self.assertTrue(MT.record_manual(self.body("4" * 88)))      # and the next write works

    def test_concurrent_writes_lose_nothing(self):
        txids = [str(i + 1) * 64 if i < 9 else "A" * 64 + "BCD"[i - 9] for i in range(12)]
        ts = [threading.Thread(target=MT.record_manual, args=(self.body(t),)) for t in txids]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sorted(r["txid"] for r in MT.manual_log()), sorted(set(txids)))

    def test_bad_input_is_refused(self):
        bad = [self.body(txid="x"), self.body(txid=EVIL), self.body(mint=EVIL), self.body(mint=MT.SOL_MINT),
               self.body(side="short"), self.body(in_amount=-1), self.body(in_amount="5"), self.body(in_amount=True),
               self.body(in_mint=EVIL), self.body(out_mint=MT.SOL_MINT), self.body(time="soon")]
        for b in bad:
            with self.subTest(b), self.assertRaises(ValueError):
                MT.record_manual(b)
        self.assertFalse(self.path.exists())


class Route(DemoCase):
    def setUp(self):
        super().setUp()
        self.httpd = serve(self.desk(), None, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def call(self, path, body=None, headers=None):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     data=None if body is None else json.dumps(body).encode(), headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def test_the_policy_adds_only_api_dexscreener_com_to_connect_src(self):
        directives = dict((d.strip().split()[0], d.strip().split()[1:]) for d in CSP.split(";") if d.strip())
        self.assertEqual(directives["connect-src"], ["'self'", "https://*.jup.ag", "https://fonts.googleapis.com",
                                                     "https://api.mainnet-beta.solana.com", "https://api.dexscreener.com",
                                                     "wss://api.mainnet-beta.solana.com"])
        self.assertEqual(directives["script-src"], ["'self'", "https://plugin.jup.ag"])
        self.assertNotIn("geckoterminal", CSP)
        self.assertNotIn("pumpportal", CSP)

    def test_bound_to_this_computer_only(self):
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")

    def test_terminal_and_manual_routes(self):
        code, _, b = self.call("/api/meme/terminal")
        self.assertEqual((code, json.loads(b)["coins"]), (200, []))
        body = {"txid": TX, "mint": GOOD, "side": "buy", "in_amount": 1, "out_amount": 2, "time": T0}
        self.assertEqual(self.call("/api/meme/manual", body, {"Content-Type": "application/json"})[0], 403)
        h = {"Content-Type": "application/json", "X-Desk": "1"}
        self.assertEqual(self.call("/api/meme/manual", dict(body, mint=EVIL), h)[0], 400)
        code, _, b = self.call("/api/meme/manual", body, h)
        self.assertEqual((code, json.loads(b)["logged"]), (200, True))
        self.assertEqual(json.loads(self.call("/api/meme/terminal")[2])["manual"][0]["txid"], TX)

    def test_the_page_cannot_turn_on_the_unsellable_switch(self):
        h = {"Content-Type": "application/json", "X-Desk": "1"}
        for changes in ({"meme_terminal_allow_unsellable": True}, {"meme_paused": True, "meme_terminal_allow_unsellable": True}):
            code, _, b = self.call("/api/settings", {"changes": changes}, h)
            self.assertEqual(code, 400)
            self.assertIn("settings.json", json.loads(b)["error"])
            self.assertIs(S.load()["meme_terminal_allow_unsellable"], False)
            self.assertIs(S.load()["meme_paused"], False)                 # nothing in the refused request is saved
        self.assertEqual(self.call("/api/settings", {"changes": {"meme_paused": True}}, h)[0], 200)   # the rest still saves
        S.save({"meme_terminal_allow_unsellable": True})                        # editing the file is the only way
        code, _, b = self.call("/api/settings", {"changes": {"meme_terminal_allow_unsellable": False}}, h)
        self.assertEqual(code, 400)
        self.assertIs(S.load()["meme_terminal_allow_unsellable"], True)

    def test_csp_allows_scripts_only_from_the_desk_and_jupiter(self):
        code, headers, page = self.call("/")
        self.assertEqual(code, 200)
        csp = headers["Content-Security-Policy"]
        self.assertEqual(csp, CSP)
        script = next(d for d in csp.split(";") if d.strip().startswith("script-src")).split()
        self.assertEqual(script, ["script-src", "'self'", "https://plugin.jup.ag"])
        for src in (b'src="/app.js"', b'src="/terminal.js"', b'src="/vendor/lightweight-charts-4.2.3.js"'):
            self.assertIn(src, page)
        for f in ("/app.js", "/terminal.js"):
            code, headers, _ = self.call(f)
            self.assertEqual((code, headers["Content-Type"]), (200, "text/javascript; charset=utf-8"))
        self.assertIn(b"Real money. The desk's tests lose on average; coins listed here failed safety checks. "
                      b"Only trade what you can lose.", page.replace(b"<b>", b"").replace(b"</b>", b""))

    def test_no_signing_and_no_key_handling_in_the_desk(self):
        words = ("seed phrase", "private key", "secretkey", "keypair", "signtransaction", "mnemonic")
        for path in [*(STATIC.parent.glob("*.py")), STATIC / "terminal.js"]:
            text = path.read_text(encoding="utf-8").lower()
            for w in words:
                if w in ("seed phrase", "private key") and path.name in ("terminal.js", "memeterminal.py"):
                    continue                    # the warning text tells you never to type one in
                self.assertNotIn(w, text, "%s mentions %s" % (path.name, w))


class D1Identical(DeskCase):
    """D1 freeze (docs/MEME.md section 17): last_strong_ts is display only. Accounts, books, shadows and every log come
    out byte-for-byte the same with and without the code that records it."""

    def replay(self):
        S.save({"meme_policy": "learn"})
        api = FakeAPI()
        second = "Second111111111111111111111111111111"
        api.feed.append({"chainId": "solana", "tokenAddress": second})
        api.pairs[second] = api.pair(second, "POOL2", 1.0, 100000.0, h1=30.0)                # strong from the start
        for i, (px, h1) in enumerate([(1.0, 2.0), (1.25, 25.0), (1.3, 40.0), (0.7, -20.0), (1.6, 60.0), (1.6, 21.0)]):
            api.pairs[SOL] = api.pair(SOL, "POOL1", px, 100000.0, h1=h1)
            api.pairs[second]["priceUsd"] = str(1.0 + i * 0.1)
            meme.run(now=T0 + i * 1000, fetch=api)
        model = json.loads((meme.folder() / "model.json").read_text())
        files = {p.name: p.read_bytes() for p in meme.folder().iterdir() if p.name != "model.json"}
        return model, files, api.calls

    def test_saved_accounts_and_shadows_are_identical_without_last_strong_ts(self):
        with_model, with_files, with_calls = self.replay()
        stamps = {k: t.pop("last_strong_ts") for k, t in with_model["tracked"].items() if "last_strong_ts" in t}
        self.assertEqual(set(stamps), {"solana:" + SOL, "solana:Second111111111111111111111111111111"})
        self.assertGreater(len(with_files["shadows.jsonl"]), 0)
        with tempfile.TemporaryDirectory() as other, mock.patch.dict(os.environ, {"DESK_DATA": other}), \
                mock.patch.object(meme, "_note_strong", lambda t, now: None):
            plain_model, plain_files, plain_calls = self.replay()
        self.assertFalse(any("last_strong_ts" in t for t in plain_model["tracked"].values()))
        for part in ("account", "books", "shadows"):
            self.assertEqual(with_model[part], plain_model[part], part)
        self.assertEqual(with_model, plain_model)
        self.assertEqual(with_files, plain_files)
        self.assertEqual(with_calls, plain_calls)

    def test_the_strong_test(self):
        for f, want in [({"r1h": 20.0, "buy_ratio_m5": 0.55}, True), ({"r1h": 19.99, "buy_ratio_m5": 0.9}, False),
                        ({"r1h": 50.0, "buy_ratio_m5": 0.549}, False), ({"r1h": None, "buy_ratio_m5": 0.9}, False), ({}, False)]:
            t = {"f": f}
            meme._note_strong(t, T0)
            self.assertEqual("last_strong_ts" in t, want, f)


if __name__ == "__main__":
    unittest.main()
