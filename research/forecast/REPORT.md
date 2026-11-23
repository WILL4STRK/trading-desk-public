# Rule F1 (Chronos zero-shot forecast gate): backtest report

Written 2026-11-22. Paper only. Pre-registration: `docs/MEME.md` section 16, with 16.1 (2026-11-22, `8166d22`) and 16.2 (`a4238a7`), all
pushed before any real series was forecast. Model: `amazon/chronos-2` at the revision in `MODEL_REVISION`, zero-shot.

## Verdict: INCONCLUSIVE (and no sign of an edge)

The holdout has 40 F1 trades on 19 tokens. The pre-registered floor is 100 trades on 50 tokens, so the result is reported as
inconclusive, not as a pass or a fail. For the record, F1 would not have passed on the numbers either: its holdout mean is negative.

## Holdout (looked at once; `out/holdout.lock` written, results in `RESULTS-HOLDOUT.json`)

| Arm | Trades | Tokens | Mean net % | Median % | Win rate % | Mean without best 3 | Exits time / stop / target |
|---|---:|---:|---:|---:|---:|---:|---|
| **F1** (Chronos gate) | 40 | 19 | -0.56 | -2.54 | 40.0 | -2.68 | 18 / 15 / 7 |
| P0 on F1-eligible points | 473 | 61 | -3.19 | -4.45 | 24.7 | -3.37 | 279 / 129 / 65 |
| C-persist | 149 | 40 | -4.83 | -13.25 | 27.5 | -5.46 | 47 / 73 / 29 |

Dev (smoke test only, nothing tuned): F1 24 trades on 13 tokens, mean -4.39 %, median -10.53 %; P0 306 trades, mean -4.85 %; C-persist 90 trades, mean -6.67 %.

Pass-bar items on the holdout: mean > 0 no; median > 0 no; mean without best 3 > 0 no; at least 100 trades on 50 tokens no (40 on 19);
3 of 4 time folds positive yes by the whole-window folds (+2.69, +1.20, -4.79, +0.22) but those folds are mostly dev-period trades;
beats P0 yes (-0.56 against -3.19); beats C-persist yes (-0.56 against -4.83). Verdict: inconclusive.

## Selection share and the rejected points

F1 selects **2.5 % of eligible decision points on dev (75 of 2,966) and 2.4 % on the holdout (116 of 4,742)**. At the point level (every
selected or rejected point's own bracket outcome, not blocked by open positions):

| | Selected by F1 | Rejected by F1 |
|---|---|---|
| Dev | 75 points, mean -5.37 %, median -17.92 % | 2,891 points, mean -4.18 %, median -4.62 % |
| Holdout | 116 points, mean -4.79 %, median -5.49 % | 4,626 closed points, mean -3.92 %, median -4.21 % |

The points F1 picks do **worse** than the points it rejects, in dev and in the holdout. F1's better trade-level mean (-0.56 against -3.19)
comes from which few trades survive the one-position-per-token blocking and cooldown with only 40 trades, not from better picks. Read it as noise.

## Limits

- **The window is short and one regime:** the eligible points run 2026-11-12 15:00 to 2026-11-23 02:45 UTC (about 10 days); the holdout is the
  last 40 % (from 2026-11-18 22:03 UTC). The candles go back about 7 days from fetch (two pages of 1,000 bars), and the 24 h context floor
  removes the first day.
- 442 tokens in the universe (16.2), 432 with candles, 10 with zero bars; 3,553 decision points were dropped for gaps (more than 20 % forward-filled).
- 23 tokens carry the dead flag (liquidity at the last sighting under $1,000); that flag uses later information, as in section 5, the same for all arms.
- Survivorship bias of section 1 applies: tokens come from the desk's own discovery feeds. A backtest can only rule F1 out.
- No price impact is charged (backtest.py convention); flat 3.5 % round trip for every arm.
- Chronos ran on the CPU, batch of one, 15-minute decision points (TIMING-NOTE.md: 0.0284 s per forecast).

## Recommendation

Do not build the forward shadow recorder: the gate shows no ability to pick better entries than P0 or plain momentum, and the sample cannot support a
claim either way. If it is revisited, it needs much longer candle history (weeks, not days) and a new pre-registration.

## Files

`core.py`, `run.py`, `data.py`, `timing.py`, `TIMING-NOTE.md`, `MODEL_REVISION`, `requirements.txt`, `RESULTS-HOLDOUT.json`; tests in
`tests/test_forecast_f1.py` (9 tests: input cutoff, fill at the next open, gaps, one position per token, universe has no liquidity filter).
