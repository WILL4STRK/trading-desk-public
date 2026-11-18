# Trading Desk: opening-range breakout
# Draws the same levels the Trading Desk dashboard uses, so the chart and the
# signal agree: the opening range, the breakout candle, the buy-limit ceiling,
# the stop and the target. It draws lines only. It cannot place an order.
#
# Use it on a 5-minute chart of regular trading hours (the desk reads 5-minute candles).
# Keep the four inputs equal to the same four settings in the dashboard.

input orMinutes = 15;        # dashboard: opening range, minutes
input targetR = 2.0;         # dashboard: Target, in R
input chaseR = 0.2;          # dashboard: Chase room, in R
input lastEntryTime = 1530;  # dashboard: No entries after (New York time)
input stopAtRangeLow = no;   # no = stop at the range midpoint (or-mid), yes = range low (or-low)
input showLabels = yes;
input soundAlert = yes;
input showShorts = no;       # dashboard: Signals to look for = short or both. Draws the short side too.

def sinceOpen = SecondsFromTime(0930);
def inRange = sinceOpen >= 0 and sinceOpen < orMinutes * 60;
def rangeStart = inRange and !inRange[1];
def afterRange = sinceOpen >= orMinutes * 60 and SecondsTillTime(1600) > 0;

def orHigh = if rangeStart then high else if inRange then Max(high, orHigh[1]) else orHigh[1];
def orLow = if rangeStart then low else if inRange then Min(low, orLow[1]) else orLow[1];
def orMid = (orHigh + orLow) / 2;

# The trigger: the first candle after the range that CLOSES above the range high.
# A wick above the high is not a trigger.
def canEnter = afterRange and SecondsTillTime(lastEntryTime) > 0;
def state = if rangeStart or inRange then 0
            else if state[1] == 0 and canEnter and close > orHigh then 1
            else if state[1] >= 1 then 2
            else 0;
def isTrigger = state == 1;

def entryPrice = if rangeStart or inRange then Double.NaN else if isTrigger then close else entryPrice[1];
def stopPrice = if stopAtRangeLow then orLow else Floor(orMid * 100) / 100;
def riskPerShare = entryPrice - stopPrice;
def targetPrice = Round(entryPrice + targetR * riskPerShare, 2);
def limitPrice = Floor((entryPrice + chaseR * riskPerShare) * 100) / 100;
def tradeOn = state >= 1 and afterRange;

plot RangeHigh = if afterRange then orHigh else Double.NaN;
plot RangeLow = if afterRange then orLow else Double.NaN;
plot Stop = if tradeOn then stopPrice else Double.NaN;
plot BuyLimit = if tradeOn then limitPrice else Double.NaN;
plot Target = if tradeOn then targetPrice else Double.NaN;
plot Breakout = if isTrigger then low else Double.NaN;

RangeHigh.SetDefaultColor(CreateColor(27, 42, 74));
RangeLow.SetDefaultColor(CreateColor(27, 42, 74));
RangeHigh.SetStyle(Curve.SHORT_DASH);
RangeLow.SetStyle(Curve.SHORT_DASH);
Stop.SetDefaultColor(CreateColor(150, 57, 44));
Stop.SetLineWeight(2);
BuyLimit.SetDefaultColor(CreateColor(168, 101, 10));
BuyLimit.SetStyle(Curve.MEDIUM_DASH);
Target.SetDefaultColor(CreateColor(47, 93, 58));
Target.SetLineWeight(2);
Breakout.SetPaintingStrategy(PaintingStrategy.ARROW_UP);
Breakout.SetDefaultColor(CreateColor(47, 93, 58));
Breakout.SetLineWeight(3);

AddCloud(if tradeOn then Target else Double.NaN, if tradeOn then BuyLimit else Double.NaN, CreateColor(220, 231, 213), CreateColor(220, 231, 213));
AddCloud(if tradeOn then BuyLimit else Double.NaN, if tradeOn then Stop else Double.NaN, CreateColor(240, 216, 208), CreateColor(240, 216, 208));

AddLabel(showLabels and afterRange and state == 0, "Range " + AsText(orLow) + " to " + AsText(orHigh) + ": waiting for a close above " + AsText(orHigh), CreateColor(27, 42, 74));
AddLabel(showLabels and tradeOn, "Buy at " + AsText(limitPrice) + " or lower", CreateColor(168, 101, 10));
AddLabel(showLabels and tradeOn, "Stop " + AsText(stopPrice), CreateColor(150, 57, 44));
AddLabel(showLabels and tradeOn, "Target " + AsText(targetPrice), CreateColor(47, 93, 58));
AddLabel(showLabels and tradeOn, "Risk a share " + AsText(riskPerShare), Color.GRAY);

Alert(soundAlert and isTrigger, "Opening-range breakout: check the Trading Desk", Alert.BAR, Sound.Ding);

# ---- Short side: opening-range breakdown. Drawn only when showShorts = yes. ----
# The long rule mirrored: the first candle after the range that CLOSES below the range low.
# The stop is ABOVE the entry and rounds up to the cent, the target is below, and the
# sell limit is the lowest price worth selling at. Lines only. It cannot place an order.
def sState = if rangeStart or inRange then 0
             else if sState[1] == 0 and canEnter and close < orLow then 1
             else if sState[1] >= 1 then 2
             else 0;
def isBreakdown = showShorts and sState == 1;
def sEntry = if rangeStart or inRange then Double.NaN else if sState == 1 then close else sEntry[1];
def sStop = if stopAtRangeLow then orHigh else Ceil(orMid * 100 - 0.000001) / 100;
def sRisk = sStop - sEntry;
def sTarget = Round(sEntry - targetR * sRisk, 2);
def sLimit = Ceil((sEntry - chaseR * sRisk) * 100 - 0.000001) / 100;
def shortOn = showShorts and sState >= 1 and afterRange;

plot ShortStop = if shortOn then sStop else Double.NaN;
plot SellLimit = if shortOn then sLimit else Double.NaN;
plot ShortTarget = if shortOn then sTarget else Double.NaN;
plot Breakdown = if isBreakdown then high else Double.NaN;

ShortStop.SetDefaultColor(CreateColor(150, 57, 44));
ShortStop.SetLineWeight(2);
ShortStop.SetStyle(Curve.LONG_DASH);
SellLimit.SetDefaultColor(CreateColor(168, 101, 10));
SellLimit.SetStyle(Curve.MEDIUM_DASH);
ShortTarget.SetDefaultColor(CreateColor(47, 93, 58));
ShortTarget.SetLineWeight(2);
ShortTarget.SetStyle(Curve.LONG_DASH);
Breakdown.SetPaintingStrategy(PaintingStrategy.ARROW_DOWN);
Breakdown.SetDefaultColor(CreateColor(150, 57, 44));
Breakdown.SetLineWeight(3);

AddLabel(showLabels and showShorts and afterRange and sState == 0, "Short side: waiting for a close below " + AsText(orLow), CreateColor(27, 42, 74));
AddLabel(showLabels and shortOn, "SHORT: sell at " + AsText(sLimit) + " or higher", CreateColor(168, 101, 10));
AddLabel(showLabels and shortOn, "Buy stop " + AsText(sStop), CreateColor(150, 57, 44));
AddLabel(showLabels and shortOn, "Short target " + AsText(sTarget), CreateColor(47, 93, 58));
AddLabel(showLabels and shortOn, "Short risk a share " + AsText(sRisk) + " (not capped if it gaps)", Color.GRAY);

Alert(soundAlert and isBreakdown, "Opening-range breakdown: check the Trading Desk", Alert.BAR, Sound.Ding);
