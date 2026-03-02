# Task: Fix Multi-Spec Push Handling in ACB Pipeline

## Problem

The current `acb_pipeline.yml` uses `head -n 1` when resolving the changed spec file:

```bash
SPEC_FILE=$(git diff --name-only HEAD~1 HEAD -- 'specs/**' | head -n 1)
```

If a single push touches more than one file under `specs/`, only the **first** changed spec is processed. All others are **silently ignored** — no error, no warning.

This is a correctness bug. Every changed spec in a push must be independently validated, reviewed, and ACK-gated.

---

## Deliverables

1. **Updated `.github/workflows/acb_pipeline.yml`** — replace the single-spec linear job with a matrix strategy that fans out one job per changed spec file
2. **Updated `tests/test_acb_pipeline.py`** — add tests covering multi-spec detection logic (if any helper script is extracted)

---

## Required Behaviour

### Detecting all changed specs

Extract ALL changed files under `specs/` from the push:

```bash
git diff --name-only HEAD~1 HEAD -- 'specs/**'
```

For `workflow_dispatch` with a single `spec_file` input, treat that as a single-element list.

### Matrix fan-out

Use a two-job structure:

**Job 1: `detect-specs`**
- Runs `git diff` to collect all changed spec paths
- Outputs a JSON array: `["specs/foo.yaml", "specs/bar.yaml"]`
- Sets `matrix_specs` output using `$GITHUB_OUTPUT`
- If list is empty: fail with `::error::No spec files found in specs/ for this push`

**Job 2: `acb-pipeline`**
- `needs: detect-specs`
- `strategy.matrix.spec_file: ${{ fromJson(needs.detect-specs.outputs.matrix_specs) }}`
- `strategy.fail-fast: false` — one failing spec must NOT cancel other spec jobs
- Each matrix job receives `matrix.spec_file` instead of `steps.find-spec.outputs.spec_file`
- Remove the `find-spec` step entirely — spec path comes from matrix
- All subsequent steps (`validate`, `review`, `evaluate`, `ack-gate`, `aider-build`) remain identical, referencing `${{ matrix.spec_file }}` instead

---

## Workflow Structure (Target)

```yaml
jobs:
  detect-specs:
    runs-on: ubuntu-latest
    outputs:
      matrix_specs: ${{ steps.get-specs.outputs.matrix_specs }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 2
      - name: Get changed spec files
        id: get-specs
        run: |
          # collect changed specs, build JSON array, write to GITHUB_OUTPUT
          # handle workflow_dispatch spec_file input
          # fail if empty

  acb-pipeline:
    needs: detect-specs
    runs-on: ubuntu-latest
    continue-on-error: false
    permissions:
      contents: read
    strategy:
      fail-fast: false
      matrix:
        spec_file: ${{ fromJson(needs.detect-specs.outputs.matrix_specs) }}
    steps:
      # All existing steps unchanged except:
      # - Remove find-spec step
      # - Replace all ${{ steps.find-spec.outputs.spec_file }} with ${{ matrix.spec_file }}
```

---

## Constraints (Zero-Placeholder Rules)

- NO PLACEHOLDERS. The `get-specs` step shell script must be fully implemented.
- `fail-fast: false` is mandatory — must not be omitted.
- `jq` is available on `ubuntu-latest` — use it to build the JSON array.
- The `workflow_dispatch` `spec_file` input must be respected as before.
- All step `id:` fields must be preserved.
- Do NOT change any script logic in `scripts/` — this is a workflow-only change.

---

## Acceptance Criteria

- [ ] Single spec push: matrix has 1 element — behaviour identical to current
- [ ] Multi-spec push: matrix has N elements — each spec runs independently in parallel
- [ ] One failing spec does NOT cancel other spec jobs (`fail-fast: false`)
- [ ] Empty `specs/` change: workflow fails with clear error annotation
- [ ] `workflow_dispatch` with `spec_file` input still works correctly
- [ ] All existing step `id:` fields preserved
- [ ] No placeholders anywhere
