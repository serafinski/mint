# Drift & Missing — Action List

**Source of truth:** `docs/proposal_vs_implementation.md`. This file is a focused extract of every 🔴 Drift and ❌ Missing item, organised by what blocks what. Cross-reference the main doc for full justifications.

**Execution plan:** For the ordered sequence of work and phase-level validation gates, see `docs/pre_eval_execution_plan.md`. This file is the "what is on the list"; the execution plan is the "in what order and why".

Legend:
- 🔴 **Drift** — diverges from proposal, needs an explicit decision
- ❌ **Missing** — proposed item not implemented at all
- **DECIDED** — decision is made; just needs scheduling
- **OPEN** — decision still needs to be made

---

## Status updates since the last pass

- **C1 (compositional sequencing)** — ✅ **DECIDED**: ship the full compositional plan (`docs/compositional_synthesis_plan.md`, Stages 1+2+3) before the eval. Grounded in LangFuse trace evidence showing three of Pillar 1's five stages diverge from the proposal text. See the execution plan for sequencing.
- **C2 (KL headline metric)** — ✅ DECIDED: Option C (point-mass primary, histogram sensitivity check). Bundled into item D3 with a small function rename.
- **B1 (AGENT-type coverage adequacy)** — re-scoped: the armchair verdict ("thin") was unsupported by evidence; the correct B1 check is an empirical pilot run on the post-Stage-3 pipeline. Sequenced for after Stage 3 lands.
- **CLI display bugs** (`-1 artifact(s)`, `0.0s Tier 3`, `0.0s Classifying gaps`, phantom `268.3s` on `generate_agent`) — ✅ **already fixed** in commit `781dc4e`. Regression-guarded by 12 unit tests in `tests/unit/test_cli_runner_gen_stage.py`. No further work needed.
- **Phase 0 baseline verification** — ✅ **complete** (2026-04-09): 887 unit tests pass (73 more than CLAUDE.md's old 814 baseline), Docker sandbox rebuilds cleanly, working tree is clean of code changes. Phase 1 of `docs/pre_eval_execution_plan.md` is unblocked.
- **Phase 1 — honest metrics + Tier 1/2 hardening** — ✅ **complete** (2026-04-09): commits `13b1ecd` (Tracks 1a/1b/1c) + `8ab68b0` (CLI runner verification sidecar follow-up). 938 unit tests pass (+51 net new). Verified end-to-end via full `test_mint.py` (7/7) and `run_pilot.py T14`. Stage 1 fake-write purge confirmed via SQL on `data/cli/metrics.duckdb`. Zero false positives from new checks across 8 real generations; one true positive caught (T11 placeholder in code fence) with retry succeeding. **A1, A2, and the Stage 1 portion of C1 are now done.** Phase 2 (per-gap restoration) and Phase 3 (compositional executor) remain.
- **Phase 2 — per-gap restoration** — ✅ **complete pending commit** (2026-04-09): per-gap classifier with parent-task-first cascade and packaging-signal checklist, decomposer anti-split revert, broader Anthropic-aligned skill definition, testbed vocabulary cleanup across all five prompts. 939 unit tests pass (+1 net new). Pilot `T01,T04,T14,T16` returned 4/4 success with mixed mcp_tool/skill/agent classifications (T04 produced two skill gaps for the inventory-reorder-rules-applier and reorder-quantity-cap-applier; T14 produced one skill gap for the transactions-cap-flagger; T16 collapsed correctly to all-agent via the over-decomposition rule; T01 stayed mcp_tool). The Stage 2 portion of C1 is now done. Phase 3 (compositional executor) and **Phase 3.5** (newly added — see below) remain.
- **Phase 3.5 — context engineering + subagents + decomposer rule 10** — ✅ **DECIDED** to add (2026-04-09): three follow-on items discovered during the Phase 2 retrospective have been promoted from Chapter 7 future work into the implementation plan as Phase 3.5 (split out from Phase 3 to keep HP4 risk bounded).
  - **Stage 3.5a (context engineering Bucket A)**: token budgets for MCP_TOOL source and SKILL.md body, generator prompt discipline, registry description compression. Cheap (~half day). Addresses the Compress primitive in the Write/Select/Compress/Isolate framework which the implementation was previously missing.
  - **Stage 3.5b (DeepAgents subagents for AGENT execution)**: when an agent-task is decomposed into multiple subtasks, the generator produces ONE lead config + N subagent dicts instead of N standalone agents; the executor invokes via `create_deep_agent(..., subagents=[...])`. Directly addresses the proposal's third tension (*"context economy in a MAS where agents pass context to each other"*) at the generated-capability layer. Promoted from Chapter 7 future work after the Phase 2 retrospective concluded that the previously-cited "evaluation tractability" objection was weaker than it sounded (subagent composition does not break any baseline ablation; it only shifts the absolute headline number for the full system, which all four conditions see uniformly). See `docs/compositional_synthesis_plan.md` Stage 4.5 for the architectural design and `docs/pre_eval_execution_plan.md` Phase 3.5b for the integrated execution plan.
  - **Stage 3.5c (decomposer rule 10 tightening)**: 30-minute prompt fix to make adaptive-investigation collapse fire reliably. T16 over-decomposed to 4 subtasks in the Phase 2 pilot despite rule 10 saying "adaptive investigations stay as ONE capability". Tightening the rule reduces wasted classifier calls; with Stage 3.5b implemented this is less critical since over-decomposed agent-tasks get wrapped in lead+subagents anyway, but it's still cheap.
  - **Per-task hard token budget** is explicitly DEFERRED to Chapter 7 future work (item F2 in `pre_eval_execution_plan.md` Phase 7), not promoted into Phase 3.5. Rationale: it's a pain to deal with if it fails a lot of tasks, and the Phase 6 token-cost measurements are needed first to set the cap intelligently.

---

## A. Pre-eval bundles (DECIDED, do before kicking off the 360-record run)

### A1. Tier 1 hardening bundle (decision-table item #3)

**Status:** ✅ **DONE** in commit `13b1ecd` (2026-04-09). Final size ~140 lines (Part A 70, Part B 50, Part C 12) + ~360 lines of new tests across `test_static_scanner.py`, `test_gate.py`, and the new `test_quick_validate.py`.

**Three parts:**

**Part A — MCP_TOOL Tier 1 additions (~25 lines)** — covers the proposal table patterns we currently miss:
- `unittest.mock` import AST check (`import unittest.mock`, `from unittest.mock import …`) — covers `@patch` and `MagicMock` transitively
- `random.seed(<Constant>)` AST check
- `faker` import AST check
- Lorem ipsum string-literal regex
- *Optional:* align existing comment check's severity HIGH → Medium per proposal

**Part B — SKILL/AGENT code-fence scanner (~40 lines)** — the layered design:
- Extract Python code fences from `skill_content` (SKILL)
- Extract Python code fences from `system_prompt` and from `skill_content` if `is_deep_agent=True` (AGENT — verified in `agent.py:264-287` that AGENT is structurally a superset of SKILL)
- Run the existing MCP_TOOL AST checks (`_check_hardcoded_return`, `_check_silent_except`, `_check_missing_external_calls`) against each extracted snippet
- Prose excluded by construction → T04-style false positives cannot recur

**Part C — Tier 0 prose checks (~10 lines)** — extend `quick_validate.py`:
- Lorem ipsum literal
- Literal `TODO: implement`
- Literal `# placeholder return`
- Patterns that are never legitimate in skill prose regardless of context

**After landing:** retag four rows 🟢 with the framing *"Tier 1 scopes its checks to executable portions of each artifact (Python source for MCP_TOOL, code-fenced snippets for SKILL/AGENT); prose-level cheating is out of scope by design and is caught by Tier 2/3 instead."* Converts the current scoping from a limitation into a deliberate layered design and lets Ch 4 claim literal proposal-table coverage.

**Affected rows in main doc:**
- Tier 1 Mock artifacts (line 64)
- Tier 1 Synthetic data indicators (line 65)
- Tier 1 type-scoping paragraph (line 70)

---

### A2. Tier 2 latency rename + variance check bundle (decision-table item #4)

**Status:** ✅ **DONE** in commit `13b1ecd` (2026-04-09). Final size ~70 lines in `behavioral.py` + ~80 lines of new tests in `test_behavioral.py`. Package shipped together as planned.

**Two parts:**

1. **Rename** existing 5s check from `category="latency"` to `category="timeout"` (~5 lines). The existing check is doing useful work (catching runaway tools) but is mislabelled — it flags *slow* execution while the proposal asked for *fast and uniform* (mock smell). The rename frees up the `latency` terminology for the proposal's threat model.

2. **Add** new `_test_latency` (~40 lines) that:
   - Reuses boundary inputs (already generated by `_test_boundary`)
   - Runs the tool 3-5 times with varied inputs
   - Computes `mean` and `cv = std/mean`
   - Flags only if `mean < 1ms` AND `cv < 0.10` (both conditions required to keep false positives near zero)

**Bounded value:** the existing output-variation test (B2.3) and hardcoded-data AST check already catch most constant-returning tools. The variance check is a backstop for the narrow case of per-input lookup tables / hash tricks where each input gets a *different* hardcoded value and only the timing gives it away.

**After landing:** retag 🟢 with the framing *"Tier 2 latency analysis matches the proposal's two-condition (fast + uniform) gate."*

**Affected row in main doc:** Tier 2 Latency row (line 90)

---

## B. Pre-eval check (no code, ~30 min, $0)

### B1. AGENT-type coverage adequacy (decision-table item #11) — **re-scoped**

**Status:** OPEN, but re-scoped. Original plan was a pre-eval armchair analysis ("could a SKILL plausibly solve each AGENT task?"). That analysis was attempted but the verdict was evidence-weak — it used loose interpretation of "plausibly", ignored AGENT's planning/file-state affordances, and was built on stale pre-audit numbers.

**New procedure (post-Stage-3 empirical pilot):** under the compositional pipeline, B1 becomes "do any subtasks in the eval actually require AGENT, or could SKILL handle them?". This is an empirical measurement on the new architecture, not an analytical guess on the old one.

1. After Stage 3 lands, run `uv run scripts/run_pilot.py --tasks T08,T10,T13,T16,T17 --condition full --n-runs 3` under the compositional pipeline
2. For any subtask classified as AGENT, record its backend execution time and success
3. (Optional) force-classify the same subtasks as SKILL via a classifier override and re-run
4. Compare AGENT vs SKILL success rates and wall-time per subtask

**Sequencing:** after Stage 3, before the full 360-record eval. Cheap (~$0.30-0.50, ~45 min).

**Independent of:** ML-training question (item #8) which is already DECIDED kept.

---

## C. Open decisions blocking pre-eval (resolve before code work begins)

### C1. Per-gap classification + multi-artifact reuse (items #1, #9, row 4b) — **DECIDED**

**Status:** ✅ DECIDED — ship the full compositional plan (`docs/compositional_synthesis_plan.md`, Stages 1+2+3) before the eval. See `docs/pre_eval_execution_plan.md` for phase sequencing.

**Historical context for future readers (left in place for auditability):**

**The coupling:**
- **Item #1** — proposal asked for per-gap classification, implementation does per-task
- **Item #9** — multi-artifact generation waste (currently mostly fixed by per-task filtering, but the underlying bug remains: the generator that fires rebuilds matched capabilities from scratch instead of reusing them)
- **Row 4b** — the *real* multi-artifact waste: registry matches are recorded as decorative invocations (`token_cost=0, latency_ms=0` in `gap_detection.py:65-73`) but never actually executed. T14 reproducer: csv-parser matched, then re-implemented inside the generated aggregator

**The big question:** **ship the compositional direction before the eval, or run the eval on the current single-artifact pipeline?**

| Sequencing | Pro | Con |
|---|---|---|
| **Ship compositional first** | Pillar 1 reuse story in Ch 6 becomes honest; Gini metric becomes clean (no decorative-match pollution) | Delays the eval; adds implementation risk |
| **Eval first** | Eval runs on tested code; no implementation risk | Pillar 1 reuse claims are inflated by decorative matches; Ch 6 needs explicit caveats; Gini reflects "intent to use" + "actual use" rather than pure execution |

**This is the biggest open call in the project.** Resolution affects items #5 (Gini caveat) and the Ch 6 narrative coherence.

---

### C2. Pillar 3 KL — which metric is the headline? (Pillar 3 KL row split) — **DECIDED**

**Status:** ✅ DECIDED — **Option C: point-mass primary, histogram as sensitivity check.**

**Two KL implementations exist:**
- `_compute_kl_divergence` in `loop.py:64-105` — **point-mass version** (per-capability identity-sensitive). Drives the optimizer's stop decision. Reported in `OptimizerState.kl_divergence`. **→ HEADLINE METRIC.**
- `_kl_divergence` in `simulation.py:76-95` — **histogram version** (shape-of-distribution, identity-insensitive). **→ SENSITIVITY CHECK.**

**Decision rationale:**
- (a) Point-mass is what the optimizer loop actually uses to drive its stop decision → operationally meaningful
- (b) More conservative metric (slower to declare convergence than histogram) → harder bar for the optimizer to clear
- (c) Preserves capability identity → matches reader intuition for "registry convergence"

**Also do:** rename one of the two functions so they don't share the name (`_compute_kl_pointmass` / `_compute_kl_histogram`) to force the API distinction. ~5 lines, can bundle with item D3.

**Unblocks:** decision-table item #6 / D3 (KL surfacing in `analyze_results.py`) — D3 now reports point-mass KL primarily and includes histogram KL as a sensitivity-check column in the Ch 6 results table.

---

## D. Post-eval aggregation work (DECIDED, sequenced after the eval)

All three are ❌ Missing aggregations. Data already exists or will exist after the eval; only the analysis script is missing.

### D1. Mock-contamination audit (decision-table item #7)

**Status:** ❌ → DECIDED post-eval. ~5 hours total.

**The most important Pillar 2 evaluation metric** — the only one that measures the gate's *false-negative rate* (of the capabilities the gate approved, how many were actually trustworthy?). All other Pillar 2 metrics are indirect.

**Five steps:**
1. Build small audit CLI (~30 min): loops through unaudited records in `data/eval/metrics.duckdb`, prints the artifact (source / SKILL.md / agent_config), prompts for `CORRECT` / `CONTAMINATED` / `PARTIAL`, writes back via `EvaluationStore`
2. Sample 30-50 records stratified by condition × capability_type (statistical credibility)
3. Sit and label (~3 h human attention)
4. Add aggregation to `analyze_results.py` (~30 min): contamination rate by condition with bootstrap CI
5. Report in Ch 6 §6.3 as the headline Pillar 2 metric

**Sequencing:** after the full 360-record eval (need real generated capabilities to audit). Does not block the eval itself.

---

### D2. Gini coefficient on longitudinal store (decision-table item #5)

**Status:** ❌ → DECIDED post-longitudinal-eval. ~1 hour with tests.

**Plan:**
- Add `_compute_gini()` helper to `analyze_results.py`
- Query invocation counts per capability from `data/eval/longitudinal.duckdb`
- Report (a) Gini at end of run as a single number, (b) Gini-over-time as a curve in Ch 6 §6.3

**Why longitudinal, not cross-sectional:** the cross-sectional eval snapshot-resets the registry between conditions, so Gini would look the same across conditions and the comparison is meaningless. The longitudinal eval (40 tasks, no reset) is where Gini becomes informative.

**Coupling caveat:** if items #1/#9 (per-gap classification) have not shipped, invocation counts are polluted by decorative match records. Either ship the compositional fix first (Gini becomes clean) or report Gini with an explicit "intent + actual use" caveat (still informative for trend analysis but not for absolute interpretation).

**Sequencing:** after the longitudinal eval re-run.

---

### D3. KL divergence surfacing in eval analysis (decision-table item #6)

**Status:** ❌ → DECIDED post-longitudinal-eval. ~1 hour with tests + ~5 lines for the rename (bundled).

**Plan:**
- Add JSON loader for `data/longitudinal_optimizer_log.json`
- Plot the KL curve over the 40 tasks (point-mass primary)
- Compute and report the convergence cycle (first cycle where KL drops below ~0.01 for ≥5 consecutive cycles) per condition
- Include histogram KL as a sensitivity-check column in the Ch 6 results table (Option C confirmed in C2)
- **Bundle:** rename `_compute_kl_divergence` → `_compute_kl_pointmass` in `loop.py` and `_kl_divergence` → `_compute_kl_histogram` in `simulation.py` to force the API distinction (~5 lines)

**Sequencing:** after the longitudinal eval re-run.

---

## E. Prose / chapter work (no code, happens during chapter drafting)

### E1. ML model training in testbed → reword Ch 6 (item #8)

**Status:** 🟢 DECIDED kept (already retagged). Pure prose work.

Replace in Ch 6 testbed description:
- ❌ "data ingestion from heterogeneous sources, data transformation, **ML model training, inference**, and report generation"
- ✅ "data ingestion from heterogeneous sources, data transformation, **statistical analysis, anomaly detection, root-cause inference**, and reporting"

ML training is not representative of business analytics workflows; adding it would be optics-driven scope creep.

---

### E2. Tech-stack pruning + additions in Ch 3 (item #10)

**Status:** Pure prose work.

**Drop from Ch 3 §3.5 + tech-stack table:**
- Google ADK
- A2A protocol
- TypeScript MCP SDK
- TOML config
- Claude Code as runtime model

**Add to Ch 3 §3.5 + tech-stack table:**
- DeepAgents (with both halves: package adoption + harness-on-small-model strategy substitution)
- DuckDB (as the metrics backbone, load-bearing for Pillar 3)
- LangFuse (as the observability backbone that delivers token cost — one of the six proposal metrics)
- Three execution backends (MCP_TOOL Docker, SKILL constrained-LLM, AGENT DeepAgent) — strengthens the classifier-routing claim

---

### E3. Tier 1 scoping framing in Ch 4 (item #2)

**Status:** Superseded by item #3 (the Tier 1 hardening bundle).

After A1 lands, Ch 4 can describe the Tier 1 scoping as a **deliberate layered design**: *"Tier 1 scopes its AST/regex checks to executable portions of each artifact (Python source for MCP_TOOL, code-fenced snippets for SKILL/AGENT); prose-level cheating is out of scope for Tier 1 by design and is caught by Tier 2's behavioral consistency tests and Tier 3's adversarial probing instead."*

No standalone work — this prose only makes sense after A1 ships.

---

## Quick reference — what to do, in order

**All open decisions are now resolved.** The ordered execution plan lives in `docs/pre_eval_execution_plan.md`. Summary:

1. **Pre-eval bundles** — Compositional plan Stages 1+2+3 + A1 (Tier 1 hardening) + A2 (Tier 2 latency)
2. **Post-Stage-3 empirical check** — B1 AGENT coverage pilot (~45 min)
3. **Full 360-record eval** (~12h parallel)
4. **Longitudinal eval** (~3h)
5. **Post-eval aggregations** — D1 (mock audit, ~5h), D2 (Gini, ~1h), D3 (KL surfacing + function rename, ~1h)
6. **Chapter drafting** — E1 (Ch 6 testbed prose), E2 (Ch 3 tech-stack), E3 (Ch 4 Tier 1 framing, superseded by A1)

Total realistic pre-eval work: **10-14 working days**, dominated by Stage 3 of the compositional plan.

See `docs/pre_eval_execution_plan.md` for phase definitions, validation gates, dependencies, and what to watch.
