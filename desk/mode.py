"""Paper or live: two separate records.

Paper trading (practice money) and real money are different accounts
with different cash. If their trades shared one journal, every later number
(cash, results, the daily loss limit) would be wrong for both. So each mode
has its own journal file and its own working folder, and nothing is copied
between them.

  paper   data/journal.db        the record that existed before modes did
  live    data/journal-live.db   starts at $0 until you enter your real cash

The desk never says when to move from paper to live. That is your decision.
It never places a real order. Auto paper (desk/auto.py) works in paper mode
only, in the desk's own pretend practice account.
"""
from .paths import data_dir

MODES = ("paper", "live")
DEFAULT = "paper"
CONFIRM_WORD = "LIVE"

_FILES = {"paper": "journal.db", "live": "journal-live.db"}
_WORK = {"paper": "work", "live": "work-live"}
# Things worth carrying across a switch so the screen is not blank: neither is money or a record.
CARRY = ("premarket", "congress")


def journal_path(mode):
    return data_dir() / _FILES[mode]


def work_root(mode):
    return data_dir() / _WORK.get(mode, "work")


def seed_amount(mode, starting_balance):
    """Paper starts with the practice balance from Settings. Live starts empty:
    the desk must never guess how much real money there is."""
    return float(starting_balance) if mode == "paper" else 0.0


def confirmed(mode, typed):
    """Going to live needs the word typed out. Going back to paper needs nothing."""
    return mode != "live" or str(typed or "").strip() == CONFIRM_WORD
