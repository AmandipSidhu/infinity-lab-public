#!/usr/bin/env python3
"""Grinder retry loop — iterates toward a backtest quality goal using Aider.

Runs up to MAX_RETRIES attempts per strategy:
  1. Generate/revise strategy with Aider (includes backtest feedback on retries)
  2. Validate syntax with qc_quick_validate.py — if FAIL, feed error to Aider next attempt
  3. Run qc_upload_eval.py — compile + backtest via QC
  4. Parse result JSON and check quality goals
  5. Break early if goal met; on final attempt, log BEST_RESULT (does not fail CI)

Quality goals (defaults):
  Sharpe >= 2.0
  Max Drawdown <= 15%
  Total Trades >= 10

Usage:
    python scripts/grinder_loop.py \\
        --name orb_15min_base \\
        --prompt "Build an ORB strategy..." \\
        --strategy-file strategies/orb_15min_base.py \\
        --output-dir output

Exit codes:
  0 — Goal met, best result logged, or non-fatal outcome
  1 — Fatal configuration error (missing required args etc.)
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Goal constants (defaults — can be overridden via CLI args)
# ---------------------------------------------------------------------------

_DEFAULT_MAX_RETRIES: int = 3
_DEFAULT_SHARPE_GOAL: float = 2.0
_DEFAULT_MAX_DRAWDOWN_GOAL: float = 15.0   # percent
_DEFAULT_MIN_TRADES_GOAL: int = 10


# ---------------------------------------------------------------------------
# Goal evaluation
# ---------------------------------------------------------------------------


def _check_goal(
    result: dict[str, Any],
    sharpe_goal: float,
    max_drawdown_goal: float,
    min_trades_goal: int,
) -> tuple[bool, str]:
    """Return ``(goal_met, failure_guidance)`` based on backtest metrics.

    ``failure_guidance`` is a semicolon-separated string of improvement hints
    for each failing metric; empty when all goals are met.
    """
    sharpe: float | None = result.get("sharpe") or result.get("sharpe_ratio")
    drawdown: float | None = result.get("max_drawdown")
    trades: int | None = result.get("total_trades") or result.get("total_orders")
    net_profit: float | None = result.get("net_profit")

    failures: list[str] = []
    guidance: list[str] = []

    if sharpe is None or float(sharpe) < sharpe_goal:
        failures.append(f"Sharpe {sharpe} < {sharpe_goal}")
        # net_profit is a percentage value (e.g. 12.5 means +12.5%)
        if net_profit is not None and float(net_profit) < 0:
            guidance.append("Review signal direction and entry/exit logic")
        else:
            guidance.append("Improve signal quality or add regime filter")

    if drawdown is not None and float(drawdown) > max_drawdown_goal:
        failures.append(f"MaxDrawdown {drawdown}% > {max_drawdown_goal}%")
        guidance.append("Add position sizing limits or tighter stop loss")

    if trades is None or int(trades) < min_trades_goal:
        failures.append(f"Trades {trades} < {min_trades_goal}")
        guidance.append("Relax entry conditions or expand universe")

    if failures:
        return False, "; ".join(guidance)
    return True, ""


# ---------------------------------------------------------------------------
# Aider message builder
# ---------------------------------------------------------------------------


def _build_aider_message(
    prompt_content: str,
    attempt: int,
    prev_result: dict[str, Any] | None,
    sharpe_goal: float,
    max_drawdown_goal: float,
    min_trades_goal: int,
    syntax_error: str | None = None,
) -> str:
    """Return the Aider --message for this attempt.

    On attempt 1 (or when no previous result is available), returns the original
    prompt.  On subsequent attempts, prepends backtest feedback so Aider can
    iterate toward the quality goal.
    """
    if syntax_error:
        return (
            f"The strategy failed syntax validation with this error:\n{syntax_error}\n\n"
            f"Fix the validation error and regenerate the strategy.\n\n"
            f"Original task:\n{prompt_content}"
        )

    if attempt == 1 or prev_result is None:
        return prompt_content

    goal_met, guidance = _check_goal(
        prev_result, sharpe_goal, max_drawdown_goal, min_trades_goal
    )
    status = "GOAL_MET" if goal_met else "NEEDS_IMPROVEMENT"

    sharpe = prev_result.get("sharpe") or prev_result.get("sharpe_ratio")
    drawdown = prev_result.get("max_drawdown")
    trades = prev_result.get("total_trades") or prev_result.get("total_orders")
    net_profit = prev_result.get("net_profit")

    msg = (
        f"Backtest attempt {attempt - 1} result:\n"
        f"- Sharpe Ratio: {sharpe}\n"
        f"- Max Drawdown: {drawdown}%\n"
        f"- Total Trades: {trades}\n"
        f"- Net Profit: {net_profit}%\n\n"
        f"Goal: Sharpe >= {sharpe_goal}, Max Drawdown <= {max_drawdown_goal}%, "
        f"Trades >= {min_trades_goal}\n"
        f"Status: {status}\n"
    )
    if not goal_met:
        msg += f"\nRevise the strategy. Focus on: {guidance}"
    return msg


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grinder retry loop with backtest feedback")
    parser.add_argument("--name", required=True, help="Strategy name (snake_case)")
    parser.add_argument("--prompt", required=True, help="Original prompt content")
    parser.add_argument("--strategy-file", required=True, help="Path to strategy .py file")
    parser.add_argument("--spec-file", default=None, help="Path to spec YAML (optional)")
    parser.add_argument("--output-dir", default="output", help="Output directory")
    parser.add_argument(
        "--max-retries", type=int, default=_DEFAULT_MAX_RETRIES,
        help=f"Max Aider+backtest attempts (default: {_DEFAULT_MAX_RETRIES})",
    )
    parser.add_argument(
        "--sharpe-goal", type=float, default=_DEFAULT_SHARPE_GOAL,
        help=f"Target Sharpe Ratio (default: {_DEFAULT_SHARPE_GOAL})",
    )
    parser.add_argument(
        "--max-drawdown-goal", type=float, default=_DEFAULT_MAX_DRAWDOWN_GOAL,
        help=f"Max Drawdown %% ceiling (default: {_DEFAULT_MAX_DRAWDOWN_GOAL})",
    )
    parser.add_argument(
        "--min-trades-goal", type=int, default=_DEFAULT_MIN_TRADES_GOAL,
        help=f"Minimum total trades (default: {_DEFAULT_MIN_TRADES_GOAL})",
    )
    parser.add_argument(
        "--model", default=None,
        help="Aider model override (default: $AIDER_MODEL env var)",
    )
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    strategy_file: str = args.strategy_file
    result_json_path = output_dir / f"{args.name}_qc_result.json"
    loop_summary_path = output_dir / f"{args.name}_loop_summary.json"

    model: str = args.model or os.environ.get("AIDER_MODEL", "gemini/gemini-2.5-flash")

    # Track state across attempts
    prev_result: dict[str, Any] | None = None
    prev_syntax_error: str | None = None
    best_result: dict[str, Any] | None = None
    best_sharpe: float = -999.0
    final_attempt: int = 1
    goal_met: bool = False
    aider_outcome: str = "failure"
    validate_outcome: str = "failure"
    qc_outcome: str = "failure"

    for attempt in range(1, args.max_retries + 1):
        final_attempt = attempt
        print(f"\n[grinder_loop] === Attempt {attempt}/{args.max_retries} ===")

        # ------------------------------------------------------------------
        # Step 1: Run Aider to generate / revise strategy
        # ------------------------------------------------------------------
        aider_message = _build_aider_message(
            prompt_content=args.prompt,
            attempt=attempt,
            prev_result=prev_result,
            sharpe_goal=args.sharpe_goal,
            max_drawdown_goal=args.max_drawdown_goal,
            min_trades_goal=args.min_trades_goal,
            syntax_error=prev_syntax_error,
        )
        aider_cmd = [
            "aider",
            "--model", model,
            "--read", "config/qc_api_reference.txt",
            "--read", "config/aider_system_prompt_qsc.txt",
            "--yes-always", "--no-git",
            "--file", strategy_file,
            "--message", aider_message,
        ]
        print(f"[grinder_loop] Running Aider (attempt {attempt})…")
        aider_proc = subprocess.run(aider_cmd)
        aider_outcome = "success" if aider_proc.returncode == 0 else "failure"
        print(f"[grinder_loop] Aider: {aider_outcome}")
        if aider_outcome != "success":
            prev_syntax_error = None
            continue

        # ------------------------------------------------------------------
        # Step 2: Validate syntax
        # ------------------------------------------------------------------
        validate_cmd = ["python", "scripts/qc_quick_validate.py", strategy_file]
        validate_proc = subprocess.run(validate_cmd, capture_output=True, text=True)
        validate_outcome = "success" if validate_proc.returncode == 0 else "failure"
        print(f"[grinder_loop] Validate: {validate_outcome}")
        if validate_outcome != "success":
            # Feed the validation error to Aider on the next attempt
            prev_syntax_error = (validate_proc.stdout + validate_proc.stderr).strip()
            prev_result = None
            continue

        prev_syntax_error = None

        # ------------------------------------------------------------------
        # Step 3: Compile + backtest via qc_upload_eval.py
        # ------------------------------------------------------------------
        qc_cmd = [
            sys.executable, "scripts/qc_upload_eval.py",
            "--strategy", strategy_file,
            "--output", str(result_json_path),
        ]
        if args.spec_file:
            qc_cmd += ["--spec", args.spec_file]
        print(f"[grinder_loop] Submitting to QC (attempt {attempt})…")
        qc_proc = subprocess.run(qc_cmd)
        qc_outcome = "success" if qc_proc.returncode == 0 else "failure"
        print(f"[grinder_loop] QC: {qc_outcome}")

        # ------------------------------------------------------------------
        # Step 4: Parse result JSON
        # ------------------------------------------------------------------
        current_result: dict[str, Any] = {}
        if result_json_path.exists():
            try:
                current_result = json.loads(result_json_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                print(f"[grinder_loop] Warning: could not parse QC result JSON: {exc}")

        prev_result = current_result

        # Track best result by Sharpe
        sharpe_val = current_result.get("sharpe") or current_result.get("sharpe_ratio")
        current_sharpe: float = float(sharpe_val) if sharpe_val is not None else -999.0
        if current_sharpe > best_sharpe:
            best_sharpe = current_sharpe
            best_result = current_result

        # ------------------------------------------------------------------
        # Step 5: Check goal
        # ------------------------------------------------------------------
        goal_met, _ = _check_goal(
            current_result,
            args.sharpe_goal,
            args.max_drawdown_goal,
            args.min_trades_goal,
        )
        if goal_met:
            print(f"[grinder_loop] ✅ Goal met on attempt {attempt}!")
            break
        if attempt < args.max_retries:
            print(
                f"[grinder_loop] Goal not met after attempt {attempt}. "
                f"Sharpe={current_sharpe}. Retrying with feedback…"
            )
        else:
            print(
                f"[grinder_loop] Max retries reached. Best Sharpe={best_sharpe}. "
                f"Logging best result."
            )

    # ------------------------------------------------------------------
    # Write loop summary JSON for downstream steps (log_grinder_result.py etc.)
    # ------------------------------------------------------------------
    summary: dict[str, Any] = {
        "name": args.name,
        "total_attempts": final_attempt,
        "goal_met": goal_met,
        "aider_outcome": aider_outcome,
        "validate_outcome": validate_outcome,
        "qc_outcome": qc_outcome,
        "best_sharpe": best_sharpe if best_sharpe > -999.0 else None,
        "best_result": best_result,
    }
    loop_summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[grinder_loop] Loop summary written to {loop_summary_path}")

    # Always exit 0 — hitting max retries without meeting the goal is not a CI failure
    return 0


if __name__ == "__main__":
    sys.exit(main())
