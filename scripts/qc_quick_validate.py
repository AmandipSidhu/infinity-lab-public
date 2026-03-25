#!/usr/bin/env python3
"""Quick validation for QuantConnect LEAN Python strategies.

Checks:
  1. Python syntax (py_compile)
  2. Anti-pattern rejection:
     - algorithm.portfolio.  (common hallucination)
     - self.algorithm.       (wrong delegation pattern)
  3. Required pattern:
     - class *Algorithm(QCAlgorithm)

Usage:
    python scripts/qc_quick_validate.py strategies/my_strategy.py

Exit codes:
    0 — passed all checks
    1 — failed (error message printed to stderr)
    2 — invalid arguments
"""

import py_compile
import re
import sys
import tempfile
from pathlib import Path


ANTI_PATTERNS: list[tuple[str, str]] = [
    (
        r"algorithm\.portfolio\.",
        "Hallucination: 'algorithm.portfolio.' is not valid. Use self.portfolio[symbol] instead.",
    ),
    (
        r"self\.algorithm\.",
        "Hallucination: 'self.algorithm.' is not valid. 'self' IS the algorithm. "
        "Call methods directly: self.market_order(), self.liquidate(), etc.",
    ),
]

REQUIRED_PATTERN = re.compile(r"class\s+\w+Algorithm\s*\(\s*QCAlgorithm\s*\)")


def check_syntax(file_path: Path) -> str | None:
    """Return error string if syntax check fails, else None."""
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        py_compile.compile(str(file_path), cfile=tmp_path, doraise=True)
        return None
    except py_compile.PyCompileError as exc:
        return f"Syntax error: {exc}"
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def check_anti_patterns(source: str) -> list[str]:
    """Return list of anti-pattern violation messages found in source."""
    errors = []
    for pattern, message in ANTI_PATTERNS:
        if re.search(pattern, source):
            errors.append(message)
    return errors


def check_required_patterns(source: str) -> list[str]:
    """Return list of missing required patterns."""
    errors = []
    if not REQUIRED_PATTERN.search(source):
        errors.append(
            "Missing required pattern: class <Name>Algorithm(QCAlgorithm). "
            "Strategy class must inherit from QCAlgorithm."
        )
    return errors


def validate_file(file_path: Path) -> list[str]:
    """Run all validation checks and return a list of error messages (empty = pass)."""
    if not file_path.exists():
        return [f"File not found: {file_path}"]

    if file_path.suffix != ".py":
        return [f"Expected a .py file, got: {file_path}"]

    errors: list[str] = []

    # 1. Syntax check
    syntax_error = check_syntax(file_path)
    if syntax_error:
        errors.append(syntax_error)
        # Cannot do pattern checks on unparseable code
        return errors

    source = file_path.read_text(encoding="utf-8")

    # 2. Anti-pattern checks
    errors.extend(check_anti_patterns(source))

    # 3. Required pattern checks
    errors.extend(check_required_patterns(source))

    return errors


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <strategy.py>", file=sys.stderr)
        sys.exit(2)

    file_path = Path(sys.argv[1])
    errors = validate_file(file_path)

    if errors:
        print(f"VALIDATION FAILED: {file_path}", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        sys.exit(1)

    print(f"VALIDATION PASSED: {file_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
