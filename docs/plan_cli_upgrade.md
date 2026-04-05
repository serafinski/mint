# Implementation Plan: MINT CLI with Typer + Rich

**MINT — Model-driven Instantiation of Novel Tools**

**Date:** 2026-03-30
**Status:** PLANNED
**Current:** `scripts/run_task.py` — 838 lines, argparse, plain `print()` output
**Target:** Polished CLI (`mint`) with typer (subcommands) + rich (tables, panels, progress)
**Future:** Full project rebrand from JustInTimeMAS to MINT (repo name, thesis title, imports) — separate effort

---

## Current Pain Points

1. **No progress indication** — the pipeline takes 30s-5min and the user sees nothing until it finishes. No spinners, no stage updates.
2. **Output is wall-of-text** — pipeline summary, verification findings, and output are all `print()` with manual formatting. Hard to scan.
3. **Flag soup** — 10 flags on a single command (`--file`, `--param`, `--skip-verification`, `--enable-optimizer`, `--no-optimizer`, `--timeout`, `--output-format`, `--dry-run`, `--list`, `--verbose`). No subcommands.
4. **Error messages are plain text** — no color differentiation between warnings, errors, and info.
5. **`--list` output is sparse** — no table formatting, no type grouping, no invocation counts.
6. **No help beyond `--help`** — no examples in help text, no guided experience.

## Proposed Architecture

### Subcommands via Typer

Replace the single `run_task.py` command with subcommands:

```bash
# Main command: run a task
mint run "Parse this CSV" --file data.csv

# List capabilities
mint list
mint list --type skill          # filter by type
mint list --inactive            # include deactivated

# Inspect a capability
mint inspect csv-parser-a1b2c3  # show source, metadata, invocation history

# Dry-run (plan only)
mint plan "Detect anomalies" --file metrics.csv

# Registry management
mint prune                      # manually trigger prune
mint history                    # show recent invocations
mint history --capability csv-parser  # filter
```

### Rich Output

```
┌─────────────────────────────────────────────────┐
│  JIT Capability Synthesis                       │
│  Task: Parse this CSV and compute statistics    │
└─────────────────────────────────────────────────┘

Pipeline ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100%

┌──────────────┬────────────────────────────────┐
│ Stage        │ Result                         │
├──────────────┼────────────────────────────────┤
│ Decompose    │ 1 subtask                      │
│ Registry     │ 0 matches, 1 gap               │
│ Classify     │ MCP_TOOL (confidence: 0.95)    │
│ Generate     │ csv_parser_a1b2c3              │
│ Verify       │ ✓ PASSED (Tier 3)             │
│ Load         │ Registered in ChromaDB         │
│ Execute      │ ✓ Success                     │
└──────────────┴────────────────────────────────┘

Verification:
  ✓ Tier 1 (static scanner) — 0 findings
  ✓ Tier 2 (behavioral) — 0 findings
  ✓ Tier 3 (adversarial) — 0 findings

Output:
╭──────────────────────────────────────────────╮
│ {                                            │
│   "row_count": 30,                           │
│   "column_names": ["id", "name", "score"]    │
│ }                                            │
╰──────────────────────────────────────────────╯

Completed in 45.2s | MCP_TOOL | 1 attempt | Optimizer: stable
```

### Live Progress via Rich

The biggest UX improvement: show pipeline stages as they happen, not after completion.

```python
from rich.live import Live
from rich.table import Table
from rich.console import Console
from rich.spinner import Spinner

# During pipeline execution, update a live display:
# [✓] Decomposition      1 subtask            3.2s
# [✓] Gap Detection       0 matches, 1 gap     0.1s
# [✓] Classification     MCP_TOOL (0.95)      2.1s
# [⟳] Generation         csv_parser_a1b2...   [spinning]
# [ ] Verification
# [ ] Loading
```

This requires hooking into the pipeline's state between nodes. Two approaches:

**A. LangGraph stream (preferred):**
```python
for event in pipeline.stream(initial_state, config=invoke_config):
    node_name = list(event.keys())[0]
    update_live_display(node_name, event[node_name])
```

**B. Callback-based:**
Add a callback to `invoke_config` that receives state updates and updates the Rich Live display.

## Implementation Plan

### Step 1: Add dependencies (5 min)

```bash
uv add typer rich
```

### Step 2: Create `src/cli/` package (new, ~200 lines)

```
src/cli/
  __init__.py
  app.py          # Typer app + subcommand definitions
  display.py      # Rich formatting (tables, panels, progress)
  runner.py       # Pipeline execution with streaming progress
```

#### `app.py` — Subcommand definitions

```python
import typer
from pathlib import Path
from typing import Optional, Annotated

app = typer.Typer(
    name="mint",
    help="MINT — Model-driven Instantiation of Novel Tools",
    no_args_is_help=True,
)

@app.command()
def run(
    task: Annotated[str, typer.Argument(help="Task description")],
    file: Annotated[Optional[list[Path]], typer.Option("--file", "-f", help="Input file(s)")] = None,
    param: Annotated[Optional[list[str]], typer.Option("--param", "-p", help="KEY=VALUE parameters")] = None,
    skip_verification: Annotated[bool, typer.Option("--skip-verification", help="Bypass verification gate")] = False,
    no_optimizer: Annotated[bool, typer.Option("--no-optimizer", help="Force-disable optimizer")] = False,
    timeout: Annotated[Optional[int], typer.Option(help="SKILL/AGENT execution timeout in seconds")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
):
    """Run a task through the JIT pipeline."""
    from src.cli.runner import execute_task
    execute_task(task, files=file, params=param, ...)


@app.command()
def plan(
    task: Annotated[str, typer.Argument(help="Task description")],
    file: Annotated[Optional[list[Path]], typer.Option("--file", "-f")] = None,
):
    """Decompose and classify only (dry-run)."""
    from src.cli.runner import plan_task
    plan_task(task, files=file)


@app.command("list")
def list_capabilities(
    cap_type: Annotated[Optional[str], typer.Option("--type", "-t", help="Filter by type")] = None,
    inactive: Annotated[bool, typer.Option("--inactive", help="Include deactivated")] = False,
):
    """List all capabilities in the registry."""
    from src.cli.display import show_capability_list
    show_capability_list(cap_type=cap_type, include_inactive=inactive)


@app.command()
def inspect(
    name: Annotated[str, typer.Argument(help="Capability name")],
):
    """Show details for a capability (source, metadata, history)."""
    from src.cli.display import show_capability_detail
    show_capability_detail(name)


@app.command()
def history(
    capability: Annotated[Optional[str], typer.Option(help="Filter by capability")] = None,
    limit: Annotated[int, typer.Option(help="Max records to show")] = 20,
):
    """Show recent invocation history."""
    from src.cli.display import show_history
    show_history(capability=capability, limit=limit)
```

#### `display.py` — Rich formatting

```python
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax
from rich.tree import Tree

console = Console()

def show_capability_list(cap_type=None, include_inactive=False):
    """Render capabilities as a Rich table grouped by type."""
    table = Table(title="Registered Capabilities")
    table.add_column("Type", style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Description")
    table.add_column("Active", justify="center")
    table.add_column("Invocations", justify="right")
    # ... populate from registry + metrics_store ...
    console.print(table)

def show_pipeline_result(state, artifact, output, elapsed_ms, verification_results):
    """Render the full pipeline result with panels and syntax highlighting."""
    # Pipeline summary table
    # Verification findings with color-coded severity
    # Output in a syntax-highlighted JSON panel
    ...

def show_capability_detail(name):
    """Render capability details including source code."""
    # Panel with metadata
    # Syntax-highlighted source code
    # Invocation history table
    ...
```

#### `runner.py` — Pipeline execution with streaming

```python
from rich.live import Live
from rich.table import Table

def execute_task(task, files, params, ...):
    """Run pipeline with live progress display."""
    # Build pipeline
    pipeline = create_pipeline(...)

    # Stream execution with live Rich display
    stages = {
        "decomposition_node": "Decompose",
        "gap_detection": "Registry Search",
        "classifier_node": "Classify",
        "generate_mcp_tool": "Generate (MCP)",
        "generate_skill": "Generate (Skill)",
        "generate_agent": "Generate (Agent)",
        "verification_node": "Verify",
        "loading_node": "Load & Register",
        "optimizer_node": "Optimize",
    }

    with Live(build_progress_table(stages), refresh_per_second=4) as live:
        for event in pipeline.stream(initial_state, config=invoke_config):
            node_name = list(event.keys())[0]
            update_stage(stages, node_name, event[node_name])
            live.update(build_progress_table(stages))

    # Post-pipeline: execute artifact, format output
    ...
```

### Step 3: Entry point (5 min)

```toml
# pyproject.toml
[project.scripts]
mint = "src.cli.app:app"
```

This allows (command name TBD — `jit` is a placeholder):
```bash
uv run mint run "Parse this CSV" --file data.csv
uv run mint list
uv run mint plan "Detect anomalies"
```

### Step 4: Keep backward compatibility

Keep `scripts/run_task.py` as-is (or have it delegate to `src.cli.app`). The evaluation harness and integration tests don't use the CLI — they call the pipeline directly. No breakage.

### Step 5: Migrate `scripts/run_pilot.py` progress display (optional)

Add a Rich progress bar for the evaluation runner:
```
Evaluation: full condition
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 45% 8/18 tasks  T08 ⟳
```

### Step 6: `mint export` / `mint import` — user-editable generated code (planned)

Currently generated source code is stored only in ChromaDB metadata. Users can view it with `mint inspect` but cannot edit and re-register a fixed version. When a generated tool has a bug, the only option is to rephrase the task and re-generate.

**`mint export`** writes a capability's source code to a local file:

```bash
# Export to a file
mint export evaluate_inventory_a4d5d7 -o inventory_tool.py

# Export to stdout
mint export evaluate_inventory_a4d5d7
```

For MCP_TOOL: writes the Python source. For SKILL: writes SKILL.md. For AGENT: writes the agent config JSON.

**`mint import`** registers a local file as a capability, replacing the generated version:

```bash
# Import a fixed tool (replaces existing by name)
mint import inventory_tool.py --name evaluate_inventory_a4d5d7

# Import a new tool with auto-detected name
mint import my_tool.py

# Import a skill
mint import SKILL.md --type skill --name my-data-cleaner
```

The imported capability goes through verification (unless `--skip-verification`) and gets registered in ChromaDB with `source="user-edited"` metadata.

**Why this matters:** The generate → fail → rephrase → regenerate loop is slow (~2-5 min per attempt). Export → edit → import lets users fix a one-line bug in seconds. This also enables a hybrid workflow: JIT generates the scaffold, the user polishes the logic.

**Implementation notes:**
- `export` reads from `registry.get(name).metadata["source_code"]`
- `import` creates a `GeneratedArtifact`, runs it through `verify()`, then `registry.add()`
- Imported capabilities get `metadata={"source": "user-edited", "imported_from": path}`
- Must handle all three types (MCP_TOOL, SKILL, AGENT)

### Step 7: `mint retry` — quick re-run with edited prompt (planned)

When a task fails, the user currently has to retype the entire command with a modified prompt. A `mint retry` command would streamline this:

```bash
# Re-run the last task with the same files/params
mint retry

# Re-run with an edited prompt (opens $EDITOR with the previous prompt pre-filled)
mint retry --edit

# Re-run with extra instructions appended to the original prompt
mint retry --hint "The rules JSON has a 'reorder_threshold' field per category"

# Re-run but skip verification (faster iteration)
mint retry --skip-verification
```

**Implementation:**
- Save the last run's config (task, files, params, flags) to `data/last_run.json` at the end of every `mint run`
- `mint retry` loads that config and re-invokes `execute_task()`
- `--edit` opens `$EDITOR` (or `--editor` flag) with the task prompt; on save, re-runs with the edited version
- `--hint` appends text to the original prompt without replacing it (e.g. "Additional context: ...")
- All other flags (`--skip-verification`, `--no-optimizer`, `--timeout`, etc.) can be overridden

**Why this matters:** The most common failure mode is an underspecified prompt. The user sees what went wrong (via `mint inspect` or the error message), knows exactly what context is missing, and wants to add one sentence — not retype the whole command with file paths.

### Step 8: `--force-regenerate` — bypass registry matching (planned)

**Problem:** `mint retry --hint` changes the prompt but the pipeline may still match an existing (wrong) capability from the registry, skipping generation entirely. The hint only helps if the pipeline reaches the generation stage.

```bash
# Current workaround (two steps)
mint disable find_duplicate_rows_in_csv_6bb264
mint retry --hint "count extras only"

# Proposed (one step)
mint retry --hint "count extras only" --force-regenerate

# Also on mint run
mint run "find duplicates" -f data.csv --force-regenerate
```

**Implementation:** Pass a flag through to `gap_detection_node` that forces all required capabilities to be treated as gaps regardless of registry matches. Or temporarily deactivate matched capabilities for the duration of the run and restore after.

**Why this matters:** Without this, users who get a wrong result must manually disable the old capability before retrying. This breaks the fast iteration loop that `mint retry` was designed for.

### Step 9: Smart prompt improvement — `mint run --improve` (planned)

The most common failure mode is an underspecified prompt. File metadata augmentation (Step 2) tells the generator *what* the data looks like, but it doesn't help if the user's intent is vague ("do something with this data"). The `--hint` flag (Step 7) fixes this reactively, but only after a failure.

**Proactive prompt improvement** uses an LLM call to rewrite the user's prompt into a more specific, actionable version *before* the pipeline runs.

```bash
# Auto-improve (asks for confirmation before proceeding)
mint run "clean up this data" -f messy.csv --improve

# Example flow:
# ╭──────────────── MINT ────────────────╮
# │ clean up this data                   │
# │   └ messy.csv                        │
# ╰────── JIT Capability Synthesis ──────╯
#
# Suggested improved prompt:
#
#   "Read messy.csv (CSV, 500 rows, columns: id, name, email, score, date).
#    Clean the data by: (1) removing rows where all values are null,
#    (2) deduplicating rows by id, (3) standardizing date format to
#    ISO-8601, (4) trimming whitespace from string columns. Return
#    the cleaned row count and a summary of changes made."
#
# Use this improved prompt? [Y/n/edit]:
#   Y     → proceed with improved prompt
#   n     → proceed with original prompt
#   edit  → open improved prompt in $EDITOR, then proceed

# Always improve without asking (for scripting)
mint run "clean up this data" -f messy.csv --improve --force
```

**Implementation:**

1. **Pre-pipeline LLM call** — Before decomposition, send the user's task + file metadata to a prompt-improvement prompt:
   - Input: user task, file structure summaries (from `_describe_file()`), parameter list
   - System prompt: "You are a task specification assistant. Rewrite the user's vague task into a precise, actionable specification. Use the file structure information to be specific about column names, data types, and expected operations. Keep the original intent. Output only the improved task description."
   - Model: same `openai:gpt-5-mini`

2. **User confirmation** — Show the improved prompt and ask:
   - `Y` (default): use improved prompt
   - `n`: use original prompt unchanged
   - `edit`: open improved prompt in `$EDITOR` for manual tweaking

3. **`--improve` flag** — opt-in to avoid the extra LLM call on every run
   - Could later become default behavior with `--no-improve` to skip

4. **Integration with retry** — `mint retry --improve` re-improves the original (not previously improved) prompt with fresh context from the failure

**Why this matters:** The gap between "what the user typed" and "what the pipeline needs" is the single biggest source of failures. File metadata helps the *generator*, but prompt improvement helps the *decomposer* and *classifier* — the upstream stages that determine what gets built. A 5-second LLM call to clarify intent can save 2-5 minutes of failed generation.

**Cost:** One additional LLM call (~2-5s, ~500 tokens). Only when `--improve` is specified. The improved prompt is shown to the user before proceeding, so there's full transparency — no hidden rewriting.

**Future:** If prompt improvement proves consistently helpful, it could become the default behavior (always improve, show diff, ask to confirm). The `--no-improve` flag would then skip it for power users who write precise prompts.

---

### Step 10: `--output-dir` — save generated file outputs (planned)

**Problem:** Generated tools that produce files (cleaned CSVs, reports, charts) write them inside the ephemeral Docker container. When the container exits, those files are gone. The JSON output may contain file paths (e.g. `/tmp/fixture_0.csv`) that don't exist on the host, or the full file content as a giant string in the JSON — neither is useful.

```bash
# Proposed: save any file outputs to a local directory
mint run "Clean this CSV and save the result" \
  -f dirty.csv --output-dir ./results/

# Result:
# Output saved to: ./results/cleaned.csv
# Summary saved to: ./results/summary.json
```

**Implementation options:**

1. **Post-execution extraction** — After sandbox execution, scan the result dict for values that look like file content (long strings, CSV-like, JSON-like). Write them to `--output-dir` with auto-generated filenames.

2. **Docker volume mount** — Mount `--output-dir` as a writable volume in the sandbox container. Generated tools write directly to it. More reliable but requires changing `sandbox_execute` to support output mounts.

3. **Convention-based** — Instruct generators to return file content in a `files` key in the result dict (e.g. `{"files": {"cleaned.csv": "..."}, "summary": {...}}`). The CLI extracts and writes them.

**Why this matters:** Without this, file-producing tasks are effectively broken for end users. They see a JSON blob with embedded file content or dead temp paths. The tool technically works but the user can't access the result.

---

## What NOT to do

- Don't make Rich a hard dependency for the pipeline — it's CLI-only. `src/orchestrator/` should never import Rich.
- Don't break JSON output mode — `--json` must produce clean parseable JSON, no Rich markup.
- Don't add interactivity (prompts, confirmations) — keep it scriptable. Typer's `--help` and subcommands provide guidance without blocking.

## Dependencies

**Already installed** — both packages are in the dependency tree via chromadb:
- `typer==0.23.0` (chromadb requires `>=0.9.0`; Annotated syntax supported since 0.9.0)
- `rich==14.3.2` (hard dependency of typer since 0.15.0)

Add explicit dependency constraints to `pyproject.toml` to make the intent clear:
```
typer >= 0.15.0     # CLI framework; 0.15+ has rich as hard dep, Annotated since 0.9
rich >= 13.0.0      # Terminal formatting (used directly for tables, panels, progress)
```

No new packages to install — `uv sync` is a no-op for these.

## Effort Estimate

| Step | Time | Notes |
|---|---|---|
| Step 1: Add deps | 5 min | `uv add typer rich` |
| Step 2: `src/cli/` package | 3-4h | app.py (~100 lines), display.py (~150 lines), runner.py (~100 lines) |
| Step 3: Entry point | 5 min | pyproject.toml |
| Step 4: Backward compat | 30 min | Verify existing tests, delegate from old script |
| Step 5: Pilot progress (optional) | 1h | Rich progress bar for run_pilot.py |

**Total: ~4-5 hours** for the core CLI upgrade.

## Migration Path

1. Implement `src/cli/` alongside existing `scripts/run_task.py`
2. Test both work for the same inputs
3. Once stable, update `scripts/run_task.py` to be a thin wrapper:
   ```python
   from src.cli.app import app
   app()
   ```
4. Update docs to recommend `uv run mint` instead of `uv run scripts/run_task.py`
