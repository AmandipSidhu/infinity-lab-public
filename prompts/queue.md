# Quick Strike Coder — Strategy Prompt Queue
#
# Format: ## [TAG] Strategy Name
# Tags: [PRIORITY], [INDEPENDENT], [IF-PREVIOUS-PASSED], [LOW-PRIORITY]
#
# Push this file to trigger the QSC grinder workflow.
# Last triggered: 2026-03-27 1:36PM PST

## [PRIORITY] Validate and Backtest

Check this code for proper syntax and run a backtest on it and present the sharpe ration

from AlgorithmImports import *

class OpeningRangeBreakout(QCAlgorithm):

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
        # Top 1000 liquid US equities
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

            # Build opening range during first 15 minutes
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

            # ATR filter — skip low-volatility stocks
            if atr < self.atr_threshold:
                continue

            # Breakout entry
            if not self.portfolio[symbol].invested:
                if bar.close > orb["high"]:
                    self.set_holdings(symbol, 0.02)   # 2% per position
                elif bar.close < orb["low"]:
                    self.set_holdings(symbol, -0.02)

            # Exit at close
            if current_time >= time(15, 45):
                self.liquidate(symbol)
