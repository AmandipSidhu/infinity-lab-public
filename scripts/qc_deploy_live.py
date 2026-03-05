#!/usr/bin/env python3
"""QuantConnect Live Deployment — Human-Approved Strategy Upload.

This script uses the QuantConnect REST API for human-approved live deployment.
For automated CI backtesting, see ``qc_upload_eval.py``.

Uploads an approved strategy to QuantConnect via the REST API (v2), creating
a new project and optionally starting a live trading algorithm after human
review.

Authentication:
  HTTP Basic — username = QC_USER_ID
  password   = sha256(QC_USER_ID + ":" + QC_API_TOKEN + ":" + timestamp)
  Header     : Timestamp: <unix_ts>

Usage:
  python scripts/qc_deploy_live.py \\
      --strategy strategies/my_strategy.py \\
      --project-name "My Live Strategy" \\
      --start-live

Exit codes:
  0 — Upload (and optional live start) succeeded
  1 — API or network error
  2 — Invalid arguments or file not found
"""

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_QC_BASE_URL: str = "https://www.quantconnect.com/api/v2"
_REQUEST_TIMEOUT_SECONDS: int = 30


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def _qc_auth(user_id: str, api_token: str) -> tuple[dict[str, str], tuple[str, str]]:
    """Build HTTP Basic auth tuple and Timestamp header for a QC API request."""
    ts = str(int(time.time()))
    token_hash = hashlib.sha256(
        f"{user_id}:{api_token}:{ts}".encode("utf-8")
    ).hexdigest()
    headers = {"Timestamp": ts}
    return headers, (user_id, token_hash)


# ---------------------------------------------------------------------------
# REST API helpers
# ---------------------------------------------------------------------------


def _qc_post(
    endpoint: str,
    user_id: str,
    api_token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """POST to a QC REST API endpoint and return the parsed JSON body.

    Raises RuntimeError on HTTP or API-level errors.
    """
    headers, auth = _qc_auth(user_id, api_token)
    url = f"{_QC_BASE_URL}/{endpoint}"
    try:
        resp = requests.post(
            url, json=payload, headers=headers, auth=auth,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"QC API POST {endpoint} failed: {exc}") from exc
    body: dict[str, Any] = resp.json()
    if not body.get("success", True):
        errors = body.get("errors", [body.get("message", "unknown error")])
        raise RuntimeError(f"QC API returned error for {endpoint}: {errors}")
    return body


# ---------------------------------------------------------------------------
# Deployment workflow
# ---------------------------------------------------------------------------


def _create_project(user_id: str, api_token: str, project_name: str) -> int:
    """Create a new QC project and return its project_id."""
    body = _qc_post(
        "projects/create", user_id, api_token,
        {"name": project_name, "language": "Py"},
    )
    projects = body.get("projects", [])
    if not projects:
        raise RuntimeError(f"projects/create response missing 'projects': {body}")
    project_id: int = int(projects[0]["projectId"])
    print(f"[qc_deploy_live] Project created: id={project_id} name={project_name!r}")
    return project_id


def _upload_file(
    user_id: str, api_token: str, project_id: int, strategy_code: str
) -> None:
    """Upload main.py to the QC project."""
    _qc_post(
        "files/create", user_id, api_token,
        {"projectId": project_id, "name": "main.py", "content": strategy_code},
    )
    print("[qc_deploy_live] Strategy file uploaded to main.py")


def _start_live_trading(
    user_id: str, api_token: str, project_id: int
) -> dict[str, Any]:
    """Request live trading on an already-uploaded project.

    Returns the live algorithm details from the QC API response.
    Raises RuntimeError if the API call fails.
    """
    body = _qc_post(
        "live/create", user_id, api_token,
        {
            "projectId": project_id,
            # Per QC API docs, empty string for compileId triggers a fresh compile on deploy
            "compileId": "",
            "serverType": "S",     # S = shared (paper trading) instance
            "baseLiveAlgorithmSettings": {
                "id": "QuantConnectBrokerage",
                "environment": "paper",
                "user": user_id,
                "password": "",
                "account": "",
            },
            "versionId": "-1",
        },
    )
    live = body.get("liveAlgorithm") or body.get("live") or body
    live_id: str = str(
        live.get("deployId") or live.get("id") or live.get("liveId") or ""
    )
    print(f"[qc_deploy_live] Live algorithm started: id={live_id!r}")
    return {"live_id": live_id, "raw": live}


def deploy(
    strategy_file: Path,
    user_id: str,
    api_token: str,
    project_name: str,
    start_live: bool,
) -> dict[str, Any]:
    """Upload strategy to QC and optionally start live trading.

    Returns a result dict with keys:
      project_id, project_url, strategy_file, project_name, live_started,
      live_id (if live trading was started)
    """
    strategy_code = strategy_file.read_text(encoding="utf-8")

    project_id = _create_project(user_id, api_token, project_name)
    _upload_file(user_id, api_token, project_id, strategy_code)

    project_url = (
        f"https://www.quantconnect.com/terminal/#{project_id}"
    )

    result: dict[str, Any] = {
        "strategy_file": str(strategy_file),
        "project_name": project_name,
        "project_id": project_id,
        "project_url": project_url,
        "live_started": False,
        "live_id": None,
    }

    if start_live:
        live_details = _start_live_trading(user_id, api_token, project_id)
        result["live_started"] = True
        result["live_id"] = live_details.get("live_id")
        print(
            f"[qc_deploy_live] Live trading started — "
            f"project_url={project_url} live_id={result['live_id']!r}"
        )
    else:
        print(f"[qc_deploy_live] Strategy deployed — project_url={project_url}")

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "QuantConnect Live Deployment — "
            "upload an approved strategy and optionally start live trading"
        )
    )
    parser.add_argument(
        "--strategy",
        required=True,
        help="Path to the strategy .py file to upload",
    )
    parser.add_argument(
        "--project-name",
        default="",
        help=(
            "Name for the QC project (default: auto-generated from strategy filename "
            "and timestamp)"
        ),
    )
    parser.add_argument(
        "--start-live",
        action="store_true",
        default=False,
        help="Start live trading after uploading the strategy (paper trading by default)",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional path to write the JSON result (default: print to stdout)",
    )
    args = parser.parse_args(argv)

    strategy_file = Path(args.strategy)

    def _write(data: dict[str, Any]) -> None:
        out = json.dumps(data, indent=2)
        if args.output:
            Path(args.output).write_text(out, encoding="utf-8")
        else:
            print(out)

    if not strategy_file.is_file():
        _write({
            "error": f"Strategy file not found: {strategy_file}",
            "strategy_file": str(strategy_file),
        })
        return 2

    project_name: str = args.project_name.strip()
    if not project_name:
        ts = int(time.time())
        project_name = f"live-{strategy_file.stem}-{ts}"

    user_id = os.environ.get("QC_USER_ID", "").strip()
    api_token = os.environ.get("QC_API_TOKEN", "").strip()

    if not user_id or not api_token:
        _write({
            "error": "QC_USER_ID and QC_API_TOKEN environment variables must be set",
            "strategy_file": str(strategy_file),
        })
        return 2

    try:
        result = deploy(
            strategy_file=strategy_file,
            user_id=user_id,
            api_token=api_token,
            project_name=project_name,
            start_live=args.start_live,
        )
    except RuntimeError as exc:
        print(f"[qc_deploy_live] API error: {exc}", file=sys.stderr)
        _write({
            "error": str(exc),
            "strategy_file": str(strategy_file),
            "project_name": project_name,
        })
        return 1

    _write(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
