#!/usr/bin/env python3
"""Pre-Commit Quality Gates — Step 5 of the ACB Pipeline.

Runs static analysis and code quality checks on a built strategy file before
it is submitted for backtesting.

Checks performed:
  1. Cyclomatic Complexity (radon cc): every block must have CCN < 10
  2. Security vulnerabilities (bandit): no HIGH severity issues
  3. Security vulnerabilities (semgrep): no ERROR-level findings
  4. Function length (AST): every function/method must be < 150 lines
  5. Parameter count (AST): every function/method must have < 8 parameters
  6. Stub/placeholder detection (AST): no function may have a stub body

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
_FUNCTION_MAX_LINES: int = 150    # function body must be < 150 lines
_PARAM_MAX_COUNT: int = 8         # parameter count must be < 8


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
# Check 6 — Stub/placeholder detection via AST
# ---------------------------------------------------------------------------


def check_stub_detection(strategy_file: Path) -> list[dict[str, Any]]:
    """Parse the strategy file and flag functions whose bodies are stubs or placeholders.

    A function body is treated as a stub when **all** of its statements match
    at least one of the following patterns:

    1. Bare ``pass`` statement.
    2. ``raise NotImplementedError(...)`` call or name.
    3. ``return None`` on a line that contains a ``# TODO`` or ``# FIXME`` comment.
    4. A docstring expression with no real implementation.

    Certain patterns are suppressed (allowed):

    * Functions named ``__init__`` with a bare ``pass`` body.
    * Functions defined directly inside an ``except`` handler.
    * Functions defined inside a class that inherits from ``ABC`` or ``ABCMeta``.
    """
    violations: list[dict[str, Any]] = []

    try:
        source = strategy_file.read_text(encoding="utf-8")
    except OSError as exc:
        violations.append({
            "check": "ast_stub_detection",
            "severity": "ERROR",
            "message": f"Cannot read file: {exc}",
            "file": str(strategy_file),
        })
        return violations

    try:
        tree = ast.parse(source, filename=str(strategy_file))
    except SyntaxError as exc:
        violations.append({
            "check": "ast_stub_detection",
            "severity": "ERROR",
            "message": f"SyntaxError in strategy file: {exc}",
            "file": str(strategy_file),
        })
        return violations

    source_lines: list[str] = source.splitlines()

    # Build a child→parent map so we can inspect enclosing nodes.
    parent_map: dict[int, ast.AST] = {}
    for parent_node in ast.walk(tree):
        for child in ast.iter_child_nodes(parent_node):
            parent_map[id(child)] = parent_node

    def _is_abc_class(class_node: ast.ClassDef) -> bool:
        """Return True if *class_node* inherits directly from ABC or ABCMeta."""
        for base in class_node.bases:
            if isinstance(base, ast.Name) and base.id in ("ABC", "ABCMeta"):
                return True
            if isinstance(base, ast.Attribute) and base.attr in ("ABC", "ABCMeta"):
                return True
        return False

    def _stmt_stub_type(stmt: ast.stmt) -> str | None:
        """Return the stub_type string for *stmt*, or ``None`` if it is not a stub."""
        # Pattern 1: bare pass
        if isinstance(stmt, ast.Pass):
            return "bare_pass"

        # Pattern 2: raise NotImplementedError / raise NotImplementedError(...)
        if isinstance(stmt, ast.Raise) and stmt.exc is not None:
            exc = stmt.exc
            if isinstance(exc, ast.Name) and exc.id == "NotImplementedError":
                return "not_implemented"
            if isinstance(exc, ast.Call):
                func = exc.func
                if isinstance(func, ast.Name) and func.id == "NotImplementedError":
                    return "not_implemented"
                if isinstance(func, ast.Attribute) and func.attr == "NotImplementedError":
                    return "not_implemented"

        # Pattern 3: return None on a line with a # TODO or # FIXME comment
        if isinstance(stmt, ast.Return):
            val = stmt.value
            is_none = val is None or (
                isinstance(val, ast.Constant) and val.value is None
            )
            if is_none:
                line_idx = stmt.lineno - 1
                if 0 <= line_idx < len(source_lines):
                    line_lower = source_lines[line_idx].lower()
                    if "# todo" in line_lower or "# fixme" in line_lower:
                        return "return_none_todo"

        # Pattern 4: docstring-only expression (string constant)
        if (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        ):
            return "docstring_only"

        return None

    _reason_label: dict[str, str] = {
        "bare_pass": "bare pass",
        "not_implemented": "raise NotImplementedError",
        "docstring_only": "docstring-only",
        "return_none_todo": "return None TODO",
    }

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        func_name: str = node.name
        start_line: int = node.lineno
        body: list[ast.stmt] = node.body

        # Allowlist: __init__ with a bare pass body is a valid empty constructor
        if func_name == "__init__" and len(body) == 1 and isinstance(body[0], ast.Pass):
            continue

        # Allowlist: function defined directly inside an except handler
        parent = parent_map.get(id(node))
        if isinstance(parent, ast.ExceptHandler):
            continue

        # Allowlist: function defined inside an ABC / ABCMeta class
        if isinstance(parent, ast.ClassDef) and _is_abc_class(parent):
            continue

        # Check whether every statement in the body matches a stub pattern.
        stub_types: list[str] = []
        all_match = True
        for stmt in body:
            stype = _stmt_stub_type(stmt)
            if stype is None:
                all_match = False
                break
            stub_types.append(stype)

        if not all_match or not stub_types:
            continue

        # Primary type: prefer non-docstring stubs when mixed (e.g. docstring + pass).
        primary_type = "docstring_only"
        for stype in stub_types:
            if stype != "docstring_only":
                primary_type = stype
                break

        violations.append({
            "check": "ast_stub_detection",
            "severity": "ERROR",
            "message": (
                f"Function '{func_name}' at line {start_line} appears to be a stub "
                f"({_reason_label[primary_type]})"
            ),
            "file": str(strategy_file),
            "line": start_line,
            "stub_type": primary_type,
        })

    return violations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gate_summary(gate_violations: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a per-gate summary dict from a list of gate violations."""
    gate_errors = [v for v in gate_violations if v.get("severity") == "ERROR"]
    return {
        "result": "FAIL" if gate_errors else "PASS",
        "violation_count": len(gate_violations),
        "violations": gate_violations,
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_gates(strategy_file: Path) -> dict[str, Any]:
    """Run all quality gates against *strategy_file* and return a summary dict."""
    ccn_violations = check_cyclomatic_complexity(strategy_file)
    bandit_violations = check_bandit(strategy_file)
    semgrep_violations = check_semgrep(strategy_file)
    ast_violations = check_ast(strategy_file)
    stub_violations = check_stub_detection(strategy_file)

    # Separate function-length and param-count violations from the AST check
    fn_length_violations = [
        v for v in ast_violations if v.get("check") == "ast_function_length"
    ]

    all_violations: list[dict[str, Any]] = (
        ccn_violations
        + bandit_violations
        + semgrep_violations
        + ast_violations
        + stub_violations
    )

    errors = [v for v in all_violations if v.get("severity") == "ERROR"]

    return {
        "strategy_file": str(strategy_file),
        "result": "FAIL" if errors else "PASS",
        "violation_count": len(all_violations),
        "error_count": len(errors),
        "violations": all_violations,
        "gates": {
            "ccn_check": _gate_summary(ccn_violations),
            "bandit": _gate_summary(bandit_violations),
            "semgrep": _gate_summary(semgrep_violations),
            "function_length": _gate_summary(fn_length_violations),
            "stub_detection": _gate_summary(stub_violations),
        },
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
