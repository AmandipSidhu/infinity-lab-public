#!/usr/bin/env python3
"""Gemini-based QC LEAN strategy builder.

Implements a direct Gemini API build loop with iterative error feedback,
mirroring the QC MIA 2 iterative loop pattern.  Replaces the aider-based
strategy builder which suffered from a broken feedback signal (pytest on
mocks rather than real LEAN errors).

Functions (in order):
    load_spec              — load + validate YAML spec
    build_prompt           — construct Gemini prompt from spec fields
    call_gemini            — invoke Gemini Flash / Pro API
    extract_code           — parse ```python ... ``` from response
    syntax_check           — py_compile validation
    run_qc_upload_eval     — QC REST upload → LEAN compile → backtest → eval
    check_backtest_constraints — evaluate fitness constraints against spec
    build_strategy         — main orchestration loop

Critical invariants:
    - NEVER report success if the file was not actually written and validated
    - NEVER write a file that failed syntax check or LEAN compile
    - Each iteration prompt includes the previous error verbatim
    - After 3 Flash failures → switch to gemini-2.5-pro-preview-03-25 for remaining attempts
    - After 5 total iterations without pass → write FAILED status, no stub
"""

import argparse
import os
import py_compile
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Local imports — add scripts dir to path so sibling modules resolve
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).parent))

from prompt_template import build_strategy_prompt  # noqa: E402
from qc_upload_eval import (  # noqa: E402
    MCPConnectionError,
    evaluate_fitness,
    upload_and_evaluate,
)
from spec_validator import validate_spec  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "").strip()
_DEFAULT_MODEL: str = "gemini-2.5-flash-preview-04-17"
_FALLBACK_MODEL: str = "gemini-2.5-pro-preview-03-25"
# After this many Flash failures, switch to Pro for remaining attempts
_FLASH_SWITCH_AFTER: int = 3


# ---------------------------------------------------------------------------
# Step 1 — load + validate spec
# ---------------------------------------------------------------------------


def load_spec(spec_path: str) -> dict[str, Any]:
    """Load and validate a YAML spec file.

    Args:
        spec_path: Path to the spec YAML file.

    Returns:
        Parsed spec dict.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError:        If the spec contains SVR ERROR-level findings.
        yaml.YAMLError:    If the YAML is malformed.
    """
    path = Path(spec_path)
    if not path.is_file():
        raise FileNotFoundError(f"Spec file not found: {spec_path}")

    with path.open(encoding="utf-8") as fh:
        spec: dict[str, Any] = yaml.safe_load(fh) or {}

    findings = validate_spec(spec)
    errors = [f for f in findings if f["severity"] == "ERROR"]
    if errors:
        error_msgs = "\n".join(f"  {f['code']}: {f['message']}" for f in errors)
        raise ValueError(
            f"Spec validation failed with {len(errors)} error(s):\n{error_msgs}"
        )

    return spec


# ---------------------------------------------------------------------------
# Step 2 — build prompt
# ---------------------------------------------------------------------------


def build_prompt(spec: dict[str, Any], feedback: str | None = None) -> str:
    """Build a Gemini prompt from the spec, optionally with prior error feedback.

    Args:
        spec:     Parsed spec dict (from :func:`load_spec`).
        feedback: Verbatim error string from the previous failed iteration.

    Returns:
        Complete prompt string ready to send to the Gemini API.
    """
    return build_strategy_prompt(spec, feedback)


# ---------------------------------------------------------------------------
# Step 3 — call Gemini API
# ---------------------------------------------------------------------------


def call_gemini(prompt: str, model: str = _DEFAULT_MODEL) -> str:
    """Call the Gemini API and return the raw text response.

    Uses the google-genai SDK (google.genai), which replaces the deprecated
    google-generativeai (google.generativeai) package.

    Args:
        prompt: The prompt string to send.
        model:  The Gemini model identifier (default: gemini-2.5-flash-preview-04-17).

    Returns:
        Raw text response from Gemini.

    Raises:
        RuntimeError: If GEMINI_API_KEY is missing or the API call fails.
    """
    if not _GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable is not set — cannot call Gemini API"
        )

    try:
        from google import genai  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "google-genai package is not installed; "
            "run: pip install google-genai"
        ) from exc

    client = genai.Client(api_key=_GEMINI_API_KEY)

    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
        )
    except Exception as exc:
        raise RuntimeError(f"Gemini API call failed (model={model}): {exc}") from exc

    return str(response.text)


# ---------------------------------------------------------------------------
# Step 4 — extract code block
# ---------------------------------------------------------------------------


def extract_code(response: str) -> str:
    """Extract the Python code block from a Gemini response.

    Looks for a ```python ... ``` fenced block.

    Args:
        response: Raw text response from :func:`call_gemini`.

    Returns:
        The Python source code (without the fence markers).

    Raises:
        ValueError: If no ```python ... ``` block is found.
    """
    pattern = re.compile(r"```python\s*\n(.*?)```", re.DOTALL)
    match = pattern.search(response)
    if not match:
        raise ValueError(
            "No ```python ... ``` code block found in Gemini response. "
            f"Response preview: {response[:300]!r}"
        )
    return match.group(1).strip()


# ---------------------------------------------------------------------------
# Step 5 — syntax check
# ---------------------------------------------------------------------------


def syntax_check(code: str) -> tuple[bool, str]:
    """Run a py_compile syntax check on a Python source string.

    Args:
        code: Python source code to validate.

    Returns:
        (True, "")          — Syntax is valid.
        (False, error_msg)  — Syntax is invalid; error_msg contains the
                              exact py_compile error string.
    """
    tmp_path: str = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(code)
            tmp_path = fh.name
        py_compile.compile(tmp_path, doraise=True)
        return True, ""
    except py_compile.PyCompileError as exc:
        return False, str(exc)
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Step 6 — QC upload + LEAN compile + backtest + fitness eval
# ---------------------------------------------------------------------------


def run_qc_upload_eval(
    spec_path: str, spec_name: str, code: str
) -> tuple[bool, str]:
    """Upload strategy to QC, run LEAN compile + backtest, evaluate fitness.

    Writes *code* to a temporary file, calls :func:`upload_and_evaluate` from
    ``qc_upload_eval``, then inspects the returned summary (specifically the
    ``violations`` list and the ``passed`` flag) to determine whether all
    backtest constraints are satisfied.

    Args:
        spec_path: Path to the spec YAML file (required by upload_and_evaluate).
        spec_name: Human-readable spec name (used for logging).
        code:      Python source code to evaluate.

    Returns:
        (True, "")          — LEAN compiled, backtest ran, all constraints pass.
        (False, error_msg)  — Failure at any stage; error_msg is verbatim for
                              feeding back into the next Gemini iteration.
    """
    tmp_strategy_path: str = ""
    summary: dict[str, Any] = {}
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(code)
            tmp_strategy_path = fh.name
        print(f"[gemini_builder] QC eval: uploading strategy for spec '{spec_name}'")
        summary = upload_and_evaluate(Path(spec_path), Path(tmp_strategy_path))
    except MCPConnectionError as exc:
        return False, (
            f"QC REST API unreachable — cannot complete backtest: {exc}\n"
            "Ensure QC_USER_ID and QC_API_TOKEN are set and the QC REST API is reachable."
        )
    except RuntimeError as exc:
        # Covers LEAN compile errors, project creation failures, poll timeouts, etc.
        return False, f"LEAN compile or QC API error for spec '{spec_name}':\n{exc}"
    finally:
        if tmp_strategy_path:
            Path(tmp_strategy_path).unlink(missing_ok=True)

    # Check fitness constraints from the summary violations list
    violations: list[dict[str, Any]] = summary.get("violations", [])
    if violations:
        msg_lines = [f"  {v['constraint']}: {v['message']}" for v in violations]
        return False, "Backtest constraint violations:\n" + "\n".join(msg_lines)

    if not summary.get("passed", False):
        return False, (
            f"QC evaluation returned FAIL with no explicit violations recorded. "
            f"Result: {summary.get('result')}. "
            f"This may indicate a QC API evaluation framework error or missing "
            f"backtest statistics. "
            f"Stats: {summary.get('backtest_stats', {})}"
        )

    return True, ""


# ---------------------------------------------------------------------------
# Step 7 — check backtest constraints (standalone utility)
# ---------------------------------------------------------------------------


def check_backtest_constraints(
    results: dict[str, Any], spec: dict[str, Any]
) -> tuple[bool, str]:
    """Evaluate FitnessTracker constraints against raw backtest stats.

    This function is a thin wrapper around :func:`evaluate_fitness` from
    ``qc_upload_eval``.  It normalises spec constraint keys so both the flat
    format (``acceptance_criteria.min_sharpe_ratio``) and the nested format
    (``strategy.performance_targets.sharpe_ratio_min``) are handled.

    Args:
        results: Backtest stats dict (e.g. the ``backtest_stats`` sub-dict
                 from an ``upload_and_evaluate`` summary, or the full result).
        spec:    Parsed spec dict.

    Returns:
        (True, "")          — All constraints satisfied.
        (False, error_msg)  — One or more violations; error_msg is verbatim.
    """
    performance_targets: dict[str, Any] = {}

    # Flat spec format: acceptance_criteria
    ac = spec.get("acceptance_criteria") or {}
    if isinstance(ac, dict):
        if "min_sharpe_ratio" in ac:
            performance_targets.setdefault(
                "sharpe_ratio_min", float(ac["min_sharpe_ratio"])
            )
        if "max_drawdown_pct" in ac:
            # acceptance_criteria stores as a whole percentage (e.g. 15.0 = 15%)
            performance_targets.setdefault(
                "max_drawdown_threshold", float(ac["max_drawdown_pct"]) / 100.0
            )

    # Nested spec format: strategy.performance_targets
    nested_targets = (spec.get("strategy") or {}).get("performance_targets") or {}
    if isinstance(nested_targets, dict):
        performance_targets.update(nested_targets)

    violations = evaluate_fitness(results, performance_targets)
    if not violations:
        return True, ""

    msg_lines = [f"  {v['constraint']}: {v['message']}" for v in violations]
    return False, "Backtest constraint violations:\n" + "\n".join(msg_lines)


# ---------------------------------------------------------------------------
# Step 8-10 — main build loop
# ---------------------------------------------------------------------------


def _get_spec_name(spec: dict[str, Any], spec_path: str) -> str:
    """Derive a filesystem-safe strategy name from the spec or file stem."""
    raw_name = (spec.get("metadata") or {}).get("name")
    if raw_name:
        return re.sub(r"[^a-z0-9_]", "_", str(raw_name).lower()).strip("_")
    return Path(spec_path).stem


def build_strategy(spec_path: str, max_iterations: int = 5) -> bool:
    """Build a QC LEAN strategy from the spec using iterative Gemini prompting.

    Loop (up to max_iterations):
      1. Load + validate spec (import from spec_validator)
      2. Build prompt from spec (reads all fields from spec — no hardcoded values)
      3. Call Gemini Flash API
      4. Extract ```python ... ``` code block
      5. py_compile syntax check — on fail, feed error back, increment iteration
      6. QC upload + LEAN compile + backtest — on fail, feed error back, increment
      7. Fitness constraint check — on fail, feed violations back, increment
      8. All gates pass → write strategies/{spec_name}/main.py, return True
      9. After _FLASH_SWITCH_AFTER Flash failures → switch to gemini-2.5-pro-preview-03-25
     10. After max_iterations without pass → log FAILED, return False (no stub written)

    Args:
        spec_path:      Path to the strategy spec YAML.
        max_iterations: Maximum total iterations (default 5).

    Returns:
        True if a validated strategy file was written; False otherwise.
    """
    # Step 1: load + validate spec
    try:
        spec = load_spec(spec_path)
    except FileNotFoundError as exc:
        print(f"[gemini_builder] ERROR: {exc}", file=sys.stderr)
        return False
    except (ValueError, yaml.YAMLError) as exc:
        print(f"[gemini_builder] Spec validation failed: {exc}", file=sys.stderr)
        return False

    spec_name = _get_spec_name(spec, spec_path)
    print(f"[gemini_builder] Starting build for spec '{spec_name}' ({spec_path})")
    print(f"[gemini_builder] Max iterations: {max_iterations}")

    feedback: str | None = None
    model: str = _DEFAULT_MODEL

    for iteration in range(1, max_iterations + 1):
        # Switch from Flash to Pro after _FLASH_SWITCH_AFTER consecutive failures
        if iteration == _FLASH_SWITCH_AFTER + 1:
            print(
                f"[gemini_builder] {_FLASH_SWITCH_AFTER} Flash iterations exhausted — "
                f"switching to {_FALLBACK_MODEL}"
            )
            model = _FALLBACK_MODEL

        print(
            f"[gemini_builder] === Iteration {iteration}/{max_iterations} "
            f"(model={model}) ==="
        )

        # Step 2: build prompt
        prompt = build_prompt(spec, feedback)

        # Step 3: call Gemini
        try:
            response = call_gemini(prompt, model=model)
        except RuntimeError as exc:
            feedback = f"Gemini API error: {exc}"
            print(f"[gemini_builder] {feedback}", file=sys.stderr)
            continue

        # Step 4: extract code block
        try:
            code = extract_code(response)
        except ValueError as exc:
            feedback = str(exc)
            print(f"[gemini_builder] Code extraction failed: {feedback}", file=sys.stderr)
            continue

        # Step 5: syntax check
        syntax_ok, syntax_error = syntax_check(code)
        if not syntax_ok:
            feedback = f"Python syntax error (fix before regenerating):\n{syntax_error}"
            print(f"[gemini_builder] Syntax check failed:\n{syntax_error}", file=sys.stderr)
            continue

        print(f"[gemini_builder] Syntax check passed ({len(code)} chars)")

        # Step 6+7: QC upload → LEAN compile → backtest → fitness constraints
        qc_ok, qc_error = run_qc_upload_eval(spec_path, spec_name, code)
        if not qc_ok:
            feedback = qc_error
            print(f"[gemini_builder] QC eval failed:\n{qc_error}", file=sys.stderr)
            continue

        # Step 8: all gates passed — write the strategy file
        output_dir = Path("strategies") / spec_name
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / "main.py"
        output_file.write_text(code, encoding="utf-8")

        print(
            f"[gemini_builder] SUCCESS: strategy written to {output_file} "
            f"(iteration {iteration}/{max_iterations})"
        )
        return True

    # Step 10: exhausted all iterations without a successful build
    print(
        f"[gemini_builder] FAILED: could not build a passing strategy for "
        f"'{spec_name}' after {max_iterations} iteration(s). "
        f"No file written.",
        file=sys.stderr,
    )
    return False


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Usage:
        python scripts/gemini_builder.py --spec specs/vwap_probe.yaml
        python scripts/gemini_builder.py --spec specs/smoke_rsi_momentum.yaml --max-iterations 3
    """
    parser = argparse.ArgumentParser(
        description="Gemini-based QC LEAN strategy builder"
    )
    parser.add_argument(
        "--spec", required=True, help="Path to the strategy spec YAML file"
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=5,
        help="Maximum number of build iterations (default: 5)",
    )
    args = parser.parse_args(argv)

    success = build_strategy(args.spec, max_iterations=args.max_iterations)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
