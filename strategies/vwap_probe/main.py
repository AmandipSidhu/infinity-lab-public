from QuantConnect import Resolution, Market
from QuantConnect.Algorithm import QCAlgorithm
from QuantConnect.Indicators import VWAP, ATR, SimpleMovingAverage
from QuantConnect.Data.Market import TradeBar
from datetime import datetime, timedelta

class VwapProbe(QCAlgorithm):

    def Initialize(self):
        """Initialize the algorithm with start date, end date, capital, and indicators."""

        # 1. Set start date, end date, and starting capital
        self.SetStartDate(2023, 1, 1)  # Arbitrary start date, adjust as needed
        self.SetEndDate(2023, 12, 31)  # Arbitrary end date, adjust as needed
        self.SetCash(100_000)

        # 2. Add the instrument with the correct resolution
        # Assuming a primary symbol like SPY for equities
        self.symbol = self.AddEquity("SPY", Resolution.Minute).Symbol

        # 3. Create ALL three indicators
        # (a) VWAP indicator on the primary symbol
        self.vwap = self.VWAP(self.symbol, 14, Resolution.Minute) # Default period for VWAP is usually based on bars, 14 is a common choice for shorter-term VWAP if not specified.
        
        # (b) ATR indicator with period 14 (ATR(14)) on the primary symbol
        self.atr = self.ATR(self.symbol, 14, Resolution.Minute)

        # (c) A volume moving average (VolumeMA) — use a rolling window or SMA on the volume field.
        # Create a helper for Volume history, then an SMA on that history
        self.volume_history = []
        self.volume_ma_period = 20
        self.volume_ma = SimpleMovingAverage(self.volume_ma_period)

        # Set a warm-up period for indicators
        self.SetWarmUp(self.volume_ma_period + 14) # Max of indicator periods + 1 for VWAP/ATR calculation start

        # Internal state variables
        self.entry_price = 0
        self.entry_time = None
        self.stop_loss_level = 0

        # 6. Close all positions at end of day (EOD)
        # Schedule to liquidate all positions 5 minutes before market close
        self.Schedule.On(self.DateRules.EveryDay(),
                         self.TimeRules.BeforeMarketClose(self.symbol, 5),
                         self.LiquidatePositions)

    def OnData(self, data):
        """
        OnData event handler: Implements entry and exit logic based on the spec.
        Entry: Close < VWAP - (2 * ATR(14)) AND Volume > 1.5 * Volume_MA(20) AND time between 09:45 and 15:30
        Exit: Close >= VWAP OR time held > 60 minutes OR stop_loss hit
        """
        if not self.vwap.IsReady or not self.atr.IsReady:
            self.Log(f"Indicators not ready for {self.symbol}. VWAP Ready: {self.vwap.IsReady}, ATR Ready: {self.atr.IsReady}")
            return

        if self.symbol not in data:
            return

        bar = data[self.symbol]
        if bar is None:
            return
            
        # Update volume history and SMA
        self.volume_history.append(bar.Volume)
        if len(self.volume_history) > self.volume_ma_period:
            self.volume_history.pop(0) # Keep history to the required period

        # For the SMA to warm up, it needs enough data points.
        # We'll use a manual SMA calculation on volume_history until self.volume_ma can be used directly with data.
        # For simplicity, here we calculate the SMA directly from the window for the check.
        # In a real-time scenario, if using a Lean indicator, it needs to be updated with `bar.Volume`.
        # QuantConnect's SMA indicator can handle this internally if hooked up correctly to a consolidator or direct OnData input.
        # For this setup, we'll manually feed the volume to the SMA if it's not directly hooked to the DataStream
        self.volume_ma.Update(self.Time, bar.Volume)
        
        if not self.volume_ma.IsReady:
            self.Log(f"Volume MA indicator not ready for {self.symbol}.")
            return

        current_close = bar.Close
        current_volume = bar.Volume
        current_vwap = self.vwap.Current.Value
        current_atr = self.atr.Current.Value
        current_volume_ma = self.volume_ma.Current.Value
        
        # Check market hours for trading
        market_open_time = datetime(self.Time.year, self.Time.month, self.Time.day, 9, 45, 0)
        market_close_time = datetime(self.Time.year, self.Time.month, self.Time.day, 15, 30, 0)
        is_trading_hour = market_open_time <= self.Time < market_close_time

        # 4. Implement EXACT entry logic
        if not self.Portfolio.Invested:
            if (current_close < (current_vwap - (2 * current_atr))) and \
               (current_volume > (1.5 * current_volume_ma)) and \
               is_trading_hour:
                
                self.SetHoldings(self.symbol, 0.9) # Go long with 90% of portfolio
                self.entry_price = current_close
                self.entry_time = self.Time
                self.stop_loss_level = self.entry_price - (2 * current_atr)
                self.Debug(f"ENTRY: {self.symbol} at {self.entry_price}, Stop: {self.stop_loss_level}, Time: {self.Time}")
        
        # 5. Implement EXACT exit logic
        elif self.Portfolio.Invested:
            # Get the current holding for the symbol
            holding = self.Portfolio[self.symbol]

            # Exit condition 1: Close >= VWAP
            vwap_reversion_exit = current_close >= current_vwap

            # Exit condition 2: time held > 60 minutes
            time_held_exit = False
            if self.entry_time:
                time_held = self.Time - self.entry_time
                time_held_exit = time_held.total_seconds() > 3600 # 60 minutes * 60 seconds/minute

            # Exit condition 3: stop_loss hit (current_close <= stop_loss_level)
            stop_loss_hit_exit = current_close <= self.stop_loss_level

            if vwap_reversion_exit or time_held_exit or stop_loss_hit_exit:
                self.Liquidate(self.symbol)
                self.Debug(f"EXIT: {self.symbol} at {current_close}, Reason: "
                           f"VWAP Reversion: {vwap_reversion_exit}, "
                           f"Time Held: {time_held_exit}, "
                           f"Stop Loss: {stop_loss_hit_exit}. Time: {self.Time}")
                self.entry_price = 0
                self.entry_time = None
                self.stop_loss_level = 0

    def LiquidatePositions(self):
        """Liquidates all open positions at end of day."""
        if self.Portfolio.Invested:
            self.Liquidate()
