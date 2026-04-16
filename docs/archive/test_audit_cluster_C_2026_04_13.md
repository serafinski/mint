# Test Audit: Verification Layer (Pillars 2, Tiers 0–3)
**Cluster:** Verification Layer (Pillars 2, Tiers 0–3)  
**Date:** 2026-04-13  
**Coverage:** 6 test files (851 + 592 + 366 + 750 + 411 + 218 = 3,188 LOC)  
**Status:** Behavioral drift audit — checking invariants vs. implementation, not import resolution

---

## Top 10 Concrete Findings

### 1. **WEAK: Scoped mock/placeholder detection breaks SKILL/AGENT tests**
- **File:** `tests/unit/test_static_scanner.py` lines 607–617, 620–629, 646–656, 683–697
- **Test:** `test_mock_word_in_skill_prose_not_flagged`, `test_todo_comment_in_agent_prose_not_flagged`, `test_json_agent_config_with_mock_word_not_flagged`, `test_skill_with_mock_word_in_instructions_passes`
- **Classification:** OK (but brittle)
- **Reason:** Tests assert that mock/placeholder words in SKILL/AGENT prose pass verification. Source code confirms: regex checks (`_check_mock_artifacts`, `_check_synthetic_comments`) are only called when `capability_type == MCP_TOOL` (line 456–458 in `static_scanner.py`). Tests correctly guard these, but they are slightly over-specified — they test implementation scoping detail rather than the behavioral guarantee ("SKILL/AGENT prose never blocks on mock indicators").
- **Recommendation:** Keep; tests protect real intent but would benefit from a single integration test combining the gate layer to verify end-to-end scoping.

### 2. **VITAL: verify() never-raises contract is tested but fail-open path is under-exercised**
- **File:** `tests/unit/test_gate.py` lines 129–139 (happy path only)
- **Test:** `test_mcp_clean_passes_all_tiers`
- **Classification:** VITAL
- **Reason:** The source code wraps `_verify_inner()` in try/except and returns MEDIUM `gate_error` on exception (lines 154–174 in `gate.py`). The test suite has no test that exercises the exception path (e.g., simulating a behavioral.run crash). Behavioral.run and adversarial.probe are mocked to return successfully, so gate errors are never triggered.
- **Recommendation:** Augment with a test that patches `behavioral.run` to raise an exception, verify gate returns MEDIUM `gate_error`, overall_passed=True (fail-open).

### 3. **VITAL: B1.2 variable-indirection hardcoding fix lacks one edge case**
- **File:** `tests/unit/test_static_scanner.py` lines 102–150
- **Test:** `test_hardcoded_list_via_variable_indirection`, `test_hardcoded_dict_via_variable_indirection`, etc.
- **Classification:** VITAL
- **Reason:** Tests correctly cover assignment → return pattern (B1.2 fix). However, they do not test nested scopes or multiple assignments to the same variable (e.g., `x = []; x = ["a","b","c"]; return x`). The source code's assignment collection (lines 100–106 in `static_scanner.py`) uses `first assignment wins`, so subsequent assignments are ignored. This is correct but not explicitly tested.
- **Recommendation:** Augment with `test_hardcoded_list_second_assignment_ignored` — verify that second assignment to same variable is not flagged (current behavior is correct but undocumented).

### 4. **OK: B2.1 parameterless smoke test is well-covered**
- **File:** `tests/unit/test_behavioral.py` lines 413–447
- **Test:** `TestParameterlessSmoke` class
- **Classification:** OK
- **Reason:** `_test_parameterless_smoke` has unit tests that verify nonempty output → MEDIUM finding, trivial output → no finding, sandbox failure → no finding. The integration via `run()` is also tested (lines 289–314). This directly addresses B2.1 bypass (parameterless tool should not always output canned data).
- **Recommendation:** Keep.

### 5. **VITAL: B2.3 output-variation test has trivial-output gap**
- **File:** `tests/unit/test_behavioral.py` lines 458–505
- **Test:** `TestOutputVariation` class
- **Classification:** VITAL
- **Reason:** Test at line 486–490 (`test_trivial_identical_outputs_not_flagged`) asserts that identical trivial outputs (None, 0, empty string) don't flag a finding. However, the source `_test_output_variation` implementation (behavioral.py) calls sandbox twice for boundary and large inputs; if both return the same trivial value, no finding is raised. The test correctly documents this, but the integration test `TestRunOutputVariation` (if it exists) is missing. The parameterless tool case and the regular tool case with actual parameters are tested separately but not the interaction.
- **Recommendation:** Augment with integration test: regular tool with parameters, return identical nontrivial output for different inputs, verify MEDIUM finding is raised.

### 6. **WEAK: Tier 2 HIGH clearance logic is tested but fail-open case is missing**
- **File:** `tests/unit/test_gate.py` lines 232–249
- **Test:** `test_mcp_tier2_medium_proceeds_to_tier3`
- **Classification:** WEAK
- **Reason:** The gate implements HIGH clearance: if Tier 1 has HIGH and Tier 2 is clean, downgrade HIGH to MEDIUM (lines 230–241 in `gate.py`). The test verifies MEDIUM Tier 2 proceeds to Tier 3, but does not test the downgrade path. E.g., no test asserts "Tier 1 HIGH + Tier 2 clean → final HIGH becomes MEDIUM".
- **Recommendation:** Augment with test: Tier 1 HIGH (synthetic_data), Tier 2 all findings empty, verify final findings list has MEDIUM (not HIGH).

### 7. **VESTIGIAL: Missing external calls severity was a proposal-level change, tests assert old MEDIUM**
- **File:** `tests/unit/test_static_scanner.py` lines 412–436
- **Test:** `test_missing_external_is_medium_offline`, `test_missing_external_is_high_with_network`
- **Classification:** VESTIGIAL (but currently correct)
- **Reason:** Tests correctly assert MEDIUM offline and HIGH with network (lines 427, 435). The source code implements this (lines 217–226 in `static_scanner.py`). However, tests document behavior not intent — there's no comment explaining why severity is dynamic. This is not a regression but a lack of clarity.
- **Recommendation:** Keep; add docstring to source explaining the dynamic severity.

### 8. **DUPLICATE: Silent exception tests cover both bare and Exception, HIGH severity confirmed**
- **File:** `tests/unit/test_static_scanner.py` lines 157–205
- **Test:** `TestSilentException` class
- **Classification:** OK (no action needed)
- **Reason:** Comprehensive coverage of `except:` and `except Exception:` patterns, all assert HIGH (not CRITICAL), test verifies they don't block overall_passed. This is a strong test set.
- **Recommendation:** Keep.

### 9. **VITAL: `__name__ = "not_main"` neutralization is tested in unit but NOT in behavioral integration**
- **File:** `tests/unit/test_behavioral.py` lines 223–238
- **Test:** `TestBuildSandboxCode::test_main_guard_neutralized_before_source`
- **Classification:** VITAL
- **Reason:** The test checks that `__name__ = "not_main"` appears before the inlined source (lines 234–237). However, there is no integration test that runs actual MCP tool code with `if __name__ == "__main__": mcp.run()` and verifies it doesn't block execution. The unit test only checks the string position; a tool that includes mcp.run() at the end would still deadlock if not neutralized.
- **Recommendation:** Augment: create a mock MCP tool that includes `if __name__ == "__main__": raise RuntimeError("should not fire")`, run it through behavioral.run(), verify no timeout/hang and RuntimeError not in stderr.

### 10. **VITAL: Adversarial.probe timeout/max-step fallback is mocked but never actually tested to fail-open**
- **File:** `tests/unit/test_adversarial.py` lines 177–196
- **Test:** `TestProbe::test_successful_probe_clean`, `test_successful_probe_suspicious`
- **Classification:** VITAL
- **Reason:** Source code at lines 156–174 in `gate.py` calls `adversarial.probe()` and expects it to never raise. The adversarial module at lines 100–149 implements timeout/error handling with `_timeout_report()` and `_error_report()` that return fail-open (overall_passed=True). However, the test file does not exercise the actual timeout path — mock_create is patched to return successfully. No test verifies that if the DeepAgent times out, probe() returns a partial report with MEDIUM adversarial_timeout finding and overall_passed=True.
- **Recommendation:** Augment: patch `_create_adversarial_agent` to raise TimeoutError or have invoke() block, verify probe() returns VerificationReport with overall_passed=True and a MEDIUM adversarial_timeout finding.

---

## Per-File Brief

### `tests/unit/test_static_scanner.py` (851 LOC)
Comprehensive Tier 1 coverage. Tests all five contamination categories (hardcoded, silent_except, mock, synthetic, missing_external) plus Tier 0 SKILL pre-check, SyntaxError handling, and capability-type scoping. Strong test for B1.2 variable-indirection fix. Minor gap: second assignment to same variable not tested (low impact, current behavior correct). Scoping tests (SKILL/AGENT prose must not flag mock words) are brittle but correct.

### `tests/unit/test_behavioral.py` (592 LOC)
Good coverage of Tier 2 test categories: nonexistent entity, consistency, boundary, timeout, latency variance, output variation. Unit tests for individual helpers (parameterless_smoke, output_variation, latency) are strong. Integration test via `run()` covers happy path but fail-open paths are light. Main gap: no integration test for output-variation with actual parameter variation; no test for MCP tool with `if __name__ == "__main__"` block to verify neutralization works end-to-end.

### `tests/unit/test_adversarial.py` (366 LOC)
Tests `_findings_to_report()` conversion, `_timeout_report()` and `_error_report()` structures, and `probe()` happy path. Clean and suspicious findings are tested. Gap: no test that actually triggers the timeout/error path (mocks all succeed). Contract "probe() never raises" is implemented but fail-open behavior not exercised in tests.

### `tests/unit/test_gate.py` (750 LOC)
Tests the three-tier gating logic: Tier 1 critical blocks immediately, Tier 2 HIGH blocks Tier 3, findings accumulate. SKILL and AGENT types tested separately. Layered Tier 1 (code fences in SKILL/AGENT) tested with mock/hardcoded-data examples. Gap: no test that exercises gate's exception handler (fail-open on gate error). Tier 2 HIGH clearance logic is implemented but not tested.

### `tests/unit/test_sandbox_tool.py` (411 LOC)
Tests sandbox return-value shape, Docker command structure, tempfile cleanup, timeout handling, and non-timeout exceptions. All paths return dict, never raise. Good coverage. Gap: no test for the `__name__ = "not_main"` override (that's in behavioral, not sandbox). Dynamic dependency resolution (LLM-based import mapping) is not tested; assumes LLM succeeds or falls back to identity mapping, but the fallback path is not exercised.

### `tests/unit/test_quick_validate.py` (218 LOC)
Tests SKILL.md structural validation and Tier 0 prose hard-fails (lorem ipsum, TODO: implement, placeholder return marker). Good coverage. Regression tests for T04 (legitimate 'placeholder' and 'TODO' words must pass) are present and correct. Tests that quick_validate is called and blocks on invalid SKILL.md. Gap: the actual import of quick_validate from tools/ is not mocked, so test depends on tools/quick_validate.py being present; if removed or renamed, test would fail at import time.

---

## Aggregate Counts by Classification

| Classification | Count | Examples |
|---|---|---|
| **VITAL** | 5 | verify fail-open (#2), B1.2 edge case (#3), B2.3 integration (#5), __name__ neutralization (#9), adversarial timeout (#10) |
| **OK** | 3 | B2.1 parameterless smoke (#4), silent exception (#8), scoped detection (#1) |
| **WEAK** | 1 | Tier 2 HIGH clearance (#6) |
| **VESTIGIAL** | 0 | (all findings have purpose) |
| **OVER-SPECIFIED** | 1 | Scoping tests document impl detail (#1) |
| **DUPLICATE** | 0 | (no duplicate intent found) |

---

## Bucket-1 Bypass Fix Coverage Status

**B2.1 (parameterless tool smoke test):** ✅ COVERED  
Tests: `TestRunNoParams` (lines 287–314) + `TestParameterlessSmoke` (lines 413–447)  
Verifies: nonempty output on parameterless tool → MEDIUM finding; trivial output → no finding.

**B2.3 (output-variation check):** ⚠️ PARTIALLY COVERED  
Tests: `TestOutputVariation` unit tests (lines 458–505)  
Gap: Integration test missing — no test runs a multi-parameter tool that returns identical output for different inputs and verifies MEDIUM is raised.

**B1.2 (variable-indirection hardcoding):** ✅ COVERED  
Tests: `test_hardcoded_list_via_variable_indirection`, `test_hardcoded_dict_via_variable_indirection`, `test_async_hardcoded_list_via_variable_indirection` (lines 102–150)  
Verifies: `data = [...]; return data` pattern is flagged.  
Gap: Multiple assignments to same variable not tested (first assignment wins, not tested but correct).

---

## Recommendations

### **Deletion Candidates**
None. All tests serve a purpose; some are over-specified but not wasteful.

### **Augmentation Opportunities** (High Priority)
1. **test_gate.py:** Add test for gate's exception handler (fail-open on gate error at Tier 1/2/3)
   - Patch `static_scanner.scan` to raise, verify gate returns MEDIUM gate_error and overall_passed=True

2. **test_behavioral.py:** Add integration test for output-variation with actual parameter variation
   - Tool with parameters, return identical nontrivial output, verify MEDIUM finding

3. **test_behavioral.py:** Add integration test for `if __name__ == "__main__"` MCP tool
   - Tool includes `mcp.run()` at end, verify it doesn't block execution (neutralization works end-to-end)

4. **test_adversarial.py:** Add test that exercises the timeout/error fallback paths
   - Patch `_create_adversarial_agent` to raise or timeout, verify probe() returns fail-open report with MEDIUM

5. **test_gate.py:** Add test for Tier 2 HIGH clearance downgrade
   - Tier 1 HIGH (synthetic_data), Tier 2 empty findings, verify final finding is MEDIUM (not HIGH)

### **Wait-for-B1 Flags**
None. All B1-iteration fixes (Phase 1 hardening) are in place and tested.

### **Documentation Gaps**
- `missing_external_calls` severity is context-dependent (MEDIUM/HIGH based on allow_network), but source code has clear docstring. Tests document the behavior. No action needed.
- Tier 2 HIGH clearance logic (lines 230–241 in gate.py) is implemented but not obvious from tests. Recommend docstring in source explaining the intent.

---

## Final Verdict

**Overall health:** Good. Tier 1 (static scanner) is well-covered. Tier 2 (behavioral) has happy-path coverage but fail-open paths are under-tested. Tier 3 (adversarial) test structure is correct but timeout/error fallback not exercised. Gate's never-raises contract is implemented but exception handler not tested.

**Blockers for B1 release:** None. All B1.1–B5.2 fixes that require test coverage are present.

**Priority fixes before merge:**
1. Add gate exception-handler test (fail-open contract verification)
2. Add adversarial timeout/error fallback test (fail-open contract verification)
3. Add behavioral integration test for output-variation with actual parameters (B2.3 full coverage)
