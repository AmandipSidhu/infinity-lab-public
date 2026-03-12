"""Tests for scripts/prompt_template.py — reference injection and prompt building."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure scripts/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from prompt_template import _load_reference, build_strategy_prompt  # noqa: E402


# ---------------------------------------------------------------------------
# Minimal spec fixtures
# ---------------------------------------------------------------------------

VWAP_MES_SPEC = {
    "metadata": {"name": "vwap_probe", "trading_style": "momentum"},
    "data": {
        "instruments": ["MES"],
        "resolution": "minute",
        "start_date": "2022-01-01",
        "end_date": "2023-01-01",
    },
    "capital": {"allocation_usd": 25000},
    "signals": {
        "entry": ["Price crosses above VWAP"],
        "exit": ["Price crosses below VWAP"],
    },
    "risk_management": {"max_position_size": 0.1},
}

VWAP_SIGNAL_ONLY_SPEC = {
    "metadata": {"name": "vwap_spy", "trading_style": "momentum"},
    "data": {
        "instruments": ["SPY"],
        "resolution": "minute",
        "start_date": "2022-01-01",
        "end_date": "2023-01-01",
    },
    "capital": {"allocation_usd": 10000},
    "signals": {
        "entry": ["Price crosses above VWAP(daily)"],
        "exit": ["Price crosses below EMA(20)"],
    },
}

SMA_SPEC = {
    "metadata": {"name": "sma_cross", "trading_style": "trend_following"},
    "data": {
        "instruments": ["SPY"],
        "resolution": "daily",
        "start_date": "2020-01-01",
        "end_date": "2024-01-01",
    },
    "capital": {"allocation_usd": 100000},
    "signals": {
        "entry": ["SMA(10) crosses above SMA(50)"],
        "exit": ["SMA(10) crosses below SMA(50)"],
    },
}

EMA_SPEC = {
    "metadata": {"name": "ema_cross", "trading_style": "trend_following"},
    "data": {
        "instruments": ["QQQ"],
        "resolution": "daily",
        "start_date": "2020-01-01",
        "end_date": "2024-01-01",
    },
    "capital": {"allocation_usd": 50000},
    "signals": {
        "entry": ["EMA(9) crosses above EMA(21)"],
        "exit": ["EMA(9) crosses below EMA(21)"],
    },
}

MEAN_REVERSION_SPEC = {
    "metadata": {"name": "mean_rev_multi", "trading_style": "mean_reversion"},
    "data": {
        "instruments": ["SPY", "QQQ", "IWM"],
        "resolution": "daily",
        "start_date": "2020-01-01",
        "end_date": "2024-01-01",
    },
    "capital": {"allocation_usd": 100000},
    "signals": {
        "entry": ["Z-score < -1.5"],
        "exit": ["Z-score > 0"],
    },
}

MEAN_REVERSION_SINGLE_SPEC = {
    "metadata": {"name": "mean_rev_single", "trading_style": "mean_reversion"},
    "data": {
        "instruments": ["SPY"],
        "resolution": "daily",
        "start_date": "2020-01-01",
        "end_date": "2024-01-01",
    },
    "capital": {"allocation_usd": 100000},
    "signals": {
        "entry": ["RSI < 30"],
        "exit": ["RSI > 70"],
    },
}

DEFAULT_SPEC = {
    "metadata": {"name": "default_strat", "trading_style": "unknown"},
    "data": {
        "instruments": ["AAPL"],
        "resolution": "daily",
        "start_date": "2020-01-01",
        "end_date": "2024-01-01",
    },
    "capital": {"allocation_usd": 10000},
    "signals": {"entry": ["Breakout"], "exit": ["Breakdown"]},
}


# ---------------------------------------------------------------------------
# _load_reference — file-selection logic
# ---------------------------------------------------------------------------


class TestLoadReferenceSelection:
    def test_mes_futures_selects_vwap(self) -> None:
        content = _load_reference(VWAP_MES_SPEC)
        assert content is not None
        assert "VWAPCrossover" in content or "vwap" in content.lower() or "VWAP" in content

    def test_es_futures_selects_vwap(self) -> None:
        spec = {**VWAP_MES_SPEC, "data": {**VWAP_MES_SPEC["data"], "instruments": ["ES"]}}
        content = _load_reference(spec)
        assert content is not None
        # vwap_ema_crossover.py is the expected file
        assert "VWAPCrossover" in content or "VWAP" in content

    def test_vwap_in_signals_selects_vwap(self) -> None:
        content = _load_reference(VWAP_SIGNAL_ONLY_SPEC)
        assert content is not None
        assert "VWAP" in content

    def test_sma_signals_selects_sma_robust(self) -> None:
        content = _load_reference(SMA_SPEC)
        assert content is not None
        # sma_crossover_robust.py should contain robust-specific markers
        assert content is not None
        # The robust file is distinct from the simple one
        robust_path = (
            Path(__file__).parent.parent / "strategies" / "reference" / "sma_crossover_robust.py"
        )
        expected = robust_path.read_text(encoding="utf-8")
        assert content == expected

    def test_ema_signals_selects_sma_robust(self) -> None:
        content = _load_reference(EMA_SPEC)
        assert content is not None
        robust_path = (
            Path(__file__).parent.parent / "strategies" / "reference" / "sma_crossover_robust.py"
        )
        expected = robust_path.read_text(encoding="utf-8")
        assert content == expected

    def test_mean_reversion_multi_selects_mean_reversion(self) -> None:
        content = _load_reference(MEAN_REVERSION_SPEC)
        assert content is not None
        mr_path = (
            Path(__file__).parent.parent
            / "strategies"
            / "reference"
            / "mean_reversion_multi_asset.py"
        )
        expected = mr_path.read_text(encoding="utf-8")
        assert content == expected

    def test_mean_reversion_single_instrument_falls_back_to_default(self) -> None:
        # single instrument mean reversion → default (sma_crossover_simple.py)
        content = _load_reference(MEAN_REVERSION_SINGLE_SPEC)
        assert content is not None
        simple_path = (
            Path(__file__).parent.parent / "strategies" / "reference" / "sma_crossover_simple.py"
        )
        expected = simple_path.read_text(encoding="utf-8")
        assert content == expected

    def test_default_fallback_selects_sma_simple(self) -> None:
        content = _load_reference(DEFAULT_SPEC)
        assert content is not None
        simple_path = (
            Path(__file__).parent.parent / "strategies" / "reference" / "sma_crossover_simple.py"
        )
        expected = simple_path.read_text(encoding="utf-8")
        assert content == expected


class TestLoadReferenceMissingFile:
    def test_returns_none_and_warns_on_oserror(self, capsys: pytest.CaptureFixture) -> None:
        """_load_reference returns None and logs a warning when the file cannot be read."""
        with patch("prompt_template.Path.read_text", side_effect=OSError("simulated missing")):
            result = _load_reference(DEFAULT_SPEC)
        assert result is None
        captured = capsys.readouterr()
        assert "WARNING" in captured.err

    def test_no_exception_on_missing_file(self) -> None:
        """_load_reference must not raise even when the reference file is absent."""
        with patch("prompt_template.Path.read_text", side_effect=OSError("simulated missing")):
            result = _load_reference(DEFAULT_SPEC)
        assert result is None


# ---------------------------------------------------------------------------
# build_strategy_prompt — reference section injection
# ---------------------------------------------------------------------------


class TestBuildStrategyPromptReferenceInjection:
    def test_vwap_spec_contains_reference_block(self) -> None:
        prompt = build_strategy_prompt(VWAP_MES_SPEC)
        assert "=== REFERENCE IMPLEMENTATION ===" in prompt
        assert "=== END REFERENCE ===" in prompt

    def test_reference_block_before_requirements(self) -> None:
        prompt = build_strategy_prompt(VWAP_MES_SPEC)
        ref_pos = prompt.index("=== REFERENCE IMPLEMENTATION ===")
        req_pos = prompt.index("=== REQUIREMENTS ===")
        assert ref_pos < req_pos

    def test_reference_block_after_risk_management(self) -> None:
        prompt = build_strategy_prompt(VWAP_MES_SPEC)
        risk_pos = prompt.index("=== RISK MANAGEMENT ===")
        ref_pos = prompt.index("=== REFERENCE IMPLEMENTATION ===")
        assert risk_pos < ref_pos

    def test_reference_contains_template_instruction(self) -> None:
        prompt = build_strategy_prompt(VWAP_MES_SPEC)
        assert "structural template" in prompt
        assert "WORKING, PROVEN" in prompt

    def test_sma_spec_contains_reference_block(self) -> None:
        prompt = build_strategy_prompt(SMA_SPEC)
        assert "=== REFERENCE IMPLEMENTATION ===" in prompt

    def test_reference_omitted_when_load_returns_none(self) -> None:
        with patch("prompt_template.Path.read_text", side_effect=OSError("simulated missing")):
            prompt = build_strategy_prompt(DEFAULT_SPEC)
        assert "=== REFERENCE IMPLEMENTATION ===" not in prompt
        assert "=== END REFERENCE ===" not in prompt

    def test_existing_prompt_structure_preserved(self) -> None:
        """All original prompt sections must still be present."""
        prompt = build_strategy_prompt(SMA_SPEC)
        for section in [
            "=== INSTRUMENTS ===",
            "=== DATA RESOLUTION ===",
            "=== BACKTEST PERIOD ===",
            "=== ENTRY SIGNALS ===",
            "=== EXIT SIGNALS ===",
            "=== RISK MANAGEMENT ===",
            "=== REQUIREMENTS ===",
        ]:
            assert section in prompt, f"Missing section: {section}"

    def test_feedback_section_still_works(self) -> None:
        prompt = build_strategy_prompt(SMA_SPEC, feedback="SyntaxError on line 5")
        assert "PREVIOUS ATTEMPT FAILED" in prompt
        assert "SyntaxError on line 5" in prompt
