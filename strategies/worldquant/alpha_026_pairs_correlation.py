"""
WorldQuant Alpha #26: Correlation-Based Pairs Trading
Formula: -1 * ts_max(correlation(ts_rank(volume, 5), ts_rank(high, 5), 5), 3)

Strategy: Identifies pairs with high volume-price correlation for mean reversion trading.
When correlation is at recent highs, expect reversion.
"""

from AlgorithmImports import *
import numpy as np


class WorldQuantAlpha026(QCAlgorithm):
    """WorldQuant Alpha #26 - Correlation Pairs Trading"""
    
    def Initialize(self):
        self.SetStartDate(2020, 1, 1)
        self.SetCash(100000)
        
        # Pairs: SPY-QQQ, IWM-DIA, etc.
        self.pairs = [
            ("SPY", "QQQ"),
            ("IWM", "DIA"),
            ("XLF", "XLE")
        ]
        
        for ticker1, ticker2 in self.pairs:
            self.AddEquity(ticker1, Resolution.Daily)
            self.AddEquity(ticker2, Resolution.Daily)
        
        # Data storage
        self.volume_history = {}
        self.high_history = {}
        
        for symbol in self.Securities.Keys:
            self.volume_history[symbol] = RollingWindow[float](10)
            self.high_history[symbol] = RollingWindow[float](10)
        
        self.Schedule.On(
            self.DateRules.EveryDay(),
            self.TimeRules.AfterMarketOpen("SPY", 60),
            self.Rebalance
        )
    
    def OnData(self, data):
        for symbol in self.Securities.Keys:
            if data.ContainsKey(symbol) and data[symbol] is not None:
                self.volume_history[symbol].Add(data[symbol].Volume)
                self.high_history[symbol].Add(data[symbol].High)
    
    def Rebalance(self):
        for sym1, sym2 in self.pairs:
            symbol1 = self.Symbol(sym1)
            symbol2 = self.Symbol(sym2)
            
            if not (self.volume_history[symbol1].IsReady and 
                    self.volume_history[symbol2].IsReady):
                continue
            
            # Calculate correlation signal
            alpha1 = self.CalculateAlpha(symbol1)
            alpha2 = self.CalculateAlpha(symbol2)
            
            if alpha1 is None or alpha2 is None:
                continue
            
            # Trade the spread
            spread_signal = alpha1 - alpha2
            
            if spread_signal > 0.2:
                # Long sym1, short sym2
                self.SetHoldings(symbol1, 0.25)
                self.SetHoldings(symbol2, -0.25)
            elif spread_signal < -0.2:
                # Short sym1, long sym2
                self.SetHoldings(symbol1, -0.25)
                self.SetHoldings(symbol2, 0.25)
            else:
                self.Liquidate(symbol1)
                self.Liquidate(symbol2)
    
    def CalculateAlpha(self, symbol):
        """Calculate alpha signal for single symbol."""
        volumes = list(self.volume_history[symbol])[:5]
        highs = list(self.high_history[symbol])[:5]
        
        if len(volumes) < 5 or len(highs) < 5:
            return None
        
        # Rank volumes and highs
        volume_ranks = [sum(1 for v in volumes if v < x) for x in volumes]
        high_ranks = [sum(1 for h in highs if h < x) for x in highs]
        
        # Correlation
        corr = np.corrcoef(volume_ranks, high_ranks)[0, 1]
        
        return -corr
