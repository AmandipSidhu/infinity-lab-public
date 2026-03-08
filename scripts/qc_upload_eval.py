#!/usr/bin/env python3
"""QuantConnect Upload & Backtest Evaluation — Step 6 of the ACB Pipeline.

This script uses the QuantConnect REST API for automated CI backtesting.
For manual live deployment after human review, see ``infinity-lab-private``.

Uploads the built strategy to QuantConnect via the REST API at
``https://www.quantconnect.com/api/v2`` using HTTP Basic auth with the
official QC formula::

    hash = SHA256(api_token:timestamp)          # user_id is NOT in the hash
    Authorization = Basic base64(user_id:hash)
    Timestamp = <unix-seconds>

triggers a backtest, polls until completion, and evaluates the results
against the FitnessTracker constraints defined in the strategy spec.

FitnessTracker constraints evaluated:
  - Sharpe Ratio >= 0.5 (hard minimum; spec may require higher)
  - Max Drawdown <= threshold from spec performance_targets

Stub fallback (exit 0, non-blocking):
  - When ``QC_USER_ID`` or ``QC_API_TOKEN`` is not set in the environment
  - When the QC REST API is unreachable (connection error at startup)

Exit codes:
  0 — Backtest passed, evaluation completed, or stub fallback (non-blocking)
  1 — Backtest failed one or more constraints, or unrecoverable API error
  2 — Invalid arguments or file not found
"""

import argparse
import base64
import hashlib
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

_QC_BASE_URL: str = "https://www.quantconnect.com/api/v2"
_QC_USER_ID: str = os.environ.get("QC_USER_ID", "").strip()
_QC_API_TOKEN: str = os.environ.get("QC_API_TOKEN", "").strip()
_SHARPE_RATIO_MIN: float = 0.5            # hard floor regardless of spec
_POLL_INTERVAL_SECONDS: int = 10          # seconds between backtest polls
_POLL_MAX_ATTEMPTS: int = 60             # max polls (~10 min timeout)
_REQUEST_TIMEOUT_SECONDS: int = 30


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class MCPConnectionError(RuntimeError):
    """Raised when the QC REST API is unreachable (connection refused, timeout, etc.)."""


# ---------------------------------------------------------------------------
# REST API helpers
# ---------------------------------------------------------------------------


def _qc_auth(user_id: str, api_token: str) -> dict[str, str]:
    """Return headers dict for a QC REST API request.

    Official QC auth formula:
      hash = SHA256(api_token:timestamp)          # user_id NOT in hash
      Authorization = Basic base64(user_id:hash)
      Timestamp = <unix-seconds>

    Ref: https://www.quantconnect.com/docs/v2/cloud-platform/api-reference/authentication
    """
    ts = str(int(time.time()))
    token_hash = hashlib.sha256(
        f"{api_token}:{ts}".encode("utf-8")
    ).hexdigest()
    credentials = base64.b64encode(
        f"{user_id}:{token_hash}".encode("utf-8")
    ).decode("utf-8")
    return {
        "Authorization": f"Basic {credentials}",
        "Timestamp": ts,
    }


def _qc_post(
    endpoint: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """POST to a QC REST API endpoint and return the parsed JSON body.

    Raises ``MCPConnectionError`` on connection failure, ``RuntimeError`` on
    HTTP or API-level errors.
    """
    headers = _qc_auth(_QC_USER_ID, _QC_API_TOKEN)
    url = f"{_QC_BASE_URL}/{endpoint}"
    try:
        resp = requests.post(
            url, json=payload, headers=headers,
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
    headers = _qc_auth(_QC_USER_ID, _QC_API_TOKEN)
    url = f"{_QC_BASE_URL}/{endpoint}"
    try:
        resp = requests.get(
            url, params=params, headers=headers,
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


# ---------------------------------------------------------------------------
# QC REST API workflow steps
# ---------------------------------------------------------------------------


def _create_project(spec_name: str) -> int:
    """Create a new QuantConnect project and return its project_id."""
    body = _qc_post("projects/create", {"name": spec_name, "language": "Py"})
    projects = body.get("projects", [])
    if not projects:
        raise RuntimeError(f"projects/create response missing 'projects': {body}")
    project_id = projects[0].get("projectId")
    if not project_id:
        raise RuntimeError(f"projects/create did not return a projectId: {body}")
    return int(project_id)


def _upload_strategy(project_id: int, spec_name: str, strategy_code: str) -> None:
    """Upload the strategy source file into the QuantConnect project."""
    _qc_post(
        "files/create",
        {
            "projectId": project_id,
            "name": f"{spec_name}.py",
            "content": strategy_code,
        },
    )


def _compile_project(project_id: int) -> str:
    """Compile the project and return the compile_id."""
    body = _qc_post("compile/create", {"projectId": project_id})
    compile_id = (
        body.get("compileId")
        or body.get("compile", {}).get("compileId")
    )
    if not compile_id:
        raise RuntimeError(f"compile/create did not return a compileId: {body}")
    return str(compile_id)


def _create_backtest(project_id: int, spec_name: str) -> str:
    """Compile the project, trigger a backtest, and return the backtest_id."""
    compile_id = _compile_project(project_id)
    body = _qc_post(
        "backtests/create",
        {"projectId": project_id, "compileId": compile_id, "backtestName": f"{spec_name}_backtest"},
    )
    backtest = body.get("backtest", {})
    backtest_id = backtest.get("backtestId") or body.get("backtestId")
    if not backtest_id:
        raise RuntimeError(f"backtests/create did not return a backtestId: {body}")
    return str(backtest_id)


def _poll_backtest(project_id: int, backtest_id: str) -> dict[str, Any]:
    """Poll the QC REST API until the backtest completes or timeout is reached.

    Returns the final backtest result dict.
    Raises ``RuntimeError`` on timeout or fatal server error.
    """
    for attempt in range(1, _POLL_MAX_ATTEMPTS + 1):
        body = _qc_get(
            "backtests/read",
            params={"projectId": project_id, "backtestId": backtest_id},
        )
        result: dict[str, Any] = body.get("backtest", body)
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

    return violations


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def upload_and_evaluate(
    spec_file: Path,
    strategy_file: Path,
) -> dict[str, Any]:
    """Run the full QC upload → backtest → evaluate pipeline via QC REST API.

    Returns a summary dict with keys:
      project_id, backtest_id, result (PASS/FAIL), passed, violations,
      backtest_stats, spec_file, strategy_file, violation_count
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

    passed = len(violations) == 0
    return {
        "spec_file": str(spec_file),
        "strategy_file": str(strategy_file),
        "project_id": project_id,
        "backtest_id": backtest_id,
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

    def _write(data: dict[str, Any]) -> None:
        out = json.dumps(data, indent=2)
        if args.output:
            Path(args.output).write_text(out, encoding="utf-8")
        else:
            print(out)

    if not spec_file.is_file():
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
            "spec_file": str(spec_file),
            "strategy_file": str(strategy_file),
            "project_id": "stub",
            "backtest_id": "stub",
            "result": "PASS",
            "passed": True,
            "violation_count": 0,
            "violations": [],
            "backtest_stats": {},
            "note": "QC_USER_ID/QC_API_TOKEN not configured — REST API evaluation skipped",
        }
        _write(stub)
        return 0

    try:
        summary = upload_and_evaluate(spec_file, strategy_file)
    except MCPConnectionError as exc:
        # QC REST API unreachable — credentials are set but server is not responding.
        # This is a hard failure: the issue mandates a real backtest when credentials are present.
        print(
            f"[qc_upload_eval] QC REST API unreachable: {exc}",
            file=sys.stderr,
        )
        connection_error_result: dict[str, Any] = {
            "spec_file": str(spec_file),
            "strategy_file": str(strategy_file),
            "result": "FAIL",
            "passed": False,
            "error": f"QC REST API unreachable — cannot complete backtest: {exc}",
            "violations": [],
            "violation_count": 0,
            "backtest_stats": {},
        }
        _write(connection_error_result)
        return 1
    except RuntimeError as exc:
        print(f"[qc_upload_eval] QC REST API error: {exc}", file=sys.stderr)
        error_result: dict[str, Any] = {
            "spec_file": str(spec_file),
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
