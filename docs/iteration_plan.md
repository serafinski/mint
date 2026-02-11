# Master's Thesis: Iteration Plan (v5)

## Verified JIT Capability Synthesis for Self-Developing Multi-Agent Systems

**Approach:** 8 iterations (0–7), each producing a working increment you can demo and evaluate. Each iteration lists concrete tasks, which ones parallelize well with Claude Code Agent Teams (your development tool, not part of the runtime system), and what to validate before moving on.

**Clarification:** Claude Code Agent Teams is used purely as a development workflow tool to parallelize coding work. The system you're building uses LangGraph for orchestration, DeepAgents (`deepagents` package) for autonomous multi-step reasoning nodes, MCP SDK for tool serving, and your own structured state protocol for agent communication.

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
| `mcp-tool-dev` | Start of Iteration 2 | MCP tool development and server management |
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

## Iteration 0: Complete Scaffolding & Prove the Loop (Weeks 1–2)

**Goal:** Finish infrastructure setup and prove the core detection loop works end-to-end.

### Already done
- Project init, uv, basic file structure
- CLAUDE.md, docs, skills (6 total + reference files)
- DeepAgents skill utilities in `tools/`

### Remaining tasks

1. **Create the `src/` directory structure** with `__init__.py` files:
   ```
   src/
   ├── orchestrator/
   │   └── generators/
   ├── registry/
   ├── verification/
   ├── optimizer/
   ├── schemas/
   ├── mcp_tools/
   ├── skills/
   └── agents/
   ```

2. **Create test directories**: `tests/unit/`, `tests/integration/`, `tests/evaluation/`

3. **Add remaining dependencies** (if not already in `pyproject.toml`):
   `langgraph`, `langchain-anthropic`, `deepagents`, `mcp`, `chromadb`, `duckdb`, `langfuse`, `pydantic`, `pytest`, `ruff`

4. **Docker sandbox** — Minimal Dockerfile in `sandbox/` for isolated Python execution. No network access. Test it.

5. **Makefile** — Targets: `test`, `lint`, `format`

6. **Minimal state schema** — Stripped-down `TaskState` (Pydantic) in `src/schemas/task.py`: task description, required capabilities, matches, gaps.

7. **Minimal ChromaDB registry** — `src/registry/store.py`: add capability, search by query, detect gaps. Seed with 3 hand-written tools (fibonacci, CSV parser, date formatter).

8. **Minimal LangGraph flow** — `src/orchestrator/pipeline.py`: 3-node graph (intake → gap_detection → report). Uses TaskState. Use Command API for routing between nodes (see `langgraph-architect` skill).

9. **Manual MCP server** — `src/mcp_tools/fibonacci.py`: hand-written fibonacci tool via FastMCP. Callable from the graph.

10. **Demo script** — Shows both paths: capability found → MCP call → result, and gap detected → logged.

11. **LangFuse tracing** — Verify traces appear for the LangGraph invocation. See `langfuse-tracing` skill for callback handler pattern.

### Prompting strategy (single session, 3–4 prompts)

**Prompt 1:** Directory structure, remaining deps, test dirs, Docker sandbox, Makefile.

**Prompt 2:** Minimal schemas, ChromaDB registry with seeding, unit tests.

**Prompt 3:** LangGraph pipeline (use Command API for routing), manual MCP fibonacci server, demo script, integration tests.

**Prompt 4:** LangFuse tracing wired in, verify everything works end-to-end.

### Agent Teams usage
**Don't use Agent Teams.** Sequential setup work.

### Exit criteria
- `uv run pytest` passes
- LangFuse traces the LangGraph flow
- Docker sandbox runs isolated Python
- Demo script shows "capability found" and "gap detected" paths
- Hand-written MCP fibonacci tool responds to calls from the graph
- **Update CLAUDE.md** with what's built

---

## Iteration 1: Production Registry, Schemas & Metrics (Weeks 2–3)

**Goal:** Upgrade the proof-of-concept into production-quality foundations.

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

### Exit criteria
- All schemas validate and serialize cleanly
- Registry supports hierarchical search with persistence
- Gap detection works against the seeded registry
- Metrics store records and queries the four key metrics
- All tests pass
- **Update CLAUDE.md**

---

## Iteration 2: JIT Capability Pipeline (Weeks 3–5)

**Goal:** Build Pillar 1 end-to-end without verification. Decompose tasks, detect gaps, classify, generate, dynamically load, and register.

### Tasks

1. **Task decomposition agent** (`src/orchestrator/decomposition.py`)
   - **Implemented as a DeepAgent** (`create_deep_agent()`)
   - The DeepAgent uses built-in `write_todos` for planning, file system tools for context management
   - Invoked from within a LangGraph node function (DeepAgents are complete graphs, not nodes)
   - Use `response_format` for structured output (Pydantic model with subtasks)
   - System prompt instructs the agent to plan decomposition, produce subtasks with capability annotations, and write results to files
   - Use `claude-sonnet-4-5-20250929` as the model
   - Trace with LangFuse via callback handler

2. **Capability classifier** (`src/orchestrator/classifier.py`)
   - Plain LangGraph node (no DeepAgent — straightforward classification)
   - Rule-based heuristics: deterministic+stateless → MCP tool, procedural+domain → skill, stateful+planning → agent
   - LLM-assisted fallback for ambiguous cases

3. **MCP tool generator** (`src/orchestrator/generators/mcp_tool.py`)
   - Plain LangGraph node
   - Given a capability description + I/O schema, generate a Python MCP tool
   - Use Claude API for code generation
   - Output: Python function + FastMCP server wrapper + basic test harness

4. **Dynamic MCP server management** (`src/orchestrator/server_manager.py`)
   - Subprocess lifecycle manager: spawn generated tool as MCP server, health-check, tear down
   - Registry integration: new tool → start server → register endpoint
   - Graceful shutdown on process exit
   - This is critical plumbing — get it right here

5. **Skill generator** (`src/orchestrator/generators/skill.py`)
   - Plain LangGraph node
   - Generate SKILL.md with YAML frontmatter and instructions, conforming to DeepAgents skill format
   - Must produce the canonical structure: `SKILL.md` + optional `scripts/`, `references/`, `assets/`
   - Validate output with `tools/quick_validate.py` before returning (structural pre-check)
   - Frontmatter rules: `name` (hyphen-case, max 64 chars), `description` (max 1024 chars, no angle brackets)
   - Use `tools/init_skill.py` as a reference for the expected template structure

6. **Agent generator** (`src/orchestrator/generators/agent.py`)
   - Plain LangGraph node
   - Generate system prompt and LangGraph node config
   - For DeepAgent-type agents, also generate skill configurations targeting `~/.deepagents/<agent>/skills/`

7. **Pipeline graph** (`src/orchestrator/pipeline.py`)
   - Full LangGraph graph: decomposition node (invokes DeepAgent) → gap detection → classification → generation → server loading → registration
   - Use Command API for routing between nodes
   - Trace everything with LangFuse

### Skills to create first
- `.claude/skills/mcp-tool-dev/SKILL.md`

### Prompting strategy
*Phase A (single session):* Build the task decomposition DeepAgent and the classifier. The DeepAgent is a `create_deep_agent()` call wrapped in a LangGraph node function. Test it in isolation — verify that `write_todos` planning works and `response_format` returns structured subtasks.

*Phase B (3-teammate Agent Team):*
```
Create an agent team with 3 teammates:
- "mcp-gen" teammate: implement the MCP tool generator in
  src/orchestrator/generators/mcp_tool.py AND the dynamic server manager in
  src/orchestrator/server_manager.py. The generator produces Python MCP tools
  (FastMCP) from a CapabilityDescriptor. The server manager spawns them as
  subprocesses, health-checks them, and tears them down. Write integration
  tests that generate a simple tool and verify it serves via MCP protocol.
  See the mcp-tool-dev skill for conventions.
- "skill-gen" teammate: implement the skill generator in
  src/orchestrator/generators/skill.py. Generate SKILL.md files + optional
  scripts/references/assets from CapabilityDescriptor, conforming to the
  DeepAgents skill format. Run tools/quick_validate.py on every generated skill
  as a structural pre-check. Use tools/init_skill.py as a reference for the
  expected template. Write tests including a test that validates the generated
  skill passes quick_validate.py.
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
- LangFuse traces show the DeepAgent's planning and tool-calling loop
- Pipeline decomposes tasks, detects gaps, classifies, and generates capabilities
- Generated MCP tools load dynamically as subprocesses and respond to calls
- Generated skills pass `tools/quick_validate.py` validation
- Server manager handles lifecycle cleanly
- LangFuse traces full pipeline end-to-end
- **Update CLAUDE.md**

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

2. **Tier 2: Behavioral testing** (`src/verification/behavioral.py`)
   - Plain LangGraph node
   - Verifier agent generates test inputs for a given capability
   - Four test categories: nonexistent entity, consistency, boundary, latency analysis
   - Runs tests in Docker sandbox
   - Appends results to `VerificationReport`

3. **Tier 3: Adversarial probing** (`src/verification/adversarial.py`)
   - **Implemented as a DeepAgent** (`create_deep_agent()`)
   - Invoked from within a LangGraph node function
   - Uses `write_todos` to plan attack strategy
   - Uses sandbox backend with `execute` tool to run probes in isolation
   - Writes intermediate analysis to files (context management)
   - Tests network-disconnected behavior
   - Uses `response_format=AdversarialFindings` for structured output
   - Trace with LangFuse

4. **Verification gate** (`src/verification/gate.py`)
   - Combines all three tiers
   - For skills: run `quick_validate.py` first as Tier 0 (fast structural check)
   - Critical findings → reject
   - High findings → must pass Tier 2
   - Returns pass/fail + detailed report

5. **Pipeline integration** — Verification node between generation and registration. Failed capabilities get feedback and can be regenerated (retry loop, max 3 attempts). Use Command API to route between generation → verification → registration with loop-back on failure.

### Skills to create first
- `.claude/skills/verification-patterns/SKILL.md`

### Agent Teams usage
**4-teammate team** (justified by thesis contribution):

```
Create an agent team with 4 teammates:
- "static" teammate: Tier 1 static scanner in src/verification/static_scanner.py.
  AST + regex for 5 contamination categories. Zero external deps beyond stdlib.
  For skill-type capabilities, also integrate tools/quick_validate.py as a
  structural pre-check. Output VerificationReport. Comprehensive tests with
  deliberately contaminated code samples. See the verification-patterns skill.
- "behavioral" teammate: Tier 2 behavioral testing in src/verification/behavioral.py.
  Plain LangGraph node. 4 test categories (nonexistent entity, consistency,
  boundary, latency). Docker sandbox execution. Tests with mock-contaminated tools.
- "adversarial" teammate: Tier 3 adversarial probing in src/verification/adversarial.py.
  Implement as a DeepAgent (create_deep_agent with sandbox backend). The agent
  uses write_todos for planning, execute tool for running probes, file system
  for intermediate analysis. Use response_format for structured findings.
  Trace with LangFuse. See the deep-agents skill for patterns. Write tests.
- "devil" teammate: after the other 3 finish, review all tiers and find ways a
  malicious or lazy code generator could bypass each tier. Pay special attention
  to whether the adversarial DeepAgent's probing can be tricked. Document
  findings in docs/decisions/verification-hardening.md.
Have teammates share findings with each other. Require plan approval.
```

### Exit criteria
- Static scanner catches all 5 contamination categories
- Skill validation via `quick_validate.py` integrated into the gate
- Behavioral tests distinguish real from mock tools
- Adversarial DeepAgent runs multi-step probing and finds additional failure modes
- LangFuse traces show the adversarial agent's full execution (planning, tool calls, file writes)
- The full gate rejects bad capabilities, passes good ones
- Pipeline retries generation on failure (max 3 attempts)
- Devil's advocate findings documented
- **Update CLAUDE.md**

---

## Iteration 4: Granularity Optimizer (Weeks 7–9)

**Goal:** Build Pillar 3 — evolutionary feedback loop.

### Tasks

1. **Utility computation** (`src/optimizer/utility.py`)
   - Implement $U(c) = \alpha \cdot \text{reuse}(c) \cdot \text{success}(c) - \beta \cdot \text{token\_cost}(c) - \gamma \cdot \text{maintenance}(c)$
   - Pull metrics from DuckDB
   - Configurable weights ($\alpha$, $\beta$, $\gamma$)

2. **Split operation** (`src/optimizer/operations.py`)
   - Detect candidates: high reuse + mediocre success (>60% reuse, <70% success)
   - **Optional DeepAgent for failure analysis**: `create_deep_agent()` that investigates why capabilities fail, using file system to manage large failure logs and `write_todos` to plan investigation
   - Generate specialized variants (reuses the JIT generator from Pillar 1)
   - Register new variants, deactivate parent

3. **Merge operation** (same file)
   - Detect candidates: low reuse (<5%) + high embedding similarity
   - Merge into broader capability
   - Register merged version, deactivate parents

4. **Prune operation** (same file)
   - Detect: zero invocations over configurable window, no dependencies
   - Archive (don't delete)

5. **Optimization loop** (`src/optimizer/loop.py`)
   - LangGraph graph that runs after each task cycle
   - Compute metrics → identify candidates → apply operations → update registry
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
  operations (split, merge, prune) in src/optimizer/. For the split operation,
  optionally implement failure analysis as a DeepAgent (create_deep_agent with
  write_todos for planning and file system for managing failure logs). Start
  with a simpler LLM-based analysis and upgrade if needed. Write unit tests
  with synthetic data.
- "simulation" teammate: build the simulation harness in src/optimizer/simulation.py.
  Configurable task distributions, optimization cycles, KL divergence convergence
  analysis, matplotlib plots. Write tests.
Wait for both to finish, then wire the optimization loop into the main pipeline.
```

### Exit criteria
- Split, merge, prune all work correctly
- Simulation shows convergence over 50+ cycles
- Utility scores improve vs. no-optimization baseline
- If DeepAgent failure analysis is used, LangFuse traces show the reasoning
- **Update CLAUDE.md**

---

## Iteration 5: Integration & End-to-End Testing (Weeks 9–11)

**Goal:** Wire all three pillars together. Build evaluation infrastructure and baselines.

### Tasks

1. **Full pipeline graph** — All pillars connected:
   Task → Decomposition (DeepAgent invoked from node) → Gap Detection → Classification → Generation → Verification (incl. Adversarial DeepAgent invoked from node) → Server Loading → Registration → Execution → Metrics → Optimization

2. **Testbed setup** — 10–15 tasks of varying complexity, expected capabilities, ground truth

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
- At least one split/merge/prune occurs naturally
- **Update CLAUDE.md**

---

## Iteration 6: Evaluation & Analysis (Weeks 11–13)

**Goal:** Run formal evaluation, collect data, produce thesis figures.

### Tasks

1. **Run all baselines** — ≥5 runs per condition for statistical significance
2. **Collect metrics** — success rate, contamination rate, reuse distribution (Gini), token cost, time-to-first-correct, convergence
3. **Manual audit** — 20–30 generated capabilities, classify: correct / contaminated / partial
4. **DeepAgent analysis** — Separately measure: how many tool calls do DeepAgents make per invocation? Does the adversarial agent's multi-step probing catch things a single-pass approach misses? Token cost of DeepAgent nodes vs. plain nodes?
5. **Figures** — success comparison, contamination rates, registry evolution, convergence curve, token breakdown, reuse distribution, DeepAgent analysis
6. **Statistical analysis** — significance tests for key comparisons

### Agent Teams usage
**2-teammate team:**

```
Create an agent team with 2 teammates:
- "analysis": run evaluation suite, collect metrics into DuckDB, compute
  summary statistics, significance tests, export CSVs. Include a separate
  analysis of DeepAgent behavior (tool call counts, planning steps, token cost).
- "visualization": publication-quality matplotlib figures from CSVs.
  Academic conventions, consistent style. PNG + PDF to docs/figures/.
Analysis must finish before visualization starts.
```

### Exit criteria
- All metrics collected with multiple runs
- Publication-quality figures
- Statistical significance confirmed or reported
- DeepAgent effectiveness analyzed separately
- Manual audit documented
- **Update CLAUDE.md**

---

## Iteration 7: Thesis Writing (Weeks 13–16)

**Goal:** Write the thesis. Agent Teams for research/review support only.

### Writing order
1. Chapter 3 (JIT Pipeline) — include DeepAgent decomposition design rationale
2. Chapter 4 (Verification) — core contribution, include adversarial DeepAgent design
3. Chapter 5 (Granularity Optimization)
4. Chapter 6 (Evaluation) — include DeepAgent analysis subsection
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

## Cross-Cutting: DeepAgents Token Budget

DeepAgents are the most expensive components in the pipeline. Track their cost separately:

| DeepAgent Component | What It Does | Approx. Token Multiplier vs. Single LLM Call |
|---|---|---|
| Task decomposition | Plans via `write_todos`, iterates through tool calls | 3–4x |
| Adversarial probing | Plans attacks, executes in sandbox, writes analysis to files | 4–5x |
| Failure analysis (optional) | Investigates failure patterns, manages logs via files | 2–3x |

Monitor via LangFuse. If costs are too high, replace DeepAgents with plain LangGraph nodes (single LLM call + structured output) and document the quality tradeoff in the thesis.

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

## Cross-Cutting: Git Strategy

- Branch per iteration: `iter/0-scaffolding`, `iter/1-registry`, etc.
- When using Agent Teams, each teammate owns different files
- Merge to `main` at exit criteria

## Cross-Cutting: Risk Management

| Risk | Mitigation |
|---|---|
| LLM generation quality too low | Start simple, increase complexity gradually |
| Verification too strict | Tune Tier 1 thresholds; Tier 2 can override "High" |
| Verification too loose | Devil's advocate in Iter 3; manual audit in Iter 6 |
| DeepAgent costs too high | Track via LangFuse; fall back to plain nodes if needed |
| DeepAgent execution too slow | Built-in summarization helps; can also reduce tool set |
| Optimizer doesn't converge | Debug with simulation harness; simplify distribution |
| Dynamic MCP server management flaky | Health checks + auto-restart in Iter 2 |
| Agent Teams overhead too high | Start with 2 teammates; scale up only when justified |
| Generated skills don't conform to format | Validate with `tools/quick_validate.py` in gate |
| Scope creep | Exit criteria per iteration |

---

## Timeline Summary

| Iteration | Weeks | Focus | Agent Teams? | DeepAgents? |
|---|---|---|---|---|
| 0 | 1–2 | Scaffolding + Prove the Loop | No | No |
| 1 | 2–3 | Production Registry, Schemas & Metrics | Yes (3) | No |
| 2 | 3–5 | JIT Pipeline + Dynamic Server Mgmt | Yes (3, phased) | Yes: decomposition |
| 3 | 5–7 | Verification Layer | Yes (4) | Yes: adversarial probing |
| 4 | 7–9 | Granularity Optimizer | Yes (2) | Optional: failure analysis |
| 5 | 9–11 | Integration, E2E & Baselines | Yes (3) | All DeepAgents active |
| 6 | 11–13 | Evaluation & Analysis | Yes (2) | Measuring DeepAgent effectiveness |
| 7 | 13–16 | Thesis Writing | Yes (review only) | Documenting design rationale |

**Total: ~16 weeks**
