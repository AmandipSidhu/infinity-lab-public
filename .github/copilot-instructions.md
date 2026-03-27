# GitHub Copilot Agent Instructions: Infinity Lab

## 1. Zero-Placeholder Policy (CRITICAL)
- **NO PLACEHOLDERS**: Never use `...`, `TODO`, `FIXME`, `pass`, or `// add implementation here` in your generated code.
- **COMPLETE LOGIC**: You must write 100% complete, production-ready code. Do not leave boilerplate or logic for the user to fill out.
- **PARSING**: If processing JSON or API responses, write out the full parsing logic.

## 2. Python Architecture & Code Style
- **Language**: Python 3.10+
- **Typing**: Use strict type hints for all function arguments, return types, and class variables.
- **Error Handling & Resilience**:
  - **Do not swallow exceptions silently.** Let the code fail loudly with full tracebacks.
  - **Implement retry logic** for external dependencies (APIs, network calls) to recover from transient failures automatically.
- **Secrets**: Load all secrets via `os.environ.get()`. Never hardcode secrets.

## 3. Testing Requirements
- **Framework**: Use `pytest`.
- **Completeness**: Always generate corresponding test files when creating or updating scripts.
- **Coverage**: Include tests for the "happy path" and explicit failure modes.
- **Corpus**: Do not mock complex data schemas blindly. Provide realistic, structured test fixtures/corpus data.

## 4. Context & Environment
- **Project**: You are building the QSC Grinder — an autonomous strategy generation pipeline for QuantConnect (LEAN engine).
- **File System**: Assume execution from the repository root. Use standard Python tools (`os`, `sys`, `pathlib`) for relative path resolution.
- **NOT the ACB pipeline** — ACB is deprecated. All work targets the QSC Grinder workflow.

---

## 5. QSC Grinder — Pipeline Overview

### 5.1 What the Grinder Does
The grinder reads strategy specs from `prompts/queue.md`, runs Aider to generate LEAN Python code,
compiles and backtests each strategy on QuantConnect via MCP, and logs results to `grinder_results.jsonl`.

### 5.2 Prompt File — Primary Input
**Strategy specs live at: `prompts/queue.md`**

To add a strategy to the grinder queue:
1. Edit `prompts/queue.md` with a well-formed strategy spec
2. Commit and push to `main` — the workflow triggers automatically on `paths: ['prompts/**']`

Spec format (one strategy block):
```markdown
## STRATEGY: <name>
type: <orb|momentum|mean_reversion|pairs|etc>
description: <natural language description of the strategy logic>
universe: <asset class and selection criteria>
entry: <entry signal description>
exit: <exit signal and stop loss>
performance_targets:
  sharpe_ratio_min: 0.8
  max_drawdown_threshold: 0.15
  min_trades: 100
```

### 5.3 How to Trigger a Full Pipeline Run
```bash
# Option A: Push a change to prompts/queue.md (auto-triggers via workflow)
git add prompts/queue.md && git commit -m "grinder: add <strategy_name> spec" && git push

# Option B: Manual dispatch via GitHub CLI
gh workflow run qsc_grinder.yml --repo AmandipSidhu/infinity-lab-public

# Option C: Manual dispatch via GitHub UI
# https://github.com/AmandipSidhu/infinity-lab-public/actions/workflows/qsc_grinder.yml
# Click "Run workflow" button
```

### 5.4 How to Watch a Run
```bash
# Watch live logs
gh run watch --repo AmandipSidhu/infinity-lab-public

# List recent runs
gh run list --repo AmandipSidhu/infinity-lab-public --workflow qsc_grinder.yml

# View logs of a specific run
gh run view <run_id> --log --repo AmandipSidhu/infinity-lab-public
```

### 5.5 Pipeline Step Order
1. Parse `prompts/queue.md` → extract strategy specs
2. Aider generates LEAN Python strategy code for each spec
3. `qc_quick_validate.py` → basic syntax/structure check
4. `qc_upload_eval.py` → upload to QC via MCP, compile, backtest, evaluate
5. `log_grinder_result.py` → append result to `grinder_results.jsonl`
6. `generate_grinder_summary.py` → produce summary report

### 5.6 Mandatory Reference Docs (READ BEFORE TOUCHING ANY QC OR PIPELINE CODE)
- **`docs/QC_MCP_SERVER.md`** — verified live tool inventory for `taylorwilsdon/quantconnect-mcp`.
  Contains exact tool names, signatures, response shapes, and known failure modes.
  **Fetch and read this file in full before writing or modifying any MCP tool call.**
- **`docs/QC_PIPELINE_CONTEXT.md`** — explains the full pipeline context, why each step exists,
  and how errors surface. Read before any PR touching `qsc_grinder.yml` or `qc_upload_eval.py`.
- **`scripts/qc_upload_eval.py`** — canonical implementation. The `TOOL INVENTORY` comment block
  at the top of this file is source-of-truth for all MCP tool names used in the pipeline.

---

## 6. QC MCP Server — MANDATORY RULES (never deviate)

> **Source-verified 2026-03-26 from taylorwilsdon/quantconnect-mcp@db736df backtest_tools.py.**
> Do NOT infer tool names from docs, README, or memory. The list below is ground truth.
> When in doubt: fetch `docs/QC_MCP_SERVER.md` from this repo and use it.

### 6.1 Exact Registered Tool Names

**Backtest tools** (`backtest_tools.py`):
| Tool | Signature |
|---|---|
| `create_backtest` | `(project_id, compile_id, backtest_name, parameters=None)` |
| `read_backtest` | `(project_id, backtest_id, chart=None)` |
| `read_backtest_chart` | `(project_id, backtest_id, name, count=100, start=None, end=None)` |
| `read_backtest_orders` | `(project_id, backtest_id, start=0, end=100)` |
| `read_backtest_insights` | `(project_id, backtest_id, start=0, end=100)` |
| `list_backtests` | `(project_id)` |
| `update_backtest` | `(project_id, backtest_id, name=None, note=None)` |
| `delete_backtest` | `(project_id, backtest_id)` |

**❌ `read_backtests` — DOES NOT EXIST. Never call this. It will fail every time. This exact bug has caused multiple lost sessions — do not reintroduce it.**

**Project tools** (`project_tools.py`):
| Tool | Notes |
|---|---|
| `create_project` | May omit `projectId` on this account — always run fallback via `read_project` |
| `read_project` | No args = list all; with `project_id` = single project |
| `compile_project` | Returns `compile_id` |
| `read_compilation_result` | Poll until `state == "BuildSuccess"` |
| `delete_project` | — |

**File tools** (`file_tools.py`):
`create_file`, `read_file`, `update_file_content`, `delete_file`

**Auth tools** (`auth_tools.py`):
`configure_quantconnect_auth`, `validate_quantconnect_auth`, `get_auth_status`

### 6.2 Node-Busy Pattern — MANDATORY

`create_backtest` returns `success=False` with `errors=["...no spare nodes..."]` when compute nodes are busy.
**There is NO separate node-check endpoint.**
The retry loop MUST wrap `create_backtest` itself and inspect `data.get("errors", [])`.
This error is in the **response body** — it is NOT an HTTP error and will NOT be caught by status code checks.

```python
# CORRECT pattern — always use this in qc_upload_eval.py
_BACKTEST_CREATE_RETRY_MAX = 6
_BACKTEST_CREATE_RETRY_WAIT = 30  # seconds

for attempt in range(1, _BACKTEST_CREATE_RETRY_MAX + 1):
    result = _parse_tool_json(_mcp_tool_call("create_backtest", {...}), "create_backtest")
    if result.get("status") == "success":
        backtest_id = result["backtest"]["backtestId"]
        break
    errors = result.get("details", result.get("errors", []))
    if any("no spare nodes" in str(e).lower() or "node" in str(e).lower() for e in errors):
        if attempt < _BACKTEST_CREATE_RETRY_MAX:
            time.sleep(_BACKTEST_CREATE_RETRY_WAIT)
            continue
        raise RuntimeError(f"No spare nodes after {_BACKTEST_CREATE_RETRY_MAX} attempts")
    raise RuntimeError(f"create_backtest non-transient error: {errors}")
```

### 6.3 create_project Fallback — MANDATORY

On this QC account, `create_project` may return without `projectId` in the response.
Always check and recover:

```python
project_id = result.get("project", {}).get("projectId")
if not project_id:
    all_projects = _parse_tool_json(_mcp_tool_call("read_project", {}), "read_project")
    for p in all_projects.get("projects", []):
        if p.get("name") == spec_name:
            project_id = p.get("projectId")
            break
if not project_id:
    raise RuntimeError(f"Could not obtain project_id for '{spec_name}'")
```

### 6.4 MCP JSON-RPC Transport

- **URL**: `http://localhost:8000/mcp/` (env: `QC_MCP_URL`)
- **Session**: Must call `initialize` first to get `Mcp-Session-Id` header
- **Envelope**: `{"jsonrpc": "2.0", "id": N, "method": "tools/call", "params": {"name": TOOL, "arguments": {...}}}`
- **Response**: May be SSE-wrapped (`data: {...}`) — strip prefix before JSON parse
- **Env vars used by server**: `QUANTCONNECT_USER_ID`, `QUANTCONNECT_API_TOKEN` (NOT `QC_USER_ID`/`QC_API_TOKEN` — must be mapped in workflow)

---

## 7. Mandatory Test Fixtures (copy-paste ready)

These fixtures MUST be present in `tests/test_qsc_smoke.py` or equivalent.
Any PR that modifies `qc_upload_eval.py` must verify both pass before merge.

### 7.1 Node-Busy Retry Fixture

```python
import pytest
from unittest.mock import patch, call

def make_mcp_response(data: dict) -> dict:
    """Wrap a dict in the MCP JSON-RPC response envelope."""
    import json
    text = json.dumps(data)
    return {"result": {"content": [{"type": "text", "text": text}]}}


def test_create_backtest_retries_on_node_busy(tmp_path, monkeypatch):
    """create_backtest must retry on 'no spare nodes' and succeed on attempt 2."""
    import scripts.qc_upload_eval as mod

    call_count = {"n": 0}

    def fake_mcp_tool_call(name: str, arguments: dict, req_id: int = 1) -> dict:
        if name == "create_backtest":
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First attempt: node busy
                return make_mcp_response({
                    "status": "error",
                    "details": ["no spare nodes available"],
                    "errors": ["no spare nodes available"],
                })
            # Second attempt: success
            return make_mcp_response({
                "status": "success",
                "backtest": {"backtestId": "bt_test_123"},
            })
        if name == "compile_project":
            return make_mcp_response({"compile_id": "cmp_001", "state": "BuildSuccess"})
        if name == "read_compilation_result":
            return make_mcp_response({"state": "BuildSuccess", "compile_id": "cmp_001"})
        raise ValueError(f"Unexpected tool call: {name}")

    monkeypatch.setattr(mod, "_mcp_tool_call", fake_mcp_tool_call)
    monkeypatch.setattr(mod, "_BACKTEST_CREATE_RETRY_WAIT", 0)  # no sleep in tests

    backtest_id = mod._create_backtest(project_id=999, spec_name="test_strategy")
    assert backtest_id == "bt_test_123"
    assert call_count["n"] == 2  # retried exactly once


def test_create_backtest_raises_after_max_retries(monkeypatch):
    """create_backtest must raise RuntimeError after exhausting all retries."""
    import scripts.qc_upload_eval as mod

    def fake_mcp_tool_call(name: str, arguments: dict, req_id: int = 1) -> dict:
        if name == "create_backtest":
            return make_mcp_response({
                "status": "error",
                "details": ["no spare nodes"],
            })
        if name == "compile_project":
            return make_mcp_response({"compile_id": "cmp_001", "state": "BuildSuccess"})
        if name == "read_compilation_result":
            return make_mcp_response({"state": "BuildSuccess"})
        raise ValueError(f"Unexpected tool call: {name}")

    monkeypatch.setattr(mod, "_mcp_tool_call", fake_mcp_tool_call)
    monkeypatch.setattr(mod, "_BACKTEST_CREATE_RETRY_WAIT", 0)

    with pytest.raises(RuntimeError, match="no spare nodes"):
        mod._create_backtest(project_id=999, spec_name="test_strategy")
```

### 7.2 create_project Missing projectId Fallback Fixture

```python
def test_create_project_fallback_when_project_id_missing(monkeypatch):
    """create_project must recover via read_project list when projectId absent."""
    import scripts.qc_upload_eval as mod

    def fake_mcp_tool_call(name: str, arguments: dict, req_id: int = 1) -> dict:
        if name == "create_project":
            # Returns success but NO projectId — known live failure mode
            return make_mcp_response({
                "project": {},  # empty — no projectId
                "status": "success",
            })
        if name == "read_project":
            # Fallback list contains the project
            return make_mcp_response({
                "projects": [
                    {"name": "my_strategy", "projectId": 42},
                    {"name": "other_strategy", "projectId": 99},
                ]
            })
        raise ValueError(f"Unexpected tool call: {name}")

    monkeypatch.setattr(mod, "_mcp_tool_call", fake_mcp_tool_call)

    project_id = mod._create_project(spec_name="my_strategy")
    assert project_id == 42


def test_create_project_raises_when_fallback_also_missing(monkeypatch):
    """create_project must raise RuntimeError if project not found in fallback list."""
    import scripts.qc_upload_eval as mod

    def fake_mcp_tool_call(name: str, arguments: dict, req_id: int = 1) -> dict:
        if name == "create_project":
            return make_mcp_response({"project": {}, "status": "success"})
        if name == "read_project":
            return make_mcp_response({"projects": []})  # nothing found
        raise ValueError(f"Unexpected tool call: {name}")

    monkeypatch.setattr(mod, "_mcp_tool_call", fake_mcp_tool_call)

    with pytest.raises(RuntimeError, match="Could not obtain project_id"):
        mod._create_project(spec_name="my_strategy")
```

---

## 8. BANNED — Never Use These

| What | Why |
|---|---|
| `read_backtests` | Does not exist in quantconnect-mcp. Has caused multiple lost sessions. Use `list_backtests` or `read_backtest` (singular). |
| `wait_for_free_node()` | Removed. Node-busy handled by retry loop inside `_create_backtest` only. |
| REST API calls to QC (`requests.post` to `quantconnect.com/api/v2`) | All QC interactions go through `quantconnect-mcp` MCP tools only. REST helpers in the file are legacy kept for backward compat with tests only. |
| Mocking `_mcp_tool_call` with only happy-path responses | Will silently pass tests while hiding node-busy and missing-projectId bugs. Always add failure fixtures (Sections 7.1, 7.2). |

---

## 9. Pre-PR Checklist (QC / Grinder Changes)

Before opening any PR that touches `qsc_grinder.yml`, `qc_upload_eval.py`, or any MCP call:

- [ ] Fetch and re-read `docs/QC_MCP_SERVER.md` — verify every tool name against live inventory
- [ ] Fetch and re-read `docs/QC_PIPELINE_CONTEXT.md` — understand the full context
- [ ] Read the `TOOL INVENTORY` comment block at the top of `scripts/qc_upload_eval.py`
- [ ] Tests include node-busy retry fixture (Section 7.1)
- [ ] Tests include create_project missing-projectId fixture (Section 7.2)
- [ ] No use of `read_backtests` (plural) anywhere in the diff
- [ ] No use of `wait_for_free_node()` anywhere in the diff
- [ ] Run `pytest tests/` locally and confirm all pass
