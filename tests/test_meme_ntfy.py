"""Phone alerts for memecoin paper trades (desk/notify.py send_meme, hooked in desk/meme.py after the run is saved).
No network: urlopen is patched and the memecoin model reads tests.test_meme's fake API."""
import json
import os
import shutil
import tempfile
from unittest import mock

from desk import meme
from desk import notify
from desk import settings as S
from desk.engine import Desk, DeskError
from desk.journal import Journal
from desk.source import Demo
from tests.base import DeskCase
from tests.test_meme import SOL, T0, FakeAPI

BASE_TOKEN = "0x4200000000000000000000000000000000000abc"
BASE_POOL = "0x9999000000000000000000000000000000000def"

SOL_BUY = {"kind": "buy", "account": "all", "symbol": "WIF", "chain": "solana", "token": SOL, "pool": "POOL1",
           "dex": "raydium", "rule": "all", "px": 0.0012345, "fill": 0.0012567, "size": 20.0,
           "stop": 0.0012345 * 0.85, "target": 0.0012345 * 1.30, "liq": 123456.0, "v1h": 50000.0, "age_h": 30.0,
           "r1h": 2.5, "screen": "pass", "strict": "fail", "warning": "LP only 40% locked"}
BASE_BUY = dict(SOL_BUY, account="main", symbol="BRETT", chain="base", token=BASE_TOKEN, pool=BASE_POOL,
                dex="aerodrome", rule="quick", px=2.5, fill=2.55, stop=2.375, target=2.75, liq=2.5e6, v1h=800.0,
                age_h=0.5, r1h=-3.0, strict=None, warning=None)
SOL_SELL = {"kind": "sell", "account": "main", "symbol": "WIF", "chain": "solana", "token": SOL, "pool": "POOL1",
            "why": "stop", "entry_px": 1.0, "exit_px": 0.8, "held_min": 12.4, "pnl": -4.12, "ret_pct": -20.6,
            "balance": 95.88}
BASE_SELL = dict(SOL_SELL, account="moonshot", symbol="BRETT", chain="base", token=BASE_TOKEN, pool=BASE_POOL,
                 why="trail", entry_px=0.5, exit_px=0.75, held_min=300, pnl=0.41, ret_pct=8.2, balance=101.23)
LAST = "Paper trade only. These accounts lose money on average. Not advice."


def sent(urlopen):
    return [json.loads(c.args[0].data) for c in urlopen.call_args_list]


class Case(DeskCase):
    def setUp(self):
        super().setUp()
        cap = mock.patch.object(notify, "_meme_cap", notify._Cap())    # each test starts a fresh hour
        cap.start()
        self.addCleanup(cap.stop)

    def push(self, events, now=T0, **settings):
        st = S.validate(dict({"ntfy_topic": "desk-1", "meme_ntfy": "all"}, **settings))
        with mock.patch("urllib.request.urlopen") as urlopen:
            t = notify.send_meme(events, st, False, now=now)
            if t:
                t.join(5)
        return urlopen


class Messages(Case):
    def test_a_solana_buy(self):
        b = notify.meme_message(SOL_BUY)
        self.assertEqual(b["title"], "[PAPER MEME] BUY WIF · solana · buy everything")
        self.assertEqual(b["message"].split("\n"), [
            "Rule: Buy everything (P0)",
            "Price: $0.001234 (filled at $0.001257 after costs)",
            "Size: $20.00",
            "Stop: $0.001049 (-15%) · Target: $0.001605 (+30%)",
            "Pool liquidity: $123.5k",
            "1h volume: $50.0k",
            "Age: 30.0 h",
            "1h change: +2.5%",
            "Safety check: pass (strict: fail)",
            "Screen warning: LP only 40% locked",
            "DEX: raydium",
            LAST])

    def test_a_base_buy_without_strict_verdict_or_warning(self):
        b = notify.meme_message(BASE_BUY)
        self.assertEqual(b["title"], "[PAPER MEME] BUY BRETT · base · main")
        lines = b["message"].split("\n")
        self.assertEqual(lines[0], "Rule: Quick (P9)")
        self.assertIn("Stop: $2.375 (-5%) · Target: $2.750 (+10%)", lines)
        self.assertIn("Pool liquidity: $2.50M", lines)
        self.assertIn("1h volume: $800", lines)
        self.assertIn("Age: 30 min", lines)
        self.assertIn("1h change: -3.0%", lines)
        self.assertIn("Safety check: pass", lines)
        self.assertFalse(any(x.startswith("Screen warning") for x in lines))
        self.assertEqual(lines[-2:], ["DEX: aerodrome", LAST])

    def test_a_moonshot_buy_says_trailing_stop(self):
        b = notify.meme_message(dict(SOL_BUY, rule="moonshot", account="moonshot", target=None))
        self.assertIn("Rule: Moonshot (P8)", b["message"])
        self.assertIn("Target: none, a trailing stop", b["message"])

    def test_a_solana_sell(self):
        b = notify.meme_message(SOL_SELL)
        self.assertEqual(b["title"], "[PAPER MEME] SELL WIF · stop · -20.6%")
        self.assertEqual(b["message"].split("\n"), [
            "Account: main", "Entry $1.000 to exit $0.8000", "Held: 12 min", "Net: -$4.12 (-20.6%)",
            "Account value after: $95.88", LAST])

    def test_a_base_sell(self):
        b = notify.meme_message(BASE_SELL)
        self.assertEqual(b["title"], "[PAPER MEME] SELL BRETT · trailing stop · +8.2%")
        self.assertIn("Account: moonshot", b["message"])
        self.assertIn("Entry $0.5000 to exit $0.7500", b["message"])
        self.assertIn("Held: 300 min", b["message"])
        self.assertIn("Net: +$0.41 (+8.2%)", b["message"])
        self.assertIn("Account value after: $101.23", b["message"])


class Links(Case):
    def test_each_chain(self):
        cases = {"solana": ("https://solscan.io/token/" + SOL, True),
                 "base": ("https://basescan.org/token/" + BASE_TOKEN, False),
                 "bsc": ("https://bscscan.com/token/" + BASE_TOKEN, False),
                 "ethereum": ("https://etherscan.io/token/" + BASE_TOKEN, False)}
        for chain, (explorer, rugcheck) in cases.items():
            tok = SOL if chain == "solana" else BASE_TOKEN
            click, acts = notify.meme_links(chain, tok, "PAIR1")
            self.assertEqual(click, "https://dexscreener.com/%s/PAIR1" % chain)
            urls = [a["url"] for a in acts]
            self.assertEqual(urls[:2], [click, explorer], chain)
            self.assertEqual(len(urls), 3 if rugcheck else 2, chain)
            if rugcheck:
                self.assertEqual(urls[2], "https://rugcheck.xyz/tokens/" + SOL)

    def test_the_body_clicks_to_dexscreener_has_at_most_three_actions_and_no_localhost(self):
        for ev in (SOL_BUY, BASE_BUY, SOL_SELL, BASE_SELL, notify.MEME_SAMPLE):
            b = notify.meme_message(ev)
            self.assertTrue(b["click"].startswith("https://dexscreener.com/"))
            self.assertLessEqual(len(b["actions"]), 3)
            self.assertNotIn("127.0.0.1", json.dumps(b))
            self.assertNotIn("localhost", json.dumps(b))

    def test_no_pool_falls_back_to_the_token_page(self):
        self.assertEqual(notify.meme_links("base", BASE_TOKEN, None)[0], "https://dexscreener.com/base/" + BASE_TOKEN)


class Sending(Case):
    def test_all_sends_every_account_main_only_the_main_one(self):
        self.assertEqual(self.push([SOL_BUY, BASE_BUY, SOL_SELL]).call_count, 3)
        u = self.push([SOL_BUY, BASE_BUY, SOL_SELL], meme_ntfy="main")
        self.assertEqual([b["title"].split(" · ")[0] for b in sent(u)], ["[PAPER MEME] BUY BRETT", "[PAPER MEME] SELL WIF"])

    def test_nothing_when_off_in_the_demo_or_with_no_topic(self):
        self.assertEqual(self.push([SOL_BUY], meme_ntfy="off").call_count, 0)
        self.assertEqual(self.push([SOL_BUY], ntfy_topic="").call_count, 0)
        st = S.validate({"ntfy_topic": "desk-1", "meme_ntfy": "all"})
        with mock.patch("urllib.request.urlopen") as u:
            self.assertIsNone(notify.send_meme([SOL_BUY], st, True))
        self.assertEqual(u.call_count, 0)

    def test_the_memecoin_topic_wins_and_empty_falls_back(self):
        self.assertEqual(sent(self.push([SOL_BUY], meme_ntfy_topic="memes_2"))[0]["topic"], "memes_2")
        self.assertEqual(sent(self.push([SOL_BUY]))[0]["topic"], "desk-1")
        self.assertEqual(sent(self.push([SOL_BUY], ntfy_topic="", meme_ntfy_topic="memes_2"))[0]["topic"], "memes_2")

    def test_settings_are_validated(self):
        self.assertEqual(S.validate({})["meme_ntfy"], "off")
        self.assertEqual(S.validate({})["meme_ntfy_max_per_hour"], 20)
        for bad in [{"meme_ntfy": "on"}, {"meme_ntfy_topic": "has space"}, {"meme_ntfy_topic": "a/b"},
                    {"meme_ntfy_topic": "x" * 65}, {"meme_ntfy_max_per_hour": 0}, {"meme_ntfy_max_per_hour": "x"}]:
            with self.assertRaises(S.SettingsError, msg=str(bad)):
                S.validate(bad)
        self.assertEqual(S.validate({"meme_ntfy_topic": " ok-1_A "})["meme_ntfy_topic"], "ok-1_A")

    def test_the_cap_drops_and_logs_then_one_skipped_summary_at_the_next_free_slot(self):
        with self.assertLogs("desk.notify", "WARNING") as logs:
            u = self.push([SOL_BUY] * 5, meme_ntfy_max_per_hour=2)
        self.assertEqual(u.call_count, 2)
        self.assertEqual(sum("skipped" in m for m in logs.output), 3)
        self.assertEqual(self.push([SOL_BUY], now=T0 + 1800, meme_ntfy_max_per_hour=2).call_count, 0)  # same hour
        u = self.push([SOL_SELL], now=T0 + 3601, meme_ntfy_max_per_hour=2)
        titles = [b["title"] for b in sent(u)]
        self.assertEqual(titles, ["[PAPER MEME] 4 alerts skipped", "[PAPER MEME] SELL WIF · stop · -20.6%"])
        self.assertIn(LAST, sent(u)[0]["message"])
        u = self.push([SOL_SELL], now=T0 + 3602, meme_ntfy_max_per_hour=2)      # the summary is sent once
        self.assertEqual(u.call_count, 0)

    def test_a_failing_post_never_raises(self):
        st = S.validate({"ntfy_topic": "x", "meme_ntfy": "all"})
        with mock.patch("urllib.request.urlopen", side_effect=OSError("down")), self.assertLogs("desk.notify", "WARNING"):
            notify.send_meme([SOL_BUY, SOL_SELL], st, False, now=T0).join(5)
        with self.assertLogs("desk.notify", "WARNING"):
            self.assertIsNone(notify.send_meme([{"kind": "buy"}], st, False, now=T0))   # a broken event is dropped
        self.assertIsNone(notify.send_meme(None, st, False, now=T0))

    def test_the_test_button(self):
        st = S.validate({"ntfy_topic": "desk-1"})          # works while meme_ntfy is off, to check the topic
        with mock.patch("urllib.request.urlopen") as u:
            notify.send_meme_test(st, False)
        b = sent(u)[0]
        self.assertTrue(b["title"].startswith("[TEST] [PAPER MEME] BUY TEST"))
        self.assertTrue(b["message"].endswith(LAST))
        with self.assertRaises(ValueError):
            notify.send_meme_test(S.validate({}), False)
        with self.assertRaises(ValueError):
            notify.send_meme_test(st, True)
        j = Journal()
        self.addCleanup(j.close)
        with self.assertRaises(DeskError):
            Desk(j, Demo("2026-10-19 10:15")).test_meme_alert()


class ScanHook(Case):
    """meme.run: alerts after the run is saved, one per practice buy and sell, none for shadows."""

    def setUp(self):
        super().setUp()
        self.api = FakeAPI()
        self.threads = []

    def run_at(self, t, demo=False, **settings):
        S.save(dict({"meme_policy": "calm_aged", "ntfy_topic": "desk-1", "meme_ntfy": "all"}, **settings))
        real = notify.send_meme

        def send(events, st, demo_):
            th = real(events, st, demo_, now=t)
            th and self.threads.append(th)
            return th

        with mock.patch("urllib.request.urlopen") as u, mock.patch.object(notify, "send_meme", side_effect=send):
            out = meme.run(now=t, fetch=self.api, demo=demo)
            for th in self.threads:
                th.join(5)
        return out, sent(u)

    def test_one_alert_per_practice_buy_and_none_for_shadows(self):
        out, bodies = self.run_at(T0)
        s = meme.load()
        held = [("main", p) for p in s["account"]["positions"]] + [(n, p) for n, b in s["books"].items()
                                                                     for p in b["positions"]]
        self.assertGreater(len(s["shadows"]), 0)
        self.assertEqual(len(bodies), len(held))                     # shadows add nothing
        self.assertIn("[PAPER MEME] BUY MEME · solana · main", [b["title"] for b in bodies])
        self.assertIn("[PAPER MEME] BUY MEME · solana · buy everything", [b["title"] for b in bodies])
        main = next(b for b in bodies if b["title"].endswith("· main"))
        self.assertIn("Rule: Calm aged (P3)", main["message"])
        self.assertIn("Size: $20.00", main["message"])
        self.assertEqual(main["click"], "https://dexscreener.com/solana/POOL1")
        self.assertIn("https://rugcheck.xyz/tokens/" + SOL, [a["url"] for a in main["actions"]])

    def test_main_only_and_a_stop_sends_the_sell(self):
        _, bodies = self.run_at(T0, meme_ntfy="main")
        self.assertEqual([b["title"] for b in bodies], ["[PAPER MEME] BUY MEME · solana · main"])
        self.api.pairs[SOL] = FakeAPI.pair(SOL, "POOL1", 0.80, 100000.0)
        out, bodies = self.run_at(T0 + 60, meme_ntfy="main")
        self.assertEqual(out["sold"], 1)
        t = meme.load()["account"]["closed"][0]
        self.assertEqual([b["title"] for b in bodies], ["[PAPER MEME] SELL MEME · stop · %+.1f%%" % t["ret_pct"]])
        self.assertIn("Entry $1.000 to exit $0.8000", bodies[0]["message"])
        self.assertIn("Held: 1 min", bodies[0]["message"])

    def test_an_unknown_screen_opens_shadows_but_sends_nothing(self):
        self.api.rug = None
        out, bodies = self.run_at(T0)
        self.assertGreater(out["shadows_opened"], 0)
        self.assertEqual(bodies, [])

    def test_nothing_when_off_in_the_demo_or_with_no_topic(self):
        for kw in ({"meme_ntfy": "off"}, {"demo": True}, {"ntfy_topic": ""}):
            self.setUp_data()
            self.assertEqual(self.run_at(T0, **kw)[1], [], kw)

    def setUp_data(self):
        d = tempfile.mkdtemp(prefix="desk-test-")
        self.addCleanup(shutil.rmtree, d, True)
        os.environ["DESK_DATA"] = d

    def test_a_failing_push_never_raises_into_the_scan(self):
        S.save({"meme_policy": "calm_aged", "ntfy_topic": "desk-1", "meme_ntfy": "all"})
        with mock.patch.object(notify, "send_meme", side_effect=RuntimeError("boom")), \
                self.assertLogs("desk.meme", "WARNING"):
            out = meme.run(now=T0, fetch=self.api)
        self.assertEqual(out["bought"], 1)
        self.assertEqual(len(meme.load()["account"]["positions"]), 1)          # the run was saved
        with mock.patch.object(meme, "_trade_alerts", side_effect=KeyError("x")), self.assertLogs("desk.meme", "WARNING"):
            meme.run(now=T0 + 60, fetch=self.api)
        with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            out = meme.run(now=T0 + 120, fetch=self.api)
        self.assertIn("equity", out)

    def test_alerts_change_no_account_shadow_exit_or_cost(self):
        """The D1 freeze: the same runs with alerts off and on leave the same model and the same shadow log."""
        def record(**kw):
            self.setUp_data()
            self.api = FakeAPI()
            self.run_at(T0, **kw)
            self.api.pairs[SOL] = FakeAPI.pair(SOL, "POOL1", 0.80, 100000.0)
            self.run_at(T0 + 60, **kw)
            return meme.load(), meme._jsonl("shadows.jsonl")
        self.assertEqual(record(meme_ntfy="off"), record(meme_ntfy="all"))
