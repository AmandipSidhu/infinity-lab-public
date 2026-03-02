"""Tests for scripts/pre_commit_gates.py, scripts/qc_upload_eval.py,
and scripts/human_review_notify.py.

Covers:
- pre_commit_gates: flake8 pass/fail, black pass/fail, missing directory,
  no Python files, run_gates aggregate logic, CLI
- qc_upload_eval: dummy result when credentials absent, API flow (upload,
  compile, backtest), network errors, strategy file not found, _extract_stats,
  _dummy_result, CLI
- human_review_notify: build_summary content, _load_qc_result edge cases,
  notify success, notify Slack failure, notify missing channel, CLI
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest
import requests

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import pre_commit_gates  # noqa: E402
import qc_upload_eval  # noqa: E402
import human_review_notify  # noqa: E402

from pre_commit_gates import main as gates_main, run_gates  # noqa: E402
from qc_upload_eval import (  # noqa: E402
    _dummy_result,
    _extract_stats,
    main as qc_main,
    run_backtest,
)
from human_review_notify import (  # noqa: E402
    _load_qc_result,
    build_summary,
    main as notify_main,
    notify,
)


# ===========================================================================
# pre_commit_gates
# ===========================================================================


class TestRunFlake8:
    def test_pass_on_clean_file(self, tmp_path: Path) -> None:
        (tmp_path / "strategy.py").write_text(
            'x = 1\n', encoding="utf-8"
        )
        with patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout="", stderr=""),
        ):
            passed, output = pre_commit_gates.run_flake8(tmp_path)
        assert passed is True

    def test_fail_on_flake8_errors(self, tmp_path: Path) -> None:
        (tmp_path / "strategy.py").write_text(
            'import os,sys\n', encoding="utf-8"
        )
        with patch(
            "subprocess.run",
            return_value=MagicMock(
                returncode=1, stdout="strategy.py:1:1: E401 multiple imports\n", stderr=""
            ),
        ):
            passed, output = pre_commit_gates.run_flake8(tmp_path)
        assert passed is False
        assert "E401" in output

    def test_no_python_files_returns_true(self, tmp_path: Path) -> None:
        passed, output = pre_commit_gates.run_flake8(tmp_path)
        assert passed is True
        assert output == ""


class TestRunBlackCheck:
    def test_pass_on_formatted_file(self, tmp_path: Path) -> None:
        (tmp_path / "strategy.py").write_text('x = 1\n', encoding="utf-8")
        with patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout="", stderr=""),
        ):
            passed, output = pre_commit_gates.run_black_check(tmp_path)
        assert passed is True

    def test_fail_on_unformatted_file(self, tmp_path: Path) -> None:
        (tmp_path / "strategy.py").write_text('x=1\n', encoding="utf-8")
        with patch(
            "subprocess.run",
            return_value=MagicMock(
                returncode=1,
                stdout="",
                stderr="would reformat strategy.py\n",
            ),
        ):
            passed, output = pre_commit_gates.run_black_check(tmp_path)
        assert passed is False
        assert "reformat" in output

    def test_no_python_files_returns_true(self, tmp_path: Path) -> None:
        passed, output = pre_commit_gates.run_black_check(tmp_path)
        assert passed is True
        assert output == ""


class TestRunGates:
    def test_all_pass_returns_0(self, tmp_path: Path) -> None:
        (tmp_path / "s.py").write_text('x = 1\n', encoding="utf-8")
        with patch.object(pre_commit_gates, "run_flake8", return_value=(True, "")):
            with patch.object(pre_commit_gates, "run_black_check", return_value=(True, "")):
                assert run_gates(str(tmp_path)) == 0

    def test_flake8_fail_returns_1(self, tmp_path: Path) -> None:
        (tmp_path / "s.py").write_text('x = 1\n', encoding="utf-8")
        with patch.object(pre_commit_gates, "run_flake8", return_value=(False, "error")):
            with patch.object(pre_commit_gates, "run_black_check", return_value=(True, "")):
                assert run_gates(str(tmp_path)) == 1

    def test_black_fail_returns_1(self, tmp_path: Path) -> None:
        (tmp_path / "s.py").write_text('x = 1\n', encoding="utf-8")
        with patch.object(pre_commit_gates, "run_flake8", return_value=(True, "")):
            with patch.object(pre_commit_gates, "run_black_check", return_value=(False, "diff")):
                assert run_gates(str(tmp_path)) == 1

    def test_both_fail_returns_1(self, tmp_path: Path) -> None:
        (tmp_path / "s.py").write_text('x = 1\n', encoding="utf-8")
        with patch.object(pre_commit_gates, "run_flake8", return_value=(False, "e")):
            with patch.object(pre_commit_gates, "run_black_check", return_value=(False, "e")):
                assert run_gates(str(tmp_path)) == 1

    def test_missing_directory_returns_1(self) -> None:
        assert run_gates("/nonexistent/path/to/strategies") == 1

    def test_empty_directory_returns_0(self, tmp_path: Path) -> None:
        assert run_gates(str(tmp_path)) == 0


class TestPreCommitGatesCLI:
    def test_default_dir_arg_used(self, tmp_path: Path) -> None:
        with patch.object(pre_commit_gates, "run_gates", return_value=0) as mock_run:
            rc = gates_main(["--dir", str(tmp_path)])
        assert rc == 0
        mock_run.assert_called_once_with(str(tmp_path))

    def test_default_dir_is_strategies(self) -> None:
        with patch.object(pre_commit_gates, "run_gates", return_value=0) as mock_run:
            gates_main([])
        mock_run.assert_called_once_with("strategies")


# ===========================================================================
# qc_upload_eval
# ===========================================================================


class TestDummyResult:
    def test_returns_passing_dict(self) -> None:
        result = _dummy_result("test_reason")
        assert result["passed"] is True
        assert result["status"] == "dummy"
        assert result["reason"] == "test_reason"
        assert result["sharpe_ratio"] > 0

    def test_has_required_keys(self) -> None:
        result = _dummy_result("x")
        for key in ("sharpe_ratio", "total_trades", "win_rate", "annual_return", "max_drawdown"):
            assert key in result


class TestExtractStats:
    def test_parses_full_stats(self) -> None:
        backtest = {
            "statistics": {
                "Sharpe Ratio": "1.5",
                "Total Trades": "200",
                "Win Rate": "60%",
                "Compounding Annual Return": "25%",
                "Drawdown": "12%",
                "Net Profit": "45%",
            }
        }
        stats = _extract_stats(backtest)
        assert stats["sharpe_ratio"] == 1.5
        assert stats["total_trades"] == 200
        assert stats["win_rate"] == pytest.approx(0.60)
        assert stats["annual_return"] == pytest.approx(0.25)
        assert stats["max_drawdown"] == pytest.approx(0.12)

    def test_missing_statistics_returns_zeros(self) -> None:
        stats = _extract_stats({})
        assert stats["sharpe_ratio"] == 0.0
        assert stats["total_trades"] == 0


class TestRunBacktestMissingCredentials:
    def test_missing_all_creds_writes_dummy_and_returns_0(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result_path = tmp_path / "qc_result.json"
        monkeypatch.setattr(qc_upload_eval, "_RESULT_PATH", result_path)
        monkeypatch.delenv("QUANTCONNECT_USER_ID", raising=False)
        monkeypatch.delenv("QUANTCONNECT_API_KEY", raising=False)
        monkeypatch.delenv("QUANTCONNECT_PROJECT_ID", raising=False)

        rc = run_backtest("strategies/some_strategy.py")

        assert rc == 0
        assert result_path.exists()
        data = json.loads(result_path.read_text())
        assert data["status"] == "dummy"
        assert data["passed"] is True

    def test_partial_missing_creds_writes_dummy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result_path = tmp_path / "qc_result.json"
        monkeypatch.setattr(qc_upload_eval, "_RESULT_PATH", result_path)
        monkeypatch.setenv("QUANTCONNECT_USER_ID", "12345")
        monkeypatch.delenv("QUANTCONNECT_API_KEY", raising=False)
        monkeypatch.setenv("QUANTCONNECT_PROJECT_ID", "99999")

        rc = run_backtest("strategies/some_strategy.py")

        assert rc == 0
        data = json.loads(result_path.read_text())
        assert data["status"] == "dummy"


class TestRunBacktestStrategyNotFound:
    def test_missing_strategy_file_returns_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result_path = tmp_path / "qc_result.json"
        monkeypatch.setattr(qc_upload_eval, "_RESULT_PATH", result_path)
        monkeypatch.setenv("QUANTCONNECT_USER_ID", "1")
        monkeypatch.setenv("QUANTCONNECT_API_KEY", "key")
        monkeypatch.setenv("QUANTCONNECT_PROJECT_ID", "2")

        rc = run_backtest("/nonexistent/strategy.py")

        assert rc == 1
        data = json.loads(result_path.read_text())
        assert data["passed"] is False


class TestRunBacktestFullFlow:
    def _setup_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QUANTCONNECT_USER_ID", "42")
        monkeypatch.setenv("QUANTCONNECT_API_KEY", "testkey")
        monkeypatch.setenv("QUANTCONNECT_PROJECT_ID", "9999")

    def test_successful_backtest_returns_0(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._setup_env(monkeypatch)
        result_path = tmp_path / "qc_result.json"
        monkeypatch.setattr(qc_upload_eval, "_RESULT_PATH", result_path)

        strategy = tmp_path / "my_strategy.py"
        strategy.write_text("# strategy\n", encoding="utf-8")

        backtest_data = {
            "progress": 1.0,
            "statistics": {
                "Sharpe Ratio": "1.8",
                "Total Trades": "300",
                "Win Rate": "62%",
                "Compounding Annual Return": "30%",
                "Drawdown": "10%",
                "Net Profit": "50%",
            },
        }

        with patch.object(qc_upload_eval, "upload_strategy_file", return_value={}):
            with patch.object(qc_upload_eval, "compile_project", return_value="compile_abc"):
                with patch.object(
                    qc_upload_eval, "wait_for_compile", return_value=True
                ):
                    with patch.object(
                        qc_upload_eval, "create_backtest", return_value="bt_xyz"
                    ):
                        with patch.object(
                            qc_upload_eval, "poll_backtest_result", return_value=backtest_data
                        ):
                            rc = run_backtest(str(strategy))

        assert rc == 0
        data = json.loads(result_path.read_text())
        assert data["passed"] is True
        assert data["sharpe_ratio"] == pytest.approx(1.8)

    def test_compile_failure_returns_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._setup_env(monkeypatch)
        result_path = tmp_path / "qc_result.json"
        monkeypatch.setattr(qc_upload_eval, "_RESULT_PATH", result_path)

        strategy = tmp_path / "my_strategy.py"
        strategy.write_text("# strategy\n", encoding="utf-8")

        with patch.object(qc_upload_eval, "upload_strategy_file", return_value={}):
            with patch.object(qc_upload_eval, "compile_project", return_value="cid"):
                with patch.object(qc_upload_eval, "wait_for_compile", return_value=False):
                    rc = run_backtest(str(strategy))

        assert rc == 1
        data = json.loads(result_path.read_text())
        assert data["status"] == "compile_failed"

    def test_low_sharpe_returns_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._setup_env(monkeypatch)
        result_path = tmp_path / "qc_result.json"
        monkeypatch.setattr(qc_upload_eval, "_RESULT_PATH", result_path)

        strategy = tmp_path / "my_strategy.py"
        strategy.write_text("# strategy\n", encoding="utf-8")

        backtest_data = {
            "progress": 1.0,
            "statistics": {
                "Sharpe Ratio": "0.1",
                "Total Trades": "50",
                "Win Rate": "40%",
                "Compounding Annual Return": "-5%",
                "Drawdown": "30%",
                "Net Profit": "-10%",
            },
        }

        with patch.object(qc_upload_eval, "upload_strategy_file", return_value={}):
            with patch.object(qc_upload_eval, "compile_project", return_value="cid"):
                with patch.object(qc_upload_eval, "wait_for_compile", return_value=True):
                    with patch.object(qc_upload_eval, "create_backtest", return_value="bid"):
                        with patch.object(
                            qc_upload_eval, "poll_backtest_result", return_value=backtest_data
                        ):
                            rc = run_backtest(str(strategy))

        assert rc == 1
        data = json.loads(result_path.read_text())
        assert data["passed"] is False

    def test_network_error_returns_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._setup_env(monkeypatch)
        result_path = tmp_path / "qc_result.json"
        monkeypatch.setattr(qc_upload_eval, "_RESULT_PATH", result_path)

        strategy = tmp_path / "my_strategy.py"
        strategy.write_text("# strategy\n", encoding="utf-8")

        with patch.object(
            qc_upload_eval,
            "upload_strategy_file",
            side_effect=requests.ConnectionError("refused"),
        ):
            rc = run_backtest(str(strategy))

        assert rc == 1
        data = json.loads(result_path.read_text())
        assert data["status"] == "network_error"

    def test_api_error_returns_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._setup_env(monkeypatch)
        result_path = tmp_path / "qc_result.json"
        monkeypatch.setattr(qc_upload_eval, "_RESULT_PATH", result_path)

        strategy = tmp_path / "my_strategy.py"
        strategy.write_text("# strategy\n", encoding="utf-8")

        with patch.object(
            qc_upload_eval,
            "upload_strategy_file",
            side_effect=RuntimeError("API quota exceeded"),
        ):
            rc = run_backtest(str(strategy))

        assert rc == 1
        data = json.loads(result_path.read_text())
        assert data["status"] == "api_error"


class TestQcUploadEvalCLI:
    def test_strategy_flag_required(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            qc_main([])
        assert exc_info.value.code != 0

    def test_strategy_flag_passed_to_run_backtest(self) -> None:
        with patch.object(qc_upload_eval, "run_backtest", return_value=0) as mock_run:
            rc = qc_main(["--strategy", "strategies/foo.py"])
        assert rc == 0
        mock_run.assert_called_once_with("strategies/foo.py")


# ===========================================================================
# human_review_notify
# ===========================================================================


class TestLoadQcResult:
    def test_returns_dummy_when_file_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            human_review_notify, "_QC_RESULT_PATH", Path("/nonexistent/qc_result.json")
        )
        result = _load_qc_result()
        assert result["status"] == "not_run"
        assert result["passed"] is True

    def test_returns_parsed_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "qc_result.json"
        payload = {"status": "complete", "passed": True, "sharpe_ratio": 1.5}
        path.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setattr(human_review_notify, "_QC_RESULT_PATH", path)
        result = _load_qc_result()
        assert result["sharpe_ratio"] == 1.5

    def test_returns_error_on_invalid_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "qc_result.json"
        path.write_text("not valid json", encoding="utf-8")
        monkeypatch.setattr(human_review_notify, "_QC_RESULT_PATH", path)
        result = _load_qc_result()
        assert result["status"] == "parse_error"
        assert result["passed"] is False


class TestBuildSummary:
    _qc_pass = {
        "status": "complete",
        "passed": True,
        "sharpe_ratio": 1.5,
        "annual_return": 0.25,
        "total_trades": 200,
        "win_rate": 0.60,
        "max_drawdown": 0.10,
    }
    _qc_fail = {
        "status": "complete",
        "passed": False,
        "sharpe_ratio": 0.2,
        "reason": "Sharpe below threshold",
    }

    def test_overall_pass_shows_green(self) -> None:
        summary = build_summary("specs/s.yaml", "success", "success", self._qc_pass)
        assert "🟢" in summary

    def test_overall_fail_shows_red_when_aider_fails(self) -> None:
        summary = build_summary("specs/s.yaml", "failure", "success", self._qc_pass)
        assert "🔴" in summary

    def test_overall_fail_shows_red_when_pre_commit_fails(self) -> None:
        summary = build_summary("specs/s.yaml", "success", "failure", self._qc_pass)
        assert "🔴" in summary

    def test_overall_fail_shows_red_when_qc_fails(self) -> None:
        summary = build_summary("specs/s.yaml", "success", "success", self._qc_fail)
        assert "🔴" in summary

    def test_spec_file_in_summary(self) -> None:
        summary = build_summary("specs/my_strategy.yaml", "success", "success", self._qc_pass)
        assert "my_strategy.yaml" in summary

    def test_all_step_statuses_present(self) -> None:
        summary = build_summary("specs/s.yaml", "success", "failure", self._qc_pass)
        assert "Aider Build" in summary
        assert "Pre-Commit Gates" in summary
        assert "QC Backtest" in summary

    def test_sharpe_ratio_shown_for_complete_backtest(self) -> None:
        summary = build_summary("specs/s.yaml", "success", "success", self._qc_pass)
        assert "1.50" in summary

    def test_failure_reason_shown_when_qc_fails(self) -> None:
        summary = build_summary("specs/s.yaml", "success", "success", self._qc_fail)
        assert "Sharpe below threshold" in summary

    def test_ack_instruction_always_present(self) -> None:
        summary = build_summary("specs/s.yaml", "success", "success", self._qc_pass)
        assert "ACK" in summary


class TestNotify:
    def test_success_posts_to_slack_and_returns_0(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SLACK_ACK_CHANNEL_ID", "C_TEST")
        monkeypatch.setenv("AIDER_BUILD_STATUS", "success")
        monkeypatch.setenv("PRE_COMMIT_STATUS", "success")
        monkeypatch.setattr(
            human_review_notify,
            "_QC_RESULT_PATH",
            Path("/nonexistent/qc_result.json"),
        )

        with patch.object(
            human_review_notify,
            "post_message",
            return_value={"ok": True, "ts": "ts.123"},
        ) as mock_post:
            rc = notify("specs/s.yaml")

        assert rc == 0
        mock_post.assert_called_once()
        channel_arg, text_arg = mock_post.call_args[0]
        assert channel_arg == "C_TEST"
        assert "ACB Pipeline" in text_arg

    def test_missing_channel_returns_1(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SLACK_ACK_CHANNEL_ID", raising=False)
        rc = notify("specs/s.yaml")
        assert rc == 1

    def test_slack_error_returns_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_ACK_CHANNEL_ID", "C_TEST")
        monkeypatch.setattr(
            human_review_notify,
            "_QC_RESULT_PATH",
            Path("/nonexistent/qc_result.json"),
        )

        with patch.object(
            human_review_notify,
            "post_message",
            side_effect=RuntimeError("Slack 500"),
        ):
            rc = notify("specs/s.yaml")

        assert rc == 1

    def test_env_error_returns_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_ACK_CHANNEL_ID", "C_TEST")
        monkeypatch.setattr(
            human_review_notify,
            "_QC_RESULT_PATH",
            Path("/nonexistent/qc_result.json"),
        )

        with patch.object(
            human_review_notify,
            "post_message",
            side_effect=EnvironmentError("SLACK_BOT_TOKEN is not set"),
        ):
            rc = notify("specs/s.yaml")

        assert rc == 1


class TestHumanReviewNotifyCLI:
    def test_spec_flag_required(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            notify_main([])
        assert exc_info.value.code != 0

    def test_spec_flag_passed_to_notify(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with patch.object(human_review_notify, "notify", return_value=0) as mock_notify:
            rc = notify_main(["--spec", "specs/foo.yaml"])
        assert rc == 0
        mock_notify.assert_called_once_with("specs/foo.yaml")
