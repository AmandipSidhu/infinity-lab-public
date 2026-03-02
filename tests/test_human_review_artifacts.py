"""Tests for scripts/human_review_artifacts.py.

Covers:
- build_step_summary: Markdown output with pre-commit and QC data
- write_step_summary: writes to GITHUB_STEP_SUMMARY env path
- post_pr_comment: posts to GitHub API when token/repo/PR available
- _get_pr_number: extracts PR number from GITHUB_REF and event payload
- CLI: exit codes, argument handling
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import human_review_artifacts  # noqa: E402
from human_review_artifacts import (  # noqa: E402
    _get_pr_number,
    build_step_summary,
    main,
    post_pr_comment,
    write_step_summary,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PRE_COMMIT_PASS: dict = {
    "strategy_file": "strategies/my_strat.py",
    "result": "PASS",
    "error_count": 0,
    "violation_count": 0,
    "violations": [],
}

_PRE_COMMIT_FAIL: dict = {
    "strategy_file": "strategies/my_strat.py",
    "result": "FAIL",
    "error_count": 2,
    "violation_count": 3,
    "violations": [
        {"check": "radon_ccn", "severity": "ERROR", "message": "CCN=12 exceeds threshold"},
        {"check": "bandit", "severity": "ERROR", "message": "[B301] Pickle usage"},
        {"check": "ast_param_count", "severity": "WARNING", "message": "5 params"},
    ],
}

_QC_PASS: dict = {
    "spec_file": "specs/my_strat.yaml",
    "strategy_file": "strategies/my_strat.py",
    "project_id": 42,
    "backtest_id": "bt123",
    "result": "PASS",
    "violation_count": 0,
    "violations": [],
    "backtest_stats": {"SharpeRatio": "1.5", "Drawdown": "0.12"},
}

_QC_FAIL: dict = {
    "spec_file": "specs/my_strat.yaml",
    "strategy_file": "strategies/my_strat.py",
    "project_id": 1,
    "backtest_id": "bt1",
    "result": "FAIL",
    "violation_count": 1,
    "violations": [
        {
            "constraint": "sharpe_ratio",
            "severity": "ERROR",
            "message": "Sharpe 0.3 < required 0.5",
            "required": 0.5,
            "actual": 0.3,
        }
    ],
    "backtest_stats": {"SharpeRatio": "0.3"},
}


# ---------------------------------------------------------------------------
# build_step_summary
# ---------------------------------------------------------------------------


class TestBuildStepSummary:
    def test_contains_spec_file_name(self) -> None:
        content = build_step_summary("specs/test.yaml", None, None)
        assert "specs/test.yaml" in content

    def test_pass_pre_commit_shows_pass(self) -> None:
        content = build_step_summary("specs/test.yaml", _PRE_COMMIT_PASS, None)
        assert "PASS" in content
        assert "All quality gate checks passed" in content

    def test_fail_pre_commit_shows_violations_table(self) -> None:
        content = build_step_summary("specs/test.yaml", _PRE_COMMIT_FAIL, None)
        assert "FAIL" in content
        assert "radon_ccn" in content
        assert "bandit" in content

    def test_pass_qc_shows_pass(self) -> None:
        content = build_step_summary("specs/test.yaml", None, _QC_PASS)
        assert "PASS" in content
        assert "SharpeRatio" in content

    def test_fail_qc_shows_constraint_violations(self) -> None:
        content = build_step_summary("specs/test.yaml", None, _QC_FAIL)
        assert "FAIL" in content
        assert "sharpe_ratio" in content

    def test_none_data_shows_warning(self) -> None:
        content = build_step_summary("specs/test.yaml", None, None)
        assert "not available" in content

    def test_overall_pass_when_both_pass(self) -> None:
        content = build_step_summary("specs/test.yaml", _PRE_COMMIT_PASS, _QC_PASS)
        assert "Overall pipeline result" in content
        assert "✅" in content

    def test_overall_fail_when_pre_commit_fails(self) -> None:
        content = build_step_summary("specs/test.yaml", _PRE_COMMIT_FAIL, _QC_PASS)
        assert "Overall pipeline result" in content
        assert "❌" in content

    def test_overall_fail_when_qc_fails(self) -> None:
        content = build_step_summary("specs/test.yaml", _PRE_COMMIT_PASS, _QC_FAIL)
        assert "Overall pipeline result" in content
        assert "❌" in content

    def test_pipe_characters_in_messages_are_escaped(self) -> None:
        data_with_pipe = {
            **_PRE_COMMIT_FAIL,
            "violations": [
                {
                    "check": "radon_ccn",
                    "severity": "ERROR",
                    "message": "a | b | c",
                }
            ],
        }
        content = build_step_summary("specs/test.yaml", data_with_pipe, None)
        # The pipe chars in the message should be escaped to avoid breaking the table
        assert "a \\| b \\| c" in content


# ---------------------------------------------------------------------------
# write_step_summary
# ---------------------------------------------------------------------------


class TestWriteStepSummary:
    def test_writes_to_summary_file(self, tmp_path: Path) -> None:
        summary_file = tmp_path / "step_summary.md"
        summary_file.touch()
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary_file)}):
            write_step_summary("## Hello\n")
        assert "## Hello" in summary_file.read_text()

    def test_appends_to_existing_content(self, tmp_path: Path) -> None:
        summary_file = tmp_path / "step_summary.md"
        summary_file.write_text("# Existing\n", encoding="utf-8")
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary_file)}):
            write_step_summary("## New\n")
        content = summary_file.read_text()
        assert "# Existing" in content
        assert "## New" in content

    def test_no_error_when_env_not_set(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"}
        with patch.dict(os.environ, env, clear=True):
            # Should not raise
            write_step_summary("## Hello\n")


# ---------------------------------------------------------------------------
# _get_pr_number
# ---------------------------------------------------------------------------


class TestGetPrNumber:
    def test_extracts_from_github_ref(self) -> None:
        with patch.dict(os.environ, {"GITHUB_REF": "refs/pull/42/merge"}, clear=False):
            assert _get_pr_number() == 42

    def test_returns_none_for_push_ref(self) -> None:
        env = {**os.environ, "GITHUB_REF": "refs/heads/main"}
        env.pop("GITHUB_EVENT_PATH", None)
        with patch.dict(os.environ, env, clear=True):
            assert _get_pr_number() is None

    def test_extracts_from_event_payload(self, tmp_path: Path) -> None:
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps({"pull_request": {"number": 99}}), encoding="utf-8")
        env = {
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_EVENT_PATH": str(event_file),
        }
        with patch.dict(os.environ, env, clear=False):
            assert _get_pr_number() == 99

    def test_returns_none_when_event_missing_pr(self, tmp_path: Path) -> None:
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps({"action": "push"}), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_REF", "GITHUB_EVENT_PATH")}
        env["GITHUB_EVENT_PATH"] = str(event_file)
        with patch.dict(os.environ, env, clear=True):
            assert _get_pr_number() is None


# ---------------------------------------------------------------------------
# post_pr_comment
# ---------------------------------------------------------------------------


class TestPostPrComment:
    def test_posts_comment_when_env_is_set(self) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        env = {
            "GITHUB_TOKEN": "token123",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_REF": "refs/pull/7/merge",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch("requests.post", return_value=mock_response) as mock_post:
                post_pr_comment("## Test\n")

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "owner/repo" in call_kwargs[0][0]
        assert call_kwargs[1]["json"]["body"] == "## Test\n"

    def test_skips_when_no_pr_number(self) -> None:
        env = {
            "GITHUB_TOKEN": "token123",
            "GITHUB_REPOSITORY": "owner/repo",
        }
        # Remove any PR-related env vars
        clean_env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_REF", "GITHUB_EVENT_PATH")}
        clean_env.update(env)
        with patch.dict(os.environ, clean_env, clear=True):
            with patch("requests.post") as mock_post:
                post_pr_comment("## Test\n")
        mock_post.assert_not_called()

    def test_skips_when_no_token(self) -> None:
        clean_env = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}
        clean_env["GITHUB_REPOSITORY"] = "owner/repo"
        clean_env["GITHUB_REF"] = "refs/pull/1/merge"
        with patch.dict(os.environ, clean_env, clear=True):
            with patch("requests.post") as mock_post:
                post_pr_comment("## Test\n")
        mock_post.assert_not_called()

    def test_does_not_raise_on_request_failure(self) -> None:
        import requests as req

        env = {
            "GITHUB_TOKEN": "token123",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_REF": "refs/pull/1/merge",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch("requests.post", side_effect=req.ConnectionError("fail")):
                # Should not raise; warning is printed
                post_pr_comment("## Test\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    def test_no_args_exits_nonzero(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code != 0

    def test_spec_only_returns_0(self, tmp_path: Path) -> None:
        with patch.object(human_review_artifacts, "write_step_summary"):
            with patch.object(human_review_artifacts, "post_pr_comment"):
                rc = main(["--spec", "specs/test.yaml"])
        assert rc == 0

    def test_loads_pre_commit_output_from_file(self, tmp_path: Path) -> None:
        pre_commit_file = tmp_path / "pre_commit.json"
        pre_commit_file.write_text(json.dumps(_PRE_COMMIT_PASS), encoding="utf-8")

        captured: list[str] = []

        with patch.object(
            human_review_artifacts,
            "write_step_summary",
            side_effect=lambda c: captured.append(c),
        ):
            with patch.object(human_review_artifacts, "post_pr_comment"):
                rc = main([
                    "--spec", "specs/test.yaml",
                    "--pre-commit-output", str(pre_commit_file),
                ])

        assert rc == 0
        assert len(captured) == 1
        assert "All quality gate checks passed" in captured[0]

    def test_loads_qc_output_from_file(self, tmp_path: Path) -> None:
        qc_file = tmp_path / "qc.json"
        qc_file.write_text(json.dumps(_QC_PASS), encoding="utf-8")

        captured: list[str] = []

        with patch.object(
            human_review_artifacts,
            "write_step_summary",
            side_effect=lambda c: captured.append(c),
        ):
            with patch.object(human_review_artifacts, "post_pr_comment"):
                rc = main([
                    "--spec", "specs/test.yaml",
                    "--qc-output", str(qc_file),
                ])

        assert rc == 0
        assert "SharpeRatio" in captured[0]

    def test_always_returns_0(self, tmp_path: Path) -> None:
        """Step 7 should never block the pipeline with a non-zero exit."""
        with patch.object(human_review_artifacts, "write_step_summary"):
            with patch.object(human_review_artifacts, "post_pr_comment"):
                rc = main(["--spec", "specs/test.yaml"])
        assert rc == 0
