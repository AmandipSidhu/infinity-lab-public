"""Tests for scripts/qc_promote.py.

Covers:
- _stem_to_base_name: CamelCase conversion from snake_case stems
- _get_next_version: version auto-increment from existing project list
- _assert_allowed_endpoint: guards against disallowed QC API endpoints
- promote: full workflow (create project, upload file)
- main (CLI): happy path, missing file, missing credentials, API error
"""

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import qc_promote  # noqa: E402
from qc_promote import (  # noqa: E402
    _assert_allowed_endpoint,
    _get_next_version,
    _stem_to_base_name,
    main,
    promote,
)


# ---------------------------------------------------------------------------
# _stem_to_base_name
# ---------------------------------------------------------------------------


class TestStemToBaseName:
    def test_single_word(self) -> None:
        assert _stem_to_base_name("vwap") == "Vwap"

    def test_two_words(self) -> None:
        assert _stem_to_base_name("vwap_probe") == "VwapProbe"

    def test_three_words(self) -> None:
        assert _stem_to_base_name("mean_reversion_v2") == "MeanReversionV2"

    def test_already_camel(self) -> None:
        assert _stem_to_base_name("strategy") == "Strategy"

    def test_multiple_underscores(self) -> None:
        assert _stem_to_base_name("my_cool_strategy") == "MyCoolStrategy"


# ---------------------------------------------------------------------------
# _assert_allowed_endpoint
# ---------------------------------------------------------------------------


class TestAssertAllowedEndpoint:
    def test_allowed_projects_create(self) -> None:
        _assert_allowed_endpoint("projects/create")

    def test_allowed_files_create(self) -> None:
        _assert_allowed_endpoint("files/create")

    def test_allowed_projects_read(self) -> None:
        _assert_allowed_endpoint("projects/read")

    def test_disallowed_files_update(self) -> None:
        with pytest.raises(ValueError, match="files/update"):
            _assert_allowed_endpoint("files/update")

    def test_disallowed_live_create(self) -> None:
        with pytest.raises(ValueError, match="live/create"):
            _assert_allowed_endpoint("live/create")

    def test_disallowed_live_read(self) -> None:
        with pytest.raises(ValueError, match="Allowed"):
            _assert_allowed_endpoint("live/read")

    def test_disallowed_portfolio(self) -> None:
        with pytest.raises(ValueError):
            _assert_allowed_endpoint("portfolio/read")

    def test_paper_project_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="paper-vwap-v1"):
            _assert_allowed_endpoint("projects/create", project_name="paper-vwap-v1")

    def test_live_project_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="live-macd-v2"):
            _assert_allowed_endpoint("projects/create", project_name="live-macd-v2")


# ---------------------------------------------------------------------------
# _get_next_version
# ---------------------------------------------------------------------------


def _mock_projects_response(names: list[str]) -> dict[str, Any]:
    return {
        "success": True,
        "projects": [
            {"projectId": i + 1, "name": name}
            for i, name in enumerate(names)
        ],
    }


class TestGetNextVersion:
    def _patch_get(self, response: dict[str, Any]):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = response
        return patch("requests.get", return_value=mock_resp)

    def test_no_existing_projects_returns_1(self) -> None:
        with self._patch_get(_mock_projects_response([])):
            version = _get_next_version("VwapProbe", "uid", "tok")
        assert version == 1

    def test_v1_exists_returns_2(self) -> None:
        with self._patch_get(_mock_projects_response(["VwapProbe-v1", "OtherProject"])):
            version = _get_next_version("VwapProbe", "uid", "tok")
        assert version == 2

    def test_v1_and_v2_exist_returns_3(self) -> None:
        with self._patch_get(_mock_projects_response(
            ["VwapProbe-v1", "VwapProbe-v2", "Unrelated"]
        )):
            version = _get_next_version("VwapProbe", "uid", "tok")
        assert version == 3

    def test_gap_in_versions_uses_max_plus_1(self) -> None:
        with self._patch_get(_mock_projects_response(
            ["VwapProbe-v1", "VwapProbe-v3"]
        )):
            version = _get_next_version("VwapProbe", "uid", "tok")
        assert version == 4

    def test_different_base_name_not_counted(self) -> None:
        with self._patch_get(_mock_projects_response(
            ["OtherStrategy-v1", "OtherStrategy-v5"]
        )):
            version = _get_next_version("VwapProbe", "uid", "tok")
        assert version == 1

    def test_api_error_falls_back_to_1(self) -> None:
        with patch("requests.get", side_effect=requests.ConnectionError("timeout")):
            version = _get_next_version("VwapProbe", "uid", "tok")
        assert version == 1


# ---------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------


class TestPromote:
    def _mock_post_response(self, project_id: int = 42) -> MagicMock:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "success": True,
            "projects": [{"projectId": project_id, "name": "VwapProbe-v1"}],
        }
        return resp

    def _mock_file_upload_response(self) -> MagicMock:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"success": True}
        return resp

    def test_happy_path_returns_correct_keys(self, tmp_path: Path) -> None:
        strategy = tmp_path / "strategy.py"
        strategy.write_text("class MyAlgo: pass", encoding="utf-8")

        get_resp = MagicMock()
        get_resp.raise_for_status = MagicMock()
        get_resp.json.return_value = {"success": True, "projects": []}

        post_responses = [self._mock_post_response(42), self._mock_file_upload_response()]

        with patch("requests.get", return_value=get_resp):
            with patch("requests.post", side_effect=post_responses):
                result = promote("vwap_probe", strategy, "uid", "tok")

        assert result["qc_project_id"] == "42"
        assert result["qc_project_name"] == "VwapProbe-v1"
        assert result["spec_stem"] == "vwap_probe"
        assert "promoted_at" in result

    def test_version_incremented_when_v1_exists(self, tmp_path: Path) -> None:
        strategy = tmp_path / "strategy.py"
        strategy.write_text("# code", encoding="utf-8")

        get_resp = MagicMock()
        get_resp.raise_for_status = MagicMock()
        get_resp.json.return_value = {
            "success": True,
            "projects": [{"projectId": 1, "name": "VwapProbe-v1"}],
        }

        post_create = MagicMock()
        post_create.raise_for_status = MagicMock()
        post_create.json.return_value = {
            "success": True,
            "projects": [{"projectId": 99, "name": "VwapProbe-v2"}],
        }
        post_upload = self._mock_file_upload_response()

        with patch("requests.get", return_value=get_resp):
            with patch("requests.post", side_effect=[post_create, post_upload]):
                result = promote("vwap_probe", strategy, "uid", "tok")

        assert result["qc_project_name"] == "VwapProbe-v2"

    def test_api_error_on_create_raises(self, tmp_path: Path) -> None:
        strategy = tmp_path / "strategy.py"
        strategy.write_text("# code", encoding="utf-8")

        get_resp = MagicMock()
        get_resp.raise_for_status = MagicMock()
        get_resp.json.return_value = {"success": True, "projects": []}

        error_resp = MagicMock()
        error_resp.raise_for_status = MagicMock(
            side_effect=requests.HTTPError("500 Server Error")
        )

        with patch("requests.get", return_value=get_resp):
            with patch("requests.post", return_value=error_resp):
                with pytest.raises(RuntimeError, match="projects/create"):
                    promote("vwap_probe", strategy, "uid", "tok")


# ---------------------------------------------------------------------------
# main (CLI)
# ---------------------------------------------------------------------------


class TestMain:
    def _mock_promote(self, result: dict[str, Any]):
        return patch.object(
            qc_promote,
            "promote",
            return_value=result,
        )

    def test_happy_path_exits_0_and_prints_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        strategy = tmp_path / "strategy.py"
        strategy.write_text("# code", encoding="utf-8")

        expected = {
            "qc_project_id": "42",
            "qc_project_name": "VwapProbe-v1",
            "spec_stem": "vwap_probe",
            "promoted_at": "2026-01-01T00:00:00+00:00",
        }

        with self._mock_promote(expected):
            rc = main([
                "--spec-stem", "vwap_probe",
                "--strategy-file", str(strategy),
                "--qc-user-id", "uid",
                "--qc-api-token", "tok",
            ])

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["qc_project_name"] == "VwapProbe-v1"

    def test_missing_strategy_file_exits_2(self, tmp_path: Path) -> None:
        rc = main([
            "--spec-stem", "vwap_probe",
            "--strategy-file", str(tmp_path / "nonexistent.py"),
            "--qc-user-id", "uid",
            "--qc-api-token", "tok",
        ])
        assert rc == 2

    def test_missing_credentials_exits_2(self, tmp_path: Path) -> None:
        strategy = tmp_path / "strategy.py"
        strategy.write_text("# code", encoding="utf-8")
        rc = main([
            "--spec-stem", "vwap_probe",
            "--strategy-file", str(strategy),
            "--qc-user-id", "",
            "--qc-api-token", "",
        ])
        assert rc == 2

    def test_credentials_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        strategy = tmp_path / "strategy.py"
        strategy.write_text("# code", encoding="utf-8")

        monkeypatch.setenv("QC_USER_ID", "env_uid")
        monkeypatch.setenv("QC_API_TOKEN", "env_tok")

        expected = {
            "qc_project_id": "7",
            "qc_project_name": "VwapProbe-v1",
            "spec_stem": "vwap_probe",
            "promoted_at": "2026-01-01T00:00:00+00:00",
        }

        with self._mock_promote(expected):
            rc = main([
                "--spec-stem", "vwap_probe",
                "--strategy-file", str(strategy),
            ])

        assert rc == 0

    def test_api_error_exits_1(self, tmp_path: Path) -> None:
        strategy = tmp_path / "strategy.py"
        strategy.write_text("# code", encoding="utf-8")

        with patch.object(
            qc_promote, "promote", side_effect=RuntimeError("API timeout")
        ):
            rc = main([
                "--spec-stem", "vwap_probe",
                "--strategy-file", str(strategy),
                "--qc-user-id", "uid",
                "--qc-api-token", "tok",
            ])

        assert rc == 1

    def test_standalone_invocation_callable(self, tmp_path: Path) -> None:
        """Verify the script is callable standalone (no import errors)."""
        strategy = tmp_path / "strategy.py"
        strategy.write_text("# code", encoding="utf-8")
        import subprocess
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "qc_promote.py"),
                "--help",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "--spec-stem" in result.stdout
