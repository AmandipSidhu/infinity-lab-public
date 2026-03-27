# Quick Strike Coder — Strategy Prompt Queue
#
# Format: ## [TAG] Strategy Name
# Tags: [PRIORITY], [INDEPENDENT], [IF-PREVIOUS-PASSED], [LOW-PRIORITY]
#
# Push this file to trigger the QSC grinder workflow.
# Last triggered: 2026-03-27T06:59 UTC

## [PRIORITY] ORB 15min Base

Build an Opening Range Breakout (ORB) strategy using SPY on 15-minute bars.

Rules:
- Define the opening range as the first 15-minute bar of each trading day
- Enter long when price breaks above the high of the opening range
- Enter short when price breaks below the low of the opening range
- Use a stop-market order for entry to avoid chasing
- Set stop-loss at the opposite side of the opening range
- Exit all positions 30 minutes before market close
- Risk 1% of portfolio per trade (size positions accordingly)
- Backtest on SPY from 2020-01-01 to 2023-12-31

## [IF-PREVIOUS-PASSED] ORB Volume Filter

Take ORB 15min Base and add a volume filter:
- Only enter a breakout if the volume on the breakout bar is at least 1.5x the 20-bar average volume
- Use the ATR(14) on daily resolution to confirm the breakout bar has meaningful range
- All other rules remain the same

## [INDEPENDENT] VWAP Mean Reversion

Build a VWAP mean-reversion strategy on QQQ using 5-minute bars.

Rules:
- Calculate VWAP for each trading day (reset at market open)
- Enter long when price falls more than 0.5% below VWAP and RSI(14) is below 35
- Enter short when price rises more than 0.5% above VWAP and RSI(14) is above 65
- Use a limit order entry at VWAP level
- Exit when price returns to VWAP or after 60 minutes, whichever comes first
- Maximum 2 trades per day (long + short combined)
- Backtest on QQQ from 2020-01-01 to 2023-12-31

## [IF-PREVIOUS-PASSED] VWAP Trend Filter

Take VWAP Mean Reversion and add a trend filter:
- Only take long entries when price is above the 50-period EMA on the same timeframe
- Only take short entries when price is below the 50-period EMA
- Add an ADX(14) filter: only enter when ADX < 25 (mean-reverting environment)
- All other rules remain the same

## [LOW-PRIORITY] Gap Fade SPY

Build a gap-fade strategy on SPY using daily bars.

Rules:
- Detect gaps: if today's open is more than 0.5% above previous close → short the gap
- Detect gaps: if today's open is more than 0.5% below previous close → long the gap
- Entry: market order at open
- Target: close price of the previous day (gap fill)
- Stop: 1.5x the gap size beyond the open price
- Exit: if gap is not filled by market close, exit at close
- Maximum 1 trade per day
- Only trade when SPY volume at open is above 30-day average
- Backtest from 2019-01-01 to 2023-12-31
