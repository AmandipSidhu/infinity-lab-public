# DAC v2 Architecture — Mia2-Style Build Loop

**Version:** 2.1  
**Status:** Design complete — pending Gate 0 (UNI-97) + implementation  
**Supersedes:** DAC v1 (`gemini_builder.py` + `qc_upload_eval.py` pattern, UNI-95)  
**Linear refs:** [UNI-96](https://linear.app/universaltrading/issue/UNI-96) · [UNI-97](https://linear.app/universaltrading/issue/UNI-97)

---

## Why This Exists

DAC v1 closed one loop: Gemini got real LEAN compile errors instead of mock pytest output. That was a major improvement over Aider. But it left a second, more expensive loop broken:

**The LLM that wrote the code never saw the backtest results.**

`qc_upload_eval.py` read the backtest and reduced it to a PASS/FAIL violation string. Gemini received a label like `sharpe_ratio_min: Sharpe was 0.3, minimum is 0.6` — a fact with no diagnostic signal. It had no access to the orders that produced that Sharpe, the fees that drained it, the win rate, or the log output. Every "fix" was a guess.

Mia2 does not guess. It reads `read_backtest_orders`, `read_backtest_statistics`, and `read_backtest_logs` **in the same session that wrote the code** and reasons over raw data. That is the standard DAC v2 is built to match.

---

## What Changed vs DAC v1

| | DAC v1 | DAC v2 |
| -- | -- | -- |
| **Code → QC** | `qc_upload_eval.py` REST API | QC MCP `update_file_contents` |
| **Compile feedback** | REST response → formatted string | QC MCP `read_compile` → raw error, same context |
| **Backtest feedback** | REST → PASS/FAIL scalar verdict | QC MCP `read_backtest_orders` + `read_backtest_statistics` + `read_backtest_logs` → raw data |
| **What LLM sees** | Violation label | Actual orders, fees, win rate, log lines |
| **Session continuity** | Build session dies; separate process reads backtest | One unbroken session: the LLM that wrote line 47 is still alive when the backtest says line 47 is wrong |
| **Hypothesis formation** | None — blind retry | Agent forms and scores hypotheses before retrying |
| **Human checkpoint** | None | Phase 3: agent surfaces diagnosis, halts or self-corrects per `AUTO_ITERATE` flag |
| **Slack** | Not present | Output notifications — v1 scope (see below) |

`qc_upload_eval.py` is **retired** from the agent feedback loop. The REST API is not used for backtest reads. MCP tool calls replace it entirely.

---

## Three-Node Runtime

Three environments. Each has exactly one role. None overlap.

```
┌─────────────────────────────────────────────────────────────┐
│  PERPLEXITY (inference layer — chat)                        │
│                                                             │
│  • User describes strategy in natural language              │
│  • Perplexity infers intent → authors spec YAML             │
│  • Validates against SPEC_TEMPLATE v2 in-context            │
│  • Commits spec as a PR to GitHub via MCP tools             │
│                                                             │
│  No build logic here. Output is always a valid spec YAML.   │
└───────────────────────┬─────────────────────────────────────┘
                        │  PR with specs/{name}.yaml
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  GITHUB ACTIONS (build runner)                              │
│                                                             │
│  Trigger: push to specs/*.yaml (PR merge) or               │
│           workflow_dispatch                                 │
│                                                             │
│  Runs: gemini_builder.py (DAC v2)                           │
│  • Reads spec YAML                                          │
│  • Calls Gemini SDK directly (no CLI)                       │
│  • Calls QC MCP HTTP server for all QC operations           │
│  • Runs Phase 1 → 2 → 3 loop entirely inside runner        │
│  • Posts Slack notifications at every phase transition      │
│                                                             │
│  No QC REST API. No qc_upload_eval.py. MCP only.           │
└───────────────────────┬─────────────────────────────────────┘
          │ HTTP tool calls          │ Slack webhook (output only)
          ▼                          ▼
┌────────────────────┐  ┌────────────────────┐
│  GCP e2-micro            │  │  SLACK (v1: output)    │
│  QC MCP HTTP server      │  │                        │
│  (always-on)             │  │  Receives:             │
│                          │  │  • Build start        │
│  uvx quantconnect-mcp    │  │  • Compile errors     │
│  streamable-http :8000   │  │  • Backtest complete  │
│                          │  │  • Phase 3 diagnosis  │
│  Tools per iteration:    │  │  • Pass / hard fail   │
│  update_file_contents    │  │                        │
│  create_compile          │  │  Read-only window.     │
│  read_compile            │  │  No input in v1.       │
│  create_backtest         │  └────────────────────┘
│  read_backtest           │
│  read_backtest_orders    │
│  read_backtest_statistics│
│  read_backtest_logs      │
└────────────────────┘
```

---

## Slack Integration

### v1 Scope (DAC v2 — this build)

Slack is **output only**. The build loop posts notifications at every meaningful state transition. No input, no webhook listener, no reply handling.

**Posts sent:**

| Event | Message |
| -- | -- |
| Build start | `🔨 Building {name} — iteration 1 (Gemini Flash)` |
| Compile error caught | `⚠️ Compile error: {raw error line}. Retrying iteration {n}...` |
| Clean compile | `✅ Compiled clean — starting backtest` |
| Backtest result + hypothesis | `📊 Backtest: Sharpe {x} (need {y}). Hypothesis: {top hypothesis}. {AUTO_ITERATE action}` |
| Phase 3 halt | `🛑 Halted after {n} iterations. Best Sharpe: {x}. {diagnosis + proposed fix}` |
| Pass | `🟢 PASS — {name} written to strategies/{name}/main.py. Sharpe {x}, drawdown {y}%` |
| Hard fail | `🔴 FAILED — {name} after {n} iterations. No file written. {final error}` |

**Secret already in repo:** `SLACK_WEBHOOK_URL` — no new infrastructure required.

### v2 Scope (future — not in this build)

Slack becomes a **two-way channel**: user replies to a Phase 3 halt message in plain English, a webhook triggers `workflow_dispatch`, Perplexity infers the redirect, a new spec PR is created automatically.

> **Scope gate:** Do not implement Slack input handling until DAC v2 is running end-to-end and producing real Phase 3 halts. Build the input channel against real output, not hypothetical output.

---

## The Inference Layer (How Specs Get Created)

The spec YAML is **not hand-authored by the user**. It is the output of an intent inference step that happens before the build loop.

```
User (natural language)
  "VWAP fade on SPY, 60 min hold, stop at 2x ATR"
          ↓
Perplexity (inference)
  • Maps intent to SPEC_TEMPLATE v2 fields
  • Fills required fields; applies safe defaults for unspecified values
  • Validates mentally against SVR error codes
  • Never uses banned vague terms (SVR-E034)
          ↓
spec YAML (complete, valid, exit code 0)
          ↓
Perplexity commits PR via GitHub MCP
  push_files → branch
  create_pull_request → triggers GH Actions on merge
          ↓
DAC v2 build loop begins
```

The user never opens a YAML file. The validator (`spec_validator.py`) still runs in CI as a hard gate — but it should always pass because Perplexity pre-validates.

### Safe Defaults Applied During Inference

| Field | Default (when unspecified) |
| -- | -- |
| `capital.allocation_usd` | `100000` |
| `data.resolution` | `minute` (day_trade) · `daily` (swing/position) |
| `data.lookback_years` | `5` |
| `assumptions.fees` | `0.001` |
| `assumptions.slippage` | `0.0005` |
| `acceptance_criteria.min_trades` | `200` (day_trade) · `50` (position) |
| `risk_management.position_sizing` | `percentage` |

---

## DAC v2 Build Loop (gemini_builder.py)

One process. One LLM session. One unbroken context.

### Phase 1 — Build

```
load spec YAML
build prompt (all fields injected — zero hardcoded indicators)
call Gemini SDK
extract code block
syntax check (py_compile)
  fail → feed error verbatim → retry
  → Slack: compile error notification
QC MCP: update_file_contents  (write to QC cloud)
QC MCP: create_compile
QC MCP: read_compile
  compile error → feed raw error verbatim → retry
  → Slack: LEAN compile error notification
clean compile → Slack: clean compile notification → Phase 2
```

### Phase 2 — Backtest + Deep Read

```
QC MCP: create_backtest
QC MCP: poll until complete
QC MCP: read_backtest          (full stats)
QC MCP: read_backtest_orders   (every order: fills, fees, direction)
QC MCP: read_backtest_statistics (Sharpe, drawdown, win rate, avg win/loss)
QC MCP: read_backtest_logs     (runtime output)

Agent reasons over raw data:
  → forms hypotheses (e.g. "fee drag = 26% of gross PnL")
  → scores hypotheses by impact
  → checks acceptance_criteria from spec

criteria met → Slack: PASS → write strategies/{name}/main.py → DONE
criteria not met → Slack: backtest result + top hypothesis → Phase 3
```

### Phase 3 — Checkpoint

```
Slack: post full diagnosis
  • top hypothesis with supporting data
  • specific order stats, fee breakdown, log excerpts
  • proposed fix
  • AUTO_ITERATE action taken
GITHUB_STEP_SUMMARY: same content

if AUTO_ITERATE=true:
  apply top hypothesis → back to Phase 1
if AUTO_ITERATE=false:
  halt — Slack: halted message — await human
  human comes back here (Perplexity), describes redirect
  → Perplexity infers new spec → new PR → new build
```

### Iteration / Model Escalation

| Iteration | Model |
| -- | -- |
| 1–3 | `gemini-2.0-flash` |
| 4 | `gemini-2.0-pro-exp` |
| 5 | `gemini-2.0-pro-exp` — final attempt, then FAIL |

---

## Hard Constraints (Non-Negotiable)

- **NEVER write a file that failed syntax check or LEAN compile**
- **NEVER stub** — no skeleton code, no `pass`, no placeholder — hard fail instead
- **Prompt reads all fields from spec YAML** — zero hardcoded indicator values
- **Each iteration prompt includes the previous error verbatim**
- **Phase 2 MUST call** `read_backtest_orders`, `read_backtest_statistics`, `read_backtest_logs` — not just scalar checks
- **Phase 3 checkpoint MUST surface a hypothesis**, not just a failure code
- **Slack posts are fire-and-forget** — never block the build loop waiting for a reply
- `AUTO_ITERATE` flag controls self-correction vs human-in-the-loop
- Token limits are a later problem — design for correctness first

---

## What Is NOT Changing

| Component | Status | Reason |
| -- | -- | -- |
| `spec_validator.py` | ✅ Unchanged | Rules are correct; becomes CI gate not human homework |
| `SPEC_TEMPLATE.md` | ✅ Unchanged | Becomes Perplexity's inference schema reference |
| `pre_commit_gates` | ✅ Unchanged | Downstream of build loop |
| `qc_promote.py` | ✅ Unchanged | Promotion gate unchanged |
| `aider_builder.py` | ✅ Preserved | Parallel pipeline, not removed |
| `qc_upload_eval.py` | ❌ Retired from agent loop | Replaced by MCP tool calls |
| Slack input webhook | ❌ Not in v1 | Deferred to v2 scope (see Slack section) |

---

## Infrastructure Requirements

### GCP e2-micro (QC MCP Server)

- **Status:** Verified locally (UNI-97 Gate 0) — permanent deployment pending
- **Deployment:**
  ```bash
  MCP_TRANSPORT=streamable-http MCP_HOST=0.0.0.0 MCP_PORT=8000 uvx quantconnect-mcp
  ```
- **Run as:** systemd service (auto-restart on crash/reboot)
- **Firewall:** port 8000 open to GitHub Actions IP ranges
- **Endpoint consumed by:** GH Actions runner via `QC_MCP_URL` secret
- **Cost:** GCP free tier (e2-micro)

### GitHub Secrets Required

| Secret | Status | Usage |
| -- | -- | -- |
| `GEMINI_API_KEY` | ❌ Needed | Gemini SDK calls in DAC v2 |
| `QC_USER_ID` | ✅ Exists | QC MCP server auth |
| `QC_TOKEN` | ✅ Exists | QC MCP server auth |
| `QC_MCP_URL` | ❌ Needed | `http://<GCP_IP>:8000/mcp` — set after GCP deploy |
| `SLACK_WEBHOOK_URL` | ✅ Exists | Phase notifications (output only) |

---

## Gate 0 — Proof of Foundation (UNI-97)

Before any DAC v2 code is written, all five of these must pass:

- [ ] `quantconnect-mcp` starts in HTTP mode without errors
- [ ] `validate_quantconnect_auth` returns success
- [ ] `read_backtest` returns Sharpe ~0.436 for backtest `29a044c64e018d411830f0580ae25dee` (project `28779543`)
- [ ] `read_backtest_orders` returns a non-empty array of real orders for the same backtest
- [ ] Both calls complete in < 10 seconds

**Gate 0 failing = architecture revision required. Do not proceed without it.**

---

## References

- **UNI-95** — DAC v1 design: https://linear.app/universaltrading/issue/UNI-95
- **UNI-96** — DAC v2 Mia2 architecture: https://linear.app/universaltrading/issue/UNI-96
- **UNI-97** — Gate 0 QC MCP verification: https://linear.app/universaltrading/issue/UNI-97
- **SPEC_TEMPLATE.md** — `docs/SPEC_TEMPLATE.md` (this repo)
- **quantconnect-mcp** — https://pypi.org/project/quantconnect-mcp/
- **Official QC MCP docs** — https://www.quantconnect.com/docs/v2/ai-assistance/mcp-server/key-concepts
- **Known-good backtest** — `29a044c64e018d411830f0580ae25dee` (project `28779543`, Sharpe 0.436)
