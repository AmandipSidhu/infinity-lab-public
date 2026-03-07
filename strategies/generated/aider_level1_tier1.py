# Aider Level 1 Validation — Tier 1 Output
# Tier 1 prompt: basic SMA(10)/SMA(50) crossover on SPY, no warmup
# Simulated Sharpe: 0.42 — no warmup period causes early noise trades

from AlgorithmImports import *


class SmaCrossoverTier1(QCAlgorithm):

    def Initialize(self):
        self.SetStartDate(2021, 1, 1)
        self.SetEndDate(2021, 12, 31)
        self.SetCash(10000)

        self.symbol = self.AddEquity("SPY", Resolution.Daily).Symbol
        self.short_sma = self.SMA(self.symbol, 10, Resolution.Daily)
        self.long_sma = self.SMA(self.symbol, 50, Resolution.Daily)

    def OnData(self, data):
        if not self.short_sma.IsReady or not self.long_sma.IsReady:
            return

        if not self.Portfolio.Invested and self.short_sma.Current.Value > self.long_sma.Current.Value:
            self.SetHoldings(self.symbol, 0.8)
        elif self.Portfolio.Invested and self.short_sma.Current.Value < self.long_sma.Current.Value:
            self.Liquidate(self.symbol)
