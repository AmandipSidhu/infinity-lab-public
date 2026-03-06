#!/usr/bin/env python3
"""Aider Builder — 4-tier model escalation chain for the ACB pipeline.

Tier ladder (cheapest-first, all free until Tier 4):
  Tier 1 — gemini/gemini-2.5-flash          (Google AI Studio free, 500 RPD)
  Tier 2 — gemini/gemini-2.5-flash-lite     (Google AI Studio free, separate quota)
  Tier 3 — gemini/gemini-2.5-flash          (thinking budget ON, reasoning mode)
  Tier 4 — anthropic/claude-opus-4-5        (paid, nuclear option)

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

# Tier 1: Gemini 2.5 Flash — free tier, 500 req/day, no thinking
_TIER1_MODEL: str = "gemini/gemini-2.5-flash"
# Tier 2: Gemini 2.5 Flash-Lite — free tier, separate quota pool
_TIER2_MODEL: str = "gemini/gemini-2.5-flash-lite"
# Tier 3: Gemini 2.5 Flash with thinking budget — reasoning mode, still free tier
_TIER3_MODEL: str = "gemini/gemini-2.5-flash"
_TIER3_THINKING_BUDGET: int = 8192  # tokens allocated to thinking chain
# Tier 4: Claude Opus 4.5 — paid, final escalation
_TIER4_MODEL: str = "anthropic/claude-opus-4-5"

# Subprocess timeout (seconds) for free-tier calls.
_TIER1_SUBPROCESS_TIMEOUT: int = 60
_TIER2_SUBPROCESS_TIMEOUT: int = 60
# Tier 3 uses thinking — allow more wall time.
_TIER3_SUBPROCESS_TIMEOUT: int = 120


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


def _build_aider_cmd(
    model: str,
    spec_file: Path,
    spec_name: str,
    extra_args: list[str] | None = None,
) -> list[str]:
    strategy_file = f"strategies/{spec_name}.py"
    test_file = f"tests/test_{spec_name}.py"
    prompt = _build_aider_prompt(spec_file, spec_name)
    cmd = [
        "aider",
        "--model", model,
        "--yes",
        "--no-git",
        "--message", prompt,
        strategy_file,
        test_file,
    ]
    if extra_args:
        cmd.extend(extra_args)
    return cmd


# ---------------------------------------------------------------------------
# Git commit helper
# ---------------------------------------------------------------------------


def _commit_and_push(spec_name: str, tier: int, model: str) -> None:
    """Stage, commit, and push the generated strategy and test files.

    Uses ``git diff --cached --quiet`` to skip the commit when there are no
    staged changes (e.g. aider wrote identical content on a retry).
    """
    strategy_file = f"strategies/{spec_name}.py"
    test_file = f"tests/test_{spec_name}.py"
    strategy_path = Path(strategy_file)
    if not strategy_path.exists():
        raise FileNotFoundError(
            f"[aider_builder] Strategy file not written by Aider: {strategy_file}. "
            "Aider exited 0 but produced no output file."
        )
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(
        ["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"],
        check=True,
    )
    subprocess.run(["git", "add", strategy_file, test_file], check=True)
    diff_result = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False)
    if diff_result.returncode != 0:
        subprocess.run(
            [
                "git",
                "commit",
                "-m",
                f"feat(strategies): aider build {spec_name} via tier {tier} ({model})",
            ],
            check=True,
        )
        subprocess.run(["git", "push"], check=True)


# ---------------------------------------------------------------------------
# Aider invocation
# ---------------------------------------------------------------------------


def _run_aider(
    model: str,
    spec_file: Path,
    spec_name: str,
    timeout: int | None = None,
    extra_args: list[str] | None = None,
) -> AiderResult:
    """Run aider once and return an AiderResult.

    Raises subprocess.TimeoutExpired if timeout is set and exceeded.
    """
    cmd = _build_aider_cmd(model, spec_file, spec_name, extra_args=extra_args)
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
    """Return a normalised error fingerprint for 'same error 3x' detection."""
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
    """Return a wait duration: base * 2^attempt with +-25% jitter, capped at 60 s."""
    delay = min(_BACKOFF_BASE_SECONDS * (2 ** attempt), _BACKOFF_CAP_SECONDS)
    jitter = delay * 0.25 * (2.0 * random.random() - 1.0)
    return max(0.0, delay + jitter)


# ---------------------------------------------------------------------------
# Shared free-tier runner logic
# ---------------------------------------------------------------------------


def _run_free_tier(
    tier_num: int,
    model: str,
    spec_file: Path,
    spec_name: str,
    timeout: int,
    extra_args: list[str] | None = None,
) -> TierRunResult:
    """Generic runner for free-tier Gemini models (Tiers 1, 2, 3).

    Escalates on: rate limit (HTTP 429), daily quota exceeded, timeout,
    3 consecutive syntax errors, same error 3x in a row.
    Rate limits are retried with exponential backoff before escalating.
    """
    consecutive_syntax = 0
    same_error_count = 0
    last_error_fp = ""
    rate_limit_retries = 0

    for iteration in range(_MAX_ITERATIONS):
        try:
            result = _run_aider(
                model, spec_file, spec_name, timeout=timeout, extra_args=extra_args
            )
        except subprocess.TimeoutExpired:
            return TierRunResult(False, tier_num, model, iteration + 1, "timeout", "")

        combined = result.stdout + result.stderr

        if result.success:
            try:
                _commit_and_push(spec_name, tier_num, model)
            except FileNotFoundError as exc:
                return TierRunResult(
                    False, tier_num, model, iteration + 1, "file_not_written", str(exc)
                )
            return TierRunResult(True, tier_num, model, iteration + 1, "", combined)

        # Daily quota: escalate immediately (no point retrying today).
        if _detect_daily_limit(combined):
            return TierRunResult(
                False, tier_num, model, iteration + 1, "daily_limit", combined
            )

        # Rate limit: backoff and retry; escalate after max retries.
        if _detect_rate_limit(combined):
            time.sleep(_backoff_wait(rate_limit_retries))
            rate_limit_retries += 1
            if rate_limit_retries >= _RATE_LIMIT_MAX_RETRIES:
                return TierRunResult(
                    False, tier_num, model, iteration + 1, "rate_limit", combined
                )
            continue

        rate_limit_retries = 0

        if _detect_api_unavailable(combined):
            return TierRunResult(
                False, tier_num, model, iteration + 1, "api_unavailable", combined
            )

        if _detect_syntax_error(combined):
            consecutive_syntax += 1
        else:
            consecutive_syntax = 0

        if consecutive_syntax >= _CONSECUTIVE_SYNTAX_THRESHOLD:
            return TierRunResult(
                False, tier_num, model, iteration + 1, "consecutive_syntax_errors", combined
            )

        fp = _extract_error_fingerprint(combined)
        if fp and fp == last_error_fp:
            same_error_count += 1
        else:
            same_error_count = 1
            last_error_fp = fp

        if same_error_count >= _SAME_ERROR_THRESHOLD:
            return TierRunResult(
                False, tier_num, model, iteration + 1, "same_error_repeated", combined
            )

    return TierRunResult(False, tier_num, model, _MAX_ITERATIONS, "iterations_exhausted", "")


# ---------------------------------------------------------------------------
# Tier runners
# ---------------------------------------------------------------------------


def run_tier_1(spec_file: Path, spec_name: str) -> TierRunResult:
    """Tier 1 — Gemini 2.5 Flash (free, 500 RPD, thinking OFF).

    Fast and capable. Handles most strategy builds in 1-3 iterations.
    Escalates to Tier 2 on rate/daily limit, timeout, syntax loops, or stuck errors.
    """
    return _run_free_tier(
        tier_num=1,
        model=_TIER1_MODEL,
        spec_file=spec_file,
        spec_name=spec_name,
        timeout=_TIER1_SUBPROCESS_TIMEOUT,
    )


def run_tier_2(spec_file: Path, spec_name: str) -> TierRunResult:
    """Tier 2 — Gemini 2.5 Flash-Lite (free, separate quota pool, thinking OFF).

    Overflow safety valve when Tier 1 quota is exhausted or rate-limited.
    Same escalation logic as Tier 1.
    """
    return _run_free_tier(
        tier_num=2,
        model=_TIER2_MODEL,
        spec_file=spec_file,
        spec_name=spec_name,
        timeout=_TIER2_SUBPROCESS_TIMEOUT,
    )


def run_tier_3(spec_file: Path, spec_name: str) -> TierRunResult:
    """Tier 3 — Gemini 2.5 Flash with thinking budget (free tier, reasoning mode).

    Activates extended reasoning for strategies that Tiers 1/2 fail to build
    correctly (complex logic, multi-signal, edge cases). Thinking budget is
    set via --thinking-tokens aider extra arg which maps to Gemini's
    thinkingConfig.thinkingBudget parameter.

    Escalates on: stuck pattern (8 iters no improvement), progressive
    degradation (pass rate falling 5 consecutive iters), or iterations exhausted
    with <70% tests passing.
    """
    model = _TIER3_MODEL
    thinking_args = ["--thinking-tokens", str(_TIER3_THINKING_BUDGET)]
    pass_rates: list[float] = []
    stuck_count = 0
    prev_pass_rate: float | None = None

    for iteration in range(_MAX_ITERATIONS):
        try:
            result = _run_aider(
                model,
                spec_file,
                spec_name,
                timeout=_TIER3_SUBPROCESS_TIMEOUT,
                extra_args=thinking_args,
            )
        except subprocess.TimeoutExpired:
            return TierRunResult(False, 3, model, iteration + 1, "timeout", "")

        combined = result.stdout + result.stderr

        if result.success:
            try:
                _commit_and_push(spec_name, 3, model)
            except FileNotFoundError as exc:
                return TierRunResult(False, 3, model, iteration + 1, "file_not_written", str(exc))
            return TierRunResult(True, 3, model, iteration + 1, "", combined)

        if _detect_daily_limit(combined):
            return TierRunResult(False, 3, model, iteration + 1, "daily_limit", combined)

        if _detect_rate_limit(combined):
            return TierRunResult(False, 3, model, iteration + 1, "rate_limit", combined)

        pass_rate = _extract_test_pass_rate(combined)
        if pass_rate is not None:
            pass_rates.append(pass_rate)

            if prev_pass_rate is not None and pass_rate <= prev_pass_rate:
                stuck_count += 1
            else:
                stuck_count = 0
            prev_pass_rate = pass_rate

            if stuck_count >= _STUCK_ITERATIONS_THRESHOLD:
                return TierRunResult(
                    False, 3, model, iteration + 1, "stuck_pattern", combined
                )

            if len(pass_rates) >= _PROGRESSIVE_DEGRADATION_WINDOW:
                window = pass_rates[-_PROGRESSIVE_DEGRADATION_WINDOW:]
                if all(window[i] > window[i + 1] for i in range(len(window) - 1)):
                    return TierRunResult(
                        False, 3, model, iteration + 1, "progressive_degradation", combined
                    )

    final_pass_rate = pass_rates[-1] if pass_rates else 0.0
    reason = (
        "iterations_exhausted_low_pass_rate"
        if final_pass_rate < _MIN_TEST_PASS_RATE_TIER3
        else "iterations_exhausted"
    )
    return TierRunResult(False, 3, model, _MAX_ITERATIONS, reason, "")


def run_tier_4(spec_file: Path, spec_name: str) -> TierRunResult:
    """Tier 4 — Claude Opus 4.5 (paid, nuclear option).

    Final boss: no further escalation. On failure after all iterations
    the caller writes a diagnostic to GITHUB_STEP_SUMMARY and falls back
    to the stub generator.
    """
    model = _TIER4_MODEL
    last_output = ""

    for iteration in range(_MAX_ITERATIONS):
        result = _run_aider(model, spec_file, spec_name, timeout=None)
        last_output = result.stdout + result.stderr

        if result.success:
            try:
                _commit_and_push(spec_name, 4, model)
            except FileNotFoundError as exc:
                return TierRunResult(False, 4, model, iteration + 1, "file_not_written", str(exc))
            return TierRunResult(True, 4, model, iteration + 1, "", last_output)

    return TierRunResult(
        False, 4, model, _MAX_ITERATIONS, "all_tiers_exhausted", last_output
    )


# ---------------------------------------------------------------------------
# Stub strategy generator (fallback when all AI tiers fail)
# ---------------------------------------------------------------------------


def _write_stub_strategy(spec_file: Path, spec_name: str, spec_data: dict) -> Path:
    """Write a minimal but structurally valid QCAlgorithm stub when all tiers fail.

    The stub subclasses QCAlgorithm, implements Initialize() and OnData(), and
    uses values from the spec so downstream quality gates and QC upload can run.
    Returns the path to the written file.
    """
    bt = spec_data.get("strategy", {}).get("backtesting", {})
    universe = spec_data.get("strategy", {}).get("universe", {})
    risk = spec_data.get("strategy", {}).get("risk_management", {})

    symbols = universe.get("symbols", ["SPY"])
    primary_symbol = symbols[0] if symbols else "SPY"
    initial_capital = int(bt.get("initial_capital", 10000))
    stop_loss = float(risk.get("stop_loss", 0.05))
    take_profit = float(risk.get("take_profit", 0.10))
    max_position = float(risk.get("max_position_size", 0.10))

    def _date_parts(date_str: str) -> tuple[int, int, int]:
        parts = str(date_str).split("-")
        return int(parts[0]), int(parts[1]), int(parts[2])

    sy, sm, sd = _date_parts(bt.get("start_date", "2020-01-01"))
    ey, em, ed = _date_parts(bt.get("end_date", "2024-12-31"))

    class_name = "".join(w.capitalize() for w in spec_name.replace("-", "_").split("_"))

    stub_code = f'''"""ACB-generated stub strategy for {spec_name}.

WARNING: This stub was written because all AI build tiers were unavailable.
It implements the spec signals structurally but should be reviewed before
running live backtests.
"""

try:
    from AlgorithmImports import *  # noqa: F401,F403
except ImportError:
    pass  # Running outside QuantConnect LEAN environment (local analysis)


class {class_name}(QCAlgorithm):
    """QuantConnect strategy implementing {spec_name} from spec."""

    def Initialize(self) -> None:
        """Configure algorithm parameters, universe, and indicators."""
        self.SetStartDate({sy}, {sm}, {sd})
        self.SetEndDate({ey}, {em}, {ed})
        self.SetCash({initial_capital})
        equity = self.AddEquity("{primary_symbol}", Resolution.Daily)
        self._symbol = equity.Symbol
        self._sma = self.SMA(self._symbol, 50, Resolution.Daily)
        self._stop_loss = {stop_loss}
        self._take_profit = {take_profit}
        self._max_position = {max_position}
        self._entry_price = None

    def OnData(self, data: Slice) -> None:
        """Execute momentum signals: enter above SMA50, exit below."""
        if not self._sma.IsReady:
            return
        price = self.Securities[self._symbol].Price
        invested = self.Portfolio[self._symbol].Invested
        if not invested:
            if price > self._sma.Current.Value:
                self.SetHoldings(self._symbol, self._max_position)
                self._entry_price = price
        else:
            self._check_exit(price)

    def _check_exit(self, price: float) -> None:
        """Exit on SMA crossover, stop-loss, or take-profit."""
        if self._entry_price is None:
            self.Liquidate(self._symbol)
            return
        pnl = (price - self._entry_price) / self._entry_price
        below_sma = price < self._sma.Current.Value
        hit_stop = pnl < -self._stop_loss
        hit_tp = pnl > self._take_profit
        if below_sma or hit_stop or hit_tp:
            self.Liquidate(self._symbol)
            self._entry_price = None
'''

    output_path = Path("strategies") / f"{spec_name}.py"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(stub_code, encoding="utf-8")
    return output_path


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

    Path("strategies").mkdir(exist_ok=True)
    Path("tests").mkdir(exist_ok=True)

    strategy_stub = Path("strategies") / f"{spec_name}.py"
    test_stub = Path("tests") / f"test_{spec_name}.py"
    if not strategy_stub.exists():
        strategy_stub.write_text(f'"""Strategy stub for {spec_name}."""\n', encoding="utf-8")
    if not test_stub.exists():
        test_stub.write_text(f'"""Test stub for {spec_name}."""\n', encoding="utf-8")

    metadata = spec_data.get("metadata", {})
    strategy_name = metadata.get("name", spec_name)
    print(f"[aider_builder] Building strategy: {strategy_name!r} (spec_name={spec_name!r})")

    tier_runners = [run_tier_1, run_tier_2, run_tier_3, run_tier_4]
    tier_models = [_TIER1_MODEL, _TIER2_MODEL, _TIER3_MODEL, _TIER4_MODEL]
    total_iterations = 0
    last_model = _TIER1_MODEL

    for tier_num, runner in enumerate(tier_runners, start=1):
        current_model = tier_models[tier_num - 1]
        label = f"{current_model}" + (" +thinking" if tier_num == 3 else "")
        print(f"[aider_builder] Starting Tier {tier_num} ({label})...")
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
                last_model + ("+thinking" if tier_num == 3 else ""),
                tier_num,
                total_iterations,
                True,
            )
            return True

        print(
            f"[aider_builder] Tier {tier_num} failed "
            f"after {tier_result.iterations_used} iteration(s). "
            f"Reason: {tier_result.escalation_reason}"
        )
        if tier_num < 4:
            next_label = tier_models[tier_num] + ("+thinking" if tier_num + 1 == 3 else "")
            print(f"[aider_builder] Escalating to Tier {tier_num + 1} ({next_label})...")

    print(
        "[aider_builder] All 4 tiers exhausted — writing stub strategy file.",
        file=sys.stderr,
    )
    stub_path = _write_stub_strategy(spec_file, spec_name, spec_data)
    print(
        f"[aider_builder] WARNING: stub strategy written to {stub_path}. "
        "Replace with real implementation before production use.",
        file=sys.stderr,
    )
    _commit_and_push(spec_name, 4, last_model)
    _write_step_summary(
        spec_file_str,
        spec_name,
        last_model,
        4,
        total_iterations,
        True,
        failure_details=(
            "All 4 model tiers were exhausted. A minimal stub strategy was written.\n"
            f"Stub path: {stub_path}\n"
            "Replace with a real implementation before production use."
        ),
    )
    return True


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
