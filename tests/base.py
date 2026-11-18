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
        os.environ["DESK_DATA"] = self.tmp
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(os.environ.pop, "DESK_DATA", None)
        # the tests check your own limits; paper mode's bold profile has its own test (test_settings.PaperBold)
        patcher = mock.patch.dict(S.PAPER_BOLD, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        lev = mock.patch.object(S, "PAPER_LEVERAGE", 1.0)        # and plain cash; test_settings.PaperBold covers 4x
        lev.start()
        self.addCleanup(lev.stop)
