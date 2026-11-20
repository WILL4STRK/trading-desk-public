# Memecoin backtest: the five pre-registered rules on GeckoTerminal candles

Run 2026-11-20, once, by the rules in [docs/MEME.md](../../docs/MEME.md) sections 2, 3 and 5 (committed before any
result). `python3 -m research.meme.backtest dev` shows the development half; `holdout` scored the held-out half and
wrote `out/results.json` and `out/holdout.lock` (a second holdout run is refused).

## What was tested

- **Coins:** the 469 on DexScreener's discovery feeds on 2026-11-20 (boosts, profiles, takeovers, ads, trending
  narratives; Solana, Base, BSC, Ethereum). Candles were downloaded for 288 before Q stopped the run. 281 had 5-minute
  candles, 7 had none, and 181 were never fetched; the download order had nothing to do with how a coin did. 14 pools
  were dead (liquidity under $1,000) and count as total losses.
- **Window:** entries 2026-11-13 18:00 to 2026-11-20 19:15 UTC. Development half to 2026-11-17 23:33, held-out half
  after. 119,729 five-minute bars, 1,043 trades.
- **Rules and exits:** decision points every 15 minutes, buy at the next bar's open, stop -15 %, target +30 %, 4 hours,
  stop first when a bar touches both, 3.5 % round-trip cost. P5 and P6 (aged and liquid, news) need data the
  candles do not have, so they were not in this test.

## Results (net after costs, per trade)

| rule | dev trades / coins | dev average | held-out trades / coins | held-out average | held-out middle |
|---|---|---|---|---|---|
| buy everything | 380 / 53 | -4.30 % | 261 / 64 | **-4.88 %** | -5.51 % |
| hype (old memebot) | 120 / 31 | -5.24 % | 59 / 28 | -5.93 % | -5.85 % |
| breakout (stocks rule) | 67 / 37 | -4.68 % | 34 / 25 | -7.22 % | -7.73 % |
| calm, 1-7 days | 33 / 6 | -8.51 % | 11 / 2 | -15.32 % | -17.92 % |
| dip, 1-7 days | 34 / 6 | +0.06 % | 14 / 3 | -0.47 % | -13.12 % |

Buying everything lost in all four time slices (-4.0, -4.8, -3.8, -5.2 %). Only 15 % of its held-out trades made money.

**The pick** (best development average among P1 to P4) was dip, 1-7 days. **It did not pass**: held-out average and
middle below 0, below 0 without its 3 best trades, 14 trades on 3 coins (100 on 50 needed), positive in 2 of 4 slices.
It beat buying everything, the one condition it met. Its development edge came from 6 coins, one of them 41 % of the
trades.

## How much to trust it

- **Survivorship works in the rules' favour.** Only coins still listed today could be fetched, so coins that rugged and
  vanished are missing. Real results would be worse. A loss here is meaningful; a gain would not have been.
- One week, one market mood, part of the universe (288 of 469 coins). The direction agrees with everything else:
  the old memebot's 46,367 snapshots, the forward record on the desk (buy everything about -12 % on its first 26
  trades), and the research (no public rule survives costs out of sample, docs/MEME-RESEARCH.md).

## Conclusion

No entry rule has an edge after costs on this data. The hype and breakout rules lose more than buying at random. The
least-bad setup the evidence supports is filtering, not picking: the desk's main memecoin account now trades in
**careful** mode (any rule, only coins that pass the strict safety check and none of the research filters), which
will trade rarely. It stays a paper experiment.
