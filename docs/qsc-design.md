# Quick Strike Coder (QSC) — Design Document
_Version 1.0 — March 24, 2026_

---

## Purpose

QSC is an overnight strategy grinder that produces **80%+ working QuantConnect strategies** from natural language prompts, minimizing reliance on Mia2 by injecting QC API documentation into the build context.

**Not a Mia2 replacement.** QSC handles high-volume prototype generation overnight. Mia2 remains the expert for:
- Debugging the 10-20% that fail
- Integrating complex components (regime classifiers, multi-timeframe logic)
- Optimizing winners

---

## Success Metrics

- **Target success rate:** 80% of prompts → working QC strategies
- **Throughput:** 30-40 strategies per 6-hour overnight run
- **Cost:** $0 (Gemini Flash free tier)
- **Morning outcome:** 8-10 working strategies to review, 2-4 failures packaged for Mia2

---

## Architecture

### Input: Smart Prompt Queue

**File:** `prompts/queue.md`

**Format:**
```markdown
## [PRIORITY] Strategy Name
Natural language description...

## [IF-PREVIOUS-PASSED] Strategy Variant
Take [parent strategy], add: modifications...

## [INDEPENDENT] Different Strategy
New strategy idea...

## [LOW-PRIORITY] Experimental Idea
(Only build if time remaining > 30min)
```

**Priority levels:**
- `[PRIORITY]` — Build first, parallel execution
- `[INDEPENDENT]` — Build after priority, parallel
- `[IF-PREVIOUS-PASSED]` — Sequential, only if parent succeeded
- `[LOW-PRIORITY]` — Fill remaining time budget

**Trigger:** Push to `prompts/**` auto-runs grinder workflow

---

### Pipeline Stages

#### Stage 1: Parse Prompts
- Extract headings from `prompts/queue.md`
- Build dependency graph (IF-PREVIOUS-PASSED chains)
- Generate execution plan with priority ordering

#### Stage 2: Priority Builds (Parallel)
- Run all `[PRIORITY]` and `[INDEPENDENT]` prompts in parallel (max 4 concurrent)
- Each build:
  1. Generate strategy name from prompt hash
  2. Run Aider with QC API docs + system prompt
  3. Quick validation (syntax + QC pattern checks)
  4. Submit to QuantConnect backtest
  5. Log result to `output/grinder_results.jsonl`

#### Stage 3: Conditional Builds (Sequential)
- For each `[IF-PREVIOUS-PASSED]` prompt:
  - Check parent result in `grinder_results.jsonl`
  - If parent passed QC: run Aider with "Take parent.py and modify..."
  - Else: skip and log "parent failed"

#### Stage 4: Low-Priority Fills
- Run `[LOW-PRIORITY]` prompts until 6-hour timeout or completion

#### Stage 5: Summary Report
- Aggregate `grinder_results.jsonl`
- Generate `output/grinder_summary.md`
- Post to GitHub Step Summary

---

## Critical Components

### 1. QC API Reference Document

**File:** `config/qc_api_reference.txt`

**Purpose:** Eliminate API hallucinations by showing Aider correct QuantConnect patterns

**Content:**
- Correct portfolio access patterns: `self.portfolio[symbol].invested`
- Correct order methods: `self.stop_market_order(symbol, qty, stop_price)`
- Common hallucinations with corrections:
  - ❌ `algorithm.portfolio.values` → ✅ `self.portfolio[symbol]`
  - ❌ `self.algorithm.stop_market_order()` → ✅ `self.stop_market_order()`
- Based on:
  - QC LEAN API docs (https://www.quantconnect.com/docs/v2)
  - Actual failure logs from Pink Pigeon and Giraffe builds

**Impact:** 50% → 80% success rate (eliminates most syntax/API errors)

---

### 2. Enhanced System Prompt

**File:** `config/aider_system_prompt_qsc.txt`

**Key additions beyond base Aider prompt:**
```
You are generating QuantConnect LEAN Python strategies.

CRITICAL RULES:
1. Read config/qc_api_reference.txt FIRST - it shows correct API patterns
2. Use ONLY patterns from qc_api_reference.txt - do NOT guess method names
3. Test understanding: before writing code, state which QC methods you'll use
4. All portfolio access via self.portfolio[symbol].method()
5. All orders via self.method_name() - never self.algorithm.method_name()

If unsure about an API, write a comment with your question rather than guessing.
```

---

### 3. Quick Validation Script

**File:** `scripts/qc_quick_validate.py`

**Checks:**
1. Python syntax: `python -m py_compile`
2. QC-specific patterns:
   - Reject: `algorithm.portfolio`
   - Reject: `self.algorithm.`
   - Require: `class <Name>Algorithm(QCAlgorithm)`

**Exit codes:**
- `0` = passed
- `1` = failed (with error message)

**Purpose:** Catch common errors before expensive QC backtest submission

---

### 4. Results Logging

**File:** `output/grinder_results.jsonl` (newline-delimited JSON)

**Schema per line:**
```json
{
  "timestamp": "2026-03-24T23:45:00Z",
  "prompt": "Build ORB 15min strategy...",
  "strategy_name": "orb_15min_base",
  "priority": "PRIORITY",
  "parent": null,
  "aider_success": true,
  "aider_tier": "gemini-flash",
  "syntax_valid": true,
  "qc_submitted": true,
  "qc_backtest_id": "Casual-Apricot-Mosquito",
  "qc_sharpe": 0.3,
  "qc_total_orders": 45,
  "qc_net_pnl_pct": 2.1,
  "status": "qc_success"
}
```

**Status values:**
- `qc_success` — Backtest ran, metrics available
- `qc_error` — Backtest submitted but QC errored
- `syntax_error` — Failed validation
- `aider_failed` — Aider timeout or error
- `skipped_parent_failed` — IF-PREVIOUS-PASSED skipped

---

### 5. Summary Report Generator

**File:** `scripts/generate_grinder_summary.py`

**Input:** `output/grinder_results.jsonl`

**Output:** `output/grinder_summary.md`

**Format:**
```markdown
# QSC Grinder Summary — 2026-03-24

## Overview
- 15 prompts attempted
- ✅ 11/15 Aider builds succeeded
- ✅ 10/15 passed syntax validation
- ✅ 8/15 ran successfully on QuantConnect
- Success rate: 53.3%

## Priority Builds (3 attempted)
| Strategy | Status | QC Sharpe | Orders |
|----------|--------|-----------|--------|
| orb_15min_base | ✅ Success | 0.3 | 45 |
| vwap_reversion | ✅ Success | -0.1 | 12 |
| gap_fade | ❌ Syntax error | - | - |

## Conditional Builds (5 attempted, 3 succeeded)
| Strategy | Parent | Status |
|----------|--------|--------|
| orb_volume_filter | orb_15min_base | ✅ Parent passed |
| orb_mtf | orb_volume_filter | ✅ Parent passed |
| orb_regime | orb_mtf | ⏭️ Skipped (parent failed) |

## Failures for Mia2 Escalation (7 total)
1. gap_fade — Syntax error line 47
2. complex_multi_asset — Aider timeout
...
```

---

## Workflow: `.github/workflows/qsc_grinder.yml`

### Trigger
```yaml
on:
  push:
    paths:
      - 'prompts/**'
```

### Jobs

**1. parse-prompts**
- Parse `prompts/queue.md` → JSON array
- Build dependency graph
- Output: `priority_prompts`, `independent_prompts`, `conditional_prompts`, `low_priority_prompts`

**2. priority-builds**
- Matrix: `${{ fromJson(needs.parse-prompts.outputs.priority_prompts) }}`
- Max parallel: 4
- Strategy: fail-fast disabled

**3. independent-builds**
- Matrix: `${{ fromJson(needs.parse-prompts.outputs.independent_prompts) }}`
- Max parallel: 4

**4. conditional-builds**
- Sequential execution
- Check parent result before each build

**5. low-priority-builds**
- Runs until timeout (6 hours) or completion

**6. summary**
- Always runs (even if builds fail)
- Generates summary report
- Posts to GitHub Step Summary

---

## Build Steps (Per Strategy)

```yaml
- name: Generate strategy name
  run: |
    name=$(echo "${{ matrix.prompt.title }}" | tr '[:upper:]' '[:lower:]' | tr -s ' ' '_')
    echo "name=$name" >> $GITHUB_OUTPUT

- name: Run Aider with QC API docs
  run: |
    aider --model gemini/gemini-2.5-flash \
          --read config/qc_api_reference.txt \
          --read config/aider_system_prompt_qsc.txt \
          --yes --no-git \
          --new-file strategies/${{ steps.name.outputs.name }}.py \
          --message "${{ matrix.prompt.content }}"

- name: Quick validation
  run: python scripts/qc_quick_validate.py strategies/${{ steps.name.outputs.name }}.py

- name: Submit to QC
  continue-on-error: true
  run: |
    python scripts/qc_upload_eval.py \
      --strategy strategies/${{ steps.name.outputs.name }}.py \
      --output output/${{ steps.name.outputs.name }}_qc_result.json

- name: Log result
  if: always()
  run: |
    python scripts/log_grinder_result.py \
      --prompt "${{ matrix.prompt.content }}" \
      --name "${{ steps.name.outputs.name }}" \
      --aider "${{ steps.aider.outcome }}" \
      --validate "${{ steps.validate.outcome }}" \
      --qc-result "output/${{ steps.name.outputs.name }}_qc_result.json"
```

---

## New Scripts Required

### `scripts/parse_prompts.py`
**Purpose:** Parse markdown headings into JSON with priority/dependencies

**Input:** `prompts/queue.md`

**Output:** JSON array:
```json
[
  {
    "title": "ORB 15min Base",
    "content": "Build opening range breakout...",
    "priority": "PRIORITY",
    "depends_on": null
  },
  {
    "title": "ORB Volume Filter",
    "content": "Take ORB 15min Base, add...",
    "priority": "IF-PREVIOUS-PASSED",
    "depends_on": "ORB 15min Base"
  }
]
```

---

### `scripts/qc_quick_validate.py`
**Purpose:** Fast QC-specific validation before backtest submission

**Checks:**
- Python syntax
- No `algorithm.portfolio.`
- No `self.algorithm.`
- Has `class *Algorithm(QCAlgorithm)`

**Exit codes:** 0 = pass, 1 = fail

---

### `scripts/log_grinder_result.py`
**Purpose:** Append build result to `grinder_results.jsonl`

**Inputs:**
- Prompt text
- Strategy name
- Aider outcome
- Validation outcome
- QC result JSON (if exists)

**Output:** One line appended to `grinder_results.jsonl`

---

### `scripts/generate_grinder_summary.py`
**Purpose:** Aggregate JSONL into human-readable summary

**Input:** `grinder_results.jsonl`

**Output:** `grinder_summary.md` with:
- Overall stats
- Priority builds table
- Conditional builds table
- Failures list for Mia2
- Top performers by Sharpe

---

### `scripts/package_failures_for_mia.py`
**Purpose:** Extract failures into Mia2-friendly context bundle

**Input:** `grinder_results.jsonl` + strategy files

**Output:** `mia_context/failed_builds.md` containing:
- Original prompts
- Aider-generated code
- Error messages
- QC logs (if reached that stage)

---

## Time Budget Model

**Per strategy (conservative):**
- Aider (Gemini Flash): 3-5 min
- Validation: 10 sec
- QC submit + backtest: 2-5 min
- Logging: 10 sec
- **Total: ~8 min**

**6-hour budget:**
- ~45 strategies theoretical max
- ~30-40 strategies realistic (accounting for failures/timeouts)

---

## User Workflow

### Night:
```bash
# Edit prompt file
vim prompts/queue.md

# Commit and push (triggers grinder)
git add prompts/queue.md
git commit -m "Add 15 strategy prompts"
git push
```

### Morning:
```bash
# Review summary
cat output/grinder_summary.md

# Check detailed results
jq . output/grinder_results.jsonl | less

# Package failures for Mia2
python scripts/package_failures_for_mia.py

# Feed to Mia2
# "Here are 7 failed grinder builds. Focus on the 3 that got furthest."
# Attach: mia_context/failed_builds.md + code files
```

---

## Dependencies on Existing ACB Components

**Reuse from existing codebase:**
- `scripts/aider_builder.py` — model invocation pattern (Tier 1 only)
- `scripts/qc_upload_eval.py` — QC backtest submission
- `scripts/qc_rest_client.py` — QC API client
- `config/aider_system_prompt_with_tools.txt` — base system prompt (extend for QSC)

**DO NOT reuse:**
- `spec_validator.py` — enforces YAML, we use natural language
- `strategy_reviewer.py` — AI policy review, overkill for experiments
- `pre_commit_gates.py` — production quality gates, too strict
- Tier 2-4 escalation — grinder uses Tier 1 only
- Slack notifications — operational friction

---

## Key Differences from ACB (Autonomous Code Builder)

| Aspect | ACB | QSC |
|--------|-----|-----|
| Input | Structured YAML spec | Natural language markdown |
| Validation | Spec validator + AI reviewer | Quick syntax + pattern check |
| Quality gates | Pre-commit (CCN, Bandit, Semgrep) | None (QC backtest is gate) |
| Model tier | 4-tier escalation | Tier 1 only (Gemini Flash) |
| Failure mode | Escalate to next tier | Log and move on |
| Success target | 95% production quality | 80% runnable prototype |
| Notifications | Slack gating | None (summary report only) |
| Purpose | Autonomous Mia2 replacement | High-volume prototype grinder |

---

## Implementation Phases

### Phase 1: Core Foundation (MVP)
**Est. 2-3 hours dev time**

1. Create `config/qc_api_reference.txt` from:
   - QC docs: https://www.quantconnect.com/docs/v2
   - Failure logs: Pink Pigeon, Giraffe builds

2. Write `scripts/parse_prompts.py`
   - Parse markdown headings
   - Extract priority levels
   - Build dependency graph

3. Write `scripts/qc_quick_validate.py`
   - Python syntax check
   - QC pattern validation

4. Write `scripts/log_grinder_result.py`
   - JSONL append logic

5. Write `scripts/generate_grinder_summary.py`
   - Aggregate JSONL → markdown summary

6. Create `.github/workflows/qsc_grinder.yml`
   - 5 jobs: parse → priority → independent → conditional → summary
   - Matrix execution with fail-fast disabled

### Phase 2: Refinement
**Post-MVP iteration**

7. Test with 5-10 prompts, tune `qc_api_reference.txt`
8. Add `scripts/package_failures_for_mia.py`
9. Add time budget tracking
10. Optimize parallel execution limits

### Phase 3: Integration
**After proven**

11. Document Mia2 handoff workflow
12. Update state.md with QSC as primary builder
13. Sunset or archive ACB components

---

## Success Criteria (MVP Complete)

- [ ] `prompts/queue.md` with 10 test prompts triggers workflow
- [ ] Grinder completes overnight (6-hour budget)
- [ ] `grinder_summary.md` generated with stats
- [ ] 80%+ prompts produce strategies that run on QC
- [ ] Failures packaged for Mia2 with full context
- [ ] Zero manual intervention required during overnight run

---

## Known Limitations

**Not solving (intentionally out of scope):**
- Complex multi-component strategies (regime classifier + portfolio coordination)
- Strategy optimization (Mia2's job)
- Production deployment (QC live trading setup)
- Historical performance validation beyond single backtest

**Requires manual follow-up:**
- Reviewing QC backtest details (metrics, equity curve)
- Deciding which winners to promote to production
- Feeding failures to Mia2

---

## Future Enhancements (v2+)

**Not for MVP, consider later:**
- Auto-retry failed builds with refined prompts
- Parallel backtest variations (different time periods, symbols)
- Integration with regime classifier for auto-gating
- Mia2 API integration (auto-escalate failures)
- Dashboard for historical grinder performance

---

## References

- Infinity ACB architecture: `docs/architecture.md`
- QC LEAN API: https://www.quantconnect.com/docs/v2
- Aider docs: https://aider.chat/docs/
- Failure logs: Available in private repo
