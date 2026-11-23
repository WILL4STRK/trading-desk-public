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
