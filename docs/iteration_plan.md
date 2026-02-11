# Master's Thesis: Iteration Plan (v3)

## Verified JIT Capability Synthesis for Self-Developing Multi-Agent Systems

**Approach:** 8 iterations (0–7), each producing a working increment you can demo and evaluate. Each iteration lists concrete tasks, which ones parallelize well with Claude Code Agent Teams (your development tool, not part of the runtime system), and what to validate before moving on.

**Clarification:** Claude Code Agent Teams is used purely as a development workflow tool to parallelize coding work. The system you're building uses LangGraph for orchestration, MCP SDK for tool serving, and your own structured state protocol for agent communication.

---

## Pre-Iteration: Project State

The following is already in place:

- Project initialized with `uv` (`pyproject.toml`, `.python-version`)
- `CLAUDE.md` with project overview, tech stack, conventions
- `docs/iteration_plan.md` and `docs/thesis_proposal.md`
- `.claude/skills/langfuse-tracing/SKILL.md` — LangFuse integration patterns
- `.claude/skills/langgraph-architect/SKILL.md` — LangGraph architecture patterns
- `.claude/skills/pydantic-schemas/SKILL.md` — Pydantic schemas patterns
- `.claude/skills/testing-conventions/SKILL.md` — testing conventions patterns
- `main.py`, `.env`, `.gitignore`, `README.md`

### Recommended Additional Skills

Create these in `.claude/skills/` to improve Claude Code's output quality across iterations:

#### `.claude/skills/mcp-tool-dev/SKILL.md`
Teaches Claude Code how to write MCP tools for this project:
- Use the `mcp` Python SDK (FastMCP pattern)
- Standard input/output schema conventions using Pydantic
- How generated tools get registered in the skill registry
- How the dynamic server manager spawns and health-checks tool servers
- Testing patterns for MCP tools (call via client, verify response schema)

#### `.claude/skills/verification-patterns/SKILL.md`
Teaches Claude Code how to write and test verification logic:
- AST-based scanning patterns (how to walk Python AST nodes)
- How to write deliberately contaminated code samples for testing
- The three-tier verification flow and how tiers interact
- How to write adversarial test inputs

**When to create each skill:** Create `mcp-tool-dev` at the start of Iteration 2. Create `verification-patterns` at the start of Iteration 3. Skills are most useful when written just before the iteration that needs them, because you'll have concrete patterns to encode.

---

## CLAUDE.md Update Protocol

**Update CLAUDE.md at the start of each iteration.** This is critical because:
- Every Claude Code session reads it automatically
- Every Agent Team teammate reads it automatically (but NOT the lead's conversation history)
- It's the primary way teammates understand what exists and what's stable

**What to update:**
1. **"What's built" section** — list completed modules with brief descriptions
2. **"Current iteration" section** — what's being built right now
3. **"Stable interfaces" section** — modules that should not be modified (import from, don't edit)
4. **"Conventions" section** — any patterns that emerged (naming, error handling, etc.)

**Do it yourself or at minimum carefully review what Claude produces.** A drifted CLAUDE.md degrades every subsequent session.

---

## Iteration 0: Complete Scaffolding & Prove the Loop (Weeks 1–2)

**Goal:** Finish infrastructure setup and prove the core detection loop works end-to-end.

### Already done
- Project init, uv, basic file structure
- CLAUDE.md, docs, skills (2)

### Remaining tasks

1. **Create the `src/` directory structure** with `__init__.py` files:
   ```
   src/
   ├── orchestrator/           # Pillar 1
   ├── registry/               # Skill registry
   ├── verification/           # Pillar 2
   ├── optimizer/              # Pillar 3
   ├── schemas/                # Shared Pydantic state schemas
   ├── mcp_tools/              # Generated & hand-built MCP tools
   ├── skills/                 # SKILL.md files (runtime, not .claude/skills)
   └── agents/                 # Agent configs & system prompts
   ```

2. **Add remaining dependencies** (if not already in `pyproject.toml`):
   `langgraph`, `langchain-anthropic`, `mcp`, `chromadb`, `duckdb`, `langfuse`, `pydantic`, `pytest`, `ruff`

3. **Create test directories**: `tests/unit/`, `tests/integration/`, `tests/evaluation/`

4. **Docker sandbox** — Minimal Dockerfile in `sandbox/` for isolated Python execution. No network access. Test it with a simple script.

5. **Minimal state schema** — A stripped-down `TaskState` (Pydantic) in `src/schemas/task.py`: task description, required capabilities, detected gaps.

6. **Minimal ChromaDB registry** — `src/registry/store.py`: add capability, search by query, detect gaps. Seed with 3 hand-written tools (fibonacci, CSV parser, date formatter).

7. **Minimal LangGraph flow** — `src/orchestrator/pipeline.py`: 3-node graph (intake → gap_detection → report). Uses the registry.

8. **Manual MCP server** — `src/mcp_tools/fibonacci.py`: hand-written fibonacci tool via MCP SDK. Callable from the LangGraph graph.

9.  **Demo script** — Show both paths: capability found → MCP call → result, and gap detected → logged.

10. **LangFuse tracing** — Verify traces appear for the LangGraph invocation.

### Prompting strategy (single session, 3–4 prompts)

**Prompt 1:** Directory structure, remaining deps, test dirs, Docker sandbox.

**Prompt 2:** Minimal schemas, ChromaDB registry with seeding, unit tests.

**Prompt 3:** LangGraph pipeline, manual MCP fibonacci server, demo script, integration tests.

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
2. **Capability classifier** (`src/orchestrator/classifier.py`)
3. **MCP tool generator** (`src/orchestrator/generators/mcp_tool.py`)
4. **Dynamic MCP server management** (`src/orchestrator/server_manager.py`) — subprocess lifecycle, health checks, teardown
5. **Skill generator** (`src/orchestrator/generators/skill.py`)
6. **Agent generator** (`src/orchestrator/generators/agent.py`)
7. **Pipeline graph** (`src/orchestrator/pipeline.py`) — full LangGraph flow with LangFuse tracing

### Skills to create first
- `.claude/skills/mcp-tool-dev/SKILL.md`

### Prompting strategy
*Phase A (single session):* Decomposition agent + classifier (these define interfaces).
*Phase B (3-teammate team):* MCP tool generator + server manager, skill generator, agent generator.
*Phase C (single session):* Wire into LangGraph pipeline.

### Exit criteria
- Pipeline decomposes tasks, detects gaps, generates capabilities
- Generated MCP tools load dynamically as subprocesses and respond to calls
- Server manager handles lifecycle cleanly
- LangFuse traces full pipeline
- **Update CLAUDE.md**

---

## Iteration 3: Verification Layer (Weeks 5–7)

**Goal:** Build Pillar 2 — three-tier verification gate. Core thesis contribution.

### Tasks

1. **Tier 1: Static scanner** (`src/verification/static_scanner.py`) — AST + regex, zero external deps
2. **Tier 2: Behavioral testing** (`src/verification/behavioral.py`) — 4 test categories, Docker sandbox
3. **Tier 3: Adversarial probing** (`src/verification/adversarial.py`) — red-team agent
4. **Verification gate** (`src/verification/gate.py`) — combines tiers, pass/fail
5. **Pipeline integration** — verification node between generation and registration, retry loop

### Skills to create first
- `.claude/skills/verification-patterns/SKILL.md`

### Agent Teams usage
**4-teammate team** (justified by thesis contribution):

```
Create an agent team with 4 teammates:
- "static" teammate: Tier 1 static scanner. AST + regex for 5 categories.
  Zero external deps. Comprehensive tests with contaminated code samples.
- "behavioral" teammate: Tier 2 behavioral testing. 4 test categories
  (nonexistent entity, consistency, boundary, latency). Docker sandbox execution.
  Tests with mock-contaminated tools.
- "adversarial" teammate: Tier 3 adversarial probing. Red-team agent, network
  disconnect tests, ground truth comparison. Tests.
- "devil" teammate: after the other 3 finish, review all tiers and find
  bypass strategies. Document findings in docs/decisions/verification-hardening.md.
Have teammates share findings with each other. Require plan approval.
```

### Exit criteria
- Scanner catches all 5 contamination categories
- Behavioral tests distinguish real from mock tools
- Adversarial probing finds additional failure modes
- Gate rejects bad capabilities, passes good ones
- Pipeline retries on failure with a limit
- Devil's advocate findings documented
- **Update CLAUDE.md**

---

## Iteration 4: Granularity Optimizer (Weeks 7–9)

**Goal:** Build Pillar 3 — evolutionary feedback loop.

### Tasks

1. **Utility computation** (`src/optimizer/utility.py`) — formal utility function with configurable weights
2. **Split/Merge/Prune operations** (`src/optimizer/operations.py`)
3. **Optimization loop** (`src/optimizer/loop.py`) — post-cycle LangGraph graph, KL divergence tracking
4. **Simulation harness** (`src/optimizer/simulation.py`) — synthetic task generator, convergence plots

### Agent Teams usage
**2-teammate team:**

```
Create an agent team with 2 teammates:
- "optimizer" teammate: utility computation and three evolutionary operations
  (split, merge, prune) in src/optimizer/. Unit tests with synthetic data.
- "simulation" teammate: simulation harness with configurable task distributions,
  optimization cycles, KL divergence convergence analysis, matplotlib plots.
Wait for both to finish, then wire the optimization loop into the main pipeline.
```

### Exit criteria
- Split, merge, prune all work correctly
- Simulation shows convergence over 50+ cycles
- Utility scores improve vs. no-optimization baseline
- **Update CLAUDE.md**

---

## Iteration 5: Integration & End-to-End Testing (Weeks 9–11)

**Goal:** Wire all three pillars together. Build evaluation infrastructure and baselines.

### Tasks

1. **Full pipeline graph** — all pillars connected end-to-end
2. **Testbed setup** — 10–15 tasks, expected capabilities, ground truth
3. **Shared evaluation harness** (`tests/evaluation/harness.py`)
4. **Baseline implementations** — static skills, JIT-no-verify, JIT-no-optimize, full system
5. **Integration tests** — full lifecycle, reuse, evolutionary operations
6. **Error handling** — generation failure, sandbox timeout, server crash, corrupted registry

### Agent Teams usage
**3-teammate team for baselines:**

```
Create an agent team with 3 teammates:
- "static-baseline": hand-curated skill library, wired to evaluation harness.
- "no-verify-baseline": pipeline with Pillar 2 bypassed, wired to harness.
- "no-optimize-baseline": pipeline with Pillar 3 bypassed, wired to harness.
All baselines must output metrics to the same DuckDB schema via the shared harness.
```

### Exit criteria
- Full system runs 10+ tasks end-to-end
- All 4 conditions produce comparable metrics
- LangFuse traces complete capability lifecycle
- At least one split/merge/prune occurs naturally
- **Update CLAUDE.md**

---

## Iteration 6: Evaluation & Analysis (Weeks 11–13)

**Goal:** Run formal evaluation, collect data, produce thesis figures.

### Tasks

1. **Run all baselines** — multiple runs per condition (≥5 for significance)
2. **Collect metrics** — success rate, contamination rate, reuse distribution (Gini), token cost, time-to-first-correct, convergence
3. **Manual audit** — 20–30 generated capabilities, classify: correct / contaminated / partial
4. **Figures** — success comparison, contamination rates, registry evolution, convergence curve, token breakdown, reuse distribution
5. **Statistical analysis** — significance tests for key comparisons

### Agent Teams usage
**2-teammate team:**

```
Create an agent team with 2 teammates:
- "analysis": run evaluation suite, collect metrics into DuckDB, compute
  summary statistics, run significance tests, export CSVs.
- "visualization": generate publication-quality matplotlib figures from CSVs.
  Academic conventions, consistent style. Save as PNG + PDF to docs/figures/.
Analysis must finish and export before visualization starts.
```

### Exit criteria
- All metrics collected with multiple runs
- Publication-quality figures
- Statistical significance confirmed or reported
- Manual audit documented
- **Update CLAUDE.md**

---

## Iteration 7: Thesis Writing (Weeks 13–16)

**Goal:** Write the thesis. Agent Teams for research/review support only.

### Writing order
1. Chapter 3 (JIT Pipeline)
2. Chapter 4 (Verification) — core contribution, most space
3. Chapter 5 (Granularity Optimization)
4. Chapter 6 (Evaluation)
5. Chapter 2 (Related Work)
6. Chapter 1 (Introduction) — write last
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
- **Final CLAUDE.md update** reflecting the completed project

---

## Cross-Cutting: When to Use What

| Situation | Use |
|---|---|
| Sequential setup, config, wiring | **Single Claude Code session** |
| Independent modules with clear interfaces | **Agent Teams** (2–3 teammates) |
| Core contribution needing adversarial review | **Agent Teams** (4 teammates with devil's advocate) |
| Quick code generation within a larger task | **Subagents** |
| Parallel baselines or analysis | **Agent Teams** (2–3 teammates) |
| Writing prose | **Single session** |

## Cross-Cutting: Git Strategy

- Branch per iteration: `iter/0-scaffolding`, `iter/1-registry`, etc.
- When using Agent Teams, each teammate owns different files — no shared file edits
- Merge to `main` at exit criteria

## Cross-Cutting: Risk Management

| Risk | Mitigation |
|---|---|
| LLM generation quality too low | Start with simple capabilities, increase complexity gradually |
| Verification too strict | Tune Tier 1 thresholds; let Tier 2 override "High" if behavioral tests pass |
| Verification too loose | Devil's advocate in Iter 3; manual audit in Iter 6 |
| Optimizer doesn't converge | Debug with simulation harness; simplify task distribution |
| Dynamic MCP server management flaky | Health checks + auto-restart in Iter 2 |
| Agent Teams overhead too high | Start with 2 teammates; scale to 3–4 only when justified |
| Scope creep | Exit criteria per iteration — meet them, don't gold-plate |

---

## Timeline Summary

| Iteration | Weeks | Focus | Agent Teams? |
|---|---|---|---|
| 0 | 1–2 | Scaffolding + Prove the Loop | No |
| 1 | 2–3 | Production Registry, Schemas & Metrics | Yes (3 teammates) |
| 2 | 3–5 | JIT Pipeline + Dynamic Server Mgmt | Yes (3 teammates, phased) |
| 3 | 5–7 | Verification Layer | Yes (4 teammates) |
| 4 | 7–9 | Granularity Optimizer | Yes (2 teammates) |
| 5 | 9–11 | Integration, E2E & Baselines | Yes (3 teammates) |
| 6 | 11–13 | Evaluation & Analysis | Yes (2 teammates) |
| 7 | 13–16 | Thesis Writing | Yes (review only) |

**Total: ~16 weeks**
