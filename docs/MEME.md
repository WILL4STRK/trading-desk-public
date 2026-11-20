# Model 4: memecoins (paper only)

Written 2026-11-20, **before any test below was run**. It moves the memebot handoff (cloud session, 2026-11-20)
onto the desk. The old memebot's code and its 3 hours of snapshots did not survive (the cloud container was
reclaimed and nothing was pushed), so this is a rebuild from the handoff's description, with the same process the
stocks models use: rules written down first, a sealed held-out test, its own practice account, a tab, a review
section, and no real orders ever.

Nothing here is advice. Paper only. The desk has no wallet, no keys and no code path to a real swap.

## 0. What is already known (from the handoff)

- 7 of 7 live paper trades lost ($100 to about $94.7).
- On 46,367 recorded snapshots the mean forward 15-minute return was **-1.08 % before costs**, and every feature
  bucket was negative. Hype was *inversely* predictive: hype score >= 7 gave -14.5 %, a 1-hour gain over 100 %
  gave -65.4 %, pairs under 1 hour old gave -73.3 %. Only pairs 24 to 168 hours old came close to flat (+0.28 %).
- Published work (the handoff's Codex review: MELT v2, Early-Buyer Groups, the Kamat bot, the meme_traderv1 and
  MOAI postmortems) shows no validated positive-return entry rule for this population after about 1.5 % cost.
  Real round-trip cost was measured at about 4 % (meme_traderv1, 0.25 SOL across 40 pools).
- One correction to the request: memecoins are far *more* volatile than stocks, not less. A 15 % move in an hour
  is ordinary for them.

So the goal is honest measurement: a risk screen that keeps the account out of rugs and honeypots, a forward
record of every candidate, and a model that trades only what a held-out test supports. "No edge" is an allowed
answer and, on the evidence so far, the likely one.

## 1. Data

| source | what | limit | key |
|---|---|---|---|
| DexScreener `api.dexscreener.com` | discovery feeds (boosts latest/top, profiles latest/updates, community takeovers, ads, trending metas) and `/tokens/v1/{chain}/{addrs}` snapshots | 60/min feeds, 300/min tokens | none |
| GeckoTerminal `api.geckoterminal.com/api/v2` | 5-minute OHLCV per pool, back at least 60 days (checked 2026-11-20) | 30/min | none |
| RugCheck `api.rugcheck.xyz/v1/tokens/{mint}/report/summary` | Solana: `score_normalised` (0 safe to 100 risky), `risks[]` with `level` (`warn`/`danger`), `lpLockedPct` | about 1/s | none |
| GoPlus `api.gopluslabs.io/api/v1/token_security/{chain_id}` | BSC 56, Base 8453, Ethereum 1: honeypot, taxes, sell limits, open source, owner and holder shares | 30/min | none |

Chains: solana, base, bsc, ethereum.

**Survivorship.** GeckoTerminal's new-pool list reaches back only minutes (10 pages), so a historical universe can
only be built from tokens that are listed *today*. Tokens that rugged and vanished are under-represented. A
backtest on that universe is biased upward, so it **can rule a policy out but cannot confirm one**. The honest
universe is the forward discovery log (section 4), which records every token at first sighting.

## 2. Costs (fixed)

- **3.5 % round trip** by default (`meme_cost_pct`), charged half on the buy price and half on the sell price:
  buy fill = price x (1 + 0.0175), sell fill = price x (1 - 0.0175). Break-even therefore needs a gross gain of
  about +3.56 %. The forward account adds price impact on each side: trade size / (pool liquidity / 2).
- A stop fills at the stop, or at the bar's open if the bar opened below it (a gap). If a bar touches both the
  stop and the target, the **stop is assumed first**.
- A pool whose liquidity has fallen under $1,000, or which has no price at all when the trade must close, is a
  **total loss** (-100 %). Missing data is never dropped from the sample.

## 3. The policies (fixed before any test)

Features at a decision time t, from 5-minute candles in the backtest and from the DexScreener snapshot forward:
`age` (since the pool was created), `r5` (last 5 minutes), `r1h`, `r6h`, `r24h` (price change, %),
`v1h` (USD volume in the last hour), `vacc` = (5-minute volume x 12) / v1h, `rvol` = v1h / (v24h / 24).

| # | name | buys when | why it is here |
|---|---|---|---|
| P0 | `all` | v1h >= $20k (any token) | the drift: what buying at random costs |
| P1 | `hype` | age >= 30 min, 0.5 % <= r5 <= 25 %, 0 % <= r1h <= 150 %, vacc >= 1.1, v1h >= $20k (forward also: 5-min buys / (buys + sells) >= 0.55 with >= 20 trades) | the old memebot rule; expected to lose |
| P2 | `breakout` | the stocks desk's rule moved over: rvol >= 1.5 (in play), price above the high of the 6 hours before the last hour, r1h <= 100 %, age >= 6 h, v1h >= $20k | Q asked for the stocks strategy; tested, not assumed |
| P3 | `calm_aged` | 24 h <= age <= 7 d, -10 % <= r1h <= +10 %, r6h > -30 %, r24h < +100 %, v1h >= $20k | the only bucket the old data did not condemn; avoids hype on purpose |
| P4 | `dip_aged` | 24 h <= age <= 7 d, r1h <= -10 %, r6h > -50 %, v1h >= $20k | the handoff's "aged dip"; its earlier +10.9 % was one token, so tested fresh |

Forward only: each policy is also logged with the **safety screen** (`+screen`). A screen passes only if every
check has data: Solana: RugCheck `score_normalised` <= 30 and no `danger` risk; EVM chains: GoPlus says not a
honeypot, buy and sell tax <= 10 %, `cannot_sell_all` is not 1, and the contract is open source. Anything missing
is `unknown`, and **unknown is never a pass**.

**Decision points.** One every 15 minutes per token (every third 5-minute bar). After a policy buys a token it
cannot buy that token again until 60 minutes after the exit.

**Exits (primary, the same for every policy).** Stop -15 %, target +30 %, time stop 4 hours, all from the buy
price before costs. Secondary, reported but not used to choose: fixed exits at +15, +60 and +240 minutes.

## 4. The forward record (desk/meme.py)

While the desk runs, every 60 seconds (crypto does not close, so this ignores NYSE hours):

1. Read the discovery feeds (every 5 minutes) and snapshot every tracked token (`/tokens/v1`, 30 per call).
2. On a token's first sighting, write a **discovery** line: time, chain, token, pool, feed tags, snapshot. Queue
   its safety screen.
3. At each decision point, run every policy and write a **shadow** line for each one that buys, screened or not,
   traded or not.
4. Close shadows and paper trades on the bracket from the desk's own 60-second price samples. Anything left open
   while the desk was off is replayed from GeckoTerminal 5-minute candles when it comes back.

How the forward record reads the data (settled while building it, before any forward result existed):

- A pump.fun bonding-curve pool has no liquidity figure on DexScreener. Missing liquidity is **unknown**, not $0, so
  it is never counted as a rug. The record may log such a coin; the paper account does not buy it (no pool size means
  no price impact). Once the coin graduates, its tracked pool moves to the real one.
- A coin whose pool already reports under $1,000 is not logged as a new buy for any rule: it is dead before the buy.

The **paper account** ($100 by default, its own folder `data/accounts/meme/`) trades one policy with the screen:
20 % of equity a position, at most 4 open, at most one position per token. Which policy, and whether it is labelled
"passed" or "did not pass", comes from section 5.

**Learning mode (Q's decision, 2026-11-20, the default).** Q wants the paper account to trade often so it learns, as
with the stocks paper bot. In learning mode it buys when any of P1 to P4 fires on a coin that passed the screen.
This changes only the practice account: every policy's own forward record is kept and scored exactly as above, and
the pass bar is then read on the account's own closed trades. `meme_policy: auto` goes back to the backtest's pick.

## 5. The test and the pass bar

Backtest (`research/meme/`): 5-minute GeckoTerminal candles for every token on the discovery feeds when it runs,
about 7 days back. Chronological split: the first 60 % of the window is **dev**, the last 40 % is the **holdout**,
looked at once. Results by distinct token as well as by trade.

The paper account's policy is the best of P1 to P4 on **dev** mean net return per trade (primary exits). It
**passes** only if, on the holdout:

- mean **and** median net return per trade are above 0;
- the mean is still above 0 with its 3 best trades removed;
- at least 100 trades on at least 50 distinct tokens;
- the mean is positive in at least 3 of 4 equal time folds of the whole window;
- and it beats P0 (`all`) on the same holdout.

Because of survivorship (section 1), a backtest pass only earns the policy a forward test. Before any real money
could even be discussed, the **forward** record must meet the same bar on at least 100 distinct-token trades.
If nothing passes, the account still trades the dev-best policy as a labelled forward experiment, the way model 3
runs, and the tab says so.

Changing any number in sections 2, 3 or 5 after a result is seen restarts the count and is logged in
docs/CHANGES.md.

## 6. Additions after the deep research (written 2026-11-20, before any of their data)

Source: [docs/MEME-RESEARCH.md](MEME-RESEARCH.md). Nothing in sections 2 to 5 changes. These are new measured variants
on the forward record only (the backtest cannot see liquidity, boosts, holders or news in the past).

**Two new policies** (forward only; both join learning mode's "any rule"):

| # | name | buys when | why |
|---|---|---|---|
| P5 | `aged_liquid` | 72 h <= age <= 14 d, liquidity >= $50k, liquidity / market cap >= 0.10, v24 >= $50k, -15 % <= r24h <= +25 %, -5 % <= r1h <= +5 %, no active boost | research R4: the least-bad bucket; expected about minus the cost |
| P6 | `news` | the coin's symbol or name matches a word from today's news (the desk's catalyst headlines and Q's "Events to watch" lines) or the coin came from a trending DexScreener narrative; age >= 1 h; v1h >= $20k; -10 % <= r1h <= +50 % | Q asked (news like the GTA 6 launch); the stocks desk uses catalysts. No study supports it for memecoins: a measured hypothesis |

**Evidence filters (vetoes, research R1 to R3)**, recorded on every shadow, never blocking the record:
R1 age under 24 h; R2 an active paid boost, or seen on the boost or ad feeds in the last 24 h; R3 rvol >= 5 with
|r1h| < 5 % (flat volume burst, a wash-trading sign).

**Strict safety check (research S1 to S3)**, recorded beside the plain check: Solana: RugCheck full report, top-10
holders (leaving out pools and known accounts) <= 30 %, creator <= 5 %, the traded pool's LP locked or burned >= 90 %.
EVM: creator + owner share <= 5 %, top-10 holders (leaving out contracts, locked and dead addresses) <= 30 %, no owner
powers (mint, hidden owner, take back ownership, change balances, pause, slippage change, blacklist, cooldown, proxy,
external call) unless ownership is renounced, `honeypot_with_same_creator` 0; Uniswap v3/v4 pools skip the LP part
(GoPlus cannot see NFT locks). Missing data is unknown and fails. S4 (trade flow) is left out: it needs GeckoTerminal
calls the desk cannot spare.

**Cost by chain:** Ethereum gas makes a $20 round trip about 5 to 7 %, so Ethereum is charged 6 % instead of 3.5 %.

**Reading it, the stocks desk's way:** each variant's forward line also shows a 95 % range for its average (bootstrap
that resamples whole coins, so one coin's many trades move together) and a Wilson 95 % range for its win rate. The
pass bar in section 5 is unchanged. With 7 rules x 4 variants, about 1 in 20 will look good by luck at 95 %, so a
variant that clears the bar is only a candidate for its own fresh forward test.

## 7. More practice accounts (Q, 2026-11-20: "run as many models as you need, more than one account")

Besides the main learning account, each of the 7 rules trades its own $100 practice account, only its own buys, on
coins that passed the safety check, with the same size, limits, costs and exits. One more, **careful**, trades any of
the six rules but only coins that pass the strict check and none of the research filters (the research's best guess).
They change nothing in the forward record or the pass bar; they turn each rule's shadow line into a balance Q can read.
Every account being above its start at one moment is shown, but it is not the finish line: with 9 accounts that can
happen by luck. The loop still ends only on the pass bar (section 5) or a clear no-edge result.

## 8. This desk's copy (2026-11-20, before any of its forward data)

Installed on WILL4STRK/trading-desk-public from the model kit. Rules, numbers, exits and costs in sections 2, 3 and 6
are unchanged (`desk/memerules.py` is the kit's file as is). What differs, and why:

- **Time slices in the pass bar.** Section 5 asks for a positive mean in at least 3 of 4 equal time slices. The kit left
  that check for later; here `meme.stats()` reports it for every forward line and `bar()` and `verdict()` require it.
  The tab shows it as "Slices up".
- **One more discovery source** (`meme_search_new`, on by default): every feed pass also asks DexScreener's search for
  3 of 12 rotating words and keeps pools under 24 hours old (tag `search-new`). The feeds alone found about 15 new coins
  an hour in the meambot loop. It widens the universe only: every rule, check and exit is the same.
- **News words come from a setting** (`meme_news_words`), because this desk has no catalyst journal. Trending
  DexScreener narratives still count as news.
- **The tab** is built from this desk's own styles with an inline SVG candle chart (no chart library), and it holds the
  meambot research loop's status at the bottom (the old Memebot tab). There is no Graph tab or money table here, so the
  account is not mirrored into a journal.
- Files are read and written as UTF-8 so coin names in any script work on Windows.
