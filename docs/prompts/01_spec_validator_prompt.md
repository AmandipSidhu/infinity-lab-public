# Task: Implement `spec_validator.py` (Phase 0, Step 1)

Please implement the deterministic spec validator according to the rules defined in our repository.

## Requirements:
1. Read `docs/SPEC_VALIDATION_RULES.md` to understand the 56 SVR rules (Errors and Warnings).
2. Read `docs/SPEC_TEMPLATE.md` to understand the expected YAML schema of a trading strategy spec.
3. Create `scripts/spec_validator.py` which:
   - Parses a given YAML spec file.
   - Validates it against the SVR rules.
   - Returns a list of WARN and ERROR codes.
   - Fails the build (exit code 1) if any ERROR is present.
   - Outputs a JSON summary of findings.
4. Create `tests/test_spec_validator.py` and a test corpus in `tests/spec_corpus/` containing at least 2 valid specs and 3 invalid specs (to test Error and Warn conditions).

## Constraints:
- NO PLACEHOLDERS. Implement the full rule logic.
- Rely on standard libraries (`yaml`, `json`, `sys`, `os`, `re`) or explicitly state dependencies.
- Ensure robust error handling but let tracebacks surface if unexpected parsing errors occur, so you can see them and auto-fix them.