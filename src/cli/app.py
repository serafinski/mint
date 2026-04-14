"""MINT CLI — Model-driven Instantiation of Novel Tools.

Entry point for the ``mint`` command. Registered via pyproject.toml
[project.scripts] so ``uv run mint`` works.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    name="mint",
    help="MINT — Model-driven Instantiation of Novel Tools\n\n"
    "Generate, verify, and execute capabilities on demand.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


# ---------------------------------------------------------------------------
# mint run
# ---------------------------------------------------------------------------

_FILE_HELP = "Input file(s)"
_PARAM_HELP = "KEY=VALUE parameter(s)"


@app.command()
def run(
    task: Annotated[str, typer.Argument(help="Task description")],
    file: Annotated[list[Path] | None, typer.Option("--file", "-f", help=_FILE_HELP)] = None,
    param: Annotated[list[str] | None, typer.Option("--param", "-p", help=_PARAM_HELP)] = None,
    skip_verification: Annotated[
        bool, typer.Option("--skip-verification", help="Bypass verification gate")
    ] = False,
    no_optimizer: Annotated[
        bool, typer.Option("--no-optimizer", help="Force-disable optimizer")
    ] = False,
    enable_optimizer: Annotated[
        bool, typer.Option("--enable-optimizer", help="Force-enable optimizer")
    ] = False,
    timeout: Annotated[
        int | None, typer.Option(help="SKILL/AGENT execution timeout (seconds)")
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", "-o", help="Save output to this directory"),
    ] = None,
    json: Annotated[
        bool, typer.Option("--json", help="Output as JSON (no Rich formatting)")
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
    allow_network: Annotated[
        bool, typer.Option("--allow-network", help="Allow generated tools to access the network")
    ] = False,
) -> None:
    """Run a task through the JIT capability synthesis pipeline."""
    from src.cli.runner import execute_task

    code = execute_task(
        task,
        files=file,
        params=param,
        skip_verification=skip_verification,
        no_optimizer=no_optimizer,
        enable_optimizer=enable_optimizer,
        timeout=timeout,
        output_dir=output_dir,
        json_output=json,
        verbose=verbose,
        allow_network=allow_network,
    )
    raise SystemExit(code)


# ---------------------------------------------------------------------------
# mint plan
# ---------------------------------------------------------------------------


@app.command()
def plan(
    task: Annotated[str, typer.Argument(help="Task description")],
    file: Annotated[list[Path] | None, typer.Option("--file", "-f", help=_FILE_HELP)] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Decompose and classify a task (dry-run, no generation)."""
    from src.cli.runner import plan_task

    code = plan_task(task, files=file, verbose=verbose)
    raise SystemExit(code)


# ---------------------------------------------------------------------------
# mint list
# ---------------------------------------------------------------------------


@app.command("list")
def list_capabilities(
    cap_type: Annotated[
        str | None,
        typer.Option("--type", "-t", help="Filter: mcp_tool, skill, agent"),
    ] = None,
    active_only: Annotated[
        bool, typer.Option("--active-only", help="Show only active capabilities")
    ] = False,
) -> None:
    """List all capabilities in the registry."""
    from src.cli.display import show_capability_list
    from src.cli.runner import _create_infra, _load_env

    _load_env()
    registry, _, metrics_store = _create_infra(seed=False)
    show_capability_list(registry, metrics_store, cap_type=cap_type, active_only=active_only)


# ---------------------------------------------------------------------------
# mint inspect
# ---------------------------------------------------------------------------


@app.command()
def inspect(
    name: Annotated[str, typer.Argument(help="Capability name")],
) -> None:
    """Show capability details (metadata, source, history)."""
    from src.cli.display import show_capability_detail
    from src.cli.runner import _create_infra, _load_env

    _load_env()
    registry, _, metrics_store = _create_infra(seed=False)
    show_capability_detail(registry, metrics_store, name)


# ---------------------------------------------------------------------------
# mint history
# ---------------------------------------------------------------------------


@app.command()
def history(
    capability: Annotated[
        str | None,
        typer.Option("--capability", "-c", help="Filter by capability"),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max records")] = 20,
) -> None:
    """Show recent invocation history."""
    from src.cli.display import show_history
    from src.cli.runner import _create_infra, _load_env

    _load_env()
    _, _, metrics_store = _create_infra(seed=False)
    show_history(metrics_store, capability=capability, limit=limit)


# ---------------------------------------------------------------------------
# mint prune
# ---------------------------------------------------------------------------


@app.command()
def prune(
    window_days: Annotated[
        int,
        typer.Option("--window", "-w", help="Days of inactivity before pruning"),
    ] = 30,
) -> None:
    """Remove capabilities with no recent invocations."""
    from src.cli.display import console
    from src.cli.runner import _create_infra, _load_env

    _load_env()
    registry, _, metrics_store = _create_infra(seed=False)

    from src.optimizer.operations import prune as do_prune

    pruned = do_prune(registry, metrics_store, window_days=window_days)
    if pruned:
        console.print(f"[yellow]Pruned {len(pruned)} capability(ies):[/yellow]")
        for name in pruned:
            console.print(f"  - {name}")
    else:
        console.print("[green]Nothing to prune — all capabilities have recent activity.[/green]")


# ---------------------------------------------------------------------------
# mint reset
# ---------------------------------------------------------------------------


@app.command()
def reset(
    force: Annotated[bool, typer.Option("--force", help="Skip confirmation prompt")] = False,
    no_seed: Annotated[
        bool,
        typer.Option("--no-seed", help="Start empty (don't restore seeded capabilities)"),
    ] = False,
) -> None:
    """Wipe all generated capabilities and invocation history."""
    from src.cli.display import console
    from src.cli.runner import _data_dir, _load_env

    _load_env()
    data = _data_dir()
    chroma_path = data / "cli" / "chroma"
    metrics_path = data / "cli" / "metrics.duckdb"
    # DuckDB write-ahead log — sits alongside metrics.duckdb. If a metrics
    # operation crashed or was interrupted, the WAL is left orphaned next
    # to the main database file. Unlink both so the reset is truly clean.
    metrics_wal_path = data / "cli" / "metrics.duckdb.wal"
    # last_run.json is written by src/cli/runner.py at
    # ``_data_dir() / "cli" / "last_run.json"``. An earlier version of this
    # reset command looked at ``data / "last_run.json"`` (no ``cli``
    # subdirectory), which meant the reset silently failed to wipe the
    # real file on every invocation — confirmed 2026-04-14 via
    # ``ls data/cli/`` showing last_run.json surviving a reset. The unit
    # test for reset was hiding the bug because the test fixture was
    # written against the same wrong path as the production code. Both
    # are fixed in this commit. See docs/perf_fixes_2026_04_14.md §4.9.
    last_run_path = data / "cli" / "last_run.json"
    # Evolutionary domain taxonomy file — wiped on reset so a "clean slate"
    # genuinely means clean slate. Without this, categories grown in prior
    # sessions survive the reset and influence the next run's gap_detection
    # filter against a freshly-seeded registry, which muddles the taxonomy
    # evolution story for the thesis. See docs/perf_fixes_2026_04_14.md §2d.
    taxonomy_path = data / "cli" / "domains.json"
    # Generated skills directory — holds both SKILL and AGENT artifacts
    # (agent.py mirrors skill.py's SKILLS_OUTPUT_DIR via set_skills_output_dir).
    # Wiped on reset for the same reason as the taxonomy file: without this,
    # SKILL.md directories from prior sessions accumulate in data/cli/skills/
    # unboundedly, and post-mortem inspection becomes confused because skills
    # from multiple runs are mixed. The registry is wiped above, so any
    # references to these directories are dangling anyway. Surfaced
    # 2026-04-14 end of day after the 21:38 test_mint run left 6 skill
    # directories from 2-3 different runs in data/cli/skills/.
    skills_path = data / "cli" / "skills"

    if not force:
        console.print(
            "[yellow]This will delete all generated capabilities, "
            "invocation history, and last run config.[/yellow]"
        )
        if no_seed:
            console.print("Registry will be empty (no seeded capabilities).")
        else:
            console.print(
                "Seeded capabilities (fibonacci, csv-parser, etc.) will be restored automatically."
            )
        confirm = typer.confirm("Continue?")
        if not confirm:
            raise SystemExit(0)

    import shutil

    removed = []
    if chroma_path.exists():
        shutil.rmtree(chroma_path)
        removed.append("registry")
    if metrics_path.exists():
        metrics_path.unlink()
        removed.append("invocation history")
    if metrics_wal_path.exists():
        metrics_wal_path.unlink()
        # Don't add to the removed list — it's a DuckDB internal file, not
        # user-facing state. Silent cleanup so the confirmation message
        # stays focused on the semantically meaningful items.
    if last_run_path.exists():
        last_run_path.unlink()
        removed.append("last run config")
    if taxonomy_path.exists():
        taxonomy_path.unlink()
        removed.append("domain taxonomy")
    if skills_path.exists():
        shutil.rmtree(skills_path)
        removed.append("generated skills")

    # Re-seed unless --no-seed
    if not no_seed:
        from src.cli.runner import _create_infra

        _create_infra(seed=True)
        if removed:
            removed.append("seeded capabilities restored")

    if removed:
        console.print(f"[green]Reset complete — {', '.join(removed)}.[/green]")
    else:
        console.print("[dim]Nothing to reset — already clean.[/dim]")


# ---------------------------------------------------------------------------
# mint disable / enable
# ---------------------------------------------------------------------------


@app.command()
def disable(
    name: Annotated[str, typer.Argument(help="Capability name to deactivate")],
    force: Annotated[bool, typer.Option("--force", help="Skip confirmation prompt")] = False,
) -> None:
    """Deactivate a capability (hidden from registry search, kept in storage)."""
    from src.cli.display import console, err_console
    from src.cli.runner import _create_infra, _load_env

    _load_env()
    registry, _, _ = _create_infra(seed=False)
    cap = registry.get(name)
    if not cap:
        err_console.print(f"[red]Capability not found: {name}[/red]")
        raise SystemExit(1)
    if not cap.is_active:
        console.print(f"[dim]{name} is already inactive.[/dim]")
        return
    if not force:
        cap_type = str(cap.capability_type).split(".")[-1].upper()
        console.print(
            f"Disable [bold]{name}[/bold] ({cap_type})? It will be excluded from registry search."
        )
        if not typer.confirm("Continue?"):
            raise SystemExit(0)
    registry.deactivate(name)
    console.print(f"[yellow]Disabled {name}.[/yellow]")


@app.command()
def enable(
    name: Annotated[str, typer.Argument(help="Capability name to reactivate")],
) -> None:
    """Reactivate a previously disabled capability."""
    from src.cli.display import console, err_console
    from src.cli.runner import _create_infra, _load_env

    _load_env()
    registry, _, _ = _create_infra(seed=False)
    cap = registry.get(name)
    if not cap:
        err_console.print(f"[red]Capability not found: {name}[/red]")
        raise SystemExit(1)
    if cap.is_active:
        console.print(f"[dim]{name} is already active.[/dim]")
        return
    registry.update(name, is_active=True)
    console.print(f"[green]Enabled {name}.[/green]")


# ---------------------------------------------------------------------------
# mint seed
# ---------------------------------------------------------------------------


@app.command()
def seed(
    force: Annotated[bool, typer.Option("--force", help="Skip confirmation prompt")] = False,
) -> None:
    """Add baseline capabilities (fibonacci, csv-parser, etc.) to the registry."""
    from src.cli.display import console
    from src.cli.runner import _create_infra, _load_env

    _load_env()
    if not force:
        console.print(
            "This will add 8 baseline capabilities (fibonacci, csv-parser, text-statistics, etc.)."
        )
        confirm = typer.confirm("Continue?")
        if not confirm:
            raise SystemExit(0)

    _create_infra(seed=True)
    console.print("[green]Seeded baseline capabilities.[/green]")


# ---------------------------------------------------------------------------
# mint retry
# ---------------------------------------------------------------------------


@app.command()
def retry(
    hint: Annotated[
        str | None,
        typer.Option("--hint", "-h", help="Extra context to append to the original prompt"),
    ] = None,
    edit: Annotated[
        bool, typer.Option("--edit", "-e", help="Open prompt in $EDITOR before re-running")
    ] = False,
    skip_verification: Annotated[
        bool | None,
        typer.Option("--skip-verification", help="Override: bypass verification"),
    ] = None,
    no_optimizer: Annotated[
        bool | None,
        typer.Option("--no-optimizer", help="Override: disable optimizer"),
    ] = None,
    enable_optimizer: Annotated[
        bool | None,
        typer.Option("--enable-optimizer", help="Override: enable optimizer"),
    ] = None,
    timeout: Annotated[
        int | None, typer.Option(help="Override: execution timeout (seconds)")
    ] = None,
    json: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Re-run the last task, optionally with extra hints or an edited prompt."""
    from src.cli.display import err_console
    from src.cli.runner import execute_task, load_last_run

    last = load_last_run()
    if not last:
        err_console.print("[red]No previous run found. Use `mint run` first.[/red]")
        raise SystemExit(1)

    task_text = last["task"]

    # --edit: open in $EDITOR
    if edit:
        import os
        import tempfile

        editor = os.environ.get("EDITOR", "vi")
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, prefix="mint_"
        ) as f:
            f.write(task_text)
            tmp_path = f.name
        os.system(f'{editor} "{tmp_path}"')
        task_text = Path(tmp_path).read_text().strip()
        os.unlink(tmp_path)
        if not task_text:
            err_console.print("[red]Empty prompt — aborting.[/red]")
            raise SystemExit(1)

    # --hint: append extra context
    if hint:
        task_text = f"{task_text}\n\nAdditional context: {hint}"

    # Restore files and params from last run
    files = [Path(f) for f in last.get("files", [])] or None
    params = last.get("params") or None

    # Use overrides if provided, otherwise fall back to last run's flags
    code = execute_task(
        task_text,
        files=files,
        params=params,
        skip_verification=(
            skip_verification
            if skip_verification is not None
            else last.get("skip_verification", False)
        ),
        no_optimizer=(
            no_optimizer if no_optimizer is not None else last.get("no_optimizer", False)
        ),
        enable_optimizer=(
            enable_optimizer
            if enable_optimizer is not None
            else last.get("enable_optimizer", False)
        ),
        timeout=timeout if timeout is not None else last.get("timeout"),
        json_output=json,
        verbose=verbose,
    )
    raise SystemExit(code)


if __name__ == "__main__":
    app()
