# CloddsBot: what the desk took from it, and what it left

The desk borrowed ideas from [CloddsBot](https://github.com/alsk1992/CloddsBot) (MIT, by alsk1992).
Read on 2026-11-11 at commit `c930628` (12 Sep 2026). File references below are paths in
that repo. This is a description of software, not investment advice.

## Why it is ideas, not a merge

| | CloddsBot | Trading Desk |
| --- | --- | --- |
| Language | TypeScript, Node 22, many npm packages | Python 3.9, standard library only |
| Markets | Prediction markets (Polymarket, Kalshi and 8 more), crypto spot, perpetual futures up to 200x leverage, Solana and EVM DeFi, token launches | US stocks, long day trades, cash account |
| Brain | Needs a paid Anthropic API key (`ANTHROPIC_API_KEY`, README "Configuration") | Runs the 11 Day Trader skills as local scripts; no paid API |
| Trades | Bots can trade live by themselves | Never trades real money by itself; auto paper is practice only |

The desk is built for a beginner with a small cash account who is still paper trading. Prediction markets, crypto derivatives and margin are
for adults, and a second language and a paid API would make the desk harder to run, not better. So no
CloddsBot code was copied and no market was added. Its **risk and record-keeping ideas** were read, and the
ones that fit a small stock account were rebuilt in the desk's own Python.

## Things in CloddsBot that are unsafe to copy

Noted as source material, not as a judgement on its author:

- **Keys in a plain file.** Setup puts the Anthropic key, a Solana private key and exchange keys in `.env`
  (README "Configuration"). The desk has no broker keys at all.
- **Open to the network by default.** The gateway listens on `0.0.0.0` with `auth: { type: 'none' }` unless
  configured (`src/gateway/index.ts:141-145`). The desk listens on 127.0.0.1 only and checks the Host
  header (`desk/server.py`).
- **Real money with no person in the loop.** Bots, copy trading and "swarm trading" (20 wallets acting
  together on Pump.fun, README "Advanced Trading") can trade live. Coordinated wallets are close to
  market manipulation. This desk never sends a real order; you place every trade yourself.
- **Leverage and adult products**: perps up to 200x, prediction markets, token launches; the README also
  advertises a token contract address.
- **Broad agent tools**: shell, Docker, files, email, SMS (README "AI System"). Shell needs approval.
- **Risk numbers that start from a guess**: the safety manager starts from a $10,000 peak
  (`src/trading/safety.ts:160`), so its drawdown figure is wrong for a small account until the real value is
  recorded, and its daily-loss and drawdown stops switch trading back on by themselves after a 4-hour
  cooldown (`safety.ts:147`, `:336-345`).

## Every idea, one row each

"Desk has it" points at the desk's own file. "Built" means added in version 1.1.0.

| # | The idea in CloddsBot | Desk has it? | For a beginner paper trading stocks under $500 |
| --- | --- | --- | --- |
| 1 | **Daily loss limit**, in dollars or % (`src/trading/safety.ts` `dailyLossLimit`, `dailyLossLimitPct`) | **Yes**: `max_daily_loss_pct`, 4 % (`desk/engine.py` `halt_reason`) | Already there. |
| 2 | **Drawdown stop**: stop trading once this far under the peak (`safety.ts` `maxDrawdownPct` 20 %) | **Part**: the review showed the worst drawdown; nothing stopped trading | **Built.** `max_drawdown_pct`, 15 % of the best point (`desk/breaker.py`). A slide over many days is the thing a daily limit cannot see. |
| 3 | **Losing-streak breaker** (`src/execution/circuit-breaker.ts` `maxConsecutiveLosses` 5) | **No** | **Built, off by default** (`max_losing_streak` 0). Useful for live money; in paper it would freeze auto paper after an ordinary run of stops and starve the review of data, so it is your call to turn on. |
| 4 | **Loss over a longer window**: hourly, daily or weekly (`src/risk/circuit-breaker.ts:27`, `LossWindow`) | **No** (daily only) | **Built.** `max_weekly_loss_pct`, 8 % (two bad days), clears on Monday. |
| 5 | **Trips need a person, or reset on a timer** (`circuit-breaker.ts` `autoReset`; `safety.ts` 4-hour cooldown) | n/a | **Built the manual way.** The drawdown and losing-run stops stay on until you press **Reset stops**; the reset is logged. A timer would let the desk carry on before anyone read the record, which is the opposite of "check the log and make changes". |
| 6 | **Kill switch** (`safety.ts` `killSwitch`) | **Yes**: the Pause switch (`paused`, Desk tab) | Already there. |
| 7 | **Trades per day cap** (`circuit-breaker.ts` `maxDailyTrades` 100) | **Yes**: `max_trades_per_day`, 2 | Already there, and much tighter. |
| 8 | **Order size, exposure and concentration caps** (`src/trading/risk.ts`; `safety.ts` `maxConcentrationPct` 25 %) | **Yes**: `max_position_pct`, `cash_buffer_pct`, settled cash only (`desk/settings.py`, `engine.spendable`) | A 25 % cap does not fit $400 (one stock per trade). Nothing to add. |
| 9 | **Stop on a high error rate** (`circuit-breaker.ts` `high_error_rate`) | **Part**: Health says "Do not trust the list" after 3 failed scans (`desk/health.py`); a failed scan makes no signals; a failed risk check blocks the slip (`desk/pipeline.py`) | Enough. A broken data feed already produces no new signals. |
| 10 | **Pre-trade gate that fails closed** (`src/trading/pre-trade.ts`) | **Yes**: no clock = market closed; Risk Manager failure = blocked; no odds in live = weak (`desk/pipeline.py`) | Already there. |
| 11 | **Exits allowed while stopped** (`pre-trade.ts` `skipSizeLimit` for risk-reducing exits) | **Yes**: halts only stop new entries; stops, targets and the flat-by sale still run (`desk/auto.py`) | Already there; the new stops keep it that way (tested). |
| 12 | **Kelly sizing**, quarter Kelly capped at 25 %, bigger after 3 wins in a row (`src/trading/kelly.ts`) | **No**: fixed % risk per trade | **Rejected.** Kelly needs a long, reliable win rate and payoff; from 20 trades it is noise and it oversizes. The "win streak boost" sizes up after wins, beyond the % limit. A Kelly fraction above zero means the same as an average R above zero, which the review already prints. Fixed 2 % risk stays. |
| 13 | **Volatility regime sizing**: 1.2x when calm, 0.5x high, 0.25x extreme (`src/risk/volatility.ts`) | **No** | **Rejected as built there**: 1.2x sizes up past the limit. "Half size on volatile days" may be added later. |
| 14 | **VaR / CVaR** (`src/risk/var.ts`, 100-trade window) | **No** | **Rejected.** Every trade has a stop, so the planned worst loss is known; VaR needs about 100 trades. **Built instead**: best and worst trade and worst day in the review, the plain version of "how bad can it get". |
| 15 | **Stress tests** (flash crash, black swan…, `src/risk/stress.ts`) | **No** | **Rejected.** One or two positions, each with a stop, flat by 15:55: no overnight book to stress. Gaps through a stop already show as "stop exits against the planned stop". |
| 16 | **Risk dashboard** with how much of each limit is used (`src/risk/dashboard.ts`) | **Part**: Today's limits showed trades and the day's loss | **Built.** Today's limits also shows the week's loss, the drop from the best point and the losing run, and the Reset stops button when one is on. |
| 17 | **Decision ledger**: every yes and no with the rule, threshold and actual value (`src/ledger/types.ts` `ConstraintEvaluation`) | **Yes, mostly**: each slip keeps its problems, warnings, Risk Manager verdict and reasons, and its rule stamp; every scan and stop trip is logged (`desk/journal.py`) | **Built**: the review counts why signals were held back (edge under the floor, no odds, stop too close, budget, risk manager, too late…) and how those would have done on the replay. |
| 18 | **Confidence calibration**: accuracy by confidence bucket (`src/ledger/storage.ts` `calculateCalibration`) | **Part**: one overall "did the odds hold up" line (`desk/review.py` `odds_check`) | **Built**: odds by edge band. If higher Time Machine edges do no better on the replays, the `min_edge_points` floor is not sorting good from bad. |
| 19 | **Tamper-proof hashes, anchored on a blockchain** (`src/ledger/hash.ts`, `anchor.ts`) | No | **Rejected.** A one-person record on one Mac; anchoring needs crypto wallets. |
| 20 | **Backtest report**: profit factor, Sharpe, Sortino, Calmar, Monte Carlo (`src/trading/backtest.ts`) | **Part**: win rate, payoff, average R, drawdown, losing run | **Built: profit factor.** Sharpe and Sortino from a few trades a week are noise. A Monte Carlo reshuffle of the record would be worth adding once there are 50 or more closed trades. |
| 21 | **Commission and slippage in the backtest** (`backtest.ts` `commissionPct`, `slippagePct`) | **Yes**: `est_cost_per_share` on every slip (`desk/costs.py`) | Already there. |
| 22 | **Strategy backtesting on history** (`backtest.ts`) | **Yes**: the Time Machine replays each signal's plan 10,000 times over 60 days; after the close every signal is replayed on the day (`desk/outcome.py`) | Already there, per signal. |
| 23 | **Several AI agents** (Main, Trading, Research, Alerts; `src/agents`) | **No**, on purpose | **Rejected.** Needs a paid API key, and the desk must not change itself. The review stays "check the log and make changes" by a person; the 11 skills are the desk's specialists. |
| 24 | **Alerts to 21 chat apps** (`src/alerts/index.ts`, `src/channels`) | **Part**: Price Alerts skill and the dashboard's event list | **Rejected.** Telegram or Discord need bot tokens and would send account details off the Mac. A Mac notification could be added later if wanted; nothing here needs it, since auto paper acts on its own. |
| 25 | **Dry run by default** (`src/trading/bots/index.ts:594`, `dryRun ?? true`) | **Yes**: paper mode by default, auto paper practice-only, no way to send a real order | Already there. |
| 26 | **Encrypted credentials** (AES-256-GCM) | n/a: the desk keeps no broker keys | Nothing to add. |
| 27 | **Paper trading** (`src/agents/handlers/paper-trading.ts`) | **Yes**: `desk/paper.py`, `desk/auto.py` | Already there. |
| 28 | **Copy trading and whale tracking** | **Part**: the Pelosi tracker lists filings, read only | **Rejected.** Copying other traders' orders is not a rule a beginner can check. |
| 29 | **Prediction markets, crypto, perps, arbitrage, token launches, DeFi, Bittensor mining** | No | **Rejected.** Not stocks, adult-only, leveraged or both. |

## What was built

1. **Risk stops** (`desk/breaker.py`, `tests/test_breaker.py`, 19 tests). Settings: `max_weekly_loss_pct`
   8, `max_drawdown_pct` 15, `max_losing_streak` 0 (off); 0 turns any of them off. They are checked in
   `engine.halt_reason`, the same place as the daily loss limit, so the dashboard, recording a fill by
   hand and auto paper all obey them. They stop new entries only: no trade is closed, nothing is sent,
   and no trade's size changes (a test checks the sizing limits and the skills' config are identical with
   them on or off). They are rule settings, so changing one starts a new rule-set code. Paper and live
   each have their own record, so each has its own stops and its own reset.
2. **Review** (`desk/review.py`, `tests/test_review.py`): odds by edge band, why signals were held back,
   the stop trips and resets, profit factor, best and worst trade, worst day. Shown in `python3 -m desk
   review` and the Journal's full report; the Results card shows profit factor and worst day.

Defaults with no trading record behind them yet: weekly 8 % (twice
the daily limit), drop from the best point 15 % (about seven full losses at 2 % risk), losing run off.

## Not verified

- The stops have not been seen tripping in a live market; they were tested with the saved demo day and
  made-up trades.
- No idea here has been shown to make or save money. They limit how much a bad run can cost and give the
  review more to read.
