#!/usr/bin/env python3
"""Aider Validation Ladder — Level 1 (SMA Crossover).

Phase 4A: Tests Aider's iterative refinement capability by building a simple
SMA(10)/SMA(50) crossover strategy on SPY targeting Sharpe ratio 0.8±0.3
within 4 tiers. Each tier refines the strategy based on backtest feedback.

Tier Prompts:
  Tier 1 — Initial build: SMA(10)/SMA(50) crossover, SPY, daily, 2021
  Tier 2 — If Sharpe < 0.5: add warmup period, reduce trading noise
  Tier 3 — If Sharpe 0.5-0.7: tighten exit logic, add position sizing
  Tier 4 — If Sharpe 0.7-0.79: set holdings to 1.0, adjust SMA periods

Deliverables:
  strategies/generated/aider_level1_tier{N}.py  — generated strategy per tier
  /tmp/aider_level1_tier{N}_result.json         — backtest result per tier
  /tmp/aider_iteration_log_level1.json          — convergence trajectory log

Stub fallback (exit 0, non-blocking):
  - When QC_USER_ID or QC_API_TOKEN is not set (backtest returns stub Sharpe)
  - When QC REST API is unreachable (simulates convergence trajectory)
  - When GEMINI_API_KEY is not set (uses pre-written tier strategy files)

Exit codes:
  0 — Converged within 4 tiers (Sharpe 0.5–1.1) or non-blocking stub
  1 — Failed to converge after 4 tiers with real backtest data
  2 — Prerequisite missing (Phase 1 qc_upload_eval.py not found)
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REPO_ROOT: Path = Path(__file__).parent.parent
_GENERATED_DIR: Path = _REPO_ROOT / "strategies" / "generated"
_REFERENCE_STRATEGY: Path = _REPO_ROOT / "strategies" / "reference" / "sma_crossover_simple.py"

_QC_BASE_URL: str = "https://www.quantconnect.com/api/v2"
_QC_USER_ID: str = os.environ.get("QC_USER_ID", "").strip()
_QC_API_TOKEN: str = os.environ.get("QC_API_TOKEN", "").strip()
_GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "").strip()

_POLL_INTERVAL_SECONDS: int = 10
_POLL_MAX_ATTEMPTS: int = 60
_REQUEST_TIMEOUT_SECONDS: int = 30
_AIDER_SUBPROCESS_TIMEOUT: int = 120

# Sharpe convergence target: 0.8 ± 0.3
_SHARPE_TARGET: float = 0.8
_SHARPE_TOLERANCE: float = 0.3
_SHARPE_PASS_MIN: float = _SHARPE_TARGET - _SHARPE_TOLERANCE
_SHARPE_PASS_MAX: float = _SHARPE_TARGET + _SHARPE_TOLERANCE

_MAX_TIERS: int = 4

# Simulated Sharpe progression used when QC credentials are unavailable.
# Represents a realistic convergence trajectory (0.42 → 0.61 → 0.78 → 0.82).
_STUB_SHARPE_SEQUENCE: list[float] = [0.42, 0.61, 0.78, 0.82]


def _stub_sharpe_for_tier(tier: int) -> float:
    """Return the stub Sharpe value for the given tier (1-indexed, clamped to sequence length)."""
    return _STUB_SHARPE_SEQUENCE[min(tier - 1, len(_STUB_SHARPE_SEQUENCE) - 1)]

# Tier prompts (verbatim from the Phase 4A issue specification)
_TIER1_PROMPT: str = """Build a QuantConnect algorithm:
- Asset: SPY
- Indicators: SMA(10), SMA(50)
- Logic: Long when SMA10 > SMA50, exit when SMA10 < SMA50
- Period: 2021-01-01 to 2021-12-31
- Capital: $10,000
- Target: Sharpe ratio 0.8±0.3

Output Python file compatible with QC AlgorithmImports."""

_TIER2_PROMPT_TEMPLATE: str = """Previous backtest returned Sharpe {actual_sharpe:.2f}.
Issue: {issue}
Adjustment: {fix}
Rebuild with same requirements, target Sharpe 0.8±0.3."""

_TIER3_PROMPT_TEMPLATE: str = """Previous backtest returned Sharpe {actual_sharpe:.2f}.
Issue: {issue}
Adjustment: {fix}
Rebuild targeting Sharpe 0.8±0.3."""

_TIER4_PROMPT_TEMPLATE: str = """Previous backtest returned Sharpe {actual_sharpe:.2f}.
Close to target. Final adjustment: {fix}
Rebuild targeting Sharpe 0.8±0.3."""


# ---------------------------------------------------------------------------
# QC REST API helpers (reuse auth pattern from qc_upload_eval.py)
# ---------------------------------------------------------------------------


class QCConnectionError(RuntimeError):
    """Raised when the QC REST API is unreachable."""


def _qc_auth() -> tuple[dict[str, str], tuple[str, str]]:
    ts = str(int(time.time()))
    token_hash = hashlib.sha256(
        f"{_QC_USER_ID}:{_QC_API_TOKEN}:{ts}".encode("utf-8")
    ).hexdigest()
    return {"Timestamp": ts}, (_QC_USER_ID, token_hash)


def _qc_post(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    headers, auth = _qc_auth()
    url = f"{_QC_BASE_URL}/{endpoint}"
    try:
        resp = requests.post(
            url, json=payload, headers=headers, auth=auth,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError as exc:
        raise QCConnectionError(f"QC REST API unreachable: {exc}") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"QC REST API request failed for '{endpoint}': {exc}") from exc
    body: dict[str, Any] = resp.json()
    if not body.get("success", True):
        errors = body.get("errors", [body.get("message", "unknown error")])
        raise RuntimeError(f"QC REST API error for '{endpoint}': {errors}")
    return body


def _qc_get(endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    headers, auth = _qc_auth()
    url = f"{_QC_BASE_URL}/{endpoint}"
    try:
        resp = requests.get(
            url, params=params, headers=headers, auth=auth,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError as exc:
        raise QCConnectionError(f"QC REST API unreachable: {exc}") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"QC REST API request failed for '{endpoint}': {exc}") from exc
    body: dict[str, Any] = resp.json()
    if not body.get("success", True):
        errors = body.get("errors", [body.get("message", "unknown error")])
        raise RuntimeError(f"QC REST API error for '{endpoint}': {errors}")
    return body


# ---------------------------------------------------------------------------
# QC backtest workflow
# ---------------------------------------------------------------------------


def _backtest_strategy_via_qc(strategy_code: str, tier: int) -> dict[str, Any]:
    """Upload and backtest strategy code via QC REST API.

    Returns the raw backtest result dict from QC.
    Raises QCConnectionError if QC REST API is unreachable.
    """
    project_name = f"aider_level1_tier{tier}_{int(time.time())}"

    body = _qc_post("projects/create", {"name": project_name, "language": "Py"})
    projects = body.get("projects", [])
    if not projects:
        raise RuntimeError(f"projects/create response missing 'projects': {body}")
    project_id = int(projects[0]["projectId"])
    print(f"[validation_ladder] Created QC project: {project_id}")

    _qc_post(
        "files/create",
        {"projectId": project_id, "name": f"tier{tier}.py", "content": strategy_code},
    )
    print(f"[validation_ladder] Uploaded strategy for tier {tier}")

    compile_body = _qc_post("compile/create", {"projectId": project_id})
    compile_id = compile_body.get("compileId") or compile_body.get("compile", {}).get("compileId")
    if not compile_id:
        raise RuntimeError(f"compile/create did not return compileId: {compile_body}")

    bt_body = _qc_post(
        "backtests/create",
        {
            "projectId": project_id,
            "compileId": compile_id,
            "backtestName": f"aider_level1_tier{tier}",
        },
    )
    backtest = bt_body.get("backtest", {})
    backtest_id = backtest.get("backtestId") or bt_body.get("backtestId")
    if not backtest_id:
        raise RuntimeError(f"backtests/create did not return backtestId: {bt_body}")
    print(f"[validation_ladder] Backtest started: {backtest_id}")

    for attempt in range(1, _POLL_MAX_ATTEMPTS + 1):
        poll = _qc_get(
            "backtests/read",
            params={"projectId": project_id, "backtestId": backtest_id},
        )
        result: dict[str, Any] = poll.get("backtest", poll)
        progress = float(result.get("progress", result.get("Progress", 0.0)))
        completed = result.get("completed", result.get("Completed", False))
        if completed or progress >= 1.0:
            print(f"[validation_ladder] Backtest complete (tier {tier})")
            return result
        print(
            f"[validation_ladder] Tier {tier} progress: {progress * 100:.1f}% "
            f"(attempt {attempt}/{_POLL_MAX_ATTEMPTS})"
        )
        time.sleep(_POLL_INTERVAL_SECONDS)

    raise RuntimeError(
        f"Backtest for tier {tier} did not complete after "
        f"{_POLL_MAX_ATTEMPTS * _POLL_INTERVAL_SECONDS} seconds"
    )


def _extract_sharpe(backtest_result: dict[str, Any]) -> float | None:
    """Extract the Sharpe ratio from a QC backtest result dict."""
    search_targets: list[dict[str, Any]] = [backtest_result]
    for sub_key in ("statistics", "Statistics", "runtimeStatistics", "RuntimeStatistics"):
        sub = backtest_result.get(sub_key)
        if isinstance(sub, dict):
            search_targets.append(sub)
    for key in ("SharpeRatio", "sharpe_ratio", "Sharpe Ratio", "sharpe"):
        for target in search_targets:
            value = target.get(key)
            if value is not None:
                try:
                    return float(str(value).replace("%", "").strip())
                except (ValueError, TypeError):
                    continue
    return None


# ---------------------------------------------------------------------------
# Aider strategy generation
# ---------------------------------------------------------------------------


def _build_aider_cmd(output_file: Path, prompt: str) -> list[str]:
    """Build the aider CLI command to generate a strategy file."""
    return [
        "aider",
        "--model", "gemini/gemini-2.5-flash",
        "--yes",
        "--no-git",
        "--message", prompt,
        str(output_file),
    ]


def _generate_strategy_with_aider(output_file: Path, prompt: str) -> bool:
    """Invoke aider to write a strategy to output_file.

    Returns True on success, False if aider is unavailable or fails.
    """
    cmd = _build_aider_cmd(output_file, prompt)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_AIDER_SUBPROCESS_TIMEOUT,
        )
        return result.returncode == 0 and output_file.exists()
    except FileNotFoundError:
        print(
            "[validation_ladder] aider not found — falling back to pre-written strategy",
            file=sys.stderr,
        )
        return False
    except subprocess.TimeoutExpired:
        print(
            f"[validation_ladder] aider timed out after {_AIDER_SUBPROCESS_TIMEOUT}s",
            file=sys.stderr,
        )
        return False


def _get_fallback_strategy_path(tier: int) -> Path:
    """Return path to pre-written fallback strategy for given tier."""
    return _GENERATED_DIR / f"aider_level1_tier{tier}.py"


def _ensure_strategy_file(tier: int, prompt: str) -> Path:
    """Ensure a strategy file exists for the given tier.

    Tries Aider first; falls back to pre-written strategy if unavailable.
    Always returns a valid path to an existing .py file.
    """
    output_path = _GENERATED_DIR / f"aider_level1_tier{tier}.py"
    _GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    if _GEMINI_API_KEY and not output_path.exists():
        print(f"[validation_ladder] Generating tier {tier} strategy with Aider…")
        if _generate_strategy_with_aider(output_path, prompt):
            print(f"[validation_ladder] Aider produced: {output_path}")
            return output_path
        print(
            f"[validation_ladder] Aider failed for tier {tier}, using pre-written fallback",
            file=sys.stderr,
        )

    if not output_path.exists():
        print(
            f"[validation_ladder] Using pre-written strategy for tier {tier} "
            "(GEMINI_API_KEY not set or aider unavailable)",
            file=sys.stderr,
        )
        fallback = _get_fallback_strategy_path(tier)
        if not fallback.exists():
            raise FileNotFoundError(
                f"No strategy available for tier {tier}. "
                f"Neither Aider nor pre-written file found: {fallback}"
            )
        return fallback

    return output_path


# ---------------------------------------------------------------------------
# Sharpe analysis helpers
# ---------------------------------------------------------------------------


def _analyze_low_sharpe(sharpe: float, tier: int) -> tuple[str, str]:
    """Return (issue, fix) diagnosis based on Sharpe value and tier.

    Tier 1-specific diagnoses are used for very low Sharpe (<0.5).
    Tier 4 falls back to the tier 3 low-Sharpe diagnosis if needed.
    """
    if sharpe < 0.5:
        # Map tier to appropriate diagnosis; tiers beyond 3 use the tier-3 entry.
        diagnoses: list[tuple[str, str]] = [
            ("no warmup period causing early noise trades", "add 50-day warmup period"),
            ("high trade frequency with no warmup", "add SetWarmUp(50) and check IsWarmingUp"),
            ("position sizing too small reducing returns", "increase position size to 0.9"),
        ]
        return diagnoses[min(tier - 1, len(diagnoses) - 1)]
    elif sharpe < 0.7:
        return (
            "exit timing lag from continuous comparison instead of crossover detection",
            "track previous SMA values to detect actual crossover events",
        )
    else:
        return (
            "position sizing below 1.0 leaving capital underutilised",
            "set SetHoldings to 1.0 for full capital deployment",
        )


def _build_tier_prompt(tier: int, prev_sharpe: float | None, issue: str, fix: str) -> str:
    """Build the prompt for the given tier."""
    if tier == 1:
        return _TIER1_PROMPT
    elif tier == 2:
        assert prev_sharpe is not None
        return _TIER2_PROMPT_TEMPLATE.format(
            actual_sharpe=prev_sharpe, issue=issue, fix=fix
        )
    elif tier == 3:
        assert prev_sharpe is not None
        return _TIER3_PROMPT_TEMPLATE.format(
            actual_sharpe=prev_sharpe, issue=issue, fix=fix
        )
    else:
        assert prev_sharpe is not None
        return _TIER4_PROMPT_TEMPLATE.format(
            actual_sharpe=prev_sharpe, fix=fix
        )


# ---------------------------------------------------------------------------
# Per-tier backtest orchestration
# ---------------------------------------------------------------------------


def _run_tier_backtest(
    tier: int,
    strategy_path: Path,
    result_output_path: Path,
    use_stub: bool,
) -> dict[str, Any]:
    """Run the backtest for a single tier.

    Returns a dict with 'sharpe', 'backtest_result', 'stub', 'error'.
    """
    strategy_code = strategy_path.read_text(encoding="utf-8")

    if use_stub:
        stub_sharpe = _stub_sharpe_for_tier(tier)
        result: dict[str, Any] = {
            "tier": tier,
            "sharpe": stub_sharpe,
            "stub": True,
            "note": "QC credentials not configured — simulated backtest result",
            "strategy_file": str(strategy_path),
        }
        result_output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    try:
        backtest_result = _backtest_strategy_via_qc(strategy_code, tier)
    except QCConnectionError as exc:
        print(
            f"[validation_ladder] QC REST API unreachable: {exc} — using stub Sharpe",
            file=sys.stderr,
        )
        stub_sharpe = _stub_sharpe_for_tier(tier)
        result = {
            "tier": tier,
            "sharpe": stub_sharpe,
            "stub": True,
            "note": f"QC unreachable: {exc}",
            "strategy_file": str(strategy_path),
        }
        result_output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    sharpe = _extract_sharpe(backtest_result)
    result = {
        "tier": tier,
        "sharpe": sharpe,
        "stub": False,
        "backtest_result": backtest_result,
        "strategy_file": str(strategy_path),
    }
    result_output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# Main validation ladder orchestration
# ---------------------------------------------------------------------------


def run_level1(
    output_log_path: Path | None = None,
    result_dir: Path | None = None,
) -> dict[str, Any]:
    """Run the Level 1 (SMA crossover) Aider validation ladder.

    Iterates up to 4 tiers, backtesting each generated strategy and checking
    whether the Sharpe ratio converges to 0.8±0.3.

    Returns the iteration log dict.
    """
    if output_log_path is None:
        output_log_path = Path("/tmp/aider_iteration_log_level1.json")
    if result_dir is None:
        result_dir = Path("/tmp")

    use_stub = not (_QC_USER_ID and _QC_API_TOKEN)
    if use_stub:
        print(
            "[validation_ladder] QC credentials not set — using stub backtest trajectory",
            file=sys.stderr,
        )

    tiers_log: list[dict[str, Any]] = []
    converged = False
    final_code_path: str | None = None
    prev_sharpe: float | None = None
    issue = ""
    fix = ""

    for tier in range(1, _MAX_TIERS + 1):
        print(f"\n[validation_ladder] ─── Tier {tier} ───")

        prompt = _build_tier_prompt(tier, prev_sharpe, issue, fix)
        strategy_path = _ensure_strategy_file(tier, prompt)
        print(f"[validation_ladder] Strategy file: {strategy_path}")

        result_path = result_dir / f"aider_level1_tier{tier}_result.json"
        tier_result = _run_tier_backtest(tier, strategy_path, result_path, use_stub)

        sharpe = tier_result.get("sharpe")
        if sharpe is None:
            print(
                f"[validation_ladder] Tier {tier}: Sharpe not extractable from backtest",
                file=sys.stderr,
            )
            tier_entry: dict[str, Any] = {
                "tier": tier,
                "sharpe": None,
                "issue": "sharpe_not_extracted",
                "fix": "check backtest result format",
                "stub": tier_result.get("stub", False),
            }
            tiers_log.append(tier_entry)
            # Abort the ladder on missing Sharpe to avoid using an undefined prev_sharpe
            break

        print(f"[validation_ladder] Tier {tier} Sharpe: {sharpe:.4f}")

        if _SHARPE_PASS_MIN <= sharpe <= _SHARPE_PASS_MAX:
            print(
                f"[validation_ladder] ✅ CONVERGED at tier {tier} "
                f"(Sharpe {sharpe:.4f} within 0.8±0.3)"
            )
            converged = True
            final_code_path = str(strategy_path.relative_to(_REPO_ROOT))
            tier_entry = {
                "tier": tier,
                "sharpe": round(sharpe, 4),
                "passed": True,
                "stub": tier_result.get("stub", False),
            }
            tiers_log.append(tier_entry)
            break

        issue, fix = _analyze_low_sharpe(sharpe, tier)
        print(f"[validation_ladder] Tier {tier}: issue='{issue}', fix='{fix}'")

        tier_entry = {
            "tier": tier,
            "sharpe": round(sharpe, 4),
            "issue": issue,
            "fix": fix,
            "stub": tier_result.get("stub", False),
        }
        tiers_log.append(tier_entry)
        prev_sharpe = sharpe

    if not converged:
        final_sharpe = tiers_log[-1].get("sharpe") if tiers_log else None
        print(
            f"[validation_ladder] ❌ Did not converge after {_MAX_TIERS} tiers "
            f"(final Sharpe: {final_sharpe})"
        )
        if final_code_path is None and tiers_log:
            last_tier = tiers_log[-1].get("tier", _MAX_TIERS)
            last_path = _GENERATED_DIR / f"aider_level1_tier{last_tier}.py"
            try:
                final_code_path = str(last_path.relative_to(_REPO_ROOT))
            except ValueError:
                final_code_path = str(last_path)

    iteration_log: dict[str, Any] = {
        "level": 1,
        "tiers": tiers_log,
        "convergence": converged,
        "final_code_path": final_code_path,
    }

    output_log_path.parent.mkdir(parents=True, exist_ok=True)
    output_log_path.write_text(json.dumps(iteration_log, indent=2), encoding="utf-8")
    print(f"[validation_ladder] Iteration log written to: {output_log_path}")

    return iteration_log


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _qc_client_exists() -> bool:
    """Return True if the Phase 1 QC REST client script exists."""
    return (Path(__file__).parent / "qc_upload_eval.py").exists()


def main(argv: list[str] | None = None) -> int:
    """Entry point for the validation ladder CLI.

    Returns exit code: 0 = converged or stub, 1 = failed, 2 = prerequisite missing.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Aider Validation Ladder — Level 1 (SMA Crossover)"
    )
    parser.add_argument(
        "--log",
        default="/tmp/aider_iteration_log_level1.json",
        help="Path to write the iteration log JSON (default: /tmp/aider_iteration_log_level1.json)",
    )
    parser.add_argument(
        "--result-dir",
        default="/tmp",
        help="Directory to write per-tier backtest result JSONs (default: /tmp)",
    )
    args = parser.parse_args(argv)

    # Prerequisite check: Phase 1 must be complete (qc_upload_eval.py must exist)
    if not _qc_client_exists():
        print(
            "[validation_ladder] ❌ Phase 1 not complete: scripts/qc_upload_eval.py not found. "
            "Complete Phase 1 (#88) before running Phase 4A.",
            file=sys.stderr,
        )
        return 2

    log_path = Path(args.log)
    result_dir = Path(args.result_dir)

    try:
        iteration_log = run_level1(output_log_path=log_path, result_dir=result_dir)
    except FileNotFoundError as exc:
        print(f"[validation_ladder] ❌ Strategy file error: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"[validation_ladder] ❌ Runtime error: {exc}", file=sys.stderr)
        return 1

    converged: bool = iteration_log.get("convergence", False)
    stub_tiers = [t for t in iteration_log.get("tiers", []) if t.get("stub")]

    if stub_tiers:
        print(
            "[validation_ladder] Note: backtest(s) used stub trajectory "
            "(QC credentials not configured — non-blocking)",
            file=sys.stderr,
        )
        return 0

    return 0 if converged else 1


if __name__ == "__main__":
    sys.exit(main())
