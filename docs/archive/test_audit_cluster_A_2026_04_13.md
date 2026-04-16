# Behavioral Test Audit — Cluster A

**Cluster:** Classifier + Pipeline + Decomposition/Gap/Verification nodes
**Date:** 2026-04-13
**Files covered:** 8 test files, ~2,630 LOC total
- `tests/unit/test_classifier.py` (765)
- `tests/unit/test_decomposition.py` (343)
- `tests/unit/test_gap_detection.py` (296)
- `tests/unit/test_pipeline.py` (254)
- `tests/unit/test_verification_node.py` (392)
- `tests/unit/test_loading_node.py` (342)
- `tests/unit/test_build_task_context.py` (170)
- `tests/unit/test_slugify_gap.py` (68)

---

## Top 10 Concrete Findings

### 1. `test_classifier.py:230–339` — `TestClassifyTask` — **VESTIGIAL**
`classify_task()` tested as a standalone LLM-invoking function with full mocking. This function now exists only as a legacy wrapper (per-gap `classify_gap` is primary). No integration test calls `classify_task()` directly at unit level. **Recommendation:** Keep, but add a file-level comment marking this block as "legacy API coverage" — they don't verify the main code path.

### 2. `test_classifier.py:399–766` — `TestClassifierNode` — **VITAL**
Per-gap classification iteration, error fallback detection, retry skip logic via description matching. Line 400–432 (`test_classifies_each_gap`), line 658–697 (`test_preserves_existing_classified_gaps`), and line 570–619 (fallback error recording) all protect the Phase 2 primary path. **Recommendation:** Keep all.

### 3. `test_decomposition.py:100–343` — Circuit breaker + structured response handling — **VITAL**
Lines 281–343 test the two-stage circuit breaker (WARN at 5 caps, TRUNCATE at 8). Lines 100–158 test structured-response fallback when the agent returns None. Operational boundaries, downstream-affecting failure modes. **Recommendation:** Keep all.

### 4. `test_gap_detection.py:234–296` — MetricsStore regression tests — **VITAL**
Explicit `metrics_store.record_invocation.assert_not_called()` assertions. Regression tests for the Phase 1 removal of fake invocations (which previously inflated reuse counts). **Recommendation:** Keep all — hard-won fix.

### 5. `test_verification_node.py:124–158` — Retry path with artifact replacement — **VITAL**
`test_some_fail_below_max_routes_to_generator`, `test_passed_artifacts_kept_on_retry`, `test_failed_artifacts_removed_on_retry` verify that failed artifacts are removed and only passing ones survive retry. Core compositional retry loop. **Recommendation:** Keep.

### 6. `test_verification_node.py:206–218` — `attempt_count` incrementation — **OK**
Simple state-threading property test. Valid but low-impact. **Recommendation:** Keep.

### 7. `test_loading_node.py:111–139` — Skills/agents are NOT loaded — **WEAK**
`test_skill_artifacts_are_skipped` / `test_agent_artifacts_are_skipped` assert `loader.load_tool.assert_not_called()`, BUT the test state has **no gaps**. When `state.gaps == []`, status is COMPLETED regardless of artifact types. The test doesn't actually verify the skip-loading logic — it just verifies no-gap → COMPLETED. **Recommendation:** Augment — add tests with gaps + skill/agent artifacts.

### 8. `test_loading_node.py:313–328` — Mixed-artifact COMPLETED when skill registers despite MCP load failure — **OK**
Correctly reflects source line 144 (`deployed = registered`). Valid edge case. **Recommendation:** Keep.

### 9. `test_build_task_context.py:63–89` — First attempt unchanged, retry with feedback — **VITAL**
`attempt_count==0` → unchanged description; `attempt_count>0` → verification feedback appended. Called by all 3 generators on retry. **Recommendation:** Keep.

### 10. `test_pipeline.py:85–119` — No-gap path smoke test — **OK**
Patches both `_get_agent` AND `detect_gaps`. Tests graph topology only, not node behavior. Somewhat fragile due to double-patching. **Recommendation:** Keep; add a comment noting the scope.

---

## Per-File Briefs

**test_classifier.py (765):** Comprehensive coverage of `classify_task` (legacy, 230–391) and `classifier_node` per-gap iteration (399–766). Per-gap tests are VITAL — Phase 2 primary path. Legacy wrapper tests are VESTIGIAL but harmless. Normalization/slug tests (86–211) VITAL for deterministic naming.

**test_decomposition.py (343):** Tests DeepAgent wrapper's success/timeout/failure paths (100–248) + two-stage circuit breaker (273–343). Mocking is at ThreadPoolExecutor level — correct layer. All VITAL.

**test_gap_detection.py (296):** Registry match/gap detection (45–139), registry call signature (163–172), and crucially the Phase 1 MetricsStore regression (234–296). Clean mocking. No behavioral drift.

**test_pipeline.py (254):** Graph compilation (51–72) + node-presence checks + no-gap smoke (85–119) + `test_classifier_produces_mixed_types` (155–223, end-to-end per-gap classification, **VITAL**). Smoke test is OK but brittle.

**test_verification_node.py (392):** Stub gate + routing + retry with artifact replacement + max-attempts fail-open + attempt_count threading + error accumulation + result preservation + parallel verification. All VITAL. No drift.

**test_loading_node.py (342):** MCP load/register + skill/agent skipping + failure resilience + status logic. Status tests VITAL. Skip-loading tests WEAK (don't test with gaps). Register-failure resilience VITAL.

**test_build_task_context.py (170):** First-attempt unchanged / retry-with-feedback / severity filtering. All VITAL for generator retry path.

**test_slugify_gap.py (68):** Determinism, collision avoidance, truncation, double-underscore regression (38–53). All VITAL for deterministic artifact naming.

---

## Aggregate Counts

| Classification | Count |
|---|---|
| VITAL | ~35 tests |
| OK | ~12 tests |
| WEAK | 3 tests |
| VESTIGIAL | 1 test class (~20 tests) |
| OVER-SPECIFIED | 0 |
| DUPLICATE | 0 |

**Total: ~71 test functions across 8 files.**

---

## Recommendations

1. **Delete nothing.** All tests run and cover meaningful properties. The VESTIGIAL `TestClassifyTask` tests a still-preserved API.
2. **Augment `test_loading_node.py:111–139`:** construct state with `gaps=[GapItem(...)]` AND skill/agent artifacts, verify non-MCP artifacts don't enter the load/register loop.
3. **Document `test_pipeline.py:85–119`:** note that this smoke test is graph-topology only, fragile due to double-patching.
4. **No wait-for-B1 flags** in this cluster. Phase 2/3 transitions are correctly captured.
5. **Minor hygiene:** label the legacy `TestClassifyTask` block with a comment so future readers know it's wrapper coverage, not primary path.

---

## Summary

**Cluster A is in very good shape.** The per-gap classification (Phase 2) is thoroughly tested. The compositional retry loop (Phase 3) is well-protected. The MetricsStore regression (Phase 1) is explicitly guarded. Only minor augmentation needed in `test_loading_node.py` skill/agent skip logic.
