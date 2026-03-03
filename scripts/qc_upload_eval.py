#!/usr/bin/env python3
"""QuantConnect Upload & Backtest Evaluation — Step 6 of the ACB Pipeline.

Uploads the built strategy to the QuantConnect MCP Server running on
localhost:8000 via JSON-RPC 2.0 HTTP requests, triggers a backtest, polls
until completion, and evaluates the results against the FitnessTracker
constraints defined in the strategy spec.

FitnessTracker constraints evaluated:
  - Sharpe Ratio >= 0.5 (hard minimum; spec may require higher)
  - Max Drawdown <= threshold from spec performance_targets

The MCP server is expected to expose the following tools:
  - create_project   (arguments: name) → {project_id}
  - create_file      (arguments: project_id, name, content) → {file_id}
  - create_backtest  (arguments: project_id, name) → {backtest_id}
  - read_backtest    (arguments: project_id, backtest_id) → {statistics, ...}

Exit codes:
  0 — Backtest passed all FitnessTracker constraints
  1 — Backtest failed one or more constraints, or MCP server error
  2 — Invalid arguments or file not found
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests
import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MCP_BASE_URL: str = os.environ.get("QC_MCP_BASE_URL", "http://localhost:8000/mcp")
_SHARPE_RATIO_MIN: float = 0.5            # hard floor regardless of spec
_POLL_INTERVAL_SECONDS: int = 10          # seconds between backtest polls
_POLL_MAX_ATTEMPTS: int = 60             # max polls (~10 min timeout)
_REQUEST_TIMEOUT_SECONDS: int = 30


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------


def _rpc_call(
    tool_name: str,
    arguments: dict[str, Any],
    call_id: str = "1",
) -> dict[str, Any]:
    """Send a single JSON-RPC 2.0 ``tools/call`` request to the MCP server.

    Returns the parsed ``result`` object from the response.
    Raises ``RuntimeError`` on transport or protocol errors.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": call_id,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }
    try:
        response = requests.post(
            _MCP_BASE_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"MCP server request failed for '{tool_name}': {exc}") from exc

    body = response.json()
    if "error" in body:
        err = body["error"]
        raise RuntimeError(
            f"MCP server returned error for '{tool_name}': "
            f"[{err.get('code')}] {err.get('message')}"
        )

    result = body.get("result", {})
    # MCP tool results may wrap text content in a content array
    content = result.get("content", [])
    if content and isinstance(content, list):
        first = content[0]
        if isinstance(first, dict) and first.get("type") == "text":
            try:
                return json.loads(first["text"])
            except (json.JSONDecodeError, KeyError):
                return {"raw": first.get("text", "")}
    return result


# ---------------------------------------------------------------------------
# MCP workflow steps
# ---------------------------------------------------------------------------


def _create_project(spec_name: str) -> int:
    """Create a new QuantConnect project and return its project_id."""
    result = _rpc_call("create_project", {"name": spec_name}, call_id="create_project")
    project_id = result.get("projectId") or result.get("project_id")
    if not project_id:
        raise RuntimeError(f"create_project did not return a project_id: {result}")
    return int(project_id)


def _upload_strategy(project_id: int, spec_name: str, strategy_code: str) -> None:
    """Upload the strategy source file into the QuantConnect project."""
    _rpc_call(
        "create_file",
        {
            "projectId": project_id,
            "name": f"{spec_name}.py",
            "content": strategy_code,
        },
        call_id="create_file",
    )


def _create_backtest(project_id: int, spec_name: str) -> str:
    """Trigger a backtest and return the backtest_id."""
    result = _rpc_call(
        "create_backtest",
        {"projectId": project_id, "name": f"{spec_name}_backtest"},
        call_id="create_backtest",
    )
    backtest_id = result.get("backtestId") or result.get("backtest_id")
    if not backtest_id:
        raise RuntimeError(f"create_backtest did not return a backtest_id: {result}")
    return str(backtest_id)


def _poll_backtest(project_id: int, backtest_id: str) -> dict[str, Any]:
    """Poll the MCP server until the backtest completes or timeout is reached.

    Returns the final backtest result dict.
    Raises ``RuntimeError`` on timeout or fatal server error.
    """
    for attempt in range(1, _POLL_MAX_ATTEMPTS + 1):
        result = _rpc_call(
            "read_backtest",
            {"projectId": project_id, "backtestId": backtest_id},
            call_id=f"read_backtest_{attempt}",
        )
        progress: float = float(result.get("progress", result.get("Progress", 0.0)))
        completed: bool = result.get("completed", result.get("Completed", False))

        if completed or progress >= 1.0:
            return result

        print(
            f"[qc_upload_eval] Backtest progress: {progress * 100:.1f}% "
            f"(attempt {attempt}/{_POLL_MAX_ATTEMPTS})"
        )
        time.sleep(_POLL_INTERVAL_SECONDS)

    raise RuntimeError(
        f"Backtest {backtest_id} did not complete after "
        f"{_POLL_MAX_ATTEMPTS * _POLL_INTERVAL_SECONDS} seconds"
    )


# ---------------------------------------------------------------------------
# FitnessTracker evaluation
# ---------------------------------------------------------------------------


def _extract_stat(backtest_result: dict[str, Any], *keys: str) -> float | None:
    """Attempt to extract a numeric statistic from the backtest result.

    Tries each key in sequence, searching both top-level and nested
    ``statistics`` / ``Statistics`` sub-dicts.
    """
    search_targets: list[dict[str, Any]] = [backtest_result]
    for sub_key in ("statistics", "Statistics", "runtimeStatistics", "RuntimeStatistics"):
        sub = backtest_result.get(sub_key)
        if isinstance(sub, dict):
            search_targets.append(sub)

    for key in keys:
        for target in search_targets:
            value = target.get(key)
            if value is not None:
                try:
                    return float(str(value).replace("%", "").strip())
                except (ValueError, TypeError):
                    continue
    return None


def evaluate_fitness(
    backtest_result: dict[str, Any],
    spec_performance_targets: dict[str, Any],
) -> list[dict[str, Any]]:
    """Evaluate FitnessTracker constraints against the backtest results.

    Returns a list of constraint violation dicts (empty list = all passed).
    """
    violations: list[dict[str, Any]] = []

    # --- Sharpe Ratio ---
    sharpe = _extract_stat(
        backtest_result, "SharpeRatio", "sharpe_ratio", "Sharpe Ratio", "sharpe"
    )
    spec_sharpe_min = spec_performance_targets.get("sharpe_ratio_min")
    effective_sharpe_min = max(
        _SHARPE_RATIO_MIN,
        float(spec_sharpe_min) if spec_sharpe_min is not None else _SHARPE_RATIO_MIN,
    )

    if sharpe is None:
        violations.append({
            "constraint": "sharpe_ratio",
            "severity": "ERROR",
            "message": "Sharpe Ratio not found in backtest results",
            "required": effective_sharpe_min,
            "actual": None,
        })
    elif sharpe < effective_sharpe_min:
        violations.append({
            "constraint": "sharpe_ratio",
            "severity": "ERROR",
            "message": (
                f"Sharpe Ratio {sharpe:.4f} is below required minimum {effective_sharpe_min:.4f}"
            ),
            "required": effective_sharpe_min,
            "actual": sharpe,
        })

    # --- Max Drawdown ---
    drawdown = _extract_stat(
        backtest_result,
        "Drawdown", "MaxDrawdown", "max_drawdown", "Max Drawdown", "drawdown",
    )
    spec_dd_threshold = spec_performance_targets.get("max_drawdown_threshold")
    if spec_dd_threshold is not None:
        dd_threshold = float(spec_dd_threshold)
        if drawdown is None:
            violations.append({
                "constraint": "max_drawdown",
                "severity": "ERROR",
                "message": "Max Drawdown not found in backtest results",
                "required": dd_threshold,
                "actual": None,
            })
        else:
            # Drawdown may be expressed as a decimal fraction (e.g., 0.15 = 15%)
            # or as a whole-number percentage (e.g., 15.0 = 15%). Values > 1.0 are
            # treated as whole-number percentages and normalized by dividing by 100.
            dd_normalized = drawdown / 100.0 if drawdown > 1.0 else drawdown
            if dd_normalized > dd_threshold:
                violations.append({
                    "constraint": "max_drawdown",
                    "severity": "ERROR",
                    "message": (
                        f"Max Drawdown {dd_normalized:.4f} exceeds allowed threshold "
                        f"{dd_threshold:.4f}"
                    ),
                    "required": dd_threshold,
                    "actual": dd_normalized,
                })

    return violations


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def upload_and_evaluate(
    spec_file: Path,
    strategy_file: Path,
) -> dict[str, Any]:
    """Run the full QC upload → backtest → evaluate pipeline.

    Returns a summary dict with keys:
      project_id, backtest_id, result (PASS/FAIL), violations, backtest_stats
    """
    with spec_file.open(encoding="utf-8") as fh:
        spec_data: dict[str, Any] = yaml.safe_load(fh) or {}

    spec_name: str = spec_file.stem
    strategy_code: str = strategy_file.read_text(encoding="utf-8")
    performance_targets: dict[str, Any] = (
        spec_data.get("strategy", {}).get("performance_targets") or {}
    )

    print(f"[qc_upload_eval] Creating project '{spec_name}' on QC MCP Server…")
    project_id = _create_project(spec_name)
    print(f"[qc_upload_eval] Project created: project_id={project_id}")

    print(f"[qc_upload_eval] Uploading strategy '{strategy_file}'…")
    _upload_strategy(project_id, spec_name, strategy_code)
    print("[qc_upload_eval] Strategy uploaded.")

    print("[qc_upload_eval] Triggering backtest…")
    backtest_id = _create_backtest(project_id, spec_name)
    print(f"[qc_upload_eval] Backtest started: backtest_id={backtest_id}")

    print("[qc_upload_eval] Polling for backtest completion…")
    backtest_result = _poll_backtest(project_id, backtest_id)
    print("[qc_upload_eval] Backtest complete.")

    violations = evaluate_fitness(backtest_result, performance_targets)

    backtest_stats: dict[str, Any] = {}
    for sub_key in ("statistics", "Statistics", "runtimeStatistics", "RuntimeStatistics"):
        sub = backtest_result.get(sub_key)
        if isinstance(sub, dict):
            backtest_stats.update(sub)

    return {
        "spec_file": str(spec_file),
        "strategy_file": str(strategy_file),
        "project_id": project_id,
        "backtest_id": backtest_id,
        "result": "FAIL" if violations else "PASS",
        "violation_count": len(violations),
        "violations": violations,
        "backtest_stats": backtest_stats,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="QuantConnect Upload & Backtest Evaluation — Step 6 of the ACB Pipeline"
    )
    parser.add_argument("--spec", required=True, help="Path to the strategy spec YAML file")
    parser.add_argument("--strategy", required=True, help="Path to the built strategy .py file")
    parser.add_argument(
        "--output",
        default="",
        help="Optional path to write the JSON result (default: print to stdout)",
    )
    args = parser.parse_args(argv)

    spec_file = Path(args.spec)
    strategy_file = Path(args.strategy)

    if not spec_file.is_file():
        error_summary = {
            "spec_file": str(spec_file),
            "strategy_file": str(strategy_file),
            "result": "FAIL",
            "error": f"Spec file not found: {spec_file}",
            "violations": [],
            "backtest_stats": {},
        }
        output_json = json.dumps(error_summary, indent=2)
        if args.output:
            Path(args.output).write_text(output_json, encoding="utf-8")
        else:
            print(output_json)
        return 1
    if not strategy_file.is_file():
        error_summary = {
            "spec_file": str(spec_file),
            "strategy_file": str(strategy_file),
            "result": "FAIL",
            "error": f"Strategy file not found: {strategy_file}",
            "violations": [],
            "backtest_stats": {},
        }
        output_json = json.dumps(error_summary, indent=2)
        if args.output:
            Path(args.output).write_text(output_json, encoding="utf-8")
        else:
            print(output_json)
        return 1

    try:
        summary = upload_and_evaluate(spec_file, strategy_file)
    except RuntimeError as exc:
        error_summary = {
            "spec_file": str(spec_file),
            "strategy_file": str(strategy_file),
            "result": "FAIL",
            "error": str(exc),
            "violations": [],
            "backtest_stats": {},
        }
        output_json = json.dumps(error_summary, indent=2)
        if args.output:
            Path(args.output).write_text(output_json, encoding="utf-8")
        else:
            print(output_json)
        return 1

    output_json = json.dumps(summary, indent=2)
    if args.output:
        Path(args.output).write_text(output_json, encoding="utf-8")
    else:
        print(output_json)

    return 0 if summary["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
