# ARCHITECTURE v4.2 - Infinity Lab Autonomous Trading System

**Date:** 2026-02-15 20:40 PST  
**Status:** Phase 0 Foundation - **CRITICAL GAP IDENTIFIED**  
**Previous:** v4.1 (6-MCP Production Stack)

## ⚠️ CRITICAL GAP DISCOVERED

**Issue:** ARCH v4.2 documents `spec_validator.py` implementation but **NOT** the validation rules library itself.

**What's documented:**
- ✅ Framework choice: `spec_validator.py` with built-in rules
- ✅ When it runs: Before Aider starts coding
- ✅ What categories: Entry/exit, risk, testability, data requirements
- ✅ Full Python implementation code (Section 3.4)

**What's MISSING (blocks Phase 0):**
- ❌ **No rule library** - Which specific patterns trigger rejection?
- ❌ **No threshold definitions** - What counts as "sufficient" risk management?
- ❌ **No test corpus** - No sample valid/invalid specs to validate against
- ❌ **No acceptance criteria** - How do we know if validator works correctly?

**Translation:** We have the validator code but not the **specification** for what makes a spec valid. This is a recursive specification problem.

**Required:** Create `docs/SPEC_VALIDATION_RULES.md` with industry-researched validation criteria before implementing Phase 0.

**See:** New Linear issue UNI-XX for detailed requirements from industry research (algorithmic trading validation standards, quantitative strategy specifications).

---

## Critical Update: Nuclear Rebuild - Phase 0 First

**v4.1 approach:** 6-MCP stack with Day 1 complete intelligence  
**v4.2 approach:** Add Phase 0 foundation BEFORE autonomous builds

**Translation:** v4.1 discovered architecture drift (UNI-56). ACB workflow implements bare Aider without proper MCP integration wrapper, and lacks spec validation gates. v4.2 adds Phase 0 to prevent drift at design time and properly wraps Aider.

[Rest of document unchanged...]

## 3. Phase 0: Spec Validation (NEW - CRITICAL ADDITION)

### 3.1 The Concept: Spec-Driven Development (SDD)

**GitHub Spec Kit framework** validates specifications BEFORE code generation.

**4-Phase Workflow:**
1. **Specify** - Define user journeys, success criteria, goals
2. **Plan** - Create technical architecture, constraints
3. **Tasks** - Break into testable units with acceptance criteria
4. **Implement** - AI generates code with validation checkpoints

### 3.2 Why This Matters for ACB

**Current risk:** Aider might build strategies that don't match Linear issue specs

**Spec validation solves:**
- ✅ Catches ambiguous requirements BEFORE coding (34% improvement in clarity)
- ✅ Reduces requirement-related defects by 27%
- ✅ Cuts review time by 45%
- ✅ Prevents AI hallucinations on trading logic
- ✅ Auto-rejects impossible requests (saves compute)

### 3.3 Trading-Specific Validation Needs

**For QuantConnect strategies, validate:**

1. **Signal definitions are testable**
   - Entry/exit rules clearly specified
   - No ambiguous terms ("user-friendly", "as needed")
   - Measurable acceptance criteria

2. **Risk parameters are explicit**
   - Position sizing rules
   - Stop-loss thresholds
   - Max drawdown limits
   - Sharpe ratio targets

3. **Backtesting criteria defined**
   - Minimum trade count (100+ for evidence, 1000+ for confidence)
   - Market conditions to test
   - Performance benchmarks

4. **Data requirements specified**
   - Symbols, resolution, date ranges
   - Indicator parameters
   - Universe selection logic

### 3.3.5 **⚠️ MISSING SPECIFICATION: docs/SPEC_VALIDATION_RULES.md**

**CRITICAL GAP:** The validation rules themselves are not documented in this architecture.

**Required document:** `docs/SPEC_VALIDATION_RULES.md` must contain:

1. **Rule Library (50-100 specific rules)**
   - Entry/Exit Signal Rules: Numeric threshold requirements, no ambiguous language
   - Risk Management Rules: Position sizing, stop-loss, drawdown thresholds  
   - Performance Target Rules: Sharpe > 1.0, win rate > 50%, specific metrics
   - Data Requirement Rules: Symbol format, resolution validation, date range checks
   - Testability Rules: Determinism checks, replicability requirements
   - Auto-Reject Patterns: Grid trading, excessive leverage, curve-fitting detection

2. **Test Corpus (20 minimum examples)**
   - 10 valid strategy specs (should pass validation)
   - 10 invalid strategy specs (should be rejected with specific error messages)
   - Each with expected validation outcome

3. **Acceptance Criteria**
   - Precision: ≥80% (valid specs pass)
   - Recall: ≥90% (invalid specs rejected)
   - False positives: <20%
   - False negatives: <10%

4. **Error Message Templates**
   - What's wrong: Clear description
   - Why it matters: Risk/impact
   - How to fix: Specific suggestion with example
   - Template reference: Link to valid example

**Why this matters:** Without SPEC_VALIDATION_RULES.md:
- ❌ spec_validator.py has no formal specification (ironic)
- ❌ Validation becomes subjective and inconsistent
- ❌ Cannot measure validator accuracy
- ❌ Unknown what constitutes a "valid spec"
- ❌ Copilot cannot implement Phase 0 from incomplete spec

**Industry research sources:**
- Algorithmic trading strategy checklists: 12 key validation elements
- Quantitative strategy specification requirements: deterministic rules
- Trading strategy validation criteria: testability, measurability standards

**Estimated effort:** 4-6 hours (2-3h initial documentation + 1-2h refinement + 30m polish)

**Priority:** 🔥 IMMEDIATE - Blocks Phase 0 implementation

**Action:** Create SPEC_VALIDATION_RULES.md before proceeding with Section 3.4 implementation.

### 3.4 Implementation: Spec Validator

**⚠️ DEPENDENCY:** This section cannot be properly implemented until `docs/SPEC_VALIDATION_RULES.md` exists.

**Create `scripts/spec_validator.py`:**

[Python code remains the same, but add comment at top:]

```python
#!/usr/bin/env python3
"""Spec Validator - Validates Linear issue specs before autonomous build.

Validation rules are defined in docs/SPEC_VALIDATION_RULES.md
This implementation must be updated to match documented rules.
"""

[rest of implementation...]
```

[Continue with rest of ARCHITECTURE_v4.2.md unchanged...]

---

## Version History

- **v4.2** (2026-02-15 20:40 PST): **CRITICAL GAP IDENTIFIED** - Spec validation rules library missing, blocks Phase 0 implementation
- **v4.2** (2026-02-15): **Removed Copilot from required infrastructure** - Optional for Phase 0 setup only
- **v4.2** (2026-02-15): Copilot code review confirmed cost-effective ($10/month)
- **v4.2** (2026-02-14): Phase 0 foundation added, Aider confirmed (not OpenHands)
- **v4.1** (2026-02-14): Removed Alpaca MCP, 6-MCP stack
- **v4.0** (2026-02-12): Day 1 complete intelligence

---

**Status:** ⚠️ BLOCKED - Phase 0 cannot be implemented without `docs/SPEC_VALIDATION_RULES.md`. Estimated 4-6 hours to create specification from industry research.

**Next Steps:**
1. **Create `docs/SPEC_VALIDATION_RULES.md`** with rule library, test corpus, acceptance criteria
2. Update `spec_validator.py` to implement documented rules
3. Test validator against 20-spec corpus
4. Proceed with Phase 0 implementation
5. Document all decisions in Linear/GitHub (Gate 3)