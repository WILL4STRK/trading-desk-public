# Rule changes

Every change to how the desk makes or sizes a signal, with the date, what
changed and why. Newest first.

Two logs work together:

- **This file** is for changes made in the code or agreed in a review of your
  record (`python3 -m desk review`). Add a line here whenever a rule changes.
- **The journal's event log** records every settings change made on the
  dashboard by itself, old value and new (`python3 -m desk review` lists them).

Each signal carries a stamp (`rule set a1b2c3d4`), a short code for the rule
settings that made it, so results can be read per rule set. One bad week is
not a reason to change a rule; a change needs enough trades to mean something.

| Date | What changed | Why |
|---|---|---|
| 2026-11-11 | First public release. Default rules as shipped in `desk/settings.py`. | Starting point. |
