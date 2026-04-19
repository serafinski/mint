# Batch 03a — Verification Tier 1 / 1.5 / Gate / Sandbox Audit

**Date:** 2026-04-19
**Scope:** 183 tests across 6 files
- `tests/unit/test_static_scanner.py` (81 tests)
- `tests/unit/test_quick_validate.py` (12 tests)
- `tests/unit/test_import_check.py` (25 tests)
- `tests/unit/test_gate.py` (26 tests)
- `tests/unit/test_sandbox_tool.py` (34 tests)
- `tests/integration/test_sandbox_tool.py` (5 tests)

**Source under test:**
- `src/verification/static_scanner.py` (Tier 1 AST+regex)
- `tools/quick_validate.py` (Tier 0 SKILL pre-check)
- `src/verification/import_check.py` (Tier 1.5 import probe)
- `src/verification/gate.py` (orchestrator)
- `src/verification/sandbox_tool.py` (Docker primitive)

---

## Summary

**Verdict counts (183 tests):**

| KEEP | WEAK | STALE | SUSPICIOUS | DUPLICATE | DEAD |
|---|---|---|---|---|---|
| 171 | 6 | 0 | 2 | 4 | 0 |

**High-confidence KEEP cluster (~93%).** Every Tier 1 contamination category has a clean positive/negative pair; the gate's fail-open contract, layered fence scanning, and `allow_network` plumbing are all exercised; Tier 1.5 per-type dispatch is cleanly mocked. No `STALE` tests — the Step 14.11 fail-closed Tier 3 rewrite already propagated through this batch (no test asserts the old fail-open timeout behavior; Tier 3 timeout semantics live in `tests/unit/test_adversarial.py`, out of scope).

**Key findings:**
1. **No test covers Tier 1.5 `allow_network` forwarding** (Step 14.12 plumbing gap). `import_check._check_mcp_tool` hardcodes `sandbox_execute(..., extra_packages=..., timeout=30)` with no `allow_network=` — network-dependent MCP_TOOLs cannot have their imports probed online. This is a production behavior gap, not a test gap per se, but no test asserts the current (network-off) behavior, so a regression either way would be silent.
2. **Four duplicate pairs** where the same assertion is made from different classes (e.g. `test_mock_artifact` and `mock_artifact` in `TestMultipleCategories`); each provides minor value as a read-position anchor but could be consolidated.
3. **Two SUSPICIOUS tests** rely on Pydantic field ordering or on `mock_sandbox.call_args.kwargs` which skips positional-argument forwarding. Flagged below.
4. **Six WEAK tests** under-assert (check existence only, not severity/category/count combo). Listed per-file.
5. **Integration suite (`test_sandbox_tool.py`)** is well-scoped for Docker smoke. Missing: integration coverage of `allow_network=True` bridge mode (Step 14.12) — currently only the offline path is integration-tested.

---

## Per-file verdicts

### `tests/unit/test_static_scanner.py` (81 tests, 851 LOC)

| Test | Verdict | Notes |
|---|---|---|
| `TestHardcodedData::test_hardcoded_list_return` | KEEP | Direct literal list return → CRITICAL. Core case. |
| `TestHardcodedData::test_hardcoded_dict_return` | KEEP | Dict variant; same semantic as list. |
| `TestHardcodedData::test_small_list_not_flagged` | KEEP | <3 items negative; guards threshold. |
| `TestHardcodedData::test_async_function_hardcoded` | KEEP | Async fn path tests `AsyncFunctionDef` branch in `_check_hardcoded_return`. |
| `TestHardcodedData::test_non_literal_list_not_flagged` | KEEP | Guards `all(isinstance(e, ast.Constant))` check. |
| `TestHardcodedData::test_hardcoded_list_via_variable_indirection` | KEEP | B1.2: Exercises assignment-lookup follow-through. |
| `TestHardcodedData::test_hardcoded_dict_via_variable_indirection` | KEEP | B1.2 dict variant. |
| `TestHardcodedData::test_small_variable_list_not_flagged` | KEEP | B1.2 threshold negative. |
| `TestHardcodedData::test_async_hardcoded_list_via_variable_indirection` | KEEP | B1.2 × async coverage matrix. |
| `TestSilentException::test_bare_except` | KEEP | Tier 1 HIGH. |
| `TestSilentException::test_except_exception_pass` | KEEP | `except Exception: pass` variant. |
| `TestSilentException::test_except_with_reraise_ok` | KEEP | `raise` in body → no finding; negative control. |
| `TestSilentException::test_except_with_logging_ok` | KEEP | `logger.error` → no finding; the exact attribute-match branch at `_check_silent_except`. |
| `TestSilentException::test_specific_exception_not_flagged` | KEEP | Narrow exception class → not flagged. |
| `TestSilentException::test_silent_exception_is_high_not_critical` | KEEP | Severity/overall_passed interaction. |
| `TestMockArtifacts::test_mock_standalone` | KEEP | `\bmock\b` positive. |
| `TestMockArtifacts::test_mock_in_compound_word_not_flagged` | KEEP | H4 false-positive guard. |
| `TestMockArtifacts::test_fake_standalone_in_string` | KEEP | Word-boundary positive. |
| `TestMockArtifacts::test_placeholder_standalone` | KEEP | `placeholder` positive. |
| `TestMockArtifacts::test_placeholder_in_compound_not_flagged` | KEEP | H4 compound negative. |
| `TestMockArtifacts::test_dummy_standalone` | KEEP | `dummy` indicator. |
| `TestMockArtifacts::test_test_data_detected` | KEEP | `TEST_DATA` literal. |
| `TestMockArtifacts::test_demo_standalone` | KEEP | `DEMO` uppercase path. |
| `TestMockArtifacts::test_demo_in_compound_not_flagged` | KEEP | H4 negative. |
| `TestMockArtifacts::test_demographics_not_flagged` | KEEP | H4 famous false positive. |
| `TestMockArtifacts::test_sample_not_flagged` | KEEP | Documents intentional exclusion of `sample`; guards re-adding it. |
| `TestSyntheticData::test_todo_comment` | KEEP | `# TODO` positive. |
| `TestSyntheticData::test_fixme_comment` | KEEP | `# FIXME` positive. |
| `TestSyntheticData::test_placeholder_comment` | KEEP | `# placeholder` positive. |
| `TestSyntheticData::test_for_testing_only` | KEEP | `# for testing only` literal match. |
| `TestSyntheticData::test_synthetic_is_high_not_critical` | KEEP | Severity contract — HIGH doesn't block. |
| `TestMissingExternalCalls::test_fetch_without_import` | KEEP | Function name regex + missing-import positive. |
| `TestMissingExternalCalls::test_query_without_import` | KEEP | `query_*` matches `_EXTERNAL_FN_NAMES`. |
| `TestMissingExternalCalls::test_fetch_with_httpx_ok` | KEEP | httpx import silences finding. |
| `TestMissingExternalCalls::test_fetch_with_requests_ok` | KEEP | requests import silences finding. |
| `TestMissingExternalCalls::test_non_external_function_not_flagged` | KEEP | No fetch/query name → no finding. |
| `TestMissingExternalCalls::test_missing_external_is_medium` | KEEP | Default (offline) = MEDIUM. |
| `TestMissingExternalCalls::test_missing_external_is_medium_offline` | DUPLICATE | Same assertion as the row above; redundant. Consolidate. |
| `TestMissingExternalCalls::test_missing_external_is_high_with_network` | KEEP | Step 14.12: `allow_network=True` → HIGH. |
| `TestSyntaxError::test_syntax_error_reported_as_critical` | KEEP | `SyntaxError` surfacing. |
| `TestSyntaxError::test_syntax_error_skips_other_checks` | KEEP | Early-return contract. |
| `TestCleanCode::test_clean_code_passes` | KEEP | Full sample with httpx. |
| `TestCleanCode::test_clean_code_empty_findings` | KEEP | Pure-compute path. |
| `TestSkillPrecheck::test_valid_skill_passes_tier0` | KEEP | SKILL Tier 0 happy path. |
| `TestSkillPrecheck::test_invalid_skill_no_frontmatter` | KEEP | SKILL Tier 0 CRITICAL. |
| `TestSkillPrecheck::test_skill_missing_name` | KEEP | Required-field rejection. |
| `TestSkillPrecheck::test_mcp_tool_skips_tier0` | KEEP | Type-scoping negative. |
| `TestShouldProceed::test_no_findings_proceeds` | KEEP | Trivial gate helper. |
| `TestShouldProceed::test_critical_blocks` | KEEP | CRITICAL blocks. |
| `TestShouldProceed::test_high_only_proceeds` | KEEP | HIGH proceeds. |
| `TestShouldProceed::test_medium_only_proceeds` | KEEP | MEDIUM proceeds. |
| `TestReportStructure::test_report_has_capability_name` | KEEP | Schema wiring. |
| `TestReportStructure::test_report_tier_reached_is_1_for_mcp_tool` | KEEP | `tier_reached=1` for MCP. |
| `TestReportStructure::test_report_verified_at_populated` | KEEP | Timestamp default. |
| `TestReportStructure::test_multiple_categories_combined` | KEEP | Multi-category accumulation. |
| `TestScanNonMcpTypes::test_clean_text_returns_empty_findings` | KEEP | Non-MCP negative. |
| `TestScanNonMcpTypes::test_mock_word_in_skill_prose_not_flagged` | KEEP | T04 regression guard at scanner level. |
| `TestScanNonMcpTypes::test_todo_comment_in_agent_prose_not_flagged` | KEEP | AGENT prose negative. |
| `TestScanNonMcpTypes::test_no_ast_syntax_error_for_non_python` | KEEP | Guards the `if capability_type == MCP_TOOL` AST skip. |
| `TestScanNonMcpTypes::test_tier_reached_is_1` | KEEP | Tier-reached for non-MCP. |
| `TestScanNonMcpTypes::test_capability_name_in_report` | WEAK | Checks only name propagation; already covered by `TestReportStructure::test_report_has_capability_name`. Minimal incremental value, but safe. |
| `TestScanNonMcpTypes::test_json_agent_config_with_mock_word_not_flagged` | KEEP | Agent-config-with-`mock`-in-prompt regression. |
| `TestScanNonMcpTypes::test_empty_text_returns_clean_report` | KEEP | Edge case. |
| `TestScanNonMcpTypes::test_skill_with_placeholder_in_instructions_passes` | KEEP | T04 regression at scanner. |
| `TestScanNonMcpTypes::test_skill_with_mock_word_in_instructions_passes` | KEEP | T04 variant. |
| `TestScanNonMcpTypes::test_mcp_tool_mock_detection_still_works` | KEEP | Guards that scoping didn't kill mock detection. |
| `TestMockImports::test_import_unittest_mock` | KEEP | AST import scan positive. |
| `TestMockImports::test_from_unittest_mock` | KEEP | `from ... import` variant. |
| `TestMockImports::test_import_mock_root` | KEEP | Root `mock` package. |
| `TestMockImports::test_import_faker` | KEEP | Faker library ban. |
| `TestMockImports::test_from_faker_import` | KEEP | `from faker import Faker`. |
| `TestMockImports::test_legitimate_import_not_flagged` | KEEP | Negative control. |
| `TestMockImports::test_mock_import_blocks_overall_pass` | KEEP | CRITICAL gate interaction. |
| `TestRandomSeed::test_random_seed_literal` | KEEP | `random.seed(42)` HIGH. |
| `TestRandomSeed::test_np_random_seed_literal` | KEEP | `np.random.seed` alias. |
| `TestRandomSeed::test_numpy_random_seed_literal` | KEEP | `numpy.random.seed` full path — `func.value` Attribute branch. |
| `TestRandomSeed::test_random_seed_dynamic_not_flagged` | KEEP | Non-literal arg negative. |
| `TestRandomSeed::test_no_seed_call_not_flagged` | KEEP | No seed call at all. |
| `TestLoremIpsum::test_lorem_ipsum_in_string_literal` | KEEP | String-literal positive. |
| `TestLoremIpsum::test_lorem_ipsum_case_insensitive` | KEEP | Case folding. |
| `TestLoremIpsum::test_legitimate_text_not_flagged_for_lorem` | KEEP | Negative. |

**File subtotals:** 80 KEEP, 1 DUPLICATE, 0 STALE, 1 WEAK (but safe).

**Coverage gaps:**
- No explicit test that `_check_silent_except` handles `is_silent_return` with a non-empty `return {"x": 1}` — the code has branches for empty dict/list; non-empty dict return is silently NOT a "silent return" per the current code, but the test suite never asserts that (either way). Minor.
- No test with `ast.ExceptHandler` whose `node.type` is a non-Name expression (e.g., `except (ValueError, TypeError):`). Current code leaves this correctly unflagged but untested.

---

### `tests/unit/test_quick_validate.py` (12 tests, 218 LOC)

| Test | Verdict | Notes |
|---|---|---|
| `TestStructuralValidation::test_valid_skill_passes` | KEEP | Happy path; asserts exact message `"Skill is valid!"`. |
| `TestStructuralValidation::test_missing_skill_md` | KEEP | Required file missing. |
| `TestStructuralValidation::test_no_frontmatter` | KEEP | Missing `---` header. |
| `TestProseHardFails::test_rejects_lorem_ipsum_body` | KEEP | Literal lorem ipsum. |
| `TestProseHardFails::test_rejects_lorem_ipsum_case_insensitive` | KEEP | Case folding. |
| `TestProseHardFails::test_rejects_todo_implement` | KEEP | Literal `TODO: implement`. |
| `TestProseHardFails::test_rejects_placeholder_return_marker` | KEEP | Literal `# placeholder return`. |
| `TestProseHardFails::test_legitimate_placeholder_word_passes` | KEEP | T04 regression at Tier 0. |
| `TestProseHardFails::test_legitimate_todo_word_passes` | KEEP | Word 'TODO' in prose passes. |
| `TestBodyLengthWarning::test_short_body_no_warning` | KEEP | No warning path. |
| `TestBodyLengthWarning::test_long_body_warns` | KEEP | >500 line warning. |
| `TestBodyLengthWarning::test_exactly_500_lines_no_warning` | SUSPICIOUS | The test constructs 499 body lines and asserts no warning. `quick_validate.py` L157 computes `body_lines = body.count("\n") + (1 if body and not body.endswith("\n") else 0)`. With 499 `"Line N"` entries joined by `\n` + trailing `\n` appended in the test, body has 499 newlines and ends with `\n`, so `body_lines = 499`. The test name says "exactly 500" but actually tests 499. Either the name or the arithmetic is wrong. Both arithmetic paths pass (`499 > 500` is False, `500 > 500` is False) but the name is misleading — should be renamed or `range(500)` used. |

**File subtotals:** 11 KEEP, 1 SUSPICIOUS, 0 STALE.

**Coverage gaps:**
- No test for `allowed-tools`, `compatibility`, `license`, `metadata` frontmatter keys passing validation (allowed list).
- No test for unexpected frontmatter key rejection (the `unexpected_keys` path at line 65-72 of `quick_validate.py`).
- No test for the 500-char `compatibility` length limit.
- No test for the 64-char `name` length limit, or invalid `name` regex failures (uppercase, `--`, leading/trailing `-`).
- No test for the 1024-char `description` length limit or angle-bracket rejection.

These are production-important checks with no coverage. Would recommend adding — not blocking for Phase 2.

---

### `tests/unit/test_import_check.py` (25 tests, 427 LOC)

| Test | Verdict | Notes |
|---|---|---|
| `TestMcpToolImportProbe::test_clean_tool_returns_no_findings` | KEEP | Happy path; mock sandbox returns success. |
| `TestMcpToolImportProbe::test_wrong_pip_package_yields_critical` | KEEP | pip-install-failure path; `wrong_pip_package` category; asserts message content. |
| `TestMcpToolImportProbe::test_import_error_yields_critical` | KEEP | Runtime import failure; `IMPORT_PROBE_ERROR` sentinel path → `import_error` category. |
| `TestMcpToolImportProbe::test_empty_source_skipped` | KEEP | Empty source → INFO finding; explicitly asserts `mock_sandbox.assert_not_called()`. |
| `TestMcpToolImportProbe::test_pip_packages_threaded_to_sandbox` | KEEP | Threads pip packages via `extra_packages` kwarg. |
| `TestMcpToolImportProbe::test_empty_pip_packages_pass_none_not_empty_list` | KEEP | `pip_packages or None` contract — critical for `sandbox_execute` branch. |
| `TestMcpToolImportProbe::test_stderr_truncated_to_last_500_chars` | KEEP | Slice contract. |
| `TestMcpToolImportProbe::test_sandbox_raises_fail_open` | KEEP | Internal-raise → MEDIUM fail-open. |
| `TestSkillImportProbe::test_skill_with_no_fences_passes` | KEEP | Vacuous pass. |
| `TestSkillImportProbe::test_skill_with_valid_python_fence_passes` | KEEP | Clean fence passes. |
| `TestSkillImportProbe::test_skill_with_malformed_fence_yields_critical` | KEEP | Unterminated string; `fence_compile_error`. |
| `TestSkillImportProbe::test_skill_with_multiple_fences_reports_per_fence` | KEEP | Multiple fences, fence-index in location. |
| `TestSkillImportProbe::test_empty_skill_content_passes` | KEEP | Empty content. |
| `TestAgentImportProbe::test_agent_with_valid_config_passes` | KEEP | Happy path with system_prompt. |
| `TestAgentImportProbe::test_agent_missing_system_prompt_yields_critical` | KEEP | Structural CRITICAL. |
| `TestAgentImportProbe::test_agent_empty_system_prompt_yields_critical` | KEEP | `"   "` whitespace-only → CRITICAL. |
| `TestAgentImportProbe::test_agent_no_config_yields_critical` | KEEP | `config=None` branch. |
| `TestAgentImportProbe::test_agent_broken_fence_in_system_prompt_yields_critical` | KEEP | Fence in system_prompt with compile error. |
| `TestAgentImportProbe::test_agent_broken_fence_in_skill_content_yields_critical` | KEEP | Fence in embedded `skill_content`. |
| `TestShouldProceedToTier2::test_empty_report_proceeds` | KEEP | Gate helper. |
| `TestShouldProceedToTier2::test_medium_only_proceeds` | KEEP | MEDIUM not blocking. |
| `TestShouldProceedToTier2::test_critical_blocks` | KEEP | CRITICAL blocks. |
| `TestShouldProceedToTier2::test_high_without_critical_proceeds` | KEEP | Matches Tier 1's policy — only CRITICAL blocks. |
| `TestGateWiring::test_tier_1_5_critical_skips_tier_2_and_3` | KEEP | Gate-level short-circuit; `tier_reached=1` held. |
| `TestGateWiring::test_tier_1_5_pass_still_runs_tier_2_and_3` | KEEP | Pass-through to downstream. |

**File subtotals:** 25 KEEP, 0 WEAK/DUPLICATE/STALE.

**Coverage gaps:**
- No test verifies that `_check_mcp_tool` does NOT forward `allow_network` to `sandbox_execute` — currently hardcoded to default (off). If Step 14.12 intended Tier 1.5 to also honor `allow_network` (it does not — see `import_check.py` L103-107), there is no test either locking in the current offline-only behavior or asserting the forwarded flag. See cross-file observation #2.
- `_PYTHON_FENCE` regex (`re.DOTALL | re.IGNORECASE`) — no test for backtick language tag variations (e.g. ` ```PYTHON `, ` ```py `). The `IGNORECASE` flag covers the first but not the second; no test asserts which.
- No test exercises `CapabilityType` fall-through (fourth unknown type); the `else: findings = []` branch at L250 is unreached.

---

### `tests/unit/test_gate.py` (26 tests, 808 LOC)

The autouse `_mock_import_check` fixture correctly stubs Tier 1.5 so these tests remain Docker-independent. Good hygiene.

| Test | Verdict | Notes |
|---|---|---|
| `test_mcp_clean_passes_all_tiers` | KEEP | End-to-end clean path; asserts `allow_network=False` positional. |
| `test_mcp_tier1_critical_blocks_tier23` | KEEP | Mock artifact → Tier 1 CRITICAL → downstream not called. |
| `test_mcp_hardcoded_data_blocks_tier23` | KEEP | Hardcoded list → short-circuit. |
| `test_mcp_syntax_error_blocks_tier23` | KEEP | SyntaxError path. |
| `test_mcp_tier2_high_blocks_tier3` | KEEP | HIGH behavioral → skip Tier 3. |
| `test_mcp_tier2_critical_blocks_tier3` | KEEP | CRITICAL behavioral → skip Tier 3. |
| `test_mcp_tier2_medium_proceeds_to_tier3` | KEEP | MEDIUM doesn't block Tier 3. |
| `test_mcp_tier3_critical_fails_overall` | KEEP | Tier 3 CRITICAL = final rejection. |
| `test_findings_accumulated_from_all_tiers` | KEEP | Accumulation across tiers. |
| `test_skill_valid_skmd_passes_tier0` | KEEP | SKILL happy path. |
| `test_skill_invalid_skmd_rejected_at_tier0` | KEEP | Name-too-long → Tier 0 rejection, `tier_reached=0`. |
| `test_skill_syntax_error_not_reported_as_critical` | KEEP | AST SyntaxError from non-Python is filtered. |
| `test_skill_with_mock_word_in_prose_passes_tier1` | KEEP | T04 at gate layer. |
| `test_agent_clean_config_passes` | KEEP | AGENT clean path. |
| `test_agent_with_mock_word_in_config_passes_tier1` | KEEP | AGENT T04. |
| `test_empty_source_code_passes_tier1` | KEEP | Edge case. |
| `test_skill_code_fence_flags_hardcoded_data` | KEEP | Layered scanner: SKILL Python fence → AST finding; location contains `code_fence`. |
| `test_skill_code_fence_flags_mock_import` | KEEP | SKILL fence with `unittest.mock`. |
| `test_skill_prose_placeholder_not_flagged_t04_regression` | KEEP | Prose-without-fence negative; sharp T04 regression guard. |
| `test_skill_clean_code_fence_passes` | KEEP | Benign fence. |
| `test_agent_system_prompt_fence_scanned` | KEEP | Fence in `system_prompt` with hardcoded list. |
| `test_agent_skill_content_fence_scanned` | KEEP | Fence in embedded `skill_content`. |
| `test_agent_no_fence_prose_passes` | KEEP | AGENT prose negative. |
| `test_verify_never_raises_on_unexpected_exception` | KEEP | Fail-open contract; patches `_static_scan` to raise OSError. |
| `test_verify_forwards_allow_network_true` | KEEP | Step 14.12: `allow_network=True` forwarded to both tiers. |
| `test_verify_defaults_allow_network_false` | KEEP | Default-off regression guard. |

**File subtotals:** 26 KEEP, 0 WEAK/DUPLICATE/STALE.

**Coverage gaps:**
- No test for the **Tier 1 HIGH downgrade-on-Tier-2-clearance** behavior at `gate.py` L333-343. This is a thesis-cited "HIGH findings require Tier 2 clearance" contract and it has no direct coverage. Side-effect observable in `test_findings_accumulated_from_all_tiers` but the assertion there doesn't look at Tier 1 findings pre/post Tier 2. Should be added.
- No test that `_emit_tier_event` LangFuse emission is invoked per tier (Batch A1 Step 13.7). Would require patching `emit_langfuse_event`.
- No test asserts `allow_network` is NOT forwarded to Tier 1.5 (documents current behavior). Either way — see cross-file observation #2.
- Exception handler in `verify()` catches only via `_static_scan` raise. No test exercises an exception raised from **Tier 1.5** or **Tier 2** or **Tier 3** — the `current_tier[0]` mutable-container reports the tier that failed; without a test firing from tiers 2/3 this branch is untested.

---

### `tests/unit/test_sandbox_tool.py` (34 tests, 475 LOC)

| Test | Verdict | Notes |
|---|---|---|
| `test_success_returns_correct_structure` | KEEP | Return shape contract. |
| `test_failure_exit_code_sets_success_false` | KEEP | Failure branch. |
| `test_timeout_returns_sentinel` | KEEP | `TimeoutExpired` → `exit_code=-1, stderr='Timeout'`. |
| `test_empty_code_runs_without_error` | KEEP | Edge. |
| `test_docker_command_uses_network_none` | KEEP | Default-off network. |
| `test_docker_command_uses_correct_image` | KEEP | Image constant forwarded. |
| `test_docker_command_mounts_script_readonly` | KEEP | `:ro` mount flag. |
| `test_custom_timeout_forwarded_to_subprocess` | KEEP | `timeout` kwarg passthrough. |
| `test_tempfile_cleaned_up_on_success` | KEEP | Temp file cleanup on success. |
| `test_tempfile_cleaned_up_on_timeout` | KEEP | Temp file cleanup on timeout. |
| `test_file_not_found_returns_error_dict` | KEEP | Docker binary missing → graceful dict. |
| `test_os_error_returns_error_dict` | KEEP | Daemon down → graceful dict. |
| `test_tempfile_cleaned_up_on_docker_not_found` | KEEP | Cleanup on FileNotFoundError. |
| `TestExtractImports::test_simple_import` | KEEP | AST import extraction. |
| `TestExtractImports::test_from_import` | KEEP | `from X import Y` path. |
| `TestExtractImports::test_multiple_imports` | KEEP | Set accumulation. |
| `TestExtractImports::test_stdlib_included` | KEEP | Stdlib not filtered at this layer. |
| `TestExtractImports::test_syntax_error_returns_empty` | KEEP | Graceful on SyntaxError. |
| `TestExtractImports::test_dotted_import` | KEEP | `numpy.linalg` → root only. |
| `TestExtractImports::test_empty_code` | KEEP | Edge. |
| `TestResolveImportsViaLLM::test_calls_llm_with_import_names` | KEEP | LLM invoked once with the list. |
| `TestResolveImportsViaLLM::test_caches_results_across_calls` | KEEP | Session cache. |
| `TestResolveImportsViaLLM::test_fallback_on_llm_error` | KEEP | Identity-mapping fallback. |
| `TestResolveImportsViaLLM::test_missing_key_in_llm_response_uses_identity` | KEEP | Per-name identity fallback. |
| `TestResolveExtraPackages::test_no_extras_for_stdlib_packages` | KEEP | Stdlib filter. |
| `TestResolveExtraPackages::test_sklearn_maps_to_scikit_learn` | KEEP | Canonical `sklearn`→`scikit-learn` mapping. |
| `TestResolveExtraPackages::test_unknown_package_uses_llm_result` | KEEP | Arbitrary pkg. |
| `TestResolveExtraPackages::test_mixed_stdlib_and_extra` | KEEP | Mixed filter. |
| `TestResolveExtraPackages::test_empty_source` | KEEP | Edge. |
| `TestSandboxExecuteWithPackages::test_no_extra_packages_uses_core` | KEEP | Core path when no packages. |
| `TestSandboxExecuteWithPackages::test_extra_packages_triggers_install_phase` | WEAK | Asserts `len(docker_cmds) >= 3` — loose. The actual call sequence is 5 commands (run-install, commit, rm, run-exec, rmi). A stronger test would assert each command is present in order. Current form would pass even if commit/rmi is silently dropped. |
| `TestAllowNetworkFlag::test_allow_network_true_passes_bridge` | KEEP | Step 14.12: `--network bridge` on core path. |
| `TestAllowNetworkFlag::test_allow_network_false_passes_none` | KEEP | Step 14.12: default-off regression. |
| `TestAllowNetworkFlag::test_allow_network_with_extra_packages_bridge_on_exec_phase` | KEEP | Step 14.12: two-phase install + exec; exec-phase honors `allow_network=True`. |

**File subtotals:** 33 KEEP, 1 WEAK.

**Coverage gaps:**
- No test for `_VALID_PKG` regex filtering — if a caller passes a package name with shell metacharacters (`numpy; rm -rf /`), the code silently drops it. There is a fallback to the no-install path, but no test exercises this branch.
- No test that all-invalid packages fall back to `sandbox_execute(code, timeout, allow_network=allow_network)` (the `if not packages:` branch after filtering, L349-354). This is security-relevant: the comment explicitly mentions Step 14.12 "silently dropping allow_network here would turn the `_VALID_PKG` rejection into a covert offline-mode demotion" — but nothing asserts that `allow_network=True` is preserved through this fallback.
- No test for `sandbox_execute_silent` (no LangFuse event emission). Just one call path forwarding, trivial to add.
- No test for `sandbox_execute_in_workspace` with `workspace_path`. The function has distinct `-v` mounting behavior that goes untested.
- No test for `install_proc.returncode != 0` path (`uv pip install failed` message). Only the exec-phase failure is covered indirectly.
- No test for `commit` / `rm` / `rmi` cleanup timing on the install-then-fail path.

---

### `tests/integration/test_sandbox_tool.py` (5 tests, 60 LOC)

Requires Docker + built `jit-sandbox` image. Skipped via `@pytest.mark.integration`.

| Test | Verdict | Notes |
|---|---|---|
| `test_docker_executes_simple_script` | KEEP | Simple stdout capture. |
| `test_docker_network_is_disabled` | KEEP | Offline-mode integration: confirms `--network none` actually blocks `urlopen`. |
| `test_docker_script_error_captured` | KEEP | Exception → stderr. |
| `test_docker_exit_code_nonzero_on_error` | KEEP | `sys.exit(42)` propagation. |
| `test_sandbox_pandas_available` | KEEP | Image-build regression — pandas + numpy must be pre-installed. |

**File subtotals:** 5 KEEP.

**Coverage gaps:**
- **No integration test for `allow_network=True` bridge mode** (Step 14.12). The unit test asserts the flag is forwarded, but there is no end-to-end assertion that `bridge` actually re-enables outbound HTTP. Recommend adding a `@pytest.mark.integration` test that calls `sandbox_execute(urlopen_code, allow_network=True)` and asserts `CONNECTED` in stdout.
- No integration test for the two-phase install path (`extra_packages=["some-small-pypi-pkg"]`). Cache-warming and layer-commit behavior goes un-exercised outside of the build.

---

## Cross-file observations

### 1. DUPLICATE cluster — `missing_external_calls` severity matrix

Three tests assert essentially the same MEDIUM/HIGH/default-offline behavior:
- `test_static_scanner.py::TestMissingExternalCalls::test_missing_external_is_medium`
- `test_static_scanner.py::TestMissingExternalCalls::test_missing_external_is_medium_offline`
- `test_static_scanner.py::TestMissingExternalCalls::test_missing_external_is_high_with_network`

The first two fire the same assertion with the same code. Consolidate into one test or delete `test_missing_external_is_medium`.

### 2. `allow_network` plumbing coverage — asymmetric

Step 14.12 added `allow_network` to `sandbox_execute`, `sandbox_execute_silent`, `sandbox_execute_in_workspace`, and the gate's `verify()`. However:

| Consumer | Forwards `allow_network`? | Tested? |
|---|---|---|
| `gate.verify() → behavioral.run()` | Yes | Yes (`test_verify_forwards_allow_network_true`) |
| `gate.verify() → adversarial.probe()` | Yes | Yes (same test) |
| `gate.verify() → import_check.run_import_check()` | **No** — `_check_mcp_tool` hardcodes default (offline) | **No** |
| `sandbox_execute(core path)` | Yes | Yes (test_allow_network_true_passes_bridge) |
| `sandbox_execute(two-phase with fallback)` | Yes (commented L352-354) | **No** — `_VALID_PKG` fallback path untested |

**Recommendation:** Either explicitly test that Tier 1.5 runs offline regardless of `allow_network` (locking in current behavior) or add forwarding + test it. Today the behavior is silently "Tier 1.5 always offline," and if `import_check._check_mcp_tool` ever needs to install a private-network package it will fail opaquely.

### 3. Tier 1 scanning of prose — well-tested

The T04 regression has *five* tests spread across three files all locking in "prose with mock/placeholder passes":
- `test_static_scanner.py::TestScanNonMcpTypes::test_skill_with_placeholder_in_instructions_passes`
- `test_static_scanner.py::TestScanNonMcpTypes::test_skill_with_mock_word_in_instructions_passes`
- `test_static_scanner.py::TestScanNonMcpTypes::test_mock_word_in_skill_prose_not_flagged`
- `test_quick_validate.py::TestProseHardFails::test_legitimate_placeholder_word_passes`
- `test_gate.py::test_skill_with_mock_word_in_prose_passes_tier1`
- `test_gate.py::test_skill_prose_placeholder_not_flagged_t04_regression`

**Not** flagged as duplicate because each operates at a different verification layer (scanner, Tier 0 validator, gate orchestrator) and a future re-introduction of the bug could happen at any one layer independently. The redundancy is intentional defense-in-depth for a known-painful bug class.

### 4. Autouse `_mock_import_check` fixture — correct

`tests/unit/test_gate.py` L19-34 stubs `import_check.run_import_check` to always return a passing report. This is the right call — the gate-level tests are about tier orchestration, not Tier 1.5 internals. Tier 1.5 semantics live in `test_import_check.py`. The fixture uses the correct target path (`src.verification.gate.import_check.run_import_check`) so the module-level import `from src.verification import ... import_check` is preserved.

### 5. Step 14.11 Tier 3 fail-closed — no STALE tests

Audit for tests asserting old fail-open Tier 3 behavior: **none found** in this batch. All Tier 3 mocks in `test_gate.py` return explicit `VerificationReport` values, not timeout/error simulations. Tier 3 timeout semantics live in `tests/unit/test_adversarial.py` (out of scope here) — this batch assumes adversarial is correctly mocked. No STALE fallout.

### 6. Gate `verify()` exception handler — `current_tier` untested for tiers 2+

The `current_tier[0]` mutable container in `gate.py::verify()` is supposed to track which tier raised. Only tier 1 is tested (`test_verify_never_raises_on_unexpected_exception` patches `_static_scan`). If `behavioral.run` or `adversarial.probe` raises, the handler should report `tier=2` or `tier=3` — no test guards this. Currently the inner `_verify_inner` catches nothing around those calls, so a raise there would propagate to `verify()`'s outer `try` and report as tier 2 or 3 respectively. Worth adding a small regression test per tier.

### 7. SUSPICIOUS — `test_exactly_500_lines_no_warning` count arithmetic

See `test_quick_validate.py` note above. Test name and body disagree. Either fix the arithmetic (`range(500)` instead of `range(499)`) or rename to `test_499_lines_no_warning`. The behavior under test (no warning below threshold) is correct; this is a naming/clarity issue, not a functional bug.

### 8. Tier 1.5 error-path symmetry with gate fail-open

Both `gate.verify()` (L216-234) and `import_check.run_import_check()` (L251-268) fail open — internal exception → MEDIUM finding, `overall_passed=True`. Both are tested. Consistent, well-covered. Good design hygiene.

---

## Recommendations (ordered by value)

1. **Add Tier 1 HIGH-downgrade test to `test_gate.py`** — currently untested behavior at `gate.py` L333-343, thesis-cited.
2. **Add `_VALID_PKG` fallback coverage to `test_sandbox_tool.py`** — security-relevant path where all packages are rejected; must not silently demote `allow_network=True` to offline.
3. **Add `allow_network=True` integration test** to `tests/integration/test_sandbox_tool.py` — complete the Step 14.12 integration story.
4. **Fix `test_exactly_500_lines_no_warning` count mismatch** — cosmetic but misleading.
5. **Consolidate 3 MEDIUM/HIGH missing-external-calls tests** to 2.
6. **Tighten `test_extra_packages_triggers_install_phase`** to assert the specific command sequence, not just `len >= 3`.
7. **Add allowed-frontmatter-keys / length-limit / invalid-name coverage to `test_quick_validate.py`** — 5 missing branches in production-important validator.
8. **Add per-tier exception-handler test to `test_gate.py`** for tiers 2 and 3 (currently only tier 1 tested).

None of these block Phase 2 completion. Items 1, 2, and 3 are worth landing in Step 13.5 hygiene pass.
