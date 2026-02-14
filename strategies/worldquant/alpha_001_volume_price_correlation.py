"""
WorldQuant Alpha #1: Volume-Price Correlation Momentum
Formula: (-1 * correlation(rank(delta(log(volume), 1)), rank(((close - open) / open)), 6))

Strategy: Exploits the relationship between volume changes and intraday price movements.
When volume increases correlate negatively with price range, it signals potential reversals.
"""

from AlgorithmImports import *
import numpy as np
from scipy.stats import spearmanr


class WorldQuantAlpha001(QCAlgorithm):
    """WorldQuant Alpha #1 - Volume-Price Correlation Strategy"""
    
    def Initialize(self):
        self.SetStartDate(2020, 1, 1)
        self.SetEndDate(2024, 1, 1)
        self.SetCash(100000)
        
        # Add universe of liquid stocks
        self.UniverseSettings.Resolution = Resolution.Daily
        self.AddUniverse(self.SelectCoarse)
        
        # Parameters
        self.lookback = 6  # Correlation lookback period
        self.leverage = 1.0  # Max leverage
        self.rebalance_days = 5  # Rebalance frequency
        
        # Data storage
        self.history_data = {}
        
        # Schedule rebalancing
        self.Schedule.On(
            self.DateRules.Every(DayOfWeek.Monday),
            self.TimeRules.AfterMarketOpen("SPY", 30),
            self.Rebalance
        )
    
    def SelectCoarse(self, coarse):
        """Select top liquid stocks by dollar volume."""
        sorted_by_dollar_volume = sorted(
            [x for x in coarse if x.HasFundamentalData and x.Price > 5],
            key=lambda x: x.DollarVolume,
            reverse=True
        )
        return [x.Symbol for x in sorted_by_dollar_volume[:100]]
    
    def OnData(self, data):
        """Store historical data for alpha calculation."""
        for symbol in self.ActiveSecurities.Keys:
            if symbol not in self.history_data:
                self.history_data[symbol] = []
            
            if data.ContainsKey(symbol) and data[symbol] is not None:
                bar = data[symbol]
                self.history_data[symbol].append({
                    'time': self.Time,
                    'open': bar.Open,
                    'close': bar.Close,
                    'volume': bar.Volume
                })
                
                # Keep only recent data
                if len(self.history_data[symbol]) > self.lookback + 5:
                    self.history_data[symbol] = self.history_data[symbol][-(self.lookback + 5):]
    
    def Rebalance(self):
        """Calculate alpha signals and rebalance portfolio."""
        alpha_scores = {}
        
        for symbol, hist in self.history_data.items():
            if len(hist) < self.lookback + 1:
                continue
            
            try:
                # Calculate alpha signal
                alpha = self.CalculateAlpha(hist)
                if alpha is not None:
                    alpha_scores[symbol] = alpha
            except:
                continue
        
        if not alpha_scores:
            return
        
        # Rank and normalize scores
        ranked = sorted(alpha_scores.items(), key=lambda x: x[1], reverse=True)
        
        # Long top decile, short bottom decile
        n_positions = max(1, len(ranked) // 10)
        long_symbols = [s for s, _ in ranked[:n_positions]]
        short_symbols = [s for s, _ in ranked[-n_positions:]]
        
        # Position sizing
        position_size = self.leverage / (len(long_symbols) + len(short_symbols))
        
        # Liquidate positions not in new portfolio
        for symbol in list(self.Portfolio.Keys):
            if symbol not in long_symbols and symbol not in short_symbols:
                self.Liquidate(symbol)
        
        # Enter long positions
        for symbol in long_symbols:
            self.SetHoldings(symbol, position_size)
        
        # Enter short positions
        for symbol in short_symbols:
            self.SetHoldings(symbol, -position_size)
    
    def CalculateAlpha(self, hist):
        """
        Calculate Alpha #1 signal.
        Returns: Negative correlation between volume rank changes and price range ranks.
        """
        if len(hist) < self.lookback + 1:
            return None
        
        # Extract data
        volumes = np.array([h['volume'] for h in hist[-(self.lookback + 1):]])
        opens = np.array([h['open'] for h in hist[-(self.lookback + 1):]])
        closes = np.array([h['close'] for h in hist[-(self.lookback + 1):]])
        
        # Calculate delta(log(volume))
        log_volumes = np.log(volumes + 1)  # +1 to avoid log(0)
        delta_log_volume = np.diff(log_volumes)
        
        # Rank delta log volume
        rank_delta_volume = spearmanr(range(len(delta_log_volume)), delta_log_volume)[0]
        
        # Calculate (close - open) / open
        price_range = (closes[1:] - opens[1:]) / (opens[1:] + 1e-6)
        
        # Rank price range
        rank_price_range = spearmanr(range(len(price_range)), price_range)[0]
        
        # Correlation of ranks
        if len(delta_log_volume) >= 2:
            correlation = np.corrcoef(
                [rank_delta_volume] * len(delta_log_volume),
                [rank_price_range] * len(price_range)
            )[0, 1]
            
            # Alpha = -1 * correlation
            return -correlation
        
        return None
