"""Tests for scripts/mcp_tool_discovery.py.

Covers:
- _convert_mcp_tool: MCP → Claude API format conversion
- _categorise_tools: category mapping logic
- build_manifest: manifest structure
- validate_manifest: validation logic
- _fetch_live_tools: live server path (mocked) and fallback on connection error
- main: CLI interface, output file generation, exit codes
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import mcp_tool_discovery  # noqa: E402
from mcp_tool_discovery import (  # noqa: E402
    _STATIC_TOOLS,
    _categorise_tools,
    _convert_mcp_tool,
    _fetch_live_tools,
    build_manifest,
    main,
    validate_manifest,
)


# ---------------------------------------------------------------------------
# _convert_mcp_tool
# ---------------------------------------------------------------------------


class TestConvertMcpTool:
    def test_renames_input_schema(self) -> None:
        mcp_tool = {
            "name": "check_syntax",
            "description": "Checks syntax.",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        }
        result = _convert_mcp_tool(mcp_tool)
        assert result["name"] == "check_syntax"
        assert result["description"] == "Checks syntax."
        assert "input_schema" in result
        assert "inputSchema" not in result
        assert result["input_schema"]["type"] == "object"

    def test_falls_back_to_snake_case_input_schema(self) -> None:
        mcp_tool = {
            "name": "some_tool",
            "description": "Does something.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        }
        result = _convert_mcp_tool(mcp_tool)
        assert result["input_schema"]["type"] == "object"

    def test_handles_missing_fields(self) -> None:
        result = _convert_mcp_tool({})
        assert result["name"] == ""
        assert result["description"] == ""
        assert result["input_schema"] == {}


# ---------------------------------------------------------------------------
# _categorise_tools
# ---------------------------------------------------------------------------


class TestCategoriseTools:
    def test_known_syntax_tools(self) -> None:
        tools = [{"name": "check_syntax"}, {"name": "complete_code"}]
        cats = _categorise_tools(tools)
        assert "syntax" in cats
        assert "check_syntax" in cats["syntax"]
        assert "complete_code" in cats["syntax"]

    def test_known_validation_tools(self) -> None:
        tools = [{"name": "check_initialization_errors"}, {"name": "enhance_error_message"}]
        cats = _categorise_tools(tools)
        assert "validation" in cats
        assert "check_initialization_errors" in cats["validation"]

    def test_unknown_tool_defaults_to_project(self) -> None:
        tools = [{"name": "some_unknown_tool"}]
        cats = _categorise_tools(tools)
        assert "project" in cats
        assert "some_unknown_tool" in cats["project"]

    def test_no_duplicate_tool_names_per_category(self) -> None:
        tools = [{"name": "check_syntax"}, {"name": "check_syntax"}]
        cats = _categorise_tools(tools)
        assert cats["syntax"].count("check_syntax") == 1

    def test_empty_categories_are_removed(self) -> None:
        tools = [{"name": "check_syntax"}]
        cats = _categorise_tools(tools)
        # compile and search have no tools in this list → should be absent
        assert "compile" not in cats
        assert "search" not in cats


# ---------------------------------------------------------------------------
# build_manifest
# ---------------------------------------------------------------------------


class TestBuildManifest:
    def test_structure(self) -> None:
        tools = [
            {
                "name": "check_syntax",
                "description": "Checks syntax.",
                "input_schema": {},
            }
        ]
        manifest = build_manifest(tools)
        assert manifest["total_count"] == 1
        assert manifest["tools"] == tools
        assert isinstance(manifest["categories"], dict)

    def test_total_count_matches_tools(self) -> None:
        manifest = build_manifest(_STATIC_TOOLS)
        assert manifest["total_count"] == len(_STATIC_TOOLS)
        assert manifest["total_count"] >= 1


# ---------------------------------------------------------------------------
# validate_manifest
# ---------------------------------------------------------------------------


class TestValidateManifest:
    def test_valid_manifest_passes(self) -> None:
        manifest = build_manifest(_STATIC_TOOLS)
        assert validate_manifest(manifest) is True

    def test_empty_tools_fails(self) -> None:
        assert validate_manifest({"tools": [], "total_count": 0, "categories": {}}) is False

    def test_missing_tools_key_fails(self) -> None:
        assert validate_manifest({"total_count": 1, "categories": {"syntax": ["x"]}}) is False

    def test_total_count_mismatch_fails(self) -> None:
        manifest = {"tools": [{"name": "x"}], "total_count": 99, "categories": {"project": ["x"]}}
        assert validate_manifest(manifest) is False

    def test_empty_categories_fails(self) -> None:
        manifest = {"tools": [{"name": "x"}], "total_count": 1, "categories": {}}
        assert validate_manifest(manifest) is False


# ---------------------------------------------------------------------------
# _fetch_live_tools
# ---------------------------------------------------------------------------


class TestFetchLiveTools:
    def test_returns_tools_on_success(self) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": "tools_list",
            "result": {
                "tools": [
                    {
                        "name": "create_project",
                        "description": "Create a project.",
                        "inputSchema": {"type": "object", "properties": {}, "required": []},
                    }
                ]
            },
        }
        with patch("mcp_tool_discovery.requests.post", return_value=mock_response):
            result = _fetch_live_tools("http://localhost:8000/mcp")
        assert result is not None
        assert len(result) == 1
        assert result[0]["name"] == "create_project"
        assert "input_schema" in result[0]

    def test_returns_none_on_connection_error(self) -> None:
        with patch(
            "mcp_tool_discovery.requests.post",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            result = _fetch_live_tools("http://localhost:8000/mcp")
        assert result is None

    def test_returns_none_on_request_exception(self) -> None:
        with patch(
            "mcp_tool_discovery.requests.post",
            side_effect=requests.RequestException("timeout"),
        ):
            result = _fetch_live_tools("http://localhost:8000/mcp")
        assert result is None

    def test_returns_none_on_mcp_error_response(self) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": "tools_list",
            "error": {"code": -32601, "message": "Method not found"},
        }
        with patch("mcp_tool_discovery.requests.post", return_value=mock_response):
            result = _fetch_live_tools("http://localhost:8000/mcp")
        assert result is None

    def test_returns_none_on_empty_tool_list(self) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": "tools_list",
            "result": {"tools": []},
        }
        with patch("mcp_tool_discovery.requests.post", return_value=mock_response):
            result = _fetch_live_tools("http://localhost:8000/mcp")
        assert result is None


# ---------------------------------------------------------------------------
# Static tool catalogue
# ---------------------------------------------------------------------------


class TestStaticToolCatalogue:
    def test_has_at_least_54_tools(self) -> None:
        assert len(_STATIC_TOOLS) >= 54

    def test_all_tools_have_name_description_input_schema(self) -> None:
        for tool in _STATIC_TOOLS:
            assert "name" in tool, f"Missing 'name' in tool: {tool}"
            assert "description" in tool, f"Missing 'description' in tool: {tool}"
            assert "input_schema" in tool, f"Missing 'input_schema' in tool: {tool}"

    def test_required_tools_are_present(self) -> None:
        names = {t["name"] for t in _STATIC_TOOLS}
        required = {
            "check_syntax",
            "complete_code",
            "update_code_to_pep8",
            "check_initialization_errors",
            "enhance_error_message",
            "create_backtest",
            "read_backtest",
            "read_backtest_orders",
            "create_compile",
            "read_compile",
            "search_quantconnect",
        }
        missing = required - names
        assert not missing, f"Required tools missing from static catalogue: {missing}"

    def test_all_tool_names_are_unique(self) -> None:
        names = [t["name"] for t in _STATIC_TOOLS]
        assert len(names) == len(set(names)), "Duplicate tool names in static catalogue"


# ---------------------------------------------------------------------------
# main (CLI)
# ---------------------------------------------------------------------------


class TestMain:
    def test_writes_manifest_to_custom_output(self, tmp_path: Path) -> None:
        out = tmp_path / "manifest.json"
        rc = main(["--output", str(out)])
        assert rc == 0
        assert out.exists()
        manifest = json.loads(out.read_text(encoding="utf-8"))
        assert manifest["total_count"] >= 1
        assert isinstance(manifest["tools"], list)
        assert isinstance(manifest["categories"], dict)

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        out = tmp_path / "sub" / "dir" / "manifest.json"
        rc = main(["--output", str(out)])
        assert rc == 0
        assert out.exists()

    def test_uses_live_tools_when_server_available(self, tmp_path: Path) -> None:
        out = tmp_path / "manifest.json"
        live_tool = {
            "name": "live_tool",
            "description": "A live tool.",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        }
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": "tools_list",
            "result": {"tools": [live_tool]},
        }
        with patch("mcp_tool_discovery.requests.post", return_value=mock_response):
            rc = main(["--output", str(out), "--mcp-url", "http://localhost:8000/mcp"])
        assert rc == 0
        manifest = json.loads(out.read_text(encoding="utf-8"))
        assert manifest["total_count"] == 1
        assert manifest["tools"][0]["name"] == "live_tool"

    def test_falls_back_to_static_on_connection_error(self, tmp_path: Path) -> None:
        out = tmp_path / "manifest.json"
        with patch(
            "mcp_tool_discovery.requests.post",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            rc = main(["--output", str(out), "--mcp-url", "http://localhost:8000/mcp"])
        assert rc == 0
        manifest = json.loads(out.read_text(encoding="utf-8"))
        assert manifest["total_count"] >= 54

    def test_exit_1_on_write_failure(self, tmp_path: Path) -> None:
        out = tmp_path / "manifest.json"
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            rc = main(["--output", str(out)])
        assert rc == 1

    def test_default_output_path_under_config(self, tmp_path: Path) -> None:
        out = tmp_path / "qc_tools_manifest.json"
        rc = main(["--output", str(out)])
        assert rc == 0

    def test_manifest_json_is_valid_json(self, tmp_path: Path) -> None:
        out = tmp_path / "manifest.json"
        main(["--output", str(out)])
        content = out.read_text(encoding="utf-8")
        parsed = json.loads(content)  # must not raise
        assert isinstance(parsed, dict)

    def test_all_six_categories_present_in_static_manifest(self, tmp_path: Path) -> None:
        out = tmp_path / "manifest.json"
        main(["--output", str(out)])
        manifest = json.loads(out.read_text(encoding="utf-8"))
        cats = set(manifest["categories"].keys())
        expected = {"syntax", "validation", "project", "backtest", "compile", "search"}
        assert expected == cats
