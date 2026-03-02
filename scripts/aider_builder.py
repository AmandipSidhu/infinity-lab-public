#!/usr/bin/env python3
"""Aider Builder — 4-tier model escalation chain for the ACB pipeline.

Invokes Aider with the spec file and escalates through model tiers
(Gemini 2.5 Flash → GitHub GPT-4o → GPT-5 → Claude Opus 4.5) until
the strategy builds successfully or all tiers are exhausted.

Exit codes:
  0 — Build succeeded
  1 — All tiers exhausted — manual intervention required
  2 — Invalid arguments or missing spec file
"""

import argparse
import os
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_ITERATIONS: int = 30
_RATE_LIMIT_MAX_RETRIES: int = 3
_SAME_ERROR_THRESHOLD: int = 3
_CONSECUTIVE_SYNTAX_THRESHOLD: int = 3
_STUCK_ITERATIONS_THRESHOLD: int = 8
_PROGRESSIVE_DEGRADATION_WINDOW: int = 5
_MIN_TEST_PASS_RATE_TIER3: float = 0.70
_BACKOFF_BASE_SECONDS: float = 2.0
_BACKOFF_CAP_SECONDS: float = 60.0

_TIER1_MODEL: str = "gemini/gemini-2.5-flash"
_TIER2_MODEL: str = "github/gpt-4o"
_TIER3_MODEL: str = "gpt-5"
_TIER4_MODEL: str = "claude-opus-4.5"

# Subprocess timeout (seconds) for tiers with per-call timeout escalation.
_TIER1_SUBPROCESS_TIMEOUT: int = 30
_TIER2_SUBPROCESS_TIMEOUT: int = 30


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class AiderResult:
    """Result of a single aider subprocess invocation."""

    success: bool
    returncode: int
    stdout: str
    stderr: str
    elapsed: float


@dataclass
class TierRunResult:
    """Result of running a complete tier (up to max_iterations)."""

    success: bool
    tier: int
    model: str
    iterations_used: int
    escalation_reason: str
    last_output: str


# ---------------------------------------------------------------------------
# Aider command / prompt builders
# ---------------------------------------------------------------------------


def _build_aider_prompt(spec_file: Path, spec_name: str) -> str:
    return (
        f"Read the spec file at {spec_file} and implement a QuantConnect LEAN algorithm "
        f"in strategies/{spec_name}.py that satisfies all acceptance_criteria defined in the spec. "
        f"Also write comprehensive unit tests in tests/test_{spec_name}.py. "
        f"Do NOT modify any files outside the strategies/ and tests/ directories."
    )


def _build_aider_cmd(model: str, spec_file: Path, spec_name: str) -> list[str]:
    strategy_file = f"strategies/{spec_name}.py"
    test_file = f"tests/test_{spec_name}.py"
    prompt = _build_aider_prompt(spec_file, spec_name)
    return [
        "aider",
        "--model", model,
        "--yes",
        "--no-git",
        "--message", prompt,
        strategy_file,
        test_file,
    ]


# ---------------------------------------------------------------------------
# Aider invocation
# ---------------------------------------------------------------------------


def _run_aider(
    model: str,
    spec_file: Path,
    spec_name: str,
    timeout: int | None = None,
) -> AiderResult:
    """Run aider once and return an AiderResult.

    Raises subprocess.TimeoutExpired if timeout is set and exceeded.
    """
    cmd = _build_aider_cmd(model, spec_file, spec_name)
    start = time.monotonic()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    elapsed = time.monotonic() - start
    return AiderResult(
        success=result.returncode == 0,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        elapsed=elapsed,
    )


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

_PYTEST_PASSED_RE: re.Pattern[str] = re.compile(r"(\d+) passed")
_PYTEST_FAILED_RE: re.Pattern[str] = re.compile(r"(\d+) failed")


def _detect_rate_limit(output: str) -> bool:
    lower = output.lower()
    return any(
        p in lower
        for p in ("429", "rate limit", "quota exceeded", "too many requests", "ratelimit")
    )


def _detect_daily_limit(output: str) -> bool:
    lower = output.lower()
    return any(
        p in lower
        for p in ("daily limit", "daily quota", "exceeded your daily", "daily request limit")
    )


def _detect_api_unavailable(output: str) -> bool:
    lower = output.lower()
    return any(
        p in lower
        for p in (
            "502 bad gateway",
            "503 service unavailable",
            "503",
            "504 gateway",
            "service unavailable",
            "api unavailable",
        )
    )


def _detect_syntax_error(output: str) -> bool:
    return "SyntaxError" in output or "syntaxerror" in output.lower()


def _extract_error_fingerprint(output: str) -> str:
    """Return a normalised error fingerprint for 'same error 3×' detection."""
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if any(
            kw in stripped
            for kw in ("Error:", "ERROR:", "error:", "FAILED", "Exception:")
        ):
            normalized = re.sub(r"\d+", "N", stripped)
            normalized = re.sub(r"/[^\s]+", "/PATH", normalized)
            return normalized[:120]
    return ""


def _extract_test_pass_rate(output: str) -> float | None:
    """Parse pytest summary output and return a pass rate in [0.0, 1.0].

    Returns None if no pytest summary is found.
    """
    passed_m = _PYTEST_PASSED_RE.search(output)
    if passed_m:
        passed = int(passed_m.group(1))
        failed_m = _PYTEST_FAILED_RE.search(output)
        failed = int(failed_m.group(1)) if failed_m else 0
        total = passed + failed
        return passed / total if total > 0 else 0.0
    return None


# ---------------------------------------------------------------------------
# Exponential backoff with jitter
# ---------------------------------------------------------------------------


def _backoff_wait(attempt: int) -> float:
    """Return a wait duration: base * 2^attempt with ±25% jitter, capped at 60 s."""
    delay = min(_BACKOFF_BASE_SECONDS * (2 ** attempt), _BACKOFF_CAP_SECONDS)
    jitter = delay * 0.25 * (2.0 * random.random() - 1.0)
    return max(0.0, delay + jitter)


# ---------------------------------------------------------------------------
# Tier runners
# ---------------------------------------------------------------------------


def run_tier_1(spec_file: Path, spec_name: str) -> TierRunResult:
    """Tier 1 — Gemini 2.5 Flash.

    Escalates on: rate limit (HTTP 429), timeout >30 s,
    3 consecutive syntax errors, same error 3× in a row.
    Rate limits are retried with exponential backoff (up to _RATE_LIMIT_MAX_RETRIES)
    before escalating.
    """
    model = _TIER1_MODEL
    consecutive_syntax = 0
    same_error_count = 0
    last_error_fp = ""
    rate_limit_retries = 0

    for iteration in range(_MAX_ITERATIONS):
        try:
            result = _run_aider(model, spec_file, spec_name, timeout=_TIER1_SUBPROCESS_TIMEOUT)
        except subprocess.TimeoutExpired:
            return TierRunResult(False, 1, model, iteration + 1, "timeout", "")

        combined = result.stdout + result.stderr

        if result.success:
            return TierRunResult(True, 1, model, iteration + 1, "", combined)

        # Rate limit: backoff and retry; escalate after max retries.
        if _detect_rate_limit(combined):
            time.sleep(_backoff_wait(rate_limit_retries))
            rate_limit_retries += 1
            if rate_limit_retries >= _RATE_LIMIT_MAX_RETRIES:
                return TierRunResult(False, 1, model, iteration + 1, "rate_limit", combined)
            continue

        rate_limit_retries = 0  # reset on non-rate-limit result

        # Consecutive syntax error tracking.
        if _detect_syntax_error(combined):
            consecutive_syntax += 1
        else:
            consecutive_syntax = 0

        if consecutive_syntax >= _CONSECUTIVE_SYNTAX_THRESHOLD:
            return TierRunResult(
                False, 1, model, iteration + 1, "consecutive_syntax_errors", combined
            )

        # Same error fingerprint tracking.
        fp = _extract_error_fingerprint(combined)
        if fp and fp == last_error_fp:
            same_error_count += 1
        else:
            same_error_count = 1
            last_error_fp = fp

        if same_error_count >= _SAME_ERROR_THRESHOLD:
            return TierRunResult(
                False, 1, model, iteration + 1, "same_error_repeated", combined
            )

    return TierRunResult(False, 1, model, _MAX_ITERATIONS, "iterations_exhausted", "")


def run_tier_2(spec_file: Path, spec_name: str) -> TierRunResult:
    """Tier 2 — GitHub Models GPT-4o.

    Escalates on: daily limit hit, API unavailable, timeout >30 s,
    quality degradation (same error 3×).
    """
    model = _TIER2_MODEL
    same_error_count = 0
    last_error_fp = ""

    for iteration in range(_MAX_ITERATIONS):
        try:
            result = _run_aider(model, spec_file, spec_name, timeout=_TIER2_SUBPROCESS_TIMEOUT)
        except subprocess.TimeoutExpired:
            return TierRunResult(False, 2, model, iteration + 1, "timeout", "")

        combined = result.stdout + result.stderr

        if result.success:
            return TierRunResult(True, 2, model, iteration + 1, "", combined)

        if _detect_daily_limit(combined):
            return TierRunResult(False, 2, model, iteration + 1, "daily_limit", combined)

        if _detect_api_unavailable(combined):
            return TierRunResult(False, 2, model, iteration + 1, "api_unavailable", combined)

        fp = _extract_error_fingerprint(combined)
        if fp and fp == last_error_fp:
            same_error_count += 1
        else:
            same_error_count = 1
            last_error_fp = fp

        if same_error_count >= _SAME_ERROR_THRESHOLD:
            return TierRunResult(
                False, 2, model, iteration + 1, "same_error_repeated", combined
            )

    return TierRunResult(False, 2, model, _MAX_ITERATIONS, "iterations_exhausted", "")


def run_tier_3(spec_file: Path, spec_name: str) -> TierRunResult:
    """Tier 3 — GPT-5.

    Escalates on: 30 iterations exhausted with <70 % tests passing,
    progressive degradation (pass rate falling for 5 consecutive iterations),
    or stuck pattern (no improvement for 8 consecutive iterations).
    """
    model = _TIER3_MODEL
    pass_rates: list[float] = []
    stuck_count = 0
    prev_pass_rate: float | None = None

    for iteration in range(_MAX_ITERATIONS):
        result = _run_aider(model, spec_file, spec_name, timeout=None)
        combined = result.stdout + result.stderr

        if result.success:
            return TierRunResult(True, 3, model, iteration + 1, "", combined)

        pass_rate = _extract_test_pass_rate(combined)
        if pass_rate is not None:
            pass_rates.append(pass_rate)

            # Stuck pattern: no forward progress for _STUCK_ITERATIONS_THRESHOLD iterations.
            if prev_pass_rate is not None and pass_rate <= prev_pass_rate:
                stuck_count += 1
            else:
                stuck_count = 0
            prev_pass_rate = pass_rate

            if stuck_count >= _STUCK_ITERATIONS_THRESHOLD:
                return TierRunResult(
                    False, 3, model, iteration + 1, "stuck_pattern", combined
                )

            # Progressive degradation: pass rate strictly decreasing over last window.
            if len(pass_rates) >= _PROGRESSIVE_DEGRADATION_WINDOW:
                window = pass_rates[-_PROGRESSIVE_DEGRADATION_WINDOW:]
                if all(window[i] > window[i + 1] for i in range(len(window) - 1)):
                    return TierRunResult(
                        False, 3, model, iteration + 1, "progressive_degradation", combined
                    )

    # 30 iterations exhausted — escalate if tests are still failing badly.
    final_pass_rate = pass_rates[-1] if pass_rates else 0.0
    reason = (
        "iterations_exhausted_low_pass_rate"
        if final_pass_rate < _MIN_TEST_PASS_RATE_TIER3
        else "iterations_exhausted"
    )
    return TierRunResult(False, 3, model, _MAX_ITERATIONS, reason, "")


def run_tier_4(spec_file: Path, spec_name: str) -> TierRunResult:
    """Tier 4 — Claude Opus 4.5.

    Final boss: no further escalation.  On failure after all iterations
    the caller writes a diagnostic to GITHUB_STEP_SUMMARY and exits 1.
    """
    model = _TIER4_MODEL
    last_output = ""

    for iteration in range(_MAX_ITERATIONS):
        result = _run_aider(model, spec_file, spec_name, timeout=None)
        last_output = result.stdout + result.stderr

        if result.success:
            return TierRunResult(True, 4, model, iteration + 1, "", last_output)

    return TierRunResult(
        False, 4, model, _MAX_ITERATIONS, "all_tiers_exhausted", last_output
    )


# ---------------------------------------------------------------------------
# Git commit / push helpers
# ---------------------------------------------------------------------------


def _git_commit_and_push(spec_name: str, model: str, tier: int) -> bool:
    """Commit and push generated strategy/test files to the repo.

    Returns True on success.  Returns True (with a warning) if no files were
    changed.  Returns False if the commit or push fails.
    """
    # Configure git bot identity.
    subprocess.run(
        ["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"],
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "github-actions[bot]"],
        check=True,
    )

    # Stage the generated files.
    subprocess.run(
        ["git", "add", f"strategies/{spec_name}.py", f"tests/test_{spec_name}.py"],
        check=True,
    )

    # Detect whether any files were actually staged.
    diff_result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        capture_output=True,
    )
    if diff_result.returncode == 0:
        print(
            f"[aider_builder] WARNING: Aider produced no output for {spec_name!r}; "
            "nothing to commit.",
            file=sys.stderr,
        )
        return True

    # Commit with a descriptive message.
    commit_msg = f"feat(aider): build {spec_name} strategy via {model} (tier {tier})"
    commit_result = subprocess.run(
        ["git", "commit", "-m", commit_msg],
        capture_output=True,
        text=True,
    )
    if commit_result.returncode != 0:
        print(
            f"[aider_builder] ERROR: git commit failed:\n"
            f"{commit_result.stdout}\n{commit_result.stderr}",
            file=sys.stderr,
        )
        return False

    # Inject GITHUB_TOKEN into the remote URL for authenticated push.
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        remote_result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
        )
        if remote_result.returncode == 0:
            remote_url = remote_result.stdout.strip()
            if remote_url.startswith("https://"):
                auth_url = remote_url.replace("https://", f"https://x-access-token:{token}@")
                subprocess.run(
                    ["git", "remote", "set-url", "origin", auth_url],
                    check=True,
                )

    # Push to origin on the current branch.
    push_result = subprocess.run(
        ["git", "push", "origin", "HEAD"],
        capture_output=True,
        text=True,
    )
    if push_result.returncode != 0:
        print(
            f"[aider_builder] ERROR: git push failed:\n{push_result.stderr}",
            file=sys.stderr,
        )
        return False

    print(
        f"[aider_builder] Committed and pushed strategies/{spec_name}.py "
        f"and tests/test_{spec_name}.py"
    )
    return True


# ---------------------------------------------------------------------------
# GITHUB_STEP_SUMMARY output
# ---------------------------------------------------------------------------


def _write_step_summary(
    spec_file: str,
    spec_name: str,
    model_used: str,
    tiers_attempted: int,
    total_iterations: int,
    success: bool,
    failure_details: str = "",
) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    result_str = "SUCCESS" if success else "FAILURE"
    lines = [
        "## Aider Build Results\n\n",
        f"**Spec**: `{spec_file}`\n",
        f"**Model used**: `{model_used}`\n",
        f"**Tiers attempted**: {tiers_attempted}\n",
        f"**Iterations**: {total_iterations}\n",
        f"**Result**: {result_str}\n",
        f"**Strategy file**: `strategies/{spec_name}.py`\n",
        f"**Test file**: `tests/test_{spec_name}.py`\n",
    ]
    if not success and failure_details:
        lines.append(f"\n### Failure Diagnostic\n\n```\n{failure_details}\n```\n")
    Path(summary_path).write_text("".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def build(spec_file_str: str) -> bool:
    """Run the 4-tier aider build chain for the given spec file.

    Returns True on success, False on failure.
    """
    spec_file = Path(spec_file_str)
    if not spec_file.is_file():
        print(f"ERROR: spec file not found: {spec_file}", file=sys.stderr)
        return False

    with spec_file.open(encoding="utf-8") as fh:
        spec_data: dict = yaml.safe_load(fh) or {}

    spec_name = spec_file.stem

    # Ensure target directories exist so aider can create the output files.
    Path("strategies").mkdir(exist_ok=True)
    Path("tests").mkdir(exist_ok=True)

    metadata = spec_data.get("metadata", {})
    strategy_name = metadata.get("name", spec_name)
    print(f"[aider_builder] Building strategy: {strategy_name!r} (spec_name={spec_name!r})")

    tier_runners = [run_tier_1, run_tier_2, run_tier_3, run_tier_4]
    tier_models = [_TIER1_MODEL, _TIER2_MODEL, _TIER3_MODEL, _TIER4_MODEL]
    total_iterations = 0
    last_model = _TIER1_MODEL

    for tier_num, runner in enumerate(tier_runners, start=1):
        current_model = tier_models[tier_num - 1]
        print(f"[aider_builder] Starting Tier {tier_num} ({current_model})...")
        tier_result = runner(spec_file, spec_name)
        total_iterations += tier_result.iterations_used
        last_model = tier_result.model

        if tier_result.success:
            print(
                f"[aider_builder] Tier {tier_num} succeeded "
                f"after {tier_result.iterations_used} iteration(s)."
            )
            _write_step_summary(
                spec_file_str,
                spec_name,
                last_model,
                tier_num,
                total_iterations,
                True,
            )
            if not _git_commit_and_push(spec_name, tier_result.model, tier_result.tier):
                return False
            return True

        print(
            f"[aider_builder] Tier {tier_num} failed "
            f"after {tier_result.iterations_used} iteration(s). "
            f"Reason: {tier_result.escalation_reason}"
        )
        if tier_num < 4:
            print(
                f"[aider_builder] Escalating to Tier {tier_num + 1} "
                f"({tier_models[tier_num]})..."
            )

    # All 4 tiers exhausted.
    print(
        "[aider_builder] All 4 tiers exhausted. Manual intervention required.",
        file=sys.stderr,
    )
    _write_step_summary(
        spec_file_str,
        spec_name,
        last_model,
        4,
        total_iterations,
        False,
        failure_details=(
            "All 4 model tiers were exhausted without successfully building the strategy.\n"
            f"Final tier model: {last_model}\n"
            f"Total iterations across all tiers: {total_iterations}\n"
            "Manual review and intervention required."
        ),
    )
    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Aider Builder — 4-tier model escalation chain for the ACB pipeline"
    )
    parser.add_argument("--spec", required=True, help="Path to the spec YAML file")
    args = parser.parse_args(argv)

    success = build(args.spec)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
