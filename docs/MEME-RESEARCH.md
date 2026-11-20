# Memecoin research: what the evidence says (2024 to Nov 2026)

Written 2026-11-20 for model 4 (docs/MEME.md). Research only. No code was changed. Nothing here is advice.
Every rule below is a **proposal**. None of it may be used until it is written into docs/MEME.md and logged in
docs/CHANGES.md **before** any result is looked at, as MEME.md section 5 requires.

## Bottom line

1. No study, bot or postmortem found here shows an entry rule with positive returns after costs, out of sample.
   The best in-sample rule in the largest public backtest (1,411 coins) went from +85% in dev to -81% out of sample.
2. Filters do cut losses, but they do not make the trade positive. The best published filter (MELT) cut the loss
   on newly migrated coins from about -61% to about -27%. It was still a loss.
3. The decay is fastest in the first hours and the first day. The median pump.fun graduate is worth 0.14x its
   graduation value after 1 hour and 0.04x after 24 hours. From 24 hours to 7 days the median barely moves
   (0.05x), though dead pools drop out of the 7-day sample.
4. Real round-trip cost for a $20 to $100 trade is about 3 to 4.5 % on Solana memecoin pools, mostly pool fees
   (pump.fun charges up to 1.25 % a side). The desk's 3.5 % is fair for Solana, too low for Ethereum at $20.
5. So the useful work is screens that keep the account out of the worst coins, an age floor, and an honest forward
   record. Expect the best case to be close to break-even, not a profit.

## How to read this

- **Strength**: strong = large sample, clear method, primary source; some = a real dataset but narrow, short or
  with method gaps; weak = small sample, vendor source, or a threshold nobody has tested.
- **Verified**: "yes" means the number was found in the primary source text (paper PDF or arXiv HTML, checked by
  searching the text for it). "page" means
  I read it on the publisher's own page but could not check the underlying data. "no" means it came only through a
  summary and should not be relied on.
- Several sources are independent analytics blogs (Bitquery, MemeFees, Arrow Algo, Scorp Labs). They publish
  methods, but nobody has audited them. They are marked "some" at best.

## Findings

### 1. Return distribution and lifecycle

| # | Claim | Number | Source | Strength | Verified |
|---|---|---|---|---|---|
| 1.1 | Almost no pump.fun coins graduate, and the rate moves a lot with the period and the method | 0.63 % (655,770 coins, Sep 2025); 0.198 % in a fast 6-minute window (832,941 coins, May-Jun 2026); 1 in 58 (1.7 %) by trade data, Nov 2025-Oct 2026 | [Marino et al. 2602.14860](https://arxiv.org/html/2602.14860v1); [Kamat 2607.02823](https://arxiv.org/html/2607.02823v3); [Bitquery](https://bitquery.io/investigations/solana-launchpad-year) | strong (rate is small); the exact number is not stable | yes / yes / page |
| 1.2 | Most launches stop trading within minutes | Half of pump.fun launches have their last trade within about 3 min; 72 % within 1 h; 83 % within 1 day; 1.5 % trade at all in the week starting day 30 | [Bitquery](https://bitquery.io/investigations/solana-launchpad-year) | some | page |
| 1.3 | Graduates fall hard right after graduating, then mostly flatten | pump.fun graduates, median market cap vs graduation: 0.14x at 1 h, 0.04x at 24 h, 0.05x at 7 d. Share at or above graduation value: 26.2 % (1 h), 8.5 % (24 h), 3.6 % (7 d). n = 55,943 / 55,302 / 16,699 | [MemeFees survival study](https://memefees.com/research/memecoin-survival-2026), live snapshot dated 2026-11-20 | some | page |
| 1.4 | Right after migration, most coins drop more than 70 % within 20 minutes | 84.1 % of 41,470 migrated launches labelled high-risk (min price within 20 min of migration < 0.3x, or manually labelled manipulated), Dec 2024-Mar 2025. On price alone it is about 76 % (my arithmetic: 37.5 % of 9,471 reviewed coins were labelled by hand) | [MELT 2602.13480v2](https://arxiv.org/html/2602.13480v2) | strong | yes |
| 1.5 | Buying every tradable new coin at 30 minutes old loses most of the stake | $25 paper buys at 30 min old (liquidity >= $5k, >= 20 tx in the last hour), 1,411 coins, Oct 2026: mean net -31.4 % at 1 h, -61.8 % at 6 h, -55.7 % at 24 h; median -100 % at 6 h. With costs removed the 6 h mean was still -59.4 % | [Arrow Algo](https://arrowalgo.com/solana-memecoin-trading/) | some (few days, one regime) | page |
| 1.6 | Ordinary buyers mostly lose | 74 % of about 2.9 million ordinary buyers had less SOL after 2 weeks (70-79 % each week); median loss $13. Share of coins sold at a profit falls slightly with later entry: 34 % (first minute) to 28 % (after a day) | [Bitquery](https://bitquery.io/investigations/solana-memecoin-traders-lose-money) | some | page |
| 1.7 | Across 4 chains (ETH, BSC, Solana, Base), over 3 months a third fell, a third were dead, a third rose | 20,688 coins alive in Oct 2024: 34.1 % fell (average -38.4 %), 31.8 % had zero volume for the whole 3 months, 34.1 % rose | [Mongardini & Mei 2507.01963](https://arxiv.org/abs/2507.01963) | strong | yes |
| 1.8 | Collapse is the default | 98.6 % of 7M+ pump.fun tokens (Jan 2024-Mar 2025) fell under $1,000 liquidity; about 93 % of 388k Raydium v4 pools had 90 %+ of LP pulled by the deployer | [Solidus Labs](https://www.soliduslabs.com/reports/solana-rug-pulls-pump-dumps-crypto-compliance) | some (pump.fun disputes it) | page |
| 1.9 | Time of day and weekday | **Searched, none found.** No study with a usable method reports returns by UTC hour or weekday | - | none | - |

### 2. Signals with measured predictive power

Most measured signals predict **graduation** or **rug risk**, not short-horizon returns. A signal that predicts
graduation does not make money by itself, because graduates then fall (finding 1.3).

| # | Signal | What was measured | Source | Strength | Verified |
|---|---|---|---|---|---|
| 2.1 | Early holder concentration and bundles | High-risk launches: first 10 / 20 buyers hold 17 / 19 points more supply. Merging bundled wallets raises the top-10 share by a median 24 points for high-risk coins vs 6 for low-risk. Bundled accounts hold 36.5 % of supply at migration | [MELT 2602.13480v2](https://arxiv.org/html/2602.13480v2) | strong (direction) | yes |
| 2.2 | ML on early features cuts losses but stays negative | Buy at migration, sell at a random time within 1 h. No model: -60.7 % (top-100). Best model (MLP): -26.6 %. AUPRC 0.57 vs 0.26 random | [MELT 2602.13480v2](https://arxiv.org/html/2602.13480v2) | strong | yes |
| 2.3 | Early rug prediction is weak | First-5-minute features, 6.4M coins: F1 0.78, MCC 0.36, AUCPRC 0.76 in-domain; cross-DEX transfer fails (MCC about 0). Authors: "not yet sufficient for real-world deployment" | [Catching the Rug 2608.20271](https://arxiv.org/html/2608.20271v1) | some | yes (test-set counts in the text look garbled, so only these metrics are cited) |
| 2.4 | Concentration is common among coins that pumped | Of 707 coins up >100 % in 3 months, 82.8 % show wash trading, LP-based inflation or concentrated ownership; 50.78 % have top-10 holders above 30 % | [Mongardini & Mei 2507.01963](https://arxiv.org/abs/2507.01963) | strong (descriptive) | yes |
| 2.5 | Wash-trading markers | Daily volume up >500 % with price change <5 %; a maker's buys and sells within 2 %; >= 99 % of volume from makers who both bought and sold. Pump-and-dump dumps: median -55.8 % | [Mongardini & Mei 2507.01963](https://arxiv.org/abs/2507.01963) | some (authors call thresholds heuristics) | yes |
| 2.6 | Wash trading is common and rises with activity on pump.fun | About 17 % of trades are wash trades; wash share of a coin's trades is 0.4 % at 1-10 tx and 21.7 % at 1,001-10,000 tx. Wash-traded coins graduate at 2.0 % vs 0.90 % | [Meme Coin Factories 2609.10246](https://arxiv.org/html/2609.10246) | strong | yes |
| 2.7 | Trading speed and non-bot share predict graduation, not profit | Reaching a given SOL level in fewer trades predicts graduation best. A buy-at-threshold, sell-at-graduation rule is below break-even over most of the curve, even before fees | [Marino et al. 2602.14860](https://arxiv.org/html/2602.14860v1) | some | yes |
| 2.8 | Socials predict graduation (association only) | Telegram 1.49 % vs 0.17 %; all three socials 1.92 % vs 0.11 % with none | [Kamat 2607.02823](https://arxiv.org/html/2607.02823v3) | weak-some (two corrigenda) | yes |
| 2.9 | Dev history predicts graduation; trading it lost | Dev with a prior graduation and 7+ days quiet: 12.0 % graduate vs 1.71 %. Real-money test: 77 positions, -0.22 SOL, stopped | [Scorp Labs](https://trader.scorplabs.online/blog/pump-fun-graduation-rate-dev-signal-12-percent) (sells the signal) | weak | page |
| 2.10 | Paid promotion and X promotion | X-promoted coins -60 % to -95 % at 6 h; paid promotion the worst group | [Arrow Algo](https://arrowalgo.com/solana-memecoin-trading/) | weak-some | page |
| 2.11 | DexScreener boosts | **Searched, no real study found.** Only a 28-coin paper test: +$268, +$11 without its best 3 | [MEMESCOPE PR #266](https://github.com/kshetty851-bit/MEMESCOPE/pull/266) | weak | page |
| 2.12 | KOL calls | "76 % of influencer coins lose 90 %+" (CoinWire, via news). Primary report not found | via [Bitget news](https://www.bitget.com/news/detail/12560604374049) | weak | no |
| 2.13 | Copy-trading smart money | One paper claims +3 % per trade for an LLM multi-agent copier; its visible ablation rows show copier gross returns below 1.0 (losses), and the full row was not readable. Simple statistical copiers were negative | [2601.08641](https://arxiv.org/html/2601.08641) | weak | partly |
| 2.14 | Sniper cohorts | The only study (+16 % first-30-min buyer lift) now carries a withdrawal notice: 43 % of its "buys" were sells. Do not use | [Kamat 2607.02795](https://arxiv.org/pdf/2607.02795) | none | yes (notice read) |
| 2.15 | Community takeovers, LP burn as a return signal, liquidity/FDV ratio, time of day | **Searched, none found.** Only rules of thumb in guides | - | none | - |

Note on Kamat: the two Kamat papers (graduation and sniper cohorts) are by Arati Uday Kamat. The graduation paper
needed two corrigenda and the sniper paper is withdrawn. MEME.md's "Kamat bot" is probably the same author; I did
not confirm that.

### 3. Screening tools

| # | Claim | Number | Source | Strength | Verified |
|---|---|---|---|---|---|
| 3.1 | GoPlus misses many honeypots | On 1,859 labelled honeypots (BSC/ETH) GoPlus flagged 59.6 % (40.4 % missed); Honeypot.is flagged 87.1 % | [2610.10149](https://arxiv.org/html/2610.10149) | some | page |
| 3.2 | Why tools miss | They check the current state: owners switch on sell blocks after a delay, use allowlists, raise sell tax later, or drain the pool | same | some | page |
| 3.3 | RugCheck accuracy | **Searched, no independent measurement found.** | - | none | - |
| 3.4 | Live example: RugCheck score is not enough | Boosted pump.fun coin `FVzq...pump`: `score_normalised` 1, `risks` empty, but `insiderNetworks` list 9 transfer-linked groups | live curl, 2026-11-20 | n = 1 | yes |
| 3.5 | Live example: the current EVM screen lets through a dev-held coin | Boosted BSC coin `0xD0D6...61a8`: GoPlus not a honeypot, tax 0, open source (passes MEME.md's screen), yet creator holds 63.9 %, 41 holders, 63 % of LP unlocked in the creator's wallet | live curl, 2026-11-20 | n = 1 | yes |
| 3.6 | Dev-made pools without a curve are almost all emptied | 451,402 "curve-skip" PumpSwap pools: 99 % emptied, mostly by the dev; 87 % within 1 hour | [Bitquery](https://bitquery.io/investigations/solana-launchpad-year) | some | page |

### 4. Strategies with out-of-sample evidence

- **Positive after costs, out of sample: none found.**
- Shown to lose: buying new coins at 30 minutes (finding 1.5); buying at migration even with the best filter
  (2.2); buy-at-curve-threshold (2.7); dev-history sniping with real money (2.9); 416 strategy variants in the Arrow
  Algo test, none significantly positive, best one +85 % dev / -81 % out of sample; 15 strategies in
  [memecoin-paper-bot](https://github.com/maxmckittrick1-oss/memecoin-paper-bot), none beat holding after real
  Jupiter quotes; and the handoff's own result (7 of 7 live paper trades lost; hype buckets -14 % to -73 %).
- Closest to flat: entries about 6 hours after the decision in the Arrow Algo test had a median of -5.2 %, about one
  round-trip cost; the handoff's 24-168 h bucket was +0.28 % gross. Both say "no proven edge, not a proven loss".

## Real cost estimate

Prices used (live, 2026-11-20): SOL about $69.4, ETH about $1,638, BNB about $542 (DexScreener).

| Part | Solana (PumpSwap) | Base | BSC | Ethereum |
|---|---|---|---|---|
| Pool fee per side | 1.25 % (cap < 420 SOL, about $29k); 1.20 % (to 1,470 SOL, about $102k); 0.95 % (about $0.7-1.0M); 0.70 % (about $2.4-2.7M); 0.475 % (about $4.4-4.8M); 0.30 % (>= 98,240 SOL, about $6.8M) | Clanker pools often 1 % static; Uniswap v2 0.3 % | PancakeSwap v2 0.25 % (GoPlus `dex[].pool_fee`); four.meme curve 1 % | Uniswap v2 0.3 %; v3/v4 meme pools often 1 % |
| Network fee per swap (measured) | base 5,000 lamports (about $0.0004); priority fee median 0 on the PumpSwap program, p90 about 200,000 micro-lamports/CU (about $0.002); Jito landed tip p50 1.8e-6 SOL | gas 0.006 gwei, well under $0.01 | gas 0.05 gwei, about $0.004 | gas 1.31 gwei x about 150k gas = about $0.32 |
| One-off | token account rent about 0.002 SOL ($0.14, 0.7 % of $20), refunded when the account is closed | - | - | - |
| Price impact, $20 | $20 vs a $57k quote side: about 0.03 % a side | same idea, depends on pool | same | same |
| Sandwich risk | 8 per 10,000 swaps at 1-3 % slippage, 148 per 10,000 at 20-50 % ([Bitquery](https://bitquery.io/investigations/solana-sandwich-attacks)); median victim loss about $5.6 in [IMC study](https://cnitarot.github.io/papers/imc26_solana.pdf). At <= 3 % slippage the expected cost is well under 0.1 % | low | low | low |
| Round trip at $20, fees only | 2.4-2.5 % under about $100k cap; about 1.9 % near $1M; about 1.4 % near $2.5M | about 2 % (1 % pools) | about 0.5 % | about 0.6-2 % pool + **3.2 % gas** |
| Plus adverse move while filling (stale price, latency) | about 1-2 % (meme_traderv1 measured about 4 % all-in; Arrow Algo modelled 1 % a side) | same | same | same |
| **All-in estimate** | **about 3-4.5 %** | **about 3-4 %** | **about 1.5-3 %** | **about 5-7 % at $20** |

Sources: pump.fun fee tiers from the [Coinpaprika fee page](https://coinpaprika.com/education/launchpad-fees/)
(Oct 2026, citing pump.fun's docs; pump.fun's own docs page returned 403, so the tiers are **secondary-source** and
only 6 of 25 tiers were visible). Clanker from its [v4 docs](https://clanker.gitbook.io/documentation/references/core-contracts/v4/clankerhook/clankerhookstaticfee).
four.meme 1 % from third-party reviews (not verified). Network numbers measured live with public RPC calls
(`eth_gasPrice`, `getRecentPrioritizationFees`) and Jito's `tip_floor` endpoint.

What this means for the desk:
- 3.5 % is a fair flat number for Solana. It is a little low for coins under about $100k market cap.
- Ethereum at a $20 position costs about twice as much as the other chains. Either drop Ethereum from the account,
  or require positions of $200 or more there. (This changes MEME.md section 2 and must be logged.)
- A better cost model would charge the PumpSwap tier from `dexId` and `marketCap` in the `/tokens/v1` snapshot.
  Same rule: decide before looking at results.
- MEV "protection" is not worth paying for at this size: the IMC study found users spent $2.4M on defensive Jito
  tips while only 0.038 % of bundles were sandwiches. Tight slippage matters more.

## Proposed new rules (to be pre-registered before any test)

These are **proposals only**. Three are vetoes (they remove buys). One is a new measuring policy. Each veto is
scored on the existing forward shadow lines: "policy + veto" against "policy alone", same exits (stop -15 %,
target +30 %, 4 h time stop), same pass bar. The forward record keeps logging vetoed buys, so nothing is lost.

**R1. `age_floor_24h` (veto).** The paper account does not buy a coin whose pool is younger than 24 hours
(`now - pairCreatedAt` from `/tokens/v1`).
- Evidence: findings 1.2-1.5 and the handoff (pairs under 1 h: -73 %). Strong.
- Counter-evidence: Bitquery's share of coins sold at a profit is slightly higher for the earliest buyers (34 % in
  the first minute vs 28 % after a day; finding 1.6). That is a hit rate, not a mean return, and it does not include
  the size of the losses, but it is the one number pointing the other way.
- Expected direction: fewer losses. It does not make the remaining trades positive.
- Side effect: in learning mode this removes almost every P1 (`hype`) buy, since P1 fires from 30 minutes. State
  it as a change to the account only; P1's own forward record stays.

**R2. `no_active_promo` (veto).** No buy while `boosts.active > 0` in the `/tokens/v1` snapshot, or within 24 hours
of the coin last appearing on the boosts-latest, boosts-top or ads feeds (from the discovery log's feed tags).
- Evidence: finding 2.10 (paid promotion the worst group), the handoff (hype inversely predictive), 2.11-2.12. Weak
  to some.
- Expected direction: fewer losses.
- Side effect: in a live check of 12 Solana feed coins, all 12 had an active boost. So this rule means "wait until
  the paid push is over". The account will trade much less.

**R3. `no_flat_volume_burst` (veto).** No buy when `rvol = volume.h1 / (volume.h24 / 24) >= 5` and
`abs(priceChange.h1) < 5 %`.
- Evidence: the wash-trading screen in 2507.01963 (daily volume up >500 % with price change <5 %), here moved to
  1-hour data. The hourly version is untested. Weak.
- Expected direction: fewer buys into fake volume. It fired on none of 18 live coins, so its effect will be small.

**R4. `P5 aged_liquid` (new measuring policy).** Buy when all of:
`72 h <= age <= 14 d`; `liquidity.usd >= $50,000`; `liquidity.usd / marketCap >= 0.10`; `volume.h24 >= $50,000`;
`-15 % <= priceChange.h24 <= +25 %`; `-5 % <= priceChange.h1 <= +5 %`; no active boost; and every screen below
passes.
- Evidence: the decay is mostly over by 24 h (1.3); the handoff's 24-168 h bucket was about flat; Arrow Algo's late
  entries were about one cost below zero. The liquidity/market-cap floor has no study behind it; it only keeps exits
  out of thin pools.
- Expected direction: gross near 0, so net about **minus the cost**. It is here to measure the least-bad bucket, not
  because it is expected to pass. Promote only if it meets the full pass bar.

## Proposed screen upgrades (to be pre-registered before any test)

All keep MEME.md's rule: a missing value is `unknown`, and unknown is never a pass. Each is scored the same way:
screened shadows against unscreened shadows on the forward record.

**S1. Solana holder concentration** (RugCheck full report `GET /v1/tokens/{mint}/report`, keyless, checked live).
- Top-10 holders from `topHolders[]`, leaving out any holder whose `owner` is a pool (`markets[].pubkey`) or appears
  in `knownAccounts`. Sum of `pct` must be **<= 30 %**.
- `creatorBalance / token.supply` must be **<= 5 %**.
- Do **not** use `insiderNetworks`. It is undocumented, and on one live coin its `tokenAmount` added up to 127.6 % of
  supply, so the field cannot be read as a share.
- Evidence: 2.1, 2.4 (the 30 % line is the paper's definition of concentrated). Some. The 5 % creator line is a
  heuristic.
- Live pass rate: 8 of 12 Solana feed coins. Only the top-10 line did any work: `creatorBalance` was 0 on all 12
  (pump.fun devs usually sell or move tokens early), so the creator line caught nothing in this sample.

**S2. Liquidity lock on the traded pool.**
- Solana: the entry in `markets[]` whose `pubkey` equals the DexScreener `pairAddress`; its `lp.lpLockedPct` must be
  **>= 90**. Pool not listed: unknown. (Do not use the summary's `lpLockedPct`; on one coin it said 85 while the
  traded pool was 100 % burned.)
- EVM, Uniswap-v2-type pools (GoPlus `dex[].liquidity_type == "UniV2"`): sum of `lp_holders[].percent` where
  `is_locked == 1` or the address is `0x...dead` / `0x0` must be **>= 0.90**. For v3/v4 pools GoPlus cannot see NFT
  LP locks. This is an **explicit exception** to "unknown is never a pass": log it as `n/a`, do not block, and rely
  on S3. Without the exception every Clanker coin on Base would be blocked.
- Evidence: 1.8 (93 % of Raydium pools LP-swept), 3.6 (99 % of curve-skip pools emptied), 3.5. Some to strong.
- Live: all 12 Solana coins passed (pump.fun graduates burn LP; on the one Meteora pool a 100 % reading may not
  mean much), so on Solana this check did no work in this sample. It matters for EVM: 2 of 3 BSC/ETH v2 pools failed.

**S3. EVM owner powers and concentration** (GoPlus `token_security`, keyless).
- `creator_percent + owner_percent <= 0.05`.
- Top-10 of `holders[]`, leaving out `is_contract == 1`, `is_locked == 1` and dead addresses: **<= 0.30**. An empty
  `holders` list is unknown.
- None of `is_mintable`, `hidden_owner`, `can_take_back_ownership`, `owner_change_balance`, `transfer_pausable`,
  `slippage_modifiable`, `personal_slippage_modifiable`, `is_blacklisted`, `trading_cooldown`, `is_proxy`,
  `external_call` equal to `"1"`, unless `owner_address` is `0x0` or `0x...dead` (renounced).
- `honeypot_with_same_creator == "0"`.
- Evidence: 3.1-3.2 (GoPlus's honeypot flag misses 40 %; misses come from owner-changeable rules) and 3.5. Some.
- Live: failed both boosted BSC coins (one had 63.9 % creator share, the other `hidden_owner`, `transfer_pausable`
  and `slippage_modifiable` with 27 holders); passed one Ethereum coin with renounced ownership.

**S4. Trade-flow check** (GeckoTerminal `GET /networks/{net}/pools/{pool}/trades`, keyless, checked live: last 300
trades with `tx_from_address`, `kind`, `volume_in_usd`, `is_anomalous`).
- Needs >= 30 trades. Unique `tx_from_address` / trades **>= 0.30**; top-5 wallets **<= 50 %** of USD volume;
  wallets that both bought and sold in the window **<= 60 %** of USD volume.
- Evidence: direction from 2.5-2.6 (same address on both sides marks wash trading). All three thresholds are
  untested heuristics. Weak.
- Limits: on Solana `tx_from_address` is the signer, which may be a router or bot. The call shares GeckoTerminal's
  30/min budget with the OHLCV replay; in my live run most calls got HTTP 429 while the desk was running, so only 4
  of 18 coins had data (1 of 4 passed). Run it only at buy time and budget it, or this screen will block most buys.

**Combined effect, stated now so nobody is surprised later:** with R1, R2 and S1-S4 together, none of the 12 live
Solana feed coins would have been bought at the moment of the check. The account would trade only coins that stay
on the tracked list past 24 hours and past their paid boost. It will trade far less often than learning mode does
today. That is the expected price of following the evidence.

## What would NOT help (do not spend time here)

- **Predicting graduation** (socials, trade speed, dev history, SOL in the curve). It predicts graduation, and
  graduates then fall to a median 0.04x in a day (1.3). The one real-money test lost (2.9).
- **Sniping or buying the first minutes.** The desk polls every 60 seconds with keyless data; sniper bots act in
  the first slot. Early buys are where the losses are largest (1.4, 1.5).
- **Momentum and hype entries.** The handoff already found them inversely predictive; 2.4 shows most big risers
  were manipulated.
- **Following KOL calls, X mentions or DexScreener boosts as buy signals.** No credible study shows a gain; the
  evidence that exists points to losses (2.10-2.12). The data is not free anyway for X.
- **Copying "smart money" wallets.** No replicable after-cost gain (2.13); the needed wallet data is not in the free
  feeds.
- **A bigger ML model on the same features.** The best published one still lost 27 % (2.2), rug models do not
  transfer across venues (2.3), and the Arrow Algo dev-best rule flipped to -81 % out of sample.
- **Trusting the RugCheck score or GoPlus honeypot flag alone** (3.1, 3.4, 3.5).
- **Paying for Jito/MEV protection at $20-$100.** Sandwiches are rare at tight slippage; defensive tips cost users
  more than they saved in the IMC study.
- **Time-of-day or weekday rules.** No evidence either way was found.
- **Using `insiderNetworks` from RugCheck as a number** (see S1).

## Sources

Papers (arXiv):
- Marino et al., Predicting the success of new crypto-tokens: the Pump.fun case. https://arxiv.org/html/2602.14860v1
- Kamat, Pump.fun Graduation Regime Windows (v3, with corrigenda). https://arxiv.org/html/2607.02823v3
- Kamat, Coordinated Sniper Cohorts on Pump.fun (carries a withdrawal notice). https://arxiv.org/pdf/2607.02795
- MELT / MemeTrans: high-risk memecoin launch detection. https://arxiv.org/html/2602.13480v2
- Catching the Rug: early prediction of fraudulent memecoins on Solana. https://arxiv.org/html/2608.20271v1
- Mongardini & Mei, A Midsummer Meme's Dream (manipulation across 4 chains). https://arxiv.org/abs/2507.01963
- Meme Coin Factories: large-scale manipulation on pump.fun. https://arxiv.org/html/2609.10246
- Mancino, The Memecoin Phenomenon (Q4 2024 pump.fun). https://arxiv.org/html/2512.11850v3
- Pump-and-Dump meets Honeypot Tokens (GoPlus / Honeypot.is coverage). https://arxiv.org/html/2610.10149
- Resisting Manipulative Bots in Meme Coin Copy Trading. https://arxiv.org/html/2601.08641
- Gerzon et al., Quantifying the Threat of Sandwiching MEV on Jito (IMC). https://cnitarot.github.io/papers/imc26_solana.pdf
- Trapdoor tokens on Uniswap (GoPlus comparison; abstract gives no number). https://arxiv.org/abs/2309.04700
- Measuring Memecoin Fragility (large caps only; not used). https://arxiv.org/abs/2512.00377

On-chain research and postmortems:
- Bitquery, launchpad year. https://bitquery.io/investigations/solana-launchpad-year
- Bitquery, 74 % of buyers lose. https://bitquery.io/investigations/solana-memecoin-traders-lose-money
- Bitquery, sandwich attacks. https://bitquery.io/investigations/solana-sandwich-attacks
- MemeFees, graduate survival (live, snapshot 2026-11-20). https://memefees.com/research/memecoin-survival-2026
- Solidus Labs, Solana rug pulls and pump-and-dumps. https://www.soliduslabs.com/reports/solana-rug-pulls-pump-dumps-crypto-compliance
- Arrow Algo, 1,411 launches. https://arrowalgo.com/solana-memecoin-trading/
- Scorp Labs, dev-history signal. https://trader.scorplabs.online/blog/pump-fun-graduation-rate-dev-signal-12-percent
- memecoin-paper-bot (15 strategies). https://github.com/maxmckittrick1-oss/memecoin-paper-bot
- MEMESCOPE Boost Lab PR. https://github.com/kshetty851-bit/MEMESCOPE/pull/266

Fees:
- Coinpaprika launchpad fees (pump.fun tiers). https://coinpaprika.com/education/launchpad-fees/
- Clanker v4 static fee hook. https://clanker.gitbook.io/documentation/references/core-contracts/v4/clankerhook/clankerhookstaticfee

Live endpoints checked on 2026-11-20 (read-only, no keys): DexScreener `/token-boosts/latest/v1`,
`/token-profiles/latest/v1`, `/token-boosts/top/v1`, `/community-takeovers/latest/v1`, `/tokens/v1/{chain}/{addr}`;
RugCheck `/v1/tokens/{mint}/report` and `/report/summary`; GoPlus `/api/v1/token_security/{56,8453,1}`;
GeckoTerminal `/networks/{net}/pools/{pool}/trades`; public RPC `eth_gasPrice` (Ethereum, BSC, Base) and
`getRecentPrioritizationFees` (Solana); Jito `bundles/tip_floor`.
