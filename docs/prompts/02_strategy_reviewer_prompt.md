# Task: Implement `strategy_reviewer.py` (Phase 0, Step 2)

Please implement the AI-powered trading logic critique gate.

## Requirements:
1. Create `scripts/strategy_reviewer.py`.
2. The script must take a spec YAML file as input and output a strict JSON response with a verdict (`WARN` or `PASS`), `risk_level`, and `concerns`.
3. Implement caching: hash the spec YAML (SHA256). If a cache exists for this hash (valid for 7 days), reuse it to preserve API free-tier limits.
4. Implement the 4-tier model fallback chain for resilience:
   - Tier 1: Gemini 2.0 Flash
   - Tier 2: Gemini 1.5 Pro
   - Tier 3: gpt-4o-mini
   - Tier 4: Claude Opus
5. If the AI response is invalid JSON, implement a retry mechanism to repair it before failing over to the next tier.
6. Create `tests/test_strategy_reviewer.py` with mocked API calls to test the fallback and caching logic.

## Constraints:
- NO PLACEHOLDERS. Provide the complete API calling logic, fallback handling, and JSON validation.
- Do not swallow exceptions in the top-level execution; if all tiers fail, exit cleanly with a fallback WARN code (e.g., `SRV-W050`) so the pipeline can proceed to the ACK gate.