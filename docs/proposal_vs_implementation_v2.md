# Proposal vs. Implementation — Fresh Audit (v2)

**Date:** 2026-04-11 (post-Phase 3.5c)
**Last updated:** 2026-04-18 — Refreshed against the five per-pillar audit docs (`docs/verification/01_*` through `05_*`) that are now the authoritative deep-dive stream. This file is the one-page scorecard; each row cross-links to its per-pillar audit for full rundown + file-by-file evidence. Updates: Pillar 3 KL convergence flipped ⚠ → ✅ (Step 18.7 landed 2026-04-17); Evaluation Strategy metrics table refreshed (Gini/KL done; M2/M5 routed to Steps 18.10/18.9); new §12-findings section documenting drift corrections #10–#17 + finding #18 from the per-pillar audit stream; verdict counts updated.
**Method:** One-page alignment scorecard synchronized with the per-pillar audit stream in `docs/verification/`. The per-pillar docs are the authoritative deep-dive; this file is the summary.
**Purpose:** Catch any gaps the original audit missed, verify all previously-flagged items are resolved, and keep the scorecard in sync with the live per-pillar audits.

**Authoritative deep-dive:** see `docs/verification/` — `01_bootstrapping_problem.md`, `02_pillar_1_jit_pipeline.md`, `03_pillar_2_verification.md`, `04_pillar_3_granularity_optimizer.md`, `05_evaluation_strategy.md`. Each per-pillar audit contains file-by-file rundown, full findings table, deliberate-divergence disclosures, and honest-gaps section for one proposal section. This scorecard summarizes without duplicating.

Legend:
- ✅ **Faithful** — implementation matches the proposal
- ⭐ **Strengthened** — goes beyond the proposal in a way that improves thesis claims
- 🟢 **Kept deviation** — deliberate divergence with justification
- 🟡 **Refined** — additive/defensive engineering choice, no thesis impact
- 🔴 **Drift** — deviation needing explicit decision (none remaining)
- ❌ **Missing** — proposed item not implemented

---

## Pillar 1 — JIT Capability Pipeline

| Stage | Proposal | Implementation | Status |
|---|---|---|---|
| **1. Decomposition** | Deep-thinking model with large token budget | `create_deep_agent()` with `gpt-5-mini` (small fast model + DeepAgents harness for `write_todos`, file context, summarization). 120s timeout, recursion_limit=50. | 🟢 Deliberate substitution — harness-on-small-model strategy. Same outcome, different cost/latency profile. |
| **2. Gap detection** | Semantic registry with hierarchical embeddings, confidence threshold | ChromaDB with `"{domain} > {type} > {name}: {summary}"` document format. Cosine similarity, threshold=0.6. Per-capability search. 50-word embedding summary. | ✅ Faithful. Hierarchy via text prefix, not nested collections — proposal silent on implementation. |
| **3. Classification** | Per-gap classification into MCP_TOOL / SKILL / AGENT | Per-gap classification via `classify_gap()` at temperature=0. Packaging-signal checklist. Agent-task collapse rule. | ✅ Faithful. Per-gap restored in Phase 2. |
| **4. Generation** | Code-gen agent produces artifact per type | Three sequential generators (MCP_TOOL, SKILL, AGENT). Structured output + few-shot + repair pass. Lead-with-subagents mode for ≥2 AGENT gaps. Parallel generation (4 workers each). | ✅ Faithful + extended (subagents not in proposal). |
| **5. Sandboxed integration** | Sandboxed MCP server, skill folder, contained agent session | importlib dynamic load + Docker execution (no persistent MCP servers). Skills to `data/*/skills/`. Agents via DeepAgent invocation. | 🟢 Kept deviation — avoids subprocess lifecycle complexity. |
| **State communication** | Strictly typed JSON schemas | 20 Pydantic models in `src/schemas/` with `extra="forbid"`. All inter-node state typed. `Command[T]` routing. Step 14 added `requires_upstream_keys: list[str]` on `SubTaskItem` (chain coherence). Step 14.6 added `OutputContract` (`required_keys` + `required_files`) and `ContractCheckResult`, both carried on `TaskState.output_contract` as a passenger field. Three explicit instances of the §Structured State Communication principle now in production: subtask chain contracts, output contracts at the system boundary, and the existing inter-node Pydantic models. | ✅ Faithful + ⭐ strengthened (Step 14 + Step 14.6 both pivoted away from regex prose parsing toward typed fields mid-implementation, applying the proposal principle structurally rather than as a one-off). |

**Extensions not in proposal:**
- Compositional executor (`composition.py`) — runs N artifacts in dependency order with workspace state passing
- Lead-with-subagents for AGENT chains — context isolation via DeepAgents `task()` tool
- Resolution map — links subtask required_capabilities to registry matches or generated artifacts
- LangFuse chain-executor traces with per-step spans
- **Output contract resolver** (`src/orchestrator/output_contract_resolver.py`, Step 14.6) — single shared boundary adapter used by both the harness and the CLI runner to populate `TaskState.output_contract` at task entry. Three tiers: authored shortcut (harness passes `task_definition=`), known-task prompt match (CLI re-run of a T-task), LLM extractor (CLI free-form). Same code path means "the eval is representative of CLI behavior" is structurally true rather than a Chapter 6 caveat.

---

## Pillar 2 — Verification Layer

### Tier 1 — Static Analysis

| Pattern | Proposal | Implementation | Status |
|---|---|---|---|
| Hardcoded data (Critical) | String literals, embedded JSON, inline arrays | AST: `return` of list ≥3 constants or dict ≥3 pairs. One level of variable indirection. | ✅ |
| Silent exception handling (High) | Bare except, except Exception return default | AST: bare/broad excepts that pass/return default. Permits re-raise/logging. | ✅ |
| Mock artifacts (Critical) | unittest.mock imports, @patch, MagicMock | AST: `import unittest.mock`, `import mock`, `import faker`. Regex: "mock", "fake", "dummy", "placeholder", "DEMO", "TEST_DATA". | ✅ Covers imports; `@patch`/`MagicMock` caught transitively via import check. |
| Synthetic data (Medium) | random.seed(fixed), faker, lorem ipsum | AST: `random.seed(<literal>)`, `np.random.seed(...)`. Regex: lorem ipsum. | ⭐ Strengthened — severity escalated to HIGH (stricter than proposal). LLM generators use these patterns more often than anticipated. |
| Missing external calls (High) | Functions declaring API params without network requests | AST: fetch/query/call/get-named functions without network imports. Dynamic severity: MEDIUM when `--network none` (default), HIGH when `--allow-network`. | ✅ Faithful — severity now matches proposal intent contextually. MEDIUM offline (informational), HIGH online (per proposal). |

**Type scoping:**
- MCP_TOOL: full AST + regex scan ✅
- SKILL: code-fence extraction (`_extract_python_fences`) → full AST on each fence. Prose excluded. ✅
- AGENT: code-fence extraction from `system_prompt` + `skill_content`. ✅
- Tier 0 (`quick_validate.py`): lorem ipsum, `TODO: implement`, `# placeholder return` hard-fails. ✅

**Severity mismatches vs proposal:**
- Synthetic data indicators: implementation HIGH, proposal says Medium — ⭐ **Strengthened**. LLM generators reach for `random.seed` and `faker` more frequently than the proposal anticipated. Escalating to HIGH ensures these patterns require Tier 2 behavioral clearance.
- Missing external calls: now **dynamic** — MEDIUM when network disabled (default), HIGH when `--allow-network` is enabled. ✅ **Resolved**. Matches proposal intent: severity is HIGH when tools can actually reach the network (per proposal), MEDIUM when they can't (informational only).

### Tier 2 — Behavioral Testing

| Test | Proposal | Implementation | Status |
|---|---|---|---|
| Nonexistent entity | Request data for entities that cannot exist | Empty/invalid inputs, flag silent success | ✅ |
| Consistency | Same input twice → same output | Identical-input replay, HIGH on mismatch | ✅ |
| Boundary | Extreme edges, empty strings, max-length | Zero/empty, negative, large inputs | ✅ |
| Latency analysis | Fast AND uniform = mock smell | Mean < 1ms AND cv < 0.10 (both required) | ✅ |

**Extras (not in proposal, added from bypass exercise):**
- Parameterless smoke test (B2.1 fix) ⭐
- Output variation test — different inputs → different outputs (B2.3 fix) ⭐
- Upfront dependency check — HIGH if pip install fails ⭐
- Timeout check (renamed from old `_test_latency`) — flags > 5s execution ⭐

### Tier 3 — Adversarial Probing

| Attack | Proposal | Implementation | Status |
|---|---|---|---|
| Hidden demo mode | Inputs with "sample"/"test" tokens | DeepAgent tests with demo vs neutral inputs | ✅ |
| Network disconnect | Behavior without network | Sandbox runs `--network none`; flags tools that claim API success | ✅ |
| Ground truth comparison | Compare against known correct output | Reference implementation comparison for deterministic tools | ✅ |

**Gate cost ladder:** Tier 0 → Tier 1 → Tier 2 → Tier 3. Critical blocks immediately. ✅
**Tier 2 clearance:** Tier 1 HIGH findings downgraded to MEDIUM if Tier 2 passes. ⭐ Enhancement not in proposal.
**Never-raises contract:** `verify()`, `sandbox_execute()`, `behavioral.run()`, `adversarial.probe()` all return results, never raise. ✅

### Pillar 2 — Scope limitations (pre-registered findings #6a + #7)

| Item | Proposal | Implementation | Status |
|---|---|---|---|
| **Semantic correctness verification (pre-reg #6a)** | Proposal never claimed semantic verification without an oracle, but thesis reader may ask | MINT has four failure-detection layers: crash-level (pipeline raises), gate-level (Tier 1/2/3 rejection), contract-level (Step 14.6 shape check), **semantic-level (out of scope for CLI)**. Semantic correctness = "output exists, has correct shape, has plausible content, but is semantically wrong" — cannot be detected without an oracle. Phase 5 eval uses per-task `validator` closures in `tests/evaluation/tasks.py` as external ground truth; CLI users have no such oracle. | ⚠ **Fundamental limitation** — thesis Chapter 4 must explicitly disclose. Draft: *"Semantic correctness of the capability's output against the user's actual question is not verifiable without an external oracle. Phase 5 / Chapter 6 correctness metrics use per-task validators which step outside what a CLI user can do."* |
| **Reasoning-effort configuration inconsistency (pre-reg #7)** | Proposal silent on LLM decoding parameters | Tier 3 adversarial `reasoning_effort="low"` (2026-04-14 perf fix, no quality baseline measured). Gap-detection taxonomy `reasoning_effort="minimal"` (pattern-matching, not reasoning). Decomposition / classifier / generators / merge / split / output contract extractor: **default** (inherits gpt-5-mini default, no explicit tuning). | 🟡 **Refined.** Chapter 4 disclosure: *"MINT's LLM calls use a mix of reasoning effort levels. The Tier 3 `'low'` choice was made on observed latency without a measured quality baseline. No systematic per-component tuning has been done for reasoning-model decoding patterns. Chapter 7 future work: targeted reasoning-level tuning with quality measurement."* |

### Beyond Artifact Verification — Task Outcome Checking (Step 14.6, ⭐ extension)

The proposal's verification layer (Tiers 1–3 above) certifies that each generated capability is correct *as a standalone artifact*. T16 surfaced a failure mode the three-tier gate is structurally blind to: **a pipeline can pass every Pillar 2 tier on every individual artifact and still produce a wrong-shape final answer if the decomposer assembles correct parts into the wrong plan.** Step 14.6 (commit `3b349f9`) closes this gap by adding a complementary axis of verification.

| Item | Proposal | Implementation | Status |
|---|---|---|---|
| Task outcome verification | (not in proposal — proposal only covers artifact verification) | `OutputContract` Pydantic schema (`required_keys` + `required_files`) carried on `TaskState.output_contract` as a passenger field. Populated at task entry by a single shared `resolve_output_contract()` function used by both the harness and CLI runner — three tiers: authored shortcut (harness `task_definition=task`), known-task prompt match (CLI re-run of a T-task), LLM extractor (CLI free-form). Terminal-state check via `OutputContract.check()` → `ContractCheckResult`. New CLI exit code 3, harness `EvaluationRecord.output_shape_mismatch` field with DuckDB migration. | ⭐ Strengthened — adds an orthogonal axis the proposal does not name. |
| Extractor validation | (n/a — task outcome check not in proposal) | `scripts/validate_contract_extractor.py` (Fix B) runs the LLM extractor against all 20 authored T-task contracts and asserts agreement. **Result: 20/20.** Same epistemic move as Pillar 2 applied to the *measurement instrument* itself. | ⭐ Strengthened — applies the verification philosophy to the eval's own measurement. |
| Token cost honesty | Proposal §164: *"Token cost per task — total tokens consumed including capability definitions, generation, verification, and execution"* | Harness runs the LLM extractor at task entry inside a LangFuse-tagged span and discards the result; only its tokens count toward the per-task budget. Authored contract still drives the actual check, so the measurement remains deterministic while the metric reflects real CLI usage. ~$0.40 over the full 400-record eval. | ✅ Faithful — keeps the metric honest without compromising deterministic measurement. |

**Two-axis correctness — thesis Chapter 4 framing:**

- **Pillar 2** answers: *"Is this generated capability correct as a standalone artifact?"* — verifies the *thing that was generated*.
- **Step 14.6** answers: *"Did the pipeline as a whole produce what the user asked for?"* — verifies the *task outcome at the system boundary*.

These are **orthogonal** axes. T16 is the canonical case where Pillar 2 said yes to every artifact and Step 14.6 catches the assembly-level wrong shape. Both are needed; neither subsumes the other. Chapter 4 should add a short subsection making this explicit.

**Empirical validation (test_mint smoke 2026-04-15 post-commit):** 6/7 baseline preserved. The 1 fail is the known langchain_openai 400 serdes bug from §14.9 (out of scope, library-level). Zero false positives — the LLM extractor produced contracts on six free-form CLI prompts and the terminal check correctly didn't fire on any of them. The exit-3 path is empirically validated by 12 helper integration tests in `tests/unit/test_output_contract.py` covering every branch (missing keys, missing files, satisfied, no-contract, hard-crash output=None, no-artifact, dict-state vs TaskState, non-dict output, the canonical T16 silent-failure pattern).

**Aligned with `docs/thesis_proposal.md`:**
- §Structured State Communication (lines 90-92): the contract is a typed Pydantic schema, populated once at the system boundary, read by a deterministic set comparison at the terminal state. Replaces the previous "expected outputs buried inside per-task validator closures and prose `task_prompt` strings" with a typed handle every reader can parse without executing code.
- §Pillar 2 verification philosophy: applied here to the LLM-based contract extractor itself via Fix B (20/20 agreement), so the measurement instrument is calibrated against the same standard the artifacts are.

---

## Pillar 3 — Granularity Optimizer

| Item | Proposal | Implementation | Status |
|---|---|---|---|
| Four metrics | reuse, success, token_cost, maintenance | All four in `MetricsStore.compute_granularity_metrics()` | ✅ |
| Utility formula | U(c) = α·reuse·success − β·token_cost − γ·maintenance | Exact match. α=1.0, β=0.1, γ=0.1 defaults. | ✅ |
| Split | >60% reuse, <70% success, per-type (MCP_TOOL / SKILL / AGENT) | Per-type dispatch landed in Step 8 (`1f8a7db`): `_split_tools` / `_split_skills` / `_split_agents` each produce real executable variants via the MCP_TOOL / SKILL / AGENT generator + three-tier gate per variant. MCP_TOOL additionally runs `verify_coverage` replay (Step 7, `95a3772`) — replays parent's recorded failure cases and asserts each failure mode is fixed. SKILL/AGENT rely on three-tier gate per variant without collective coverage verification (MCP_TOOL-only scope — deterministic executor assumption). Atomic rollback via `verify_and_register_or_rollback` (Step 5): gate rejection → `split()` returns None → parent stays active. MCP_TOOL ValueError guard removed. | ✅ **Resolved 2026-04-16.** Proposal-faithful across all three types. Coverage-verifier scope limitation disclosed in §12 pre-registered finding #5 and mapped to Chapter 7 future work (per-runtime replay statistics or LLM-judged correctness for nondeterministic executors). |
| Merge | <5% reuse + similarity (**embedding distance of descriptions AND overlapping input schemas**), per-type | Both conjuncts now enforced in `loop.py:identify_candidates_node`: description cosine ≥ 0.5 AND schema Jaccard ∈ [0.5, 1.0) via `_schema_jaccard` helper (exclusive upper bound preserves diversity — identical-schema caps may hide different logic). Schemas populated at registration time by the AST signature extractor wired in Steps 2+3. Per-type merge in Step 5+6 (`0927261`): `_merge_tools` / `_merge_skills` / `_merge_agents` each produce real executable merged descriptors with source_code / skill_content / agent_config inherited from the strongest parent. Atomic rollback via `verify_and_register_or_rollback`: gate rejection on the merged artifact → both parents stay active, no orphan descriptor. Verification experiment (`scripts/step10_merge_candidates.py`, 2026-04-16) confirmed schemas populate (18/18 output, 16/18 input) and gate logic fires correctly on real generated caps (writer pair at cosine 0.793 / Jaccard 0.625). | ✅ **Resolved 2026-04-16.** Proposal-faithful across all three types. Default `merge_reuse_threshold=0.05` fires only in longitudinal conditions where caps accumulate varied reuse — smoke tests and single-run conditions don't trigger it by construction (documented Chapter 6 observation, not a defect). |
| Prune | Zero invocations, all types | `deactivate()` (soft-delete). Respects pinned capabilities via `metadata.pinned == "true"` skip — seeded baseline caps (csv-parser, fibonacci, date-formatter, etc., 11 total) are protected from optimizer pressure. | ✅ + 🟢 pre-reg #1 |
| **Pinning exception in `prune()` (pre-reg finding #1)** | Proposal: *"zero invocations over a configurable window and no declared dependencies"* — no explicit pinning allowance | `metadata.pinned == "true"` skip added to protect seeded baseline caps from optimizer pressure. Without pinning, the `static` baseline degrades to an empty registry after one optimizer cycle, making the Chapter 6 comparison meaningless. | 🟢 **Kept deviation** — measurement-apparatus concession. Chapter 6 footnote: *"Seeded baseline capabilities are protected from optimizer pressure via a `pinned` metadata flag. This narrows the proposal's prune rule to preserve the integrity of the `static` baseline across longitudinal runs."* |
| **Jaccard over I∪O keys (pre-reg finding #2)** | Proposal: *"overlapping **input** schemas"* (input-only) | `loop.py::_schema_jaccard` computes over `input_schema ∪ output_schema` key names | 🟡 **Refined** — broader criterion is **more** discriminating than input-only (catches cases where inputs align but outputs diverge). Chapter 5 footnote: *"We use union-of-I/O-keys because input-only would make merge MORE eager and produce worse signal — the proposal's 'input schemas' is probably shorthand for 'I/O contract similarity'."* |
| **Atomic verify-or-rollback on Pillar 3 ops (pre-reg finding #3)** | Proposal: Pillar 3 section doesn't couple merge/split outputs to Pillar 2 gate | Every merge and split artifact flows through `src.verification.gate.verify()` before registration; on gate failure parents stay active (atomic rollback). Extends Pillar 2's *"must pass all three tiers"* contract to optimizer-generated capabilities, not just generator-generated. | ⭐ **Strengthened** — additive, closes a gap the proposal leaves unspecified. Worth a Chapter 4 subsection linking Pillar 2 and Pillar 3 through `verify_and_register_or_rollback`. |
| **Coverage verifier — "handled" semantics (pre-reg finding #5b)** | Proposal Split: *"specialized variants that target input subspaces where the capability breaks"* — no oracle defined for correctness | `verify_coverage` asserts *"variant fixes the specific failure mode recorded on the parent"*, not *"variant produces correct output"*. Error-case: variant runs without raising → fixed. Wrong-output-case: variant runs without raising AND output ≠ parent's `actual_output` → differentiated. Same-output-case: flagged `unverifiable`. | 🟡 **Refined.** Not "Tier 4" — separate coverage cross-check orthogonal to the three-tier gate. Chapter 4 disclosure: *"True correctness-verification would require external ground truth or LLM-judged correctness (Tier 3 `ground_truth` pattern), deferred to Chapter 7."* |
| **Coverage verifier — MCP_TOOL-only scope (pre-reg finding #5c)** | Proposal Split is type-agnostic on surface but presupposes stable per-input failure semantics | `verify_coverage` runs only on MCP_TOOL splits. SKILL/AGENT splits rely on three-tier gate per variant without collective coverage check. | ⚠ **Scope-limited** — NOT drift. Pillar 1 × Pillar 3 tension: proposal's Pillar 1 labels MCP_TOOL as deterministic and SKILL/AGENT as LLM-mediated; Pillar 3 Split's *"input subspaces"* presupposes stable failure semantics that only deterministic executors satisfy. Chapter 4 disclosure drafted in master plan §12 pre-reg finding #5c. |
| **Coverage verifier — wrong-output requires oracle (pre-reg finding #5d)** | Proposal Split has no defined oracle for wrong-output cases | Wrong-output-case branch only meaningful when recorded `actual_output` is *genuinely* wrong relative to some oracle. Trustworthy oracles: eval-harness `task.validator` (Phase 5), `mint feedback --wrong` (Chapter 7). Non-trustworthy: naive CLI flow without feedback. | ⚠ **Empirical limitation** (cross-linked to #5b). Chapter 4 disclosure: *"The coverage verifier's wrong-output-case semantic is only meaningful when paired with a trustworthy oracle. Chapter 6 numbers on MCP_TOOL splits in the `full` condition ARE meaningful because the eval harness injects the validator oracle."* |
| KL convergence | KL divergence between consecutive snapshots | Utility-score KL (proposal-literal, mirrors `src/optimizer/loop.py::_compute_kl_divergence`) computed online in the optimizer loop + persisted per-cycle to `cycle_utility_scores` table by the harness flush helper (Step 18.7). Post-hoc analyzer `scripts/analyze_longitudinal.py` reports cycles-to-convergence, convergence curve CSV + optional PNG, per-type op counts, Gini trajectory, seeded-vs-evolved Gini delta (Pillar 3 RQ #1), and Step 18.8 distribution-shift adaptation verdict (graceful / sluggish / collapse, Pillar 3 RQ #3). Secondary invocation-count KL retained as sensitivity series on legacy DBs via `_table_exists()` fallback. Histogram KL in `simulation.py` for visualization. | ✅ **Resolved 2026-04-17.** Step 18.7 landed full proposal-aligned (utility-score primary, not proxy); Step 18.8 added distribution-shift scripts + three-tier adaptation classifier. All three Pillar 3 RQs answered. |

---

## Evaluation Strategy

### Testbed

| Proposal | Implementation | Status |
|---|---|---|
| Business Analytics Pipeline | 20 tasks (T01-T20), 7 file formats, 34 fixtures, 3 tiers. 5 seeded (2 MCP_TOOL, 2 SKILL, 1 AGENT), 5 JIT MCP_TOOL, 5 JIT SKILL, 5 JIT AGENT. | ✅ |
| ML model training, inference | No actual ML. Statistical analysis, anomaly detection, root-cause inference. | 🟢 Kept — proposal language was loose. Reword Ch 6. |
| Diverse capability types | ~8 MCP_TOOL, ~3 SKILL (weak), ~5 AGENT | 🟢 Kept — all three types present. See coverage analysis below. |

**Capability type coverage analysis (2026-04-11):**

*SKILL coverage (weak):* No task is pure-SKILL. SKILL artifacts appear as part of mixed chains (e.g., T04 produces 2 MCP_TOOL + 1 SKILL). The decomposer strips packaging signals at the step level, making individual steps look like MCP_TOOL. Tasks T04, T05, T12 are SKILL-dominant but produce mixed outputs. Document as Ch 6 limitation.

*AGENT coverage assessment (item #11 from v1):*

| Task | Genuinely needs AGENT? | Rationale |
|---|---|---|
| T16 | ✅ Yes | Hypothesis formation + testing, causal reasoning about cascade order. Cannot be linearized. |
| T17 | ✅ Yes | Cross-referencing 3 data sources with conditional branching based on alert details. |
| T08 | 🟡 Borderline | Steps are linear (profile → categorize → rank). Prompt says "adaptive" but a SKILL with 5 sandbox calls could handle the mechanical version. |
| T13 | 🟡 Borderline | Linear (profile → compare to baselines → list anomalies). Prompt says "adapt based on findings" but the adaptation is thin. |
| T10 | 🟢 Could be SKILL | Deterministic pipeline (load → trend → seasonality → forecast). "Choose method based on data" is a one-shot decision, not iterative. |

**Verdict:** 2/5 genuine AGENT tasks, 2/5 borderline, 1/5 could be SKILL. The classifier may correctly route T10 as MCP_TOOL, which is the system working as designed. Document the 2+2+1 split in Ch 6 as a testbed observation, not a limitation — the eval measures what the classifier actually does, not what we expect.

### Baselines

| Baseline | Implementation | Status |
|---|---|---|
| Static skills | `static` condition — registry lookup only | ✅ |
| JIT without verification | `no_verification` — `gate_fn=None` | ✅ |
| JIT without optimization | `no_optimization` — `run_optimizer=False` | ✅ |
| Full system | `full` — all three pillars | ✅ |

### Metrics

| Metric | Status | Notes |
|---|---|---|
| Task success rate | ✅ Implemented | `gate_passed AND output_in_range` in `EvaluationRecord`. `scripts/analyze_results.py` reports per-condition success rate + Clopper-Pearson 95% binomial CI + Fisher's exact test vs static + Holm-Bonferroni correction. Disaggregated into retrieval (T01–T03) vs synthesis (T04–T20). |
| Mock contamination rate | ⚠️ **Scheduled — Step 18.10** | `HumanLabel` enum + `human_label` column exist in schema + DuckDB. Audit CLI + `print_contamination_rate()` reader + manual labelling pass scheduled as master plan Step 18.10 (~3-5 h: ~1 h tooling + 2-4 h human audit). Must happen post-Step-18 (needs real generated capabilities to sample from). Without it, Chapter 6 §M2 is "deferred to Chapter 7" prose. Full audit source: `docs/verification/05_evaluation_strategy.md` §7 item #1. |
| Capability reuse (Gini) | ✅ **Resolved 2026-04-17** | Step 18.5 shipped full proposal-aligned: new `invocation_events` persistent table + harness flush wiring + `_gini_coefficient()` helper + `print_gini_summary()` in `analyze_results.py` (per-condition mean + stdev + aggregate). Formula: `(2·Σ (i+1)·x_i) / (n·Σx) − (n+1)/n`. |
| Token cost per task | ✅ Implemented | Via LangFuse backfill to DuckDB (`scripts/backfill_token_costs.py`). Attribution: static=0, longitudinal=total/n_tasks, eval JIT=total/n_jit_tasks (retrieval T01–T03 excluded). Proposal-literal M3 *"definition token cost"* side-measurement **landed 2026-04-18** as Step 18.6 (`definition_token_cost` field on `CapabilityDescriptor`, tokenized at registration via `tiktoken`, idempotent backfill covers CLI + eval stores). See `docs/verification/04_pillar_3_granularity_optimizer.md` §5 for the runtime-vs-definition scope disclosure. |
| Time to first correct execution | ⚠️ **Proxy with planned fix — Step 18.9** | `attempt_latency_ms` in `EvaluationRecord` is total wall-clock for the entire `_run_one_task()` invocation. For **successful runs** this equals time-to-first-correct by construction (pipeline stops on first verified-correct output). For failed runs it's wall-time-until-give-up — not time-to-first-correct (there was no first correct). **`print_latency_summary()` already exists** in `analyze_results.py:761` but uses the weaker filter `WHERE is_pilot=FALSE AND condition != 'static'` which pools successes with failures. Step 18.9 (~15 min, revised down from ~30 min after code inspection) narrows the filter to `WHERE gate_passed AND output_in_range AND NOT is_pilot` + adds p95/stdev. Flips this row to ✅ when Step 18.9 lands. Full audit source: `docs/verification/05_evaluation_strategy.md` §5 finding #11. |
| Registry convergence (KL) | ✅ **Resolved 2026-04-17** | Step 18.7 shipped full proposal-aligned: `cycle_utility_scores` + `cycle_events` persistent tables + harness flush hooks + `scripts/analyze_longitudinal.py`. Utility-score KL (proposal-literal) + cycles-to-convergence scalar + convergence curve CSV/PNG + per-type op counts + seeded-vs-evolved Gini delta (Pillar 3 RQ #1). Step 18.8 added distribution-shift adaptation verdict (Pillar 3 RQ #3). All three Pillar 3 RQs answered with real numbers. |

**Status (2026-04-19):** 4/6 metrics fully implemented (success, Gini, token cost, KL); 2/6 scheduled via Step 18.9 (M5 latency filter, ~15 min — helper already exists, needs narrowed filter) + Step 18.10 (M2 contamination audit, ~3-5 h). Step 18.6 (definition-string token cost side-measurement) landed 2026-04-18 as an additive scope disclosure for M4. Total remaining metric work: ~3-5 h across two pre-Chapter-6 steps (18.9 + 18.10). Routed in `docs/MASTER_PLAN_2026_04_13.md` §6.1.

---

## Tech Stack

| Proposal | Implementation | Status |
|---|---|---|
| LangGraph / Google ADK | LangGraph only | ✅ (proposal listed as alternatives) |
| Claude Code or equivalent | `gpt-5-mini` via `init_chat_model()` | ✅ (proposal allowed "or equivalent") |
| MCP SDK Python/TypeScript | Python `mcp>=1.26.0` only | ✅ (picked one of two) |
| A2A protocol / LangGraph messaging | LangGraph messaging only | ✅ (picked one of two) |
| TOML config | Python constants + env vars in `src/config.py` | 🟢 Kept — functionality equivalent at testbed scale. |
| Agent Skills spec | DeepAgents-flavoured SKILL.md | ✅ |
| Docker sandbox | Docker with `--network none`, two-phase install, `uv pip` | ✅ |
| ChromaDB registry | ChromaDB persistent client, cosine similarity | ✅ |
| — (not in proposal) | DeepAgents harness | ⭐ Strengthened |
| — (not in proposal) | DuckDB metrics store | ⭐ Strengthened — load-bearing for Pillar 3 |
| — (not in proposal) | LangFuse observability | ⭐ Strengthened — delivers token-cost metric |
| — (not in proposal) | Three execution backends | ⭐ Strengthened — makes classifier routing measurable |

---

## Cross-Cutting: Things the Proposal Did Not Anticipate

These came out of building and testing the system. They are not in the proposal but matter for the thesis.

| Item | Description | Verdict |
|---|---|---|
| **Compositional Executor** | `CompositionalExecutor` in `composition.py` runs all N artifacts in dependency order via workspace at `/tmp/mint_workspace_<id>/`. MCP_TOOL via Docker mount, SKILL/AGENT via `fixture_contents` injection. Makes per-gap generation actually usable — without it, only one artifact could execute per task. | ⭐ Strengthened — proposal's Stage 5 ("connected for immediate use") is now multi-artifact, not single-artifact. |
| **Lead-with-Subagents** | When ≥2 AGENT gaps exist, generates ONE lead + N subagent dicts. Lead delegates via `task()` tool. Each subagent runs in isolated context, returns only final result. | ⭐ Strengthened — directly addresses proposal's third tension (context economy) at the generated-capability level. |
| **Anti-Monolith Prompts** | Generator prompts include explicit instructions to produce step-specific artifacts, not monoliths that solve the entire task. SKILL execution prompt tells LLM to "produce ONLY the output described in the skill instructions." | 🟡 Refined — engineering response to observed LLM behavior (generators compute full answer in step 1, making decomposition pointless). |
| **Registry Threshold 0.6** | Proposal says "confidence threshold" without specifying a value. Implementation uses 0.6 (raised from 0.5 after false-match analysis causing T09/T14 failures). Based on best practices research (0.6-0.7 recommended for balanced precision/recall). | 🟡 Refined — proposal silent on value; we chose based on empirical data. |
| **Retry Loop with Feedback** | Proposal silent on retry mechanism. Implementation: MAX_ATTEMPTS=3, verification feedback injected into next generation prompt. Failed artifacts are regenerated with error context. | 🟢 Kept deviation — materially affects success rate. Without retry, single Tier 1 hit terminates generation. |
| **Three Execution Backends** | Proposal treats execution as undifferentiated. Implementation: MCP_TOOL → Docker no-LLM (0 calls), SKILL → constrained LLM (max 5 calls), AGENT → full DeepAgent (unbounded). Makes classifier routing decision have measurable runtime consequences. | ⭐ Strengthened — converts classifier from metadata tag to load-bearing routing decision. |
| **Network-Disabled Sandbox** | Proposal implies tools can make network requests (Tier 1 "missing external calls" check, Tier 3 "network disconnect" test). Implementation runs `--network none` by default; `--allow-network` flag enables `--network bridge` for opt-in network access. | ✅ Faithful — default is secure (`--network none`); `--allow-network` enables network when needed (opt-in via CLI `mint run --allow-network`). Tier 1 severity is dynamic (MEDIUM offline, HIGH online). |
| **`FailureReason.CHAIN_STEP_FAILED`** | Post-Phase-3 addition to evaluation schema for compositional chain failures. Original `FailureReason` enum didn't anticipate multi-step chain execution. | 🟡 Refined — schema evolution to support compositional execution. |
| **Context Engineering Mapping** | `docs/archive/context_engineering_mapping.md` maps Write/Select/Compress/Isolate primitives to all three artifact types across orchestration and execution layers. Not in proposal but directly addresses the thesis domain ("Context Engineering in LLM-Powered Multi-Agent Systems"). | ⭐ Strengthened — makes the proposal's third tension (context economy) concrete and measurable. |
| **DuckDB Metrics Store** | Proposal silent on metrics storage. Implementation uses DuckDB for invocation records, evaluation records, and longitudinal optimizer state. Load-bearing for Pillar 3. | ⭐ Strengthened — without it, no prune decisions, no convergence curves, no Chapter 6 analysis. |
| **LangFuse Observability** | Proposal silent on tracing. Implementation traces every LLM call. `backfill_token_costs.py` reads traces post-hoc → delivers the "token cost per task" metric directly. | ⭐ Strengthened — delivers one of six proposal metrics. |
| **Chain Data Flow Contract** | Proposal says "strictly typed JSON schemas representing the world state at each handoff point." Implementation enforces: first step reads raw files → JSON, middle steps JSON → JSON, last step JSON → requested format (default JSON). MCP_TOOL generator prompt describes this explicitly. | ✅ Faithful — directly implements proposal §3.4. |
| **Binary File Structural Fix** | Proposal silent on binary file handling. Implementation: step 0 gets all fixtures (broad matching), steps 1+ use name-aware matching only (fixture filename must overlap with parameter name). Prevents `.xlsx` being blindly assigned to unrelated params in downstream steps. | 🟡 Refined — engineering fix for observed failure mode (T11 `.xlsx` crash). |
| **Chain Step Diagnostic Logging** | Not in proposal. Sandbox wrapper logs `_all_inputs` keys, `_fixture_paths`, `_kwargs`, and result keys to stderr for each chain step. Enables debugging data flow issues without accessing ephemeral Docker containers. | 🟡 Refined — debugging infrastructure. |

---

## §12 Findings — Drift Corrections from Per-Pillar Audit Stream

Drift findings surfaced by the per-pillar audit stream in `docs/verification/` + the master plan §12 cluster (2026-04-16 / 2026-04-17 / 2026-04-18 sessions). Each finding is either landed-in-code or scheduled in the master plan TBD list. No silent drift remains.

| # | Finding | Source | Status | Where landed / scheduled |
|---|---|---|---|---|
| #10 | File-output fix: Option A (executor-level synthesis) pivoted to Option B (decomposer Rule 15 + `_ensure_file_writer_last_step` validator append deterministic MCP_TOOL writer as LAST subtask) | `docs/verification/02_pillar_1_jit_pipeline.md`; master plan §12 | ✅ **Resolved 2026-04-16** | `src/orchestrator/decomposition.py` + classifier file-writer exception (`_enforce_file_writer_mcp_tool`) |
| #11 | Classifier parent-task bias: AGENT-parent tasks collapsed writer subtasks into AGENT → silent file-output failure | `docs/verification/02_pillar_1_jit_pipeline.md` | ✅ **Resolved 2026-04-16** | Two-layer fix: prompt carve-out + Example G + `_enforce_file_writer_mcp_tool` post-classification override |
| #12 | AGENT `output_schema` compliance: runtime output drifts from decomposer's declared schema (CLI falls through to GenericResult `extra="allow"`) | `docs/verification/02_pillar_1_jit_pipeline.md` | ✅ **Resolved 2026-04-16** | Two-layer fix: dynamic Pydantic `response_format` via `_build_dynamic_result_model` + `CompositionalExecutor` runtime key validator. Closes Structured State Communication at 3 granularities. |
| #13 | Multi-file writers chained serially instead of parallel | `docs/verification/02_pillar_1_jit_pipeline.md` | ✅ **Resolved 2026-04-16** | `1e65978` — writer subtasks now parallelized in `_ensure_file_writer_last_step`. Unit test previously asserted the broken serial behavior verbatim (tests-codifying-bugs pattern). |
| #14 | Pillar 2 verification retry loop: fail-open → fail-closed | `docs/verification/03_pillar_2_verification.md` | ✅ **Resolved 2026-04-16** | `src/orchestrator/verification_node.py` — 3× retry + sticky `verification_succeeded` flag + CLI exit code 4. Part of three-layer strict-gate bundle `f12dbd2`. **Scope:** the retry loop is fail-closed; two narrow fail-open branches on unexpected internal exceptions (gate-code errors, adversarial non-timeout crashes) are kept by design and disclosed in `03_pillar_2 §7`. |
| #15 | Pillar 1.1 decomposition single-attempt + `first_sentence[:200]` fallback → 3× retry + fail-closed | `docs/verification/02_pillar_1_jit_pipeline.md` | ✅ **Resolved 2026-04-16** | `src/orchestrator/decomposition.py` — sticky `decomposition_succeeded` flag + CLI exit code 2. |
| #16 | Pillar 1.3 classification single-attempt + silent MCP_TOOL default → 3× retry + fail-closed | `docs/verification/02_pillar_1_jit_pipeline.md` | ✅ **Resolved 2026-04-16** | `src/orchestrator/classifier.py::classify_gap` — sticky `classification_succeeded` flag + CLI exit code 5. |
| #17 | T16 AGENT reasoning failure: prompt ambiguity (pre-onset vs post-onset) + AGENT symptom bias | `docs/verification/02_pillar_1_jit_pipeline.md` | ✅ **Resolved 2026-04-16** | `b0ff872` — narrow prompt anchor (`>= onset_time`). `symptoms-are-not-a-hint` clause deliberately dropped before landing so Chapter 6's AGENT characterization keeps its symptom-bias signal. |
| #18 | `--allow-network` dead-flag trace: CLI accepts the flag but `sandbox_execute` always defaults to `--network none` | `docs/verification/03_pillar_2_verification.md` | ✅ **Resolved 2026-04-18 (partial closure)** | Step 14.12 landed: `--allow-network` threaded end-to-end through `TaskState` → pipeline/optimizer → verification_node → `gate.verify(artifact, allow_network=...)` → `sandbox_execute(--network bridge)`. `mint config set network.allow` persistent toggle + live-API integration test (`scripts/test_allow_network.py`). Tier 2 adds a `needs_network`-gated differential probe for F2 silent-fallback detection. **Partial closure scope:** the `needs_network` schema field exists and the Tier 2 probe consumes it, but the generator does not auto-set it — so the probe is dormant in the evaluated eval. Classifier/generator auto-flagging + a network-dependent testbed task are Chapter 7 future work. Full prose in master plan §11.10 post-landing refinement. |
| M2 | Mock contamination rate: `human_label` field defined but no audit, no reader | `docs/verification/05_evaluation_strategy.md` | ⏳ **Scheduled — Step 18.10** | Master plan row 6.10. ~3-5 h: stratified sample (30 × 3 JIT conditions = 90), audit CLI, `print_contamination_rate()` with Clopper-Pearson CI. |
| M5 | Time to first correct execution: `attempt_latency_ms` is total wall-clock (OK for successful runs; wrong for failed runs) | `docs/verification/05_evaluation_strategy.md` | ⏳ **Scheduled — Step 18.9** | Master plan row 6.9. ~15 min (helper already exists at `analyze_results.py:761`; needs WHERE filter narrowed to `gate_passed AND output_in_range AND NOT is_pilot` + p95/stdev additions). |
| M3 scope | Token cost measures runtime, not definition-string (proposal literal) | `docs/verification/04_pillar_3_granularity_optimizer.md` | ✅ **Resolved 2026-04-18** | Step 18.6 landed: `definition_token_cost: int` field on `CapabilityDescriptor` (tokenized with `tiktoken` gpt-4 encoding at registration time), wired into all five registration touch points (`seed_registry`, `ToolLoader.register_tool`, `loading_node`, `_register_artifact_directly`, `SkillRegistry.add`). ChromaDB round-trip with default=0 fallback for pre-18.6 rows. Idempotent `scripts/backfill_definition_token_costs.py` covers both CLI and eval stores. Runtime cost remains the primary U(c) driver; definition-string cost reported side-by-side in Chapter 6. Full disclosure in master plan §11.12 including per-type serialization scope. |
| T3 comp-loss | Context economy — compression-loss half of T3 not measured | `docs/verification/01_bootstrapping_problem.md` | ⏳ **Scheduled — Step 13.8** | Master plan row 6.8. ~1.5-2 h: `scripts/analyze_compression_loss.py` classifies chain failures into missing-key vs wrong-value vs exception vs timeout vs gate-rejection. Reader over eval output. |

**All addressable drift is either landed or scheduled.** Non-addressable findings are Chapter 4/5/7 disclosure items tracked in master plan §11.x and §12.

---

## Summary: Verdict Counts

| Verdict | Count | Description |
|---|---|---|
| ✅ Faithful | 30 | Matches proposal (Pillar 3 Split/Merge/KL + Eval Strategy Gini resolved; +2 from Step 14.12 and Step 18.6 landing 2026-04-18) |
| ⭐ Strengthened | 10 | Goes beyond proposal, improves thesis claims (+1: atomic verify-or-rollback on Pillar 3 ops, pre-reg finding #3) |
| 🟢 Kept deviation | 5 | Deliberate divergence with justification (+1: pinning exception in `prune()`, pre-reg finding #1) |
| 🟡 Refined | 9 | Additive/defensive, no thesis impact (+3: Jaccard over I∪O pre-reg #2, coverage "handled" semantics pre-reg #5b, reasoning-effort inconsistency pre-reg #7) |
| ⚠ Scope-limited / Empirical limitation | 3 | NEW verdict category for pre-registered findings that surface scope boundaries rather than drift: MCP_TOOL-only coverage (pre-reg #5c), wrong-output-requires-oracle (pre-reg #5d), semantic correctness verification out-of-scope (pre-reg #6a). All three have Chapter 4 disclosure text drafted in master plan §12. |
| 🔴 Drift | 0 | **All Pillar 3 and §12 code-level drift resolved 2026-04-16 / 2026-04-17 / 2026-04-18.** Remaining TBD items (Steps 18.9 / 18.10 / 13.8) are scheduled extensions, not drift. |
| ⏳ Scheduled | 3 | Pre-Chapter-6 additive work routed through master plan TBD list (18.9 M5 filter, 18.10 M2 audit, 13.8 compression-loss analyzer). Steps 14.12 and 18.6 landed 2026-04-18. |
| ❌ Missing | 0 | Prior "Gini / KL / mock audit" entries either ✅ resolved (Gini, KL) or ⏳ scheduled (mock audit as Step 18.10). |

### ✅ Done (no further code work before eval):
- ✅ All 5 pipeline stages (decomposition → gap detection → classification → generation → integration)
- ✅ Per-gap classification and generation
- ✅ Compositional execution with workspace state passing
- ✅ Three-tier verification gate with cost ladder + fail-closed retry contract (§12 #14); two narrow fail-open branches on unexpected internal exceptions disclosed in `03_pillar_2 §7`
- ✅ Granularity optimizer with exact proposal formula/thresholds, 9/9 type×operation matrix
- ✅ Pillar 3 atomic verify-and-register-or-rollback (Steps 5-8)
- ✅ Coverage verifier for MCP_TOOL split (Step 7)
- ✅ Four baselines implemented and isolated; 5th `LONGITUDINAL` sequencing condition added
- ✅ Structured State Communication at 3 granularities (§12 #12): task-level OutputContract, subtask-level chain validation, generation-level dynamic response_format
- ✅ Three-layer strict-gate pattern (§12 #14/#15/#16): decomposition / classification / verification all 3× retry + sticky flag + dedicated CLI exit code
- ✅ Step 18.5 Gini + Core-Work Classification Accuracy (`invocation_events` persistent log)
- ✅ Step 18.7 utility-score KL longitudinal analyzer + `cycle_utility_scores` + `cycle_events`
- ✅ Step 18.8 distribution-shift scripts + three-tier adaptation verdict (Pillar 3 RQ #3)
- ✅ Step 12.5 Tier 1.5 sandbox import probe (cost optimization)
- ✅ Step 14.12 `--allow-network` end-to-end plumbing + Tier 2 differential probe (partial closure: `needs_network` auto-flagging deferred to Ch 7)
- ✅ Step 18.6 Definition-string M3 token cost side-measurement (`tiktoken` + backfill, reported alongside runtime cost; runtime still drives U(c))
- ⭐ Lead-with-subagents for AGENT context isolation
- ⭐ Compositional executor for multi-artifact chain execution
- ⭐ Context engineering mapping (Write/Select/Compress/Isolate)
- ⭐ Step 14.6 task-level output contract check (orthogonal to Pillar 2)
- 🟡 Registry threshold tuned to 0.6
- 🟡 Anti-monolith prompts across all generator types

### ⏳ Scheduled (pre-Chapter-6, master plan TBD):
- **Step 18.9** (~15 min) — M5 time-to-first-correct filter narrowing (helper already exists, needs `gate_passed AND output_in_range` filter + p95/stdev); Evaluation Strategy audit
- **Step 18.10** (~3-5 h: ~1 h tooling + 2-4 h human audit) — M2 mock contamination manual audit + analyzer reader; Evaluation Strategy audit
- **Step 13.8** (~1.5-2 h) — Context-economy compression-loss analyzer; Bootstrapping Problem audit

### ⚠️ Open questions (pre-eval):
- ~~SKILL testbed coverage is weak~~ → ✅ Resolved: T04, T12, T14, T15 prompts strengthened with policy/rules language; T20 added as pure-SKILL task. 6 JIT SKILL tasks total (T04/T11/T12/T14/T15/T20).
- ~~AGENT coverage: 2/5 genuine, 2/5 borderline~~ → ✅ Resolved: T08, T10, T13 prompts strengthened with iterative/adaptive investigation requirements. 6 JIT AGENT tasks total (T08/T10/T13/T16/T17/T19).
- ~~JSON-only output format~~ → ✅ Resolved: B3 file output support implemented (ChainResult.output_files, CLI file saving, file_validator on TaskDefinition).
- ~~Network-disabled sandbox~~ → ✅ **Resolved 2026-04-18 (partial closure).** Step 14.12 threaded `--allow-network` end-to-end from CLI through `TaskState` → pipeline/optimizer → verification_node → `gate.verify` → `sandbox_execute(--network bridge)`. Live-API integration test + persistent `mint config set network.allow` toggle shipped. Tier 2 `needs_network`-gated differential probe added for F2 silent-fallback detection. Partial-closure scope: generator does not auto-flag `needs_network=True`, so the Tier 2 probe is dormant in Chapter 6; classifier/generator auto-flagging + a network-dependent testbed task are Ch 7 future work.
- ~~Tier 1 `missing_external_calls` severity~~ → ✅ Resolved: dynamic severity (MEDIUM offline, HIGH with `--allow-network`).
- Decomposer non-determinism (rules 10/11 ignored ~50% of the time) — accepted, document as Ch 6 finding
- Binary file handling depends on LLM compliance (T11 fails) — accepted, document as limitation
