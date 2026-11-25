"""Rule P10, yellow, and its controls (docs/MEME.md section 21; desk/memeyellow.py and its hooks in desk/meme.py).
No network."""
from unittest import mock

from desk import meme
from desk import memerules as R
from desk import memeyellow as YL
from desk import settings as S
from desk.memeterminal import cant_sell

from .base import DeskCase
from .test_meme import FakeAPI, SOL, T0


def sol(risks=(), score=5, strict="pass", strict_why=(), verdict=None):
    """A saved Solana screen as desk/meme.py writes it."""
    danger = [r for r in risks if r[1] == "danger"]
    v = verdict or ("fail" if danger or score > 30 else "pass")
    return {"verdict": v, "why": [], "strict": strict, "strict_why": list(strict_why),
            "report": {"score_normalised": score, "risks": [{"name": n, "level": lv} for n, lv in risks]}}


def evm(why=(), strict_why=(), honeypot="0", sell_tax="0"):
    return {"verdict": "fail" if why else "pass", "why": list(why), "strict": "fail" if strict_why else "pass",
            "strict_why": list(strict_why), "report": {"is_honeypot": honeypot, "sell_tax": sell_tax}}


def rug(risks=(), score=5, lock=100, top=5.0):
    """RugCheck's full report for FakeAPI: known holders, supply and the traded pool, so the strict check is known."""
    return {"score_normalised": score, "lpLockedPct": lock, "risks": [{"name": n, "level": lv} for n, lv in risks],
            "topHolders": [{"owner": "W1", "address": "A1", "pct": top}], "token": {"supply": 1e9}, "creatorBalance": 0,
            "markets": [{"pubkey": "POOL1", "lp": {"lpLockedPct": lock}}]}


class Flags(DeskCase):
    def test_solana_levels_score_and_strict_reasons(self):
        d, w = YL.flags("solana", sol([("Mutable metadata", "warn"), ("Copycat token", "warn")]))
        self.assertEqual((d, len(w)), ([], 2))
        d, w = YL.flags("solana", sol([("Low Liquidity", "danger")]))
        self.assertEqual((len(d), w), (1, []))
        d, _ = YL.flags("solana", sol(score=31))                          # the plain check's score limit is a danger flag
        self.assertEqual(d, ["RugCheck risk 31/100"])
        self.assertEqual(YL.flags("solana", sol(score=30))[0], [])        # 30 is no flag
        _, w = YL.flags("solana", sol(strict="fail", strict_why=["top 10 holders 45%"]))
        self.assertEqual(w, ["strict: top 10 holders 45%"])
        # a RugCheck holder warning and a strict holder reason are two flags (two sources)
        _, w = YL.flags("solana", sol([("High holder concentration", "warn")], strict="fail",
                                      strict_why=["top 10 holders 45%"]))
        self.assertEqual(len(w), 2)

    def test_evm_plain_reasons_are_danger_and_strict_reasons_are_warn(self):
        d, w = YL.flags("bsc", evm(why=["honeypot", "sell tax 20%"]))
        self.assertEqual((len(d), w), (2, []))
        d, w = YL.flags("base", evm(strict_why=["LP 0% locked", "owner power: is mintable"]))
        self.assertEqual((d, len(w)), ([], 2))

    def test_unknown_screens_are_none(self):
        for sc in (None, {}, sol(verdict="unknown"), sol(strict="unknown"), {"verdict": "pass", "strict": "pass"} | {"strict": None}):
            self.assertIsNone(YL.flags("solana", sc))
        self.assertIsNone(YL.flags("solana", sol([("Odd", "info")])))      # a level the definition does not know

    def test_groups_are_disjoint_and_unknown_or_blocked_is_in_none(self):
        self.assertEqual(YL.group("solana", sol(), []), YL.CLEAN)
        self.assertEqual(YL.group("solana", sol([("Copycat token", "warn")]), []), YL.POLICY)
        two = sol([("Copycat token", "warn")], strict="fail", strict_why=["pool 0% locked"])
        self.assertEqual(YL.group("solana", two, []), YL.POLICY)
        three = sol([("Copycat token", "warn"), ("Mutable metadata", "warn")], strict="fail", strict_why=["pool 0% locked"])
        self.assertEqual(YL.group("solana", three, []), YL.HEAVY)
        self.assertEqual(YL.group("solana", sol([("Low Liquidity", "danger"), ("Copycat token", "warn")]), []), YL.DANGER)
        self.assertIsNone(YL.group("solana", sol([("Copycat token", "warn")]), ["blocked"]))
        self.assertIsNone(YL.group("solana", sol(strict="unknown"), []))

    def test_decide_is_p0s_decision_in_one_group(self):
        sc = sol([("Copycat token", "warn")])
        self.assertEqual(YL.decide(True, "solana", sc, []), {"yellow": True, "yellow_clean": False,
                                                             "yellow_heavy": False, "yellow_danger": False})
        self.assertFalse(any(YL.decide(False, "solana", sc, []).values()))           # P0 did not fire
        self.assertFalse(any(YL.decide(True, "solana", None, []).values()))          # not screened: no buy
        self.assertFalse(any(YL.decide(True, "solana", sc, ["freeze"]).values()))    # can't-sell: no buy

    def test_the_terminal_block_is_what_excludes(self):
        frozen = sol([("Freeze Authority still enabled", "danger")])
        self.assertTrue(cant_sell(frozen))
        self.assertIsNone(YL.group("solana", frozen, cant_sell(frozen)))
        pot = evm(strict_why=["creator made a honeypot before"])                    # a warn, but the Terminal blocks it
        self.assertTrue(cant_sell(pot))
        self.assertIsNone(YL.group("bsc", pot, cant_sell(pot)))
        self.assertEqual(YL.group("bsc", evm(why=["contract not open source"]), []), YL.DANGER)


class Bar(DeskCase):
    @staticmethod
    def rows(policy, n, coins, net, t0=1000.0):
        return [{"key": "c%d" % (i % coins), "policy": policy, "screen": "pass", "ts": t0 + i * 60,
                 "net_pct": net + (i % 3)} for i in range(n)]

    def test_comparison_period_starts_at_the_first_shadow_of_the_four_lines(self):
        rows = [{"key": "a", "policy": "all", "screen": "pass", "ts": 10, "net_pct": -9.0},
                {"key": "b", "policy": "all", "screen": "fail", "ts": 210, "net_pct": -5.0},
                {"key": "c", "policy": "yellow_clean", "screen": "pass", "ts": 200, "net_pct": 1.0},
                {"key": "d", "policy": "yellow", "screen": "pass", "ts": 300, "net_pct": 6.0}]
        fw = meme.forward(rows)
        self.assertEqual((fw["yellow_all"]["n"], fw["yellow_all"]["mean"]), (1, -5.0))
        self.assertEqual((fw["yellow"]["n"], fw["yellow_clean"]["n"]), (1, 1))

    def test_only_the_yellow_line_can_pass_and_it_must_beat_clean_and_everything(self):
        rows = self.rows("yellow", 120, 60, 2.0) + self.rows("yellow_clean", 50, 25, -1.0) + self.rows("all", 50, 25, -3.0)
        fw = meme.forward(rows)
        self.assertTrue(meme._bar_for("yellow", fw["yellow"], fw))
        for k in ("yellow_clean", "yellow_heavy", "yellow_danger", "yellow_all", "yellow+strict"):
            self.assertFalse(meme._bar_for(k, fw.get(k) or {}, fw))
        better = self.rows("yellow", 120, 60, 2.0) + self.rows("yellow_clean", 50, 25, 5.0) + self.rows("all", 50, 25, -3.0)
        fw = meme.forward(better)
        self.assertFalse(meme._bar_for("yellow", fw["yellow"], fw))                 # the clean control is better
        none = self.rows("yellow", 120, 60, 2.0) + self.rows("all", 50, 25, -3.0)
        fw = meme.forward(none)
        self.assertFalse(meme._bar_for("yellow", fw["yellow"], fw))                 # no control (a) yet: no pass

    def test_too_few_coins_or_trades_is_no_pass(self):
        rows = self.rows("yellow", 120, 40, 2.0) + self.rows("yellow_clean", 50, 25, -1.0)
        fw = meme.forward(rows)
        self.assertFalse(meme._bar_for("yellow", fw["yellow"], fw))


class Forward(DeskCase):
    def setUp(self):
        super().setUp()
        S.save({"meme_policy": "calm_aged"})
        self.api = FakeAPI()

    def run_at(self, t):
        return meme.run(now=t, fetch=self.api)

    def lines(self):
        return sorted(x["policy"] for x in meme.load()["shadows"] if x["policy"].startswith("yellow"))

    def test_one_warning_opens_a_yellow_shadow_and_its_own_account(self):
        self.api.rug = rug([("Mutable metadata", "warn")])
        self.run_at(T0)
        s = meme.load()
        self.assertEqual(self.lines(), ["yellow"])
        sh = [x for x in s["shadows"] if x["policy"] == "yellow"][0]
        self.assertEqual((sh["screen"], sh["strict"], sh["px"]), ("pass", "pass", 1.0))
        self.assertNotIn("dex", sh)
        pos = s["books"]["yellow"]["positions"][0]
        self.assertAlmostEqual(pos["cost"], 20.0)                                    # 20 % of $100
        self.assertAlmostEqual(pos["fill"], 1.0 * (1 + 0.0175 + 20 / 50000))         # desk cost plus price impact
        self.assertTrue([x for x in s["shadows"] if x["policy"] == "all"])           # P0 untouched

    def test_a_clean_coin_is_control_a_and_the_yellow_book_does_not_buy(self):
        self.api.rug = rug()
        self.run_at(T0)
        self.assertEqual(self.lines(), ["yellow_clean"])
        self.assertEqual(meme.load()["books"]["yellow"]["positions"], [])

    def test_a_danger_coin_is_control_c(self):
        self.api.rug = rug([("Low Liquidity", "danger")])
        self.run_at(T0)
        self.assertEqual(self.lines(), ["yellow_danger"])
        self.assertEqual(meme.load()["books"]["yellow"]["positions"], [])

    def test_three_warnings_are_control_b(self):
        self.api.rug = rug([("Mutable metadata", "warn"), ("Copycat token", "warn")], lock=0, top=50.0)
        self.run_at(T0)
        self.assertEqual(self.lines(), ["yellow_heavy"])                             # 2 RugCheck + 2 strict reasons

    def test_cant_sell_and_unknown_screens_buy_in_no_line(self):
        self.api.rug = rug([("Freeze Authority still enabled", "danger")])
        self.run_at(T0)
        self.assertEqual(self.lines(), [])
        self.assertTrue([x for x in meme.load()["shadows"] if x["policy"] == "all"])

    def test_strict_unknown_is_no_buy(self):
        self.run_at(T0)                                                              # FakeAPI's default: no holder list
        self.assertEqual(self.lines(), [])

    def test_section_3_target_closes_the_yellow_shadow(self):
        self.api.rug = rug([("Mutable metadata", "warn")])
        self.run_at(T0)
        self.api.pairs[SOL] = FakeAPI.pair(SOL, "POOL1", 1.12, 100000.0)
        self.run_at(T0 + 60)
        self.assertEqual(self.lines(), ["yellow"])                                   # +12 % is no exit for P10
        self.api.pairs[SOL] = FakeAPI.pair(SOL, "POOL1", 1.31, 100000.0)
        self.run_at(T0 + 120)
        done = [x for x in meme._jsonl("shadows.jsonl") if x["policy"] == "yellow"][0]
        self.assertEqual((done["why"], done["exit_px"]), ("target", 1.30))
        self.assertAlmostEqual(done["net_pct"], R.net_pct(1.0, 1.30), places=2)
        self.assertEqual(meme.load()["books"]["yellow"]["closed"][0]["why"], "target")

    def test_a_p10_failure_is_kept_on_its_record_and_the_core_scan_goes_on(self):
        self.api.rug = rug([("Mutable metadata", "warn")])
        with mock.patch.object(YL, "decide", side_effect=RuntimeError("boom")):
            out = self.run_at(T0)
        s = meme.load()
        self.assertIn("RuntimeError: boom", s["yellow_v1"]["error"])
        self.assertEqual(out["new"], 1)
        self.assertEqual(self.lines(), [])
        self.assertTrue([x for x in s["shadows"] if x["policy"] == "all"])
        self.assertIn("RuntimeError: boom", meme.view()["yellow"]["error"])

    def test_view_card(self):
        self.api.rug = rug([("Mutable metadata", "warn")])
        self.run_at(T0)
        self.api.pairs[SOL] = FakeAPI.pair(SOL, "POOL1", 1.31, 100000.0)
        self.run_at(T0 + 60)
        v = meme.view()
        y = v["yellow"]
        self.assertEqual((y["stats"]["n"], y["ends"], y["passed"]), (1, {"target": 1}, False))
        self.assertEqual(set(y["controls"]), {"yellow_clean", "yellow_heavy", "yellow_danger"})
        self.assertEqual(y["everything"]["n"], 1)                                    # P0 since the first P10 shadow
        self.assertIn("yellow", v["model_results"])
        self.assertIn("yellow", [b["name"] for b in v["books"]])
