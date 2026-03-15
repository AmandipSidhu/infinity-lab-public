# DAC v2 Architecture

**Version:** 2.2  
**Last updated:** 2026-03-15  
**Status:** Designed — blocked on Gate 0 (UNI-97)

---

## 1. Overview

DAC v2 is a Mia2-process architecture: one LLM session, one unbroken context, a live QC loop.  
The sole evolution point is `scripts/gemini_builder.py`. Everything upstream (spec validator, strategy reviewer) and downstream (pre-commit gates, qc_promote) is unchanged.

**RACI**

| Role | Executor |
|---|---|
| Human — hypothesis, spec approval, capital decisions | Amandip |
| AI assistant — research, doc writing, Linear/GitHub ops, spec YAML authoring | Perplexity |
| Copilot agent — PR creation, code implementation | GitHub Copilot |
| Code writer / LEAN strategy builder | DAC (`gemini_builder.py`) |
| Backtest execution | QuantConnect cloud |

---

## 2. Gate 0 — Prerequisite (UNI-97)

**No DAC v2 work begins until Gate 0 passes.**

Gate 0 verifies the QC MCP server (`quantconnect-mcp` community package) is callable and returns deep backtest data.

**Pass criteria — ALL five must pass:**

- [ ] `quantconnect-mcp` starts in HTTP mode (`MCP_TRANSPORT=streamable-http`)
- [ ] `validate_quantconnect_auth` returns success
- [ ] `read_backtest` returns Sharpe ~0.436 for backtest `29a044c64e018d411830f0580ae25dee` (project `28779543`)
- [ ] `read_backtest_orders` returns a non-empty array of real orders
- [ ] Both calls complete in < 10 seconds

**Gate 0 automation:** UNI-98 Copilot task creates `.github/workflows/gate0_qc_mcp_verify.yml` — a `workflow_dispatch`-only diagnostic workflow.

---

## 3. Three-Phase Loop

```
Phase 1 — BUILD (up to N iterations)
  write code → update_file (QC cloud)
  → create_compile → read_compile
  compile error? → reason → fix → repeat
  clean compile → Phase 2

Phase 2 — BACKTEST + DEEP READ
  create_backtest → poll → read_backtest
  read_backtest_statistics
  read_backtest_orders
  read_backtest_logs
  → agent reasons: form hypotheses, score them
  criteria met? → DONE
  criteria not met? → Phase 3

Phase 3 — CHECKPOINT (the Mia2 pause)
  output diagnosis to Slack / GITHUB_STEP_SUMMARY
  if AUTO_ITERATE=true → apply top hypothesis → back to Phase 1
  if AUTO_ITERATE=false → halt, surface diagnosis, await human
```

**Phase 2 is mandatory.** DAC must call `read_backtest_orders`, `read_backtest_statistics`, and `read_backtest_logs` — not check scalar thresholds from `qc_upload_eval.py`.

---

## 4. AUTO_ITERATE Flag

| Value | Behaviour |
|---|---|
| `true` | Walks away mode — runs all iterations unattended, posts Slack at each phase transition, halts only on hard fail or pass |
| `false` | Checkpoint mode — halts after every failed backtest, posts full diagnosis + hypothesis, waits for human decision |

Rule of thumb: new untested strategy → `false`. Known pattern, tuning a parameter → `true`.

---

## 5. Key Design Constraints

- **NEVER write a file that failed syntax check or LEAN compile**
- **NEVER stub** — no skeleton, no `pass`, no placeholder — hard fail instead
- **Prompt reads all fields from spec YAML** — zero hardcoded indicators
- **Each iteration prompt includes the previous error verbatim**
- **Phase 2 must call** `read_backtest_orders`, `read_backtest_statistics`, `read_backtest_logs`
- **Phase 3 checkpoint must surface a hypothesis, not just a failure code**
- `AUTO_ITERATE` flag controls whether agent self-corrects or halts for human

---

## 6. LEAN Reference Library (RAG Pattern)

### Decision

Reference content (guard rails, built-in class list, timing patterns, realism settings, research integrity rules) is **NOT** embedded statically in `prompt_template.py`. It is stored in versioned markdown files under `docs/lean_reference/` and retrieved on demand.

### Storage

```
docs/lean_reference/
  README.md                ← topic index + when to use each file
  guard_rails.md           ← warmup, market-open, zero-qty, tradable guards
  lean_builtins.md         ← Tier 3 built-in class list with import paths
  timing_patterns.md       ← schedule.on, stale price, free-portfolio patterns
  realism_settings.md      ← brokerage models, slippage, fee models
  research_integrity.md    ← bias types, walk-forward rules, hypothesis-first rule
```

Access URL pattern:
```
https://raw.githubusercontent.com/AmandipSidhu/infinity-lab-public/main/docs/lean_reference/{topic}.md
```

### Access Layer: `scripts/lean_reference.py` (~40 lines)

```python
import os
import urllib.request

_BASE_URL = (
    "https://raw.githubusercontent.com/"
    "AmandipSidhu/infinity-lab-public/main/docs/lean_reference"
)

VALID_TOPICS = {
    "guard_rails",
    "lean_builtins",
    "timing_patterns",
    "realism_settings",
    "research_integrity",
}

def fetch(topic: str) -> str:
    if topic not in VALID_TOPICS:
        raise ValueError(f"Unknown topic: {topic!r}")
    local_dir = os.environ.get("LEAN_REFERENCE_DIR")
    if local_dir:
        path = os.path.join(local_dir, f"{topic}.md")
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            pass
    url = f"{_BASE_URL}/{topic}.md"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.read().decode("utf-8")
    except Exception as exc:
        print(f"[lean_reference] WARNING: could not fetch {topic}: {exc}")
        return ""  # non-fatal — build proceeds without this chunk
```

- `LEAN_REFERENCE_DIR` env var allows CI to serve files locally (no network call during tests)
- Fetch failure is **non-fatal** — returns `""`, build continues
- `urllib.request` only — zero new dependencies
- 5s timeout

### Injection into `gemini_builder.py`

Fetch happens **once per strategy build** (before the iteration loop), not per iteration.

```
Current flow:
  build_prompt(spec, feedback) → call_gemini(prompt)

v2 flow:
  fetch_references(spec) → build_prompt(spec, feedback, refs) → call_gemini(prompt)
```

`fetch_references()` is selective — only topics relevant to the spec are fetched:

```python
def fetch_references(spec: dict) -> dict[str, str]:
    refs = {
        "guard_rails": fetch("guard_rails"),
        "lean_builtins": fetch("lean_builtins"),
    }
    assumptions = spec.get("assumptions") or {}
    if assumptions.get("fees") or assumptions.get("slippage"):
        refs["realism_settings"] = fetch("realism_settings")
    strategy_type = (spec.get("strategy") or {}).get("type", "")
    if strategy_type in ("swing", "position", "monthly_rebalance"):
        refs["timing_patterns"] = fetch("timing_patterns")
    return refs
```

### Two Callers

| Caller | When | Topics |
|---|---|---|
| `gemini_builder.py` (DAC) | Before code generation, every build | `guard_rails` + `lean_builtins` always; others conditionally per spec |
| Perplexity (inference layer) | When authoring spec from natural language | `research_integrity` — applies bias-awareness rules to spec design |

---

## 7. What Stays in `prompt_template.py`

Only content that is **structurally true forever**:

| What | Why it stays |
|---|---|
| DAC role definition | Never changes |
| Phase 1 → 2 → 3 loop instructions | Core loop logic |
| Hard constraints (no stubs, no hardcoded indicators) | Non-negotiable rules |
| Instruction: *"before writing code, call `fetch_reference`"* | Bootstraps retrieval behaviour |

No code snippets. No class lists. No pattern blocks in `prompt_template.py`.

---

## 8. Slack Integration

### v1 — Output Only (implement with DAC v2 core)

Every meaningful state transition in `gemini_builder.py` posts to `SLACK_WEBHOOK_URL`:

```
Phase 1 start:         🔨 Building {name} — iteration {n} ({model})
Compile error:         ⚠️ Compile error line {L}: {error} — Retrying {n+1}...
Clean compile:         ✅ Compiled clean — starting backtest
Backtest, AUTO=true:   📊 Backtest done. Sharpe {x} (need {y}) — Hypothesis: {h} — Fix: {f} — AUTO_ITERATE=true applying fix
Phase 3 halt:          🛑 Halted after {n} iterations. Best: Sharpe {x} — Hypothesis: {h} — Proposed: {f}
Pass:                  ✅ PASS — {name} met all criteria. Sharpe {x} · Drawdown {y}% · Trades {z}
```

### v2 — Slack Input Channel (deferred, post Gate 0)

```
Slack reply (plain English)
  → lightweight webhook
  → triggers workflow_dispatch with message as input
  → Perplexity infers new spec
  → PR created
  → build restarts
```

---

## 9. Three-Tier Reference Library (for DAC code generation)

**Tier 1 — Official LEAN Regression Tests (highest trust)**  
Source: `QuantConnect/Lean/Algorithm.Python/` — CI-tested before every release.

Key examples:
- `BasicTemplateFrameworkAlgorithm.py` — canonical Framework pipeline
- `BasicTemplateDailyAlgorithm.py` — daily resolution baseline
- `CoarseFineFundamentalComboAlgorithm.py` — point-in-time universe (survivorship-safe)
- `BlackLittermanPortfolioOptimizationFrameworkAlgorithm.py` — built-in PCM
- `CapmAlphaRankingFrameworkAlgorithm.py` — factor alpha ranking

**Tier 2 — Official QC AI Trading Book**  
Source: `QuantConnect/HandsOnAITradingBook` (2024, QC-official)

**Tier 3 — LEAN Framework Built-ins (drop-in models)**

| Need | Built-in class |
|---|---|
| Portfolio construction | `EqualWeightingPortfolioConstructionModel`, `RiskParityPortfolioConstructionModel`, `BlackLittermanOptimizationPortfolioConstructionModel` |
| Alpha | `ConstantAlphaModel`, `MacdAlphaModel`, `RsiAlphaModel` |
| Universe | `QQQUniverseSelectionModel`, `ManualUniverseSelectionModel`, `FundamentalUniverseSelectionModel` |
| Execution | `ImmediateExecutionModel`, `VolumeWeightedAveragePriceExecutionModel` |
| Risk | `MaximumDrawdownPercentPerSecurity`, `TrailingStopRiskManagementModel` |

---

## 10. Build Order

| Step | What | Status |
|---|---|---|
| Gate 0 | Run `gate0_qc_mcp_verify.yml`, confirm pass criteria | ⛔ Blocked — needs workflow run |
| 1 | Create `docs/lean_reference/` — 5 markdown files from UNI-99 content | ⏳ Pending Gate 0 |
| 2 | Create `scripts/lean_reference.py` — fetch module (~40 lines) | ⏳ Pending |
| 3 | Update `scripts/prompt_template.py` — add `refs` parameter, inject reference block | ⏳ Pending |
| 4 | Update `scripts/gemini_builder.py` — Phase 2 deep read loop, Phase 3 checkpoint, `fetch_references()` call | ⏳ Pending |
| 5 | Add `LEAN_REFERENCE_DIR` to CI workflow env for offline testing | ⏳ Pending |
| 6 | Slack v1 output posts wired in `gemini_builder.py` | ⏳ Pending |

---

## 11. What This Is NOT

- Not a UI replication of Mia2
- Not a rewrite of the full pipeline
- Not a new YAML schema
- Not a customization of the spec format

---

## References

- **UNI-96** (DAC v2 architecture): https://linear.app/universaltrading/issue/UNI-96
- **UNI-97** (Gate 0 verification): https://linear.app/universaltrading/issue/UNI-97
- **UNI-98** (Gate 0 Copilot task): https://linear.app/universaltrading/issue/UNI-98
- **UNI-99** (LEAN best practices / RAG source): https://linear.app/universaltrading/issue/UNI-99
- **UNI-95** (DAC v1 — merged PR #122): https://linear.app/universaltrading/issue/UNI-95
- **Mia2 demo video**: https://www.youtube.com/watch?v=lKzPauVifZY
- **QC MCP community package**: https://pypi.org/project/quantconnect-mcp/
- **LEAN Algorithm.Python examples**: https://github.com/QuantConnect/Lean/tree/master/Algorithm.Python
- **QC HandsOnAITradingBook**: https://github.com/QuantConnect/HandsOnAITradingBook
- **Known-good backtest**: `29a044c64e018d411830f0580ae25dee` (project `28779543`, Sharpe 0.436)
