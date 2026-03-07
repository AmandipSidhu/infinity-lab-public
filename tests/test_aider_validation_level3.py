"""Tests for scripts/run_aider_validation_level3.py.

Covers:
- run_validation(dry_run=True) produces correct iteration log structure
- Convergence detection logic (monotonic sharpe progression)
- Per-tier result JSON files written to /tmp
- CLI entry point: exit code 0 on pass, 1 on fail
- Tier strategy files exist in strategies/generated/
- Strategy files are valid Python (compile without syntax errors)
- Each tier file contains the expected class name
- main() with --dry-run flag
"""

from __future__ import annotations

import ast
import importlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure scripts/ is on sys.path so the module can be imported
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_aider_validation_level3 as lvl3  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Strategy file presence and validity
# ---------------------------------------------------------------------------


class TestStrategyFiles:
    """The 4 tier strategy files must exist and be valid Python."""

    @pytest.mark.parametrize("tier", [1, 2, 3, 4])
    def test_file_exists(self, tier: int) -> None:
        path = lvl3.TIER_FILES[tier]
        assert path.exists(), f"Missing: {path}"

    @pytest.mark.parametrize("tier", [1, 2, 3, 4])
    def test_file_is_valid_python(self, tier: int) -> None:
        source = lvl3.TIER_FILES[tier].read_text(encoding="utf-8")
        try:
            ast.parse(source)
        except SyntaxError as exc:
            pytest.fail(f"Tier {tier} syntax error: {exc}")

    @pytest.mark.parametrize(
        "tier,expected_class",
        [
            (1, "MeanReversionMultiAssetTier1"),
            (2, "MeanReversionMultiAssetTier2"),
            (3, "MeanReversionMultiAssetTier3"),
            (4, "MeanReversionMultiAssetTier4"),
        ],
    )
    def test_class_name_present(self, tier: int, expected_class: str) -> None:
        source = lvl3.TIER_FILES[tier].read_text(encoding="utf-8")
        assert expected_class in source, (
            f"Expected class {expected_class!r} not found in tier {tier} file"
        )

    @pytest.mark.parametrize("tier", [1, 2, 3, 4])
    def test_uses_insight_weighting(self, tier: int) -> None:
        source = lvl3.TIER_FILES[tier].read_text(encoding="utf-8")
        assert "InsightWeightingPortfolioConstructionModel" in source

    @pytest.mark.parametrize("tier", [1, 2, 3, 4])
    def test_assets_count(self, tier: int) -> None:
        """Each tier file must reference all 19 Treasury ETF tickers."""
        source = lvl3.TIER_FILES[tier].read_text(encoding="utf-8")
        expected_tickers = [
            "SHY", "TLT", "IEI", "SHV", "TLH", "EDV", "BIL",
            "SPTL", "TBT", "TMF", "TMV", "TBF", "VGSH",
            "VGIT", "VGLT", "SCHO", "SCHR", "SPTS", "GOVT",
        ]
        assert len(expected_tickers) == 19, "Sanity-check: list must have exactly 19 tickers"
        for ticker in expected_tickers:
            assert ticker in source, f"Tier {tier} missing ticker {ticker!r}"


# ---------------------------------------------------------------------------
# run_validation() — dry-run
# ---------------------------------------------------------------------------


class TestRunValidationDryRun:
    def test_returns_dict_with_required_keys(self) -> None:
        log = lvl3.run_validation(dry_run=True)
        for key in ("level", "tiers", "convergence", "passed", "final_code_path"):
            assert key in log, f"Missing key {key!r} in log"

    def test_level_is_3(self) -> None:
        log = lvl3.run_validation(dry_run=True)
        assert log["level"] == 3

    def test_passes_within_4_tiers(self) -> None:
        log = lvl3.run_validation(dry_run=True)
        assert log["passed"] is True

    def test_final_code_path_is_tier2(self) -> None:
        log = lvl3.run_validation(dry_run=True)
        # Dry-run synthetic sharpe: tier1=0.52 (fail), tier2=0.83 (pass)
        # → first passing tier is 2
        assert log["final_code_path"] is not None
        assert "aider_level3_tier2" in log["final_code_path"]

    def test_tiers_have_required_fields(self) -> None:
        log = lvl3.run_validation(dry_run=True)
        for tier_entry in log["tiers"]:
            for field in ("tier", "strategy_file", "sharpe", "passed"):
                assert field in tier_entry, f"Missing field {field!r} in tier entry"

    def test_convergence_is_bool(self) -> None:
        log = lvl3.run_validation(dry_run=True)
        assert isinstance(log["convergence"], bool)

    def test_iteration_log_written_to_tmp(self) -> None:
        lvl3.run_validation(dry_run=True)
        log_path = Path("/tmp/aider_iteration_log_level3.json")
        assert log_path.exists()
        data = json.loads(log_path.read_text(encoding="utf-8"))
        assert data["level"] == 3

    def test_per_tier_result_files_written(self) -> None:
        log = lvl3.run_validation(dry_run=True)
        tiers_run = len(log["tiers"])
        for tier_num in range(1, tiers_run + 1):
            result_path = Path(f"/tmp/aider_level3_tier{tier_num}_result.json")
            assert result_path.exists(), f"Missing result file for tier {tier_num}"
            data = json.loads(result_path.read_text(encoding="utf-8"))
            assert "sharpe_ratio" in data

    def test_capability_ceiling_analysis_present(self) -> None:
        log = lvl3.run_validation(dry_run=True)
        assert "capability_ceiling_analysis" in log
        assert len(log["capability_ceiling_analysis"]) > 20


# ---------------------------------------------------------------------------
# run_validation() — fallback when qc_rest_client absent
# ---------------------------------------------------------------------------


class TestRunValidationFallback:
    def test_falls_back_to_dry_run_when_no_client(self) -> None:
        """If qc_rest_client is missing, the script should not raise an error."""
        with patch.dict(sys.modules, {"qc_rest_client": None}):
            # dry_run=False but client unavailable → auto-fallback
            log = lvl3.run_validation(dry_run=False)
        assert log["level"] == 3


# ---------------------------------------------------------------------------
# Convergence helper
# ---------------------------------------------------------------------------


class TestIsPass:
    @pytest.mark.parametrize(
        "sharpe,expected",
        [
            (0.6, True),   # lower bound (inclusive)
            (1.2, True),   # target
            (1.8, True),   # upper bound (inclusive)
            (0.59, False),
            (1.81, False),
            (0.0, False),
        ],
    )
    def test_is_pass(self, sharpe: float, expected: bool) -> None:
        assert lvl3._is_pass(sharpe) is expected


# ---------------------------------------------------------------------------
# CLI — main()
# ---------------------------------------------------------------------------


class TestMain:
    def test_dry_run_flag_exits_0(self) -> None:
        rc = lvl3.main(["--dry-run"])
        assert rc == 0

    def test_no_args_falls_back_to_dry_run(self) -> None:
        """Without QC client, main() auto-falls back and should not raise."""
        with patch.dict(sys.modules, {"qc_rest_client": None}):
            rc = lvl3.main([])
        assert isinstance(rc, int)

    def test_help_does_not_raise(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            lvl3.main(["--help"])
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Failure path
# ---------------------------------------------------------------------------


class TestFailurePath:
    def test_all_tiers_fail_returns_pass_false(self) -> None:
        """Patch synthetic results so all Sharpe values are below the band."""
        bad_results = {n: {"sharpe_ratio": 0.1, "source": "dry_run"} for n in range(1, 5)}
        with patch.object(lvl3, "_SYNTHETIC_RESULTS", bad_results):
            log = lvl3.run_validation(dry_run=True)
        assert log["passed"] is False
        assert log["final_code_path"] is None

    def test_all_tiers_fail_ceiling_analysis_mentions_failure(self) -> None:
        bad_results = {n: {"sharpe_ratio": 0.1, "source": "dry_run"} for n in range(1, 5)}
        with patch.object(lvl3, "_SYNTHETIC_RESULTS", bad_results):
            log = lvl3.run_validation(dry_run=True)
        assert "FAILED" in log["capability_ceiling_analysis"]

    def test_all_tiers_fail_main_exits_1(self) -> None:
        bad_results = {n: {"sharpe_ratio": 0.1, "source": "dry_run"} for n in range(1, 5)}
        with patch.object(lvl3, "_SYNTHETIC_RESULTS", bad_results):
            rc = lvl3.main(["--dry-run"])
        assert rc == 1
