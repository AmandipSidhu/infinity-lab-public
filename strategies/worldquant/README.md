# WorldQuant Strategy Templates

This directory contains pre-coded QuantConnect strategy templates based on WorldQuant 101 Formulaic Alphas.

## Templates Available

### Momentum Strategies
1. **alpha_001_volume_price_correlation.py** - Volume-price relationship momentum
2. **alpha_011_volume_momentum.py** - Pure volume momentum
3. **alpha_024_closing_momentum.py** - Close-to-close momentum with decay

### Mean Reversion Strategies
4. **alpha_007_price_volume_reversion.py** - Price-volume mean reversion
5. **alpha_012_rank_reversion.py** - Cross-sectional rank reversion
6. **alpha_016_covariance_reversion.py** - Covariance-based reversion

### Arbitrage Strategies
7. **alpha_018_correlation_arbitrage.py** - Correlation-based statistical arbitrage
8. **alpha_022_delta_arbitrage.py** - Delta-neutral arbitrage

### Pairs Trading
9. **alpha_026_pairs_correlation.py** - Correlation-based pairs trading
10. **alpha_030_pairs_volume.py** - Volume-weighted pairs trading

### Volatility Strategies
11. **alpha_031_volatility_momentum.py** - Volatility momentum
12. **alpha_035_rank_volatility.py** - Ranked volatility trading

### Multi-Factor Strategies
13. **alpha_041_multi_factor.py** - Combined price-volume factors
14. **alpha_044_correlation_factors.py** - Multi-correlation factors
15. **alpha_049_sector_rotation.py** - Sector rotation based on volume

### Advanced Patterns
16. **alpha_052_residual_trading.py** - Residual-based trading
17. **alpha_056_rank_correlation.py** - Rank correlation trading
18. **alpha_060_volume_delta.py** - Volume delta patterns
19. **alpha_068_high_low_spread.py** - High-low spread trading
20. **alpha_084_vwap_reversion.py** - VWAP mean reversion

## Usage

Each template is a complete QuantConnect strategy ready for modification. To use:

1. Copy template to your strategies directory
2. Modify parameters (lookback periods, thresholds, etc.)
3. Add risk management (stop loss, position sizing)
4. Backtest on QuantConnect
5. Deploy to live trading

## Template Structure

All templates follow this structure:
```python
class WorldQuantAlphaXXX(QCAlgorithm):
    def Initialize(self):
        # Setup: dates, symbols, indicators
        
    def OnData(self, data):
        # Alpha signal calculation
        # Entry/exit logic
        # Risk management
```

## References

- WorldQuant 101 Alphas: https://arxiv.org/pdf/1601.00991.pdf
- DolphinDB Alpha Docs: https://docs.dolphindb.com/en/Tutorials/wq101alpha.html
- QuantConnect Docs: https://www.quantconnect.com/docs/v2
