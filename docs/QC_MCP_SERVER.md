# QC MCP Server — Verified Reference

**Source-verified:** 2026-03-17 from `taylorwilsdon/quantconnect-mcp` live source code.
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
| `QUANTCONNECT_ORGANIZATION_ID` | Optional | — |

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

---

## Backtest Tool Names (exact — from `backtest_tools.py`)

| Tool | Exists | Use for |
|---|---|---|
| `read_backtest` | ✅ YES | Statistics, Sharpe ratio, full backtest object |
| `read_backtest_orders` | ✅ YES | Order array — returns `{status, orders, length}` |
| `read_backtest_chart` | ✅ YES | Chart data by name |
| `read_backtest_insights` | ✅ YES | Insights array |
| `read_backtest_statistics` | ❌ DOES NOT EXIST | Never use — was incorrectly referenced in PR #140 |
| `read_backtest_logs` | ❌ DOES NOT EXIST | Never use — was incorrectly referenced in PR #140 |

---

## Response Shapes (exact — from `backtest_tools.py`)

### `read_backtest`

```json
{
  "status": "success",
  "project_id": 28779543,
  "backtest_id": "...",
  "backtest": { ... },
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

> ⚠️ **Pagination constraint:** `end - start` must be ≤ 100 per call (enforced server-side — will return error if violated).

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

## GH Actions Pattern (Gate 0 / DAC v2 CI)

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

## Known-Good Test Backtest

| Field | Value |
|---|---|
| `project_id` | `28779543` |
| `backtest_id` | `29a044c64e018d411830f0580ae25dee` |
| Expected Sharpe | ~0.436 |
| Verified | 2026-03-16 (UNI-100 Phase 0) |

---

## What Does NOT Exist (confirmed from source)

- `read_backtest_statistics` — not in `backtest_tools.py`, not in any tool registration
- `read_backtest_logs` — not in `backtest_tools.py`, not in any tool registration
- Any tool named `list_backtests` that returns a statistics envelope

---

## Change Log

| Date | Change | Source |
|---|---|---|
| 2026-03-17 | File created — all facts verified from live `taylorwilsdon/quantconnect-mcp` source | `main.py`, `backtest_tools.py` |
| 2026-03-17 | PR #140 identified as incorrect — used non-existent tool names | PR #140 review |
