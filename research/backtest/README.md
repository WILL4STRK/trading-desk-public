# Blind formula backtest

Standard-library-only research; never imports a broker client or calls the desk's file-writing pipeline. All downloads, scratch files and outputs live here. It reads the existing source modules without editing them. No user settings or account data are read.

## Run

From the repository root:

```sh
python3 -B research/backtest/data.py
python3 research/backtest/backtest.py --fetch --variant base
open research/backtest/out/report.html
```

The first command downloads one year of daily candles for the current Nasdaq Trader common-stock directory and SPY. It caches every response, starts requests at least 0.31 seconds apart, backs off on rate limits and records unavailable symbols. This can take over half an hour. The second command downloads missing five-minute charts only for core names and the union of point-in-time screener candidates. No current-day screener is used.

After acquisition:

```sh
python3 research/backtest/backtest.py --variant base
```

This is offline. Identical reruns render the saved experiment without replaying the locked test. `--start YYYY-MM-DD --end YYYY-MM-DD` can restrict the evaluation dates on the first experiment. Twenty preceding full sessions are required for the desk's historical odds. `--replays` defaults to the desk's 10,000 paths and is recorded in the experiment identity.

Thirty variants are declared in `variants()` before any performance is computed. Validation selects one; `out/selection.json` seals it before the test runs. Changing data, code, dates or replay counts after exposure does not create a fresh holdout: the command refuses to overwrite the existing experiment. Do not delete the seal to repeatedly tune against the same test. A sealed but interrupted experiment requires inspection before resuming.

Every candidate gets a signal or no-signal record; orders, rejections, fills, expirations and exits are in `out/decisions.csv`. `trades.csv` and `daily.csv` cover tuning, validation and the predeclared locked runs. Segment labels distinguish them. `variants.csv` records all tried parameters and tuning/validation scores. `RESULTS.md` explains the limits and prints the full metric table.

## Invariants

- The strategy only receives a `View` of completed bars. Requesting future intraday or daily observations raises an assertion. Daily bars become available the next morning. Clock reversal is forbidden.
- The baseline invokes the desk's shared breakout, exit-plan, budget and risk functions and its vendored Time Machine. No headlines or Claude selection are reconstructed.
- No trigger-bar fills. Gap-open price improvement, stop-first ambiguous candles, no target credit in a fill candle. Flat-by is scheduled from the known market calendar.
- Live-like accounts reserve pending-order cash and cannot spend unsettled sale proceeds. T+1 follows the vendored trading calendar; the leveraged/immediate-settlement paper profile is separately labelled.
- Missing bars while holding or with a live pending order fail the run: there is no invented fill or exit. Insufficient opening candles prevent a signal.
- All-variant charts show validation. Showing every rejected variant on locked data would conflict with a single-winner holdout. The winner, base and predeclared trend fallback have locked charts, with fixed capital/cost sensitivity runs.

## Tests

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s research/backtest
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
```

The current directory is survivorship-biased, and free bars cannot recover order-book fills. A short locked result cannot justify a live change. See `RESULTS.md` after the experiment completes.
