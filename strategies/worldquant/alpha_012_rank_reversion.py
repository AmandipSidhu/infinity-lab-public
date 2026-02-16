"""
WorldQuant Alpha #12: Cross-Sectional Rank Reversion
Formula: (sign(delta(volume, 1)) * (-1 * delta(close, 1)))

Strategy: Simple mean reversion based on volume-price divergence.
When volume increases but price decreases (or vice versa), signal reversal.
"""

from AlgorithmImports import *
import numpy as np


class WorldQuantAlpha012(QCAlgorithm):
    """WorldQuant Alpha #12 - Rank Mean Reversion"""
    
    def Initialize(self):
        self.SetStartDate(2020, 1, 1)
        self.SetCash(100000)
        
        # Universe selection
        self.UniverseSettings.Resolution = Resolution.Daily
        self.AddUniverse(self.CoarseSelection)
        
        # Store previous day data
        self.previous_data = {}
        
        self.Schedule.On(
            self.DateRules.EveryDay(),
            self.TimeRules.AfterMarketOpen("SPY", 30),
            self.Rebalance
        )
    
    def CoarseSelection(self, coarse):
        sorted_by_volume = sorted(
            [x for x in coarse if x.Price > 10 and x.HasFundamentalData],
            key=lambda x: x.DollarVolume,
            reverse=True
        )
        return [x.Symbol for x in sorted_by_volume[:50]]
    
    def OnData(self, data):
        for symbol in self.ActiveSecurities.Keys:
            if data.ContainsKey(symbol) and data[symbol] is not None:
                bar = data[symbol]
                if symbol not in self.previous_data:
                    self.previous_data[symbol] = {
                        'close': bar.Close,
                        'volume': bar.Volume
                    }
    
    def Rebalance(self):
        alpha_scores = {}
        
        for symbol in self.ActiveSecurities.Keys:
            if symbol not in self.previous_data:
                continue
            
            current = self.Securities[symbol]
            prev = self.previous_data[symbol]
            
            # Calculate deltas
            delta_volume = current.Volume - prev['volume']
            delta_close = current.Close - prev['close']
            
            # Alpha signal
            alpha = np.sign(delta_volume) * (-delta_close)
            alpha_scores[symbol] = alpha
            
            # Update previous data
            self.previous_data[symbol] = {
                'close': current.Close,
                'volume': current.Volume
            }
        
        if not alpha_scores:
            return
        
        # Rank signals
        ranked = sorted(alpha_scores.items(), key=lambda x: x[1], reverse=True)
        
        # Long top quartile, short bottom quartile
        n = len(ranked) // 4
        long_symbols = [s for s, _ in ranked[:n]]
        short_symbols = [s for s, _ in ranked[-n:]]
        
        # Equal weight positions
        weight = 1.0 / (len(long_symbols) + len(short_symbols))
        
        for symbol in list(self.Portfolio.Keys):
            if symbol not in long_symbols and symbol not in short_symbols:
                self.Liquidate(symbol)
        
        for symbol in long_symbols:
            self.SetHoldings(symbol, weight)
        
        for symbol in short_symbols:
            self.SetHoldings(symbol, -weight)
