"""Tests for scripts/spec_validator.py.

Covers:
- Happy path: valid specs (day_trade and swing) produce zero errors
- Error conditions: missing required fields, bad risk params, ambiguous signals
- Warning conditions: missing recommended fields
- CLI integration: exit codes, JSON output format
- Edge cases: empty spec, non-dict YAML, date boundary conditions
- Unit tests for every SVR rule category
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

# Ensure the scripts directory is importable.
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from spec_validator import (  # noqa: E402
    _sort_findings,
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

    def test_valid_001_produces_no_warnings(self) -> None:
        spec = load_corpus("valid_001.yaml")
        findings = validate_spec(spec)
        assert warning_codes(findings) == set(), f"Unexpected warnings: {warning_codes(findings)}"

    def test_valid_002_produces_no_warnings(self) -> None:
        spec = load_corpus("valid_002.yaml")
        findings = validate_spec(spec)
        assert warning_codes(findings) == set(), f"Unexpected warnings: {warning_codes(findings)}"

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
    def test_missing_trading_style_triggers_e001(self) -> None:
        spec = load_corpus("invalid_001_missing_fields.yaml")
        findings = validate_spec(spec)
        assert "SVR-E001" in error_codes(findings)

    def test_missing_metadata_name_triggers_e005(self) -> None:
        spec = load_corpus("invalid_001_missing_fields.yaml")
        findings = validate_spec(spec)
        assert "SVR-E005" in error_codes(findings)

    def test_missing_capital_triggers_e003(self) -> None:
        spec = load_corpus("invalid_001_missing_fields.yaml")
        findings = validate_spec(spec)
        assert "SVR-E003" in error_codes(findings)

    def test_missing_stop_loss_triggers_e023(self) -> None:
        spec = load_corpus("invalid_001_missing_fields.yaml")
        findings = validate_spec(spec)
        assert "SVR-E023" in error_codes(findings)

    def test_missing_acceptance_criteria_triggers_e021(self) -> None:
        spec = load_corpus("invalid_001_missing_fields.yaml")
        findings = validate_spec(spec)
        assert "SVR-E021" in error_codes(findings)

    def test_invalid_001_summary_result_is_fail(self) -> None:
        spec = load_corpus("invalid_001_missing_fields.yaml")
        findings = validate_spec(spec)
        summary = build_summary("invalid_001_missing_fields.yaml", findings)
        assert summary["result"] == "FAIL"
        assert summary["error_count"] > 0


class TestInvalidSpecBadRiskParams:
    def test_excessive_leverage_triggers_e025(self) -> None:
        spec = load_corpus("invalid_002_bad_risk_params.yaml")
        findings = validate_spec(spec)
        assert "SVR-E025" in error_codes(findings)

    def test_invalid_002_summary_result_is_fail(self) -> None:
        spec = load_corpus("invalid_002_bad_risk_params.yaml")
        findings = validate_spec(spec)
        summary = build_summary("invalid_002_bad_risk_params.yaml", findings)
        assert summary["result"] == "FAIL"


class TestInvalidSpecAmbiguousSignals:
    def test_vague_language_triggers_e034(self) -> None:
        spec = load_corpus("invalid_003_ambiguous_signals.yaml")
        findings = validate_spec(spec)
        assert "SVR-E034" in error_codes(findings)

    def test_no_time_based_exit_triggers_w030(self) -> None:
        spec = load_corpus("invalid_003_ambiguous_signals.yaml")
        findings = validate_spec(spec)
        assert "SVR-W030" in warning_codes(findings)

    def test_invalid_003_summary_result_is_fail(self) -> None:
        spec = load_corpus("invalid_003_ambiguous_signals.yaml")
        findings = validate_spec(spec)
        summary = build_summary("invalid_003_ambiguous_signals.yaml", findings)
        assert summary["result"] == "FAIL"


# ---------------------------------------------------------------------------
# Unit tests for individual rule categories
# ---------------------------------------------------------------------------


def _base_spec() -> dict[str, Any]:
    """Return a fully valid spec against the new schema (produces zero findings)."""
    return {
        "metadata": {
            "name": "Test Strategy",
            "trading_style": "swing",
            "description": "A test strategy with all required fields populated for unit testing purposes.",
            "author": "Tester",
            "version": "1.0.0",
        },
        "capital": {
            "allocation_usd": 50000,
        },
        "data": {
            "instruments": ["SPY", "QQQ"],
            "resolution": "daily",
            "start_date": "2015-01-01",
            "end_date": "2023-12-31",
        },
        "signals": {
            "entry": ["RSI(14) > 50 and price above the 200-day SMA"],
            "exit": ["RSI(14) < 40 or holding period exceeds 20 bars"],
        },
        "risk_management": {
            "stop_loss": 0.05,
            "position_sizing": "1% risk per trade",
            "leverage": 1.0,
            "max_positions": 5,
            "risk_per_trade_pct": 0.01,
        },
        "acceptance_criteria": {
            "max_drawdown_pct": 20.0,
            "min_sharpe_ratio": 1.2,
            "min_profit_factor": 1.4,
            "min_trades": 200,
            "min_cagr": 15.0,
        },
        "assumptions": {
            "fees": 0.001,
            "slippage": 0.001,
        },
    }


class TestMetadataRules:
    def test_base_spec_has_no_metadata_errors(self) -> None:
        assert error_codes(validate_spec(_base_spec())) == set()

    def test_missing_trading_style_triggers_e001(self) -> None:
        spec = _base_spec()
        del spec["metadata"]["trading_style"]
        assert "SVR-E001" in error_codes(validate_spec(spec))

    def test_empty_trading_style_triggers_e001(self) -> None:
        spec = _base_spec()
        spec["metadata"]["trading_style"] = "   "
        assert "SVR-E001" in error_codes(validate_spec(spec))

    def test_invalid_trading_style_triggers_e002(self) -> None:
        spec = _base_spec()
        spec["metadata"]["trading_style"] = "grid_trading"
        assert "SVR-E002" in error_codes(validate_spec(spec))

    def test_valid_trading_styles_accepted(self) -> None:
        for style in ("day_trade", "swing", "position"):
            spec = _base_spec()
            spec["metadata"]["trading_style"] = style
            if style == "day_trade":
                spec["constraints"] = {"max_holding_minutes": 60, "close_eod": True}
                spec["assumptions"]["fees"] = 0.001
            assert "SVR-E001" not in error_codes(validate_spec(spec))
            assert "SVR-E002" not in error_codes(validate_spec(spec))

    def test_missing_name_triggers_e005(self) -> None:
        spec = _base_spec()
        del spec["metadata"]["name"]
        assert "SVR-E005" in error_codes(validate_spec(spec))

    def test_empty_name_triggers_e005(self) -> None:
        spec = _base_spec()
        spec["metadata"]["name"] = "   "
        assert "SVR-E005" in error_codes(validate_spec(spec))

    def test_missing_description_triggers_w001(self) -> None:
        spec = _base_spec()
        del spec["metadata"]["description"]
        assert "SVR-W001" in warning_codes(validate_spec(spec))

    def test_short_description_triggers_w001(self) -> None:
        spec = _base_spec()
        spec["metadata"]["description"] = "Too short."
        assert "SVR-W001" in warning_codes(validate_spec(spec))

    def test_long_enough_description_no_w001(self) -> None:
        spec = _base_spec()
        spec["metadata"]["description"] = "A" * 20
        assert "SVR-W001" not in warning_codes(validate_spec(spec))

    def test_missing_author_triggers_w002(self) -> None:
        spec = _base_spec()
        del spec["metadata"]["author"]
        assert "SVR-W002" in warning_codes(validate_spec(spec))

    def test_missing_version_triggers_w062(self) -> None:
        spec = _base_spec()
        del spec["metadata"]["version"]
        assert "SVR-W062" in warning_codes(validate_spec(spec))

    def test_empty_version_triggers_w062(self) -> None:
        spec = _base_spec()
        spec["metadata"]["version"] = ""
        assert "SVR-W062" in warning_codes(validate_spec(spec))


class TestCapitalRules:
    def test_missing_capital_section_triggers_e003(self) -> None:
        spec = _base_spec()
        del spec["capital"]
        assert "SVR-E003" in error_codes(validate_spec(spec))

    def test_capital_missing_both_fields_triggers_e003(self) -> None:
        spec = _base_spec()
        spec["capital"] = {}
        assert "SVR-E003" in error_codes(validate_spec(spec))

    def test_zero_allocation_usd_triggers_e004(self) -> None:
        spec = _base_spec()
        spec["capital"] = {"allocation_usd": 0}
        assert "SVR-E004" in error_codes(validate_spec(spec))

    def test_negative_allocation_usd_triggers_e004(self) -> None:
        spec = _base_spec()
        spec["capital"] = {"allocation_usd": -1000}
        assert "SVR-E004" in error_codes(validate_spec(spec))

    def test_zero_allocation_pct_triggers_e004(self) -> None:
        spec = _base_spec()
        spec["capital"] = {"allocation_pct": 0}
        assert "SVR-E004" in error_codes(validate_spec(spec))

    def test_valid_allocation_usd_no_errors(self) -> None:
        spec = _base_spec()
        spec["capital"] = {"allocation_usd": 10000}
        assert "SVR-E003" not in error_codes(validate_spec(spec))
        assert "SVR-E004" not in error_codes(validate_spec(spec))

    def test_valid_allocation_pct_no_errors(self) -> None:
        spec = _base_spec()
        spec["capital"] = {"allocation_pct": 0.25}
        assert "SVR-E003" not in error_codes(validate_spec(spec))
        assert "SVR-E004" not in error_codes(validate_spec(spec))


class TestConstraintsRules:
    def _day_trade_spec(self) -> dict[str, Any]:
        spec = _base_spec()
        spec["metadata"]["trading_style"] = "day_trade"
        spec["constraints"] = {"max_holding_minutes": 60, "close_eod": True}
        spec["data"]["resolution"] = "minute"
        return spec

    def test_day_trade_base_has_no_constraint_errors(self) -> None:
        assert error_codes(validate_spec(self._day_trade_spec())) == set()

    def test_day_trade_missing_max_holding_triggers_e011(self) -> None:
        spec = self._day_trade_spec()
        del spec["constraints"]["max_holding_minutes"]
        assert "SVR-E011" in error_codes(validate_spec(spec))

    def test_day_trade_max_holding_over_390_triggers_e012(self) -> None:
        spec = self._day_trade_spec()
        spec["constraints"]["max_holding_minutes"] = 400
        assert "SVR-E012" in error_codes(validate_spec(spec))

    def test_day_trade_max_holding_exactly_390_no_e012(self) -> None:
        spec = self._day_trade_spec()
        spec["constraints"]["max_holding_minutes"] = 390
        assert "SVR-E012" not in error_codes(validate_spec(spec))

    def test_day_trade_close_eod_false_triggers_e013(self) -> None:
        spec = self._day_trade_spec()
        spec["constraints"]["close_eod"] = False
        assert "SVR-E013" in error_codes(validate_spec(spec))

    def test_day_trade_close_eod_missing_triggers_e013(self) -> None:
        spec = self._day_trade_spec()
        del spec["constraints"]["close_eod"]
        assert "SVR-E013" in error_codes(validate_spec(spec))

    def test_swing_ignores_constraint_rules(self) -> None:
        spec = _base_spec()  # trading_style = "swing"
        assert "SVR-E011" not in error_codes(validate_spec(spec))
        assert "SVR-E012" not in error_codes(validate_spec(spec))
        assert "SVR-E013" not in error_codes(validate_spec(spec))


class TestDataRules:
    def test_missing_instruments_and_no_universe_triggers_e056(self) -> None:
        spec = _base_spec()
        del spec["data"]["instruments"]
        assert "SVR-E056" in error_codes(validate_spec(spec))

    def test_empty_instruments_list_triggers_e056(self) -> None:
        spec = _base_spec()
        spec["data"]["instruments"] = []
        assert "SVR-E056" in error_codes(validate_spec(spec))

    def test_dynamic_universe_valid_satisfies_e056(self) -> None:
        spec = _base_spec()
        del spec["data"]["instruments"]
        spec["data"]["universe"] = {
            "mode": "dynamic",
            "screener": {"criteria": "top_volume", "max_symbols": 10},
        }
        found_codes = error_codes(validate_spec(spec))
        assert "SVR-E056" not in found_codes
        assert "SVR-E056a" not in found_codes
        assert "SVR-E056b" not in found_codes

    def test_dynamic_universe_invalid_criteria_triggers_e056a(self) -> None:
        spec = _base_spec()
        del spec["data"]["instruments"]
        spec["data"]["universe"] = {
            "mode": "dynamic",
            "screener": {"criteria": "bad_criteria", "max_symbols": 10},
        }
        assert "SVR-E056a" in error_codes(validate_spec(spec))

    def test_dynamic_universe_zero_max_symbols_triggers_e056b(self) -> None:
        spec = _base_spec()
        del spec["data"]["instruments"]
        spec["data"]["universe"] = {
            "mode": "dynamic",
            "screener": {"criteria": "top_volume", "max_symbols": 0},
        }
        assert "SVR-E056b" in error_codes(validate_spec(spec))

    def test_dynamic_universe_invalid_screener_also_triggers_e056(self) -> None:
        spec = _base_spec()
        del spec["data"]["instruments"]
        spec["data"]["universe"] = {
            "mode": "dynamic",
            "screener": {"criteria": "bad", "max_symbols": -5},
        }
        found = error_codes(validate_spec(spec))
        assert "SVR-E056" in found
        assert "SVR-E056a" in found
        assert "SVR-E056b" in found

    def test_universe_non_dynamic_without_instruments_triggers_e056(self) -> None:
        spec = _base_spec()
        del spec["data"]["instruments"]
        spec["data"]["universe"] = {"mode": "static", "symbols": ["SPY"]}
        assert "SVR-E056" in error_codes(validate_spec(spec))

    def test_invalid_resolution_triggers_e057(self) -> None:
        spec = _base_spec()
        spec["data"]["resolution"] = "monthly"
        assert "SVR-E057" in error_codes(validate_spec(spec))

    def test_missing_resolution_triggers_e057(self) -> None:
        spec = _base_spec()
        del spec["data"]["resolution"]
        assert "SVR-E057" in error_codes(validate_spec(spec))

    def test_no_dates_no_lookback_triggers_e058(self) -> None:
        spec = _base_spec()
        del spec["data"]["start_date"]
        del spec["data"]["end_date"]
        assert "SVR-E058" in error_codes(validate_spec(spec))

    def test_lookback_years_satisfies_date_requirement(self) -> None:
        spec = _base_spec()
        del spec["data"]["start_date"]
        del spec["data"]["end_date"]
        spec["data"]["lookback_years"] = 6
        assert "SVR-E058" not in error_codes(validate_spec(spec))

    def test_start_after_end_triggers_e060(self) -> None:
        spec = _base_spec()
        spec["data"]["start_date"] = "2024-01-01"
        spec["data"]["end_date"] = "2020-01-01"
        assert "SVR-E060" in error_codes(validate_spec(spec))

    def test_start_equal_end_triggers_e060(self) -> None:
        spec = _base_spec()
        spec["data"]["start_date"] = "2022-06-01"
        spec["data"]["end_date"] = "2022-06-01"
        assert "SVR-E060" in error_codes(validate_spec(spec))

    def test_range_under_2_years_triggers_e059_not_w050(self) -> None:
        spec = _base_spec()
        spec["data"]["start_date"] = "2022-01-01"
        spec["data"]["end_date"] = "2023-06-01"  # ~1.4 years
        found = validate_spec(spec)
        assert "SVR-E059" in error_codes(found)
        assert "SVR-W050" not in warning_codes(found)

    def test_range_2_to_5_years_triggers_w050_not_e059(self) -> None:
        spec = _base_spec()
        spec["data"]["start_date"] = "2021-01-01"
        spec["data"]["end_date"] = "2023-12-31"  # ~3 years
        found = validate_spec(spec)
        assert "SVR-W050" in warning_codes(found)
        assert "SVR-E059" not in error_codes(found)

    def test_range_over_5_years_no_date_findings(self) -> None:
        spec = _base_spec()
        spec["data"]["start_date"] = "2015-01-01"
        spec["data"]["end_date"] = "2023-12-31"  # ~9 years
        found = validate_spec(spec)
        assert "SVR-E059" not in error_codes(found)
        assert "SVR-W050" not in warning_codes(found)


class TestSignalRules:
    def test_missing_signals_section_triggers_e031_and_e033(self) -> None:
        spec = _base_spec()
        del spec["signals"]
        found = error_codes(validate_spec(spec))
        assert "SVR-E031" in found
        assert "SVR-E033" in found

    def test_empty_entry_list_triggers_e031(self) -> None:
        spec = _base_spec()
        spec["signals"]["entry"] = []
        assert "SVR-E031" in error_codes(validate_spec(spec))

    def test_entry_not_list_triggers_e031(self) -> None:
        spec = _base_spec()
        spec["signals"]["entry"] = "buy when price is high"
        assert "SVR-E031" in error_codes(validate_spec(spec))

    def test_entry_without_numeric_threshold_triggers_e032(self) -> None:
        spec = _base_spec()
        spec["signals"]["entry"] = ["price above moving average"]
        assert "SVR-E032" in error_codes(validate_spec(spec))

    def test_entry_with_numeric_threshold_no_e032(self) -> None:
        spec = _base_spec()
        spec["signals"]["entry"] = ["RSI(14) > 50"]
        assert "SVR-E032" not in error_codes(validate_spec(spec))

    def test_empty_exit_list_triggers_e033(self) -> None:
        spec = _base_spec()
        spec["signals"]["exit"] = []
        assert "SVR-E033" in error_codes(validate_spec(spec))

    def test_banned_term_in_entry_triggers_e034(self) -> None:
        for term in ("momentum", "trending", "oversold", "overbought", "volatile",
                     "reasonable", "appropriate", "approximately", "as needed"):
            spec = _base_spec()
            spec["signals"]["entry"] = [f"buy when {term} exceeds threshold 50"]
            assert "SVR-E034" in error_codes(validate_spec(spec)), f"Expected E034 for term: {term!r}"

    def test_banned_term_in_exit_triggers_e034(self) -> None:
        spec = _base_spec()
        spec["signals"]["exit"] = ["sell when market is volatile"]
        assert "SVR-E034" in error_codes(validate_spec(spec))

    def test_clean_signal_conditions_no_e034(self) -> None:
        spec = _base_spec()
        assert "SVR-E034" not in error_codes(validate_spec(spec))

    def test_lookahead_next_bar_triggers_e066(self) -> None:
        spec = _base_spec()
        spec["signals"]["entry"] = ["enter at next bar open price > 100"]
        assert "SVR-E066" in error_codes(validate_spec(spec))

    def test_lookahead_pattern_triggers_e066(self) -> None:
        spec = _base_spec()
        spec["signals"]["entry"] = ["use lookahead price of 50 to enter"]
        assert "SVR-E066" in error_codes(validate_spec(spec))

    def test_nondeterministic_random_triggers_e067(self) -> None:
        spec = _base_spec()
        spec["signals"]["entry"] = ["enter at a random price above 100"]
        assert "SVR-E067" in error_codes(validate_spec(spec))

    def test_unavailable_level2_data_triggers_e068(self) -> None:
        spec = _base_spec()
        spec["signals"]["entry"] = ["enter when level 2 bid > 50"]
        assert "SVR-E068" in error_codes(validate_spec(spec))

    def test_unavailable_dark_pool_triggers_e068(self) -> None:
        spec = _base_spec()
        spec["signals"]["entry"] = ["enter when dark pool volume > 1000"]
        assert "SVR-E068" in error_codes(validate_spec(spec))

    def test_no_time_based_exit_triggers_w030(self) -> None:
        spec = _base_spec()
        spec["signals"]["exit"] = ["RSI(14) < 30", "price drops 5% below entry"]
        assert "SVR-W030" in warning_codes(validate_spec(spec))

    def test_time_based_exit_satisfies_w030(self) -> None:
        for condition in [
            "exit after 30 minutes",
            "exit after 10 bars",
            "close at end of session",
            "exit if holding period exceeds 5 days",
        ]:
            spec = _base_spec()
            spec["signals"]["exit"] = [condition]
            assert "SVR-W030" not in warning_codes(validate_spec(spec)), (
                f"Expected no W030 for time-based exit: {condition!r}"
            )


class TestRiskManagementRules:
    def test_missing_risk_management_triggers_e023_and_e024(self) -> None:
        spec = _base_spec()
        del spec["risk_management"]
        found = error_codes(validate_spec(spec))
        assert "SVR-E023" in found
        assert "SVR-E024" in found

    def test_missing_stop_loss_triggers_e023(self) -> None:
        spec = _base_spec()
        del spec["risk_management"]["stop_loss"]
        assert "SVR-E023" in error_codes(validate_spec(spec))

    def test_zero_stop_loss_triggers_e023(self) -> None:
        spec = _base_spec()
        spec["risk_management"]["stop_loss"] = 0
        assert "SVR-E023" in error_codes(validate_spec(spec))

    def test_negative_stop_loss_triggers_e023(self) -> None:
        spec = _base_spec()
        spec["risk_management"]["stop_loss"] = -0.05
        assert "SVR-E023" in error_codes(validate_spec(spec))

    def test_positive_stop_loss_number_no_e023(self) -> None:
        spec = _base_spec()
        spec["risk_management"]["stop_loss"] = 0.05
        assert "SVR-E023" not in error_codes(validate_spec(spec))

    def test_stop_loss_dict_valid_pct_no_e023(self) -> None:
        spec = _base_spec()
        spec["risk_management"]["stop_loss"] = {"pct": 0.05}
        assert "SVR-E023" not in error_codes(validate_spec(spec))

    def test_stop_loss_dict_valid_atr_no_e023(self) -> None:
        spec = _base_spec()
        spec["risk_management"]["stop_loss"] = {"atr_multiplier": 2.0}
        assert "SVR-E023" not in error_codes(validate_spec(spec))

    def test_stop_loss_dict_valid_absolute_usd_no_e023(self) -> None:
        spec = _base_spec()
        spec["risk_management"]["stop_loss"] = {"absolute_usd": 100}
        assert "SVR-E023" not in error_codes(validate_spec(spec))

    def test_stop_loss_dict_no_valid_fields_triggers_e023(self) -> None:
        spec = _base_spec()
        spec["risk_management"]["stop_loss"] = {"pct": 0, "atr_multiplier": -1}
        assert "SVR-E023" in error_codes(validate_spec(spec))

    def test_missing_position_sizing_triggers_e024(self) -> None:
        spec = _base_spec()
        del spec["risk_management"]["position_sizing"]
        assert "SVR-E024" in error_codes(validate_spec(spec))

    def test_empty_position_sizing_triggers_e024(self) -> None:
        spec = _base_spec()
        spec["risk_management"]["position_sizing"] = "   "
        assert "SVR-E024" in error_codes(validate_spec(spec))

    def test_leverage_over_4_triggers_e025(self) -> None:
        spec = _base_spec()
        spec["risk_management"]["leverage"] = 4.1
        assert "SVR-E025" in error_codes(validate_spec(spec))

    def test_leverage_exactly_4_no_e025(self) -> None:
        spec = _base_spec()
        spec["risk_management"]["leverage"] = 4.0
        assert "SVR-E025" not in error_codes(validate_spec(spec))

    def test_margin_mention_without_leverage_triggers_e026(self) -> None:
        spec = _base_spec()
        spec["notes"] = "This strategy uses margin to amplify returns."
        del spec["risk_management"]["leverage"]
        assert "SVR-E026" in error_codes(validate_spec(spec))

    def test_futures_mention_without_leverage_triggers_e026(self) -> None:
        spec = _base_spec()
        spec["metadata"]["description"] = "A strategy trading equity index futures on the CME with 100 contracts."
        del spec["risk_management"]["leverage"]
        assert "SVR-E026" in error_codes(validate_spec(spec))

    def test_futures_mention_with_leverage_no_e026(self) -> None:
        spec = _base_spec()
        spec["notes"] = "Strategy trades futures for hedging."
        spec["risk_management"]["leverage"] = 2.0
        assert "SVR-E026" not in error_codes(validate_spec(spec))

    def test_missing_max_positions_triggers_w020(self) -> None:
        spec = _base_spec()
        del spec["risk_management"]["max_positions"]
        assert "SVR-W020" in warning_codes(validate_spec(spec))

    def test_missing_risk_per_trade_pct_triggers_w021(self) -> None:
        spec = _base_spec()
        del spec["risk_management"]["risk_per_trade_pct"]
        assert "SVR-W021" in warning_codes(validate_spec(spec))


class TestAcceptanceCriteriaRules:
    def test_missing_max_drawdown_pct_triggers_e021(self) -> None:
        spec = _base_spec()
        del spec["acceptance_criteria"]["max_drawdown_pct"]
        assert "SVR-E021" in error_codes(validate_spec(spec))

    def test_zero_max_drawdown_pct_triggers_e022(self) -> None:
        spec = _base_spec()
        spec["acceptance_criteria"]["max_drawdown_pct"] = 0
        assert "SVR-E022" in error_codes(validate_spec(spec))

    def test_negative_max_drawdown_pct_triggers_e022(self) -> None:
        spec = _base_spec()
        spec["acceptance_criteria"]["max_drawdown_pct"] = -5
        assert "SVR-E022" in error_codes(validate_spec(spec))

    def test_missing_min_sharpe_triggers_e046(self) -> None:
        spec = _base_spec()
        del spec["acceptance_criteria"]["min_sharpe_ratio"]
        assert "SVR-E046" in error_codes(validate_spec(spec))

    def test_zero_min_sharpe_triggers_e047(self) -> None:
        spec = _base_spec()
        spec["acceptance_criteria"]["min_sharpe_ratio"] = 0
        assert "SVR-E047" in error_codes(validate_spec(spec))

    def test_missing_min_profit_factor_triggers_e048(self) -> None:
        spec = _base_spec()
        del spec["acceptance_criteria"]["min_profit_factor"]
        assert "SVR-E048" in error_codes(validate_spec(spec))

    def test_zero_min_profit_factor_triggers_e049(self) -> None:
        spec = _base_spec()
        spec["acceptance_criteria"]["min_profit_factor"] = 0
        assert "SVR-E049" in error_codes(validate_spec(spec))

    def test_missing_min_trades_triggers_e050(self) -> None:
        spec = _base_spec()
        del spec["acceptance_criteria"]["min_trades"]
        assert "SVR-E050" in error_codes(validate_spec(spec))

    def test_zero_min_trades_triggers_e051(self) -> None:
        spec = _base_spec()
        spec["acceptance_criteria"]["min_trades"] = 0
        assert "SVR-E051" in error_codes(validate_spec(spec))

    def test_negative_min_trades_triggers_e051(self) -> None:
        spec = _base_spec()
        spec["acceptance_criteria"]["min_trades"] = -10
        assert "SVR-E051" in error_codes(validate_spec(spec))

    def test_missing_min_cagr_triggers_w040(self) -> None:
        spec = _base_spec()
        del spec["acceptance_criteria"]["min_cagr"]
        assert "SVR-W040" in warning_codes(validate_spec(spec))

    def test_present_min_cagr_no_w040(self) -> None:
        spec = _base_spec()
        spec["acceptance_criteria"]["min_cagr"] = 10.0
        assert "SVR-W040" not in warning_codes(validate_spec(spec))

    def test_missing_acceptance_criteria_section_all_errors(self) -> None:
        spec = _base_spec()
        del spec["acceptance_criteria"]
        found = error_codes(validate_spec(spec))
        assert "SVR-E021" in found
        assert "SVR-E046" in found
        assert "SVR-E048" in found
        assert "SVR-E050" in found


class TestAssumptionsRules:
    def test_missing_assumptions_section_triggers_w051(self) -> None:
        spec = _base_spec()
        del spec["assumptions"]
        assert "SVR-W051" in warning_codes(validate_spec(spec))

    def test_present_assumptions_no_w051(self) -> None:
        spec = _base_spec()
        assert "SVR-W051" not in warning_codes(validate_spec(spec))

    def test_day_trade_missing_fees_triggers_w010(self) -> None:
        spec = _base_spec()
        spec["metadata"]["trading_style"] = "day_trade"
        spec["constraints"] = {"max_holding_minutes": 60, "close_eod": True}
        spec["data"]["resolution"] = "minute"
        del spec["assumptions"]["fees"]
        assert "SVR-W010" in warning_codes(validate_spec(spec))

    def test_day_trade_with_fees_no_w010(self) -> None:
        spec = _base_spec()
        spec["metadata"]["trading_style"] = "day_trade"
        spec["constraints"] = {"max_holding_minutes": 60, "close_eod": True}
        spec["data"]["resolution"] = "minute"
        spec["assumptions"]["fees"] = 0.001
        assert "SVR-W010" not in warning_codes(validate_spec(spec))

    def test_day_trade_zero_slippage_triggers_w011(self) -> None:
        spec = _base_spec()
        spec["metadata"]["trading_style"] = "day_trade"
        spec["constraints"] = {"max_holding_minutes": 60, "close_eod": True}
        spec["data"]["resolution"] = "minute"
        spec["assumptions"]["slippage"] = 0
        assert "SVR-W011" in warning_codes(validate_spec(spec))

    def test_day_trade_nonzero_slippage_no_w011(self) -> None:
        spec = _base_spec()
        spec["metadata"]["trading_style"] = "day_trade"
        spec["constraints"] = {"max_holding_minutes": 60, "close_eod": True}
        spec["data"]["resolution"] = "minute"
        spec["assumptions"]["slippage"] = 0.0005
        assert "SVR-W011" not in warning_codes(validate_spec(spec))

    def test_swing_strategy_no_day_trade_assumption_checks(self) -> None:
        spec = _base_spec()  # swing
        spec["assumptions"] = {}  # no fees or slippage
        assert "SVR-W010" not in warning_codes(validate_spec(spec))
        assert "SVR-W011" not in warning_codes(validate_spec(spec))


class TestGeneralRules:
    def test_very_short_spec_triggers_w061(self) -> None:
        # A minimal spec that will produce a very small YAML dump.
        spec: dict[str, Any] = {
            "metadata": {"trading_style": "swing", "name": "X"},
            "capital": {"allocation_usd": 1},
        }
        assert "SVR-W061" in warning_codes(validate_spec(spec))

    def test_non_dict_spec_triggers_e000(self) -> None:
        found = validate_spec("not a dict")  # type: ignore[arg-type]
        assert any(f["code"] == "SVR-E000" for f in found)

    def test_none_spec_triggers_e000(self) -> None:
        found = validate_spec(None)  # type: ignore[arg-type]
        assert any(f["code"] == "SVR-E000" for f in found)


# ---------------------------------------------------------------------------
# build_summary tests
# ---------------------------------------------------------------------------


class TestBuildSummary:
    def test_summary_structure_on_pass(self) -> None:
        summary = build_summary("test.yaml", [])
        assert summary["result"] == "PASS"
        assert summary["valid"] is True
        assert summary["error_count"] == 0
        assert summary["warning_count"] == 0
        assert summary["findings"] == []
        assert summary["errors"] == []
        assert summary["warnings"] == []
        assert summary["spec_file"] == "test.yaml"

    def test_summary_result_fail_when_errors_present(self) -> None:
        findings = [{"code": "SVR-E001", "severity": "ERROR", "message": "missing trading_style", "field": "metadata.trading_style"}]
        summary = build_summary("bad.yaml", findings)
        assert summary["result"] == "FAIL"
        assert summary["valid"] is False
        assert summary["error_count"] == 1
        assert summary["warning_count"] == 0

    def test_summary_result_pass_when_only_warnings(self) -> None:
        findings = [{"code": "SVR-W001", "severity": "WARNING", "message": "missing description", "field": "metadata.description"}]
        summary = build_summary("warn.yaml", findings)
        assert summary["result"] == "PASS"
        assert summary["valid"] is True
        assert summary["warning_count"] == 1
        assert summary["error_count"] == 0

    def test_summary_errors_list_contains_messages(self) -> None:
        findings = [
            {"code": "SVR-E001", "severity": "ERROR", "message": "err msg", "field": ""},
            {"code": "SVR-W001", "severity": "WARNING", "message": "warn msg", "field": ""},
        ]
        summary = build_summary("test.yaml", findings)
        assert "err msg" in summary["errors"]
        assert "warn msg" in summary["warnings"]


# ---------------------------------------------------------------------------
# _sort_findings tests
# ---------------------------------------------------------------------------


class TestSortFindings:
    def test_errors_before_warnings(self) -> None:
        findings = [
            {"code": "SVR-W001", "severity": "WARNING", "message": "", "field": ""},
            {"code": "SVR-E001", "severity": "ERROR", "message": "", "field": ""},
        ]
        sorted_ = _sort_findings(findings)
        assert sorted_[0]["severity"] == "ERROR"
        assert sorted_[1]["severity"] == "WARNING"

    def test_codes_sorted_within_group(self) -> None:
        findings = [
            {"code": "SVR-E003", "severity": "ERROR", "message": "", "field": ""},
            {"code": "SVR-E001", "severity": "ERROR", "message": "", "field": ""},
            {"code": "SVR-E002", "severity": "ERROR", "message": "", "field": ""},
        ]
        sorted_ = _sort_findings(findings)
        assert [f["code"] for f in sorted_] == ["SVR-E001", "SVR-E002", "SVR-E003"]


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
        assert "error_count" in parsed
        assert "warning_count" in parsed

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
        assert "SVR-E025" in error_found_codes

    def test_findings_sorted_errors_before_warnings(self) -> None:
        result = self._run(str(CORPUS_DIR / "invalid_003_ambiguous_signals.yaml"))
        parsed = json.loads(result.stdout)
        findings = parsed["findings"]
        seen_warning = False
        for f in findings:
            if f["severity"] == "WARNING":
                seen_warning = True
            if seen_warning and f["severity"] == "ERROR":
                pytest.fail("An ERROR appeared after a WARNING in the findings list")

    def test_spec_flag_equivalent_to_positional(self) -> None:
        path = str(CORPUS_DIR / "valid_002.yaml")
        result_pos = self._run(path)
        result_flag = self._run("--spec", path)
        assert result_pos.returncode == result_flag.returncode == 0
