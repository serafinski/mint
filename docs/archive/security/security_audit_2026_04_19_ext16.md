# Security / Robustness Audit — 2026-04-19, Sixteenth Extension (#174-#187)

**Parent document:** [`security_audit_2026_04_19.md`](./security_audit_2026_04_19.md) — #1-#18 all ✅ FIXED 2026-04-20.
**Sibling extensions:**
- [`security_audit_2026_04_19_ext4.md`](./security_audit_2026_04_19_ext4.md) — #12-#14 ✅ (folded into parent).
- [`security_audit_2026_04_19_ext5.md`](./security_audit_2026_04_19_ext5.md) — #15-#18 ✅ (folded into parent).
- [`security_audit_2026_04_19_ext6.md`](./security_audit_2026_04_19_ext6.md) — #19-#43 ✅ all FIXED 2026-04-20.
- [`security_audit_2026_04_19_ext7.md`](./security_audit_2026_04_19_ext7.md) — #44-#55 ✅ all FIXED 2026-04-20.
- [`security_audit_2026_04_19_ext8.md`](./security_audit_2026_04_19_ext8.md) — #56-#78 (20 ✅ FIXED + 1 ❎ false-positive + 1 ✅ partial + 2 ❎ acknowledged).
- [`security_audit_2026_04_19_ext9.md`](./security_audit_2026_04_19_ext9.md) — #79-#98 ✅ all FIXED 2026-04-21.
- [`security_audit_2026_04_19_ext10.md`](./security_audit_2026_04_19_ext10.md) — #98-#107 ✅ FIXED; #108 ⚠ latent/documented.
- [`security_audit_2026_04_19_ext11.md`](./security_audit_2026_04_19_ext11.md) — #109-#113 ✅ all FIXED 2026-04-21.
- [`security_audit_2026_04_19_ext12.md`](./security_audit_2026_04_19_ext12.md) — #114-#123 ✅ all FIXED 2026-04-21.
- [`security_audit_2026_04_19_ext13.md`](./security_audit_2026_04_19_ext13.md) — #124-#138 ✅ all FIXED 2026-04-21 (commit `61bf17d`).
- [`security_audit_2026_04_19_ext14.md`](./security_audit_2026_04_19_ext14.md) — #139-#150 ✅ all FIXED 2026-04-21 (commit `891be59`).
- [`security_audit_2026_04_19_ext15.md`](./security_audit_2026_04_19_ext15.md) — #151-#173 ❐ open as of 2026-04-22 (reviewer-driven sweep in 9 bundles covering CAMPAIGN_RUN_IDS selector, LangFuse analyzer coverage gates, asymmetric completeness checks, M4 silent-truncation, Row 17 headline-tables, Row 11 backfill integrity, Row 15 prose-vs-command, foreign-generation attribution, run-id cardinality + longitudinal upper-bound).

**This extension's scope.** Four findings surfaced 2026-04-22 via reviewer continuation on the longitudinal runner + Row 18/19 + M5 axes. Three bundles:

**Bundle A (#174-#176) — Longitudinal runner campaign-isolation + selector stale-fallback trio.** Three findings that compound end-to-end: #175 + #176 are architectural omissions in `run_longitudinal.py` and `run_longitudinal_shift.py` — both default to fixed DB paths (`data/eval/longitudinal.duckdb`, `data/eval/longitudinal_shifted.duckdb`) with **no `--fresh-db` / `--append` / existing-DB-guard** matching `run_pilot.py`'s ext7 #49 contract. Prior runs accumulate in-place silently. #174 is the downstream consequence on the Row 18/19 selectors (`docs/run_sheet.md:146-149` + sibling at 156-163): `HAVING COUNT(*) >= :expected ORDER BY MAX(timestamp) DESC LIMIT 1` — **when the newest run is partial (watchdog-aborted, crashed, or shorter `--n-tasks`), the `HAVING` clause drops it and the selector silently falls back to the next-newest complete run, which is a stale older campaign**. Row 18/19 then produces apparently valid Chapter 6 RQ #2/RQ #3 output on entirely wrong data — no loud failure, no stale-data banner. Reviewer submitted #174 unlabeled; **escalating to P0 CRITICAL** (default-workflow-reachable, corrupts headline metric integrity, matches the bar set for #154/#155/#160/#162/#170). #175 + #176 are **P1 confirmed** per reviewer — architectural gaps that enable #174's reachability.

**Bundle B (#177) — M5 latency silent-skip, mirrors #164 on a new metric.** Standalone **P1 confirmed**. `scripts/analyze_results.py:1208-1300` `print_latency_summary(conn) -> None` returns nothing; call site at line 1691 doesn't capture return; `missing_metrics` list at line 1701 has no entry for M5. When no successful runs exist in scope, the function prints `"(no successful runs — nothing to summarize)"` and proceeds → Row 17 exits 0 despite Chapter 6 M5 evidence being structurally absent. Fifth instance of the retrofit gap closed by ext13 #126/#128/#132 + ext14 #149 for M2/M3/M4; #164 closed it for Classification Accuracy; #177 is the same retrofit for M5 latency.

**Bundle C (#178-#180) — Row 13 + Row 15 analyzer silent-drop paths.** Three findings on the LangFuse-analyzer integrity axis, mixed severity. #178 is a **P0 CRITICAL** ordering-gap between the ext14 #139 session-coverage gate (line 676-694, runs FIRST on pre-filter `contributed_sessions_total`) and the ext12 #120 degraded-trace filter (line 710-741, runs SECOND and drops traces whose every observation fetch failed). A session whose traces are ALL degraded passes the gate (its trace IDs contributed pre-filter) then gets dropped entirely from aggregates — Chapter 6 Context Economy silently undercounts without a missing-session banner. #179 is **P1 confirmed** — `extract_trace_context` at line 236-344 flags `_fetch_degraded=True` only when `n_failed == n_attempted` (all fetches failed); partial failures (some succeed, some fail) set `_fetch_degraded=False` at line 343-344 despite missing observations materially biasing peak/chain token aggregates low. Trace enters aggregates flagged as complete. #180 is a **P0 CRITICAL** silent-erasure path on Row 15: `aggregate()` at `scripts/analyze_tier2_probes.py:264-268` skips events whose `metadata.categories` isn't a list via `continue`, without incrementing a malformed-events counter. Downstream `render_table` then reads `if not bd.categories: print("No tier-2 findings observed...")` — on metadata shape drift, Row 15 prints `"50 events, 12 rejects, No findings observed"` contradictory narrative with Chapter 4.3 evidence structurally erased. Currently latent under the runner's always-list emission but no guard against SDK version drift or third-party emitter changes; escalated to P0 on structural-correctness grounds.

**Bundle D (#181) — Pillar 1 RQ #1 proxy scoring admits registry-match rows where no classifier ran.** Standalone **P0 CRITICAL confirmed**. `scripts/analyze_results.py:1131-1137` SQL scores `capability_type` on every non-static row; rows with `matched_in_registry_count > 0 AND generated_artifact_count == 0` (pure registry matches — retrieval tasks T01-T03 plus any task with seeded-registry coverage) carry a `capability_type` inherited from the registered capability, not produced by the classifier. Counted as hits, silently inflating Chapter 6 Pillar 1 RQ #1 proxy accuracy. Default-workflow-reachable. Same family as #166 (classification SQL uses blacklist `!= 'static'` rather than whitelist) but on a different axis — #166 includes extra condition labels; #181 includes rows that bypass the classifier entirely. Two-layer fix: first WHERE-clause filter on `generated_artifact_count > 0` (only score rows where the classifier produced output); then downstream per-condition accuracy reporter distinguishes "scored" from "registry-only" in the footer.

**Bundle E (#182-#184) — Row 13 + Row 14 analyzer sub-shape gaps.** Three findings, all **P1 confirmed**. #182 is a missing-file-contract-violation path in Row 14's T3 compression-loss classifier: `FailureRow` dataclass at line 89-99 omits `output_shape_missing_files`; classifier at line 120 requires `output_shape_missing_keys` truthy. File-only violations (empty keys list + non-empty files list) fall through to generic buckets; `compression_loss_task` undercounts. #183 is a hardcoded `MODEL_CONTEXT_WINDOW = 128_000` at `analyze_context_utilization.py:50-51` — utilization percentages assume gpt-5-mini uniformly despite the pipeline supporting `JIT_LLM_MODEL` override per `src/config.py`. Per-step `model` field captured but never used for per-step window mapping; future model migration or env-override silently biases Row 13 percentages. #184 is the third shape in the `extract_trace_context` empty-steps branch: `fetch_degraded = n_attempted > 0 and n_failed == n_attempted` at line 294 short-circuits to False when `n_attempted == 0` (trace had empty observation_ids). Such traces enter aggregates as zero-util silently. Complements #179 (partial-fetch case) with the "no-fetches-attempted" case.

**Bundle F (#185-#187) — Harness write-path integrity: enum-vs-writer mismatch crash + silent-telemetry-drop + snapshot-vs-count parity gap.** Three findings on the `tests/evaluation/harness.py` write path, mixed severity. **#185** is **P0 CRITICAL**: three writer sites at lines 647, 1054, 1369 assign `failure_reason = "output_shape_mismatch"` but `FailureReason` StrEnum at `src/schemas/evaluation.py:45-71` does NOT include that value. `EvaluationRecord` Pydantic validator at `src/schemas/evaluation.py:165` fails with `ValidationError` when `in_range=True AND shape_mismatch=True` is encountered. No try/except around the `_run_one_task` call at `tests/evaluation/harness.py:435-447`. Exception propagates and can **crash the harness mid-campaign**, leaving partial data in the DB. Reviewer confirmed with direct model instantiation. **#186** is **P1 confirmed**: invocation-recording try/except with `logger.debug` in `composition.py:483` + `harness.py:663` silently swallows exceptions; `cycle_flush.py:118` sets `invocations_ok=True` unconditionally on `record_invocation_events` success; `evaluation_store.py:343-344` returns 0 on empty events — chain admits zero-insert as healthy. Chapter 6 M3 Gini biased on under-written telemetry without a surface signal. **#187** is **P1 confirmed**: `_snapshot_artifacts` at `harness.py:158-188` carries an explicit "never-raises contract" (line 186 `continue` on exception); `generated_artifact_count` counter at 1061-1063 + 1079 is derived independently from snapshot output. No parity gate between the two paths. Silent drops create rows claiming N generated artifacts but carrying fewer source bodies — Row 16 auditors see partial evidence without a loud signal. Fix family: enum membership verification for #185, per-task inserted-count gate for #186, parity-count assertion for #187.

**Status at time of write (2026-04-22):** fourteen **open**. Total open across audit series: ext8 (2) + ext10 (1 latent) + ext15 (23) + ext16 (14) = **~40 findings**.

| # | Severity | Scope | Impact | Status |
|---|---|---|---|---|
| 174 | **P0 CRITICAL (confirmed, escalated from unlabeled)** | `docs/run_sheet.md:146-149` (LONGITUDINAL_RUN_ID) + 156-163 (SHIFT_RUN_ID) use `HAVING COUNT(*) >= ? ORDER BY MAX(timestamp) DESC LIMIT 1`; when the newest run is partial, HAVING drops it and the selector picks the next-newest complete run instead | Silent fallback to stale older campaign. Watchdog abort (ext12 #119 exit 3/5), ungraceful interrupt, shorter `--n-tasks` on re-run, or crashed campaign all produce a partial newest run that gets silently skipped — Row 18/19 then operates on a prior campaign's data without any signal. Chapter 6 RQ #2 (cycles-to-convergence) + RQ #3 (distribution-shift adaptation) produced from entirely wrong data. Reachability amplified by #175 + #176 (fixed default DBs, silent accumulation). Default-workflow-reachable without contract violation. Distinct from #173 (admits overfull of the latest) — #174 selects a wholly different older run. | ❐ OPEN |
| 175 | **P1 (confirmed)** | `scripts/run_longitudinal.py:50, 69` defines `DEFAULT_DB = str(DATA_DIR / "eval" / "longitudinal.duckdb")`; `--db` defaults to it with no `--fresh-db` / `--append` / existing-DB-guard matching `run_pilot.py`'s ext7 #49 contract | Prior longitudinal runs accumulate in the same DB silently. Enables #174 (selector stale-fallback) reachability. Campaign isolation contract for Row 18 broken by default. Unlike `run_pilot.py` which enforces "one DB = one campaign" via ext7 #49's fresh-timestamped-default + exit-2-on-existing-non-empty guard, longitudinal runner has no isolation at all. | ❐ OPEN |
| 176 | **P1 (confirmed)** | `scripts/run_longitudinal_shift.py:79, 166` defines `DEFAULT_DB = str(DATA_DIR / "eval" / "longitudinal_shifted.duckdb")`; `--db` default with no freshness contract | Sibling of #175 on shift runner. Prior distribution-shift runs accumulate silently → Row 19 selector stale-fallback via #174. | ❐ OPEN |
| 177 | **P1 (confirmed)** | `scripts/analyze_results.py:1208-1300` `print_latency_summary(conn) -> None` has no return value; call site at 1691 doesn't capture return; `missing_metrics` accumulation at 1701 has no entry for M5; empty-rows branch at 1267-1268 prints dashed placeholder and proceeds | Row 17 silently exits 0 when M5 latency section has zero successful runs or partial per-condition coverage. Chapter 6 M5 evidence structurally absent but no loud error. Fifth retrofit in the ext13 #126/#128/#132 + ext14 #149 family (M2/M3/M4 plus #164 Classification Accuracy); #177 closes the same gap for M5. | ❐ OPEN |
| 178 | **P0 CRITICAL (confirmed)** | `scripts/analyze_context_utilization.py:676-694` ext14 #139 session-coverage gate runs FIRST on `contributed_sessions_total` (populated pre-filter during trace fetch); lines 710-741 ext12 #120 degraded-filter runs SECOND and drops traces whose every observation fetch failed. Ordering gap | A session whose traces are ALL marked `_fetch_degraded=True` passes the gate (trace IDs contributed pre-filter) then gets zeroed from `results[agent_name]` by the degraded filter. Session silently absent from Chapter 6 Context Economy aggregates — no missing-session banner, no coverage gate re-evaluation. Default-workflow-reachable under LangFuse transient-failure window affecting a session's every trace. Row 13 output silently undercounts. | ❐ OPEN |
| 179 | **P1 (confirmed)** | `scripts/analyze_context_utilization.py:236-344` `extract_trace_context` sets `_fetch_degraded` only when `n_failed == n_attempted` (line 294); line 343-344 when steps exist `"_fetch_degraded": False` unconditionally — partial-fetch failures kept as complete | Trace with partial observation-fetch failure enters aggregates flagged complete. `peak_input_tokens = max(steps)` at line 312 understates true peak if missing observations had larger tokens; `total_chain_*` at 322-324 undercounts proportional to loss rate. Chapter 6 Context Economy averages silently biased low. Same structural shape as #161 on a different analyzer. Comment at line 341-343 explicitly concedes the design; fix is to flag `_fetch_partial` alongside `_fetch_degraded` with coverage-ratio disclosure. | ❐ OPEN |
| 180 | **P0 CRITICAL (confirmed)** | `scripts/analyze_tier2_probes.py:264-268` `aggregate()` silently skips events on `if not isinstance(categories, list): continue` without incrementing a malformed-events counter; `render_table` at line 318-326 prints `"No tier-2 findings observed"` when `bd.categories` is empty, even if `bd.events_total > 0 AND bd.events_reject > 0` | Metadata shape drift (SDK version change, third-party emitter) silently erases Row 15 Chapter 4.3 evidence. Output prints contradictory narrative: `50 events, 12 rejects, No findings observed`. Currently latent under the runner's always-list emission but no structural guard. Fix: track `events_malformed_categories` counter; loud-fail on `events_malformed > 0 AND bd.categories empty`; emit stderr warning on any non-list categories encounter. | ❐ OPEN |
| 181 | **P0 CRITICAL (confirmed)** | `scripts/analyze_results.py:1131-1137` SQL filters `is_pilot=FALSE AND condition != 'static'` only; does NOT filter on `generated_artifact_count > 0`. Rows with `matched_in_registry_count > 0 AND generated_artifact_count == 0` (pure registry matches) carry `capability_type` inherited from the registered capability, not classifier output | Registry-match rows silently counted as classifier hits. T01-T03 retrieval tasks and any task with seeded-registry coverage fall into this bucket. Chapter 6 Pillar 1 RQ #1 proxy accuracy inflated proportional to the fraction of registry-satisfied rows. Default-workflow-reachable — the classifier-bypass path is precisely when the pipeline's JIT synthesis is not needed (successful Pillar 1 hit). Fix: SQL filter `AND generated_artifact_count > 0`; distinguish "scored by classifier" from "registry-only" in the output footer. | ❐ OPEN |
| 182 | **P1 (confirmed)** | `scripts/analyze_compression_loss.py:89-121` `FailureRow` dataclass at 89-99 omits `output_shape_missing_files` (present in schema at `src/registry/evaluation_store.py:54`); classifier at 120 requires `row.output_shape_missing_keys` truthy | File-only contract violations (empty keys list + non-empty files list from `_collect_output_files` mismatch) fall through to generic buckets (class 5 output_rejected, class 8 chain_step_failed). T3 aggressive-compression `compression_loss_task` undercounts proportional to file-contract-heavy tasks in testbed (T19/T20 + AGENT tasks with required_files). Chapter 6 Row 14 9-bucket taxonomy biased. Fix: add `output_shape_missing_files` to FailureRow + extend line 120 to `(keys or files)`. | ❐ OPEN |
| 183 | **P1 (confirmed)** | `scripts/analyze_context_utilization.py:50-51` hardcodes `MODEL_CONTEXT_WINDOW = 128_000` (GPT-5-mini); lines 337-340 compute utilization percentages against this fixed constant for every trace; per-step `model` field captured at line 277 never used for per-step window mapping | Currently latent under default `JIT_LLM_MODEL=openai:gpt-5-mini`. Env-var override or future model migration (Opus 200k, GPT-5 200k/400k, Haiku 200k, mixed-model traces) silently biases Row 13 percentages — a 200k-window model reports halved utilization. Chapter 6 reader can't detect the drift from output shape. Fix: `MODEL_CONTEXT_WINDOWS: dict[str, int]` table + per-step lookup + WARNING on unknown-model fallback to default. | ❐ OPEN |
| 184 | **P1 (confirmed)** | `scripts/analyze_context_utilization.py:289-306` empty-steps branch sets `fetch_degraded = n_attempted > 0 and n_failed == n_attempted` — when `n_attempted == 0` (trace has empty `observation_ids`), the condition short-circuits to False; trace returns `_fetch_degraded=False` with all-zero metrics | Traces with ingest-incomplete envelopes (empty `observation_ids` from LangFuse partial ingest, corrupted trace write, trace fetched pre-observation-flush) enter aggregates as legitimate zero-util. Silent downward bias on Chapter 6 Context Economy. Complements #179 (partial-fetch as complete) with "no-fetches-attempted as complete." Fix: `fetch_degraded = (n_attempted == 0) or (n_failed == n_attempted)` to catch both ingest-incomplete and all-failures, preserving "observations present but non-GENERATION" as the only legitimate-zero path. | ❐ OPEN |
| 185 | **P0 CRITICAL (confirmed)** | `tests/evaluation/harness.py:647, 1054, 1369` three writer sites assign `failure_reason = "output_shape_mismatch"`; `src/schemas/evaluation.py:45-71` `FailureReason` enum does NOT include that value; `src/schemas/evaluation.py:165` validates `failure_reason: FailureReason` on `EvaluationRecord` construction; `tests/evaluation/harness.py:547` passes the value through Pydantic; no try/except around the `_run_one_task` call at 435-447 | Pydantic `ValidationError` on every shape-mismatch row (`in_range=True AND shape_mismatch=True`). Exception propagates from `EvaluationRecord(...)` through `_run_one_task` → `_execute_run` loop → **campaign aborts mid-run**. Rows written before the crash persist as partial data; Chapter 6 analyses operate on biased partial campaigns. Default-workflow-reachable on any canonical campaign where a JIT or static run produces in-range output with a shape mismatch. Fix: add `OUTPUT_SHAPE_MISMATCH = "output_shape_mismatch"` to the enum; verify all three writer sites match the exact string. | ❐ OPEN |
| 186 | **P1 (confirmed)** | `src/orchestrator/composition.py:458, 483-486` + `tests/evaluation/harness.py:653-667` wrap `metrics_store.record_invocation` in `try/except Exception` with `logger.debug`, no counter; `tests/evaluation/cycle_flush.py:107-123` sets `result.invocations_ok = True` after `record_invocation_events(events=fetched)` succeeds, no row-count check; `src/registry/evaluation_store.py:343-344` returns `0` on empty events | Invocation writes fail silently at record time → `fetch_invocations_for_task` returns `[]` → `record_invocation_events([])` returns 0 without error → `invocations_ok=True` → Row 8 FlushResult reports healthy → exit 0 → Chapter 6 M3 Gini computed on under-written telemetry. Direction of bias depends on WHICH invocations fail to record — if JIT condition's invocations drop preferentially, M3 Gini distortion is thesis-flattering. Default-workflow-reachable under any MetricsStore transient error (DB lock, write permission, disk pressure). | ❐ OPEN |
| 187 | **P1 (confirmed)** | `tests/evaluation/harness.py:158-188` `_snapshot_artifacts` explicit "never-raises contract" (line 186 `continue` on exception); `tests/evaluation/harness.py:1061-1063, 1079, 1093` + `1386, 1392` derive `generated_artifact_count` from `resolution.values()` independently of `_snapshot_artifacts(artifacts)` output; no parity assertion between the two | Silent snapshot drops create rows with `generated_artifact_count = N` but `len(generated_artifact_sources) < N`. Row 16 `audit_contamination.py` samples pool by `generated_artifact_count > 0`; human auditor reviews the sampled row and sees fewer source bodies than the count suggests. M2 contamination labels assigned against partially-missing evidence — some artifacts audited, others invisible. Chapter 6 M2 biased toward "clean" (unaudited dropped snapshots can't contribute contamination labels). | ❐ OPEN |

---

## Issue #174 — Row 18/19 selectors silently fall back to older complete run when newest is partial; `HAVING COUNT(*) >= ? ORDER BY MAX(timestamp) DESC LIMIT 1` produces stale-data Chapter 6 output (P0 CRITICAL, escalated from unlabeled)

### Summary

`docs/run_sheet.md:142-169`. Both longitudinal selectors (`LONGITUDINAL_RUN_ID` + `SHIFT_RUN_ID`):

```sql
SELECT run_id FROM evaluation_runs
GROUP BY run_id
HAVING COUNT(*) >= ?
ORDER BY MAX(timestamp) DESC LIMIT 1
```

The query has two clauses interacting:

1. `HAVING COUNT(*) >= :expected_rows` — filter candidates to runs with at least `:expected_rows` cycles.
2. `ORDER BY MAX(timestamp) DESC LIMIT 1` — among candidates, pick the most recently modified.

**If the newest run is partial** (fewer than `:expected_rows` cycles), `HAVING` drops it from the candidate set. The `ORDER BY ... LIMIT 1` then picks the **next-newest complete run** — which is an **older campaign**. The selector returns that older run_id. Row 18/19 operate on stale data. No warning. No fallback-detection signal.

### How a "partial newest run" arises

Under the default workflow (without #175/#176's freshness guard), `run_longitudinal.py` appends into `data/eval/longitudinal.duckdb` on every invocation. Paths to a partial newest run:

1. **ext12 #119 watchdog abort.** The per-task watchdog (`--per-task-budget-seconds`, default 1800s) or campaign watchdog (`--campaign-budget-hours`, default 12h) fires mid-campaign → runner exits 3 or 5 → partial cycles persisted.
2. **Ungraceful interrupt.** Ctrl-C, OS kill, disk full, LangFuse outage → partial cycles persisted.
3. **Shorter `--n-tasks` on re-run.** User re-invokes `run_longitudinal.py --n-tasks 20` during exploration → 20-cycle run written alongside prior 40-cycle run; `:expected_rows=40` excludes the newer one.
4. **Docker-daemon failure mid-campaign.** Container crash kills the sandbox mid-cycle; subsequent cycles abort → partial run persisted.
5. **Pipeline recursion/timeout.** DeepAgent recursion-limit hit on a task drops the task to failure; partial cycles still persisted per `cycle_flush` semantics.

All five produce a DB with:
- run_id_A (T1): 40 cycles — complete
- run_id_B (T2 > T1): 20 cycles — partial (newest)

Selector picks run_id_A (the stale one). Row 18 computes convergence / KL on the **wrong campaign**.

### Why P0 CRITICAL (escalated from unlabeled)

- **Default-workflow-reachable without contract violation.** Any operator who re-runs longitudinal (common — exploration phase, crash recovery, parameter tuning) can hit this.
- **Silent.** No banner, no warning, no stale-data signal. The exported `$LONGITUDINAL_RUN_ID` is a valid run_id from the DB; the analyzer receives it; all downstream gates pass (the older run IS complete, just not current).
- **Directly corrupts Chapter 6 RQ #2 + RQ #3.** Convergence / adaptation metrics cited in the thesis would be from an abandoned exploration run, not the campaign the operator thinks they're analyzing.
- **Matches the bar** set for #154/#155/#160/#162/#170: headline-integrity corruption reachable under default workflow.

Distinct from #173 (admits overfull of the latest run — merges two campaigns under one run_id): #174 picks a wholly different older run_id when the newest is partial. Complementary failure modes, both need fixing.

### Proposed fix (two-layer)

**Layer 1 — selector: pick the actual latest run_id, then validate completeness.**

```sql
-- ext16 #174: pick the latest run_id UNCONDITIONALLY, then validate
-- completeness. If the latest is partial, fail LOUDLY rather than
-- falling back to stale older data.
--
-- Step A: find the newest run_id (regardless of completeness).
WITH latest AS (
    SELECT run_id, COUNT(*) AS n_rows, MAX(timestamp) AS ts
    FROM evaluation_runs
    GROUP BY run_id
    ORDER BY ts DESC
    LIMIT 1
)
-- Step B: emit the run_id only if it passes the completeness bar.
SELECT run_id FROM latest WHERE n_rows = :expected_rows  -- strict equality per #173
```

If the CTE's row fails the WHERE, the query returns empty; the run-sheet's existing `if row is None` branch fires with a distinct error:

```bash
if row is None:
    # Report what we found to distinguish stale-fallback risk from genuine empty-DB.
    latest_info = conn.execute('''
        SELECT run_id, COUNT(*), MAX(timestamp)
        FROM evaluation_runs GROUP BY run_id
        ORDER BY MAX(timestamp) DESC LIMIT 1
    ''').fetchone()
    if latest_info:
        sys.exit(
            f'ERROR: latest longitudinal run {latest_info[0]!r} has '
            f'{latest_info[1]} rows, expected exactly '
            f'{${EXPECTED_LONGITUDINAL_ROWS}}. The pre-fix selector would '
            f'have silently fallen back to an older complete run. Options: '
            f'(a) re-run row 9, (b) pass --run-id <specific> explicitly, '
            f'(c) investigate the partial run (watchdog abort? crash?). '
            f'ext16 #174. Exit 3.'
        )
    sys.exit(
        f'ERROR: no longitudinal runs in DB. Run row 9 first. ext16 #174. Exit 3.'
    )
```

**Layer 2 — pair with #173's strict-equality fix.** Once #173 lands `COUNT(*) = :expected_rows`, #174's Step B WHERE clause inherits that semantic. Merged/overfull runs (the #173 surface) and partial-newest runs (the #174 surface) both fail the completeness bar; the selector produces no false positive in either direction.

### Proposed test

Extend `tests/unit/test_run_sheet.py`:

```python
def test_longitudinal_selector_rejects_partial_newest_rather_than_falling_back():
    """ext16 #174: when the newest run is partial, the selector must fail
    loudly rather than silently selecting an older complete run. Pre-fix
    `HAVING COUNT(*) >= :expected ORDER BY MAX(timestamp) DESC LIMIT 1`
    silently fell back to stale campaign data.
    """
    text = RUN_SHEET.read_text()
    # The new selector must pick the latest run UNCONDITIONALLY, then
    # validate. Look for either a CTE-based "latest then validate" shape
    # or an inline "= :expected with explicit stale-fallback guard" shape.
    for label in ["LONGITUDINAL_RUN_ID", "SHIFT_RUN_ID"]:
        block_match = re.search(rf"{label}=\"\$\(.*?\)\"", text, re.DOTALL)
        assert block_match, f"{label} selector missing from run sheet"
        block = block_match.group(0)
        # The fallback must be explicit: either the selector picks latest
        # without HAVING filter, or there's an explicit stale-detection branch.
        assert (
            "ORDER BY MAX(timestamp) DESC LIMIT 1" in block
            and "HAVING COUNT(*) >= " not in block
        ) or "ext16 #174" in block, (
            f"ext16 #174: {label} selector must not use `HAVING COUNT(*) >= "
            f"... ORDER BY timestamp DESC LIMIT 1` — that silently falls back "
            f"to older complete runs when the newest is partial. Use a CTE "
            f"that picks latest unconditionally then validates completeness."
        )
```

### Interaction with other findings

- **#173** (selector admits overfull): both #173 and #174 are selector-correctness gaps. #173 fails when the latest is merged/overfull; #174 fails when the latest is partial. Land together for full end-to-end Row 18/19 integrity.
- **#172** (analyzer shortfall-only gate): even if #174 exports a stale older run, the analyzer's gate accepts it as "complete" (it is — from a prior campaign). So #172's strict-equality fix does NOT catch #174 — the older run is legitimately 40 cycles. #174 must be fixed at the selector layer.
- **#175 + #176** (fixed DB paths, silent accumulation): enable #174's reachability. Fixing #175 + #176 alongside makes stale runs structurally rarer; fixing #174 closes the final silent-failure gap.

---

## Issue #175 — `run_longitudinal.py` defaults to fixed DB path with no `--fresh-db` / `--append` guard; prior runs accumulate silently (P1, confirmed)

### Summary

`scripts/run_longitudinal.py:50, 69`:

```python
DEFAULT_DB = str(DATA_DIR / "eval" / "longitudinal.duckdb")
...
parser.add_argument("--db", type=str, default=DEFAULT_DB, help="DuckDB output path")
```

There is **no check** for whether the DB already exists. No `--fresh-db` / `--append` flag family. No exit-2 contract on existing non-empty file.

### Contrast with `run_pilot.py`'s ext7 #49 contract

`scripts/run_pilot.py:175-262` (the sibling Phase-4 runner):

```python
# ext7 #49 (2026-04-20): campaign isolation.
parser.add_argument(
    "--db", type=str, default=None,
    help="DuckDB path (default: data/eval/metrics_<timestamp>.duckdb — a fresh "
         "campaign-scoped path). ..."
)
parser.add_argument("--fresh-db", action="store_true", ...)
parser.add_argument("--append", action="store_true", ...)
...
if args.db is None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    db_path = DATA_DIR / "eval" / f"metrics_{ts}.duckdb"
elif args.db == "legacy":
    db_path = Path(_LEGACY_DB_PATH)
else:
    db_path = Path(args.db)

if db_path.exists() and db_path.stat().st_size > 0:
    if args.fresh_db:
        db_path.unlink()
    elif not args.append:
        print(
            f"ERROR: DB exists at {db_path} and is non-empty. Pass "
            "--fresh-db to wipe it, --append to add to it, or choose a "
            "different --db path.",
            file=sys.stderr,
        )
        sys.exit(2)
```

Contract: fresh timestamped default + exit-2 on existing-non-empty unless explicit `--fresh-db` / `--append`. **Longitudinal runner has none of this.** Invoking it twice silently appends to the same file.

### Reachability + downstream impact

Any re-invocation of `run_longitudinal.py` (exploration, crash recovery, parameter-tuning iterations, deliberate continued accumulation) adds rows to the same DB. Rows from prior runs persist. The longitudinal DB becomes a multi-campaign pool, exactly like the state ext7 #49 was designed to prevent for Phase-4 DBs.

Downstream consequences:

1. **#174 selector stale fallback.** Multiple runs in one DB means the selector's `ORDER BY MAX(timestamp) DESC LIMIT 1` has multiple candidates. If the newest is partial, the selector silently picks an older complete run from a prior campaign.
2. **#172 analyzer overfull** (when combined with #153 collision): if two logical runs happen to share a `run_id` (across campaigns), their cycles merge under one key.
3. **Disk usage growth.** Unbounded accumulation over development lifetime.

### Why P1 (confirmed)

Reviewer-submitted severity. The architectural omission itself is P1 (operational hygiene, not direct Chapter 6 corruption); the downstream compound effect with #174 is P0 but that severity lives on #174. Landing #175 (plus #176 for the shift runner) removes the primary reachability path for #174.

### Proposed fix

Mirror `run_pilot.py`'s ext7 #49 contract on `run_longitudinal.py`:

```python
DEFAULT_DB_DIR = DATA_DIR / "eval"
# ext16 #175: default to a fresh timestamped path matching run_pilot.py's
# ext7 #49 contract. Fixed path available via explicit --db argument; legacy
# shared path kept for back-compat but discouraged.
_LEGACY_LONGITUDINAL_DB = str(DEFAULT_DB_DIR / "longitudinal.duckdb")

parser.add_argument(
    "--db", type=str, default=None,
    help="DuckDB output path (default: data/eval/longitudinal_<timestamp>.duckdb "
         "— fresh campaign-scoped path per ext16 #175). Use an explicit path "
         "to re-use or wipe an existing DB (see --fresh-db / --append). "
         "Pass 'legacy' for the pre-fix shared path (NOT recommended).",
)
parser.add_argument("--fresh-db", action="store_true", ...)  # ext16 #175
parser.add_argument("--append", action="store_true", ...)    # ext16 #175
...
if args.fresh_db and args.append:
    print("ERROR: --fresh-db and --append are mutually exclusive.", file=sys.stderr)
    sys.exit(2)

if args.db is None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    db_path = DEFAULT_DB_DIR / f"longitudinal_{ts}.duckdb"
    print(f"ext16 #175: using fresh timestamped DB path: {db_path}")
elif args.db == "legacy":
    db_path = Path(_LEGACY_LONGITUDINAL_DB)
else:
    db_path = Path(args.db)

if db_path.exists() and db_path.stat().st_size > 0:
    if args.fresh_db:
        db_path.unlink()
        print(f"ext16 #175: wiped existing DB at {db_path} per --fresh-db")
    elif not args.append:
        print(
            f"ERROR: DB exists at {db_path} and is non-empty. Pass "
            "--fresh-db to wipe it, --append to add to it, or choose a "
            "different --db path. ext16 #175.",
            file=sys.stderr,
        )
        sys.exit(2)
```

### Proposed test

```python
def test_run_longitudinal_rejects_existing_db_without_flag(tmp_path, monkeypatch):
    """ext16 #175: re-invocation against an existing non-empty longitudinal
    DB must exit 2 unless --fresh-db or --append is passed explicitly.
    Matches run_pilot.py's ext7 #49 contract.
    """
    existing = tmp_path / "existing.duckdb"
    existing.write_bytes(b"\x00" * 100)  # simulate non-empty

    import subprocess
    result = subprocess.run(
        ["uv", "run", "scripts/run_longitudinal.py",
         "--db", str(existing), "--n-tasks", "2", "--campaign-budget-hours", "0"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2, (
        "ext16 #175: existing non-empty DB without --fresh-db/--append "
        "must exit 2, matching run_pilot.py's ext7 #49 contract"
    )
    assert "ext16 #175" in result.stderr
```

### Interaction with #176

Identical pattern on `run_longitudinal_shift.py`. Land #175 + #176 together; they share the fix template.

---

## Issue #176 — `run_longitudinal_shift.py` defaults to fixed DB path with no freshness guard; sibling of #175 on the shift runner (P1, confirmed)

### Summary

`scripts/run_longitudinal_shift.py:79, 166`:

```python
DEFAULT_DB = str(DATA_DIR / "eval" / "longitudinal_shifted.duckdb")
...
parser.add_argument(
    "--db", type=str, default=DEFAULT_DB,
    help="DuckDB output path (default: data/eval/longitudinal_shifted.duckdb)",
)
```

Identical shape to #175: fixed default path, no existing-DB check, no `--fresh-db` / `--append` contract.

### Why P1 (confirmed)

Same reasoning as #175. Sibling finding on the shift runner; sibling fix. Separately tracked so that the changelog + tests cover both files.

### Proposed fix

Apply the same pattern as #175 to `scripts/run_longitudinal_shift.py`:

```python
DEFAULT_DB_DIR = DATA_DIR / "eval"
_LEGACY_SHIFT_DB = str(DEFAULT_DB_DIR / "longitudinal_shifted.duckdb")

parser.add_argument(
    "--db", type=str, default=None,
    help="DuckDB output path (default: data/eval/longitudinal_shifted_<timestamp>.duckdb "
         "— fresh campaign-scoped path per ext16 #176). Pass 'legacy' for the "
         "pre-fix shared path (NOT recommended).",
)
parser.add_argument("--fresh-db", action="store_true", ...)  # ext16 #176
parser.add_argument("--append", action="store_true", ...)    # ext16 #176
# ... same validation + path-resolution block as #175 ...
```

### Interaction with run-sheet Rows 9/10

`docs/run_sheet.md:59-60` currently shows Rows 9/10 with no `--db` flag:

```
| 9 | `uv run scripts/run_longitudinal.py` | ...
| 10 | `uv run scripts/run_longitudinal_shift.py --phase-a-tasks T01,T02,T03,T04,T05 --phase-b-tasks T11,T12,T13,T14,T15 --shift-at 20 --total 40` | ...
```

After #175 + #176 land, update both to an explicit campaign-scoped path matching Row 8's `$CAMPAIGN_DB` pattern:

```bash
export CAMPAIGN_LONGITUDINAL_DB="data/eval/longitudinal_$(date +%Y-%m-%d_%H%M%S).duckdb"
export CAMPAIGN_SHIFT_DB="data/eval/longitudinal_shifted_$(date +%Y-%m-%d_%H%M%S).duckdb"
```

And thread `$CAMPAIGN_LONGITUDINAL_DB` / `$CAMPAIGN_SHIFT_DB` through Rows 9/10/18/19 consistently. This removes the last implicit shared-DB assumption from the run sheet.

---

## Issue #177 — `print_latency_summary` returns `None`; Row 17 silently exits 0 when M5 latency section has no rows (P1, confirmed)

### Summary

`scripts/analyze_results.py:1208`:

```python
def print_latency_summary(conn: duckdb.DuckDBPyConnection) -> None:
    """..."""
    rows = conn.execute(...).fetchall()
    ...
    if not rows:
        lines.append("(no successful runs — nothing to summarize)")
    else:
        for condition, n, avg, med, p95, stdev in rows:
            ...
```

Call site at line 1691:

```python
print_latency_summary(conn)  # return value ignored (function is -> None)
```

`missing_metrics` list at line 1701-1718:

```python
missing_metrics: list[str] = []
if not print_gini_summary(conn):
    missing_metrics.append("M3 (Gini / Pillar 3 RQ #1)")
if not print_contamination_rate(conn):
    missing_metrics.append("M2 (Contamination)")
if not m4_ok:
    missing_metrics.append("M4 (Token Cost / Pillar 3 Context Economy)")
```

**No entry for M5.** If no successful runs exist in scope — either because (a) the campaign ran but every condition produced 100% failures (catastrophic), (b) `gate_passed=FALSE` filter excluded all rows, or (c) `output_in_range=FALSE` / `output_shape_mismatch=TRUE` filters excluded all rows — `print_latency_summary` prints `"(no successful runs — nothing to summarize)"` and continues. Row 17 exits 0 without Chapter 6 M5 evidence.

### The ext13 #126 / #128 / #132 + ext14 #149 pattern

M2 / M3 / M4 were retrofitted into `-> bool` + missing-metrics exit-7. ext15 #164 closed the same gap for Classification Accuracy. M5 is the fifth Chapter 6 metric but was never retrofitted.

Retrofit history:

| Metric | Function | Retrofitted in | Status post-fix |
|---|---|---|---|
| M2 Contamination | `print_contamination_rate` | ext13 #132 | ✓ `-> bool` + missing_metrics |
| M3 Gini | `print_gini_summary` | ext13 #126 / #128 | ✓ `-> bool` + missing_metrics |
| M4 Token Cost | `print_token_cost_summary` | ext14 #149 | ✓ `-> bool` + missing_metrics |
| Classification Accuracy | `print_classification_accuracy` | ext15 #164 (proposed) | proposed `-> bool` + missing_metrics |
| **M5 Latency** | `print_latency_summary` | **MISSING** | **still `-> None`, no gate** |

### Partial-condition coverage case

Beyond the "zero successful runs" case, a more common partial case: one condition has zero successful runs while others do. The function iterates `rows` (grouped by condition) — conditions with zero rows in the SUCCESS scope silently drop from the output. M5 latency comparison (Chapter 6 M5 table's JIT-vs-JIT or JIT-vs-static comparison) becomes incomplete without warning.

Example: `no_verification` condition has 100% failure (verification bypass misroutes a critical step on every task) → zero rows in `gate_passed AND output_in_range` filter → M5 table shows three conditions, not four. Chapter 6 reader draws conclusions from a 3-way comparison that was supposed to be 4-way. Row 17 exits 0.

### Why P1 (confirmed)

Same severity framing as ext15 #164 (M2/M3/M4 retrofit was P1; Classification Accuracy retrofit was P1; M5 retrofit is P1). Default-workflow-reachable whenever a condition has zero successful runs. Fix is mechanical: signature change + one missing_metrics entry.

### Proposed fix

```python
def print_latency_summary(conn: duckdb.DuckDBPyConnection) -> bool:
    """...
    ext16 #177: signature returns ``bool``. Returns ``False`` when
    (a) no successful runs exist in scope, or (b) any required JIT
    condition has zero successful runs. Consistent with M2 / M3 / M4 /
    Classification Accuracy per ext13 #126/#128/#132 + ext14 #149 +
    ext15 #164.
    """
    rows = conn.execute(...).fetchall()
    ...

    if not rows:
        lines.append("(no successful runs — nothing to summarize)")
        for line in lines:
            print(line)
        return False  # <-- was implicit None

    for condition, n, avg, med, p95, stdev in rows:
        ...

    # ext16 #177: per-condition coverage check. Mirrors ext13 #132's
    # expected-conditions assertion on M2.
    present_conditions = {r[0] for r in rows}
    expected_conditions = {"full", "no_verification", "no_optimization", "static"}
    missing = expected_conditions - present_conditions
    if missing:
        print(
            f"\n  ⚠ M5 partial coverage: conditions with zero successful "
            f"runs: {sorted(missing)}. JIT-vs-JIT / JIT-vs-static latency "
            f"comparison incomplete.",
            file=sys.stderr,
        )
        return False

    return True
```

Main-wiring at line 1688-1718:

```python
m5_ok = print_latency_summary(conn)
...
if not m5_ok:
    missing_metrics.append("M5 (Latency / Chapter 6 time-to-correct)")
```

### Proposed test

```python
def test_m5_latency_no_successful_runs_fails_missing_metrics_gate(conn):
    """ext16 #177: M5 latency with zero successful runs must enter
    missing_metrics and fail exit 7 unless --allow-missing-metrics.
    Pre-fix the function returned None and main() ignored it → silent
    exit 0.
    """
    _populate_evaluation_runs(conn, condition="full", n=100, gate_passed=False)
    _populate_evaluation_runs(conn, condition="no_verification", n=100, gate_passed=False)
    _populate_evaluation_runs(conn, condition="no_optimization", n=100, gate_passed=False)
    _populate_evaluation_runs(conn, condition="static", n=100, gate_passed=False)

    rc = _run_main(["--run-ids", *all_run_ids])

    assert rc == 7, "ext16 #177: zero successful runs must fail exit-7 gate"
    assert "M5 (Latency / Chapter 6 time-to-correct)" in captured.err
    assert "CHAPTER 6 INCOMPLETE" in captured.err


def test_m5_latency_partial_condition_coverage_fails_gate(conn):
    """ext16 #177: when one JIT condition has zero successful runs, M5
    partial coverage must fail the gate. Pre-fix three-condition table
    printed silently and Row 17 exited 0.
    """
    # full + no_optimization + static have successful runs; no_verification
    # has 100% failure.
    _populate_evaluation_runs(conn, condition="full", n=100, gate_passed=True, output_in_range=True)
    _populate_evaluation_runs(conn, condition="no_verification", n=100, gate_passed=False)
    _populate_evaluation_runs(conn, condition="no_optimization", n=100, gate_passed=True, output_in_range=True)
    _populate_evaluation_runs(conn, condition="static", n=100, gate_passed=True, output_in_range=True)

    rc = _run_main(["--run-ids", *all_run_ids])

    assert rc == 7
    assert "no_verification" in captured.err
    assert "M5 partial coverage" in captured.err
```

### Interaction with other Row 17 retrofits

| Metric | Retrofit status pre-ext16 | Post-ext16 |
|---|---|---|
| M2 | ✓ (ext13 #132) | unchanged |
| M3 | ✓ (ext13 #126/#128) | unchanged |
| M4 | ✓ (ext14 #149) | unchanged |
| Classification Accuracy | proposed (ext15 #164) | unchanged (still proposed) |
| M5 Latency | **missing** | **proposed via #177** |

After ext15 #164 + ext16 #177 land, all five Chapter 6 metrics uniformly honor the missing-metrics exit-7 contract.

---

## Issue #178 — Row 13 session-coverage gate (ext14 #139) runs before degraded-trace filter (ext12 #120); sessions with all-degraded traces silently pass the gate then drop from aggregates (P0 CRITICAL, confirmed)

### Summary

`scripts/analyze_context_utilization.py:676-741`. Current execution order:

```python
# STEP 1 (line 679-694): ext14 #139 per-requested-session coverage gate
# runs on contributed_sessions_total — the set of session IDs that
# contributed ≥1 fetched trace. Built during the fetch loop, independent
# of whether those traces have any usable observations.
if args.session_ids:
    requested = set(args.session_ids)
    missing = requested - contributed_sessions_total
    if missing and not args.allow_partial_sessions:
        ...
        return 4

# STEP 2 (line 699-708): ext7 #47 zero-traces loud-fail
total_traces = sum(len(tlist) for tlist in results.values())
if total_traces == 0 and not args.allow_empty:
    ...

# STEP 3 (line 710-741): ext12 #120 degraded-trace filter runs AFTER
# the coverage gate. A trace is degraded iff every observation fetch
# for it failed (n_failed == n_attempted). Degraded traces are
# REMOVED from `results[agent_name]` before aggregation.
for agent_name, trace_list in results.items():
    degraded = [t for t in trace_list if t.get("_fetch_degraded")]
    valid = [t for t in trace_list if not t.get("_fetch_degraded")]
    ...
    results[agent_name] = valid
```

**Ordering gap**: a session that had traces fetched but all of those traces got marked `_fetch_degraded=True` during `extract_trace_context` (all observation fetches failed) passes Step 1 (trace IDs contributed to `contributed_sessions_total` pre-filter) and then loses all its traces in Step 3. The session is absent from `results[agent_name]` post-filter but the coverage gate already declared it "contributed." No post-filter coverage re-evaluation.

Chapter 6 Context Economy aggregates (peak token %, chain token %, utilization metrics) are computed on `results[agent_name]` (post-filter). Affected sessions contribute zero rows to the aggregate — silently undercount.

### Reachability

A session's every trace is marked degraded when every observation fetch for every one of its traces fails during `extract_trace_context`. Causes:

1. **LangFuse transient outage affecting a narrow time window.** If the outage coincides with one session's trace-extraction window (the session happens to be processed while LangFuse is returning 5xx on observation fetches), that session's traces all degrade.
2. **LangFuse rate-limit throttling.** Bulk observation fetches on a high-volume session hit the rate limit; retry-after backoff exceeds the httpx timeout; all observations fail for that session.
3. **Session-specific auth error.** Project-scoped auth token loses access to one session's project mid-analysis.

Under the default workflow, cases 1 + 2 are both realistic during Row 13's LangFuse-heavy fetch phase. The script's existing degraded-filter WARNING (line 724-729) does print per-agent counts, but there's no per-session re-evaluation of coverage.

### Why P0 CRITICAL (confirmed)

- **Default-workflow-reachable under LangFuse transient failures.** Not a contract violation; the degraded filter is designed for exactly this scenario, but the coverage gate runs before it.
- **Silent Chapter 6 undercount.** A partial-coverage Chapter 6 Context Economy headline is reported without a missing-session banner. Reader assumes full coverage.
- **No Chapter 4.5 disclosure path.** The existing `--allow-partial-sessions` flag covers the pre-filter coverage gate; affected sessions slip past it silently and never enter the disclosure track.
- **Stacks with #156**: under `condition_scope='static'` sessions (per #156, those don't produce DeepAgent traces at all), the gate already fires for legitimate reasons. Adding this silent path means operators can't distinguish "static = expected empty" from "degraded = catastrophic transient."

### Proposed fix

Two options, both close the ordering gap:

**Option A — Re-evaluate coverage AFTER degraded filter.** Add a second coverage check after line 741 on the post-filter contributed set:

```python
# ext16 #178: re-evaluate session-coverage AFTER the ext12 #120 degraded
# filter. Sessions whose traces ALL got marked degraded passed the
# pre-filter gate at line 679-694 but contribute zero rows to aggregates.
# Catch them here with the same exit-4 contract.
if args.session_ids:
    contributed_post_filter: set[str] = set()
    for agent_name, trace_list in results.items():
        for t in trace_list:
            sid = t.get("session_id") or t.get("sessionId")
            if sid:
                contributed_post_filter.add(sid)
    degraded_dropout = contributed_sessions_total - contributed_post_filter - (requested - contributed_sessions_total)
    still_missing_post_filter = requested - contributed_post_filter
    if still_missing_post_filter and not args.allow_partial_sessions:
        newly_missing = still_missing_post_filter - (requested - contributed_sessions_total)
        print(
            f"\nERROR: {len(newly_missing)}/{len(requested)} requested session "
            f"IDs contributed traces initially but had ALL traces dropped by "
            f"the ext12 #120 degraded-fetch filter: {sorted(newly_missing)}\n"
            f"  Likely cause: LangFuse transient failure / rate-limit on those "
            f"sessions during observation fetch. Retry when LangFuse is "
            f"healthy, or pass --allow-partial-sessions with Chapter 4.5 "
            f"disclosure. ext16 #178. Exit 4.",
            file=sys.stderr,
        )
        return 4
```

**Option B — Swap the two blocks.** Move the degraded-filter block (lines 710-741) before the coverage gate (lines 676-694), and let the gate see post-filter contribution natively. Minor risk: the existing total_traces==0 exit-2 gate between them needs re-ordering too.

**Recommended:** Option A. Surgical addition that preserves the distinct semantics of "coverage gate catches typo/retention" (pre-filter) vs "degraded-dropout gate catches LangFuse transient" (post-filter). Two distinct error messages help operator diagnosis.

### Proposed test

```python
def test_coverage_gate_catches_session_dropout_from_degraded_filter(monkeypatch, capsys):
    """ext16 #178: a session whose traces all get marked degraded must
    fail the post-filter coverage gate at exit 4 unless
    --allow-partial-sessions. Pre-fix the pre-filter gate passed these
    sessions silently, then the degraded filter removed them from
    aggregates.
    """
    # Seed: session 'sess_A' has 2 traces, both with every observation
    # fetch failing (all degraded). Session 'sess_B' has 1 valid trace.
    _populate_trace_list_with_degraded_flags(
        {"sess_A": [{"_fetch_degraded": True}, {"_fetch_degraded": True}],
         "sess_B": [{"_fetch_degraded": False, "input_tokens": 100}]}
    )

    rc = _run_main(["--session-ids", "sess_A", "sess_B"])

    assert rc == 4
    assert "sess_A" in captured.err
    assert "degraded-fetch filter" in captured.err
    assert "sess_B" not in captured.err.replace("sess_B']", "")  # sess_B valid
```

---

## Issue #179 — `extract_trace_context` flags `_fetch_degraded` only when ALL observation fetches fail; partial-fetch failures kept as complete, silently undercounting Chapter 6 peak/chain token metrics (P1, confirmed)

### Summary

`scripts/analyze_context_utilization.py:236-344`. The `_fetch_degraded` determination logic:

```python
# Line 289-310: empty-steps branch
if not steps:
    # ext12 #120: distinguish "every observation fetch failed" from
    # "trace had observations but none were GENERATION-typed"
    fetch_degraded = n_attempted > 0 and n_failed == n_attempted
    return {
        ...
        "_fetch_degraded": fetch_degraded,
        ...
    }

# Line 328-348: non-empty-steps branch
return {
    ...
    # ext12 #120: a trace with usable GENERATION observations is never
    # fetch-degraded (some fetches may have failed, but enough landed
    # to produce real metrics — aggregating these is correct).
    "_fetch_degraded": False,
    ...
}
```

Two failure shapes:

| Shape | `n_attempted` | `n_failed` | `_fetch_degraded` | Kept in aggregates |
|---|---|---|---|---|
| Full failure | 10 | 10 | True | No (ext12 #120 filters) |
| Partial failure (1 of 10 survives) | 10 | 9 | False | Yes, aggregated as complete |
| Partial failure (9 of 10 survive) | 10 | 1 | False | Yes, aggregated as complete |
| No failures | 10 | 0 | False | Yes, aggregated as complete |

The **partial-failure row** is the gap: the comment at line 341-343 explicitly concedes `"some fetches may have failed, but enough landed to produce real metrics — aggregating these is correct"` — but that's only correct for some aggregates, not all.

### Which aggregates are biased

- **`peak_input_tokens = max(s["input_tokens"] for s in steps)`** (line 312): if the missing observations had larger `input_tokens` than any survivor, peak is understated. Statistically: `max` over a subsample is biased low vs `max` over the full sample, with bias proportional to loss rate and the right-tail shape of the token distribution.
- **`total_chain_input_tokens = sum(steps)`** (line 322): chain sum over surviving steps only → **directly proportional to the survivor coverage ratio**. A 50%-failure trace reports half the chain cost.
- **`context_utilization_chain_pct = total_chain_input / MODEL_CONTEXT_WINDOW * 100`** (line 338-340): derived from biased chain sum → biased low.
- **`step_count = len(steps)`** (line 329): undercounts the true chain length.

Chapter 6 Context Economy headline (§Pillar 3 token tension) computed across agent types uses `peak_input_tokens` and `total_chain_input_tokens` per trace, aggregated via mean/median/p95. Partial-failure traces silently shift those aggregates down.

### Reachability

Partial observation-fetch failures arise from:

1. **LangFuse transient per-observation errors.** Rate-limit on a burst, 5xx on specific observation IDs, connection resets mid-batch.
2. **Observation-level auth scoping.** Certain observation types or metadata fields require elevated scope; token has it for some but not others.
3. **Long-running trace crossing an auth-token refresh window.** Fetches before refresh fail with 401; fetches after succeed.

Real-world rate: non-zero. The existing `--allow-empty` flag exists precisely because LangFuse transient failures happen. Partial failures are a subset of that same phenomenon.

### Why P1 (confirmed)

Same structural shape as #161 (pagination truncation silently undercounts) and #165 (unmatched-generations population ungated). The "silent undercount from partial fetch coverage" family. Reviewer-confirmed severity.

### Proposed fix

Add a `_fetch_partial` flag + coverage ratio; either exclude partials with a WARNING, or include them with a banner:

```python
# Line 328-348 (non-empty-steps branch):
# ext16 #179: track partial-fetch coverage. Pre-fix, traces with any
# observation-fetch failure were flagged _fetch_degraded=False and
# entered aggregates as if complete, silently biasing peak/chain token
# metrics low by a factor proportional to the loss rate.
fetch_coverage = 1.0 - (n_failed / n_attempted) if n_attempted > 0 else 1.0
return {
    ...
    "_fetch_degraded": False,
    "_fetch_partial": n_failed > 0,
    "_fetch_coverage_pct": round(fetch_coverage * 100, 1),
    "_obs_attempted": n_attempted,
    "_obs_failed": n_failed,
    "steps": steps,
}
```

Aggregation-layer guard:

```python
# main() after the existing ext12 #120 degraded filter:
# ext16 #179: partial-fetch coverage gate.
partial_counts: dict[str, int] = {}
for agent_name, trace_list in results.items():
    partial = [t for t in trace_list if t.get("_fetch_partial")]
    if partial:
        mean_cov = sum(t["_fetch_coverage_pct"] for t in partial) / len(partial)
        print(
            f"WARNING: {len(partial)}/{len(trace_list)} {agent_name!r} traces "
            f"had partial observation-fetch coverage (mean {mean_cov:.1f}%). "
            f"Chapter 6 Context Economy aggregates biased low proportional "
            f"to the missing observations. ext16 #179.",
            file=sys.stderr,
        )
        partial_counts[agent_name] = len(partial)

if partial_counts and not args.allow_partial_fetch:
    print(
        f"\nERROR: {sum(partial_counts.values())} trace(s) had partial "
        f"observation-fetch coverage, aggregated as if complete. Pass "
        f"--allow-partial-fetch to proceed with Chapter 4.5 disclosure. "
        f"ext16 #179. Exit 6.",
        file=sys.stderr,
    )
    return 6
```

New CLI arg: `--allow-partial-fetch` (default: refuse at exit 6).

### Proposed test

```python
def test_partial_fetch_sets_partial_flag_not_degraded(monkeypatch):
    """ext16 #179: partial observation-fetch (some succeed, some fail)
    must set _fetch_partial=True and record coverage_pct. Pre-fix the
    flag was False and the trace entered aggregates as complete.
    """
    fake_obs_ids = [f"obs_{i}" for i in range(10)]
    def fake_fetch(client, obs_id):
        # Simulate 3/10 fetches failing (partial).
        if obs_id in {"obs_3", "obs_5", "obs_7"}:
            return None
        return {"type": "GENERATION", "usage": {"input": 100, "output": 50, "total": 150}}
    monkeypatch.setattr("scripts.analyze_context_utilization.fetch_observation", fake_fetch)

    result = extract_trace_context(None, {"observation_ids": fake_obs_ids})

    assert result["_fetch_degraded"] is False  # unchanged — not all failed
    assert result["_fetch_partial"] is True     # new
    assert result["_fetch_coverage_pct"] == 70.0
    assert result["_obs_failed"] == 3
```

### Interaction with #178

#178 catches session-level drop-out from ALL-traces-degraded; #179 catches trace-level partial-fetch that doesn't reach the degraded threshold. Both surface LangFuse-transient silent undercount at different granularities. Land together for full Row 13 integrity. Operator-facing error messages distinguish the two cases.

---

## Issue #180 — Row 15 `aggregate()` silently drops events with non-list `metadata.categories`; `render_table` then prints "No tier-2 findings observed" narrative despite `events_total > 0`, silently erasing Chapter 4.3 evidence on metadata shape drift (P0 CRITICAL, confirmed)

### Summary

`scripts/analyze_tier2_probes.py:243-299` `aggregate()`:

```python
def aggregate(events: list[dict]) -> ProbeBreakdown:
    bd = ProbeBreakdown()

    for obs in events:
        bd.events_total += 1
        name = obs.get("name", "")
        is_reject = name == _TIER2_EVENT_REJECT
        if is_reject:
            bd.events_reject += 1
        elif name == _TIER2_EVENT_PASS:
            bd.events_pass += 1

        metadata = obs.get("metadata") or {}
        ...

        cap_type = str(metadata.get("capability_type") or "unknown")
        categories = metadata.get("categories") or []
        severity_dist = metadata.get("severity_distribution") or {}

        if not isinstance(categories, list):
            continue  # <-- silent skip; no counter; no warning

        # ext8 #60: severity_by_event accumulator runs AFTER the continue,
        # so malformed-categories events lose their severity contribution too.
        if isinstance(severity_dist, dict):
            for sev, count in severity_dist.items():
                bd.severity_by_event[sev] += ...
        ...
```

The `continue` at line 268 silently discards the event's per-category contribution AND its severity contribution (`severity_by_event` at line 275-279 runs after the continue). `events_total`, `events_reject`, `events_pass` counters at lines 248-254 are already incremented — so the event is in the total but contributes zero to the breakdown.

Downstream `render_table` at line 307-326:

```python
def render_table(bd: ProbeBreakdown, *, include_pass: bool) -> None:
    print("=" * 70)
    print("  TIER 2 BEHAVIORAL PROBE-TYPE BREAKDOWN")
    print("=" * 70)
    print(f"\nTotal tier-2 events:        {bd.events_total}")
    print(f"  verification.tier_2.reject  {bd.events_reject}")
    ...

    if not bd.categories:
        print(
            "No tier-2 findings observed in the time window. "
            "Expected sources:\n"
            "  - scripts/test_verification.py (smoke, small sample)\n"
            "  - scripts/run_pilot.py --all-conditions (Phase 4 eval, full)\n"
            "Check the --days window and --environment filter."
        )
        return
```

If every event has shape-drifted `categories`, the narrative reads:

```
Total tier-2 events:        50
  verification.tier_2.reject  12
  verification.tier_2.pass    38

No tier-2 findings observed in the time window. ...
```

Contradictory: 50 events, 12 rejects — but zero findings observed. Row 15 Chapter 4.3 evidence structurally erased; the operator-facing message blames the `--days` window / `--environment` filter rather than a metadata-shape drift the script could detect.

### Reachability

Currently **latent** under the runner's always-list emission: `src/verification/behavioral.py` and `src/verification/adversarial.py` both emit `categories` as a `list[str]`. But there's no structural guard against:

1. **SDK version drift.** LangFuse SDK updates occasionally reshape metadata envelope; a future version could serialize empty lists as `None`, or reshape `categories` to a dict keyed by severity.
2. **Third-party emitter.** A future Chapter 7 work (custom probe types, externally-generated probe events) could emit `categories` as a string (single category) or a dict. No contract enforcement.
3. **Schema migration.** A decision to restructure `metadata` during the thesis eval period (not planned but possible) would silently erase Chapter 4.3.
4. **Manual backfill scripts.** Any script writing directly to LangFuse observations (for backfill or correction) could accidentally write a malformed shape.

Structural integrity: the script has all the information to diagnose shape drift (it's literally reading `categories` with `isinstance(categories, list)`) but silently suppresses the signal instead of converting it to a loud error.

### Why P0 CRITICAL (confirmed)

- **Chapter 4.3 evidence silently erased** under metadata drift. The narrative "No tier-2 findings observed" directly contradicts `events_total=50, events_reject=12` in the same output — a reader reasonably concludes "there were no probe rejections to break down" rather than "the script silently dropped the breakdown."
- **No Chapter 4.5 disclosure path.** There's no `--allow-malformed-metadata` or equivalent; the operator cannot even opt out of the silent-drop behavior.
- **Asymmetric-antipattern family.** Same "count the total but drop the detail silently" shape as #165 (unmatched count printed, never gated), #162 (cell-level vs pool-level compute), #168 (prose-vs-command denominator ambiguity). Part of the "stated-vs-realized diverges, failure silenced" structural antipattern cluster tracked across ext15/ext16.
- **Latent under current runner** but the structural gap warrants P0 on correctness-independent-of-reachability grounds. Reviewer-submitted P0 matches the bar set for #154/#155/#160/#162/#170.

### Proposed fix

Track malformed-category events as a first-class ProbeBreakdown field; convert the silent skip to a loud-fail:

```python
@dataclass
class ProbeBreakdown:
    events_total: int = 0
    events_reject: int = 0
    events_pass: int = 0
    # ext16 #180: track events that failed metadata shape validation.
    events_malformed_categories: int = 0
    ...
```

```python
def aggregate(events: list[dict]) -> ProbeBreakdown:
    bd = ProbeBreakdown()

    for obs in events:
        bd.events_total += 1
        ...
        categories = metadata.get("categories") or []
        severity_dist = metadata.get("severity_distribution") or {}

        if not isinstance(categories, list):
            # ext16 #180: count malformed-categories events loudly
            # instead of silently dropping.
            bd.events_malformed_categories += 1
            continue
        ...
```

```python
def render_table(bd: ProbeBreakdown, *, include_pass: bool) -> None:
    print("=" * 70)
    print("  TIER 2 BEHAVIORAL PROBE-TYPE BREAKDOWN")
    print("=" * 70)
    print(f"\nTotal tier-2 events:        {bd.events_total}")
    print(f"  verification.tier_2.reject  {bd.events_reject}")
    if include_pass:
        print(f"  verification.tier_2.pass    {bd.events_pass}")
    # ext16 #180: surface malformed-metadata events loudly.
    if bd.events_malformed_categories:
        print(
            f"  ⚠ malformed categories       {bd.events_malformed_categories} "
            f"(events with non-list metadata.categories — dropped from "
            f"per-category breakdown; possible SDK version drift, third-"
            f"party emitter, or schema migration)"
        )
    print()

    if not bd.categories:
        if bd.events_malformed_categories > 0:
            # ext16 #180: distinguish genuine empty from shape-drift erasure.
            print(
                f"\nERROR: per-category breakdown is empty because ALL "
                f"{bd.events_malformed_categories} events had non-list "
                f"metadata.categories. Chapter 4.3 evidence erased by "
                f"metadata shape drift. Investigate the runner's probe "
                f"emission convention (src/verification/behavioral.py + "
                f"adversarial.py) or the LangFuse SDK version. "
                f"ext16 #180. Exit 7.",
                file=sys.stderr,
            )
            import sys as _sys
            _sys.exit(7)
        print(
            "No tier-2 findings observed in the time window. ..."
        )
        return
```

Loud-fail semantics at `bd.categories empty AND events_malformed_categories > 0`; narrative-only fallback when genuinely empty.

### Proposed test

```python
def test_aggregate_surfaces_malformed_categories_loudly(capsys):
    """ext16 #180: events with non-list metadata.categories must be
    counted in events_malformed_categories and surfaced in render_table.
    Pre-fix they were silently dropped and render_table printed
    'No tier-2 findings observed' narrative despite events_total > 0.
    """
    events = [
        {"name": "verification.tier_2.reject", "metadata": {
            "categories": "hardcoded",  # <-- string, not list (shape drift)
            "capability_type": "mcp_tool",
        }},
        {"name": "verification.tier_2.reject", "metadata": {
            "categories": ["fabricated"],
            "capability_type": "skill",
        }},
    ]
    bd = aggregate(events)

    assert bd.events_total == 2
    assert bd.events_reject == 2
    assert bd.events_malformed_categories == 1
    assert bd.categories == {"fabricated": 1}  # unchanged

    # render_table must NOT print "No findings" when only 1 of 2 was malformed.
    render_table(bd, include_pass=False)
    out = capsys.readouterr().out
    assert "malformed categories" in out
    assert "1" in out  # the count
    assert "No tier-2 findings observed" not in out  # bd.categories is non-empty


def test_aggregate_exit_7_when_all_events_malformed(capsys):
    """ext16 #180: when ALL events have non-list categories, render_table
    must exit 7 with a shape-drift diagnosis rather than printing the
    generic 'No findings' narrative. Chapter 4.3 evidence erasure
    mustn't look like genuine empty data.
    """
    events = [
        {"name": "verification.tier_2.reject", "metadata": {"categories": "hardcoded"}},
        {"name": "verification.tier_2.reject", "metadata": {"categories": {"hardcoded": 1}}},
    ]
    bd = aggregate(events)

    with pytest.raises(SystemExit) as exc_info:
        render_table(bd, include_pass=False)
    assert exc_info.value.code == 7

    err = capsys.readouterr().err
    assert "metadata shape drift" in err
    assert "ext16 #180" in err
```

### Interaction with other Row 15 findings

- **#154** (reject-only coverage gate collision): #180 is about metadata-shape erasure downstream of the coverage gate; #154 is about the coverage gate itself misfiring on all-pass sessions. Orthogonal — land both.
- **#155** (Row 15 session-id scope): #155 ensures the right sessions reach the aggregate; #180 ensures the aggregate doesn't silently erase their evidence.
- **#168** (prose-vs-command denominator): #168 clarifies Chapter 4.3 denominator semantics; #180 ensures the breakdown numerator survives metadata drift.

After #154 + #155 + #168 + #180 all land, Row 15's end-to-end Chapter 4.3 evidence path has:
1. Correct session scope (#155).
2. Correct coverage gate semantics (#154).
3. Correct denominator documentation (#168).
4. Loud metadata-shape-drift detection (#180).

All four close distinct Row 15 silent-corruption paths.

---

## Issue #181 — `print_classification_accuracy` scores pure registry-match rows where the classifier never ran; Pillar 1 RQ #1 proxy silently inflated (P0 CRITICAL, confirmed)

### Summary

`scripts/analyze_results.py:1131-1161`:

```python
rows = conn.execute(
    f"""
    SELECT task_id, condition, capability_type
    FROM evaluation_runs
    WHERE is_pilot = FALSE AND condition != 'static'{_SCOPE_SQL}
    """
).fetchall()
...
for task_id, cond, classified in rows:
    if task_id not in scorable:
        continue
    expected = expected_map[task_id]
    classified_norm = (classified or "unclassified").lower()
    per_condition[cond]["total"] += 1
    if classified_norm == expected:
        per_condition[cond]["hit"] += 1
    confusion[(expected, classified_norm)] += 1
```

The SQL filters on condition but **does not filter on whether the classifier actually ran** for that task. `evaluation_runs` schema (`src/registry/evaluation_store.py:42-47`) carries columns that distinguish the execution paths:

- `required_capability_count` — how many gaps the task has.
- `matched_in_registry_count` — of those, how many were satisfied by registry retrieval.
- `matched_and_executed_count` — how many registry matches successfully executed.
- `generated_artifact_count` — how many capabilities the classifier+generator produced in this run.

Rows where `matched_in_registry_count > 0 AND generated_artifact_count == 0` are **pure registry-match rows** — the pipeline found every needed capability in the registry, never invoked the classifier, never generated anything. `capability_type` on such a row reflects the registered capability's type from a prior campaign or the seeded registry, not the current classifier's judgment.

The Pillar 1 RQ #1 proxy scores these rows as if the classifier produced them. When the registered capability's type happens to match `expected_type`, the scorer counts it as a classifier hit — **inflating reported accuracy by the fraction of rows that bypass the classifier entirely**.

### Reachability

Pure registry-match rows arise on multiple default-workflow paths:

1. **Retrieval tasks T01-T03** (`tests/evaluation/tasks.py` defines them as registry-first). Their decomposer output (when decomposition even runs — see `src/orchestrator/pipeline.py` retrieval short-circuit) produces gaps that the registry satisfies directly. `matched_in_registry_count = k, generated_artifact_count = 0`. The registered tool (e.g., `parse_csv`) has type `mcp_tool`; `expected_type` for T01 is `mcp_tool`; current code scores this as a classifier hit despite the classifier never being invoked.
2. **Seeded-registry coverage on synthesis tasks.** `src/registry/store.py::seed_registry` populates the registry with hand-built capabilities. Any task whose full gap chain is already covered by seeds skips classification.
3. **Cross-session registry accumulation** (ext7 #49 mitigates for Phase-4 DB but longitudinal runs accumulate in-place per #175/#176). Later tasks in a longitudinal run benefit from earlier-run registrations; the classifier doesn't run for gaps already satisfied.
4. **`no_optimization` + `full` conditions** specifically: these write registered capabilities back for reuse across tasks within a campaign. By mid-campaign, the registry has generated capabilities from earlier tasks; later tasks may be fully covered without classification.

All four paths are **default-workflow-reachable without contract violation**. The fraction of pure registry-match rows scales with registry warm-up during a campaign — worst-case, the metric is scored overwhelmingly on registered-capability-type inheritance rather than classifier output.

### Why P0 CRITICAL (confirmed)

- **Chapter 6 Pillar 1 RQ #1 proxy inflation.** Reported classification accuracy number is silently wrong direction (higher than true) by a fraction equal to the registry-hit rate of the campaign.
- **Direction of bias is thesis-favorable.** The thesis argues the classifier routes work to the correct backend — inflation via registry hits masks classifier errors behind retrieval wins. Chapter 6 reader cannot distinguish "classifier is good" from "retrieval is good."
- **Default-workflow-reachable.** No contract violation required. Every canonical campaign produces these rows.
- **Structural analog to #166** (classification-accuracy SQL uses `!= 'static'` blacklist admitting `longitudinal` / enum-drift rows). #166 is on the condition axis; #181 is on the execution-path axis. Same antipattern family: the scoring query doesn't filter to rows the metric's semantics apply to.

### Proposed fix

Two-layer defense:

**Layer 1 — WHERE-clause filter at SQL:** only score rows where the classifier produced output.

```python
# ext16 #181: the classifier ran only when the pipeline generated at least
# one artifact (MCP tool, skill, or agent). Pure registry-match rows
# inherit capability_type from pre-existing registered capabilities and
# must not count toward Pillar 1 RQ #1 proxy accuracy — including them
# silently inflates reported accuracy by the fraction of retrieval-only
# rows in the campaign.
rows = conn.execute(
    f"""
    SELECT task_id, condition, capability_type,
           matched_in_registry_count, generated_artifact_count
    FROM evaluation_runs
    WHERE is_pilot = FALSE
      AND condition IN ('full', 'no_verification', 'no_optimization')  -- ext15 #166 whitelist
      AND generated_artifact_count > 0                                 -- ext16 #181 classifier-ran gate
      {_SCOPE_SQL}
    """
).fetchall()
```

**Layer 2 — explicit reporting footer:** surface the filtered-out count so Chapter 6 readers can interpret coverage.

```python
# ext16 #181: report how many rows were excluded from scoring because the
# classifier didn't run (pure registry-match). Chapter 4.5 MUST disclose
# this denominator caveat — the accuracy is over rows where classification
# actually happened, not over the full testbed.
registry_only_count = conn.execute(
    f"""
    SELECT COUNT(*) FROM evaluation_runs
    WHERE is_pilot = FALSE
      AND condition IN ('full', 'no_verification', 'no_optimization')
      AND generated_artifact_count = 0
      AND matched_in_registry_count > 0
      {_SCOPE_SQL}
    """
).fetchone()[0]
if registry_only_count:
    print(
        f"\n  Note: {registry_only_count} row(s) excluded from classification "
        f"accuracy denominator — pure registry-match rows where the "
        f"classifier didn't run. Chapter 4.5 disclosure: Pillar 1 RQ #1 proxy "
        f"is scored over classifier-invoked rows only."
    )
```

### Proposed test

```python
def test_classification_accuracy_excludes_pure_registry_match_rows(conn):
    """ext16 #181: rows with generated_artifact_count=0 AND
    matched_in_registry_count>0 (pure registry matches) must not count
    toward classifier accuracy. Pre-fix they were scored as classifier
    hits because capability_type reflected the registered capability's
    type, silently inflating Pillar 1 RQ #1 proxy accuracy.
    """
    # 5 rows: classifier ran correctly, all hits
    _populate_classification_rows(
        conn, task_id="T11", condition="full", capability_type="skill",
        generated_artifact_count=2, matched_in_registry_count=0, n=5,
    )
    # 5 rows: pure registry match, capability_type inherited, would pre-fix score as hits
    _populate_classification_rows(
        conn, task_id="T01", condition="full", capability_type="mcp_tool",
        generated_artifact_count=0, matched_in_registry_count=1, n=5,
    )

    captured = _capture(print_classification_accuracy, conn)

    # Post-fix: accuracy is 5/5 = 100% (only classifier rows scored),
    # not 10/10 = 100% (registry rows contribute too, masking any classifier
    # errors on retrieval tasks).
    match = re.search(r"JIT \+ Verif \+ Opt\s+(\d+)/(\d+)", captured.out)
    assert match, "couldn't find full row"
    hits, total = int(match.group(1)), int(match.group(2))
    assert total == 5, (
        f"ext16 #181: only classifier-ran rows must enter denominator. "
        f"Got total={total}, expected 5 (pure-registry rows excluded)"
    )

    # Disclosure footer must be present.
    assert "pure registry-match" in captured.out
    assert "Chapter 4.5 disclosure" in captured.out


def test_classification_accuracy_detects_classifier_error_on_retrieval_task(conn):
    """ext16 #181 motivation: pre-fix, a classifier that would mis-classify
    T01 (retrieval → mcp_tool) as skill was masked by registry hits. Post-
    fix, classifier-ran rows score on classifier output; retrieval paths
    don't contribute.
    """
    # Synthetic: 5 T01 rows where the classifier ran (generated_artifact_count=1)
    # and mis-classified as 'skill' (expected 'mcp_tool').
    _populate_classification_rows(
        conn, task_id="T01", condition="full", capability_type="skill",
        generated_artifact_count=1, matched_in_registry_count=0, n=5,
    )
    # 5 T01 pure-registry rows where capability_type matches expected (mcp_tool).
    _populate_classification_rows(
        conn, task_id="T01", condition="full", capability_type="mcp_tool",
        generated_artifact_count=0, matched_in_registry_count=1, n=5,
    )

    captured = _capture(print_classification_accuracy, conn)

    # Pre-fix: 10 total, 5 hits (registry-only rows), 50% accuracy with
    # classifier error silently masked. Post-fix: 5 total, 0 hits, 0%
    # accuracy — classifier error visible.
    match = re.search(r"JIT \+ Verif \+ Opt\s+(\d+)/(\d+)\s+([\d.]+)%", captured.out)
    assert match
    hits, total, acc = int(match.group(1)), int(match.group(2)), float(match.group(3))
    assert total == 5 and hits == 0 and acc == 0.0, (
        "ext16 #181: post-fix must expose classifier error without registry masking"
    )
```

### Interaction with other findings

- **#166** (classification SQL uses `!= 'static'` blacklist): #166 extra-conditions + #181 registry-hits both distort the denominator, from different angles. Both fix the SQL in the same commit — WHERE `condition IN ('full', 'no_verification', 'no_optimization') AND generated_artifact_count > 0`.
- **#164** (classification-accuracy silent-skip retrofit): orthogonal to #181. #164 makes the section exit 7 when skipped; #181 makes the scored denominator actually reflect classifier runs. Both land for end-to-end correctness.
- **Chapter 4.5 methodology caveat**: post-fix the metric is explicitly "accuracy over rows where the classifier was invoked," not "accuracy over the full testbed." This is the honest framing — the footer surfaces the registry-hit rate so readers can judge coverage. Matches the proposal §Pillar 1's actual claim scope.

After #164 + #166 + #181 all land, Pillar 1 RQ #1 proxy is correctly scoped on condition, gated on execution-path, and loud on skip. Three orthogonal integrity layers.

---

## Issue #182 — Row 14 T3 compression-loss taxonomy bucket requires `output_shape_missing_keys` truthy; file-only contract violations fall through to generic buckets (P1, confirmed)

### Summary

`scripts/analyze_compression_loss.py:89-121`. The `FailureRow` dataclass (line 89-99):

```python
@dataclass(frozen=True)
class FailureRow:
    condition: str
    capability_type: str | None
    failed_step_reason: str | None
    output_shape_mismatch: bool
    output_shape_missing_keys: list[str]
    output_in_range: bool
    gate_passed: bool
    chain_success: bool
    failure_reason: str
```

**Omits `output_shape_missing_files`** which is present in the `evaluation_runs` schema at `src/registry/evaluation_store.py:54` alongside `output_shape_missing_keys` (landed via ext8 schema extension for file-contract validation).

The classifier at line 115-121:

```python
# 1. Subtask-level schema violation (the T3 signal)
if row.failed_step_reason and _SCHEMA_VIOLATION_MARKER in row.failed_step_reason:
    return "compression_loss_subtask"

# 2. Task-boundary contract violation (Step 14.6)
if row.output_shape_mismatch and row.output_shape_missing_keys:
    return "compression_loss_task"
```

Bucket 2 requires `output_shape_missing_keys` **truthy** (non-empty list). A task contract with `required_files=["expected.csv"]` and `required_keys=[]` where the pipeline produces the declared keys but drops the file-write step would have:

- `output_shape_mismatch = True`
- `output_shape_missing_keys = []` (empty — no key violations)
- `output_shape_missing_files = ["expected.csv"]` (file violation)

The condition `output_shape_missing_keys` evaluates falsy on empty list → bucket 2 skipped → row falls through to:

- Line 123-127: agent timeout / error buckets (conditions on `failure_reason`, don't match)
- Line 129+ (not shown): class 5 output_rejected, class 8 chain_step_failed, etc.

The row lands in a generic bucket rather than the T3 aggressive-compression `compression_loss_task` category.

### Impact

Chapter 6 Row 14's 9-bucket taxonomy is proposal §Pillar 2 evidence for "how do tasks fail under aggressive compression?" The `compression_loss_task` bucket is specifically about task-boundary contract violations — the LLM produced structurally-valid output but violated the declared contract. File-only violations are a subset of exactly this pattern: LLM produced JSON output, dropped the declared file write.

Reachability: tasks with `required_files` non-empty, per `tests/evaluation/tasks.py` (T19, T20, T07, T04, T05, several others). All of these can produce file-only contract violations when the pipeline's classifier routes the final step away from the file-writer MCP_TOOL or when the AGENT's final output drops the file step.

Fraction affected: proportional to `required_files`-carrying tasks that fail at the task-boundary without key violations. On the canonical testbed, estimable as 20-30% of task-boundary failures.

### Why P1 (confirmed)

Reviewer-confirmed severity. Default-workflow-reachable but impact is taxonomic undercount rather than headline-metric corruption. Chapter 6 Row 14 still produces a failure breakdown; the breakdown under-attributes to `compression_loss_task` and over-attributes to generic buckets.

### Proposed fix

Two-part: add the field to `FailureRow` + extend the classifier condition.

```python
@dataclass(frozen=True)
class FailureRow:
    condition: str
    capability_type: str | None
    failed_step_reason: str | None
    output_shape_mismatch: bool
    output_shape_missing_keys: list[str]
    output_shape_missing_files: list[str]  # ext16 #182: file-only contract violations
    output_in_range: bool
    gate_passed: bool
    chain_success: bool
    failure_reason: str
```

```python
def classify_failure(row: FailureRow) -> str:
    # 1. Subtask-level schema violation (unchanged)
    if row.failed_step_reason and _SCHEMA_VIOLATION_MARKER in row.failed_step_reason:
        return "compression_loss_subtask"

    # 2. Task-boundary contract violation — keys OR files.
    # ext16 #182: pre-fix required keys truthy; file-only violations fell
    # through to generic buckets, undercounting T3 aggressive-compression
    # evidence proportional to required_files-carrying tasks in testbed.
    if row.output_shape_mismatch and (
        row.output_shape_missing_keys or row.output_shape_missing_files
    ):
        return "compression_loss_task"
    ...
```

Row loader at the DB-read layer also needs `output_shape_missing_files` populated from the DuckDB JSON column:

```python
def _row_to_failure_row(db_row) -> FailureRow:
    ...
    return FailureRow(
        ...,
        output_shape_missing_keys=json.loads(db_row["output_shape_missing_keys"]),
        output_shape_missing_files=json.loads(db_row["output_shape_missing_files"]),  # ext16 #182
        ...
    )
```

### Proposed test

```python
def test_classify_failure_file_only_contract_violation(conn):
    """ext16 #182: a task-boundary failure with empty missing_keys list
    but non-empty missing_files list must classify as compression_loss_task.
    Pre-fix the bucket required keys truthy; file-only violations fell
    through to generic buckets.
    """
    row = FailureRow(
        condition="full",
        capability_type="agent",
        failed_step_reason=None,
        output_shape_mismatch=True,
        output_shape_missing_keys=[],  # no key violations
        output_shape_missing_files=["expected.csv"],  # file-only violation
        output_in_range=False,
        gate_passed=True,
        chain_success=True,
        failure_reason="output_rejected",
    )
    assert classify_failure(row) == "compression_loss_task", (
        "ext16 #182: file-only contract violation must classify as "
        "compression_loss_task, not fall through to class 5 output_rejected"
    )
```

### Interaction with other findings

- **Proposal alignment**: Section 3.2's "required_files as first-class contract" framing. The `compression_loss_task` bucket must reflect both axes of the contract.
- **Stacks with #159** (M2 pooled extras) and #166 (classification blacklist) on the "analyzer ignores a schema column" structural family — different column (`output_shape_missing_files`), different analyzer (Row 14 vs Row 17), same pattern.

---

## Issue #183 — `MODEL_CONTEXT_WINDOW = 128_000` hardcoded; Row 13 utilization percentages silently wrong under `JIT_LLM_MODEL` override or model migration (P1, confirmed)

### Summary

`scripts/analyze_context_utilization.py:50-51`:

```python
# GPT-5-mini context window (tokens)
MODEL_CONTEXT_WINDOW = 128_000
```

Used at lines 337-340:

```python
"context_utilization_pct": round(peak_input / MODEL_CONTEXT_WINDOW * 100, 1),
"context_utilization_chain_pct": round(
    total_chain_input / MODEL_CONTEXT_WINDOW * 100, 1
),
```

Per-step `model` field captured at line 277: `"model": obs.get("model", "")`. **Never used** for per-step window lookup.

The pipeline supports model override via `JIT_LLM_MODEL` env var (documented in `src/config.py`: *"Override via JIT_LLM_MODEL env var"*). Different models have different context windows:

| Model | Context window |
|---|---|
| gpt-5-mini (default) | 128k (correct) |
| gpt-5 | 400k or 200k (version-dependent) |
| claude-sonnet-4-6 | 200k (1M with cache) |
| claude-opus-4-7 | 200k (1M with cache) |
| claude-haiku-4-5 | 200k |

Under `JIT_LLM_MODEL=openai:gpt-5` or `JIT_LLM_MODEL=anthropic:claude-sonnet-4-6`, Row 13 percentages are divided by the wrong denominator. A trace with 150k peak input tokens on Claude Sonnet 4.6 reports `150000 / 128000 * 100 = 117.2%` — nonsensical output (>100% of the window) — or under-the-ceiling traces under-report utilization proportionally.

### Why P1 (confirmed)

Currently latent under canonical thesis eval (uniform `openai:gpt-5-mini`). Becomes active under:

1. **Env-var override during eval**: operator sets `JIT_LLM_MODEL` for a campaign variant. Row 13 silently reports wrong percentages.
2. **Future model migration**: thesis revision adopts a larger model; analyzer constant needs manual update.
3. **Mixed-model pipeline**: if different stages use different models (e.g., classifier on gpt-5-mini, AGENT on Claude), per-trace percentages should use the step's actual model.

Same label-vs-compute drift family as #163 (hardcoded range labels). Reviewer-submitted severity P1 matches.

### Proposed fix

Replace the single constant with a per-model lookup table + per-step mapping:

```python
# ext16 #183: per-model context-window table. Each step's utilization
# is computed against its actual model's window, not a hardcoded 128k.
# Unknown-model entries fall back to 128k with a stderr WARNING on
# first encounter so the operator knows the percentage semantics are
# approximate.
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # OpenAI
    "gpt-5-mini": 128_000,
    "gpt-5": 200_000,  # update per provider docs
    "gpt-4.1": 128_000,
    "gpt-4.1-mini": 128_000,
    # Anthropic (Claude 4.x)
    "claude-opus-4-7": 200_000,
    "claude-opus-4-7-1m": 1_000_000,
    "claude-sonnet-4-6": 200_000,
    "claude-sonnet-4-6-1m": 1_000_000,
    "claude-haiku-4-5": 200_000,
}
_DEFAULT_CONTEXT_WINDOW = 128_000

_warned_models: set[str] = set()  # stderr warning dedup
def _context_window_for(model: str) -> int:
    # LangFuse may return "openai:gpt-5-mini" with provider prefix; strip.
    bare = model.split(":", 1)[-1] if ":" in model else model
    # Strip version suffix (e.g., "gpt-5-mini-2025-01-01").
    for key in MODEL_CONTEXT_WINDOWS:
        if bare.startswith(key):
            return MODEL_CONTEXT_WINDOWS[key]
    if bare and bare not in _warned_models:
        print(
            f"WARNING: ext16 #183: unknown model {model!r} — falling back to "
            f"{_DEFAULT_CONTEXT_WINDOW:,}-token window for utilization "
            f"percentages. Update MODEL_CONTEXT_WINDOWS in analyze_context_"
            f"utilization.py if this is a new model.",
            file=sys.stderr,
        )
        _warned_models.add(bare)
    return _DEFAULT_CONTEXT_WINDOW
```

Per-step utilization:

```python
steps.append({
    ...
    "model": obs.get("model", ""),
    "context_window": _context_window_for(obs.get("model", "")),  # ext16 #183
    ...
})

# In aggregation:
peak_input = max(s["input_tokens"] for s in steps)
peak_window = max(s["context_window"] for s in steps) if steps else _DEFAULT_CONTEXT_WINDOW
peak_utilization_pct = round(peak_input / peak_window * 100, 1)

# Chain utilization stays against the max window encountered (not ideal
# for mixed-model traces but preserves single-denominator semantics).
```

For mixed-model traces, an additional disclosure in the per-trace output: list distinct models encountered + their windows.

### Proposed test

```python
def test_context_window_lookup_per_model(monkeypatch, capsys):
    """ext16 #183: utilization percentages must be computed against each
    step's actual model window, not a hardcoded 128k.
    """
    from scripts.analyze_context_utilization import _context_window_for

    assert _context_window_for("gpt-5-mini") == 128_000
    assert _context_window_for("openai:gpt-5-mini") == 128_000
    assert _context_window_for("claude-sonnet-4-6") == 200_000
    assert _context_window_for("claude-opus-4-7-1m") == 1_000_000

    # Unknown model → default + WARNING.
    capsys.readouterr()
    assert _context_window_for("brand-new-model") == 128_000
    err = capsys.readouterr().err
    assert "brand-new-model" in err
    assert "ext16 #183" in err
    # Warning is deduplicated on second call.
    _context_window_for("brand-new-model")
    err2 = capsys.readouterr().err
    assert err2 == ""
```

### Interaction with other findings

- **#163** (hardcoded range labels): same family — constant defined far from usage, drifts silently under upstream changes. #163 was ALL_TASKS drift; #183 is model drift.
- **Chapter 4.5 disclosure**: post-fix, percentages are accurately labeled per-model; methodology section should disclose the window table used.

---

## Issue #184 — `extract_trace_context` empty-steps branch treats `n_attempted == 0` (empty `observation_ids`) as legitimate zero-util; trace enters aggregates as valid data, silent downward bias (P1, confirmed)

### Summary

`scripts/analyze_context_utilization.py:289-310`:

```python
if not steps:
    # ext12 #120: distinguish "every observation fetch failed" from
    # "trace had observations but none were GENERATION-typed" —
    # without the flag, downstream aggregation pools both cases as
    # zero-util and deflates Chapter 6's Context Economy headline.
    fetch_degraded = n_attempted > 0 and n_failed == n_attempted
    return {
        "step_count": 0,
        "peak_input_tokens": 0,
        "peak_output_tokens": 0,
        ...
        "context_utilization_pct": 0.0,
        "context_utilization_chain_pct": 0.0,
        "_fetch_degraded": fetch_degraded,
        "_obs_attempted": n_attempted,
        "_obs_failed": n_failed,
        "steps": [],
    }
```

The `fetch_degraded` condition `n_attempted > 0 and n_failed == n_attempted` has **three empty-steps cases**:

| Case | `n_attempted` | `n_failed` | `_fetch_degraded` | Disposition |
|---|---|---|---|---|
| A: All fetches failed | >0 | == n_attempted | True | Filtered out by ext12 #120 ✓ |
| B: Fetches succeeded, none were GENERATION | >0 | < n_attempted | False | Kept as legit zero (comment says "correct") |
| C: **No observations to fetch** | **== 0** | **== 0** | **False** | **Kept as legit zero silently** |

Case C is the reviewer's finding: when a trace has an empty `observation_ids` list (the fetch loop at line 236 never executes), `n_attempted` stays at 0. The degraded condition short-circuits on the `> 0` guard. Trace returns with `_fetch_degraded=False`, `step_count=0`, all zeros — enters aggregates as a legitimate zero-util data point.

### Reachability

Empty `observation_ids` arises when:

1. **LangFuse partial ingest.** The trace was created but observations weren't flushed yet; fetch at Row 13 time returns the trace envelope with no observations. Common when Row 13 runs soon after Row 8 before LangFuse-server-side ingest catches up.
2. **Observation-write failures at runner side.** The harness creates a trace but observation emission fails (rare but possible on LangFuse rate-limit-driven drop).
3. **Trace-envelope corruption.** A trace row with valid metadata but no attached observations (bug in upstream persistence).
4. **Deliberate zero-observation traces.** The harness creates trace envelopes for sessions that don't actually invoke LLM (e.g., static-condition runs pass through `create_trace` in some code paths) — these are semantically zero-util.

Cases 1-3 are degraded (trace ingest incomplete → trace shouldn't contribute real-zero data). Case 4 is legitimate zero-util. The current `fetch_degraded` condition treats all four the same (all False), which is wrong for cases 1-3.

### Distinguishing ingest-incomplete from legitimate-zero

Hard to do with current signals. Options:

- **Treat all `n_attempted == 0` as degraded** (reviewer's implicit recommendation). Risk: case 4 legitimate-zero traces get filtered out, biasing aggregates up (if those traces had low peak tokens, removing them raises the mean).
- **Cross-reference with `n_total_observations` from the trace envelope.** If LangFuse's trace metadata carries a total-observation count that's `> 0` but the fetched `observation_ids` is empty, that's strong ingest-incomplete signal. Feasibility depends on LangFuse REST API shape.
- **Cross-reference with DuckDB trace-emission counter.** The harness emits tracks per-step events via `langfuse_handler` — the DB could track whether the runner expected to emit observations vs whether it did.

Simplest defensive fix: **flag `n_attempted == 0` as degraded by default**, add `--allow-zero-observation-traces` override for operators who want to include case 4 legitimate-zero traces.

### Why P1 (confirmed)

Silent downward bias on Chapter 6 Context Economy — traces that should be degraded (ingest-incomplete) enter aggregates as zero-util, dragging percentiles and means down. Reviewer-submitted severity P1 matches the #179 family (partial-fetch-as-complete) on a different empty-steps path.

### Proposed fix

```python
if not steps:
    # ext16 #184: flag traces with no observations attempted (empty
    # observation_ids list) as fetch_degraded. Pre-fix only the "all
    # fetches failed" case was flagged; traces with ingest-incomplete
    # envelopes silently returned zero-util data and entered aggregates
    # as legitimate zeros, biasing Chapter 6 Context Economy percentiles
    # down. Case C (n_attempted == 0) is now degraded alongside case A
    # (all fetches failed). Case B (fetches succeeded but no GENERATION)
    # remains legitimate-zero per ext12 #120.
    fetch_degraded = (
        (n_attempted == 0)  # ext16 #184: ingest-incomplete envelope
        or (n_attempted > 0 and n_failed == n_attempted)  # ext12 #120: all fetches failed
    )
    return {
        ...
        "_fetch_degraded": fetch_degraded,
        "_obs_attempted": n_attempted,
        "_obs_failed": n_failed,
        "_empty_observation_ids": n_attempted == 0,  # ext16 #184: diagnostic
        "steps": [],
    }
```

The downstream degraded filter at `main()` line 710-741 already handles `_fetch_degraded=True` — the fix is purely in `extract_trace_context`. No new CLI arg needed (override via existing `--allow-empty`). Per-agent WARNING at line 724-729 now surfaces the count of `_empty_observation_ids=True` traces separately for operator diagnosis.

### Proposed test

```python
def test_empty_observation_ids_flagged_as_degraded(monkeypatch):
    """ext16 #184: a trace with empty observation_ids list must return
    _fetch_degraded=True. Pre-fix only n_attempted>0 AND n_failed==n_attempted
    was flagged; n_attempted==0 silently returned zero-util data.
    """
    trace = {"id": "t1", "observation_ids": []}

    result = extract_trace_context(None, trace)

    assert result["_fetch_degraded"] is True, (
        "ext16 #184: empty observation_ids → ingest-incomplete → must flag "
        "as degraded so downstream filter drops from aggregates"
    )
    assert result["_empty_observation_ids"] is True
    assert result["_obs_attempted"] == 0
    assert result["step_count"] == 0


def test_non_generation_observations_still_treated_as_legit_zero(monkeypatch):
    """ext16 #184: case B (observations exist, none are GENERATION) must
    remain legitimate zero-util per ext12 #120 comment — the fix narrows
    to case C (empty observation_ids), not case B.
    """
    fake_obs = [
        {"type": "SPAN", "id": "o1"},   # not GENERATION
        {"type": "EVENT", "id": "o2"},  # not GENERATION
    ]
    def fake_fetch(client, obs_id):
        return next((o for o in fake_obs if o["id"] == obs_id), None)
    monkeypatch.setattr("scripts.analyze_context_utilization.fetch_observation", fake_fetch)

    trace = {"id": "t1", "observation_ids": ["o1", "o2"]}
    result = extract_trace_context(None, trace)

    # Case B: n_attempted=2, n_failed=0, no GENERATION → legit zero.
    assert result["_fetch_degraded"] is False
    assert result["_obs_attempted"] == 2
    assert result["_obs_failed"] == 0
```

### Interaction with #179

#179 + #184 together close all three empty-steps cases:

| Case | Pre-fix | Post-#179 + #184 |
|---|---|---|
| A: All fetches failed | degraded ✓ | degraded (unchanged) |
| B: No GENERATION observations | legit zero | legit zero (unchanged) |
| C: Empty observation_ids | legit zero ✗ | **degraded via #184** |
| D: Partial failure with survivors | complete ✗ | **partial via #179** |

After both fixes, only case B is legitimate zero-util. Chapter 6 Context Economy aggregates reflect true data only.

---

## Parent-document integration (pending)

When ext16 lands, fold forward into `security_audit_2026_04_19.md`:

1. Update header: "One hundred seventy-three findings → One hundred eighty-seven findings; #174-#187 open as of 2026-04-22 (plus ext15 #151-#173 still open)."
2. Insert TL;DR rows #174-#187 after ext15's #173.
3. Tranche 3 (eval-blocking) gains **five P0 CRITICAL confirmed** (#174, #178, #180, #181, #185) + **nine P1 confirmed** (#175, #176, #177, #179, #182, #183, #184, #186, #187). Coverage map: (A) #174-#176 longitudinal runner campaign-isolation + selector stale-fallback trio; (B) #177 M5 latency silent-skip; (C) #178-#180 Row 13 + Row 15 analyzer silent-drop paths; (D) #181 Pillar 1 RQ #1 proxy admits registry-match rows; (E) #182-#184 Row 13 + Row 14 analyzer sub-shape gaps; (F) #185-#187 harness write-path integrity — enum-vs-writer mismatch (crash risk), silent-telemetry-drop (M3 under-write), snapshot-vs-count parity (M2 partial evidence).
4. Extend "Behavioral regressions expected":
   - (#174) `docs/run_sheet.md` LONGITUDINAL_RUN_ID + SHIFT_RUN_ID selectors switch to a CTE-based "pick latest unconditionally then validate completeness" pattern: `WITH latest AS (SELECT run_id, COUNT(*), MAX(timestamp) ... ORDER BY ts DESC LIMIT 1) SELECT run_id FROM latest WHERE n_rows = :expected_rows`. Error path distinguishes "latest run is partial (stale-fallback would have occurred)" from "no runs in DB." Combines with #173's strict-equality fix. New run-sheet lint in `tests/unit/test_run_sheet.py` enforces the pattern.
   - (#175) `scripts/run_longitudinal.py` adds `--fresh-db` + `--append` CLI args + timestamped-default-path + existing-non-empty-DB guard matching `run_pilot.py`'s ext7 #49 contract. Fixed path available via `--db legacy` for back-compat.
   - (#176) `scripts/run_longitudinal_shift.py` gains the same ext7 #49-style contract as #175. Symmetric fix.
   - (#177) `scripts/analyze_results.py::print_latency_summary` signature becomes `-> bool`. Empty-rows branch returns `False`; per-condition coverage check returns `False` on missing JIT conditions. main() threads result into `missing_metrics` via `if not m5_ok: missing_metrics.append("M5 (Latency / Chapter 6 time-to-correct)")`. Fifth retrofit; closes the Row 17 missing-metrics sweep across all five Chapter 6 metrics.
   - (#178) `scripts/analyze_context_utilization.py::main` adds a POST-filter session-coverage gate after the ext12 #120 degraded filter (line 710-741) computing `contributed_sessions_post_filter` from the surviving `results[agent_name]` traces; refuses with a new exit-4 code `"{N} session(s) contributed traces initially but had ALL traces dropped by the ext12 #120 degraded-fetch filter"` unless `--allow-partial-sessions`. Complements the pre-filter gate at line 679-694; two distinct diagnostic messages (pre = typo/retention; post = LangFuse transient).
   - (#179) `scripts/analyze_context_utilization.py::extract_trace_context` returns `{_fetch_partial: bool, _fetch_coverage_pct: float, _obs_attempted, _obs_failed}` on the non-empty-steps branch (line 328-348). main() adds a partial-fetch-coverage gate after the ext12 #120 degraded filter; refuses with exit 6 unless new `--allow-partial-fetch` flag. Chapter 4.5 disclosure REQUIRED on override.
   - (#180) `scripts/analyze_tier2_probes.py::ProbeBreakdown` gains `events_malformed_categories: int` field. `aggregate()` increments it at the `not isinstance(categories, list)` branch instead of silently `continue`. `render_table()` surfaces the count in the header block and loud-fails with exit 7 + `"Chapter 4.3 evidence erased by metadata shape drift"` error when `bd.categories` is empty AND `events_malformed_categories > 0`. Distinguishes genuine-empty from shape-drift erasure.
   - (#181) `scripts/analyze_results.py::print_classification_accuracy` SQL gains `AND generated_artifact_count > 0` filter (only score rows where the classifier actually produced output). Sibling filter whitelists the three JIT conditions per #166. Reporter footer surfaces `registry_only_count` excluded from denominator so Chapter 4.5 readers can interpret the coverage caveat.
   - (#182) `scripts/analyze_compression_loss.py::FailureRow` dataclass gains `output_shape_missing_files: list[str]` field; `_row_to_failure_row` populates it from the DuckDB column; `classify_failure` at line 120 extends to `if row.output_shape_mismatch and (row.output_shape_missing_keys or row.output_shape_missing_files):`. File-only contract violations correctly bucket into `compression_loss_task`.
   - (#183) `scripts/analyze_context_utilization.py` replaces `MODEL_CONTEXT_WINDOW = 128_000` with `MODEL_CONTEXT_WINDOWS: dict[str, int]` table + `_context_window_for(model: str) -> int` helper with stderr WARNING on unknown-model fallback (deduplicated). Per-step `context_window` captured at line 274-284; per-trace utilization uses `max(s["context_window"] for s in steps)`. Mixed-model traces surface distinct models encountered.
   - (#184) `scripts/analyze_context_utilization.py::extract_trace_context` empty-steps branch at line 289-310 updates `fetch_degraded = (n_attempted == 0) or (n_attempted > 0 and n_failed == n_attempted)`. Empty `observation_ids` now flags as degraded (was silent zero-util). New diagnostic field `_empty_observation_ids: bool`. Existing ext12 #120 filter at line 710-741 automatically drops the newly-flagged traces.
   - (#185) `src/schemas/evaluation.py::FailureReason` enum gains `OUTPUT_SHAPE_MISMATCH = "output_shape_mismatch"` member. Three writer sites at `tests/evaluation/harness.py:647, 1054, 1369` now construct valid `EvaluationRecord` on shape-mismatch paths instead of raising `ValidationError`. Regression test instantiates `EvaluationRecord(failure_reason="output_shape_mismatch", ...)` to assert the crash is gone.
   - (#186) `src/registry/evaluation_store.py::record_invocation_events` returns the inserted row count (was already returning `0` on empty input; now signals `inserted != expected` as a flushable signal). `tests/evaluation/cycle_flush.py::flush_task_cycle_state` compares `inserted_count` against the MetricsStore's per-task expected count (new counter `MetricsStore.invocations_recorded_since(cutoff)`), sets `invocations_ok = inserted == expected`. `src/orchestrator/composition.py:483` + `tests/evaluation/harness.py:663` upgrade `logger.debug` to `logger.warning` + increment a per-task drop counter flushed alongside cycle state.
   - (#187) `tests/evaluation/harness.py` adds parity assertion after every `_snapshot_artifacts(artifacts)` call: `assert len(snapshots) == sum(1 for ref in resolution.values() if ref.source == "generated")` or loud WARNING + persisted `snapshot_parity_mismatch: bool` field on `EvaluationRecord`. Row 16 `audit_contamination.py` reads the field; excludes mismatched-parity rows from the audit pool with a stderr notice.
5. End-of-audit note: "Extended 2026-04-22 (sixteenth extension) with findings #174-#184 (detail sections in `security_audit_2026_04_19_ext16.md`). Eleven findings, five bundles: (A) #174-#176 longitudinal runner campaign-isolation + selector stale-fallback trio — one P0 CRITICAL + two P1 confirmed. Bundle A root cause: longitudinal runners predated `run_pilot.py`'s ext7 #49 campaign-isolation contract and never received the retrofit; silent DB accumulation enables #174's selector stale-fallback on partial newest runs. Default-workflow-reachable for any re-invocation of Rows 9/10 (exploration, crash recovery, parameter tuning). (B) #177 M5 latency silent-skip — fifth retrofit in the ext13/ext14 missing-metrics family. After #177 + ext15 #164 land, all five Chapter 6 metrics (M2/M3/M4/Classification Accuracy/M5) uniformly honor the missing-metrics exit-7 contract. (C) #178-#180 Row 13 + Row 15 analyzer silent-drop paths — two P0 CRITICAL + one P1 confirmed. Bundle C root cause: LangFuse-analyzer completeness gates designed at different times (ext12 #120 degraded filter, ext14 #139 coverage gate, ext8 #60 severity aggregation) were not re-audited together for ordering and shape-drift interactions. #178 exposes an ordering gap between two gates that each work correctly in isolation; #179 exposes a partial-failure threshold gap in the degraded-flag logic; #180 exposes a silent-erasure path in metadata-shape validation. (D) #181 Pillar 1 RQ #1 proxy registry-match inflation — one P0 CRITICAL confirmed. Extension of #166's label-vs-compute-scope family to the execution-path axis: the classification-accuracy scorer was gated on condition but not on whether the classifier actually ran. Default-workflow-reachable via retrieval tasks + registry warm-up. (E) #182-#184 Row 13 + Row 14 analyzer sub-shape gaps — three P1 confirmed. Bundle E root cause: analyzer implementations left structural fields un-handled (#182: `output_shape_missing_files` schema column) or used constants where lookups were needed (#183: hardcoded model window, #184: partial empty-steps case coverage). **Recurring cross-extension pattern:** ext16's bundle A extends the 'campaign-isolation contract gap' surface that ext7 #49 closed for `run_pilot.py`; ext16's bundles C + D + E extend the ext15 bundle-E/F/G/H/I 'stated-vs-realized diverges, failure silenced' antipattern family with **seven new instances** (#178 pre-filter-vs-post-filter ordering gap, #179 partial-failure-as-complete threshold gap, #180 malformed-metadata-as-zero shape-drift gap, #181 classifier-bypass-scored-as-classifier-hit, #182 file-contract-violation-as-generic-failure, #183 model-window-constant-drift, #184 empty-observation-ids-as-legit-zero). The antipattern now has **nineteen confirmed instances** across the analyzer family: ext15 #152/#157/#159/#160/#162/#163/#164/#165/#166/#167/#168/#169/#170/#171/#172/#173 + ext16 #178/#179/#180/#181/#182/#183/#184. **Ext17 should continue the sweep on remaining analyzers** (Row 12 `backfill_definition_token_costs.py`, further `analyze_longitudinal.py` compute surfaces, Row 14 additional taxonomy buckets) — the antipattern likely recurs in per-capability utility-score gates, per-cycle convergence gates, and additional schema-column-omission failures where new fields landed post-analyzer."

---

**End of extension.**
