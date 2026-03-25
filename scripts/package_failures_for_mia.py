#!/usr/bin/env python3
"""Package failed grinder builds into a Mia2-friendly context bundle.

Reads grinder_results.jsonl, filters out failures, and writes a markdown
file with the original prompt, generated code, error messages, and QC logs
for each failure.

Usage:
    python scripts/package_failures_for_mia.py
    python scripts/package_failures_for_mia.py \\
        --input output/grinder_results.jsonl \\
        --strategies strategies/ \\
        --output mia_context/failed_builds.md
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


SUCCESS_STATUS = "qc_success"
SKIP_STATUS = "skipped_parent_failed"


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


def load_strategy_code(strategies_dir: Path, name: str) -> str | None:
    """Attempt to load the generated strategy source code."""
    candidate_paths = [
        strategies_dir / f"{name}.py",
        strategies_dir / name / "main.py",
    ]
    for path in candidate_paths:
        if path.exists():
            return path.read_text(encoding="utf-8")
    return None


def load_qc_result(record: dict) -> dict | None:
    """Attempt to load QC result JSON if a path can be inferred."""
    name = record.get("strategy_name", "")
    candidate_paths = [
        Path(f"output/{name}_qc_result.json"),
        Path(f"/tmp/{name}_qc_result.json"),
    ]
    for path in candidate_paths:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
    return None


def format_failure_section(
    index: int,
    record: dict,
    code: str | None,
    qc_data: dict | None,
) -> str:
    """Format a single failure into a markdown section."""
    name = record.get("strategy_name", "unknown")
    status = record.get("status", "unknown")
    priority = record.get("priority", "UNKNOWN")
    parent = record.get("parent")
    prompt = record.get("prompt", "_No prompt recorded_")
    qc_error = record.get("qc_error")
    timestamp = record.get("timestamp", "")

    lines: list[str] = []
    lines.append("---")
    lines.append("")
    lines.append(f"## Failure {index}: `{name}`")
    lines.append("")
    lines.append(f"**Status:** {status}  ")
    lines.append(f"**Priority:** {priority}  ")
    if parent:
        lines.append(f"**Parent:** {parent}  ")
    if timestamp:
        lines.append(f"**Timestamp:** {timestamp}  ")
    lines.append("")

    lines.append("### Original Prompt")
    lines.append("")
    lines.append(f"{prompt}")
    lines.append("")

    if qc_error:
        lines.append("### Error Message")
        lines.append("")
        lines.append("```")
        lines.append(qc_error)
        lines.append("```")
        lines.append("")

    # Aider/validation stage errors
    if not record.get("aider_success"):
        lines.append("### Aider Build Failed")
        lines.append("")
        lines.append(
            "Aider did not produce output. Possible causes: timeout, rate limit, API error."
        )
        lines.append("")
    elif not record.get("syntax_valid"):
        lines.append("### Syntax Validation Failed")
        lines.append("")
        lines.append(
            "The generated code failed `qc_quick_validate.py`. "
            "Check for hallucinated API patterns (self.algorithm.xxx, algorithm.portfolio.xxx)."
        )
        lines.append("")

    if code:
        lines.append("### Generated Code")
        lines.append("")
        lines.append("```python")
        lines.append(code)
        lines.append("```")
        lines.append("")
    else:
        lines.append("### Generated Code")
        lines.append("")
        lines.append("_Code file not found (strategy may not have been generated)._")
        lines.append("")

    if qc_data:
        lines.append("### QC Backtest Data")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(qc_data, indent=2))
        lines.append("```")
        lines.append("")

    lines.append("### Suggested Fix")
    lines.append("")
    if status == "syntax_error":
        lines.append(
            "1. Check the generated code for `self.algorithm.` or `algorithm.portfolio.` patterns\n"
            "2. Verify the class inherits from `QCAlgorithm`\n"
            "3. Re-run with more explicit prompt referencing `config/qc_api_reference.txt`"
        )
    elif status == "aider_failed":
        lines.append(
            "1. Break the prompt into smaller, more focused steps\n"
            "2. Check Aider rate limits / API key validity\n"
            "3. Try with a simplified strategy description"
        )
    elif status == "qc_error":
        lines.append(
            "1. Review QC error message above\n"
            "2. Check LEAN compile output for type errors or missing imports\n"
            "3. Verify indicator configuration and resolution settings"
        )
    else:
        lines.append(
            "1. Review error messages above\n"
            "2. Simplify the strategy requirements\n"
            "3. Verify QC API patterns against `config/qc_api_reference.txt`"
        )
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Package grinder failures for Mia2")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("output/grinder_results.jsonl"),
        help="Path to JSONL results file",
    )
    parser.add_argument(
        "--strategies",
        type=Path,
        default=Path("strategies"),
        help="Directory containing generated strategy files",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("mia_context/failed_builds.md"),
        help="Path to write failure bundle markdown",
    )
    args = parser.parse_args()

    records = load_results(args.input)

    failures = [
        r
        for r in records
        if r.get("status") not in (SUCCESS_STATUS, SKIP_STATUS)
    ]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    header_lines: list[str] = [
        f"# Mia2 Escalation Bundle — {now}",
        "",
        f"**Total failures:** {len(failures)}  ",
        f"**Total builds:** {len(records)}  ",
        "",
        "> These builds failed the QSC grinder and require Mia2 intervention.",
        "> Each section below contains the original prompt, generated code (if any),",
        "> error messages, and a suggested fix direction.",
        "",
    ]

    if not failures:
        header_lines.append("_No failures to report. All builds succeeded or were skipped._")
        header_lines.append("")

    sections: list[str] = []
    for i, record in enumerate(failures, 1):
        name = record.get("strategy_name", "")
        code = load_strategy_code(args.strategies, name)
        qc_data = load_qc_result(record)
        sections.append(format_failure_section(i, record, code, qc_data))

    content = "\n".join(header_lines) + "\n".join(sections)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")

    print(f"Packaged {len(failures)} failure(s) → {args.output}")


if __name__ == "__main__":
    main()
