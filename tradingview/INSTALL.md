# Put the Trading Desk study on a TradingView chart

`TradingDesk_ORB.pine` draws the same opening-range-breakout levels as the
desk and the thinkorswim study: the opening range, the breakout candle, the
buy-limit ceiling, the stop and the target. It is an **indicator**, not a
strategy. The study itself cannot place, change or cancel an order.

You still place every order yourself, at your broker.

> **Checked inside TradingView on 2026-11-10** (free Basic plan, Chrome on a
> Mac). It compiled with no errors and drew the opening range and the info box
> on a 5-minute AAPL chart. If the Pine Editor ever shows a red error, copy the
> line number and message and open an issue on this project.

## 1. Add the study (about 2 minutes)

1. Open [tradingview.com](https://www.tradingview.com) and **sign in first**.
   Then open a chart: for example
   `https://www.tradingview.com/chart/?symbol=NASDAQ:AAPL&interval=5`.
   (If you opened the chart before signing in, open it again in a new tab:
   see "Nothing draws" below.)
2. In the toolbar down the **right edge** of the window, click the
   **Pine Editor** button. The editor opens as a panel on the right with an
   "Untitled script".
3. If another script is open instead, click the script's name at the top of
   the editor and choose **Create new > Indicator**.
4. Click inside the editor, press **Cmd+A** to select everything, then
   **Delete**.
5. On your Mac, open `tradingview/TradingDesk_ORB.pine` in TextEdit, press
   **Cmd+A**, **Cmd+C**. Back in the Pine Editor, press **Cmd+V**. The last
   line with text starts with `alertcondition(`.
6. Press **Cmd+S**. A **Save script** box opens with the name
   `Trading Desk ORB` already filled in. Click **Save**.
7. Click the **Add to chart** button (the ▷ next to the script's name). The
   chart's legend shows `TD ORB 15 2 0.2 1,530`. The button becomes
   **Update on chart** (Cmd+Enter) for later edits.
8. Click the editor's **–** (minimize) at its top right to get the chart back.
   The opening-range lines appear once the first 15 minutes of the session are
   done, with a box at the top right such as "Range 350.12 to 353.81: waiting
   for a close above 353.81".
9. Click **Save** under the layout name at the top right of the chart, so the
   study is still there next time.

### Nothing draws (every value is ∅)

If the legend shows `TD ORB` but every value after it is ∅ and no lines draw,
even mid-afternoon, the chart page was loaded before you signed in and its
scripts are not being calculated. Open the chart again in a new tab (the
study is saved to your account: open the Pine Editor, click the script's name,
pick **Trading Desk ORB** under Recently used, then **Add to chart**).
Seen on 2026-11-10: the same happened to TradingView's own
default `plot(close)` script in that tab, so it is not the study.

### Pop-ups you can close

On the free plan TradingView shows upgrade screens (for example "Ad blocker
detected" if your Mac blocks ads, or one when you use a paid feature). Close
them with the **×** at the top right. Nothing in these steps needs a paid
plan.

## 2. Set the chart up the way the desk reads it

1. Click the time-frame button at the top left of the chart (it shows **1D**
   or similar) and pick **5 minutes**. The info box at the top right warns
   you if the chart is on anything else.
2. Times are worked out in New York time inside the script, so your chart's
   own time zone does not matter.
3. Open the study's settings (hover its name on the chart, click the gear) and
   check the **Inputs** tab matches the desk's Settings:

   | TradingView input | Desk setting | Default |
   | --- | --- | --- |
   | Opening range, minutes | Opening range (`or_minutes`) | 15 |
   | Target, in R | Target (`target_r`) | 2.0 |
   | Chase room, in R | Chase room (`chase_r`) | 0.2 |
   | No entries after (HHMM, New York) | No entries after (`no_entries_after`) | 1530 |
   | Stop at range low | Stop rule: off = `or-mid`, on = `or-low` | off |

   If you change a setting in the desk, change it here too, and in the
   thinkorswim study.

## 3. Set an alert (needs a paid TradingView plan)

**The free Basic plan cannot keep this alert.** Checked on 2026-11-10: the
alert window fills in correctly, but **Create** opens an upgrade screen ("a
limit of 0 technical alerts in your plan"; Essential allows 20) and then
"Alert saving failed". Alerts built on an indicator need a paid plan. On the
free plan, watch the chart and the desk instead.

On a paid plan:

1. Click **Alert** (the alarm-clock icon) in the top toolbar, or press
   **Option+A**. The window is titled **Create alert on AAPL** (your symbol).
2. Open the first **Condition** menu (it says **Price**) and pick
   **TD ORB (15, 2, 0.2, 1,530)**. The next menu fills in
   **Trading Desk breakout**, **Interval** says **Same as chart: 5 minutes**,
   and **Message** fills in "Opening-range breakout on {{ticker}} ...".
3. Set **Trigger** to **Once per bar close** so it fires only after a
   5-minute candle has closed above the range.
4. **Notifications** defaults to **App, Toasts**. Leave any webhook off: the
   desk only listens on your Mac, so TradingView cannot reach it.
5. Click **Create**. Make one alert per stock you are watching. Alerts expire
   after about a month (the **Expiration** line).

An orange **!** in the window says the alert "may function differently than
expected" because an indicator can repaint. That is TradingView's general
warning for indicator alerts. This study only marks a breakout on a closed
candle, and Once per bar close waits for the close too.

An alert only tells you to look. When it fires, check the Trading Desk
dashboard: the desk also checks your cash, the daily limits and the odds
before it shows a ticket.

## Limits to know

- The free TradingView plan limits how many indicators can sit on one chart
  and allows **no indicator alerts** (checked 2026-11-10). Check
  TradingView's pricing page if the study will not add.
- The free plan cannot **Upload list…** into a watchlist (checked
  2026-11-10: it opens an upgrade screen). Add tickers with the watchlist's
  **+** one at a time, or use the desk's **Chart in TradingView** links.
- The study ignores early-close days and holidays the same way the
  thinkorswim study does; on a half day, treat any line after 13:00 as noise.
- The study draws today's levels only from candles the chart has. With
  extended hours shown, pre-market candles are ignored by the range.
