# Plan: Add SKILL/AGENT Tasks and Execution to Evaluation Harness

## Context

The thesis claims three capability types (MCP_TOOL, SKILL, AGENT) but the evaluation only exercises MCP_TOOL end-to-end. All 10 current tasks are single-function data operations that the classifier routes to MCP_TOOL. Additionally, the harness returns `{}` for SKILL/AGENT artifacts — it can't execute them. This means two of three pillars are untested in evaluation, which undermines the thesis contribution.

**Goal:** Add 2-3 tasks that naturally route to SKILL and AGENT, implement execution for those artifact types in the harness, and re-run the evaluation.

## Design Decisions

### How to execute SKILL artifacts
A SKILL is a markdown guidance document (SKILL.md) consumed by DeepAgents. To execute one, we create a **skill-executor DeepAgent** that:
1. Receives the SKILL.md content + fixture file contents in its prompt
2. Has `sandbox_execute` as a tool (same as adversarial probing)
3. Follows the skill instructions to process the data
4. Returns structured output via `response_format`

This follows the exact pattern from `adversarial.py`: `create_deep_agent()` → `ThreadPoolExecutor` → `future.result(timeout)` → extract `structured_response`.

### How to execute AGENT artifacts
An AGENT artifact contains `agent_config` with `system_prompt`, `node_config`, and optionally `skill_content` (if `is_deep_agent=True`). We create a DeepAgent using the agent's system_prompt, give it `sandbox_execute` + fixture data, and invoke it. Same pattern as SKILL execution but using the agent's own system prompt instead of a generic executor prompt.

### How fixture data reaches the DeepAgent
Fixture files are small (<10KB). We read them on the host and embed their content in the DeepAgent's user message. The DeepAgent then uses `sandbox_execute` to process them (writing content to `/tmp/` inside the sandbox). This avoids filesystem mounting complexity.

### Task design for natural SKILL/AGENT routing
The classifier routes to SKILL when the task requires "orchestrating separate tools with intermediate outputs" and to AGENT when it requires "planning, reasoning, adaptive behavior." The Single-Capability Rule biases toward MCP_TOOL when decomposition produces 1 gap.

For tasks to route correctly, the **task prompt must describe a multi-step workflow** so decomposition produces 2+ gaps, disabling the Single-Capability Rule. The gaps themselves must clearly describe orchestration (SKILL) or adaptive reasoning (AGENT).

## Implementation Steps

### Step 1: New fixtures (`tests/evaluation/fixtures/generate.py`)

Add three fixture generators:

**T11 — Multi-source data integration** (targets SKILL):
- `t11_sales.csv`: 60 rows (order_id, product_id, quantity, unit_price, region)
- `t11_products.json`: 10 products (product_id, name, category)
- `t11_targets.csv`: 4 rows (region, target_revenue)
- Expected values: `summary_row_count=4` (one per region), pre-computed `region_revenues` dict, `regions_meeting_target` count

**T12 — Configurable data quality pipeline** (targets SKILL):
- `t12_raw_data.csv`: 80 rows with planted nulls, out-of-range values, duplicates
- `t12_rules.json`: Quality rules config (non-null columns, valid ranges, dedup key)
- Expected values: `clean_row_count`, `violations_found` count, `rules_applied` count

**T13 — Autonomous anomaly investigation** (targets AGENT):
- `t13_metrics.csv`: 100 rows of time-series metrics with 5 planted anomalies
- `t13_baselines.json`: Normal ranges per metric for cross-referencing
- Expected values: `anomaly_count=5`, `metrics_analyzed` count

### Step 2: New task definitions (`tests/evaluation/tasks.py`)

Add T11, T12, T13 with multi-step task prompts that explicitly describe orchestration/planning:

**T11 task prompt** (designed to decompose into 2-3 capabilities):
> "Build a data integration pipeline: (1) join the sales CSV with the product catalog JSON on product_id to get product names and categories, (2) compute per-region revenue totals (quantity * unit_price), (3) compare against regional targets from the targets CSV. Return a dict with keys 'summary_row_count' (int, number of regions), 'region_revenues' (dict mapping region to float revenue), and 'regions_meeting_target' (int)."

**T12 task prompt**:
> "Build a data quality pipeline: read the rules configuration from JSON, then apply each rule in sequence to the raw CSV data — first enforce non-null constraints (drop rows with nulls in required columns), then enforce value range constraints (drop out-of-range rows), then deduplicate on the specified key columns. Return a dict with keys 'clean_row_count' (int), 'violations_found' (int, total rows removed), and 'rules_applied' (int, number of rule types applied)."

**T13 task prompt**:
> "Investigate anomalies in the metrics dataset. Profile the data to understand distributions, then detect anomalies by comparing each metric against the baselines JSON (values outside the normal range). For each anomaly found, determine which metric and timestamp are involved. Return a dict with keys 'anomaly_count' (int), 'anomalies' (list of dicts with 'timestamp', 'metric', 'value' keys), and 'metrics_analyzed' (int, number of unique metrics checked)."

Validators:
- T11: `summary_row_count == 4`, each region revenue within ±5% of expected, `regions_meeting_target` exact
- T12: `clean_row_count == expected`, `violations_found > 0`, `rules_applied == 3`
- T13: `anomaly_count >= 3` (tolerant — agent may find more or fewer), `len(anomalies) > 0`, `metrics_analyzed > 0`

Add to `ALL_TASKS`. Do NOT add to `PILOT_TASKS` (keep pilot as-is).

### Step 3: Execution module (`tests/evaluation/execution.py`)

New module. Response Pydantic models live here (not in `src/schemas/`) because they are harness-internal — they define what the executor DeepAgent returns, not pipeline state. If they grow beyond 3-4 models, extract to `tests/evaluation/result_schemas.py`.

```python
# Pydantic response models (one per task, matching validator expectations)
class T11Result(BaseModel):
    summary_row_count: int
    region_revenues: dict[str, float]
    regions_meeting_target: int

class T12Result(BaseModel):
    clean_row_count: int
    violations_found: int
    rules_applied: int

class T13Result(BaseModel):
    anomaly_count: int
    anomalies: list[dict]
    metrics_analyzed: int

# Generic fallback for unknown tasks
class GenericResult(BaseModel):
    result: dict

# Task-to-model mapping
TASK_RESULT_MODELS = {"T11": T11Result, "T12": T12Result, "T13": T13Result}
```

Main functions:

```python
def execute_skill_artifact(artifact, task, fixture_contents: dict[str, str]) -> dict:
    """Execute a SKILL artifact via DeepAgent. Never raises."""

def execute_agent_artifact(artifact, task, fixture_contents: dict[str, str]) -> dict:
    """Execute an AGENT artifact via DeepAgent. Never raises."""

def _read_fixture_contents(task: TaskDefinition) -> dict[str, str]:
    """Read all fixture files into a dict keyed by filename."""
```

Both execution functions follow the adversarial.py pattern:
1. Create a fresh DeepAgent with `response_format=TaskResultModel`
2. Give it `sandbox_execute` as a tool
3. Format fixture contents into the user message
4. Invoke via `ThreadPoolExecutor` with timeout (120s for SKILL, 180s for AGENT)
5. Extract `structured_response`, convert to dict
6. On timeout/error, return `{}`

**SKILL executor:** Pass the SKILL.md content as the DeepAgent's `system_prompt` (not just user context). This gives the skill instructions maximum weight in the agent's behavior, treating them as system-level instructions rather than user-provided context.

**AGENT executor:** Uses the agent's own `system_prompt` from `agent_config` as the DeepAgent's system prompt. If `is_deep_agent` and skill content exists, prepend skill content to the system prompt.

### Step 4: Wire into harness (`tests/evaluation/harness.py`)

**Modify `_execute_artifact`** (line 515):
```python
# Replace the early return for non-MCP_TOOL:
if cap_type == CapabilityType.SKILL:
    from tests.evaluation.execution import execute_skill_artifact, _read_fixture_contents
    fixture_contents = _read_fixture_contents(task)
    return execute_skill_artifact(artifact, task, fixture_contents)
elif cap_type == CapabilityType.AGENT:
    from tests.evaluation.execution import execute_agent_artifact, _read_fixture_contents
    fixture_contents = _read_fixture_contents(task)
    return execute_agent_artifact(artifact, task, fixture_contents)
```

**Modify `_extract_result`** (lines 401-406, 450-453):
Remove MCP_TOOL preference. Currently selects `mcp_artifacts[-1] if mcp_artifacts else artifacts[-1]`. Change to just `artifacts[-1]` — use whatever the pipeline generated. The pipeline already produces the correct type based on classification.

### Step 5: Static implementations for T11/T12/T13

Add `_static_t11()`, `_static_t12()`, `_static_t13()` to `harness.py` so the `static` baseline has coverage for these tasks too. These are hand-written Python functions (no LLM) that produce the correct output. Also set `has_static_coverage=True` and `static_capability_name` for each.

However — these tasks don't have seeded capabilities in the registry. We need to either:
- (a) Add seeded capabilities for them in `seed_registry()` in `src/registry/store.py`, OR
- (b) Set `has_static_coverage=False` and accept that static baseline can't cover them

**Decision:** Option (b). These tasks are specifically designed to test JIT generation of SKILL/AGENT types. The static baseline's purpose is to show what a hand-curated library can do — and a hand-curated library of MCP tools genuinely can't solve multi-step orchestration tasks. This is a valid thesis finding: static libraries are limited to single-function capabilities.

### Step 6: Unit tests

**`tests/unit/test_execution.py`** (new):
- Test `execute_skill_artifact` with mocked `create_deep_agent` returning structured response → correct dict
- Test `execute_skill_artifact` timeout → returns `{}`
- Test `execute_skill_artifact` no structured response → returns `{}`
- Test `execute_agent_artifact` same three cases
- Test `_read_fixture_contents` reads files correctly
- Test result model validation for T11Result, T12Result, T13Result
- ~12-15 tests

**`tests/unit/test_harness.py`** updates:
- Test `_execute_artifact` dispatches to SKILL execution
- Test `_execute_artifact` dispatches to AGENT execution
- Test `_extract_result` no longer prefers MCP_TOOL
- ~5 new/modified tests

**`tests/unit/test_harness_static.py`** — no changes needed (T11-T13 have no static coverage)

**`tests/unit/test_tasks.py`** updates:
- Test T11, T12, T13 validators with good and bad input
- ~6-9 new tests

### Step 7: Regenerate fixtures

Run `python -m tests.evaluation.fixtures.generate` to regenerate all fixtures including new ones. Verify `expected_values.json` includes T11, T12, T13 entries.

### Step 8: Run evaluation

Run all 4 conditions with expanded task set (13 tasks):
```bash
caffeinate -dims uv run scripts/run_pilot.py --all-conditions --all-tasks --n-runs 5
```

## File Change Summary

| File | Change | Complexity |
|------|--------|------------|
| `tests/evaluation/fixtures/generate.py` | Add `_gen_t11()`, `_gen_t12()`, `_gen_t13()` + wire into `generate_all_fixtures` | Medium |
| `tests/evaluation/tasks.py` | Add T11, T12, T13 definitions with validators; update `ALL_TASKS` | Medium |
| `tests/evaluation/execution.py` | **New file**: SKILL/AGENT execution via DeepAgent | Medium-High |
| `tests/evaluation/harness.py` | Modify `_execute_artifact` + `_extract_result` to support SKILL/AGENT | Low |
| `tests/unit/test_execution.py` | **New file**: unit tests for execution module | Medium |
| `tests/unit/test_harness.py` | Update existing tests for new dispatch logic | Low |
| `tests/unit/test_tasks.py` | Add validator tests for T11-T13 | Low |
| `docs/iteration_plan.md` | Document T11-T13 and SKILL/AGENT execution | Low |
| `CLAUDE.md` | Update task count, add stable interfaces | Low |

## Risks

1. **Decomposition may still produce 1 gap** → Single-Capability Rule routes to MCP_TOOL. Mitigation: task prompts explicitly describe multi-step workflows with named stages. If it still collapses, adjust the decomposition system prompt in `src/orchestrator/prompts/decomposition.py` to demand that orchestration logic be separated from data primitives. If that still fails, this is itself a thesis finding about decomposition limitations.

2. **DeepAgent execution non-deterministic** → Output may not match validator. Mitigation: tolerant validators (ranges, structural checks, ≥ thresholds rather than exact matches). Some runs failing is expected and is thesis data.

3. **Added latency** → ~60-120s per SKILL/AGENT execution × 3 tasks × 5 runs × 3 JIT conditions = ~45-90 min added. Acceptable for overnight run. Ensure LangFuse tracing is active during these runs — it's the only way to debug *why* a SKILL/AGENT execution failed (bad generated code vs. misunderstood SKILL.md vs. timeout).

4. **Classifier routes to wrong type** → E.g., SKILL task classified as MCP_TOOL. This is an honest finding about classifier accuracy, not a bug.

## Verification

1. `uv run python -m pytest tests/unit/ -x` — all existing + new tests pass
2. `python -m tests.evaluation.fixtures.generate` — regenerates fixtures including T11-T13
3. Quick pilot: `uv run scripts/run_pilot.py --condition full` with T11 in pilot set to verify SKILL execution works
4. Full run: `caffeinate -dims uv run scripts/run_pilot.py --all-conditions --all-tasks --n-runs 5`
5. Query DuckDB to verify T11/T12/T13 records exist with `capability_type` = `skill` or `agent`
