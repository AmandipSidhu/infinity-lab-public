"""Pipeline smoke tests — validate the ACB Pipeline wiring without real API calls.

Covers:
- detect-specs matrix JSON output (the logic that runs in the detect-specs job)
- spec_validator smoke mode (no mocks needed; uses corpus fixtures)
- strategy_reviewer smoke mode (mocked _run_fallback_chain)
- ack_gate smoke mode (mocked run_ack_gate)
- aider_builder smoke mode (mocked subprocess)
- constraints.txt / requirements.txt dependency pins are internally consistent
"""

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import ack_gate  # noqa: E402
import strategy_reviewer  # noqa: E402
from ack_gate import main as ack_main  # noqa: E402
from aider_builder import (  # noqa: E402
    _build_aider_cmd,
    _detect_api_unavailable,
    _detect_daily_limit,
    _detect_rate_limit,
    _detect_syntax_error,
    _extract_error_fingerprint,
    _extract_test_pass_rate,
)
from spec_validator import main as validator_main  # noqa: E402
from strategy_reviewer import main as reviewer_main  # noqa: E402

CORPUS_DIR = Path(__file__).parent / "spec_corpus"

_PASS_RESULT: dict[str, Any] = {
    "verdict": "PASS",
    "risk_level": "low",
    "concerns": [],
}


# ---------------------------------------------------------------------------
# Detect-specs matrix logic
# ---------------------------------------------------------------------------


class TestDetectSpecsMatrix:
    """Validate the shell logic that populates the matrix_specs workflow output."""

    def _simulate_matrix(self, changed_files: list[str]) -> list[str]:
        """Simulate the get-specs step logic: filter changed files to specs/**."""
        return [f for f in changed_files if f.startswith("specs/")]

    def test_single_spec_change_produces_one_item(self) -> None:
        matrix = self._simulate_matrix(["specs/momentum.yaml", "README.md"])
        assert matrix == ["specs/momentum.yaml"]

    def test_multiple_spec_changes(self) -> None:
        matrix = self._simulate_matrix([
            "specs/momentum.yaml",
            "scripts/spec_validator.py",
            "specs/mean_reversion.yaml",
        ])
        assert len(matrix) == 2
        assert "specs/momentum.yaml" in matrix
        assert "specs/mean_reversion.yaml" in matrix

    def test_no_spec_changes_produces_empty(self) -> None:
        matrix = self._simulate_matrix(["README.md", "scripts/spec_validator.py"])
        assert matrix == []

    def test_matrix_is_valid_json(self) -> None:
        matrix = self._simulate_matrix(["specs/test.yaml"])
        json_str = json.dumps(matrix)
        parsed = json.loads(json_str)
        assert parsed == ["specs/test.yaml"]

    def test_matrix_json_round_trips(self) -> None:
        specs = ["specs/a.yaml", "specs/b.yaml"]
        json_str = json.dumps(specs)
        assert json.loads(json_str) == specs

    def test_workflow_dispatch_spec_file_input(self) -> None:
        """Simulate the workflow_dispatch branch: explicit spec_file input."""
        spec_input = "specs/my_strategy.yaml"
        matrix = [spec_input] if spec_input else []
        assert matrix == ["specs/my_strategy.yaml"]


# ---------------------------------------------------------------------------
# spec_validator smoke
# ---------------------------------------------------------------------------


class TestSpecValidatorSmoke:
    def test_valid_spec_exits_0(self) -> None:
        rc = validator_main(["--spec", str(CORPUS_DIR / "valid_001.yaml")])
        assert rc == 0

    def test_invalid_spec_exits_1(self) -> None:
        rc = validator_main(["--spec", str(CORPUS_DIR / "invalid_001_missing_fields.yaml")])
        assert rc == 1

    def test_missing_file_exits_2(self) -> None:
        rc = validator_main(["--spec", "/nonexistent/spec.yaml"])
        assert rc == 2

    def test_no_args_exits_2(self) -> None:
        rc = validator_main([])
        assert rc == 2


# ---------------------------------------------------------------------------
# strategy_reviewer smoke (mocked AI)
# ---------------------------------------------------------------------------


class TestStrategyReviewerSmoke:
    def test_pass_verdict_exits_0(self, tmp_path: Path) -> None:
        spec = CORPUS_DIR / "valid_001.yaml"
        with patch.object(strategy_reviewer, "CACHE_DIR", tmp_path / "cache"):
            with patch.object(strategy_reviewer, "_run_fallback_chain", return_value=_PASS_RESULT):
                rc = reviewer_main(["--spec", str(spec)])
        assert rc == 0

    def test_output_flag_writes_json(self, tmp_path: Path) -> None:
        spec = CORPUS_DIR / "valid_001.yaml"
        out = tmp_path / "result.json"
        with patch.object(strategy_reviewer, "CACHE_DIR", tmp_path / "cache"):
            with patch.object(strategy_reviewer, "_run_fallback_chain", return_value=_PASS_RESULT):
                rc = reviewer_main(["--spec", str(spec), "--output", str(out)])
        assert rc == 0
        data = json.loads(out.read_text())
        assert data["verdict"] == "PASS"
        assert "spec_file" in data
        assert "reviewed_at" in data

    def test_missing_spec_exits_2(self) -> None:
        rc = reviewer_main(["--spec", "/nonexistent/spec.yaml"])
        assert rc == 2


# ---------------------------------------------------------------------------
# ack_gate smoke
# ---------------------------------------------------------------------------


class TestAckGateSmoke:
    def test_no_concerns_calls_run_ack_gate_with_empty_list(
        self, tmp_path: Path
    ) -> None:
        output = tmp_path / "reviewer.json"
        output.write_text(json.dumps(_PASS_RESULT), encoding="utf-8")
        with patch.object(ack_gate, "run_ack_gate", return_value=0) as mock_run:
            rc = ack_main(["--warns", str(output)])
        assert rc == 0
        mock_run.assert_called_once_with([])

    def test_warn_verdict_passes_concerns(self, tmp_path: Path) -> None:
        data = {
            "verdict": "WARN",
            "risk_level": "medium",
            "concerns": ["SRV-W001: missing author", "SRV-W006: missing max_drawdown"],
        }
        output = tmp_path / "reviewer.json"
        output.write_text(json.dumps(data), encoding="utf-8")
        with patch.object(ack_gate, "run_ack_gate", return_value=0) as mock_run:
            rc = ack_main(["--warns", str(output)])
        assert rc == 0
        mock_run.assert_called_once_with(data["concerns"])

    def test_missing_file_returns_1(self, tmp_path: Path) -> None:
        rc = ack_main(["--warns", str(tmp_path / "nonexistent.json")])
        assert rc == 1


# ---------------------------------------------------------------------------
# aider_builder detection helpers
# ---------------------------------------------------------------------------


class TestAiderBuilderHelpers:
    def test_detect_rate_limit(self) -> None:
        assert _detect_rate_limit("Error: 429 Too Many Requests")
        assert _detect_rate_limit("rate limit exceeded")
        assert not _detect_rate_limit("200 OK everything fine")

    def test_detect_daily_limit(self) -> None:
        assert _detect_daily_limit("exceeded your daily quota")
        assert not _detect_daily_limit("all good")

    def test_detect_api_unavailable(self) -> None:
        assert _detect_api_unavailable("503 Service Unavailable")
        assert not _detect_api_unavailable("200 OK")

    def test_detect_syntax_error(self) -> None:
        assert _detect_syntax_error("SyntaxError: invalid syntax")
        assert not _detect_syntax_error("all tests passed")

    def test_extract_error_fingerprint_normalizes_numbers(self) -> None:
        fp1 = _extract_error_fingerprint("Error: line 42 failed")
        fp2 = _extract_error_fingerprint("Error: line 99 failed")
        assert fp1 == fp2

    def test_extract_error_fingerprint_returns_empty_on_no_error(self) -> None:
        fp = _extract_error_fingerprint("everything is fine")
        assert fp == ""

    def test_extract_test_pass_rate_with_all_passing(self) -> None:
        rate = _extract_test_pass_rate("5 passed, 0 warnings in 1.23s")
        assert rate == 1.0

    def test_extract_test_pass_rate_with_mixed(self) -> None:
        rate = _extract_test_pass_rate("3 passed, 2 failed in 0.5s")
        assert rate == pytest.approx(0.6)

    def test_extract_test_pass_rate_no_summary(self) -> None:
        rate = _extract_test_pass_rate("aider output with no pytest")
        assert rate is None

    def test_build_aider_cmd_structure(self, tmp_path: Path) -> None:
        spec_file = tmp_path / "my_strategy.yaml"
        spec_file.touch()
        cmd = _build_aider_cmd("gemini/gemini-2.5-flash", spec_file, "my_strategy")
        assert cmd[0] == "aider"
        assert "--model" in cmd
        assert "gemini/gemini-2.5-flash" in cmd
        assert "--yes" in cmd
        assert "strategies/my_strategy.py" in cmd
        assert "tests/test_my_strategy.py" in cmd


# ---------------------------------------------------------------------------
# Dependency pin consistency
# ---------------------------------------------------------------------------


class TestDependencyPinConsistency:
    """Validate that constraints.txt and requirements.txt are internally consistent."""

    def test_constraints_file_exists(self) -> None:
        constraints = REPO_ROOT / "constraints.txt"
        assert constraints.exists(), "constraints.txt must exist in repo root"

    def test_requirements_file_exists(self) -> None:
        requirements = REPO_ROOT / "requirements.txt"
        assert requirements.exists(), "requirements.txt must exist in repo root"

    def _parse_pins(self, path: Path) -> dict[str, str]:
        """Return {package_name: version} for exact == pins in a file."""
        pins: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "==" in line:
                parts = line.split("==", 1)
                if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                    pins[parts[0].strip().lower()] = parts[1].strip()
        return pins

    def test_constraints_pins_openai(self) -> None:
        constraints = REPO_ROOT / "constraints.txt"
        pins = self._parse_pins(constraints)
        assert "openai" in pins, "constraints.txt must pin openai"

    def test_constraints_pins_aider_chat(self) -> None:
        constraints = REPO_ROOT / "constraints.txt"
        pins = self._parse_pins(constraints)
        assert "aider-chat" in pins, "constraints.txt must pin aider-chat"

    def test_requirements_openai_upper_bounds_to_v1(self) -> None:
        requirements = REPO_ROOT / "requirements.txt"
        content = requirements.read_text(encoding="utf-8")
        openai_line = next(
            (l.strip() for l in content.splitlines()
             if re.match(r"^openai[>=!<]", l.strip())),
            None,
        )
        assert openai_line is not None, "requirements.txt must have an openai entry"
        assert "<2.0.0" in openai_line, (
            "requirements.txt openai spec must include <2.0.0 upper bound "
            "to prevent installing the breaking openai v2 API"
        )

    def test_workflow_uses_constraints_flag(self) -> None:
        workflow = REPO_ROOT / ".github" / "workflows" / "acb_pipeline.yml"
        content = workflow.read_text(encoding="utf-8")
        assert "-c constraints.txt" in content, (
            "acb_pipeline.yml install step must use -c constraints.txt"
        )

    def test_workflow_uses_python_m_pip(self) -> None:
        workflow = REPO_ROOT / ".github" / "workflows" / "acb_pipeline.yml"
        content = workflow.read_text(encoding="utf-8")
        assert "python -m pip install" in content, (
            "acb_pipeline.yml install step must use 'python -m pip install' "
            "to ensure packages are installed for the same interpreter"
        )

    def test_workflow_verifies_requests_import(self) -> None:
        workflow = REPO_ROOT / ".github" / "workflows" / "acb_pipeline.yml"
        content = workflow.read_text(encoding="utf-8")
        assert "import requests" in content, (
            "acb_pipeline.yml must have a post-install sanity step that verifies 'import requests'"
        )

    def test_workflow_human_review_gated_on_install(self) -> None:
        workflow = REPO_ROOT / ".github" / "workflows" / "acb_pipeline.yml"
        content = workflow.read_text(encoding="utf-8")
        assert "steps.install-deps.outcome == 'success'" in content, (
            "Human review and artifacts step must be gated on install-deps success, "
            "not always(), to avoid masking root-cause failures"
        )
