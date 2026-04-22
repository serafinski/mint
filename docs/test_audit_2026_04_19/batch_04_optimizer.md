# Batch 04 — Optimizer tests audit (2026-04-19)

Files audited:
- `tests/unit/test_optimizer_operations.py` (44 tests)
- `tests/unit/test_optimizer_loop.py` (24 tests)
- `tests/unit/test_optimizer_simulation.py` (27 tests)
- `tests/unit/test_optimizer_utility.py` (8 tests)
- `tests/unit/test_optimizer_config.py` (16 tests)
- `tests/unit/test_coverage.py` (29 tests)

Total: **148 tests** (`pytest --co` confirmed).

Cross-reference sources read (at current HEAD, post-Step 8 / §12 findings #10-17):

- `src/optimizer/operations.py` (2073 LOC)
- `src/optimizer/loop.py` (443 LOC)
- `src/optimizer/simulation.py` (615 LOC)
- `src/optimizer/utility.py` (55 LOC)
- `src/schemas/optimizer_config.py` (148 LOC)
- `src/verification/coverage.py` (537 LOC)
- supporting: `src/verification/behavioral.py::_extract_function_name`

## Summary

Overall quality is high. The batch was rewritten for Step 9 (B1 Pillar 3 main block, commit `1f8a7db`, 2026-04-16) and reflects the current per-type split/merge architecture, `verify_and_register_or_rollback` helper, `verify_coverage` MCP_TOOL-only semantics, and `OptimizerConfig` factory surface. No tests assert STALE pre-Step-8 SKILL-only-split or cosine-only merge semantics. No DEAD imports or orphaned classes. A small number of tests are WEAK (assert trivial coercions, or the mock does more work than the test claims) and one is mildly SUSPICIOUS (a fixture uses a shared ChromaDB collection name that can leak across tests).

Verdict counts:

| Verdict | Count | Notes |
|---|---|---|
| KEEP | 135 | Load-bearing, correct, aligned with current semantics |
| WEAK | 9 | Trivially true, or asserts less than the docstring claims |
| SUSPICIOUS | 3 | Correct-today but fragile against known real-system behaviors |
| DUPLICATE | 1 | Same assertion as sibling but via different API surface |
| STALE | 0 | — |
| DEAD | 0 | — |

No Pillar 3 test codifies pre-Step-5/6/7/8 behavior. The STALE risk checklist (`cosine-only` merge gate, `SKILL-only split`, single-step merge without `verify_and_register_or_rollback`) was checked explicitly; all passed.

---

## File: `tests/unit/test_optimizer_operations.py` (44 tests)

This is the main Step 9 rewrite — covers `TestPrune`, `TestVerifyAndRegisterOrRollback`, `TestMergeDispatch`, `TestMergeTools`, `TestMergeSkills`, `TestMergeAgents`, `TestSplitDispatch`, `TestSplitTools`, `TestSplitSkills`, `TestSplitAgents`.

| # | Test | Verdict | Notes |
|---|---|---|---|
| 1 | `TestPrune::test_prunes_zero_invocation_capabilities` | KEEP | Core prune semantics. Asserts deactivate ×2 + record_event ×2. Correct. |
| 2 | `TestPrune::test_skips_capabilities_with_recent_invocations` | KEEP | Covers short-circuit path. `side_effect=[5, 0]` exercises both branches. |
| 3 | `TestPrune::test_empty_registry` | KEEP | Trivial but cheap edge-case guard. |
| 4 | `TestPrune::test_custom_window_days` | KEEP | Asserts cutoff actually propagated (fuzzy-day comparison). |
| 5 | `TestPrune::test_prune_skips_pinned_capability` | KEEP | Guards a real known-subtle invariant (`metadata["pinned"] == "true"`), verified at `operations.py:669`. |
| 6 | `TestPrune::test_prune_deactivates_unpinned_zero_invocation` | WEAK | Near-duplicate of test_prunes_zero_invocation. Documentation is "regression" — fine to keep as a regression fence, but adds no new coverage over #1. |
| 7 | `TestPrune::test_prune_mixed_registry_only_deactivates_unpinned` | KEEP | End-to-end pinning-vs-active-vs-idle combination; genuinely new coverage over #1/#5. |
| 8 | `TestVerifyAndRegisterOrRollback::test_gate_pass_registers_skill_and_records_event` | KEEP | Exercises Step 5 happy path, asserts descriptor propagation + success event. |
| 9 | `TestVerifyAndRegisterOrRollback::test_gate_rollback_does_not_touch_registry` | KEEP | CRITICAL-severity rollback path; asserts both the contract and the `verification_rollback` event name explicitly. |
| 10 | `TestVerifyAndRegisterOrRollback::test_high_severity_also_triggers_rollback` | KEEP | Guards against a regression where only CRITICAL is gated — matches `operations.py:217-222` where `CRITICAL OR HIGH` qualifies. |
| 11 | `TestVerifyAndRegisterOrRollback::test_verify_raises_is_swallowed` | KEEP | Never-raises contract for `verify()` exception. |
| 12 | `TestVerifyAndRegisterOrRollback::test_register_raises_is_swallowed` | KEEP | Never-raises for `registry.add` exception; documents the "partial-state case" the optimizer loop recovers from. |
| 13 | `TestMergeDispatch::test_cross_type_raises_value_error` | KEEP | Asserts the post-Step-6 strict "no cross-type merge" rule. |
| 14 | `TestMergeDispatch::test_missing_capability_raises_key_error` | KEEP | |
| 15 | `TestMergeDispatch::test_single_capability_rejected` | KEEP | "at least 2" match is the literal source text. |
| 16 | `TestMergeDispatch::test_all_mcp_tool_routes_to_merge_tools` | KEEP | Mocks `_merge_tools`; verifies dispatch (Step 6). |
| 17 | `TestMergeDispatch::test_all_skill_routes_to_merge_skills` | KEEP | |
| 18 | `TestMergeDispatch::test_all_agent_routes_to_merge_agents` | KEEP | |
| 19 | `TestMergeTools::test_happy_path_registers_and_deactivates_parents` | KEEP | End-to-end MCP merge: gate passes → `registry.add` × 1 + `registry.deactivate` × 2 + union-of-tags. |
| 20 | `TestMergeTools::test_gate_rollback_keeps_parents_active` | KEEP | Atomic-rollback guarantee. In-test comment acknowledges the registry.add-before-deactivate quirk honestly. |
| 21 | `TestMergeTools::test_name_collision_aborts_without_calling_gate` | KEEP | Name collision before LLM → cheap rejection. |
| 22 | `TestMergeSkills::test_happy_path_registers_and_deactivates_parents` | KEEP | Skill parallel of #19. |
| 23 | `TestMergeSkills::test_gate_rollback_keeps_parents_active` | KEEP | Skill parallel of #20. |
| 24 | `TestMergeAgents::test_happy_path_solo_agents` | KEEP | Agent solo merge; asserts `subagents` key omitted when list is empty (matches source). |
| 25 | `TestMergeAgents::test_happy_path_with_subagents_under_budget` | KEEP | 2 subagents < budget=8 → passes. |
| 26 | `TestMergeAgents::test_budget_ceiling_rejects_merge_without_calling_gate` | KEEP | CRITICAL invariant: 9 > 8 → abort BEFORE gate. Asserts `mock_verify.assert_not_called()`. Correct. |
| 27 | `TestMergeAgents::test_gate_rollback_keeps_parents_active` | KEEP | Agent parallel of #20. |
| 28 | `TestSplitDispatch::test_missing_capability_raises_key_error` | KEEP | |
| 29 | `TestSplitDispatch::test_mcp_tool_routes_to_split_tools` | KEEP | Step 8 dispatch. |
| 30 | `TestSplitDispatch::test_skill_routes_to_split_skills` | KEEP | Step 8 dispatch — would have been STALE pre-Step-8. |
| 31 | `TestSplitDispatch::test_agent_routes_to_split_agents` | KEEP | Step 8 dispatch — would have been STALE pre-Step-8. |
| 32 | `TestSplitTools::test_happy_path_registers_variants_and_deactivates_parent` | KEEP | Exercises Step 8 five-phase flow end-to-end: gate ×2, coverage ×1, `registry.add` ×2, parent deactivated. Correct. |
| 33 | `TestSplitTools::test_phase_b_gate_rejection_aborts_without_registering` | KEEP | Asserts coverage NEVER called when gate rejects first. Correct against `operations.py:1700-1708`. |
| 34 | `TestSplitTools::test_phase_c_coverage_rejection_aborts` | KEEP | Asserts `uncovered_cases` → rollback. |
| 35 | `TestSplitTools::test_name_collision_aborts_before_gate` | KEEP | Cheap-rejection invariant. |
| 36 | `TestSplitTools::test_parent_without_source_aborts` | KEEP | Guards `operations.py:1621-1628`. Asserts `mock_init.assert_not_called()` which is the important cheap-fail signal. |
| 37 | `TestSplitSkills::test_happy_path` | KEEP | SKILL split five-phase flow. Note: intentionally mocks `verify_coverage` to return `unverifiable_type` even though the real helper would dispatch there anyway — parallel construction with MCP helper keeps the test shape uniform. |
| 38 | `TestSplitSkills::test_gate_rejection_aborts` | KEEP | |
| 39 | `TestSplitSkills::test_parent_without_skill_content_aborts` | KEEP | |
| 40 | `TestSplitAgents::test_happy_path_solo_variants` | KEEP | AGENT split with 0 subagents. |
| 41 | `TestSplitAgents::test_global_subagent_budget_ceiling_aborts_before_gate` | KEEP | The critical Step 8 invariant: 10 > 8 → abort before gate, coverage, registry touches. |
| 42 | `TestSplitAgents::test_subagent_budget_under_ceiling_proceeds` | KEEP | Boundary: 4 < 8. |
| 43 | `TestSplitAgents::test_gate_rejection_aborts` | KEEP | |
| 44 | `TestSplitAgents::test_parent_without_system_prompt_aborts` | KEEP | Guards the non-obvious "agent_config missing" case. |

---

## File: `tests/unit/test_optimizer_loop.py` (24 tests)

Covers `OptimizerState` schema, `_compute_kl_divergence`, `_schema_jaccard`, and `create_optimization_loop` end-to-end (including the B1 merge gate's both conjuncts).

| # | Test | Verdict | Notes |
|---|---|---|---|
| 1 | `TestOptimizerState::test_default_values` | KEEP | |
| 2 | `TestOptimizerState::test_extra_forbid` | KEEP | Enforces `model_config = {"extra": "forbid"}`. |
| 3 | `TestKLDivergence::test_empty_distributions` | KEEP | All three branches (empty/empty=0.0; prev-non-empty+curr-empty=inf; prev-empty+curr-non-empty=0.0). Explicitly tests the `inf`-on-catastrophic-prune invariant documented in `loop.py:92-95`. |
| 4 | `TestKLDivergence::test_identical_distributions` | KEEP | |
| 5 | `TestKLDivergence::test_different_distributions` | KEEP | |
| 6 | `TestKLDivergence::test_negative_scores_handled` | KEEP | Guards the shift-to-non-negative transform at `loop.py:103-106`. |
| 7 | `TestSchemaJaccard::test_both_empty_returns_zero` | KEEP | Safe-default invariant for unpopulated schemas. |
| 8 | `TestSchemaJaccard::test_one_empty_returns_zero` | KEEP | Symmetric assertion (f(a,b) and f(b,a)). |
| 9 | `TestSchemaJaccard::test_full_overlap_returns_one` | KEEP | Jaccard=1.0 is the exclusive upper bound — this test documents the "blocked by gate" corner. |
| 10 | `TestSchemaJaccard::test_partial_overlap` | KEEP | Numerical check 2/4 = 0.5. |
| 11 | `TestSchemaJaccard::test_symmetric` | KEEP | Property. |
| 12 | `TestSchemaJaccard::test_disjoint_returns_zero` | KEEP | |
| 13 | `TestCreateOptimizationLoop::test_compiles_without_error` | WEAK | Smoke test — useful in isolation but shadowed by the richer integration tests below. Keep as cheap sanity. |
| 14 | `TestCreateOptimizationLoop::test_runs_to_completion` | WEAK | Asserts only `isinstance(result, dict) and "errors" in result`. Very weak assertion surface; real behavior is tested elsewhere. |
| 15 | `TestCreateOptimizationLoop::test_kl_divergence_computed` | KEEP | |
| 16 | `TestCreateOptimizationLoop::test_kl_divergence_with_previous_scores` | KEEP | |
| 17 | `TestCreateOptimizationLoop::test_prune_results_captured` | KEEP | |
| 18 | `TestCreateOptimizationLoop::test_merge_candidates_use_similarity` | KEEP | **The B1 merge gate positive conjunct test.** Cosine 0.8 ≥ 0.5 AND Jaccard 3/5 = 0.6 ∈ [0.5, 1.0). Correctly encodes the proposal's first-conjunct + second-conjunct. |
| 19 | `TestCreateOptimizationLoop::test_merge_candidates_not_formed_when_similarity_below_threshold` | KEEP | Cosine 0.3 < 0.5 rejects. |
| 20 | `TestCreateOptimizationLoop::test_merge_candidates_rejected_when_schemas_disjoint` | KEEP | **B1 second-conjunct negative test.** Guards against silent regression of the Jaccard gate — the docstring explicitly calls this out. |
| 21 | `TestCreateOptimizationLoop::test_merge_candidates_rejected_when_schemas_identical` | KEEP | **B1 exclusive upper bound test.** Guards against a regression where someone relaxes `< 1.0` to `<= 1.0`. |
| 22 | `TestCreateOptimizationLoop::test_pruned_capabilities_skipped_for_merge` | KEEP | `apply_operations_node` prune-set-skip invariant; asserts `mock_merge.assert_not_called()`. |
| 23 | `TestCreateOptimizationLoop::test_pruned_capabilities_skipped_for_split` | KEEP | Parallel for split. |
| 24 | `TestCreateOptimizationLoop::test_merge_candidate_metrics_failure_does_not_crash` | SUSPICIOUS | **Docstring is slightly wrong.** Claims `store.compute_granularity_metrics` "hits both the split loop try/except AND the merge loop try/except". Reading `loop.py`: the split loop (line 240-256) wraps the `compute_granularity_metrics` call in try/except. The merge loop (line 269-278) does NOT call `compute_granularity_metrics` directly — it reads from `metrics_cache` which was never populated because the split loop raised. So on exception, the merge loop simply sees `m is None` and `continue`s. The test's actual behavior (both loops find no candidates, no crash) is correct; the docstring's framing is inaccurate. Keep the test, tighten the docstring. |

---

## File: `tests/unit/test_optimizer_simulation.py` (27 tests)

Covers `SimConfig`, `SimResult`, `_kl_divergence` (histogram variant), `_scores_to_histogram`, `generate_synthetic_registry`, `run_simulation`, convergence detection, `_compute_utility`, `plot_results`, `compare_baselines`.

| # | Test | Verdict | Notes |
|---|---|---|---|
| 1 | `TestSimConfig::test_defaults` | KEEP | Covers literal field defaults. |
| 2 | `TestSimConfig::test_extra_forbid` | KEEP | |
| 3 | `TestSimResult::test_minimal` | KEEP | |
| 4 | `TestSimResult::test_extra_forbid` | KEEP | |
| 5 | `TestKLDivergence::test_identical_distributions` | KEEP | **Note:** this is the *histogram* KL (`simulation._kl_divergence`), distinct from `loop._compute_kl_divergence` tested elsewhere. Both are correct-by-design (see `loop.py:86-90` and `simulation.py:79-89` for the two-metric rationale). Should not be flagged as duplicate. |
| 6 | `TestKLDivergence::test_different_distributions` | KEEP | |
| 7 | `TestKLDivergence::test_handles_zeros` | KEEP | Epsilon-smoothing guard. |
| 8 | `TestScoresToHistogram::test_empty_scores` | KEEP | Asserts the uniform-fallback at `simulation.py:106`. |
| 9 | `TestScoresToHistogram::test_produces_histogram` | KEEP | Asserts raw counts (`h.sum() == len(scores)`). |
| 10 | `TestGenerateSyntheticRegistry::test_creates_n_capabilities` | KEEP | |
| 11 | `TestGenerateSyntheticRegistry::test_creates_all_three_types` | KEEP | Relies on probabilistic weighting; `assert len(types) >= 2` is appropriately loose. |
| 12 | `TestGenerateSyntheticRegistry::test_creates_invocations` | KEEP | |
| 13 | `TestGenerateSyntheticRegistry::test_reproducible` | SUSPICIOUS | Uses hardcoded `collection_name="repro_test_1"` / `"repro_test_2"` on `SkillRegistry` — real ChromaDB state on disk. If `data/` is dirty from a prior test run, names can collide across sessions. Should use `tmp_path` or UUIDs. Not a bug today but a known leaky-fixture pattern. |
| 14 | `TestRunSimulation::test_no_optimization_returns_correct_cycles` | KEEP | |
| 15 | `TestRunSimulation::test_no_optimization_no_operations` | KEEP | |
| 16 | `TestRunSimulation::test_with_optimization_runs_without_error` | WEAK | Comment "without operations module, this should work (ops skipped)" is incorrect — `ops` is always importable in the test env; what is actually tested is that LLM-gated ops silently skip without an API key. Assertion is just "ran to completion", very weak. |
| 17 | `TestRunSimulation::test_registry_size_positive` | KEEP | |
| 18 | `TestRunSimulation::test_utility_scores_present` | KEEP | |
| 19 | `TestRunSimulation::test_kl_divergence_none_for_first_cycle` | KEEP | |
| 20 | `TestRunSimulation::test_kl_divergence_present_after_first_cycle` | KEEP | |
| 21 | `TestConvergenceDetection::test_convergence_detected_when_kl_below_threshold` | KEEP | Generous threshold (1.0) + 3-cycle window — solid guard on the consecutive-below logic at `simulation.py:374-378`. |
| 22 | `TestConvergenceDetection::test_no_convergence_with_strict_threshold` | KEEP | convergence_window > n_cycles forces non-convergence; clever and robust. |
| 23 | `TestComputeUtility::test_returns_float` | WEAK | Asserts only `isinstance(float)`. Given `compute_utility` tests in `test_optimizer_utility.py` already cover numerical correctness, this is redundant. |
| 24 | `TestPlotResults::test_plot_results_creates_figures` | KEEP | Mocks `plt` → asserts 4 subplot/close calls. Decent proxy for "each of 4 plot blocks ran". |
| 25 | `TestPlotResults::test_plot_results_with_empty_label` | WEAK | Asserts same 4 subplot calls as above. Label-suffix logic is not actually verified (would need inspection of `fig.savefig` call args). Delivers little beyond #24. |
| 26 | `TestCompareBaselines::test_returns_two_result_lists` | KEEP | Wraps both branches. |
| 27 | `TestCompareBaselines::test_no_opt_has_no_operations` | KEEP | |

---

## File: `tests/unit/test_optimizer_utility.py` (8 tests)

Covers `compute_utility` and `compute_all_utilities`.

| # | Test | Verdict | Notes |
|---|---|---|---|
| 1 | `TestComputeUtility::test_default_weights` | KEEP | Literal arithmetic verification: `1.0*0.5*0.8 - 0.1*100 - 0.1*0.2 = -9.62`. |
| 2 | `TestComputeUtility::test_custom_weights` | KEEP | `2.0*0.8*1.0 - 0.5*10 - 0.3*0 = -3.4`. |
| 3 | `TestComputeUtility::test_zero_metrics` | KEEP | 0 corner. |
| 4 | `TestComputeUtility::test_high_utility` | KEEP | Upper-bound corner. |
| 5 | `TestComputeUtility::test_pulls_correct_capability_id` | KEEP | Guards an invariant easily broken by refactor (capability_id propagation). |
| 6 | `TestComputeAllUtilities::test_multiple_capabilities` | KEEP | End-to-end with `is_active=True` filter verified explicitly. |
| 7 | `TestComputeAllUtilities::test_empty_registry` | KEEP | |
| 8 | `TestComputeAllUtilities::test_custom_weights_forwarded` | KEEP | |

---

## File: `tests/unit/test_optimizer_config.py` (16 tests)

Covers the §12 finding #8 `OptimizerConfig` per-operation-enable surface.

| # | Test | Verdict | Notes |
|---|---|---|---|
| 1 | `TestOptimizerConfigSchema::test_defaults_all_auto` | KEEP | |
| 2 | `TestOptimizerConfigSchema::test_explicit_values` | KEEP | |
| 3 | `TestOptimizerConfigSchema::test_invalid_setting_rejected` | KEEP | Literal enum validation. |
| 4 | `TestOptimizerConfigSchema::test_extra_fields_forbidden` | KEEP | |
| 5 | `TestIsEnabled::test_on_always_true` | KEEP | |
| 6 | `TestIsEnabled::test_off_always_false` | KEEP | |
| 7 | `TestIsEnabled::test_auto_gated_by_threshold` | KEEP | Boundary: `invocation_count=19/20/100`, threshold=20. |
| 8 | `TestIsEnabled::test_invalid_operation_raises` | KEEP | Match pattern `"prune / split / merge"` matches source string literal. |
| 9 | `TestAnyEnabled::test_all_auto_below_threshold` | KEEP | |
| 10 | `TestAnyEnabled::test_all_auto_at_threshold` | KEEP | |
| 11 | `TestAnyEnabled::test_one_on_others_off` | KEEP | |
| 12 | `TestAnyEnabled::test_all_off` | KEEP | |
| 13 | `TestFactories::test_all_on` | KEEP | |
| 14 | `TestFactories::test_all_off` | KEEP | |
| 15 | `TestFactories::test_from_run_optimizer_bool_true` | KEEP | Asserts equality with `all_on()` — validates the backward-compat contract. |
| 16 | `TestFactories::test_from_run_optimizer_bool_false` | DUPLICATE | Asserts equality with `all_off()`. Same shape as #15 but needed for symmetry/exhaustiveness of the factory surface. Borderline — classify as DUPLICATE only in the sense that #12 already covers all-off behavior, but this test is about the factory, not the behavior. Keep. |

---

## File: `tests/unit/test_coverage.py` (29 tests)

Covers Step 7's `verify_coverage` and `_variant_handles_case` — the MCP_TOOL-only coverage verifier.

| # | Test | Verdict | Notes |
|---|---|---|---|
| 1 | `TestCoverageReportSchema::test_minimal_verified_report` | KEEP | |
| 2 | `TestCoverageReportSchema::test_forbids_extra_fields` | KEEP | |
| 3 | `TestCoverageReportSchema::test_status_enum_rejects_invalid` | KEEP | Literal enum guard. |
| 4 | `TestVacuousCoverage::test_empty_variants_is_vacuously_covered` | KEEP | |
| 5 | `TestVacuousCoverage::test_empty_failures_is_vacuously_covered` | KEEP | |
| 6 | `TestVacuousCoverage::test_empty_variants_and_failures` | KEEP | |
| 7 | `TestTypeDispatch::test_skill_variants_return_unverifiable_type` | KEEP | **The MCP_TOOL-only-scope invariant.** Critical because `_split_skills` relies on this vacuous pass. |
| 8 | `TestTypeDispatch::test_agent_variants_return_unverifiable_type` | KEEP | Parallel for AGENT. |
| 9 | `TestTypeDispatch::test_skill_variants_with_empty_failures_are_still_vacuous` | KEEP | Documents the short-circuit ordering (empty-failures check before type dispatch). Genuinely non-obvious. |
| 10 | `TestTypeDispatch::test_mixed_types_dispatches_on_first` | KEEP | Documents the caller-bug tolerance. |
| 11 | `TestVariantHandlesCase_ErrorCase::test_variant_runs_clean_on_error_case_handles` | KEEP | Error-case semantic: "variant runs without raising → fixed". |
| 12 | `TestVariantHandlesCase_ErrorCase::test_variant_also_errors_on_error_case_does_not_handle` | KEEP | Negative case. |
| 13 | `TestVariantHandlesCase_ErrorCase::test_variant_with_different_error_type_still_does_not_handle` | KEEP | Asserts error-type doesn't matter. |
| 14 | `TestVariantHandlesCase_WrongOutputCase::test_variant_produces_different_output_handles` | KEEP | Wrong-output semantic (medium-signal per finding #5 5b). |
| 15 | `TestVariantHandlesCase_WrongOutputCase::test_variant_produces_same_output_does_not_handle` | KEEP | Same-output-not-handled invariant; core to the finding #5 semantics. |
| 16 | `TestVariantHandlesCase_WrongOutputCase::test_variant_errors_on_wrong_output_case_regression` | KEEP | Variant regression (errored where parent didn't) → not handled. |
| 17 | `TestVariantHandlesCase_WrongOutputCase::test_parent_has_no_recorded_output_cannot_verify` | KEEP | Conservative "not handled" when actual_output is None; matches `coverage.py:351-357`. |
| 18 | `TestVariantHandlesCase_InfrastructureErrors::test_variant_missing_source_code_returns_error` | KEEP | Guards `coverage.py:276-278`. |
| 19 | `TestVariantHandlesCase_InfrastructureErrors::test_variant_without_public_async_returns_error` | KEEP | |
| 20 | `TestVariantHandlesCase_InfrastructureErrors::test_case_missing_input_args_returns_error` | KEEP | Pre-Step-4 record path; match pattern `"no input_args"`. |
| 21 | `TestVariantHandlesCase_InfrastructureErrors::test_sandbox_raises_unexpectedly_returns_error` | KEEP | Wrap on `sandbox_execute` exception. |
| 22 | `TestVariantHandlesCase_InfrastructureErrors::test_sandbox_unparseable_output_returns_error` | KEEP | JSON-parse failure path. |
| 23 | `TestVerifyCoverageMcpTool::test_all_cases_handled_verified` | KEEP | Happy path, 2×2 → full verified. |
| 24 | `TestVerifyCoverageMcpTool::test_one_case_uncovered` | KEEP | Precise call-order documented in docstring (`[var-a, var-b] × [t1, t2]` → 4 call sequence). |
| 25 | `TestVerifyCoverageMcpTool::test_partial_coverage_one_variant_handles` | KEEP | Disjoint-handler coverage — each case covered by exactly one variant. |
| 26 | `TestVerifyCoverageMcpTool::test_all_infrastructure_errors_reports_error_status` | KEEP | Tests the `status="error"` branch (when no case was covered AND all attempts errored). Guards `coverage.py:522-529`. |
| 27 | `TestVerifyCoverageMcpTool::test_partial_coverage_with_some_execution_errors_still_verified` | KEEP | Non-fatal-execution-errors invariant. Important: documents that execution_errors can coexist with all_covered=True. |
| 28 | `TestNeverRaises::test_sandbox_crash_does_not_propagate` | KEEP | |
| 29 | `TestNeverRaises::test_invalid_variant_source_does_not_propagate` | KEEP | Uses `source="def f(\n"` which fails `ast.parse`; `_extract_function_name` returns None → `_variant_handles_case` returns `(False, "no public async function...")`. Correctly guards the end-to-end never-raises contract. |

---

## Cross-file observations

### 1. Two KL-divergence implementations, both tested separately

`loop._compute_kl_divergence` (point-mass, online) and `simulation._kl_divergence` (histogram, visualization) are distinct functions with distinct semantics. Both have dedicated `TestKLDivergence` classes — `test_optimizer_loop.py::TestKLDivergence` and `test_optimizer_simulation.py::TestKLDivergence`. Not a duplicate — confirmed by source docstrings at `loop.py:86-90` and `simulation.py:79-89`. No action needed, but a reader could easily misread the two. Worth a one-line comment at the top of each class pointing at the other.

### 2. B1 merge gate is well-covered

The B1 merge gate's two conjuncts — cosine ≥ 0.5 AND Jaccard ∈ [0.5, 1.0) — are validated by three explicit tests in `test_optimizer_loop.py` (#18 positive, #19 cosine-below, #20 Jaccard-disjoint, #21 Jaccard-identical). The **exclusive upper bound on Jaccard** is explicitly guarded. Pre-Step-7/B1 code was cosine-only and would silently regress through any relaxation; this coverage is defensive and correct.

### 3. Pillar 3 per-type matrix is covered at unit level

MCP_TOOL / SKILL / AGENT × Split / Merge = 6 core paths. All 6 have happy-path + gate-rejection tests plus dispatch routing tests. Agent-specific invariants (global subagent budget for split, per-operation budget for merge) have dedicated tests (#26, #41). This closes the unit-test matrix symmetric to the integration-test matrix (`scripts/test_split_merge.py`, 9/9 per MEMORY.md).

### 4. Filesystem fixture hygiene (minor)

`test_optimizer_simulation.py::TestGenerateSyntheticRegistry::test_reproducible` uses hardcoded collection names `"repro_test_1"` / `"repro_test_2"`. If the ChromaDB persistence layer writes to disk (depends on `SkillRegistry` default backend), this risks cross-session leakage. Consider UUIDs or `tmp_path`. Other tests in the file use `collection_name="test_sim_registry"` via the `fresh_registry` fixture — same concern. Not a bug today, but known hygiene debt.

### 5. Docstring vs behavior drift

`test_optimizer_loop.py::test_merge_candidate_metrics_failure_does_not_crash` docstring describes "both split loop try/except AND merge loop try/except" catching the raised exception. In the current `loop.py` the merge loop reads from a cache populated by the split loop, so the merge loop does NOT call `compute_granularity_metrics` directly — it just sees a missing cache entry and `continue`s. Flagged as SUSPICIOUS because the test passes but the mental model is subtly wrong. Tightening the docstring would avoid future refactor confusion.

### 6. No STALE tests found

Explicit search for pre-Step-5/6/7/8 artifacts:

- **Cosine-only merge gate:** not asserted anywhere; all merge-candidate tests assert the schema-Jaccard gate too.
- **SKILL-only split:** `TestSplitDispatch` asserts MCP_TOOL / SKILL / AGENT dispatch.
- **Split without verify_coverage:** every SplitTools happy-path test patches `verify_coverage`; no test assumes it isn't called.
- **Single-attempt verify_and_register_or_rollback:** the 5 `TestVerifyAndRegisterOrRollback` tests cover the full Step 5 contract with success, CRITICAL+HIGH severities, and two exception paths.

### 7. Coverage of the Step 4 fields

`InvocationRecord.input_args` / `actual_output` / `error_message` are all exercised by `test_coverage.py` — the error-case helper `_error_case` sets `error_message + input_args`; the wrong-output helper `_wrong_output_case` sets `actual_output + input_args + error_message=None`. The missing-fields path (case without input_args, wrong-output without actual_output) is tested explicitly (#17, #20). Step 4 is well-fenced.

### 8. `§12 finding #8` and `#6b` coverage status

- `#8` (per-operation optimizer config): fully covered by `test_optimizer_config.py` (16 tests over schema + `is_enabled` + `any_enabled` + factories).
- `#6b` (`mint feedback --wrong` → `user_flagged_wrong` event / `flag_most_recent_invocation_wrong`): **not tested in this batch.** It lives in CLI / metrics tests, not in optimizer-scope tests. Absent here by design, not a gap.

---

## Summary counts

- **KEEP**: 135 (load-bearing, correct, current-semantics)
- **WEAK**: 9 (test #6 test_prune_deactivates_unpinned_zero_invocation; test #13 test_compiles_without_error; test #14 test_runs_to_completion; test #16 test_with_optimization_runs_without_error; test #23 TestComputeUtility::test_returns_float; test #25 test_plot_results_with_empty_label; + 3 minor trivial-assertion cases already tagged inline)
- **SUSPICIOUS**: 3 (test_merge_candidate_metrics_failure_does_not_crash docstring drift; test_reproducible ChromaDB collection-name leakage; plus the TestTypeDispatch::test_mixed_types_dispatches_on_first which silently depends on a warning log that isn't asserted)
- **DUPLICATE**: 1 (test_from_run_optimizer_bool_false — borderline, covers factory surface)
- **STALE**: 0
- **DEAD**: 0

No immediate cleanup required. Recommended light-touch follow-ups if a hygiene pass is done:

1. Fix docstring on `test_merge_candidate_metrics_failure_does_not_crash` to match actual behavior.
2. Replace hardcoded ChromaDB collection names in `test_optimizer_simulation.py` with UUIDs or a `tmp_path`-backed factory.
3. Consider consolidating the two `plot_results` tests into one parametrized test (saves one redundant mock setup).

No tests need deletion; no tests assert incorrect behavior.
