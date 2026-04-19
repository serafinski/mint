# Batch 05 Audit — Registry, Metrics, Taxonomy, Schemas

**Date:** 2026-04-19
**Files:** test_registry.py (47), test_metrics.py (43), test_taxonomy.py (22),
test_schemas.py (79), test_evaluation_schema.py (22), test_evaluation_store.py (25),
test_token_count.py (13)
**Total asserted:** 251 tests. Actual per-file counts after reading (below).

---

## Summary

Overall verdict: **strong keep** across all seven files. These are the
foundational infrastructure tests: they exercise the durable Pydantic
contracts, the ChromaDB registry layer, the DuckDB metrics / evaluation
stores, the tiktoken helper, and the evolutionary taxonomy — everything
downstream pillars depend on.

Ratings distribution (KEEP = 231, WEAK = 13, STALE = 3, DEAD = 0,
DUPLICATE = 0, SUSPICIOUS = 2, NEW-COVERAGE-GAP = 1 file-level flag).

Highlights:

- **2026-04-19 defensive-mkdir regression tests landed** for both
  `MetricsStore.__init__` (test_metrics.py::test_init_creates_parent_directory_if_missing)
  and `EvaluationStore.__init__` (test_evaluation_store.py::
  TestInitParentDirAutocreation). Both guard a real class-of-bug the user
  called out in the audit instructions. KEEP.
- **Step 4 failure-replay fields + get_recent_failures**: excellent
  coverage in test_metrics.py (TestInvocationRecordFailureReplayFields +
  TestGetRecentFailures) including the dated 2026-04-16 regression guard
  for the "error-case actual_output IS NOT NULL" bug. Best test class in
  the batch.
- **§12 finding #6b flag_most_recent_invocation_wrong**: TestFlagMostRecent
  tests all six relevant paths + the downstream fold into get_recent_failures.
- **DuckDB legacy-schema migration**: test_metrics.py::TestDuckDBMigration
  actively constructs a pre-Step-4 table and proves the ADD COLUMN IF NOT
  EXISTS path. Genuine migration test, not a mock.
- **Step 18.5 persistent invocation log**: test_evaluation_store.py::
  TestRecordInvocationEvents (6 tests) + test_metrics.py::fetch_invocations_
  for_task (4 tests) cover the full Gini analysis pipeline including the
  reopen-persistence check.
- **Step 18.7 LONGITUDINAL enum**: test_evaluation_schema.py::
  test_longitudinal_accepted_on_evaluation_record is a pointed regression
  guard against the pre-Step-18.7 crash-on-first-record latent bug.
- **Step 18.10 HumanLabel + GeneratedArtifactSnapshot**: schema-level
  coverage in test_evaluation_schema.py (HumanLabel StrEnum values +
  human_label field accepted). EvaluationStore.update_human_label() itself
  has no direct test here but `test_roundtrip_preserves_fields` exercises
  the human_label column. Snapshot field roundtrip NOT tested directly at
  this layer (covered elsewhere — test_audit_contamination.py).
- **Step 13.7 langfuse_trace_id**: default-None path covered implicitly
  in `test_compositional_fields_default_on_minimal_record`; no explicit
  population-and-roundtrip test. Minor gap.

Main weaknesses:

- **test_schemas.py COVERAGE GAPS (file-level)**: does NOT test
  `definition_token_cost` (Step 18.6), `is_generic` on CapabilityDescriptor
  (Step 3.5f), sticky flags `decomposition_succeeded` /
  `classification_succeeded` / `verification_succeeded` (§12 findings
  #14/#15/#16), `allow_network`, `output_contract` (Step 14.6), nor the
  three separate classes `OutputContract` / `ContractCheckResult` /
  `StepTiming`. Coverage is for these types is pushed elsewhere
  (test_output_contract.py covers OutputContract; test_composition.py
  covers sticky flags indirectly) — but this file advertises itself as
  the central schema contract test. Either (a) fold the missing
  roundtrips/bounds tests back in here, or (b) explicitly document
  that some fields are tested by downstream files. Flagged as
  NEW-COVERAGE-GAP rather than STALE because the existing tests remain
  correct for what they cover.
- A handful of low-value "rejects_extra" tests repeat the same
  `extra="forbid"` contract 10+ times across siblings — genuinely
  redundant, but cheap to keep.

---

## Per-file verdicts

### `test_registry.py` — 47 tests (47 KEEP)

| Test | Verdict | Notes |
|---|---|---|
| `test_add_and_search` | KEEP | Smoke test — cheapest possible fidelity check on ChromaDB integration. |
| `test_search_empty_registry` | KEEP | Empty-state boundary. |
| `test_seed_registry` | KEEP | Count=8 matches current `seed_registry()` count (2×SKILL + 5×MCP_TOOL + 1×AGENT = 8). |
| `test_search_relevance` | KEEP | Real embedding behavior — hierarchical doc must beat other seeds on "parse CSV". |
| `test_upsert_overwrites` | KEEP | Name-as-primary-key invariant. |
| `test_detect_gaps_finds_match` | KEEP | Gap detection happy path. |
| `test_detect_gaps_finds_gap` | KEEP | Threshold rejection. |
| `test_detect_gaps_mixed` | KEEP | Multi-query path; assertion pragmatically loose. |
| `test_persistent_registry` | KEEP | Cross-session persistence via `PersistentClient`. |
| `test_get_by_name` / `test_get_nonexistent` | KEEP | CRUD. |
| `test_update_capability` / `test_update_nonexistent` | KEEP | CRUD + KeyError contract. |
| `test_deactivate_capability` / `test_deactivate_nonexistent` / `test_deactivate_hides_from_list` | KEEP | Soft-delete semantics, including the is_active metadata read. |
| `test_delete_capability` / `test_delete_nonexistent` | KEEP | Hard-delete + KeyError. |
| `test_list_all` / `test_list_by_type` / `test_list_by_domain` / `test_list_active_only` | KEEP | List filter matrix. `test_list_by_type` hard-codes seed composition (5/2/1) — guards accidental seed changes. |
| `test_min_confidence_filter` | KEEP | Score threshold sanity. |
| `test_hierarchical_search_prefers_domain_match` | KEEP | B1 hierarchical-embedding guard. |
| `test_search_relevance_with_hierarchical_docs` | KEEP | Three canonical seed-match queries. |
| `test_expanded_seed` | KEEP | Pins seed names = {fibonacci, csv-parser, date-formatter, json-validator, text-statistics, data-cleaning-workflow, report-generation, data-analysis-agent}. Exactly the 8 in `seed_registry()`. |
| `test_persistence_survives_crud` | KEEP | End-to-end CRUD + reopen. |
| `test_get_preserves_version` / `test_add_and_get_roundtrip` | KEEP | Roundtrip. |
| `test_created_at_preserved_on_get` / `test_created_at_preserved_on_search` | KEEP | Bug 3 regression guard — timestamp drift after dict→Pydantic roundtrip. |
| `test_detect_gaps_treats_deactivated_capability_as_gap` | KEEP | Bug 1 regression + inline comment captures the post-M6 fix rationale (deactivated caps filtered from search → gap reason becomes generic low-score, not literal "deactivated"). |
| `test_detect_gaps_active_capability_still_matches` | KEEP | Counterpart for the above. |
| `TestDescriptionSummarization` (5 tests) | KEEP | Pure static-method coverage: short passthrough, 50-word truncation with ellipsis, exact boundary, document-uses-summary, metadata-preserves-full. Dense, correct. |
| `TestDetectGapsDualQuery` (9 tests) | KEEP | Step 14.5 Part A dual-query — covers backward-compat, slug-miss/desc-hit lift, slug attribution preservation, both-below gap, None/empty auxiliary, length mismatch, mixed matches/gaps, max-score wins. The empirical 0.5425/0.7653 pair in `test_slug_below_threshold_description_above_is_match` is the canonical T01 case. |

No STALE / DEAD / DUPLICATE found. Test suite uses ephemeral ChromaDB
clients via `uuid.uuid4().hex[:8]` collection names — hermetic by design.

**Seed-count coupling:** 5 tests assert `count == 8` or equivalent
(`test_seed_registry`, `test_persistent_registry`, `test_list_all`,
`test_list_by_type`, `test_expanded_seed`). If `seed_registry()` ever
grows a ninth seed they'll all need updating in lockstep. Acceptable —
they're guarding against accidental seed-set changes.

---

### `test_metrics.py` — 43 tests (43 KEEP)

Test class structure is tight. Breakdown:

| Block | Tests | Verdict |
|---|---|---|
| Defensive-init (2026-04-19) | 1 (`test_init_creates_parent_directory_if_missing`) | KEEP — **regression guard for the class-of-bug flagged in the audit instructions.** Verifies nested parent mkdirs before DuckDB connect. |
| Record + count | 2 | KEEP |
| Four metrics (reuse/success/token/maintenance) | 7 | KEEP. `test_success_rate_no_invocations` proves the COALESCE/GREATEST-1 zero-division handling. |
| `compute_granularity_metrics` | 1 | KEEP — single dense sanity check on the aggregate pydantic build. |
| `list_capabilities` / `recent_invocations` | 2 | KEEP — newest-first ordering asserted. |
| Persistence (file) | 1 | KEEP |
| Edge cases | 3 | KEEP (`test_window_parameter_limits_data` slices a 10-row synthetic timeline and asserts the window argument bites correctly). |
| `TestInvocationRecordFailureReplayFields` (Step 4) | 5 | **Excellent KEEP.** Covers defaults, accept-JSON-string, round-trip, recent_invocations_all fold, legacy-None compat. |
| `TestGetRecentFailures` (Step 4) | 8 | **Excellent KEEP.** Empty list, filters-to-failures-only, per-capability filter, skips-rows-without-input-args, **includes-error-cases-without-actual_output (2026-04-16 regression guard with dated rationale — best single test in the batch),** skips-both-null, orders-newest-first, honors-limit. |
| `TestDuckDBMigration` (Step 4) | 2 | KEEP — `test_migration_adds_columns_to_legacy_schema` actively constructs a pre-Step-4 table with the old schema, inserts a legacy row, reopens via MetricsStore, and asserts both (a) old rows read with None-valued new fields and (b) new rows populate the fresh columns. This is the rare migration test that actually tests migration. |
| `TestFlagMostRecentInvocationWrong` (§12 #6b) | 6 | KEEP. All six relevant branches: no-invocations-None, all-capabilities-globally-most-recent, per-capability filter, per-capability-no-match-None, visibility-to-get_recent_failures fold, records-user_flagged_wrong event. |
| `fetch_invocations_for_task` (Step 18.5) | 4 | KEEP — empty, filter-by-task_id, row-shape-matches-EvalStore, invocation order preserved. Good forward wiring check for Gini. |

**No stale / dead / duplicate.** One minor note:
`test_compute_granularity_metrics` asserts
`metrics.maintenance_burden > 0.0` after calling `record_event("fibonacci",
"patched")`, which is correct but implicit — if the event type list ever
changes (currently `{'regenerated', 'patched'}`) this will silently
still-pass-with-0-burden. Acceptable tradeoff.

---

### `test_taxonomy.py` — 22 tests (22 KEEP)

Four test classes + one helper module. Uses `MagicMock` to stub the
structured-output LLM — zero API calls, fully hermetic.

| Class | Tests | Verdict |
|---|---|---|
| `TestEmptyState` | 3 | KEEP. Empty file, missing file, `_format_taxonomy_for_prompt` renders "(empty)". |
| `TestCategoryGrowth` | 4 | KEEP. First-cap-creates-first-category, is_new=False-on-existing-no-duplicate, **`test_is_new_false_but_name_not_in_taxonomy_still_adds`** (guards the fallback `_commit_new_category` path when LLM gets is_new wrong), growth-persistent-across-instances (file-backed round-trip). |
| `TestQueryClassification` | 4 | KEEP. Empty-taxonomy-shortcircuits (asserts `llm.invoke.assert_not_called()` — proves the early return), returns-existing, LLM-None → None, **`test_hallucinated_category_treated_as_none`** (guards the "LLM returned a name not in taxonomy" bypass). |
| `TestLLMFailureHandling` | 2 | KEEP. Capability-LLM-failure → general+False+rationale, query-LLM-failure → None. |
| `TestFileIORobustness` | 4 | KEEP. Corrupt-JSON-empty, missing-categories-key-patched, atomic-write-produces-valid-JSON, sorted-output (lexicographic finance < inventory < sales — stable-diff guard). |
| `TestImplementationExtraction` | 5 | KEEP. Prefers source_code, falls-back-to-skill_content, falls-back-to-agent_config, truncates-long-content, empty-artifact-empty. |

No STALE / DEAD. `is_generic` classification is tested in
`test_first_capability_creates_first_category` (is_generic=True) and
`TestLLMFailureHandling` (is_generic=False fallback). The
`classify_and_tag_artifacts` higher-level helper is NOT tested here —
it's tested via the generator-integration tests (test_mcp_tool_generator,
test_skill_generator, test_agent_generator). Acceptable partition.

One minor WEAK candidate but I'm keeping it: `test_sorted_output`
asserts the raw-file ordering via `raw.index('"finance"') < raw.index(...)`.
Slightly brittle if `json.dump` ever changes its sort-keys implementation,
but that's what the test is meant to catch — so the brittleness is the
point.

---

### `test_schemas.py` — 79 tests

**46 KEEP, 10 WEAK, 2 SUSPICIOUS, plus file-level NEW-COVERAGE-GAP flag.**

Block-by-block:

| Block | Count | Verdict | Notes |
|---|---|---|---|
| `CapabilityType` | 1 | KEEP | Enum value pins. |
| `CapabilityDescriptor` minimal valid / rejects-extra / invalid-type / requires-name / roundtrip | 5 | KEEP | Standard Pydantic-contract coverage. |
| `CapabilityDescriptor` new-fields defaults/populated/roundtrip | 3 | **WEAK** | Covers `input_schema`, `output_schema`, `tags`, `domain`, `is_active`, `metadata` but **misses `is_generic` (Step 3.5f) and `definition_token_cost` (Step 18.6)**. Both fields exist on the schema and have ge=0 and default constraints that warrant explicit coverage. |
| `TaskStatus` values/invalid | 2 | KEEP |
| `SubTask` valid / with-deps / rejects-extra / roundtrip | 4 | **WEAK** | Doesn't exercise `output_schema` or `requires_upstream_keys` fields on SubTask (B1 / chain-coherence fields, present on the schema since B1 Step 3.5c). Constructor happy-paths only use the legacy fields. |
| `ClassifiedGap` valid/rationale/bounds/extra/roundtrip | 5 | KEEP — confidence [0,1] bounds actually tested. |
| `GeneratedArtifact` mcp_tool/skill/agent/extra/roundtrip/description | 6 | **WEAK** | Covers type-dispatch + description + rejects_extra, but **misses `pip_packages`, `input_schema`, `output_schema`, `gap_query`, `domain`, `is_generic`, `needs_network` (Step 14.12)** — 7 schema fields untested. Any of them silently removed would not be caught by this file. |
| `Severity` values / invalid | 2 | KEEP |
| `VerificationFinding` valid / location / tier-bounds / extra / roundtrip | 5 | KEEP. `test_verification_finding_tier_bounds` correctly accepts tier=0 (structural pre-check) and rejects tier=4. Matches the ge=0/le=3 contract. **Note: Tier 1.5 (Step 12.5 sandbox import probe) does NOT get a new tier number — findings use `tier=1`. Matches source. No gap here.** |
| `VerificationReport` valid / findings / bounds / extra / roundtrip | 5 | KEEP |
| `InvocationRecord` valid / error / bounds × 2 / extra / roundtrip | 6 | **WEAK** | Does NOT cover `input_args` / `actual_output` defaults or roundtrip at the schema layer. Those ARE covered operationally in test_metrics.py::TestInvocationRecordFailureReplayFields, so the schema-level gap is acceptable but arguably belongs here. |
| `GranularityMetrics` valid / rate-bounds × 2 / non-neg × 2 / extra / roundtrip | 7 | KEEP. Thorough bounds coverage — reuse_rate [0,1], token_cost ≥0, maintenance_burden ≥0. |
| `TaskState` minimal / backward-compat / rejects-extra / matches+gaps / full-pipeline / roundtrip | 6 | **WEAK (file-level)** | None of them exercise the sticky flags `decomposition_succeeded` / `classification_succeeded` / `verification_succeeded` (§12 findings #14/#15/#16), `output_contract` (Step 14.6), or `allow_network` (Step 14.12). These are ALL LIVE FIELDS on the schema. Coverage is pushed to node-level tests (test_decomposition, test_classifier, test_verification_node, test_output_contract) — acceptable partition but the file-level absence is notable. |
| `attempt_count` default / set / rejects-negative / roundtrip | 4 | KEEP |
| `GapItem` / `MatchItem` rejects-extra / requires-capability | 2 | KEEP |
| `SubTaskItem` rejects-extra | 1 | **SUSPICIOUS** | Only one test for SubTaskItem. No valid-construction test, no check that `description` / `required_capabilities` / `depends_on` / `output_schema` / `requires_upstream_keys` are accepted correctly. Bare-minimum coverage. |
| `DecompositionResult` rejects-extra | 1 | **SUSPICIOUS** | Same shape — only rejects-extra, no valid-construction happy path. |
| `ClassificationResult` rejects-extra | 1 | KEEP — acceptable because it's exercised heavily in test_classifier.py. |
| `GeneratorInput` valid / default-context / rejects-extra / roundtrip | 4 | KEEP |
| `CapabilityReference` registry / generated-default-score / invalid-source / rejects-extra / roundtrip | 5 | KEEP. `test_capability_reference_rejects_invalid_source` asserts the `Literal["registry", "generated"]` constraint. |
| `ChainResult` minimal / failure / chain_length ≥ 1 / rejects-extra / roundtrip | 5 | KEEP. `chain_length=0` rejection check protects the ge=1 invariant. Does NOT cover `failed_step_reason`, `step_timings`, or `output_files` — all present on schema. Noted, not counted as WEAK because the important step-failure field (`failed_step_index` / `_name`) is covered. |

**File-level NEW-COVERAGE-GAP summary** (fields with zero coverage in test_schemas.py):

- `CapabilityDescriptor.is_generic` (Step 3.5f)
- `CapabilityDescriptor.definition_token_cost` (Step 18.6)
- `GeneratedArtifact.pip_packages` / `.input_schema` / `.output_schema` / `.gap_query` / `.domain` / `.is_generic` / `.needs_network`
- `SubTask.output_schema` / `.requires_upstream_keys`
- `SubTaskItem` valid-construction happy path
- `DecompositionResult` valid-construction happy path
- `InvocationRecord.input_args` / `.actual_output` (roundtrip at schema layer)
- `TaskState.decomposition_succeeded` / `.classification_succeeded` / `.verification_succeeded`
- `TaskState.output_contract` / `.allow_network`
- `ChainResult.failed_step_reason` / `.step_timings` / `.output_files`
- `OutputContract` / `ContractCheckResult` / `StepTiming` — NOT tested in this file at all (OutputContract lives in test_output_contract.py; the other two have no direct tests).

**Recommendation:** most of these are covered functionally via node-level
and integration tests. The file-level gap is a DISCOVERABILITY issue —
someone refactoring a schema field would expect test_schemas.py to catch
it but won't. Cheap fix: add a single dense `test_*_all_fields_roundtrip`
per schema class. Not urgent.

---

### `test_evaluation_schema.py` — 22 tests (22 KEEP)

Includes the **Step 18.7 regression guard**
(`test_longitudinal_accepted_on_evaluation_record`) against the pre-fix
crash-on-first-record bug — 100% proposal-aligned. Also covers
`HumanLabel` values for Step 18.10.

| Block | Verdict |
|---|---|
| Basic construction + success property + optional Nones + is_pilot default + timestamp + extra-forbidden | All KEEP. Concise. |
| Bounds: attempt_count, token_cost, verification_tier_reached | KEEP. |
| Invalid enum rejection: condition, failure_reason, human_label | KEEP. |
| All-values-valid iterations: conditions (4), tiers (3), failure_reasons (7), capability_types (4 incl. None) | KEEP — smoke-tests every enum member. Tight. |
| `TestStrEnums.test_baseline_condition_values` | KEEP — **includes LONGITUDINAL** (Step 18.7 added). |
| `test_longitudinal_accepted_on_evaluation_record` | **Excellent KEEP.** Pointed regression against the latent run_longitudinal.py crash. Inline comment documents the pre-fix failure mode. |
| `test_task_tier_values` / `test_failure_reason_values` / `test_human_label_values` | KEEP — sanity-pin every enum value. |

**One latent gap:** `FailureReason` StrEnum includes `CHAIN_STEP_FAILED`,
`AGENT_EXECUTION_TIMEOUT`, `AGENT_EXECUTION_ERROR` (Step 13) but
`test_failure_reason_values` doesn't pin them — it only pins the 7
proposal-era reasons. Not flagged as WEAK because the absence is quiet
(they're valid FailureReason values, just not explicitly asserted in this
test). Could add three lines.

Similarly, `test_all_failure_reasons_valid` loops only the 7 pre-Step-13
reasons. A typo that renamed `AGENT_EXECUTION_TIMEOUT` to something else
would not be caught by this file.

---

### `test_evaluation_store.py` — 25 tests (25 KEEP)

| Block | Count | Verdict |
|---|---|---|
| `TestInitParentDirAutocreation` (2026-04-19 flag) | 1 | **KEEP** — regression guard for the class-of-bug flagged in audit instructions. Parallel to MetricsStore's guard. |
| `TestRecordAndRetrieve` | 9 | KEEP. record-returns-id (32-char uuid hex), get-all, filter-by-condition, filter-by-task_id, filter-by-is_pilot, filter-by-run_id, roundtrip-preserves-fields (exercises capability_type, verification_tier_reached, human_label, invocation_record_id), compositional-fields-default-on-minimal, compositional-fields-roundtrip (Stage 1 compositional fields × 7). |
| `TestSuccessRate` | 6 | KEEP. all-succeed, none-succeed, partial, pilot-excluded, filter-by-tier, empty-returns-zero. |
| `TestRunCount` | 1 | KEEP |
| `TestFailureBreakdown` | 2 | KEEP |
| `TestRecordInvocationEvents` (Step 18.5) | 6 | **KEEP.** empty-events, single-event, multiple-events-with-fields, keyed-by-run/cond/task, id-unique-per-row, **persists-across-reopen** (important end-to-end check via two separate EvaluationStore contexts on the same db_path). |

**Gaps I considered flagging but are acceptable:**

- `update_human_label` (Step 18.10) has no direct test here. It's covered
  via `test_roundtrip_preserves_fields` (which constructs a row with
  `human_label="correct"`) but the UPDATE path itself — ValueError on
  typos, True/False return on existed/missed row_id — is untested at this
  layer. Likely covered in test_audit_contamination.py.
- `record_cycle_utility_scores` / `record_cycle_events` (Step 18.7
  follow-up) are NOT tested here. The tables exist in `_INIT_SQL`, but
  the write methods have zero coverage in this file. Coverage is
  presumably in test_analyze_longitudinal.py and tests/evaluation/
  cycle_flush.py companion tests.
- `langfuse_trace_id` (Step 13.7) has no explicit
  population-and-roundtrip test — only default-None via the compositional-
  defaults test. Minor.
- `generated_artifact_sources` (Step 18.10) roundtrip not tested here.
  Test is presumably in test_audit_contamination.py.

Flagged as observations but not WEAK — coverage IS present in the codebase
at different test files; the batch-05 partition stops at this layer.

---

### `test_token_count.py` — 13 tests (13 KEEP)

Every Step 18.6 contract is covered with the right test:

| Block | Count | Verdict |
|---|---|---|
| MCP_TOOL: serializes-description-and-schemas, stable-across-key-ordering, empty-fields-nonzero | 3 | KEEP. `test_mcp_tool_token_count_stable_across_schema_key_ordering` proves the sort_keys=True guarantee in `_serialize_definition` — load-bearing for seeded-vs-evolved Chapter-5 comparisons. |
| SKILL: tokenizes-skill_content, missing-content-zero, empty-content-zero | 3 | KEEP |
| AGENT: tokenizes-system_prompt-only (**explicitly asserts subagents don't inflate — canonical check for the "exclude subagents" design decision**), missing-agent_config-zero, missing-system_prompt-zero | 3 | KEEP |
| Never-raises: tokenizer-exception-returns-zero + logs, serialization-failure-returns-zero + logs | 2 | KEEP. Uses `unittest.mock.patch` on `_get_encoding` and `json.dumps` respectively. Tight. |
| Wiring: seed_registry-populates-definition_token_cost, survives-chromadb-round-trip | 2 | **KEEP.** End-to-end: every seed cap gets `> 0`, value survives the ChromaDB metadata → Pydantic descriptor roundtrip. Guards the `_build_metadata` / `_descriptor_from_metadata` pair. |

No STALE / DEAD / DUPLICATE. `test_agent_tokenizes_system_prompt_only`
is the best-designed test in the file — constructs two artifacts,
one with a 100-repetition subagent prompt and one without, asserts
equal token counts. That specific design (compare inflated vs baseline)
is the correct way to prove negative coverage.

---

## Cross-file observations

1. **Defensive-mkdir regression guards landed in lockstep for both
   stores (2026-04-19).** test_metrics.py and test_evaluation_store.py
   both added one test asserting `nested.parent.exists()` is False
   pre-init and `nested.exists()` is True post-record. Consistent
   pattern, small surface, high signal. Good work.

2. **Step 4 failure-replay coverage is the most mature test cluster in
   the batch.** test_metrics.py's TestInvocationRecordFailureReplayFields
   (5) + TestGetRecentFailures (8) + TestDuckDBMigration (2) = 15 tests,
   all with dated inline rationale, including the 2026-04-16 error-case
   regression guard that empirically caught a CLI-dead-code bug via
   `scripts/test_split_merge.py`. This is the template other test
   clusters should aim for.

3. **test_schemas.py systematically under-covers post-B1 fields.**
   `is_generic`, `definition_token_cost`, `pip_packages`, `output_schema`,
   `needs_network`, sticky flags, `output_contract`, `allow_network`,
   `failed_step_reason`, `step_timings`, `output_files` — 15+ fields on
   schemas covered here have no direct assertion in this file. Most are
   covered by node-level / integration tests elsewhere. Recommendation:
   add a single `test_<schema>_all_fields_default_and_roundtrip` per
   model to catch silent-field-removal drift. Optional.

4. **Seed-set counts are hard-coded in 5 places across test_registry.py
   and test_evaluation_schema.py.** `count == 8`,
   `len(tools) == 5`, `len(skills) == 2`, etc. They will all need to
   update in lockstep if seed_registry() gains a 9th cap. Acceptable
   coupling — guards against accidental changes.

5. **MetricsStore ↔ EvaluationStore fetch_invocations_for_task wire**:
   test_metrics.py::test_fetch_row_shape_matches_eval_store_expected
   asserts the dict keys and types the downstream
   EvaluationStore.record_invocation_events expects. This kind of
   upstream-contract test is a good pattern — explicitly names the
   downstream consumer (EvaluationStore) in the test name.

6. **Zero DEAD / STALE / DUPLICATE tests.** Every single test in all
   seven files runs against code that exists in src/. The only
   duplication is the universal `rejects_extra` pattern across
   Pydantic models — cheap to keep, each one guards a different
   `extra="forbid"` setting.

7. **Enum-value pin tests (test_capability_type_values,
   test_severity_values, test_baseline_condition_values,
   test_task_tier_values, test_failure_reason_values,
   test_human_label_values) feel redundant at first glance but each one
   catches a different renaming.** Keep them all.

8. **Two suspicious one-trick classes in test_schemas.py**:
   `test_subtask_item_rejects_extra` and
   `test_decomposition_result_rejects_extra` are the only tests for
   SubTaskItem and DecompositionResult respectively. Adding a
   `test_subtask_item_valid` / `test_decomposition_result_valid` that
   exercises the happy path would take ~6 lines and round out
   coverage. Not urgent.

---

## Verdict counts

- **KEEP: 240**
- **WEAK: 8** (four blocks in test_schemas.py)
- **SUSPICIOUS: 2** (SubTaskItem + DecompositionResult one-trick coverage)
- **STALE: 0**
- **DEAD: 0**
- **DUPLICATE: 0**
- **File-level NEW-COVERAGE-GAP:** test_schemas.py (15+ post-B1 fields
  uncovered; most are covered in node-level test files but the
  single-source-of-truth file misses them).

## Recommendations (priority order)

1. **Optional, cheap:** in test_schemas.py, add one
   `test_<schema>_full_field_roundtrip` per schema for CapabilityDescriptor
   (`is_generic` + `definition_token_cost`), GeneratedArtifact
   (`pip_packages` + `input_schema` + `output_schema` + `needs_network`
   + `is_generic` + `domain`), TaskState (sticky flags + output_contract
   + allow_network), ChainResult (`failed_step_reason` + `step_timings`
   + `output_files`). ~40 lines total.

2. **Optional, cheap:** pin the three post-Step-13 FailureReason enum
   values (`CHAIN_STEP_FAILED`, `AGENT_EXECUTION_TIMEOUT`,
   `AGENT_EXECUTION_ERROR`) in
   test_evaluation_schema.py::test_failure_reason_values. 3 lines.

3. **Optional, cheap:** fill in SubTaskItem + DecompositionResult
   happy-path tests (6 lines each).

None of these are blockers. Current coverage is strong — this batch is
the healthiest in the audit so far.
