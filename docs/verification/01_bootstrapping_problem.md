# Bootstrapping Problem — Implementation Alignment Audit

**Scope:** `docs/thesis_proposal.md` lines 9–21 (The Bootstrapping Problem section).
**Audited directories:** `sandbox/`, `scripts/`, `src/`, `tools/` (81 files, excluding `__pycache__` and data).
**Date:** 2026-04-17.
**Method:** File-by-file rundown mapped against the section's two structural claims: (1) the task-analysis → plan → gap-detection → generation pipeline, and (2) the three tensions it names (autonomy vs correctness, specificity vs generality, context economy).

---

## 1. What the proposal's Bootstrapping section actually says

The section makes exactly two structural claims:

**Claim A — The pipeline.** A self-developing MAS must:

1. Analyze a complex task,
2. Generate an action plan,
3. Identify the skills / tools / agents / MCP servers required,
4. Detect gaps between what exists and what is needed,
5. Generate the missing capabilities on the fly.

**Claim B — The three tensions.** The bootstrapping loop ("the system must be capable enough to build the capabilities it doesn't yet have") produces:

| Tension | What it says |
|---|---|
| **T1. Autonomy vs. correctness** | LLM agents silently inject mocks, hardcode responses, and create fallback paths that mask failures. More autonomy ⇒ harder correctness guarantee. |
| **T2. Specificity vs. generality** | Narrow skills are reliable but single-use; general skills are reusable but hallucinate on edges. No principled framework for the tradeoff. |
| **T3. Context economy** | Naive full-history passing hits token limits / adds noise; aggressive compression loses critical detail. |

The section concludes by promising that "a unified three-pillar design" addresses all three. The Bootstrapping section itself does **not** specify the pillars — that's downstream content. So this audit only judges whether the implementation **stages the pipeline** and **surfaces the three tensions**, not whether pillar internals match their own chapters.

---

## 2. Verdict at a glance

| Claim | Realized? | Primary implementation surface |
|---|---|---|
| A1. Task analysis | ✅ ALIGNED | `src/orchestrator/decomposition.py` (DeepAgent + retry) |
| A2. Action plan (as ordered SubTasks) | ✅ ALIGNED | `src/schemas/task.py::SubTask` + decomposition post-processing |
| A3. Identify required capability types | ✅ ALIGNED | `src/orchestrator/classifier.py` (per-gap MCP_TOOL/SKILL/AGENT) |
| A4. Gap detection | ✅ ALIGNED + IMPROVED | `src/orchestrator/gap_detection.py` + `src/registry/store.py` |
| A5. Generate missing capabilities | ✅ ALIGNED | `src/orchestrator/generators/{mcp_tool,skill,agent}.py` |
| T1. Autonomy vs correctness | ✅ ALIGNED + IMPROVED | `src/verification/*` (four tiers: 1 static, 1.5 import, 2 behavioral, 3 adversarial) |
| T2. Specificity vs generality | ✅ ALIGNED + IMPROVED | `src/optimizer/*` + evolutionary taxonomy |
| T3. Context economy | ⚠️ PARTIALLY ALIGNED | Typed Pydantic schemas, schema-extraction, lead-with-subagents. **Missing:** no measurement of compression-quality / information-retention loss (only input-token peak utilization). |

**Bottom line:** Claim A is fully realized end-to-end. Two of the three tensions (T1 + T2) are strongly addressed with documented improvements over the proposal. T3 is addressed *structurally* (typed contracts instead of prose) but the empirical *measurement* of context economy only captures peak utilization, not compression-induced loss — noted as the one partial gap.

---

## 3. File-by-file rundown

Legend: **ALIGNED** = directly realizes a Bootstrapping claim. **IMPROVEMENT** = realizes it with a mechanism stronger than the proposal sketch. **DRIFT** = diverges from the literal proposal (usually disclosed in master plan). **NEUTRAL** = infrastructure, does not bear on the Bootstrapping claims.

### 3.1 `sandbox/` (1 file)

| File | Role | Mapping | Alignment |
|---|---|---|---|
| `sandbox/Dockerfile` | Base image with `mcp` + `pydantic`; runtime deps resolved dynamically per capability via `uv`. | A5 (sandboxed integration); T1 (isolation prevents silent network fallbacks). | ALIGNED |

### 3.2 `src/orchestrator/` — Claim A (the pipeline)

| File | Role | Mapping | Alignment |
|---|---|---|---|
| `__init__.py` | Package marker. | — | NEUTRAL |
| `pipeline.py` | 9-node LangGraph wiring: decomposition → gap detection → classification → generation → verification → loading → composition → (optional) optimizer. | A1–A5 end-to-end | ALIGNED |
| `decomposition.py` | DeepAgent-backed task decomposition; 3-attempt retry + sticky-flag + fail-closed; validates chain coherence; `_ensure_file_writer_last_step` validator. | A1 + A2 | ALIGNED |
| `gap_detection.py` | ChromaDB semantic search with dual-query retrieval (slug + auxiliary description) + evolutionary taxonomy domain filter with `include_generic=True` bypass. | A4 | IMPROVEMENT — dual-query + evolutionary domain filter go beyond the proposal's "hierarchical embeddings" sketch. |
| `classifier.py` | Per-gap LLM classification into MCP_TOOL / SKILL / AGENT; 3-attempt retry + fail-closed; `_enforce_file_writer_mcp_tool` structural override. | A3 + T1 (fail-closed rather than silent MCP_TOOL default) | ALIGNED |
| `composition.py` | `CompositionalExecutor`: topological chain execution with per-step workspace threading; runtime enforcement of `output_schema` on AGENT/SKILL steps (§12 finding #12 layer 2). | T3 (structured state between chain steps) | ALIGNED |
| `schema_extraction.py` | AST-based I/O schema extraction for MCP_TOOL artifacts; populates `CapabilityDescriptor.input_schema / output_schema`. | T3 + T2 (schemas feed merge Jaccard similarity) | IMPROVEMENT — makes merge eligibility schema-aware. |
| `taxonomy.py` | `DomainTaxonomy`: evolutionary, file-backed domain registry growing one category at a time; `is_generic` flag bypasses strict domain filter. | T2 | IMPROVEMENT — proposal sketches a fixed hierarchy; this is evolutionary + genericity-aware. |
| `loading_node.py` | Dynamic importlib loading + smoke test; registers all three artifact types; forwards `domain` + `is_generic`. | A5 | ALIGNED |
| `verification_node.py` | Retry loop orchestrating the three-tier gate; sticky `verification_succeeded` flag; regenerate-on-strike (§12 finding #14). | T1 | ALIGNED |
| `tool_loader.py` | `importlib` dynamic MCP tool import in sandboxed subprocess; no persistent FastMCP server. | A5 | ALIGNED (disclosed simplification vs. proposal's MCP server; full MCP deployment is Chapter 7 future work.) |
| `tracing.py` | Shared LangFuse emission helper. | — | NEUTRAL |
| `output_contract_resolver.py` | Authored `OutputContract` shortcut + LLM fallback extractor for free-form prompts (Step 14.6). | A1 + T3 | ALIGNED |

### 3.3 `src/orchestrator/generators/` — Claim A5 (generation)

| File | Role | Mapping | Alignment |
|---|---|---|---|
| `__init__.py` | Package marker. | — | NEUTRAL |
| `mcp_tool.py` | FastMCP code generation; compile-check + one-shot repair; `pip_packages` declared at generation time. | A5; T1 (compile-check is first-line defense). | ALIGNED |
| `skill.py` | SKILL.md + YAML frontmatter generation; runs `tools/quick_validate.py` as structural pre-check. | A5 | ALIGNED |
| `agent.py` | System-prompt + model-config generation; lead-with-subagents composition for ≥2 AGENT gaps. | A5; T3 (lead-with-subagents collapses context). | ALIGNED |
| `utils.py` | Shared helpers: task-context builder, gap slugification, schema context from decomposer output. | T3 (schema context threading) | ALIGNED |

### 3.4 `src/orchestrator/prompts/` — prompt templates

| File | Role | Mapping | Alignment |
|---|---|---|---|
| `__init__.py` | Package marker. | — | NEUTRAL |
| `decomposition.py` | Decomposer system + user prompts; Rule 15 (file-writer last step). | A1 + A2 | ALIGNED |
| `classifier.py` | Per-gap + legacy task-level classification prompts; file-writer carve-out. | A3 | ALIGNED |
| `mcp_tool_generator.py` | Generator prompt with first-step vs downstream-step divergence + repair template. | A5 | ALIGNED |
| `skill_generator.py` | Skill generator + repair prompts. | A5 | ALIGNED |
| `agent_generator.py` | Single-agent + lead-with-subagents prompt templates. | A5 | ALIGNED |

### 3.5 `src/verification/` — Tension T1 (autonomy vs correctness)

| File | Role | Mapping | Alignment |
|---|---|---|---|
| `__init__.py` | Package marker. | — | NEUTRAL |
| `gate.py` | Fail-fast tier orchestration; `_extract_python_fences` feeds prose-embedded code back through AST scanner for SKILL/AGENT. | T1 | ALIGNED |
| `static_scanner.py` | Tier 1: AST + regex for mock imports, hardcoded returns (≥3-element literals), bare excepts, lorem ipsum, random-seed abuse. | T1 (directly mirrors the proposal's §Tier-1 pattern table). | ALIGNED |
| `import_check.py` | Tier 1.5 sandbox import probe; Docker `python -c "import tool"` for MCP_TOOL, `compile()` for SKILL/AGENT fences. | T1 | IMPROVEMENT — cost-optimization layer not in proposal; **disclosed** as such in master plan §Step 12.5 (same coverage as Tier 2 `ModuleNotFoundError`, cheaper). |
| `behavioral.py` | Tier 2: six probe categories (nonexistent entity, consistency, boundary, timeout, latency variance, input-ignoring). | T1 | ALIGNED |
| `adversarial.py` | Tier 3: DeepAgent red-team running hidden-demo-mode / network-disconnect / ground-truth attacks in sandbox; Step 14.11 fail-closed on timeout. | T1 | IMPROVEMENT — fail-closed timeout behavior is stronger than the proposal's implicit "probe and report." |
| `coverage.py` | Post-split verifier: replays parent failure cases against variants (MCP_TOOL only; nondeterministic types deferred to Chapter 7). | T2 + T1 | IMPROVEMENT — explicit cross-pillar bridge. |
| `sandbox_tool.py` | Docker execution primitive; two-phase (pip install → `--network none` run); LLM-based import→PyPI resolution + cache. | A5 + T1 | ALIGNED |

### 3.6 `src/optimizer/` — Tension T2 (specificity vs generality)

| File | Role | Mapping | Alignment |
|---|---|---|---|
| `__init__.py` | Package marker. | — | NEUTRAL |
| `utility.py` | Exact formula `U(c) = α·reuse·success − β·token_cost − γ·maintenance` from proposal §Pillar 3. | T2 | ALIGNED |
| `operations.py` | `prune` / `split` / `merge` with per-type dispatch (9/9 matrix); atomic verify-and-rollback per variant. | T2 | ALIGNED |
| `loop.py` | LangGraph loop: compute metrics → candidate selection → apply → KL-convergence event emission. | T2 | ALIGNED |
| `simulation.py` | Synthetic-registry harness for convergence / KL divergence validation in isolation. | T2 | ALIGNED |

### 3.7 `src/registry/` — "what already exists" half of A4

| File | Role | Mapping | Alignment |
|---|---|---|---|
| `__init__.py` | Package marker. | — | NEUTRAL |
| `store.py` | ChromaDB registry with hierarchical embedding document (`domain > type > name: summary`); `$or` filter honoring `is_generic`. | A4 + T2 | ALIGNED |
| `metrics.py` | DuckDB invocation log (`input_args`, `actual_output`, token_cost, latency) + granularity metric queries. | T2 + evidence base for split replay | ALIGNED |
| `evaluation_store.py` | Evaluation-specific DuckDB: `evaluation_records`, `invocation_events`, `cycle_utility_scores`, `cycle_events`. | Evaluation infrastructure for all three tensions | NEUTRAL (measurement plumbing, not claim-bearing itself). |

### 3.8 `src/schemas/` — Tension T3 (context economy via typed contracts)

| File | Role | Mapping | Alignment |
|---|---|---|---|
| `__init__.py` | Barrel export. | — | NEUTRAL |
| `task.py` | `TaskState`, `SubTask` (with `requires_upstream_keys`, `output_schema`), `GeneratedArtifact`, `OutputContract`. | T3 | ALIGNED — core mechanism replacing prose handoffs. |
| `capability.py` | `CapabilityDescriptor` with `input_schema`, `output_schema`, `domain`, `is_generic`. | A4 + T2 + T3 | ALIGNED |
| `verification.py` | `VerificationReport` / `VerificationFinding` with `tier`, `severity`, `category`. | T1 (structured, not prose, gate output). | ALIGNED |
| `metrics.py` | `InvocationRecord`, `GranularityMetrics`. | T2 + split-replay evidence. | ALIGNED |
| `evaluation.py` | `EvaluationRecord` + compositional fields. | Measurement plumbing | NEUTRAL |
| `optimizer_config.py` | Per-operation `"auto" \| "on" \| "off"` gating config. | T2 | IMPROVEMENT — not in proposal; addresses §12 finding #8 ergonomics gap. |

### 3.9 `src/cli/` — operator surface

| File | Role | Mapping | Alignment |
|---|---|---|---|
| `__init__.py` | Package marker. | — | NEUTRAL |
| `app.py` | Typer CLI: `run`, `plan`, `list`, `inspect`, `feedback --wrong`, `config set/show`, etc. | Exercises A1–A5 loop + user feedback (§12 finding #6b). | ALIGNED |
| `runner.py` | Pipeline invocation + Rich progress + per-invocation `--skip-verification` / `--enable-optimizer` flags. | All three pillars with ablation knobs. | IMPROVEMENT — enables user-driven pillar ablation; aligned with master plan's baseline grid. |
| `display.py` | Rich rendering. | — | NEUTRAL |
| `settings.py` | Atomic JSON settings for per-op optimizer config. | T2 | NEUTRAL (persistence for a T2 mechanism). |

### 3.10 `src/mcp_tools/` — reference tool

| File | Role | Mapping | Alignment |
|---|---|---|---|
| `__init__.py` | Package marker. | — | NEUTRAL |
| `fibonacci.py` | Hand-written FastMCP tool used as seed + verification target. | — | NEUTRAL |

### 3.11 `src/config.py`

| File | Role | Mapping | Alignment |
|---|---|---|---|
| `config.py` | `DEFAULT_LLM_MODEL`, `SANDBOX_IMAGE`, thresholds, retry limits. | — | NEUTRAL |

### 3.12 `tools/` (skill utilities)

| File | Role | Mapping | Alignment |
|---|---|---|---|
| `tools/init_skill.py` | Scaffolds a new SKILL.md directory. | — | NEUTRAL |
| `tools/quick_validate.py` | Tier 0 structural SKILL validator; runs as pre-check inside skill generator. | T1 | ALIGNED |

### 3.13 `scripts/` — evaluation, smoke tests, diagnostics

| File | Role | Mapping | Alignment |
|---|---|---|---|
| `run_pilot.py` | Main 400-record evaluation harness across 4 baseline conditions × 20 tasks × 5 runs. | A1–A5 + T1 + T2 end-to-end | ALIGNED (CORE empirical surface). |
| `run_longitudinal.py` | 30–50 sequential tasks without snapshot reset; tests optimizer on accumulated history. | T2 (RQ #1, #2) | ALIGNED |
| `run_longitudinal_shift.py` | Two-phase distribution-shift experiment; cycles event `distribution_shift`. | T2 (RQ #3 — distribution stability) | IMPROVEMENT — Pillar 3 RQ #3 moved out of Chapter 7 future work. |
| `analyze_results.py` | Binomial CIs, Fisher's exact, Holm-Bonferroni; adds Gini + classification-accuracy (Step 18.5). | A1–A5 end-to-end success + T2 Gini | ALIGNED |
| `analyze_longitudinal.py` | Utility-score KL primary + invocation-count KL sensitivity; per-type op counts; shift-detection + three-tier adaptation verdict. | T2 convergence + RQ #2/#3 | IMPROVEMENT — proposal-literal utility-KL, not a proxy. |
| `analyze_context_utilization.py` | Peak input-token % per agent/task from LangFuse. | T3 | PARTIAL — measures utilization peak, not compression-loss. |
| `backfill_token_costs.py` | LangFuse GENERATION → DuckDB token_cost backfill. | T3 measurement | ALIGNED (with disclosed drift: LLM-call cost only, not full pipeline cost). |
| `migrate_business_domain_labels.py` | One-off domain migration for Phase 2 Step 3.5 taxonomy experiment. | T2 | NEUTRAL (one-off migration). |
| `step10_merge_candidates.py` | Populate schemas + surface merge candidates on CLI registry. | T2 | ALIGNED |
| `test_pipeline.py` | Full 9-node pipeline integration test with real LLM. | A1–A5 | ALIGNED |
| `test_verification.py` | Three-tier gate test with BAD_MCP_TOOL canary + good Fibonacci. | T1 | ALIGNED |
| `test_mint.py` | CLI smoke tests across MCP_TOOL / SKILL / AGENT + all file formats. | A1–A5 | ALIGNED |
| `test_execution_backends.py` | T04 SKILL + T16 AGENT backend spot-check. | A5 | ALIGNED |
| `test_auto_optimizer.py` | Full pipeline + optimizer node with `patch("src.verification.gate.verify")` rollback canary. | T2 | ALIGNED |
| `test_split_merge.py` | 9/9 Pillar 3 integration matrix (MCP_TOOL / SKILL / AGENT × split / merge). | T2 | ALIGNED |
| `test_prune.py` | Deterministic prune semantics (pinned / recent / idle). | T2 | ALIGNED |
| `test_feedback_split.py` | `feedback --wrong` → MetricsStore → split trigger loop. | T2 + user oracle (§12 finding #6b) | ALIGNED |
| `test_cli_settings.py` | Settings persistence + feedback UX smoke tests. | Operator surface | NEUTRAL |
| `test_registry_matching.py` | Three-phase matching audit (naive cosine → query-audit → self-consistency). | A4 + T2 (false-positive quantification) | IMPROVEMENT — empirically probes proposal's "hierarchical embeddings prevent false positives" claim. |
| `validate_contract_extractor.py` | Validates the authored vs LLM `OutputContract` resolver. | A1 | ALIGNED |

---

## 4. Tension-by-tension verdict

### T1 — Autonomy vs. correctness

**Strong alignment, with two disclosed improvements.**

- The proposal names three verification tiers; the implementation ships **four** (Tier 1 static, Tier 1.5 import probe, Tier 2 behavioral, Tier 3 adversarial).
- Tier 1.5 is **disclosed as a cost-optimization, not a fourth methodological tier**. The failure modes it catches (`wrong_pip_package`, `import_error`, `fence_compile_error`) are operational-correctness issues *not* listed in the proposal's §Pillar 2 LLM-failure-mode taxonomy (hardcoded data / silent fallbacks / mocks / graceful degradation). Without Tier 1.5, Tier 2 still catches them via `ModuleNotFoundError`, just more expensively. Chapter 4.5 disclosure required (already in master plan Step 12.5).
- **Fail-closed semantics** throughout: 3-attempt retries + sticky flags + dedicated CLI exit codes at decomposition (exit 2), classification (exit 5), and verification (exit 4). All three originally had silent-fallback paths and were hardened in §12 findings #14 / #15 / #16.
- **Adversarial timeout** now fail-closed (HIGH severity + retry) per Step 14.11 — stronger than a naive "probe and report."
- `coverage.py` adds a *Pillar 2 × Pillar 3* bridge (replay parent failure cases against split variants) not in the proposal.

### T2 — Specificity vs. generality

**Strong alignment, with multiple improvements.**

- Formula `U(c)` implemented verbatim in `optimizer/utility.py`.
- Operations coverage 9/9 (MCP_TOOL × SKILL × AGENT × prune/split/merge).
- **Evolutionary taxonomy** (`taxonomy.py`) replaces the proposal's fixed-hierarchy sketch with a file-backed, atomically-updated, one-category-at-a-time registry plus an `is_generic` bypass that resolves the Phase E third-run residual LOSS cases.
- **Per-operation optimizer config** (`optimizer_config.py`) — `"auto" \| "on" \| "off"` per operation, addresses §12 finding #8. Not in the proposal.
- **Split coverage verification** is MCP_TOOL-only by design (deterministic executors have stable per-input failure semantics; SKILL/AGENT do not). Disclosed in master plan §12 finding #5 and slated for Chapter 7 future work on nondeterministic-executor coverage verification.
- Distribution-shift adaptation (Pillar 3 RQ #3) is now measurable pre-eval via `run_longitudinal_shift.py` + `analyze_longitudinal.py` three-tier adaptation classifier — moved out of Chapter 7 future work.

### T3 — Context economy

**Structurally aligned, partial on measurement.**

- Inter-node handoffs use **typed Pydantic schemas** (`src/schemas/`), never prose summaries or full histories — directly answering the proposal's "context engineering that preserves exactly the information each downstream agent requires."
- Enforced at **three granularities** (per §3.4 of proposal, tracked as §12 finding #12):
  1. Task-level — `OutputContract.check()` at chain completion (Step 14.6, CLI exit 3 on mismatch).
  2. Subtask-level — `CompositionalExecutor` validates AGENT/SKILL steps against decomposer's `output_schema`.
  3. Generation-level — dynamic Pydantic `response_format` built from `output_schema` forces the LLM's structured-output call to declare exactly the required keys.
- Lead-with-subagents (`agent.py` Phase 3.5b) collapses multi-gap AGENT contexts.
- **Partial measurement gap:** `analyze_context_utilization.py` captures peak input-token % of window but does **not** measure compression-induced information loss (aggressive-compression-loses-detail half of the tension is not empirically quantified). This is the one genuine coverage gap against the Bootstrapping section — noted here for Chapter 6 disclosure.

---

## 5. Summary table of improvements over proposal

| # | Improvement | Surface | Disclosed in |
|---|---|---|---|
| 1 | Tier 1.5 sandbox import probe (cost-optimization, not a proposal tier) | `src/verification/import_check.py` | Master plan §Step 12.5 — Chapter 4.5 disclosure required. |
| 2 | Dual-query retrieval + evolutionary taxonomy with `is_generic` | `gap_detection.py`, `taxonomy.py` | Memory `project_thesis_proposal_pivot_lens.md` |
| 3 | Fail-closed decomposition + classification + verification with dedicated CLI exit codes | `decomposition.py`, `classifier.py`, `verification_node.py` | §12 findings #14 / #15 / #16 |
| 4 | Split coverage verifier (Pillar 2 × Pillar 3 bridge) | `verification/coverage.py` | §12 pre-registered finding #5 |
| 5 | Adversarial timeout fail-closed + 480s budget | `verification/adversarial.py` | Step 14.11 |
| 6 | Per-operation optimizer config (`"auto" \| "on" \| "off"`) | `schemas/optimizer_config.py`, `cli/settings.py` | §12 finding #8 |
| 7 | Distribution-shift Pillar 3 RQ #3 now answerable pre-eval | `scripts/run_longitudinal_shift.py`, `analyze_longitudinal.py` | Step 18.8 |
| 8 | Utility-score KL (proposal-literal) rather than invocation-count proxy | `analyze_longitudinal.py` | Step 18.7 |
| 9 | Dynamic sandbox dependencies (generator-declared `pip_packages` + LLM fallback) | `sandbox/Dockerfile`, `verification/sandbox_tool.py` | CLAUDE.md §Dynamic Sandbox Dependencies |
| 10 | Three-layer Structured State Communication enforcement | `task.py::OutputContract`, `composition.py`, dynamic `response_format` in `execution.py` | §12 finding #12 |

## 6. Drift / gaps

| # | Item | Severity | Action |
|---|---|---|---|
| ~~1~~ | ~~**Context economy measurement only captures peak utilization, not compression-loss.**~~ | ~~Partial gap against T3~~ | ✅ **RESOLVED 2026-04-18 via Step 13.8.** Pre-flight verification against the code discovered the scheduled-step prose was optimistic — `ChainResult.failed_step_reason` was computed in-memory by `composition.py` but NOT persisted to the `evaluation_runs` DB (it was flattened into the `failure_reason` enum's `chain_step_failed` bucket, losing the distinguishing string). Fix: added `failed_step_reason VARCHAR` column + idempotent migration + `EvaluationRecord.failed_step_reason` field + harness wire-through. New `scripts/analyze_compression_loss.py` classifies failures into 9 categories (subtask-level compression loss, task-level compression loss, wrong_value, agent_execution_timeout, agent_execution_error, gate_rejection, generation_failed, exception, other) and emits three tables: per-condition, per-capability-type, top-N most-dropped keys. 20 new unit tests. |
| 2 | **`tool_loader.py` uses importlib subprocess, not persistent FastMCP servers.** | Disclosed simplification of A5. Chapter write-up text staged in master plan §11.8. | Chapter 3.5 methodology + Chapter 7.3 future work per CLAUDE.md §MCP Tool Loading Strategy. |
| 3 | **Split coverage *verification* is MCP_TOOL-only — split itself works for all three types.** Two mechanisms often conflated: `split()` (dispatches to `_split_tools` / `_split_skills` / `_split_agents`, 9/9 matrix, each runs the three-tier gate per variant) vs `verify_coverage()` (the post-split cross-check that replays the parent's recorded failures against variants — MCP_TOOL-only, returns `CoverageReport(all_covered=True, status="unverifiable_type")` vacuously for SKILL/AGENT). Split is fully capability-type-general; the collective *"variants cover the parent's failure surface"* guarantee is MCP_TOOL-only. Rationale in `src/verification/coverage.py` lines 47–79: Pillar 3 Split's *"input subspaces where the capability breaks"* framing presupposes stable per-input failure semantics that only deterministic executors satisfy; for LLM-mediated types, same input yields variable output per invocation so *"did the variant fix the failure on input X?"* collapses into *"did the variant happen to succeed on this replay?"*. | Disclosed per-type scope per §12 finding #5. Chapter write-up text staged in master plan §11.1 (thesis_proposal.md stays immutable per §8 contract rule). | Chapter 4 methodology disclosure; Chapter 7 future work for nondeterministic executors (per-variant replay statistics or LLM-judged correctness). |
| 4 | **Token cost backfill captures all LLM GENERATIONs with documented approximations.** Corrected framing: `scripts/backfill_token_costs.py` fetches all LangFuse GENERATION observations in the eval window — that includes decomposer + classifier + generator + Tier 2/3 verifier + SKILL/AGENT executor LLM calls (generation + verification + execution categories are all covered, contrary to the earlier framing). Actual simplifications: (a) timestamp-window matching falls back to per-condition averages when exact mapping isn't possible; (b) registry-match tasks T01–T03 hardcoded to 0 tokens under JIT conditions (safe IF those tasks only match MCP_TOOL seeds); (c) static-baseline runs hardcoded to 0 tokens (correct by construction); (d) embedding-call tokens may not surface as GENERATION spans — to be confirmed by Step 13.7 LangFuse audit. | Known simplifications; item (d) is an open question for Step 13.7. Chapter write-up text staged in master plan §11.2. | Chapter 6.1 methodology + Chapter 7.2 Limitations. Item (d) closed by Step 13.7 LangFuse audit before Phase 4. |

No drift falls at the level of "fails to realize a Bootstrapping Problem claim." Items 2–4 remain disclosed simplifications / measurement scopings; item #1 closed 2026-04-18 via Step 13.8. Per the §8 contract rule in `docs/MASTER_PLAN_2026_04_13.md`, `docs/thesis_proposal.md` is immutable — disclosure text for items #3/#4 is staged in master plan §11 (Thesis write-up disclosures) and flows into Chapter 4/5/6/7.2 prose at write-up time.

---

## 7. Bottom line

The implementation **realizes Claim A end-to-end** (task analysis → plan → gap detection → classification → generation → verified registration → composed execution) and **materially addresses all three tensions** with ≥10 documented improvements over the proposal's sketch. The only honest gap against the Bootstrapping section itself is the **compression-loss half of T3**: the system *implements* structured-state communication as a mechanism to solve context economy, but does not *measure* the information-retention side of the tradeoff the proposal names. **This gap is scheduled to close pre-eval as Step 13.8 in `docs/MASTER_PLAN_2026_04_13.md`** (new `scripts/analyze_compression_loss.py` reader over existing `failed_step_reason` / `output_shape_missing_keys` signal — no new write-side plumbing needed). When Step 13.8 lands, the "single honest gap" disappears.

Chapter write-up text for all disclosed items (§3, §4, §6 drift rows) is staged in master plan §11 (*Thesis write-up disclosures*). Per the §8 contract rule, `docs/thesis_proposal.md` is preserved as the rolled-back original — disclosures flow into the thesis body chapters at write-up time, not into the proposal itself.

Everything else in the Bootstrapping section is either directly realized or realized with a stronger mechanism than the proposal itself describes.
