# Evaluation Protocol (the one that actually ships)

**Date:** 2026-04-22
**Supersedes:** `docs/archive/run_sheet.md` (Rows 1–19, CAMPAIGN_RUN_IDS framework, all per-row coverage gates). The old run sheet is retained only as historical context.

**Why this document exists.** Three days of patching produced ~60 P0/P1 findings in the *analyzer and backfill scripts*, not in the thesis contribution. The analyzers grew past the size of the three pillars they measure. This protocol replaces the Row 1–19 framework with a shorter pipeline that measures only what Chapter 6 actually needs, with independent cross-checks as the correctness mechanism instead of more gates.

**Two rules that make everything else work:**
1. **Fresh DB per campaign.** No `--append`. No CAMPAIGN_RUN_IDS selectors. One DB = one campaign. If you re-run, delete the DB first.
2. **Two-source agreement on every headline number.** Every metric in Chapter 6 is produced two ways (analyzer + hand-written SQL or scipy). If they disagree, the analyzer is wrong; fix it or drop the metric.

If you feel the urge to add a completeness gate inside an analyzer, stop. Add the cross-check outside instead.

---

## 1. Scope cuts (locked-in for the Chapter 6 evidence set)

**Proposal metrics in Chapter 6 primary scope:**
- **M1** — End-to-end task success rate. Headline number.
- **M3** — Capability reuse distribution (Gini coefficient on `invocation_events`).
- **M5** — Time to first correct execution (latency, already narrowed in Step 18.9).
- **M6** — Registry convergence (utility-score KL divergence, Step 18.7).
- **Core-Work Classification Accuracy** — Pillar 1 RQ #1 operational proxy. Task-level primary-type match between `TaskDefinition.expected_type` and the recorded `capability_type`, cross-checked by the Stage 2 script against the task definitions. It stays in scope, but the stage script's JSON artifacts are the authoritative implementation.

**Proposal metrics deferred to Chapter 7 future work, with a one-paragraph justification each:**
- **M2 — Mock contamination rate.** Proposal calls for manual audit of a sample. Automated lower bound = Tier 3 rejection rate from the three-tier gate; report that in Chapter 6 as a proxy, defer the human audit.
- **M4 — Token cost per task.** Proxied by per-task wall-clock latency (M5) + per-capability definition-string cost (Step 18.6, static, already shipped). Full M4 would require LangFuse reconciliation plumbing that has produced ~20 of the recent P0s; the proxy is sufficient for Chapter 6.
- **T3 compression-loss analyzer.** The three-granularity Structured State Communication *implementation* (§12 findings #10/#11/#12) is the contribution. Quantifying the failure-rate reduction is a reviewer-nice-to-have, not a pillar RQ.

**Evaluation runs in Chapter 6 primary scope:**
- Phase 4 Step 18 — 240-record core eval (20 tasks × 4 conditions × 3 runs).
- Phase 4 Step 19 — Longitudinal run (optimizer convergence evidence for Pillar 3 RQ #2).

**Evaluation runs deferred to Chapter 7:**
- Distribution-shift run (Pillar 3 RQ #3). It is outside this eval-minimum branch and is not part of the Chapter 6 evidence set. Report: "Distribution-shift execution deferred to future work."

**Analyzer scripts in Chapter 6 primary scope:**
- `analyze_results.py` — run in `--chapter6-primary-scope` mode for the M1/M3/M5/ClassAcc outputs used by Stage 2. Deferred M2/M4 sections remain outside the Chapter 6 primary metric contract.
- `analyze_longitudinal.py` — but only the plain (non-shift) path.
- `scripts/backfill_definition_token_costs.py` — static, already shipped, keep as-is.

**Removed or excluded from this eval-minimum branch (not executed for Chapter 6):**
- Compression-loss, context-utilization, and Tier-2-probe analysis readers.
- LangFuse token-cost reconciliation. Definition-token-cost backfill stays.
- Manual mock-contamination audit tooling.
- Distribution-shift longitudinal execution.

Chapter 7 future-work can describe these capabilities by topic so the work is visible, not lost.

**What this cut does to the P0/P1 backlog.** Every finding in ext9–ext17 whose subject line names one of the deferred analyzers or the `run_sheet.md` CAMPAIGN_RUN_IDS framework stops blocking the eval. That's the overwhelming majority. You do not need to fix them; you need to document that the analyzer is deferred, with a one-line pointer from Chapter 7.

---

## 2. The pipeline, end to end

Six stage scripts. Each stage has a **go/no-go check** before you proceed to the next. The check is the point — if it fails, stop and fix; if it passes, move on without second-guessing.

### Stage 0 — Pre-flight (30 min)

Before any eval run, verify the contribution code still works. This is a smoke test, not an audit.

```bash
uv run pytest tests/unit/ -q                 # must pass, all of it
uv run scripts/test_pipeline.py              # one task, one condition, end-to-end
uv run scripts/test_verification.py          # gate works on a good and a bad MCP_TOOL
```

**Longitudinal smoke (3 tasks, ~10-15 min) — added because Stage 4 is the weakest link:**

```bash
rm -f data/eval/longitudinal_smoke.duckdb
uv run scripts/run_longitudinal.py \
    --db data/eval/longitudinal_smoke.duckdb \
    --n-tasks 3 \
    --fresh-db \
    --per-task-budget-seconds 900 \
    --campaign-budget-hours 1
```

Then in duckdb against the smoke DB:

```sql
-- Smoke check: cycle_utility_scores is populated for the three sampled tasks
SELECT COUNT(DISTINCT task_id) FROM cycle_utility_scores;  -- expect >= 1
SELECT COUNT(*)                FROM cycle_events;          -- may be 0 in a tiny smoke
SELECT task_id, COUNT(*) AS rows_per_task
FROM cycle_utility_scores GROUP BY 1 ORDER BY 1;           -- expect non-empty rows
```

The `900s` per-task smoke budget is intentionally looser than the old `600s`
ceiling so Stage 0 does not fail on the same long-running-but-legitimate tasks
that `run_pilot.py` already tolerates via the shared execution backends. This still
catches `cycle_flush.py` fail-open (#125) and the
`cycle_utility_scores`-empty-but-analyzer-exits-0 path (#124) *before* you
commit to the main unattended run. If smoke is clean, Stage 4 is wired correctly; if smoke
shows empty tables or missing task rows, diagnose before Stage 1.

**Go/no-go:** pytest green, `test_pipeline` green, `test_verification` green, longitudinal smoke shows non-empty rows for the sampled tasks. If anything is red, fix before Stage 1.

**Read this before interpreting green.** Stage 0 is a **wiring check**, not a correctness check. Finding #136 flagged `scripts/test_pipeline.py` as fail-open on verification + registration stages — meaning "green" proves the pipeline *ran*, not that it *produced correct numbers*. Correctness lives in Stages 2 and 4 (two-source agreement). If Stage 0 is green and Stage 2 disagrees with SQL, the bug is in the analyzer, not in the pipeline; trust the SQL.

---

### Stage 1 — Main eval (Step 18, 240 records, roughly a day unattended; 32 h hard ceiling)

```bash
scripts/eval_protocol/stage1_main_eval.sh docs/eval_runs/<ts>    # launch, detached
scripts/eval_protocol/stage1_main_eval.sh docs/eval_runs/<ts>    # verify + audit handoff
```

The stage wrapper is the canonical entrypoint. It enforces the fresh-DB rule,
wipes the Stage 1 campaign registry state, passes an explicit MetricsStore path
to `run_pilot.py`, and runs the shipped matrix:

- `20 tasks × 4 conditions × 3 runs = 240 rows`
- `8 h` watchdog budget per condition, so the Stage 1 launcher has a `32 h` hard ceiling across all four conditions

**Go/no-go (after the launcher exits, before any analyzer runs):**

```sql
-- In duckdb CLI against data/eval/metrics.duckdb:

-- Check A: row count is exactly 240
SELECT COUNT(*) FROM evaluation_runs WHERE NOT is_pilot;
-- expect: 240

-- Check B: every (condition, task_id) has exactly 3 rows
SELECT condition, task_id, COUNT(*) AS n
FROM evaluation_runs WHERE NOT is_pilot
GROUP BY 1, 2 HAVING n != 3;
-- expect: zero rows returned

-- Check C: every run_id appears exactly once per (condition, task_id)
SELECT condition, task_id, run_id, COUNT(*)
FROM evaluation_runs WHERE NOT is_pilot
GROUP BY 1, 2, 3 HAVING COUNT(*) > 1;
-- expect: zero rows returned

-- Check D: no NULL gate_passed or NULL output_in_range in non-error rows
SELECT COUNT(*) FROM evaluation_runs
WHERE NOT is_pilot AND (gate_passed IS NULL OR output_in_range IS NULL)
  AND failure_reason IS NULL;
-- expect: 0
```

These four queries are the conceptual completeness gate. The canonical implementation is `scripts/eval_protocol/stage1_main_eval.sh`, which runs the same checks and blocks advancement if they fail.

**If any check fails:** DB is incomplete or corrupted. Do not run analyzers on it. Re-run the failed tasks/conditions via `run_pilot.py --task-ids T01,T02 --conditions full` (single command, fresh tasks appended to the same DB are allowed *only* as recovery of missing rows, never as a first-class campaign-merging mechanism).

**If all four pass:** move to Stage 2.

---

### Stage 2 — Headline numbers with two-source agreement (2 h)

This is the step that catches "exit 0 with silently-wrong numbers."

For each headline metric, produce the number **two ways** and require agreement to three significant figures (or exact equality for integer counts). If they disagree, the analyzer has a bug — fix it or report the cross-check number and flag the analyzer as deferred.

The authoritative Stage 2 audit artifacts are now:

- `20_analyzer_metrics.json` — machine-readable Source 1 numbers extracted directly from the Stage 1 DB snapshot
- `21_sql_crosscheck.json` — machine-readable Source 2 cross-check numbers with semantics aligned to the current codebase
- `22_agreement_summary.json` — machine-readable agreement verdict

Do **not** rely on the ad-hoc SQL snippets that used to live in this section. Several of them drifted over time, for example by not excluding `output_shape_mismatch`, or by assuming `expected_type` lived in `evaluation_runs`. `scripts/eval_protocol/stage2_headline.sh` is now the canonical implementation of the cross-check.

#### M1 — Success rate

Use `stage2_headline.sh`; read `M1` from `20_analyzer_metrics.json` and `21_sql_crosscheck.json`.

**Agreement required:** per-condition percentage matches to 0.01. Record both in `docs/chapter6_numbers.md`.

#### M3 — Gini coefficient on invocation counts

**Semantics being checked:** Gini over **invocation counts pooled by `capability_name` across all non-pilot runs in scope** (one scalar per analyzer section — e.g. "Aggregate", per-condition, per-type). The Stage 2 cross-check implements *pooled-by-capability*; make sure the analyzer section you compare it against uses the same pooling. If `analyze_results.py` reports a "raw per-(run, capability)" Gini and labels it "Aggregate", that is finding #162 — the analyzer is lying about which Gini it computed. Cross-check will surface it.

Use `stage2_headline.sh`; read `M3` from `20_analyzer_metrics.json` and `21_sql_crosscheck.json`.

**Agreement required:** three significant figures.

#### Core-Work Classification Accuracy

Use `stage2_headline.sh`; read `ClassAcc` from `20_analyzer_metrics.json` and `21_sql_crosscheck.json`.

Important implementation note: the cross-check no longer assumes `expected_type` is a column in `evaluation_runs`. It resolves expected types from `tests.evaluation.tasks.ALL_TASKS` and compares them to generated-artifact rows in the JIT conditions. Treat the stage script's JSON outputs as the authoritative cross-check.

**Agreement required:** aggregate percentage to 0.01.

#### M5 — Latency

Use `stage2_headline.sh`; read `M5` from `20_analyzer_metrics.json` and `21_sql_crosscheck.json`.

**Agreement required:** p50 and p95 within ±1 ms.

#### M6 — KL convergence

Runs on `data/eval/longitudinal.duckdb`, not the main DB. See Stage 4.

---

### Manual trace spot-check — main-track integrity check (30 min)

This catches "the pipeline label disagrees with what actually ran." LangFuse is the source of truth for trace behavior; DuckDB is the source of truth for aggregates. They must agree on individual rows.

```sql
SELECT run_id, condition, task_id, gate_passed, output_in_range,
       capability_type, langfuse_trace_id
FROM evaluation_runs
WHERE NOT is_pilot
  AND condition != 'static'
  AND langfuse_trace_id IS NOT NULL
ORDER BY hash(run_id, condition, task_id, 42)
LIMIT 3;
```

Use a deterministic pseudo-random ordering instead of `USING SAMPLE ... REPEATABLE`
here. On DuckDB 1.4.4, reservoir sampling can under-return for some seeds; the
hashed ordering above yields a stable 3-row spot-check without changing the
audit intent.

Sample only rows with a non-NULL `langfuse_trace_id`; `static` rows are not
valid Stage 3 audit targets because the static baseline does not emit
comparable task-level LangFuse traces.

For each of the 3 rows:
1. Open the LangFuse trace in the UI.
2. Confirm the task prompt shown in the trace matches the T-task prompt for `task_id`.
3. Confirm the capability type in the trace matches `capability_type`.
4. Confirm the pipeline outcome in the trace (pass/reject/crash) matches `gate_passed` ∧ `output_in_range`.

If all three rows agree, the pipeline is wired correctly. If one disagrees, localize the bug to that run_id and investigate.

**No analyzer touches LangFuse in Chapter 6.** LangFuse is for debugging and this spot-check. Pagination, session filtering, and `fetch_all_generations` reconciliation — all of that is Chapter 7 if it matters at all.

---

### Stage 3 — Supplemental recurrence track (optional, ~16 h for the default matrix)

This stage is the protocolized Pillar 3 recurrence run. It reuses existing task families with fixture variants so optimizer behavior sees repeated pressure instead of only one-off heterogeneous tasks.

Default matrix:

```bash
RECURRENCE_TASKS="T04,T11,T13,T14,T15,T20" \
RECURRENCE_VARIANTS=3 \
RECURRENCE_CONDITIONS="full,no_verification,no_optimization,static" \
RECURRENCE_RUNS=3 \
RECURRENCE_CONDITION_BUDGET_HOURS=8 \
scripts/eval_protocol/stage3_recurrence.sh docs/eval_runs/<ts>
```

The wrapper launches `scripts/run_recurrence_eval.py`, snapshots the DB, applies a recurrence-specific shape gate, then generates a LangFuse audit request (`recurrence_sample`) exactly like Stages 0/1/4.

Like Stage 1, recurrence now runs with a per-condition watchdog. The default
is `8 h` per condition; override via `RECURRENCE_CONDITION_BUDGET_HOURS` if
hardware is unusually slow. Set it to `0` only for attended debugging.

**Go/no-go:**

- total recurrence row count matches `|tasks| × variants × conditions × runs`
- every `(condition, task_family_id, variant_id)` cell has exactly `RECURRENCE_RUNS` rows
- no duplicate `(condition, task_instance_id, run_id)` tuples
- no NULL `gate_passed` / `output_in_range` on non-error rows

Use this stage for proposal-aligned Pillar 3 evidence. Keep Stage 1 as the breadth run; the recurrence stage is supplemental, not a replacement.

---

### Stage 4 — Longitudinal run + M6 (Step 19, several hours)

This stage is the most vulnerable one in the protocol. `run_longitudinal.py`, `cycle_flush.py`, and `analyze_longitudinal.py` collectively own findings #124, #125, #127, #150, #172, #174, #175 — every one of them is a "exit 0 with silently-wrong or silently-missing M6 number" failure mode. Stage 0's smoke already de-risked the wiring; the checks below are the defense for the full run.

**Pre-run guard (defends #175 — no fresh-DB guard in `run_longitudinal.py`):**

```bash
# Hard-fail if the DB already exists rather than silently appending.
test ! -f data/eval/longitudinal.duckdb || \
    { echo "ERROR: longitudinal.duckdb exists. Delete it explicitly if you mean to re-run."; exit 1; }

# Record the expected number of cycles so the post-run check can assert on it.
export EXPECTED_CYCLES=40   # whatever you pass via --n-tasks, keep it in one env var

caffeinate -i -s uv run scripts/run_longitudinal.py \
    --db data/eval/longitudinal.duckdb \
    --n-tasks $EXPECTED_CYCLES
```

If you re-run after a crash, `rm -f data/eval/longitudinal.duckdb` is the only acceptable reset — do not pass `--append`, do not merge DBs, do not use `HAVING COUNT(*) >= N` selectors (#172, #174).

**Go/no-go after the run (before `analyze_longitudinal.py` is trusted):**

Re-run `scripts/eval_protocol/stage4_longitudinal.sh`. Its verify phase now reconstructs cycle windows from raw `evaluation_runs.timestamp` boundaries and requires:

- exactly the commanded number of longitudinal `evaluation_runs` rows
- exactly one `run_id` in the Stage 4 DB
- one reconstructed utility snapshot per cycle window, even when `task_id` repeats
- per-cycle utility row floor (seeded-registry sanity check)
- numerical agreement between the analyzer's `kl_utility` series and an independent scipy reconstruction

Do not rely on ad-hoc `cycle_id` SQL: the longitudinal tables do not expose a stable `cycle_id` column, and repeated `task_id`s are expected in the 40-task run.

The stage script now writes the authoritative machine-readable outputs:

- `32_stage4_gate.json` — raw-window completeness checks plus independent scipy-style KL reconstruction
- `33_analyzer_m6.json` — machine-readable analyzer-side M6 series
- `34_stage4_agreement.json` — analyzer-vs-scipy agreement summary

**All gate checks must pass and `34_stage4_agreement.json` must report zero disagreements.** If any fail, the analyzer output for M6 cannot be trusted.

**M6 two-source check (defends #124 — silent utility-score → invocation-count downgrade, and #150 — cumulative carry-forward bypass):**

The analyzer reads `cycle_utility_scores`. If that table is sparse, #124 makes it fall back silently to `invocation_events` and report an invocation-count KL *labeled* as utility-score KL. The cross-check below pulls **strictly from `cycle_utility_scores` with no fallback**, so a silent downgrade in the analyzer becomes visible as a disagreement (or a scipy crash on empty data, which is the desired behavior).

Use `stage4_longitudinal.sh`; read Source 1 from `33_analyzer_m6.json` and Source 2 from `32_stage4_gate.json`.

**Agreement required:** every non-`None` KL matches to three significant figures, `primary_kl_source` stays `utility_scores`, and `34_stage4_agreement.json` reports zero disagreements.

**Record both outputs in `docs/chapter6_numbers.md` side by side with the agreement column.** If they disagree on any cycle pair, the analyzer KL is not trustworthy and Chapter 6 should quote the scipy number with a footnote explaining the analyzer disagreement.

---

### Stage 5 — Plausibility priors + flagged-row audit

Run the final plausibility guard after Stage 4 is `PASS`:

```bash
scripts/eval_protocol/stage5_plausibility.sh docs/eval_runs/<ts>
```

This stage does **not** produce a new headline metric. It reads the already-frozen Stage 1 and Stage 4 databases:

- `12_metrics.duckdb` — main eval rows from Stage 1
- `31_longitudinal.duckdb` — longitudinal rows from Stage 4

It writes `40_plausibility.json`, which records sanity checks over the final dataset:

- condition coverage and suspicious success-rate ranges
- static/full/no-verification ordering checks
- dead full-condition tasks
- dominant failure reasons
- capability-type and classifier plausibility
- invocation diversity
- snapshot-leak and longitudinal registry collapse/freeze checks

**Go/no-go:**

- If there are no `INVESTIGATE` flags, the stage marks `PASS` directly. Warnings remain in `40_plausibility.json` for Chapter 6 disclosure.
- If there are `INVESTIGATE` flags, the stage writes `stage5_audit_request.md` using the `plausibility_flagged` profile and moves to `SHAPE_OK`.
- Save the trace-audit response as `stage5_audit_verdicts.md` and re-run the script. Zero audit `FAIL`s moves the marker to `PASS`; any audit `FAIL` moves it to `FAIL`.

After Stage 5 is `PASS`, copy Stage 2, Stage 4, and Stage 5 outputs into `docs/chapter6_numbers.md`. Do not re-run analyzers to regenerate numbers — the recorded pair (analyzer value + cross-check value + agreement yes/no) is the audit trail, and `40_plausibility.json` is the final plausibility record.

**Rule: once a number is in `chapter6_numbers.md` with agreement=yes, it does not change.** If you re-run the eval, start a new file (`chapter6_numbers_v2.md`). Version the numbers, do not mutate them.

---

## 3. Archived security-audit status

`docs/archive/security/security_audit_2026_04_19.md` carries the Chapter 6
closeout note: contribution-code findings are fixed, and later
analyzer/backfill/run-sheet findings are either covered by this protocol's
checks or deferred outside the Chapter 6 evidence set. No security-audit item
remains a Chapter 6 blocker.

---

## 4. What docs actually still make sense

Keep and trust:
- `docs/thesis_proposal.md` — immutable contract.
- `docs/EVAL_PROTOCOL.md` — this file.
- `docs/chapter6_evaluation_report.md`, `docs/chapter6_fact_pack.md`, and `docs/chapter6_numbers.md` — frozen Chapter 6 reporting artifacts.
- `docs/eval_minimum_branch_audit.md` — branch-minimum cleanup record.
- `src/` — the three pillars. Unchanged from 2026-04-17.

Keep as archived reference material, not active task lists:
- `docs/archive/verification/01_*` through `05_*` — per-pillar audits against the proposal. This is where Chapter 4/5 prose can be sourced from.
- `docs/archive/proposal_vs_implementation_v2.md` — scorecard. Useful as appendix/background material if needed.
- `docs/archive/MASTER_PLAN_2026_04_13.md` — historical record of how you got here. Do not use it to drive next steps anymore; use this file.
- `docs/archive/run_sheet.md` — superseded by this protocol.
- `docs/archive/security/*` — bug log. Close-out note only, no further extensions.

Honestly stop re-opening:
- Any of the scripts/ audit docs, test audit docs, or proposal-vs-impl extensions. The measurement apparatus is frozen.

---

## 5. Post-run status

The evaluation campaign has completed. Stage 2 and Stage 4 produced the
two-source agreement artifacts for the headline numbers, Stage 5 produced the
plausibility record, and `docs/chapter6_numbers.md` freezes the resulting
Chapter 6 evidence set.

This file remains the protocol reference for why those artifacts are trusted;
the frozen values and provenance live in the Chapter 6 number and fact-pack
documents.

---

## 6. Finding → Check crosswalk

This table is not an extra action list. It explains why the protocol checks are
enough to trust the Chapter 6 evidence: every defended risk maps to a concrete
shape, agreement, or trace-audit check, and every undefended risk is outside the
Chapter 6 scope defined in §1.

| Finding | What it is | Defended by |
|---|---|---|
| #124 | `analyze_longitudinal.py` silent utility-score → invocation-count downgrade | Stage 4 two-source check reads `cycle_utility_scores` strictly, no fallback. |
| #125 | `cycle_flush.py` fail-open persistence | Stage 0 smoke + Stage 4 Checks 4D (per-cycle floor) + 4E (non-NULL utility). |
| #127 | Global `any()` data-presence gate admits sparse cycles | Stage 4 Check 4D + scipy None-on-empty. |
| #131 | Overfull DB with duplicated tasks per condition would pass a naive row-count check | Stage 1 Check B uses `(condition, task_id)` identity, not just COUNT. |
| #150 | `cumulative_invocations` bypasses #127 | Stage 4 two-source uses raw `cycle_utility_scores`, not cumulatives. |
| #172 | Completeness gate shortfall-only; overfull passes | Stage 4 Check 4A uses `=` against `$EXPECTED_CYCLES`, not `>=`. |
| #174 | `HAVING COUNT(*) >= N ORDER BY ts DESC` picks stale over partial-fresh | Stage 4 pre-run guard (`test ! -f`) eliminates the selector entirely. |
| #175 | No fresh-DB guard on `run_longitudinal.py` | Stage 4 pre-run guard. |
| #164, #166, #181 | `print_classification_accuracy` skip/blacklist/bypass | Stage 2 Core-Work Classification Accuracy two-source check (stage script resolves expected types from `TaskDefinition`, no analyzer trust). |
| #162 | M3 'Aggregate' Gini computed on raw per-(run, capability) instead of pooled | Stage 2 M3 two-source — the stage script explicitly pools by `capability_name`; header/compute divergence surfaces as disagreement. |
| #136 | `test_pipeline.py` fail-open on verification/registration | Stage 0 framed as *wiring check only*; correctness lives in Stage 2 two-source. |
| #126, #128, #147 | Gini silent-skip / per-run coverage gaps | Stage 2 machine-readable cross-check asserts non-empty pooled invocation data. |
| #177 | `print_latency_summary` returns None on empty → silent exit 0 | Stage 2 machine-readable M5 cross-check makes empty/NULL latency output visible as disagreement. |
| #121 | Sample-plan persistence keys on DB stem, overrides new runs | Stage 1 fresh-DB rule — no persisted plans to override. |
| **Deferred (not defended — analyzer or runner outside this eval-minimum branch):** | | |
| #122, #129, #130, #140, #141, #160, #161, #165, #170 | LangFuse reconciliation / pagination / filter gaps (M4 path) | M4 deferred. Proxied by M5 + Step 18.6 definition-token-cost. |
| #132, #137, #143, #144, #146, #158, #159 | M2 contamination audit gates | M2 deferred. |
| #142, #182 | T3 compression-loss taxonomy/coverage | T3 analyzer deferred. |
| #154, #155, #156, #178 | Row 13/15 per-session coverage gates | Row 13/15 LangFuse analyzers deferred. |
| #180 | Row 15 `aggregate()` drops non-list metadata | Row 15 analyzer deferred. |
| #183, #184 | Context-utilization hardcoded window / empty-steps | Context-utilization analyzer deferred. |
| #176 | Shift-runner fresh-DB guard | Shift run deferred. |
| #188 | Shift-runner condition label collision | Shift run deferred. |

**Reading the table:** every "defended" row has a check that fires on the bug. Every "deferred" row is silenced by §1's scope cut — the analyzer or runner is not run for Chapter 6, so its bugs cannot surface in Chapter 6 numbers. Chapter 7 future work can describe the deferred work by topic; the audit trail remains in `docs/archive/security/`.

If a check in Stages 1/4 does not actually fire on the finding it claims to defend, that is a protocol bug — fix it here, not in the analyzer.
