# MINT: Model-driven Instantiation of Novel Tools

**A System Overview and Evaluation Report**
*Updated: April 7, 2026*

---

## 1. What This Project Is

Imagine you have a multi-agent system that needs to perform a task it has never seen before. Traditionally, you would stop, write the code, test it, deploy it, and then let the system use it. This project asks: *what if the system could generate, verify, and register new capabilities on-the-fly, without human intervention?*

JustInTimeMAS is a Master's thesis prototype that implements **just-in-time capability synthesis** -- a pipeline where an AI system detects when it lacks a required capability, generates one using LLMs, verifies it through three tiers of automated testing, registers it in a skill registry, and optionally evolves the registry over time through evolutionary operations (split, merge, prune).

The system produces three types of capabilities, each corresponding to a formally specified layer of the emerging agentic AI stack:

- **MCP Tools**: Standalone Python functions following the [Model Context Protocol](https://modelcontextprotocol.io) specification (Anthropic/Linux Foundation, Nov 2025). Execution model: stateless function call -- the model decides when to invoke a tool and receives a structured result. These are executable code that can parse files, run computations, or transform data.
- **Skills**: Markdown instruction documents (SKILL.md) following the [Agent Skills](https://agentskills.io) specification (Anthropic, Dec 2025; adopted by OpenAI, Microsoft, Google, Cursor). Execution model: context injection -- the agent loads procedural knowledge into its context window and follows the instructions. Skills are recipes for multi-step workflows, not executable code.
- **Agents**: System prompts and configuration for autonomous DeepAgent instances. Execution model: autonomous planning -- you delegate a task, the agent decides how to solve it using its own planning loop (`write_todos`), tool access, and multi-step reasoning. Unlike MCP Tools and Skills, the AGENT type does not follow an external protocol specification -- it is the system's abstraction for capabilities that require adaptive, goal-directed behavior beyond what a static function or procedural instruction can provide.

The first two types follow formal open standards (MCP and Agent Skills). The AGENT type represents the autonomous planning end of the capability spectrum -- capabilities that require adaptive, multi-step reasoning that neither a single function call nor a static set of instructions can provide. Together, the three types cover distinct execution models (function call vs. instruction following vs. autonomous planning) with different control loci (model-controlled vs. context-controlled vs. task-controlled). No prior system generates all three on demand through a unified pipeline.

The research question: *How can we balance autonomy vs. correctness, specificity vs. generality, and context economy in self-developing multi-agent systems?*

---

## 2. Architecture: Three Pillars

The system is organized around three pillars, each addressing one aspect of the research question.

### Pillar 1: JIT Capability Pipeline

When a task arrives, the pipeline:

1. **Decomposes** the task into subtasks and identifies required capabilities (using a DeepAgent that plans with `write_todos`)
2. **Searches** the existing registry (ChromaDB with semantic embeddings) for capabilities that might already satisfy the requirements
3. **Classifies** any gaps -- does this gap need an MCP Tool (executable code), a Skill (multi-step instructions), or an Agent (autonomous reasoner)?
4. **Generates** the capability using GPT-5-mini with few-shot examples and a repair pass
5. **Loads** the result -- MCP Tools are dynamically imported via `importlib`, Skills are written to `.deepagents/skills/`, Agents get configuration stored in the registry

The pipeline is built as a 9-node LangGraph state machine (10 nodes when the optimizer is enabled):

```
START -> decomposition -> gap_detection -> classifier
      -> generate_mcp_tool -> generate_skill -> generate_agent
      -> verification -> loading -> [optimizer] -> END
```

All nodes communicate through a shared `TaskState` (Pydantic model with 15+ fields). Routing uses LangGraph's Command API (`Command(update={...}, goto="next_node")`).

The three generators are chained **sequentially**, not as parallel branches. Each generator only processes gaps classified for its type and passes everything else through untouched. If a task needs a SKILL, `generate_mcp_tool` does nothing, `generate_skill` generates it, and `generate_agent` does nothing. This was a deliberate simplicity choice -- parallel fan-out via LangGraph's `Send` API would require `Annotated` reducers on `TaskState`, adding schema complexity for minimal latency benefit (empty generators return instantly). Within each generator, multiple gaps of the same type *are* processed in parallel via ThreadPoolExecutor (4 workers).

### Pillar 2: Three-Tier Verification

Every generated capability must pass verification before being registered. The verification layer uses a **fail-fast, cheapest-first** strategy:

**Tier 0 (Skills only):** `tools/quick_validate.py` structural pre-check -- valid YAML frontmatter, naming rules, required fields. ~0 cost.

**Tier 1 -- Static Scanner (~0 cost):** Pure Python AST and regex analysis. Detects hardcoded return values (literal lists/dicts with 3+ items, including one level of variable-indirection), silent exception handling (`except: pass`), mock artifacts (`unittest.mock`, `MagicMock`), synthetic data indicators, and functions claiming to fetch external data without network imports. If any CRITICAL finding is detected, the capability is rejected immediately -- Tiers 2 and 3 are skipped entirely. The static scanner only runs on MCP_TOOL artifacts (SKILL is YAML, AGENT is JSON -- AST analysis is not applicable).

**Tier 2 -- Behavioral Testing (moderate cost):** Extracts the function's parameter types from the AST and generates test inputs across five categories: nonexistent entity tests (empty/invalid inputs), consistency tests (same input twice must produce same output), boundary tests (zero, negative, very large values), latency analysis, and output-variation checks (different inputs must produce different outputs -- catches tools that ignore their parameters). Also includes a parameterless smoke test for no-arg tools. All tests run inside a Docker sandbox with network disabled.

**Tier 3 -- Adversarial Probing (high cost):** A DeepAgent plans and executes multi-step attacks. It probes for three patterns: hidden demo/sample mode (does the tool return different data when asked for "sample"?), network-disconnect behavior (does it fail gracefully without network?), and ground-truth comparison (do outputs match known-correct values?). The agent uses `write_todos` for planning and executes probes in the Docker sandbox. Timeout: 300s, recursion_limit: 50.

If verification fails, the pipeline retries up to 3 times. On retry, only the failed gaps are regenerated -- passing artifacts are kept. Verification findings (CRITICAL/HIGH) are fed back into the generator prompt so the LLM can learn from the specific failure. After 3 failures, the pipeline "fails open" (loads whatever it has, logs a warning, does not hang).

The verification layer is designed to **never raise exceptions**. Every function wraps its internals in try/except and returns a report on failure rather than crashing.

### Pillar 3: Granularity Optimizer

Over time, a capability registry accumulates entries that may be too specific (rarely reused), too general (low success rate), or redundant (duplicates). The optimizer addresses this through evolutionary operations:

**Utility Function:** `U(c) = alpha * reuse(c) * success(c) - beta * token_cost(c) - gamma * maintenance(c)`

**Operations:**
- **Prune**: Deactivate capabilities with zero invocations in the last 30 days (all types). Seeded capabilities are protected with `metadata={"pinned": "true"}`.
- **Split**: Break a high-reuse but low-success capability into specialized variants (Skills and Agents only).
- **Merge**: Combine low-reuse, semantically similar capabilities into a single broader one (Skills only).

**Why operations are type-scoped:** Split and merge operate on **metadata** (names and descriptions), not on generated code. When the optimizer splits "data-file-processor" into "csv-cleaner" and "json-validator", it asks the LLM to produce two new `CapabilityDescriptor` entries with narrower descriptions. The original is deactivated. The next time a CSV task arrives, the pipeline finds "csv-cleaner" via registry search and generates fresh code for it.

This is why MCP_TOOL split/merge is deferred to future work: splitting a Python function into two working functions requires **code refactoring** (understanding the implementation, separating concerns, producing two compilable outputs). That's a harder LLM task than splitting a description into two narrower descriptions. For Skills, the SKILL.md is an instruction document -- the LLM can narrow or broaden instructions reliably. For Agents, the system prompt serves the same role.

Merge is restricted to Skills (not Agents) because merging two autonomous agents into one with a broader mandate risks creating an unfocused agent that does neither task well. Skills are instruction sets that can be combined without this risk.

| Operation | MCP_TOOL | SKILL | AGENT | Operates on |
|---|---|---|---|---|
| Prune | Yes | Yes | Yes | Registry entry (type-agnostic) |
| Split | No | Yes | Yes | Description -> 2+ narrower descriptions |
| Merge | No | Yes | No | 2 descriptions -> 1 broader description |

**Convergence:** The optimizer tracks KL divergence between successive utility score distributions. When the distribution stabilizes (divergence < threshold), it stops iterating.

The optimizer runs as an optional node appended after loading. It shares a MetricsStore with the rest of the pipeline so it can see invocation history from earlier tasks in the same run. In CLI mode (`uv run mint`), the optimizer **auto-enables** after 20+ invocations (configurable via `AUTO_OPTIMIZER_THRESHOLD` in `src/config.py`) -- no flag needed.

---

## 3. Tech Stack

| Component | Technology | Purpose |
|---|---|---|
| Pipeline orchestration | LangGraph | State machine with Command API routing |
| Multi-step agents | DeepAgents (`deepagents` package) | Task decomposition, adversarial probing, SKILL/AGENT execution |
| LLM | `openai:gpt-5-mini` (unified) | All LLM-calling components |
| CLI | Typer + Rich | User-facing `mint` command with progress display |
| Skill registry | ChromaDB | Semantic search with hierarchical embeddings |
| Metrics storage | DuckDB | Invocation recording, evaluation records |
| Sandbox execution | Docker | Network-isolated code execution for verification |
| Observability | LangFuse | LLM call tracing, token usage tracking, per-task attribution |
| Package management | uv | Python dependency management |
| State schemas | Pydantic | 18+ typed models for all inter-node state |
| Configuration | `src/config.py` | Single source of truth for model, timeouts, thresholds |

The system was originally hybrid (DeepSeek for plain nodes, GPT-5-mini for DeepAgent components) but was unified to `openai:gpt-5-mini` in March 2026 to eliminate the mixed-model confound in evaluation.

---

## 4. The MINT CLI

MINT (**M**odel-driven **I**nstantiation of **N**ovel **T**ools) is the user-facing command-line interface. It wraps the full JIT pipeline with Rich progress display, file metadata augmentation, and output quality detection.

**Entry point:** `uv run mint` (registered via `pyproject.toml`). Built with Typer + Rich across three modules:

- **`src/cli/app.py`** (428 lines) -- Typer command definitions: `run`, `plan`, `retry`, `list`, `inspect`, `history`, `prune`, `seed`, `reset`, `disable`, `enable`
- **`src/cli/runner.py`** (1,319 lines) -- Pipeline execution with streaming progress, file metadata augmentation, AST-based parameter matching, auto-save to `data/mint_output/`
- **`src/cli/display.py`** (603 lines) -- Rich formatting: animated spinner, progress table, verification findings, capability list/inspect/history, output quality detection

**Key features:**
- **File format support:** CSV, JSON, YAML, XML, Excel (.xlsx), TSV, TXT. Binary files (Excel, Parquet) are base64-encoded for sandbox transfer.
- **Output quality detection:** Warns if output looks incomplete (too few keys, too small for execution time, echoes input prompt). Yellow border + retry suggestions.
- **Auto-save:** All output is saved to `data/mint_output/` with timestamped filenames. Large string values are extracted as separate files.
- **Optimizer auto-enable:** After 20+ invocations, the optimizer runs automatically. Manual override via `--enable-optimizer` / `--no-optimizer`.
- **Retry with context:** `mint retry --hint "..."` re-runs the last task with extra context. `mint retry --edit` opens `$EDITOR`.

**Data isolation:** CLI uses separate storage from the evaluation harness:
- `data/cli_chroma/` -- ChromaDB registry
- `data/cli_metrics.duckdb` -- DuckDB invocation history
- `data/last_run.json` -- last run config (for `mint retry`)

---

## 5. The Evaluation

### Design

The evaluation uses a **business analytics pipeline** testbed -- 18 tasks spanning data ingestion, transformation, quality investigation, and report generation. Tasks were chosen to naturally produce diverse capability types and range from trivial (CSV parsing) to complex (autonomous anomaly investigation).

**18 Tasks:**

| Task | Description | Type | Difficulty |
|---|---|---|---|
| T01 | Parse standard CSV | MCP_TOOL | Trivial |
| T02 | Validate JSON against schema | MCP_TOOL | Trivial |
| T03 | Compute text statistics | MCP_TOOL | Trivial |
| T04 | Inventory reorder pipeline (stock levels vs rules) | SKILL | Complex |
| T05 | Clean dirty CSV (nulls + duplicates) | MCP_TOOL | Moderate |
| T06 | KNN imputation (k=5) on dataset with ~15% missing values | MCP_TOOL | Moderate |
| T07 | Fuzzy join on mismatched company names | MCP_TOOL | Moderate |
| T08 | Data quality investigation (autonomous profiling of messy dataset) | AGENT | Complex |
| T09 | Generate analytics report with top-5 anomalies | MCP_TOOL | Complex |
| T10 | Trend detection and forecasting (weekly sales analysis) | AGENT | Complex |
| T11 | Multi-source data integration (3 sources, revenue summary) | SKILL | Complex |
| T12 | Configurable data quality pipeline (rules-based cleaning) | SKILL | Complex |
| T13 | Autonomous anomaly investigation (baselines + metrics) | AGENT | Complex |
| T14 | Hierarchical data aggregation (multi-level rollups) | SKILL | Complex |
| T15 | Time-series forecasting pipeline (trend + seasonality) | SKILL | Complex |
| T16 | Root cause analysis agent (metric degradation diagnosis) | AGENT | Complex |
| T17 | Adaptive ETL orchestration (schema drift handling) | AGENT | Complex |
| T18 | Record deduplication via row hashing | MCP_TOOL | Moderate |

T01-T03 have matching seeded capabilities in the registry (testing retrieval). T04-T18 require the system to generate new capabilities (testing synthesis). The testbed is balanced across capability types: 8 MCP_TOOL (3 retrieval + 5 synthesis), 5 SKILL, 5 AGENT.

**Fixture format diversity:** 33 fixture files across 7 formats (CSV, JSON, TXT, YAML, XML, Excel/.xlsx, TSV), generated programmatically with seed=42 for reproducibility.

**4 Baseline Conditions (additive ablation):**

| Condition | JIT Generation | Verification | Optimizer | What it tests |
|---|---|---|---|---|
| Static | No | No | No | Hand-coded library only |
| JIT only (no_verification) | Yes | No | No | Pure generation quality |
| JIT + Verification (no_optimization) | Yes | Yes | No | Does verification help? |
| JIT + Verif + Optimizer (full) | Yes | Yes | Yes | Does the optimizer help? |

Each condition runs 5 times (randomized task order, different seeds) for 360 total records (18 tasks x 4 conditions x 5 runs). Between runs, a clean snapshot restores the registry, tools directory, and skills directory to a known state.

**Validators:** Each task has a ground-truth validator that compares output against pre-computed expected values (from deterministic fixtures with seed=42). Success = gate_passed AND output_in_range.

### Results

> **ALL PREVIOUS RESULTS ARE STALE.** The codebase underwent a comprehensive audit on 2026-04-06 that resolved 33 internal contradictions (16 + 17 across two commits), rewrote generator prompts, and fixed config centralization issues. All 520+ prior evaluation records (340 from the pre-upgrade run + 180 from batch 1) are invalidated by these changes. A **full re-run** of the evaluation (18 tasks x 4 conditions x 5 runs = 360 records) is required before thesis figures can be produced.

#### Previous Results (Pre-Audit, For Context Only)

The following numbers are from runs **before** the April 2026 audit fixes. They are included for directional guidance only -- do not cite in the thesis.

**Batch 1 (2026-04-05, 180 records, STALE -- generator prompt changed):**

| Condition | Success | Gate Pass |
|---|---|---|
| no_verification | 69/90 (76.7%) | 90/90 (100%) |
| static | 25/90 (27.8%) | 25/90 (27.8%) |

**Pre-upgrade run (2026-03-18 + 2026-03-20, 340 records, STALE -- pre-rebalance):**

| Condition | Success | Gate Pass |
|---|---|---|
| full | 61/85 (71.8%) | 81/85 (95.3%) |
| no_optimization | 66/85 (77.6%) | 85/85 (100%) |
| no_verification | 72/85 (84.7%) | 84/85 (98.8%) |
| static | 25/85 (29.4%) | 25/85 (29.4%) |

Key patterns observed (to be confirmed or revised after re-run):
- JIT synthesis significantly outperformed static libraries (~73% vs ~14% on synthesis tasks)
- Verification hurt overall success rate due to false positives (~9pp drop)
- The optimizer had a cold-start problem in snapshot-reset evaluation
- Type selection was critical for complex tasks (T17: AGENT 100% vs SKILL 9%)

#### Longitudinal Optimizer Test (40 tasks, no snapshot reset)

This test ran 40 sequential tasks (~3 cycles through T01-T13) without resetting the registry between tasks. It demonstrates the optimizer's value with accumulated history:

| Window | Success | Rate |
|---|---|---|
| First cycle (tasks 1-13) | 9/13 | 69.2% |
| Second cycle (tasks 14-26) | 13/13 | 100.0% |
| Third cycle (tasks 27-40) | 13/13 | 100.0% |
| **Overall** | **35/40** | **87.5%** |

From task 19 onward, the system achieved 22 consecutive successes. The active capability count stabilized at 8. The optimizer used prune exclusively -- split and merge require larger workloads with overlapping capability needs. This test was run pre-audit and should be re-validated, though the optimizer code had fewer changes than the generators.

#### Context Window Utilization

Analysis of 1,255 LangFuse traces shows the system never approaches the GPT-5-mini context window limit:

| Agent Type | Traces | Avg Steps | Avg Peak Context | Max Utilization |
|---|---|---|---|---|
| Decomposition | 709 | 2.2 | 6,313 tokens | 7.4% |
| Adversarial Probing | 353 | 5.2 | 13,352 tokens | 28.8% |
| SKILL Execution | 147 | 3.5 | 15,352 tokens | 27.6% |
| AGENT Execution | 46 | 1.4 | 10,441 tokens | 10.8% |

The system is **cost-bound, not context-bound**. Context grows ~2,500 tokens per DeepAgent step. At the observed growth rate, the system would need ~40 steps to reach 50% of the 128K window -- well beyond the `recursion_limit=50` safety bound.

---

## 6. Key Implementation Patterns

### Centralized Configuration

`src/config.py` is the single source of truth for all cross-cutting constants:

| Constant | Value | Env Override | Purpose |
|---|---|---|---|
| `DEFAULT_LLM_MODEL` | `openai:gpt-5-mini` | `JIT_LLM_MODEL` | All LLM calls |
| `SANDBOX_IMAGE` | `jit-sandbox` | `JIT_SANDBOX_IMAGE` | Docker image |
| `DOCKER_PREINSTALLED` | `{mcp, pydantic}` | -- | Sandbox base packages |
| `FILE_SIZE_LIMIT_BYTES` | 10 MB | -- | Input file limit |
| `BINARY_EXTENSIONS` | 30+ extensions | -- | Binary file detection |
| `AUTO_OPTIMIZER_THRESHOLD` | 20 | -- | CLI optimizer trigger |

### Fail-Safe Everything

The system is designed to never hang or crash:

- `verify()` wraps `_verify_inner()` in try/except -- returns a MEDIUM finding and passes the capability on gate bugs. The log explicitly says "PASSING (fail-open policy)" so the behavior is traceable.
- `sandbox_execute()` always returns a dict, never raises
- `adversarial.probe()` always returns a VerificationReport, never raises
- `decomposition_node` returns partial results on timeout (120s) with an error flag
- After 3 failed verification attempts, the pipeline "fails open" and loads whatever it has
- The optimizer node catches all exceptions and continues to END with a warning
- `loading_node` registers MCP tools in ChromaDB even when host `importlib` fails (e.g., tool imports pandas which isn't in the project venv). The tool's `source_code` is stored in metadata, allowing sandbox execution and cross-task reuse. Log distinguishes "registered" from "host-loadable".
- `harness.prepare()` automatically wipes `.deepagents/skills/` and rebuilds ChromaDB from scratch before taking the baseline snapshot, preventing leftover artifacts from contaminating evaluation runs.

### Dynamic Dependency Resolution

Generated MCP tools may import packages not in the base Docker image. The system resolves dependencies through two methods:

**Primary (generator-declared):** The MCP tool generator includes a `pip_packages` field in its structured output (e.g., `["pyyaml", "scikit-learn"]`). These are stored on `GeneratedArtifact.pip_packages` and used directly at execution time -- no import resolution needed.

**Fallback (LLM-based resolution):** For code paths without generator-declared packages (behavioral testing, old registry matches): AST-based import extraction, stdlib filtering via `sys.stdlib_module_names`, then an LLM call to map import names to PyPI names (cached per session). On LLM failure, falls back to identity mapping.

**Execution:** Two-phase Docker: Phase 1 installs packages with network access, commits to ephemeral image; Phase 2 runs code with `--network none`. Binary files (xlsx, parquet) are transferred via base64 encoding. Ephemeral images are cleaned up after execution.

### Retry with Feedback

When verification rejects a capability, the retry loop:

1. Keeps artifacts that passed verification
2. Skips gaps already covered by passing artifacts (via `gap_query` field matching)
3. Feeds verification findings back into the generator prompt (`build_task_context()` injects CRITICAL/HIGH findings)
4. Replaces the failed artifact by name (prevents duplicate accumulation)

### Deterministic Naming

LLM-generated capability names are non-deterministic across runs. To prevent duplicate artifacts, the pipeline overrides LLM name choices with deterministic slugs derived from the gap query + a SHA-256 hash suffix. This ensures that retrying the same gap always targets the same artifact name.

### Seeded Capabilities with Source Code

The three seeded MCP tools that correspond to retrieval tasks (csv-parser, json-validator, text-statistics) include executable `source_code` in their ChromaDB metadata. This allows the CLI to execute registry matches directly via `sandbox_execute()` without JIT generation. Other seeded capabilities (date-formatter, fibonacci, data-cleaning-workflow, report-generation, data-analysis-agent) are descriptors without source code -- they serve as registry match targets but cannot be executed without generation.

### Loading Node: All Three Types

The loading node (`src/orchestrator/loading_node.py`, 177 lines) handles registration for all capability types, not just MCP tools:

- **MCP_TOOL:** Dynamic import via `importlib` + smoke test. If host import fails (missing deps), the tool is still registered in ChromaDB with `source_code` in metadata for sandbox-only execution.
- **SKILL:** Written to `.deepagents/skills/<name>/` during generation. Registered in ChromaDB with `skill_content` metadata.
- **AGENT:** Registered in ChromaDB with `agent_config` metadata (system prompt, tool list, config JSON).

### LangFuse Tracing

All pipeline execution is traced through LangFuse callback handlers:
- Per-task trace tagging (`langfuse_session_id`, `langfuse_tags` with task_id and condition)
- Token attribution per pipeline stage (decomposition, generation, verification)
- Context window utilization analysis via `scripts/analyze_context_utilization.py`
- The MINT CLI emits structured traces for every run, including optimizer operations

---

## 7. Repository Structure

```
src/
  config.py             # Centralized constants (model, sandbox, thresholds)
  cli/                  # MINT CLI (Typer + Rich)
    app.py              # Command definitions (run, plan, retry, list, inspect, ...)
    runner.py           # Pipeline execution, file handling, output auto-save
    display.py          # Rich progress display, output quality detection
  orchestrator/         # Pillar 1: 9-node LangGraph pipeline
    pipeline.py         # Main graph construction
    decomposition.py    # DeepAgent task decomposition
    classifier.py       # LLM capability classification
    gap_detection.py    # Registry search + gap identification
    verification_node.py # Retry loop orchestration
    loading_node.py     # Dynamic loading + registration (all 3 types)
    tool_loader.py      # importlib-based MCP tool loading
    tracing.py          # LangFuse event emission helpers
    generators/         # Three generators (mcp_tool, skill, agent)
    prompts/            # All LLM prompt templates
  verification/         # Pillar 2: Three-tier verification
    gate.py             # Tiered cost orchestration
    static_scanner.py   # Tier 1: AST + regex (388 lines)
    behavioral.py       # Tier 2: Docker sandbox testing (497 lines, 5 test categories)
    adversarial.py      # Tier 3: DeepAgent red-teaming (363 lines)
    sandbox_tool.py     # Docker execution + dependency resolution (290 lines)
  optimizer/            # Pillar 3: Evolutionary registry optimization
    utility.py          # U(c) = alpha*reuse*success - beta*cost - gamma*maintenance
    operations.py       # Prune, split, merge (type-scoped)
    loop.py             # LangGraph optimization loop
    simulation.py       # Synthetic workload simulator
  registry/             # ChromaDB skill registry + DuckDB metrics
    store.py            # SkillRegistry (CRUD + semantic search, 533 lines)
    metrics.py          # MetricsStore (invocations + events, 269 lines)
    evaluation_store.py # EvaluationStore (experiment records)
  schemas/              # 18+ Pydantic models (single source of truth)
    task.py             # TaskState, SubTask, GeneratedArtifact, etc.
    capability.py       # CapabilityType, CapabilityDescriptor
    verification.py     # Severity, VerificationFinding, VerificationReport
    metrics.py          # InvocationRecord, GranularityMetrics
    evaluation.py       # EvaluationRecord, BaselineCondition, FailureReason
  mcp_tools/            # Hand-built MCP tools (fibonacci reference)
tests/
  unit/                 # 814+ tests, no I/O
  integration/          # Docker, ChromaDB, DuckDB
  evaluation/           # Harness, tasks, snapshots, fixtures
    tasks.py            # T01-T18 definitions + validators
    harness.py          # EvaluationHarness (snapshot, seed, run loop)
    snapshots.py        # ChromaDB + skills + tools snapshot/restore
    execution.py        # SKILL/AGENT execution via DeepAgent
    fixtures/           # 33 files across 7 formats (seed=42)
scripts/
  run_task.py           # Legacy CLI runner (851 lines)
  run_pilot.py          # Evaluation runner (18 tasks x 4 conditions x N runs)
  run_longitudinal.py   # Longitudinal optimizer test (40+ tasks, no reset)
  analyze_results.py    # Statistical analysis (CIs, Fisher's exact)
  backfill_token_costs.py # LangFuse -> DuckDB token cost extraction
  analyze_context_utilization.py  # Context window utilization from LangFuse
  test_pipeline.py      # Integration test: full 9-node pipeline (Pillar 1)
  test_verification.py  # Integration test: three-tier gate (Pillar 2)
  test_split_merge.py   # Integration test: optimizer operations (Pillar 3)
  test_auto_optimizer.py # Integration test: CLI auto-optimizer
  test_mint.py          # MINT CLI smoke test (all 3 types + 7 formats)
sandbox/
  Dockerfile            # Minimal: python:3.11-slim + mcp + pydantic
tools/
  quick_validate.py     # DeepAgents skill validator (Tier 0)
  init_skill.py         # Skill scaffolding script
```

**Total codebase:** ~12,000 lines of production code (`src/`), ~5,000 lines of scripts, ~8,000 lines of tests.

---

## 8. How to Run

```bash
# Install dependencies
uv sync

# Set environment variables
export OPENAI_API_KEY="sk-..."
export LANGFUSE_PUBLIC_KEY="pk-..."   # optional
export LANGFUSE_SECRET_KEY="sk-..."   # optional
export LANGFUSE_HOST="http://localhost:3000"  # optional

# Run tests
uv run pytest tests/unit/ -q

# --- MINT CLI (uv run mint) ---
#
# MINT — Model-driven Instantiation of Novel Tools
# Built with Typer + Rich. 11 subcommands for running tasks, managing the
# registry, and iterating on failures. Requires Docker for verification
# and execution of generated MCP tools.
#
# Data storage (isolated from evaluation harness):
#   data/cli_chroma/        — ChromaDB registry (capabilities + embeddings)
#   data/cli_metrics.duckdb — DuckDB invocation history (optimizer input)
#   data/last_run.json      — last run config (for mint retry)
#
# The granularity optimizer auto-enables after 20+ invocations — no flag
# needed. It prunes unused capabilities, splits high-reuse/low-success
# ones, and merges similar low-reuse SKILLs.

# --- Running tasks ---

# Simple task (finds seeded text-statistics in registry)
uv run mint run "Count the number of words in this text" \
  -f tests/evaluation/fixtures/t03_document.txt

# Multi-file task (JIT generates a new MCP tool)
uv run mint run "Read this CSV and tell me which products need reordering" \
  -f tests/evaluation/fixtures/t04_inventory.csv \
  -f tests/evaluation/fixtures/t04_reorder_rules.yaml

# Dry-run: decompose and classify only, no generation
uv run mint plan "Detect anomalies in this data" -f metrics.csv

# JSON output, skip verification
uv run mint run "Count words" -f report.txt --json --skip-verification

# --- Iterating on failures ---

# Re-run last task with extra context
uv run mint retry --hint "The JSON has reorder_threshold per category"

# Re-run with full prompt editing in $EDITOR
uv run mint retry --edit

# Re-run as-is (maybe flaky generation)
uv run mint retry

# --- Registry management ---

# List all capabilities (active + inactive)
uv run mint list

# List only active, filter by type
uv run mint list --active-only --type skill

# Inspect a capability (metadata, source code, invocation history)
uv run mint inspect fibonacci

# Disable/enable capabilities
uv run mint disable fibonacci
uv run mint enable fibonacci

# Manual pruning (remove capabilities unused in last 30 days)
uv run mint prune

# Show invocation history
uv run mint history
uv run mint history --capability text-statistics

# --- Setup and reset ---

# Seed baseline capabilities (fibonacci, csv-parser, etc.)
uv run mint seed

# Full reset (wipe all, re-seed)
uv run mint reset

# Reset to empty (no seeded capabilities)
uv run mint reset --no-seed

# --- Output ---
#
# All output is auto-saved to data/mint_output/ with timestamped filenames.
# Large string values (CSV, JSON, Markdown) are extracted as separate files.
# Override the directory with --output-dir:

uv run mint run "Clean this CSV" -f data.csv --output-dir ./results/

# Output quality detection: warns if output looks incomplete (too few keys,
# too small for execution time, or echoes the input prompt). Yellow border
# on the output panel + retry suggestions.

# --- Legacy CLI (still works, not deprecated) ---
uv run scripts/run_task.py "task description" --file data.csv

# --- Evaluation runners ---

# Run full evaluation (18 tasks x 4 conditions x 5 runs, ~14h, ~$5)
caffeinate -dims uv run scripts/run_pilot.py \
  --all-tasks --all-conditions --n-runs 5 --log-file auto

# Backfill token costs from LangFuse
uv run scripts/backfill_token_costs.py --write --csv

# Generate analysis tables
uv run scripts/analyze_results.py --csv

# Run longitudinal optimizer test (40 tasks, no reset, ~5h, ~$1.50)
caffeinate -dims uv run scripts/run_longitudinal.py \
  --n-tasks 40 --seed 42 --log-file auto

# --- Integration tests (require Docker + OPENAI_API_KEY) ---
uv run scripts/test_pipeline.py       # Full pipeline, all 3 types
uv run scripts/test_verification.py   # Three-tier gate
uv run scripts/test_split_merge.py    # Optimizer operations
uv run scripts/test_auto_optimizer.py # CLI auto-optimizer
uv run scripts/test_mint.py           # MINT CLI smoke test
```

---

## 9. What Makes This System Interesting

The conventional approach to multi-agent capabilities is **pre-built**: you write tools, test them, deploy them, and the agent uses them. When a new task arrives that no existing tool handles, a human intervenes.

This system inverts that: capabilities are generated on demand, verified automatically, and evolved over time. The verification layer is the core contribution -- it defines a concrete, measurable boundary between what automated testing can and cannot catch.

The stale evaluation data (pre-audit) showed:
- A ~95-100% gate pass rate with ~66-81% success rate, quantifying the semantic correctness gap
- Verification adding ~2.3x tokens and ~18x latency while reducing success by ~9pp due to false positives
- The optimizer converging to 100% success after ~18 tasks in longitudinal testing

These patterns give practitioners a framework for deciding when verification is worth its cost and when evolutionary optimization adds value. The full re-run (post-audit) will confirm whether the generator prompt fixes and audit remediations change these dynamics.

The expanded 18-task evaluation (adding T14-T18) strengthened the evidence for capability type diversity. T17 (adaptive ETL orchestration) demonstrated that type selection is not merely cosmetic: AGENT classification yielded 100% success vs SKILL at 9%. This validates the three-type taxonomy as more than a theoretical distinction -- the execution model (function call vs. instruction following vs. autonomous planning) has measurable impact on task success for complex, adaptive tasks.

Together, these findings provide empirical grounding for the three tensions in the research question: autonomy vs. correctness (the verification tradeoff), specificity vs. generality (the optimizer's split/merge operations), and context economy (token costs per capability type).

---

## 10. Changes Since Initial Overview (March 23 -> April 7, 2026)

This section documents what changed between the first system overview (March 23) and this revision, for traceability.

### New Modules

| Module | Lines | Purpose |
|---|---|---|
| `src/config.py` | 80 | Centralized configuration (was scattered across files) |
| `src/cli/app.py` | 428 | MINT CLI entry point (Typer) |
| `src/cli/runner.py` | 1,319 | Pipeline execution with Rich progress |
| `src/cli/display.py` | 603 | Rich formatting and output quality detection |
| `src/orchestrator/tracing.py` | 49 | LangFuse event emission helpers |
| `scripts/test_mint.py` | 374 | CLI smoke test (all types + formats) |
| `scripts/test_auto_optimizer.py` | 315 | Auto-optimizer integration test |

### Major Changes to Existing Modules

- **Generators** (`mcp_tool.py`, `skill.py`, `agent.py`): Added `pip_packages` declaration, deterministic naming via `forced_name`, centralized model config
- **Static scanner** (`static_scanner.py`): Added variable-indirection check (B1.2 fix), skips non-MCP types
- **Behavioral testing** (`behavioral.py`): Added output-variation check (B2.3), parameterless smoke test (B2.1), 5 test categories (was 4)
- **Loading node** (`loading_node.py`): Registers SKILL/AGENT in ChromaDB (was MCP_TOOL only), handles host import failures gracefully
- **Registry store** (`store.py`): Added `is_active` filter, source code in seeded capabilities, smarter gap detection
- **Evaluation schema** (`evaluation.py`): Added `NO_SOURCE_CODE` failure reason, proper StrEnum types

### Audit & Quality

Two audit commits (2026-04-06) resolved **33 internal contradictions**:
- Config constants duplicated across files -> centralized in `src/config.py`
- Generator naming inconsistencies -> `forced_name` used everywhere
- Static scanner running on SKILL/AGENT (YAML/JSON, not Python) -> type check added
- Optimizer loop state merging implicit -> explicit `utility_scores` return
- Various docstring/test mismatches fixed

### Evaluation Status

All prior evaluation data is **stale** due to the audit fixes. A full re-run (18 tasks x 4 conditions x 5 runs = 360 records) is required. The longitudinal optimizer test (40 tasks) should also be re-validated.

---

## 11. Future Work and Demo Notes

### MCP Server Deployment (Chapter 7)

The current prototype uses `importlib` dynamic import for tool execution -- no running servers. However, every generated MCP tool is **server-ready by construction**:

```python
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("csv-parser")

@mcp.tool()
async def csv_parser(file_path: str) -> dict:
    ...

if __name__ == "__main__":
    mcp.run()  # starts a stdio MCP server
```

The sandbox uses `__name__ = "not_main"` to prevent `mcp.run()` from starting. Remove that one line and the generated tool becomes a live MCP server.

**For thesis defense demo:** Save `artifact.source_code` to a file, run with `uv run python generated_tool.py`, connect from Claude Desktop. Takes ~20 lines of glue code.

**For production (future work):** Needs process lifecycle management, multi-tool server consolidation, and persistent deployment (Docker/K8s).

### Other Future Work

- **MINT rebrand** -- rename project from JustInTimeMAS to MINT. CLI is implemented; remaining: repo rename, import paths, thesis title.
- **`mint export` / `mint import`** -- export generated source, edit, re-import.
- **`--force-regenerate`** -- bypass registry matching on retry.
- **Smart prompt improvement (`--improve`)** -- LLM pre-pipeline rewrite of vague prompts.
- **`mint fix <name> --error "..."`** -- targeted code repair for single-line bugs.
- **Semantic correctness testing** -- LLM-as-judge or property-based testing to close the gate/success gap.
- **Classifier tuning for AGENT tasks** -- T16/T17 frequently misclassified as SKILL.
- **Larger testbed for organic split/merge** -- 50+ diverse tasks to test evolutionary operations.
- **Per-task token attribution** -- implemented in harness, pending re-run for data collection.
