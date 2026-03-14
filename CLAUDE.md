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
- **LLM Provider** - Provider-agnostic via `init_chat_model("provider:model")`.
  - `deepseek-chat` (DeepSeek-V3.2) — for plain LangGraph nodes (classifiers, generators, behavioral testing)
  - `openai:gpt-5-mini` — for DeepAgent components only (task decomposition, adversarial probing). Switched from DeepSeek during Iteration 5 pilot because DeepSeek's tool-call latency under DeepAgent's multi-step loop was prohibitive (~120s per decomposition vs ~30s with GPT-5-mini).
  - `deepseek-reasoner` (thinking mode) — **not usable with DeepAgents** due to missing `tool_choice` support; reserved for direct LLM calls without tool use
  - **Environment:** Requires both `DEEPSEEK_API_KEY` and `OPENAI_API_KEY` in `.env`
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
| Task decomposition | **DeepAgent** (invoked from node) | `openai:gpt-5-mini` | Multi-step planning with `write_todos`, file-based context for large task descriptions |
| Capability classifier | Plain LangGraph node | `deepseek-chat` | LLM classification with structured output (`temperature=0`) — single-step |
| MCP tool generator | Plain LangGraph node | `deepseek-chat` | Single-pass code generation with retry on failure |
| Skill generator | Plain LangGraph node | `deepseek-chat` | Structured text generation targeting DeepAgents skill format |
| Agent generator | Plain LangGraph node | `deepseek-chat` | System prompt + config generation |
| Tier 1 static scanner | Plain Python (no LLM) | — | AST + regex, deterministic |
| Tier 2 behavioral testing | Plain LangGraph node | `deepseek-chat` | Test input generation + execution |
| Tier 3 adversarial probing | **DeepAgent** (invoked from node) | `openai:gpt-5-mini` | Multi-step reasoning: plan attacks via `write_todos`, execute in sandbox via `execute` tool, write findings to files, iterate |
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

LangFuse traces LLM calls via `langfuse.langchain.CallbackHandler` passed as `config={"callbacks": [langfuse_handler]}`. Required env vars: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`. See `langfuse-tracing` skill for full patterns.

## Development Guidelines

### State Management

Use **Pydantic BaseModel** for all inter-node state (see `pydantic-schemas` skill). Never pass raw dicts between nodes.

### LangGraph Patterns

Use **Command API** for routing: `Command(update={...}, goto="next_node")`. Use **Send** for parallel fan-out. Use `interrupt()` for HITL. See `langgraph-architect` skill.

### LLM Initialization

Use `init_chat_model("deepseek:deepseek-chat")` for plain LangGraph nodes. Use `init_chat_model("openai:gpt-5-mini")` for DeepAgent components (decomposition, adversarial probing). Never hardcode provider imports. `deepseek-reasoner` is NOT usable with DeepAgents (missing `tool_choice`).

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
| `recursion_limit` (decomposition) | 50 | ~25 logical steps; 35 hit limit in practice under slow DeepSeek |
| `recursion_limit` (adversarial probing) | 50 | Needs more exploration room; 35 hit limit in practice |
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

### Sandbox Dependency Limitation

The sandbox Dockerfile hardcodes `pip install pandas numpy mcp pydantic`. This suffices for the thesis testbed but limits generated tools to these packages. Production approach: extract imports from generated source (AST), map to PyPI packages, and inject `pip install` dynamically — or build per-artifact ephemeral containers. Documented as future work (Chapter 7).

## What's Built

### Iterations 0–4 (All Complete)

All core infrastructure is built and tested. Key architectural decisions locked in:

- **Iter 0**: Pipeline skeleton, sandbox Dockerfile (adds `pip install mcp pydantic pandas numpy`), fibonacci reference tool.
- **Iter 1**: All 18 Pydantic schemas (`src/schemas/`), ChromaDB registry (`store.py`), DuckDB metrics (`metrics.py`).
- **Iter 2**: Full generator pipeline (`decomposition → gap_detection → classifier → generate_mcp_tool → generate_skill → generate_agent → loading_node`). Generators chained **sequentially** (not Send fan-out — simpler, no schema changes needed). All prompts include few-shot examples + repair pass.
- **Iter 3**: Three-tier verification gate. Critical fix: `_build_sandbox_code` prepends `__name__ = "not_main"` to prevent `mcp.run()` blocking sandbox. `sandbox/Dockerfile` installs `mcp pydantic`. `build_task_context(state)` injects CRITICAL/HIGH findings on retry. 18 bypass findings documented in `docs/iteration_plan.md` (see Devil's Advocate section).
- **Iter 4**: Granularity optimizer (utility function, prune/split/merge operations, optimization loop, simulation). KL divergence in `loop.py` uses raw scores (point-mass); `simulation.py` uses histograms — thesis should clarify which is canonical. `create_pipeline` gains `run_optimizer` and `metrics_store` params.

## Current Iteration

**Iteration 5: Integration & End-to-End Testing.** Wire all three pillars together. Build evaluation infrastructure and baselines.

### Evaluation infrastructure (complete — 707 unit tests pass)

- **`src/schemas/evaluation.py`** — `EvaluationRecord` Pydantic model (18 fields). Separate from `InvocationRecord`. `success` property = `gate_passed AND output_in_range`. 21 unit tests.
- **`src/registry/evaluation_store.py`** — Separate DuckDB `EvaluationStore`. `evaluation_runs` table. Methods: `record_run()`, `get_runs()`, `compute_success_rate()`, `compute_gate_pass_rate()`, `run_count()`, `failure_breakdown()`. 17 unit tests.
- **`tests/evaluation/fixtures/`** — 10 fixture files generated programmatically (seed=42) + `expected_values.json` with pre-computed validator values. `generate.py` regenerates deterministically.
- **`tests/evaluation/tasks.py`** — `TaskDefinition` frozen dataclass. T01–T10 with validators reading `expected_values.json` at import time. Static coverage: T01 (csv-parser), T02 (json-validator), T03 (text-statistics), T05 (data-cleaning-workflow), T09 (report-generation). `ALL_TASKS`, `PILOT_TASKS=[T01,T07,T10]`, `get_task()`. 34 unit tests.
- **`tests/evaluation/snapshots.py`** — `take_clean_snapshot()`, `restore_clean_snapshot()`, `snapshot_exists()`, `_hash_snapshot()`. Covers ChromaDB dir + `src/mcp_tools/` + `.deepagents/skills/`. SHA-256 hash (excluding `meta.json`) as snap ID. Idempotent. 19 unit tests.
- **`tests/evaluation/harness.py`** — `HarnessConfig` dataclass, `EvaluationHarness` class. `prepare()`, `run_pilot()`, `run_condition()`, `close()`. Run loop: restore snapshot → seed registry → shuffle tasks → execute per condition → record to `EvaluationStore`. `MetricsStore` created once per run (shared across tasks so optimizer accumulates data). Static implementations for T01/T02/T03/T05/T09. `_execute_artifact()` uses `sandbox_execute` for MCP_TOOL artifacts. 22 unit tests.
- **`src/orchestrator/pipeline.py`** — Added `verification_enabled: bool = True` parameter. `gate_fn=None` when `False` (no_verification baseline).

### Pilot findings (2026-03-11)

First pilot run (T01, T07, T10 under `full` condition) achieved 3/3 success but took **2+ hours** — unacceptable for evaluation. Root cause: six compounding architectural bottlenecks documented in `docs/investigation_pipeline_bottlenecks.md`:

1. **Over-decomposition** (CRITICAL) — T07 fuzzy-join produces 14 capabilities instead of 1-2. Decomposition prompt optimizes for "smallest meaningful" granularity.
2. **Full regeneration on retry** (CRITICAL) — When 2/10 artifacts fail verification, all 11 gaps are regenerated (not just the 2 failed ones).
3. **Sequential verification** (HIGH) — 14 artifacts × ~2 min each = 28 min per attempt, runs in a `for` loop.
4. **Optimizer prunes seeded capabilities** (HIGH) — `prune()` deactivates all seeded caps (csv-parser, etc.) because metrics store only tracks MCP tool invocations, not registry matches.
5. **Sequential generation** (MEDIUM) — 11 gaps × ~25s each = 5 min per cycle, sequential.
6. **Artifact accumulation** (MEDIUM) — LLM name non-determinism causes duplicate artifacts across retries.

Additionally: sandbox missing `pandas`/`numpy` causes all data-processing tools to fail Tier 2 unconditionally.

### Pipeline performance fix plan

**8 fixes** documented in `docs/plan.md`. Implementation order:

```
Fix 0: sandbox/Dockerfile + generator prompt (pandas/numpy) — unblocking
Fix 1: decomposition prompt + two-stage circuit breaker (warn@5, truncate@8)
Fix 2: pin seeded capabilities against pruning (metadata={"pinned": "true"})
Fix 3: track registry matches as invocations in MetricsStore
Fix 5: deterministic capability naming with sha256 hash suffix (before Fix 4)
Fix 4: skip already-passing gaps on retry (gap_query field on GeneratedArtifact)
Fix 6: parallelize verification (ThreadPoolExecutor, 4 workers)
Fix 7: parallelize generation (ThreadPoolExecutor, 4 workers)
```

Expected result: pilot runtime from 2+ hours → ~15-25 minutes. 707 tests, zero expected breakage, ~27 new tests.

### Pilot re-run results (2026-03-12, post-fix)

After Fix 0–7 and classifier prompt rewrite (Single-Capability Rule): **66.7% success (2/3), 100% gate pass (3/3)**. T01 registry match, T10 generated MCP_TOOL success, T07 generated MCP_TOOL passes gate but output has semantic bug (null_count_on_key=3 vs expected 0).

**Semantic correctness gap:** Verification gate catches structural/deception issues but not business logic correctness. The gap between gate_pass_rate (100%) and success_rate (66.7%) quantifies this limitation. Deferred to future work — see `docs/iteration_plan.md` → "Pilot run findings (2026-03-12)" for full analysis and three proposed approaches.

### Full evaluation results (2026-03-13, 200 records)

| Condition | Success | Gate Pass |
|---|---|---|
| no_verification | 62.0% | 100.0% |
| full | 58.0% | 90.0% |
| no_optimization | 52.0% | 94.0% |
| static | 50.0% | 50.0% |

Task tiers (JIT): Always pass (T01-T03, T10 = 100%), Stochastic (T05 80%, T07 73%), Near-zero (T04, T08, T09 ~7%), Never pass (T06 0%).

### SKILL/AGENT execution support (2026-03-13)

Original evaluation only exercised MCP_TOOL end-to-end. Added:
- **T11** (multi-source data integration) — targets SKILL classification
- **T12** (configurable data quality pipeline) — targets SKILL classification
- **T13** (autonomous anomaly investigation) — targets AGENT classification
- **`tests/evaluation/execution.py`** — SKILL/AGENT execution via DeepAgent (same pattern as adversarial.py)
- Harness now dispatches to `execute_skill_artifact()` / `execute_agent_artifact()` instead of returning `{}` for non-MCP_TOOL
- Removed MCP_TOOL preference in `_extract_result` — uses whatever type the classifier chose
- T11-T13 have no static coverage (by design — static libraries can't solve multi-step orchestration)

### Still needed

- [x] Implement Fix 0–7 per `docs/plan.md`
- [x] Re-run pilot after fixes
- [x] Classifier prompt rewrite (Single-Capability Rule)
- [x] Run 4 baseline conditions across all tasks (n_runs ≥ 5 for statistical significance)
- [x] SKILL/AGENT execution in harness + T11-T13 tasks
- [ ] Re-run full evaluation with 13 tasks
- [ ] Critique checkpoint: does the evaluation design produce usable thesis data?

**Runner:** `uv run scripts/run_pilot.py` (see `docs/iteration_plan.md` → Iteration 5 for full command reference)

**Agent team:** Not needed — all 3 baseline conditions are condition flags on the shared harness, not separate codebases.

## Stable Interfaces

The following modules are implemented and tested. Import from them; avoid modifying unless explicitly required:

- **`src/optimizer/utility.py`** — `compute_utility(capability_id, metrics_store, alpha, beta, gamma) -> float`, `compute_all_utilities(registry, metrics_store, ...) -> dict[str, float]`
- **`src/optimizer/operations.py`** — `prune(registry, metrics_store, window_days) -> list[str]`, `split(registry, metrics_store, capability_id, llm_model) -> list[str]` (SKILL/AGENT only), `merge(registry, metrics_store, capability_ids, llm_model) -> str` (SKILL only)
- **`src/optimizer/loop.py`** — `create_optimization_loop(registry, metrics_store, ...) -> CompiledStateGraph`, `OptimizerState`
- **`src/optimizer/simulation.py`** — `SimConfig`, `SimResult`, `generate_synthetic_registry()`, `run_simulation(config, with_optimization) -> list[SimResult]`, `plot_results()`, `compare_baselines()`
- **`src/schemas/`** — All 18 types: `CapabilityType`, `CapabilityDescriptor`, `TaskState` (now includes `attempt_count: int = 0`), `TaskStatus`, `SubTask`, `SubTaskItem`, `DecompositionResult`, `ClassificationResult`, `ClassifiedGap`, `GeneratedArtifact`, `GeneratorInput`, `GapItem`, `MatchItem`, `Severity`, `VerificationFinding`, `VerificationReport`, `InvocationRecord`, `GranularityMetrics`
- **`src/orchestrator/decomposition.py`** — `decomposition_node()` (DeepAgent-backed task decomposition)
- **`src/orchestrator/classifier.py`** — `classify_gap()`, `classifier_node()` (LLM-based classification)
- **`src/orchestrator/prompts/`** — All prompt constants for decomposition, classification, and all three generators
- **`src/orchestrator/gap_detection.py`** — `gap_detection_node(state, registry)` (detects registry gaps, always routes to `"classifier_node"`)
- **`src/orchestrator/loading_node.py`** — `loading_node(state, loader, registry)` (loads + registers MCP_TOOL artifacts, sets terminal status)
- **`src/orchestrator/pipeline.py`** — `create_pipeline(registry, loader, *, run_optimizer=False, metrics_store=None)` (9-node pipeline; real three-tier gate wired via `gate_fn=gate_verify` closure; optional `optimizer_node` appended after `loading_node` when `run_optimizer=True`)
- **`src/orchestrator/verification_node.py`** — `verification_node(state, gate_fn=None)` (retry + artifact replacement logic; real gate wired in pipeline.py)
- **`src/orchestrator/generators/`** — `generate_mcp_tool()`, `generate_skill()`, `generate_agent()` (LangGraph nodes; chained sequentially to `verification_node`)
- **`src/verification/static_scanner.py`** — `scan(source_code, capability_name, capability_type) -> VerificationReport`, `scan_text_only(text, name) -> VerificationReport`, `should_proceed_to_tier2(report) -> bool`
- **`src/verification/behavioral.py`** — `run(artifact) -> list[VerificationFinding]`, `extract_parameter_schema(source_code) -> dict`
- **`src/verification/adversarial.py`** — `probe(artifact) -> VerificationReport` (DeepAgent, never raises), `AdversarialFinding`, `AdversarialFindings`
- **`src/verification/gate.py`** — `verify(artifact) -> VerificationReport` (tiered cost gate, wired as `gate_fn` in pipeline)
- **`src/verification/sandbox_tool.py`** — `sandbox_execute(code, timeout=10) -> dict` (Docker isolation; use for all adversarial/behavioral probing)
- **`src/orchestrator/tool_loader.py`** — `ToolLoader` (load, execute, register_tool, cleanup), `LoadedTool`, `ToolResult`
- **`src/registry/store.py`** — `SkillRegistry` (full CRUD + hierarchical search), `seed_registry()`
- **`src/registry/metrics.py`** — `MetricsStore` (invocation recording + 4 granularity metric queries)
- **`src/registry/evaluation_store.py`** — `EvaluationStore` (evaluation_runs DuckDB table, success rates, failure breakdown)
- **`src/schemas/evaluation.py`** — `EvaluationRecord` (18 fields, `success` property)
- **`src/mcp_tools/fibonacci.py`** — Reference MCP tool implementation
- **`tests/evaluation/tasks.py`** — `TaskDefinition`, `ALL_TASKS`, `PILOT_TASKS`, `get_task()`
- **`tests/evaluation/snapshots.py`** — `take_clean_snapshot()`, `restore_clean_snapshot()`, `snapshot_exists()`
- **`tests/evaluation/harness.py`** — `EvaluationHarness`, `HarnessConfig`, `_run_static_implementation()`
- **`tests/evaluation/execution.py`** — `execute_skill_artifact()`, `execute_agent_artifact()`, `_read_fixture_contents()` (DeepAgent-based SKILL/AGENT execution)
- **`scripts/run_pilot.py`** — CLI runner: `uv run scripts/run_pilot.py [--condition] [--all-tasks] [--all-conditions] [--n-runs N]`

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

**Last updated:** March 2026
