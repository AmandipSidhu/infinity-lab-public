"""
WorldQuant Alpha #41: Multi-Factor Strategy
Combines price momentum, volume patterns, and volatility signals.
"""

from AlgorithmImports import *


class WorldQuantAlpha041(QCAlgorithm):
    
    def Initialize(self):
        self.SetStartDate(2020, 1, 1)
        self.SetCash(100000)
        
        # Add liquid stocks
        for ticker in ["SPY", "QQQ", "IWM"]:
            self.AddEquity(ticker, Resolution.Daily)
        
        # Multi-factor indicators
        for symbol in self.Securities.Keys:
            self.SMA(symbol, 20)
            self.RSI(symbol, 14)
            self.ATR(symbol, 14)
        
        self.Schedule.On(self.DateRules.EveryDay(), 
                        self.TimeRules.AfterMarketOpen("SPY", 30),
                        self.Rebalance)
    
    def Rebalance(self):
        for symbol in self.Securities.Keys:
            price = self.Securities[symbol].Price
            sma = self.SMA(symbol, 20).Current.Value
            rsi = self.RSI(symbol, 14).Current.Value
            
            # Multi-factor signal
            signal = 0
            if price > sma: signal += 1
            if rsi < 40: signal += 1
            if rsi > 60: signal -= 1
            
            if signal >= 2:
                self.SetHoldings(symbol, 0.3)
            elif signal <= -1:
                self.SetHoldings(symbol, -0.3)
            else:
                self.Liquidate(symbol)
