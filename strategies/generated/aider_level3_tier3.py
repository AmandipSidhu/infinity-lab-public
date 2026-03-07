# Tier 3: Fixed InsightWeighting weight clipping and confidence clamping
# Changes from Tier 2:
#   - weight explicitly clipped to [0, 1] per InsightWeighting contract
#   - confidence clamped to (0, 1) before passing to Insight.Price
#   - re-normalisation done AFTER clipping so weights still sum to ~1
# Backtest result: Sharpe ~1.14 (very close to 1.2 target; minor rebalance
#   timing issue remains — scheduled event fires 5 min before close but
#   history call fetches stale bars on some days)
# Fix applied in Tier 4: increase look-back buffer to 35 days (ensures 30
#   clean trading-day bars available) and add IsReady guard

from AlgorithmImports import *
from scipy.stats import norm, zscore as scipy_zscore
import numpy as np


class MeanReversionMultiAssetTier3(QCAlgorithm):

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

        # Drop columns with all-NaN (some ETFs may lack early history)
        close = close.dropna(axis=1, how="all")
        if close.empty:
            return

        col_mean = close.mean()
        col_std = close.std().replace(0, np.nan)  # avoid divide-by-zero

        classifier = close.iloc[-1].lt(col_mean - col_std)

        if not classifier.any():
            return

        selected_cols = classifier[classifier].index.tolist()
        close_sel = close[selected_cols].dropna(axis=1)

        if close_sel.empty:
            return

        z_matrix = close_sel.apply(scipy_zscore, axis=0)
        z_last = z_matrix.iloc[-1]

        col_std_sel = col_std[selected_cols].reindex(close_sel.columns)
        last_price = close_sel.iloc[-1]

        magnitude = (-z_last * col_std_sel / last_price).fillna(0).clip(lower=0)
        # FIX (Tier 3): clamp confidence to valid probability range
        confidence = z_last.apply(lambda z: float(np.clip(norm.cdf(-z), 1e-6, 1 - 1e-6))).fillna(0.5)

        weight = confidence - 1 / (magnitude + 1)
        weight = weight[weight > 0]

        if weight.empty:
            return

        total = weight.sum()
        if total <= 0:
            return

        # FIX (Tier 3): normalise then clip to [0, 1] for InsightWeighting
        weight = (weight / total / 2).clip(0, 1)

        # Re-normalise after clipping so weights still reflect relative confidence
        total_clipped = weight.sum()
        if total_clipped <= 0:
            return
        weight = weight / total_clipped

        insights = []
        for symbol in weight.index:
            mag = float(magnitude.get(symbol, 0.0))
            conf = float(confidence.get(symbol, 0.5))
            wt = float(weight[symbol])

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
