#!/usr/bin/env python3
"""Log a QSC grinder build result to output/grinder_results.jsonl.

Each invocation appends one JSON line to the results file.

Usage:
    python scripts/log_grinder_result.py \\
        --prompt "Build ORB strategy..." \\
        --name "orb_15min_base" \\
        --aider "success" \\
        --validate "success" \\
        --qc-result output/orb_15min_base_qc_result.json

Arguments:
    --prompt       Original natural language prompt text
    --name         Generated strategy name (snake_case)
    --aider        Aider step outcome: success | failure | skipped
    --validate     Validation step outcome: success | failure | skipped
    --qc-result    Path to QC backtest result JSON (optional; may not exist)
    --priority     Priority tag (PRIORITY | INDEPENDENT | IF-PREVIOUS-PASSED | LOW-PRIORITY)
    --parent       Parent strategy name for IF-PREVIOUS-PASSED builds
    --output       Path to output JSONL file (default: output/grinder_results.jsonl)
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def parse_qc_result(qc_result_path: str | None) -> dict:
    """Parse QC backtest result JSON and return relevant metrics."""
    defaults: dict = {
        "qc_submitted": False,
        "qc_backtest_id": None,
        "qc_sharpe": None,
        "qc_total_orders": None,
        "qc_net_pnl_pct": None,
        "qc_max_drawdown": None,
        "qc_error": None,
    }

    if not qc_result_path:
        return defaults

    path = Path(qc_result_path)
    if not path.exists():
        return defaults

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        defaults["qc_error"] = f"Could not read QC result: {exc}"
        return defaults

    defaults["qc_submitted"] = True
    defaults["qc_backtest_id"] = data.get("backtest_id") or data.get("BacktestId")

    # Support both nested statistics and flat result formats
    stats = data.get("statistics", data.get("Statistics", {})) or {}

    def _get_float(key_variants: list[str]) -> float | None:
        for key in key_variants:
            raw = stats.get(key)
            if raw is None:
                raw = data.get(key)
            if raw is not None:
                try:
                    return float(str(raw).replace("%", "").replace(",", ""))
                except (ValueError, TypeError):
                    pass
        return None

    defaults["qc_sharpe"] = _get_float(["Sharpe Ratio", "sharpe_ratio", "sharpe"])
    defaults["qc_total_orders"] = _get_float(["Total Orders", "total_orders", "totalOrders"])
    defaults["qc_net_pnl_pct"] = _get_float(["Net Profit", "net_profit", "netProfit"])
    defaults["qc_max_drawdown"] = _get_float(
        ["Drawdown", "Max Drawdown", "max_drawdown", "maxDrawdown"]
    )
    defaults["qc_error"] = data.get("error") or data.get("Error")

    return defaults


def determine_status(
    aider_outcome: str,
    validate_outcome: str,
    qc_metrics: dict,
    priority: str,
) -> str:
    """Derive overall build status from component outcomes."""
    if priority == "IF-PREVIOUS-PASSED" and aider_outcome == "skipped":
        return "skipped_parent_failed"
    if aider_outcome not in ("success",):
        return "aider_failed"
    if validate_outcome not in ("success",):
        return "syntax_error"
    if qc_metrics.get("qc_submitted"):
        if qc_metrics.get("qc_error"):
            return "qc_error"
        return "qc_success"
    return "qc_not_submitted"


def main() -> None:
    parser = argparse.ArgumentParser(description="Log grinder build result to JSONL")
    parser.add_argument("--prompt", required=True, help="Original prompt text")
    parser.add_argument("--name", required=True, help="Strategy name (snake_case)")
    parser.add_argument(
        "--aider",
        required=True,
        help="Aider step outcome: success | failure | skipped",
    )
    parser.add_argument(
        "--validate",
        required=True,
        help="Validation step outcome: success | failure | skipped",
    )
    parser.add_argument(
        "--qc-result",
        default=None,
        help="Path to QC result JSON file (optional)",
    )
    parser.add_argument(
        "--priority",
        default="INDEPENDENT",
        choices=["PRIORITY", "INDEPENDENT", "IF-PREVIOUS-PASSED", "LOW-PRIORITY"],
        help="Priority tag for this build",
    )
    parser.add_argument(
        "--parent",
        default=None,
        help="Parent strategy name (for IF-PREVIOUS-PASSED builds)",
    )
    parser.add_argument(
        "--output",
        default="output/grinder_results.jsonl",
        help="Path to output JSONL file",
    )
    args = parser.parse_args()

    qc_metrics = parse_qc_result(args.qc_result)

    status = determine_status(args.aider, args.validate, qc_metrics, args.priority)

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prompt": args.prompt,
        "strategy_name": args.name,
        "priority": args.priority,
        "parent": args.parent,
        "aider_success": args.aider == "success",
        "aider_tier": "gemini-flash",
        "syntax_valid": args.validate == "success",
        "qc_submitted": qc_metrics["qc_submitted"],
        "qc_backtest_id": qc_metrics["qc_backtest_id"],
        "qc_sharpe": qc_metrics["qc_sharpe"],
        "qc_total_orders": qc_metrics["qc_total_orders"],
        "qc_net_pnl_pct": qc_metrics["qc_net_pnl_pct"],
        "qc_max_drawdown": qc_metrics["qc_max_drawdown"],
        "qc_error": qc_metrics["qc_error"],
        "status": status,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")

    print(f"Logged: {args.name} → {status} → {output_path}")


if __name__ == "__main__":
    main()
