"""E2E smoke tests for the QSC pipeline.

Covers the four core scripts end-to-end using minimal fixtures:
  - parse_prompts.py
  - qc_quick_validate.py
  - log_grinder_result.py
  - generate_grinder_summary.py

No external API calls, no Aider, no Gemini.
All tests use only stdlib + pytest.
"""

import json
import importlib
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import generate_grinder_summary  # noqa: E402
import log_grinder_result  # noqa: E402
import parse_prompts  # noqa: E402
import qc_quick_validate  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures
# ─────────────────────────────────────────────────────────────────────────────

SMOKE_QUEUE = """\
## [PRIORITY] Base Strategy

Build a simple SPY buy-and-hold strategy.

## [IF-PREVIOUS-PASSED] Enhanced Strategy

Take Base Strategy and add a 50-day moving average filter.

## [INDEPENDENT] Gap Fade

Build a gap-fade strategy on SPY.
"""

VALID_STRATEGY = """\
from AlgorithmImports import *

class TestAlgorithm(QCAlgorithm):
    def initialize(self) -> None:
        self.set_start_date(2020, 1, 1)
        self.set_end_date(2023, 12, 31)
        self.set_cash(100000)
        self.symbol = self.add_equity("SPY", Resolution.DAILY).symbol

    def on_data(self, data):
        if not self.portfolio[self.symbol].invested:
            self.market_order(self.symbol, 10)
"""

ANTI_SELF_ALGORITHM = """\
from AlgorithmImports import *

class BadAlgorithm(QCAlgorithm):
    def initialize(self) -> None:
        self.symbol = self.add_equity("SPY", Resolution.DAILY).symbol

    def on_data(self, data):
        self.algorithm.stop_market_order(self.symbol, -10, 400)
"""

ANTI_ALGORITHM_PORTFOLIO = """\
from AlgorithmImports import *

class BadAlgorithm(QCAlgorithm):
    def initialize(self) -> None:
        self.symbol = self.add_equity("SPY", Resolution.DAILY).symbol

    def on_data(self, data):
        for holding in algorithm.portfolio.values():
            pass
"""


# ─────────────────────────────────────────────────────────────────────────────
# 1. parse_prompts smoke tests
# ─────────────────────────────────────────────────────────────────────────────


class TestParsePromptsSmoke:
    """E2E tests for parse_prompts.parse_queue_file using a 3-prompt fixture."""

    def _queue_file(self, tmp_path: Path) -> Path:
        f = tmp_path / "queue.md"
        f.write_text(SMOKE_QUEUE)
        return f

    def test_returns_three_prompts(self, tmp_path):
        prompts = parse_prompts.parse_queue_file(self._queue_file(tmp_path))
        assert len(prompts) == 3

    def test_priority_prompt_fields(self, tmp_path):
        prompts = parse_prompts.parse_queue_file(self._queue_file(tmp_path))
        priority = next(p for p in prompts if p["priority"] == "PRIORITY")
        assert priority["title"] == "Base Strategy"
        assert priority["depends_on"] is None
        assert "SPY" in priority["content"]

    def test_conditional_prompt_depends_on(self, tmp_path):
        """[IF-PREVIOUS-PASSED] prompt must have depends_on set to the parent title."""
        prompts = parse_prompts.parse_queue_file(self._queue_file(tmp_path))
        conditional = next(p for p in prompts if p["priority"] == "IF-PREVIOUS-PASSED")
        assert conditional["title"] == "Enhanced Strategy"
        assert conditional["depends_on"] == "Base Strategy"

    def test_independent_prompt_fields(self, tmp_path):
        prompts = parse_prompts.parse_queue_file(self._queue_file(tmp_path))
        independent = next(p for p in prompts if p["priority"] == "INDEPENDENT")
        assert independent["title"] == "Gap Fade"
        assert independent["depends_on"] is None

    def test_split_counts(self, tmp_path):
        prompts = parse_prompts.parse_queue_file(self._queue_file(tmp_path))
        split = parse_prompts.split_by_priority(prompts)
        assert len(split["priority_prompts"]) == 1
        assert len(split["conditional_prompts"]) == 1
        assert len(split["independent_prompts"]) == 1
        assert len(split["low_priority_prompts"]) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 2. qc_quick_validate smoke tests (exit-code level)
# ─────────────────────────────────────────────────────────────────────────────


class TestQcQuickValidateSmoke:
    """E2E tests that call qc_quick_validate as a subprocess to verify exit codes."""

    def _write(self, tmp_path: Path, name: str, content: str) -> Path:
        f = tmp_path / name
        f.write_text(content)
        return f

    def _run(self, path: Path) -> int:
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "qc_quick_validate.py"), str(path)],
            capture_output=True,
        )
        return result.returncode

    def test_valid_strategy_exit_0(self, tmp_path):
        f = self._write(tmp_path, "valid.py", VALID_STRATEGY)
        assert self._run(f) == 0

    def test_self_algorithm_exit_1(self, tmp_path):
        """self.algorithm.xxx is a hallucinated pattern — must fail with exit code 1."""
        f = self._write(tmp_path, "bad_self.py", ANTI_SELF_ALGORITHM)
        assert self._run(f) == 1

    def test_algorithm_portfolio_exit_1(self, tmp_path):
        """algorithm.portfolio.xxx is a hallucinated pattern — must fail with exit code 1."""
        f = self._write(tmp_path, "bad_portfolio.py", ANTI_ALGORITHM_PORTFOLIO)
        assert self._run(f) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 3. log_grinder_result smoke tests
# ─────────────────────────────────────────────────────────────────────────────


class TestLogGrinderResultSmoke:
    """E2E tests for log_grinder_result.main using mock argv (no real QC result file)."""

    def test_creates_jsonl_with_expected_fields(self, tmp_path):
        output_file = tmp_path / "output" / "grinder_results.jsonl"
        test_args = [
            "log_grinder_result.py",
            "--prompt", "Build a simple buy-and-hold strategy",
            "--name", "base_strategy",
            "--aider", "success",
            "--validate", "success",
            "--priority", "PRIORITY",
            "--output", str(output_file),
        ]
        with patch("sys.argv", test_args):
            log_grinder_result.main()

        assert output_file.exists(), "JSONL output file was not created"

        lines = [l for l in output_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 1, "Expected exactly one record"

        record = json.loads(lines[0])
        assert "timestamp" in record
        assert record["strategy_name"] == "base_strategy"
        assert record["aider_success"] is True
        assert record["syntax_valid"] is True
        assert "status" in record

    def test_status_is_qc_not_submitted_when_no_qc_file(self, tmp_path):
        output_file = tmp_path / "grinder_results.jsonl"
        test_args = [
            "log_grinder_result.py",
            "--prompt", "Build strategy",
            "--name", "no_qc_strategy",
            "--aider", "success",
            "--validate", "success",
            "--priority", "INDEPENDENT",
            "--output", str(output_file),
        ]
        with patch("sys.argv", test_args):
            log_grinder_result.main()

        record = json.loads(output_file.read_text().strip())
        assert record["status"] == "qc_not_submitted"
        assert record["qc_submitted"] is False

    def test_aider_failure_status(self, tmp_path):
        output_file = tmp_path / "grinder_results.jsonl"
        test_args = [
            "log_grinder_result.py",
            "--prompt", "Build strategy",
            "--name", "failed_strategy",
            "--aider", "failure",
            "--validate", "skipped",
            "--priority", "INDEPENDENT",
            "--output", str(output_file),
        ]
        with patch("sys.argv", test_args):
            log_grinder_result.main()

        record = json.loads(output_file.read_text().strip())
        assert record["status"] == "aider_failed"
        assert record["aider_success"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. generate_grinder_summary smoke tests
# ─────────────────────────────────────────────────────────────────────────────

SMOKE_RECORDS = [
    {
        "timestamp": "2026-03-25T00:00:00Z",
        "prompt": "Build ORB",
        "strategy_name": "base_strategy",
        "priority": "PRIORITY",
        "parent": None,
        "aider_success": True,
        "syntax_valid": True,
        "qc_submitted": True,
        "qc_backtest_id": "Smoke-BT-1",
        "qc_sharpe": 0.82,
        "qc_total_orders": 30,
        "qc_net_pnl_pct": 4.5,
        "qc_max_drawdown": 7.0,
        "qc_error": None,
        "status": "qc_success",
    },
    {
        "timestamp": "2026-03-25T00:10:00Z",
        "prompt": "Build VWAP",
        "strategy_name": "vwap_strategy",
        "priority": "INDEPENDENT",
        "parent": None,
        "aider_success": True,
        "syntax_valid": False,
        "qc_submitted": False,
        "qc_backtest_id": None,
        "qc_sharpe": None,
        "qc_total_orders": None,
        "qc_net_pnl_pct": None,
        "qc_max_drawdown": None,
        "qc_error": None,
        "status": "syntax_error",
    },
    {
        "timestamp": "2026-03-25T00:20:00Z",
        "prompt": "Build gap fade",
        "strategy_name": "gap_fade",
        "priority": "INDEPENDENT",
        "parent": None,
        "aider_success": False,
        "syntax_valid": False,
        "qc_submitted": False,
        "qc_backtest_id": None,
        "qc_sharpe": None,
        "qc_total_orders": None,
        "qc_net_pnl_pct": None,
        "qc_max_drawdown": None,
        "qc_error": None,
        "status": "aider_failed",
    },
]


class TestGenerateGrinderSummarySmoke:
    """E2E tests for generate_grinder_summary.main using a 3-record JSONL fixture."""

    def _write_jsonl(self, tmp_path: Path) -> Path:
        f = tmp_path / "grinder_results.jsonl"
        f.write_text("\n".join(json.dumps(r) for r in SMOKE_RECORDS) + "\n")
        return f

    def test_creates_summary_markdown(self, tmp_path):
        jsonl = self._write_jsonl(tmp_path)
        output = tmp_path / "grinder_summary.md"
        test_args = [
            "generate_grinder_summary.py",
            "--input", str(jsonl),
            "--output", str(output),
        ]
        with patch("sys.argv", test_args):
            generate_grinder_summary.main()

        assert output.exists(), "Summary markdown file was not created"

    def test_summary_shows_total_count(self, tmp_path):
        jsonl = self._write_jsonl(tmp_path)
        summary = generate_grinder_summary.generate_summary(SMOKE_RECORDS)
        # "3 prompts attempted" in the overview section
        assert "3" in summary

    def test_summary_contains_success_emoji(self, tmp_path):
        summary = generate_grinder_summary.generate_summary(SMOKE_RECORDS)
        assert "✅" in summary

    def test_summary_contains_failure_emoji(self, tmp_path):
        summary = generate_grinder_summary.generate_summary(SMOKE_RECORDS)
        assert "❌" in summary

    def test_summary_lists_all_strategy_names(self, tmp_path):
        summary = generate_grinder_summary.generate_summary(SMOKE_RECORDS)
        assert "base_strategy" in summary
        assert "vwap_strategy" in summary
        assert "gap_fade" in summary

    def test_summary_has_failures_section(self, tmp_path):
        summary = generate_grinder_summary.generate_summary(SMOKE_RECORDS)
        assert "Failures for Mia2 Escalation" in summary


# ─────────────────────────────────────────────────────────────────────────────
# 5. Environment / dependency regression tests
# ─────────────────────────────────────────────────────────────────────────────


def test_python_version_meets_quantconnect_mcp_requirement():
    """quantconnect-mcp requires Python >=3.12. Fail fast if runner is misconfigured."""
    assert sys.version_info >= (3, 12), (
        f"Python >=3.12 required for quantconnect-mcp. "
        f"Current: {sys.version_info.major}.{sys.version_info.minor}"
    )


def test_quantconnect_mcp_importable():
    """quantconnect-mcp must be importable — catches pip install failures before grinder runs."""
    try:
        importlib.import_module("quantconnect_mcp")
    except ImportError as e:
        raise AssertionError(
            f"quantconnect-mcp is not installed or not importable: {e}. "
            f"Ensure python-version: '3.12' in all workflow jobs."
        ) from e
