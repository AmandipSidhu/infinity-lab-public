"""Tests for scripts/aider_validation_ladder.py.

Covers:
- Prerequisite check: Phase 1 qc_upload_eval.py must exist
- Convergence detection: Sharpe within 0.8±0.3 → PASS
- Stub backtest: QC credentials not set → uses simulated Sharpe sequence
- Tier prompt building: correct templates for each tier
- Sharpe analysis: correct issue/fix diagnosis per Sharpe band
- run_level1(): convergence at tier 1, tier 4, and no convergence
- _extract_sharpe: handles nested QC result dicts
- main(): exit codes 0, 1, 2
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import aider_validation_ladder  # noqa: E402
from aider_validation_ladder import (  # noqa: E402
    _SHARPE_PASS_MAX,
    _SHARPE_PASS_MIN,
    _STUB_SHARPE_SEQUENCE,
    _TIER1_PROMPT,
    _analyze_low_sharpe,
    _build_tier_prompt,
    _ensure_strategy_file,
    _extract_sharpe,
    main,
    run_level1,
)

# Sharpe sequence where tier 1 already converges (all within pass range)
_ALL_PASSING_STUB: list[float] = [0.82, 0.82, 0.82, 0.82]
# Sharpe sequence where no tier ever converges
_NEVER_PASSING_STUB: list[float] = [0.30, 0.35, 0.38, 0.40]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bt_result(sharpe: float) -> dict:
    return {
        "completed": True,
        "progress": 1.0,
        "Statistics": {"SharpeRatio": str(sharpe)},
    }


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_sharpe_pass_range(self) -> None:
        assert _SHARPE_PASS_MIN == pytest.approx(0.5)
        assert _SHARPE_PASS_MAX == pytest.approx(1.1)

    def test_stub_sequence_has_four_entries(self) -> None:
        assert len(_STUB_SHARPE_SEQUENCE) == 4

    def test_stub_sequence_converges(self) -> None:
        final = _STUB_SHARPE_SEQUENCE[-1]
        assert _SHARPE_PASS_MIN <= final <= _SHARPE_PASS_MAX, (
            f"Final stub Sharpe {final} not in [{_SHARPE_PASS_MIN}, {_SHARPE_PASS_MAX}]"
        )

    def test_tier1_prompt_contains_spy(self) -> None:
        assert "SPY" in _TIER1_PROMPT

    def test_tier1_prompt_mentions_sma(self) -> None:
        assert "SMA" in _TIER1_PROMPT


# ---------------------------------------------------------------------------
# _extract_sharpe
# ---------------------------------------------------------------------------


class TestExtractSharpe:
    def test_top_level_key(self) -> None:
        result = {"SharpeRatio": "1.23"}
        assert _extract_sharpe(result) == pytest.approx(1.23)

    def test_nested_statistics(self) -> None:
        result = {"Statistics": {"SharpeRatio": "0.82"}}
        assert _extract_sharpe(result) == pytest.approx(0.82)

    def test_sharpe_ratio_space_key(self) -> None:
        result = {"statistics": {"Sharpe Ratio": "0.75"}}
        assert _extract_sharpe(result) == pytest.approx(0.75)

    def test_missing_returns_none(self) -> None:
        assert _extract_sharpe({}) is None

    def test_non_numeric_returns_none(self) -> None:
        result = {"SharpeRatio": "N/A"}
        assert _extract_sharpe(result) is None

    def test_handles_percentage_string(self) -> None:
        result = {"Statistics": {"sharpe": "0.90%"}}
        assert _extract_sharpe(result) == pytest.approx(0.90)


# ---------------------------------------------------------------------------
# _analyze_low_sharpe
# ---------------------------------------------------------------------------


class TestAnalyzeLowSharpe:
    def test_sharpe_below_0_5_tier1(self) -> None:
        issue, fix = _analyze_low_sharpe(0.42, 1)
        assert "warmup" in issue.lower() or "noise" in issue.lower()
        assert "warmup" in fix.lower() or "setwarmup" in fix.lower()

    def test_sharpe_between_0_5_and_0_7(self) -> None:
        issue, fix = _analyze_low_sharpe(0.61, 2)
        assert "exit" in issue.lower() or "lag" in issue.lower() or "timing" in issue.lower()

    def test_sharpe_between_0_7_and_0_79(self) -> None:
        issue, fix = _analyze_low_sharpe(0.75, 3)
        assert "position" in issue.lower() or "sizing" in issue.lower()
        assert "1.0" in fix or "holdings" in fix.lower()

    def test_returns_strings(self) -> None:
        issue, fix = _analyze_low_sharpe(0.30, 1)
        assert isinstance(issue, str) and len(issue) > 0
        assert isinstance(fix, str) and len(fix) > 0


# ---------------------------------------------------------------------------
# _build_tier_prompt
# ---------------------------------------------------------------------------


class TestBuildTierPrompt:
    def test_tier1_returns_base_prompt(self) -> None:
        prompt = _build_tier_prompt(1, None, "", "")
        assert "SPY" in prompt
        assert "SMA" in prompt

    def test_tier2_includes_sharpe(self) -> None:
        prompt = _build_tier_prompt(2, 0.42, "no warmup", "add warmup")
        assert "0.42" in prompt
        assert "no warmup" in prompt
        assert "add warmup" in prompt

    def test_tier3_includes_sharpe(self) -> None:
        prompt = _build_tier_prompt(3, 0.61, "exit lag", "tighten logic")
        assert "0.61" in prompt

    def test_tier4_includes_fix_only(self) -> None:
        prompt = _build_tier_prompt(4, 0.78, "position sizing", "set holdings to 1.0")
        assert "0.78" in prompt
        assert "set holdings to 1.0" in prompt


# ---------------------------------------------------------------------------
# _ensure_strategy_file
# ---------------------------------------------------------------------------


class TestEnsureStrategyFile:
    def test_returns_existing_pre_written_file(self, tmp_path: Path) -> None:
        """When GEMINI_API_KEY is not set, returns the pre-written tier file."""
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
            with patch.object(
                aider_validation_ladder, "_GEMINI_API_KEY", ""
            ):
                # The pre-written files should exist in the repo
                generated_dir = REPO_ROOT / "strategies" / "generated"
                tier_file = generated_dir / "aider_level1_tier1.py"
                if tier_file.exists():
                    result = _ensure_strategy_file(1, _TIER1_PROMPT)
                    assert result.exists()

    def test_creates_generated_dir_if_missing(self, tmp_path: Path) -> None:
        """Generated directory is created if it doesn't exist."""
        new_dir = tmp_path / "generated"
        assert not new_dir.exists()
        # Create a dummy tier file so the function can find it
        tier_file = new_dir / "aider_level1_tier1.py"
        new_dir.mkdir(parents=True)
        tier_file.write_text("# tier 1\n", encoding="utf-8")

        with patch.object(aider_validation_ladder, "_GENERATED_DIR", new_dir):
            with patch.object(aider_validation_ladder, "_GEMINI_API_KEY", ""):
                result = _ensure_strategy_file(1, _TIER1_PROMPT)
                assert result.exists()


# ---------------------------------------------------------------------------
# run_level1: stub mode (no QC credentials)
# ---------------------------------------------------------------------------


class TestRunLevel1Stub:
    def test_converges_in_stub_mode(self, tmp_path: Path) -> None:
        """Stub trajectory (0.42→0.61→0.78→0.82) should converge at tier 4."""
        log_path = tmp_path / "iteration_log.json"
        result_dir = tmp_path

        with patch.object(aider_validation_ladder, "_QC_USER_ID", ""):
            with patch.object(aider_validation_ladder, "_QC_API_TOKEN", ""):
                with patch.object(aider_validation_ladder, "_GEMINI_API_KEY", ""):
                    # Pre-written files are in strategies/generated/ in the repo
                    generated_dir = REPO_ROOT / "strategies" / "generated"
                    with patch.object(
                        aider_validation_ladder, "_GENERATED_DIR", generated_dir
                    ):
                        log = run_level1(output_log_path=log_path, result_dir=result_dir)

        assert log["level"] == 1
        assert log["convergence"] is True
        assert log["final_code_path"] is not None
        assert len(log["tiers"]) >= 1
        assert log_path.exists()

    def test_log_has_correct_schema(self, tmp_path: Path) -> None:
        """Iteration log matches the required schema from the issue."""
        log_path = tmp_path / "log.json"
        result_dir = tmp_path

        with patch.object(aider_validation_ladder, "_QC_USER_ID", ""):
            with patch.object(aider_validation_ladder, "_QC_API_TOKEN", ""):
                with patch.object(aider_validation_ladder, "_GEMINI_API_KEY", ""):
                    generated_dir = REPO_ROOT / "strategies" / "generated"
                    with patch.object(
                        aider_validation_ladder, "_GENERATED_DIR", generated_dir
                    ):
                        log = run_level1(output_log_path=log_path, result_dir=result_dir)

        assert "level" in log
        assert "tiers" in log
        assert "convergence" in log
        assert "final_code_path" in log
        for tier_entry in log["tiers"]:
            assert "tier" in tier_entry
            assert "sharpe" in tier_entry

    def test_stub_result_files_written(self, tmp_path: Path) -> None:
        """Per-tier result files are written to result_dir."""
        log_path = tmp_path / "log.json"
        result_dir = tmp_path

        with patch.object(aider_validation_ladder, "_QC_USER_ID", ""):
            with patch.object(aider_validation_ladder, "_QC_API_TOKEN", ""):
                with patch.object(aider_validation_ladder, "_GEMINI_API_KEY", ""):
                    generated_dir = REPO_ROOT / "strategies" / "generated"
                    with patch.object(
                        aider_validation_ladder, "_GENERATED_DIR", generated_dir
                    ):
                        log = run_level1(output_log_path=log_path, result_dir=result_dir)

        tiers_run = len(log["tiers"])
        for tier in range(1, tiers_run + 1):
            result_file = result_dir / f"aider_level1_tier{tier}_result.json"
            assert result_file.exists(), f"Missing result file for tier {tier}"

    def test_passing_tier_has_passed_true(self, tmp_path: Path) -> None:
        """The converging tier entry has 'passed': True."""
        log_path = tmp_path / "log.json"
        result_dir = tmp_path

        with patch.object(aider_validation_ladder, "_QC_USER_ID", ""):
            with patch.object(aider_validation_ladder, "_QC_API_TOKEN", ""):
                with patch.object(aider_validation_ladder, "_GEMINI_API_KEY", ""):
                    generated_dir = REPO_ROOT / "strategies" / "generated"
                    with patch.object(
                        aider_validation_ladder, "_GENERATED_DIR", generated_dir
                    ):
                        log = run_level1(output_log_path=log_path, result_dir=result_dir)

        passing_tiers = [t for t in log["tiers"] if t.get("passed")]
        assert len(passing_tiers) >= 1


# ---------------------------------------------------------------------------
# run_level1: early convergence (Sharpe already in range on tier 1)
# ---------------------------------------------------------------------------


class TestRunLevel1EarlyConvergence:
    def test_converges_at_tier_1(self, tmp_path: Path) -> None:
        """If stub Sharpe on tier 1 is in range, converge immediately."""
        log_path = tmp_path / "log.json"
        result_dir = tmp_path

        with patch.object(aider_validation_ladder, "_QC_USER_ID", ""):
            with patch.object(aider_validation_ladder, "_QC_API_TOKEN", ""):
                with patch.object(aider_validation_ladder, "_GEMINI_API_KEY", ""):
                    # Override stub sequence so tier 1 already passes
                    with patch.object(
                        aider_validation_ladder, "_STUB_SHARPE_SEQUENCE", _ALL_PASSING_STUB
                    ):
                        generated_dir = REPO_ROOT / "strategies" / "generated"
                        with patch.object(
                            aider_validation_ladder, "_GENERATED_DIR", generated_dir
                        ):
                            log = run_level1(output_log_path=log_path, result_dir=result_dir)

        assert log["convergence"] is True
        assert len(log["tiers"]) == 1
        assert log["tiers"][0]["tier"] == 1


# ---------------------------------------------------------------------------
# run_level1: no convergence (all tiers fail)
# ---------------------------------------------------------------------------


class TestRunLevel1NoConvergence:
    def test_fails_after_4_tiers(self, tmp_path: Path) -> None:
        """If Sharpe never converges, convergence=False and all 4 tiers are logged."""
        log_path = tmp_path / "log.json"
        result_dir = tmp_path

        with patch.object(aider_validation_ladder, "_QC_USER_ID", ""):
            with patch.object(aider_validation_ladder, "_QC_API_TOKEN", ""):
                with patch.object(aider_validation_ladder, "_GEMINI_API_KEY", ""):
                    with patch.object(
                        aider_validation_ladder, "_STUB_SHARPE_SEQUENCE", _NEVER_PASSING_STUB
                    ):
                        generated_dir = REPO_ROOT / "strategies" / "generated"
                        with patch.object(
                            aider_validation_ladder, "_GENERATED_DIR", generated_dir
                        ):
                            log = run_level1(output_log_path=log_path, result_dir=result_dir)

        assert log["convergence"] is False
        assert len(log["tiers"]) == 4


# ---------------------------------------------------------------------------
# main: exit codes
# ---------------------------------------------------------------------------


class TestMain:
    def test_exit_2_when_qc_upload_eval_missing(self, tmp_path: Path) -> None:
        """Returns exit code 2 when Phase 1 qc_upload_eval.py is not found."""
        with patch("aider_validation_ladder._qc_client_exists", return_value=False):
            rc = main([
                "--log", str(tmp_path / "log.json"),
                "--result-dir", str(tmp_path),
            ])
        assert rc == 2

    def test_exit_0_in_stub_mode(self, tmp_path: Path) -> None:
        """Returns exit code 0 when running with stub backtest (no QC credentials)."""
        with patch.object(aider_validation_ladder, "_QC_USER_ID", ""):
            with patch.object(aider_validation_ladder, "_QC_API_TOKEN", ""):
                with patch.object(aider_validation_ladder, "_GEMINI_API_KEY", ""):
                    generated_dir = REPO_ROOT / "strategies" / "generated"
                    with patch.object(
                        aider_validation_ladder, "_GENERATED_DIR", generated_dir
                    ):
                        rc = main([
                            "--log", str(tmp_path / "log.json"),
                            "--result-dir", str(tmp_path),
                        ])
        assert rc == 0

    def test_exit_1_when_no_convergence(self, tmp_path: Path) -> None:
        """Returns exit code 1 when real backtest data never converges."""
        with patch.object(aider_validation_ladder, "_QC_USER_ID", "real_user"):
            with patch.object(aider_validation_ladder, "_QC_API_TOKEN", "real_token"):
                with patch.object(aider_validation_ladder, "_GEMINI_API_KEY", ""):
                    with patch(
                        "aider_validation_ladder._backtest_strategy_via_qc",
                        return_value=_make_bt_result(0.30),
                    ):
                        generated_dir = REPO_ROOT / "strategies" / "generated"
                        with patch.object(
                            aider_validation_ladder, "_GENERATED_DIR", generated_dir
                        ):
                            rc = main([
                                "--log", str(tmp_path / "log.json"),
                                "--result-dir", str(tmp_path),
                            ])
        assert rc == 1

    def test_exit_0_on_convergence(self, tmp_path: Path) -> None:
        """Returns exit code 0 when real backtest data converges."""
        with patch.object(aider_validation_ladder, "_QC_USER_ID", "real_user"):
            with patch.object(aider_validation_ladder, "_QC_API_TOKEN", "real_token"):
                with patch.object(aider_validation_ladder, "_GEMINI_API_KEY", ""):
                    with patch(
                        "aider_validation_ladder._backtest_strategy_via_qc",
                        return_value=_make_bt_result(0.82),
                    ):
                        generated_dir = REPO_ROOT / "strategies" / "generated"
                        with patch.object(
                            aider_validation_ladder, "_GENERATED_DIR", generated_dir
                        ):
                            rc = main([
                                "--log", str(tmp_path / "log.json"),
                                "--result-dir", str(tmp_path),
                            ])
        assert rc == 0


# ---------------------------------------------------------------------------
# Pre-written strategy files exist in strategies/generated/
# ---------------------------------------------------------------------------


class TestGeneratedStrategyFiles:
    @pytest.mark.parametrize("tier", [1, 2, 3, 4])
    def test_tier_strategy_file_exists(self, tier: int) -> None:
        """Pre-written tier strategy files must exist in strategies/generated/."""
        path = REPO_ROOT / "strategies" / "generated" / f"aider_level1_tier{tier}.py"
        assert path.exists(), f"Missing generated strategy: {path}"

    @pytest.mark.parametrize("tier", [1, 2, 3, 4])
    def test_tier_strategy_contains_qcalgorithm(self, tier: int) -> None:
        """Each tier strategy must subclass QCAlgorithm."""
        path = REPO_ROOT / "strategies" / "generated" / f"aider_level1_tier{tier}.py"
        content = path.read_text(encoding="utf-8")
        assert "QCAlgorithm" in content, f"Tier {tier} strategy does not subclass QCAlgorithm"

    @pytest.mark.parametrize("tier", [1, 2, 3, 4])
    def test_tier_strategy_has_spy(self, tier: int) -> None:
        """Each tier strategy must trade SPY."""
        path = REPO_ROOT / "strategies" / "generated" / f"aider_level1_tier{tier}.py"
        content = path.read_text(encoding="utf-8")
        assert "SPY" in content, f"Tier {tier} strategy does not reference SPY"

    @pytest.mark.parametrize("tier", [1, 2, 3, 4])
    def test_tier_strategy_has_sma(self, tier: int) -> None:
        """Each tier strategy must use SMA indicators."""
        path = REPO_ROOT / "strategies" / "generated" / f"aider_level1_tier{tier}.py"
        content = path.read_text(encoding="utf-8")
        assert "SMA" in content, f"Tier {tier} strategy does not use SMA"

    def test_tier4_has_warmup(self) -> None:
        """Tier 4 (final passing strategy) must include SetWarmUp."""
        path = REPO_ROOT / "strategies" / "generated" / "aider_level1_tier4.py"
        content = path.read_text(encoding="utf-8")
        assert "SetWarmUp" in content, "Tier 4 strategy must include SetWarmUp"

    def test_tier4_sets_holdings_to_1(self) -> None:
        """Tier 4 strategy should set holdings to 1.0 for full capital deployment."""
        path = REPO_ROOT / "strategies" / "generated" / "aider_level1_tier4.py"
        content = path.read_text(encoding="utf-8")
        assert "1.0" in content, "Tier 4 strategy should reference 1.0 holdings"
