# Master's Thesis: Iteration Plan (v6)

## Verified JIT Capability Synthesis for Self-Developing Multi-Agent Systems

**Approach:** 8 iterations (0–7), each producing a working increment you can demo and evaluate. Each iteration lists concrete tasks, which ones parallelize well with Claude Code Agent Teams (your development tool, not part of the runtime system), and what to validate before moving on.

**Clarification:** Claude Code Agent Teams is used purely as a development workflow tool to parallelize coding work. The system you're building uses LangGraph for orchestration, DeepAgents (`deepagents` package) for autonomous multi-step reasoning nodes, MCP SDK for tool serving, and your own structured state protocol for agent communication.

**Changes from v5:** Post-Iteration-0 feasibility critique incorporated. Key changes:
- Testbed committed: Business Analytics Pipeline
- Pillar 3 split/merge scoped to Skills/Agent configs only (no MCP tool code refactoring)
- Verification gate adopts tiered cost optimization (fail fast on Tier 1 Critical)
- DeepAgent operational boundaries added (max_steps, timeout, fresh init)
- MCP server management decision point added (subprocess vs. importlib)
- Minimum viable scope defined per pillar as fallback
- Few-shot prompting required for all generator nodes
- Critique checkpoints scheduled at Iteration 2 and Iteration 5

---

## Pre-Iteration: Project State

The following is already in place:

- Project initialized with `uv` (`pyproject.toml`, `.python-version`)
- `CLAUDE.md` with project overview, tech stack, conventions
- `docs/iteration_plan.md` and `docs/thesis_proposal.md`
- `.claude/skills/langfuse-tracing/SKILL.md` — LangFuse integration patterns (grouped traces, scoring, prompt management)
- `.claude/skills/langgraph-architect/SKILL.md` — LangGraph architecture patterns (Command API, Send, interrupt, streaming, persistence)
  - `references/hitl-patterns.md` — Human-in-the-loop patterns (approve/reject, tool interrupts, validation loops)
  - `references/streaming-deep-dive.md` — Stream modes, custom streaming, subgraph streaming
  - `references/memory-and-store.md` — Cross-thread memory, message management, checkpointers
- `.claude/skills/langgraph-docs/SKILL.md` — Fetches live LangGraph docs from llms.txt
- `.claude/skills/pydantic-schemas/SKILL.md` — Pydantic schema conventions
- `.claude/skills/testing-conventions/SKILL.md` — pytest patterns, test structure
- `.claude/skills/deep-agents/SKILL.md` — When and how to use DeepAgents vs. plain nodes

### DeepAgents Skill Utilities

From the DeepAgents ecosystem, these utilities are available in `tools/`:

- `tools/quick_validate.py` — Validates skill SKILL.md structure (frontmatter, naming, field lengths)
- `tools/init_skill.py` — Scaffolds a new skill directory (SKILL.md template + scripts/references/assets)

These are used by the skill generator (Iteration 2) and the verification gate (Iteration 3).

### Skills Roadmap

| Skill | Create When | Purpose |
|---|---|---|
| `pydantic-schemas` | ✅ Done | Schema conventions for all state |
| `testing-conventions` | ✅ Done | pytest patterns, test structure |
| `deep-agents` | ✅ Done | DeepAgents vs. plain nodes decision framework |
| `langfuse-tracing` | ✅ Done | LangFuse observability patterns |
| `langgraph-architect` | ✅ Done | LangGraph architecture patterns + reference files |
| `langgraph-docs` | ✅ Done | Live LangGraph documentation fetching |
| `mcp-builder` | ✅ Done | MCP server development (FastMCP, tool design, testing, evaluation) |
| `verification-patterns` | Start of Iteration 3 | AST scanning, adversarial testing patterns |

---

## CLAUDE.md Update Protocol

**Update CLAUDE.md at the start of each iteration.** This is critical because:
- Every Claude Code session reads it automatically
- Every Agent Team teammate reads it automatically (but NOT the lead's conversation history)
- It's the primary way teammates understand what exists and what's stable

**What to update:**
1. **"What's Built" section** — list completed modules with brief descriptions
2. **"Current Iteration" section** — what's being built right now
3. **"Stable Interfaces" section** — modules that should not be modified (import from, don't edit)
4. **"Conventions" section** — any patterns that emerged (naming, error handling, etc.)

**Do it yourself or at minimum carefully review what Claude produces.** A drifted CLAUDE.md degrades every subsequent session.

---

## Iteration 0: Complete Scaffolding & Prove the Loop (Weeks 1–2) ✅ COMPLETE

**Goal:** Finish infrastructure setup and prove the core detection loop works end-to-end.

### What was built
- **`src/schemas/`** — Pydantic state schemas: `CapabilityType` (StrEnum), `CapabilityDescriptor`, `TaskState`, `GapItem`, `MatchItem`. All use `extra="forbid"`, `Field(description=...)`, `datetime.now(UTC)`.
- **`src/registry/store.py`** — ChromaDB-backed skill registry with cosine similarity search, gap detection (threshold 0.3), and `seed_registry()` with 3 hand-written tools (fibonacci, csv-parser, date-formatter). Supports `collection_name` parameter for test isolation.
- **`src/orchestrator/pipeline.py`** — 3-node LangGraph graph (`intake` → `gap_detection` → `report`) using Command API for routing. Classifies registry matches vs. gaps based on similarity threshold.
- **`src/mcp_tools/fibonacci.py`** — Hand-written FastMCP tool with `Annotated[int, Field()]` for flat MCP parameter schema, tool annotations (`readOnlyHint`, `idempotentHint`, etc.), async implementation.
- **`demo.py`** — End-to-end demo showing both paths (capability found + MCP call, gap detected). Wired with LangFuse `CallbackHandler` via `config={"callbacks": [...]}`.
- **`sandbox/Dockerfile`** — Minimal Python 3.11-slim container, non-root `sandbox` user, no network access.
- **`Makefile`** — Convenience targets: `test`, `lint`, `format` (all via `uv run`).
- **Tests** — 30 unit tests across 4 files (`test_schemas.py`, `test_registry.py`, `test_pipeline.py`, `test_fibonacci.py`) + 2 integration tests (`test_mcp_fibonacci.py`).

### Exit criteria: ✅ All met
- `uv run pytest` passes
- LangFuse traces the LangGraph flow
- Docker sandbox runs isolated Python
- Demo script shows "capability found" and "gap detected" paths
- Hand-written MCP fibonacci tool responds to calls from the graph
- CLAUDE.md updated with what's built

---

## Iteration 1: Production Registry, Schemas & Metrics (Weeks 2–3) ✅ COMPLETE

**Goal:** Upgrade the proof-of-concept into production-quality foundations.

### What was built
- **`src/schemas/`** — 14 Pydantic models across 4 files, all with `extra="forbid"`, `Field(description=...)`, StrEnum:
  - `capability.py`: `CapabilityType`, `CapabilityDescriptor` (expanded with `input_schema`, `output_schema`, `tags`, `domain`, `is_active`, `metadata` — all with defaults for backward compat)
  - `task.py`: `TaskStatus` (9-value StrEnum), `SubTask`, `ClassifiedGap`, `GeneratedArtifact`, `TaskState` (expanded with subtasks, classified_gaps, generated_artifacts, verification_results, status, errors, trace_id), `GapItem`, `MatchItem`
  - `verification.py` (NEW): `Severity` (5-value StrEnum), `VerificationFinding`, `VerificationReport`
  - `metrics.py` (NEW): `InvocationRecord`, `GranularityMetrics`
- **`src/registry/store.py`** — Production ChromaDB registry with hierarchical embeddings (`{domain} > {type} > {name}: {description}`), full CRUD (`get`, `update`, `deactivate`, `delete`, `list_all` with filters), `min_confidence` search filtering, `seed_registry()` with 8 capabilities (5 MCP_TOOL, 2 SKILL, 1 AGENT) across 5 domains.
- **`src/registry/metrics.py`** (NEW) — DuckDB metrics store with two tables (`invocations`, `capability_events`). Records invocations and lifecycle events. Computes four granularity metrics: `reuse_rate`, `success_rate`, `avg_token_cost`, `maintenance_burden`. Aggregate via `compute_granularity_metrics()`. Supports persistence to disk and context manager pattern.
- **Tests** — 115 passing: 57 schema tests, 29 registry tests, 17 metrics tests, 6 pipeline tests, 4 fibonacci tests, 2 integration tests. All lint clean.

### Note on gap detection
Gap detection (`detect_gaps()`) remains in `src/registry/store.py` rather than a separate `gap_detection.py` file. It was already implemented in Iteration 0 and upgraded with hierarchical search support. A dedicated `src/orchestrator/gap_detection.py` module will be created in Iteration 2 when the full pipeline graph is built.

### Tasks

1. **Full state schemas** (`src/schemas/`)
   - `TaskState` — subtasks, required capabilities, gaps, results
   - `CapabilityDescriptor` — type enum (MCP_TOOL / SKILL / AGENT), description, I/O schemas, version, metrics
   - `VerificationReport` — severity-ranked findings
   - `GranularityMetrics` — reuse rate, success rate, token cost, maintenance burden
   - Full validators, serialization, unit tests

2. **Hierarchical skill registry** (`src/registry/store.py`)
   - ChromaDB with hierarchical embeddings (domain → capability → specific skill)
   - CRUD: register, query with confidence threshold, update, deactivate
   - Persistence to disk
   - Seed with 5–10 capabilities across types
   - Replaces the minimal registry

3. **Skill gap detection** (`src/orchestrator/gap_detection.py`)
   - Query registry, return matches above threshold and gaps below
   - Configurable confidence threshold

4. **DuckDB metrics store** (`src/registry/metrics.py`)
   - Record: capability ID, timestamp, success/fail, token cost, latency
   - Query functions for four granularity metrics
   - Persist to disk

### Agent Teams usage
**3-teammate team:**

```
Create an agent team with 3 teammates:
- "schemas" teammate: implement full Pydantic state schemas in src/schemas/
  with validators, serialization, and unit tests. See docs/thesis_proposal.md
  for schema descriptions. Expand the minimal TaskState that already exists.
  Follow the pydantic-schemas skill (extra="forbid", Field descriptions, str enums).
- "registry" teammate: upgrade the ChromaDB registry in src/registry/store.py.
  Add hierarchical embeddings, confidence thresholds, disk persistence, full
  CRUD. Seed with 5-10 capabilities. Write tests. Import schemas from
  src/schemas/ — do not modify that package.
- "metrics" teammate: build the DuckDB metrics store in src/registry/metrics.py.
  Track invocations, outcomes, token costs, latency. Write query functions for
  reuse rate, success rate, token cost, maintenance burden. Write tests. Import
  schemas from src/schemas/ — do not modify that package.
Require plan approval before implementation.
```

### Exit criteria: ✅ All met
- All schemas validate and serialize cleanly
- Registry supports hierarchical search with persistence
- Gap detection works against the seeded registry
- Metrics store records and queries the four key metrics
- All tests pass
- CLAUDE.md updated

---

## Iteration 2: JIT Capability Pipeline (Weeks 3–5)

**Goal:** Build Pillar 1 end-to-end without verification. Decompose tasks, detect gaps, classify, generate, dynamically load, and register.

### Decision point: MCP server management strategy

Before starting, decide between two approaches for loading generated MCP tools:

**Option A: Subprocess MCP servers** (`server_manager.py`). Spawn each generated tool as a FastMCP server subprocess, health-check it, register the endpoint. This is production-realistic but adds subprocess lifecycle complexity, zombie process risk, and port management overhead.

**Option B: Dynamic import + sandbox execution.** Generate MCP-compliant code (FastMCP structure) but load it via `importlib` in a sandboxed subprocess rather than running a persistent server. Simpler lifecycle, same generation/verification loop, avoids subprocess management. Mention full MCP server deployment as production future work in Chapter 7.

**Recommendation:** Start with Option B. If it works cleanly, keep it. If you need persistent servers for evaluation realism, upgrade to Option A for Iteration 5.

### Tasks

1. **Task decomposition agent** (`src/orchestrator/decomposition.py`)
   - **Implemented as a DeepAgent** (`create_deep_agent()`)
   - The DeepAgent uses built-in `write_todos` for planning, file system tools for context management
   - Invoked from within a LangGraph node function (DeepAgents are complete graphs, not nodes)
   - Use `response_format` for structured output (Pydantic model with subtasks)
   - System prompt instructs the agent to plan decomposition, produce subtasks with capability annotations, and write results to files
   - Use `deepseek-chat` as the model (`deepseek-reasoner` lacks `tool_choice` support required by DeepAgents)
   - **Operational boundaries:** `recursion_limit=25` (~12 logical steps; each tool call = 2 graph steps), timeout at node level (120s), fresh initialization per invocation
   - Trace with LangFuse via callback handler

2. **Capability classifier** (`src/orchestrator/classifier.py`)
   - Plain LangGraph node (no DeepAgent — straightforward classification)
   - LLM-based semantic classification with `temperature=0` and `with_structured_output`
   - Few-shot prompt with intent-focused category definitions (action nature, not domain vocabulary)
   - *Revised from rule-based keywords during implementation: keyword lists were too broad (LLM fallback never triggered) and classified by syntax rather than intent*

3. **MCP tool generator** (`src/orchestrator/generators/mcp_tool.py`)
   - Plain LangGraph node
   - Given a capability description + I/O schema, generate a Python MCP tool
   - Use Claude API (or DeepSeek) for code generation
   - **Few-shot prompting required:** include a complete valid MCP tool example in the prompt
   - **Repair pass:** if Pydantic validation fails on the response, send raw output + validation error back to the model once
   - Output: Python function + FastMCP server wrapper + basic test harness

4. **MCP tool loading** (`src/orchestrator/server_manager.py` or `tool_loader.py`)
   - Based on decision point above: either subprocess lifecycle manager or importlib dynamic loader
   - Registry integration: new tool → load → register
   - Cleanup on process exit

5. **Skill generator** (`src/orchestrator/generators/skill.py`)
   - Plain LangGraph node
   - Generate SKILL.md with YAML frontmatter and instructions, conforming to DeepAgents skill format
   - Must produce the canonical structure: `SKILL.md` + optional `scripts/`, `references/`, `assets/`
   - **Few-shot prompting required:** include a complete valid SKILL.md example in the prompt
   - Validate output with `tools/quick_validate.py` before returning (structural pre-check)
   - Frontmatter rules: `name` (hyphen-case, max 64 chars), `description` (max 1024 chars, no angle brackets)
   - Use `tools/init_skill.py` as a reference for the expected template structure

6. **Agent generator** (`src/orchestrator/generators/agent.py`)
   - Plain LangGraph node
   - Generate system prompt and LangGraph node config
   - For DeepAgent-type agents, also generate skill configurations targeting `~/.deepagents/<agent>/skills/`

7. **Pipeline graph** (`src/orchestrator/pipeline.py`)
   - Full LangGraph graph: decomposition node (invokes DeepAgent) → gap detection → classification → generation → loading → registration
   - Use Command API for routing between nodes
   - Trace everything with LangFuse

### Prompting strategy
*Phase A (single session):* Build the task decomposition DeepAgent and the classifier. The DeepAgent is a `create_deep_agent()` call wrapped in a LangGraph node function. Test it in isolation — verify that `write_todos` planning works and `response_format` returns structured subtasks.

*Phase B (3-teammate Agent Team):*
```
Create an agent team with 3 teammates:
- "mcp-gen" teammate: implement the MCP tool generator in
  src/orchestrator/generators/mcp_tool.py AND the tool loader in
  src/orchestrator/server_manager.py (or tool_loader.py). The generator produces
  Python MCP tools (FastMCP) from a CapabilityDescriptor. Include at least one
  complete few-shot example of a valid MCP tool in the generator prompt. Add a
  repair pass for malformed output. The loader handles dynamic import and
  execution. Write integration tests that generate a simple tool and verify it
  executes correctly. See the mcp-builder skill for conventions.
- "skill-gen" teammate: implement the skill generator in
  src/orchestrator/generators/skill.py. Generate SKILL.md files + optional
  scripts/references/assets from CapabilityDescriptor, conforming to the
  DeepAgents skill format. Include a complete few-shot example in the prompt.
  Run tools/quick_validate.py on every generated skill as a structural pre-check.
  Use tools/init_skill.py as a reference for the expected template. Write tests
  including a test that validates the generated skill passes quick_validate.py.
- "agent-gen" teammate: implement the agent generator in
  src/orchestrator/generators/agent.py. Generate system prompts and LangGraph
  node configs. For DeepAgent-type agents, also produce skill configurations.
  Write tests.
Require plan approval. Only approve plans that include test coverage and
use the existing schemas from src/schemas/.
```

*Phase C (single session):* Wire everything into the LangGraph pipeline graph. Use Command API for node routing.

### Exit criteria
- DeepAgent decomposition produces structured subtask breakdowns using `write_todos` and `response_format`
- DeepAgent respects `max_steps` limit and completes within timeout
- LangFuse traces show the DeepAgent's planning and tool-calling loop
- Pipeline decomposes tasks, detects gaps, classifies, and generates capabilities
- Generated MCP tools load dynamically and respond to calls
- Generated skills pass `tools/quick_validate.py` validation
- Tool loading handles lifecycle cleanly
- LangFuse traces full pipeline end-to-end
- **Update CLAUDE.md**

### 🔍 CRITIQUE CHECKPOINT after this iteration
Before starting Iteration 3, assess: Are the generated capabilities good enough for verification to have something meaningful to work with? If generation quality is too low, adjust Iteration 3's expectations.

---

## Iteration 3: Verification Layer (Weeks 5–7)

**Goal:** Build Pillar 2 — three-tier verification gate. Core thesis contribution.

### Tasks

1. **Tier 1: Static scanner** (`src/verification/static_scanner.py`)
   - Plain Python (no LLM) — AST-based + regex pattern detection
   - Detect: hardcoded data, silent exception handling, mock artifacts, synthetic data indicators, missing external calls
   - For skill-type capabilities: also run `tools/quick_validate.py` as a structural check
   - Output `VerificationReport` with severity-ranked findings
   - Zero external dependencies
   - **Cost gate: Critical findings → reject immediately, do not proceed to Tier 2/3**

2. **Tier 2: Behavioral testing** (`src/verification/behavioral.py`)
   - Plain LangGraph node
   - Verifier agent generates test inputs for a given capability
   - Four test categories: nonexistent entity, consistency, boundary, latency analysis
   - Runs tests in Docker sandbox
   - Appends results to `VerificationReport`
   - **Only runs if Tier 1 passes or has only High/Medium findings**

3. **Tier 3: Adversarial probing** (`src/verification/adversarial.py`)
   - **Implemented as a DeepAgent** (`create_deep_agent()`)
   - Invoked from within a LangGraph node function
   - Uses `write_todos` to plan attack strategy
   - Uses sandbox backend with `execute` tool to run probes in isolation
   - Writes intermediate analysis to files (context management)
   - Tests network-disconnected behavior
   - Uses `response_format=AdversarialFindings` for structured output
   - **Operational boundaries:** `max_steps=15`, timeout at node level (180s), fresh initialization
   - **Only runs if Tier 2 passes**
   - **Focused on 3 specific attack patterns:** (1) hidden demo/sample mode detection, (2) network-disconnect behavioral change, (3) ground-truth comparison where available
   - Trace with LangFuse

4. **Verification gate** (`src/verification/gate.py`)
   - Combines all three tiers with tiered cost optimization:
     - Tier 0 (Skills only): `quick_validate.py` structural check (~0 cost)
     - Tier 1: Static scan (~0 cost). Critical → reject immediately
     - Tier 2: Behavioral testing (moderate cost). Only if Tier 1 passes
     - Tier 3: Adversarial probing (high cost). Only if Tier 2 passes
   - Returns pass/fail + detailed report

5. **Pipeline integration** — Verification node between generation and registration. Failed capabilities get feedback and can be regenerated (retry loop, max 3 attempts — "strike system"). If generation fails 3 times, fail open with "Unable to generate" rather than hallucinating success. Pass the `VerificationReport` from the previous failure into the prompt for the next attempt. Use Command API to route between generation → verification → registration with loop-back on failure.

### Skills to create first
- `.claude/skills/verification-patterns/SKILL.md`

### Agent Teams usage
**4-teammate team** (justified by thesis contribution):

```
Create an agent team with 4 teammates:
- "static" teammate: Tier 1 static scanner in src/verification/static_scanner.py.
  AST + regex for 5 contamination categories. Zero external deps beyond stdlib.
  For skill-type capabilities, also integrate tools/quick_validate.py as a
  structural pre-check. Output VerificationReport. Critical findings must block
  immediately without proceeding to Tier 2/3. Comprehensive tests with
  deliberately contaminated code samples. See the verification-patterns skill.
- "behavioral" teammate: Tier 2 behavioral testing in src/verification/behavioral.py.
  Plain LangGraph node. 4 test categories (nonexistent entity, consistency,
  boundary, latency). Docker sandbox execution. Tests with mock-contaminated tools.
- "adversarial" teammate: Tier 3 adversarial probing in src/verification/adversarial.py.
  Implement as a DeepAgent (create_deep_agent with sandbox backend). The agent
  uses write_todos for planning, execute tool for running probes, file system
  for intermediate analysis. Use response_format for structured findings.
  max_steps=15, timeout=180s. Focus on 3 attack patterns: hidden demo mode,
  network-disconnect behavior change, ground-truth comparison.
  Trace with LangFuse. See the deep-agents skill for patterns. Write tests.
- "devil" teammate: after the other 3 finish, review all tiers and find ways a
  malicious or lazy code generator could bypass each tier. Pay special attention
  to whether the adversarial DeepAgent's probing can be tricked. Document
  findings in docs/decisions/verification-hardening.md.
Have teammates share findings with each other. Require plan approval.
```

### Exit criteria
- Static scanner catches all 5 contamination categories
- Tier 1 Critical findings block immediately (no Tier 2/3 invocation)
- Skill validation via `quick_validate.py` integrated into the gate
- Behavioral tests distinguish real from mock tools
- Adversarial DeepAgent runs multi-step probing within max_steps and timeout
- LangFuse traces show the adversarial agent's full execution (planning, tool calls, file writes)
- The full gate rejects bad capabilities, passes good ones
- Pipeline retries generation on failure (max 3 attempts with feedback)
- Devil's advocate findings documented
- **Update CLAUDE.md**

---

## Iteration 4: Granularity Optimizer (Weeks 7–9)

**Goal:** Build Pillar 3 — evolutionary feedback loop.

### Scoping decision (from feasibility critique)

Split and Merge operations apply **only to Skills and Agent configs**, not to MCP tool source code. Automated code refactoring is beyond reliable LLM capability and would consume weeks of debugging. Prune applies to all artifact types. The formal framework (utility function, convergence analysis) is type-agnostic — the thesis describes the full framework and implements it where LLMs can perform the operations.

### Tasks

1. **Utility computation** (`src/optimizer/utility.py`)
   - Implement $U(c) = \alpha \cdot \text{reuse}(c) \cdot \text{success}(c) - \beta \cdot \text{token\_cost}(c) - \gamma \cdot \text{maintenance}(c)$
   - Pull metrics from DuckDB
   - Configurable weights ($\alpha$, $\beta$, $\gamma$)

2. **Split operation** (`src/optimizer/operations.py`)
   - Detect candidates: high reuse + mediocre success (>60% reuse, <70% success)
   - **Applies to Skills and Agent configs only.** Check `capability_type` before attempting.
   - **Optional DeepAgent for failure analysis**: `create_deep_agent()` that investigates why capabilities fail, using file system to manage large failure logs and `write_todos` to plan investigation
   - Generate specialized variants (reuses the JIT generator from Pillar 1)
   - Register new variants, deactivate parent

3. **Merge operation** (same file)
   - Detect candidates: low reuse (<5%) + high embedding similarity
   - **Applies to Skills only.** Check `capability_type` before attempting.
   - Merge into broader capability (combining instruction sets — a natural LLM strength)
   - Register merged version, deactivate parents

4. **Prune operation** (same file)
   - Detect: zero invocations over configurable window, no dependencies
   - **Applies to all artifact types.**
   - Archive (don't delete)

5. **Optimization loop** (`src/optimizer/loop.py`)
   - LangGraph graph that runs after each task cycle
   - Compute metrics → identify candidates → apply operations (respecting type constraints) → update registry
   - Track KL divergence between consecutive registry snapshots for convergence

6. **Simulation harness** (`src/optimizer/simulation.py`)
   - Synthetic task generator with configurable distribution
   - Run N cycles and observe registry evolution
   - Plot convergence curves (matplotlib)

### Agent Teams usage
**2-teammate team:**

```
Create an agent team with 2 teammates:
- "optimizer" teammate: implement utility computation and the three evolutionary
  operations (split, merge, prune) in src/optimizer/. Split applies to Skills
  and Agent configs only. Merge applies to Skills only. Prune applies to all
  types. Check capability_type before attempting split/merge. For the split
  operation, optionally implement failure analysis as a DeepAgent
  (create_deep_agent with write_todos for planning and file system for managing
  failure logs). Start with a simpler LLM-based analysis and upgrade if needed.
  Write unit tests with synthetic data.
- "simulation" teammate: build the simulation harness in src/optimizer/simulation.py.
  Configurable task distributions, optimization cycles, KL divergence convergence
  analysis, matplotlib plots. Write tests.
Wait for both to finish, then wire the optimization loop into the main pipeline.
```

### Exit criteria
- Split, merge, prune all work correctly with type constraints enforced
- Attempting split/merge on MCP tools is gracefully rejected (not silently skipped)
- Simulation shows convergence over 50+ cycles
- Utility scores improve vs. no-optimization baseline
- If DeepAgent failure analysis is used, LangFuse traces show the reasoning
- **Update CLAUDE.md**

---

## Iteration 5: Integration & End-to-End Testing (Weeks 9–11)

**Goal:** Wire all three pillars together. Build evaluation infrastructure and baselines.

### Testbed: Business Analytics Pipeline

Design 10–15 tasks spanning multiple difficulty levels:

**Trivially simple (3–4 tasks):** String manipulation, basic statistics, date formatting. These should always succeed end-to-end and guarantee analyzable data even if complex tasks fail.

**Moderate (4–5 tasks):** CSV ingestion with schema detection, data filtering/aggregation, basic visualization generation. These exercise the generation pipeline without requiring external APIs.

**Complex (3–4 tasks):** Multi-source data joins with schema reconciliation, ML model training pipelines, report generation combining multiple data sources. These may fail — that's fine, the failure modes are themselves data for the thesis.

### Tasks

1. **Full pipeline graph** — All pillars connected:
   Task → Decomposition (DeepAgent invoked from node) → Gap Detection → Classification → Generation → Verification (tiered cost gate, incl. Adversarial DeepAgent invoked from node) → Loading → Registration → Execution → Metrics → Optimization (type-constrained operations)

2. **Testbed setup** — 10–15 tasks as described above, with expected capabilities and ground truth

3. **Shared evaluation harness** (`tests/evaluation/harness.py`) — Common interface for all baselines

4. **Baseline implementations:**
   - **Static skills** — hand-curated library, no JIT
   - **JIT without verification** — Pillar 1 only, bypass Pillar 2
   - **JIT without optimization** — Pillars 1 + 2, bypass Pillar 3
   - **Full system** — all three pillars active

5. **Integration tests** — Full lifecycle, capability reuse, evolutionary operations

6. **Error handling** — Generation failure, sandbox timeout, server crash, corrupted registry. Use Command API loop-back for recoverable errors, RetryPolicy for transient failures (see `langgraph-architect` skill error handling section).

### Agent Teams usage
**3-teammate team for baselines:**

```
Create an agent team with 3 teammates:
- "static-baseline": hand-curated skill library, wired to evaluation harness.
- "no-verify-baseline": pipeline with Pillar 2 bypassed (no verification gate,
  no adversarial DeepAgent). Wired to harness.
- "no-optimize-baseline": pipeline with Pillar 3 bypassed (no granularity
  operations). Wired to harness.
All baselines must output metrics to the same DuckDB schema via the shared harness.
```

### Exit criteria
- Full system runs 10+ tasks end-to-end
- All 4 conditions produce comparable metrics via shared harness
- LangFuse traces show complete lifecycle including DeepAgent executions
- At least one prune occurs naturally; split/merge on Skills if metrics warrant
- Trivially simple tasks succeed in all conditions
- **Update CLAUDE.md**

### 🔍 CRITIQUE CHECKPOINT after this iteration
Before starting Iteration 6, assess: Does the evaluation design actually produce the data needed for the thesis? Are there enough successful end-to-end runs for statistical analysis? If not, adjust task difficulty or fix pipeline issues before committing to formal evaluation.

---

## Iteration 6: Evaluation & Analysis (Weeks 11–13)

**Goal:** Run formal evaluation, collect data, produce thesis figures.

### Tasks

1. **Run all baselines** — ≥5 runs per condition for statistical significance
2. **Collect metrics** — success rate, contamination rate, reuse distribution (Gini), token cost, time-to-first-correct, convergence
3. **Manual audit** — 20–30 generated capabilities, classify: correct / contaminated / partial
4. **DeepAgent analysis** — Separately measure: how many tool calls do DeepAgents make per invocation? Does the adversarial agent's multi-step probing catch things a single-pass approach misses? Token cost of DeepAgent nodes vs. plain nodes?
5. **Verification cost analysis** — How often does Tier 1 reject immediately (saving Tier 2/3 cost)? What's the token cost distribution across tiers?
6. **Figures** — success comparison, contamination rates, registry evolution, convergence curve, token breakdown, reuse distribution, DeepAgent analysis, verification tier cost distribution
7. **Statistical analysis** — significance tests for key comparisons

### Agent Teams usage
**2-teammate team:**

```
Create an agent team with 2 teammates:
- "analysis": run evaluation suite, collect metrics into DuckDB, compute
  summary statistics, significance tests, export CSVs. Include a separate
  analysis of DeepAgent behavior (tool call counts, planning steps, token cost).
  Include verification tier cost analysis (how often Tier 1 rejects early).
- "visualization": publication-quality matplotlib figures from CSVs.
  Academic conventions, consistent style. PNG + PDF to docs/figures/.
Analysis must finish before visualization starts.
```

### Exit criteria
- All metrics collected with multiple runs
- Publication-quality figures
- Statistical significance confirmed or reported
- DeepAgent effectiveness analyzed separately
- Verification cost optimization documented
- Manual audit documented
- **Update CLAUDE.md**

---

## Iteration 7: Thesis Writing (Weeks 13–16)

**Goal:** Write the thesis. Agent Teams for research/review support only.

### Writing order
1. Chapter 3 (JIT Pipeline) — include DeepAgent decomposition design rationale
2. Chapter 4 (Verification) — core contribution, include adversarial DeepAgent design, tiered cost optimization
3. Chapter 5 (Granularity Optimization) — full formal framework, note type-scoped implementation, discuss MCP tool split/merge as future work
4. Chapter 6 (Evaluation) — include DeepAgent analysis subsection, verification cost analysis
5. Chapter 2 (Related Work)
6. Chapter 1 (Introduction)
7. Chapter 7 (Conclusion)

### Agent Teams usage
**Research and review only:**

```
Create an agent team with 3 teammates:
- "related-work": for each reference paper, summarize approach and identify
  what it does NOT do that this thesis addresses. Output comparison table
  to docs/related_work_analysis.md.
- "code-review": review src/ for documentation quality. Add docstrings,
  verify type hints, resolve TODOs. Do not change functionality.
- "figure-check": verify all figures match raw data. Check labels, legends,
  scales. Flag anything misleading.
```

### Exit criteria
- Complete thesis draft
- All figures embedded and referenced
- Code documented and reproducible
- **Final CLAUDE.md update**

---

## Cross-Cutting: DeepAgent Operational Boundaries

DeepAgents are powerful but expensive and unpredictable. These constraints apply to every DeepAgent invocation:

| Constraint | Value | Rationale |
|---|---|---|
| `max_steps` (decomposition) | 10 | Decomposition is bounded — there are only so many subtasks |
| `max_steps` (adversarial) | 15 | Adversarial probing needs more exploration room |
| `max_steps` (failure analysis) | 10 | Failure analysis is focused investigation |
| Node-level timeout (decomposition) | 120s | Prevents pipeline hang if agent loops |
| Node-level timeout (adversarial) | 180s | Longer for sandbox execution |
| Initialization | Fresh every time | No state carried between invocations |
| Output contract | `response_format=PydanticModel` | Structured output, not free-text parsing |
| Fallback | If timeout/max_steps hit, return partial results with error flag | Pipeline continues with degraded data rather than hanging |

Monitor all DeepAgent metrics via LangFuse. If costs are too high, replace with plain LangGraph nodes (single LLM call + structured output) and document the quality tradeoff in the thesis.

## Cross-Cutting: DeepAgents Token Budget

| DeepAgent Component | What It Does | Approx. Token Multiplier vs. Single LLM Call |
|---|---|---|
| Task decomposition | Plans via `write_todos`, iterates through tool calls | 3–4x |
| Adversarial probing | Plans attacks, executes in sandbox, writes analysis to files | 4–5x |
| Failure analysis (optional) | Investigates failure patterns, manages logs via files | 2–3x |

## Cross-Cutting: Minimum Viable Scope

If timeline pressure forces scope reduction, each pillar has a defined minimum:

| Pillar | Full Scope | Minimum Viable | What You Lose |
|---|---|---|---|
| **Pillar 1** | Generate MCP tools, Skills, and Agents | Generate MCP tools only | Classification becomes trivial; less artifact diversity |
| **Pillar 2** | Tier 1 + Tier 2 + Tier 3 | Tier 1 + Tier 2 only | Lose adversarial probing DeepAgent; discuss Tier 3 theoretically with attack taxonomy |
| **Pillar 3** | Prune (all) + Split (Skills) + Merge (Skills) | Prune only + full formal framework | Demonstrate framework theoretically; simulation with synthetic metrics still shows convergence |

The thesis contribution survives at minimum viable scope: the pipeline, verification taxonomy, and formal framework are all present. The evaluation is narrower but still produces ablation data.

## Cross-Cutting: LLM Output Robustness

All LLM-calling nodes must implement:

1. **Few-shot prompting.** Every generator (MCP tool, Skill, Agent) includes at least one complete valid example in the prompt. Don't just give the schema — give a full example of correct output.
2. **Repair pass.** If Pydantic validation fails on the response, send the raw output + the validation error back to the model once. If the repair also fails, return an error.
3. **LangFuse logging of all failures.** Every malformed output, validation error, and repair attempt is logged so you have data for the thesis on LLM reliability.

## Cross-Cutting: When to Use What

| Situation | Development Tool |
|---|---|
| Sequential setup, config, wiring | **Single Claude Code session** |
| Independent modules with clear interfaces | **Agent Teams** (2–3 teammates) |
| Core contribution needing adversarial review | **Agent Teams** (4 teammates with devil's advocate) |
| Quick code generation within a larger task | **Subagents** |
| Parallel baselines or analysis | **Agent Teams** (2–3 teammates) |
| Writing prose | **Single session** |

| Situation | Runtime Component |
|---|---|
| Multi-step autonomous reasoning (decomposition, adversarial probing) | **DeepAgent** (`create_deep_agent()`, invoked from a node) |
| Straightforward transforms, classification, generation | **Plain LangGraph node** |
| Deterministic analysis (static scanning, metrics) | **Plain Python (no LLM)** |

## Cross-Cutting: Critique Checkpoints

| When | What to Assess |
|---|---|
| After Iteration 0 ✅ | Architecture feasibility, dependency risks, scope | 
| After Iteration 2 | Generation quality sufficient for verification? DeepAgent integration stable? |
| After Iteration 5 | Evaluation design produces usable data? Enough end-to-end successes? |

These are full feasibility reviews, not just exit criteria checks. Budget 1–2 hours for each.

## Cross-Cutting: Git Strategy

- Branch per iteration: `iter/0-scaffolding`, `iter/1-registry`, etc.
- When using Agent Teams, each teammate owns different files
- Merge to `main` at exit criteria

## Cross-Cutting: Risk Management

| Risk | Mitigation |
|---|---|
| LLM generation quality too low | Few-shot prompting, repair pass, start simple |
| Verification too strict | Tune Tier 1 thresholds; tiered cost gate prevents wasting tokens on doomed artifacts |
| Verification too loose | Devil's advocate in Iter 3; manual audit in Iter 6 |
| DeepAgent hangs/loops | max_steps limit, node-level timeout, fallback to partial results |
| DeepAgent costs too high | Track via LangFuse; fall back to plain nodes if needed |
| Optimizer tries to merge MCP tool code | Type constraint check prevents this; graceful rejection |
| Optimizer doesn't converge | Debug with simulation harness; simplify distribution |
| Dynamic MCP tool loading flaky | Start with importlib (Option B); upgrade to subprocess if needed |
| Agent Teams overhead too high | Start with 2 teammates; scale up only when justified |
| Generated skills don't conform to format | Validate with `tools/quick_validate.py` in gate |
| DeepSeek output malformed | Few-shot examples, repair pass, LangFuse logging |
| Scope creep | Exit criteria per iteration, minimum viable scope defined |
| Timeline overrun | Minimum viable scope per pillar, critique checkpoints catch problems early |

---

## Timeline Summary

| Iteration | Weeks | Focus | Agent Teams? | DeepAgents? |
|---|---|---|---|---|
| 0 ✅ | 1–2 | Scaffolding + Prove the Loop | No | No |
| 1 | 2–3 | Production Registry, Schemas & Metrics | Yes (3) | No |
| 2 | 3–5 | JIT Pipeline + Tool Loading | Yes (3, phased) | Yes: decomposition |
| — | — | 🔍 Critique checkpoint | — | — |
| 3 | 5–7 | Verification Layer | Yes (4) | Yes: adversarial probing |
| 4 | 7–9 | Granularity Optimizer (type-scoped) | Yes (2) | Optional: failure analysis |
| 5 | 9–11 | Integration, E2E & Baselines | Yes (3) | All DeepAgents active |
| — | — | 🔍 Critique checkpoint | — | — |
| 6 | 11–13 | Evaluation & Analysis | Yes (2) | Measuring DeepAgent effectiveness |
| 7 | 13–16 | Thesis Writing | Yes (review only) | Documenting design rationale |

**Total: ~16 weeks**
