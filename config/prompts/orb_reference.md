# QSC Grinder Prompt — ORB Reference Backtest

## Task
Write the strategy file exactly as shown below. Do NOT modify logic. Do NOT regenerate.
This is a reference backtest. Output the code verbatim.

## Reference Stats (for context only — do not embed in code)
- Backtest period: 2016
- Sharpe Ratio: 2.396
- Beta: -0.042
- SPY benchmark Sharpe: 0.836
- Source: https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/

## Code

# QSC-REFERENCE-CODE: USE VERBATIM

```python
from AlgorithmImports import *
from datetime import time

class OpeningRangeBreakoutAlgorithm(QCAlgorithm):

    def initialize(self):
        self.set_start_date(2016, 1, 1)
        self.set_end_date(2016, 12, 31)
        self.set_cash(100000)

        self.universe_settings.resolution = Resolution.MINUTE
        self.add_universe(self.coarse_selection)

        self.opening_range_end = time(9, 45)  # 15-min ORB window
        self.atr_threshold = 0.50             # min $0.50 ATR filter
        self.ranges = {}

    def coarse_selection(self, coarse):
        sorted_by_volume = sorted(
            [x for x in coarse if x.has_fundamental_data and x.price > 5],
            key=lambda x: x.dollar_volume, reverse=True
        )
        return [x.symbol for x in sorted_by_volume[:1000]]

    def on_data(self, data):
        for symbol in self.active_securities.keys:
            if not data.bars.contains_key(symbol):
                continue

            bar = data.bars[symbol]
            current_time = self.time.time()

            if current_time <= self.opening_range_end:
                if symbol not in self.ranges:
                    self.ranges[symbol] = {"high": bar.high, "low": bar.low}
                else:
                    self.ranges[symbol]["high"] = max(self.ranges[symbol]["high"], bar.high)
                    self.ranges[symbol]["low"] = min(self.ranges[symbol]["low"], bar.low)
                return

            if symbol not in self.ranges:
                continue

            orb = self.ranges[symbol]
            atr = orb["high"] - orb["low"]

            if atr < self.atr_threshold:
                continue

            if not self.portfolio[symbol].invested:
                if bar.close > orb["high"]:
                    self.set_holdings(symbol, 0.02)
                elif bar.close < orb["low"]:
                    self.set_holdings(symbol, -0.02)

            if current_time >= time(15, 45):
                self.liquidate(symbol)
```
