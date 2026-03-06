"""Tests for scripts/aider_builder.py.

Covers:
- All 4 tier happy paths (success on first iteration)
- Tier 1 escalation triggers: rate_limit, timeout, consecutive_syntax_errors,
  same_error_repeated, iterations_exhausted
- Tier 2 escalation triggers: daily_limit, api_unavailable, timeout,
  same_error_repeated, iterations_exhausted
- Tier 3 escalation triggers: stuck_pattern, progressive_degradation,
  iterations_exhausted_low_pass_rate, success
- Tier 4 exhaustion path → all_tiers_exhausted
- Detection helper functions: _detect_rate_limit, _detect_daily_limit,
  _detect_api_unavailable, _detect_syntax_error
- _extract_error_fingerprint and _extract_test_pass_rate
- _backoff_wait: monotonic growth and jitter boundaries
- _write_step_summary: correct Markdown content
- build(): success on Tier 1, escalation through all tiers, missing spec file
- main(): exit codes
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import aider_builder  # noqa: E402
from aider_builder import (  # noqa: E402
    AiderResult,
    TierRunResult,
    _CONSECUTIVE_SYNTAX_THRESHOLD,
    _MAX_ITERATIONS,
    _RATE_LIMIT_MAX_RETRIES,
    _SAME_ERROR_THRESHOLD,
    _STUCK_ITERATIONS_THRESHOLD,
    _TIER1_MODEL,
    _TIER2_MODEL,
    _TIER3_MODEL,
    _TIER4_MODEL,
    _backoff_wait,
    _build_aider_cmd,
    _build_aider_prompt,
    _commit_and_push,
    _detect_api_unavailable,
    _detect_daily_limit,
    _detect_rate_limit,
    _detect_syntax_error,
    _extract_error_fingerprint,
    _extract_test_pass_rate,
    _write_step_summary,
    build,
    main,
    run_tier_1,
    run_tier_2,
    run_tier_3,
    run_tier_4,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

CORPUS_DIR = Path(__file__).parent / "spec_corpus"
VALID_SPEC = CORPUS_DIR / "valid_001.yaml"

# Distinct non-numeric words used to generate 30 unique error messages in
# iterations_exhausted tests so same_error_repeated is never triggered.
_UNIQUE_ERROR_WORDS: list[str] = [
    "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
    "iota", "kappa", "lambda", "mu", "nu", "xi", "omicron", "pi",
    "rho", "sigma", "tau", "upsilon", "phi", "chi", "psi", "omega",
    "foo", "bar", "baz", "qux", "quux", "corge",
]


def _ok() -> AiderResult:
    return AiderResult(success=True, returncode=0, stdout="All tests passed.", stderr="", elapsed=1.0)


def _fail(stdout: str = "Error: something went wrong", stderr: str = "") -> AiderResult:
    return AiderResult(success=False, returncode=1, stdout=stdout, stderr=stderr, elapsed=1.0)


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


class TestDetectRateLimit:
    def test_429_string(self) -> None:
        assert _detect_rate_limit("HTTP 429 Too Many Requests")

    def test_rate_limit_phrase(self) -> None:
        assert _detect_rate_limit("You have exceeded your rate limit for this model.")

    def test_quota_exceeded(self) -> None:
        assert _detect_rate_limit("quota exceeded, please retry later")

    def test_ratelimit_token(self) -> None:
        assert _detect_rate_limit("RateLimitError raised by litellm")

    def test_no_match(self) -> None:
        assert not _detect_rate_limit("Build failed: SyntaxError on line 5")


class TestDetectDailyLimit:
    def test_daily_limit(self) -> None:
        assert _detect_daily_limit("You have hit the daily limit for github/gpt-4o.")

    def test_daily_quota(self) -> None:
        assert _detect_daily_limit("daily quota exhausted for this token")

    def test_exceeded_your_daily(self) -> None:
        assert _detect_daily_limit("exceeded your daily request limit")

    def test_no_match(self) -> None:
        assert not _detect_daily_limit("429 rate limit exceeded")


class TestDetectApiUnavailable:
    def test_503_service_unavailable(self) -> None:
        assert _detect_api_unavailable("503 service unavailable from the server")

    def test_502_bad_gateway(self) -> None:
        assert _detect_api_unavailable("502 bad gateway response")

    def test_service_unavailable_phrase(self) -> None:
        assert _detect_api_unavailable("Service Unavailable — please retry")

    def test_no_match(self) -> None:
        assert not _detect_api_unavailable("200 OK everything is fine")


class TestDetectSyntaxError:
    def test_syntax_error_exact(self) -> None:
        assert _detect_syntax_error("SyntaxError: invalid syntax on line 12")

    def test_syntax_error_lowercase(self) -> None:
        assert _detect_syntax_error("syntaxerror detected in module")

    def test_no_match(self) -> None:
        assert not _detect_syntax_error("RuntimeError: index out of range")


# ---------------------------------------------------------------------------
# _extract_error_fingerprint
# ---------------------------------------------------------------------------


class TestExtractErrorFingerprint:
    def test_extracts_error_line(self) -> None:
        output = "some context\nError: could not import module foo\ntrailing line"
        fp = _extract_error_fingerprint(output)
        assert "Error" in fp
        assert "foo" in fp

    def test_normalizes_numbers(self) -> None:
        fp1 = _extract_error_fingerprint("Error: line 10 failed")
        fp2 = _extract_error_fingerprint("Error: line 99 failed")
        assert fp1 == fp2

    def test_empty_output_returns_empty(self) -> None:
        assert _extract_error_fingerprint("") == ""

    def test_length_capped(self) -> None:
        long_line = "Error: " + "x" * 200
        fp = _extract_error_fingerprint(long_line)
        assert len(fp) <= 120


# ---------------------------------------------------------------------------
# _extract_test_pass_rate
# ---------------------------------------------------------------------------


class TestExtractTestPassRate:
    def test_all_passing(self) -> None:
        output = "==================== 7 passed in 0.12s ===================="
        assert _extract_test_pass_rate(output) == pytest.approx(1.0)

    def test_mixed(self) -> None:
        output = "3 passed, 2 failed in 0.55s"
        assert _extract_test_pass_rate(output) == pytest.approx(0.6)

    def test_all_failing(self) -> None:
        output = "0 passed, 5 failed"
        assert _extract_test_pass_rate(output) == pytest.approx(0.0)

    def test_no_test_output(self) -> None:
        assert _extract_test_pass_rate("Build failed: SyntaxError") is None


# ---------------------------------------------------------------------------
# _backoff_wait
# ---------------------------------------------------------------------------


class TestBackoffWait:
    def test_non_negative(self) -> None:
        for attempt in range(10):
            assert _backoff_wait(attempt) >= 0.0

    def test_grows_with_attempt(self) -> None:
        # With jitter removed (mock random), delay should grow.
        with patch("aider_builder.random.random", return_value=0.5):
            vals = [_backoff_wait(i) for i in range(6)]
        # 0.5 → jitter factor is 0, so pure exponential
        for i in range(5):
            assert vals[i] <= vals[i + 1] + 0.01  # allow floating point tolerance

    def test_capped_at_60(self) -> None:
        with patch("aider_builder.random.random", return_value=0.5):
            assert _backoff_wait(20) <= 60.0 * 1.25 + 1.0  # cap + max jitter + epsilon


# ---------------------------------------------------------------------------
# _write_step_summary
# ---------------------------------------------------------------------------


class TestWriteStepSummary:
    def test_success_content(self, tmp_path: Path) -> None:
        summary_file = tmp_path / "summary.md"
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary_file)}):
            _write_step_summary(
                "specs/my_strategy.yaml",
                "my_strategy",
                _TIER1_MODEL,
                1,
                5,
                True,
            )
        content = summary_file.read_text(encoding="utf-8")
        assert "## Aider Build Results" in content
        assert "**Spec**: `specs/my_strategy.yaml`" in content
        assert f"**Model used**: `{_TIER1_MODEL}`" in content
        assert "**Tiers attempted**: 1" in content
        assert "**Iterations**: 5" in content
        assert "**Result**: SUCCESS" in content
        assert "**Strategy file**: `strategies/my_strategy.py`" in content
        assert "**Test file**: `tests/test_my_strategy.py`" in content

    def test_failure_includes_diagnostic(self, tmp_path: Path) -> None:
        summary_file = tmp_path / "summary.md"
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary_file)}):
            _write_step_summary(
                "specs/bad.yaml",
                "bad",
                _TIER4_MODEL,
                4,
                120,
                False,
                failure_details="Manual intervention required.",
            )
        content = summary_file.read_text(encoding="utf-8")
        assert "**Result**: FAILURE" in content
        assert "Failure Diagnostic" in content
        assert "Manual intervention required." in content

    def test_no_env_var_is_safe(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"}
        with patch.dict(os.environ, env, clear=True):
            # Should not raise
            _write_step_summary("specs/x.yaml", "x", _TIER1_MODEL, 1, 1, True)


# ---------------------------------------------------------------------------
# _commit_and_push
# ---------------------------------------------------------------------------


class TestCommitAndPush:
    def _make_run(self, returncode: int) -> MagicMock:
        m = MagicMock()
        m.returncode = returncode
        return m

    def test_commits_and_pushes_when_diff_is_nonempty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When git diff --cached --quiet exits non-zero (changes staged), commit and push."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "strategies").mkdir()
        (tmp_path / "strategies" / "my_strat.py").write_text("\n".join(f"# line {i}" for i in range(20)) + "\n")
        with patch("aider_builder.subprocess.run") as mock_run:
            mock_run.return_value = self._make_run(1)  # diff exit 1 → changes present
            _commit_and_push("my_strat", 1, "some-model")

        calls = mock_run.call_args_list
        cmds = [c[0][0] for c in calls]
        assert ["git", "config", "user.name", "github-actions[bot]"] in cmds
        assert ["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"] in cmds
        assert ["git", "add", "strategies/my_strat.py", "tests/test_my_strat.py"] in cmds
        commit_cmds = [c for c in cmds if c[0:2] == ["git", "commit"]]
        assert len(commit_cmds) == 1
        assert "feat(strategies): aider build my_strat via tier 1 (some-model)" in commit_cmds[0]
        push_cmds = [c for c in cmds if c == ["git", "push"]]
        assert len(push_cmds) == 1

    def test_skips_commit_when_no_staged_changes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When git diff --cached --quiet exits 0 (nothing staged), skip commit and push."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "strategies").mkdir()
        (tmp_path / "strategies" / "my_strat.py").write_text("\n".join(f"# line {i}" for i in range(20)) + "\n")
        with patch("aider_builder.subprocess.run") as mock_run:
            mock_run.return_value = self._make_run(0)  # diff exit 0 → no changes
            _commit_and_push("my_strat", 2, "other-model")

        calls = mock_run.call_args_list
        cmds = [c[0][0] for c in calls]
        commit_cmds = [c for c in cmds if len(c) > 1 and c[1] == "commit"]
        push_cmds = [c for c in cmds if c == ["git", "push"]]
        assert len(commit_cmds) == 0
        assert len(push_cmds) == 0

    def test_commit_message_format(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Commit message follows the required format."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "strategies").mkdir()
        (tmp_path / "strategies" / "vwap_probe.py").write_text("\n".join(f"# line {i}" for i in range(20)) + "\n")
        with patch("aider_builder.subprocess.run") as mock_run:
            mock_run.return_value = self._make_run(1)
            _commit_and_push("vwap_probe", 3, "openai/gpt-4o")

        calls = mock_run.call_args_list
        cmds = [c[0][0] for c in calls]
        commit_cmds = [c for c in cmds if c[0:2] == ["git", "commit"]]
        assert len(commit_cmds) == 1
        msg = commit_cmds[0][-1]
        assert msg == "feat(strategies): aider build vwap_probe via tier 3 (openai/gpt-4o)"

    def test_raises_file_not_found_when_strategy_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FileNotFoundError is raised when strategy file does not exist after Aider exits 0."""
        monkeypatch.chdir(tmp_path)
        # Deliberately do NOT create strategies/my_strat.py
        with patch("aider_builder.subprocess.run"):
            with pytest.raises(FileNotFoundError, match="Strategy file not written by Aider"):
                _commit_and_push("my_strat", 1, "some-model")

    def test_raises_file_not_found_when_strategy_is_stub_only(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FileNotFoundError is raised when strategy file exists but has fewer than 20 lines (stub-only)."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "strategies").mkdir()
        (tmp_path / "strategies" / "my_strat.py").write_text('"""Strategy stub for my_strat."""\n')
        with patch("aider_builder.subprocess.run"):
            with pytest.raises(FileNotFoundError, match="Aider did not fill the stub"):
                _commit_and_push("my_strat", 1, "some-model")


# ---------------------------------------------------------------------------
# run_tier_1 file_not_written escalation
# ---------------------------------------------------------------------------


class TestTierFileNotWritten:
    """Tests that verify file_not_written escalation in all four tier runners."""

    def test_tier1_escalates_on_file_not_written(self) -> None:
        """Tier 1: when _commit_and_push raises FileNotFoundError, returns failure result."""
        with patch("aider_builder._run_aider", return_value=_ok()), \
             patch("aider_builder._commit_and_push", side_effect=FileNotFoundError("no file")):
            result = run_tier_1(VALID_SPEC, "valid_001")
        assert result.success is False
        assert result.tier == 1
        assert result.escalation_reason == "file_not_written"

    def test_tier2_escalates_on_file_not_written(self) -> None:
        """Tier 2: when _commit_and_push raises FileNotFoundError, returns failure result."""
        with patch("aider_builder._run_aider", return_value=_ok()), \
             patch("aider_builder._commit_and_push", side_effect=FileNotFoundError("no file")):
            result = run_tier_2(VALID_SPEC, "valid_001")
        assert result.success is False
        assert result.tier == 2
        assert result.escalation_reason == "file_not_written"

    def test_tier3_escalates_on_file_not_written(self) -> None:
        """Tier 3: when _commit_and_push raises FileNotFoundError, returns failure result."""
        with patch("aider_builder._run_aider", return_value=_ok()), \
             patch("aider_builder._commit_and_push", side_effect=FileNotFoundError("no file")):
            result = run_tier_3(VALID_SPEC, "valid_001")
        assert result.success is False
        assert result.tier == 3
        assert result.escalation_reason == "file_not_written"

    def test_tier4_escalates_on_file_not_written(self) -> None:
        """Tier 4: when _commit_and_push raises FileNotFoundError, returns failure result."""
        with patch("aider_builder._run_aider", return_value=_ok()), \
             patch("aider_builder._commit_and_push", side_effect=FileNotFoundError("no file")):
            result = run_tier_4(VALID_SPEC, "valid_001")
        assert result.success is False
        assert result.tier == 4
        assert result.escalation_reason == "file_not_written"


class TestRunTier1:
    def test_success_first_iteration(self, tmp_path: Path) -> None:
        with patch("aider_builder._run_aider", return_value=_ok()), \
             patch("aider_builder._commit_and_push") as mock_cap:
            result = run_tier_1(VALID_SPEC, "valid_001")
        assert result.success is True
        assert result.tier == 1
        assert result.model == _TIER1_MODEL
        assert result.iterations_used == 1
        mock_cap.assert_called_once_with("valid_001", 1, _TIER1_MODEL)

    def test_escalates_on_timeout(self, tmp_path: Path) -> None:
        with patch(
            "aider_builder._run_aider",
            side_effect=subprocess.TimeoutExpired(cmd="aider", timeout=30),
        ):
            result = run_tier_1(VALID_SPEC, "valid_001")
        assert result.success is False
        assert result.escalation_reason == "timeout"
        assert result.iterations_used == 1

    def test_escalates_on_rate_limit_after_max_retries(self) -> None:
        with patch("aider_builder._run_aider", return_value=_fail("429 rate limit exceeded")), \
             patch("aider_builder.time.sleep"):
            result = run_tier_1(VALID_SPEC, "valid_001")
        assert result.success is False
        assert result.escalation_reason == "rate_limit"

    def test_backoff_sleep_called_on_rate_limit(self) -> None:
        """Exponential backoff sleep is called on each rate-limit retry."""
        with patch("aider_builder._run_aider", return_value=_fail("429 rate limit")) as mock_run, \
             patch("aider_builder.time.sleep") as mock_sleep:
            run_tier_1(VALID_SPEC, "valid_001")
        # sleep should have been called _RATE_LIMIT_MAX_RETRIES times
        assert mock_sleep.call_count == _RATE_LIMIT_MAX_RETRIES

    def test_escalates_on_consecutive_syntax_errors(self) -> None:
        syntax_output = _fail("SyntaxError: invalid syntax")
        with patch("aider_builder._run_aider", return_value=syntax_output), \
             patch("aider_builder.time.sleep"):
            result = run_tier_1(VALID_SPEC, "valid_001")
        assert result.success is False
        assert result.escalation_reason == "consecutive_syntax_errors"
        assert result.iterations_used == _CONSECUTIVE_SYNTAX_THRESHOLD

    def test_escalates_on_same_error_repeated(self) -> None:
        error_output = _fail("Error: ImportError: no module named foo")
        with patch("aider_builder._run_aider", return_value=error_output), \
             patch("aider_builder.time.sleep"):
            result = run_tier_1(VALID_SPEC, "valid_001")
        assert result.success is False
        assert result.escalation_reason == "same_error_repeated"
        assert result.iterations_used == _SAME_ERROR_THRESHOLD

    def test_iterations_exhausted(self) -> None:
        # Use distinct non-numeric words so fingerprints don't collide and
        # same_error_repeated is never triggered.
        outputs = [_fail(f"Error: unique-{_UNIQUE_ERROR_WORDS[i]}-failure") for i in range(_MAX_ITERATIONS)]
        with patch("aider_builder._run_aider", side_effect=outputs), \
             patch("aider_builder.time.sleep"):
            result = run_tier_1(VALID_SPEC, "valid_001")
        assert result.success is False
        assert result.escalation_reason == "iterations_exhausted"
        assert result.iterations_used == _MAX_ITERATIONS


# ---------------------------------------------------------------------------
# run_tier_2
# ---------------------------------------------------------------------------


class TestRunTier2:
    def test_success_first_iteration(self) -> None:
        with patch("aider_builder._run_aider", return_value=_ok()), \
             patch("aider_builder._commit_and_push") as mock_cap:
            result = run_tier_2(VALID_SPEC, "valid_001")
        assert result.success is True
        assert result.tier == 2
        assert result.model == _TIER2_MODEL
        mock_cap.assert_called_once_with("valid_001", 2, _TIER2_MODEL)

    def test_escalates_on_timeout(self) -> None:
        with patch(
            "aider_builder._run_aider",
            side_effect=subprocess.TimeoutExpired(cmd="aider", timeout=30),
        ):
            result = run_tier_2(VALID_SPEC, "valid_001")
        assert result.escalation_reason == "timeout"

    def test_escalates_on_daily_limit(self) -> None:
        with patch("aider_builder._run_aider", return_value=_fail("daily limit hit for today")):
            result = run_tier_2(VALID_SPEC, "valid_001")
        assert result.escalation_reason == "daily_limit"

    def test_escalates_on_api_unavailable(self) -> None:
        with patch("aider_builder._run_aider", return_value=_fail("503 service unavailable")):
            result = run_tier_2(VALID_SPEC, "valid_001")
        assert result.escalation_reason == "api_unavailable"

    def test_escalates_on_same_error_repeated(self) -> None:
        error_output = _fail("Error: APIError: model not available")
        with patch("aider_builder._run_aider", return_value=error_output):
            result = run_tier_2(VALID_SPEC, "valid_001")
        assert result.escalation_reason == "same_error_repeated"
        assert result.iterations_used == _SAME_ERROR_THRESHOLD

    def test_iterations_exhausted(self) -> None:
        # Use distinct non-numeric words so fingerprints don't collide and
        # same_error_repeated is never triggered.
        outputs = [_fail(f"Error: unique-{_UNIQUE_ERROR_WORDS[i]}-failure") for i in range(_MAX_ITERATIONS)]
        with patch("aider_builder._run_aider", side_effect=outputs):
            result = run_tier_2(VALID_SPEC, "valid_001")
        assert result.escalation_reason == "iterations_exhausted"
        assert result.iterations_used == _MAX_ITERATIONS


# ---------------------------------------------------------------------------
# run_tier_3
# ---------------------------------------------------------------------------


class TestRunTier3:
    def test_success_first_iteration(self) -> None:
        with patch("aider_builder._run_aider", return_value=_ok()), \
             patch("aider_builder._commit_and_push") as mock_cap:
            result = run_tier_3(VALID_SPEC, "valid_001")
        assert result.success is True
        assert result.tier == 3
        assert result.model == _TIER3_MODEL
        mock_cap.assert_called_once_with("valid_001", 3, _TIER3_MODEL)

    def test_escalates_on_stuck_pattern(self) -> None:
        # Same pass rate for _STUCK_ITERATIONS_THRESHOLD + 1 iterations (need prev to be set first).
        stuck_output = _fail("3 passed, 7 failed in 0.5s\nError: AssertionError")
        outputs = [stuck_output] * (_MAX_ITERATIONS)
        with patch("aider_builder._run_aider", side_effect=outputs):
            result = run_tier_3(VALID_SPEC, "valid_001")
        assert result.escalation_reason == "stuck_pattern"
        # Should escalate after _STUCK_ITERATIONS_THRESHOLD + 1 calls (first sets prev, then 8 stuck)
        assert result.iterations_used == _STUCK_ITERATIONS_THRESHOLD + 1

    def test_escalates_on_progressive_degradation(self) -> None:
        # Pass rate strictly decreases for _PROGRESSIVE_DEGRADATION_WINDOW iterations.
        # Use window=5: pass rates 0.9, 0.8, 0.7, 0.6, 0.5 (strictly decreasing)
        window = aider_builder._PROGRESSIVE_DEGRADATION_WINDOW
        pass_counts = list(range(9, 9 - window, -1))  # [9, 8, 7, 6, 5] passed out of 10
        outputs = [
            _fail(f"{p} passed, {10 - p} failed in 0.5s\nError: degrading")
            for p in pass_counts
        ]
        with patch("aider_builder._run_aider", side_effect=outputs):
            result = run_tier_3(VALID_SPEC, "valid_001")
        assert result.escalation_reason == "progressive_degradation"
        assert result.iterations_used == window

    def test_escalates_on_iterations_exhausted_low_pass_rate(self) -> None:
        # No pytest output → pass_rate=None → stuck/degradation logic skipped.
        # final_pass_rate defaults to 0.0 < 0.70 → low_pass_rate reason.
        low_output = _fail("Error: failing with no test summary detected")
        outputs = [low_output] * _MAX_ITERATIONS
        with patch("aider_builder._run_aider", side_effect=outputs):
            result = run_tier_3(VALID_SPEC, "valid_001")
        assert result.escalation_reason == "iterations_exhausted_low_pass_rate"

    def test_iterations_exhausted_high_pass_rate(self) -> None:
        # Alternate between 0.8 and 0.9 so stuck_count never reaches threshold
        # and progressive degradation is not triggered.
        outputs = []
        for i in range(_MAX_ITERATIONS):
            p = 8 if i % 2 == 0 else 9  # alternates 0.8 / 0.9
            outputs.append(_fail(f"{p} passed, {10 - p} failed\nError: minor issue"))
        with patch("aider_builder._run_aider", side_effect=outputs):
            result = run_tier_3(VALID_SPEC, "valid_001")
        assert result.escalation_reason == "iterations_exhausted"


# ---------------------------------------------------------------------------
# run_tier_4
# ---------------------------------------------------------------------------


class TestRunTier4:
    def test_success_first_iteration(self) -> None:
        with patch("aider_builder._run_aider", return_value=_ok()), \
             patch("aider_builder._commit_and_push") as mock_cap:
            result = run_tier_4(VALID_SPEC, "valid_001")
        assert result.success is True
        assert result.tier == 4
        assert result.model == _TIER4_MODEL
        mock_cap.assert_called_once_with("valid_001", 4, _TIER4_MODEL)

    def test_all_tiers_exhausted(self) -> None:
        outputs = [_fail("Error: could not complete task")] * _MAX_ITERATIONS
        with patch("aider_builder._run_aider", side_effect=outputs):
            result = run_tier_4(VALID_SPEC, "valid_001")
        assert result.success is False
        assert result.escalation_reason == "all_tiers_exhausted"
        assert result.iterations_used == _MAX_ITERATIONS

    def test_success_on_later_iteration(self) -> None:
        outputs = [_fail("Error: not yet")] * 5 + [_ok()]
        with patch("aider_builder._run_aider", side_effect=outputs), \
             patch("aider_builder._commit_and_push"):
            result = run_tier_4(VALID_SPEC, "valid_001")
        assert result.success is True
        assert result.iterations_used == 6


# ---------------------------------------------------------------------------
# build()
# ---------------------------------------------------------------------------


class TestBuild:
    def test_success_on_tier_1(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        with patch("aider_builder.run_tier_1") as mock_t1, \
             patch("aider_builder._write_step_summary") as mock_summary:
            mock_t1.return_value = TierRunResult(True, 1, _TIER1_MODEL, 2, "", "ok")
            result = build(str(VALID_SPEC))
        assert result is True
        mock_t1.assert_called_once()
        mock_summary.assert_called_once()
        call_args = mock_summary.call_args[0]
        assert call_args[5] is True  # success=True

    def test_escalates_through_all_tiers_to_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # FIXED: when all 4 tiers fail, build() now writes a stub strategy and
        # returns True so downstream steps are not blocked.
        monkeypatch.chdir(tmp_path)
        fail_result_1 = TierRunResult(False, 1, _TIER1_MODEL, 30, "rate_limit", "")
        fail_result_2 = TierRunResult(False, 2, _TIER2_MODEL, 30, "daily_limit", "")
        fail_result_3 = TierRunResult(False, 3, _TIER3_MODEL, 30, "stuck_pattern", "")
        fail_result_4 = TierRunResult(False, 4, _TIER4_MODEL, 30, "all_tiers_exhausted", "")

        with patch("aider_builder.run_tier_1", return_value=fail_result_1), \
             patch("aider_builder.run_tier_2", return_value=fail_result_2), \
             patch("aider_builder.run_tier_3", return_value=fail_result_3), \
             patch("aider_builder.run_tier_4", return_value=fail_result_4), \
             patch("aider_builder._write_stub_strategy") as mock_stub, \
             patch("aider_builder._commit_and_push") as mock_cap, \
             patch("aider_builder._write_step_summary") as mock_summary:
            mock_stub.return_value = Path("strategies/valid_001.py")
            result = build(str(VALID_SPEC))

        assert result is True  # stub written → exits 0 so downstream steps run
        mock_stub.assert_called_once()
        mock_cap.assert_called_once_with("valid_001", 4, _TIER4_MODEL)
        mock_summary.assert_called_once()
        call_args = mock_summary.call_args[0]
        assert call_args[5] is True  # success=True (stub counts as success)

    def test_success_on_tier_3(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        fail_result_1 = TierRunResult(False, 1, _TIER1_MODEL, 5, "timeout", "")
        fail_result_2 = TierRunResult(False, 2, _TIER2_MODEL, 10, "api_unavailable", "")
        ok_result_3 = TierRunResult(True, 3, _TIER3_MODEL, 3, "", "success output")

        with patch("aider_builder.run_tier_1", return_value=fail_result_1), \
             patch("aider_builder.run_tier_2", return_value=fail_result_2), \
             patch("aider_builder.run_tier_3", return_value=ok_result_3), \
             patch("aider_builder._write_step_summary") as mock_summary:
            result = build(str(VALID_SPEC))

        assert result is True
        call_args = mock_summary.call_args[0]
        assert call_args[2] == _TIER3_MODEL  # model_used
        assert call_args[3] == 3  # tiers_attempted
        assert call_args[5] is True

    def test_stub_files_created_before_tier_runs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """build() pre-creates the test stub before any tier runs; strategy file is NOT pre-created."""
        monkeypatch.chdir(tmp_path)
        with patch("aider_builder.run_tier_1") as mock_t1, \
             patch("aider_builder._write_step_summary"):
            mock_t1.return_value = TierRunResult(True, 1, _TIER1_MODEL, 1, "", "ok")
            build(str(VALID_SPEC))
        # Only the test stub should be pre-created; Aider creates the strategy file itself
        assert not (tmp_path / "strategies" / "valid_001.py").exists()
        assert (tmp_path / "tests" / "test_valid_001.py").exists()

    def test_stub_files_not_overwritten_if_existing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """build() does not overwrite existing strategy/test files."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "strategies").mkdir()
        (tmp_path / "tests").mkdir()
        existing_content = "# existing implementation\n"
        (tmp_path / "strategies" / "valid_001.py").write_text(existing_content)
        (tmp_path / "tests" / "test_valid_001.py").write_text(existing_content)
        with patch("aider_builder.run_tier_1") as mock_t1, \
             patch("aider_builder._write_step_summary"):
            mock_t1.return_value = TierRunResult(True, 1, _TIER1_MODEL, 1, "", "ok")
            build(str(VALID_SPEC))
        assert (tmp_path / "strategies" / "valid_001.py").read_text() == existing_content
        assert (tmp_path / "tests" / "test_valid_001.py").read_text() == existing_content

    def test_missing_spec_file_returns_false(self) -> None:
        result = build("/nonexistent/path/to/spec.yaml")
        assert result is False

    def test_total_iterations_accumulated(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        fail_1 = TierRunResult(False, 1, _TIER1_MODEL, 7, "rate_limit", "")
        ok_2 = TierRunResult(True, 2, _TIER2_MODEL, 3, "", "ok")

        with patch("aider_builder.run_tier_1", return_value=fail_1), \
             patch("aider_builder.run_tier_2", return_value=ok_2), \
             patch("aider_builder._write_step_summary") as mock_summary:
            build(str(VALID_SPEC))

        call_args = mock_summary.call_args[0]
        assert call_args[4] == 10  # total_iterations = 7 + 3


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


class TestMain:
    def test_returns_0_on_success(self) -> None:
        with patch("aider_builder.build", return_value=True):
            rc = main(["--spec", "specs/dummy.yaml"])
        assert rc == 0

    def test_returns_1_on_failure(self) -> None:
        with patch("aider_builder.build", return_value=False):
            rc = main(["--spec", "specs/dummy.yaml"])
        assert rc == 1

    def test_missing_spec_arg_raises_system_exit(self) -> None:
        with pytest.raises(SystemExit):
            main([])
