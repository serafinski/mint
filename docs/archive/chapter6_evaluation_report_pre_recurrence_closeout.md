# Chapter 6 — Evaluation Report (thesis-ready draft)

**Status:** v1, drawn from `docs/chapter6_fact_pack.md` (companion structural doc) and `docs/chapter6_numbers.md` (frozen v1 numbers); cross-checked against primary artifacts in `docs/eval_runs/20260425/` (Phase 1 + Phase 4) and `docs/eval_runs/20260425_recurrence/` (Phase 3b).
**Protocol:** `docs/EVAL_PROTOCOL.md` (2026-04-22).
**Branch / HEAD:** `iter/6-execution-backends` @ `2724363` (Stages 1-verify → 5); `1cf9de7` for Stage 0 / Stage 1 launch (test-only diffs; see §9).

---

## 1. Executive summary

The evaluation campaign tested the proposal's three pillars (JIT capability pipeline, three-tier verification, granularity optimizer) across three workload regimes:

- **Phase 1 — cold-start breadth (Stage 1+2 primary):** 4 conditions × 3 iters × 20 tasks = 240 records.
- **Phase 3b — recurrence supplemental:** 4 conditions × 3 iters × 6 tasks × 3 fixture variants = 216 records.
- **Phase 4 — longitudinal warm-registry (Stage 4 primary for M6):** 1 run × 40 tasks.

**Trustworthiness verdict: the campaign produces evidence strong enough to cite in the thesis on M1, M3, M5, ClassAcc, and M6.** All five headline metrics passed two-source agreement (analyzer vs SQL/SciPy) with `n_disagreements: 0`. Six main-track audit stages (Stage 0+1+2+3+4+5) sampled 27 rows in total — 27 OK / 0 SUSPICIOUS / 0 FAIL across LangFuse-trace-level inspection — confirming pipeline behaviour matches DB labels at the row level. The supplemental recurrence track adds 4 more audited rows (recurrence Stage 3, all OK), bringing the all-tracks total to 31/31/0/0.

**Stages that completed cleanly:** Stage 0 (smoke), Stage 1 (main eval shape), Stage 2 (M1/M3/M5/ClassAcc with agreement), Stage 3 (deterministic spotcheck), Stage 4 (M6 with agreement), Stage 5 (plausibility drill-down). Recurrence Stage 1 (shape PASS) and recurrence Stage 3 (shape + audit PASS, finalized 2026-04-28) are wrapper-complete on the supplemental track.

**Stage qualified as supplemental:** Phase 3b recurrence — wrapper-finalized (Stage 1 PASS + Stage 3 PASS, shape gate clean, 4-row LangFuse audit clean). The 216-record run completed end-to-end and the protocol wrapper closeout is now complete. Recurrence remains supplemental for two protocol-level reasons: it is not the primary breadth track per `EVAL_PROTOCOL.md` §1/§2, and the recurrence DB has no Stage-2-style two-source agreement file. Cited as cross-phase trade-off support only (see §5).

**The thesis can defensibly claim:**
- Pillar 1 contributes a 27.5pp synthesis-task lift over the no-JIT control (full 80.4% vs static 52.9%), with operational classification accuracy 93.5%.
- Pillar 2 narrows cross-iter variance from 6.1pp (no_verification) to 2.4pp (full) at cold start; gate pass 100% in full; zero unverified-leak failures across 240 cold-start records.
- Pillar 3 mechanism integrity is verified across all four merge variants, with utility-score KL convergence at cycle 4 and seeded→evolved Gini delta +0.063 over 40 longitudinal tasks.

The evidence does **not** demonstrate Pillar-2 mean-success lift at N=60 (means tied with `no_verification`) or Pillar-3 task-success lift over the same horizon (M1 effect within CI overlap). Both are disclosed as scope-of-evidence limitations rather than null results — Pillar 2's value at this scale is failure-kind variance reduction, not mean lift; Pillar 3's task-success amortization needs more cycles than 60 tasks afford.

---

## 2. Protocol compliance and execution status

### 2.1 Marker-status matrix (built from actual marker files)

| Stage | Track | Marker | Verdict line | Audit-verdicts file | Sample size |
|---|---|---|---|---|---|
| 0 (smoke / preflight) | main | `stage0.marker` PASS (2026-04-25T18:09:54Z) | "shape OK + audit verdicts clean" | `stage0_audit_verdicts.md` | 5 OK / 0 / 0 |
| 1 (main_sample, pass 1) | main | `stage1.marker` PASS (2026-04-26T12:53:17Z) | "shape + audit clean" | `stage1_audit_verdicts.md` | 7 OK / 0 / 0 |
| 2 (main_sample, pass 2) | main | `stage2.marker` PASS (2026-04-26T12:54:28Z) | "agreement clean; optional main_sample audit request generated" | `stage2_audit_verdicts.md` | 7 OK / 0 / 0 |
| 3 (deterministic spot-check) | main | (no marker file by design) | embedded in `stage3_spotcheck.md` | `stage3_spotcheck.md` | 3 OK / 0 / 0 |
| 4 (longitudinal_sample) | main | `stage4.marker` PASS (2026-04-27T20:41:47Z) | "shape + audit clean" | `stage4_audit_verdicts.md` | 4 OK / 0 / 0 |
| 5 (plausibility_flagged) | main | `stage5.marker` PASS (2026-04-27T20:50:11Z) | "priors + audit clean" | `stage5_audit_verdicts.md` | 1 OK / 0 / 0 |
| 1 (recurrence) | recurrence | `stage1.marker` PASS (2026-04-26T12:53:17Z) | "shape + audit clean" | (cold-start audit reused) | — |
| 3 (recurrence) | recurrence | `stage3.marker` PASS (2026-04-28T20:14:12Z) | "shape + audit clean" | `stage3_audit_verdicts.md` | 4 OK / 0 / 0 |

**Distinction enforced:** every "PASS" marker carries a verdict reason describing what passed (shape, agreement, audit), not just shape. Recurrence Stage 3 finalized to PASS on 2026-04-28 with shape gate clean (`43_stage3_gate.json`: 216/216, no problems) and a 4-row LangFuse audit (recurrence_sample profile) returning 4 OK / 0 SUSPICIOUS / 0 FAIL. The 216-record run is operationally complete (`40_recurrence.log` confirms `Total recurrence records: 216`) and the protocol wrapper closeout is now complete. Reported as supplemental for protocol-role reasons, not for incomplete wrapper finalization.

**SHAPE_OK ≠ PASS rule:** none of the markers above carry a `SHAPE_OK` label; every PASS includes a verdict reason.

### 2.2 Two-source agreement (the protocol's primary correctness mechanism)

| Headline scope | Source-1 (analyzer) | Source-2 (independent cross-check) | Agreement file | Disagreements |
|---|---|---|---|---|
| Stage 2 M1 / M3 / M5 / ClassAcc | `20_analyzer_metrics.json` | `21_sql_crosscheck.json` (hand-written SQL) | `22_agreement_summary.json` | **0** |
| Stage 4 M6 (utility-KL series + convergence) | `33_analyzer_m6.json` | `32_stage4_gate.json::M6_scipy_kl` (independent SciPy) | `34_stage4_agreement.json` | **0** |

Both agreement files are present and machine-readable; both report empty `details: []`.

### 2.3 Audit integrity

Six main-track audit stages, 27 sampled rows total, **27 OK / 0 SUSPICIOUS / 0 FAIL**. Including the supplemental recurrence Stage 3 (4 rows, 4 OK / 0 / 0), the all-tracks total is **31/31/0/0**. Sampling axes covered: random + condition-stratified (Stage 1, 2), deterministic hash-sample (main Stage 3), longitudinal task + Pillar-3-event (Stage 4), plausibility-flagged (Stage 5), preflight smoke (Stage 0), recurrence variant audit (recurrence Stage 3). The closeout doc `stage5_dead_task_findings.md` quotes 23/5-stages because its scope was the cold-start campaign only; this report cites the main-track 27/27/0/0 as the headline figure and the all-tracks 31/31/0/0 when the supplemental recurrence track is included (resolution in `chapter6_fact_pack.md` §B and `chapter6_numbers.md` §5).

### 2.4 Stages-by-classification (per protocol §1)

- **Fully passed (cite as primary):** Stage 1 (shape), Stage 2 (M1/M3/M5/ClassAcc), Stage 4 (M6).
- **Audit-qualified (trace-level integrity):** Stage 0, Stage 3 (main), Stage 5.
- **Supplemental (wrapper-finalized but not headline):** Phase 3b recurrence (Stage 1 PASS, Stage 3 PASS as of 2026-04-28 — shape gate + LangFuse audit both clean). Cited for cross-phase trade-off only because (a) recurrence is not the primary breadth track per protocol, and (b) recurrence has no Stage-2-style two-source agreement file for headline numbers.
- **No incomplete stages on either track.**

---

## 3. Chapter 6 number ledger

All numbers below are recorded in `docs/chapter6_numbers.md` v1 (frozen 2026-04-27). Re-running the campaign requires versioning to v2; the table below cites v1.

### 3.1 Stage 2 metrics (Phase 1, N=240; ClassAcc denominator n=170)

| Metric | Final value | Source-1 (analyzer) | Source-2 (cross-check) | Agreement | Recorded in numbers? |
|---|---|---|---|---|---|
| **M1 full** | 50/60 = 83.33% | `20_analyzer_metrics.json::M1.full` | `21_sql_crosscheck.json::M1.full` | ✅ 0 disagreements | ✅ §1.2 |
| **M1 no_optimization** | 49/60 = 81.67% | `20_analyzer_metrics.json::M1.no_optimization` | `21_sql_crosscheck.json::M1.no_optimization` | ✅ | ✅ §1.2 |
| **M1 no_verification** | 50/60 = 83.33% | `20_analyzer_metrics.json::M1.no_verification` | `21_sql_crosscheck.json::M1.no_verification` | ✅ | ✅ §1.2 |
| **M1 static** | 33/60 = 55.00% | `20_analyzer_metrics.json::M1.static` | `21_sql_crosscheck.json::M1.static` | ✅ | ✅ §1.2 |
| **M3 aggregate (pooled, with caveat)** | 0.10183 | `20_analyzer_metrics.json::M3.aggregate` | `21_sql_crosscheck.json::M3.aggregate` | ✅ within 1e-12 | ✅ §1.3 |
| **M3 per-run mean (preferred citation)** | full 0.667 / no_opt 0.663 / no_verif 0.664 / static 0.000 | `20_analyzer.log` | (single source, log-derived) | n/a | ✅ §1.3 disclosure |
| **ClassAcc aggregate** | 93.53% (n=170) | `20_analyzer_metrics.json::ClassAcc.aggregate_pct` | `21_sql_crosscheck.json::ClassAcc.aggregate_pct` | ✅ | ✅ §1.1 |
| **ClassAcc per-condition** | full 92.98 / no_opt 94.74 / no_verif 92.86 | `20_analyzer_metrics.json::ClassAcc.per_condition_pct` | `21_sql_crosscheck.json::ClassAcc.per_condition_pct` | ✅ | ✅ §1.1 |
| **M5 median (s)** | full 283.99 / no_opt 198.96 / no_verif 175.71 / static 0.001 | `20_analyzer_metrics.json::M5.{cond}.median_sec` | `21_sql_crosscheck.json::M5.{cond}.median_sec` | ✅ | ✅ §1.4 |
| **M5 p95 (s)** | full 916.02 / no_opt 534.81 / no_verif 471.89 / static 0.0068 | `20_analyzer_metrics.json::M5.{cond}.p95_sec` | `21_sql_crosscheck.json::M5.{cond}.p95_sec` | ✅ | ✅ §1.4 |

### 3.2 Stage 4 metrics (Phase 4, N=40)

| Metric | Final value | Source-1 (analyzer-of-record) | Source-2 (independent SciPy) | Agreement | Recorded? |
|---|---|---|---|---|---|
| **M6 convergence cycle** | 4 | `33_analyzer_m6.json::convergence_cycle` (utility-score KL, threshold 0.05, min consecutive 3) | `32_stage4_gate.json::M6_scipy_kl` (independent SciPy reconstruction; strictly no fallback) | ✅ 0 disagreements | ✅ §2.2 |
| **M6 series (per-cycle kl_utility)** | 40 rows | `33_analyzer_m6.json::rows[*].kl_utility` | `32_stage4_gate.json::M6_scipy_kl[*].kl_utility` | ✅ to 3 sig figs | ✅ §2.2 |
| Stage 4 gate shape | 4B=4C=40 cycles, no empty cycles, utility n=1970/1970 | `32_stage4_gate.json` | n/a (gate is its own check) | shape PASS | ✅ §2.1 |
| Pillar 3 final cumulative Gini | 0.063 | `33_analyze_longitudinal.log` | (single-source log) | n/a | ✅ §2.3 |
| Pillar 3 seeded→evolved Gini delta | +0.063 (RQ #1) | `33_analyze_longitudinal.log` | n/a | n/a | ✅ §2.3 |
| Per-type op counts (cycle_events) | merged 21 (first cycle 1, last cycle 28); 0 split / 0 prune / 0 verification_rollback | `33_analyze_longitudinal.log` + DB query | `cycle_events` table directly inspected | ✅ identical | ✅ §2.3 |
| **Phase 4 harness M1** | 33/40 = 82.5% (raw) / 35/40 = 87.5% (harness-corrected sensitivity) | `31_longitudinal_status.txt` | `data/longitudinal_optimizer_log.json` (40-entry mirror) | ✅ identical | ✅ §2.4 |

### 3.3 Phase 3b recurrence (supplemental — NOT in headline ledger)

| Condition | M1 | Source | Two-source check? |
|---|---|---|---|
| full | 48/54 = 88.9% | `phase3b_findings.md` §1 + DB `metrics_recurrence_20260425_recurrence.duckdb::evaluation_runs` | analyzer-derived only; no Stage-2-style agreement file generated for the recurrence track |
| no_verification | 50/54 = 92.6% | same | same |
| no_optimization | 50/54 = 92.6% | same | same |
| static | 54/54 = 100.0% (sampling-bias artifact) | same | same |

Recorded in `chapter6_numbers.md` §3 as supplemental (clearly demarcated; not promoted to primary ledger).

---

## 4. Breadth results interpretation (Phase 1 cold-start)

### 4.1 Condition-level comparison

The three JIT conditions cluster: full = no_verification = 83.33%, no_optimization = 81.67%. Pairwise Fisher's exact (Holm–Bonferroni adjusted) returns `p_adj = 1.00` for all three pairs — **statistically a cluster, not a ranking.** Static = 55.0%.

The synthesis subset (T04–T20) sharpens the contrast: full 80.4% vs static 52.9% = **27.5pp gap** (Pillar 1's citable contribution). The retrieval subset (T01–T03) is JIT-100% vs static-66.7%, confirming even hand-curated seeded coverage is incomplete and JIT generation is doing real work.

The pooled-JIT vs static p<0.001 is **not cited** — pooling non-exchangeable ablations inflates N by 3× (Chapter 4.5 disclosure).

### 4.2 Task-family behaviour (full condition, 3 iters per task)

From `40_plausibility.json::info.per_task_full`:

- **17/20 tasks succeed 3/3.** Operationally robust.
- **T05 = 0/3** — the campaign's only Phase-1-full dead-task. Drove the Stage 5 INVESTIGATE flag; Stage 5 trace `5d3058fb...` confirms real pipeline executed end-to-end with gate=PASS but oracle rejected for residual nulls. Pure LLM-correctness defect.
- **T07 = 1/3, T10 = 1/3** — single LLM-correctness defect modes (fuzzy join output rejected; forecast quality).
- **T15 = 2/3, T16 = 2/3, T20 = 2/3** — single-iter LLM stochasticity at the cluster boundary.

Capability-type distribution in full-success rows: agent 15 / mcp_tool 19 / skill 16. **Balanced spread across all three execution backends** — no type dominates the success bucket; the three-backend architecture is exercised in production-equivalent proportions.

### 4.3 Run-index trend (variance, full condition)

Iter 1 → 0.85, iter 2 → 0.80, iter 3 → 0.85. σ ≈ 2.4pp across iters. `phase1_2_findings.md` §2.2 reports the no_verification σ ≈ 6.1pp on the same 3 iters: **verification narrows the failure-kind distribution even when it does not lift the mean.**

### 4.4 What the breadth results demonstrate

- **Pillar 1 contributes a real, large, citable mean lift** at cold start (27.5pp on synthesis tasks).
- **Pillar 2 stabilises outcomes** (σ 2.4pp vs 6.1pp; zero unverified-leak failures across 240 records).
- **Classification accuracy is high** (93.5% aggregate) and proves the per-gap classifier routes capability types correctly — the three-backend architecture's runtime consequence is measurable.

### 4.5 What the breadth results do NOT demonstrate

- **No mean-success lift from Pillar 2 alone at N=60.** Means tied with no_verification; reframe as failure-kind variance reduction, not mean lift.
- **No mean-success lift from Pillar 3 alone at N=60.** full vs no_optimization +1.7pp inside CI overlap; mechanism integrity verified separately (27 merges in full, 21 merges + 4 atomic rollbacks in no_verification — `cycle_events` aggregates).
- **No statistical claim about ranking among the JIT cluster.** Holm–Bonferroni explicitly forbids it.
- **No claim against deferred metrics M2 / M4 / T3 compression-loss** — all three deferred to Chapter 7 per `EVAL_PROTOCOL.md` §1.

---

## 5. Recurrence interpretation (Phase 3b supplemental)

**What recurrence contributes:** evidence that under warm-registry repeated-task pressure the JIT cluster *inverts* — the ablations beat the full configuration by ~3.7pp:

- full = 48/54 = 88.9%
- no_verification = 50/54 = 92.6%
- no_optimization = 50/54 = 92.6%

This is not a contradiction of the cold-start result. It is the **cost-side** of the proposal's autonomy/correctness/economy trade-off: Tier 3 wallclock (~5–10 min per generated artifact) and Pillar 3 reuse-drift (§3.6 sub-types A/B/C; `phase3b_findings.md` §4.2) cost the mean when warm-registry reuse is the dominant path.

**Protocol qualification.** The recurrence track is wrapper-finalized: `stage1.marker = PASS` (2026-04-26T12:53:17Z) and `stage3.marker = PASS` (2026-04-28T20:14:12Z, "shape + audit clean") with shape gate `43_stage3_gate.json` clean (216/216, no problems) and a 4-row LangFuse audit returning 4 OK / 0 / 0 (`stage3_audit_verdicts.md`). A frozen recurrence snapshot exists at `docs/eval_runs/20260425_recurrence/42_recurrence.duckdb`. However, **no two-source agreement file exists for recurrence headline numbers** — they were derived analyzer-side from the recurrence DB without an independent SQL cross-check on the recurrence track.

**Therefore recurrence is cited as supplemental only.** It supports the workload-conditional framing of the trade-off; it does not stand alone as a primary headline. The remaining limitation is the absence of Stage-2-style two-source agreement on recurrence headline numbers, *not* incomplete wrapper finalization. The static = 100% in this track is a **sampling-bias artifact** (the recurrence pool was deliberately drawn to overlap seeded coverage; the real no-JIT floor is Phase 1 static = 55.0%).

---

## 6. Longitudinal interpretation (Phase 4)

**What Stage 4 tested.** A single 40-task longitudinal-warm run over a two-pass shuffled sequence of T01–T20 (one condition: longitudinal-warm). No inter-task registry reset — exactly the regime cold-start (Phase 1) and intra-iter recurrence (Phase 3b) cannot probe.

**40-cycle campaign at a glance.** Run id `longitudinal_516eda0ba6ec4e6ab41dbc97188b6802`. Wallclock 4.4h. Active capabilities trajectory 14 → 76 (harness count). 7 Pillar 3 merges across all four merge variants (SKILL ×3, MCP_TOOL ×3, AGENT ×1 — first AGENT merge captured in any phase); 21 merge ops persisted in `cycle_events`; 0 rollbacks, 0 splits, 0 prunes in this 40-task warm run.

**Convergence vs raw task-success — separate the two signals.**

- **M6 (utility-score KL convergence): cycle 4.** Source-1 (`33_analyzer_m6.json::convergence_cycle`) and Source-2 (independent SciPy reconstruction in `32_stage4_gate.json::M6_scipy_kl`) agree, with `34_stage4_agreement.json` reporting 0 disagreements. The analyzer reads strictly from `cycle_utility_scores` with no fallback to invocation counts (defends finding #124). **M6 is trustworthy.**
- **Raw task success: 33/40 = 82.5%, flat trajectory** (first 10 = 80%, last 10 = 80%, Δ +0pp). Inside the Phase 1 JIT cluster band 81.67–83.33%. The mean is *stable*, not improving; the structural signal (cumulative merges, 2-tier chains, registry reuse) is where Pillar 3's evolution lives.

**Substantive meaning of convergence at cycle 4.** After three consecutive cycles below the 0.05 KL threshold, the registry's utility-score distribution is stable in proposal-literal terms: novel cycles continue to add capabilities (active-caps grow monotonically), but the relative utility shape stops shifting. This is the proposal's RQ #2 signal — registry convergence — answered with both the analyzer-of-record value and an independent SciPy cross-check.

**Pillar 3 RQ #1 (seeded → evolved Gini): delta +0.063.** The registry becomes more skewed during evolution, consistent with the "few capabilities carry more traffic" hypothesis. Magnitude is small because the 40-cycle horizon is short; framed as qualitative directional evidence, not effect-size quantification.

**Two harness validation-path findings (NOT pipeline failures).** T06 task 29 — solo registry-match drops `upstream_data` kwarg (harness invocation bug). T10 task 34 — AGENT registry-match has no `source_code` in metadata (harness measurement gap). Both fixes are post-eval scope. Disclose harness-corrected sensitivity 35/40 = 87.5% alongside raw 33/40 = 82.5%.

---

## 7. Failure analysis (organized by pattern)

Total Phase 1 failures = 58/240. Per-condition × failure_reason from direct DB query against `evaluation_runs`:

### 7.1 `generation_failed` (28; mostly structural)

- **Static: 27** — every static failure on a synthesis task with no seeded capability covering it. Deterministic (run is exact-reproducible across iters); hits T03/T06/T07/T08/T10/T12/T16/T17/T18 in every iter. **Not an LLM-quality finding** — proves seeded coverage is intentionally narrow so static is a real no-JIT floor, not a 0/0 sham.
- **no_verification: 1** — strict-gate exhaustion via §12 finding #15 fail-closed. Mechanism evidence; no Pillar 2 leak.
- **Class:** structural / instrumentation. **No scientific signal beyond "the static control is honest."**

### 7.2 `output_rejected` (23; THE largest LLM-quality bucket)

- **full: 8** (T05 ×3, T07 ×2, T10 ×2, T15 ×1) — all are gate=PASS but oracle=FAIL.
- **no_optimization: 9** (T05 ×3, T07 ×2, T10 ×1, T15 ×1, T20 ×2).
- **no_verification: 6** — same pattern.
- **Class:** scientifically meaningful. **The canonical Critique-A surface.** Verification gate cannot catch domain-correctness defects by design — its taxonomy probes mocks, hardcoding, silent fallbacks, graceful degradation, not "the cleaner missed `'NA'` as a null sentinel." T05 is the textbook example (Stage 5 trace `5d3058fb...`); cite once in §6.3.

### 7.3 `chain_step_failed` (5; downstream coupling)

- **full: 2; no_verification: 3.**
- **Class:** scientifically meaningful — §3.6 sub-A cascades (value-shape drift across chained capabilities). Verification clears each artifact in isolation; inter-step compatibility is a registry-reuse-time property the gate does not assert.

### 7.4 Registry / schema mismatch (cascade visibility)

- **Phase 1 ShapeMismatch counter (`evaluation_runs.output_shape_mismatch`):** full 2/60, no_optimization 1/60, no_verification 0/60, static 0/60. **Optimizer-on conditions produce shape mismatches; optimizer-off do not.** Pillar 3 boundary effect (`phase1_2_findings.md` §3.2).
- **Phase 3b §3.6 sub-types A/B/C** (recurrence supplemental): sub-A value-shape drift (`aggregate_transactions_by_department_a4a51f`), sub-B cached-broken-tool (`parse_eligibility_policy_json_and_applic_91434c` × 3 regenerations), sub-C cross-step name reuse.
- **Phase 4 cascades:** task 21 T15 (sub-B pandas 2.x `infer_datetime_format`), task 22 T09 (sub-A stats-shape mismatch via cached pass-1 outlier detector).
- **Class:** scientifically meaningful. Chapter 7 future work — value-shape comparator at registry-reuse time.

### 7.5 Validation / metadata failures (operational)

- **`tier3` (1, no_optimization):** Pillar 3-internal `verify_and_register_or_rollback` rejecting a merged child whose Tier 1/2/3 gate failed. **Not a Pillar 2 leak.**
- **Phase 4 harness findings (NOT in `failure_reason`):** T06 task 29 solo registry-match `upstream_data` drop; T10 task 34 AGENT registry-match `source_code` absence. Underlying capabilities were generated, verified, registered correctly.
- **Class:** operational / instrumentation. Disclose; do not over-interpret as scientific findings.

### 7.6 Agent execution issues (operational boundaries)

- **`agent_execution_error` (1, no_optimization):** DeepAgent runtime exception, captured cleanly via never-raises contract.
- **8-cap subagent budget guard (Phase 4 task 17 T08):** `_MAX_AGENT_SUBAGENT_BUDGET=8` fired; 1 subtask truncation-dropped; T08 still completed via collapsed-lead reuse.
- **Class:** operational envelope evidence. Guards working as designed; cite as instrumentation maturity, not scientific finding.

---

## 8. LangFuse-backed validation

LangFuse is the trace-level source of truth (`EVAL_PROTOCOL.md` Stage 3); DuckDB is the aggregate source of truth. Across 6 main-track audit stages and 27 sampled rows, **0 disagreements** between LangFuse trace content and DB labels. Including the supplemental recurrence Stage 3 audit (4 rows), the all-tracks total is 31 sampled / 0 disagreements.

### 8.1 Key sessions / run_ids

- Phase 4 main: `longitudinal_516eda0ba6ec4e6ab41dbc97188b6802` (campaign `20260427-180751`).
- Phase 4 smoke (Stage 0): `longitudinal_4e272b5a649a4ebb81a2b72382f2492a` (LangFuse session `longitudinal-42-20260425-192941`).
- Phase 1 pilot: per-iter run_ids (e.g. `full_00_1a91ca6409ff4a60a17fa7534a73e7fc`, `no_verification_01_a96eaf43f68848099b44507ec0f0bf89`) recorded on `evaluation_runs.langfuse_trace_id` — every audit row was opened in the LangFuse UI.

### 8.2 Representative success traces

| Trace ID | Stage | Task / Cond / Type | Validates |
|---|---|---|---|
| `4e53db3cc1de50877cf5f6e8fb8c8713` | 1 R1 | T17 / full / agent | Solo DeepAgent path — Tier 1+2 + prose-skip Tier 3, full pipeline executed |
| `fa6ebf76e713ed7eb4cc2c1a85817b46` | 1 R2 | T08 / no_opt / agent | Lead-with-subagents, 2 subagents, no optimizer events (correct ablation) |
| `5b580bcea12756290eec63208caa2ea7` | 1 R6 | T16 / full / agent | 5-subagent fanout, 64-char-cap fix held, 22.8 min real DeepAgent runtime |
| `fd216e4ba2fc18678dc351b5ef72dcb3` | 2 R1 | T15 / full / skill | Pillar 3 merge `aggregate-and-apply-alert-thresholds-daily` (8065 code_bytes, exit_code=0) |
| `9c072417e8fce258755cf1929312d8aa` | 2 R2 | T09 / no_opt / mcp_tool | 4 mcp_tools, 40+ real `sandbox.execute` events; NO `pillar3.merged` (correct ablation) |
| `7e24bad4a97b5014bee9238390b4c3c2` | 3 | T01 / full / mcp_tool | Hash-sampled spotcheck; merge `csv-parse-and-stats` from 2 parents |
| `2dfaf67da5eb3a38ae93aea8dc65def4` | 3 | T14 / full / skill | 5-gap budget allocation; classifier carve-out (parsing→mcp_tool, policy→skill) |
| `9758c49501039f68a8fb31c96b0ec2f4` | 3 | T02 / no_opt / mcp_tool | Tier 3 `network_disconnect` adversarial probe — sandbox-no-network behavior verified |
| `8a2583341ce30e6370e28dd611a22123` | 4 R1+R2 | T20 / longitudinal | Phase 4 first task; first Pillar 3 merge `apply-eligibility-policy` |
| `41d1ccc18e82635432135244ca4110a4` | 4 R3 | T18 / longitudinal | 5328-byte SHA-256 dedupe mcp_tool; KL ≈ 1e-5 (converged) |
| `a233e2372dc809e17f132271e39a488f` | 4 R4 | T04 / longitudinal | 2-gap parse+policy chain, both verified Tier 3 |

### 8.3 Representative failure / mechanism traces

| Trace ID | Stage | Context | Validates |
|---|---|---|---|
| `5d3058fb82e49a6b4ebf983fd9319b24` | 5 | T05 / full / output_rejected | The dead-task drill-down. Real pipeline executed; oracle rejected for residual nulls. **Canonical Critique-A example.** |
| `afa6d5aa906fb98e26b756c45129f5e0` | 1 R3 | T11 / no_verification | `pillar3.verification_rollback` — Pillar 3-internal verify-and-rollback fires even when task-time gate is off (semantic split) |
| `a54390c762d6bc72a43ae1bd072ec0b3` | 2 R3 | T11 / no_verification | `compare-and-write-regions` merge candidate fails Tier 1 CRITICAL → atomic rollback per §12 finding #14 |

### 8.4 Recurrence Stage 3 audit (supplemental track)

Recurrence Stage 3 was wrapper-finalized 2026-04-28 with a 4-row LangFuse audit on the recurrence_sample profile (3× T04 happy across full / no_optimization / no_verification on variant v01 + 1× T13 agent on variant v03 in full). All four rows verdict OK (`stage3_audit_verdicts.md`). Notable evidence:

| Trace ID | Task / Cond / Variant | Validates |
|---|---|---|
| `a7423919934f5cdb50761a817fe2cd09` | T04 / full / skill (v01) | JIT pipeline decomposed T04 into 3 capabilities scoped to v01 fixture paths; sticky flags green |
| `a065df533f82bc5194429bd3d3be4aaf` | T04 / no_optimization / skill (v01) | 2-cap decomposition; no optimizer events (correct ablation) |
| `9aee58628cbcb8f181494121e71b3659` | T04 / no_verification / skill (v01) | 3-cap decomposition same shape as full; sticky flags true |
| `84d3567738fe09beea85ee3281ef2bee` (audit-prompt; runtime evidence on session-mate `a0ed6c0e62b6bd4cdf0b92bc9a99488d`) | T13 / full / agent (v03) | 6-todo plan; `sandbox_execute` with pandas/numpy on v03-specific data (cpu spike 224.584 only present in v03); wrote `profiling.json` + `anomalies.json`; contract-shaped dict returned |

**Trace-attachment quirk for the AGENT case (R4).** The trace_id surfaced in `stage3_audit_plan.json` (`84d3567738fe09beea85ee3281ef2bee`) points to the generation+merge event for the agent capability, not the runtime tool-call work. The runtime evidence lives on a session-mate trace `a0ed6c0e62b6bd4cdf0b92bc9a99488d` within the same `run_id`. The verdicts file documents this explicitly. The audit was OK because both traces belong to the same run and the runtime work is fully evidenced; cite as a known LangFuse trace-attachment quirk for AGENT-type capabilities, not an audit-integrity gap.

### 8.5 Trace coverage limitations (explicit)

- **Static condition has no LangFuse traces.** Stage 3 deliberately filters `condition != 'static' AND langfuse_trace_id IS NOT NULL`. Static is a no-LLM control; trace-level audit does not apply.
- **Pillar 3 merge events are sometimes piggy-backed on the host task's trace** rather than a dedicated trace ID. Stage 0 R4/R5 audit captures this explicitly: the `audit_key` is the same for both rows because they reference the same merge from parent and child sides. The same quirk surfaced on the recurrence Stage 3 R4 AGENT row (§8.4).
- **Recurrence trace coverage is supplemental scope.** The 4-row recurrence Stage 3 audit (§8.4) provides trace-level evidence for the recurrence track but is smaller than the main-track sampling and does not include a Stage-2-style two-source agreement on aggregate metrics. Recurrence numbers therefore cite trace-backed integrity but remain supplemental (§5).

---

## 9. Validity, trustworthiness, and limitations

### 9.1 Metric integrity (high)

Stage 2 and Stage 4 each have machine-readable analyzer + cross-check + agreement files; both pairs agree with `n_disagreements: 0`. Two-source agreement is the protocol's primary correctness mechanism (`EVAL_PROTOCOL.md` §1 rule 2). M1, M3, ClassAcc, M5 are trustworthy from Phase 1 cold-start; M6 is trustworthy from Phase 4 longitudinal.

### 9.2 Runtime behaviour integrity (high)

LangFuse traces corroborate DB labels on 27/27 audited rows (4 sampling axes plus smoke). Local logs (`10_pilot.log`, `30_longitudinal.log`, `40_recurrence.log`) corroborate timestamps, run_ids, and per-task outcomes. `cycle_events` aggregates from direct DB query match the narrative (`full → merged ×27`, `no_verification → merged ×21 + verification_rollback ×4`, `no_optimization → 0`, `static → 0` in Phase 1; `merged ×21` in Phase 4).

### 9.3 Protocol completeness (high)

Six main-track stages produced PASS markers with verdict reasons. The recurrence track is also wrapper-finalized: Stage 1 PASS (2026-04-26) and Stage 3 PASS (2026-04-28, "shape + audit clean"). The remaining limitation on recurrence is that its headline numbers **do not have a Stage-2-style two-source agreement file** — they were derived analyzer-side without an independent SQL cross-check on the recurrence DB. Recurrence is therefore reported as supplemental for protocol-role and two-source-agreement reasons, not for incomplete wrapper finalization.

### 9.4 External validity / experimental limitations

- **N=60 per condition** in Phase 1. Small enough that ~3.7pp shifts (Phase 3b inversion direction; Pillar 3 mean-effect) sit inside CI overlap. The SCALE chosen lets us argue mechanism integrity and variance, not effect-size quantification at the mean.
- **Stage 0 / Stage 1 launch executed at HEAD `1cf9de7`** with WIP modifications to harness, skill_generator, sandbox_tool, and several test files; from Stage 1 verify onward consolidated to `2724363`. The diffs were test-only and harness instrumentation (no behaviour changes to Pillar 1/2/3 code paths). Disclose once in Chapter 4.5.
- **Three uncommitted xlsx fixtures** (`tests/evaluation/fixtures_recurrence/T11/v0{1,2,3}/t11_products.xlsx`) appear in working-tree from Stage 4 launch onward and remain uncommitted at campaign closeout.
- **Tier 3 capability-type coverage gap:** prose-only SKILL/AGENT artifacts are not adversarially probed because the sandbox payload requires inlined source. Disclose; not a methodology substitution (the agent does craft inputs and execute via `sandbox_execute`), but a coverage gap.
- **Tier 1.5 sandbox import probe is a cost-optimization, not a fourth methodological tier** — disclose in Chapter 4.5 per master plan Step 12.5.

### 9.5 What the evidence supports vs. what it does not

The evidence supports claims of: (a) a large cold-start mean lift driven by Pillar 1; (b) variance reduction from Pillar 2 (failure-kind diversity narrows; cross-iter σ narrows); (c) Pillar 3 mechanism integrity (merges, atomic rollbacks, name-collision guards, cumulative chains, four-variant coverage); (d) longitudinal utility-score convergence at cycle 4 with proposal-literal KL.

The evidence does **not** support claims that (a) Pillar 2 lifts the mean at this N; (b) Pillar 3 lifts task success at this N; (c) recurrence inversion is statistically certain (N=54 per condition, no two-source agreement); (d) Pillar 1 registry rescue is a frequency-quantified mechanism (single-instance qualitative observation in Phase 4 task 33 T05).

Under N=60 the system is **stable but not measurably more accurate** at the mean for Pillars 2+3; that is exactly what the evidence shows and exactly how the chapter must frame it.

---

## 10. Chapter-ready conclusion bullets

Findings the chapter can defensibly assert:

- **Pillar 1 contributes a 27.5pp synthesis-task lift** (full 80.4% vs static 52.9%, Phase 1, N=51 per condition on synthesis subset). Operational classification accuracy 93.5% aggregate (n=170). Two-source agreement clean.
- **Pillar 2 narrows cross-iter variance from 6.1pp to 2.4pp** at cold start (Phase 1, full vs no_verification). Zero unverified-leak failures across 240 records. Mean lift over no_verification at N=60 is below detection — Pillar 2's value is failure-kind variance reduction, not mean lift.
- **Pillar 3 mechanism integrity is verified across all four merge variants** (SKILL ×3, MCP_TOOL ×3, AGENT ×1 in Phase 4; 27 merges in Phase 1 full; 4 atomic rollbacks in Phase 1 no_verification). M1 effect at N=60 is below detection at the mean; structural evolution is observed at longitudinal scale.
- **Registry convergence at cycle 4** under proposal-literal utility-score KL, with independent SciPy cross-check agreement (0 disagreements). Final cumulative Gini 0.063 from seeded baseline 0.000 (delta +0.063, RQ #1 directional evidence).
- **Phase 4 longitudinal task success 33/40 = 82.5%** lands inside the Phase 1 JIT cluster band; first 10 = last 10 = 80%, Δ +0pp. Mean is stable across 40 warm-registry tasks; structural Pillar 3 signal is the substantive contribution at this regime.

Findings the chapter must disclose as caveats:

- **Recurrence track is wrapper-finalized but supplemental.** Stage 1 PASS + Stage 3 PASS (shape gate clean, 4-row LangFuse audit 4 OK / 0 / 0 on 2026-04-28). Cited as supplemental because (a) recurrence is not the primary breadth track per protocol, and (b) recurrence has no Stage-2-style two-source agreement file for headline numbers. Pillar inversion under recurrence (full < no_verification = no_optimization by 3.7pp) is cited as cross-phase trade-off support, not standalone evidence.
- **T05 is the campaign's only zero-success cell in Phase 1 full** (3/3 failures, all `output_rejected` post-gate). Canonical Critique-A example: Pillar 2 cannot catch domain-correctness defects by design. Disclose the slice qualifier ("dead in Phase 1 full," not "dead everywhere" — T05 succeeded once in Phase 4 longitudinal pass-2 via Pillar 1 registry rescue, qualitative-only).
- **Two harness validation-path findings in Phase 4** (T06 task 29 solo-registry-match `upstream_data` drop; T10 task 34 AGENT registry-match missing `source_code`). Disclose harness-corrected sensitivity 35/40 = 87.5% alongside raw 33/40 = 82.5%.
- **Pooled-JIT vs static p<0.001 is diagnostic only** (Chapter 4.5; non-exchangeable ablations, ~3× N inflation).
- **Aggregate Gini across runs is artifact** — cite per-run mean (full 0.667, no_optimization 0.663, no_verification 0.664, static 0.000), not the pooled 0.10183 figure (`phase1_2_findings.md` §3.3).
- **`no_verification` is not "all gates off"** — Pillar 3-internal `verify_and_register_or_rollback` still fires (4 rollbacks observed in Phase 1; `pillar3.verification_rollback` event in Stage 1 R3 LangFuse trace `afa6d5aa...`).
- **§3.6 registry-reuse drift has three sub-types** (A value-shape, B cached-broken-tool, C cross-step name reuse). Chapter 7 future work — value-shape comparator at registry-reuse time.
- **Tier 1.5 sandbox import probe is cost-optimization, not a fourth methodological tier** (Chapter 4.5 disclosure).
- **Tier 3 capability-type coverage gap** — prose-only SKILL/AGENT artifacts un-probed because sandbox payload requires inlined source.
- **Pillar 1 registry-rescue is qualitative-only** (single instance, Phase 4 task 33 T05; not a frequency claim).

Findings the chapter must NOT assert:

- A mean-lift ranking among full / no_verification / no_optimization at N=60 (Holm–Bonferroni p_adj=1.00 trio — cluster, not ranking).
- A Pillar 3 mean-effect on M1 at N=60 (within CI overlap).
- A token-cost claim from M4 (deferred per `EVAL_PROTOCOL.md` §1; proxied by M5 + Step 18.6 definition-token-cost).
- A mock-contamination-rate claim from M2 (deferred; proxied by Tier 3 rejection rate).
- A compression-loss claim from T3 (deferred analyzer; the *implementation* of three-granularity Structured State Communication is the contribution).

---

**Audit trail.** Every claim above traces to a file, table, marker, audit verdict, or LangFuse trace cited inline. The thesis defense can answer "where does this number come from?" by following the per-claim citation back to `chapter6_numbers.md` (frozen v1) or `chapter6_fact_pack.md` (structural facts) or directly to the primary artifact under `docs/eval_runs/20260425/` or `data/eval/`.
