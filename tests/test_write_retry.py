"""desk/skills.py write_json: retries the atomic replace on PermissionError (Windows: another process has the file open)."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from desk import skills


class FakeReplace:
    """Path.replace that raises PermissionError the first `fails` times, then does the real replace.
    Patch Path.replace with `fake.method` (a plain function, so it binds to the Path like the real method)."""

    def __init__(self, fails):
        self.fails, self.calls = fails, 0
        real = Path.replace

        def method(src, dst):
            self.calls += 1
            if self.calls <= self.fails:
                raise PermissionError(13, "simulated: the file is open in another process")
            return real(src, dst)

        self.method = method


class WriteRetry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.f = Path(self.tmp) / "model.json"
        self.f.write_text('{"old": 1}\n')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fails_twice_then_succeeds(self):
        fake = FakeReplace(2)
        with mock.patch.object(Path, "replace", fake.method), mock.patch.object(skills.time, "sleep") as sleep:
            skills.write_json(self.f, {"new": 2})
        self.assertEqual(fake.calls, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.05, 0.1])
        self.assertEqual(json.loads(self.f.read_text()), {"new": 2})
        self.assertFalse(self.f.with_suffix(".json.tmp").exists())

    def test_always_failing_still_raises(self):
        fake = FakeReplace(10 ** 6)
        with mock.patch.object(Path, "replace", fake.method), mock.patch.object(skills.time, "sleep") as sleep:
            with self.assertRaises(PermissionError):
                skills.write_json(self.f, {"new": 2})
        self.assertEqual(fake.calls, skills.REPLACE_TRIES)
        self.assertEqual(skills.REPLACE_TRIES, 10)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.05, 0.1, 0.2, 0.4, 0.5, 0.5, 0.5, 0.5, 0.5])
        self.assertEqual(json.loads(self.f.read_text()), {"old": 1})

    def test_other_errors_are_not_retried(self):
        with mock.patch.object(Path, "replace", side_effect=FileNotFoundError(2, "gone")) as rep, \
                mock.patch.object(skills.time, "sleep") as sleep:
            with self.assertRaises(FileNotFoundError):
                skills.write_json(self.f, {"new": 2})
        self.assertEqual(rep.call_count, 1)
        sleep.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows sharing semantics")
    def test_real_reader_released_during_retries(self):
        # a real open handle on the target blocks the replace; once it closes, a retry succeeds
        fh = open(self.f, "rb")
        real_sleep = skills.time.sleep

        def sleep_then_close(s):
            if not fh.closed:
                fh.close()
            real_sleep(0)

        with mock.patch.object(skills.time, "sleep", side_effect=sleep_then_close):
            skills.write_json(self.f, {"new": 2})
        self.assertEqual(json.loads(self.f.read_text()), {"new": 2})


if __name__ == "__main__":
    unittest.main()
