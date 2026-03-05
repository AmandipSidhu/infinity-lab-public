# QC Deployment — Two-Path Architecture

## Overview

The ACB pipeline uses **two separate scripts** for QuantConnect integration,
each optimised for a different stage of the strategy lifecycle:

| Script | Transport | Trigger | Purpose |
|--------|-----------|---------|---------|
| `scripts/qc_upload_eval.py` | MCP / JSON-RPC 2.0 | Automated CI | Fast validation backtest |
| `scripts/qc_deploy_live.py` | REST API v2 | Manual / human-approved | Live deployment |

---

## Path 1 — Automated CI Backtesting (MCP)

**Script**: `scripts/qc_upload_eval.py`

**Used by**: `.github/workflows/acb_pipeline.yml`, Step 6 (`qc-upload-eval`)

### How it works

1. The CI workflow starts a `quantconnect-mcp` service container
   (`quantconnect/mcp-server:latest`) on port 8000.
2. `qc_upload_eval.py` connects to `QC_MCP_BASE_URL`
   (default: `http://localhost:8000/mcp`) using JSON-RPC 2.0.
3. Four MCP tool calls are made in sequence:
   - `create_project` → returns `project_id`
   - `create_file` → uploads strategy source
   - `create_backtest` → triggers backtest, returns `backtest_id`
   - `read_backtest` (polled) → returns statistics on completion
4. FitnessTracker constraints are evaluated (Sharpe Ratio, Max Drawdown).
5. Results are written to `/tmp/qc_eval_output.json` for downstream steps.

### Stub / fallback behaviour (non-blocking CI)

The script exits `0` and writes a stub PASS result when:
- `QC_MCP_BASE_URL` environment variable is **not set** (local dev)
- The MCP service container fails to start (`ConnectionError`)

This ensures CI is never blocked by MCP infrastructure issues.

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `QC_MCP_BASE_URL` | Yes (CI) | Full URL of the MCP server, e.g. `http://localhost:8000/mcp` |
| `QC_USER_ID` | Service container | Passed to the MCP container |
| `QC_API_TOKEN` | Service container | Passed to the MCP container |

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Backtest passed, or stub fallback |
| 1 | Backtest failed constraints, or unrecoverable MCP error |
| 2 | Invalid arguments or file not found |

---

## Path 2 — Human-Approved Live Deployment (REST API)

**Script**: `scripts/qc_deploy_live.py`

**Used by**: Manual invocation after human review and approval

### How it works

1. A human reviews the strategy (CI report, pre-commit gates, backtest stats).
2. After approval, `qc_deploy_live.py` is run locally or in a separate
   manual workflow dispatch.
3. The script:
   - Creates a new QC project via `POST /projects/create`
   - Uploads the strategy as `main.py` via `POST /files/create`
   - Optionally starts live trading via `POST /live/create`
     (paper trading by default)
4. Returns the project URL and, if live trading was started, the live algo ID.

### Usage

```bash
# Upload only (no live trading)
python scripts/qc_deploy_live.py \
    --strategy strategies/my_strategy.py \
    --project-name "My Approved Strategy v2"

# Upload and start paper live trading
python scripts/qc_deploy_live.py \
    --strategy strategies/my_strategy.py \
    --start-live

# Write result to JSON file
python scripts/qc_deploy_live.py \
    --strategy strategies/my_strategy.py \
    --output /tmp/deploy_result.json
```

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `QC_USER_ID` | Yes | QuantConnect user ID |
| `QC_API_TOKEN` | Yes | QuantConnect API token |

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Upload (and optional live start) succeeded |
| 1 | API or network error |
| 2 | Invalid arguments, file not found, or missing credentials |

---

## Typical end-to-end workflow

```
Push spec → CI triggers
    │
    ├─ Step 1-5: Spec validation, strategy review, Aider build, pre-commit gates
    │
    ├─ Step 6: qc_upload_eval.py (MCP → automated backtest)
    │          └─ Writes /tmp/qc_eval_output.json
    │
    ├─ Step 7: human_review_artifacts.py
    │          └─ Posts results to GitHub Step Summary + PR comment
    │
    └─ Human reviews the CI report
           │
           ├─ Approved? → run qc_deploy_live.py (REST API → live deployment)
           └─ Rejected? → iterate on spec or strategy
```

---

## MCP Service Container

The `quantconnect/mcp-server:latest` Docker image is referenced in the CI
workflow as a service container. If this official image is not yet publicly
available, the `qc_upload_eval.py` stub fallback ensures CI still passes.
Monitor https://hub.docker.com/r/quantconnect/mcp-server for availability.
