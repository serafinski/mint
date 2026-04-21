# Security / Robustness Audit — 2026-04-19, Eleventh Extension (#109-#113)

**Parent document:** [`security_audit_2026_04_19.md`](./security_audit_2026_04_19.md) — #1-#18 all ✅ FIXED 2026-04-20.
**Sibling extensions:**
- [`security_audit_2026_04_19_ext4.md`](./security_audit_2026_04_19_ext4.md) — #12-#14 ✅ (folded into parent).
- [`security_audit_2026_04_19_ext5.md`](./security_audit_2026_04_19_ext5.md) — #15-#18 ✅ (folded into parent).
- [`security_audit_2026_04_19_ext6.md`](./security_audit_2026_04_19_ext6.md) — #19-#43 ✅ all FIXED 2026-04-20.
- [`security_audit_2026_04_19_ext7.md`](./security_audit_2026_04_19_ext7.md) — #44-#55 ✅ all FIXED 2026-04-20.
- [`security_audit_2026_04_19_ext8.md`](./security_audit_2026_04_19_ext8.md) — #56-#78 (20 ✅ FIXED + 1 ❎ false-positive (#56) + 1 ✅ partial (#63, Chapter 7 remainder) + 2 ❎ acknowledged (#61 MEDIUM latent, #70 MEDIUM methodology). Genuinely-code-open: 2 — #61, #63).
- [`security_audit_2026_04_19_ext9.md`](./security_audit_2026_04_19_ext9.md) — #79-#98 ✅ **all FIXED** (re-verified 2026-04-21 by direct code-comment cross-reference; every finding carries a pinned fix).
- [`security_audit_2026_04_19_ext10.md`](./security_audit_2026_04_19_ext10.md) — #98-#108 (re-verified 2026-04-21, second pass: #98-#107 ✅ FIXED; #108 ⚠ latent/documented — list still in execution order but `ChainResult.final_output` property + docstring document the hazard; all current consumers use the safe access path, audit-classified MEDIUM latent).

**This extension's scope.** Two **P0 regressions against the ext8 #78 fix** (Gini pruned-cap exclusion) — both discovered via direct reproduction by the reviewer. One is a data-contamination bug (cross-run prune leakage), the other is a hard crash on a legitimate CLI path (`--since`). Both live in `scripts/analyze_results.py::print_gini_summary`; the ext8 #78 remediation solved the original "pruned caps inflate Gini" problem but introduced two new issues while doing so.

**Status at time of write (2026-04-20):** all five **open**. Total open across audit series: **58 findings** (ext8+ext9+ext10+ext11).

**Cross-audit status snapshot as of 2026-04-21 (post-ext12 + re-verification sweep):**
- ext1-#18 parent: 18/18 ✅
- ext6 #19-#43: 25/25 ✅
- ext7 #44-#55: 12/12 ✅
- ext8 #56-#78: 20 ✅ FIXED + 1 ❎ false-positive (#56) + 1 ✅ PARTIAL (#63, WARN-log defensive check; audit §A full AST-extract remediation is Chapter 7 work per audit's own estimate ~3-4h) + 2 ❎ acknowledged-not-fixed (#61 MEDIUM latent Chapter-7, #70 MEDIUM methodology Chapter 4.5 doc). **Genuinely code-open: 2 (#61, #63).**
- ext9 #79-#98: 20/20 ✅
- ext10 #98-#107: 10/10 ✅; ext10 #108 ⚠ latent/documented (not structurally fixed).
- ext11 #109-#113: 5/5 ✅
- ext12 #114-#123: 10/10 ✅ (fixed 2026-04-21).
**Total genuinely open: 2 (ext8 #61, ext8 #63). Total latent/documented: 2 (ext10 #108, ext8 #70).** No result-destroying bugs remain on the run-sheet critical path.

**Status update 2026-04-21 — all five FIXED:**

- **#109** ✅ FIXED. `scripts/analyze_results.py::print_gini_summary` prune subquery correlates on `(capability_id, run_id)` AND scopes the subquery via `_scope_for_table("cycle_events")`. Regression tests `test_gini_prune_exclusion_does_not_bleed_across_runs` + `test_gini_prune_exclusion_still_works_within_same_run` in `tests/unit/test_analyze_results.py`.
- **#110** ✅ FIXED. New module-level helper `_scope_for_table(table, scope_sql=None)` rewrites `timestamp` → `occurred_at` for `invocation_events` / `cycle_events`. `print_gini_summary` routes both its main query and its legacy-fallback through the helper. Regression tests `test_since_scope_uses_occurred_at_on_invocation_events` + `test_scope_for_table_rewrites_since_column_for_occurred_at_tables`. The `test_every_analyzer_query_applies_scope_or_is_exempt` guard was extended to recognize `_scope_for_table`/`scope_ie`/`scope_ce` as valid scope references.
- **#111** ✅ FIXED. `docs/run_sheet.md:94, 99` now use `SELECT run_id FROM evaluation_runs GROUP BY run_id ORDER BY MAX(timestamp) DESC LIMIT 1` (option A). Explanatory comment added.
- **#112** ✅ FIXED. `docs/run_sheet.md:113` Row 12 command now carries `--stores eval` with rationale inline.
- **#113** ✅ FIXED. Both `scripts/run_longitudinal.py` and `scripts/run_longitudinal_shift.py` gained a `--reuse-metrics-store` flag (opt-in, default off) and a guarded `unlink()` at invocation start. Regression tests `test_longitudinal_runners_declare_reuse_metrics_store_flag` + `test_longitudinal_runners_reset_metrics_store_by_default` + `test_longitudinal_reset_is_isolated_from_sibling_runner_store` in `tests/unit/test_metrics_store_persistence.py`.

Unit suite: 1929 passed. No regressions.

| # | Severity | Scope | Impact |
|---|---|---|---|
| 109 | **P0 CRITICAL** | `analyze_results.py::print_gini_summary` prune subquery | Cross-run Gini contamination: pruning in run B zeros out cap_x's counts in run A's Gini too |
| 110 | **P0 CRITICAL** | `_resolve_scope` `--since` uses `timestamp` column; Gini query runs against `invocation_events` which uses `occurred_at` | Hard crash on Row 17 `--since` invocation: `Binder Error: Referenced column "timestamp" not found in FROM clause` |
| 111 | **P0** | `run_sheet.md:94, 99` run-id discovery SQL | `SELECT DISTINCT run_id ORDER BY timestamp DESC LIMIT 1` can return non-latest run_id in DuckDB (DISTINCT + ORDER-BY-non-selected-column semantics); rows 18/19 may silently analyze the wrong run |
| 112 | MEDIUM | `run_sheet.md:113` Row 12 command lacks `--stores eval` | `backfill_definition_token_costs.py` defaults to `--stores all` → mutates `data/cli/chroma` during an eval campaign; unexpected side-effect on user's CLI registry |
| 113 | **P0 CRITICAL** | Longitudinal `metrics_store` persistent path is hardcoded + never reset across invocations | Rows 9/10 re-runs on the same machine silently read stale prior-invocation utility scores + invocation counts; prune/split/merge decisions flip based on contaminated history |

---

## Issue #109 — Gini prune-exclusion subquery is cross-run contaminated; pruning a capability in one run erases its counts from every other run's Gini (P0 CRITICAL)

### Summary

Ext8 #78 correctly identified that pruned capabilities' historical invocations were inflating Gini (ghost high-count rows). The fix: `LEFT JOIN cycle_events WHERE event_type='pruned'` and filter out the matched capability_ids.

The fix's subquery is un-scoped:

```sql
LEFT JOIN (
    SELECT DISTINCT capability_id
    FROM cycle_events
    WHERE event_type = 'pruned'
) pruned ON ie.capability_id = pruned.capability_id
WHERE ... AND pruned.capability_id IS NULL ...
```

`SELECT DISTINCT capability_id FROM cycle_events WHERE event_type='pruned'` returns capability names pruned in ANY run at ANY time. The join then excludes rows whose `capability_id` appears in that set — regardless of which run was being analyzed.

Consequence: if run A used `cap_x` 100 times (never pruned) AND run B pruned `cap_x` after using it, the analyzer running on run A's `--run-ids` filter STILL excludes `cap_x` from run A's Gini. Run A's capability concentration collapses to whatever caps survived both runs.

Reviewer's reproduction matches exactly:
- Run A: `cap_x=100, cap_y=1` → expected Gini = 0.4901
- Run B: pruned `cap_x`
- Reported Gini on run A: **0.000** (only `cap_y=1` remains in the counts list; single-element Gini = 0)

### Where

- **Prune subquery (no run scope):** `scripts/analyze_results.py:687-691`:
  ```python
  LEFT JOIN (
      SELECT DISTINCT capability_id
      FROM cycle_events
      WHERE event_type = 'pruned'
  ) pruned ON ie.capability_id = pruned.capability_id
  ```
- **Join condition (no run correlation):** line 691 — matches by capability_id only, not by (capability_id, run_id).

### Reproduction

```sql
-- Seed DB state:
INSERT INTO invocation_events (run_id, capability_id, ...) VALUES
    ('run_A', 'cap_x', ...) × 100,
    ('run_A', 'cap_y', ...) × 1;
INSERT INTO cycle_events (run_id, capability_id, event_type, ...) VALUES
    ('run_B', 'cap_x', 'pruned', ...);

-- Run the Gini query (via analyze_results.py with --run-ids run_A):
-- Expected counts after filter: [100, 1] → Gini ≈ 0.4901
-- Actual counts (cap_x excluded via un-scoped prune subquery): [1] → Gini = 0.0
```

User's direct reproduction against the tip confirms.

### Threat model / impact

- **Chapter 6 Pillar 3 M3 Gini is silently corrupted** whenever a DB contains multiple longitudinal-style runs with differing prune histories.
- **Direction of bias: artificially reports lower Gini** (more "equal") for runs whose popular capabilities happened to be pruned in OTHER runs. Makes the registry look healthier than it is.
- **Compounds with ext8 #78's own success.** The fix correctly excluded run A's OWN pruning of cap_x (desired), but also incorrectly excluded run A's invocations of cap_x when OTHER runs pruned it (not desired).
- **Any multi-run DB triggers the bug.** User's #49 in ext7 established that campaign DBs can accumulate runs across re-runs; any accumulation exposes Gini to this cross-run bleed.

### Remediation

**A. Scope the prune subquery by run_id via correlated join.**

```sql
-- scripts/analyze_results.py:685-696 — after fix:
SELECT ie.condition, ie.run_id, ie.capability_id, COUNT(*) AS invocations
FROM invocation_events ie
LEFT JOIN (
    SELECT DISTINCT run_id, capability_id   -- NEW: include run_id
    FROM cycle_events
    WHERE event_type = 'pruned'
) pruned
    ON ie.capability_id = pruned.capability_id
    AND ie.run_id = pruned.run_id            -- NEW: correlate on run_id
WHERE NOT COALESCE(ie.is_pilot, FALSE)
  AND pruned.capability_id IS NULL
  {_SCOPE_SQL_PREFIX_IE}
GROUP BY ie.condition, ie.run_id, ie.capability_id
ORDER BY ie.condition, ie.run_id, ie.capability_id
```

The join now excludes a row only when that capability was pruned IN THAT RUN — preserving cross-run isolation.

**B. Defence-in-depth: also scope the prune subquery by the caller's `--run-ids`.**

When the analyzer is scoped to a specific run-set, the prune subquery should see only that scope's pruning events:

```sql
LEFT JOIN (
    SELECT DISTINCT run_id, capability_id
    FROM cycle_events
    WHERE event_type = 'pruned'
      {_SCOPE_SQL_FOR_CYCLE_EVENTS}   -- NEW: apply user-scope to pruning events too
) pruned ...
```

Belt-and-suspenders: even if a future bug re-introduces cross-run matching, the subquery's data pool is scoped first.

**C. Regression test.**

```python
def test_gini_does_not_bleed_prune_events_across_runs(tmp_path):
    """Pruning cap_x in run B must not affect run A's Gini."""
    db = _seed_cross_run_prune_scenario(tmp_path)
    # run A: cap_x=100, cap_y=1 (expected Gini ~0.49)
    # run B: pruned cap_x
    scope = _resolve_scope(db, run_ids=["run_A"], since=None)
    counts_for_run_a = _run_gini_query(db, scope)
    # Pre-fix: counts = [1] → Gini = 0.0
    # Post-fix: counts = [100, 1] → Gini ≈ 0.49
    assert _gini_coefficient(counts_for_run_a) > 0.4
```

### Recommendation

**P0 — blocking any Chapter 6 Pillar 3 M3 Gini table.** ~10 LOC SQL fix + 1 regression test. ~20 min.

### Legacy data

Existing Chapter 6 numbers computed pre-fix are wrong for any DB with cross-run prune events. Re-run `analyze_results.py` post-fix against the same DB → corrected numbers.

---

## Issue #110 — `_resolve_scope` returns `AND timestamp >= ...` which is appended to an `invocation_events` query; `invocation_events` uses `occurred_at` — hard crash on `--since` (P0 CRITICAL)

### Summary

Two schema-coupling bugs combine:

1. **`_resolve_scope` at `scripts/analyze_results.py:174-176`** returns:
   ```python
   return f" AND timestamp >= '{safe_since}'"
   ```
   Hardcoded column name: `timestamp`. This is correct for `evaluation_runs` (which has a `timestamp` column) but not for `invocation_events`.

2. **Gini query at line 693 (and fallback at line 703)** appends `_SCOPE_SQL` to a query against `invocation_events`:
   ```python
   f"WHERE NOT COALESCE(ie.is_pilot, FALSE) AND pruned.capability_id IS NULL{_SCOPE_SQL.replace(...)}"
   ```
   When `_SCOPE_SQL` carries `AND timestamp >= '...'`, the final SQL references a column that doesn't exist in `invocation_events`. The table's time column is `occurred_at` (per `evaluation_store.py:77`).

Direct crash: `Binder Error: Referenced column "timestamp" not found in FROM clause!`

The crash happens at the first `--since`-scoped run of Row 17. Cannot be worked around without editing the code.

### Where

- **`_resolve_scope` hardcoded column:** `scripts/analyze_results.py:174-176`:
  ```python
  if since is not None:
      safe_since = since.replace("'", "''")
      return f" AND timestamp >= '{safe_since}'"
  ```
- **Gini query fallout (applies the fragment to invocation_events):** `scripts/analyze_results.py:693` (with LEFT JOIN variant) + `:703` (fallback without prune filter).
- **Schema mismatch:** `src/registry/evaluation_store.py:77` — `invocation_events.occurred_at TIMESTAMP`; no `timestamp` column.
- **Other queries that use `_SCOPE_SQL` against `evaluation_runs` (work correctly):** `:255, :283, :306, :527, :860, :873, :1152` — these query `evaluation_runs` which DOES have `timestamp`.

### Reproduction

```bash
$ uv run scripts/analyze_results.py --since 2026-04-20T00:00:00
...
Traceback (most recent call last):
  File "analyze_results.py", line 683, in print_gini_summary
    per_run_rows = conn.execute(f"...").fetchall()
duckdb.duckdb.BinderException: Binder Error: Referenced column "timestamp" not found in FROM clause!
```

User's reported error message matches verbatim.

### Threat model / impact

- **`--since` is a documented CLI flag (per line 1029-1032) but is completely non-functional.** The run sheet's "scope campaign by time window" pattern cannot be used.
- **Workaround requires falling back to `--run-ids`** which the user might not know individually. For campaigns discovered by timestamp, `--since` is the natural entry point.
- **Compounds with ext9 #82 (shift marker timing) and ext8 #56 (cycle bucketing)** — all three are symptoms of inconsistent time-column naming across the analyzer's data stores. `evaluation_runs.timestamp`, `invocation_events.occurred_at`, `cycle_events.occurred_at`, `cycle_utility_scores.occurred_at` — the analyzer mixes these and the `--since` scope doesn't know which table it's being applied to.

### Remediation

**A. Return a table-aware scope fragment.**

The simplest fix: make `_resolve_scope` return a dict of per-table fragments, and callers pick the right one:

```python
# scripts/analyze_results.py:127 — change signature:
def _resolve_scope(...) -> dict[str, str]:
    """Return {table_alias: sql_fragment} so callers can pick per-query."""
    if run_ids:
        frag = " AND run_id IN ({})".format(...)
        return {
            "evaluation_runs": frag,
            "invocation_events": frag,
            "cycle_events": frag,
        }
    if since is not None:
        return {
            "evaluation_runs": f" AND timestamp >= '{safe_since}'",
            "invocation_events": f" AND occurred_at >= '{safe_since}'",
            "cycle_events": f" AND occurred_at >= '{safe_since}'",
        }
    # auto-select fallback: returns run_id-based fragment for all tables
    ...
```

Callers:
```python
# Gini query:
f"... WHERE ... AND pruned.capability_id IS NULL {_SCOPE_SQL['invocation_events']}"
# Evaluation runs summary query:
f"... WHERE ... {_SCOPE_SQL['evaluation_runs']}"
```

**B. Alternative (simpler): remap the column name at interpolation time.**

```python
# Keep _SCOPE_SQL as a single string; define a helper:
def _scope_for_table(table: str, scope_sql: str) -> str:
    if table == "invocation_events" or table == "cycle_events":
        return scope_sql.replace("timestamp >=", "occurred_at >=")
    return scope_sql
```

Then use `_scope_for_table("invocation_events", _SCOPE_SQL)` wherever the scope is applied to a non-evaluation_runs table. Smaller diff but more fragile.

**C. Regression test.**

```python
def test_analyze_results_since_scope_works_for_invocation_events(tmp_path):
    """--since must not crash on the Gini query."""
    db = _seed_minimal_eval_db(tmp_path)
    # Should not raise:
    subprocess.run(
        ["python", "-m", "scripts.analyze_results", "--since", "2026-04-20T00:00:00"],
        check=True,
    )
```

Basic smoke — catches regression on any future query that applies the scope to the wrong table.

### Recommendation

**P0 — ship immediately; documented CLI flag is currently broken.** ~20 LOC for option (A) or ~5 LOC for option (B) + 1 test. ~20-30 min.

### Legacy data

Not applicable — the fix changes only query formation; existing data is unaffected.

---

## Issue #111 — `run_sheet.md` run-id discovery SQL uses `SELECT DISTINCT ... ORDER BY timestamp` which can return a non-latest run_id in DuckDB (P0)

### Summary

`docs/run_sheet.md:94-103` populates `$LONGITUDINAL_RUN_ID` and `$SHIFT_RUN_ID` (used by rows 18 and 19) via:

```sql
SELECT DISTINCT run_id FROM evaluation_runs ORDER BY timestamp DESC LIMIT 1;
```

This SQL has a subtle correctness bug: `SELECT DISTINCT run_id` de-duplicates on `run_id` alone, but `ORDER BY timestamp` references a column NOT in the SELECT list. DuckDB's handling of DISTINCT + ORDER-BY-non-selected-column is **not guaranteed to pick the row with the max timestamp per distinct value**. The engine may:
1. Apply DISTINCT first (collapsing duplicates to one arbitrary row per run_id, losing timestamp-order information)
2. Then ORDER BY that reduced set's arbitrarily-picked timestamp
3. LIMIT 1

The reviewer reproduced directly: running this query against a DB with multiple runs returned a non-latest run_id. Rows 18/19's analyzer therefore analyzes the wrong run silently — no error, no warning, just Chapter 6 numbers derived from the wrong campaign.

### Where

- `docs/run_sheet.md:93` — commented-example query line (the pattern shown to users as the "canonical" way).
- `docs/run_sheet.md:97` — `$LONGITUDINAL_RUN_ID` discovery (identical SQL).
- `docs/run_sheet.md:102` — `$SHIFT_RUN_ID` discovery (identical SQL against a different DB).
- All three pipe into rows 18/19 (`docs/run_sheet.md:119-120`) as `--run-id $LONGITUDINAL_RUN_ID`/`$SHIFT_RUN_ID`.

The bug fires on each of the three SQL call sites independently. Fixing any one without the others leaves the remaining sites broken.

### Reproduction

**Deterministic repro** (reviewer-confirmed against current DuckDB):

```sql
-- Seed three rows, TWO of which share run_id='run_2' with different timestamps:
INSERT INTO evaluation_runs (run_id, timestamp, ...) VALUES
    ('run_1', '2026-03-07 17:29:00', ...),
    ('run_2', '2026-02-15 19:11:00', ...),  -- earlier row of run_2
    ('run_2', '2026-04-21 07:24:00', ...);  -- LATEST row overall; belongs to run_2

-- Run-sheet query:
SELECT DISTINCT run_id FROM evaluation_runs ORDER BY timestamp DESC LIMIT 1;
-- Result:   'run_1'       ← WRONG
-- Correct:  'run_2'       (its 2026-04-21 row is the true max-timestamp)
```

**Why this happens in DuckDB.** `SELECT DISTINCT run_id` deduplicates to `{run_1, run_2}`. The ORDER BY references `timestamp` which is NOT in the SELECT list. After DISTINCT, each remaining row needs a timestamp to sort by — DuckDB picks one arbitrarily per run_id from the underlying rows. In this repro, the arbitrary `run_2` representative got the earlier 2026-02-15 timestamp. Sort by that → `run_1` (2026-03-07) beats `run_2` (2026-02-15) → `run_1` returned as "latest."

Scenario fires whenever any run has multiple rows with distinct timestamps — which is the NORMAL case (every task in a longitudinal run writes a row with its own completion timestamp). The "latest" semantic the run sheet wants is ambiguous under DISTINCT.

### Threat model / impact

- **Rows 18/19 silently analyze the wrong longitudinal run.** Chapter 6 Pillar 3 RQ #2/#3 numbers can reference a stale/older run instead of the intended just-finished campaign.
- **No visible signal of misselection.** User sees a run_id, assumes it's the latest, copies numbers into thesis.
- **Compounds with ext7 #49 (DB not isolated).** Accumulated runs increase the probability of the wrong run being picked.
- **Direction of bias: unpredictable** — depends on DuckDB's internal iteration order, which can shift between versions and query plans.

### Remediation

**A. Use GROUP BY to make "max timestamp per run_id" explicit.**

```sql
-- Correct: "give me the run_id whose latest-event timestamp is the greatest"
SELECT run_id
FROM evaluation_runs
GROUP BY run_id
ORDER BY MAX(timestamp) DESC
LIMIT 1;
```

**B. Or: drop DISTINCT; use plain ORDER BY LIMIT 1.**

```sql
-- Correct: "the run_id of the most recent event row"
SELECT run_id
FROM evaluation_runs
ORDER BY timestamp DESC
LIMIT 1;
```

(B) is simpler; works if the most recent row by timestamp is always the latest run's (true for append-only logs). (A) is strictly correct.

**C. Regression test.**

```python
def test_run_sheet_run_id_discovery_returns_latest():
    """Seed 3 runs with decreasing timestamps; the discovery SQL picks the latest."""
    seeded = [("run_oldest", t0), ("run_middle", t1), ("run_newest", t2)]
    ...
    result = conn.execute(SAME_SQL_AS_RUN_SHEET).fetchone()[0]
    assert result == "run_newest"
```

Could be a unit test against a controlled synthetic DB.

### Recommendation

**Land before rows 18/19 are re-run.** Two-line SQL edit in `run_sheet.md`. ~5 min.

---

## Issue #112 — `run_sheet.md` Row 12 invokes `backfill_definition_token_costs.py` without `--stores eval`; script defaults to `--stores all` and mutates `data/cli/chroma` too (MEDIUM)

### Summary

`docs/run_sheet.md:113`:

```
| 12 | `uv run scripts/backfill_definition_token_costs.py --write` | ... | ~1 min |
```

The command invokes the backfill without a `--stores` argument. The script's argparse at `scripts/backfill_definition_token_costs.py:135-139`:

```python
parser.add_argument("--stores", ..., default=["all"], ...)
```

And line 143: `targets = list(_STORES.keys()) if "all" in args.stores else args.stores`.

Where `_STORES` at lines 41-42:
```python
_STORES = {
    "cli":  ROOT / "data" / "cli" / "chroma",
    "eval": ROOT / "data" / "eval" / "chroma",
}
```

Default → writes to BOTH `data/cli/chroma` AND `data/eval/chroma`. Running Row 12 as-written during a Phase 5 eval campaign ALSO writes to the user's CLI registry. That's unexpected — Row 12 sits in a Phase 4/5 eval block; users expect eval-scope-only side effects.

### Where

- **Run sheet command (missing `--stores eval`):** `docs/run_sheet.md:113`.
- **Script default (`all`):** `scripts/backfill_definition_token_costs.py:135-143`.
- **Stores definition:** `scripts/backfill_definition_token_costs.py:41-42`.

### Reproduction

```bash
$ uv run scripts/backfill_definition_token_costs.py --write
# Writes to data/cli/chroma AND data/eval/chroma.
# User's CLI capability token-cost metadata is mutated as a side-effect of the eval.
```

### Threat model / impact

- **Phase 5 eval side-effects user's CLI registry.** For someone using `mint run` interactively after the campaign, their CLI registry's `definition_token_cost` values now reflect the eval's backfill pass.
- **Direction of bias: makes CLI registry's token-cost data consistent with eval** — arguably desirable, but silently. User has no signal that their CLI state was modified.
- **Non-reversible without re-running against pre-write snapshot.** Token costs are deterministic (tokenizer output), so re-running regenerates the same values, but if the tokenizer was updated between runs, the CLI registry drifts.

### Remediation

**A. Update run_sheet.md Row 12 command.**

```markdown
| 12 | `uv run scripts/backfill_definition_token_costs.py --write --stores eval` | ... | ~1 min |
```

Explicit scope matches the Phase 5 eval context.

**B. Or: flip the script's default to `eval` only.**

```python
# scripts/backfill_definition_token_costs.py:138 — change:
default=["eval"],
```

Users who want both can pass `--stores all` explicitly; default is safe for eval scope.

### Recommendation

**MEDIUM — land with next run-sheet pass.** Option (A) is a one-line doc edit. ~2 min.

---

## Issue #113 — Longitudinal runners use a hardcoded persistent `metrics_store` path that's never reset; re-runs silently inherit prior invocations' utility scores + prune history (P0 CRITICAL)

### Summary

`ext8 #71`'s remediation fixed "MetricsStore reset per run wipes maintenance_burden" by making the store persistent across pipeline recompiles within a run AND across runs. Both longitudinal runners adopted a fixed persistent DuckDB path:

- `scripts/run_longitudinal.py:156`:
  ```python
  _metrics_store_path = str(DATA_DIR / "eval" / "longitudinal_metrics_store.duckdb")
  metrics_store = MetricsStore(db_path=_metrics_store_path)
  ```
- `scripts/run_longitudinal_shift.py:267`:
  ```python
  _metrics_store_path = str(DATA_DIR / "eval" / "longitudinal_shift_metrics_store.duckdb")
  metrics_store = MetricsStore(db_path=_metrics_store_path)
  ```

The fix correctly enables **within-sequence** state persistence (cycle 5 sees cycle 4's invocations — ext8 #62's intent). But the fix path is **never reset between script invocations**. A second `uv run scripts/run_longitudinal.py` reads the prior invocation's accumulated utility scores, invocation counts, and prune events from the same DuckDB file.

Downstream consequences:
- **`pipeline.py:167`** — `previous_utility_scores = metrics_store.load_previous_utility_scores()` seeds the first cycle's KL comparison with PRIOR run's final utility state. New run's "cycle 1 KL" is meaningless (compares fresh distribution against another run's converged distribution).
- **`operations.py:699`** — `count = metrics_store.invocations_since(cap.name, cutoff)` uses persisted invocation history. A capability invoked in the PRIOR run but not in the current one still counts as invoked for the prune threshold. Prune decisions flip.
- **Split/merge candidate selection** (same file) reads persistent reuse_rate / success_rate. Cross-run contamination of these metrics changes which capabilities get split or merged in the new run.

Net effect: two back-to-back longitudinal runs with the same seed produce DIFFERENT optimizer behavior. Chapter 6 Pillar 3 RQ #2/#3 conclusions depend on the optimizer's decisions being reproducible; this breaks that reproducibility.

### Where

- **Hardcoded persistent path (run_longitudinal.py):** `scripts/run_longitudinal.py:152-157` — comment at line 152 cites ext8 #71 as the authority.
- **Hardcoded persistent path (run_longitudinal_shift.py):** `scripts/run_longitudinal_shift.py:265-270` — same ext8 #71 reference.
- **Downstream readers of persisted state:**
  - `src/orchestrator/pipeline.py:167` — `metrics_store.load_previous_utility_scores()` for KL baseline.
  - `src/optimizer/operations.py:699` — `metrics_store.invocations_since(cap.name, cutoff)` for prune threshold.
  - `src/optimizer/operations.py` (split/merge candidate helpers, various) — read reuse_rate / success_rate from the same store.

### Reproduction

User's deterministic repro:

```bash
# Day 1 — first longitudinal run.
$ uv run scripts/run_longitudinal.py --seed 42
# data/eval/longitudinal_metrics_store.duckdb now contains ~40 cycles' worth
# of invocation records, utility scores, cycle events (including prune events).

# Day 2 — rerun with fresh intent.
$ uv run scripts/run_longitudinal.py --seed 42
# metrics_store = MetricsStore(db_path=same file) — LOADS Day 1's data.
# First-cycle optimizer:
#   - load_previous_utility_scores() returns Day 1's FINAL scores (~40 caps' worth)
#   - Not an empty dict; Day 2's cycle 1 KL computes against Day 1's converged state.
#   - prune: invocations_since(cap_x, cutoff) returns COMBINED Day 1 + Day 2 counts.
#     A cap pruned in Day 1 re-surfaces if it had invocations before the cutoff window.
```

User reported: "pruned in clean baseline? True, pruned with stale store? False" — exactly this scenario.

### Why this is a fix-induced regression (not a new bug)

- **Pre-ext8-#71:** MetricsStore was `:memory:` by default. Within-run state was lost on pipeline recompile (ext8 #62 symptom). Between-run state was naturally isolated because in-memory DBs don't persist.
- **Ext8 #71 fix:** Switch to persistent DB path for within-run persistence. **Did not address between-run isolation** — the assumption was one longitudinal invocation = one campaign.
- **Reality:** users re-run `run_longitudinal.py` for debugging, variance estimation, or campaign restarts. Every re-run inherits stale state.

Same pattern as ext11 #109/#110 (regressions against ext8 #78) and ext9 #86 (regression against ext6 #31). Each audit fix introduces a non-zero chance of unintended state-coupling.

### Threat model / impact

- **Chapter 6 Pillar 3 RQs #2 and #3 silently non-reproducible.** Same seed, same script, different numbers — because Day-N's state depends on Day-(N-1)'s.
- **Optimizer prune/split/merge decisions contaminated.** A cap's "reuse rate" reflects combined-history, not current-campaign.
- **Step 18.8 shift-adaptation verdict unreliable.** The adaptation measurement compares Phase A utility (early cycles) to Phase B utility (late cycles). If Phase A starts with Day-1's converged state pre-loaded, the "Phase A → Phase B shift" signal is swamped by the stale pre-load.
- **Direction of bias: makes convergence look faster than it is.** Day 2 starts "already converged" by Day 1 standards → Day 2's KL drops to near-zero by cycle 2 → misreported as "fast convergence."
- **Cross-run prune decisions flip.** User's observed "pruned: True → False" exactly — a cap that would be pruned in a clean run is NOT pruned with stale state.

### Remediation

**A. Reset the metrics_store DB at the start of every invocation.**

```python
# scripts/run_longitudinal.py:152-157 — before:
_metrics_store_path = str(DATA_DIR / "eval" / "longitudinal_metrics_store.duckdb")
metrics_store = MetricsStore(db_path=_metrics_store_path)

# after:
_metrics_store_path = Path(DATA_DIR / "eval" / "longitudinal_metrics_store.duckdb")
# Explicit reset at invocation start — each run gets a clean baseline.
if _metrics_store_path.exists():
    _metrics_store_path.unlink()
metrics_store = MetricsStore(db_path=str(_metrics_store_path))
```

Apply same change at `run_longitudinal_shift.py:265-270`.

Within-run persistence (ext8 #71's actual intent) is preserved because the DB is created fresh on every invocation and populated as the invocation proceeds.

**B. Or: per-invocation timestamped paths.**

```python
_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
_metrics_store_path = str(DATA_DIR / "eval" / f"longitudinal_metrics_store_{_ts}.duckdb")
metrics_store = MetricsStore(db_path=_metrics_store_path)
```

Natural isolation without explicit `unlink`. Leaves historical stores for forensic inspection. Trades clean cleanup for audit trail.

**C. Or: `--reuse-metrics-store` CLI flag (off by default).**

For users who genuinely want cross-run state accumulation (unusual), make it opt-in:

```python
parser.add_argument(
    "--reuse-metrics-store", action="store_true",
    help="Inherit prior invocation's metrics_store state. Default: fresh DB per run.",
)
...
if not args.reuse_metrics_store and _metrics_store_path.exists():
    _metrics_store_path.unlink()
```

**D. Regression test.**

```python
def test_longitudinal_metrics_store_isolated_across_invocations(tmp_path):
    """Two back-to-back invocations with same seed must produce same cycle-1 state."""
    # Invocation 1: run a short longitudinal sequence. Record cycle-1 KL.
    # Invocation 2: re-run with same seed. Cycle-1 KL must match.
    # Pre-fix: invocation 2's cycle-1 KL is anomalously low (compares against
    #   invocation 1's converged state).
    ...
```

### Recommendation

**P0 — blocking reproducibility of any longitudinal Chapter 6 data.** The reset is a ~3-line code change per runner + 1 regression test. ~20 min total. Must ship before Chapter 6 Pillar 3 RQ #2/#3 results are considered final.

### Legacy data

Any longitudinal DB produced pre-fix reflects cumulative state across invocations. Options:
- (a) Disclose in Chapter 4.5: "longitudinal results computed from the first invocation after a clean `data/eval/` state; subsequent invocations were verification-only and used for error-bar estimation." Practical for thesis submission if only ONE canonical invocation fed Chapter 6.
- (b) Re-run post-fix against a fresh state. Costs ~6-10h per longitudinal + ~6-10h per shift, manageable.

(b) is cleaner; (a) is the escape hatch if re-running is infeasible.

---

## Already-logged findings (reminders in submission)

### User's reminder: "Row 17 is still affected by open P0s from ext11 (#109, #110)"

Confirmed. Both ext11 #109 and #110 remain open in the current tip; see this extension's main sections for detail + remediation. Row 17 is blocked for campaign finalization until those two land.

### User's reminder: "Rows 9/10 still call scripts that wipe `src/mcp_tools`"

Confirmed. `run_longitudinal.py:137` and `run_longitudinal_shift.py:252` both include `MCP_TOOLS_DIR` (= `ROOT / "src" / "mcp_tools"`) in the rmtree loop. This is **ext9 #86** which was logged as P0 and confirmed **STILL OPEN** during the ext8 verification pass. User's additional observation that **the run sheet doesn't warn** that rows 9/10 can delete repo files is a legitimate procedural polish item — the run sheet should explicitly note "rows 9/10 delete `src/mcp_tools/` contents; run `git checkout src/mcp_tools/` after each longitudinal campaign" OR fix ext9 #86 at the code level (remove `MCP_TOOLS_DIR` from the rmtree loop).

Bundle fix recommendation: land ext9 #86's remediation (~2 line code change) rather than adding a run-sheet warning. Warning a user about a destructive default is weaker than fixing the destructive default.

---

## Relationship to ext6-ext10 findings

ext11's five findings span several classes:

| ext11 # | Root cause class | Prior-fix relationship |
|---|---|---|
| 109 | **Fix-induced scope leak** | ext8 #78 added the prune-exclusion LEFT JOIN but didn't scope the subquery by run_id |
| 110 | **Fix-induced schema coupling** | ext8 #78's LEFT JOIN and the pre-existing `_resolve_scope(since)` function work against different tables but share `_SCOPE_SQL` plumbing |
| 111 | **SQL semantics misuse** | `SELECT DISTINCT ... ORDER BY non-selected-column` produces undefined row selection in DuckDB |
| 112 | **Procedure-vs-default scope mismatch** | Run sheet command uses script's default scope; default is broader than the run sheet's Phase 5 context |
| 113 | **Fix-induced cross-invocation state bleed** | ext8 #71 made MetricsStore persistent for within-run state; didn't address between-run isolation |

**#109, #110, and #113 together are the second-third-fourth occurrences of the "fix-induced bug" pattern** (ext9 #86 was the first — ext6 #31's rmtree over-reached into `src/mcp_tools`). Four instances across 5 audit extensions strongly supports flagging this pattern in Chapter 4.5 methodology: each remediation introduces a non-zero regression chance; regression tests must cover BOTH the original bug AND the surfaces the fix touched. Specifically, regression tests should verify:
- The original bug is gone (the easy part — usually asserted)
- The fix doesn't over-reach into unintended scopes (#86: into project source; #109: into cross-run data; #113: into cross-invocation state)
- The fix's plumbing remains internally consistent with related code (#110: column-name mismatch between `_resolve_scope` and `invocation_events`)

Guard patterns added:

- **Subquery scope must match outer query scope** (#109): when adding a LEFT JOIN whose subquery reads from a related table, the subquery should share the outer query's scope predicates (run_id, since, is_pilot). A lint would check: for any JOIN subquery, does it carry the same WHERE filters as the outer query?
- **Table-aware scope plumbing** (#110): functions that return SQL fragments should be table-aware when column names differ between tables. Either return per-table dicts or abstract the column-name mapping at the formation site.
- **DISTINCT + ORDER-BY-non-selected-column is undefined** (#111): any SQL with `SELECT DISTINCT X ... ORDER BY Y` where Y is not in the select list is a semantic hazard. Prefer explicit `GROUP BY X ORDER BY MAX(Y)` or drop DISTINCT if uniqueness is already implied by LIMIT 1.
- **Procedure-default-scope alignment** (#112): run-sheet commands should use explicit scope flags even when a script's default matches the common case; a default safe for one context is rarely safe for all contexts.
- **Persistent-store reset on invocation boundary** (#113): any store that benefits from within-run persistence should explicitly decide its inter-run behavior. Default choice: fresh per invocation (with explicit opt-in flag for cross-invocation accumulation). Within-run persistence doesn't imply cross-run persistence is desired.

---

## Parent-document integration (pending)

When ext11 lands, fold forward into `security_audit_2026_04_19.md`:

1. Update header: "One hundred eight findings → One hundred thirteen findings; #56-#113 open as of 2026-04-20."
2. Insert TL;DR rows #109-#113 after ext10's #108.
3. Tranche 3 (eval-blocking) gains **four P0** (#109, #110, #111, #113) — #109/#110 block Row 17 (Chapter 6 Pillar 3 M3 Gini); #111 blocks Rows 18/19 (longitudinal analysis); #113 blocks reproducibility of Rows 9/10 (longitudinal campaign itself).
4. Extend "Behavioral regressions expected":
   - (#109) Prune subquery is correlated on `(capability_id, run_id)`; cross-run bleed eliminated.
   - (#110) `_resolve_scope` returns per-table fragments; `--since` scope uses `occurred_at` for `invocation_events` / `cycle_events`.
   - (#111) Run-sheet run-id discovery SQL rewritten as `SELECT run_id FROM evaluation_runs GROUP BY run_id ORDER BY MAX(timestamp) DESC LIMIT 1` (or equivalent non-DISTINCT form).
   - (#112) Run sheet Row 12 passes `--stores eval` explicitly.
   - (#113) Longitudinal runners reset metrics_store DB at invocation start (or use per-invocation timestamped paths); `--reuse-metrics-store` flag available for explicit cross-invocation accumulation.
5. End-of-audit note: "Extended 2026-04-20 (eleventh extension) with findings #109-#113 (detail sections in `security_audit_2026_04_19_ext11.md`). #109 + #110 + #113 are P0 fix-induced regressions (against ext8 #78 / ext8 #78 / ext8 #71 respectively); #111 is a SQL semantics misuse; #112 is a procedure-default-scope mismatch. Fix-induced bug pattern now has four instances across the series — strong signal for Chapter 4.5 methodology discussion."

---

**End of extension.**
