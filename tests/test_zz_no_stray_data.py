"""Runs last (zz). A test run must never create <repo>/data: it belongs to the real desk."""
import unittest

import tests


class NoStrayData(unittest.TestCase):
    @unittest.skipIf(tests.DATA_EXISTED_AT_START,
                     "data/ already existed when the run started (the real folder): nothing to judge here; "
                     "run the suite in a copy without data/")
    def test_run_did_not_create_repo_data(self):
        self.assertFalse((tests.REPO / "data").exists(),
                         "something wrote to <repo>/data instead of DESK_DATA: a thread or call outside a test's temp folder")


if __name__ == "__main__":
    unittest.main()
