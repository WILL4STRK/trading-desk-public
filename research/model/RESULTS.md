# Model lab: can a long/short model beat SPY?

## Summary

1. **Nothing beat SPY on the sealed holdout** (2021-01-04 to 2026-11-18, 5.9 years). SPY returned 13.49%/yr (total return) with a −24.5% max drawdown.
2. I tried 326 configs on 2000–2020 data only, and the holdout bars were never loaded during that search. Eight finalists were picked by fixed rules, then sealed, then run once.
3. Best return: **QQQ buy-and-hold, 14.07%/yr**, +0.58/yr over SPY. It was a control, not a model, and its lead is not significant (95% CI of daily excess −0.022% to +0.034%).
4. Best risk-adjusted model, which also uses shorting: **`rsi base=1.0 dip_w=1.5 lo=10 short=True`**, 11.69%/yr.
   - Sharpe 0.88 vs SPY 0.84; max drawdown −19.8% vs −24.5%.
   - In bull markets it holds SPY and buys dips at 1.5×. In bear markets it shorts SPY only for a few days after a sharp bounce.
   - It still trailed SPY by 1.8%/yr, and the gap is not significant.
5. The primary finalist (dual momentum across SPY/QQQ/IWM/MDY, bonds when equities lag) made 11.58%/yr with a −21.8% drawdown. That was −1.91/yr vs SPY.
6. Picking a new "best" each year (walk-forward) was worst: 2.72%/yr. Chasing last period's winner fails.
7. In development, no config passed the multiple-testing (deflated Sharpe) test. The best was p=0.81, where 0.95 would be convincing.
8. **Verdict:** none of these models beats SPY in a way worth trusting, so the desk should not change. If you want SPY-like growth, buy-and-hold SPY is the honest benchmark to beat. The RSI dip-buy/rip-short model is the one candidate worth paper trading forward. It matched SPY's risk-adjusted return with smaller crashes, but it gave up return.

## Holdout (sealed, opened once)

$400 start, base costs (3 bps per side, 1%/yr borrow on shorts, T-bill + 5%/yr on margin). Total returns, dividends included.

| finalist (rule that picked it) | CAGR % | vs SPY | Sharpe | max DD % | CAGR at 0× / 3× costs |
| --- | --- | --- | --- | --- | --- |
| SPY buy-and-hold (benchmark) | 13.49 | — | 0.84 | −24.5 | — |
| QQQ buy-and-hold (control) | 14.07 | +0.58 | 0.70 | −35.1 | 14.07 / 14.06 |
| R6 best shorting config: rsi dip/rip | 11.69 | −1.80 | 0.88 | −19.8 | 12.33 / 10.42 |
| R1 primary, best dev excess without margin: dual momentum L=63, bonds | 11.58 | −1.91 | 0.68 | −21.8 | 12.04 / 10.66 |
| R3 same with 1.5× margin | 10.89 | −2.60 | 0.52 | −32.3 | 11.54 / 9.59 |
| R2 best deflated Sharpe: SPY 200-day trend, IEF when below | 10.05 | −3.44 | 0.82 | −26.3 | 10.17 / 9.82 |
| R7 best blend (trend + dual + sector) | 7.85 | −5.64 | 0.58 | −25.6 | 8.13 / 7.28 |
| R4 best worst-period: QQQ 200-day trend, TLT when below | 7.49 | −6.00 | 0.48 | −44.3 | 7.57 / 7.33 |
| R5 walk-forward, best trailing 5y Sharpe | 2.72 | −10.77 | 0.25 | −28.1 | — |

Full numbers, with block-bootstrap CIs and p-values, are in `out/final.json` and `out/final.md`, and the charts are in `out/report.html`.

## Development (2000-01-03 to 2020-12-31)

SPY returned 6.49%/yr here, with a −55% drawdown. All 326 configs are in `out/dev_configs.csv`, with excess return per period: 2000–06, 2007–13 and 2014–20.

- Almost every edge came from **avoiding the 2000–02 and 2008 crashes**. In 2014–2020, nearly every model lost to SPY. The few that didn't used QQQ, and picking QQQ is hindsight: we know now that tech won.
- The holdout looked like 2014–2020, mostly a bull market with one sharp bear year (2022). Crash insurance then costs more than it saves.
- **Bonds as the "off" asset failed in 2022**, when stocks and bonds fell together. The "safe" switch fixes that: it holds bonds or gold only when they beat T-bills. It did not raise development returns, though.

## What shorting taught

- **Trend-shorting the index loses.** Shorting SPY whenever it is below its 200-day average made +15%/yr vs SPY in 2000–06, then −19%/yr in 2014–20. Bear markets have the sharpest rallies, and a short squeeze in a downtrend is what hurts.
- **Short-term rip-shorting works better.** The best model shorts only when the market is in a downtrend *and* has just bounced hard (2-day RSI above 90). It holds the short for a few days, until the price drops back under its 5-day average. Holding periods are short, so a squeeze has little time to hurt. This was the best risk-adjusted finalist.
- **Long/short sector overlays** (long the strongest sectors, short the weakest) did not help in development. Sector momentum reverses too often.
- **What shorting needs in real life** (see `docs/SHORTING.md` in this repo):
  - a margin account with at least $2,000;
  - a borrow fee, from about 0.25–1%/yr on liquid ETFs to much more on hot stocks;
  - unlimited loss risk, plus gap risk over your stop;
  - the SEC Rule 201 restriction after a 10% drop.
- **A cash account cannot short.** It can buy inverse ETFs (SH for −1× SPY, PSQ for −1× QQQ). They reset daily, so they decay over long holds. They were tested as an "off" asset here and were not better than cash or bonds.

## Honest limits

- **I am not blind to 2021–2026.** My training includes broad market history up to mid-2026: the 2022 bear market, the 2023–25 tech rally. The code never loaded those bars, and finalists were picked by fixed rules, not by me. Even so, the choice of what to test (QQQ, bonds) may carry hindsight. Treat the holdout as close to blind, not fully blind.
- **Choices:**
  - The 326 configs are a modest family: trend, dual momentum, sector rotation, vol targeting, RSI mean reversion and blends.
  - Round 2 was added after reading round-1 development results, and it is counted in the trial total.
  - The universe is ETFs only, with no single stocks.
- **Fills:** daily bars, fills at the next open with fixed costs. There are no partial fills.
- **Fractional shares:** with $400, models that split money across several ETFs need fractional shares. Brokers usually don't allow fractional shorts.
- **Taxes:** not modelled. Frequent switching creates short-term gains, which taxable buy-and-hold avoids. That favours SPY further.
- **Cash:** earns 0%; SHY, the short-Treasury ETF, is the only cash yield. A money-market fund would add about 1–5%/yr in the years models sat in cash, which favours the models slightly.
- **Holdout length:** 5.9 years is one market regime. The CIs above are wide: none of the gaps vs SPY is statistically clear, either way.

## Reproduce

```sh
cd ~/Projects/trading-desk-public-main
python3 research/model/history.py        # free daily data, cached (already done)
python3 research/model/lab.py            # development search, 2000-2020 only
open research/model/out/report.html      # holdout charts
python3 -m unittest discover -s research/model
```

`lab.py --final` refuses to run again: the holdout has been opened once (`out/final.json`). It also refuses if code or data changed after sealing (`out/selection.json`).
