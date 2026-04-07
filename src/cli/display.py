"""Rich-based display formatting for the MINT CLI."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

console = Console()
err_console = Console(stderr=True)


# ---------------------------------------------------------------------------
# Pipeline stage tracking
# ---------------------------------------------------------------------------

# Superset of all possible pipeline nodes.  optimizer_node is only present
# when run_optimizer=True; extra entries are harmless (only nodes that emit
# stream events get looked up).
STAGE_MAP: dict[str, str] = {
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

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _spinner_icon() -> str:
    """Return a cycling spinner character based on current time."""
    import time

    idx = int(time.monotonic() * 8) % len(_SPINNER_FRAMES)
    return f"[cyan]{_SPINNER_FRAMES[idx]}[/cyan]"


def build_progress_table(
    stages: dict[str, str],
    active: str | None = None,
    completed: set[str] | None = None,
    timings: dict[str, float] | None = None,
    stage_details: dict[str, str] | None = None,
    elapsed_active: float | None = None,
) -> Table:
    """Build a Rich table showing pipeline stage progress."""
    completed = completed or set()
    timings = timings or {}
    stage_details = stage_details or {}

    table = Table(
        show_header=False,
        box=None,
        padding=(0, 1),
        expand=False,
    )
    table.add_column("status", width=3, no_wrap=True)
    table.add_column("stage", min_width=20)
    table.add_column("detail", style="dim")
    table.add_column("time", justify="right", style="dim", min_width=8)

    for node_name, label in stages.items():
        if node_name in completed:
            icon = "[green]\u2713[/green]"
            detail = stage_details.get(node_name, "")
            t = f"{timings[node_name]:.1f}s" if node_name in timings else ""
        elif node_name == active:
            icon = _spinner_icon()
            detail = ""
            t = f"{elapsed_active:.0f}s" if elapsed_active else ""
        else:
            icon = "[dim]\u00b7[/dim]"
            detail = ""
            t = ""
        table.add_row(icon, label, detail, t)

    return table


# ---------------------------------------------------------------------------
# Pipeline result display
# ---------------------------------------------------------------------------


def _check_output_quality(
    output: dict,
    formatted: str,
    elapsed_ms: int,
    task_description: str,
) -> str | None:
    """Return a warning string if output looks like garbage, else None."""
    n_keys = len(output)
    n_chars = len(formatted)
    elapsed_s = elapsed_ms / 1000

    # Empty output — no keys at all
    if n_keys == 0:
        return "The output is empty (0 keys)."

    # Long execution, almost no content — likely a real problem
    if elapsed_s > 60 and n_chars < 50:
        return f"The output looks incomplete ({n_chars} chars after {elapsed_s:.0f}s execution)."

    # Echo detection — output contains the input prompt.
    # Filter out common data-processing words to avoid false positives
    # (e.g. "return", "count", "column" appear in both prompts and outputs).
    _ECHO_STOPWORDS = {
        "return", "count", "column", "parse", "value", "input", "output",
        "field", "number", "string", "should", "which", "where", "their",
        "about", "using", "after", "before", "between", "given", "based",
    }
    task_lower = task_description.lower()[:200] if task_description else ""
    if task_lower:
        output_str = formatted.lower()
        words = [w for w in task_lower.split() if len(w) > 5 and w not in _ECHO_STOPWORDS]
        if words:
            matches = sum(1 for w in words if w in output_str)
            if matches > len(words) * 0.7 and n_chars < 500:
                return "The output appears to echo the input prompt."

    return None


def show_pipeline_result(
    state: Any,
    artifact: Any | None,
    output: dict | None,
    elapsed_ms: int,
    *,
    verbose: bool = False,
) -> None:
    """Render the full pipeline result with panels and syntax highlighting."""
    _get = state.get if isinstance(state, dict) else lambda k, d=None: getattr(state, k, d)
    verification_results = _get("verification_results", []) or []
    attempt_count = _get("attempt_count", 0)
    task_description = _get("task_description", "") or ""

    # Verification findings
    if verification_results and artifact:
        _show_verification_findings(verification_results, artifact, verbose=verbose)

    # Output
    console.print()
    if artifact:
        cap_type = str(artifact.capability_type).split(".")[-1].upper()
        cap_name = artifact.capability_name
        console.print(f"Capability: [bold]{cap_name}[/bold] ([cyan]{cap_type}[/cyan])")

    if output:
        formatted = json.dumps(output, indent=2, default=str)
        quality_warning = _check_output_quality(output, formatted, elapsed_ms, task_description)
        border = "yellow" if quality_warning else "green"
        console.print(
            Panel(
                Syntax(formatted, "json", theme="monokai"),
                title="Output",
                border_style=border,
                expand=False,
            )
        )
        if quality_warning:
            console.print(f"\n[yellow]{quality_warning}[/yellow]")
            console.print("[dim]Things to try:[/dim]")
            console.print("[dim]  - mint retry — re-run (may get a better result)[/dim]")
            console.print('[dim]  - mint retry --hint "provide more detailed output"[/dim]')
    elif artifact:
        console.print("[yellow]The generated tool ran but produced no output.[/yellow]")
        console.print()
        console.print("[dim]Things to try:[/dim]")
        console.print('[dim]  - mint retry --hint "describe what went wrong or add context"[/dim]')
        console.print(
            f"[dim]  - mint inspect {artifact.capability_name} "
            "— view the generated source code[/dim]"
        )
        console.print("[dim]  - mint retry --edit — edit the full prompt in your editor[/dim]")
    else:
        console.print("[yellow]No capability was generated.[/yellow]")
        console.print()
        console.print("[dim]Things to try:[/dim]")
        console.print('[dim]  - mint retry --hint "add more detail about the task"[/dim]')
        console.print("[dim]  - mint retry --edit — edit the full prompt in your editor[/dim]")
        console.print("[dim]  - Check that input files are correct[/dim]")

    # Footer
    elapsed_s = elapsed_ms / 1000
    parts = [f"{elapsed_s:.1f}s"]
    if artifact:
        parts.append(str(artifact.capability_type).split(".")[-1].upper())
        parts.append(f"{attempt_count} attempt(s)")
    console.print(f"\n[dim]{' | '.join(parts)}[/dim]")


def _find_report(verification_results: list, artifact: Any) -> Any | None:
    name = getattr(artifact, "capability_name", None)
    if name:
        for r in reversed(verification_results):
            if r.capability_name == name:
                return r
    return verification_results[-1] if verification_results else None


def _show_verification_findings(
    verification_results: list, artifact: Any, *, verbose: bool = False
) -> None:
    report = _find_report(verification_results, artifact)
    if not report:
        return

    findings = getattr(report, "findings", []) or []
    if not findings:
        return

    _SEV_STYLE = {
        "critical": "bold red",
        "high": "red",
        "medium": "yellow",
        "low": "dim",
        "info": "dim",
    }

    _SEV_LABELS = {
        "critical": "Blocked",
        "high": "Warning",
        "medium": "Note",
        "low": "Info",
        "info": "Info",
    }

    if verbose:
        shown = findings
    else:
        shown = [
            f for f in findings if str(getattr(f, "severity", "")).lower() in ("critical", "high")
        ]

    hidden = len(findings) - len(shown)

    if not shown and not hidden:
        return

    console.print()
    if shown:
        console.print("[dim]Verification notes (automated safety checks on generated code):[/dim]")
        for f in shown:
            sev = str(getattr(f, "severity", "")).lower()
            style = _SEV_STYLE.get(sev, "")
            label = _SEV_LABELS.get(sev, sev.upper())
            msg = getattr(f, "message", "")
            explanation = _explain_finding(f)
            console.print(f"  [{style}]{label}:[/{style}] {explanation}")
            if verbose:
                console.print(f"    [dim]Raw: {msg}[/dim]")
        if hidden > 0:
            console.print(f"  [dim]... and {hidden} more (use --verbose)[/dim]")
    elif hidden > 0:
        console.print(f"[dim]{hidden} minor verification note(s) (use --verbose to see)[/dim]")


def _explain_finding(finding: Any) -> str:
    """Translate a raw verification finding into user-friendly language."""
    msg = str(getattr(finding, "message", "")).lower()

    # Hardcoded return / mock data
    if "hardcoded" in msg and "return" in msg:
        return "The generated code may return fixed data instead of computing a real result."
    if "mock" in msg or "placeholder" in msg or "fake" in msg:
        return "The generated code contains placeholder or mock data instead of real logic."

    # Missing external calls
    if "no network" in msg or "no subprocess" in msg:
        return (
            "The tool's name suggests it fetches external data, but it "
            "only reads local files. This is usually fine — the name is "
            "just misleading."
        )

    # Silent exception handling
    if "silent" in msg and "except" in msg:
        return (
            "The code silently swallows errors, which could hide bugs. "
            "Errors should be reported, not ignored."
        )

    # Synthetic comments (TODO, FIXME)
    if "todo" in msg or "fixme" in msg or "placeholder" in msg:
        return "The code contains TODO/FIXME comments, suggesting it may be incomplete."

    # Consistency / behavioral
    if "consistency" in msg or "different results" in msg:
        return (
            "Running the tool twice with the same input produced "
            "different results — the output may be unreliable."
        )

    # Latency
    if "latency" in msg or "slow" in msg or "timeout" in msg:
        return "The tool took longer than expected to run."

    # Output variation
    if "identical output" in msg or "output variation" in msg:
        return (
            "The tool returned the same output for different inputs, "
            "suggesting it may ignore its parameters."
        )

    # Adversarial / demo mode
    if "demo" in msg or "sample" in msg or "test mode" in msg:
        return (
            "The tool may behave differently when it detects test/demo "
            "inputs — a sign of hardcoded test data."
        )

    # Network disconnect
    if "network" in msg and "disconnect" in msg:
        return (
            "The tool claims to fetch data but works without network "
            "access — it may be returning fake results."
        )

    # Ground truth
    if "ground.truth" in msg or "expected result" in msg:
        return "The tool's output doesn't match the known correct answer for a test case."

    # Fallback: return the raw message as-is
    return str(getattr(finding, "message", ""))


# ---------------------------------------------------------------------------
# Dry-run display
# ---------------------------------------------------------------------------


def show_plan_result(state: Any, elapsed_ms: int) -> None:
    """Render dry-run (decompose + classify) results."""
    _get = state.get if isinstance(state, dict) else lambda k, d=None: getattr(state, k, d)
    subtasks = _get("subtasks", []) or []
    classified_gaps = _get("classified_gaps", []) or []
    matches = _get("matches", []) or []

    console.print()
    if subtasks:
        console.print(f"[bold]Subtasks ({len(subtasks)}):[/bold]")
        for i, st in enumerate(subtasks):
            desc = getattr(st, "description", str(st))
            console.print(f"  {i + 1}. {desc}")

    if matches:
        console.print(f"\n[bold]Registry matches ({len(matches)}):[/bold]")
        for m in matches:
            cap = getattr(m, "capability", m)
            name = getattr(cap, "name", "?")
            console.print(f"  [green]\u2713[/green] {name}")

    if classified_gaps:
        console.print(f"\n[bold]Gaps ({len(classified_gaps)}):[/bold]")
        for g in classified_gaps:
            cap_type = str(getattr(g, "classified_type", "?")).split(".")[-1].upper()
            desc = getattr(g, "description", str(g))[:80]
            console.print(f"  [cyan][{cap_type}][/cyan] {desc}")

    console.print(f"\n[dim]Completed in {elapsed_ms / 1000:.1f}s[/dim]")


# ---------------------------------------------------------------------------
# Capability list / inspect / history
# ---------------------------------------------------------------------------


def show_capability_list(
    registry: Any,
    metrics_store: Any | None = None,
    *,
    cap_type: str | None = None,
    active_only: bool = False,
) -> None:
    """Render capabilities as a Rich table."""
    from src.schemas.capability import CapabilityType

    type_filter = None
    if cap_type:
        try:
            type_filter = CapabilityType(cap_type.lower())
        except ValueError:
            err_console.print(f"[red]Unknown type: {cap_type}[/red]")
            err_console.print(f"Valid types: {', '.join(t.value for t in CapabilityType)}")
            raise SystemExit(1)

    capabilities = registry.list_all(
        capability_type=type_filter,
        is_active=True if active_only else None,
    )

    if not capabilities:
        console.print("[dim]No capabilities found.[/dim]")
        return

    table = Table(title="Registered Capabilities")
    table.add_column("Type", style="cyan", no_wrap=True, width=8)
    table.add_column("Name", style="bold", ratio=1)
    table.add_column("Active", justify="center", width=6)
    if metrics_store:
        table.add_column("Invocations", justify="right", width=11)

    for cap in sorted(capabilities, key=lambda c: (str(c.capability_type), c.name)):
        ct = str(cap.capability_type).split(".")[-1].upper()
        active = "[green]\u2713[/green]" if cap.is_active else "[red]\u2717[/red]"
        desc = cap.description or ""
        name_cell = f"{cap.name}\n[dim]{desc}[/dim]" if desc else cap.name
        row = [ct, name_cell, active]
        if metrics_store:
            count = metrics_store.invocation_count(cap.name)
            row.append(str(count))
        table.add_row(*row)

    console.print(table)
    active = sum(1 for c in capabilities if c.is_active)
    inactive = len(capabilities) - active
    parts = [f"{len(capabilities)} total"]
    if active:
        parts.append(f"[green]{active} active[/green]")
    if inactive:
        parts.append(f"[yellow]{inactive} inactive[/yellow]")
    console.print(f"\n[dim]{', '.join(parts)}[/dim]")


def show_capability_detail(
    registry: Any,
    metrics_store: Any | None,
    name: str,
) -> None:
    """Render capability details including source code and invocation history."""
    cap = registry.get(name)
    if not cap:
        err_console.print(f"[red]Capability not found: {name}[/red]")
        raise SystemExit(1)

    ct = str(cap.capability_type).split(".")[-1].upper()
    active = "[green]active[/green]" if cap.is_active else "[red]inactive[/red]"

    # Metadata panel
    meta_lines = [
        f"[bold]Type:[/bold] {ct}",
        f"[bold]Domain:[/bold] {cap.domain}",
        f"[bold]Status:[/bold] {active}",
        f"[bold]Tags:[/bold] {', '.join(cap.tags) if cap.tags else '(none)'}",
        f"[bold]Created:[/bold] {cap.created_at:%Y-%m-%d %H:%M}",
    ]
    _HIDDEN_META = {"source_code", "skill_content", "agent_config"}
    if cap.metadata:
        for k, v in cap.metadata.items():
            if k not in _HIDDEN_META:
                meta_lines.append(f"[bold]{k}:[/bold] {v}")

    console.print(
        Panel(
            "\n".join(meta_lines),
            title=f"[bold]{name}[/bold]",
            border_style="cyan",
        )
    )

    meta = cap.metadata or {}

    # MCP_TOOL: show source code
    source = meta.get("source_code")
    if source:
        console.print(
            Panel(
                Syntax(source, "python", theme="monokai", line_numbers=True, word_wrap=True),
                title="Source Code",
                border_style="dim",
            )
        )

    # SKILL: show SKILL.md content
    skill_content = meta.get("skill_content")
    if skill_content:
        console.print(
            Panel(
                Syntax(skill_content, "markdown", theme="monokai", word_wrap=True),
                title="SKILL.md",
                border_style="dim",
            )
        )

    # AGENT: show agent config (system prompt + settings)
    agent_config = meta.get("agent_config")
    if agent_config:
        import json as json_mod

        if isinstance(agent_config, str):
            try:
                agent_config = json_mod.loads(agent_config)
            except (json_mod.JSONDecodeError, TypeError):
                pass
        if isinstance(agent_config, dict):
            # Show system prompt separately if present
            sys_prompt = agent_config.get("system_prompt")
            if sys_prompt:
                console.print(
                    Panel(
                        sys_prompt,
                        title="System Prompt",
                        border_style="dim",
                    )
                )
            # Show rest of config
            other = {k: v for k, v in agent_config.items() if k != "system_prompt"}
            if other:
                # Truncate skill_content in display (full content
                # is in the SKILL.md panel or via mint export)
                display_other = dict(other)
                if "skill_content" in display_other:
                    sc = display_other["skill_content"]
                    if isinstance(sc, str) and len(sc) > 200:
                        display_other["skill_content"] = sc[:200] + "... (truncated)"
                console.print(
                    Panel(
                        Syntax(
                            json_mod.dumps(display_other, indent=2),
                            "json",
                            theme="monokai",
                            word_wrap=True,
                        ),
                        title="Agent Config",
                        border_style="dim",
                    )
                )
        else:
            console.print(
                Panel(
                    str(agent_config),
                    title="Agent Config",
                    border_style="dim",
                )
            )

    # Invocation history
    if metrics_store:
        recent = metrics_store.recent_invocations(name, limit=10)
        if recent:
            hist = Table(title="Recent Invocations", expand=False)
            hist.add_column("Time", style="dim")
            hist.add_column("Task", max_width=40)
            hist.add_column("Success", justify="center")
            hist.add_column("Latency", justify="right")
            for inv in recent:
                success = "[green]\u2713[/green]" if inv.success else "[red]\u2717[/red]"
                latency = f"{inv.latency_ms:.0f}ms"
                hist.add_row(
                    f"{inv.invoked_at:%Y-%m-%d %H:%M}",
                    inv.task_id[:40],
                    success,
                    latency,
                )
            console.print(hist)
        else:
            console.print("[dim]No invocation history.[/dim]")


def show_history(
    metrics_store: Any,
    *,
    capability: str | None = None,
    limit: int = 20,
) -> None:
    """Show recent invocation history."""
    if capability:
        records = metrics_store.recent_invocations(capability, limit=limit)
    else:
        records = metrics_store.recent_invocations_all(limit=limit)

    if not records:
        console.print("[dim]No invocation history.[/dim]")
        return

    table = Table(title="Invocation History", expand=False)
    table.add_column("Time", style="dim")
    table.add_column("Capability", style="bold")
    table.add_column("Task", max_width=35)
    table.add_column("Success", justify="center")
    table.add_column("Latency", justify="right")

    for r in records:
        success = "[green]\u2713[/green]" if r.success else "[red]\u2717[/red]"
        table.add_row(
            f"{r.invoked_at:%H:%M:%S}",
            r.capability_id,
            r.task_id[:35],
            success,
            f"{r.latency_ms:.0f}ms",
        )

    console.print(table)
