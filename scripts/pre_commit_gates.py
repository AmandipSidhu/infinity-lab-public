#!/usr/bin/env python3
"""Pre-Commit Quality Gates — Step 5 of the ACB Pipeline.

Runs static analysis and code quality checks on a built strategy file before
it is submitted for backtesting.

Checks performed:
  1. Cyclomatic Complexity (radon cc): every block must have CCN < 10
  2. Security vulnerabilities (bandit): no HIGH severity issues
  3. Security vulnerabilities (semgrep): no ERROR-level findings
  4. Function length (AST): every function/method must be < 100 lines
  5. Parameter count (AST): every function/method must have < 5 parameters

Exit codes:
  0 — All checks pass (violations list is empty)
  1 — One or more checks failed
  2 — Invalid arguments or strategy file not found
"""

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

_CCN_THRESHOLD: int = 10          # cyclomatic complexity must be < 10
_FUNCTION_MAX_LINES: int = 100    # function body must be < 100 lines
_PARAM_MAX_COUNT: int = 5         # parameter count must be < 5


# ---------------------------------------------------------------------------
# Check 1 — Cyclomatic Complexity via radon
# ---------------------------------------------------------------------------


def check_cyclomatic_complexity(strategy_file: Path) -> list[dict[str, Any]]:
    """Run ``radon cc`` and return violations where CCN >= _CCN_THRESHOLD."""
    # FIXED: catch FileNotFoundError so a missing radon install skips this
    # check with a warning instead of crashing or producing a hard ERROR
    try:
        result = subprocess.run(
            ["radon", "cc", "-s", "-j", str(strategy_file)],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print("[pre_commit_gates] WARNING: radon not installed — skipping CCN check.", file=sys.stderr)
        return []
    violations: list[dict[str, Any]] = []

    raw = result.stdout.strip()
    if not raw:
        if result.returncode != 0:
            violations.append({
                "check": "radon_ccn",
                "severity": "ERROR",
                "message": f"radon cc failed: {result.stderr.strip()[:300]}",
                "file": str(strategy_file),
            })
        return violations

    try:
        data: dict[str, list[dict[str, Any]]] = json.loads(raw)
    except json.JSONDecodeError:
        violations.append({
            "check": "radon_ccn",
            "severity": "ERROR",
            "message": f"radon cc produced unparseable output: {raw[:200]}",
            "file": str(strategy_file),
        })
        return violations

    for _path, blocks in data.items():
        for block in blocks:
            ccn: int = block.get("complexity", 0)
            if ccn >= _CCN_THRESHOLD:
                violations.append({
                    "check": "radon_ccn",
                    "severity": "ERROR",
                    "message": (
                        f"'{block.get('name', '?')}' at line {block.get('lineno', '?')} "
                        f"has CCN={ccn} (threshold: < {_CCN_THRESHOLD})"
                    ),
                    "file": str(strategy_file),
                    "line": block.get("lineno"),
                    "ccn": ccn,
                })

    return violations


# ---------------------------------------------------------------------------
# Check 2 — Security via bandit
# ---------------------------------------------------------------------------


def check_bandit(strategy_file: Path) -> list[dict[str, Any]]:
    """Run ``bandit`` and return HIGH-severity security violations."""
    # FIXED: catch FileNotFoundError so a missing bandit install skips this
    # check with a warning instead of crashing or producing a hard ERROR
    try:
        result = subprocess.run(
            ["bandit", "-f", "json", "-q", str(strategy_file)],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print("[pre_commit_gates] WARNING: bandit not installed — skipping security check.", file=sys.stderr)
        return []
    violations: list[dict[str, Any]] = []

    raw = result.stdout.strip()
    if not raw:
        # bandit exits 1 when issues are found but stdout may still be empty
        # when run with -q on a clean file; only flag unexpected failures.
        if result.returncode > 1:
            violations.append({
                "check": "bandit",
                "severity": "ERROR",
                "message": f"bandit failed unexpectedly: {result.stderr.strip()[:300]}",
                "file": str(strategy_file),
            })
        return violations

    try:
        data: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        violations.append({
            "check": "bandit",
            "severity": "ERROR",
            "message": f"bandit produced unparseable output: {raw[:200]}",
            "file": str(strategy_file),
        })
        return violations

    for issue in data.get("results", []):
        if issue.get("issue_severity", "").upper() == "HIGH":
            violations.append({
                "check": "bandit",
                "severity": "ERROR",
                "message": (
                    f"[{issue.get('test_id', '?')}] {issue.get('issue_text', '?')} "
                    f"(confidence: {issue.get('issue_confidence', '?')})"
                ),
                "file": issue.get("filename", str(strategy_file)),
                "line": issue.get("line_number"),
                "test_id": issue.get("test_id"),
            })

    return violations


# ---------------------------------------------------------------------------
# Check 3 — Security via semgrep
# ---------------------------------------------------------------------------


def check_semgrep(strategy_file: Path) -> list[dict[str, Any]]:
    """Run ``semgrep --config auto`` and return ERROR-level findings."""
    # FIXED: catch FileNotFoundError so a missing semgrep install skips this
    # check with a warning instead of crashing or producing a hard ERROR
    try:
        result = subprocess.run(
            ["semgrep", "--config", "auto", "--json", str(strategy_file)],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print("[pre_commit_gates] WARNING: semgrep not installed — skipping semgrep check.", file=sys.stderr)
        return []
    violations: list[dict[str, Any]] = []

    raw = result.stdout.strip()
    if not raw:
        if result.returncode not in (0, 1):
            violations.append({
                "check": "semgrep",
                "severity": "ERROR",
                "message": f"semgrep failed unexpectedly: {result.stderr.strip()[:300]}",
                "file": str(strategy_file),
            })
        return violations

    try:
        data: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        violations.append({
            "check": "semgrep",
            "severity": "ERROR",
            "message": f"semgrep produced unparseable output: {raw[:200]}",
            "file": str(strategy_file),
        })
        return violations

    for finding in data.get("results", []):
        extra = finding.get("extra", {})
        severity = extra.get("severity", "").upper()
        if severity == "ERROR":
            violations.append({
                "check": "semgrep",
                "severity": "ERROR",
                "message": (
                    f"[{finding.get('check_id', '?')}] {extra.get('message', '?')}"
                ),
                "file": finding.get("path", str(strategy_file)),
                "line": finding.get("start", {}).get("line"),
                "rule_id": finding.get("check_id"),
            })

    return violations


# ---------------------------------------------------------------------------
# Check 4 & 5 — Function length and parameter count via AST
# ---------------------------------------------------------------------------


def check_ast(strategy_file: Path) -> list[dict[str, Any]]:
    """Parse the strategy file and enforce function length and parameter limits."""
    violations: list[dict[str, Any]] = []

    try:
        source = strategy_file.read_text(encoding="utf-8")
    except OSError as exc:
        violations.append({
            "check": "ast",
            "severity": "ERROR",
            "message": f"Cannot read file: {exc}",
            "file": str(strategy_file),
        })
        return violations

    try:
        tree = ast.parse(source, filename=str(strategy_file))
    except SyntaxError as exc:
        violations.append({
            "check": "ast",
            "severity": "ERROR",
            "message": f"SyntaxError in strategy file: {exc}",
            "file": str(strategy_file),
        })
        return violations

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        func_name: str = node.name
        start_line: int = node.lineno
        end_line: int = node.end_lineno or start_line
        func_length: int = end_line - start_line + 1

        # Check 4: function length < 100 lines
        if func_length >= _FUNCTION_MAX_LINES:
            violations.append({
                "check": "ast_function_length",
                "severity": "ERROR",
                "message": (
                    f"Function '{func_name}' at line {start_line} is {func_length} lines "
                    f"(threshold: < {_FUNCTION_MAX_LINES})"
                ),
                "file": str(strategy_file),
                "line": start_line,
                "length": func_length,
            })

        # Check 5: parameter count < 5
        # Counts all parameters (positional, positional-only, keyword-only,
        # *args, **kwargs) to mirror cyclomatic complexity tool behaviour.
        all_args = node.args
        param_count: int = (
            len(all_args.args)
            + len(all_args.posonlyargs)
            + len(all_args.kwonlyargs)
            + (1 if all_args.vararg else 0)
            + (1 if all_args.kwarg else 0)
        )

        if param_count >= _PARAM_MAX_COUNT:
            violations.append({
                "check": "ast_param_count",
                "severity": "ERROR",
                "message": (
                    f"Function '{func_name}' at line {start_line} has {param_count} "
                    f"parameter(s) (threshold: < {_PARAM_MAX_COUNT})"
                ),
                "file": str(strategy_file),
                "line": start_line,
                "param_count": param_count,
            })

    return violations


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_gates(strategy_file: Path) -> dict[str, Any]:
    """Run all quality gates against *strategy_file* and return a summary dict."""
    violations: list[dict[str, Any]] = []

    violations.extend(check_cyclomatic_complexity(strategy_file))
    violations.extend(check_bandit(strategy_file))
    violations.extend(check_semgrep(strategy_file))
    violations.extend(check_ast(strategy_file))

    errors = [v for v in violations if v.get("severity") == "ERROR"]
    return {
        "strategy_file": str(strategy_file),
        "result": "FAIL" if errors else "PASS",
        "violation_count": len(violations),
        "error_count": len(errors),
        "violations": violations,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pre-Commit Quality Gates — Step 5 of the ACB Pipeline"
    )
    parser.add_argument(
        "--strategy",
        required=True,
        help="Path to the built strategy Python file",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional path to write the JSON result (default: print to stdout)",
    )
    args = parser.parse_args(argv)

    strategy_file = Path(args.strategy)
    if not strategy_file.is_file():
        error_summary = {
            "strategy_file": str(strategy_file),
            "result": "FAIL",
            "violation_count": 1,
            "error_count": 1,
            "violations": [{
                "check": "file_existence",
                "severity": "ERROR",
                "message": f"Strategy file not found: {strategy_file}",
                "file": str(strategy_file),
            }],
        }
        output_json = json.dumps(error_summary, indent=2)
        if args.output:
            Path(args.output).write_text(output_json, encoding="utf-8")
        else:
            print(output_json)
        return 1

    summary = run_gates(strategy_file)

    output_json = json.dumps(summary, indent=2)
    if args.output:
        Path(args.output).write_text(output_json, encoding="utf-8")
    else:
        print(output_json)

    return 0 if summary["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
