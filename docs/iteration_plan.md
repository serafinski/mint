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

### ✅ Decision: MCP tool loading strategy → Option B (dynamic import)

Generated MCP tools are loaded via `importlib` in a sandboxed subprocess — **not** as persistent FastMCP server processes. The tool loader lives in `src/orchestrator/tool_loader.py`.

**Why not subprocess MCP servers:** Zombie processes, port management, and health-check overhead are orthogonal to the thesis contribution. Option B preserves the same FastMCP code format and upgrade path while keeping lifecycle simple. Tier 2 behavioral testing (Iteration 3) reuses the same loader directly — no duplication.

**Full MCP server deployment** is documented as production future work in Chapter 7.

### ✅ Decision: Generated skills landing path → `.deepagents/skills/`

Generated SKILL.md files are written to `.deepagents/skills/<skill-name>/` in the project root — **not** `src/skills/` (removed). DeepAgents loads project-level skills from `.deepagents/skills/` automatically, so no copy or symlink step is needed. User-level (`~/.deepagents/<agent>/skills/`) is reserved for stable, hand-curated skills.

### Tasks

1. **Task decomposition agent** (`src/orchestrator/decomposition.py`)
   - **Implemented as a DeepAgent** (`create_deep_agent()`)
   - The DeepAgent uses built-in `write_todos` for planning, file system tools for context management
   - Invoked from within a LangGraph node function (DeepAgents are complete graphs, not nodes)
   - Use `response_format` for structured output (Pydantic model with subtasks)
   - System prompt instructs the agent to plan decomposition, produce subtasks with capability annotations, and write results to files
   - Use `openai:gpt-5-mini` as the model (unified across all components since March 2026; originally `deepseek-chat`)
   - **Operational boundaries:** `recursion_limit=50` (~17 logical steps; each tool call = 2 graph steps), timeout at node level (120s), fresh initialization per invocation
   - Trace with LangFuse via callback handler

2. **Capability classifier** (`src/orchestrator/classifier.py`)
   - Plain LangGraph node (no DeepAgent — straightforward classification)
   - LLM-based semantic classification with `temperature=0` and `with_structured_output`
   - Few-shot prompt with intent-focused category definitions (action nature, not domain vocabulary)
   - *Revised from rule-based keywords during implementation: keyword lists were too broad (LLM fallback never triggered) and classified by syntax rather than intent*

3. **MCP tool generator** (`src/orchestrator/generators/mcp_tool.py`)
   - Plain LangGraph node
   - Given a capability description + I/O schema, generate a Python MCP tool
   - Use `openai:gpt-5-mini` for code generation (configurable via `src/config.py`)
   - **Few-shot prompting required:** include a complete valid MCP tool example in the prompt
   - **Repair pass:** if Pydantic validation fails on the response, send raw output + validation error back to the model once
   - Output: Python function + FastMCP server wrapper + basic test harness

4. **MCP tool loading** (`src/orchestrator/tool_loader.py`)
   - Dynamic import via importlib + sandboxed subprocess execution (see decided loading strategy in this file)
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
  src/orchestrator/tool_loader.py. Loading strategy: dynamic import via importlib
  (NOT subprocess MCP servers — see the decided loading strategy above).
  The generator produces Python MCP tools (FastMCP) from a CapabilityDescriptor.
  Include at least one complete few-shot example of a valid MCP tool in the
  generator prompt. Add a repair pass for malformed output. The loader handles
  importlib dynamic loading and sandboxed subprocess execution. Write integration
  tests that generate a simple tool and verify it executes correctly.
  See the mcp-builder skill for conventions.
- "skill-gen" teammate: implement the skill generator in
  src/orchestrator/generators/skill.py. Generated skills must be written to
  .deepagents/skills/<skill-name>/ (NOT src/skills/ — see the decided landing
  path above). Generate SKILL.md files + optional scripts/references/assets from
  CapabilityDescriptor, conforming to the DeepAgents skill format. Include a
  complete few-shot example in the prompt. Run tools/quick_validate.py on every
  generated skill as a structural pre-check. Use tools/init_skill.py as a
  reference for the expected template. Write tests including a test that validates
  the generated skill passes quick_validate.py.
- "agent-gen" teammate: implement the agent generator in
  src/orchestrator/generators/agent.py. Generate system prompts and LangGraph
  node configs. For DeepAgent-type agents, also produce skill configurations.
  Write tests.
Require plan approval. Only approve plans that include test coverage and
use the existing schemas from src/schemas/.
```

### ✅ Decision: Generator chaining strategy → sequential (not Send fan-out)

The three generator nodes are chained sequentially: `generate_mcp_tool` → `generate_skill` → `generate_agent`. Each node filters by `classified_type` and hands off via `Command(goto=...)`. No `TaskState` schema changes (no Annotated reducers) are required. Send-based parallel fan-out is future work.

### ✅ Decision: `DECOMPOSITION_RECURSION_LIMIT` raised to 50

Integration tests confirmed `recursion_limit=25` was too low for the DeepAgent to complete real tasks. Raised to 50 (~25 logical steps). The production fallback (return partial results with error flag) remains in place.

*Phase C (single session):* ✅ COMPLETE

**Goal:** Wire all Phase A/B components into a single end-to-end LangGraph pipeline. No new LLM logic — this is plumbing work only.

**Final graph topology:**
```
START → decomposition_node → gap_detection → classifier_node
      → generate_mcp_tool → generate_skill → generate_agent
      → loading_node → END
```

All nodes use Command API for routing. The registry and ToolLoader are injected into the graph via closures (same pattern as the Iteration 0 pipeline).

**Components to build:**

1. **`src/orchestrator/gap_detection.py`** (new file)
   - Plain LangGraph node that wraps `registry.detect_gaps(state.required_capabilities)`.
   - Signature: `gap_detection_node(state: TaskState, registry: SkillRegistry) -> Command[Literal["classifier_node"]]`
   - Populates `state.matches` (from `detect_gaps` matches) and `state.gaps` (from `detect_gaps` gaps) using `MatchItem` and `GapItem` schemas.
   - Sets `status = TaskStatus.DETECTING_GAPS`.
   - Routes to `"classifier_node"` regardless of whether gaps exist (classifier handles empty gap list gracefully — already tested).
   - `detect_gaps` return signature: `(list[tuple[CapabilityDescriptor, float, str]], list[tuple[str, str]])` — matches are `(capability, score, query)`, gaps are `(query, reason)`.

2. **`src/orchestrator/loading_node.py`** (new file)
   - Plain LangGraph node that loads and registers all `MCP_TOOL` artifacts produced by the generators.
   - Skills and agents are already written to disk during generation — no loading needed for them.
   - Signature: `loading_node(state: TaskState, loader: ToolLoader, registry: SkillRegistry) -> Command[Literal["__end__"]]`
   - For each artifact in `state.generated_artifacts` where `capability_type == MCP_TOOL`:
     - Call `loader.load_tool(artifact)` — raises on import/syntax failure; catch and record error, continue.
     - Call `loader.execute_tool(loaded, inputs={})` with empty inputs as a smoke-test — if it fails, log a warning but still register (Tier 2 verification in Iteration 3 will do proper testing).
     - Call `loader.register_tool(artifact, registry)` to make it discoverable.
   - Sets `status = TaskStatus.COMPLETED` if no errors, `TaskStatus.FAILED` if all generations failed (i.e., no artifacts at all), otherwise `TaskStatus.COMPLETED` with errors recorded.
   - Routes to `END`.

3. **`src/orchestrator/pipeline.py`** (rewrite)
   - Replace the Iteration 0 pipeline entirely.
   - `create_pipeline(registry: SkillRegistry, loader: ToolLoader) -> StateGraph`
   - Wire all nodes using the topology above.
   - Inject registry and loader via closures for nodes that need them (gap_detection_node, loading_node).
   - Full LangFuse tracing: pass a `CallbackHandler` in the config at invocation time (caller's responsibility, same as demo.py).
   - Keep `StateGraph(TaskState)` as the state type.

4. **Changes to existing nodes** (minimal edits):
   - `src/orchestrator/classifier.py` — change `goto=END` to `goto="generate_mcp_tool"`.
   - `src/orchestrator/generators/mcp_tool.py` — change `goto=END` to `goto="generate_skill"`.
   - `src/orchestrator/generators/skill.py` — change `goto=END` to `goto="generate_agent"`.
   - `src/orchestrator/generators/agent.py` — change `goto=END` to `goto="loading_node"`.
   - Remove the `TODO Phase C` comments from all four files once the changes are made.

**Tests to write:**

- `tests/unit/test_gap_detection.py` — mock `SkillRegistry.detect_gaps()` for: (a) all matches, (b) all gaps, (c) mixed. Verify `state.matches`, `state.gaps`, `status`, and `goto` target.
- `tests/unit/test_loading_node.py` — mock `ToolLoader.load_tool`, `execute_tool`, `register_tool`. Verify: (a) MCP artifacts get loaded and registered, (b) SKILL/AGENT artifacts are skipped, (c) load failure is recorded in errors but pipeline continues.
- `tests/unit/test_pipeline.py` (update existing) — compile the new pipeline with mock registry + loader, invoke with a synthetic `TaskState`, verify the graph reaches END and state fields are populated as expected.
- `tests/integration/test_pipeline_e2e.py` (new) — full end-to-end integration test with a real registry (seeded) and real ToolLoader. Provide a simple task description that maps to a known gap in the seeded registry, verify a `GeneratedArtifact` is produced and registered. Mark with `pytest.mark.integration` and skip without `DEEPSEEK_API_KEY`.

**What not to do in Phase C:**
- Do not add LLM logic to gap_detection_node or loading_node — they are pure Python wrappers.
- Do not change generator signatures to accept `GeneratorInput` — that is future work.
- Do not add verification logic — that is Iteration 3.
- Do not modify `src/schemas/` — all required types already exist.

**After Phase C completes:** Update CLAUDE.md — mark Phase C complete, update "Stable Interfaces" with the new modules, update "What's Built" section. Then run the Iteration 2 critique checkpoint before starting Iteration 3.

### Exit criteria
- DeepAgent decomposition produces structured subtask breakdowns using `write_todos` and `response_format`
- DeepAgent respects `recursion_limit` and completes within timeout
- LangFuse traces show the DeepAgent's planning and tool-calling loop
- Pipeline decomposes tasks, detects gaps, classifies, and generates capabilities
- Generated MCP tools load dynamically and respond to calls
- Generated skills pass `tools/quick_validate.py` validation
- Tool loading handles lifecycle cleanly
- LangFuse traces full pipeline end-to-end
- **Update CLAUDE.md**

### 🔍 CRITIQUE CHECKPOINT after this iteration ✅ PASSED
Assessment: Generated capabilities are structurally valid and executable (344/344 tests pass). MCP tools pass syntax + FastMCP structure validation before reaching the gate. Skills pass `quick_validate.py`. Pipeline always reaches a terminal state. Iteration 3 expectations unchanged.

---

## Iteration 3: Verification Layer (Weeks 5–7) ✅ COMPLETE

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
   - **Operational boundaries:** `recursion_limit=50`, timeout at node level (180s), fresh initialization
   - **Only runs if Tier 2 passes**
   - **Focused on 3 specific attack patterns:** (1) hidden demo/sample mode detection, (2) network-disconnect behavioral change, (3) ground-truth comparison where available
   - Trace with LangFuse

   > ✅ **Decision: `sandbox_execute` uses Docker (Option A, not Option B).** Build `sandbox_execute(code: str) -> dict` in `src/verification/sandbox_tool.py` using `docker run --rm --network none`. Do NOT wrap `ToolLoader.execute_tool` — that runs on the host via `sys.executable`, which is safe for smoke-testing your own generated code but not for adversarial probing. The `adversarial` teammate must use this new tool, not the tool loader. See the `verification-patterns` skill for the implementation pattern.

4. **Verification gate** (`src/verification/gate.py`)
   - Combines all three tiers with tiered cost optimization:
     - Tier 0 (Skills only): `quick_validate.py` structural check (~0 cost)
     - Tier 1: Static scan (~0 cost). Critical → reject immediately
     - Tier 2: Behavioral testing (moderate cost). Only if Tier 1 passes
     - Tier 3: Adversarial probing (high cost). Only if Tier 2 passes
   - Returns pass/fail + detailed report

5. **Pipeline integration** — Verification node between generation and registration. Failed capabilities get feedback and can be regenerated (retry loop, max 3 attempts — "strike system"). If generation fails 3 times, fail open with "Unable to generate" rather than hallucinating success. Pass the `VerificationReport` from the previous failure into the prompt for the next attempt. Use Command API to route between generation → verification → registration with loop-back on failure.

### Pre-work completed before agent team start

- ✅ `.claude/skills/verification-patterns/SKILL.md` — created (AST patterns, behavioral testing, adversarial probing, `sandbox_execute` Docker design, retry loop)
- ✅ `attempt_count: int = 0` added to `TaskState` (`src/schemas/task.py`, ge=0 constraint, 4 new tests)
- ✅ `sandbox_execute` decision recorded above (Option A — Docker, not tool_loader wrapping)
- ✅ `VerificationFinding.tier` constraint corrected to `ge=0` (`src/schemas/verification.py`). Tier 0 structural findings from `quick_validate.py` require tier=0 to be a valid value.
- ✅ `src/orchestrator/verification_node.py` — stub verification node created with correct retry + artifact-replacement logic. `gate_fn=None` uses `_stub_gate` (always passes); pipeline runs end-to-end. In Iteration 3, wire the real gate by passing `gate_fn=gate.verify` via a closure in `pipeline.py`. 19 unit tests.
- ✅ Pipeline updated: `generate_agent` now routes to `"verification_node"` (was `"loading_node"`); `verification_node` wired in `pipeline.py` between generator chain and `loading_node`. Topology is now 9 nodes.
- ✅ Generator replace-by-name: all three generators replace existing artifacts by `capability_name` instead of appending, preventing accumulation across retry cycles.
- **Note for behavioral teammate:** `CapabilityDescriptor.input_schema` is empty by design. Parse parameter schema from `source_code` AST using `ast.parse()`. See `verification-patterns` skill → Tier 2 section.
- **Note for all teammates:** `verification_node` stub is already in place — do **not** create a new one. Implement `src/verification/gate.py` with `verify(artifact, loader, registry) -> VerificationReport`, then update `pipeline.py` to pass `gate_fn=verify` via a closure.

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
  recursion_limit=50, timeout=180s. Focus on 3 attack patterns: hidden demo mode,
  network-disconnect behavior change, ground-truth comparison.
  Trace with LangFuse. See the deep-agents skill for patterns. Write tests.
- "devil" teammate: after the other 3 finish, review all tiers and find ways a
  malicious or lazy code generator could bypass each tier. Pay special attention
  to whether the adversarial DeepAgent's probing can be tricked. Document
  findings in docs/iteration_plan.md or CLAUDE.md.
Have teammates share findings with each other. Require plan approval.
```

### Exit criteria: ✅ All met
- Static scanner catches all 5 contamination categories
- Tier 1 Critical findings block immediately (no Tier 2/3 invocation)
- Skill validation via `quick_validate.py` integrated into the gate
- Behavioral tests distinguish real from mock tools
- Adversarial DeepAgent runs multi-step probing within recursion_limit and timeout
- LangFuse traces show the adversarial agent's full execution (planning, tool calls, file writes)
- The full gate rejects bad capabilities, passes good ones
- Pipeline retries generation on failure (max 3 attempts with feedback)
- Devil's advocate findings documented
- **Update CLAUDE.md**

### Devil's Advocate Findings (Post-Implementation Review)

This section documents ways a malicious or lazy LLM code generator could produce capabilities that pass all verification tiers while still being contaminated or fake. These are honest limitations of the verification approach and should be discussed in the thesis (Chapter 4).

#### 1. Tier 1 Static Scanner Bypasses

**B1.1: Hardcoded data below threshold**
- **Attack:** Return a literal list/dict of 2 items instead of 3. The `_check_hardcoded_return` check uses `len(val.elts) >= 3`.
- **Example:** `return ["Alice", "Bob"]` passes. A tool claiming to "fetch users" that always returns these two names is still fake.
- **Root cause:** Arbitrary threshold at 3 items. Any fixed threshold creates a gap for data sets just below it.
- **Suggested mitigation:** Combine with Tier 2 consistency checks (same inputs should not always produce the same canned output regardless of parameters). Also consider flagging hardcoded returns of any size in functions whose names imply dynamic data retrieval.
- **Risk level:** HIGH for the thesis. LLMs are likely to generate 1-2 item canned responses.

**B1.2: Hardcoded data via indirection**
- **Attack:** Store canned data in a variable instead of returning it directly. `data = ["Alice", "Bob", "Charlie"]; return data` bypasses the check because `ast.Return.value` is `ast.Name`, not `ast.List`.
- **Example:**
  ```python
  async def fetch_users(query: str) -> list:
      results = ["Alice", "Bob", "Charlie"]
      return results
  ```
- **Root cause:** `_check_hardcoded_return` only inspects `ast.Return` value nodes that are literal `ast.List` or `ast.Dict`. Variable assignments that hold literals are invisible.
- **Suggested mitigation:** Track assignments to local variables and flag returns of variables whose assigned value is a large literal. A simple single-pass constant propagation would catch this.
- **Risk level:** HIGH. This is the most natural way an LLM writes canned data.

**B1.3: Mock names in non-obvious forms**
- **Attack:** Use synonyms not in the regex: `stub`, `example_data`, `hardcoded`, `default_response`, `canned`, `synthetic`, `generated_data`, `lorem`. The regex `_MOCK_NAMES` matches `mock|fake|sample|dummy|placeholder|DEMO|TEST_DATA` but misses many common indicators.
- **Root cause:** Finite keyword list. The LLM vocabulary for "not real" is large.
- **Suggested mitigation:** Expand the list (especially `stub`, `example`, `hardcoded`, `default`). Note: `sample` already matches but may false-positive on legitimate uses (e.g., `random.sample`). Consider context-aware matching (only flag when used as variable names, not function calls).
- **Risk level:** MEDIUM. The current list catches the most obvious cases.

**B1.4: The `sample` false positive and the `get_` false negative**
- **Attack (false positive):** Legitimate code using `random.sample()` or `pandas.DataFrame.sample()` gets flagged as CRITICAL, causing valid tools to fail Tier 1.
- **Attack (false negative):** A function named `compute_metrics()` that returns hardcoded data escapes `_EXTERNAL_FN_NAMES` (which only matches `fetch|query|call|get|retrieve|search`). Functions without these prefixes are never checked for missing external calls.
- **Root cause:** The mock regex scans all text including legitimate API calls. The external function name regex is prefix-based and incomplete.
- **Suggested mitigation:** For `_MOCK_NAMES`, exclude matches inside function calls (e.g., `random.sample()`). For missing-external-calls, consider a broader heuristic: any tool that takes entity-like string parameters and returns structured data should have some data source.
- **Risk level:** MEDIUM. False positives reduce trust in the gate; false negatives let fake tools through.

**B1.5: Silent exceptions via specific exception types**
- **Attack:** Use `except ValueError: pass` or `except KeyError: pass` instead of bare `except:` or `except Exception: pass`. The check only flags `is_bare` (type is None) or `is_exception` (type.id == "Exception").
- **Root cause:** The check is scoped to only the broadest exception handlers.
- **Suggested mitigation:** Flag any `except <Type>: pass` (with no re-raise or logging) as at least MEDIUM severity. Catching and silently discarding typed exceptions is almost always a bug in generated code.
- **Risk level:** LOW. Specific exception swallowing is less dangerous than blanket swallowing, but still masks failures.

#### 2. Tier 2 Behavioral Testing Bypasses

**B2.1: Parameterless tools skip all tests**
- **Attack:** Generate a tool with no parameters (or only `**kwargs`) that always returns canned data. `extract_parameter_schema` returns `{}`, so all four test categories are skipped with an INFO finding.
- **Example:**
  ```python
  async def get_system_status() -> dict:
      return {"status": "healthy", "uptime": 99.9}
  ```
- **Root cause:** Behavioral testing is entirely parameter-driven. No-parameter tools have no test surface.
- **Suggested mitigation:** For parameterless tools, run at least a consistency check (call twice, compare outputs) and a timing check. Even without parameter variation, you can detect always-identical outputs.
- **Risk level:** HIGH. Many plausible tool descriptions produce parameterless functions.

**B2.2: Sync functions are invisible**
- **Attack:** Define the tool function as `def fetch_data(query: str):` (sync, not async). `_extract_function_name` and `extract_parameter_schema` only look for `ast.AsyncFunctionDef`. The tool returns MEDIUM "No public async function found" and skips all behavioral tests.
- **Root cause:** Tier 2 only searches for async functions. If the LLM generates a sync function, the entire tier is skipped.
- **Suggested mitigation:** Also search for `ast.FunctionDef`. MCP tools should be async, but a lazy LLM may forget the `async` keyword while producing otherwise functional code.
- **Risk level:** MEDIUM. The MCP tool generator prompt requests async, but LLMs sometimes omit it.

**B2.3: Input-independent tools pass consistency trivially**
- **Attack:** A tool that ignores its parameters and returns the same canned result passes the consistency test (same result for same input), the boundary test (no crashes), and the latency test (fast execution). The nonexistent-entity test only flags `None`/empty returns, not canned data.
- **Example:**
  ```python
  async def analyze_sentiment(text: str) -> dict:
      return {"sentiment": "positive", "confidence": 0.85}
  ```
- **Root cause:** Consistency checks only verify same-input-same-output. They don't verify that different inputs produce different outputs. Boundary tests only check for crashes/timeouts, not for suspiciously identical outputs across different inputs.
- **Suggested mitigation:** Add a "variation" test: call the tool with 3+ meaningfully different inputs and check whether outputs differ. If outputs are identical across all varied inputs, flag as HIGH (tool likely ignores parameters).
- **Risk level:** HIGH. This is the most likely contamination pattern for LLM-generated tools.

**B2.4: Type annotation parsing limitations**
- **Attack:** Use complex type annotations like `Annotated[list[dict[str, Any]], Field(...)]` or `Optional[str]` or union types. The `extract_parameter_schema` handles `Annotated[X, ...]` and simple names, but complex generics may produce unhelpful type strings, leading to the fallback empty-string input for all parameters.
- **Root cause:** The schema extractor does basic `ast.unparse` on annotations. It doesn't recursively interpret generic types to determine the base type (e.g., `Optional[str]` unparsed is `Optional[str]`, not `str`).
- **Suggested mitigation:** Add pattern matching for `Optional[X]`, `list[X]`, `dict[X, Y]` to extract the inner type for input generation.
- **Risk level:** LOW. Most generated tools use simple types.

#### 3. Tier 3 Adversarial Probing Bypasses

**B3.1: Fail-open on timeout/error means unreliable rejection**
- **Attack:** Generate a tool complex enough that the adversarial agent times out (180s) trying to analyze it, or that causes the agent to error. Since `_timeout_report` and `_error_report` both set `overall_passed=True`, the tool passes.
- **Example:** A tool with 200+ lines of plausible-looking but ultimately fake logic could exhaust the agent's step budget.
- **Root cause:** Fail-open design. The adversarial tier is designed as a best-effort improvement, not a hard gate.
- **Suggested mitigation:** Track timeout/error rates via LangFuse. If a specific tool pattern consistently causes timeouts, add it as a Tier 1 heuristic (e.g., flag tools over N lines with no external imports). Consider fail-closed for specific high-risk scenarios.
- **Risk level:** MEDIUM for the thesis. The fail-open design is a deliberate tradeoff documented in the architecture.

**B3.2: The DeepAgent is only as smart as its LLM**
- **Attack:** Craft a tool that subtly returns plausible but wrong results. For example, a unit conversion tool that is off by 5%. The ground-truth comparison attack pattern relies on the DeepAgent writing a reference implementation -- if the agent doesn't think to verify the specific conversion factor, it passes.
- **Root cause:** The adversarial agent uses a general-purpose LLM (`openai:gpt-5-mini`), which may not catch subtle numerical errors in domains it doesn't deeply understand.
- **Suggested mitigation:** For deterministic tools, consider a separate automated ground-truth check (outside the DeepAgent) that compares against known test vectors. The DeepAgent adds value for non-deterministic tools.
- **Risk level:** MEDIUM. The ground-truth attack pattern is the weakest of the three because it relies entirely on the LLM's domain knowledge.

**B3.3: Network-disconnect check is easy to bypass**
- **Attack:** A tool that claims to "fetch" data but wraps the network call in a try/except and returns fallback data on `ConnectionError`. In the sandbox (`--network none`), it silently returns the fallback data. The adversarial agent may see "successful" results and flag it, but only if it reasons about the specific fallback mechanism.
- **Example:**
  ```python
  async def fetch_weather(city: str) -> dict:
      try:
          import httpx
          resp = await httpx.AsyncClient().get(f"https://api.weather.com/{city}")
          return resp.json()
      except Exception:
          return {"city": city, "temp": 22, "condition": "sunny"}
  ```
- **Root cause:** The network-disconnect pattern looks for "successful results despite no network" but a tool with explicit fallback logic produces results that look like real outputs. The tool has a real `httpx` import (passing Tier 1's missing-external-calls check), and the fallback data is parameterized (includes `city`), making it harder to detect as canned.
- **Suggested mitigation:** In Tier 1, flag `except Exception:` blocks in tools that import network libraries as HIGH. In Tier 2, add a test that checks whether the result changes when the tool's network import is present vs. absent.
- **Risk level:** HIGH. This is a realistic pattern for LLM-generated "API wrapper" tools.

**B3.4: Only CRITICAL findings from Tier 3 cause failure**
- **Attack:** The adversarial agent flags suspicious behavior as HIGH or MEDIUM severity. Since `_findings_to_report` converts findings and `gate.verify` only checks for CRITICAL across all findings at the final stage, and Tier 3 findings often come as HIGH (the DeepAgent may hedge), many genuine concerns are logged but don't block registration.
- **Root cause:** Tier 3 was designed to only block on high-confidence "definitely fake" verdicts. The DeepAgent's severity calibration may be conservative.
- **Suggested mitigation:** Consider counting the number of HIGH findings from Tier 3. If 2+ attack patterns flag HIGH, escalate to CRITICAL. Also log and review Tier 3 HIGH findings in the thesis evaluation.
- **Risk level:** MEDIUM. Conservative severity is better than false rejections, but may miss cumulative evidence.

#### 4. Gate Logic Gaps

**B4.1: Skills and agents have weaker Tier 1 coverage**
- **Attack:** Skills only get `scan_text_only()` (regex, no AST) after passing Tier 0. Agents get `scan_text_only()` on JSON config only. Neither applies the hardcoded-data, silent-exception, or missing-external-calls AST checks.
- **Root cause:** Skills are YAML/markdown (not Python) and agents are JSON configs. AST analysis doesn't apply. But this means SKILL.md bodies containing executable instructions or agent system prompts with embedded code samples are only checked for mock names and synthetic comments.
- **Suggested mitigation:** For skills, extract any fenced code blocks from SKILL.md and run AST checks on them. For agents with `skill_content` (DeepAgent-type), parse the skill content separately.
- **Risk level:** MEDIUM. Skills and agents are less code-centric, so contamination manifests differently. But a skill whose instructions say "return hardcoded data" passes all checks.

**B4.2: Tier 2 runs on all artifact types but only works for MCP tools**
- **Attack:** `behavioral.run(artifact)` looks for `artifact.source_code` and an async function. For SKILL and AGENT artifacts, `source_code` is typically empty (they use `skill_content` or `agent_config`). So Tier 2 returns an INFO "No source code to test" for non-MCP-tool artifacts.
- **Root cause:** Tier 2 was designed for executable Python tools. Skills and agent configs are not directly executable.
- **Suggested mitigation:** Document this as a known limitation. Consider generating a small test harness for skills (execute the skill's instructions against a test scenario) as future work.
- **Risk level:** LOW for the thesis. The thesis focuses on MCP tool generation as the primary artifact type.

**B4.3: The 3-strike retry loop re-runs generators from scratch** ✅ PARTIALLY FIXED
- **Attack:** A generator that produces fake tools deterministically (same classified gap always produces the same fake output) will fail Tier 1 three times and then fail-open via the verification node (line 114-117: `all_passed or new_attempt_count >= MAX_ATTEMPTS` routes to `loading_node` with all artifacts, including failed ones).
- **Root cause (original):** The retry loop did not pass previous `VerificationReport` findings back into the generator prompt.
- **Fix applied (Iteration 3 post-implementation):** `src/orchestrator/generators/utils.py` introduces `build_task_context(state)` which, on retry (`attempt_count > 0`), appends CRITICAL and HIGH findings from failed `VerificationReport`s into the generator prompt with a "PREVIOUS ATTEMPT FAILED VERIFICATION" header. All three generator nodes (`generate_mcp_tool`, `generate_skill`, `generate_agent`) now use this helper.
- **Remaining risk:** Fail-open after 3 strikes is still in effect — a deterministically-bad generator that ignores feedback will still eventually load its contaminated artifact. Consider fail-closed as future work.
- **Risk level:** MEDIUM (downgraded from HIGH). Feedback injection makes it harder for deterministic bad generators to succeed; the remaining risk is a degenerate LLM that ignores its own failure reports.

#### 5. Cross-Tier Gaps

**B5.1: The "clean enough" tool pattern**
- **Attack:** Generate a tool that (a) uses no mock/demo names, (b) has no hardcoded returns over 2 items, (c) imports `httpx` (passing missing-external-calls), (d) has an async function with parameters, (e) catches `ConnectionError` specifically (not bare `except:`), (f) returns parameterized fallback data. This tool passes Tier 1 completely clean, passes Tier 2 (consistent results, handles boundaries, fast), and only Tier 3 might catch it -- if the DeepAgent happens to test the network-disconnect pattern thoroughly.
- **Root cause:** Each tier's checks are independently bypassable. There is no cross-tier correlation (e.g., "Tier 1 found no issues AND Tier 2 found no issues for a tool that claims to fetch external data -- that itself is suspicious").
- **Suggested mitigation:** Add a meta-check: if a tool's name/description implies external data access but all tiers find zero issues, flag it for manual review. Real network-dependent tools should fail or degrade in the sandbox.
- **Risk level:** HIGH. This is the most complete bypass pattern. A moderately sophisticated LLM prompt could produce tools matching this template.

**B5.2: Agent artifacts with embedded SKILL.md bypass all code checks**
- **Attack:** The agent generator (for DeepAgent-type artifacts) produces `skill_content` (SKILL.md format) and writes it to disk. The gate runs `scan_text_only` on the JSON config but never scans the `skill_content` through any AST-based check. The skill content may contain instructions like "always return these example values" that are undetectable by regex.
- **Root cause:** Agent artifacts are checked as JSON configs. The embedded skill content (which contains the actual behavioral instructions) is not independently verified through the skill verification path.
- **Suggested mitigation:** When an agent artifact has `skill_content`, run it through the SKILL verification path (Tier 0 + `scan_text_only`) as well.
- **Risk level:** MEDIUM. Agent generation is the least common artifact type in the thesis scope.

#### Summary Table

| ID | Bypass | Tier(s) Affected | Risk | Exploitable by Lazy LLM? |
|----|--------|-------------------|------|--------------------------|
| B1.1 | Hardcoded data below threshold (< 3 items) | Tier 1 | HIGH | Very likely |
| B1.2 | Hardcoded data via variable indirection | Tier 1 | HIGH | Very likely |
| B1.3 | Mock names using synonyms | Tier 1 | MEDIUM | Likely |
| B1.4 | `sample` false positive / `get_` false negative | Tier 1 | MEDIUM | Moderate |
| B1.5 | Silent exceptions with specific types | Tier 1 | LOW | Unlikely |
| B2.1 | Parameterless tools skip all tests | Tier 2 | HIGH | Likely |
| B2.2 | Sync functions invisible to Tier 2 | Tier 2 | MEDIUM | Moderate |
| B2.3 | Input-independent tools pass consistency | Tier 2 | HIGH | Very likely |
| B2.4 | Complex type annotation parsing | Tier 2 | LOW | Unlikely |
| B3.1 | Fail-open on timeout/error | Tier 3 | MEDIUM | Moderate |
| B3.2 | DeepAgent limited by LLM domain knowledge | Tier 3 | MEDIUM | Moderate |
| B3.3 | Network fallback with parameterized data | Tier 3 | HIGH | Likely |
| B3.4 | Only CRITICAL findings from Tier 3 block | Tier 3 | MEDIUM | N/A |
| B4.1 | Skills/agents have weaker Tier 1 coverage | Gate | MEDIUM | Likely |
| B4.2 | Tier 2 only works for MCP tools | Gate | LOW | N/A |
| B4.3 | 3-strike fail-open with no feedback to generators | Gate + Retry | MEDIUM | Likely (✅ feedback injection fixed) |
| B5.1 | "Clean enough" tool passes all tiers | Cross-tier | HIGH | Likely |
| B5.2 | Agent embedded SKILL.md not independently verified | Cross-tier | MEDIUM | Moderate |

#### Thesis Implications

The findings above do not invalidate the verification approach -- they define its boundaries. For the thesis:

1. **Honest limitations section (Chapter 4):** The 5 remaining HIGH-risk bypasses (B1.1, B1.2, B2.1, B2.3, B5.1) should be explicitly discussed (B4.3 downgraded to MEDIUM after feedback injection fix). They demonstrate that static + behavioral + adversarial probing forms a defense-in-depth strategy with known gaps, not an impenetrable gate.

2. **Manual audit alignment (Iteration 6):** The 20-30 capability manual audit should specifically look for instances of these bypass patterns in generated tools. This provides empirical data on how often LLMs naturally produce code that falls into these gaps.

3. **Future work:** Variable-indirection tracking (B1.2), input-variation testing (B2.3), and cross-tier meta-checks (B5.1) are concrete, implementable improvements that can be discussed as future work.

4. **The fail-open design (B3.1, B4.3) is a thesis design choice, not a bug.** The pipeline must terminate. However, the thesis should clearly document what "fail-open" means: unverified capabilities enter the registry, relying on the granularity optimizer (Pillar 3, prune operation) to eventually remove underperforming ones. B4.3's risk has been reduced by the generator feedback injection (`build_task_context`) but the fail-open exit remains.

#### Remediation Plan (Three-Bucket Strategy)

Findings are triaged into three buckets. This decision is final — do not re-litigate per session.

**Bucket 1 — Fix before Iteration 6 (before evaluation runs):**
These are HIGH risk, simple enough to fix, and would contaminate evaluation data if left unfixed.

| ID | Fix |
|----|-----|
| B2.1 | Add a smoke test for parameterless tools: if `schema == {}`, run a single no-arg execution and flag if it returns non-empty canned-looking output |
| B2.3 | Add output-variation check: call the tool twice with *different* boundary inputs and flag if outputs are identical despite different inputs |
| B1.2 | Extend `_check_hardcoded_return` to follow one level of variable indirection: if `ast.Return.value` is `ast.Name`, resolve it to the assignment in the same function scope |

Implement these as part of Iteration 5 pre-work (before wiring evaluation harness), not a separate iteration.

**Bucket 2 — Document as future work (Chapter 7):**
HIGH/MEDIUM risk but architecturally constrained or requiring significant new machinery.

- ~~**Dynamic sandbox dependencies**~~ — **IMPLEMENTED (Iteration 5).** LLM-based import→PyPI resolution via `resolve_extra_packages()`. Two-phase Docker execution (pip install with network → run without network). Dockerfile stripped to minimal base (`mcp` + `pydantic` only).
- B1.1 (hardcoded data below 3-item threshold) — threshold tuning is a research problem
- B1.3 (unrecognized mock synonyms) — finite keyword list, LLM vocabulary is unbounded
- B3.3 (network fallback bypasses network-disconnect check) — requires semantic analysis of error handling
- B5.1 ("clean enough" cross-tier pattern) — requires cross-tier correlation meta-check
- B5.2 (agent embedded SKILL.md not independently verified) — requires routing agent artifacts through skill verification path

**Bucket 3 — Accept as honest limitations (Chapter 4):**
Inherent to the design; fixing would require architectural changes incompatible with the thesis scope.

- B3.1 (fail-open on timeout/error) — deliberate tradeoff: pipeline must terminate
- B3.2 (DeepAgent limited by LLM domain knowledge) — fundamental LLM limitation
- B4.1 (skills/agents get weaker Tier 1 coverage) — YAML/JSON are not Python; AST analysis does not apply
- B3.4 (only CRITICAL from Tier 3 blocks) — deliberate calibration: Tier 3 should only hard-reject high-confidence findings

---

## Iteration 4: Granularity Optimizer (Weeks 7–9) ✅ COMPLETE

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

### Exit criteria: ✅ All met
- Split, merge, prune all work correctly with type constraints enforced
- Attempting split/merge on MCP tools is gracefully rejected (not silently skipped)
- Simulation shows convergence over 50+ cycles (verified via simulation harness with KL divergence)
- Utility scores improve vs. no-optimization baseline (verified via `compare_baselines()`)
- DeepAgent failure analysis not used — plain LLM-based split/merge sufficient for thesis scope
- **Update CLAUDE.md**

---

## Iteration 5: Integration & End-to-End Testing (Weeks 9–11) ✅ COMPLETE

**Goal:** Wire all three pillars together. Build evaluation infrastructure and baselines.

### Infrastructure (792 unit tests pass)

| Component | Status | Notes |
|---|---|---|
| `src/schemas/evaluation.py` | ✅ | `EvaluationRecord` (18 fields), separate from `InvocationRecord` |
| `src/registry/evaluation_store.py` | ✅ | DuckDB `EvaluationStore`, `evaluation_runs` table |
| `tests/evaluation/fixtures/` | ✅ | 32 fixture files for 18 tasks, programmatic generation (seed=42), `expected_values.json` |
| `tests/evaluation/tasks.py` | ✅ | T01–T18 `TaskDefinition` with validators. Distribution: 8 MCP (3 retrieval + 5 synthesis), 5 SKILL (2 retrieval + 3 synthesis), 5 AGENT |
| `tests/evaluation/execution.py` | ✅ | SKILL/AGENT execution via DeepAgent; T04/T08/T10-T17 response models |
| `tests/evaluation/snapshots.py` | ✅ | `take_clean_snapshot` / `restore_clean_snapshot` (ChromaDB + mcp_tools + skills) |
| `tests/evaluation/harness.py` | ✅ | Full run loop; `MetricsStore` per-run; `prepare()` auto-cleans before snapshot; per-task LangFuse tagging; static implementations for T01/T02/T03/T05/T09 |
| `src/orchestrator/pipeline.py` | ✅ | Added `verification_enabled` for no_verification baseline |
| `src/orchestrator/loading_node.py` | ✅ | Registers tools even when host import fails (source_code in metadata) |
| `scripts/run_task.py` | ✅ | User-facing CLI; auto-optimizer after 20+ invocations; verification findings in output |
| `scripts/run_pilot.py` | ✅ | Evaluation runner; all conditions + task selection |
| Bucket 1 bypass fixes (B1.2, B2.1, B2.3) | ✅ | Committed pre-Iteration-5 |
| Integration tests | ✅ | `test_pipeline.py` (Pillar 1, 3 types), `test_verification.py` (Pillar 2, 5 scenarios), `test_split_merge.py` (Pillar 3, full loop), `test_auto_optimizer.py` (all 3 operations via pipeline) |
| Logging improvements | ✅ | Gate fail-open message, package resolution context, generator summaries, ChromaDB/LangFuse debug logs |
| deepagents upgrade | ✅ | 0.4.1 → 0.4.12; timeouts: adversarial 300s, SKILL 240s, AGENT 300s |
| Testbed rebalancing | ✅ | T04→SKILL (inventory reorder), T08→AGENT (data quality), T10→AGENT (trend forecasting), T18 added (hash dedup MCP). Synthesis split: 5/5/5 |

### Pilot run findings (2026-03-11)

First pilot run completed (T01, T07, T10 under `full` condition). **3/3 tasks succeeded** but total runtime was **2+ hours** — unacceptable for evaluation (target: ~15-25 min).

**Model change:** Switched DeepAgent components (task decomposition + adversarial probing) from `deepseek:deepseek-chat` to `openai:gpt-5-mini`. Decomposition improved from ~120s to ~30s, but model speed was a red herring — architectural issues dominated runtime. Only `OPENAI_API_KEY` is required (DeepSeek key no longer needed after full unification).

**Root cause analysis:** Six compounding architectural bottlenecks identified. Full investigation in `docs/archive/investigation_pipeline_bottlenecks.md`:

| Issue | Impact | Root cause |
|---|---|---|
| Over-decomposition | +60-70 min/task | Prompt says "smallest meaningful subtasks"; T07 → 14 caps instead of 1-2 |
| Full regeneration on retry | +30-40 min/task | Generators iterate all `classified_gaps`, not just failed ones |
| Sequential verification | +40-50 min/task | `for artifact in artifacts: gate_fn(artifact)` — no parallelism |
| Optimizer prunes seeded caps | +20-30 min (T01, T10) | `prune()` kills all zero-invocation caps; registry matches not tracked |
| Sequential generation | +10-15 min/task | Sequential loop over gaps, ~25s each |
| Artifact accumulation | +5-10 min/task | LLM name non-determinism → duplicates across retries |
| Sandbox missing pandas | Hard fail | Generated tools importing pandas fail Tier 2 unconditionally |

**Fix plan:** 8 fixes documented in `docs/archive/plan.md` (Fix 0–7). Implementation order accounts for dependencies (e.g., Fix 5 deterministic naming must precede Fix 4 skip-on-retry). Expected outcome: pilot runtime → ~15-25 min, zero test breakage, ~27 new tests.

### Pilot run findings (2026-03-12) — post-fix re-run

After implementing Fix 0–7 and the classifier prompt rewrite (Single-Capability Rule), the pilot was re-run (T01, T07, T10 under `full` condition):

| Task | Result | gate_passed | output_in_range | Notes |
|---|---|---|---|---|
| T01 | ✅ success | n/a (registry match) | True | Seeded `csv-parser` matched directly |
| T07 | ❌ output_rejected | True | False | Generated MCP_TOOL fuzzy-join; `row_count=12` correct, `null_count_on_key=3` wrong (expected 0) |
| T10 | ✅ success | True | True | Generated MCP_TOOL log parser; passed all tiers on attempt 1 |

**Success rate: 66.7% (2/3). Gate pass rate: 100% (3/3).**

**Classifier fix worked:** T07 and T10 now correctly classified as MCP_TOOL (previously misclassified as SKILL). The Single-Capability Rule in the classifier prompt biases toward MCP_TOOL when decomposition produces a single gap. This is legitimate per MCP guidance ("consolidate tools"), Voyager's all-executable skill library, and SkillsBench's finding that self-generated skills provide no benefit over direct tool use.

**Key finding — semantic correctness gap:** The 100% gate pass rate vs 66.7% success rate exposes a fundamental limitation: the three-tier verification gate catches structural issues (Tier 1 AST/regex), behavioral edge cases (Tier 2 sandbox testing), and adversarial deception (Tier 3 red-team probing), but **does not verify business logic correctness**. T07's fuzzy-join tool produces the correct number of rows (12) but reports 3 null keys instead of 0 — likely implementing a left outer join instead of inner join, or miscounting nulls after the join. The verification gate has no way to detect this because it lacks task-specific acceptance criteria.

**Approaches for future work (not Iteration 5):**

1. **Task-level acceptance tests** — Attach expected input/output pairs to each task definition. Run the generated tool against these after Tier 3 passes. Highest impact but requires ground-truth data for every task.
2. **Retry with output feedback** — After execution, compare output against the validator. If rejected, feed the actual vs expected output back to the generator as context for a retry. Leverages the existing retry loop (`attempt_count`).
3. **Human-in-the-loop gate** — Use LangGraph's `interrupt()` to pause after execution and present results to a human for approval. Appropriate for high-stakes tasks where automated validation is insufficient.

**Decision:** Defer semantic correctness verification past Iteration 5 evaluation runs. The gap between gate_pass_rate and success_rate is itself valuable thesis data — it quantifies what structural/behavioral/adversarial verification can and cannot catch. Document as an honest limitation (Chapter 4) and propose approaches 1–2 as future work (Chapter 7).

### Still needed

- [x] Implement Fix 0–7 per `docs/archive/plan.md`
- [x] Rebuild sandbox Docker image after Fix 0
- [x] Re-run pilot after fixes (classifier fix + Single-Capability Rule)
- [x] Run 4 baseline conditions across T01-T10 (n_runs=5) — results stale, need re-run post-fixes
- [x] SKILL/AGENT execution in harness + T11-T13 tasks
- [x] Harness execution fixes: dynamic deps, extra_inputs, error logging, registry match (2026-03-15)
- [x] Re-run full evaluation with 13 tasks × 4 conditions × 5 runs (2026-03-16/17)
- [ ] Critique checkpoint (see exit criteria below)

### Full evaluation results (2026-03-16/17) — post-fix, 13 tasks

260 records total (13 tasks × 4 conditions × 5 runs).

| Condition | Success | Gate Pass | Pre-fix (10 tasks) |
|---|---|---|---|
| **no_optimization** | **52/65 (80.0%)** | 64/65 (98.5%) | 52.0% |
| **no_verification** | 50/65 (76.9%) | 65/65 (100%) | 62.0% |
| **full** | 47/65 (72.3%) | 62/65 (95.4%) | 58.0% |
| **static** | 25/65 (38.5%) | 25/65 (38.5%) | 50.0% |

Per-task breakdown across JIT conditions:

| Task | full | no_verif | no_opt | static | Type | Notes |
|---|---|---|---|---|---|---|
| T01 | 5/5 | 5/5 | 5/5 | 5/5 | mcp_tool | Registry match |
| T02 | 5/5 | 5/5 | 5/5 | 5/5 | mcp_tool | Registry match |
| T03 | 5/5 | 5/5 | 5/5 | 5/5 | mcp_tool | Registry match |
| T04 | 3/5 | 5/5 | 4/5 | 0/5 | mcp_tool | Tier1 rejections in full |
| T05 | 3/5 | 3/5 | 3/5 | 5/5 | skill/mcp | Classifier oscillation |
| T06 | 0/5 | 0/5 | 0/5 | 0/5 | mcp_tool | Semantic gap (see below) |
| T07 | 4/5 | 3/5 | 4/5 | 0/5 | mcp_tool | Stochastic |
| T08 | 5/5 | 5/5 | 5/5 | 0/5 | mcp_tool | extra_inputs fix worked |
| T09 | 1/5 | 0/5 | 1/5 | 5/5 | mcp_tool | JSON mismatch (see below) |
| T10 | 5/5 | 5/5 | 5/5 | 0/5 | mcp_tool | Consistent |
| T11 | 4/5 | 5/5 | 5/5 | 0/5 | skill | Multi-source integration |
| T12 | 2/5 | 4/5 | 5/5 | 0/5 | skill | Verification variance |
| T13 | 5/5 | 5/5 | 5/5 | 0/5 | agent | Anomaly investigation |

**Static dropped from 50% to 38.5%** — expected arithmetic: same 5 covered tasks but denominator grew from 10 to 13 (5/13 = 38.5%). All 3 JIT conditions improved significantly despite harder task mix (+3 SKILL/AGENT tasks).

**All three capability types confirmed working end-to-end:** MCP_TOOL (T01–T10), SKILL (T05, T11, T12), AGENT (T13). This validates the thesis's three-pillar architecture claim.

#### Finding 1: Optimizer hurts performance (80% no_opt vs 72.3% full)

`no_optimization` is the best JIT condition — the optimizer's pruning removes freshly-generated capabilities (zero invocations in 30 days) within each pipeline run. Pinned seeded capabilities are correctly protected (Fix 2 working), but pruning generated artifacts changes the registry state mid-run, affecting gap detection and classification for subsequent tasks. With `no_optimization`, the registry stays stable and generation is more consistent.

**Thesis implication:** The optimizer needs invocation history to add value. In a single-run evaluation with no repeat queries, the optimizer lacks the data to make good decisions. This is a valid finding about optimizer maturity — it would likely improve with longitudinal deployment where capabilities accumulate invocation data over time. Document as honest limitation (Chapter 4) and note that evaluation conditions (fresh snapshot per run) don't give the optimizer its intended operating environment.

#### Finding 2: T12 degradation under full condition (2/5 vs 5/5 no_opt)

T12 failures in `full` condition:
- 1× tier1 static scanner rejection (CRITICAL finding in generated skill)
- 1× output validation failure (semantic bug in generated skill)
- 1× skill execution timeout (120s limit)

In `no_optimization`, T12 was 5/5. The optimizer modifying registry state within the run introduces variance in classification and generation quality. Combined with the stochastic nature of skill generation at n=5, this produces the observed degradation. Not a bug — expected variance amplified by optimizer side-effects.

#### Finding 3: T06 = 0% everywhere — semantic correctness gap

T06 (KNN imputation) has `has_static_coverage=False`, so static = 0% (generation_failed by design — no hand-coded implementation).

All JIT conditions: gate passes 100% but output fails 100%. Generated tools produce variance values ~10-18% off from expected (e.g., `feature_c` expected 111.33, got 91.77 = -17.6%, exceeding ±10% tolerance). The LLM generates syntactically correct KNN code that successfully imputes nulls (`null_count=0` ✓) but with subtle math errors — likely sample vs population variance formula, improper neighbor weighting, or edge-case handling.

**Thesis implication:** This is the strongest evidence for the semantic correctness gap. Gate_pass=100%, success=0% — a 100 percentage-point gap. The three-tier verification gate catches structural issues, behavioral edge cases, and adversarial deception, but has no mechanism to verify mathematical correctness of the output. Documenting this as the core limitation (Chapter 4) is more valuable than fixing it with prompt engineering.

#### Finding 4: T09 = 5/5 static but ~0% JIT — prompt specificity tradeoff

The T09 fixture (`t09_report_data.json`) is a nested dict: `{"title": "...", "period": {...}, "records": [{...}, ...]}`. The static implementation correctly accesses `data.get("records", [])`. But LLM-generated tools consistently guess wrong — they look for `data["revenues"]`, `data["data"]`, or expect a flat list. Log errors: `"Error: JSON data must be a list of records"`, `"Error: No valid revenue data found"`.

The task prompt says "generate an analytics report from the data at [path]" but doesn't describe the JSON structure. The LLM has no way to know `records` is the key containing the data. One run out of ~15 happened to guess correctly.

**Thesis implication:** This demonstrates the **prompt specificity vs generality tradeoff**. Adding fixture schema hints to the prompt would fix T09 but would make the evaluation less realistic — real-world tasks often arrive with incomplete specifications. The JIT system's value depends on how well it handles ambiguity. T09 shows that for complex nested data structures, the LLM needs explicit schema information. Document as a finding about the boundary conditions of JIT generation (Chapter 4) and propose schema inference as future work (Chapter 7).

### Pilot details

**Runner:** `uv run scripts/run_pilot.py`

| Task | Tier | What it tests | Expected outcome |
|---|---|---|---|
| T01 | Trivial | Seeded retrieval (csv-parser is in registry — no JIT needed) | Succeeds in all 4 conditions; validates happy path and static baseline |
| T07 | Moderate | Fuzzy join across two CSVs (no seeded capability — JIT required) | Succeeds in full/no_verify/no_optimize; fails in static (no static coverage) |
| T10 | Complex | Server log analysis, 200-line log (complex JIT generation) | May fail even in full — failure modes are thesis data |

**What to check after the pilot:**
- T01 succeeds in `full` condition (gate_passed=True, output_in_range=True)
- T07 generates an MCP_TOOL or SKILL artifact that passes verification
- LangFuse shows complete lifecycle traces for T07/T10 (decomposition DeepAgent + generation + gate + adversarial DeepAgent)
- No pipeline hangs (DeepAgent timeout fallbacks working)

**Full evaluation commands:**
```bash
# Pilot (3 tasks, full condition, 1 run)
uv run scripts/run_pilot.py

# Static baseline only (fast, no LLM, ~2 seconds)
caffeinate -dims uv run scripts/run_pilot.py --condition static --all-tasks --n-runs 5 2>&1 | tee evaluation_run_static.log

# Single condition (e.g., no_optimization)
caffeinate -dims uv run scripts/run_pilot.py --condition no_optimization --all-tasks --n-runs 5 2>&1 | tee evaluation_run_no_opt.log

# Full evaluation (all 4 conditions, 13 tasks, 5 runs each — ~9-10h)
caffeinate -dims uv run scripts/run_pilot.py --all-conditions --all-tasks --n-runs 5 2>&1 | tee evaluation_run.log
```

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
   - **Longitudinal** — same as full, but 40 sequential tasks without snapshot reset (tests optimizer convergence over time)

5. **Integration tests** — Full lifecycle, capability reuse, evolutionary operations

6. **Error handling** — Generation failure, sandbox timeout, server crash, corrupted registry. Use Command API loop-back for recoverable errors, RetryPolicy for transient failures (see `langgraph-architect` skill error handling section).

### ✅ Decision: No agent team — baselines are condition flags, not separate codebases

The original plan called for a 3-teammate agent team to build three separate baseline implementations. During harness design, it became clear that all five conditions share the same run loop (restore snapshot → seed registry → invoke pipeline → validate output → record to DB). The only differences are two boolean flags:

- `verification_enabled=False` for the no-verification condition
- `run_optimizer=False` for the no-optimization condition
- `_run_static()` instead of `_run_jit()` for the static condition

Building separate codebases would have duplicated the entire run loop and created three divergent harnesses that could drift apart. A single `EvaluationHarness` with a `condition` parameter is simpler, less error-prone, and ensures all conditions produce records in exactly the same schema.

**Why the original plan assumed separate codebases:** The plan was written before the harness was designed. At planning time, "baselines" implied different pipelines. Once the harness abstracted the run loop, the baselines reduced to parameter choices within it.

| Planned teammate | What it would have built | What we have instead |
|---|---|---|
| `static-baseline` | Hand-curated skill library | `_run_static` + 5 implementations in `harness.py` |
| `no-verify-baseline` | Pipeline with gate bypassed | `verification_enabled=False` in `create_pipeline` |
| `no-optimize-baseline` | Pipeline without optimizer | `run_optimizer=False` (already the default) |

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

## Iteration 6: Evaluation & Analysis (Weeks 11–13) 🔄 IN PROGRESS

**Goal:** Run formal evaluation, collect data, produce thesis figures.

### Completed

| Task | Status | Notes |
|---|---|---|
| Run all baselines (pre-rebalance) | ✅ | 340 records (T01-T17, March 18+20). Stale — replaced by April re-run. |
| Statistical analysis script | ✅ | `scripts/analyze_results.py` — Clopper-Pearson CIs, Fisher's exact per task, gate vs output separation |
| Token cost backfill | ✅ | `scripts/backfill_token_costs.py` — LangFuse REST API, per-task attribution (tagged traces) |
| Context window analysis | ✅ | `scripts/analyze_context_utilization.py` — 1,255 traces, max 28.8% utilization. System is cost-bound. |
| Longitudinal optimizer test | ✅ | `scripts/run_longitudinal.py` — 40 tasks, 69.2%→100%, prune only. `data/longitudinal.duckdb` |
| Model unification | ✅ | All LLM calls unified to `openai:gpt-5-mini` (was hybrid DeepSeek/GPT-5-mini) |
| MINT CLI | ✅ | `uv run mint` — Typer + Rich. 11 subcommands. See `src/cli/` and `docs/plan_cli_upgrade.md`. |
| Re-run batch 1 | ✅ | 180 records: no_verification (76.7% success) + static (27.8%). 2026-04-05, ~2h. |

### In progress

- **Re-run batch 2** — no_optimization + full conditions (18 tasks × 5 runs each = 180 records). Running with verification enabled. Expected ~8-12h.

### Remaining

1. **Collect metrics** — success rate, token cost, time-to-first-correct, convergence (after re-run completes)
2. **Manual audit** — 20–30 generated capabilities, classify: correct / contaminated / partial
3. **DeepAgent analysis** — tool call counts, planning steps, token cost per agent type (data exists in LangFuse traces)
4. **Verification cost analysis** — Tier 1 rejection rate, token distribution across tiers (data exists in evaluation logs)
5. **Figures** — success comparison, registry evolution, convergence curve, token breakdown, context utilization
6. **Statistical analysis** — significance tests for key comparisons (script exists, re-run after new data)

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

### Future work topics for Chapter 7

**Pydantic Monty for two-tier execution (sandboxed orchestration):**
The current architecture uses Docker for all sandboxed execution (`sandbox_execute`) and DeepAgent for SKILL/AGENT orchestration. Pydantic Monty (`pydantic-monty`) — a Rust-based Python interpreter with microsecond startup — could replace the orchestration tier. Two-tier model:
- **Monty tier**: LLM-generated coordination code (fan-out, conditionals, glue logic between pipeline stages). No Docker needed, ~4μs startup. External functions bridge to the Docker tier.
- **Docker tier**: Generated MCP tools that need third-party libraries (pandas, numpy, sklearn). Full CPython environment, ~195ms startup.

Benefits: eliminates DeepAgent overhead for SKILL execution (currently ~20-55s per invocation), enables `asyncio.gather` for parallel tool calls in one LLM round-trip (vs sequential tool-call loop), and type-checks LLM-generated orchestration code before execution. Limitation: Monty cannot run third-party libraries, so generated MCP tools still require Docker.

Skill documentation available at `.claude/skills/monty/SKILL.md`.

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
| `recursion_limit` (decomposition) | 50 | ~25 logical steps; each tool call = 2 graph steps |
| `recursion_limit` (adversarial) | 50 | Adversarial probing needs more exploration room |
| `recursion_limit` (failure analysis) | 25 | Failure analysis is focused investigation |
| Node-level timeout (decomposition) | 120s | Prevents pipeline hang if agent loops |
| Node-level timeout (adversarial) | 180s | Longer for sandbox execution |
| Initialization | Fresh every time | No state carried between invocations |
| Output contract | `response_format=PydanticModel` | Structured output, not free-text parsing |
| Fallback | If timeout/recursion_limit hit, return partial results with error flag | Pipeline continues with degraded data rather than hanging |

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
| DeepAgent hangs/loops | recursion_limit, node-level timeout, fallback to partial results |
| DeepAgent costs too high | Track via LangFuse; fall back to plain nodes if needed |
| Optimizer tries to merge MCP tool code | Type constraint check prevents this; graceful rejection |
| Optimizer doesn't converge | Debug with simulation harness; simplify distribution |
| Dynamic MCP tool loading flaky | Start with importlib (Option B); upgrade to subprocess if needed |
| Agent Teams overhead too high | Start with 2 teammates; scale up only when justified |
| Generated skills don't conform to format | Validate with `tools/quick_validate.py` in gate |
| LLM output malformed | Few-shot examples, repair pass, LangFuse logging |
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
