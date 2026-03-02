#!/usr/bin/env python3
"""Human Review Notification — Step 7 of the ACB Pipeline.

Collects results from the prior pipeline steps, formats a clean markdown
summary, and posts it to the configured Slack channel for human review.

Environment variables (required for Slack posting):
    SLACK_BOT_TOKEN       — xoxb-… Slack bot token
    SLACK_ACK_CHANNEL_ID  — Channel ID to post the review notification to

Environment variables (status from pipeline steps):
    AIDER_BUILD_STATUS    — "success" or "failure"
    PRE_COMMIT_STATUS     — "success" or "failure"

Input files (optional):
    /tmp/qc_result.json   — Written by qc_upload_eval.py; omitted → dummy assumed

Exit codes:
    0 — Notification posted successfully
    1 — Slack posting failed
    2 — Invalid arguments
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Allow running as `python scripts/human_review_notify.py` from repo root.
sys.path.insert(0, str(Path(__file__).parent))

from slack_api import post_message  # noqa: E402

_QC_RESULT_PATH = Path("/tmp/qc_result.json")


# ---------------------------------------------------------------------------
# Result loading helpers
# ---------------------------------------------------------------------------


def _load_qc_result() -> dict:
    """Load QC backtest result from /tmp/qc_result.json.

    Returns a dummy passing result dict if the file is absent or malformed.
    """
    if not _QC_RESULT_PATH.is_file():
        return {
            "status": "not_run",
            "passed": True,
            "reason": "qc_result.json not found — assuming skipped",
        }
    try:
        return json.loads(_QC_RESULT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {
            "status": "parse_error",
            "passed": False,
            "reason": f"Failed to read qc_result.json: {exc}",
        }


def _status_emoji(status: str) -> str:
    """Return a Slack-friendly emoji for a step status string."""
    return "✅" if status.lower() == "success" else "❌"


# ---------------------------------------------------------------------------
# Summary formatting
# ---------------------------------------------------------------------------


def build_summary(
    spec_file: str,
    aider_status: str,
    pre_commit_status: str,
    qc_result: dict,
) -> str:
    """Return a markdown-formatted summary string for posting to Slack."""
    aider_emoji = _status_emoji(aider_status)
    pre_commit_emoji = _status_emoji(pre_commit_status)

    qc_passed = qc_result.get("passed", False)
    qc_emoji = "✅" if qc_passed else "❌"
    qc_status_label = qc_result.get("status", "unknown")

    overall_pass = (
        aider_status.lower() == "success"
        and pre_commit_status.lower() == "success"
        and qc_passed
    )
    overall_emoji = "🟢" if overall_pass else "🔴"

    lines = [
        f"*{overall_emoji} ACB Pipeline — Human Review Required*",
        "",
        f"*Spec:* `{spec_file}`",
        "",
        "| Step | Status |",
        "|------|--------|",
        f"| Aider Build | {aider_emoji} {aider_status} |",
        f"| Pre-Commit Gates | {pre_commit_emoji} {pre_commit_status} |",
        f"| QC Backtest | {qc_emoji} {qc_status_label} |",
        "",
    ]

    if qc_status_label not in ("not_run", "dummy"):
        sharpe = qc_result.get("sharpe_ratio")
        annual_return = qc_result.get("annual_return")
        total_trades = qc_result.get("total_trades")
        win_rate = qc_result.get("win_rate")
        max_drawdown = qc_result.get("max_drawdown")

        if sharpe is not None:
            lines.append("*QC Backtest Stats:*")
            lines.append(f"• Sharpe Ratio: `{sharpe:.2f}`")
        if annual_return is not None:
            lines.append(f"• Annual Return: `{annual_return:.1%}`")
        if total_trades is not None:
            lines.append(f"• Total Trades: `{total_trades}`")
        if win_rate is not None:
            lines.append(f"• Win Rate: `{win_rate:.1%}`")
        if max_drawdown is not None:
            lines.append(f"• Max Drawdown: `{max_drawdown:.1%}`")
        lines.append("")

    if not overall_pass:
        failed_steps = []
        if aider_status.lower() != "success":
            failed_steps.append("Aider Build")
        if pre_commit_status.lower() != "success":
            failed_steps.append("Pre-Commit Gates")
        if not qc_passed:
            failed_steps.append("QC Backtest")
            reason = qc_result.get("reason", "")
            if reason:
                lines.append(f"*QC failure reason:* `{reason}`")
                lines.append("")
        lines.append(f"*⚠️ Failed steps:* {', '.join(failed_steps)}")
        lines.append("")

    lines.append(
        "_Please review the pipeline output and reply `ACK <token>` if approved._"
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def notify(spec_file: str) -> int:
    """Build the review summary and post it to Slack.

    Returns 0 on success, 1 on failure.
    """
    aider_status = os.environ.get("AIDER_BUILD_STATUS", "unknown")
    pre_commit_status = os.environ.get("PRE_COMMIT_STATUS", "unknown")
    channel = os.environ.get("SLACK_ACK_CHANNEL_ID", "")

    qc_result = _load_qc_result()
    summary = build_summary(spec_file, aider_status, pre_commit_status, qc_result)

    print("[human_review_notify] Summary to post:")
    print(summary)
    print()

    if not channel:
        print(
            "[human_review_notify] ERROR: SLACK_ACK_CHANNEL_ID is not set.",
            file=sys.stderr,
        )
        return 1

    try:
        response = post_message(channel, summary)
        thread_ts = response.get("ts", "")
        print(
            f"[human_review_notify] Notification posted to channel {channel} "
            f"(ts={thread_ts})."
        )
        return 0
    except (RuntimeError, EnvironmentError) as exc:
        print(
            f"[human_review_notify] ERROR: Failed to post Slack notification: {exc}",
            file=sys.stderr,
        )
        return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Human Review Notification — Step 7 of the ACB Pipeline"
    )
    parser.add_argument(
        "--spec",
        required=True,
        help="Path to the spec YAML file that was processed",
    )
    args = parser.parse_args(argv)
    return notify(args.spec)


if __name__ == "__main__":
    sys.exit(main())
