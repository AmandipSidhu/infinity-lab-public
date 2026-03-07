# Aider Level 1 Validation — Tier 2 Output
# Tier 2 prompt: previous Sharpe 0.42, issue: no warmup period, fix: add 50-day warmup
# Simulated Sharpe: 0.61 — warmup reduces early noise but exit timing still lagged

from AlgorithmImports import *


class SmaCrossoverTier2(QCAlgorithm):

    def Initialize(self):
        self.SetStartDate(2021, 1, 1)
        self.SetEndDate(2021, 12, 31)
        self.SetCash(10000)

        self.symbol = self.AddEquity("SPY", Resolution.Daily).Symbol
        self.short_sma = self.SMA(self.symbol, 10, Resolution.Daily)
        self.long_sma = self.SMA(self.symbol, 50, Resolution.Daily)

        self.SetWarmUp(50)

    def OnData(self, data):
        if self.IsWarmingUp:
            return

        if not self.Portfolio.Invested and self.short_sma.Current.Value > self.long_sma.Current.Value:
            self.SetHoldings(self.symbol, 0.9)
            self.Debug("Buy signal at {}".format(self.Time))
        elif self.Portfolio.Invested and self.short_sma.Current.Value < self.long_sma.Current.Value:
            self.Liquidate(self.symbol)
            self.Debug("Sell signal at {}".format(self.Time))
