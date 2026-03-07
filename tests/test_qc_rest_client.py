"""Integration tests for scripts/qc_rest_client.py — Phase 1.

Tests that hit the REAL QuantConnect REST API (api.quantconnect.com).
No mocks are used for the integration test suite.

Requirements:
  - QC_USER_ID and QC_API_TOKEN environment variables must be set.
  - The test WILL be skipped (not faked) if credentials are absent.
  - If credentials are present but authentication fails, the test FAILS LOUDLY.

The integration test:
  1. Creates a throwaway QC project named test-acb-{unix_timestamp}
  2. Uploads strategies/reference/sma_crossover_simple.py
  3. Compiles it and polls until CompileState == "BuildSuccess"
  4. Creates a backtest and polls until Completed == True
  5. Asserts result["sharpe_ratio"] is a float (not None, not mocked)
  6. Asserts /tmp/backtest_result.json is written with the required schema
"""

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import qc_rest_client  # noqa: E402
from qc_rest_client import (  # noqa: E402
    QCAPIError,
    QCAuthError,
    QCCompileError,
    QCTimeoutError,
    _extract_stat,
    _extract_int_stat,
    _qc_auth,
    create_backtest,
    create_project,
    compile_project,
    poll_backtest,
    poll_compile,
    run_backtest,
    upload_file,
    main,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REFERENCE_STRATEGY = (
    REPO_ROOT / "strategies" / "reference" / "sma_crossover_simple.py"
)
_QC_USER_ID = os.environ.get("QC_USER_ID", "").strip()
_QC_API_TOKEN = os.environ.get("QC_API_TOKEN", "").strip()
_CREDENTIALS_AVAILABLE = bool(_QC_USER_ID and _QC_API_TOKEN)


# ---------------------------------------------------------------------------
# Unit tests (no network calls)
# ---------------------------------------------------------------------------


class TestQCAuth:
    def test_returns_headers_and_auth_tuple(self) -> None:
        headers, auth = _qc_auth("user123", "token456")
        assert "Timestamp" in headers
        assert int(headers["Timestamp"]) > 0
        assert auth[0] == "user123"
        assert len(auth[1]) == 64  # SHA-256 hex digest

    def test_timestamp_is_recent(self) -> None:
        headers, _ = _qc_auth("u", "t")
        assert abs(int(headers["Timestamp"]) - int(time.time())) <= 2

    def test_different_calls_produce_different_hashes(self) -> None:
        _, auth1 = _qc_auth("u", "t")
        time.sleep(1)
        _, auth2 = _qc_auth("u", "t")
        # Hashes differ because timestamps differ
        assert auth1[1] != auth2[1]


class TestExtractStat:
    def test_top_level_key(self) -> None:
        assert _extract_stat({"SharpeRatio": "1.5"}, "SharpeRatio") == 1.5

    def test_nested_statistics(self) -> None:
        result = {"Statistics": {"Sharpe Ratio": "0.82"}}
        assert _extract_stat(result, "Sharpe Ratio") == 0.82

    def test_strips_percent(self) -> None:
        assert _extract_stat({"Drawdown": "15.3%"}, "Drawdown") == 15.3

    def test_returns_none_when_missing(self) -> None:
        assert _extract_stat({}, "SharpeRatio") is None

    def test_tries_multiple_keys(self) -> None:
        result = {"sharpe": "0.77"}
        assert _extract_stat(result, "SharpeRatio", "sharpe") == 0.77

    def test_lowercase_statistics_subdict(self) -> None:
        result = {"statistics": {"SharpeRatio": "1.2"}}
        assert _extract_stat(result, "SharpeRatio") == 1.2


class TestExtractIntStat:
    def test_returns_int(self) -> None:
        assert _extract_int_stat({"TotalNumberOfTrades": "42"}, "TotalNumberOfTrades") == 42

    def test_returns_none_when_missing(self) -> None:
        assert _extract_int_stat({}, "TotalTrades") is None

    def test_truncates_float(self) -> None:
        assert _extract_int_stat({"Trades": "7.9"}, "Trades") == 7


class TestCreateProject:
    def test_returns_project_id(self) -> None:
        mock_response = {"success": True, "projects": [{"projectId": 99999}]}
        with patch.object(qc_rest_client, "_http_post", return_value=mock_response):
            pid = create_project("u", "t", "test-project")
        assert pid == 99999

    def test_raises_on_missing_projects_list(self) -> None:
        mock_response = {"success": True, "projects": []}
        with patch.object(qc_rest_client, "_http_post", return_value=mock_response):
            with pytest.raises(QCAPIError, match="missing 'projects'"):
                create_project("u", "t", "test-project")

    def test_raises_on_missing_project_id(self) -> None:
        mock_response = {"success": True, "projects": [{"name": "test"}]}
        with patch.object(qc_rest_client, "_http_post", return_value=mock_response):
            with pytest.raises(QCAPIError, match="projectId"):
                create_project("u", "t", "test-project")


class TestCompileProject:
    def test_returns_compile_id_top_level(self) -> None:
        mock_response = {"success": True, "compileId": "cid-abc"}
        with patch.object(qc_rest_client, "_http_post", return_value=mock_response):
            cid = compile_project("u", "t", 12345)
        assert cid == "cid-abc"

    def test_returns_compile_id_nested(self) -> None:
        mock_response = {"success": True, "compile": {"compileId": "cid-xyz"}}
        with patch.object(qc_rest_client, "_http_post", return_value=mock_response):
            cid = compile_project("u", "t", 12345)
        assert cid == "cid-xyz"

    def test_raises_on_missing_compile_id(self) -> None:
        mock_response = {"success": True}
        with patch.object(qc_rest_client, "_http_post", return_value=mock_response):
            with pytest.raises(QCAPIError, match="compileId"):
                compile_project("u", "t", 12345)


class TestPollCompile:
    def test_returns_on_build_success(self) -> None:
        mock_get = MagicMock(
            return_value={"success": True, "compile": {"state": "BuildSuccess"}}
        )
        with patch.object(qc_rest_client, "_http_get", mock_get):
            state = poll_compile(
                "u", "t", 1, "cid", time.time() + 300
            )
        assert state == "BuildSuccess"

    def test_raises_on_build_error(self) -> None:
        mock_get = MagicMock(
            return_value={"success": True, "compile": {"state": "BuildError", "logs": []}}
        )
        with patch.object(qc_rest_client, "_http_get", mock_get):
            with pytest.raises(QCCompileError):
                poll_compile("u", "t", 1, "cid", time.time() + 300)

    def test_raises_on_timeout(self) -> None:
        mock_get = MagicMock(
            return_value={"success": True, "compile": {"state": "InProgress"}}
        )
        with patch.object(qc_rest_client, "_http_get", mock_get):
            with patch.object(qc_rest_client, "_POLL_INTERVAL_SECONDS", 0):
                with pytest.raises(QCTimeoutError):
                    poll_compile(
                        "u", "t", 1, "cid", time.time() - 1  # deadline already past
                    )

    def test_polls_until_success_after_wait(self) -> None:
        responses = [
            {"success": True, "compile": {"state": "InProgress"}},
            {"success": True, "compile": {"state": "BuildSuccess"}},
        ]
        mock_get = MagicMock(side_effect=responses)
        with patch.object(qc_rest_client, "_http_get", mock_get):
            with patch.object(qc_rest_client, "time") as mock_time:
                mock_time.time.side_effect = [
                    1000.0,  # initial deadline check
                    1001.0,  # first poll deadline check
                    1002.0,  # sleep call check
                    1003.0,  # second poll deadline check
                ]
                mock_time.sleep = MagicMock()
                state = poll_compile(
                    "u", "t", 1, "cid", 1100.0
                )
        assert state == "BuildSuccess"
        assert mock_get.call_count == 2


class TestCreateBacktest:
    def test_returns_backtest_id_nested(self) -> None:
        mock_response = {
            "success": True,
            "backtest": {"backtestId": "bt-abc123"},
        }
        with patch.object(qc_rest_client, "_http_post", return_value=mock_response):
            bid = create_backtest("u", "t", 1, "cid", "bt_name")
        assert bid == "bt-abc123"

    def test_returns_backtest_id_top_level(self) -> None:
        mock_response = {"success": True, "backtestId": "bt-top123"}
        with patch.object(qc_rest_client, "_http_post", return_value=mock_response):
            bid = create_backtest("u", "t", 1, "cid", "bt_name")
        assert bid == "bt-top123"

    def test_raises_on_missing_id(self) -> None:
        mock_response = {"success": True}
        with patch.object(qc_rest_client, "_http_post", return_value=mock_response):
            with pytest.raises(QCAPIError, match="backtestId"):
                create_backtest("u", "t", 1, "cid", "bt_name")


class TestPollBacktest:
    def test_returns_when_completed(self) -> None:
        bt_result = {"completed": True, "progress": 1.0, "statistics": {}}
        mock_get = MagicMock(return_value={"success": True, "backtest": bt_result})
        with patch.object(qc_rest_client, "_http_get", mock_get):
            result = poll_backtest("u", "t", 1, "bid", time.time() + 300)
        assert result["completed"] is True

    def test_raises_on_timeout(self) -> None:
        bt_result = {"completed": False, "progress": 0.5}
        mock_get = MagicMock(return_value={"success": True, "backtest": bt_result})
        with patch.object(qc_rest_client, "_http_get", mock_get):
            with patch.object(qc_rest_client, "_POLL_INTERVAL_SECONDS", 0):
                with pytest.raises(QCTimeoutError):
                    poll_backtest(
                        "u", "t", 1, "bid", time.time() - 1  # deadline already past
                    )


class TestRunBacktest:
    def test_full_pipeline_success(self, tmp_path: Path) -> None:
        strategy_file = tmp_path / "strategy.py"
        strategy_file.write_text("# strategy\n", encoding="utf-8")
        output_path = tmp_path / "result.json"

        bt_result = {
            "completed": True,
            "progress": 1.0,
            "Statistics": {
                "Sharpe Ratio": "0.82",
                "Net Profit": "12.4%",
                "Drawdown": "18.2%",
                "Total Trades": "15",
            },
        }

        with patch.object(qc_rest_client, "create_project", return_value=42):
            with patch.object(qc_rest_client, "upload_file"):
                with patch.object(qc_rest_client, "compile_project", return_value="cid"):
                    with patch.object(qc_rest_client, "poll_compile", return_value="BuildSuccess"):
                        with patch.object(qc_rest_client, "create_backtest", return_value="bid"):
                            with patch.object(qc_rest_client, "poll_backtest", return_value=bt_result):
                                result = run_backtest(
                                    strategy_file, "u", "t", output_path
                                )

        assert result["project_id"] == "42"
        assert result["backtest_id"] == "bid"
        assert isinstance(result["sharpe_ratio"], float)
        assert result["sharpe_ratio"] == pytest.approx(0.82)
        assert result["compile_state"] == "BuildSuccess"
        assert result["backtest_status"] == "Completed"
        assert result["qc_ui_url"] == "https://www.quantconnect.com/project/42"
        assert "timestamp" in result

        # Verify JSON written to disk
        assert output_path.is_file()
        on_disk = json.loads(output_path.read_text())
        assert on_disk["sharpe_ratio"] == pytest.approx(0.82)

    def test_raises_on_missing_strategy_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            run_backtest(tmp_path / "nonexistent.py", "u", "t")

    def test_result_schema_keys(self, tmp_path: Path) -> None:
        """All required output schema keys must be present."""
        strategy_file = tmp_path / "strategy.py"
        strategy_file.write_text("# code\n", encoding="utf-8")

        bt_result = {"completed": True, "statistics": {"SharpeRatio": "1.0"}}

        with patch.object(qc_rest_client, "create_project", return_value=1):
            with patch.object(qc_rest_client, "upload_file"):
                with patch.object(qc_rest_client, "compile_project", return_value="c1"):
                    with patch.object(qc_rest_client, "poll_compile", return_value="BuildSuccess"):
                        with patch.object(qc_rest_client, "create_backtest", return_value="b1"):
                            with patch.object(qc_rest_client, "poll_backtest", return_value=bt_result):
                                result = run_backtest(strategy_file, "u", "t")

        required_keys = {
            "project_id", "backtest_id", "sharpe_ratio",
            "total_return_pct", "max_drawdown_pct", "total_trades",
            "compile_state", "backtest_status", "qc_ui_url", "timestamp",
        }
        assert required_keys.issubset(result.keys()), (
            f"Missing keys: {required_keys - result.keys()}"
        )


class TestCLI:
    def test_missing_strategy_returns_2(self, tmp_path: Path) -> None:
        rc = main(["--strategy", str(tmp_path / "nonexistent.py"),
                   "--qc-user-id", "u", "--qc-api-token", "t"])
        assert rc == 2

    def test_missing_credentials_returns_1(self, tmp_path: Path) -> None:
        strategy = tmp_path / "strategy.py"
        strategy.write_text("# code\n", encoding="utf-8")
        rc = main(["--strategy", str(strategy), "--qc-user-id", "", "--qc-api-token", ""])
        assert rc == 1

    def test_auth_error_returns_1(self, tmp_path: Path) -> None:
        strategy = tmp_path / "strategy.py"
        strategy.write_text("# code\n", encoding="utf-8")
        with patch.object(
            qc_rest_client,
            "run_backtest",
            side_effect=QCAuthError("Unauthorized"),
        ):
            rc = main(["--strategy", str(strategy),
                       "--qc-user-id", "u", "--qc-api-token", "t"])
        assert rc == 1

    def test_compile_error_returns_1(self, tmp_path: Path) -> None:
        strategy = tmp_path / "strategy.py"
        strategy.write_text("# code\n", encoding="utf-8")
        with patch.object(
            qc_rest_client,
            "run_backtest",
            side_effect=QCCompileError("Build failed"),
        ):
            rc = main(["--strategy", str(strategy),
                       "--qc-user-id", "u", "--qc-api-token", "t"])
        assert rc == 1

    def test_timeout_error_returns_1(self, tmp_path: Path) -> None:
        strategy = tmp_path / "strategy.py"
        strategy.write_text("# code\n", encoding="utf-8")
        with patch.object(
            qc_rest_client,
            "run_backtest",
            side_effect=QCTimeoutError("Timed out"),
        ):
            rc = main(["--strategy", str(strategy),
                       "--qc-user-id", "u", "--qc-api-token", "t"])
        assert rc == 1

    def test_success_returns_0(self, tmp_path: Path) -> None:
        strategy = tmp_path / "strategy.py"
        strategy.write_text("# code\n", encoding="utf-8")
        fake_result = {
            "project_id": "1", "backtest_id": "b1",
            "sharpe_ratio": 0.9, "total_return_pct": 10.0,
            "max_drawdown_pct": -5.0, "total_trades": 10,
            "compile_state": "BuildSuccess", "backtest_status": "Completed",
            "qc_ui_url": "https://www.quantconnect.com/project/1",
            "timestamp": "2026-01-01T00:00:00Z",
        }
        with patch.object(qc_rest_client, "run_backtest", return_value=fake_result):
            rc = main(["--strategy", str(strategy),
                       "--qc-user-id", "u", "--qc-api-token", "t",
                       "--output", str(tmp_path / "out.json")])
        assert rc == 0


# ---------------------------------------------------------------------------
# Integration test — hits real QC API
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _CREDENTIALS_AVAILABLE,
    reason=(
        "QC_USER_ID and QC_API_TOKEN not set — "
        "set both env vars to run the live integration test"
    ),
)
class TestQCIntegration:
    """Live integration test against api.quantconnect.com.

    Skipped when QC credentials are absent.
    Fails loudly if credentials are present but authentication fails.
    """

    def test_backtest_sma_crossover_simple(self, tmp_path: Path) -> None:
        """End-to-end: upload sma_crossover_simple.py, compile, backtest, assert Sharpe."""
        assert _REFERENCE_STRATEGY.is_file(), (
            f"Reference strategy not found: {_REFERENCE_STRATEGY}"
        )

        output_path = Path("/tmp/backtest_result.json")

        result = run_backtest(
            _REFERENCE_STRATEGY,
            _QC_USER_ID,
            _QC_API_TOKEN,
            output_path,
        )

        # --- assertions ---

        # Project created
        assert result["project_id"].isdigit(), (
            f"project_id should be numeric: {result['project_id']!r}"
        )

        # Backtest ID is a non-empty string
        assert result["backtest_id"], "backtest_id must not be empty"

        # Sharpe ratio is a float (not None, not a string)
        assert result["sharpe_ratio"] is not None, (
            "sharpe_ratio is None — backtest may have failed. "
            f"Full result:\n{json.dumps(result, indent=2)}"
        )
        assert isinstance(result["sharpe_ratio"], float), (
            f"sharpe_ratio must be float, got {type(result['sharpe_ratio'])}: "
            f"{result['sharpe_ratio']!r}"
        )

        # Compile and backtest statuses
        assert result["compile_state"] in qc_rest_client._COMPILE_SUCCESS_STATES, (
            f"Unexpected compile_state: {result['compile_state']!r}"
        )
        assert result["backtest_status"] == "Completed", (
            f"Unexpected backtest_status: {result['backtest_status']!r}"
        )

        # QC UI URL format
        expected_url = f"https://www.quantconnect.com/project/{result['project_id']}"
        assert result["qc_ui_url"] == expected_url, (
            f"QC UI URL mismatch: {result['qc_ui_url']!r}"
        )

        # Output file written with all required keys
        assert output_path.is_file(), (
            f"/tmp/backtest_result.json was not written"
        )
        on_disk: dict = json.loads(output_path.read_text())
        required_keys = {
            "project_id", "backtest_id", "sharpe_ratio",
            "total_return_pct", "max_drawdown_pct", "total_trades",
            "compile_state", "backtest_status", "qc_ui_url", "timestamp",
        }
        missing = required_keys - on_disk.keys()
        assert not missing, f"Missing keys in output JSON: {missing}"

        print(
            f"\n[INTEGRATION] Backtest result:\n"
            f"  project_id  = {result['project_id']}\n"
            f"  backtest_id = {result['backtest_id']}\n"
            f"  sharpe_ratio= {result['sharpe_ratio']}\n"
            f"  qc_ui_url   = {result['qc_ui_url']}\n"
        )
