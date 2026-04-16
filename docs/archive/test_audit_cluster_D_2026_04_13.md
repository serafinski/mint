# Test Audit: Cluster D (Optimizer + Composition + Execution + Schemas + Registry + Evaluation Infra)

**Date:** 2026-04-13  
**Branch:** iter/6-execution-backends (post-Phase-3.5c, pre-B1)  
**Scope:** 19 test files, 6,358 LOC; 10 source files, 3,500+ LOC

## Executive Summary

This cluster contains the three largest Pydantic state schemas (OptimizerState, TaskState, EvaluationRecord) and the execution backends (SKILL/AGENT via DeepAgent, MCP_TOOL via Docker). **The most critical finding: merge/split operation tests pass against metadata-only stubs and will NOT catch the B1 source_code/skill_content/agent_config fix without augmentation.** Additionally, the merge-candidate selector is missing the second conjunct (schema Jaccard overlap), and tests do not verify it exists.

**Aggregate findings:**
- **15 concrete test issues** identified (VITAL: 3, WEAK: 7, VESTIGIAL: 2, OVER-SPECIFIED: 3)
- **3 B1-blocking gaps** in test coverage (merge/split code assertion, schema Jaccard validation, split reuse threshold)
- **2 stale references** (PILOT_TASKS definition matches but comment is outdated; test_optimizer_simulation.py orphan but valid)
- **OK coverage:** execution backends (happy path only), composition DAG ordering, schema field defaults, registry seed accuracy (post-correction), metrics store invocations_since

---

## Top 15 Concrete Findings

### 1. **test_merge_skills — passes against metadata-only stub** (VITAL → WEAK)
- **File:** `tests/unit/test_optimizer_operations.py:395–425`
- **Issue:** Asserts only `capability_type`, `domain`, `tags` on merged descriptor. Does NOT assert `source_code`, `skill_content`, or `agent_config`. Source at `src/optimizer/operations.py:304–310` creates descriptor with only name/description/capability_type/domain/tags — merged cap is unexecutable but test passes.
- **Reason:** Pydantic's field defaults (all code fields default to None) mask the stub. When B1 populates these fields in the merged result, test will still pass without catching the behavior change.
- **B1 impact:** Test will pass after B1 fix; does NOT protect the fix.
- **Recommendation:** Add assertions for `assert new_cap.source_code is not None or new_cap.skill_content is not None or new_cap.agent_config is not None` (at least one code field non-None for SKILL type).

### 2. **test_split_skill — same stub issue as merge** (VITAL → WEAK)
- **File:** `tests/unit/test_optimizer_operations.py:159–192`
- **Issue:** Tests split variants but only asserts `capability_type` and `domain`. Does not verify variants have ANY generated code. Source at `src/optimizer/operations.py:191–197` creates variants with zero code fields.
- **B1 impact:** Will pass after B1 adds code generation; test provides no regression protection.
- **Recommendation:** Assert `all(v.source_code or v.skill_content or v.agent_config for v in new_caps)` or similar (at least one field populated per variant).

### 3. **identify_candidates_node missing schema Jaccard conjunct** (VITAL)
- **File:** `tests/unit/test_optimizer_loop.py:180–209` + `src/optimizer/loop.py:199–218`
- **Issue:** Test `test_merge_candidates_use_similarity` only validates description cosine ≥ 0.5. Does NOT assert second conjunct: overlapping input/output schemas. Source code line 211 checks only `score >= 0.5`; no `_schema_jaccard()` call visible.
- **Gap:** The proposal (Chapter 5.2.3) specifies "description similarity AND schema overlap"; implementation only checks description.
- **B1 impact:** B1 will add `_schema_jaccard()` helper and gate. Current test will still pass; no regression protection for the gate.
- **Recommendation:** Add test case that verifies a pair with high description cosine but LOW schema overlap (< 0.5) is NOT formed as merge candidate. Requires mocking `_schema_jaccard()` return value.

### 4. **test_prune_skips_pinned_capability — OK, valid regression** (OK)
- **File:** `tests/unit/test_optimizer_operations.py:95–108`
- **Test:** Confirms pinned capabilities (metadata["pinned"]="true") are never deactivated. Source `operations.py:91` checks this before pruning.
- **Status:** Valid and necessary (guards against accidental loss of seeded entries).

### 5. **test_kl_divergence_* — passes for on-line metric, not simulation** (OK)
- **File:** `tests/unit/test_optimizer_loop.py:56–79`
- **Issue:** Tests `_compute_kl_divergence` (line 64–105 in loop.py), which is the on-line metric used in OptimizerState. Tests correctly handle empty distributions and identical/different distributions. Separate histogram-based KL in `simulation.py` is orphan research code.
- **Status:** On-line metric is correct and well-tested. Simulation KL is intentionally unused.

### 6. **test_merge_records_events — incomplete record count** (WEAK)
- **File:** `tests/unit/test_optimizer_operations.py:427–449`
- **Test:** Asserts `store.record_event.call_count == 3` (1 "created" + 2 "merged"). Does NOT verify the event descriptions or types are correct. Passes if any 3 calls are made.
- **Recommendation:** Assert event types: `calls = [call[0][1] for call in store.record_event.call_args_list]; assert "created" in calls; assert calls.count("merged") == 2`.

### 7. **test_split_repair_pass_on_first_failure — happy path only** (OK)
- **File:** `tests/unit/test_optimizer_operations.py:248–273`
- **Test:** Mocks LLM to fail once then succeed. Asserts retry happens (call_count==2). Valid protection for repair logic.
- **Status:** Good coverage of retry-on-LLM-error path.

### 8. **test_composition.py TestChainExecution — MCP_TOOL path not isolated** (WEAK)
- **File:** `tests/unit/test_composition.py:335+` (1037 LOC total; execution begins line 335+)
- **Issue:** Chain execution tests use mocked backends. Do NOT separately verify MCP_TOOL workspace mount vs SKILL/AGENT fixture_contents injection. Tests mock `execute_mcp_tool_in_chain` and `execute_skill_artifact` without asserting the fixture path handling differences.
- **B1 impact:** Phase 3.5c changed fixture path handling (step 0 broad, steps 1+ name-aware). Tests don't verify this distinction per type.
- **Recommendation:** Add separate test case that asserts MCP_TOOL step receives `/tmp/mint_workspace_*/fixtures/` mount and SKILL step receives only `fixture_paths[...]` names in `fixture_contents` dict.

### 9. **test_execute_skill_artifact / test_execute_agent_artifact — happy path only** (WEAK)
- **File:** `tests/unit/test_execution.py:95–156`
- **Issue:** Tests only the success case (mocked inner function returns dict). Do NOT exercise the max-tool-call loop (SKILL backend has hard limit; AGENT has tool-use loop). Do NOT test tool-call exhaustion or timeout behavior.
- **Recommendation:** Add test that mocks inner function to exhaust max-tool-calls and asserts truncation/failure handling.

### 10. **test_schema.py GeneratedArtifact — new fields lack exercise** (WEAK)
- **File:** `tests/unit/test_schemas.py:262–300`
- **Issue:** Tests construct `GeneratedArtifact` with `source_code`, `skill_content`, `agent_config` (lines 263–291) but do NOT test construction with BOTH `pip_packages` (Phase 1) and `input_schema`/`output_schema` (today, 2026-04-13) together. Field defaults absorb missing fields, so Pydantic validation passes even if the new fields are never exercised in realistic scenarios.
- **Status:** Tests are technically OK (they verify the fields exist and round-trip), but coverage is thin for Phase 1 + Phase 3 fields in combination.
- **Recommendation:** Add test `test_generated_artifact_with_all_new_fields()` that constructs with `pip_packages=["numpy"], input_schema={"type":"object"}, output_schema={"type":"array"}` and verifies all are present after round-trip.

### 11. **test_registry.py _build_document — over-specified prose format** (OVER-SPECIFIED)
- **File:** `tests/unit/test_registry.py:42–69` (search relevance tests)
- **Issue:** Test `test_search_relevance` asserts `results[0][0].name == "csv-parser"` based on seeded registry. Seed descriptions were hand-corrected 2026-04-13 (5/8 had drift). Tests do NOT verify the prose format; they rely on hardcoded seed state. If enrichment is tried again in future (e.g., Phase B2), the exact prose format may change without breaking these tests.
- **Status:** Tests are brittle to enrichment attempts.
- **Recommendation:** Lower specificity: test `results[0][1] > 0.7` (score above threshold) rather than `results[0][0].name == ...` (exact name hardcoded to seed state).

### 12. **test_metrics.py invocations_since — present and correct** (OK)
- **File:** `tests/unit/test_metrics.py` (no explicit invocations_since test found)
- **Issue:** `MetricsStore.invocations_since()` was added Iter 4 and is used in `prune()` operation. Tests of `prune()` at `test_optimizer_operations.py:79–93` indirectly exercise it via mock (call_count checks). No direct unit test exists, but integration coverage via prune tests is sufficient.
- **Recommendation:** Optional: add explicit test `test_invocations_since_counts_in_window()` to document the behavior.

### 13. **test_harness.py EvaluationHarness.run_pilot — PILOT_TASKS assumed fixed** (WEAK)
- **File:** `tests/unit/test_harness.py:295+`
- **Issue:** Test fixture and test setup assume `PILOT_TASKS = [T01, T07, T05]` is stable. Hardcoded in test expectations (e.g., loop count). If PILOT_TASKS is extended (e.g., to include T04), test count assertion will break — but the test does NOT derive PILOT_TASKS from the module constant; it hardcodes expectations.
- **Status:** Not a major issue (PILOT_TASKS is stable), but brittle.
- **Recommendation:** Change assertions to `len(results) == len(PILOT_TASKS)` and dynamically check IDs against module constant.

### 14. **test_harness_static.py T05 coverage — SKILL type correct** (OK)
- **File:** `tests/unit/test_harness_static.py:86–102`
- **Issue:** T05 is now `data-cleaning-workflow` SKILL (Phase 3 redesign). Static implementation is correct: `_run_static_implementation("T05", [...])` calls the SKILL handler. Test passes; no regression.
- **Status:** Valid coverage for updated task.

### 15. **test_cli_runner_gen_stage.py — validates display diff logic** (OK)
- **File:** `tests/unit/test_cli_runner_gen_stage.py`
- **Issue:** Tests `_compute_gen_stage_diff` function, which is a display/progress helper. Regression for T11 CLI "-1 artifact(s)" bug (2026-04-08). Tests correctly cover backward/retry cases.
- **Status:** Valid and necessary regression protection.

---

## Per-File Brief (19 Files)

### Test Files

1. **test_optimizer_operations.py (549 LOC):** Prune, split, merge operation tests. Prune is well-tested (pinned, invocation-count logic correct). Split/merge stubs lack code-field assertions (VITAL). LLM retry logic solid.

2. **test_optimizer_loop.py (359 LOC):** Graph compilation, KL divergence, merge-candidate formation. KL metric correct. Merge candidates check description similarity only, missing schema overlap conjunct (VITAL B1 gap). Pruned-capability skip guards valid.

3. **test_optimizer_utility.py (118 LOC):** Utility score computation. Tests default and custom weights. Correct coverage.

4. **test_optimizer_simulation.py (316 LOC):** Orphan research code (no live callers outside tests). Histogram KL, synthetic registry generation. Tests are valid documentation but not production-critical.

5. **test_composition.py (1037 LOC):** DAG ordering, workspace management, resolution. Large file, well-structured. MCP_TOOL vs SKILL/AGENT fixture handling not isolated per type (WEAK). Fixture contents tests use generic paths.

6. **test_execution.py (563 LOC):** SKILL/AGENT execution wrappers. Happy path only; max-tool-call loops not exercised (WEAK). JSON extraction regression (pretty-printed output bug) is well-tested.

7. **test_schemas.py (1010 LOC):** Pydantic schema validation. Field defaults tested but new fields (pip_packages, input_schema, output_schema) not exercised together (WEAK). CapabilityDescriptor roundtrip correct.

8. **test_registry.py (554 LOC):** Search, gap detection, CRUD operations. Seed description accuracy post-correction. Search relevance tests are over-specified to seed state (OVER-SPECIFIED but OK for now).

9. **test_metrics.py (215 LOC):** Reuse rate, success rate, maintenance burden, granularity metrics. Invocations_since integrated via prune tests, no explicit unit test.

10. **test_tool_loader.py (572 LOC):** Not audited (separate MCP tool system, out of Cluster D scope per assignment).

11. **test_harness.py (419 LOC):** EvaluationHarness.run_pilot, _extract_result, _run_static. Verification tier logic correct. PILOT_TASKS hardcoded assumptions (WEAK but stable).

12. **test_harness_static.py (128 LOC):** Smoke tests for hand-written static implementations (T01, T02, T03, T05, T09). All pass; T05 SKILL coverage correct.

13. **test_cli_runner_gen_stage.py (171 LOC):** T11 CLI "-1 artifact(s)" retry bug regression. Well-designed test cases for baseline refresh logic.

14. **test_fibonacci.py (25 LOC):** Trivial MCP tool test. Sanity check only.

15. **test_evaluation_schema.py (143 LOC):** Not detailed audit (schema field definitions).

16. **test_evaluation_snapshots.py (127 LOC):** Not detailed audit (snapshot comparison).

17. **test_evaluation_store.py (184 LOC):** Not detailed audit (evaluation record storage).

18. **test_evaluation_tasks.py (547 LOC):** Task registry and validators. ALL_TASKS = 20 tasks, PILOT_TASKS = [T01, T07, T05]. Validators well-tested per task. Static coverage count correct (5: T01, T02, T03, T05, T09).

### Source Files

1. **src/optimizer/operations.py:** Prune (correct), split (metadata-only stubs), merge (metadata-only stubs, missing code fields).

2. **src/optimizer/loop.py:** Graph compilation correct. Merge candidate selector missing schema Jaccard check.

3. **src/optimizer/utility.py:** Utility computation correct. Weights correctly forwarded.

4. **src/optimizer/simulation.py:** Orphan research code (intentional, documented in loop.py comments).

5. **src/orchestrator/composition.py:** DAG-ordered executor correct. Fixture path handling per type not explicitly tested.

6. **src/schemas/task.py:** Pydantic schemas well-defined. New fields (input_schema, output_schema) defaults correct.

7. **src/schemas/capability.py:** CapabilityDescriptor with all fields. Merge/split produce stubs missing code fields.

8. **src/registry/store.py:** Hierarchical embedding reverted today; prose-only format correct. Seed descriptions corrected (5/8).

9. **src/registry/metrics.py:** invocations_since() added Iter 4, used in prune. Correct implementation.

10. **src/registry/evaluation_store.py:** Not detailed audit.

---

## Aggregate Counts by Classification

| Classification | Count | Issues |
|---|---|---|
| **VITAL** | 3 | merge/split code stubs (2), schema Jaccard missing (1) |
| **OK** | 5 | prune pinned, KL metric, split retry, harness_static, cli_runner |
| **WEAK** | 7 | merge events, composition fixture handling, execution max-calls, schemas new fields, harness PILOT_TASKS, registry search over-specified, composition mcp/skill isolation |
| **VESTIGIAL** | 0 | (none; simulation.py orphan but valid) |
| **OVER-SPECIFIED** | 2 | registry search (hardcoded seed names), harness assumptions |
| **DUPLICATE** | 0 | (none) |

---

## B1-Specific Findings

**B1 will land:**
- **merge/split source_code/skill_content/agent_config population** (Ch 7 Phase B1 Gap 2)
- **split_reuse_threshold=1.01 disablement** (Ch 7 deferral)
- **_schema_jaccard() helper + [0.5, 1.0) gate for merge candidates** (Ch 7 B1, targeting proposal Chapter 5.2.3)

**Tests that will pass after B1 but provide NO regression protection:**
1. `test_merge_skills` — will still pass with code fields populated (does not assert their values)
2. `test_split_skill` — will still pass with code-populated variants (does not assert code is non-None)
3. `test_merge_candidates_use_similarity` — will still pass after schema Jaccard gate added (tests only description cosine)

**Tests that will fail or need augmentation after B1:**
- None expected to fail; augmentation needed (see recommendations below)

---

## Recommendations

### Augmentation (Priority: HIGH)

1. **Merge/split stub fix (Finding #1–2):**
   - Add assertion in `test_merge_skills`: `assert new_cap.source_code or new_cap.skill_content or new_cap.agent_config`
   - Add assertion in `test_split_skill`: `for v in new_caps: assert v.source_code or v.skill_content or v.agent_config`
   - Pairs with B1 fix; will catch regression if code fields are removed.

2. **Schema Jaccard validation (Finding #3):**
   - Add new test case `test_merge_candidates_reject_low_schema_overlap()` that mocks `registry.search()` to return a capability with high description cosine (0.8) but specifies via helper mock that schema Jaccard is < 0.5.
   - Assert no merge pair is formed.
   - Prerequisite: B1 must implement `_schema_jaccard()` helper; test can mock it.

3. **Composition fixture handling (Finding #8):**
   - Add test `test_composition_mcp_vs_skill_fixture_paths()` that verifies MCP_TOOL steps receive `/fixtures/` mount and SKILL steps receive name-filtered dict.
   - Requires patching both execution backends and introspecting fixture_contents parameter.

4. **Execution max-tool-calls (Finding #9):**
   - Add test `test_execute_skill_exhausts_tool_calls()` that mocks `_execute_skill_inner` to trigger max-call loop condition and asserts graceful exit.
   - Verify SKILL_EXECUTION_MAX_TOOL_CALLS constant (currently 3; may be 5 per Phase 3.5b note).

5. **Schema field coverage (Finding #10):**
   - Add test `test_generated_artifact_all_new_fields()` constructing GeneratedArtifact with pip_packages, input_schema, output_schema and verifying round-trip.

### Cleanup (Priority: LOW)

6. **Reduce registry search over-specification (Finding #11):**
   - Change `test_search_relevance` assertion from `results[0][0].name == "csv-parser"` to `results[0][1] > 0.7` (score-based, not name-based).
   - Insulates from future enrichment attempts.

7. **Add direct invocations_since test (Finding #12):**
   - Optional: `test_invocations_since_in_window()` documents behavior explicitly.

8. **Dynamicize PILOT_TASKS assumptions (Finding #13):**
   - Change `test_pilot_records_written` to derive expected count from `len(PILOT_TASKS)` rather than hardcode 3.

### Wait-for-B1

9. **Schema Jaccard implementation:**
   - B1 must add `_schema_jaccard()` helper to `src/optimizer/loop.py` before Recommendation #2 can be implemented.
   - Current test (`test_merge_candidates_use_similarity`) will continue to pass; augmentation required post-B1.

---

## Critical Gaps Summary

| Gap | Severity | B1-blocking | Recommendation |
|---|---|---|---|
| Merge/split stubs lack code assertions | VITAL | No | Augment tests with code-field checks |
| Schema Jaccard missing from merge candidate selector | VITAL | Yes | Implement helper, add test gate |
| Composition fixture handling not isolated per type | WEAK | No | Add fixture-path verification test |
| Execution max-tool-calls loop not exercised | WEAK | No | Mock loop-exhaustion scenario |
| Schema new fields not combined in single test | WEAK | No | Add integration test |

---

## Checklist for B1 Integration

- [ ] Implement `_schema_jaccard()` in `src/optimizer/loop.py`
- [ ] Add gate check at line 211 merge-candidate loop: `score >= 0.5 AND _schema_jaccard(...) >= 0.5`
- [ ] Populate source_code/skill_content/agent_config in merge/split operations
- [ ] Set split_reuse_threshold=1.01 to disable split (Chapter 7 deferral)
- [ ] Run augmented tests after B1 lands: verify merge/split stubs assert code fields, schema Jaccard gate test passes
- [ ] Verify SKILL_EXECUTION_MAX_TOOL_CALLS constant (3 or 5?) and update execution tests

---

**Audit Complete: 2026-04-13**
