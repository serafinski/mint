# Batch 03b — Verification Tier 2 / Tier 3 / verification_node

**Scope.** Audit of three test files covering Pillar 2's Tier 2 behavioral, Tier 3 adversarial, and the 3x retry loop with strict-gate semantics.

- `tests/unit/test_behavioral.py` — 59 tests / 804 LOC
- `tests/unit/test_adversarial.py` — 36 tests / 894 LOC
- `tests/unit/test_verification_node.py` — 25 tests / 461 LOC

Cross-referenced against:
- `src/verification/behavioral.py` (Tier 2; categories verified against source lines 193-764)
- `src/verification/adversarial.py` (Tier 3; Step 14.11 / 14.12 semantics verified)
- `src/orchestrator/verification_node.py` (Step 14 §12 #14 strict-gate)

---

## Summary

- **120 tests audited.** No DEAD, STALE, or SUSPICIOUS tests found. One DUPLICATE pair (minor), two WEAK tests (both weak in a deliberate / defensible way).
- **All 6 "special flag" risks came back clean:**
  - Step 14.11 timeout fail-CLOSED is correctly asserted (3 tests verify `overall_passed=False` + HIGH severity + retry-loop integration).
  - Step 14.12 `allow_network` plumbing + network-disconnect differential is fully covered (5 tests, categories `network_silent_fallback` and `network_disconnect_differential` match source verbatim).
  - Strict-gate §12 #14 semantics asserted (fail-closed drop + sticky flag + VERIFICATION_FAILED error marker).
  - **No references to stale categories** (`latency_variance` or `network_disconnect` without `_differential`) anywhere in the three files. The renames landed cleanly.
- **All category strings used in assertions are live** — cross-checked against the 11 canonical categories listed in the audit brief.
- The one genuinely behaviorally-redundant pair is `TestBuildSandboxCode::test_main_guard_neutralized_before_source` vs `test_main_guard_neutralized_end_to_end_subprocess` — but this is deliberate (Step 14 Gap A rationale: lexical check + runtime check form a complementary pair, not a duplicate). I flag it as DUPLICATE-by-letter / KEEP-by-intent with explanation.

## Special-flag cross-check (verified live)

| Flag / invariant | Verified by | Status |
|---|---|---|
| Step 14.11 Tier 3 timeout → fail-CLOSED (HIGH) | `test_timeout_report_fail_closed_high_severity`, `test_timeout_returns_fail_closed_report`, `test_timeout_report_trips_retry_loop`, `test_retry_converges_on_second_attempt` | ALL GREEN |
| Step 14.11 error branch still fail-OPEN | `test_error_report_still_fail_open`, `test_non_timeout_exception_still_fail_open`, `test_agent_error_returns_error_report` | ALL GREEN |
| Step 14.11 timeout ceiling = 480s | `test_adversarial_timeout_ceiling_is_480s` | GREEN |
| Step 14.12 `allow_network` threaded through Tier 2 | `TestAllowNetworkPropagates` (2 tests) | GREEN |
| Step 14.12 network-disconnect differential fires only when `needs_network=True AND allow_network=True` | `TestNetworkDisconnectDifferential` (4 tests) | GREEN |
| §12 #14 fail-closed drop + sticky flag | `test_max_attempts_drops_failed_artifacts`, `test_max_attempts_sets_sticky_fail_flag`, `test_max_attempts_records_fail_closed_error`, `test_max_attempts_all_pass_keeps_sticky_flag_true`, `test_retry_below_max_does_not_flip_sticky_flag` | ALL GREEN |
| Stale `latency_variance` / `network_disconnect` (without `_differential`) references | grepped, none found | CLEAN |

---

## Per-file verdict tables

### `tests/unit/test_behavioral.py` (59 tests)

| Class / test | Verdict | Note |
|---|---|---|
| `TestExtractParameterSchema::test_annotated_params` | KEEP | AST extraction for Annotated[int, Field]. |
| `TestExtractParameterSchema::test_plain_type_annotations` | KEEP | Covers 6 primitive types including list/dict. |
| `TestExtractParameterSchema::test_no_params` | KEEP | Empty-schema contract. |
| `TestExtractParameterSchema::test_private_function_skipped` | KEEP | Covers the `_`-prefix skip in source line 49. |
| `TestExtractParameterSchema::test_syntax_error_returns_empty` | KEEP | Covers the `except SyntaxError` at source line 43. |
| `TestExtractParameterSchema::test_sync_function_skipped` | KEEP | Covers the `AsyncFunctionDef` restriction at source line 47. |
| `TestExtractParameterSchema::test_unannotated_param` | KEEP | Covers the `else "unknown"` branch at source line 64. |
| `TestExtractFunctionName::test_finds_public_async_function` | KEEP | Primary happy path. |
| `TestExtractFunctionName::test_skips_private` | KEEP | Private skip branch. |
| `TestExtractFunctionName::test_syntax_error` | KEEP | SyntaxError branch. |
| `TestInputGenerators::test_boundary_inputs` | KEEP | |
| `TestInputGenerators::test_negative_inputs` | KEEP | |
| `TestInputGenerators::test_large_inputs` | KEEP | |
| `TestInputGenerators::test_unknown_type_defaults_to_empty_string` | KEEP | Asserts the unknown-type contract across all 3 generators (valuable because it pins the `_test_output_variation` "inputs_a == inputs_b" early-return). |
| `TestBuildSandboxCode::test_contains_asyncio_run` | KEEP | Smoke. |
| `TestBuildSandboxCode::test_contains_function_call` | KEEP | Smoke. |
| `TestBuildSandboxCode::test_contains_source_code` | KEEP | Smoke. |
| `TestBuildSandboxCode::test_main_guard_neutralized_before_source` | KEEP (DUPLICATE-by-letter w/ subprocess test below, KEEP-by-intent) | Lexical-position check. Deliberately paired with the Gap A subprocess test per Step 14 rationale (see CLAUDE.md & MEMORY.md — "string-position + runtime" pair guards silent no-op regressions at two layers). Document the pairing if either is ever removed in isolation. |
| `TestBuildSandboxCode::test_main_guard_neutralized_end_to_end_subprocess` | KEEP | Step 14 Gap A runtime guard. Runs the built code through `sys.executable -c` and asserts (1) exit 0, (2) no "main fired" in stderr, (3) well-formed JSON with `result == "42"`. Load-bearing for Pillar 2 Tier 2 integrity — without it, a regression that moves the `__name__` override after the source block would pass the lexical test but fire `mcp.run()` at runtime, silently no-op-ing every behavioral test. |
| `TestRunCleanTool::test_clean_tool_no_critical_findings` | KEEP | End-to-end clean path. |
| `TestRunCleanTool::test_all_findings_are_tier_2` | KEEP | Asserts tier=2 invariant across `run()` output. |
| `TestRunNoSourceCode::test_no_source_code_returns_info` | KEEP | Covers both `""` (falsy-str) and `None` paths. |
| `TestRunNoPublicFunction::test_private_only_returns_medium` | KEEP | Covers "no public async function" branch at source lines 596-604. |
| `TestRunNoParams::test_parameterless_tool_with_nonempty_output_produces_medium` | KEEP | B2.1 parameterless smoke full-pipeline. |
| `TestRunNoParams::test_parameterless_tool_with_none_output_no_finding` | KEEP | Trivial-output contract. |
| `TestRunNoParams::test_parameterless_tool_sandbox_failure_no_finding` | KEEP | Source line 206: `parsed is None` → `return []`. |
| `TestRunNoParams::test_parameterless_tool_with_error_no_finding` | KEEP | Source line 206: `parsed.get("error") is not None` → `return []`. |
| `TestRunInconsistentTool::test_inconsistent_results_produce_high_finding` | KEEP | Consistency category + HIGH severity. Hand-counted call sequence (calls 2 and 3 flip). Fragile but the comment documents the call order; acceptable. |
| `TestRunTimeoutViolation::test_slow_tool_produces_timeout_finding` | KEEP | `timeout` category, MEDIUM. Class docstring says "(renamed from latency, 5s)" — the rename is a historical note, the actual category in source is still `timeout`. |
| `TestRunBoundaryTimeout::test_timeout_on_boundary_produces_high_finding` | KEEP | Boundary timeout branch (source lines 421-430). |
| `TestRunSandboxFailure::test_all_sandbox_failures_handled_gracefully` | WEAK | Only asserts `isinstance(findings, list)` — doesn't validate the dependency-failure branch. Could be tightened to `assert all(f.severity in (...) for f in findings)` or assert at least one `dependency`/`boundary` finding surfaces. Not dead — exercises the `not result.get("success")` branches. Flagged for possible tightening. |
| `TestRunNonexistentEntity::test_silent_none_return_produces_finding` | KEEP | `nonexistent_entity` category + MEDIUM + "silently swallow" message. |
| `TestParameterlessSmoke::test_nonempty_output_produces_medium` | KEEP | Helper-level unit. |
| `TestParameterlessSmoke::test_trivial_none_output_no_finding` | KEEP | |
| `TestParameterlessSmoke::test_trivial_empty_string_no_finding` | KEEP | Pins the `""` entry of `_TRIVIAL_RESULTS`. |
| `TestParameterlessSmoke::test_sandbox_failure_no_finding` | KEEP | |
| `TestParameterlessSmoke::test_tool_error_no_finding` | KEEP | |
| `TestParameterlessSmoke::test_finding_is_tier_2` | KEEP | Tier invariant. |
| `TestOutputVariation::test_identical_nontrivial_outputs_flagged` | KEEP | B2.3 "ignoring parameters" branch. |
| `TestOutputVariation::test_different_outputs_no_finding` | KEEP | Negative case. |
| `TestOutputVariation::test_sandbox_failure_no_finding` | KEEP | `parsed is None` branch. |
| `TestOutputVariation::test_trivial_identical_outputs_not_flagged` | KEEP | Pins the `_TRIVIAL_RESULTS` guard in source line 276. |
| `TestOutputVariation::test_empty_schema_returns_no_findings` | KEEP | Source line 236 early return. |
| `TestOutputVariation::test_schema_with_identical_boundary_and_large_no_findings` | KEEP | Source line 242 `inputs_a == inputs_b` early return. |
| `TestOutputVariation::test_finding_is_tier_2` | KEEP | Tier invariant. |
| `TestTimeoutHelper::test_slow_execution_flagged_as_timeout` | KEEP | `_test_timeout` helper direct. |
| `TestTimeoutHelper::test_fast_execution_no_finding` | KEEP | |
| `TestTimeoutHelper::test_empty_schema_returns_no_findings` | KEEP | |
| `TestLatencyVariance::test_latency_flags_fast_uniform` | KEEP | Two-condition gate happy path (mean<1ms AND cv<0.10). |
| `TestLatencyVariance::test_latency_ignores_slow` | KEEP | First-condition failure. |
| `TestLatencyVariance::test_latency_ignores_variable` | KEEP | Second-condition failure (cv≥0.10). |
| `TestLatencyVariance::test_latency_handles_insufficient_samples` | KEEP | Source line 531 guard. |
| `TestLatencyVariance::test_latency_skips_empty_schema` | KEEP | Also asserts `mock_sandbox.call_count == 0` — strong early-return guard. |
| `TestAllowNetworkPropagates::test_allow_network_true_forwards_to_dep_check` | KEEP | Step 14.12 plumbing — dep-check receives `allow_network=True`. |
| `TestAllowNetworkPropagates::test_allow_network_false_by_default` | KEEP | Negative: asserts no call leaks `allow_network=True`. |
| `TestNetworkDisconnectDifferential::test_differential_flags_silent_fallback` | KEEP | F2 silent-fallback detector — CRITICAL + `network_silent_fallback` category. |
| `TestNetworkDisconnectDifferential::test_differential_legitimate_network_tool_passes` | KEEP | `network_disconnect_differential` INFO branch (on=ok, off=fail). |
| `TestNetworkDisconnectDifferential::test_differential_skipped_when_needs_network_false` | KEEP | Gate: `needs_network=False` → neither category fires. |
| `TestNetworkDisconnectDifferential::test_differential_skipped_when_allow_network_false` | KEEP | Gate: offline run → vacuous → both categories silent. |

#### File-level observations

- **Call-count-indexed side effects** (`TestRunInconsistentTool`, `TestRunBoundaryTimeout`) are fragile to reordering inside `run()`. Both tests explicitly document the call order in comments. If anyone reorders the `findings.extend(...)` calls in `behavioral.py::run`, these will false-flag. Worth a follow-up that indexes by input-payload inspection rather than call ordinal, but not urgent.
- **The `TestRunTimeoutViolation` class docstring says "(renamed from latency, 5s)"** — this is a historical note, not a stale reference. The category string in source is still `timeout` and the test asserts `category == "timeout"`. NOT stale.
- No references to `latency_variance` (old name) or standalone `network_disconnect` (old name) anywhere in the file — rename cleanup is complete.

### `tests/unit/test_adversarial.py` (36 tests)

| Class / test | Verdict | Note |
|---|---|---|
| `TestFindingsToReport::test_clean_findings_produce_empty_report` | KEEP | Filter contract: non-suspicious dropped. |
| `TestFindingsToReport::test_suspicious_findings_produce_tier3_findings` | KEEP | CRITICAL → `overall_passed=False`; tier=3 invariant. |
| `TestFindingsToReport::test_suspicious_high_only_passes` | KEEP | Pins the "only CRITICAL fails" contract at source line 385. |
| `TestPartialReports::test_timeout_report_fail_closed_high_severity` | KEEP | Step 14.11 fail-CLOSED. Docstring explicitly cites thesis contract. |
| `TestPartialReports::test_error_report_still_fail_open` | KEEP | Step 14.11 deliberate fail-open on non-timeout errors. |
| `TestPartialReports::test_adversarial_timeout_ceiling_is_480s` | KEEP | Constant regression guard. Docstring correctly cites Step 14.11 rationale. |
| `TestProbe::test_successful_probe_clean` | KEEP | |
| `TestProbe::test_successful_probe_suspicious` | KEEP | Covers the 2-of-3 filter — categories `hidden_demo_mode` + `ground_truth` surface. |
| `TestProbe::test_timeout_returns_fail_closed_report` | KEEP | Step 14.11 end-to-end via a 0.01s patched `ADVERSARIAL_TIMEOUT_S` + blocking `time.sleep(5)` invoke. Real concurrent.futures.TimeoutError path. |
| `TestProbe::test_agent_error_returns_error_report` | KEEP | Non-timeout exception → fail-open MEDIUM. |
| `TestProbe::test_no_structured_response` | KEEP | `structured_response=None` branch. |
| `TestProbe::test_empty_source_code_skips` | KEEP | `adversarial_skip` category. |
| `TestProbe::test_skill_with_python_fence_probes` | KEEP | Fix 2c-3 completeness: SKILL + fence → probed. |
| `TestProbe::test_skill_pure_prose_skips_with_specific_message` | KEEP | SKILL + no fences → skip-with-specific-message. Asserts both agent-not-created AND skip-category. |
| `TestProbe::test_agent_with_python_fence_in_system_prompt_probes` | KEEP | Critical completeness-fix test (pre-fix AGENT ALWAYS skipped). Load-bearing. |
| `TestProbe::test_agent_with_python_fence_in_skill_content_probes` | KEEP | Covers the second fence source for AGENT at `adversarial.py` line 530-531. |
| `TestProbe::test_agent_without_python_fences_skips_with_specific_message` | KEEP | Negative: asserts "agent_config" is in skip message (post-mortem disambiguation). |
| `TestProbe::test_agent_creation_failure` | KEEP | ImportError → fail-open. |
| `TestProbe::test_recursion_limit_passed_to_config` | KEEP | Threads `recursion_limit=50` through to `agent.invoke` config. |
| `TestTimeoutTriggersRetry::test_timeout_report_trips_retry_loop` | KEEP | The load-bearing thesis-alignment test. Feeds `_timeout_report`'s exact output through `verification_node` and asserts `goto=="generate_mcp_tool"` + artifact stripped + `attempt_count=1`. |
| `TestTimeoutTriggersRetry::test_retry_converges_on_second_attempt` | KEEP | Full 2-attempt round-trip simulation. |
| `TestTimeoutTriggersRetry::test_non_timeout_exception_still_fail_open` | KEEP | Step 14.11 error-branch non-regression. |
| `TestAdversarialModels::test_adversarial_finding_forbids_extra` | KEEP | `extra="forbid"` contract. |
| `TestAdversarialModels::test_adversarial_findings_forbids_extra` | KEEP | Same for container model. |
| `TestAdversarialModels::test_adversarial_finding_valid_severities` | KEEP | Iterates full Severity enum. |
| `TestPipPackagesPropagation::test_empty_packages_delegates_to_base` | KEEP | Empty-list path. |
| `TestPipPackagesPropagation::test_single_package_always_passed` | KEEP | LLM omits `extra_packages` → baseline still threaded through. |
| `TestPipPackagesPropagation::test_llm_supplied_packages_union_with_artifact_packages` | KEEP | Order-preserving union with dedup. |
| `TestPipPackagesPropagation::test_duplicate_packages_are_deduplicated` | KEEP | Dedup guard (no `uv pip install pandas pandas`). |
| `TestPipPackagesPropagation::test_baseline_packages_snapshot_protects_against_mutation` | KEEP | Closure-snapshot immutability. Strong test — catches a sneaky class of shared-reference bugs. |
| `TestPipPackagesPropagation::test_probe_threads_artifact_pip_packages_into_agent` | KEEP | End-to-end: probe() → _create_adversarial_agent(pip_packages=...). |
| `TestPipPackagesPropagation::test_probe_handles_missing_pip_packages_field_gracefully` | KEEP | Legacy artifacts with empty default list. |
| `TestPipPackagesPropagation::test_create_adversarial_agent_none_packages_uses_base_tool` | KEEP | `None` → unwrapped sandbox_execute (pre-fix behavior preserved). |
| `TestPipPackagesPropagation::test_create_adversarial_agent_empty_list_uses_base_tool` | KEEP | Empty list → unwrapped (truthiness contract). |
| `TestPipPackagesPropagation::test_create_adversarial_agent_with_packages_uses_wrapped_tool` | KEEP | Pins `tools[0].__name__ == "sandbox_execute"` so LangChain tool introspection still works. |
| `test_adversarial_probe_integration` | KEEP | `@pytest.mark.integration` — only runs with `DEEPSEEK_API_KEY` + Docker. Appropriately gated. |

#### File-level observations

- **The integration test references `DEEPSEEK_API_KEY` (line 869)**. The rest of the repo now uses `OPENAI_API_KEY` per CLAUDE.md (`openai:gpt-5-mini` default). Not STALE per se (the test skips if the key is missing, so it's a no-op in CI), but the env-var name is an artifact from an earlier provider and could mislead. Consider renaming to `OPENAI_API_KEY` in Step 13.5 test-hygiene pass. Not blocking — flagged as a follow-up only.
- **No stale category strings.** Live categories `hidden_demo_mode`, `network_disconnect`, `ground_truth` in `_suspicious_findings`/`_clean_findings` helpers are the **attack_pattern** values (source line 84-90 in `adversarial.py`), NOT Tier 2 category names. Not stale — this is the Tier 3 taxonomy, distinct from the Tier 2 renames.
- **Test infrastructure is clean.** All mocks patch at `src.verification.adversarial._create_adversarial_agent` (the right seam) rather than reaching into LangGraph internals. `test_timeout_returns_fail_closed_report` uses a real ThreadPoolExecutor + blocking `time.sleep` — genuinely exercises the `concurrent.futures.TimeoutError` path.

### `tests/unit/test_verification_node.py` (25 tests)

| Test | Verdict | Note |
|---|---|---|
| `test_stub_gate_always_passes` | KEEP | `_stub_gate` contract. |
| `test_default_gate_all_pass_routes_to_loading_node` | KEEP | |
| `test_default_gate_all_artifacts_kept` | KEEP | |
| `test_no_artifacts_routes_to_loading_node` | KEEP | Empty-artifact early route. |
| `test_some_fail_below_max_routes_to_generator` | KEEP | Retry routing. |
| `test_passed_artifacts_kept_on_retry` | KEEP | Failed stripped, passed preserved. |
| `test_failed_artifacts_removed_on_retry` | KEEP | 3-artifact variant — asserts exact `names == ["b"]`. |
| `test_max_attempts_routes_to_loading_node` | KEEP | §12 #14 terminal routing. Docstring correctly cites the sticky-flag rationale. |
| `test_max_attempts_drops_failed_artifacts` | KEEP | §12 #14 fail-closed drop — `"bad"` gone, `"good"` kept. |
| `test_max_attempts_sets_sticky_fail_flag` | KEEP | Sticky `verification_succeeded=False`. |
| `test_max_attempts_all_pass_keeps_sticky_flag_true` | KEEP | Happy path at MAX_ATTEMPTS — flag stays True. |
| `test_max_attempts_records_fail_closed_error` | KEEP | "VERIFICATION_FAILED" or "Pillar 2 contract" marker in errors list. |
| `test_retry_below_max_does_not_flip_sticky_flag` | KEEP | Mid-retry: flag NOT flipped (only on exhaustion). |
| `test_attempt_count_incremented_from_zero` | KEEP | |
| `test_attempt_count_incremented_from_two` | KEEP | Boundary (2→3 = MAX_ATTEMPTS). |
| `test_critical_finding_added_to_errors` | KEEP | CRITICAL → errors list. |
| `test_high_finding_added_to_errors` | KEEP | HIGH → errors list. |
| `test_low_finding_not_added_to_errors` | KEEP | LOW filtered out (source line 269 checks CRITICAL/HIGH only). |
| `test_existing_errors_preserved` | KEEP | Prior errors carry through. |
| `test_verification_results_appended` | KEEP | Reports accumulate. |
| `test_existing_verification_results_preserved` | KEEP | Prior reports not clobbered. |
| `test_status_is_verifying` | KEEP | `TaskStatus.VERIFYING`. |
| `test_parallel_verification_processes_all_artifacts` | KEEP | 4 artifacts via ThreadPoolExecutor. |
| `test_gate_exception_treated_as_failure_not_pass` | KEEP | Gate `RuntimeError` → HIGH `gate_error` finding (source line 246-262). Load-bearing fail-safe test — without this, a buggy gate function would silently pass artifacts. |
| `test_parallel_preserves_prior_verification_results` | KEEP | Concurrency doesn't clobber prior state. |

#### File-level observations

- **Module docstring line 6 says "Fail-open behaviour at MAX_ATTEMPTS"** — this is STALE-by-docstring (module was fail-open pre-§12 #14, 2026-04-16). The TESTS themselves all assert the new fail-CLOSED behavior (`test_max_attempts_drops_failed_artifacts`, `test_max_attempts_sets_sticky_fail_flag`, `test_max_attempts_records_fail_closed_error`). **Not a STALE test, but a STALE docstring.** Recommend fixing the docstring in Step 13.5 hygiene pass. One-line edit:
  - Line 6: `- Fail-open behaviour at MAX_ATTEMPTS` → `- Fail-closed behaviour at MAX_ATTEMPTS (§12 #14): failed artifacts dropped + sticky verification_succeeded=False`
- **No tests assert old fail-open behavior.** The §12 #14 migration was done cleanly — every MAX_ATTEMPTS test was updated to assert fail-closed semantics.
- **Gate-exception test is the strongest safety test.** `test_gate_exception_treated_as_failure_not_pass` asserts the fail-safe path (gate raises → artifact treated as failed, errors accumulated, retry fires). This is the behavior that stops a buggy gate implementation from silently passing artifacts into the registry.

---

## Cross-file observations

1. **Rename cleanup is complete.** Searched all three files for `latency_variance` and `network_disconnect` (standalone, not `_differential`). Zero hits. Categories match source verbatim.

2. **Step 14.11 (fail-closed timeout) is covered at three layers.** Unit (`test_timeout_report_fail_closed_high_severity`) + probe integration (`test_timeout_returns_fail_closed_report`) + retry-loop integration (`TestTimeoutTriggersRetry` 3 tests). This is proper depth for a thesis-contract-load-bearing behavior.

3. **Step 14.12 (network-disconnect differential) is covered at three layers.** Behavioral helper unit (implicit via `TestNetworkDisconnectDifferential`) + `run()` integration (4 tests) + Tier 3 prompt addendum not directly tested but the `allow_network` threading in `_create_adversarial_agent` is covered by `test_probe_threads_artifact_pip_packages_into_agent` (checks `allow_network` capture). Could add an explicit test for the Tier 3 system-prompt addendum (`_ALLOW_NETWORK_PROMPT_ADDENDUM`) in Step 13.5, but not required — the prompt is static text and the plumbing is covered.

4. **§12 #14 strict-gate is covered comprehensively.** 5 of the 25 verification_node tests directly exercise the fail-closed path (drop + sticky flag + error marker + happy path at MAX_ATTEMPTS + mid-retry flag stability). Nothing further needed.

5. **One DUPLICATE-by-letter pair, deliberate.** `test_main_guard_neutralized_before_source` (lexical-position check) and `test_main_guard_neutralized_end_to_end_subprocess` (real Python interpreter) overlap in intent but are complementary in implementation. Step 14 behavioral-audit notes (MEMORY.md 1bae0da) specifically called this pairing out as the Gap A fix — the lexical check is cheap CI, the subprocess check is the actual runtime guard. KEEP both; document the pairing rationale in a comment if either is ever proposed for removal.

6. **One WEAK test, defensible.** `TestRunSandboxFailure::test_all_sandbox_failures_handled_gracefully` only asserts `isinstance(findings, list)`. It exercises the `not result.get("success")` branch in `_test_boundary` (source line 419) but doesn't verify the specific `dependency` / `boundary` category that should surface. Can be tightened to assert at least one finding of a specific category surfaces; not urgent.

7. **One STALE docstring, not a test.** `test_verification_node.py` module docstring line 6 still says "Fail-open behaviour at MAX_ATTEMPTS". The tests themselves correctly assert fail-CLOSED. One-line fix.

8. **Tier 2 category coverage against the brief's canonical 11.**

   | Category | Covered? |
   |---|---|
   | `parameterless_smoke` | YES (`TestParameterlessSmoke` × 6) |
   | `output_variation` | YES (`TestOutputVariation` × 7) |
   | `nonexistent_entity` | YES (`TestRunNonexistentEntity`) |
   | `consistency` | YES (`TestRunInconsistentTool`) |
   | `boundary` | YES (`TestRunBoundaryTimeout`) |
   | `dependency` | PARTIALLY (only via `TestRunSandboxFailure` broad assert; no explicit `extra_pkgs` dep-install-fail test) |
   | `timeout` | YES (`TestTimeoutHelper`, `TestRunTimeoutViolation`) |
   | `latency` | YES (`TestLatencyVariance` × 5) |
   | `behavioral` (skip-reason) | YES (`TestRunNoSourceCode`, `TestRunNoPublicFunction`) |
   | `network_silent_fallback` | YES (`TestNetworkDisconnectDifferential::test_differential_flags_silent_fallback`) |
   | `network_disconnect_differential` | YES (other 3 tests in that class) |

   Only `dependency` category lacks a focused test. The `test_all_sandbox_failures_handled_gracefully` test exercises the path indirectly but doesn't assert `category=="dependency"` + `severity==HIGH` + `location=="pip_packages"`. Recommended addition in Step 13.5.

9. **Tier 3 category coverage.** Categories `adversarial_timeout`, `adversarial_error`, `adversarial_skip` all covered. Skip reasons for SKILL/AGENT/MCP_TOOL all distinguished in assertions (post-mortem disambiguation). Good.

---

## Recommended follow-ups (all optional, for Step 13.5 hygiene)

1. Fix the STALE docstring in `test_verification_node.py` line 6 — change "Fail-open" → "Fail-closed" with §12 #14 citation.
2. Tighten `TestRunSandboxFailure::test_all_sandbox_failures_handled_gracefully` to assert the specific `dependency`/`boundary` category surfaces rather than only `isinstance(findings, list)`.
3. Add one focused Tier 2 `dependency`-category test: mock the pre-flight `dep_check` call (source line 614-629 in `behavioral.py`) to fail, assert a single HIGH `dependency`-category finding with `location=="pip_packages"` surfaces and nothing else runs.
4. Rename `DEEPSEEK_API_KEY` → `OPENAI_API_KEY` in `test_adversarial_probe_integration` (line 869) to match the current provider default in CLAUDE.md.
5. Consider replacing call-count-indexed `side_effect` functions in `TestRunInconsistentTool` and `TestRunBoundaryTimeout` with input-payload inspection (match on the `inputs` dict in the built code), to decouple from `run()`'s internal call order.

All five are optional; none block the Phase 4 smoke tests or Phase 5 evaluation.
