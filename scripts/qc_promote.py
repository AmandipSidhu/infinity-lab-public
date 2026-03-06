#!/usr/bin/env python3
"""QuantConnect Promotion — Step 10 of the ACB Pipeline.

Promotes an accepted strategy to a permanent named QuantConnect project.
Runs only when ``qc_upload_eval.py`` returns an acceptance-criteria PASS.

Workflow:
  1. Derive a clean CamelCase project name from the spec stem
     (e.g. ``vwap_probe`` → ``VwapProbe``), then append ``-v<N>`` where N is
     auto-incremented by checking existing QC projects with the same base name.
  2. POST ``/projects/create`` — create the permanent named project.
  3. POST ``/files/create``   — upload the accepted strategy ``.py`` file.
  4. Write ``qc_project.json`` to stdout:
     ``{"qc_project_id": "...", "qc_project_name": "...",
        "spec_stem": "...", "promoted_at": "<iso_timestamp>"}``

Authentication:
  HTTP Basic — username = QC_USER_ID
  password   = sha256(QC_USER_ID + ":" + QC_API_TOKEN + ":" + timestamp)
  Header     : Timestamp: <unix_ts>

Allowed QC API endpoints (hard constraint — no live/paper/portfolio endpoints):
  /projects/create, /files/create, /files/update, /projects/read

Usage:
  python scripts/qc_promote.py \\
      --spec-stem vwap_probe \\
      --strategy-file /tmp/strategy.py \\
      --qc-user-id $QC_USER_ID \\
      --qc-api-token $QC_API_TOKEN

Exit codes:
  0 — Promotion succeeded; qc_project.json written to stdout
  1 — QC API error or network failure
  2 — Invalid arguments or strategy file not found
"""

import argparse
import hashlib
import json
import os
import re
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
# REST API helpers (allowed endpoints only)
# ---------------------------------------------------------------------------


def _qc_post(
    endpoint: str,
    user_id: str,
    api_token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """POST to a QC REST API endpoint and return the parsed JSON body.

    Raises RuntimeError on HTTP or API-level errors.
    Only allowed endpoints may be called; others raise ValueError.
    """
    _assert_allowed_endpoint(endpoint)
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


def _qc_get(
    endpoint: str,
    user_id: str,
    api_token: str,
) -> dict[str, Any]:
    """GET a QC REST API endpoint and return the parsed JSON body.

    Raises RuntimeError on HTTP or API-level errors.
    Only allowed endpoints may be called; others raise ValueError.
    """
    _assert_allowed_endpoint(endpoint)
    headers, auth = _qc_auth(user_id, api_token)
    url = f"{_QC_BASE_URL}/{endpoint}"
    try:
        resp = requests.get(
            url, headers=headers, auth=auth,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"QC API GET {endpoint} failed: {exc}") from exc
    body: dict[str, Any] = resp.json()
    if not body.get("success", True):
        errors = body.get("errors", [body.get("message", "unknown error")])
        raise RuntimeError(f"QC API returned error for {endpoint}: {errors}")
    return body


_ALLOWED_ENDPOINTS: frozenset[str] = frozenset(
    {"projects/create", "files/create", "files/update", "projects/read"}
)


def _assert_allowed_endpoint(endpoint: str) -> None:
    """Raise ValueError if *endpoint* is not in the allowed set."""
    if endpoint not in _ALLOWED_ENDPOINTS:
        raise ValueError(
            f"Endpoint '{endpoint}' is not permitted. "
            f"Allowed: {sorted(_ALLOWED_ENDPOINTS)}"
        )


# ---------------------------------------------------------------------------
# Project name derivation
# ---------------------------------------------------------------------------


def _stem_to_base_name(spec_stem: str) -> str:
    """Convert a snake_case spec stem to a CamelCase project base name.

    Examples:
        ``vwap_probe``      → ``VwapProbe``
        ``mean_reversion``  → ``MeanReversion``
        ``my_strategy_v2``  → ``MyStrategyV2``
    """
    return "".join(word.capitalize() for word in spec_stem.split("_"))


def _get_next_version(
    base_name: str,
    user_id: str,
    api_token: str,
) -> int:
    """Query QC for existing projects and return the next version number.

    Scans all projects whose name matches ``<base_name>-v<N>`` and returns
    ``max(N) + 1``.  Returns 1 when no such projects exist.
    """
    try:
        body = _qc_get("projects/read", user_id, api_token)
    except RuntimeError as exc:
        print(
            f"[qc_promote] WARNING: Could not read existing projects ({exc}); "
            "defaulting to version 1.",
            file=sys.stderr,
        )
        return 1

    projects: list[dict[str, Any]] = body.get("projects", [])
    pattern = re.compile(
        r"^" + re.escape(base_name) + r"-v(\d+)$", re.IGNORECASE
    )
    max_version = 0
    for project in projects:
        name: str = project.get("name", "")
        match = pattern.match(name)
        if match:
            max_version = max(max_version, int(match.group(1)))

    return max_version + 1


# ---------------------------------------------------------------------------
# Promotion workflow
# ---------------------------------------------------------------------------


def promote(
    spec_stem: str,
    strategy_file: Path,
    user_id: str,
    api_token: str,
) -> dict[str, Any]:
    """Promote an accepted strategy to a permanent named QC project.

    Returns a dict with keys:
      qc_project_id, qc_project_name, spec_stem, promoted_at
    """
    base_name = _stem_to_base_name(spec_stem)
    version = _get_next_version(base_name, user_id, api_token)
    project_name = f"{base_name}-v{version}"

    print(f"[qc_promote] Creating project '{project_name}'…", file=sys.stderr)
    body = _qc_post(
        "projects/create",
        user_id,
        api_token,
        {"name": project_name, "language": "Py"},
    )
    projects = body.get("projects", [])
    if not projects:
        raise RuntimeError(
            f"projects/create response missing 'projects' key: {body}"
        )
    project_id: int = int(projects[0]["projectId"])
    print(f"[qc_promote] Project created: id={project_id}", file=sys.stderr)

    strategy_code = strategy_file.read_text(encoding="utf-8")
    print(f"[qc_promote] Uploading strategy '{strategy_file}'…", file=sys.stderr)
    _qc_post(
        "files/create",
        user_id,
        api_token,
        {
            "projectId": project_id,
            "name": "main.py",
            "content": strategy_code,
        },
    )
    print("[qc_promote] Strategy uploaded as main.py.", file=sys.stderr)

    promoted_at = datetime.now(tz=timezone.utc).isoformat()
    return {
        "qc_project_id": str(project_id),
        "qc_project_name": project_name,
        "spec_stem": spec_stem,
        "promoted_at": promoted_at,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="QuantConnect Promotion — Step 10 of the ACB Pipeline"
    )
    parser.add_argument(
        "--spec-stem",
        required=True,
        help="Spec stem (e.g. 'vwap_probe') — used to derive the project name",
    )
    parser.add_argument(
        "--strategy-file",
        required=True,
        help="Path to the accepted strategy .py file to upload",
    )
    parser.add_argument(
        "--qc-user-id",
        default="",
        help="QuantConnect user ID (falls back to QC_USER_ID env var)",
    )
    parser.add_argument(
        "--qc-api-token",
        default="",
        help="QuantConnect API token (falls back to QC_API_TOKEN env var)",
    )
    args = parser.parse_args(argv)

    strategy_file = Path(args.strategy_file)
    if not strategy_file.is_file():
        print(
            f"[qc_promote] Strategy file not found: {strategy_file}",
            file=sys.stderr,
        )
        return 2

    user_id: str = (args.qc_user_id or os.environ.get("QC_USER_ID", "")).strip()
    api_token: str = (
        args.qc_api_token or os.environ.get("QC_API_TOKEN", "")
    ).strip()

    if not user_id or not api_token:
        print(
            "[qc_promote] QC_USER_ID and QC_API_TOKEN must be provided via "
            "--qc-user-id/--qc-api-token or environment variables.",
            file=sys.stderr,
        )
        return 2

    try:
        result = promote(
            spec_stem=args.spec_stem,
            strategy_file=strategy_file,
            user_id=user_id,
            api_token=api_token,
        )
    except RuntimeError as exc:
        print(f"[qc_promote] QC API error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
