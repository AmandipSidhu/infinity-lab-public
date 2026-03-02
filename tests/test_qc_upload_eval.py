"""Tests for scripts/qc_upload_eval.py.

Covers:
- _rpc_call: HTTP transport, error handling
- _create_project: project_id extraction
- _upload_strategy: file creation call
- _create_backtest: backtest_id extraction
- _poll_backtest: polling until completed flag
- evaluate_fitness: Sharpe Ratio and Max Drawdown constraints
- upload_and_evaluate: end-to-end integration with mocks
- CLI: exit codes, --output flag, missing files
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import qc_upload_eval  # noqa: E402
from qc_upload_eval import (  # noqa: E402
    _SHARPE_RATIO_MIN,
    _create_backtest,
    _create_project,
    _extract_stat,
    _poll_backtest,
    _rpc_call,
    _upload_strategy,
    evaluate_fitness,
    main,
    upload_and_evaluate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(json_body: dict, status_code: int = 200) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_body
    mock.raise_for_status = MagicMock()
    if status_code >= 400:
        import requests
        mock.raise_for_status.side_effect = requests.HTTPError(response=mock)
    return mock


def _mcp_ok(payload: dict) -> dict:
    """Wrap *payload* in a standard MCP tools/call success response."""
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "result": {
            "content": [{"type": "text", "text": json.dumps(payload)}]
        },
    }


def _make_spec(tmp_path: Path) -> Path:
    spec = {
        "metadata": {"name": "test_strategy", "version": "1.0", "description": "test"},
        "strategy": {
            "type": "momentum",
            "performance_targets": {
                "sharpe_ratio_min": 1.0,
                "max_drawdown_threshold": 0.20,
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
# _rpc_call
# ---------------------------------------------------------------------------


class TestRpcCall:
    def test_successful_call_returns_parsed_content(self) -> None:
        payload = {"projectId": 42}
        with patch("requests.post", return_value=_mock_response(_mcp_ok(payload))):
            result = _rpc_call("create_project", {"name": "test"})
        assert result == {"projectId": 42}

    def test_mcp_error_raises_runtime_error(self) -> None:
        error_body = {
            "jsonrpc": "2.0",
            "id": "1",
            "error": {"code": -32600, "message": "Invalid request"},
        }
        with patch("requests.post", return_value=_mock_response(error_body)):
            with pytest.raises(RuntimeError, match="MCP server returned error"):
                _rpc_call("create_project", {"name": "test"})

    def test_network_error_raises_runtime_error(self) -> None:
        import requests as req

        with patch("requests.post", side_effect=req.ConnectionError("refused")):
            with pytest.raises(RuntimeError, match="MCP server request failed"):
                _rpc_call("create_project", {"name": "test"})

    def test_result_without_content_returned_directly(self) -> None:
        body = {"jsonrpc": "2.0", "id": "1", "result": {"project_id": 7}}
        with patch("requests.post", return_value=_mock_response(body)):
            result = _rpc_call("create_project", {"name": "test"})
        assert result == {"project_id": 7}


# ---------------------------------------------------------------------------
# _create_project
# ---------------------------------------------------------------------------


class TestCreateProject:
    def test_returns_project_id_from_projectId_key(self) -> None:
        with patch.object(qc_upload_eval, "_rpc_call", return_value={"projectId": 99}):
            pid = _create_project("my_strategy")
        assert pid == 99

    def test_returns_project_id_from_project_id_key(self) -> None:
        with patch.object(qc_upload_eval, "_rpc_call", return_value={"project_id": 55}):
            pid = _create_project("my_strategy")
        assert pid == 55

    def test_raises_if_no_project_id(self) -> None:
        with patch.object(qc_upload_eval, "_rpc_call", return_value={"name": "x"}):
            with pytest.raises(RuntimeError, match="project_id"):
                _create_project("my_strategy")


# ---------------------------------------------------------------------------
# _create_backtest
# ---------------------------------------------------------------------------


class TestCreateBacktest:
    def test_returns_backtest_id_from_backtestId_key(self) -> None:
        with patch.object(qc_upload_eval, "_rpc_call", return_value={"backtestId": "abc123"}):
            bid = _create_backtest(1, "strat")
        assert bid == "abc123"

    def test_raises_if_no_backtest_id(self) -> None:
        with patch.object(qc_upload_eval, "_rpc_call", return_value={}):
            with pytest.raises(RuntimeError, match="backtest_id"):
                _create_backtest(1, "strat")


# ---------------------------------------------------------------------------
# _poll_backtest
# ---------------------------------------------------------------------------


class TestPollBacktest:
    def test_returns_immediately_when_completed_true(self) -> None:
        finished = {"completed": True, "progress": 1.0, "statistics": {"SharpeRatio": "1.5"}}
        with patch.object(qc_upload_eval, "_rpc_call", return_value=finished):
            result = _poll_backtest(1, "bid")
        assert result["completed"] is True

    def test_polls_until_completed(self) -> None:
        call_count = 0

        def mock_rpc(*args: object, **kwargs: object) -> dict:  # noqa: ANN001
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return {"completed": False, "progress": call_count * 0.3}
            return {"completed": True, "progress": 1.0}

        with patch.object(qc_upload_eval, "_rpc_call", side_effect=mock_rpc):
            with patch("time.sleep"):
                result = _poll_backtest(1, "bid")

        assert result["completed"] is True
        assert call_count == 3

    def test_raises_on_timeout(self) -> None:
        with patch.object(
            qc_upload_eval,
            "_POLL_MAX_ATTEMPTS",
            2,
        ):
            with patch.object(
                qc_upload_eval, "_rpc_call", return_value={"completed": False, "progress": 0.1}
            ):
                with patch("time.sleep"):
                    with pytest.raises(RuntimeError, match="did not complete"):
                        _poll_backtest(1, "bid")


# ---------------------------------------------------------------------------
# evaluate_fitness
# ---------------------------------------------------------------------------


class TestEvaluateFitness:
    def test_pass_when_sharpe_above_minimum(self) -> None:
        result = {"statistics": {"SharpeRatio": "1.2", "Drawdown": "0.10"}}
        targets = {"sharpe_ratio_min": 0.5, "max_drawdown_threshold": 0.20}
        violations = evaluate_fitness(result, targets)
        assert violations == []

    def test_fail_when_sharpe_below_minimum(self) -> None:
        result = {"statistics": {"SharpeRatio": "0.3"}}
        targets = {"sharpe_ratio_min": 0.5, "max_drawdown_threshold": 0.20}
        violations = evaluate_fitness(result, targets)
        sharpe_violations = [v for v in violations if v["constraint"] == "sharpe_ratio"]
        assert len(sharpe_violations) == 1

    def test_fail_when_sharpe_missing(self) -> None:
        result = {"statistics": {}}
        targets = {"sharpe_ratio_min": 0.5}
        violations = evaluate_fitness(result, targets)
        sharpe_violations = [v for v in violations if v["constraint"] == "sharpe_ratio"]
        assert len(sharpe_violations) == 1
        assert sharpe_violations[0]["actual"] is None

    def test_fail_when_drawdown_exceeds_threshold(self) -> None:
        result = {"statistics": {"SharpeRatio": "1.5", "Drawdown": "0.35"}}
        targets = {"sharpe_ratio_min": 0.5, "max_drawdown_threshold": 0.20}
        violations = evaluate_fitness(result, targets)
        dd_violations = [v for v in violations if v["constraint"] == "max_drawdown"]
        assert len(dd_violations) == 1

    def test_drawdown_as_percentage_normalized(self) -> None:
        # 25% expressed as 25.0 should be normalized to 0.25, exceeding 0.20 threshold
        result = {"statistics": {"SharpeRatio": "1.5", "Drawdown": "25.0"}}
        targets = {"sharpe_ratio_min": 0.5, "max_drawdown_threshold": 0.20}
        violations = evaluate_fitness(result, targets)
        dd_violations = [v for v in violations if v["constraint"] == "max_drawdown"]
        assert len(dd_violations) == 1

    def test_uses_hard_floor_sharpe_when_spec_is_lower(self) -> None:
        # spec says 0.3 but hard floor is _SHARPE_RATIO_MIN (0.5)
        result = {"statistics": {"SharpeRatio": "0.4"}}
        targets = {"sharpe_ratio_min": 0.3}
        violations = evaluate_fitness(result, targets)
        sharpe_violations = [v for v in violations if v["constraint"] == "sharpe_ratio"]
        assert len(sharpe_violations) == 1
        assert sharpe_violations[0]["required"] == _SHARPE_RATIO_MIN

    def test_no_drawdown_check_when_threshold_not_in_spec(self) -> None:
        result = {"statistics": {"SharpeRatio": "1.5", "Drawdown": "0.99"}}
        targets = {"sharpe_ratio_min": 0.5}  # no max_drawdown_threshold
        violations = evaluate_fitness(result, targets)
        dd_violations = [v for v in violations if v["constraint"] == "max_drawdown"]
        assert dd_violations == []


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
# upload_and_evaluate (integration)
# ---------------------------------------------------------------------------


class TestUploadAndEvaluate:
    def test_returns_pass_on_success(self, tmp_path: Path) -> None:
        spec_file = _make_spec(tmp_path)
        strategy_file = _make_strategy_file(tmp_path)

        backtest_result = {
            "completed": True,
            "progress": 1.0,
            "statistics": {"SharpeRatio": "1.5", "Drawdown": "0.10"},
        }

        with patch.object(qc_upload_eval, "_create_project", return_value=42):
            with patch.object(qc_upload_eval, "_upload_strategy"):
                with patch.object(qc_upload_eval, "_create_backtest", return_value="bt123"):
                    with patch.object(qc_upload_eval, "_poll_backtest", return_value=backtest_result):
                        summary = upload_and_evaluate(spec_file, strategy_file)

        assert summary["result"] == "PASS"
        assert summary["project_id"] == 42
        assert summary["backtest_id"] == "bt123"
        assert summary["violations"] == []

    def test_returns_fail_when_sharpe_low(self, tmp_path: Path) -> None:
        spec_file = _make_spec(tmp_path)
        strategy_file = _make_strategy_file(tmp_path)

        backtest_result = {
            "completed": True,
            "statistics": {"SharpeRatio": "0.1", "Drawdown": "0.05"},
        }

        with patch.object(qc_upload_eval, "_create_project", return_value=1):
            with patch.object(qc_upload_eval, "_upload_strategy"):
                with patch.object(qc_upload_eval, "_create_backtest", return_value="bt1"):
                    with patch.object(qc_upload_eval, "_poll_backtest", return_value=backtest_result):
                        summary = upload_and_evaluate(spec_file, strategy_file)

        assert summary["result"] == "FAIL"
        assert any(v["constraint"] == "sharpe_ratio" for v in summary["violations"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    def test_missing_spec_returns_2(self, tmp_path: Path) -> None:
        strategy = _make_strategy_file(tmp_path)
        rc = main(["--spec", "/nonexistent/spec.yaml", "--strategy", str(strategy)])
        assert rc == 2

    def test_missing_strategy_returns_2(self, tmp_path: Path) -> None:
        spec = _make_spec(tmp_path)
        rc = main(["--spec", str(spec), "--strategy", "/nonexistent/strategy.py"])
        assert rc == 2

    def test_no_args_exits_nonzero(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code != 0

    def test_output_flag_writes_json(self, tmp_path: Path) -> None:
        spec_file = _make_spec(tmp_path)
        strategy_file = _make_strategy_file(tmp_path)
        out_file = tmp_path / "output.json"

        backtest_result = {
            "completed": True,
            "statistics": {"SharpeRatio": "1.5", "Drawdown": "0.10"},
        }

        with patch.object(qc_upload_eval, "_create_project", return_value=1):
            with patch.object(qc_upload_eval, "_upload_strategy"):
                with patch.object(qc_upload_eval, "_create_backtest", return_value="bt1"):
                    with patch.object(qc_upload_eval, "_poll_backtest", return_value=backtest_result):
                        rc = main(
                            [
                                "--spec", str(spec_file),
                                "--strategy", str(strategy_file),
                                "--output", str(out_file),
                            ]
                        )

        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert "result" in data
        assert rc == 0

    def test_runtime_error_returns_1_and_writes_output(self, tmp_path: Path) -> None:
        spec_file = _make_spec(tmp_path)
        strategy_file = _make_strategy_file(tmp_path)
        out_file = tmp_path / "output.json"

        with patch.object(qc_upload_eval, "_create_project", side_effect=RuntimeError("conn refused")):
            rc = main(
                [
                    "--spec", str(spec_file),
                    "--strategy", str(strategy_file),
                    "--output", str(out_file),
                ]
            )

        assert rc == 1
        data = json.loads(out_file.read_text())
        assert data["result"] == "FAIL"
        assert "conn refused" in data["error"]
