# Context Engineering Mapping

**Purpose:** Map the four context engineering primitives (Write, Select, Compress, Isolate) to the three artifact types (MCP_TOOL, SKILL, AGENT) across two layers: the **orchestration layer** (pipeline infrastructure) and the **generated-capability layer** (what the generated artifacts experience at execution time).

**Why this document exists:** The thesis domain is *"Context Engineering in LLM-Powered Multi-Agent Systems"* and context economy is one of three headline tensions in the proposal. This mapping is the implementation-level reference that Chapter 3/4/6 build on. It must be grounded in code, not aspirational.

**References:**
- Martin (2025) — Write/Select/Compress/Isolate primitives
- Zhang et al. (2025) — ACE framework, cited in proposal §2.2
- `docs/thesis_proposal.md` — the contract (do not modify)

---

## Layer 1: Orchestration (pipeline infrastructure)

These mechanisms apply to the pipeline itself — how the decomposer, classifier, generators, verifiers, and executor communicate. They are artifact-type-agnostic.

### Write

| Mechanism | Location | What it does |
|---|---|---|
| Pydantic state schemas | `src/schemas/*.py` (18 core + 5 eval types) | All inter-node state uses typed models. Replaces natural-language summaries and raw conversation histories with deterministically parseable JSON contracts. Per proposal §3.4: *"Agents within the pipeline communicate via strictly typed JSON schemas."* |
| Workspace JSON files | `src/orchestrator/composition.py` (`step_N_output.json`) | Chain steps write structured output to `/tmp/mint_workspace_<id>/step_N_output.json`. Downstream steps read upstream outputs as typed JSON. Per proposal §3.4: *"the 'world state' at each handoff point."* |
| SKILL.md on disk | `src/orchestrator/generators/skill.py` | Generated skills are written to `data/{eval,cli}/skills/<name>/SKILL.md`. Persistent, inspectable, version-trackable. |

### Select

| Mechanism | Location | What it does |
|---|---|---|
| ChromaDB hierarchical search | `src/registry/store.py` `_build_document()` | Embeddings use `"{domain} > {type} > {name}: {summary}"` format. Three-level hierarchy prevents false positives (keyword matches) and false negatives (semantically equivalent but differently named). |
| Gap detection threshold | `src/orchestrator/gap_detection.py` | Only capabilities above a confidence threshold (default 0.5) count as matches. Below-threshold results are gaps that trigger generation. Prevents noisy partial matches from polluting the pipeline. |
| Registry embedding summary | `src/registry/store.py` `_summarize_description()` | Descriptions truncated to 50 words for the embedding document. Full description preserved in metadata. Reduces embedding-time and search-time token costs. |

### Compress

| Mechanism | Location | What it does |
|---|---|---|
| β·token_cost optimizer penalty | `src/optimizer/utility.py` `compute_utility()` | Token cost is one of four metrics in the utility function. Creates soft evolutionary pressure toward smaller artifacts. Per proposal §5.1: *"token-constrained environments increase β."* |
| `fixture_contents` injection | `src/orchestrator/composition.py` `_build_chain_fixture_contents()` | SKILL/AGENT chain steps receive only upstream outputs + original fixtures as inline strings — not full conversation histories, not all registry metadata, not the pipeline's internal state. This is a Select+Compress decision: inject exactly what the downstream step needs. |

### Isolate

| Mechanism | Location | What it does |
|---|---|---|
| Fresh DeepAgent per invocation | `src/orchestrator/decomposition.py`, `src/verification/adversarial.py` | Each pipeline DeepAgent (decomposer, adversarial prober) is created fresh with its own `write_todos` scratchpad and file workspace. No state leaks between invocations. |
| Three execution backends | `tests/evaluation/execution.py`, `src/orchestrator/composition.py` | The classifier's routing decision determines the context budget: MCP_TOOL (0 LLM calls), SKILL (max 5 tool calls), AGENT (unbounded). Each backend has different context boundaries. |
| Docker `--network none` | `src/verification/sandbox_tool.py` | Code execution is sandboxed. Phase 2 runs with network for `pip install`; Phase 2 commits to an ephemeral image; Phase 3 (execution) runs `--network none`. |

---

## Layer 2: Generated-capability execution (per artifact type)

These mechanisms apply at runtime when a generated artifact is actually executed. This is where the three artifact types diverge and where the thesis's context-engineering claims become concrete.

### MCP_TOOL

**Context profile:** Deterministic code, 0 LLM calls at runtime. Context cost is only at registry-discovery time (source code stored in metadata) and generation time.

| Primitive | Mechanism | Status |
|---|---|---|
| **Write** | Source code stored in `registry.metadata["source_code"]`. Workspace `step_N_output.json` for chain output. | ✅ Implemented |
| **Select** | Registry search selects which tool to reuse. In chains, only upstream `step_N_output.json` files are mounted — not the full registry. | ✅ Implemented |
| **Compress** | β·token_cost optimizer penalty creates soft pressure toward smaller tools. No hard source-size limit — the MCP spec prescribes none, and the thesis proposal calls for soft pressure, not hard gates. | ✅ Implemented (soft) |
| **Isolate** | Docker sandbox with `--network none`. Workspace mounted at `/workspace` for chain steps. Separate from agent/skill in-memory workspaces. | ✅ Implemented |

**Why MCP_TOOL needs less context engineering:** With 0 LLM calls at runtime, there is no context window to manage during execution. The only context-engineering concern is registry storage cost (how much source code is stored and searched), which the optimizer handles via β·token_cost.

### SKILL

**Context profile:** Constrained LLM with SKILL.md as system prompt, max 5 `sandbox_execute` tool calls, no planning loop, no `write_todos`. Prescribed workflow execution.

| Primitive | Mechanism | Status |
|---|---|---|
| **Write** | SKILL.md on disk at `data/{eval,cli}/skills/<name>/SKILL.md`. Structured output extracted from sandbox JSON or follow-up structured output call. | ✅ Implemented |
| **Select** | In agent execution: progressive disclosure via `skills=` parameter — metadata loaded at discovery, full SKILL.md loaded only on activation (`tests/evaluation/execution.py:770-771`). In chains: `fixture_contents` injects only upstream outputs, not full pipeline state. | ✅ Implemented |
| **Compress** | Body < 500 lines per Agent Skills spec (`tools/quick_validate.py` warning). Generator prompt discipline cites 500-line / 5,000-token guideline (`src/orchestrator/prompts/skill_generator.py`). β·token_cost for long-term pressure. | ✅ Implemented |
| **Isolate** | Constrained tool budget (max 5 `sandbox_execute` calls). SKILL execution has no `write_todos`, no file-based context management, no planning loop — the context boundary is the system prompt + user message + tool results. | ✅ Implemented |

**Design choice — full body in system prompt:** The SKILL execution backend (`_execute_skill_inner` in `execution.py`) prepends the entire SKILL.md body into the system prompt. There is no within-body progressive disclosure (loading sections on demand). This is a deliberate simplification: the Agent Skills spec recommends < 500 lines, and at that size the full body fits comfortably in the context window of `gpt-5-mini` (128K tokens). Within-body progressive disclosure (a `read_skill_section()` tool) is deferred to future work (F3 in `pre_eval_execution_plan.md`) — it becomes valuable only when SKILL.md bodies routinely exceed 500 lines, which the Phase 3.5a body-length check is designed to prevent.

### AGENT

**Context profile:** Full DeepAgent with `sandbox_execute` + built-in file/planning tools (`write_todos`, `read_file`, `write_file`, `edit_file`, `ls`, `glob`, `grep`). Adaptive autonomous reasoning with unbounded tool calls.

| Primitive | Mechanism | Status |
|---|---|---|
| **Write** | Agent writes intermediate results to files via `write_file` (in-memory StateBackend workspace). Large results (>20K tokens) are auto-evicted to files by DeepAgents. Structured output via `response_format=PydanticModel`. | ✅ Implemented |
| **Select** | Progressive disclosure via `skills=` parameter: agent sees skill metadata at start, activates skills on demand, full SKILL.md loaded only when activated. In chains: `fixture_contents` injects upstream outputs. In evaluation: only the task's own skill is passed to avoid noise from other tasks' skills. | ✅ Implemented |
| **Compress** | `write_todos` for structured planning (compresses open-ended reasoning into a checklist). File-based intermediate results (compresses long tool outputs into files, referenced by name instead of carried in conversation). β·token_cost soft penalty. | ✅ Implemented |
| **Isolate** | Fresh `create_deep_agent()` per invocation — no state from previous tasks. Dual filesystem isolation: `write_file` operates on StateBackend (in-memory), `sandbox_execute` runs in Docker. System prompts document this boundary explicitly. **Lead-with-subagents** (Phase 3.5b): when ≥2 AGENT gaps exist, the lead delegates via `task()` tool; each subagent runs in isolated context, returning only its final result. | ✅ Implemented |

**Generated-capability-level Isolate (subagents) — implemented in Phase 3.5b:** When ≥2 gaps are classified as AGENT, the generator produces ONE lead agent + N subagent dicts (`src/orchestrator/generators/agent.py:_generate_lead_with_subagents()`). The lead delegates via the built-in `task` tool; each subagent runs in an isolated context and returns only its structured result. The compositional executor detects when multiple subtasks resolve to the same lead artifact and executes it only once (`_lead_cache` in `composition.py`). This is the canonical context-isolation pattern from DeepAgents — *"Subagents solve the context bloat problem... the main agent receives only the final result, not the dozens of tool calls that produced it"* (DeepAgents docs).

---

## Summary: what's implemented, what's deferred

| Primitive | Orchestration layer | MCP_TOOL | SKILL | AGENT |
|---|---|---|---|---|
| **Write** | ✅ Pydantic schemas, workspace JSON | ✅ Source in registry | ✅ SKILL.md on disk | ✅ StateBackend files, write_todos |
| **Select** | ✅ ChromaDB hierarchical, gap threshold | ✅ Registry search | ✅ Progressive disclosure (skills=), fixture_contents | ✅ Progressive disclosure, fixture_contents |
| **Compress** | ✅ β·token_cost, fixture_contents injection | ✅ Soft optimizer penalty | ✅ Body < 500 lines (spec), prompt discipline | ✅ write_todos, file-based intermediates |
| **Isolate** | ✅ Fresh DeepAgent per node, 3 backends | ✅ Docker --network none | ✅ Max 5 tool calls, no planning | ✅ Fresh agent, dual filesystem, lead-with-subagents (3.5b) |

**Deferred to future work (with rationale):**
- **F1 — Bundled resources** (`references/`, `scripts/`, `assets/`): becomes valuable when SKILL.md bodies exceed 500 lines. The Phase 3.5a body-length check prevents this for the current testbed.
- **F3 — Within-body progressive disclosure** (`read_skill_section()` tool): same trigger as F1. At < 500 lines, full-body injection is adequate.
- **F2 — Hard token budget**: proposal calls for soft pressure (β·token_cost), not hard gates. Phase 6 D4 counterfactual analysis provides calibrated data for future implementation.

**Implemented this session (Phase 3.5a):**
- SKILL body length warning in `tools/quick_validate.py` (< 500 lines per Agent Skills spec)
- SKILL generator prompt discipline (500-line / 5,000-token guideline)
- Registry description compression (50-word embedding summary for ChromaDB)

**Implemented this session (Phase 3.5b):**
- DeepAgents subagents for AGENT execution — the generated-capability-level Isolate primitive
- Lead-with-subagents generator mode (≥2 AGENT gaps → one coordinated LLM call)
- Resolution map + compositional executor dedup for lead artifacts
