"""Test package setup. Keep this free of imports from desk: it runs before any test code.

Every test run gets a process-wide temp DESK_DATA, so code that runs outside a test's own temp folder
(a thread that outlives its test, a module-level call) can never fall back to <repo>/data."""
import atexit
import os
import shutil
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Recorded before anything can create it: test_zz_no_stray_data only judges a run that started without data/.
DATA_EXISTED_AT_START = (REPO / "data").exists()

SESSION_DATA = tempfile.mkdtemp(prefix="desk-test-session-")
os.environ["DESK_DATA"] = SESSION_DATA
atexit.register(shutil.rmtree, SESSION_DATA, True)
