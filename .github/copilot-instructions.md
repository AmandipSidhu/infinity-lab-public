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
- **Project**: You are building the ACB (Autonomous Coding Bot) infrastructure within GitHub Actions.
- **File System**: Assume execution from the repository root. Use standard Python tools (`os`, `sys`, `pathlib`) for relative path resolution.

## 5. QC MCP Server — MANDATORY REFERENCE (read this before touching any QC code)

> **Source-verified 2026-03-26 from taylorwilsdon/quantconnect-mcp@db736df backtest_tools.py.**
> Do NOT infer tool names from docs, README, or memory. The list below is ground truth.

### 5.1 Exact Registered Tool Names

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

**❌ `read_backtests` — DOES NOT EXIST. Never call this. Will fail every time.**

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

### 5.2 Node-Busy Pattern — MANDATORY

`create_backtest` returns `success=False` with `errors=["...no spare nodes..."]` when compute nodes are busy.
**There is NO separate node-check endpoint.**
The retry loop MUST wrap `create_backtest` itself and inspect `data.get("errors", [])`.

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

### 5.3 create_project Fallback — MANDATORY

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

### 5.4 MCP JSON-RPC Transport

- **URL**: `http://localhost:8000/mcp/` (env: `QC_MCP_URL`)
- **Session**: Must call `initialize` first to get `Mcp-Session-Id` header
- **Envelope**: `{"jsonrpc": "2.0", "id": N, "method": "tools/call", "params": {"name": TOOL, "arguments": {...}}}`
- **Response**: May be SSE-wrapped (`data: {...}`) — strip prefix before JSON parse
- **Env vars used by server**: `QUANTCONNECT_USER_ID`, `QUANTCONNECT_API_TOKEN` (NOT `QC_USER_ID`/`QC_API_TOKEN` — must be mapped in workflow)

### 5.5 Pipeline Step Order

1. `create_project` → recover `project_id` if missing
2. `create_file` → upload strategy code
3. `compile_project` → get `compile_id`
4. `read_compilation_result` → poll until `BuildSuccess`
5. `create_backtest` (with node-busy retry) → get `backtest_id`
6. `read_backtest` → poll until `completed=True` or `progress>=1.0`
7. `read_backtest_orders` → get order count (pagination max 100)
8. Evaluate FitnessTracker constraints (Sharpe, Drawdown, Trades)

### 5.6 Smoke Test Gap — Known Issue

The smoke tests and unit tests do NOT exercise the live MCP server pipeline end-to-end.
They mock `_mcp_tool_call` at the function level, which means:
- Wrong tool names (e.g., `read_backtests` instead of `list_backtests`) will pass smoke tests
- Node-busy error handling is not tested unless fixtures include `success=False + errors=["no spare nodes"]`
- Any future changes to `qc_upload_eval.py` that call MCP tools must be verified against
  the live tool inventory in Section 5.1 above before merging

**When writing or updating tests for this file:**
- Always include a fixture where `create_backtest` returns `{"status": "error", "details": ["no spare nodes"]}`
  on the first call and `{"status": "success", "backtest": {"backtestId": "bt_123"}}` on the second
- Always include a fixture where `create_project` returns without `projectId` and the
  fallback `read_project` list contains the project
