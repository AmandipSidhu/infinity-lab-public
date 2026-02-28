#!/usr/bin/env python3
"""Spec Validator — Validates trading strategy YAML specs against SVR rules.

Validation rules are defined in docs/SPEC_VALIDATION_RULES.md.
Schema reference is in docs/SPEC_TEMPLATE.md.

Exit codes:
  0 — No errors (warnings may be present)
  1 — One or more ERROR-level findings
  2 — Unrecoverable file or YAML parse failure
"""

import json
import os
import re
import sys
from datetime import date, datetime
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_STRATEGY_TYPES: set[str] = {
    "momentum",
    "mean_reversion",
    "trend_following",
    "arbitrage",
    "market_making",
    "statistical_arb",
    "pairs_trading",
    "breakout",
    "volatility",
}

ALLOWED_RESOLUTIONS: set[str] = {
    "tick",
    "second",
    "minute",
    "hour",
    "daily",
    "weekly",
}

# Vague / non-quantifiable language patterns — any match triggers SVR-E030.
_VAGUE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bas needed\b",
        r"\bwhen appropriate\b",
        r"\bgood time\b",
        r"\blooks good\b",
        r"\bseems right\b",
        r"\bfeels?\b",
        r"\bintuition\b",
        r"\bsometimes\b",
        r"\buser.friendly\b",
        r"\bmarket conditions? permit\b",
        r"\bdiscretion\b",
        r"\bwhenever possible\b",
        r"\bat some point\b",
    ]
]

# A condition is considered numeric if it contains at least one digit.
_NUMERIC_RE: re.Pattern[str] = re.compile(r"\d")

DATE_FMT = "%Y-%m-%d"


# ---------------------------------------------------------------------------
# Finding dataclass-equivalent
# ---------------------------------------------------------------------------


def _finding(code: str, severity: str, message: str, field: str = "") -> dict[str, str]:
    return {"code": code, "severity": severity, "message": message, "field": field}


# ---------------------------------------------------------------------------
# Rule implementations
# ---------------------------------------------------------------------------


def _get(data: Any, *keys: str) -> Any:
    """Safely traverse nested dicts; returns None if any key is missing."""
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, DATE_FMT).date()
    except ValueError:
        return None


def _check_metadata(spec: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    meta = spec.get("metadata")

    if not isinstance(meta, dict) or not _is_nonempty_str(meta.get("name")):
        findings.append(_finding("SVR-E001", "ERROR", "metadata.name is missing or empty", "metadata.name"))

    if not isinstance(meta, dict) or not _is_nonempty_str(meta.get("version")):
        findings.append(_finding("SVR-E002", "ERROR", "metadata.version is missing or empty", "metadata.version"))

    if not isinstance(meta, dict) or not _is_nonempty_str(meta.get("description")):
        findings.append(_finding("SVR-E003", "ERROR", "metadata.description is missing or empty", "metadata.description"))

    if isinstance(meta, dict):
        if not _is_nonempty_str(meta.get("author")):
            findings.append(_finding("SVR-W001", "WARNING", "metadata.author is missing or empty", "metadata.author"))

        created_at = meta.get("created_at")
        if not created_at or _parse_date(str(created_at)) is None:
            findings.append(_finding("SVR-W002", "WARNING", "metadata.created_at is missing or not in YYYY-MM-DD format", "metadata.created_at"))

    return findings


def _check_strategy_top(spec: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    strategy = spec.get("strategy")

    if not isinstance(strategy, dict):
        findings.append(_finding("SVR-E004", "ERROR", "Top-level 'strategy' block is missing", "strategy"))
        return findings  # Cannot continue without the block

    strategy_type = strategy.get("type")
    if not _is_nonempty_str(strategy_type) or strategy_type not in ALLOWED_STRATEGY_TYPES:
        allowed = ", ".join(sorted(ALLOWED_STRATEGY_TYPES))
        findings.append(_finding(
            "SVR-E005", "ERROR",
            f"strategy.type is missing or invalid (got {strategy_type!r}). Allowed: {allowed}",
            "strategy.type",
        ))

    return findings


def _check_universe(strategy: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    universe = strategy.get("universe")

    if not isinstance(universe, dict):
        findings.append(_finding("SVR-W003", "WARNING", "strategy.universe block is missing", "strategy.universe"))
        return findings

    symbols = universe.get("symbols")
    if not isinstance(symbols, list) or len(symbols) == 0:
        findings.append(_finding("SVR-E006", "ERROR", "strategy.universe.symbols is missing or empty", "strategy.universe.symbols"))
    elif len(symbols) == 1:
        findings.append(_finding("SVR-W020", "WARNING", "strategy.universe.symbols contains only 1 symbol (concentration risk)", "strategy.universe.symbols"))

    resolution = universe.get("resolution")
    if not _is_nonempty_str(resolution) or resolution not in ALLOWED_RESOLUTIONS:
        allowed = ", ".join(sorted(ALLOWED_RESOLUTIONS))
        findings.append(_finding(
            "SVR-E007", "ERROR",
            f"strategy.universe.resolution is missing or invalid (got {resolution!r}). Allowed: {allowed}",
            "strategy.universe.resolution",
        ))

    return findings


def _has_vague_language(text: str) -> bool:
    return any(p.search(text) for p in _VAGUE_PATTERNS)


def _has_numeric_threshold(text: str) -> bool:
    return bool(_NUMERIC_RE.search(text))


def _check_signals(strategy: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    signals = strategy.get("signals")

    if not isinstance(signals, dict):
        findings.append(_finding("SVR-E008", "ERROR", "strategy.signals block is missing", "strategy.signals"))
        return findings

    for direction in ("entry", "exit"):
        block = signals.get(direction)
        e_missing = "SVR-E009" if direction == "entry" else "SVR-E010"
        e_cond = "SVR-E011" if direction == "entry" else "SVR-E012"
        w_numeric = "SVR-W023" if direction == "entry" else "SVR-W024"

        if not isinstance(block, dict):
            findings.append(_finding(e_missing, "ERROR", f"strategy.signals.{direction} section is missing", f"strategy.signals.{direction}"))
            continue

        conditions = block.get("conditions")
        if not isinstance(conditions, list) or len(conditions) == 0:
            findings.append(_finding(e_cond, "ERROR", f"strategy.signals.{direction}.conditions is missing or empty", f"strategy.signals.{direction}.conditions"))
            continue

        vague_found = False
        has_any_numeric = False
        for cond in conditions:
            if not isinstance(cond, str):
                continue
            if _has_vague_language(cond):
                vague_found = True
            if _has_numeric_threshold(cond):
                has_any_numeric = True

        if vague_found:
            findings.append(_finding(
                "SVR-E030", "ERROR",
                f"strategy.signals.{direction}.conditions contains vague, non-quantifiable language. Replace with explicit numeric thresholds.",
                f"strategy.signals.{direction}.conditions",
            ))

        if not has_any_numeric:
            findings.append(_finding(
                w_numeric, "WARNING",
                f"strategy.signals.{direction}.conditions has no numeric threshold. Add explicit values (e.g., RSI > 70).",
                f"strategy.signals.{direction}.conditions",
            ))

    return findings


def _check_risk_management(strategy: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    risk = strategy.get("risk_management")

    if not isinstance(risk, dict):
        findings.append(_finding("SVR-E013", "ERROR", "strategy.risk_management block is missing", "strategy.risk_management"))
        # Sub-field checks impossible — add dependent errors and return
        findings.append(_finding("SVR-E014", "ERROR", "strategy.risk_management.stop_loss is missing (implied by missing block)", "strategy.risk_management.stop_loss"))
        findings.append(_finding("SVR-E015", "ERROR", "strategy.risk_management.max_position_size is missing (implied by missing block)", "strategy.risk_management.max_position_size"))
        return findings

    # stop_loss
    stop_loss = risk.get("stop_loss")
    if stop_loss is None:
        findings.append(_finding("SVR-E014", "ERROR", "strategy.risk_management.stop_loss is missing", "strategy.risk_management.stop_loss"))
    else:
        try:
            sl_val = float(stop_loss)
            if sl_val > 0.20:
                findings.append(_finding("SVR-E027", "ERROR", f"strategy.risk_management.stop_loss={sl_val} exceeds maximum allowed 0.20 (20%)", "strategy.risk_management.stop_loss"))
            elif sl_val <= 0:
                findings.append(_finding("SVR-W021", "WARNING", f"strategy.risk_management.stop_loss={sl_val} must be positive", "strategy.risk_management.stop_loss"))
        except (TypeError, ValueError):
            findings.append(_finding("SVR-E014", "ERROR", f"strategy.risk_management.stop_loss is not a valid number: {stop_loss!r}", "strategy.risk_management.stop_loss"))

    # max_position_size
    mps = risk.get("max_position_size")
    if mps is None:
        findings.append(_finding("SVR-E015", "ERROR", "strategy.risk_management.max_position_size is missing", "strategy.risk_management.max_position_size"))
    else:
        try:
            mps_val = float(mps)
            if mps_val <= 0 or mps_val > 1.0:
                findings.append(_finding("SVR-E028", "ERROR", f"strategy.risk_management.max_position_size={mps_val} must be in (0, 1.0]", "strategy.risk_management.max_position_size"))
        except (TypeError, ValueError):
            findings.append(_finding("SVR-E015", "ERROR", f"strategy.risk_management.max_position_size is not a valid number: {mps!r}", "strategy.risk_management.max_position_size"))

    # max_leverage (optional, but checked if present)
    leverage = risk.get("max_leverage")
    if leverage is not None:
        try:
            lev_val = float(leverage)
            if lev_val > 3.0:
                findings.append(_finding("SVR-E029", "ERROR", f"strategy.risk_management.max_leverage={lev_val} exceeds maximum allowed 3.0", "strategy.risk_management.max_leverage"))
            elif lev_val > 1.0:
                findings.append(_finding("SVR-W026", "WARNING", f"strategy.risk_management.max_leverage={lev_val} > 1.0 amplifies drawdown risk", "strategy.risk_management.max_leverage"))
        except (TypeError, ValueError):
            pass  # Non-numeric leverage is caught by schema, not a separate rule

    # max_drawdown (recommended)
    max_dd = risk.get("max_drawdown")
    if max_dd is None:
        findings.append(_finding("SVR-W006", "WARNING", "strategy.risk_management.max_drawdown is missing", "strategy.risk_management.max_drawdown"))
    else:
        try:
            dd_val = float(max_dd)
            if dd_val > 0.50:
                findings.append(_finding("SVR-W015", "WARNING", f"strategy.risk_management.max_drawdown={dd_val} > 0.50 suggests insufficient risk control", "strategy.risk_management.max_drawdown"))
        except (TypeError, ValueError):
            pass

    # take_profit (recommended)
    if risk.get("take_profit") is None:
        findings.append(_finding("SVR-W005", "WARNING", "strategy.risk_management.take_profit is missing", "strategy.risk_management.take_profit"))

    # position_sizing (recommended)
    if not _is_nonempty_str(risk.get("position_sizing")):
        findings.append(_finding("SVR-W004", "WARNING", "strategy.risk_management.position_sizing is missing", "strategy.risk_management.position_sizing"))

    return findings


def _check_performance_targets(strategy: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    pt = strategy.get("performance_targets")

    if not isinstance(pt, dict):
        findings.append(_finding("SVR-E016", "ERROR", "strategy.performance_targets block is missing", "strategy.performance_targets"))
        findings.append(_finding("SVR-E017", "ERROR", "strategy.performance_targets.sharpe_ratio_min is missing (implied by missing block)", "strategy.performance_targets.sharpe_ratio_min"))
        findings.append(_finding("SVR-E018", "ERROR", "strategy.performance_targets.max_drawdown_threshold is missing (implied by missing block)", "strategy.performance_targets.max_drawdown_threshold"))
        return findings

    # sharpe_ratio_min
    sharpe = pt.get("sharpe_ratio_min")
    if sharpe is None:
        findings.append(_finding("SVR-E017", "ERROR", "strategy.performance_targets.sharpe_ratio_min is missing", "strategy.performance_targets.sharpe_ratio_min"))
    else:
        try:
            sh_val = float(sharpe)
            if sh_val < 1.0:
                findings.append(_finding("SVR-W012", "WARNING", f"strategy.performance_targets.sharpe_ratio_min={sh_val} is below the industry standard of 1.0", "strategy.performance_targets.sharpe_ratio_min"))
            if sh_val > 5.0:
                findings.append(_finding("SVR-W014", "WARNING", f"strategy.performance_targets.sharpe_ratio_min={sh_val} > 5.0 is likely curve-fitted", "strategy.performance_targets.sharpe_ratio_min"))
        except (TypeError, ValueError):
            findings.append(_finding("SVR-E017", "ERROR", f"strategy.performance_targets.sharpe_ratio_min is not a valid number: {sharpe!r}", "strategy.performance_targets.sharpe_ratio_min"))

    # max_drawdown_threshold
    mdt = pt.get("max_drawdown_threshold")
    if mdt is None:
        findings.append(_finding("SVR-E018", "ERROR", "strategy.performance_targets.max_drawdown_threshold is missing", "strategy.performance_targets.max_drawdown_threshold"))
    else:
        try:
            mdt_val = float(mdt)
            if mdt_val > 0.30:
                findings.append(_finding("SVR-W016", "WARNING", f"strategy.performance_targets.max_drawdown_threshold={mdt_val} > 0.30; recommend ≤ 0.20", "strategy.performance_targets.max_drawdown_threshold"))
        except (TypeError, ValueError):
            findings.append(_finding("SVR-E018", "ERROR", f"strategy.performance_targets.max_drawdown_threshold is not a valid number: {mdt!r}", "strategy.performance_targets.max_drawdown_threshold"))

    # win_rate_min (recommended)
    win_rate = pt.get("win_rate_min")
    if win_rate is None:
        findings.append(_finding("SVR-W007", "WARNING", "strategy.performance_targets.win_rate_min is missing", "strategy.performance_targets.win_rate_min"))
    else:
        try:
            wr_val = float(win_rate)
            if wr_val < 0.50:
                findings.append(_finding("SVR-W013", "WARNING", f"strategy.performance_targets.win_rate_min={wr_val} < 0.50; suboptimal without high reward-to-risk", "strategy.performance_targets.win_rate_min"))
        except (TypeError, ValueError):
            pass

    return findings


def _check_backtesting(strategy: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    bt = strategy.get("backtesting")

    if not isinstance(bt, dict):
        findings.append(_finding("SVR-E019", "ERROR", "strategy.backtesting block is missing", "strategy.backtesting"))
        findings.append(_finding("SVR-E020", "ERROR", "strategy.backtesting.start_date is missing (implied by missing block)", "strategy.backtesting.start_date"))
        findings.append(_finding("SVR-E021", "ERROR", "strategy.backtesting.end_date is missing (implied by missing block)", "strategy.backtesting.end_date"))
        findings.append(_finding("SVR-E022", "ERROR", "strategy.backtesting.initial_capital is missing (implied by missing block)", "strategy.backtesting.initial_capital"))
        findings.append(_finding("SVR-E023", "ERROR", "strategy.backtesting.min_trades is missing (implied by missing block)", "strategy.backtesting.min_trades"))
        return findings

    # start_date
    start_raw = bt.get("start_date")
    if start_raw is None:
        findings.append(_finding("SVR-E020", "ERROR", "strategy.backtesting.start_date is missing", "strategy.backtesting.start_date"))
        start_date = None
    else:
        start_date = _parse_date(str(start_raw))
        if start_date is None:
            findings.append(_finding("SVR-E024", "ERROR", f"strategy.backtesting.start_date={start_raw!r} is not in YYYY-MM-DD format", "strategy.backtesting.start_date"))

    # end_date
    end_raw = bt.get("end_date")
    if end_raw is None:
        findings.append(_finding("SVR-E021", "ERROR", "strategy.backtesting.end_date is missing", "strategy.backtesting.end_date"))
        end_date = None
    else:
        end_date = _parse_date(str(end_raw))
        if end_date is None:
            findings.append(_finding("SVR-E025", "ERROR", f"strategy.backtesting.end_date={end_raw!r} is not in YYYY-MM-DD format", "strategy.backtesting.end_date"))

    # Date order
    if start_date is not None and end_date is not None:
        if start_date >= end_date:
            findings.append(_finding("SVR-E026", "ERROR", f"strategy.backtesting.start_date ({start_date}) must be before end_date ({end_date})", "strategy.backtesting"))

        today = date.today()
        if end_date > today:
            findings.append(_finding("SVR-W018", "WARNING", f"strategy.backtesting.end_date ({end_date}) is in the future (look-ahead bias risk)", "strategy.backtesting.end_date"))

        days = (end_date - start_date).days
        if days < 730:
            findings.append(_finding("SVR-W019", "WARNING", f"Backtesting period is {days} days (< 2 years). Use ≥ 2 years to capture multiple market conditions.", "strategy.backtesting"))

    # initial_capital
    capital = bt.get("initial_capital")
    if capital is None:
        findings.append(_finding("SVR-E022", "ERROR", "strategy.backtesting.initial_capital is missing", "strategy.backtesting.initial_capital"))
    else:
        try:
            cap_val = float(capital)
            if cap_val <= 0:
                findings.append(_finding("SVR-E022", "ERROR", f"strategy.backtesting.initial_capital={cap_val} must be > 0", "strategy.backtesting.initial_capital"))
            elif cap_val < 10000:
                findings.append(_finding("SVR-W017", "WARNING", f"strategy.backtesting.initial_capital={cap_val} < $10,000 may produce unrealistic fill assumptions", "strategy.backtesting.initial_capital"))
        except (TypeError, ValueError):
            findings.append(_finding("SVR-E022", "ERROR", f"strategy.backtesting.initial_capital is not a valid number: {capital!r}", "strategy.backtesting.initial_capital"))

    # min_trades
    min_trades = bt.get("min_trades")
    if min_trades is None:
        findings.append(_finding("SVR-E023", "ERROR", "strategy.backtesting.min_trades is missing", "strategy.backtesting.min_trades"))
    else:
        try:
            mt_val = int(min_trades)
            if mt_val < 100:
                findings.append(_finding("SVR-E023", "ERROR", f"strategy.backtesting.min_trades={mt_val} is below minimum of 100", "strategy.backtesting.min_trades"))
            elif mt_val < 1000:
                findings.append(_finding("SVR-W011", "WARNING", f"strategy.backtesting.min_trades={mt_val} < 1000; consider ≥ 1000 for high statistical confidence", "strategy.backtesting.min_trades"))
        except (TypeError, ValueError):
            findings.append(_finding("SVR-E023", "ERROR", f"strategy.backtesting.min_trades is not a valid integer: {min_trades!r}", "strategy.backtesting.min_trades"))

    # benchmark (recommended)
    if not _is_nonempty_str(bt.get("benchmark")):
        findings.append(_finding("SVR-W008", "WARNING", "strategy.backtesting.benchmark is missing; add a benchmark (e.g., SPY) for relative performance comparison", "strategy.backtesting.benchmark"))

    return findings


def _check_data_requirements(strategy: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    dr = strategy.get("data_requirements")

    if not isinstance(dr, dict):
        findings.append(_finding("SVR-W009", "WARNING", "strategy.data_requirements block is missing", "strategy.data_requirements"))
        return findings

    indicators = dr.get("indicators")
    if not isinstance(indicators, list) or len(indicators) == 0:
        findings.append(_finding("SVR-W010", "WARNING", "strategy.data_requirements.indicators is missing or empty; list technical indicators for reproducibility", "strategy.data_requirements.indicators"))

    if dr.get("min_history_days") is None:
        findings.append(_finding("SVR-W022", "WARNING", "strategy.data_requirements.min_history_days is missing; specify minimum data history for indicator warm-up", "strategy.data_requirements.min_history_days"))

    return findings


def _check_market_making_resolution(strategy: dict[str, Any]) -> list[dict[str, str]]:
    """SVR-W025: market_making type requires sub-minute resolution."""
    findings: list[dict[str, str]] = []
    if strategy.get("type") == "market_making":
        resolution = _get(strategy, "universe", "resolution")
        if resolution in ("daily", "weekly", "hour"):
            findings.append(_finding(
                "SVR-W025", "WARNING",
                f"strategy.type=market_making typically requires sub-minute resolution, but universe.resolution={resolution!r}",
                "strategy.universe.resolution",
            ))
    return findings


# ---------------------------------------------------------------------------
# Main validation entry-point
# ---------------------------------------------------------------------------


def validate_spec(spec: dict[str, Any]) -> list[dict[str, str]]:
    """Run all 56 SVR rules against a parsed spec dict.

    Returns a list of finding dicts sorted by severity (ERRORs first) then code.
    """
    findings: list[dict[str, str]] = []

    findings.extend(_check_metadata(spec))

    # Top-level strategy check — if missing, stop immediately
    top_findings = _check_strategy_top(spec)
    findings.extend(top_findings)
    strategy = spec.get("strategy")
    if not isinstance(strategy, dict):
        return _sort_findings(findings)

    findings.extend(_check_universe(strategy))
    findings.extend(_check_signals(strategy))
    findings.extend(_check_risk_management(strategy))
    findings.extend(_check_performance_targets(strategy))
    findings.extend(_check_backtesting(strategy))
    findings.extend(_check_data_requirements(strategy))
    findings.extend(_check_market_making_resolution(strategy))

    return _sort_findings(findings)


def _sort_findings(findings: list[dict[str, str]]) -> list[dict[str, str]]:
    order = {"ERROR": 0, "WARNING": 1}
    return sorted(findings, key=lambda f: (order.get(f["severity"], 2), f["code"]))


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def build_summary(spec_path: str, findings: list[dict[str, str]]) -> dict[str, Any]:
    errors = [f for f in findings if f["severity"] == "ERROR"]
    warnings = [f for f in findings if f["severity"] == "WARNING"]
    return {
        "spec_file": spec_path,
        "result": "FAIL" if errors else "PASS",
        "error_count": len(errors),
        "warning_count": len(warnings),
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]

    if len(args) != 1:
        print(
            json.dumps({"error": "Usage: spec_validator.py <path/to/spec.yaml>"}),
            file=sys.stderr,
        )
        return 2

    spec_path = args[0]

    if not os.path.isfile(spec_path):
        print(
            json.dumps({"error": f"File not found: {spec_path}"}),
            file=sys.stderr,
        )
        return 2

    with open(spec_path, "r", encoding="utf-8") as fh:
        # Let yaml.YAMLError propagate loudly so problems are immediately visible
        spec = yaml.safe_load(fh)

    if not isinstance(spec, dict):
        print(
            json.dumps({"error": f"YAML file did not parse to a mapping: {spec_path}"}),
            file=sys.stderr,
        )
        return 2

    findings = validate_spec(spec)
    summary = build_summary(spec_path, findings)
    print(json.dumps(summary, indent=2))

    return 1 if summary["result"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
