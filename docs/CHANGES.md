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
| 2026-11-22 | Known: the server's candle list repeats its last bar with the same timestamp; the page now drops duplicates (PR #8); a server-side fix needs a desk restart. | A repeated time made the Desk chart log "Value is null" on every page load. |
| 2026-11-22 | Model 6 §13.1: rules L1/L2, controls C0/C0-10, fills, exits, holdout split method and pass bar fixed. Post-migration fee 1.25 % flat (tier table unsourced). | Registered before any return was computed. |
| 2026-11-22 | Pre-registered Model 6 (new launches) data gate, universe and limits as MEME.md §13 on branch model-launch; the entry rule is fixed in a later commit, before any return. | 7 days of collector uptime first; mayhem and non-SOL handling decided before any data is read. |
| 2026-11-22 | Pre-registered N1 as a paired, shadow-only same-mint lockout on new Careful fills. | Measure net losses avoided and winners skipped after two stops. See [MEME.md §12](MEME.md#12-n1-shadow-same-mint-two-stop-lockout-pre-registered-2026-11-22). |
| 2026-11-21 | Registered an opt-in, practice-only main-account companion with a 15 % realized drawdown halt. | Compare sizing and halting on the same simulated fill tape. See [MEME.md §11](MEME.md#11-main-practice-drawdown-prototype-registration-2026-11-21-before-any-of-its-data). |
| 2026-11-18 | Paper mode only: 4x buying power, 5 % risk a trade, 20 % daily loss, no weekly stop, 50 % drop stop, no cash buffer, no trade cap, sales settle at once. Live unchanged. Rule stamps now record these. | Bolder, bigger practice trades show more in the review log. Paper is pretend money. |
| 2026-11-18 | New risk stops: weekly loss (`max_weekly_loss_pct`), drop from the best point (`max_drawdown_pct`), losers in a row (`max_losing_streak`, off by default). | Ideas from CloddsBot (docs/CLODDSBOT.md). |
| 2026-11-18 | Short side added, off by default (`directions` = long). Shorts are practice only unless `short_approved` is on. | docs/SHORTING.md. |
| 2026-11-11 | First public release. Default rules as shipped in `desk/settings.py`. | Starting point. |
