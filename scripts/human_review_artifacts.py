#!/usr/bin/env python3
"""Human Review & Artifacts — Step 7 of the ACB Pipeline.

Reads the JSON outputs produced by Steps 5 (pre_commit_gates) and 6
(qc_upload_eval) and:

  1. Appends a formatted Markdown summary to the GitHub Step Summary
     ($GITHUB_STEP_SUMMARY).
  2. Posts an automated comment on the associated pull request (when a PR
     number can be derived from the GITHUB_REF or GITHUB_EVENT_PATH env vars).

Larger artifacts (JSON files) are uploaded by the workflow YAML step that
calls ``actions/upload-artifact``.

Exit codes:
  0 — Always (non-fatal; reporting failures are printed as warnings)
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Step Summary helpers
# ---------------------------------------------------------------------------


def _load_json(path: str) -> dict[str, Any] | None:
    """Load a JSON file, returning None on any error."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[human_review] WARNING: could not load {path}: {exc}", file=sys.stderr)
        return None


def _result_emoji(result: str) -> str:
    return "✅" if result == "PASS" else "❌"


def build_step_summary(
    spec_file: str,
    pre_commit_data: dict[str, Any] | None,
    qc_data: dict[str, Any] | None,
) -> str:
    """Build the Markdown content for the GitHub Step Summary."""
    lines: list[str] = [
        "## ACB Pipeline — Evaluation Report\n\n",
        f"**Spec file**: `{spec_file}`\n\n",
    ]

    # --- Step 5: Pre-Commit Quality Gates ---
    lines.append("### Step 5 — Pre-Commit Quality Gates\n\n")
    if pre_commit_data is None:
        lines.append("⚠️ Pre-commit gate results not available.\n\n")
    else:
        pc_result = pre_commit_data.get("result", "UNKNOWN")
        pc_errors = pre_commit_data.get("error_count", 0)
        pc_total = pre_commit_data.get("violation_count", 0)
        lines.append(
            f"**Result**: {_result_emoji(pc_result)} {pc_result}  "
            f"({pc_errors} error(s), {pc_total} total violation(s))\n\n"
        )
        violations = pre_commit_data.get("violations", [])
        if violations:
            lines.append("| Check | Severity | Message |\n")
            lines.append("|-------|----------|---------|\n")
            for v in violations:
                check = v.get("check", "?")
                sev = v.get("severity", "?")
                msg = v.get("message", "?").replace("|", "\\|")
                lines.append(f"| `{check}` | {sev} | {msg} |\n")
            lines.append("\n")
        else:
            lines.append("All quality gate checks passed.\n\n")

    # --- Step 6: QC Backtest ---
    lines.append("### Step 6 — QuantConnect Backtest\n\n")
    if qc_data is None:
        lines.append("⚠️ Backtest results not available.\n\n")
    else:
        qc_result = qc_data.get("result", "UNKNOWN")
        project_id = qc_data.get("project_id", "N/A")
        backtest_id = qc_data.get("backtest_id", "N/A")
        lines.append(
            f"**Result**: {_result_emoji(qc_result)} {qc_result}  \n"
            f"**Project ID**: `{project_id}`  \n"
            f"**Backtest ID**: `{backtest_id}`\n\n"
        )

        stats = qc_data.get("backtest_stats", {})
        if stats:
            lines.append("#### Key Backtest Statistics\n\n")
            lines.append("| Metric | Value |\n")
            lines.append("|--------|-------|\n")
            for metric, value in list(stats.items())[:20]:
                lines.append(f"| {metric} | {value} |\n")
            lines.append("\n")

        violations = qc_data.get("violations", [])
        if violations:
            lines.append("#### FitnessTracker Constraint Violations\n\n")
            lines.append("| Constraint | Required | Actual | Message |\n")
            lines.append("|------------|----------|--------|---------|\n")
            for v in violations:
                constraint = v.get("constraint", "?")
                required = v.get("required", "?")
                actual = v.get("actual", "?")
                msg = v.get("message", "?").replace("|", "\\|")
                lines.append(f"| `{constraint}` | {required} | {actual} | {msg} |\n")
            lines.append("\n")
        elif qc_result == "PASS":
            lines.append("All FitnessTracker constraints satisfied.\n\n")

    # --- Overall verdict ---
    pc_ok = pre_commit_data is None or pre_commit_data.get("result") == "PASS"
    qc_ok = qc_data is None or qc_data.get("result") == "PASS"
    overall = "PASS" if (pc_ok and qc_ok) else "FAIL"
    lines.append(f"---\n\n**Overall pipeline result**: {_result_emoji(overall)} **{overall}**\n")

    return "".join(lines)


def write_step_summary(content: str) -> None:
    """Append *content* to the GitHub Step Summary file if available."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        print("[human_review] GITHUB_STEP_SUMMARY not set; skipping step summary.", file=sys.stderr)
        return
    with open(summary_path, "a", encoding="utf-8") as fh:
        fh.write(content)
    print("[human_review] Step summary written.")


# ---------------------------------------------------------------------------
# PR comment helpers
# ---------------------------------------------------------------------------


def _get_pr_number() -> int | None:
    """Attempt to derive the pull-request number from the environment."""
    # GITHUB_REF is 'refs/pull/{number}/merge' for PR events
    ref = os.environ.get("GITHUB_REF", "")
    if ref.startswith("refs/pull/"):
        parts = ref.split("/")
        if len(parts) >= 3:
            try:
                return int(parts[2])
            except ValueError:
                pass

    # Fall back to reading the event payload
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if event_path:
        try:
            event = json.loads(Path(event_path).read_text(encoding="utf-8"))
            pr_number = (
                event.get("pull_request", {}).get("number")
                or event.get("number")
            )
            if pr_number:
                return int(pr_number)
        except (OSError, json.JSONDecodeError, ValueError):
            pass

    return None


def post_pr_comment(body: str) -> None:
    """Post *body* as a comment on the current pull request (if any)."""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    pr_number = _get_pr_number()

    if not all([token, repo, pr_number]):
        print(
            "[human_review] PR comment skipped: GITHUB_TOKEN, GITHUB_REPOSITORY or PR number "
            "not available.",
            file=sys.stderr,
        )
        return

    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    try:
        response = requests.post(
            url,
            json={"body": body},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
        )
        response.raise_for_status()
        print(f"[human_review] PR comment posted to PR #{pr_number}.")
    except requests.RequestException as exc:
        print(f"[human_review] WARNING: PR comment failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Human Review & Artifacts — Step 7 of the ACB Pipeline"
    )
    parser.add_argument("--spec", required=True, help="Path to the strategy spec YAML file")
    parser.add_argument(
        "--pre-commit-output",
        default="",
        help="Path to the pre_commit_gates JSON output (Step 5)",
    )
    parser.add_argument(
        "--qc-output",
        default="",
        help="Path to the qc_upload_eval JSON output (Step 6)",
    )
    args = parser.parse_args(argv)

    pre_commit_data = _load_json(args.pre_commit_output) if args.pre_commit_output else None
    qc_data = _load_json(args.qc_output) if args.qc_output else None

    summary_content = build_step_summary(args.spec, pre_commit_data, qc_data)
    write_step_summary(summary_content)
    post_pr_comment(summary_content)

    return 0


if __name__ == "__main__":
    sys.exit(main())
