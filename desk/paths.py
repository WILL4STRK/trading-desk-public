"""Where things live. DESK_DATA moves the data folder (tests use a temp dir)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "day-trader-skill"
STATIC = Path(__file__).resolve().parent / "static"
FIXTURES = VENDOR / "tests" / "fixtures"


def data_dir():
    d = Path(os.environ.get("DESK_DATA") or (ROOT / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d
