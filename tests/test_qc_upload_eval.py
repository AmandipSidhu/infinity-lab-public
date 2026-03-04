"""Tests for scripts/qc_upload_eval.py.

Covers:
- _extract_stat: stat extraction from nested result dicts
- _evaluate_criteria: acceptance criteria evaluation
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
    _SHARPE_RATIO_MIN,
    _create_backtest,
    _create_project,
    _evaluate_criteria,
    _extract_stat,
    _poll_backtest,
    _upload_file,
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
# _evaluate_criteria
# ---------------------------------------------------------------------------


class TestEvaluateCriteria:
    def test_pass_when_all_criteria_met(self) -> None:
        bt = {"statistics": {"SharpeRatio": "1.5", "Drawdown": "0.10",
                              "WinRate": "0.60", "TotalOrders": "100"}}
        targets = {"sharpe_ratio_min": 1.0, "max_drawdown_threshold": 0.20, "win_rate_min": 0.50}
        result = _evaluate_criteria(bt, targets, min_trades=50)
        assert result["passed"] is True
        assert result["failures"] == []

    def test_fail_when_sharpe_below_minimum(self) -> None:
        bt = {"statistics": {"SharpeRatio": "0.3", "TotalOrders": "100"}}
        targets = {"sharpe_ratio_min": 1.0}
        result = _evaluate_criteria(bt, targets, min_trades=50)
        assert result["passed"] is False
        assert any("Sharpe" in f for f in result["failures"])

    def test_fail_when_drawdown_exceeds_threshold(self) -> None:
        bt = {"statistics": {"SharpeRatio": "1.5", "Drawdown": "0.35", "TotalOrders": "100"}}
        targets = {"sharpe_ratio_min": 1.0, "max_drawdown_threshold": 0.20}
        result = _evaluate_criteria(bt, targets, min_trades=50)
        assert result["passed"] is False
        assert any("Drawdown" in f or "rawdown" in f for f in result["failures"])

    def test_drawdown_as_percentage_normalized(self) -> None:
        # 25% expressed as 25.0 should be normalized to 0.25, exceeding 0.20 threshold
        bt = {"statistics": {"SharpeRatio": "1.5", "Drawdown": "25.0", "TotalOrders": "100"}}
        targets = {"sharpe_ratio_min": 1.0, "max_drawdown_threshold": 0.20}
        result = _evaluate_criteria(bt, targets, min_trades=50)
        assert result["passed"] is False

    def test_no_drawdown_check_when_not_in_targets(self) -> None:
        bt = {"statistics": {"SharpeRatio": "1.5", "Drawdown": "0.99", "TotalOrders": "100"}}
        targets = {"sharpe_ratio_min": 1.0}
        result = _evaluate_criteria(bt, targets, min_trades=50)
        # Only sharpe checked; drawdown not in targets → no drawdown failure
        assert "drawdown" not in result["criteria_results"]

    def test_fail_when_min_trades_not_met(self) -> None:
        bt = {"statistics": {"SharpeRatio": "1.5", "TotalOrders": "10"}}
        targets = {"sharpe_ratio_min": 1.0}
        result = _evaluate_criteria(bt, targets, min_trades=100)
        assert result["passed"] is False
        assert result["criteria_results"]["min_trades"]["passed"] is False

    def test_uses_hard_floor_sharpe(self) -> None:
        # Even if spec requires 0.3, hard floor _SHARPE_RATIO_MIN (0.5) should apply
        bt = {"statistics": {"SharpeRatio": "0.4", "TotalOrders": "100"}}
        targets = {"sharpe_ratio_min": 0.3}
        result = _evaluate_criteria(bt, targets, min_trades=50)
        assert result["passed"] is False
        assert result["criteria_results"]["sharpe"]["required"] == _SHARPE_RATIO_MIN


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
                "WinRate": "0.60",
                "TotalOrders": "100",
            },
        }

        with patch.object(qc_upload_eval, "_create_project", return_value=42):
            with patch.object(qc_upload_eval, "_upload_file"):
                with patch.object(qc_upload_eval, "_create_backtest", return_value="bt123"):
                    with patch.object(qc_upload_eval, "_poll_backtest", return_value=backtest_result):
                        summary = upload_and_evaluate(spec_file, strategy_file, "uid", "tok")

        assert summary["result"] == "PASS"
        assert summary["project_id"] == 42
        assert summary["backtest_id"] == "bt123"
        assert summary["failures"] == []

    def test_returns_fail_when_sharpe_low(self, tmp_path: Path) -> None:
        spec_file = _make_spec(tmp_path, min_trades=50)
        strategy_file = _make_strategy_file(tmp_path)

        backtest_result = {
            "statistics": {"SharpeRatio": "0.1", "Drawdown": "0.05", "TotalOrders": "100"},
        }

        with patch.object(qc_upload_eval, "_create_project", return_value=1):
            with patch.object(qc_upload_eval, "_upload_file"):
                with patch.object(qc_upload_eval, "_create_backtest", return_value="bt1"):
                    with patch.object(qc_upload_eval, "_poll_backtest", return_value=backtest_result):
                        summary = upload_and_evaluate(spec_file, strategy_file, "uid", "tok")

        assert summary["result"] == "FAIL"
        assert len(summary["failures"]) > 0


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

    def test_stub_result_when_no_credentials(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When QC_USER_ID/QC_API_TOKEN unset, writes stub result and exits 0."""
        monkeypatch.delenv("QC_USER_ID", raising=False)
        monkeypatch.delenv("QC_API_TOKEN", raising=False)
        spec = _make_spec(tmp_path)
        strategy = _make_strategy_file(tmp_path)
        out_file = tmp_path / "out.json"
        rc = main(["--spec", str(spec), "--strategy", str(strategy), "--output", str(out_file)])
        assert rc == 0
        data = json.loads(out_file.read_text())
        assert data["result"] == "PASS"
        assert data["passed"] is True
        assert "note" in data

    def test_output_flag_writes_json(self, tmp_path: Path) -> None:
        spec_file = _make_spec(tmp_path)
        strategy_file = _make_strategy_file(tmp_path)
        out_file = tmp_path / "output.json"

        backtest_result = {
            "statistics": {"SharpeRatio": "1.5", "Drawdown": "0.10", "TotalOrders": "100"},
        }

        with patch.object(qc_upload_eval, "_create_project", return_value=1):
            with patch.object(qc_upload_eval, "_upload_file"):
                with patch.object(qc_upload_eval, "_create_backtest", return_value="bt1"):
                    with patch.object(qc_upload_eval, "_poll_backtest", return_value=backtest_result):
                        with patch.dict(os.environ, {"QC_USER_ID": "uid", "QC_API_TOKEN": "tok"}):
                            rc = main(["--spec", str(spec_file), "--strategy", str(strategy_file),
                                       "--output", str(out_file)])

        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert "result" in data
        assert rc == 0

    def test_api_error_returns_1_and_writes_output(self, tmp_path: Path) -> None:
        spec_file = _make_spec(tmp_path)
        strategy_file = _make_strategy_file(tmp_path)
        out_file = tmp_path / "output.json"

        with patch.object(qc_upload_eval, "_create_project",
                          side_effect=RuntimeError("conn refused")):
            with patch.dict(os.environ, {"QC_USER_ID": "uid", "QC_API_TOKEN": "tok"}):
                rc = main(["--spec", str(spec_file), "--strategy", str(strategy_file),
                           "--output", str(out_file)])

        assert rc == 1
        data = json.loads(out_file.read_text())
        assert data["result"] == "FAIL"
        assert "conn refused" in data["error"]
