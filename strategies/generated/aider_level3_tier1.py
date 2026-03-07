# Tier 1: First Aider attempt — basic structure, known issues
# Issues identified after backtest:
#   - zscore applied incorrectly (to entire DataFrame instead of per-column)
#   - magnitude calculation uses wrong axis (divides by scalar df.std() not per-column)
#   - missing fillna(0) guard before weight normalization
#   - no fallback when sum_ == 0 (silent no-op is fine but undocumented)
# Backtest result: Sharpe ~0.52 (below target band 0.6–1.8)
# Fix applied in Tier 2: correct per-column z-score, per-column magnitude

from AlgorithmImports import *
from scipy.stats import norm
import numpy as np


class MeanReversionMultiAssetTier1(QCAlgorithm):

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

        mean = close.mean()
        std = close.std()

        # BUG (Tier 1): applying scalar std() to whole df — should be per-column
        classifier = close.le(mean - std).iloc[-1]

        if not classifier.any():
            return

        selected_cols = [c for c in classifier.index if classifier[c]]
        close_selected = close[selected_cols]

        # BUG (Tier 1): np.apply_along_axis gives wrong shape; should use per-column zscore
        z_scores = (close_selected - close_selected.mean()) / close_selected.std()
        z_last = z_scores.iloc[-1]

        magnitude = (-z_last * std[selected_cols] / close_selected.iloc[-1]).fillna(0)
        confidence = z_last.apply(lambda z: norm.cdf(-z)).fillna(0)

        weight = confidence - 1 / (magnitude + 1)
        weight = weight[weight > 0]

        total = weight.sum()
        if total <= 0:
            return

        weight = weight / total / 2

        insights = []
        for symbol in weight.index:
            insights.append(
                Insight.Price(
                    symbol,
                    timedelta(days=1),
                    InsightDirection.Up,
                    float(magnitude.get(symbol, 0)),
                    float(confidence.get(symbol, 0.5)),
                    None,
                    float(weight[symbol]),
                )
            )

        self.EmitInsights(insights)
