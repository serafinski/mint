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
