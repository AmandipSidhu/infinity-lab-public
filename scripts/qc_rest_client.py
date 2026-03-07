#!/usr/bin/env python3
"""QuantConnect REST API Client — Phase 1 of the ACB E2E Rebuild.

Uploads ``strategies/reference/sma_crossover_simple.py`` to QuantConnect via
the REST API, compiles it, runs a backtest, and returns the Sharpe ratio.

Authentication:
  HTTP Basic Auth + SHA-256 HMAC timestamp.
  Secrets: ``QC_USER_ID``, ``QC_API_TOKEN`` (read from environment).

QC API Endpoints used:
  POST /projects/create      — create throwaway project
  POST /files/create         — upload strategy source
  POST /compile/create       — trigger compilation
  GET  /compile/read         — poll compile status
  POST /backtests/create     — trigger backtest
  GET  /backtests/read       — poll backtest status

Retry policy:
  HTTP 5xx errors are retried up to 3 times with 30-second exponential
  backoff (30s, 60s, 90s).  Auth failures (401/403) fail immediately.

Timeout:
  10 minutes total for compile polling + backtest polling combined.

Output:
  Writes ``/tmp/backtest_result.json`` on success with the schema:
  {
    "project_id": str,
    "backtest_id": str,
    "sharpe_ratio": float,
    "total_return_pct": float | None,
    "max_drawdown_pct": float | None,
    "total_trades": int | None,
    "compile_state": str,
    "backtest_status": str,
    "qc_ui_url": str,
    "timestamp": str   # ISO-8601 UTC
  }

Exit codes:
  0 — Backtest completed and result written
  1 — API / auth / timeout / parse error
  2 — Invalid arguments or file not found
"""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_QC_BASE_URL: str = "https://www.quantconnect.com/api/v2"
_REQUEST_TIMEOUT_SECONDS: int = 30
_POLL_INTERVAL_SECONDS: int = 10
_POLL_TIMEOUT_SECONDS: int = 600          # 10 minutes total
_MAX_RETRIES: int = 3
_RETRY_BACKOFF_BASE_SECONDS: int = 30     # 30s, 60s, 90s

# Compile states that indicate a successful build
_COMPILE_SUCCESS_STATES: frozenset[str] = frozenset(
    {"BuildSuccess", "Success", "success", "build_success"}
)
# Compile states that indicate a permanent build failure
_COMPILE_FAILURE_STATES: frozenset[str] = frozenset(
    {"BuildError", "Error", "error", "build_error"}
)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class QCAuthError(RuntimeError):
    """Raised on HTTP 401/403 — authentication failure."""


class QCAPIError(RuntimeError):
    """Raised on HTTP 5xx or API-level error responses."""


class QCTimeoutError(RuntimeError):
    """Raised when compile/backtest polling exceeds the timeout limit."""


class QCCompileError(RuntimeError):
    """Raised when the strategy fails to compile on QuantConnect."""


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def _qc_auth(user_id: str, api_token: str) -> tuple[dict[str, str], tuple[str, str]]:
    """Return (headers, basic-auth tuple) for a QC REST API request.

    QC expects:
      - Authorization: Basic base64(userId:sha256(userId:apiToken:timestamp))
      - Timestamp: <unix seconds>
    """
    ts = str(int(time.time()))
    token_hash = hashlib.sha256(
        f"{api_token}:{ts}".encode("utf-8")
    ).hexdigest()
    headers = {"Timestamp": ts}
    return headers, (user_id, token_hash)


# ---------------------------------------------------------------------------
# Low-level HTTP helpers with retry
# ---------------------------------------------------------------------------


def _http_post(
    endpoint: str,
    user_id: str,
    api_token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """POST ``payload`` to ``endpoint`` and return parsed JSON.

    Retries up to ``_MAX_RETRIES`` times on HTTP 5xx with exponential backoff.
    Fails immediately on 401/403.
    """
    url = f"{_QC_BASE_URL}/{endpoint}"
    last_exc: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        headers, auth = _qc_auth(user_id, api_token)
        try:
            resp = requests.post(
                url, json=payload, headers=headers, auth=auth,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            last_exc = exc
            print(
                f"[qc_rest_client] POST {endpoint} network error "
                f"(attempt {attempt}/{_MAX_RETRIES}): {exc}",
                file=sys.stderr,
            )
            if attempt < _MAX_RETRIES:
                backoff = _RETRY_BACKOFF_BASE_SECONDS * attempt
                print(
                    f"[qc_rest_client] Retrying in {backoff}s…",
                    file=sys.stderr,
                )
                time.sleep(backoff)
            continue

        if resp.status_code in (401, 403):
            raise QCAuthError(
                f"QC API authentication failed for POST {endpoint} "
                f"(HTTP {resp.status_code}): {resp.text}"
            )

        if resp.status_code >= 500:
            print(
                f"[qc_rest_client] POST {endpoint} server error "
                f"HTTP {resp.status_code} (attempt {attempt}/{_MAX_RETRIES})",
                file=sys.stderr,
            )
            last_exc = QCAPIError(f"HTTP {resp.status_code}: {resp.text}")
            if attempt < _MAX_RETRIES:
                backoff = _RETRY_BACKOFF_BASE_SECONDS * attempt
                print(
                    f"[qc_rest_client] Retrying in {backoff}s…",
                    file=sys.stderr,
                )
                time.sleep(backoff)
            continue

        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise QCAPIError(
                f"QC API HTTP error for POST {endpoint}: {exc}"
            ) from exc

        body: dict[str, Any] = resp.json()
        print(
            f"[qc_rest_client] POST {endpoint} → "
            f"success={body.get('success')}",
        )
        if not body.get("success", True):
            errors = body.get("errors", [body.get("message", "unknown error")])
            raise QCAPIError(
                f"QC API returned error for POST {endpoint}: {errors}"
            )
        return body

    raise QCAPIError(
        f"POST {endpoint} failed after {_MAX_RETRIES} attempts"
    ) from last_exc


def _http_get(
    endpoint: str,
    user_id: str,
    api_token: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """GET ``endpoint`` and return parsed JSON.

    Retries up to ``_MAX_RETRIES`` times on HTTP 5xx with exponential backoff.
    Fails immediately on 401/403.
    """
    url = f"{_QC_BASE_URL}/{endpoint}"
    last_exc: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        headers, auth = _qc_auth(user_id, api_token)
        try:
            resp = requests.get(
                url, params=params, headers=headers, auth=auth,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            last_exc = exc
            print(
                f"[qc_rest_client] GET {endpoint} network error "
                f"(attempt {attempt}/{_MAX_RETRIES}): {exc}",
                file=sys.stderr,
            )
            if attempt < _MAX_RETRIES:
                backoff = _RETRY_BACKOFF_BASE_SECONDS * attempt
                print(
                    f"[qc_rest_client] Retrying in {backoff}s…",
                    file=sys.stderr,
                )
                time.sleep(backoff)
            continue

        if resp.status_code in (401, 403):
            raise QCAuthError(
                f"QC API authentication failed for GET {endpoint} "
                f"(HTTP {resp.status_code}): {resp.text}"
            )

        if resp.status_code >= 500:
            print(
                f"[qc_rest_client] GET {endpoint} server error "
                f"HTTP {resp.status_code} (attempt {attempt}/{_MAX_RETRIES})",
                file=sys.stderr,
            )
            last_exc = QCAPIError(f"HTTP {resp.status_code}: {resp.text}")
            if attempt < _MAX_RETRIES:
                backoff = _RETRY_BACKOFF_BASE_SECONDS * attempt
                print(
                    f"[qc_rest_client] Retrying in {backoff}s…",
                    file=sys.stderr,
                )
                time.sleep(backoff)
            continue

        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise QCAPIError(
                f"QC API HTTP error for GET {endpoint}: {exc}"
            ) from exc

        body: dict[str, Any] = resp.json()
        if not body.get("success", True):
            errors = body.get("errors", [body.get("message", "unknown error")])
            raise QCAPIError(
                f"QC API returned error for GET {endpoint}: {errors}"
            )
        return body

    raise QCAPIError(
        f"GET {endpoint} failed after {_MAX_RETRIES} attempts"
    ) from last_exc


# ---------------------------------------------------------------------------
# QC workflow steps
# ---------------------------------------------------------------------------


def create_project(user_id: str, api_token: str, project_name: str) -> int:
    """Create a new QuantConnect project and return its project_id."""
    print(f"[qc_rest_client] Creating project '{project_name}'…")
    body = _http_post(
        "projects/create",
        user_id,
        api_token,
        {"name": project_name, "language": "Py"},
    )
    projects = body.get("projects", [])
    if not projects:
        raise QCAPIError(
            f"projects/create response missing 'projects' list: {body}"
        )
    project_id = projects[0].get("projectId")
    if not project_id:
        raise QCAPIError(
            f"projects/create did not return a projectId: {body}"
        )
    project_id_int = int(project_id)
    print(f"[qc_rest_client] Project created: project_id={project_id_int}")
    return project_id_int


def upload_file(
    user_id: str,
    api_token: str,
    project_id: int,
    filename: str,
    content: str,
) -> None:
    """Upload ``content`` as ``filename`` into ``project_id``."""
    print(f"[qc_rest_client] Uploading file '{filename}' to project {project_id}…")
    _http_post(
        "files/create",
        user_id,
        api_token,
        {"projectId": project_id, "name": filename, "content": content},
    )
    print(f"[qc_rest_client] File '{filename}' uploaded.")


def compile_project(
    user_id: str,
    api_token: str,
    project_id: int,
) -> str:
    """Trigger compilation and return the compile_id."""
    print(f"[qc_rest_client] Triggering compilation for project {project_id}…")
    body = _http_post(
        "compile/create",
        user_id,
        api_token,
        {"projectId": project_id},
    )
    compile_id = (
        body.get("compileId")
        or body.get("compile", {}).get("compileId")
    )
    if not compile_id:
        raise QCAPIError(
            f"compile/create did not return a compileId: {body}"
        )
    print(f"[qc_rest_client] Compilation triggered: compile_id={compile_id}")
    return str(compile_id)


def poll_compile(
    user_id: str,
    api_token: str,
    project_id: int,
    compile_id: str,
    deadline: float,
) -> str:
    """Poll compile/read until success/failure or deadline.

    Returns the final compile state string (e.g. ``"BuildSuccess"``).
    Raises ``QCCompileError`` if compilation failed.
    Raises ``QCTimeoutError`` if deadline is exceeded.
    """
    print(f"[qc_rest_client] Polling compile status for compile_id={compile_id}…")
    while True:
        if time.time() > deadline:
            raise QCTimeoutError(
                f"Compile polling timed out after {_POLL_TIMEOUT_SECONDS}s "
                f"for compile_id={compile_id}"
            )

        body = _http_get(
            "compile/read",
            user_id,
            api_token,
            params={"projectId": project_id, "compileId": compile_id},
        )

        # QC returns the compile object nested or at the top level
        compile_obj: dict[str, Any] = body.get("compile", body)
        state: str = str(
            compile_obj.get("state")
            or compile_obj.get("State")
            or compile_obj.get("compileState")
            or compile_obj.get("CompileState")
            or ""
        )

        print(f"[qc_rest_client] Compile state: {state!r}")

        if state in _COMPILE_SUCCESS_STATES:
            return state
        if state in _COMPILE_FAILURE_STATES:
            logs = compile_obj.get("logs", compile_obj.get("Logs", []))
            raise QCCompileError(
                f"Strategy compilation failed (state={state!r}). Logs: {logs}"
            )

        remaining = max(0.0, deadline - time.time())
        print(
            f"[qc_rest_client] Waiting {_POLL_INTERVAL_SECONDS}s "
            f"(~{remaining:.0f}s remaining)…"
        )
        time.sleep(_POLL_INTERVAL_SECONDS)


def create_backtest(
    user_id: str,
    api_token: str,
    project_id: int,
    compile_id: str,
    backtest_name: str,
) -> str:
    """Trigger a backtest and return the backtest_id."""
    print(
        f"[qc_rest_client] Creating backtest '{backtest_name}' "
        f"for project {project_id}…"
    )
    body = _http_post(
        "backtests/create",
        user_id,
        api_token,
        {
            "projectId": project_id,
            "compileId": compile_id,
            "backtestName": backtest_name,
        },
    )
    backtest = body.get("backtest", {})
    backtest_id = backtest.get("backtestId") or body.get("backtestId")
    if not backtest_id:
        raise QCAPIError(
            f"backtests/create did not return a backtestId: {body}"
        )
    print(f"[qc_rest_client] Backtest started: backtest_id={backtest_id}")
    return str(backtest_id)


def poll_backtest(
    user_id: str,
    api_token: str,
    project_id: int,
    backtest_id: str,
    deadline: float,
) -> dict[str, Any]:
    """Poll backtests/read until completion or deadline.

    Returns the final backtest result dict.
    Raises ``QCTimeoutError`` if deadline is exceeded.
    """
    print(
        f"[qc_rest_client] Polling backtest completion for backtest_id={backtest_id}…"
    )
    while True:
        if time.time() > deadline:
            raise QCTimeoutError(
                f"Backtest polling timed out after {_POLL_TIMEOUT_SECONDS}s "
                f"for backtest_id={backtest_id}"
            )

        body = _http_get(
            "backtests/read",
            user_id,
            api_token,
            params={"projectId": project_id, "backtestId": backtest_id},
        )

        result: dict[str, Any] = body.get("backtest", body)
        progress: float = float(
            result.get("progress", result.get("Progress", 0.0))
        )
        completed: bool = bool(
            result.get("completed", result.get("Completed", False))
        )

        print(
            f"[qc_rest_client] Backtest progress: {progress * 100:.1f}% "
            f"(completed={completed})"
        )

        if completed or progress >= 1.0:
            return result

        remaining = max(0.0, deadline - time.time())
        print(
            f"[qc_rest_client] Waiting {_POLL_INTERVAL_SECONDS}s "
            f"(~{remaining:.0f}s remaining)…"
        )
        time.sleep(_POLL_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Statistics extraction
# ---------------------------------------------------------------------------


def _extract_stat(result: dict[str, Any], *keys: str) -> float | None:
    """Extract a numeric stat from backtest result, searching top-level and sub-dicts."""
    search_targets: list[dict[str, Any]] = [result]
    for sub_key in ("statistics", "Statistics", "runtimeStatistics", "RuntimeStatistics"):
        sub = result.get(sub_key)
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


def _extract_int_stat(result: dict[str, Any], *keys: str) -> int | None:
    """Extract an integer stat from backtest result."""
    val = _extract_stat(result, *keys)
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def run_backtest(
    strategy_file: Path,
    user_id: str,
    api_token: str,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Run the full QC pipeline: create → upload → compile → backtest.

    Returns the result dict (same schema written to ``output_path``).
    Raises ``QCAuthError``, ``QCAPIError``, ``QCTimeoutError``, or
    ``QCCompileError`` on failure.
    """
    if not strategy_file.is_file():
        raise FileNotFoundError(f"Strategy file not found: {strategy_file}")

    strategy_code = strategy_file.read_text(encoding="utf-8")
    unix_ts = int(time.time())
    project_name = f"test-acb-{unix_ts}"
    backtest_name = f"{project_name}_backtest"
    deadline = time.time() + _POLL_TIMEOUT_SECONDS

    # 1. Create project
    project_id = create_project(user_id, api_token, project_name)

    # 2. Upload strategy file (use the source filename)
    upload_file(
        user_id, api_token, project_id,
        filename=strategy_file.name,
        content=strategy_code,
    )

    # 3. Compile
    compile_id = compile_project(user_id, api_token, project_id)

    # 4. Poll compile status
    compile_state = poll_compile(
        user_id, api_token, project_id, compile_id, deadline
    )
    print(f"[qc_rest_client] Compilation succeeded: state={compile_state!r}")

    # 5. Create backtest
    backtest_id = create_backtest(
        user_id, api_token, project_id, compile_id, backtest_name
    )

    # 6. Poll backtest
    backtest_result = poll_backtest(
        user_id, api_token, project_id, backtest_id, deadline
    )
    print("[qc_rest_client] Backtest completed.")

    # 7. Extract statistics
    sharpe_ratio = _extract_stat(
        backtest_result,
        "SharpeRatio", "sharpe_ratio", "Sharpe Ratio", "sharpe",
    )
    total_return_pct = _extract_stat(
        backtest_result,
        "TotalNetProfit", "total_net_profit", "Net Profit", "TotalReturn",
        "Total Net Profit",
    )
    max_drawdown_pct = _extract_stat(
        backtest_result,
        "Drawdown", "MaxDrawdown", "max_drawdown", "Max Drawdown", "drawdown",
    )
    total_trades = _extract_int_stat(
        backtest_result,
        "TotalNumberOfTrades", "total_trades", "Total Trades",
        "NumberOfTrades", "Trades",
    )

    backtest_status = "Completed" if backtest_result.get(
        "completed", backtest_result.get("Completed", False)
    ) else "Unknown"

    result: dict[str, Any] = {
        "project_id": str(project_id),
        "backtest_id": str(backtest_id),
        "sharpe_ratio": sharpe_ratio,
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "total_trades": total_trades,
        "compile_state": compile_state,
        "backtest_status": backtest_status,
        "qc_ui_url": f"https://www.quantconnect.com/project/{project_id}",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    if output_path is not None:
        output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"[qc_rest_client] Result written to {output_path}")

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="QC REST Client — upload, compile, backtest a strategy"
    )
    parser.add_argument(
        "--strategy",
        default=str(
            Path(__file__).parent.parent
            / "strategies" / "reference" / "sma_crossover_simple.py"
        ),
        help=(
            "Path to the strategy .py file "
            "(default: strategies/reference/sma_crossover_simple.py)"
        ),
    )
    parser.add_argument(
        "--output",
        default="/tmp/backtest_result.json",
        help="Path to write the JSON result (default: /tmp/backtest_result.json)",
    )
    parser.add_argument(
        "--qc-user-id",
        default=os.environ.get("QC_USER_ID", "").strip(),
        help="QuantConnect user ID (default: $QC_USER_ID)",
    )
    parser.add_argument(
        "--qc-api-token",
        default=os.environ.get("QC_API_TOKEN", "").strip(),
        help="QuantConnect API token (default: $QC_API_TOKEN)",
    )
    args = parser.parse_args(argv)

    strategy_file = Path(args.strategy)
    output_path = Path(args.output)
    user_id: str = args.qc_user_id
    api_token: str = args.qc_api_token

    if not strategy_file.is_file():
        print(
            f"[qc_rest_client] ERROR: Strategy file not found: {strategy_file}",
            file=sys.stderr,
        )
        return 2

    if not user_id or not api_token:
        print(
            "[qc_rest_client] ERROR: QC_USER_ID and QC_API_TOKEN must be set.",
            file=sys.stderr,
        )
        return 1

    try:
        result = run_backtest(strategy_file, user_id, api_token, output_path)
    except QCAuthError as exc:
        print(f"[qc_rest_client] AUTH ERROR: {exc}", file=sys.stderr)
        return 1
    except QCCompileError as exc:
        print(f"[qc_rest_client] COMPILE ERROR: {exc}", file=sys.stderr)
        return 1
    except QCTimeoutError as exc:
        print(f"[qc_rest_client] TIMEOUT ERROR: {exc}", file=sys.stderr)
        return 1
    except (QCAPIError, RuntimeError) as exc:
        print(f"[qc_rest_client] API ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        f"[qc_rest_client] Done. "
        f"Sharpe Ratio={result['sharpe_ratio']}, "
        f"Project URL={result['qc_ui_url']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
