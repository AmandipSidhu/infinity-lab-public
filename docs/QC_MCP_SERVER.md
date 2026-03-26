# QC MCP Server — Verified Reference

**Source-verified:** 2026-03-26 from `taylorwilsdon/quantconnect-mcp` live source code.
Do NOT update this file from memory or README summaries — re-fetch source before editing.

---

## Server Identity

- **Package:** `taylorwilsdon/quantconnect-mcp` (PyPI: `quantconnect-mcp`)
- **Transport used in CI:** `streamable-http`
- **Default port:** `8000`
- **Default path:** `/mcp`
- **Install tool:** `uvx` (no clone required)

---

## Startup Command (exact — from `main.py`)

```bash
MCP_TRANSPORT=streamable-http MCP_HOST=0.0.0.0 MCP_PORT=8000 uvx quantconnect-mcp
```

---

## Required Environment Variables (exact — from `main.py` lines 24–26)

| Variable | Required | Notes |
|---|---|---|
| `QUANTCONNECT_USER_ID` | ✅ Yes | Server reads this exact name via `os.getenv("QUANTCONNECT_USER_ID")` |
| `QUANTCONNECT_API_TOKEN` | ✅ Yes | Server reads this exact name via `os.getenv("QUANTCONNECT_API_TOKEN")` |
| `QUANTCONNECT_ORGANIZATION_ID` | Optional | If not set, `auth.organization_id` is None — affects `create_project` fallback |

> ⚠️ **CRITICAL:** GitHub secrets in this repo are named `QC_USER_ID` and `QC_API_TOKEN`.
> These must be **mapped** in any workflow step that starts the server:
>
> ```yaml
> env:
>   QUANTCONNECT_USER_ID: ${{ secrets.QC_USER_ID }}
>   QUANTCONNECT_API_TOKEN: ${{ secrets.QC_API_TOKEN }}
> ```
>
> If you pass `QC_USER_ID` directly, the server starts unauthenticated and every tool call fails silently.

> ℹ️ **No org ID:** This account has no `QUANTCONNECT_ORGANIZATION_ID`. QC defaults to user-level scope.
> `organizationId` will be `None` in all project creation responses. This is expected and not an error.

---

## JSON-RPC Envelope

```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "TOOL_NAME", "arguments": {}}}
```

Required headers:
```
Content-Type: application/json
Accept: application/json, text/event-stream
Mcp-Session-Id: <session_id>
```

Session ID is obtained from the `initialize` handshake response headers before any tool calls.

---

## GH Actions Pattern

```yaml
- name: Start QC MCP Server
  env:
    QUANTCONNECT_USER_ID: ${{ secrets.QC_USER_ID }}
    QUANTCONNECT_API_TOKEN: ${{ secrets.QC_API_TOKEN }}
    MCP_TRANSPORT: streamable-http
    MCP_HOST: 0.0.0.0
    MCP_PORT: "8000"
  run: |
    pip install uv
    uvx quantconnect-mcp &
    sleep 10
```

---

## AUTH TOOLS (`auth_tools.py`)

### `configure_quantconnect_auth` — success
```json
{
  "status": "success",
  "message": "QuantConnect authentication configured and validated successfully",
  "user_id": "<uid>",
  "organization_id": null,
  "has_organization": false,
  "authenticated": true
}
```

### `validate_quantconnect_auth` — success
```json
{
  "status": "success",
  "authenticated": true,
  "message": "<validation message>",
  "user_id": "<uid>",
  "organization_id": null,
  "has_organization": false
}
```

### `get_auth_status` — configured
```json
{
  "status": "configured",
  "user_id": "<uid>",
  "organization_id": null,
  "has_organization": false,
  "api_base_url": "<url>",
  "message": "Authentication configured - use validate_quantconnect_auth to test"
}
```

### `read_account` — success
```json
{
  "status": "success",
  "account": { ...account object... },
  "message": "Successfully retrieved account information"
}
```

---

## PROJECT TOOLS (`project_tools.py`)

### `create_project` — success (full response, project found in list)
```json
{
  "status": "success",
  "project": {
    "projectId": 12345678,
    "name": "orb_15min_base",
    "language": "Py",
    "organizationId": null
  },
  "message": "Successfully created project 'orb_15min_base' with Py language"
}
```

### `create_project` — FALLBACK (project created but NOT found in returned list)
> ⚠️ This is the live failure mode on this account. `projectId` is ABSENT from the response.
> The project WAS created successfully on QC. You must call `read_project()` (no args) to recover the ID.
```json
{
  "status": "success",
  "project": {
    "name": "orb_15min_base",
    "language": "Py",
    "organizationId": null
  },
  "message": "Successfully created project 'orb_15min_base' with Py language",
  "note": "Full project details not available in response"
}
```

> **MANDATORY RECOVERY PATTERN:** After `create_project`, always check for `project_id` key.
> If absent (fallback hit), call `read_project()` with no args, then find project by `name` in returned list.
> Never hard-fail on missing `project_id` — always recover via lookup.

```python
# Required pattern in qc_upload_eval.py
result = call_tool("create_project", {"name": name, "language": "Py"})
project_id = result.get("project", {}).get("projectId")
if not project_id:
    # fallback: recover via read_project
    all_projects = call_tool("read_project", {})
    for p in all_projects.get("projects", []):
        if p.get("name") == name:
            project_id = p.get("projectId")
            break
if not project_id:
    raise RuntimeError(f"Could not obtain project_id for '{name}'")
```

### `read_project` — all projects (no project_id arg)
```json
{
  "status": "success",
  "projects": [
    {"projectId": 12345678, "name": "orb_15min_base", "language": "Py", ...}
  ],
  "total_projects": 1,
  "versions": [],
  "message": "Successfully retrieved 1 projects"
}
```

### `read_project` — single project (project_id provided)
```json
{
  "status": "success",
  "project": {"projectId": 12345678, "name": "orb_15min_base", ...},
  "versions": [],
  "message": "Successfully retrieved project 12345678"
}
```

### `compile_project`
```json
{
  "status": "success",
  "compile_id": "<compile_id_string>",
  "state": "BuildSuccess",
  "project_id": 12345678,
  "signature": "<sig>",
  "signature_order": [],
  "logs": [],
  "message": "Project compilation started successfully."
}
```

### `read_compilation_result` — success (no warnings/errors)
```json
{
  "status": "success",
  "compile_id": "<compile_id_string>",
  "state": "BuildSuccess",
  "project_id": 12345678,
  "signature": "<sig>",
  "signature_order": [],
  "logs": [],
  "errors": [],
  "message": "Compilation result retrieved successfully. State: BuildSuccess"
}
```

### `read_compilation_result` — failure (warnings or errors present)
> ⚠️ Returns `status: error` even if QC API returned `success: true` — the tool treats any warning as a failure.
```json
{
  "status": "error",
  "compile_id": "<compile_id_string>",
  "state": "BuildError",
  "project_id": 12345678,
  "logs": ["Warning: ..."],
  "errors": ["CS0246: ..."],
  "warnings": ["Warning: ..."],
  "message": "Compilation completed with 1 warnings and 1 errors.",
  "error": "Compilation failed: 1 warnings, 1 errors found"
}
```

### `delete_project`
```json
{
  "status": "success",
  "project_id": 12345678,
  "message": "Successfully deleted project 12345678."
}
```

### `read_project_nodes`
```json
{
  "status": "success",
  "project_id": 12345678,
  "nodes": { ...node objects... },
  "message": "Successfully retrieved node information for project 12345678"
}
```

### `update_project_nodes`
```json
{
  "status": "success",
  "project_id": 12345678,
  "updated_nodes": {"B2-8-node-id": true},
  "active_nodes": ["B2-8-node-id"],
  "message": "Successfully updated 1 node(s) for project 12345678, 1 now active"
}
```

---

## FILE TOOLS (`file_tools.py`)

### `create_file`
```json
{
  "status": "success",
  "project_id": 12345678,
  "file_name": "main.py",
  "content_length": 1234,
  "message": "Successfully created file 'main.py' in project 12345678"
}
```

### `read_file` — single file
```json
{
  "status": "success",
  "project_id": 12345678,
  "file": {"name": "main.py", "content": "..."},
  "message": "Successfully read file 'main.py' from project 12345678"
}
```

### `read_file` — all files
```json
{
  "status": "success",
  "project_id": 12345678,
  "files": [{"name": "main.py", "content": "..."}],
  "total_files": 1,
  "message": "Successfully read 1 files from project 12345678"
}
```

### `update_file_content`
```json
{
  "status": "success",
  "project_id": 12345678,
  "file_name": "main.py",
  "content_length": 1234,
  "message": "Successfully updated content of file 'main.py' in project 12345678"
}
```

### `update_file_name`
```json
{
  "status": "success",
  "project_id": 12345678,
  "old_name": "old.py",
  "new_name": "new.py",
  "message": "Successfully renamed file from 'old.py' to 'new.py' in project 12345678"
}
```

### `delete_file`
```json
{
  "status": "success",
  "project_id": 12345678,
  "file_name": "main.py",
  "message": "Successfully deleted file 'main.py' from project 12345678"
}
```

---

## BACKTEST TOOLS (`backtest_tools.py`)

### `read_backtest`
```json
{
  "status": "success",
  "project_id": 28779543,
  "backtest_id": "...",
  "backtest": { ...full backtest object... },
  "debugging": false,
  "chart_included": false
}
```

Sharpe ratio is nested inside `backtest`. Hard fail condition: `status != "success"` OR `backtest` is null.

### `read_backtest_orders`
```json
{
  "status": "success",
  "project_id": 28779543,
  "backtest_id": "...",
  "start": 0,
  "end": 100,
  "orders": {},
  "length": 0
}
```

Hard fail condition: `length == 0` OR `status != "success"`.

> ⚠️ **Pagination constraint:** `end - start` must be ≤ 100 per call.

### `read_backtest_chart`
```json
{
  "status": "success",
  "project_id": 28779543,
  "backtest_id": "...",
  "chart": { ...chart data... }
}
```

### `read_backtest_insights`
```json
{
  "status": "success",
  "project_id": 28779543,
  "backtest_id": "...",
  "insights": []
}
```

---

## Tools That DO NOT EXIST (confirmed from source)

| Tool Name | Status | Notes |
|---|---|---|
| `read_backtest_statistics` | ❌ DOES NOT EXIST | Incorrectly referenced in PR #140 — never use |
| `read_backtest_logs` | ❌ DOES NOT EXIST | Incorrectly referenced in PR #140 — never use |
| `list_backtests` | ❌ DOES NOT EXIST | Not registered anywhere |

---

## Known-Good Test Backtest

| Field | Value |
|---|---|
| `project_id` | `28779543` |
| `backtest_id` | `29a044c64e018d411830f0580ae25dee` |
| Expected Sharpe | ~0.436 |
| Verified | 2026-03-16 (UNI-100 Phase 0) |

---

## Change Log

| Date | Change | Source |
|---|---|
|---|
| 2026-03-17 | File created — backtest response shapes verified from live source | `backtest_tools.py` |
| 2026-03-17 | PR #140 identified as incorrect — used non-existent tool names | PR #140 review |
| 2026-03-26 | Added complete response shapes: all project, file, auth tools. Added `create_project` fallback pattern + recovery code. Added no-org-id note. | `project_tools.py`, `file_tools.py`, `auth_tools.py` |
