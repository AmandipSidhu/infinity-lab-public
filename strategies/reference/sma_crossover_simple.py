# Source: https://quantscripts.com/the-basic-moving-average-crossover-strategy/
# Proven to compile and backtest in QuantConnect
# 23 lines, SPY trading with SMA(10) / SMA(50) crossover
# Backtest period: 2021-01-01 to 2021-12-31
# Initial capital: $10,000

from AlgorithmImports import *

class MovingAverageCrossover(QCAlgorithm):

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
            self.SetHoldings(self.symbol, 1.0)
            self.Debug("Buy signal at {}".format(self.Time))
        elif self.Portfolio.Invested and self.short_sma.Current.Value < self.long_sma.Current.Value:
            self.Liquidate(self.symbol)
            self.Debug("Sell signal at {}".format(self.Time))
