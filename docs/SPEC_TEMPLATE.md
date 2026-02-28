# SPEC_TEMPLATE.md — Trading Strategy Specification YAML Schema

This document defines the canonical YAML schema for an Infinity Lab trading strategy specification. Every spec file passed to `scripts/spec_validator.py` must conform to this structure.

---

## Full Schema with Annotations

```yaml
# ────────────────────────────────────────────────
# SECTION 1: Metadata
# ────────────────────────────────────────────────
metadata:
  name: "My Strategy Name"          # string, required — short identifier
  version: "1.0.0"                  # string, required — semver format
  author: "Jane Doe"                # string, recommended
  created_at: "2026-01-15"          # string, recommended — YYYY-MM-DD
  description: >                    # string, required — 1+ sentences; no vague phrases
    A clear description of what this strategy does and why.

# ────────────────────────────────────────────────
# SECTION 2: Strategy Core
# ────────────────────────────────────────────────
strategy:
  type: "momentum"                  # string, required — one of:
                                    #   momentum | mean_reversion | trend_following |
                                    #   arbitrage | market_making | statistical_arb |
                                    #   pairs_trading | breakout | volatility

  # ── 2a. Universe Selection ──────────────────
  universe:
    symbols:                        # list[str], required — at least 1 ticker
      - "AAPL"
      - "MSFT"
    resolution: "daily"             # string, required — one of:
                                    #   tick | second | minute | hour | daily | weekly
    market: "equities"              # string, optional — equities | futures | forex | crypto

  # ── 2b. Signal Definitions ──────────────────
  signals:
    entry:
      conditions:                   # list[str], required — at least 1 explicit condition
        - "50-day SMA crosses above 200-day SMA"
        - "RSI(14) < 70"
      logic: "AND"                  # string, optional — AND | OR (default AND)

    exit:
      conditions:                   # list[str], required — at least 1 explicit condition
        - "50-day SMA crosses below 200-day SMA"
        - "RSI(14) > 80"
      logic: "OR"                   # string, optional — AND | OR (default OR)

  # ── 2c. Risk Management ─────────────────────
  risk_management:
    position_sizing: "percentage"   # string, recommended — fixed | percentage |
                                    #   volatility_adjusted | kelly
    max_position_size: 0.10         # float, required — max fraction of portfolio per trade (0, 1]
    stop_loss: 0.05                 # float, required — max loss per trade as fraction (0, 0.20]
    take_profit: 0.15               # float, optional — profit target as fraction (> 0)
    max_drawdown: 0.20              # float, recommended — portfolio-level drawdown limit (0, 1]
    max_leverage: 1.0               # float, optional — leverage multiplier (default 1.0; max 3.0)

  # ── 2d. Performance Targets ─────────────────
  performance_targets:
    sharpe_ratio_min: 1.0           # float, required — minimum acceptable Sharpe ratio (> 0)
    win_rate_min: 0.50              # float, recommended — minimum win rate (0, 1)
    max_drawdown_threshold: 0.20    # float, required — alert if drawdown exceeds this

  # ── 2e. Backtesting Parameters ──────────────
  backtesting:
    start_date: "2020-01-01"        # string, required — YYYY-MM-DD; must be < end_date
    end_date: "2024-12-31"          # string, required — YYYY-MM-DD; must be <= today
    initial_capital: 100000         # number, required — starting capital in USD (> 0)
    min_trades: 200                 # integer, required — minimum trades for statistical validity (>= 100)
    benchmark: "SPY"                # string, recommended — comparison ticker

  # ── 2f. Data Requirements ───────────────────
  data_requirements:
    resolution: "daily"             # string, required here if different from universe.resolution
    min_history_days: 504           # integer, optional — minimum historical data needed
    indicators:                     # list[str], optional — any technical indicators used
      - "SMA(50)"
      - "SMA(200)"
      - "RSI(14)"
```

---

## Field Reference Table

| Path | Type | Required | Notes |
|------|------|----------|-------|
| `metadata.name` | string | ✅ | Non-empty |
| `metadata.version` | string | ✅ | Non-empty |
| `metadata.author` | string | ⚠️ Warn if missing | |
| `metadata.created_at` | string | ⚠️ Warn if missing | YYYY-MM-DD |
| `metadata.description` | string | ✅ | Non-empty, no vague language |
| `strategy` | mapping | ✅ | Top-level section |
| `strategy.type` | string | ✅ | One of the allowed types |
| `strategy.universe` | mapping | ⚠️ Warn if missing | |
| `strategy.universe.symbols` | list | ✅ | At least 1 symbol |
| `strategy.universe.resolution` | string | ✅ | Valid resolution value |
| `strategy.signals` | mapping | ✅ | |
| `strategy.signals.entry` | mapping | ✅ | |
| `strategy.signals.entry.conditions` | list | ✅ | ≥1 condition, no vague language |
| `strategy.signals.exit` | mapping | ✅ | |
| `strategy.signals.exit.conditions` | list | ✅ | ≥1 condition, no vague language |
| `strategy.risk_management` | mapping | ✅ | |
| `strategy.risk_management.stop_loss` | float | ✅ | (0, 0.20] |
| `strategy.risk_management.max_position_size` | float | ✅ | (0, 1.0] |
| `strategy.risk_management.position_sizing` | string | ⚠️ Warn if missing | |
| `strategy.risk_management.max_drawdown` | float | ⚠️ Warn if missing | (0, 1.0] |
| `strategy.risk_management.take_profit` | float | ⚠️ Warn if missing | > 0 |
| `strategy.risk_management.max_leverage` | float | optional | ≤ 3.0 |
| `strategy.performance_targets` | mapping | ✅ | |
| `strategy.performance_targets.sharpe_ratio_min` | float | ✅ | > 0 |
| `strategy.performance_targets.max_drawdown_threshold` | float | ✅ | (0, 1.0] |
| `strategy.performance_targets.win_rate_min` | float | ⚠️ Warn if missing | (0, 1.0) |
| `strategy.backtesting` | mapping | ✅ | |
| `strategy.backtesting.start_date` | string | ✅ | YYYY-MM-DD, < end_date |
| `strategy.backtesting.end_date` | string | ✅ | YYYY-MM-DD, ≤ today |
| `strategy.backtesting.initial_capital` | number | ✅ | > 0 |
| `strategy.backtesting.min_trades` | integer | ✅ | ≥ 100 |
| `strategy.backtesting.benchmark` | string | ⚠️ Warn if missing | |
| `strategy.data_requirements` | mapping | ⚠️ Warn if missing | |
| `strategy.data_requirements.indicators` | list | ⚠️ Warn if missing | |

---

## Example Valid Spec

See `tests/spec_corpus/valid_001.yaml` for a complete working example.
