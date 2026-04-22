# Pillar 1 — JIT Capability Pipeline — Implementation Alignment Audit

**Scope:** `docs/thesis_proposal.md` §Pillar 1 lines 65–99 (Problem, 5-stage Approach, Structured State Communication, Research Questions).
**Audited directories:** `sandbox/`, `scripts/`, `src/`, `tools/`.
**Date:** 2026-04-17.
**Method:** File-by-file rundown mapped against the five stages the proposal names (task decomposition → skill gap detection → capability classification → generation → sandboxed integration), the Structured State Communication clause, and the three Research Questions.

---

## 1. What the proposal's Pillar 1 actually says

### Problem
A self-developing MAS encountering a missing capability has two options that don't scale (fail, ask a human) and a third (generate at runtime) that raises two questions:
- What **form** should the generated capability take?
- How does it **integrate** with the existing system?

### Approach — five sequential stages

| # | Stage | Proposal specification (verbatim-ish) |
|---|---|---|
| 1 | **Task decomposition** | Orchestrator agent *"using a deep-thinking model with a large token budget"* breaks the input task into subtasks, each annotated with capabilities required. |
| 2 | **Skill gap detection** | Match required capabilities against a *"semantically indexed library of existing tools, skills, and agents"*. Gaps = subtasks with no match above a confidence threshold. Registry uses *"hierarchical embeddings (domain → capability → specific skill)"* to prevent false positives and false negatives. |
| 3 | **Capability classification** | Each gap is classified into one of three artifact types: **Deterministic + stateless → MCP tool** (Python/TS function wrapped in MCP server interface, JSON-RPC), **Procedural + domain-specific → Agent Skill** (SKILL.md with YAML frontmatter, instructions, templates, optional scripts), **Stateful + requires planning → new agent** (system prompt + model config via *"TOML-based provider-agnostic config"*). |
| 4 | **Generation** | *"A code-generation agent (Claude Code or equivalent)"* produces the artifact. For MCP tools: function + server wrapper + test harness. For Skills: SKILL.md with YAML frontmatter + body. For agents: system prompt + config entry. |
| 5 | **Sandboxed integration** | Artifact deployed into an *"isolated environment — a sandboxed MCP server, a skill folder, or a contained agent session"* and connected to the orchestrator for immediate use. |

### Structured State Communication
Agents within the pipeline communicate via *"strictly typed JSON schemas representing the 'world state' at each handoff point"*. Replaces natural-language summaries and raw conversation histories with a *"protocol-buffer-style contract that each agent can parse deterministically"*. The schema evolves as the pipeline discovers new capability types.

### Research Questions (Pillar 1)
- **RQ-1.1** — How accurately can an LLM classify gaps into the three artifact types?
- **RQ-1.2** — Success rate of JIT-generated capabilities vs. pre-built human-designed equivalents?
- **RQ-1.3** — Latency of generation + verification vs. cost of failing and escalating to a human?

---

## 2. Verdict at a glance

| Proposal claim | Realized? | Primary implementation surface |
|---|---|---|
| Stage 1 — Task decomposition | ✅ ALIGNED + IMPROVED | `src/orchestrator/decomposition.py` (DeepAgent + 3x retry + chain-coherence validator + writer-append safety net) |
| Stage 2 — Skill gap detection | ✅ ALIGNED + IMPROVED | `src/orchestrator/gap_detection.py` + `src/registry/store.py` (ChromaDB + dual-query + evolutionary taxonomy) |
| Stage 3 — Capability classification (3 types) | ✅ ALIGNED + IMPROVED | `src/orchestrator/classifier.py` (per-gap, not per-task; 3x retry + sticky flag + file-writer override) |
| Stage 4 — Generation | ✅ ALIGNED with deliberate DIVERGENCE | `src/orchestrator/generators/{mcp_tool,skill,agent}.py` (LangGraph nodes + structured output, not a DeepAgent-per-type) |
| Stage 5 — Sandboxed integration | ✅ ALIGNED with deliberate DIVERGENCE | `src/orchestrator/{loading_node,tool_loader}.py` + `sandbox/Dockerfile` (importlib + ephemeral Docker, not a persistent FastMCP server) |
| Structured State Communication | ✅ ALIGNED + IMPROVED | `src/schemas/` (Pydantic only, no raw dicts; 3-granularity enforcement) |
| RQ-1.1 (classification accuracy) | ✅ INSTRUMENTED | `scripts/analyze_results.py::print_classification_accuracy` + `TaskDefinition.expected_type` |
| RQ-1.2 (JIT vs. pre-built success) | ✅ INSTRUMENTED | `BaselineCondition` matrix (`static`, `no_verification`, `no_optimization`, `full`) |
| RQ-1.3 (gen+verify latency vs. escalation) | ⚠️ PARTIALLY INSTRUMENTED | Wall-clock + token-cost recorded; *"cost of escalation"* has no direct counterpart (there's no human-escalation code path to benchmark against). |

**Bottom line.** All five stages are end-to-end realized; Structured State Communication is enforced at three granularities (task, subtask, generation) which is stronger than the proposal's single-handoff framing. Two conscious divergences from the proposal wording are disclosed in `CLAUDE.md` (Generation is not a DeepAgent; Sandboxed integration is not a persistent FastMCP server) — both are documented as *implementation-style choices that preserve the architectural semantics* rather than silent drift. The one research-question gap (RQ-1.3's *"escalation cost"*) is an evaluation-methodology limit, not an implementation gap.

---

## 3. File-by-file rundown

Legend: **ALIGNED** = directly realizes a proposal claim. **IMPROVEMENT** = stronger mechanism than the proposal sketch. **DIVERGENCE** = diverges from the literal proposal wording (disclosed if it affects semantics). **NEUTRAL** = infrastructure, does not directly bear on Pillar 1 claims.

### 3.1 `sandbox/` (1 file)

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `sandbox/Dockerfile` | Minimal base image (`mcp` + `pydantic` only). Dynamic deps installed via `uv pip install --system` inside each ephemeral container. Non-root user. | Stage 5 (isolated environment) | ALIGNED — realizes "isolated environment" with per-invocation ephemeral containers + `--network none` enforcement downstream. |

**Verdict.** Stage 5's isolation requirement is met. Divergence from proposal wording (below §5): this is not a persistent MCP server.

### 3.2 `scripts/` (21 files)

Scripts in this directory are evaluation / test / analysis harnesses — they exercise the Pillar 1 pipeline but do not implement any of its stages. Listed here for completeness.

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `scripts/run_pilot.py` | Runs 20-task testbed × 4 conditions through the Pillar 1 pipeline, records to DuckDB. | RQ-1.2 (JIT vs. static baseline) | NEUTRAL — evaluation harness |
| `scripts/run_longitudinal.py` | Multi-cycle eval (Pillar 3-facing but touches Pillar 1 registration path). | RQ-1.2 (registry accumulation effects) | NEUTRAL |
| `scripts/run_longitudinal_shift.py` | Distribution-shift phased runner (Phase A → B). | Pillar 3 RQ #3 | NEUTRAL for Pillar 1. |
| `scripts/test_pipeline.py` | Integration test: runs a task end-to-end through all 9 nodes, verifies every intermediate state. | Every stage | NEUTRAL — smoke test |
| `scripts/test_mint.py` | CLI smoke tests covering all three types × 7 file formats. | Stage 3 coverage | NEUTRAL |
| `scripts/test_execution_backends.py` | Bypasses generation; exercises SKILL + AGENT executors directly. | Stage 5 runtime | NEUTRAL |
| `scripts/test_pipeline.py`, `test_prune.py`, `test_split_merge.py`, `test_auto_optimizer.py`, `test_feedback_split.py`, `test_mint.py`, `test_verification.py`, `test_cli_settings.py`, `test_registry_matching.py` | Integration & diagnostic scripts. | Various | NEUTRAL |
| `scripts/analyze_results.py` | Analyzer with `print_classification_accuracy` (operational proxy for RQ-1.1) + Gini + success-rate tables. | RQ-1.1, RQ-1.2 | ALIGNED — instruments RQ-1.1. |
| `scripts/analyze_longitudinal.py` | KL-convergence + distribution-shift analysis. | Pillar 3 | NEUTRAL for Pillar 1 |
| `scripts/analyze_context_utilization.py` | Peak input-token utilization per task. | RQ-1.3 / T3 partial | NEUTRAL — informs context-economy framing. |
| `scripts/backfill_token_costs.py` | Retroactive token-cost population. | RQ-1.3 instrumentation | NEUTRAL |
| `scripts/migrate_business_domain_labels.py` | One-shot ChromaDB label migration (to evolutionary taxonomy schema). | Stage 2 (registry) | NEUTRAL — migration |
| `scripts/step10_merge_candidates.py` | Merge-candidate experiment (Pillar 3). | Pillar 3 | NEUTRAL |
| `scripts/validate_contract_extractor.py` | Pre-eval validation of `OutputContract` LLM extractor against authored contracts. | Structured State Communication at boundary | ALIGNED. |

**Verdict.** Scripts are uniformly evaluation / instrumentation — they support the Pillar 1 research questions but do not realize the pipeline. No drift, no blockers.

### 3.3 `tools/` (2 files)

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `tools/quick_validate.py` | Structural validator for SKILL.md frontmatter: checks required `name` + `description`, allowed keys (`license`, `compatibility`, `allowed-tools`, `metadata`), hyphen-case name, length caps. Tier 0 prose check: rejects lorem-ipsum / `TODO: implement` / `# placeholder return` literal markers. | Stage 3 (SKILL as *"SKILL.md with YAML frontmatter"*) + Stage 4 generation validation | ALIGNED — directly enforces the proposal's SKILL.md spec. Imported lazily by `skill.py::_get_validate_skill()` and by `agent.py::_validate_result` when `is_deep_agent=True`. |
| `tools/init_skill.py` | Stand-alone template scaffolder for hand-authored skills (not used in the automated pipeline). | Stage 3 reference | NEUTRAL — dev-tooling |

**Verdict.** Stage 3's *"SKILL.md with YAML frontmatter, instructions, templates, and optional scripts"* claim is structurally enforced by `quick_validate.py`, which is the single source of truth for skill validity. Both generators (`skill.py`, `agent.py`) depend on it.

### 3.4 `src/schemas/` (6 files + `__init__.py`)

All inter-node state is a Pydantic `BaseModel` with `extra="forbid"`. No raw dicts cross node boundaries — this is the load-bearing Structured State Communication surface.

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `capability.py` | `CapabilityType` enum (MCP_TOOL / SKILL / AGENT) + `CapabilityDescriptor` (registry row). Fields: `domain`, `is_generic`, `is_active`, `input_schema`, `output_schema`, `tags`, `metadata`. | Stages 2, 3; Structured State | ALIGNED — enum exactly matches proposal's three types. |
| `task.py` | `SubTaskItem`, `DecompositionResult`, `SubTask`, `ClassifiedGap`, `GeneratedArtifact`, `ClassificationResult`, `GapItem`, `MatchItem`, `OutputContract`, `ContractCheckResult`, `TaskState`, `CapabilityReference`, `ChainResult`, `StepTiming`. | Every stage; Structured State | ALIGNED + IMPROVED — `SubTask.output_schema` + `SubTask.requires_upstream_keys` give Stage 1 a structured *contract* about downstream data-flow that the proposal leaves implicit. `OutputContract` promotes the expected-output shape from prose into a typed field. |
| `verification.py` | `VerificationFinding`, `VerificationReport`, `Severity`. | Pillar 2 boundary | NEUTRAL for Pillar 1 |
| `metrics.py` | `InvocationRecord`. | RQ-1.3 instrumentation | ALIGNED — underpins token/latency measurement. |
| `evaluation.py` | `EvaluationRecord`, `BaselineCondition`. | RQ-1.2 instrumentation | ALIGNED. |
| `optimizer_config.py` | `OptimizerConfig`. | Pillar 3 | NEUTRAL |

**Key structural improvements over the proposal wording:**
1. **Subtask-level data contract.** The proposal says subtasks are *"annotated with the capabilities required"*. The implementation adds `output_schema` (what the subtask MUST produce) and `requires_upstream_keys` (what it reads) — a typed DAG-coherence contract. See `src/orchestrator/decomposition.py::_validate_chain_coherence`.
2. **Task-level output contract.** `OutputContract.required_keys` / `required_files` lets the CLI and eval harness check *task outcomes* at the boundary (Step 14.6). Orthogonal to Pillar 2's artifact-level verification.
3. **Sticky failure flags.** `decomposition_succeeded`, `classification_succeeded`, `verification_succeeded` — three-layer strict-gate pattern per §12 findings #14/#15/#16. Enum `status` transitions forward through every node and cannot carry a rejection signal; these dedicated flags do.

### 3.5 `src/orchestrator/` — the Pillar 1 pipeline proper

#### 3.5.1 Entry point + topology

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `pipeline.py` | `create_pipeline(registry, loader, *, run_optimizer, optimizer_config, metrics_store, verification_enabled)` builds a 9-node LangGraph StateGraph. Entry edge: `START → decomposition_node`. All subsequent routing via `Command(update=…, goto=…)` returns. | Overall pipeline topology | ALIGNED — literal node-chain matches the 5-stage proposal (decomposition → gap_detection → classifier_node → generate_mcp_tool → generate_skill → generate_agent → verification_node → loading_node). |

#### 3.5.2 Stage 1 — Task decomposition

| File | Role | Alignment |
|---|---|---|
| `decomposition.py` | `decomposition_node` invokes a fresh `create_deep_agent(model=DEFAULT_LLM_MODEL, response_format=DecompositionResult)` per call. `recursion_limit=50`, 240 s hard timeout, `MAX_DECOMPOSITION_ATTEMPTS=3`. Post-processing: `_validate_chain_coherence` (HARD — upstream keys must trace to a predecessor's `output_schema`), `_detect_upstream_key_collisions` (SOFT — last-write-wins warning), `_ensure_file_writer_last_step` (Rule-15 safety net that appends deterministic MCP_TOOL writer subtasks when `OutputContract.required_files` is non-empty). | ALIGNED + IMPROVED |
| `prompts/decomposition.py` | ~600-line system prompt with 15 explicit rules covering: three-layer architectural framing (Tools / Skills / Agents), subtask scoping, `output_schema` contracts, `requires_upstream_keys` structured contract, parent-task-first classification reasoning, and Rule 15 (file-writer-as-last-step). | ALIGNED |

**Proposal literal:** *"orchestrator agent (using a deep-thinking model with a large token budget)"*.
**Implementation:** uses `openai:gpt-5-mini` (centralized via `DEFAULT_LLM_MODEL`, override via `JIT_LLM_MODEL`) wrapped in a DeepAgent harness with `write_todos` planning and file-system scratchpad. Not a dedicated "deep-thinking" model tier. The DeepAgent harness provides the multi-step reasoning the proposal gestures at.

**Improvements over the literal proposal:**
1. **Retries with sticky failure flag.** The proposal says the agent *"breaks the input task into subtasks"* but is silent on failure handling. Implementation: 3× retry on timeout/empty/error, then fail-closed (`decomposition_succeeded=False`) and CLI exit code 2 per §12 finding #15. Prior single-attempt + first-sentence fallback was silent drift.
2. **Structured chain-coherence.** Subtasks carry `requires_upstream_keys` which is cross-checked against transitive predecessors' `output_schema` (pure set comparison, no regex). A canonical failure mode from T14 (`parse-csv` produces `departments` but `aggregate-by-department` requires `transactions`) is caught at decomposition rather than execution.
3. **File-writer-as-last-step safety net.** File outputs are *"deterministic + stateless → MCP tool"* per Pillar 1.3; Rule 15 + `_ensure_file_writer_last_step` + classifier `_enforce_file_writer_mcp_tool` guarantee this structurally, even when the LLM decomposer collapses writer steps into an AGENT parent-task (§12 finding #11).

#### 3.5.3 Stage 2 — Skill gap detection

| File | Role | Alignment |
|---|---|---|
| `gap_detection.py` | `gap_detection_node(state, registry, metrics_store)` — no LLM call in the hot path. Threads two signals into `registry.detect_gaps`: (a) dual-query retrieval (primary = decomposer slug, auxiliary = `SubTask.description`), (b) evolutionary-taxonomy `domain` filter with `include_generic=True` bypass. Never-raises contract: any taxonomy or LLM failure falls back to unfiltered search. | ALIGNED + IMPROVED |
| `taxonomy.py` | `DomainTaxonomy` with JSON-file backing + `fcntl.flock`. Grows one category at a time via `get_or_create_for_capability` (capability classification). `get_for_query` picks best-match existing category or None (bypass). `classify_and_tag_artifacts` called from each generator. | ALIGNED + IMPROVEMENT over static hierarchical embeddings — domain taxonomy **evolves** with the capability distribution. |
| `../registry/store.py` | `SkillRegistry` over ChromaDB with cosine similarity. `search(…, domain=…, include_generic=True)`, `detect_gaps(queries, auxiliary_queries, threshold=0.6, domain=…, include_generic=True)`. `_build_document` concatenates `"{domain} > {capability_type} > {name}: {summary}"`. | ALIGNED with DIVERGENCE (see below) |

**Proposal literal:** *"registry uses hierarchical embeddings (domain → capability → specific skill)"*.
**Implementation:**
- The phrase *"hierarchical embeddings"* is realized as a *single* prose document (`"{domain} > {capability_type} > {name}: {summary}"`) embedded by ChromaDB's default MiniLM-L6-v2, with domain applied as a **pre-filter** on metadata, not as a separate embedding layer.
- Earlier B1 experiments tried true hierarchical (multi-line labeled, `||`-delimited, natural-prose enriched variants) and **all regressed Phase A recall by 6–12pp**. Documented in `registry/store.py::_build_document` docstring. The structural signal (input/output schema Jaccard) is moved to Pillar 3 Merge and to a post-embedding filter rather than into the embedding itself.
- **DRIFT DISCLOSURE REQUIRED**: the final embedding is not hierarchical in the traditional embedding-hierarchy sense (no separate vectors per level). It is a single concatenated document with a hierarchical *prefix* + a cheap metadata domain filter. Functionally effective but not literal.

**Improvements:**
1. **Dual-query retrieval.** Decomposer slugs are short hyphen-case (`csv-parser`) and systematically fall below the 0.6 cosine threshold against prose documents; adding the natural-language `SubTask.description` as an auxiliary query and taking max-score lifts seeded-cap recall. Pre-2026-04-14 the slug-only path produced measurable false gaps.
2. **Evolutionary taxonomy.** Rather than a fixed domain vocabulary, categories grow organically. Generic caps (`fibonacci`, `csv-parser`) bypass the domain filter via `is_generic=True` — addresses the Phase E false-rejection of genuinely cross-domain caps.

#### 3.5.4 Stage 3 — Capability classification

| File | Role | Alignment |
|---|---|---|
| `classifier.py` | `classify_gap(gap, task_description, required_capabilities)` is the primary entry point — **per-gap**, not per-task. Uses `with_structured_output(ClassificationResult)` at `temperature=0`. `MAX_CLASSIFICATION_ATTEMPTS=3` with fail-closed (returns `None`; `classifier_node` sets `classification_succeeded=False`) per §12 finding #16. `_enforce_file_writer_mcp_tool` overrides any `write-*` query gap to MCP_TOOL when the task declares `required_files` (Pillar 1.3 + §12 finding #11). Legacy `classify_task` preserved for single-gap fallback compatibility. | ALIGNED + IMPROVED |
| `prompts/classifier.py` | Per-gap prompt (~400 lines) with explicit three-layer definitions (MCP Tool / Skill / Agent), parent-task-first decision process, 7 worked examples covering file-writer carve-out (Example G). | ALIGNED |

**Proposal literal (verbatim decision rules):**
- Deterministic + stateless → **MCP tool**
- Procedural + domain-specific → **Agent Skill**
- Stateful + requires planning → **new agent**

**Implementation maps exactly onto these three enum values** (`CapabilityType.MCP_TOOL`, `SKILL`, `AGENT`).

**Improvements:**
1. **Per-gap classification** rather than per-task. The proposal says *"each gap is classified"* — implementation iterates `state.gaps` and calls `classify_gap` once per gap. Two gaps in the same task can classify as different types and route to different generators (e.g. T07: SKILL analyzer + MCP_TOOL writer).
2. **Fail-closed + sticky flag.** Pre-fix behaviour was silent default to MCP_TOOL with `confidence=0.3` — exactly the type-mismatch failure mode Pillar 1.3 warns against (*"An MCP tool that should have been a skill will be too rigid"*). Now: 3× retry, then `classification_succeeded=False` + CLI exit 5.
3. **File-writer override** is a structural enforcement of Pillar 1.3's deterministic-file-I/O classification, independent of LLM judgement drift. Two-layer defense: prompt exception (Example G) + post-hoc `_enforce_file_writer_mcp_tool`.

**Divergence from proposal wording:**
- Proposal says new agents are spawned with *"model configuration (selected via TOML-based provider-agnostic config)"*. Implementation uses `src/config.py` Python module with env-var override (`JIT_LLM_MODEL`), not TOML. The underlying property — provider-agnostic config via `init_chat_model("provider:model")` — is preserved, so the architectural principle holds. **Disclosure:** TOML-vs-Python is a format choice, not a semantics change.

#### 3.5.5 Stage 4 — Generation

| File | Role | Alignment |
|---|---|---|
| `generators/mcp_tool.py` | `generate_mcp_tool(state)` LangGraph node. Per-gap thread-pool generation (max 4 workers) via `generate_single_tool`. Single LLM call → `GeneratedToolCode` (structured output) → `_validate_code` (AST syntax + FastMCP structural checks + step-signature validation for chain position) → one-shot repair pass → artifact. Generator emits `pip_packages` (exact PyPI names, e.g. `pyyaml` not `yaml`) which feed Tier 1.5 import probe and sandbox execution. | ALIGNED |
| `generators/skill.py` | `generate_skill(state)` LangGraph node. One LLM call per SKILL gap. `validate_skill_content` calls `tools/quick_validate.py::validate_skill` + checks non-empty body after frontmatter. Repair pass on validation failure. Writes `SKILL.md` to `data/{eval,cli}/skills/<name>/`. | ALIGNED |
| `generators/agent.py` | `generate_agent(state)` LangGraph node. **Two modes:** (a) single AGENT gap → `_generate_single_agent` producing `AgentGenerationResult` (system prompt + node config + optional SKILL.md); (b) ≥ 2 AGENT gaps → `_generate_lead_with_subagents` producing ONE lead + N subagents with `SubAgentConfig` per delegated step. Both paths: repair pass + quick-validate SKILL.md when `is_deep_agent=True`. | ALIGNED + IMPROVED (lead+subagents composition) |
| `generators/utils.py` | `slugify_gap`, `slugify_gap_hyphen`, `build_task_context` (injects verification-failure feedback on retry), `find_subtask_for_gap`, `is_first_step_for_gap`, `build_schema_context` (formats upstream / self schemas into a per-prompt contract section). | ALIGNED — `build_schema_context` is the in-prompt load-bearing piece of Structured State Communication at generation granularity. |
| `prompts/mcp_tool_generator.py` | System + user + repair prompts. `FIRST_STEP_DIRECTIVE` / `DOWNSTREAM_STEP_DIRECTIVE` injected based on `is_first_step_for_gap` to prevent step-0 tools from declaring `upstream_data` as a required parameter (Step 14.7 regression fix). | ALIGNED |
| `prompts/skill_generator.py` | Skill generator + repair prompts. | ALIGNED |
| `prompts/agent_generator.py` | Agent generator + lead-with-subagents generator + repair prompts. | ALIGNED |

**Proposal literal:** *"A code-generation agent (Claude Code or equivalent) produces the artifact. For MCP tools, this means writing the function, the server wrapper, and a test harness. For Skills, it produces the SKILL.md with YAML frontmatter and the instruction body. For agents, it generates the system prompt and configuration entry."*

**Divergences from the literal proposal:**
1. **Not a single "code-generation agent" per type.** Implementation uses **plain LangGraph nodes** (single-pass `structured_llm.invoke` + one-shot repair), not a full DeepAgent per generator. `CLAUDE.md` explicitly documents this choice: *"Plain LangGraph node. Why: Single-pass structured output with retry on failure"*. A DeepAgent loop would add planning + subagent overhead with no reliability benefit for single-call code gen.
2. **No generated test harness for MCP tools.** The proposal says generation includes *"a test harness"*. Implementation defers this to **Pillar 2's three-tier gate** (static + import-probe + behavioral + adversarial) — an *adversarially-generated* test harness rather than a generator-authored one. Practically stronger: LLM-generated self-tests have well-documented collusion failure modes (the same LLM that wrote the code writes tests that pass by construction). The three-tier gate is external and independent.
3. **Lead-with-subagents mode for ≥ 2 AGENT gaps.** Not in the proposal but a documented Phase 3.5b improvement: one lead coordinates via `task(name=…, task=…)` over N isolated subagent contexts. Each subagent runs in its own isolated context. Stored in `agent_config["subagents"]` + `agent_config["subagent_gap_queries"]`.

**Improvements (beyond divergence):**
- Per-gap schema contract section in every generator prompt (`build_schema_context`) — input schema = upstream predecessors' `output_schema`, output schema = own declared `output_schema`. This is Structured State Communication pushed down to generation granularity (§12 finding #12 Layer 3).
- Retry loop: failed artifacts carry forward verification feedback into the regeneration prompt (`build_task_context` on `attempt_count > 0`).
- Thread-pool generation across gaps (max 4 workers) — latency-efficient for multi-gap tasks.

#### 3.5.6 Stage 5 — Sandboxed integration

| File | Role | Alignment |
|---|---|---|
| `loading_node.py` | Final pipeline node. MCP_TOOL artifacts: `ToolLoader.load_tool` (importlib) → optional smoke-test → `ToolLoader.register_tool` (registry). SKILL / AGENT artifacts: already on disk from their generators → construct `CapabilityDescriptor` (with `input_schema` / `output_schema` derived via `schema_extraction.derive_input_schema_from_upstream` + decomposer `output_schema` fallback) → `registry.add`. Tolerates host-import failures for MCP tools with e.g. pandas: still registers with `source_code` so the Docker sandbox can execute them. | ALIGNED with DIVERGENCE |
| `tool_loader.py` | `ToolLoader.load_tool` (importlib + tempfile + `__module__` filter so e.g. `from asyncio import sleep` doesn't get picked up as the tool), `execute_tool` (subprocess isolation, 30 s default timeout), `register_tool` (builds descriptor with AST-extracted schemas). | ALIGNED |
| `schema_extraction.py` | AST-based `extract_mcp_tool_signature` that walks `@mcp.tool`-decorated async def, unwraps `Annotated[T, …]`, scans `upstream_data[...]` / `.get("…")` accesses to recover effective input contract, and reads `return {…}` dict literals for output schema. `find_subtask_for_gap` + `derive_input_schema_from_upstream` complete the fallback chain for SKILL/AGENT. | ALIGNED — closes the long-standing empty-schema-at-registration gap. |
| `composition.py` | `CompositionalExecutor` runs resolved chains in topological order on a shared workspace, with per-type backends (MCP_TOOL = Docker sandbox, SKILL = constrained LLM with `bind_tools`, AGENT = DeepAgent). Output-schema compliance checked at runtime (§12 finding #12 Layer 2). File outputs collected from workspace + inline `_output_file_{name,content}` keys. | ALIGNED — Stage-5-adjacent runtime (exercises registered capabilities in compositional chains). |
| `verification_node.py` | Sits between Stage 4 (generation) and Stage 5 (loading). Runs the three-tier gate, retries (max 3), drops failed artifacts + sets `verification_succeeded=False` on exhaustion per §12 finding #14. | Pillar 2 boundary — ALIGNED with Pillar 1 handoff. |
| `output_contract_resolver.py` | Task-entry resolver: authored `TaskDefinition.output_contract` shortcut → LLM extractor fallback. Single shared entry point for CLI + harness. | Structured State Communication at system boundary. ALIGNED. |
| `tracing.py` | `emit_langfuse_event` helper for structured observability events. | NEUTRAL — instrumentation |

**Proposal literal:** *"The generated artifact is deployed into an isolated environment — a sandboxed MCP server, a skill folder, or a contained agent session."*

**Divergence from proposal wording** (documented in `CLAUDE.md` § "MCP Tool Loading Strategy"):
- **NOT a persistent FastMCP server** for MCP tools. Implementation uses `importlib` dynamic import + ephemeral Docker subprocess per invocation. No ports, no health checks, no zombie process risk. Full MCP server deployment is listed as Chapter 7 future work.
- **Skill folder → YES** (`data/{eval,cli}/skills/<name>/SKILL.md`) — literal match.
- **Contained agent session → YES** via DeepAgent runtime with `tools=[sandbox_execute]` + optional `skills=[…]`. Generated agents cannot compose with other registry capabilities cross-type (also Chapter 7).

**Rationale for the divergence:** persistent FastMCP servers would add operational complexity (service lifecycle, port allocation, crash recovery) with no research-question payoff. The importlib + ephemeral Docker path preserves every architectural invariant the proposal cares about (isolation, typed inputs via `Annotated[T, Field(…)]`, JSON I/O) at a fraction of the operational surface.

### 3.6 `src/mcp_tools/` (1 hand-written tool)

| File | Role | Alignment |
|---|---|---|
| `fibonacci.py` | Reference FastMCP tool (hand-written, Iteration 0). Demonstrates the exact pattern generators must emit: `FastMCP(…)` + `@mcp.tool(…)` + `Annotated[T, Field(…)]` + `if __name__ == "__main__": mcp.run()`. Used as a shape exemplar; seeded into the registry by `seed_registry()` in `store.py`. | NEUTRAL — reference implementation. |

### 3.7 `src/registry/` (3 files)

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `store.py` | `SkillRegistry` (ChromaDB + cosine); `seed_registry()` seeds 8 hand-curated caps spanning all three types and multiple domains. | Stage 2 | ALIGNED (see §3.5.3 divergence re "hierarchical embeddings"). |
| `metrics.py` | `MetricsStore` (DuckDB): invocations, reuse, token costs, granularity queries. `fetch_invocations_for_task` for Gini / Step 18.5. | RQ-1.2, RQ-1.3, Pillar 3 | NEUTRAL — instrumentation |
| `evaluation_store.py` | `EvaluationStore` (DuckDB): `EvaluationRecord` persistence, `invocation_events` table, `cycle_utility_scores`, `cycle_events` tables. | RQ-1.1, RQ-1.2, RQ-1.3 | NEUTRAL — evaluation-side storage |

### 3.8 `src/cli/` (4 files)

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `app.py` | Typer CLI with 11 subcommands (`run`, `plan`, `list`, `inspect`, `retry`, `history`, `prune`, `seed`, `reset`, `disable`, `enable`, `config`, `feedback`). | RQ-1.2 user-facing | NEUTRAL |
| `runner.py` | Orchestrates `mint run` end-to-end: resolves `OutputContract` via `output_contract_resolver`, builds pipeline, executes, classifies terminal state, writes verification sidecar JSON, returns CLI exit code per sticky-flag layer. | Structured State Communication at boundary + RQ-1.2 | ALIGNED. |
| `display.py` | Rich-based CLI rendering. | NEUTRAL |
| `settings.py` | Per-op optimizer config per §12 finding #8. | Pillar 3 | NEUTRAL for Pillar 1 |

### 3.9 `src/verification/`, `src/optimizer/`

Both are **downstream of Pillar 1** (Pillars 2 and 3). Listed here for completeness — they do not implement any Pillar 1 stage:

- `src/verification/{gate,static_scanner,import_check,behavioral,adversarial,sandbox_tool,coverage}.py` — three-tier gate that sits between Stage 4 and Stage 5.
- `src/optimizer/{loop,operations,utility,simulation}.py` — Pillar 3 evolutionary optimizer.

Their interaction with Pillar 1 is only through `verification_node` (Pillar 2 boundary) and the Pillar-3-triggered registration / rollback of regenerated artifacts.

---

## 4. Structured State Communication — audit of the explicit claim

> *"Agents within the pipeline communicate via strictly typed JSON schemas representing the 'world state' at each handoff point. This replaces natural-language summaries (lossy, prone to hallucination) and raw conversation histories (noisy, token-expensive) with a protocol-buffer-style contract that each agent can parse deterministically."* — `docs/thesis_proposal.md` line 91

**Status:** ✅ ALIGNED + IMPROVED — the implementation enforces this **at three granularities**, where the proposal names only one.

| Granularity | Where it's enforced | Mechanism |
|---|---|---|
| **Pipeline-node handoff** (the proposal's literal claim) | Every `src/schemas/` model with `extra="forbid"` | All inter-node state is `TaskState` (Pydantic). No raw dicts cross node boundaries. `CLAUDE.md` lists this as a hard development rule. |
| **Task-level output contract** (Step 14.6) | `src/schemas/task.py::OutputContract` + `output_contract_resolver.py` | `required_keys` + `required_files` declared at task entry; `OutputContract.check()` is a pure set-comparison run at terminal state by both the CLI runner and the eval harness. Exit code 3 on shape violation. |
| **Subtask-level chain contract** (§12 finding #12 Layer 2) | `SubTask.output_schema` + `CompositionalExecutor` runtime check | Every subtask declares its output_schema; at chain execution, AGENT/SKILL step outputs are validated against the declared keys. MCP_TOOL output is deterministic (whatever the function returns) so enforcement targets only the LLM-mediated steps where drift is possible. |
| **Generation-level contract** (§12 finding #12 Layer 1) | `build_schema_context` + dynamic `response_format` Pydantic | The generator prompt gets an "Input/Output Schema Contract" section derived from `SubTask.output_schema` and upstream predecessors. For AGENT execution, `_build_dynamic_result_model` constructs a dynamic Pydantic `response_format` with exactly the declared keys, forcing the LLM's structured-output call to comply. |

**Additional structural contracts beyond the proposal sketch:**
- `SubTask.requires_upstream_keys` — explicit structured contract about which top-level keys a subtask reads from `upstream_data`. Cross-validated against predecessors' `output_schema` by `_validate_chain_coherence`. Earlier prose-parsing approaches (regex over subtask descriptions) were removed per the "thesis proposal pivot lens" memory entry — structured contracts won.
- Sticky failure flags (`decomposition_succeeded`, `classification_succeeded`, `verification_succeeded`) — carry rejection state through the terminal-enum-transition forward-flow.

**No natural-language summaries or raw conversation histories are passed between Pillar 1 nodes.** The DeepAgent decomposer's internal conversation is encapsulated inside its `invoke()` call; only the `response_format=DecompositionResult` structured output crosses the node boundary.

---

## 5. Research Questions — instrumentation audit

### RQ-1.1 — *"How accurately can an LLM classify capability gaps into the three artifact types (MCP tool vs. Skill vs. Agent)?"*

**Instrumentation:** ✅ Present (Step 18.5, shipped 2026-04-17, commit `ad4908e`).

- `TaskDefinition.expected_type` carries author-assigned ground-truth labels for all 20 testbed tasks (8 MCP_TOOL / 6 SKILL / 6 AGENT).
- `scripts/analyze_results.py::print_classification_accuracy` reports per-task primary-type match rate.
- **Methodological note:** the instrumentation is a *task-level primary-match proxy* for the RQ's literal per-gap phrasing. Four reasons documented in the analyzer docstring:
  1. Classifier's job is correct routing — primary-match captures that.
  2. Per-gap ground truth is ill-defined when the decomposer dynamically reshapes the plan.
  3. Structural overrides (e.g. `_enforce_file_writer_mcp_tool`) would contaminate any per-gap measurement.
  4. Task-level is the proposal's natural granularity.
- Full disclosure of proxy framing is in the `MASTER_PLAN_2026_04_13.md` Step 18.5 block and must be reproduced in Chapter 6.

### RQ-1.2 — *"Success rate of JIT-generated vs. pre-built equivalents?"*

**Instrumentation:** ✅ Present.

- Four `BaselineCondition` enum values: `static` (fixed hand-curated library, no JIT), `no_verification`, `no_optimization`, `full`.
- `EvaluationHarness` runs 20 tasks × 4 conditions × N runs, writes `EvaluationRecord` rows to `data/eval/metrics.duckdb`.
- `scripts/analyze_results.py` produces success-rate tables per condition + per-task.
- `static` baseline directly measures the proposal's *"pre-built, human-designed equivalents"* ceiling; `full` measures the JIT system. Delta is the RQ-1.2 answer.

### RQ-1.3 — *"Latency of generation + verification vs. cost of failing and escalating to a human?"*

**Instrumentation:** ⚠️ PARTIAL.

- **What's measured:** wall-clock latency per pipeline stage (`StepTiming.latency_ms`), token cost per invocation (`InvocationRecord.token_cost` + `backfill_token_costs.py`), LangFuse per-trace observability with span-level drill-in.
- **What's not measured:** the cost of escalation. The implementation has no human-in-the-loop code path (`CLAUDE.md`: DeepAgent timeout returns partial + error flag; CLI exits with non-zero code; no human operator is notified). Consequently, the "cost of escalation" side of the comparison has no ground truth and the RQ-1.3 answer is one-sided — we can report JIT latency / cost but we cannot compare against escalation cost empirically.
- **Evaluation-chapter framing:** this is an *evaluation methodology limit*, not an implementation gap. Comparing against a human baseline would require a user study outside thesis scope. Chapter 6 should treat RQ-1.3 as *"what is the JIT cost"* rather than a two-sided cost-benefit.

---

## 6. Consolidated findings table

| ID | Area | Finding | Severity | Where | Cross-ref |
|---|---|---|---|---|---|
| F1 | Stage 1 | Decomposition is DeepAgent-backed with `write_todos`, 3× retry, 240 s timeout, chain-coherence validation, and file-writer safety net. Strict superset of the proposal. | ALIGNED + IMPROVED | `src/orchestrator/decomposition.py` | §3.5.2 |
| F2 | Stage 1 | "Deep-thinking model with large token budget" is realized via `gpt-5-mini` + DeepAgent harness, not a dedicated reasoning model tier. No observed quality impact; empirical 87–180 s median wall time. | IMPLEMENTATION CHOICE | `src/config.py::DEFAULT_LLM_MODEL` + `decomposition.py` | §3.5.2 |
| F3 | Stage 1 | `SubTask.output_schema` + `requires_upstream_keys` add a DAG-coherence contract the proposal doesn't specify. Cross-validated by `_validate_chain_coherence`. | IMPROVEMENT | `src/schemas/task.py` + `decomposition.py` | §3.4, §3.5.2 |
| F4 | Stage 2 | Gap detection uses dual-query retrieval (primary slug + auxiliary description, max-score). | IMPROVEMENT | `gap_detection.py` + `store.py::detect_gaps` | §3.5.3 |
| F5 | Stage 2 | "Hierarchical embeddings (domain → capability → specific skill)" is realized as a **single concatenated document** `"{domain} > {capability_type} > {name}: {summary}"` + a cheap ChromaDB metadata domain pre-filter — not a separate embedding vector per level. True multi-level embeddings regressed Phase A recall 6–12pp and were rejected. | DIVERGENCE (disclosed) | `store.py::_build_document` | §3.5.3 |
| F6 | Stage 2 | Evolutionary domain taxonomy (`DomainTaxonomy`) grows organically; `is_generic` bypass for cross-domain caps (Phase E fix). | IMPROVEMENT | `taxonomy.py` | §3.5.3 |
| F7 | Stage 3 | Per-gap classification (not per-task). Two gaps in same task can route to different generators. | IMPROVEMENT | `classifier.py::classify_gap` | §3.5.4 |
| F8 | Stage 3 | Three `CapabilityType` enum values exactly match proposal's three types. | ALIGNED | `src/schemas/capability.py::CapabilityType` | §3.4 |
| F9 | Stage 3 | 3× retry + fail-closed (`classification_succeeded` sticky flag + CLI exit 5). Pre-fix silent MCP_TOOL default was exactly the Pillar-1.3 type-mismatch failure mode. | IMPROVEMENT (§12 finding #16) | `classifier.py` | §3.5.4 |
| F10 | Stage 3 | File-writer override (`_enforce_file_writer_mcp_tool`) structurally enforces Pillar 1.3 "deterministic + stateless → MCP tool" for `write-*` gaps when `required_files` is non-empty. Two-layer defense (prompt Example G + post-hoc override). | IMPROVEMENT (§12 finding #11) | `classifier.py` + `prompts/classifier.py` | §3.5.4 |
| F11 | Stage 3 | Proposal's "TOML-based provider-agnostic config" for agent model selection is realized as `src/config.py` Python module + env-var override (`JIT_LLM_MODEL`), not TOML. Underlying provider-agnostic property via `init_chat_model("provider:model")` preserved. | DIVERGENCE (cosmetic, disclosed) | `src/config.py` | §3.5.4 |
| F12 | Stage 4 | Generators are **plain LangGraph nodes with structured output + one-shot repair**, not DeepAgents per type. Single-pass structured output is more reliable for code gen than agent planning loops. | DIVERGENCE (deliberate, disclosed in CLAUDE.md) | `generators/{mcp_tool,skill,agent}.py` | §3.5.5 |
| F13 | Stage 4 | Proposal says MCP tool generation includes a **test harness**; implementation defers this to Pillar 2's three-tier gate (external, adversarial). Stronger than self-generated tests due to LLM self-collusion failure mode. | DIVERGENCE (strict improvement, disclosed) | `src/verification/gate.py` | §3.5.5 |
| F14 | Stage 4 | Lead-with-subagents mode for ≥ 2 AGENT gaps (Phase 3.5b). ONE lead + N isolated subagent contexts coordinated via `task(name=…, task=…)`. | IMPROVEMENT | `generators/agent.py::_generate_lead_with_subagents` | §3.5.5 |
| F15 | Stage 4 | Every generator prompt carries an "Input/Output Schema Contract" section derived from decomposer `output_schema` + upstream predecessors. Structured State Communication at generation granularity. | IMPROVEMENT (§12 finding #12 Layer 3) | `generators/utils.py::build_schema_context` | §3.5.5, §4 |
| F16 | Stage 5 | "Sandboxed MCP server" realized as **importlib + ephemeral Docker subprocess**, not a persistent FastMCP server. No ports, no service lifecycle. Full MCP server deployment listed as Chapter 7 future work. | DIVERGENCE (deliberate, disclosed in CLAUDE.md) | `tool_loader.py` + `sandbox/Dockerfile` | §3.5.6 |
| F17 | Stage 5 | Skill folder (`data/{eval,cli}/skills/<name>/SKILL.md`) and contained agent session (DeepAgent with `tools=[sandbox_execute]`) match proposal literally. | ALIGNED | `loading_node.py` + `generators/{skill,agent}.py` | §3.5.6 |
| F18 | Stage 5 | Generated MCP tool registration uses AST-extracted `input_schema` + `output_schema` (from `@mcp.tool`-decorated function signature + `return {…}` dict literals). Closes the long-standing empty-schema-at-registration TODO. | IMPROVEMENT | `schema_extraction.py::extract_mcp_tool_signature` | §3.5.6 |
| F19 | Structured State | Proposal specifies ONE handoff granularity; implementation enforces at **three** (pipeline-node, subtask-chain, generation-prompt) + a task-level `OutputContract` at the system boundary. | IMPROVEMENT (§12 finding #12) | `src/schemas/*` + `output_contract_resolver.py` | §4 |
| F20 | Structured State | No raw dicts between nodes. All `src/schemas/` models use `extra="forbid"`. CLAUDE.md codifies this as a hard rule. | ALIGNED | `src/schemas/*` | §3.4 |
| F21 | RQ-1.1 | Classification accuracy instrumented via task-level primary-match proxy (`TaskDefinition.expected_type` + `print_classification_accuracy`). Four-reason justification for proxy framing is baked into the analyzer docstring. | INSTRUMENTED with methodology disclosure | `scripts/analyze_results.py` + `tests/evaluation/tasks.py` | §5 |
| F22 | RQ-1.2 | Four baseline conditions (`static`, `no_verification`, `no_optimization`, `full`); `static` directly measures pre-built equivalents ceiling. | INSTRUMENTED | `src/schemas/evaluation.py::BaselineCondition` + harness | §5 |
| F23 | RQ-1.3 | JIT latency + token cost measured. **Escalation cost has no ground truth** — no human-in-the-loop code path exists. RQ answered one-sided; two-sided comparison would need a user study outside thesis scope. | PARTIALLY INSTRUMENTED (methodology limit) | LangFuse traces + `InvocationRecord` | §5 |
| F24 | Cross-cutting | Three-layer strict-gate pattern (decomposition / classification / verification) uniformly applies 3× retry + sticky flag + dedicated CLI exit code. Pre-fix fail-open paths at all three layers let unverified / mis-classified / degraded artifacts through production. | IMPROVEMENT (§12 findings #14/#15/#16) | `decomposition.py`, `classifier.py`, `verification_node.py` | §2 |
| F25 | Cross-cutting | Retry propagates verification feedback into regeneration prompts (`build_task_context` when `attempt_count > 0`). | IMPROVEMENT | `generators/utils.py` | §3.5.5 |

**Finding counts:** 14 ALIGNED-only / ALIGNED+IMPROVED, 5 DIVERGENCE (all disclosed, 3 deliberate improvements), 1 PARTIAL instrumentation (methodology limit, not an implementation gap). Zero undisclosed drifts.

---

## 7. What Chapter 4 (JIT Capability Pipeline) must disclose

Based on the findings above, Chapter 4's narrative must explicitly reproduce:

1. **F5 — hierarchical-embedding realization.** Acknowledge that the final embedding is a single prose document with a hierarchical prefix + metadata domain pre-filter, not a separate vector per level. Cite the Phase A recall regression (6–12 pp) that rejected true multi-level embeddings. Explain why the structural signal moved to Pillar 3 Merge's Jaccard instead.
2. **F11 — config format.** Python module + env var, not TOML. One-sentence mention; the provider-agnostic property via `init_chat_model("provider:model")` is preserved.
3. **F12 — generators are not DeepAgents.** Single-pass structured output + one-shot repair is the design choice. Reasoning: code-gen reliability, not an accident of scope.
4. **F13 — test harness.** MCP tool generation deliberately omits a generator-authored test harness; Pillar 2's three-tier gate fills that role. This is a strict improvement (LLM self-test collusion is well-documented).
5. **F16 — no persistent FastMCP server.** Importlib + ephemeral Docker per invocation. Full server deployment is Chapter 7 future work.
6. **F21 — RQ-1.1 task-level proxy.** Reproduce the four-reason docstring. Per-gap ground truth is ill-defined; task-level primary match is the operational analogue.
7. **F23 — RQ-1.3 one-sided.** Escalation cost has no ground truth. Reframe the RQ as *"what is the JIT end-to-end cost"* rather than a two-sided comparison.

Every other finding is **strict superset** of the proposal and can be framed as a methodology contribution rather than a caveat.

---

## 8. Bottom line

Pillar 1 is **fully and end-to-end realized** across the 5 stages, with Structured State Communication enforced at three granularities beyond the proposal's single-handoff framing and three deliberate divergences (hierarchical embedding realization, generator-as-node vs. generator-as-agent, sandboxed integration as importlib+Docker vs. persistent server) all disclosed in `CLAUDE.md` and `MASTER_PLAN_2026_04_13.md`. Research-question instrumentation is in place for RQ-1.1 and RQ-1.2; RQ-1.3 is partially instrumented subject to the methodology limit that no human-escalation code path exists to compare against. No undisclosed drift. No blockers to Chapter 4 prose.
