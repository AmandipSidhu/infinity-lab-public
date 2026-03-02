#!/usr/bin/env python3
"""Slack Web API — Lightweight stateless wrapper.

Exposes two thin functions:
    post_message(channel, text, *, thread_ts=None) -> dict
    get_replies(channel, thread_ts) -> list[dict]

Authentication: SLACK_BOT_TOKEN env var (xoxb-…).

Retry policy:
    HTTP 429 (rate-limited): honour Retry-After header, back-off up to 3 attempts.
    HTTP 5xx (server errors): exponential back-off, up to 3 attempts.
    All other errors: raise immediately.
"""

import os
import time
from typing import Any

import requests

SLACK_API_BASE = "https://slack.com/api"
_MAX_RETRIES = 3
_INITIAL_BACKOFF = 1.0  # seconds


def _token() -> str:
    tok = os.environ.get("SLACK_BOT_TOKEN", "")
    if not tok:
        raise EnvironmentError("SLACK_BOT_TOKEN is not set")
    return tok


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json; charset=utf-8",
    }


def _call(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST to Slack API with retry logic for 429/5xx responses."""
    url = f"{SLACK_API_BASE}/{method}"
    backoff = _INITIAL_BACKOFF

    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.post(url, json=payload, headers=_headers(), timeout=30)
        except requests.RequestException as exc:
            raise RuntimeError(f"Network error calling Slack {method}: {exc}") from exc

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
            raise RuntimeError(f"Slack API error in {method}: {data.get('error', 'unknown')}")
        return data

    raise RuntimeError(f"Slack {method} failed after {_MAX_RETRIES} retries")


def post_message(
    channel: str, text: str, *, thread_ts: str | None = None
) -> dict[str, Any]:
    """Post a message to a Slack channel, optionally in a thread.

    Returns the full Slack API response dict (includes 'ts' for the new message).
    """
    payload: dict[str, Any] = {"channel": channel, "text": text}
    if thread_ts is not None:
        payload["thread_ts"] = thread_ts
    return _call("chat.postMessage", payload)


def get_replies(channel: str, thread_ts: str) -> list[dict[str, Any]]:
    """Fetch all messages in a Slack thread (original message + replies).

    Returns the list of message dicts from the 'messages' key.
    """
    payload: dict[str, Any] = {"channel": channel, "ts": thread_ts}
    data = _call("conversations.replies", payload)
    return data.get("messages", [])
