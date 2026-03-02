#!/usr/bin/env python3
"""Ack Gate — Single acknowledgment gate for build pipeline WARNings.

Workflow:
    1. If warn_list is empty → exit 0 immediately (no human review needed).
    2. Post a summary message to SLACK_ACK_CHANNEL_ID with a unique 6-char ACK_TOKEN.
    3. Poll the Slack thread every POLL_INTERVAL_SECONDS for a reply matching
       "ACK <token>" (case-insensitive, leading/trailing whitespace tolerated).
    4. On match → write audit JSON to ACK_AUDIT_PATH (default: ack_audit.json) → exit 0.
    5. On 2-hour timeout → exit 1.
    6. Network errors during polling bubble up as exceptions (fail loudly).

Environment variables:
    SLACK_BOT_TOKEN        — Slack bot OAuth token (xoxb-…)
    SLACK_ACK_CHANNEL_ID   — Channel to post the WARN summary and await ACK
    ACK_AUDIT_PATH         — (optional) Path to write the audit JSON; default ack_audit.json
"""

import json
import os
import re
import secrets
import string
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import slack_api

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default values — override at runtime via environment variables for different
# deployment scenarios or to speed up tests.
# 2-hour window gives teams time to review warnings during working hours without
# blocking the pipeline indefinitely.
_DEFAULT_TIMEOUT_SECONDS: int = 2 * 3600
# 30-second interval keeps Slack API usage well within free-tier rate limits
# (~1 req/30s) while remaining responsive.
_DEFAULT_POLL_INTERVAL_SECONDS: int = 30

ACK_TOKEN_LENGTH: int = 6
ACK_TOKEN_ALPHABET: str = string.ascii_uppercase + string.digits

# Matches "ACK <6-char token>" allowing surrounding whitespace and case-insensitivity.
_ACK_PATTERN: re.Pattern[str] = re.compile(
    r"^\s*ACK\s+([A-Z0-9]{6})\s*$", re.IGNORECASE
)

# ---------------------------------------------------------------------------
# Token generation
# ---------------------------------------------------------------------------


def generate_ack_token() -> str:
    """Return a cryptographically-random 6-character ACK token (A-Z0-9)."""
    return "".join(secrets.choice(ACK_TOKEN_ALPHABET) for _ in range(ACK_TOKEN_LENGTH))


# ---------------------------------------------------------------------------
# Slack helpers
# ---------------------------------------------------------------------------


def _channel() -> str:
    ch = os.environ.get("SLACK_ACK_CHANNEL_ID", "")
    if not ch:
        raise EnvironmentError("SLACK_ACK_CHANNEL_ID is not set")
    return ch


def _build_summary_text(warn_list: list[str], ack_token: str) -> str:
    lines: list[str] = [
        f":warning: *Build pipeline WARN gate — {len(warn_list)} warning(s) require acknowledgment*",
        "",
    ]
    for i, w in enumerate(warn_list, start=1):
        lines.append(f"  {i}. {w}")
    lines.append("")
    lines.append(f"To approve and continue the build, reply in this thread:  `ACK {ack_token}`")
    lines.append("_This gate will expire in 2 hours._")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------


def poll_for_ack(
    channel: str, thread_ts: str, ack_token: str, deadline: float,
    poll_interval: int = _DEFAULT_POLL_INTERVAL_SECONDS,
) -> bool:
    """Poll thread replies until ACK <token> is seen or deadline is exceeded.

    Args:
        poll_interval: Seconds to wait between polls (default: 30).

    Returns True if ACK received within the deadline, False if timed out.
    Raises RuntimeError on unrecoverable Slack API errors.
    """
    expected_token = ack_token.upper()
    while time.monotonic() < deadline:
        messages = slack_api.get_replies(channel, thread_ts)
        # Skip the first message (the original summary); check all replies.
        for msg in messages[1:]:
            text = msg.get("text", "").strip()
            m = _ACK_PATTERN.match(text)
            if m and m.group(1).upper() == expected_token:
                return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_interval, remaining))
    return False


# ---------------------------------------------------------------------------
# Audit file
# ---------------------------------------------------------------------------


def write_audit(
    warn_list: list[str],
    ack_token: str,
    channel: str,
    thread_ts: str,
    acknowledged_at: str,
) -> None:
    """Write a JSON audit record to ACK_AUDIT_PATH."""
    audit_path = Path(os.environ.get("ACK_AUDIT_PATH", "ack_audit.json"))
    audit: dict[str, Any] = {
        "status": "ACKNOWLEDGED",
        "ack_token": ack_token,
        "channel": channel,
        "thread_ts": thread_ts,
        "warn_count": len(warn_list),
        "warnings": warn_list,
        "acknowledged_at": acknowledged_at,
    }
    audit_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------


def run_ack_gate(warn_list: list[str]) -> int:
    """Execute the acknowledgment gate.

    Returns:
        0 — No WARNs, or ACK received within timeout.
        1 — Timeout expired without ACK.

    Raises:
        EnvironmentError — Required env vars missing.
        RuntimeError     — Unrecoverable Slack API error.
    """
    if not warn_list:
        print("[ack_gate] No WARNs — gate passes immediately.", file=sys.stderr)
        return 0

    channel = _channel()
    ack_token = generate_ack_token()
    summary = _build_summary_text(warn_list, ack_token)

    print(
        f"[ack_gate] Posting WARN summary to channel {channel} (token: {ack_token})",
        file=sys.stderr,
    )
    response = slack_api.post_message(channel, summary)
    thread_ts: str = response["ts"]

    deadline = time.monotonic() + int(
        os.environ.get("ACK_TIMEOUT_SECONDS", str(_DEFAULT_TIMEOUT_SECONDS))
    )
    poll_interval = int(
        os.environ.get("ACK_POLL_INTERVAL_SECONDS", str(_DEFAULT_POLL_INTERVAL_SECONDS))
    )
    print(
        f"[ack_gate] Polling thread {thread_ts} for ACK {ack_token} (2-hour timeout)…",
        file=sys.stderr,
    )

    acked = poll_for_ack(channel, thread_ts, ack_token, deadline, poll_interval)

    if acked:
        acknowledged_at = datetime.now(timezone.utc).isoformat()
        write_audit(warn_list, ack_token, channel, thread_ts, acknowledged_at)
        print("[ack_gate] ACK received — audit written. Gate passed.", file=sys.stderr)
        return 0

    print(
        "[ack_gate] Timeout — no ACK received within 2 hours. Gate failed.",
        file=sys.stderr,
    )
    return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Accept WARNs as positional arguments, newline-delimited stdin, or --warns JSON file.

    Usage:
        python ack_gate.py "WARN: stop-loss > 20%" "WARN: backtesting < 2 years"
        echo -e "warn1\\nwarn2" | python ack_gate.py
        python ack_gate.py --warns /tmp/reviewer_output.json
    """
    args = argv if argv is not None else sys.argv[1:]

    if len(args) == 2 and args[0] == "--warns":
        try:
            with open(args[1], "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            print(f"[ack_gate] --warns file not found: {args[1]}", file=sys.stderr)
            return 1
        except json.JSONDecodeError as exc:
            print(f"[ack_gate] --warns file is not valid JSON: {exc}", file=sys.stderr)
            return 1
        warn_list = data.get("concerns", [])
        return run_ack_gate(warn_list)

    if args:
        warn_list = [a for a in args if a.strip()]
    else:
        stdin_text = sys.stdin.read()
        warn_list = [line.strip() for line in stdin_text.splitlines() if line.strip()]

    return run_ack_gate(warn_list)


if __name__ == "__main__":
    sys.exit(main())
