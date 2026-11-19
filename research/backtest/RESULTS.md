# Backtest results

1. Evaluation: 2026-09-25–2026-11-18 (39 sessions); locked test: 2026-11-09–2026-11-18 (8 sessions).
2. Data: Yahoo five-minute OHLCV; 5415 daily-history symbols, 212 intraday symbols; formula picks only, no Claude or headlines.
3. Locked $400 live-like base: -5.40%; SPY: -0.69%.
4. Headline — validation winner min_volume_x=1.5: -2.77% on locked data, -2.08 percentage points versus SPY, after base costs.
5. The selected strategy did not beat SPY on the locked test.
6. Predeclared trend-only filter fallback: 0.00%; better than the winner.
7. Mean daily excess 95% block-bootstrap CI: [-0.9282%, 0.3883%]; two-sided p=0.4693.
8. Too few independent market days to establish an edge. No live setting changes recommended.

## Full metrics

| run | return_pct | cagr_pct | daily_vol_pct | sharpe | sortino | max_drawdown_pct | longest_drawdown_days | win_rate_pct | expectancy_r | profit_factor | trades | trades_per_day | time_in_market_pct | correlation_spy | days |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base / $400 / costs 0x / locked | -2.6088 | N/A | 1.6768 | -3.0079 | -4.7234 | -5.6537 | 7 | 25.0000 | -0.3351 | 0.6178 | 12 | 1.5000 | 19.5513 | 0.6401 | 8 |
| base / $400 / costs 1x / locked | -5.4038 | N/A | 1.7287 | -6.2350 | -7.6101 | -7.9668 | 7 | 20.0000 | -0.6331 | 0.4028 | 10 | 1.2500 | 8.3333 | 0.6376 | 8 |
| base / $400 / costs 2x / locked | -8.0777 | N/A | 1.7666 | -9.2883 | -9.3928 | -10.1815 | 7 | 20.0000 | -0.9239 | 0.2732 | 10 | 1.2500 | 8.3333 | 0.6420 | 8 |
| base / $2000 / costs 0x / locked | -2.5978 | N/A | 1.6925 | -2.9644 | -4.6628 | -5.6575 | 7 | 18.1818 | -0.3940 | 0.6215 | 11 | 1.3750 | 8.6538 | 0.6315 | 8 |
| base / $2000 / costs 1x / locked | -5.4048 | N/A | 1.7361 | -6.2087 | -7.5918 | -7.9806 | 7 | 30.7692 | -0.4820 | 0.4051 | 13 | 1.6250 | 20.3526 | 0.6351 | 8 |
| base / $2000 / costs 2x / locked | -8.1380 | N/A | 1.7905 | -9.2330 | -9.3494 | -10.2421 | 7 | 25.0000 | -0.8353 | 0.2748 | 12 | 1.5000 | 9.1346 | 0.6310 | 8 |
| min_volume_x=1.5 / $400 / costs 0x / locked | -2.1300 | N/A | 0.4006 | -10.6228 | -9.2362 | -2.1300 | 5 | 0.0000 | -0.8787 | 0.0000 | 3 | 0.3750 | 5.6090 | 0.4614 | 8 |
| min_volume_x=1.5 / $400 / costs 1x / locked | -2.7687 | N/A | 0.5566 | -9.9543 | -8.8393 | -2.7687 | 5 | 0.0000 | -1.1155 | 0.0000 | 3 | 0.3750 | 5.6090 | 0.3963 | 8 |
| min_volume_x=1.5 / $400 / costs 2x / locked | -3.4212 | N/A | 0.7279 | -9.4180 | -8.5024 | -3.4212 | 5 | 0.0000 | -1.3523 | 0.0000 | 3 | 0.3750 | 5.6090 | 0.3529 | 8 |
| min_volume_x=1.5 / $2000 / costs 0x / locked | -2.1625 | N/A | 0.4052 | -10.6627 | -9.2591 | -2.1625 | 5 | 0.0000 | -0.8787 | 0.0000 | 3 | 0.3750 | 5.6090 | 0.4663 | 8 |
| min_volume_x=1.5 / $2000 / costs 1x / locked | -2.8115 | N/A | 0.5646 | -9.9647 | -8.8457 | -2.8115 | 5 | 0.0000 | -1.1155 | 0.0000 | 3 | 0.3750 | 5.6090 | 0.3962 | 8 |
| min_volume_x=1.5 / $2000 / costs 2x / locked | -3.4657 | N/A | 0.7357 | -9.4416 | -8.5175 | -3.4657 | 5 | 0.0000 | -1.3523 | 0.0000 | 3 | 0.3750 | 5.6090 | 0.3538 | 8 |
| trend / $400 / costs 0x / locked | 0.0000 | N/A | 0.0000 | N/A | N/A | 0.0000 | 0 | N/A | N/A | N/A | 0 | 0.0000 | 0.0000 | N/A | 8 |
| trend / $400 / costs 1x / locked | 0.0000 | N/A | 0.0000 | N/A | N/A | 0.0000 | 0 | N/A | N/A | N/A | 0 | 0.0000 | 0.0000 | N/A | 8 |
| trend / $400 / costs 2x / locked | 0.0000 | N/A | 0.0000 | N/A | N/A | 0.0000 | 0 | N/A | N/A | N/A | 0 | 0.0000 | 0.0000 | N/A | 8 |
| trend / $2000 / costs 0x / locked | 0.0000 | N/A | 0.0000 | N/A | N/A | 0.0000 | 0 | N/A | N/A | N/A | 0 | 0.0000 | 0.0000 | N/A | 8 |
| trend / $2000 / costs 1x / locked | 0.0000 | N/A | 0.0000 | N/A | N/A | 0.0000 | 0 | N/A | N/A | N/A | 0 | 0.0000 | 0.0000 | N/A | 8 |
| trend / $2000 / costs 2x / locked | 0.0000 | N/A | 0.0000 | N/A | N/A | 0.0000 | 0 | N/A | N/A | N/A | 0 | 0.0000 | 0.0000 | N/A | 8 |
| base / $400 / paper 4x immediate settlement / locked | -15.4875 | N/A | 2.5214 | -12.9300 | -11.4511 | -18.4717 | 7 | 29.0323 | -0.4721 | 0.3393 | 31 | 3.8750 | 44.0705 | 0.7558 | 8 |
| SPY / $400 / locked | -0.6922 | N/A | 1.3341 | -0.9405 | -1.5608 | -3.8970 | 5 | N/A | N/A | N/A | N/A | N/A | 100 | 1.0000 | 8 |
| SPY / $2000 / locked | -0.6922 | N/A | 1.3341 | -0.9405 | -1.5608 | -3.8970 | 5 | N/A | N/A | N/A | N/A | N/A | 100 | 1.0000 | 8 |
| base / $400 / tuning | -2.7286 | N/A | 1.1777 | -1.5315 | -2.4939 | -4.4113 | 12 | 29.7297 | -0.2291 | 0.7953 | 37 | 1.6087 | 29.9889 | 0.4156 | 23 |
| min_volume_x=1.5 / $400 / tuning | -3.6738 | N/A | 0.4683 | -5.4761 | -5.9904 | -4.7218 | 22 | 10.0000 | -0.7281 | 0.2759 | 10 | 0.4348 | 12.8205 | 0.0518 | 23 |
| SPY / $400 / tuning | 0.3233 | N/A | 0.6173 | 0.4079 | 0.5438 | -2.4542 | 15 | N/A | N/A | N/A | N/A | N/A | 100 | 1.0000 | 23 |
| base / $400 / validation | -5.5275 | N/A | 0.9602 | -11.6424 | -10.5838 | -5.5275 | 8 | 11.1111 | -0.4827 | 0.1747 | 9 | 1.1250 | 47.4359 | 0.1096 | 8 |
| min_volume_x=1.5 / $400 / validation | 3.2700 | N/A | 0.7934 | 8.1184 | N/A | 0.0000 | 0 | 100.0000 | 1.3255 | N/A | 2 | 0.2500 | 14.4231 | 0.0441 | 8 |
| SPY / $400 / validation | -4.4656 | N/A | 1.8336 | -4.8000 | -5.1953 | -5.0899 | 7 | N/A | N/A | N/A | N/A | N/A | 100 | 1.0000 | 8 |

## Selection and inference

```json
{
  "hashes": {
    "code": "6801fc049980b54f48118f6a5a4769ffaf9d6e626589bf4a2334252844391ff0",
    "universe": "baebc9d0c5b8d2ad142c2bbd313bfb9c7838ac742268cdc97ea1aa9b567dda8d",
    "as_of": "2026-11-19T09:47:24.262486-05:00",
    "daily": "8e28b7180a6509847898bdd8f9f5fb13a8135a8a50b86c165427a83649c30a03",
    "intraday": "15af8bf1266afb389790ce3ceee94120ddbcbf1abf1c76bbb1337a61e81be615",
    "start": "2026-09-25",
    "end": "2026-11-18",
    "replays": 10000
  },
  "train": [
    "2026-09-25",
    "2026-09-28",
    "2026-09-29",
    "2026-09-30",
    "2026-10-01",
    "2026-10-02",
    "2026-10-05",
    "2026-10-06",
    "2026-10-07",
    "2026-10-08",
    "2026-10-09",
    "2026-10-12",
    "2026-10-13",
    "2026-10-14",
    "2026-10-15",
    "2026-10-16",
    "2026-10-19",
    "2026-10-20",
    "2026-10-21",
    "2026-10-22",
    "2026-10-23",
    "2026-10-26",
    "2026-10-27"
  ],
  "validation": [
    "2026-10-28",
    "2026-10-29",
    "2026-10-30",
    "2026-11-02",
    "2026-11-03",
    "2026-11-04",
    "2026-11-05",
    "2026-11-06"
  ],
  "locked": [
    "2026-11-09",
    "2026-11-10",
    "2026-11-11",
    "2026-11-12",
    "2026-11-13",
    "2026-11-16",
    "2026-11-17",
    "2026-11-18"
  ],
  "winner": {
    "name": "min_volume_x=1.5",
    "settings": {
      "min_volume_x": 1.5
    }
  },
  "selection": "highest validation net excess return at $400/base costs; tie: fewer trades then declaration order",
  "predefined_test_controls": [
    "base",
    "trend"
  ],
  "trials": 30
}
```

| mean_daily_excess_pct | ci_low_pct | ci_high_pct | p_two_sided | samples | seed | block_days | days |
| --- | --- | --- | --- | --- | --- | --- | --- |
| -0.2700 | -0.9282 | 0.3883 | 0.4693 | 10000 | 19 | 5 | 8 |

Validation Bonferroni p (30 trials): 0.0030

## Methods, recommendations and limits

The fixed 30 variants and selection criterion were declared before the test. Selection uses $400/base-cost validation excess return; ties prefer fewer trades, then declaration order. The last 20% is opened only after selection.json is written. The base and trend fallback, both capital sizes, three costs and paper control were predeclared; none can replace the winner headline.

Charts of all variants use validation, not locked data: testing every variant on the holdout would defeat the requested one-winner holdout. Only the winner and predeclared controls are measured on locked data. Each segment starts with fresh capital and no carried risk halt; all segments may use price history strictly before each simulated moment.

Entry/stop/target rules call desk.outcome._breakout and the vendored exit planner; sizing calls trader-budget.size_trade; circuit breakers call desk.breaker.status; calendar and early-close rules come from trader-entry and desk.settings.for_day. Time Machine uses its actual 10,000-path simulation (or the explicitly recorded CLI replay count), a fixed seed of 19 and only prior complete sessions. Its 20-session warmup is excluded from evaluation.

No duplicated trading rule engine or broker connection. Default settings are copied in memory; user settings files are never loaded. Shorts are not tested because live-like accounts are cash accounts. Paper control uses PAPER_BOLD, immediate settlement and the desk’s buying-power calculation with 4x leverage; whole-share budget sizing still applies.

Signals use only completed five-minute candles. Pending orders fill only in a later candle, at the limit or a better gap-open price. Stops win ambiguous bars; a gap below the stop fills at the lower open. Targets are never credited in the fill candle. At flat-by, the known calendar schedules a market exit at that candle’s open, observed when its candle closes.

Base execution cost is the desk’s round-trip est_cost_per_share plus $0.01 per share per side, charged as cash deductions. Zero and double scenarios re-simulate sizing and cash, not just subtract fees afterward. Whole shares; no leverage and T+1 sale proceeds for live-like accounts. Pending orders reserve cash, including entry costs.

SPY is a fractional, fully invested price-return benchmark bought at the previous close before each segment, so the first day return is included. Dividends, taxes, interest and SPY trading friction are excluded; strategy costs are explicit. Yahoo close (not adjusted close) is used. This is a downloaded history, not an as-of vendor archive: corrections or retroactive corporate-action adjustments can affect whole-share affordability and cent rounding. Timestamp blindness does not eliminate that revision bias. R uses planned limit-to-stop risk, including net costs in the numerator.

Sharpe and Sortino use daily returns, 252 trading days/year, risk-free rate zero; Sortino uses all days in downside deviation. CAGR is suppressed below 252 sessions. Drawdown includes the starting balance. Time in market is the fraction of regular-session five-minute intervals with exposure, not capital-weighted. Buy-and-hold trade win rate/R/profit factor are N/A. Profit factor with no losses, or ratios with zero denominators, are N/A.

Inference: 10,000 paired circular moving-block bootstrap resamples, five-session blocks, seed 19. CI estimates the mean daily strategy-minus-SPY return; the two-sided p-value resamples centered differences under the null. Dependence and regime shifts can invalidate it. Validation p is Bonferroni-adjusted for 30 variants; this is a conservative multiple-testing correction, not a claim that tuning creates 30 independent trials.

Rolling five-session tuning diagnostics are logged. No repeated model refit or additional rolling winner selection is performed: the available history is too short after the 20-session odds warmup. Test controls are sensitivity checks, not additional opportunities to select a winner.

Survivorship: the Nasdaq Trader directory is current, not historical. Delisted stocks and prior ticker mappings are missing, and IPOs need sufficient prior bars. This could reverse a small apparent edge; its magnitude cannot be estimated from this dataset. Failed daily downloads are explicitly listed in data_failures_daily.json; screen results cover only successful current listings, not a survivorship-free historical market.

Movers: each 09:00 screen takes the top 25 prior-session volumes, top 25 prior-close gains and top 25 losses across the available directory. The desk’s average-volume/price filters, 40-candidate chart budget and prior-day ATR ranking are reused. No present-day Yahoo screener is used. Core watchlist order breaks simultaneous-signal cash competition; pending fills use alphabetical order.

Missing bars block new signals through that moment. Missing candles while holding or with pending orders cause a hard failure, rather than a made-up exit. The feed is unofficial, may be delayed/revised and is not a historical order book. Full fills assume liquidity; spreads, queue priority, halts and within-candle chronology are not observable. No historical headlines or Claude actions are included.

Data-source check: Yahoo actually returned five-minute data from 2026-08-27 and hourly data from 2023-12-22 in this download. Hourly bars cannot recreate a 15-minute opening range. Alpha Vantage historical intraday is premium and needs a key, so it was not used. Dukascopy exposes longer historical bid/ask exports; its FAQ defines volume as summed best-bid or best-ask volume. That is not the exchange share-volume input this screener requires, so it was not substituted for Yahoo OHLCV. Stooq’s archive presented a JavaScript verification barrier. No sign-up, paid source or account creation was attempted.

No live setting change is supported by this short locked sample, regardless of the winner’s sign. Observe several months of genuinely new paper trading before treating any apparent excess return as evidence. Tuning/data/code hashes and the selected parameters are sealed in selection.json; identical reruns render saved experiment results without replaying the holdout.

## Sources

- [Yahoo observed five-minute SPY endpoint](https://query2.finance.yahoo.com/v8/finance/chart/SPY?range=60d&interval=5m)
- [Nasdaq Trader symbol directory](https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs)
- [Alpha Vantage historical intraday documentation](https://www.alphavantage.co/documentation/)
- [Dukascopy historical export](https://www.dukascopy.com/swiss/english/marketwatch/historical/)
- [Dukascopy volume definition](https://www.dukascopy.com/swiss/english/about/faq/)
- [Stooq archive](https://stooq.com/db/h/)

## Reproduce

```sh
cd ~/Projects/trading-desk-public-main
python3 research/backtest/backtest.py --variant base
open research/backtest/out/report.html
python3 -m unittest discover -s research/backtest
python3 -m unittest discover -s tests
```
