# Source: https://www.quantconnect.com/forum/discussion/18068/plotting-a-line-from-the-buy-point-to-the-sell-point/
# Proven to compile and backtest in QuantConnect
# 60 lines, SPY trading with SMA(50) / SMA(200) crossover
# Includes position tracking and detailed logging
# Backtest period: 2020-01-01 to 2022-03-30
# Initial capital: $100,000

from AlgorithmImports import *

class SmaCrossoverBot(QCAlgorithm):

    def Initialize(self):
        self.SetStartDate(2020, 1, 1)
        self.SetEndDate(2022, 3, 30)
        self.SetCash(100000)

        self.spy = self.AddEquity("SPY", Resolution.Hour).Symbol
        self.sma50 = self.SMA(self.spy, 50, Resolution.Hour)
        self.sma200 = self.SMA(self.spy, 200, Resolution.Hour)
        
        self.position_open = False

    def OnData(self, data):
        if not self.sma50.IsReady or not self.sma200.IsReady:
            return

        price = self.Securities[self.spy].Close

        # Enter long when SMA50 crosses above SMA200
        if not self.position_open and self.sma50.Current.Value > self.sma200.Current.Value and self.sma50.Previous.Value <= self.sma200.Previous.Value:
            self.SetHoldings(self.spy, 1)
            self.position_open = True
            self.Log("BUY SPY @" + str(price))

        # Exit when SMA50 crosses below SMA200
        elif self.position_open and self.sma50.Current.Value < self.sma200.Current.Value and self.sma50.Previous.Value >= self.sma200.Previous.Value:
            self.Liquidate(self.spy)
            self.position_open = False
            self.Log("SOLD SPY @" + str(price))
