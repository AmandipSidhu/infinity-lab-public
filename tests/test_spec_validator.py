"""Tests for scripts/spec_validator.py.

Covers:
- Happy path: valid specs produce zero errors
- Error conditions: missing required fields, bad risk params, ambiguous signals
- Warning conditions: missing recommended fields, low statistical power
- CLI integration: exit codes, JSON output format
- Edge cases: empty file, non-dict YAML, future end_date, date order
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

# Ensure the scripts directory is importable
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from spec_validator import (  # noqa: E402
    build_summary,
    validate_spec,
)

CORPUS_DIR = Path(__file__).parent / "spec_corpus"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_corpus(filename: str) -> dict[str, Any]:
    path = CORPUS_DIR / filename
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), f"Expected a dict from {filename}"
    return data


def codes(findings: list[dict[str, str]]) -> set[str]:
    return {f["code"] for f in findings}


def error_codes(findings: list[dict[str, str]]) -> set[str]:
    return {f["code"] for f in findings if f["severity"] == "ERROR"}


def warning_codes(findings: list[dict[str, str]]) -> set[str]:
    return {f["code"] for f in findings if f["severity"] == "WARNING"}


# ---------------------------------------------------------------------------
# Corpus: valid specs
# ---------------------------------------------------------------------------


class TestValidSpecs:
    def test_valid_001_produces_no_errors(self) -> None:
        spec = load_corpus("valid_001.yaml")
        findings = validate_spec(spec)
        assert error_codes(findings) == set(), f"Unexpected errors: {error_codes(findings)}"

    def test_valid_002_produces_no_errors(self) -> None:
        spec = load_corpus("valid_002.yaml")
        findings = validate_spec(spec)
        assert error_codes(findings) == set(), f"Unexpected errors: {error_codes(findings)}"

    def test_valid_001_summary_result_is_pass(self) -> None:
        spec = load_corpus("valid_001.yaml")
        findings = validate_spec(spec)
        summary = build_summary("valid_001.yaml", findings)
        assert summary["result"] == "PASS"
        assert summary["error_count"] == 0

    def test_valid_002_summary_result_is_pass(self) -> None:
        spec = load_corpus("valid_002.yaml")
        findings = validate_spec(spec)
        summary = build_summary("valid_002.yaml", findings)
        assert summary["result"] == "PASS"
        assert summary["error_count"] == 0


# ---------------------------------------------------------------------------
# Corpus: invalid specs — errors
# ---------------------------------------------------------------------------


class TestInvalidSpecMissingFields:
    def test_missing_metadata_name_triggers_e001(self) -> None:
        spec = load_corpus("invalid_001_missing_fields.yaml")
        findings = validate_spec(spec)
        assert "SVR-E001" in error_codes(findings)

    def test_missing_metadata_version_triggers_e002(self) -> None:
        spec = load_corpus("invalid_001_missing_fields.yaml")
        findings = validate_spec(spec)
        assert "SVR-E002" in error_codes(findings)

    def test_missing_metadata_description_triggers_e003(self) -> None:
        spec = load_corpus("invalid_001_missing_fields.yaml")
        findings = validate_spec(spec)
        assert "SVR-E003" in error_codes(findings)

    def test_missing_risk_management_triggers_e013(self) -> None:
        spec = load_corpus("invalid_001_missing_fields.yaml")
        findings = validate_spec(spec)
        assert "SVR-E013" in error_codes(findings)

    def test_missing_performance_targets_triggers_e016(self) -> None:
        spec = load_corpus("invalid_001_missing_fields.yaml")
        findings = validate_spec(spec)
        assert "SVR-E016" in error_codes(findings)

    def test_invalid_001_summary_result_is_fail(self) -> None:
        spec = load_corpus("invalid_001_missing_fields.yaml")
        findings = validate_spec(spec)
        summary = build_summary("invalid_001_missing_fields.yaml", findings)
        assert summary["result"] == "FAIL"
        assert summary["error_count"] > 0


class TestInvalidSpecBadRiskParams:
    def test_stop_loss_too_wide_triggers_e027(self) -> None:
        spec = load_corpus("invalid_002_bad_risk_params.yaml")
        findings = validate_spec(spec)
        assert "SVR-E027" in error_codes(findings)

    def test_max_position_size_over_100_triggers_e028(self) -> None:
        spec = load_corpus("invalid_002_bad_risk_params.yaml")
        findings = validate_spec(spec)
        assert "SVR-E028" in error_codes(findings)

    def test_excessive_leverage_triggers_e029(self) -> None:
        spec = load_corpus("invalid_002_bad_risk_params.yaml")
        findings = validate_spec(spec)
        assert "SVR-E029" in error_codes(findings)

    def test_high_drawdown_threshold_triggers_w016(self) -> None:
        spec = load_corpus("invalid_002_bad_risk_params.yaml")
        findings = validate_spec(spec)
        assert "SVR-W016" in warning_codes(findings)

    def test_invalid_002_summary_result_is_fail(self) -> None:
        spec = load_corpus("invalid_002_bad_risk_params.yaml")
        findings = validate_spec(spec)
        summary = build_summary("invalid_002_bad_risk_params.yaml", findings)
        assert summary["result"] == "FAIL"


class TestInvalidSpecAmbiguousSignals:
    def test_vague_language_in_entry_triggers_e030(self) -> None:
        spec = load_corpus("invalid_003_ambiguous_signals.yaml")
        findings = validate_spec(spec)
        assert "SVR-E030" in error_codes(findings)

    def test_low_min_trades_triggers_w011(self) -> None:
        spec = load_corpus("invalid_003_ambiguous_signals.yaml")
        findings = validate_spec(spec)
        assert "SVR-W011" in warning_codes(findings)

    def test_missing_benchmark_triggers_w008(self) -> None:
        spec = load_corpus("invalid_003_ambiguous_signals.yaml")
        findings = validate_spec(spec)
        assert "SVR-W008" in warning_codes(findings)

    def test_invalid_003_summary_result_is_fail(self) -> None:
        spec = load_corpus("invalid_003_ambiguous_signals.yaml")
        findings = validate_spec(spec)
        summary = build_summary("invalid_003_ambiguous_signals.yaml", findings)
        assert summary["result"] == "FAIL"


# ---------------------------------------------------------------------------
# Unit tests for individual rule categories
# ---------------------------------------------------------------------------


class TestMetadataRules:
    def _base_spec(self) -> dict[str, Any]:
        return {
            "metadata": {
                "name": "Test Strategy",
                "version": "1.0.0",
                "description": "A test strategy.",
                "author": "Tester",
                "created_at": "2026-01-01",
            },
            "strategy": {
                "type": "momentum",
                "universe": {"symbols": ["SPY"], "resolution": "daily"},
                "signals": {
                    "entry": {"conditions": ["RSI(14) > 50"]},
                    "exit": {"conditions": ["RSI(14) < 40"]},
                },
                "risk_management": {"stop_loss": 0.05, "max_position_size": 0.10},
                "performance_targets": {"sharpe_ratio_min": 1.0, "max_drawdown_threshold": 0.20},
                "backtesting": {
                    "start_date": "2018-01-01",
                    "end_date": "2023-12-31",
                    "initial_capital": 100000,
                    "min_trades": 200,
                },
            },
        }

    def test_missing_name_triggers_e001(self) -> None:
        spec = self._base_spec()
        del spec["metadata"]["name"]
        assert "SVR-E001" in error_codes(validate_spec(spec))

    def test_empty_name_triggers_e001(self) -> None:
        spec = self._base_spec()
        spec["metadata"]["name"] = "   "
        assert "SVR-E001" in error_codes(validate_spec(spec))

    def test_missing_version_triggers_e002(self) -> None:
        spec = self._base_spec()
        del spec["metadata"]["version"]
        assert "SVR-E002" in error_codes(validate_spec(spec))

    def test_missing_description_triggers_e003(self) -> None:
        spec = self._base_spec()
        del spec["metadata"]["description"]
        assert "SVR-E003" in error_codes(validate_spec(spec))

    def test_missing_author_triggers_w001(self) -> None:
        spec = self._base_spec()
        del spec["metadata"]["author"]
        assert "SVR-W001" in warning_codes(validate_spec(spec))

    def test_missing_created_at_triggers_w002(self) -> None:
        spec = self._base_spec()
        del spec["metadata"]["created_at"]
        assert "SVR-W002" in warning_codes(validate_spec(spec))

    def test_invalid_created_at_format_triggers_w002(self) -> None:
        spec = self._base_spec()
        spec["metadata"]["created_at"] = "01-01-2026"
        assert "SVR-W002" in warning_codes(validate_spec(spec))


class TestStrategyTypeRules:
    def _base(self) -> dict[str, Any]:
        return {
            "metadata": {"name": "X", "version": "1.0", "description": "X"},
            "strategy": {
                "type": "momentum",
                "universe": {"symbols": ["SPY"], "resolution": "daily"},
                "signals": {
                    "entry": {"conditions": ["RSI(14) > 50"]},
                    "exit": {"conditions": ["RSI(14) < 40"]},
                },
                "risk_management": {"stop_loss": 0.05, "max_position_size": 0.10},
                "performance_targets": {"sharpe_ratio_min": 1.0, "max_drawdown_threshold": 0.20},
                "backtesting": {
                    "start_date": "2018-01-01",
                    "end_date": "2023-12-31",
                    "initial_capital": 100000,
                    "min_trades": 200,
                },
            },
        }

    def test_missing_strategy_block_triggers_e004(self) -> None:
        spec: dict[str, Any] = {"metadata": {"name": "X", "version": "1.0", "description": "X"}}
        assert "SVR-E004" in error_codes(validate_spec(spec))

    def test_invalid_strategy_type_triggers_e005(self) -> None:
        spec = self._base()
        spec["strategy"]["type"] = "grid_trading"
        assert "SVR-E005" in error_codes(validate_spec(spec))

    def test_missing_strategy_type_triggers_e005(self) -> None:
        spec = self._base()
        del spec["strategy"]["type"]
        assert "SVR-E005" in error_codes(validate_spec(spec))


class TestUniverseRules:
    def _base(self) -> dict[str, Any]:
        return {
            "metadata": {"name": "X", "version": "1.0", "description": "X"},
            "strategy": {
                "type": "momentum",
                "universe": {"symbols": ["SPY", "QQQ"], "resolution": "daily"},
                "signals": {
                    "entry": {"conditions": ["SMA(50) > SMA(200)"]},
                    "exit": {"conditions": ["SMA(50) < SMA(200)"]},
                },
                "risk_management": {"stop_loss": 0.05, "max_position_size": 0.10},
                "performance_targets": {"sharpe_ratio_min": 1.0, "max_drawdown_threshold": 0.20},
                "backtesting": {
                    "start_date": "2018-01-01",
                    "end_date": "2023-12-31",
                    "initial_capital": 100000,
                    "min_trades": 200,
                },
            },
        }

    def test_missing_symbols_triggers_e006(self) -> None:
        spec = self._base()
        spec["strategy"]["universe"]["symbols"] = []
        assert "SVR-E006" in error_codes(validate_spec(spec))

    def test_invalid_resolution_triggers_e007(self) -> None:
        spec = self._base()
        spec["strategy"]["universe"]["resolution"] = "monthly"
        assert "SVR-E007" in error_codes(validate_spec(spec))

    def test_single_symbol_triggers_w020(self) -> None:
        spec = self._base()
        spec["strategy"]["universe"]["symbols"] = ["AAPL"]
        assert "SVR-W020" in warning_codes(validate_spec(spec))

    def test_missing_universe_triggers_w003(self) -> None:
        spec = self._base()
        del spec["strategy"]["universe"]
        assert "SVR-W003" in warning_codes(validate_spec(spec))


class TestSignalRules:
    def _base(self) -> dict[str, Any]:
        return {
            "metadata": {"name": "X", "version": "1.0", "description": "X"},
            "strategy": {
                "type": "momentum",
                "universe": {"symbols": ["SPY"], "resolution": "daily"},
                "signals": {
                    "entry": {"conditions": ["RSI(14) > 50"]},
                    "exit": {"conditions": ["RSI(14) < 40"]},
                },
                "risk_management": {"stop_loss": 0.05, "max_position_size": 0.10},
                "performance_targets": {"sharpe_ratio_min": 1.0, "max_drawdown_threshold": 0.20},
                "backtesting": {
                    "start_date": "2018-01-01",
                    "end_date": "2023-12-31",
                    "initial_capital": 100000,
                    "min_trades": 200,
                },
            },
        }

    def test_missing_signals_block_triggers_e008(self) -> None:
        spec = self._base()
        del spec["strategy"]["signals"]
        assert "SVR-E008" in error_codes(validate_spec(spec))

    def test_missing_entry_section_triggers_e009(self) -> None:
        spec = self._base()
        del spec["strategy"]["signals"]["entry"]
        assert "SVR-E009" in error_codes(validate_spec(spec))

    def test_missing_exit_section_triggers_e010(self) -> None:
        spec = self._base()
        del spec["strategy"]["signals"]["exit"]
        assert "SVR-E010" in error_codes(validate_spec(spec))

    def test_empty_entry_conditions_triggers_e011(self) -> None:
        spec = self._base()
        spec["strategy"]["signals"]["entry"]["conditions"] = []
        assert "SVR-E011" in error_codes(validate_spec(spec))

    def test_empty_exit_conditions_triggers_e012(self) -> None:
        spec = self._base()
        spec["strategy"]["signals"]["exit"]["conditions"] = []
        assert "SVR-E012" in error_codes(validate_spec(spec))

    def test_vague_entry_condition_triggers_e030(self) -> None:
        spec = self._base()
        spec["strategy"]["signals"]["entry"]["conditions"] = ["buy when it feels right"]
        assert "SVR-E030" in error_codes(validate_spec(spec))

    def test_vague_exit_condition_triggers_e030(self) -> None:
        spec = self._base()
        spec["strategy"]["signals"]["exit"]["conditions"] = ["sell as needed"]
        assert "SVR-E030" in error_codes(validate_spec(spec))

    def test_no_numeric_entry_triggers_w023(self) -> None:
        spec = self._base()
        spec["strategy"]["signals"]["entry"]["conditions"] = ["price above moving average"]
        assert "SVR-W023" in warning_codes(validate_spec(spec))

    def test_no_numeric_exit_triggers_w024(self) -> None:
        spec = self._base()
        spec["strategy"]["signals"]["exit"]["conditions"] = ["price drops below average"]
        assert "SVR-W024" in warning_codes(validate_spec(spec))


class TestRiskManagementRules:
    def _base(self) -> dict[str, Any]:
        return {
            "metadata": {"name": "X", "version": "1.0", "description": "X"},
            "strategy": {
                "type": "momentum",
                "universe": {"symbols": ["SPY"], "resolution": "daily"},
                "signals": {
                    "entry": {"conditions": ["SMA(50) > SMA(200)"]},
                    "exit": {"conditions": ["SMA(50) < SMA(200)"]},
                },
                "risk_management": {
                    "stop_loss": 0.05,
                    "max_position_size": 0.10,
                    "position_sizing": "percentage",
                    "take_profit": 0.15,
                    "max_drawdown": 0.20,
                    "max_leverage": 1.0,
                },
                "performance_targets": {"sharpe_ratio_min": 1.0, "max_drawdown_threshold": 0.20},
                "backtesting": {
                    "start_date": "2018-01-01",
                    "end_date": "2023-12-31",
                    "initial_capital": 100000,
                    "min_trades": 200,
                },
            },
        }

    def test_missing_stop_loss_triggers_e014(self) -> None:
        spec = self._base()
        del spec["strategy"]["risk_management"]["stop_loss"]
        assert "SVR-E014" in error_codes(validate_spec(spec))

    def test_stop_loss_too_wide_triggers_e027(self) -> None:
        spec = self._base()
        spec["strategy"]["risk_management"]["stop_loss"] = 0.25
        assert "SVR-E027" in error_codes(validate_spec(spec))

    def test_missing_max_position_size_triggers_e015(self) -> None:
        spec = self._base()
        del spec["strategy"]["risk_management"]["max_position_size"]
        assert "SVR-E015" in error_codes(validate_spec(spec))

    def test_max_position_size_over_1_triggers_e028(self) -> None:
        spec = self._base()
        spec["strategy"]["risk_management"]["max_position_size"] = 1.5
        assert "SVR-E028" in error_codes(validate_spec(spec))

    def test_max_position_size_zero_triggers_e028(self) -> None:
        spec = self._base()
        spec["strategy"]["risk_management"]["max_position_size"] = 0
        assert "SVR-E028" in error_codes(validate_spec(spec))

    def test_leverage_over_3_triggers_e029(self) -> None:
        spec = self._base()
        spec["strategy"]["risk_management"]["max_leverage"] = 4.0
        assert "SVR-E029" in error_codes(validate_spec(spec))

    def test_leverage_between_1_and_3_triggers_w026(self) -> None:
        spec = self._base()
        spec["strategy"]["risk_management"]["max_leverage"] = 2.0
        assert "SVR-W026" in warning_codes(validate_spec(spec))

    def test_missing_risk_block_triggers_e013(self) -> None:
        spec = self._base()
        del spec["strategy"]["risk_management"]
        assert "SVR-E013" in error_codes(validate_spec(spec))

    def test_missing_take_profit_triggers_w005(self) -> None:
        spec = self._base()
        del spec["strategy"]["risk_management"]["take_profit"]
        assert "SVR-W005" in warning_codes(validate_spec(spec))

    def test_missing_position_sizing_triggers_w004(self) -> None:
        spec = self._base()
        del spec["strategy"]["risk_management"]["position_sizing"]
        assert "SVR-W004" in warning_codes(validate_spec(spec))

    def test_missing_max_drawdown_triggers_w006(self) -> None:
        spec = self._base()
        del spec["strategy"]["risk_management"]["max_drawdown"]
        assert "SVR-W006" in warning_codes(validate_spec(spec))

    def test_very_high_max_drawdown_triggers_w015(self) -> None:
        spec = self._base()
        spec["strategy"]["risk_management"]["max_drawdown"] = 0.60
        assert "SVR-W015" in warning_codes(validate_spec(spec))


class TestPerformanceTargetRules:
    def _base(self) -> dict[str, Any]:
        return {
            "metadata": {"name": "X", "version": "1.0", "description": "X"},
            "strategy": {
                "type": "momentum",
                "universe": {"symbols": ["SPY"], "resolution": "daily"},
                "signals": {
                    "entry": {"conditions": ["RSI(14) > 50"]},
                    "exit": {"conditions": ["RSI(14) < 40"]},
                },
                "risk_management": {"stop_loss": 0.05, "max_position_size": 0.10},
                "performance_targets": {
                    "sharpe_ratio_min": 1.2,
                    "win_rate_min": 0.55,
                    "max_drawdown_threshold": 0.20,
                },
                "backtesting": {
                    "start_date": "2018-01-01",
                    "end_date": "2023-12-31",
                    "initial_capital": 100000,
                    "min_trades": 200,
                },
            },
        }

    def test_missing_performance_targets_triggers_e016(self) -> None:
        spec = self._base()
        del spec["strategy"]["performance_targets"]
        assert "SVR-E016" in error_codes(validate_spec(spec))

    def test_missing_sharpe_triggers_e017(self) -> None:
        spec = self._base()
        del spec["strategy"]["performance_targets"]["sharpe_ratio_min"]
        assert "SVR-E017" in error_codes(validate_spec(spec))

    def test_low_sharpe_triggers_w012(self) -> None:
        spec = self._base()
        spec["strategy"]["performance_targets"]["sharpe_ratio_min"] = 0.8
        assert "SVR-W012" in warning_codes(validate_spec(spec))

    def test_very_high_sharpe_triggers_w014(self) -> None:
        spec = self._base()
        spec["strategy"]["performance_targets"]["sharpe_ratio_min"] = 6.0
        assert "SVR-W014" in warning_codes(validate_spec(spec))

    def test_missing_max_drawdown_threshold_triggers_e018(self) -> None:
        spec = self._base()
        del spec["strategy"]["performance_targets"]["max_drawdown_threshold"]
        assert "SVR-E018" in error_codes(validate_spec(spec))

    def test_high_drawdown_threshold_triggers_w016(self) -> None:
        spec = self._base()
        spec["strategy"]["performance_targets"]["max_drawdown_threshold"] = 0.40
        assert "SVR-W016" in warning_codes(validate_spec(spec))

    def test_missing_win_rate_triggers_w007(self) -> None:
        spec = self._base()
        del spec["strategy"]["performance_targets"]["win_rate_min"]
        assert "SVR-W007" in warning_codes(validate_spec(spec))

    def test_low_win_rate_triggers_w013(self) -> None:
        spec = self._base()
        spec["strategy"]["performance_targets"]["win_rate_min"] = 0.40
        assert "SVR-W013" in warning_codes(validate_spec(spec))


class TestBacktestingRules:
    def _base(self) -> dict[str, Any]:
        return {
            "metadata": {"name": "X", "version": "1.0", "description": "X"},
            "strategy": {
                "type": "momentum",
                "universe": {"symbols": ["SPY"], "resolution": "daily"},
                "signals": {
                    "entry": {"conditions": ["SMA(50) > SMA(200)"]},
                    "exit": {"conditions": ["SMA(50) < SMA(200)"]},
                },
                "risk_management": {"stop_loss": 0.05, "max_position_size": 0.10},
                "performance_targets": {"sharpe_ratio_min": 1.0, "max_drawdown_threshold": 0.20},
                "backtesting": {
                    "start_date": "2018-01-01",
                    "end_date": "2023-12-31",
                    "initial_capital": 100000,
                    "min_trades": 200,
                    "benchmark": "SPY",
                },
            },
        }

    def test_missing_backtesting_triggers_e019(self) -> None:
        spec = self._base()
        del spec["strategy"]["backtesting"]
        assert "SVR-E019" in error_codes(validate_spec(spec))

    def test_missing_start_date_triggers_e020(self) -> None:
        spec = self._base()
        del spec["strategy"]["backtesting"]["start_date"]
        assert "SVR-E020" in error_codes(validate_spec(spec))

    def test_invalid_start_date_format_triggers_e024(self) -> None:
        spec = self._base()
        spec["strategy"]["backtesting"]["start_date"] = "01/01/2018"
        assert "SVR-E024" in error_codes(validate_spec(spec))

    def test_missing_end_date_triggers_e021(self) -> None:
        spec = self._base()
        del spec["strategy"]["backtesting"]["end_date"]
        assert "SVR-E021" in error_codes(validate_spec(spec))

    def test_invalid_end_date_format_triggers_e025(self) -> None:
        spec = self._base()
        spec["strategy"]["backtesting"]["end_date"] = "Dec 31 2023"
        assert "SVR-E025" in error_codes(validate_spec(spec))

    def test_start_after_end_triggers_e026(self) -> None:
        spec = self._base()
        spec["strategy"]["backtesting"]["start_date"] = "2024-01-01"
        spec["strategy"]["backtesting"]["end_date"] = "2020-01-01"
        assert "SVR-E026" in error_codes(validate_spec(spec))

    def test_start_equal_end_triggers_e026(self) -> None:
        spec = self._base()
        spec["strategy"]["backtesting"]["start_date"] = "2022-06-01"
        spec["strategy"]["backtesting"]["end_date"] = "2022-06-01"
        assert "SVR-E026" in error_codes(validate_spec(spec))

    def test_missing_initial_capital_triggers_e022(self) -> None:
        spec = self._base()
        del spec["strategy"]["backtesting"]["initial_capital"]
        assert "SVR-E022" in error_codes(validate_spec(spec))

    def test_zero_initial_capital_triggers_e022(self) -> None:
        spec = self._base()
        spec["strategy"]["backtesting"]["initial_capital"] = 0
        assert "SVR-E022" in error_codes(validate_spec(spec))

    def test_small_capital_triggers_w017(self) -> None:
        spec = self._base()
        spec["strategy"]["backtesting"]["initial_capital"] = 5000
        assert "SVR-W017" in warning_codes(validate_spec(spec))

    def test_missing_min_trades_triggers_e023(self) -> None:
        spec = self._base()
        del spec["strategy"]["backtesting"]["min_trades"]
        assert "SVR-E023" in error_codes(validate_spec(spec))

    def test_min_trades_below_100_triggers_e023(self) -> None:
        spec = self._base()
        spec["strategy"]["backtesting"]["min_trades"] = 50
        assert "SVR-E023" in error_codes(validate_spec(spec))

    def test_min_trades_below_1000_triggers_w011(self) -> None:
        spec = self._base()
        spec["strategy"]["backtesting"]["min_trades"] = 300
        assert "SVR-W011" in warning_codes(validate_spec(spec))

    def test_missing_benchmark_triggers_w008(self) -> None:
        spec = self._base()
        del spec["strategy"]["backtesting"]["benchmark"]
        assert "SVR-W008" in warning_codes(validate_spec(spec))

    def test_future_end_date_triggers_w018(self) -> None:
        spec = self._base()
        spec["strategy"]["backtesting"]["end_date"] = "2099-12-31"
        assert "SVR-W018" in warning_codes(validate_spec(spec))

    def test_short_period_triggers_w019(self) -> None:
        spec = self._base()
        spec["strategy"]["backtesting"]["start_date"] = "2023-01-01"
        spec["strategy"]["backtesting"]["end_date"] = "2023-06-30"
        assert "SVR-W019" in warning_codes(validate_spec(spec))


class TestDataRequirementsRules:
    def _base(self) -> dict[str, Any]:
        return {
            "metadata": {"name": "X", "version": "1.0", "description": "X"},
            "strategy": {
                "type": "momentum",
                "universe": {"symbols": ["SPY"], "resolution": "daily"},
                "signals": {
                    "entry": {"conditions": ["RSI(14) > 50"]},
                    "exit": {"conditions": ["RSI(14) < 40"]},
                },
                "risk_management": {"stop_loss": 0.05, "max_position_size": 0.10},
                "performance_targets": {"sharpe_ratio_min": 1.0, "max_drawdown_threshold": 0.20},
                "backtesting": {
                    "start_date": "2018-01-01",
                    "end_date": "2023-12-31",
                    "initial_capital": 100000,
                    "min_trades": 200,
                },
                "data_requirements": {
                    "resolution": "daily",
                    "min_history_days": 252,
                    "indicators": ["RSI(14)"],
                },
            },
        }

    def test_missing_data_requirements_triggers_w009(self) -> None:
        spec = self._base()
        del spec["strategy"]["data_requirements"]
        assert "SVR-W009" in warning_codes(validate_spec(spec))

    def test_empty_indicators_triggers_w010(self) -> None:
        spec = self._base()
        spec["strategy"]["data_requirements"]["indicators"] = []
        assert "SVR-W010" in warning_codes(validate_spec(spec))

    def test_missing_indicators_triggers_w010(self) -> None:
        spec = self._base()
        del spec["strategy"]["data_requirements"]["indicators"]
        assert "SVR-W010" in warning_codes(validate_spec(spec))

    def test_missing_min_history_days_triggers_w022(self) -> None:
        spec = self._base()
        del spec["strategy"]["data_requirements"]["min_history_days"]
        assert "SVR-W022" in warning_codes(validate_spec(spec))


class TestMarketMakingResolutionRule:
    def _base(self) -> dict[str, Any]:
        return {
            "metadata": {"name": "X", "version": "1.0", "description": "X"},
            "strategy": {
                "type": "market_making",
                "universe": {"symbols": ["AAPL"], "resolution": "minute"},
                "signals": {
                    "entry": {"conditions": ["bid-ask spread > 0.05"]},
                    "exit": {"conditions": ["inventory > 100 shares"]},
                },
                "risk_management": {"stop_loss": 0.02, "max_position_size": 0.05},
                "performance_targets": {"sharpe_ratio_min": 1.0, "max_drawdown_threshold": 0.10},
                "backtesting": {
                    "start_date": "2020-01-01",
                    "end_date": "2023-12-31",
                    "initial_capital": 500000,
                    "min_trades": 1000,
                },
            },
        }

    def test_market_making_daily_resolution_triggers_w025(self) -> None:
        spec = self._base()
        spec["strategy"]["universe"]["resolution"] = "daily"
        assert "SVR-W025" in warning_codes(validate_spec(spec))

    def test_market_making_minute_resolution_no_w025(self) -> None:
        spec = self._base()
        assert "SVR-W025" not in warning_codes(validate_spec(spec))


# ---------------------------------------------------------------------------
# build_summary tests
# ---------------------------------------------------------------------------


class TestBuildSummary:
    def test_summary_structure_on_pass(self) -> None:
        summary = build_summary("test.yaml", [])
        assert summary["result"] == "PASS"
        assert summary["error_count"] == 0
        assert summary["warning_count"] == 0
        assert summary["findings"] == []
        assert summary["spec_file"] == "test.yaml"

    def test_summary_result_fail_when_errors_present(self) -> None:
        findings = [{"code": "SVR-E001", "severity": "ERROR", "message": "missing name", "field": "metadata.name"}]
        summary = build_summary("bad.yaml", findings)
        assert summary["result"] == "FAIL"
        assert summary["error_count"] == 1
        assert summary["warning_count"] == 0

    def test_summary_result_pass_when_only_warnings(self) -> None:
        findings = [{"code": "SVR-W001", "severity": "WARNING", "message": "missing author", "field": "metadata.author"}]
        summary = build_summary("warn.yaml", findings)
        assert summary["result"] == "PASS"
        assert summary["warning_count"] == 1
        assert summary["error_count"] == 0


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


class TestCLI:
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "spec_validator.py"), *args],
            capture_output=True,
            text=True,
        )

    def test_valid_spec_exits_0(self) -> None:
        result = self._run(str(CORPUS_DIR / "valid_001.yaml"))
        assert result.returncode == 0

    def test_invalid_spec_exits_1(self) -> None:
        result = self._run(str(CORPUS_DIR / "invalid_001_missing_fields.yaml"))
        assert result.returncode == 1

    def test_output_is_valid_json(self) -> None:
        result = self._run(str(CORPUS_DIR / "valid_001.yaml"))
        parsed = json.loads(result.stdout)
        assert "result" in parsed
        assert "findings" in parsed

    def test_missing_file_exits_2(self) -> None:
        result = self._run("/nonexistent/path/spec.yaml")
        assert result.returncode == 2

    def test_no_args_exits_2(self) -> None:
        result = self._run()
        assert result.returncode == 2

    def test_invalid_spec_json_contains_errors(self) -> None:
        result = self._run(str(CORPUS_DIR / "invalid_002_bad_risk_params.yaml"))
        parsed = json.loads(result.stdout)
        assert parsed["result"] == "FAIL"
        error_found_codes = {f["code"] for f in parsed["findings"] if f["severity"] == "ERROR"}
        assert "SVR-E027" in error_found_codes

    def test_findings_sorted_errors_before_warnings(self) -> None:
        result = self._run(str(CORPUS_DIR / "invalid_003_ambiguous_signals.yaml"))
        parsed = json.loads(result.stdout)
        findings = parsed["findings"]
        severities = [f["severity"] for f in findings]
        # All ERRORs must appear before any WARNING
        seen_warning = False
        for sev in severities:
            if sev == "WARNING":
                seen_warning = True
            if seen_warning and sev == "ERROR":
                pytest.fail("An ERROR appeared after a WARNING in the findings list")
