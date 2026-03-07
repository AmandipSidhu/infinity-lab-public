# Tier 2: Fixed z-score calculation and magnitude axis
# Changes from Tier 1:
#   - z-score now computed per column using scipy.stats.zscore
#   - magnitude computed per column: -z * col_std / col_price
#   - confidence correctly uses -z (was using +z before)
#   - added guard for zero-length weight Series
# Backtest result: Sharpe ~0.83 (within acceptance band 0.6–1.8, but below target 1.2)
# Fix applied in Tier 3: InsightWeighting weight parameter clipped to [0, 1]
#   and confidence clamped to valid probability range

from AlgorithmImports import *
from scipy.stats import norm, zscore as scipy_zscore
import numpy as np


class MeanReversionMultiAssetTier2(QCAlgorithm):

    def Initialize(self):
        self.SetStartDate(2021, 1, 1)
        self.SetEndDate(2021, 12, 31)
        self.SetCash(1_000_000)
        self.SetBenchmark("SPY")

        self.SetPortfolioConstruction(InsightWeightingPortfolioConstructionModel())
        self.SetExecution(ImmediateExecutionModel())

        # Portfolio of 19 Treasury ETFs
        self.assets = [
            "SHY", "TLT", "IEI", "SHV", "TLH", "EDV", "BIL",
            "SPTL", "TBT", "TMF", "TMV", "TBF", "VGSH",
            "VGIT", "VGLT", "SCHO", "SCHR", "SPTS", "GOVT",
        ]

        for ticker in self.assets:
            self.AddEquity(ticker, Resolution.Minute)

        self.Schedule.On(
            self.DateRules.EveryDay(),
            self.TimeRules.BeforeMarketClose("SHY", 5),
            self.EveryDayBeforeMarketClose,
        )

    def EveryDayBeforeMarketClose(self):
        df = self.History(list(self.Securities.Keys), 30, Resolution.Daily)

        if df.empty:
            return

        close = df.close.unstack(level=0)

        col_mean = close.mean()
        col_std = close.std()

        # FIX (Tier 2): per-column oversold classifier (price < mean - 1*std)
        classifier = close.iloc[-1].lt(col_mean - col_std)

        if not classifier.any():
            return

        selected_cols = classifier[classifier].index.tolist()
        close_sel = close[selected_cols]

        # FIX (Tier 2): per-column z-score via scipy.stats.zscore
        z_matrix = close_sel.apply(scipy_zscore, axis=0)
        z_last = z_matrix.iloc[-1]

        col_std_sel = col_std[selected_cols]
        last_price = close_sel.iloc[-1]

        # FIX (Tier 2): correct per-column magnitude and confidence
        magnitude = (-z_last * col_std_sel / last_price).fillna(0)
        confidence = z_last.apply(lambda z: norm.cdf(-z)).fillna(0)

        weight = confidence - 1 / (magnitude.clip(lower=0) + 1)
        weight = weight[weight > 0]

        if weight.empty:
            return

        total = weight.sum()
        weight = weight / total / 2  # conservative sizing

        insights = []
        for symbol in weight.index:
            mag = float(magnitude.get(symbol, 0.0))
            conf = float(confidence.get(symbol, 0.5))
            wt = float(weight[symbol])

            # BUG (Tier 2): weight not clipped to [0,1]; InsightWeighting
            # expects weight in [0,1] — large magnitude can push wt > 1
            insights.append(
                Insight.Price(
                    symbol,
                    timedelta(days=1),
                    InsightDirection.Up,
                    mag,
                    conf,
                    None,
                    wt,
                )
            )

        self.EmitInsights(insights)
