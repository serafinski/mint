# MINT — Model-driven Instantiation of Novel Tools

**Master's Thesis Project — February 2026**

## Overview

Just-in-time capability synthesis in multi-agent systems. The system generates, verifies, and optimizes its own capabilities on-demand. User-facing CLI: `uv run mint`.

**Research question:** How to balance autonomy vs correctness, specificity vs generality, and context economy in self-developing multi-agent systems?

**Active work:** See `docs/MASTER_PLAN_2026_04_13.md` for the current fix list (single source of truth for Phase 2 onwards). Thesis contract is `docs/thesis_proposal.md`.

## Proposed Architecture (Three Pillars)

1. **JIT Capability Pipeline** — Generate capabilities on-demand (MCP tools, agent skills, or new agents)
2. **Verification Layer** — Three-tier verification to catch LLM generation failures
3. **Granularity Optimizer** — Evolutionary feedback loop to optimize skill granularity

## Tech Stack

- **LangGraph** — Multi-agent orchestration and pipeline graphs
- **DeepAgents** (`deepagents` package) — Agent harness with built-in planning (`write_todos`), file system context management, subagent spawning. Used for task decomposition (Pillar 1) and adversarial probing (Pillar 2).
- **LLM Provider** — Provider-agnostic via `init_chat_model("provider:model")`.
  - `openai:gpt-5-mini` — unified default for all components, centralized in `src/config.py` as `DEFAULT_LLM_MODEL`. Override via `JIT_LLM_MODEL` env var.
  - Requires `OPENAI_API_KEY` in `.env`
- **MCP SDK** — Tool serving (Python, FastMCP pattern)
- **ChromaDB** — Skill registry (semantic search with hierarchical embeddings)
- **DuckDB** — Metrics storage (invocations, outcomes, token costs)
- **LangFuse** — Observability and tracing
- **Docker** — Sandboxed execution for generated capabilities
- **uv** — Python package management

### Where DeepAgents vs. Plain LangGraph Nodes

DeepAgents (`create_deep_agent()`) return a `CompiledStateGraph` — a complete agent with built-in planning, file system tools, subagent delegation, and context management. They are NOT LangGraph nodes. To use them in the pipeline, invoke them from within a node function.

| Component | Implementation | Why |
|---|---|---|
| Task decomposition | **DeepAgent** (invoked from node) | Multi-step planning with `write_todos`, file-based context |
| Capability classifier | Plain LangGraph node | Per-gap LLM classification with structured output (`temperature=0`) |
| MCP tool / Skill / Agent generators | Plain LangGraph nodes | Single-pass structured output with retry on failure |
| Tier 1 static scanner | Plain Python (no LLM) | AST + regex, deterministic |
| Tier 1.5 import probe | Plain Python + Docker subprocess (no LLM) | Dry-run import / fence compile; cost-optimization, not a proposal tier |
| Tier 2 behavioral testing | Plain LangGraph node | Test input generation + execution |
| Tier 3 adversarial probing | **DeepAgent** (invoked from node) | Multi-step attacks: plan → execute in sandbox → iterate |
| Failure analysis (optimizer) | **DeepAgent** (optional) | Multi-step investigation of why capabilities fail |
| Granularity operations | Plain LangGraph node | Metrics-driven, mostly deterministic |

All LLM-calling components default to `openai:gpt-5-mini` via `DEFAULT_LLM_MODEL`.

### DeepAgents Skill Ecosystem

Generated skills (Pillar 1) target the DeepAgents runtime skill format:

- **Skill structure**: `SKILL.md` (required) + optional `scripts/`, `references/`, `assets/` directories
- **Skill locations**: `data/eval/skills/` (evaluation) or `data/cli/skills/` (CLI), configured via `set_skills_output_dir()`
- **Frontmatter**: YAML with `name` and `description` (required). Allowed keys: `name`, `description`, `license`, `compatibility`, `allowed-tools`, `metadata`
- **Validation**: `tools/quick_validate.py` validates structure; `tools/init_skill.py` scaffolds new skills
- **Naming**: hyphen-case, max 64 chars. Description max 1024 chars, no angle brackets.

The verification gate runs `quick_validate.py` as a Tier 0 structural pre-check before the three-tier semantic verification.

## Directory Structure

```
src/
├── config.py               # Centralized constants (model, sandbox, thresholds)
├── cli/                    # MINT CLI (Typer + Rich): app.py, runner.py, display.py
├── orchestrator/           # Pillar 1: pipeline orchestration (LangGraph)
│   ├── pipeline.py         # Main LangGraph pipeline graph
│   ├── decomposition.py    # Task decomposition (DeepAgent-backed)
│   ├── classifier.py       # Per-gap capability classification
│   ├── gap_detection.py    # Skill gap detection
│   ├── loading_node.py     # Dynamic loading + registration (all 3 types)
│   ├── verification_node.py  # Retry loop orchestration
│   ├── tool_loader.py      # Dynamic MCP tool loading (importlib)
│   ├── composition.py      # Compositional chain executor (Phase 3)
│   ├── schema_extraction.py  # AST-based schema extraction (B1)
│   ├── prompts/            # All LLM prompt templates
│   └── generators/         # mcp_tool.py, skill.py, agent.py
├── registry/               # ChromaDB skill registry + DuckDB metrics
├── verification/           # Pillar 2: static_scanner, behavioral, adversarial, gate, sandbox_tool
├── optimizer/              # Pillar 3: utility, operations, loop, simulation
├── schemas/                # Shared Pydantic state schemas (single source of truth)
└── mcp_tools/              # Reference hand-built MCP tools (fibonacci)

tests/    unit/ (1000+ tests), integration/ (Docker-required), evaluation/ (harness + tasks + fixtures)
scripts/  run_pilot, run_longitudinal, analyze_results, backfill_token_costs, test_mint,
          test_pipeline, test_verification, test_auto_optimizer, test_split_merge, test_registry_matching
tools/    quick_validate.py, init_skill.py (DeepAgents skill utilities)
sandbox/  Dockerfile (mcp + pydantic base image)
```

## Skills

The following skills in `.claude/skills/` must be followed:

- **pydantic-schemas** — All inter-node state must use Pydantic models from `src/schemas/`. Never raw dicts.
- **testing-conventions** — pytest conventions, test directory structure.
- **langfuse-tracing** — LangFuse integration patterns (grouped traces, custom trace IDs, scoring).
- **langgraph-architect** — LangGraph patterns: Command API, Send, HITL, persistence, streaming.
- **langgraph-docs** — Fetches live LangGraph docs when langgraph-architect doesn't cover.
- **deep-agents** — When and how to use DeepAgents vs plain LangGraph nodes.
- **mcp-builder** — MCP server development guide (FastMCP for Python).
- **verification-patterns** — AST scanning, behavioral testing, adversarial probing patterns.

## Package Management

This project uses **uv**. Common commands:

```bash
uv add <package>       # Add dependency
uv sync                # Sync .venv to pyproject.toml
uv run <script.py>     # Run script in project env
uv run python -m pytest
```

## Observability with LangFuse

LangFuse traces LLM calls via `langfuse.langchain.CallbackHandler` passed as `config={"callbacks": [langfuse_handler]}`. Required env vars: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`. See `langfuse-tracing` skill for full patterns.

## Development Guidelines

**State Management.** Use Pydantic BaseModel for all inter-node state (see `pydantic-schemas` skill). Never pass raw dicts between nodes.

**LangGraph Patterns.** Use `Command(update={...}, goto="next_node")` for routing. Use `Send` for parallel fan-out. Use `interrupt()` for HITL. See `langgraph-architect` skill.

**LLM Initialization.** Import `DEFAULT_LLM_MODEL` from `src.config` and use `init_chat_model(DEFAULT_LLM_MODEL)`. Never hardcode `"openai:gpt-5-mini"` — use the config constant so the model can be changed in one place (or via `JIT_LLM_MODEL`).

**DeepAgents Integration.** Use DeepAgents only where multi-step autonomous reasoning adds value (see table above). `create_deep_agent()` returns a `CompiledStateGraph` — invoke it, don't wire it as a node. Use `response_format=YourPydanticModel` for structured output. Instruct agents to use files for intermediate results (context management). See `deep-agents` skill.

**Verification First.** Any generated capability must pass the three-tier verification gate before being registered. Skills also run `tools/quick_validate.py` as a structural pre-check. The verification layer is the core thesis contribution.

**Track Metrics.** Record which capabilities get generated, which pass/fail verification (and why), how often capabilities are reused, token costs per pipeline stage. This feeds the granularity optimizer and thesis evaluation.

## Architectural Constraints

### Testbed

The evaluation testbed is the **Business Analytics Pipeline** — data ingestion, transformation, ML training, inference, and report generation tasks. 20 tasks (T01–T20), 4 baseline conditions (`static`, `no_verification`, `no_optimization`, `full`), 5 runs each. Balanced spread across MCP_TOOL / SKILL / AGENT types. Chosen over Factory Simulation because it naturally produces diverse capability types. Task definitions in `tests/evaluation/tasks.py`.

### DeepAgent Operational Boundaries

| Constraint | Value |
|---|---|
| `recursion_limit` (decomposition / adversarial) | 50 (~25 logical steps) |
| `recursion_limit` (failure analysis) | 25 |
| Node-level timeout (decomposition) | 120s |
| Node-level timeout (adversarial) | 480s (Step 14.11, fail-closed with HIGH severity → retry) |
| Initialization | Fresh every invocation (no carried state) |
| Output contract | `response_format=PydanticModel` |
| Fallback on timeout/max_steps | Return partial result with error flag; pipeline continues |

### MCP Tool Loading Strategy

Generated MCP tools are loaded via `importlib` dynamic import in a sandboxed subprocess (`src/orchestrator/tool_loader.py`). **Not** persistent FastMCP server processes — no ports, health checks, or zombie process risk. Full MCP server deployment is Chapter 7 future work.

### Generated Skills Landing Path

Generated SKILL.md files are written to `data/eval/skills/<name>/` (evaluation, snapshot-controlled) or `data/cli/skills/<name>/` (CLI, accumulates). Path set by `set_skills_output_dir()` in `src/orchestrator/generators/skill.py`. `src/skills/` does not exist and should not be used.

### Generated Agent Tool Scope

Generated agents execute as DeepAgents with `tools=[sandbox_execute]` plus built-in file/planning tools. The built-in `execute` (shell) tool is **non-functional by design** — `sandbox_execute` provides Docker-isolated execution instead.

**Lead-with-subagents (Phase 3.5b):** When ≥2 gaps are classified as AGENT, the generator produces ONE lead agent + N subagents. Each subagent runs in an **isolated context** with its own tool set. The lead coordinates via `task(name=..., task=...)` and receives only the subagent's final result, not its intermediate reasoning. Subagent configs stored in `agent_config["subagents"]` and `agent_config["subagent_gap_queries"]`.

Generated agents cannot compose with other registry capabilities (e.g., use a registry MCP tool as a tool) — cross-registry composition is Chapter 7 future work.

### Three Distinct Execution Backends

Each capability type executes via a genuinely different backend, making the classifier's routing decision have measurable runtime consequences:

| Type | Backend | LLM at runtime | Character |
|---|---|---|---|
| **MCP_TOOL** | `sandbox_execute()` — Docker, no LLM | No (0 calls) | Deterministic code execution |
| **SKILL** | Constrained LLM with `bind_tools([sandbox_execute])`, max 3 tool calls | Yes (1-3 calls) | Prescribed workflow |
| **AGENT** | Full DeepAgent with `skills=` parameter (progressive disclosure) | Yes (5-15+ calls) | Adaptive reasoning, planning loop |

**Critical filesystem isolation:** Built-in DeepAgent tools (`write_file`, `read_file`, etc.) operate on the agent's in-memory workspace (`StateBackend`), NOT on the Docker sandbox filesystem. Files written with `write_file` are NOT accessible inside `sandbox_execute`. All generator prompts document this explicitly.

**Structured State Communication is enforced at three granularities** (§3.4 of thesis proposal). Inter-stage handoffs use strictly typed schemas, never free-form LLM output:

1. **Task level** (Step 14.6): `OutputContract.check()` at chain completion validates final output against declared `required_keys` + `required_files`, CLI exit code 3 on mismatch.
2. **Subtask level** (§12 finding #12, Layer 2): `CompositionalExecutor` validates AGENT/SKILL step outputs against the decomposer's per-gap `output_schema` at runtime; fails chain immediately with a descriptive `failed_step_reason` on missing keys. MCP_TOOL output is deterministic so this check skips MCP_TOOL.
3. **Generation level** (§12 finding #12, Layer 1): `execute_agent_artifact` builds a dynamic Pydantic `response_format` from the decomposer's `output_schema` (via `_build_dynamic_result_model` in `tests/evaluation/execution.py`) when the CLI path would otherwise fall through to `GenericResult`. Forces the LLM's structured-output call to produce exactly the declared keys.

**File outputs are MCP_TOOL last steps (decomposer Rule 15 + classifier file-writer exception).** Only MCP_TOOL can write to the Docker workspace where `_collect_output_files` picks files up. When a task's authored `OutputContract.required_files` is non-empty:

1. **Decomposer** is instructed (prompt Rule 15) to append a deterministic file-writer subtask as the LAST step. A post-decomposition validator (`_ensure_file_writer_last_step` in `src/orchestrator/decomposition.py`) enforces this structurally as a safety net.
2. **Classifier** has a file-writer exception (prompt carve-out + Example G) that routes `write-*` subtasks to MCP_TOOL even when the parent task is AGENT-leaning. A post-classification override (`_enforce_file_writer_mcp_tool` in `src/orchestrator/classifier.py`) is the structural safety net — any gap whose query starts with `write-` is forced to MCP_TOOL when `output_contract.required_files` is non-empty.

The appended writer is generated by the MCP_TOOL generator, verified by the three-tier gate, and registered for reuse — e.g. `write-csv-file`, `write-xlsx-file`, `write-md-file` become first-class capabilities eligible for Pillar 3 operations. An earlier executor-level synthesis approach (Option A) was implemented and reverted on 2026-04-16 after a proposal-alignment review — see `docs/file_output_fix_plan.md` §Resolution and master plan §12 findings #10 (Option A → Option B pivot) + #11 (classifier parent-task bias drift) for the full methodology nuggets.

**Data path separation:**
- Evaluation: `data/eval/chroma/`, `data/eval/metrics.duckdb`, `data/eval/skills/`, `data/eval/longitudinal.duckdb`
- CLI: `data/cli/chroma/`, `data/cli/metrics.duckdb`, `data/cli/skills/`

### Verification Gate (Tiered Cost Optimization)

The gate uses fail-fast tiering to avoid wasting tokens:

1. **Tier 0** (Skills only): `tools/quick_validate.py` structural check (~0 cost)
2. **Tier 1**: Static scanner — AST + regex on Python source (~0 cost). Critical findings → reject immediately, skip Tier 1.5/2/3.
3. **Tier 1.5** (implementation-layer cost-optimization, NOT a proposal-named tier — see §Step 12.5 in `docs/MASTER_PLAN_2026_04_13.md`): Sandbox import probe (~5-10 s Docker for MCP_TOOL, ~0 cost for SKILL/AGENT). MCP_TOOL runs `python -c "import tool"` in Docker with `pip_packages` installed, catching wrong PyPI names + runtime import errors before Tier 2/3. SKILL/AGENT run `compile()` on each embedded python fence, catching syntax errors that `ast.parse` masks (unterminated strings, mismatched brackets). Critical findings → reject, skip Tier 2/3. Proposal-alignment note: the failure modes Tier 1.5 catches (`wrong_pip_package` / `import_error` / `fence_compile_error`) are operational-correctness issues NOT in the proposal's §Pillar 2 LLM-failure-mode taxonomy; without Tier 1.5, Tier 2 still catches them via `ModuleNotFoundError`, just more expensively. Tier 1.5 moves detection from the Tier 2/3 path to the Docker-import path — same coverage, cheaper. Chapter 4.5 must disclose this framing.
4. **Tier 2**: Behavioral testing — edge-case probing (moderate cost). Only runs if Tier 1 + Tier 1.5 pass or have High/Medium only.
5. **Tier 3**: Adversarial probing — DeepAgent red-team (high cost). Only runs if Tier 2 passes.

**Tier 1 scans Python source wherever it appears.** `static_scanner.scan()` runs AST + regex only on MCP_TOOL Python (prose-based SKILL/AGENT content false-flagged on words like "mock" and "placeholder" — T04 reproducer, 2026-04-08). However, `gate.py _static_scan()` wraps the scanner with a **layered code-fence extractor** (`_extract_python_fences`): for SKILL and AGENT artifacts, every ```python ... ``` block inside `skill_content` / `system_prompt` / embedded `skill_content` is extracted and fed back through the full MCP_TOOL AST + regex scan. Only the prose around code is skipped. Full methodology note in `docs/proposal_vs_implementation_v2.md`.

**Three-layer strict-gate pattern (§12 findings #14, #15, #16, 2026-04-16).** Every proposal-critical LLM-dependent stage follows a uniform retry + sticky-flag + dedicated exit code pattern. No silent fallback paths anywhere in the pipeline:

| Layer | Code | Sticky flag | Max attempts | CLI exit |
|---|---|---|---|---|
| Pillar 1.1 decomposition | `src/orchestrator/decomposition.py` | `decomposition_succeeded` | `MAX_DECOMPOSITION_ATTEMPTS=3` | 2 |
| Pillar 1.3 classification | `src/orchestrator/classifier.py::classify_gap` | `classification_succeeded` | `MAX_CLASSIFICATION_ATTEMPTS=3` | 5 |
| Pillar 2 verification | `src/orchestrator/verification_node.py` | `verification_succeeded` | `MAX_ATTEMPTS=3` | 4 |

Each stage retries the LLM call up to N times. On exhaustion: flip the sticky flag, emit an ERROR-level LangFuse event, drop any partially-produced artifacts from state, allow the pipeline to continue to terminal state via the never-raises contract. The CLI runner detects the sticky flag and exits with a dedicated code so failure reasons are layer-specific. Pre-fix behaviour (single-attempt + silent fallback at decomposition; single-attempt + MCP_TOOL default at classification; 3x retry + fail-open at verification) let unverified / mis-classified / degraded artifacts through production — all three corrected to proposal-strict semantics. See master plan §12 findings #14/#15/#16 for full audit trails.

### Pillar 3: Type-Scoped Operations

| Operation | MCP Tools | Skills | Agents |
|---|---|---|---|
| **Prune** | ✅ | ✅ | ✅ |
| **Split** | ✅ | ✅ | ✅ |
| **Merge** | ✅ | ✅ | ✅ |

**Status:** Pillar 3 reached 9/9 type coverage in B1 Steps 6 + 8 (2026-04-15 / 2026-04-16 commits). Merge and split both produce real executable `GeneratedArtifact` for MCP_TOOL / SKILL / AGENT and route each variant through the shared `verify_and_register_or_rollback` helper (Step 5) with atomic rollback on any gate or coverage failure. Split additionally runs `verify_coverage` (Step 7, MCP_TOOL-only) as a post-gate check before the parent is deactivated — see next paragraph. No pre-Step-8 type guards remain; `split()` and `merge()` accept all three capability types.

**Split coverage verification (Step 7, MCP_TOOL-only scope).** Step 8 couples split to a `verify_coverage` helper (`src/verification/coverage.py`, Step 7) that replays the parent's recorded failure cases against new split variants and asserts each failure mode is fixed. Coverage verification is **MCP_TOOL-only** by design, not by infrastructure complexity: the proposal's Pillar 1 classification explicitly labels MCP_TOOL as deterministic and SKILL/AGENT as LLM-mediated (procedural/stateful), and Pillar 3 Split's "input subspaces where the capability breaks" framing presupposes stable per-input failure semantics that only deterministic executors satisfy. SKILL and AGENT splits rely on the three-tier gate per variant without collective coverage verification (same as merge for those types). See §12 pre-registered finding #5 in `docs/MASTER_PLAN_2026_04_13.md` for the Pillar 1 × Pillar 3 tension analysis and Chapter 4 disclosure text. True coverage verification for nondeterministic executors (per-variant replay statistics or LLM-judged correctness) is Chapter 7 future work.

### LLM Output Robustness

All LLM-calling nodes must implement:

1. **Few-shot prompting**: Include at least one complete valid example in every generator prompt. Don't just give the schema — give a full example of correct output.
2. **Repair pass**: If Pydantic validation fails, send raw output + validation error back to the model once. If repair also fails, return an error.
3. **LangFuse logging**: Log every malformed output, validation error, and repair attempt.

### Dynamic Sandbox Dependencies

The sandbox resolves dependencies dynamically — no hardcoded package lists. The Dockerfile installs only `mcp` and `pydantic`.

- **Primary (generator-declared):** MCP tool generator's `GeneratedToolCode.pip_packages` carries exact PyPI names (e.g., `pyyaml` not `yaml`), populated at generation time and used directly at execution.
- **Fallback (LLM-based):** For old artifacts or non-generator code paths, `extract_imports()` + LLM-based pip-name resolution (cached per session), with identity-mapping fallback on LLM failure.
- **Execution:** Two-phase Docker — Phase 1 runs `pip install` with network access; Phase 2 runs code with `--network none`. Binary files (xlsx, parquet) transferred via base64 encoding.

## Current State

**Iteration 6 — Evaluation & Analysis.** Phase 2 **nearly complete** as of 2026-04-17 end of session: B1 Pillar 3 main block (Steps 4-8), pre-B1 fixes (14.7 / 13 / 14.11), §12 findings #8/#6b/#10-#17, Steps 9/9.5/10/11 all landed. Step 9.5b closeout shipped 2026-04-17 (`test_auto_optimizer.py` pipeline-wiring rewrite + `test_split_merge.py` extended with 3 new integration tests — MCP_TOOL merge + AGENT split + AGENT merge — closing the Pillar 3 matrix to 9/9 at the LLM-integration layer). **Step 18.5 shipped 2026-04-17** (commit `ad4908e`): Gini coefficient of capability invocation counts (proposal §Metrics, fully proposal-aligned via new `invocation_events` persistent table + harness flush wiring) + Core-Work Classification Accuracy (operational proxy for Pillar 1 RQ #1, task-level primary-type match with explicit four-reason framing). **Step 18.7 shipped 2026-04-17** (full proposal-aligned): new `scripts/analyze_longitudinal.py` + `cycle_utility_scores` + `cycle_events` persistent tables + harness flush hooks. Uses utility-score KL (proposal-literal, mirrors `src.optimizer.loop._compute_kl_divergence`) as primary convergence metric; reports true per-type op counts + seeded-vs-evolved Gini directly answering Pillar 3 RQ #1. `BaselineCondition.LONGITUDINAL` enum fix (latent bug). 26 new unit tests. **Step 18.8 shipped 2026-04-17** (distribution-shift scripts): new `scripts/run_longitudinal_shift.py` (phased runner with `--phase-a-tasks` / `--phase-b-tasks` / `--shift-at` / `--total`), `tests/evaluation/cycle_flush.py` helper factoring per-task flush logic out of the harness + wiring `run_longitudinal.py` to use it (fixes a latent gap where longitudinal bypassed Step 18.7 flushes). `analyze_longitudinal.py` extended with shift-detection + Phase A/B re-convergence analysis + three-tier adaptation verdict ("graceful" / "sluggish" / "collapse"). 10 new unit tests. Answers proposal Pillar 3 RQ #3 ("distribution-shift stability") — moves it out of Chapter 7 future work. **Step 14 behavioral-audit test augmentations shipped 2026-04-17 (commit `1bae0da`):** 8 new tests across 5 files closing all three gaps from `docs/test_suite_behavioral_audit_2026_04_13.md` §"Before Phase 4 smoke tests". Gap A (`test_behavioral.py::test_main_guard_neutralized_end_to_end_subprocess`) runs `_build_sandbox_code` output through a real Python subprocess and asserts `__name__ = "not_main"` blocks `mcp.run()` at runtime — guards Pillar 2 Tier 2 integrity against silent-no-op regressions. Gap B (`test_composition.py::test_per_type_fixture_routing_mcp_vs_skill`) asserts MCP_TOOL steps receive a `workspace_path: Path` kwarg with NO `fixture_contents` dict, while SKILL still gets the dict — guards the three-backend architectural invariant. Gap C (`test_schema_context_reaches_prompt` + empty-context companion in each of the three generator test files, 6 tests) patches `_get_llm`, builds a SubTask pair with distinctive `output_schema` keys, and asserts `build_schema_context(gap, subtasks)` output lands in the captured LLM prompt — guards Pillar 1 Structured State Communication (§3.4) at generation granularity. **Step 12.5 Tier 1.5 sandbox import probe shipped 2026-04-17 (commit `3260cb0`):** new `src/verification/import_check.py` (~200 LOC) + gate wiring + 25 unit tests + existing 24 gate tests retrofitted with an autouse stub fixture to stay Docker-independent. MCP_TOOL runs Docker `python -c "import tool"` with `pip_packages`; SKILL/AGENT run in-process `compile()` on embedded python fences; AGENT also validates required `agent_config.system_prompt`. Finding categories `wrong_pip_package` / `import_error` / `fence_compile_error` / `import_check_structural` / `import_check_skipped` / `import_check_error`. Integration-validated via `scripts/test_verification.py` on all four good/bad × MCP_TOOL/SKILL cases. **Proposal-alignment disclosure required for Chapter 4.5**: Tier 1.5 is a cost-optimization, NOT a fourth methodological tier — the proposal §Pillar 2 names exactly three tiers, and the failure modes Tier 1.5 catches (operational-correctness: wrong pip names, import errors, fence compile errors) are NOT in the proposal's LLM-failure-mode taxonomy. Without Tier 1.5, Tier 2 still catches these via `ModuleNotFoundError` — more expensively. Full disclosure framing in `docs/MASTER_PLAN_2026_04_13.md` Step 12.5. Remaining Phase 2: Step 12 audit. Remaining Phase 3: Step 13.5 (test hygiene, optional). Then Phase 4 smoke tests + Phase 5 eval (~18h unattended). See `docs/MASTER_PLAN_2026_04_13.md` for the live fix list and `MEMORY.md` for the verified session state. For the live test count, run `uv run pytest tests/unit/ -q` (1406 passing at 2026-04-17 end of next-session slice).

## Stable Interfaces

Import from these; avoid modifying unless explicitly required. These are the modules an agent is most likely to need:

- **`src/schemas/`** — Pydantic state schemas. Every inter-node state transition uses one. Includes `OutputContract` + `ContractCheckResult` (Step 14.6 — task-level output shape verification, carried on `TaskState.output_contract`).
- **`src/orchestrator/pipeline.py`** — `create_pipeline(registry, loader, *, run_optimizer=False, metrics_store=None, verification_enabled=True)` — 9-node pipeline with real three-tier gate.
- **`src/orchestrator/generators/`** — `generate_mcp_tool()`, `generate_skill()`, `generate_agent()` — LangGraph nodes chained to `verification_node`.
- **`src/orchestrator/classifier.py`** — `classify_gap(gap, task_description, required_capabilities)` is primary (per-gap); `classifier_node()` iterates `state.gaps`. `classify_task()` is a legacy wrapper preserved for one integration test.
- **`src/verification/gate.py`** — `verify(artifact) -> VerificationReport` — never raises; fail-open on gate errors. Verifies generated capabilities *as artifacts*; complemented by `src/orchestrator/output_contract_resolver.py::resolve_output_contract` (Step 14.6) which verifies *task outcomes* at the system boundary — orthogonal axes, both needed.
- **`src/registry/store.py`** — `SkillRegistry` (CRUD + hierarchical embedding search); `seed_registry()`.
- **`src/registry/metrics.py`** — `MetricsStore` (invocation recording + granularity metric queries).
- **`src/config.py`** — `DEFAULT_LLM_MODEL` (env: `JIT_LLM_MODEL`), `SANDBOX_IMAGE` (env: `JIT_SANDBOX_IMAGE`), `FILE_SIZE_LIMIT_BYTES`, `BINARY_EXTENSIONS`, `AUTO_OPTIMIZER_THRESHOLD`.
- **`src/cli/app.py`** — MINT CLI entry point (Typer): `run`, `plan`, `retry`, `list`, `inspect`, `history`, `prune`, `seed`, `reset`, `disable`, `enable`, `config set/show/unset` (per-operation optimizer config per §12 finding #8), `feedback --wrong` (CLI wrong-output flagging per §12 finding #6b).
- **`tests/evaluation/`** — `tasks.py` (`TaskDefinition`, `ALL_TASKS`, `PILOT_TASKS`), `harness.py` (`EvaluationHarness`), `execution.py` (`execute_skill_artifact`, `execute_agent_artifact`), `snapshots.py`.

Other stable interfaces (optimizer loop, verification tiers, tool_loader, sandbox_tool, evaluation_store, schema_extraction) can be discovered by reading the corresponding source files.

## Notes for AI Agents

1. **Use uv** — All Python package operations use `uv`, not `pip` directly.
2. **Read skills** — Check `.claude/skills/` for conventions before implementing. `langgraph-architect` has `references/` files for HITL and streaming deep dives.
3. **Use Pydantic + Command API** — Never pass raw dicts between nodes; use `Command(update={...}, goto="node")` for routing.
4. **DeepAgents where appropriate** — Only for task decomposition and adversarial probing. Invoked from nodes, not wired as nodes.
5. **Verification First** — Every generated capability must pass the three-tier gate before registration.
6. **Tests mandatory** — No module is done without tests (see `testing-conventions` skill).

## Resources

- **MCP specification**: https://modelcontextprotocol.io/
- **Agent Skills spec**: https://agentskills.io/
- **LangGraph llms.txt**: https://docs.langchain.com/llms.txt (fetched live by `langgraph-docs` skill)

---

**Last updated:** April 2026
