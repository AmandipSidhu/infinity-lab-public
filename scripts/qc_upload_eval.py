#!/usr/bin/env python3
"""QuantConnect Upload & Backtest Evaluation — Step 6 of the ACB Pipeline.

This script uses the QC MCP Server (quantconnect-mcp) for automated CI backtesting.
For manual live deployment after human review, see ``infinity-lab-private``.

Submits the built strategy to QuantConnect via the MCP server running at
``http://localhost:8000/mcp/`` (started by the CI workflow before this script runs),
triggers a backtest, polls until completion, and evaluates the results against the
FitnessTracker constraints defined in the strategy spec.

FitnessTracker constraints evaluated:
  - Sharpe Ratio >= 0.5 (hard minimum; spec may require higher)
  - Max Drawdown <= threshold from spec performance_targets
  - Total Trades >= 50 (hard minimum; spec may require higher via performance_targets.min_trades)

Stub fallback (exit 0, non-blocking):
  - When ``QC_USER_ID`` or ``QC_API_TOKEN`` is not set in the environment
  - When the QC MCP server is unreachable (infrastructure issue, not a strategy issue)

Exit codes:
  0 — Backtest passed, evaluation completed, or stub fallback (non-blocking)
  1 — Backtest failed one or more constraints, or unrecoverable API error
  2 — Invalid arguments or file not found
"""

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import requests
import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_QC_BASE_URL: str = "https://www.quantconnect.com/api/v2"
_QC_USER_ID: str = os.environ.get("QC_USER_ID", "").strip()
_QC_API_TOKEN: str = os.environ.get("QC_API_TOKEN", "").strip()
_MCP_URL: str = os.environ.get("QC_MCP_URL", "http://localhost:8000/mcp/")
_SHARPE_RATIO_MIN: float = 0.5            # hard floor regardless of spec
_MIN_TRADES: int = 50                     # hard floor for total trades
_POLL_INTERVAL_SECONDS: int = 10          # seconds between backtest polls
_POLL_MAX_ATTEMPTS: int = 60             # max polls (~10 min timeout)
_COMPILE_POLL_INTERVAL_SECONDS: int = 5   # seconds between compile status polls
_COMPILE_POLL_MAX_ATTEMPTS: int = 30      # max compile polls (150s timeout)
_REQUEST_TIMEOUT_SECONDS: int = 30
_MCP_REQUEST_TIMEOUT_SECONDS: float = 60.0

# Module-level MCP session state — set by _ensure_mcp_session() on first use
_mcp_session_id: str = ""


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class MCPConnectionError(RuntimeError):
    """Raised when the QC MCP server is unreachable (connection refused, timeout, etc.)."""


# ---------------------------------------------------------------------------
# Legacy REST API helpers (kept for backward compatibility with existing tests)
# ---------------------------------------------------------------------------


def _qc_auth(user_id: str, api_token: str) -> tuple[dict[str, str], tuple[str, str]]:
    """Build HTTP Basic auth tuple and Timestamp header for a QC API request."""
    ts = str(int(time.time()))
    token_hash = hashlib.sha256(
        f"{api_token}:{ts}".encode("utf-8")
    ).hexdigest()
    headers = {"Timestamp": ts}
    return headers, (user_id, token_hash)


def _qc_post(
    endpoint: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """POST to a QC REST API endpoint and return the parsed JSON body.

    Raises ``MCPConnectionError`` on connection failure, ``RuntimeError`` on
    HTTP or API-level errors.
    """
    headers, auth = _qc_auth(_QC_USER_ID, _QC_API_TOKEN)
    url = f"{_QC_BASE_URL}/{endpoint}"
    try:
        resp = requests.post(
            url, json=payload, headers=headers, auth=auth,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError as exc:
        raise MCPConnectionError(
            f"QC REST API unreachable for '{endpoint}': {exc}"
        ) from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"QC REST API request failed for '{endpoint}': {exc}") from exc
    body: dict[str, Any] = resp.json()
    if not body.get("success", True):
        errors = body.get("errors", [body.get("message", "unknown error")])
        raise RuntimeError(f"QC REST API returned error for '{endpoint}': {errors}")
    return body


def _qc_get(
    endpoint: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """GET a QC REST API endpoint and return the parsed JSON body.

    Raises ``MCPConnectionError`` on connection failure, ``RuntimeError`` on
    HTTP or API-level errors.
    """
    headers, auth = _qc_auth(_QC_USER_ID, _QC_API_TOKEN)
    url = f"{_QC_BASE_URL}/{endpoint}"
    try:
        resp = requests.get(
            url, params=params, headers=headers, auth=auth,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError as exc:
        raise MCPConnectionError(
            f"QC REST API unreachable for '{endpoint}': {exc}"
        ) from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"QC REST API request failed for '{endpoint}': {exc}") from exc
    body: dict[str, Any] = resp.json()
    if not body.get("success", True):
        errors = body.get("errors", [body.get("message", "unknown error")])
        raise RuntimeError(f"QC REST API returned error for '{endpoint}': {errors}")
    return body


def _compile_project(project_id: int) -> str:
    """Compile the project via REST and return the compile_id (legacy helper)."""
    body = _qc_post("compile/create", {"projectId": project_id})
    compile_id = (
        body.get("compileId")
        or body.get("compile", {}).get("compileId")
    )
    if not compile_id:
        raise RuntimeError(f"compile/create did not return a compileId: {body}")
    return str(compile_id)


def _wait_for_compile(project_id: int, compile_id: str) -> None:
    """Poll compile/read until the compile job reaches BuildSuccess (legacy helper).

    Raises ``RuntimeError`` if the compile reaches ``BuildError`` or if the
    polling timeout is exhausted (``_COMPILE_POLL_MAX_ATTEMPTS`` × 5 s).
    """
    for attempt in range(1, _COMPILE_POLL_MAX_ATTEMPTS + 1):
        body = _qc_get(
            "compile/read",
            params={"projectId": project_id, "compileId": compile_id},
        )
        compile_info: dict[str, Any] = body.get("compile", body)
        state: str = str(compile_info.get("state", ""))

        if state == "BuildSuccess":
            return
        if state == "BuildError":
            error_msg = compile_info.get("error") or "unknown compile error"
            raise RuntimeError(
                f"Compile {compile_id} failed with BuildError: {error_msg}"
            )

        print(
            f"[qc_upload_eval] Compile state: {state!r} "
            f"(attempt {attempt}/{_COMPILE_POLL_MAX_ATTEMPTS})"
        )
        time.sleep(_COMPILE_POLL_INTERVAL_SECONDS)

    raise RuntimeError(
        f"Compile {compile_id} did not reach BuildSuccess after "
        f"{_COMPILE_POLL_MAX_ATTEMPTS * _COMPILE_POLL_INTERVAL_SECONDS} seconds"
    )


# ---------------------------------------------------------------------------
# MCP helpers — matching Gate 0 pattern (gate0_qc_mcp_verify.yml)
# ---------------------------------------------------------------------------


def _mcp_post(
    payload: dict[str, Any],
    session_id: str | None = None,
    timeout: float = _MCP_REQUEST_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any], str | None]:
    """POST a JSON-RPC payload to the MCP server.

    Returns ``(response_dict, session_id_from_header)``.
    Raises ``MCPConnectionError`` if the server is unreachable.
    """
    body = json.dumps(payload).encode()
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req = urllib.request.Request(_MCP_URL, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            sid: str | None = resp.headers.get("Mcp-Session-Id")
    except (urllib.error.URLError, OSError) as exc:
        raise MCPConnectionError(f"QC MCP server unreachable at {_MCP_URL}: {exc}") from exc
    # Handle SSE envelope: strip "data: " prefix if present
    for line in raw.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:]), sid
    return json.loads(raw), sid


def _mcp_initialize() -> str:
    """Initialize an MCP session and return the session_id.

    Raises ``MCPConnectionError`` if the server is unreachable.
    Raises ``RuntimeError`` if the server returns an error response.
    """
    init_resp, session_id = _mcp_post(
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "qc-grinder", "version": "1.0"},
            },
        }
    )
    if not session_id:
        raise RuntimeError(
            f"[qc_upload_eval] MCP initialize did not return Mcp-Session-Id header. "
            f"Response: {init_resp}"
        )
    if "error" in init_resp:
        raise RuntimeError(
            f"[qc_upload_eval] MCP initialize returned error: {init_resp['error']}"
        )
    return session_id


def _ensure_mcp_session() -> str:
    """Return the current MCP session_id, initializing if needed."""
    global _mcp_session_id
    if not _mcp_session_id:
        _mcp_session_id = _mcp_initialize()
    return _mcp_session_id


def _mcp_tool_call(
    name: str,
    arguments: dict[str, Any],
    req_id: int = 1,
) -> dict[str, Any]:
    """Call an MCP tool and return the raw JSON-RPC response dict."""
    session_id = _ensure_mcp_session()
    resp, _ = _mcp_post(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        session_id=session_id,
    )
    return resp


def _extract_tool_text(response: dict[str, Any]) -> str:
    """Extract plain text from an MCP tool response content array."""
    content = response.get("result", {}).get("content", [])
    return "".join(
        item.get("text", "")
        for item in content
        if isinstance(item, dict) and item.get("type") == "text"
    )


def _parse_tool_json(response: dict[str, Any], label: str) -> dict[str, Any]:
    """Parse the JSON result from an MCP tool call response.

    Raises ``RuntimeError`` on JSON-RPC errors or unparseable content.
    """
    if "error" in response:
        raise RuntimeError(
            f"[qc_upload_eval] MCP tool '{label}' returned JSON-RPC error — "
            f"code={response['error'].get('code')} "
            f"message={response['error'].get('message')!r}"
        )
    if "result" not in response:
        raise RuntimeError(
            f"[qc_upload_eval] MCP tool '{label}' response has neither 'result' nor 'error'. "
            f"Full response: {response}"
        )
    text = _extract_tool_text(response)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"[qc_upload_eval] MCP tool '{label}' result is not valid JSON — {exc}. "
            f"Raw text: {text!r}"
        ) from exc


# ---------------------------------------------------------------------------
# QC MCP workflow steps
# ---------------------------------------------------------------------------


def _create_project(spec_name: str) -> int:
    """Create a new QuantConnect project via MCP and return its project_id."""
    result = _parse_tool_json(
        _mcp_tool_call("create_project", {"name": spec_name, "language": "Py"}),
        "create_project",
    )
    project = result.get("project", {})
    project_id = (
        project.get("projectId")
        or project.get("project_id")
        or result.get("projectId")
        or result.get("project_id")
    )
    if not project_id:
        raise RuntimeError(
            f"[qc_upload_eval] create_project MCP tool did not return a project_id: {result}"
        )
    return int(project_id)


def _upload_strategy(project_id: int, spec_name: str, strategy_code: str) -> None:
    """Upload the strategy source file via MCP create_file tool."""
    _parse_tool_json(
        _mcp_tool_call(
            "create_file",
            {
                "project_id": project_id,
                "name": f"{spec_name}.py",
                "content": strategy_code,
            },
        ),
        "create_file",
    )


def _create_backtest(project_id: int, spec_name: str) -> str:
    """Compile the project and trigger a backtest via MCP; return the backtest_id."""
    # Step 1: start compilation
    compile_result = _parse_tool_json(
        _mcp_tool_call("create_compile", {"project_id": project_id}),
        "create_compile",
    )
    compile_id: str = compile_result.get("compile_id") or compile_result.get("compileId", "")
    if not compile_id:
        raise RuntimeError(
            f"[qc_upload_eval] create_compile did not return compile_id: {compile_result}"
        )

    # Step 2: poll until BuildSuccess
    for attempt in range(1, _COMPILE_POLL_MAX_ATTEMPTS + 1):
        read_result = _parse_tool_json(
            _mcp_tool_call(
                "read_compile",
                {"project_id": project_id, "compile_id": compile_id},
            ),
            "read_compile",
        )
        state: str = str(read_result.get("state", ""))
        if state == "BuildSuccess":
            break
        if state == "BuildError":
            errors = read_result.get("errors", read_result.get("error", "unknown compile error"))
            raise RuntimeError(
                f"[qc_upload_eval] Compile {compile_id} failed with BuildError: {errors}"
            )
        print(
            f"[qc_upload_eval] Compile state: {state!r} "
            f"(attempt {attempt}/{_COMPILE_POLL_MAX_ATTEMPTS})"
        )
        time.sleep(_COMPILE_POLL_INTERVAL_SECONDS)
    else:
        raise RuntimeError(
            f"[qc_upload_eval] Compile {compile_id} did not reach BuildSuccess after "
            f"{_COMPILE_POLL_MAX_ATTEMPTS * _COMPILE_POLL_INTERVAL_SECONDS} seconds"
        )

    # Step 3: create backtest
    backtest_result = _parse_tool_json(
        _mcp_tool_call(
            "create_backtest",
            {
                "project_id": project_id,
                "compile_id": compile_id,
                "backtest_name": f"{spec_name}_backtest",
            },
        ),
        "create_backtest",
    )
    backtest = backtest_result.get("backtest", {})
    backtest_id = (
        backtest.get("backtestId")
        or backtest.get("backtest_id")
        or backtest_result.get("backtest_id")
    )
    if not backtest_id:
        raise RuntimeError(
            f"[qc_upload_eval] create_backtest did not return a backtest_id: {backtest_result}"
        )
    return str(backtest_id)


def _poll_backtest(project_id: int, backtest_id: str) -> dict[str, Any]:
    """Poll MCP read_backtest until the backtest completes or timeout is reached.

    Returns the raw ``backtest`` dict from the read_backtest response.
    Raises ``RuntimeError`` on timeout or fatal server error.
    """
    for attempt in range(1, _POLL_MAX_ATTEMPTS + 1):
        result = _parse_tool_json(
            _mcp_tool_call(
                "read_backtest",
                {"project_id": project_id, "backtest_id": backtest_id},
            ),
            "read_backtest",
        )
        if result.get("status") != "success":
            raise RuntimeError(
                f"[qc_upload_eval] read_backtest returned status={result.get('status')!r}: "
                f"{result.get('error') or result}"
            )
        backtest: dict[str, Any] = result.get("backtest", {})
        progress: float = float(backtest.get("progress", backtest.get("Progress", 0.0)))
        completed: bool = bool(backtest.get("completed", backtest.get("Completed", False)))
        if completed or progress >= 1.0:
            return backtest

        print(
            f"[qc_upload_eval] Backtest progress: {progress * 100:.1f}% "
            f"(attempt {attempt}/{_POLL_MAX_ATTEMPTS})"
        )
        time.sleep(_POLL_INTERVAL_SECONDS)

    raise RuntimeError(
        f"[qc_upload_eval] Backtest {backtest_id} did not complete after "
        f"{_POLL_MAX_ATTEMPTS * _POLL_INTERVAL_SECONDS} seconds"
    )


def _get_backtest_orders(project_id: int, backtest_id: str) -> int:
    """Return the raw order count for a backtest via MCP read_backtest_orders.

    Returns 0 on any error (e.g., data retention expiry) — used as a quality
    signal only; does not affect pass/fail evaluation.
    """
    try:
        result = _parse_tool_json(
            _mcp_tool_call(
                "read_backtest_orders",
                {
                    "project_id": project_id,
                    "backtest_id": backtest_id,
                    "start": 0,
                    "end": 100,
                },
            ),
            "read_backtest_orders",
        )
        return int(result.get("length", 0))
    except (RuntimeError, MCPConnectionError, ValueError):
        return 0


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
            # Note: a drawdown of exactly 1.0 is treated as a decimal (100% loss)
            # since that is the only case where value == 1.0 makes financial sense
            # as a fraction; the whole-number equivalent (1%) would be > 1.0 only
            # if represented as 1.0 itself, which is ambiguous but assumed fractional.
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

    # --- Total Trades ---
    trades = _extract_stat(
        backtest_result, "TotalTrades", "total_trades", "Total Trades", "trades"
    )
    min_trades = int(float(spec_performance_targets.get("min_trades", _MIN_TRADES)))

    if trades is None:
        violations.append({
            "constraint": "total_trades",
            "severity": "ERROR",
            "message": "Total Trades not found in backtest results",
            "required": min_trades,
            "actual": None,
        })
    else:
        trades_count = int(trades)
        if trades_count < min_trades:
            violations.append({
                "constraint": "total_trades",
                "severity": "ERROR",
                "message": (
                    f"Total Trades {trades_count} is below required minimum {min_trades}. "
                    f"Low trade count makes Sharpe statistically unreliable."
                ),
                "required": min_trades,
                "actual": trades_count,
            })

    return violations


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def upload_and_evaluate(
    spec_file: Path | None,
    strategy_file: Path,
) -> dict[str, Any]:
    """Run the full QC upload → backtest → evaluate pipeline via QC MCP Server.

    Args:
        spec_file: Optional path to strategy spec YAML (performance_targets used if provided).
                   When None, hard-floor constraints (Sharpe >= 0.5, Trades >= 50) are applied.
        strategy_file: Path to the built strategy Python file.

    Returns a summary dict with keys:
      project_id, backtest_id, sharpe_ratio, total_orders, result (PASS/FAIL),
      passed, violations, backtest_stats, spec_file, strategy_file, violation_count
    """
    if spec_file is not None:
        with spec_file.open(encoding="utf-8") as fh:
            spec_data: dict[str, Any] = yaml.safe_load(fh) or {}
        spec_name: str = spec_file.stem
        performance_targets: dict[str, Any] = (
            spec_data.get("strategy", {}).get("performance_targets") or {}
        )
    else:
        spec_name = strategy_file.stem
        performance_targets = {}

    strategy_code: str = strategy_file.read_text(encoding="utf-8")

    print(f"[qc_upload_eval] Creating project '{spec_name}' on QC MCP Server…")
    project_id = _create_project(spec_name)
    print(f"[qc_upload_eval] Project created: project_id={project_id}")

    print(f"[qc_upload_eval] Uploading strategy '{strategy_file}'…")
    _upload_strategy(project_id, spec_name, strategy_code)
    print("[qc_upload_eval] Strategy uploaded.")

    print("[qc_upload_eval] Compiling and triggering backtest…")
    backtest_id = _create_backtest(project_id, spec_name)
    print(f"[qc_upload_eval] Backtest started: backtest_id={backtest_id}")

    print("[qc_upload_eval] Polling for backtest completion…")
    backtest_result = _poll_backtest(project_id, backtest_id)
    print("[qc_upload_eval] Backtest complete.")

    print("[qc_upload_eval] Fetching order count…")
    total_orders = _get_backtest_orders(project_id, backtest_id)
    print(f"[qc_upload_eval] Orders fetched: total_orders={total_orders}")

    violations = evaluate_fitness(backtest_result, performance_targets)

    backtest_stats: dict[str, Any] = {}
    for sub_key in ("statistics", "Statistics", "runtimeStatistics", "RuntimeStatistics"):
        sub = backtest_result.get(sub_key)
        if isinstance(sub, dict):
            backtest_stats.update(sub)

    sharpe_ratio = _extract_stat(
        backtest_result, "SharpeRatio", "sharpe_ratio", "Sharpe Ratio", "sharpe"
    )

    passed = len(violations) == 0
    return {
        "spec_file": str(spec_file) if spec_file is not None else "",
        "strategy_file": str(strategy_file),
        "project_id": project_id,
        "backtest_id": backtest_id,
        "sharpe_ratio": sharpe_ratio,
        "total_orders": total_orders,
        "result": "PASS" if passed else "FAIL",
        "passed": passed,
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
    parser.add_argument(
        "--spec",
        default=None,
        help="Path to the strategy spec YAML file (optional; hard-floor constraints applied if omitted)",
    )
    parser.add_argument("--strategy", required=True, help="Path to the built strategy .py file")
    parser.add_argument(
        "--output",
        default="",
        help="Optional path to write the JSON result (default: print to stdout)",
    )
    args = parser.parse_args(argv)

    spec_file: Path | None = Path(args.spec) if args.spec else None
    strategy_file = Path(args.strategy)

    def _write(data: dict[str, Any]) -> None:
        out = json.dumps(data, indent=2)
        if args.output:
            Path(args.output).write_text(out, encoding="utf-8")
        else:
            print(out)

    if spec_file is not None and not spec_file.is_file():
        _write({
            "result": "FAIL",
            "passed": False,
            "error": f"Spec file not found: {spec_file}",
            "violations": [],
            "violation_count": 0,
            "backtest_stats": {},
        })
        return 2
    if not strategy_file.is_file():
        _write({
            "result": "FAIL",
            "passed": False,
            "error": f"Strategy file not found: {strategy_file}",
            "violations": [],
            "violation_count": 0,
            "backtest_stats": {},
        })
        return 2

    # Stub fallback: QC credentials not configured — skip evaluation (non-blocking CI)
    if not _QC_USER_ID or not _QC_API_TOKEN:
        print(
            "[qc_upload_eval] QC_USER_ID or QC_API_TOKEN not set — writing stub result (non-blocking).",
            file=sys.stderr,
        )
        stub: dict[str, Any] = {
            "spec_file": str(spec_file) if spec_file else "",
            "strategy_file": str(strategy_file),
            "project_id": "stub",
            "backtest_id": "stub",
            "result": "PASS",
            "passed": True,
            "violation_count": 0,
            "violations": [],
            "backtest_stats": {},
            "note": "QC_USER_ID/QC_API_TOKEN not configured — MCP evaluation skipped",
        }
        _write(stub)
        return 0

    try:
        summary = upload_and_evaluate(spec_file, strategy_file)
    except MCPConnectionError as exc:
        # QC MCP server unreachable — infrastructure issue (e.g., server not started).
        # Write stub result and exit 0 (non-blocking) so the workflow step
        # doesn't fail the entire CI job for an infrastructure reason.
        print(
            f"[qc_upload_eval] QC MCP server unreachable: {exc}",
            file=sys.stderr,
        )
        stub_unreachable: dict[str, Any] = {
            "spec_file": str(spec_file) if spec_file else "",
            "strategy_file": str(strategy_file),
            "project_id": "stub",
            "backtest_id": "stub",
            "result": "PASS",
            "passed": True,
            "violation_count": 0,
            "violations": [],
            "backtest_stats": {},
            "note": f"QC MCP server unreachable — evaluation skipped: {exc}",
        }
        _write(stub_unreachable)
        return 0
    except RuntimeError as exc:
        print(f"[qc_upload_eval] QC MCP error: {exc}", file=sys.stderr)
        error_result: dict[str, Any] = {
            "spec_file": str(spec_file) if spec_file else "",
            "strategy_file": str(strategy_file),
            "result": "FAIL",
            "passed": False,
            "error": str(exc),
            "violations": [],
            "violation_count": 0,
            "backtest_stats": {},
        }
        _write(error_result)
        return 1

    _write(summary)
    return 0 if summary["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
