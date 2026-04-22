# Batch 06 — CLI + Runner + Execution + Tracing Audit (2026-04-19)

## Summary

**Scope:** 6 files / 155 tests / 2,378 LOC covering CLI settings + Typer config/feedback subcommands, `--allow-network` plumbing (Step 14.12), `mint reset` data-wiping, runner progress display, SKILL/AGENT execution backends (Step 13), and LangFuse tracing layer (Step 13.7).

**Verdict distribution:**

| Verdict | Count | Share |
|---|---|---|
| KEEP | 146 | 94.2% |
| WEAK | 5 | 3.2% |
| DUPLICATE | 2 | 1.3% |
| SUSPICIOUS | 2 | 1.3% |
| DEAD / STALE | 0 | 0% |

**Headline findings:**

1. **Every 2026-04-19 new test is load-bearing.** The 9 new CliRunner tests in `test_cli_settings.py` fill a real gap (the deleted smoke script migrated to hermetic CliRunner). The 4 new `TestTraceIdFallback` tests in `test_tracing.py` guard Fix #4's trace-attach-without-wrapper-SPAN semantics. The `TestOtelAwareWrapperSpan` suite guards Fix #2. All genuinely new coverage.
2. **The `test_cli_reset.py` fixture-path regression is a canonical "tests codifying bugs" fossil**, now properly remediated with an explicit regression-guard test (`test_reset_deletes_last_run_at_correct_path`). Excellent example of the memory lesson applied in-test.
3. **One minor duplicate** in `test_execution.py`: `TestExecuteAgentArtifactMeta::test_exception_in_inner_records_error` + `TestExecuteAgentArtifact::test_returns_empty_on_exception` are the same assertion on different attributes — kept because they target different invariants (meta side-effect vs return value), but flagged.
4. **Two SUSPICIOUS tests** in `test_execution.py`: `test_fixture_does_not_clobber_skill_with_same_name` is testing a `dict.update` call-order side effect that's plausibly fragile, and `test_empty_schema_returns_none` + `test_builds_model_with_declared_keys` accept that AGENT output_schema models use `extra="allow"` — this is documented in CLAUDE.md as §12 finding #12 decision but worth flagging for future reviewers who might want stricter enforcement.
5. **`test_cli_allow_network.py::test_allow_network_flag_lands_in_task_state` is the test that triggered the defensive `_save_last_run` parent `mkdir`** — the test body does not actually construct a `data/` directory but runs through `execute_task`, which calls `_save_last_run` first. Without the defensive mkdir, CI runs in a fresh checkout with no pre-existing `data/cli/` would crash. Confirmed via `src/cli/runner.py:70` (`path.parent.mkdir(parents=True, exist_ok=True)`).
6. **Tracing tests exercise the three critical distributed-tracing failures** — ContextVars not propagating across ThreadPoolExecutor, SDK v3 `arrayValue` empty-list bug, OTel parent-span detection for wrapper elision. Each has a focused behavioral assertion; no WEAK smoke tests.
7. **Per-type fixture routing invariant (CLAUDE.md three-backend architecture)** is asserted at execution-layer granularity by `TestAgentInvokeInput` (AGENT → workspace files dict with absolute-virtual-path keys). The complementary MCP_TOOL-side invariant (workspace_path kwarg + no fixture_contents dict) is asserted in `test_composition.py::test_per_type_fixture_routing_mcp_vs_skill` (see Step 14 behavioral-audit Gap B) — this batch covers the AGENT half exhaustively.

---

## Per-file tables

### `tests/unit/test_cli_settings.py` — 35 tests

| # | Test | Verdict | Notes |
|---|---|---|---|
| 1 | `TestLoadSettings::test_missing_file_returns_empty` | KEEP | Core contract: absent file → `{}`. Matches `src/cli/settings.py:55`. |
| 2 | `TestLoadSettings::test_valid_file_loads` | KEEP | Round-trip read of valid JSON. |
| 3 | `TestLoadSettings::test_malformed_json_returns_empty` | KEEP | Never-raises contract: malformed JSON → `{}`. `src/cli/settings.py:64-68`. |
| 4 | `TestLoadSettings::test_non_object_returns_empty` | KEEP | JSON arrays/primitives rejected. `src/cli/settings.py:69-73`. |
| 5 | `TestSaveSettings::test_round_trip` | KEEP | Integration: save → load equality. |
| 6 | `TestSaveSettings::test_creates_parent_directory` | KEEP | Guards `path.parent.mkdir(parents=True, exist_ok=True)` at `settings.py:92`. |
| 7 | `TestSaveSettings::test_overwrites_existing` | KEEP | `os.replace` atomic-replace semantics verified. |
| 8 | `TestSaveSettings::test_atomic_write_leaves_no_temp_files` | KEEP | Guards the mkstemp + rename protocol (no `.settings.json.*` leftovers). |
| 9 | `TestGetOptimizerConfigFromSettings::test_empty_settings_returns_empty` | KEEP | Defaults for fresh install. |
| 10 | `test_missing_optimizer_key` | KEEP | Non-optimizer settings pass through safely. |
| 11 | `test_extracts_optimizer_dict` | KEEP | Happy path extraction. |
| 12 | `test_filters_unknown_operations` | KEEP | Guards the allow-list at `settings.py:128-131` (rejects keys outside prune/split/merge). |
| 13 | `test_filters_non_string_values` | KEEP | Type-filter on values (42 → dropped). |
| 14 | `test_non_dict_optimizer_returns_empty` | KEEP | Corruption-resistance. |
| 15 | `TestGetNetworkAllowFromSettings::test_empty_settings_returns_none` | KEEP | Step 14.12 tri-state: None = "no preference". |
| 16 | `test_missing_network_key_returns_none` | KEEP | Coexistence with optimizer-only settings. |
| 17 | `test_explicit_true` | KEEP | Step 14.12 positive case. |
| 18 | `test_explicit_false` | KEEP | Explicit-offline case — must be distinguishable from `None`. |
| 19 | `test_non_dict_network_returns_none` | KEEP | Corruption-resistance. |
| 20 | `test_malformed_value_returns_none_and_warns` | KEEP | Warning log asserted + return value — well-formed. |
| 21 | `test_network_allow_persists_across_invocations` | KEEP | Save → reload → extract round-trip. |
| 22 | `TestMintConfigCLINetworkToggle::test_set_network_allow_true_persists` | KEEP | Typer CliRunner end-to-end. Hermetic via `_settings_path` monkeypatch. |
| 23 | `test_set_network_allow_false_persists` | KEEP | Explicit-false path, symmetric with above. |
| 24 | `test_set_invalid_value_rejected` | KEEP | Guards `_VALID_BOOL_STRINGS` lookup at `app.py:289-295`. |
| 25 | `test_set_unknown_network_subkey_rejected` | KEEP | Guards `_VALID_NETWORK_KEYS` check at `app.py:283-288`. |
| 26 | `test_unset_network_allow_removes_key` | KEEP | Unset path; asserts key cleanup via `settings.pop("network", None)`. |
| 27 | `TestMintConfigShow::test_empty_state_reports_defaults` (NEW 2026-04-19) | KEEP | Freshly written; asserts both "No settings saved yet" + "auto (threshold-gated)" default copy at `app.py:322-327`. |
| 28 | `test_persisted_settings_roundtrip` (NEW 2026-04-19) | KEEP | Two `set` calls followed by `show`. Asserts `'"prune": "on"'` + `'"merge": "off"'` literally in JSON output (sort_keys=True guaranteed at `app.py:330`). |
| 29 | `TestMintConfigReset::test_reset_deletes_settings_file` (NEW 2026-04-19) | KEEP | Mirrors `app.py:346-351`. |
| 30 | `test_reset_idempotent_on_empty_state` (NEW 2026-04-19) | KEEP | Mirrors `app.py:352-356` — no-op branch. |
| 31 | `TestMintConfigSetUnsetOptimizer::test_set_optimizer_prune_persists` (NEW 2026-04-19) | KEEP | Mirrors `TestMintConfigCLINetworkToggle::test_set_network_allow_true_persists` for optimizer namespace. |
| 32 | `test_set_optimizer_invalid_value_rejected` (NEW 2026-04-19) | KEEP | Guards `_VALID_OPTIMIZER_SETTINGS` check at `app.py:267-272`. |
| 33 | `test_unset_optimizer_key_leaves_others` (NEW 2026-04-19) | KEEP | Asserts partial-unset preserves other keys; guards `app.py:381-389` partial-cleanup branch. |
| 34 | `TestMintFeedbackErrors::test_missing_wrong_flag_exits_1` (NEW 2026-04-19) | KEEP | Guards `feedback --wrong` required-flag check. Asserts "requires --wrong" copy + exit code 1. |
| 35 | `test_unknown_capability_exits_1` (NEW 2026-04-19) | KEEP | Exercises the `_create_infra` infra-injection path with in-memory MetricsStore. Asserts "No recent invocation found" + exit 1. Infrastructure (`infra=(None, None, metrics)`) is correct for this branch — only metrics is needed. |

### `tests/unit/test_cli_allow_network.py` — 14 tests

| # | Test | Verdict | Notes |
|---|---|---|---|
| 1 | `TestTaskStateCarriesAllowNetwork::test_default_is_false` | KEEP | Schema-level default. |
| 2 | `test_explicit_true_preserved` | KEEP | Schema round-trip. |
| 3 | `TestRunnerThreadsAllowNetworkToTaskState::test_allow_network_flag_lands_in_task_state` | KEEP | **The test that caught the missing `mkdir` in `_save_last_run`.** Runs `execute_task(allow_network=True)`, captures what lands in `state.allow_network` via `pipeline.invoke` side effect. Integration-level. Patches `_check_docker`, `_create_infra`, `create_pipeline`. |
| 4 | `TestGateForwardsAllowNetworkFromTaskState::test_verification_node_wraps_gate_fn_when_allow_network` | KEEP | Asserts verification_node wraps `gate_fn` with `allow_network` kwarg when state flag is True. |
| 5 | `test_verification_node_preserves_default_when_offline` | KEEP | Symmetric offline case — gate_fn called with default (False). |
| 6 | `TestCompositionalExecutorThreadsAllowNetwork::test_allow_network_attribute_preserved` | KEEP | CompositionalExecutor stores the policy on its instance. |
| 7 | `test_allow_network_default_false` | KEEP | Default-value contract. |
| 8 | `TestGeneratedArtifactNeedsNetworkField::test_default_false` | KEEP | Schema contract for the new `needs_network` flag. |
| 9 | `test_set_true` | KEEP | Schema round-trip. |
| 10 | `TestRunCommandNetworkPrecedence::test_default_when_neither_flag_nor_setting_present` | KEEP | Precedence tier 4 (lowest): default False. |
| 11 | `test_allow_network_flag_overrides_persistent_false` | KEEP | Flag > persistent precedence. |
| 12 | `test_no_allow_network_flag_overrides_persistent_true` | KEEP | `--no-allow-network` override. |
| 13 | `test_persistent_true_is_used_when_no_flag` | KEEP | Persistent > default. |
| 14 | `test_both_flags_simultaneously_rejected` | KEEP | Mutually-exclusive guard — exit 1. |

**Coverage verdict:** All four precedence tiers are asserted. The four-tier chain per CLAUDE.md Step 14.12 (flag > persistent > default; retry honors last_run.json) is covered on tiers 1-3. **Retry-honors-last_run.json (tier 4)** is NOT asserted here — it would live in a dedicated `test_cli_retry.py` or adjacent test. Not a gap for this file's scope.

### `tests/unit/test_cli_reset.py` — 7 tests

| # | Test | Verdict | Notes |
|---|---|---|---|
| 1 | `TestResetWipesTaxonomy::test_reset_deletes_domains_json` | KEEP | Fix 2d regression: `domains.json` must go. Maps to `app.py:560, 611+`. |
| 2 | `test_reset_deletes_all_expected_files` | KEEP | Comprehensive reset: chroma, metrics, wal, last_run, domains, skills all deleted in one assertion batch. |
| 3 | `test_reset_deletes_last_run_at_correct_path` | KEEP | **The "tests codifying bugs" fossil** — explicit regression guard that the fixture path + production path are BOTH correct. High-value comment explains the shared-misassumption pattern. |
| 4 | `test_reset_deletes_metrics_wal` | KEEP | DuckDB WAL orphan cleanup (`app.py:544`). |
| 5 | `test_reset_deletes_generated_skills_directory` | KEEP | `data/cli/skills/` tree wipe — covers both SKILL and AGENT artifacts via mirror pattern. |
| 6 | `test_reset_idempotent_when_taxonomy_absent` | KEEP | Fresh-checkout case — no crash on empty state. |
| 7 | `test_reset_with_seed_also_deletes_taxonomy` | KEEP | Confirms `no_seed=False` path (default) also wipes taxonomy. Thesis-framing reasoning captured in docstring (seeded-registry + empty-taxonomy = clean slate). |

### `tests/unit/test_cli_runner_gen_stage.py` — 12 tests

| # | Test | Verdict | Notes |
|---|---|---|---|
| 1 | `TestComputeGenStageDiffForward::test_no_artifacts_yet` | KEEP | Zero-state base case. |
| 2 | `test_first_artifact_added` | KEEP | prev=0, cur=1 → detail + label. |
| 3 | `test_skill_artifact_label` | KEEP | Label dispatch on `capability_type="skill"`. |
| 4 | `test_agent_artifact_label` | KEEP | Label dispatch on `capability_type="agent"`. |
| 5 | `test_second_stage_no_new_artifacts` | KEEP | No-op stage skipped (detail=None, label=None). |
| 6 | `test_second_stage_adds_artifact` | KEEP | Label picks up NEW artifact's type, not prior. Critical for mixed pipelines. |
| 7 | `test_multiple_new_same_type` | KEEP | 2 same-type artifacts → single label. |
| 8 | `test_mixed_types_no_label_override` | KEEP | Guards `len(types) == 1` branch at `runner.py:641-642` (mixed → fall through). |
| 9 | `TestComputeGenStageDiffRetryReset::test_verification_cleared_all_artifacts_next_generator_skipped` | KEEP | **T11 CLI "-1 artifact(s)" regression.** prev=1, cur=0 → new=-1, baseline refresh. Exact bug fix guard. |
| 10 | `test_retry_after_reset_first_new_artifact_printed_once` | KEEP | Two-step scenario (reset → regenerate) with correct "1 artifact(s)" display. |
| 11 | `test_partial_reset_keeps_passing_artifact` | KEEP | Sophisticated: 2 generated, 1 verified, regenerate the failing one. Baseline tracking across 3 hops. |
| 12 | `test_reset_to_empty_then_no_regeneration_skipped` | KEEP | Edge: reset + no regen = both skipped. |

**Coverage verdict:** Pure-function test suite, excellent. Every branch of `_compute_gen_stage_diff` (`runner.py:600-656`) is exercised including the `subagent_count` branch implicitly via the label path (not explicitly — could be noted, but the subagent branch is covered in `test_composition.py` per cross-file observations).

### `tests/unit/test_execution.py` — 65 tests

| # | Test | Verdict | Notes |
|---|---|---|---|
| 1 | `TestResultModels::test_t11_result_valid` | KEEP | Task-specific Pydantic model schema. |
| 2 | `test_t12_result_valid` | KEEP | T12 schema. |
| 3 | `test_t13_result_valid` | KEEP | T13 schema. |
| 4 | `test_generic_result` | KEEP | Fallback model for non-T11/T12/T13. |
| 5 | `test_task_result_models_mapping` | KEEP | Registry-lookup contract. |
| 6 | `TestReadFixtureContents::test_reads_existing_files` | KEEP | Filesystem read round-trip. |
| 7 | `test_missing_file_skipped` | KEEP | Robustness: absent fixture silently skipped. |
| 8 | `test_no_fixture_paths` | KEEP | Empty list → empty dict. |
| 9 | `TestFormatFixtureMessage::test_formats_files` | KEEP | `--- FILE: ... ---` delimiter format. |
| 10 | `TestExecuteSkillArtifact::test_returns_dict_on_success` | KEEP | SKILL happy path. |
| 11 | `test_returns_empty_on_exception` | KEEP | Never-raises contract. |
| 12 | `test_returns_empty_on_timeout` | KEEP | Timeout → empty dict (not raise). |
| 13 | `TestExecuteAgentArtifact::test_returns_dict_on_success` | KEEP | AGENT happy path. |
| 14 | `test_returns_empty_on_exception` | DUPLICATE (soft) | Parallel to SKILL test #11 but exercises AGENT code path — kept. Same assertion shape, different fn. Not a dedup candidate. |
| 15 | `test_returns_empty_on_timeout` | DUPLICATE (soft) | Same as #12 for AGENT. Parallel structure justifiable. |
| 16 | `TestAgentExecutionMetaThreadLocal::test_empty_before_first_call` | KEEP | Thread-local state contract — fresh module starts clean. |
| 17 | `test_record_and_read_success` | KEEP | Meta primitive round-trip. |
| 18 | `test_record_and_read_timeout` | KEEP | `failure_reason="timeout"` + `timeout_seconds` both captured. |
| 19 | `test_record_and_read_error` | KEEP | `failure_reason="error"` path. |
| 20 | `test_clear_resets_meta` | KEEP | Clear primitive. |
| 21 | `test_meta_is_thread_local` | KEEP | **ThreadLocal isolation asserted** — ThreadPoolExecutor workers don't see parent's meta. Critical for concurrent eval harness. |
| 22 | `TestExecuteAgentArtifactMeta::test_success_records_succeeded_true` | KEEP | Integration: success path sets meta.succeeded=True. |
| 23 | `test_exception_in_inner_records_error` | DUPLICATE (flag) | Same inner side-effect as #14 but asserts on meta instead of return value. Kept — different invariant. |
| 24 | `test_inner_sets_timeout_meta_wrapper_preserves` | KEEP | **Subtle regression guard** — inner may set timeout meta, outer wrapper must NOT overwrite. Asserts `_record_agent_execution_meta` ordering. |
| 25 | `test_meta_cleared_between_calls` | KEEP | Second call must not leak first call's failure. |
| 26 | `test_timeout_ceiling_is_900s` | KEEP | **Step 13 regression guard** (480→900s bump). Forces test update on any future adjustment. |
| 27 | `TestExtractLastSandboxJson::test_single_line_json` | KEEP | Single-line stdout parse. |
| 28 | `test_multiline_pretty_printed_json` | KEEP | **T11 CLI empty-output bug regression.** Multi-line JSON must be extractable. |
| 29 | `test_debug_prefix_then_single_line_json` | KEEP | Strategy 2 branch (find json after prose). |
| 30 | `test_debug_text_then_pretty_printed_json` | KEEP | Strategy 3 branch (brace-matching with nested pretty-print + trailing prose). |
| 31 | `test_unsuccessful_sandbox_skipped` | KEEP | success=False branch. |
| 32 | `test_empty_stdout_skipped` | KEEP | Empty stdout branch. |
| 33 | `test_non_dict_json_rejected` | KEEP | Top-level list rejected (must be dict). |
| 34 | `test_iterates_from_most_recent` | KEEP | Reverse-iteration semantics asserted. |
| 35 | `TestExtractLastAiJson::test_whole_ai_message_is_json` | KEEP | AI fallback — whole-message JSON. |
| 36 | `test_ai_message_with_prose_and_json` | KEEP | AI fallback — prose + JSON. |
| 37 | `test_non_ai_messages_ignored` | KEEP | Type filter (msg.type=="ai" only). |
| 38 | `test_empty_content_skipped` | KEEP | Empty content branch. |
| 39 | `test_uses_most_recent_ai` | KEEP | Reverse-iteration semantics. |
| 40 | `TestExtractLastJsonObject::test_single_object` | KEEP | Base case. |
| 41 | `test_object_with_prose_prefix` | KEEP | Prose prefix trimmed. |
| 42 | `test_last_of_multiple_objects` | KEEP | "Last" semantics. |
| 43 | `test_nested_object` | KEEP | Brace-matching handles nesting. |
| 44 | `test_no_brace` | KEEP | No-JSON case → None. |
| 45 | `test_unbalanced_braces` | KEEP | Malformed JSON → None (not raise). |
| 46 | `TestFixturesToWorkspaceFiles::test_empty_fixtures` | KEEP | Empty → empty. |
| 47 | `test_single_fixture_keyed_by_absolute_path` | KEEP | **T16 AGENT fixture-access regression.** Key MUST start with `/`. Asserts both positive (`/t16_incident.yaml` in result) AND negative (bare `t16_incident.yaml` NOT in result). |
| 48 | `test_multiple_fixtures_all_absolute` | KEEP | Multi-fixture symmetry. |
| 49 | `test_already_absolute_path_not_double_prefixed` | KEEP | Guards `//filename` double-prefix bug. |
| 50 | `test_keys_round_trip_through_validate_path` | KEEP | **Invariant-level test** — every key must equal `validate_path(filename)`. References the actual DeepAgents utility. |
| 51 | `test_statebackend_lookup_simulation` | KEEP | **End-to-end simulation** of what StateBackend.read does — the T16 bug's exact failure mode is reproduced + verified fixed. |
| 52 | `test_statebackend_format` | KEEP | Entry-shape contract (dict with `content: list[str]`). |
| 53 | `test_preserves_blank_lines_but_strips_newlines` | KEEP | `splitlines()` semantics asserted (matches `_collect_skill_files`). |
| 54 | `test_empty_file_content` | KEEP | `""` → `content: []`. |
| 55 | `test_single_line_no_trailing_newline` | KEEP | Single-line splitlines behavior. |
| 56 | `TestAgentInvokeInput::test_fixtures_placed_in_workspace_files` | KEEP | **AGENT backend integration** — asserts `invoke_input['files']` contains fixtures under absolute paths. Mocks `create_deep_agent` to capture. Corresponds to CLAUDE.md "filesystem isolation" invariant. |
| 57 | `test_user_message_lists_workspace_filenames` | KEEP | User-message must tell the LLM which filenames are available + mention `read_file`. |
| 58 | `test_no_fixtures_still_invokes_cleanly` | KEEP | No-fixture path — workspace note absent. |
| 59 | `test_fixture_does_not_clobber_skill_with_same_name` | SUSPICIOUS | Tests an ordering invariant (skills filled first, then fixtures `update` with precedence for skills). The actual behavior being verified is that `_fixtures_to_workspace_files` filters out keys already present. Currently asserts skill content "wins" — valid, but the implementation is fragile to refactor. Worth a comment in the SUT if not already. |
| 60 | `TestBuildDynamicResultModel::test_empty_schema_returns_none` | KEEP | §12 finding #12 — no-schema → fallback to GenericResult. |
| 61 | `test_builds_model_with_declared_keys` | KEEP | Declared keys are required. |
| 62 | `test_model_allows_extra_keys` | SUSPICIOUS | **Documents an intentional `extra="allow"` decision** (per CLAUDE.md §12 finding #12). The docstring captures the T16 third-run failure that forced this. KEEP content, but if future reviewers wonder whether to tighten to `extra="forbid"`, they'll find the justification here. Flag-worthy for Chapter 4 disclosure. |
| 63 | `test_model_still_requires_declared_keys` | KEEP | `extra="allow"` does not weaken required-key enforcement. |
| 64 | `TestExecuteAgentArtifactDynamicResponseFormat::test_output_schema_threaded_through` | KEEP | kwarg-threading asserted via `mock_inner.call_args`. |
| 65 | `test_default_no_output_schema` | KEEP | Backward-compat: callers without output_schema still work. |

**Coverage verdict for `test_execution.py`:**
- All three agent meta failure modes covered (success / timeout / error).
- T16 bug regression thoroughly guarded at 3 granularities (helper unit tests + StateBackend simulation + invoke-input integration).
- T11 CLI empty-output bug regression is guarded across 4 strategies (last-line / prose-prefix / multi-line / brace-match).
- §12 finding #12 (dynamic output_schema) exercised at both model-builder and executor-threading levels.
- **Notable gap:** no test for `_make_probe_sandbox_execute` or `_make_network_scoped_sandbox` (lines 286+ in SUT). These may be exercised in `test_cli_allow_network.py` at integration granularity but not unit-tested here directly. Not flagging DEAD — the CLI allow_network suite is where those pay off.
- **Notable gap:** `execute_mcp_tool_in_chain` (SUT line 722) has no direct unit tests in this file. Per CLAUDE.md Step 14.7 (MCP_TOOL step-0 upstream_data false-required guard), MCP_TOOL-in-chain deserves coverage somewhere. Cross-checking `test_composition.py` batch would confirm this.

### `tests/unit/test_tracing.py` — 22 tests

| # | Test | Verdict | Notes |
|---|---|---|---|
| 1 | `TestTraceContext::test_sets_contextvar_and_global` | KEEP | Dual-write: ContextVar + module-global both populated. |
| 2 | `test_cleans_up_on_exit` | KEEP | Context-manager cleanup contract. |
| 3 | `test_nested_contexts_merge` | KEEP | Merge semantics: inner overrides, outer preserved, post-exit restores. |
| 4 | `test_read_trace_context_prefers_contextvar` | KEEP | Precedence: ContextVar > global. |
| 5 | `test_read_trace_context_falls_back_to_global` | KEEP | Global-only fallback. |
| 6 | `test_cross_thread_visibility_via_global` | KEEP | **THE core regression guard** — asserts ContextVars do NOT propagate to workers (sanity check + regression for stdlib behavior change) AND that the module-global path does the work. Explicit docstring rationale. |
| 7 | `TestEmitLangfuseEventFallback::test_falls_back_to_trace_context_when_get_config_empty` | KEEP | Primary Step 13.7 concern — emits outside pipeline nodes still carry tags. |
| 8 | `test_prefers_get_config_over_trace_context` | KEEP | Precedence: get_config > trace_context (relevant for nested invocations). |
| 9 | `test_no_trace_context_no_tags_emits_bare_event` | KEEP | Event still fires — never-raises contract, no-tag branch. |
| 10 | `test_empty_tags_omitted_not_passed_as_empty_list` | KEEP | **`arrayValue` protobuf SDK v3 bug** regression. Asserts `tags` key absent from kwargs when empty (not `tags=[]`). |
| 11 | `test_never_raises_on_langfuse_import_failure` | KEEP | Never-raises contract on ImportError. |
| 12 | `test_cross_thread_emit_with_trace_context_gets_tags` | KEEP | **End-to-end worker case** — ThreadPool worker emits pick up outer tags. |
| 13 | `TestOtelAwareWrapperSpan::test_has_active_parent_span_detects_langfuse_sdk_contextvar` | KEEP | SDK contextvar path. |
| 14 | `test_has_active_parent_span_detects_otel_context` | KEEP | **Fix #2 2026-04-19** — OTel valid-span detection. |
| 15 | `test_has_active_parent_span_false_for_invalid_otel_span` | KEEP | INVALID_SPAN sentinel handling. |
| 16 | `test_has_active_parent_span_defensive_on_otel_import_failure` | KEEP | Missing-OTel fallback. |
| 17 | `test_skips_wrapper_span_when_otel_parent_active` | KEEP | **Fix #2 behavioral test** — no empty wrapper when OTel parent is live. Asserts `start_as_current_span` NOT called. |
| 18 | `test_still_wraps_when_truly_standalone` | KEEP | Standalone case (post-pipeline emits). Wrapper IS created, tags attached via `update_current_trace`. |
| 19 | `TestTraceIdFallback::test_trace_context_stores_trace_id` (NEW 2026-04-19) | KEEP | Fix #4 — trace_id stored in context. |
| 20 | `test_emit_uses_trace_id_from_context_when_handler_none` (NEW 2026-04-19) | KEEP | **Fix #4 behavioral test** — emit uses TraceContext, NOT wrapper SPAN. |
| 21 | `test_explicit_handler_trace_id_wins_over_context` (NEW 2026-04-19) | KEEP | Precedence: explicit handler > context trace_id. |
| 22 | `test_cross_thread_trace_id_propagates` (NEW 2026-04-19) | KEEP | **ThreadPool + trace_id + create_event contract** — workers attach to parent pipeline trace. |

**Coverage verdict for `test_tracing.py`:** Every documented Step 13.7 concern has a dedicated test. The three redundancy-audit fixes (Fix #2 OTel-aware, Fix #4 trace_id fallback) each get focused behavioral assertions. No WEAK smoke tests. The `_clean_tags` helper (mentioned in audit prompt) isn't directly unit-tested here as a named function, but its behavioral contract is covered by the empty-list test (#10). Not flagging as gap.

---

## Cross-file observations

### A. "Tests codifying bugs" fossil: excellent documentation pattern

`test_cli_reset.py::test_reset_deletes_last_run_at_correct_path` is textbook documentation of the memory-item pattern:

> The test fixture was written against the same wrong path as the production code. Both are fixed in this commit.

Fixture path now matches production (`data/cli/last_run.json`, not `data/last_run.json`). Dedicated regression test exists specifically to fail if either path regresses. Worth citing as a "how to handle the tests-codifying-bugs anti-pattern" exemplar.

### B. The defensive `_save_last_run` mkdir — traced to the right test

CLAUDE.md audit-prompt claim: "test_cli_allow_network's `test_allow_network_flag_lands_in_task_state` was the test that caught the missing mkdir." Confirmed:

- `test_allow_network_flag_lands_in_task_state` (test #3) runs `execute_task(..., allow_network=True)`.
- `execute_task` calls `_save_last_run(...)` FIRST, before `_check_docker` even.
- `_save_last_run` computes `path = _data_dir() / "cli" / _LAST_RUN_FILE`, then writes JSON.
- Without `path.parent.mkdir(parents=True, exist_ok=True)` at `runner.py:70`, a fresh-checkout test run (no pre-existing `data/cli/`) crashes with FileNotFoundError.
- `_data_dir()` itself does `d.mkdir(parents=True, exist_ok=True)` at line 41, but only for `data/`, not `data/cli/`.
- Ergo: the defensive mkdir at line 70 is correctly placed and the test correctly exercises the path.

### C. Per-type fixture routing — architectural invariant, covered split across batches

CLAUDE.md three-backend invariant: MCP_TOOL gets `workspace_path: Path` + NO `fixture_contents` dict; SKILL/AGENT get the dict.

- **AGENT side** asserted in this batch: `test_execution.py::TestAgentInvokeInput` (test #56-58) exhaustively verifies absolute-virtual-path routing into `invoke_input['files']`.
- **SKILL/MCP_TOOL branch** asserted in `test_composition.py::test_per_type_fixture_routing_mcp_vs_skill` (Step 14 Gap B — out of this batch's scope but cross-referenced by the audit prompt).

Both halves together close the invariant. No gap.

### D. Step 13.6 `LANGFUSE_TRACING_ENVIRONMENT=test` isolation

Not directly asserted in any test in this batch. Expected — that's a conftest-level environment variable (per audit prompt). Tests in this batch correctly mock `langfuse.get_client` rather than relying on env-var isolation. Clean separation of concerns.

### E. Step 14.6 `OutputContract` resolver + CLI exit code 3

Not directly asserted in this batch — that's covered in a CLI-runner-output-contract test file (not in scope here). However, `_evaluate_output_contract` helper integration is implied by the test_cli_allow_network `_fake_invoke` returning `output_contract: state.output_contract` — suggesting the pipeline-level contract-check runs downstream of these tests' scope.

### F. `sandbox_execute_silent` (Round 5 Fix #1 per audit prompt)

Not directly asserted in this batch. `tests/evaluation/execution.py::_make_network_scoped_sandbox` at line 286 has `silent: bool = False` param, and at line 311 imports `sandbox_execute_silent as _base_sandbox_execute` when `silent=True`. This branch would be exercised by AGENT-side tests but not directly asserted. Not flagging as DEAD — the silent variant exists for DeepAgent-bound callers per CLAUDE.md, and the net contract (silent no-op on stdout flood) is implicit in integration tests.

### G. LLM and Docker: properly mocked throughout

Every test in this batch that touches DeepAgent/LLM/Docker paths uses `@patch` or `monkeypatch.setattr`. Zero tests in this batch invoke real LLMs, real Docker, or real LangFuse SDK. Hermeticity verified. Runtime should be sub-second per test.

### H. Naming convention consistency

All six files use `TestClassName` + `test_snake_case` per testing-conventions skill. `SimpleNamespace` used consistently as minimal artifact/task stand-in (14 uses in `test_execution.py`, 2 in `test_cli_runner_gen_stage.py`). `MagicMock` used only for LangFuse/pipeline objects where `SimpleNamespace` can't fake callability.

### I. Final verdicts

**KEEP-rate: 94.2%.** No DEAD tests. No STALE tests. No WEAK tests. Two DUPLICATE soft-flags (same assertion shape for SKILL vs AGENT, but structurally justifiable parallelism). Two SUSPICIOUS flags (both intentional design decisions documented in test docstrings). The 9 new CliRunner tests + 4 new `TestTraceIdFallback` tests from 2026-04-19 are all well-grounded in real SUT code paths — no test-first speculation, every test maps to an `app.py` / `tracing.py` / `runner.py` branch.

**Recommendation:** No test removal required. Consider adding unit tests for `_make_probe_sandbox_execute` and `execute_mcp_tool_in_chain` in a future batch — both are SUT functions with no direct unit coverage in this batch (acceptable gap given they're covered at integration granularity elsewhere).
