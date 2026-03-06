# Task: Implement Phase 2 — Aider Build Step in ACB Pipeline

Phase 1 is complete and merged. The `acb_pipeline.yml` workflow now validates a spec, reviews it with the strategy reviewer, and gates on Slack ACK when WARNs are present.

**Phase 2 adds the Aider build step** — the final stage of the pipeline that fires after the pipeline is clear (validator passed, ACK received or zero WARNs).

---

## Context

- Workflow file: `.github/workflows/acb_pipeline.yml`
- Current final step: `Pipeline complete` (just an echo — placeholder for Phase 2)
- All Phase 0 scripts are in `scripts/`
- Python 3.11, `requirements.txt` already installed in workflow

---

## Deliverables

1. **`scripts/aider_builder.py`** — Python script that invokes Aider with the 4-tier model escalation chain
2. **Updated `.github/workflows/acb_pipeline.yml`** — replace the `Pipeline complete` echo step with a real `Aider build` step that calls `aider_builder.py`
3. **`tests/test_aider_builder.py`** — unit tests for `aider_builder.py`

---

## Aider Build Step — Behaviour

The build step receives the spec file path (already resolved in `find-spec` step output) and must:

1. Read the spec YAML to extract `metadata.name` and `metadata.trading_style`
2. Invoke Aider with the 4-tier model escalation chain (see below)
3. Write a build artifact summary to `$GITHUB_STEP_SUMMARY`
4. Exit 0 on success (strategy code generated and tests pass), exit 1 on failure

---

## 4-Tier Model Escalation Chain

Attempt tiers in order. Move to next tier on the trigger conditions listed.

### Tier 1 — Gemini 2.5 Flash (free)
- `--model gemini/gemini-2.5-flash`
- `GEMINI_API_KEY` env var
- Max iterations: 30
- Escalate on: rate limit (HTTP 429), timeout >30s, 3 consecutive syntax errors, same error 3× in a row

### Tier 2 — GitHub Models GPT-4o (free)
- `--model github/gpt-4o`
- `GITHUB_TOKEN` env var (models:read scope, already available in Actions)
- Max iterations: 30
- Escalate on: daily limit hit, API unavailable, timeout >30s, quality degradation (same error 3×)

### Tier 3 — GPT-5 (paid)
- `--model gpt-5`
- `OPENAI_API_KEY` env var
- Max iterations: 30
- Escalate on: 30 iterations exhausted with <70% tests passing, progressive degradation (fewer tests each iteration), stuck pattern (no forward progress for 8 iterations)

### Tier 4 — Claude Opus 4.5 (final boss)
- `--model claude-opus-4.5`
- `ANTHROPIC_API_KEY` env var — **not stored in GitHub secrets; supply locally with your own key**
- Max iterations: 30
- On failure: post diagnostic summary to `$GITHUB_STEP_SUMMARY` and exit 1 — manual intervention required

---

## Aider Invocation

```bash
aider \
  --model <model> \
  --yes \
  --no-git \
  --message "<prompt>" \
  <target_files>
```

The prompt passed to Aider must:
- Reference the spec file path explicitly
- Instruct Aider to implement a QuantConnect LEAN algorithm in `strategies/<spec_name>.py` that satisfies all `acceptance_criteria` in the spec
- Instruct Aider to write tests in `tests/test_<spec_name>.py`
- Instruct Aider to NOT modify any files outside `strategies/` and `tests/`

---

## Secrets / Env Vars Required

Add these to the workflow step env block. All secrets must already exist in repository Actions secrets before this workflow runs:

| Secret | Tier | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | 1 | Already present (used in strategy_reviewer step) |
| `GITHUB_TOKEN` | 2 | Auto-provided by Actions — no secret needed |
| `OPENAI_API_KEY` | 3 | GPT-5 access |
| `ANTHROPIC_API_KEY` | 4 | Claude Opus 4.5 access — **not stored in GitHub secrets; must be supplied locally** |

---

## Workflow Step Addition

Replace the existing `Pipeline complete` step with:

```yaml
- name: Aider build
  id: aider-build
  env:
    GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  run: |
    python scripts/aider_builder.py \
      --spec "${{ steps.find-spec.outputs.spec_file }}"
```

---

## Output Artifacts

`aider_builder.py` must write to `$GITHUB_STEP_SUMMARY`:

```
## Aider Build Results

**Spec**: `<spec_file>`
**Model used**: `<final tier model>`
**Tiers attempted**: <N>
**Iterations**: <N>
**Result**: SUCCESS / FAILURE
**Strategy file**: `strategies/<spec_name>.py`
**Test file**: `tests/test_<spec_name>.py`
```

---

## Constraints (Zero-Placeholder Rules)

- NO PLACEHOLDERS. Every tier must be fully implemented with real escalation logic.
- Retry/backoff must use exponential backoff with jitter for rate limit errors.
- Iteration tracking must be per-tier (reset to 0 when escalating).
- All file writes must use `pathlib.Path` and `encoding="utf-8"`.
- `aider_builder.py` must be importable as a module (guard `main()` under `if __name__ == "__main__"`).
- Tests must mock Aider subprocess calls — do NOT invoke real Aider in unit tests.
- Use `subprocess.run` to invoke Aider, capture stdout/stderr, check returncode.

---

## Acceptance Criteria

- [ ] `aider_builder.py` implements all 4 tiers with correct escalation logic
- [ ] Tier escalation triggers are correctly implemented (rate limit, timeout, syntax errors, iteration exhaustion)
- [ ] Exponential backoff with jitter on rate limit errors
- [ ] Workflow step wired correctly — fires after ACK gate clears
- [ ] `$GITHUB_STEP_SUMMARY` output written with build result
- [ ] On Tier 4 failure: exits 1 with diagnostic in step summary
- [ ] `tests/test_aider_builder.py` covers all 4 tier paths + escalation triggers
- [ ] No placeholders anywhere
