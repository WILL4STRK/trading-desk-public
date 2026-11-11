# Account tab

The desk never connects to a broker, so it needs you to tell it two things:
your cash, and how each trade ended. The Account tab is where you do that.

## What it does

- **Balance.** Shows the cash the desk is using. Type the cash your broker
  shows, or drop, pick or paste (Cmd+V) a screenshot of your broker's balance
  screen. The desk reads the screenshot, shows every figure it found (for
  example Net Liquidating Value, Buying Power, Cash) as buttons, fills in the
  cash, and saves only when you press **Save**.
- **What happened in a trade.** One card per open trade. Drop a screenshot of
  the filled sell order, or type the price. The desk finds the sale of that
  stock, suggests the price and whether it was the stop or the target, and
  saves only when you press **Save what happened**.

## How a screenshot is read

1. The page sends the image to the desk on your own computer
   (`POST /api/screenshot`). Nothing is uploaded anywhere else.
2. The image is saved under `data/screenshots/`.
3. On a Mac, Apple's Vision text recognition reads it (`tools/ocr/ocr.swift`,
   built once into `build/ocr` with the Xcode command line tools). On other
   systems the reader is not available, so type the numbers instead.
4. `desk/screenshot.py` looks for balance labels (Net Liquidating Value, Cash
   & Sweep Vehicle, Buying Power, Cash) and for fill rows (BOT/SOLD or
   Buy/Sell, quantity, symbol, price). The labels match thinkorswim's screens;
   other brokers may need more label words added there.
5. Nothing is recorded until you press Save. If the reader finds nothing, the
   message says so and you type the number.
