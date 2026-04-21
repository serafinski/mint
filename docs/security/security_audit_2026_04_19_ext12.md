# Security / Robustness Audit — 2026-04-19, Twelfth Extension (#114-#123)

**Parent document:** [`security_audit_2026_04_19.md`](./security_audit_2026_04_19.md) — #1-#18 all ✅ FIXED 2026-04-20.
**Sibling extensions:**
- [`security_audit_2026_04_19_ext4.md`](./security_audit_2026_04_19_ext4.md) — #12-#14 ✅ (folded into parent).
- [`security_audit_2026_04_19_ext5.md`](./security_audit_2026_04_19_ext5.md) — #15-#18 ✅ (folded into parent).
- [`security_audit_2026_04_19_ext6.md`](./security_audit_2026_04_19_ext6.md) — #19-#43 ✅ all FIXED 2026-04-20.
- [`security_audit_2026_04_19_ext7.md`](./security_audit_2026_04_19_ext7.md) — #44-#55 ✅ all FIXED 2026-04-20.
- [`security_audit_2026_04_19_ext8.md`](./security_audit_2026_04_19_ext8.md) — #56-#78 (21 ✅ fixed, 2 open: #61 MEDIUM latent, #63 P0).
- [`security_audit_2026_04_19_ext9.md`](./security_audit_2026_04_19_ext9.md) — #79-#97 (#86 still P0 open per verification; others pending audit).
- [`security_audit_2026_04_19_ext10.md`](./security_audit_2026_04_19_ext10.md) — #98-#108 open.
- [`security_audit_2026_04_19_ext11.md`](./security_audit_2026_04_19_ext11.md) — #109-#113 ✅ all FIXED 2026-04-21.

**This extension's scope.** Six findings surfaced 2026-04-21 via reviewer-run direct reproduction. All six are silent-wrong-answer / silent-success bugs that corrupt the Phase-5 eval pipeline's trust contracts:

- **#114** is a regression against ext7 #46's own remediation — the per-condition cap was supposed to pick "N run_ids per condition," but ranks rows instead of distinct run_ids, so the canonical eval shape collapses to ~1 run_id per condition while the warning text still claims 5. Row 17's Chapter-6 aggregates silently compute on a tiny subset if the user forgets `--run-ids`.
- **#115** is an independent bug in `run_pilot.py`'s watchdog-abort path — on `BudgetExceeded` the loop breaks, the final summary reports `Total records: 0`, and `main()` returns `None`. Since `__main__` doesn't wrap `main()` in `sys.exit(...)`, the process exits 0. An unattended row 8 can "succeed" with no data, and downstream pipeline steps proceed on empty input.
- **#116** — `analyze_longitudinal.py` accepts any non-empty longitudinal DB as valid and returns exit 0 whenever `_detect_convergence` finds a convergence cycle. 5-cycle DBs pass as "converged" with no expected-cycle completeness check.
- **#117** — the run-sheet `LONGITUDINAL_RUN_ID` / `SHIFT_RUN_ID` selector picks the latest run by `MAX(timestamp)` only. A partial/aborted 5-row run written AFTER a complete 40-row run wins the selector; rows 18/19 then analyze the stub and exit 0.
- **#118** — the KL helper returns 0.0 when both input distributions are empty, and the convergence detector consumes that as a valid sub-threshold signal. A 40-cycle longitudinal DB with ONLY `evaluation_runs` rows (no invocation/utility/event signal) reports "Cycles-to-convergence: 2, n_caps=0, exit 0."
- **#119** — `run_longitudinal.py` and `run_longitudinal_shift.py` have no campaign-level watchdog. A single hanging task (Docker stall, LangFuse API stall, DeepAgent recursion plateau) blocks the rows 9/10 campaigns indefinitely with no abort signal; `run_pilot.py` has `_condition_budget_watchdog` for the same risk but the longitudinal drivers inherited none of it.

**Status at time of write (2026-04-21):** all six **open**. Total open across audit series: **~62 findings** (ext8 + ext9 + ext10 + ext12).

| # | Severity | Scope | Impact |
|---|---|---|---|
| 114 | **P0 CRITICAL** | `scripts/analyze_results.py::_resolve_scope` auto-select CTE | Row-level `ROW_NUMBER()` + `rn <= 5` → DISTINCT collapses to ~1 run_id per condition on canonical 20-row/run shape; Chapter 6 aggregates silently use a tiny subset when `--run-ids` is absent |
| 115 | **P0 CRITICAL** | `scripts/run_pilot.py` watchdog abort + no `sys.exit` wrapper on `main()` | `BudgetExceeded` → break → final summary `Total records: 0` → `main()` returns None → process exit 0. Unattended row 8 "succeeds" with partial/empty campaign; downstream analyzers proceed on empty data |
| 116 | **P0** | `scripts/analyze_longitudinal.py::main` missing completeness guard | Only rejects empty DB (line 659); no expected-cycle / min-cycle check; returns exit 0 whenever `_detect_convergence` returns non-None. 5-cycle stub DB passes as "converged, exit 0" |
| 117 | **P0** | `docs/run_sheet.md:99, 103` latest-run selector has no completeness criterion | `GROUP BY run_id ORDER BY MAX(timestamp) DESC LIMIT 1` picks the newest run even if it's a 5-row abort. Rows 18/19 then read the stub and exit 0 |
| 118 | **P0** | `analyze_longitudinal.py::_compute_kl` empty-vs-empty returns 0.0; consumed as valid signal | Lines 112-117 return 0.0 for empty-vs-empty; lines 731-737 treat sub-threshold KL as convergence. 40-cycle DB with no invocation/utility/event rows reports Cycles-to-convergence: 2, n_caps=0, exit 0 |
| 119 | **HIGH** (run-sheet blocker) | `scripts/run_longitudinal.py:233-306` + `scripts/run_longitudinal_shift.py:328-399` missing campaign watchdog | No SIGALRM budget, no per-task timeout wrapper. A hanging Docker container / stalled LangFuse API / DeepAgent recursion plateau blocks rows 9/10 indefinitely with no abort signal. `run_pilot.py` has `_condition_budget_watchdog`; longitudinal drivers don't |
| 120 | **P0 CRITICAL** | `scripts/analyze_context_utilization.py::fetch_observation` swallows all exceptions → `extract_trace_context` returns a zero-metrics dict → averages pool failed-fetch traces as 0% util | One good trace at 50% util + one all-failed-fetch trace at 0% util → reported average = 25% instead of 50%; exit 0. Silent deflation of Row 13's Pillar 3 Context Economy numbers on LangFuse transient failures |
| 121 | **P0** | `scripts/audit_contamination.py::_sample_plan_path` keys plan file on DB path stem only; ignores `run_ids` / `since` / `conditions` / `types` / `per_stratum` / `seed` | Plan persisted by a prior broader/different-scope audit run overrides a new scoped run. Intersection with new scoped pool can be empty → "Nothing to audit" path → exit 0 with no M2 labels produced. Row 17 then consumes whatever labels exist (or nothing) as Chapter-6 M2 input |
| 122 | **P0 CRITICAL** | `scripts/backfill_token_costs.py::main()` loud-fail is ALL-or-nothing; per-run zero-generation runs write zero token_cost + exit 0 | `sys.exit(2)` fires only when `fetch_all_generations` is globally empty (line 736). Per-run missing generations yield `n_generations=0, total_tokens=0` silently; `compute_backfill_values` writes 0 tokens for every record in those runs; script prints `Done.` and exits 0. Row 11 can silently zero whole non-static runs (M4 under-reported in Row 17) |
| 123 | **P0** | `scripts/run_longitudinal_shift.py:508-511` distribution-shift marker write wrapped in broad except; `scripts/analyze_longitudinal.py:762-763` silently skips RQ #3 block when marker absent | Shift-marker write failure logs a warning and proceeds. Analyzer gates its Distribution-Shift Analysis on `_detect_shift_point` returning non-None; when marker never landed, the section is silently omitted and `main()` still returns 0 via the convergence branch. Row 19 reports "success" with zero RQ #3 evidence |

---

## Issue #114 — `analyze_results.py` auto-scope CTE ranks rows, not distinct run_ids; per-condition cap silently under-selects (P0 CRITICAL)

### Summary

Ext7 #46 was supposed to replace the "4 most recent total run_ids" auto-select with a balanced per-condition cap (default 5 per condition). The remediation at `scripts/analyze_results.py:219-235` uses a CTE with `ROW_NUMBER() OVER (PARTITION BY condition ORDER BY timestamp DESC)` on `evaluation_runs` — but `evaluation_runs` has ONE ROW PER TASK, not one row per run. On the canonical eval shape (20 tasks × 5 runs × 4 conditions = 400 rows, 20 distinct run_ids), the top 5 rows per condition are nearly always the 5 most recent rows of the single most recent run. After `SELECT DISTINCT run_id, condition` collapses duplicates, the result is ~1 run_id per condition, not 5.

The `WARNING` text emitted at line 252-258 still claims "auto-selecting the 5 most recent per condition" — the warning lies about what actually happened, and the `_SCOPE_SQL` filter silently restricts Chapter-6 aggregates to that tiny slice.

### Where

- **Broken CTE:** `scripts/analyze_results.py:219-235`:
  ```python
  selected_rows = conn.execute(
      """
      WITH ranked AS (
          SELECT run_id, condition, timestamp,
                 ROW_NUMBER() OVER (
                     PARTITION BY condition ORDER BY timestamp DESC
                 ) AS rn
          FROM evaluation_runs
          WHERE is_pilot = FALSE
      )
      SELECT DISTINCT run_id, condition
      FROM ranked
      WHERE rn <= ?
      ORDER BY condition, run_id
      """,
      [per_condition_cap],
  ).fetchall()
  ```
  `ROW_NUMBER()` assigns 1..N over the ROWS of `evaluation_runs` per condition (not per run_id). With 20 tasks per run, `rn <= 5` picks 5 rows from the single most recent run, then DISTINCT collapses to 1 run_id.
- **Warning text that drifts from behavior:** `scripts/analyze_results.py:252-258`.

### Reproduction

Reviewer-confirmed on canonical synthetic shape:

```sql
-- Seed: 4 conditions × 5 runs × 20 rows/run (= 400 rows, 20 distinct run_ids)
INSERT INTO evaluation_runs (run_id, condition, task_id, timestamp, is_pilot) VALUES
    ('full_00', 'full', 'T01', '2026-04-21 00:00:00', FALSE),
    ('full_00', 'full', 'T02', '2026-04-21 00:00:01', FALSE),
    ... (20 rows for full_00, then 20 for full_01 .. full_04, same pattern for the other 3 conditions)

-- Run _resolve_scope() without --run-ids:
-- Expected: 5 run_ids per condition × 4 = 20 selected.
-- Actual:   4 run_ids (*_04 only) — one per condition.
```

Reviewer's repro output: "returned only 4 run_ids (*_04), while warning still says '5 per condition'."

### Why ext7 #46's test didn't catch this

Ext7 #46's regression test `test_auto_select_covers_all_conditions_in_canonical_eval_shape` seeded ONE row per run_id (20 rows total for 20 run_ids). At that row density, `rn <= 5` correctly picks 5 distinct run_ids per condition because each row IS a distinct run_id. Real evaluation_runs has 20 rows per run (one per task), which the test didn't model. Classic "test fixture doesn't match production shape" — same failure class as parent #19 (sandbox-contract mismatch in behavioral tests).

### Threat model / impact

- **Chapter 6 tables silently compute on ~5% of the intended data** when user invokes row 17 without `--run-ids`. The warning message reassures them the scope is correct.
- **Direction of bias: unpredictable** — depends on which single run_id survives DISTINCT per condition. Typically the temporally-last run, which may or may not be representative.
- **Compounds with ext7 #46's own fix claim.** #46 explicitly promised "balanced per condition"; the fix's behavior is the opposite of its docstring.
- **Mostly-masked by ext7 #52 completeness guard.** #52 would catch the under-count IF the expected count is declared (`--expected-runs-per-condition 5`). If the user forgets to pass expected shape, under-count is silent.
- **Blocks unattended Phase 5 eval.** Row 17 is the canonical Chapter-6 aggregation step; silent subset corrupts the final thesis numbers.

### Remediation

**A. Rewrite the CTE to rank distinct run_ids, not rows.**

```python
selected_rows = conn.execute(
    """
    WITH distinct_runs AS (
        SELECT run_id, condition, MAX(timestamp) AS latest_ts
        FROM evaluation_runs
        WHERE is_pilot = FALSE
        GROUP BY run_id, condition
    ),
    ranked AS (
        SELECT run_id, condition, latest_ts,
               ROW_NUMBER() OVER (
                   PARTITION BY condition ORDER BY latest_ts DESC
               ) AS rn
        FROM distinct_runs
    )
    SELECT run_id, condition
    FROM ranked
    WHERE rn <= ?
    ORDER BY condition, run_id
    """,
    [per_condition_cap],
).fetchall()
```

The inner `distinct_runs` CTE collapses to one row per (run_id, condition) pair BEFORE ranking. `ROW_NUMBER()` now ranks distinct run_ids. `rn <= 5` correctly picks 5 distinct run_ids per condition. DISTINCT is no longer needed because each row is already unique by `run_id`.

**B. Strengthen the regression test to match production row density.**

```python
def test_auto_select_picks_n_distinct_run_ids_per_condition_at_production_density(tmp_path):
    """Canonical eval shape: 20 tasks per run × 5 runs × 4 conditions = 400 rows, 20 run_ids.

    Pre-fix: rn <= 5 picks 5 rows per condition → DISTINCT collapses to 1 run_id/condition → 4 total.
    Post-fix: distinct_runs CTE ranks run_ids first → 5 run_ids per condition → 20 total.
    """
    db = _seed_full_eval_shape(tmp_path, conditions=4, runs_per_condition=5, tasks_per_run=20)
    scope = _resolve_scope(db, run_ids=None, since=None, per_condition_cap=5)
    selected = _parse_scope_run_ids(scope)
    assert len(selected) == 20, f"Expected 20 run_ids (5 per × 4 conditions), got {len(selected)}"
    # Per-condition balance:
    by_condition = _group_by_condition(db, selected)
    for cond, ids in by_condition.items():
        assert len(ids) == 5, f"Condition {cond}: expected 5 run_ids, got {len(ids)}"
```

**C. Warning text reality-check.**

Make the warning read from the actual selected count, not the parameter:

```python
print(
    f"WARNING: DB contains {total_available} distinct non-pilot run_ids; "
    f"auto-selected {len(latest)} total across {len(selected_conditions)} conditions "
    f"(target cap: {per_condition_cap} per condition). "
    f"Pass --run-ids to override.",
    file=_sys.stderr,
)
```

If the actual selection is short of the cap (DB has fewer than cap runs for some condition), the message reflects what actually happened rather than what was asked for.

### Recommendation

**P0 — blocks Row 17 / Chapter 6 aggregation.** ~15 LOC SQL rewrite + 1 production-density regression test + 1 warning-text edit. ~30 min total. Must ship before the 400-record Phase 5 eval's analysis pass.

### Legacy data

Existing Chapter 6 numbers computed pre-fix on multi-run DBs without explicit `--run-ids` used a silently-small subset. Re-run `analyze_results.py` post-fix against the same DB → corrected numbers. No data migration needed; the fix changes only query formation.

---

## Issue #115 — `run_pilot.py` watchdog-abort path returns success (exit 0) with partial/empty campaign (P0 CRITICAL)

### Summary

Ext10 #103's remediation added a SIGALRM-based watchdog around `harness.run_condition(...)` that raises `BudgetExceeded` on timeout. The except-handler logs the abort and `break`s the outer condition loop so remaining conditions are skipped. But:

1. The final-summary block at `scripts/run_pilot.py:385-400` then runs normally on whatever `all_records` has accumulated (which may be 0 if the first condition hit the watchdog before producing any complete runs).
2. `main()` has no `return`, no `sys.exit(...)`, and no `raise`. It returns `None` by falling off the end.
3. The module entrypoint at line 414-415 is bare:
   ```python
   if __name__ == "__main__":
       main()
   ```
   No `sys.exit(main())`. Python's default exit code for a normal completion is 0.

Net effect: a watchdog abort is indistinguishable from a successful run at the process-exit layer. Row 8 of the run sheet ("run the pilot campaign") can report "SUCCESS" to a shell orchestrator, upstream CI, or a downstream analyzer script while having produced `Total records: 0`.

### Where

- **Except-handler + break:** `scripts/run_pilot.py:340-352`.
- **Final summary runs unconditionally on `all_records`:** `scripts/run_pilot.py:385-400`.
- **`main()` has no `sys.exit(...)`:** `scripts/run_pilot.py:414-415`:
  ```python
  if __name__ == "__main__":
      main()
  ```
- **No early-return on abort flag:** the break only exits the condition loop; control falls through to the final summary and the LangFuse flush, then to natural function return.

### Reproduction

Reviewer-ran stub-harness repro:

```python
# Patch harness.run_condition to raise BudgetExceeded.
# Run: uv run scripts/run_pilot.py --n-runs 5
# Observed stderr:
#   ERROR  Condition 'full' aborted by watchdog: budget exceeded...
#          Skipping the remaining conditions.
# Observed stdout:
#   Total records: 0
# Observed exit code:
#   $ echo $?
#   0         ← WRONG; expected non-zero exit for watchdog abort
```

MAIN_RETURN is None; Python translates None-return to exit 0.

### Threat model / impact

- **Silent campaign corruption.** Row 8 of the run sheet is typically unattended (overnight); a watchdog abort produces 0 records, the analyzer (row 14/16/17) then runs on an empty DB, Chapter 6 tables show `n=0` for the relevant condition — or the completeness guard (ext7 #52) catches it ONLY IF the user passed `--expected-*` flags. Without those flags, the downstream analysis silently reports "no data found" or, worse, aggregates whatever pre-existing legacy rows happen to be in the shared DB.
- **CI / orchestration false-positive.** Any wrapper that inspects `$?` to decide "should I run the next step?" gets a lying answer. Scheduled campaigns (cron, `scripts/run_longitudinal.py` driver) would proceed as if the pilot succeeded.
- **Direction of bias: toward silent-failure.** The thesis-critical invariant "a run's exit code reflects whether it produced valid data" is violated.
- **Compounds with ext7 #49 (DB isolation).** If the user opted for `--db legacy` (shared DB), empty-abort runs don't even leave a trace that a run was attempted — they just don't append rows.
- **Compounds with ext10 #103 disposition.** #103's stated remediation was "log and skip to preserve partial records" — that's correct intent for the data side, but the exit-code side was never addressed.

### Remediation

**A. Track abort state and propagate as non-zero exit.**

```python
# scripts/run_pilot.py — add a flag in main() near the top:
watchdog_aborted = False
abort_condition: str | None = None

# In the except-handler (line 340):
except BudgetExceeded as exc:
    logger.error(
        "Condition '%s' aborted by watchdog: %s. "
        "Partial per-task records ARE persisted via record_run "
        "inside _execute_run; downstream analyzers will see them. "
        "Skipping the remaining conditions.",
        condition, exc,
    )
    watchdog_aborted = True
    abort_condition = condition
    break

# After the final-summary block (around line 400):
if watchdog_aborted:
    logger.error(
        "Campaign aborted during condition '%s' by watchdog. "
        "Partial data persisted; exiting with code 3 so orchestrators can detect the abort.",
        abort_condition,
    )
    return 3  # distinct from argparse-error (2), gate-failure (4), etc.

return 0
```

**B. Wrap the entrypoint with `sys.exit(main())`.**

```python
# scripts/run_pilot.py:414 — before:
if __name__ == "__main__":
    main()

# after:
if __name__ == "__main__":
    sys.exit(main() or 0)
```

Guarantees any integer returned by `main()` becomes the process exit code. `main() or 0` handles the None-return-from-happy-path case.

**C. Add an exit-code-3 sibling to the CLI exit-code table.**

`CLAUDE.md`'s "Three-layer strict-gate pattern" table already enumerates exit codes (2/3/4/5/6). Add:

| Layer | Code | Sticky flag | Max attempts | CLI exit |
|---|---|---|---|---|
| Pillar 5 eval campaign — watchdog abort | `scripts/run_pilot.py` | — (runner-level) | — (single attempt) | **7** (was placeholder) |

Using 7 rather than 3 keeps `run_pilot.py`'s exit codes in a distinct range from the `mint` CLI's; update the number above once the owner picks the canonical value.

**D. Empty-records loud-fail even without watchdog.**

If `all_records` is 0 for reasons unrelated to the watchdog (e.g., no tasks matched the filter), the current code silently prints `Total records: 0` and exits 0. Extend the end-of-main check:

```python
if len(all_records) == 0 and not watchdog_aborted:
    logger.error(
        "Campaign produced zero records and no watchdog abort was recorded. "
        "Likely causes: empty --tasks filter, DB misconfiguration, harness setup error. "
        "Exiting with code 4."
    )
    return 4
```

**E. Regression test.**

```python
def test_run_pilot_exits_nonzero_on_watchdog_abort(tmp_path, monkeypatch):
    """Patch harness to raise BudgetExceeded; assert main() returns non-zero."""
    monkeypatch.setattr(
        "scripts.run_pilot.harness.run_condition",
        Mock(side_effect=BudgetExceeded("stub")),
    )
    # run main() in-process (or via subprocess.run) and capture exit code
    rc = run_pilot.main()
    assert rc != 0, "main() returned 0 on watchdog-aborted campaign"

def test_run_pilot_exits_nonzero_on_empty_records_no_abort(tmp_path, monkeypatch):
    """Harness returns [] with no watchdog abort → exit code 4."""
    monkeypatch.setattr(
        "scripts.run_pilot.harness.run_condition",
        Mock(return_value=[]),
    )
    rc = run_pilot.main()
    assert rc == 4
```

### Recommendation

**P0 — blocks unattended Phase 5 eval trustworthiness.** ~25 LOC in `run_pilot.py` + ~15 LOC across 2 regression tests. ~45 min total. Must ship before the 400-record Phase 5 eval if any step of the run-sheet pipeline interprets the pilot runner's exit code.

### Legacy data

Not applicable — the fix changes exit-code semantics only. Existing campaign DBs are untouched.

---

## Issue #116 — `analyze_longitudinal.py` accepts partial longitudinal runs as valid Chapter-6 output; exit 0 on any non-empty run (P0)

### Summary

`scripts/analyze_longitudinal.py::main()` has a single completeness check at line 659:

```python
if not cycles:
    print(f"ERROR: no longitudinal rows in {db_path}")
    print(f"       (evaluation_runs has no rows with condition='longitudinal')")
    conn.close()
    return 1
```

That's the only gate before the analyzer proceeds to compute per-cycle KL, Gini, convergence, and (optionally) the shift-adaptation verdict. There is no minimum-cycle guard, no `--expected-cycles` flag, no check that the number of cycles matches what the user's `run_longitudinal.py --n-tasks` would have produced. A partial DB with 5 cycles (e.g., from an aborted 40-cycle campaign) passes the `if not cycles` check and is analyzed as if it were a complete run.

The exit-code contract at line 993 compounds the silent-success shape:

```python
return 0 if convergence_cycle is not None else 2
```

Any cycle count ≥ `args.min_consecutive + 1` that happens to contain a `kl < threshold` streak is reported as "converged" and exits 0. `_detect_convergence` only needs `min_consecutive=3` sub-threshold cycles by default — a 5-cycle stub with two cycles of sub-threshold KL (trivially reachable from `kls[1:]` where cycle-1 KL is 0.0 by the empty-prior sentinel) can report convergence at cycle 2.

Reviewer-run repro matches: "5-cycle DB with valid activity → analyzer printed `Total cycles: 5, Cycles-to-convergence: 2, exit 0`." The analyzer treats a 5-cycle stub as a successful 40-cycle campaign.

### Where

- **Only-empty-is-rejected gate:** `scripts/analyze_longitudinal.py:659-663`.
- **`main()` entry point — no completeness param:** `scripts/analyze_longitudinal.py:621-643`.
- **Success-exit-on-convergence semantics:** `scripts/analyze_longitudinal.py:993`:
  ```python
  return 0 if convergence_cycle is not None else 2
  ```

### Reproduction

Reviewer-confirmed on synthetic DB:

```python
# Seed a DB with condition='longitudinal' but only 5 evaluation_runs rows + a handful
# of invocation_events. Real 40-cycle campaign would have ~40 evaluation_runs rows
# and ~100+ invocation_events.
seed_longitudinal_db(n_cycles=5)

# Run the analyzer:
$ uv run scripts/analyze_longitudinal.py --db /tmp/stub.duckdb
# Output:
#   Total cycles:    5
#   KL threshold:    0.05
#   Min consecutive: 3
#   ✅ Cycles-to-convergence: 2
#
# Exit code:
$ echo $?
0
```

The partial campaign passes silently. No signal that this is not a complete 40-cycle run.

### Threat model / impact

- **Chapter 6 Pillar 3 RQ #2 (cycles-to-convergence) silently computed on incomplete data.** The thesis's core convergence claim can land on a 5-cycle stub; the reviewer has no way to know without cross-checking `evaluation_runs` row count.
- **Compounds with #117.** If the run-sheet selector (ext11 #111's post-fix SQL) picks a stub run as "latest," #116 then produces normal-looking output for it.
- **Compounds with #119.** A missing longitudinal watchdog (#119) lets aborted campaigns leave partial data; #116 then analyzes the partial data as valid.
- **Direction of bias: pro-convergence.** Short stubs with KL=0 at cycle 1 (empty-prior sentinel) + one or two small-change cycles satisfy the default `min_consecutive=3` sub-threshold streak, producing "converged at cycle 2-3" verdicts that the real 40-cycle run would likely not produce at that index.

### Remediation

**A. Explicit `--expected-cycles` completeness guard, mirroring ext7 #52.**

```python
parser.add_argument(
    "--expected-cycles", type=int, default=40,
    help="Expected cycle count (default: 40 for run_longitudinal.py --n-tasks 40). "
         "Analyzer refuses to proceed if len(cycles) < expected-cycles unless "
         "--allow-incomplete is passed.",
)
parser.add_argument(
    "--allow-incomplete", action="store_true",
    help="Proceed with partial data; sets _COMPLETENESS_BANNER so Chapter-6 "
         "titles are marked [INCOMPLETE: N/expected cycles].",
)

# After loading cycles, before proceeding:
if len(cycles) < args.expected_cycles and not args.allow_incomplete:
    print(
        f"ERROR: longitudinal run has {len(cycles)} cycles, expected "
        f"{args.expected_cycles}. Pass --allow-incomplete to proceed with "
        f"an [INCOMPLETE] banner, or --expected-cycles N to override.",
        file=sys.stderr,
    )
    conn.close()
    return 3  # distinct from empty-DB (1) and no-convergence (2)
```

**B. Stricter exit-code semantics.**

```python
# Before:
return 0 if convergence_cycle is not None else 2

# After — distinguish the three failure modes:
if len(cycles) < args.expected_cycles:
    return 3  # incomplete but allow_incomplete passed → return non-zero to signal
if convergence_cycle is None:
    return 2  # complete data, no convergence detected
return 0      # complete data + converged
```

**C. Gate convergence detection on minimum signal.**

Before invoking `_detect_convergence`, assert that at least one cycle had non-trivial signal (non-zero Gini OR non-empty invocation_events OR non-empty cycle_utility_scores). A run whose per-cycle data is identically empty is covered by #118 below but also surfaces here as "no cycle had anything to compute on."

**D. Regression test.**

```python
def test_analyzer_refuses_partial_longitudinal_run():
    db = _seed_partial_longitudinal(n_cycles=5)
    rc = run_analyze_longitudinal(db, expected_cycles=40)
    assert rc == 3, "Analyzer accepted 5-cycle DB as valid 40-cycle campaign"

def test_analyzer_accepts_partial_with_allow_incomplete():
    db = _seed_partial_longitudinal(n_cycles=5)
    rc, stdout = run_analyze_longitudinal(db, expected_cycles=40, allow_incomplete=True)
    assert rc in (2, 3), "incomplete run should never exit 0"
    assert "[INCOMPLETE" in stdout
```

### Recommendation

**P0 — blocks trustworthy rows 18/19.** ~30 LOC in analyzer + 2 regression tests. ~45 min.

---

## Issue #117 — Run-sheet "latest longitudinal run" selector picks aborted/partial runs; rows 18/19 then treat the stub as valid (P0)

### Summary

`docs/run_sheet.md:99, 103` populate `$LONGITUDINAL_RUN_ID` and `$SHIFT_RUN_ID` via:

```sql
SELECT run_id FROM evaluation_runs GROUP BY run_id ORDER BY MAX(timestamp) DESC LIMIT 1;
```

The selector picks the most-recently-written run. No completeness criterion (no minimum row count, no minimum cycle count, no "run matches the expected campaign shape" check).

Scenario: row 9 is restarted after an earlier abort. The old DB contains a complete 40-row longitudinal run (`longitudinal_complete_old`). The restart produces 5 rows before being interrupted (`longitudinal_partial_new`) — e.g., Docker daemon crash, user Ctrl-C, or #119's missing watchdog firing into an indefinite hang that's then manually killed. Both rows live in the DB. The selector picks `longitudinal_partial_new` because its `MAX(timestamp)` is later. Row 18 then analyzes the 5-row stub; #116 above lets the stub pass as "converged"; the final Chapter-6 table reports stub-derived numbers.

### Where

- **Selector (no completeness filter):** `docs/run_sheet.md:99` and `:103`:
  ```sql
  SELECT run_id FROM evaluation_runs GROUP BY run_id ORDER BY MAX(timestamp) DESC LIMIT 1
  ```
- **Consuming rows:** `docs/run_sheet.md` row 18 (`analyze_longitudinal.py --run-id $LONGITUDINAL_RUN_ID`) and row 19 (shift variant).
- **Compounds with #116** — the analyzer the selected `run_id` is fed into has no completeness guard.

### Reproduction

Reviewer-confirmed:

```sql
INSERT INTO evaluation_runs VALUES
    ('longitudinal_complete_old', ...)  × 40 rows, timestamps around 2026-04-20 08:00
    ('longitudinal_partial_new',  ...)  ×  5 rows, timestamps around 2026-04-21 10:00

SELECT run_id FROM evaluation_runs GROUP BY run_id ORDER BY MAX(timestamp) DESC LIMIT 1;
-- Result: 'longitudinal_partial_new'
```

User reported: "selector picked longitudinal_partial_new; analyzer then produced normal-looking output and exit 0."

### Threat model / impact

- **Silent campaign substitution.** The user expects row 18 to analyze "the campaign from row 9." Row 18 actually analyzes "whatever was written most recently" — which may or may not be the same thing.
- **Fix-induced-on-fix interaction.** Ext11 #111 already rewrote this SQL to GROUP BY run_id. The fix correctly avoided the DISTINCT + ORDER-BY-non-selected-column hazard, but didn't add a completeness filter. Third-order regression: the SQL is now semantically well-defined but still selects the wrong row by Chapter-6 standards.
- **Direction of bias: toward truncated-run analysis.** Partial runs are always newer (they overwrite aborted predecessors on re-run). So the selector systematically prefers partial runs over complete runs when both exist.
- **Run-sheet procedure assumption violated.** The Phase 4/5 scoping block assumes each campaign produces exactly one run_id. Re-runs after abort break that assumption; the selector has no way to distinguish.

### Remediation

**A. Add a completeness filter to the selector.**

```sql
SELECT run_id
FROM evaluation_runs
GROUP BY run_id
HAVING COUNT(*) >= :expected_rows      -- e.g., 40 for --n-tasks 40
ORDER BY MAX(timestamp) DESC
LIMIT 1
```

Run-sheet snippet:
```bash
# Pass the expected row count explicitly so the selector rejects stubs.
# For --n-tasks 40 longitudinal, expected_rows = 40.
export EXPECTED_LONGITUDINAL_ROWS=40
export LONGITUDINAL_RUN_ID="$(uv run python -c "
import duckdb
conn = duckdb.connect('data/eval/longitudinal.duckdb')
row = conn.execute(
    'SELECT run_id FROM evaluation_runs GROUP BY run_id '
    'HAVING COUNT(*) >= ? ORDER BY MAX(timestamp) DESC LIMIT 1',
    [${EXPECTED_LONGITUDINAL_ROWS}],
).fetchone()
if row is None:
    raise SystemExit('ERROR: no complete longitudinal run in DB')
print(row[0])
")"
```

**B. Alternative — explicit user pin.**

Require the user to set `LONGITUDINAL_RUN_ID` manually from a list-candidates command:

```bash
# Row 9 prints the run_id it produced at the end of its own output.
# User copies it into the shell env before running row 18.
export LONGITUDINAL_RUN_ID="<paste the printed uuid here>"
```

Less convenient but eliminates the "wrong run picked silently" failure mode entirely.

**C. Regression guard via run-sheet lint.**

```python
def test_run_sheet_longitudinal_selector_has_completeness_filter():
    content = Path("docs/run_sheet.md").read_text()
    selector_block = _extract_selector_block(content)
    assert "HAVING COUNT(*)" in selector_block, (
        "Longitudinal run-id selector missing completeness filter; "
        "aborted partial runs will win by MAX(timestamp)"
    )
```

### Recommendation

**P0 — land with #116.** ~10 LOC in run_sheet.md + 1 lint test. ~20 min. Must ship before any rerun scenario on an existing `longitudinal.duckdb`.

---

## Issue #118 — `_compute_kl` returns 0.0 on empty-vs-empty; convergence detector accepts zero-evidence runs as "converged" (P0)

### Summary

`analyze_longitudinal.py:112-117`:

```python
if not prev_dist and not curr_dist:
    return 0.0
if prev_dist and not curr_dist:
    return float("inf")
if curr_dist and not prev_dist:
    return 0.0
```

Empty-vs-empty returns 0.0. The docstring rationale is "both distributions are empty = no change = KL 0" which is mathematically defensible as a limit. But the downstream convergence detector at lines 731-737 cannot distinguish "KL 0.0 because the registry genuinely stabilized" from "KL 0.0 because there's no data at all." It consumes the 0.0 as a valid sub-threshold signal, triggers the `min_consecutive` streak detection, and reports convergence.

A 40-cycle longitudinal DB that contains ONLY `evaluation_runs` rows — no `invocation_events`, no `cycle_utility_scores`, no `cycle_events` — produces 40 consecutive empty-vs-empty KL values of 0.0. `_detect_convergence` sees an unbroken sub-threshold streak from cycle 1, reports "converged at cycle 2," and `main()` exits 0. The reviewer's reproduction confirmed "Cycles-to-convergence: 2, n_caps=0, exit 0."

`n_caps=0` is the only surface signal that something's wrong — but it's a line in the output table, not a gate. The exit code says success.

### Where

- **Empty-vs-empty sentinel:** `scripts/analyze_longitudinal.py:112-117`.
- **Convergence consumer (no data-presence gate):** `scripts/analyze_longitudinal.py:730-737`:
  ```python
  convergence_kls = kls_utility if primary_kl_source == "utility_scores" else kls_invocations
  if len(convergence_kls) > 1:
      convergence_cycle = _detect_convergence(
          convergence_kls[1:], args.threshold, args.min_consecutive,
      )
      if convergence_cycle is not None:
          convergence_cycle += 1
  ```

### Reproduction

```python
# Seed: only evaluation_runs rows, no invocation/utility/event rows.
for i in range(40):
    eval_store.record_run(
        condition="longitudinal", task_id=f"T{(i%20)+1:02d}",
        run_id="test_zero_evidence",
        ... no invocations flushed, no cycle_utility_scores, no cycle_events ...
    )

$ uv run scripts/analyze_longitudinal.py --db /tmp/zero_evidence.duckdb
# Output excerpt:
#   Total cycles:    40
#   ✅ Cycles-to-convergence: 2
#   ...per-cycle table shows n_caps=0 on every row...
$ echo $?
0
```

### Threat model / impact

- **Zero-evidence "convergence" is thesis-fatal.** Chapter 6 Pillar 3 RQ #2 concludes "JIT converges in K cycles." A DB missing invocation flushes (e.g., from a run that hit the flush-cutoff bug ext6 #23 pre-fix, or from a DB where Step 18.7 flush wiring failed) would report "converged at cycle 2" — a cleaner result than the real converged run.
- **Compounds with #116 + #117 + #119.** Incomplete runs (#116 / #117) or watchdog-aborted runs (#119) tend to have missing flush tables. Each independent bug lets the others produce their false-positive more confidently.
- **Direction of bias: pro-convergence, pro-thesis.** Worst direction — manufactures positive evidence for the convergence claim.
- **Detection from output is hard.** `n_caps=0` is in the per-cycle table but not in the headline ✅ banner. A reviewer skimming the stdout sees "✅ Cycles-to-convergence: 2" and moves on.

### Remediation

**A. Empty-vs-empty returns a sentinel that the detector skips.**

Replace the `0.0` return with a value the detector ignores:

```python
# scripts/analyze_longitudinal.py:112-117 — before:
if not prev_dist and not curr_dist:
    return 0.0

# after:
if not prev_dist and not curr_dist:
    return float("nan")  # no data; detector must skip NaN entries
```

Then in `_detect_convergence` (or at the consumer site), treat NaN as "insufficient data — reset the streak":

```python
def _detect_convergence(kls, threshold, min_consecutive):
    streak = 0
    for i, kl in enumerate(kls):
        if math.isnan(kl):
            streak = 0  # no data — break any sub-threshold run
            continue
        if kl < threshold:
            streak += 1
            if streak >= min_consecutive:
                return i - min_consecutive + 1
        else:
            streak = 0
    return None
```

**B. Independent data-presence gate at `main()`.**

Before invoking `_detect_convergence`, assert that some cycle had non-trivial data:

```python
has_any_signal = any(
    c.get("invocation_counts") or c.get("utility_scores") or c.get("events")
    for c in cycles
)
if not has_any_signal:
    print(
        "ERROR: longitudinal DB has evaluation_runs rows but NO invocation/utility/event data. "
        "Convergence cannot be computed on zero-evidence runs. Check that cycle_flush wired correctly.",
        file=sys.stderr,
    )
    conn.close()
    return 4  # new exit code: data present but no signal
```

**C. Regression test.**

```python
def test_zero_evidence_db_does_not_report_convergence(tmp_path):
    db = _seed_zero_evidence_db(tmp_path, n_cycles=40)
    rc, stdout = run_analyze_longitudinal(db)
    assert rc != 0, "exit 0 on zero-evidence run"
    assert "Cycles-to-convergence" not in stdout or "zero-evidence" in stdout.lower()
```

### Recommendation

**P0 — land with #116.** ~20 LOC across KL helper + detector + main() gate + 1 regression test. ~30 min.

---

## Issue #119 — Longitudinal runners have no campaign watchdog; rows 9/10 can hang indefinitely with no abort signal (HIGH, run-sheet blocker)

### Summary

`scripts/run_pilot.py` wraps each `harness.run_condition(...)` call in a SIGALRM-based `_condition_budget_watchdog(...)` that raises `BudgetExceeded` on timeout (ext10 #103). Neither longitudinal runner inherited this:

- `scripts/run_longitudinal.py:233-306` — the per-task loop invokes `pipeline.invoke(initial_state, config=invoke_config)` directly inside a `trace_context` block with no timeout, no SIGALRM, no per-task budget.
- `scripts/run_longitudinal_shift.py:328-399` — same shape. Same missing watchdog.

Consequence: a single hung task (Docker container stuck in `pip install`, LangFuse API rate-limited and retrying, a DeepAgent that plateaus on `recursion_limit=50` without producing terminal output, or network egress blocked mid-task) freezes the whole row 9 / row 10 campaign. The runner gives no abort signal; the user must Ctrl-C manually (if attended) or wait for the host-level timeout (if any) to kill the process. If the campaign is unattended overnight, the `~6-10h` wall-clock budget can be burned on a single stuck task.

This is not a correctness bug per se — no silent-wrong-answer surface — but it's a run-sheet blocker: unattended rows 9/10 can't be scheduled with confidence.

### Where

- **`run_longitudinal.py` per-task loop without watchdog:** `scripts/run_longitudinal.py:233-306`.
- **`run_longitudinal_shift.py` same shape:** `scripts/run_longitudinal_shift.py:328-399`.
- **Reference implementation on `run_pilot.py`:** `scripts/run_pilot.py:325-352` (`_condition_budget_watchdog` + `BudgetExceeded` handler).

### Reproduction

Patch `pipeline.invoke` to `time.sleep(10_000)` inside the first task of a longitudinal run; observe:

```bash
$ uv run scripts/run_longitudinal.py --n-tasks 40 &
$ JOB=$!
$ # Wait 2 hours; the process is still alive, stuck on task 1.
$ kill -0 $JOB && echo "STILL RUNNING"
STILL RUNNING
```

No watchdog fires. No `BudgetExceeded` path exists. Only `Ctrl-C` / `kill` recovers.

### Threat model / impact

- **Unattended campaign scheduling is unsafe.** Phase 5 eval expectation is "launch overnight, check output in the morning." A stuck task burns the whole window.
- **Compounds with #115 / #117 / #116.** A hung run that's manually killed leaves a partial DB; #117 picks it as "latest"; #116 / #118 analyze the stub; Chapter 6 gets false-positive convergence from incomplete data.
- **Run-sheet wall-clock estimates are wrong under contention.** Row 9's "~6-10h" budget assumes every task completes. Without a watchdog, a single 10h stall blows the estimate without any data in the DB.

### Remediation

**A. Extract the watchdog from `run_pilot.py` into a shared helper.**

```python
# scripts/_budget_watchdog.py  (new)
import signal
import contextlib
import logging

class BudgetExceeded(Exception):
    pass

@contextlib.contextmanager
def budget_watchdog(seconds: int, *, name: str, logger_: logging.Logger):
    """SIGALRM-based timeout. Raises BudgetExceeded in main thread on expiry."""
    if seconds <= 0:
        yield
        return
    def _handler(signum, frame):
        raise BudgetExceeded(f"'{name}' exceeded {seconds}s budget")
    old = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
```

Port `run_pilot.py::_condition_budget_watchdog` to delegate to this shared helper (keeps its existing behavior).

**B. Wrap each longitudinal task invocation.**

```python
# run_longitudinal.py:233 — per-task loop:
parser.add_argument(
    "--per-task-budget-seconds", type=int, default=1800,  # 30 min default
    help="SIGALRM budget per task; 0 disables.",
)
parser.add_argument(
    "--campaign-budget-hours", type=float, default=12.0,
    help="Total wall-clock budget for the full sequence.",
)

# Around pipeline.invoke:
with budget_watchdog(args.per_task_budget_seconds, name=f"task-{i}-{task.task_id}", logger_=logger):
    final_state = pipeline.invoke(initial_state, config=invoke_config)

# Wrap the whole loop:
_campaign_deadline = time.monotonic() + args.campaign_budget_hours * 3600
for i, task in enumerate(task_sequence):
    if time.monotonic() > _campaign_deadline:
        logger.error("Campaign budget exhausted; stopping at task %d/%d", i, len(task_sequence))
        break
    ...
```

**C. Surface the abort as a non-zero exit.**

Mirror #115's exit-code contract: if any task's watchdog fires OR the campaign deadline is exceeded, `main()` returns a distinct non-zero exit code (e.g., 3 for per-task timeout, 5 for campaign deadline).

**D. Regression test.**

```python
def test_longitudinal_runner_aborts_on_per_task_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.orchestrator.pipeline.create_pipeline",
        _make_pipeline_that_sleeps_forever,
    )
    rc = run_longitudinal.main(["--n-tasks", "3", "--per-task-budget-seconds", "2"])
    assert rc != 0, "expected non-zero exit on watchdog abort"
```

### Recommendation

**HIGH — run-sheet blocker for unattended rows 9/10.** ~50 LOC (shared helper + two runner integrations + exit-code wiring) + 2 regression tests. ~1h. Must ship before any unattended longitudinal campaign.

---

## Issue #120 — `analyze_context_utilization.py` silently deflates Context Economy averages on LangFuse observation-fetch failures (P0 CRITICAL)

### Summary

`scripts/analyze_context_utilization.py::fetch_observation` wraps the LangFuse observation GET in a blanket `try/except Exception: return None`. Callers of `extract_trace_context` then skip `None` observations silently (line 155-156), and if **every** observation for a trace failed to fetch, `extract_trace_context` reaches line 204's `if not steps:` branch and returns a zero-valued metrics dict (`step_count=0, context_utilization_pct=0.0, total_tokens_consumed=0, …`). This zero-metrics dict is indistinguishable from a trace that genuinely had no GENERATION observations.

Line 523's outer loop appends the zero-metrics dict to `trace_results` unconditionally. All downstream aggregations (`peak_context_utilization_pct` averages, `total_chain_input_tokens` distributions, Chapter-6 Context Economy columns) pool these phantom-zero rows with the real rows — silently deflating every headline percentage and every mean.

Line 535's `--allow-empty` / `exit 2` loud-fail guard fires ONLY when the entire fetch returned zero traces (the fetch-side zero case). It does NOT fire when traces were returned but their observation sub-fetches all failed. That is the gap this finding documents.

Transient LangFuse failures that would hit this path: rate-limit throttling during a heavy read window, intermittent network egress loss, observations aged past a tier-2 retention boundary, or token-auth expiration partway through the loop. All return 4xx/5xx, all are swallowed identically by the bare `except Exception`.

### Where

- **Exception-swallowing fetch:** `scripts/analyze_context_utilization.py:135-142`:
  ```python
  def fetch_observation(client: httpx.Client, obs_id: str) -> dict | None:
      try:
          resp = client.get(f"/api/public/observations/{obs_id}")
          resp.raise_for_status()
          return resp.json()
      except Exception:
          return None
  ```
- **Silent skip on None:** `scripts/analyze_context_utilization.py:153-156`:
  ```python
  for obs_id in trace["observation_ids"]:
      obs = fetch_observation(client, obs_id)
      if obs is None:
          continue
  ```
- **Zero-metrics-dict fallback when all fetches failed:** `scripts/analyze_context_utilization.py:204-217` — the `if not steps:` branch returns a canonical zero dict without any "degraded" / "fetch_failed" marker.
- **Outer loop appends unconditionally:** `scripts/analyze_context_utilization.py:519-527`:
  ```python
  for i, trace in enumerate(traces):
      metrics = extract_trace_context(client, trace)
      metrics["trace_id"] = trace["id"]
      metrics["timestamp"] = trace["timestamp"]
      metrics["latency"] = trace["latency"]
      trace_results.append(metrics)
  ```

### Reproduction

Reviewer-confirmed:

```python
# One good trace → extract_trace_context returns steps, util=50%
# One trace whose every observation fetch returns None (patched httpx client)
#   → extract_trace_context returns {step_count: 0, context_utilization_pct: 0.0, ...}
# Downstream aggregate:
#   peak_context_utilization_pct_mean = (50.0 + 0.0) / 2 = 25.0
# Expected (excluding the failed trace):
#   peak_context_utilization_pct_mean = 50.0 / 1 = 50.0

$ echo $?
0
```

### Threat model / impact

- **Chapter 6 Pillar 3 Context Economy numbers silently deflated.** Row 13's "peak context utilization across chain" averages are pulled toward 0 proportional to LangFuse transient-failure rate during the analysis window.
- **Direction of bias: anti-chain / pro-single-agent.** Deflation reduces the reported multi-agent context pressure, making the "Context Economy tension" narrative weaker than the real data supports (or, if the real data weakly supports it, masking genuine tension).
- **Compounds with ext7 #47.** #47 correctly loud-fails on zero-fetch traces but the post-fetch observation-level degradation is invisible. Two layers of defense needed; only one exists.
- **Detectability from stdout is poor.** Per-trace zero dicts are indistinguishable from traces that genuinely had zero GENERATION observations (pre-LangFuse-instrumented runs). A careful reviewer would need to cross-check `step_count` distribution against trace counts — not flagged in the standard output.
- **Worst-case silent cascade.** A day of LangFuse throttling during the analysis window could push the reported Context Economy mean toward 0 across every agent class, and Chapter 6 would tell the opposite story from the real data.

### Remediation

**A. Distinguish fetch-success-with-no-GENERATION from all-fetches-failed.**

```python
def extract_trace_context(client: httpx.Client, trace: dict) -> dict:
    steps: list[dict] = []
    n_attempted = 0
    n_failed = 0

    for obs_id in trace["observation_ids"]:
        n_attempted += 1
        obs = fetch_observation(client, obs_id)
        if obs is None:
            n_failed += 1
            continue
        # ... existing step-extraction logic ...

    # Degraded signal: every observation we tried to fetch failed.
    if n_attempted > 0 and n_failed == n_attempted:
        return {
            "step_count": 0,
            "context_utilization_pct": 0.0,
            # ...other zero fields...
            "_fetch_degraded": True,        # NEW: explicit flag
            "_obs_attempted": n_attempted,
            "_obs_failed": n_failed,
            "steps": [],
        }

    # Legitimate no-GENERATION trace: reached here with steps still empty
    # because none of the successful fetches were GENERATION-typed.
    if not steps:
        return {
            # ...existing zero dict...
            "_fetch_degraded": False,
            "steps": [],
        }
```

**B. Aggregate pass filters out degraded rows or surfaces them explicitly.**

```python
# Downstream aggregate:
degraded = [m for m in trace_results if m.get("_fetch_degraded")]
valid = [m for m in trace_results if not m.get("_fetch_degraded")]

if degraded:
    print(
        f"WARNING: {len(degraded)}/{len(trace_results)} traces had all "
        f"observation fetches fail (likely LangFuse transient). "
        f"Excluded from Context Economy averages.",
        file=sys.stderr,
    )

# Averages computed only over `valid`.
if not valid and not args.allow_empty:
    print(
        f"ERROR: zero valid traces remain after fetch-degradation filter "
        f"({len(degraded)} degraded, 0 valid). Pass --allow-empty to proceed.",
        file=sys.stderr,
    )
    return 2
```

**C. Per-observation retry with jitter, not a blanket swallow.**

```python
def fetch_observation(client: httpx.Client, obs_id: str, *, max_retries: int = 3) -> dict | None:
    for attempt in range(max_retries):
        try:
            resp = client.get(f"/api/public/observations/{obs_id}")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (404, 410):
                return None  # genuinely gone
            if attempt < max_retries - 1:
                time.sleep(0.5 * (2 ** attempt))
                continue
            return None
        except httpx.HTTPError:
            if attempt < max_retries - 1:
                time.sleep(0.5 * (2 ** attempt))
                continue
            return None
    return None
```

Retries absorb transient 429/5xx without contaminating the downstream average with phantom zeros.

**D. Regression test.**

```python
def test_all_observations_failed_is_flagged_not_averaged(monkeypatch):
    # Patch client.get to always raise
    monkeypatch.setattr("analyze_context_utilization.fetch_observation", lambda c, o: None)
    trace = {"id": "t1", "observation_ids": ["o1", "o2", "o3"]}
    metrics = extract_trace_context(mock_client, trace)
    assert metrics.get("_fetch_degraded") is True
    assert metrics["_obs_failed"] == 3

def test_partial_fetch_failure_still_averages_successful_steps(monkeypatch):
    # Patch first obs to fail, second to succeed with usable data
    ...
    metrics = extract_trace_context(mock_client, trace)
    assert metrics.get("_fetch_degraded") is False
    assert metrics["step_count"] == 1
```

### Recommendation

**P0 — blocks Chapter 6 Context Economy table trustworthiness.** ~60 LOC across `extract_trace_context` + aggregate pass + retry wrapper + 2 regression tests. ~1-1.5h. Must ship before any Row 13 analysis feeds Chapter 6.

### Legacy data

Any existing Row 13 output computed pre-fix on a LangFuse-throttled window has unknown deflation magnitude. Re-run post-fix to get corrected numbers; no data migration needed.

---

## Issue #121 — Sample-plan persistence keys only on DB path stem; pre-existing plans override new scoped audit runs (P0)

### Summary

Ext8 #57 introduced a persistent `audit_sample_plan.json` to make `audit_contamination.py` resumes idempotent across sessions. The fix is correct for the single-campaign case but fails for the multi-scope case:

`_sample_plan_path(db_path: Path) -> Path` at `scripts/audit_contamination.py:235-237` keys the plan file on `db_path.stem` alone:

```python
_SAMPLE_PLAN_FILENAME = "audit_sample_plan.json"

def _sample_plan_path(db_path: Path) -> Path:
    return db_path.parent / f".{db_path.stem}_{_SAMPLE_PLAN_FILENAME}"
```

No dependence on `run_ids`, `since`, `conditions`, `types`, `per_stratum`, or `seed`. Two independent audit invocations against the same DB with different scopes collide on the same plan file.

Scenario:
1. First invocation — `audit_contamination.py --run-ids run_old --per-stratum 10 --seed 42` — draws a sample of rows exclusively from `run_old` and persists their ids to `.<dbname>_audit_sample_plan.json`.
2. Second invocation — `audit_contamination.py --run-ids run_new --per-stratum 20 --seed 99` — intends to audit `run_new` exclusively.

`fetch_unlabeled_rows` at line 505 correctly scopes the pool to `run_new`. `get_or_create_sample_plan` at line 516 finds the pre-existing plan file, loads `run_old`'s ids, computes `id_to_row = {r.row_id: r for r in rows}` where `rows` is the `run_new`-scoped pool. The intersection `[id_to_row[rid] for rid in plan_ids if rid in id_to_row]` at line 273 is empty — none of `run_old`'s row_ids appear in `run_new`'s row set.

Result: the resumed sample has 0 rows. Line 547-549:

```python
if not sample:
    print("\nNothing to audit — all strata empty or already labeled.")
    return 0
```

Exit 0 with no M2 labels produced. Row 17 proceeds with whatever pre-existing labels exist; Chapter 6 M2 table reflects the wrong campaign's contamination.

Reviewer-confirmed reproduction: "first scoped run (`run_old`) persisted plan; second scoped run (`run_new`) had pool size 2 but loaded plan intersection as 0 and reported 'Nothing to audit' path."

### Where

- **Plan path keyed on DB stem only:** `scripts/audit_contamination.py:235-237`.
- **Load-and-intersect without scope check:** `scripts/audit_contamination.py:257-278`:
  ```python
  plan_path = _sample_plan_path(db_path)
  id_to_row = {r.row_id: r for r in rows}
  if plan_path.exists():
      try:
          plan_ids = json.loads(plan_path.read_text())
      except (OSError, ValueError) as exc:
          ...
      if plan_ids is not None:
          resumed = [id_to_row[rid] for rid in plan_ids if rid in id_to_row]
          print(f"ext8 #57: loaded pre-committed sample plan from {plan_path} — "
                f"{len(resumed)}/{len(plan_ids)} rows still unlabeled.")
          return resumed
  ```
- **Main wires scope args but doesn't thread them into plan key:** `scripts/audit_contamination.py:495-523`.
- **"Nothing to audit" exit 0:** `scripts/audit_contamination.py:547-549`.

### Reproduction

```bash
# Seed: DB with two complete campaigns run_old and run_new, both with
# is_pilot=FALSE human_label IS NULL rows suitable for sampling.

$ uv run scripts/audit_contamination.py --db data/eval/metrics.duckdb \
    --run-ids run_old --per-stratum 10 --seed 42
# Draws 40 rows from run_old, persists plan to
# .metrics_audit_sample_plan.json with their row_ids.

# User restarts with a new scope intending fresh audit:
$ uv run scripts/audit_contamination.py --db data/eval/metrics.duckdb \
    --run-ids run_new --per-stratum 20 --seed 99
# ext8 #57: loaded pre-committed sample plan from .metrics_audit_sample_plan.json
# — 0/40 rows still unlabeled.
#
# Nothing to audit — all strata empty or already labeled.
$ echo $?
0
```

User's repro matches. Second invocation silently exits 0 with zero audit work performed; the user has no surface signal that the plan file from the first invocation silently blocked the second.

### Threat model / impact

- **Chapter 6 M2 contamination silently mis-scoped or skipped entirely.** The user invokes Row 16 with `--run-ids $CAMPAIGN_RUN_IDS` per ext7 #45's scoping convention, expecting fresh M2 labels for that campaign. The pre-existing plan file (from a test run, dry run, earlier campaign, or a colleague's session on the same shared machine) can silently suppress label collection.
- **Direction of bias: under-labeling.** The plan override reduces the effective audit sample; intersections with non-matching pools trend toward 0 in the worst case, shrinking M2 data.
- **Compounds with ext7 #45 / #52.** Completeness guards assume some M2 labels exist; a silent "nothing to audit" returns 0 rows, so subsequent Row 17 analyzers either use stale labels or compute M2 on an empty sample.
- **Compounds with ext7 #49 (DB isolation).** If the user does NOT use `--fresh-db`, the shared DB can carry plan files across campaigns invisibly. The plan-file side-channel is orthogonal to the DB side-channel; #49 closed the latter, #121 opens a new one.
- **Fix-induced regression against ext8 #57.** The fix correctly solved "resume draws from shrinking pool"; it did not address "resume binds to a stale plan key" — the seventh fix-induced regression in the series.

### Remediation

**A. Key the plan file on all scope + param inputs.**

```python
import hashlib, json

def _sample_plan_path(
    db_path: Path,
    *,
    run_ids: list[str] | None,
    since: str | None,
    conditions: list[str],
    types: list[str],
    per_stratum: int,
    seed: int,
) -> Path:
    """Plan file lives alongside the DB, scoped to the audit invocation's
    scope + sampling parameters.

    ext12 #121: pre-fix keyed on db_path.stem only; two invocations with
    different --run-ids / --per-stratum collided on the same plan file,
    silently suppressing the later invocation's audit work.
    """
    payload = json.dumps(
        {
            "run_ids": sorted(run_ids) if run_ids else None,
            "since": since,
            "conditions": sorted(conditions),
            "types": sorted(types),
            "per_stratum": per_stratum,
            "seed": seed,
        },
        sort_keys=True,
    )
    scope_hash = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return db_path.parent / f".{db_path.stem}_{scope_hash}_{_SAMPLE_PLAN_FILENAME}"
```

Two distinct scopes → two distinct plan files. Resumes within a scope still share their plan. `seed` is part of the key so changing `--seed` produces a fresh draw (intentional — seed changes mean a new sample).

**B. Thread scope args through `get_or_create_sample_plan`.**

```python
def get_or_create_sample_plan(
    rows: list[AuditRow],
    *,
    db_path: Path,
    run_ids: list[str] | None,
    since: str | None,
    conditions: list[str],
    types: list[str],
    per_stratum: int,
    seed: int,
) -> list[AuditRow]:
    plan_path = _sample_plan_path(
        db_path,
        run_ids=run_ids, since=since,
        conditions=conditions, types=types,
        per_stratum=per_stratum, seed=seed,
    )
    # ... existing load-or-create logic ...

# Main call site (line 516):
sample = get_or_create_sample_plan(
    pool,
    db_path=db_path,
    run_ids=run_ids, since=since,
    conditions=conditions, types=types,
    per_stratum=per_stratum, seed=seed,
)
```

**C. Loud-fail when the resumed plan's intersection with the current pool is surprisingly empty.**

```python
if plan_ids is not None:
    resumed = [id_to_row[rid] for rid in plan_ids if rid in id_to_row]
    if len(plan_ids) > 0 and len(resumed) == 0:
        # Every plan id is outside the current pool — sign that the plan
        # file was drawn for a different scope and the scope-hash check missed.
        print(
            f"ERROR: plan file at {plan_path} has {len(plan_ids)} rows, "
            f"but ZERO of them are in the current scoped pool of {len(rows)} rows. "
            f"Possible scope mismatch. Delete {plan_path} to force a fresh draw, "
            f"or review your --run-ids / --per-stratum / --seed args.",
            file=sys.stderr,
        )
        raise SystemExit(3)
    print(f"ext8 #57: loaded pre-committed sample plan from {plan_path} "
          f"— {len(resumed)}/{len(plan_ids)} rows still unlabeled.")
    return resumed
```

Defence-in-depth: even if (A) has a bug or the user manually renames plan files, a scope-mismatch is surfaced rather than silently collapsing to empty.

**D. `--force-fresh-plan` CLI flag.**

```python
parser.add_argument(
    "--force-fresh-plan", action="store_true",
    help="Delete any existing plan file before sampling. Use when the audit "
         "scope or params changed in a way the plan key doesn't capture.",
)
```

User escape hatch.

**E. Regression test.**

```python
def test_plan_path_differs_by_run_ids(tmp_path):
    db = tmp_path / "x.duckdb"
    p1 = _sample_plan_path(db, run_ids=["a"], since=None,
                           conditions=["full"], types=["mcp_tool"],
                           per_stratum=10, seed=42)
    p2 = _sample_plan_path(db, run_ids=["b"], since=None,
                           conditions=["full"], types=["mcp_tool"],
                           per_stratum=10, seed=42)
    assert p1 != p2

def test_zero_intersection_raises_scope_mismatch_error(tmp_path):
    # Write plan for scope A; invoke resume with scope B pool that doesn't
    # overlap. Assert SystemExit(3) with scope-mismatch message.
    ...
```

### Recommendation

**P0 — blocks Chapter 6 M2 trustworthiness for any campaign re-run on a non-isolated DB.** ~40 LOC in `audit_contamination.py` (path builder + threading + defence-in-depth check + CLI flag) + 2 regression tests. ~45 min.

### Legacy data

Existing plan files under `.<dbname>_audit_sample_plan.json` are keyed on the old scheme. Options:
- (a) Delete all `.*_audit_sample_plan.json` sidecars before the fix ships (user-side one-liner).
- (b) Fix accepts both the old naming (fallback when the new-scheme file doesn't exist) AND the new naming; warn on fallback so users migrate to fresh plan files.

(a) is cleaner; (b) is more forgiving. Recommend (a) + a release note.

---

## Issue #122 — `backfill_token_costs.py` silently zeros whole non-static runs when LangFuse generations are partial; exit 0 masks material M4 under-reporting (P0 CRITICAL)

### Summary

Ext6 #36 added a loud-fail at `scripts/backfill_token_costs.py:725-736` — if `fetch_all_generations` returns empty, the script exits 2 instead of silently writing zeros. The fix is globally correct but locally incomplete: it only fires when **every** generation is missing. If generations exist for SOME runs but not others (the common case when a LangFuse reingest completes for a subset of runs, or when one run's traces aged out of the retention window), the partial-fetch path produces silent zeros for the affected runs.

Specifically:

1. Line 738-740 maps whatever generations exist to runs via `map_generations_to_runs`. Runs with no matched generations silently get no entries in `run_to_gens`.
2. Line 744 `compute_run_costs(run_to_gens, run_windows)` iterates `run_windows` (all non-static runs in scope). A run with zero matched generations yields `{"n_generations": 0, "total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_cost_usd": 0.0, ...}` — valid dict, all zeros.
3. Line 750-763 — `compute_per_task_costs` OR `compute_backfill_values` consumes the zero-summary; since per-run totals are 0, every record in that run gets `token_cost=0` written.
4. Line 765-766 prints `Backfill: N/M records would get non-zero token costs`. Reader would see the ratio but unless they explicitly sanity-check, the write proceeds.
5. Line 768-779 — under `--write`, the UPDATE loop runs on all records including zero-cost ones.
6. Line 800 `print("Done.")`. `main()` returns None. Bare `if __name__ == "__main__": main()` at line 803-804 → exit 0.

Reviewer-confirmed reproduction: "two full runs in one DB, LangFuse generations only for run_a → run_b got 0 tokens for all rows, script printed `Done.` and exited 0." Row 17's M4 aggregate then reads run_b as genuinely zero-cost, silently halving or worse the reported JIT condition's per-task cost.

### Where

- **Loud-fail is global-empty only:** `scripts/backfill_token_costs.py:725-736`.
- **Zero-summary production for unmatched runs:** `scripts/backfill_token_costs.py:258` (`compute_run_costs`) — emits a valid dict for every `run_windows` entry regardless of whether any generations landed.
- **Per-task / run-level backfill consumes zero-summaries without sanity check:** `scripts/backfill_token_costs.py:754-763`.
- **Write proceeds on any non-empty `updates` list:** `scripts/backfill_token_costs.py:768-779`.
- **`main()` has no exit-code wrapper:** `scripts/backfill_token_costs.py:800, 803-804`.

### Reproduction

```bash
# Seed: DB with 2 runs (run_a, run_b), each with 20 evaluation_runs rows.
# Mock LangFuse generations: only run_a's are populated; run_b's have aged out.

$ uv run scripts/backfill_token_costs.py --db data/eval/metrics.duckdb --write
Mapped to 1 runs, 0 unmatched generations.
  Condition full / run_a: 15000 tokens (20 rows)
  Condition full / run_b: 0 tokens (20 rows)       ← silent zero
  Backfill: 20/40 records would get non-zero token costs
Writing to DuckDB...
  Updated 40 records.
Done.
$ echo $?
0
```

User's observation: "script printed `Done.` and exited 0" — matches the reproduction.

### Threat model / impact

- **Chapter 6 M4 silently under-reported whenever LangFuse has partial retention.** A JIT condition whose last 5 runs happened 8+ days before the backfill (default retention window) would show reduced per-run cost for those 5; the aggregate averages cleanly with the earlier 5 at full cost.
- **Direction of bias: anti-JIT cost reporting.** Under-reported JIT cost makes the JIT-vs-static cost comparison LOOK BETTER than reality. Worst direction — manufactures evidence for a thesis-favorable claim.
- **Compounds with ext6 #36's fix messaging.** #36 documented the loud-fail on empty; users assume the fix covers "any missing data"; it only covers global-empty. Silent per-run zeros still happen.
- **Compounds with ext10 #105 (backfill window buffer collisions).** If two back-to-back runs land inside the ±5-min window, cross-attribution can leak generations across runs, producing a DIFFERENT flavor of wrong: run_a's cost attributed to run_b plus run_b's own missing generations = wild under- or over-report.
- **Detection requires careful reading.** `Backfill: 20/40 records would get non-zero token costs` is the only pre-write signal. A half-coverage ratio looks plausible if the user expected some retrieval-only rows; no loud distinction between "retrieval-only → correctly 0" and "should-have-cost → silently 0."

### Remediation

**A. Per-run completeness check before write.**

```python
# Before the write loop (around line 765):
zero_summary_runs = [
    rid for rid, s in run_summaries.items()
    if s["n_generations"] == 0 and s["condition"] != "static"
]
if zero_summary_runs:
    print(
        f"\nERROR: {len(zero_summary_runs)} non-static runs have ZERO "
        f"LangFuse generations matched: {zero_summary_runs[:5]}"
        f"{'...' if len(zero_summary_runs) > 5 else ''}",
        file=sys.stderr,
    )
    print(
        "  Likely causes: (a) LangFuse retention aged out this run's traces, "
        "(b) LangFuse credentials changed between run and backfill, "
        "(c) run_id naming drift between runner and trace metadata.",
        file=sys.stderr,
    )
    if not args.allow_partial:
        print(
            "  Refusing to write partial backfill. Pass --allow-partial to "
            "proceed with zeros for these runs (and document in Chapter 4.5).",
            file=sys.stderr,
        )
        conn.close()
        sys.exit(3)
    print(
        f"  --allow-partial passed: proceeding with zero token_cost for "
        f"{len(zero_summary_runs)} runs. Chapter 4.5 disclosure REQUIRED.",
        file=sys.stderr,
    )
```

Exit code 3 is distinct from ext6 #36's exit 2 (global-empty). Analyzers consuming the output can distinguish "no data at all" from "data for some runs only."

**B. CLI flag for explicit override.**

```python
parser.add_argument(
    "--allow-partial", action="store_true",
    help="Proceed with token_cost=0 for non-static runs whose LangFuse "
         "generations didn't match. Default: refuse with exit 3. Must be "
         "documented in Chapter 4.5 if used.",
)
```

**C. Per-run count in the pre-write summary.**

Extend the line-766 one-liner to break down by condition:

```python
by_condition: dict[str, tuple[int, int]] = {}
for rid, s in run_summaries.items():
    cond = s["condition"]
    has, total = by_condition.get(cond, (0, 0))
    has += (1 if s["n_generations"] > 0 else 0)
    total += 1
    by_condition[cond] = (has, total)

print(f"\n  Per-condition coverage:")
for cond, (has, total) in sorted(by_condition.items()):
    marker = "⚠" if has < total and cond != "static" else " "
    print(f"  {marker} {cond}: {has}/{total} runs have non-zero LangFuse generations")
```

Reader sees "full: 1/2 runs" and has a chance to abort the write.

**D. `sys.exit(main() or 0)` wrapper.**

Mirror #115's fix:

```python
# scripts/backfill_token_costs.py:803-804 — before:
if __name__ == "__main__":
    main()

# after:
if __name__ == "__main__":
    sys.exit(main() or 0)
```

And convert `main()`'s internal `sys.exit(N)` calls to `return N` so the wrapper handles propagation uniformly.

**E. Regression test.**

```python
def test_backfill_refuses_partial_langfuse_coverage(monkeypatch, tmp_path):
    db = _seed_two_runs(tmp_path)
    monkeypatch.setattr(
        "scripts.backfill_token_costs.fetch_all_generations",
        lambda *a, **kw: _generations_for_run("run_a"),  # run_b empty
    )
    rc = backfill_token_costs.main(["--db", str(db), "--write"])
    assert rc == 3, "expected exit 3 on partial LangFuse coverage"

def test_backfill_allow_partial_proceeds_with_warning(...):
    ...
    rc = backfill_token_costs.main(["--db", str(db), "--write", "--allow-partial"])
    assert rc == 0
```

### Recommendation

**P0 — blocks Chapter 6 M4 trustworthiness.** ~40 LOC in `backfill_token_costs.py` (completeness check + CLI flag + per-condition breakdown + exit-code wrapper) + 2 regression tests. ~1h. Must ship before any Row 11 → Row 17 pipeline execution.

### Legacy data

Any prior Row 11 output on partial-LangFuse DBs silently under-reports M4 for affected runs. Re-run post-fix after confirming LangFuse data completeness (or accept the override with Chapter 4.5 disclosure).

---

## Issue #123 — Row 19 silently degrades to non-shift analysis and still exits 0 when the distribution-shift marker write fails (P0)

### Summary

`run_longitudinal_shift.py:467-511` wraps the `distribution_shift` marker write in a broad `except Exception` that logs a warning and continues:

```python
if cycle == args.shift_at and not shift_marked:
    try:
        details = ...
        eval_store.record_cycle_events(
            run_id=run_id, condition="longitudinal",
            task_id=task.task_id, events=[{
                "capability_id": "__system__",
                "event_type": "distribution_shift",
                "details": details,
            }],
            occurred_at=record.timestamp,
        )
        shift_marked = True
        ...
        try:
            # Step 13.7 LangFuse mirror emit
            ...
        except Exception as exc:  # noqa: BLE001
            logger.debug("LangFuse distribution_shift emit failed: %s", exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to write distribution_shift marker: %s", exc)
```

If `record_cycle_events` raises (DuckDB transient lock, schema drift, disk full), the runner logs a warning and the per-task loop continues. `shift_marked` stays False; the runner never retries. The campaign completes normally; rows for both phases exist in `evaluation_runs` and `invocation_events`; only the `cycle_events` row flagging the boundary is missing.

Downstream at `analyze_longitudinal.py:762-763`:

```python
shift_cycle = _detect_shift_point(cycles)
if shift_cycle is not None:
    _print_header("Distribution-Shift Analysis — Pillar 3 RQ #3")
    ...
```

`_detect_shift_point` reads the `cycle_events` table for `event_type="distribution_shift"`. Marker absent → returns None → entire Distribution-Shift Analysis section silently omitted. No else-branch, no warning, no exit-code escalation. `main()` reaches line 993's `return 0 if convergence_cycle is not None else 2` and exits based ONLY on whether convergence was detected.

Reviewer-confirmed reproduction: "40-cycle shift DB without distribution_shift marker → analyzer return code 0, reported convergence, and no 'Distribution-Shift Analysis' section." Row 19 prints "✅ Cycles-to-convergence: N" and exits 0; the thesis's Pillar 3 RQ #3 evidence is silently missing.

### Where

- **Broad-except on shift-marker write:** `scripts/run_longitudinal_shift.py:510-511`.
- **Runner never retries, no exit-code propagation:** `scripts/run_longitudinal_shift.py:467-511`.
- **Analyzer silently omits RQ #3 on marker absence:** `scripts/analyze_longitudinal.py:762-764` — `if shift_cycle is not None:` with no else.
- **Exit-code only gates on convergence:** `scripts/analyze_longitudinal.py:993` — `return 0 if convergence_cycle is not None else 2`.

### Reproduction

```bash
# Run shift campaign with DuckDB locked mid-phase-A boundary:
$ uv run scripts/run_longitudinal_shift.py --shift-at 20 --total 40
# Runner log: "Failed to write distribution_shift marker: ..."
# Campaign continues; all 40 evaluation_runs rows written.

# Analyzer:
$ uv run scripts/analyze_longitudinal.py --db data/eval/longitudinal_shifted.duckdb
# Total cycles: 40
# ✅ Cycles-to-convergence: 12
# (no Distribution-Shift Analysis section)
$ echo $?
0
```

### Threat model / impact

- **Pillar 3 RQ #3 silently absent.** Thesis claims "graceful / sluggish / collapse adaptation" to distribution shifts; the only evidence for that claim is the Distribution-Shift Analysis section. When missing, the claim has no support in the run's output, but Row 19's exit 0 suggests "analysis succeeded."
- **Compounds with #116 (no completeness guard) and #118 (zero-evidence convergence).** A shift run with a failed marker write can still report convergence (false or real) and exit 0 — the reviewer sees a clean "converged" verdict and moves on.
- **Direction of bias: pro-thesis when coupled with convergence reporting.** The missing RQ #3 section isn't reported as an absence; the present convergence verdict reads as the full analysis output. Silent under-reporting of the harder-to-obtain adaptation metric.
- **Runner-level silent-degradation.** The `except Exception` at line 510 is deliberate defensive programming (don't kill a 6-10h campaign over a DB hiccup), but it trades campaign-completion robustness for downstream-trust. Without an explicit retry + final re-check, the trade silently corrupts thesis output.

### Remediation

**A. Runner retries the shift-marker write at least N times before giving up.**

```python
# scripts/run_longitudinal_shift.py:467 — before:
if cycle == args.shift_at and not shift_marked:
    try:
        eval_store.record_cycle_events(...)
        shift_marked = True
    except Exception as exc:
        logger.warning("Failed to write distribution_shift marker: %s", exc)

# after — retry 3 times with jitter:
if cycle == args.shift_at and not shift_marked:
    for attempt in range(3):
        try:
            eval_store.record_cycle_events(
                run_id=run_id, condition="longitudinal",
                task_id=task.task_id, events=[{...}],
                occurred_at=record.timestamp,
            )
            shift_marked = True
            break
        except Exception as exc:
            if attempt < 2:
                logger.warning(
                    "distribution_shift marker write failed (attempt %d/3): %s — retrying",
                    attempt + 1, exc,
                )
                time.sleep(0.5 * (2 ** attempt))
            else:
                logger.error(
                    "distribution_shift marker write FAILED after 3 attempts: %s. "
                    "RQ #3 analysis will be impossible; re-run the campaign "
                    "or manually insert a cycle_events row before Row 19.",
                    exc,
                )
```

**B. End-of-campaign re-check + loud-fail.**

After the per-task loop, before final summary:

```python
if not shift_marked:
    logger.error(
        "CRITICAL: campaign completed without a distribution_shift marker. "
        "Row 19 analysis will silently skip RQ #3. Either re-run this campaign "
        "or manually insert a cycle_events row (event_type='distribution_shift', "
        "occurred_at=<last_phase_a_task_timestamp>) before invoking "
        "analyze_longitudinal.py.",
    )
    # Return non-zero so unattended orchestrators detect the partial run.
    return 4  # new exit code: campaign ran but thesis-critical marker missing
```

**C. Analyzer loud-fail when `_detect_shift_point` returns None for a shift-condition run.**

```python
# scripts/analyze_longitudinal.py:762-764 — before:
shift_cycle = _detect_shift_point(cycles)
if shift_cycle is not None:
    _print_header("Distribution-Shift Analysis — Pillar 3 RQ #3")
    ...

# after — detect shift-campaign vs vanilla-longitudinal via metadata:
run_condition = _infer_run_condition(conn, args.run_id)  # reads evaluation_runs.condition or event tags
shift_cycle = _detect_shift_point(cycles)
is_shift_campaign = run_condition == "longitudinal_shift" or _has_shift_tags(conn, args.run_id)

if is_shift_campaign and shift_cycle is None:
    print(
        "ERROR: run is tagged as longitudinal_shift but no distribution_shift "
        "marker was found in cycle_events. RQ #3 analysis cannot proceed. "
        "Check that the runner completed the shift-marker write.",
        file=sys.stderr,
    )
    conn.close()
    return 5  # new exit code: shift campaign missing marker

if shift_cycle is not None:
    _print_header("Distribution-Shift Analysis — Pillar 3 RQ #3")
    ...
```

The analyzer distinguishes "this is a vanilla longitudinal run (no shift expected)" from "this is a shift campaign with missing marker" and loud-fails only in the second case.

**D. Exit-code propagation.**

Mirror #116's exit-code table: add 5 for "shift marker missing." `main()` returns 5; `sys.exit(main())` at the entrypoint propagates.

**E. Regression test.**

```python
def test_runner_exits_nonzero_when_shift_marker_write_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.registry.evaluation_store.EvaluationStore.record_cycle_events",
        Mock(side_effect=RuntimeError("simulated DB failure")),
    )
    rc = run_longitudinal_shift.main(["--shift-at", "2", "--total", "4"])
    assert rc == 4, "expected exit 4 when shift marker never landed"

def test_analyzer_loud_fails_on_shift_campaign_missing_marker(tmp_path):
    db = _seed_shift_campaign_without_marker(tmp_path)
    rc, stderr = run_analyze_longitudinal(db)
    assert rc == 5
    assert "distribution_shift marker" in stderr
```

### Recommendation

**P0 — blocks Chapter 6 Pillar 3 RQ #3 trustworthiness.** ~60 LOC across runner retry + runner final-check + analyzer loud-fail + 2 regression tests. ~1-1.5h. Must ship before Row 19 analysis feeds Chapter 6.

### Legacy data

Any prior shift-campaign DBs without the marker are unrecoverable for RQ #3 — re-run is required. The fix doesn't attempt a sidecar/best-effort marker recovery; the marker's `occurred_at` is load-bearing for the cycle-bucketing (ext8 #56 resolution), and synthesizing it post-hoc would require re-deriving the last Phase A task's timestamp, which is doable but out of scope for a same-day fix.

---

## Pattern notes

All ten findings fit patterns already flagged in prior extensions:

- **#114 is a test-fixture-vs-production-shape mismatch** (like ext6 #19, parent #1) — the regression test modeled 1 row per run_id; production has 20 rows per run_id; the CTE's semantics differ at those two shapes. The fix-verification guard ("does the regression test actually match production") should be extended: for any `ROW_NUMBER()` / window-function query, the test fixture should seed enough rows per partition to distinguish "row-level" vs "entity-level" ranking.
- **#115, #116, #117, #118 are silent-success exit-code bugs** (like parent #10 — CLI returning exit 0 on execution failure via registry-match). The fix pattern is the same across all four: whenever a subsystem has a distinct failure mode (watchdog abort, incomplete data, wrong-run picked, zero-evidence analysis), promote it to a distinct exit code, wrap the entrypoint with `sys.exit(main())`, and add a pre-computation gate that refuses to proceed rather than defaulting to permissive behavior.
- **#119 is a defensive-coverage gap** — `run_pilot.py` has the watchdog; the longitudinal drivers don't. Whenever a safety mechanism exists in one runner, it should be extracted into a shared helper and applied uniformly to sibling runners. Same code-duplication-hiding-a-bug shape as ext7 #53 (output_contract initialization was in harness but not in longitudinal runners until the shared `build_initial_state` helper landed).
- **#120 is a silent-degradation pattern** (like parent #6, #7, #10, #11-half-B, #14; ext6 #25's fail-open gate) — an exception is swallowed and a narrow default ("fetch failed → None → skip") silently contaminates an aggregate. The fix pattern: whenever a fetch/compute operation can fail transiently, surface the degradation to the caller (via `_fetch_degraded` flag or explicit exception class), let the caller decide whether to retry, exclude from averages, or loud-fail. Never default to "zero data = valid zero" at an aggregation boundary.
- **#121 is a fix-induced cross-invocation state leak** (same shape as ext11 #113 — `metrics_store` persistent path never reset across invocations; ext8 #71 made persistence work WITHIN a run but didn't address BETWEEN runs). Every persistent sidecar file needs an invocation-scope key that captures every input parameter that could legitimately produce a different sample/plan/cache. The fix pattern: derive the key via a hash of the full parameter payload, not the file's logical location alone.
- **#122 is an all-or-nothing loud-fail pattern** (same shape as ext6 #36's original fix — exit 2 on empty, nothing on partial). Whenever a completeness gate is added, ask "what does completeness mean?" — global-empty is the easy case; per-record / per-run / per-scope completeness is usually the thesis-critical case. Gate at the level the downstream analyzer cares about.
- **#123 is a silent-degradation + missing-else-branch pattern** (same shape as ext6 #19's dead CRITICAL branch — the code is present, the gate that should fire it isn't). The fix pattern: whenever a side-effect write is wrapped in a broad except, (a) retry, (b) loud-fail at end-of-scope if it never succeeded, (c) have the consumer explicitly distinguish "this side-effect wasn't expected here" from "this side-effect failed when it should have landed."
- **Silent-success exit-code pattern is now 7 instances in a single extension** (#115, #116, #117, #118, #121, #122, #123). Chapter 4.5 disclosure should flag this as a cross-cutting methodology concern: every analyzer / runner script must have a documented exit-code contract and a "loud-fail when data isn't what we assumed" gate at its entry point. The run-sheet lint introduced in ext7 #45 / ext7 #54 should be extended to verify exit-code non-zero semantics on each entry listed. Graduation proposal for Chapter 4.5: a **distinct exit code per failure class** across the entire pipeline (2=argparse, 3=data-incomplete, 4=data-empty, 5=thesis-critical-marker-missing, 6=execution-failure-post-pipeline, 7=watchdog-abort) so any orchestrator / CI / run-sheet lint can route on the code alone.

**Fix-induced bug count now 7** across the series (ext9 #86 vs ext6 #31; ext11 #109 vs ext8 #78; ext11 #110 vs ext8 #78; ext11 #113 vs ext8 #71; ext12 #114 vs ext7 #46; ext12 #117 vs ext11 #111; ext12 #121 vs ext8 #57). #122 is arguably the 8th (against ext6 #36 — global-empty loud-fail is correct, but partial-empty wasn't covered by the same remediation). The "regression tests must cover BOTH the original bug AND the surfaces the fix touched" meta-finding from ext11 applies verbatim — Chapter 4.5 methodology disclosure should now cite eight instances. The pattern is consistent enough that Chapter 4.5 can graduate from "list the instances" to "name the class": **every remediation that introduces persistent state, scoped SQL, cross-invocation sidecars, or completeness gates must have a regression test proving the fix's scope boundaries**.

**Compounding failure modes.** Two tightly-coupled failure chains exist:

- **Rows 9/10 → 18/19 chain:** #119 (no watchdog) lets a campaign hang → manually killed → partial DB → #117 (selector picks partial run by MAX(timestamp)) → #116 (analyzer accepts partial run without completeness guard) → #118 (zero-evidence KL = 0 reads as convergence) → #123 (shift-marker missing silently omits RQ #3 but run reports "converged") → exit 0 with a false-positive thesis claim. Any one of the five being fixed breaks the chain, but the chain is still dangerous until all five are closed — the path of least resistance (silent-positive) is currently open.
- **Rows 11 + 13 + 16 → 17 chain:** #122 (partial LangFuse generations silently zero whole runs) + #120 (observation-fetch failures silently deflate Context Economy averages) + #121 (sample-plan persistence overrides a new scoped audit) → Row 17 consumes M2/M3/M4 values that each have a silent-under-report shape pointing in different thesis-favorable directions. Closing #122 alone still leaves #120 deflating Row 13; closing #121 alone still leaves #122 zeroing Row 11.

---

## Parent-document integration (pending)

When ext12 lands, fold forward into `security_audit_2026_04_19.md`:

1. Update header: "One hundred thirteen findings → One hundred twenty-three findings; #56-#123 open as of 2026-04-21."
2. Insert TL;DR rows #114-#123 after ext11's #113.
3. Tranche 3 (eval-blocking) gains **nine P0** (#114, #115, #116, #117, #118, #120, #121, #122, #123) and **one HIGH** (#119) — #114 blocks Row 17 (Chapter 6 aggregation); #115 blocks Row 8 (pilot campaign) trustworthiness; #116 + #117 + #118 jointly block Rows 18/19 (longitudinal analysis); #119 blocks unattended Rows 9/10 scheduling; #120 blocks Row 13 (Context Economy) trustworthiness; #121 blocks Row 16 (M2 contamination labeling); #122 blocks Row 11 (token-cost backfill) → Row 17 M4; #123 blocks Row 19's Pillar 3 RQ #3 evidence.
4. Extend "Behavioral regressions expected":
   - (#114) Auto-select CTE groups distinct run_ids via inner `distinct_runs` CTE before ranking; warning text reflects actual selection count.
   - (#115) `run_pilot.py::main()` returns an integer exit code; entrypoint uses `sys.exit(main() or 0)`; watchdog abort → exit 3 (or owner-assigned code).
   - (#116) `analyze_longitudinal.py` gains `--expected-cycles` / `--allow-incomplete` flags; refuses partial DBs by default; exit 3 on partial-without-override.
   - (#117) Run-sheet selector carries `HAVING COUNT(*) >= :expected_rows` completeness filter; raises `SystemExit` when no complete run exists.
   - (#118) `_compute_kl` returns `float('nan')` on empty-vs-empty; `_detect_convergence` treats NaN as streak-reset; `main()` gates on non-trivial signal presence and exits 4 on zero-evidence DBs.
   - (#119) Shared `scripts/_budget_watchdog.py::budget_watchdog` context manager; longitudinal runners wrap per-task `pipeline.invoke` and enforce campaign-level deadline; non-zero exit codes on timeout.
   - (#120) `extract_trace_context` returns `_fetch_degraded=True` when every observation fetch failed; aggregate pass excludes degraded rows with a WARNING count; optional per-observation retry with exponential backoff.
   - (#121) `_sample_plan_path` keyed on SHA-256 hash of scope + sampling parameters (`run_ids`, `since`, `conditions`, `types`, `per_stratum`, `seed`); zero-intersection resume triggers `SystemExit(3)` with scope-mismatch message; new `--force-fresh-plan` CLI flag.
   - (#122) `backfill_token_costs.py` gains per-run completeness check: non-static runs with `n_generations=0` trigger exit 3 unless `--allow-partial` passed; per-condition coverage breakdown printed pre-write; `main()` wrapped with `sys.exit(main() or 0)`.
   - (#123) `run_longitudinal_shift.py` retries distribution-shift marker write 3× with jitter; final end-of-campaign re-check returns exit 4 if marker never landed. `analyze_longitudinal.py` distinguishes "vanilla longitudinal run" from "shift campaign with missing marker" via condition/tag metadata; loud-fails with exit 5 on the latter.
5. End-of-audit note: "Extended 2026-04-21 (twelfth extension) with findings #114-#123 (detail sections in `security_audit_2026_04_19_ext12.md`). #114 is the fifth fix-induced regression in the series (against ext7 #46's balanced per-condition auto-select); #117 is the sixth (against ext11 #111's GROUP BY rewrite — correct-at-SQL-level, incomplete at campaign-level); #121 is the seventh (against ext8 #57's sample-plan persistence — correct-within-a-scope, leaky across scopes); #122 is the eighth (against ext6 #36's global-empty loud-fail — correct for total absence, silent on partial). #115 + #116 + #118 + #121 + #122 + #123 are silent-success exit-code bugs (7 instances in a single extension); #119 is a defensive-coverage gap where `run_pilot.py`'s watchdog wasn't ported to the longitudinal drivers; #120 is a silent-degradation pattern where blanket exception swallow + zero-valued fallback pollutes averages. Two compounding failure chains: Rows 9/10 → 18/19 (#116 + #117 + #118 + #119 + #123) and Rows 11 + 13 + 16 → Row 17 (#120 + #121 + #122). All ten eval-blocking; all must close before Phase 5 Chapter 6 numbers can be trusted."

---

**End of extension.**
