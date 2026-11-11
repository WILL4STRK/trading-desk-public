from desk import guards, pipeline, skills
from desk import settings as S
from tests.test_desk import DemoCase


class Allowlist(DemoCase):
    def test_only_ticket_show_can_run(self):
        for bad in (["approve"], ["result", "x"], ["flatten"], [], ["--json", "show"]):
            with self.assertRaises(guards.NotAllowed, msg=str(bad)):
                skills.run("broker", bad, self.tmp)
        guards.check_command("broker", ["show", "--json"])
        guards.check_command("scanner", ["orb"])            # other tools are not restricted

    def test_the_desk_source_never_names_another_ticket_command(self):
        import pathlib
        src = "".join(p.read_text() for p in pathlib.Path(skills.__file__).parent.glob("*.py"))
        for word in ('"approve"', '"result"', '"flatten"', '"submit"'):
            self.assertNotIn('run("broker", [%s' % word, src)


class MinStop(DemoCase):
    def test_arithmetic(self):
        self.assertIsNone(guards.stop_too_close(100, 0.30, 0.3))
        self.assertIn("0.29%", guards.stop_too_close(100, 0.29, 0.3))
        self.assertIsNone(guards.stop_too_close(100, 0.01, 0))        # 0 = off

    def test_a_stop_closer_than_the_floor_blocks_the_signal(self):
        d = self.desk(min_stop_pct=5)                                   # TSLA's stop is 1.6% away
        status, sig = pipeline.evaluate("TSLA", S.load(), d.j, d.src)
        self.assertEqual(status, "blocked")
        self.assertEqual(sig["qty"], 0)
        self.assertIn("Your floor is 5.00%", sig["problems"][0])

    def test_the_default_floor_lets_tsla_through(self):
        d = self.desk()
        status, sig = pipeline.evaluate("TSLA", S.load(), d.j, d.src)
        self.assertNotEqual(status, "blocked")

    def test_setting_is_validated(self):
        with self.assertRaises(S.SettingsError):
            S.validate({"min_stop_pct": 50})
