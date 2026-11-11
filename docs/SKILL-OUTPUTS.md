# What each skill script returns

Recorded 2026-11-09 by running the vendored scripts (commit in `vendor/PINNED_COMMIT`)
against their own fixtures at `--now "2026-10-19 10:15"`. This is the contract
`desk/pipeline.py` reads. If the skills are updated, rerun the
tests (`tests/test_desk.py` pins the shapes and values the desk relies on).

All calls go through `desk/skills.py` (called `SkillRunner` below).

## Rules that hold for every script

- With `--json`, the answer is on stdout as one JSON value.
- **A non-zero exit is not always a failure.** Budget exits 1 on a refusal and
  risk exits 1 on a BLOCK, both with full JSON. `SkillRunner` treats "JSON
  parsed" as `ok`, and keeps `returncode`.
- A real error prints a one-line message that is not JSON (for example
  `entry.py: the chart file holds TSLA, not ZZZZ`). That gives `ok=False`.
- Scripts read and write files under `trader/` in the working folder. Each
  symbol gets its own working folder, so one symbol's files never mix with
  another's.
- `--write` on budget with a refusal does **not** remove an older
  `trader/orders.json`. Call `SkillRunner.clear_outputs()` before a rebuild and
  never run risk after a refusal.

## Inputs we write

`trader/config.json`: from `config.skill_config()`. `broker` is `"none"`.

`trader/portfolio.json`: `{"cash": 400.0, "positions": [{"symbol": "ABCD", "shares": 3}]}`

## alerts.py clock

`{now_new_york, date, weekday, calendar_known, open: bool, reason, early_close: null | "day after Thanksgiving", opens: "09:30", closes: "16:00" | "13:00", next_open}`

`opens` and `closes` are missing when the market is closed that day.

## scan.py daily

`{mode: "daily", as_of, universe, scanned, failed, filters: {min_price, min_volume}, hits: [...], counts: {...}}`

A hit: `{symbol, setups: ["oversold" | "breakout" | "flag" | ...], price, close_date, rsi?, volume}`

Also writes `trader/watchlist.json`. There is no maximum-price option, so the
pipeline filters hits by price itself.

## news.py

`{checked, moves: [{ticker, price, prev_close, pct, time, moving}], items: [...], notes: []}`

An item: `{id, source, kind: "headline" | "filing", ticker, title, summary, link, time, touches: [tickers], tags: [], new: bool}`

## entry.py check SYMBOL

```
{symbol, setup: "orb", date, interval: "5m", or_minutes,
 status: "triggered" | "waiting" | (others, see below), phase, reason,
 opening_range: {high, low, minutes, bars, of, complete, avg_volume},
 level, trigger: null | {time: "10:15 AM", close_time: "10:15", bar_start, bar_index, price, volume, volume_x, pct_over},
 last: {bar_start, close, vs_level_pct},
 forming: {bar_start, closes, price},
 prev_close, data_time: "2026-10-19 10:49:45", opens, closes, early_close,
 no_entries_before, no_entries_after, bars_closed, minutes_since}
```

- `status` values seen: `triggered`, `waiting`. The skill also documents
  expired and not-open states; the pipeline treats any status it does not know
  as "not triggered" and shows the skill's `reason`.
- `reason` is plain words and is shown to the user as it is.
- With `--write`, a trigger is saved to `trader/entry.json` for exit.py.

## exit.py plan

```
{symbol, side: "buy", date, setup, entry, entry_time, entry_bar, interval,
 opening_range: {high, low, minutes},
 stop, stop_rule: "or_mid", stop_label, stop_detail,
 target, target_r, target_label, risk_per_share, reward_risk, flat_by,
 note, warnings: []}
```

Saved to `trader/plan.json`. Tesla example: entry 364.18, stop 358.32, target
370.04 at 1R (375.90 at 2R), risk 5.86 a share.

## timemachine.py run

```
{verdict: "Close to a coin flip: target first 29.4 % vs stop first 25.5 %.",
 verdict_notes: [...], warnings: [], note,
 mc: {replays: 10000, seed: 19, p_target, p_stop, p_neither, p_green,
      median_close, p5_close, p95_close, avg_R, until, pool_sessions, ...},
 history: {sessions, triggered, target, stop, close, green, avg_R},
 plan: {...}, data: {...}}
```

`p_*` are fractions from 0 to 1.

## size.py (budget)

```
{sizing: {symbol, entry, stop, target, risk_per_share, reward_risk,
          budget, cash_buffer, cash_available, max_loss_per_trade_usd,
          limits: [{name, usd, per, shares, how, binds}],
          qty, bound_by, cost, max_loss, flat_by, order_type,
          refusal: null | "one share risks $5.86 ... No order. ..."},
 orders: [] | [{symbol, side, qty: "8", type: "market", time_in_force: "day",
                order_class: "bracket", take_profit_limit_price, stop_loss_stop_price, why, plan}],
 notes: []}
```

A refusal has `qty: 0`, `orders: []` and exit code 1.

## check.py (risk)

```
{date, now_new_york, pass: n, block: n, warnings: [], cash_after,
 results: [{index, symbol, side, kind: "entry", amount, value_usd, stop, target,
            risk_per_share, max_loss, reward_risk, fingerprint,
            verdict: "PASS" | "BLOCK", reasons: [...], warnings: [], info: [...]}],
 today: {orders_placed, entries_placed, loss_at_risk, day_trades_5d}}
```

`reasons` and `info` are plain words. BLOCK exits 1.

## ticket.py show

A list: `[{index, ready: bool, status, plain, fingerprint, why, bracket, max_loss, stop, tool, tool_input, ...}]`

- `plain`: `"BUY 8 TSLA, market, good for today / stop-loss 358.32 / take-profit 370.04 / max loss about $46.88 ..."`
- The non-JSON text output talks about Alpaca and about an AI assistant placing the
  order. **Never show that text.** Trading Desk writes its own ticket for
  your broker from the order's fields, and uses this call only to confirm
  `ready`.
- `SkillRunner` refuses every ticket.py command except `show`.

## alerts.py add --from-plan, check, quote

- `add --from-plan` prints text and adds three rules (stop, target, flat-by) to `trader/alerts.json`.
- `check`: `{market: {clock fields}, rings: [...], errors: [], quotes: {TSLA: 366.475}}`
- `quote`: `{quotes: {TSLA: {symbol, price, open, high, low, prev_close, time, currency, source}}, errors: []}`

## Not yet recorded

`facts.py` (analyst) and `ptr.py new` (Congress filings) have not been run
here. Record their shapes before the side panels are built.
