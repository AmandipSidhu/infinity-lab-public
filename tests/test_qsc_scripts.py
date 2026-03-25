"""Tests for QSC scripts: parse_prompts, qc_quick_validate, log_grinder_result,
generate_grinder_summary, package_failures_for_mia."""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import parse_prompts  # noqa: E402
import qc_quick_validate  # noqa: E402
import log_grinder_result  # noqa: E402
import generate_grinder_summary  # noqa: E402
import package_failures_for_mia  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# parse_prompts tests
# ─────────────────────────────────────────────────────────────────────────────


SAMPLE_QUEUE = """\
## [PRIORITY] ORB 15min Base

Build an opening range breakout strategy on SPY.

## [IF-PREVIOUS-PASSED] ORB Volume Filter

Take ORB 15min Base and add a volume filter.

## [INDEPENDENT] VWAP Mean Reversion

Build a VWAP mean-reversion strategy on QQQ.

## [IF-PREVIOUS-PASSED] VWAP Trend Filter

Take VWAP Mean Reversion and add a trend filter.

## [LOW-PRIORITY] Gap Fade SPY

Build a gap-fade strategy on SPY.
"""


def write_queue(tmp_path: Path, content: str = SAMPLE_QUEUE) -> Path:
    queue_file = tmp_path / "queue.md"
    queue_file.write_text(content)
    return queue_file


class TestParsePrompts:
    def test_parses_all_five_prompts(self, tmp_path):
        queue_file = write_queue(tmp_path)
        prompts = parse_prompts.parse_queue_file(queue_file)
        assert len(prompts) == 5

    def test_priority_prompt(self, tmp_path):
        queue_file = write_queue(tmp_path)
        prompts = parse_prompts.parse_queue_file(queue_file)
        orb = next(p for p in prompts if p["title"] == "ORB 15min Base")
        assert orb["priority"] == "PRIORITY"
        assert orb["depends_on"] is None
        assert "opening range breakout" in orb["content"]

    def test_conditional_prompt_extracts_dependency(self, tmp_path):
        queue_file = write_queue(tmp_path)
        prompts = parse_prompts.parse_queue_file(queue_file)
        orb_vol = next(p for p in prompts if p["title"] == "ORB Volume Filter")
        assert orb_vol["priority"] == "IF-PREVIOUS-PASSED"
        assert orb_vol["depends_on"] == "ORB 15min Base"

    def test_second_conditional_dependency(self, tmp_path):
        queue_file = write_queue(tmp_path)
        prompts = parse_prompts.parse_queue_file(queue_file)
        vwap_trend = next(p for p in prompts if p["title"] == "VWAP Trend Filter")
        assert vwap_trend["priority"] == "IF-PREVIOUS-PASSED"
        assert vwap_trend["depends_on"] == "VWAP Mean Reversion"

    def test_independent_prompt(self, tmp_path):
        queue_file = write_queue(tmp_path)
        prompts = parse_prompts.parse_queue_file(queue_file)
        vwap = next(p for p in prompts if p["title"] == "VWAP Mean Reversion")
        assert vwap["priority"] == "INDEPENDENT"
        assert vwap["depends_on"] is None

    def test_low_priority_prompt(self, tmp_path):
        queue_file = write_queue(tmp_path)
        prompts = parse_prompts.parse_queue_file(queue_file)
        gap = next(p for p in prompts if p["title"] == "Gap Fade SPY")
        assert gap["priority"] == "LOW-PRIORITY"
        assert gap["depends_on"] is None

    def test_split_by_priority(self, tmp_path):
        queue_file = write_queue(tmp_path)
        prompts = parse_prompts.parse_queue_file(queue_file)
        split = parse_prompts.split_by_priority(prompts)
        assert len(split["priority_prompts"]) == 1
        assert len(split["independent_prompts"]) == 1
        assert len(split["conditional_prompts"]) == 2
        assert len(split["low_priority_prompts"]) == 1

    def test_empty_file(self, tmp_path):
        queue_file = tmp_path / "queue.md"
        queue_file.write_text("")
        prompts = parse_prompts.parse_queue_file(queue_file)
        assert prompts == []

    def test_missing_file_exits(self, tmp_path):
        missing = tmp_path / "nonexistent.md"
        with pytest.raises(SystemExit) as exc_info:
            parse_prompts.parse_queue_file(missing)
        assert exc_info.value.code == 1

    def test_filter_by_priority(self, tmp_path):
        queue_file = write_queue(tmp_path)
        prompts = parse_prompts.parse_queue_file(queue_file)
        filtered = [p for p in prompts if p["priority"] == "PRIORITY"]
        assert len(filtered) == 1
        assert filtered[0]["title"] == "ORB 15min Base"


# ─────────────────────────────────────────────────────────────────────────────
# qc_quick_validate tests
# ─────────────────────────────────────────────────────────────────────────────


VALID_STRATEGY = """\
from AlgorithmImports import *

class OrbAlgorithm(QCAlgorithm):
    def initialize(self) -> None:
        self.set_start_date(2020, 1, 1)
        self.set_end_date(2023, 12, 31)
        self.set_cash(100000)
        self.symbol = self.add_equity("SPY", Resolution.DAILY).symbol

    def on_data(self, data):
        if not self.portfolio[self.symbol].invested:
            self.market_order(self.symbol, 10)
"""

ANTI_PATTERN_PORTFOLIO = """\
from AlgorithmImports import *

class BadAlgorithm(QCAlgorithm):
    def initialize(self) -> None:
        self.symbol = self.add_equity("SPY", Resolution.DAILY).symbol

    def on_data(self, data):
        # BUG: hallucinated API
        for h in algorithm.portfolio.values():
            pass
"""

ANTI_PATTERN_SELF_ALGORITHM = """\
from AlgorithmImports import *

class BadAlgorithm(QCAlgorithm):
    def initialize(self) -> None:
        self.symbol = self.add_equity("SPY", Resolution.DAILY).symbol

    def on_data(self, data):
        self.algorithm.market_order(self.symbol, 10)
"""

MISSING_QCALGORITHM = """\
from AlgorithmImports import *

class OrbStrategy:
    def initialize(self) -> None:
        pass
"""

SYNTAX_ERROR_CODE = """\
from AlgorithmImports import *

class OrbAlgorithm(QCAlgorithm:
    def initialize(self) -> None:
        pass
"""


class TestQcQuickValidate:
    def _write(self, tmp_path: Path, content: str) -> Path:
        f = tmp_path / "strategy.py"
        f.write_text(content)
        return f

    def test_valid_strategy_passes(self, tmp_path):
        f = self._write(tmp_path, VALID_STRATEGY)
        errors = qc_quick_validate.validate_file(f)
        assert errors == []

    def test_rejects_algorithm_portfolio_pattern(self, tmp_path):
        f = self._write(tmp_path, ANTI_PATTERN_PORTFOLIO)
        errors = qc_quick_validate.validate_file(f)
        assert any("algorithm.portfolio." in e for e in errors)

    def test_rejects_self_algorithm_pattern(self, tmp_path):
        f = self._write(tmp_path, ANTI_PATTERN_SELF_ALGORITHM)
        errors = qc_quick_validate.validate_file(f)
        assert any("self.algorithm." in e for e in errors)

    def test_rejects_missing_qcalgorithm(self, tmp_path):
        f = self._write(tmp_path, MISSING_QCALGORITHM)
        errors = qc_quick_validate.validate_file(f)
        assert any("QCAlgorithm" in e for e in errors)

    def test_rejects_syntax_error(self, tmp_path):
        f = self._write(tmp_path, SYNTAX_ERROR_CODE)
        errors = qc_quick_validate.validate_file(f)
        assert any("Syntax error" in e for e in errors)

    def test_missing_file_returns_error(self, tmp_path):
        missing = tmp_path / "does_not_exist.py"
        errors = qc_quick_validate.validate_file(missing)
        assert any("not found" in e.lower() for e in errors)

    def test_check_anti_patterns_clean(self):
        errors = qc_quick_validate.check_anti_patterns(VALID_STRATEGY)
        assert errors == []

    def test_check_required_patterns_present(self):
        errors = qc_quick_validate.check_required_patterns(VALID_STRATEGY)
        assert errors == []

    def test_check_required_patterns_missing(self):
        errors = qc_quick_validate.check_required_patterns(MISSING_QCALGORITHM)
        assert len(errors) == 1


# ─────────────────────────────────────────────────────────────────────────────
# log_grinder_result tests
# ─────────────────────────────────────────────────────────────────────────────


class TestLogGrinderResult:
    def test_parse_qc_result_missing_file(self):
        metrics = log_grinder_result.parse_qc_result("/tmp/no_such_file_xyz.json")
        assert metrics["qc_submitted"] is False
        assert metrics["qc_sharpe"] is None

    def test_parse_qc_result_none_path(self):
        metrics = log_grinder_result.parse_qc_result(None)
        assert metrics["qc_submitted"] is False

    def test_parse_qc_result_with_data(self, tmp_path):
        result = {
            "backtest_id": "Test-Backtest-123",
            "statistics": {
                "Sharpe Ratio": "0.75",
                "Total Orders": "42",
                "Net Profit": "5.3%",
                "Drawdown": "12.1%",
            },
        }
        result_file = tmp_path / "result.json"
        result_file.write_text(json.dumps(result))
        metrics = log_grinder_result.parse_qc_result(str(result_file))
        assert metrics["qc_submitted"] is True
        assert metrics["qc_backtest_id"] == "Test-Backtest-123"
        assert abs(metrics["qc_sharpe"] - 0.75) < 0.001
        assert metrics["qc_total_orders"] == 42.0
        assert abs(metrics["qc_net_pnl_pct"] - 5.3) < 0.01

    def test_determine_status_qc_success(self):
        qc_metrics = {
            "qc_submitted": True,
            "qc_error": None,
        }
        status = log_grinder_result.determine_status("success", "success", qc_metrics, "PRIORITY")
        assert status == "qc_success"

    def test_determine_status_aider_failed(self):
        status = log_grinder_result.determine_status("failure", "skipped", {}, "PRIORITY")
        assert status == "aider_failed"

    def test_determine_status_syntax_error(self):
        status = log_grinder_result.determine_status("success", "failure", {}, "INDEPENDENT")
        assert status == "syntax_error"

    def test_determine_status_skipped_parent_failed(self):
        status = log_grinder_result.determine_status("skipped", "skipped", {}, "IF-PREVIOUS-PASSED")
        assert status == "skipped_parent_failed"

    def test_determine_status_qc_not_submitted(self):
        status = log_grinder_result.determine_status(
            "success", "success", {"qc_submitted": False}, "INDEPENDENT"
        )
        assert status == "qc_not_submitted"

    def test_main_writes_jsonl(self, tmp_path):
        output_file = tmp_path / "output" / "results.jsonl"
        test_args = [
            "log_grinder_result.py",
            "--prompt", "Build ORB strategy",
            "--name", "orb_test",
            "--aider", "success",
            "--validate", "success",
            "--priority", "PRIORITY",
            "--output", str(output_file),
        ]
        with patch("sys.argv", test_args):
            log_grinder_result.main()

        assert output_file.exists()
        records = [json.loads(line) for line in output_file.read_text().splitlines() if line]
        assert len(records) == 1
        assert records[0]["strategy_name"] == "orb_test"
        assert records[0]["aider_success"] is True

    def test_main_appends_multiple_records(self, tmp_path):
        output_file = tmp_path / "results.jsonl"
        base_args = [
            "--aider", "success",
            "--validate", "success",
            "--output", str(output_file),
        ]
        for i in range(3):
            test_args = [
                "log_grinder_result.py",
                "--prompt", f"Prompt {i}",
                "--name", f"strategy_{i}",
            ] + base_args
            with patch("sys.argv", test_args):
                log_grinder_result.main()

        records = [json.loads(line) for line in output_file.read_text().splitlines() if line]
        assert len(records) == 3


# ─────────────────────────────────────────────────────────────────────────────
# generate_grinder_summary tests
# ─────────────────────────────────────────────────────────────────────────────


SAMPLE_RECORDS = [
    {
        "timestamp": "2026-03-24T23:00:00Z",
        "prompt": "Build ORB",
        "strategy_name": "orb_15min_base",
        "priority": "PRIORITY",
        "parent": None,
        "aider_success": True,
        "syntax_valid": True,
        "qc_submitted": True,
        "qc_backtest_id": "Test-BT-1",
        "qc_sharpe": 0.75,
        "qc_total_orders": 45,
        "qc_net_pnl_pct": 5.2,
        "qc_max_drawdown": 8.1,
        "qc_error": None,
        "status": "qc_success",
    },
    {
        "timestamp": "2026-03-24T23:10:00Z",
        "prompt": "Build VWAP",
        "strategy_name": "vwap_mean_reversion",
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
        "timestamp": "2026-03-24T23:20:00Z",
        "prompt": "Take ORB 15min Base and add volume filter",
        "strategy_name": "orb_volume_filter",
        "priority": "IF-PREVIOUS-PASSED",
        "parent": "orb_15min_base",
        "aider_success": True,
        "syntax_valid": True,
        "qc_submitted": True,
        "qc_backtest_id": "Test-BT-2",
        "qc_sharpe": 0.42,
        "qc_total_orders": 38,
        "qc_net_pnl_pct": 3.1,
        "qc_max_drawdown": 6.5,
        "qc_error": None,
        "status": "qc_success",
    },
]


class TestGenerateGrinderSummary:
    def write_jsonl(self, tmp_path: Path, records: list[dict]) -> Path:
        f = tmp_path / "grinder_results.jsonl"
        f.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return f

    def test_load_results(self, tmp_path):
        f = self.write_jsonl(tmp_path, SAMPLE_RECORDS)
        records = generate_grinder_summary.load_results(f)
        assert len(records) == 3

    def test_load_results_missing_file(self, tmp_path):
        missing = tmp_path / "nonexistent.jsonl"
        records = generate_grinder_summary.load_results(missing)
        assert records == []

    def test_generate_summary_contains_stats(self, tmp_path):
        summary = generate_grinder_summary.generate_summary(SAMPLE_RECORDS)
        assert "3" in summary  # total
        assert "2/3" in summary  # qc success count
        assert "orb_15min_base" in summary
        assert "vwap_mean_reversion" in summary

    def test_generate_summary_empty(self):
        summary = generate_grinder_summary.generate_summary([])
        assert "No builds recorded yet" in summary

    def test_generate_summary_has_failures_section(self):
        summary = generate_grinder_summary.generate_summary(SAMPLE_RECORDS)
        assert "Failures for Mia2 Escalation" in summary
        assert "vwap_mean_reversion" in summary

    def test_generate_summary_has_top_performers(self):
        summary = generate_grinder_summary.generate_summary(SAMPLE_RECORDS)
        assert "Top Performers" in summary
        assert "orb_15min_base" in summary

    def test_generate_summary_conditional_table(self):
        summary = generate_grinder_summary.generate_summary(SAMPLE_RECORDS)
        assert "Conditional Builds" in summary
        assert "orb_volume_filter" in summary

    def test_main_writes_file(self, tmp_path):
        jsonl = self.write_jsonl(tmp_path, SAMPLE_RECORDS)
        output = tmp_path / "summary.md"
        test_args = [
            "generate_grinder_summary.py",
            "--input", str(jsonl),
            "--output", str(output),
        ]
        with patch("sys.argv", test_args):
            generate_grinder_summary.main()
        assert output.exists()
        content = output.read_text()
        assert "QSC Grinder Summary" in content

    def test_status_emoji_mapping(self):
        assert generate_grinder_summary.status_emoji("qc_success") == "✅"
        assert generate_grinder_summary.status_emoji("syntax_error") == "❌"
        assert generate_grinder_summary.status_emoji("skipped_parent_failed") == "⏭️"
        assert generate_grinder_summary.status_emoji("aider_failed") == "💥"


# ─────────────────────────────────────────────────────────────────────────────
# package_failures_for_mia tests
# ─────────────────────────────────────────────────────────────────────────────


class TestPackageFailuresForMia:
    def write_jsonl(self, tmp_path: Path, records: list[dict]) -> Path:
        f = tmp_path / "grinder_results.jsonl"
        f.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return f

    def test_load_results_empty_file(self, tmp_path):
        missing = tmp_path / "nonexistent.jsonl"
        records = package_failures_for_mia.load_results(missing)
        assert records == []

    def test_filters_successes_and_skipped(self):
        failures = [
            r
            for r in SAMPLE_RECORDS
            if r.get("status") not in ("qc_success", "skipped_parent_failed")
        ]
        assert len(failures) == 1
        assert failures[0]["strategy_name"] == "vwap_mean_reversion"

    def test_load_strategy_code_missing(self, tmp_path):
        result = package_failures_for_mia.load_strategy_code(tmp_path, "nonexistent_strategy")
        assert result is None

    def test_load_strategy_code_found(self, tmp_path):
        strat_file = tmp_path / "my_strategy.py"
        strat_file.write_text("# strategy code")
        result = package_failures_for_mia.load_strategy_code(tmp_path, "my_strategy")
        assert result == "# strategy code"

    def test_format_failure_section_syntax_error(self):
        record = {
            "strategy_name": "vwap_test",
            "status": "syntax_error",
            "priority": "INDEPENDENT",
            "parent": None,
            "prompt": "Build VWAP strategy",
            "qc_error": None,
            "timestamp": "2026-03-24T23:00:00Z",
            "aider_success": True,
            "syntax_valid": False,
        }
        section = package_failures_for_mia.format_failure_section(1, record, None, None)
        assert "vwap_test" in section
        assert "syntax_error" in section
        assert "Build VWAP strategy" in section

    def test_main_creates_output_file(self, tmp_path):
        jsonl = self.write_jsonl(tmp_path, SAMPLE_RECORDS)
        output = tmp_path / "mia_context" / "failed_builds.md"
        strats_dir = tmp_path / "strategies"
        strats_dir.mkdir()
        test_args = [
            "package_failures_for_mia.py",
            "--input", str(jsonl),
            "--strategies", str(strats_dir),
            "--output", str(output),
        ]
        with patch("sys.argv", test_args):
            package_failures_for_mia.main()
        assert output.exists()
        content = output.read_text()
        assert "Mia2 Escalation Bundle" in content
        assert "vwap_mean_reversion" in content

    def test_main_no_failures(self, tmp_path):
        """When all builds succeed, output file should say no failures."""
        only_success = [r for r in SAMPLE_RECORDS if r["status"] == "qc_success"]
        jsonl = self.write_jsonl(tmp_path, only_success)
        output = tmp_path / "failed_builds.md"
        strats_dir = tmp_path / "strategies"
        strats_dir.mkdir()
        test_args = [
            "package_failures_for_mia.py",
            "--input", str(jsonl),
            "--strategies", str(strats_dir),
            "--output", str(output),
        ]
        with patch("sys.argv", test_args):
            package_failures_for_mia.main()
        content = output.read_text()
        assert "No failures to report" in content
