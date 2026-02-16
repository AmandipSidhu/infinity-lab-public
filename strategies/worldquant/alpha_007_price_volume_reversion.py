"""
WorldQuant Alpha #7: Price-Volume Mean Reversion
Formula: ((adv20 < volume) ? ((-1 * ts_rank(abs(delta(close, 7)), 60)) * sign(delta(close, 7))) : -1)

Strategy: Mean reversion based on volume spikes and recent price movements.
Sells when volume exceeds 20-day average and price has moved significantly.
"""

from AlgorithmImports import *
import numpy as np


class WorldQuantAlpha007(QCAlgorithm):
    """WorldQuant Alpha #7 - Price-Volume Mean Reversion"""
    
    def Initialize(self):
        self.SetStartDate(2020, 1, 1)
        self.SetCash(100000)
        
        # Add SPY and liquid ETFs
        symbols = ["SPY", "QQQ", "IWM", "DIA", "EFA", "EEM"]
        for ticker in symbols:
            self.AddEquity(ticker, Resolution.Daily)
        
        # Indicators
        self.adv = {}  # 20-day average volume
        self.price_history = {}
        
        for symbol in self.Securities.Keys:
            self.adv[symbol] = self.SMA(symbol, 20, Resolution.Daily, Field.Volume)
            self.price_history[symbol] = RollingWindow[float](60)
        
        self.Schedule.On(
            self.DateRules.EveryDay("SPY"),
            self.TimeRules.AfterMarketOpen("SPY", 30),
            self.Rebalance
        )
    
    def OnData(self, data):
        for symbol in self.Securities.Keys:
            if data.ContainsKey(symbol) and data[symbol] is not None:
                self.price_history[symbol].Add(data[symbol].Close)
    
    def Rebalance(self):
        for symbol in self.Securities.Keys:
            if not self.adv[symbol].IsReady or not self.price_history[symbol].IsReady:
                continue
            
            current_volume = self.Securities[symbol].Volume
            avg_volume = self.adv[symbol].Current.Value
            
            if current_volume < avg_volume:
                # No signal when volume below average
                self.Liquidate(symbol)
                continue
            
            # Calculate 7-day price delta
            prices = list(self.price_history[symbol])[:7]
            if len(prices) < 7:
                continue
            
            delta_7 = prices[0] - prices[6]
            
            # Calculate rank of abs(delta) over 60 days
            all_prices = list(self.price_history[symbol])
            deltas = [abs(all_prices[i] - all_prices[min(i+7, 59)]) for i in range(53)]
            rank = sum(1 for d in deltas if d < abs(delta_7)) / len(deltas)
            
            # Alpha signal
            alpha = -rank * np.sign(delta_7)
            
            # Position sizing based on alpha strength
            if abs(alpha) > 0.3:
                target = np.clip(alpha * 0.5, -0.2, 0.2)
                self.SetHoldings(symbol, target)
            else:
                self.Liquidate(symbol)
