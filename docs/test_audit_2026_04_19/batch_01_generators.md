# Batch 01 — Generators

## Summary
- Total: 189 tests / 8 files
- KEEP: 181 | DEAD: 0 | STALE: 0 | WEAK: 1 | DUPLICATE: 6 | SUSPICIOUS: 1

All tests import cleanly against current source. No DEAD/STALE verdicts — the generator modules' public surface matches what's exercised. Three generators (`mcp_tool`, `skill`, `agent`) follow a very symmetric unit-test pattern (schema → validators → node routing → single-generation repair path → schema_context plumbing). The handful of DUPLICATE verdicts below flag where Test Class A and Test Class B cover effectively the same assertion twice inside the same file; they are low-value duplication, not test bugs.

## Per-file findings

### tests/unit/test_mcp_tool_generator.py (79 tests)
Summary: KEEP: 74 | DEAD: 0 | STALE: 0 | WEAK: 1 | DUPLICATE: 4 | SUSPICIOUS: 0

| test | verdict | reason (1 line) |
|---|---|---|
| TestGeneratedToolCodeSchema::test_valid | KEEP | Constructs valid `GeneratedToolCode`, matches current 4-field schema (tool_name, source_code, description, pip_packages). |
| TestGeneratedToolCodeSchema::test_extra_fields_forbidden | KEEP | Asserts `extra="forbid"` config still rejects unknown fields (matches `model_config` on source). |
| TestGeneratedToolCodeSchema::test_roundtrip | WEAK | Asserts only `tool_name` round-trips; does not exercise all fields. Low signal but harmless. |
| TestValidateCode::test_syntax_error | KEEP | Matches `compile()`-first path in `_validate_code`. |
| TestValidateCode::test_valid_full_mcp_tool | KEEP | Canonical happy-path for `_validate_code`. |
| TestValidateCode::test_missing_fastmcp | KEEP | Matches `if "FastMCP" not in source_code` branch. |
| TestValidateCode::test_missing_mcp_tool_decorator | KEEP | Matches `@mcp.tool` branch. |
| TestValidateCode::test_missing_async_def | KEEP | Matches `async_def_count == 0` branch. |
| TestValidateCode::test_missing_field_import | KEEP | Matches `Field` import check via AST. |
| TestValidateCode::test_multiple_async_def_rejected | KEEP | Matches `async_def_count > 1` rejection. |
| TestValidateCode::test_nested_async_def_not_rejected | KEEP | Exercises `ast.iter_child_nodes` vs `ast.walk` behavior — guards BUG-10-like regressions. |
| TestValidateCode::test_missing_annotated_params | KEEP | Matches `Annotated` import check. |
| TestValidateCode::test_multiple_structural_errors_reported_together | KEEP | Matches the `"; ".join(errors)` coalescing so repair prompt sees all issues at once. |
| TestValidateCode::test_async_def_in_string_literal_not_counted | KEEP | AST-based detection regression test. |
| TestValidateCode::test_annotated_in_docstring_not_false_positive | KEEP | AST-import-scan regression test. |
| TestValidateCode::test_field_in_docstring_not_false_positive | KEEP | AST-import-scan regression test. |
| TestValidateCode::test_private_top_level_async_helper_accepted | KEEP | Matches `not n.name.startswith("_")` filter — mirrors runner's `dir()` skip. |
| TestValidateToolName::test_valid_snake_case | KEEP | |
| TestValidateToolName::test_valid_single_word | KEEP | |
| TestValidateToolName::test_valid_with_digits | KEEP | |
| TestValidateToolName::test_empty_name | KEEP | |
| TestValidateToolName::test_whitespace_only | KEEP | |
| TestValidateToolName::test_spaces_in_name | KEEP | |
| TestValidateToolName::test_hyphens_rejected | KEEP | |
| TestValidateToolName::test_uppercase_rejected | KEEP | |
| TestValidateToolName::test_name_too_long | KEEP | |
| TestValidateToolName::test_name_exactly_64_chars | KEEP | Boundary test. |
| TestValidateToolName::test_starts_with_digit | KEEP | |
| TestValidateToolName::test_starts_with_underscore | KEEP | Matches `_SNAKE_CASE_RE = r"^[a-z][a-z0-9_]*$"` which rejects leading underscore. |
| TestValidateStepSignature::test_first_step_without_upstream_ok | KEEP | Step 14.7 signature validator happy path. |
| TestValidateStepSignature::test_first_step_with_required_upstream_rejected | KEEP | Step 14.7 core regression guard. |
| TestValidateStepSignature::test_downstream_with_upstream_ok | KEEP | |
| TestValidateStepSignature::test_downstream_missing_upstream_rejected | KEEP | Matches `has_required_upstream` branch for downstream. |
| TestValidateStepSignature::test_downstream_with_optional_upstream_rejected | KEEP | Matches explicit "optional masks empty-dict" rule. |
| TestValidateStepSignature::test_none_is_first_step_skips_check | KEEP | Matches `is_first_step is None` early-return. |
| TestValidateStepSignature::test_syntax_error_returns_none | KEEP | Matches try/except SyntaxError branch. |
| TestValidateStepSignature::test_missing_async_function_returns_none | KEEP | Matches `if func is None: return None` early-return. |
| TestValidateStepSignature::test_private_async_helper_ignored | KEEP | Matches `not n.name.startswith("_")` filter. |
| TestValidateStepSignature::test_kwonly_required_upstream_also_flagged | KEEP | Matches kwonly-required detection (`for arg, default in zip(args.kwonlyargs, args.kw_defaults)`). |
| TestStepPositionBlock::test_first_step_returns_first_directive | KEEP | |
| TestStepPositionBlock::test_downstream_returns_downstream_directive | KEEP | |
| TestStepPositionBlock::test_none_returns_empty | KEEP | |
| TestStepPositionBlock::test_first_directive_mentions_no_upstream_data | KEEP | Snapshot-style guard on FIRST_STEP_DIRECTIVE content. |
| TestStepPositionBlock::test_downstream_directive_mentions_required_upstream_data | KEEP | Snapshot-style guard on DOWNSTREAM_STEP_DIRECTIVE content. |
| TestIsFirstStepForGap::test_first_step_when_no_depends_on | KEEP | |
| TestIsFirstStepForGap::test_downstream_when_depends_on_nonempty | KEEP | |
| TestIsFirstStepForGap::test_none_when_gap_not_in_subtasks | KEEP | |
| TestIsFirstStepForGap::test_none_when_subtasks_empty | KEEP | |
| TestIsFirstStepForGap::test_find_subtask_returns_matching_entry | KEEP | |
| TestIsFirstStepForGap::test_t11_join_first_step_regression | KEEP | T11 regression fixture. |
| TestStepPositionInPrompt::test_first_step_directive_in_prompt | KEEP | End-to-end check that directive lands in captured user message. |
| TestStepPositionInPrompt::test_downstream_directive_in_prompt | KEEP | |
| TestStepPositionInPrompt::test_none_omits_directive | KEEP | |
| TestStepPositionInPrompt::test_schema_context_reaches_prompt | KEEP | Step 14 Gap C regression guard (shipped 2026-04-17, commit `1bae0da`). |
| TestStepPositionInPrompt::test_empty_schema_context_omits_contract_section | KEEP | Step 14 Gap C companion test. |
| TestGenerateMcpToolNode::test_filters_mcp_tool_gaps_only | KEEP | Matches `mcp_gaps = [...]` filter. |
| TestGenerateMcpToolNode::test_empty_gaps | KEEP | |
| TestGenerateMcpToolNode::test_mixed_success_and_failure | KEEP | |
| TestGenerateMcpToolNode::test_preserves_existing_errors | KEEP | |
| TestGenerateMcpToolNode::test_preserves_existing_artifacts | KEEP | |
| TestGenerateMcpToolNode::test_node_passes_task_description_as_task_context | KEEP | Matches `task_context = build_task_context(state)` on attempt 0. |
| TestGenerateMcpToolNode::test_command_structure | KEEP | |
| TestGenerateSingleTool::test_first_attempt_succeeds | KEEP | |
| TestGenerateSingleTool::test_description_preserved_in_artifact | KEEP | Bug 1 regression. |
| TestGenerateSingleTool::test_description_preserved_after_repair | KEEP | |
| TestGenerateSingleTool::test_first_llm_call_fails | KEEP | |
| TestGenerateSingleTool::test_repair_succeeds | KEEP | |
| TestGenerateSingleTool::test_repair_llm_call_fails | KEEP | |
| TestGenerateSingleTool::test_repair_still_invalid | KEEP | |
| TestGenerateSingleTool::test_task_context_forwarded_to_prompt | KEEP | |
| TestGenerateSingleTool::test_default_task_context_falls_back_to_gap_description | KEEP | Matches `task_context or gap.description` fallback. |
| TestGenerateSingleTool::test_generate_single_tool_uses_forced_name | KEEP | Fix 5 regression. |
| TestSkipOnRetry::test_skip_gaps_with_existing_passing_artifact | KEEP | |
| TestSkipOnRetry::test_gap_query_set_on_generated_artifact | DUPLICATE | Essentially re-proves the mock-artifact's `gap_query` survives — the mock itself sets it. Signal is tiny beyond `test_skip_gaps_with_existing_passing_artifact`. |
| TestSkipOnRetry::test_first_attempt_processes_all_gaps | KEEP | |
| TestSkipOnRetry::test_gap_query_none_does_not_crash_skip_set | KEEP | Matches `if a.gap_query is not None` guard. |
| TestParallelGeneration::test_parallel_generation_produces_all_artifacts | KEEP | |
| TestParallelGeneration::test_parallel_generation_exception_recorded_as_error | KEEP | Matches `future.result()` try/except branch. |
| TestParallelGeneration::test_already_passing_gaps_not_submitted_to_pool | DUPLICATE | Re-proves what `TestSkipOnRetry::test_skip_gaps_with_existing_passing_artifact` already asserts (mock called exactly once for one unmatched gap). |

### tests/unit/test_skill_generator.py (36 tests)
Summary: KEEP: 34 | DEAD: 0 | STALE: 0 | WEAK: 0 | DUPLICATE: 2 | SUSPICIOUS: 0

| test | verdict | reason (1 line) |
|---|---|---|
| TestStripCodeFences::test_no_fences | KEEP | |
| TestStripCodeFences::test_plain_fences | KEEP | |
| TestStripCodeFences::test_language_fences | KEEP | |
| TestStripCodeFences::test_preserves_inner_content | KEEP | |
| TestStripCodeFences::test_inner_code_fence_preserved | KEEP | Matches `fence_count % 2 == 1` vs even-count branches. |
| TestStripCodeFences::test_outer_closing_fence_absent_inner_fence_not_stripped | KEEP | BUG-01 regression. |
| TestExtractSkillName::test_valid_frontmatter | KEEP | |
| TestExtractSkillName::test_no_frontmatter | KEEP | |
| TestExtractSkillName::test_invalid_yaml | KEEP | Matches yaml.YAMLError except branch in `_parse_frontmatter`. |
| TestExtractSkillName::test_missing_name | KEEP | |
| TestValidateSkillContent::test_valid_content | KEEP | |
| TestValidateSkillContent::test_invalid_name | KEEP | |
| TestValidateSkillContent::test_no_frontmatter | KEEP | |
| TestValidateSkillContent::test_empty_body_rejected | KEEP | Matches explicit body-empty check. |
| TestValidateSkillContent::test_empty_skill_name_rejected | KEEP | |
| TestValidateSkillContent::test_whitespace_skill_name_rejected | KEEP | |
| TestValidateSkillContent::test_slash_in_skill_name_rejected | KEEP | Matches `"/" in skill_name` guard. |
| TestValidateSkillContent::test_absolute_path_in_skill_name_rejected | DUPLICATE | Same guard path as `test_slash_in_skill_name_rejected` (leading `/` is caught by the same `"/" in skill_name` check). |
| TestGenerateSkillNode::test_skips_non_skill_gaps | KEEP | |
| TestGenerateSkillNode::test_empty_gaps_command_structure | KEEP | |
| TestGenerateSkillNode::test_preserves_existing_artifacts | KEEP | |
| TestGenerateSkillNode::test_preserves_existing_errors | KEEP | |
| TestGenerateSkillNode::test_filters_skill_gaps_only | KEEP | |
| TestGenerateSkillNode::test_disk_write_failure_returned_as_error | KEEP | Uses blocker-file trick, matches OSError except branch. |
| TestGenerateSkillNode::test_mixed_success_and_failure | KEEP | |
| TestGenerateSingleSkill::test_first_attempt_succeeds | KEEP | |
| TestGenerateSingleSkill::test_repair_succeeds | KEEP | |
| TestGenerateSingleSkill::test_first_llm_call_fails | KEEP | |
| TestGenerateSingleSkill::test_repair_llm_call_fails | KEEP | |
| TestGenerateSingleSkill::test_repair_still_invalid | KEEP | |
| TestGenerateSingleSkill::test_skill_name_derived_from_query_when_frontmatter_has_no_name | KEEP | Matches `skill_name = extract_skill_name(content) or forced_name` fallback. |
| TestGenerateSingleSkill::test_task_context_forwarded_to_prompt | KEEP | |
| TestGenerateSingleSkill::test_schema_context_reaches_prompt | KEEP | Step 14 Gap C regression. |
| TestGenerateSingleSkill::test_empty_schema_context_omits_contract_section | KEEP | Step 14 Gap C companion. |
| TestGenerateSingleSkill::test_all_special_char_query_uses_forced_name | DUPLICATE | Overlaps with `test_skill_name_derived_from_query_when_frontmatter_has_no_name` — both verify the forced_name fallback path via `slugify_gap_hyphen`. |
| TestGetValidateSkillLazyImport::test_module_not_cached_on_exec_failure | KEEP | Bug 2 regression. |

### tests/unit/test_agent_generator.py (47 tests)
Summary: KEEP: 46 | DEAD: 0 | STALE: 0 | WEAK: 0 | DUPLICATE: 0 | SUSPICIOUS: 1

| test | verdict | reason (1 line) |
|---|---|---|
| TestValidateResult::test_valid_plain_agent | KEEP | |
| TestValidateResult::test_valid_deep_agent | KEEP | |
| TestValidateResult::test_empty_system_prompt | KEEP | |
| TestValidateResult::test_invalid_agent_name | KEEP | |
| TestValidateResult::test_agent_name_too_long | KEEP | |
| TestValidateResult::test_invalid_node_name | KEEP | |
| TestValidateResult::test_deep_agent_missing_skill_content | KEEP | |
| TestValidateResult::test_skill_content_missing_frontmatter | KEEP | Matches `if not result.skill_content.startswith("---"):` branch. |
| TestValidateResult::test_skill_content_invalid_name_format | KEEP | |
| TestValidateResult::test_skill_content_empty_body | KEEP | |
| TestValidateResult::test_agent_name_exactly_64_chars | KEEP | Boundary. |
| TestValidateResult::test_skill_content_name_mismatch_with_agent_name | KEEP | Matches `content_name != result.agent_name` branch. |
| TestValidateResult::test_skill_content_invalid_yaml_syntax | KEEP | |
| TestValidateResult::test_node_name_with_leading_underscore_is_valid | SUSPICIOUS | Documents leading-underscore acceptance. Looking at `_SNAKE_CASE_RE = r"^[a-z_][a-z0-9_]*$"` (agent.py:141), this regex does match `_private_node`, so the assertion is correct — but it contradicts the MCP tool generator's `_SNAKE_CASE_RE = r"^[a-z][a-z0-9_]*$"` (mcp_tool.py:94) which rejects leading underscore. Intentional asymmetry between generators warrants a comment/link — not a test bug, but a latent "which rule applies when" drift. |
| TestValidateResult::test_empty_agent_name | KEEP | |
| TestValidateResult::test_whitespace_only_agent_name | KEEP | |
| TestValidateResult::test_invalid_agent_name_with_skill_content_does_not_crash | KEEP | Matches `elif not agent_name_valid: ... "skill_content validation skipped..."` branch. |
| TestValidateResult::test_empty_node_name | KEEP | |
| TestValidateResult::test_empty_description | KEEP | |
| TestGenerateAgentNode::test_node_returns_command | KEEP | |
| TestGenerateAgentNode::test_empty_gaps | KEEP | |
| TestGenerateAgentNode::test_skips_non_agent_gaps | KEEP | |
| TestGenerateAgentNode::test_preserves_existing_artifacts | KEEP | |
| TestGenerateAgentNode::test_preserves_existing_errors | KEEP | |
| TestGenerateAgentNode::test_filters_agent_gaps_only | KEEP | |
| TestGenerateAgentNode::test_mixed_success_and_failure | KEEP | Exercises lead-failure → standalone fallback + per-gap error aggregation. |
| TestGenerateAgentNode::test_deep_agent_disk_write_failure_returned_as_error | KEEP | |
| TestGenerateSingleAgent::test_first_attempt_succeeds_plain_agent | KEEP | |
| TestGenerateSingleAgent::test_repair_succeeds | KEEP | |
| TestGenerateSingleAgent::test_first_llm_call_fails | KEEP | |
| TestGenerateSingleAgent::test_repair_llm_call_fails | KEEP | |
| TestGenerateSingleAgent::test_repair_still_invalid | KEEP | |
| TestGenerateSingleAgent::test_task_context_forwarded_to_prompt | KEEP | |
| TestGenerateSingleAgent::test_schema_context_reaches_prompt | KEEP | Step 14 Gap C regression. |
| TestGenerateSingleAgent::test_empty_schema_context_omits_contract_section | KEEP | Step 14 Gap C companion. |
| TestGenerateSingleAgent::test_repair_prompt_includes_validation_errors | KEEP | Matches `AGENT_REPAIR_PROMPT.format(..., validation_errors=...)` wiring. |
| TestGenerateSingleAgent::test_deep_agent_skill_written_to_disk | KEEP | Matches the frontmatter-name-rewrite path (`re.sub(r"(?m)^(name:\s*).+$", ...)`). |
| TestValidateLeadResult::test_valid_lead_with_subagents | KEEP | |
| TestValidateLeadResult::test_empty_subagents_list | KEEP | Note: `LeadWithSubagentsResult.subagents` has `min_length=1` pydantic constraint. Setting `.subagents = []` post-construction bypasses that constraint, so the path exercised is `_validate_lead_result`'s own `if not result.subagents:` branch. Worth a comment, but valid. |
| TestValidateLeadResult::test_duplicate_subagent_names | KEEP | |
| TestValidateLeadResult::test_invalid_subagent_name | KEEP | |
| TestValidateLeadResult::test_empty_subagent_system_prompt | KEEP | |
| TestValidateLeadResult::test_is_deep_agent_must_be_true | KEEP | Matches explicit `is_deep_agent` branch in `_validate_lead_result`. |
| TestLeadWithSubagentsModeSelection::test_single_agent_gap_uses_standalone_mode | KEEP | Matches `if len(gaps_to_generate) >= 2` branch in `generate_agent`. |
| TestLeadWithSubagentsModeSelection::test_multiple_agent_gaps_uses_lead_mode | KEEP | |
| TestLeadWithSubagentsModeSelection::test_lead_failure_falls_back_to_standalone | KEEP | Matches fallback `with concurrent.futures.ThreadPoolExecutor(...)` block after lead failure. |
| TestLeadWithSubagentsModeSelection::test_mixed_types_only_agent_gaps_counted | KEEP | |

### tests/integration/test_mcp_tool_generator.py (3 tests)
Summary: KEEP: 3 | DEAD: 0 | STALE: 0 | WEAK: 0 | DUPLICATE: 0 | SUSPICIOUS: 0

| test | verdict | reason (1 line) |
|---|---|---|
| TestMcpToolGeneratorLive::test_generate_add_tool | KEEP | Real-LLM happy path; compiles + asserts snake_case. Matches current source. |
| TestMcpToolGeneratorLive::test_generate_and_execute_tool | KEEP | End-to-end generate → ToolLoader.load_tool → execute_tool; robust via `inspect.signature`. |
| TestMcpToolGeneratorLive::test_generate_mcp_tool_node | KEEP | Node-level integration test; asserts `cmd.goto == "generate_skill"`. |

### tests/integration/test_skill_generator.py (3 tests)
Summary: KEEP: 3 | DEAD: 0 | STALE: 0 | WEAK: 0 | DUPLICATE: 0 | SUSPICIOUS: 0

| test | verdict | reason (1 line) |
|---|---|---|
| TestSkillGeneratorLive::test_generate_skill_passes_validation | KEEP | Real-LLM happy path + quick_validate. |
| TestSkillGeneratorLive::test_skill_written_to_disk | KEEP | Asserts SKILL.md on disk under `tmp_path / capability_name`. |
| TestSkillGeneratorLive::test_generate_skill_node | KEEP | Patches `SKILLS_OUTPUT_DIR` — matches source's `set_skills_output_dir` convention. Asserts `cmd.goto == "generate_agent"`. |

### tests/integration/test_agent_generator.py (5 tests)
Summary: KEEP: 5 | DEAD: 0 | STALE: 0 | WEAK: 0 | DUPLICATE: 0 | SUSPICIOUS: 0

| test | verdict | reason (1 line) |
|---|---|---|
| TestAgentGeneratorLive::test_generate_plain_agent | KEEP | |
| TestAgentGeneratorLive::test_generate_deep_agent_writes_skill_to_disk | KEEP | Conditional on `is_deep_agent`; graceful if LLM picks otherwise. |
| TestAgentGeneratorLive::test_agent_config_has_required_keys | KEEP | |
| TestAgentGeneratorLive::test_generate_agent_node | KEEP | |
| TestAgentGeneratorLive::test_deep_agent_skill_disk_write_guaranteed | KEEP | Crafted prompt forces DeepAgent classification; explicit `pytest.fail` if LLM drifts. |

### tests/unit/test_build_task_context.py (8 tests)
Summary: KEEP: 8 | DEAD: 0 | STALE: 0 | WEAK: 0 | DUPLICATE: 0 | SUSPICIOUS: 0

| test | verdict | reason (1 line) |
|---|---|---|
| test_first_attempt_returns_task_description_unchanged | KEEP | Matches `if state.attempt_count == 0:` branch. |
| test_first_attempt_with_results_still_unchanged | KEEP | |
| test_retry_with_failed_report_injects_feedback | KEEP | Matches "PREVIOUS ATTEMPT FAILED VERIFICATION" literal in source. |
| test_retry_only_includes_critical_and_high | KEEP | Matches `if finding.severity in (Severity.CRITICAL, Severity.HIGH)` filter. |
| test_retry_with_passing_reports_returns_unchanged | KEEP | Matches `[r for r in state.verification_results if not r.overall_passed]` filter. |
| test_retry_with_no_results_returns_unchanged | KEEP | |
| test_retry_multiple_failed_reports_all_included | KEEP | |
| test_retry_failed_report_with_only_low_findings_returns_unchanged | KEEP | Matches the no-feedback-lines early-return (`if not feedback_lines:`). |

### tests/unit/test_slugify_gap.py (8 tests)
Summary: KEEP: 8 | DEAD: 0 | STALE: 0 | WEAK: 0 | DUPLICATE: 0 | SUSPICIOUS: 0

| test | verdict | reason (1 line) |
|---|---|---|
| TestSlugifyGap::test_stable_across_calls | KEEP | Deterministic hash-based slug. |
| TestSlugifyGap::test_collision_free | KEEP | `slugify('parse CSV file') != slugify('parse CSV files')` — verified empirically via uv run. |
| TestSlugifyGap::test_handles_special_chars | KEEP | |
| TestSlugifyGap::test_truncates_long_base | KEEP | |
| TestSlugifyGap::test_hyphen_variant | KEEP | |
| TestSlugifyGap::test_no_double_underscore_when_truncating_mid_separator | KEEP | Regression fixture for strip-after-truncate fix. |
| TestSlugifyGap::test_no_double_hyphen_in_hyphen_variant | KEEP | |
| TestSlugifyGap::test_empty_query_falls_back_to_cap | KEEP | Matches `base = ... or "cap"` fallback. |

## Cross-file observations

- **Pattern symmetry.** The three generator unit-test files (`mcp_tool`, `skill`, `agent`) implement an almost identical 5-section layout: schema validation → pure validators → node-routing → single-generation repair path → schema_context plumbing. This symmetry is a feature, not a defect — any missing coverage in one generator becomes obvious compared to the others.
- **Step 14 Gap C coverage landed (commit `1bae0da`, 2026-04-17).** All three generator unit files have `test_schema_context_reaches_prompt` + `test_empty_schema_context_omits_contract_section` pairs asserting the `build_schema_context` → prompt injection path. They guard Pillar 1 Structured State Communication (§3.4) at the generation granularity — confirmed still in place.
- **No STALE/DEAD findings.** Every assertion was cross-checked against current source. The Pillar 3 flip (Merge/Split supporting all three types) and Step 14.7 signature validator are both well-reflected; no tests codify the old narrower behavior. `build_task_context` still returns the literal `"PREVIOUS ATTEMPT FAILED VERIFICATION — Fix these issues in the regenerated code:"` string that the tests key on.
- **Low-value DUPLICATES (6 tests across two files).** Worth pruning in a later hygiene pass (Step 13.5) — `TestSkipOnRetry::test_gap_query_set_on_generated_artifact` + `TestParallelGeneration::test_already_passing_gaps_not_submitted_to_pool` in `test_mcp_tool_generator.py`; `test_absolute_path_in_skill_name_rejected` + `test_all_special_char_query_uses_forced_name` in `test_skill_generator.py`. Each is re-proving a claim a neighbor test already covers. Not harmful; net redundant.
- **One SUSPICIOUS item in `test_agent_generator.py::test_node_name_with_leading_underscore_is_valid`.** Test correctly documents that `agent.py`'s `_SNAKE_CASE_RE = r"^[a-z_][a-z0-9_]*$"` accepts leading underscore. But `mcp_tool.py`'s `_SNAKE_CASE_RE = r"^[a-z][a-z0-9_]*$"` rejects it (covered by `TestValidateToolName::test_starts_with_underscore`). Two regex constants with the same name and different rules in sibling generator files is a latent footgun — not a test bug, a source-of-truth drift. Worth a source-side comment cross-linking the two.
- **Import failures: none.** All test-file imports resolve against the current codebase. `generate_mcp_tool`, `generate_skill`, `generate_agent`, `_generate_single_*`, `_validate_*`, `slugify_gap`, `build_task_context`, `build_schema_context`, `is_first_step_for_gap`, `find_subtask_for_gap`, `AgentGenerationResult`, `LeadWithSubagentsResult`, `SubAgentConfig`, `GeneratedToolCode`, `_step_position_block`, `_strip_code_fences`, `extract_skill_name`, `validate_skill_content`, `_get_validate_skill`, `FIRST_STEP_DIRECTIVE`, `DOWNSTREAM_STEP_DIRECTIVE` — all present and public-surface-matched.
- **Integration tests gated correctly.** All 11 integration tests have `pytest.mark.skipif(not _has_api_key)` + `pytest.mark.integration`, and each resets the lazy LLM singleton via an autouse fixture. Safe to leave in place.
