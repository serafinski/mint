# Chapter 6 — Evaluation Report (thesis-ready, Pass 2)

**Status:** rebuilt from `docs/chapter6_fact_pack.md` + `docs/chapter6_numbers.md` v1.1 + primary artifacts under `docs/eval_runs/20260425/` (Phase 1 cold-start, Phase 4 longitudinal) and `docs/eval_runs/20260425_recurrence/` (Phase 3b).
**Protocol:** `docs/EVAL_PROTOCOL.md` (2026-04-22).
**Branch / HEAD:** `iter/6-execution-backends` @ `2724363` for Stage 1 verify → Stage 5 and recurrence Stage 3 verify; `1cf9de7` for Stage 0 / Stage 1 launch (test-only and harness-instrumentation diffs; see §9.4).
**Numbers ledger:** `docs/chapter6_numbers.md` v1.1 (initial freeze 2026-04-27; surgical recurrence-state correction 2026-04-28; **no headline number changed**).

---

## 1. Executive summary

The evaluation campaign tested the proposal's three pillars (JIT capability pipeline, three-tier verification, granularity optimizer) across three workload regimes:

- **Phase 1 — cold-start breadth (Stage 1+2 primary).** Four conditions × three iters × twenty tasks = 240 records. Snapshot-reset registry per iter.
- **Phase 3b — recurrence supplemental.** Four conditions × three iters × six task families × three fixture variants = 216 records. Warm registry within iter; reset between iters.
- **Phase 4 — longitudinal warm-registry (Stage 4 primary for M6).** One condition × one run × 40 tasks. No inter-task registry reset.

**Trustworthiness verdict.** The campaign produces evidence strong enough to cite in the thesis on M1, M3, ClassAcc, M5, and M6. All five headline metrics passed two-source agreement (analyzer Source-1 vs SQL or SciPy Source-2) with `n_disagreements: 0` (`22_agreement_summary.json`, `34_stage4_agreement.json`). Six main-track audit stages sampled 27 trace-level rows; the supplemental recurrence Stage 3 audit added 4 more. **All 31 audited rows verdict OK; 0 SUSPICIOUS, 0 FAIL.**

**Stages that completed cleanly.** Main track: Stage 0 (smoke), Stage 1 (main-eval shape gate), Stage 2 (M1 / M3 / M5 / ClassAcc with two-source agreement), Stage 3 (deterministic spotcheck), Stage 4 (M6 with two-source agreement), Stage 5 (plausibility drill-down). Supplemental track: recurrence Stage 1 (shape gate) and recurrence Stage 3 (shape gate + 4-row LangFuse audit, finalized 2026-04-28T20:14:12Z).

**Stages qualified as supplemental.** Phase 3b recurrence is wrapper-finalized but remains supplemental for two protocol-level reasons: it is not the primary breadth track per `EVAL_PROTOCOL.md` §1/§2, and recurrence headline numbers lack a Stage-2-style two-source agreement file. Cited as cross-phase trade-off support only.

**Defensible thesis claims.**

- **Pillar 1** contributes a 27.5pp lift on synthesis tasks at cold start (full 80.4 % vs static 52.9 %, T04–T20). Core-Work classification accuracy is 93.53 % aggregate (n = 170).
- **Pillar 2** narrows cross-iter variance from 6.1pp to 2.4pp at cold start (`phase1_2_findings.md` §2.2); gate pass 100 % in full; zero unverified-leak failures across 240 records.
- **Pillar 3** mechanism integrity is verified across all four merge variants. Utility-score KL convergence at cycle 4 (Phase 4); seeded → evolved Gini delta +0.063.

The evidence does **not** support a mean-success ranking among the three JIT conditions at N = 60 (Holm–Bonferroni `p_adj = 1.00` on all three pairs; cluster, not ranking) and does not support a Pillar-2 or Pillar-3 mean-success lift at this horizon. Both are explicitly disclosed as scope-of-evidence limitations rather than null results.

---

## 2. Protocol compliance and execution status

### 2.1 Marker-status matrix

Marker files were inspected directly. Every "PASS" marker carries a verdict reason describing what passed (shape, agreement, audit), not just shape (`EVAL_PROTOCOL.md` §2.1 invariant: `SHAPE_OK ≠ PASS`).

| Stage | Track | Marker file | State | Verdict reason | Audit verdicts file | Sample size |
|---|---|---|---|---|---|---|
| 0 (smoke / preflight) | main | `stage0.marker` | PASS | shape OK + audit verdicts clean (2026-04-25T18:09:54Z) | `stage0_audit_verdicts.md` | 5 OK / 0 / 0 |
| 1 (main_sample, pass 1) | main | `stage1.marker` | PASS | shape + audit clean (2026-04-26T12:53:17Z) | `stage1_audit_verdicts.md` | 7 OK / 0 / 0 |
| 2 (main_sample, pass 2) | main | `stage2.marker` | PASS | agreement clean; main_sample audit handoff (2026-04-26T12:54:28Z) | `stage2_audit_verdicts.md` | 7 OK / 0 / 0 |
| 3 (deterministic spotcheck) | main | (no marker by design — deterministic SQL hash-sample) | PASS | embedded verdict in `stage3_spotcheck.md` | `stage3_spotcheck.md` | 3 OK / 0 / 0 |
| 4 (longitudinal_sample) | main | `stage4.marker` | PASS | shape + audit clean (2026-04-27T20:41:47Z) | `stage4_audit_verdicts.md` | 4 OK / 0 / 0 |
| 5 (plausibility_flagged) | main | `stage5.marker` | PASS | priors + audit clean (2026-04-27T20:50:11Z) | `stage5_audit_verdicts.md` | 1 OK / 0 / 0 |
| 1 (recurrence) | recurrence (supplemental) | `stage1.marker` | PASS | shape + audit clean (2026-04-26T12:53:17Z) | (cold-start audit reused) | — |
| 3 (recurrence) | recurrence (supplemental) | `stage3.marker` | PASS | shape + audit clean (2026-04-28T20:14:12Z) | `stage3_audit_verdicts.md` | 4 OK / 0 / 0 |

The recurrence Stage 3 marker advanced from LAUNCHED to PASS on 2026-04-28 once the wrapper closeout completed (`41_recurrence_status.txt` "Finished at 2026-04-28T20:04:41Z"), the shape gate `43_stage3_gate.json` returned `_problems: []`, and the four-row LangFuse audit `stage3_audit_verdicts.md` returned 4 OK / 0 / 0. There are no remaining marker anomalies on either track.

### 2.2 Two-source agreement (the protocol's primary correctness mechanism)

Per `EVAL_PROTOCOL.md` §1 rule 2, every headline metric is produced two ways and required to agree. Both pairs of artifacts exist and both report empty `details: []`:

| Headline scope | Source-1 (analyzer) | Source-2 (independent) | Agreement file | Disagreements |
|---|---|---|---|---|
| Stage 2: M1 / M3 / M5 / ClassAcc | `20_analyzer_metrics.json` | `21_sql_crosscheck.json` (hand-written SQL) | `22_agreement_summary.json` | **0** |
| Stage 4: M6 (utility-score KL series + convergence cycle) | `33_analyzer_m6.json` | `32_stage4_gate.json::M6_scipy_kl` (independent SciPy reconstruction) | `34_stage4_agreement.json` | **0** |

The recurrence track has no equivalent agreement file; its headline numbers are analyzer-only. This is the single material reason recurrence stays supplemental (§5).

### 2.3 Audit integrity

Six main-track audit stages sampled 27 trace-level rows. The supplemental recurrence Stage 3 added 4 more rows. Aggregate result: **31 sampled / 31 OK / 0 SUSPICIOUS / 0 FAIL.**

Sampling axes covered:

- random + condition-stratified (Stage 1, Stage 2)
- deterministic hash-sample per `EVAL_PROTOCOL.md` §Stage 3 (main Stage 3)
- longitudinal task + Pillar 3 merge event (Stage 4)
- plausibility-flagged (Stage 5)
- preflight smoke (Stage 0)
- recurrence variant audit (recurrence Stage 3)

Three internally consistent audit-rollup scopes coexist (`chapter6_numbers.md` §5; `chapter6_fact_pack.md` §B):

- **Main-track 27/27/0/0** — Chapter 6 cites this as the headline figure.
- **All-tracks 31/31/0/0** — cite when discussing campaign-wide trace integrity including the supplemental track.
- **Cold-start subset 23/23/0/0** — `stage5_dead_task_findings.md` §1.2; consistent within its scope (Stages 0/1/2/3/5 only).

The three figures are scope differences, not verdict mismatches.

### 2.4 Stage classification per `EVAL_PROTOCOL.md` §1

- **Fully passed (cite as primary):** Stage 1 (shape gate), Stage 2 (M1 / M3 / M5 / ClassAcc with two-source agreement), Stage 4 (M6 with two-source agreement).
- **Audit-qualified (trace-level integrity):** Stage 0, Stage 3 (main), Stage 5.
- **Supplemental, wrapper-finalized:** Phase 3b recurrence (Stage 1 PASS + Stage 3 PASS). Cited for cross-phase trade-off support; not promoted to primary because (a) it is not the protocol's primary breadth track and (b) it has no Stage-2-style two-source agreement file.
- **No incomplete stages remain on either track.**

---

## 3. Chapter 6 number ledger

All values below are recorded in `docs/chapter6_numbers.md` v1.1 (frozen). Values are cited verbatim; do not re-derive.

### 3.1 Stage 2 — Phase 1 cold-start (N = 240; ClassAcc denominator n = 170)

| Metric | Final value | Source-1 | Source-2 | Agreement | In `chapter6_numbers.md`? |
|---|---|---|---|---|---|
| **M1** full | 50/60 = 83.33 % | `20_analyzer_metrics.json::M1.full` | `21_sql_crosscheck.json::M1.full` | ✅ | §1.2 |
| **M1** no_optimization | 49/60 = 81.67 % | `…::M1.no_optimization` | `…::M1.no_optimization` | ✅ | §1.2 |
| **M1** no_verification | 50/60 = 83.33 % | `…::M1.no_verification` | `…::M1.no_verification` | ✅ | §1.2 |
| **M1** static | 33/60 = 55.00 % | `…::M1.static` | `…::M1.static` | ✅ | §1.2 |
| **M3** aggregate (pooled, with caveat) | 0.10183 | `20_analyzer_metrics.json::M3.aggregate` | `21_sql_crosscheck.json::M3.aggregate` | ✅ within 1e-12 | §1.3 |
| **M3** per-run mean (preferred citation) | full 0.667 / no_opt 0.663 / no_verif 0.664 / static 0.000 | `20_analyzer.log` | log-derived (single source) | n/a | §1.3 disclosure |
| **ClassAcc** aggregate | 93.53 % (n = 170) | `…::ClassAcc.aggregate_pct` | `21_sql_crosscheck.json::ClassAcc.aggregate_pct` | ✅ | §1.1 |
| **ClassAcc** per-condition | full 92.98 / no_opt 94.74 / no_verif 92.86 | `…::ClassAcc.per_condition_pct` | same | ✅ | §1.1 |
| **M5** median (s) | full 283.99 / no_opt 198.96 / no_verif 175.71 / static 0.001 | `…::M5.{cond}.median_sec` | `21_sql_crosscheck.json::M5.{cond}.median_sec` | ✅ | §1.4 |
| **M5** p95 (s) | full 916.02 / no_opt 534.81 / no_verif 471.89 / static 0.0068 | `…::M5.{cond}.p95_sec` | same | ✅ | §1.4 |

Derived from the analyzer log (Stage 2 evidence chain, also recorded in `chapter6_numbers.md` §1.5):

- **Synthesis subset (T04–T20):** full 41/51 = 80.4 %, no_opt 40/51 = 78.4 %, no_verif 41/51 = 80.4 %, static 27/51 = 52.9 %.
- **Retrieval subset (T01–T03):** JIT cluster 9/9 = 100 % across all three conditions; static 6/9 = 66.7 %.
- **JIT cluster pairwise Fisher's exact, Holm–Bonferroni adjusted:** `p_adj = 1.00` on all three pairs.
- **27.5pp gap:** full synthesis (80.4 %) − static synthesis (52.9 %) = 27.5pp. Citable cold-start contribution of Pillar 1.
- **Pooled-JIT vs static p < 0.001:** Chapter 4.5 diagnostic only; non-exchangeable ablations pooled, ~3× N inflation. **Not cited.**

### 3.2 Stage 4 — Phase 4 longitudinal (N = 40)

| Metric | Final value | Source-1 (analyzer) | Source-2 (SciPy) | Agreement | In `chapter6_numbers.md`? |
|---|---|---|---|---|---|
| **M6** convergence cycle | 4 | `33_analyzer_m6.json::convergence_cycle` (utility-score KL, threshold 0.05, min consecutive 3) | `32_stage4_gate.json::M6_scipy_kl` (independent SciPy reconstruction; strictly no fallback to invocation counts) | ✅ 0 disagreements | §2.2 |
| **M6** per-cycle series (`kl_utility`) | 40 rows | `33_analyzer_m6.json::rows[*].kl_utility` | `32_stage4_gate.json::M6_scipy_kl[*].kl_utility` | ✅ to 3 sig figs (spot-check cycle 3: 0.05421464175960593 vs 0.05421464175960551, diff < 1e-15) | §2.2 |
| Stage 4 gate shape | 4B = 4C = 40 cycles, no empty cycles, utility n = 1970 / 1970 | `32_stage4_gate.json` | n/a (gate is its own check) | shape PASS | §2.1 |
| Pillar 3 final cumulative Gini | 0.063 | `33_analyze_longitudinal.log` | (single-source log) | n/a | §2.3 |
| Pillar 3 seeded → evolved Gini delta (RQ #1) | +0.063 | `33_analyze_longitudinal.log` | n/a | n/a | §2.3 |
| Per-type op counts (`cycle_events`) | merged 21 (first cycle 1, last cycle 28); 0 split / 0 prune / 0 verification_rollback | `33_analyze_longitudinal.log` + DB query | direct `cycle_events` table inspection | ✅ identical | §2.3 |
| Phase 4 harness M1 | **33/40 = 82.5 %** raw / **35/40 = 87.5 %** harness-corrected sensitivity | `31_longitudinal_status.txt` | `data/longitudinal_optimizer_log.json` (40-entry mirror) | ✅ identical | §2.4 |

The harness-corrected figure 35/40 = 87.5 % excludes two harness validation-path findings (T06 task 29 solo-registry-match `upstream_data` drop; T10 task 34 AGENT registry-match missing `source_code`). Both are measurement-layer artifacts, not pipeline failures (`phase4_longitudinal_findings.md` §4.2). The chapter cites both bracketed values.

### 3.3 Phase 3b recurrence (supplemental — NOT in headline ledger)

| Condition | M1 | Source | Two-source check? |
|---|---|---|---|
| full | 48/54 = 88.9 % | `phase3b_findings.md` §1 + `42_recurrence.duckdb::evaluation_runs` | analyzer-derived only; **no Stage-2-style agreement file** |
| no_verification | 50/54 = 92.6 % | same | same |
| no_optimization | 50/54 = 92.6 % | same | same |
| static | 54/54 = 100.0 % (sampling-bias artifact, §5 / §4.4) | same | same |

Recorded in `chapter6_numbers.md` §3 as supplemental, clearly demarcated, not promoted to primary ledger.

---

## 4. Breadth results interpretation (Phase 1 cold-start)

### 4.1 Condition-level comparison

The three JIT conditions cluster on M1: full = no_verification = 83.33 %; no_optimization = 81.67 %. Pairwise Fisher's exact (Holm–Bonferroni adjusted) returns `p_adj = 1.00` on all three pairs — **statistically a cluster, not a ranking.** Static = 55.0 %.

The synthesis subset (T04–T20) sharpens the contrast: full 80.4 % vs static 52.9 % = **27.5pp**. This is Pillar 1's citable cold-start contribution. The retrieval subset (T01–T03) shows JIT 100 % vs static 66.7 %, demonstrating that even hand-curated seeded coverage is incomplete and JIT generation is doing real work, not duplicating the seed.

The pooled-JIT vs static p < 0.001 figure is **not cited** because pooling non-exchangeable ablations inflates N by 3× (Chapter 4.5 disclosure).

### 4.2 Task-family behaviour (full condition, 3 iters/task)

From `40_plausibility.json::info.per_task_full`:

- **17/20 tasks succeed 3/3.** Operationally robust.
- **T05 = 0/3** — the campaign's only Phase-1-full dead-task. Drove the Stage 5 INVESTIGATE flag; trace `5d3058fb...` confirms real pipeline executed end-to-end with gate = PASS but oracle rejected for residual nulls. Pure LLM-correctness defect; canonical Critique-A example (§7.2).
- **T07 = 1/3, T10 = 1/3** — single LLM-correctness defect modes (fuzzy join output rejected; forecast quality).
- **T15 = 2/3, T16 = 2/3, T20 = 2/3** — single-iter LLM stochasticity at the cluster boundary.

Capability-type distribution among the 50 successes in full: agent 15 / mcp_tool 19 / skill 16. **Balanced spread across all three execution backends** — no type dominates the success bucket; the three-backend architecture is exercised in production-equivalent proportions.

### 4.3 Run-index trend (variance, full condition)

Iter 1 → 0.85, iter 2 → 0.80, iter 3 → 0.85; σ ≈ 2.4pp. `phase1_2_findings.md` §2.2 reports the no_verification σ ≈ 6.1pp on the same iters: **verification narrows the failure-kind distribution even when it does not lift the mean.**

### 4.4 What the breadth results demonstrate

- Pillar 1 contributes a real, large, citable mean lift at cold start (27.5pp on synthesis tasks).
- Pillar 2 stabilises outcomes — failure-kind variance reduces (Phase 1 §2.2 ledger of failure types) and cross-iter σ narrows from 6.1 to 2.4pp. Zero unverified-leak failures across 240 records.
- Classification accuracy is high (93.53 % aggregate). The three-backend architecture's runtime consequence is measurable.

### 4.5 What the breadth results do NOT demonstrate

- **No mean-success lift from Pillar 2 alone at N = 60** — means tied with no_verification. Reframe as failure-kind variance reduction, not mean lift.
- **No mean-success lift from Pillar 3 alone at N = 60** — full vs no_optimization gap is 1.7pp, inside CI overlap; mechanism integrity is verified separately (27 merges in full, 21 merges + 4 atomic rollbacks in no_verification, from `cycle_events` aggregates in `12_metrics.duckdb`).
- **No statistical claim about ranking among the JIT cluster.** Holm–Bonferroni explicitly forbids it.
- **No claim against deferred metrics M2 / M4 / T3 compression-loss** — all three deferred to Chapter 7 per `EVAL_PROTOCOL.md` §1.

---

## 5. Recurrence interpretation (Phase 3b supplemental)

**What recurrence contributes.** Evidence that under warm-registry repeated-task pressure the JIT cluster **inverts** — the ablations beat the full configuration by ~3.7pp:

- full = 48/54 = 88.9 %
- no_verification = 50/54 = 92.6 %
- no_optimization = 50/54 = 92.6 %

This is not a contradiction of the cold-start result. It is the **cost-side** of the proposal's autonomy/correctness/economy trade-off: Tier 3 wallclock (~5–10 min per generated artifact) and Pillar 3 reuse-drift (§7.4 sub-types A/B/C) cost the mean when warm-registry reuse is the dominant path.

**Protocol qualification.** The recurrence track is now fully wrapper-finalized:

- `stage1.marker` = PASS (2026-04-26T12:53:17Z, "shape + audit clean").
- `stage3.marker` = PASS (2026-04-28T20:14:12Z, "shape + audit clean").
- Shape gate `43_stage3_gate.json` clean (216/216 rows, no problems, `_problems: []`).
- Frozen recurrence snapshot `42_recurrence.duckdb` available under `docs/eval_runs/20260425_recurrence/`, parallel to the main-track snapshots.
- Four-row LangFuse audit `stage3_audit_verdicts.md` returned 4 OK / 0 / 0 (recurrence_sample profile: 3× T04 happy across full / no_optimization / no_verification on variant v01, plus 1× T13 agent on variant v03 in full).

**Why recurrence remains supplemental.** Two protocol-level reasons, neither related to wrapper completeness:

1. Recurrence is not the primary breadth track per `EVAL_PROTOCOL.md` §1 / §2 — Stage 1 main_eval is.
2. **No Stage-2-style two-source agreement file exists for recurrence headline numbers** — they are derived analyzer-side from `42_recurrence.duckdb` without an independent SQL cross-check on the recurrence DB.

Recurrence is therefore cited as cross-phase trade-off support, never as a standalone headline. The static = 100 % in this track is a **sampling-bias artifact** (`phase3b_findings.md` §4.1) — the recurrence pool was deliberately drawn to overlap seeded coverage; the real no-JIT floor is Phase 1 static = 55.0 % across 20 diverse tasks.

---

## 6. Longitudinal interpretation (Phase 4)

**What Stage 4 tested.** A single 40-task longitudinal-warm run over a two-pass shuffled sequence of T01–T20 (one condition: longitudinal-warm). No inter-task registry reset — exactly the regime cold-start Phase 1 and intra-iter recurrence Phase 3b cannot probe.

**40-cycle campaign at a glance.** Run id `longitudinal_516eda0ba6ec4e6ab41dbc97188b6802` (campaign `20260427-180751`). Wallclock ~4.4 h. Active capabilities trajectory 14 → 76 (harness count) / 1 → 68 (analyzer count). Seven Pillar 3 merges across all four merge variants (SKILL ×3, MCP_TOOL ×3, AGENT ×1 — first AGENT merge captured in any phase); 21 merge ops persisted in `cycle_events`; 0 rollbacks, 0 splits, 0 prunes in this 40-task warm run.

**Convergence vs raw task-success — separate the two signals.**

- **M6 (utility-score KL convergence): cycle 4.** Source-1 (`33_analyzer_m6.json::convergence_cycle`) and Source-2 (independent SciPy reconstruction in `32_stage4_gate.json::M6_scipy_kl`) agree, with `34_stage4_agreement.json` reporting 0 disagreements. The analyzer reads strictly from `cycle_utility_scores` with no fallback to invocation counts (defends `EVAL_PROTOCOL.md` finding #124). **M6 is trustworthy.**
- **Raw task success: 33/40 = 82.5 %, flat trajectory** (first 10 = 80 %, last 10 = 80 %, Δ +0pp). Inside the Phase 1 JIT cluster band 81.67–83.33 %. The mean is *stable*, not improving; the structural signal (cumulative merges, 2-tier chains, registry reuse) is where Pillar 3's evolution lives.

**Substantive meaning of convergence at cycle 4.** After three consecutive cycles below the 0.05 KL threshold, the registry's utility-score distribution is stable in proposal-literal terms: novel cycles continue to add capabilities (active-caps grow monotonically), but the relative utility shape stops shifting. This is the proposal's RQ #2 signal — registry convergence — answered with both the analyzer-of-record value and an independent SciPy cross-check.

**Pillar 3 RQ #1 signal.** Seeded (cycle 1) Gini = 0.000 → final Gini = 0.063, **delta +0.063**. The registry becomes more skewed during evolution, consistent with the "few capabilities carry more traffic" hypothesis. Magnitude is small because the 40-cycle horizon is short; framed as qualitative directional evidence, not effect-size quantification.

**Two harness validation-path findings (NOT pipeline failures; `phase4_longitudinal_findings.md` §4.2).**

1. **T06 task 29** — solo registry-match invocation drops the `upstream_data` kwarg for multi-arg MCP_TOOL caps when there is no upstream `step_0` to inject. Harness-side bug, not pipeline.
2. **T10 task 34** — AGENT registry-match has no `source_code` in metadata, so the harness records `output_in_range=False` even though the AGENT itself was registered correctly and would have run. Harness-side measurement gap.

Both fixes are post-eval scope. The chapter cites raw 33/40 = 82.5 % alongside harness-corrected sensitivity 35/40 = 87.5 %.

---

## 7. Failure analysis (organized by pattern)

Phase 1 total failures = 58/240 (= 240 − 182 successes). Per-condition × `failure_reason` from direct query against `evaluation_runs` in `12_metrics.duckdb`.

### 7.1 `generation_failed` (28; mostly structural)

- **Static: 27.** Every static failure on a synthesis task with no seeded capability covering it. Deterministic; hits T03 / T06 / T07 / T08 / T10 / T12 / T16 / T17 / T18 in every iter. **Not an LLM-quality finding** — proves seeded coverage is intentionally narrow so static is a real no-JIT floor, not a 0/0 sham.
- **no_verification: 1.** Strict-gate exhaustion via §12 finding #15 fail-closed; mechanism evidence, no Pillar 2 leak.
- **Class:** structural / instrumentation. No scientific signal beyond "the static control is honest."

### 7.2 `output_rejected` (23; THE largest LLM-quality bucket)

- **full: 8** (T05 ×3, T07 ×2, T10 ×2, T15 ×1) — all gate = PASS, oracle = FAIL.
- **no_optimization: 9** (T05 ×3, T07 ×2, T10 ×1, T15 ×1, T20 ×2).
- **no_verification: 6** — same pattern.
- **Class: scientifically meaningful — the canonical Critique-A surface.** The verification gate cannot catch domain-correctness defects by design. Its taxonomy probes mocks, hardcoding, silent fallbacks, graceful degradation — not "the cleaner missed `'NA'` as a null sentinel." T05 is the textbook example (Stage 5 trace `5d3058fb82e49a6b4ebf983fd9319b24`). Cite once in the Pillar 2 discussion.

### 7.3 `chain_step_failed` (5; downstream coupling)

- **full: 2; no_verification: 3.**
- **Class:** scientifically meaningful — §7.4 sub-A cascades (value-shape drift across chained capabilities). Verification clears each artifact in isolation; inter-step compatibility is a registry-reuse-time property the gate does not assert.

### 7.4 Registry / schema mismatch (cascade visibility)

- **Phase 1 ShapeMismatch counter (`evaluation_runs.output_shape_mismatch`):** full 2/60, no_optimization 1/60, no_verification 0/60, static 0/60. **Optimizer-on conditions produce shape mismatches; optimizer-off do not.** Pillar 3 boundary effect (`phase1_2_findings.md` §3.2).
- **Phase 3b §3.6 sub-types A/B/C** (recurrence supplemental; `phase3b_findings.md` §4.2):
  - **sub-A — value-shape drift:** cached `aggregate_transactions_by_department_a4a51f` reused against drifted parser.
  - **sub-B — cached-broken-tool:** `parse_eligibility_policy_json_and_applic_91434c` (3 independent regenerations, all fail at runtime against fixture).
  - **sub-C — cross-step name reuse:** same skill matched twice in single decomposition.
- **Phase 4 cascades:** task 21 T15 (sub-B pandas 2.x `infer_datetime_format`); task 22 T09 (sub-A stats-shape mismatch via cached pass-1 outlier detector). The task-21 → task-22 cascade is the campaign's clearest §3.6 worked example.
- **Class:** scientifically meaningful. Chapter 7 future work — value-shape comparator at registry-reuse time.

### 7.5 Validation / metadata failures (operational)

- **`tier3` (1, no_optimization):** Pillar 3-internal `verify_and_register_or_rollback` rejecting a merged child whose Tier 1/2/3 gate failed. **Not a Pillar 2 leak.** Demonstrates that no_verification is not "all gates off" — Pillar 3-internal verification still fires (Phase 1+2 §3.1).
- **Phase 4 harness findings** (NOT in `failure_reason`): T06 task 29 solo registry-match `upstream_data` drop; T10 task 34 AGENT registry-match `source_code` absence. Underlying capabilities were generated, verified, registered correctly.
- **Class:** operational / instrumentation. Disclose; do not over-interpret as scientific findings.

### 7.6 Agent execution issues (operational boundaries)

- **`agent_execution_error` (1, no_optimization):** DeepAgent runtime exception, captured cleanly via never-raises contract.
- **8-cap subagent budget guard (Phase 4 task 17 T08):** `_MAX_AGENT_SUBAGENT_BUDGET = 8` fired; one subtask truncation-dropped; T08 still completed via collapsed-lead reuse.
- **Class:** operational envelope evidence. Guards working as designed; cite as instrumentation maturity, not scientific finding.

---

## 8. LangFuse-backed validation

LangFuse is the trace-level source of truth (`EVAL_PROTOCOL.md` Stage 3); DuckDB is the aggregate source of truth. Across 7 audit checkpoints (6 main-track + recurrence Stage 3) and 31 sampled rows, **0 disagreements** between LangFuse trace content and DB labels.

### 8.1 Key sessions / run_ids

- **Phase 4 main:** `longitudinal_516eda0ba6ec4e6ab41dbc97188b6802` (campaign `20260427-180751`).
- **Phase 4 smoke (Stage 0):** `longitudinal_4e272b5a649a4ebb81a2b72382f2492a` (LangFuse session `longitudinal-42-20260425-192941`).
- **Phase 1 pilot:** per-iter run_ids recorded on `evaluation_runs.langfuse_trace_id` (e.g., `full_00_1a91ca6409ff4a60a17fa7534a73e7fc`, `no_verification_01_a96eaf43f68848099b44507ec0f0bf89`).
- **Phase 3b recurrence:** per-condition run_ids (e.g., `full_00_0736c0ff2fb3496295c4821c2485e0ab`).

### 8.2 Representative success traces (main track)

| Trace ID | Stage | Task / Cond / Type | Validates |
|---|---|---|---|
| `4e53db3cc1de50877cf5f6e8fb8c8713` | 1 R1 | T17 / full / agent | Solo DeepAgent path; Tier 1+2 + prose-skip Tier 3; full pipeline executed |
| `fa6ebf76e713ed7eb4cc2c1a85817b46` | 1 R2 | T08 / no_opt / agent | Lead-with-subagents, 2 subagents, no optimizer events (correct ablation) |
| `5b580bcea12756290eec63208caa2ea7` | 1 R6 | T16 / full / agent | 5-subagent fanout; `c200592` 64-char-cap fix held; 22.8 min DeepAgent runtime |
| `fd216e4ba2fc18678dc351b5ef72dcb3` | 2 R1 | T15 / full / skill | Pillar 3 merge `aggregate-and-apply-alert-thresholds-daily` (8065 code_bytes, exit_code = 0) |
| `9c072417e8fce258755cf1929312d8aa` | 2 R2 | T09 / no_opt / mcp_tool | 4 mcp_tools, 40+ real `sandbox.execute` events; NO `pillar3.merged` (correct ablation) |
| `7e24bad4a97b5014bee9238390b4c3c2` | 3 | T01 / full / mcp_tool | Hash-sampled spotcheck; merge `csv-parse-and-stats` from 2 parents |
| `2dfaf67da5eb3a38ae93aea8dc65def4` | 3 | T14 / full / skill | 5-gap budget allocation; classifier carve-out (parsing → mcp_tool, policy → skill) |
| `9758c49501039f68a8fb31c96b0ec2f4` | 3 | T02 / no_opt / mcp_tool | Tier 3 `network_disconnect` adversarial probe — sandbox-no-network behavior verified |
| `8a2583341ce30e6370e28dd611a22123` | 4 R1+R2 | T20 / longitudinal | Phase 4 first task; first Pillar 3 merge `apply-eligibility-policy` |
| `41d1ccc18e82635432135244ca4110a4` | 4 R3 | T18 / longitudinal | 5328-byte SHA-256 dedupe mcp_tool; KL ≈ 1e-5 (converged) |
| `a233e2372dc809e17f132271e39a488f` | 4 R4 | T04 / longitudinal | 2-gap parse + policy chain, both verified Tier 3 |

### 8.3 Representative failure / mechanism traces (main track)

| Trace ID | Stage | Context | Validates |
|---|---|---|---|
| `5d3058fb82e49a6b4ebf983fd9319b24` | 5 | T05 / full / output_rejected | The dead-task drill-down. Real pipeline (gate PASS + Tier 3 cleared + adversarial probes); oracle rejected for residual nulls. **Canonical Critique-A example.** |
| `afa6d5aa906fb98e26b756c45129f5e0` | 1 R3 | T11 / no_verification | `pillar3.verification_rollback` — Pillar 3-internal verify-and-rollback fires even when task-time gate is off (semantic split). |
| `a54390c762d6bc72a43ae1bd072ec0b3` | 2 R3 | T11 / no_verification | `compare-and-write-regions` merge candidate fails Tier 1 CRITICAL → atomic rollback per §12 finding #14. |

### 8.4 Recurrence Stage 3 audit (supplemental track)

Wrapper-finalized 2026-04-28 with a 4-row LangFuse audit on the recurrence_sample profile. Verdicts in `docs/eval_runs/20260425_recurrence/stage3_audit_verdicts.md`.

| Trace ID | Task / Cond / Type / Variant | Validates |
|---|---|---|
| `a7423919934f5cdb50761a817fe2cd09` | T04 / full / skill / v01 | JIT pipeline decomposed T04 into 3 capabilities scoped to v01 fixture paths; sticky flags green |
| `a065df533f82bc5194429bd3d3be4aaf` | T04 / no_optimization / skill / v01 | 2-cap decomposition; no optimizer events (correct ablation) |
| `9aee58628cbcb8f181494121e71b3659` | T04 / no_verification / skill / v01 | 3-cap decomposition same shape as the full-condition row; sticky flags true |
| `84d3567738fe09beea85ee3281ef2bee` (audit-prompt; runtime evidence on session-mate `a0ed6c0e62b6bd4cdf0b92bc9a99488d`) | T13 / full / agent / v03 | 6-todo plan; `sandbox_execute` with pandas/numpy on v03-specific data (cpu spike 224.584 only present in v03); wrote `profiling.json` + `anomalies.json`; contract-shaped dict returned |

**Trace-attachment quirk for the AGENT case (R4).** The trace_id surfaced in `stage3_audit_plan.json` (`84d3567738fe09beea85ee3281ef2bee`) points to the generation+merge event for the agent capability, not the runtime tool-call work. The runtime evidence lives on a session-mate trace `a0ed6c0e62b6bd4cdf0b92bc9a99488d` within the same `run_id`. The verdicts file documents this explicitly. The audit was OK because both traces belong to the same run and the runtime work is fully evidenced; cite as a known LangFuse trace-attachment quirk for AGENT-type capabilities, not an audit-integrity gap.

### 8.5 Trace coverage limitations (explicit)

- **Static condition has no LangFuse traces.** Stage 3 deliberately filters `condition != 'static' AND langfuse_trace_id IS NOT NULL`. Static is a no-LLM control; trace-level audit does not apply.
- **Pillar 3 merge events are sometimes piggy-backed on the host task's trace** rather than a dedicated trace ID. Stage 0 R4/R5 capture this from parent and child sides; the same quirk surfaced on the recurrence Stage 3 R4 AGENT row (§8.4).
- **Recurrence trace coverage is supplemental scope.** The 4-row recurrence Stage 3 audit (§8.4) provides trace-level evidence for the recurrence track but is smaller than main-track sampling and does not include a Stage-2-style two-source agreement on aggregate metrics. Recurrence numbers therefore cite trace-backed integrity but remain supplemental (§5).

---

## 9. Validity, trustworthiness, and limitations

### 9.1 Metric integrity (high)

Stage 2 and Stage 4 each have machine-readable analyzer + cross-check + agreement files; both pairs report `n_disagreements: 0`. Two-source agreement is the protocol's primary correctness mechanism (`EVAL_PROTOCOL.md` §1 rule 2). M1, M3, ClassAcc, M5 are trustworthy from Phase 1 cold-start; M6 is trustworthy from Phase 4 longitudinal.

### 9.2 Runtime behaviour integrity (high)

LangFuse traces corroborate DB labels on 31/31 audited rows across 7 sampling axes. Local logs (`10_pilot.log`, `30_longitudinal.log`, `40_recurrence.log`) corroborate timestamps, run_ids, and per-task outcomes. `cycle_events` aggregates from direct DB query match the narrative (`full → merged ×27`, `no_verification → merged ×21 + verification_rollback ×4`, `no_optimization → 0`, `static → 0` in Phase 1; `merged ×21` in Phase 4).

### 9.3 Protocol completeness (high)

All six main-track stages produced PASS markers with verdict reasons. The recurrence track is also wrapper-finalized — Stage 1 PASS (2026-04-26), Stage 3 PASS (2026-04-28). The remaining limitation on recurrence is the **absence of a Stage-2-style two-source agreement file for headline numbers**, not incomplete wrapper finalization. Recurrence is reported as supplemental for protocol-role and two-source-agreement reasons.

### 9.4 External validity / experimental limitations

- **N = 60 per condition** in Phase 1. Small enough that ~3.7pp shifts (Phase 3b inversion direction; Pillar 3 mean-effect) sit inside CI overlap. The chosen scale supports mechanism-integrity and variance arguments, not effect-size quantification at the mean.
- **Stage 0 / Stage 1 launch executed at HEAD `1cf9de7`** with WIP modifications to harness, skill_generator, sandbox_tool, and several test files. From Stage 1 verify onward, the campaign consolidated to `2724363`. The diffs were test-only and harness instrumentation (no behaviour changes to Pillar 1/2/3 code paths). Disclose once in Chapter 4.5.
- **Three uncommitted xlsx fixtures** (`tests/evaluation/fixtures_recurrence/T11/v0{1,2,3}/t11_products.xlsx`) appear in working-tree from Stage 4 launch onward and remain uncommitted at campaign closeout.
- **Tier 3 capability-type coverage gap.** Prose-only SKILL/AGENT artifacts are not adversarially probed because the sandbox payload requires inlined source. Disclose; not a methodology substitution (the agent does craft inputs and execute via `sandbox_execute`), but a coverage gap.
- **Tier 1.5 sandbox import probe is a cost-optimization, not a fourth methodological tier** (Chapter 4.5 disclosure per master plan Step 12.5).
- **Recurrence has no Stage-2-style two-source agreement file** (§5).
- **Pillar 1 registry-rescue is qualitative-only** — Phase 4 task 33 T05 single instance; not a frequency claim (`stage5_dead_task_findings.md` §2.2).

### 9.5 What the evidence supports vs. what it does not

The evidence supports claims of:

(a) a large cold-start mean lift driven by Pillar 1 (27.5pp synthesis subset);
(b) variance reduction from Pillar 2 (failure-kind diversity narrows; cross-iter σ narrows from 6.1 to 2.4pp);
(c) Pillar 3 mechanism integrity (merges, atomic rollbacks, name-collision guards, cumulative chains, all four merge variants);
(d) longitudinal utility-score convergence at cycle 4 with proposal-literal KL.

The evidence does **not** support claims that:

(a) Pillar 2 lifts the mean at this N;
(b) Pillar 3 lifts task success at this N;
(c) recurrence inversion is statistically certain (N = 54 per condition, no two-source agreement);
(d) Pillar 1 registry-rescue is a frequency-quantified mechanism (single-instance qualitative observation in Phase 4 task 33 T05).

Under N = 60 the system is **stable but not measurably more accurate** at the mean for Pillars 2 + 3 in cold start; that is exactly what the evidence shows and exactly how the chapter must frame it.

---

## 10. Chapter-ready conclusion bullets

**Findings the chapter can defensibly assert:**

- **Pillar 1 contributes a 27.5pp synthesis-task lift** at cold start (full 80.4 % vs static 52.9 %, T04–T20). Operational classification accuracy 93.53 % aggregate (n = 170). Two-source agreement clean.
- **Pillar 2 narrows cross-iter variance from 6.1pp to 2.4pp** at cold start (Phase 1, full vs no_verification). Zero unverified-leak failures across 240 records. Mean lift over no_verification at N = 60 is below detection — Pillar 2's value is failure-kind variance reduction and σ narrowing, not mean lift.
- **Pillar 3 mechanism integrity is verified across all four merge variants** (SKILL ×3, MCP_TOOL ×3, AGENT ×1 in Phase 4; 27 merges in Phase 1 full; 4 atomic rollbacks in Phase 1 no_verification). M1 effect at N = 60 is below detection at the mean; structural evolution is observed at longitudinal scale.
- **Registry convergence at cycle 4** under proposal-literal utility-score KL, with independent SciPy cross-check agreement (0 disagreements). Final cumulative Gini 0.063 from seeded baseline 0.000 (delta +0.063, RQ #1 directional evidence).
- **Phase 4 longitudinal task success 33/40 = 82.5 %** lands inside the Phase 1 JIT cluster band; first 10 = last 10 = 80 %, Δ +0pp. The mean is stable across 40 warm-registry tasks; the Pillar 3 structural signal is the substantive contribution at this regime.
- **Cross-phase trade-off characterization.** Cold-start = Pillar 1 dominates; Pillar 2 narrows σ; Pillar 3 mechanism integral but mean-effect below detection. Recurrence = Pillars 2 + 3 cost the mean (~3.7pp each). Longitudinal = Pillar 3 evolves cumulatively; Pillar 1 registry rescues at least one Pillar-2-undetectable defect. **The autonomy/correctness/economy trade-off is workload-conditional, not absolute.**

**Findings the chapter must disclose as caveats:**

- **Recurrence track is wrapper-finalized but supplemental.** Stage 1 PASS + Stage 3 PASS (shape gate clean, 4-row LangFuse audit clean on 2026-04-28). Cited as supplemental because it is not the primary breadth track and has no Stage-2-style two-source agreement file. Pillar inversion under recurrence (full < no_verification = no_optimization by ~3.7pp) is cross-phase trade-off support, not standalone evidence.
- **T05 is the campaign's only zero-success cell in Phase 1 full** (3/3 failures, all `output_rejected` post-gate). Canonical Critique-A example: Pillar 2 cannot catch domain-correctness defects by design. Disclose the slice qualifier ("dead in Phase 1 full," not "dead everywhere" — T05 succeeded once in Phase 4 longitudinal pass-2 via Pillar 1 registry rescue, qualitative-only).
- **Two harness validation-path findings in Phase 4** (T06 task 29 solo-registry-match `upstream_data` drop; T10 task 34 AGENT registry-match missing `source_code`). Cite raw 33/40 = 82.5 % alongside harness-corrected sensitivity 35/40 = 87.5 %.
- **Pooled-JIT vs static p < 0.001 is diagnostic only** (Chapter 4.5; non-exchangeable ablations, ~3× N inflation).
- **Aggregate Gini across runs is artifact** — cite per-run mean (full 0.667, no_optimization 0.663, no_verification 0.664, static 0.000), not the pooled 0.10183 figure.
- **`no_verification` is not "all gates off"** — Pillar 3-internal `verify_and_register_or_rollback` still fires (4 rollbacks observed in Phase 1; `pillar3.verification_rollback` event in trace `afa6d5aa...`).
- **§3.6 registry-reuse drift has three sub-types** (A value-shape, B cached-broken-tool, C cross-step name reuse). Chapter 7 future work — value-shape comparator at registry-reuse time.
- **Tier 1.5 sandbox import probe is cost-optimization, not a fourth methodological tier** (Chapter 4.5).
- **Tier 3 capability-type coverage gap** — prose-only SKILL/AGENT artifacts un-probed because sandbox payload requires inlined source.
- **Pillar 1 registry-rescue is qualitative-only** (Phase 4 task 33 T05 single instance; not a frequency claim).

**Findings the chapter must NOT assert:**

- A mean-lift ranking among full / no_verification / no_optimization at N = 60 (Holm–Bonferroni `p_adj = 1.00` trio — cluster, not ranking).
- A Pillar 3 mean-effect on M1 at N = 60 (within CI overlap).
- A token-cost claim from M4 (deferred per `EVAL_PROTOCOL.md` §1; proxied by M5 + Step 18.6 definition-token-cost).
- A mock-contamination-rate claim from M2 (deferred; proxied by Tier 3 rejection rate).
- A compression-loss claim from T3 (deferred analyzer; the *implementation* of three-granularity Structured State Communication is the contribution).

---

**Audit trail.** Every claim above traces to a file, table, marker, audit verdict, or LangFuse trace cited inline. The thesis defense can answer "where does this number come from?" by following the per-claim citation back to `chapter6_numbers.md` v1.1 (frozen) or `chapter6_fact_pack.md` (structural facts) or directly to the primary artifact under `docs/eval_runs/20260425/` or `docs/eval_runs/20260425_recurrence/` or `data/eval/`.
