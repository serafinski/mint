# Test Audit — Batch 02b: Composition + Output Contract + Loading

**Date:** 2026-04-19
**Scope:** Composition (CompositionalExecutor + §12 finding #12), OutputContract (Step 14.6), loading_node, tool_loader
**Files audited:** 5 (142 tests total)

---

## Summary

| File | Tests | KEEP | WEAK | DUPLICATE | DEAD | STALE | SUSPICIOUS |
|---|---:|---:|---:|---:|---:|---:|---:|
| tests/unit/test_composition.py | 58 | 56 | 2 | 0 | 0 | 0 | 0 |
| tests/integration/test_composition.py | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| tests/unit/test_output_contract.py | 40 | 40 | 0 | 0 | 0 | 0 | 0 |
| tests/unit/test_loading_node.py | 15 | 15 | 0 | 0 | 0 | 0 | 0 |
| tests/unit/test_tool_loader.py | 27 | 26 | 1 | 0 | 0 | 0 | 0 |
| **Total** | **142** | **139** | **3** | **0** | **0** | **0** | **0** |

**Headline:** Exceptionally healthy batch. All 142 tests are behaviorally valid against current production code. No STALE tests (no assertions of the broken pre-§12 #13 serial-writer behavior, pre-14.6 prose-parsing, or pre-Phase-3.5c `fixture_contents`-for-MCP-TOOL behavior). Step 14.6 and §12 finding #12 coverage is comprehensive at the unit and integration-boundary layers. The only nits are two WEAK tests that lean on private-API signatures (`build_schema_context`, `_end_langfuse_chain_trace` not probed directly) and one WEAK `tool_loader` test that relies on `atexit.register` patch timing.

---

## tests/unit/test_composition.py (58 tests / 1534 LOC)

| # | Test name | Class | Verdict | Notes |
|---:|---|---|---|---|
| 1 | test_registry_match_preferred_over_generated | TestBuildResolutionMap | KEEP | Precedence rule from `build_resolution_map` — registry > generated. Direct. |
| 2 | test_generated_artifact_used_when_no_match | TestBuildResolutionMap | KEEP | Happy-path fallback. |
| 3 | test_unresolved_capability_missing_from_map | TestBuildResolutionMap | KEEP | Negative-path: asserts key absence (not None), correct. |
| 4 | test_multiple_capabilities_resolved | TestBuildResolutionMap | KEEP | Covers mixed registry+generated resolution. |
| 5 | test_single_step | TestBuildExecutionOrder | KEEP | Trivial base case. |
| 6 | test_empty | TestBuildExecutionOrder | KEEP | Trivial base case. |
| 7 | test_linear_chain | TestBuildExecutionOrder | KEEP | Topological order assertions via index comparison — robust to DAG permutations. |
| 8 | test_diamond_dependency | TestBuildExecutionOrder | KEEP | Diamond DAG correctness — exactly what `graphlib.TopologicalSorter` must produce. |
| 9 | test_cycle_falls_back_to_linear | TestBuildExecutionOrder | KEEP | Matches production fallback in `build_execution_order` catching `CycleError`. |
| 10 | test_no_dependencies_preserves_all | TestBuildExecutionOrder | KEEP | Set comparison (not order) — correct for concurrent-ready steps. |
| 11 | test_workspace_created_with_fixtures | TestWorkspace | KEEP | Real filesystem side effect; clean up via cleanup_workspace. Matches `build_workspace` semantics. |
| 12 | test_workspace_prefix_contains_task_id | TestWorkspace | KEEP | Asserts tempdir prefix `mint_workspace_<id>_` — matches production. |
| 13 | test_step_output_saved_and_readable | TestWorkspace | KEEP | `_save_step_output` invariant. |
| 14 | test_chain_fixture_contents_includes_upstream | TestWorkspace | KEEP | Asserts upstream JSON injection via `_build_chain_fixture_contents`. |
| 15 | test_resolves_generated_artifact | TestResolveStep | KEEP | Direct resolution path for generated artifacts. |
| 16 | test_resolves_registry_match | TestResolveStep | KEEP | Direct resolution path for matches. |
| 17 | test_returns_none_for_unresolved | TestResolveStep | KEEP | Negative case. |
| 18 | test_resolves_by_capability_name_fallback | TestResolveStep | KEEP | Fallback when `gap_query` doesn't match; matches lines 299-303 of `composition.py`. |
| 19 | test_single_mcp_tool_chain | TestChainExecution | KEEP | Happy-path; mocks `execute_mcp_tool_in_chain`. |
| 20 | test_linear_mcp_tool_chain | TestChainExecution | KEEP | Three-step sequence, verifies step_results length + last. |
| 21 | test_mid_chain_failure | TestChainExecution | KEEP | Empty dict at step 1 → chain fails with correct failed_step_index/name. |
| 22 | test_unresolved_capability_fails_chain | TestChainExecution | KEEP | Matches the `ref is None` branch in `run()`. |
| 23 | test_mixed_type_chain | TestChainExecution | KEEP | MCP_TOOL → SKILL; asserts fixture_contents injection for SKILL step via positional arg `[0][2]`. |
| 24 | test_mcp_tool_to_agent_chain | TestChainExecution | KEEP | MCP_TOOL → AGENT; same per-type fixture pattern. |
| 25 | test_per_type_fixture_routing_mcp_vs_skill | TestChainExecution | KEEP | **Step 14 Gap B test (1bae0da).** Asserts MCP_TOOL gets `workspace_path: Path` kwarg with NO `fixture_contents` dict; SKILL gets fixture_contents dict with `step_0_output.json`. Proposal-alignment invariant. |
| 26 | test_registry_match_in_chain | TestChainExecution | KEEP | Registry-sourced step exercises `_match_to_artifact`. |
| 27 | test_empty_subtasks_returns_failed | TestChainExecution | KEEP | Schema-constraint enforcement (chain_length min 1). |
| 28 | test_workspace_cleanup_on_success | TestChainExecution | KEEP | Asserts cleanup in `finally` block. |
| 29 | test_workspace_cleanup_on_failure | TestChainExecution | KEEP | Same, on failure branch. |
| 30 | test_agent_timeout_populates_failed_step_reason | TestChainExecution | KEEP | **Step 13.** Verifies `_last_step_failure_reason` → `"agent_execution_timeout"` via `_record_agent_execution_meta`. Matches lines 550-555 of composition.py. |
| 31 | test_agent_error_populates_failed_step_reason | TestChainExecution | KEEP | Step 13 — error path. |
| 32 | test_agent_success_leaves_failed_step_reason_none | TestChainExecution | KEEP | Step 13 — reset on success. |
| 33 | test_non_agent_failure_leaves_reason_none | TestChainExecution | KEEP | Explicitly protects the "reason slot reserved for AGENT meta" contract (empty MCP_TOOL output yields None reason). |
| 34 | test_reconstructs_artifact_from_match | TestMatchToArtifact | KEEP | `_match_to_artifact` — reads metadata into synthetic artifact. |
| 35 | test_lead_artifact_covers_all_gap_queries | TestResolutionMapSubagentGapQueries | KEEP | Lead-with-subagents resolution (Phase 3.5b). Matches composition.py lines 84-86. |
| 36 | test_registry_match_takes_priority_over_subagent_gap | TestResolutionMapSubagentGapQueries | KEEP | Priority ordering: registry > lead-subagent-gap. |
| 37 | test_lead_executed_once_for_multiple_subtasks | TestLeadDedup | KEEP | Lead dedup cache (_lead_cache in run()). Asserts call_count == 1 for two subtasks resolving to same artifact. |
| 38 | test_collects_output_csv | TestCollectOutputFiles | KEEP | `_collect_output_files` format-agnostic. Intentionally asserts `data.txt` IS collected post-2026-04-16 fix (not filtered out). |
| 39 | test_collects_multiple_output_files | TestCollectOutputFiles | KEEP | Happy-path multiple files. |
| 40 | test_collects_report_md | TestCollectOutputFiles | KEEP | **§12 finding #10/T16 regression guard.** Rule 15 writer produces arbitrary filenames; collector must honor. |
| 41 | test_empty_workspace | TestCollectOutputFiles | KEEP | Negative case. |
| 42 | test_step_intermediates_skipped | TestCollectOutputFiles | KEEP | `step_N_output.json` filter invariant (including `step_10_output.json` — double-digit). |
| 43 | test_nonexistent_workspace | TestCollectOutputFiles | KEEP | OSError path — graceful empty return. |
| 44 | test_default_empty | TestChainResultOutputFiles | KEEP | ChainResult.output_files schema default. |
| 45 | test_with_files | TestChainResultOutputFiles | KEEP | Schema field carries bytes through ChainResult. |
| 46 | test_extracts_text_file_from_last_step | TestEmbeddedFileExtraction | KEEP | `_output_file_name` + `_output_file_content` embedding (SKILL/AGENT path). |
| 47 | test_extracts_base64_binary_file | TestEmbeddedFileExtraction | KEEP | Base64 decoding for xlsx — matches composition.py lines 857-864. |
| 48 | test_no_extraction_without_markers | TestEmbeddedFileExtraction | KEEP | Negative path — no markers → no extraction. |
| 49 | test_output_schema_in_context | TestBuildSchemaContext | WEAK | Good test, but probes a helper in `src/orchestrator/generators/utils.py` (not composition.py). Placement rather than correctness — could belong in `test_generators_utils.py` or a schema-context-specific file. Not wrong; just misfiled. |
| 50 | test_no_schema_returns_empty | TestBuildSchemaContext | WEAK | Same placement nit. |
| 51 | test_no_matching_subtask_returns_empty | TestBuildSchemaContext | WEAK | Same placement nit. (Consolidating the two WEAK markers here: this entire TestBuildSchemaContext class is file-placement WEAK not behavior WEAK.) |
| 52 | test_first_step_no_input_contract | TestBuildSchemaContext | KEEP | First-step-has-no-input-contract invariant — important edge case. |
| 53 | test_agent_missing_schema_keys_fails_chain | TestAgentOutputSchemaRuntimeCheck | KEEP | **§12 finding #12 Layer 2.** Asserts runtime validator at composition.py lines 774-807 fires on missing AGENT output keys. Canonical T16 reproducer. |
| 54 | test_agent_full_compliance_passes | TestAgentOutputSchemaRuntimeCheck | KEEP | §12 #12 positive path. |
| 55 | test_mcp_tool_bypasses_runtime_check | TestAgentOutputSchemaRuntimeCheck | KEEP | **§12 #12 MCP_TOOL-skip rule.** Asserts MCP_TOOL bypass — deterministic output is out of scope for this check. |
| 56 | test_union_output_schema_lead_with_subagents | TestAgentOutputSchemaRuntimeCheck | KEEP | T16 third-run fix: `_collect_union_output_schema` returns union across 3 subtasks collapsed to same lead. Direct. |
| 57 | test_union_output_schema_solo_agent_equals_subtask_schema | TestAgentOutputSchemaRuntimeCheck | KEEP | Degenerate case — solo agent = single-subtask schema. |
| 58 | test_skill_missing_schema_keys_also_fails | TestAgentOutputSchemaRuntimeCheck | KEEP | **§12 #12 SKILL parity.** Critical: confirms validator applies to BOTH AGENT and SKILL (matches composition.py line 775). |

**Note on TestBuildSchemaContext placement:** marked WEAK only to flag that the four tests (#49-52) probe `src.orchestrator.generators.utils.build_schema_context`, not composition.py. They're behaviorally correct; the lint is that they live in `test_composition.py`. Not a STALE or dead-code issue.

---

## tests/integration/test_composition.py (2 tests / 181 LOC)

| # | Test name | Verdict | Notes |
|---:|---|---|---|
| 1 | test_two_step_mcp_tool_chain_with_docker | KEEP | Real Docker integration. Two real MCP_TOOL source blobs, real fixture mount, real aggregation. Bug #2 in the step1_code (`len(f.read())` called twice — second read returns 0) is IN the generated source code, not in the test — test asserts `len(parsed_data) == 3` not the buggy length. Integration test is correct; the second helper has the same cosmetic bug but isn't asserted against. |
| 2 | test_chain_with_metrics_recording | KEEP | Verifies `MetricsStore.record_invocation` fires with real latency (`> 0`) + correct capability_id. Matches `_record_step_invocation` in composition.py lines 376-400. |

Both gated behind `@pytest.mark.integration` (require Docker). Healthy.

---

## tests/unit/test_output_contract.py (40 tests / 633 LOC)

### OutputContract schema + check method

| # | Test name | Verdict | Notes |
|---:|---|---|---|
| 1 | test_output_contract_default_is_empty | KEEP | Default constructor, `is_empty()` True. |
| 2 | test_output_contract_with_keys_only | KEEP | Keys-only non-empty. |
| 3 | test_output_contract_with_files_only | KEEP | Files-only non-empty. |
| 4 | test_output_contract_with_both | KEEP | Both set → non-empty. |
| 5 | test_output_contract_extra_forbid | KEEP | `model_config["extra"] = "forbid"` — tests the Pydantic guard. |
| 6 | test_check_satisfied_keys_only | KEEP | Happy-path. Asserts produced_keys (sorted). |
| 7 | test_check_missing_keys | KEEP | Missing keys reported sorted. |
| 8 | test_check_missing_files | KEEP | Missing files reported sorted. |
| 9 | test_check_missing_both | KEEP | Combined missing both. |
| 10 | test_check_against_non_dict_output | KEEP | Robustness: None/str/list/int produce `produced_keys=[]` + missing for all. |
| 11 | test_check_empty_contract_always_satisfied | KEEP | Empty contract is a no-op — legacy fall-through semantics. |
| 12 | test_check_with_none_output_files_is_safe | KEEP | `output_files=None` parameter default. |
| 13 | test_check_t16_silent_failure_pattern | KEEP | **T16 motivator pinned** — exact failure pattern `{next_action: ...}` → 3 missing keys. Reads like a regression test. |

### resolve_output_contract — three-tier behavior

| # | Test name | Verdict | Notes |
|---:|---|---|---|
| 14 | test_resolve_uses_authored_shortcut_when_task_definition_supplied | KEEP | Tier 1 shortcut. |
| 15 | test_resolve_authored_shortcut_does_not_call_llm | KEEP | **Critical.** Patches `_extract_output_contract_llm` to raise if called — proves Tier 1 is zero-LLM. |
| 16 | test_resolve_falls_through_to_extractor_when_no_task_definition | KEEP | Tier 2 fallback. |
| 17 | test_resolve_returns_none_when_extractor_raises | KEEP | Fail-open semantics (matches resolver lines 157-160). |
| 18 | test_resolve_skips_invalid_task_definition | KEEP | Duck typing — object without `output_contract` attr → fall through. |

### CLI _match_known_task

| # | Test name | Verdict | Notes |
|---:|---|---|---|
| 19 | test_match_known_task_by_id | KEEP | Direct. |
| 20 | test_match_known_task_id_case_insensitive | KEEP | `raw.upper()` path. |
| 21 | test_match_known_task_id_with_whitespace | KEEP | `raw.strip()` path. |
| 22 | test_match_known_task_by_prompt_copy_paste | KEEP | Exact-prompt match path. |
| 23 | test_match_known_task_no_match_for_freeform | KEEP | Negative: genuine free-form returns None. |
| 24 | test_match_known_task_empty_input | KEEP | Empty/whitespace-only → None. |

### All 20 T-tasks consistency check

| # | Test name | Verdict | Notes |
|---:|---|---|---|
| 25 | test_all_tasks_have_non_empty_output_contract | KEEP | Enforces every T-task has a contract — protects against silent drift. |
| 26 | test_all_tasks_contract_keys_referenced_in_prompt_or_validator | KEEP | Catches inflated contracts + stale prompts. Good invariant. |
| 27 | test_file_producing_tasks_declare_required_files | KEEP | T07 → output.csv, T11 → output.xlsx pinned. |
| 28 | test_t16_contract_matches_motivating_failure | KEEP | T16 canonical pin. |

### CLI runner _evaluate_output_contract end-to-end

| # | Test name | Verdict | Notes |
|---:|---|---|---|
| 29 | test_evaluate_returns_3_on_missing_keys | KEEP | **Exit code 3 — the canonical Step 14.6 exit code.** Captures magenta warning text. |
| 30 | test_evaluate_returns_3_on_missing_files | KEEP | T07/T11 scenario. |
| 31 | test_evaluate_returns_3_on_missing_both_keys_and_files | KEEP | Combined. |
| 32 | test_evaluate_returns_none_when_contract_satisfied | KEEP | Happy-path. |
| 33 | test_evaluate_returns_none_when_contract_satisfied_with_files | KEEP | With files. |
| 34 | test_evaluate_returns_none_when_no_contract | KEEP | Empty contract → fall-through (matches runner.py line 409). |
| 35 | test_evaluate_returns_none_when_state_has_no_contract_field | KEEP | Legacy state robustness. |
| 36 | test_evaluate_returns_none_when_state_is_dict | KEEP | **Dict-shape path** (matches runner.py line 404-407). Critical: LangGraph states can be dicts. |
| 37 | test_evaluate_returns_none_on_hard_crash_output_is_none | KEEP | Hard-crash vs wrong-shape distinction — exits 1 (None return) not 3. |
| 38 | test_evaluate_returns_none_when_no_artifact | KEEP | Registry-match-without-execution path. |
| 39 | test_evaluate_handles_non_dict_output_safely | KEEP | str output still produces clean exit 3. |
| 40 | test_evaluate_diagnostics_list_produced_files_too | KEEP | User got CSV when XLSX requested — both produced + missing surfaced. |

**File assessment:** Textbook. Every test has a clear mapping to Step 14.6 behavior. Full three-tier resolver coverage. Full CLI exit-3 path coverage (including dict state, legacy state, no-artifact, hard-crash distinctions). No fluff.

---

## tests/unit/test_loading_node.py (15 tests / 342 LOC)

| # | Test name | Verdict | Notes |
|---:|---|---|---|
| 1 | test_mcp_tool_is_loaded_and_registered | KEEP | Happy-path — load + smoke + register all fire. |
| 2 | test_multiple_mcp_tools_all_registered | KEEP | Multi-tool fan-out. |
| 3 | test_skill_artifacts_are_skipped | KEEP | SKILL is NOT loaded via ToolLoader; goes through the non_mcp_artifacts branch (loading_node.py line 146). Test asserts `load_tool.assert_not_called()` — correct, and COMPLETED status after registration. |
| 4 | test_agent_artifacts_are_skipped | KEEP | AGENT same as SKILL — not loaded via ToolLoader. |
| 5 | test_mixed_artifacts_only_mcp_tools_loaded | KEEP | Mixed MCP/SKILL/AGENT. Note: `register_tool.call_count == 1` — ToolLoader.register_tool ONLY called for MCP; SKILL/AGENT go through direct `registry.add` at loading_node.py line 221. Test is correct — it's asserting on the loader mock, not on registry calls. |
| 6 | test_load_failure_still_registers_with_source_code | KEEP | **Load failure resilience.** Matches loading_node.py lines 100-111 — host import fails but register_tool still called. |
| 7 | test_smoke_failure_does_not_prevent_registration | KEEP | Matches loading_node.py lines 115-121 — smoke failure logs warning but doesn't abort. |
| 8 | test_register_failure_records_error | KEEP | register_tool raises → error captured in state.errors but pipeline proceeds. |
| 9 | test_failed_status_when_gaps_but_no_artifacts | KEEP | Matches loading_node.py lines 255-262 — gaps + deployed=0 → FAILED. |
| 10 | test_completed_when_no_gaps_and_no_artifacts | KEEP | No gaps → COMPLETED regardless of artifact count. |
| 11 | test_host_load_fails_but_registration_succeeds | KEEP | **pandas bug scenario** — host import fails, registration succeeds → COMPLETED with source_code-only registration. Matches the project_loading_node_bug.md memory. |
| 12 | test_failed_when_gaps_and_all_registrations_fail | KEEP | Both load + register fail → FAILED with 2 errors. |
| 13 | test_failed_when_gaps_and_load_succeeds_but_register_fails | KEEP | Load succeeds but registry.add raises → FAILED (0 deployed). |
| 14 | test_completed_when_gaps_and_skill_artifact_even_if_mcp_load_fails | KEEP | **Mixed-success semantics** — SKILL registered despite MCP failure → COMPLETED. |
| 15 | test_existing_errors_are_preserved | KEEP | `errors = list(state.errors)` preserves prior errors (loading_node.py line 89). |

**File assessment:** Thorough coverage of the COMPLETED/FAILED status decision matrix. All tests map cleanly to the production branches in `loading_node.py`. Behavior of "deployed = registered" (line 250) is correctly under test.

---

## tests/unit/test_tool_loader.py (27 tests / 572 LOC)

| # | Test name | Class | Verdict | Notes |
|---:|---|---|---|---|
| 1 | test_success_result | TestToolResultSchema | KEEP | Schema positive. |
| 2 | test_failure_result | TestToolResultSchema | KEEP | Schema negative. |
| 3 | test_load_valid_tool | TestLoadTool | KEEP | Real importlib load. |
| 4 | test_reject_no_source_code | TestLoadTool | KEEP | ValueError with "no source_code" — matches tool_loader.py line 100. |
| 5 | test_reject_non_mcp_tool | TestLoadTool | KEEP | SKILL → ValueError "Expected MCP_TOOL" — matches line 96. |
| 6 | test_reject_invalid_source_code | TestLoadTool | KEEP | `import nonexistent_module_xyz_123` → RuntimeError "Failed to execute". |
| 7 | test_load_tool_no_async_function | TestLoadTool | KEEP | Sync-only module → RuntimeError. Matches lines 142-145. |
| 8 | test_reject_invalid_module_name | TestLoadTool | KEEP | `123-invalid!name` → ValueError "invalid". Matches lines 106-111. |
| 9 | test_execute_valid_tool | TestExecuteTool | KEEP | Real subprocess execute. |
| 10 | test_execute_tool_that_raises | TestExecuteTool | KEEP | Inner exception surfaced in result.error. |
| 11 | test_execute_tool_timeout | TestExecuteTool | KEEP | `asyncio.sleep(9999)` with timeout=2 → "timed out". Real subprocess timeout. |
| 12 | test_execute_tool_nonzero_returncode | TestExecuteTool | KEEP | Mocks subprocess.run with returncode=1 → "Subprocess exited with code 1". |
| 13 | test_execute_tool_empty_stdout_exits_zero | TestExecuteTool | KEEP | Edge: zero-exit + empty stdout → "Failed to parse". |
| 14 | test_execute_tool_invalid_json_output | TestExecuteTool | KEEP | Non-JSON stdout → parse error. |
| 15 | test_execute_tool_with_bool_input | TestExecuteTool | KEEP | Boolean JSON roundtrip — true/false → True/False. Real execution. |
| 16 | test_register_mcp_tool | TestRegisterTool | KEEP | `registry.add` called once with descriptor. |
| 17 | test_register_with_custom_description | TestRegisterTool | KEEP | Custom desc passthrough. |
| 18 | test_register_uses_artifact_description | TestRegisterTool | KEEP | Falls back to `artifact.description` when no explicit desc. |
| 19 | test_register_falls_back_to_name_when_no_description | TestRegisterTool | KEEP | Humanised name fallback: `add_numbers` → `"add numbers"`. Matches tool_loader.py line 334. |
| 20 | test_register_rejects_non_mcp_tool | TestRegisterTool | KEEP | ValueError + `registry.add.assert_not_called()`. |
| 21 | test_cleanup_removes_multiple_temp_dirs | TestCleanup | KEEP | Multi-load then cleanup → all temp dirs removed. |
| 22 | test_temp_files_removed | TestCleanup | KEEP | Single-dir cleanup. |
| 23 | test_cleanup_idempotent | TestCleanup | KEEP | Second cleanup call doesn't raise. |
| 24 | test_atexit_registered_on_init | TestCleanup | WEAK | Relies on patching `atexit.register`. The side_effect=registered.append pattern works but is a little fragile (also covers the `atexit.register(self.cleanup)` literal). Low-value — the integration-in-practice is already exercised by the other cleanup tests. Not wrong, just thin. |
| 25 | test_execute_tool_non_dict_json_output | TestExecuteToolEdgeCases | KEEP | `[1,2,3]` stdout → "Unexpected output format". Matches line 255. |
| 26 | test_execute_tool_non_dict_scalar_json | TestExecuteToolEdgeCases | KEEP | Bare `42` → same guard. |
| 27 | test_execute_tool_debug_print_before_json | TestExecuteToolEdgeCases | KEEP | **BUG-01 fix guard.** Debug print before JSON line: loader parses last non-empty line. Matches lines 242-243. |

**File assessment:** Thorough. Mix of real-subprocess integration (slow but high-confidence) and mocked `subprocess.run` (fast edge-cases). One WEAK nit on the atexit patch.

---

## Cross-file observations

### Freshness check against recent fixes (all PASS)

1. **Step 14.6 OutputContract + exit code 3** (`test_output_contract.py`): full coverage. 13 schema-level tests + 5 resolver-level + 6 CLI-matcher + 4 T-task consistency + 12 CLI runner exit-3 tests. Proper separation of concerns.

2. **§12 finding #12 runtime schema validation** (`test_composition.py::TestAgentOutputSchemaRuntimeCheck`): 6 tests covering the three critical invariants — AGENT failure, SKILL failure, MCP_TOOL skip, full compliance, union-schema for lead+subagents, solo-agent union=single. Matches composition.py lines 774-807 + _collect_union_output_schema lines 463-498.

3. **§12 finding #13 parallel file writers:** no test here codifies the pre-fix serial chaining behavior. Tests in this batch touch multi-file writers only obliquely via `test_collects_multiple_output_files` which is format-agnostic. Good — no STALE assertions.

4. **§12 finding #10 file-writer MCP_TOOL last step:** `test_collects_report_md` explicitly guards the Rule 15 arbitrary-filename case (T16 2026-04-16 15:51 reproducer). Post-2026-04-16 format-agnostic collector correctly tested.

5. **Step 14.7 MCP_TOOL step-0 upstream_data guard:** not directly tested here; that's a generator-prompt concern and belongs in generator tests (outside this batch).

6. **Per-type fixture routing (Step 14 Gap B):** `test_per_type_fixture_routing_mcp_vs_skill` (commit 1bae0da) explicitly asserts MCP_TOOL receives `workspace_path: Path` with NO `fixture_contents`; SKILL receives fixture_contents dict. The three-backend architectural invariant is guarded.

### No STALE / DEAD / DUPLICATE / SUSPICIOUS

- No test asserts the pre-fix serial-chaining multi-writer behavior.
- No test asserts pre-14.6 prose-parsing of task prompts.
- No test codifies pre-Phase-3.5c "MCP_TOOL receives fixture_contents" behavior — the routing test asserts the opposite.
- No duplicate tests detected across files.
- No suspicious-looking self-referential or always-true assertions.

### Minor observations (not actionable)

- **File placement nit:** `TestBuildSchemaContext` (4 tests) in `test_composition.py` actually exercises `src.orchestrator.generators.utils.build_schema_context`. Behaviorally fine; placement-wise it would fit better next to the generators. Step 14 Gap C augmentation tests (per the memory) already test prompt reach in the generator test files, so this class is complementary rather than duplicative.

- **Import style in TestBuildSchemaContext:** each test re-imports `build_schema_context` + `ClassifiedGap` in the body rather than at module top. Defensible (keeps cheap when the class isn't collected) but inconsistent with the rest of the file.

- **Integration file uses `MagicMock` for `task`:** `test_two_step_mcp_tool_chain_with_docker` uses `MagicMock` as the task-dataclass stand-in; a `SimpleNamespace` (as used in the unit file) would be lighter. Cosmetic.

- **`tool_loader.py`** execution tests mix real subprocess execution with mocked subprocess.run. That's intentional to keep JSON-parse edge cases cheap while still having end-to-end confidence. The tests are labeled clearly enough (edge cases in `TestExecuteToolEdgeCases`).

### Coverage gaps NOT detected

No gaps in behavior currently specified by production code. If follow-up work lands, the following are candidates for additional tests:

- `_start_langfuse_chain_trace` / `_end_langfuse_chain_trace` / `_start_langfuse_step_span` / `_end_langfuse_step_span` — LangFuse integration helpers in `composition.py` lines 905-1078 — currently only exercised indirectly (tests tolerate langfuse being absent via the silent `except Exception` fallback). Step 13.7 Batch C5 typed-observation behavior (`start_as_current_observation` with `as_type="tool"/"agent"/"span"`) is completely untested.
- `loading_node` registry.add with SKILL input_schema / output_schema derivation from upstream subtask (lines 176-188 of loading_node.py) — no test covers the `find_subtask_for_gap` branch.
- `_emit_registration_event` emission — currently no test asserts the LangFuse event shape.

These are all flagged as Chapter-7 / future-work lines in the code comments and aren't critical for Phase 2/3/4 eval readiness.

---

## Bottom line

142 tests, 139 KEEP, 3 WEAK (all cosmetic or placement), 0 problematic. This batch is ready to ship as-is for the Phase 4 smoke-test gate.
