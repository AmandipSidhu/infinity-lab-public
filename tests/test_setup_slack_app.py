"""Tests for scripts/setup_slack_app.py.

Covers:
- Manifest loading (happy path, file not found)
- create_slack_app: successful API call, API error, network error
- _handle_api_error: known and unknown error codes
- print_success: output format
- main(): argument parsing, success path, error paths
"""

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, mock_open, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import setup_slack_app  # noqa: E402
from setup_slack_app import (  # noqa: E402
    MANIFEST_PATH,
    _handle_api_error,
    _load_manifest,
    build_parser,
    create_slack_app,
    main,
    print_success,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_MANIFEST: dict[str, Any] = {
    "display_information": {"name": "ACB Pipeline Bot"},
    "features": {"bot_user": {"display_name": "ACB Pipeline Bot", "always_online": False}},
    "oauth_config": {"scopes": {"bot": ["chat:write"]}},
    "settings": {"org_deploy_enabled": False},
}

_SUCCESS_RESPONSE: dict[str, Any] = {
    "ok": True,
    "app_id": "A0123456789",
    "credentials": {
        "bot_user_oauth_token": "xoxb-test-token",
        "verification_token": "verify-xyz",
    },
}


def _mock_resp(json_data: dict[str, Any], status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    resp.headers = {}
    return resp


# ---------------------------------------------------------------------------
# _load_manifest
# ---------------------------------------------------------------------------


class TestLoadManifest:
    def test_loads_valid_manifest(self) -> None:
        manifest_content = json.dumps(_SAMPLE_MANIFEST)
        with patch.object(Path, "exists", return_value=True):
            with patch("builtins.open", mock_open(read_data=manifest_content)):
                result = _load_manifest()
        assert result["display_information"]["name"] == "ACB Pipeline Bot"

    def test_raises_when_file_missing(self) -> None:
        with patch.object(Path, "exists", return_value=False):
            with pytest.raises(FileNotFoundError, match="Manifest not found"):
                _load_manifest()

    def test_real_manifest_file_exists(self) -> None:
        """The actual slack_app_manifest.json must exist in the repo root."""
        assert MANIFEST_PATH.exists(), f"Expected manifest at {MANIFEST_PATH}"

    def test_real_manifest_has_required_fields(self) -> None:
        """The actual manifest must contain the required fields per the spec."""
        with MANIFEST_PATH.open("r") as fh:
            manifest = json.load(fh)
        assert manifest["display_information"]["name"] == "ACB Pipeline Bot"
        assert manifest["features"]["bot_user"]["display_name"] == "ACB Pipeline Bot"
        assert "chat:write" in manifest["oauth_config"]["scopes"]["bot"]
        assert manifest["settings"]["org_deploy_enabled"] is False


# ---------------------------------------------------------------------------
# _handle_api_error
# ---------------------------------------------------------------------------


class TestHandleApiError:
    def test_invalid_auth_message(self) -> None:
        with pytest.raises(RuntimeError, match="Invalid configuration token"):
            _handle_api_error("invalid_auth")

    def test_not_authed_message(self) -> None:
        with pytest.raises(RuntimeError, match="Missing or malformed"):
            _handle_api_error("not_authed")

    def test_token_expired_message(self) -> None:
        with pytest.raises(RuntimeError, match="expired"):
            _handle_api_error("token_expired")

    def test_ratelimited_message(self) -> None:
        with pytest.raises(RuntimeError, match="Rate limited"):
            _handle_api_error("ratelimited")

    def test_invalid_manifest_message(self) -> None:
        with pytest.raises(RuntimeError, match="manifest JSON is invalid"):
            _handle_api_error("invalid_manifest")

    def test_unknown_error_includes_code(self) -> None:
        with pytest.raises(RuntimeError, match="some_unknown_error"):
            _handle_api_error("some_unknown_error")


# ---------------------------------------------------------------------------
# create_slack_app
# ---------------------------------------------------------------------------


class TestCreateSlackApp:
    def _patch_manifest(self) -> Any:
        return patch.object(setup_slack_app, "_load_manifest", return_value=_SAMPLE_MANIFEST)

    def test_successful_call_returns_data(self) -> None:
        resp = _mock_resp(_SUCCESS_RESPONSE)
        with self._patch_manifest():
            with patch("requests.post", return_value=resp):
                data = create_slack_app("xoxe-1-test")
        assert data["ok"] is True
        assert data["app_id"] == "A0123456789"

    def test_raises_on_api_error(self) -> None:
        resp = _mock_resp({"ok": False, "error": "invalid_auth"})
        with self._patch_manifest():
            with patch("requests.post", return_value=resp):
                with pytest.raises(RuntimeError, match="Invalid configuration token"):
                    create_slack_app("bad-token")

    def test_raises_on_network_error(self) -> None:
        import requests as req_lib

        with self._patch_manifest():
            with patch("requests.post", side_effect=req_lib.RequestException("timeout")):
                with pytest.raises(RuntimeError, match="Network error"):
                    create_slack_app("xoxe-1-test")

    def test_retries_on_429(self) -> None:
        rate_limited = _mock_resp({"ok": False, "error": "ratelimited"}, status=429)
        rate_limited.headers = {"Retry-After": "0"}
        success = _mock_resp(_SUCCESS_RESPONSE)
        with self._patch_manifest():
            with patch("requests.post", side_effect=[rate_limited, success]):
                with patch("time.sleep"):
                    data = create_slack_app("xoxe-1-test")
        assert data["ok"] is True

    def test_raises_after_max_retries_on_5xx(self) -> None:
        server_error = _mock_resp({}, status=500)
        server_error.raise_for_status.side_effect = Exception("500 Server Error")
        with self._patch_manifest():
            with patch("requests.post", return_value=server_error):
                with patch("time.sleep"):
                    with pytest.raises(Exception, match="500"):
                        create_slack_app("xoxe-1-test")


# ---------------------------------------------------------------------------
# print_success
# ---------------------------------------------------------------------------


class TestPrintSuccess:
    def test_output_contains_app_id_and_token(self, capsys: Any) -> None:
        print_success("A0123456789", "xoxb-test-token")
        captured = capsys.readouterr()
        assert "A0123456789" in captured.out
        assert "xoxb-test-token" in captured.out

    def test_output_contains_next_steps(self, capsys: Any) -> None:
        print_success("A001", "xoxb-001")
        captured = capsys.readouterr()
        assert "SLACK_BOT_TOKEN" in captured.out
        assert "#forge_reports" in captured.out
        assert "ACB Pipeline Bot" in captured.out

    def test_output_contains_checkmark(self, capsys: Any) -> None:
        print_success("A001", "xoxb-001")
        captured = capsys.readouterr()
        assert "✅" in captured.out


# ---------------------------------------------------------------------------
# build_parser
# ---------------------------------------------------------------------------


class TestBuildParser:
    def test_config_token_required(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args([])

    def test_config_token_parsed(self) -> None:
        args = build_parser().parse_args(["--config-token", "xoxe-1-abc"])
        assert args.config_token == "xoxe-1-abc"


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


class TestMain:
    def _patch_create(self, return_value: dict[str, Any]) -> Any:
        return patch.object(setup_slack_app, "create_slack_app", return_value=return_value)

    def test_success_exits_zero(self) -> None:
        with self._patch_create(_SUCCESS_RESPONSE):
            with patch.object(setup_slack_app, "print_success"):
                rc = main(["--config-token", "xoxe-1-test"])
        assert rc == 0

    def test_missing_token_exits_nonzero(self) -> None:
        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code != 0

    def test_empty_token_exits_nonzero(self) -> None:
        rc = main(["--config-token", "   "])
        assert rc == 1

    def test_runtime_error_exits_nonzero(self) -> None:
        with patch.object(
            setup_slack_app, "create_slack_app", side_effect=RuntimeError("invalid_auth")
        ):
            rc = main(["--config-token", "xoxe-1-bad"])
        assert rc == 1

    def test_file_not_found_exits_nonzero(self) -> None:
        with patch.object(
            setup_slack_app, "create_slack_app", side_effect=FileNotFoundError("missing")
        ):
            rc = main(["--config-token", "xoxe-1-test"])
        assert rc == 1

    def test_missing_app_id_in_response_exits_nonzero(self) -> None:
        response_missing_app_id = {"ok": True, "app_id": "", "credentials": {"bot_user_oauth_token": "xoxb-x"}}
        with self._patch_create(response_missing_app_id):
            rc = main(["--config-token", "xoxe-1-test"])
        assert rc == 1

    def test_missing_bot_token_in_response_exits_nonzero(self) -> None:
        response_missing_bot_token = {"ok": True, "app_id": "A001", "credentials": {"bot_user_oauth_token": ""}}
        with self._patch_create(response_missing_bot_token):
            rc = main(["--config-token", "xoxe-1-test"])
        assert rc == 1
