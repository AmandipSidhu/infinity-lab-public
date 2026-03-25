#!/usr/bin/env python3
"""Generate a markdown summary report from grinder_results.jsonl.

Usage:
    python scripts/generate_grinder_summary.py
    python scripts/generate_grinder_summary.py \\
        --input output/grinder_results.jsonl \\
        --output output/grinder_summary.md

Reads:  output/grinder_results.jsonl
Writes: output/grinder_summary.md
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def load_results(jsonl_path: Path) -> list[dict]:
    """Load all records from the JSONL file."""
    if not jsonl_path.exists():
        return []

    records = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def status_emoji(status: str) -> str:
    """Return an emoji for a given status string."""
    mapping = {
        "qc_success": "✅",
        "qc_error": "⚠️",
        "syntax_error": "❌",
        "aider_failed": "💥",
        "skipped_parent_failed": "⏭️",
        "qc_not_submitted": "🔵",
    }
    return mapping.get(status, "❓")


def fmt_float(value: float | None, decimals: int = 3) -> str:
    """Format a float or return '-' if None."""
    if value is None:
        return "-"
    return f"{value:.{decimals}f}"


def fmt_int(value: float | None) -> str:
    """Format as integer or return '-' if None."""
    if value is None:
        return "-"
    return str(int(value))


def generate_summary(records: list[dict]) -> str:
    """Generate markdown summary from records."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    total = len(records)
    aider_ok = sum(1 for r in records if r.get("aider_success"))
    syntax_ok = sum(1 for r in records if r.get("syntax_valid"))
    qc_ok = sum(1 for r in records if r.get("status") == "qc_success")
    qc_submitted = sum(1 for r in records if r.get("qc_submitted"))
    skipped = sum(1 for r in records if r.get("status") == "skipped_parent_failed")

    success_rate = (qc_ok / total * 100) if total > 0 else 0.0

    lines: list[str] = []
    lines.append(f"# QSC Grinder Summary — {now}")
    lines.append("")

    # Overview
    lines.append("## Overview")
    lines.append("")
    if total == 0:
        lines.append("_No builds recorded yet._")
        lines.append("")
    else:
        lines.append(f"- **{total}** prompts attempted")
        lines.append(f"- ✅ **{aider_ok}/{total}** Aider builds succeeded")
        lines.append(f"- ✅ **{syntax_ok}/{total}** passed syntax validation")
        lines.append(f"- ✅ **{qc_submitted}/{total}** submitted to QuantConnect")
        lines.append(f"- ✅ **{qc_ok}/{total}** ran successfully on QuantConnect")
        if skipped:
            lines.append(f"- ⏭️ **{skipped}** skipped (parent build failed)")
        lines.append(f"- **Success rate: {success_rate:.1f}%**")
        lines.append("")

    # Priority builds table
    priority_records = [r for r in records if r.get("priority") == "PRIORITY"]
    if priority_records:
        lines.append(f"## Priority Builds ({len(priority_records)} attempted)")
        lines.append("")
        lines.append("| Strategy | Status | QC Sharpe | Orders | Net PnL% |")
        lines.append("|----------|--------|-----------|--------|----------|")
        for r in priority_records:
            emoji = status_emoji(r.get("status", ""))
            name = r.get("strategy_name", "-")
            status = r.get("status", "-")
            sharpe = fmt_float(r.get("qc_sharpe"))
            orders = fmt_int(r.get("qc_total_orders"))
            pnl = fmt_float(r.get("qc_net_pnl_pct"), 1)
            lines.append(f"| {name} | {emoji} {status} | {sharpe} | {orders} | {pnl} |")
        lines.append("")

    # Independent builds table
    independent_records = [r for r in records if r.get("priority") == "INDEPENDENT"]
    if independent_records:
        lines.append(f"## Independent Builds ({len(independent_records)} attempted)")
        lines.append("")
        lines.append("| Strategy | Status | QC Sharpe | Orders | Net PnL% |")
        lines.append("|----------|--------|-----------|--------|----------|")
        for r in independent_records:
            emoji = status_emoji(r.get("status", ""))
            name = r.get("strategy_name", "-")
            status = r.get("status", "-")
            sharpe = fmt_float(r.get("qc_sharpe"))
            orders = fmt_int(r.get("qc_total_orders"))
            pnl = fmt_float(r.get("qc_net_pnl_pct"), 1)
            lines.append(f"| {name} | {emoji} {status} | {sharpe} | {orders} | {pnl} |")
        lines.append("")

    # Conditional builds table
    conditional_records = [r for r in records if r.get("priority") == "IF-PREVIOUS-PASSED"]
    if conditional_records:
        passed = sum(1 for r in conditional_records if r.get("status") == "qc_success")
        lines.append(
            f"## Conditional Builds ({len(conditional_records)} attempted, {passed} succeeded)"
        )
        lines.append("")
        lines.append("| Strategy | Parent | Status |")
        lines.append("|----------|--------|--------|")
        for r in conditional_records:
            emoji = status_emoji(r.get("status", ""))
            name = r.get("strategy_name", "-")
            parent = r.get("parent") or "-"
            status = r.get("status", "-")
            lines.append(f"| {name} | {parent} | {emoji} {status} |")
        lines.append("")

    # Low-priority builds table
    low_records = [r for r in records if r.get("priority") == "LOW-PRIORITY"]
    if low_records:
        lines.append(f"## Low-Priority Builds ({len(low_records)} attempted)")
        lines.append("")
        lines.append("| Strategy | Status | QC Sharpe |")
        lines.append("|----------|--------|-----------|")
        for r in low_records:
            emoji = status_emoji(r.get("status", ""))
            name = r.get("strategy_name", "-")
            status = r.get("status", "-")
            sharpe = fmt_float(r.get("qc_sharpe"))
            lines.append(f"| {name} | {emoji} {status} | {sharpe} |")
        lines.append("")

    # Top performers by Sharpe
    performers = [
        r
        for r in records
        if r.get("status") == "qc_success" and r.get("qc_sharpe") is not None
    ]
    performers.sort(key=lambda r: r.get("qc_sharpe") or 0, reverse=True)
    if performers:
        lines.append("## Top Performers (by Sharpe Ratio)")
        lines.append("")
        lines.append("| Strategy | Priority | Sharpe | Orders | Net PnL% |")
        lines.append("|----------|----------|--------|--------|----------|")
        for r in performers[:10]:
            name = r.get("strategy_name", "-")
            priority = r.get("priority", "-")
            sharpe = fmt_float(r.get("qc_sharpe"))
            orders = fmt_int(r.get("qc_total_orders"))
            pnl = fmt_float(r.get("qc_net_pnl_pct"), 1)
            lines.append(f"| {name} | {priority} | {sharpe} | {orders} | {pnl} |")
        lines.append("")

    # Failures for Mia2 escalation
    failures = [
        r
        for r in records
        if r.get("status") not in ("qc_success", "skipped_parent_failed")
    ]
    if failures:
        lines.append(f"## Failures for Mia2 Escalation ({len(failures)} total)")
        lines.append("")
        for i, r in enumerate(failures, 1):
            name = r.get("strategy_name", "-")
            status = r.get("status", "-")
            err = r.get("qc_error") or ""
            lines.append(f"{i}. **{name}** — {status}{(': ' + err) if err else ''}")
        lines.append("")
        lines.append(
            "_Run `python scripts/package_failures_for_mia.py` to bundle these for Mia2._"
        )
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate grinder summary markdown report")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("output/grinder_results.jsonl"),
        help="Path to JSONL results file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/grinder_summary.md"),
        help="Path to write markdown summary",
    )
    args = parser.parse_args()

    records = load_results(args.input)

    summary = generate_summary(records)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(summary, encoding="utf-8")

    print(f"Summary written to {args.output} ({len(records)} records)")
    if records:
        qc_ok = sum(1 for r in records if r.get("status") == "qc_success")
        print(f"Success rate: {qc_ok}/{len(records)} = {qc_ok/len(records)*100:.1f}%")


if __name__ == "__main__":
    main()
