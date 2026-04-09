# Compositional Capability Synthesis

**Date:** 2026-04-08 (initial), updated 2026-04-09 (Stage 4.5 added after Phase 2 retrospective)
**Branch:** `iter/6-execution-backends` → new branch `iter/6-compositional-synthesis`
**Status:** Stages 1-2 complete (landed in Phase 1 + Phase 2 of `pre_eval_execution_plan.md`); Stages 3-8 pending
**Supersedes (partially):** `docs/single_artifact_enforcement_plan.md`
**Cross-reference:** `docs/pre_eval_execution_plan.md` is the sequencing-and-validation layer; this doc is the architectural detail. Stage 1 ↔ Phase 1 Track 1a, Stage 2 ↔ Phase 2, Stages 3-4 ↔ Phase 3, **Stage 4.5 ↔ Phase 3.5b (added 2026-04-09)**, Stages 5-7 are absorbed into Phase 3 / Phase 3.5 verification, Stage 8 ↔ Phases 5-6.

---

## Relationship to the Thesis Proposal

The original thesis proposal (`docs/thesis_proposal.md`, Pillar 1) describes **per-gap classification and compositional execution**:

> *"Skill gap detection. Each required capability is matched against a skill registry... Gaps are identified as subtasks with no match"*
>
> *"Capability classification. Each gap is classified to determine what kind of artifact should be generated"*
>
> *"Sandboxed integration. Generated capabilities are integrated into the agent's available tool set and invoked to complete the original task."*

The `single_artifact_enforcement_plan.md` deviated from this by collapsing classification and generation to the task level, producing at most one artifact per task. That deviation was justified by evaluation tractability: the 18-task testbed labels each task with ONE target capability type, so per-gap accuracy had no ground truth.

**This plan restores the proposal's compositional model**, motivated by an empirical finding on 2026-04-08: the single-artifact pipeline produces **decorative registry matches** — capabilities that are matched and recorded as "invocations" but never actually executed. T14 is the concrete reproducer:

- Decomposition split T14 into 2 subtasks: `read-files-and-parse-csv-tsv` + `aggregate-spending-by-department`
- Registry matched seeded `csv-parser` for the first subtask
- Task-level classifier classified the WHOLE task as `mcp_tool`
- Generator produced a 127-line monolith that **re-implements CSV parsing from scratch** (verified: `import csv`, `csv.DictReader`, zero references to `csv-parser`)
- Metrics store recorded a fake invocation for `csv-parser` with `token_cost=0, latency_ms=0` specifically to prevent it from being pruned as idle (comment in `gap_detection.py:65-73` literally says so)
- `mint list` shows `csv-parser | Invocations: 3` but only 1 of those was a real execution (the other 2 are fake match records)

**Consequence for the thesis**: "registry reuse rate" is currently unmeasurable because it conflates "match intent" with "actual reuse". This plan fixes that by making matched capabilities actually execute as part of a composition.

---

## Problem

Three concrete failures of the current single-artifact design:

### 1. Decorative matches (T14 reproducer)

Partial registry matches (registry has SOME but not ALL required capabilities) produce ZERO actual reuse. The generated monolith re-implements the matched capability's functionality. This defeats Pillar 1 of the thesis.

### 2. Misleading metrics

`gap_detection_node` records fake `InvocationRecord`s for matched capabilities (`token_cost=0, latency_ms=0`) to keep them alive in the optimizer's prune logic. This:
- Inflates `csv-parser.invocations` from 1 to 3 in the CLI registry
- Corrupts the optimizer's `invocations_since()` prune signal
- Makes Chapter 6's "reuse rate" statistic meaningless

### 3. No compositional execution

The pipeline has no way to execute multiple capabilities in dependency order. `SubTask.depends_on` exists in the schema but is never read by any node. Tasks that are genuinely composed of multiple sub-capabilities (like T14: parse + aggregate) must be solved by a monolithic artifact.

This forces the generator to produce large, single-purpose artifacts that duplicate functionality from the registry — exactly the anti-pattern Pillar 3 (granularity optimization) is supposed to address.

---

## Approach: True Compositional Synthesis

Restore per-gap classification and add a compositional executor that runs artifacts in dependency order, passing outputs between them.

### High-level pipeline (target)

```
task
  → decomposition       (N subtasks with depends_on)
  → gap_detection       (per-cap registry search, produces matches + N gaps)
  → per-gap classifier  (classify each unmatched gap individually)
  → generators          (produce N artifacts for N gaps)
  → verification        (verify each artifact, retry per-artifact)
  → loading             (register all N artifacts)
  → compositional exec  (run matched + generated in dependency order)
  → validator           (check final output matches ground truth)
```

### Key changes from current single-artifact design

| Stage | Current | Target |
|---|---|---|
| Classifier | 1 call per task | 1 call per unmatched gap |
| Generators | Produce ≤1 artifact | Produce N artifacts (one per unmatched gap) |
| Verification | Verify 1 artifact | Verify N artifacts (still with 3-retry per artifact) |
| Execution | Run 1 artifact monolithically | Run artifacts in `depends_on` topological order, pass outputs |
| Registry match effect | Decorative (fake invocation, never executed) | Active (the matched capability is invoked as part of the chain) |
| EvaluationRecord | `capability_type` (singular) | `capability_types: list[str]`, `chain_length`, `matched_and_executed_count`, `generated_artifact_count` |

### What stays the same

- **Decomposition** unchanged. Already produces `subtasks` with `required_capabilities` and `depends_on`. Current implementation is advisory; this plan makes `depends_on` load-bearing.
- **Task-level classifier prompt structure** — three-layer framing preserved. Just applied per-gap instead of per-task.
- **LLM-chosen capability names** — the semantic naming refactor (`refactor: task-level classifier with semantic LLM-chosen names`) is compatible. Each gap gets its own semantic name.
- **Verification gate** — unchanged. Tier 0 (skills), Tier 1 MCP_TOOL-only, Tier 2 behavioral, Tier 3 adversarial. Applied per artifact.
- **Generator prompts** — minor revisions (tell the generator it's producing ONE step of a larger workflow, not the whole task).
- **Registry** — unchanged. Same ChromaDB store, same seed capabilities.
- **Docker sandbox** — unchanged. Same `sandbox_execute`, same image.
- **Single-artifact tests that pass today** — most still pass unchanged; we just add compositional tests alongside.

---

## Hard Problems to Solve

### HP1: Output passing between artifacts

When subtask B depends on subtask A, B needs A's output. Three concrete options:

**Option X: File-based workspace**
A writes output to `/workspace/step_N_output.json`. B reads it. The compositional executor maintains the workspace.

- Pros: works identically for MCP_TOOL / SKILL / AGENT. Sandbox-friendly (just a shared directory). Natural dependency chain.
- Cons: requires a workspace directory per task. Binary outputs (xlsx, parquet) need serialization. Generator must know about workspace convention.

**Option Y: In-memory dict passing**
Compositional executor holds outputs in memory, injects A's output as parameters to B.

- Pros: type-safe (Python dicts), no serialization
- Cons: Only works for same-process execution. Breaks for sandbox MCP tools (which run in Docker, separate process). Parameter matching is fragile (naming collisions between A's output keys and B's parameter names).

**Option Z: Typed artifact interface**
Each artifact declares `inputs` and `outputs` in its metadata. Executor wires A's outputs to B's inputs by schema matching.

- Pros: rigorous, proposal-aligned
- Cons: significant schema work, generator prompts become much stricter, LLM generation failures likely increase

**Decision: Option X (file-based workspace)** for the initial implementation. Simple, sandbox-compatible, debug-friendly. Type-unsafe but matches how the existing sandbox already works. Option Z is Chapter 7 future work.

**Workspace structure**:
```
/tmp/mint_workspace_<task_id>/
  step_0_output.json      ← first artifact's output
  step_1_output.json      ← second artifact's output
  ...
  fixtures/               ← symlinked or copied input fixtures
    t14_departments.csv
    t14_transactions.tsv
```

Each artifact is invoked with the workspace path. The generator prompts are updated to instruct each artifact to:
- Read its inputs from `workspace/fixtures/` OR `workspace/step_<K>_output.json` for K in its dependencies
- Write its output to `workspace/step_<N>_output.json`
- Return the output dict via the normal MCP/skill/agent return path (the executor uses BOTH the return value AND the written file for redundancy)

### HP2: Dependency resolution and execution order

Currently `SubTask.depends_on: list[int]` is populated but unused. The compositional executor needs to:

1. Build a DAG from `subtasks` and their `depends_on` indices
2. Topologically sort (Kahn's algorithm, stdlib `graphlib.TopologicalSorter`)
3. Map each subtask to either a matched capability or a generated artifact
4. Execute in order, passing workspace state

**Cycle handling**: detect cycles via `graphlib` — if present, log error and fall back to linear execution in subtask order.

**Missing artifact handling**: if a subtask has no matched capability and no generated artifact (e.g., verification failed for that gap after all retries), the chain fails. The validator sees an error and returns `False`.

### HP3: Mapping subtasks to artifacts

Current flow has a clean separation:
- `required_capabilities` (flat list of phrases)
- `matches` (registry hits, keyed by capability name)
- `classified_gaps` / `generated_artifacts` (the unmatched portion)

But a subtask references `required_capabilities` by phrase, not by registry name or generated artifact name. We need a mapping layer.

**Proposed structure**: after loading, build a per-task resolution dict:

```python
resolution: dict[str, CapabilityReference] = {
    "read-files-and-parse-csv-tsv": CapabilityReference(
        source="registry", name="csv-parser"
    ),
    "aggregate-spending-by-department": CapabilityReference(
        source="generated", name="department-spend-aggregator-d4ea26"
    ),
}
```

Each `SubTask` has `required_capabilities: list[str]`. For each, look up in `resolution`. If missing (classifier failed for that gap), the chain execution fails at that step.

`CapabilityReference` is a new schema added in `src/schemas/task.py`:

```python
class CapabilityReference(BaseModel):
    source: Literal["registry", "generated"]
    name: str  # capability_name
    capability_type: CapabilityType
    # Source code / skill content / agent config can be looked up via name
```

### HP4: Mixed-type execution

A compositional task might have:
- Step 0: MCP_TOOL (matched csv-parser)
- Step 1: MCP_TOOL (generated aggregator)
- Step 2: SKILL (generated reporting workflow)

Each type has a different executor:
- MCP_TOOL → `sandbox_execute()` via `ToolLoader` + Docker sandbox
- SKILL → `execute_skill_artifact()` (constrained LLM + sandbox)
- AGENT → `execute_agent_artifact()` (DeepAgent)

The compositional executor dispatches each step to the right backend based on the artifact's type. The workspace is shared across all steps regardless of type.

**Critical: the sandbox-based MCP_TOOL path runs in Docker**, which is a separate filesystem from the agent/skill workspace. Two strategies:

1. **Mount the workspace** into the Docker sandbox via `-v` flag. Simple but requires changing `sandbox_execute` to accept a workspace path.
2. **Copy files in/out** of the sandbox before/after each invocation. More work but matches the existing binary-file pattern (base64 inline transfer).

**Decision: Strategy 1 (mount workspace)**. Change `sandbox_execute` to optionally accept a `workspace_path` parameter that mounts the host workspace into `/workspace` in the sandbox. All artifact types then use the same workspace convention.

### HP5: Validator redesign

Current validators (`_validate_t14`, etc.) take the final output dict and check fields:

```python
def _validate_t14(output: dict) -> bool:
    return output.get("department_count") == 5 and ...
```

Under compositional execution, the "final output" is the output of the LAST artifact in the chain. If the chain has 3 steps, we validate step 2's output (the root of the DAG by topological order — the subtask with no dependents).

**Design**: the compositional executor returns a `ChainResult`:

```python
class ChainResult(BaseModel):
    final_output: dict  # the root artifact's output
    step_outputs: list[dict]  # all intermediate outputs
    chain_success: bool  # did every step execute without error
    failed_step_index: int | None  # if chain failed, which step
    failed_step_name: str | None
    execution_errors: list[str]
```

The validator operates on `final_output` exactly as before. New `EvaluationRecord` fields capture chain-level metadata:
- `chain_length: int`
- `chain_success: bool`
- `failed_step_index: int | None`
- `matched_and_executed_count: int`
- `generated_artifact_count: int`
- `capability_types: list[str]` (per-step types)

Existing `output_in_range` semantics preserved. Existing `capability_type` field keeps the "root artifact's type" for backwards compatibility.

### HP6: Testbed compatibility

The 18-task testbed was designed for single-artifact execution. Each task has ONE target `capability_type`. Under compositional execution:

- Tasks that genuinely decompose into multiple sub-capabilities (T14, T11, T17) should benefit — the chain can reuse registry matches and compose freshly generated artifacts
- Tasks that are truly single-capability (T01, T04) should still work — the "chain" is 1 step, either matched or generated
- Tasks where decomposition over-splits spuriously (T16 = 1 subtask, T06 = 1 subtask) will have chains of length 1 — identical to single-artifact behavior

**No test fixture changes required** if the compositional executor correctly handles chain length 1. This is a key design constraint: single-step chains must work identically to the current single-artifact path.

**Classification accuracy measurement**: under compositional mode, the "classified type" is the dominant type in the chain (root artifact's type, or majority if tied). This preserves the task-level accuracy metric from the single-artifact plan while enabling per-gap classification internally.

---

## What Does NOT Change

- **Decomposition agent** (unchanged) — still produces subtasks with `required_capabilities` and `depends_on`. The DeepAgent prompt is already correct; `depends_on` just stops being ignored.
- **Registry search semantics** — unchanged. Same ChromaDB similarity threshold, same seeded capabilities.
- **Verification gate** — unchanged. Tier 0/1/2/3 applied per artifact. Retries per artifact (up to 3 attempts).
- **Optimizer (Pillar 3)** — unchanged. Will see HONEST invocation metrics after this refactor, which improves prune/split/merge decisions but requires no code changes.
- **Task fixtures** — unchanged. `tests/evaluation/fixtures/` untouched. Validators unchanged (they operate on the final output of the chain root).
- **LLM model / provider** — unchanged. `openai:gpt-5-mini` default.
- **Sandbox Docker image** — minor change to mount workspace volume.
- **MINT CLI surface** — unchanged. Same `mint run`, same `mint list`, same output format. Internals change; UX doesn't.

---

## Files Affected

### Pipeline (restored to per-gap with compositional hookup)

| File | Change |
|---|---|
| `src/orchestrator/gap_detection.py` | **Revert** placeholder-gap behavior. Produce N enumerated `GapItem`s (one per unmatched required_capability), like before the single-artifact refactor. **Remove fake invocation recording** (no more `token_cost=0, latency_ms=0` writes). Still populates `state.matches` per hit, but no longer pollutes metrics. |
| `src/orchestrator/classifier.py` | **Revert** to per-gap classification. New `classify_gap(gap, total_gaps, task_context)` replaces `classify_task`. `classifier_node` iterates gaps, producing N `ClassifiedGap` entries. Keep LLM-chosen `capability_name` per gap. |
| `src/orchestrator/prompts/classifier.py` | Adjust prompt to classify a single gap (not a whole task). Keep three-layer framing. Task context passed as background. Few-shot examples updated to single-gap focus. |
| `src/orchestrator/prompts/{mcp_tool,skill,agent}_generator.py` | Update to frame each artifact as ONE STEP of a composition. Instructions: read inputs from `workspace/fixtures/` or `workspace/step_<K>_output.json` per dependencies, write output to `workspace/step_<N>_output.json`. |
| `src/orchestrator/generators/{mcp_tool,skill,agent}.py` | Revert to iterating over gaps and producing N artifacts. Each artifact gets a `gap_query` tag for later resolution. |
| `src/orchestrator/verification_node.py` | Per-artifact verification (already supported). Retry loop is already per-artifact via `gap_query`. Minor cleanup if needed. |
| `src/orchestrator/loading_node.py` | Register all N artifacts (already supported). Build `resolution` map from matches + generated artifacts. |
| `src/orchestrator/pipeline.py` | No structural change to the graph (same 9 nodes). |

### New compositional execution layer

| File | Change |
|---|---|
| `src/orchestrator/composition.py` | **NEW**. `CompositionalExecutor` class. Takes `subtasks`, `resolution` map, and fixture paths. Builds DAG, topologically sorts, executes in order with workspace, dispatches to the right backend per artifact type. Returns `ChainResult`. |
| `src/schemas/task.py` | Add `CapabilityReference` (source, name, capability_type) and `ChainResult` (final_output, step_outputs, chain_success, failed_step_index, execution_errors). |
| `src/verification/sandbox_tool.py` | Add optional `workspace_path` parameter to `sandbox_execute()`. When provided, mount it into the Docker container at `/workspace`. |
| `tests/evaluation/execution.py` | Add `execute_mcp_tool_in_chain()` helper that loads + invokes a registered MCP tool with the workspace convention. Refactor `execute_skill_artifact` and `execute_agent_artifact` to accept a `workspace_path` and pass it to the sandbox. |

### Harness + CLI integration

| File | Change |
|---|---|
| `tests/evaluation/harness.py` | `_extract_result` branches on `len(generated_artifacts) + len(matches) > 1` → use compositional executor. Otherwise, fall back to single-artifact path (unchanged). |
| `src/cli/runner.py` | Similar: if compositional, run `CompositionalExecutor`; else current single-artifact flow. Update display to show chain steps. |
| `src/cli/display.py` | New chain visualization: `Step 1/3: csv-parser (registry match) → Step 2/3: department-aggregator (generated MCP_TOOL) → Step 3/3: ...` |

### Metrics + evaluation

| File | Change |
|---|---|
| `src/schemas/evaluation.py` | Add `chain_length`, `chain_success`, `matched_and_executed_count`, `generated_artifact_count`, `capability_types: list[str]`, `failed_step_index`, `failed_step_name` fields. Keep `capability_type` (singular) for backwards compat — set to the chain root's type. |
| `src/registry/evaluation_store.py` | DuckDB schema migration: add columns for the new fields. ALTER TABLE if DB exists; CREATE TABLE with new columns for fresh DBs. |
| `src/registry/metrics.py` | No changes — invocation recording is already correct. We just stop writing FAKE invocations from `gap_detection`. |

### Tests

| File | Change |
|---|---|
| `tests/unit/test_gap_detection.py` | Revert single-placeholder-gap tests. Update to assert N-gaps output. Remove fake-invocation assertions. Add assertion: no invocations are recorded by gap_detection at all. |
| `tests/unit/test_classifier.py` | Restore `classify_gap` tests. Keep `classify_task` as a thin wrapper that iterates if we want to preserve the single-call path for chain-length-1. |
| `tests/unit/test_pipeline.py` | Update single-classified-gap assertion to per-gap assertion. |
| `tests/unit/test_composition.py` | **NEW**. Tests for `CompositionalExecutor`: DAG building, topological sort, workspace management, mixed-type chain, chain failure handling, cycle detection. |
| `tests/unit/test_schemas.py` | Tests for new `CapabilityReference` and `ChainResult`. |
| `tests/unit/test_harness.py` | Update `_extract_result` tests for compositional branching. |
| `tests/evaluation/tasks.py` | No changes. |
| `tests/integration/test_composition.py` | **NEW**. End-to-end integration test: generate a 3-step chain (T14-style), verify matched csv-parser is actually executed (non-zero latency_ms), verify output is correct. |

### Documentation

| File | Change |
|---|---|
| `docs/compositional_synthesis_plan.md` | This file. |
| `CLAUDE.md` | Update classifier table row, stable interfaces, known findings. Note that the single-artifact deviation from the proposal has been reverted and full compositional synthesis is now implemented. |
| `docs/single_artifact_enforcement_plan.md` | Add a "Superseded" banner at the top pointing to this plan. Keep the doc for historical context — Chapter 3 references it. |

### What does NOT need changes

- `tests/evaluation/fixtures/` — no fixture changes
- `tests/evaluation/tasks.py` — no task definition changes (validators unchanged, chain of length 1 == single-artifact)
- `src/optimizer/*` — no changes (optimizer metrics become honest automatically)
- `src/verification/{static_scanner,behavioral,adversarial,gate}.py` — no changes
- `sandbox/` (Docker image) — only volume mount wiring in `sandbox_execute`, not the image itself

---

## Implementation Stages

Each stage is independently testable and reversible. Commit at each stage boundary.

### Stage 1: Schema additions + honest metrics

**Goal**: stop writing fake invocations, add the new EvaluationRecord fields. No pipeline behavior changes yet.

**Changes**:
- `src/orchestrator/gap_detection.py` — remove the fake `InvocationRecord` recording block. Log a warning instead: "matched capabilities are recorded but not invoked at match time".
- `src/schemas/task.py` — add `CapabilityReference` and `ChainResult` schemas.
- `src/schemas/evaluation.py` — add `chain_length`, `chain_success`, `matched_and_executed_count`, `generated_artifact_count`, `capability_types`, `failed_step_index`, `failed_step_name` fields. Defaults chosen so existing code still populates them correctly (`chain_length=1`, `chain_success=gate_passed and output_in_range`, etc.).
- `src/registry/evaluation_store.py` — DuckDB schema migration. New fresh DBs get the new columns. Existing DBs get `ALTER TABLE ADD COLUMN` statements with safe defaults.
- `tests/evaluation/harness.py` — `_extract_result` populates the new fields with single-artifact-compatible defaults.

**Tests**:
- `test_gap_detection.py` — assert NO invocations are recorded. Existing tests must still pass.
- `test_schemas.py` — new Pydantic tests for `CapabilityReference` and `ChainResult`.
- `test_evaluation_store.py` — schema migration test.

**Verification**:
- Full unit suite passes
- Run a pilot smoke test — T01 still works (chain-length 1), mint list shows csv-parser with 1 REAL invocation (no more fake ones)

**Deliverable**: Stage 1 commit. No behavior regression for single-artifact path. Metrics are honest.

---

### Stage 2: Revert per-gap classifier + generators

**Goal**: restore per-gap classification and multi-artifact generation. Pipeline now produces N artifacts per task. Execution is still single-artifact (priority sort).

**Changes**:
- `src/orchestrator/gap_detection.py` — produce N enumerated `GapItem`s, one per unmatched capability (like before the single-artifact plan). Keep per-capability registry matching.
- `src/orchestrator/classifier.py` — new `classify_gap(gap, task_context)` function. `classifier_node` iterates `state.gaps`, producing N entries in `state.classified_gaps`. Keep LLM-chosen `capability_name` per gap. `classify_task` remains as a convenience wrapper for the single-gap case but is no longer the primary entry point.
- `src/orchestrator/prompts/classifier.py` — adjust prompt to classify a single gap within the context of the whole task. Few-shot examples updated.
- `src/orchestrator/generators/{mcp_tool,skill,agent}.py` — iterate over `classified_gaps` filtering by type. Each gap produces one artifact. Artifact's `gap_query` field is the gap's query (LLM-chosen name).
- `src/orchestrator/prompts/{mcp_tool,skill,agent}_generator.py` — update to describe the artifact as ONE STEP of a workflow, not the whole task. Include the full task description as context.
- Execution remains single-artifact via priority sort in `tests/evaluation/harness.py` and `src/cli/runner.py`. This preserves the pilot pass/fail semantics while the compositional executor is being built.

**Tests**:
- `test_classifier.py` — restore per-gap classification tests. Mix with task-level for backwards compat.
- `test_gap_detection.py` — assert N-gaps output when N capabilities are unmatched.
- `test_pipeline.py` — assert multiple classified_gaps are possible.
- `test_mcp_tool_generator.py`, `test_skill_generator.py`, `test_agent_generator.py` — multi-gap generation tests.
- Pilot sanity check: T01 (1 gap) still works, T14 now generates 2 artifacts (one for parse, one for aggregate) — verified via LangFuse trace.

**Verification**:
- Full unit suite passes (after test updates)
- Pilot T14: generates 2 MCP_TOOL artifacts but only 1 executes (priority sort picks one). **This is a temporary regression we knowingly accept** — the chain will execute in Stage 3.

**Deliverable**: Stage 2 commit. Pipeline produces multiple artifacts; execution is still single-artifact. Existing tests pass; compositional executor does not yet exist.

---

### Stage 3: CompositionalExecutor + workspace plumbing

**Goal**: implement the compositional executor. NOT YET wired into harness or CLI.

**Changes**:
- `src/orchestrator/composition.py` — NEW file. `CompositionalExecutor` class:
  - `__init__(self, subtasks, resolution, fixture_paths, metrics_store=None)`
  - `build_dag() -> TopologicalSorter` — from `subtasks[*].depends_on`
  - `build_workspace(task_id) -> Path` — creates `/tmp/mint_workspace_<task_id>/`, copies fixtures
  - `execute_step(step, workspace_path) -> dict` — dispatches by `CapabilityType`
  - `run() -> ChainResult` — topological execution loop
  - Records REAL invocations via `metrics_store` (not fake)
- `src/verification/sandbox_tool.py` — add optional `workspace_path: Path | None = None` param. When provided, add `-v {workspace_path}:/workspace` to the Docker invocation. Code inside the sandbox can read/write `/workspace/*` normally.
- `tests/evaluation/execution.py` — add `execute_mcp_tool_in_chain()` helper that uses `ToolLoader` to invoke a matched or generated MCP_TOOL with workspace context. Update `execute_skill_artifact` and `execute_agent_artifact` to accept a `workspace_path` keyword argument.

**Tests**:
- `tests/unit/test_composition.py` — NEW file:
  - DAG building from subtasks with depends_on
  - Topological ordering (single-step, linear 3-step, diamond, invalid cycle)
  - Workspace creation + fixture copy
  - Mock execution: each step returns a dict; verify chain threading
  - Cycle detection raises a clear error
  - Missing artifact handling: if a subtask's required_capability has no resolution, chain fails with a clear error
  - Mixed-type chain: step 0 MCP_TOOL (registry), step 1 MCP_TOOL (generated), step 2 SKILL
  - Failure in middle of chain stops execution and returns partial results
- `tests/unit/test_sandbox_tool.py` — add tests for workspace mounting (can just assert the `-v` flag is present in the Docker command; don't actually run Docker in unit tests)

**Verification**:
- `test_composition.py` full coverage
- Manual smoke test: instantiate `CompositionalExecutor` with a mocked 3-step chain, verify workspace is created, files flow correctly
- No regression in existing tests (composition module is not yet invoked by harness/CLI)

**Deliverable**: Stage 3 commit. Compositional executor exists as a standalone module with full unit tests. Not yet wired into the pipeline.

---

### Stage 4: Wire compositional executor into harness + CLI

**Goal**: replace single-artifact execution with compositional execution for multi-step cases.

**Changes**:
- `tests/evaluation/harness.py` — in `_extract_result`, if the task has multiple subtasks AND the resolution map can cover all of them, route to `CompositionalExecutor`. Otherwise fall back to the existing single-artifact execution path.
  - Build the resolution map: for each required_capability, check if it's in `state.matches` (→ use registry capability) or in `state.generated_artifacts` (→ use generated artifact). If neither, the chain fails with "unresolved capability".
  - Pass fixture paths and the resolution map to `CompositionalExecutor`.
  - Record `chain_length`, `chain_success`, `matched_and_executed_count`, `generated_artifact_count`, `capability_types` in the `EvaluationRecord`.
- `src/cli/runner.py` — same routing logic. Update progress display to show chain steps via `src/cli/display.py`.
- `src/cli/display.py` — new chain visualization. Show each step's label, capability name, source (registry / generated), and status.
- `src/orchestrator/loading_node.py` — after loading, build the resolution map and attach it to `state` (new field on `TaskState`). This avoids rebuilding it in both the harness and the CLI.
- `src/schemas/task.py` — add `resolution: dict[str, CapabilityReference] | None = None` to `TaskState`.

**Tests**:
- `tests/unit/test_harness.py` — new tests for compositional dispatch
- `tests/unit/test_loading_node.py` — resolution map construction
- `tests/integration/test_composition.py` — NEW integration test: create a 3-step chain, run it through the harness, assert csv-parser has a REAL invocation (non-zero latency_ms).

**Verification**:
- Pilot re-run: T14 generates 2 MCP tools, chain executes with csv-parser AND the aggregator, output is correct, csv-parser shows real invocation with non-zero latency_ms in `data/cli/metrics.duckdb`.
- Check `mint list` — csv-parser invocations are all real.

**Deliverable**: Stage 4 commit. Compositional execution is live for multi-step tasks. Single-step tasks still use the fast single-artifact path.

---

### Stage 4.5: DeepAgents subagents for AGENT-typed compositional execution

**Status**: Added 2026-04-09 after the Phase 2 verification gate exposed a context-engineering gap. See `docs/pre_eval_execution_plan.md` Phase 3.5b for the integrated execution-plan view; the detail below is the architectural design and lives in this plan because it is a Stage 4 follow-up.

**Goal**: when the compositional executor handles an agent-typed chain (i.e., one or more gaps classified as AGENT, including over-decomposed multi-step agent tasks), wrap the chain in a DeepAgents lead-with-subagents pattern instead of running each agent as an independent standalone DeepAgent process. Directly addresses the proposal's third tension (*"context economy in a MAS where agents pass context to each other"*) at the generated-capability layer.

**Background** — why this stage exists:

The original Stage 3/4 design treats AGENT-typed artifacts uniformly with MCP_TOOL and SKILL: each is a single artifact in a dependency-ordered chain, executed sequentially with workspace state passed via JSON files. This works correctly for single-agent tasks but is suboptimal for multi-step agent tasks (e.g. an over-decomposed cascade investigation), because each standalone agent gets a fresh planning context, fresh `write_todos` plan, fresh conversation history, and no awareness of what its sibling agents have done. The agents communicate only through serialised workspace JSON, which is the *workflow with agent steps* pattern from the LangChain multi-agent guide — not the *agent with subagents* pattern that DeepAgents was designed for.

The canonical DeepAgents subagent pattern (per [the subagents documentation](https://docs.langchain.com/oss/python/deepagents/subagents.md)) provides **context quarantine** — the lead agent's context window receives only the structured return values from subagent invocations, not the dozens of intermediate tool calls that produced them. Per the [LangChain multi-agent guide](https://docs.langchain.com/oss/python/langchain/multi-agent/index.md), subagents process *"67% fewer tokens than skills"* in multi-domain scenarios due to this isolation. This is the canonical architectural answer to the proposal's third tension.

CLAUDE.md previously listed compositional agents as Chapter 7 future work, citing "evaluation tractability" — the concern was that switching agent execution backends mid-thesis would change eval results in ways hard to attribute. On re-examination during Phase 2, this concern turned out to be weaker than it sounded: the subagent vs standalone choice does not break any of the four ablation conditions (static / no_verification / no_optimization / full) because all four conditions see the same agent backend uniformly. The choice shifts the absolute headline number for the full system, but does not affect the deltas between conditions, which are the load-bearing eval signal. So compositional agents can be promoted from Chapter 7 future work into the implementation without breaking the eval design.

**Changes**:

- `src/schemas/task.py` — add subagent representation. Two options:
  - **Option A (preferred)**: extend `GeneratedArtifact.agent_config` (which is `dict | None`) to optionally include a `subagents` key carrying a list of subagent dicts matching the DeepAgents `SubAgent` spec (`name`, `description`, `system_prompt`, `tools`, `model`). No new top-level schema field; backwards compatible with single-agent configs.
  - **Option B (alternative)**: add a new top-level `CapabilitySubAgent` Pydantic model and a `subagents: list[CapabilitySubAgent] | None = None` field on `GeneratedArtifact`. Stricter typing but breaks the existing `agent_config` shape.
  - Choose Option A for the smaller schema-migration footprint.
- `src/orchestrator/generators/agent.py` — when the parent task has multiple gaps classified as `AGENT` in `state.classified_gaps` (i.e., the agent-task was decomposed into multiple subtasks instead of being collapsed by decomposer rule 10), generate ONE coordinated lead config + N subagent dicts in a single LLM call, instead of N independent agent configs. Single-gap agent tasks continue to produce a single standalone agent config (no subagents).
- `src/orchestrator/prompts/agent_generator.py` — add a "lead-with-subagents" mode to the prompt. New structured-output schema field `subagents: list[SubAgentSpec] | None`. Few-shot example showing a multi-step agent task decomposed into a lead + subagents (e.g., a cascade investigation lead with separate "data-loader" and "anomaly-detector" subagents that the lead delegates to via the `task` tool).
- `tests/evaluation/execution.py:_execute_agent_inner` — when `agent_config.get("subagents")` is non-empty, pass them to `create_deep_agent(..., subagents=[...])`. The DeepAgents library wires up the `task` tool automatically from the subagent list; the lead agent can then delegate to subagents during execution.
- `src/orchestrator/composition.py` (the new compositional executor module from Stages 3-4) — when handling an agent-typed chain, route to the lead-with-subagents path instead of running standalone agents in sequence. For the agent-task case, the "chain" collapses to a single lead-agent invocation with subagents pre-bound; the compositional executor's job for agent-task chains becomes "build the lead config from the chain, invoke once".
- `src/orchestrator/loading_node.py` — when registering a lead-with-subagents agent capability, the registry entry represents the whole composition (lead + subagents). Subagents are not separately searchable in the registry; they exist only as part of the lead's configuration. The capability descriptor's `description` field summarises the lead's purpose.
- (Optional, deferred to Stage 5) Generator prompt awareness of subagent composition for cross-stage workspace conventions — but agent subagents communicate via the `task` tool's structured return values, not via workspace JSON files, so the Stage 5 workspace-convention prompt updates are less relevant for agent subagents than for tool/skill chains.

**Tests**:

- `tests/unit/test_agent_generator.py` — add multi-gap-agent-task test verifying the generator produces lead+subagents instead of N independent agents. Mock the LLM, assert the structured output contains a `subagents` field with N entries.
- `tests/unit/test_composition.py` — add an agent-task-chain test verifying the compositional executor's branch logic (multi-gap agent task → lead-with-subagents path; single-gap agent task → standalone agent path).
- `tests/integration/test_pipeline_e2e.py` (or new `tests/integration/test_subagents.py`) — end-to-end T16-style run, verify the lead agent's LangFuse trace contains subagent invocations via the `task` tool and that the subagent contexts do not bloat the lead's context window (manual inspection of token counts in the trace).

**Verification**:

- Unit suite passes
- `uv run scripts/run_pilot.py --tasks T16 --condition full --n-runs 1` — T16 should now produce 1 lead agent + N subagents in the generated_artifacts list (or, if decomposer rule 10 is also tightened in Phase 3.5c, T16 collapses to a single standalone agent and the subagent code path is not exercised on this specific task — exercise it on a deliberately multi-step agent task instead)
- LangFuse trace for T16 shows the lead's `task` tool calls invoking the subagents — not 4 standalone DeepAgent processes
- Token cost per agent task should drop relative to the Phase 3 baseline (subagent context quarantine: ~67% fewer tokens per the LangChain multi-agent guide for similar isolation patterns)

**Deliverable**: Stage 4.5 commit. AGENT-typed compositional execution uses DeepAgents subagents for context quarantine. The other capability types (MCP_TOOL, SKILL) continue to use the Stage 4 standalone-in-chain pattern via workspace JSON, because their context-bloat surfaces are different (no in-loop tool-call accumulation to quarantine).

---

### Stage 5: Update generator prompts for composition-awareness

**Goal**: generated artifacts know they're part of a chain and produce composable outputs.

**Changes**:
- `src/orchestrator/prompts/mcp_tool_generator.py` — add a section to the prompt:
  - "Your tool is one step of a larger workflow. The full task is: {task_context}. Your step's specific job is: {gap_description}."
  - "Input data: you can find input files at `workspace/fixtures/{filename}` and outputs from previous steps at `workspace/step_{K}_output.json` for each K in your dependencies (dependency step indices: {depends_on})."
  - "Write your output to `workspace/step_{N}_output.json` as a JSON dict. Also return the same dict from your MCP tool function so callers have both file-based and direct access."
  - Update few-shot example to show the workspace convention.
- `src/orchestrator/prompts/skill_generator.py` — similar updates for SKILL.md.
- `src/orchestrator/prompts/agent_generator.py` — similar updates for agent config. Agents have the extra freedom to use `write_file` / `read_file` on workspace files.
- `src/orchestrator/generators/{mcp_tool,skill,agent}.py` — pass the new context fields (`task_context`, `depends_on`, `step_index`, `workspace_path_hint`) into the prompt template.

**Tests**:
- Update generator unit tests to verify the new context fields are passed through.
- Pilot regeneration: run T14 through the pipeline and verify both generated tools use the workspace convention (they should reference `workspace/` in their code).

**Verification**:
- T14 regeneration with fresh LLM calls produces tools that read from `workspace/fixtures/` and write to `workspace/step_*_output.json`.
- T01 (single-step) generator still works without workspace context (the prompt falls back to the old behavior when `depends_on` is empty and the chain has only 1 step).

**Deliverable**: Stage 5 commit. Generators are composition-aware.

---

### Stage 6: Re-run pilot, validate, iterate

**Goal**: ensure the compositional pipeline is working correctly before the full eval.

**Changes** (if needed):
- Any bugs discovered during the pilot
- Edge cases revealed by real compositional runs

**Tests**:
- Pilot spot-check: `uv run scripts/run_pilot.py --tasks T01,T04,T11,T14,T16,T17 --condition full --n-runs 1`
- Spot-check each task's LangFuse trace:
  - T01: chain length 1 (registry match), csv-parser REAL invocation, correct output
  - T04: chain length 1 (generated skill), skill executes, correct output
  - T11: chain length 3? (join, aggregate, compare), all steps execute, correct output
  - T14: chain length 2 (csv-parser registry + aggregator generated), csv-parser invoked with non-zero latency, correct output
  - T16: chain length 1 (agent), agent executes, correct cascade output
  - T17: chain length depends on decomposition

**Verification**:
- All 6 pilot tasks pass with correct outputs
- `data/cli/metrics.duckdb` shows no fake invocations (all token_cost > 0 or latency_ms > 0 for invocation records)
- `csv-parser.invocations` count matches actual execution count

**Deliverable**: Pilot validation report (verbal, not a commit). Iterate on bugs.

---

### Stage 7: Full unit test suite update

**Goal**: bring all 887+ unit tests to green.

**Changes**:
- Update any unit tests that assumed single-artifact behavior (classifier, gap_detection, pipeline, harness, runner)
- Update mocked `ClassificationResult` constructions to include the per-gap classification semantics
- Remove tests that asserted fake invocation recording
- Add any missing compositional test coverage

**Tests**:
- Target: all unit tests pass
- Target: coverage for `CompositionalExecutor` is ~90%+
- Target: no test asserts `matched_in_registry_count` is incremented without a real execution

**Verification**:
- `uv run pytest tests/unit/` — all tests pass
- `uv run pytest tests/integration/` — all integration tests pass

**Deliverable**: Stage 7 commit with all tests updated.

---

### Stage 8: Documentation + full eval re-run

**Goal**: update docs and produce Chapter 6 numbers.

**Changes**:
- `CLAUDE.md` — update classifier table row, stable interfaces, findings section. Add a new "Finding: Compositional synthesis restored" subsection with the pre/post reuse rate comparison.
- `docs/single_artifact_enforcement_plan.md` — add a "Superseded" banner pointing to `compositional_synthesis_plan.md`.
- `docs/compositional_synthesis_plan.md` (this file) — mark as Status: Implemented, add the empirical results from the full eval.
- Full eval: `uv run scripts/run_pilot.py --all-tasks --all-conditions --n-runs 5` (360 records, ~$5, ~26h)

**Tests**:
- Full eval completes with 360 records in `data/eval/metrics.duckdb`
- Reuse rate is measurable: `matched_and_executed_count / matched_and_executed_count + generated_artifact_count`
- Chapter 6 figures can be produced

**Verification**:
- `uv run scripts/analyze_results.py --csv` — statistical analysis
- Spot-check 5-10 traces in LangFuse to verify chain execution is working correctly at scale

**Deliverable**: Stage 8 commit. Full eval complete. Chapter 6 numbers produced.

---

## Edge Cases

### EC1: Chain of length 1 (no composition needed)
Single-step chains must behave identically to the single-artifact path. The CompositionalExecutor handles length-1 chains as a special case: skip DAG building, just invoke the one artifact with workspace context.

### EC2: Decomposition produces 0 subtasks
Fallback: treat the task description as a single subtask. `depends_on = []`, length-1 chain.

### EC3: All capabilities matched in registry (no generation needed)
Resolution map points all subtasks to registered capabilities. CompositionalExecutor runs them in dependency order. No generation happens. The optimizer sees real invocations for the matched capabilities.

### EC4: No capabilities matched, full generation needed
Current single-artifact behavior approximated: N gaps → N artifacts → N-step chain. If decomposition over-splits, the chain is artificially long. Future work: a chain collapsing pass that merges steps with no inter-dependencies.

### EC5: Partial match (T14 case)
The motivating example. Resolution map mixes registry and generated sources. Both types execute in dependency order. csv-parser runs as step 0, department-aggregator runs as step 1 reading csv-parser's output from `workspace/step_0_output.json`.

### EC6: Cycle in depends_on
Decomposition agent produces a cycle (shouldn't happen with the current prompt, but possible). `graphlib.TopologicalSorter.prepare()` raises `CycleError`. CompositionalExecutor catches it, logs, and falls back to linear execution in subtask order (preserving input order).

### EC7: Missing dependency (subtask references unresolved capability)
Classifier or generator failed for one of the gaps. The resolution map has no entry for that capability. CompositionalExecutor detects the missing resolution at build time and fails the chain with a clear error: `"Subtask 'compute-statistics' references unresolved capability 'statistical-summarizer'"`. EvaluationRecord gets `chain_success=False, failed_step_name='compute-statistics'`.

### EC8: Artifact execution fails mid-chain
Step K returns an error. CompositionalExecutor returns a `ChainResult` with `chain_success=False, failed_step_index=K, failed_step_name=..., step_outputs=[output_0, ..., output_{K-1}]`. The validator sees no `final_output` and fails with `failure_reason="chain_failed_step_{K}"`.

### EC9: Registry capability has no executable source code
Seeded capability metadata lacks `source_code`. CompositionalExecutor falls back to `_run_static_implementation` if available (T01-T03 have it), otherwise fails with `"Capability 'X' has no executable source"`.

### EC10: Type mismatch between chain steps
Step K outputs `{"row_count": 5}` but step K+1 expects a `{"rows": [...]}` input. Under the file-based workspace design, the workspace file is present but the downstream generator might read the wrong fields. This is a generator prompt quality issue, not a framework issue. Future work: typed IO schemas (HP1 Option Z).

### EC11: Concurrent modification of workspace
If two steps write to `workspace/step_{N}_output.json` for the same N, the second wins. Steps are executed sequentially in the current design, so this only happens on race conditions within a single step's code. Not a concern for the sequential executor.

### EC12: Workspace cleanup
Workspace is created in `/tmp/mint_workspace_<task_id>/`. Cleanup strategy: delete after successful validation, keep on failure for debugging. Add a `--keep-workspace` flag to the CLI for development.

---

## Risk Analysis

| Risk | Severity | Mitigation |
|---|---|---|
| Compositional executor bugs cause silent wrong outputs | **High** | Comprehensive unit tests; each stage has a verification step; validator catches most bugs; LangFuse trace spot-checks before the full eval |
| Generator produces non-composable artifacts (e.g., hardcoded paths) | **High** | Stage 5 prompt updates + few-shot examples showing workspace convention; pilot regeneration validates; iterate on bugs |
| Docker workspace mounting breaks on macOS (permissions, paths) | **Medium** | Test on the development machine before Stage 3 commit; use the existing sandbox binary-file pattern as a reference |
| Token cost increases due to multiple generators per task | **High** | Accept it. Compositional synthesis is inherently more expensive than monolithic when nothing is reused. The thesis finding is "reuse rate compensates" — if reuse is high enough, total cost per task goes down. If not, the trade-off is explicit in Chapter 6. |
| Verification gate runs redundantly per artifact, tripling cost | **High** | Unchanged. Each artifact still goes through Tier 1-3 independently. This is the cost of the proposal's design. |
| 18-task testbed was designed for single-artifact and doesn't exercise compositional behavior | **Medium** | Most tasks are naturally single-step; T11, T14, T17 are the multi-step cases. If the testbed is too single-step-biased, consider adding 2-3 new compositional tasks in Chapter 6. |
| Full eval invalidated; need ~$5 + ~26h to re-run | **Known** | Budgeted. Stage 8 re-run is the final cost. |
| Pipeline state schema changes break existing eval records | **Medium** | DuckDB schema migration with ALTER TABLE; existing records get NULL for new columns. `EvaluationStore._row_to_record` handles missing columns gracefully. |
| Classifier accuracy metric becomes ambiguous (per-gap vs per-task) | **Medium** | Report BOTH metrics in Chapter 6. Primary metric: per-task (root artifact type). Secondary: per-gap (micro-accuracy across all gaps). |
| Optimizer behavior changes because metrics are now honest | **Low** | Positive change. The optimizer will make better prune/split/merge decisions with real invocation data. |
| Mixed-type chains have unexpected interaction failures (MCP_TOOL output feeding SKILL input) | **Medium** | Each backend reads/writes the same workspace convention. Mixed-type integration test in Stage 4. |

---

## Cost Analysis

### Token costs per multi-step task (rough estimate)

**Current (single-artifact)**:
- Decomposition: ~3-10K tokens (unchanged)
- Task classification: ~500 tokens
- Generation: ~3K tokens × 1 artifact = 3K
- Verification Tier 2/3: ~2K tokens × 1 artifact = 2K
- Execution: ~1-3K tokens (skill/agent) or ~0 (MCP tool)
- **Total: ~10-18K tokens per task**

**Compositional (N=3 chain, 1 registry match, 2 generated)**:
- Decomposition: ~3-10K tokens (unchanged)
- Per-gap classification: ~500 × 2 unmatched gaps = 1K
- Generation: ~3K × 2 generated artifacts = 6K
- Verification Tier 2/3: ~2K × 2 artifacts = 4K
- Execution: ~1-3K tokens per skill/agent step, ~0 per MCP tool. For a 3-step chain with 1 MCP + 1 MCP + 1 SKILL: ~1-3K
- **Total: ~15-24K tokens per task**

**Delta**: +5-6K tokens per multi-gap task compared to the single-artifact monolith approach.

**BUT**: registry reuse savings.
- If step 0 is a registry match, we avoid generating an artifact for it: saves ~3K generation + ~2K verification = ~5K tokens
- If multiple matches, savings compound

**Break-even**: a compositional task with 1+ registry matches has approximately the same token cost as the single-artifact equivalent. Tasks with 2+ registry matches are cheaper compositionally.

**Over 360-record evaluation** (assuming ~30% of tasks have ≥1 registry match):
- ~108 tasks × ~5K tokens saved = **~540K tokens saved**
- ~252 tasks × ~5K tokens extra = **~1.26M tokens extra**
- **Net: +720K tokens**, approximately +$2 at current gpt-5-mini rates

This is a real cost. The thesis justification is Chapter 6 reuse rate analysis: if reuse rate is high, compositional is cheaper; if low, it's not. The explicit measurement is the contribution.

### Sandbox time estimates

**Current**: ~1 sandbox invocation per task (monolithic execution).
**Compositional**: ~N sandbox invocations per task (one per chain step).

**Per-step overhead**: ~6s (uv install + container start/stop) + actual computation.
**N=3 chain cost**: ~18s overhead + computation. Previous monolith: ~6s overhead + more computation (does everything in one call).

**Net**: compositional is ~10-15s slower per multi-step task. Over 360 records × ~30% multi-step tasks: ~108 × 12s = ~22 minutes extra sandbox time. Acceptable.

### Total additional cost for full eval re-run

- **Tokens**: +$2
- **Sandbox time**: +22 minutes
- **Wall clock**: +22 minutes + N×2 minutes for parallel execution (~30-50 min extra)
- **Engineering time**: 3-5 days for the full implementation (Stages 1-7) + 1 day for Stage 8 analysis

**Total**: ~$7-8 in compute, ~4-6 days of engineering.

---

## Success Criteria

- [ ] All 887+ unit tests pass at every stage boundary (some will be updated, new ones added)
- [ ] Stage 1: pipeline no longer writes fake invocations; all invocation records have non-zero `token_cost` OR non-zero `latency_ms`
- [ ] Stage 2: T14 generates exactly 2 artifacts (one per unmatched gap)
- [ ] Stage 3: `test_composition.py` has 90%+ coverage; DAG building, topological sort, cycle detection, mixed-type execution all covered
- [ ] Stage 4: T14 pilot shows chain execution with csv-parser invoked (non-zero `latency_ms` in metrics)
- [ ] Stage 5: T14 regenerated tools use the workspace convention (verified via LangFuse trace of generated source code)
- [ ] Stage 6: all 6 pilot tasks (T01, T04, T11, T14, T16, T17) pass with correct outputs
- [ ] Stage 7: full unit + integration suite is green
- [ ] Stage 8: full eval re-run produces 360 records with meaningful `matched_and_executed_count` per record
- [ ] **Chapter 6 can report honest reuse rate**: csv-parser was matched N times AND executed N times (no fake invocations)
- [ ] **Thesis-level**: the deviation from Pillar 1 documented in `single_artifact_enforcement_plan.md` is closed; the implementation now matches the proposal's literal text

---

## Thesis Implications

### Chapter 3 (JIT Capability Pipeline)

Rewrite Section 3.2 (Capability Classification Framework) to describe per-gap classification with compositional execution. Specifically:

> "The classifier runs once per unmatched required capability, classifying each gap into one architectural layer (MCP tool, skill, or agent). Generators then produce one artifact per classified gap. Matched capabilities (found in the registry) are reused directly. The compositional executor runs the resulting set of artifacts — mixed registry + generated — in dependency order determined by `SubTask.depends_on` from the decomposition output, passing intermediate results between steps via a shared file-based workspace."

Acknowledge the earlier single-artifact deviation as an intermediate implementation that we revised after empirical analysis revealed it prevented actual registry reuse:

> "An earlier iteration used task-level classification producing a single artifact per task. Empirical analysis on 2026-04-08 revealed that this approach prevented registry reuse entirely: partial matches (where the registry contained some but not all required sub-capabilities) were recorded as metric entries but never actually executed, and generated monoliths re-implemented the matched capability's functionality. We reverted to per-gap classification and added compositional execution to address this, matching the proposal's original intent."

### Chapter 6 (Evaluation)

New metrics become meaningful:
- **Real reuse rate**: `matched_and_executed_count / (matched_and_executed_count + generated_artifact_count)` — the fraction of chain steps served by the registry
- **Chain length distribution**: histogram of chain lengths across the 18 tasks
- **Mixed-type chain frequency**: how often chains contain more than one artifact type
- **Per-step success rate**: did each step in the chain execute successfully?
- **Chain vs monolith cost comparison**: pre/post token costs, pre/post success rates

New figures:
- Reuse rate over time (longitudinal test)
- Chain length vs task success rate
- Per-type execution success rate (MCP_TOOL in chains vs SKILL in chains vs AGENT in chains)

New findings to report:
- Actual reuse rate (vs the 0% effective rate under the single-artifact design)
- Token cost delta (positive or negative depending on reuse rate)
- Whether compositional execution improves or degrades task success rate
- Whether the validator leniency issues (T16 cascade ordering) are affected by chain-length 1 vs N

### Chapter 7 (Future Work)

Closed items (no longer future work):
- Compositional capability synthesis
- Dependency-ordered execution
- Output passing between artifacts
- Mixed-type chains

Remaining future work:
- ~~Subagent delegation (generated agents that call other generated agents)~~ — **promoted into Stage 4.5 on 2026-04-09** after the Phase 2 retrospective concluded that the proposal's third tension (context economy in a MAS where agents pass context to each other) is under-exercised at the generated-capability layer and that DeepAgents subagents are the canonical fix. See Stage 4.5 above and `pre_eval_execution_plan.md` Phase 3.5b.
- Parallel execution of independent chain steps
- Typed IO schemas (HP1 Option Z) — currently using untyped JSON file passing
- Dynamic dependency rewiring (if step B fails, retry with different upstream)
- Compositional verification (verify the chain as a whole, not just individual artifacts)
- Subagent-to-subagent delegation (a subagent itself spawning further subagents) — Stage 4.5 only enables one level of lead+subagents, not arbitrary nesting

---

## Open Questions

1. **Should the compositional executor retry failed steps, or fail the whole chain?**
   - **Decision**: fail the whole chain on first step failure. Per-artifact retries already happen at generation time (verification gate). Per-execution retries would mask problems and complicate metrics.

2. **How are matched capabilities' invocation metrics recorded?**
   - **Decision**: the `CompositionalExecutor` records invocations with REAL costs (token_cost from the LLM call if applicable, latency_ms from the wall clock). `gap_detection_node` stops writing any invocation records.

3. **What happens if a subtask's `required_capabilities` list has multiple entries?**
   - Current decomposition usually produces 1 required capability per subtask. If multiple, we need a resolution strategy: pick the first, or fail. **Decision**: log a warning and use the first. Future work: allow subtasks to compose multiple capabilities internally.

4. **Can a chain step BE a skill that calls another skill?**
   - Currently no. Skills execute via a constrained LLM loop with `sandbox_execute` — they don't dispatch to other skills. **Decision**: out of scope for this plan; future work. (Note: AGENT-typed chain steps CAN delegate to subagents as of Stage 4.5; this question is specifically about SKILL-typed chain steps, which remain monolithic.)

5. **How is the resolution map built when matches and generated artifacts cover overlapping capabilities?**
   - E.g., registry matched `csv-parser` for `read-csv` AND the classifier routed `read-csv` to generation (returning a new CSV reader). **Decision**: registry matches take precedence. If a capability name is in both `state.matches` and `state.generated_artifacts`, use the match. This prevents redundant generation.

6. **Does the optimizer's split/merge operate on individual artifacts or on chains?**
   - **Decision**: individual artifacts, unchanged from Pillar 3 spec. Chains are a runtime composition; the registry stores atomic capabilities.

7. **Should `mint list` distinguish chain-level statistics from artifact-level?**
   - **Decision**: no. `mint list` shows capabilities with real invocation counts. The chain is a runtime concept, not a registry concept. `mint history` could optionally show chain-level executions in a future iteration.

8. **What happens to the `single_artifact_enforcement_plan.md` doc?**
   - **Decision**: keep it, add a "Superseded" banner. Chapter 3 references it for the methodology story (single-artifact → compositional refinement).

---

## Compatibility with Prior Changes

This plan is compatible with the following changes committed earlier today:

- **`refactor: task-level classifier with semantic LLM-chosen names`** — LLM-chosen names remain per gap. Each gap gets its own `capability_name`. The `_normalize_capability_name` logic is unchanged.
- **`fix: scope Tier 1 regex checks to MCP_TOOL source only`** — unchanged. Tier 1 still only runs on MCP_TOOL. Each artifact in the chain gets its own Tier 1 check.
- **`fix: skill JSON extraction and AGENT workspace fixtures`** — unchanged. The agent workspace fix is compatible; the new compositional workspace is a superset of the agent's per-invocation workspace.
- **`fix: CLI streaming display diff and stage timing`** — compatible. The display updates for chain steps build on the existing progress-display infrastructure.
- **`docs: CLAUDE.md updates for Iter 6 pilot findings`** — the T16 ambiguity finding is unrelated and carries forward.

This plan REVERSES these decisions from `single_artifact_enforcement_plan.md`:
- Task-level classification → per-gap classification (restored)
- Single placeholder gap → N enumerated gaps (restored)
- Single-artifact generation → multi-artifact generation (restored)
- Fake invocation recording in gap_detection → removed entirely
- "Decorative registry match" semantics → real compositional reuse

---

## Timeline Estimate

| Stage | Content | Engineering time |
|---|---|---|
| 1 | Honest metrics + schemas | 0.5 day |
| 2 | Per-gap classifier + generators | 1 day |
| 3 | CompositionalExecutor + workspace | 1.5 days |
| 4 | Wire into harness + CLI | 1 day |
| 5 | Generator prompt composition-awareness | 0.5 day |
| 6 | Pilot validation + iteration | 0.5 day |
| 7 | Full unit test suite update | 0.5 day |
| 8 | Docs + full eval re-run | 0.5 day + 26h wall clock |
| **Total** | | **~5.5 days engineering + ~1 day eval wall clock** |

Realistic buffer for bugs and surprises: **7-8 days total**.

---

## Rollback Plan

Each stage is independently committed and testable. If a stage reveals a fundamental flaw:

- **Stages 1-2 rollback**: `git revert` the stage commits. The single-artifact path is preserved in earlier stages; reverting 1-2 restores that path.
- **Stage 3 rollback**: the compositional executor is a new module with no dependents until Stage 4. Can be kept as dead code or removed.
- **Stage 4 rollback**: revert the harness/CLI integration. The compositional executor stays available as a library; the pipeline falls back to single-artifact execution.
- **Stage 5-8 rollback**: fix forward. By this point the refactor is well-exercised.

If the full implementation reveals the compositional approach is genuinely worse than single-artifact (lower success rates, higher cost without reuse benefit), the thesis finding becomes: *"We attempted full compositional synthesis as specified in the proposal. Empirically, the single-artifact simplification produced equal or better task success rates at lower cost for the 18-task testbed, due to [specific reasons]. Compositional synthesis remains theoretically superior for heterogeneous task distributions but requires higher registry reuse rates than our testbed exhibited."*

That would be a legitimate (if disappointing) thesis outcome. The empirical measurement is the contribution either way.

---

**Next action**: create a new branch `iter/6-compositional-synthesis`, commit this plan, start Stage 1.
