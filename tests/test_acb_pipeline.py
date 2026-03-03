"""Tests for the new CLI flags added to support the ACB pipeline workflow.

Covers:
- spec_validator.py: --spec flag (backward-compatible with positional arg)
- strategy_reviewer.py: --spec and --output flags (backward-compatible with positional arg)
- ack_gate.py: --warns flag for reading JSON file (backward-compatible with positional args)
"""

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import ack_gate  # noqa: E402
import strategy_reviewer  # noqa: E402
from ack_gate import main as ack_main  # noqa: E402
from spec_validator import main as validator_main  # noqa: E402
from strategy_reviewer import main as reviewer_main  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_RESULT: dict[str, Any] = {
    "verdict": "PASS",
    "risk_level": "low",
    "concerns": [],
}

WARN_RESULT: dict[str, Any] = {
    "verdict": "WARN",
    "risk_level": "medium",
    "concerns": ["SRV-W001: metadata.author is missing", "SRV-W006: max_drawdown is missing"],
}

CORPUS_DIR = Path(__file__).parent / "spec_corpus"


# ---------------------------------------------------------------------------
# spec_validator.py — --spec flag
# ---------------------------------------------------------------------------


class TestSpecValidatorSpecFlag:
    def test_spec_flag_accepted(self, tmp_path: Path) -> None:
        spec_file = CORPUS_DIR / "valid_001.yaml"
        rc = validator_main(["--spec", str(spec_file)])
        assert rc == 0

    def test_spec_flag_missing_file_returns_2(self) -> None:
        rc = validator_main(["--spec", "/nonexistent/path/spec.yaml"])
        assert rc == 2

    def test_no_args_still_returns_2(self) -> None:
        rc = validator_main([])
        assert rc == 2

    def test_positional_arg_still_works(self) -> None:
        spec_file = CORPUS_DIR / "valid_001.yaml"
        rc = validator_main([str(spec_file)])
        assert rc == 0

    def test_invalid_flag_returns_2(self) -> None:
        rc = validator_main(["--unknown", "value"])
        assert rc == 2

    def test_spec_flag_with_error_spec_returns_1(self) -> None:
        spec_file = CORPUS_DIR / "invalid_001_missing_fields.yaml"
        rc = validator_main(["--spec", str(spec_file)])
        assert rc == 1


# ---------------------------------------------------------------------------
# strategy_reviewer.py — --spec and --output flags
# ---------------------------------------------------------------------------


class TestStrategyReviewerSpecFlag:
    def test_spec_flag_accepted(self, tmp_path: Path) -> None:
        spec_file = CORPUS_DIR / "valid_001.yaml"
        with patch.object(strategy_reviewer, "CACHE_DIR", tmp_path / "cache"):
            with patch.object(strategy_reviewer, "_run_fallback_chain", return_value=VALID_RESULT):
                rc = reviewer_main(["--spec", str(spec_file)])
        assert rc == 0

    def test_output_flag_writes_to_file(self, tmp_path: Path) -> None:
        spec_file = CORPUS_DIR / "valid_001.yaml"
        out_file = tmp_path / "reviewer_output.json"
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
            with patch.object(strategy_reviewer, "CACHE_DIR", tmp_path / "cache"):
                with patch.object(strategy_reviewer, "_run_fallback_chain", return_value=VALID_RESULT):
                    rc = reviewer_main(["--spec", str(spec_file), "--output", str(out_file)])
        assert rc == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert data["verdict"] == "PASS"
        assert "spec_file" in data
        assert "reviewed_at" in data

    def test_output_flag_does_not_print_to_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        spec_file = CORPUS_DIR / "valid_001.yaml"
        out_file = tmp_path / "reviewer_output.json"
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
            with patch.object(strategy_reviewer, "CACHE_DIR", tmp_path / "cache"):
                with patch.object(strategy_reviewer, "_run_fallback_chain", return_value=VALID_RESULT):
                    reviewer_main(["--spec", str(spec_file), "--output", str(out_file)])
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_without_output_flag_prints_to_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        spec_file = CORPUS_DIR / "valid_001.yaml"
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
            with patch.object(strategy_reviewer, "CACHE_DIR", tmp_path / "cache"):
                with patch.object(strategy_reviewer, "_run_fallback_chain", return_value=VALID_RESULT):
                    reviewer_main(["--spec", str(spec_file)])
        captured = capsys.readouterr()
        assert captured.out.strip() != ""
        data = json.loads(captured.out)
        assert data["verdict"] == "PASS"

    def test_no_args_returns_2(self) -> None:
        rc = reviewer_main([])
        assert rc == 2

    def test_two_positional_args_returns_2(self) -> None:
        rc = reviewer_main(["a.yaml", "b.yaml"])
        assert rc == 2

    def test_positional_arg_still_works(self, tmp_path: Path) -> None:
        spec_file = CORPUS_DIR / "valid_001.yaml"
        with patch.object(strategy_reviewer, "CACHE_DIR", tmp_path / "cache"):
            with patch.object(strategy_reviewer, "_run_fallback_chain", return_value=VALID_RESULT):
                rc = reviewer_main([str(spec_file)])
        assert rc == 0


# ---------------------------------------------------------------------------
# ack_gate.py — --warns flag
# ---------------------------------------------------------------------------


class TestAckGateWarnsFlag:
    def test_warns_flag_passes_concerns_to_run_ack_gate(
        self, tmp_path: Path
    ) -> None:
        reviewer_output = tmp_path / "reviewer_output.json"
        reviewer_output.write_text(json.dumps(WARN_RESULT), encoding="utf-8")

        with patch.object(ack_gate, "run_ack_gate", return_value=0) as mock_run:
            rc = ack_main(["--warns", str(reviewer_output)])

        assert rc == 0
        mock_run.assert_called_once_with(WARN_RESULT["concerns"])

    def test_warns_flag_pass_verdict_passes_concerns_to_run_ack_gate(
        self, tmp_path: Path
    ) -> None:
        reviewer_output = tmp_path / "reviewer_output.json"
        reviewer_output.write_text(json.dumps(VALID_RESULT), encoding="utf-8")

        with patch.object(ack_gate, "run_ack_gate", return_value=0) as mock_run:
            rc = ack_main(["--warns", str(reviewer_output)])

        assert rc == 0
        mock_run.assert_called_once_with([])

    def test_warns_flag_empty_concerns_calls_run_ack_gate_with_empty_list(
        self, tmp_path: Path
    ) -> None:
        data = {"verdict": "WARN", "risk_level": "high", "concerns": []}
        reviewer_output = tmp_path / "reviewer_output.json"
        reviewer_output.write_text(json.dumps(data), encoding="utf-8")

        with patch.object(ack_gate, "run_ack_gate", return_value=0) as mock_run:
            rc = ack_main(["--warns", str(reviewer_output)])

        assert rc == 0
        mock_run.assert_called_once_with([])

    def test_warns_flag_propagates_run_ack_gate_exit_code(
        self, tmp_path: Path
    ) -> None:
        reviewer_output = tmp_path / "reviewer_output.json"
        reviewer_output.write_text(json.dumps(WARN_RESULT), encoding="utf-8")

        with patch.object(ack_gate, "run_ack_gate", return_value=1):
            rc = ack_main(["--warns", str(reviewer_output)])

        assert rc == 1

    def test_warns_flag_missing_file_returns_1(self, tmp_path: Path) -> None:
        rc = ack_main(["--warns", str(tmp_path / "nonexistent.json")])
        assert rc == 1

    def test_warns_flag_invalid_json_returns_1(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not valid json", encoding="utf-8")
        rc = ack_main(["--warns", str(bad_file)])
        assert rc == 1

    def test_positional_args_still_work(self) -> None:
        with patch.object(ack_gate, "run_ack_gate", return_value=0) as mock_run:
            rc = ack_main(["warn1", "warn2"])
        assert rc == 0
        mock_run.assert_called_once_with(["warn1", "warn2"])
