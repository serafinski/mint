# Chapter 6 — Numbers (frozen, v1.1)

**Version:** v1.1 (initial freeze 2026-04-27; surgical recurrence-state correction 2026-04-28 — no headline numbers changed; see §7)
**Source campaign:** `docs/eval_runs/20260425/` (Phase 1 + Phase 4) and `docs/eval_runs/20260425_recurrence/` (Phase 3b)
**Branch:** `iter/6-execution-backends`
**Authoritative HEAD (Stage 1 verify, Stage 2, Stage 4 launch/verify, Stage 5):** `2724363650e4f1bc355c9e45ee168a9c595a589a`
**Stage 0 / Stage 1 launch HEAD:** `1cf9de76f1791b890925bf86e565c550835b6149` (older; with WIP test/harness modifications — see provenance §6)

**Protocol invariant:** Stage 2 outputs are authoritative for the headline two-source agreement on M1, M3, ClassAcc, M5. Stage 4 outputs are authoritative for M6, conditional on Stage 4 agreement being clean. Both conditions hold (0 disagreements). **No number in this file may be silently re-derived;** if a future run produces different values, version the file (v2, v3, …) instead of overwriting v1.

---

## 1. Stage 2 — primary metrics (M1, M3, M5, ClassAcc) — agreement clean

**Source files (verbatim):**
- Analyzer (Source-1): `docs/eval_runs/20260425/20_analyzer_metrics.json`
- SQL cross-check (Source-2): `docs/eval_runs/20260425/21_sql_crosscheck.json`
- Agreement: `docs/eval_runs/20260425/22_agreement_summary.json` → `{"n_disagreements": 0, "details": []}`
- Marker: `docs/eval_runs/20260425/stage2.marker` → `PASS` (2026-04-26T12:54:28Z)
- Underlying DB (snapshot): `docs/eval_runs/20260425/12_metrics.duckdb` (Phase 1, N=240)

**Population:** Phase 1 cold-start, 4 conditions × 3 iters × 20 tasks (T01–T20) = 240 records. ClassAcc denominator excludes 10 pure registry-match rows where the classifier did not run (n=170).

### 1.1 ClassAcc (Pillar 1 RQ #1 operational proxy)

| | Source-1 | Source-2 | Agreement |
|---|---|---|---|
| Aggregate | **93.53%** | **93.53%** | ✅ |
| full | 92.98% | 92.98% | ✅ |
| no_optimization | 94.74% | 94.74% | ✅ |
| no_verification | 92.86% | 92.86% | ✅ |
| Population n | 170 | 170 | ✅ |

### 1.2 M1 — task success (gate ∧ output_in_range), N=60 per condition

| Condition | n | Successes | pct | Source-2 pct | Agreement |
|---|---|---|---|---|---|
| full | 60 | 50 | **83.33%** | 83.33% | ✅ |
| no_optimization | 60 | 49 | **81.67%** | 81.67% | ✅ |
| no_verification | 60 | 50 | **83.33%** | 83.33% | ✅ |
| static | 60 | 33 | **55.00%** | 55.00% | ✅ |

### 1.3 M3 — Gini of capability-invocation counts

| | Source-1 | Source-2 | Agreement |
|---|---|---|---|
| Aggregate (pooled across runs) | **0.10183** | 0.10183 | ✅ (within 1e-12) |
| full (pooled) | 0.0 | 0.0 | ✅ |
| no_optimization (pooled) | 0.00926 | 0.00926 | ✅ |
| no_verification (pooled) | 0.00990 | 0.00990 | ✅ |
| static (pooled) | 0.0 | 0.0 | ✅ |
| n_capabilities | 307 | 307 | ✅ |

**Disclosure (Chapter 4.5 + §3.3 of `phase1_2_findings.md`):** the pooled aggregate is unreliable because per-run capability hashes differ. Cite **per-run mean** Gini from `20_analyzer.log` (NOT from the agreed JSON above): full 0.667, no_optimization 0.663, no_verification 0.664, static 0.000.

### 1.4 M5 — time-to-first-correct (seconds; successful runs only)

| Condition | n_included | avg_sec | median_sec | p95_sec | Agreement |
|---|---|---|---|---|---|
| full | 50 | **357.849** | **283.992** | **916.0193** | ✅ |
| no_optimization | 49 | 228.5666 | 198.955 | 534.8078 | ✅ |
| no_verification | 50 | 206.4951 | 175.7085 | 471.8863 | ✅ |
| static | 33 | 0.002 | 0.001 | 0.0068 | ✅ |

**Excluded:** 58 non-pilot runs where gate or output failed (full=10, no_opt=11, no_verif=10, static=27). Failed runs have no "first correct output" by construction.

### 1.5 Derived from Stage 2 (analyzer log, also in stage 2 evidence chain)

- **Synthesis subset (T04–T20)**: full 41/51 = 80.4%, no_opt 40/51 = 78.4%, no_verif 41/51 = 80.4%, static 27/51 = 52.9%.
- **Retrieval subset (T01–T03)**: JIT cluster 9/9 = 100% (all three conditions); static 6/9 = 66.7%.
- **JIT cluster pairwise Fisher's exact, Holm–Bonferroni adjusted**: `p_adj = 1.00` for all three pairs (full vs no_verif, full vs no_opt, no_verif vs no_opt). Cite as a **cluster, not a ranking.**
- **27.5pp gap**: full synthesis (80.4%) vs static synthesis (52.9%) = 27.5pp — citable headline contribution of Pillar 1 at cold start.
- **Pooled-JIT vs static p<0.001**: Chapter 4.5 diagnostic only — non-exchangeable ablations pooled, ~3× N inflation. **Do not cite.**

---

## 2. Stage 4 — longitudinal metrics (M6 + Pillar 3 evolution) — agreement clean

**Source files (verbatim):**
- **Source-1 (analyzer-of-record, M6 series):** `docs/eval_runs/20260425/33_analyzer_m6.json` — output of `scripts/analyze_longitudinal.py` reading `cycle_utility_scores` from the longitudinal DB.
- **Source-2 (independent gate / SciPy cross-check):** `docs/eval_runs/20260425/32_stage4_gate.json::M6_scipy_kl` — independent SciPy KL reconstruction over raw cycle windows, produced by `scripts/eval_protocol/stage4_longitudinal.sh`'s gate phase. Strictly reads `cycle_utility_scores` with no fallback (defends finding #124).
- **Agreement verdict:** `docs/eval_runs/20260425/34_stage4_agreement.json` → `{"n_disagreements": 0, "details": []}`
- Marker: `docs/eval_runs/20260425/stage4.marker` → `PASS` (2026-04-27T20:41:47Z)
- Companion analyzer log: `docs/eval_runs/20260425/33_analyze_longitudinal.log`
- Underlying DB (snapshot): `docs/eval_runs/20260425/31_longitudinal.duckdb`

**Population:** Phase 4 longitudinal-warm, 1 condition × 1 iter × 40 tasks (two-pass shuffled sequence over T01–T20) = 40 records. **Run id:** `longitudinal_516eda0ba6ec4e6ab41dbc97188b6802` (campaign `20260427-180751`).

### 2.1 Stage 4 gate shape

- `expected_cycles=40`, `4B_total_cycles=40`, `4C_reconstructed_cycles=40`, `4D_cycles_below_floor=[]`. ✅
- Available run_ids: `["longitudinal_516eda0ba6ec4e6ab41dbc97188b6802"]` (single available run, selected as primary).
- `4E_utility_fill`: n_rows 1970 / n_non_null 1970, range `[-43.94, 0.02999999999999997]`, `empty_cycles=[]`.
- `4F_event_windows`: 7 cycles with events, 21 total event rows.

### 2.2 M6 — utility-score KL convergence (analyzer-of-record)

**Primary KL source:** `utility_scores` (proposal-literal, mirrors `src/optimizer/loop._compute_kl_divergence`).
**Threshold:** 0.05. **Min consecutive cycles below threshold:** 3.

- **Convergence cycle: 4** (`33_analyzer_m6.json::convergence_cycle`).
- **Source-1 series:** `33_analyzer_m6.json::rows` (40 entries, key `kl_utility`). `primary_kl_source = "utility_scores"` (proposal-literal; not the invocation-count fallback).
- **Source-2 series:** `32_stage4_gate.json::M6_scipy_kl` (40 entries, key `kl_utility`). Independent SciPy reconstruction.
- **Agreement:** `34_stage4_agreement.json` reports `n_disagreements: 0` after pairwise comparison to three significant figures. Spot-checked: cycle 3 Source-1 0.05421464175960593 vs Source-2 0.05421464175960551 (diff < 1e-15).

**Notable cycles (top KL spikes after convergence):**
- Cycle 3 (T15): kl_utility = 0.0542 (above threshold; pre-convergence)
- Cycle 22 (T09, pass-2): kl_utility = 0.0356 — §3.6 sub-A cascade impact (cleaner-cached against drifted producer)
- Cycle 21 (T15, pass-2): kl_utility = 0.00966 — §3.6 sub-B cached-broken-tool impact

### 2.3 Pillar 3 evolution metrics (`33_analyze_longitudinal.log`)

- **Cycles-to-convergence: 4** (matches §2.2).
- **Cumulative Gini final: 0.063** (n_caps=68 final; range [0.000, 0.063]).
- **Seeded-vs-Evolved Gini (Pillar 3 RQ #1):** seeded (cycle 1) = 0.000 → final = 0.063, **delta +0.063** (evolution made the distribution more skewed — proposal-aligned signal).
- **Per-type op counts (cycle_events aggregate):** `merged: 21` (first-fire cycle 1, last-fire cycle 28). Other op types (split, prune, verification_rollback) absent in this 40-task warm run.
- **Active capabilities trajectory:** 1 → 68 (analyzer count) / 14 → 76 (harness count, includes seeded baseline).

### 2.4 Headline harness metrics (Phase 4 closeout, `31_longitudinal_status.txt`)

- **M1 cumulative success: 33/40 = 82.5%** (inside Phase 1 JIT cluster band 81.67–83.33%).
- Gate pass rate: 40/40 = 100.0%.
- First 10 tasks: 8/10 = 80.0%; last 10 tasks: 8/10 = 80.0%; **Δ = +0pp**.
- **Harness-corrected sensitivity: 35/40 = 87.5%** (excluding 2 harness validation-path findings — T06 task 29 solo-registry-match `upstream_data` drop; T10 task 34 AGENT registry-match missing `source_code`). See `phase4_longitudinal_findings.md` §4.2.

---

## 3. Phase 3b recurrence (supplemental — NOT a Stage 2 substitute)

**Source files:**
- Findings doc: `docs/eval_runs/20260425_recurrence/phase3b_findings.md` (authoritative narrative for Phase 3b)
- DB (live): `data/eval/metrics_recurrence_20260425_recurrence.duckdb`
- DB (frozen snapshot): `docs/eval_runs/20260425_recurrence/42_recurrence.duckdb` (v1.1 correction — recurrence snapshot was finalized after the v1 freeze; see §7)
- Per-condition metric stores root: `data/eval/recurrence_metrics_20260425_recurrence/`
- Markers: stage1 = **PASS** (2026-04-26T12:53:17Z); stage3 = **PASS** (2026-04-28T20:14:12Z, "shape + audit clean") — wrapper-finalized in v1.1 correction.
- Shape gate: `43_stage3_gate.json` (216/216, no problems, `_problems: []`).
- Audit verdicts: `stage3_audit_verdicts.md` (recurrence_sample profile, 4 rows, **4 OK / 0 SUSPICIOUS / 0 FAIL**).
- Wrapper status: `41_recurrence_status.txt` ("Finished at 2026-04-28T20:04:41Z").
- Log: `40_recurrence.log` (1.07 MB) — final line confirms `Total recurrence records: 216`.

**Population:** 4 conditions × 3 iters × 18 tasks (recurrence pool: T04, T11, T13, T14, T15, T20) = 216 records.

| Condition | Aggregate | Iter splits |
|---|---|---|
| full | 48/54 = **88.9%** | 15 / 15 / 18 |
| no_verification | 50/54 = **92.6%** | 16 / 17 / 17 |
| no_optimization | 50/54 = **92.6%** | 16 / 17 / 17 |
| static | 54/54 = **100.0%** ⚠ artifact | 18 / 18 / 18 |

**Total across all conditions:** 202/216 = 93.5%.

**Mandatory disclosure (`phase3b_findings.md` §4.1):** static = 100% is a **sampling-bias artifact**. The recurrence pool was deliberately sampled to overlap seeded coverage. The real no-JIT floor is **Phase 1 static = 55.0%** across 20 diverse tasks. Cite Phase 3b static as a deterministic registry-only sanity check, NOT as a real "no-JIT works fine" claim.

**Supplemental status (post-v1.1):** recurrence is now wrapper-finalized (Stage 1 PASS + Stage 3 PASS, shape gate clean, 4-row LangFuse audit clean) but remains **supplemental** because (a) it is not the protocol's primary breadth track per `EVAL_PROTOCOL.md` §1/§2, and (b) the recurrence DB has no Stage-2-style two-source agreement file (no analyzer-vs-SQL JSON cross-check). Cite recurrence as cross-phase trade-off support only; do not promote to headline status.

---

## 4. Stage 5 — plausibility-flagged drill-down (audit-trustworthiness only, NOT a primary metric source)

**Source files:**
- Plausibility flags: `docs/eval_runs/20260425/40_plausibility.json`
- Verdict: `docs/eval_runs/20260425/stage5_audit_verdicts.md` → `1 OK / 0 SUSPICIOUS / 0 FAIL`
- Marker: `docs/eval_runs/20260425/stage5.marker` → `PASS` (2026-04-27T20:50:11Z)
- Findings doc: `docs/eval_runs/20260425/stage5_dead_task_findings.md`

**One INVESTIGATE flag:** `P4_dead_tasks_full` — T05 in full condition = 0/3 successes. Verdict OK after trace inspection (real pipeline executed end-to-end; failure was downstream `output_rejected` from oracle on residual nulls — known §2.2 LLM-correctness defect). T05 is the campaign's only Phase-1-full dead-task.

---

## 5. Audit-integrity rollup (cross-stage)

**Main track (Chapter 6 headline):**

| Stage | Sampled rows | OK | SUSPICIOUS | FAIL | Marker | Audit verdicts file |
|---|---|---|---|---|---|---|
| Stage 0 (smoke) | 5 | 5 | 0 | 0 | PASS | `stage0_audit_verdicts.md` |
| Stage 1 (main_sample, pass 1) | 7 | 7 | 0 | 0 | PASS | `stage1_audit_verdicts.md` |
| Stage 2 (main_sample, pass 2) | 7 | 7 | 0 | 0 | PASS | `stage2_audit_verdicts.md` |
| Stage 3 (spotcheck, deterministic-sampled) | 3 | 3 | 0 | 0 | n/a (md only) | `stage3_spotcheck.md` |
| Stage 4 (longitudinal_sample) | 4 | 4 | 0 | 0 | PASS | `stage4_audit_verdicts.md` |
| Stage 5 (plausibility-flagged) | 1 | 1 | 0 | 0 | PASS | `stage5_audit_verdicts.md` |
| **Main-track total** | **27** | **27** | **0** | **0** | — | — |

**Supplemental (recurrence track):**

| Stage | Sampled rows | OK | SUSPICIOUS | FAIL | Marker | Audit verdicts file |
|---|---|---|---|---|---|---|
| Recurrence Stage 3 (recurrence_sample, post-v1.1) | 4 | 4 | 0 | 0 | PASS (2026-04-28T20:14:12Z) | `20260425_recurrence/stage3_audit_verdicts.md` |

**All-tracks total:** 31 sampled / 31 OK / 0 SUSPICIOUS / 0 FAIL.

**Audit-rollup scope-qualified resolution.** Three internally consistent scopes coexist:
- **Main-track (27/27/0/0)** — Stages 0+1+2+3+4+5 = 5+7+7+3+4+1. Chapter 6 cites this as the headline figure.
- **All-tracks (31/31/0/0)** — main-track 27 + recurrence Stage 3 4-row audit. Cite when discussing campaign-wide trace integrity including the supplemental track.
- **Cold-start subset (23/23/0/0)** — `stage5_dead_task_findings.md` §1.2; Stages 0/1/2/3/5 only (excludes Stage 4 longitudinal and recurrence Stage 3). Internally consistent within the doc's scope.

The three figures are scope differences, not verdict mismatches — every individual-stage verdict is identical across all sources.

---

## 6. Provenance summary

| Stage | Captured at HEAD | Working-tree state at capture |
|---|---|---|
| Stage 0 | `1cf9de7` | M tests/harness, M skill_generator, M sandbox_tool, M several test files (uncommitted at capture) |
| Stage 1 launch | `1cf9de7` | same as Stage 0 |
| Stage 1 verify | `2724363` | clean (untracked data/, docs/eval_runs/ only) |
| Stage 2 | `2724363` | clean |
| Stage 4 launch | `2724363` | M tests/evaluation/fixtures_recurrence/T11/v0{1,2,3}/t11_products.xlsx |
| Stage 4 verify | `2724363` | same as Stage 4 launch |
| Stage 5 | `2724363` | same |
| Recurrence stage3 launch | `2724363` | clean (untracked only) |
| Recurrence stage3 verify | `2724363` | clean (untracked only); marker advanced to PASS at 2026-04-28T20:14:12Z |

**Reproducibility note:** Stage 0 and Stage 1 launch were captured on an older HEAD (`1cf9de7`) with WIP modifications to harness/skill_generator/sandbox_tool/tests; from Stage 1 verify onward, the campaign consolidated to the published HEAD `2724363`. The 3 modified `t11_products.xlsx` fixtures from Stage 4 onward are still uncommitted as of campaign closeout.

---

## 7. Versioning policy

- **v1 (initial freeze):** numbers as of 2026-04-27 campaign closeout, frozen verbatim from Stage 2 + Stage 4 outputs with agreement clean.
- **v1.1 (this file, surgical correction 2026-04-28):** no headline metric changed; the only edits are factual corrections to recurrence-track wrapper-finalization state (recurrence Stage 3 advanced from LAUNCHED to PASS on 2026-04-28T20:14:12Z), the recurrence frozen-snapshot path (`docs/eval_runs/20260425_recurrence/42_recurrence.duckdb` now exists), the audit-integrity rollup (added supplemental recurrence Stage 3 row + all-tracks total 31/31/0/0), the recurrence-section status text, and the provenance summary. **No M1 / M3 / M5 / M6 / ClassAcc / latency value changed.** Stage 2 + Stage 4 number discipline preserved.
- **v2+:** required if any new run changes any *headline* number. **Do not overwrite v1.x.** Cite version (e.g. "Chapter 6 cites Numbers v1.1") so the thesis defense has a stable reference.

---

## Appendix A — Metric provenance map

| Metric | Final cited value | Source-1 file | Source-1 field/basis | Source-2 file | Source-2 field/basis | Agreement file | Status |
|---|---|---|---|---|---|---|---|
| **M1** (full) | 50/60 = 83.33% | `20_analyzer_metrics.json` | `M1.full.{n,successes,pct}` | `21_sql_crosscheck.json` | `M1.full.{n,successes,pct}` (SQL pooled `evaluation_runs WHERE NOT is_pilot AND condition='full' AND gate_passed AND output_in_range`) | `22_agreement_summary.json` | ✅ 0 disagreements |
| **M1** (no_optimization) | 49/60 = 81.67% | `20_analyzer_metrics.json` | `M1.no_optimization` | `21_sql_crosscheck.json` | `M1.no_optimization` | `22_agreement_summary.json` | ✅ |
| **M1** (no_verification) | 50/60 = 83.33% | `20_analyzer_metrics.json` | `M1.no_verification` | `21_sql_crosscheck.json` | `M1.no_verification` | `22_agreement_summary.json` | ✅ |
| **M1** (static) | 33/60 = 55.00% | `20_analyzer_metrics.json` | `M1.static` | `21_sql_crosscheck.json` | `M1.static` | `22_agreement_summary.json` | ✅ |
| **M3 aggregate** | 0.10183 (pooled, with caveat §1.3) | `20_analyzer_metrics.json` | `M3.aggregate` | `21_sql_crosscheck.json` | `M3.aggregate` (SQL `Gini` over pooled `invocation_events.capability_id` counts, NOT pilot) | `22_agreement_summary.json` | ✅ within 1e-12 |
| **M3 per-condition** | full 0.0 / no_opt 0.00926 / no_verif 0.00990 / static 0.0 | `20_analyzer_metrics.json` | `M3.per_condition_aggregate.*` | `21_sql_crosscheck.json` | same path | `22_agreement_summary.json` | ✅ |
| **ClassAcc aggregate** | 93.53% (n=170) | `20_analyzer_metrics.json` | `ClassAcc.aggregate_pct`, `ClassAcc.population_n` | `21_sql_crosscheck.json` | `ClassAcc.aggregate_pct` (SQL joins `evaluation_runs.capability_type` to `tests.evaluation.tasks.ALL_TASKS` expected types, excludes pure registry-match) | `22_agreement_summary.json` | ✅ |
| **ClassAcc per-condition** | full 92.98 / no_opt 94.74 / no_verif 92.86 | `20_analyzer_metrics.json` | `ClassAcc.per_condition_pct` | `21_sql_crosscheck.json` | same path | `22_agreement_summary.json` | ✅ |
| **M5 medians** | full 283.99s / no_opt 198.96s / no_verif 175.71s / static 0.001s | `20_analyzer_metrics.json` | `M5.{cond}.median_sec` | `21_sql_crosscheck.json` | `M5.{cond}.median_sec` (SQL `quantile_cont(attempt_latency_ms / 1000.0, 0.5) WHERE gate AND output`) | `22_agreement_summary.json` | ✅ |
| **M5 p95** | full 916.02 / no_opt 534.81 / no_verif 471.89 / static 0.0068 | `20_analyzer_metrics.json` | `M5.{cond}.p95_sec` | `21_sql_crosscheck.json` | `M5.{cond}.p95_sec` | `22_agreement_summary.json` | ✅ |
| **M6 convergence cycle** | 4 | `33_analyzer_m6.json` | `convergence_cycle` (analyzer of record, threshold 0.05, min consecutive 3) | `32_stage4_gate.json` | `M6_scipy_kl` series → independent SciPy KL reconstruction | `34_stage4_agreement.json` | ✅ 0 disagreements |
| **M6 series** | 40-row `kl_utility` per-cycle | `33_analyzer_m6.json::rows` | utility-score-based KL (proposal-literal) | `32_stage4_gate.json::M6_scipy_kl` | independent SciPy implementation | `34_stage4_agreement.json` | ✅ to 3 sig figs |

---

## Appendix B — DB provenance

| Snapshot path (under `docs/eval_runs/`) | Backs which Stage(s) | Live source | Key tables (row counts) |
|---|---|---|---|
| `docs/eval_runs/20260425/12_metrics.duckdb` | **Stage 1 + Stage 2** (M1, M3, M5, ClassAcc) | `data/eval/metrics_pilot_20260425.duckdb` (mirror) | `evaluation_runs` (240) · `cycle_utility_scores` (6674) · `cycle_events` (52) · `invocation_events` (344) |
| `docs/eval_runs/20260425/31_longitudinal.duckdb` | **Stage 4** (M6, Pillar 3 evolution) | `data/eval/longitudinal.duckdb` (mirror) | `evaluation_runs` (40) · `cycle_utility_scores` (1970) · `cycle_events` (21) · `invocation_events` (73) |
| `docs/eval_runs/20260425_recurrence/42_recurrence.duckdb` | **Phase 3b recurrence** (supplemental, §3) | `data/eval/metrics_recurrence_20260425_recurrence.duckdb` (live mirror) | `evaluation_runs` (216) · `cycle_utility_scores` (6206) · `cycle_events` (24) · `invocation_events` (74) |

Each `cycle_events.event_type` aggregate (Phase 1 pilot DB): `full → merged ×27`, `no_verification → merged ×21 + verification_rollback ×4`, `no_optimization → 0`, `static → 0`. Phase 4 longitudinal DB: `merged ×21` total (no rollbacks, no splits, no prunes in this 40-task warm run).
