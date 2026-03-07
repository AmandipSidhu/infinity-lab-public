# QC Deployment — Two-Path Architecture

## Overview

The ACB pipeline uses **two separate paths** for QuantConnect integration,
each optimised for a different stage of the strategy lifecycle:

| Script | Transport | Trigger | Purpose |
|--------|-----------|---------|---------|
| `scripts/qc_upload_eval.py` | lean-cli (local Docker) | Automated CI | Fast validation backtest |
| `qc_deploy_live.py` (private repo) | REST API v2 | Manual / human-approved | Live deployment |

> **Note:** `qc_deploy_live.py` has been moved to `infinity-lab-private`.
> Live deployment is manual only and must never run from this public repository.

---

## Path 1 — Automated CI Backtesting (lean-cli)

**Tool**: `lean` CLI (`pip install lean`)

**Used by**: `.github/workflows/acb_pipeline.yml`, step `lean-backtest` (runs after Aider build)

### How it works

1. The CI workflow installs `lean` via `pip install lean`.
2. After Aider writes the strategy file, the `Lean local backtest` step runs:
   ```bash
   lean backtest "strategies/<spec_name>" --output /tmp/lean_backtest_output
   ```
3. On success the backtest result JSON is copied to `/tmp/backtest_result.json`.
4. On failure the error log is echoed to `GITHUB_STEP_SUMMARY`; the step exits 0
   so downstream CI steps are not blocked.
5. The result artifact is uploaded as `lean-backtest-result-<spec_name>`.

### Fallback behaviour (non-blocking CI)

If `lean backtest` exits non-zero, the step:
- Writes `{"status":"failed","exit_code":<n>}` to `/tmp/backtest_result.json`
- Appends the last 50 lines of the lean log to `GITHUB_STEP_SUMMARY`
- Continues without failing the workflow (`set +e`)

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Backtest succeeded, or lean failure captured non-fatally |

---

## Path 2 — Human-Approved Live Deployment (REST API)

> **Script location:** `qc_deploy_live.py` has been moved to `infinity-lab-private`.
> Live deployment is manual only — it must never run from this public repository.

### How it works

1. A human reviews the strategy (CI report, pre-commit gates, backtest stats).
2. After approval, `qc_deploy_live.py` is run manually from `infinity-lab-private`.
3. The script:
   - Creates a new QC project via `POST /projects/create`
   - Uploads the strategy as `main.py` via `POST /files/create`
   - Optionally starts live trading via `POST /live/create`
     (paper trading by default)
4. Returns the project URL and, if live trading was started, the live algo ID.

---

## Typical end-to-end workflow

```
Push spec → CI triggers
    │
    ├─ Step 1-5: Spec validation, strategy review, Aider build, pre-commit gates
    │
    ├─ Step 6: lean backtest (lean-cli → local Docker backtest)
    │          └─ Writes /tmp/backtest_result.json
    │
    ├─ Step 7: qc_upload_eval.py (uploads result to QC cloud for deep evaluation)
    │          └─ Writes /tmp/qc_eval_output.json
    │          └─ Falls back gracefully if QC_MCP_BASE_URL is not set
    │
    ├─ Step 8: human_review_artifacts.py
    │          └─ Posts results to GitHub Step Summary + PR comment
    │
    └─ Human reviews the CI report
           │
           ├─ Approved? → run qc_deploy_live.py from infinity-lab-private (manual only)
           └─ Rejected? → iterate on spec or strategy
```

---

## lean-cli Local Backtesting

The `lean` CLI (`pip install lean`) is used in CI to run local backtests using
the official `quantconnect/lean` Docker image. No external service container is
required. The lean backtest step is non-blocking: failures are captured and
surfaced in `GITHUB_STEP_SUMMARY` without failing the overall workflow.
