# SPEC_VALIDATION_RULES.md — Spec Validator Rule Library (SVR)

This document is the formal specification for all 56 deterministic validation rules implemented in `scripts/spec_validator.py`. Rules are divided into **Errors** (SVR-E) and **Warnings** (SVR-W).

- **ERROR**: Spec is rejected. Build fails (`exit code 1`). Must be fixed before proceeding.
- **WARNING**: Spec has a risky or incomplete element. Build continues but the ACK gate may require human acknowledgment.

---

## ERROR Rules (SVR-E) — 30 Rules

### Structural / Required Fields

| Code | Field | Rule | Fix |
|------|-------|------|-----|
| SVR-E001 | `metadata.name` | Missing or empty string | Add a non-empty `name` to `metadata` |
| SVR-E002 | `metadata.version` | Missing or empty string | Add a non-empty `version` (e.g., `"1.0.0"`) |
| SVR-E003 | `metadata.description` | Missing or empty string | Describe what the strategy does in ≥1 sentence |
| SVR-E004 | `strategy` | Top-level `strategy` key is missing | Add the `strategy` block |
| SVR-E005 | `strategy.type` | Missing or not one of the allowed types | Use one of: `momentum`, `mean_reversion`, `trend_following`, `arbitrage`, `market_making`, `statistical_arb`, `pairs_trading`, `breakout`, `volatility` |
| SVR-E006 | `strategy.universe.symbols` | Missing or empty list | Specify at least one ticker symbol |
| SVR-E007 | `strategy.universe.resolution` | Missing or not a valid resolution | Use one of: `tick`, `second`, `minute`, `hour`, `daily`, `weekly` |
| SVR-E008 | `strategy.signals` | Missing `signals` block | Add `signals` with `entry` and `exit` sub-sections |
| SVR-E009 | `strategy.signals.entry` | Missing `entry` section | Define at least one entry condition |
| SVR-E010 | `strategy.signals.exit` | Missing `exit` section | Define at least one exit condition |
| SVR-E011 | `strategy.signals.entry.conditions` | Missing or empty list | Provide at least one explicit, quantifiable entry condition |
| SVR-E012 | `strategy.signals.exit.conditions` | Missing or empty list | Provide at least one explicit, quantifiable exit condition |
| SVR-E013 | `strategy.risk_management` | Missing `risk_management` block | Add position sizing, stop-loss, and drawdown rules |
| SVR-E014 | `strategy.risk_management.stop_loss` | Missing | Define a stop-loss fraction |
| SVR-E015 | `strategy.risk_management.max_position_size` | Missing | Define the maximum position size as a fraction of portfolio |
| SVR-E016 | `strategy.performance_targets` | Missing `performance_targets` block | Add Sharpe ratio minimum and drawdown threshold |
| SVR-E017 | `strategy.performance_targets.sharpe_ratio_min` | Missing | Specify minimum acceptable Sharpe ratio |
| SVR-E018 | `strategy.performance_targets.max_drawdown_threshold` | Missing | Specify the maximum acceptable drawdown level |
| SVR-E019 | `strategy.backtesting` | Missing `backtesting` block | Add backtesting parameters |
| SVR-E020 | `strategy.backtesting.start_date` | Missing | Provide a start date in `YYYY-MM-DD` format |
| SVR-E021 | `strategy.backtesting.end_date` | Missing | Provide an end date in `YYYY-MM-DD` format |
| SVR-E022 | `strategy.backtesting.initial_capital` | Missing or ≤ 0 | Provide a positive initial capital value in USD |
| SVR-E023 | `strategy.backtesting.min_trades` | Missing or < 100 | Minimum 100 trades required for statistical evidence |

### Value Constraint Errors

| Code | Field | Rule | Fix |
|------|-------|------|-----|
| SVR-E024 | `strategy.backtesting.start_date` | Not in `YYYY-MM-DD` format | Fix date format (e.g., `"2020-01-01"`) |
| SVR-E025 | `strategy.backtesting.end_date` | Not in `YYYY-MM-DD` format | Fix date format (e.g., `"2024-12-31"`) |
| SVR-E026 | `strategy.backtesting` date order | `start_date` is not before `end_date` | Ensure `start_date < end_date` |
| SVR-E027 | `strategy.risk_management.stop_loss` | Value > 0.20 (more than 20% stop-loss is recklessly wide) | Reduce stop-loss to ≤ 0.20 |
| SVR-E028 | `strategy.risk_management.max_position_size` | Value ≤ 0 or > 1.0 | Must be a fraction in (0, 1.0] |
| SVR-E029 | `strategy.risk_management.max_leverage` | Value > 3.0 (excessive leverage — auto-reject) | Reduce leverage to ≤ 3.0 |
| SVR-E030 | Ambiguous signal language | Any entry or exit condition contains vague, non-quantifiable phrases (e.g., "as needed", "when appropriate", "good time", "user-friendly", "looks good", "seems right", "feel", "intuition", "sometimes") | Replace with explicit numeric thresholds |

---

## WARNING Rules (SVR-W) — 26 Rules

### Missing Recommended Fields

| Code | Field | Rule | Suggestion |
|------|-------|------|------------|
| SVR-W001 | `metadata.author` | Missing `author` field | Add the strategy author's name |
| SVR-W002 | `metadata.created_at` | Missing or not in `YYYY-MM-DD` format | Add a creation date for audit trail |
| SVR-W003 | `strategy.universe` | Missing `universe` block | Define symbol list and resolution |
| SVR-W004 | `strategy.risk_management.position_sizing` | Missing `position_sizing` method | Specify `fixed`, `percentage`, `volatility_adjusted`, or `kelly` |
| SVR-W005 | `strategy.risk_management.take_profit` | Missing `take_profit` level | Define a profit target to avoid holding losers |
| SVR-W006 | `strategy.risk_management.max_drawdown` | Missing `max_drawdown` portfolio limit | Set a portfolio drawdown limit |
| SVR-W007 | `strategy.performance_targets.win_rate_min` | Missing | Specify a minimum win rate target |
| SVR-W008 | `strategy.backtesting.benchmark` | Missing benchmark ticker | Add a benchmark (e.g., `SPY`) for relative performance comparison |
| SVR-W009 | `strategy.data_requirements` | Missing `data_requirements` block | Specify data resolution, history depth, and indicators used |
| SVR-W010 | `strategy.data_requirements.indicators` | Missing or empty list | List technical indicators used for reproducibility |

### Value Quality Warnings

| Code | Field | Rule | Suggestion |
|------|-------|------|------------|
| SVR-W011 | `strategy.backtesting.min_trades` | Value < 1000 | Consider ≥1000 trades for high statistical confidence |
| SVR-W012 | `strategy.performance_targets.sharpe_ratio_min` | Value < 1.0 | Industry standard minimum Sharpe is 1.0 |
| SVR-W013 | `strategy.performance_targets.win_rate_min` | Value < 0.50 | Win rate below 50% is suboptimal without high reward-to-risk |
| SVR-W014 | `strategy.performance_targets.sharpe_ratio_min` | Value > 5.0 | Sharpe > 5.0 is likely curve-fitted; consider lowering the target |
| SVR-W015 | `strategy.risk_management.max_drawdown` | Value > 0.50 | Max drawdown > 50% suggests insufficient risk control |
| SVR-W016 | `strategy.performance_targets.max_drawdown_threshold` | Value > 0.30 | Threshold > 30% is high; recommend ≤ 0.20 |
| SVR-W017 | `strategy.backtesting.initial_capital` | Value < 10000 | Capital < $10,000 may produce unrealistic fill assumptions |
| SVR-W018 | `strategy.backtesting.end_date` | Date is in the future | Future end dates introduce look-ahead bias risk |
| SVR-W019 | Backtesting period | `end_date - start_date` < 730 days (< 2 years) | Use ≥ 2 years to capture multiple market conditions |
| SVR-W020 | `strategy.universe.symbols` | Only 1 symbol | Single-symbol strategies carry concentration risk |
| SVR-W021 | `strategy.risk_management.stop_loss` | Value ≤ 0 | Stop-loss must be positive to be effective |
| SVR-W022 | `strategy.data_requirements` | Missing `min_history_days` | Specify minimum data history to ensure indicator warm-up |

### Pattern Detection Warnings

| Code | Field | Rule | Suggestion |
|------|-------|------|------------|
| SVR-W023 | Entry/Exit signals | Entry conditions contain no numeric threshold | Add explicit numeric values (e.g., RSI > 70, SMA cross) |
| SVR-W024 | Entry/Exit signals | Exit conditions contain no numeric threshold | Add explicit numeric values for exit triggers |
| SVR-W025 | `strategy.type` | Type is `market_making` with no `resolution` finer than `minute` | Market-making typically requires sub-minute resolution |
| SVR-W026 | `strategy.risk_management.max_leverage` | Value > 1.0 | Leveraged strategies carry amplified drawdown risk; document rationale |

---

## Ambiguous Language Patterns (SVR-E030)

The following regular expression patterns in any `conditions` string trigger SVR-E030:

```
as needed
when appropriate
good time
looks good
seems right
feel(s)?
intuition
sometimes
user.friendly
market condition(s)? permit
discretion
whenever possible
at some point
```

---

## Numeric Threshold Pattern (SVR-W023, SVR-W024)

A condition string is considered to contain a "numeric threshold" if it matches:

```regex
\d+(\.\d+)?
```

i.e., it contains at least one number. Conditions without any number trigger SVR-W023 or SVR-W024.

---

## Auto-Reject Patterns

The following patterns in metadata or signal descriptions trigger hard errors:

| Pattern | Trigger | Rule |
|---------|---------|------|
| `max_leverage > 3.0` | Numeric field | SVR-E029 |
| Vague language in conditions | Text match | SVR-E030 |
| `stop_loss > 0.20` | Numeric field | SVR-E027 |

---

## Acceptance Criteria for the Validator

| Metric | Target |
|--------|--------|
| Precision (valid specs that pass) | ≥ 80% |
| Recall (invalid specs rejected) | ≥ 90% |
| False positives | < 20% |
| False negatives | < 10% |

---

## Error Message Template

Each finding must include:
- **code**: `SVR-E###` or `SVR-W###`
- **severity**: `ERROR` or `WARNING`
- **message**: What is wrong
- **field**: The YAML path of the offending field (if applicable)

---

## Appendix: Complete Rule Code Index

**Errors:** SVR-E001 through SVR-E030  
**Warnings:** SVR-W001 through SVR-W026  
**Total:** 56 rules
