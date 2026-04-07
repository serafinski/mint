# MINT — Model-driven Instantiation of Novel Tools

**Master's Thesis Project - February 2026**

## Overview

This project explores just-in-time capability synthesis in multi-agent systems. The goal is to build a system that can generate, verify, and optimize its own capabilities on-demand. The user-facing CLI is `uv run mint`.

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
  - `openai:gpt-5-mini` — unified default for all components, centralized in `src/config.py` as `DEFAULT_LLM_MODEL`. Override via `JIT_LLM_MODEL` environment variable for experiments.
  - **Environment:** Requires `OPENAI_API_KEY` in `.env`
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
| Capability classifier | Plain LangGraph node | `openai:gpt-5-mini` | LLM classification with structured output (`temperature=0`) — single-step |
| MCP tool generator | Plain LangGraph node | `openai:gpt-5-mini` | Single-pass code generation with retry on failure |
| Skill generator | Plain LangGraph node | `openai:gpt-5-mini` | Structured text generation targeting DeepAgents skill format |
| Agent generator | Plain LangGraph node | `openai:gpt-5-mini` | System prompt + config generation |
| Tier 1 static scanner | Plain Python (no LLM) | — | AST + regex, deterministic |
| Tier 2 behavioral testing | Plain LangGraph node | `openai:gpt-5-mini` | Test input generation + execution |
| Tier 3 adversarial probing | **DeepAgent** (invoked from node) | `openai:gpt-5-mini` | Multi-step reasoning: plan attacks via `write_todos`, execute in sandbox via `execute` tool, write findings to files, iterate |
| Failure analysis (optimizer) | **DeepAgent** (optional, invoked from node) | `openai:gpt-5-mini` | Multi-step investigation of why capabilities fail |
| Granularity operations | Plain LangGraph node | `openai:gpt-5-mini` | Metrics-driven decisions, mostly deterministic |

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
├── config.py               # Centralized constants (model, sandbox, thresholds)
├── cli/                    # MINT CLI (Typer + Rich)
│   ├── app.py              # Command definitions (run, plan, retry, list, inspect, ...)
│   ├── runner.py           # Pipeline execution, file handling, output auto-save
│   └── display.py          # Rich progress display, output quality detection
├── orchestrator/           # Pillar 1: pipeline orchestration (LangGraph)
│   ├── decomposition.py    # Task decomposition (DeepAgent, invoked from node)
│   ├── classifier.py       # Capability classification
│   ├── gap_detection.py    # Skill gap detection
│   ├── pipeline.py         # Main LangGraph pipeline graph
│   ├── tool_loader.py      # Dynamic MCP tool loading (importlib, not subprocess servers)
│   ├── loading_node.py     # Dynamic loading + registration (all 3 types)
│   ├── verification_node.py # Retry loop orchestration
│   ├── tracing.py          # LangFuse event emission helpers
│   ├── prompts/            # All LLM prompt templates
│   └── generators/         # Capability generators
│       ├── mcp_tool.py
│       ├── skill.py
│       └── agent.py
├── registry/           # Skill registry (ChromaDB) and metrics (DuckDB)
│   ├── store.py            # ChromaDB-backed registry
│   ├── metrics.py          # DuckDB metrics store
│   └── evaluation_store.py # DuckDB evaluation records
├── verification/       # Pillar 2: three-tier verification
│   ├── static_scanner.py   # Tier 1: AST + regex
│   ├── behavioral.py       # Tier 2: edge-case probing (5 test categories)
│   ├── adversarial.py      # Tier 3: red-team agent (DeepAgent, invoked from node)
│   ├── gate.py             # Verification gate (combines all tiers)
│   └── sandbox_tool.py     # Docker execution + dependency resolution
├── optimizer/          # Pillar 3: granularity optimizer
│   ├── utility.py          # Utility function computation
│   ├── operations.py       # Split, merge, prune
│   ├── loop.py             # Optimization loop
│   └── simulation.py       # Simulation harness
├── schemas/            # Shared Pydantic state schemas (single source of truth)
└── mcp_tools/          # Generated & hand-built MCP tools

.deepagents/
└── skills/             # Generated skills runtime location — DeepAgents loads from here automatically

tools/                  # Development & validation utilities
├── quick_validate.py       # DeepAgents skill validator (structural checks)
└── init_skill.py           # DeepAgents skill scaffolding script

scripts/                # Evaluation runners + integration tests
├── run_pilot.py            # Evaluation runner (18 tasks × 4 conditions × N runs)
├── run_longitudinal.py     # Longitudinal optimizer test (40+ tasks, no reset)
├── run_task.py             # Legacy CLI runner
├── analyze_results.py      # Statistical analysis (CIs, Fisher's exact)
├── backfill_token_costs.py # LangFuse → DuckDB token cost extraction
├── analyze_context_utilization.py  # Context window utilization from LangFuse
├── test_pipeline.py        # Integration test: full pipeline (Pillar 1)
├── test_verification.py    # Integration test: three-tier gate (Pillar 2)
├── test_split_merge.py     # Integration test: optimizer operations (Pillar 3)
├── test_auto_optimizer.py  # Integration test: CLI auto-optimizer
└── test_mint.py            # MINT CLI smoke test (all types + formats)

tests/
├── unit/               # 814+ tests, no I/O, no external services
├── integration/        # Docker, ChromaDB, DuckDB, MCP servers
└── evaluation/         # Harness, tasks, snapshots, fixtures
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

Import `DEFAULT_LLM_MODEL` from `src.config` and use `init_chat_model(DEFAULT_LLM_MODEL)` for all LLM-calling components. Never hardcode `"openai:gpt-5-mini"` directly — use the config constant so the model can be changed in one place (or via `JIT_LLM_MODEL` env var).

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
| `recursion_limit` (decomposition) | 50 | ~25 logical steps; typical usage is 2-3 steps |
| `recursion_limit` (adversarial probing) | 50 | Typical usage is 3-5 steps; timeout (300s) is the binding constraint, not step count |
| `recursion_limit` (failure analysis) | 25 | Focused investigation |
| Node-level timeout (decomposition) | 120s | Prevents pipeline hang |
| Node-level timeout (adversarial) | 300s | Allows sandbox execution |
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

### Dynamic Sandbox Dependencies

The sandbox resolves dependencies dynamically at runtime — no hardcoded package lists. The Dockerfile installs only `mcp` and `pydantic` (required by the FastMCP tool pattern). All other packages are resolved via two methods:

**Primary (generator-declared):** The MCP tool generator includes a `pip_packages` field in its structured output (`GeneratedToolCode.pip_packages`). The LLM declares exact PyPI names (e.g., `pyyaml` not `yaml`) at generation time. These are stored on `GeneratedArtifact.pip_packages` and used directly at execution — no import resolution needed.

**Fallback (LLM-based resolution):** For old artifacts or non-generator code paths (behavioral testing, adversarial probing):

1. `extract_imports()` — AST-based import extraction from generated source
2. `sys.stdlib_module_names` — filters out stdlib (no hardcoded list)
3. `_resolve_imports_via_llm()` — asks the LLM to map import names to PyPI names; cached per session
4. On LLM failure, falls back to identity mapping (import name = pip name)

**Execution:** Two-phase Docker execution: Phase 1 runs `pip install` with network access and commits to an ephemeral image; Phase 2 runs code with `--network none`. Binary files (xlsx, parquet) are transferred into the sandbox via base64 encoding. The ephemeral image is cleaned up after execution.

## Current State (April 2026)

**All iterations (0–5) are complete.** Current phase: **Iteration 6 — Evaluation & Analysis.**

All core infrastructure is built and tested (814 unit tests pass). The MINT CLI (`uv run mint`) is implemented. A comprehensive code audit on 2026-04-06 resolved 33 internal contradictions. **All prior evaluation data is stale** — a full re-run (18 tasks × 4 conditions × 5 runs = 360 records) is required before thesis figures can be produced.

### What was built per iteration

- **Iter 0**: Pipeline skeleton, sandbox Dockerfile, fibonacci reference tool
- **Iter 1**: 18 Pydantic schemas, ChromaDB registry, DuckDB metrics
- **Iter 2**: Full 9-node generator pipeline (sequential chaining, few-shot + repair)
- **Iter 3**: Three-tier verification gate, `__name__ = "not_main"` sandbox fix, 18 bypass findings
- **Iter 4**: Granularity optimizer (utility function, prune/split/merge, optimization loop, simulation)
- **Iter 5**: Performance fixes (Fix 0–7), SKILL/AGENT execution, evaluation harness, MINT CLI

### Evaluation testbed

18 tasks (T01–T18), 4 baseline conditions (static, no_verification, no_optimization, full), 5 runs each. Balanced: 8 MCP_TOOL (3 retrieval + 5 synthesis), 5 SKILL, 5 AGENT. 33 fixture files across 7 formats (CSV, JSON, TXT, YAML, XML, Excel, TSV). See `tests/evaluation/tasks.py` for definitions and `docs/system_overview.md` for full evaluation design.

### Key findings (pre-audit, directional only)

- JIT synthesis ~73% vs static ~14% on synthesis tasks (p<0.001)
- Verification hurts success by ~9pp due to false positives, adds ~2.3x tokens
- Optimizer cold-start in snapshot-reset evaluation, but converges to 100% in longitudinal test (40 tasks)
- Type selection critical for complex tasks (T17: AGENT 100% vs SKILL 9%)

All numbers pending confirmation after full re-run with post-audit code.

### What remains

- [ ] Run additional code audits and contradiction checks (catch issues before burning $5+ on a full eval run)
- [ ] Purge stale evaluation data
- [ ] Full evaluation re-run (18 × 4 × 5 = 360 records)
- [ ] Re-validate longitudinal optimizer test
- [ ] Produce thesis figures and analysis

## Stable Interfaces

The following modules are implemented and tested. Import from them; avoid modifying unless explicitly required:

- **`src/optimizer/utility.py`** — `compute_utility(capability_id, metrics_store, alpha, beta, gamma) -> float`, `compute_all_utilities(registry, metrics_store, ...) -> dict[str, float]`
- **`src/optimizer/operations.py`** — `prune(registry, metrics_store, window_days) -> list[str]`, `split(registry, metrics_store, capability_id, llm_model) -> list[str]` (SKILL/AGENT only), `merge(registry, metrics_store, capability_ids, llm_model) -> str` (SKILL only)
- **`src/optimizer/loop.py`** — `create_optimization_loop(registry, metrics_store, ...) -> CompiledStateGraph`, `OptimizerState`
- **`src/optimizer/simulation.py`** — `SimConfig`, `SimResult`, `generate_synthetic_registry()`, `run_simulation(config, with_optimization) -> list[SimResult]`, `plot_results()`, `compare_baselines()`
- **`src/config.py`** — Centralized constants: `DEFAULT_LLM_MODEL` (env: `JIT_LLM_MODEL`), `SANDBOX_IMAGE` (env: `JIT_SANDBOX_IMAGE`), `DOCKER_PREINSTALLED`, `FILE_SIZE_LIMIT_BYTES`, `BINARY_EXTENSIONS`, `AUTO_OPTIMIZER_THRESHOLD`
- **`src/schemas/`** — 18 core types (`CapabilityType`, `CapabilityDescriptor`, `TaskState` (now includes `attempt_count: int = 0`), `TaskStatus`, `SubTask`, `SubTaskItem`, `DecompositionResult`, `ClassificationResult`, `ClassifiedGap`, `GeneratedArtifact` (now includes `pip_packages: list[str]`), `GeneratorInput`, `GapItem`, `MatchItem`, `Severity`, `VerificationFinding`, `VerificationReport`, `InvocationRecord`, `GranularityMetrics`) + 5 evaluation types (`BaselineCondition`, `EvaluationRecord`, `FailureReason`, `HumanLabel`, `TaskTier`)
- **`src/orchestrator/decomposition.py`** — `decomposition_node()` (DeepAgent-backed task decomposition)
- **`src/orchestrator/classifier.py`** — `classify_gap()`, `classifier_node()` (LLM-based classification)
- **`src/orchestrator/prompts/`** — All prompt constants for decomposition, classification, and all three generators
- **`src/orchestrator/gap_detection.py`** — `gap_detection_node(state, registry, metrics_store=None)` (detects registry gaps, records match invocations, always routes to `"classifier_node"`)
- **`src/orchestrator/loading_node.py`** — `loading_node(state, loader, registry)` (loads MCP_TOOL via importlib, registers all 3 types in ChromaDB with source_code/skill_content/agent_config metadata, sets terminal status)
- **`src/orchestrator/pipeline.py`** — `create_pipeline(registry, loader, *, run_optimizer=False, metrics_store=None, verification_enabled=True)` (9-node pipeline; real three-tier gate wired via `gate_fn=gate_verify` closure; `verification_enabled=False` bypasses gate for no_verification baseline; optional `optimizer_node` appended after `loading_node` when `run_optimizer=True`)
- **`src/orchestrator/verification_node.py`** — `verification_node(state, gate_fn=None)` (retry + artifact replacement logic; real gate wired in pipeline.py)
- **`src/orchestrator/generators/`** — `generate_mcp_tool()`, `generate_skill()`, `generate_agent()` (LangGraph nodes; chained sequentially to `verification_node`)
- **`src/verification/static_scanner.py`** — `scan(source_code, capability_name, capability_type) -> VerificationReport`, `should_proceed_to_tier2(report) -> bool`
- **`src/verification/behavioral.py`** — `run(artifact) -> list[VerificationFinding]`, `extract_parameter_schema(source_code) -> dict`
- **`src/verification/adversarial.py`** — `probe(artifact) -> VerificationReport` (DeepAgent, never raises), `AdversarialFinding`, `AdversarialFindings`
- **`src/verification/gate.py`** — `verify(artifact) -> VerificationReport` (tiered cost gate, wired as `gate_fn` in pipeline)
- **`src/verification/sandbox_tool.py`** — `sandbox_execute(code, timeout=10, extra_packages=None) -> dict` (Docker isolation with dynamic dependency resolution; use for all adversarial/behavioral probing), `extract_imports(source_code) -> set[str]`, `resolve_extra_packages(source_code) -> list[str]`
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
- **`scripts/run_pilot.py`** — Evaluation runner: `uv run scripts/run_pilot.py [--condition] [--all-tasks] [--all-conditions] [--n-runs N]`
- **`scripts/test_mint.py`** — MINT CLI smoke test: `uv run scripts/test_mint.py [--quick] [--mcp-only] [--skill-only] [--agent-only]`. Tests all 3 capability types + 6 file formats (CSV, JSON, YAML, XML, Excel, TSV). Logs to `data/logs/`.
- **`src/cli/app.py`** — MINT CLI entry point (Typer): `run`, `plan`, `retry`, `list`, `inspect`, `history`, `prune`, `seed`, `reset`, `disable`, `enable`
- **`src/cli/runner.py`** — Pipeline execution with streaming progress, file metadata augmentation, AST-based parameter matching, auto-save output to `data/mint_output/` (timestamped), `--output-dir` override
- **`src/cli/display.py`** — Rich formatting (progress table, verification findings, capability list/inspect/history, output quality detection)
- **`scripts/run_task.py`** — User-facing CLI: `uv run scripts/run_task.py "task" [--file PATH] [--param K=V] [--dry-run] [--list] [--skip-verification] [--enable-optimizer] [--no-optimizer] [--timeout SECONDS] [--output-format json|human]`. Optimizer auto-enables after 20+ invocations. Persists capabilities (ChromaDB) and invocation metrics (`data/cli_metrics.duckdb`) across runs.

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
- **DeepAgents skill spec**: See `tools/quick_validate.py` and `tools/init_skill.py` for format rules
- **uv docs**: https://docs.astral.sh/uv/
- **MCP specification**: https://modelcontextprotocol.io/
- **Agent Skills spec**: https://agentskills.io/
- **LangFuse docs**: https://langfuse.com/docs

---

**Last updated:** April 2026
