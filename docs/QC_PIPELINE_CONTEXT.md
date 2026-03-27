# QC MCP Pipeline — Copilot Reference

> Source-verified 2026-03-26 from `taylorwilsdon/quantconnect-mcp@db736df`.
> This file is the canonical reference for anyone (human or Copilot) modifying
> `scripts/qc_upload_eval.py` or any workflow that calls the QC MCP server.

## Why This File Exists

The smoke tests and unit tests mock `_mcp_tool_call` at the function level.
This means **incorrect tool names pass all local tests** but fail in live CI.
The root cause that burned multiple sessions: `read_backtests` was called but
does not exist in `backtest_tools.py`. The fix is documented here permanently.

---

## Verified Tool Inventory

### Backtest Tools

| Tool name | Args | Notes |
|---|---|---|
| `create_backtest` | `project_id, compile_id, backtest_name, parameters=None` | Returns `backtest.backtestId`. Node-busy = retry (see below) |
| `read_backtest` | `project_id, backtest_id, chart=None` | Poll until `completed=True` or `progress>=1.0` |
| `read_backtest_chart` | `project_id, backtest_id, name, count=100` | Optional start/end timestamps |
| `read_backtest_orders` | `project_id, backtest_id, start=0, end=100` | Max range 100. `length` = total across all pages |
| `read_backtest_insights` | `project_id, backtest_id, start=0, end=100` | Max range 100 |
| `list_backtests` | `project_id` | Use this to list/check backtest status |
| `update_backtest` | `project_id, backtest_id, name=None, note=None` | At least one of name/note required |
| `delete_backtest` | `project_id, backtest_id` | — |

### ❌ Non-Existent Tools — Never Call These

```
read_backtests          ← does not exist (confirmed 2026-03-26)
```

### Project Tools

| Tool | Notes |
|---|---|
| `create_project(name, language)` | Known fallback: may not return `projectId` on this account |
| `read_project(project_id=None)` | No args = all projects list |
| `compile_project(project_id)` | Returns `compile_id` |
| `read_compilation_result(project_id, compile_id)` | Poll until `state == "BuildSuccess"` |
| `delete_project(project_id)` | — |
| `read_project_nodes(project_id)` | — |
| `update_project_nodes(project_id, nodes)` | — |

### File Tools

`create_file(project_id, name, content)`, `read_file(project_id, name=None)`,
`update_file_content(project_id, name, content)`, `delete_file(project_id, name)`

### Auth Tools

`configure_quantconnect_auth(user_id, api_token)`, `validate_quantconnect_auth()`, `get_auth_status()`

---

## Node-Busy Error Pattern

`create_backtest` returns this when all nodes are occupied:

```json
{"status": "error", "error": "Backtest creation failed", "details": ["No spare nodes available"]}
```

**No separate node-check endpoint exists.** The retry loop wraps `create_backtest` itself.
See `_create_backtest()` in `scripts/qc_upload_eval.py` for the production implementation.
Constants: `_BACKTEST_CREATE_RETRY_MAX = 6`, `_BACKTEST_CREATE_RETRY_WAIT = 30`

---

## create_project Fallback

On this account, `create_project` may succeed on QC but not return `projectId` in the
JSON-RPC response. Always recover via `read_project()` and match by name.
See `_create_project()` in `scripts/qc_upload_eval.py`.

---

## MCP Transport

- Server: `http://localhost:8000/mcp/` started by CI before `qc_upload_eval.py` runs
- Session: `initialize` handshake → extract `Mcp-Session-Id` from response headers
- All tool calls: `POST` with `{"method": "tools/call", "params": {"name": ..., "arguments": {...}}}`
- Response may be SSE (`data: {...}`) — strip prefix before JSON parse
- Server env vars: `QUANTCONNECT_USER_ID`, `QUANTCONNECT_API_TOKEN`
  (GitHub secrets `QC_USER_ID`/`QC_API_TOKEN` must be **mapped** in workflow `env:` block)

---

## Known Test Gap

Unit/smoke tests mock `_mcp_tool_call` and therefore **cannot catch wrong tool names**.
Before any PR that modifies MCP tool calls in `qc_upload_eval.py`:
1. Cross-check tool names against Section "Verified Tool Inventory" above
2. Ensure test fixtures cover `create_backtest` node-busy retry path
3. Ensure test fixtures cover `create_project` missing-`projectId` fallback path
