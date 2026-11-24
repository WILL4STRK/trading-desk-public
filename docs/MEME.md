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

From 2026-11-21 P6's news words come from the daily topic checker (C:\Trading\topics, history.jsonl); P6 results before and after this date are reported separately.

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

## 9. Rule P7: buy after a launchpad migration (pre-registered 2026-11-21, before any of its data)

Source: test C in [WILL4STRK/meambot `research/meme/OTHER-METHODS.md`](https://github.com/WILL4STRK/meambot/blob/main/research/meme/OTHER-METHODS.md)
(commit 2914c2f). Nothing in sections 2 to 8 changes. P7 is forward only: the backtest cannot see buyers or
migrations in the past. The code lives in `desk/memigrate.py` (pure rules) and `desk/meme.py` (the plumbing).

**The event.** A Solana coin launched on pump.fun (mint ends in `pump`) or LetsBonk (mint ends in `bonk`) whose
tracked DexScreener pool is no longer the launchpad's own curve (`dexId` not `pumpfun` or `launchlab`). The
**anchor** is that pool's `pairCreatedAt`: the migration. One decision per mint, taken by the first run between
**15 and 17 minutes** after the anchor. A coin first seen later, or a run that misses the window, is logged as
attrition and never caught up. *Data-fetch note, 2026-11-21 (no rule change):* when
GeckoTerminal's trade list cannot be fetched (a 429 cool-down shared with other programs on the same internet
address, or no request budget left in the run), the decision is retried on the next run while the window is still
open, instead of being taken on missing data; a graduate still without data at minute 17 is logged as missed with the
reason. Every 429 is logged in `api_errors.jsonl`. Only a coin the desk first saw within an hour of its migration counts as a missed
graduate; coins that graduated earlier than that are outside the study and are not logged (added 2026-11-21, after a
dry run in a scratch folder and before any recorded data).

**Two lines, decided at the same moment:**

| # | name | buys when | why |
|---|---|---|---|
| P7 | `migration` | pool liquidity >= $50k; pool volume in the last hour >= $20k; >= 25 distinct buying wallets since the anchor (the creator left out when RugCheck names one); price now between 90 % and 120 % of the first traded price after the anchor | test C: stabilized flow after graduation |
| P7b | `migration_base` | the same coin, liquidity and volume floors only (no buyer or price test) | test C's control: every graduate that passes the same safety and volume bar at minute 15 |

Both require the **strict** safety check to pass (unknown fails) and no R2 (paid promotion) or R3 (flat volume
burst) veto. R1 (under 24 h old) is waived: every graduate is young by definition.

**Data.** Liquidity, volume and price from the DexScreener snapshot. Buyers and the first traded price from
GeckoTerminal's last-300-trades list for the pool (`kind` = buy, `tx_from_address`). If that list does not reach
back to within 60 s of the anchor, the first price comes from GeckoTerminal's first 1-minute candle at or after the
anchor, and the buyer count is what the list shows (a lower bound). A missing field is a no, with the reason logged.

**Fill (the report's shared contract, stricter than sections 2 and 3).** The buy is taken at the first snapshot
price at least **60 s** after the decision and no later than **150 s** after it (the desk samples about once a
minute); otherwise it is cancelled and logged. Exits are the shared stop -15 %, target +30 % and 4 hours from the
fill price. Cost is the larger of 4 % and the Settings cost, round trip.

**Accounts.** Each line is recorded as shadow trades. P7 also trades its own practice account (desk sizing, $100
start). Neither changes the main or careful accounts.

**Pass bar.** Section 5's bar (with section 8's time slices), plus the report's extras: at least **10 trades in
each of the 4 time slices**, and P7 must beat **P7b** (its own control) as well as buy-everything. The report asks
for 120 days; the desk reads the bar continuously but a pass before 120 days is labelled early.

**Known differences from the report, decided now:** the hour's volume counts the new pool only (curve trades before
the migration are not in DexScreener's pool figure, so this is stricter); exits are not delayed a further 60 s (the
desk's once-a-minute sample already lags); sizing follows the desk, not $100 on $1,000 (the bar is per trade, so
size does not change it); the creator is excluded only when RugCheck names one. Every graduate the desk sees is
written to `migrations.jsonl` with the outcome and the reason, so attrition can be audited.

## 10. Rule P8: moonshot, ride young coins that are taking off (pre-registered 2026-11-21, before any of its data)

P8 and P8b decision lines and shadows record LP and pool-flow diagnostics for a future v2; these fields change nothing in v1.

The user asked for a bot whose goal is the coins that go up very far, very fast. Every rule above sells at +30 %, so
none of them can catch a 5x. P8 is the opposite bet: small positions on young coins that are accelerating, no
target, and a trailing stop that lets a winner run, so a few large winners have to pay for many losers. Section 0
already says hype was inversely predictive on average (coins under an hour old: -73 % in 15 minutes); P8 tests
whether the right tail is fat enough to pay for that. It is written before the missed-winners research (any rule
fitted to those winners would be hindsight) and before any of its own data. Code: `desk/memoon.py` (pure rules)
and `desk/meme.py` (plumbing).

**Decisions.** Every 5 minutes per tracked coin (the other rules use 15).

| # | name | buys when |
|---|---|---|
| P8 | `moonshot` | age 1 h to 48 h; price up 20 % to 150 % in the last hour and up over the last 5 minutes; 5-minute volume x 12 at least 1.5 x the last hour's volume; at least 50 trades in the last 5 minutes with buys >= 55 % of them; hour volume >= $50k; pool liquidity >= $25k |
| P8b | `moonshot_base` | the control: same age band, hour volume and liquidity floors, no momentum test |

Both need the **plain** safety check to pass (honeypot, taxes, RugCheck score; unknown fails). The strict check is
recorded but not required: young coins nearly always fail its holder-concentration part. R3 (flat volume burst,
a wash-trading sign) vetoes; R1 (age) is waived; R2 (paid promotion) is recorded on the shadow but does not veto,
so its effect can be read afterwards.

**Fill.** As section 9: the first price sample at least 60 s after the decision and no later than 150 s, else
cancelled. Cost: the larger of 4 % and the Settings cost, round trip (Ethereum 6 %), plus price impact.

**Exits (no target).** From the fill price:
- stop at -25 %;
- once the peak is +40 % or more, sell if the price falls 25 % below the peak;
- once the peak is +150 % or more, sell if the price falls 30 % below the peak;
- time stop 24 hours; liquidity falling under half of its level at the buy sells at once; a dead pool (under $1,000)
  is a total loss.
- *Correction, 2026-11-21, before any P8 trade had closed:* as first written, crossing +150 % loosened the stop (from
  25 % under a 2.5x peak, 1.875x, to 30 % under it, 1.75x). A trailing stop must never fall as the peak rises, so the
  level is now the higher of the current step and the level the step below reached at its top (from +150 %: the
  higher of 30 % under the peak and 1.875x). Flagged by research/meme/MOONSHOT-V2.md in WILL4STRK/meambot (6fec63d).
Forward samples are about a minute apart and a stop fills at the sampled price (a gap is real). When the desk was off,
5-minute candles are replayed: in each bar the stop level from the peak *before* the bar is checked against the low
first (filling at the bar's open if it opened below it), then the peak is updated from the high.

**Account.** P8 trades its own practice account ($100): **5 %** of the account a position, at most **10** open, one
per coin, 60 minutes before the same coin can be bought again. P8b is shadow-only.

**Pass bar.** Section 5's bar with section 8's time slices and section 9's 10 trades per slice, and P8 must beat P8b
and buy-everything. Because P8 is built for a skewed payoff, its **median is expected to be negative**; that check
stays (a rule that only works through its top three trades fails "still above 0 without the 3 best" anyway), so the
bar is not loosened for it. Report also its largest win, the share of trades above +100 %, and its result without
its best 3 and best 10 trades.

## 11. Main practice drawdown (prototype registration, 2026-11-21, before any of its data)

**Account.** Opt in with `meme_practice_drawdown: true` in the desk settings JSON. The companion starts with
`meme_starting_balance` on its first enabled pass. It uses the main practice account's new paper fills and
corresponding exits as its event tape. At entry it uses the desk's position percentage on its own marked
equity, capped by the main fill's dollar size and its own cash, with the same $1 minimum. Entry and exit charge
the desk's costs and price impact at the companion's own size. The companion uses the liquidity saved on the
main close for exit impact and falls back to entry liquidity when that field is missing or unknown. The main
book's slots, cooldown and execution timing define this prototype's opportunity set.

**Halt.** Realized equity is cash plus the entry cost of positions still open. Start the peak at starting
cash and update it on each matched close in the order the desk books them. At a realized loss of 15 % or more
from that peak, latch the halt before copying any new entries that pass. Already open positions close on
their reference exits. A later recovery or switching the setting off and on preserves the latch. Switching
off stops copying entries while still processing exits. The existing main-account reset or a changed starting
balance starts a new companion record. The review and `/api/meme` report the peak and halt state.

**Control and readout.** Compare with the simultaneous main practice account from a common empty-account start,
with settings frozen over the comparison. Read net ending equity, maximum realized drawdown and the count and
net result of matched and skipped opportunities. This account overlay is evaluated on its own ledger. It consumes
finalized paper events locally. Source inspiration: [ClaudeFi's
README](https://github.com/claudefi/claudefi/blob/e86dea3d3c5777d1145137c935de8e5487bcf0bf/README.md)
(MIT as declared there).

## 12. N1 shadow. Same-mint two-stop lockout (pre-registered 2026-11-22)

**Question.** Does skipping a repeat after two stops improve the net result of the same Careful fills?
FIONA's losses and later target motivated the test. All historical fills are outside the study.
[Meambot N1 and the source tape](https://github.com/WILL4STRK/Meambot/blob/b7e32f2a4cef7578006427e09a2c1f8f3d6b960f/research/models/NEW-IDEAS.md#n1--same-mint-two-stop-lockout)

**Opportunity and control.** Use the named `careful` book's actual new Solana practice fills from P1–P6,
with at least $25k pool liquidity and $20k trailing-hour pool volume at entry. The book supplies its existing
strict screen, vetoes, slots, 60-minute re-entry cooldown, fill clock, size, exits and cost model. Take every
eligible fill in the control. An accepted shadow copies that fill and its closed net P&L, including price
impact at the reference size. A blocked shadow stays in cash at zero P&L and retains the control's eventual
outcome. This is the paired part of N1, conditional on recorded Careful fills. Testing replacement trades or
independent size-aware quotes would require a separately registered study.

**Lock.** Key by chain and exact token mint, across rule and pool changes. Count only accepted shadow fills
that the reference book closes with reason `stop`, including gap fills. If the latest two accepted stops are
less than 24 hours apart by exit timestamp, block new fills until 24 hours after the second exit. An entry at
the expiry is eligible. Only a booked close can affect a decision. Process closes before entries on a pass
and record both exit time and the pass time, so candle catch-up cannot change an earlier decision. Exits
labelled `rug` or `liquidity pulled` do not count toward the two-stop lock. Neither do targets, time exits
or the control outcomes of blocked fills.

**Window and record.** Opt in with `meme_reentry_shadow: true` in Settings JSON. The first enabled pass sets
the start to the next midnight UTC. Enroll for 120 consecutive days in four fixed 30-day UTC slices, then
allow four hours and 150 seconds for final closes. Start with empty lock history. Freeze the desk source,
starting balance, size, slots, cost, chains and discovery setting. The ordinary daily topic feed is part of the
source protocol. Disabling the observer or changing a frozen setting before enrollment ends marks the window
incomplete and stops new enrollment. An observer error is recorded on the study and also marks it incomplete.
Retain unmatched fills as unresolved. Store inputs, lock decisions and
reference outcomes under `reentry_shadow_v1` in the same atomic `model.json` save. The observer makes zero
provider requests. Existing practice books and P0–P8 use their registered paths.

**Readout.** Score once after the window. Assign fills to slices by their entry time and use the observed
close order for realized drawdown. Report accepted and blocked counts, distinct mints, foregone winners,
avoided losses, net P&L for each arm, difference per eligible fill, and realized drawdown from the same starting
balance. Compute the paired 95% UTC-day block-bootstrap interval with mint-cluster sensitivity. Require 100
resolved accepted fills on at least 50 mints, ten accepted fills in each slice, and 30 resolved blocks on at
least 15 mints. A favorable result also needs the paired interval's lower bound above zero and drawdown no
worse than the control. An incomplete window, unresolved outcomes or too few observations is inconclusive.
A less negative result can show loss avoidance. Any entry-edge claim requires the separate N1 arms and their
full controls and pass bar.

## 15. Rule P9: quick, a tight bracket on P0's own entries (pre-registered 2026-11-22, before any of its data)

**Question.** P0 (`all`) entries on coins that passed the plain safety check lose on average with the section 3
exits (-15 % stop, +30 % target, 4 hours). Would the same entries do better with a short, tight bracket? Paper only.
This section is written and pushed before any P9 code or P9 data exists. Sections 2 to 8 are unchanged.

**Entry.** Exactly P0's decision: hour volume >= $20k, one decision point per coin every 15 minutes, on coins whose
plain safety check (`screen` verdict) is `pass`. Unknown is no. Same fill as P0: the snapshot price at the decision.
Same guards as P0: no buy of a coin whose pool already reports under $1,000, none while that coin's P9 shadow is open,
none inside the 60-minute cooldown that follows that coin's last P9 exit. Each entry also logs the pool's `dexId`
(`dex`) so results can be re-priced per venue later.

**Exits (from the buy price, before costs).** Target +10 %, stop -5 %, time limit 30 minutes. A pool whose liquidity
falls under $1,000, a missing price, or a drop of liquidity under half of its level at the buy (practice account) closes
as in section 2. Samples arrive about every 60 seconds, so a stop fills at the **sampled** price, which can be well
under -5 % (gaps are real); a target fills at the target, as a limit order would. When one sample or candle touches
both stop and target the **stop is first**. Order of checks on one sample: dead pool, stop, target, time, liquidity pull.
If the desk was away, an open item is replayed from 5-minute candles as in section 4 (a candle that opens below the
stop fills at its open).

**Cost.** The desk cost, 3.5 % round trip and 6 % on Ethereum (section 2 and 6), half on each side. The practice account
also pays price impact on each side (size / half the pool's liquidity), as P0's account does. The shadow line, like every
shadow line, is net of the round-trip cost only.

**Break-even win rate.** At 3.5 % cost a win nets about +6.2 % and a stop about -8.3 %, so a bracket needs about **57 %**
of trades to win to break even (on Ethereum's 6 % cost, about 75 %). The readout reports P9's win rate (net above 0) with
its 95 % range, and its target-hit rate, next to 57 %.

**Control.** The same rule's entries with P0's exits (-15 % / +30 % / 4 hours) are the existing `all+screen` line. P9
must beat it. Because P9 exits sooner and its cooldown starts at its own exit, its entry times are not identical to the
control's, so the two share the decision rule and the coins but not every entry. To keep the comparison fair, the control
and the buy-everything line (`all`, P0 with no screen) are both restricted to entries on or after the first P9 shadow.
Older P0 history is not used for the comparison.

**Practice account.** Its own $100 account (`quick`), desk sizing: 20 % of equity a position, at most 4 open, one
position per coin, the P0 entries above, the P9 exits and cost. A shadow is recorded for every entry whether or not the
account can take it. The account is a readable balance. The pass bar is read on the shadows.

**Pass bar.** Section 5 plus section 8's time slices, on P9's closed shadows: at least 100 trades on at least 50 distinct
coins; mean and median net return above 0; mean above 0 without its 3 best trades; positive mean in at least 3 of 4 equal
time slices of its own entries; **at least 10 trades in every slice**; and a mean above both the control and
buy-everything lines (entries since the first P9 shadow). The 95 % range of the mean, by whole coins, is reported.
The same rule applies to a pass as to every rule here: it earns a fresh forward test and nothing more.

**Changes.** Changing any number in this section after a result is seen restarts the count and is logged in
docs/CHANGES.md.

## 16. Rule F1: Chronos zero-shot forecast gate (pre-registered 2026-11-22, before any code, forecast or data)

**Question.** Does a pretrained time-series model (Amazon Chronos, zero-shot, no training on our data) pick a better set of
P0's own entries than P0 itself and than a naive "it kept rising" rule? Paper only. No wallet, key or order path. "No edge" is
an allowed and the likely answer (section 0).

**Model.** `chronos-forecasting` 2.3.2 in `C:\Trading\forecast-venv` (Python 3.13, torch 2.15.0 CPU, transformers 5.24.0, pandas
3.1.1, numpy 2.5.5; pins in `research/forecast/requirements.txt`). Checkpoint: `amazon/chronos-2`. Its Hugging Face commit hash
is written to `research/forecast/MODEL_REVISION` at the first download and never changed; a different checkpoint or revision is
a new rule. Zero-shot only: no fine-tuning, no fitting to dev or holdout, so dev is a smoke test of the code and cannot tune a number.

**Data.** The same 5-minute GeckoTerminal candles and discovery list as the section 5 backtest (`research/meme/`). Desk data
(`discoveries.jsonl`, `model.json`) is read only through a copy placed under `C:\Trading\research\forecast\input\`; the live
desk folder and `C:\Trading\memebot` are never opened by the code. GeckoTerminal: 5 calls a minute or fewer, one process.
Chronos sees only closes dated on or before the decision time.

**Decision points and floor (same as P0 plus a history floor).** One every 15 minutes per token. Eligible when `age` >= 24 h
and `v1h` >= $20k. The 24 h floor guarantees a full context. Same 60-minute re-buy cooldown after an exit as section 3.

**Entry F1.** Context = the last 288 five-minute closes (24 h). Forecast the next 48 steps (4 h). Buy when **both** hold:
the median forecast close at +4 h is at least **+8 %** above the last close, and the 20th-percentile forecast close at +4 h is at
least **-10 %** versus the last close. (+8 % is the 3.56 % break-even plus a 4.4-point margin; -10 % caps the forecast downside
inside the -15 % stop.) Both numbers are fixed here and not tuned.

**Exit and cost.** The section 3 primary exits (stop -15 %, target +30 %, time stop 4 h, all from the buy price before costs,
stop first when a bar touches both) and the section 2 costs (3.5 % round trip, half each side, plus price impact size / half the
pool liquidity per side; a pool under $1,000 or without a price is -100 %). Secondary fixed exits at +15, +60, +240 minutes
are reported only.

**Controls, on the same holdout and the same decision points.**
1. **P0** `all`: every eligible decision point buys (the section 5 control).
2. **C-persist**: buy when the token's own last-4-hour return is at least +8 % and its 1-hour return is >= 0. This is the naive
   forecast "what rose keeps rising" at the same threshold, so a Chronos win must beat plain momentum too.
Report also the share of eligible points F1 selects, and F1's result on the points it rejected.

**Test and pass bar (section 5, unchanged).** Chronological split of the window: first 60 % dev, last 40 % holdout, holdout
looked at once and locked with a lock file as in `research/meme/backtest.py`. F1 **passes** only if, on the holdout:
mean and median net return per trade are above 0; the mean is above 0 with its 3 best trades removed; at least 100 trades on
at least 50 distinct tokens (if the window cannot supply that, the result is reported as inconclusive, never as a pass);
the mean is positive in at least 3 of 4 equal time folds of the whole window; and F1 beats **both** P0 and C-persist on the
same holdout. Results by distinct token as well as by trade.

**Limits stated now.** The sample has the survivorship bias of section 1: a backtest can rule F1 out but cannot confirm it, and a
pass earns only a forward test (a shadow recorder logging F1 beside P0, no new account) that must meet the same bar on at least
100 distinct-token trades. The window is short (about 7 days of 5-minute candles), so one market regime. CPU inference is slow;
if the number of forecasts needed exceeds what finishes in a day, forecast every second decision point (every 30 minutes), a
choice made by calendar and not by results.

**Changes.** Changing any number in this section after a result is seen restarts the count and is logged in docs/CHANGES.md.

### 16.1 Amendment, 2026-11-22 (after review, before any F1 code, forecast or data)

No F1 code, no forecast, no model download and no desk-data copy exists yet; the only files on this branch are this section and `research/forecast/requirements.txt`. This amendment fixes ambiguities only. It changes no threshold, exit, cost, control or pass-bar number of section 16, and it was written without seeing any F1, P0 or C-persist result. It also corrects two Windows paths above that had been saved with control characters (they now read `C:\Trading\forecast-venv` and `C:\Trading\research\forecast\input\`).

1. **Input cutoff.** GeckoTerminal stamps a bar by its open time. A bar is usable at decision time `t` only if its close time (bar timestamp + 300 s) is at or before `t`. Decision times are the same as `research/meme/backtest.py`: `t` is a bar's close time that falls on the 15-minute grid. No bar that closes after `t` reaches Chronos or any feature.
2. **Fill.** An entry fills at the **open of the first bar after the input window** (the bar that opens at `t`), with the section 2 cost (`net_pct`, 3.5 % round trip, half each side). This is the rule `backtest.py` uses (`bars[i + 1]` open), and it applies unchanged to F1, P0 and C-persist.
3. **Gaps and eligibility.** The 288 context closes are placed on a regular 5-minute grid ending at the last usable bar. GeckoTerminal omits bars with no trades, so a missing step takes the previous close (forward fill). A decision point is **ineligible** if more than 20 % of its 288 steps (more than 57) were filled, or if the first step has no earlier close to fill from. The same eligibility applies to all three arms, so P0 and C-persist in this study are "P0 and C-persist on F1-eligible points", not the section 5 P0. The age >= 24 h and `v1h` >= $20k floors still apply.
4. **Liquidity and dead pools.** The backtest uses what `backtest.py` uses for P0: one liquidity figure per token, `liq_usd` of its highest-liquidity pool when `research/meme/data.py` fetched it, and the token's `dead` flag (`liq_usd` under $1,000, a missing figure also counted as dead, as `data.py` stores it). The same flag applies to every arm. The backtest charges no size-dependent price impact (`net_pct` is flat 3.5 %) for any arm. The missing-liquidity-treated-as-alive sensitivity from `backtest.py` is reported, not used to decide. The forward recorder, if one is ever built, adds the section 2 price impact.
5. **One position per token per arm.** No new buy of a token by an arm while that arm's position in it is open; a position still open at the end of the data blocks that arm for the rest of the data, and the section 3 60-minute cooldown after an exit stays. Arms do not block each other.
6. **15 versus 30 minute decision points.** The default is 15 minutes (as for every rule). The choice of 30 is allowed only by a rule that uses no price result: after the model is downloaded and the desk-data copy exists, a timing run on **synthetic series** (50 random walks of 288 steps, batch of one, 48-step horizon, on this PC's CPU) gives the median seconds per forecast `s`. The estimated number of forecasts is `N = (tokens in the input copy that are at least 24 h old) x 7 days x 96`, using only a token count. If `s x N` is 12 hours or less, decide every 15 minutes; otherwise every 30 minutes (the `:00` and `:30` grid points) for all three arms. The numbers `s`, `N` and the choice are committed to `research/forecast/TIMING-NOTE.md` with its date **before** any real price series is forecast.
7. **Order of work.** After sign-off: (a) the first model download, writing `research/forecast/MODEL_REVISION`; (b) the copy of desk data to `C:\Trading\research\forecast\input\`; (c) the timing run and its note; (d) code and tests; (e) the real run. GeckoTerminal stays at 5 calls a minute or fewer, one process. No merge and no deploy.

### 16.2 Amendment, 2026-11-22 (universe correction, before any forecast and before the code change)

No forecast has run: no Chronos call on any real series, no dev run, no holdout run, no result of any arm has been computed (the forecast cache folder does not exist). Only synthetic series were forecast, for the timing note.

**Error found in review.** The candle fetcher (`research/forecast/data.py`, commit `b6af716` and earlier) selected tokens with a rule that is not in section 16 or 16.1: a maximum liquidity over **all** sightings of at least $25,000. That figure includes sightings after any decision point, so it keeps tokens that stayed alive and drops tokens that fell below $25k, a survivorship bias that would lift every arm's absolute mean. The section 5 universe (`research/meme/data.py`) has no such filter. The rule is withdrawn.

**The universe, exactly.** Every token in the copied `discoveries.jsonl` whose **first sighting** has `age_h` >= 24 and which has a pool address, and nothing else: **no liquidity filter** in the selection. Each token's pool is the one on its first sighting. Eligibility of a decision point is still decided by section 16 and 16.1 (age >= 24 h at the point, `v1h` >= $20k from the bars, at most 20 % filled steps), the same for F1, P0 and C-persist. A pool that has died is handled by the 16.1 item 4 dead flag (liquidity under $1,000, missing counts as dead), the same for all arms. For this study the flag is read from the token's **last sighting** in the copy, the nearest equivalent of the fetch-time figure `research/meme/data.py` stores. Like section 5, this uses later information to mark a dead pool, which treats every arm alike and is stated here as a limit.

**Data already fetched.** 32 token candle files were downloaded under the withdrawn rule; they are a subset of the 442-token universe, are kept (a token's candles do not depend on the rule), and nothing was computed from them. The remaining tokens are fetched at 4 GeckoTerminal calls a minute or fewer, one process. The timing note stands: its N already used the 442 tokens of this universe.

Nothing else in section 16 or 16.1 changes. A number changed after a result is seen would restart the count (docs/CHANGES.md).

## 17. D1: downside vetoes on P0's own entries (pre-registered 2026-11-22, before any code and before any outcome was looked at)

**Why.** Both Chronos studies (section 16 and the Meambot F1 holdout) found the model picking coins that had just run up, and those hit the
stop early (on the Meambot F1 holdout, 19 of 32 kept trades stopped within the first hour). D1 tests that directly, without Chronos: would a
simple "do not buy a coin that just ran up, or whose last 5 minutes were mostly selling" rule have avoided downside on P0's own entries?
Paper only. Nothing here changes any running rule.

**What was seen before this section.** Only counts of the copied `shadows.jsonl` (1,456 rows; 714 with policy `all` and screen `pass` on 335
distinct coins over about 2.3 days; none open; 148 of the 714 without an entry liquidity figure; 16 without `buy_ratio_m5`) and its field names.
No return, exit reason, feature value, veto count or outcome of any shadow was looked at.

**Population (the forward window).** Shadows in `data/accounts/meme/shadows.jsonl` with `policy == "all"` and `screen == "pass"` whose entry
time `ts` is **after the author time of the commit that adds this section** (read from git by `d1.py --since`) and before that time plus
**7 days**. Every such shadow is one signal; a coin may give several. The read-out waits until 4 hours and 150 seconds after the window ends so
shadows can close; a signal still open then is unresolved, excluded and counted, and more than 2 % unresolved makes the result inconclusive.
The desk source for P0, the screen and the shadow exits and costs (sections 2 and 3) is frozen for the window: a change logged in
docs/CHANGES.md during it marks the window incomplete (inconclusive). Shadows use the desk's section 3 exits (stop -15 %, target +30 %, 4 h time
stop) and the section 2 cost as recorded in `net_pct`; D1 does not re-price anything.

**Vetoes, each tested on its own (fixed, not tuned).** Fields are those the desk writes: `f.r1h` (percent change over the last hour at entry) and
`f.buy_ratio_m5` (buys / (buys + sells) over 5 minutes at entry).
- **V1 run-up:** skip the signal if `f.r1h` >= **+5**.
- **V2 sell pressure:** skip the signal if `f.buy_ratio_m5` < **0.50**.
- A missing (null or absent) feature means **no veto**.

**Outcomes.**
- (a) **Net return per original signal:** a kept signal contributes its shadow's `net_pct`; a vetoed signal contributes **0**. The mean is over all
  original signals.
- (b) **Severe-loss rate:** a signal is severe when its shadow exited with `why == "stop"` within 60 minutes of `ts` (`exit_ts - ts` <= 3600), or
  `net_pct` <= -15. The rate is severe kept signals over all original signals (a vetoed signal cannot lose).

**Control: a random veto, same skip count in every stratum.** Strata = chain x entry-liquidity tercile x 6-hour UTC entry block
(`floor(ts / 21600)`). Entry liquidity is the shadow's own `f.liq`; the terciles are the 33rd and 67th percentile of `f.liq` over the window's
signals with a liquidity figure; a signal with a missing or zero figure goes to its own "unknown" liquidity stratum. In each stratum the random veto
skips exactly as many signals as the real veto skipped there, chosen at random. **1,000 draws, fixed seed 17.** The random veto's severe-loss rate
and its spread of gains come from the 1,000 draws. For the bootstrap the random veto's expected return is computed exactly (the average over all
possible draws within each stratum is the stratum's mean return times its kept count), which equals the limit of infinite draws.

**Gain.** Per-signal gain of a veto = (mean return per original signal with the real veto) - (the random veto's expected mean return per original
signal). It measures what the veto adds beyond skipping the same number of signals at random in the same strata.

**Pass bar, per veto (V1 and V2 are two separate tests, no correction; a pass is a lead only).** All of:
1. at least **300 signals** on at least **100 distinct coins** (`key`) in the window;
2. the **95 % lower bound** of the per-signal gain is above 0, by a bootstrap that resamples **whole coins** (all signals of a `key` together),
   1,000 resamples, seed 17, percentile interval;
3. the gain is positive in at least **3 of 4** equal slices of the window by entry time;
4. the severe-loss rate with the veto is **below** the random veto's mean severe-loss rate.
If the floor in 1 is not met the veto is **inconclusive**, not failed. **The forward window is read once**: `d1.py forward` writes
`research/downside/out/forward.lock` and refuses a second run; deleting the lock restarts the count and is logged in docs/CHANGES.md.

**Earlier shadows (before this section).** Reported as **description only, labelled exploratory**: per veto, the skip share, the kept mean against the
skipped mean, the gain against random with its 95 % range, and the severe-loss rates. They are never used to change a number here, and they do not count
toward the pass bar (one coin can repeat across days and both windows).

**Limits.** Shadows use the desk's section 3 exits and section 2 cost. The population is P0 entries that passed the safety screen, on the desk's
discovery feeds (section 1 survivorship applies). One regime, one week. A pass earns only a further forward test and a separate pre-registration;
it changes no live rule.

**Changes.** Changing any number in this section after a result is seen restarts the count and is logged in docs/CHANGES.md.

### 17.1 Amendment, 2026-11-23 (before the forward window was read: no D1 forward result has been seen)

**Duplicate closes.** When a desk save of model.json fails after a shadow's close was already appended to shadows.jsonl,
the next run can close the same shadow again, so shadows.jsonl holds two lines for one entry. A duplicate is the same
`key`, `policy` and entry `ts`. Before any scoring, the D1 read keeps only the **first** close of each (the earliest
`exit_ts`, the one the paper logic took) and drops the rest. It reports the number of lines removed. One such case is
known in the window: `solana:8WW5B189mPntQQ85HTPvQNsYxXhQbAhdjXvZ6qaapump`, policy `all`, entry 2026-11-23 21:14:58 UTC,
closed at 21:20:00 (stop, -17.985 %) and again at 21:21:01 (stop, -26.216 %), around a failed save at 21:20:11; the
21:20:00 close is kept. This changes no number in section 17. The desk's save now retries the replace (PR #19).

### 17.2 Note, 2026-11-24 (before the forward window was read)

While building the desk's memecoin play-money accounts (PR #27), a development dry-run on a copy of the desk's files on
2026-11-24 computed interim D1 V1 and V2 virtual balances from shadows already in the forward window. They were not
shared and not acted on. The desk now hides the D1 cards' numbers until `research/downside/out/forward.lock` exists.
No number in section 17 changes, and the forward window is still read once, as section 17 says.

## 18. L1: a continuously learning model on P0's entries (pre-registered 2026-11-23, before any code and before any of its data)

**Why.** Sections 16 and 17 test fixed rules on P0's entries. L1 tests whether a small model that keeps learning from P0's own closed
shadows can pick the better entries. **Paper only.** L1 has no order path: it never signs, swaps or sends anything. It reads the desk's files
and never writes them. It changes no running rule. The D1 freeze (section 17) holds: nothing here changes P0, the safety screen, or the
section 2 cost or section 3 exits.

**What was seen before this section.** The field names that `desk/meme.py` and `memerules.features_from_pair` write; the fact that
`shadows.jsonl` holds closed shadows only (the desk appends a shadow there when it closes) and that open shadows live in `model.json`
(`"shadows"`, rewritten atomically about every 60 s); the chain and dex names in `model.json` `"tracked"`. No return, exit reason or feature value
of any shadow was looked at for this section.

**Start time.** `T18` = the author time of the commit that adds this section's heading to docs/MEME.md (read from git by the learner).

**Population.** Every shadow with `policy == "all"` and `screen == "pass"` whose entry time `ts` is after `T18`. A shadow is identified by
`(key, ts)`. The learner polls the desk's folder `data/accounts/meme/` **read-only** every **30 s**:
- `model.json` `"shadows"`: a population shadow seen there (open) for the first time is **predicted** at once.
- `shadows.jsonl`: only new complete lines are parsed (a byte offset; a line without its final newline waits). Each population line is a close.
- A population shadow whose first sighting is its closed line in `shadows.jsonl` (for example after an outage, or before the learner first
  ran) is **missed**: counted, never predicted, never scored.

**Features (fixed).** From the shadow's `f` at entry: `r5`, `r1h`, `r6h`, `r24h`, `v1h`, `vacc`, `rvol`, `tx_m5`, `buy_ratio_m5`, `liq`,
`age_h` (11 numeric). The dollar fields `v1h`, `liq` and the age `age_h` are transformed to `ln(1 + max(x, 0))`; the others are used as
written. A missing (null, absent or non-finite) value is set to **0** after standardisation, and its own **missing flag** (1/0) is a feature
(11 flags). Standardisation: each numeric feature uses a running mean and variance (Welford) over the training data only: a value enters the
running statistics only when its shadow closes and trains the model; missing values do not enter them. While a feature has fewer than 2 values
its standardised value is 0. One-hot **chain**: `solana`, `ethereum`, `base`, `bsc`, other (5). One-hot **dex**: `pumpswap`, `raydium`,
`uniswap`, `pumpfun`, `pancakeswap`, `meteora`, other, missing (8). Policy `all` shadows carry no `dex`, so dex is the token's
`model.json` `"tracked"[key]["dex"]` read when the shadow is first seen (an entry-time field). Plus a bias term. No other input: a shadow's exit
fields (`exit_at`, `exit_ts`, `exit_px`, `why`, `net_pct`, `peak`, `checked`) never reach a prediction.

**Model (fixed).** Online linear regression, squared loss, plain SGD. Target `y = clip(net_pct, -30, +30)`. Learning rate **0.01**, L2
**1e-4** on the weights (not on the bias). Starting weights and bias are 0. The weights change **only when a population shadow closes** (predicted,
void or missed), one step per close in the order closes are read: first the close's raw features enter the running statistics, then the
standardised vector `x` is built and `w <- w - 0.01 * ((w.x + b - y) * x + 1e-4 * w)`, `b <- b - 0.01 * (w.x + b - y)`. A predicted shadow trains
on the features logged with its prediction; a missed shadow on the `f` in its closed line and the tracked dex at that time (missing if gone).
The model version is the number of steps taken; the weight hash is the first 16 hex of SHA-256 of the canonical JSON of weights, bias and running
statistics.

**Take rule.** Take if the predicted net is **> 0**. **Warm-up:** the first **300** population closes after `T18` only train. A prediction is
**scored** only if, when it is made, at least 300 closes have trained the model, and it is not void. Predictions during warm-up are logged and
never scored.

**Log.** `C:\Trading\research\learner\out\predictions.jsonl`, append-only, one line per prediction: `key`, `entry_ts`, `pred_ts`, `features`
(raw and the vector used), `pred`, `take`, `warmup`, `model_version`, `weight_hash`, `prev` (SHA-256 of the previous line's bytes; 64 zeros for the
first). A prediction is written as soon as the shadow is first seen open. A prediction whose `pred_ts` is at or after that shadow's `exit_ts` is
**void**: counted, trains, never scored. Each close is logged the same way in `out/closes.jsonl` (hash-chained: key, entry ts, exit ts, net_pct,
predicted / void / missed, model version after the step). The weights are snapshotted once per UTC day to `out/weights/YYYY-MM-DD.json`. The
learner's own state is in `C:\Trading\research\learner\state\`, written atomically; it resumes after a restart without duplicate lines.

**Controls.** Both on the same scored signals as L1 (below).
- **P0:** every scored signal (take or not), i.e. what P0 itself did.
- **Random take:** in each 6-hour UTC entry block (`floor(entry_ts / 21600)`), take exactly as many signals as L1 took in that block, chosen at
  random. **1,000 draws, seed 18** give its spread; its expected mean (each block's mean net times L1's take count there, over all takes) is
  computed exactly, as in section 17.

**Formal looks (at most 4).** Only at **150, 300, 600 and 1,200** scored takes. Look `N` uses the first `N` scored takes in prediction order and
every scored signal predicted at or before the `N`-th take (its "signals"), and runs once all of them have closed. It writes
`out/look-N.json` and refuses a second run. The alpha (0.05, one-sided) is split 4 ways: each look uses **0.0125**. Anything read between looks
(a live readout) is descriptive, labelled so, and never a verdict.

**Pass bar at a look (all of):**
1. at least **100 takes** on at least **50 distinct coins** (`key`);
2. mean and median `net_pct` of the takes above 0;
3. mean of the takes above 0 without its best 3 takes;
4. mean of the takes positive in at least **3 of 4** equal slices of the look's entry-time range (a slice with no take is not positive);
5. mean of the takes above P0's mean over the look's signals;
6. gain = (mean of the takes) - (random take's expected mean): the **98.75 % one-sided lower bound** (1.25th percentile, i.e. the 95 % bound at
   alpha/4) of a bootstrap that resamples **whole coins** (all of a `key`'s signals together, takes and non-takes), **10,000** resamples, seed 18,
   is above 0.
L1 passes at the first look that meets all six; it then stops. No pass by the 1,200 look is a fail. A pass is a lead only: it earns a separate
pre-registration and changes no live rule.

**Changes.** Only the weights (and running statistics) change on their own. Any change to the features, learning rate, L2, clip, take rule or
warm-up makes a new study, **L2**: a new section, a new log, and the count starts over.

### 18.1 Amendment, 2026-11-23 (after review, before deploy, before any L1 outcome)

**What was seen before this amendment.** No L1 outcome. The learner has not been deployed and the warm-up (300 closes) has not been reached.
The only run was a trial on copies of the desk's files in a scratch folder; it looked at counts only (opens predicted, closes missed, the hash
chains). No `net_pct`, prediction value, take or weight was looked at. Its output has been deleted, so the real `predictions.jsonl` starts empty.

**Why.** Review found that one shadow with an extreme feature (for example `r24h` = 889) gives a standardised value far from 0; with plain SGD
at learning rate 0.01 a single such step can throw the weights far off. These two changes bound every step. They are made before any data, so
this stays L1 (not L2); `T18` stays the start.

**Changes (they replace the matching text of section 18).**
- Each standardised numeric feature is **capped to [-5, +5]** after standardisation (the missing flags and one-hots are 0/1 already). The cap
  applies to predictions and training alike, and the logged vector is the capped one.
- The SGD step is **normalised**. With `err = y - (w.x + b)` and `n2 = ||x||^2` (the sum of squares of the 35 features, the bias not included):
  `w <- w + 0.01 * (err * x / (1 + n2) - 1e-4 * w)` and `b <- b + 0.01 * err / (1 + n2)`. Learning rate 0.01, L2 1e-4 and the clip to [-30, +30]
  are unchanged.

Everything else in section 18 is unchanged.

## 19. C1: copy niche Solana wallets that buy early in runners, and C1-S: side wallets of popular callers (pre-registered 2026-11-23, before any code and before any wallet data)

**Why.** Every rule above decides from price and volume. C1 asks a different question: do a few small Solana wallets that kept buying runners
early, and made money doing it, keep doing it, so that a paper copy that is a few seconds late still makes money? C1-S asks the same of
**side wallets** linked to popular public callers ("KOLs"), and whether a side wallet beats the caller's public main wallet. **Paper only.**
C1 has no order path: no wallet, no signing key, no swap code. It sends phone alerts. Section 0 applies: "no edge" is an allowed and likely
answer, and callers often sell into the people who follow them.

**Isolation.** C1 runs as its own process (`research/copy/follow.py`) with its own scheduled task, from its own pinned clone
`C:\Trading\copy\repo`, never from the live desk folder and never inside `desk/meme.py`. It changes no running rule. The D1 freeze (section 17)
holds: nothing here changes P0, the safety screen, or the section 2 cost or section 3 exits. Desk files are read only through copies under
`C:\Trading\research\copy\input\`, except the desk's `settings.json`, which `follow.py` reads for `meme_ntfy_topic` only (shared-delete
handle, closed at once, at most once an hour).

**Secrets.** The Helius key is read at run time from `C:\Trading\secrets\helius.key`. It is never committed, logged or printed; any URL that
is logged has its `api-key` value replaced by `***`. The caller-name map (C1-S) lives only in `C:\Trading\secrets\kol-map.json` and never
reaches the repo, a log or an alert.

**What was seen before this section.** Field names and line counts of the desk's `discoveries.jsonl` (7,292 lines, 1,572 distinct Solana
coins, first line 2026-11-20 20:24 UTC), `shadows.jsonl` and `model.json`; the desk's `memeterminal.cant_sell` reasons; Helius's public docs
(plans, credits, rate limits). No price path, return, wallet address, swap or caller of any coin was looked at.

**T19.** The author time of the commit that adds this section's heading to docs/MEME.md (read from git by `discover.py`). The **dev window**
is all data stamped before T19. Discovery runs after T19 and must not use anything stamped at or after T19 (block time, candle close time,
desk line `ts`).

### 19.A Discovery (dev window only)

**Data.** Desk: a copy of `discoveries.jsonl` in `C:\Trading\research\copy\input\`, lines with `ts < T19` only. Prices: GeckoTerminal 5-minute
OHLCV, at most 5 calls a minute, one process. Swaps and transfers: Helius `getTransactionsForAddress` (full transactions, ascending, with a
`blockTime` filter `lt T19`); if the free plan refuses it, `getSignaturesForAddress` plus `getTransaction` (1 credit each, at most 8 calls a
second). Coin creator: RugCheck `tokens/{mint}/report` (`creator`), about 1 a second.

**Swap, buy and sell (used everywhere in section 19).** A successful transaction is a swap by wallet `W` of mint `M` when `W` signed it, `W`'s
own balance of `M` changed (sum of `postTokenBalances` minus `preTokenBalances` over token accounts whose `owner` is `W`), and `W`'s SOL plus
wrapped-SOL balance changed the other way. **Buy**: `M` up, SOL down. **Sell**: `M` down, SOL up. Its SOL amount is the change in `W`'s
lamports plus wrapped SOL, fees included. Transfers (no opposite SOL change) and token-for-token swaps without SOL are not swaps.

**Runner coins.** Every coin in the copy with `chain == "solana"` whose **first sighting** `ts0` (its first line) satisfies `ts0 + 24 h <= T19`.
Base price `p0` = that line's `px` (DexScreener `priceUsd` at first sighting). Pool = that line's `pool`. Candles: GeckoTerminal 5-minute OHLCV
(USD) of that pool. A coin is a **runner** if some 5-minute candle with open time `>= ts0` and close time (open + 300 s) `<= ts0 + 24 h` has a
**close** `>= 2.0 x p0` (+100 %). Closes, not highs, so one wick does not make a runner. A coin with no candles in that range is not a runner
(counted). **Run start** `t_run` = the open time of the first candle in that range whose close is `>= 1.25 x p0`.

**Early buyers.** Pool creation `t_c = ts0 - 3600 x features.age_h` from the first-sighting line (DexScreener `pairCreatedAt`). A buy of the
runner's mint (any venue, launchpad curve or pool, found through the mint address) is **early** if its block time is in `[t_c, t_c + 30 min)`
or in `[max(t_c, t_run - 6 h), t_run)`, and before T19. Wallets with at least one early buy are the **early buyers**.

**Exclusions (applied in this order, each counted in the report).**
1. Creator and dev: RugCheck's `creator` for that mint, and the fee payer of the mint's earliest transaction; excluded for every runner.
2. Bots and MEV, from each wallet's dev-window swaps (all mints): more than **20 swaps in any 60 s**; or a **sandwich pattern** (a buy and a
   sell of the same mint in the same slot, around another wallet's swap of that mint in that slot) on 2 or more slots.
3. Heavy traders: more than **50 swaps of any one mint in any one UTC day**.
4. Exchanges and services: **10,000 or more** signatures in the dev window.
5. Not niche: the **top 100** early buyers by total SOL spent on early buys across all runners.

**Score (all of).** Early buys on at least **5 distinct runners**; and **positive realized P&L** over the wallet's own dev-window swaps after its
costs: per mint, average-cost basis in SOL; realized = SOL received from sells minus the basis of the tokens sold; SOL figures include the
wallet's network and priority fees; summed over mints. Unsold tokens count nothing.

**C1 list.** Rank qualifying wallets by distinct runners (desc), then realized P&L in SOL (desc), then address. Freeze the top **30** (fewer if
fewer qualify) into `research/copy/WALLETS-C1.json`: address, tag (`C1-01`...), dev stats (runners, early buys, swaps, swaps a day, realized
SOL, share of closed mints in profit), plus T19, the input file's SHA-256 and the Helius credits used. **If fewer than 10 wallets qualify, C1
does not start**; the report says so, and any retry is a new section with a new dev window. `discover.py` also writes
`research/copy/DISCOVERY-C1.md`: runners, coins without candles, early buyers, each exclusion's count, candidates, qualifiers.

**Random control list.** Early buyers that passed exclusions 1 to 5 but did **not** meet the score are binned into quintiles of swaps a day
(dev window). For each frozen C1 wallet, in rank order, one wallet is drawn from its quintile without replacement (Python
`random.Random(19)`, candidates sorted by address). Frozen in the same file as `random`, with the same dev stats.

**Credit cap.** Discovery stops at **600,000** Helius credits (60 % of a free month). If the cap would be passed, runners are processed in order
of SHA-256(mint) and the rest are dropped and counted; the order does not depend on any result.

### 19.S C1-S: side wallets of popular callers (dev window only)

**Seed list.** Public main wallets of popular Solana callers from public KOL trackers: the top **30** Solana wallets of the kolscan.io
leaderboard (monthly), read once within 48 h after T19; if it cannot be read, another public KOL tracker the user names, read the same way. For
each: the source URL and read date. Committed files carry cluster IDs only (`K01`, `K02`... in leaderboard order); the name-to-cluster map is
only in `C:\Trading\secrets\kol-map.json`. A main wallet that is an exchange (exclusion 4) is dropped.

**Candidates.** For each main wallet `M`: wallets that received SOL from `M` in the dev window; wallets that received SOL from an intermediary
`I` that `M` funded in the dev window; and wallets that bought a mint 10 s to 30 min before `M`'s first buy of that mint (`M`'s dev-window
buys, at most its first 200 mints by time).

**Linking rules.** `W` links to `M` if any holds; every rule that holds is recorded.
- **(a) funded:** `W`'s first incoming SOL transfer came directly from `M`, or from a **fresh** intermediary `I` (`I`'s first transaction at
  most 7 days earlier, and `I` received SOL from `M` before funding `W`), and that transfer was within **7 days before** `W`'s first swap.
- **(b) lead:** `W` bought the same mint **10 s to 30 min before** `M`'s first buy of it, on at least **3 distinct mints**.
- **(c) sweep:** `W` sent SOL or tokens (a transfer, not a swap) to `M` or to `M`'s funder (the source of `M`'s first incoming SOL), unless
  that funder is an exchange (exclusion 4).

**Exclusions:** C1 exclusions 2 to 4 (bots and MEV, heavy traders, exchanges), every main wallet, and any wallet linked to **more than one
cluster**. Each rule's link count is reported before and after exclusions. No score: every linked wallet is followed.

**Frozen list.** `research/copy/WALLETS-C1S.json`: per side wallet, address, tag (`K03-side-1`...), cluster, rules, lead stats (rule (b) count
and median lead), dev stats as in C1; and the main wallets as `main` (`K03-main`) with their dev stats. No names and no URL that carries a name.

### 19.B Copy rules (the same for every arm and control)

**Forward window.** C1 and its random control start at the author time of the commit that adds `WALLETS-C1.json`; C1-S and its main-wallet
control at the commit that adds `WALLETS-C1S.json`. The lists never change during the study; a new list is C2 (a new section, a fresh count).
`follow.py` checks each file's SHA-256 against the one recorded at its freeze commit at every start, and stops on a mismatch.

**Detection.** `follow.py` subscribes to every frozen wallet over Helius standard WebSocket `logsSubscribe` (`mentions`, `confirmed`) and
fetches each new signature with `getTransaction`. After a reconnect or restart it back-fills from the last processed signature with
`getSignaturesForAddress`. Detection time `t_det` = local UTC time the transaction was parsed; **detection lag** = `t_det - blockTime`, logged.
A swap first seen more than 120 s after its block time is logged as **missed**, never copied.

**Buy.** A followed wallet's buy (SOL to token) is our paper buy at the **first DexScreener price sample taken at least 3 s after `t_det`**
(`/tokens/v1/solana/{mint}`, the pair with the most liquidity; a sample every 5 s; none within 120 s cancels the copy, logged). Size **$20**.
Fill = `priceUsd x (1 + 0.0175 + 20 / (liquidity_usd / 2))`. No liquidity figure means no fill (logged). At most **10 open** per arm and **one
per coin** per arm; a buy over either limit is skipped and logged.

**Sells.** When the wallet that opened our position sells, we sell the same **fraction** of our remaining tokens: fraction = tokens it sold /
its balance just before that sale. Same 3 s rule and sample; fill = `priceUsd x (1 - 0.0175 - value / (liquidity_usd / 2))`.

**Other exits.** Every open position is priced every 30 s: liquidity under **$1,000** closes the rest at **-100 %**; **24 h** after our buy the
rest is sold at the next sample (time exit).

**Can't-sell skip.** Before a buy the mint is checked (cached per mint). Helius `getAccountInfo` (jsonParsed) of the mint: blocked if a freeze
authority is set, or if it is Token-2022 with a permanent delegate, transfer hook, non-transferable flag, default frozen account state, or a
transfer fee over 10 %. RugCheck summary: blocked on the desk's `memeterminal.cant_sell` reasons (honeypot, freeze authority, Token-2022 traps,
a tax over 10 %). A check that cannot be completed is **unknown**, and unknown is a skip. Every skip is logged with its reasons.

**Net per copied buy.** `(dollars returned over all its sells) / 20 - 1`, in %, once fully closed. **Own result** of the copied wallet on the
same trade: SOL received for the matching fractions of its tokens / SOL it paid, minus 1, fees included (when we exit on our own rule, its
remaining tokens are valued at our exit sample's `priceNative`). **Copy gap** = own result - our net.

**Logs.** `C:\Trading\research\copy\`: append-only JSONL (one flushed line per write) and `state.json` replaced atomically. Every event has a
unique id (signature + leg), so a restart resumes with no duplicate line.

**Alerts.** One ntfy push per followed-wallet buy or sell (C1 and C1-S, not the controls): `[PAPER COPY] <tag> BUY|SELL <SYMBOL>`, the
DexScreener, Solscan and RugCheck links, the wallet's dev stats, and "Paper. Callers often sell into followers." Topic: the desk setting
`meme_ntfy_topic`. At most **20 an hour**. C1-S tags are cluster IDs only.

### 19.C Controls and measures

C1: (1) the **random** wallets, copied the same way; (2) **P0**: the desk's `all` shadows on Solana with entry `ts` in the forward window (from
a copy of `shadows.jsonl`, net as recorded), descriptive; (3) the copied wallets' **own** results on the same trades, giving the copy gap.
C1-S: (1) the clusters' **main** wallets, copied the same way ("does the side wallet beat the public one?"); (2) C1's random wallets. For every
side-wallet trade: **lead** = its buy time minus the main wallet's first buy of the same mint (within 24 h either way, else none); **dump** =
it sold while the main wallet still held that mint, or within 1 h after a main-wallet buy of it. The dump share is reported.

### 19.D Pass bar (C1 and C1-S each, against its own controls)

Formal looks only at **100, 200, 400 and 800** closed copied buys (the first N by entry time, once all have closed); each writes
`look-<arm>-N.json` and refuses a second run. Alpha 0.05 one-sided, split 4 ways: **0.0125** a look. A live readout is descriptive only.
An arm passes at a look if all hold:
1. at least **100** copied buys on at least **50** distinct coins;
2. mean and median net above 0;
3. mean above 0 without its **best 3**;
4. mean positive in at least **3 of 4** equal slices of the look's entry-time range (a slice with no buy is not positive);
5. gain = arm mean - random-control mean (C1-S: and arm mean - main-wallet mean, both): the **1.25th percentile** of a bootstrap that
   resamples **whole coins** (all copies of a mint in both arms together), **10,000** resamples, seed 19, is above 0;
6. no single wallet makes more than **25 %** of the arm's net dollar profit.
No pass by the 800 look is a fail. A pass is a lead only: it changes no live rule and earns a separate pre-registration.

**Helius budget (free plan, read 2026-11-23: 1M credits a month, 10 RPC calls/s, 5 WebSocket connections, 1,000 subscriptions per connection,
WebSocket data 2 credits per 0.1 MB).** If the month's credits run out, swaps not seen are logged as missed, never filled in later.

**Changes.** Changing any number here after a result is seen restarts the count and is logged in docs/CHANGES.md.

### 19.1 Amendment, 2026-11-23 (written with the code, before discovery ran and before any wallet, swap, candle or caller was fetched)

No Helius, GeckoTerminal, RugCheck or KOL-tracker call has been made, and the desk-data copy for C1 does not exist yet. This fixes what
section 19 left open, as `research/copy/` implements it. It changes no threshold, cost, exit, control or pass-bar number above.

1. **History window.** A wallet's dev-window swaps (exclusions 2 to 4, score, dev stats, C1-S candidates) are those with block time in
   `[D0, T19)`, where `D0` is the `ts` of the earliest line of the desk copy (about 2026-11-20 20:24 UTC). Swaps a day = swaps / window days.
   The runner universe therefore spans about 2 days of first sightings (`ts0 + 24 h <= T19`): a short dev window, stated as a limit.
2. **Order of exclusions.** 1 (creator and dev), then 5 (top 100 by early SOL, ranked among the early buyers left after 1; it needs no
   history), then 2 to 4 on fetched histories. A wallet with more than **3,000** transactions in the history window is excluded as
   **too active to score** (counted on its own). Exclusion 4 counts signatures in the history window, up to 10,000.
3. **Sandwich pattern.** One wallet's buy and sell of the same mint in the same slot (separate transactions), on 2 or more slots. Whether
   another wallet's swap sits between them is not checked (it needs every slot's full data).
4. **Early-window fetch.** Transactions of the runner's mint and of its first-sighting pool, successful only, ascending, the earliest
   **10,000** per address and window. A truncated window is counted.
5. **Random control pool.** Early buyers left after exclusions 1 and 5 that are not C1 candidates (fewer than 5 runners), sampled with
   `random.Random(19)` to **300** (sorted addresses), plus candidates that failed only the P&L test; exclusions 2 to 4 then apply. Quintiles
   are over that pool. A quintile that runs dry draws from the nearest one (lower first).
6. **Creator and dev.** RugCheck's full-report `creator`, and the fee payer of the mint's earliest transaction (before T19).
7. **C1-S.** `M`'s funder and `W`'s first incoming SOL are read from each address's first 20 transactions; `W`'s first swap from those or
   from its history. Rule (b) candidates are fetched from the mint's transactions in `[t_M - 30 min, t_M - 10 s)`, at most 2,000 per mint,
   in round-robin order over clusters by mint index. C1-S discovery stops at **300,000** Helius credits; mints not fetched are counted.
8. **Follower.** On its very first start a wallet's back-fill begins at its newest signature (nothing older is replayed). An unknown
   can't-sell check is retried after 10 minutes. The open limit counts buys waiting for their fill. A position is checked every 30 s, so the
   time exit fills at the first such sample at or after 24 h. Alerts are sent at detection (not for missed swaps and not for controls). The
   copied wallet's own price for its remaining lot uses DexScreener `priceNative` when the pair is quoted in SOL, else `priceUsd` / SOL's USD
   price from the same request.
9. **Looks.** A control's rows at look N are its closed copies entered at or before the arm's N-th copied buy. The 1.25th percentile is the
   `ceil(0.0125 x B)`-th smallest of the B usable resamples; a resample with an empty side is dropped and counted.
10. **P0 readout.** The desk's `all` shadows with `chain == "solana"` (no screen filter), net as recorded, entered after C1's start.

### 19.2 Amendment, 2026-11-23 (before discovery ran and before any wallet data)

The section 19.A fallback (if the plan refuses `getTransactionsForAddress`) pages `getSignaturesForAddress` backwards from the newest
signature, at most **3,000** pages of 1,000 per address and window, keeps successful signatures with block time in the window and before
T19, and fetches each with `getTransaction`. A window cut by the page cap is counted as truncated. Once refused, the fallback is used for the
rest of the run. Nothing else changes.

## 20. C2: a deep scan for consistently profitable Solana wallets, two windows against luck (pre-registered 2026-11-24, before any C2 code or data)

**Why.** C1 (section 19) picks wallets from about two days of desk data. C2 is the bigger successor: it looks for wallets whose own
trades made money, after desk costs, in **two separate 90-day windows**. Wallets that pass both and clear a shuffle test against luck are
then paper-copied the way section 19 copies. **Paper only.** No wallet, no signing key, no order path. C1 and C1-S carry on unchanged; nothing
here changes their discovery, lists or rules. The D1 freeze (section 17) holds: no change to P0, the safety screen, or the section 2 cost or
section 3 exits.

**Decided with the user before this section (2026-11-24):** the scope (a wallet must have bought at least 3 of the set's coins, history 180
days), the coin source (7 daily DexScreener snapshots), one time split for every wallet, and qualification on coins **outside** the set only.
The budget was estimated before any data: about 17M Helius credits, with a hard stop at 20M.

**What was seen before this section.** C1's code and its frozen rules; Helius's public plans and credit costs; the count of Solana coins in the
desk's discoveries copy (1,638). No wallet, swap, P&L, price path, coin ranking or volume figure was looked at for C2.

**T20.** The author time of the commit that adds this section's heading. The **history period** is `[T20 - 180 d, T20)`: window **A** =
`[T20 - 180 d, T20 - 90 d)`, window **B** = `[T20 - 90 d, T20)`. No transaction at or after T20 is used for selection.

### 20.A Coins

1. **Candidates.** Solana tokens from (a) every `chain == "solana"` token in the desk's `discoveries.jsonl` at the time of each snapshot,
   and (b) the base tokens of the top 500 Solana pairs by 24 h volume on dexscreener.com (its public ranking page, read-only, the first 5
   pages, once, on the first snapshot day; the URL and time are recorded). The union of (a) and (b) is the candidate list. DexScreener's API
   has no ranking endpoint, so (b) is the only way to see coins the desk never tracked.
2. **Snapshots.** Once a day for **7** consecutive UTC days, starting on the first 00:05 UTC after T20: every candidate's 24 h USD volume,
   summed over all its Solana pairs, from DexScreener `/tokens/v1/solana/{addresses}` (30 a call). A candidate missing on a day counts 0 that
   day. No Helius or GeckoTerminal call.
3. **Exclusions:** stablecoins, SOL and liquid-staking SOL tokens, wrapped majors, and the infrastructure and DeFi tokens in
   `research/copy2/coin_exclusions.json` (committed with this section, before any snapshot). Also excluded: any token whose first transaction
   is at or after `T20 - 24 h` (its first 24 h is not complete before T20).
4. **The set.** The **top 300** remaining candidates by summed 7-day volume (ties by mint address), **plus every runner C1's discovery found**
   (section 19.A, from C1's cached candles; no new GeckoTerminal call). The set is written to `research/copy2/COINS-C2.json` with the
   snapshot file hashes.

### 20.B Buyers

- **A coin's first 24 h:** from the block time of its mint's earliest transaction (Helius), for 24 hours, and before T20.
- **Buyers:** every wallet with at least one section 19 **buy** of the coin in its first 24 h, read from the mint's transactions with Helius
  (`getTransactionsForAddress`, full, ascending; or the section 19.2 fallback). There is no cap on the number of wallets. There is a cap of
  **1,000,000** transactions per coin; a coin cut by it is counted.
- **Scope filter:** only wallets that bought **at least 3 distinct coins of the set** in those coins' first 24 h go on to history fetches.
  Everything else is counted, not fetched.
- **Prices** come from the swaps themselves: SOL in per token out, never GeckoTerminal.

### 20.C Exclusions (each counted)

1. **Creator and dev:** the fee payer of each mint's earliest transaction, and RugCheck's `creator`.
2. **Snipers:** a buy of a set coin in the slot of that coin's first swap or the next slot.
3. **Bundlers:** two or more wallets that bought the same set coin in the **same slot** and whose first incoming SOL came from the **same
   source** (read from each wallet's first 20 transactions; a source with 50,000 or more signatures in the history period is an exchange and
   does not make a bundle). Every wallet of such a group is excluded, and so is a wallet in more than one group (multi-cluster).
4. **Bots and MEV, heavy traders:** as section 19 and 19.1, measured over the history period: more than 20 swaps in any 60 s, the sandwich
   pattern on 2 or more slots, more than 50 swaps of one mint in one UTC day.
5. **Exchanges and services:** **50,000** or more signatures in the history period. More than **20,000** transactions in the history period
   is too active to score.

### 20.D Two-window qualification (coins outside the set only)

- **Trades.** From the wallet's section 19 swaps in the history period, per mint, average-cost basis in SOL. Desk cost: each buy's SOL
  counts as `sol x 1.0175`, each sell's SOL as `sol x 0.9825` (3.5 % round trip, half each side). A **trade** is one sell: realized = net SOL
  received minus the basis of the tokens sold. Tokens bought before `T20 - 180 d` have no basis; their sells are not counted. A trade belongs
  to the window of its sell time.
- **Only coins outside the set count.** Trades in the set's coins are reported beside the result, never used to qualify or rank.
- **A wallet qualifies** if, in **each** window: net realized P&L > 0; trades on at least **5 distinct coins**; and its largest single
  trade's profit is at most **40 %** of that window's net P&L.
- **Rank** qualifiers by window B's net P&L divided by its bootstrap standard error: B's trades resampled with replacement **1,000** times,
  `random.Random(20)`; SE = standard deviation of the resampled sums. Ties by address. **Freeze the top 50** in
  `research/copy2/WALLETS-C2.json` with dev stats per window.
- **Against luck.** The eligible wallets are those that passed 20.C with trades on at least 5 distinct outside-set coins in each window. For
  each eligible wallet, its trade P&Ls are permuted across its own trade slots (each slot keeps its coin and window), **1,000** times,
  `random.Random(20)`, and the qualifier count is taken each time. Report the null mean and its 95th and 99th percentiles beside the actual
  count. **Clearly above chance** means the actual count is above the null's 99th percentile **and** at least twice the null mean. If not,
  C2 says so and **stops: no forward test**.
- **Random control:** 50 wallets drawn from eligible wallets that did not qualify, one per C2 wallet from the same quintile of swaps a day
  (history period), without replacement, `random.Random(20)`, candidates sorted by address. Frozen in the same file.

### 20.E Copy rules, controls and pass bar

Exactly as section 19.B, 19.C (random control, P0 descriptive, own result and copy gap) and 19.D. That means a 3 s delay after detection,
desk cost plus price impact, $20 a copy, at most 10 open, one per coin, mirrored sell fractions, a dead-pool exit at -100 %, a 24 h time
exit, the can't-sell skip, looks at 100, 200, 400 and 800 closed copies (alpha 0.0125 each), and no wallet over 25 % of the profit. The
arm is `C2`, its control `C2-random`. The forward window starts at the author time of the commit that adds `WALLETS-C2.json`, which never
changes; a new list is a new study.

### 20.F Budget and running

- Helius at most **8 calls/s**. A credit meter in the scan's state file. A **hard stop at 20,000,000 credits**: the scan stops, and wallets
  not reached are counted.
- Wallets are fetched in SHA-256(address) order, so where the stop falls does not depend on any result.
- The scan does not start before C1's discovery has finished, and not before the user has confirmed the plan and budget.
- It is resumable: an interrupted scan picks up where it stopped.
- It streams per-wallet results to disk and never holds all wallets in memory.

**Changes.** Changing any number here after a result is seen restarts the count and is logged in docs/CHANGES.md.

### 20.1 Amendment, 2026-11-24 (before any snapshot and before any C2 data)

The dexscreener.com ranking page of 20.A item 1 (b) is read once on 2026-11-24, before the first snapshot day, instead of on the
first snapshot day, so the first 00:05 UTC snapshot does not wait on it. Only its list of tokens is used (as candidates); its order
and volumes are not. Nothing else changes.

### 20.2 Amendment, 2026-11-24 (before any snapshot and before any C2 data)

**What exists before this amendment.** No C2 snapshot, buyer list, wallet history, swap, price or P&L exists or has been read. The only
C2 input read so far is the dexscreener.com ranking page of 20.1 (`C:\Trading\research\copy2\input\ranking-page.json`, its token list
only). This amendment also draws on a methods memo, Meambot `research/copy/CODEX-COPY-METHODS.md` (commit `dbff14c`, branch
`codex/copy-research`, 2026-11-24). That memo holds published literature and product documentation only, and no C2 data.
It warns that "the most impressive leader fills may be the least copyable" and that "random wallets may supply a weak counterfactual". Its
Draft 1 proposes selecting leaders by **prior copyable return** against a control matched on activity, size and launch age.

**1. The selection score is the copyable return, not the wallet's own P&L.** In 20.D, "net realized P&L" and every measure built on it
(qualification, rank, against luck) now mean the **copyable P&L** defined here. The wallet's own P&L, as 20.D first defined it, is
kept and reported, never used to select.

- **Replayed follower.** For each wallet, its section 19 swaps of coins outside the set in the history period are replayed in time
  order. A wallet **buy** opens a follower position of **0.1 SOL** when the follower holds none of that mint (one per coin, as 19.B). The
  follower buys at the **first price at least 3 s after the wallet's buy**. A buy while the follower already holds that mint is not copied.
- **Mirrored sells.** On each wallet **sell** of a mint the follower holds, the follower sells the same fraction of its remaining tokens.
  The fraction is the tokens the wallet sold divided by its balance just before that sale, as in 19.B. The follower sells at the **first
  price at least 3 s after the wallet's sell**.
- **Time exit, as 20.E copies forward.** A follower position still open **24 h** after the follower's buy sells the rest at the first
  price at or after that time.
- **A price.** "The first price" is the first successful swap of that mint against SOL, by any signer, with block time at or after the
  given time. Its price is the swapping account's SOL change divided by its token change. Its **liquidity** is twice the pool's SOL
  reserve after that swap (the pool's WSOL token-account post-balance).
  - No such swap within **120 s** of a wallet buy: the buy is not copied, and it is counted.
  - No such swap before T20 for a sell or the time exit: that leg is unfilled. Its tokens stay unrealized and are not counted, as 20.D
    treats tokens still held. The leg is counted.
- **Costs.** These are the desk cost of 20.D plus the price impact of 19.B, both in SOL.
  - Buy fill = `price x (1 + 0.0175 + 0.1 / reserve)`.
  - Sell fill = `price x (1 - 0.0175 - value / reserve)`.
  - `reserve` is the pool's SOL reserve (half the liquidity above), and `value` is the leg's SOL value at `price`.
- **A copyable trade** is one follower sell leg: SOL received minus the cost basis of the tokens sold (average cost). It belongs to the
  window of the follower's sell time. A position opened before `T20 - 180 d` does not exist; a wallet sell before its first copied buy is
  not mirrored.

**2. Qualification, rank and luck use the copyable measure** with the same numbers as 20.D:
- **Qualify:** in **each** window A and B, copyable net P&L > 0, copyable trades on at least **5 distinct coins**, and the largest single
  copyable trade's profit at most **40 %** of that window's copyable net. Only coins outside the set count, as before.
- **Rank:** window B's copyable net P&L divided by its bootstrap standard error. B's copyable trades are resampled with replacement
  **1,000** times, `random.Random(20)`, and the SE is the standard deviation of the resampled sums. Ties go by address. Freeze the top
  **50**.
- **Against luck:** eligible wallets are those that passed 20.C with copyable trades on at least 5 distinct outside-set coins in each
  window. Each eligible wallet's copyable trade P&Ls are permuted across its own copyable trade slots (each slot keeps its coin and
  window), **1,000** times, `random.Random(20)`. The null, the 95th and 99th percentiles, and the "clearly above chance" rule are unchanged.
  If the actual count is not clearly above chance, C2 stops with no forward test.

**3. Report beside each qualifier** (in `WALLETS-C2.json` dev stats and the scan report), per window:
- its **own** realized P&L (20.D as first written, in SOL) and its own return per SOL of basis sold;
- its **copyable** P&L (in SOL, on 0.1 SOL copies) and the copyable return per SOL of follower basis sold;
- the **gap** = own return minus copyable return, in percentage points.

The report gives the **average gap** over the qualifiers, per window, and the counts of uncopied buys and unfilled legs.

**4. The random control is matched on three measures, in terciles** (replacing 20.D's quintile of swaps a day). Following the memo's
matched-control advice:
- **Swaps a day:** buys and sells over the history period, as 20.D's control used.
- **Median entry liquidity:** over the wallet's buys of outside-set coins, the median liquidity (twice the pool's SOL reserve after the
  wallet's own buy).
- **Median token age at entry:** buy time minus the block time of the mint's earliest transaction.

Each measure is cut at its 33rd and 67th percentile over the eligible wallets, which gives 27 cells. Each C2 wallet draws one control from
eligible wallets that did not qualify, in its own cell, without replacement, `random.Random(20)`, candidates sorted by address. If its cell
has no candidate left, the draw comes from the nearest cell: smallest sum of tercile steps, ties to the lower cell in the order swaps,
liquidity, age. Every non-exact match is counted and reported.

**5. Budget and code.**
- The copyable replay and the age measure need Helius calls that 20.F's estimate of about 17M credits did not include: one price lookup
  per copied leg and one earliest-transaction lookup per outside-set mint.
- The hard stop stays at **20,000,000** credits, and the SHA-256 wallet order of 20.F is unchanged.
- A new estimate is given to the user before the scan, which still does not start without the user's confirmation (20.F).
- `research/copy2/scan.py` at this commit still implements 20.D as first written. It is changed to this amendment in a separate commit
  before any scan runs.

Nothing else in section 20 changes: the coins, buyers, exclusions, copy rules, pass bar and T20 stay as written.
