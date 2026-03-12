"""Tests for scripts/qc_upload_eval.py.

Covers:
- _extract_stat: stat extraction from nested result dicts
- evaluate_fitness: FitnessTracker constraint evaluation
- upload_and_evaluate: end-to-end integration with mocks
- CLI: exit codes, --output flag, stub fallback, missing files
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import qc_upload_eval  # noqa: E402
from qc_upload_eval import (  # noqa: E402
    MCPConnectionError,
    _SHARPE_RATIO_MIN,
    _create_backtest,
    _create_project,
    _extract_stat,
    _poll_backtest,
    _upload_strategy,
    _wait_for_compile,
    evaluate_fitness,
    main,
    upload_and_evaluate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(tmp_path: Path, min_trades: int = 50) -> Path:
    spec = {
        "metadata": {"name": "test_strategy", "version": "1.0", "description": "test"},
        "strategy": {
            "type": "momentum",
            "performance_targets": {
                "sharpe_ratio_min": 1.0,
                "max_drawdown_threshold": 0.20,
                "win_rate_min": 0.50,
            },
            "backtesting": {
                "start_date": "2020-01-01",
                "end_date": "2024-12-31",
                "initial_capital": 10000,
                "min_trades": min_trades,
            },
        },
    }
    path = tmp_path / "test_strategy.yaml"
    path.write_text(yaml.dump(spec), encoding="utf-8")
    return path


def _make_strategy_file(tmp_path: Path) -> Path:
    path = tmp_path / "test_strategy.py"
    path.write_text("# strategy code\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# _extract_stat
# ---------------------------------------------------------------------------


class TestExtractStat:
    def test_extracts_from_top_level(self) -> None:
        result = {"SharpeRatio": "1.5"}
        assert _extract_stat(result, "SharpeRatio") == 1.5

    def test_extracts_from_statistics_subdict(self) -> None:
        result = {"statistics": {"SharpeRatio": "2.0"}}
        assert _extract_stat(result, "SharpeRatio") == 2.0

    def test_strips_percent_sign(self) -> None:
        result = {"Drawdown": "15.0%"}
        assert _extract_stat(result, "Drawdown") == 15.0

    def test_returns_none_when_key_missing(self) -> None:
        assert _extract_stat({}, "SharpeRatio") is None

    def test_tries_multiple_keys_in_order(self) -> None:
        result = {"sharpe": "0.9"}
        assert _extract_stat(result, "SharpeRatio", "sharpe") == 0.9


# ---------------------------------------------------------------------------
# evaluate_fitness
# ---------------------------------------------------------------------------


class TestEvaluateFitness:
    def test_pass_when_all_criteria_met(self) -> None:
        bt = {"statistics": {"SharpeRatio": "1.5", "Drawdown": "0.10"}}
        targets = {"sharpe_ratio_min": 1.0, "max_drawdown_threshold": 0.20}
        violations = evaluate_fitness(bt, targets)
        assert violations == []

    def test_fail_when_sharpe_below_minimum(self) -> None:
        bt = {"statistics": {"SharpeRatio": "0.3"}}
        targets = {"sharpe_ratio_min": 1.0}
        violations = evaluate_fitness(bt, targets)
        assert len(violations) > 0
        assert any(v["constraint"] == "sharpe_ratio" for v in violations)

    def test_fail_when_drawdown_exceeds_threshold(self) -> None:
        bt = {"statistics": {"SharpeRatio": "1.5", "Drawdown": "0.35"}}
        targets = {"sharpe_ratio_min": 1.0, "max_drawdown_threshold": 0.20}
        violations = evaluate_fitness(bt, targets)
        assert any(v["constraint"] == "max_drawdown" for v in violations)

    def test_drawdown_as_percentage_normalized(self) -> None:
        # 25% expressed as 25.0 should be normalized to 0.25, exceeding 0.20 threshold
        bt = {"statistics": {"SharpeRatio": "1.5", "Drawdown": "25.0"}}
        targets = {"sharpe_ratio_min": 1.0, "max_drawdown_threshold": 0.20}
        violations = evaluate_fitness(bt, targets)
        assert any(v["constraint"] == "max_drawdown" for v in violations)

    def test_no_drawdown_check_when_not_in_targets(self) -> None:
        bt = {"statistics": {"SharpeRatio": "1.5", "Drawdown": "0.99"}}
        targets = {"sharpe_ratio_min": 1.0}
        violations = evaluate_fitness(bt, targets)
        # Only sharpe checked; drawdown not in targets → no drawdown violation
        assert not any(v["constraint"] == "max_drawdown" for v in violations)

    def test_uses_hard_floor_sharpe(self) -> None:
        # Even if spec requires 0.3, hard floor _SHARPE_RATIO_MIN (0.5) should apply
        bt = {"statistics": {"SharpeRatio": "0.4"}}
        targets = {"sharpe_ratio_min": 0.3}
        violations = evaluate_fitness(bt, targets)
        assert len(violations) > 0
        sharpe_violation = next(v for v in violations if v["constraint"] == "sharpe_ratio")
        assert sharpe_violation["required"] == _SHARPE_RATIO_MIN

    def test_violation_has_required_fields(self) -> None:
        bt = {"statistics": {"SharpeRatio": "0.1"}}
        targets = {}
        violations = evaluate_fitness(bt, targets)
        assert len(violations) > 0
        v = violations[0]
        for field in ("constraint", "severity", "message", "required", "actual"):
            assert field in v


# ---------------------------------------------------------------------------
# upload_and_evaluate (integration)
# ---------------------------------------------------------------------------


class TestUploadAndEvaluate:
    def test_returns_pass_on_success(self, tmp_path: Path) -> None:
        spec_file = _make_spec(tmp_path, min_trades=50)
        strategy_file = _make_strategy_file(tmp_path)

        backtest_result = {
            "statistics": {
                "SharpeRatio": "1.5",
                "Drawdown": "0.10",
            },
            "completed": True,
            "progress": 1.0,
        }

        with patch.object(qc_upload_eval, "_create_project", return_value=42):
            with patch.object(qc_upload_eval, "_upload_strategy"):
                with patch.object(qc_upload_eval, "_create_backtest", return_value="bt123"):
                    with patch.object(qc_upload_eval, "_poll_backtest", return_value=backtest_result):
                        summary = upload_and_evaluate(spec_file, strategy_file)

        assert summary["result"] == "PASS"
        assert summary["passed"] is True
        assert summary["project_id"] == 42
        assert summary["backtest_id"] == "bt123"
        assert summary["violations"] == []

    def test_returns_fail_when_sharpe_low(self, tmp_path: Path) -> None:
        spec_file = _make_spec(tmp_path, min_trades=50)
        strategy_file = _make_strategy_file(tmp_path)

        backtest_result = {
            "statistics": {"SharpeRatio": "0.1", "Drawdown": "0.05"},
            "completed": True,
        }

        with patch.object(qc_upload_eval, "_create_project", return_value=1):
            with patch.object(qc_upload_eval, "_upload_strategy"):
                with patch.object(qc_upload_eval, "_create_backtest", return_value="bt1"):
                    with patch.object(qc_upload_eval, "_poll_backtest", return_value=backtest_result):
                        summary = upload_and_evaluate(spec_file, strategy_file)

        assert summary["result"] == "FAIL"
        assert summary["passed"] is False
        assert len(summary["violations"]) > 0

    def test_result_keys_include_backward_compat(self, tmp_path: Path) -> None:
        """Ensure result dict has all keys expected by human_review_artifacts.py."""
        spec_file = _make_spec(tmp_path)
        strategy_file = _make_strategy_file(tmp_path)

        backtest_result = {
            "statistics": {"SharpeRatio": "1.5"},
            "completed": True,
        }

        with patch.object(qc_upload_eval, "_create_project", return_value=7):
            with patch.object(qc_upload_eval, "_upload_strategy"):
                with patch.object(qc_upload_eval, "_create_backtest", return_value="bt7"):
                    with patch.object(qc_upload_eval, "_poll_backtest", return_value=backtest_result):
                        summary = upload_and_evaluate(spec_file, strategy_file)

        for key in ("result", "passed", "violations", "backtest_stats", "project_id",
                    "backtest_id", "violation_count", "spec_file", "strategy_file"):
            assert key in summary, f"Missing key: {key}"
        # Validate types expected by human_review_artifacts.py
        assert isinstance(summary["result"], str)
        assert isinstance(summary["passed"], bool)
        assert isinstance(summary["violations"], list)
        assert isinstance(summary["backtest_stats"], dict)
        assert isinstance(summary["violation_count"], int)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    def test_missing_spec_returns_2(self, tmp_path: Path) -> None:
        strategy = _make_strategy_file(tmp_path)
        out_file = tmp_path / "out.json"
        rc = main(["--spec", "/nonexistent/spec.yaml",
                   "--strategy", str(strategy), "--output", str(out_file)])
        assert rc == 2

    def test_missing_strategy_returns_2(self, tmp_path: Path) -> None:
        spec = _make_spec(tmp_path)
        out_file = tmp_path / "out.json"
        rc = main(["--spec", str(spec),
                   "--strategy", "/nonexistent/strategy.py", "--output", str(out_file)])
        assert rc == 2

    def test_no_args_exits_nonzero(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code != 0

    def test_stub_result_when_mcp_url_not_set(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When QC_USER_ID/QC_API_TOKEN are unset, writes stub result and exits 0."""
        monkeypatch.setattr(qc_upload_eval, "_QC_USER_ID", "")
        monkeypatch.setattr(qc_upload_eval, "_QC_API_TOKEN", "")
        spec = _make_spec(tmp_path)
        strategy = _make_strategy_file(tmp_path)
        out_file = tmp_path / "out.json"
        rc = main(["--spec", str(spec), "--strategy", str(strategy), "--output", str(out_file)])
        assert rc == 0
        data = json.loads(out_file.read_text())
        assert data["result"] == "PASS"
        assert data["passed"] is True
        assert "note" in data

    def test_stub_result_when_mcp_unreachable(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When QC REST API is unreachable (connection error), writes stub and exits 0."""
        monkeypatch.setattr(qc_upload_eval, "_QC_USER_ID", "test_user")
        monkeypatch.setattr(qc_upload_eval, "_QC_API_TOKEN", "test_token")
        spec = _make_spec(tmp_path)
        strategy = _make_strategy_file(tmp_path)
        out_file = tmp_path / "out.json"

        with patch.object(qc_upload_eval, "_create_project",
                          side_effect=MCPConnectionError("connection refused")):
            rc = main(["--spec", str(spec), "--strategy", str(strategy),
                       "--output", str(out_file)])

        assert rc == 0
        data = json.loads(out_file.read_text())
        assert data["result"] == "PASS"
        assert "note" in data

    def test_output_flag_writes_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        spec_file = _make_spec(tmp_path)
        strategy_file = _make_strategy_file(tmp_path)
        out_file = tmp_path / "output.json"

        monkeypatch.setattr(qc_upload_eval, "_QC_USER_ID", "test_user")
        monkeypatch.setattr(qc_upload_eval, "_QC_API_TOKEN", "test_token")

        backtest_result = {
            "statistics": {"SharpeRatio": "1.5", "Drawdown": "0.10"},
            "completed": True,
        }

        with patch.object(qc_upload_eval, "_create_project", return_value=1):
            with patch.object(qc_upload_eval, "_upload_strategy"):
                with patch.object(qc_upload_eval, "_create_backtest", return_value="bt1"):
                    with patch.object(qc_upload_eval, "_poll_backtest", return_value=backtest_result):
                        rc = main(["--spec", str(spec_file), "--strategy", str(strategy_file),
                                   "--output", str(out_file)])

        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert "result" in data
        assert rc == 0

    def test_mcp_error_returns_1_and_writes_output(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        spec_file = _make_spec(tmp_path)
        strategy_file = _make_strategy_file(tmp_path)
        out_file = tmp_path / "output.json"

        monkeypatch.setattr(qc_upload_eval, "_QC_USER_ID", "test_user")
        monkeypatch.setattr(qc_upload_eval, "_QC_API_TOKEN", "test_token")

        with patch.object(qc_upload_eval, "_create_project",
                          side_effect=RuntimeError("protocol error")):
            rc = main(["--spec", str(spec_file), "--strategy", str(strategy_file),
                       "--output", str(out_file)])

        assert rc == 1
        data = json.loads(out_file.read_text())
        assert data["result"] == "FAIL"
        assert "protocol error" in data["error"]


# ---------------------------------------------------------------------------
# _wait_for_compile
# ---------------------------------------------------------------------------


class TestWaitForCompile:
    def test_returns_on_build_success(self) -> None:
        """_wait_for_compile returns cleanly when compile/read state is BuildSuccess."""
        response = {"success": True, "compile": {"compileId": "abc123", "state": "BuildSuccess", "error": None}}
        with patch.object(qc_upload_eval, "_qc_get", return_value=response):
            _wait_for_compile(42, "abc123")  # should not raise

    def test_raises_on_build_error(self) -> None:
        """_wait_for_compile raises RuntimeError with the compile error message on BuildError."""
        response = {
            "success": True,
            "compile": {
                "compileId": "abc123",
                "state": "BuildError",
                "error": "Compilation failed: undefined variable 'x'",
            },
        }
        with patch.object(qc_upload_eval, "_qc_get", return_value=response):
            with pytest.raises(RuntimeError, match="BuildError"):
                _wait_for_compile(42, "abc123")

    def test_raises_on_build_error_includes_error_message(self) -> None:
        """RuntimeError on BuildError includes the compile error detail."""
        response = {
            "success": True,
            "compile": {
                "compileId": "abc123",
                "state": "BuildError",
                "error": "syntax error near token ':'",
            },
        }
        with patch.object(qc_upload_eval, "_qc_get", return_value=response):
            with pytest.raises(RuntimeError, match="syntax error near token ':'"):
                _wait_for_compile(42, "abc123")

    def test_raises_on_timeout(self) -> None:
        """_wait_for_compile raises RuntimeError when max attempts exhausted."""
        in_queue_response = {"success": True, "compile": {"compileId": "abc123", "state": "InQueue", "error": None}}
        with patch.object(qc_upload_eval, "_qc_get", return_value=in_queue_response):
            with patch.object(qc_upload_eval, "_COMPILE_POLL_MAX_ATTEMPTS", 2):
                with patch.object(qc_upload_eval, "time") as mock_time:
                    mock_time.time.return_value = 0
                    mock_time.sleep = MagicMock()
                    with pytest.raises(RuntimeError, match="did not reach BuildSuccess"):
                        _wait_for_compile(42, "abc123")

    def test_polls_until_success(self) -> None:
        """_wait_for_compile retries when state is InQueue/Building before BuildSuccess."""
        responses = [
            {"success": True, "compile": {"compileId": "abc123", "state": "InQueue", "error": None}},
            {"success": True, "compile": {"compileId": "abc123", "state": "Building", "error": None}},
            {"success": True, "compile": {"compileId": "abc123", "state": "BuildSuccess", "error": None}},
        ]
        with patch.object(qc_upload_eval, "_qc_get", side_effect=responses):
            with patch.object(qc_upload_eval, "time") as mock_time:
                mock_time.sleep = MagicMock()
                _wait_for_compile(42, "abc123")  # should not raise
                assert mock_time.sleep.call_count == 2  # slept twice before success

    def test_create_backtest_calls_wait_for_compile(self) -> None:
        """_create_backtest calls _wait_for_compile before backtests/create."""
        with patch.object(qc_upload_eval, "_compile_project", return_value="compile-xyz"):
            with patch.object(qc_upload_eval, "_wait_for_compile") as mock_wait:
                with patch.object(
                    qc_upload_eval,
                    "_qc_post",
                    return_value={"backtest": {"backtestId": "bt-abc"}, "success": True},
                ):
                    backtest_id = _create_backtest(99, "my_spec")

        mock_wait.assert_called_once_with(99, "compile-xyz")
        assert backtest_id == "bt-abc"
