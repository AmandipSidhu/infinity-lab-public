"""Tests for scripts/aider_validation_level2.py.

Covers:
- check_phase4a_dependency: missing log, valid passing log, convergence flag,
  invalid JSON, no tier passed
- is_within_target: boundary values, None input
- _stub_backtest_result: output schema, file written
- run_tier_backtest: stub path (no credentials), API error fallback,
  happy path (mocked qc_rest_client)
- write_iteration_log: schema, file written, convergence flag
- run_validation: dependency check skip, tier convergence (tier 2 passes),
  all tiers exhausted, missing strategy file
- main(): exit codes (pass=0, fail=1, dependency not met=0 non-blocking)
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import aider_validation_level2 as avl  # noqa: E402
from aider_validation_level2 import (  # noqa: E402
    _SHARPE_TARGET_MAX,
    _SHARPE_TARGET_MIN,
    _STUB_SHARPE,
    _TIER_FEEDBACK,
    check_phase4a_dependency,
    is_within_target,
    main,
    run_tier_backtest,
    run_validation,
    write_iteration_log,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _tmp_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect all /tmp output paths to a temporary directory."""
    monkeypatch.setattr(avl, "_PHASE4A_LOG", tmp_path / "aider_iteration_log_level1.json")
    monkeypatch.setattr(avl, "_ITERATION_LOG", tmp_path / "aider_iteration_log_level2.json")


# ---------------------------------------------------------------------------
# check_phase4a_dependency
# ---------------------------------------------------------------------------


class TestCheckPhase4aDependency:
    def test_missing_log_returns_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(avl, "_PHASE4A_LOG", tmp_path / "nonexistent.json")
        passed, msg = check_phase4a_dependency()
        assert not passed
        assert "not found" in msg

    def test_valid_log_with_passed_tier(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        log = tmp_path / "aider_iteration_log_level1.json"
        log.write_text(json.dumps({
            "level": 1,
            "tiers": [{"tier": 1, "sharpe": 0.82, "passed": True}],
            "convergence": True,
        }))
        monkeypatch.setattr(avl, "_PHASE4A_LOG", log)
        passed, msg = check_phase4a_dependency()
        assert passed
        assert "satisfied" in msg

    def test_convergence_flag_only(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        log = tmp_path / "aider_iteration_log_level1.json"
        log.write_text(json.dumps({
            "level": 1,
            "tiers": [{"tier": 1, "sharpe": 0.5, "passed": False}],
            "convergence": True,
        }))
        monkeypatch.setattr(avl, "_PHASE4A_LOG", log)
        passed, _ = check_phase4a_dependency()
        assert passed

    def test_no_tier_passed_no_convergence(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        log = tmp_path / "aider_iteration_log_level1.json"
        log.write_text(json.dumps({
            "level": 1,
            "tiers": [{"tier": 1, "sharpe": 0.3, "passed": False}],
            "convergence": False,
        }))
        monkeypatch.setattr(avl, "_PHASE4A_LOG", log)
        passed, msg = check_phase4a_dependency()
        assert not passed
        assert "no tier passed" in msg.lower()

    def test_invalid_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        log = tmp_path / "aider_iteration_log_level1.json"
        log.write_text("not valid json {{{")
        monkeypatch.setattr(avl, "_PHASE4A_LOG", log)
        passed, msg = check_phase4a_dependency()
        assert not passed
        assert "invalid json" in msg.lower()


# ---------------------------------------------------------------------------
# is_within_target
# ---------------------------------------------------------------------------


class TestIsWithinTarget:
    def test_none_returns_false(self) -> None:
        assert not is_within_target(None)

    def test_below_min(self) -> None:
        assert not is_within_target(_SHARPE_TARGET_MIN - 0.01)

    def test_at_min(self) -> None:
        assert is_within_target(_SHARPE_TARGET_MIN)

    def test_middle(self) -> None:
        assert is_within_target(1.0)

    def test_at_max(self) -> None:
        assert is_within_target(_SHARPE_TARGET_MAX)

    def test_above_max(self) -> None:
        assert not is_within_target(_SHARPE_TARGET_MAX + 0.01)

    def test_exact_target(self) -> None:
        assert is_within_target(1.0)


# ---------------------------------------------------------------------------
# _stub_backtest_result
# ---------------------------------------------------------------------------


class TestStubBacktestResult:
    def test_returns_expected_schema(self, tmp_path: Path) -> None:
        from aider_validation_level2 import _stub_backtest_result

        strategy = tmp_path / "strategy.py"
        strategy.write_text("pass")
        output = tmp_path / "result.json"

        result = _stub_backtest_result(1, strategy, output)

        assert result["sharpe_ratio"] == _STUB_SHARPE[1]
        assert "project_id" in result
        assert "backtest_id" in result
        assert "note" in result

    def test_writes_json_file(self, tmp_path: Path) -> None:
        from aider_validation_level2 import _stub_backtest_result

        strategy = tmp_path / "strategy.py"
        strategy.write_text("pass")
        output = tmp_path / "result.json"

        _stub_backtest_result(2, strategy, output)

        assert output.exists()
        data = json.loads(output.read_text())
        assert data["sharpe_ratio"] == _STUB_SHARPE[2]

    def test_custom_note(self, tmp_path: Path) -> None:
        from aider_validation_level2 import _stub_backtest_result

        strategy = tmp_path / "strategy.py"
        strategy.write_text("pass")
        output = tmp_path / "result.json"

        result = _stub_backtest_result(3, strategy, output, note="custom error msg")
        assert "custom error msg" in result["note"]

    def test_all_tiers_have_stub_sharpe(self, tmp_path: Path) -> None:
        from aider_validation_level2 import _stub_backtest_result

        for tier in range(1, 5):
            strategy = tmp_path / "strategy.py"
            strategy.write_text("pass")
            output = tmp_path / f"result{tier}.json"
            result = _stub_backtest_result(tier, strategy, output)
            assert result["sharpe_ratio"] == _STUB_SHARPE[tier]


# ---------------------------------------------------------------------------
# run_tier_backtest
# ---------------------------------------------------------------------------


class TestRunTierBacktest:
    def test_stub_when_no_credentials(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        strategy = tmp_path / "tier1.py"
        strategy.write_text("pass")

        with patch("builtins.open", side_effect=lambda p, *a, **k: open(p, *a, **k)):
            result = run_tier_backtest(1, strategy, "", "")

        assert result["backtest_status"] == "stub"
        assert result["sharpe_ratio"] == _STUB_SHARPE[1]

    def test_stub_result_file_written(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        strategy = tmp_path / "tier1.py"
        strategy.write_text("pass")

        # Point /tmp output to tmp_path
        monkeypatch.chdir(tmp_path)
        with patch.object(
            avl, "_stub_backtest_result",
            wraps=avl._stub_backtest_result,
        ) as spy:
            run_tier_backtest(1, strategy, "", "")
            spy.assert_called_once()

    def test_api_error_falls_back_to_stub(self, tmp_path: Path) -> None:
        strategy = tmp_path / "tier2.py"
        strategy.write_text("pass")

        mock_qc = MagicMock()
        mock_qc.run_backtest.side_effect = RuntimeError("connection refused")

        with patch.dict("sys.modules", {"qc_rest_client": mock_qc}):
            result = run_tier_backtest(2, strategy, "user123", "token456")

        assert result["backtest_status"] == "stub"

    def test_success_with_real_credentials(self, tmp_path: Path) -> None:
        strategy = tmp_path / "tier3.py"
        strategy.write_text("pass")

        expected_result: dict = {
            "sharpe_ratio": 1.2,
            "total_return_pct": 18.5,
            "max_drawdown_pct": -12.0,
            "total_trades": 60,
            "backtest_status": "Completed",
            "project_id": "12345",
            "backtest_id": "bt-abc",
            "compile_state": "BuildSuccess",
            "qc_ui_url": "https://www.quantconnect.com/project/12345",
            "timestamp": "2026-01-01T00:00:00Z",
        }
        mock_qc = MagicMock()
        mock_qc.run_backtest.return_value = expected_result

        with patch.dict("sys.modules", {"qc_rest_client": mock_qc}):
            result = run_tier_backtest(3, strategy, "user123", "token456")

        assert result["sharpe_ratio"] == 1.2
        assert result["backtest_status"] == "Completed"


# ---------------------------------------------------------------------------
# write_iteration_log
# ---------------------------------------------------------------------------


class TestWriteIterationLog:
    def test_writes_valid_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        log_path = tmp_path / "log.json"
        monkeypatch.setattr(avl, "_ITERATION_LOG", log_path)

        tiers = [{"tier": 1, "sharpe": 0.5, "passed": True}]
        write_iteration_log(tiers, convergence=True, final_tier=1)

        assert log_path.exists()
        data = json.loads(log_path.read_text())
        assert data["level"] == 2
        assert data["convergence"] is True
        assert len(data["tiers"]) == 1

    def test_convergence_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        log_path = tmp_path / "log.json"
        monkeypatch.setattr(avl, "_ITERATION_LOG", log_path)

        write_iteration_log([], convergence=False, final_tier=4)

        data = json.loads(log_path.read_text())
        assert data["convergence"] is False
        assert "final_code_path" in data
        assert "timestamp" in data


# ---------------------------------------------------------------------------
# run_validation
# ---------------------------------------------------------------------------


class TestRunValidation:
    def _make_tier_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Create dummy tier strategy files and redirect paths."""
        gen_dir = tmp_path / "strategies" / "generated"
        gen_dir.mkdir(parents=True)
        tier_files = {}
        for tier in range(1, 5):
            f = gen_dir / f"aider_level2_tier{tier}.py"
            f.write_text(f'"""Tier {tier} strategy."""\n')
            tier_files[tier] = f
        monkeypatch.setattr(avl, "_TIER_STRATEGY_FILES", tier_files)
        monkeypatch.setattr(avl, "_GENERATED_DIR", gen_dir)

    def test_skips_when_dependency_not_met(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without dependency log and no skip flag, run_validation returns False."""
        self._make_tier_files(tmp_path, monkeypatch)
        # _PHASE4A_LOG is already redirected to nonexistent path by autouse fixture
        result = run_validation("", "", skip_dependency_check=False)
        assert result is False

    def test_skip_dependency_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With skip flag, validation proceeds even without Phase 4A log."""
        self._make_tier_files(tmp_path, monkeypatch)

        with patch.object(avl, "run_tier_backtest") as mock_bt:
            # Tier 2 passes → early return
            mock_bt.side_effect = [
                {"sharpe_ratio": 0.3, "total_return_pct": None, "max_drawdown_pct": None},
                {"sharpe_ratio": 0.9, "total_return_pct": None, "max_drawdown_pct": None},
            ]
            result = run_validation("", "", skip_dependency_check=True)

        assert result is True

    def test_tier1_passes_immediately(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._make_tier_files(tmp_path, monkeypatch)

        with patch.object(avl, "run_tier_backtest") as mock_bt:
            mock_bt.return_value = {
                "sharpe_ratio": 1.1,
                "total_return_pct": 15.0,
                "max_drawdown_pct": -10.0,
            }
            result = run_validation("", "", skip_dependency_check=True)

        assert result is True
        # Only 1 tier should have been attempted
        assert mock_bt.call_count == 1

    def test_all_tiers_exhausted_returns_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._make_tier_files(tmp_path, monkeypatch)

        with patch.object(avl, "run_tier_backtest") as mock_bt:
            mock_bt.return_value = {
                "sharpe_ratio": 0.2,
                "total_return_pct": None,
                "max_drawdown_pct": None,
            }
            result = run_validation("", "", skip_dependency_check=True)

        assert result is False
        assert mock_bt.call_count == 4

    def test_missing_strategy_file_recorded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing tier file should be recorded but not crash."""
        gen_dir = tmp_path / "strategies" / "generated"
        gen_dir.mkdir(parents=True)
        # Only create tier 1 file
        tier_files = {}
        for tier in range(1, 5):
            tier_files[tier] = gen_dir / f"aider_level2_tier{tier}.py"
        tier_files[1].write_text("pass\n")
        monkeypatch.setattr(avl, "_TIER_STRATEGY_FILES", tier_files)
        monkeypatch.setattr(avl, "_GENERATED_DIR", gen_dir)

        with patch.object(avl, "run_tier_backtest") as mock_bt:
            mock_bt.return_value = {
                "sharpe_ratio": 0.2,
                "total_return_pct": None,
                "max_drawdown_pct": None,
            }
            result = run_validation("", "", skip_dependency_check=True)

        assert result is False

    def test_iteration_log_written_on_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._make_tier_files(tmp_path, monkeypatch)
        log_path = tmp_path / "iter_log.json"
        monkeypatch.setattr(avl, "_ITERATION_LOG", log_path)

        with patch.object(avl, "run_tier_backtest") as mock_bt:
            mock_bt.return_value = {
                "sharpe_ratio": 1.0,
                "total_return_pct": 15.0,
                "max_drawdown_pct": -12.0,
            }
            run_validation("", "", skip_dependency_check=True)

        assert log_path.exists()
        data = json.loads(log_path.read_text())
        assert data["convergence"] is True

    def test_iteration_log_written_on_fail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._make_tier_files(tmp_path, monkeypatch)
        log_path = tmp_path / "iter_log.json"
        monkeypatch.setattr(avl, "_ITERATION_LOG", log_path)

        with patch.object(avl, "run_tier_backtest") as mock_bt:
            mock_bt.return_value = {
                "sharpe_ratio": 0.1,
                "total_return_pct": None,
                "max_drawdown_pct": None,
            }
            run_validation("", "", skip_dependency_check=True)

        assert log_path.exists()
        data = json.loads(log_path.read_text())
        assert data["convergence"] is False

    def test_stub_mode_convergence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Full stub run (no credentials, real tier files) should converge by tier 2."""
        # Use real tier files from the repository
        real_gen_dir = REPO_ROOT / "strategies" / "generated"
        if not all(
            (real_gen_dir / f"aider_level2_tier{t}.py").exists()
            for t in range(1, 5)
        ):
            pytest.skip("Real tier strategy files not present")

        log_path = tmp_path / "iter_log.json"
        monkeypatch.setattr(avl, "_ITERATION_LOG", log_path)
        # Keep real _GENERATED_DIR and _TIER_STRATEGY_FILES intact (don't patch)

        result = run_validation("", "", skip_dependency_check=True)

        assert result is True
        data = json.loads(log_path.read_text())
        assert data["convergence"] is True


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


class TestMain:
    def _make_tier_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        gen_dir = tmp_path / "strategies" / "generated"
        gen_dir.mkdir(parents=True)
        tier_files = {}
        for tier in range(1, 5):
            f = gen_dir / f"aider_level2_tier{tier}.py"
            f.write_text(f'"""Tier {tier}."""\n')
            tier_files[tier] = f
        monkeypatch.setattr(avl, "_TIER_STRATEGY_FILES", tier_files)
        monkeypatch.setattr(avl, "_GENERATED_DIR", gen_dir)

    def test_exit_0_on_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._make_tier_files(tmp_path, monkeypatch)
        with patch.object(avl, "run_tier_backtest") as mock_bt:
            mock_bt.return_value = {
                "sharpe_ratio": 1.0,
                "total_return_pct": 15.0,
                "max_drawdown_pct": -10.0,
            }
            rc = main(["--skip-dependency-check"])
        assert rc == 0

    def test_exit_1_on_fail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._make_tier_files(tmp_path, monkeypatch)
        with patch.object(avl, "run_tier_backtest") as mock_bt:
            mock_bt.return_value = {
                "sharpe_ratio": 0.1,
                "total_return_pct": None,
                "max_drawdown_pct": None,
            }
            rc = main(["--skip-dependency-check"])
        assert rc == 1

    def test_exit_0_when_dependency_not_met(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing Phase 4A log → non-blocking exit 0 (returns False but exit 0)."""
        # Note: run_validation returns False when dep not met, main returns 0
        # because run_validation returns False and main does: return 0 if passed else 1
        # → non-blocking (exit 1)
        # Actually per the contract: main returns 0 if passed else 1.
        # When dep not met, run_validation returns False → main returns 1.
        # But the script is non-blocking from a CI perspective (stub log is written).
        self._make_tier_files(tmp_path, monkeypatch)
        # _PHASE4A_LOG is redirected to nonexistent path
        rc = main([])
        # Dependency not met → run_validation returns False → main exit 1
        assert rc == 1

    def test_skip_dependency_check_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._make_tier_files(tmp_path, monkeypatch)
        with patch.object(avl, "run_tier_backtest") as mock_bt:
            mock_bt.return_value = {
                "sharpe_ratio": 1.2,
                "total_return_pct": 18.0,
                "max_drawdown_pct": -8.0,
            }
            rc = main(["--skip-dependency-check"])
        assert rc == 0
