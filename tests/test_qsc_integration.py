#!/usr/bin/env python3
"""QSC Integration Test — Aider + QC end-to-end validation.

Run conditions:
    This test is skipped unless the environment variable QSC_INTEGRATION=1 is set.
    It makes real network calls (Aider → Gemini Flash, optionally QC REST).

Usage:
    # Full integration (Aider required; QC optional):
    QSC_INTEGRATION=1 pytest tests/test_qsc_integration.py -v

    # With QC evaluation:
    QSC_INTEGRATION=1 QC_USER_ID=<id> QC_API_TOKEN=<token> \
        pytest tests/test_qsc_integration.py -v

Requirements:
    GEMINI_API_KEY must be set in the environment for Aider to call Gemini Flash.
    QC_USER_ID and QC_API_TOKEN are optional; QC step is skipped when absent.
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Skip guard — integration tests are opt-in only
# ---------------------------------------------------------------------------

if os.environ.get("QSC_INTEGRATION") != "1":
    pytest.skip("QSC integration test disabled (set QSC_INTEGRATION=1 to enable)", allow_module_level=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent

# The single PRIORITY prompt used throughout this test suite
_PROMPT_TITLE = "ORB 15min Base"
_PROMPT_CONTENT = textwrap.dedent("""\
    Build a minimal opening range breakout strategy that:
    - Inherits from QCAlgorithm
    - Trades SPY
    - Uses first 15 minutes high/low
    - Places a single market order on breakout
    - Has a fixed time exit
""").strip()

_QUEUE_MD = f"""\
## [PRIORITY] {_PROMPT_TITLE}
{_PROMPT_CONTENT}
"""

_STRATEGY_NAME = "orb_15min_base"


def _run(
    cmd: list[str],
    cwd: Path | None = None,
    env: dict | None = None,
    timeout: int = 600,
) -> subprocess.CompletedProcess:
    """Run a subprocess and return the result."""
    merged_env = {**os.environ, **(env or {})}
    return subprocess.run(
        cmd,
        cwd=cwd or REPO_ROOT,
        env=merged_env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Integration test suite
# ---------------------------------------------------------------------------

class TestQscIntegration:
    """End-to-end integration tests for the QSC grinder pipeline.

    Each test method is independent and uses a shared temp directory fixture
    so artefacts (strategy file, JSONL) persist across test methods within
    the same run.
    """

    @pytest.fixture(scope="class", autouse=True)
    def integration_workspace(self, tmp_path_factory: pytest.TempPathFactory, request):
        """Create a temp workspace shared by all methods in this class."""
        workspace = tmp_path_factory.mktemp("qsc_integration")

        # Directory structure expected by the scripts
        (workspace / "prompts").mkdir()
        (workspace / "strategies").mkdir()
        (workspace / "output").mkdir()

        # Write the minimal prompt queue
        (workspace / "prompts" / "queue.md").write_text(_QUEUE_MD, encoding="utf-8")

        # Attach to the class so test methods can access it
        request.cls.workspace = workspace

    # ------------------------------------------------------------------ #
    # Step 1 — parse_prompts.py                                           #
    # ------------------------------------------------------------------ #

    def test_parse_prompts_returns_one_priority_prompt(self):
        """parse_prompts.py must return exactly 1 PRIORITY prompt from the fixture."""
        queue_md = self.workspace / "prompts" / "queue.md"
        result = _run([sys.executable, "scripts/parse_prompts.py", str(queue_md)])

        assert result.returncode == 0, f"parse_prompts.py failed:\n{result.stderr}"

        prompts = json.loads(result.stdout)
        assert len(prompts) == 1, f"Expected 1 prompt, got {len(prompts)}: {prompts}"
        assert prompts[0]["priority"] == "PRIORITY"
        assert prompts[0]["title"] == _PROMPT_TITLE

    # ------------------------------------------------------------------ #
    # Step 2 — Aider generates the strategy                               #
    # ------------------------------------------------------------------ #

    def test_aider_generates_strategy_file(self):
        """Aider (Gemini Flash) must produce a non-empty .py file."""
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        if not gemini_key:
            pytest.skip("GEMINI_API_KEY not set — skipping Aider call")

        strategy_file = self.workspace / "strategies" / f"{_STRATEGY_NAME}.py"
        aider_model = os.environ.get("AIDER_MODEL", "gemini/gemini-2.5-flash")

        api_ref = REPO_ROOT / "config" / "qc_api_reference.txt"
        system_prompt = REPO_ROOT / "config" / "aider_system_prompt_qsc.txt"
        assert api_ref.exists(), f"Missing required config file: {api_ref}"
        assert system_prompt.exists(), f"Missing required config file: {system_prompt}"

        result = _run(
            [
                "aider",
                "--model", aider_model,
                "--read", str(api_ref),
                "--read", str(system_prompt),
                "--yes", "--no-git",
                "--new-file", str(strategy_file),
                "--message", _PROMPT_CONTENT,
            ],
        )

        assert result.returncode == 0, (
            f"Aider exited with code {result.returncode}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert strategy_file.exists(), "Aider did not create the strategy file"
        assert strategy_file.stat().st_size > 0, "Aider wrote an empty strategy file"

    # ------------------------------------------------------------------ #
    # Step 3 — qc_quick_validate.py                                       #
    # ------------------------------------------------------------------ #

    def test_qc_quick_validate_passes_on_generated_file(self):
        """qc_quick_validate.py must exit 0 on the Aider-generated strategy."""
        strategy_file = self.workspace / "strategies" / f"{_STRATEGY_NAME}.py"
        if not strategy_file.exists():
            pytest.skip("Strategy file not generated (Aider step was skipped or failed)")

        result = _run([sys.executable, "scripts/qc_quick_validate.py", str(strategy_file)])
        assert result.returncode == 0, (
            f"qc_quick_validate.py failed (exit {result.returncode}):\n"
            f"{result.stdout}\n{result.stderr}"
        )

    # ------------------------------------------------------------------ #
    # Step 4 — log_grinder_result.py                                      #
    # ------------------------------------------------------------------ #

    def test_log_grinder_result_appends_valid_jsonl_row(self):
        """log_grinder_result.py must append a valid JSONL row with required fields."""
        strategy_file = self.workspace / "strategies" / f"{_STRATEGY_NAME}.py"
        aider_outcome = "success" if strategy_file.exists() else "skipped"
        validate_outcome = "success" if strategy_file.exists() else "skipped"

        jsonl_path = self.workspace / "output" / "grinder_results.jsonl"

        result = _run(
            [
                sys.executable, "scripts/log_grinder_result.py",
                "--prompt", _PROMPT_CONTENT,
                "--name", _STRATEGY_NAME,
                "--aider", aider_outcome,
                "--validate", validate_outcome,
                "--priority", "PRIORITY",
                "--output", str(jsonl_path),
                # --qc-result intentionally omitted (file may not exist yet)
            ],
        )

        assert result.returncode == 0, f"log_grinder_result.py failed:\n{result.stderr}"
        assert jsonl_path.exists(), "grinder_results.jsonl was not created"

        rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(rows) >= 1, "No rows written to grinder_results.jsonl"

        row = rows[-1]
        required_fields = {"timestamp", "strategy_name", "aider_success", "syntax_valid", "status"}
        missing = required_fields - row.keys()
        assert not missing, f"JSONL row is missing fields: {missing}\nRow: {row}"
        assert row["strategy_name"] == _STRATEGY_NAME

    # ------------------------------------------------------------------ #
    # Step 5 — qc_upload_eval.py (optional, requires QC secrets)         #
    # ------------------------------------------------------------------ #

    def test_qc_upload_eval_records_status(self):
        """qc_upload_eval.py must record a non-empty status in grinder_results.jsonl.

        Skips gracefully if QC secrets are absent.
        """
        qc_user_id = os.environ.get("QC_USER_ID", "")
        qc_api_token = os.environ.get("QC_API_TOKEN", "")
        if not qc_user_id or not qc_api_token:
            pytest.skip("QC_USER_ID / QC_API_TOKEN not set — skipping QC upload step")

        strategy_file = self.workspace / "strategies" / f"{_STRATEGY_NAME}.py"
        if not strategy_file.exists():
            pytest.skip("Strategy file not generated — skipping QC upload step")

        # Write a minimal spec YAML required by qc_upload_eval.py
        spec_file = self.workspace / f"{_STRATEGY_NAME}_spec.yaml"
        spec_file.write_text(
            "strategy:\n  performance_targets:\n    sharpe_ratio_min: 0.0\n    min_trades: 1\n",
            encoding="utf-8",
        )

        qc_result_json = self.workspace / "output" / f"{_STRATEGY_NAME}_qc_result.json"

        result = _run(
            [
                sys.executable, "scripts/qc_upload_eval.py",
                "--spec", str(spec_file),
                "--strategy", str(strategy_file),
                "--output", str(qc_result_json),
            ],
        )

        # qc_upload_eval exits 0 (pass/stub) or 1 (fail constraints) — both are valid
        # for this test; we only care that it ran and produced output.
        assert result.returncode in (0, 1), (
            f"qc_upload_eval.py returned unexpected exit code {result.returncode}.\n"
            f"stderr: {result.stderr[:2000]}"
        )

        # Now log the QC result into the JSONL
        jsonl_path = self.workspace / "output" / "grinder_results.jsonl"
        log_result = _run(
            [
                sys.executable, "scripts/log_grinder_result.py",
                "--prompt", _PROMPT_CONTENT,
                "--name", f"{_STRATEGY_NAME}_with_qc",
                "--aider", "success",
                "--validate", "success",
                "--qc-result", str(qc_result_json),
                "--priority", "PRIORITY",
                "--output", str(jsonl_path),
            ],
        )
        assert log_result.returncode == 0, f"log_grinder_result.py failed:\n{log_result.stderr}"

        rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        qc_rows = [r for r in rows if r.get("strategy_name") == f"{_STRATEGY_NAME}_with_qc"]
        assert qc_rows, "No JSONL row found for QC-evaluated strategy"

        status = qc_rows[-1].get("status", "")
        valid_statuses = {"qc_success", "qc_error", "qc_fail", "syntax_error", "aider_failed", "skipped"}
        assert status in valid_statuses, (
            f"Unexpected status '{status}' in JSONL row — expected one of {valid_statuses}"
        )
