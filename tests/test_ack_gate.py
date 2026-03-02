"""Tests for scripts/ack_gate.py and scripts/slack_api.py.

Covers:
- generate_ack_token: length, alphabet, uniqueness
- _build_summary_text: contains token, warn list, count
- poll_for_ack: ACK on first poll, ACK on later poll, timeout, wrong token ignored,
  non-ACK messages ignored, original message skipped
- run_ack_gate: empty warn list (pass-through), ACK success (audit written),
  timeout (exit 1), missing SLACK_ACK_CHANNEL_ID
- write_audit: file contents
- main (CLI): positional args, stdin, empty args
- slack_api: retry on 429, retry on 500, non-ok response, network error,
  post_message, get_replies
"""

import json
import sys
import time
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest
import requests

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import ack_gate  # noqa: E402
import slack_api  # noqa: E402
from ack_gate import (  # noqa: E402
    _ACK_PATTERN,
    _build_summary_text,
    _channel,
    generate_ack_token,
    main,
    poll_for_ack,
    run_ack_gate,
    write_audit,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

SAMPLE_WARNS = [
    "SVR-W001: metadata.author is missing",
    "SVR-W006: max_drawdown is missing",
]
TOKEN = "ABC123"


def _make_message(text: str) -> dict[str, Any]:
    return {"type": "message", "text": text, "user": "U123", "ts": "1234567890.123456"}


# ---------------------------------------------------------------------------
# generate_ack_token
# ---------------------------------------------------------------------------


class TestGenerateAckToken:
    def test_length_is_six(self) -> None:
        assert len(generate_ack_token()) == 6

    def test_chars_are_alphanumeric_uppercase(self) -> None:
        import string
        allowed = set(string.ascii_uppercase + string.digits)
        token = generate_ack_token()
        assert all(c in allowed for c in token)

    def test_two_calls_are_almost_always_different(self) -> None:
        tokens = {generate_ack_token() for _ in range(20)}
        assert len(tokens) > 1  # Statistically near-impossible to collide 20 times


# ---------------------------------------------------------------------------
# _build_summary_text
# ---------------------------------------------------------------------------


class TestBuildSummaryText:
    def test_contains_token(self) -> None:
        text = _build_summary_text(SAMPLE_WARNS, TOKEN)
        assert TOKEN in text

    def test_contains_warn_count(self) -> None:
        text = _build_summary_text(SAMPLE_WARNS, TOKEN)
        assert "2" in text

    def test_contains_each_warning(self) -> None:
        text = _build_summary_text(SAMPLE_WARNS, TOKEN)
        for w in SAMPLE_WARNS:
            assert w in text

    def test_contains_ack_instruction(self) -> None:
        text = _build_summary_text(SAMPLE_WARNS, TOKEN)
        assert f"ACK {TOKEN}" in text


# ---------------------------------------------------------------------------
# _ACK_PATTERN matching
# ---------------------------------------------------------------------------


class TestAckPattern:
    def test_exact_match(self) -> None:
        m = _ACK_PATTERN.match(f"ACK {TOKEN}")
        assert m is not None
        assert m.group(1).upper() == TOKEN

    def test_lowercase_ack(self) -> None:
        m = _ACK_PATTERN.match(f"ack {TOKEN}")
        assert m is not None

    def test_lowercase_token(self) -> None:
        m = _ACK_PATTERN.match(f"ACK {TOKEN.lower()}")
        assert m is not None
        assert m.group(1).upper() == TOKEN

    def test_leading_trailing_whitespace(self) -> None:
        m = _ACK_PATTERN.match(f"  ACK {TOKEN}  ")
        assert m is not None

    def test_wrong_length_no_match(self) -> None:
        assert _ACK_PATTERN.match("ACK AB1") is None  # 3 chars
        assert _ACK_PATTERN.match("ACK ABCDEFG") is None  # 7 chars

    def test_extra_text_no_match(self) -> None:
        assert _ACK_PATTERN.match(f"ACK {TOKEN} please") is None

    def test_not_ack_no_match(self) -> None:
        assert _ACK_PATTERN.match("APPROVE ABC123") is None
        assert _ACK_PATTERN.match("Thanks!") is None


# ---------------------------------------------------------------------------
# poll_for_ack
# ---------------------------------------------------------------------------


class TestPollForAck:
    def _future_deadline(self, seconds: float = 3600) -> float:
        return time.monotonic() + seconds

    def test_ack_on_first_poll_returns_true(self) -> None:
        messages = [
            _make_message("Original summary"),
            _make_message(f"ACK {TOKEN}"),
        ]
        with patch.object(slack_api, "get_replies", return_value=messages):
            result = poll_for_ack("C123", "ts.001", TOKEN, self._future_deadline())
        assert result is True

    def test_ack_found_on_second_poll_returns_true(self) -> None:
        no_ack_messages = [_make_message("Original summary")]
        ack_messages = [
            _make_message("Original summary"),
            _make_message(f"ACK {TOKEN}"),
        ]
        call_count = {"n": 0}

        def side_effect(channel: str, thread_ts: str) -> list[dict[str, Any]]:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return no_ack_messages
            return ack_messages

        with patch.object(slack_api, "get_replies", side_effect=side_effect):
                result = poll_for_ack("C123", "ts.001", TOKEN, self._future_deadline(), 0)
        assert result is True
        assert call_count["n"] == 2

    def test_timeout_returns_false(self) -> None:
        messages = [_make_message("Original summary")]
        with patch.object(slack_api, "get_replies", return_value=messages):
            # Deadline is already in the past
            result = poll_for_ack("C123", "ts.001", TOKEN, time.monotonic() - 1)
        assert result is False

    def test_wrong_token_is_ignored(self) -> None:
        messages = [
            _make_message("Original summary"),
            _make_message("ACK ZZZZZZ"),  # Wrong token
        ]
        with patch.object(slack_api, "get_replies", return_value=messages):
            result = poll_for_ack("C123", "ts.001", TOKEN, time.monotonic() - 1)
        assert result is False

    def test_non_ack_messages_ignored(self) -> None:
        messages = [
            _make_message("Original summary"),
            _make_message("LGTM"),
            _make_message("looks good"),
            _make_message("+1"),
        ]
        with patch.object(slack_api, "get_replies", return_value=messages):
            result = poll_for_ack("C123", "ts.001", TOKEN, time.monotonic() - 1)
        assert result is False

    def test_original_message_is_skipped(self) -> None:
        # Only message is [0] which contains the token in the original summary text
        messages = [_make_message(f"ACK {TOKEN}")]
        with patch.object(slack_api, "get_replies", return_value=messages):
            result = poll_for_ack("C123", "ts.001", TOKEN, time.monotonic() - 1)
        assert result is False

    def test_ack_case_insensitive(self) -> None:
        messages = [
            _make_message("Original summary"),
            _make_message(f"ack {TOKEN.lower()}"),
        ]
        with patch.object(slack_api, "get_replies", return_value=messages):
            result = poll_for_ack("C123", "ts.001", TOKEN, self._future_deadline())
        assert result is True

    def test_slack_error_propagates(self) -> None:
        with patch.object(
            slack_api, "get_replies", side_effect=RuntimeError("Slack 500")
        ):
            with pytest.raises(RuntimeError, match="Slack 500"):
                poll_for_ack("C123", "ts.001", TOKEN, self._future_deadline())


# ---------------------------------------------------------------------------
# write_audit
# ---------------------------------------------------------------------------


class TestWriteAudit:
    def test_audit_file_contents(self, tmp_path: Path) -> None:
        audit_path = tmp_path / "audit.json"
        with patch.dict("os.environ", {"ACK_AUDIT_PATH": str(audit_path)}):
            write_audit(SAMPLE_WARNS, TOKEN, "C123", "ts.001", "2026-01-01T00:00:00+00:00")

        data = json.loads(audit_path.read_text())
        assert data["status"] == "ACKNOWLEDGED"
        assert data["ack_token"] == TOKEN
        assert data["channel"] == "C123"
        assert data["thread_ts"] == "ts.001"
        assert data["warn_count"] == len(SAMPLE_WARNS)
        assert data["warnings"] == SAMPLE_WARNS
        assert data["acknowledged_at"] == "2026-01-01T00:00:00+00:00"

    def test_audit_default_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ACK_AUDIT_PATH", raising=False)
        write_audit([], TOKEN, "C123", "ts.001", "2026-01-01T00:00:00+00:00")
        assert (tmp_path / "ack_audit.json").exists()


# ---------------------------------------------------------------------------
# run_ack_gate
# ---------------------------------------------------------------------------


class TestRunAckGate:
    def test_empty_warn_list_returns_0_immediately(self) -> None:
        with patch.object(slack_api, "post_message") as mock_post:
            result = run_ack_gate([])
        assert result == 0
        mock_post.assert_not_called()

    def test_ack_received_returns_0_and_writes_audit(self, tmp_path: Path) -> None:
        audit_path = tmp_path / "audit.json"
        fixed_token = "FXD001"  # exactly 6 chars to match _ACK_PATTERN
        post_response = {"ok": True, "ts": "ts.999"}
        ack_messages = [
            _make_message("Summary"),
            _make_message(f"ACK {fixed_token}"),
        ]

        with patch.dict(
            "os.environ",
            {"SLACK_ACK_CHANNEL_ID": "C999", "ACK_AUDIT_PATH": str(audit_path)},
        ):
            with patch.object(slack_api, "post_message", return_value=post_response):
                with patch.object(
                    ack_gate, "generate_ack_token", return_value=fixed_token
                ):
                    with patch.object(
                        slack_api, "get_replies", return_value=ack_messages
                    ):
                        result = run_ack_gate(SAMPLE_WARNS)

        assert result == 0
        assert audit_path.exists()
        audit = json.loads(audit_path.read_text())
        assert audit["status"] == "ACKNOWLEDGED"
        assert audit["ack_token"] == fixed_token

    def test_timeout_returns_1(self) -> None:
        post_response = {"ok": True, "ts": "ts.001"}
        no_ack = [_make_message("Summary")]

        with patch.dict(
            "os.environ",
            {"SLACK_ACK_CHANNEL_ID": "C123", "ACK_TIMEOUT_SECONDS": "0"},
        ):
            with patch.object(slack_api, "post_message", return_value=post_response):
                with patch.object(slack_api, "get_replies", return_value=no_ack):
                    result = run_ack_gate(SAMPLE_WARNS)

        assert result == 1

    def test_missing_channel_env_raises(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            # Ensure SLACK_ACK_CHANNEL_ID is absent
            import os
            os.environ.pop("SLACK_ACK_CHANNEL_ID", None)
            with pytest.raises(EnvironmentError, match="SLACK_ACK_CHANNEL_ID"):
                run_ack_gate(SAMPLE_WARNS)


# ---------------------------------------------------------------------------
# main (CLI)
# ---------------------------------------------------------------------------


class TestMain:
    def test_positional_args_passed_to_run_ack_gate(self) -> None:
        with patch.object(ack_gate, "run_ack_gate", return_value=0) as mock_run:
            rc = main(["warn1", "warn2"])
        assert rc == 0
        mock_run.assert_called_once_with(["warn1", "warn2"])

    def test_blank_args_filtered_out(self) -> None:
        with patch.object(ack_gate, "run_ack_gate", return_value=0) as mock_run:
            rc = main(["warn1", "  ", "warn2"])
        mock_run.assert_called_once_with(["warn1", "warn2"])

    def test_stdin_read_when_no_args(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin", StringIO("warn_a\nwarn_b\n"))
        with patch.object(ack_gate, "run_ack_gate", return_value=0) as mock_run:
            rc = main([])
        mock_run.assert_called_once_with(["warn_a", "warn_b"])

    def test_empty_stdin_calls_with_empty_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.stdin", StringIO(""))
        with patch.object(ack_gate, "run_ack_gate", return_value=0) as mock_run:
            rc = main([])
        mock_run.assert_called_once_with([])


# ---------------------------------------------------------------------------
# slack_api unit tests
# ---------------------------------------------------------------------------


class TestSlackApi:
    def _mock_ok_response(self, extra: dict[str, Any] | None = None) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        body: dict[str, Any] = {"ok": True, "ts": "ts.001"}
        if extra:
            body.update(extra)
        resp.json.return_value = body
        resp.raise_for_status = MagicMock()
        return resp

    def _mock_error_response(self, status: int) -> MagicMock:
        resp = MagicMock()
        resp.status_code = status
        resp.headers = {}
        resp.raise_for_status = MagicMock(side_effect=requests.HTTPError(f"HTTP {status}"))
        return resp

    def test_post_message_success(self) -> None:
        with patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"}):
            with patch("requests.post", return_value=self._mock_ok_response()) as mock_post:
                result = slack_api.post_message("C123", "hello")
        assert result["ok"] is True
        assert result["ts"] == "ts.001"
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        assert kwargs["json"]["channel"] == "C123"
        assert kwargs["json"]["text"] == "hello"

    def test_post_message_with_thread_ts(self) -> None:
        with patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"}):
            with patch("requests.post", return_value=self._mock_ok_response()) as mock_post:
                slack_api.post_message("C123", "reply", thread_ts="ts.parent")
        _, kwargs = mock_post.call_args
        assert kwargs["json"]["thread_ts"] == "ts.parent"

    def test_get_replies_returns_messages(self) -> None:
        msgs = [{"text": "original"}, {"text": "ACK ABC123"}]
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"ok": True, "messages": msgs}
        ok_resp.raise_for_status = MagicMock()

        with patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"}):
            with patch("requests.post", return_value=ok_resp):
                result = slack_api.get_replies("C123", "ts.001")
        assert result == msgs

    def test_retry_on_429_then_success(self) -> None:
        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.headers = {"Retry-After": "0"}
        rate_limited.raise_for_status = MagicMock()

        ok = self._mock_ok_response()

        with patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"}):
            with patch("requests.post", side_effect=[rate_limited, ok]) as mock_post:
                with patch("time.sleep") as mock_sleep:
                    result = slack_api.post_message("C123", "hi")

        assert result["ok"] is True
        assert mock_post.call_count == 2
        mock_sleep.assert_called_once_with(0.0)

    def test_retry_on_500_then_success(self) -> None:
        server_error = MagicMock()
        server_error.status_code = 500
        server_error.headers = {}
        server_error.raise_for_status = MagicMock()

        ok = self._mock_ok_response()

        with patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"}):
            with patch("requests.post", side_effect=[server_error, ok]):
                with patch("time.sleep"):
                    result = slack_api.post_message("C123", "hi")

        assert result["ok"] is True

    def test_500_exhausts_retries_raises(self) -> None:
        server_error = MagicMock()
        server_error.status_code = 500
        server_error.headers = {}
        server_error.raise_for_status = MagicMock(side_effect=requests.HTTPError("500"))

        with patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"}):
            with patch("requests.post", return_value=server_error):
                with patch("time.sleep"):
                    with pytest.raises(requests.HTTPError):
                        slack_api.post_message("C123", "hi")

    def test_non_ok_slack_response_raises(self) -> None:
        not_ok = MagicMock()
        not_ok.status_code = 200
        not_ok.json.return_value = {"ok": False, "error": "channel_not_found"}
        not_ok.raise_for_status = MagicMock()

        with patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"}):
            with patch("requests.post", return_value=not_ok):
                with pytest.raises(RuntimeError, match="channel_not_found"):
                    slack_api.post_message("C123", "hi")

    def test_network_error_raises_immediately(self) -> None:
        with patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"}):
            with patch(
                "requests.post",
                side_effect=requests.ConnectionError("connection refused"),
            ):
                with pytest.raises(RuntimeError, match="Network error"):
                    slack_api.post_message("C123", "hi")

    def test_missing_token_raises(self) -> None:
        import os
        os.environ.pop("SLACK_BOT_TOKEN", None)
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(EnvironmentError, match="SLACK_BOT_TOKEN"):
                slack_api.post_message("C123", "hi")
