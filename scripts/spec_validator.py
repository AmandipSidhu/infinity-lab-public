#!/usr/bin/env python3
"""Spec Validator — Validates trading strategy YAML specs against SVR rules.

Validation rules are defined in docs/SPEC_VALIDATION_RULES.md.
Schema reference is in docs/SPEC_TEMPLATE.md.

New top-level schema sections (NOT nested under ``strategy``):
  metadata, capital, constraints, data, signals, risk_management,
  acceptance_criteria, assumptions, notes

Exit codes:
  0 — No errors (warnings may be present)
  1 — One or more ERROR-level findings
  2 — Unrecoverable file or YAML parse failure
"""

import argparse
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

ALLOWED_TRADING_STYLES: set[str] = {"day_trade", "swing", "position"}

ALLOWED_RESOLUTIONS: set[str] = {"tick", "second", "minute", "hour", "daily"}

ALLOWED_SCREENER_CRITERIA: set[str] = {
    "top_volume",
    "gap_up_pct",
    "relative_volume",
    "float_under",
    "custom",
}

# Banned vague terms in signal conditions — SVR-E034 (word-boundary, case-insensitive).
_BANNED_TERM_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bmomentum\b",
        r"\btrending\b",
        r"\boversold\b",
        r"\boverbought\b",
        r"\bvolatile\b",
        r"\breasonable\b",
        r"\bappropriate\b",
        r"\bapproximately\b",
        r"\bas needed\b",
    ]
]

# Lookahead bias patterns — SVR-E066.
_LOOKAHEAD_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"next[ _]bar",
        r"look.?ahead",
    ]
]

# Non-deterministic entry patterns — SVR-E067.
_NONDETERMINISTIC_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\brandom\b",
        r"coin.?flip",
        r"roll.+die",
    ]
]

# Unavailable / non-public data source patterns — SVR-E068.
_UNAVAILABLE_DATA_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"level.?2\b",
        r"dark.?pool",
        r"\binsider\b",
        r"news.+before.+release",
    ]
]

# Time-based exit condition patterns — SVR-W030.
_TIME_EXIT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\btime\b",
        r"\bminutes?\b",
        r"\bhours?\b",
        r"\bbars?\b",
        r"\bduration\b",
        r"\bhold(?:ing)?\b",
        r"\bsession\b",
    ]
]

# A condition is considered to have a numeric threshold if it contains at least one digit.
_NUMERIC_RE: re.Pattern[str] = re.compile(r"\d")

DATE_FMT = "%Y-%m-%d"
_MIN_RANGE_ERROR_YEARS = 2.0   # SVR-E059 threshold
_MIN_RANGE_WARNING_YEARS = 5.0  # SVR-W050 threshold


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _finding(code: str, severity: str, message: str, field: str = "") -> dict[str, str]:
    """Return a finding dict with the standard four keys."""
    return {"code": code, "severity": severity, "message": message, "field": field}


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
    """Parse a YYYY-MM-DD string (or date/datetime object) into a date; None on failure."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, DATE_FMT).date()
    except ValueError:
        return None


def _years_between(d1: date, d2: date) -> float:
    return (d2 - d1).days / 365.25


def _is_positive_number(value: Any) -> bool:
    """Return True if value can be cast to a float that is strictly > 0."""
    try:
        return value is not None and float(value) > 0
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Section-level check functions
# ---------------------------------------------------------------------------


def _check_metadata(spec: dict[str, Any]) -> list[dict[str, str]]:
    """Metadata section: SVR-E001, E002, E005, W001, W002, W062."""
    findings: list[dict[str, str]] = []
    meta = spec.get("metadata")

    # SVR-E001 / SVR-E002: trading_style
    trading_style = _get(spec, "metadata", "trading_style")
    if not _is_nonempty_str(trading_style):
        findings.append(_finding(
            "SVR-E001", "ERROR",
            "metadata.trading_style is missing or empty",
            "metadata.trading_style",
        ))
    elif trading_style not in ALLOWED_TRADING_STYLES:
        allowed = ", ".join(sorted(ALLOWED_TRADING_STYLES))
        findings.append(_finding(
            "SVR-E002", "ERROR",
            f"metadata.trading_style is invalid (got {trading_style!r}). Allowed: {allowed}",
            "metadata.trading_style",
        ))

    # SVR-E005: name
    name = _get(spec, "metadata", "name")
    if not _is_nonempty_str(name):
        findings.append(_finding(
            "SVR-E005", "ERROR",
            "metadata.name is missing or empty",
            "metadata.name",
        ))

    # SVR-W001: description (missing or < 20 chars)
    description = _get(spec, "metadata", "description")
    if not isinstance(meta, dict) or not _is_nonempty_str(description) or len(str(description).strip()) < 20:
        findings.append(_finding(
            "SVR-W001", "WARNING",
            "metadata.description is missing or shorter than 20 characters",
            "metadata.description",
        ))

    # SVR-W002: author
    author = _get(spec, "metadata", "author")
    if not _is_nonempty_str(author):
        findings.append(_finding(
            "SVR-W002", "WARNING",
            "metadata.author is missing or empty",
            "metadata.author",
        ))

    # SVR-W062: version
    version = _get(spec, "metadata", "version")
    if not _is_nonempty_str(version):
        findings.append(_finding(
            "SVR-W062", "WARNING",
            "metadata.version is missing or empty",
            "metadata.version",
        ))

    return findings


def _check_capital(spec: dict[str, Any]) -> list[dict[str, str]]:
    """Capital section: SVR-E003, E004."""
    findings: list[dict[str, str]] = []
    capital = spec.get("capital")

    if not isinstance(capital, dict):
        findings.append(_finding(
            "SVR-E003", "ERROR",
            "capital section is missing: neither capital.allocation_usd nor capital.allocation_pct is present",
            "capital",
        ))
        return findings

    alloc_usd = capital.get("allocation_usd")
    alloc_pct = capital.get("allocation_pct")

    if alloc_usd is None and alloc_pct is None:
        findings.append(_finding(
            "SVR-E003", "ERROR",
            "Neither capital.allocation_usd nor capital.allocation_pct is present",
            "capital",
        ))
        return findings

    # Check whichever field is present has a value > 0.
    value = alloc_usd if alloc_usd is not None else alloc_pct
    field = "capital.allocation_usd" if alloc_usd is not None else "capital.allocation_pct"
    try:
        if float(value) <= 0:
            findings.append(_finding(
                "SVR-E004", "ERROR",
                f"{field} must be > 0 (got {value!r})",
                field,
            ))
    except (TypeError, ValueError):
        findings.append(_finding(
            "SVR-E004", "ERROR",
            f"{field} is not a valid number (got {value!r})",
            field,
        ))

    return findings


def _check_constraints(spec: dict[str, Any], trading_style: str | None) -> list[dict[str, str]]:
    """Constraints section (day_trade only): SVR-E011, E012, E013."""
    findings: list[dict[str, str]] = []

    if trading_style != "day_trade":
        return findings

    # SVR-E011: max_holding_minutes required for day_trade
    max_holding = _get(spec, "constraints", "max_holding_minutes")
    if max_holding is None:
        findings.append(_finding(
            "SVR-E011", "ERROR",
            "constraints.max_holding_minutes is required for day_trade strategies",
            "constraints.max_holding_minutes",
        ))
    else:
        try:
            if float(max_holding) > 390:
                findings.append(_finding(
                    "SVR-E012", "ERROR",
                    f"constraints.max_holding_minutes > 390 (got {max_holding!r}); "
                    "a regular trading session is at most 390 minutes",
                    "constraints.max_holding_minutes",
                ))
        except (TypeError, ValueError):
            findings.append(_finding(
                "SVR-E012", "ERROR",
                f"constraints.max_holding_minutes is not a valid number (got {max_holding!r})",
                "constraints.max_holding_minutes",
            ))

    # SVR-E013: close_eod must be true for day_trade
    close_eod = _get(spec, "constraints", "close_eod")
    if close_eod is not True:
        findings.append(_finding(
            "SVR-E013", "ERROR",
            "constraints.close_eod must be true for day_trade strategies "
            f"(got {close_eod!r})",
            "constraints.close_eod",
        ))

    return findings


def _check_data(spec: dict[str, Any]) -> list[dict[str, str]]:
    """Data section: SVR-E056/a/b, E057, E058, E059, E060, W050."""
    findings: list[dict[str, str]] = []
    data = spec.get("data")

    if not isinstance(data, dict):
        findings.append(_finding("SVR-E056", "ERROR", "data section is missing: no instruments or universe defined", "data"))
        findings.append(_finding("SVR-E057", "ERROR", "data.resolution is missing", "data.resolution"))
        findings.append(_finding(
            "SVR-E058", "ERROR",
            "No date range defined: provide data.start_date + data.end_date or data.lookback_years",
            "data",
        ))
        return findings

    # --- SVR-E056: instruments / universe ---
    instruments = data.get("instruments")
    universe = data.get("universe")
    has_valid_instruments = isinstance(instruments, list) and len(instruments) > 0

    if not has_valid_instruments:
        if isinstance(universe, dict) and universe.get("mode") == "dynamic":
            # Dynamic universe — validate screener sub-rules.
            screener_valid = True

            criteria = _get(universe, "screener", "criteria")
            if criteria not in ALLOWED_SCREENER_CRITERIA:
                allowed = ", ".join(sorted(ALLOWED_SCREENER_CRITERIA))
                findings.append(_finding(
                    "SVR-E056a", "ERROR",
                    f"data.universe.screener.criteria is missing or invalid "
                    f"(got {criteria!r}). Allowed: {allowed}",
                    "data.universe.screener.criteria",
                ))
                screener_valid = False

            max_symbols = _get(universe, "screener", "max_symbols")
            try:
                if max_symbols is None or float(max_symbols) <= 0:
                    findings.append(_finding(
                        "SVR-E056b", "ERROR",
                        f"data.universe.screener.max_symbols is missing or <= 0 (got {max_symbols!r})",
                        "data.universe.screener.max_symbols",
                    ))
                    screener_valid = False
            except (TypeError, ValueError):
                findings.append(_finding(
                    "SVR-E056b", "ERROR",
                    f"data.universe.screener.max_symbols is not a valid number (got {max_symbols!r})",
                    "data.universe.screener.max_symbols",
                ))
                screener_valid = False

            if not screener_valid:
                # Dynamic universe screener is invalid → instruments source is also unsatisfied.
                findings.append(_finding(
                    "SVR-E056", "ERROR",
                    "data.universe dynamic screener is invalid; no valid instruments source defined",
                    "data",
                ))
            # else: screener is valid — SVR-E056 is satisfied by dynamic universe.
        else:
            # No valid instruments and no valid dynamic universe.
            findings.append(_finding(
                "SVR-E056", "ERROR",
                "Neither data.instruments (non-empty list) nor a valid data.universe is present",
                "data",
            ))

    # --- SVR-E057: resolution ---
    resolution = data.get("resolution")
    if not _is_nonempty_str(resolution) or resolution not in ALLOWED_RESOLUTIONS:
        allowed = ", ".join(sorted(ALLOWED_RESOLUTIONS))
        findings.append(_finding(
            "SVR-E057", "ERROR",
            f"data.resolution is missing or invalid (got {resolution!r}). Allowed: {allowed}",
            "data.resolution",
        ))

    # --- SVR-E058 / E059 / E060 / W050: date range ---
    start_date = _parse_date(data.get("start_date"))
    end_date = _parse_date(data.get("end_date"))
    lookback_years = data.get("lookback_years")

    has_dates = start_date is not None and end_date is not None
    has_lookback = lookback_years is not None

    if not has_dates and not has_lookback:
        findings.append(_finding(
            "SVR-E058", "ERROR",
            "No date range defined: provide data.start_date + data.end_date or data.lookback_years",
            "data",
        ))
    elif has_dates:
        if start_date >= end_date:
            findings.append(_finding(
                "SVR-E060", "ERROR",
                f"data.start_date ({start_date}) must be strictly before data.end_date ({end_date})",
                "data.start_date",
            ))
        else:
            years = _years_between(start_date, end_date)
            if years < _MIN_RANGE_ERROR_YEARS:
                findings.append(_finding(
                    "SVR-E059", "ERROR",
                    f"Date range is only {years:.1f} years; minimum required is {_MIN_RANGE_ERROR_YEARS:.0f} years",
                    "data",
                ))
            elif years < _MIN_RANGE_WARNING_YEARS:
                findings.append(_finding(
                    "SVR-W050", "WARNING",
                    f"Date range is {years:.1f} years; recommend at least "
                    f"{_MIN_RANGE_WARNING_YEARS:.0f} years for robust backtesting",
                    "data",
                ))

    return findings


def _check_signals(spec: dict[str, Any]) -> list[dict[str, str]]:
    """Signals section: SVR-E031, E032, E033, E034, E066, E067, E068, W030."""
    findings: list[dict[str, str]] = []
    signals = spec.get("signals")

    if not isinstance(signals, dict):
        findings.append(_finding("SVR-E031", "ERROR", "signals.entry is missing or not a non-empty list", "signals.entry"))
        findings.append(_finding("SVR-E033", "ERROR", "signals.exit is missing or not a non-empty list", "signals.exit"))
        return findings

    # SVR-E031: entry must be a non-empty list
    entry = signals.get("entry")
    if not isinstance(entry, list) or len(entry) == 0:
        findings.append(_finding("SVR-E031", "ERROR", "signals.entry is missing or not a non-empty list", "signals.entry"))
        entry = []

    # SVR-E032: at least one entry condition must contain a numeric threshold
    if entry and not any(_NUMERIC_RE.search(str(c)) for c in entry):
        findings.append(_finding(
            "SVR-E032", "ERROR",
            "signals.entry has no condition containing a numeric threshold (at least one digit required)",
            "signals.entry",
        ))

    # SVR-E033: exit must be a non-empty list
    exit_ = signals.get("exit")
    if not isinstance(exit_, list) or len(exit_) == 0:
        findings.append(_finding("SVR-E033", "ERROR", "signals.exit is missing or not a non-empty list", "signals.exit"))
        exit_ = []

    # SVR-W030: at least one exit condition should be time-based
    if exit_ and not any(
        any(p.search(str(c)) for p in _TIME_EXIT_PATTERNS) for c in exit_
    ):
        findings.append(_finding(
            "SVR-W030", "WARNING",
            "signals.exit has no time-based exit condition; consider adding a time, "
            "minute, hour, bar, duration, hold, or session-based exit",
            "signals.exit",
        ))

    # Combine all conditions into one string for pattern checks below.
    all_conditions = " ".join(str(c) for c in (entry + exit_))

    # SVR-E034: banned vague terms (report first match only)
    for pattern in _BANNED_TERM_PATTERNS:
        match = pattern.search(all_conditions)
        if match:
            findings.append(_finding(
                "SVR-E034", "ERROR",
                f"Signal condition contains banned vague term: {match.group()!r}",
                "signals",
            ))
            break

    # SVR-E066: lookahead bias patterns (report first match only)
    for pattern in _LOOKAHEAD_PATTERNS:
        if pattern.search(all_conditions):
            findings.append(_finding(
                "SVR-E066", "ERROR",
                "Signal condition contains a lookahead bias pattern",
                "signals",
            ))
            break

    # SVR-E067: non-deterministic entry patterns (report first match only)
    for pattern in _NONDETERMINISTIC_PATTERNS:
        if pattern.search(all_conditions):
            findings.append(_finding(
                "SVR-E067", "ERROR",
                "Signal condition contains a non-deterministic entry pattern",
                "signals",
            ))
            break

    # SVR-E068: unavailable / non-public data source patterns (report first match only)
    for pattern in _UNAVAILABLE_DATA_PATTERNS:
        if pattern.search(all_conditions):
            findings.append(_finding(
                "SVR-E068", "ERROR",
                "Signal condition references an unavailable data source",
                "signals",
            ))
            break

    return findings


def _check_risk_management(spec: dict[str, Any]) -> list[dict[str, str]]:
    """Risk management section: SVR-E023, E024, E025, E026, W020, W021."""
    findings: list[dict[str, str]] = []
    rm = spec.get("risk_management")

    if not isinstance(rm, dict):
        findings.append(_finding(
            "SVR-E023", "ERROR",
            "risk_management.stop_loss is missing",
            "risk_management.stop_loss",
        ))
        findings.append(_finding(
            "SVR-E024", "ERROR",
            "risk_management.position_sizing is missing or empty",
            "risk_management.position_sizing",
        ))
        return findings

    # SVR-E023: stop_loss must be a simple positive number or a dict with
    #   at least one positive sub-field: pct, atr_multiplier, absolute_usd.
    stop_loss = rm.get("stop_loss")
    if stop_loss is None:
        findings.append(_finding(
            "SVR-E023", "ERROR",
            "risk_management.stop_loss is missing",
            "risk_management.stop_loss",
        ))
    elif isinstance(stop_loss, dict):
        pct = stop_loss.get("pct")
        atr = stop_loss.get("atr_multiplier")
        abs_usd = stop_loss.get("absolute_usd")
        if not (_is_positive_number(pct) or _is_positive_number(atr) or _is_positive_number(abs_usd)):
            findings.append(_finding(
                "SVR-E023", "ERROR",
                "risk_management.stop_loss dict has none of pct, atr_multiplier, "
                "absolute_usd with a positive value",
                "risk_management.stop_loss",
            ))
    else:
        try:
            if float(stop_loss) <= 0:
                findings.append(_finding(
                    "SVR-E023", "ERROR",
                    f"risk_management.stop_loss must be > 0 (got {stop_loss!r})",
                    "risk_management.stop_loss",
                ))
        except (TypeError, ValueError):
            findings.append(_finding(
                "SVR-E023", "ERROR",
                f"risk_management.stop_loss is not a valid number or dict (got {stop_loss!r})",
                "risk_management.stop_loss",
            ))

    # SVR-E024: position_sizing must be a non-empty string
    position_sizing = rm.get("position_sizing")
    if not _is_nonempty_str(position_sizing):
        findings.append(_finding(
            "SVR-E024", "ERROR",
            "risk_management.position_sizing is missing or empty",
            "risk_management.position_sizing",
        ))

    # SVR-E025: leverage > 4
    leverage = rm.get("leverage")
    if leverage is not None:
        try:
            if float(leverage) > 4:
                findings.append(_finding(
                    "SVR-E025", "ERROR",
                    f"risk_management.leverage > 4 (got {leverage!r}); maximum allowed is 4×",
                    "risk_management.leverage",
                ))
        except (TypeError, ValueError):
            pass  # Non-numeric leverage is not a leverage-range error; leave for schema linters.

    # SVR-E026: if notes or metadata.description mention "margin" or "futures"
    #   but leverage is absent, emit an error.
    notes = spec.get("notes")
    description = _get(spec, "metadata", "description")
    search_text = " ".join(filter(None, [
        str(notes) if isinstance(notes, str) else "",
        str(description) if isinstance(description, str) else "",
    ]))
    if re.search(r"\bmargin\b|\bfutures\b", search_text, re.IGNORECASE) and leverage is None:
        findings.append(_finding(
            "SVR-E026", "ERROR",
            "Spec mentions 'margin' or 'futures' but risk_management.leverage is absent",
            "risk_management.leverage",
        ))

    # SVR-W020: max_positions recommended
    if rm.get("max_positions") is None:
        findings.append(_finding(
            "SVR-W020", "WARNING",
            "risk_management.max_positions is missing; recommend specifying a "
            "maximum concurrent position count",
            "risk_management.max_positions",
        ))

    # SVR-W021: risk_per_trade_pct recommended
    if rm.get("risk_per_trade_pct") is None:
        findings.append(_finding(
            "SVR-W021", "WARNING",
            "risk_management.risk_per_trade_pct is missing; recommend defining "
            "risk per trade as a percentage of account equity",
            "risk_management.risk_per_trade_pct",
        ))

    return findings


def _check_acceptance_criteria(spec: dict[str, Any]) -> list[dict[str, str]]:
    """Acceptance criteria section: SVR-E021/22, E046/47, E048/49, E050/51, W040."""
    findings: list[dict[str, str]] = []
    ac = spec.get("acceptance_criteria")

    if not isinstance(ac, dict):
        ac = {}  # Missing entirely — all required fields will trigger below.

    def _check_required_positive(
        key: str,
        err_missing: str,
        err_nonpositive: str,
        field_path: str,
    ) -> None:
        value = ac.get(key)
        if value is None:
            findings.append(_finding(
                err_missing, "ERROR",
                f"acceptance_criteria.{key} is missing",
                field_path,
            ))
        else:
            try:
                if float(value) <= 0:
                    findings.append(_finding(
                        err_nonpositive, "ERROR",
                        f"acceptance_criteria.{key} must be > 0 (got {value!r})",
                        field_path,
                    ))
            except (TypeError, ValueError):
                findings.append(_finding(
                    err_nonpositive, "ERROR",
                    f"acceptance_criteria.{key} is not a valid number (got {value!r})",
                    field_path,
                ))

    _check_required_positive(
        "max_drawdown_pct", "SVR-E021", "SVR-E022",
        "acceptance_criteria.max_drawdown_pct",
    )
    _check_required_positive(
        "min_sharpe_ratio", "SVR-E046", "SVR-E047",
        "acceptance_criteria.min_sharpe_ratio",
    )
    _check_required_positive(
        "min_profit_factor", "SVR-E048", "SVR-E049",
        "acceptance_criteria.min_profit_factor",
    )
    _check_required_positive(
        "min_trades", "SVR-E050", "SVR-E051",
        "acceptance_criteria.min_trades",
    )

    # SVR-W040: min_cagr is recommended
    if ac.get("min_cagr") is None:
        findings.append(_finding(
            "SVR-W040", "WARNING",
            "acceptance_criteria.min_cagr is missing; recommend defining a "
            "minimum annual return target",
            "acceptance_criteria.min_cagr",
        ))

    return findings


def _check_assumptions(spec: dict[str, Any], trading_style: str | None) -> list[dict[str, str]]:
    """Assumptions section: SVR-W010, W011, W051."""
    findings: list[dict[str, str]] = []
    assumptions = spec.get("assumptions")

    # SVR-W051: assumptions section entirely absent
    if assumptions is None:
        findings.append(_finding(
            "SVR-W051", "WARNING",
            "assumptions section is entirely missing; recommend documenting fee "
            "and slippage assumptions",
            "assumptions",
        ))
        return findings

    if trading_style == "day_trade":
        # SVR-W010: fees required for day_trade
        fees = _get(spec, "assumptions", "fees")
        if fees is None:
            findings.append(_finding(
                "SVR-W010", "WARNING",
                "assumptions.fees is missing for a day_trade strategy; "
                "transaction fees can significantly affect intraday P&L",
                "assumptions.fees",
            ))

        # SVR-W011: slippage == 0 is unrealistic for day_trade
        slippage = _get(spec, "assumptions", "slippage")
        if slippage is not None:
            try:
                if float(slippage) == 0:
                    findings.append(_finding(
                        "SVR-W011", "WARNING",
                        "assumptions.slippage is 0 for a day_trade strategy; "
                        "zero slippage is unrealistic for intraday trading",
                        "assumptions.slippage",
                    ))
            except (TypeError, ValueError):
                pass

    return findings


def _check_general(spec: dict[str, Any]) -> list[dict[str, str]]:
    """General spec quality checks: SVR-W061."""
    findings: list[dict[str, str]] = []

    try:
        body = yaml.dump(spec, default_flow_style=False)
    except Exception:
        body = str(spec)

    if len(body) < 200:
        findings.append(_finding(
            "SVR-W061", "WARNING",
            f"Spec body is only {len(body)} characters; a thorough spec should be "
            "at least 200 characters",
            "",
        ))

    return findings


# ---------------------------------------------------------------------------
# Sort findings
# ---------------------------------------------------------------------------


def _sort_findings(findings: list[dict[str, str]]) -> list[dict[str, str]]:
    """Sort findings: ERRORs before WARNINGs, then alphabetically by code within each group."""
    severity_order = {"ERROR": 0, "WARNING": 1}
    return sorted(findings, key=lambda f: (severity_order.get(f["severity"], 2), f["code"]))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_spec(spec: dict[str, Any]) -> list[dict[str, str]]:
    """Validate a spec dict against all SVR rules.

    Args:
        spec: The parsed YAML spec as a Python dict.

    Returns:
        A sorted list of finding dicts.  An empty list means fully valid.
        Each finding has keys: ``code``, ``severity``, ``message``, ``field``.
    """
    if not isinstance(spec, dict):
        return [_finding("SVR-E000", "ERROR", "Spec root is not a YAML mapping (dict)", "")]

    trading_style: str | None = _get(spec, "metadata", "trading_style")

    findings: list[dict[str, str]] = []
    findings.extend(_check_metadata(spec))
    findings.extend(_check_capital(spec))
    findings.extend(_check_constraints(spec, trading_style))
    findings.extend(_check_data(spec))
    findings.extend(_check_signals(spec))
    findings.extend(_check_risk_management(spec))
    findings.extend(_check_acceptance_criteria(spec))
    findings.extend(_check_assumptions(spec, trading_style))
    findings.extend(_check_general(spec))
    return _sort_findings(findings)


def build_summary(spec_path: str, findings: list[dict[str, str]]) -> dict[str, Any]:
    """Build the JSON-serialisable output summary.

    Args:
        spec_path: Path to the validated spec file (used as a label only).
        findings:  List of finding dicts returned by :func:`validate_spec`.

    Returns:
        Summary dict with keys: spec_file, valid, result, error_count,
        warning_count, errors, warnings, findings.
    """
    errors = [f for f in findings if f["severity"] == "ERROR"]
    warnings = [f for f in findings if f["severity"] == "WARNING"]
    return {
        "spec_file": spec_path,
        "valid": len(errors) == 0,
        "result": "PASS" if len(errors) == 0 else "FAIL",
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": [f["message"] for f in errors],
        "warnings": [f["message"] for f in warnings],
        "findings": findings,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Usage:
        spec_validator.py --spec <path> [--output <path>]
        spec_validator.py <path>

    Returns:
        0 on no errors, 1 on one or more ERROR findings, 2 on parse/file failure.
    """
    parser = argparse.ArgumentParser(
        description="Validate a trading strategy YAML spec against SVR rules.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--spec",
        metavar="PATH",
        help="Path to the YAML spec file to validate.",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        help="Optional path to write the JSON summary output.",
    )
    parser.add_argument(
        "positional",
        nargs="?",
        metavar="PATH",
        help="Path to the YAML spec file (alternative to --spec).",
    )
    args = parser.parse_args(argv)

    spec_path = args.spec or args.positional
    if not spec_path:
        print(
            json.dumps({"error": "No spec file provided. Use --spec <path> or pass path as positional argument."}),
            file=sys.stderr,
        )
        return 2

    if not os.path.isfile(spec_path):
        print(json.dumps({"error": f"File not found: {spec_path}"}), file=sys.stderr)
        return 2

    try:
        with open(spec_path, "r", encoding="utf-8") as fh:
            spec = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        print(json.dumps({"error": f"YAML parse error: {exc}"}), file=sys.stderr)
        return 2
    except OSError as exc:
        print(json.dumps({"error": f"File read error: {exc}"}), file=sys.stderr)
        return 2

    findings = validate_spec(spec)
    summary = build_summary(spec_path, findings)
    output_json = json.dumps(summary, indent=2)

    print(output_json)

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(output_json)
                fh.write("\n")
        except OSError as exc:
            print(json.dumps({"error": f"Failed to write output: {exc}"}), file=sys.stderr)
            return 2

    return 1 if summary["error_count"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
