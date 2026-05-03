# Pillar 3 — Granularity Optimizer — Implementation Alignment Audit

**Scope:** `docs/thesis_proposal.md` §Pillar 3 lines 155–196 (Problem, Evolutionary Feedback Loop, Formal Framework, Key Research Questions).
**Audited directories:** `sandbox/`, `scripts/`, `src/`, `tools/`.
**Date:** 2026-04-18.
**Method:** File-by-file rundown of every source file in the four listed directories, mapped against the four metrics the proposal names (reuse, success, token cost, maintenance), the three evolutionary operations (split, merge, prune), the utility formula U(c), and the three research questions (different from human-designed? cycles to converge? stable under distribution shift?). Every item that extends beyond the proposal wording is flagged as IMPROVEMENT or DIVERGENCE and cross-linked to its master-plan disclosure.

---

## 1. What the proposal's Pillar 3 actually says

### Problem
After many task cycles the registry accumulates capabilities at arbitrary granularity levels — some too general (high reuse, inconsistent success), some too specific (perfect success, invoked once). Without optimization pressure the registry grows unboundedly while effective coverage stagnates.

### Four metrics (computed per capability after each cycle)

| Metric | Proposal wording |
|---|---|
| **M1. Reuse rate** | Fraction of recent tasks that invoked this capability (*"is it general enough to be useful?"*). |
| **M2. Success rate** | Fraction of invocations producing *verified correct output* (*"is it reliable?"*). |
| **M3. Token cost** | Context window tokens consumed by the capability's definition — schema for MCP tools, instruction text for Skills (*"is it efficient?"*). |
| **M4. Maintenance burden** | How often the capability has required regeneration, patching, or version updates (*"is it stable?"*). |

### Three evolutionary operations

| Op | Trigger | Action |
|---|---|---|
| **O1. Split** | High reuse + mediocre success, *"e.g. >60% reuse, <70% success"*. | Decompose into two or more specialized variants. Optimizer analyzes failure cases, identifies input subspaces where the capability breaks, generates targeted variants. |
| **O2. Merge** | ≥2 capabilities with *very low reuse (<5%)* **and** *high similarity* (measured by embedding distance of descriptions + overlapping input schemas). | Merge into a single broader capability. Merged version *"inherits the strongest behavioral patterns from each parent."* |
| **O3. Prune** | Zero invocations over a *configurable window* **and** *no declared dependencies*. | Retire from the active registry (*"archived, not deleted, for potential resurrection"*). |

### Formal framework

$$U(c) = \alpha \cdot \text{reuse}(c) \cdot \text{success}(c) - \beta \cdot \text{token\_cost}(c) - \gamma \cdot \text{maintenance}(c)$$

Objective: maximize $\sum_c U(c)$ subject to coverage constraints. *"Darwinian pressure: capabilities compete for registry space based on demonstrated value."* Over cycles, the registry converges toward a Pareto-optimal granularity distribution.

### Key research questions

| RQ | Proposal wording |
|---|---|
| **RQ1** | Does evolutionary optimization produce meaningfully different granularity distributions than human-designed skill libraries? |
| **RQ2** | How many task cycles are required for the registry to converge? |
| **RQ3** | Is the converged distribution stable under task distribution shift? |

---

## 2. Verdict at a glance

| Proposal claim | Realized? | Primary implementation surface |
|---|---|---|
| M1 Reuse rate — fraction of recent tasks | ✅ ALIGNED (literal) | `src/registry/metrics.py::reuse_rate` (window=100 tasks) |
| M2 Success rate — fraction of verified-correct invocations | ✅ ALIGNED | `src/registry/metrics.py::success_rate` (window=100 invocations) |
| M3 Token cost — tokens consumed by definition | ✅ ALIGNED with scope divergence | `src/registry/metrics.py::avg_token_cost` — measures **per-invocation** cost not **definition** cost; see §5 |
| M4 Maintenance burden — regenerate/patch frequency | ✅ ALIGNED | `src/registry/metrics.py::maintenance_burden` (event_type IN `regenerated`/`patched`, window_days=30) |
| O1 Split trigger: >60% reuse, <70% success | ✅ ALIGNED (literal) | `src/optimizer/loop.py::create_optimization_loop` — `split_reuse_threshold=0.6`, `split_success_threshold=0.7` |
| O1 Split mechanism: failure-case analysis → input-subspace variants | ✅ ALIGNED + IMPROVED | `src/optimizer/operations.py::_summarize_failures_for_prompt` feeds `MetricsStore.get_recent_failures` into per-type split prompts |
| O2 Merge trigger: <5% reuse + embedding distance + schema overlap | ✅ ALIGNED (both conjuncts) | `loop.py` — `merge_reuse_threshold=0.05`, cosine ≥ 0.5 from `registry.search`, `_schema_jaccard` ∈ [0.5, 1.0) |
| O2 Merge: *"inherits strongest behavioral patterns"* | ⚠️ PARTIAL (disclosed) | `_MERGE_ROBUSTNESS_HINT` in `operations.py` is a domain-general proxy; true per-parent success-metric selection is Chapter 7 (§12 finding #4) |
| O3 Prune: zero invocations + no declared dependencies + archive not delete | ✅ ALIGNED | `operations.py::prune` — cutoff via `metrics_store.invocations_since`, pinned-metadata protection, `registry.deactivate` (archive) |
| Utility formula U(c) = α·reuse·success − β·tokens − γ·maintenance | ✅ ALIGNED (literal) | `src/optimizer/utility.py::compute_utility` — defaults α=1.0, β=0.1, γ=0.1 |
| Objective: maximize ΣU(c) under coverage constraint | ✅ ALIGNED (observational by design) | U(c) is the **descriptive framework** for the Darwinian pressure the three operations implement, and the **convergence observable** for RQ2 (via KL on utility-score distributions). The triggers of split/merge/prune encode U(c)'s structure implicitly — they target exactly the regions of (reuse, success, token, maintenance) space where U(c) is suppressed. Darwinian selection is explicitly *not* global optimization; implementing an explicit ΣU optimizer would break the proposal's *"capabilities compete based on demonstrated value"* framing. See §11.11 disclosure |
| RQ1 Different from human-designed | ✅ ALIGNED | `scripts/analyze_longitudinal.py` seeded-vs-evolved Gini comparison + `compare_baselines` in `simulation.py` |
| RQ2 Cycles to converge | ✅ ALIGNED (literal) | `_compute_kl_divergence` in `loop.py` (online) + `analyze_longitudinal.py::_kl_divergence_between_snapshots` on utility-score snapshots (Step 18.7) |
| RQ3 Stable under distribution shift | ✅ ALIGNED | `scripts/run_longitudinal_shift.py` (Step 18.8) + `_detect_shift_point` + `_classify_adaptation` three-tier verdict |
| Atomic verification at every operation | ➕ IMPROVEMENT | `operations.py::verify_and_register_or_rollback` — Pillar 2 × Pillar 3 coupling; not in proposal |
| Coverage replay on split variants | ➕ IMPROVEMENT | `src/verification/coverage.py` (MCP_TOOL-only, disclosed scope) |
| Per-operation enable/disable gating | ➕ IMPROVEMENT | `src/schemas/optimizer_config.py::OptimizerConfig` (§12 finding #8) |

**Bottom line.** All three operations + four metrics + utility formula + three RQs are realized with faithful fidelity to the proposal. Two deliberate divergences are disclosed: (i) M3 token cost is measured as *per-invocation runtime token cost* rather than the proposal's *definition-string token cost*; (ii) the proposal's *"strongest behavioral patterns"* merge inheritance is approximated by prompt-level robustness hints pending Chapter 7 treatment. Three improvements strengthen the optimizer beyond the proposal spec: atomic verify-or-rollback at every operation (Step 5), coverage replay on split variants (Step 7, MCP_TOOL-only), and per-operation auto/on/off gating (§12 finding #8). No silent drift identified in this audit.

---

## 3. File-by-file rundown

Legend: **ALIGNED** = directly realizes a proposal claim. **IMPROVEMENT** = stronger mechanism than the proposal sketch. **DIVERGENCE** = diverges from the literal proposal wording (disclosed). **NEUTRAL** = infrastructure, does not directly bear on Pillar 3 claims.

### 3.1 `sandbox/` (1 file)

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `sandbox/Dockerfile` | Minimal base image used by the coverage verifier (Step 7) to replay a parent's recorded failure cases against split variants — same image as Tier 2/3. | O1 Split coverage replay. | NEUTRAL — shared with Pillar 2; no Pillar-3-specific surface. |

### 3.2 `src/optimizer/` (4 files) — THE CORE

This directory *is* Pillar 3. Every file here is directly in scope.

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `utility.py` (55 LOC) | `compute_utility(cap, store, α=1.0, β=0.1, γ=0.1)` evaluates the proposal's U(c) formula verbatim, pulling the four metrics from `MetricsStore.compute_granularity_metrics`. `compute_all_utilities(registry, store)` batches over active caps. Module docstring quotes the formula literally. | Formal framework. | ALIGNED (literal). Weights are function parameters with proposal-reasonable defaults; no silent rescaling. |
| `loop.py` (403 LOC) | LangGraph loop: `compute_metrics_node → identify_candidates_node → apply_operations_node → END`. Carries `OptimizerState` with `utility_scores`, `kl_divergence`, `previous_utility_scores` (persisted across pipeline invocations via pipeline closure). Online `_compute_kl_divergence(prev, curr)` computes KL(curr ‖ prev) on utility-score point masses after shift-to-non-negative + ε-smoothing + normalization. **Split trigger**: `reuse > 0.6 AND success < 0.7` across all three capability types (MCP_TOOL / SKILL / AGENT). **Merge trigger** (line 240 + 270–283): `reuse < 0.05` at both endpoints, cosine ≥ 0.5 from `registry.search`, `_schema_jaccard ∈ [0.5, 1.0)`, same-type only. `_schema_jaccard` uses *key-name* overlap (not types) — `input_schema ∪ output_schema` keys. **Prune** dispatched through `apply_operations_node` with `window_days=30`. | O1/O2/O3 triggers, RQ2 convergence, utility computation. | ALIGNED + one disclosed ceiling: `jaccard < 1.0` (exclusive) deliberately preserves diversity when two caps have identical schema shapes but possibly different internal logic (comment lines 264–269). |
| `operations.py` (1972 LOC) | Implements `prune`, `split`, `merge` + the shared `verify_and_register_or_rollback` helper. Per-type dispatch for split (`_split_tools`, `_split_skills`, `_split_agents`) and merge (`_merge_tools`, `_merge_skills`, `_merge_agents`). Split follows a five-phase atomic flow: **(1) build** variants from LLM output, **(2) gate.verify** each variant, **(3) coverage** replay (MCP_TOOL only), **(4) register** each variant via `_register_artifact_directly`, **(5) deactivate** parent. `_MERGE_ROBUSTNESS_HINT` + `_SPLIT_ROBUSTNESS_HINT` inject domain-general robustness guidance (library defaults, edge cases, error handling, no exception swallowing). `_summarize_failures_for_prompt` feeds `MetricsStore.get_recent_failures` into the split prompt. Prune honours `metadata.pinned == "true"` as proposal's *"declared dependencies"* proxy and calls `registry.deactivate()` (archive, not delete). `_MAX_AGENT_SUBAGENT_BUDGET = 8` ceiling on agent-split subagent count. | O1/O2/O3 mechanics; Pillar 2 × Pillar 3 coupling; failure-case analysis for split. | ALIGNED + IMPROVED. The `verify_and_register_or_rollback` helper is the key atomicity improvement: pre-2026-04-15 split/merge created metadata-only descriptors that bypassed the gate with a self-justifying comment (*"metadata-only, no verification needed"*), violating the proposal's Pillar 2 *"must pass all three tiers"* contract. Steps 5/6/7/8 corrected this. |
| `simulation.py` (615 LOC) | Synthetic registry generator + multi-cycle optimization harness + matplotlib visualization. `SimConfig(convergence_threshold=0.01, convergence_window=5)`. Uses a *histogram-based* KL for visualization (`_kl_divergence(p, q)` on `_scores_to_histogram` with adaptive range floor [-200, 5]) — a DIFFERENT KL than `loop.py`'s online point-mass KL. Module docstring (lines 79–89) explicitly notes both exist and serves different purposes. `compare_baselines()` runs with/without optimizer and overlays on plots for RQ1 visual comparison. Synthetic maintenance = 0 (no regeneration events in simulation); reuse = invocation-fraction; success = stochastic; token cost = Gaussian sample. | RQ1 visualization, RQ2 convergence visualization. | ALIGNED + DIVERGENCE (disclosed dual-KL). The thesis text must specify the **primary** convergence metric: Step 18.7 wired `analyze_longitudinal.py` to use the utility-score KL from real longitudinal data (proposal-literal), so the histogram-KL here is visualization-only. |
| `__init__.py` | Empty. | — | NEUTRAL. |

**Verdict.** The four-file core realizes every proposal mechanism. The only semantic nuances requiring thesis disclosure are (i) dual KL metrics (point-mass online vs histogram visualization; utility-score KL in the longitudinal analyzer is the proposal-literal primary) and (ii) the deliberate `jaccard < 1.0` exclusive upper bound.

### 3.3 `src/registry/` (3 files) — the metrics substrate

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `metrics.py` (514 LOC) | DuckDB-backed `MetricsStore`. Tables: `invocations` (with `input_args`, `actual_output` columns added via Step-4 forward migration for coverage replay), `capability_events`. **Four metric methods verbatim:** `reuse_rate(cap, window=100)` — *"among last 100 unique tasks, fraction that invoked cap"*; `success_rate(cap, window=100)` — *"among last 100 invocations, fraction succeeded"*; `avg_token_cost(cap, window=100)` — avg `token_cost` column from recent invocations; `maintenance_burden(cap, window_days=30)` — count of events where `event_type IN ('regenerated', 'patched')` divided by `window_days`. `compute_granularity_metrics` aggregates all four into a `GranularityMetrics` Pydantic. `get_recent_failures(cap, limit=10)` returns failed invocations with recorded inputs + outputs, including both error-cases and wrong-output-cases (2026-04-16 fix — previous query required `actual_output IS NOT NULL`, silently excluding error-cases). `invocations_since(cap, cutoff)` drives prune. `invocation_count_total()` drives the auto-optimizer threshold gate. | M1/M2/M3/M4 all four. | ALIGNED + one scope divergence on M3 (see §5). Windows are operationally sensible defaults, aligned with *"recent tasks"* / *"recent invocations"* in the proposal. |
| `store.py` | `SkillRegistry` — ChromaDB-backed; `deactivate(name)` flips `is_active=False` (archive, not delete) — used by prune + split parent-deactivation + merge parent-deactivation. `search(description, n_results)` returns embedding-similar capabilities with cosine scores for merge pairing. | O3 archive semantics, O2 embedding-distance measurement. | ALIGNED. |
| `evaluation_store.py` (564 LOC) | Step 18.5 / 18.7 / 18.8 persistence. Tables: **`invocation_events`** (Step 18.5 — Gini on capability reuse distribution), **`cycle_utility_scores`** (Step 18.7 — utility-score snapshots per task for proposal-literal KL convergence), **`cycle_events`** (Step 18.7 — per-cycle prune/split/merge/verification_rollback/distribution_shift event counts). Flushed by `tests/evaluation/cycle_flush.py::flush_task_cycle_state` after each task. | RQ1 (Gini), RQ2 (utility-score KL), RQ3 (shift-point event). | IMPROVEMENT. Not in proposal; necessary infrastructure for post-hoc longitudinal analysis. |

### 3.4 `src/schemas/` (6 files)

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `metrics.py` (73 LOC) | `InvocationRecord` (with Step-4 `input_args`/`actual_output` additions) and `GranularityMetrics` (aggregated four-metric tuple, bounded: reuse/success ∈ [0,1], token_cost ≥ 0, maintenance ≥ 0). | Four metrics transport. | ALIGNED. |
| `optimizer_config.py` (148 LOC) | `OptimizerConfig` — per-operation `Literal["auto", "on", "off"]` for prune/split/merge. `"auto"` fires when `invocation_count ≥ AUTO_OPTIMIZER_THRESHOLD` (20). Factories `all_on()` / `all_off()` / `from_run_optimizer_bool()` for back-compat with eval harness. `is_enabled(op, invocation_count, threshold)` is the gate. | Not in proposal. | IMPROVEMENT (§12 finding #8). Purely operational tuning; disclosed as such. Eval harness uses `all_on()` to preserve pre-finding semantics. |
| `capability.py` | `CapabilityDescriptor` — `is_active`, `capability_type`, `input_schema`, `output_schema`, `metadata` (pinned flag for prune), `domain`, `is_generic`. Metrics are stored separately in `MetricsStore`, not on the descriptor. | O2 schema Jaccard + O3 pinned protection. | ALIGNED (clean metadata/metrics separation). |
| `verification.py`, `task.py`, `evaluation.py` | Peripheral for Pillar 3. | — | NEUTRAL. |

### 3.5 `src/verification/coverage.py` — Pillar 2 × Pillar 3 boundary

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `coverage.py` (502 LOC) | Coverage verifier for split (Step 7). Replays parent's recorded `(input_args, actual_output, error_message)` tuples against each new variant. **Failure-mode-fix semantics, not correctness-verified:** error-case → variant handles iff replay runs without raising; wrong-output-case → variant handles iff runs AND output ≠ parent's `actual_output`; same-output-case → flagged `unverifiable`. **MCP_TOOL-only scope** — SKILL/AGENT return `CoverageReport(all_covered=True, status="unverifiable_type")` (vacuously passing). Docstring (lines 1–86) is an explicit declaration that this is **NOT a fourth Pillar 2 tier** — it is an orthogonal coverage cross-check, and that earlier master plan prose calling it "Tier 4" is superseded. The MCP_TOOL-only scope is justified by the Pillar 1 × Pillar 3 tension: the proposal's Split clause *"input subspaces where the capability breaks"* presupposes stable per-input failure semantics that only deterministic executors satisfy. | O1 Split mechanism — *"analyzes failure cases to identify the input subspaces where the capability breaks"* — operationalized as replay against recorded failures. | IMPROVEMENT (Step 7). Disclosure of MCP_TOOL-only scope + Pillar 1 × Pillar 3 tension is in master plan §12 finding #5. Chapter 4 must disclose. |

### 3.6 `src/orchestrator/pipeline.py`

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `pipeline.py` | Wires the optimizer subgraph after `loading_node` when `optimizer_config.any_enabled()` is True. Closure over `_prev_optimizer_scores` persists the previous cycle's utility scores across pipeline invocations, enabling online KL-divergence tracking across sequential tasks (the substrate for RQ2 convergence detection in a real longitudinal run). | Optimizer wiring + RQ2 substrate. | ALIGNED. |

Other orchestrator files are Pillar 1 surface (see `02_pillar_1_jit_pipeline.md`).

### 3.7 `src/cli/` (4 files)

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `app.py`, `runner.py` | `mint` CLI surface: `--no-optimizer` / `--enable-optimizer` flags, `mint prune` / `mint disable` / `mint enable` commands, `mint config set optimizer.{prune,split,merge} {auto,on,off}` per-op config (§12 finding #8), `mint feedback --wrong` (§12 finding #6b). Wires the CLI to `OptimizerConfig`. | User-facing surface to O1/O2/O3. | ALIGNED + IMPROVEMENT (§12 finding #8 per-op config not in proposal). |
| `display.py`, `settings.py` | Rich UX + settings I/O. | — | NEUTRAL. |

### 3.8 `src/mcp_tools/`, `src/orchestrator/classifier.py`, etc.

Pillar 1 surface; NEUTRAL for Pillar 3.

### 3.9 `scripts/` (21 files)

Evaluation / diagnostic / test harnesses. Every Pillar-3-relevant script is listed.

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `run_longitudinal.py` (311 LOC) | 30-50 sequential tasks on a shared registry + metrics store. One snapshot restore at start, no reset between tasks — the optimizer accumulates real cross-task history. Writes to `data/eval/longitudinal.duckdb`. Uses `tests/evaluation/cycle_flush.py` to flush per-task utility scores + events (wired in Step 18.8 after a latent gap where `run_longitudinal.py` bypassed Step 18.7 flushes). | RQ2 empirical substrate. | ALIGNED (post-Step-18.8 wiring). |
| `run_longitudinal_shift.py` (432 LOC) | Two-phase longitudinal with `--shift-at` boundary. Phase A uses `--phase-a-tasks`, Phase B uses `--phase-b-tasks`. Writes a `distribution_shift` event to `cycle_events` at the boundary so the analyzer can locate it. Step 18.8 shipped this script; moved RQ3 out of Chapter 7 future work. | RQ3 empirical substrate. | ALIGNED. |
| `analyze_longitudinal.py` (772 LOC) | Proposal-literal longitudinal analyzer. Reads `cycle_utility_scores` when populated → computes utility-score KL via `_kl_divergence_between_snapshots` that mirrors `loop.py::_compute_kl_divergence`. Falls back to invocation-count KL on legacy DBs (`_table_exists` check). Five outputs: **(1) cycles-to-convergence** (first cycle where consecutive-snapshot utility KL stays below `--threshold` default 0.05 for `--min-consecutive` default 3); **(2) convergence curve** (CSV + optional PNG); **(3) per-type operation counts** from `cycle_events`; **(4) Gini trajectory**; **(5) seeded-vs-evolved Gini delta**. Step-18.8 extensions: `_detect_shift_point` + `_classify_adaptation` three-tier verdict (*"graceful"* ≤2×, *"sluggish"* 2×–5×, *"collapse"* >5× or never). | RQ1 (Gini delta), RQ2 (cycles-to-convergence), RQ3 (adaptation verdict). | ALIGNED (literal). Primary KL is utility-score-based (proposal-literal) per Step 18.7 follow-up; invocation-count KL retained as sensitivity series. |
| `analyze_results.py` | Produces Chapter 6 tables + Gini coefficient (Step 18.5) across the 4 baseline conditions (`static`, `no_verification`, `no_optimization`, `full`). `_gini_coefficient(invocation_counts: dict[str,int])` is the canonical helper; also re-imported by `analyze_longitudinal.py`. Exposes Core-Work Classification Accuracy (Step 18.5 — operational proxy for Pillar 1 RQ1). | RQ1 (Gini on reuse distribution); cross-baseline disaggregation. | ALIGNED (proposal §Metrics item on Gini is literal). |
| `step10_merge_candidates.py` (188 LOC) | Merge-candidate count experiment on regenerated CLI registry. Thresholds matched exactly to `loop.py`: `merge_reuse_threshold=0.05`, `cosine_threshold=0.5`, `jaccard ∈ [0.5, 1.0)`. Reports schema population rate + merge candidates + split candidates. Minimum success criterion: ≥1 merge candidate post-Step-2+3 schema extraction (up from 0 baseline). | Validates both O2 conjuncts are implemented + observable on real data. | ALIGNED. |
| `test_prune.py` (187 LOC) | Deterministic smoke test. Seeds three capabilities: pinned-zero-invocations (survives), recently-used (survives), idle-orphan (pruned). Asserts exactly one `pruned` event + `deactivate()` (archive) not deletion. | O3 archive-not-delete + pinned = *"declared dependency"* proxy. | ALIGNED. |
| `test_auto_optimizer.py` (706 LOC) | Pipeline-wiring integration test with real LLM calls. Asserts split/merge fire in realistic pipeline context; includes a `patch("src.verification.gate.verify")` rollback-scenario test that asserts atomic rollback when gate rejects a merge (Step 9.5b, 2026-04-17). | Pillar 2 × Pillar 3 atomic-rollback contract at pipeline layer. | ALIGNED + IMPROVEMENT (rollback is not in proposal). |
| `test_split_merge.py` (2753 LOC) | LLM-integration test matrix. Two merge scenarios: **CSV/TSV cleaners** (inherently Tier-3-hostile — `csv.Sniffer.has_header` is heuristic; exercises rollback path) + **list-stats** (deterministic; exercises happy path). Post-Step-8 (2026-04-17) adds `test_merge_mcp_tool` + `test_split_agent` + `test_merge_agent`, closing the Pillar 3 integration matrix to 9/9 (MCP_TOOL × SKILL × AGENT × {split, merge, prune}). | Per-type O1/O2 coverage validation. | ALIGNED. |
| `backfill_token_costs.py` (655 LOC) | Backfills M3 token-cost metric from LangFuse traces into evaluation DuckDB for post-hoc analysis. Maps GENERATION observations to evaluation runs by timestamp, falls back to per-condition averages when exact mapping is unavailable. Static condition = 0 tokens (no LLM calls); retrieval tasks T01–T03 = 0 tokens. | M3 instrumentation (see §5 divergence disclosure). | IMPROVEMENT (operational necessity, not in proposal). |
| `test_prune.py`, `test_split_merge.py`, `test_auto_optimizer.py` | (covered above) | — | — |
| `test_feedback_split.py` | §12 finding #6b: wrong-output feedback drives Pillar 3 split → gate → registration chain. | O1 triggered from user feedback, not just metric thresholds. | IMPROVEMENT (feedback pathway not in proposal). |
| `test_mint.py`, `test_pipeline.py`, `test_execution_backends.py`, `test_cli_settings.py`, `test_registry_matching.py`, `test_verification.py`, `run_pilot.py`, `analyze_context_utilization.py`, `migrate_business_domain_labels.py`, `validate_contract_extractor.py` | Pillar 1/2 surface or maintenance scripts. | — | NEUTRAL for Pillar 3. |

**Verdict.** The script inventory gives full coverage of the three RQs (Step 18.5 Gini + Step 18.7 utility-score KL + Step 18.8 shift adaptation verdict), the per-type integration matrix (Steps 6/8 × test_split_merge.py 9/9), and the atomic-rollback contract at the pipeline layer (test_auto_optimizer.py). `backfill_token_costs.py` is the only operational piece that exists specifically because of the M3 scope divergence disclosed in §5.

### 3.10 `tools/` (2 files)

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `quick_validate.py`, `init_skill.py` | Agent-skill authoring + structural validation — used by Pillar 2 Tier 0. No direct Pillar 3 surface. | — | NEUTRAL for Pillar 3. |

---

## 4. Full findings table

Findings scored against M1–M4, O1–O3, the utility formula, and RQ1–RQ3. Column **Status**: `✅` aligned; `✅+` improved; `➕` additive feature; `⚠️` partial / disclosed divergence; `ℹ️` disclosed scope divergence.

| # | Area | Proposal wording | Implementation | Status | Where | Note |
|---|---|---|---|---|---|---|
| 1 | M1 Reuse rate | *"Fraction of recent tasks that invoked this capability"* | `COUNT(DISTINCT tasks invoking cap) / COUNT(DISTINCT tasks)` over last 100 unique tasks | ✅ | `src/registry/metrics.py::reuse_rate` (lines 116–137) | Literal implementation; 100-task window is operational. |
| 2 | M2 Success rate | *"Fraction of invocations producing verified correct output"* | `AVG(success)` over last 100 invocations | ✅ | `metrics.py::success_rate` (lines 139–154) | Success flag is set by executor after verification run. |
| 3 | M3 Token cost | *"Context window tokens consumed by the capability's **definition** — schema for MCP tools, instruction text for Skills"* | `AVG(token_cost)` over last 100 **runtime invocations** — proposal meant definition-string cost, implementation measures per-run cost | ⚠️ ℹ️ | `metrics.py::avg_token_cost` (lines 156–171) | **Scope divergence disclosed in §5.** Runtime cost is a strictly stronger signal — includes the definition-context cost plus any per-invocation overhead the capability induces. |
| 4 | M4 Maintenance | *"How often the capability has required regeneration, patching, or version updates"* | `COUNT(events WHERE event_type IN ('regenerated','patched')) / window_days` | ✅ | `metrics.py::maintenance_burden` (lines 173–187) | Version-update events are subsumed under `regenerated`; no separate `versioned` event type. |
| 5 | O1 Split trigger | *">60% reuse, <70% success"* (literal example) | `reuse_rate > 0.6 AND success_rate < 0.7`, all three capability types eligible | ✅ | `loop.py::identify_candidates_node` | Pre-2026-04-19 the predicate carried an additional `type != MCP_TOOL` clause — a residual from Iteration 4 when `operations.py::split` raised `ValueError` on MCP_TOOL. Step 8 (2026-04-16) landed per-type split dispatch (`_split_tools`) + coverage replay (Step 7) but didn't remove the symmetric loop-level filter; the symmetric merge filter was already lifted in B1 Step 3 (`6a8fc63`). Filter removed 2026-04-19 to restore autonomous-trigger parity across types and make `mint feedback --wrong` → split reachable for MCP_TOOL caps. |
| 6 | O1 Split — failure-case analysis | *"Analyzes failure cases to identify the input subspaces where the capability breaks"* | `_summarize_failures_for_prompt` reads `get_recent_failures(cap, limit=10)` (both error-case + wrong-output-case post-2026-04-16 fix) and injects into per-type split LLM prompt | ✅ | `operations.py::_summarize_failures_for_prompt` + `metrics.py::get_recent_failures` | The 2026-04-16 fix closed a silent bug where error-cases (actual_output=None) were excluded from the prompt. |
| 7 | O1 Split — variant generation | *"Generates targeted variants for each subspace"* | Per-type LLM generation (`_split_tools`/`_split_skills`/`_split_agents`) with robustness hints + schema propagation | ✅ | `operations.py` lines 608–680 + per-type helpers | Each variant is a real executable artifact (not metadata-only stub). |
| 8 | O1 Split — atomicity | (not in proposal) | Five-phase atomic flow: build → verify-all → coverage → register-all → deactivate-parent. If any phase fails, neither variants nor parent are touched. | ✅+ | `operations.py::split` + `_split_phase_b_verify_all` + `verify_and_register_or_rollback` | IMPROVEMENT — pre-2026-04-15 code was non-atomic and bypassed the gate with metadata-only stubs. |
| 9 | O1 Split — coverage | (not in proposal) | Replays parent's recorded failures against new variants; MCP_TOOL-only scope per Pillar 1 × Pillar 3 tension | ➕ | `src/verification/coverage.py` | IMPROVEMENT. MCP_TOOL-only scope disclosed in master plan §12 finding #5 + module docstring. |
| 10 | O2 Merge trigger | *"Very low reuse (<5%)"* | `reuse_rate < 0.05` at both endpoints | ✅ | `loop.py::identify_candidates_node` (line 240) | Literal. |
| 11 | O2 Merge — embedding distance | *"Embedding distance of their descriptions"* | `registry.search(description, n_results).score ≥ 0.5` (ChromaDB cosine, inverted from distance) | ✅ | `loop.py` (line 276) | `0.5` cosine threshold is operational — tuned via `step10_merge_candidates.py`. |
| 12 | O2 Merge — schema overlap | *"Overlapping input schemas"* | `_schema_jaccard(cap_a, cap_b) ∈ [0.5, 1.0)` on `input_schema ∪ output_schema` key names (not types) | ✅ | `loop.py::_schema_jaccard` (lines 110–138) + usage line 279 | Exclusive `< 1.0` upper bound preserves diversity — two caps with identical schemas but different internal logic must not be collapsed. Comment lines 264–269 documents this safety rule. |
| 13 | O2 Merge — *"strongest behavioral patterns"* | *"The merged version inherits the strongest behavioral patterns from each parent"* | `_MERGE_ROBUSTNESS_HINT` — domain-general prompt-level guidance (library defaults, edge cases, error handling, no exception swallowing) | ⚠️ | `operations.py::_MERGE_ROBUSTNESS_HINT` (lines 723–768) | PARTIAL — true per-parent success-metric selection on each behavior subspace is Chapter 7 (§12 finding #4). Disclosed in operations.py preamble. |
| 14 | O2 Merge — atomicity | (not in proposal) | Verify-and-register-or-rollback helper; parents deactivated only after merged artifact is registered | ✅+ | `operations.py::merge` + `verify_and_register_or_rollback` | IMPROVEMENT — same correction as Step 5 (atomic rollback). |
| 15 | O2 Merge — type dispatch | (proposal implicit) | `merge()` dispatches to `_merge_tools` / `_merge_skills` / `_merge_agents` based on all-same-type; cross-type raises ValueError (Chapter 7 future work) | ✅ | `operations.py::merge` (lines 1254–1270) | Cross-type merge is excluded; disclosed in docstring. |
| 16 | O3 Prune trigger | *"Zero invocations over a configurable window"* | `invocations_since(cap, datetime.now(UTC) - timedelta(days=window_days)) == 0`; `window_days=30` default, parameterizable per call | ✅ | `operations.py::prune` (lines 575–605) + call site `loop.py` line 327 | Literal. |
| 17 | O3 Prune — declared dependencies | *"No declared dependencies"* | `metadata.get("pinned") == "true"` → skipped | ✅ | `operations.py::prune` (line 595) | "Pinned" is the operational proxy for "declared dependency" — a user signal to preserve a capability regardless of invocation history. Validated by `scripts/test_prune.py`. |
| 18 | O3 Prune — archive not delete | *"Archived, not deleted, for potential resurrection"* | `registry.deactivate(cap.name)` flips `is_active=False` — descriptor retained | ✅ | `operations.py::prune` (line 600) + `SkillRegistry.deactivate` | Literal archive semantics. |
| 19 | Utility formula | $U(c) = \alpha \cdot \text{reuse} \cdot \text{success} - \beta \cdot \text{token\_cost} - \gamma \cdot \text{maintenance}$ | Line-for-line match in `compute_utility`; defaults α=1.0, β=0.1, γ=0.1 | ✅ | `src/optimizer/utility.py` (lines 12–33) | Weights are function parameters, not hardcoded — tunable per proposal's deployment-context note. |
| 20 | Coverage-constrained objective | *"Maximize ΣU(c) subject to coverage constraints"* | U(c) is **observational by design**: computed per-cap + per-cycle, stored in `OptimizerState.utility_scores`, used for (a) KL-divergence convergence tracking (RQ2) and (b) post-hoc longitudinal analysis. Operation *decisions* are driven by the proposal's own literal triggers (reuse > 0.6, success < 0.7, reuse < 0.05), which encode U(c)'s structure implicitly — they target the regions where U(c) is suppressed. Coverage is enforced locally (pinning + coverage verifier + atomic rollback). | ✅ | `loop.py::compute_metrics_node` + `operations.py` | ALIGNED with the proposal's Darwinian framing. *"Darwinian pressure"* is explicitly not global optimization — an omniscient ΣU maximizer would contradict *"capabilities compete based on demonstrated value."* Disclosed in §11.11. |
| 21 | Darwinian pressure | *"Capabilities compete for registry space based on demonstrated value"* | Prune + split + merge drive selection; utility score surfaces in `OptimizerState.utility_scores` and drives cross-cycle KL | ✅ | `loop.py::compute_metrics_node` + `apply_operations_node` | Realized via the three operations + utility tracking; qualitative. |
| 22 | RQ1 Different from human-designed | — | Seeded-vs-evolved Gini comparison on `invocation_events` (Step 18.5) + `compare_baselines()` in simulation | ✅ | `scripts/analyze_longitudinal.py` + `scripts/analyze_results.py::_gini_coefficient` + `simulation.compare_baselines` | Non-zero Gini delta under stabilization indicates evolutionary optimization is changing distribution. |
| 23 | RQ2 Cycles to converge | *"Measured by KL divergence between consecutive registry snapshots"* | Two-metric KL: online point-mass KL in `loop.py` (persistent across invocations); utility-score KL in `analyze_longitudinal.py` (proposal-literal post-Step 18.7). Convergence = consecutive-below-threshold (default 0.05 for 3 cycles). | ✅ | `loop.py::_compute_kl_divergence` + `analyze_longitudinal.py::_kl_divergence_between_snapshots` | Primary metric is utility-score KL (proposal-literal); invocation-count KL retained as sensitivity series. Thesis Chapter 6 must declare primary. |
| 24 | RQ3 Stable under shift | — | `run_longitudinal_shift.py` writes `distribution_shift` event; `analyze_longitudinal.py` `_detect_shift_point` + `_classify_adaptation` three-tier verdict (*"graceful"* ≤2×, *"sluggish"* 2×–5×, *"collapse"* >5×) | ✅ | Step 18.8 (2026-04-17) | Moved out of Chapter 7 future work into pre-eval scope. |
| 25 | *(additive)* | Pillar 2 × Pillar 3 atomic rollback | `verify_and_register_or_rollback` funnels every split-variant + merge through three-tier gate; parents deactivated only after success | ➕ | `src/optimizer/operations.py` (lines 71–172) | Extends *"must pass all three tiers"* from Pillar 1's generated capabilities to Pillar 3's evolved capabilities. |
| 26 | *(additive)* | Per-operation auto/on/off gating | `OptimizerConfig` — each of prune/split/merge independently gated; `"auto"` threshold against `AUTO_OPTIMIZER_THRESHOLD=20`; eval harness uses `all_on()` for back-compat | ➕ | `src/schemas/optimizer_config.py` + §12 finding #8 | Purely operational tuning parameter; not proposal mechanics. |
| 27 | *(additive)* | Feedback-driven split | `mint feedback --wrong` records an invocation as failed → triggers split candidate on next cycle | ➕ | `src/cli/app.py` + §12 finding #6b | Extends O1 trigger surface beyond metric thresholds alone. |
| 28 | *(additive)* | AST schema extraction | MCP_TOOL input/output schemas populated via AST extraction at registration → enables `_schema_jaccard` to produce non-trivial pairings on real registries | ➕ | `src/orchestrator/schema_extraction.py` + `step10_merge_candidates.py` validation | Without this, schema Jaccard returns 0.0 for all unannotated tools, making the second O2 conjunct vacuous. |
| 29 | *(additive)* | Step-4 forward migration | `invocations` table gains `input_args` + `actual_output` nullable columns (ALTER TABLE IF NOT EXISTS) for split coverage replay | ➕ | `src/registry/metrics.py` lines 33–44 | Idempotent; old rows get NULL. Necessary infrastructure for Step 7 coverage. |

---

## 5. Deliberate divergences — summary

The implementation diverges from the proposal's literal wording in exactly two places. Both are disclosed, neither is silent drift.

| Divergence | Proposal wording | Implementation | Why | Disclosure |
|---|---|---|---|---|
| **M3 Token cost scope** | *"Context window tokens consumed by the capability's **definition** — schema for MCP tools, instruction text for Skills"* | `avg_token_cost` averages the `token_cost` column recorded on each **runtime invocation**, not the definition-string size. | Runtime token cost is a strictly stronger signal: it includes the definition-context cost (every invocation loads the definition into the prompt) **plus** any per-invocation overhead the capability induces downstream. For MCP tools the definition-string cost is near-constant (the JSON-RPC schema is small), so the meaningful variance is in runtime behavior. `backfill_token_costs.py` exists specifically to fill this metric from LangFuse traces post-hoc when runtime instrumentation missed it. Chapter 4 should disclose this as "we measure the proposal's M3 as runtime cost rather than definition-string cost, yielding a strictly stronger efficiency signal". | `src/registry/metrics.py::avg_token_cost` docstring says *"Average token cost of the last N invocations"*; Chapter 4 or 5 must explicitly disclose the scope shift against the proposal's *"definition"* wording. |
| **Merge's *"strongest behavioral patterns"* inheritance** | *"The merged version inherits the strongest behavioral patterns from each parent."* | Domain-general robustness hints in the merge prompt (library defaults, edge cases, error handling, no exception swallowing) — not per-parent success-metric-driven pattern selection. | True per-parent per-subspace pattern selection requires observing which parent succeeds on which input subspace, then synthesising a merge that inherits each winning pattern. That's Chapter 7 work: it needs per-subspace success metrics that the MetricsStore doesn't yet record, and a merge prompt capable of pattern grafting rather than fresh synthesis. The robustness hints are the operational compromise that keeps merges from degrading relative to parents without claiming full inheritance. | `src/optimizer/operations.py::_MERGE_ROBUSTNESS_HINT` preamble (lines 686–722) + master plan §12 finding #4. |

Additive pieces that extend the proposal without diverging from it: atomic verify-or-rollback (`verify_and_register_or_rollback`), coverage verifier (`src/verification/coverage.py`, MCP_TOOL-only), per-operation auto/on/off gating (`OptimizerConfig`), feedback-driven split trigger (`mint feedback --wrong`), AST schema extraction, Step-4 invocation-table forward migration, three-tier shift-adaptation verdict. None contradict the proposal; all strengthen the realization of the three operations + three RQs.

---

## 6. Research-question instrumentation

| RQ | Proposal wording | Instrumentation |
|---|---|---|
| **RQ1** — Different from human-designed? | *"Does evolutionary optimization produce meaningfully different granularity distributions than human-designed skill libraries?"* | `scripts/analyze_longitudinal.py` reports cycle-1 Gini (seeded) vs final-cycle Gini (evolved); non-zero delta under stabilization = evolutionary optimization IS changing distribution. `simulation.compare_baselines()` overlays with/without optimizer curves visually. `analyze_results.py` `_gini_coefficient` across 4 baselines (`static` vs `full`). |
| **RQ2** — Convergence cycles? | *"How many task cycles are required for the registry to converge?"* | **Primary:** `analyze_longitudinal.py::_kl_divergence_between_snapshots` on `cycle_utility_scores` table (Step 18.7 wired this through `EvaluationStore` + `flush_task_cycle_state`). Mirrors `loop.py::_compute_kl_divergence`. Convergence = first cycle where consecutive-snapshot KL stays below `--threshold` (default 0.05) for `--min-consecutive` (default 3) cycles. **Secondary/sensitivity:** invocation-count KL on legacy DBs + histogram KL in `simulation.py`. |
| **RQ3** — Stable under shift? | *"Is the converged distribution stable under task distribution shift?"* | `scripts/run_longitudinal_shift.py` (Step 18.8) writes a `distribution_shift` event at `--shift-at`; `analyze_longitudinal.py::_detect_shift_point` locates it; `_classify_adaptation` emits a three-tier verdict based on (Phase-B-re-converge / Phase-A-converge) ratio: *"graceful adaptation"* (≤2×), *"sluggish"* (2×–5×), *"collapse"* (>5× or never). Synthetic-data sanity test verified 20-cycle shifted DB produces correct detection + graceful verdict. |

---

## 7. Loose ends / honest gaps

| Gap | Severity | Notes |
|---|---|---|
| M3 token cost measures runtime cost, not definition-string cost | Low (strictly stronger signal; disclosed in §5) | Chapter 5.2 must explicitly disclose. Disclosure text in master plan §11.12. **Optional pre-eval Step 11.12a** (~2–3h): add `definition_token_cost` on `CapabilityDescriptor` as a secondary measurement alongside runtime cost. Closes the literal-interpretation gap without changing U(c). Recommended. |
| Merge *"strongest patterns"* is proxied by domain-general hints | Low (disclosed in §5 + §12 finding #4) | Chapter 7 future work. Full disclosure text in master plan §11.13. Not recommended for pre-eval implementation — genuine research problem. |
| Coverage verifier restricted to MCP_TOOL | Disclosed | Pillar 1 × Pillar 3 tension — SKILL/AGENT nondeterminism breaks the stable-subspace assumption. Master plan §11.1 is the full disclosure text. |
| Global ΣU maximization is not explicit | Low (observational by design) | The proposal's *"Darwinian pressure"* framing is explicitly NOT global optimization. U(c) is the convergence observable; triggers encode its structure implicitly. Full disclosure in master plan §11.11. If a reviewer pushes hard, Step 11.11a (~1 day) would add a local ΣU-monotonicity gate in `verify_and_register_or_rollback`. Not recommended unless requested. |
| Two KL metrics coexist (point-mass online vs histogram simulation vs utility-score longitudinal) | Disclosure needed | Thesis Chapter 6 must declare utility-score KL (proposal-literal) as primary. The other two are visualization / sensitivity artifacts. Noted in `simulation.py` (lines 79–89) + `loop.py` (lines 76–81) + `analyze_longitudinal.py` (lines 90–135). |
| Dual prune / feedback / per-op-config improvements beyond proposal | None (additive) | Documented as §12 findings #6b, #8. Chapter 4 should list these under "operational deviations". |
| Chapter 4.5 disclosure for "three-axis cross-check": Pillar 2 gate (per-artifact) + coverage verifier (per-split-batch) + global ΣU (qualitative) | Open disclosure | The coverage verifier is **not** a fourth Pillar 2 tier — it is an orthogonal axis. `coverage.py` docstring is explicit; master plan §12 finding #5 is the Chapter 4 prose source. |

---

## 8. Citations

- `docs/thesis_proposal.md` §Pillar 3 lines 155–196 — source of the claims audited.
- `docs/MASTER_PLAN_2026_04_13.md` §12 pre-registered finding #5 (Pillar 1 × Pillar 3 tension + MCP_TOOL-only coverage scope), finding #4 (merge *"strongest patterns"* Chapter 7 disclosure), finding #6b (feedback-driven split), finding #8 (per-op optimizer config).
- `docs/MASTER_PLAN_2026_04_13.md` Steps 4/5/6/7/8 — B1 Pillar 3 main block (forward migration, atomic rollback helper, per-type merge, coverage verifier, per-type split).
- `docs/MASTER_PLAN_2026_04_13.md` Step 18.5 — Gini + Core-Work Classification Accuracy.
- `docs/MASTER_PLAN_2026_04_13.md` Step 18.7 — utility-score KL longitudinal analyzer.
- `docs/MASTER_PLAN_2026_04_13.md` Step 18.8 — distribution-shift script + adaptation verdict (RQ3).
- `CLAUDE.md` §Pillar 3: Type-Scoped Operations + §Three-layer strict-gate pattern.
- `MEMORY.md` §Step 18.7 + §Step 18.8 summaries.
- Prior audits in the same series: `docs/verification/01_bootstrapping_problem.md`, `docs/verification/02_pillar_1_jit_pipeline.md`, `docs/verification/03_pillar_2_verification.md`.
