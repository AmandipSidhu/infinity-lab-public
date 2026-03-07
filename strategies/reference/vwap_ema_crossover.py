# Source: https://www.quantconnect.com/forum/discussion/10644/ema-vwap-strategy/
# Proven to compile and backtest in QuantConnect
# ~50 lines, SPY trading with VWAP/EMA(30) crossover
# Minute resolution for higher frequency trading
# Backtest period: 2022-01-01 to 2023-01-01
# Initial capital: $10,000
# Target metrics (from Medium study on VWAP strategies):
#   Sharpe Ratio: 1.0 ± 0.5
#   Total Return: 15% ± 8%
#   Max Drawdown: -20% ± 7%
#   Trades: 50-100

from AlgorithmImports import *

class VWAPCrossoverStrategy(QCAlgorithm):

    def Initialize(self):
        self.SetStartDate(2022, 1, 1)
        self.SetEndDate(2023, 1, 1)
        self.SetCash(10000)
        
        self.spy = self.AddEquity("SPY", Resolution.Minute).Symbol
        self.ema = self.EMA(self.spy, 30, Resolution.Minute)
        self.vwap = self.VWAP(self.spy, 30)
        
    def OnData(self, data):
        if not self.ema.IsReady or not self.vwap.IsReady:
            return
            
        # Long when EMA crosses above VWAP
        if self.ema.Current.Value > self.vwap.Current.Value:
            if not self.Portfolio.Invested:
                self.SetHoldings(self.spy, 1.0)
                self.Debug("Buy signal: EMA > VWAP at {}".format(self.Time))
                
        # Exit when EMA crosses below VWAP
        elif self.ema.Current.Value < self.vwap.Current.Value:
            if self.Portfolio.Invested:
                self.Liquidate(self.spy)
                self.Debug("Sell signal: EMA < VWAP at {}".format(self.Time))
