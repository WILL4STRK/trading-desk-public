"""The watchlist agent (desk/agent.py), with a fake Claude and fake research: no network, no claude."""
import json
import subprocess
from pathlib import Path
from unittest import mock

from desk import agent as AG
from desk import settings as S
from tests.base import DeskCase


def cand(sym, atr):
    return {"symbol": sym, "price": 20.0, "avg_volume": 5_000_000, "from": ["most_actives"], "atr_pct": atr}


CANDS = [cand("WILD", 9.0), cand("MID", 5.0), cand("CALM", 2.0)]


def gather(st, max_price):
    return {"core": ["AAPL"], "picks": CANDS[:st["auto_watchlist_movers"]], "errors": [],
            "watchlist": ["AAPL"] + [c["symbol"] for c in CANDS]}


def no_news(symbols, st, wd):
    return {s: [{"title": "%s beats estimates" % s, "source": "test"}] for s in symbols}


class Agent(DeskCase):
    def st(self, n=2):
        return dict(S.validate({}), auto_watchlist_movers=n)

    def research(self, ask, n=2):
        return AG.research(self.st(n), 500, Path(self.tmp), ask=ask, gather=gather, news=no_news)

    def test_claude_picks_from_the_candidates_with_reasons(self):
        seen = {}

        def ask(text):
            seen["text"] = text
            return {"summary": "Catalysts today.", "picks": [{"symbol": "calm", "why": "earnings"}, {"symbol": "MID", "why": "deal"}]}
        r = self.research(ask)
        self.assertEqual((r["by"], r["watchlist"]), ("claude", ["AAPL", "CALM", "MID"]))
        self.assertEqual(r["picks"][0]["why"], "earnings")
        self.assertIn("CALM beats estimates", seen["text"])
        self.assertIn("do not pick): AAPL", seen["text"])

    def test_only_candidates_count_and_at_most_n(self):
        r = self.research(lambda t: {"summary": "", "picks": [{"symbol": "EVIL", "why": "a headline said so"},
                                                              {"symbol": "WILD", "why": "a"}, {"symbol": "WILD", "why": "b"},
                                                              {"symbol": "MID", "why": "c"}, {"symbol": "CALM", "why": "d"}]})
        self.assertEqual([p["symbol"] for p in r["picks"]], ["WILD", "MID"])

    def test_a_failing_claude_falls_back_to_the_formula(self):
        def ask(text):
            raise RuntimeError("not logged in")
        r = self.research(ask)
        self.assertEqual((r["by"], r["watchlist"]), ("formula", ["AAPL", "WILD", "MID"]))
        self.assertIn("not logged in", r["errors"][-1])
        r = self.research(lambda t: {"summary": "", "picks": [{"symbol": "NOPE", "why": "x"}]})
        self.assertEqual(r["by"], "formula")

    def test_ask_claude_reads_structured_output(self):
        fake = mock.Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(
            {"is_error": False, "structured_output": {"summary": "s", "picks": []}}), ""))
        with mock.patch.object(AG.shutil, "which", return_value="/bin/claude"):
            self.assertEqual(AG.ask_claude("hi", run=fake), {"summary": "s", "picks": []})
            cmd = fake.call_args[0][0]
            self.assertEqual(cmd[cmd.index("--tools") + 1], "")          # no tools at all
            self.assertIn("--strict-mcp-config", cmd)
            fake.return_value = subprocess.CompletedProcess([], 1, json.dumps({"is_error": True, "result": "auth"}), "")
            with self.assertRaises(RuntimeError):
                AG.ask_claude("hi", run=fake)
        with mock.patch.object(AG.shutil, "which", return_value=None), self.assertRaises(RuntimeError):
            AG.ask_claude("hi")
