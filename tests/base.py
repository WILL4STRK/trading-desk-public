import os
import shutil
import tempfile
import unittest


class DeskCase(unittest.TestCase):
    """Every test gets its own empty data folder."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="desk-test-")
        os.environ["DESK_DATA"] = self.tmp
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(os.environ.pop, "DESK_DATA", None)
