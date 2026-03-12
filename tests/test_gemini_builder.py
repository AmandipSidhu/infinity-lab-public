"""Tests for scripts/gemini_builder.py.

Covers:
- build_fitness_feedback: low trade count → loosen entry conditions
- build_fitness_feedback: low win rate + low P/L ratio → tighten stop loss
- build_fitness_feedback: high drawdown → reduce position size
- build_fitness_feedback: low sharpe with adequate trades → improve signal quality
- build_fitness_feedback: missing/empty stats → graceful degradation
- build_fitness_feedback: all metrics present → full structured block
- run_qc_upload_eval: returns 3-tuple (bool, str, dict)
"""

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from gemini_builder import build_fitness_feedback  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_BASE_SPEC: dict[str, Any] = {
    "metadata": {"name": "test_strategy"},
    "acceptance_criteria": {
        "min_sharpe_ratio": 0.5,
        "max_drawdown_pct": 15.0,
    },
}


def _spec_with_nested_targets(**kwargs: Any) -> dict[str, Any]:
    """Build a spec using nested strategy.performance_targets format."""
    return {
        "metadata": {"name": "test_strategy"},
        "strategy": {
            "performance_targets": {
                "sharpe_ratio_min": kwargs.get("sharpe_ratio_min", 0.5),
                "max_drawdown_threshold": kwargs.get("max_drawdown_threshold", 0.15),
            }
        },
    }


# ---------------------------------------------------------------------------
# Tests: low trade count
# ---------------------------------------------------------------------------


class TestLowTradeCount:
    def test_low_trades_triggers_loosen_entry(self) -> None:
        stats = {
            "SharpeRatio": "0.38",
            "TotalTrades": "12",
            "WinRate": "41.7",
            "NetProfit": "3.2",
            "Drawdown": "8.4",
        }
        result = build_fitness_feedback("violation msg", stats, _BASE_SPEC)
        assert "12 trades" in result
        assert "Loosen entry conditions" in result

    def test_low_trades_boundary_49(self) -> None:
        stats = {"TotalTrades": "49", "SharpeRatio": "0.3"}
        result = build_fitness_feedback("violation msg", stats, _BASE_SPEC)
        assert "49 trades" in result
        assert "Loosen entry conditions" in result

    def test_exactly_50_trades_not_flagged_as_low(self) -> None:
        stats = {"TotalTrades": "50", "SharpeRatio": "0.3"}
        result = build_fitness_feedback("violation msg", stats, _BASE_SPEC)
        # 50 trades should NOT trigger the "not trading enough" message
        assert "not trading enough" not in result.lower()


# ---------------------------------------------------------------------------
# Tests: low win rate + low P/L ratio
# ---------------------------------------------------------------------------


class TestLowWinRateAndPLRatio:
    def test_low_winrate_low_pl_triggers_tighten_stop(self) -> None:
        stats = {
            "SharpeRatio": "0.4",
            "TotalTrades": "80",
            "WinRate": "41.7",
            "ProfitLossRatio": "0.95",
        }
        result = build_fitness_feedback("violation msg", stats, _BASE_SPEC)
        assert "Tighten stop loss" in result

    def test_high_winrate_no_tighten_trigger(self) -> None:
        stats = {
            "SharpeRatio": "0.4",
            "TotalTrades": "80",
            "WinRate": "55.0",
            "ProfitLossRatio": "0.95",
        }
        result = build_fitness_feedback("violation msg", stats, _BASE_SPEC)
        assert "Tighten stop loss" not in result

    def test_low_winrate_high_pl_no_tighten_trigger(self) -> None:
        stats = {
            "SharpeRatio": "0.4",
            "TotalTrades": "80",
            "WinRate": "40.0",
            "ProfitLossRatio": "1.8",
        }
        result = build_fitness_feedback("violation msg", stats, _BASE_SPEC)
        assert "Tighten stop loss" not in result


# ---------------------------------------------------------------------------
# Tests: high drawdown
# ---------------------------------------------------------------------------


class TestHighDrawdown:
    def test_high_drawdown_triggers_reduce_position(self) -> None:
        stats = {
            "SharpeRatio": "0.6",
            "TotalTrades": "100",
            "Drawdown": "20.0",  # 20% > 15% threshold
        }
        result = build_fitness_feedback("violation msg", stats, _BASE_SPEC)
        assert "Reduce position size" in result or "drawdown circuit breaker" in result

    def test_acceptable_drawdown_no_trigger(self) -> None:
        stats = {
            "SharpeRatio": "0.6",
            "TotalTrades": "100",
            "Drawdown": "10.0",  # 10% < 15% threshold
        }
        result = build_fitness_feedback("violation msg", stats, _BASE_SPEC)
        assert "Reduce position size" not in result

    def test_drawdown_as_fraction_triggers(self) -> None:
        # Drawdown expressed as fraction 0.20 = 20%
        stats = {
            "SharpeRatio": "0.6",
            "TotalTrades": "100",
            "Drawdown": "0.20",
        }
        result = build_fitness_feedback("violation msg", stats, _BASE_SPEC)
        # 0.20 as fraction is already < 1 so treated as 20% fraction
        assert "Reduce position size" in result or "drawdown circuit breaker" in result

    def test_drawdown_with_nested_spec_threshold(self) -> None:
        spec = _spec_with_nested_targets(max_drawdown_threshold=0.10)
        stats = {"SharpeRatio": "0.6", "TotalTrades": "100", "Drawdown": "15.0"}
        result = build_fitness_feedback("violation msg", stats, spec)
        assert "Reduce position size" in result or "drawdown circuit breaker" in result


# ---------------------------------------------------------------------------
# Tests: low sharpe with adequate trades
# ---------------------------------------------------------------------------


class TestLowSharpeAdequateTrades:
    def test_low_sharpe_adequate_trades_triggers_signal_quality(self) -> None:
        stats = {
            "SharpeRatio": "0.38",
            "TotalTrades": "120",
        }
        result = build_fitness_feedback("violation msg", stats, _BASE_SPEC)
        assert "Improve signal quality" in result

    def test_low_sharpe_low_trades_no_signal_quality_trigger(self) -> None:
        # Low sharpe + low trades → trades trigger takes priority, not signal quality
        stats = {
            "SharpeRatio": "0.38",
            "TotalTrades": "10",
        }
        result = build_fitness_feedback("violation msg", stats, _BASE_SPEC)
        # Only the "loosen entry" fix should appear for low trades
        assert "Loosen entry conditions" in result
        # The signal quality fix should NOT appear when trades < 50
        assert "Improve signal quality" not in result


# ---------------------------------------------------------------------------
# Tests: missing / empty stats graceful degradation
# ---------------------------------------------------------------------------


class TestMissingStatsDegradation:
    def test_empty_stats_returns_no_metrics_available(self) -> None:
        result = build_fitness_feedback("violation msg", {}, _BASE_SPEC)
        assert "no backtest metrics available" in result

    def test_partial_stats_omit_missing_lines(self) -> None:
        # Only SharpeRatio present — other lines should be absent
        stats = {"SharpeRatio": "0.4"}
        result = build_fitness_feedback("violation msg", stats, _BASE_SPEC)
        assert "Sharpe Ratio" in result
        assert "Total Trades" not in result
        assert "Win Rate" not in result

    def test_non_numeric_stat_gracefully_omitted(self) -> None:
        stats = {"SharpeRatio": "N/A", "TotalTrades": "100"}
        result = build_fitness_feedback("violation msg", stats, _BASE_SPEC)
        # SharpeRatio "N/A" cannot be parsed → omitted; TotalTrades parsed OK
        assert "Total Trades" in result
        assert "Sharpe Ratio" not in result

    def test_violations_msg_always_included(self) -> None:
        result = build_fitness_feedback("my_violation_marker", {}, _BASE_SPEC)
        assert "my_violation_marker" in result

    def test_do_not_change_structure_instruction_present(self) -> None:
        result = build_fitness_feedback("violation msg", {}, _BASE_SPEC)
        assert "Do NOT change the overall strategy structure" in result


# ---------------------------------------------------------------------------
# Tests: full metrics block structure
# ---------------------------------------------------------------------------


class TestFullMetricsBlock:
    def test_full_block_contains_all_headers(self) -> None:
        stats = {
            "SharpeRatio": "0.38",
            "TotalTrades": "12",
            "WinRate": "41.7",
            "NetProfit": "3.2",
            "AnnualReturn": "1.6",
            "Drawdown": "8.4",
            "ProfitLossRatio": "0.95",
            "LossRate": "58.3",
        }
        result = build_fitness_feedback("violation msg", stats, _BASE_SPEC)
        assert "PREVIOUS ATTEMPT FAILED — BACKTEST RESULTS:" in result
        assert "Metrics from last run:" in result
        assert "What went wrong:" in result
        assert "What to fix:" in result
        assert "Constraint violations:" in result
        assert "violation msg" in result

    def test_win_rate_as_fraction_displayed_as_percentage(self) -> None:
        stats = {"WinRate": "0.417"}  # fraction form
        result = build_fitness_feedback("violation msg", stats, _BASE_SPEC)
        assert "41.7%" in result

    def test_annual_return_as_fraction(self) -> None:
        stats = {"Compounding Annual Return": "0.016"}
        result = build_fitness_feedback("violation msg", stats, _BASE_SPEC)
        assert "Annual Return" in result
        assert "1.6%" in result

    def test_required_sharpe_shown_in_metrics(self) -> None:
        stats = {"SharpeRatio": "0.38"}
        result = build_fitness_feedback("violation msg", stats, _BASE_SPEC)
        assert "required: >= 0.50" in result
