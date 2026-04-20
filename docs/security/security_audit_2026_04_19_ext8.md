# Security / Robustness Audit — 2026-04-19, Eighth Extension (#56-#78)

**Parent document:** [`security_audit_2026_04_19.md`](./security_audit_2026_04_19.md) — #1-#18 all ✅ FIXED 2026-04-20.
**Sibling extensions:**
- [`security_audit_2026_04_19_ext4.md`](./security_audit_2026_04_19_ext4.md) — #12-#14 ✅ (folded into parent).
- [`security_audit_2026_04_19_ext5.md`](./security_audit_2026_04_19_ext5.md) — #15-#18 ✅ (folded into parent).
- [`security_audit_2026_04_19_ext6.md`](./security_audit_2026_04_19_ext6.md) — #19-#43 ✅ all FIXED 2026-04-20.
- [`security_audit_2026_04_19_ext7.md`](./security_audit_2026_04_19_ext7.md) — #44-#55 ✅ all FIXED 2026-04-20.

**Why this file exists (separate from ext6/ext7).** Five more findings surfaced 2026-04-20 spanning analysis correctness (off-by-one cycle attribution, severity over-counting), statistical methodology (resume-sampling bias), cost attribution (retrieval-task leakage), and cross-store consistency (LangFuse vs DuckDB M1 disagreement). None are duplicates of ext4-ext7; most are independent failure modes rather than remediation-scope drift. Logged as ext8 to preserve the ext-per-audit-pass convention.

**At this audit's landing, ext6 + ext7 were all closed (#19-#55 fixed).** ext8's twenty-three findings below are the only currently-open items across the entire audit series.

**Status at time of write (2026-04-20):** all twenty-three **open**.

| # | Severity | Scope | Impact |
|---|---|---|---|
| 56 | **P0 (CRITICAL)** | `analyze_longitudinal.py::_load_cycle_data` | ❎ **INVESTIGATED 2026-04-20 — audit claim is a FALSE POSITIVE.** Empirical trace: `invocation_events.occurred_at` is set at FLUSH time (`record_invocation_events:346 → datetime.now(UTC)`), AFTER `record_run` in `_execute_run`. Production `occurred_at > evaluation_runs.timestamp`, so the pre-fix loop (`while task_boundaries[cycle_idx+1][0] <= inv_ts`) correctly attributes invocations. Audit assumed `occurred_at < task_boundaries[N]` — would only hold if `record_invocation_events` preserved original `invoked_at` (it overwrites with `datetime.now(UTC)`). Pre-existing `test_analyze_longitudinal.py::TestLoadCycleData` passes (uses occurred_at > task timestamp). Comments in `analyze_longitudinal.py:274-290` now document the empirical trace. A true fix would thread `invoked_at` through the flush — deferred as out of scope. |
| 57 | **P0** | `audit_contamination.py::stratified_sample` resume semantics | ✅ **FIXED 2026-04-20.** New `get_or_create_sample_plan` pre-commits + persists the sample's row ids on first call to a sidecar `audit_sample_plan.json` next to the DB; resumes load the plan + return only the plan-ids-still-unlabeled (NOT a fresh draw from the shrunk pool). Chapter-6 M2 Clopper-Pearson CI's "single pre-committed uniform random sample" assumption now holds across resumes. Corrupt-plan fallback logs WARN + fresh-draws. Regression guards at `tests/unit/test_audit_sample_plan.py` (5 tests: persistence, resume-returns-same-ids, resume-with-shrunk-pool, corrupt-file fallback, DB-scoped path). |
| 58 | **P0** | `backfill_token_costs.py` retrieval exclusion gaps | ✅ **FIXED 2026-04-20.** Bug A: untagged-run fallback zeros `task_id in RETRIEVAL_TASKS` (uniform with the main-path branch) + fallback divisor uses `n_jit_records_in_run` (excludes retrieval rows). Bug B: `compute_run_costs` reads `actual_task_ids` per-run (new field populated by `get_run_windows` via `SELECT DISTINCT run_id, task_id`); `n_jit_tasks = len(actual - RETRIEVAL_TASKS)` replaces the blind-subtract heuristic. Partial runs / all-JIT runs / retrieval-only runs all compute correctly; legacy callers without `actual_task_ids` fall back to the clamped heuristic. Regression guards at `tests/unit/test_backfill_retrieval.py` (5 tests). |
| 59 | **P0** | `harness.py::_run_jit` M1 score vs DuckDB M1 | ✅ **FIXED 2026-04-20 (bundled with #64).** New `EvaluationRecord.is_success(gate_passed, output_in_range, output_shape_mismatch)` classmethod — single source of truth for M1. `harness.py:715` LangFuse scoring uses `EvaluationRecord.is_success(...)` so `m1.success` agrees with DuckDB SQL predicate (ext6 #32 / ext7 #44 tightening). The `success` `@property` delegates. Regression guards at `tests/unit/test_cross_store_parity.py::TestIsSuccessPredicate` + `TestEvaluationRecordSuccessProperty` + `TestLangFuseScoreFormulaMatchesDuckDB`. |
| 60 | HIGH | `analyze_tier2_probes.py::aggregate` severity-by-category | ✅ **FIXED 2026-04-20.** New `ProbeBreakdown.severity_by_event` counter — each event's severity distribution contributes ONCE (sums to actual finding volume). `severity_by_category` retained for the distributional view but relabelled "severity HITS by category" in `render_table` with explicit "summing across categories will EXCEED the per-event total" caveat. Per-event view rendered first with "sums to total finding volume" label. Regression guards at `tests/unit/test_analyze_tier2_probes_severity.py` (4 tests). |
| 61 | MEDIUM | `InvocationRecord.token_cost=0` hardcoded | ❎ **ACKNOWLEDGED (not fixed) 2026-04-20.** Audit downgrades this from user-reported P0 to MEDIUM: no current analyzer reads `invocation_events.token_cost` (M4 flows through `evaluation_runs.token_cost`; M3 Gini is count-based). Latent defect pre-empting Chapter 7 cost-weighted Gini only. Chapter 4.5 disclosure: per-invocation cost column unpopulated; cost-weighted M3 variants deferred. |
| 62 | **P0** | Optimizer `_prev_optimizer_scores` nonlocal wiped per task | ✅ **FIXED 2026-04-20.** New `MetricsStore.load_previous_utility_scores` + `persist_utility_scores` + `optimizer_prev_utility` DuckDB table carry scores across pipeline recompiles (and across runs when store is opened with a persistent path, per #71). `pipeline.py::_optimizer_node` loads via MetricsStore instead of the nonlocal closure; nonlocal removed. Regression guards at `tests/unit/test_metrics_store_persistence.py` (4 tests: empty-on-fresh, roundtrip, replace-not-append, cross-session persistence). |
| 63 | **P0** | Optimizer `_register_artifact_directly` skips schema extraction for SKILL/AGENT | ✅ **PARTIALLY FIXED 2026-04-20 (defensive pragmatic subset).** Full AST-based schema extraction for SKILL (embedded Python fences) + AGENT (config cross-check) is Chapter 7 work (~3-4h per audit). Pragmatic fix: WARN-level log when a SKILL/AGENT variant enters the registry with executable content (`skill_content` / `agent_config`) but BOTH `input_schema` and `output_schema` empty — surfaces the LLM-forgot-schemas case for Chapter 4.5 disclosure rather than silently corrupting the registry. |
| 64 | **P0** | `_extract_result_compositional` shape-mismatch doesn't flip `output_in_range` | ✅ **FIXED 2026-04-20 (bundled with #59).** Both shape-mismatch promotion sites in harness.py (compositional ~line 922 + single-artifact ~line 1230) flip `output_in_range = False` alongside `failure_reason = "output_shape_mismatch"`. `_run_static` does the same via local `in_range`. LangFuse `m1.success` (via `is_success`) and DuckDB SQL predicate agree on every shape-mismatched row. AST-lint in `test_cross_store_parity.py::TestHarnessShapeMismatchFlipsOutputInRange` guards future regressions. |
| 65 | **P0** | `_run_static` never records invocations → `invocation_events` empty for static | ✅ **FIXED 2026-04-20.** `_run_static` signature gained `metrics_store` kwarg; records one `InvocationRecord(capability_id=task.static_capability_name, task_id=..., success=...)` per static run. `_run_one_task` threads it. Static condition now has per-task invocation rows in `invocation_events`, giving Chapter 6 M3 Gini a real static baseline. Regression guards at `tests/unit/test_static_condition_integrity.py` (6 tests). |
| 66 | **P0** | `compute_utility` ignores `definition_token_cost` | ✅ **FIXED 2026-04-20.** `compute_utility` gained `registry` arg + `delta` weight; formula: `U(c) = α·reuse·success − β·token_cost − γ·maintenance − δ·definition_token_cost`. Default `delta=0.01`. `compute_all_utilities` threads the registry. Legacy callers without `registry` see delta-term=0. Regression guards at `tests/unit/test_optimizer_utility.py::TestExt8_66ContextEconomyTerm` (3 tests: larger-def-lowers-U, no-registry-preserves-legacy, delta-configurable). Two pre-existing tests updated to set `cap.definition_token_cost = 0` (MagicMock default made `int(<Mock>) = 1`, breaking pre-fix expected values). |
| 67 | **P0** | LangFuse `m5.latency_ms` score reads nonexistent key | ✅ **FIXED 2026-04-20 (bundled with #59).** `_run_jit` measures `jit_elapsed_ms = int((time.monotonic() - _t_start_jit) * 1000)` around `pipeline.invoke`; LangFuse score reads `float(jit_elapsed_ms or 0)` directly. Regression guards at `tests/unit/test_cross_store_parity.py::TestM5LatencyKeyFix` (stale-key lint + jit_elapsed_ms is-measured-and-used). |
| 68 | **P0** | `_run_static` bypasses `output_contract.check` | ✅ **FIXED 2026-04-20 (bundled with #65).** `_run_static` applies `task.output_contract.check(output, {})`. Shape mismatch promotes `failure_reason = "output_shape_mismatch"` + flips `in_range = False`; return dict carries shape fields. Three no-op branches (no-coverage / cap-missing / exception) also return shape fields as False/[] for schema consistency. Regression test uses a synthesized task with both `has_static_coverage=True` + `required_files=["output.csv"]` (no current testbed task has both — defensive fix). |
| 69 | **P0** | Domain taxonomy classifier lacks retry | ✅ **FIXED 2026-04-20.** `DomainTaxonomy.get_for_query` applies `_MAX_TAXONOMY_ATTEMPTS = 3` retry matching §12 findings #14/#15/#16. Transient failures retry with WARN logs; exhaustion emits ERROR + returns None (preserves never-raises contract). Regression guards at `tests/unit/test_taxonomy.py::TestExt8_69TaxonomyRetry` (3 tests). |
| 70 | MEDIUM | Harness fixture injection + pip padding masks real latency/dependency failures | ❎ **ACKNOWLEDGED (doc-only) 2026-04-20.** Methodology concern, not a code bug. Chapter 4.5 must disclose: (a) fixture content is injected via `_read_fixture_contents` + `_format_fixture_message` rather than capabilities opening files themselves; (b) pip install timeout is 60s (see #74 below — different P0 fix). Optional `--production-mode` harness flag is Chapter 7 future work. |
| 71 | **P0** | Fresh `MetricsStore()` per run wipes `maintenance_burden` | ✅ **FIXED 2026-04-20.** `HarnessConfig.metrics_store_path` field added (default `None` preserves legacy `:memory:` for unit tests); harness `_execute_run` passes it to `MetricsStore(db_path=...)`. Both longitudinal runners use per-campaign paths (`longitudinal_metrics_store.duckdb`, `longitudinal_shift_metrics_store.duckdb`). `maintenance_burden` + optimizer utility scores now accumulate across runs. Regression guards at `tests/unit/test_metrics_store_persistence.py` (5 tests). |
| 72 | **P0** | B2.3 Variation probe uses strict equality | ✅ **FIXED 2026-04-20.** `_test_output_variation` now compares NORMALIZED outputs via `_normalize_for_variation` — regex patterns mask ISO-8601 timestamps, epoch timestamps, UUIDs, and `(count/counter/id/nonce/request_id/trace_id): N` counter-like fields before equality. A contaminated tool whose only inter-call variation is an embedded `datetime.now()` / `uuid.uuid4()` now gets flagged. |
| 73 | MEDIUM | `_check_latency_variance` `mean<0.001 AND cv<0.10` gate fragile | ✅ **FIXED 2026-04-20.** Loosened the `cv < 0.10` tail to an absolute-stdev gate: `stdev < max(2e-4, 0.5 * mean)` (200µs or half-the-mean, whichever larger). Catches sub-millisecond-mean mocks whose natural GC jitter pushes cv > 0.10 while preserving rejection of genuinely-variable real computation. |
| 74 | **P0** | `_sandbox_execute_with_install` 60s pip timeout | ✅ **FIXED 2026-04-20.** Raised from 60s → 180s. Added distinct `error_category` field: `dependency_install_failure` (returncode != 0) / `dependency_install_timeout` (TimeoutExpired). Analyzers can now separate dependency-environment failures from tool-correctness failures when attributing M1. |
| 75 | MEDIUM | `quick_validate.py::validate_skill` doesn't reject redundant `---` delimiters | ✅ **FIXED 2026-04-20.** `validate_skill` counts bare-line `---` occurrences after frontmatter extraction; expects exactly 2 (open + close). `count > 2` → loud rejection with explanatory message ("use '***' or indented '---' for horizontal rules"). Prevents silent body-erasure at DeepAgents load time. |
| 76 | **P0** | `is_generic=True` bypasses all domain filters with no defense-in-depth | ✅ **FIXED 2026-04-20.** `classify_and_tag_artifacts` now runs an AST cross-check: if LLM said `is_generic=True` but the artifact's `source_code` / `skill_content` / `agent_config` contains quoted string literals matching `_DOMAIN_SPECIFIC_LITERALS` (`order_id`, `product_id`, `unit_price`, `customer_id`, `company_name`, `reorder`, `inventory`, `needs_reorder`, `days_of_stock`, `region_revenue`), `is_generic` is forced to False with a WARN log. Defense-in-depth against single-LLM-decision cross-domain pollution. |
| 77 | **P0** | Convergence detection uses consecutive-snapshot KL instead of stability-to-final | ✅ **FIXED 2026-04-20.** New `_detect_stable_convergence(snapshots, threshold, min_consecutive, window_size=5)` compares each cycle to the FINAL-WINDOW-AVERAGED distribution (proposal-aligned "distance-to-attractor" metric). Existing `_detect_convergence` (local-stability) kept + documented as the complementary signal. Helper `_average_distributions` for the smoothing. Chapter 6 should cite both for defensibility. |
| 78 | MEDIUM | Gini includes pruned (inactive) capabilities via historical invocation_events | ✅ **FIXED 2026-04-20.** `print_gini_summary` Gini query now `LEFT JOIN`s against `cycle_events WHERE event_type = 'pruned'` and filters `pruned.capability_id IS NULL`. Pruned caps' historical invocations no longer inflate Gini. Graceful fallback to the pre-fix query for legacy DBs without the `cycle_events` table (try/except at query level). |

**Numbering.** Continues from ext7's #55. #56-#60 first batch; #61-#65 second batch; #66-#71 third batch; #72-#78 fourth batch (all 2026-04-20).

---

## Issue #56 — `analyze_longitudinal.py::_load_cycle_data` off-by-one cycle attribution invalidates all longitudinal Pillar-3 metrics (P0, CRITICAL)

### Summary

`_load_cycle_data` buckets invocation events, utility scores, and cycle events into per-cycle windows by comparing timestamps to a `task_boundaries` list built from `evaluation_runs.timestamp`. But `evaluation_runs.timestamp` is populated via `EvaluationRecord(...).timestamp`'s `default_factory=lambda: datetime.now(UTC)`, which fires at **task-completion time** (after `_run_jit` / `_run_static` returns). So `task_boundaries` are END timestamps, not start timestamps.

The advancement loop:

```python
while (
    cycle_idx + 1 < len(task_boundaries)
    and task_boundaries[cycle_idx + 1][0] <= inv_ts
):
    cycle_idx += 1
```

advances `cycle_idx` only when the NEXT task's END timestamp is ≤ the current invocation's timestamp. During task N's execution, invocations have `occurred_at < task_boundaries[N]` (the task hasn't ended yet). So the condition `task_boundaries[N] <= inv_ts` is False, and `cycle_idx` stays at N-1. **Every invocation during task N is attributed to task N-1.**

This cascades across the full longitudinal sequence:
- Task 0's invocations → attributed to cycle 0 (correctly, by coincidence — no "cycle -1" exists)
- Task 1's invocations → attributed to cycle 0 (**wrong**; should be cycle 1)
- Task 2's invocations → attributed to cycle 1
- … every subsequent attribution is shifted by one cycle.

Downstream effects:
- **KL convergence curves**: per-cycle distributions are measured against the WRONG cycle's actual behavior. "Cycle 5 converged" actually describes cycle 6's distribution.
- **Gini coefficient**: per-cycle Gini shifts by one cycle. The curve from cycle 0 to cycle N describes cycles -1 to N-1.
- **Shift-detection boundaries** (Step 18.8): distribution-shift detection looks for a KL spike at the shift-at cycle. With a one-cycle offset, the detected boundary is systematically off by one cycle; the "graceful/sluggish/collapse" adaptation verdict reads from the wrong side of the shift.

### Where

- **The flawed attribution loop:** `scripts/analyze_longitudinal.py:292-300`:
  ```python
  cycle_idx = 0
  for inv_task_id, cap_id, inv_ts in inv_rows:
      while (
          cycle_idx + 1 < len(task_boundaries)
          and task_boundaries[cycle_idx + 1][0] <= inv_ts
      ):
          cycle_idx += 1
      cycle_increment[cycle_idx][cap_id] += 1
  ```
- **Same pattern for utility scores:** `scripts/analyze_longitudinal.py:324-328` (`c_idx` variant over `cycle_utility_scores`).
- **Same pattern for cycle events:** `scripts/analyze_longitudinal.py:348-352`.
- **`task_boundaries` constructed from end-time timestamps:** `scripts/analyze_longitudinal.py:285-288`:
  ```python
  task_boundaries = [
      (task_ts, (run_id, task_id))
      for run_id, task_id, task_ts in task_rows
  ]
  ```
  where `task_ts` comes from `evaluation_runs.timestamp`.
- **Timestamp source is task-end:** `src/schemas/evaluation.py:313-316`:
  ```python
  timestamp: datetime = Field(
      default_factory=lambda: datetime.now(UTC),
      description="When this record was written",
  )
  ```
  The `EvaluationRecord(...)` constructor is called AFTER `result = self._run_jit(...)` at `tests/evaluation/harness.py:491-493` — the default_factory fires at that point, i.e., at task completion.

### Reproduction

```python
# Synthetic longitudinal DB:
# Task 0 runs t=0..10; evaluation_runs row timestamp = 10s
# Task 1 runs t=11..20; evaluation_runs row timestamp = 20s
# Task 2 runs t=21..30; evaluation_runs row timestamp = 30s
# invocation_events:
#   during task 0 at t=5  → occurred_at=5
#   during task 1 at t=15 → occurred_at=15
#   during task 2 at t=25 → occurred_at=25

task_boundaries = [(10, ...), (20, ...), (30, ...)]

# Invocation 1 (t=5): while-loop checks 20 <= 5 → False; cycle_idx=0 ✓
# Invocation 2 (t=15): while-loop checks 20 <= 15 → False; cycle_idx=0 ✗ (should be 1)
# Invocation 3 (t=25): while-loop checks 20 <= 25 → True; cycle_idx=1 ✗ (should be 2)

# Every invocation is attributed to the PREVIOUS cycle's window.
```

Confirmed by direct trace of the code paths.

### Threat model / impact

- **Pillar 3 RQ #2 (cycles-to-convergence) — FALSIFIED.** The convergence detection uses per-cycle KL deltas; with a one-cycle offset, the detected convergence cycle is off by one. Chapter 6's claim "JIT converges in K cycles" is off by one.
- **Pillar 3 RQ #3 (distribution-shift adaptation, Step 18.8) — FALSIFIED.** The shift-detection compares pre-shift vs post-shift KL. With off-by-one bucketing, the "shift cycle" in the analyzer is one cycle before the real shift. The graceful/sluggish/collapse verdict reads from the wrong side of the boundary.
- **M3 Gini per-cycle — FALSIFIED.** Per-cycle capability-usage concentration shifts by one. The "seeded vs evolved" Gini comparison is measuring different actual cycles than the comparison intends.
- **Chapter 6 Pillar 3 tables are fully invalid.** Unlike most ext6/7 findings which affect one metric or one condition, this one corrupts **all three Pillar-3 headline metrics simultaneously** because they all flow through `_load_cycle_data`.

Direction of bias: **cycle-aligned shift** — magnitudes may be approximately right (N invocations in cycle N is approximately N invocations in cycle N-1 as measured) but **causal-temporal alignment is broken** — which is what Pillar 3 RQs claim to measure.

### Remediation

**A. Use HALF-OPEN intervals with start-of-next-task as the boundary.**

The correct interpretation: invocation with `occurred_at = t` belongs to task N iff `start(task_N) ≤ t < start(task_{N+1})`. Today's code uses END times; the fix is to use the NEXT task's end as the boundary condition, but compared with `<` not `<=`:

```python
# BEFORE:
while (
    cycle_idx + 1 < len(task_boundaries)
    and task_boundaries[cycle_idx + 1][0] <= inv_ts
):
    cycle_idx += 1

# AFTER (correct with END timestamps):
while (
    cycle_idx + 1 < len(task_boundaries)
    and task_boundaries[cycle_idx][0] < inv_ts  # NOTE: cycle_idx, not cycle_idx+1
):
    cycle_idx += 1
```

Semantics: advance past cycle N when the invocation timestamp is past cycle N's END. Before advancing past cycle 0, invocations in (task_boundaries[0], task_boundaries[1]] are attributed to cycle 1 — which is the correct window for task 1's invocations.

Wait — this still has an edge case. Let me rewrite more carefully:

With END timestamps, task N's "window" is `(task_boundaries[N-1], task_boundaries[N]]` (half-open below, inclusive above). For an invocation at time t:
- Find the smallest N such that `t ≤ task_boundaries[N]`.
- Attribute to cycle N.

Translating:
```python
for inv_task_id, cap_id, inv_ts in inv_rows:
    while (
        cycle_idx < len(task_boundaries)
        and task_boundaries[cycle_idx][0] < inv_ts  # Keep advancing while inv_ts > boundary[cycle_idx]
    ):
        cycle_idx += 1
    if cycle_idx < len(task_boundaries):
        cycle_increment[cycle_idx][cap_id] += 1
    # else: invocation occurred after the last task — unusual, can drop or count in last cycle
```

**B. Or (cleaner): add start-timestamp to evaluation_runs + attribute by [start, end] window.**

The cleanest fix is to record BOTH start and end timestamps:

```python
# src/schemas/evaluation.py — new field:
timestamp_start: datetime = Field(
    default_factory=lambda: datetime.now(UTC),
    description="When the task began execution (set at _run_* entry)",
)
# Existing `timestamp` remains as completion time.

# tests/evaluation/harness.py:476 — set start timestamp:
ts_start = datetime.now(UTC)
...
return EvaluationRecord(
    ...,
    timestamp_start=ts_start,
    # timestamp set by default_factory at record construction = task end
)
```

Then `_load_cycle_data` uses `[timestamp_start, timestamp]` half-open intervals. Unambiguous per-cycle attribution.

**C. Regression test.**

```python
def test_load_cycle_data_attributes_invocations_to_correct_cycle(tmp_path):
    """Invocation during task 1's window must attribute to cycle 1, not cycle 0."""
    db = seed_synthetic_longitudinal_db(
        tmp_path,
        tasks=[
            {"task_id": "T01", "start_ts": 0, "end_ts": 10},
            {"task_id": "T02", "start_ts": 11, "end_ts": 20},
        ],
        invocations=[
            {"task_id": "T01", "occurred_at": 5, "cap": "A"},
            {"task_id": "T02", "occurred_at": 15, "cap": "B"},
        ],
    )
    cycles = _load_cycle_data(db, run_id="test_run")
    assert cycles[0]["incremental_invocations"] == {"A": 1}
    assert cycles[1]["incremental_invocations"] == {"B": 1}
    # Pre-fix: cycles[0] = {"A": 1, "B": 1}, cycles[1] = {}
```

### Recommendation

**Land immediately — blocks every row-18/19 analysis.** All three Pillar-3 RQ conclusions in Chapter 6 are invalid pre-fix. (A) is a one-line-plus-off-by-one-variable-rename fix; (B) is the proposal-correct structural fix (~1h with schema migration); (C) is the defence. Required before any longitudinal re-run; also requires re-running rows 18/19 analysis post-fix against the existing DB (data is fine, only the aggregation is wrong).

### Legacy data

`evaluation_runs.timestamp` (task END) and `invocation_events.occurred_at` are correctly recorded. Re-running `analyze_longitudinal.py` post-fix against the same DB produces correctly-aligned output; no data collection change needed.

---

## Issue #57 — `audit_contamination.py::stratified_sample` resume with constant seed produces incoherent multi-session samples (P0)

### Summary

`stratified_sample` uses `rng = random.Random(42)` (default seed) to shuffle each stratum and take the first `per_stratum` items. The function is called on a caller-provided `rows` list, which (per `audit_contamination.py:128-134`) excludes already-labeled rows via `WHERE human_label IS NULL`.

Single-invocation semantics: within one call, the seeded shuffle produces a valid uniform random subset of the provided rows. This is fine.

Multi-session resume semantics: when an auditor labels some items, quits, and resumes:
- Session 1's `rows` = full unlabeled set S1.
- Session 1's output = first `per_stratum` under `π_42(S1)`.
- Auditor labels some of those items.
- Session 2's `rows` = S1 \ {labeled} = S2 ⊂ S1.
- Session 2's output = first `per_stratum` under `π_42(S2)`.

Session 2's output is a valid random subset of S2, but the COMBINED labeled set across sessions is no longer a coherent "stratified random sample of size K from S1" — it's samples from two different pools with overlapping ancestry. The statistical framework underlying Chapter 6's M2 claim (Clopper-Pearson CI on the contamination rate) assumes a single pre-committed sample from a fixed pool.

The bug isn't "the sample within a session isn't random" — it's "the combined multi-session sample doesn't satisfy the pre-registered sampling plan." For an audit tool that is **designed to be resumable** (line 195 docstring calls out "Resumable" as a feature), this is a load-bearing correctness issue.

### Where

- **Sampler with constant-seed reset each run:** `scripts/audit_contamination.py:194-215`:
  ```python
  def stratified_sample(
      rows: list[AuditRow],
      *,
      conditions, types, per_stratum, seed,
  ) -> list[AuditRow]:
      """Sample `per_stratum` rows from each (condition, type) stratum.
      ...
      Deterministic given `seed` — re-running with the same seed on the
      same DB produces the same sample order.
      """
      rng = random.Random(seed)  # default seed = 42
      ...
      for condition in conditions:
          for cap_type in types:
              stratum = [r for r in rows if r.condition == condition and r.capability_type == cap_type]
              rng.shuffle(stratum)
              out.extend(stratum[:per_stratum])
      return out
  ```
- **Callers provide unlabeled-only rows:** `scripts/audit_contamination.py:128-134` — `WHERE human_label IS NULL` in the fetch query means each session's `rows` excludes previously-labeled items.

### Reproduction

```python
# Synthetic stratum of 10 items [A, B, ..., J].
items = [f"row_{c}" for c in "ABCDEFGHIJ"]

# Session 1 with seed=42, per_stratum=5:
rng1 = random.Random(42)
s1 = list(items)
rng1.shuffle(s1)
sample_1 = s1[:5]  # e.g., [row_C, row_F, row_A, row_H, row_D]

# Auditor labels those 5. Remaining = [B, E, G, I, J].

# Session 2 with seed=42, per_stratum=5 on remaining:
rng2 = random.Random(42)
s2 = ["row_B", "row_E", "row_G", "row_I", "row_J"]
rng2.shuffle(s2)
sample_2 = s2[:5]  # e.g., [row_G, row_I, row_B, row_J, row_E] — all 5 remaining

# In this case total labeled = all 10 items = full stratum, so no statistical issue.
# But consider per_stratum=3 across two sessions: total sampled = 3 + 3 = 6 out of 10.
# The question: is {A,C,D,F,H} ∪ {items shown in session 2} a uniform random 6-subset of 10?
# No — session 1 pre-commits items in A,C,D,F,H positions; session 2's 3 are drawn from the 5
# remaining, NOT the original 10. The joint distribution over "which 6 items are labeled"
# differs from "uniform 6-subset of 10".
```

The statistical issue manifests when the aggregate sample is used for inference about the population: pre-registered Clopper-Pearson CI formulae assume a single pre-committed uniform random sample, not a union of per-session samples from shrinking pools.

### Why the design docstring calls this a feature (and why it's still a bug)

The docstring at line 205-206:
> "Deterministic given `seed` — re-running with the same seed on the same DB produces the same sample order."

This is true for a single pool — but the callers never call with the same pool twice (by design — resume filters out labeled rows). The docstring describes a scenario that doesn't occur in practice; the actual multi-session flow has different semantics that the docstring doesn't describe.

### Threat model / impact

- **M2 contamination-rate CI arithmetic is invalid when audit is resumed.** Chapter 6 reports M2 as "X% contamination (95% CI [Y, Z])" using Clopper-Pearson. That CI assumes the labeled sample is a single uniform random sample from the population; it's not.
- **Direction of bias: depends on the interaction between seed-ordering and labeler quit timing.** If the auditor quits at item 5 of 9, session 2 samples from items 6-9. If at item 7 of 9, samples from items 8-9. The labeled-set composition varies by quit timing — not random.
- **Reader-confidence impact.** A reviewer who inspects `audit_contamination.py` sees `random.Random(42)` and assumes standard stratified sampling. The documentation of resume semantics needs to match the statistical framework Chapter 6 invokes.

### Remediation

**A. Pre-commit the sample IDs on first invocation; reload them on resume.**

```python
# scripts/audit_contamination.py — persist the sample plan on first invocation:

SAMPLE_PLAN_FILE = DATA_DIR / "audit_sample_plan.json"

def get_or_create_sample_plan(rows, *, conditions, types, per_stratum, seed) -> list[AuditRow]:
    """Return the pre-committed sample plan, creating + persisting it on first call."""
    if SAMPLE_PLAN_FILE.exists():
        plan_ids = json.loads(SAMPLE_PLAN_FILE.read_text())
        # Filter `rows` to those in the plan (order preserved from plan):
        id_to_row = {r.id: r for r in rows}
        return [id_to_row[rid] for rid in plan_ids if rid in id_to_row]
    # First invocation — draw the sample and persist its IDs.
    sample = stratified_sample(
        rows, conditions=conditions, types=types,
        per_stratum=per_stratum, seed=seed,
    )
    SAMPLE_PLAN_FILE.write_text(json.dumps([r.id for r in sample]))
    return sample
```

The auditor's resume calls `get_or_create_sample_plan()`, which on session 2+ reads the pre-committed list of IDs and filters to unlabeled items in that list. The sample is pre-registered; resume never re-draws.

**B. Alternative: use a campaign-scoped seed derived from stable identifiers.**

```python
campaign_id = ...  # per run sheet's $CAMPAIGN_ID
seed = int(hashlib.sha256(f"{campaign_id}|audit_m2".encode()).hexdigest()[:8], 16) % (2**31)
rng = random.Random(seed)
```

Doesn't fix the multi-session-pool issue but at least the seed is stable across runs AND correlated with the campaign.

(A) is the statistically-sound fix; (B) is a band-aid that helps reproducibility but doesn't fix the pool-shrinkage semantics.

**C. Document the resume semantics explicitly + warn when detected.**

```python
if len(labeled_in_db) > 0 and not SAMPLE_PLAN_FILE.exists():
    print(
        "WARNING: DB already contains human-labeled rows but no sample plan file exists. "
        "This indicates either (a) a prior run without the pre-commit fix, or (b) the "
        "plan file was deleted. Chapter 6's M2 Clopper-Pearson CI assumes a pre-committed "
        "uniform random sample; the current labeled set may not satisfy that assumption. "
        "Options: (1) re-create a plan from scratch and label fresh items, "
        "(2) acknowledge the procedural drift in Chapter 4.5.",
        file=sys.stderr,
    )
```

### Recommendation

**Land (A) before any M2 labeling resumes.** ~40 LOC + 1 regression test. ~45 min. If labeling has already started under the pre-fix procedure, discuss with the advisor whether to restart labeling or disclose the procedural deviation in Chapter 4.5.

### Legacy data

Existing human_label values were produced under the pre-fix procedure. Decide with advisor: (a) clear existing labels and re-run M2 with the pre-committed plan (costs another human-audit pass), or (b) document the procedural deviation in Chapter 4.5 and acknowledge the CI's formal-assumption violation.

---

## Issue #58 — `backfill_token_costs.py` retrieval-task exclusion gaps inflate T01-T03 M4 cost and distort `n_jit_tasks` divisor (P0)

### Summary

Two distinct retrieval-task-handling bugs in the same file:

**Bug A — untagged-run fallback doesn't zero retrieval tasks.**
`compute_per_task_costs`'s fallback branch (ext6 #37 added this to avoid silent-0 when a run lacks task_id tags) blindly divides `run_total / n_records` and assigns to every record, including T01/T02/T03. The original `compute_backfill_values` at line 310-312 explicitly zeroes retrieval tasks:

```python
if rec["task_id"] in RETRIEVAL_TASKS:
    updates.append((rec["id"], 0))
    continue
```

The untagged-run fallback at line 575-583 skips this check:

```python
else:
    summary = (run_summaries or {}).get(rid)
    run_total = (summary or {}).get("total_tokens", 0)
    n_records = run_record_counts.get(rid, 1) or 1
    tokens = run_total // n_records
updates.append((rec["id"], tokens))
```

Retrieval tasks get a share of the JIT tokens they didn't incur.

**Bug B — `compute_run_costs` blindly subtracts 3 for `n_jit_tasks`.**
Lines 245-250:
```python
n_jit_tasks = rw["n_distinct_tasks"] - len(RETRIEVAL_TASKS)  # subtract 3
if rw["n_distinct_tasks"] >= 13:
    n_jit_tasks = rw["n_distinct_tasks"] - 3
```
(Lines 245-246 and 249-250 are redundant — both subtract the same 3.)

The subtraction assumes T01/T02/T03 are always among the run's `n_distinct_tasks`. For a partial-run invocation (`run_pilot.py --tasks T11,T12,T13`), `n_distinct_tasks=3`, `n_jit_tasks = 3-3 = 0`, and `tokens_per_jit_task` collapses to 0 despite all 3 being JIT tasks. For a mixed partial-run (`--tasks T01,T05,T07`), same issue: `n_distinct_tasks=3`, `n_jit_tasks = 0`, but only ONE of those is a retrieval task.

### Where

- **Bug A — untagged-run fallback missing retrieval-zero:** `scripts/backfill_token_costs.py:575-583`.
- **Bug A — reference (correct path for comparison):** `scripts/backfill_token_costs.py:310-312` (`compute_backfill_values`).
- **Bug B — blind subtraction:** `scripts/backfill_token_costs.py:241-250` (`compute_run_costs`).
- **Retrieval-task constant:** `scripts/backfill_token_costs.py:47` — `RETRIEVAL_TASKS = {"T01", "T02", "T03"}`.

### Reproduction

**Bug A:**
```python
records = [
    {"id": "a", "run_id": "r1", "task_id": "T01", "condition": "full"},  # retrieval
    {"id": "b", "run_id": "r1", "task_id": "T05", "condition": "full"},  # JIT
]
run_summaries = {"r1": {"total_tokens": 1000, ...}}
run_record_counts = {"r1": 2}  # 2 records for this run
tagged_run_ids = set()  # r1 is untagged → hits fallback

updates = compute_per_task_costs(records, ...)
# Both records get 1000 // 2 = 500 tokens
# But T01 should get 0.
```

**Bug B:**
```python
# Partial run: --tasks T11,T12,T13 (3 JIT tasks, zero retrieval)
rw = {"n_distinct_tasks": 3, "condition": "full"}
n_jit_tasks = rw["n_distinct_tasks"] - len(RETRIEVAL_TASKS)  # 3 - 3 = 0
# Guard at line 268: `tokens_per_jit_task = total // n_jit_tasks if n_jit_tasks > 0 else 0`
# → per-jit-task cost shows 0 despite 3 JIT tasks running.
```

### Threat model / impact

- **Chapter 6 M4 (token cost) inflated for retrieval tasks + undercounted for JIT tasks under Bug A.**
- **Chapter 6 M4 silently zeroes out partial-run cost summaries under Bug B.** Any run that doesn't include all 3 retrieval tasks plus ≥4 JIT tasks produces misleading `tokens_per_jit_task=0` in the run summary table.
- **Compounds with ext6 #28 (longitudinal divisor 2× inflation) and ext7 #37 (per-task untagged-run zeroes).** The backfill script is the highest-density bug locus in the analysis surface — four distinct correctness issues (28, 30, 37, 58A+B) in one file across three call paths.
- **Direction of bias A: pro-retrieval, anti-JIT.** Retrieval tasks look more expensive than they are; JIT tasks look cheaper. Chapter 6's "JIT is cost-efficient" claim is artificially strengthened.
- **Direction of bias B: silent zero in partial runs.** Partial-run cost summaries appear as "zero cost per JIT task," which a reader interprets as "this JIT condition was free" — completely wrong.

### Remediation

**A. Add retrieval-task zero to the untagged-run fallback.**

```python
# scripts/backfill_token_costs.py:575 — before:
else:
    summary = (run_summaries or {}).get(rid)
    run_total = (summary or {}).get("total_tokens", 0)
    n_records = run_record_counts.get(rid, 1) or 1
    tokens = run_total // n_records
updates.append((rec["id"], tokens))

# after:
else:
    if rec["task_id"] in RETRIEVAL_TASKS:
        tokens = 0
    else:
        summary = (run_summaries or {}).get(rid)
        run_total = (summary or {}).get("total_tokens", 0)
        n_jit_records = run_jit_record_counts.get(rid, 1) or 1  # excludes retrieval rows
        tokens = run_total // n_jit_records
updates.append((rec["id"], tokens))
```

Note: switch divisor to `n_jit_records` (rows excluding retrieval) instead of `n_records` (all rows). Otherwise the JIT-share is proportional to all rows including retrieval-zeros, which understates per-JIT cost.

**B. Compute `n_jit_tasks` from actual run contents, not blind subtraction.**

```python
# scripts/backfill_token_costs.py:237 — add query for actual task_ids per run:
actual_task_ids_per_run = {
    rid: {r[0] for r in conn.execute(
        "SELECT DISTINCT task_id FROM evaluation_runs WHERE run_id = ? AND NOT is_pilot",
        [rid],
    ).fetchall()}
    for rid in run_ids
}

# Inside compute_run_costs:
actual_tasks = actual_task_ids_per_run[rid]
actual_retrieval = actual_tasks & RETRIEVAL_TASKS
actual_jit = actual_tasks - RETRIEVAL_TASKS
n_jit_tasks = len(actual_jit)  # exact count, not n_distinct_tasks - 3
```

Closes Bug B. Works for partial runs, for all-JIT runs, for retrieval-only runs.

**C. Regression tests.**

```python
def test_untagged_run_fallback_zeros_retrieval_tasks():
    records = [
        {"id": "a", "run_id": "r1", "task_id": "T01", "condition": "full"},
        {"id": "b", "run_id": "r1", "task_id": "T05", "condition": "full"},
    ]
    updates = compute_per_task_costs(records, ...)
    assert dict(updates)["a"] == 0  # retrieval task zeroed

def test_n_jit_tasks_counts_actual_task_ids():
    # Run with tasks=[T11, T12, T13] → n_jit_tasks == 3, not 0.
    rw = {...}
    summary = compute_run_costs(...)
    assert summary["n_jit_tasks"] == 3
```

### Recommendation

**Bundle with ext6 #28 + ext6 #30 + ext7 #36 + ext7 #37 as the `backfill_token_costs.py` cleanup pass.** Five independent bugs in one file; land them in one PR with a unified set of regression tests. Estimated ~2-3h for the backfill bundle end-to-end.

### Legacy data

Re-running the fixed backfill script against the existing DB produces corrected numbers. LangFuse source data is unchanged.

---

## Issue #59 — `harness.py::_run_jit` LangFuse `m1.success` score disagrees with DuckDB M1 for shape-mismatched rows (P0)

### Summary

The harness calls LangFuse `create_score(name="m1.success", value=success_val)` with:

```python
success_val = (
    float(result.get("gate_passed", False))
    * float(result.get("output_in_range", False))
)
```

This formula matches the DuckDB success predicate **pre-ext7-#44-fix**. Ext6 #32 + ext7 #44 tightened the DuckDB predicate to `gate_passed AND output_in_range AND NOT output_shape_mismatch` — the LangFuse scoring formula was never updated to match.

Effect: a row that hits the output-contract failure path (`_run_jit` sets `failure_reason = "output_shape_mismatch"` at `harness.py:909-914` but leaves `output_in_range` unchanged) gets:
- DuckDB M1: **failure** (per the tightened predicate).
- LangFuse `m1.success` score: **1.0** (per the pre-tightening formula).

Anyone cross-referencing Chapter 6 M1 tables with LangFuse dashboards sees a discrepancy with no log-level explanation.

### Where

- **Score formula (pre-tightening):** `tests/evaluation/harness.py:715-718`:
  ```python
  success_val = (
      float(result.get("gate_passed", False))
      * float(result.get("output_in_range", False))
  )
  ```
- **Failure-reason promotion that doesn't flip output_in_range:** `tests/evaluation/harness.py:909-914`:
  ```python
  if (
      failure_reason == "none"
      and shape_check is not None
      and not shape_check.satisfied
  ):
      failure_reason = "output_shape_mismatch"
  ```
  (No accompanying `output_in_range = False`.)
- **DuckDB analyzer predicate (tightened per ext6 #32 / ext7 #44):** `scripts/analyze_results.py:128` (planned post-fix): `gate_passed AND output_in_range AND NOT output_shape_mismatch`.

### Reproduction

```python
# T07 run with dict-only output (missing required output.csv):
result = _run_jit(task_t07, ...)
# result = {
#   "gate_passed": True,
#   "output_in_range": True,       # dict validator happy
#   "output_shape_mismatch": True, # contract check failed
#   "failure_reason": "output_shape_mismatch",
# }

# harness.py:715 computes:
success_val = float(True) * float(True)  # = 1.0
# LangFuse receives m1.success=1.0

# analyze_results.py (post-#44-fix):
# WHERE gate_passed AND output_in_range AND NOT output_shape_mismatch
# → excludes this row from successes
# DuckDB M1 for this row = 0
```

Two authoritative sources for the same metric, disagreement built into the current code.

### Threat model / impact

- **LangFuse dashboard M1 reads different from Chapter 6 tables.** A thesis committee or advisor cross-checking the number gets inconsistent answers depending on source.
- **Harder-to-diagnose debugging.** A per-task drill-down in LangFuse shows `m1.success=1.0`; the same task in DuckDB shows failure. Debugging which is "right" requires knowing ext6 #32's policy — most readers don't.
- **Direction of bias: LangFuse over-reports success.** Any shape-mismatched row inflates LangFuse M1 relative to DuckDB M1.
- **Trust in LangFuse as a secondary data source erodes.** Future cross-referencing will be treated with suspicion even on metrics where LangFuse is correct.

### Remediation

**A. Factor success into a shared helper, use it in both places.**

```python
# src/schemas/evaluation.py — add class method:
@classmethod
def is_success(cls, *, gate_passed: bool, output_in_range: bool, output_shape_mismatch: bool = False) -> bool:
    """Canonical M1 success predicate. Must be used everywhere M1 is computed."""
    return gate_passed and output_in_range and not output_shape_mismatch

# tests/evaluation/harness.py:715 — use it:
success_val = 1.0 if EvaluationRecord.is_success(
    gate_passed=bool(result.get("gate_passed", False)),
    output_in_range=bool(result.get("output_in_range", False)),
    output_shape_mismatch=bool(result.get("output_shape_mismatch", False)),
) else 0.0

# src/schemas/evaluation.py — update the `success` @property (line 321-323):
@property
def success(self) -> bool:
    """Composite success: gate accepted AND output is correct AND shape is valid."""
    return self.__class__.is_success(
        gate_passed=self.gate_passed,
        output_in_range=self.output_in_range,
        output_shape_mismatch=self.output_shape_mismatch,
    )

# scripts/analyze_results.py — ideally consume the same predicate, or
# document that SQL WHERE mirrors EvaluationRecord.is_success verbatim.
```

Single source of truth for M1; DuckDB predicate and LangFuse score compute the same thing.

**B. Regression test ensuring LangFuse-DuckDB parity.**

```python
def test_langfuse_m1_score_matches_duckdb_success_predicate():
    """A shape-mismatched row must score 0.0 in LangFuse AND fail the DuckDB predicate."""
    result = {
        "gate_passed": True,
        "output_in_range": True,
        "output_shape_mismatch": True,
    }
    # Extract success_val from the harness's m1.success computation path:
    success_val = compute_m1_success_val(result)
    assert success_val == 0.0

    # DuckDB predicate equivalence:
    row = EvaluationRecord(
        gate_passed=True, output_in_range=True,
        output_shape_mismatch=True, ...,
    )
    assert row.success is False
```

**C. Update CLAUDE.md.**

Document that M1 has a single canonical predicate `EvaluationRecord.is_success(...)` and any new consumer (dashboards, alerts, downstream scripts) must use it or mirror it exactly.

### Recommendation

**Land with ext7 #44.** Both are M1-predicate correctness fixes; landing #44 without #59 fixes DuckDB but leaves LangFuse inconsistent. ~20 LOC + 1 test. ~30 min additional to #44's estimate.

### Legacy data

Existing LangFuse scores are wrong for shape-mismatched rows. Options: (a) re-compute + overwrite scores by replaying the DB through the corrected formula, (b) add a "legacy scores may be incorrect" disclaimer to any pre-fix dashboard. (a) is cleaner if LangFuse is queried post-fix; (b) suffices if the dashboards are one-shot.

---

## Issue #60 — `analyze_tier2_probes.py::aggregate` multiplies severity counts by number of categories on multi-category events (HIGH)

### Summary

The Tier-2 probe aggregator walks each LangFuse event, reads its list of categories and its event-level `severity_distribution`, and for each category increments `severity_by_category[cat][sev]` by the full count. An event with 3 categories and `{"HIGH": 5}` writes 5 to HIGH under all three categories — total "HIGH" hits across categories = 15, actual finding count = 5.

The code comment at `analyze_tier2_probes.py:238-242` acknowledges the behavior:

> "Attribute the severity distribution to each category in the event. (LangFuse stores severity_distribution at the event level, not per-finding, so this is an overcount on multi-category events — reported as 'severity hits' rather than 'per-category severity'.)"

So it's documented as intentional. But the downstream CSV + terminal output doesn't carry this framing — a Chapter 6 reader sees the numbers as "per-category severity counts" (what the column is implicitly named) rather than "severity hits with multi-category overcount." The labels don't match the semantics.

### Where

`scripts/analyze_tier2_probes.py:205-249` — `aggregate()` function:
```python
for cat in categories:
    if not isinstance(cat, str):
        continue
    bd.categories[cat] += 1
    bd.capability_types_by_category[cat][cap_type] += 1

    if isinstance(severity_dist, dict):
        for sev, count in severity_dist.items():
            bd.severity_by_category[cat][sev] += int(count) if isinstance(
                count, (int, float)
            ) else 1
```

### Reproduction

```python
event = {
    "metadata": {
        "categories": ["hardcoded", "silent_fallback", "contamination"],
        "severity_distribution": {"HIGH": 5, "MEDIUM": 2},
    },
}

bd = aggregate([event])
print(bd.severity_by_category)
# {
#   "hardcoded": Counter({"HIGH": 5, "MEDIUM": 2}),
#   "silent_fallback": Counter({"HIGH": 5, "MEDIUM": 2}),
#   "contamination": Counter({"HIGH": 5, "MEDIUM": 2}),
# }
# Totals: 15 HIGH across categories, 6 MEDIUM — but actual = 5 HIGH + 2 MEDIUM
```

### Threat model / impact

- **Chapter 6 Row 15 severity tables over-state findings.** A Tier 2 probe event that flags multiple categories (common — a contaminated hardcoded tool typically flags both "hardcoded" AND "contamination") contributes its severity counts to each category. Summed-across-categories totals exceed the true number of findings.
- **"Per-category severity" label misleads.** The comment acknowledges this; the report labels don't.
- **Lower severity than #56-#59 because the raw data (per-category hit counts) is recoverable.** A reader who understands the overcounting can back it out. But without that context, Chapter 6's severity-by-category table is misinterpreted.
- **Direction of bias: inflationary.** Severity hits across categories always ≥ actual finding count, never less. Chapter 6 over-reports Tier 2 finding volume.

### Remediation

**A. Clarify the column name in output.**

Rename `severity_by_category` to `severity_hits_by_category_overcounted_on_multi_category_events` (or something less verbose) in terminal output + CSV headers. Align labels with the comment's acknowledged semantics.

```python
print_summary(bd):
    print("Severity hits by category (NOTE: multi-category events contribute to each category):")
    for cat, counter in bd.severity_by_category.items():
        ...
```

Cheap and explicit.

**B. Compute BOTH views and report both.**

```python
# Add a per-event-severity view that counts each event once (regardless of N categories):
severity_by_event: Counter = Counter()
for ev in events:
    metadata = ev.get("metadata", {}) or {}
    severity_dist = metadata.get("severity_distribution") or {}
    if isinstance(severity_dist, dict):
        for sev, count in severity_dist.items():
            severity_by_event[sev] += int(count)

# Report the per-event-severity table as the primary "how many findings at each severity",
# and per-category severity-hits as a secondary "distribution across categories".
```

Reader gets both numbers; can cross-check.

**C. Or (cleanest): attribute severity proportionally across categories.**

```python
n_cats = len(categories)
if n_cats > 0:
    for cat in categories:
        for sev, count in severity_dist.items():
            bd.severity_by_category[cat][sev] += int(count) / n_cats
```

Produces fractional counts, but the sum across categories equals the actual finding count. Makes "per-category severity" statistically coherent.

(A) + (B) is the minimum-risk path; (C) is cleaner statistically but changes column values in ways that break continuity with any earlier reports.

**D. Regression test.**

```python
def test_aggregate_severity_does_not_multiply_across_categories(...):
    """5 HIGH findings in a 3-category event must report 5 total HIGH events, not 15."""
    events = [{"metadata": {"categories": ["A", "B", "C"], "severity_distribution": {"HIGH": 5}}}]
    bd = aggregate(events)
    total_high_across_categories = sum(bd.severity_by_category[c]["HIGH"] for c in "ABC")
    # With remediation C: total_high_across_categories == 5
    # With remediation A+B: per_event_severity["HIGH"] == 5
    ...
```

### Recommendation

**Land (A) + (B) before any Chapter 6 Row 15 table is generated.** Documentation alignment + secondary per-event view. ~20 LOC + 1 regression test. ~30 min. (C) is a fairness choice that should be discussed with advisor separately.

### Legacy data

Not applicable — re-running `analyze_tier2_probes.py` post-fix against the same LangFuse traces produces both views.

---

## Issue #61 — `CompositionalExecutor._record_step_invocation` hardcodes `token_cost=0`; cost-weighted metrics are pre-empted (MEDIUM)

### Summary

`composition.py:469` writes `token_cost=0` into every `InvocationRecord` it emits. The user's original framing claimed this destroys M3 Gini and M4 cost. That framing is **overstated under current analyzers**: M3 Gini reads `COUNT(*)` from `invocation_events` (not `token_cost`), and M4 total-cost is backfilled by `scripts/backfill_token_costs.py` into `evaluation_runs.token_cost` (not `invocation_events.token_cost`). So neither current metric reads the zeroed column.

The bug is a **latent defect** — it forecloses future cost-weighted Gini ("concentration of capability usage weighted by actual token cost per invocation") which is a natural Chapter 7 extension but not a current headline claim.

### Where

- **Hardcoded zero:** `src/orchestrator/composition.py:469` — inside `_record_step_invocation`:
  ```python
  record = InvocationRecord(
      capability_id=ref.capability_name,
      task_id=task_id[:64],
      success=success,
      token_cost=0,            # ← hardcoded
      latency_ms=latency_ms,
      ...
  )
  ```
- **Cache-hit skip is correct:** caller at `composition.py:920-921` guards against cache-hit recording (per ext6 #29) — so reuse-count is accurate, only per-row cost is zero.
- **Actual M4 source:** `scripts/backfill_token_costs.py` → `evaluation_runs.token_cost`. `analyze_results.py:583` reads from `evaluation_runs`, never from `invocation_events.token_cost`.

### Remediation

**A. Backfill `invocation_events.token_cost` proportionally from `evaluation_runs.token_cost`.** Extend `scripts/backfill_token_costs.py` to apportion per-run totals across its invocation rows.

**B. Or delete the column.** Explicit-absence > misleading-presence if no near-term consumer needs per-invocation cost.

### Recommendation

**MEDIUM — downgraded from the user's original P0 framing.** No current analyzer corrupted; the bug is latent. Not eval-blocking.

---

## Issue #62 — Optimizer `_prev_optimizer_scores` nonlocal is wiped every task; KL divergence always computes against empty baseline (P0)

### Summary

`src/orchestrator/pipeline.py:156` defines a nonlocal `_prev_optimizer_scores: dict[str, float] = {}` inside `create_pipeline`, and line 158-208's `_optimizer_node` uses it to carry utility scores across pipeline invocations. Design intent: task N's KL divergence compared against task N-1's scores — a convergence signal.

Every caller recompiles the pipeline per task, creating a **new closure** with a **fresh empty `_prev_optimizer_scores`** on every invocation:

- `tests/evaluation/harness.py:592-598` — inside `_run_jit`, per task
- `scripts/run_longitudinal.py:181-187` — per task iteration
- `scripts/run_longitudinal_shift.py:288-293` — per task iteration

Every optimizer run sees `previous_utility_scores={}`. KL divergence is always against the empty dict. M6 convergence metric never measures actual convergence across tasks.

### Where

- **Closure state:** `src/orchestrator/pipeline.py:156`.
- **Nonlocal binding + carry-forward:** `src/orchestrator/pipeline.py:159` + `:208`.
- **Per-task recompile (three sites):** `harness.py:592-598`, `run_longitudinal.py:181-187`, `run_longitudinal_shift.py:288-293`.
- **Consumer:** `src/optimizer/loop.py:193` — `kl = _compute_kl_divergence(state.previous_utility_scores, scores)`.

### Reproduction

Direct inspection confirms the closure pattern and per-task recompile. For a 40-cycle longitudinal run, every cycle's KL is computed against `{}` — producing either `inf`, a constant, or noise depending on `_compute_kl_divergence`'s handling.

### Threat model / impact

- **M6 convergence fully invalidated for the optimizer's in-flight decisions.** The optimizer can't see previous-task scores, so its "has the registry stabilized?" question always answers the same way.
- **Dashboards (LangFuse `m6.kl_divergence` scores) are wrong per-cycle.** Post-hoc analyzer-derived M6 from `cycle_utility_scores` is CORRECT (analyzer reads persisted rows, not in-flight closure state) — so Chapter 6's M6 table is recoverable, but Chapter 6's discussion of *how the optimizer used KL to decide* is not.
- **Compounds with #56** (off-by-one cycle attribution). Both must land for M6-story integrity.

### Remediation

**A. Externalize `_prev_optimizer_scores` to persistent storage.**

```python
# src/registry/metrics.py — new helpers:
def load_utility_scores(self, *, run_id: str) -> dict[str, float]: ...
def persist_utility_scores(self, *, run_id: str, scores: dict[str, float]) -> None: ...

# New DB table:
CREATE TABLE IF NOT EXISTS optimizer_state (
    run_id VARCHAR PRIMARY KEY,
    scores VARCHAR NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

# src/orchestrator/pipeline.py — replace nonlocal with persisted state:
def _optimizer_node(state):
    prev = metrics_store.load_utility_scores(run_id=state.run_id)
    init_state = OptimizerState(previous_utility_scores=prev, ...)
    result = _opt_loop.invoke(init_state)
    metrics_store.persist_utility_scores(run_id=state.run_id, scores=result.get("utility_scores", {}))
```

**B. Alternative: compile the pipeline once per run, not per task.** Simpler but requires harness + longitudinal runner refactoring.

### Recommendation

**Land before any longitudinal analysis rerun.** ~60 LOC + 1 new DB table + tests. ~1h.

### Legacy data

`cycle_utility_scores` rows are correctly persisted pre-fix. Re-running `analyze_longitudinal.py` post-fix produces correct KL curves from existing data (the analyzer reads persisted rows, not in-flight closure). Only **in-flight LangFuse `m6.kl_divergence` scores** are wrong — can be recomputed.

---

## Issue #63 — `_register_artifact_directly` skips AST schema extraction for SKILL/AGENT variants; LLM-claimed schemas enter registry unchecked (P0)

### Summary

Optimizer merge/split paths call `_register_artifact_directly` to register new SKILL/AGENT variants. For MCP_TOOL variants (line 292-305), the function delegates to `ToolLoader.register_tool` — which runs AST-based input/output schema extraction. For SKILL/AGENT (line 307-337), it builds `CapabilityDescriptor` directly from `artifact.input_schema` / `artifact.output_schema` — **whichever fields the LLM's merge/split response populated**. No validation that those schemas match the actual code inside `artifact.skill_content` or `artifact.agent_config`.

If the merge/split LLM output claims a SKILL takes `{"data": "str"}` but the embedded Python fence expects `{"orders": "list[dict]", "customers": "dict"}`, the registry entry is unexecutable. The next retrieval invokes it with claimed-shape kwargs; the SKILL's code raises TypeError; the chain fails; attribution blames the SKILL itself, not the registration step.

### Where

- **MCP_TOOL (correct) path:** `src/optimizer/operations.py:292-305` — delegates to `ToolLoader.register_tool` (AST + schema extraction).
- **SKILL/AGENT (bypass) path:** `src/optimizer/operations.py:306-337`:
  ```python
  else:
      # SKILL / AGENT — build the descriptor directly. The caller
      # is responsible for populating artifact.input_schema and
      # artifact.output_schema.
      ...
      descriptor = CapabilityDescriptor(
          ...,
          input_schema=dict(artifact.input_schema or {}),
          output_schema=dict(artifact.output_schema or {}),
          ...,
      )
      registry.add(descriptor)
  ```
  Line 308-310 comment acknowledges caller-responsibility but doesn't enforce it.

### Threat model / impact

- **Silent registry corruption** via optimizer-generated variants with schema-code mismatch.
- **Attribution failure:** the bug surfaces as "SKILL crashed at runtime" downstream, blaming the SKILL rather than the optimizer's registration step.
- **Compounds with #64**: #64 lets LangFuse report shape-mismatched tasks as successful; a bad-schema SKILL registered via #63 and run via #64 path produces double-wrong attribution.

### Remediation

**A. AST-extract schemas at registration; reject or correct on mismatch.**

```python
# src/optimizer/operations.py — inside SKILL/AGENT branch:
if artifact.capability_type == CapabilityType.SKILL:
    extracted = extract_skill_schema(artifact.skill_content)
    if extracted.input_schema != artifact.input_schema:
        logger.warning("SKILL '%s': schema mismatch — using extracted", name)
        artifact.input_schema = extracted.input_schema
    # same for output_schema
elif artifact.capability_type == CapabilityType.AGENT:
    issues = validate_agent_config(artifact.agent_config, artifact.input_schema, artifact.output_schema)
    if issues:
        logger.warning("AGENT '%s': validation issues %s", name, issues)
```

Requires new helpers `extract_skill_schema` (AST-parse embedded fences) and `validate_agent_config`.

**B. Stricter: reject at mismatch rather than auto-correct.**

### Recommendation

**Land before Phase-5 longitudinal runs** — split/merge fire heavily in 40-cycle windows. ~3-4h.

---

## Issue #64 — `_extract_result_compositional` shape-mismatch doesn't flip `output_in_range`; same bug as #59, different function (P0)

### Summary

`tests/evaluation/harness.py:909-914` promotes `failure_reason = "output_shape_mismatch"` when the output-contract check fails, but doesn't flip `output_in_range=False`. The returned dict at line 924-926 still reports `output_in_range=True`. **Identical shape to #59's bug in `_run_jit`, applied to the compositional-chain path.**

Same downstream asymmetry as #59:
- DuckDB M1 (post-ext6-#32): `WHERE ... AND NOT COALESCE(output_shape_mismatch, FALSE)` → correctly excludes shape-mismatched rows.
- LangFuse `m1.success` (per `harness.py:715-718`): `gate_passed * output_in_range` → reads 1.0.

Two M1 sources, disagreement on shape-mismatched rows. Since most Phase-4 tasks are compositional (multi-subtask chains), this is arguably the **more important** site than #59.

### Where

- **Shape-mismatch without `output_in_range=False`:** `tests/evaluation/harness.py:909-914`:
  ```python
  if (failure_reason == "none" and shape_check is not None and not shape_check.satisfied):
      failure_reason = "output_shape_mismatch"
  # (output_in_range left as-is)
  ```
- **Return dict preserves stale flag:** `tests/evaluation/harness.py:924-926`.
- **Sibling in `_run_jit`:** see #59.

### Remediation

**Bundle with #59.** One shared `EvaluationRecord.is_success()` helper covers both sites. Plus an explicit `output_in_range = False` flip at the promotion site (line 914) as belt-and-suspenders.

```python
if failure_reason == "none" and shape_check is not None and not shape_check.satisfied:
    failure_reason = "output_shape_mismatch"
    output_in_range = False   # NEW
```

### Recommendation

**Bundle with #59.** ~30 min combined.

---

## Issue #65 — `_run_static` never records invocations; `invocation_events` empty for static condition; JIT-vs-static Gini comparison has no baseline (P0)

### Summary

`tests/evaluation/harness.py:529-573` is the static baseline path: registry lookup + call `_run_static_implementation(task_id, fixture_paths)` + run the validator. It returns a dict but **never calls `metrics_store.record_invocation()`** for the capability it just used.

The per-task flush at line 420 calls `flush_task_cycle_state` unconditionally. The flush reads `metrics_store.fetch_invocations_for_task(task_id, since=task_events_cutoff)`. Since `_run_static` never wrote any invocation records, the fetch returns empty; zero rows persist to `invocation_events`.

Result: the `static` condition has zero rows in `invocation_events`. Any Chapter 6 "does JIT produce more concentrated reuse than hand-written static?" claim has no static data to compare against.

### Where

- **Static path skipping record_invocation:** `tests/evaluation/harness.py:529-573` (`_run_static`). No `metrics_store.record_invocation(...)` call anywhere.
- **Static capability name:** per `task.static_capability_name`.
- **Consumer with empty data:** `scripts/analyze_results.py::print_gini_summary` groups `invocation_events` by condition — static group is empty.

### Reproduction

```sql
SELECT condition, COUNT(*) FROM invocation_events WHERE NOT COALESCE(is_pilot, FALSE) GROUP BY condition;
-- Actual: full ~2000, no_verification ~2000, no_optimization ~2000, static: 0
```

### Remediation

**A. Record an invocation per static run.**

```python
# tests/evaluation/harness.py:557 — after _run_static_implementation:
try:
    from src.schemas.metrics import InvocationRecord
    metrics_store.record_invocation(InvocationRecord(
        capability_id=task.static_capability_name,
        task_id=task.task_id,
        success=bool(in_range),
        token_cost=0,
        latency_ms=0,
    ))
except Exception:
    logger.debug("Failed to record static invocation for %s", task.task_id, exc_info=True)
```

**B. Thread `metrics_store` into `_run_static`.** Update signature at line 529 + caller at line 479.

### Recommendation

**Land before Chapter 6 M3 Gini comparison table is generated.** ~15 LOC + 1 test. ~20 min.

### Legacy data

Static-condition data pre-fix has zero `invocation_events` rows. Re-run static condition only (5 runs × 20 tasks, no LLM calls — ~minutes).

---

## Issue #66 — `compute_utility` ignores `CapabilityDescriptor.definition_token_cost`; Pillar 3 U(c) cannot optimize for prompt bloat (P0)

### Summary

`src/optimizer/utility.py::compute_utility` implements:

```python
return alpha * m.reuse_rate * m.success_rate - beta * m.token_cost - gamma * m.maintenance_burden
```

Where `m.token_cost` is the average **execution** token cost from `metrics_store.compute_granularity_metrics`. The formula **does not read** `CapabilityDescriptor.definition_token_cost` — the static tokenization of the capability's definition string that Step 18.6 added specifically as the Pillar 3 "Context Economy" input.

Proposal §Pillar 3 names three tensions, one of which is Context Economy (prompt-bloat prevention: as more capabilities enter the registry, each LLM call pays a proportional prompt-token cost for definitions, regardless of whether those capabilities are used). The utility score is designed to penalize capabilities that cost a lot of prompt tokens relative to their usefulness. Currently U(c) penalizes only per-invocation execution cost — ignoring the definition cost that's paid on every pipeline invocation whether the cap is used or not.

Consequence: the optimizer cannot evolve toward a context-economical distribution. The Pillar 3 RQ "does the optimizer reduce prompt-token cost over time?" is formally unanswerable because the utility function never reads the input needed to drive that optimization.

### Where

- **Formula:** `src/optimizer/utility.py:33`:
  ```python
  return alpha * m.reuse_rate * m.success_rate - beta * m.token_cost - gamma * m.maintenance_burden
  ```
- **Missing term:** `m.definition_token_cost` (or `registry.get(cap_id).definition_token_cost`) is not referenced anywhere in `utility.py`.
- **Row 12 backfill** (`scripts/backfill_definition_token_costs.py`) populates `CapabilityDescriptor.definition_token_cost` — the field EXISTS, it's just unread by the utility function.
- **Proposal §Pillar 3 "Context Economy"** — names this as a core tension the optimizer should evolve against.

### Reproduction

```python
from src.optimizer.utility import compute_utility
# Two capabilities with identical reuse/success/maintenance but very different definition sizes:
#   cap_small: 50-token definition
#   cap_huge:  5000-token definition
# compute_utility returns the SAME score for both — U(c) is blind to definition size.
```

Direct inspection of `compute_utility` confirms the formula doesn't read definition_token_cost.

### Threat model / impact

- **Pillar 3 "Context Economy" claim unsupported by code.** The thesis's claim that the optimizer reduces prompt bloat is not backed by the utility function's inputs. Whatever the observed registry evolution does, it's not optimizing for definition-token-cost.
- **Compounds with #61.** #61 leaves `invocation_events.token_cost=0`; #66 leaves the definition-cost axis unread entirely. Together, neither the per-invocation cost nor the per-definition cost feeds the optimizer's utility function as the proposal specifies.
- **Direction of bias: optimizer converges toward different distribution than claimed.** Whatever distribution it DOES converge toward is a function of reuse × success minus execution cost + maintenance — not the proposal's four-input formula.

### Remediation

**A. Add `delta * m.definition_token_cost` to the formula.**

```python
# src/optimizer/utility.py:12 — update signature:
def compute_utility(
    capability_id: str,
    metrics_store: MetricsStore,
    registry: SkillRegistry,          # NEW — needed to read definition_token_cost
    *,
    alpha: float = 1.0,
    beta: float = 0.1,
    gamma: float = 0.1,
    delta: float = 0.01,              # NEW weight — smaller than beta because definition_cost is size, not per-invocation
) -> float:
    m = metrics_store.compute_granularity_metrics(capability_id)
    cap = registry.get(capability_id)
    def_cost = cap.definition_token_cost if cap else 0
    return (
        alpha * m.reuse_rate * m.success_rate
        - beta * m.token_cost
        - gamma * m.maintenance_burden
        - delta * def_cost
    )
```

**B. Tune `delta` via simulation.** The existing `src/optimizer/simulation.py` harness can sweep delta against known synthetic distributions to find a value that produces the Pillar 3-claimed behavior.

**C. Regression test.**

```python
def test_utility_penalizes_large_definition_cost():
    """Two caps with identical m but different definition sizes: larger def → lower utility."""
    cap_small = seed_cap(def_cost=50, reuse_rate=0.5, success_rate=0.9)
    cap_huge = seed_cap(def_cost=5000, reuse_rate=0.5, success_rate=0.9)
    assert compute_utility(cap_small.name, ...) > compute_utility(cap_huge.name, ...)
```

### Recommendation

**Land before any longitudinal Pillar 3 analysis.** Formula change + signature threading + 1 test + simulation-based delta tuning. ~1.5-2h.

### Legacy data

Existing longitudinal DBs' `cycle_utility_scores` rows were computed under the pre-fix formula. Re-running `compute_all_utilities` post-fix produces different numbers. Decide: re-run analysis OR disclose in Chapter 4.5 that the reported U(c) was 3-input (not 4-input) and acknowledge Context Economy is Chapter 7 future work.

---

## Issue #67 — LangFuse `m5.latency_ms` score reads `result.get("chain_result_attempt_latency_ms", 0)`; the key never exists in the result dict; every M5 score is 0 (P0)

### Summary

`tests/evaluation/harness.py:737` computes the LangFuse M5 score as:

```python
value=float(
    result.get("chain_result_attempt_latency_ms", 0)
    or 0
),
```

The key `chain_result_attempt_latency_ms` does NOT appear in any result-dict construction across the harness. The actual latency measure is computed at line 491 as `elapsed_ms = int((time.monotonic() - t_start) * 1000)` and assigned to `EvaluationRecord.attempt_latency_ms` (DB column) at line 504 — but it's never put into the `result` dict that `_run_jit` returns.

Result: `result.get("chain_result_attempt_latency_ms", 0)` always returns 0. LangFuse `m5.latency_ms` score is 0 for every successful run. Any Chapter 6 dashboard or cross-check against LangFuse reports zero latency.

### Where

- **Stale/non-existent key read:** `tests/evaluation/harness.py:737`.
- **Actual latency computation:** `tests/evaluation/harness.py:491` — `elapsed_ms = int((time.monotonic() - t_start) * 1000)`.
- **Latency assignment to DB (not to result dict):** `tests/evaluation/harness.py:504` — `attempt_latency_ms=elapsed_ms`.
- **No `chain_result_attempt_latency_ms` anywhere else in the tree** — verified by grep across src/ + tests/.

### Reproduction

Trivial — `result.get("chain_result_attempt_latency_ms", 0)` with no such key in the dict returns 0. Every LangFuse M5 score row would be 0. Direct inspection confirms.

### Threat model / impact

- **LangFuse M5 dashboard uniformly reports 0 latency.** Any real-time monitoring dashboard that reads M5 from LangFuse sees impossibly-fast system.
- **Cross-check between LangFuse M5 and DuckDB M5 disagrees.** DuckDB's `evaluation_runs.attempt_latency_ms` carries the real elapsed_ms; LangFuse carries 0. Reviewer cross-referencing sees the discrepancy with no log explanation.
- **Direction of bias: LangFuse under-reports latency.** Zero is always less than actual.
- **Compounds with #59/#64 (M1 cross-store divergence).** #59/#64 give LangFuse a wrong SUCCESS value; #67 gives LangFuse a wrong LATENCY value. Three metrics across both stores are now asymmetric.

### Remediation

**A. Fix the key.** Use the correct field name — either `attempt_latency_ms` (matches the EvaluationRecord field) or add the elapsed_ms value directly.

```python
# tests/evaluation/harness.py:737 — before:
value=float(result.get("chain_result_attempt_latency_ms", 0) or 0),
# after (Option A1): thread elapsed_ms from the outer scope:
value=float(elapsed_ms or 0),
# after (Option A2): put the correct key in the result dict upstream:
#   result["attempt_latency_ms"] = elapsed_ms
# then:
value=float(result.get("attempt_latency_ms", 0) or 0),
```

A1 is simpler (elapsed_ms is already in scope at this point — declared at line 491). A2 is cleaner architecturally because it makes the result dict the single source of truth.

**B. Regression test for cross-store parity.**

```python
def test_langfuse_m5_score_matches_duckdb_attempt_latency():
    """LangFuse m5.latency_ms score must equal evaluation_runs.attempt_latency_ms."""
    # Run a minimal task; capture both LangFuse score and DuckDB row.
    # Assert they're equal.
    ...
```

Bundle with the M1 parity test from #59/#64 into a general "cross-store metric parity" test suite.

### Recommendation

**Land immediately — blocking any LangFuse dashboard use.** 2-LOC fix + 1 regression test. ~15 min.

### Legacy data

Pre-fix LangFuse M5 scores are all 0. Options: (a) recompute and overwrite, (b) deprecate LangFuse M5 as a secondary source and reference DuckDB only. (b) is cleaner if the dashboards aren't load-bearing.

---

## Issue #68 — `_run_static` bypasses `output_contract.check`; static condition records shape-mismatched outputs as successes (P0)

### Summary

`tests/evaluation/harness.py::_run_static` (lines 529-573) validates the static baseline's output via `task.validator(output)` only. It does NOT call `task.output_contract.check(output, output_files)` — the shape-mismatch check that JIT paths apply (per ext6 #32's fix at `_extract_result_compositional:897` and `_run_jit:1208`).

Consequence: a static capability that produces valid metadata (validator passes) but is missing its declared `required_files` (e.g., T07's `output.csv`) records as:
- JIT path (`full` condition): `output_shape_mismatch=True`, row excluded from Chapter 6 success rate.
- Static path (`static` condition): `output_shape_mismatch=False`, row counts as success.

For Pillar 1 JIT-vs-static comparison, this is asymmetric: the static condition gets a strictly more lenient validation. Chapter 6's "JIT differs from static by X%" claim is inflated by the subset of tasks where static silently ships shape-mismatched outputs.

### Where

- **Static path lacks contract check:** `tests/evaluation/harness.py:529-573` (`_run_static`). Reads `task.validator(output)` at line 558, returns result dict without `output_shape_mismatch`.
- **JIT paths apply contract check:** `_extract_result_compositional` at line 897 (shape_check) + `_run_jit` at line 1208.
- **Static result dict omits shape fields:** line 559-566 return dict has no `output_shape_mismatch` / `output_shape_missing_keys` / `output_shape_missing_files`. Downstream EvaluationRecord defaults these to `False` / `[]`.

### Reproduction

T07 with a static hand-written capability that returns valid metadata but doesn't emit `output.csv`:

```python
# T07's validator passes on dict-only metadata (_validate_t07 at tasks.py:355-360).
# Static implementation returns {"row_count": N, "null_count_on_key": 0} without writing CSV.
# _run_static returns {"gate_passed": True, "output_in_range": True, "failure_reason": "none"}
# No shape_check. EvaluationRecord.output_shape_mismatch = False (default).
# Chapter 6 success filter: gate_passed AND output_in_range AND NOT output_shape_mismatch → True.
# But this row's required_files=["output.csv"] wasn't satisfied — would have failed JIT path.
```

### Threat model / impact

- **Pillar 1 comparison biased pro-static.** Static condition gets success credit for file-output tasks where it didn't emit the file; JIT condition doesn't. Chapter 6 "does JIT outperform static on composition quality?" has a systematic thumb on the scale.
- **Compounds with ext8 #65.** #65 makes static condition invisible in Gini (no invocations recorded); #68 makes static condition artificially successful in M1 (no shape check). Both distort JIT-vs-static comparisons but in opposite directions.
- **Direction of bias: anti-JIT in M1 comparisons.** Static looks better; JIT looks worse.

### Remediation

**A. Thread `task.output_contract.check` into `_run_static`.**

```python
# tests/evaluation/harness.py:557-566 — after running validator:
output = _run_static_implementation(task.task_id, task.fixture_paths)
in_range = task.validator(output)

# NEW: apply the same contract check the JIT path uses.
shape_check = (
    task.output_contract.check(output, {})  # static path has no output_files
    if task.output_contract else None
)

failure_reason = "none" if in_range else "output_rejected"
if in_range and shape_check is not None and not shape_check.satisfied:
    failure_reason = "output_shape_mismatch"
    in_range = False  # per #59/#64 pattern

return {
    "gate_passed": True,
    "output_in_range": in_range,
    "failure_reason": failure_reason,
    "output_shape_mismatch": shape_check is not None and not shape_check.satisfied,
    "output_shape_missing_keys": shape_check.missing_keys if shape_check else [],
    "output_shape_missing_files": shape_check.missing_files if shape_check else [],
    ...
}
```

**B. Or: thread `output_files` if static implementations actually write files.**

`_run_static_implementation` at line 1530 runs a pre-written implementation; some static implementations may write output files to a workspace. If so, pass the workspace + collect files into `output_files` before the contract check. Symmetric with the JIT path's `chain_result.output_files`.

**C. Regression test.**

```python
def test_run_static_records_shape_mismatch_for_missing_required_files():
    """Static task with required_files=[output.csv] but no file output → output_shape_mismatch=True."""
    task = t07  # has output_contract.required_files=["output.csv"]
    result = harness._run_static(task, registry)
    assert result.get("output_shape_mismatch") is True
    assert "output.csv" in result.get("output_shape_missing_files", [])
```

### Recommendation

**Land before Pillar 1 JIT-vs-static comparison is finalized.** ~20 LOC + 1 test. ~30 min.

### Legacy data

Static-condition rows pre-fix have `output_shape_mismatch=False` regardless of actual contract satisfaction. Decide: (a) recompute shape_check post-hoc from DB (requires re-running static implementations to get output files), (b) accept that pre-fix static numbers are lenient and disclose. (a) is cleaner.

---

## Issue #69 — `DomainTaxonomy.get_for_query` has no retry; single LLM failure falls through to domain-blind search (P0)

### Summary

`src/orchestrator/taxonomy.py::DomainTaxonomy.get_for_query` wraps a single `structured_llm.invoke(prompt)` call in a `try/except` that returns `None` on any failure (lines 494-502). The calling code (`src/orchestrator/gap_detection.py::gap_detection_node`, around line 90) uses the returned domain to filter the registry search; on `None`, the search falls back to domain-blind (unfiltered) matching.

Every other LLM-calling stage in the strict-gate pattern (decomposition per §12 finding #15, classification per §12 finding #16, verification per §12 finding #14) has 3x retry + sticky-flag + loud fail. Taxonomy classification skips this pattern and silently falls back.

Consequence: under high-concurrency eval load (the 400-record Phase 4 run hits OpenAI with 20 concurrent requests per condition), a single transient LLM failure (rate limit, 500, network flap) demotes the affected task's retrieval to unfiltered search — introducing random noise into Pillar 1 retrieval metrics.

### Where

- **No-retry classifier:** `src/orchestrator/taxonomy.py:494-502`:
  ```python
  try:
      result: QueryDomainChoice = structured_llm.invoke(prompt)
  except Exception as exc:
      logger.warning("DomainTaxonomy.get_for_query LLM failure ... — bypassing filter")
      return None
  ```
- **Consumer with domain-blind fallback:** `src/orchestrator/gap_detection.py:90-107`:
  ```python
  selected_domain = taxonomy.get_for_query(...)
  if selected_domain is not None:
      # filtered search
  ...
  ```
- **Strict-gate pattern (reference):** `src/orchestrator/decomposition.py` / `classifier.py` / `verification_node.py` all implement 3x retry + sticky_succeeded flag.

### Reproduction

```python
# Force an OpenAI 429 on the taxonomy classifier's LLM call:
with mock.patch(
    "src.orchestrator.taxonomy.structured_llm.invoke",
    side_effect=openai.RateLimitError("rate limited"),
):
    domain = taxonomy.get_for_query("parse sales CSV", llm)
assert domain is None   # fall-through; no retry
# Downstream gap_detection runs unfiltered search → different matches than a retried run.
```

### Threat model / impact

- **Pillar 1 retrieval metrics carry random noise.** "Did we find the right capability?" depends on whether the taxonomy classifier happened to succeed. For Fisher's exact + Holm multi-comparison tests (per row 17), this introduces uncontrolled variance that weakens any statistical-significance claim.
- **Direction of bias: unpredictable (noise, not shift).** Under high concurrency, some fraction of gap_detection calls fall back to unfiltered; the exact fraction depends on provider's transient state.
- **Strict-gate-coverage gap.** §12 findings #14-#16 committed to "no silent fallbacks anywhere in the pipeline for proposal-critical LLM stages." Taxonomy classifier is arguably proposal-critical (it drives retrieval) but was excluded from the pattern.
- **Compounds with #13-family methodology concerns.** User's earlier #13 flagged that optimizer thresholds may not fire in 20-task windows. #69 introduces additional retrieval-layer variance. Between them, Pillar 1 retrieval statistics have multiple sources of uncontrolled noise.

### Remediation

**A. Apply the strict-gate retry pattern.**

```python
# src/orchestrator/taxonomy.py:488 — replace single-try with 3x retry:
MAX_TAXONOMY_ATTEMPTS = 3

def get_for_query(self, query: str, llm: Any, ...) -> str | None:
    ...
    for attempt in range(1, MAX_TAXONOMY_ATTEMPTS + 1):
        try:
            result = structured_llm.invoke(prompt)
            break
        except Exception as exc:
            if attempt >= MAX_TAXONOMY_ATTEMPTS:
                logger.error(
                    "DomainTaxonomy.get_for_query exhausted %d attempts (query=%r): %s",
                    MAX_TAXONOMY_ATTEMPTS, query[:80], exc,
                )
                # Loud-fail: raise or emit LangFuse event instead of silent None.
                emit_langfuse_event(None, "taxonomy.classification_failed",
                                    "ERROR", {"query": query, "error": str(exc)})
                return None
            logger.warning(
                "DomainTaxonomy.get_for_query attempt %d/%d failed: %s — retrying",
                attempt, MAX_TAXONOMY_ATTEMPTS, exc,
            )
    # ... existing validation + return
```

Match pattern used by `decomposition.py`, `classifier.py`, `verification_node.py`.

**B. Optional: add a sticky-flag on the state.**

If the thesis wants to distinguish "taxonomy filtered correctly" from "taxonomy fell back after 3 tries," add `taxonomy_succeeded: bool` on `TaskState` and surface via a CLI exit code. Likely overkill for this layer but consistent with the pattern used elsewhere.

**C. Regression test.**

```python
def test_taxonomy_classifier_retries_on_transient_failure():
    side_effect = [ApiError(), ApiError(), MagicMock(domain="analytics")]
    with mock.patch.object(structured_llm, "invoke", side_effect=side_effect):
        domain = taxonomy.get_for_query("parse sales CSV", llm)
    assert domain == "analytics"
    # Pre-fix: None
```

### Recommendation

**Land before Phase-4 400-record eval.** The eval is high-concurrency; transient LLM failures are expected. ~30 LOC + 1 test. ~30 min.

### Legacy data

Pre-fix retrieval metrics carry uncontrolled noise from the no-retry path. Re-run post-fix for Chapter 6 finalization.

---

## Issue #70 — Harness injects fixture content + pads pip-install timeouts; M1/M5 systematically better than production reality (MEDIUM, methodology)

### Summary

The evaluation harness does two things that improve the system's apparent performance relative to production deployment:

1. **Fixture injection:** `tests/evaluation/execution.py::_read_fixture_contents` + `_format_fixture_message` read test-fixture files and inject their contents into the LLM prompt / agent workspace as pre-read data. A real user invoking `mint run` would have to direct the capability to open the file itself. Harness saves that step.
2. **Pip-install timeout padding:** Docker sandboxed pip installs in `_sandbox_execute_with_install` have a 60s timeout (line 414 from earlier inspections). Under production-latency conditions (slow network, package backfill from a slow PyPI mirror), this may be too short; under harness conditions (warm host network, cached packages), it's generous. The asymmetry means harness runs succeed where production would timeout.

Net effect: **M1 (success rate) and M5 (latency) measured in the harness are systematically better than what a production user would experience.** Not a code bug — a methodological ecological-validity concern.

### Where

- **Fixture injection:** `tests/evaluation/execution.py:266-300` (`_read_fixture_contents`, `_format_fixture_message`).
- **Pip-install timeout:** `src/verification/sandbox_tool.py::_sandbox_execute_with_install` — 60s timeout on install phase.
- **Workspace file preparation:** `src/orchestrator/composition.py::build_workspace` — fixtures copied into workspace at task start.

### Threat model / impact

- **Chapter 6 M1/M5 are upper bounds, not expected values.** Reviewer asking "what would a real user see?" gets optimistic numbers.
- **Direction of bias: optimistic across the board.** Every condition benefits, so relative comparisons are less affected than absolute numbers.
- **Chapter 4.5 disclosure gap.** If the methodology section doesn't call out these harness affordances, the defense is weak.

### Remediation

**Documentation-only (primary):** Add to Chapter 4.5 a subsection "Harness affordances not representative of production":
- Fixture content is injected rather than opened from disk by the capability. Production latency adds file-read time per task.
- Pip install timeouts are set for controlled harness conditions; production may need wider margins.
- LangFuse write-path is instrumented by the harness and may batch differently in production.

**Code-level (secondary, optional):** Add a `--production-mode` harness flag that:
- Disables fixture content injection (capabilities must open files themselves).
- Tightens pip timeouts to match a production SLA.
- Runs a 10-task "production" smoke alongside the standard harness to measure the gap.

Chapter 6 reports both numbers: "harness M1 = 72%, production-mode M1 = 64% (8pt gap attributable to harness affordances)."

### Recommendation

**MEDIUM — methodology concern, not correctness.** (A) documentation update is ~30 min. (B) production-mode flag is ~3-4h if desired. (A) alone closes the reviewer-defensibility gap.

### Legacy data

Not applicable — data is correct under the stated methodology; the fix is to state the methodology explicitly.

---

## Issue #71 — Fresh `MetricsStore()` per run wipes `maintenance_burden`; longitudinal optimizer has no cross-run memory (P0)

### Summary

Both `tests/evaluation/harness.py::_execute_run` (line 364) and `scripts/run_longitudinal.py::main` (line 152) instantiate `MetricsStore()` with no arguments — creating a fresh in-memory DuckDB for every run. The `MetricsStore` accumulates per-task data within a run (intentional, per the comment at line 360-361) — but between runs, all state is lost.

`maintenance_burden` (used by `compute_utility`'s γ term) is tracked in `MetricsStore` — the count of verification failures / regeneration events per capability. A capability that failed verification in run N-1 has no record of that failure in run N's MetricsStore. Over a multi-run longitudinal experiment, the optimizer "forgets" stability history between runs.

The user's framing calls this out for longitudinal specifically: "the optimizer will 'forget' that a capability was unstable in the previous run, breaking the 'learning over time' claim."

### Where

- **Harness fresh-store:** `tests/evaluation/harness.py:364`:
  ```python
  metrics_store = MetricsStore()
  ```
  Comment at line 360-361 explicitly scopes this to within-run sharing: "shared across tasks so the optimizer accumulates data from all tasks in the same run."
- **Longitudinal fresh-store:** `scripts/run_longitudinal.py:152` — same pattern.
- **`maintenance_burden` consumer:** `src/optimizer/utility.py::compute_utility` — reads `m.maintenance_burden` via `compute_granularity_metrics`.
- **Persistence layer that DOES survive runs:** `src/registry/evaluation_store.py::invocation_events`, `cycle_utility_scores`, `cycle_events` — these tables ARE persisted, just not loaded back into MetricsStore on fresh startup.

### Reproduction

```python
# Run 1: T01 runs, verification fails twice, maintenance_burden["cap_x"] = 2.
ms1 = MetricsStore()
# ... populate ms1 through run 1 ...

# Run 2: fresh MetricsStore.
ms2 = MetricsStore()
# compute_utility("cap_x", ms2) reads maintenance_burden["cap_x"] = 0 (fresh).
# The optimizer has no memory of cap_x's instability.
```

### Threat model / impact

- **Pillar 3 "learning over time" claim unsupported.** Longitudinal experiments are designed to show the optimizer evolves based on accumulating evidence. Resetting MetricsStore at every run boundary resets one of the four U(c) inputs, breaking the accumulation narrative.
- **Step 18.8 shift adaptation metric (Pillar 3 RQ #3) undermined.** The three-tier verdict (graceful/sluggish/collapse) depends on the optimizer adapting to changed task distributions. Adapting requires remembering. Fresh-store-per-run prevents remembering.
- **Direction of bias: anti-"learning" narrative.** Whatever behavior the optimizer shows, it's not learning across runs — it's making the same first-run decisions on each fresh MetricsStore.
- **Compounds with ext8 #62.** #62 wipes optimizer closure state per task (within a run); #71 wipes MetricsStore between runs. Together, the optimizer has no cross-task memory within a run AND no cross-run memory across runs. The "learning" signal is structurally absent.

### Remediation

**A. Persist MetricsStore to disk; reload on startup.**

```python
# src/registry/metrics.py — accept an optional path:
class MetricsStore:
    def __init__(self, path: str | Path | None = None):
        if path is None:
            self._conn = duckdb.connect(":memory:")
        else:
            self._conn = duckdb.connect(str(path))
        # ... run _INIT_SQL; it's idempotent via CREATE TABLE IF NOT EXISTS
```

Callers (harness, longitudinal runner) pass a persistent path instead of the default `:memory:`:

```python
# tests/evaluation/harness.py:364 — before:
metrics_store = MetricsStore()
# after:
metrics_store = MetricsStore(path=cfg.metrics_store_path)  # e.g., data/eval/metrics_store.duckdb
```

Longitudinal run uses the same path across cycles; harness runs may use per-run paths or a shared path.

**B. Alternative: load MetricsStore from `evaluation_store.invocation_events` at startup.**

If the persistent data is already in `evaluation_store.duckdb` via `flush_task_cycle_state`, MetricsStore could rebuild its in-memory state by querying that store at init:

```python
# tests/evaluation/harness.py:364 — after:
metrics_store = MetricsStore()
metrics_store.hydrate_from_evaluation_store(eval_store, condition=condition)
```

Requires new `hydrate_from_evaluation_store` method. Slower startup but uses the existing persistence layer.

**C. Regression test.**

```python
def test_maintenance_burden_persists_across_run_boundaries(tmp_path):
    """Run 2's optimizer sees run 1's maintenance_burden, not 0."""
    store_path = tmp_path / "metrics.duckdb"

    ms1 = MetricsStore(path=store_path)
    # ... populate ms1 with verification failures ...

    # Close ms1, open ms2 at same path:
    del ms1
    ms2 = MetricsStore(path=store_path)
    m = ms2.compute_granularity_metrics("cap_x")
    assert m.maintenance_burden > 0  # pre-fix: 0
```

### Recommendation

**Land before any longitudinal re-run.** ~40 LOC + 2 regression tests. ~1h.

### Legacy data

Pre-fix longitudinal runs had MetricsStore reset between runs → any cross-run U(c) evolution is noise. Re-run post-fix produces correctly-accumulating utility scores.

---

## Issue #72 — B2.3 output-variation probe uses strict equality; tools varying a single timestamp/UUID bypass detection (P0)

### Summary

`src/verification/behavioral.py:373` — inside `_test_output_variation`:

```python
elif (
    err_a is None
    and err_b is None
    and parsed_a.get("result") == parsed_b.get("result")
    and parsed_a.get("result") not in _TRIVIAL_RESULTS
):
    findings.append(... "Tool returned identical output for very different inputs" ...)
```

Strict `==` equality. A contaminated tool that returns `{"data": [...hardcoded...], "timestamp": "2026-04-20T14:32:17"}` with a fresh timestamp per invocation produces formally-different results even though 99% of the payload is canned. The probe doesn't fire; M2 contamination under-reports.

Same failure class applies to:
- Embedded UUIDs / request-ids
- `datetime.now()` in output
- `random.random()` noise
- Counter fields incremented per call

### Where

- **Strict-equality check:** `src/verification/behavioral.py:373` — `parsed_a.get("result") == parsed_b.get("result")`.
- **_TRIVIAL_RESULTS filter:** line 272 — trivially-constant outputs (`"None"`, `"[]"`, etc.) are excluded, but complex near-identical outputs aren't.

### Reproduction

```python
# Contaminated tool:
async def fetch_sales():
    return {
        "revenue_by_region": [{"region": "NA", "revenue": 42000}, ...],  # hardcoded
        "generated_at": datetime.now().isoformat(),  # varies per call
    }

# Probe: inputs_a = boundary, inputs_b = large. Both call fetch_sales (parameterless).
# parsed_a["result"] = '{"revenue_by_region": [...], "generated_at": "2026-04-20T14:32:17.111"}'
# parsed_b["result"] = '{"revenue_by_region": [...], "generated_at": "2026-04-20T14:32:18.234"}'
# parsed_a == parsed_b is FALSE (timestamps differ).
# Probe does not fire. Contamination hidden.
```

### Threat model / impact

- **M2 contamination rate systematically under-reports.** Any contaminated tool whose LLM generator embeds a timestamp/UUID/counter (common — LLMs often include `"generated_at"`-style metadata by default) escapes detection.
- **Direction of bias: anti-detection.** Makes the system look cleaner than it is.
- **Compounds with ext6 #32 + ext8 #65**: M2 static-baseline data gap + M1 shape-mismatch-not-success-flip + this probe-bypass. Three layers of under-counting on contamination.

### Remediation

**A. Normalize before comparison — strip timestamps and UUIDs.**

```python
import re
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_COUNTER_RE = re.compile(r"(count|counter|id|nonce)[\"']?\s*:\s*\d+")

def _normalize_for_variation(result_str: str) -> str:
    s = _TIMESTAMP_RE.sub("<TS>", result_str)
    s = _UUID_RE.sub("<UUID>", s)
    s = _COUNTER_RE.sub(r"\1: <N>", s)
    return s

# In _test_output_variation:
if _normalize_for_variation(parsed_a.get("result")) == _normalize_for_variation(parsed_b.get("result")):
    # Flag as "output-variation after normalization" — still looks canned.
```

**B. Structural-diff comparison (cleaner).**

Parse both outputs as JSON and compute a depth-weighted structural similarity. If >90% of fields match, flag as suspicious. Less aggressive than strict equality but catches the timestamp-only variation case. Requires `json.loads` + recursive diff.

**C. Fuzzy-hash comparison.**

Take the SHA-256 of the lowercased, digits-stripped, whitespace-normalized output. Compare. Matches timestamp-varying canned outputs.

**D. Regression test.**

```python
def test_variation_probe_flags_timestamp_only_variation():
    """Tool whose only variation between inputs is an embedded timestamp should fail the probe."""
    source = '''
    async def contaminated():
        return {"data": "canned", "ts": datetime.now().isoformat()}
    '''
    findings = _test_output_variation(source, "contaminated", {}, ...)
    assert any(f.category == "output_variation" for f in findings)
```

### Recommendation

**Land before Phase-5 eval.** ~30 LOC + 1 test. ~45 min. Decide between (A) cheap regex normalization and (B) structural diff based on false-positive tolerance; (A) is the faster fix.

---

## Issue #73 — `_check_latency_variance` gate `mean<0.001 AND cv<0.10` is fragile; environmental jitter masks the mock-smell signal (MEDIUM)

### Summary

`src/verification/behavioral.py:637` — two-condition gate:

```python
if mean < 0.001 and cv < 0.10:
    findings.append(... "Tool execution is suspiciously fast and uniform" ...)
```

The `mean` and `cv` are computed from `elapsed` values INSIDE the sandbox (correctly excluding Docker startup + pip install). For a trivial mock returning a canned dict, inside-sandbox elapsed is typically 10-100 microseconds (well under 1ms).

But across a small sample (typically 3-5 inputs), Python GC jitter + dict-hash randomization + allocation variance produce cv values that commonly exceed 0.10 for sub-millisecond means. Real measurements on canned mocks show cv in the 0.2-0.5 range — the gate's `cv < 0.10` threshold rarely fires.

Net effect: the mock-smell detector intended to catch "very fast AND very uniform" outputs rarely reports CRITICAL findings because the `cv < 0.10` tail is too tight for statistical power on small samples.

### Where

- **Gate logic:** `src/verification/behavioral.py:637`.
- **Elapsed sample source:** lines 623-627 — `elapsed_samples` appended per sandbox invocation from the JSON payload's `elapsed` field.

### Reproduction

```python
# Simulated: 5 samples from a canned-dict-returning mock
import statistics
elapsed_samples = [0.00005, 0.00012, 0.00008, 0.00018, 0.00007]  # typical
mean = statistics.mean(elapsed_samples)     # ~0.0001
stdev = statistics.stdev(elapsed_samples)   # ~0.00005
cv = stdev / mean                            # ~0.5

# mean < 0.001 → True
# cv < 0.10 → FALSE (0.5 > 0.10)
# Gate doesn't fire; contamination not flagged.
```

### Threat model / impact

- **Pillar 2 mock-smell signal under-fires.** Canned-output tools pass the latency check routinely.
- **Direction of bias: anti-detection (pro-artifact).** Same direction as #72 for contamination.
- **Compounds with #72:** both probes under-detect contamination; Pillar 2's contamination-catching claim rests on multiple layers of defense, all of which are weaker than the spec describes.

### Remediation

**A. Relax cv threshold; add absolute-stdev gate.**

For sub-millisecond means, cv from natural jitter dominates. Switch to an absolute-stdev gate instead:

```python
# Before:
if mean < 0.001 and cv < 0.10:
# After: "very fast AND stdev-within-a-few-percent-of-mean"
if mean < 0.001 and stdev < 0.1 * mean:  # Actually same as cv < 0.1, just reframed
    ...

# Better: sample more (10+ instead of 5) to reduce jitter-driven cv variance:
# In _check_latency_variance, expand input-generation to produce more distinct inputs.
```

**B. Tighter gate on absolute bounds for both.**

```python
if mean < 0.001 and stdev < 0.0002:  # 200 microseconds absolute stdev
    ...
```

Eliminates the per-sample-jitter inflation; only genuinely-uniform (sub-microsecond-jitter) mocks fire.

**C. Run more samples.**

Increase the number of input variations from 5 to 15+. With more samples, cv converges toward the true underlying variance (closer to 0 for mocks, closer to higher values for real tools). Statistical power scales as sqrt(N).

### Recommendation

**Bundle with #72.** Both are Pillar 2 probe-robustness issues. ~20 LOC + 1 test. ~30 min combined.

---

## Issue #74 — `_sandbox_execute_with_install` 60s pip install timeout is too tight for heavy dependencies with cold caches (P0)

### Summary

`src/verification/sandbox_tool.py:414` sets `timeout=60` for the Phase 1 pip install in `_sandbox_execute_with_install`. For artifacts that require scikit-learn, scipy, pandas+numpy+openpyxl, or large transitive dependency trees:

- Cold PyPI cache: download time alone can exceed 60s on a home connection.
- Slow mirror / regional Indian/Asian PyPI mirrors: slower still.
- `uv pip install` is faster than stock pip but still bound by network.

Timeouts produce `return_code != 0` → `_sandbox_execute_with_install` returns a failed dict → Tier 2 behavioral test fails → gate rejects the artifact. The rejection surfaces as "artifact failed Tier 2" rather than "timeout during dependency install," inflating M1 failure attribution.

### Where

- **Pip install timeout:** `src/verification/sandbox_tool.py:414` — `timeout=60`.
- **Phase 2 code-execution timeout:** `:430` — `timeout=30` (separate, not at issue here).

### Reproduction

```bash
# Artifact requiring scipy + scikit-learn + pandas:
extra_packages = ["scipy", "scikit-learn", "pandas"]
# Cold-cache pip install on a 10 Mbps connection takes 90-120 seconds.
# Timeout fires at 60s → install_proc.returncode != 0 → failure dict.
# Artifact rejected.
```

### Threat model / impact

- **M1 success rate depressed on data-science-heavy tasks.** T13 (anomaly detection), T10 (forecasting), and any task requiring scipy/scikit-learn are at risk.
- **CI + Docker-runner environments vary wildly in PyPI speed.** Reproduction of Chapter 6 results on a reviewer's machine may fail where the author's machine succeeded.
- **Direction of bias: condition-agnostic**, but amplitude depends on which packages each condition's generated code happens to require.

### Remediation

**A. Raise the timeout to 180s.**

Covers cold-cache installs on typical connections. Minimal risk; pip install is bounded anyway (it either completes or fails cleanly).

**B. Pre-warm the Docker base image.**

Bake common data-science dependencies (pandas, numpy, scipy, scikit-learn, openpyxl) into the sandbox base image. Phase 1 pip install only adds packages not in the base → much faster.

**C. Retry on timeout.**

If the first install times out, retry once with a longer timeout. Bounded; avoids infinite waits on genuinely-broken packages.

**D. Report install-timeout distinctly from Tier 2 failure.**

Current code path attributes install failures to general Tier 2 failure. A distinct error category (`dependency_install_timeout`) lets the analyzer exclude these from M1 failure tallies (and Chapter 4.5 can disclose they exist).

### Recommendation

**Land (A) + (D) before Phase 5.** Raise to 180s + distinct error attribution. ~10 LOC + 1 test. ~20 min.

---

## Issue #75 — `quick_validate.py::validate_skill` doesn't reject SKILL bodies containing extra `---` delimiters; DeepAgents parser may silently drop body content (MEDIUM)

### Summary

`tools/quick_validate.py:40` extracts frontmatter via `re.match(r"^---\n(.*?)\n---", content, re.DOTALL)` — non-greedy, matches the FIRST `---` and the NEXT `---`. No subsequent check for additional `---` in the body.

If the SKILL body contains `---` inline (e.g., markdown horizontal rule, YAML snippet embedded in prose, or mistaken LLM double-frontmatter), DeepAgents' skill-loader (which may use `yaml.safe_load_all` for document separators) can interpret the body as multiple YAML documents. Documents after the frontmatter may be dropped silently.

### Where

- **Validator regex:** `tools/quick_validate.py:40` — `re.match(r"^---\n(.*?)\n---", content, re.DOTALL)`.
- **No body-delimiter check:** nothing after line 54 inspects the body for extra `---`.

### Reproduction

```markdown
---
name: my-skill
description: Tests horizontal rules
---

# Overview

Some content.

---

# Caveats

This section appears after a horizontal rule.
```

`validate_skill` accepts this — frontmatter parses correctly. At runtime, a parser that splits on `\n---\n` treats "Some content." and "Caveats" as separate documents; depending on the runtime, the second document may be ignored.

### Threat model / impact

- **Runtime-behavior divergence from validation-behavior.** A skill that `validate_skill` accepts may fail silently at DeepAgent load time.
- **Silent body erasure.** The auditor sees a valid skill file on disk; the runtime sees a truncated version. Debugging requires knowing DeepAgents' internal YAML-splitting behavior.
- **Direction of bias: dependent on LLM output patterns.** If the generator frequently emits markdown horizontal rules or YAML-in-prose, this fires regularly; if not, rarely.

### Remediation

**A. Reject bodies with a bare `---` line outside frontmatter.**

```python
# tools/quick_validate.py — after frontmatter validation:
# Count occurrences of bare-line "---" in content.
count = sum(1 for line in content.splitlines() if line.strip() == "---")
if count > 2:  # frontmatter has exactly 2 (opening + closing)
    return False, (
        f"SKILL body contains {count - 2} extra '---' delimiter(s); "
        "DeepAgents parser may split body into multiple YAML documents. "
        "Escape horizontal rules as '\\---\\' or use '***' instead."
    )
```

**B. Auto-escape in the generator prompt.**

Update `src/orchestrator/generators/skill.py` system prompt with: "Never use `---` as a horizontal rule in the body; use `***` instead. Embedded `---` will be misinterpreted as a document separator."

Preventive (no false rejections) but depends on LLM compliance.

**C. Regression test.**

```python
def test_validate_skill_rejects_body_with_bare_dashes():
    content = textwrap.dedent("""\
        ---
        name: my-skill
        description: test
        ---
        # Body
        content
        ---
        trailing content
    """)
    ok, err = validate_skill_from_string(content)
    assert not ok
    assert "---" in err
```

### Recommendation

**MEDIUM — land as quick hygiene.** 10 LOC + 1 test + prompt update. ~15 min.

---

## Issue #76 — `is_generic=True` bypasses all domain filters with no defense-in-depth; single LLM mis-classification pollutes every domain (P0)

### Summary

`src/registry/store.py::search` at line 328-331 uses a `$or` clause:

```python
where_conditions = {"$or": [
    {"domain": {"$eq": domain}},
    {"is_generic": {"$eq": True}},
]}
```

Any capability flagged `is_generic=True` enters EVERY domain's search results. `is_generic` is set by `src/orchestrator/taxonomy.py::classify_and_tag_artifacts` — an LLM-driven single-pass classifier (per lines 195-205 of taxonomy.py: prompts the LLM with "the test is simple: if the implementation contains ANY hardcoded column name, field name, business term, or schema assumption, is_generic=False").

If the LLM mis-classifies a contaminated/hardcoded tool as `is_generic=True` (one decision, no retry, no cross-check), that tool pollutes every domain's search space for the rest of the run. There's no AST-based defense-in-depth (e.g., "if the code contains string literals matching known business terms, force is_generic=False").

Compounds with #69 (taxonomy classifier no retry) — a single transient LLM failure on a contaminated tool's classification has cascading effects.

### Where

- **$or bypass logic:** `src/registry/store.py:328-331`.
- **Single-pass LLM classifier (no retry, no cross-check):** `src/orchestrator/taxonomy.py::classify_and_tag_artifacts` around line 669.
- **Related: #69** — same classifier has no retry.

### Reproduction

```python
# Contaminated tool with hardcoded business columns:
source = """
async def parse_sales(path: str) -> dict:
    # hardcoded columns specific to business-analytics testbed
    cols = ["order_id", "product_id", "quantity", "unit_price", "region"]
    ...
"""
# LLM classification (transient failure or true miss):
# classify_and_tag_artifacts → is_generic=True (WRONG)

# Registry.search for a task in domain="healthcare":
# $or matches either domain="healthcare" OR is_generic=True.
# The mis-classified tool enters every future domain's search results.
```

### Threat model / impact

- **Contamination pollution across domains.** A mis-classified hardcoded tool pollutes every domain's search pool, not just its original domain.
- **Amplification factor scales with cross-domain task count.** 20 tasks across N domains → the mis-classified tool enters N search spaces instead of 1.
- **Direction of bias: pro-contamination spread.** Makes M2 contamination under-reporting (cf. #72) worse by multiplying exposure.

### Remediation

**A. AST cross-check: if the code contains literal strings matching domain-specific terms, force is_generic=False.**

```python
# src/orchestrator/taxonomy.py::classify_and_tag_artifacts — after LLM decision:
KNOWN_DOMAIN_TERMS = {
    "order_id", "product_id", "quantity", "unit_price", "region",
    # Business-analytics testbed terms; per-domain whitelist configurable
}

def _looks_domain_specific(source_code: str) -> bool:
    for term in KNOWN_DOMAIN_TERMS:
        if f'"{term}"' in source_code or f"'{term}'" in source_code:
            return True
    return False

if llm_said_generic and _looks_domain_specific(artifact.source_code):
    logger.warning(
        "Classifier said is_generic=True but code contains domain-specific terms; "
        "forcing is_generic=False (ext8 #76)."
    )
    artifact.is_generic = False
```

Similar defense-in-depth pattern to ext6's file-writer-MCP_TOOL override for the classifier.

**B. Combine with #69 retry.** Three attempts at classification + AST cross-check. Exit with sticky flag on exhaustion.

**C. Regression test.**

```python
def test_is_generic_override_forces_false_on_domain_specific_code():
    """Code containing hardcoded business terms cannot be marked is_generic=True."""
    artifact = make_artifact(source_code="...cols = ['order_id', 'product_id']...")
    with mock.patch("taxonomy.llm_classify", return_value={"is_generic": True}):
        classified = classify_and_tag_artifacts([artifact])
    assert classified[0].is_generic is False
```

### Recommendation

**Land with #69.** Same failure surface; shared retry + cross-check framework. ~40 LOC + 2 tests. ~1h combined with #69's retry fix.

---

## Issue #77 — `_detect_convergence` uses consecutive-snapshot KL; oscillating/drifting distributions mis-classified as converged (P0)

### Summary

`scripts/analyze_longitudinal.py:381-400`:

```python
def _detect_convergence(kls, threshold, min_consecutive):
    """Return the first cycle N (1-indexed) where the KL stays < threshold
    for ``min_consecutive`` consecutive cycles."""
    ...
```

The `kls` list is per-cycle values of `_kl_divergence_between_snapshots(prev, curr)` — CONSECUTIVE snapshots (line 90). Convergence is detected when consecutive-snapshot KL stays below threshold for N consecutive cycles.

Failure modes this misses:
- **Oscillation**: distribution alternates between two similar-but-distinct states. Each step's KL is small → detected as converged; actual state is not stable.
- **Slow drift**: distribution moves in one direction, small per-step KL. Detected as converged before the drift has stopped.
- **Final-state divergence**: small per-cycle KL with final state still far from the true attractor.

Proposal definition of convergence (§Metrics): "registry reaches a stable state where further optimization cycles no longer meaningfully change its contents." The correct measurement is "distance from the final stable state" — which requires comparing each cycle to the FINAL distribution, not to the previous cycle.

### Where

- **Consecutive-snapshot KL computation:** `scripts/analyze_longitudinal.py:90` (`_kl_divergence_between_snapshots`).
- **Convergence detector using the per-step values:** `scripts/analyze_longitudinal.py:381-400`.

### Reproduction

```python
# Oscillating distribution: {A: 0.5, B: 0.5} → {A: 0.6, B: 0.4} → {A: 0.5, B: 0.5} → ...
# Each step's KL is small (~0.01).
# _detect_convergence with threshold=0.02 and min_consecutive=3 fires at cycle 3.
# But the distribution is NOT stable — it's oscillating.

# Drifting: {A: 0.5} → {A: 0.51} → {A: 0.52} → ... → {A: 0.9}
# Each step's KL is tiny (~0.001).
# Detector fires at cycle 3 → reported as "converged at cycle 3".
# Actual state at cycle 30: very different from cycle 3.
```

### Threat model / impact

- **M6 cycles-to-convergence misrepresents the optimizer's actual stabilization timeline.**
- **Chapter 6 Pillar 3 RQ #2 answer weakened:** "JIT converges in N cycles" is really "JIT's per-step changes shrink below threshold for N consecutive cycles" — a weaker claim.
- **Step 18.8 shift-detection verdict affected:** "graceful/sluggish/collapse" uses the same convergence detection. Oscillations post-shift may be labeled "graceful" when they're actually unstable.
- **Direction of bias: over-report convergence.** Every ambiguous case resolves toward "converged" rather than "not stable."

### Remediation

**A. Compare each cycle to the final (or final-window-averaged) distribution.**

```python
def _detect_stable_convergence(
    snapshots: list[dict[str, float]],   # per-cycle distributions
    threshold: float,
    min_consecutive: int,
    window_size: int = 5,  # average over last N cycles to smooth noise
) -> int | None:
    if len(snapshots) < window_size:
        return None
    final_window_avg = _average_distributions(snapshots[-window_size:])
    kls_to_final = [
        _kl_divergence_between_snapshots(final_window_avg, s)
        for s in snapshots
    ]
    # Find the first cycle where kl_to_final stays below threshold consistently.
    run_len = 0
    for i, kl in enumerate(kls_to_final):
        if kl < threshold:
            run_len += 1
            if run_len >= min_consecutive:
                return (i + 1) - (min_consecutive - 1)
        else:
            run_len = 0
    return None
```

**B. Report both metrics.** Keep the consecutive-snapshot KL (describes "local stability") AND add distance-to-final (describes "approached final state"). Disclose in Chapter 4.5 that both are reported; the proposal-aligned one is distance-to-final.

**C. Regression test.**

```python
def test_detect_convergence_rejects_oscillating_distribution():
    """An oscillating series with small per-step KL should NOT be marked as converged."""
    snapshots = [{"A": 0.5, "B": 0.5}] * 5 + [{"A": 0.6, "B": 0.4}] * 5 + [{"A": 0.5, "B": 0.5}] * 5
    result = _detect_stable_convergence(snapshots, threshold=0.02, min_consecutive=3)
    # Pre-fix: returns 3 (false positive). Post-fix: returns None or a cycle inside the final-stable window.
    ...
```

### Recommendation

**Land before any longitudinal Chapter 6 analysis.** ~40 LOC + 2 regression tests. ~45 min. Bundle with ext8 #56 (off-by-one attribution) and #62 (optimizer state wipe) as the Pillar 3 M6 integrity cluster — all three must land for M6 numbers to be defensible.

---

## Issue #78 — Gini counts historical invocations of pruned (inactive) capabilities; optimizer's pruning work never reflects in Gini reduction (MEDIUM)

### Summary

`scripts/analyze_results.py::print_gini_summary` at line 673-681 computes Gini from `invocation_events` rows without joining to `registry` for `is_active`. If the optimizer prunes a capability mid-run, its historical invocations (recorded earlier in the run) still contribute to the invocation-count distribution the Gini is computed over.

A capability that was heavily used before pruning (high count) and then pruned (no longer invokable) appears as a "ghost" high-count entry in the Gini computation. This inflates the measured inequality even after the optimizer has successfully reduced active-registry concentration.

The proposal's Gini claim (§Metrics: "Healthy registries have moderate inequality; unhealthy ones have extreme skew") is ambiguous between "distribution across active capabilities" and "distribution across all historical usage." The current implementation is the latter; the more useful interpretation (how concentrated is the ACTIVE registry) is not available.

### Where

- **Gini query (no active-capability join):** `scripts/analyze_results.py:673-681`:
  ```python
  SELECT condition, run_id, capability_id, COUNT(*) AS invocations
  FROM invocation_events
  WHERE NOT COALESCE(is_pilot, FALSE){_SCOPE_SQL}
  GROUP BY condition, run_id, capability_id
  ```
- **`is_active` field:** present on `CapabilityDescriptor`; set to `False` by pruning.
- **ChromaDB stores this field** — queryable at analysis time.

### Reproduction

```
# Synthetic longitudinal: cap_X invoked 50 times in cycles 1-5, then pruned.
# Cap_Y invoked 10 times in cycles 1-20.
# Cap_Z invoked 10 times in cycles 6-20.

# Current Gini (includes cap_X):
#   counts = [50, 10, 10] → Gini ~0.47 (high inequality)

# "Active-only" Gini (excludes pruned cap_X):
#   counts = [10, 10] → Gini = 0.0 (perfect equality across active)

# Current implementation reports 0.47 throughout the run — the optimizer's
# pruning work is invisible to the metric.
```

### Threat model / impact

- **Optimizer's pruning activity is invisible in the headline Gini metric.** Chapter 6 reports a high Gini even when the active registry is perfectly balanced.
- **Pillar 3 RQ #1 framing** (seeded-vs-evolved): if evolution includes pruning, and pruning doesn't affect Gini, the "evolved is more/less concentrated than seeded" claim depends on which Gini you mean.
- **Direction of bias: over-report inequality.** The optimizer can never reduce Gini by pruning outliers; only by reducing usage of heavily-used capabilities.
- **Chapter 4.5 disclosure needed** regardless of fix — specify which Gini the thesis reports.

### Remediation

**A. Add an active-only Gini alongside the historical Gini.**

```python
# Fetch active capabilities from registry:
active_ids = {cap.name for cap in registry.list_all(is_active=True)}

per_run_rows_active_only = conn.execute(
    f"""
    SELECT condition, run_id, capability_id, COUNT(*) AS invocations
    FROM invocation_events
    WHERE NOT COALESCE(is_pilot, FALSE){_SCOPE_SQL}
      AND capability_id IN ({','.join('?' * len(active_ids))})
    GROUP BY condition, run_id, capability_id
    """,
    list(active_ids),
).fetchall()

# Compute and report BOTH:
# - Gini (historical, all rows) — current behavior, renamed "Gini (all historical)"
# - Gini (active-only) — new, renamed "Gini (active registry)"
```

Chapter 6 Pillar 3 claims use "active-only" as the primary metric.

**B. Or: join on capability_id and filter.**

Use DuckDB's ability to read a CSV/JSON list of active IDs and filter; no separate pre-query needed. Same logical outcome.

**C. Chapter 4.5 disclosure.**

Document which Gini is reported and why. Pre-fix reports were historical-Gini; post-fix reports active-Gini (or both). Disclose the interpretive difference.

**D. Regression test.**

```python
def test_gini_excludes_pruned_capabilities(tmp_path):
    """Cap_X pruned after 50 invocations → active-only Gini excludes it."""
    # Seed DB + registry; prune cap_X via registry.deactivate.
    # Compute active-only Gini; compare to historical Gini; assert they differ.
    ...
```

### Recommendation

**Land before Chapter 6 Pillar 3 Gini table.** ~40 LOC + 1 test. ~45 min. Pairs with #65 (static-condition Gini baseline) as the Gini-integrity cluster.

### Legacy data

Re-running `analyze_results.py` post-fix against existing DBs produces both Ginis (historical + active). No data migration needed — the registry's `is_active` flag is already persisted.

---

## Claims submitted but NOT added (overstated / already-covered / not reproducible)

Five additional claims were submitted in the same review batch as #61-#65. The following are logged here for audit trail:

### Submitted claim (user's #12): `analyze_compression_loss.py` under-counts mid-chain failures → **NOT CONFIRMED**

Claim: "Only loads rows where NOT gate_passed; ignores chain_success=False events mid-chain."

**Actual filter at `analyze_compression_loss.py:227`:** `clauses = ["NOT is_pilot", "(NOT gate_passed OR NOT output_in_range)"]`. The `OR NOT output_in_range` catches mid-chain failures: in `harness.py::_extract_result_compositional`, when `chain_result.chain_success is False`, the validator never runs and `output_in_range` stays at its default `False` (line 865). `chain_success=False AND output_in_range=True` is structurally impossible.

### Submitted claim (user's #13): Optimizer thresholds never met in 20-task eval → **PARTIALLY OVERSTATED**

Claim: "Optimizer will be silent during the entire primary evaluation."

**Actual thresholds** (`src/optimizer/loop.py:155-157`): `split_reuse_threshold=0.6`, `merge_reuse_threshold=0.05`, `AUTO_OPTIMIZER_THRESHOLD=20`. Prune + merge WILL fire in 20-task runs (reuse_rate=0 on untouched seeded capabilities satisfies `< 0.05` trivially; 20 invocations hits the auto-enable threshold). Only **split** is unlikely (requires reuse_rate > 0.6 AND success < 0.7, rare in a diverse 20-task window).

A cleaner framing: **Chapter 6 under-samples split-operation behavior in the primary eval; rows 18/19 longitudinal (40+ cycles) are the proper test of split**. This is already acknowledged in methodology; not a code correctness issue.

### Submitted claim (user's #16): Double-counting M4 across backfill scripts → **NOT CONFIRMED**

Claim: "If analyze_results.py sums these for the M4 metric, it will double-count capability definition cost."

**Actual behavior (`analyze_results.py:583`):** reads ONLY `evaluation_runs.token_cost` (Row 11 backfill). Never references `CapabilityDescriptor.definition_token_cost` (Row 12 backfill, ChromaDB metadata).

The two are in different stores for different purposes:
- Row 11 → DuckDB `evaluation_runs.token_cost` → M4 analyzer.
- Row 12 → ChromaDB `CapabilityDescriptor.definition_token_cost` → `src/optimizer/utility.py` Pillar-3 U(c) input.

No current code path sums them. No double-counting risk. Documentation clarity could improve (rename `backfill_definition_token_costs.py` → `backfill_pillar3_definition_cost.py` would help) but it's not a correctness bug.

### Submitted claim (user's #17): "generation_failed" default over-used → **PARTIALLY CORRECT**

Claim: "Defaults failure_reason to generation_failed for execution crashes or contract violations."

**Actual code (`harness.py:866-914`):** contract violations → `"output_shape_mismatch"`, agent timeouts → `"agent_execution_timeout"`, agent errors → `"agent_execution_error"`, chain step failures → `"chain_step_failed"`. Execution crashes DO get specific reasons, not the default.

**Valid residual concern:** the initial default IS `"generation_failed"` (line 866) and sticks for any early-exit path (e.g., `pipeline.invoke()` raising, line 677). So "pipeline raised" and "generator LLM produced nothing" share the same label. Minor framing improvement: rename default to `"pipeline_error"` and reserve `"generation_failed"` for the narrow LLM-produced-nothing case. ~10 LOC polish; bundle with #59/#64 pass.

### Submitted claim (user's #18): Concurrent-handle corruption of `prepare()` snapshot → **OVERSTATED**

Claim: "Stray pytest / uv run holding a ChromaDB handle corrupts the clean snapshot."

**Existing mitigations:**
- `scripts/_eval_registry_lock.py::acquire_runner_lock` (fcntl.flock LOCK_EX | LOCK_NB).
- All three main runners (`run_pilot.py:162`, `run_longitudinal.py:125`, `run_longitudinal_shift.py:242`) acquire this lock before registry operations.
- DuckDB's own file-lock catches concurrent writers with a loud IOException.
- ChromaDB's `shared_system_client.clear_system_cache()` is called at `harness.prepare:219` before the rmtree.

Runner-level lock + DuckDB write-lock + ChromaDB cache-clear cover the primary collision scenarios. Residual risk (stray pytest / leaked CLI) requires procedures outside the canonical run sheet. Narrower than user's framing. Defensive polish would extend the lock to `audit_contamination.py` + `analyze_*.py` but that's beyond current-correctness scope.

---

### Third-batch submissions, claims that did NOT become ext8 findings

### Submitted claim (user's #19): Execution token costs hidden from Gini (M3) → **DUPLICATE of ext8 #61**

The reviewer's framing (`composition.py _record_step_invocation` hardcodes `token_cost=0`) is correct but was already logged as ext8 #61 in the previous batch. The reviewer's additional concern ("over-weighting one-time generation cost in Gini") is a subset of #61's threat-model section. No new finding; see #61 for remediation and recommendation.

### Submitted claim (user's #20): 18-hour campaign crash via shutil.copytree race → **OVERSTATED (same class as user's #18, previously marked)**

Claim: "If OS or background process hasn't finished flushing file deletions from the optimizer's prune operation, `shutil.copytree` will raise `FileExistsError` or `PermissionError`."

**Existing mitigations:**
- `scripts/_eval_registry_lock.py` holds a cross-runner file lock (ext7 #51 remediation).
- `snapshots.py::take_clean_snapshot` and `restore_clean_snapshot` both do `rmtree` BEFORE `copytree` (lines 48-50 and 89-91) — so FileExistsError from destination-already-existing is precluded by the rmtree.
- The optimizer's `prune` operation deactivates capabilities in ChromaDB via `registry.deactivate(...)` — it does NOT asynchronously delete skills-directory files. The "unflushed file deletions from prune" scenario doesn't match the code's actual behavior.

Plausible residual risk: stray non-runner process (e.g., a debug `pytest` held open) with a file handle could cause rmtree to fail on certain OSes. This IS the same scenario user's #18 raised, which was already marked overstated. The runner-level lock + DuckDB's own file-lock + ChromaDB's cache clear cover the documented run sheet paths.

Not distinct enough from #18 to warrant a new finding. Defensive polish (extending the eval lock to cover analyzer scripts) remains as a possible Chapter 7 future-work item.

### Submitted claim (user's #25): Verification token leakage → **COVERED by ext8 #58**

Claim: "The script spreads the cost of the failed verification LLM calls across all successful tasks in the same run."

This is exactly the shape of ext8 #58 (backfill retrieval-task leakage + n_jit_tasks distortion) — the "averaging cost across records that didn't incur it" problem. For **tagged** LangFuse runs (post-Step-13.7), each trace is tagged with `task_id` and costs ARE attributed correctly per-task. For **untagged** runs, the fallback path averages — and #58 already documents this gap.

Verification calls happen within the enclosing task's trace context (per Step 13.7 `trace_context` wrapping), so they carry the task_id tag. Tagged attribution is correct; untagged fallback is not. Same bug, same remediation as #58. No new finding.

### Submitted claim (user's #26): Static baseline doesn't record `invocation_events` → **DUPLICATE of ext8 #65**

Exact duplicate. See #65 for detail section.

### Submitted claim (user's #29): Similarity threshold 0.6 vs 0.7 → **NOT REPRODUCIBLE**

Claim: "Default hardcoded to 0.6, but thesis proposal and Chapter 3 claim 0.7."

**Actual proposal (`docs/thesis_proposal.md:77`):** "Gaps are identified as subtasks with no match above a confidence threshold." No numeric value is specified.

**Actual implementation (`src/registry/store.py:360`):** `threshold: float = 0.6` — documented at `docs/proposal_vs_implementation_v2.md:25` as the implementation's chosen value.

The `0.7` value the user cites may be a confusion with `split_success_threshold=0.7` in `src/optimizer/loop.py:156` (Pillar 3, not Pillar 1). The user's "Step 13.9 False Positive prevention evidence" can't be cross-checked — grep for that step + 0.7 in docs returns no match.

No reproducible inconsistency. If the user can point at a specific document claiming 0.7, re-evaluate; until then, logged as unverified.

### Submitted claim (user's #30): AGENT snapshot loses system_prompt via `str()` → **NOT CONFIRMED**

Claim: "Generic str() or json.dumps() on agent_config; if __str__ is truncated, auditor can't see system_prompt."

**Actual code (`tests/evaluation/harness.py:155-161`):**
```python
elif art.capability_type == CapabilityType.AGENT:
    if art.agent_config is None:
        body = ""
    elif isinstance(art.agent_config, dict):
        body = _json.dumps(art.agent_config, default=str)
    else:
        body = str(art.agent_config)
```

Primary path: `json.dumps(dict, default=str)` serializes the **full** dict including `system_prompt`. NOT truncated.

Fallback path: `str(non_dict)` is only reached when `agent_config` is NOT a dict — which per the schema should be rare/never. If it's ever a dict subclass with a weird `__str__`, the output might be abbreviated — but that's a defensive concern about hypothetical schema drift, not a current bug.

Current M2 audit snapshots DO include system_prompt in full. No reproducible information loss.

Residual concern (different from user's framing): if the downstream M2 audit UI truncates `source_body` for display, system_prompt could be visually cut off even though the DB has full data. That's an audit-tool UI concern, not a snapshot-generation bug. Could be checked separately by auditing the `audit_contamination.py` display code — but distinct from the claim as stated.

---

### Fourth-batch submissions, claims that did NOT become ext8 findings

### Submitted claim (user's #44): Double-backfill of failed retries → **COVERED by ext8 #58**

Claim: "Failed retries are not excluded from per-task cost divisions, leading to massive over-reporting of M4 costs for JIT conditions."

Retries within a single task live under that task's enclosing `trace_context` (per Step 13.7 wrapping at `harness.py`), so their generation costs ARE correctly tagged with the task's `task_id`. The per-task-tags branch of `compute_per_task_costs` (line 570-574) attributes them correctly.

The untagged-run fallback at line 575-583 does average all tokens across records (including retries' tokens), which is the exact scope of **ext8 #58** (retrieval-task leakage + n_jit_tasks distortion). The remediation for #58 (per-task tag detection + per-run fallback) closes #44 as a side-effect.

No new finding needed.

### Submitted claim (user's #48): Classification accuracy measures classifier intent vs generator output → **NOT CONFIRMED**

Claim: "Measures classifier intent rather than generator output; a 'Success' in classification can still lead to a 'Failure' in behavior."

The metric `print_classification_accuracy` (`analyze_results.py:739`) is explicitly documented as measuring classifier accuracy — not end-to-end success. Its docstring (lines 740-788) specifies:
- What it measures: whether the classifier's primary-type decision matches `TaskDefinition.expected_type`.
- What it does NOT measure: generator-output quality, runtime success.
- Why: proposal Pillar 1 RQ #1 asks "can an LLM classify correctly?" — this metric answers exactly that question.

End-to-end success is the M1 metric, reported separately. Chapter 6 reports both.

User's concern is a framing question (could the label be clearer?) rather than a bug. The docstring is explicit about scope. No finding.

### Submitted claim (user's #49): Tier 2 latency dominated by Docker/pip wall-clock → **NOT CONFIRMED**

Claim: "Tier 2 latency metrics are dominated by network/Docker overhead (60s+) rather than code execution (ms), masking Mock Smell signals."

**Actual code (`behavioral.py:261-264`):**
```python
async def _run():
    start = time.monotonic()
    try:
        result = await {func_name}(**json.loads({inputs_json!r}))
        elapsed = time.monotonic() - start
```

The `elapsed` measurement is INSIDE the sandbox, AFTER the function call starts. Docker startup + pip install happen BEFORE the `start` reference — they are NOT included in `elapsed`. Mock Smell detection (`_check_latency_variance`) reads this inside-sandbox elapsed, not wall-clock.

Separately, ext8 #70 flagged a **related** concern: the OUTER M5 latency (harness's `time.monotonic() - t_start`) DOES include Docker + pip. But that's M5-specific and already documented under #70's ecological-validity disclosure.

User's specific claim that Mock Smell is masked by Docker overhead doesn't hold up against the code. No finding. (Related probe-robustness concern lives at ext8 #73 — the cv threshold is fragile for different reasons.)

---

## Fifth-batch submissions, claims already addressed or not distinct

No fifth-batch additions. The full claim set from this audit pass is processed.

---

## Relationship to ext6/ext7 findings

ext8's twenty-three findings don't follow a single drift pattern; they're independent failure modes across distinct analysis + orchestration layers:

| # | Drift class | Notes |
|---|---|---|
| 56 | **Time-window attribution logic error** | Off-by-one in `_load_cycle_data`; root cause is `timestamp` ambiguity between task-start and task-end semantics |
| 57 | **Statistical-methodology / resume semantics mismatch** | Fixed-seed-across-resumes design assumes single-pool sampling; the resume workflow operates on shrinking pools |
| 58 | **Retrieval-task exclusion scope drift** | Two independent gaps in the same file; one is an incompletely-propagated invariant, the other a partial-run-unaware assumption |
| 59 | **Cross-store M1-predicate divergence** | DuckDB and LangFuse compute "success" differently after ext6 #32 tightened one side without updating the other |
| 60 | **Column-label vs semantics mismatch** | Documented intentional overcount; labels don't reflect it. Fix is either rename the column or change the computation. |
| 61 | **Latent column defect** | `token_cost=0` hardcoded in `invocation_events`; no current analyzer reads it but forecloses future cost-weighted analysis |
| 62 | **Closure-state-vs-restart-pattern interaction** | Nonlocal in pipeline closure reset on every per-task recompile; state must be externalized |
| 63 | **Type-asymmetric registration validation** | MCP_TOOL gets AST schema extraction at registration; SKILL/AGENT get LLM-claimed schemas with no cross-check |
| 64 | **Cross-store M1-predicate divergence (compositional sibling of #59)** | Same root cause in `_extract_result_compositional`; bundle with #59 |
| 65 | **Asymmetric condition telemetry** | Static condition never records invocations; JIT-vs-static Gini comparison has no baseline |
| 66 | **Pillar 3 formula-vs-proposal gap** | `compute_utility` omits `definition_token_cost`; "Context Economy" tension unoptimizable |
| 67 | **Cross-store M5 latency divergence (sibling of #59/#64 but for latency)** | LangFuse `m5.latency_ms` reads nonexistent dict key → always 0 |
| 68 | **Static-condition validation asymmetry** | Static path skips `output_contract.check`; JIT-vs-static Pillar 1 comparison inflates static |
| 69 | **Strict-gate coverage gap (taxonomy classifier)** | `get_for_query` lacks the 3x-retry pattern used by decomposition / classification / verification |
| 70 | **Ecological-validity / harness-affordance disclosure** | Methodology concern rather than correctness; Chapter 4.5 needs the gap labelled |
| 71 | **Cross-run state persistence gap** | MetricsStore reset per run → `maintenance_burden` loses cross-run history → longitudinal "learning" claim unsupported |
| 72 | **Probe-bypass via trivial variation** | B2.3 variation probe uses strict equality; timestamp/UUID variation escapes detection |
| 73 | **Statistical-power gap in small-sample mock smell** | `cv<0.10` threshold too tight for sub-ms means; jitter dominates |
| 74 | **Timeout vs dependency-install mismatch** | 60s pip timeout too tight for heavy-dependency artifacts with cold caches |
| 75 | **Validator vs runtime parser semantic gap** | SKILL validator accepts bodies that runtime parser may silently truncate |
| 76 | **Single-decision-point LLM trust** | `is_generic` has no defense-in-depth; one mis-classification pollutes all domains |
| 77 | **Convergence metric formula mismatch** | Consecutive-snapshot KL vs proposal's implied distance-to-final-state |
| 78 | **Metric semantic ambiguity (historical vs active)** | Gini counts pruned capabilities' historical invocations; active-registry concentration invisible |

Cross-references:
- **#56 + #62 + #71 combine** on Pillar 3 learning-over-time claims. #56 shifts per-cycle attribution; #62 wipes optimizer closure state per task; #71 wipes MetricsStore per run. Three layers of state loss; all three must land for "learning over time" to have code backing.
- **#58 cascades with ext6 #28 + #30 + ext7 #36 + #37:** five bugs in `backfill_token_costs.py`. Plus #25 (rejected) is the same design surface.
- **#59 + #64 + #67** form a cross-store parity triplet: LangFuse vs DuckDB diverge on M1 (both halves — `_run_jit` and `_extract_result_compositional`) AND M5 latency. Bundle with ext6 #32 + ext7 #44 for complete cross-store integrity.
- **#65 + #68 + ext6 #31** form the static-condition integrity triplet: #31 fixed the baseline, #65 fixes telemetry, #68 fixes validation.
- **#63 + #66**: both are Pillar 3 formula / enforcement gaps. #63 at registration time (schema validation missing for SKILL/AGENT); #66 at utility computation time (definition_token_cost missing from formula).
- **#61 + #66 + #67**: three metric-field omissions. #61 hardcodes invocation_events.token_cost=0; #66 omits definition_token_cost from utility; #67 references a nonexistent key for LangFuse latency. All three silently under-report costs/latency that the proposal claims to track.
- **#69 + ext6/§12 #14-#16**: strict-gate pattern coverage. #69 is the single classifier stage that was never migrated.
- **#72 + #73 + #76**: Pillar 2 probe-robustness cluster. All three weaken M2 contamination detection. #72 bypasses via output-noise; #73 under-powers via statistical jitter; #76 amplifies via `is_generic` pollution.
- **#74 + #70**: harness-vs-production ecological-validity. #74 is specifically about pip timeout; #70 is the broader disclosure.
- **#56 + #62 + #71 + #77**: Pillar 3 M6 convergence integrity cluster. #56 shifts cycle attribution; #62 wipes optimizer closure state per task; #71 wipes MetricsStore per run; #77 uses wrong formula for convergence detection. All four must land for M6 convergence claim to be defensible.
- **#65 + #78**: Gini integrity cluster. #65 provides the static-condition data; #78 distinguishes historical-vs-active.
- **#75**: standalone validator hygiene item; no direct cluster cross-references.

Guard patterns for future regressions:

- **Timestamp semantics**: every `datetime` column's docstring must specify start-vs-end-vs-other (#56). CI lint could enforce docstring presence.
- **Resume-safe sampling**: any "resumable" sampling tool should pre-commit and persist its sample plan; re-running the tool reads the plan rather than re-drawing (#57).
- **Cross-store metric parity**: every metric computed in multiple stores should flow through a single shared predicate function or a shared dict key (#59/#64/#67). One test asserts parity on synthetic rows across all metric sources.
- **Column-name-vs-semantics match**: reports that include "overcount / approximation / see docstring" caveats in comments should surface the caveat in the column name or immediate output text (#60).
- **Closure state shouldn't carry cross-task state** (#62) when the container is known to be recreated per task. Any nonlocal inside a factory function is suspicious for cross-task workflows; tests should explicitly verify persistence across recompile.
- **Type-parity in registration paths** (#63): if MCP_TOOL gets AST validation, SKILL/AGENT should delegate to equivalent type-specific extractors.
- **Symmetric validation across compared conditions** (#65/#68): if the analysis question is "does X differ between A and B?", both must generate comparable data AND pass through the same validation surface.
- **Formula-vs-proposal alignment test** (#66): for every proposal-specified formula, a unit test asserts the implementation reads all named inputs.
- **Dict-key integrity test** (#67): any `result.get(KEY, default)` site where KEY is a string literal should be paired with a test that the producer actually writes KEY into the dict. Stale keys should fail loud, not default silently.
- **Strict-gate coverage** (#69): every LLM-calling stage that drives proposal-critical routing or filtering should follow the retry + sticky-flag + exit-code pattern. A one-pass audit for `structured_llm.invoke(...)` wrapped in a single try/except with a fallback return catches stages outside the pattern.
- **Cross-run state persistence contract** (#71): any in-memory store used by a component with "learning over time" claims must be either (a) persistent by construction or (b) explicitly re-hydrated at startup from a persistent source. Fresh empty containers are the default; if that's wrong for the component, tests should enforce non-empty init from prior state.
- **Probe robustness vs generator output-noise** (#72): equality-based checks on LLM output should normalize away timestamp/UUID/counter fields before comparison. A parametrized test across known noise patterns prevents regression.
- **Statistical-power gating** (#73): any threshold-based probe should document its sample-size assumption and test against jitter-heavy synthetic runs before release.
- **Validator-vs-runtime-parser alignment** (#75): every content validator should have a paired "can the runtime parser process this?" check. Divergences here are silent-correctness failures at load time.
- **Defense-in-depth for LLM-driven single-decision routing** (#76): any LLM decision that has ripple effects (registry tagging, domain classification, capability type) should either have retry + AST cross-check OR be gated by a downstream structural override. Single-point-of-trust with no fallback is brittle.
- **Convergence-metric semantics** (#77): distance-to-final-state, not distance-to-previous-step. Consecutive-snapshot KL measures local stability, not global convergence.
- **Historical-vs-active metric scope** (#78): when the subject of a metric can be pruned/removed, explicitly state which population the metric covers. "All historical usage" and "current active registry" are both defensible; the default should match the proposal's claim.

---

## Parent-document integration (pending)

When ext8 lands, fold forward into `security_audit_2026_04_19.md`:

1. Update header: "Fifty-five findings now → Seventy-eight findings; #19-#78 open as of 2026-04-20."
2. Insert TL;DR rows #56-#78 after ext7's #55.
3. Tranche 3 (eval-blocking set) gains twenty-three rows — seventeen P0 (#56, #57, #58, #59, #62, #63, #64, #65, #66, #67, #68, #69, #71, #72, #74, #76, #77) must-fix-before-Chapter-6-finalization; #60 HIGH is label-correctness; #61/#73/#75/#78 MEDIUM is latent-defects / probe-fragility / validator-hygiene / metric-semantics; #70 MEDIUM methodology-disclosure.
4. Extend "Behavioral regressions expected":
   - (#56) `_load_cycle_data` bucketing uses half-open intervals; per-cycle attribution shifts by one cycle vs pre-fix.
   - (#57) `audit_contamination.py` persists a sample plan; resume reads the plan; loud warning if plan is missing + labeled rows exist.
   - (#58) Backfill script zeros retrieval tasks in all code paths; `n_jit_tasks` computed from actual task IDs.
   - (#59/#64) `EvaluationRecord.is_success()` class method is the single source of truth for M1; both harness sites flip `output_in_range=False` on shape-mismatch promotion.
   - (#60) Tier 2 probe analyzer reports per-event-severity alongside per-category-severity-hits; column labels clarify the two views.
   - (#61) `invocation_events.token_cost` populated via backfill extension (or column removed).
   - (#62) Optimizer state persisted via new `optimizer_state` DuckDB table; pipeline recompile safe.
   - (#63) `_register_artifact_directly` runs schema-extraction + mismatch handling for SKILL/AGENT.
   - (#65) `_run_static` records one invocation per task; static condition comparable in Gini.
   - (#66) `compute_utility` adds `-delta * definition_token_cost` term; signature threads the registry.
   - (#67) LangFuse M5 score reads `result["attempt_latency_ms"]` (correct key); producer writes it upstream.
   - (#68) `_run_static` applies `output_contract.check`; shape-mismatched rows no longer inflate static M1.
   - (#69) `DomainTaxonomy.get_for_query` has 3x retry + loud-fail on exhaustion.
   - (#70) Chapter 4.5 discloses harness affordances (fixture injection, pip timeouts); optional `--production-mode` flag for cross-check.
   - (#71) MetricsStore accepts persistent path; harness + longitudinal use a per-campaign DB instead of `:memory:`.
   - (#72) Variation probe normalizes timestamps/UUIDs/counters before strict-equality check.
   - (#73) Mock-smell cv threshold tuned for small-sample jitter; absolute-stdev gate added.
   - (#74) Pip install timeout raised to 180s; distinct `dependency_install_timeout` error category.
   - (#75) `validate_skill` rejects bodies containing extra `---` delimiters; generator prompt updated.
   - (#76) `is_generic` decision gains AST cross-check; retry pattern applied to taxonomy classifier.
   - (#77) Convergence metric computes distance-to-final-window-average, not consecutive-snapshot.
   - (#78) Gini reports both historical and active-registry variants; Chapter 4.5 disclosure clarifies semantics.
5. End-of-audit note: "Extended 2026-04-20 (eighth extension) with findings #56 - #78 (detail sections in `security_audit_2026_04_19_ext8.md`). 17 P0 correctness bugs (#56-#59, #62-#69, #71, #72, #74, #76, #77); 1 HIGH (#60); 5 MEDIUM (#61, #70, #73, #75, #78). Reviewer batches across four passes produced 23 confirmed findings (#56-#78) plus claims logged as overstated/duplicate in §Claims submitted but NOT added."

---

**End of extension.**
