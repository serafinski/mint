# JIT Capability Synthesis for Self-Developing Multi-Agent Systems

**Master's Thesis Project - February 2026**

## Overview

This project explores just-in-time capability synthesis in multi-agent systems. The goal is to build a system that can generate, verify, and optimize its own capabilities on-demand.

**Research question:** How can we balance autonomy vs correctness, specificity vs generality, and context economy in self-developing multi-agent systems?

## Proposed Architecture (Three Pillars)

1. **JIT Capability Pipeline** - Generate capabilities on-demand (MCP tools, agent skills, or new agents)
2. **Verification Layer** - Three-tier verification to catch LLM generation failures
3. **Granularity Optimizer** - Evolutionary feedback loop to optimize skill granularity

See `docs/thesis_proposal.md` for full details.

## Tech Stack

- **LangGraph** - Multi-agent orchestration and pipeline graphs
- **DeepAgents** (`deepagents` package) - Agent harness with built-in planning (`write_todos`), file system context management, subagent spawning, and conversation summarization. Used for task decomposition (Pillar 1) and adversarial probing (Pillar 2).
- **LLM Provider** - DeepSeek via `langchain-deepseek` for cost efficiency. Provider-agnostic via `init_chat_model("provider:model")`.
  - `deepseek-chat` (DeepSeek-V3.2) — for all LLM tasks including DeepAgents and plain LangGraph nodes
  - `deepseek-reasoner` (thinking mode) — **not usable with DeepAgents** due to missing `tool_choice` support; reserved for direct LLM calls without tool use
- **Claude Code** - Code generation (development tool only, not part of the runtime system)
- **MCP SDK** - Tool serving (Python, FastMCP pattern)
- **ChromaDB** - Skill registry (semantic search with hierarchical embeddings)
- **DuckDB** - Metrics storage (invocations, outcomes, token costs)
- **LangFuse** - Observability and tracing (local or cloud)
- **Docker** - Sandboxed execution for generated capabilities
- **uv** - Python package management

### Where DeepAgents vs. Plain LangGraph Nodes

DeepAgents (`create_deep_agent()`) return a `CompiledStateGraph` — a complete agent with built-in planning, file system tools, subagent delegation, and context management. They are NOT LangGraph nodes. To use them in the pipeline, invoke them from within a node function.

| Component | Implementation | Model | Why |
|---|---|---|---|
| Task decomposition | **DeepAgent** (invoked from node) | `deepseek-chat` | Multi-step planning with `write_todos`, file-based context for large task descriptions |
| Capability classifier | Plain LangGraph node | `deepseek-chat` | LLM classification with structured output (`temperature=0`) — single-step |
| MCP tool generator | Plain LangGraph node | `deepseek-chat` | Single-pass code generation with retry on failure |
| Skill generator | Plain LangGraph node | `deepseek-chat` | Structured text generation targeting DeepAgents skill format |
| Agent generator | Plain LangGraph node | `deepseek-chat` | System prompt + config generation |
| Tier 1 static scanner | Plain Python (no LLM) | — | AST + regex, deterministic |
| Tier 2 behavioral testing | Plain LangGraph node | `deepseek-chat` | Test input generation + execution |
| Tier 3 adversarial probing | **DeepAgent** (invoked from node) | `deepseek-chat` | Multi-step reasoning: plan attacks via `write_todos`, execute in sandbox via `execute` tool, write findings to files, iterate |
| Failure analysis (optimizer) | **DeepAgent** (optional, invoked from node) | `deepseek-chat` | Multi-step investigation of why capabilities fail |
| Granularity operations | Plain LangGraph node | `deepseek-chat` | Metrics-driven decisions, mostly deterministic |

### DeepAgents Skill Ecosystem

Generated skills (Pillar 1) target the DeepAgents runtime skill format. Key details:

- **Skill structure**: `SKILL.md` (required) + optional `scripts/`, `references/`, `assets/` directories
- **Skill locations**: DeepAgents loads skills from `~/.deepagents/<agent>/skills/` (user) and `.deepagents/skills/` (project), with project skills taking precedence
- **Frontmatter**: YAML with `name` and `description` (required). Allowed keys: `name`, `description`, `license`, `compatibility`, `allowed-tools`, `metadata`
- **Validation utilities**: `tools/quick_validate.py` validates skill structure; `tools/init_skill.py` scaffolds new skills. Both from the DeepAgents ecosystem.
- **Naming**: hyphen-case, max 64 characters. Description max 1024 characters, no angle brackets.

The skill generator (`src/orchestrator/generators/skill.py`) must produce skills conforming to this format. The verification gate runs `quick_validate.py` as a structural pre-check before the three-tier verification.

## Directory Structure

```
src/
├── orchestrator/       # Pillar 1: pipeline orchestration (LangGraph)
│   ├── decomposition.py    # Task decomposition (DeepAgent, invoked from node)
│   ├── classifier.py       # Capability classification
│   ├── gap_detection.py    # Skill gap detection
│   ├── pipeline.py         # Main LangGraph pipeline graph
│   ├── tool_loader.py      # Dynamic MCP tool loading (importlib, not subprocess servers)
│   └── generators/         # Capability generators
│       ├── mcp_tool.py
│       ├── skill.py
│       └── agent.py
├── registry/           # Skill registry (ChromaDB) and metrics (DuckDB)
│   ├── store.py            # ChromaDB-backed registry
│   └── metrics.py          # DuckDB metrics store
├── verification/       # Pillar 2: three-tier verification
│   ├── static_scanner.py   # Tier 1: AST + regex
│   ├── behavioral.py       # Tier 2: edge-case probing
│   ├── adversarial.py      # Tier 3: red-team agent (DeepAgent, invoked from node)
│   └── gate.py             # Verification gate (combines all tiers)
├── optimizer/          # Pillar 3: granularity optimizer
│   ├── utility.py          # Utility function computation
│   ├── operations.py       # Split, merge, prune
│   ├── loop.py             # Optimization loop
│   └── simulation.py       # Simulation harness
├── schemas/            # Shared Pydantic state schemas (single source of truth)
├── mcp_tools/          # Generated & hand-built MCP tools
└── agents/             # Agent configs & system prompts

.deepagents/
└── skills/             # Generated skills runtime location — DeepAgents loads from here automatically

tools/                  # Development & validation utilities
├── quick_validate.py       # DeepAgents skill validator (structural checks)
└── init_skill.py           # DeepAgents skill scaffolding script

tests/
├── unit/               # Fast, no I/O, no external services
├── integration/        # Docker, ChromaDB, DuckDB, MCP servers
└── evaluation/         # Thesis evaluation harnesses and baselines
```

## Skills

The following skills are available in `.claude/skills/` and must be followed:

- **pydantic-schemas** — All inter-node state must use Pydantic models from `src/schemas/`. Never use raw dicts for state passing.
- **testing-conventions** — pytest conventions, test directory structure, how to test LangGraph nodes and MCP tools.
- **langfuse-tracing** — LangFuse integration patterns for tracing LLM calls and agent execution. Covers grouped traces, custom trace IDs, scoring, prompt management.
- **langgraph-architect** — LangGraph architecture patterns: Command API for routing, Send for fan-out, `interrupt()` for HITL, error handling, persistence, streaming, runtime config. Has reference files in `references/` for HITL patterns and streaming deep dives.
- **langgraph-docs** — Fetches live LangGraph documentation from `https://docs.langchain.com/llms.txt` for up-to-date API guidance. Use when the langgraph-architect skill doesn't cover a specific API question.
- **deep-agents** — When and how to use DeepAgents vs. plain LangGraph nodes. DeepAgents are complete agent graphs invoked from within pipeline nodes.
- **mcp-builder** — MCP server development guide (FastMCP for Python, TypeScript SDK). Covers tool design, implementation, testing, and evaluation. Has reference files for Python/TypeScript patterns and best practices. Use for both hand-written and generated MCP tools.

- **verification-patterns** — AST scanning patterns, behavioral testing (signature-based input generation), adversarial probing (3 attack patterns), `sandbox_execute` Docker design, tiered cost gate, retry loop with `attempt_count`.

## Package Management

This project uses **uv** for Python management.

### Common Commands

```bash
# Add a dependency
uv add <package-name>

# Remove a dependency
uv remove <package-name>

# Run a script
uv run script.py

# Run a command in project environment
uv run python -m pytest

# Sync dependencies (updates .venv to match pyproject.toml)
uv sync
```

## Observability with LangFuse

LangFuse is used for tracing LLM calls, agent execution, and debugging workflows.

### Integration with LangChain/LangGraph

LangFuse integrates via the `CallbackHandler`:

```python
from langfuse.langchain import CallbackHandler

# Initialize handler
langfuse_handler = CallbackHandler()

# Add to LangGraph/LangChain invocation
response = agent.invoke(
    {"messages": [...]},
    config={"callbacks": [langfuse_handler]}
)
```

### Environment Variables

```bash
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=http://localhost:3000  # or https://cloud.langfuse.com
```

## Development Guidelines

### State Management

Use **structured state schemas** (Pydantic BaseModel) instead of conversation histories for agent handoffs. This reduces token costs and prevents hallucination. See the `pydantic-schemas` skill for conventions.

**Example:**
```python
from pydantic import BaseModel, Field
from typing import Optional

class TaskState(BaseModel):
    task_description: str
    subtasks: list[dict] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
```

### LangGraph Patterns

Use the **Command API** for routing (return `Command(update={...}, goto="next_node")` instead of separate conditional edge functions). Use **Send** for parallel fan-out/fan-in. Use `interrupt()` for human-in-the-loop gates. See the `langgraph-architect` skill and its `references/` directory for detailed patterns.

### LLM Initialization

Use `init_chat_model()` for provider-agnostic LLM access. Never hardcode a provider import:

```python
from langchain.chat_models import init_chat_model

# For plain nodes (fast, single-step)
llm = init_chat_model("deepseek:deepseek-chat")

# For DeepAgents (also deepseek-chat — reasoner lacks tool_choice support)
agent_llm = init_chat_model("deepseek:deepseek-chat")

# Switch provider without changing code
llm = init_chat_model("anthropic:claude-sonnet-4-5-20250929")
```

### DeepAgents Integration

Use DeepAgents only where multi-step autonomous reasoning adds value (see the table above). Key points:
- `create_deep_agent()` returns a `CompiledStateGraph` — invoke it, don't wire it as a node
- Use `response_format=YourPydanticModel` for structured output
- Instruct agents to use files for intermediate results (context management)
- Built-in `write_todos` handles planning — no custom planning loop needed
- See the `deep-agents` skill for implementation patterns

### Verification First

Any generated capability (MCP tool, skill, agent) must pass the three-tier verification gate before being registered. For skills, also run `tools/quick_validate.py` as a structural pre-check. The verification layer is the core thesis contribution.

### Track Metrics

Keep track of:
- Which capabilities get generated
- Which ones pass/fail verification (and why)
- How often capabilities are reused
- Token costs per pipeline stage

This data feeds into the granularity optimizer and thesis evaluation.

## Architectural Constraints (Post-Iteration-0 Critique)

### Testbed Decision

The evaluation testbed is the **Business Analytics Pipeline** — data ingestion, transformation, ML training, inference, and report generation tasks. This was chosen over the Factory Simulation because it naturally produces diverse capability types (MCP tools for deterministic ingestion, Skills for domain workflows, Agents for stateful analysis), exercising all three artifact classifications.

### DeepAgent Operational Boundaries

Every DeepAgent invocation (`create_deep_agent()`) must respect these constraints:

| Constraint | Value | Notes |
|---|---|---|
| `recursion_limit` (decomposition) | 35 | ~17 logical steps; each tool call = 2 graph steps |
| `recursion_limit` (adversarial probing) | 35 | Needs more exploration room |
| `recursion_limit` (failure analysis) | 25 | Focused investigation |
| Node-level timeout (decomposition) | 120s | Prevents pipeline hang |
| Node-level timeout (adversarial) | 180s | Allows sandbox execution |
| Initialization | Fresh every invocation | No state carried between calls |
| Output contract | `response_format=PydanticModel` | Structured output, not free-text |
| Fallback on timeout/max_steps | Return partial results with error flag | Pipeline continues degraded |

If a DeepAgent exceeds its budget, the LangGraph node returns a partial result with an error flag — the pipeline does not hang.

### MCP Tool Loading Strategy

Generated MCP tools are loaded via `importlib` dynamic import in a sandboxed subprocess (`src/orchestrator/tool_loader.py`). **Not** persistent FastMCP server processes — no ports, no health checks, no zombie process risk. The same loader is reused by Tier 2 behavioral testing in Iteration 3. Full MCP server deployment is future work (Chapter 7).

### Generated Skills Landing Path

Generated SKILL.md files are written to `.deepagents/skills/<skill-name>/` in the project root. DeepAgents loads project-level skills from this path automatically — no copy or symlink step needed. `src/skills/` does not exist and should not be used. User-level `~/.deepagents/<agent>/skills/` is reserved for stable, hand-curated skills only.

### Verification Gate: Tiered Cost Optimization

The verification layer uses fail-fast to avoid wasting tokens:

1. **Tier 0** (Skills only): `tools/quick_validate.py` structural check (~0 cost)
2. **Tier 1**: Static scanner (~0 cost). **Critical findings → reject immediately, skip Tier 2/3**
3. **Tier 2**: Behavioral testing (moderate cost). Only if Tier 1 passes or has High/Medium only
4. **Tier 3**: Adversarial probing (high cost). Only if Tier 2 passes

Never run Tier 2/3 on a capability that Tier 1 has already condemned as Critical.

### Pillar 3: Type-Scoped Operations

Evolutionary operations are scoped by artifact type:

| Operation | MCP Tools | Skills | Agents |
|---|---|---|---|
| **Prune** | ✅ | ✅ | ✅ |
| **Split** | ❌ (future work) | ✅ | ✅ |
| **Merge** | ❌ (future work) | ✅ | ❌ |

The formal framework (utility function, metrics, convergence) applies to all types. Implementation is scoped to where LLMs can reliably perform the operations. MCP tool code refactoring is documented as future work.

Always check `capability_type` before attempting split/merge. If someone tries to split/merge an MCP tool, reject gracefully with a clear message — don't silently skip.

### LLM Output Robustness

All LLM-calling nodes must implement:

1. **Few-shot prompting**: Include at least one complete valid example in every generator prompt (MCP tool, Skill, Agent). Don't just give the schema — give a full example of correct output.
2. **Repair pass**: If Pydantic validation fails, send raw output + validation error back to the model once. If repair also fails, return an error.
3. **LangFuse logging**: Log every malformed output, validation error, and repair attempt.

### MCP Server Management

For the thesis prototype, use `importlib` dynamic imports + sandbox execution rather than subprocess MCP servers. Generated tools follow FastMCP structure but are loaded dynamically, not deployed as persistent servers. This avoids subprocess lifecycle complexity (zombie processes, port conflicts, health checks) while preserving the generate → verify → load → execute loop.

Full MCP server deployment is production future work (Chapter 7).

### Minimum Viable Scope

If timeline pressure forces scope reduction:

| Pillar | Full | Minimum | What You Lose |
|---|---|---|---|
| **1 (Pipeline)** | MCP tools + Skills + Agents | MCP tools only | Classification trivial, less diversity |
| **2 (Verification)** | Tier 1 + 2 + 3 | Tier 1 + 2 only | Lose adversarial DeepAgent; discuss theoretically |
| **3 (Optimizer)** | Prune + Split + Merge | Prune only + formal framework | Demonstrate framework theoretically; simulation still shows convergence |

### Critique Checkpoints

| When | Question |
|---|---|
| After Iteration 2 | Are generated capabilities good enough for verification to work with? |
| After Iteration 5 | Does evaluation design produce thesis data? |

## What's Built

### Iteration 0 (Complete)

- **`src/orchestrator/pipeline.py`** — 3-node LangGraph graph (`intake` → `gap_detection` → `report`) using Command API for routing. Classifies registry matches vs. gaps based on similarity threshold.
- **`src/mcp_tools/fibonacci.py`** — Hand-written FastMCP tool with `Annotated[int, Field()]` for flat MCP parameter schema, tool annotations (`readOnlyHint`, `idempotentHint`, etc.), async implementation.
- **`demo.py`** — End-to-end demo showing both paths (capability found + MCP call, gap detected). Wired with LangFuse `CallbackHandler` via `config={"callbacks": [...]}`.
- **`sandbox/Dockerfile`** — Minimal Python 3.11-slim container, non-root `sandbox` user, no network access.
- **`Makefile`** — Convenience targets: `test`, `lint`, `format` (all via `uv run`).

### Iteration 1 (Complete)

- **`src/schemas/`** — 14 Pydantic models across 4 files, all with `extra="forbid"`, `Field(description=...)`, StrEnum:
  - `capability.py`: `CapabilityType`, `CapabilityDescriptor` (expanded with `input_schema`, `output_schema`, `tags`, `domain`, `is_active`, `metadata`)
  - `task.py`: `TaskStatus`, `SubTask`, `ClassifiedGap`, `GeneratedArtifact`, `TaskState` (expanded with subtasks, classified_gaps, generated_artifacts, verification_results, status, errors, trace_id), `GapItem`, `MatchItem`
  - `verification.py`: `Severity`, `VerificationFinding`, `VerificationReport`
  - `metrics.py`: `InvocationRecord`, `GranularityMetrics`
- **`src/registry/store.py`** — Production ChromaDB registry with hierarchical embeddings (`{domain} > {type} > {name}: {description}`), full CRUD (`get`, `update`, `deactivate`, `delete`, `list_all`), `min_confidence` filtering, 8 seeded capabilities across 3 types and 5 domains.
- **`src/registry/metrics.py`** — DuckDB metrics store tracking invocations and capability lifecycle events. Computes four granularity metrics: `reuse_rate`, `success_rate`, `avg_token_cost`, `maintenance_burden`. Supports `compute_granularity_metrics()` aggregate and persistence to disk.
- **Tests** — 115 passing: 57 schema tests, 29 registry tests, 17 metrics tests, 6 pipeline tests, 4 fibonacci tests, 2 integration tests.

### Iteration 2 Phase A (Complete)

- **`src/orchestrator/decomposition.py`** — DeepAgent (`create_deep_agent`) wrapped in a LangGraph node. Uses `deepseek-chat` (reasoner lacks `tool_choice`), `response_format=DecompositionResult`, 120s timeout via `ThreadPoolExecutor`, `recursion_limit=35`. Falls back to treating the whole task as one subtask on error.
- **`src/orchestrator/classifier.py`** — LLM-only classification with `temperature=0` and `with_structured_output(ClassificationResult)`. Shared LLM instance via lazy singleton. Falls back to `MCP_TOOL` with 0.3 confidence on LLM failure.
- **`src/orchestrator/prompts/`** — Prompt package with `decomposition.py`, `classifier.py`, `mcp_tool_generator.py`, `skill_generator.py`, `agent_generator.py`. All generator prompts include complete few-shot examples.
- **`src/schemas/task.py`** — Added `SubTaskItem`, `DecompositionResult`, `ClassificationResult`, `GeneratorInput` (18 total schema types across 4 files).
- **Tests** — 143 unit + 7 integration (3 decomposition, 4 classifier) = 150 passing.

### Iteration 2 Phase B (Complete)

- **`src/orchestrator/generators/mcp_tool.py`** — Plain LangGraph node. Generates FastMCP Python tools from `ClassifiedGap`. Validates syntax + FastMCP structure (`FastMCP`, `@mcp.tool`, `async def`). One-shot repair pass on failure. LangFuse tracing.
- **`src/orchestrator/generators/skill.py`** — Plain LangGraph node. Generates SKILL.md files from `ClassifiedGap`. Validates with `tools/quick_validate.py` + non-empty body check. Repair pass. Writes to `.deepagents/skills/<name>/SKILL.md`. LangFuse tracing.
- **`src/orchestrator/generators/agent.py`** — Plain LangGraph node. Generates system prompts + LangGraph node configs. Detects DeepAgent vs. plain agent. For DeepAgent-type, generates SKILL.md content AND writes it to `.deepagents/skills/<agent-name>/SKILL.md`. Repair pass. LangFuse tracing.
- **`src/orchestrator/tool_loader.py`** — `ToolLoader` class. Loads generated MCP tools via `importlib` dynamic import. Executes in sandboxed subprocess. `register_tool()` registers loaded tools in `SkillRegistry`. Cleanup on exit via `atexit`.
- **Tests** — 304 total (280 unit + 24 integration). All pass with `DEEPSEEK_API_KEY` set. Integration `_node` tests for generators had stale `goto == "__end__"` assertions (fixed post Phase C: mcp_tool → "generate_skill", skill → "generate_agent", agent → "loading_node"). 24 integration tests: 5 agent-generator, 4 classifier, 3 decomposition, 2 fibonacci, 3 mcp-tool-generator, 4 pipeline-e2e, 3 skill-generator.

### Iteration 2 Phase C (Complete)

- **`src/orchestrator/gap_detection.py`** — Pure Python LangGraph node wrapping `registry.detect_gaps()`. Converts raw registry output to `MatchItem` / `GapItem` lists. Routes to `"classifier_node"`.
- **`src/orchestrator/loading_node.py`** — Loads and registers `MCP_TOOL` artifacts via `ToolLoader`. Smoke-tests each tool with empty inputs (warning on failure, continues). Skills/agents already on disk — no loading needed. Sets `FAILED` if gaps existed but no artifacts were generated; `COMPLETED` otherwise.
- **`src/orchestrator/pipeline.py`** (rewritten) — Full Iteration 2 pipeline. `create_pipeline(registry, loader)` builds: `START → decomposition_node → gap_detection → classifier_node → generate_mcp_tool → generate_skill → generate_agent → loading_node → END`. Registry and loader injected via closures.
- **Generator `goto` updates** — All four chaining nodes updated: `classifier_node → generate_mcp_tool`, `generate_mcp_tool → generate_skill`, `generate_skill → generate_agent`, `generate_agent → loading_node`.
- **Tests** — 297 unit tests pass (0 skipped). 4 integration tests in `tests/integration/test_pipeline_e2e.py` (skip without `DEEPSEEK_API_KEY`). Total: 321 tests (297 unit + 24 integration).

### ✅ Decision: Generator chaining strategy → sequential (not Send fan-out)

The three generator nodes (`generate_mcp_tool`, `generate_skill`, `generate_agent`) are chained sequentially. Each node filters by `classified_type`, processes its gaps, and hands off to the next node via `Command(goto=...)`.

**Why not Send fan-out:** Parallel fan-out requires Annotated reducers on `TaskState` list fields and refactored generator signatures accepting `GeneratorInput` instead of `TaskState`. Sequential chaining is simpler, avoids schema changes, and is sufficient for the thesis — generation latency is not a bottleneck. Send-based parallelism is documented as future work.

### Iteration 3 Pre-work (Complete)

- **`src/schemas/task.py`** — Added `attempt_count: int = 0` (ge=0) to `TaskState`. Tracks generation+verification retry cycles for the 3-strike system. 4 new unit tests (324 unit total).
- **`.claude/skills/verification-patterns/SKILL.md`** — New skill covering: 5 Tier 1 contamination categories with AST patterns, Tier 2 signature-based input extraction, `sandbox_execute` Docker design (must use Docker, never `sys.executable`), 3-strike retry loop, tiered cost gate, updated Iteration 3 pipeline topology.

### Iteration 3 Foundation (Complete — pre-team start)

- **`src/schemas/verification.py`** — `VerificationFinding.tier` constraint corrected to `ge=0` (was `ge=1`). Tier 0 findings (structural pre-check from `quick_validate.py`) require tier=0 to be valid.
- **`src/orchestrator/verification_node.py`** — NEW. Stub verification node with correct retry logic: runs `gate_fn` per artifact, increments `attempt_count`, removes failed artifacts from state on retry (routes to `"generate_mcp_tool"`), fails open at `MAX_ATTEMPTS=3` (routes to `"loading_node"`). `gate_fn=None` uses `_stub_gate` (always passes) until Iteration 3 replaces it with `gate.verify`. 19 unit tests.
- **`src/orchestrator/generators/`** — All three generators (`mcp_tool`, `skill`, `agent`) now replace by `capability_name` instead of blindly appending. On retry cycles the generator re-runs all classified gaps; replacing by name prevents duplicate entries for the same capability.
- **`src/orchestrator/generators/agent.py`** — `goto` changed from `"loading_node"` to `"verification_node"`.
- **`src/orchestrator/pipeline.py`** — `verification_node` added between `generate_agent` and `loading_node`. Topology is now 9 nodes: `START → decomposition_node → gap_detection → classifier_node → generate_mcp_tool → generate_skill → generate_agent → verification_node → loading_node → END`.
- **`src/verification/sandbox_tool.py`** — NEW. `sandbox_execute(code, timeout=10) -> dict` runs an arbitrary Python snippet inside `docker run --rm --network none` (128 MB RAM, 0.5 CPU). Returns `{stdout, stderr, exit_code, success}`. Failures and timeouts are captured — never raises. The adversarial teammate **must import from here**; do **not** use `ToolLoader.execute_tool` (runs on host via `sys.executable`). 10 unit tests (subprocess mocked). 4 `@pytest.mark.integration` tests require the `jit-sandbox` image (`make build-sandbox`).
- **`Makefile`** — `build-sandbox` target added (`docker build -t jit-sandbox sandbox/`).
- **Tests** — 353 unit (0 skipped) + 24 integration = 377 total.

## Current Iteration

**Iteration 3: Verification Layer.** Pipeline skeleton is wired (`verification_node` stub in place). Ready to implement the real three-tier gate.

- `verification_node` stub in `src/orchestrator/verification_node.py` — replace `gate_fn` with `gate.verify` in `pipeline.py` once `src/verification/gate.py` is ready
- `attempt_count` and retry routing already work end-to-end (tested)
- `sandbox_execute` is **ready** in `src/verification/sandbox_tool.py` — import from there; do **not** recreate it
- Tier 2 behavioral tester must parse `source_code` AST for parameter schema (`input_schema` is empty in registry by design)
- `VerificationFinding.tier` supports 0–3 (Tier 0 = structural pre-check)
- Build the sandbox image before running integration tests: `make build-sandbox`

See `docs/iteration_plan.md` for the full Iteration 3 spec.

## Stable Interfaces

The following modules are implemented and tested. Import from them; avoid modifying unless explicitly required:

- **`src/schemas/`** — All 18 types: `CapabilityType`, `CapabilityDescriptor`, `TaskState` (now includes `attempt_count: int = 0`), `TaskStatus`, `SubTask`, `SubTaskItem`, `DecompositionResult`, `ClassificationResult`, `ClassifiedGap`, `GeneratedArtifact`, `GeneratorInput`, `GapItem`, `MatchItem`, `Severity`, `VerificationFinding`, `VerificationReport`, `InvocationRecord`, `GranularityMetrics`
- **`src/orchestrator/decomposition.py`** — `decomposition_node()` (DeepAgent-backed task decomposition)
- **`src/orchestrator/classifier.py`** — `classify_gap()`, `classifier_node()` (LLM-based classification)
- **`src/orchestrator/prompts/`** — All prompt constants for decomposition, classification, and all three generators
- **`src/orchestrator/gap_detection.py`** — `gap_detection_node(state, registry)` (detects registry gaps, always routes to `"classifier_node"`)
- **`src/orchestrator/loading_node.py`** — `loading_node(state, loader, registry)` (loads + registers MCP_TOOL artifacts, sets terminal status)
- **`src/orchestrator/pipeline.py`** — `create_pipeline(registry, loader)` (9-node pipeline; verification_node stub wired between generate_agent and loading_node)
- **`src/orchestrator/verification_node.py`** — `verification_node(state, gate_fn=None)` (retry + artifact replacement logic; stub gate until Iter 3 fills in real gate)
- **`src/orchestrator/generators/`** — `generate_mcp_tool()`, `generate_skill()`, `generate_agent()` (LangGraph nodes; chained sequentially to `verification_node`)
- **`src/orchestrator/tool_loader.py`** — `ToolLoader` (load, execute, register_tool, cleanup), `LoadedTool`, `ToolResult`
- **`src/registry/store.py`** — `SkillRegistry` (full CRUD + hierarchical search), `seed_registry()`
- **`src/registry/metrics.py`** — `MetricsStore` (invocation recording + 4 granularity metric queries)
- **`src/mcp_tools/fibonacci.py`** — Reference MCP tool implementation

## Notes for AI Agents

When working on this project:

1. **Use uv** — All Python package operations should use `uv`, not `pip` directly
2. **Read skills** — Check `.claude/skills/` for conventions before implementing anything. The `langgraph-architect` skill has `references/` files for HITL and streaming patterns.
3. **Trace with LangFuse** — Agent execution and LLM calls should be traced for debugging
4. **Use Pydantic for all state** — Never pass raw dicts between LangGraph nodes
5. **Use Command API for routing** — Return `Command(update={...}, goto="node")` from nodes instead of using `add_conditional_edges`
6. **DeepAgents where appropriate** — Use for task decomposition and adversarial probing; plain nodes for everything else. Remember: DeepAgents are invoked from nodes, not wired as nodes.
7. **Generated skills must conform to DeepAgents format** — SKILL.md + optional scripts/references/assets. Validate with `tools/quick_validate.py`.
8. **Ask before making architectural decisions** — Many design choices are still being explored
9. **Document decisions** — Update this file or `docs/iteration_plan.md` when important choices are made (not `docs/decisions/` — all agents read CLAUDE.md and the iteration plan; separate decision files drift out of sync)
10. **Work incrementally** — Build and test one component at a time
11. **Tests are mandatory** — No module is done without tests (see `testing-conventions` skill)

## Resources

- **LangGraph docs**: https://docs.langchain.com/oss/python/langgraph/overview
- **LangGraph llms.txt**: https://docs.langchain.com/llms.txt
- **DeepAgents docs**: https://docs.langchain.com/oss/python/deepagents/overview/
- **DeepAgents PyPI**: https://pypi.org/project/deepagents/
- **DeepSeek API docs**: https://api-docs.deepseek.com/
- **DeepAgents skill spec**: See `tools/quick_validate.py` and `tools/init_skill.py` for format rules
- **uv docs**: https://docs.astral.sh/uv/
- **MCP specification**: https://modelcontextprotocol.io/
- **Agent Skills spec**: https://agentskills.io/
- **LangFuse docs**: https://langfuse.com/docs

---

**Last updated:** February 2026
