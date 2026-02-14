"""
WorldQuant Alpha #030: Volume Pairs
Volume-weighted pairs trading
"""

from AlgorithmImports import *


class WorldQuantAlpha030(QCAlgorithm):
    """WorldQuant Alpha #030 - Volume Pairs"""
    
    def Initialize(self):
        self.SetStartDate(2020, 1, 1)
        self.SetCash(100000)
        
        # Add universe
        symbols = ["SPY", "QQQ", "IWM", "DIA"]
        for ticker in symbols:
            self.AddEquity(ticker, Resolution.Daily)
            
        # Add indicators
        self.indicators = {}
        for symbol in self.Securities.Keys:
            self.indicators[symbol] = {
                'sma20': self.SMA(symbol, 20),
                'rsi': self.RSI(symbol, 14),
                'bb': self.BB(symbol, 20, 2)
            }
        
        self.Schedule.On(
            self.DateRules.EveryDay(),
            self.TimeRules.AfterMarketOpen("SPY", 30),
            self.Rebalance
        )
    
    def Rebalance(self):
        """Calculate alpha signals and rebalance portfolio."""
        for symbol in self.Securities.Keys:
            if not all(ind.IsReady for ind in self.indicators[symbol].values()):
                continue
            
            # Placeholder alpha signal - customize based on specific alpha formula
            price = self.Securities[symbol].Price
            sma = self.indicators[symbol]['sma20'].Current.Value
            rsi = self.indicators[symbol]['rsi'].Current.Value
            
            # Simple momentum + mean reversion combo
            if price > sma and rsi < 50:
                self.SetHoldings(symbol, 0.25)
            elif price < sma and rsi > 50:
                self.SetHoldings(symbol, -0.25)
            else:
                self.Liquidate(symbol)
