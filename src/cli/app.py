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
    add_completion=False,
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
        bool, typer.Option(
            "--allow-network",
            help=(
                "Allow generated tools to make outbound network requests at "
                "runtime. Without this flag, all code executes in "
                "`--network none` Docker isolation. Persistent default can "
                "be set via `mint config set network.allow true`."
            ),
        )
    ] = False,
    no_allow_network: Annotated[
        bool, typer.Option(
            "--no-allow-network",
            help=(
                "Force-disable network access for this invocation even "
                "when `mint config set network.allow true` is persisted."
            ),
        )
    ] = False,
) -> None:
    """Run a task through the JIT capability synthesis pipeline."""
    from src.cli.display import err_console
    from src.cli.runner import execute_task
    from src.cli.settings import get_network_allow_from_settings, load_settings

    if allow_network and no_allow_network:
        err_console.print(
            "[red]--allow-network and --no-allow-network are mutually "
            "exclusive.[/red]"
        )
        raise typer.Exit(code=1)

    # Step 14.12 — precedence: per-invocation flag > persistent setting > default.
    # ``allow_network_source`` is surfaced in the Rich warning panel so the
    # user can see WHY the network was enabled.
    resolved_network = False
    network_source: str | None = None
    if allow_network:
        resolved_network = True
        network_source = "--allow-network flag"
    elif no_allow_network:
        resolved_network = False
        network_source = None
    else:
        persisted = get_network_allow_from_settings(load_settings(_settings_path()))
        if persisted is True:
            resolved_network = True
            network_source = "persistent setting (mint config show)"
        elif persisted is False:
            resolved_network = False
            network_source = None

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
        allow_network=resolved_network,
        allow_network_source=network_source,
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
# mint config — persistent CLI settings (§12 finding #8)
# ---------------------------------------------------------------------------

config_app = typer.Typer(
    name="config",
    help="Manage persistent CLI settings (optimizer config + network access).",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(config_app, name="config")

_VALID_OPTIMIZER_OPS = ("prune", "split", "merge")
_VALID_OPTIMIZER_SETTINGS = ("auto", "on", "off")
_VALID_NETWORK_KEYS = ("allow",)
_VALID_BOOL_STRINGS = {"true": True, "false": False, "1": True, "0": False}


def _settings_path() -> Path:
    from src.cli.runner import _data_dir

    return _data_dir() / "cli" / "settings.json"


@config_app.command("set")
def config_set(
    key: Annotated[str, typer.Argument(
        help="Setting key (e.g., optimizer.prune, optimizer.split, optimizer.merge, network.allow)"
    )],
    value: Annotated[str, typer.Argument(
        help="Setting value (optimizer: auto | on | off; network.allow: true | false)"
    )],
) -> None:
    """Set a persistent CLI setting.

    Currently supported keys:

    - ``optimizer.prune``, ``optimizer.split``, ``optimizer.merge`` —
      each accepts ``auto`` (threshold-gated), ``on`` (always run), or
      ``off`` (never run). Per-invocation flags (``--no-optimizer``,
      ``--enable-optimizer``) still override these per run.
    - ``network.allow`` — ``true`` / ``false``. When true, ``mint run``
      allows generated tools to make outbound network requests by
      default. Per-invocation flags (``--allow-network`` /
      ``--no-allow-network``) still override per run. Step 14.12.
    """
    from src.cli.display import console, err_console
    from src.cli.settings import load_settings, save_settings

    if key.startswith("optimizer."):
        op = key.split(".", 1)[1]
        if op not in _VALID_OPTIMIZER_OPS:
            err_console.print(
                f"[red]Unknown optimizer operation '{op}'. Valid: "
                f"{', '.join(_VALID_OPTIMIZER_OPS)}.[/red]"
            )
            raise typer.Exit(code=1)

        if value not in _VALID_OPTIMIZER_SETTINGS:
            err_console.print(
                f"[red]Invalid value '{value}'. Valid: "
                f"{', '.join(_VALID_OPTIMIZER_SETTINGS)}.[/red]"
            )
            raise typer.Exit(code=1)

        path = _settings_path()
        settings = load_settings(path)
        settings.setdefault("optimizer", {})[op] = value
        save_settings(path, settings)
        console.print(f"[green]Set {key} = {value}[/green] ({path})")
        return

    if key.startswith("network."):
        sub = key.split(".", 1)[1]
        if sub not in _VALID_NETWORK_KEYS:
            err_console.print(
                f"[red]Unknown network setting '{sub}'. Valid: "
                f"{', '.join(_VALID_NETWORK_KEYS)}.[/red]"
            )
            raise typer.Exit(code=1)
        bool_value = _VALID_BOOL_STRINGS.get(value.lower())
        if bool_value is None:
            err_console.print(
                f"[red]Invalid value '{value}'. network.allow expects "
                "'true' or 'false'.[/red]"
            )
            raise typer.Exit(code=1)
        path = _settings_path()
        settings = load_settings(path)
        settings.setdefault("network", {})[sub] = bool_value
        save_settings(path, settings)
        console.print(f"[green]Set {key} = {bool_value}[/green] ({path})")
        return

    err_console.print(
        f"[red]Unknown setting key '{key}'. Supported: "
        f"optimizer.prune, optimizer.split, optimizer.merge, "
        f"network.allow.[/red]"
    )
    raise typer.Exit(code=1)


@config_app.command("show")
def config_show() -> None:
    """Show current persistent CLI settings."""
    import json

    from src.cli.display import console
    from src.cli.settings import load_settings

    path = _settings_path()
    settings = load_settings(path)
    if not settings:
        console.print(f"[dim]No settings saved yet ({path}).[/dim]")
        console.print(
            "[dim]Defaults:[/dim]\n"
            "[dim]  optimizer.{prune,split,merge} = auto (threshold-gated)[/dim]\n"
            "[dim]  network.allow = false (offline sandbox)[/dim]"
        )
        return
    console.print(f"[bold]Settings ({path}):[/bold]")
    console.print(json.dumps(settings, indent=2, sort_keys=True))


@config_app.command("reset")
def config_reset() -> None:
    """Reset ALL settings to defaults (optimizer ops → 'auto', network → offline).

    Deletes ``data/cli/settings.json``. Per-invocation flags
    (``--no-optimizer`` / ``--enable-optimizer`` / ``--allow-network`` /
    ``--no-allow-network``) and the in-memory defaults are unchanged.
    This does NOT wipe generated capabilities, metrics, or the registry —
    use ``mint reset`` for that.
    """
    from src.cli.display import console

    path = _settings_path()
    if path.exists():
        path.unlink()
        console.print(
            f"[green]Reset all settings to defaults[/green] ([dim]{path}[/dim] deleted). "
            "optimizer.{prune,split,merge} = auto; network.allow = false."
        )
    else:
        console.print(
            "[dim]No settings file exists; already at defaults "
            "(optimizer.{prune,split,merge} = auto; network.allow = false).[/dim]"
        )


@config_app.command("unset")
def config_unset(
    key: Annotated[str, typer.Argument(
        help="Setting key to unset (e.g., optimizer.split, network.allow)"
    )],
) -> None:
    """Unset a persistent CLI setting (revert to default).

    For wiping ALL settings at once, use ``mint config reset``.
    """
    from src.cli.display import console, err_console
    from src.cli.settings import load_settings, save_settings

    if key.startswith("optimizer."):
        op = key.split(".", 1)[1]
        if op not in _VALID_OPTIMIZER_OPS:
            err_console.print(f"[red]Unknown optimizer operation '{op}'.[/red]")
            raise typer.Exit(code=1)

        path = _settings_path()
        settings = load_settings(path)
        optimizer = settings.get("optimizer", {})
        if op in optimizer:
            del optimizer[op]
            if not optimizer:
                settings.pop("optimizer", None)
            save_settings(path, settings)
            console.print(f"[green]Unset {key} (revert to default 'auto')[/green]")
        else:
            console.print(f"[dim]{key} was not set (already at default).[/dim]")
        return

    if key.startswith("network."):
        sub = key.split(".", 1)[1]
        if sub not in _VALID_NETWORK_KEYS:
            err_console.print(f"[red]Unknown network setting '{sub}'.[/red]")
            raise typer.Exit(code=1)

        path = _settings_path()
        settings = load_settings(path)
        network = settings.get("network", {})
        if sub in network:
            del network[sub]
            if not network:
                settings.pop("network", None)
            save_settings(path, settings)
            console.print(
                f"[green]Unset {key} (revert to default: offline).[/green]"
            )
        else:
            console.print(f"[dim]{key} was not set (already at default).[/dim]")
        return

    err_console.print(f"[red]Unknown setting key '{key}'.[/red]")
    raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# mint feedback — user-judgment wrong-output flagging (§12 finding #6b)
# ---------------------------------------------------------------------------


@app.command()
def feedback(
    wrong: Annotated[
        bool,
        typer.Option("--wrong", help="Flag the most recent run as producing wrong output."),
    ] = False,
    reason: Annotated[
        str | None,
        typer.Option("--reason", "-r", help="Why the output was wrong (free text)."),
    ] = None,
    capability: Annotated[
        str | None,
        typer.Option(
            "--capability", "-c",
            help="Flag the most recent invocation of this specific capability.",
        ),
    ] = None,
) -> None:
    """Flag the most recent run's output as wrong.

    Closes the CLI feedback loop (§12 finding #6b): without this subcommand,
    non-crashing but semantically-wrong outputs were invisible to the
    Pillar 3 optimizer's success_rate metric on CLI invocations. Flagging
    updates the most recent ``InvocationRecord`` to ``success=False`` with
    the user's reason as ``error_message``, and records a
    ``user_flagged_wrong`` event for Chapter 6 analytics.

    Usage:
        mint feedback --wrong
        mint feedback --wrong --reason "got db_connections wrong"
        mint feedback --wrong --capability write-markdown-report
    """
    from src.cli.display import console, err_console
    from src.cli.runner import _create_infra, _load_env

    if not wrong:
        err_console.print(
            "[red]mint feedback requires --wrong to flag output as incorrect.[/red]"
        )
        raise typer.Exit(code=1)

    _load_env()
    _, _, metrics_store = _create_infra(seed=False)

    record = metrics_store.flag_most_recent_invocation_wrong(
        reason=reason or "user-flagged-wrong",
        capability_id=capability,
    )
    if record is None:
        target = f"for capability '{capability}'" if capability else ""
        err_console.print(
            f"[yellow]No recent invocation found {target}; nothing to flag.[/yellow]"
        )
        raise typer.Exit(code=1)

    console.print(
        f"[green]Flagged invocation of '{record.capability_id}' as wrong[/green] "
        f"(reason: {record.error_message!r}). Recorded at {record.invoked_at}."
    )
    console.print(
        "[dim]The Pillar 3 optimizer will now see this as success=False on next "
        "optimizer cycle — split's wrong-output-case branch can fire on this "
        "capability if it also has high reuse.[/dim]"
    )


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
    """Deactivate capabilities with no recent invocations (archived, not deleted; re-enable with `mint enable`)."""
    from src.cli.display import console
    from src.cli.runner import _create_infra, _load_env

    _load_env()
    registry, _, metrics_store = _create_infra(seed=False)

    from src.optimizer.operations import prune as do_prune

    pruned = do_prune(registry, metrics_store, window_days=window_days)
    if pruned:
        console.print(
            f"[yellow]Deactivated {len(pruned)} capability(ies) "
            f"(still in registry, re-enable with `mint enable <name>`):[/yellow]"
        )
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
    allow_network: Annotated[
        bool, typer.Option(
            "--allow-network",
            help="Override: allow outbound network in the sandbox for this retry.",
        )
    ] = False,
    no_allow_network: Annotated[
        bool, typer.Option(
            "--no-allow-network",
            help="Override: force-disable network for this retry.",
        )
    ] = False,
    timeout: Annotated[
        int | None, typer.Option(help="Override: execution timeout (seconds)")
    ] = None,
    json: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Re-run the last task, optionally with extra hints or an edited prompt."""
    from src.cli.display import err_console
    from src.cli.runner import execute_task, load_last_run
    from src.cli.settings import get_network_allow_from_settings, load_settings

    if allow_network and no_allow_network:
        err_console.print(
            "[red]--allow-network and --no-allow-network are mutually "
            "exclusive.[/red]"
        )
        raise typer.Exit(code=1)

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

    # Step 14.12: network precedence for retry. Same rule as `mint run`
    # (flag > persistent setting > last-run fallback > default False).
    # Retry adds a "last run" tier so that if a user ran with
    # ``--allow-network`` originally, the retry inherits it without
    # needing to re-pass the flag.
    resolved_network = False
    network_source: str | None = None
    if allow_network:
        resolved_network = True
        network_source = "--allow-network flag (retry override)"
    elif no_allow_network:
        resolved_network = False
        network_source = None
    else:
        persisted = get_network_allow_from_settings(load_settings(_settings_path()))
        if persisted is True:
            resolved_network = True
            network_source = "persistent setting (mint config show)"
        elif persisted is False:
            resolved_network = False
            network_source = None
        elif last.get("allow_network"):
            resolved_network = True
            network_source = "inherited from previous run"

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
        allow_network=resolved_network,
        allow_network_source=network_source,
    )
    raise SystemExit(code)


if __name__ == "__main__":
    app()
