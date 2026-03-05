#!/usr/bin/env python3
"""Slack App Setup — Creates a Slack app via the App Manifest API.

Usage:
    python scripts/setup_slack_app.py --config-token xoxe-1-...

The script reads slack_app_manifest.json from the repository root, calls the
apps.manifest.create endpoint, and prints the resulting App ID and Bot Token.

Dependency: requests (already in requirements.txt)
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests

SLACK_API_BASE = "https://slack.com/api"
MANIFEST_PATH = Path(__file__).parent.parent / "slack_app_manifest.json"
_MAX_RETRIES = 3
_INITIAL_BACKOFF = 1.0  # seconds


def _load_manifest() -> dict[str, Any]:
    """Read and parse slack_app_manifest.json from the repository root."""
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"Manifest not found at {MANIFEST_PATH}")
    with MANIFEST_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def create_slack_app(config_token: str) -> dict[str, Any]:
    """Call apps.manifest.create and return the full API response.

    Retries on HTTP 429 (rate-limited) and 5xx (server errors).

    Args:
        config_token: Slack app configuration token (xoxe-1-…).

    Returns:
        Parsed JSON response dict with 'ok', 'app_id', 'credentials', etc.

    Raises:
        RuntimeError: If the API call fails or returns ok=false.
    """
    manifest = _load_manifest()
    url = f"{SLACK_API_BASE}/apps.manifest.create"
    headers = {
        "Authorization": f"Bearer {config_token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {"manifest": json.dumps(manifest)}
    backoff = _INITIAL_BACKOFF

    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
        except requests.RequestException as exc:
            raise RuntimeError(f"Network error calling apps.manifest.create: {exc}") from exc

        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", backoff))
            time.sleep(retry_after)
            backoff *= 2
            continue

        if resp.status_code >= 500:
            if attempt < _MAX_RETRIES - 1:
                time.sleep(backoff)
                backoff *= 2
                continue
            resp.raise_for_status()

        resp.raise_for_status()
        data: dict[str, Any] = resp.json()

        if not data.get("ok"):
            error = data.get("error", "unknown")
            _handle_api_error(error)

        return data

    raise RuntimeError(f"apps.manifest.create failed after {_MAX_RETRIES} retries")


def _handle_api_error(error: str) -> None:
    """Raise a descriptive RuntimeError for known Slack API error codes."""
    messages: dict[str, str] = {
        "invalid_auth": (
            "Invalid configuration token. Ensure you are using a valid "
            "xoxe-1-… token from https://api.slack.com/apps."
        ),
        "not_authed": (
            "Missing or malformed configuration token. "
            "Provide a token via --config-token."
        ),
        "token_expired": (
            "Configuration token has expired. Generate a new one at "
            "https://api.slack.com/apps → Your App Configuration Tokens."
        ),
        "ratelimited": (
            "Rate limited by Slack API. Please wait a moment and try again."
        ),
        "invalid_manifest": (
            "The manifest JSON is invalid. Check slack_app_manifest.json "
            "against https://api.slack.com/reference/manifests."
        ),
    }
    friendly = messages.get(error, f"Slack API error: {error}")
    raise RuntimeError(friendly)


def print_success(app_id: str, bot_token: str) -> None:
    """Print success output with next steps."""
    print()
    print("✅ Slack app created!")
    print(f"App ID: {app_id}")
    print(f"Bot Token: {bot_token}")
    print()
    print("Next steps:")
    print(f"1. Update GitHub secret SLACK_BOT_TOKEN with: {bot_token}")
    print("2. Open Slack #forge_reports channel")
    print("3. Type: /invite @ACB Pipeline Bot")
    print("4. Retrigger ACB pipeline workflow")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a Slack app using the App Manifest API."
    )
    parser.add_argument(
        "--config-token",
        required=True,
        metavar="TOKEN",
        help="Slack app configuration token (xoxe-1-…)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_token: str = args.config_token

    if not config_token.strip():
        print("❌ Error: --config-token must not be empty.", file=sys.stderr)
        return 1

    print("🔧 Creating Slack app from manifest…")
    try:
        data = create_slack_app(config_token)
    except FileNotFoundError as exc:
        print(f"❌ Error: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"❌ Error: {exc}", file=sys.stderr)
        return 1

    app_id: str = data.get("app_id", "")
    credentials: dict[str, Any] = data.get("credentials", {})
    bot_token: str = credentials.get("bot_user_oauth_token", "")

    if not app_id or not bot_token:
        print(
            "❌ Error: Unexpected API response — missing app_id or bot token.",
            file=sys.stderr,
        )
        print(f"Full response: {data}", file=sys.stderr)
        return 1

    print_success(app_id, bot_token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
