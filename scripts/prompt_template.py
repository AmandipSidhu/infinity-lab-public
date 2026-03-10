#!/usr/bin/env python3
"""Prompt template builder for Gemini-based QC LEAN strategy generation.

Extracts spec fields (instruments, signals, resolution, capital, dates,
risk rules) and assembles a structured prompt for the Gemini API.

No specific indicator names are hard-coded here — all signal details
come directly from the spec.
"""

from typing import Any


# ---------------------------------------------------------------------------
# Spec field extractors (handle both flat and strategy-nested formats)
# ---------------------------------------------------------------------------


def _get_nested(spec: dict[str, Any], *keys: str) -> Any:
    """Walk a nested dict by a sequence of keys; return None if any key is missing."""
    node: Any = spec
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _extract_instruments(spec: dict[str, Any]) -> list[str]:
    """Return the list of trading instruments from the spec."""
    # Flat format: data.instruments
    instruments = _get_nested(spec, "data", "instruments")
    if isinstance(instruments, list) and instruments:
        return [str(s) for s in instruments]
    # Nested format: strategy.universe.symbols
    symbols = _get_nested(spec, "strategy", "universe", "symbols")
    if isinstance(symbols, list) and symbols:
        return [str(s) for s in symbols]
    return ["SPY"]


def _extract_resolution(spec: dict[str, Any]) -> str:
    """Return the data resolution string (e.g. 'minute', 'hour', 'daily')."""
    res = _get_nested(spec, "data", "resolution")
    if res:
        return str(res)
    res = _get_nested(spec, "strategy", "universe", "resolution")
    if res:
        return str(res)
    return "daily"


def _extract_dates(spec: dict[str, Any]) -> tuple[str, str]:
    """Return (start_date, end_date) as ISO-8601 strings."""
    start = _get_nested(spec, "data", "start_date") or _get_nested(
        spec, "strategy", "backtesting", "start_date"
    )
    end = _get_nested(spec, "data", "end_date") or _get_nested(
        spec, "strategy", "backtesting", "end_date"
    )
    return str(start or "2020-01-01"), str(end or "2024-12-31")


def _extract_capital(spec: dict[str, Any]) -> float:
    """Return the initial capital in USD."""
    cap = _get_nested(spec, "capital", "allocation_usd")
    if cap is not None:
        return float(cap)
    cap = _get_nested(spec, "strategy", "backtesting", "initial_capital")
    if cap is not None:
        return float(cap)
    return 100_000.0


def _extract_signals(spec: dict[str, Any]) -> dict[str, list[str]]:
    """Return {'entry': [...], 'exit': [...]} signal condition strings."""
    # Flat format: signals.entry (list of strings), signals.exit (list of strings)
    flat_signals = spec.get("signals") or {}
    if isinstance(flat_signals, dict):
        entry_raw = flat_signals.get("entry") or []
        exit_raw = flat_signals.get("exit") or []
        entry: list[str] = [str(c) for c in entry_raw] if isinstance(entry_raw, list) else []
        exit_: list[str] = [str(c) for c in exit_raw] if isinstance(exit_raw, list) else []
        if entry or exit_:
            return {"entry": entry, "exit": exit_}

    # Nested format: strategy.signals.entry.conditions, strategy.signals.exit.conditions
    nested_signals = _get_nested(spec, "strategy", "signals") or {}
    if isinstance(nested_signals, dict):
        entry_raw = _get_nested(nested_signals, "entry", "conditions") or []
        exit_raw = _get_nested(nested_signals, "exit", "conditions") or []
        entry = [str(c) for c in entry_raw] if isinstance(entry_raw, list) else []
        exit_ = [str(c) for c in exit_raw] if isinstance(exit_raw, list) else []
        return {"entry": entry, "exit": exit_}

    return {"entry": [], "exit": []}


def _extract_risk_management(spec: dict[str, Any]) -> dict[str, Any]:
    """Return the risk management parameters dict."""
    risk = spec.get("risk_management") or {}
    if isinstance(risk, dict) and risk:
        return risk
    risk = _get_nested(spec, "strategy", "risk_management") or {}
    return risk if isinstance(risk, dict) else {}


def _extract_constraints(spec: dict[str, Any]) -> dict[str, Any]:
    """Return position/holding constraints dict."""
    constraints = spec.get("constraints") or {}
    return constraints if isinstance(constraints, dict) else {}


def _format_signal_list(signals: list[str]) -> str:
    """Format a list of signal conditions as a bulleted string."""
    if not signals:
        return "  (none specified)"
    return "\n".join(f"  - {s}" for s in signals)


def _format_risk_rules(risk: dict[str, Any]) -> str:
    """Format risk management rules as a readable string."""
    if not risk:
        return "  (none specified)"
    lines = []
    for key, value in risk.items():
        if isinstance(value, dict):
            lines.append(f"  {key}:")
            for k2, v2 in value.items():
                lines.append(f"    {k2}: {v2}")
        else:
            lines.append(f"  {key}: {value}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_strategy_prompt(spec: dict[str, Any], feedback: str | None = None) -> str:
    """Build a complete Gemini prompt for generating a QC LEAN strategy.

    Args:
        spec:     The parsed YAML spec dict.
        feedback: Error message from a previous iteration to include in the prompt.

    Returns:
        A complete prompt string ready to send to the Gemini API.
    """
    instruments = _extract_instruments(spec)
    resolution = _extract_resolution(spec)
    start_date, end_date = _extract_dates(spec)
    capital = _extract_capital(spec)
    signals = _extract_signals(spec)
    risk = _extract_risk_management(spec)
    constraints = _extract_constraints(spec)

    spec_name = (spec.get("metadata") or {}).get("name", "unnamed_strategy")
    description = (spec.get("metadata") or {}).get("description", "").strip()

    # Format dates for SetStartDate / SetEndDate calls
    try:
        _s = str(start_date).split("-")
        _e = str(end_date).split("-")
        start_call = f"self.SetStartDate({int(_s[0])}, {int(_s[1])}, {int(_s[2])})"
        end_call = f"self.SetEndDate({int(_e[0])}, {int(_e[1])}, {int(_e[2])})"
    except (IndexError, ValueError):
        start_call = f'self.SetStartDate(*"{start_date}".split("-"))'
        end_call = f'self.SetEndDate(*"{end_date}".split("-"))'

    resolution_map = {
        "tick": "Resolution.Tick",
        "second": "Resolution.Second",
        "minute": "Resolution.Minute",
        "hour": "Resolution.Hour",
        "daily": "Resolution.Daily",
    }
    qc_resolution = resolution_map.get(str(resolution).lower(), "Resolution.Daily")

    constraint_lines = ""
    if constraints:
        constraint_lines = "\nPosition constraints:\n" + "\n".join(
            f"  {k}: {v}" for k, v in constraints.items()
        )

    feedback_section = ""
    if feedback:
        feedback_section = (
            "\n\n---\n"
            "PREVIOUS ATTEMPT FAILED. Fix EXACTLY this error before regenerating:\n\n"
            f"{feedback}\n"
            "---"
        )

    prompt = f"""You are an expert QuantConnect LEAN Python strategy developer.

Write a complete, working QuantConnect LEAN algorithm in Python for the following strategy spec.

Strategy name: {spec_name}
{f"Description: {description}" if description else ""}

=== INSTRUMENTS ===
{", ".join(instruments)}

=== DATA RESOLUTION ===
{resolution} (use {qc_resolution})

=== BACKTEST PERIOD ===
Start: {start_date}
End:   {end_date}
Initial capital: ${capital:,.0f}

=== ENTRY SIGNALS ===
{_format_signal_list(signals["entry"])}

=== EXIT SIGNALS ===
{_format_signal_list(signals["exit"])}

=== RISK MANAGEMENT ===
{_format_risk_rules(risk)}{constraint_lines}

=== REQUIREMENTS ===
- Inherit from QCAlgorithm
- Implement Initialize(self) and OnData(self, data)
- Use {start_call} and {end_call}
- Use self.SetCash({int(capital)})
- Add symbols using self.AddEquity / self.AddFuture with {qc_resolution}
- Implement ALL entry and exit signals listed above exactly as specified
- Apply ALL risk management rules listed above
- Use self.SetHoldings, self.MarketOrder, or self.LimitOrder for order placement
- Use self.Liquidate for exits
- Handle the IsWarmingUp guard: return early if self.IsWarmingUp
- Add self.SetWarmUp(period) appropriate for the indicators used
- Do NOT use any indicator, instrument, or date that is not listed above

{feedback_section}

Return ONLY a Python code block. No explanation. No markdown except the code fence.

```python
from AlgorithmImports import *

class {_class_name(spec_name)}(QCAlgorithm):
    # Your complete implementation here
```"""

    return prompt


def _class_name(spec_name: str) -> str:
    """Convert a spec name to a valid Python class name."""
    parts = str(spec_name).replace("-", "_").replace(" ", "_").split("_")
    return "".join(p.capitalize() for p in parts if p) or "GeneratedStrategy"
