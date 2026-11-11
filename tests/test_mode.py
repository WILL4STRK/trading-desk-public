import json

from desk import mode as M
from desk import pipeline
from desk import settings as S
from desk.engine import DeskError
from desk.journal import Journal
from tests import test_desk
from tests.test_desk import DemoCase


class Mode(DemoCase):
    def test_paper_is_the_default_and_keeps_the_old_file(self):
        d = self.desk()
        self.assertEqual(S.load()["mode"], "paper")
        self.assertEqual(M.journal_path("paper").name, "journal.db")      # the journal that existed before modes
        self.assertEqual(M.journal_path("live").name, "journal-live.db")
        self.assertEqual(d.state()["mode"], "paper")
        self.assertEqual(d.state()["account"]["cash"], 400.0)

    def test_live_needs_the_word(self):
        d = self.desk()
        for typed in (None, "", "live", "yes", "LIVE!"):
            with self.assertRaises(DeskError, msg=repr(typed)):
                d.set_mode("live", typed)
        self.assertEqual(S.load()["mode"], "paper")
        with self.assertRaises(DeskError):
            d.set_mode("real", "LIVE")
        self.assertEqual(d.set_mode("live", " LIVE "), "live")
        self.assertEqual(S.load()["mode"], "live")
        self.assertEqual(d.set_mode("paper"), "paper")                    # going back needs nothing

    def test_the_two_records_never_mix(self):
        d = self.desk()
        tid = d.j.open_trade("F", 10, 12.0, 11.5, 13.0, d.src.now())
        with self.assertRaises(DeskError):                                # not with a trade open
            d.set_mode("live", "LIVE")
        d.exit(tid, 13.0, "target")                                       # paper: +$10
        d.set_mode("live", "LIVE")
        st = d.state()
        self.assertEqual((st["mode"], st["account"]["cash"], st["stats"]["closed"], st["trades"]), ("live", 0.0, 0, []))
        self.assertIn("no cash yet", st["halt"])                          # live never guesses a balance
        d.cash_move("set", "$250")
        self.assertEqual(d.state()["account"]["cash"], 250.0)
        d.set_mode("paper")
        st = d.state()
        self.assertEqual((st["account"]["cash"], st["stats"]["closed"], st["stats"]["total_pnl"]), (410.0, 1, 10.0))
        d.set_mode("live", "LIVE")
        self.assertEqual(d.state()["account"]["cash"], 250.0)             # and live kept its own
        paper = Journal(M.journal_path("paper"))
        self.addCleanup(paper.close)
        self.assertEqual(len(paper.trades()), 1)
        kinds = [e["message"] for e in paper.events() if e["kind"] == "mode"]
        self.assertIn("Switched from paper to live. This paper record is kept as it is.", kinds)

    def test_each_mode_has_its_own_working_folder(self):
        d = self.desk()
        a = pipeline.workdir("2026-10-19", "TSLA")
        d.set_mode("live", "LIVE")
        b = pipeline.workdir("2026-10-19", "TSLA")
        self.assertNotEqual(a, b)
        self.assertEqual((a.parent.parent.name, b.parent.parent.name), ("work", "work-live"))

    def test_a_restart_in_live_opens_the_live_journal(self):
        d = self.desk()
        d.set_mode("live", "LIVE")
        d.cash_move("set", 300)
        from desk.engine import Desk
        from desk.source import Demo
        j = Journal(M.journal_path(S.load()["mode"]))
        self.addCleanup(j.close)
        self.assertEqual(Desk(j, Demo()).state()["account"]["cash"], 300.0)

    def test_settings_refuse_an_unknown_mode(self):
        with self.assertRaises(S.SettingsError):
            S.validate({"mode": "real"})


class ModeOverHttp(test_desk.Server):
    test_state_and_page = test_guards = test_scan_and_settings_over_http = None      # already run in test_desk

    def test_switch(self):
        code, body = self.call("/api/mode", {"mode": "live"})
        self.assertEqual(code, 400)
        self.assertIn("type LIVE", json.loads(body)["error"])
        self.assertEqual(self.call("/api/settings", {"changes": {"mode": "live"}})[0], 400)   # not through the form
        self.assertEqual(self.call("/api/mode", {"mode": "live", "confirm": "LIVE"})[0], 200)
        self.assertEqual(json.loads(self.call("/api/state")[1])["mode"], "live")
