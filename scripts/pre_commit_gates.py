#!/usr/bin/env python3
"""Pre-Commit Quality Gates — Step 5 of the ACB Pipeline.

Runs flake8 (syntax/style) and black --check (formatting) on all Python
files in the target directory.  Exits 0 if all checks pass, 1 on any failure.

Usage:
    python scripts/pre_commit_gates.py --dir strategies/
    python scripts/pre_commit_gates.py          # defaults to strategies/
"""

import argparse
import subprocess
import sys
from pathlib import Path


def run_flake8(target_dir: Path) -> tuple[bool, str]:
    """Run flake8 on all Python files in target_dir.

    Returns (passed, output).
    """
    py_files = sorted(target_dir.glob("*.py"))
    if not py_files:
        print(
            f"[pre_commit_gates] No Python files found in {target_dir} — skipping flake8."
        )
        return True, ""

    result = subprocess.run(
        ["flake8", "--max-line-length=120", str(target_dir)],
        capture_output=True,
        text=True,
    )
    passed = result.returncode == 0
    output = result.stdout + result.stderr
    if passed:
        print(f"[pre_commit_gates] flake8 PASSED for {target_dir}")
    else:
        print(f"[pre_commit_gates] flake8 FAILED:\n{output}", file=sys.stderr)
    return passed, output


def run_black_check(target_dir: Path) -> tuple[bool, str]:
    """Run black --check on all Python files in target_dir.

    Returns (passed, output).
    """
    py_files = sorted(target_dir.glob("*.py"))
    if not py_files:
        print(
            f"[pre_commit_gates] No Python files found in {target_dir} — skipping black check."
        )
        return True, ""

    result = subprocess.run(
        ["black", "--check", "--line-length=120", str(target_dir)],
        capture_output=True,
        text=True,
    )
    passed = result.returncode == 0
    output = result.stdout + result.stderr
    if passed:
        print(f"[pre_commit_gates] black --check PASSED for {target_dir}")
    else:
        print(f"[pre_commit_gates] black --check FAILED:\n{output}", file=sys.stderr)
    return passed, output


def run_gates(target_dir: str) -> int:
    """Run all quality gates on target_dir.

    Returns 0 if all pass, 1 if any fail.
    """
    path = Path(target_dir)
    if not path.is_dir():
        print(
            f"[pre_commit_gates] ERROR: directory not found: {path}", file=sys.stderr
        )
        return 1

    flake8_passed, _ = run_flake8(path)
    black_passed, _ = run_black_check(path)

    if flake8_passed and black_passed:
        print("[pre_commit_gates] All quality gates PASSED.")
        return 0

    failures = []
    if not flake8_passed:
        failures.append("flake8")
    if not black_passed:
        failures.append("black")
    print(
        f"[pre_commit_gates] Quality gates FAILED: {', '.join(failures)}",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pre-Commit Quality Gates — flake8 + black for generated strategies"
    )
    parser.add_argument(
        "--dir",
        default="strategies",
        help="Directory containing generated Python strategy files (default: strategies/)",
    )
    args = parser.parse_args(argv)
    return run_gates(args.dir)


if __name__ == "__main__":
    sys.exit(main())
