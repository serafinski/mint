# Security / Robustness Audit — 2026-04-19, Seventh Extension (#44-#55)

**Parent document:** [`security_audit_2026_04_19.md`](./security_audit_2026_04_19.md) — #1-#18 all ✅ FIXED 2026-04-20.
**Sibling extensions:**
- [`security_audit_2026_04_19_ext4.md`](./security_audit_2026_04_19_ext4.md) — #12-#14 ✅ (folded into parent).
- [`security_audit_2026_04_19_ext5.md`](./security_audit_2026_04_19_ext5.md) — #15-#18 ✅ (folded into parent).
- [`security_audit_2026_04_19_ext6.md`](./security_audit_2026_04_19_ext6.md) — #19-#43 open as of 2026-04-20.

**Why this file exists (separate from ext6).** Two post-remediation regressions surfaced 2026-04-20 after the ext6 analyzer-scoping batch (#38/#40/#41) was drafted. Both are narrow, specific, and stem from two layers of the fix landing without the third:

1. **#44 — `analyze_results.py` partial-fix:** the module-level `_SCOPE_SQL` variable is set correctly at startup and applied to 8 of the analyzer's 10 queries — but the **M2 contamination** and **M3 Gini** queries silently skip it. Chapter 6's Row 17 still reports mixed-campaign M2/M3 even when the user explicitly passes `--run-ids`.
2. **#45 — `run_sheet.md` invocations don't exercise the `--run-ids` fix:** rows 60 + 62 call `analyze_compression_loss.py` (Row 14) and `audit_contamination.py` (Row 16) without `--run-ids`, despite both scripts now exposing the flag with explicit "unsafe on multi-run DBs" / "wastes expert labeling time" warnings in their `--help` text. The fixes are code-present but procedure-absent.

These are logged as a separate extension (rather than appended to ext6) because they are regression findings against ext6's own remediations — they describe gaps in the fix, not new bugs in pre-fix code. Future audits of this shape (post-remediation verification) should land in their own files so the audit history cleanly separates "bugs we found" from "bugs our fixes missed."

**Status at time of write (2026-04-20):** all twelve **open**.

| # | Severity | Scope | Impact |
|---|---|---|---|
| 44 | **P0** | `analyze_results.py` Gini + M2 queries | ✅ **FIXED 2026-04-20.** `print_gini_summary` per-run Gini query (line ~609) + `print_contamination_rate` M2 query (line ~956) both now interpolate `f"…{_SCOPE_SQL}"` into their WHERE clauses. Regression guards at `tests/unit/test_analyze_results.py::test_m3_gini_respects_scope_sql` + `test_m2_contamination_respects_scope_sql` + a new AST lint `test_every_analyzer_query_applies_scope_or_is_exempt` that walks every `conn.execute(...)` in the analyzer and asserts scoped-table queries reference `_SCOPE_SQL` (unless explicitly exempt: `_resolve_scope`, `_count_check`, `_print_header_counts`, or bare `SELECT 1 FROM t LIMIT 1` existence probes). |
| 45 | **P0** | `run_sheet.md` rows 60 + 62 | ✅ **FIXED 2026-04-20.** Added "Scoping convention" block above the Phase 4/5 table documenting `$CAMPAIGN_RUN_IDS` + `$LONGITUDINAL_RUN_ID` + `$SHIFT_RUN_ID` env vars with the one-liner query for discovering them. Rows 14 + 16 + 17 now pass `--run-ids $CAMPAIGN_RUN_IDS`. Rows 18 + 19 simultaneously fixed for ext7 #55. Regression guards at `tests/unit/test_run_sheet.py::test_scoping_convention_block_present` + `test_every_db_reader_row_passes_scope_flag` — AST-style lint walks every `| N | \`cmd\` |` row, looks up the script by filename in `_DB_READERS_TO_SCOPE`, asserts at least one accepted scope flag is present. Dropping a scope flag in any future edit regresses the lint. |
| 46 | **P0 (CRITICAL)** | `analyze_results.py` auto-select of 4 run_ids | ✅ **FIXED 2026-04-20.** `_resolve_scope` rewritten: auto-select is now **balanced per condition** via `ROW_NUMBER() OVER (PARTITION BY condition ORDER BY timestamp DESC)`, default cap = 5 per condition (matches run_sheet.md `--n-runs 5`). New `--per-condition-cap` CLI flag to override. Loud-fail with `sys.exit(2)` if the selected set doesn't cover every condition present in the DB. New `_DEFAULT_PER_CONDITION_CAP = 5` module constant. Regression guards at `tests/unit/test_analyze_results.py::test_auto_select_covers_all_conditions_in_canonical_eval_shape` (20 run_ids × 4 conditions → all 4 covered, pre-fix would have picked 1) + `test_auto_select_respects_per_condition_cap` + `test_auto_select_no_scope_needed_when_few_runs` (≤ condition_count → legacy empty scope). Updated pre-existing `test_resolve_scope_auto_selects_latest_when_many_runs_with_warning` to reflect per-condition semantics (was asserting "4 most recent total"). |
| 47 | **P0 (CRITICAL)** | Row 13 env mismatch | ✅ **FIXED 2026-04-20.** `scripts/analyze_context_utilization.py` argparse `--environment` default flipped from `production` to `default` to match runner emission convention (`LANGFUSE_TRACING_ENVIRONMENT=default`). New `--allow-empty` flag opts out of the loud-fail; default behavior now exits 2 when the fetch returns zero traces across every agent — converts invisible-empty T3 Chapter 6 section into actionable error. Regression guards at `tests/unit/test_analyze_context_utilization.py::test_default_environment_matches_runner_emission_convention` + `test_fails_loud_on_zero_traces_across_all_agents` + `test_allow_empty_suppresses_loud_fail`. |
| 48 | HIGH (methodology) | Condition-order randomization gap | ✅ **FIXED 2026-04-20.** Closed the docstring-vs-implementation gap. Harness protocol docstring (`tests/evaluation/harness.py:13-25`) rewritten: determinism is the default (easier to reproduce + debug), with explicit Chapter 4.5 disclosure that time/order confounders (LangFuse latency drift, Docker cache warmth) are a known potential bias on M5 latency. New `run_pilot.py --shuffle-conditions` flag opts into deterministic-shuffled order (keyed on `base_seed=42` so a given seed always reproduces the same sequence) — the methodology-pure path when Chapter 4.5 wants the mitigation. Pragmatic compromise over a full loop-inversion (outer=run, inner=shuffled-condition): addresses the docstring-vs-code gap + adds the knob, without restructuring the 18h eval path. |
| 49 | **P0 (CRITICAL)** | Phase-4 eval DB not isolated | ✅ **FIXED 2026-04-20.** `scripts/run_pilot.py` gained `--db` (default: fresh `data/eval/metrics_<timestamp>.duckdb`), `--fresh-db` (wipes existing path), `--append` (opts into append). Refuses with exit 2 on existing non-empty DB when neither flag passed; `--fresh-db` + `--append` mutually exclusive. Legacy shared path reachable only via `--db legacy`. `docs/run_sheet.md` adds a "Campaign DB isolation" block documenting `$CAMPAIGN_DB` env var; rows 8/11/14/16/17 all pass `--db $CAMPAIGN_DB`. Regression guards at `tests/unit/test_run_pilot_db_isolation.py` (4 tests covering refuse-to-append, fresh-db-wipes, mutual-exclusion, timestamped-default). |
| 50 | **P0 (CRITICAL)** | `invocation_events` missing `is_pilot` column | ✅ **FIXED 2026-04-20.** `invocation_events` schema gained `is_pilot BOOLEAN` column (nullable on migrate, always written explicitly by `record_invocation_events`). Idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` migration lands in `_MIGRATIONS`. On EvaluationStore open, a `_INVOCATION_IS_PILOT_BACKFILL_SQL` UPDATE JOINs legacy rows to `evaluation_runs` so pre-fix DBs are lossless-migrated. `EvaluationStore.record_invocation_events` takes an `is_pilot: bool = False` kwarg; `tests/evaluation/cycle_flush.py::flush_task_cycle_state` threads it; harness `_execute_run` passes `is_pilot=is_pilot` (pilot vs full); longitudinal runners pass `is_pilot=False` (never pilot). Gini query in `print_gini_summary` now filters `WHERE NOT COALESCE(is_pilot, FALSE)`. Regression guards at `tests/unit/test_analyze_results.py::test_invocation_events_schema_has_is_pilot_column` + `test_record_invocation_events_threads_is_pilot` + `test_m3_gini_excludes_pilot_invocations` + `test_invocation_events_is_pilot_backfill_from_evaluation_runs` (end-to-end: legacy-shape DB → open → backfill fires → pilot rows correctly tagged). |
| 51 | **P0** | Run sheet "parallelizable" claim is false | ✅ **FIXED 2026-04-20.** Retracted the "all three can run concurrently" claim from `docs/run_sheet.md` Phase 4 header — now explicitly labelled **SERIAL** with rationale (shared `data/eval/chroma`, `src/mcp_tools`, `data/eval/skills`, snapshot dirs). Wall-clock updated from "~18 h parallel" to "~30–38 h serial". Defense-in-depth: new `scripts/_eval_registry_lock.py::acquire_runner_lock(runner_name, data_dir)` uses `fcntl.flock(LOCK_EX | LOCK_NB)` on `data/eval/.eval_runner.lock`; second concurrent runner exits code 2 with a pointer to this finding. Wired into all three runners (`run_pilot.py`, `run_longitudinal.py`, `run_longitudinal_shift.py`) at the top of the respective setup blocks — BEFORE any rmtree on shared dirs. Distinct data_dir overrides get distinct lock files (Chapter-7 genuinely-parallel path still works when shared-state isn't the issue). Regression guards at `tests/unit/test_eval_registry_lock.py` (4 tests: first-acquires, second-exits-2, released-reacquirable, distinct-dirs-no-contention). |
| 52 | P1 | No completeness guard in Row 17 analyzer | ✅ **FIXED 2026-04-20.** `scripts/analyze_results.py::main` now runs a per-condition count check after scope resolution. Default expected shape: `--expected-conditions 4 × --expected-tasks 20 × --expected-runs-per-condition 5 = 400`. Mismatches collect into an `issues` list and exit with code 2 + stderr "dataset appears incomplete" message unless `--allow-incomplete` is passed. Override mode sets module-level `_COMPLETENESS_BANNER` which `print_condition_table` interpolates into every Chapter-6 table title — partial-data runs are in-band marked rather than silent. Regression guards at `tests/unit/test_analyze_results.py::test_completeness_banner_empty_when_not_incomplete` + `test_print_condition_table_includes_banner_when_set` + `test_completeness_guard_refuses_partial_dataset` (1-condition × 10-row DB → exit 2) + `test_completeness_guard_allow_incomplete_proceeds_with_banner` (override → banner set). |
| 53 | **P0 (CRITICAL)** | Longitudinal runners skip `output_contract` initialization | ✅ **FIXED 2026-04-20.** New `src/orchestrator/task_state_builder.py::build_initial_state(task, allow_network=...)` helper threads `output_contract` unconditionally. Both longitudinal runners (`scripts/run_longitudinal.py:211`, `scripts/run_longitudinal_shift.py:320`) + `tests/evaluation/harness.py:654` now use the shared builder instead of constructing `TaskState` directly. Regression guard `tests/unit/test_task_state_builder.py::test_longitudinal_runners_use_shared_builder` greps the three runner files for `TaskState(task_description=` and fails if any direct construction is introduced — the builder is now the one canonical path. +3 helper unit tests covering contract threading (T07 with `required_files`), empty contract preservation (T01), and `allow_network` propagation. |
| 54 | **P0 (CRITICAL)** | Rows 13 + 15 lack `--session-ids` in run sheet | ✅ **FIXED 2026-04-20.** Rows 13 + 15 now pass `--session-ids $CAMPAIGN_SESSION_IDS`. The env-var contract (`CAMPAIGN_SESSION_IDS`) is documented in the Phase 4/5 scoping block alongside `CAMPAIGN_RUN_IDS`. Run-sheet lint extended (`tests/unit/test_run_sheet.py::test_every_langfuse_analyzer_row_passes_scope_flag` + `test_campaign_session_ids_convention_present`) — `_LANGFUSE_ANALYZERS_TO_SCOPE` map covers `analyze_context_utilization.py` + `analyze_tier2_probes.py`. Dropping `--session-ids` on a future run-sheet edit regresses the lint. |
| 55 | HIGH | Rows 18 + 19 unpinned to specific longitudinal run_id | ✅ **FIXED 2026-04-20 (bundled with #45).** Rows 18 + 19 now pass `--run-id $LONGITUDINAL_RUN_ID` / `--run-id $SHIFT_RUN_ID`. The env-var contract is documented in the same Phase 4/5 scoping-convention block. Same run-sheet lint (`test_every_db_reader_row_passes_scope_flag`) catches future regressions — `analyze_longitudinal.py` is in the `_DB_READERS_TO_SCOPE` map requiring either `--run-id` or `--run-ids`. |

---

## Issue #44 — `analyze_results.py` Gini (M3) and Contamination (M2) queries bypass `_SCOPE_SQL`; Row 17 reports falsified metrics under `--run-ids` (P0)

### Summary

Ext6 #38 added a module-level `_SCOPE_SQL` variable to `analyze_results.py`, populated at startup from `--run-ids` / `--since` CLI args, and applied it to the per-condition summary, per-task summary, failure breakdown, classification accuracy, core-work classification, and chain-stats queries. **Two queries were not updated**:

1. **M3 Gini per-run rows** at `analyze_results.py:607-614` — fetches every row from `invocation_events` with no `WHERE` clause at all (beyond GROUP BY).
2. **M2 Contamination** at `analyze_results.py:954-967` — filters by `human_label IS NOT NULL AND NOT is_pilot` but **does not** append `_SCOPE_SQL`.

The other 8 queries in the file correctly interpolate `{_SCOPE_SQL}` into their WHERE clauses. The fix was 80%-complete but silently regressed on exactly the two Chapter-6-headline metric sections.

### Where

**Gini (M3) — missing `_SCOPE_SQL`:**
```python
# scripts/analyze_results.py:607-614
per_run_rows = conn.execute(
    """
    SELECT condition, run_id, capability_id, COUNT(*) AS invocations
    FROM invocation_events
    GROUP BY condition, run_id, capability_id
    ORDER BY condition, run_id, capability_id
    """
).fetchall()
```
No `WHERE` filters at all. Every row in `invocation_events` enters the Gini computation regardless of which run wrote it.

**Contamination (M2) — missing `_SCOPE_SQL`:**
```python
# scripts/analyze_results.py:954-967
rows = conn.execute(
    """
    SELECT condition,
           SUM(CASE WHEN human_label = 'correct'      THEN 1 ELSE 0 END) AS n_correct,
           SUM(CASE WHEN human_label = 'contaminated' THEN 1 ELSE 0 END) AS n_contaminated,
           SUM(CASE WHEN human_label = 'partial'      THEN 1 ELSE 0 END) AS n_partial,
           COUNT(*)                                                      AS n_sampled
    FROM evaluation_runs
    WHERE human_label IS NOT NULL
      AND NOT is_pilot
    GROUP BY condition
    ORDER BY condition
    """
).fetchall()
```
`_SCOPE_SQL` not appended to the WHERE clause — so any `human_label`-bearing row across any campaign counts. Especially pernicious because M2 labels are expensive human input; cross-campaign pooling means labels intended for campaign A get consumed by campaign B's aggregate.

**Reference — queries that correctly apply `_SCOPE_SQL`:**
- Line 195 — `WHERE is_pilot = FALSE AND ({task_filter}){_SCOPE_SQL}`
- Line 223 — `WHERE is_pilot = FALSE{_SCOPE_SQL}`
- Line 246 — `WHERE is_pilot = FALSE AND failure_reason != 'none'{_SCOPE_SQL}`
- Line 527 — `WHERE is_pilot = FALSE{_SCOPE_SQL}`
- Line 760 — `WHERE is_pilot = FALSE AND condition != 'static'{_SCOPE_SQL}`
- Line 860 — `AND NOT is_pilot{_SCOPE_SQL}`
- Line 873 — `AND NOT is_pilot{_SCOPE_SQL}`

The two missing sites stand out precisely because 8 siblings got it right.

### Reproduction

Synthetic DB seeded with a mix of old-campaign and new-campaign rows, run Row 17 with explicit scope:

```bash
# DB seeded with:
#   run_id="old_full"  — 3 rows, 2 labeled contaminated, invocation_events 2 runs worth
#   run_id="new_full"  — 3 rows, 0 labeled, invocation_events 1 run worth

$ uv run scripts/analyze_results.py --run-ids new_full

# Expected (scoped to new_full):
#   total rows          = 3
#   M2 n_sampled        = 0 (nothing labeled in new_full)
#   M3 Gini             = over 1 run's invocation_events

# Actual (M2 + M3 ignore --run-ids):
#   total rows          = 3              ✓ scoped (from line 195)
#   M2 n_sampled        = 2              ✗ includes old_full's labels
#   M3 Gini             = over 2 runs    ✗ includes old_full's invocations
```

User's repro matches verbatim: `scoped total rows = 3, but M2 counted 6 and M3 used 2 runs`. (User had 6 M2 counts with 3 labeled each old+new; numbers vary with seed data but the asymmetry is the same.)

### Threat model / impact

- **Chapter 6 M2 and M3 tables silently falsified when the user follows the now-recommended `--run-ids` practice.** The user does the right thing; the script still lies.
- **Direction of bias: depends on DB contents.** If prior campaigns were "worse" (more contaminated, less balanced invocations), M2/M3 are dragged toward "worse" regardless of the current campaign's actual state. If prior campaigns were "better," dragged the other way. Un-signposted either way — reader of Chapter 6 has no way to know.
- **Compounds with #41.** #41 fixed `audit_contamination.py`'s sampling pool scoping. But the labels produced by that (now-scoped) sampling feed into #44's (still-unscoped) M2 aggregate. So the audit-sample side is correct, the aggregate side is wrong — producing a silent mismatch between "what the human labeled" and "what Chapter 6 reports."
- **M3 Gini is the Pillar 3 RQ #1 / RQ #2 headline metric at the non-longitudinal granularity.** Its scope-breakage is symmetric with ext5 #18 (longitudinal key-collision) and ext6 #24 (longitudinal run_id mixing) — all three analyzer families had the same class of flaw; #44 is the one that slipped through.

### Remediation

**A. Append `_SCOPE_SQL` to both queries.**

```python
# scripts/analyze_results.py:607 — before:
"SELECT condition, run_id, capability_id, COUNT(*) AS invocations "
"FROM invocation_events "
"GROUP BY condition, run_id, capability_id "
"ORDER BY condition, run_id, capability_id"

# after — filter before GROUP BY. Note: invocation_events uses the SAME run_id
# column as evaluation_runs so the same scope SQL applies verbatim:
f"SELECT condition, run_id, capability_id, COUNT(*) AS invocations "
f"FROM invocation_events "
f"WHERE 1=1{_SCOPE_SQL} "
f"GROUP BY condition, run_id, capability_id "
f"ORDER BY condition, run_id, capability_id"
```

```python
# scripts/analyze_results.py:954 — before:
"WHERE human_label IS NOT NULL AND NOT is_pilot "

# after:
f"WHERE human_label IS NOT NULL AND NOT is_pilot{_SCOPE_SQL} "
```

Two lines of diff; exactly the same shape the siblings already use.

**B. Regression-guard: grep for scope-naïve queries.**

Add a one-shot test (runs in CI) that enumerates every `conn.execute(...)` call in `analyze_results.py` and asserts either (a) the query contains `_SCOPE_SQL`, or (b) the query is explicitly whitelisted in a small allowlist (e.g. the `SELECT COUNT(*)` sanity check at line 1052 that runs BEFORE scope resolution):

```python
def test_every_analyzer_query_applies_scope_or_is_explicitly_exempt():
    import ast
    src = Path("scripts/analyze_results.py").read_text()
    tree = ast.parse(src)
    SCOPE_EXEMPT = {
        "_count_check",      # startup sanity check, runs before scope is resolved
        "_resolve_scope",    # the scope-resolution call itself
    }
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute"):
            # Find the enclosing function; skip whitelisted
            ...
            sql_literal = _extract_sql_string(node.args[0])
            if "_SCOPE_SQL" not in sql_literal:
                raise AssertionError(
                    f"{enclosing_fn}:{node.lineno} — query missing _SCOPE_SQL; "
                    f"add it or whitelist in SCOPE_EXEMPT."
                )
```

Catches any future query added without scope. Same pattern applies to the other five analyzers in the ext6 #24/#38/#39/#40/#41/#42 batch — this test is reusable.

**C. Cross-check regression test per metric.**

```python
def test_m2_contamination_respects_run_ids(tmp_path):
    """M2 counts only labels from the specified run_ids, not all DB labels."""
    db = seed_mixed_campaign_db(tmp_path, old_labels=2, new_labels=0)
    # Invoke the analyzer with --run-ids new_full; assert M2 n_sampled == 0
    ...

def test_m3_gini_respects_run_ids(tmp_path):
    """M3 Gini computed only over invocation_events from the specified run_ids."""
    ...
```

### Recommendation

**Land immediately — blocking any Chapter 6 Row 17 invocation.** Two-line fix + two regression tests + one AST-based lint for future-proofing. ~20 min. Must ship before the first post-fix re-run of `analyze_results.py`.

### Legacy data

Not applicable — the data is correct; only the aggregation is scoped-wrong. Post-fix re-run of `analyze_results.py` against the same DB produces corrected Row 17 output with no DB-level changes.

---

## Issue #45 — `run_sheet.md` rows 60 + 62 omit `--run-ids` despite scripts warning "unsafe on multi-run DBs" (P0)

### Summary

Ext6 #40 and #41 landed `--run-ids` CLI args on `analyze_compression_loss.py` and `audit_contamination.py`. Both scripts' `--help` text explicitly warns the default behavior is unsafe for multi-run DBs:

- `analyze_compression_loss.py:464-467`: `"--run-ids ... default: all rows in the DB — unsafe on multi-run DBs; pass explicitly for Chapter 6."`
- `audit_contamination.py:562-567`: `"--run-ids ... default: all unlabeled non-pilot rows across every campaign in the DB — cross-campaign pooling wastes expert labeling time; pass explicitly for Chapter 6 M2."`

But `docs/run_sheet.md` rows 60 + 62 still invoke them unscoped:

```
| 60 | uv run scripts/analyze_compression_loss.py    | **T3 aggressive-compression half** ... |
| 62 | uv run scripts/audit_contamination.py          | **M2** — manual contamination audit    |
```

The fixes are code-present but procedure-absent. Following the run sheet step-by-step reproduces ext6 #40 + #41's original symptoms against the same DB — cross-campaign pooling on Rows 14 + 16, then Row 17's M1/M4/M5 columns (which DID get the `--run-ids` fix via #38) are correctly scoped, but Row 17's M2 column then reads labels produced by Row 16's cross-campaign sample AND further mis-aggregates them via #44. Three layers of scope inconsistency compound.

### Where

- **Run sheet rows:**
  - `docs/run_sheet.md:60` — `uv run scripts/analyze_compression_loss.py`.
  - `docs/run_sheet.md:62` — `uv run scripts/audit_contamination.py`.
- **Scripts that expose `--run-ids` with warning text:**
  - `scripts/analyze_compression_loss.py:462-467` — argparse entry.
  - `scripts/audit_contamination.py:561-567` — argparse entry.

### Reproduction

Run the run sheet against any DB that contains multiple non-pilot campaigns:

```bash
$ uv run scripts/analyze_compression_loss.py     # no --run-ids
# → emits compression-loss numbers pooled across every non-pilot run.
# Help text already warned "unsafe on multi-run DBs" — but the user
# copy-pasted the run sheet's exact command, so they never saw --help.

$ uv run scripts/audit_contamination.py          # no --run-ids
# → begins labelling session with samples drawn from every campaign
#   in the DB. User spends 2-4h labelling rows some of which belong
#   to experiments that Chapter 6 won't reference.
```

### Why fixes-without-procedure is its own finding

The three-layer strict-gate pattern (§12 findings #14/#15/#16, and echoed across multiple ext6 findings) says that silent fallback is the core anti-pattern. A CLI default that emits correct-looking output while silently pooling mixed campaigns IS a silent fallback — the script ran, exit code 0, numbers printed, but the numbers are wrong. The `--help` warning doesn't change the default; it only helps users who happen to read `--help`. The run sheet is the primary interface for Chapter 6 reproduction; if it doesn't set `--run-ids`, the warning is rarely seen.

This is cross-cutting observation #5 (silent-degradation paths) from the parent audit, re-stated at the procedure layer: "default behavior is unsafe" is a documentation-only mitigation against a correctness bug.

### Threat model / impact

- **Chapter 6 reproducibility from the run sheet is broken.** A reader following `docs/run_sheet.md` verbatim gets falsified M2 + compression-loss numbers, which then feed #44's falsified Row 17.
- **Compounds with #44.** #44: `analyze_results.py` M2 aggregation ignores scope. #45: Rows 14 + 16 feeding data into the DB that #44 then mis-aggregates. The two together close the loop: sampling is unscoped (Row 16), aggregation is unscoped (Row 17 M2), result is doubly-wrong.
- **Compounds with #41.** #41's `audit_contamination.py` fix added `--run-ids` but the run sheet never exercises it. The fix lands in the codebase but never in the methodology — which means every Chapter 6 labeling session continues to hit the pre-fix sampling pool.
- **Direction of bias: spends human-labeling budget on irrelevant campaigns.** The M2 audit is the single most expensive methodology step (2-4h human time per run sheet row 62). Mis-directing that budget is both methodology-wrong AND person-wasteful.

### Remediation

**A. Update `run_sheet.md` to require `--run-ids` on rows 14, 16, 17, 18, 19 (and any sibling row that consumes scoped data).**

Add a small "scope convention" block at the top of the run sheet:

```markdown
## Scoping convention (Phase 5)

Chapter 6 Rows 13-19 all read `data/eval/metrics.duckdb`. Set a campaign
session id at the top of the run sheet:

```bash
export CAMPAIGN_ID="eval_2026-04-20_full_final"
export CAMPAIGN_RUN_IDS="full_00_abc123 no_verification_00_def456 no_optimization_00_ghi789 static_00_jkl012"
```

Every Row below that reads the DB MUST pass `--run-ids $CAMPAIGN_RUN_IDS`.
```

Then update every row that's DB-reading to include `--run-ids $CAMPAIGN_RUN_IDS`:

```markdown
| 14 | `uv run scripts/analyze_compression_loss.py --run-ids $CAMPAIGN_RUN_IDS`  | ... |
| 16 | `uv run scripts/audit_contamination.py --run-ids $CAMPAIGN_RUN_IDS`       | ... |
| 17 | `uv run scripts/analyze_results.py --run-ids $CAMPAIGN_RUN_IDS`           | ... |
| 18 | `uv run scripts/analyze_longitudinal.py --run-id $LONGITUDINAL_RUN_ID`    | ... |  # single run per longitudinal
| 19 | `uv run scripts/analyze_longitudinal.py --db ... --run-id $SHIFT_RUN_ID`  | ... |
```

The env-var pattern is better than a hardcoded literal in the run sheet because it lets a future campaign re-use the same procedure by just redefining the variable.

**B. Script-side defence: make `--run-ids` required when `is_pilot=FALSE` rows from multiple campaigns exist in the DB.**

```python
# scripts/analyze_compression_loss.py — at startup:
if args.run_ids is None and args.since is None:
    distinct = conn.execute(
        "SELECT COUNT(DISTINCT run_id) FROM evaluation_runs WHERE NOT is_pilot"
    ).fetchone()[0]
    if distinct > 4:
        print(
            "ERROR: DB contains multiple non-pilot campaigns. "
            "Pass --run-ids to scope, or --since to use a time window.",
            file=sys.stderr,
        )
        sys.exit(2)
```

Same defence as #38's auto-select-latest-with-WARN, but hardened to an exit when the ambiguity is too high to auto-resolve. Apply to `analyze_compression_loss.py`, `audit_contamination.py`, and `analyze_results.py`.

Combining (A) procedure-level and (B) script-level defences closes the loop: the run sheet documents the right command, and the script refuses to run the wrong one.

**C. CI smoke: run-sheet dry-run parser.**

```python
def test_run_sheet_invocations_pass_scope_when_required():
    """Every DB-reading row in run_sheet.md must pass --run-ids or --since."""
    import re
    rows = _parse_run_sheet_table(Path("docs/run_sheet.md").read_text())
    DB_READERS = {"analyze_compression_loss.py", "audit_contamination.py",
                  "analyze_results.py", "analyze_longitudinal.py"}
    for row_num, cmd in rows:
        script_name = cmd.split("/")[-1].split(" ")[0]
        if script_name in DB_READERS:
            assert "--run-ids" in cmd or "--since" in cmd or "--run-id" in cmd, (
                f"Row {row_num}: DB-reading script {script_name} invoked without "
                f"scope argument.\n  {cmd}"
            )
```

Prevents silent drift back to unscoped invocations in future run sheet edits.

### Recommendation

**Land (A) + (B) + (C) together before any Phase 5 run sheet execution.** Documentation-only fixes are fragile; pair every run-sheet edit with a script-side refusal to run without scope. ~1h combined.

### Legacy data

Not applicable — the issue is procedural, not in the data. Re-running the run sheet post-fix with `--run-ids` produces correctly-scoped output from the same DB.

---

## Issue #46 — `analyze_results.py` default `_resolve_scope` auto-selects only 4 run_ids; run-sheet eval has 20, so 80% of the campaign is silently dropped (P0, CRITICAL)

### Summary

Ext6 #38's remediation added an auto-select fallback: when the user doesn't pass `--run-ids` or `--since`, and the DB has >4 non-pilot run_ids, `_resolve_scope` picks "the 4 most recent" and WARNs on stderr. The comment at `analyze_results.py:142` justifies the number 4 as "one per condition is the canonical eval shape." That assumption is wrong for the thesis's actual eval shape:

- **Run sheet at `docs/run_sheet.md:45` specifies 20 run_ids** (4 conditions × 5 runs).
- **Row 17 at `docs/run_sheet.md:63` invokes the analyzer with no scope args**, so the auto-select path fires.
- **`_resolve_scope` picks 4 run_ids strictly by `MAX(timestamp) DESC`** (`analyze_results.py:163-164`) — no per-condition balancing.

With 4 conditions × 5 runs = 20 run_ids, the 4 most recent by timestamp are usually 4 runs of the LAST-EXECUTED condition (whichever the runner finished last — `static` under the current fixed order, see #48). The analyzer reports a "campaign" consisting of 4 runs of 1 condition instead of 20 runs across 4 conditions. The WARN stderr line does announce the 4 auto-selected IDs, but the human reader of Chapter 6 tables sees numbers that look correct — 80 rows, one condition, zeros across the other three conditions — and has no signal that 320 rows were silently dropped.

### Where

- **Auto-select logic:** `scripts/analyze_results.py:142` (comment), `scripts/analyze_results.py:160-179` (implementation, specifically line 169: `latest = [r[0] for r in available[:4]]`).
- **CLI entry (no default scope enforced):** `scripts/analyze_results.py:1031` — `--run-ids` optional with `default=None`, triggering the auto-select path when unset.
- **Run sheet eval shape:** `docs/run_sheet.md:45` — "4 conditions × 5 runs each = 20 non-pilot run_ids".
- **Run sheet Row 17 invocation:** `docs/run_sheet.md:63` — `uv run scripts/analyze_results.py` with no args.

### Reproduction

```bash
# Seed a synthetic DB with the run sheet's canonical eval shape:
#   5 runs × 4 conditions × 20 tasks = 400 rows
#   Timestamps: conditions run sequentially, `static` most recent.

$ uv run scripts/analyze_results.py  # no args — triggers auto-select

# Stderr:
# WARNING: DB contains 20 distinct non-pilot run_ids; auto-selecting the 4
# most recent: ['static_04_xyz', 'static_03_wvu', 'static_02_tsr', 'static_01_qpo'].
# Pass --run-ids to override.

# Stdout (condensed):
# Total non-pilot records: 80        ← should be 400
# Condition breakdown:
#   static: 80
#   full: 0                           ← all zeros
#   no_verification: 0
#   no_optimization: 0
```

User's repro matches verbatim: "Total non-pilot records: 80 (not 400), with only one condition represented."

### Why the auto-select premise is wrong

The comment at line 142 says:
> "Auto-select the 4 most recent run_ids (one per condition is the canonical eval shape)"

"One per condition" would be 1 run per condition × 4 conditions = 4 run_ids — correct for an interactive debug run producing exactly one run per condition. But `run_sheet.md` specifies `--n-runs 5` per condition for the statistical replication Chapter 6 relies on (Fisher's exact + Holm + Clopper-Pearson CIs all assume independent replicates). The canonical eval shape is **5 per condition, not 1 per condition**. The auto-select's hardcoded 4 is off by a factor of 5.

### Threat model / impact

- **Chapter 6 headline numbers silently wrong when user follows the run sheet as written.** Following the run sheet verbatim → 80 rows analyzed, 3 conditions dropped → success rate for those 3 conditions reads as 0/0 = undefined (division by zero protection elsewhere in the file produces blank cells).
- **Warning-on-stderr insufficient.** The WARN prints to stderr; the user runs the analyzer, scrolls past stderr, copies the stdout table into the thesis. Stdout looks plausible. The mismatch surfaces only if the reviewer independently counts rows in the DB.
- **Compounds with #44 + #45.** The three together: #45 means rows 14 + 16 pool historical campaigns into the DB; #46 means row 17 auto-selects the wrong 4 (possibly from one condition); #44 means row 17's M2 + M3 sub-queries ignore that scope anyway. Every Chapter 6 number has a different wrong-source path.
- **Direction of bias: condition-dependent.** Whichever condition was executed last (under fixed order — see #48, `static`) becomes the sole sample. If `static` outperforms the JIT conditions in some metric, Chapter 6 under-reports JIT; if `static` underperforms, over-reports. Worst direction depends on which metric the reviewer probes.

### Remediation

**A. Change auto-select from "4 most recent" to "N most recent balanced across conditions."**

```python
# scripts/analyze_results.py: replace lines 160-179 with:
PER_CONDITION_CAP = 5  # Match run_sheet.md's --n-runs default.

latest_per_condition = conn.execute(
    """
    WITH ranked AS (
        SELECT run_id, condition, timestamp,
               ROW_NUMBER() OVER (PARTITION BY condition ORDER BY timestamp DESC) AS rn
        FROM evaluation_runs
        WHERE is_pilot = FALSE
    )
    SELECT DISTINCT run_id FROM ranked WHERE rn <= ?
    ORDER BY run_id
    """,
    [PER_CONDITION_CAP],
).fetchall()
latest = [r[0] for r in latest_per_condition]

if len(latest) == 0:
    return ""  # Empty DB or all pilot — no scope needed.

# Still warn if the number differs from what we got:
total_available = conn.execute(
    "SELECT COUNT(DISTINCT run_id) FROM evaluation_runs WHERE is_pilot = FALSE"
).fetchone()[0]
if total_available > len(latest):
    print(
        f"WARNING: DB contains {total_available} distinct non-pilot run_ids; "
        f"auto-selecting the {PER_CONDITION_CAP} most recent per condition "
        f"({len(latest)} total). Pass --run-ids to override.",
        file=_sys.stderr,
    )
# ... quote-safe IN clause construction as before
```

Configurable via `--per-condition-cap N` (default 5 to match run_sheet.md).

**B. Refuse to run when the DB has rows from conditions the auto-select couldn't cover.**

If the DB has rows from N distinct conditions but the auto-selected set covers < N, fail loud:
```python
condition_count = conn.execute(
    "SELECT COUNT(DISTINCT condition) FROM evaluation_runs WHERE is_pilot = FALSE"
).fetchone()[0]
selected_condition_count = conn.execute(
    f"SELECT COUNT(DISTINCT condition) FROM evaluation_runs "
    f"WHERE is_pilot = FALSE AND run_id IN ({','.join('?' * len(latest))})",
    latest,
).fetchone()[0]
if selected_condition_count < condition_count:
    print(
        f"ERROR: auto-select covered {selected_condition_count}/{condition_count} conditions. "
        f"Pass --run-ids explicitly to include all conditions.",
        file=_sys.stderr,
    )
    sys.exit(2)
```

Converts the silent correctness bug into a loud-fail.

**C. Bundle with #45's run-sheet update.**

Once `CAMPAIGN_RUN_IDS` is set per #45's recommendation, Row 17 becomes `uv run scripts/analyze_results.py --run-ids $CAMPAIGN_RUN_IDS` and the auto-select path never fires. The (A)+(B) remediations are belt-and-suspenders for any user who skips the env-var step.

**D. Regression test.**

```python
def test_auto_select_covers_all_conditions_in_canonical_eval_shape(tmp_path):
    """20 run_ids × 4 conditions × 5 runs — auto-select must include all 4 conditions."""
    db = seed_canonical_run_sheet_db(tmp_path, runs_per_condition=5)
    scope_sql = _resolve_scope(db, run_ids=None, since=None)
    # Extract the run_id list from the SQL, re-query conditions:
    selected = _run_ids_from_scope_sql(scope_sql)
    conditions = db.execute(
        f"SELECT DISTINCT condition FROM evaluation_runs WHERE run_id IN ({','.join('?' * len(selected))})",
        selected,
    ).fetchall()
    assert len(conditions) == 4  # pre-fix: 1
```

### Recommendation

**Land immediately — blocking Row 17.** The bug manifests the first time a user runs the run sheet verbatim against the canonical 20-run-id DB, producing silently-wrong numbers in every table row. ~30 LOC + 2 regression tests. ~45 min. Must ship before any Chapter 6 table is generated.

### Legacy data

Not applicable — data is correct; selection is wrong. Re-run post-fix against the same DB produces corrected numbers.

---

## Issue #47 — Row 13 (`analyze_context_utilization.py`) default environment is `production` but thesis runners emit to `default`; T3 evidence is silently empty with exit 0 (P0, CRITICAL)

### Summary

The ext6 #39 remediation added `--environment production` as the default to filter out smoke/dev/test traces. That filter is correct for the intent, but the default value is wrong for the actual runner convention documented in `docs/langfuse_audit/langfuse_redundancy_audit_2026_04_19.md:400`:

> "Thesis-load-bearing runners (`run_pilot.py`, `run_longitudinal.py`, `run_longitudinal_shift.py`, CLI `mint run`) are deliberately unchanged — they emit to `environment=default` by design so Chapter 6 queries see them."

So the thesis's actual trace stream lives in LangFuse's `default` environment. `analyze_context_utilization.py:443` filters for `production`. Row 13 fetches zero traces. The analyzer handles zero-traces path at `:481-483` by assigning an empty list per agent and continuing; `:500` prints the summary anyway; `:505` returns 0. The thesis's T3 naive-verbose evidence section is silently empty — no warning, no non-zero exit code, no differentiation from "genuinely no context issues found."

### Where

- **Analyzer default:** `scripts/analyze_context_utilization.py:443` — `parser.add_argument("--environment", default="production", ...)`.
- **Runner emission convention:** `docs/langfuse_audit/langfuse_redundancy_audit_2026_04_19.md:400` — "emit to `environment=default` by design".
- **Silent empty-traces handling:**
  - `scripts/analyze_context_utilization.py:481-483` — `if not traces: results[agent_name] = []; continue`.
  - `scripts/analyze_context_utilization.py:500` — prints summary regardless.
  - `scripts/analyze_context_utilization.py:505` — `return 0`.
- **Run sheet Row 13:** `docs/run_sheet.md:59` — `uv run scripts/analyze_context_utilization.py` with no `--environment` override.

### Reproduction

Against any LangFuse instance populated by a real `run_pilot.py` execution:

```bash
$ uv run scripts/analyze_context_utilization.py
Connected to LangFuse at https://langfuse.internal/
Window: 2026-04-06 11:00 -> 2026-04-20 11:00 UTC

Fetching 'decomposition_deepagent' traces... 0 found. done.
Fetching 'adversarial_deepagent' traces... 0 found. done.
Fetching 'failure_analysis_deepagent' traces... 0 found. done.

==== Summary ====
(empty tables; exits 0)
```

A `run_pilot.py --all-conditions --n-runs 5` run emits ~400 traces to `environment=default`. The analyzer's default `--environment production` query returns 0 for all three. The run completes "successfully" and the thesis's T3 section has no data.

### Why two layers of the fix combine to hide this

The ext6 #39 remediation had TWO goals: (a) exclude smoke/dev from prod eval and (b) enable per-campaign scoping. Goal (a) is satisfied by `--environment production` as a default — but that default doesn't match the runner convention. Goal (b) is satisfied by `--session-ids` (line 447) but that requires explicit scope. Neither default matches the out-of-box "run the runner, run the analyzer, see Chapter 6 data" path. The fix for #39 lands the knobs; the defaults were never re-validated against the thesis's real trace flow.

Plus: the silent-empty-traces branch at `:481-483` converts the environment mismatch from a loud "zero traces found" into an invisible "no data to report," because the analyzer still runs the summary printer on an empty dict and the reader sees a normally-formatted report.

### Threat model / impact

- **Chapter 6 T3 (naive-verbose context utilization) evidence is empty by default.** The section that was supposed to be populated from Row 13 has no data — not because the phenomenon doesn't exist, but because the query filter excludes the data.
- **No visible signal.** Exit 0, normal-looking summary output, no ERROR or stderr WARN. A reader not looking at LangFuse directly won't notice.
- **Compounds with #39.** #39 stated the analyzer "excludes smoke + dev + prior-campaign traces" — correct, but the default value for which environment IS thesis-prod was left at the LangFuse documentation default (`production`) instead of being aligned with this project's `default` convention.
- **Runner convention wasn't propagated to the analyzer.** The audit document at `docs/langfuse_audit/langfuse_redundancy_audit_2026_04_19.md:400` explicitly called out the `environment=default` convention for runners, but the ext6 #39 fix was written against LangFuse-generic conventions rather than this project's convention.
- **Direction of bias: evidence-absence, not evidence-distortion.** Worst-case "we have nothing" rather than "we have the wrong thing." Still bad for the thesis, but at least the reader sees empty tables rather than plausible wrong tables.

### Remediation

**A. Default `--environment` to `default`.**

```python
# scripts/analyze_context_utilization.py:443 — before:
parser.add_argument("--environment", default="production", ...)
# after:
parser.add_argument(
    "--environment", default="default",
    help="LangFuse environment tag to fetch (default: 'default' — matches runner "
         "emission convention per docs/langfuse_audit/langfuse_redundancy_audit_2026_04_19.md).",
)
```

Align with the project convention. Users who want to analyze a different environment can still pass `--environment production` / `staging` / `test` explicitly.

**B. Loud-fail on zero traces.**

```python
# scripts/analyze_context_utilization.py:481 — add strict mode:
parser.add_argument(
    "--allow-empty", action="store_true",
    help="Don't treat zero traces as an error (default: exit 2 if any agent returns no traces)",
)

# inside main, after fetching all agents:
total_traces = sum(len(t) for t in results.values())
if total_traces == 0 and not args.allow_empty:
    print(
        f"ERROR: zero traces found for any agent in environment={args.environment!r}, "
        f"window={args.days}d. "
        f"Check (a) runner emission convention, (b) session_ids filter, (c) window length. "
        f"Pass --allow-empty to suppress this check.",
        file=sys.stderr,
    )
    return 2
```

A Chapter 6 analysis run should never produce empty T3 evidence; the loud-fail converts silent-empty into actionable.

**C. Apply the same "empty-traces fails loud" pattern to siblings.**

`analyze_tier2_probes.py`, `analyze_compression_loss.py`, and `analyze_results.py` should all add `--allow-empty` with a default fail-loud. Zero rows in any of those analyzers means the data pipeline is broken, not that the thesis has nothing to say. Align the failure-attribution pattern with cross-cutting observation #5 of the parent audit (silent-degradation paths).

**D. Regression test.**

```python
def test_context_utilization_default_env_matches_runner_emission(monkeypatch):
    """Default env should align with LANGFUSE_TRACING_ENVIRONMENT used by runners."""
    # Parse the argparse default:
    from scripts.analyze_context_utilization import main
    import argparse
    parser = argparse.ArgumentParser()
    # ... rebuild the parser and inspect the default
    assert parser.get_default("environment") == "default"

def test_context_utilization_fails_loud_on_zero_traces(monkeypatch, capsys):
    """Zero traces → exit 2, not exit 0 with empty summary."""
    monkeypatch.setattr("scripts.analyze_context_utilization.fetch_traces",
                        lambda *a, **kw: [])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
```

### Recommendation

**Land immediately — blocking Row 13.** Two-line default change + loud-fail on empty + 2 regression tests. ~30 min. Must ship before any Chapter 6 T3 section is written.

### Legacy data

Not applicable — data is in LangFuse; the analyzer just wasn't querying the right environment. Post-fix re-run of Row 13 produces the correct evidence against the same LangFuse instance.

---

## Issue #48 — Condition-order randomization is documented as part of the harness protocol but the runner executes in fixed order; Chapter 6 has time/order confounding (HIGH, methodology)

### Summary

The harness docstring at `tests/evaluation/harness.py:17` states protocol step 3:

> "Randomise condition order across runs."

The actual runner iterates conditions in fixed declaration order:

- `scripts/run_pilot.py:49` — `ALL_CONDITIONS = ["full", "no_verification", "no_optimization", "static"]` (fixed list literal).
- `scripts/run_pilot.py:147` — `for condition in conditions:` iterates that list verbatim.

No `random.shuffle(conditions)` anywhere in the runner, and no seed-based randomization per run. Every invocation of `run_pilot.py --all-conditions` executes conditions in the same order: full → no_verification → no_optimization → static.

This is a methodology finding: Chapter 6's per-condition comparisons may carry time/order confounders (machine load, LLM provider state, cache warmth, TCP-connection re-use, LangFuse write latency drift) that the harness protocol's randomization was specifically designed to eliminate.

### Where

- **Protocol declaration:** `tests/evaluation/harness.py:17` — "3. Randomise condition order across runs."
- **Fixed order declaration:** `scripts/run_pilot.py:49`.
- **Fixed-order iteration:** `scripts/run_pilot.py:147`.
- **Sibling runners:** `run_longitudinal.py`, `run_longitudinal_shift.py` — not applicable (single-condition-per-run by design). Condition-order randomization applies only to `run_pilot.py` + `run_evaluation.py`.

### Reproduction

Two fresh `uv run scripts/run_pilot.py --all-conditions --n-runs 5` invocations produce the same condition order in both. Direct inspection of `run_pilot.py:147` confirms no shuffle.

### Threat model / impact

- **Time-of-day / system-state confounding.** If `static` always executes last, and the LangFuse write-latency drifts up through the run (common at the end of long batches), `static`'s M5 (latency) measurements inherit that drift. The same applies to OpenAI rate limits, Docker cache state, and anything else that varies over a several-hour run.
- **Direction of bias: condition-dependent.** Whichever condition runs late is disadvantaged on any metric that degrades with time. Whichever runs early is disadvantaged on cold-start metrics (first-task registry is empty → more JIT generation for the first few tasks of the first condition).
- **Chapter 6 headline comparisons.** "JIT condition X vs static" comparisons implicitly assume the two ran under equal conditions. Fixed order violates that assumption every single run. Statistical tests (Fisher's exact + Holm) assume independence; order-dependent confounders break that assumption more subtly than sample-size issues.
- **Lower severity than #44-#47 but harder to fix post-hoc.** The data is what it is; you can't retroactively randomize. Either re-run the eval with shuffling, or add a Chapter 4.5 disclosure note.
- **Harness protocol / runner disagreement is a long-lived correctness drift.** The harness docstring has read "randomise condition order" since commit history shows. The runner has never implemented it. Either the docstring is aspirational (delete it) or the runner is wrong (add shuffle). Both paths should land explicitly rather than leaving the disagreement in place.

### Remediation

**A. Implement the randomization in the runner.**

```python
# scripts/run_pilot.py: around line 139-147 — add:
import random

conditions = ALL_CONDITIONS if args.all_conditions else [args.condition]
if args.all_conditions and args.n_runs > 1:
    # Protocol per harness.py:17 — randomize condition order.
    rng = random.Random(args.base_seed or 42)
    # Generate a per-run condition permutation:
    permutations = [list(conditions) for _ in range(args.n_runs)]
    for perm in permutations:
        rng.shuffle(perm)
    # The runner's outer loop iterates runs, inner loop iterates conditions per run.
    # Restructure to: for run_idx in range(n_runs): for condition in permutations[run_idx]:
```

This requires restructuring the runner's loop (currently outer = condition, inner = run_index). Inverting the loop order is a larger change; the inversion is also necessary for Bug B's fix.

**B. Restructure loop: outer=run_index, inner=shuffled-condition (per the protocol).**

The current loop shape:
```
for condition in conditions:
    for run_idx in range(n_runs):
        execute(condition, run_idx)
```

is "all N runs of condition A, then all N runs of condition B, …". Under this shape, condition A's N runs happen back-to-back and share time-correlated confounders (LangFuse write queue state, Docker layer cache state, LLM cache effects). Inverting to:

```
for run_idx in range(n_runs):
    conditions_this_run = list(conditions)
    rng.shuffle(conditions_this_run)
    for condition in conditions_this_run:
        execute(condition, run_idx)
```

Interleaves conditions across the run, which is what the harness protocol intended. The N samples per condition are now temporally spread across the full run duration, averaging out time-correlated effects.

**C. Alternative: if randomization is deliberately not wanted, delete the protocol claim.**

If the team decides determinism > randomization (easier to reproduce, easier to debug), remove protocol step 3 from the docstring and add a Chapter 4.5 disclosure note:

> "Per run_pilot.py's current implementation, conditions execute in a deterministic order (full → no_verification → no_optimization → static). Chapter 4.5 discloses this as a potential confounder for time-dependent metrics; Chapter 7 future work proposes per-run condition randomization."

Explicit disagreement-with-protocol is better than silent disagreement.

**D. Regression test.**

```python
def test_run_pilot_shuffles_condition_order_per_run(monkeypatch):
    """With --n-runs 3, condition order differs across the 3 runs."""
    executed_orders = []
    def capture_order(condition, ...):
        executed_orders[-1].append(condition)
    ...  # wire into the runner, invoke with --n-runs 3 --all-conditions
    assert executed_orders[0] != executed_orders[1] or executed_orders[1] != executed_orders[2]
    # Or assert that the sorted set of conditions covered is identical across runs.
```

### Recommendation

**Land (A) + (B) before Phase 5 eval if the team elects randomization.** Estimated ~1h for the loop restructure + regression test. If the team elects (C) — determinism + disclosure — 10 min to update the docstring and Chapter 4.5 note.

**Decision needed from thesis advisor:** randomization (methodology-pure, harder to reproduce) vs determinism (easier to debug, requires disclosure). Until resolved, the gap between docstring and runner is itself a finding that should be closed one way or the other.

### Legacy data

Pre-fix Chapter 6 data was collected under fixed order. Any metric that plausibly has order dependence (M5 latency, M4 token cost when LangFuse caches are warm) carries potential confounding. Post-(A)+(B) re-runs produce comparable-across-conditions data. Post-(C) re-runs keep the current fixed-order semantics — no re-collection needed, just add the disclosure.

---

## Issue #49 — Phase-4 eval DB is shared across every invocation; rerunning `run_pilot.py --all-conditions` after a code change silently pools old + new rows (P0, CRITICAL)

### Summary

`run_pilot.py` — the script that generates the 400-row Chapter 6 eval data — has no `--db` argument. The DB path is hardcoded to `data/eval/metrics.duckdb`. `run_sheet.md` Phase 4 row 8 invokes the script without any isolation step (no `--fresh-db`, no pre-rm, no timestamped path). Every record is appended via `record_run` (harness.py:400 → evaluation_store.py:197 INSERT). Two executions of the same Row 8 command — e.g., one aborted midway, one after a code change, one "let me re-run with a different LLM model" — leave both invocations' rows in the same DB, same condition, same task_ids, differing only by `run_id` and `timestamp`.

Phase 4's 18-hour wall-clock makes re-runs almost inevitable (network hiccup, OOM, exploratory re-run, version bump). The analyzer's own scoping mechanisms (ext6 #38, ext7 #46) are band-aids over a missing primitive at the runner layer: the runner itself cannot distinguish "this is campaign A" from "this is campaign B" in the DB.

### Where

- **Hardcoded DB path:** `scripts/run_pilot.py:47` — `DB_PATH = str(DATA_DIR / "eval" / "metrics.duckdb")`.
- **Downstream consumption of the hardcode:** `scripts/run_pilot.py:123-127` — `HarnessConfig(chroma_dir=CHROMA_DIR, mcp_tools_dir=MCP_TOOLS_DIR, skills_dir=SKILLS_DIR, db_path=DB_PATH, ...)`. The constant is wired into the harness config with no path override possible.
- **No `--db` argument:** `scripts/run_pilot.py:53-88` — full argparse block; no `--db`, no `--fresh-db`, no `--suffix`.
- **Record persistence (append-only semantics):**
  - `tests/evaluation/harness.py:400` — `self._store.record_run(record)` inside the per-task loop.
  - `src/registry/evaluation_store.py:192` — `record_run` entry point; `src/registry/evaluation_store.py:197` — `INSERT INTO evaluation_runs (...)` — straight append, no UPSERT, no pre-DELETE, no uniqueness/campaign guard.
- **Run sheet convention:** `docs/run_sheet.md:45` — `uv run scripts/run_pilot.py --all-tasks --all-conditions --n-runs 5` writing "400 records → `data/eval/metrics.duckdb`" as if the DB were guaranteed empty before this step.
- **Missing reset step in run sheet:** `docs/run_sheet.md:39` is the Phase 4 header; the Phase-3-to-Phase-4 transition has no "wipe DB" or "move previous DB aside" step.

### Reproduction

```bash
# Day 1: kick off the real eval, find a bug mid-run, kill it.
$ uv run scripts/run_pilot.py --all-tasks --all-conditions --n-runs 5
# ... 6 hours later, Ctrl-C after ~140 records written.

# Day 2: fix the bug, re-run.
$ uv run scripts/run_pilot.py --all-tasks --all-conditions --n-runs 5
# 18 h later: 400 new records appended. DB now has 540 rows
# (140 from the aborted pre-fix + 400 from the new). No row distinguishes
# them except by run_id + timestamp.

$ uv run scripts/analyze_results.py
# Analyzer's auto-select (ext7 #46) picks 4 of ~28 run_ids (140/5 per
# condition × 4 conditions = 28). Likely picks 4 of the most recent
# (post-fix), IF they're all from one condition, see #46. Still mixed
# with the aborted run's data if auto-select straddles the boundary.
```

The run_ids do include timestamps, so theoretically a user could manually filter to the post-fix set — but that requires the user (a) knowing there's contamination and (b) manually reconstructing the run_id list. Neither is part of the documented procedure.

### Why this is not addressed by ext6 #38 / ext7 #45, #46

The three earlier findings address **analyzer-side scoping**:
- #38: give the analyzer a `--run-ids` arg.
- #45: make the run sheet pass `--run-ids` to the analyzer.
- #46: fix auto-select to cover all conditions.

They assume **the user knows which run_ids constitute the current campaign**. #49 is the converse: at write time, there is no mechanism to delimit "the current campaign" from "everything else in the DB." The user has to manually track which run_ids belong to which campaign — via timestamps, via the log file, via memory. This is the exact class of bookkeeping that should be structural, not procedural.

Put differently: ext6 #38 and friends let the analyzer answer "give me only run A's rows" — but the user has no enforced way to record "run A started at 2026-04-20 14:00 and consists of these 20 run_ids." #49 is about adding that primitive.

### Threat model / impact

- **Re-run-after-code-change silently pools data.** The scenario isn't hypothetical; it's the dominant failure mode in a ~$6 / 18-hour batch. Any bug discovered post-run-start means re-running; without a fresh DB, the prior run's rows persist.
- **Per-run-ids scoping doesn't prevent it, just papers over it.** A user who ran Phase 4 twice and passes `--run-ids <20 ids>` to the analyzer gets the right scope IF they correctly enumerated the 20 post-fix run_ids. The manual enumeration is itself error-prone.
- **Interacts with ext7 #46 (auto-select).** Auto-select picks "4 most recent" — in a re-run scenario, the 4 most recent might straddle the pre-fix / post-fix boundary (e.g., 2 from the aborted run + 2 from the new). The analyzer silently analyzes a mix.
- **Chapter 6 reproducibility.** Claiming "here is our eval data" and providing `metrics.duckdb` is only meaningful if the DB represents exactly one campaign. Today's semantics: "some subset of some number of campaigns, which you can figure out from run_ids if you know what to look for."
- **Direction of bias: depends on what was in the DB pre-Phase-4.** Pilot rows (is_pilot=TRUE) are filtered out by the analyzer's `NOT is_pilot` clause — OK. But any prior exploratory non-pilot run (`run_pilot.py --all-conditions` against an earlier code version) enters the aggregate. An earlier run with a known-broken pipeline drags numbers down; an earlier run with an over-tuned pipeline drags them up.

### Remediation

**A. Add a `--db` argument + a `--fresh-db` flag to `run_pilot.py`.**

```python
parser.add_argument(
    "--db", default=None,
    help="DuckDB path (default: data/eval/metrics_<timestamp>.duckdb). "
         "Explicit path wipes-and-writes that file.",
)
parser.add_argument(
    "--fresh-db", action="store_true",
    help="Wipe the DB before writing. Required when --db points at an existing "
         "non-empty file, unless --append is also passed.",
)
parser.add_argument(
    "--append", action="store_true",
    help="Append to existing DB. Only valid with --db. Must not be combined "
         "with --fresh-db. Use for resumable runs (different --condition).",
)

# At startup:
if args.db is None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    db_path = DATA_DIR / "eval" / f"metrics_{ts}.duckdb"
    logger.info("No --db provided; using fresh timestamped path: %s", db_path)
else:
    db_path = Path(args.db)
    if db_path.exists() and db_path.stat().st_size > 0:
        if args.fresh_db:
            db_path.unlink()
            logger.info("Wiped existing DB at %s per --fresh-db", db_path)
        elif not args.append:
            logger.error(
                "DB exists at %s and is non-empty. Pass --fresh-db to wipe "
                "or --append to add to it.", db_path,
            )
            sys.exit(2)
```

The timestamped default path solves the re-run scenario: every invocation gets its own DB, no pooling possible.

**B. Update `run_sheet.md` to set + reuse a campaign DB path.**

```markdown
## Phase 4 setup

Set a campaign-scoped DB path at the top of the sheet:

```bash
export CAMPAIGN_DB="data/eval/metrics_2026-04-20_full.duckdb"
```

Every Phase-4 and analysis row passes `--db $CAMPAIGN_DB` explicitly.
```

Then rows 8, 17, 11, 16 all thread `--db $CAMPAIGN_DB` into their invocations. Mirrors the `CAMPAIGN_RUN_IDS` pattern from ext7 #45.

**C. Record a `campaign_id` column on every EvaluationRecord.**

Add `campaign_id` to the schema (nullable on old rows). Populated from a new `--campaign-id` CLI arg (default: same as the DB filename stem, or a fresh UUID).

```python
# src/registry/evaluation_store.py — schema:
CREATE TABLE evaluation_runs (
    ...,
    campaign_id VARCHAR,  -- nullable for legacy rows
    ...
);

# run_pilot.py:
parser.add_argument("--campaign-id", default=None,
                    help="Campaign identifier stamped on every row (default: DB filename stem).")

# Pass to harness → record_run → INSERT.
```

Analyzers then filter by `campaign_id = ?` as the primary scoping mechanism. Cleaner than reconstructing campaigns from run_id timestamps.

**D. Regression tests.**

```python
def test_run_pilot_refuses_to_append_to_non_empty_db_without_explicit_flag(tmp_path):
    """A DB that already has rows cannot be silently appended to."""
    db = tmp_path / "metrics.duckdb"
    _seed_db(db, n_rows=100)
    with pytest.raises(SystemExit) as exc:
        subprocess.run(["python", "-m", "scripts.run_pilot",
                        "--db", str(db), "--all-conditions", "--n-runs", "1"],
                       check=True)
    assert exc.value.code == 2

def test_run_pilot_defaults_to_timestamped_fresh_db(tmp_path, monkeypatch):
    """Default invocation writes to a new timestamped path, not a shared one."""
    ...
```

**E. Document the campaign lifecycle in `run_sheet.md`.**

Add a "Campaign isolation" section before Phase 4:

```markdown
## Campaign isolation (Phase 4 prerequisite)

Every Phase-4 eval run MUST write to its own DB. Set `$CAMPAIGN_DB` at the
start of the session:

```bash
export CAMPAIGN_DB="data/eval/metrics_$(date +%Y-%m-%d_%H%M%S)_full.duckdb"
```

If you re-run Phase 4 after a code change, generate a new `$CAMPAIGN_DB`.
Do NOT overwrite or append to an existing campaign DB — the analyzer's
scoping assumes one DB per campaign.
```

### Recommendation

**Land (A) + (B) before any Phase-4 re-run.** (A) is a ~30 LOC runner-side change + tests (~45 min). (B) is a ~15 min doc update. (C) `campaign_id` column is the proposal-aligned structural fix (~2h) but (A)+(B) address the immediate hazard. (E) documentation is 10 min.

Must ship before the first Phase-4 re-run because the bug's symptom is silent data pooling and the cost of a contaminated run is 18 hours + $6 to re-collect clean data.

### Legacy data

Any `data/eval/metrics.duckdb` in the wild is potentially contaminated. For the thesis, the safest path is to re-run Phase 4 cleanly post-fix against a fresh DB. Short of that, a manual audit of `run_id` timestamps + a `--run-ids` whitelist can reconstruct the correct campaign — but this is manual, error-prone, and not reproducible from the run sheet alone.

---

## Issue #50 — `invocation_events` schema has no `is_pilot` column; M3 Gini analyzer cannot exclude pilot rows even after #44's scope fix (P0, CRITICAL)

### Summary

The Chapter 6 Row 17 M3 Gini analyzer reads from `invocation_events` (Step 18.5 persistent invocation log). The companion `evaluation_runs` table carries an `is_pilot` boolean that the analyzer's M1/M4/M5 queries use to exclude pilot rows (`WHERE is_pilot = FALSE`). But `invocation_events` has no such column — its schema tracks `(run_id, condition, task_id, capability_id, capability_type, success, token_cost, latency_ms, occurred_at)` only. `flush_task_cycle_state` unconditionally flushes invocation records for every task in `_execute_run`, including pilot runs (Row 7 of `run_sheet.md`).

Net effect: every Row-7 pilot invocation lands in `invocation_events` indistinguishable from Row-8 eval invocations, and M3 Gini silently aggregates both populations.

This is **orthogonal to ext7 #44's scope-missing bug**:
- **#44 fix:** add `_SCOPE_SQL` to the Gini query → filters by `run_id IN (...)` of the eval campaign.
- **#50 residual:** even scoped correctly by `run_id`, pilot invocations from the eval campaign's own pre-flight pilot step leak in if their run_id matches — AND more broadly, pilot invocations have no filterable marker.

Both fixes are needed. #44 alone doesn't close #50; #50 alone doesn't close #44.

### Where

- **Schema missing `is_pilot`:** `src/registry/evaluation_store.py:67-78` — `CREATE TABLE invocation_events` columns: `id`, `run_id`, `condition`, `task_id`, `capability_id`, `capability_type`, `success`, `token_cost`, `latency_ms`, `occurred_at`. No `is_pilot`.
- **Gini query (sibling of ext7 #44):** `scripts/analyze_results.py:607-614` — reads `invocation_events` with neither `_SCOPE_SQL` nor any pilot filter.
- **Unconditional flush for pilot:** `tests/evaluation/harness.py:410-419` — every task in `_execute_run` flushes via `flush_task_cycle_state`, regardless of whether the call originated from `run_pilot` (`is_pilot=True`) or `run_condition` (`is_pilot=False`).
- **Companion `evaluation_runs` with `is_pilot`:** `src/registry/evaluation_store.py:41` (schema) — the table that M1/M4/M5 queries correctly filter by. Asymmetry between the two tables is the root cause.
- **Run sheet convention that triggers the leak:** `docs/run_sheet.md:35` (Row 7 pilot command) + `docs/run_sheet.md:45` (Row 8 full eval, same DB).

### Reproduction

```bash
# Run 7 of run_sheet.md — pilot
$ uv run scripts/run_pilot.py --all-tasks --condition full --n-runs 1
# Writes ~20 rows to evaluation_runs with is_pilot=TRUE, plus
# ~N invocation_events rows with NO is_pilot marker.

# Run 8 of run_sheet.md — full eval
$ uv run scripts/run_pilot.py --all-tasks --all-conditions --n-runs 5
# Writes 400 rows to evaluation_runs with is_pilot=FALSE, plus
# ~K invocation_events rows with NO is_pilot marker.

# Run 17
$ uv run scripts/analyze_results.py
# M3 Gini aggregates N+K invocation_events rows — pilot-contaminated.
# Even passing --run-ids <Row-8's 20 ids> scopes evaluation_runs correctly,
# but the Gini query is scope-less (see #44). Fixing #44 scopes Gini by
# run_id — but pilot rows from the pilot campaign would still slip in if
# their run_id matched any of the scope list, AND more generally, there's
# still no structural way to say "invocations from non-pilot runs only."
```

Direct schema inspection confirms the missing column.

### Why the asymmetry matters structurally

`evaluation_runs` is a table of **task outcomes**. `invocation_events` is a table of **capability usages** (each tool/skill/agent invocation within a task). Both are per-task populations; the asymmetry means one is self-describing (pilot flag on each row) and the other is not (must join back to `evaluation_runs` to determine pilot status).

The Step 18.5 commit that added `invocation_events` (per commit history) focused on the Gini-coefficient analysis and didn't mirror the `is_pilot` column from `evaluation_runs` — likely because the intended use at the time was longitudinal runs (which don't have a pilot phase). When the table was later reused for the Phase-4 Chapter 6 Gini analysis (which DOES have a pilot phase), the asymmetry became a correctness issue.

### Threat model / impact

- **Chapter 6 M3 Gini always pilot-contaminated under the canonical run sheet.** Row 7 pilot ALWAYS runs before Row 8 eval, in the same DB (per #49). Row 17 M3 Gini always includes pilot invocations.
- **Direction of bias: depends on pilot's capability-usage distribution.** The pilot is one run of one condition (Row 7 uses `--condition full --n-runs 1`); its capability-usage distribution is typically narrower than the 20-run × 4-condition full eval. Adding a narrow distribution to a broader one lowers measured concentration — i.e., artificially **deflates** Gini. Chapter 6 reads M3 Gini as "capability-reuse concentration"; #50 makes it look less concentrated than reality.
- **Survives all three sibling fixes.** Fixing #44 (add `_SCOPE_SQL`), #46 (per-condition auto-select), and #49 (DB isolation) individually and together does NOT close #50 — the schema is missing the filter dimension. Only a schema change + query update closes it.
- **Not observable from SQL output alone.** Without running analysis against the `evaluation_runs` `run_id` set, a user reading the Gini output has no way to detect that pilot rows entered.

### Remediation

**A. Add `is_pilot` column to `invocation_events`.**

```sql
-- src/registry/evaluation_store.py — schema update:
CREATE TABLE IF NOT EXISTS invocation_events (
    id                        VARCHAR PRIMARY KEY,
    run_id                    VARCHAR NOT NULL,
    condition                 VARCHAR NOT NULL,
    task_id                   VARCHAR NOT NULL,
    capability_id             VARCHAR NOT NULL,
    capability_type           VARCHAR,
    success                   BOOLEAN NOT NULL,
    token_cost                INTEGER NOT NULL DEFAULT 0,
    latency_ms                INTEGER NOT NULL DEFAULT 0,
    is_pilot                  BOOLEAN NOT NULL DEFAULT FALSE,   -- NEW
    occurred_at               TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Forward-migration for existing DBs: ALTER TABLE ADD COLUMN IF NOT EXISTS
-- (idempotent, default FALSE on legacy rows — which is incorrect for any pre-fix
-- pilot rows, but those are uncategorizable without a run_id → is_pilot join).
```

**B. Thread `is_pilot` through the flush path.**

```python
# tests/evaluation/cycle_flush.py — add is_pilot parameter:
def flush_task_cycle_state(
    *,
    eval_store, metrics_store, registry, run_id, condition, task_id,
    task_events_cutoff, is_pilot: bool,
):
    ...
    eval_store.record_invocation_events(
        run_id=run_id, condition=condition, task_id=task_id,
        is_pilot=is_pilot, events=task_invocations,
    )

# tests/evaluation/harness.py:410 — pass the existing is_pilot flag from _execute_run:
flush_task_cycle_state(
    eval_store=self._store, metrics_store=metrics_store, registry=registry,
    run_id=run_id, condition=condition, task_id=task.task_id,
    task_events_cutoff=task_events_cutoff,
    is_pilot=is_pilot,  # already a parameter on _execute_run
)
```

**C. Apply filter to every `invocation_events` read in the analyzer.**

```python
# scripts/analyze_results.py:607 — before:
conn.execute("""
    SELECT condition, run_id, capability_id, COUNT(*) AS invocations
    FROM invocation_events
    GROUP BY condition, run_id, capability_id
""").fetchall()

# after — pilot filter + scope filter from ext7 #44:
conn.execute(f"""
    SELECT condition, run_id, capability_id, COUNT(*) AS invocations
    FROM invocation_events
    WHERE NOT is_pilot{_SCOPE_SQL}
    GROUP BY condition, run_id, capability_id
""").fetchall()
```

The `WHERE NOT is_pilot{_SCOPE_SQL}` shape matches the evaluation_runs queries in the same file. Visual consistency reduces the chance a future query forgets one of the filters.

**D. Alternative: JOIN to `evaluation_runs` instead of adding column.**

```sql
SELECT ie.condition, ie.run_id, ie.capability_id, COUNT(*) AS invocations
FROM invocation_events ie
JOIN evaluation_runs er ON ie.run_id = er.run_id AND ie.task_id = er.task_id
WHERE NOT er.is_pilot{_SCOPE_SQL}
GROUP BY ie.condition, ie.run_id, ie.capability_id
```

Pros: no schema migration. Cons: per-query JOIN overhead (index needed on `(run_id, task_id)`); every future invocation_events consumer must remember to JOIN. Option (A)+(C) is better for read-heavy patterns; (D) is fine if the analyzer is the only consumer.

**E. Regression test.**

```python
def test_m3_gini_excludes_pilot_invocations(tmp_path):
    """Pilot + non-pilot invocation rows in same DB → Gini computed only over non-pilot."""
    db = seed_mixed_pilot_and_full_db(tmp_path, pilot_invocations=5, full_invocations=10)
    # Invoke analyze_results.py, capture M3 Gini stdout, assert the count of
    # invocations aggregated is 10 (non-pilot only), not 15.
    ...
```

**F. Documentation: audit every `invocation_events` consumer.**

Once the column is added, grep for `FROM invocation_events` across `scripts/`, `src/`, and `tests/evaluation/`. Every consumer must decide: (a) explicitly include pilot rows (rare — maybe one analyzer wants both populations for A/B), or (b) filter them out. Default is (b). Flag any that don't make an explicit decision.

### Relationship to #44

| | #44 fix | #50 fix | Both fixed |
|---|---|---|---|
| **Gini query** | Scoped by `run_id` | Excludes pilot rows | Scoped AND pilot-free |
| **M2 contamination query** | Scoped by `run_id` | N/A (reads evaluation_runs which has is_pilot) | Correctly scoped + filtered |

#44 covers the scope side; #50 covers the pilot-filter side. Both must land for Row 17 M3 to be correct. #44 addresses the broader M2+M3 scope issue; #50 is Gini-specific.

### Recommendation

**Land as a bundle with #44.** Both fixes touch the same two files (`analyze_results.py` + `evaluation_store.py`) and the same query at line 607-614. Doing them together (plus the existing #44 remediation for line 954 M2) means one batch of touches, one batch of tests, one schema migration. Estimated combined effort: ~1 hour (schema migration + 4 query updates + 3 regression tests).

Must ship before Row 17 is run against any multi-phase DB — which is every canonical run_sheet execution.

### Legacy data

Pre-fix `invocation_events` rows have no `is_pilot` marker. Post-fix options:
- **Correctable:** backfill via JOIN to `evaluation_runs` by `(run_id, task_id)`:
  ```sql
  UPDATE invocation_events SET is_pilot = er.is_pilot
  FROM evaluation_runs er
  WHERE invocation_events.run_id = er.run_id
    AND invocation_events.task_id = er.task_id;
  ```
- **Discard + re-run:** simpler but costs ~18h / $6 per campaign.

Backfill is cheap and lossless; recommended.

---

## Issue #51 — Run sheet claims Phase 4 rows 8/9/10 are "parallelizable" but the three runners share + mutate the same registry directories and snapshot storage (P0)

### Summary

`docs/run_sheet.md:41` reads:

> "Each row writes to a **different** DuckDB; all three can run concurrently in separate terminals. Wall-clock lower bound if parallel: **~18 h** (dominated by row 8)."

The DB paths are distinct (`metrics.duckdb`, `longitudinal.duckdb`, `longitudinal_shifted.duckdb`), so the DB-level claim is correct. But the three runners share **everything else** the registry cares about:

- **ChromaDB directory:** `data/eval/chroma/` — all three runners hardcode this path.
- **MCP tools directory:** `src/mcp_tools/` — all three.
- **Skills directory:** `data/eval/skills/` — all three.
- **Snapshot storage:** `tests/evaluation/snapshots/` — the snapshot-copy-of-registry lives here, per `snapshots.py:21`.

Both longitudinal runners explicitly `rmtree` those three shared dirs at startup (per ext6 #31's fix). If row 8 is mid-run and row 9 starts, row 9 wipes row 8's active registry. Conversely, `take_clean_snapshot` does `rmtree` + `copytree` to the snapshot directory — if row 8 takes a snapshot, then row 9 takes its own, row 8's subsequent `restore_clean_snapshot` loads a different hash and `harness.py:334-340` raises `RuntimeError: "Snapshot ID mismatch ... registry state may be corrupted"`.

Following the run sheet's parallel guidance doesn't just race — it actively guarantees data corruption + a hard abort.

### Where

- **Run sheet "parallel" claim:** `docs/run_sheet.md:41` — "all three can run concurrently in separate terminals."
- **Shared directory hardcodes:**
  - `scripts/run_pilot.py:44-47` — `CHROMA_DIR`, `MCP_TOOLS_DIR`, `SKILLS_DIR`, `DB_PATH`.
  - `scripts/run_longitudinal.py:47-49` — same three dirs.
  - `scripts/run_longitudinal_shift.py:76-78` — same three dirs.
- **`rmtree` on shared dirs (registry wipe):**
  - `scripts/run_longitudinal.py:128-131` — `for _d in (CHROMA_DIR, MCP_TOOLS_DIR, SKILLS_DIR): rmtree(_d); mkdir`.
  - `scripts/run_longitudinal_shift.py:243-246` — same block.
- **Shared snapshot storage:**
  - `tests/evaluation/snapshots.py:21` — `_SNAPSHOT_DIR = Path(__file__).parent / "snapshots"` (single global).
  - `tests/evaluation/snapshots.py:41-52` — `take_clean_snapshot` does `rmtree` + `copytree` per invocation.
  - `tests/evaluation/snapshots.py:82-93` — `restore_clean_snapshot` does the same shape into the registry dirs.
- **Harness snapshot-hash check:** `tests/evaluation/harness.py:334-340` — raises `RuntimeError` when `restore_clean_snapshot`'s returned hash differs from the one `prepare()` captured.

### Reproduction

```bash
# Terminal 1: start row 8 (the 18h run).
$ uv run scripts/run_pilot.py --all-tasks --all-conditions --n-runs 5 &
# ... captures snap_id=A at harness.prepare(); begins task loop.

# Terminal 2: start row 9 per run sheet's parallel guidance.
$ uv run scripts/run_longitudinal.py
# Line 128-131: rmtree on data/eval/chroma, src/mcp_tools, data/eval/skills
# → row 8's active ChromaDB collection is now gone.
# Line 133: take_clean_snapshot overwrites the shared tests/evaluation/snapshots/
# → snapshot on disk is now row 9's, not row 8's.

# Terminal 1 (row 8) advances to next task's pre-task flush:
# harness.py:333 calls restore_clean_snapshot → reads overwritten snapshot
# → computes a hash that differs from the captured snap_id=A.
# harness.py:334 raises RuntimeError: "Snapshot ID mismatch ... registry state may be corrupted"
# Row 8 aborts ~6h in with no recovery.
```

Confirmed by inspection of the shared paths and the rmtree + copytree calls. The mismatch branch at `harness.py:334-340` is the loud-fail; the silent-corruption window before that point (rows registered against a wiped ChromaDB) is worse.

### Threat model / impact

- **Direct methodology hazard.** A thesis writer following the run sheet verbatim and seeing "all three can run concurrently" starts rows 8/9/10 in parallel in good faith, then loses 6-18 hours of compute to a RuntimeError. Or worse: row 8 survives but its underlying ChromaDB state was wiped mid-run and its last N tasks ran against an empty registry → silently-contaminated eval data.
- **Run sheet is load-bearing documentation.** It's the single source of truth for "how to reproduce Chapter 6." A false parallel-guidance line there is more dangerous than a code bug, because the reader trusts it.
- **Cross-cutting with #31.** Ext6 #31 fixed "longitudinal baseline is dirty" by adding the `rmtree` pre-wipe. That fix is correct in isolation but magnifies #51: pre-#31, the longitudinal runner snapshotted whatever was on disk without wiping (bad for correctness but parallel-safer); post-#31 it actively wipes shared state (better for correctness but parallel-hostile). Neither fix in isolation anticipated the interaction with `run_pilot`'s concurrent execution.
- **No loud signal in the run sheet.** The "parallelizable" line is presented without caveats; no warning about shared registry state, no recommendation to set up separate registry paths per-row.
- **Direction of bias: varies.** Best case: row 8 hits the snapshot-mismatch `RuntimeError` and the thesis writer re-runs serially. Worst case: silent registry-wipe mid-run produces 400 rows of nominally-successful data where the back half ran against a wrong registry state. The latter is undetectable without forensic log analysis.

### Remediation

**A. Retract the parallel claim in the run sheet.**

```markdown
## Phase 4 — Data generation (unattended, SERIAL)

Rows 8/9/10 share the ``data/eval/chroma``, ``src/mcp_tools``,
``data/eval/skills``, and ``tests/evaluation/snapshots`` directories.
Running them concurrently **will** corrupt one or more of the runs (either
via registry `rmtree` or via snapshot-hash mismatch). Run them **strictly
in sequence**: row 8 → row 9 → row 10.

Wall-clock sum: ~30-38 h (18 h row 8 + 6-10 h row 9 + 6-10 h row 10).
```

Alternative phrasing if the team wants to keep the parallel option: "parallelizable if and only if each row is invoked with separate `--chroma-dir`, `--mcp-tools-dir`, `--skills-dir`, `--snapshot-dir` overrides" — which requires the below (B).

**B. Parameterize the shared paths per-runner.**

```python
# scripts/run_pilot.py / run_longitudinal.py / run_longitudinal_shift.py — add:
parser.add_argument("--chroma-dir", default=str(DATA_DIR / "eval" / "chroma"))
parser.add_argument("--mcp-tools-dir", default=str(ROOT / "src" / "mcp_tools"))
parser.add_argument("--skills-dir", default=str(DATA_DIR / "eval" / "skills"))
parser.add_argument("--snapshot-dir", default=str(Path(__file__).parent.parent / "tests/evaluation/snapshots"))

# tests/evaluation/snapshots.py:21 — make _SNAPSHOT_DIR a caller-provided path:
# every call to take_clean_snapshot / restore_clean_snapshot already accepts
# snapshot_dir as a kwarg; just thread it from the runner CLI arg.
```

With path overrides, rows 8/9/10 can be invoked as:

```bash
$ uv run scripts/run_pilot.py \
    --chroma-dir data/eval/row8/chroma \
    --skills-dir data/eval/row8/skills \
    --snapshot-dir tests/evaluation/snapshots_row8 \
    --all-tasks --all-conditions --n-runs 5

$ uv run scripts/run_longitudinal.py \
    --chroma-dir data/eval/row9/chroma \
    --skills-dir data/eval/row9/skills \
    --snapshot-dir tests/evaluation/snapshots_row9

# Etc.
```

All three can then genuinely run in parallel. The run sheet becomes:

```markdown
| 8  | uv run scripts/run_pilot.py --chroma-dir ... --skills-dir ... --snapshot-dir ... (full args)
| 9  | uv run scripts/run_longitudinal.py --chroma-dir ... --skills-dir ... --snapshot-dir ...
| 10 | uv run scripts/run_longitudinal_shift.py --chroma-dir ... --skills-dir ... --snapshot-dir ...
```

Verbose but correct. An env-var preamble (`export ROW8_CHROMA=... ROW8_SKILLS=...`) reduces the line length.

**C. Defence-in-depth: file-lock the shared dirs.**

```python
# scripts/run_pilot.py / longitudinal runners — before the rmtree block:
import fcntl
lock_path = CHROMA_DIR.parent / ".eval_lock"
lock_path.parent.mkdir(parents=True, exist_ok=True)
lock_file = lock_path.open("w")
try:
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    logger.error(
        "Another eval runner holds the lock at %s. Per security audit #51, "
        "the eval registry dirs are NOT safe for concurrent access. "
        "Either wait for the other run to finish, or use per-runner "
        "--chroma-dir/--skills-dir/--snapshot-dir overrides.",
        lock_path,
    )
    sys.exit(2)
# ... proceed with run. Lock released on process exit.
```

Cross-platform lock on the shared dir prevents accidental parallel invocation even when the documentation is ignored.

**D. Regression test — lock contention.**

```python
def test_second_runner_refuses_to_start_while_first_holds_lock(tmp_path):
    """Spawn two run_pilot processes; the second must exit 2 within ~1 s."""
    # Subprocess 1: starts, acquires lock, sleeps in a mock loop.
    # Subprocess 2: starts, fails to acquire lock, exits 2 with the error message.
    ...
```

### Recommendation

**Land (A) + (C) + (D) before any Phase-4 re-run.** (A) is a documentation-only 5-minute fix; (C) is ~15 LOC per runner + test; (D) is one cross-process test. The pair closes the worst case (silent corruption) with a loud-fail and corrects the misleading documentation.

(B) is the cleaner long-term answer (genuine parallelism) but requires threading path overrides through `EvaluationHarness` and `take_clean_snapshot`'s kwarg surface — ~1-2h of plumbing. Worth doing if the 18h serial wall-clock is a thesis-timeline blocker; skip if it isn't.

### Legacy data

Not applicable — this is a methodology-documentation + defence-in-depth fix. No data to correct.

---

## Issue #52 — Row 17 analyzer has no hard guard that the 400-record eval is complete; partial datasets produce Chapter-6-shaped output without an "incomplete" signal (P1)

### Summary

`run_sheet.md:45` defines the canonical Phase-4 eval as "20 tasks × 4 conditions × 5 runs = 400 records." `analyze_results.py` prints the record count (`analyze_results.py:1051-1054`) but never validates that the scope-filtered row set matches the expected shape. An 18h run aborted at row 200, or a subset-invocation (`--tasks T01,T02`) that produced 20 rows instead of 400, gets analyzed as if it were a complete campaign. The output has correct per-row math; it just silently misrepresents incomplete data as headline numbers.

### Where

- **Expected shape (documentation):** `docs/run_sheet.md:45` — "20 tasks × 4 conditions × 5 runs = 400 records".
- **Record-count print (no assertion):** `scripts/analyze_results.py:1051-1054`:
  ```python
  total = conn.execute(
      f"SELECT COUNT(*) FROM evaluation_runs WHERE is_pilot = FALSE{_SCOPE_SQL}"
  ).fetchone()[0]
  print(f"\nTotal non-pilot records: {total}")
  ```
  No `if total != 400: fail`. No per-condition count assertion. No per-task count assertion. Just a print line that a reader can miss.

### Reproduction

```bash
# Scenario 1: aborted run. Row 8 dies at ~50% progress.
# DB now has ~200 non-pilot rows, distributed non-uniformly across the 4 conditions.

$ uv run scripts/analyze_results.py --run-ids <whatever>

# Total non-pilot records: 200
# Table 1: Success Rates — All Tasks
#   full: 52 rows, 71.2% success
#   no_verification: 48 rows, 65.3% success
#   ...

# Numbers look reasonable. Nothing flags "this is half a run."

# Scenario 2: subset-tasks invocation.
$ uv run scripts/run_pilot.py --tasks T07,T11 --all-conditions --n-runs 5  # 40 rows
$ uv run scripts/analyze_results.py

# Total non-pilot records: 40
# Table 1 reports per-condition success rates.
# Chapter 6 claim "we report per-task success for 20 tasks × 4 conditions" is FALSE
# for the output tables, but the output doesn't differentiate.
```

Direct inspection of the analyzer's main function confirms no assertion beyond the print line.

### Threat model / impact

- **Thesis-integrity hazard.** A Chapter 6 submission that includes tables derived from a 200-row DB looks identical in format to one derived from 400 rows. The reader cannot tell the difference without reading the "Total non-pilot records" line and mentally checking against the run sheet.
- **Compounds with #49.** #49: Phase 4 eval DB isn't campaign-isolated, so re-runs append. #52: analyzer doesn't check the count anyway. Together, a DB with 250 rows (a failed 50% run + a pilot + some other debug) gets analyzed without either the campaign-isolation flag or the completeness flag firing.
- **Lower severity than #49/#50 because the number IS printed.** A careful reader sees `Total non-pilot records: 200` and realizes. A casual reader (e.g., thesis committee spot-checking) doesn't. The bug is more about defense-in-depth than primary correctness.
- **Direction of bias: depends on which half of the run aborted.** If the first half (conditions run in #48's fixed order: full, no_verification) aborted, the surviving data is biased toward `no_optimization` + `static`. If the second half aborted, biased toward `full` + `no_verification`. Neither is representative.

### Remediation

**A. Assert expected shape at startup + fail loud on mismatch.**

```python
# scripts/analyze_results.py:1048-1055 — after _SCOPE_SQL resolution:
EXPECTED_CONDITIONS = 4  # full, no_verification, no_optimization, static
EXPECTED_TASKS = 20       # T01-T20
EXPECTED_RUNS_PER_CONDITION = 5  # default for run_sheet.md row 8
EXPECTED_TOTAL = EXPECTED_CONDITIONS * EXPECTED_TASKS * EXPECTED_RUNS_PER_CONDITION  # 400

# Per-condition shape:
per_condition_counts = conn.execute(
    f"SELECT condition, COUNT(*) FROM evaluation_runs "
    f"WHERE is_pilot = FALSE{_SCOPE_SQL} GROUP BY condition"
).fetchall()
total = sum(c[1] for c in per_condition_counts)

issues = []
if total != EXPECTED_TOTAL:
    issues.append(f"total rows={total}, expected {EXPECTED_TOTAL}")
for condition, count in per_condition_counts:
    expected_per = EXPECTED_TASKS * EXPECTED_RUNS_PER_CONDITION  # 100
    if count != expected_per:
        issues.append(f"{condition}: {count} rows, expected {expected_per}")
missing_conditions = {"full", "no_verification", "no_optimization", "static"} - {c[0] for c in per_condition_counts}
for c in missing_conditions:
    issues.append(f"condition {c} missing entirely (0 rows)")

if issues and not args.allow_incomplete:
    print(
        "ERROR: dataset appears incomplete — refusing to produce Chapter-6 output.\n"
        "Issues:\n  - " + "\n  - ".join(issues) + "\n"
        "Pass --allow-incomplete to override (output will be marked INCOMPLETE in every table header).",
        file=sys.stderr,
    )
    sys.exit(2)
```

**B. If overridden, mark every table header "INCOMPLETE".**

```python
if args.allow_incomplete and issues:
    completeness_banner = f"[INCOMPLETE: {', '.join(issues)}]"
    # Thread banner into every print_condition_table / print_per_task_table call
    # so the text output makes clear this is partial data.
```

Makes the override loud: user chose to analyze partial data, output tables carry an in-band marker.

**C. CLI flag for subset runs.**

```python
parser.add_argument(
    "--subset-tasks", nargs="+", default=None,
    help="Analyze only these task_ids (e.g. T07,T11). Adjusts expected row counts accordingly.",
)
parser.add_argument(
    "--runs-per-condition", type=int, default=5,
    help="Expected runs per condition (default: 5, matches run_sheet.md row 8).",
)
```

A legitimate subset-eval invocation declares the subset shape; the completeness check adjusts. Subset invocations DON'T silently slip through as if they were full evals.

**D. Regression test.**

```python
def test_analyzer_refuses_to_run_on_incomplete_dataset(tmp_path):
    """200-row DB → exit 2 with 'dataset appears incomplete'."""
    db = seed_partial_eval_db(tmp_path, rows=200)
    with pytest.raises(SystemExit) as exc:
        subprocess.run(["python", "-m", "scripts.analyze_results", "--db", str(db)],
                       check=True)
    assert exc.value.code == 2

def test_analyzer_with_allow_incomplete_marks_tables(tmp_path, capsys):
    """--allow-incomplete produces output with [INCOMPLETE: ...] banners."""
    ...
```

### Recommendation

**Bundle with #49.** Both are structural completeness defences — #49 ensures the DB is campaign-isolated, #52 ensures the campaign is complete. Landing them together closes the "is this the right data and is there enough of it?" pair. ~30 LOC + 2 regression tests. ~45 min.

### Legacy data

Not applicable — the analyzer is the consumer; no data-side migration needed.

---

## Issue #53 — Longitudinal runners skip `output_contract` initialization; decomposition's writer-append safety net never fires on rows 9/10 (P0, CRITICAL)

### Summary

`tests/evaluation/harness.py:653-657` — the canonical path — builds `TaskState` with `output_contract=contract` (the per-task `OutputContract` extracted at task-init time). The decomposition-layer safety net at `src/orchestrator/decomposition.py:725` uses exactly that field to decide whether to append a deterministic writer subtask when the decomposer's LLM forgot (§12 finding #10 Option B).

Both longitudinal runners build `TaskState` without the kwarg:

- `scripts/run_longitudinal.py:211` — `initial_state = TaskState(task_description=task.task_prompt)`
- `scripts/run_longitudinal_shift.py:320` — same line

The default is `None`. Decomposition's guard `if state.output_contract is not None and state.output_contract.required_files:` (line 725) is False → writer-append branch never executes → longitudinal pipelines silently execute a different decomposition semantics than row 8.

Longitudinal runs are the evidence base for Pillar 3 RQ #1/#2/#3 (rows 18/19 of the run sheet). If their pipeline path diverges from the eval-harness path, any conclusion about "how the optimizer / convergence / shift-adaptation behaves on the eval pipeline" is measuring a different pipeline than the one Chapter 6 describes.

### Where

- **Reference (correct) — harness injects contract:** `tests/evaluation/harness.py:653-657`:
  ```python
  initial_state = TaskState(
      task_description=task.task_prompt,
      output_contract=contract,
      allow_network=self._config.allow_network,
  )
  ```
- **Broken — `run_longitudinal.py:211`:** `initial_state = TaskState(task_description=task.task_prompt)` — no `output_contract`, no `allow_network`.
- **Broken — `run_longitudinal_shift.py:320`:** same call.
- **Contract consumer:** `src/orchestrator/decomposition.py:725` — `if state.output_contract is not None and state.output_contract.required_files:` → the writer-append validator (§12 finding #10 Option B) is gated by contract presence.
- **Contract extraction reference:** `src/orchestrator/output_contract_resolver.py` + the task-init path in the harness that calls it.

### Reproduction

```python
# harness path (row 8):
contract = task.output_contract  # present, with required_files=["output.csv"] for T07
state = TaskState(task_description="...", output_contract=contract, allow_network=False)
# decomposition.py:725 — contract branch fires
# → if decomposer didn't emit writer subtask, _ensure_file_writer_last_step appends one
# → final chain: [analyze, ..., write-csv-file (MCP_TOOL)]

# longitudinal path (row 9):
state = TaskState(task_description="...")  # NO output_contract
# decomposition.py:725 — contract branch skipped (contract is None)
# → if decomposer didn't emit writer subtask, chain ends without one
# → final chain: [analyze, ..., (no writer)] — file never produced
# → but task.validator might pass on dict-only metadata (see ext6 #33)
# → silent Pillar-3-biased failure mode
```

Direct inspection of the two code paths confirms the asymmetry.

### Compounding with other findings

- **Compounds with ext6 #32** (success predicate ignores `output_shape_mismatch`): longitudinal runs without contract can't even produce a `shape_check` result (the contract is missing); the longitudinal harness code at `tests/evaluation/harness.py:861-863` does `task.output_contract.check(...)` with `task.output_contract` present regardless — but the CONTRACT-DRIVEN DECOMPOSITION is a different, upstream effect. The chain is built differently; the subsequent shape-check reports "missing files" but the writer never had a chance to be appended.
- **Compounds with ext6 #33** (T07/T11 validators fail-open): a longitudinal pipeline that can't append a writer subtask relies entirely on the decomposer LLM remembering Rule 15. Empirically, the LLM doesn't always remember — that's why the safety-net exists in the first place.
- **Compounds with ext7 #50** (invocation_events pilot contamination): the longitudinal-vs-eval behavioral divergence feeds different invocation rows into the same table; even with `is_pilot` added, the rows represent different pipeline semantics.

### Threat model / impact

- **Pillar 3 RQ #1 (seeded-vs-evolved Gini) biased.** If the longitudinal pipeline never appends writer subtasks, the optimizer sees different capability-usage patterns than the eval pipeline. Gini measured on longitudinal data doesn't generalize to the eval condition's Gini.
- **Pillar 3 RQ #2 (cycles-to-converge) biased.** Convergence dynamics depend on which capabilities get invoked + succeed. A chain without a writer step has different success/failure distribution than one with a writer.
- **Pillar 3 RQ #3 (shift adaptation) biased.** Phase A / Phase B re-convergence measurements are against a pipeline that differs from the headline eval pipeline; Chapter 6's adaptation-verdict claim ("graceful / sluggish / collapse" from Step 18.8) is about a different pipeline than the reader thinks.
- **Direction of bias: probably anti-JIT-pillar-3.** The longitudinal pipeline without contract is "simpler" (no writer step); fewer capabilities, fewer optimizer opportunities, slower convergence. Chapter 6 under-reports Pillar 3 effectiveness.
- **Silent and pervasive.** No error message, no warning. The pipelines run to completion, produce invocation_events, and get analyzed by rows 18/19 — all with the divergent behavior.

### Remediation

**A. Thread `output_contract` + `allow_network` into longitudinal TaskState construction.**

```python
# scripts/run_longitudinal.py:211 — before:
initial_state = TaskState(task_description=task.task_prompt)
# after:
contract = getattr(task, "output_contract", None)
initial_state = TaskState(
    task_description=task.task_prompt,
    output_contract=contract,
    allow_network=False,  # longitudinal doesn't set allow_network today; default explicit
)
```

Same change at `scripts/run_longitudinal_shift.py:320`.

**B. Factor the TaskState construction into a shared helper.**

To prevent the longitudinal paths and the harness path from diverging again in the future:

```python
# src/orchestrator/task_state_builder.py (new module):
def build_initial_state(task: TaskDefinition, *, allow_network: bool = False) -> TaskState:
    """Canonical TaskState construction for every pipeline entry point.

    Security audit finding #53 (2026-04-20): the harness, longitudinal
    runners, shift runner, and any future runner must all build state
    the same way — most importantly, all must inject output_contract
    so decomposition's writer-subtask validator fires identically.
    """
    return TaskState(
        task_description=task.task_prompt,
        output_contract=getattr(task, "output_contract", None),
        allow_network=allow_network,
    )

# All four callers (harness + two longitudinal scripts + any future runner)
# import and use this helper. Adding a new required field to TaskState becomes
# a one-site change.
```

**C. Regression test.**

```python
def test_longitudinal_initial_state_carries_output_contract():
    """TaskState built by longitudinal runner must carry task.output_contract."""
    # Mock the pipeline capture; invoke a minimal longitudinal run.
    # Assert pipeline.invoke received a TaskState with output_contract matching
    # task.output_contract, not None.
    ...

def test_longitudinal_decomposition_appends_writer_subtask_when_required():
    """End-to-end: longitudinal run of a file-output task produces a writer subtask."""
    # Run run_longitudinal.py against a 1-task sequence using T07 (required_files=[output.csv]);
    # assert the final chain's last subtask's required_capabilities starts with "write-".
    ...
```

**D. CLAUDE.md / architecture note.**

Document that every pipeline entry point MUST construct TaskState via `build_initial_state()`; add a code-review checkbox for "if you're touching pipeline entry, use the shared builder."

### Recommendation

**Land before any longitudinal re-run.** The fix is two-line at each call site (~5 min) + the shared helper (~15 min) + tests (~15 min). Any rows 9/10 data pre-fix carries the divergent-pipeline bias; re-run post-fix for Chapter 6.

### Legacy data

Pre-fix longitudinal DBs were collected with the divergent pipeline. No retroactive fix possible — re-run required. Rows 18/19 analysis pre-fix describes a different pipeline than the one Chapter 6 claims; acknowledge in Chapter 4.5 or re-run.

---

## Issue #54 — Rows 13 + 15 of the run sheet don't pass `--session-ids`; scripts support it for contamination control but the procedure doesn't use it (P0, CRITICAL)

### Summary

Ext6 #39 and #42 wired `--session-ids` CLI args on `analyze_context_utilization.py` and `analyze_tier2_probes.py` to allow session-level scope control (parallel-run-safe, campaign-pure LangFuse analysis). The flags landed on both scripts. The run sheet never used them.

- Row 13 (`docs/run_sheet.md:59`): `uv run scripts/analyze_context_utilization.py` — no `--session-ids`.
- Row 15 (`docs/run_sheet.md:61`): `uv run scripts/analyze_tier2_probes.py --environment default` — no `--session-ids`.

Under ext7 #51's pre-fix world where rows 8/9/10 "were parallelizable" per misleading docs, all three produced traces in the same time window that rows 13 + 15 pulled from. Post-#51-fix (serial execution), concurrent smoke tests still contaminate the window. In either regime, session-id scoping is the proper primitive; the run sheet doesn't exercise it.

Same class as ext7 #45 (run-sheet-doesn't-use-script-arg), but applied to LangFuse-trace analyzers instead of DB analyzers.

### Where

- **Run sheet rows:**
  - `docs/run_sheet.md:59` — Row 13.
  - `docs/run_sheet.md:61` — Row 15.
  - `docs/run_sheet.md:41` — the (now-corrected per #51) parallel claim for rows 8/9/10.
- **Scripts with `--session-ids`:**
  - `scripts/analyze_context_utilization.py:447-450` — argparse entry with help text pointing at session_ids examples.
  - `scripts/analyze_tier2_probes.py:380-383` — same shape, with docstring explicitly citing "security audit finding #42 (2026-04-20)".

### Reproduction

```bash
# Pre-#51-fix parallel scenario:
# Terminal 1: row 8 running (rows emit to session_id=eval_<timestamp>_row8).
# Terminal 2: row 9 running (session_id=eval_<timestamp>_row9).
# Terminal 3: row 10 running (session_id=eval_<timestamp>_row10).
$ uv run scripts/analyze_context_utilization.py
# Fetches every trace in the last 14 days, environment=default by ext7 #47 fix
# → pulls from sessions for rows 8/9/10 indiscriminately.
# T3 evidence in Chapter 6 represents a mixture.

# Post-#51-fix serial scenario:
$ uv run scripts/analyze_context_utilization.py
# Fetches every trace in the last 14 days; still includes any smoke-test or
# exploratory run in that window that used the same environment tag.
```

Confirmed by inspection of run_sheet rows and script argparse surfaces.

### Compounding with ext7 #47 and ext6 #39/#42

- **Ext7 #47** fixed `--environment` default for Row 13 (production → default). Good.
- **Ext6 #39** added `--environment` AND `--session-ids` to Row 13. Both flags exist; run sheet uses neither fully (post-#47 #47 defaults to `default` so the env angle is covered, but session-ids still unused).
- **Ext6 #42** added `--session-ids` to Row 15. Same procedure-layer gap.

The fix at the code layer (ext6 + ext7) is done for both analyzers. The procedure layer (run sheet) was never updated to pass them.

### Threat model / impact

- **Chapter 6 Row 13 (T3 naive-verbose context utilization) evidence is not reproducible per-campaign.** Same class as #45 for DB analyzers, applied to LangFuse analyzers.
- **Chapter 6 Row 15 (Tier-2 probe-type breakdown) similarly unreproducible.** The "of the N Tier 2 rejections, which probe types caught them?" claim at Row 15 is about whichever traces happen to be in the fetch window.
- **Direction of bias: depends on what concurrent/recent traces exist.** If smoke tests heavily exercise one probe type, that probe type is over-represented in Row 15. If a separate exploratory eval has different tool distributions, Row 13 T3 numbers shift.
- **Compounds with #51.** Pre-#51-fix, rows 8/9/10 parallel execution MAKES the contamination inevitable. Post-#51-fix, it's only a concern for historical traces in the window — lower probability but not zero.

### Remediation

**A. Update `run_sheet.md` to pass session-ids on rows 13 + 15.**

Same pattern as ext7 #45's `$CAMPAIGN_RUN_IDS`:

```markdown
## Campaign isolation setup (top of run sheet)

```bash
export CAMPAIGN_SESSION_IDS="eval_2026-04-20_row8 eval_2026-04-20_row9 eval_2026-04-20_row10"
```

Each runner emits a session_id tag deterministically from the runner name + timestamp; record them at kickoff for later analysis.
```

Then update rows 13 + 15:

```markdown
| 13 | `uv run scripts/analyze_context_utilization.py --session-ids $CAMPAIGN_SESSION_IDS` | ... |
| 15 | `uv run scripts/analyze_tier2_probes.py --environment default --session-ids $CAMPAIGN_SESSION_IDS` | ... |
```

**B. Script-side refusal when session filter is absent AND traces exceed a threshold.**

```python
# analyze_context_utilization.py + analyze_tier2_probes.py:
if not args.session_ids and total_traces > 1000:
    print(
        f"ERROR: fetched {total_traces} traces without --session-ids filter. "
        f"This is too many to trust as a single-campaign sample. "
        f"Pass --session-ids or --allow-broad-fetch to override.",
        file=sys.stderr,
    )
    sys.exit(2)
```

Heuristic threshold; value depends on typical single-campaign trace counts (~500 for a 20-task pilot, scales linearly). Adjust per-script.

**C. Regression-guard: run-sheet parser (extension of ext7 #45's lint).**

Extend the CI smoke test proposed in ext7 #45 to cover LangFuse-trace-analyzer rows:

```python
LANGFUSE_SCOPE_ARGS = {"--session-ids", "--trace-ids", "--environment"}

LANGFUSE_ANALYZERS = {
    "analyze_context_utilization.py",
    "analyze_tier2_probes.py",
    "analyze_compression_loss.py",  # this one reads DB but some variants also reference traces
}

for row_num, cmd in parsed_run_sheet_rows:
    script = cmd.split("/")[-1].split(" ")[0]
    if script in LANGFUSE_ANALYZERS:
        assert any(arg in cmd for arg in LANGFUSE_SCOPE_ARGS), \
            f"Row {row_num}: LangFuse analyzer {script} invoked without scope arg"
```

### Recommendation

**Bundle with ext7 #45 as one run-sheet update pass.** Both are procedure-layer gaps for already-remediated code-layer flags. ~20 min combined to update the run sheet + (optionally) add the refusal in (B).

### Legacy data

Not applicable — re-running rows 13 + 15 post-fix against the existing LangFuse data, with session-ids populated, produces correctly-scoped output. No trace-data correction needed.

---

## Issue #55 — Rows 18 + 19 of the run sheet don't pass `--run-id`; longitudinal analyzer auto-selects the latest run with WARN only (HIGH)

### Summary

`scripts/analyze_longitudinal.py:188` prints a WARN when multiple longitudinal runs are present in the DB and auto-selects "the latest." The run sheet at `docs/run_sheet.md:64-65` invokes Rows 18 + 19 with no `--run-id`. If the team runs `run_longitudinal.py` twice (e.g., after fixing a bug or after ext6 #31's clean-baseline fix lands), both longitudinal DBs contain multiple run_ids; rows 18 + 19 silently analyze only the latest.

Similar shape to ext7 #46 (auto-select in `analyze_results.py`) but for the longitudinal analyzer. Lower severity than #46 because the auto-select target (latest run) is more sensible for longitudinal (typically one-campaign-per-DB in practice) — but still silently shifts between invocations.

### Where

- **Run sheet unscoped invocations:**
  - `docs/run_sheet.md:64` — Row 18: `uv run scripts/analyze_longitudinal.py`
  - `docs/run_sheet.md:65` — Row 19: `uv run scripts/analyze_longitudinal.py --db data/eval/longitudinal_shifted.duckdb`
- **Analyzer's auto-select path:** `scripts/analyze_longitudinal.py:186-195`:
  ```python
  latest = run_ids_in_db[0]
  if len(run_ids_in_db) > 1:
      import sys as _sys
      print(
          f"WARNING: multiple longitudinal runs present ({len(run_ids_in_db)}); "
          f"auto-selecting latest {latest!r}. Pass --run-id to pick a "
          f"specific run. Available: {run_ids_in_db}",
          file=_sys.stderr,
      )
  ```
  WARN only; never errors out.

### Reproduction

```bash
# Day 1: run the first longitudinal.
$ uv run scripts/run_longitudinal.py
# Writes run_id="a"

# Day 2: fix a bug (e.g., ext6 #31 clean-baseline), re-run.
$ uv run scripts/run_longitudinal.py
# Writes run_id="b" to the SAME longitudinal.duckdb (per ext7 #49 — no DB isolation).

$ uv run scripts/analyze_longitudinal.py
# WARNING: multiple longitudinal runs present (2); auto-selecting latest 'b'.
# [Chapter 6 Pillar 3 tables populated from run 'b']

# Day 3: tweak seed, re-run.
$ uv run scripts/run_longitudinal.py
# Writes run_id="c"

$ uv run scripts/analyze_longitudinal.py
# WARNING: multiple longitudinal runs present (3); auto-selecting latest 'c'.
# Chapter 6 Pillar 3 tables now describe run 'c' — reader never knows that
# Day 2's output described run 'b'.
```

Confirmed by code inspection.

### Relationship to ext6/7 findings

- **Ext6 #24** addressed the `run_id IN (...)` scope filter for `analyze_longitudinal.py`'s main queries. The current code correctly scopes by the resolved run_id; the issue is WHICH run_id gets resolved.
- **Ext7 #46** is the direct analog for `analyze_results.py`. #55 is the longitudinal analyzer's version.
- **Ext7 #49** (Phase-4 DB isolation) makes this worse for longitudinal: the same DB accumulates runs across invocations, so auto-select drift happens on every re-run.

### Threat model / impact

- **Chapter 6 Pillar 3 tables silently shift between run sheet invocations.** A thesis writer who runs Row 18 on Day 2, tweaks the data on Day 3, and re-runs Row 18 gets Day-3-data numbers with no signal that Day 2's numbers described a different run.
- **Direction of bias: depends on which run was latest at analysis time.** Not directional in the absolute sense — but non-reproducible. Reviewer can't regenerate the specific numbers in the thesis from the stated commands.
- **Lower severity than #46.** #46 can drop 80% of the data (4 of 20 eval runs); #55 picks only one longitudinal run at a time, which is (usually) what you want — the issue is you want to CONTROL which one, not have it auto-selected.
- **WARN-to-stderr insufficient.** Same class as #46's stderr WARN. User running `uv run ... 2>/dev/null` (or reading only stdout) misses the WARN. Thesis writer copy-pasting stdout into the manuscript has no signal that the numbers are run-drift-affected.

### Remediation

**A. Update run sheet to pass `--run-id` explicitly.**

Mirror the `$CAMPAIGN_RUN_IDS` pattern from ext7 #45 / #54:

```markdown
## Longitudinal campaign isolation

```bash
export LONGITUDINAL_RUN_ID="longitudinal_2026-04-20_<seed>"
export SHIFT_RUN_ID="longitudinal_shifted_2026-04-20_<seed>"
```

And rows 18 + 19:

```markdown
| 18 | `uv run scripts/analyze_longitudinal.py --run-id $LONGITUDINAL_RUN_ID` | ... |
| 19 | `uv run scripts/analyze_longitudinal.py --db data/eval/longitudinal_shifted.duckdb --run-id $SHIFT_RUN_ID` | ... |
```

**B. Upgrade analyzer WARN to ERROR when multiple runs exist.**

```python
# scripts/analyze_longitudinal.py:186 — before:
if len(run_ids_in_db) > 1:
    print("WARNING: ... auto-selecting latest ...", file=sys.stderr)

# after:
if len(run_ids_in_db) > 1:
    print(
        f"ERROR: multiple longitudinal runs present ({len(run_ids_in_db)}). "
        f"Pass --run-id explicitly to pick one. Available: {run_ids_in_db}. "
        f"Pass --auto-select-latest to restore the WARN-on-ambiguity behavior.",
        file=sys.stderr,
    )
    sys.exit(2)
```

Loud-fail by default; user explicitly opts into auto-select when they want it.

**C. Regression test.**

```python
def test_analyze_longitudinal_fails_loud_on_multiple_runs(tmp_path):
    """Two longitudinal run_ids in DB → exit 2, not auto-select."""
    db = seed_db_with_two_longitudinal_runs(tmp_path)
    with pytest.raises(SystemExit) as exc:
        subprocess.run(["python", "-m", "scripts.analyze_longitudinal",
                        "--db", str(db)], check=True)
    assert exc.value.code == 2

def test_analyze_longitudinal_accepts_explicit_run_id(tmp_path):
    """Passing --run-id explicitly analyzes that run alone."""
    ...
```

### Recommendation

**Bundle with ext7 #45 + #54 as one procedure-layer batch.** All three share the shape "script supports scope arg; run sheet doesn't pass it." ~20 min combined. Must ship before Rows 18 + 19 are used for final Chapter 6 numbers.

### Legacy data

Not applicable — DB contents unchanged; re-running rows 18 + 19 post-fix with the correct `--run-id` produces correctly-scoped output.

---

## Relationship to ext6 findings

#44, #45, #46, and #47 are **follow-up findings** against specific ext6 remediations. #48 is an independent methodology finding. #49 is a structural gap that ext6's analyzer-scoping findings (#38/#40/#41/#42) implicitly papered over by assuming the DB would be cleanly delimited per-campaign — which is not enforced anywhere.

| ext6 finding | Remediation status | ext7 follow-up |
|---|---|---|
| #38 (`analyze_results.py` scoping) | Fix landed at 8 of 10 query sites | **#44** — M2 + M3 sites silently skipped |
| #38 (`analyze_results.py` scoping) | Auto-select fallback added | **#46** — fallback picks wrong 4 run_ids for canonical 20-run eval shape |
| #39 (`analyze_context_utilization.py` scoping) | `--environment` + `--session-ids` wired | **#47** — default `production` doesn't match runners' `default` convention |
| #40 (`analyze_compression_loss.py` scoping) | `--run-ids` wired on script, help text warns | **#45** — `run_sheet.md` invocation unscoped |
| #41 (`audit_contamination.py` scoping) | `--run-ids` wired on script, help text warns | **#45** — `run_sheet.md` invocation unscoped |

Five layers of the same pattern — "fix 80% of the surface, leave 20% for the next audit" — worth naming **remediation-scope drift**:

- #44: fix stopped at 8 of 10 query sites.
- #45: fix stopped at code layer (script has `--run-ids`); procedure layer (run_sheet) never updated.
- #46: fix introduced a helper function (`_resolve_scope`) with a hardcoded constant (4) that wasn't re-validated against the actual eval shape (20).
- #47: fix added a `--environment` flag but defaulted it to LangFuse generic (`production`) instead of this project's convention (`default`).

Proposed guards against future instances:
- **Code-layer lint (AST test):** enumerate `conn.execute` sites and assert each contains the scope variable or is whitelisted. Catches #44-class regressions.
- **Procedure-layer lint (run_sheet parser):** assert every DB-reading row in `run_sheet.md` includes `--run-ids` or `--since`. Catches #45-class regressions.
- **Default-value convention test:** for every analyzer's `--environment` default, assert it matches the value `LANGFUSE_TRACING_ENVIRONMENT` is documented to be set to by thesis runners. Catches #47-class regressions.
- **Canonical-eval-shape test for auto-select:** seed a synthetic DB with the run sheet's `--n-runs` × `conditions` shape and assert the auto-select covers all conditions. Catches #46-class regressions.

#48 is a different class — a long-lived disagreement between the harness docstring ("randomize condition order") and the runner ("fixed order"). Not a remediation-scope drift, but a protocol-code drift. A "docstring-matches-implementation" check is the equivalent guard.

#49 is yet a different class — **missing structural primitive**. Ext6 #38 et al. gave the analyzer layer the ability to scope, but never added the corresponding primitive at the runner layer to enforce what it means for a DB to BE one campaign. The fix is inherently new functionality (a `--db` / `--fresh-db` / `--campaign-id` primitive), not a bug repair. Guard pattern: before fixing an analyzer-side scoping issue, verify the underlying data has a stable scope definition at the producer side.

#50 is a **schema-symmetry drift**. Ext6 #38 and ext7 #44 both address scope (run_id filtering); #50 reveals that a related filter dimension (pilot flag) is present on the primary outcomes table (`evaluation_runs`) but absent on the companion invocation log (`invocation_events`). Two tables that should carry parallel filter primitives diverged when `invocation_events` was added for a different workload (longitudinal, no pilot) and later reused for a workload that has pilots (Chapter 6 Phase 4). Guard pattern: when reusing a schema for a new workload, verify that every filter the existing workload applies has a matching column or join path in the reused schema.

#51 is a **documentation-vs-implementation disagreement**, similar in shape to #48 but inverted: here the documentation over-promises capability that the implementation can't deliver. The run sheet says rows are parallelizable; the code guarantees they're not. Guard pattern: documentation claims about concurrency, isolation, or reproducibility should be tested directly by a smoke script that attempts the claimed pattern and asserts it succeeds.

#52 is a **defence-in-depth gap**. Not a primary correctness bug — the record count IS printed. But a reader who skims past line 1054 can interpret half-complete data as a complete campaign. Guard pattern: any headline quantity with a documented expected value (here "400 records") should be asserted at tool entry, not merely printed.

#53 is a **pipeline-entry-point asymmetry**. The harness constructs `TaskState` with the canonical field set; the longitudinal runners construct a narrower variant that silently opts out of downstream behavior (writer-subtask append). Same shape as cross-cutting observation #4 in the parent audit: "latent invariants held together by cross-file contracts." Guard pattern: every pipeline entry point should use a shared constructor for the state object, and adding a required field on state should be a one-site change — not spread across four copies.

#54 and #55 are additional **remediation-scope drift** instances at the procedure layer (ext7 #45 was the first of this class). #54: ext6 #39/#42 landed `--session-ids` on the LangFuse-trace analyzers but the run sheet never passed it. #55: ext6 #24 scoped the longitudinal analyzer by run_id but the run sheet never selects a specific run_id and the analyzer silently auto-picks the latest. Combined with #45, the run sheet is the single most concentrated source of procedure-layer drift — it needs a systematic campaign-scoping update pass, with a CI lint to prevent re-drift.

---

## Parent-document integration (pending)

When ext7 lands, fold forward into `security_audit_2026_04_19.md`:

1. Update header: "Forty-three findings now → Fifty-five findings; #19-#55 open as of 2026-04-20."
2. Insert TL;DR rows #44 through #55 after ext6's #43.
3. Tranche 3 (eval-blocking set) gains twelve rows at the top — nine are must-fix-before-Phase-4-rerun (#44, #45, #46, #47, #49, #50, #51, #53, #54), three are defence-in-depth/methodology (#48 decision, #52 completeness guard, #55 longitudinal run-id pin).
4. Extend "Behavioral regressions expected" (if adopted into parent):
   - (#44) `analyze_results.py` M2 + M3 queries now respect `--run-ids`; AST lint catches future scope-naïve queries.
   - (#45) `run_sheet.md` requires `--run-ids $CAMPAIGN_RUN_IDS` on every DB-reading row; DB-reader scripts refuse to run without scope when multiple non-pilot campaigns exist in the DB.
   - (#46) `analyze_results.py` auto-select picks N per condition (default 5) instead of 4 total; fails loud when auto-selection would drop a condition entirely.
   - (#47) `analyze_context_utilization.py --environment` defaults to `default` (matches thesis runner convention); analyzer fails loud on zero traces unless `--allow-empty` is passed.
   - (#48) `run_pilot.py --all-conditions` shuffles condition order per run (seeded), or the protocol claim in `harness.py:17` is removed with a Chapter 4.5 disclosure — whichever the advisor chooses.
   - (#49) `run_pilot.py` gains `--db` / `--fresh-db` / `--append` flags; default is a timestamped fresh path; refuses to silently append to a non-empty existing DB. Run sheet sets `$CAMPAIGN_DB` and threads it through every phase-4 + analysis row.
   - (#50) `invocation_events` gains `is_pilot` column; `flush_task_cycle_state` threads the flag; Gini query filters `NOT is_pilot`. Legacy rows backfilled via JOIN to `evaluation_runs`.
   - (#51) Run sheet Phase-4 header says SERIAL (not parallel); runners acquire a cross-process file lock on the shared registry dir and exit 2 on contention; optional per-runner path overrides unlock genuine parallelism.
   - (#52) `analyze_results.py` asserts 400-record + per-condition balance at startup; exits 2 on mismatch unless `--allow-incomplete` is passed (which stamps tables with INCOMPLETE banner).
   - (#53) Longitudinal runners build `TaskState` via a shared `build_initial_state()` helper that injects `output_contract` + `allow_network`; decomposition's writer-append safety net now fires identically across harness + longitudinal paths.
   - (#54) Run sheet rows 13 + 15 pass `--session-ids $CAMPAIGN_SESSION_IDS`; LangFuse-trace analyzers refuse large fetches without session-id filter.
   - (#55) Run sheet rows 18 + 19 pass `--run-id $LONGITUDINAL_RUN_ID`; `analyze_longitudinal.py` exits 2 on multiple runs unless `--auto-select-latest` is explicitly passed.
5. End-of-audit note: "Extended 2026-04-20 (seventh extension) with findings #44 - #55 (detail sections in `security_audit_2026_04_19_ext7.md`). #44 - #47 post-remediation regressions against ext6 #38/#39/#40/#41; #48 methodology; #49 missing structural primitive at runner layer; #50 schema-symmetry drift; #51 documentation-vs-implementation disagreement on parallelism; #52 completeness defence-in-depth gap; #53 pipeline-entry-point asymmetry; #54/#55 additional procedure-layer remediation-scope drift."

---

**End of extension.**
