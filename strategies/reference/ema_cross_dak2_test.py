from AlgorithmImports import *



class EmaCrossUniverseSelectionAlgorithm(QCAlgorithm):


    def initialize(self):
        self.set_start_date(2022, 1, 1)
        self.set_end_date(2023, 12, 31)
        self.set_cash(100000)


        self.set_brokerage_model(BrokerageName.INTERACTIVE_BROKERS_BROKERAGE, AccountType.MARGIN)


        symbols = [
            Symbol.create("SPY", SecurityType.EQUITY, Market.USA),
            Symbol.create("QQQ", SecurityType.EQUITY, Market.USA),
            Symbol.create("IWM", SecurityType.EQUITY, Market.USA),
        ]


        self.set_universe_selection(ManualUniverseSelectionModel(symbols))


        self.set_alpha(EmaCrossAlphaModel(
            fast_period=50,
            slow_period=200,
            resolution=Resolution.DAILY
        ))


        self.set_portfolio_construction(EqualWeightingPortfolioConstructionModel(Resolution.DAILY))
        self.set_execution(ImmediateExecutionModel())
        self.set_risk_management(MaximumDrawdownPercentPerSecurity(0.05))


    def on_order_event(self, order_event):
        if order_event.status == OrderStatus.FILLED:
            self.log(f"Order filled: {order_event.symbol} qty={order_event.fill_quantity} price={order_event.fill_price:.2f}")
