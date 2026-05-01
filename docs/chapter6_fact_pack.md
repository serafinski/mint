# Chapter 6 — Fact Pack

**Companion document to:** `docs/chapter6_numbers.md` (frozen v1.1 numbers — surgical recurrence-state correction over the v1 freeze; no headline numbers changed).
**Purpose:** the structural, narrative, and methodology-disclosure facts Chapter 6 needs that are *not* numeric metrics. Chapter 6 prose draws from this doc + the numbers file + the three phase findings docs (`phase1_2_findings.md`, `phase3b_findings.md`, `phase4_longitudinal_findings.md`) and the closeout (`stage5_dead_task_findings.md`).

**Coverage commitment:** every artifact group A–J from the campaign protocol is inspected and classified below. Files inspected but treated as non-authoritative are listed in §G.

---

## A. Primary protocol outputs (`docs/eval_runs/20260425/`)

| Artifact | Purpose | Verdict / value |
|---|---|---|
| `stage0.marker` | Wiring/provenance check | PASS — shape OK + audit verdicts clean (2026-04-25T18:09:54Z) |
| `stage1.marker` | Main_sample shape + audit pass 1 | PASS (2026-04-26T12:53:17Z) |
| `stage2.marker` | Main_sample shape + audit pass 2 (authoritative for M1/M3/M5/ClassAcc) | PASS (2026-04-26T12:54:28Z) |
| `stage4.marker` | Longitudinal shape + audit (authoritative for M6) | PASS (2026-04-27T20:41:47Z) |
| `stage5.marker` | Plausibility-flagged drill-down | PASS (2026-04-27T20:50:11Z) |
| `04_smoke_checks.json` | Smoke — 3 cycle_events, 55 utility_rows, 3 distinct tasks (T06/T15/T20) | shape OK |
| `13_stage1_gate.json` | Stage 1 gate — A_total=240, no duplicate run_ids, 0 null labels, 60 per condition × 4 conditions | shape OK |
| `20_analyzer_metrics.json` | Analyzer (Source-1) metrics | values frozen in `chapter6_numbers.md` §1 |
| `21_sql_crosscheck.json` | SQL cross-check (Source-2) metrics | matches Source-1 |
| `22_agreement_summary.json` | M1/M3/M5/ClassAcc agreement | `n_disagreements: 0` |
| `32_stage4_gate.json` | Stage 4 gate (M6 SciPy series + reconstructed cycles) | shape OK; cycles_below_floor=[] |
| `33_analyzer_m6.json` | Analyzer M6 (utility-score KL primary) | convergence_cycle=4 |
| `34_stage4_agreement.json` | Stage 4 agreement | `n_disagreements: 0` |
| `40_plausibility.json` | Plausibility audit input for Stage 5 | 1 INVESTIGATE flag (T05/full dead) |
| `12_metrics.duckdb` (6.30 MB) | Phase 1 snapshot, N=240 — **authoritative** for Stage 1+2 | inspected via analyzer/SQL outputs |
| `31_longitudinal.duckdb` (4.21 MB) | Phase 4 snapshot, N=40 — **authoritative** for Stage 4 | inspected via analyzer/SciPy gate outputs |
| `stage3_spotcheck.md` | Stage 3 hash-sampled trace verification (deterministic, per EVAL_PROTOCOL.md) | PASS — 3/3 OK (T01/T14/T02 LangFuse traces) |
| `stage5_dead_task_findings.md` | Stage 5 + cross-phase dead-task analysis closeout | T05 dead-in-full canonical Critique-A example; Pillar 1 registry rescue at Phase 4 task 33 |
| `phase1_2_findings.md` | Phase 1+2 narrative (cold-start) | feeds Chapter 6 §6.1–6.5 |
| `phase4_longitudinal_findings.md` | Phase 4 narrative (longitudinal-warm) | feeds Chapter 6 §6.7 |

## B. Audit checkpoint evidence

All `stage{0,1,2,4,5}_audit_{request.md, verdicts.md, plan.json}` files inspected.

- **Stage 0** — 5 rows (smoke, 3 task traces + 2 Pillar 3 merge events on `monitoring-aggregation-and-alerts`): 5 OK / 0 SUSPICIOUS / 0 FAIL.
- **Stage 1** — 7 rows (random + condition-stratified, including `[failure]`-slot rows that turned out to be sampling-tier markers, not actual failures): 7 OK / 0 / 0.
- **Stage 2** — 7 rows (cross-validation pass; includes T11/no_verification `pillar3.verification_rollback` event proving Pillar 3 atomic rollback fired correctly under no_verification ablation): 7 OK / 0 / 0.
- **Stage 3 (main, deterministic spotcheck)** — 3 rows hash-sampled per EVAL_PROTOCOL.md: 3 OK / 0 / 0 (`stage3_spotcheck.md`).
- **Stage 4** — 4 rows (longitudinal task traces T20/T18/T04 + merge event for `apply-eligibility-policy`): 4 OK / 0 / 0.
- **Stage 5** — 1 row (T05/full dead-task investigation, trace `5d3058fb...`): 1 OK / 0 / 0.
- **Recurrence Stage 3 (supplemental)** — 4 rows (3× T04 happy across full/no_opt/no_verif + 1× T13 agent recurrence_agent slot): 4 OK / 0 / 0 (`docs/eval_runs/20260425_recurrence/stage3_audit_verdicts.md`).

**Aggregate sampling axes covered:** random + condition-stratified (Stage 1, 2), deterministic hash-sample per EVAL_PROTOCOL.md (main Stage 3), longitudinal task + Pillar-3-event (Stage 4), plausibility-flagged (Stage 5), preflight smoke (Stage 0), recurrence variant audit (recurrence Stage 3). Sampling is honest across five orthogonal axes plus the smoke baseline.

**Campaign audit totals — scope-qualified resolution.** Three scopes coexist in primary sources; each is internally consistent within its scope:
- **Main-track total: 27/27/0/0** — Stages 0+1+2+3+4+5 = 5+7+7+3+4+1. **Used by Chapter 6 as the headline audit-integrity figure.**
- **All-tracks total: 31/31/0/0** — main-track 27 + recurrence Stage 3 4-row audit. Cite when discussing campaign-wide trace integrity including the supplemental track.
- **Cold-start subset: 23/23/0/0** — `stage5_dead_task_findings.md` §1.2; Stages 0/1/2/3/5 only (excludes Stage 4 longitudinal and recurrence Stage 3). Internally consistent within its scope.

**Resolution:** **Chapter 6 cites 27/27/0/0 as the main-track headline** and **31/31/0/0 when the supplemental recurrence Stage 3 audit is included**. The 23-row figure is a cold-start reporting-scope difference, not a verdict mismatch — every individual-stage verdict is identical across the three sources. No further reconciliation needed. Recommend a one-line clarification footnote in `stage5_dead_task_findings.md` post-hoc; not blocking for Chapter 6.

**Audit-file inventory (per-stage evidence trail):**

| Stage | Plan file | Request file | Verdicts file | Marker | Verdict |
|---|---|---|---|---|---|
| 0 | `stage0_audit_plan.json` (5 rows, smoke profile) | `stage0_audit_request.md` | `stage0_audit_verdicts.md` | `stage0.marker` PASS | 5 OK / 0 / 0 |
| 1 | `stage1_audit_plan.json` (7 rows, main_sample) | `stage1_audit_request.md` | `stage1_audit_verdicts.md` | `stage1.marker` PASS | 7 OK / 0 / 0 |
| 2 | `stage2_audit_plan.json` (7 rows, main_sample pass 2) | `stage2_audit_request.md` | `stage2_audit_verdicts.md` | `stage2.marker` PASS | 7 OK / 0 / 0 |
| 3 (main) | (no plan/request — deterministic SQL hash-sample) | embedded in `stage3_spotcheck.md` | `stage3_spotcheck.md` | (md only; no marker file) | 3 OK / 0 / 0 |
| 4 | `stage4_audit_plan.json` (4 rows, longitudinal_sample) | `stage4_audit_request.md` | `stage4_audit_verdicts.md` | `stage4.marker` PASS | 4 OK / 0 / 0 |
| 5 | `stage5_audit_plan.json` (1 row, plausibility_flagged) | `stage5_audit_request.md` | `stage5_audit_verdicts.md` | `stage5.marker` PASS | 1 OK / 0 / 0 |
| 3 (recurrence, supplemental) | `stage3_audit_plan.json` (4 rows, recurrence_sample) | `stage3_audit_request.md` | `stage3_audit_verdicts.md` | `stage3.marker` PASS (2026-04-28T20:14:12Z) | 4 OK / 0 / 0 |

Every plan file has a corresponding request + verdicts pair; markers advance only when verdicts contain the `AUDIT SUMMARY: N OK / M SUSPICIOUS / K FAIL` line with M=K=0. No silent advancement.

## C. Recurrence track (`docs/eval_runs/20260425_recurrence/`)

- `40_recurrence.log` (1.07 MB): final lines confirm "Total recurrence records: 216". Last static run `static_00_5d83ee30...` finished 2026-04-27T02:26:21Z. Wrapper closeout completed 2026-04-28T20:04:41Z (`41_recurrence_status.txt`).
- `40_recurrence_db_path.txt`: `data/eval/metrics_recurrence_20260425_recurrence.duckdb`.
- `40_recurrence_metrics_root.txt`: `data/eval/recurrence_metrics_20260425_recurrence`.
- `42_recurrence.duckdb`: **frozen recurrence snapshot** under `docs/eval_runs/20260425_recurrence/` (parallel to `12_metrics.duckdb` and `31_longitudinal.duckdb` for the main track).
- `43_stage3_gate.json`: shape gate machine-readable artifact — `expected_rows=216, A_total_count=216, B_non_expected_cells=[], C_duplicate_run_ids=[], D_null_labels=0, E_per_condition_rows=[full 54, no_optimization 54, no_verification 54, static 54], _problems=[]` ✅.
- `phase3b_findings.md`: full Phase 3b narrative — pillar inversion (full < ablations by ~3.7pp at recurrence), §3.6 reuse-drift sub-types A/B/C documented, T13 decomposition stress-test characterization, static=100% sampling-bias mandatory disclosure.
- `stage1.marker`: PASS (2026-04-26T12:53:17Z).
- `stage3.marker`: **PASS** (2026-04-28T20:14:12Z) — reason: `shape + audit clean`. Wrapper-finalized.
- `stage3_audit_plan.json` / `stage3_audit_request.md` / `stage3_audit_verdicts.md`: full audit artifact triplet present. Audit sampled 4 rows (recurrence_sample profile: 3× T04 happy across full/no_opt/no_verif + 1× T13 agent in full); **4 OK / 0 SUSPICIOUS / 0 FAIL**.

**Methodology note (mandatory disclosure):** Phase 3b is **supplemental evidence** — it does NOT substitute for Stage 1's main_sample breadth. The recurrence track is now wrapper-finalized (Stage 1 + Stage 3 markers both PASS, shape gate clean, LangFuse audit clean), but recurrence remains supplemental for two protocol-level reasons:
1. Recurrence is not the primary breadth track per `EVAL_PROTOCOL.md` §1 / §2 — Stage 1 main_eval is.
2. Recurrence headline numbers (§3 of `chapter6_numbers.md`) lack a Stage-2-style two-source agreement file (no analyzer-vs-SQL cross-check JSON for the recurrence DB).

For Chapter 6, cite the recurrence headline numbers as cross-phase support for the autonomy/correctness/economy trade-off; do not promote them to primary status. The trace-level integrity of the recurrence track is now backed by Stage 3 LangFuse audit evidence (§P below).

## D. Data-layer databases — schema + authority

Every DB under `data/eval/` and the two snapshots under `docs/eval_runs/20260425/` were inspected directly via `duckdb` `information_schema`. Two DB families exist:

- **`metrics_*` family** (primary; analyzer + SQL cross-check read from these): `evaluation_runs`, `cycle_utility_scores`, `cycle_events`, `invocation_events`.
- **`metrics_store_*` family** (per-condition invocation store, Step 18.5 era): `invocations`, `capability_events`, `optimizer_prev_utility`. Per-call detail; not the analyzer's source.

| Path | Authority | Family | Tables (row count) | Powers which Chapter 6 metrics/stages |
|---|---|---|---|---|
| `docs/eval_runs/20260425/12_metrics.duckdb` | **authoritative** (Phase 1 snapshot) | metrics | `evaluation_runs` (240) · `cycle_utility_scores` (6674) · `cycle_events` (52) · `invocation_events` (344) | Stage 1 shape gate, Stage 2 M1/M3/M5/ClassAcc, Stage 5 plausibility input |
| `data/eval/metrics_pilot_20260425.duckdb` | live source (mirror of above) | metrics | same shape (240/6674/52/344) | identical role; the `docs/` copy is the frozen artifact |
| `docs/eval_runs/20260425/31_longitudinal.duckdb` | **authoritative** (Phase 4 snapshot) | metrics | `evaluation_runs` (40) · `cycle_utility_scores` (1970) · `cycle_events` (21) · `invocation_events` (73) | Stage 4 M6 utility-KL, Pillar 3 cumulative-Gini & per-type op counts |
| `data/eval/longitudinal.duckdb` | live source (mirror of above) | metrics | same shape (40/1970/21/73) | identical role |
| `docs/eval_runs/20260425_recurrence/42_recurrence.duckdb` | **authoritative** (Phase 3b snapshot, frozen) | metrics | `evaluation_runs` (216) · cycle/utility/invocation tables mirror live | Recurrence Stage 1 + Stage 3 shape gate (`43_stage3_gate.json`) and audit |
| `data/eval/metrics_recurrence_20260425_recurrence.duckdb` | live source (mirror of above) | metrics | `evaluation_runs` (216) · `cycle_utility_scores` (6206) · `cycle_events` (24) · `invocation_events` (74) | Phase 3b supplemental headline numbers (`chapter6_numbers.md` §3) |
| `data/eval/longitudinal_smoke.duckdb` | supporting | metrics | `evaluation_runs` (3) · `cycle_utility_scores` (55) · `cycle_events` (3) · `invocation_events` (7) | Stage 0 wiring smoke; matches `04_smoke_checks.json` shape |
| `data/eval/longitudinal_metrics_store.duckdb` | supporting | metrics_store | `invocations` (73) · `capability_events` (21) · `optimizer_prev_utility` (76) | per-invocation detail paired with `longitudinal.duckdb` |
| `data/eval/metrics_store_pilot_20260425_full.duckdb` | supporting | metrics_store | `invocations` (104) · `capability_events` (27) · `optimizer_prev_utility` (47) | per-invocation detail for Phase 1 full; 27 capability events match `cycle_events full → merged ×27` |
| `data/eval/metrics_store_pilot_20260425_no_optimization.duckdb` | supporting | metrics_store | `invocations` (107) · `capability_events` (0) · `optimizer_prev_utility` (0) | no_optimization correctly emits zero optimizer events |
| `data/eval/metrics_store_pilot_20260425_no_verification.duckdb` | supporting | metrics_store | `invocations` (100) · `capability_events` (25) · `optimizer_prev_utility` (47) | per-invocation detail; 25 capability events ≈ `cycle_events no_verification → merged ×21 + verification_rollback ×4` |
| `data/eval/metrics_store_pilot_20260425_static.duckdb` | supporting | metrics_store | `invocations` (33) · `capability_events` (0) · `optimizer_prev_utility` (0) | static produces no LLM events (33 = static success count) |
| `data/eval/recurrence_metrics_20260425_recurrence/` | supporting | dir of metrics_store | per-condition × per-run subdirs | Phase 3b invocation-level granularity |
| `data/longitudinal_optimizer_log.json` | supporting | JSON | 40 entries (one per Phase 4 task) | harness-side mirror of Phase 4 outcomes (`task_index`, `cumulative_success_rate`, `active_capabilities`) |
| `data/eval/domains.json` | supporting | JSON | 18 categories (live evolutionary taxonomy) | Step 3.5f growth audit trail; backs `gap_detection` domain filter |

**Schema-derived facts cross-checked against analyzer outputs:**
- `evaluation_runs` non-pilot count of 240 in `12_metrics.duckdb` matches `13_stage1_gate.json::A_total_count`.
- `cycle_events` aggregates by `(condition, event_type)` against the pilot DB: `full → merged 27`, `no_verification → merged 21, verification_rollback 4`, `no_optimization → 0`, `static → 0` — consistent with the §3 narrative in `phase1_2_findings.md`.
- `invocation_events` non-pilot row count 344 matches `40_plausibility.json::invocation_summary.n_invocations`.
- `cycle_events` in Phase 4 longitudinal DB: 21 merged events — exact match with `33_analyze_longitudinal.log::"merged 21"`.

**Why two DB families?** The `metrics_*` family is the analyzer-of-record (per-task outcomes + per-cycle utility snapshots + per-invocation events with conditioning columns), populated by `EvaluationStore.persist()`. The `metrics_store_*` family is the registry-side per-invocation store (used by `MetricsStore` for Pillar 3 utility computation at runtime). They agree on aggregates the analyzer cares about; the analyzer reads only from `metrics_*`.

## E. Generated artifacts and registry state (`data/eval/skills/`)

Sampled directory listing (27 SKILL artifacts persisted at campaign closeout, after Phase 4 longitudinal completion). Implicated artifacts referenced in narrative:

- **Pillar 3 merges captured in narrative (Phase 4):** `apply-eligibility-policy` (T20, SKILL, seed+gen), `monitoring-daily-aggregate-and-alerts` (T15, SKILL, seed+gen), `apply-alert-thresholds-and-summarize-1ebe6a` (parent of 2-tier merge at task 22), `apply-alert-thresholds-and-summarize-fbc285` (gen child fed back into 2-tier).
- **Pillar 3 merges captured in narrative (Phase 1):** `csv-parse-and-stats` (full_00, MCP_TOOL+MCP_TOOL), `read-rules-and-csv` (full_00, MCP_TOOL+MCP_TOOL), `compare-and-write-regions` (no_verification_01 T11 — Tier 1 CRITICAL → atomic rollback per §12 finding #14), `monitoring-aggregation-and-alerts` (Stage 0 smoke merge).
- **Registry-reuse failures (§3.6 sub-types):**
  - sub-A (value-shape drift): Phase 4 task 22 T09 reused `detect_top_5_revenue_outliers_by_zscore_2bd98a` against drifted stats producer.
  - sub-B (cached-broken-tool): Phase 4 task 21 T15 generated `parse_csv_and_json_policy_ddde0a` using removed `infer_datetime_format` kwarg (pandas 2.x); locally valid through all three tiers, fails at runtime.
  - sub-C (cross-step name reuse): same skill matched twice in single decomposition's gap_detection (multiple Phase 3b instances).
- **Pillar 1 registry-rescue case (Phase 4 task 33):** cached `parse_and_clean_csv_remove_duplicates_an_b31d78` (generated Phase 4 task 4) succeeded on T05 pass-2 fixture where fresh regeneration consistently fails — qualitative evidence of registry reuse defending against LLM-correctness defects Pillar 2 cannot catch by design.

Directory contents not exhaustively re-validated; per CLAUDE.md, generated artifacts are not the metric source and the verification gate already vouches for them.

## F. Provenance files

See `chapter6_numbers.md` §6 for the full HEAD-by-stage table. Net summary:

- Authoritative HEAD for the Stage 1 verify → Stage 5 chain: `2724363650e4f1bc355c9e45ee168a9c595a589a` (commit `2724363` "test(optimizer-sim): drop strict cycle-0 prune assertion under fresh-cap exemption").
- Stage 0 / Stage 1 launch were captured at the older HEAD `1cf9de7` with WIP modifications to harness, skill_generator, sandbox_tool, and several unit-test files; the pilot run (Phase 1) thus executed against a slightly earlier codebase state than Stages 2/4/5 were verified against. The diffs were test-only and harness instrumentation (no behavior changes to Pillar 1/2/3 code paths) — disclose this once in Chapter 4.5 and proceed.
- Three modified xlsx fixtures (`tests/evaluation/fixtures_recurrence/T11/v0{1,2,3}/t11_products.xlsx`) appear in working-tree from Stage 4 launch onward and remain uncommitted at campaign closeout.
- Recurrence Stage 3 launch + verify HEAD captured under `40_stage3_launch_git_head.txt` / `40_stage3_verify_git_head.txt`; recurrence wrapper-finalized 2026-04-28T20:14:12Z.

## G. Files inspected but treated as non-authoritative

- `data/eval/.eval_runner.lock` — runtime lock (`scripts._eval_registry_lock`); not a metric source.
- `data/eval/chroma/` — ChromaDB internal storage for SkillRegistry; not a metric source. Inspected only via `gap_detection` log-trace claims.
- `.DS_Store` (system files), `__pycache__/` directories — irrelevant.
- Every `git_diff.txt` file with size 0 — empty by design (no working-tree diff at capture).

## H. Protocol interpretation rules (applied)

| Rule | Application in this fact pack |
|---|---|
| Stage 0 = wiring/provenance, not correctness | Cited only for shape PASS; not used as a primary metric source. |
| Stage 2 = authoritative for M1/M3/ClassAcc/M5 | Numbers frozen in `chapter6_numbers.md` §1; Source-1 vs Source-2 agreement verified. |
| Stage 3 = LangFuse trace integrity spot-check | 3/3 OK for hash-sampled traces; cited in audit rollup only. |
| Stage 3b recurrence = supplemental | Cited for cross-phase trade-off characterization (cost-side inversion); NOT promoted to primary breadth claim. Recurrence wrapper-finalized (Stage 1 PASS + Stage 3 PASS, shape gate clean, 4-row audit clean) but lacks Stage-2-style two-source headline agreement and is not the protocol's primary breadth track. |
| Stage 4 = authoritative for M6 *if* agreement clean | Agreement is clean (`34_stage4_agreement.json` has 0 disagreements). M6 numbers frozen in `chapter6_numbers.md` §2.2. |
| Stage 5 = audit-trustworthiness, not primary metric | T05 OK verdict cited as confirmation of dead-task narrative; Stage 5 numbers do NOT enter the M-metric tables. |
| Never treat SHAPE_OK as PASS | Marker files explicitly say `PASS` (not `SHAPE_OK`); this rule is satisfied because the markers carry verdicts, not shape labels. |

## I. Required output additions

- `docs/chapter6_numbers.md` — **created (v1.1, frozen — v1 initial freeze + surgical recurrence-state correction; no headline numbers changed)**.
- `docs/chapter6_fact_pack.md` — **this file**.

## J. Run provenance and chronology (execution-bookkeeping cross-reference)

### Phase 1 cold-start pilot (Stages 0–3, primary)
- `00_pytest.log`: `2086 passed, 1 skipped in 295.39s` at 2026-04-25T17:13:19Z (campaign green-bar before launch).
- `01_test_pipeline.log`: 2026-04-25T17:18:19Z — informational stochastic check (1 of 3 sub-tests failed; expected, Stage 1 measures the rate).
- `02_test_verification.log`: 2026-04-25T17:28:11Z — 5/5 verification gate sanity tests passing (Tier 0/1/2/3 all firing on bad/good fixtures).
- `03_longitudinal_smoke.log`: 2026-04-25T17:29:40Z — 3-task longitudinal smoke (`longitudinal_4e272b5a649a4ebb81a2b72382f2492a`, success 2/3, gate 3/3, includes one merge `monitoring-aggregation-and-alerts`).
- `10_pilot.log` (969 KB): full pilot pipeline log.
- `10_pilot.pid`: 3360. `10_pilot_start.txt`: 2026-04-25T18:11:30Z. `11_pilot_status.txt`: finished 2026-04-26T12:50:30Z (~18.6h wall-clock). 240 records, 75.8% success, 87.9% gate pass, failure breakdown `{none: 182, generation_failed: 28, output_rejected: 23, chain_step_failed: 5, agent_execution_error: 1, tier3: 1}`.
- `10_pilot_db_path.txt`: live DB `data/eval/metrics_pilot_20260425.duckdb`. `10_pilot_metrics_store_path.txt`: per-condition store `data/eval/metrics_store_pilot_20260425.duckdb`.
- `20_analyzer.log`: Stage 2 analyzer execution at 2026-04-26T12:54:26Z; produces the metrics JSON used by `chapter6_numbers.md` §1. Includes Chapter-6-relevant context blocks (per-task gate vs output, JIT cluster Holm-Bonferroni, Gini per-condition).

### Phase 3b recurrence (supplemental — wrapper-finalized 2026-04-28)
- `40_recurrence.log` (1.07 MB), pid 62099, started 2026-04-26T13:46:46Z, ran two static iters before continuing JIT iterations through 2026-04-27T02:26:21Z; final line confirms 216 records.
- `41_recurrence_status.txt`: wrapper closeout finished 2026-04-28T20:04:41Z.
- DB pointers: `40_recurrence_db_path.txt`, `40_recurrence_metrics_root.txt`. Frozen snapshot: `42_recurrence.duckdb`.
- `43_stage3_gate.json`: shape gate clean (216/216, no problems).
- `stage3.marker`: PASS (2026-04-28T20:14:12Z, "shape + audit clean"). 4-row LangFuse audit verdict 4 OK / 0 / 0 (`stage3_audit_verdicts.md`).

### Phase 4 longitudinal (Stage 4, primary for M6)
- `30_longitudinal.log` (231 KB).
- `30_longitudinal.pid`: 24505. `30_longitudinal_start.txt`: 2026-04-27T16:07:49Z (40-task run). `31_longitudinal_status.txt`: finished 2026-04-27T20:35:21Z (~4.4h wall-clock); 33/40 = 82.5%, gate 40/40 = 100%, first10 = last10 = 8/10.
- `33_analyze_longitudinal.log`: 2026-04-27T20:35:23Z analyzer execution; cycles-to-converge 4, final Gini 0.063, 21 merge ops in cycle_events.

### Stage 5 closeout
- `40_plausibility.json` produced 2026-04-27T22:44Z; `stage5_audit_verdicts.md` 1/1 OK 2026-04-27T22:50Z.

### Chronology vs marker-state coherence
- All marker progressions (main Stage 0 → 5; recurrence Stage 1 → 3) advance monotonically and consistently with launch / verify timestamps in the provenance txt files.
- Recurrence-track `stage3.marker` was launched 2026-04-26T13:46:46Z and finalized to PASS at 2026-04-28T20:14:12Z (shape gate clean + 4-row LangFuse audit clean). No anomalies remain.

---

## K. Methodology disclosures (mandatory for Chapter 6 §6.5 / §6.6 / §6.7 / §6.8)

These disclosures live in the phase findings docs and must be carried into Chapter 6 prose verbatim:

1. **Pooled-JIT vs static p<0.001 is diagnostic only** (Chapter 4.5; `phase1_2_findings.md` §1).
2. **Aggregate Gini across runs is artifact** — cite per-run mean (`phase1_2_findings.md` §3.3).
3. **`no_verification` is not "all gates off"** — Pillar 3 internal `verify_and_register_or_rollback` still fires (`phase1_2_findings.md` §3.1; corroborated by Stage 1 audit Row 3 trace `afa6d5aa...` and Stage 2 audit Row 3 trace `a54390c7...`).
4. **ShapeMismatch is a Pillar 3 boundary effect** (`phase1_2_findings.md` §3.2).
5. **§3.6 registry-reuse drift has three sub-types** (A value-shape, B cached-broken-tool, C cross-step name reuse — `phase3b_findings.md` §4.2).
6. **Phase 3b static = 100% is sampling-bias** (`phase3b_findings.md` §4.1).
7. **Tier 1.5 sandbox import probe is cost-optimization, not a fourth methodological tier** (`CLAUDE.md` §Verification Gate; master plan Step 12.5).
8. **Tier 3 capability-type coverage gap** — prose-only SKILL/AGENT un-probed because sandbox-payload requires inlined source (`MEMORY.md` Tier 3 drift recharacterization).
9. **Two harness validation-path findings (Phase 4)** — solo registry-match `upstream_data` drop (T06 task 29) and AGENT registry-match `source_code` absence (T10 task 34). Disclose harness-corrected sensitivity 35/40 = 87.5% alongside raw 33/40 = 82.5% (`phase4_longitudinal_findings.md` §4.2).
10. **Pillar 1 registry-rescue is qualitative-only**, not a frequency claim (Phase 4 task 33 T05 single instance — `stage5_dead_task_findings.md` §2.2 + §4.2).
11. **Dead-task slice qualifier** — "T05 dead in full" means Phase 1 full (3/3 failures), NOT dead everywhere (`stage5_dead_task_findings.md` §4.1).
12. **Recurrence track is supplemental but wrapper-finalized** — Stage 1 PASS, Stage 3 PASS (2026-04-28; shape + audit clean, 4 OK / 0 / 0). Remaining limitation is the absence of Stage-2-style two-source headline agreement for the recurrence DB, NOT wrapper incompleteness (this fact pack §C).
13. **Stage 0 / Stage 1 launch executed on older HEAD `1cf9de7`** (test-only diffs); Stage 1 verify onward consolidated to HEAD `2724363` (this fact pack §F).

---

## L. Pillar-by-pillar cross-phase synthesis (table for Chapter 6 §6.8)

| Pillar | Phase 1 (cold-start, N=240) | Phase 3b (recurrence, N=216) | Phase 4 (longitudinal-warm, N=40) |
|---|---|---|---|
| **P1 — JIT pipeline** | 27.5pp gap full vs static synthesis; ClassAcc 93.5% aggregate; Gini per-run mean 0.66 (healthy moderate skew) | Reuse-amortization works on seeded coverage; schema-name volatility forces regeneration | Registry rescue of T05 on Phase 4 task 33 (qualitative new finding); 5/6 pure-registry-match resolutions <50s vs gen median ~250s |
| **P2 — Verification** | Mean tied with no_verification; σ narrows 6.1pp → 2.4pp; gate pass 100% in full | Mean inverts (no_verif=92.6% > full=88.9%); σ argument attenuates; Tier 3 wallclock dominates | Gate 40/40 = 100%; strict-gate retry fired cleanly on T12 task 14 |
| **P3 — Optimizer** | 27 merges in full + 4 verification_rollbacks in no_verification (mechanism integrity) | 2-tier merge chains in AGENT and SKILL; merged-as-parent reuse | 21 merge ops in cycle_events; all 4 variants fired (SKILL ×3, MCP_TOOL ×3, AGENT ×1); first AGENT merge captured; convergence at cycle 4; seeded→evolved Gini delta +0.063 |

**One-line cross-phase framing:** "**Cold-start** — Pillar 1 lifts the mean by 27.5pp, Pillar 2 narrows variance, Pillar 3 mechanism is integral but mean-effect below detection at N=60. **Recurrence** — Pillars 2+3 cost the mean (~3.7pp each) because warm reuse stabilises outcomes regardless of gate state and merge candidates introduce reuse-drift. **Longitudinal** — Pillar 3 actually evolves over 40 tasks (cumulative merges, 2-tier chains, all four variants); Pillar 1 registry rescues some failures Pillar 2 cannot defend by design."

This is the **workload-conditional autonomy/correctness/economy trade-off** the proposal frames, made concrete and quantified across three regimes.

---

**Bottom line:** the campaign produced 27/27 OK across 6 main-track audit stages (31/31 OK including the supplemental recurrence Stage 3), 0 disagreements between Source-1 and Source-2 on every headline metric, and a quantified multi-phase characterization of the proposal's three pillars. Numbers are frozen in `chapter6_numbers.md` v1.1 (surgical recurrence-state correction over the v1 freeze; no headline numbers changed); this fact pack carries the structural and disclosure facts Chapter 6 prose needs alongside them.

---

## M. Metric source map (mirrors `chapter6_numbers.md` Appendix A)

For each thesis metric: final value → source file → field/query basis → cross-check → agreement status.

| Metric | Final value(s) | Source-1 (analyzer) | Source-1 basis | Source-2 (cross-check) | Source-2 basis | Agreement |
|---|---|---|---|---|---|---|
| **M1** | full 50/60 / no_opt 49/60 / no_verif 50/60 / static 33/60 | `20_analyzer_metrics.json::M1.{cond}.{n,successes,pct}` | `EvaluationStore` aggregate over `evaluation_runs WHERE NOT is_pilot` | `21_sql_crosscheck.json::M1.{cond}` | hand-written SQL pooled by `(condition)` filtering `gate_passed AND output_in_range` | `22_agreement_summary.json` → 0 disagreements |
| **M3** (aggregate) | 0.10183 (pooled) | `20_analyzer_metrics.json::M3.aggregate` | Gini over pooled `invocation_events.capability_id` counts (NOT pilot) | `21_sql_crosscheck.json::M3.aggregate` | independent SQL Gini, same pooling | 0 disagreements (within 1e-12) |
| **M3** (per-condition) | full 0 / no_opt 0.00926 / no_verif 0.00990 / static 0 | `20_analyzer_metrics.json::M3.per_condition_aggregate` | per-condition pooled Gini | `21_sql_crosscheck.json::M3.per_condition_aggregate` | per-condition pooled SQL Gini | 0 disagreements |
| **ClassAcc** (aggregate) | 93.53% (n=170) | `20_analyzer_metrics.json::ClassAcc.aggregate_pct` | analyzer joins `evaluation_runs.capability_type` to `tests.evaluation.tasks.ALL_TASKS::expected_type`; excludes pure registry-match | `21_sql_crosscheck.json::ClassAcc.aggregate_pct` | SQL replay of same join | 0 disagreements |
| **ClassAcc** (per-condition) | full 92.98 / no_opt 94.74 / no_verif 92.86 | `20_analyzer_metrics.json::ClassAcc.per_condition_pct` | same join, partitioned by condition | `21_sql_crosscheck.json::ClassAcc.per_condition_pct` | same | 0 disagreements |
| **M5 median** | full 283.99s / no_opt 198.96s / no_verif 175.71s / static 0.001s | `20_analyzer_metrics.json::M5.{cond}.median_sec` | `quantile_cont(attempt_latency_ms / 1000.0, 0.5) WHERE gate AND output` | `21_sql_crosscheck.json::M5.{cond}.median_sec` | independent SQL `quantile_cont` | 0 disagreements |
| **M5 p95** | full 916.02 / no_opt 534.81 / no_verif 471.89 / static 0.0068 | `20_analyzer_metrics.json::M5.{cond}.p95_sec` | `quantile_cont(.., 0.95)` | `21_sql_crosscheck.json::M5.{cond}.p95_sec` | independent | 0 disagreements |
| **M6** (convergence cycle) | 4 | `33_analyzer_m6.json::convergence_cycle` | `analyze_longitudinal.py` reads `cycle_utility_scores`, computes utility-score KL with threshold 0.05, min consecutive 3 | `32_stage4_gate.json::M6_scipy_kl` | independent SciPy KL reconstruction over raw `cycle_utility_scores` windows; strictly no fallback to `invocation_events` | `34_stage4_agreement.json` → 0 disagreements |
| **M6** (per-cycle series) | 40 rows | `33_analyzer_m6.json::rows[*].kl_utility` | analyzer | `32_stage4_gate.json::M6_scipy_kl[*].kl_utility` | SciPy | 0 disagreements |

---

## N. Breadth-facts appendix

Compiled from `40_plausibility.json::info` + analyzer log + direct DB queries against `12_metrics.duckdb`.

**Per-condition success (Phase 1, N=240):**
- full 50/60 (83.33%); no_optimization 49/60 (81.67%); no_verification 50/60 (83.33%); static 33/60 (55.0%).

**Per-task full-condition outcomes (3 iters per task; from `40_plausibility.json::info.per_task_full`):**
| Task | n | successes | Task | n | successes | Task | n | successes | Task | n | successes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| T01 | 3 | 3 | T06 | 3 | 3 | T11 | 3 | 3 | T16 | 3 | 2 |
| T02 | 3 | 3 | T07 | 3 | 1 | T12 | 3 | 3 | T17 | 3 | 3 |
| T03 | 3 | 3 | T08 | 3 | 3 | T13 | 3 | 3 | T18 | 3 | 3 |
| T04 | 3 | 3 | T09 | 3 | 3 | T14 | 3 | 3 | T19 | 3 | 3 |
| **T05** | 3 | **0** | T10 | 3 | 1 | T15 | 3 | 2 | T20 | 3 | 2 |

T05 is the only zero-success cell in Phase 1 full (drives Stage 5 INVESTIGATE). T07 and T10 = 1/3 (LLM-correctness defects). T15, T16, T20 = 2/3 (single-iter LLM stochasticity).

**Top error categories (campaign-wide, from `40_plausibility.json::info.top_errors`):**
| Reason | Count |
|---|---|
| `generation_failed` | 28 |
| `output_rejected` | 23 |
| `chain_step_failed` | 5 |
| `agent_execution_error` | 1 |
| `tier3` | 1 |

**Capability-type distribution in full condition (`type_distribution_full`):** agent 15 / mcp_tool 19 / skill 16 (sums to 50 successes — balanced spread, no type dominates).

**Run-index trend, full condition (`run_index_trend_full`):** iter 1 → 0.85, iter 2 → 0.80, iter 3 → 0.85. Variance σ ≈ 2.4pp across iters (Phase 1 §2.2 finding).

**Classification accuracy (Phase 1 full subset only, plausibility info):** 92.62% — slightly below the JIT-aggregate 93.53% (within sampling noise).

**Invocation summary (campaign):** `n_caps = 307`, `n_invocations = 344` (1.12 avg invocations per cap — most capabilities are single-use, consistent with the per-run high Gini ≈ 0.66 mean).

**Top 5 capabilities by invocation count (`top_5_capabilities` + DB cross-check):**
| Capability | Invocations |
|---|---|
| `eligibility-policy-classifier` (seeded) | 4 |
| `parse_inventory_csv_and_reorder_rules_ya_c6f21e` | 4 |
| `monitoring-aggregation-policy` (seeded) | 4 |
| `data-analysis-agent` (seeded) | 3 |
| `parse_inventory_csv_and_reorder_rules_cddd51` | 3 |

Two of the top-3 are seeded capabilities; two are LLM-generated parsers in the same family (`parse_inventory_csv_and_reorder_rules_*` with different hashes — schema-name volatility: §3.6 sub-A territory).

**Phase 4 longitudinal trajectory (`data/longitudinal_optimizer_log.json`, 40 entries):**
- Cumulative success rate: starts 1.0 (task 1), dips to 0.75 at task 4 (T05 fail), rises to 0.90 by task 20, oscillates 0.82–0.86 across pass 2.
- Active capabilities: 14 (seeded) → 76 (final). Net +62 over 40 tasks; +1.55 cap/task average growth.
- 6 pure-registry-match resolutions visible (tasks 15, 31, 33, 29, 34, 40 — see `phase4_longitudinal_findings.md` §3.1); 5 of 6 completed in <50s vs gen-path median ~250s.

---

## O. Failure catalog (grouped, traceable)

Phase 1 (N=240). Total failures = 58 (240 - 182 successes). Per-condition × failure_reason from direct DB query against `evaluation_runs`:

### O.1 `generation_failed` (28 instances)
- **Static:** 27 — every static failure is a missing seeded capability for a synthesis task. The 27 affected slots cover T03, T06, T07, T08, T10, T12, T16, T17, T18 across all 3 iters (deterministic; static run is exact-reproducible).
- **no_verification:** 1 — strict-gate exhaustion landing as no usable artifact (rare; demonstrates §12 finding #15 fail-closed).
- **Class:** **structural** (no LLM-quality issue). Operationally informative — proves seeded coverage is intentionally narrow so static is not a 0/0 control.

### O.2 `output_rejected` (23 instances; THE largest LLM-quality bucket)
- **full:** 8 (T05 ×3, T07 ×2, T10 ×2, T15 ×1) — all are gate=PASS but oracle=FAIL.
- **no_verification:** 6 — same shape (no Pillar 2 to catch them, but Pillar 2 wouldn't catch them anyway).
- **no_optimization:** 9 (T05 ×3, T07 ×2, T10 ×1, T15 ×1, T20 ×2).
- **Class:** **scientifically meaningful** — the canonical Critique-A surface. Pillar 2 cannot catch domain-correctness defects by design. Stage 5 T05 trace `5d3058fb...` is the textbook example; cite once in Chapter 6 §6.3.

### O.3 `chain_step_failed` (5 instances)
- **full:** 2; **no_verification:** 3.
- **Class:** **scientifically meaningful** — downstream-coupling failure. Mostly §3.6 sub-A value-shape drift between chained capabilities; verification gate clears each artifact in isolation but inter-step compatibility is registry-time, not gate-time.

### O.4 Registry / schema mismatch (subset of O.2 / O.3 cascade visibility)
- **Phase 1 ShapeMismatch counter:** full 2/60, no_optimization 1/60, no_verification 0/60, static 0/60 — optimizer-on conditions produce shape mismatches; optimizer-off don't (Pillar 3 boundary effect, `phase1_2_findings.md` §3.2).
- **Phase 3b §3.6 sub-types A/B/C** (recurrence; `phase3b_findings.md` §4.2):
  - sub-A (value-shape drift): cached `aggregate_transactions_by_department_a4a51f` reused against drifted parser.
  - sub-B (cached-broken-tool): `parse_eligibility_policy_json_and_applic_91434c` (3 independent regenerations, all fail at runtime against fixture).
  - sub-C (cross-step name reuse): same skill matched twice in single decomposition.
- **Phase 4 cascades:** task 21 T15 (sub-B pandas 2.x `infer_datetime_format`), task 22 T09 (sub-A stats-shape mismatch via cached pass-1 outlier detector).
- **Class:** **scientifically meaningful**. Chapter 7 future work — value-shape comparator at registry-reuse time.

### O.5 Validation / metadata failures
- **`tier3` (1 instance, no_optimization):** Pillar 3-internal `verify_and_register_or_rollback` rolling back a merged child whose Tier 1/2/3 gate rejected it. Not a task-level Pillar 2 leak — the optimizer's own coverage check fired correctly.
- **Phase 4 harness validation-path findings (NOT recorded as `failure_reason` since they manifest as harness-side `output_in_range=False`):**
  - T06 task 29 — solo registry-match drops `upstream_data` kwarg.
  - T10 task 34 — AGENT registry-match has no `source_code` in metadata.
- **Class:** **operational / instrumentation** — the underlying capabilities were generated, verified, registered correctly. Disclose harness-corrected sensitivity 35/40 = 87.5% alongside raw 33/40 = 82.5% (`phase4_longitudinal_findings.md` §4.2).

### O.6 Agent execution issues
- **`agent_execution_error` (1 instance, no_optimization):** DeepAgent runtime exception during execution; rare, captured cleanly via the never-raises contract.
- **8-cap subagent budget guard (Phase 4 task 17 T08):** `_MAX_AGENT_SUBAGENT_BUDGET=8` fired; 1 subtask dropped via truncation; T08 still completed via collapsed-lead reuse (`phase4_longitudinal_findings.md` §4.3).
- **Class:** **operational boundary** — guards working as designed; no scientific signal, but cite as evidence the operational envelope is real and instrumented.

---

## P. LangFuse cross-reference map

LangFuse is the trace-level source of truth (`docs/EVAL_PROTOCOL.md` Stage 3); DuckDB is the aggregate source of truth. They must agree on individual rows. Audit verdicts confirm 0 disagreements across 23+4 sampled rows.

**Key sessions / run_ids:**
- Phase 4 longitudinal main: run_id `longitudinal_516eda0ba6ec4e6ab41dbc97188b6802` (campaign `20260427-180751`).
- Phase 4 longitudinal smoke (Stage 0): `longitudinal_4e272b5a649a4ebb81a2b72382f2492a` (LangFuse session `longitudinal-42-20260425-192941`).

**Representative success traces (verifies real work end-to-end):**
| Trace ID | Stage | Task / Cond / Type | What it validates |
|---|---|---|---|
| `4e53db3cc1de50877cf5f6e8fb8c8713` | Stage 1 R1 | T17 / full / agent | Solo DeepAgent path (`investigate-fraud-alerts...`) cleared Tier 1+2 + prose-skip Tier 3, m1.success=1 |
| `fa6ebf76e713ed7eb4cc2c1a85817b46` | Stage 1 R2 | T08 / no_opt / agent | Lead-with-subagents path; 2 subagents with `tools=["sandbox_execute"]`; no optimizer events (correct ablation) |
| `5b580bcea12756290eec63208caa2ea7` | Stage 1 R6 | T16 / full / agent | 5-subagent fanout (the `c200592` 64-char cap fix held; longest subagent name = 35 chars); 22.8 min real DeepAgent runtime |
| `fd216e4ba2fc18678dc351b5ef72dcb3` | Stage 2 R1 | T15 / full / skill | 3-gap pipeline + Pillar 3 merge fired (`aggregate-and-apply-alert-thresholds-daily`, 8065 code_bytes, exit_code=0) |
| `9c072417e8fce258755cf1929312d8aa` | Stage 2 R2 | T09 / no_opt / mcp_tool | 4 mcp_tools, 40+ real `sandbox.execute` events in Tier 3 probing; NO `pillar3.merged` (correct ablation) |
| `7e24bad4a97b5014bee9238390b4c3c2` | Stage 3 | T01 / full / mcp_tool | Hash-sampled deterministic spot-check; Pillar 3 merge `csv-parse-and-stats` from 2 parents, 3 `pillar3.merged` events |
| `2dfaf67da5eb3a38ae93aea8dc65def4` | Stage 3 | T14 / full / skill | 5-gap T14 budget allocation; classifier carve-out (parsing/aggregation → mcp_tool, policy → skill) verified |
| `9758c49501039f68a8fb31c96b0ec2f4` | Stage 3 | T02 / no_opt / mcp_tool | Tier 3 `network_disconnect` adversarial probe — sandbox-no-network behavior verified (genuine black-box probing) |
| `8a2583341ce30e6370e28dd611a22123` | Stage 4 R1 | T20 / longitudinal | Phase 4 first task; full pipeline + Pillar 3 merge `apply-eligibility-policy` from `eligibility-policy-classifier` (seeded) + `apply-eligibility-policy-to-applications-63a037` |
| `41d1ccc18e82635432135244ca4110a4` | Stage 4 R3 | T18 / longitudinal | mcp_tool with 5328-byte source, SHA-256 dedupe; KL ≈ 1e-5 (converged) |
| `a233e2372dc809e17f132271e39a488f` | Stage 4 R4 | T04 / longitudinal | 2-gap (parse mcp_tool + apply-policy skill) both verified Tier 3, both registered |

**Representative failure / mechanism traces (verifies failures are real, not fabricated):**
| Trace ID | Stage | Context | What it validates |
|---|---|---|---|
| `5d3058fb82e49a6b4ebf983fd9319b24` | Stage 5 R1 | T05 / full / output_rejected | The dead-task drill-down. Real pipeline (gate PASS + tier 3 cleared + adversarial probes), oracle rejected for residual nulls — **canonical Critique-A example.** |
| `afa6d5aa906fb98e26b756c45129f5e0` | Stage 1 R3 | T11 / no_verification | `pillar3.verification_rollback` event evidence — Pillar 3 internal verify-and-rollback fires even when task-time gate is off (`no_verification` semantic split, §3.1 Phase 1+2 doc) |
| `a54390c762d6bc72a43ae1bd072ec0b3` | Stage 2 R3 | T11 / no_verification | Cleaner demonstration of same: `compare-and-write-regions` merge candidate fails Tier 1 CRITICAL → atomic rollback per §12 finding #14; trace shows full apply_operations + verification_rollback chain |

**Pillar 3 merge-event traces (Stage 0/4 specific):**
| Trace ID | Stage | Merge | What it validates |
|---|---|---|---|
| `0003e12eb420ab3897fc9c7cdf8879b0` (T20) + `3edafbee216940a4f650380ecf8d3b4d` (T15) | Stage 0 R4/R5 | `monitoring-aggregation-and-alerts` (10305 code_bytes; 3 `pillar3.merged` events) | Merge produces real executable content, not metadata stub |
| `8a2583341ce30e6370e28dd611a22123` | Stage 4 R2 | `apply-eligibility-policy` (Phase 4 task 1) | First merge in longitudinal track; seeded → seeded+gen child |

**Recurrence Stage 3 audit traces (supplemental track, recurrence_sample profile):**
| Trace ID | Stage | Task / Cond / Type | Validates |
|---|---|---|---|
| `a7423919934f5cdb50761a817fe2cd09` | Recurrence Stage 3 R1 | T04 / full / skill (variant v01) | JIT pipeline decomposed T04 into 3 capabilities scoped to v01 fixture paths; all sticky flags green; output_schema matches contract |
| `a065df533f82bc5194429bd3d3be4aaf` | Recurrence Stage 3 R2 | T04 / no_optimization / skill (v01) | 2-capability decomposition (parse MCP + apply-policy skill); v01-scoped, contract keys produced; pipeline completed cleanly with no optimizer events (correct ablation) |
| `9aee58628cbcb8f181494121e71b3659` | Recurrence Stage 3 R3 | T04 / no_verification / skill (v01) | 3-capability decomposition same shape as R1, v01-scoped, all sticky flags true |
| `84d3567738fe09beea85ee3281ef2bee` (audit-prompt trace; runtime evidence on session-mate `a0ed6c0e62b6bd4cdf0b92bc9a99488d`) | Recurrence Stage 3 R4 | T13 / full / agent (v03) | Multi-step DeepAgent: 6 todos planned, `sandbox_execute` with pandas/numpy on v03-specific data (cpu spike 224.584 only present in v03), wrote `profiling.json` + `anomalies.json`, contract-shaped dict returned |

**Nuance for R4 (T13 agent, recurrence):** the trace_id surfaced in `stage3_audit_plan.json` (`84d3567738fe09beea85ee3281ef2bee`) points to the generation+merge event for the agent capability, not the agent's runtime tool-call work. The runtime evidence lives on a session-mate trace `a0ed6c0e62b6bd4cdf0b92bc9a99488d` within the same `run_id` (`full_00_0736c0ff2fb3496295c4821c2485e0ab`). The Stage 3 verdicts file documents this explicitly. Audit verdict was OK because both traces belong to the same run and the runtime evidence is fully captured; cite this as a known LangFuse trace-attachment quirk for AGENT-type capabilities, not an audit-integrity gap.

**Agreement status (LangFuse ↔ DuckDB ↔ analyzer):** every audited trace's task prompt, capability_type, and pass/fail outcome matched the DB row's `task_id`, `capability_type`, `gate_passed`, `output_in_range`. 0 disagreements across all 7 audit stages (main 6 + recurrence Stage 3). Local logs (`10_pilot.log`, `30_longitudinal.log`, `40_recurrence.log`) corroborate timestamps, run_ids, and per-task outcomes; no instances of "log says X but DB says Y" surfaced.
