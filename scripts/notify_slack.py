#!/usr/bin/env python3
"""ACB Pipeline Slack Notification Script.

Sends structured Block Kit messages to Slack for all 10 ACB pipeline
notification gates, plus a connectivity test mode.

Usage:
    python scripts/notify_slack.py --event <event_name> [gate-specific flags]
    python scripts/notify_slack.py --test

Events (gates):
    spec_submitted      Gate 1  – spec pushed, waiting for workflow trigger
    coding_begins       Gate 2  – Aider iteration kicked off
    testing_started     Gate 3  – QC backtest submitted
    iteration_progress  Gate 4  – mid-loop progress summary
    test_results        Gate 5  – QC test suite outcomes
    model_switch        Gate 6  – model fallback triggered
    cost_alert          Gate 7  – budget threshold exceeded
    success             Gate 8  – pipeline completed successfully
    failure             Gate 9  – pipeline failed after exhausting retries
    timeout_warning     Gate 10 – approaching max elapsed time

Authentication: SLACK_BOT_TOKEN env var (xoxb-…).
Channel:        SLACK_ACK_CHANNEL_ID env var.
"""

import argparse
import os
import sys
from typing import Any

import requests

SLACK_POST_URL = "https://slack.com/api/chat.postMessage"
GITHUB_REPO_URL = "https://github.com/AmandipSidhu/infinity-lab-public"

EVENTS = [
    "spec_submitted",
    "coding_begins",
    "testing_started",
    "iteration_progress",
    "test_results",
    "model_switch",
    "cost_alert",
    "success",
    "failure",
    "timeout_warning",
]


# ---------------------------------------------------------------------------
# Low-level Slack helper
# ---------------------------------------------------------------------------


def _channel() -> str:
    ch = os.environ.get("SLACK_ACK_CHANNEL_ID", "")
    if not ch:
        raise EnvironmentError("SLACK_ACK_CHANNEL_ID is not set")
    return ch


def _token() -> str:
    tok = os.environ.get("SLACK_BOT_TOKEN", "")
    if not tok:
        raise EnvironmentError("SLACK_BOT_TOKEN is not set")
    return tok


def _post(payload: dict[str, Any]) -> None:
    """POST payload to chat.postMessage and raise on failure."""
    headers = {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json; charset=utf-8",
    }
    resp = requests.post(SLACK_POST_URL, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    if not data.get("ok"):
        raise RuntimeError(
            f"Slack API error: {data.get('error', 'unknown')} "
            f"(channel={payload.get('channel', '?')!r}, text={payload.get('text', '?')!r})"
        )


# ---------------------------------------------------------------------------
# Connectivity test
# ---------------------------------------------------------------------------


def send_test_message(text: str = "Hi") -> None:
    """Send a plain-text test message to verify Slack connectivity."""
    _post({"channel": _channel(), "text": text})
    print(f"Test message sent: {text!r}")


# ---------------------------------------------------------------------------
# Block Kit formatters — one per gate
# ---------------------------------------------------------------------------


def format_spec_submitted(args: argparse.Namespace) -> dict[str, Any]:
    """Gate 1 – 📋 Spec Submitted."""
    linear_id = args.linear_id or "N/A"
    github_issue = args.github_issue or "N/A"

    linear_text = (
        f"<https://linear.app/universaltrading/issue/{linear_id}|{linear_id}>"
        if linear_id != "N/A"
        else "N/A"
    )
    github_text = (
        f"<{GITHUB_REPO_URL}/issues/{github_issue}|#{github_issue}>"
        if github_issue not in ("N/A", "")
        else "N/A"
    )

    return {
        "channel": _channel(),
        "text": "📋 Spec Submitted",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "📋 Spec Submitted"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Strategy:*\n{args.strategy}"},
                    {"type": "mrkdwn", "text": f"*Spec:*\n`{args.spec_path}`"},
                    {"type": "mrkdwn", "text": f"*Linear:*\n{linear_text}"},
                    {"type": "mrkdwn", "text": f"*GitHub:*\n{github_text}"},
                ],
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": "Status: Waiting for workflow trigger"}
                ],
            },
        ],
    }


def format_coding_begins(args: argparse.Namespace) -> dict[str, Any]:
    """Gate 2 – 🤖 Coding Begins."""
    run_url = args.run_url or "#"
    return {
        "channel": _channel(),
        "text": "🤖 Coding Begins",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "🤖 Coding Begins"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Model:*\n`{args.model}`"},
                    {"type": "mrkdwn", "text": f"*Iteration:*\n{args.iteration}"},
                ],
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View Run"},
                        "url": run_url,
                    }
                ],
            },
        ],
    }


def format_testing_started(args: argparse.Namespace) -> dict[str, Any]:
    """Gate 3 – 🧪 Testing Started."""
    qc_url = args.qc_project_url or "#"
    return {
        "channel": _channel(),
        "text": "🧪 Testing Started",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "🧪 Testing Started"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Test #:*\n{args.test_num}"},
                    {"type": "mrkdwn", "text": f"*Test Name:*\n{args.test_name}"},
                ],
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View QC Project"},
                        "url": qc_url,
                    }
                ],
            },
        ],
    }


def format_iteration_progress(args: argparse.Namespace) -> dict[str, Any]:
    """Gate 4 – 🔄 Iteration Progress."""
    return {
        "channel": _channel(),
        "text": "🔄 Iteration Progress",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "🔄 Iteration Progress"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Max Iterations:*\n{args.max_iterations}"},
                    {"type": "mrkdwn", "text": f"*Best Result:*\n{args.best_result}"},
                    {"type": "mrkdwn", "text": f"*Current Cost:*\n{args.current_cost}"},
                    {"type": "mrkdwn", "text": f"*Status:*\n{args.status}"},
                ],
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Next action: {args.next_action}"}
                ],
            },
        ],
    }


def format_test_results(args: argparse.Namespace) -> dict[str, Any]:
    """Gate 5 – 📊 Test Results."""
    return {
        "channel": _channel(),
        "text": "📊 Test Results",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "📊 Test Results"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Tests Passed:*\n{args.tests_passed}"},
                    {"type": "mrkdwn", "text": f"*Home Tests:*\n{args.home_tests}"},
                    {"type": "mrkdwn", "text": f"*Hostile Tests:*\n{args.hostile_tests}"},
                    {"type": "mrkdwn", "text": f"*Crisis Tests:*\n{args.crisis_tests}"},
                ],
            },
        ],
    }


def format_model_switch(args: argparse.Namespace) -> dict[str, Any]:
    """Gate 6 – 🔀 Model Switch."""
    return {
        "channel": _channel(),
        "text": "🔀 Model Switch",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "🔀 Model Switch"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Reason:*\n{args.reason}"},
                    {"type": "mrkdwn", "text": f"*Old Model:*\n`{args.old_model}`"},
                    {"type": "mrkdwn", "text": f"*New Model:*\n`{args.new_model}`"},
                    {"type": "mrkdwn", "text": f"*Cost Impact:*\n{args.cost_impact}"},
                ],
            },
        ],
    }


def format_cost_alert(args: argparse.Namespace) -> dict[str, Any]:
    """Gate 7 – 💰 Cost Alert."""
    return {
        "channel": _channel(),
        "text": "💰 Cost Alert",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "💰 Cost Alert"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Budget:*\n{args.budget}"},
                    {"type": "mrkdwn", "text": f"*Overage %:*\n{args.overage_pct}"},
                    {"type": "mrkdwn", "text": f"*Action:*\n{args.action}"},
                ],
            },
        ],
    }


def format_success(args: argparse.Namespace) -> dict[str, Any]:
    """Gate 8 – ✅ Success."""
    pr_url = args.pr_url or "#"
    return {
        "channel": _channel(),
        "text": "✅ Pipeline Success",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "✅ Pipeline Success"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Version:*\n{args.version}"},
                    {"type": "mrkdwn", "text": f"*Iterations Used:*\n{args.iterations_used}"},
                    {"type": "mrkdwn", "text": f"*Final Cost:*\n{args.final_cost}"},
                    {"type": "mrkdwn", "text": f"*Output Path:*\n`{args.output_path}`"},
                ],
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View PR"},
                        "url": pr_url,
                    }
                ],
            },
        ],
    }


def format_failure(args: argparse.Namespace) -> dict[str, Any]:
    """Gate 9 – ❌ Failure."""
    log_url = args.log_url or "#"
    checkpoint_text = "Yes" if args.checkpoint_saved else "No"
    return {
        "channel": _channel(),
        "text": "❌ Pipeline Failed",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "❌ Pipeline Failed"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Best Result:*\n{args.best_result}"},
                    {"type": "mrkdwn", "text": f"*Reason:*\n{args.reason}"},
                    {"type": "mrkdwn", "text": f"*Checkpoint Saved:*\n{checkpoint_text}"},
                ],
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View Logs"},
                        "url": log_url,
                    }
                ],
            },
        ],
    }


def format_timeout_warning(args: argparse.Namespace) -> dict[str, Any]:
    """Gate 10 – ⏰ Timeout Warning."""
    return {
        "channel": _channel(),
        "text": "⏰ Timeout Warning",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "⏰ Timeout Warning"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Elapsed:*\n{args.elapsed}"},
                    {"type": "mrkdwn", "text": f"*Remaining:*\n{args.remaining}"},
                ],
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "⚠️ Pipeline approaching maximum allowed run time.",
                    }
                ],
            },
        ],
    }


_FORMATTERS = {
    "spec_submitted": format_spec_submitted,
    "coding_begins": format_coding_begins,
    "testing_started": format_testing_started,
    "iteration_progress": format_iteration_progress,
    "test_results": format_test_results,
    "model_switch": format_model_switch,
    "cost_alert": format_cost_alert,
    "success": format_success,
    "failure": format_failure,
    "timeout_warning": format_timeout_warning,
}


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send ACB pipeline Slack notifications via Block Kit."
    )

    parser.add_argument(
        "--event",
        choices=EVENTS,
        help="Notification gate event to fire.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Send a simple 'Hi' connectivity test message and exit.",
    )

    # Gate 1 – spec_submitted
    parser.add_argument("--strategy", default="", help="Strategy name (Gate 1)")
    parser.add_argument("--spec-path", dest="spec_path", default="", help="Spec file path (Gate 1)")
    parser.add_argument("--linear-id", dest="linear_id", default="N/A", help="Linear issue ID (Gate 1)")
    parser.add_argument("--github-issue", dest="github_issue", default="N/A", help="GitHub issue/PR number (Gate 1)")

    # Gate 2 – coding_begins
    parser.add_argument("--model", default="", help="Model name (Gate 2)")
    parser.add_argument("--iteration", default="", help="Current iteration (Gate 2)")
    parser.add_argument("--run-url", dest="run_url", default="", help="GitHub Actions run URL (Gate 2)")

    # Gate 3 – testing_started
    parser.add_argument("--test-num", dest="test_num", default="", help="Test number (Gate 3)")
    parser.add_argument("--test-name", dest="test_name", default="", help="Test name (Gate 3)")
    parser.add_argument("--qc-project-url", dest="qc_project_url", default="", help="QC project URL (Gate 3)")

    # Gate 4 – iteration_progress
    parser.add_argument("--max-iterations", dest="max_iterations", default="", help="Max iterations (Gate 4)")
    parser.add_argument("--best-result", dest="best_result", default="", help="Best result so far (Gate 4, Gate 9)")
    parser.add_argument("--current-cost", dest="current_cost", default="", help="Current cost (Gate 4)")
    parser.add_argument("--status", default="", help="Status description (Gate 4)")
    parser.add_argument("--next-action", dest="next_action", default="", help="Next action (Gate 4)")

    # Gate 5 – test_results
    parser.add_argument("--tests-passed", dest="tests_passed", default="", help="Pass/fail summary (Gate 5)")
    parser.add_argument("--home-tests", dest="home_tests", default="", help="Home tests result (Gate 5)")
    parser.add_argument("--hostile-tests", dest="hostile_tests", default="", help="Hostile tests result (Gate 5)")
    parser.add_argument("--crisis-tests", dest="crisis_tests", default="", help="Crisis tests result (Gate 5)")

    # Gate 6 – model_switch
    parser.add_argument("--reason", default="", help="Reason for action (Gate 6, Gate 9)")
    parser.add_argument("--old-model", dest="old_model", default="", help="Old model name (Gate 6)")
    parser.add_argument("--new-model", dest="new_model", default="", help="New model name (Gate 6)")
    parser.add_argument("--cost-impact", dest="cost_impact", default="", help="Cost impact description (Gate 6)")

    # Gate 7 – cost_alert
    parser.add_argument("--budget", default="", help="Budget limit (Gate 7)")
    parser.add_argument("--overage-pct", dest="overage_pct", default="", help="Overage percentage (Gate 7)")
    parser.add_argument("--action", default="", help="Action taken (Gate 7)")

    # Gate 8 – success
    parser.add_argument("--version", default="", help="Output version/tag (Gate 8)")
    parser.add_argument("--iterations-used", dest="iterations_used", default="", help="Iterations consumed (Gate 8)")
    parser.add_argument("--final-cost", dest="final_cost", default="", help="Total cost (Gate 8)")
    parser.add_argument("--pr-url", dest="pr_url", default="", help="Pull request URL (Gate 8)")
    parser.add_argument("--output-path", dest="output_path", default="", help="Output file path (Gate 8)")

    # Gate 9 – failure
    parser.add_argument(
        "--checkpoint-saved",
        dest="checkpoint_saved",
        action="store_true",
        help="Whether a checkpoint was saved (Gate 9)",
    )
    parser.add_argument("--log-url", dest="log_url", default="", help="Log URL (Gate 9)")

    # Gate 10 – timeout_warning
    parser.add_argument("--elapsed", default="", help="Elapsed time (Gate 10)")
    parser.add_argument("--remaining", default="", help="Remaining time (Gate 10)")

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.test:
        send_test_message("Hi")
        return 0

    if not args.event:
        parser.error("--event is required unless --test is specified")

    formatter = _FORMATTERS[args.event]
    payload = formatter(args)
    _post(payload)
    print(f"Slack notification sent: event={args.event!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
