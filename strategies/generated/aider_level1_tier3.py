# Aider Level 1 Validation — Tier 3 Output
# Tier 3 prompt: previous Sharpe 0.61, issue: exit timing lag, fix: tighten crossover logic
# Simulated Sharpe: 0.78 — tighter logic improves but position sizing not optimal

from AlgorithmImports import *


class SmaCrossoverTier3(QCAlgorithm):

    def Initialize(self):
        self.SetStartDate(2021, 1, 1)
        self.SetEndDate(2021, 12, 31)
        self.SetCash(10000)

        self.symbol = self.AddEquity("SPY", Resolution.Daily).Symbol
        self.short_sma = self.SMA(self.symbol, 10, Resolution.Daily)
        self.long_sma = self.SMA(self.symbol, 50, Resolution.Daily)

        self.SetWarmUp(50)
        self._prev_short = None
        self._prev_long = None

    def OnData(self, data):
        if self.IsWarmingUp:
            return

        short_val = self.short_sma.Current.Value
        long_val = self.long_sma.Current.Value

        if self._prev_short is not None and self._prev_long is not None:
            bullish_cross = self._prev_short <= self._prev_long and short_val > long_val
            bearish_cross = self._prev_short >= self._prev_long and short_val < long_val

            if not self.Portfolio.Invested and bullish_cross:
                self.SetHoldings(self.symbol, 0.95)
                self.Debug("Bullish crossover at {}".format(self.Time))
            elif self.Portfolio.Invested and bearish_cross:
                self.Liquidate(self.symbol)
                self.Debug("Bearish crossover at {}".format(self.Time))

        self._prev_short = short_val
        self._prev_long = long_val
