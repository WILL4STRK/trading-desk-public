import os
import shutil
import tempfile
import unittest
from unittest import mock

from desk import settings as S


class DeskCase(unittest.TestCase):
    """Every test gets its own empty data folder."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="desk-test-")
        prev = os.environ.get("DESK_DATA")        # the session's temp folder (tests/__init__.py); put it back after
        os.environ["DESK_DATA"] = self.tmp
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(_restore_data_env, prev)
        # the tests check your own limits; paper mode's bold profile has its own test (test_settings.PaperBold)
        patcher = mock.patch.dict(S.PAPER_BOLD, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        lev = mock.patch.object(S, "PAPER_LEVERAGE", 1.0)        # and plain cash; test_settings.PaperBold covers 4x
        lev.start()
        self.addCleanup(lev.stop)
        # model 4 (desk/meme.py): never on the network in tests, and the scheduler's memecoin thread never starts
        from desk import meme
        mm = mock.patch.object(meme, "get", side_effect=meme.MemeError("not in tests"))
        mm.start()
        self.addCleanup(mm.stop)
        from desk.scheduler import Scheduler
        ms = mock.patch.object(Scheduler, "_meme", lambda self: None)
        ms.start()
        self.addCleanup(ms.stop)
        # registered last, so it runs first: wait for model threads while the temp DESK_DATA and fakes are in place
        self.addCleanup(_join_desk_threads)


def _restore_data_env(prev):
    if prev is None:
        os.environ.pop("DESK_DATA", None)
    else:
        os.environ["DESK_DATA"] = prev


def _join_desk_threads(timeout=10.0):
    """Wait for every desk-* thread (scheduler, news, meme) so none outlives its test's DESK_DATA and fakes."""
    import threading
    import time
    end = time.time() + timeout
    for t in threading.enumerate():
        if t is not threading.current_thread() and t.name.startswith("desk-"):
            t.join(max(0.0, end - time.time()))
