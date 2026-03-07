import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
from QuantConnect import Resolution, Market, SecurityType
from QuantConnect.Algorithm import QCAlgorithm
from QuantConnect.Indicators import VWAP, ATR, SimpleMovingAverage
from QuantConnect.Data.Market import TradeBar
from QuantConnect.Securities import Equity, SecurityPortfolio, SecurityManager, SecurityTransactionManager, SecurityExchangeHours, SymbolProperties
from QuantConnect.Orders import OrderTicket
from QuantConnect.Orders.OrderTypes import MarketOrder
from QuantConnect.Python import PythonQuandl
from QuantConnect.Data import SubscriptionManager
from QuantConnect.Data.UniverseSelection import SecurityChanges
from QuantConnect.CashBook import Cash
from QuantConnect.Orders import OrderTicket, OrderStatus
from QuantConnect.Util import * # For extensions like OrderTicket.IsFill

# Mock QuantConnect Globals for testing
class MockAlgorithm(QCAlgorithm):
    def __init__(self):
        super().__init__()
        self.Debug = MagicMock()
        self.Log = MagicMock()
        self.SetHoldings = MagicMock(return_value=MagicMock(IsFill=True))
        self.Liquidate = MagicMock(return_value=MagicMock(IsFill=True))
        self.UniverseSettings = MagicMock()
        self.Settings = MagicMock()
        self.Transactions = MagicMock(spec=SecurityTransactionManager)
        self.Transactions.GetOrderTickets = MagicMock(return_value=[]) # Mock to return empty list by default
        
        # Mock Portfolio and CashBook
        self.Portfolio = MagicMock(spec=SecurityPortfolio)
        self.Portfolio.Invested = False # Default to not invested
        self.Portfolio.CashBook = MagicMock()
        self.Portfolio.CashBook.Add = MagicMock()
        self.Portfolio.CashBook.__getitem__.return_value = MagicMock(Amount=100_000, Symbol="USD")


        self.Securities = SecurityManager(MagicMock(), MagicMock())
        self.SubscriptionManager = SubscriptionManager()
        self.current_time = datetime(2023, 1, 1, 10, 0, 0) # Default time for tests
        self.Schedule = MagicMock()
        self.Schedule.On = MagicMock()
        self.DateRules = MagicMock()
        self.TimeRules = MagicMock()
        self.DateRules.EveryDay = MagicMock(return_value=MagicMock())
        self.TimeRules.BeforeMarketClose = MagicMock(return_value=MagicMock())

    def AddEquity(self, ticker, resolution, market=Market.USA, fillForward=True, leverage=1.0, extendedMarketHours=False):
        # Create a mock Symbol object with a Value attribute
        mock_symbol = MagicMock()
        mock_symbol.Value = ticker
        
        # Mock SecurityExchangeHours and other properties if needed
        mock_exchange_hours = MagicMock(spec=SecurityExchangeHours)
        mock_exchange_hours.IsMarketOpen.return_value = True # Assume market is always open for simplicity in tests if not explicitly testing hours

        equity = MagicMock(spec=Equity)
        equity.Symbol = mock_symbol
        equity.Exchange.Hours = mock_exchange_hours
        
        self.Securities.Add(mock_symbol, equity)
        return equity

    def SetStartDate(self, year, month, day):
        self.start_date = datetime(year, month, day)

    def SetEndDate(self, year, month, day):
        self.end_date = datetime(year, month, day)

    def SetCash(self, cash):
        self.cash = cash
        self.Portfolio.CashBook.Add("USD", cash, 1) # Mock cashbook

    @property
    def Time(self):
        return self.current_time

    def ATR(self, symbol, period, resolution):
        indicator = ATR(period)
        indicator.IsReady = False # Default to not ready
        indicator.Current = MagicMock()
        indicator.Current.Value = 0 # Default value
        return indicator

    def VWAP(self, symbol, period, resolution):
        indicator = VWAP(symbol, period, resolution)
        indicator.IsReady = False # Default to not ready
        indicator.Current = MagicMock()
        indicator.Current.Value = 0 # Default value
        return indicator
    
    def SimpleMovingAverage(self, period):
        indicator = SimpleMovingAverage(period)
        indicator.IsReady = False
        indicator.Current = MagicMock()
        indicator.Current.Value = 0 # Default value
        return indicator

    # We need to manually update the mock indicator for testing
    def _mock_update_indicator(self, indicator, time, value):
        indicator.Update(time, value)
        if indicator.Samples >= indicator.Period:
            indicator.IsReady = True

    def _set_portfolio_state(self, invested=False, holding_quantity=0, average_price=0):
        self.Portfolio.Invested = invested
        self.Portfolio.__contains__.return_value = invested # For `self.symbol in self.Portfolio` check
        if invested:
            mock_holding = MagicMock()
            mock_holding.Symbol = self.symbol
            mock_holding.Quantity = holding_quantity
            mock_holding.AveragePrice = average_price
            self.Portfolio.__getitem__.return_value = mock_holding
            self.Portfolio.__setitem__.return_value = mock_holding
        else:
            self.Portfolio.__getitem__.return_value = None


# Import the algorithm to be tested
from strategies.vwap_probe.main import VwapProbe

class VwapProbeTests(unittest.TestCase):

    def setUp(self):
        # Patch AddReference to prevent it from trying to load .NET assemblies during unit tests
        self.add_reference_patch = patch('clr.AddReference')
        self.add_reference_patch.start()

        self.algo = VwapProbe()
        self.algo.Debug = MagicMock() # Reset mocks for each test
        self.algo.Log = MagicMock()
        self.algo.SetHoldings = MagicMock(return_value=MagicMock(IsFill=True))
        self.algo.Liquidate = MagicMock(return_value=MagicMock(IsFill=True))
        self.algo.Schedule = MagicMock()
        self.algo.Schedule.On = MagicMock()
        self.algo.DateRules = MagicMock()
        self.algo.TimeRules = MagicMock()
        self.algo.DateRules.EveryDay = MagicMock(return_value=MagicMock())
        self.algo.TimeRules.BeforeMarketClose = MagicMock(return_value=MagicMock())

        # Patch QCAlgorithm's internal methods for mocks that are called directly
        self.algo.SetStartDate = MockAlgorithm.SetStartDate.__get__(self.algo, MockAlgorithm)
        self.algo.SetEndDate = MockAlgorithm.SetEndDate.__get__(self.algo, MockAlgorithm)
        self.algo.SetCash = MockAlgorithm.SetCash.__get__(self.algo, MockAlgorithm)
        self.algo.AddEquity = MockAlgorithm.AddEquity.__get__(self.algo, MockAlgorithm)
        
        # Override indicators with mock-aware versions
        self.algo.ATR = MagicMock(side_effect=lambda s, p, r: MockAlgorithm.ATR(self.algo, s, p, r))
        self.algo.VWAP = MagicMock(side_effect=lambda s, p, r: MockAlgorithm.VWAP(self.algo, s, p, r))
        self.algo.SimpleMovingAverage = MagicMock(side_effect=lambda p: MockAlgorithm.SimpleMovingAverage(self.algo, p))

        # Run Initialize to set up the algorithm as if it were on QuantConnect
        self.algo.Initialize()
        
        # Manually assign mock portfolio and securities after Initialize
        self.algo.Portfolio = MagicMock(spec=SecurityPortfolio)
        self.algo.Portfolio.Invested = False
        self.algo.Portfolio.CashBook = MagicMock()
        self.algo.Portfolio.CashBook.Add("USD", 100_000, 1) # Mock initial cash
        self.algo.Portfolio.__contains__.return_value = False
        
        # Set self.algo.symbol to the mock symbol created by AddEquity
        self.algo.symbol = list(self.algo.Securities.Keys)[0] if self.algo.Securities.Count > 0 else MagicMock()
        if not hasattr(self.algo.symbol, 'Value'):
            self.algo.symbol.Value = "SPY" # Ensure symbol has a value attribute
        
        self.algo.Securities = MagicMock(spec=SecurityManager)
        self.algo.Securities.__contains__.return_value = True
        self.algo.Securities.__getitem__.return_value = MagicMock(Symbol=self.algo.symbol)
        

    def tearDown(self):
        self.add_reference_patch.stop()

    def _create_trade_bar(self, open_price, high_price, low_price, close_price, volume, bar_time=None):
        if bar_time is None:
            bar_time = self.algo.Time
        # Mock TradeBar with required properties
        bar = MagicMock(spec=TradeBar)
        bar.Open = open_price
        bar.High = high_price
        bar.Low = low_price
        bar.Close = close_price
        bar.Volume = volume
        bar.Time = bar_time
        return bar

    def _setup_indicator_warmup(self, vwap_value, atr_value, volume_ma_value):
        # Simulate indicator warmup for the specified period
        # For simplicity, we'll just set them ready and assign values
        self.algo.vwap.IsReady = True
        self.algo.vwap.Current.Value = vwap_value
        
        self.algo.atr.IsReady = True
        self.algo.atr.Current.Value = atr_value

        self.algo.volume_ma.IsReady = True
        self.algo.volume_ma.Current.Value = volume_ma_value

    def test_initialize_method(self):
        # Initialize is called in setUp, so we just check assertions
        self.algo.SetStartDate.assert_called_with(2023, 1, 1)
        self.algo.SetEndDate.assert_called_with(2023, 12, 31)
        self.algo.SetCash.assert_called_with(100_000)
        self.algo.AddEquity.assert_called_with("SPY", Resolution.Minute)
        self.assertTrue(hasattr(self.algo, 'vwap'))
        self.assertTrue(hasattr(self.algo, 'atr'))
        self.assertTrue(hasattr(self.algo, 'volume_ma'))
        self.algo.Schedule.On.assert_called_once()
        self.algo.DateRules.EveryDay.assert_called_once()
        self.algo.TimeRules.BeforeMarketClose.assert_called_with(self.algo.symbol, 5)


    def test_entry_logic_true(self):
        self.algo.current_time = datetime(2023, 1, 1, 10, 0, 0) # Within trading hours
        self._set_portfolio_state(invested=False)
        self._setup_indicator_warmup(vwap_value=100, atr_value=1, volume_ma_value=1000)

        # Conditions: Close < VWAP - (2 * ATR) AND Volume > 1.5 * Volume_MA
        # Let's make close price satisfy the condition
        bar = self._create_trade_bar(97, 98, 96, 97, 1600) # Close < 100 - (2*1) = 98 AND Volume > 1.5 * 1000 = 1500
        data = {self.algo.symbol: bar}
        
        self.algo.OnData(data)
        
        self.algo.SetHoldings.assert_called_with(self.algo.symbol, 0.9)
        self.assertEqual(self.algo.entry_price, 97)
        self.assertEqual(self.algo.entry_time, self.algo.Time)
        self.assertEqual(self.algo.stop_loss_level, 97 - (2 * 1)) # entry_price - (2 * atr)


    def test_entry_logic_false_vwap_condition(self):
        self.algo.current_time = datetime(2023, 1, 1, 10, 0, 0) # Within trading hours
        self._set_portfolio_state(invested=False)
        self._setup_indicator_warmup(vwap_value=100, atr_value=1, volume_ma_value=1000)

        # Conditions: Close < VWAP - (2 * ATR) AND Volume > 1.5 * Volume_MA
        # Close price fails the condition: Close = 99 (not < 98)
        bar = self._create_trade_bar(99, 100, 98, 99, 1600) 
        data = {self.algo.symbol: bar}

        self.algo.OnData(data)
        self.algo.SetHoldings.assert_not_called()


    def test_entry_logic_false_volume_condition(self):
        self.algo.current_time = datetime(2023, 1, 1, 10, 0, 0) # Within trading hours
        self._set_portfolio_state(invested=False)
        self._setup_indicator_warmup(vwap_value=100, atr_value=1, volume_ma_value=1000)

        # Conditions: Close < VWAP - (2 * ATR) AND Volume > 1.5 * Volume_MA
        # Volume fails the condition: Volume = 1400 (not > 1500)
        bar = self._create_trade_bar(97, 98, 96, 97, 1400) 
        data = {self.algo.symbol: bar}

        self.algo.OnData(data)
        self.algo.SetHoldings.assert_not_called()

    def test_entry_logic_false_time_condition(self):
        self.algo.current_time = datetime(2023, 1, 1, 9, 30, 0) # Outside trading hours (too early)
        self._set_portfolio_state(invested=False)
        self._setup_indicator_warmup(vwap_value=100, atr_value=1, volume_ma_value=1000)

        # All other conditions true, but time is false
        bar = self._create_trade_bar(97, 98, 96, 97, 1600) 
        data = {self.algo.symbol: bar}

        self.algo.OnData(data)
        self.algo.SetHoldings.assert_not_called()

        self.algo.current_time = datetime(2023, 1, 1, 16, 0, 0) # Outside trading hours (too late)
        self._set_portfolio_state(invested=False)
        self._setup_indicator_warmup(vwap_value=100, atr_value=1, volume_ma_value=1000)

        data = {self.algo.symbol: bar} # Use same bar data
        self.algo.OnData(data)
        self.algo.SetHoldings.assert_not_called()


    def test_exit_logic_vwap_reversion(self):
        self.algo.current_time = datetime(2023, 1, 1, 10, 0, 0)
        self.algo.entry_price = 97
        self.algo.entry_time = self.algo.current_time
        self.algo.stop_loss_level = 95
        self._set_portfolio_state(invested=True, holding_quantity=100, average_price=97)
        self._setup_indicator_warmup(vwap_value=98, atr_value=1, volume_ma_value=1000)

        # Close >= VWAP (99 >= 98)
        bar = self._create_trade_bar(98, 99, 97, 99, 1000)
        data = {self.algo.symbol: bar}

        self.algo.OnData(data)
        self.algo.Liquidate.assert_called_with(self.algo.symbol)
        self.assertEqual(self.algo.entry_price, 0)
        self.assertIsNone(self.algo.entry_time)


    def test_exit_logic_time_held(self):
        self.algo.entry_price = 97
        self.algo.entry_time = datetime(2023, 1, 1, 9, 0, 0) # Entry at 9:00
        self.algo.current_time = datetime(2023, 1, 1, 10, 1, 0) # Current time > 60 minutes after entry
        self.algo.stop_loss_level = 95
        self._set_portfolio_state(invested=True, holding_quantity=100, average_price=97)
        self._setup_indicator_warmup(vwap_value=97, atr_value=1, volume_ma_value=1000)

        # Close is not above VWAP, not at stop loss, but time held > 60 minutes
        bar = self._create_trade_bar(97, 97, 96, 96.5, 1000)
        data = {self.algo.symbol: bar}

        self.algo.OnData(data)
        self.algo.Liquidate.assert_called_with(self.algo.symbol)
        self.assertEqual(self.algo.entry_price, 0)
        self.assertIsNone(self.algo.entry_time)

    def test_exit_logic_stop_loss_hit(self):
        self.algo.current_time = datetime(2023, 1, 1, 10, 0, 0)
        self.algo.entry_price = 97
        self.algo.entry_time = self.algo.current_time - timedelta(minutes=10)
        self.algo.stop_loss_level = 95 # Stop loss at 95
        self._set_portfolio_state(invested=True, holding_quantity=100, average_price=97)
        self._setup_indicator_warmup(vwap_value=97, atr_value=1, volume_ma_value=1000)

        # Close is not above VWAP, not past time held, but stop loss hit (94 <= 95)
        bar = self._create_trade_bar(95, 95, 93, 94, 1000)
        data = {self.algo.symbol: bar}

        self.algo.OnData(data)
        self.algo.Liquidate.assert_called_with(self.algo.symbol)
        self.assertEqual(self.algo.entry_price, 0)
        self.assertIsNone(self.algo.entry_time)

    def test_eod_liquidation(self):
        self.algo.current_time = datetime(2023, 1, 1, 15, 55, 0) # Just before market close
        self._set_portfolio_state(invested=True, holding_quantity=100, average_price=97)

        self.algo.LiquidatePositions()
        self.algo.Liquidate.assert_called_once()
        self.algo.Debug.assert_called_with(f"EOD Liquidated all positions at {self.algo.Time}.")

    def test_no_trading_if_indicators_not_ready(self):
        self.algo.current_time = datetime(2023, 1, 1, 10, 0, 0)
        self.algo.vwap.IsReady = False
        self.algo.atr.IsReady = False
        self.algo.volume_ma.IsReady = False
        self._set_portfolio_state(invested=False)
        bar = self._create_trade_bar(97, 98, 96, 97, 1600)
        data = {self.algo.symbol: bar}

        self.algo.OnData(data)
        self.algo.SetHoldings.assert_not_called()
        self.algo.Log.assert_called_with(f"Indicators not ready for {self.algo.symbol}. VWAP Ready: False, ATR Ready: False")

    def test_no_trading_if_no_data_for_symbol(self):
        self.algo.current_time = datetime(2023, 1, 1, 10, 0, 0)
        self._set_portfolio_state(invested=False)
        self._setup_indicator_warmup(vwap_value=100, atr_value=1, volume_ma_value=1000)

        data = {} # No data for self.symbol
        self.algo.OnData(data)
        self.algo.SetHoldings.assert_not_called()
        self.algo.Liquidate.assert_not_called()

    def test_no_trading_if_bar_is_none(self):
        self.algo.current_time = datetime(2023, 1, 1, 10, 0, 0)
        self._set_portfolio_state(invested=False)
        self._setup_indicator_warmup(vwap_value=100, atr_value=1, volume_ma_value=1000)

        data = {self.algo.symbol: None} # Bar is None
        self.algo.OnData(data)
        self.algo.SetHoldings.assert_not_called()
        self.algo.Liquidate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
