# Critique Checkpoint Review — Iteration 5 Evaluation Design

**Date:** 2026-03-17
**Reviewers:** Critique A (Claude, thesis advisor perspective), Critique B (external second opinion)
**Scope:** 260 records (13 tasks × 4 conditions × 5 runs)

---

## 1. Statistical validity

**Consensus: 5 runs is borderline.** Both critiques agree that n=5 is sufficient for large-effect claims (JIT vs static) but insufficient for fine-grained condition comparisons.

A single hallucination shifts per-task success rate by 20% (Critique B). For tasks with high variance (T05 at 60%, T12 at 40-100%), the 95% exact binomial CI is roughly ±30-40 percentage points (Critique A).

**What you CAN claim with n=5:**
- JIT >> static (72-80% vs 38.5%) — the gap survives any reasonable CI
- T06 = 0% across all JIT conditions (0/17 JIT attempts) — strong evidence of semantic gap
- T01-T03 = 100% across all conditions — registry matching is deterministic

**What you CANNOT claim:**
- That no_optimization is better than full (80% vs 72.3% — overlapping CIs)
- That no_verification is better than full (76.9% vs 72.3%)
- Per-task differences between JIT conditions (e.g., T12 at 40% vs 87.5%)

**Confounds:** Snapshot restore + seed control is good. LLM non-determinism (even at temperature=0) is the dominant noise source and cannot be eliminated — only averaged out with more runs.

**Recommendations:**
- Frame JIT conditions as a cluster at 72-80%, significantly above static at 38.5% — not rank-ordered (both critiques)
- Use exact binomial CIs in all tables (Critique A)
- If condition comparisons matter: increase to n=10-15 for high-variance tasks T04-T13 only, keep n=5 for deterministic tasks (Critique B — more targeted than blanket increase)

---

## 2. Condition design

**Consensus: additive ablation, not factorial.** Both critiques identify the same structure:

1. `static` = baseline
2. `no_verification` = JIT only
3. `no_optimization` = JIT + Verification
4. `full` = JIT + Verification + Optimizer

**Clean comparisons available:**

| Comparison | What it tests |
|---|---|
| full vs no_optimization | Optimizer effect (clean) |
| no_optimization vs no_verification | Verification effect (clean) |
| full vs no_verification | Verification + Optimizer (confounded) |
| any JIT vs static | JIT generation effect (clean) |

**On the confound:** Critique A flags that `no_verification` disabling both Pillar 2 and 3 prevents isolating verification's standalone effect. Critique B argues this is not a confound but a requirement — Pillar 3 assumes Pillar 2 is active, so testing them independently is conceptually invalid for this architecture. **Critique B's framing is stronger for the thesis:** state that the optimizer is architecturally dependent on verification, making independent testing meaningless.

**Missing condition:** No JIT + Optimizer without Verification condition. Minor gap given the optimizer hurts regardless.

**Recommendations:**
- Rename conditions in thesis to make additive structure explicit (both critiques)
- State that Pillar 3 presupposes Pillar 2, so independent ablation is architecturally invalid (Critique B)

---

## 3. Task coverage

**Consensus: T01-T03 inflate aggregates.** Both critiques agree these registry-match tasks must be reported separately from synthesis tasks.

For generation-only tasks (T04-T13), JIT success drops from ~75% to ~65% — the more honest number for Pillar 1 claims.

**Blind spots (Critique A):**
- No task tests capability reuse (generate once, invoke multiple times)
- No task tests inter-capability dependencies
- All tasks are single-capability
- All acceptable limitations for thesis scope — document in Chapter 4

**Domain bias (Critique B):** The business analytics testbed may bias toward tasks well-represented in LLM pre-training data (Pandas, SQL). Synthesis might fail in niche domains.

**Type coverage is adequate:** 9 MCP_TOOL, 2-3 SKILL, 1 AGENT. AGENT coverage is thin (only T13) but sufficient given it works at 77%.

**Recommendations:**
- Report "All tasks" and "Synthesis tasks (T04-T13)" separately in all tables (both critiques)
- Document blind spots and domain bias as limitations in Chapter 4

---

## 4. Validator soundness

**Consensus: false negatives are the bigger risk.** T09 (static=100%, JIT~20%) is the clearest case — LLM produces valid but differently-structured JSON that fails the validator.

**T06 validators are sound** — ±10% tolerance on variance is reasonable; tools consistently miss by 10-18%. Genuine numerical incorrectness, not a validator problem.

**False positive risk** is low given deterministic fixtures and threshold-based checks (e.g., T08: accuracy > baseline+5%).

**Divergence on remedy:**
- Critique A: Add a "validator strictness" classification (deterministic, tolerance, structural). Acknowledge T09 as penalizing valid alternative outputs.
- Critique B: Build an LLM-as-judge semantic validator for T09 to check equivalence rather than structure.

**Resolution:** Both are useful. The classification is low-effort and should be done regardless. LLM-as-judge is higher effort but would strengthen the T09 analysis. A pragmatic middle ground: manually inspect the 5 failed T09 JIT outputs and report how many were semantically correct despite failing the validator.

**Recommendations:**
- Classify validators by strictness type in thesis (low effort)
- Manually inspect T09 failures for semantic correctness (medium effort)
- LLM-as-judge for T09 if time permits (higher effort, stronger evidence)

---

## 5. Semantic correctness gap

**Consensus: T06 is the strongest and most academically interesting finding.** Both critiques agree it demonstrates a "Verification Illusion" — generated code passes all three verification tiers but fails actual business logic.

**Quantified across the dataset:**
- 249/260 gate passes vs 197/260 successes = 52 records where verification passed but output was wrong
- 20.9% false-negative rate for the gate (Critique A)
- Additional tasks showing the gap: T07 (77% gate, 59% success), T09 (100% gate, 32% success), T12 (74% gate, 57% success)

**On evidence sufficiency:**
- Critique A: One task at 0% is sufficient given the aggregate 20.9% false-negative rate across all tasks
- Critique B: One task is thin for a general claim — needs qualitative root-cause analysis

**Resolution:** Both are right. The aggregate rate proves the gap exists broadly; T06 at 0% is the extreme case. A qualitative deep-dive on T06's failure mechanism strengthens the claim without requiring additional tasks.

**Recommendations:**
- Frame as: "Three-tier verification eliminates structural and adversarial defects but has a fundamental blind spot for semantic/mathematical correctness" (Critique A)
- Perform qualitative root-cause analysis on T06's failed runs — categorize why (hallucinated stdlib behavior, misinterpreted constraints, etc.) (Critique B)
- Propose output-based verification (oracle testing, property-based testing) as future work

---

## 6. Optimizer evaluation fairness

**Consensus: the experiment actively punishes the optimizer.** Both critiques agree the single-run snapshot design is the wrong environment for an evolutionary optimizer.

**What makes the current results defensible:**
- Issue was predicted before running (documented in advance)
- Mechanism is well-understood (pruning with no invocation history)
- no_optimization condition proves removing optimizer helps in single-run

**Divergence on remedy:**
- Critique A: Don't try to rescue it. Report honestly, reference simulation.py as theoretical evidence, frame as cold-start problem.
- Critique B: Design a longitudinal workload test (30-50 sequential tasks without snapshot reset). If infeasible, demote Pillar 3 to "Theoretical Design & Pilot Implementation."

**Resolution:** The longitudinal test is the stronger academic move if feasible (~8-10 hours compute). If not feasible, Critique A's framing (cold-start analogy + simulation evidence) is defensible for a Master's thesis. Demoting to "theoretical" (Critique B's fallback) is overly conservative given the simulation already exists.

**Recommendations:**
- If time permits: run 30-50 sequential tasks without snapshot reset to test longitudinal behavior
- If not: report single-run results honestly + cite simulation.py + frame as cold-start problem
- Either way: do NOT demote Pillar 3 entirely — the implementation and simulation are real contributions

---

## 7. Missing metrics

**Divergence on severity:**
- Critique A: `token_cost` is a "moderate weakness"
- Critique B: `token_cost` is "non-negotiable" for an LLM engineering thesis

**Critique B's position is stronger.** The primary tradeoff of JIT synthesis vs static capabilities is compute cost. Without cost data, the ROI argument is incomplete.

**Recovery path (both agree):** Extract token usage from LangFuse traces via `run_id`. Even approximate data is better than none.

**On `human_label`:**
- Critique A: Skip it entirely
- Critique B: Label a 10% stratified sample to validate automated validators

**Resolution:** The 10% sample would strengthen validator soundness claims but is lower priority than token costs.

**Recommendations:**
1. Backfill token costs from LangFuse traces (high priority, both critiques)
2. If traces are incomplete, estimate from model pricing + typical token counts (document as estimates)
3. 10% stratified human labeling if time permits (low priority)

---

## 8. Reproducibility

**Consensus: good foundations, irreducible LLM non-determinism.**

Strong points:
- Snapshot restore + seed control
- DuckDB persistence
- Deterministic fixtures (seed=42)

**Hidden non-determinism:**
- **LLM responses** — dominant noise source, even at temperature=0 (both critiques)
- **API-side changes** — model version updates, hidden temperature scaling (Critique B)
- **Docker layer caching** — dynamic LLM-based import resolution may vary across runs (Critique A)
- **Network latency** — affects timeouts, can cause failures (Critique A)

**Recommendations:**
- Document exact model versions and evaluation date range (Critique B)
- Note that LLM sampling variance is itself a finding about JIT systems — they inherit non-determinism (Critique A)
- Acknowledge API-side shifts as a Threat to Validity

---

## 9. Threats to validity

**Internal validity:**
- JIT vs static comparison is robust — large gap, consistent across tasks (both critiques)
- Between-JIT-condition comparisons are weak — small gaps, n=5, overlapping CIs (both critiques)
- Optimizer single-shot design confounds Pillar 3 results (Critique B)

**External validity:**
- Business analytics testbed is one domain (both critiques)
- May bias toward tasks well-represented in LLM pre-training data (Critique B adds: Pandas, SQL)
- One testbed deeply explored > three testbeds with shallow coverage (Critique A)

**Construct validity:**
- "Success = gate_passed AND output_in_range" conflates two dimensions (Critique A)
- Binary pass/fail ignores code quality, maintainability, security (Critique B)
- Report gate_pass and output_in_range as separate columns (Critique A)

---

## 10. Actionable recommendations (synthesized, ranked by impact/effort)

### Tier 1 — Before writing the thesis chapter (analysis only, no new runs)

| # | Action | Impact | Effort | Source |
|---|---|---|---|---|
| 1 | **Disaggregate retrieval (T01-T03) vs synthesis (T04-T13) tasks** in all tables | High | Trivial | Both |
| 2 | **Add exact binomial CIs** to all success rates | High | Low | Critique A |
| 3 | **Backfill token costs** from LangFuse traces | High | Medium | Both (B: non-negotiable) |
| 4 | **Frame conditions as additive ablation**, report JIT as cluster not ranking | High | Zero (writing) | Both |
| 5 | **Qualitative root-cause analysis on T06** — categorize failure mechanisms | High | Medium | Critique B |
| 6 | **Report gate_pass and output_in_range as separate columns** | Medium | Low | Critique A |
| 7 | **Manually inspect T09 JIT outputs** for semantic correctness | Medium | Low | Synthesis |
| 8 | **Classify validators by strictness type** (deterministic, tolerance, structural) | Medium | Low | Critique A |
| 9 | **Document model versions and evaluation date range** | Low | Trivial | Critique B |
| 10 | **Compute per-task effect sizes** (Fisher's exact test) | Medium | Low | Critique A |

### Tier 2 — If time permits before defense

| # | Action | Impact | Effort | Source |
|---|---|---|---|---|
| 11 | **Longitudinal optimizer test** (30-50 sequential tasks, no snapshot reset) | High | High (~8-10h compute) | Critique B |
| 12 | **Increase runs to n=10-15** for high-variance tasks T04-T13 only | High | High (runtime + cost) | Critique B |
| 13 | **LLM-as-judge validator for T09** (semantic equivalence check) | Medium | Medium | Critique B |

### Tier 3 — Skip unless committee asks

| # | Action | Impact | Effort | Source |
|---|---|---|---|---|
| 14 | **10% stratified human labeling** to validate automated validators | Low | High | Critique B |
| 15 | **Blanket n=15 across all conditions** (only if p<0.05 demanded) | Low | Very High | Critique A |

---

## Bottom Line

**Both critiques agree: the evaluation is defensible for a Master's thesis.**

The data supports three clear claims:

1. **JIT capability synthesis significantly outperforms static baselines** (72-80% vs 38.5%)
2. **Three-tier verification catches structural defects but misses semantic correctness** (~21% false-negative rate, T06 as poster child)
3. **Granularity optimization requires longitudinal invocation data to be effective** (cold-start problem)

The main risks are **presentation risks, not data risks.** The evaluation design is sound; the danger is overclaiming from small samples. Frame JIT conditions as a cluster, disaggregate retrieval from synthesis tasks, add confidence intervals, backfill token costs, and do a qualitative deep-dive on T06. These are all writing and analysis changes — no new compute required for the core claims.

The only items requiring new compute (longitudinal optimizer test, more runs) are Tier 2 — nice-to-have for a stronger defense but not required for a defensible thesis.
