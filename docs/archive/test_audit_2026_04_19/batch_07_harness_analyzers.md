# Batch 07 — Harness + Analyzer Tests Audit

**Date:** 2026-04-19
**Scope:** 10 test files covering EvaluationHarness, static baselines, snapshots, TaskDefinition validators, post-hoc analyzer scripts (M-metrics: compression loss, longitudinal convergence, contamination audit), and the hand-built fibonacci reference MCP tool.

---

## Summary

**Verdicts at a glance**

| File | Tests | KEEP | WEAK | STALE | DEAD | DUP | SUSP |
|---|---|---|---|---|---|---|---|
| test_harness.py | 28 | 28 | 0 | 0 | 0 | 0 | 0 |
| test_harness_static.py | 19 | 19 | 0 | 0 | 0 | 0 | 0 |
| test_evaluation_snapshots.py | 12 | 12 | 0 | 0 | 0 | 0 | 0 |
| test_evaluation_tasks.py | 80 | 78 | 2 | 0 | 0 | 0 | 0 |
| test_analyze_results.py | 7 | 7 | 0 | 0 | 0 | 0 | 0 |
| test_analyze_compression_loss.py | 20 | 20 | 0 | 0 | 0 | 0 | 0 |
| test_analyze_longitudinal.py | 36 | 36 | 0 | 0 | 0 | 0 | 0 |
| test_audit_contamination.py | 31 | 31 | 0 | 0 | 0 | 0 | 0 |
| test_fibonacci.py | 4 | 4 | 0 | 0 | 0 | 0 | 0 |
| test_mcp_fibonacci.py | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| **TOTAL** | **239** | **237** | **2** | **0** | **0** | **0** | **0** |

**Headline finding:** This batch is in very good shape. All 239 tests collect successfully (verified via `uv run pytest --collect-only`). The two WEAK flags are tolerance-related observations on T03/T04 validators, not brokenness. No dead, stale, or duplicate tests.

**Correction to prompt:** `test_fibonacci.py` (4 tests) and `test_mcp_fibonacci.py` (2 tests) are NOT placeholders — the "0 tests / possibly imports-only" hint in the audit brief was incorrect. Both are real, thin smoke-test suites. Pytest-async markers did not hide them; they collect cleanly.

**Coverage gaps (not test bugs, but scope observations — see Cross-File Observations):**

1. **T19 + T20 have no validator tests in `test_evaluation_tasks.py`.** 18 of 20 tasks get validator classes; T19/T20 are absent. This is the only real gap in this batch.
2. **`TaskDefinition.expected_type` field is completely untested.** Step 18.5 added this (8 mcp_tool / 6 skill / 6 agent labels). No test asserts the dict covers all 20, no test asserts the counts, no test asserts the label values survive the `object.__setattr__` retrofit. These tests likely belong in `test_evaluation_tasks.py`.
3. **`scripts/analyze_results.py::_gini_coefficient` / `print_gini_summary` / `print_classification_accuracy` have ZERO dedicated tests in this batch** despite the `test_analyze_results.py` filename suggesting that is where they'd live. Grep confirms they're not tested anywhere in `tests/`. MEMORY.md claims Step 18.5 added "+10 unit tests" — those tests are not in this batch.
4. **`tests/evaluation/cycle_flush.py::flush_task_cycle_state` (Step 18.8 refactor) has no dedicated tests.** Only `tests/unit/test_tracing.py` touches it indirectly. Its never-raises contract + three-flush atomic semantics are not directly verified.

All four gaps are recommendations to ADD tests, not to remove/change anything in the existing 239.

---

## Per-File Verdicts

### 1. `tests/unit/test_harness.py` (28 tests)

Target: `tests/evaluation/harness.py::EvaluationHarness`, `_extract_result`, `_extract_result_compositional`, `_run_static`, `run_pilot`, `run_condition`.

All 28 tests KEEP. Tests organize into 5 classes matching the harness's public surface:

| Class | Tests | Verdict | Notes |
|---|---|---|---|
| `TestRunStatic` | 5 | KEEP | Covers the 5 branches: no-coverage, registry miss, success, output-rejected, impl-exception. All 5 are load-bearing Layer-2 tests. |
| `TestExtractResult` | 13 | KEEP | Full branch coverage: no-artifacts, passing report, failing at tier1/tier2/tier3, validator pass/fail, no-verification bypass (`no_verification` condition), capability_type extraction, artifact-execution exception, three registry-match paths (with source + source fails + no source). |
| `TestExtractResultCompositionalAgentFailure` | 3 | KEEP | Step 13 tracking: asserts `ChainResult.failed_step_reason` translates to the matching `FailureReason` enum value (`agent_execution_timeout`, `agent_execution_error`, fallback `chain_step_failed`). Critical for Phase 5 compression-loss analytics per Step 13.8. |
| `TestRunPilot` | 3 | KEEP | Verifies snapshot ID propagation, default condition = "full" (patched to "static"), and DB persistence via `store.get_runs(is_pilot=True)`. |
| `TestRunCondition` | 4 | KEEP | Verifies `restore_clean_snapshot` called per-run (3 times), record cardinality = `n_runs × n_tasks`, `is_pilot=False` on non-pilot runs, and distinct seeds per run. |

**Mocking hygiene.** All tests use `unittest.mock.patch` correctly — `restore_clean_snapshot`, `seed_registry`, `SkillRegistry`, `ToolLoader`, `_execute_artifact`, `_run_static_implementation`, `CompositionalExecutor` are all patched at import-site. No leakage across tests.

**Coverage cross-check.** `_extract_result` returns 3 failure-reason strings — `tier1`, `tier2`, `tier3`, `output_rejected`, `generation_failed`, `no_source_code`, `none` — all 7 are exercised. `_run_static` returns `generation_failed` / `output_rejected` / `none` — all 3 are exercised.

---

### 2. `tests/unit/test_harness_static.py` (19 tests)

Target: `tests/evaluation/harness.py::_run_static_implementation` + the 5 T-task validators (T01 / T02 / T03 / T05 / T09) that have static baselines.

All 19 tests KEEP. Structure:

| Class | Tests | Verdict | Purpose |
|---|---|---|---|
| `TestStaticT01` | 3 | KEEP | validator pass, row_count=50, column set match |
| `TestStaticT02` | 5 | KEEP | validator pass, error count = 3, port/ttl/level errors each present |
| `TestStaticT03` | 3 | KEEP | validator pass, word_count > 0, readability in [0,100] |
| `TestStaticT05` | 3 | KEEP | validator pass, null_count=0 after cleaning, output_row_count < input_row_count |
| `TestStaticT09` | 4 | KEEP | validator pass, has 3 required keys, exactly 5 anomalies, summary is non-empty string |
| `TestStaticUnknownTask` | 1 | KEEP | `ValueError("No static implementation")` on unknown task ID |

**This is the Layer-1 smoke test** per the file docstring — if the static baseline is broken, the pilot is broken. Tests hit real fixture files (no mocks, no LLM, no Docker). Fast (~0.1s).

**Cross-check:** `ALL_TASKS` has 5 tasks with `has_static_coverage=True` (T01, T02, T03, T05, T09); all 5 are tested here. `test_static_coverage_count` in `test_evaluation_tasks.py` confirms the count.

---

### 3. `tests/unit/test_evaluation_snapshots.py` (12 tests)

Target: `tests/evaluation/snapshots.py` — `take_clean_snapshot`, `restore_clean_snapshot`, `snapshot_exists`, `_hash_snapshot`.

All 12 tests KEEP.

| Class | Tests | Notes |
|---|---|---|
| `TestTakeSnapshot` | 4 | snap_id format (`snap_` + 8 hex), meta.json written, files copied into chroma/mcp_tools/skills subdirs, idempotent on repeat call |
| `TestRestoreSnapshot` | 3 | files restored (dirty contamination wiped), same snap_id returned, `FileNotFoundError` when snapshot_dir empty |
| `TestSnapshotExists` | 2 | false before + true after `take_clean_snapshot` |
| `TestHashSnapshot` | 3 | deterministic, content-sensitive, `meta.json` excluded from hash (self-reference would otherwise cause hash-then-write loop) |

**Quirk in fixture (line 26-28):** The fixture uses a slightly awkward `mkdir(parents=True)` → `rmdir()` → `mkdir(parents=True)` dance for `skills/skill_x` before writing `SKILL.md`. That is an artifact of wanting SKILL.md to be a file under a directory named `skill_x/`. Functionally correct; aesthetically odd. Not worth changing.

**File is self-contained.** Does not depend on any real `data/eval/` snapshots; all paths are `tmp_path`-scoped.

---

### 4. `tests/unit/test_evaluation_tasks.py` (80 tests)

Target: `tests/evaluation/tasks.py` — `ALL_TASKS`, `PILOT_TASKS`, `get_task`, and the per-task validator closures (T01-T18).

78 KEEP, 2 WEAK.

| Class | Tests | Verdict |
|---|---|---|
| `TestTaskRegistry` | 8 | KEEP — count=20, unique IDs, pilot tasks = {T01,T05,T07}, get_task works + raises, static coverage count = 5, tier set = {trivial, moderate, complex}, all fixture paths exist on disk |
| `TestT01Validator` | 5 | KEEP |
| `TestT02Validator` | 3 | KEEP |
| `TestT03Validator` | 4 | 3 KEEP + 1 **WEAK** (see below) |
| `TestT04Validator` | 3 | 2 KEEP + 1 **WEAK** (see below) |
| `TestT05Validator` | 3 | KEEP |
| `TestT06Validator` | 3 | KEEP |
| `TestT07Validator` | 3 | KEEP |
| `TestT08Validator` | 3 | KEEP |
| `TestT09Validator` | 3 | KEEP |
| `TestT10Validator` | 4 | KEEP |
| `TestT11Validator` | 5 | KEEP |
| `TestT12Validator` | 5 | KEEP |
| `TestT13Validator` | 6 | KEEP |
| `TestT14Validator` | 4 | KEEP |
| `TestT15Validator` | 4 | KEEP |
| `TestT16Validator` | 5 | KEEP |
| `TestT17Validator` | 5 | KEEP |
| `TestT18Validator` | 4 | KEEP |

#### WEAK flags

1. **`TestT03Validator::test_word_count_slightly_off_passes` (line 102-105).** Asserts `word_count = ev["word_count"] + 1` still passes. The intent is "tolerance" but the test hardcodes `readability_score=50.0`, which may or may not be inside T03's actual tolerance window. The test does not assert the ceiling — e.g. `+1000` still passing (if that's the tolerance threshold) would also pass here. The test asserts directionality without pinning the tolerance width. Fine as a smoke test; too coarse for a regression guard. Verdict: WEAK.

2. **`TestT04Validator::test_cost_too_far_off` (line 135-140).** Asserts `total_reorder_cost=0.0` against the expected ev value fails. It uses `product_count=10, items_below_threshold=7` — hardcoded values that may not even match `ev["product_count"]`. If the validator is all-or-nothing, this test passes for the wrong reason (product_count mismatch alone would fail). Verdict: WEAK — single-dimension rejection test that might be rejecting on a different field than named.

#### Coverage gaps (not test bugs, but scope observations)

- **T19 and T20 have no dedicated validator classes.** T19 is at `tasks.py:1103`, T20 at `tasks.py:1157`. 18 out of 20 tasks get validator-class coverage; T19 + T20 are missing. Their tolerances/expected values are therefore unasserted.
- **`TaskDefinition.expected_type` field (Step 18.5) is completely untested.** No assertion on the 20 labels, their count distribution (8/6/6), or the `object.__setattr__` retrofit on a frozen dataclass. The prompt explicitly called this out as a Step 18.5 flag; recommendation is to add ~3 tests (e.g. `test_all_20_tasks_have_expected_type`, `test_type_distribution_matches_spec`, `test_get_task_preserves_expected_type`).

---

### 5. `tests/unit/test_analyze_results.py` (7 tests)

Target: `scripts/analyze_results.py::print_latency_summary` (Step 18.9 narrowed filter).

All 7 tests KEEP. The narrowing (gate_passed AND output_in_range AND NOT is_pilot) is thoroughly exercised:

| Test | Verdict | Purpose |
|---|---|---|
| `test_filter_excludes_failed_runs` | KEEP | gate_passed=False → excluded; mean computed only over success runs. "Excluded: 1" line asserted. |
| `test_filter_excludes_output_in_range_false` | KEEP | gate_passed=True alone is NOT enough — output_in_range=False also counts as excluded. Critical for Step 14.6 output-shape-mismatch semantics. |
| `test_empty_db_prints_no_successful_runs` | KEEP | Empty DB → "no successful runs" + "Excluded: 0". |
| `test_only_failed_runs_prints_no_successful_runs` | KEEP | All-failed DB → "no successful runs" + "Excluded: 2". |
| `test_stdev_n_equals_one_renders_dash` | KEEP | NaN-safe rendering — sample stdev over n=1 is NULL, should render as "-" not "nan". |
| `test_stdev_n_equals_two_computes_real_number` | KEEP | n=2 → real stdev, ~1.4s for latencies 1000ms + 3000ms. |
| `test_pilot_rows_excluded_from_summary` | KEEP | Pilot rows are invisible to the summary — not in mean, not in "Excluded" count. |

**Coverage gap (Step 18.5):** `_gini_coefficient`, `print_gini_summary`, `print_classification_accuracy` are NOT tested in this file. Grep confirms they're not tested anywhere. The filename suggests this is where those Step 18.5 tests should live. MEMORY.md's "+10 unit tests" for Step 18.5 are not here — either in a different batch or never written.

---

### 6. `tests/unit/test_analyze_compression_loss.py` (20 tests)

Target: `scripts/analyze_compression_loss.py` (Step 13.8 — 9-bucket failure taxonomy).

All 20 tests KEEP. Organization matches the analyzer's public surface:

| Class / block | Tests | Verdict |
|---|---|---|
| `TestClassifyFailure` | 9 | KEEP — priority rules: subtask > task-boundary, exception reasons, wrong-value (chain_success + validator reject), gate_rejection, generation_failed, exception fallback, subtask vs exception-reason priority, subtask vs task-boundary priority |
| `test_is_compression_loss_*` | 1 | KEEP — covers both subtask + task-level as compression loss |
| `TestExtractMissingKeys` | 6 | KEEP — parses first bracketed list from reason string, falls back to task-level, empty when neither, single-key + empty-bracket + double-quoted key parsing |
| `test_aggregate_by_*` | 1 | KEEP — 4 rows across 2 conditions, each category counted correctly |
| `test_load_failures_*` | 3 | KEEP — round-trip through real DuckDB (successes excluded), pilot filter, empty DB |

**The priority-rule tests are the meat.** The 9-bucket taxonomy has a strict precedence order; `classify_failure` must route to the most-precise category. All 5 priority edges are explicitly tested:
1. subtask-level beats exception-reason (test line 94)
2. subtask-level beats task-boundary (test line 115)
3. agent_execution_timeout / agent_execution_error get their own buckets even when `failed_step_reason` is generic
4. wrong_value requires chain_success=True + gate_passed=True + output_in_range=False
5. Unclassified chain_step_failed → "exception" fallback

**DB integration is healthy.** `load_failures` is verified against a real in-memory `EvaluationStore`; `failed_step_reason` / `output_shape_mismatch` / `output_shape_missing_keys` all survive the round-trip. `is_pilot` filter is tested.

---

### 7. `tests/unit/test_analyze_longitudinal.py` (36 tests)

Target: `scripts/analyze_longitudinal.py` (Step 18.7 + Step 18.8).

All 36 tests KEEP. Broad coverage across 6 helper functions:

| Class | Tests | Verdict | Step |
|---|---|---|---|
| `TestKLDivergenceBetweenSnapshots` | 8 | KEEP | 18.7 — empty/empty, empty/populated, populated/empty→inf, identical→near-zero, opposite→non-trivial, negative utility scores, union of keys, converged trajectory |
| `TestDetectConvergence` | 8 | KEEP | 18.7 — never converged, converged immediately, converged after warmup (cycle 4), single dip is not convergence, converged→diverged→reconverged (first-window wins), min_consecutive=1 edge, empty list, shorter-than-min_consecutive |
| `TestTableExists` | 3 | KEEP | 18.7 — existing table true, missing false, all 3 Step 18.5/18.7 tables (invocation_events, cycle_utility_scores, cycle_events) exist on fresh store |
| `TestLoadCycleData` | 6 | KEEP | 18.7 — empty DB, static-only excluded, longitudinal in timestamp order with cycle numbering, utility scores attached, events attached, cumulative invocations snapshot |
| `TestDetectShiftPoint` | 4 | KEEP | 18.8 — no shift event → None, shift event → cycle number, first-shift-wins, empty list → None |
| `TestClassifyAdaptation` | 6 | KEEP | 18.8 — three-tier classifier: graceful (≤2x), 2x-boundary still graceful, sluggish (2x-5x), collapse (>5x), never-reconverged → collapse, phase A never converged → indeterminate |
| `TestEndToEnd` | 1 | KEEP | 18.7 end-to-end — 3-cycle populated DB with all four data sources, all expected fields present |

**Step 18.8 shift detection is the Pillar 3 RQ #3 answer.** The `distribution_shift` event type is asserted with `capability_id="__system__"` as the sentinel — matches the schema in `scripts/run_longitudinal_shift.py`.

**Negative utility scores (`test_negative_utility_scores_handled`).** This test is critical — the proposal-literal utility formula is `U = α·reuse·success − β·cost − γ·maint`, which can go negative. The analyzer must shift to non-negative + normalize without raising. The test asserts `np.isfinite(kl) and kl >= 0.0`, which is correct but could be sharper (e.g. assert a specific KL value). Still KEEP.

**Converged-then-diverged-then-reconverged (line 118).** The test asserts first-window wins. This is the documented behavior — `_detect_convergence` does NOT try to distinguish "real convergence" from "transient dip". Per the Step 18.8 docstring, the shift-point analyzer separately handles post-shift re-convergence by slicing the cycles list around the shift point. Clean separation of concerns.

---

### 8. `tests/unit/test_audit_contamination.py` (31 tests)

Target: `scripts/audit_contamination.py` + `scripts/analyze_results.py::print_contamination_rate` + `scripts/analyze_results.py::_clopper_pearson_ci` + Step 18.10 Option B (`generated_artifact_sources` column) + harness `_snapshot_artifacts` helper.

All 31 tests KEEP. Organization:

| Class / block | Tests | Verdict |
|---|---|---|
| `TestFetchUnlabeledRows` | 5 | KEEP — already-labeled excluded, pilot excluded, zero-generated excluded (T01-T03 registry-match path), condition filter, capability-type filter |
| `TestStratifiedSample` | 4 | KEEP — target-per-stratum count, determinism under fixed seed, different seeds → different orders (statistical sanity), stratum-smaller-than-target returns all available |
| `TestUpdateHumanLabel` | 5 | KEEP — insert-update-read round-trip, overwrite semantics, clear via None, invalid label → ValueError, unknown row_id → False |
| `TestClopperPearsonCI` | 5 | KEEP — n=0 sentinel (0.0, 0.0), zero successes lower=0, all successes upper=1, interval contains point estimate, larger sample narrows interval |
| `TestPrintContaminationRate` | 5 | KEEP — empty DB → "Audit not performed", per-condition counts (33.3%), CI bounds rendered, single-condition suppresses pooled row, pilot labels excluded |
| `TestDBSnapshotAuditPath` | 3 | KEEP — Step 18.10 Option B — DB-snapshot column round-trip, empty-tuple when column unpopulated (legacy fallback), multi-snapshot preservation for compositional chains |
| `_snapshot_artifacts` helper | 4 | KEEP — MCP_TOOL source_code extraction, SKILL skill_content extraction, AGENT agent_config JSON serialization, empty/None input |

**Step 18.10 Option B is thoroughly exercised.** The `generated_artifact_sources` column persists `GeneratedArtifactSnapshot` objects; `fetch_unlabeled_rows` returns them as `source_snapshots` tuples. Round-trip for MCP_TOOL (Python source), SKILL (SKILL.md body), and AGENT (JSON-serialized config) is all verified via `_snapshot_artifacts`.

**Clopper-Pearson CI is the proposal-aligned interval.** The test `test_larger_sample_narrows_interval` verifies (3/10) vs (30/100) — same point estimate, tighter CI at larger n. That's the load-bearing property.

**`test_excludes_pilot_labels` (line 312).** Asserts pilot rows with `human_label="contaminated"` do NOT surface in the contamination rate output. Matches `test_pilot_rows_excluded_from_summary` in `test_analyze_results.py`. Correct behavior across the M-metric suite.

---

### 9. `tests/unit/test_fibonacci.py` (4 tests)

Target: `src/mcp_tools/fibonacci.py::fibonacci`.

All 4 KEEP. `@pytest.mark.asyncio` test of the async function: fibonacci(0)=0, (1)=1, (10)=55, (20)=6765. Reference hand-built MCP tool smoke test. Not dead.

**Correction to audit brief:** The prompt said "0 tests / possibly just imports". Pytest collects 4 tests cleanly. No placeholder.

---

### 10. `tests/integration/test_mcp_fibonacci.py` (2 tests)

Target: `src/mcp_tools/fibonacci.py` via MCP stdio transport (subprocess).

Both KEEP. `@pytest.mark.integration` tests that:
1. Start the fibonacci MCP server as subprocess via `uv run python src/mcp_tools/fibonacci.py`
2. Initialize an MCP `ClientSession` over stdio
3. Call `fibonacci(10)` → assert 55; and `list_tools()` → assert "fibonacci" in tool_names

**Not dead.** This is the end-to-end MCP transport smoke test — verifies the hand-built reference server actually works over the MCP protocol. Subprocess-heavy, so it's in `tests/integration/` (not `tests/unit/`). Will skip if the `integration` marker is excluded from a run.

**Correction to audit brief:** The prompt said "0 tests / possibly just imports". Pytest collects 2 tests cleanly.

---

## Cross-File Observations

### Structural patterns

1. **Uniform in-memory DB fixture.** `test_analyze_results.py`, `test_analyze_compression_loss.py`, `test_analyze_longitudinal.py`, and `test_audit_contamination.py` all use `EvaluationStore()` (`:memory:` default) + direct `store._conn` access for query execution. This pattern works reliably and isolates each test. All analyzer tests avoid filesystem-backed DBs — fast (<1s each).

2. **`_record(...)` / `_base_record(...)` helpers.** Each analyzer test file has its own local `EvaluationRecord`-builder helper with sensible defaults and `**overrides`. Functional duplication across 3 files, but intentionally localized to each file's test fixtures — not worth DRYing up. No DUPLICATE flag.

3. **All harness unit tests use `MagicMock()` for `SkillRegistry` + `ToolLoader`.** Correct — these are stable interfaces per CLAUDE.md and shouldn't be hit in Layer-2 harness tests. The Layer-1 smoke tests (`test_harness_static.py`) don't mock anything — they use real fixtures.

### Coverage gaps to fill

**HIGH priority — Step 18.5 artifact tests missing:**

- **`TaskDefinition.expected_type` field has no tests.** Step 18.5 added 20 author-assigned labels (8 mcp_tool / 6 skill / 6 agent) via `_EXPECTED_TYPES` + `object.__setattr__` retrofit. A single test asserting the distribution matches the spec and all 20 T-tasks have a non-None label would close the gap. Best location: `test_evaluation_tasks.py::TestTaskRegistry`.
- **`scripts/analyze_results.py::_gini_coefficient` / `print_gini_summary` / `print_classification_accuracy` have zero dedicated tests** in this batch (or anywhere in `tests/` per grep). MEMORY.md claims "+10 unit tests" for Step 18.5 — those tests are not in this file collection. If the Step 18.5 closeout was correctly landed, these tests exist somewhere; this audit could not find them. Either they're in a different batch file not listed here, or the claim is stale.

**MEDIUM priority — coverage completeness:**

- **T19 + T20 validators untested.** `test_evaluation_tasks.py` covers T01-T18 but not T19/T20. Validator classes `TestT19Validator` + `TestT20Validator` should be added for parity.
- **`tests/evaluation/cycle_flush.py::flush_task_cycle_state` (Step 18.8 refactor) has no dedicated test file.** Its never-raises contract over the three flush batches (invocation_events, cycle_utility_scores, cycle_events) would benefit from direct exercise with a MetricsStore + EvaluationStore pair. Currently only indirectly covered via `test_tracing.py`.

### Fragility observations

1. **`test_stdev_n_equals_one_renders_dash` (test_analyze_results.py:177)** asserts "`-` somewhere in the row for stdev OR `      -` in output". This is a wide assertion — if the rendering changes to `---` or `N/A`, this test passes on the first clause but might miss a real regression. Not currently failing; flagging as mild fragility.

2. **`TestT04Validator::test_cost_too_far_off`** uses hardcoded `product_count=10, items_below_threshold=7` that may not match `_EV["T04"]`. If the T04 validator rejects on product_count mismatch alone, the test is verifying the wrong thing. WEAK flag stands.

3. **`TestT03Validator::test_word_count_slightly_off_passes`** asserts `+1` passes without pinning the tolerance ceiling. WEAK flag stands.

### Positive observations

1. **No tests-codifying-bugs pattern in this batch.** Unlike the T16 / chain-coherence cases called out in MEMORY.md, these tests do not appear to assert broken behavior of any pre-fix regression. The compression-loss priority tests explicitly encode the correct precedence order.

2. **The four MEMORY.md-referenced steps (18.5, 18.7, 18.8, 18.9, 18.10) all have test coverage in this batch** EXCEPT Step 18.5 analyzer helpers (`_gini_coefficient` / `print_gini_summary` / `print_classification_accuracy`) which are absent. Step 18.5's TaskDefinition.expected_type field is also untested.

3. **The 9-bucket compression-loss taxonomy is exhaustively covered.** Every category in `classify_failure` has a direct test + every priority edge.

4. **Never-raises contracts are verified** in multiple places (e.g. `test_execute_artifact_exception_does_not_raise`, `test_registry_match_execution_failure_does_not_raise`) — matches the pipeline's fail-closed strict-gate pattern from §12 findings #14/#15/#16 without requiring flush-failure robustness to propagate.

---

## File paths referenced

- `/Users/tomasz/Developer/master/JustInTimeMAS/tests/unit/test_harness.py`
- `/Users/tomasz/Developer/master/JustInTimeMAS/tests/unit/test_harness_static.py`
- `/Users/tomasz/Developer/master/JustInTimeMAS/tests/unit/test_evaluation_snapshots.py`
- `/Users/tomasz/Developer/master/JustInTimeMAS/tests/unit/test_evaluation_tasks.py`
- `/Users/tomasz/Developer/master/JustInTimeMAS/tests/unit/test_analyze_results.py`
- `/Users/tomasz/Developer/master/JustInTimeMAS/tests/unit/test_analyze_compression_loss.py`
- `/Users/tomasz/Developer/master/JustInTimeMAS/tests/unit/test_analyze_longitudinal.py`
- `/Users/tomasz/Developer/master/JustInTimeMAS/tests/unit/test_audit_contamination.py`
- `/Users/tomasz/Developer/master/JustInTimeMAS/tests/unit/test_fibonacci.py`
- `/Users/tomasz/Developer/master/JustInTimeMAS/tests/integration/test_mcp_fibonacci.py`
- `/Users/tomasz/Developer/master/JustInTimeMAS/tests/evaluation/harness.py`
- `/Users/tomasz/Developer/master/JustInTimeMAS/tests/evaluation/tasks.py`
- `/Users/tomasz/Developer/master/JustInTimeMAS/tests/evaluation/snapshots.py`
- `/Users/tomasz/Developer/master/JustInTimeMAS/tests/evaluation/cycle_flush.py`
- `/Users/tomasz/Developer/master/JustInTimeMAS/scripts/analyze_results.py`
- `/Users/tomasz/Developer/master/JustInTimeMAS/scripts/analyze_compression_loss.py`
- `/Users/tomasz/Developer/master/JustInTimeMAS/scripts/analyze_longitudinal.py`
- `/Users/tomasz/Developer/master/JustInTimeMAS/scripts/audit_contamination.py`
- `/Users/tomasz/Developer/master/JustInTimeMAS/src/mcp_tools/fibonacci.py`
