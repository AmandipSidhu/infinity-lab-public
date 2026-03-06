#!/usr/bin/env python3
"""MCP Tool Discovery — QC tool manifest generator.

Connects to the running lean-cli/QC MCP environment (if available) and
enumerates all available tools.  Falls back to a built-in static list of
the full QuantConnect API tool catalogue when the server is not reachable.

Converts MCP ``inputSchema`` format → LLM tool-calling ``input_schema`` format and
categorises tools into: ``syntax``, ``validation``, ``project``, ``backtest``,
``compile``, ``search``.

Output is written to ``config/qc_tools_manifest.json`` (relative to the
repository root, or the path supplied via ``--output``).

Exit codes:
  0 — Manifest written successfully (total_count >= 1)
  1 — Manifest could not be written or total_count < 1
  2 — Invalid arguments
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MCP_BASE_URL: str = os.environ.get("QC_MCP_BASE_URL", "").strip()
_REQUEST_TIMEOUT_SECONDS: int = 10
_DEFAULT_OUTPUT: Path = Path(__file__).parent.parent / "config" / "qc_tools_manifest.json"

# ---------------------------------------------------------------------------
# Category mapping
# Maps tool names (or prefixes) to their manifest category.
# ---------------------------------------------------------------------------

_CATEGORY_MAP: dict[str, list[str]] = {
    "syntax": [
        "check_syntax",
        "complete_code",
        "update_code_to_pep8",
        "format_code",
        "lint_code",
    ],
    "validation": [
        "check_initialization_errors",
        "enhance_error_message",
        "validate_strategy",
        "check_runtime_errors",
        "validate_imports",
    ],
    "project": [
        "create_project",
        "read_project",
        "update_project",
        "delete_project",
        "list_projects",
        "create_file",
        "read_file",
        "update_file",
        "delete_file",
        "list_files",
        "create_node",
        "read_node",
        "update_node",
        "delete_node",
        "list_nodes",
        "read_project_packages",
        "create_project_package",
        "delete_project_package",
    ],
    "backtest": [
        "create_backtest",
        "read_backtest",
        "update_backtest",
        "delete_backtest",
        "list_backtests",
        "read_backtest_orders",
        "read_backtest_trades",
        "read_backtest_charts",
        "read_backtest_insights",
        "read_backtest_portfolio",
        "read_backtest_summary",
        "read_backtest_statistics",
        "read_backtest_logs",
        "create_backtest_report",
        "create_live_algorithm",
        "read_live_algorithm",
        "update_live_algorithm",
        "delete_live_algorithm",
        "list_live_algorithms",
        "read_live_orders",
        "read_live_trades",
        "read_live_charts",
        "read_live_insights",
        "read_live_portfolio",
        "read_live_logs",
    ],
    "compile": [
        "create_compile",
        "read_compile",
    ],
    "search": [
        "search_quantconnect",
        "search_algorithms",
        "search_data",
        "read_data_link",
        "read_data_prices",
    ],
}

# Reverse lookup: tool_name → category
_TOOL_TO_CATEGORY: dict[str, str] = {
    tool: category
    for category, tools in _CATEGORY_MAP.items()
    for tool in tools
}

# ---------------------------------------------------------------------------
# Static built-in tool catalogue (LLM tool-calling function format)
# ---------------------------------------------------------------------------

_STATIC_TOOLS: list[dict[str, Any]] = [
    # ── syntax ────────────────────────────────────────────────────────────
    {
        "name": "check_syntax",
        "description": "Check Python code syntax for errors in a QuantConnect algorithm file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "fileName": {"type": "string", "description": "Name of the file to check"},
            },
            "required": ["projectId", "fileName"],
        },
    },
    {
        "name": "complete_code",
        "description": "Auto-complete Python code using AI-assisted suggestions for QuantConnect algorithms.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "fileName": {"type": "string", "description": "Name of the file"},
                "position": {
                    "type": "object",
                    "description": "Cursor position (line, column)",
                    "properties": {
                        "line": {"type": "integer"},
                        "column": {"type": "integer"},
                    },
                },
            },
            "required": ["projectId", "fileName", "position"],
        },
    },
    {
        "name": "update_code_to_pep8",
        "description": "Format Python code to PEP 8 standards and return the updated source.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "fileName": {"type": "string", "description": "Name of the file to reformat"},
            },
            "required": ["projectId", "fileName"],
        },
    },
    {
        "name": "format_code",
        "description": "Format source code in a project file using the configured formatter.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "fileName": {"type": "string", "description": "Name of the file to format"},
            },
            "required": ["projectId", "fileName"],
        },
    },
    {
        "name": "lint_code",
        "description": "Run static analysis (linting) on a project file and return diagnostics.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "fileName": {"type": "string", "description": "Name of the file to lint"},
            },
            "required": ["projectId", "fileName"],
        },
    },
    # ── validation ────────────────────────────────────────────────────────
    {
        "name": "check_initialization_errors",
        "description": "Check a QuantConnect algorithm for common initialization errors before backtesting.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
            },
            "required": ["projectId"],
        },
    },
    {
        "name": "enhance_error_message",
        "description": "Enhance a raw error message with contextual explanation and suggested fixes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "error": {"type": "string", "description": "The raw error message to enhance"},
                "projectId": {"type": "integer", "description": "Project ID (optional context)"},
            },
            "required": ["error"],
        },
    },
    {
        "name": "validate_strategy",
        "description": "Validate a strategy implementation against QuantConnect best practices.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
            },
            "required": ["projectId"],
        },
    },
    {
        "name": "check_runtime_errors",
        "description": "Check for common runtime errors and anti-patterns in algorithm code.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "fileName": {"type": "string", "description": "Name of the file to check"},
            },
            "required": ["projectId", "fileName"],
        },
    },
    {
        "name": "validate_imports",
        "description": "Validate that all imports in an algorithm are available in the LEAN environment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "fileName": {"type": "string", "description": "Name of the file to validate"},
            },
            "required": ["projectId", "fileName"],
        },
    },
    # ── project ───────────────────────────────────────────────────────────
    {
        "name": "create_project",
        "description": "Create a new QuantConnect project and return its project_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Project name"},
                "language": {
                    "type": "string",
                    "description": "Programming language (Py or C#)",
                    "enum": ["Py", "C#"],
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "read_project",
        "description": "Read metadata and settings for an existing QuantConnect project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
            },
            "required": ["projectId"],
        },
    },
    {
        "name": "update_project",
        "description": "Update the name or description of an existing QuantConnect project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "name": {"type": "string", "description": "New project name (optional)"},
                "description": {"type": "string", "description": "New description (optional)"},
            },
            "required": ["projectId"],
        },
    },
    {
        "name": "delete_project",
        "description": "Permanently delete a QuantConnect project and all its files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
            },
            "required": ["projectId"],
        },
    },
    {
        "name": "list_projects",
        "description": "List all QuantConnect projects accessible to the authenticated user.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "create_file",
        "description": "Create a new source file inside a QuantConnect project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "name": {"type": "string", "description": "File name (e.g. main.py)"},
                "content": {"type": "string", "description": "File content"},
            },
            "required": ["projectId", "name", "content"],
        },
    },
    {
        "name": "read_file",
        "description": "Read the content of a source file from a QuantConnect project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "fileName": {"type": "string", "description": "File name to read"},
            },
            "required": ["projectId", "fileName"],
        },
    },
    {
        "name": "update_file",
        "description": "Update the content of an existing file in a QuantConnect project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "fileName": {"type": "string", "description": "File name to update"},
                "content": {"type": "string", "description": "New file content"},
            },
            "required": ["projectId", "fileName", "content"],
        },
    },
    {
        "name": "delete_file",
        "description": "Delete a source file from a QuantConnect project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "fileName": {"type": "string", "description": "File name to delete"},
            },
            "required": ["projectId", "fileName"],
        },
    },
    {
        "name": "list_files",
        "description": "List all source files in a QuantConnect project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
            },
            "required": ["projectId"],
        },
    },
    {
        "name": "create_node",
        "description": "Create a compute node for running backtests or live trading.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Node name"},
                "organizationId": {"type": "string", "description": "Organisation ID"},
                "skuId": {"type": "string", "description": "SKU identifier for the node type"},
            },
            "required": ["name", "organizationId", "skuId"],
        },
    },
    {
        "name": "read_node",
        "description": "Read metadata and status for a compute node.",
        "input_schema": {
            "type": "object",
            "properties": {
                "organizationId": {"type": "string", "description": "Organisation ID"},
                "nodeId": {"type": "string", "description": "Node ID"},
            },
            "required": ["organizationId", "nodeId"],
        },
    },
    {
        "name": "update_node",
        "description": "Update the name or settings of a compute node.",
        "input_schema": {
            "type": "object",
            "properties": {
                "organizationId": {"type": "string", "description": "Organisation ID"},
                "nodeId": {"type": "string", "description": "Node ID"},
                "name": {"type": "string", "description": "New node name"},
            },
            "required": ["organizationId", "nodeId"],
        },
    },
    {
        "name": "delete_node",
        "description": "Delete a compute node from the organisation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "organizationId": {"type": "string", "description": "Organisation ID"},
                "nodeId": {"type": "string", "description": "Node ID"},
            },
            "required": ["organizationId", "nodeId"],
        },
    },
    {
        "name": "list_nodes",
        "description": "List all compute nodes available in an organisation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "organizationId": {"type": "string", "description": "Organisation ID"},
            },
            "required": ["organizationId"],
        },
    },
    {
        "name": "read_project_packages",
        "description": "Read the list of packages installed in a QuantConnect project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
            },
            "required": ["projectId"],
        },
    },
    {
        "name": "create_project_package",
        "description": "Install a Python package into a QuantConnect project environment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "name": {"type": "string", "description": "Package name (e.g. pandas)"},
                "version": {"type": "string", "description": "Package version (optional)"},
            },
            "required": ["projectId", "name"],
        },
    },
    {
        "name": "delete_project_package",
        "description": "Remove an installed package from a QuantConnect project environment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "name": {"type": "string", "description": "Package name to remove"},
            },
            "required": ["projectId", "name"],
        },
    },
    # ── backtest ──────────────────────────────────────────────────────────
    {
        "name": "create_backtest",
        "description": "Trigger a new backtest for a QuantConnect project and return the backtest_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "name": {"type": "string", "description": "Backtest name"},
                "compileId": {"type": "string", "description": "Compile ID to use (optional)"},
            },
            "required": ["projectId", "name"],
        },
    },
    {
        "name": "read_backtest",
        "description": "Read the status and results of a running or completed backtest.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "backtestId": {"type": "string", "description": "Backtest ID"},
            },
            "required": ["projectId", "backtestId"],
        },
    },
    {
        "name": "update_backtest",
        "description": "Update the name or note of an existing backtest.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "backtestId": {"type": "string", "description": "Backtest ID"},
                "name": {"type": "string", "description": "New backtest name (optional)"},
                "note": {"type": "string", "description": "New backtest note (optional)"},
            },
            "required": ["projectId", "backtestId"],
        },
    },
    {
        "name": "delete_backtest",
        "description": "Delete a backtest and its results from a QuantConnect project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "backtestId": {"type": "string", "description": "Backtest ID"},
            },
            "required": ["projectId", "backtestId"],
        },
    },
    {
        "name": "list_backtests",
        "description": "List all backtests for a QuantConnect project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
            },
            "required": ["projectId"],
        },
    },
    {
        "name": "read_backtest_orders",
        "description": "Read all orders generated during a completed backtest.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "backtestId": {"type": "string", "description": "Backtest ID"},
                "start": {"type": "integer", "description": "Pagination start index (optional)"},
                "end": {"type": "integer", "description": "Pagination end index (optional)"},
            },
            "required": ["projectId", "backtestId"],
        },
    },
    {
        "name": "read_backtest_trades",
        "description": "Read all trades (filled orders) from a completed backtest.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "backtestId": {"type": "string", "description": "Backtest ID"},
            },
            "required": ["projectId", "backtestId"],
        },
    },
    {
        "name": "read_backtest_charts",
        "description": "Read chart data (equity curve, benchmark) from a completed backtest.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "backtestId": {"type": "string", "description": "Backtest ID"},
                "name": {"type": "string", "description": "Chart name (optional, defaults to all)"},
            },
            "required": ["projectId", "backtestId"],
        },
    },
    {
        "name": "read_backtest_insights",
        "description": "Read Alpha model insights generated during a completed backtest.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "backtestId": {"type": "string", "description": "Backtest ID"},
                "start": {"type": "integer", "description": "Pagination start index (optional)"},
                "end": {"type": "integer", "description": "Pagination end index (optional)"},
            },
            "required": ["projectId", "backtestId"],
        },
    },
    {
        "name": "read_backtest_portfolio",
        "description": "Read the final portfolio state (holdings, cash) at the end of a backtest.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "backtestId": {"type": "string", "description": "Backtest ID"},
            },
            "required": ["projectId", "backtestId"],
        },
    },
    {
        "name": "read_backtest_summary",
        "description": "Read high-level summary statistics (Sharpe, CAGR, max drawdown) for a backtest.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "backtestId": {"type": "string", "description": "Backtest ID"},
            },
            "required": ["projectId", "backtestId"],
        },
    },
    {
        "name": "read_backtest_statistics",
        "description": "Read detailed performance statistics for a completed backtest.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "backtestId": {"type": "string", "description": "Backtest ID"},
            },
            "required": ["projectId", "backtestId"],
        },
    },
    {
        "name": "read_backtest_logs",
        "description": "Read console log output from a completed or running backtest.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "backtestId": {"type": "string", "description": "Backtest ID"},
                "start": {"type": "integer", "description": "Pagination start index (optional)"},
                "end": {"type": "integer", "description": "Pagination end index (optional)"},
            },
            "required": ["projectId", "backtestId"],
        },
    },
    {
        "name": "create_backtest_report",
        "description": "Generate a PDF or HTML report for a completed backtest.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "backtestId": {"type": "string", "description": "Backtest ID"},
            },
            "required": ["projectId", "backtestId"],
        },
    },
    {
        "name": "create_live_algorithm",
        "description": "Deploy a compiled algorithm to live trading on a brokerage.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "compileId": {"type": "string", "description": "Compile ID to deploy"},
                "nodeId": {"type": "string", "description": "Live trading node ID"},
                "brokerageSettings": {
                    "type": "object",
                    "description": "Brokerage-specific configuration",
                },
                "versionId": {"type": "string", "description": "Algorithm version ID (optional)"},
            },
            "required": ["projectId", "compileId", "nodeId", "brokerageSettings"],
        },
    },
    {
        "name": "read_live_algorithm",
        "description": "Read the status and runtime details of a live trading algorithm.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "deployId": {"type": "string", "description": "Deployment ID"},
            },
            "required": ["projectId", "deployId"],
        },
    },
    {
        "name": "update_live_algorithm",
        "description": "Send a live command (e.g. parameter update) to a running live algorithm.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "deployId": {"type": "string", "description": "Deployment ID"},
                "command": {"type": "object", "description": "Command payload to send"},
            },
            "required": ["projectId", "deployId", "command"],
        },
    },
    {
        "name": "delete_live_algorithm",
        "description": "Stop and liquidate a live trading algorithm.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "deployId": {"type": "string", "description": "Deployment ID"},
            },
            "required": ["projectId", "deployId"],
        },
    },
    {
        "name": "list_live_algorithms",
        "description": "List all live trading algorithms for the authenticated user.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by status (optional): running, stopped, runtime-error",
                },
            },
            "required": [],
        },
    },
    {
        "name": "read_live_orders",
        "description": "Read orders placed by a running live trading algorithm.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "start": {"type": "integer", "description": "Pagination start index (optional)"},
                "end": {"type": "integer", "description": "Pagination end index (optional)"},
            },
            "required": ["projectId"],
        },
    },
    {
        "name": "read_live_trades",
        "description": "Read trades executed by a running live trading algorithm.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "start": {"type": "integer", "description": "Pagination start index (optional)"},
                "end": {"type": "integer", "description": "Pagination end index (optional)"},
            },
            "required": ["projectId"],
        },
    },
    {
        "name": "read_live_charts",
        "description": "Read chart data from a running live trading algorithm.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "name": {"type": "string", "description": "Chart name"},
                "start": {"type": "integer", "description": "Start Unix timestamp (optional)"},
                "end": {"type": "integer", "description": "End Unix timestamp (optional)"},
            },
            "required": ["projectId", "name"],
        },
    },
    {
        "name": "read_live_insights",
        "description": "Read Alpha model insights from a running live trading algorithm.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "start": {"type": "integer", "description": "Pagination start index (optional)"},
                "end": {"type": "integer", "description": "Pagination end index (optional)"},
            },
            "required": ["projectId"],
        },
    },
    {
        "name": "read_live_portfolio",
        "description": "Read the current portfolio state (holdings, cash) of a live algorithm.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
            },
            "required": ["projectId"],
        },
    },
    {
        "name": "read_live_logs",
        "description": "Read console log output from a running live trading algorithm.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "algorithmId": {"type": "string", "description": "Algorithm deployment ID"},
                "start": {"type": "integer", "description": "Start Unix timestamp (optional)"},
                "stop": {"type": "integer", "description": "Stop Unix timestamp (optional)"},
            },
            "required": ["projectId", "algorithmId"],
        },
    },
    # ── compile ───────────────────────────────────────────────────────────
    {
        "name": "create_compile",
        "description": "Compile a QuantConnect project and return a compile_id for backtest deployment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
            },
            "required": ["projectId"],
        },
    },
    {
        "name": "read_compile",
        "description": "Read the result of a compilation (success/failure and error messages).",
        "input_schema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "integer", "description": "Project ID"},
                "compileId": {"type": "string", "description": "Compile ID"},
            },
            "required": ["projectId", "compileId"],
        },
    },
    # ── search ────────────────────────────────────────────────────────────
    {
        "name": "search_quantconnect",
        "description": "Search QuantConnect documentation, forum posts, and algorithm examples.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query string"},
                "type": {
                    "type": "string",
                    "description": "Result type filter (optional): documentation, forum, algorithm",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_algorithms",
        "description": "Search the QuantConnect Algorithm Lab for public algorithm examples.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "language": {
                    "type": "string",
                    "description": "Filter by language (optional): Py or C#",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_data",
        "description": "Search for available data sources and datasets in the QC Data Library.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "category": {"type": "string", "description": "Data category filter (optional)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_data_link",
        "description": "Read a download link for a QuantConnect dataset file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "organizationId": {"type": "string", "description": "Organisation ID"},
                "fileName": {"type": "string", "description": "Dataset file path"},
            },
            "required": ["organizationId", "fileName"],
        },
    },
    {
        "name": "read_data_prices",
        "description": "Read pricing information for a QuantConnect dataset.",
        "input_schema": {
            "type": "object",
            "properties": {
                "organizationId": {"type": "string", "description": "Organisation ID"},
            },
            "required": ["organizationId"],
        },
    },
]


# ---------------------------------------------------------------------------
# MCP protocol helpers
# ---------------------------------------------------------------------------


def _fetch_live_tools(mcp_url: str) -> list[dict[str, Any]] | None:
    """Attempt to enumerate tools via the MCP ``tools/list`` JSON-RPC call.

    Returns the converted tool list on success, or ``None`` if the server is
    unreachable or returns an unexpected response.
    """
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": "tools_list",
        "method": "tools/list",
        "params": {},
    }
    try:
        response = requests.post(
            mcp_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError as exc:
        print(
            f"[mcp_tool_discovery] MCP server unreachable ({exc}) — "
            "falling back to static tool list.",
            file=sys.stderr,
        )
        return None
    except requests.RequestException as exc:
        print(
            f"[mcp_tool_discovery] MCP request failed ({exc}) — "
            "falling back to static tool list.",
            file=sys.stderr,
        )
        return None

    body: dict[str, Any] = response.json()
    if "error" in body:
        print(
            f"[mcp_tool_discovery] MCP server error: {body['error']} — "
            "falling back to static tool list.",
            file=sys.stderr,
        )
        return None

    mcp_tools: list[dict[str, Any]] = (
        body.get("result", {}).get("tools", [])
    )
    if not mcp_tools:
        print(
            "[mcp_tool_discovery] MCP server returned empty tool list — "
            "falling back to static tool list.",
            file=sys.stderr,
        )
        return None

    return [_convert_mcp_tool(t) for t in mcp_tools]


def _convert_mcp_tool(mcp_tool: dict[str, Any]) -> dict[str, Any]:
    """Convert a single MCP tool definition to LLM tool-calling format.

    MCP uses ``inputSchema`` (camelCase); the tool-calling format uses
    ``input_schema`` (snake_case).  All other fields are passed through
    unchanged.
    """
    return {
        "name": mcp_tool.get("name", ""),
        "description": mcp_tool.get("description", ""),
        "input_schema": mcp_tool.get("inputSchema", mcp_tool.get("input_schema", {})),
    }


# ---------------------------------------------------------------------------
# Category helpers
# ---------------------------------------------------------------------------


def _categorise_tools(tools: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Build the categories dict mapping each category to its tool names.

    Tools whose names are not in ``_TOOL_TO_CATEGORY`` are assigned to
    ``project`` as a safe default.
    """
    categories: dict[str, list[str]] = {cat: [] for cat in _CATEGORY_MAP}
    for tool in tools:
        name: str = tool.get("name", "")
        category: str = _TOOL_TO_CATEGORY.get(name, "project")
        if category not in categories:
            categories[category] = []
        if name not in categories[category]:
            categories[category].append(name)
    # Remove empty categories
    return {cat: names for cat, names in categories.items() if names}


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------


def build_manifest(tools: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the full manifest dict from a list of tool definitions."""
    return {
        "tools": tools,
        "total_count": len(tools),
        "categories": _categorise_tools(tools),
    }


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------


def validate_manifest(manifest: dict[str, Any]) -> bool:
    """Return True iff the manifest passes basic sanity checks.

    Checks:
    - ``tools`` is a non-empty list
    - ``total_count`` >= 1 and matches len(tools)
    - ``categories`` is a non-empty dict
    """
    tools = manifest.get("tools")
    total_count = manifest.get("total_count", 0)
    categories = manifest.get("categories")

    if not isinstance(tools, list) or len(tools) < 1:
        return False
    if not isinstance(total_count, int) or total_count < 1:
        return False
    if total_count != len(tools):
        return False
    if not isinstance(categories, dict) or len(categories) == 0:
        return False
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point for the MCP tool discovery script.

    Returns 0 on success, 1 on manifest failure, 2 on argument error.
    """
    parser = argparse.ArgumentParser(
        description="Generate the QC MCP tools manifest for use with Aider."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help="Path to write the manifest JSON (default: config/qc_tools_manifest.json)",
    )
    parser.add_argument(
        "--mcp-url",
        default=_MCP_BASE_URL or None,
        help="MCP server base URL (overrides QC_MCP_BASE_URL env var)",
    )
    args = parser.parse_args(argv)

    output_path: Path = args.output

    # 1. Try live server first
    tools: list[dict[str, Any]] | None = None
    if args.mcp_url:
        print(
            f"[mcp_tool_discovery] Attempting live tool enumeration from {args.mcp_url} …"
        )
        tools = _fetch_live_tools(args.mcp_url)
        if tools is not None:
            print(
                f"[mcp_tool_discovery] Live enumeration succeeded: "
                f"{len(tools)} tools discovered."
            )

    # 2. Fall back to built-in static catalogue
    if tools is None:
        print(
            "[mcp_tool_discovery] Using static built-in tool catalogue "
            f"({len(_STATIC_TOOLS)} tools)."
        )
        tools = _STATIC_TOOLS

    manifest = build_manifest(tools)

    if not validate_manifest(manifest):
        print(
            "[mcp_tool_discovery] ERROR: generated manifest failed validation "
            "(total_count < 1 or malformed).",
            file=sys.stderr,
        )
        return 1

    # 3. Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        output_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(
            f"[mcp_tool_discovery] ERROR: cannot write manifest to {output_path}: {exc}",
            file=sys.stderr,
        )
        return 1

    print(
        f"[mcp_tool_discovery] Manifest written to {output_path} "
        f"(total_count={manifest['total_count']})."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
