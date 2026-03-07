#!/usr/bin/env python3
"""Aider Builder — 4-tier model escalation chain for the ACB pipeline.

Tier ladder (cheapest-first, all Gemini / Google AI Studio):
  Tier 1 — gemini/gemini-2.5-flash          (free tier, 500 RPD, thinking OFF)
  Tier 2 — gemini/gemini-2.0-flash-lite     (free tier, separate quota pool)
  Tier 3 — gemini/gemini-2.5-flash          (thinking budget ON, reasoning mode)
  Tier 4 — gemini/gemini-2.5-pro            (paid, nuclear option)

Exit codes:
  0 — Build succeeded
  1 — All tiers exhausted — manual intervention required
  2 — Invalid arguments or missing spec file
"""

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
_MIN_STRATEGY_LINES: int = 80

# Tier 1: Gemini 2.5 Flash — free tier, 500 req/day, no thinking
_TIER1_MODEL: str = "gemini/gemini-2.5-flash"
# Tier 2: Gemini 2.5 Flash-Lite — free tier, separate quota pool
_TIER2_MODEL: str = "gemini/gemini-2.0-flash-lite"
# Tier 3: Gemini 2.5 Flash with thinking budget — reasoning mode, still free tier
_TIER3_MODEL: str = "gemini/gemini-2.5-flash"
_TIER3_THINKING_BUDGET: int = 8192  # tokens allocated to thinking chain
# Tier 4: Gemini 2.5 Pro — paid, final escalation (replaces Opus; GEMINI_API_KEY already in CI)
_TIER4_MODEL: str = "gemini/gemini-2.5-pro"

# Subprocess timeout (seconds) for free-tier calls.
_TIER1_SUBPROCESS_TIMEOUT: int = 60
_TIER2_SUBPROCESS_TIMEOUT: int = 60
# Tier 3 uses thinking — allow more wall time.
_TIER3_SUBPROCESS_TIMEOUT: int = 300
# Tier 4 uses Pro — slower, allow more wall time.
_TIER4_SUBPROCESS_TIMEOUT: int = 180


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
    # Load spec to include actual signal strings verbatim in the prompt.
    try:
        with spec_file.open(encoding="utf-8") as fh:
            spec_data: dict = yaml.safe_load(fh) or {}
    except Exception:
        spec_data = {}

    signals: dict = spec_data.get("signals", {})
    entry_signals: list = signals.get("entry", [])
    exit_signals: list = signals.get("exit", [])
    entry_str = " | ".join(str(s) for s in entry_signals) if entry_signals else "see spec file"
    exit_str = " | ".join(str(s) for s in exit_signals) if exit_signals else "see spec file"

    risk: dict = spec_data.get("risk_management", {})
    stop_cfg = risk.get("stop_loss", {})
    atr_period = 14  # default; spec may specify differently
    atr_mult = float(stop_cfg.get("atr_multiplier", 2.0)) if isinstance(stop_cfg, dict) else 2.0

    constraints: dict = spec_data.get("constraints", {})
    max_hold = constraints.get("max_holding_minutes", 60)

    return (
        f"Read the spec file at {spec_file} carefully. "
        f"CREATE a complete, production-quality QuantConnect LEAN algorithm "
        f"in strategies/{spec_name}/main.py that fully implements ALL signals and rules in the spec.\n\n"
        f"SPEC SIGNALS (implement these VERBATIM):\n"
        f"  Entry:  {entry_str}\n"
        f"  Exit:   {exit_str}\n\n"
        f"MANDATORY REQUIREMENTS — the build will be REJECTED if any of these are missing:\n"
        f"1. The file MUST subclass QCAlgorithm with a real Initialize() method that sets "
        f"   start date, end date, and starting capital from the spec values.\n"
        f"2. Initialize() MUST add the instrument(s) from the spec with the correct resolution.\n"
        f"3. Initialize() MUST create ALL three indicators as specified:\n"
        f"   (a) a VWAP indicator on the primary symbol,\n"
        f"   (b) an ATR indicator with period {atr_period} (ATR({atr_period})) on the primary symbol, and\n"
        f"   (c) a volume moving average (VolumeMA) — use a rolling window or SMA on the volume field.\n"
        f"4. OnData() MUST implement the EXACT entry logic above from the spec. Do NOT paraphrase it.\n"
        f"5. OnData() MUST implement the EXACT exit logic above from the spec, including the "
        f"   {max_hold}-minute max hold time, the {atr_mult}*ATR stop-loss, and VWAP reversion exit.\n"
        f"6. Close all positions at end of day (EOD) as required by the spec constraints.\n"
        f"7. The file MUST contain at least 80 non-blank, non-comment lines of real Python logic. "
        f"   A file containing only comments, stubs, `pass` statements, or `# TODO` markers "
        f"   WILL BE REJECTED by the quality gate and the build will fail.\n"
        f"8. Do NOT use `pass`, `raise NotImplementedError`, `# TODO`, `# placeholder`, or "
        f"   ellipsis (...) as the body of any method.\n"
        f"9. Also write comprehensive unit tests in tests/test_{spec_name}.py.\n"
        f"10. Do NOT modify any files outside the strategies/ and tests/ directories."
    )


def _build_aider_cmd(
    model: str,
    spec_file: Path,
    spec_name: str,
    extra_args: list[str] | None = None,
) -> list[str]:
    strategy_file = f"strategies/{spec_name}/main.py"
    test_file = f"tests/test_{spec_name}.py"
    prompt = _build_aider_prompt(spec_file, spec_name)
    cmd = [
        "aider",
        "--model", model,
        "--yes",
        "--no-git",
        "--read", "config/aider_system_prompt_with_tools.txt",
        "--read", "config/qc_tools_manifest.json",
        "--message", prompt,
        strategy_file,
        test_file,
    ]
    if extra_args:
        cmd.extend(extra_args)
    return cmd


# ---------------------------------------------------------------------------
# File hash helper (used to detect unchanged-file false-success)
# ---------------------------------------------------------------------------


def _file_sha256(path: Path) -> str | None:
    """Return the SHA-256 hex digest of *path*, or None if it does not exist."""
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Git commit helper
# ---------------------------------------------------------------------------


def _commit_and_push(
    spec_name: str,
    tier: int,
    model: str,
    pre_run_hash: str | None = None,
) -> None:
    """Stage, commit, and push the generated strategy and test files.

    Uses ``git diff --cached --quiet`` to skip the commit when there are no
    staged changes (e.g. aider wrote identical content on a retry).

    ``pre_run_hash`` is the SHA-256 of the strategy file *before* aider ran.
    If the file hash is identical to pre-run, aider made no changes — raise
    FileNotFoundError so the tier is treated as a failure and escalation occurs.
    """
    strategy_file = f"strategies/{spec_name}/main.py"
    test_file = f"tests/test_{spec_name}.py"
    strategy_path = Path(strategy_file)
    if not strategy_path.exists():
        raise FileNotFoundError(
            f"[aider_builder] Strategy file not written by Aider: {strategy_file}. "
            "Aider exited 0 but produced no output file."
        )
    real_lines = [
        ln for ln in strategy_path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    if len(real_lines) < _MIN_STRATEGY_LINES:
        raise FileNotFoundError(
            f"[aider_builder] Strategy file has only {len(real_lines)} non-blank, non-comment lines "
            f"(minimum: {_MIN_STRATEGY_LINES}). Aider produced a stub or skeleton — rejecting."
        )
    # BUG FIX: detect unchanged-file false-success.
    # If aider exited 0 but the file is byte-for-byte identical to what existed
    # before, it made no changes. Treat as a failure so the tier escalates.
    if pre_run_hash is not None:
        post_run_hash = _file_sha256(strategy_path)
        if post_run_hash == pre_run_hash:
            raise FileNotFoundError(
                f"[aider_builder] Strategy file was not modified by Aider (hash unchanged: "
                f"{pre_run_hash[:12]}\u2026). Aider exited 0 but made no edits."
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
        # Snapshot the strategy file hash BEFORE running aider so we can
        # detect an unchanged-file false-success after aider exits 0.
        strategy_path = Path(f"strategies/{spec_name}/main.py")
        pre_run_hash = _file_sha256(strategy_path)

        try:
            result = _run_aider(
                model, spec_file, spec_name, timeout=timeout, extra_args=extra_args
            )
        except subprocess.TimeoutExpired:
            return TierRunResult(False, tier_num, model, iteration + 1, "timeout", "")

        combined = result.stdout + result.stderr

        if result.success:
            try:
                _commit_and_push(spec_name, tier_num, model, pre_run_hash=pre_run_hash)
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
        strategy_path = Path(f"strategies/{spec_name}/main.py")
        pre_run_hash = _file_sha256(strategy_path)

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
                _commit_and_push(spec_name, 3, model, pre_run_hash=pre_run_hash)
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
    """Tier 4 — Gemini 2.5 Pro (paid, nuclear option).

    Final boss: no further escalation. Uses GEMINI_API_KEY already present in CI.
    On failure after all iterations the caller writes a diagnostic to
    GITHUB_STEP_SUMMARY and falls back to the stub generator.
    """
    model = _TIER4_MODEL
    last_output = ""

    for iteration in range(_MAX_ITERATIONS):
        strategy_path = Path(f"strategies/{spec_name}/main.py")
        pre_run_hash = _file_sha256(strategy_path)

        try:
            result = _run_aider(model, spec_file, spec_name, timeout=_TIER4_SUBPROCESS_TIMEOUT)
        except subprocess.TimeoutExpired:
            return TierRunResult(False, 4, model, iteration + 1, "timeout", last_output)
        last_output = result.stdout + result.stderr

        if result.success:
            try:
                _commit_and_push(spec_name, 4, model, pre_run_hash=pre_run_hash)
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

    BUG FIX: spec field paths corrected.  The previous version read from a
    non-existent ``strategy.backtesting`` / ``strategy.universe`` nesting.
    The actual spec schema has ``capital``, ``data``, ``signals``, and
    ``risk_management`` at the root level.
    """
    # --- capital ----------------------------------------------------------
    capital = spec_data.get("capital", {})
    initial_capital = int(capital.get("allocation_usd", 10000))

    # --- data / universe --------------------------------------------------
    data = spec_data.get("data", {})
    instruments = data.get("instruments", ["SPY"])
    primary_symbol = instruments[0] if instruments else "SPY"
    start_date_str: str = str(data.get("start_date", "2020-01-01"))
    end_date_str: str = str(data.get("end_date", "2024-12-31"))
    resolution_str: str = str(data.get("resolution", "daily")).lower()
    resolution_map = {
        "minute": "Resolution.Minute",
        "hour": "Resolution.Hour",
        "daily": "Resolution.Daily",
        "day": "Resolution.Daily",
        "tick": "Resolution.Tick",
        "second": "Resolution.Second",
    }
    qc_resolution = resolution_map.get(resolution_str, "Resolution.Daily")

    # --- risk_management --------------------------------------------------
    risk = spec_data.get("risk_management", {})
    stop_cfg = risk.get("stop_loss", {})
    if isinstance(stop_cfg, dict):
        # e.g. { atr_multiplier: 2.0 }  — express as fraction of price for stub
        atr_mult = float(stop_cfg.get("atr_multiplier", 2.0))
        stop_loss = round(atr_mult * 0.01, 4)  # rough proxy: 2xATR ~= 2% stop
    else:
        stop_loss = float(stop_cfg) if stop_cfg else 0.05
    take_profit = float(risk.get("take_profit", 0.10))
    leverage = float(risk.get("leverage", 1.0))
    max_position = min(leverage, 1.0)  # never exceed 100% for stub

    # --- constraints ------------------------------------------------------
    constraints = spec_data.get("constraints", {})
    max_hold_minutes = int(constraints.get("max_holding_minutes", 0))
    close_eod = bool(constraints.get("close_eod", False))

    def _date_parts(date_str: str) -> tuple[int, int, int]:
        parts = str(date_str).split("-")
        return int(parts[0]), int(parts[1]), int(parts[2])

    sy, sm, sd = _date_parts(start_date_str)
    ey, em, ed = _date_parts(end_date_str)

    class_name = "".join(w.capitalize() for w in spec_name.replace("-", "_").split("_"))

    hold_logic = ""
    if max_hold_minutes > 0:
        hold_logic = (
            f"\n    def _check_max_hold(self) -> None:\n"
            f"        'Exit if the position has been held longer than {max_hold_minutes} minutes.'\n"
            f"        if self._entry_time is None:\n"
            f"            return\n"
            f"        elapsed = (self.Time - self._entry_time).total_seconds() / 60\n"
            f"        if elapsed >= {max_hold_minutes}:\n"
            f"            self.Liquidate(self._symbol)\n"
            f"            self._entry_price = None\n"
            f"            self._entry_time = None\n"
        )

    eod_logic = ""
    if close_eod:
        eod_logic = (
            "\n    def OnEndOfDay(self, symbol) -> None:\n"
            "        'Close all positions at end of day as required by spec.'\n"
            "        self.Liquidate()\n"
            "        self._entry_price = None\n"
            "        self._entry_time = None\n"
        )

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
        equity = self.AddEquity("{primary_symbol}", {qc_resolution})
        self._symbol = equity.Symbol
        self._sma = self.SMA(self._symbol, 50, {qc_resolution})
        self._stop_loss = {stop_loss}
        self._take_profit = {take_profit}
        self._max_position = {max_position}
        self._entry_price = None
        self._entry_time = None

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
                self._entry_time = self.Time
        else:
            self._check_exit(price)
            {"self._check_max_hold()" if max_hold_minutes > 0 else ""}

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
            self._entry_time = None
{hold_logic}{eod_logic}'''

    output_path = Path("strategies") / spec_name / "main.py"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(stub_code, encoding="utf-8")
    return output_path


# ---------------------------------------------------------------------------
# Backtest metrics reader
# ---------------------------------------------------------------------------


def _read_backtest_metrics(path: Path | None = None) -> dict[str, Any]:
    """Read backtest metrics from /tmp/backtest_result.json.

    Merges ``Statistics`` and ``RuntimeStatistics`` sub-keys with a top-level
    fallback so callers get a single flat dict.  Explicit ``None`` checks are
    used internally to avoid silently discarding ``0.0`` values (e.g. a Sharpe
    Ratio of exactly zero).

    Returns an empty dict when the file is missing or contains invalid JSON.

    Args:
        path: Override the default ``/tmp/backtest_result.json`` location.
              Primarily used in tests to point at a temporary file.
    """
    if path is None:
        path = Path("/tmp/backtest_result.json")
    if not path.exists():
        return {}
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    merged: dict[str, Any] = {}
    # Populate from sub-dicts first (Statistics, then RuntimeStatistics)
    for sub_key in ("Statistics", "RuntimeStatistics"):
        sub = data.get(sub_key)
        if isinstance(sub, dict):
            merged.update(sub)
    # Top-level fallback: add keys not already populated from sub-dicts
    for k, v in data.items():
        if k not in ("Statistics", "RuntimeStatistics") and k not in merged:
            merged[k] = v

    return merged


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
        f"**Strategy file**: `strategies/{spec_name}/main.py`\n",
        f"**Test file**: `tests/test_{spec_name}.py`\n",
    ]
    if not success and failure_details:
        lines.append(f"\n### Failure Diagnostic\n\n```\n{failure_details}\n```\n")
    metrics = _read_backtest_metrics()
    sharpe = metrics.get("Sharpe Ratio")
    total_return = metrics.get("Total Return")
    max_drawdown = metrics.get("Max Drawdown")
    if sharpe is not None or total_return is not None or max_drawdown is not None:
        lines.append("\n### Backtest Metrics\n\n")
        lines.append("| Metric | Value |\n|--------|-------|\n")
        if sharpe is not None:
            lines.append(f"| Sharpe Ratio | {sharpe} |\n")
        if total_return is not None:
            lines.append(f"| Total Return | {total_return} |\n")
        if max_drawdown is not None:
            lines.append(f"| Max Drawdown | {max_drawdown} |\n")
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

    # Pre-create the strategy directory so aider has a target directory
    strategy_dir = Path("strategies") / spec_name
    strategy_dir.mkdir(parents=True, exist_ok=True)
    # DO NOT pre-create main.py — aider must create it fresh so hash comparison works

    # Only pre-create the test stub — Aider needs an edit target for tests
    test_stub = Path("tests") / f"test_{spec_name}.py"
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
        "[aider_builder] FATAL: All 4 tiers exhausted — no real strategy was produced.",
        file=sys.stderr,
    )
    print(
        "[aider_builder] The pipeline MUST fail. A stub strategy is NOT a valid build outcome.",
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
            "All 4 model tiers were exhausted. No real strategy was produced.\n"
            "The build has FAILED. Manual intervention is required."
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
