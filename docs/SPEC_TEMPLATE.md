# SPEC_TEMPLATE.md — Trading Strategy Specification YAML Schema

This document defines the canonical YAML schema for an Infinity Lab trading strategy specification. Every spec file passed to `scripts/spec_validator.py` must conform to this structure.

> **Schema version**: v2 (flat top-level sections — NOT nested under `strategy`).
> Sections in validation order: `metadata` → `capital` → `constraints` → `data` → `signals` → `risk_management` → `acceptance_criteria` → `assumptions` → `notes`

---

## Quick Reference: ERROR-Triggering Fields

| SVR Code | Field | Rule |
|----------|-------|------|
| SVR-E001 | `metadata.trading_style` | Missing or empty |
| SVR-E002 | `metadata.trading_style` | Not in `day_trade\|swing\|position` |
| SVR-E003 | `capital` | Neither `allocation_usd` nor `allocation_pct` present |
| SVR-E004 | `capital.allocation_usd` / `capital.allocation_pct` | Present but ≤ 0 |
| SVR-E005 | `metadata.name` | Missing or empty |
| SVR-E011 | `constraints.max_holding_minutes` | Missing when `trading_style: day_trade` |
| SVR-E012 | `constraints.max_holding_minutes` | > 390 (NYSE session limit) |
| SVR-E013 | `constraints.close_eod` | Not `true` when `trading_style: day_trade` |
| SVR-E021 | `acceptance_criteria.max_drawdown_pct` | Missing |
| SVR-E022 | `acceptance_criteria.max_drawdown_pct` | ≤ 0 |
| SVR-E023 | `risk_management.stop_loss` | Missing (need `pct`, `atr_multiplier`, or `absolute_usd`) |
| SVR-E024 | `risk_management.position_sizing` | Missing or empty |
| SVR-E025 | `risk_management.leverage` | > 4 |
| SVR-E026 | `risk_management.leverage` | "margin"/"futures" in spec but no leverage field |
| SVR-E031 | `signals.entry` | Missing or empty list |
| SVR-E032 | `signals.entry` | No condition contains a numeric threshold |
| SVR-E033 | `signals.exit` | Missing or empty list |
| SVR-E034 | `signals.entry` / `signals.exit` | Contains banned vague terms |
| SVR-E046 | `acceptance_criteria.min_sharpe_ratio` | Missing |
| SVR-E047 | `acceptance_criteria.min_sharpe_ratio` | ≤ 0 |
| SVR-E048 | `acceptance_criteria.min_profit_factor` | Missing |
| SVR-E049 | `acceptance_criteria.min_profit_factor` | ≤ 0 |
| SVR-E050 | `acceptance_criteria.min_trades` | Missing |
| SVR-E051 | `acceptance_criteria.min_trades` | ≤ 0 |
| SVR-E056 | `data.instruments` / `data.universe` | Neither present with valid content |
| SVR-E056a | `data.universe.screener.criteria` | Dynamic mode but criteria missing/invalid |
| SVR-E056b | `data.universe.screener.max_symbols` | Dynamic mode but max_symbols missing or ≤ 0 |
| SVR-E057 | `data.resolution` | Missing or not in `tick\|second\|minute\|hour\|daily` |
| SVR-E058 | `data.start_date` + `data.end_date` / `data.lookback_years` | Neither defined |
| SVR-E059 | Date range | < 2 years |
| SVR-E060 | Date range | `start_date ≥ end_date` |
| SVR-E066 | Signal conditions | Lookahead bias patterns detected |
| SVR-E067 | Signal conditions | Non-deterministic entry patterns detected |
| SVR-E068 | Signal conditions | Unavailable/non-public data source referenced |

---

## Common Mistakes

| Wrong (old schema) | Correct (new schema) | SVR Code |
|--------------------|----------------------|----------|
| `strategy.type: mean_reversion` | `metadata.trading_style: day_trade` | SVR-E001/E002 |
| `strategy.universe.symbols: [SPY]` | `data.instruments: [SPY]` | SVR-E056 |
| `strategy.universe.resolution: minute` | `data.resolution: minute` | SVR-E057 |
| `strategy.signals.entry.conditions: [...]` | `signals.entry: [...]` (flat list) | SVR-E031 |
| `strategy.risk_management.stop_loss: 0.04` | `risk_management.stop_loss: {pct: 0.04}` | SVR-E023 |
| `strategy.performance_targets.sharpe_ratio_min: 1.2` | `acceptance_criteria.min_sharpe_ratio: 1.2` | SVR-E046 |
| `strategy.backtesting.start_date: ...` | `data.start_date: ...` | SVR-E058 |
| `strategy.backtesting.initial_capital: 100000` | `capital.allocation_usd: 100000` | SVR-E003 |
| `strategy.backtesting.min_trades: 200` | `acceptance_criteria.min_trades: 200` | SVR-E050 |

---

## Full Schema with Annotations

```yaml
# ────────────────────────────────────────────────────────────────────────────
# SECTION 1: Metadata
# ────────────────────────────────────────────────────────────────────────────
metadata:
  name: "My Strategy Name"          # REQUIRED (SVR-E005) — short identifier
  trading_style: "day_trade"        # REQUIRED (SVR-E001/E002) — enum:
                                    #   day_trade | swing | position
  version: "1.0.0"                  # RECOMMENDED (SVR-W062) — semver format
  author: "Jane Doe"                # RECOMMENDED (SVR-W002)
  created_at: "2026-01-15"          # OPTIONAL — YYYY-MM-DD
  description: >                    # RECOMMENDED (SVR-W001) — ≥20 chars,
    A clear description of what     #   no vague phrases
    this strategy does and why.

# ────────────────────────────────────────────────────────────────────────────
# SECTION 2: Capital Allocation
# ────────────────────────────────────────────────────────────────────────────
capital:
  allocation_usd: 100000            # REQUIRED unless allocation_pct set (SVR-E003/E004)
  # allocation_pct: 0.20            # Alternative to allocation_usd (0 < pct ≤ 1.0)

# ────────────────────────────────────────────────────────────────────────────
# SECTION 3: Constraints  [day_trade ONLY — omit for swing/position]
# ────────────────────────────────────────────────────────────────────────────
constraints:                        # Required block when trading_style: day_trade
  max_holding_minutes: 60           # REQUIRED for day_trade (SVR-E011/E012) — max 390
  close_eod: true                   # REQUIRED for day_trade (SVR-E013) — must be true

# ────────────────────────────────────────────────────────────────────────────
# SECTION 4: Data Requirements
# ────────────────────────────────────────────────────────────────────────────
data:
  # Option A — Static universe (list of specific tickers):
  instruments:                      # REQUIRED unless data.universe defined (SVR-E056)
    - "SPY"
    - "QQQ"

  # Option B — Dynamic universe (screener-based, omit instruments if using this):
  # universe:
  #   mode: dynamic                 # Must be "dynamic" to activate SVR-E056a/E056b checks
  #   screener:
  #     criteria: top_volume        # REQUIRED for dynamic (SVR-E056a) — enum:
  #                                 #   top_volume | gap_up_pct | relative_volume |
  #                                 #   float_under | custom
  #     max_symbols: 10             # REQUIRED for dynamic (SVR-E056b) — must be > 0
  #     min_price: 5.0              # OPTIONAL
  #     min_volume: 500000          # OPTIONAL

  resolution: "minute"              # REQUIRED (SVR-E057) — enum:
                                    #   tick | second | minute | hour | daily

  start_date: "2020-01-01"          # REQUIRED with end_date OR use lookback_years (SVR-E058)
  end_date: "2024-12-31"            # Range must be ≥ 2 years (SVR-E059), start < end (SVR-E060)
  # lookback_years: 5               # Alternative to start_date/end_date

# ────────────────────────────────────────────────────────────────────────────
# SECTION 5: Signal Definitions
# ────────────────────────────────────────────────────────────────────────────
signals:
  entry:                            # REQUIRED (SVR-E031) — list of condition strings
    - "50-day SMA crosses above 200-day SMA AND RSI(14) < 70"
    # ↑ At least ONE entry string must contain a digit (SVR-E032)
    # ↑ NO banned vague terms (SVR-E034):
    #   momentum, trending, oversold, overbought, volatile,
    #   reasonable, appropriate, approximately, as needed

  exit:                             # REQUIRED (SVR-E033) — list of condition strings
    - "50-day SMA crosses below 200-day SMA"
    - "Time held > 45 minutes"      # RECOMMENDED: include a time-based exit (SVR-W030)

# ────────────────────────────────────────────────────────────────────────────
# SECTION 6: Risk Management
# ────────────────────────────────────────────────────────────────────────────
risk_management:
  stop_loss:                        # REQUIRED (SVR-E023) — use at least ONE sub-field:
    pct: 0.05                       #   pct: fraction of entry price (e.g., 0.05 = 5%)
    # atr_multiplier: 2.0           #   or atr_multiplier: ATR multiple
    # absolute_usd: 500             #   or absolute_usd: fixed dollar amount

  position_sizing: "percentage"     # REQUIRED (SVR-E024) — describe the sizing method

  leverage: 1.0                     # OPTIONAL — must be ≤ 4 (SVR-E025)
                                    #   Required if spec mentions "margin" or "futures" (SVR-E026)

  max_positions: 3                  # RECOMMENDED (SVR-W020) — max concurrent positions
  risk_per_trade_pct: 1.0           # RECOMMENDED (SVR-W021) — % account at risk per trade

# ────────────────────────────────────────────────────────────────────────────
# SECTION 7: Acceptance Criteria
# ────────────────────────────────────────────────────────────────────────────
acceptance_criteria:
  max_drawdown_pct: 20.0            # REQUIRED (SVR-E021/E022) — must be > 0
  min_sharpe_ratio: 1.0             # REQUIRED (SVR-E046/E047) — must be > 0
  min_profit_factor: 1.3            # REQUIRED (SVR-E048/E049) — must be > 0
  min_trades: 200                   # REQUIRED (SVR-E050/E051) — must be > 0
  min_cagr: 15.0                    # RECOMMENDED (SVR-W040) — minimum annual return %

# ────────────────────────────────────────────────────────────────────────────
# SECTION 8: Cost Assumptions  [day_trade strongly recommended]
# ────────────────────────────────────────────────────────────────────────────
assumptions:                        # RECOMMENDED (SVR-W051)
  fees: 0.001                       # RECOMMENDED for day_trade (SVR-W010) — cost per trade
  slippage: 0.0005                  # RECOMMENDED for day_trade (SVR-W011) — must be > 0

# ────────────────────────────────────────────────────────────────────────────
# SECTION 9: Notes  [free text, not validated]
# ────────────────────────────────────────────────────────────────────────────
notes: |                            # OPTIONAL — any free-text implementation notes
  Implementation notes, lessons learned, and deployment guidance go here.
```

---

## Worked Example: `day_trade` (Zero Errors, Zero Warnings)

```yaml
metadata:
  name: "VWAP Intraday Fade"
  trading_style: day_trade
  version: "1.0.0"
  author: "Jane Doe"
  description: >-
    Intraday mean reversion strategy fading 2-sigma VWAP deviations on SPY
    with volume confirmation. ATR-based stops, 60-minute max hold, EOD flatten.

capital:
  allocation_usd: 100000

constraints:
  max_holding_minutes: 60
  close_eod: true

data:
  instruments:
    - SPY
  resolution: minute
  start_date: "2019-01-01"
  end_date: "2024-12-31"

signals:
  entry:
    - "Close < VWAP - (2 * ATR(14)) AND Volume > 1.5 * Volume_MA(20) AND time 09:45-15:45"
  exit:
    - "Close >= VWAP OR time held > 60 minutes"

risk_management:
  stop_loss:
    atr_multiplier: 2.0
  position_sizing: volatility_based
  max_positions: 1
  risk_per_trade_pct: 1.0

acceptance_criteria:
  max_drawdown_pct: 12
  min_sharpe_ratio: 1.2
  min_profit_factor: 1.5
  min_trades: 300
  min_cagr: 20.0

assumptions:
  fees: 0.001
  slippage: 0.0005

notes: "Runs during regular NYSE hours (09:30–16:00 ET) only."
```

---

## Worked Example: Dynamic Universe (Screener-Based)

```yaml
metadata:
  name: "Gap-and-Go Momentum"
  trading_style: day_trade
  version: "1.0.0"
  author: "Jane Doe"
  description: >-
    Intraday gap-and-go strategy using a dynamic screener to select top-gapping
    stocks each morning. Enters on first 5-minute bar breakout above pre-market
    high with volume confirmation. ATR stops and 30-minute max hold.

capital:
  allocation_usd: 50000

constraints:
  max_holding_minutes: 30
  close_eod: true

data:
  universe:
    mode: dynamic
    screener:
      criteria: gap_up_pct          # top_volume|gap_up_pct|relative_volume|float_under|custom
      max_symbols: 5                # must be > 0
      min_price: 5.0                # OPTIONAL filter
      min_volume: 500000            # OPTIONAL filter
  resolution: minute
  start_date: "2021-01-01"
  end_date: "2024-12-31"

signals:
  entry:
    - "Price breaks above pre-market high AND Volume > 2x average on first 5-minute bar"
  exit:
    - "Price retraces 1 * ATR(14) from entry OR time held > 30 minutes"

risk_management:
  stop_loss:
    pct: 0.03
  position_sizing: fixed_dollar
  max_positions: 2
  risk_per_trade_pct: 1.5

acceptance_criteria:
  max_drawdown_pct: 15
  min_sharpe_ratio: 1.0
  min_profit_factor: 1.3
  min_trades: 200
  min_cagr: 25.0

assumptions:
  fees: 0.001
  slippage: 0.001

notes: "Screener runs at 09:25 ET each morning."
```

---

## Field Reference Table

| Path | Type | Required | SVR Code | Notes |
|------|------|----------|----------|-------|
| `metadata.name` | string | ✅ ERROR | SVR-E005 | Non-empty |
| `metadata.trading_style` | string | ✅ ERROR | SVR-E001/E002 | `day_trade\|swing\|position` |
| `metadata.version` | string | ⚠️ WARN | SVR-W062 | Semver recommended |
| `metadata.author` | string | ⚠️ WARN | SVR-W002 | |
| `metadata.description` | string | ⚠️ WARN | SVR-W001 | ≥ 20 chars |
| `capital.allocation_usd` | number | ✅ ERROR* | SVR-E003/E004 | *Either this or `allocation_pct` |
| `capital.allocation_pct` | float | ✅ ERROR* | SVR-E003/E004 | *Alternative to `allocation_usd` |
| `constraints.max_holding_minutes` | int | ✅ ERROR† | SVR-E011/E012 | †Required for `day_trade`; max 390 |
| `constraints.close_eod` | bool | ✅ ERROR† | SVR-E013 | †Must be `true` for `day_trade` |
| `data.instruments` | list | ✅ ERROR* | SVR-E056 | *Either this or `data.universe` |
| `data.universe.mode` | string | ✅ ERROR* | SVR-E056 | *`dynamic` activates screener checks |
| `data.universe.screener.criteria` | string | ✅ ERROR† | SVR-E056a | †Required if dynamic mode |
| `data.universe.screener.max_symbols` | int | ✅ ERROR† | SVR-E056b | †Required if dynamic mode, > 0 |
| `data.resolution` | string | ✅ ERROR | SVR-E057 | `tick\|second\|minute\|hour\|daily` |
| `data.start_date` | string | ✅ ERROR* | SVR-E058 | *Either start+end or `lookback_years` |
| `data.end_date` | string | ✅ ERROR* | SVR-E058/E059/E060 | *Must form ≥ 2-year range |
| `data.lookback_years` | number | ✅ ERROR* | SVR-E058 | *Alternative to start/end dates |
| `signals.entry` | list[str] | ✅ ERROR | SVR-E031/E032/E034 | Non-empty, numeric, no vague terms |
| `signals.exit` | list[str] | ✅ ERROR | SVR-E033/E034 | Non-empty, no vague terms |
| `risk_management.stop_loss` | dict\|number | ✅ ERROR | SVR-E023 | Need `pct`, `atr_multiplier`, or `absolute_usd` |
| `risk_management.position_sizing` | string | ✅ ERROR | SVR-E024 | Non-empty |
| `risk_management.leverage` | float | optional | SVR-E025/E026 | Max 4; required if margin/futures mentioned |
| `risk_management.max_positions` | int | ⚠️ WARN | SVR-W020 | |
| `risk_management.risk_per_trade_pct` | float | ⚠️ WARN | SVR-W021 | |
| `acceptance_criteria.max_drawdown_pct` | float | ✅ ERROR | SVR-E021/E022 | > 0 |
| `acceptance_criteria.min_sharpe_ratio` | float | ✅ ERROR | SVR-E046/E047 | > 0 |
| `acceptance_criteria.min_profit_factor` | float | ✅ ERROR | SVR-E048/E049 | > 0 |
| `acceptance_criteria.min_trades` | int | ✅ ERROR | SVR-E050/E051 | > 0 |
| `acceptance_criteria.min_cagr` | float | ⚠️ WARN | SVR-W040 | |
| `assumptions.fees` | float | ⚠️ WARN† | SVR-W010 | †Warned if missing for `day_trade` |
| `assumptions.slippage` | float | ⚠️ WARN† | SVR-W011 | †Warned if 0 for `day_trade` |
| `assumptions` | mapping | ⚠️ WARN | SVR-W051 | Entire section |
| `notes` | string | optional | — | Free text, not validated |

†  day_trade-only conditional requirement.  
\* At least one of the listed alternatives is required.

---

## Banned Vague Terms (SVR-E034)

Any of the following words/phrases in `signals.entry` or `signals.exit` conditions will trigger an ERROR:

| Banned Term | Why it's banned |
|-------------|-----------------|
| `momentum` | Ambiguous direction; use RSI/MACD thresholds instead |
| `trending` | Non-quantifiable; use SMA slope or ADX > N |
| `oversold` | Subjective; use RSI < 30 or similar |
| `overbought` | Subjective; use RSI > 70 or similar |
| `volatile` | Non-specific; use ATR > N or VIX > N |
| `reasonable` | Subjective |
| `appropriate` | Subjective |
| `approximately` | Non-exact; use specific numeric threshold |
| `as needed` | Discretionary — not deterministic |

---

## Signal Pattern Errors (SVR-E066, E067, E068)

| Pattern | Code | Why banned |
|---------|------|-----------|
| `next bar`, `look-ahead`, `lookahead` | SVR-E066 | Lookahead bias — uses future data |
| `random`, `coin flip`, `roll die` | SVR-E067 | Non-deterministic — cannot be backtested |
| `level 2`, `dark pool`, `insider`, `news before release` | SVR-E068 | Unavailable data — cannot be replicated |

---

## Validator Exit Codes

| Code | Meaning |
|------|---------|
| `0` | No errors (warnings may be present) |
| `1` | One or more ERROR-level findings |
| `2` | File not found or YAML parse failure |

## CLI Usage

```bash
# Validate a spec file (prints JSON to stdout):
python scripts/spec_validator.py specs/my_strategy.yaml

# Named flag syntax + write output to file:
python scripts/spec_validator.py --spec specs/my_strategy.yaml --output reports/validation.json
```
