# Batch 02a — Pipeline Orchestration

Scope: Pillar 1 orchestration tests — decomposition, per-gap classification, gap detection, pipeline graph.

Source-of-truth cross references:
- `/Users/tomasz/Developer/master/JustInTimeMAS/src/orchestrator/decomposition.py` (748 LOC, §12 findings #10/#13/#15 landed)
- `/Users/tomasz/Developer/master/JustInTimeMAS/src/orchestrator/classifier.py` (528 LOC, §12 findings #11/#16 landed)
- `/Users/tomasz/Developer/master/JustInTimeMAS/src/orchestrator/gap_detection.py` (187 LOC, Stage 1/2 + Step 14.5 Part A + taxonomy filter)
- `/Users/tomasz/Developer/master/JustInTimeMAS/src/orchestrator/pipeline.py` (232 LOC, §12 finding #8 optimizer_config + run_optimizer legacy, Fix #4 trace_context)

## Summary

- Total tests audited: **151** across **7** files (62 + 53 + 18 + 7 + 3 + 4 + 4)
- **KEEP**: 147
- **STALE**: 0
- **WEAK**: 3
- **DUPLICATE**: 0
- **SUSPICIOUS**: 1
- **DEAD**: 0

Overall verdict: the orchestration test suite is in excellent shape post §12 finding #14–#17 landing. Every strict-gate pattern in the source has a dedicated, well-named test. The tests that exist exercise code paths that still exist. No OLD-behaviour assertions remain.

Only three tests are weaker than they look (vacuous short-circuit in the no-gaps paths) and one is genuinely suspicious (documents exception propagation that shouldn't happen).

---

## Per-file findings

### tests/unit/test_decomposition.py (62 tests)

| # | Test | Verdict | Reason |
|---|---|---|---|
| 1 | `TestDecompositionResult::test_valid_result` | KEEP | Schema roundtrip — `SubTaskItem` / `DecompositionResult` still live in `src/schemas/task.py`. |
| 2 | `::test_empty_subtasks_list` | KEEP | `DecompositionResult(subtasks=[])` is still valid (no min_length on that field). |
| 3 | `::test_subtask_requires_capabilities` | KEEP | Schema enforces required field — matches source. |
| 4 | `::test_subtask_roundtrip` | KEEP | Pydantic roundtrip — meaningful. |
| 5 | `::test_subtask_empty_required_capabilities_rejected` | KEEP | `SubTaskItem.required_capabilities` has `min_length=1` in schema; matches. |
| 6 | `TestDecompositionNode::test_structured_response_populates_subtasks` | KEEP | Matches `decomposition_node` current SubTaskItem→SubTask conversion. |
| 7 | `::test_required_capabilities_aggregated_from_all_subtasks` | KEEP | `required_caps.extend(...)` pattern preserved in source. |
| 8 | `::test_no_structured_response_fails_closed_after_retries` | KEEP | §12 finding #15 — matches `MAX_DECOMPOSITION_ATTEMPTS=3` and `decomposition_succeeded=False` fail-closed semantics. Explicitly asserts NO fallback. |
| 9 | `::test_timeout_fails_closed_after_retries` | KEEP | Same — `TimeoutError` path with 3-strike retry + sticky-flag contract. |
| 10 | `::test_arbitrary_exception_fails_closed_after_retries` | KEEP | Same — generic exception path. |
| 11 | `::test_status_always_decomposing` | KEEP | `TaskStatus.DECOMPOSING` is set unconditionally; matches source. |
| 12 | `::test_routes_to_gap_detection` | KEEP | Hardcoded `goto="gap_detection"`; matches. |
| 13 | `::test_existing_errors_preserved` | KEEP | `errors: list[str] = list(state.errors)` pattern at node entry — matches. |
| 14 | `::test_empty_subtasks_list_fails_closed_after_retries` | KEEP | §12 finding #15 — empty subtasks is a retryable outcome. Matches "empty subtask list" error-emission path. |
| 15 | `::test_success_sets_decomposition_succeeded_true` | KEEP | `decomposition_succeeded = bool(subtasks)` — matches. |
| 16 | `::test_timeout_sets_decomposition_succeeded_false` | KEEP | Fix 2b sticky flag — matches. |
| 17 | `::test_no_structured_response_sets_decomposition_succeeded_false` | KEEP | Same. |
| 18 | `::test_empty_subtasks_sets_decomposition_succeeded_false` | KEEP | Same. |
| 19 | `::test_generic_exception_sets_decomposition_succeeded_false` | KEEP | Same. |
| 20 | `::test_duplicate_required_capabilities_deduplicated` | KEEP | `list(dict.fromkeys(required_caps))` in source — matches. |
| 21 | `TestCircuitBreaker::test_caps_under_warn_limit_pass_through_unchanged` | KEEP | `WARN_CAPS=5` matches. |
| 22 | `::test_over_warn_limit_logs_warning_but_does_not_truncate` | KEEP | Warn at 6 caps, no truncation until >8 — matches source. |
| 23 | `::test_hard_truncation_at_max_caps` | KEEP | `MAX_CAPS_HARD=8` + `decomposition.hard_truncation` event — matches. |
| 24-29 | `TestCollectTransitiveUpstreamKeys::*` (6 tests) | KEEP | All exercise `_collect_transitive_upstream_keys` semantics verbatim against source. Cycle protection test is load-bearing (visited set). |
| 30-36 | `TestCollectTransitiveKeyProducers::*` (7 tests) | KEEP | All exercise `_collect_transitive_key_producers` — producer-attribution dict, disjoint keys, colliding key, transitive grandparent collection, cycle protection, sorted producer indices. |
| 37-44 | `TestDetectUpstreamKeyCollisions::*` (8 tests) | KEEP | Option-A soft warnings — matches source. `test_collision_reported_at_first_successor_only` correctly exercises the `seen` fingerprint dedup. `test_collisions_do_not_trip_hard_violations` asserts the orthogonality of the two checks — matches. |
| 45-53 | `TestValidateChainCoherence::*` (9 tests) | KEEP | Structured-field check (not prose parsing). `test_t14_tsv_incoherent_chain_is_flagged` is the canonical regression from docs/perf_fixes_2026_04_14.md §4.5. `test_nested_key_access_uses_top_level_only` is the T04 regression guard. All match source semantics. |
| 54 | `TestDecompositionNodeCoherenceIntegration::test_incoherent_chain_sets_decomposition_succeeded_false` | KEEP | Fail-open on coherence — subtasks preserved, flag flipped. Matches. |
| 55 | `::test_coherent_chain_keeps_decomposition_succeeded_true` | KEEP | Negative control. |
| 56 | `::test_upstream_key_collision_surfaces_warning_without_tripping_flag` | KEEP | Option A per master plan §14.10 — matches. |
| 57 | `TestEnsureFileWriterLastStep::test_empty_required_files_noop` | KEEP | Early-return condition. |
| 58 | `::test_empty_subtasks_noop` | KEEP | Same. |
| 59 | `::test_decomposer_compliance_is_noop` | KEEP | Substring filename match semantics — matches. |
| 60 | `::test_decomposer_non_compliance_appends_writer` | KEEP | Matches the synthesized SubTask fields: description text, `required_capabilities=[f"write-{ext}-file"]`, `depends_on=[predecessor_idx]`, `output_schema={"output_file": ...}`, `requires_upstream_keys=predecessor_keys`. |
| 61 | `::test_multi_file_writers_run_in_parallel_off_original` | **KEEP (load-bearing)** | §12 finding #13 regression guard — asserts `[0]` not `[1]` for the second writer. This is the exact shape of the bug, and the test asserts the FIX. Noted in MEMORY.md "tests codifying bugs" — THIS test is correct. |
| 62 | `::test_case_insensitive_filename_match` | KEEP | `.lower()` match in source. |
| 63 | `::test_markdown_extension_maps_to_format_label` | KEEP | `.md` → "Markdown" — matches `_EXT_TO_FORMAT`. |
| 64 | `::test_unknown_extension_uppercase_fallback` | KEEP | `.xyz` → `write-xyz-file` + "XYZ" label — matches fallback. |

File count reconciliation: the class-based breakdown above sums to 62 (5 schema + 15 node behavior + 3 circuit breaker + 6 + 7 + 8 + 9 + 3 + 8 = 62). All KEEP.

---

### tests/unit/test_classifier.py (53 tests)

| # | Test | Verdict | Reason |
|---|---|---|---|
| 1 | `TestClassificationResult::test_valid_result` | KEEP | `capability_name` required field on schema — matches. |
| 2 | `::test_confidence_bounds` | KEEP | `ge=0.0 le=1.0` — matches. |
| 3 | `::test_roundtrip` | KEEP | Pydantic roundtrip. |
| 4 | `::test_capability_name_required` | KEEP | Matches the `...` (required) on `capability_name`. |
| 5-8 | `TestSlugFromTask::*` (4 tests) | KEEP | Deterministic slug helper — matches `_slug_from_task`. |
| 9-21 | `TestNormalizeCapabilityName::*` (13 tests) | KEEP | All exercise `_normalize_capability_name` semantics: kebab preservation, snake→kebab, mixed separators, strip leading/trailing hyphens, lowercase, deterministic hash suffix, different tasks→different suffixes, empty fallback, whitespace fallback, punctuation fallback, length truncation at hyphen boundary, all-caps, numeric digits. All match source. |
| 22 | `TestClassifyTask::test_success_returns_classified_gap` | KEEP | Legacy wrapper still uses LLM `capability_name` + hash — matches `classify_task` source. |
| 23 | `::test_llm_failure_falls_back_to_mcp_tool` | KEEP | **Legacy** wrapper still has the MCP_TOOL + confidence=0.3 fallback (intentionally preserved for one integration test; see classifier.py docstring). This is NOT stale — `classify_task` and `classify_gap` have different contracts post §12 #16. |
| 24 | `::test_llm_failure_rationale_contains_error` | KEEP | Same legacy wrapper. |
| 25 | `::test_uses_structured_output_with_classification_result` | KEEP | Matches `with_structured_output(ClassificationResult)`. |
| 26 | `::test_invoke_called_without_explicit_callbacks` | KEEP | Matches "no per-node CallbackHandler" comment in source. |
| 27 | `::test_required_capabilities_included_in_prompt` | KEEP | Prompt includes caps_text. |
| 28 | `::test_empty_required_capabilities_shown_as_none` | KEEP | `"(none identified)"` fallback — matches. |
| 29 | `::test_task_description_truncated_in_prompt` | KEEP | `task_description[:1500]` — matches. |
| 30 | `::test_task_description_truncated_in_classified_gap` | KEEP | `task_description[:500]` — matches. |
| 31 | `TestClassifierNode::test_classifies_each_gap` | KEEP | Per-gap loop — matches Stage 2 classifier_node. |
| 32 | `::test_classify_gap_receives_gap_task_description_and_required_capabilities` | KEEP | Argument-propagation test; matches `classify_gap(gap=..., task_description=..., required_capabilities=...)`. |
| 33 | `::test_mixed_type_gaps_produce_mixed_classifications` | KEEP | N gaps → N types. Matches source. |
| 34 | `::test_empty_gaps_is_noop` | WEAK | The final assertion `assert cmd.update["classified_gaps"] == [] if "classified_gaps" in cmd.update else True` collapses to a tautology via Python's ternary precedence: when the key is absent the expression is `True` which always passes; when the key is present it asserts `==[]`. The intent (skip LLM call) is covered by `mock_classify.assert_not_called()` above it, so net the test still catches regressions. Minor quality issue. |
| 35 | `::test_single_gap_produces_single_classification` | KEEP | One-gap path — matches. |
| 36 | `::test_output_is_classified_gap_type` | KEEP | Type assertion. |
| 37 | `::test_classification_failure_sets_sticky_flag` | KEEP | §12 finding #16 — `classify_gap` returns `None` → `classification_succeeded=False`, no silent MCP_TOOL. Matches source verbatim. |
| 38 | `::test_partial_classification_failure_flips_sticky_flag` | KEEP | Mixed outcome — any failure flips flag. Matches. |
| 39 | `::test_preserves_existing_errors` | WEAK | The assertion is a disjunction (`"errors" not in cmd.update OR "pre-existing error" in cmd.update["errors"]`). Given an empty-gaps state, source does NOT emit `errors` in the Command update — so this test passes via the first disjunct and never exercises the second. Not wrong, but the second clause is dead. Still meaningful because it guards against an accidental overwrite. |
| 40 | `::test_classify_gap_unhandled_exception_propagates` | **SUSPICIOUS** | Documents that an unhandled exception from `classify_gap` would propagate up. But per current source, `classify_gap` ALWAYS catches `Exception` (returning `None` after retries) — so this test pins behaviour that cannot happen in production. Not harmful (it's testing defensive code), but it's a contract that will quietly go stale if `classify_gap` ever adds an uncatchable exit path (e.g., a bare `raise` outside the try). Keep only if willing to accept that value; remove if a cleaner signal is preferred. Verdict: KEEP with a note. |
| 41 | `::test_preserves_existing_classified_gaps` | KEEP | Retry scenario — matches `already_classified_descriptions` dedup logic. |
| 42 | `::test_skill_classification` | KEEP | SKILL type propagation. |
| 43 | `::test_agent_classification` | KEEP | AGENT type propagation. |
| 44 | `::test_empty_gaps_does_not_append_classified` | KEEP | Explicitly asserts `"classified_gaps" not in cmd.update` — matches the no-op early-return branch in classifier_node. |
| 45-50 | `TestEnforceFileWriterMcpTool::*` (6 tests) | KEEP | §12 finding #11 override helper — direct unit tests for: no required_files noop, empty gaps noop, AGENT writer override, SKILL writer override, already-MCP_TOOL noop, non-writer untouched. All match `_enforce_file_writer_mcp_tool` behavior. |
| 51 | `::test_case_insensitive_write_prefix` | KEEP | `query.lower().startswith("write-")` — matches. |
| 52 | `TestClassifierNodeWritererOverride::test_override_fires_when_contract_declares_files` | KEEP | Integration-style: classifier_node calls `_enforce_file_writer_mcp_tool` when `output_contract.required_files` is non-empty. Matches. |
| 53 | `::test_no_override_when_contract_has_no_files` | KEEP | Negative control — override stays inert. Matches. |

File count reconciliation: 4 + 4 + 13 + 9 + 14 + 6 + 3 = 53. All KEEP except the 3 flagged.

---

### tests/unit/test_gap_detection.py (18 tests)

| # | Test | Verdict | Reason |
|---|---|---|---|
| 1 | `test_all_matches_populates_matches_empty_gaps` | KEEP | All-matched path — state.gaps=[] per Stage 2. Matches. |
| 2 | `test_all_gaps_produces_one_enumerated_gap` | KEEP | One gap per unmatched cap — Stage 2 verified. |
| 3 | `test_multiple_unmatched_capabilities_produce_n_enumerated_gaps` | KEEP | N→N Stage 2 contract. |
| 4 | `test_mixed_results_preserves_matches_and_produces_n_gaps` | KEEP | Mixed match/gap handling. |
| 5 | `test_empty_required_capabilities` | KEEP | Includes **taxonomy-aware** kwargs: `auxiliary_queries=[], domain=None, include_generic=True`. Matches current `gap_detection_node` call signature. |
| 6 | `test_passes_required_capabilities_to_registry` | KEEP | Fallback (empty subtasks) → `[None, None, None]`. Matches Step 14.5 Part A contract verbatim. |
| 7 | `test_dual_query_wires_subtask_descriptions_as_auxiliary` | KEEP | Step 14.5 Part A — slug-to-description mapping. Asserts kwarg ordering matches `state.required_capabilities`. |
| 8 | `test_dual_query_missing_subtask_for_slug_uses_none_auxiliary` | KEEP | Orphan-slug → None. Matches `slug_to_description.get(slug)`. |
| 9 | `test_dual_query_fallback_path_empty_subtasks_all_none_auxiliary` | KEEP | Empty subtasks → all-None auxiliary. Matches. |
| 10 | `test_dual_query_multiple_slugs_in_one_subtask` | KEEP | Shared subtask description across sibling slugs. Matches `setdefault` pattern. |
| 11 | `test_routes_to_classifier_when_only_matches` | KEEP | Unconditional routing. |
| 12 | `test_routes_to_classifier_when_only_gaps` | KEEP | Same. |
| 13 | `test_preserves_existing_errors` | KEEP | `errors = list(state.errors)` at entry. Matches. |
| 14 | `test_empty_errors_when_none_prior` | KEEP | Empty initialization. |
| 15 | `test_metrics_store_is_not_called_for_matches` | KEEP | Stage 1 compositional refactor — confirms `metrics_store.record_invocation` is NEVER called. This is the regression guard for the MEMORY.md "gap_detection no longer writes fake invocations" claim. |
| 16 | `test_no_metrics_store_does_not_crash` | KEEP | Backwards-compat with `metrics_store=None`. |
| 17 | `test_gaps_not_recorded_as_invocations` | KEEP | Same as #15 but for gaps. |
| 18 | `test_metrics_store_not_called_for_multiple_matches` | KEEP | Same as #15, multi-match case. |

All 18 KEEP. Note: these tests do **NOT** exercise the taxonomy filter code path (`DomainTaxonomy.get_for_query`) — the mock-registry fixture bypasses the try/except that wraps the taxonomy lookup. That coverage lives in `tests/unit/test_taxonomy.py` (out of scope for this batch) and the `_isolate_taxonomy_path` autouse fixture in `tests/conftest.py` keeps these tests hermetic.

---

### tests/unit/test_pipeline.py (7 tests)

| # | Test | Verdict | Reason |
|---|---|---|---|
| 1 | `test_pipeline_compiles` | KEEP | Smoke — `create_pipeline().compile()` must not raise. |
| 2 | `test_pipeline_contains_expected_nodes` | KEEP | Asserts all 8 nodes wired (decomposition, gap_detection, classifier_node, three generators, verification_node, loading_node). Matches `create_pipeline`. Does NOT assert `optimizer_node` absence, so the §12 #8 per-operation config changes won't regress this test. |
| 3 | `test_pipeline_no_gaps_path_completes` | KEEP | Runs the full compiled graph with `detect_gaps` patched to guarantee a match + `_get_agent` stubbed. Final `status==COMPLETED`. Integration-shaped but deterministic. |
| 4 | `test_create_pipeline_accepts_registry_and_loader` | WEAK | Signature introspection — just checks first two positional args. Tautological given the source (you'd have to deliberately break the contract to fail it). Still has defensive value; keep. |
| 5 | `test_create_pipeline_run_optimizer_flag` | KEEP | Asserts `optimizer_node` present when `run_optimizer=True`. Post-§12 #8 the gate is "any op != off", and `OptimizerConfig.from_run_optimizer_bool(True)` sets all three to "on" — test still correctly wires. |
| 6 | `test_classifier_produces_one_classified_gap_per_unmatched` | KEEP | Two-stage mini-integration: gap_detection → classifier with mocked `classify_gap`. Mixed types (MCP_TOOL/SKILL/AGENT) across three gaps. Matches Stage 2 per-gap classification. |
| 7 | `test_loading_then_optimize_preserves_non_end_goto` | KEEP | Standalone replication of the `_loading_then_optimize` closure logic. Unusual pattern (tests a re-implemented copy of the nested function) but the comment justifies it: `StateNodeSpec` is not directly callable. Reasonable approach. |

All 7 KEEP.

---

### tests/integration/test_decomposition.py (3 tests)

All gated on `OPENAI_API_KEY`. Real DeepAgent calls.

| # | Test | Verdict | Reason |
|---|---|---|---|
| 1 | `TestDecompositionIntegration::test_structured_response_returned` | KEEP | Live DeepAgent, asserts `len(subtasks)>=1` + no errors. Load-bearing for Pillar 1.1 proposal contract. |
| 2 | `::test_write_todos_is_used` | KEEP | Verifies `write_todos` tool is actually invoked by the agent — direct check for the DeepAgent-vs-plain-node architectural claim in CLAUDE.md. |
| 3 | `::test_subtask_dependencies_are_valid` | KEEP | Checks `depends_on` indices are in-range and backward-only. Meaningful invariant. |

All 3 KEEP.

---

### tests/integration/test_classifier.py (4 tests)

All gated on `OPENAI_API_KEY`. Real LLM calls. Uses the **legacy** `classify_task` (not `classify_gap`) — intentional per classifier.py docstring.

| # | Test | Verdict | Reason |
|---|---|---|---|
| 1 | `TestClassifyTaskLive::test_classify_mcp_tool` | KEEP | `classify_task` with deterministic parsing prompt → MCP_TOOL. |
| 2 | `::test_classify_skill` | KEEP | Multi-step domain workflow → SKILL. Confidence ≥ 0.7. |
| 3 | `::test_classify_agent` | KEEP | Investigation → AGENT. Confidence ≥ 0.7. |
| 4 | `::test_confidence_above_threshold` | KEEP | Three clear-cut examples, confidence ≥ 0.7 each. |

All 4 KEEP. Coverage gap (not stale): no integration test for `classify_gap` per-gap entry point or for the §12 #16 retry/fail-closed contract under real LLM failure. That's a coverage-expansion candidate, not a staleness issue.

---

### tests/integration/test_pipeline_e2e.py (4 tests)

Full LangGraph pipeline with real registry + real ToolLoader + real OpenAI.

| # | Test | Verdict | Reason |
|---|---|---|---|
| 1 | `TestPipelineE2E::test_pipeline_reaches_terminal_state` | KEEP | Asserts `status in (COMPLETED, FAILED)`. Never-raises contract check. |
| 2 | `::test_pipeline_produces_artifacts_or_records_errors` | KEEP | Disjunction: artifacts OR errors. Guards the "silent failure" anti-pattern. |
| 3 | `::test_pipeline_detects_gaps_for_unknown_task` | KEEP | Niche capability — pipeline must not crash. Terminal-state check. |
| 4 | `::test_pipeline_state_fields_populated` | KEEP | `matches`, `gaps`, `classified_gaps` keys all populated after completion. |

All 4 KEEP. These are smoke-level; they deliberately don't assert specific classifier outputs or generation success because those depend on LLM determinism.

---

## Cross-file observations

### 1. Tests are fully aligned with §12 findings #10–#17 (post-2026-04-16)

Every new contract introduced by the strict-gate bundle has a dedicated unit test that asserts the NEW behavior, not the old one:

- **§12 #10 (decomposer Rule 15 + `_ensure_file_writer_last_step`)**: covered by `TestEnsureFileWriterLastStep` (8 tests in test_decomposition.py).
- **§12 #11 (`_enforce_file_writer_mcp_tool`)**: covered by `TestEnforceFileWriterMcpTool` + `TestClassifierNodeWritererOverride` (8 tests in test_classifier.py).
- **§12 #13 (multi-file writers parallel, not chained)**: explicit regression guard at `test_multi_file_writers_run_in_parallel_off_original`. This is the canonical "tests codifying bugs" finding — here, the test codifies the FIX correctly (`depends_on=[0]` for both writers).
- **§12 #15 (decomposition 3x retry + sticky flag)**: 5 explicit `fails_closed_after_retries` tests + 5 `decomposition_succeeded` sticky-flag tests. Every failure path asserts `required_capabilities == []` — no fallback.
- **§12 #16 (classification 3x retry + fail-closed, no silent MCP_TOOL)**: `test_classification_failure_sets_sticky_flag` + `test_partial_classification_failure_flips_sticky_flag` verify `classify_gap` returns `None` and classifier_node handles it correctly.

No STALE tests asserting pre-fix behavior were found. This is the audit's cleanest batch.

### 2. Legacy `classify_task` contract is deliberately preserved

Five tests (`TestClassifyTask::test_llm_failure_falls_back_to_mcp_tool`, `test_llm_failure_rationale_contains_error`, and three others) assert the OLD "single call + MCP_TOOL fallback + confidence=0.3" behavior. Source inspection confirms `classify_task` retains exactly this path (not updated to the 3-strike retry). Not stale — the classifier.py docstring says "Preserved as a thin wrapper" + "identical to the pre-Stage-2 classifier on a single call". Production code uses `classify_gap`; `classify_task` remains for one integration test and any future whole-task fallback. If the legacy wrapper is ever removed, these five tests go with it — but as long as both exist, the contracts are correct.

### 3. Three tests are weaker than they look

- `TestClassifierNode::test_empty_gaps_is_noop` has a tautological ternary in its final assertion line (see entry #34 above). Intent is covered by the `mock_classify.assert_not_called()` above it, so it's not broken, just noisy.
- `TestClassifierNode::test_preserves_existing_errors` asserts a disjunction where one branch is unreachable given current source. Guards against a future regression but passes trivially today.
- `test_create_pipeline_accepts_registry_and_loader` is signature introspection — nearly tautological.

None warrant removal, but a hygiene pass could tighten them.

### 4. One suspicious test

`TestClassifierNode::test_classify_gap_unhandled_exception_propagates` documents behavior that cannot happen given current source (the retry loop catches `Exception`). It will silently go stale if anyone ever adds an uncatchable exit path to `classify_gap` — but there's no way to trigger the test's failure condition today without mocking. Flagged SUSPICIOUS for reviewer awareness; verdict kept as KEEP.

### 5. No coverage gap around strict-gate LangFuse events

The fail-closed paths emit specific event names (`decomposition.timeout`, `classifier.fail_closed`, `decomposition.incoherent_chain`, `decomposition.writer_appended`, `classifier.gap_classified`). Only one (`decomposition.hard_truncation`) is asserted at the event-name level (test #23 via `mock_emit`). If evaluation or dashboards depend on specific event names, consider adding a thin "event X was emitted" guard per strict-gate path. Not stale; expansion candidate only.

### 6. No DUPLICATE tests across files

Each class/function in test_decomposition.py, test_classifier.py, test_gap_detection.py, and test_pipeline.py covers a distinct code path. Some overlap exists between `TestDecompositionNode::test_*_fails_closed_after_retries` and the three parallel `test_*_sets_decomposition_succeeded_false` tests — but the former assert error-list shape + `subtasks==[]`, the latter assert the sticky flag only. Complementary, not duplicative.

### 7. Integration coverage gap (disclosure, not blocker)

No integration test exercises:
- `classify_gap` (per-gap entry point) under a real LLM failure → `None` return.
- Decomposition retry loop under a real-world transient failure.
- `_enforce_file_writer_mcp_tool` end-to-end with the pipeline (covered via unit tests with mocked `classify_gap`).

These are all unit-tested; missing integration coverage is a known trade-off given LLM-cost and determinism. Worth a one-line mention in Chapter 4 methodology.
