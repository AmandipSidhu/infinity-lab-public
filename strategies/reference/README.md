# Reference Strategies

These are **proven working strategies** stolen from QuantConnect documentation and community forums. They are used as reference implementations to validate the ACB pipeline infrastructure.

## Purpose

- **Phase 1 Testing:** Prove QC REST API can upload, compile, and backtest real code
- **Quality Gate Validation:** Verify `pre_commit_gates.py` works on production-quality code
- **Aider Comparison:** When Aider generates strategies from specs, compare output structure against these references

## Files

### `sma_crossover_simple.py` (23 lines)
- **Source:** [QuantScripts Moving Average Tutorial](https://quantscripts.com/the-basic-moving-average-crossover-strategy/)
- **Strategy:** SPY trading with SMA(10) / SMA(50) crossover
- **Period:** 2021-01-01 to 2021-12-31
- **Capital:** $10,000
- **Features:** Warmup period, simple entry/exit logic
- **Use Case:** Minimal viable QC algorithm for REST API testing

### `sma_crossover_robust.py` (60 lines)
- **Source:** [QC Forum Discussion](https://www.quantconnect.com/forum/discussion/18068/plotting-a-line-from-the-buy-point-to-the-sell-point/)
- **Strategy:** SPY trading with SMA(50) / SMA(200) crossover
- **Period:** 2020-01-01 to 2022-03-30
- **Capital:** $100,000
- **Features:** Position tracking, detailed logging, crossover detection with previous value comparison
- **Use Case:** More realistic algorithm structure with state management

## Manual Testing Instructions

1. Go to https://www.quantconnect.com/terminal
2. Create new project
3. Copy-paste code from either file
4. Click "Backtest"
5. Verify:
   - Compiles successfully (green)
   - Returns Sharpe ratio (any value)
   - Completes in < 2 minutes
6. Save backtest ID and Sharpe ratio for CI test validation

## CI Test Expectations

When `qc_rest_client.py` backtests these files:
- Compilation must succeed
- Backtest must complete (not timeout)
- Result JSON must contain `Statistics["Sharpe Ratio"]` as a float
- Sharpe ratio should match manual QC UI result within ±0.1

## Quality Gate Expectations

When `pre_commit_gates.py` validates these files:
- ✅ Stub detection: PASS (no `pass`, `TODO`, or placeholders)
- ✅ CCN: < 10 per function
- ✅ Bandit: No HIGH findings
- ✅ Function length: < 150 lines
- ✅ Params: < 8 per function

If gates fail on these production-quality files, gate thresholds need adjustment (not the code).
