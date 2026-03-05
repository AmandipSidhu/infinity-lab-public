"""Tests for scripts/notify_slack.py.

Covers:
- Argument parsing: all 10 events, --test flag, missing --event
- Block Kit payload structure for each gate
- send_test_message: posts plain-text 'Hi'
- main() exit codes: 0 on success, error on missing env vars
- _post: raises RuntimeError on Slack API errors
- SLACK_BOT_TOKEN / SLACK_ACK_CHANNEL_ID missing
"""

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import notify_slack  # noqa: E402
from notify_slack import (  # noqa: E402
    EVENTS,
    GITHUB_REPO_URL,
    _FORMATTERS,
    build_parser,
    format_coding_begins,
    format_cost_alert,
    format_failure,
    format_iteration_progress,
    format_model_switch,
    format_spec_submitted,
    format_success,
    format_test_results,
    format_testing_started,
    format_timeout_warning,
    main,
    send_test_message,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOCK_OK = {"ok": True, "ts": "1111.2222"}


def _mock_resp(json_data: dict[str, Any], status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    resp.ok = status < 400
    return resp


def _parse(args: list[str]) -> Any:
    return build_parser().parse_args(args)


# ---------------------------------------------------------------------------
# EVENTS list
# ---------------------------------------------------------------------------


class TestEventsList:
    def test_has_10_events(self) -> None:
        assert len(EVENTS) == 10

    def test_expected_names(self) -> None:
        expected = {
            "spec_submitted",
            "coding_begins",
            "testing_started",
            "iteration_progress",
            "test_results",
            "model_switch",
            "cost_alert",
            "success",
            "failure",
            "timeout_warning",
        }
        assert set(EVENTS) == expected

    def test_formatters_keys_match_events(self) -> None:
        assert set(_FORMATTERS.keys()) == set(EVENTS)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


class TestArgumentParsing:
    def test_test_flag(self) -> None:
        args = _parse(["--test"])
        assert args.test is True

    def test_event_spec_submitted(self) -> None:
        args = _parse(["--event", "spec_submitted", "--strategy", "s", "--spec-path", "p"])
        assert args.event == "spec_submitted"
        assert args.strategy == "s"
        assert args.spec_path == "p"

    def test_event_coding_begins(self) -> None:
        args = _parse(["--event", "coding_begins", "--model", "gpt-4o", "--iteration", "1"])
        assert args.model == "gpt-4o"
        assert args.iteration == "1"

    def test_event_testing_started(self) -> None:
        args = _parse(["--event", "testing_started", "--test-num", "3", "--test-name", "Backtest"])
        assert args.test_num == "3"
        assert args.test_name == "Backtest"

    def test_event_iteration_progress(self) -> None:
        args = _parse(["--event", "iteration_progress", "--max-iterations", "5",
                       "--best-result", "0.8", "--current-cost", "$2", "--status", "running",
                       "--next-action", "retry"])
        assert args.max_iterations == "5"
        assert args.best_result == "0.8"
        assert args.next_action == "retry"

    def test_event_test_results(self) -> None:
        args = _parse(["--event", "test_results", "--tests-passed", "3/4",
                       "--home-tests", "pass", "--hostile-tests", "fail", "--crisis-tests", "pass"])
        assert args.tests_passed == "3/4"

    def test_event_model_switch(self) -> None:
        args = _parse(["--event", "model_switch", "--reason", "cost",
                       "--old-model", "gpt-4o", "--new-model", "gemini-2.0",
                       "--cost-impact=−30%"])
        assert args.old_model == "gpt-4o"
        assert args.new_model == "gemini-2.0"

    def test_event_cost_alert(self) -> None:
        args = _parse(["--event", "cost_alert", "--budget", "$10",
                       "--overage-pct", "120%", "--action", "pause"])
        assert args.budget == "$10"
        assert args.overage_pct == "120%"

    def test_event_success(self) -> None:
        args = _parse(["--event", "success", "--version", "v1", "--iterations-used", "3",
                       "--final-cost", "$5", "--pr-url", "http://x", "--output-path", "out/x.py"])
        assert args.version == "v1"
        assert args.pr_url == "http://x"

    def test_event_failure(self) -> None:
        args = _parse(["--event", "failure", "--best-result", "0.5",
                       "--reason", "timeout", "--checkpoint-saved", "--log-url", "http://logs"])
        assert args.checkpoint_saved is True
        assert args.log_url == "http://logs"

    def test_event_timeout_warning(self) -> None:
        args = _parse(["--event", "timeout_warning", "--elapsed", "4h", "--remaining", "30m"])
        assert args.elapsed == "4h"
        assert args.remaining == "30m"

    def test_invalid_event_exits(self) -> None:
        with pytest.raises(SystemExit):
            _parse(["--event", "nonexistent_event"])


# ---------------------------------------------------------------------------
# Block Kit payload structure
# ---------------------------------------------------------------------------


class TestFormatSpecSubmitted:
    def test_returns_blocks(self) -> None:
        args = _parse(["--event", "spec_submitted", "--strategy", "VWAP",
                       "--spec-path", "specs/vwap.yaml",
                       "--linear-id", "UNI-75", "--github-issue", "42"])
        with patch.object(notify_slack, "_channel", return_value="C123"):
            payload = format_spec_submitted(args)
        assert payload["channel"] == "C123"
        assert payload["blocks"][0]["text"]["text"] == "📋 Spec Submitted"
        fields = payload["blocks"][1]["fields"]
        assert any("VWAP" in f["text"] for f in fields)
        assert any("UNI-75" in f["text"] for f in fields)
        assert any("#42" in f["text"] for f in fields)

    def test_missing_linear_renders_na(self) -> None:
        args = _parse(["--event", "spec_submitted", "--strategy", "S",
                       "--spec-path", "p"])
        with patch.object(notify_slack, "_channel", return_value="C123"):
            payload = format_spec_submitted(args)
        fields = payload["blocks"][1]["fields"]
        assert any(f["text"] == "*Linear:*\nN/A" for f in fields)

    def test_github_url_uses_constant(self) -> None:
        args = _parse(["--event", "spec_submitted", "--strategy", "S",
                       "--spec-path", "p", "--github-issue", "99"])
        with patch.object(notify_slack, "_channel", return_value="C123"):
            payload = format_spec_submitted(args)
        fields = payload["blocks"][1]["fields"]
        assert any(GITHUB_REPO_URL in f["text"] for f in fields)


class TestFormatCodingBegins:
    def test_contains_model_and_iteration(self) -> None:
        args = _parse(["--event", "coding_begins", "--model", "gemini-2.0-flash",
                       "--iteration", "2", "--run-url", "http://run"])
        with patch.object(notify_slack, "_channel", return_value="C"):
            payload = format_coding_begins(args)
        fields = payload["blocks"][1]["fields"]
        assert any("gemini-2.0-flash" in f["text"] for f in fields)
        assert any("2" in f["text"] for f in fields)


class TestFormatTestingStarted:
    def test_has_qc_button(self) -> None:
        args = _parse(["--event", "testing_started", "--test-num", "1",
                       "--test-name", "Home", "--qc-project-url", "http://qc"])
        with patch.object(notify_slack, "_channel", return_value="C"):
            payload = format_testing_started(args)
        actions = payload["blocks"][2]
        assert actions["type"] == "actions"
        assert actions["elements"][0]["url"] == "http://qc"


class TestFormatIterationProgress:
    def test_next_action_in_context(self) -> None:
        args = _parse(["--event", "iteration_progress", "--max-iterations", "5",
                       "--best-result", "0.7", "--current-cost", "$3",
                       "--status", "ok", "--next-action", "continue"])
        with patch.object(notify_slack, "_channel", return_value="C"):
            payload = format_iteration_progress(args)
        ctx = payload["blocks"][2]["elements"][0]["text"]
        assert "continue" in ctx


class TestFormatTestResults:
    def test_all_four_counts_in_fields(self) -> None:
        args = _parse(["--event", "test_results", "--tests-passed", "3/4",
                       "--home-tests", "pass", "--hostile-tests", "fail", "--crisis-tests", "na"])
        with patch.object(notify_slack, "_channel", return_value="C"):
            payload = format_test_results(args)
        texts = " ".join(f["text"] for f in payload["blocks"][1]["fields"])
        assert "3/4" in texts and "pass" in texts and "fail" in texts


class TestFormatModelSwitch:
    def test_old_new_model_present(self) -> None:
        args = _parse(["--event", "model_switch", "--reason", "rate-limit",
                       "--old-model", "gpt-4o", "--new-model", "claude-3", "--cost-impact", "+10%"])
        with patch.object(notify_slack, "_channel", return_value="C"):
            payload = format_model_switch(args)
        texts = " ".join(f["text"] for f in payload["blocks"][1]["fields"])
        assert "gpt-4o" in texts and "claude-3" in texts


class TestFormatCostAlert:
    def test_overage_present(self) -> None:
        args = _parse(["--event", "cost_alert", "--budget", "$20",
                       "--overage-pct", "150%", "--action", "abort"])
        with patch.object(notify_slack, "_channel", return_value="C"):
            payload = format_cost_alert(args)
        texts = " ".join(f["text"] for f in payload["blocks"][1]["fields"])
        assert "150%" in texts


class TestFormatSuccess:
    def test_pr_button_url(self) -> None:
        args = _parse(["--event", "success", "--version", "v2",
                       "--iterations-used", "4", "--final-cost", "$8",
                       "--pr-url", "http://pr", "--output-path", "strategies/x.py"])
        with patch.object(notify_slack, "_channel", return_value="C"):
            payload = format_success(args)
        assert payload["blocks"][2]["elements"][0]["url"] == "http://pr"


class TestFormatFailure:
    def test_checkpoint_yes_when_flag_set(self) -> None:
        args = _parse(["--event", "failure", "--best-result", "0.3",
                       "--reason", "bug", "--checkpoint-saved", "--log-url", "http://log"])
        with patch.object(notify_slack, "_channel", return_value="C"):
            payload = format_failure(args)
        texts = " ".join(f["text"] for f in payload["blocks"][1]["fields"])
        assert "Yes" in texts

    def test_checkpoint_no_when_flag_absent(self) -> None:
        args = _parse(["--event", "failure", "--best-result", "0.3",
                       "--reason", "bug"])
        with patch.object(notify_slack, "_channel", return_value="C"):
            payload = format_failure(args)
        texts = " ".join(f["text"] for f in payload["blocks"][1]["fields"])
        assert "No" in texts


class TestFormatTimeoutWarning:
    def test_elapsed_remaining_in_fields(self) -> None:
        args = _parse(["--event", "timeout_warning", "--elapsed", "5h", "--remaining", "10m"])
        with patch.object(notify_slack, "_channel", return_value="C"):
            payload = format_timeout_warning(args)
        texts = " ".join(f["text"] for f in payload["blocks"][1]["fields"])
        assert "5h" in texts and "10m" in texts


# ---------------------------------------------------------------------------
# _post error handling
# ---------------------------------------------------------------------------


class TestPost:
    def test_raises_on_slack_error(self) -> None:
        bad_resp = _mock_resp({"ok": False, "error": "channel_not_found"})
        with patch("requests.post", return_value=bad_resp):
            with patch.object(notify_slack, "_token", return_value="tok"):
                with pytest.raises(RuntimeError, match="channel_not_found"):
                    notify_slack._post({"channel": "C", "text": "hi"})

    def test_raises_on_http_error(self) -> None:
        bad_resp = _mock_resp({}, status=500)
        bad_resp.raise_for_status.side_effect = Exception("500 Server Error")
        with patch("requests.post", return_value=bad_resp):
            with patch.object(notify_slack, "_token", return_value="tok"):
                with pytest.raises(Exception, match="500"):
                    notify_slack._post({"channel": "C", "text": "hi"})


# ---------------------------------------------------------------------------
# send_test_message
# ---------------------------------------------------------------------------


class TestSendTestMessage:
    def test_sends_hi_to_channel(self) -> None:
        with patch.object(notify_slack, "_channel", return_value="C_TEST"):
            with patch.object(notify_slack, "_post") as mock_post:
                send_test_message("Hi")
        mock_post.assert_called_once_with({"channel": "C_TEST", "text": "Hi"})

    def test_default_text_is_hi(self) -> None:
        with patch.object(notify_slack, "_channel", return_value="C_TEST"):
            with patch.object(notify_slack, "_post") as mock_post:
                send_test_message()
        call_args = mock_post.call_args[0][0]
        assert call_args["text"] == "Hi"


# ---------------------------------------------------------------------------
# main() exit codes
# ---------------------------------------------------------------------------


class TestMain:
    def test_test_flag_exits_zero(self) -> None:
        with patch.object(notify_slack, "send_test_message"):
            rc = main(["--test"])
        assert rc == 0

    def test_no_event_exits_nonzero(self) -> None:
        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code != 0

    def test_spec_submitted_exits_zero(self) -> None:
        with patch.object(notify_slack, "_post"):
            with patch.object(notify_slack, "_channel", return_value="C"):
                rc = main(["--event", "spec_submitted",
                           "--strategy", "VWAP",
                           "--spec-path", "specs/vwap.yaml"])
        assert rc == 0


# ---------------------------------------------------------------------------
# Missing env-var guards
# ---------------------------------------------------------------------------


class TestEnvVarGuards:
    def test_missing_token_raises(self) -> None:
        import os

        env = {k: v for k, v in os.environ.items() if k != "SLACK_BOT_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(EnvironmentError, match="SLACK_BOT_TOKEN"):
                notify_slack._token()

    def test_missing_channel_raises(self) -> None:
        import os

        env = {k: v for k, v in os.environ.items() if k != "SLACK_ACK_CHANNEL_ID"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(EnvironmentError, match="SLACK_ACK_CHANNEL_ID"):
                notify_slack._channel()
