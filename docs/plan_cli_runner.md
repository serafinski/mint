# Implementation Plan: User-Facing CLI Runner

**Date:** 2026-03-20
**Status:** IMPLEMENTED — `scripts/run_task.py` (2026-03-22)
**Scope:** Level 1 CLI — single-task execution via command line
**Estimated effort:** 2-4 hours
**Dependencies:** All existing pipeline infrastructure (no new packages)

---

## 1. Goal

A script where a user submits a task description + input files, the JIT pipeline generates/finds a capability, executes it, and returns the result. Currently the pipeline only runs inside the evaluation harness with hardcoded fixture paths and validators. The CLI makes the system usable outside evaluation.

```bash
uv run scripts/run_task.py \
  "Parse this CSV and compute summary statistics" \
  --file data.csv

# Output:
# {
#   "row_count": 1000,
#   "column_names": ["id", "name", "value"],
#   "success": true,
#   "capability_type": "mcp_tool",
#   "capability_name": "parse-csv-and-compute-summary-stat-a3f21b"
# }
```

---

## 2. Architecture

### Data Flow

```
User (task description + files)
    |
    v
scripts/run_task.py (argparse, input validation)
    |
    v
Pipeline setup (registry + loader + metrics)
    |
    v
create_pipeline().compile().invoke(TaskState)
    |
    v
Extract best artifact from final TaskState
    |
    v
Execute artifact against user inputs:
    MCP_TOOL  -> sandbox_execute() (Docker)
    SKILL     -> execute_skill_artifact() (DeepAgent)
    AGENT     -> execute_agent_artifact() (DeepAgent)
    |
    v
Format and print result (JSON or human-readable)
```

### Key Insight: Most Execution Logic Already Exists

The evaluation harness (`tests/evaluation/harness.py`) already:
- Creates the pipeline and invokes it
- Extracts artifacts from `TaskState`
- Executes MCP_TOOL artifacts via `sandbox_execute` with fixture embedding
- Executes SKILL/AGENT artifacts via DeepAgent (`tests/evaluation/execution.py`)

The CLI reuses these paths but replaces:
- Hardcoded fixture paths -> user-provided `--file` arguments
- Task validators -> no validation (return raw output to user)
- `EvaluationRecord` recording -> stdout output

---

## 3. Implementation: Single File

The CLI is a single script (`scripts/run_task.py`) rather than a `cli/` package. Rationale: this is a thesis demo, not a production tool. A single file keeps it simple and avoids package structure overhead.

### 3.1 Entry Point and Arguments

```python
# scripts/run_task.py
parser = argparse.ArgumentParser()
parser.add_argument("task", type=str, help="Task description")
parser.add_argument("--file", action="append", dest="files", help="Input file(s)")
parser.add_argument("--param", action="append", dest="params", help="key=value parameters")
parser.add_argument("--skip-verification", action="store_true")
parser.add_argument("--enable-optimizer", action="store_true")
parser.add_argument("--output-format", choices=["json", "human"], default="human")
parser.add_argument("--dry-run", action="store_true", help="Decompose only, don't generate")
parser.add_argument("--list", action="store_true", help="List registry capabilities")
parser.add_argument("--timeout", type=int, default=None, help="SKILL/AGENT execution timeout (default: 240s/300s)")
parser.add_argument("--verbose", action="store_true")
```

### 3.2 Pipeline Invocation

Reuse existing infrastructure directly:

```python
from src.orchestrator.pipeline import create_pipeline
from src.orchestrator.tool_loader import ToolLoader
from src.registry.store import SkillRegistry, seed_registry
from src.registry.metrics import MetricsStore

registry = SkillRegistry(persist_directory=str(CHROMA_DIR))
seed_registry(registry)
loader = ToolLoader()
metrics_store = MetricsStore()

pipeline = create_pipeline(
    registry, loader,
    run_optimizer=args.enable_optimizer,
    metrics_store=metrics_store if args.enable_optimizer else None,
    verification_enabled=not args.skip_verification,
).compile()

initial_state = TaskState(task_description=task_prompt)
final_state = pipeline.invoke(initial_state, config=invoke_config)
```

### 3.3 Task Prompt Construction

The task description needs to reference the user's files by path. The pipeline's decomposition agent reads these paths from the prompt.

```python
# Build task prompt with file references
task_prompt = args.task
if args.files:
    task_prompt += "\n\nInput files:\n"
    for f in args.files:
        task_prompt += f"- {f}\n"
if params:
    task_prompt += "\nParameters:\n"
    for k, v in params.items():
        task_prompt += f"- {k}: {v}\n"
```

### 3.4 Artifact Selection

After the pipeline completes, select the best artifact to execute:

```python
def select_artifact(state: TaskState) -> GeneratedArtifact | None:
    """Pick the best artifact from the pipeline output."""
    # First try: artifact that passed verification
    for art in state.generated_artifacts:
        for report in state.verification_results:
            if report.capability_name == art.capability_name and report.overall_passed:
                return art
    # Fallback: any generated artifact (pipeline may have failed open)
    if state.generated_artifacts:
        return state.generated_artifacts[-1]
    return None
```

### 3.5 Artifact Execution (Three Paths)

This is the critical section. Each capability type has a different execution model.

#### MCP_TOOL Execution

Reuse the harness pattern from `_execute_artifact` in `harness.py`:

```python
def execute_mcp_tool(artifact, file_paths, params):
    """Execute an MCP_TOOL artifact in the Docker sandbox."""
    source_code = artifact.source_code
    func_name = _extract_function_name(source_code)  # from behavioral.py

    # 1. Read fixture files into memory
    fixture_contents = {}
    for fp in file_paths:
        fixture_contents[Path(fp).name] = Path(fp).read_text()

    # 2. Build fixture preamble (write files into sandbox /tmp/)
    preamble_lines = []
    fixture_inputs = {}
    for i, (filename, content) in enumerate(fixture_contents.items()):
        sandbox_path = f"/tmp/fixture_{i}{Path(filename).suffix}"
        escaped = content.replace("\\", "\\\\").replace("'", "\\'")
        preamble_lines.append(
            f"import pathlib as _pl\n"
            f"_pl.Path('{sandbox_path}').write_text('{escaped}')"
        )
        fixture_inputs[f"file_path_{i}"] = sandbox_path

    # 3. Merge params + fixture paths, match to function signature
    all_inputs = {**fixture_inputs, **params}

    # 4. Build sandbox code
    sandbox_code = build_sandbox_code(source_code, func_name, all_inputs, preamble_lines)

    # 5. Resolve extra packages
    extra_pkgs = resolve_extra_packages(source_code)

    # 6. Execute
    result = sandbox_execute(sandbox_code, timeout=30, extra_packages=extra_pkgs or None)

    # 7. Parse JSON from stdout
    return parse_sandbox_output(result)
```

**Key detail:** The sandbox code must include `__name__ = "not_main"` before the source code to prevent `mcp.run()` from blocking. The exact pattern is in `harness.py:_execute_artifact`.

**Key detail:** Parameter matching uses fuzzy word-overlap stemming (from harness.py) because the generated function may name its parameter `csv_path` while we pass `file_path_0`. The harness's `_match_param` logic handles this.

#### SKILL Execution

Reuse `execute_skill_artifact` from `tests/evaluation/execution.py`:

```python
def execute_skill(artifact, file_paths):
    """Execute a SKILL artifact via DeepAgent."""
    from tests.evaluation.execution import execute_skill_artifact

    # Build fixture contents dict
    fixture_contents = {}
    for fp in file_paths:
        fixture_contents[Path(fp).name] = Path(fp).read_text()

    # Build a minimal task-like object with fixture_paths
    # (execution.py expects task.fixture_paths for _read_fixture_contents)
    return execute_skill_artifact(artifact, task_context, fixture_contents)
```

**Key detail:** `execute_skill_artifact` creates a fresh DeepAgent per invocation, passes the skill content as the system prompt, and gives the agent access to `sandbox_execute` as a tool. The agent reads the fixture contents from the user message, plans its work via `write_todos`, and returns structured output. Timeout: 240s.

**Key detail:** The current `execute_skill_artifact` expects a task-specific Pydantic response model (e.g., `T11Result`, `T12Result`). For the CLI, we need a generic response model since we don't know what the skill will return. Use a flexible model like `class GenericResult(BaseModel): result: dict`.

#### AGENT Execution

Same as SKILL but with `execute_agent_artifact` and 300s timeout:

```python
def execute_agent(artifact, file_paths):
    """Execute an AGENT artifact via DeepAgent."""
    from tests.evaluation.execution import execute_agent_artifact

    fixture_contents = {}
    for fp in file_paths:
        fixture_contents[Path(fp).name] = Path(fp).read_text()

    return execute_agent_artifact(artifact, task_context, fixture_contents)
```

**Key detail:** Agent execution uses `agent_config["system_prompt"]` as the DeepAgent system prompt instead of skill_content. If `artifact.skill_content` is also present (is_deep_agent=True), it's prepended to the system prompt.

### 3.6 Output Formatting

```python
def format_human(state, artifact, output, elapsed_ms):
    """Human-readable output for terminal."""
    lines = []
    lines.append(f"\nTask: {state.task_description[:100]}...")
    lines.append("")

    # Pipeline progress
    lines.append("Pipeline:")
    lines.append(f"  Decomposed into {len(state.subtasks)} subtask(s)")
    lines.append(f"  Registry: {len(state.matches)} match(es), {len(state.gaps)} gap(s)")
    if state.classified_gaps:
        types = [g.classified_type for g in state.classified_gaps]
        lines.append(f"  Classified: {', '.join(types)}")
    lines.append(f"  Generated {len(state.generated_artifacts)} artifact(s)")
    lines.append(f"  Attempts: {state.attempt_count}")
    lines.append("")

    # Result
    if artifact:
        lines.append(f"Capability: {artifact.capability_name} ({artifact.capability_type})")
        if output:
            lines.append(f"Output: {json.dumps(output, indent=2)}")
        else:
            lines.append("Output: (execution failed)")
    else:
        lines.append("No capability generated.")

    lines.append(f"\nCompleted in {elapsed_ms/1000:.1f}s")
    return "\n".join(lines)
```

---

## 4. Existing Code to Reuse (Do NOT Rewrite)

| Component | Location | What to import |
|---|---|---|
| Pipeline creation | `src/orchestrator/pipeline.py` | `create_pipeline()` |
| Tool loading | `src/orchestrator/tool_loader.py` | `ToolLoader` |
| Registry | `src/registry/store.py` | `SkillRegistry`, `seed_registry()` |
| Metrics | `src/registry/metrics.py` | `MetricsStore` |
| Sandbox execution | `src/verification/sandbox_tool.py` | `sandbox_execute()`, `resolve_extra_packages()` |
| Function name extraction | `src/verification/behavioral.py` | `extract_parameter_schema()`, `_extract_function_name()` |
| SKILL execution | `tests/evaluation/execution.py` | `execute_skill_artifact()` |
| AGENT execution | `tests/evaluation/execution.py` | `execute_agent_artifact()` |
| Task state | `src/schemas/task.py` | `TaskState`, `GeneratedArtifact` |
| Sandbox code building | `tests/evaluation/harness.py` | Pattern from `_execute_artifact()` (copy logic, don't import private method) |

---

## 5. What Needs to Be Written New

| Component | Effort | Description |
|---|---|---|
| `scripts/run_task.py` | ~150 lines | Main script: argparse, pipeline setup, execution dispatch, output |
| Generic response model | ~10 lines | `class GenericResult(BaseModel): result: dict` for SKILL/AGENT execution without task-specific models |
| Sandbox code builder for CLI | ~40 lines | Adapted from harness `_execute_artifact` — builds sandbox code with user file embedding |
| Parameter matching | ~20 lines | Copy fuzzy matching logic from harness for mapping user params to function signature |
| Output formatters | ~30 lines | JSON and human-readable formatting |
| `--list` command | ~15 lines | Print active capabilities from registry |
| `--dry-run` mode | ~10 lines | Run pipeline up to classification, print plan, exit |

**Total new code: ~275 lines** (single file).

---

## 6. Modifications to Existing Code

### 6.1 `tests/evaluation/execution.py`

Currently `execute_skill_artifact` and `execute_agent_artifact` use task-specific response models (T11Result, T12Result, T13Result) selected by task_id. For the CLI, we need a generic path.

**Change:** Add a `response_model` parameter with a default generic model:

```python
def execute_skill_artifact(
    artifact, task_context, fixture_contents,
    response_model=None,  # NEW: override response format
) -> dict:
    if response_model is None:
        response_model = _default_response_model_for_task(...)  # existing behavior
```

This is backward-compatible — existing evaluation code passes no `response_model` and gets the current behavior.

### 6.2 No other modifications needed

The pipeline, registry, loader, sandbox, and verification all work as-is. The CLI is purely additive.

---

## 7. Gotchas

1. **`__name__ = "not_main"` must precede source code** in sandbox code to prevent `mcp.run()` from blocking. The harness handles this; the CLI must replicate the pattern.

2. **Fixture files must be written into sandbox `/tmp/`** because the Docker container has no access to the host filesystem. The harness embeds file contents as Python string literals in the sandbox code.

3. **Parameter name matching is fuzzy.** The generated function may use `csv_path`, `file_path`, `input_file`, etc. The harness uses word-overlap stemming to match user-provided names to function parameters.

4. **Large files may fail.** File contents are embedded as Python string literals in the sandbox code. Files > 10MB will produce sandbox code that's too large. Warn and fail gracefully.

5. **Docker must be running.** `sandbox_execute` returns `{"success": false, "stderr": "..."}` if Docker is unavailable. The CLI should detect this and print a clear error.

6. **ChromaDB persists to disk.** The registry survives between CLI runs. Generated capabilities accumulate. Users can `--reset-registry` to start fresh.

7. **LangFuse is optional.** The CLI works without LangFuse env vars. If present, tracing is enabled automatically (same pattern as `run_pilot.py`).

8. **The `_PackageMapping` structured output issue.** OpenAI's strict JSON schema mode rejects the `_PackageMapping` model. We already fixed this with `method="function_calling"` in `sandbox_tool.py`.

---

## 8. Testing Plan

No separate test file needed for the thesis. Verify manually with:

```bash
# Test 1: Simple CSV parsing (should find registry match for csv-parser)
uv run scripts/run_task.py "Parse this CSV" --file tests/evaluation/fixtures/t01_standard.csv

# Test 2: JIT generation (no existing capability)
uv run scripts/run_task.py "Parse this pipe-delimited file" --file tests/evaluation/fixtures/t04_pipe_delimited.csv

# Test 3: SKILL task
uv run scripts/run_task.py \
  "Integrate sales with products and compute regional revenue" \
  --file tests/evaluation/fixtures/t11_sales.csv \
  --file tests/evaluation/fixtures/t11_products.json \
  --file tests/evaluation/fixtures/t11_targets.csv

# Test 4: Dry run
uv run scripts/run_task.py "Detect anomalies in this data" --file data.csv --dry-run

# Test 5: List capabilities
uv run scripts/run_task.py --list
```

---

## 9. Out of Scope (Future Work)

- **Conversational interface** (multi-turn, follow-up questions)
- **MCP server mode** (expose generated tools as MCP server for Claude/Cursor)
- **Streaming output** (progress updates during pipeline execution)
- **Web UI** (browser-based interface)
- **Batch mode** (process multiple tasks from a file)
- **Capability export** (save generated tool as standalone .py file)

These are documented in `docs/system_overview.md` as Level 2/3 integration options.
