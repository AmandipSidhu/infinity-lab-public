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
    build_fitness_feedback — build enriched retry feedback from backtest metrics
    check_backtest_constraints — evaluate fitness constraints against spec
    build_strategy         — main orchestration loop

Critical invariants:
    - NEVER report success if the file was not actually written and validated
    - NEVER write a file that failed syntax check or LEAN compile
    - Each iteration prompt includes the previous error verbatim
    - After 3 Flash failures → switch to gemini-2.5-pro for remaining attempts
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
_DEFAULT_MODEL: str = "gemini-2.5-flash"   # stable alias — verified 2026-03-10
_FALLBACK_MODEL: str = "gemini-2.5-pro"    # stable alias — verified 2026-03-10
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
        model:  The Gemini model identifier (default: gemini-2.5-flash).

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
) -> tuple[bool, str, dict[str, Any]]:
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
        (True, "", {})       — LEAN compiled, backtest ran, all constraints pass.
        (False, error_msg, backtest_stats) — Failure at any stage; error_msg is
                              verbatim for feeding back into the next Gemini
                              iteration; backtest_stats is the raw metrics dict
                              (may be empty on compile/API failures).
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
        ), {}
    except RuntimeError as exc:
        # Covers LEAN compile errors, project creation failures, poll timeouts, etc.
        return False, f"LEAN compile or QC API error for spec '{spec_name}':\n{exc}", {}
    finally:
        if tmp_strategy_path:
            Path(tmp_strategy_path).unlink(missing_ok=True)

    backtest_stats: dict[str, Any] = summary.get("backtest_stats", {})

    # Check fitness constraints from the summary violations list
    violations: list[dict[str, Any]] = summary.get("violations", [])
    if violations:
        msg_lines = [f"  {v['constraint']}: {v['message']}" for v in violations]
        return False, "Backtest constraint violations:\n" + "\n".join(msg_lines), backtest_stats

    if not summary.get("passed", False):
        return False, (
            f"QC evaluation returned FAIL with no explicit violations recorded. "
            f"Result: {summary.get('result')}. "
            f"This may indicate a QC API evaluation framework error or missing "
            f"backtest statistics. "
            f"Stats: {backtest_stats}"
        ), backtest_stats

    return True, "", backtest_stats


def build_fitness_feedback(
    violations_msg: str,
    backtest_stats: dict[str, Any],
    spec: dict[str, Any],
) -> str:
    """Build an enriched feedback string from backtest metrics for the retry prompt.

    Combines the raw violation message with a structured block showing the full
    backtest metrics and dynamically generated "What went wrong / What to fix"
    sections so that Gemini has actionable signal on each retry.

    Args:
        violations_msg:  The raw violation message string from :func:`run_qc_upload_eval`.
        backtest_stats:  The ``backtest_stats`` dict returned from
                         ``upload_and_evaluate`` (may be empty).
        spec:            Parsed spec dict (used to read constraint thresholds).

    Returns:
        An enriched feedback string ready to pass to :func:`build_prompt`.
    """

    def _get_stat(keys: list[str]) -> float | None:
        """Return the first matching numeric value from backtest_stats."""
        for key in keys:
            val = backtest_stats.get(key)
            if val is not None:
                try:
                    # QC may return stats as strings like "0.38" or "12.5%"
                    return float(str(val).rstrip("%").replace(",", ""))
                except (ValueError, TypeError):
                    continue
        return None

    sharpe = _get_stat(["SharpeRatio", "sharpe_ratio", "Sharpe Ratio", "sharpe"])
    total_trades = _get_stat(["TotalTrades", "total_trades", "Total Trades", "Trades Made"])
    win_rate = _get_stat(["WinRate", "win_rate", "Win Rate"])
    net_profit = _get_stat(["NetProfit", "net_profit", "Net Profit"])
    annual_return = _get_stat(["AnnualReturn", "annual_return", "Annual Return", "Compounding Annual Return"])
    drawdown = _get_stat(["Drawdown", "MaxDrawdown", "max_drawdown", "Max Drawdown"])
    pl_ratio = _get_stat(["ProfitLossRatio", "profit_loss_ratio", "Profit-Loss Ratio"])
    loss_rate = _get_stat(["LossRate", "loss_rate", "Loss Rate"])

    # Extract required thresholds from spec for context in the feedback
    ac = spec.get("acceptance_criteria") or {}
    nested_targets = (spec.get("strategy") or {}).get("performance_targets") or {}
    required_sharpe = (
        float(ac.get("min_sharpe_ratio", 0.5))
        if "min_sharpe_ratio" in ac
        else float(nested_targets.get("sharpe_ratio_min", 0.5))
    )
    max_dd_spec = (
        float(ac.get("max_drawdown_pct", 100)) / 100.0
        if "max_drawdown_pct" in ac
        else float(nested_targets.get("max_drawdown_threshold", 1.0))
    )

    # Build metric lines — omit any that are unavailable
    metric_lines: list[str] = []
    if sharpe is not None:
        metric_lines.append(f"  Sharpe Ratio:    {sharpe:.2f}  (required: >= {required_sharpe:.2f})")
    if total_trades is not None:
        metric_lines.append(f"  Total Trades:    {int(total_trades)}")
    if win_rate is not None:
        # Win rate may come as 0-1 fraction or 0-100 percentage
        wr_display = win_rate if win_rate > 1.0 else win_rate * 100.0
        metric_lines.append(f"  Win Rate:        {wr_display:.1f}%")
    if net_profit is not None:
        metric_lines.append(f"  Net Profit:      {net_profit:+.1f}%")
    if annual_return is not None:
        ar_display = annual_return if annual_return > 1.0 else annual_return * 100.0
        metric_lines.append(f"  Annual Return:   {ar_display:.1f}%")
    if drawdown is not None:
        dd_display = drawdown if drawdown > 1.0 else drawdown * 100.0
        metric_lines.append(f"  Max Drawdown:    {dd_display:.1f}%")
    if pl_ratio is not None:
        metric_lines.append(f"  Profit/Loss:     {pl_ratio:.2f}")
    if loss_rate is not None:
        lr_display = loss_rate if loss_rate > 1.0 else loss_rate * 100.0
        metric_lines.append(f"  Loss Rate:       {lr_display:.1f}%")

    metrics_block = (
        "\n".join(metric_lines) if metric_lines else "  (no backtest metrics available)"
    )

    # Dynamic "What went wrong" diagnosis
    wrong_lines: list[str] = []
    fix_lines: list[str] = []

    trades_int = int(total_trades) if total_trades is not None else None
    wr_frac = (
        (win_rate / 100.0 if win_rate > 1.0 else win_rate)
        if win_rate is not None
        else None
    )
    dd_frac = (
        (drawdown / 100.0 if drawdown > 1.0 else drawdown)
        if drawdown is not None
        else None
    )

    # Threshold: 50 trades minimum for statistical significance; below this the
    # strategy likely has entry conditions that are too restrictive.
    if trades_int is not None and trades_int < 50:
        wrong_lines.append(
            f"- Only {trades_int} trades over the backtest period. Strategy is not trading enough."
        )
        fix_lines.append(
            "- Loosen entry conditions to generate more trades (target 100+ over backtest period)"
        )

    # Thresholds: win_rate < 45% AND P/L < 1.2 together indicate that losses
    # outweigh wins in expected value (breakeven requires wr/(1-wr) >= 1/pl_ratio).
    if wr_frac is not None and pl_ratio is not None and wr_frac < 0.45 and pl_ratio < 1.2:
        wrong_lines.append(
            f"- Win rate {wr_frac*100:.1f}% with P/L ratio {pl_ratio:.2f} — losses outweigh wins."
        )
        fix_lines.append(
            "- Tighten stop loss relative to profit target (P/L ratio should be >= 1.5)"
        )

    if dd_frac is not None and dd_frac > max_dd_spec:
        wrong_lines.append(
            f"- Max drawdown {dd_frac*100:.1f}% exceeds allowed threshold {max_dd_spec*100:.1f}%."
        )
        fix_lines.append(
            "- Reduce position size or add a drawdown circuit breaker"
        )

    if (
        sharpe is not None
        and sharpe < required_sharpe
        and (trades_int is None or trades_int >= 50)
    ):
        wrong_lines.append(
            f"- Sharpe {sharpe:.2f} below required {required_sharpe:.2f} despite adequate trade count."
        )
        fix_lines.append(
            "- Improve signal quality: consider adding a confirmation filter or tightening entry criteria"
        )

    if not wrong_lines:
        wrong_lines.append("- See violation details below for specific constraint failures.")

    if not fix_lines:
        fix_lines.append("- Refine strategy parameters based on the violation details below.")

    wrong_block = "\n".join(wrong_lines)
    fix_block = "\n".join(fix_lines)

    return (
        "PREVIOUS ATTEMPT FAILED — BACKTEST RESULTS:\n\n"
        f"Metrics from last run:\n{metrics_block}\n\n"
        f"What went wrong:\n{wrong_block}\n\n"
        f"What to fix:\n{fix_block}\n\n"
        f"Constraint violations:\n{violations_msg}\n\n"
        "Do NOT change the overall strategy structure — refine parameters only."
    )


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
      9. After _FLASH_SWITCH_AFTER Flash failures → switch to gemini-2.5-pro
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
        qc_ok, qc_error, backtest_stats = run_qc_upload_eval(spec_path, spec_name, code)
        if not qc_ok:
            # Enrich feedback with full metrics when backtest stats are available
            if backtest_stats:
                feedback = build_fitness_feedback(qc_error, backtest_stats, spec)
            else:
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
