# Reference Strategies

These are **proven working strategies** stolen from QuantConnect documentation and community forums. They are used as reference implementations to validate the ACB pipeline infrastructure.

## Purpose

- **Phase 1 Testing:** Prove QC REST API can upload, compile, and backtest real code
- **Quality Gate Validation:** Verify `pre_commit_gates.py` works on production-quality code
- **Aider Comparison:** When Aider generates strategies from specs, compare output structure against these references
- **3-Level Validation Ladder:** Test Aider capability across simple → medium → hard complexity

## Files

### Level 1: `sma_crossover_simple.py` (23 lines) — SIMPLE
- **Source:** [QuantScripts Moving Average Tutorial](https://quantscripts.com/the-basic-moving-average-crossover-strategy/)
- **Strategy:** SPY trading with SMA(10) / SMA(50) crossover
- **Period:** 2021-01-01 to 2021-12-31
- **Capital:** $10,000
- **Resolution:** Daily
- **Target Metrics:**
  - Sharpe Ratio: 0.8 ± 0.3
  - Total Return: 12% ± 5%
  - Max Drawdown: -18% ± 5%
  - Trades: 10-20
- **Complexity Factors:**
  - Single indicator (SMA)
  - Simple crossover logic
  - No risk management
  - Daily resolution
- **Use Case:** Minimal viable QC algorithm for REST API testing
- **Aider Pass Criteria:** Match Sharpe 0.8 ±0.3 in 4 tiers

### Level 2: `vwap_ema_crossover.py` (50 lines) — MEDIUM
- **Source:** [QC Forum EMA/VWAP Strategy](https://www.quantconnect.com/forum/discussion/10644/ema-vwap-strategy/)
- **Strategy:** SPY trading with VWAP/EMA(30) crossover
- **Period:** 2022-01-01 to 2023-01-01
- **Capital:** $10,000
- **Resolution:** Minute
- **Target Metrics:**
  - Sharpe Ratio: 1.0 ± 0.5
  - Total Return: 15% ± 8%
  - Max Drawdown: -20% ± 7%
  - Trades: 50-100
- **Complexity Factors:**
  - Two indicators (EMA + VWAP)
  - Minute resolution (more data)
  - State tracking for positions
  - Higher trade frequency
- **Use Case:** Prove Aider can handle multi-indicator logic
- **Aider Pass Criteria:** Match Sharpe 1.0 ±0.5 in 4 tiers

### Level 3: `mean_reversion_multi_asset.py` (150 lines) — HARD
- **Source:** [QC Mean Reversion Documentation](https://www.quantconnect.com/docs/v2/research-environment/applying-research/mean-reversion)
- **Strategy:** Portfolio of 18 Treasury ETFs, z-score < -1 entry, daily rebalancing
- **Period:** 2021-01-01 to 2021-12-31
- **Capital:** $1,000,000
- **Resolution:** Minute (but daily rebalancing)
- **Target Metrics:**
  - Sharpe Ratio: 1.2 ± 0.6
  - Total Return: 8% ± 5% (bonds are lower volatility)
  - Max Drawdown: -12% ± 4%
  - Trades: 200-400
- **Complexity Factors:**
  - 18 assets (multi-asset portfolio)
  - Statistical calculations (z-score, rolling mean/std)
  - Framework architecture (InsightWeighting, ScheduledEvent)
  - Probability calculations (magnitude, confidence)
  - Weight normalization
  - Daily rebalancing logic
- **Use Case:** Test Aider ceiling — can it handle framework-level complexity?
- **Aider Pass Criteria:** Match Sharpe 1.2 ±0.6 in 4 tiers (or document failure threshold)

---

## Manual Testing Instructions

1. Go to https://www.quantconnect.com/terminal
2. Create new project
3. Copy-paste code from any file
4. Click "Backtest"
5. Verify:
   - Compiles successfully (green)
   - Returns Sharpe ratio (any value)
   - Completes in < 5 minutes
6. Save backtest ID and metrics for CI test validation

---

## CI Test Expectations

When `qc_rest_client.py` backtests these files:
- ✅ Compilation must succeed
- ✅ Backtest must complete (not timeout)
- ✅ Result JSON must contain `Statistics["Sharpe Ratio"]` as a float
- ✅ Sharpe ratio should match manual QC UI result within tolerance

---

## Quality Gate Expectations

When `pre_commit_gates.py` validates these files:
- ✅ Stub detection: PASS (no `pass`, `TODO`, or placeholders)
- ✅ CCN: < 10 per function
- ✅ Bandit: No HIGH findings
- ✅ Function length: < 150 lines
- ✅ Params: < 8 per function

If gates fail on these production-quality files, gate thresholds need adjustment (not the code).

---

## 3-Level Validation Ladder Logic

**Goal:** Test Aider's capability ceiling by attempting strategies of increasing complexity.

**Hypothesis:** Aider can iteratively build strategies up to complexity level X in 4 tiers.

**Test Design:**
1. **Level 1 (Simple):** Proves Aider can follow basic prompt → code → backtest loop
2. **Level 2 (Medium):** Proves Aider can handle multi-indicator logic + iteration feedback
3. **Level 3 (Hard):** Proves Aider can manage portfolio logic, statistical calculations, framework architecture

**Outcome Analysis:**
- ✅✅✅ → Aider works up to complex strategies (4 tiers sufficient)
- ✅✅❌ → Aider ceiling is ~50 lines / 2 indicators (most probes fit here, still useful)
- ✅❌❌ → Prompt engineering needs work (not tier count)
- ❌❌❌ → Aider integration broken (infrastructure issue)

**Why This Works:**
Each failure point gives actionable data:
- If Level 3 fails but Level 2 passes → add more tiers OR simplify complex specs
- If Level 2 fails → fix feedback loop clarity
- If Level 1 fails → QC API or prompt structure issue

**Next Steps After Validation:**
- If all 3 pass → attempt VWAP probe build from `specs/vwap_probe.yaml`
- If Level 2 passes → use ACB for Level 1-2 complexity strategies
- If Level 1 only → use infrastructure for manual strategy uploads
