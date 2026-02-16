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
  - `deepseek-reasoner` (thinking mode, DeepSeek-V3.2) — for DeepAgents and complex reasoning (decomposition, adversarial probing, failure analysis)
  - `deepseek-chat` (non-thinking mode, DeepSeek-V3.2) — for plain LangGraph nodes and single-step tasks (classification, generation, behavioral testing)
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
| Task decomposition | **DeepAgent** (invoked from node) | `deepseek-reasoner` | Multi-step planning with `write_todos`, file-based context for large task descriptions |
| Capability classifier | Plain LangGraph node | `deepseek-chat` | Rule-based heuristics with LLM fallback — single-step |
| MCP tool generator | Plain LangGraph node | `deepseek-chat` | Single-pass code generation with retry on failure |
| Skill generator | Plain LangGraph node | `deepseek-chat` | Structured text generation targeting DeepAgents skill format |
| Agent generator | Plain LangGraph node | `deepseek-chat` | System prompt + config generation |
| Tier 1 static scanner | Plain Python (no LLM) | — | AST + regex, deterministic |
| Tier 2 behavioral testing | Plain LangGraph node | `deepseek-chat` | Test input generation + execution |
| Tier 3 adversarial probing | **DeepAgent** (invoked from node) | `deepseek-reasoner` | Multi-step reasoning: plan attacks via `write_todos`, execute in sandbox via `execute` tool, write findings to files, iterate |
| Failure analysis (optimizer) | **DeepAgent** (optional, invoked from node) | `deepseek-reasoner` | Multi-step investigation of why capabilities fail |
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
│   ├── server_manager.py   # Dynamic MCP server lifecycle
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
├── skills/             # Generated SKILL.md files (runtime skills, not .claude/skills)
└── agents/             # Agent configs & system prompts

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

Additional skills will be added in later iterations:
- **verification-patterns** (Iteration 3) — AST scanning, adversarial testing, and verification gate patterns.

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

# For DeepAgents (complex reasoning)
reasoning_llm = init_chat_model("deepseek:deepseek-reasoner")

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
| `max_steps` (decomposition) | 10 | Bounded planning task |
| `max_steps` (adversarial probing) | 15 | Needs more exploration room |
| `max_steps` (failure analysis) | 10 | Focused investigation |
| Node-level timeout (decomposition) | 120s | Prevents pipeline hang |
| Node-level timeout (adversarial) | 180s | Allows sandbox execution |
| Initialization | Fresh every invocation | No state carried between calls |
| Output contract | `response_format=PydanticModel` | Structured output, not free-text |
| Fallback on timeout/max_steps | Return partial results with error flag | Pipeline continues degraded |

If a DeepAgent exceeds its budget, the LangGraph node returns a partial result with an error flag — the pipeline does not hang.

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

## Current Iteration

**Iteration 1 is complete.** Next: Iteration 2 (JIT Capability Pipeline).

See `docs/iteration_plan.md` for the full plan.

## Stable Interfaces

The following modules are implemented and tested. Import from them; avoid modifying unless explicitly required:

- **`src/schemas/`** — All 14 types: `CapabilityType`, `CapabilityDescriptor`, `TaskState`, `TaskStatus`, `SubTask`, `ClassifiedGap`, `GeneratedArtifact`, `GapItem`, `MatchItem`, `Severity`, `VerificationFinding`, `VerificationReport`, `InvocationRecord`, `GranularityMetrics`
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
9. **Document decisions** — Update this file or `docs/decisions/` when important choices are made
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
