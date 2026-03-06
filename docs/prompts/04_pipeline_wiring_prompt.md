# Task: Implement ACB Pipeline GitHub Actions Workflow (`acb_pipeline.yml`)

Please implement the GitHub Actions workflow that wires the three Phase 0 scripts into the live ACB pipeline.

## Deliverables

1. `.github/workflows/acb_pipeline.yml` — the main pipeline workflow
2. `tests/test_acb_pipeline.py` — unit tests for any orchestration helper logic (if extracted)

---

## Workflow Behaviour

The workflow MUST execute these steps **in order**, with the exact gating logic below:

```
[Trigger]
    ↓
1. spec_validator.py   → if any ERROR: fail workflow immediately (exit 1)
    ↓ PASS
2. strategy_reviewer.py → collect all WARNs (SVR + SRV codes) into a list
    ↓
3. if WARN count == 0: proceed to exit 0
   if WARN count >= 1: surface WARNs in step summary (acknowledgment gate removed in v4.4)
    ↓
4. Exit 0 (pipeline clear — Aider build step will be added in Phase 2)
```

---

## Trigger

Trigger on:
- `push` to any branch where changed files include `specs/**`
- `workflow_dispatch` (manual trigger, for testing)

Do NOT trigger on every push to main — only when spec files change.

---

## Secrets / Env Vars Required

All secrets are already set in the repository's GitHub Actions secrets:

| Secret | Purpose |
|---|---|
| `SLACK_BOT_TOKEN` | Slack Web API bot token for pipeline notifications |
| `SLACK_ACK_CHANNEL_ID` | Slack channel ID (`C0A3CGW9ECS` = `#forge_reports`) |
| `GEMINI_API_KEY` | Required by `strategy_reviewer.py` (Gemini model tier 1) |

Expose these as environment variables in the relevant steps.

---

## Script Invocation

All scripts live in `scripts/`. Invoke them as Python modules:

```bash
# Step 1 — Spec Validator
python scripts/spec_validator.py --spec $SPEC_FILE
# Exit code 1 on any ERROR rule violation; 0 on PASS (WARNs allowed)

# Step 2 — Strategy Reviewer
python scripts/strategy_reviewer.py --spec $SPEC_FILE --output /tmp/reviewer_output.json
# Always exits 0; write verdict + warn list to --output JSON
```

The `SPEC_FILE` is the path to the changed spec YAML file. Use `git diff --name-only` filtered to `specs/` to find it.

---

## Constraints (Zero-Placeholder Rules)

- **NO PLACEHOLDERS.** Every step must be fully implemented — no `# TODO`, no `echo 'not implemented'`.
- Use `actions/checkout@v4` and `actions/setup-python@v5` with Python `3.11`.
- Cache pip dependencies using `actions/cache@v4` keyed on `requirements.txt` hash.
- All steps must have `id:` fields so outputs can be referenced.
- The workflow must surface the reviewer's WARN list in the GitHub Actions step summary (`$GITHUB_STEP_SUMMARY`) so it is visible in the PR check UI.
- Use `continue-on-error: false` everywhere — no silent failures.

---

## Requirements File

If `requirements.txt` does not already include the dependencies needed by the scripts, add them. Expected dependencies:
- `google-generativeai` (Gemini SDK)
- `openai` (GitHub Models / GPT-4o-mini fallback)
- `slack-sdk` (Slack Web API)
- `pyyaml` (spec YAML parsing)

---

## Acceptance Criteria

- [ ] Workflow triggers correctly on `specs/` path changes
- [ ] `spec_validator.py` ERROR causes immediate workflow failure (red check)
- [ ] `strategy_reviewer.py` WARNs appear in the GitHub Step Summary
- [ ] Zero WARNs path exits 0
- [ ] `requirements.txt` is complete (all script deps present)
- [ ] No placeholders anywhere in the workflow or any modified files
