# GAPS.md — ARCHITECTURE v4.5 Audit

**Generated:** 2026-03-07T00:29:44Z
**Auditor:** GitHub Copilot
**Arch SHA:** 8643605d3a17d8dbf3c24da5d7cfa85b782db54c

---

## Section 4 — Pipeline Step Coverage

| Step | Description | File | Line | Verdict |
|------|-------------|------|------|---------|
| 1 | Spec YAML pushed to `specs/` — push trigger on `specs/**` and `workflow_dispatch` | .github/workflows/acb_pipeline.yml | 3–7 | OK |
| 2 | `spec_validator.py` runs | .github/workflows/acb_pipeline.yml | 173 | OK |
| 3a | If ERROR → fail build, notify Slack | .github/workflows/acb_pipeline.yml | 216–222 | OK |
| 3b | If WARN → log, continue | .github/workflows/acb_pipeline.yml | 177–215 | OK |
| 4 | `strategy_reviewer.py` runs (Gemini review, advisory, 0–10 score) | .github/workflows/acb_pipeline.yml | 296 | OK |
| 5 | `mcp_tool_discovery.py` regenerates `config/qc_tools_manifest.json` | .github/workflows/acb_pipeline.yml | 334 | OK |
| 6 | `aider_builder.py` runs (4-tier all-Gemini) | .github/workflows/acb_pipeline.yml | 358 | OK |
| 7 | `lean backtest` (local, best-effort) | .github/workflows/acb_pipeline.yml | 374 | OK |
| 8 | `pre_commit_gates.py` (CCN, Bandit, Semgrep, func length, stub detection) | .github/workflows/acb_pipeline.yml | 405 | OK |
| 9 | `qc_upload_eval.py` (QC REST API) | .github/workflows/acb_pipeline.yml | 425 | OK |
| 10 | If PASS → `qc_promote.py` runs | .github/workflows/acb_pipeline.yml | 437 | OK |
| 10b | If FAIL → pipeline fails, Slack notified (qc_outcome == "failure" branch) | .github/workflows/acb_pipeline.yml | 496 | OK |
| 11 | `human_review_artifacts.py` | .github/workflows/acb_pipeline.yml | 458 | OK |
| 12 | E2E report committed to `infinity-lab-private/reports/e2e/<spec_stem>/` | .github/workflows/acb_pipeline.yml | 657 | MISMATCH |

**Step 12 note:** `git add reports/e2e/` at line 657 and `git push` at line 662 commit the E2E report to `infinity-lab-public` (the current repo). The architecture mandates `infinity-lab-private`. No cross-repo push is implemented.

---

## Section 5 — Implementation Notes

| Claim | File | Line | Verdict | Notes |
|-------|------|------|---------|-------|
| `aider_builder.py` implements 4-tier all-Gemini ladder | scripts/aider_builder.py | 46–53 | OK | Tier 1: `gemini/gemini-2.5-flash`, Tier 2: `gemini/gemini-2.5-flash-lite`, Tier 3: `gemini/gemini-2.5-flash`, Tier 4: `gemini/gemini-2.5-pro` |
| `aider_builder.py` uses `--read config/aider_system_prompt_with_tools.txt` | scripts/aider_builder.py | 120 | OK | `--read` flag present in `_build_aider_cmd` |
| `aider_builder.py` uses `--read config/qc_tools_manifest.json` (not `--mcp-config`) | scripts/aider_builder.py | 121 | OK | `--mcp-config` absent from command construction |
| Tier 3 uses `--thinking-tokens 8192` | scripts/aider_builder.py | 51, 433 | OK | `_TIER3_THINKING_BUDGET = 8192`; `thinking_args = ["--thinking-tokens", str(_TIER3_THINKING_BUDGET)]` |
| `ANTHROPIC_API_KEY` is not used (arch: GEMINI_API_KEY only) | scripts/strategy_reviewer.py | 212 | MISMATCH | `strategy_reviewer.py` reads and requires `ANTHROPIC_API_KEY` for Tier 4 (Claude Opus) |
| `OPENAI_API_KEY` is not used (arch: GEMINI_API_KEY only) | scripts/strategy_reviewer.py | 192 | MISMATCH | `strategy_reviewer.py` reads and requires `OPENAI_API_KEY` for Tier 3 (gpt-4o-mini) |
| `OPENAI_API_KEY` secret not required by pipeline | .github/workflows/acb_pipeline.yml | 104, 110, 355 | MISMATCH | Validated as a required secret (line 110); passed to `aider-build` step env (line 355) despite `aider_builder.py` not consuming it |
| Pre-commit gates: CCN threshold is `< 10` | scripts/pre_commit_gates.py | 33 | OK | `_CCN_THRESHOLD: int = 10` |
| Pre-commit gates: function length threshold is `< 150` lines | scripts/pre_commit_gates.py | 34 | OK | `_FUNCTION_MAX_LINES: int = 150` |
| Pre-commit gates: parameter count threshold is `< 8` | scripts/pre_commit_gates.py | 35 | OK | `_PARAM_MAX_COUNT: int = 8` |
| Pre-commit gates: stub detection with allowlist | scripts/pre_commit_gates.py | 307 | OK | `check_stub_detection` function present |
| E2E reports committed to `infinity-lab-private` — NEVER to `infinity-lab-public` | .github/workflows/acb_pipeline.yml | 657 | MISMATCH | Report written and pushed to `reports/e2e/` inside `infinity-lab-public` (same repo) |
| `scripts/ack_gate.py` must not exist | scripts/ack_gate.py | N/A | OK | File absent from repository |
| `scripts/qc_deploy_live.py` must not exist | scripts/qc_deploy_live.py | N/A | OK | File absent from repository |

---

## Section 7 — Cleanup Task Status

| # | File | Status | Notes |
|---|------|--------|-------|
| 1 | scripts/ack_gate.py | RESOLVED | File does not exist in repository |
| 2 | scripts/pre_commit_gates.py | RESOLVED | `--output` path written to `/tmp/pre_commit_gates_<spec_name>.json` (acb_pipeline.yml:407) matches `--pre-commit-output` read by `human_review_artifacts.py` (acb_pipeline.yml:460) |
| 3 | acb_pipeline.yml | OPEN | Step 12 E2E report is committed and pushed to `infinity-lab-public/reports/e2e/` (acb_pipeline.yml:657–662); architecture requires `infinity-lab-private` |
| 4 | scripts/qc_promote.py | RESOLVED | File exists (327 lines); called at acb_pipeline.yml:437 |

---

## Section 9 — Hard Constraint Violations

| Constraint | File | Line | Verdict |
|------------|------|------|---------|
| ACB may CREATE new QC projects — never update or delete existing ones | scripts/qc_promote.py | 137 | MISMATCH |
| ACB may CREATE new QC projects — never update or delete existing ones | scripts/mcp_tool_discovery.py | 63–64 | MISMATCH |
| ACB may never call QC live trading, paper trading, or portfolio management endpoints | scripts/mcp_tool_discovery.py | 95–105 | MISMATCH |
| ACB may never call QC live trading, paper trading, or portfolio management endpoints | scripts/qc_upload_eval.py | 8, 47 | MISMATCH |
| ACB may never modify files in a QC project tagged `paper-*` or `live-*` | scripts/qc_promote.py | 136–147 | MISMATCH |
| `qc_deploy_live.py` must not exist in `infinity-lab-public` | scripts/qc_deploy_live.py | N/A | OK |

**Violation details:**

1. **`qc_promote.py:137`** — `_ALLOWED_ENDPOINTS` includes `"files/update"`. This permits overwriting file content in any existing QC project, violating the "never update existing" constraint.

2. **`mcp_tool_discovery.py:63–64`** — The static catalogue includes `update_project` (line 63) and `delete_project` (line 64) in the `"project"` category. These tool definitions are written to `config/qc_tools_manifest.json` and passed to the LLM via `--read config/qc_tools_manifest.json` (aider_builder.py:121), enabling the LLM to invoke project update/delete operations.

3. **`mcp_tool_discovery.py:95–105`** — The static catalogue includes `create_live_algorithm` (95), `read_live_algorithm` (96), `update_live_algorithm` (97), `delete_live_algorithm` (98), `list_live_algorithms` (99), `read_live_orders` (100), `read_live_trades` (101), `read_live_charts` (102), `read_live_insights` (103), `read_live_portfolio` (104), and `read_live_logs` (105) in the manifest exposed to the LLM. These live trading tools are published to `config/qc_tools_manifest.json`, violating the "never call live trading endpoints" constraint.

4. **`qc_upload_eval.py:8, 47`** — The script accesses `http://localhost:8000/mcp` via JSON-RPC 2.0 (module docstring line 8; `os.environ.get("QC_MCP_BASE_URL", "")` line 47). The architecture (Section 2) classifies port 8000 as "REST API only (not local MCP)"; using JSON-RPC 2.0 to a `/mcp` endpoint is MCP protocol, not REST.

5. **`qc_promote.py:136–147`** — `_assert_allowed_endpoint` enforces only the endpoint allowlist; there is no check that the target project name does not match `paper-*` or `live-*` patterns. Any project regardless of name can be written to via the allowed endpoints.

---

## Orphan Scripts

| Script | Called by acb_pipeline.yml? | Notes |
|--------|----------------------------|-------|
| scripts/setup_slack_app.py | NO | Called by `.github/workflows/setup_slack_app.yml:31` only |
| scripts/slack_api.py | NO | Not called directly and not imported by any script that acb_pipeline.yml invokes |

---

## Orphan Workflow Steps

| Step | Script Called | Script Exists? |
|------|---------------|----------------|
| — | — | All scripts invoked by acb_pipeline.yml exist in scripts/ |

No orphan workflow steps detected.

---

## Dead Files (should be deleted)

| File | Reason |
|------|--------|
| — | Both `scripts/ack_gate.py` and `scripts/qc_deploy_live.py` listed in Section 7 / Section 5 are already absent from the repository |

No dead files remain.

---

## Summary

| Category | Total | OK | MISSING | BROKEN | MISMATCH |
|----------|-------|----|---------|--------|----------|
| Section 4 — Pipeline Steps | 13 | 12 | 0 | 0 | 1 |
| Section 5 — Implementation Notes | 14 | 9 | 0 | 0 | 4 (across 5 rows) |
| Section 7 — Cleanup Tasks | 4 | 3 (RESOLVED) | 0 | 0 | 1 (OPEN) |
| Section 9 — Hard Constraints | 6 | 1 | 0 | 0 | 5 |
| Orphan Scripts | 2 | 0 | 0 | 0 | 2 |
| Orphan Workflow Steps | 0 | 0 | 0 | 0 | 0 |
| Dead Files | 0 | 0 | 0 | 0 | 0 |
| **TOTAL** | **39** | **25** | **0** | **0** | **13** |
